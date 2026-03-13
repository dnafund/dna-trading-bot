#!/usr/bin/env python3
"""
SD Zone Comparison Script — Fetch 3000+ candles via OKX SPOT (priority)
with Binance fallback, then detect zones for comparison with BigBeluga.

Shows BOTH FIFO (BigBeluga's method) and DELTA (our method) side by side
so user can compare which matches TradingView better.

Usage:
    python scripts/sd_compare.py [SYMBOL] [TIMEFRAME]
    python scripts/sd_compare.py                    # Default: TRUMPUSDT, all TFs
    python scripts/sd_compare.py BTCUSDT 4h         # Specific symbol + TF
"""

import sys
import os
import time

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
# Try worktree .env first, then main repo .env
_script_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_env_path = os.path.join(_script_root, '.env')
if not os.path.exists(_env_path):
    # Worktree: walk up to find main repo .env
    _env_path = os.path.join(_script_root.split('.claude')[0].rstrip(os.sep), '.env')
load_dotenv(_env_path)

import ccxt
import numpy as np
import pandas as pd

try:
    from tvDatafeed import TvDatafeed, Interval
    TV_AVAILABLE = True
except ImportError:
    TV_AVAILABLE = False

from src.trading.core.sd_zones import SupplyDemandZones, SDZone
from src.trading.core.indicators import ATRIndicator
from src.trading.core.config import SD_ZONES_CONFIG

# TradingView interval mapping
_TV_INTERVAL_MAP = {
    '5m': 'in_5_minute',
    '15m': 'in_15_minute',
    '1h': 'in_1_hour',
    '4h': 'in_4_hour',
    '1d': 'in_daily',
}

# OKX timeframe mapping for raw API calls
_OKX_TF_MAP = {
    '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6Hutc', '12h': '12Hutc',
    '1d': '1Dutc', '1w': '1Wutc', '1M': '1Mutc',
}


_tv_client_cache = None


def _get_tv_client(username=None, password=None):
    """Get authenticated TvDatafeed client (cached, token injected).

    TvDatafeed's built-in login often hits rate limits.
    Workaround: login via requests, inject token into nologin client.
    """
    global _tv_client_cache
    if _tv_client_cache is not None:
        return _tv_client_cache

    import requests

    tv = TvDatafeed()  # nologin base

    if username and password:
        try:
            r = requests.post(
                'https://www.tradingview.com/accounts/signin/',
                data={'username': username, 'password': password, 'remember': 'on'},
                headers={
                    'Referer': 'https://www.tradingview.com',
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                },
                timeout=10,
            )
            j = r.json()
            if 'user' in j:
                tv.token = j['user']['auth_token']
                print(f"[TV-AUTH] logged in as {j['user'].get('username', '?')}")
        except Exception as e:
            print(f"[TV-AUTH] login failed: {e}, using nologin")

    _tv_client_cache = tv
    return tv


def _to_dataframe(candles: list) -> pd.DataFrame:
    """Convert raw candle list to DataFrame."""
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(
        candles,
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
    )
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    df = df[~df.index.duplicated(keep='last')]
    df.sort_index(inplace=True)
    return df


def _fetch_okx_phase(okx, symbol: str, inst_id: str, timeframe: str,
                      remaining: int, all_candles: list, label: str,
                      ) -> tuple[int, list, int]:
    """Fetch from OKX: market/candles then history-candles.

    Returns (phase1_count, updated_all_candles, remaining).
    """
    okx_bar = _OKX_TF_MAP.get(timeframe, timeframe)
    cursor_ts = None
    p1_count = 0

    # market/candles (300/req, ~1440 max)
    print(f"    {label} candles...", end="", flush=True)
    while remaining > 0:
        batch_size = min(remaining, 300)
        params = {}
        if cursor_ts is not None:
            params['until'] = cursor_ts

        try:
            batch = okx.fetch_ohlcv(
                symbol=symbol, timeframe=timeframe,
                limit=batch_size, params=params,
            )
        except Exception as e:
            print(f" error: {e}")
            break

        if not batch:
            break

        all_candles = batch + all_candles
        remaining -= len(batch)
        p1_count += len(batch)

        if len(batch) < batch_size:
            break
        cursor_ts = batch[0][0]
        time.sleep(0.5)

    if p1_count > 0:
        print(f" got {p1_count}")
    else:
        print(f" (none)")
        return 0, all_candles, remaining

    # history-candles (100/req, older data)
    p2_count = 0
    if remaining > 0 and all_candles:
        print(f"    {label} history...", end="", flush=True)
        oldest_ts = all_candles[0][0]

        while remaining > 0:
            batch_size = min(remaining, 100)
            try:
                result = okx.publicGetMarketHistoryCandles({
                    'instId': inst_id,
                    'bar': okx_bar,
                    'limit': str(batch_size),
                    'after': str(oldest_ts),
                })
                data = result.get('data', [])
            except Exception as e:
                print(f" error: {e}")
                break

            if not data:
                break

            batch = [
                [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                for c in data
            ]
            batch.reverse()
            all_candles = batch + all_candles
            remaining -= len(batch)
            p2_count += len(batch)

            if len(data) < batch_size:
                break
            oldest_ts = int(data[-1][0])
            time.sleep(0.5)

        if p2_count > 0:
            print(f" got {p2_count}")
        else:
            print(f" (none)")

    return p1_count + p2_count, all_candles, remaining


def fetch_candles_tv(symbol: str, timeframe: str, limit: int) -> tuple[pd.DataFrame, str]:
    """Fetch candles from TradingView (same data BigBeluga sees).

    Uses OKX perpetual swap (.P suffix) which matches BigBeluga's chart.
    Returns (DataFrame, source_label).
    """
    if not TV_AVAILABLE:
        print("    tvDatafeed not installed, skipping TV source")
        return pd.DataFrame(), ""

    base = symbol.replace("USDT", "")
    tv_symbol = f"{base}USDT.P"  # .P = perpetual swap on TV
    interval_name = _TV_INTERVAL_MAP.get(timeframe)
    if not interval_name:
        print(f"    TF {timeframe} not mapped for TradingView")
        return pd.DataFrame(), ""

    interval = getattr(Interval, interval_name, None)
    if interval is None:
        print(f"    Interval.{interval_name} not available")
        return pd.DataFrame(), ""

    tv_user = os.getenv('TV_USERNAME')
    tv_pass = os.getenv('TV_PASSWORD')
    auth_label = "auth" if tv_user else "nologin"
    print(f"    TradingView {tv_symbol} OKX ({auth_label})...", end="", flush=True)
    try:
        tv = _get_tv_client(tv_user, tv_pass)
        df = tv.get_hist(
            symbol=tv_symbol, exchange='OKX',
            interval=interval, n_bars=limit,
        )
    except Exception as e:
        print(f" error: {e}")
        return pd.DataFrame(), ""

    if df is None or df.empty:
        print(f" no data")
        return pd.DataFrame(), ""

    count = len(df)
    print(f" got {count}")

    # tvDatafeed returns: index=datetime, columns=[symbol, open, high, low, close, volume]
    result = df[['open', 'high', 'low', 'close', 'volume']].copy()
    result.index.name = 'timestamp'
    return result, f"TradingView:{count}"


def fetch_candles(symbol: str, timeframe: str, limit: int) -> tuple[pd.DataFrame, str]:
    """Fetch candles: TradingView → OKX SWAP → OKX SPOT → Binance fallback.

    TradingView is preferred (exact same data BigBeluga sees).
    Returns (DataFrame, source_label).
    """
    # ── Phase 0: Try TradingView first (exact same data as BigBeluga) ──
    tv_df, tv_source = fetch_candles_tv(symbol, timeframe, limit)
    if not tv_df.empty:
        return tv_df, tv_source

    # ── Fallback: OKX SWAP → OKX SPOT → Binance ──
    print("    TV failed, falling back to exchange APIs...")
    base = symbol.replace("USDT", "")
    spot_symbol = f"{base}/USDT"
    swap_symbol = f"{base}/USDT:USDT"
    spot_inst_id = f"{base}-USDT"
    swap_inst_id = f"{base}-USDT-SWAP"

    all_candles = []
    remaining = limit
    source_parts = []

    # ── Phase 1: OKX SWAP (matches BigBeluga "Perpetual Swap Contract") ──
    okx_swap = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'swap'}})
    count, all_candles, remaining = _fetch_okx_phase(
        okx_swap, swap_symbol, swap_inst_id, timeframe,
        remaining, all_candles, "OKX-SWAP",
    )
    if count > 0:
        source_parts.append(f"OKX-SWAP:{count}")

    # ── Phase 2: OKX SPOT backfill ──
    if remaining > 0:
        okx_spot = ccxt.okx({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
        count, all_candles, remaining = _fetch_okx_phase(
            okx_spot, spot_symbol, spot_inst_id, timeframe,
            remaining, all_candles, "OKX-SPOT",
        )
        if count > 0:
            source_parts.append(f"OKX-SPOT:{count}")

    # ── Phase 3: Binance SPOT fallback (1000/req) ──
    if remaining > 0:
        print(f"    Binance SPOT fallback...", end="", flush=True)
        binance = ccxt.binance({'enableRateLimit': True})
        end_time = all_candles[0][0] if all_candles else None
        phase3_count = 0

        while remaining > 0:
            batch_size = min(remaining, 1000)
            params = {}
            if end_time is not None:
                params['endTime'] = end_time - 1

            try:
                batch = binance.fetch_ohlcv(
                    symbol=spot_symbol, timeframe=timeframe,
                    limit=batch_size, params=params,
                )
            except Exception as e:
                print(f" error: {e}")
                break

            if not batch:
                break

            all_candles = batch + all_candles
            remaining -= len(batch)
            phase3_count += len(batch)

            if len(batch) < batch_size:
                break
            end_time = batch[0][0]
            time.sleep(0.1)

        if phase3_count > 0:
            print(f" got {phase3_count}")
            source_parts.append(f"Binance:{phase3_count}")
        else:
            print(f" (none)")

    source_label = " + ".join(source_parts) if source_parts else "none"
    return _to_dataframe(all_candles), source_label


def detect_zones_fifo(df, tf, config):
    """Detect zones using FIFO selection (BigBeluga's PineScript behavior).

    BigBeluga uses array.shift() which removes oldest when exceeding max_size.
    This means it keeps the N most RECENT surviving zones.
    """
    max_zones = config["max_zones"]

    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    volumes = df['volume'].values
    n = len(df)

    atr_series = ATRIndicator.calculate_atr_pinescript(df, period=config["atr_period"])
    atr = atr_series.values
    vol_avg = pd.Series(volumes).rolling(
        window=min(config["vol_lookback"], n), min_periods=1
    ).mean().values

    supply_zones = SupplyDemandZones._detect_supply(
        opens, highs, lows, closes, volumes, atr, vol_avg,
        config["atr_multiplier"], config["cooldown_bars"], n, df, tf,
    )
    demand_zones = SupplyDemandZones._detect_demand(
        opens, highs, lows, closes, volumes, atr, vol_avg,
        config["atr_multiplier"], config["cooldown_bars"], n, df, tf,
    )

    # Invalidate + test
    supply_zones = SupplyDemandZones._invalidate_and_test(supply_zones, closes, n, "supply")
    demand_zones = SupplyDemandZones._invalidate_and_test(demand_zones, closes, n, "demand")

    # Remove overlaps
    supply_zones = SupplyDemandZones._remove_overlaps(supply_zones, "supply")
    demand_zones = SupplyDemandZones._remove_overlaps(demand_zones, "demand")

    # FIFO: keep N most recent (highest created_idx)
    if len(supply_zones) > max_zones:
        supply_zones.sort(key=lambda z: z.created_idx, reverse=True)
        supply_zones = supply_zones[:max_zones]

    if len(demand_zones) > max_zones:
        demand_zones.sort(key=lambda z: z.created_idx, reverse=True)
        demand_zones = demand_zones[:max_zones]

    # Sort for display
    supply_zones.sort(key=lambda z: z.top, reverse=True)
    demand_zones.sort(key=lambda z: z.bottom)

    supply_zones = SupplyDemandZones._calc_delta_pct(supply_zones)
    demand_zones = SupplyDemandZones._calc_delta_pct(demand_zones)

    return supply_zones + demand_zones


def _fmt_price(price: float) -> str:
    """Dynamic price format — handles both BTC (65000) and PEPE (0.0000034)."""
    if price == 0:
        return "0"
    abs_p = abs(price)
    if abs_p >= 1:
        return f"{price:.4f}"
    # Count leading zeros after decimal point
    # e.g. 0.00000347 → 6 leading zeros → need ~8 sig figs
    import math
    leading_zeros = -math.floor(math.log10(abs_p)) - 1
    decimals = max(leading_zeros + 4, 6)  # at least 4 significant digits
    return f"{price:.{decimals}f}"


def print_zones(zones, current_price, label=""):
    """Print zone list."""
    supply = [z for z in zones if z.zone_type == "supply"]
    demand = [z for z in zones if z.zone_type == "demand"]

    print(f"\n  {label}SUPPLY ({len(supply)}):")
    if supply:
        for i, z in enumerate(supply, 1):
            tested = " [T]" if z.tested else ""
            dist = (z.bottom - current_price) / current_price * 100
            height = z.top - z.bottom
            height_pct = height / z.bottom * 100 if z.bottom > 0 else 999
            print(f"    S{i}: {_fmt_price(z.bottom)} - {_fmt_price(z.top)} "
                  f"(h={_fmt_price(height)}/{height_pct:.1f}%) | "
                  f"delta={z.delta:,.0f} | "
                  f"{z.created_time or 'N/A'} | "
                  f"dist={dist:+.1f}%{tested}")
    else:
        print("    (none)")

    print(f"\n  {label}DEMAND ({len(demand)}):")
    if demand:
        for i, z in enumerate(demand, 1):
            tested = " [T]" if z.tested else ""
            dist = (z.top - current_price) / current_price * 100
            height = z.top - z.bottom
            height_pct = height / z.top * 100 if z.top > 0 else 999
            bottom_str = _fmt_price(z.bottom) if z.bottom >= 0 else f"{z.bottom:.2f}(!)"
            print(f"    D{i}: {bottom_str} - {_fmt_price(z.top)} "
                  f"(h={_fmt_price(height)}/{height_pct:.1f}%) | "
                  f"delta={z.delta:,.0f} | "
                  f"{z.created_time or 'N/A'} | "
                  f"dist={dist:+.1f}%{tested}")
    else:
        print("    (none)")


def run_comparison(symbol: str, timeframes: list[str] | None = None):
    """Fetch candles and detect SD zones — FIFO vs DELTA comparison."""
    if timeframes is None:
        timeframes = ["5m", "15m", "1h", "4h"]

    config = SD_ZONES_CONFIG
    candle_limits = config["candle_limits"]

    print(f"\n{'='*70}")
    print(f"  SD Zone Comparison: {symbol}")
    print(f"  ATR={config['atr_period']}, mult={config['atr_multiplier']}, "
          f"vol={config['vol_lookback']}, max={config['max_zones']}")
    print(f"  Data: TradingView (priority) -> OKX SWAP -> Binance")
    print(f"  Showing: FIFO (BigBeluga) vs DELTA (our method)")
    print(f"{'='*70}")

    atr_period = config["atr_period"]       # 200
    vol_lookback = config["vol_lookback"]    # 1000

    for tf in timeframes:
        limit = candle_limits.get(tf, 1200)
        # BigBeluga on TV has 5000+ bars loaded — match that for comparison
        # Override: ensure at least 5000 bars for zone detection (excl warmup)
        limit = max(limit, 5000)
        warmup = max(atr_period, vol_lookback) + 50
        fetch_limit = limit + warmup
        print(f"\n{'_'*60}")
        print(f"  {tf.upper()} -- Fetching {fetch_limit} candles ({limit} + {warmup} warmup)...")
        print(f"{'_'*60}")

        df, source = fetch_candles(symbol, tf, fetch_limit)
        if df.empty:
            print(f"  ERROR: No data returned")
            continue

        n = len(df)
        current_price = float(df['close'].iloc[-1])
        print(f"  Got {n} candles: {df.index[0]} -> {df.index[-1]}")
        print(f"  Source: {source}")
        print(f"  Price: {_fmt_price(current_price)}")

        # Method 1: FIFO (BigBeluga)
        fifo_zones = detect_zones_fifo(df, tf, config)
        print(f"\n  --- FIFO (BigBeluga method) ---")
        print_zones(fifo_zones, current_price, "FIFO ")

        # Method 2: DELTA (our current method)
        delta_zones = SupplyDemandZones.detect(
            df, timeframe=tf,
            atr_period=config["atr_period"],
            atr_multiplier=config["atr_multiplier"],
            vol_lookback=config["vol_lookback"],
            max_zones=config["max_zones"],
            cooldown_bars=config["cooldown_bars"],
        )
        print(f"\n  --- DELTA (strongest by volume) ---")
        print_zones(delta_zones, current_price, "DELTA ")

        # Differences
        fifo_s = {f"{_fmt_price(z.bottom)}-{_fmt_price(z.top)}" for z in fifo_zones if z.zone_type == "supply"}
        delta_s = {f"{_fmt_price(z.bottom)}-{_fmt_price(z.top)}" for z in delta_zones if z.zone_type == "supply"}
        fifo_d = {f"{_fmt_price(z.bottom)}-{_fmt_price(z.top)}" for z in fifo_zones if z.zone_type == "demand"}
        delta_d = {f"{_fmt_price(z.bottom)}-{_fmt_price(z.top)}" for z in delta_zones if z.zone_type == "demand"}

        only_fifo_s = fifo_s - delta_s
        only_delta_s = delta_s - fifo_s
        only_fifo_d = fifo_d - delta_d
        only_delta_d = delta_d - fifo_d

        if only_fifo_s or only_delta_s or only_fifo_d or only_delta_d:
            print(f"\n  --- DIFFERENCES ---")
            if only_fifo_s:
                print(f"    Supply FIFO-only: {', '.join(sorted(only_fifo_s))}")
            if only_delta_s:
                print(f"    Supply DELTA-only: {', '.join(sorted(only_delta_s))}")
            if only_fifo_d:
                print(f"    Demand FIFO-only: {', '.join(sorted(only_fifo_d))}")
            if only_delta_d:
                print(f"    Demand DELTA-only: {', '.join(sorted(only_delta_d))}")
        else:
            print(f"\n  --- FIFO and DELTA identical ---")

    print(f"\n{'='*70}")
    print(f"  Compare with BigBeluga on TradingView to pick best method.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "TRUMPUSDT"
    tf_arg = sys.argv[2] if len(sys.argv) > 2 else None
    timeframes = [tf_arg] if tf_arg else None
    run_comparison(symbol, timeframes)
