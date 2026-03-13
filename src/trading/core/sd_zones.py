"""
Supply and Demand Zones Indicator

Implements the BigBeluga "Supply and Demand Zones" algorithm.
Standalone indicator — calculates and displays zones only, no entry/exit logic.

Algorithm:
- Supply: 3 consecutive bear candles + above-avg volume → look back for bull candle → zone
- Demand: 3 consecutive bull candles + above-avg volume → look back for bear candle → zone
- ATR(200)*2 for zone height
- Volume delta shows supply/demand imbalance
- Zones invalidated when price closes through
- Max 5 zones per type, overlapping zones removed
"""

import json
import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from src.trading.core.indicators import ATRIndicator

logger = logging.getLogger(__name__)


@dataclass
class SDZone:
    """A single Supply or Demand zone."""
    zone_type: str          # "supply" / "demand"
    top: float
    bottom: float
    delta: float            # Volume delta (negative = sell pressure)
    timeframe: str          # "5m", "1h", "4h", "1d"
    created_time: Optional[str] = None   # ISO timestamp of zone creation
    created_idx: int = 0    # Bar index in DataFrame
    tested: bool = False    # Price touched but didn't break
    delta_pct: float = 0.0  # Delta % strength relative to total same-type


class SupplyDemandZones:
    """Detect Supply and Demand zones using BigBeluga algorithm.

    All methods are static — no instance state.
    """

    @staticmethod
    def detect(
        df: pd.DataFrame,
        timeframe: str = "1h",
        atr_period: int = 200,
        atr_multiplier: float = 2.0,
        vol_lookback: int = 1000,
        max_zones: int = 5,
        cooldown_bars: int = 15,
    ) -> List[SDZone]:
        """Detect all active S/D zones in OHLCV DataFrame.

        Args:
            df: DataFrame with open, high, low, close, volume columns
            timeframe: Timeframe label for zone metadata
            atr_period: ATR calculation period (default 200)
            atr_multiplier: Zone height = ATR * this (default 2)
            vol_lookback: Rolling window for volume average (default 1000)
            max_zones: Max zones per type (default 5)
            cooldown_bars: Bars between zone detections (default 15)

        Returns:
            List of active SDZone objects, supply sorted high→low, demand low→high
        """
        if df is None or len(df) < atr_period + 10:
            return []

        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        volumes = df['volume'].values
        n = len(df)

        # Pre-calculate ATR (PineScript-exact: SMA seed + Wilder's RMA)
        atr_series = ATRIndicator.calculate_atr_pinescript(df, period=atr_period)
        atr = atr_series.values

        # Rolling volume average — BigBeluga uses a custom array that grows
        # from 1 to vol_lookback: vol.push(volume), vol.avg() is always valid.
        # Use min_periods=1 to match: average is valid from bar 0.
        vol_avg = pd.Series(volumes).rolling(
            window=min(vol_lookback, n),
            min_periods=1,
        ).mean().values

        # BigBeluga-exact: single pass, per-bar processing
        # Order: detect → FIFO → invalidate → tested → overlap
        supply_zones: List[SDZone] = []
        demand_zones: List[SDZone] = []
        supply_cd = 0
        demand_cd = 0

        for i in range(2, n):
            c = closes[i]

            # --- 1. Detect supply: 3 bear + above-avg volume ---
            if supply_cd > 0:
                supply_cd -= 1
            else:
                # BigBeluga: vol.push(volume) then vol.avg() → avg includes current bar
                if (closes[i] < opens[i] and closes[i-1] < opens[i-1]
                        and closes[i-2] < opens[i-2]
                        and volumes[i-1] > vol_avg[i-1]
                        and not np.isnan(atr[i])):
                    zone_height = atr[i] * atr_multiplier
                    for off in range(0, min(6, i + 1)):
                        idx = i - off
                        if closes[idx] > opens[idx]:
                            zbot = lows[idx]
                            # Cap zone height at 20% of base price
                            max_h = zbot * 0.20 if zbot > 0 else zone_height
                            ztop = zbot + min(zone_height, max_h)
                            delta = 0.0
                            for k in range(0, off):
                                ki = i - k
                                if closes[ki] < opens[ki]:
                                    delta -= volumes[ki]
                                else:
                                    delta += volumes[ki]
                            ct = None
                            if hasattr(df.index, '__getitem__'):
                                try:
                                    ct = str(df.index[i])
                                except (IndexError, TypeError):
                                    pass
                            supply_zones.append(SDZone(
                                "supply", ztop, zbot, round(delta, 2),
                                timeframe, ct, i, False,
                            ))
                            supply_cd = cooldown_bars
                            break

            # --- 2. Detect demand: 3 bull + above-avg volume ---
            if demand_cd > 0:
                demand_cd -= 1
            else:
                # BigBeluga: vol.push(volume) then vol.avg() → avg includes current bar
                if (closes[i] > opens[i] and closes[i-1] > opens[i-1]
                        and closes[i-2] > opens[i-2]
                        and volumes[i-1] > vol_avg[i-1]
                        and not np.isnan(atr[i])):
                    zone_height = atr[i] * atr_multiplier
                    for off in range(0, min(6, i + 1)):
                        idx = i - off
                        if closes[idx] < opens[idx]:
                            ztop = highs[idx]
                            # Cap zone height at 20% of base price
                            max_h = ztop * 0.20 if ztop > 0 else zone_height
                            zbot = ztop - min(zone_height, max_h)
                            delta = 0.0
                            for k in range(0, off):
                                ki = i - k
                                if closes[ki] > opens[ki]:
                                    delta += volumes[ki]
                                else:
                                    delta -= volumes[ki]
                            ct = None
                            if hasattr(df.index, '__getitem__'):
                                try:
                                    ct = str(df.index[i])
                                except (IndexError, TypeError):
                                    pass
                            demand_zones.append(SDZone(
                                "demand", ztop, zbot, round(delta, 2),
                                timeframe, ct, i, False,
                            ))
                            demand_cd = cooldown_bars
                            break

            # --- 3. Supply: invalidate → test → overlap → FIFO ---
            supply_zones = [z for z in supply_zones if c <= z.top]
            for idx_z in range(len(supply_zones)):
                z = supply_zones[idx_z]
                if not z.tested and z.bottom <= c <= z.top:
                    supply_zones[idx_z] = SDZone(
                        z.zone_type, z.top, z.bottom, z.delta, z.timeframe,
                        z.created_time, z.created_idx, tested=True)
            supply_zones = SupplyDemandZones._remove_overlaps(supply_zones, "supply")
            if len(supply_zones) > max_zones:
                # FIFO: keep most recent (highest created_idx) — matches BigBeluga
                supply_zones.sort(key=lambda z: z.created_idx, reverse=True)
                supply_zones = supply_zones[:max_zones]

            # --- 4. Demand: invalidate → test → overlap → FIFO ---
            demand_zones = [z for z in demand_zones if c >= z.bottom]
            for idx_z in range(len(demand_zones)):
                z = demand_zones[idx_z]
                if not z.tested and z.bottom <= c <= z.top:
                    demand_zones[idx_z] = SDZone(
                        z.zone_type, z.top, z.bottom, z.delta, z.timeframe,
                        z.created_time, z.created_idx, tested=True)
            demand_zones = SupplyDemandZones._remove_overlaps(demand_zones, "demand")
            if len(demand_zones) > max_zones:
                # FIFO: keep most recent (highest created_idx) — matches BigBeluga
                demand_zones.sort(key=lambda z: z.created_idx, reverse=True)
                demand_zones = demand_zones[:max_zones]

        # Sort: supply high→low, demand low→high
        supply_zones.sort(key=lambda z: z.top, reverse=True)
        demand_zones.sort(key=lambda z: z.bottom)

        # Calculate delta % strength using grand total (supply + demand)
        # BigBeluga uses |all_supply_delta| + |all_demand_delta| as denominator
        all_zones = supply_zones + demand_zones
        grand_total = sum(abs(z.delta) for z in all_zones)
        supply_zones = SupplyDemandZones._calc_delta_pct(supply_zones, grand_total)
        demand_zones = SupplyDemandZones._calc_delta_pct(demand_zones, grand_total)

        return supply_zones + demand_zones

    @staticmethod
    def _detect_supply(
        opens, highs, lows, closes, volumes, atr, vol_avg,
        atr_multiplier, cooldown_bars, n, df, timeframe,
    ) -> List[SDZone]:
        """Detect supply zones: 3 bear candles + volume → find bull candle base."""
        zones = []
        cooldown = 0

        for i in range(2, n):
            if cooldown > 0:
                cooldown -= 1
                continue

            # 3 consecutive bear candles (current, -1, -2)
            bear_0 = closes[i] < opens[i]
            bear_1 = closes[i - 1] < opens[i - 1]
            bear_2 = closes[i - 2] < opens[i - 2]

            if not (bear_0 and bear_1 and bear_2):
                continue

            # Extra volume on middle candle
            if np.isnan(vol_avg[i - 1]) or volumes[i - 1] <= vol_avg[i - 1]:
                continue

            # ATR must be valid
            if np.isnan(atr[i]):
                continue

            zone_height = atr[i] * atr_multiplier

            # Look back 0-5 bars from current to find first bull candle
            for offset in range(0, min(6, i + 1)):
                idx = i - offset
                if closes[idx] > opens[idx]:  # Bull candle found
                    zone_bottom = lows[idx]
                    # Cap zone height at 20% of base price
                    max_h = zone_bottom * 0.20 if zone_bottom > 0 else zone_height
                    zone_top = zone_bottom + min(zone_height, max_h)

                    # Calculate volume delta in quote currency (USDT)
                    delta = 0.0
                    for k in range(0, offset):
                        kidx = i - k
                        if closes[kidx] < opens[kidx]:  # Bear
                            delta -= volumes[kidx] * closes[kidx]
                        else:  # Bull
                            delta += volumes[kidx] * closes[kidx]

                    created_time = None
                    if hasattr(df.index, '__getitem__'):
                        try:
                            ts = df.index[i]
                            created_time = str(ts) if ts is not None else None
                        except (IndexError, TypeError):
                            pass

                    zones.append(SDZone(
                        zone_type="supply",
                        top=zone_top,
                        bottom=zone_bottom,
                        delta=round(delta, 2),
                        timeframe=timeframe,
                        created_time=created_time,
                        created_idx=i,
                        tested=False,
                    ))
                    cooldown = cooldown_bars
                    break

        return zones

    @staticmethod
    def _detect_demand(
        opens, highs, lows, closes, volumes, atr, vol_avg,
        atr_multiplier, cooldown_bars, n, df, timeframe,
    ) -> List[SDZone]:
        """Detect demand zones: 3 bull candles + volume → find bear candle base."""
        zones = []
        cooldown = 0

        for i in range(2, n):
            if cooldown > 0:
                cooldown -= 1
                continue

            # 3 consecutive bull candles
            bull_0 = closes[i] > opens[i]
            bull_1 = closes[i - 1] > opens[i - 1]
            bull_2 = closes[i - 2] > opens[i - 2]

            if not (bull_0 and bull_1 and bull_2):
                continue

            # Extra volume on middle candle
            if np.isnan(vol_avg[i - 1]) or volumes[i - 1] <= vol_avg[i - 1]:
                continue

            # ATR must be valid
            if np.isnan(atr[i]):
                continue

            zone_height = atr[i] * atr_multiplier

            # Look back 0-5 bars to find first bear candle
            for offset in range(0, min(6, i + 1)):
                idx = i - offset
                if closes[idx] < opens[idx]:  # Bear candle found
                    zone_top = highs[idx]
                    # Cap zone height at 20% of base price
                    max_h = zone_top * 0.20 if zone_top > 0 else zone_height
                    zone_bottom = zone_top - min(zone_height, max_h)

                    # Calculate volume delta in quote currency (USDT)
                    delta = 0.0
                    for k in range(0, offset):
                        kidx = i - k
                        if closes[kidx] > opens[kidx]:  # Bull
                            delta += volumes[kidx] * closes[kidx]
                        else:  # Bear
                            delta -= volumes[kidx] * closes[kidx]

                    created_time = None
                    if hasattr(df.index, '__getitem__'):
                        try:
                            ts = df.index[i]
                            created_time = str(ts) if ts is not None else None
                        except (IndexError, TypeError):
                            pass

                    zones.append(SDZone(
                        zone_type="demand",
                        top=zone_top,
                        bottom=zone_bottom,
                        delta=round(delta, 2),
                        timeframe=timeframe,
                        created_time=created_time,
                        created_idx=i,
                        tested=False,
                    ))
                    cooldown = cooldown_bars
                    break

        return zones

    @staticmethod
    def _invalidate_and_test(
        zones: List[SDZone],
        closes: np.ndarray,
        n: int,
        zone_type: str,
    ) -> List[SDZone]:
        """Remove broken zones and mark tested ones.

        Supply: invalidated if close > top, tested if price touches bottom (age > 20)
        Demand: invalidated if close < bottom, tested if price touches top (age > 20)
        """
        active = []
        for zone in zones:
            broken = False
            start = zone.created_idx + 1

            for j in range(start, n):
                if zone_type == "supply" and closes[j] > zone.top:
                    broken = True
                    break
                if zone_type == "demand" and closes[j] < zone.bottom:
                    broken = True
                    break

                # Mark tested after 20 bars
                bars_since = j - zone.created_idx
                if bars_since > 20 and not zone.tested:
                    if zone_type == "supply" and closes[j] >= zone.bottom:
                        zone = SDZone(
                            zone_type=zone.zone_type, top=zone.top,
                            bottom=zone.bottom, delta=zone.delta,
                            timeframe=zone.timeframe,
                            created_time=zone.created_time,
                            created_idx=zone.created_idx, tested=True,
                        )
                    elif zone_type == "demand" and closes[j] <= zone.top:
                        zone = SDZone(
                            zone_type=zone.zone_type, top=zone.top,
                            bottom=zone.bottom, delta=zone.delta,
                            timeframe=zone.timeframe,
                            created_time=zone.created_time,
                            created_idx=zone.created_idx, tested=True,
                        )

            if not broken:
                active.append(zone)

        return active

    @staticmethod
    def _calc_delta_pct(zones: List[SDZone], grand_total: float = 0) -> List[SDZone]:
        """Calculate delta % strength for each zone.

        Args:
            zones: List of zones to calculate percentages for.
            grand_total: Combined |supply| + |demand| total.
                If 0, falls back to same-type total (legacy behavior).
        """
        if not zones:
            return zones

        total = grand_total if grand_total > 0 else sum(abs(z.delta) for z in zones)
        if total == 0:
            return zones

        return [
            SDZone(
                zone_type=z.zone_type, top=z.top, bottom=z.bottom,
                delta=z.delta, timeframe=z.timeframe,
                created_time=z.created_time, created_idx=z.created_idx,
                tested=z.tested,
                delta_pct=round(abs(z.delta) / total * 100, 2),
            )
            for z in zones
        ]

    @staticmethod
    def _remove_overlaps(zones: List[SDZone], zone_type: str) -> List[SDZone]:
        """PineScript-exact all-pairs overlap removal.

        For each zone i, check ALL other zones j (older and newer):
        Supply: if zone j's TOP is inside zone i's [bottom, top] → delete zone i
        Demand: if zone j's BOTTOM is inside zone i's [bottom, top] → delete zone i

        The zone that CONTAINS another zone's key value gets deleted.
        Uses strict inequality (< and >) matching PineScript.
        """
        if len(zones) <= 1:
            return zones

        result = sorted(zones, key=lambda z: z.created_idx)

        i = 0
        while i < len(result):
            removed = False
            zi = result[i]
            for j in range(len(result)):
                if j == i:
                    continue
                zj = result[j]

                if zone_type == "supply":
                    if zj.top < zi.top and zj.top > zi.bottom:
                        result.pop(i)
                        removed = True
                        break
                else:  # demand
                    if zj.bottom < zi.top and zj.bottom > zi.bottom:
                        result.pop(i)
                        removed = True
                        break

            if not removed:
                i += 1

        return result


class SDCandleCache:
    """Persistent disk cache for SD zone candle data.

    Stores OHLCV data to disk so we don't re-fetch full history each scan.
    On first run: fetch all available data (up to limit) and save.
    On subsequent runs: load from disk, fetch only new candles, append.
    """

    def __init__(self, exchange_client, cache_dir: Path):
        self._exchange = exchange_client
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory: Dict[str, pd.DataFrame] = {}

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        safe_sym = symbol.replace("/", "_").replace(":", "_")
        return self._cache_dir / f"{safe_sym}_{timeframe}.parquet"

    def fetch(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Fetch candles with persistent disk cache.

        First call: fetch `limit` candles from exchange, save to disk.
        Subsequent: load from disk, append only new candles since last save.
        """
        cache_key = f"{symbol}_{timeframe}"
        path = self._cache_path(symbol, timeframe)

        # Try load from memory first, then disk
        cached_df = self._memory.get(cache_key)
        if cached_df is None and path.exists():
            try:
                cached_df = pd.read_parquet(path)
                if not cached_df.empty:
                    self._memory[cache_key] = cached_df
            except Exception as e:
                logger.warning(f"[SD Cache] Failed to read {path}: {e}")
                cached_df = None

        if cached_df is not None and len(cached_df) > 0:
            # Append new candles + refresh last (potentially incomplete) candle.
            # Use ccxt 'since' (maps to OKX 'before' = forward pagination).
            last_ts = int(cached_df.index[-1].timestamp() * 1000)
            try:
                new_ohlcv = self._exchange.exchange.fetch_ohlcv(
                    symbol=self._exchange._to_ccxt(symbol),
                    timeframe=timeframe,
                    limit=300,
                    since=last_ts,
                )
                if new_ohlcv:
                    new_df = pd.DataFrame(
                        new_ohlcv,
                        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
                    )
                    new_df['timestamp'] = pd.to_datetime(new_df['timestamp'], unit='ms')
                    new_df.set_index('timestamp', inplace=True)
                    # Drop duplicates and append
                    combined = pd.concat([cached_df, new_df])
                    combined = combined[~combined.index.duplicated(keep='last')]
                    combined.sort_index(inplace=True)
                    # Save updated cache
                    self._memory[cache_key] = combined
                    try:
                        tmp = path.with_suffix('.tmp')
                        combined.to_parquet(tmp)
                        tmp.replace(path)
                    except Exception:
                        pass
                    return combined
                return cached_df
            except Exception as e:
                logger.debug(f"[SD Cache] Append failed for {symbol} {timeframe}: {e}")
                return cached_df

        # No cache — full fetch
        try:
            df = self._exchange.fetch_ohlcv(symbol, timeframe, limit)
            if df is not None and not df.empty:
                self._memory[cache_key] = df
                try:
                    tmp = path.with_suffix('.tmp')
                    df.to_parquet(tmp)
                    tmp.replace(path)
                    logger.info(
                        f"[SD Cache] Saved {len(df)} candles for {symbol} {timeframe}"
                    )
                except Exception:
                    pass
            return df
        except Exception as e:
            logger.error(f"[SD Cache] Fetch failed {symbol} {timeframe}: {e}")
            return pd.DataFrame()


class SDZoneCache:
    """In-memory cache for S/D zones across symbols and timeframes.

    Thread-safe via immutable dict updates (copy-on-write).
    """

    def __init__(self):
        self._zones: Dict[str, Dict[str, List[SDZone]]] = {}
        self._last_update: Dict[str, Dict[str, float]] = {}

    def update(self, symbol: str, timeframe: str, zones: List[SDZone]) -> None:
        """Replace zones for a symbol+timeframe."""
        sym_zones = {**self._zones.get(symbol, {}), timeframe: zones}
        self._zones = {**self._zones, symbol: sym_zones}

        sym_times = {**self._last_update.get(symbol, {}), timeframe: time.monotonic()}
        self._last_update = {**self._last_update, symbol: sym_times}

    def get(self, symbol: str, timeframe: str) -> List[SDZone]:
        """Get zones for symbol + timeframe."""
        return self._zones.get(symbol, {}).get(timeframe, [])

    def get_all_for_symbol(self, symbol: str) -> Dict[str, List[SDZone]]:
        """Get all timeframe zones for a symbol."""
        return dict(self._zones.get(symbol, {}))

    def get_all(self) -> Dict[str, Dict[str, List[SDZone]]]:
        """Get all cached zones."""
        return dict(self._zones)

    def needs_update(self, symbol: str, timeframe: str, max_age: float) -> bool:
        """Check if zones need recalculation."""
        last = self._last_update.get(symbol, {}).get(timeframe, 0)
        return (time.monotonic() - last) >= max_age

    def save_to_file(self, path: Path) -> None:
        """Atomic write zones to JSON file for dashboard consumption."""
        data = {}
        for symbol, tf_zones in self._zones.items():
            data[symbol] = {}
            for tf, zones in tf_zones.items():
                data[symbol][tf] = [
                    {
                        "type": z.zone_type,
                        "top": z.top,
                        "bottom": z.bottom,
                        "delta": z.delta,
                        "delta_pct": z.delta_pct,
                        "timeframe": z.timeframe,
                        "created_time": z.created_time,
                        "tested": z.tested,
                    }
                    for z in zones
                ]
            # Total supply/demand volume for this timeframe
            supply_total = sum(z.delta for z in zones if z.zone_type == "supply")
            demand_total = sum(z.delta for z in zones if z.zone_type == "demand")
            data[symbol][f"{tf}_totals"] = {
                "total_supply": round(supply_total, 2),
                "total_demand": round(demand_total, 2),
            }
        data["_updated_at"] = datetime.now().isoformat()

        tmp_path = path.with_suffix('.tmp')
        tmp_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        tmp_path.replace(path)

    def format_telegram(self, symbol: str, current_price: Optional[float] = None) -> str:
        """Format zones for Telegram display."""
        all_zones = self.get_all_for_symbol(symbol)
        if not all_zones:
            return f"No S/D zones for {symbol}"

        lines = [f"*S/D Zones: {symbol}*"]
        if current_price:
            lines.append(f"Price: {current_price:.4g}")
        lines.append("")

        for tf in ["5m", "15m", "1h", "4h", "1d"]:
            zones = all_zones.get(tf, [])
            if not zones:
                continue

            supply = [z for z in zones if z.zone_type == "supply"]
            demand = [z for z in zones if z.zone_type == "demand"]

            supply_total = sum(z.delta for z in supply)
            demand_total = sum(z.delta for z in demand)

            lines.append(f"*{tf.upper()}*")
            if supply:
                lines.append("Supply:")
                for z in supply[:3]:
                    tested_mark = " T" if z.tested else ""
                    lines.append(f"  {z.bottom:.4g}-{z.top:.4g} | {z.delta_pct:.1f}%{tested_mark}")
            if demand:
                lines.append("Demand:")
                for z in demand[:3]:
                    tested_mark = " T" if z.tested else ""
                    lines.append(f"  {z.bottom:.4g}-{z.top:.4g} | {z.delta_pct:.1f}%{tested_mark}")

            # Total imbalance
            if supply_total or demand_total:
                def _fmt_vol(v):
                    av = abs(v)
                    if av >= 1e9:
                        return f"{v/1e9:.1f}B"
                    if av >= 1e6:
                        return f"{v/1e6:.1f}M"
                    if av >= 1e3:
                        return f"{v/1e3:.1f}K"
                    return f"{v:.0f}"
                lines.append(f"  Total: S {_fmt_vol(supply_total)} / D {_fmt_vol(demand_total)}")
            lines.append("")

        return "\n".join(lines)


# Timeframe hierarchy for SD zone blocking
# Higher rank = higher timeframe. Used to compare zone TF vs position TF.
TF_RANK = {
    'm5': 0, '5m': 0,
    'm15': 1, '15m': 1,
    'h1': 2, '1h': 2,
    'h4': 3, '4h': 3,
    'd1': 4, '1d': 4,
}


def get_tf_rank(timeframe: str) -> int:
    """Get numeric rank for a timeframe string. Higher = higher timeframe."""
    rank = TF_RANK.get(timeframe.lower(), -1)
    if rank == -1:
        logger.warning(f"[SD-BLOCK] Unknown timeframe: {timeframe}")
    return rank


def get_position_tf(entry_type: str) -> Optional[str]:
    """Extract timeframe from entry_type string.

    Examples:
        'standard_m15' → 'm15'
        'ema610_h1' → 'h1'
        'rsi_div_h4' → 'h4'
        'sd_zone_m15' → 'm15'
    """
    for prefix in ("standard_", "ema610_", "rsi_div_", "sd_zone_", "sd_demand_", "sd_supply_"):
        if entry_type.startswith(prefix):
            return entry_type[len(prefix):]
    return None
