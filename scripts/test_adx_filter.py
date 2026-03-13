"""
Test ADX filter impact on BTC backtest.
H1 ADX < threshold → skip ALL entries (sideway/weak trend).

Approach: Subclass FuturesBacktester, override _backtest_symbol to inject
ADX check before each entry point. Does NOT modify engine.py or any source.

Uses best config from optimization: CE(34,4.0) + WIDE_SL.
"""

import sys
import json
import logging
import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import pandas as pd
import numpy as np

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.trading.backtest.engine import (
    FuturesBacktester, BacktestResult, load_cached_ohlcv, CE_GRACE_CANDLES,
)
from src.trading.core.indicators import ATRIndicator, TechnicalIndicators

logging.basicConfig(level=logging.WARNING)
logging.getLogger("src.trading").setLevel(logging.WARNING)

SYMBOL = "BTCUSDT"
START_DATE = "2025-01-01"
END_DATE = "2026-02-21"
INITIAL_BALANCE = 10000
OUTPUT_FILE = project_root / "data" / "test_adx_filter_results.json"


# ─── ADX Calculation ────────────────────────────────────────────
def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate ADX (Average Directional Index) from OHLC DataFrame."""
    high = df['high']
    low = df['low']
    close = df['close']

    # +DM and -DM
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Smoothed averages (Wilder's smoothing)
    atr = tr.ewm(alpha=1 / period, min_periods=period).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, min_periods=period).mean() / atr)

    # DX and ADX
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=1 / period, min_periods=period).mean()

    return adx


class ADXFilterBacktester(FuturesBacktester):
    """Backtest engine with ADX filter. Skips entries when H1 ADX < threshold."""

    def __init__(self, adx_threshold: float = 25, adx_period: int = 14, **kwargs):
        super().__init__(**kwargs)
        self.adx_threshold = adx_threshold
        self.adx_period = adx_period
        self.entries_blocked = 0
        self.entries_allowed = 0

    def _backtest_symbol(self, symbol, since, until):
        """Override: identical to parent but adds ADX gate before each entry."""

        # ── Fetch data (same as parent) ──
        warmup = timedelta(days=120)
        fetch_since = since - warmup

        df_m15 = load_cached_ohlcv(self.client, symbol, '15m', fetch_since, until)
        df_h1 = load_cached_ohlcv(self.client, symbol, '1h', fetch_since, until)
        df_h4 = load_cached_ohlcv(self.client, symbol, '4h', fetch_since, until)

        if df_m15.empty or df_h1.empty or df_h4.empty:
            return [], {}

        # ── Pre-compute indicators (same as parent) ──
        ch_period = self._cfg_chandelier.get('period', 34)
        ch_mult = self._cfg_chandelier.get('multiplier', 4.0)

        df_m15['atr'] = ATRIndicator.calculate_atr(df_m15, 14)
        ch_long_m15, ch_short_m15 = ATRIndicator.chandelier_exit(df_m15, ch_period, ch_mult)
        df_m15['chandelier_long'] = ch_long_m15
        df_m15['chandelier_short'] = ch_short_m15
        df_m15['ema200'] = TechnicalIndicators.calculate_ema(df_m15['close'], 200)
        df_m15['vol_avg21'] = df_m15['volume'].rolling(window=21).mean()
        df_m15['ema34'] = TechnicalIndicators.calculate_ema(df_m15['close'], 34)
        df_m15['ema89'] = TechnicalIndicators.calculate_ema(df_m15['close'], 89)

        if len(df_h1) >= 610:
            df_h1['ema610'] = TechnicalIndicators.calculate_ema(df_h1['close'], 610)
        else:
            df_h1['ema610'] = float('nan')
        ch_long_h1, ch_short_h1 = ATRIndicator.chandelier_exit(df_h1, ch_period, ch_mult)
        df_h1['chandelier_long'] = ch_long_h1
        df_h1['chandelier_short'] = ch_short_h1
        df_h1['ema34'] = TechnicalIndicators.calculate_ema(df_h1['close'], 34)
        df_h1['ema89'] = TechnicalIndicators.calculate_ema(df_h1['close'], 89)

        if len(df_h4) >= 610:
            df_h4['ema610'] = TechnicalIndicators.calculate_ema(df_h4['close'], 610)
        else:
            df_h4['ema610'] = float('nan')
        ch_long_h4, ch_short_h4 = ATRIndicator.chandelier_exit(df_h4, ch_period, ch_mult)
        df_h4['chandelier_long'] = ch_long_h4
        df_h4['chandelier_short'] = ch_short_h4
        df_h4['ema34'] = TechnicalIndicators.calculate_ema(df_h4['close'], 34)
        df_h4['ema89'] = TechnicalIndicators.calculate_ema(df_h4['close'], 89)

        # ── ADX on H1 (the filter — H1 as primary ADX timeframe) ──
        df_h1['adx'] = calculate_adx(df_h1, self.adx_period)

        # ── Simulation state (same as parent) ──
        trades: List[Dict] = []
        all_positions: List[Dict] = []
        signaled_m15: set = set()
        signaled_h1: set = set()
        signaled_h4: set = set()
        last_scanned_m15 = None
        last_scanned_h1 = None
        last_scanned_h4 = None
        sl_cooldown_m15: Dict = {}
        sl_cooldown_h1: Dict = {}
        sl_cooldown_h4: Dict = {}
        last_ema610_h1_entry_candle_ts = None
        last_ema610_h4_entry_candle_ts = None
        prev_h1_candle_ts = None
        prev_h4_candle_ts = None

        def _safe_float(series, idx):
            val = series.iloc[idx]
            return float(val) if not pd.isna(val) else None

        def _active_of_type(entry_type):
            return [p for p in all_positions if p.get('status') != 'CLOSED' and p.get('entry_type') == entry_type]

        def _all_active():
            return [p for p in all_positions if p.get('status') != 'CLOSED']

        def _get_h1_adx(h1_slice_local):
            """Get current H1 ADX value."""
            if h1_slice_local.empty:
                return None
            last_idx = df_h1.index.get_loc(h1_slice_local.index[-1])
            return _safe_float(df_h1['adx'], last_idx)

        def _adx_allows_entry(h1_slice_local):
            """Returns True if H1 ADX >= threshold (trending), False if sideway."""
            adx_val = _get_h1_adx(h1_slice_local)
            if adx_val is None:
                return True  # No data → allow
            return adx_val >= self.adx_threshold

        m15_in_range = df_m15[df_m15.index >= since]

        for idx in range(len(m15_in_range)):
            current_ts = m15_in_range.index[idx]
            current_candle = m15_in_range.iloc[idx]
            close_price = float(current_candle['close'])
            high_price = float(current_candle['high'])
            low_price = float(current_candle['low'])

            m15_global_idx = df_m15.index.get_loc(current_ts)

            m15_ch_long = _safe_float(df_m15['chandelier_long'], m15_global_idx)
            m15_ch_short = _safe_float(df_m15['chandelier_short'], m15_global_idx)
            m15_ema200 = _safe_float(df_m15['ema200'], m15_global_idx)
            m15_vol = float(current_candle['volume']) if not pd.isna(current_candle['volume']) else 0
            m15_vol_avg = _safe_float(df_m15['vol_avg21'], m15_global_idx)

            # H1/H4 candle detection
            current_h1_ts = current_ts.floor('1h')
            current_h4_ts = current_ts.floor('4h')

            h1_candle_closed = prev_h1_candle_ts is not None and current_h1_ts != prev_h1_candle_ts
            h4_candle_closed = prev_h4_candle_ts is not None and current_h4_ts != prev_h4_candle_ts

            h4_slice = df_h4[df_h4.index <= current_ts]
            h1_slice = df_h1[df_h1.index <= current_ts]

            h1_close = h1_high = h1_low = None
            h4_close = h4_high = h4_low = None
            h1_ch_long = h1_ch_short = None
            h4_ch_long = h4_ch_short = None

            if len(h1_slice) > 0:
                h1_idx = df_h1.index.get_loc(h1_slice.index[-1])
                h1_close = _safe_float(df_h1['close'], h1_idx)
                h1_high = _safe_float(df_h1['high'], h1_idx)
                h1_low = _safe_float(df_h1['low'], h1_idx)
                h1_ch_long = _safe_float(df_h1['chandelier_long'], h1_idx)
                h1_ch_short = _safe_float(df_h1['chandelier_short'], h1_idx)

            if len(h4_slice) > 0:
                h4_idx = df_h4.index.get_loc(h4_slice.index[-1])
                h4_close = _safe_float(df_h4['close'], h4_idx)
                h4_high = _safe_float(df_h4['high'], h4_idx)
                h4_low = _safe_float(df_h4['low'], h4_idx)
                h4_ch_long = _safe_float(df_h4['chandelier_long'], h4_idx)
                h4_ch_short = _safe_float(df_h4['chandelier_short'], h4_idx)

            # ── EXITS (identical to parent) ──
            for pos in all_positions:
                if pos.get('status') == 'CLOSED':
                    continue
                pos['candles_since_entry'] = pos.get('candles_since_entry', 0) + 1
                grace = CE_GRACE_CANDLES.get(pos['entry_type'], 1)
                if not pos.get('ce_armed', False) and pos['candles_since_entry'] >= grace:
                    if self._cfg_chandelier.get('enabled', True):
                        pos['ce_armed'] = True

            for pos in all_positions:
                if pos.get('status') == 'CLOSED' or not pos['entry_type'].startswith('STANDARD_'):
                    continue
                exit_trades = self._check_exits_standard(
                    pos, close_price, high_price, low_price,
                    m15_ch_long, m15_ch_short, m15_ema200, m15_vol, m15_vol_avg,
                )
                for et in exit_trades:
                    et['close_time'] = current_ts
                    trades.append(et)
                    self.balance += et['margin_returned'] + et['pnl']

            if h1_candle_closed and h1_close is not None:
                for pos in all_positions:
                    if pos.get('status') == 'CLOSED' or pos.get('entry_type') != 'EMA610_H1':
                        continue
                    fb_longs = [m15_ch_long]
                    fb_shorts = [m15_ch_short]
                    exit_trades = self._check_exits_ema610(
                        pos, h1_close, h1_high, h1_low,
                        h1_ch_long, h1_ch_short, fb_longs, fb_shorts, 'h1',
                    )
                    for et in exit_trades:
                        et['close_time'] = current_ts
                        trades.append(et)
                        self.balance += et['margin_returned'] + et['pnl']

            if h4_candle_closed and h4_close is not None:
                for pos in all_positions:
                    if pos.get('status') == 'CLOSED' or pos.get('entry_type') != 'EMA610_H4':
                        continue
                    cur_h1_ch_long = cur_h1_ch_short = None
                    if len(h1_slice) > 0:
                        cur_h1_idx = df_h1.index.get_loc(h1_slice.index[-1])
                        cur_h1_ch_long = _safe_float(df_h1['chandelier_long'], cur_h1_idx)
                        cur_h1_ch_short = _safe_float(df_h1['chandelier_short'], cur_h1_idx)
                    fb_longs = [cur_h1_ch_long, m15_ch_long]
                    fb_shorts = [cur_h1_ch_short, m15_ch_short]
                    exit_trades = self._check_exits_ema610(
                        pos, h4_close, h4_high, h4_low,
                        h4_ch_long, h4_ch_short, fb_longs, fb_shorts, 'h4',
                    )
                    for et in exit_trades:
                        et['close_time'] = current_ts
                        trades.append(et)
                        self.balance += et['margin_returned'] + et['pnl']

            # ── ENTRIES (with ADX gate) ──
            if idx < 1:
                prev_h1_candle_ts = current_h1_ts
                prev_h4_candle_ts = current_h4_ts
                continue

            m15_slice = df_m15[df_m15.index <= current_ts]
            if len(h4_slice) < 100 or len(h1_slice) < 100 or len(m15_slice) < 100:
                prev_h1_candle_ts = current_h1_ts
                prev_h4_candle_ts = current_h4_ts
                continue

            h4_trend = self._detect_h4_trend(h4_slice)

            if h4_trend:
                ema610_side = 'BUY' if h4_trend == 'BUY_TREND' else 'SELL'

                # ══════════════════════════════════════════════
                # ADX GATE — skip ALL entries if H1 ADX < threshold
                # ══════════════════════════════════════════════
                adx_ok = _adx_allows_entry(h1_slice)
                if not adx_ok:
                    self.entries_blocked += 1
                    prev_h1_candle_ts = current_h1_ts
                    prev_h4_candle_ts = current_h4_ts
                    continue
                self.entries_allowed += 1

                # ── H4 Standard Entry ──
                if h4_candle_closed and len(h4_slice) >= 2:
                    closed_h4_ts_str = str(h4_slice.index[-2])
                    dedup_key = f"{symbol}:{closed_h4_ts_str}"
                    if dedup_key not in signaled_h4:
                        new_scanned = closed_h4_ts_str
                        if last_scanned_h4 is not None and new_scanned != last_scanned_h4:
                            signal = self._detect_wick_entry(h4_slice, h4_trend)
                            if signal:
                                if len(_active_of_type('STANDARD_H4')) == 0:
                                    margin = float(self._cfg_risk['fixed_margin'])
                                    if self._can_open_with_margin(margin, _all_active(), close_price):
                                        entry_price = float(h4_slice.iloc[-2]['close'])
                                        pos = self._open_standard_position(symbol, signal, entry_price, current_ts, entry_type='STANDARD_H4')
                                        if pos:
                                            all_positions.append(pos)
                                            self.balance -= pos['margin']
                                            signaled_h4.add(dedup_key)
                        last_scanned_h4 = new_scanned

                # ── H1 trend + entries ──
                h1_trend_ok = self._check_tf_trend(h1_slice, h4_trend)
                if h1_trend_ok:
                    rsi_ok = self._check_h1_rsi_filter(h1_slice, h4_trend)
                    div_ok = self._check_divergence_filter(h1_slice, h4_slice, h4_trend) if rsi_ok else False

                    if rsi_ok and div_ok and h1_candle_closed and len(h1_slice) >= 2:
                        closed_h1_ts_str = str(h1_slice.index[-2])
                        dedup_key = f"{symbol}:{closed_h1_ts_str}"
                        if dedup_key not in signaled_h1:
                            new_scanned = closed_h1_ts_str
                            if last_scanned_h1 is not None and new_scanned != last_scanned_h1:
                                signal = self._detect_wick_entry(h1_slice, h4_trend)
                                if signal:
                                    if len(_active_of_type('STANDARD_H1')) == 0:
                                        margin = float(self._cfg_risk['fixed_margin'])
                                        if self._can_open_with_margin(margin, _all_active(), close_price):
                                            entry_price = float(h1_slice.iloc[-2]['close'])
                                            pos = self._open_standard_position(symbol, signal, entry_price, current_ts, entry_type='STANDARD_H1')
                                            if pos:
                                                all_positions.append(pos)
                                                self.balance -= pos['margin']
                                                signaled_h1.add(dedup_key)
                            last_scanned_h1 = new_scanned

                    m15_trend_ok = self._check_tf_trend(m15_slice, h4_trend)
                    if m15_trend_ok and rsi_ok and div_ok:
                        closed_m15_ts_str = str(m15_in_range.index[idx - 1])
                        dedup_key = f"{symbol}:{closed_m15_ts_str}"
                        if dedup_key not in signaled_m15:
                            new_scanned = closed_m15_ts_str
                            if last_scanned_m15 is not None and new_scanned != last_scanned_m15:
                                sl_cd = sl_cooldown_m15.get(symbol)
                                if sl_cd and sl_cd == new_scanned:
                                    pass
                                else:
                                    if sl_cd and sl_cd != new_scanned:
                                        del sl_cooldown_m15[symbol]
                                    signal = self._detect_wick_entry(m15_slice, h4_trend)
                                    if signal:
                                        if len(_active_of_type('STANDARD_M15')) == 0:
                                            margin = float(self._cfg_risk['fixed_margin'])
                                            if self._can_open_with_margin(margin, _all_active(), close_price):
                                                entry_price = float(m15_slice.iloc[-2]['close'])
                                                pos = self._open_standard_position(symbol, signal, entry_price, current_ts, entry_type='STANDARD_M15')
                                                if pos:
                                                    all_positions.append(pos)
                                                    self.balance -= pos['margin']
                                                    signaled_m15.add(dedup_key)
                            last_scanned_m15 = new_scanned

                # ── EMA610 H1 Entry ──
                if self._cfg_ema610_entry.get('enabled', True) and h1_candle_closed and len(h1_slice) >= 2:
                    h1_slice_closed = df_h1[df_h1.index <= h1_slice.index[-2]]
                    active_h1_ema = [p for p in all_positions if p.get('status') == 'OPEN' and p.get('entry_type') == 'EMA610_H1']
                    can_open_h1 = len(active_h1_ema) == 0
                    closed_h1_val = h1_slice.index[-2]
                    if can_open_h1 and last_ema610_h1_entry_candle_ts is not None:
                        if closed_h1_val == last_ema610_h1_entry_candle_ts:
                            can_open_h1 = False
                    if can_open_h1:
                        ema610_h1_margin = self._cfg_risk['fixed_margin'] * self._cfg_risk.get('ema610_margin_multiplier', 1)
                        can_open_h1 = self._can_open_with_margin(ema610_h1_margin, _all_active(), close_price)
                    if can_open_h1 and len(h1_slice_closed) >= 610:
                        ema610_signal = self._check_ema610_touch(h1_slice_closed, 'h1', ema610_side, close_price)
                        if ema610_signal:
                            pos = self._open_ema610_position(symbol, ema610_signal, current_ts, 'h1')
                            if pos:
                                all_positions.append(pos)
                                last_ema610_h1_entry_candle_ts = closed_h1_val
                                self.balance -= pos['margin']

                # ── EMA610 H4 Entry ──
                if self._cfg_ema610_entry.get('enabled', True) and h4_candle_closed and len(h4_slice) >= 2:
                    h4_slice_closed = df_h4[df_h4.index <= h4_slice.index[-2]]
                    active_h4_ema = [p for p in all_positions if p.get('status') != 'CLOSED' and p.get('entry_type') == 'EMA610_H4']
                    can_open_h4 = len(active_h4_ema) == 0
                    closed_h4_val = h4_slice.index[-2]
                    if can_open_h4 and last_ema610_h4_entry_candle_ts is not None:
                        if closed_h4_val == last_ema610_h4_entry_candle_ts:
                            can_open_h4 = False
                    if can_open_h4:
                        ema610_h4_margin = self._cfg_risk['fixed_margin'] * self._cfg_risk.get('ema610_h4_margin_multiplier', 1)
                        can_open_h4 = self._can_open_with_margin(ema610_h4_margin, _all_active(), close_price)
                    if can_open_h4 and len(h4_slice_closed) >= 610:
                        ema610_signal = self._check_ema610_touch(h4_slice_closed, 'h4', ema610_side, close_price)
                        if ema610_signal:
                            pos = self._open_ema610_position(symbol, ema610_signal, current_ts, 'h4')
                            if pos:
                                all_positions.append(pos)
                                last_ema610_h4_entry_candle_ts = closed_h4_val
                                self.balance -= pos['margin']

            prev_h1_candle_ts = current_h1_ts
            prev_h4_candle_ts = current_h4_ts

        # ── Close remaining + build trades ──
        for pos in all_positions:
            if pos.get('status') == 'CLOSED':
                continue
            exit_price = close_price
            side = pos['side']
            rem = pos['remaining_size']
            if side == 'BUY':
                pnl = (exit_price - pos['entry_price']) * rem
            else:
                pnl = (pos['entry_price'] - exit_price) * rem
            exit_fee = self._calc_fee(rem * exit_price, 'taker')
            pnl -= exit_fee
            margin_returned = pos['margin'] * (rem / pos['size'])
            trades.append({
                'symbol': symbol, 'side': side,
                'entry_price': pos['entry_price'], 'exit_price': exit_price,
                'size': rem, 'pnl': round(pnl, 4),
                'margin': pos['margin'], 'margin_returned': round(margin_returned, 4),
                'open_time': pos['open_time'], 'close_time': m15_in_range.index[-1],
                'close_reason': 'END_OF_BACKTEST',
                'entry_type': pos.get('entry_type', 'STANDARD_M15'),
                'entry_fee': pos.get('entry_fee', 0), 'exit_fee': round(exit_fee, 4),
            })
            self.balance += margin_returned + pnl
            pos['status'] = 'CLOSED'

        chart_dfs = {'m15': df_m15, 'h1': df_h1, 'h4': df_h4}
        return trades, chart_dfs


def run_test(adx_threshold):
    """Run single backtest with ADX threshold, return summary."""
    overrides = {
        "CHANDELIER_EXIT": {"period": 34, "multiplier": 4.0},
        "EMA610_ENTRY": {"tolerance": 0.005},
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 50, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 70, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 30, "tp1_percent": 70},
            "h1": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 40, "tp1_percent": 70},
            "h4": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 60, "tp1_percent": 70},
        },
    }

    label = f"ADX>={adx_threshold}" if adx_threshold > 0 else "NO_FILTER"

    try:
        engine = ADXFilterBacktester(
            adx_threshold=adx_threshold,
            adx_period=14,
            symbols=[SYMBOL],
            initial_balance=INITIAL_BALANCE,
            enable_divergence=True,
            config_overrides=overrides,
        )
        result = engine.backtest(START_DATE, END_DATE)

        return {
            "label": label,
            "adx_threshold": adx_threshold,
            "total_pnl": round(result.total_pnl, 2),
            "total_trades": result.total_trades,
            "win_rate": round(result.win_rate, 1),
            "profit_factor": round(result.profit_factor, 2),
            "max_drawdown": round(result.max_drawdown, 2),
            "avg_win": round(result.avg_win, 2),
            "avg_loss": round(result.avg_loss, 2),
            "total_fees": round(result.total_fees, 2),
            "entries_blocked": engine.entries_blocked,
            "entries_allowed": engine.entries_allowed,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"label": label, "error": str(e), "total_pnl": float("-inf")}


def main():
    # Test ADX thresholds: 0 (no filter), 15, 20, 25, 30, 35
    thresholds = [0, 15, 20, 25, 30, 35]
    total = len(thresholds)

    print(f"={'=' * 70}")
    print(f"ADX Filter Test - {total} thresholds")
    print(f"Base: CE(34,4.0) + WIDE_SL | Period: {START_DATE} to {END_DATE}")
    print(f"Thresholds: {thresholds}")
    print(f"={'=' * 70}")
    print()

    results = []
    start_time = datetime.now()

    for i, thresh in enumerate(thresholds, 1):
        print(f"[{i}/{total}] ADX >= {thresh:2d}  ", end="", flush=True)
        r = run_test(thresh)
        results.append(r)

        if "error" in r:
            print(f"ERROR: {r['error']}")
        else:
            blocked_pct = r['entries_blocked'] / max(r['entries_blocked'] + r['entries_allowed'], 1) * 100
            print(
                f"PNL: ${r['total_pnl']:>10,.2f} | "
                f"Trades: {r['total_trades']:>4} | "
                f"WR: {r['win_rate']:>5.1f}% | "
                f"PF: {r['profit_factor']:>5.2f} | "
                f"MDD: ${r['max_drawdown']:>8,.2f} | "
                f"Blocked: {blocked_pct:.0f}%"
            )

        # Incremental save
        with open(OUTPUT_FILE, "w") as f:
            json.dump({"results": results, "run_date": datetime.now().isoformat()}, f, indent=2)

    # Summary
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["total_pnl"], reverse=True)

    print(f"\n{'=' * 70}")
    print(f"RANKING BY PNL")
    print(f"{'=' * 70}")
    for rank, r in enumerate(valid, 1):
        blocked_pct = r['entries_blocked'] / max(r['entries_blocked'] + r['entries_allowed'], 1) * 100
        print(
            f"  #{rank}  ADX >= {r['adx_threshold']:2d}  "
            f"PNL: ${r['total_pnl']:>10,.2f}  "
            f"Trades: {r['total_trades']:>4}  "
            f"WR: {r['win_rate']:>5.1f}%  "
            f"PF: {r['profit_factor']:>5.2f}  "
            f"MDD: ${r['max_drawdown']:>8,.2f}  "
            f"Blocked: {blocked_pct:.0f}%"
        )

    baseline = next((r for r in valid if r['adx_threshold'] == 0), None)
    if baseline and valid[0]['total_pnl'] > baseline['total_pnl']:
        diff = valid[0]['total_pnl'] - baseline['total_pnl']
        print(f"\n  Best ADX filter ({valid[0]['label']}) improves PNL by ${diff:,.2f}")

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Total time: {total_time/60:.1f} minutes")


if __name__ == "__main__":
    main()
