"""
Backtesting Engine for Futures Trading Strategy V7.4

Rebuilt to match live bot logic exactly:
- Signal detection mirrors signal_detector.py cascade
- Exit logic mirrors position_manager.py + bot.py
- All config from config.py (single source of truth)

Entry types (9):
  STANDARD_M5:  H4 trend → ADX H1 → H1 trend → M15 trend → RSI + Divergence → M5 wick
  STANDARD_M15: H4 trend → ADX H1 → H1 trend → M15 trend → RSI + Divergence → M15 wick
  STANDARD_H1:  H4 trend → ADX H1 → H1 trend → RSI + Divergence → H1 wick
  STANDARD_H4:  H4 trend → ADX H1 → H4 wick
  EMA610_H1:    H4 EMA34+89 vs EMA610 → ADX H1 → H1 EMA alignment → H1 EMA610 touch
  EMA610_H4:    H4 EMA34+89 vs EMA610 → ADX H1 → H4 EMA610 touch
  RSI_DIV_M15:  M15 RSI divergence → close EMA positions → leverage 1.5x → EMA34 dynamic TP
  RSI_DIV_H1:   H1 RSI divergence → close EMA positions → leverage 1.5x → EMA34 dynamic TP
  RSI_DIV_H4:   H4 RSI divergence → close EMA positions → leverage 1.5x → EMA34 dynamic TP

Exit priority:
  1. Hard SL (ROI-based, intra-candle wick trigger)
  2. Chandelier Exit trailing SL (close-price trigger for Standard, with CE grace + validation)
  3. TP1 partial close (intra-candle trigger)
  4. TP2 full close (intra-candle trigger)
"""

import json
import math
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging

from src.trading.exchanges.binance import BinanceFuturesClient
from src.trading.core.indicators import (
    TechnicalIndicators,
    RSIDivergence,
    ATRIndicator,
    ADXIndicator,
)
from src.trading.core.config import (
    LEVERAGE,
    RISK_MANAGEMENT,
    INDICATORS,
    ENTRY,
    STANDARD_ENTRY,
    DIVERGENCE_CONFIG,
    CHANDELIER_EXIT,
    SMART_SL,
    EMA610_ENTRY,
    STANDARD_EXIT,
    EMA610_EXIT,
    RSI_DIV_EXIT,
    FEES,
)

logger = logging.getLogger(__name__)

# ── Apply runtime config overrides from web dashboard ────────────
# Web dashboard saves config changes to data/config.json.
# Apply those overrides so backtest matches the live bot.
def _apply_config_overrides():
    """Merge data/config.json overrides into imported config dicts."""
    config_file = Path(__file__).resolve().parent.parent.parent.parent / "data" / "config.json"
    if not config_file.exists():
        return

    try:
        with open(config_file, 'r') as f:
            overrides = json.load(f)
    except Exception:
        return

    config_map = {
        'STANDARD_EXIT': STANDARD_EXIT,
        'EMA610_EXIT': EMA610_EXIT,
        'RSI_DIV_EXIT': RSI_DIV_EXIT,
        'RISK_MANAGEMENT': RISK_MANAGEMENT,
        'CHANDELIER_EXIT': CHANDELIER_EXIT,
        'SMART_SL': SMART_SL,
        'EMA610_ENTRY': EMA610_ENTRY,
        'INDICATORS': INDICATORS,
        'ENTRY': ENTRY,
        'FEES': FEES,
    }

    for section, target in config_map.items():
        source = overrides.get(section)
        if not source or not isinstance(source, dict):
            continue
        for k, v in source.items():
            if k in target and isinstance(v, dict) and isinstance(target[k], dict):
                target[k].update(v)
            elif k in target:
                target[k] = v

    # LEVERAGE: full replacement
    lev = overrides.get('LEVERAGE')
    if lev and isinstance(lev, dict):
        LEVERAGE.clear()
        LEVERAGE.update(lev)

    logger.info(f"Backtest: applied config overrides from {config_file}")

_apply_config_overrides()

# ==============================================================================
# CE Grace Period (in M15 candles)
# ==============================================================================
CE_GRACE_CANDLES = {
    'STANDARD_M5': 1,     # 1 M15 candle (exits run on M15 resolution)
    'STANDARD_M15': 1,    # 1 M15 candle = 15 min
    'STANDARD_H1': 4,     # 4 M15 candles = 1 hour
    'STANDARD_H4': 16,    # 16 M15 candles = 4 hours
    'EMA610_H1': 1,
    'EMA610_H4': 1,
    'RSI_DIV_M15': 1,     # 1 M15 candle = 15 min
    'RSI_DIV_H1': 4,      # 4 M15 candles = 1 hour
    'RSI_DIV_H4': 16,     # 16 M15 candles = 4 hours
}


# ==============================================================================
# Data Fetching
# ==============================================================================

def fetch_full_ohlcv(
    client: BinanceFuturesClient,
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime = None,
) -> pd.DataFrame:
    """Fetch OHLCV data in batches for a full date range."""
    if until is None:
        until = datetime.now()

    tf_ms = {
        '1m': 60_000, '3m': 180_000, '5m': 300_000,
        '15m': 900_000, '30m': 1_800_000,
        '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000,
        '1d': 86_400_000,
    }

    candle_ms = tf_ms.get(timeframe, 3_600_000)
    batch_size = 1000
    batch_ms = candle_ms * batch_size

    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)

    all_frames: List[pd.DataFrame] = []
    current_ms = since_ms

    while current_ms < until_ms:
        try:
            ohlcv = client.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=current_ms,
                limit=batch_size,
            )

            if not ohlcv:
                break

            df_batch = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'],
            )
            df_batch['timestamp'] = pd.to_datetime(df_batch['timestamp'], unit='ms')
            df_batch.set_index('timestamp', inplace=True)

            all_frames.append(df_batch)

            last_ts = int(ohlcv[-1][0])
            if last_ts <= current_ms:
                break
            current_ms = last_ts + candle_ms

            time.sleep(0.15)

        except Exception as e:
            logger.error(f"Error fetching {symbol} {timeframe} batch: {e}")
            time.sleep(1)
            current_ms += batch_ms

    if not all_frames:
        return pd.DataFrame()

    df = pd.concat(all_frames)
    df = df[~df.index.duplicated(keep='last')]
    df = df.sort_index()
    df = df[df.index <= until]

    logger.info(f"Fetched {symbol} {timeframe}: {len(df)} candles ({df.index[0]} -> {df.index[-1]})")
    return df


def load_cached_ohlcv(
    client: BinanceFuturesClient,
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime = None,
) -> pd.DataFrame:
    """Load OHLCV from Parquet cache if available, else fetch from API."""
    from pathlib import Path

    if until is None:
        until = datetime.now()

    cache_dir = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ohlcv"
    cache_file = cache_dir / f"{symbol}_{timeframe}.parquet"

    if cache_file.exists():
        try:
            df = pd.read_parquet(cache_file)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index)

            df = df[(df.index >= since) & (df.index <= until)]

            if not df.empty:
                logger.info(
                    f"Loaded {symbol} {timeframe} from cache: {len(df)} candles "
                    f"({df.index[0]} -> {df.index[-1]})"
                )
                return df
        except Exception as e:
            logger.warning(f"Cache read failed for {symbol} {timeframe}: {e}")

    return fetch_full_ohlcv(client, symbol, timeframe, since, until)


# ==============================================================================
# Backtest Result
# ==============================================================================

class BacktestResult:
    """Backtest results container with detailed stats by entry type"""

    def __init__(self):
        self.trades: List[Dict] = []
        self.total_trades = 0
        self.winning_trades = 0
        self.losing_trades = 0
        self.total_pnl = 0.0
        self.total_fees = 0.0
        self.win_rate = 0.0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.max_drawdown = 0.0
        self.profit_factor = 0.0
        self.risk_reward = 0.0
        self.tp1_hits = {
            'STANDARD_M5': 0, 'STANDARD_M15': 0, 'STANDARD_H1': 0, 'STANDARD_H4': 0,
            'EMA610_H1': 0, 'EMA610_H4': 0,
            'RSI_DIV_M15': 0, 'RSI_DIV_H1': 0, 'RSI_DIV_H4': 0,
        }
        self.tp2_hits = {
            'STANDARD_M5': 0, 'STANDARD_M15': 0, 'STANDARD_H1': 0, 'STANDARD_H4': 0,
            'EMA610_H1': 0, 'EMA610_H4': 0,
            'RSI_DIV_M15': 0, 'RSI_DIV_H1': 0, 'RSI_DIV_H4': 0,
        }
        self.chart_data: Dict = {}
        self.chart_json: Dict = {}

    def calculate_metrics(self):
        if not self.trades:
            return

        self.total_trades = len(self.trades)
        wins = [t for t in self.trades if t['pnl'] > 0]
        losses = [t for t in self.trades if t['pnl'] <= 0]

        self.winning_trades = len(wins)
        self.losing_trades = len(losses)
        self.total_pnl = sum(t['pnl'] for t in self.trades)
        self.total_fees = sum(t.get('fees', 0) for t in self.trades)
        self.win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        self.avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
        self.avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0

        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in self.trades:
            cum_pnl += t['pnl']
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd:
                max_dd = dd
        self.max_drawdown = max_dd

        total_wins = sum(t['pnl'] for t in wins) if wins else 0
        total_losses = abs(sum(t['pnl'] for t in losses)) if losses else 0
        self.profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
        self.risk_reward = abs(self.avg_win / self.avg_loss) if self.avg_loss != 0 else float('inf')

        # Aggregate TP hits per position (deduplicated)
        position_tp = {}
        for t in self.trades:
            key = (t.get('symbol'), t.get('open_time'), t.get('side'), t.get('entry_type'))
            et = t.get('entry_type', 'STANDARD_M15')
            if key not in position_tp:
                position_tp[key] = {'tp1': False, 'tp2': False, 'entry_type': et}
            if t.get('_tp1_hit_tracked', False):
                position_tp[key]['tp1'] = True
            if t.get('_tp2_hit_tracked', False):
                position_tp[key]['tp2'] = True

        for pos_data in position_tp.values():
            et = pos_data['entry_type']
            if pos_data['tp1']:
                self.tp1_hits[et] = self.tp1_hits.get(et, 0) + 1
            if pos_data['tp2']:
                self.tp2_hits[et] = self.tp2_hits.get(et, 0) + 1

    def print_summary(self, symbol: str = "", start: str = "", end: str = ""):
        self.calculate_metrics()

        print("\n" + "=" * 75)
        print(f"  BACKTEST V7.4: {symbol}  ({start} -> {end})")
        print("=" * 75)
        print(f"  Total Trades:    {self.total_trades}")
        print(f"  Winning:         {self.winning_trades} ({self.win_rate:.1f}%)")
        print(f"  Losing:          {self.losing_trades}")
        print("-" * 75)
        print(f"  Total PNL:       ${self.total_pnl:+,.2f}  (after fees)")
        print(f"  Total Fees:      ${self.total_fees:,.2f}")
        print(f"  Avg Win:         ${self.avg_win:+,.2f}")
        print(f"  Avg Loss:        ${self.avg_loss:+,.2f}")
        print(f"  Max Drawdown:    ${self.max_drawdown:,.2f}")
        print(f"  Profit Factor:   {self.profit_factor:.2f}")
        print(f"  Risk/Reward:     {self.risk_reward:.2f}")
        print("=" * 75)

        # Entry Type Breakdown
        entry_types: Dict[str, List] = {}
        for t in self.trades:
            et = t.get('entry_type', 'STANDARD_M15')
            entry_types.setdefault(et, []).append(t)

        if entry_types:
            print("\n  Entry Type Breakdown:")
            print(f"    {'Type':20s} {'Trades':>6s} {'WR':>6s} {'PNL':>12s} {'Fees':>10s} {'Avg PNL':>10s} {'TP1':>5s} {'TP2':>5s}")
            print("    " + "-" * 86)
            for et, et_trades in sorted(entry_types.items()):
                et_wins = len([t for t in et_trades if t['pnl'] > 0])
                et_pnl = sum(t['pnl'] for t in et_trades)
                et_fees = sum(t.get('fees', 0) for t in et_trades)
                et_wr = (et_wins / len(et_trades) * 100) if et_trades else 0
                et_avg = et_pnl / len(et_trades) if et_trades else 0
                tp1_count = self.tp1_hits.get(et, 0)
                tp2_count = self.tp2_hits.get(et, 0)
                print(f"    {et:20s} {len(et_trades):6d} {et_wr:5.1f}% ${et_pnl:+10.2f} ${et_fees:8.2f} ${et_avg:+8.2f} {tp1_count:5d} {tp2_count:5d}")

        # Close Type Breakdown
        close_types: Dict[str, List] = {}
        for t in self.trades:
            ct = t.get('close_type', 'UNKNOWN')
            close_types.setdefault(ct, []).append(t['pnl'])

        if close_types:
            print("\n  Close Type Breakdown:")
            print(f"    {'Type':25s} {'Trades':>7s} {'Avg PNL':>12s} {'Total':>14s}")
            print("    " + "-" * 62)
            for ct, pnls in sorted(close_types.items()):
                avg_pnl = sum(pnls) / len(pnls)
                total = sum(pnls)
                print(f"    {ct:25s} {len(pnls):7d} ${avg_pnl:+10.2f} ${total:+12,.2f}")

        print("=" * 75)

    def print_trades(self, max_trades: int = 300):
        if not self.trades:
            print("No trades.")
            return

        display = self.trades[:max_trades]
        print(f"\n  TRADE HISTORY ({len(self.trades)} trades, showing {len(display)}):")
        print("-" * 135)
        for i, t in enumerate(display, 1):
            emoji = "[W]" if t['pnl'] > 0 else "[L]"
            ct = t.get('close_type', '')
            et = t.get('entry_type', 'STD')
            pct = t.get('close_percent', 1.0)
            pct_str = f" {pct*100:.0f}%" if pct < 1.0 else ""
            sl_t = t.get('sl_type', '')
            sl_str = f" [{sl_t}]" if sl_t else ""
            fee = t.get('fees', 0)
            print(
                f"  {i:3d}. {emoji} {t['side']:4s} [{et:14s}] "
                f"${t['entry_price']:>10,.2f} -> ${t['close_price']:>10,.2f}  "
                f"[{ct}{pct_str}]{sl_str}  "
                f"PNL: ${t['pnl']:+8.2f} ({t['pnl_percent']:+6.1f}%) fee:${fee:.2f}"
            )
        print("-" * 135)


# ==============================================================================
# Futures Backtester V7.4 (Rebuilt)
# ==============================================================================

class FuturesBacktester:
    """Backtest futures strategy V7.4 — matches live bot logic exactly."""

    def __init__(
        self,
        symbols: List[str],
        initial_balance: float = 10000,
        enable_divergence: bool = True,
        config_overrides: dict | None = None,
    ):
        self.symbols = symbols
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.enable_divergence = enable_divergence
        self.client = BinanceFuturesClient()
        self._build_config(config_overrides)

    def _build_config(self, overrides: dict | None) -> None:
        """Build per-run config from module-level defaults + optional overrides.

        Deep-copies all config dicts so each backtest run is isolated.
        The module-level dicts already include data/config.json overrides
        from _apply_config_overrides() which runs at import time.
        """
        import copy
        self._cfg_leverage = copy.deepcopy(LEVERAGE)
        self._cfg_risk = copy.deepcopy(RISK_MANAGEMENT)
        self._cfg_indicators = copy.deepcopy(INDICATORS)
        self._cfg_entry = copy.deepcopy(ENTRY)
        self._cfg_standard_entry = copy.deepcopy(STANDARD_ENTRY)
        self._cfg_chandelier = copy.deepcopy(CHANDELIER_EXIT)
        self._cfg_smart_sl = copy.deepcopy(SMART_SL)
        self._cfg_ema610_entry = copy.deepcopy(EMA610_ENTRY)
        self._cfg_standard_exit = copy.deepcopy(STANDARD_EXIT)
        self._cfg_ema610_exit = copy.deepcopy(EMA610_EXIT)
        self._cfg_rsi_div_exit = copy.deepcopy(RSI_DIV_EXIT)
        self._cfg_fees = copy.deepcopy(FEES)
        self._cfg_divergence = copy.deepcopy(DIVERGENCE_CONFIG)

        if not overrides:
            return

        config_map = {
            'STANDARD_EXIT': self._cfg_standard_exit,
            'EMA610_EXIT': self._cfg_ema610_exit,
            'RSI_DIV_EXIT': self._cfg_rsi_div_exit,
            'RISK_MANAGEMENT': self._cfg_risk,
            'CHANDELIER_EXIT': self._cfg_chandelier,
            'SMART_SL': self._cfg_smart_sl,
            'EMA610_ENTRY': self._cfg_ema610_entry,
            'INDICATORS': self._cfg_indicators,
            'ENTRY': self._cfg_entry,
            'STANDARD_ENTRY': self._cfg_standard_entry,
            'FEES': self._cfg_fees,
        }

        for section, target in config_map.items():
            source = overrides.get(section)
            if not source or not isinstance(source, dict):
                continue
            for k, v in source.items():
                if k in target and isinstance(v, dict) and isinstance(target[k], dict):
                    target[k].update(v)
                elif k in target:
                    target[k] = v

        # LEVERAGE: full replacement
        lev = overrides.get('LEVERAGE')
        if lev and isinstance(lev, dict):
            self._cfg_leverage.clear()
            self._cfg_leverage.update(lev)

    def backtest(
        self,
        start_date: str,
        end_date: str = None,
    ) -> BacktestResult:
        if not end_date:
            end_date = datetime.now().strftime('%Y-%m-%d')

        since = datetime.strptime(start_date, '%Y-%m-%d')
        until = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)

        result = BacktestResult()

        for symbol in self.symbols:
            logger.info(f"\n{'='*50}")
            logger.info(f"Backtesting {symbol}: {start_date} -> {end_date}")
            logger.info(f"{'='*50}")

            try:
                trades, chart_dfs = self._backtest_symbol(symbol, since, until)
                result.trades.extend(trades)
                result.chart_data[symbol] = chart_dfs
            except Exception as e:
                logger.error(f"Error backtesting {symbol}: {e}", exc_info=True)

        result.calculate_metrics()
        return result

    def backtest_with_chart_data(
        self,
        start_date: str,
        end_date: str = None,
        chart_timeframe: str = 'auto',
    ) -> BacktestResult:
        """Run backtest and return result with chart-ready data (all 3 timeframes)."""
        result = self.backtest(start_date, end_date)

        if result.chart_data:
            symbol = self.symbols[0]
            dfs = result.chart_data.get(symbol, {})
            if dfs:
                result.chart_json = self._build_multi_tf_chart_json(
                    dfs, result.trades, start_date,
                    end_date or datetime.now().strftime('%Y-%m-%d'),
                    chart_timeframe,
                )

        return result

    def _build_tf_data(
        self,
        df: pd.DataFrame,
        tf_label: str,
        ema610_source: pd.DataFrame = None,
        adx_source: pd.DataFrame = None,
        max_candles: int = 5000,
    ) -> Dict:
        """Build candles + indicators for a single timeframe.

        Args:
            max_candles: Downsample to this many candles max (0 = no limit).
        """
        import math

        if df.empty:
            return {'timeframe': tf_label, 'candles': [], 'indicators': {}}

        # Downsample if too many rows (evenly spaced, always keep last row)
        if max_candles > 0 and len(df) > max_candles:
            step = len(df) / max_candles
            indices = [int(i * step) for i in range(max_candles)]
            if indices[-1] != len(df) - 1:
                indices[-1] = len(df) - 1
            df = df.iloc[indices]
            if ema610_source is not None and not ema610_source.empty:
                ema_step = len(ema610_source) / max_candles
                ema_indices = [int(i * ema_step) for i in range(min(max_candles, len(ema610_source)))]
                if ema_indices and ema_indices[-1] != len(ema610_source) - 1:
                    ema_indices[-1] = len(ema610_source) - 1
                ema610_source = ema610_source.iloc[ema_indices]

        def _ts(idx):
            return int(idx.timestamp())

        def _safe(val):
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return None
            return round(float(val), 4)

        # Use .timestamp() — works regardless of datetime64 resolution (ns/ms/us)
        timestamps = [int(t.timestamp()) for t in df.index]
        candles = [
            {'time': t, 'open': _safe(o), 'high': _safe(h), 'low': _safe(l), 'close': _safe(c)}
            for t, o, h, l, c in zip(
                timestamps,
                df['open'].values, df['high'].values, df['low'].values, df['close'].values
            )
        ]

        indicators = {}
        for col in ['ema34', 'ema89', 'ema610', 'chandelier_long', 'chandelier_short']:
            if col in df.columns:
                col_vals = df[col].values
                series = [
                    {'time': t, 'value': round(float(v), 4)}
                    for t, v in zip(timestamps, col_vals)
                    if v is not None and not (isinstance(v, float) and math.isnan(v))
                ]
                indicators[col] = series

        # Map EMA610 from higher TF when not in current TF (M15 gets it from H1)
        if 'ema610' not in indicators and ema610_source is not None:
            if not ema610_source.empty and 'ema610' in ema610_source.columns:
                ema_timestamps = [int(t.timestamp()) for t in ema610_source.index]
                ema_vals = ema610_source['ema610'].values
                ema610_mapped = [
                    {'time': t, 'value': round(float(v), 4)}
                    for t, v in zip(ema_timestamps, ema_vals)
                    if v is not None and not (isinstance(v, float) and math.isnan(v))
                ]
                indicators['ema610'] = ema610_mapped

        # ADX — extract from current df or map from H1 source
        if 'adx' in df.columns:
            adx_vals = df['adx'].values
            adx_data = [
                {'time': t, 'value': round(float(v), 2)}
                for t, v in zip(timestamps, adx_vals)
                if v is not None and not (isinstance(v, float) and math.isnan(v))
            ]
            if adx_data:
                indicators['adx'] = adx_data
        elif adx_source is not None and not adx_source.empty and 'adx' in adx_source.columns:
            adx_timestamps = [int(t.timestamp()) for t in adx_source.index]
            adx_vals = adx_source['adx'].values
            adx_data = [
                {'time': t, 'value': round(float(v), 2)}
                for t, v in zip(adx_timestamps, adx_vals)
                if v is not None and not (isinstance(v, float) and math.isnan(v))
            ]
            if adx_data:
                indicators['adx'] = adx_data

        rsi_series = TechnicalIndicators.calculate_rsi(df['close'], 14)
        rsi_timestamps = [int(t.timestamp()) for t in rsi_series.index]
        rsi_vals = rsi_series.values
        rsi_data = [
            {'time': t, 'value': round(float(v), 4)}
            for t, v in zip(rsi_timestamps, rsi_vals)
            if v is not None and not (isinstance(v, float) and math.isnan(v))
        ]
        indicators['rsi'] = rsi_data

        return {
            'timeframe': tf_label,
            'candles': candles,
            'indicators': indicators,
        }

    def _build_multi_tf_chart_json(
        self,
        dfs: Dict[str, pd.DataFrame],
        trades: List[Dict],
        start_date: str,
        end_date: str,
        default_timeframe: str = 'auto',
    ) -> Dict:
        """Build chart data for all 4 timeframes + shared trades list."""
        import math

        df_m5 = dfs.get('m5', pd.DataFrame())
        df_m15 = dfs.get('m15', pd.DataFrame())
        df_h1 = dfs.get('h1', pd.DataFrame())
        df_h4 = dfs.get('h4', pd.DataFrame())

        if df_m15.empty:
            return {}

        # Pick default TF
        since = datetime.strptime(start_date, '%Y-%m-%d')
        until = datetime.strptime(end_date, '%Y-%m-%d')
        days = (until - since).days

        if default_timeframe == 'auto':
            if days <= 3:
                default_tf = '5m'
            elif days <= 7:
                default_tf = '15m'
            elif days <= 60:
                default_tf = '1h'
            else:
                default_tf = '4h'
        else:
            default_tf = default_timeframe

        def _safe(val):
            if val is None or (isinstance(val, float) and math.isnan(val)):
                return None
            return round(float(val), 4)

        # Build chart data per timeframe
        # ADX is computed on H1 — pass as source for M5/M15
        timeframes = {
            '5m': self._build_tf_data(df_m5, '5m', ema610_source=df_h1, adx_source=df_h1) if not df_m5.empty else {},
            '15m': self._build_tf_data(df_m15, '15m', ema610_source=df_h1, adx_source=df_h1),
            '1h': self._build_tf_data(df_h1, '1h'),
            '4h': self._build_tf_data(df_h4, '4h'),
        }

        # Shared trades (entry + close markers)
        chart_trades = []
        for t in trades:
            open_time = t.get('open_time')
            if open_time is None:
                continue
            entry_ts = int(open_time.timestamp()) if hasattr(open_time, 'timestamp') else int(open_time)

            close_time = t.get('close_time')
            close_ts = None
            if close_time is not None:
                close_ts = int(close_time.timestamp()) if hasattr(close_time, 'timestamp') else int(close_time)

            chart_trades.append({
                'time': entry_ts,
                'close_time': close_ts,
                'side': t.get('side', ''),
                'entry_price': _safe(t.get('entry_price')),
                'close_price': _safe(t.get('close_price')),
                'entry_type': t.get('entry_type', 'STANDARD_M15'),
                'close_type': t.get('close_type', ''),
                'pnl': _safe(t.get('pnl')),
                'pnl_percent': _safe(t.get('pnl_percent')),
            })

        available_tfs = [tf for tf, d in timeframes.items() if d.get('candles')]

        return {
            'default_timeframe': default_tf,
            'available_timeframes': available_tfs,
            'timeframes': timeframes,
            'trades': chart_trades,
        }

    # ------------------------------------------------------------------
    # Fee Calculation
    # ------------------------------------------------------------------

    def _calc_fee(self, position_value: float, fee_type: str = 'maker') -> float:
        rate = self._cfg_fees.get(fee_type, self._cfg_fees['maker'])
        return position_value * rate

    # ------------------------------------------------------------------
    # Signal Detection — mirrors signal_detector.py
    # ------------------------------------------------------------------

    def _detect_h4_trend(self, df_h4: pd.DataFrame) -> Optional[str]:
        """H4 trend: EMA34 > EMA89 AND Price > EMA89 → BUY_TREND (closed candle)."""
        if len(df_h4) < 100:
            return None
        try:
            indicators = TechnicalIndicators.get_all_indicators(
                df_h4.tail(100),
                ema_fast=self._cfg_indicators['ema_fast'],
                ema_slow=self._cfg_indicators['ema_slow'],
                use_closed_candle=True,
            )
            ema34 = indicators.ema34
            ema89 = indicators.ema89
            price = indicators.current_price

            if ema34 > ema89 and price > ema89:
                return "BUY_TREND"
            elif ema34 < ema89 and price < ema89:
                return "SELL_TREND"
        except Exception:
            pass
        return None

    def _check_tf_trend(self, df_tf: pd.DataFrame, h4_trend: str) -> bool:
        """Check if TF trend aligns with H4 trend (closed candle)."""
        if len(df_tf) < 100:
            return False
        try:
            indicators = TechnicalIndicators.get_all_indicators(
                df_tf.tail(100),
                ema_fast=self._cfg_indicators['ema_fast'],
                ema_slow=self._cfg_indicators['ema_slow'],
                use_closed_candle=True,
            )
            ema34 = indicators.ema34
            ema89 = indicators.ema89
            price = indicators.current_price

            if h4_trend == "BUY_TREND":
                return ema34 > ema89 and price > ema89
            elif h4_trend == "SELL_TREND":
                return ema34 < ema89 and price < ema89
        except Exception:
            pass
        return False

    def _check_h1_rsi_filter(self, df_h1: pd.DataFrame, h4_trend: str) -> bool:
        """H1 RSI filter: BUY needs RSI < 70, SELL needs RSI > 30."""
        if len(df_h1) < 50:
            return False
        try:
            indicators = TechnicalIndicators.get_all_indicators(
                df_h1.tail(100),
                rsi_period=self._cfg_indicators['rsi_period'],
                use_closed_candle=True,
            )
            rsi = indicators.rsi
            if h4_trend == "BUY_TREND":
                return rsi < self._cfg_entry['rsi_overbought']
            elif h4_trend == "SELL_TREND":
                return rsi > self._cfg_entry['rsi_oversold']
        except Exception:
            pass
        return False

    def _check_divergence_filter(
        self, df_m15: pd.DataFrame, df_h1: pd.DataFrame, df_h4: pd.DataFrame, h4_trend: str,
    ) -> bool:
        """Check RSI divergence on M15 + H1 + H4. Returns True if NOT blocked."""
        if not self.enable_divergence or not self._cfg_divergence.get('enabled', True):
            return True

        signal_dir = "BUY" if h4_trend == "BUY_TREND" else "SELL"
        cfg = self._cfg_divergence

        try:
            m15_div = RSIDivergence.detect(
                df=df_m15.tail(cfg.get('m15_lookback', 80)),
                timeframe="M15",
                lookback=cfg.get('m15_lookback', 80),
                rsi_period=self._cfg_indicators['rsi_period'],
                swing_window=cfg['swing_window'],
                min_swing_distance=cfg['min_swing_distance'],
                max_swing_pairs=cfg['max_swing_pairs'],
                min_retracement_pct=cfg.get('min_retracement_pct', 1.5),
            )
            h1_div = RSIDivergence.detect(
                df=df_h1.tail(cfg['h1_lookback']),
                timeframe="H1",
                lookback=cfg['h1_lookback'],
                rsi_period=self._cfg_indicators['rsi_period'],
                swing_window=cfg['swing_window'],
                min_swing_distance=cfg['min_swing_distance'],
                max_swing_pairs=cfg['max_swing_pairs'],
                min_retracement_pct=cfg.get('min_retracement_pct', 1.5),
            )
            h4_div = RSIDivergence.detect(
                df=df_h4.tail(cfg['h4_lookback']),
                timeframe="H4",
                lookback=cfg['h4_lookback'],
                rsi_period=self._cfg_indicators['rsi_period'],
                swing_window=cfg['swing_window'],
                min_swing_distance=cfg['min_swing_distance'],
                max_swing_pairs=cfg['max_swing_pairs'],
                min_retracement_pct=cfg.get('min_retracement_pct', 1.5),
            )

            for d in [m15_div, h1_div, h4_div]:
                if d.has_divergence and d.blocks_direction == signal_dir:
                    return False
        except Exception:
            pass

        return True

    def _detect_wick_entry(
        self, df_tf: pd.DataFrame, h4_trend: str, entry_type: str = "standard_m15",
    ) -> Optional[Dict]:
        """
        Wick touch EMA34/89 + close correct side + rejection wick.
        Uses CLOSED candle (iloc[-2]).
        Returns {side, entry_price, wick_ratio} or None.
        """
        if len(df_tf) < 50:
            return None
        try:
            indicators = TechnicalIndicators.get_all_indicators(
                df_tf.tail(100),
                ema_fast=self._cfg_indicators['ema_fast'],
                ema_slow=self._cfg_indicators['ema_slow'],
                use_closed_candle=True,
            )
            ema34 = indicators.ema34
            ema89 = indicators.ema89
            price = indicators.current_price

            closed = df_tf.iloc[-2]
            o = float(closed['open'])
            h = float(closed['high'])
            l = float(closed['low'])
            c = float(closed['close'])

            candle_range = h - l
            if candle_range == 0:
                return None

            # Per-timeframe tolerance from STANDARD_ENTRY
            tf_key = entry_type.replace("standard_", "")  # "standard_m15" → "m15"
            tolerance = self._cfg_standard_entry.get(tf_key, {}).get('tolerance', 0.002)
            wick_threshold = self._cfg_indicators['wick_threshold']

            if h4_trend == "BUY_TREND":
                touches_ema34 = l <= ema34 * (1 + tolerance) and c > ema34
                touches_ema89 = l <= ema89 * (1 + tolerance) and c > ema89
                if not (touches_ema34 or touches_ema89):
                    return None
                lower_wick = min(o, c) - l
                wick_ratio = (lower_wick / candle_range) * 100
                if wick_ratio >= wick_threshold:
                    return {'side': 'BUY', 'entry_price': price, 'wick_ratio': wick_ratio}

            elif h4_trend == "SELL_TREND":
                touches_ema34 = h >= ema34 * (1 - tolerance) and c < ema34
                touches_ema89 = h >= ema89 * (1 - tolerance) and c < ema89
                if not (touches_ema34 or touches_ema89):
                    return None
                upper_wick = h - max(o, c)
                wick_ratio = (upper_wick / candle_range) * 100
                if wick_ratio >= wick_threshold:
                    return {'side': 'SELL', 'entry_price': price, 'wick_ratio': wick_ratio}

        except Exception as e:
            logger.debug(f"Wick entry detection error: {e}")

        return None

    # ------------------------------------------------------------------
    # EMA610 Entry Detection
    # ------------------------------------------------------------------

    def _check_ema610_touch(
        self,
        df_tf: pd.DataFrame,
        tf_label: str,
        h4_trend: str,
        current_m15_price: float,
    ) -> Optional[Dict]:
        """Check if candle touches EMA610 ±tolerance zone."""
        try:
            latest = df_tf.iloc[-1]
            ema610_val = float(latest.get('ema610', float('nan')))
            if pd.isna(ema610_val):
                return None

            candle_high = float(latest['high'])
            candle_low = float(latest['low'])
            tolerance = self._cfg_ema610_entry.get('tolerance', 0.002)

            side = h4_trend  # 'BUY' or 'SELL'

            ema_upper = ema610_val * (1 + tolerance)
            ema_lower = ema610_val * (1 - tolerance)
            touched = candle_low <= ema_upper and candle_high >= ema_lower

            if not touched:
                return None

            entry_price = ema610_val

            return {
                'side': side,
                'entry_price': entry_price,
                'ema610_val': ema610_val,
                'tf': tf_label,
            }

        except Exception as e:
            logger.debug(f"EMA610 touch check error [{tf_label}]: {e}")
            return None

    # ------------------------------------------------------------------
    # Fast Signal Detection (index-based, no DataFrame slicing)
    # ------------------------------------------------------------------

    def _detect_h4_trend_fast(self, df_h4: pd.DataFrame, h4_end_idx: int) -> Optional[str]:
        """H4 trend using pre-computed ema34/ema89 columns. ~100x faster."""
        if h4_end_idx < 100:
            return None
        closed_idx = h4_end_idx - 2
        if closed_idx < 0:
            return None
        ema34 = df_h4['ema34'].iat[closed_idx]
        ema89 = df_h4['ema89'].iat[closed_idx]
        price = df_h4['close'].iat[closed_idx]
        if pd.isna(ema34) or pd.isna(ema89):
            return None
        if ema34 > ema89 and price > ema89:
            return "BUY_TREND"
        elif ema34 < ema89 and price < ema89:
            return "SELL_TREND"
        return None

    def _check_tf_trend_fast(self, df_tf: pd.DataFrame, tf_end_idx: int, h4_trend: str) -> bool:
        """Check TF trend alignment using pre-computed columns."""
        if tf_end_idx < 100:
            return False
        closed_idx = tf_end_idx - 2
        if closed_idx < 0:
            return False
        ema34 = df_tf['ema34'].iat[closed_idx]
        ema89 = df_tf['ema89'].iat[closed_idx]
        price = df_tf['close'].iat[closed_idx]
        if pd.isna(ema34) or pd.isna(ema89):
            return False
        if h4_trend == "BUY_TREND":
            return ema34 > ema89 and price > ema89
        elif h4_trend == "SELL_TREND":
            return ema34 < ema89 and price < ema89
        return False

    def _check_h1_rsi_filter_fast(self, df_h1: pd.DataFrame, h1_end_idx: int, h4_trend: str) -> bool:
        """H1 RSI filter using pre-computed RSI column."""
        if h1_end_idx < 50:
            return False
        closed_idx = h1_end_idx - 2
        if closed_idx < 0:
            return False
        rsi = df_h1['rsi'].iat[closed_idx]
        if pd.isna(rsi):
            return False
        if h4_trend == "BUY_TREND":
            return rsi < self._cfg_entry['rsi_overbought']
        elif h4_trend == "SELL_TREND":
            return rsi > self._cfg_entry['rsi_oversold']
        return False

    def _detect_wick_entry_fast(self, df_tf: pd.DataFrame, tf_end_idx: int, h4_trend: str, entry_type: str = "standard_m15") -> Optional[Dict]:
        """Wick entry detection using pre-computed columns. No get_all_indicators call."""
        if tf_end_idx < 50:
            return None
        closed_idx = tf_end_idx - 2
        if closed_idx < 0:
            return None
        ema34 = df_tf['ema34'].iat[closed_idx]
        ema89 = df_tf['ema89'].iat[closed_idx]
        if pd.isna(ema34) or pd.isna(ema89):
            return None
        ema34 = float(ema34)
        ema89 = float(ema89)
        price = float(df_tf['close'].iat[closed_idx])
        o = float(df_tf['open'].iat[closed_idx])
        h = float(df_tf['high'].iat[closed_idx])
        l = float(df_tf['low'].iat[closed_idx])
        c = price
        candle_range = h - l
        if candle_range == 0:
            return None
        # Per-timeframe tolerance from STANDARD_ENTRY
        tf_key = entry_type.replace("standard_", "")
        tolerance = self._cfg_standard_entry.get(tf_key, {}).get('tolerance', 0.002)
        wick_threshold = self._cfg_indicators['wick_threshold']
        if h4_trend == "BUY_TREND":
            touches_ema34 = l <= ema34 * (1 + tolerance) and c > ema34
            touches_ema89 = l <= ema89 * (1 + tolerance) and c > ema89
            if not (touches_ema34 or touches_ema89):
                return None
            lower_wick = min(o, c) - l
            wick_ratio = (lower_wick / candle_range) * 100
            if wick_ratio >= wick_threshold:
                return {'side': 'BUY', 'entry_price': price, 'wick_ratio': wick_ratio}
        elif h4_trend == "SELL_TREND":
            touches_ema34 = h >= ema34 * (1 - tolerance) and c < ema34
            touches_ema89 = h >= ema89 * (1 - tolerance) and c < ema89
            if not (touches_ema34 or touches_ema89):
                return None
            upper_wick = h - max(o, c)
            wick_ratio = (upper_wick / candle_range) * 100
            if wick_ratio >= wick_threshold:
                return {'side': 'SELL', 'entry_price': price, 'wick_ratio': wick_ratio}
        return None

    def _detect_ema610_h4_trend_fast(self, df_h4: pd.DataFrame, h4_end_idx: int) -> Optional[str]:
        """EMA610 H4 trend: EMA34+89 vs EMA610 alignment (not EMA34/89 cross).
        Both EMA34 and EMA89 must be on same side of EMA610."""
        if h4_end_idx < 100:
            return None
        closed_idx = h4_end_idx - 2
        if closed_idx < 0:
            return None
        ema34 = df_h4['ema34'].iat[closed_idx]
        ema89 = df_h4['ema89'].iat[closed_idx]
        ema610 = df_h4['ema610'].iat[closed_idx]
        if pd.isna(ema34) or pd.isna(ema89) or pd.isna(ema610):
            return None
        ema34 = float(ema34)
        ema89 = float(ema89)
        ema610 = float(ema610)
        if ema34 > ema610 and ema89 > ema610:
            trend = "BUY"
        elif ema34 < ema610 and ema89 < ema610:
            trend = "SELL"
        else:
            return None  # Mixed — skip

        # Price-close override: H4 candle closed beyond EMA610 → flip trend
        h4_close = float(df_h4['close'].iat[closed_idx])
        if trend == "SELL" and h4_close > ema610:
            trend = "BUY"
        elif trend == "BUY" and h4_close < ema610:
            trend = "SELL"

        return trend

    def _check_ema610_h1_alignment_fast(
        self, df_h1: pd.DataFrame, h1_end_idx: int, ema610_side: str,
    ) -> bool:
        """H1 EMA alignment: H1 EMA34+89 vs H1 EMA610 must align with trend."""
        if h1_end_idx < 2:
            return False
        closed_idx = h1_end_idx - 2
        if closed_idx < 0:
            return False
        ema34 = df_h1['ema34'].iat[closed_idx]
        ema89 = df_h1['ema89'].iat[closed_idx]
        ema610 = df_h1['ema610'].iat[closed_idx]
        if pd.isna(ema34) or pd.isna(ema89) or pd.isna(ema610):
            return False
        ema34 = float(ema34)
        ema89 = float(ema89)
        ema610 = float(ema610)
        if ema610_side == "BUY":
            return ema34 > ema610 and ema89 > ema610
        elif ema610_side == "SELL":
            return ema34 < ema610 and ema89 < ema610
        return False

    def _check_ema610_distance(
        self, df_tf: pd.DataFrame, tf_end_idx: int, max_distance_pct: float,
    ) -> bool:
        """Check if current price is within max_distance_pct of EMA610."""
        if tf_end_idx < 1:
            return False
        idx = tf_end_idx - 1
        ema610 = df_tf['ema610'].iat[idx]
        close = df_tf['close'].iat[idx]
        if pd.isna(ema610) or pd.isna(close):
            return False
        dist = abs(float(close) - float(ema610)) / float(ema610)
        return dist <= max_distance_pct

    def _check_ema610_touch_fast(self, df_tf: pd.DataFrame, tf_end_idx: int, tf_label: str, h4_trend: str) -> Optional[Dict]:
        """EMA610 touch check using pre-computed column and integer index."""
        latest_idx = tf_end_idx - 1
        if latest_idx < 0:
            return None
        ema610_val = df_tf['ema610'].iat[latest_idx]
        if pd.isna(ema610_val):
            return None
        ema610_val = float(ema610_val)
        candle_high = float(df_tf['high'].iat[latest_idx])
        candle_low = float(df_tf['low'].iat[latest_idx])
        tolerance = self._cfg_ema610_entry.get('tolerance', 0.002)
        ema_upper = ema610_val * (1 + tolerance)
        ema_lower = ema610_val * (1 - tolerance)
        touched = candle_low <= ema_upper and candle_high >= ema_lower
        if not touched:
            return None
        return {'side': h4_trend, 'entry_price': ema610_val, 'ema610_val': ema610_val, 'tf': tf_label}

    # ------------------------------------------------------------------
    # Position Opening
    # ------------------------------------------------------------------

    def _open_standard_position(
        self,
        symbol: str,
        signal: Dict,
        entry_price: float,
        timestamp: pd.Timestamp,
        entry_type: str = 'STANDARD_M15',
    ) -> Optional[Dict]:
        """Open standard position with per-TF TP/SL from STANDARD_EXIT."""

        margin = float(self._cfg_risk['fixed_margin'])
        if self.balance < self._cfg_risk.get('min_balance_to_trade', 200):
            return None
        if self.balance < margin:
            margin = self.balance

        leverage = self._cfg_leverage.get(symbol, self._cfg_leverage['default'])
        position_value = margin * leverage
        size = position_value / entry_price
        side = signal['side']

        entry_fee = self._calc_fee(position_value, 'maker')

        # Per-TF TP/SL from STANDARD_EXIT config
        tf = entry_type.replace('STANDARD_', '').lower()  # "m15", "h1", "h4"
        exit_cfg = self._cfg_standard_exit.get(tf, self._cfg_standard_exit['m15'])

        tp1_roi_pct = exit_cfg['tp1_roi'] / 100 / leverage
        tp2_roi_pct = exit_cfg['tp2_roi'] / 100 / leverage
        hard_sl_pct = exit_cfg['hard_sl_roi'] / 100 / leverage
        tp1_close_pct = exit_cfg.get('tp1_percent', 70)

        if side == 'BUY':
            tp1_price = entry_price * (1 + tp1_roi_pct)
            tp2_price = entry_price * (1 + tp2_roi_pct)
            sl_price = entry_price * (1 - hard_sl_pct)
        else:
            tp1_price = entry_price * (1 - tp1_roi_pct)
            tp2_price = entry_price * (1 - tp2_roi_pct)
            sl_price = entry_price * (1 + hard_sl_pct)

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'size': size,
            'margin': margin,
            'leverage': leverage,
            'sl_price': sl_price,
            'chandelier_sl': None,
            'trailing_sl': None,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'tp1_close_pct': tp1_close_pct / 100,
            'tp1_hit': False,
            'remaining_size': size,
            'realized_pnl': 0.0,
            'open_time': timestamp,
            'status': 'OPEN',
            'entry_type': entry_type,
            'entry_fee': entry_fee,
            # CE grace tracking
            'ce_armed': False,
            'ce_price_validated': False,
            'candles_since_entry': 0,
        }

    def _open_ema610_position(
        self,
        symbol: str,
        signal: Dict,
        timestamp: pd.Timestamp,
        tf_label: str,
    ) -> Optional[Dict]:
        """Open EMA610 position with ROI-based TP/SL from EMA610_EXIT."""

        margin = float(self._cfg_risk['fixed_margin'])
        if tf_label.upper() == 'H4':
            ema610_mult = self._cfg_risk.get('ema610_h4_margin_multiplier', 1)
        else:
            ema610_mult = self._cfg_risk.get('ema610_margin_multiplier', 1)
        if ema610_mult > 1:
            margin = margin * ema610_mult

        if self.balance < self._cfg_risk.get('min_balance_to_trade', 200):
            return None
        if self.balance < margin:
            margin = self.balance

        leverage = self._cfg_leverage.get(symbol, self._cfg_leverage['default'])
        entry_price = signal['entry_price']
        position_value = margin * leverage
        size = position_value / entry_price
        side = signal['side']

        entry_fee = self._calc_fee(position_value, 'maker')

        cfg = self._cfg_ema610_exit[tf_label]

        tp1_roi_pct = cfg['tp1_roi'] / 100 / leverage
        tp2_roi_pct = cfg['tp2_roi'] / 100 / leverage

        if side == 'BUY':
            tp1_price = entry_price * (1 + tp1_roi_pct)
            tp2_price = entry_price * (1 + tp2_roi_pct)
        else:
            tp1_price = entry_price * (1 - tp1_roi_pct)
            tp2_price = entry_price * (1 - tp2_roi_pct)

        hard_sl_pct = cfg['hard_sl_roi'] / 100 / leverage
        if side == 'BUY':
            sl_price = entry_price * (1 - hard_sl_pct)
        else:
            sl_price = entry_price * (1 + hard_sl_pct)

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'size': size,
            'margin': margin,
            'leverage': leverage,
            'sl_price': sl_price,
            'chandelier_sl': None,
            'trailing_sl': None,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'tp1_close_pct': cfg['tp1_percent'] / 100,
            'tp1_hit': False,
            'remaining_size': size,
            'realized_pnl': 0.0,
            'open_time': timestamp,
            'status': 'OPEN',
            'entry_type': f'EMA610_{tf_label.upper()}',
            'ema610_tf': tf_label.upper(),
            'entry_fee': entry_fee,
            # CE grace tracking
            'ce_armed': False,
            'ce_price_validated': False,
            'candles_since_entry': 0,
        }

    def _open_rsi_div_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        timestamp: pd.Timestamp,
        tf_label: str,
    ) -> Optional[Dict]:
        """Open RSI divergence position with enhanced leverage and per-TF TP/SL."""

        margin = float(self._cfg_risk['fixed_margin'])
        if self.balance < self._cfg_risk.get('min_balance_to_trade', 200):
            return None
        if self.balance < margin:
            margin = self.balance

        base_leverage = self._cfg_leverage.get(symbol, self._cfg_leverage['default'])
        cfg = self._cfg_rsi_div_exit[tf_label]
        lev_mult = cfg.get('leverage_multiplier', 1.5)
        leverage = min(math.ceil(base_leverage * lev_mult), 125)

        position_value = margin * leverage
        size = position_value / entry_price

        entry_fee = self._calc_fee(position_value, 'maker')

        tp1_roi_pct = cfg['tp1_roi'] / 100 / leverage
        tp2_roi_pct = cfg['tp2_roi'] / 100 / leverage
        hard_sl_pct = cfg['hard_sl_roi'] / 100 / leverage

        if side == 'BUY':
            tp1_price = entry_price * (1 + tp1_roi_pct)
            tp2_price = entry_price * (1 + tp2_roi_pct)
            sl_price = entry_price * (1 - hard_sl_pct)
        else:
            tp1_price = entry_price * (1 - tp1_roi_pct)
            tp2_price = entry_price * (1 - tp2_roi_pct)
            sl_price = entry_price * (1 + hard_sl_pct)

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'size': size,
            'margin': margin,
            'leverage': leverage,
            'sl_price': sl_price,
            'chandelier_sl': None,
            'trailing_sl': None,
            'tp1_price': tp1_price,
            'tp2_price': tp2_price,
            'tp1_close_pct': cfg['tp1_percent'] / 100,
            'tp1_hit': False,
            'remaining_size': size,
            'realized_pnl': 0.0,
            'open_time': timestamp,
            'status': 'OPEN',
            'entry_type': f'RSI_DIV_{tf_label.upper()}',
            'entry_fee': entry_fee,
            # CE grace tracking
            'ce_armed': False,
            'ce_price_validated': False,
            'candles_since_entry': 0,
        }

    # ------------------------------------------------------------------
    # Exit Logic: Standard
    # ------------------------------------------------------------------

    def _update_chandelier_standard(
        self, pos: Dict,
        m15_ch_long: Optional[float],
        m15_ch_short: Optional[float],
        close_price: float,
        vol_current: float,
        vol_avg: Optional[float],
        ema200: Optional[float],
    ):
        """
        Update chandelier SL for standard position.
        BUY uses chandelier_long (HH-ATR), SELL uses chandelier_short (LL+ATR).
        Uses close price for trigger (not wick).
        Includes Smart SL breathing.
        """
        # Skip entirely if Chandelier Exit is disabled
        if not self._cfg_chandelier.get('enabled', True):
            return

        side = pos['side']

        # Update chandelier_sl — set to latest indicator value each candle
        # No ratchet: CE updates every candle to match TradingView behavior
        # BUY uses chandelier_long (HH-ATR), SELL uses chandelier_short (LL+ATR)
        if side == 'BUY' and m15_ch_long is not None:
            pos['chandelier_sl'] = m15_ch_long
        elif side == 'SELL' and m15_ch_short is not None:
            pos['chandelier_sl'] = m15_ch_short

        ch_sl = pos['chandelier_sl']
        if ch_sl is None:
            return

        # Wrong-side check: don't set trailing_sl if CE is on wrong side of price
        if side == 'BUY' and ch_sl > close_price:
            return
        elif side == 'SELL' and ch_sl < close_price:
            return

        # Check if chandelier would trigger (close price trigger)
        ch_triggered = False
        if side == 'BUY' and close_price <= ch_sl:
            ch_triggered = True
        elif side == 'SELL' and close_price >= ch_sl:
            ch_triggered = True

        if ch_triggered and self._cfg_smart_sl.get('enabled', True):
            if vol_current is not None and vol_avg is not None and vol_avg > 0:
                vol_threshold = self._cfg_smart_sl.get('volume_threshold_pct', 80) / 100
                if vol_current <= vol_avg * vol_threshold:
                    # Low volume → breathing, but check EMA200 safety
                    if ema200 is not None and self._cfg_smart_sl.get('hard_sl_on_ema_break', True):
                        if side == 'BUY' and close_price < ema200:
                            pos['trailing_sl'] = ch_sl
                            return
                        elif side == 'SELL' and close_price > ema200:
                            pos['trailing_sl'] = ch_sl
                            return
                    # Volume low + EMA200 safe → breathing, don't update trailing
                    return

        # Normal: set trailing SL to chandelier level
        pos['trailing_sl'] = ch_sl

    def _check_exits_standard(
        self,
        pos: Dict,
        close_price: float,
        high_price: float,
        low_price: float,
        m15_ch_long: Optional[float],
        m15_ch_short: Optional[float],
        ema200: Optional[float],
        vol_current: float,
        vol_avg: Optional[float],
    ) -> List[Dict]:
        """Standard exit: Hard SL → CE trailing (close-price) → TP1 → TP2."""
        trades = []
        side = pos['side']

        # 1. Hard SL (intra-candle wick trigger, taker fee)
        hard_sl = pos['sl_price']
        if (side == 'BUY' and low_price <= hard_sl) or (side == 'SELL' and high_price >= hard_sl):
            remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
            trade = self._close_trade(pos, hard_sl, remaining_ratio, 'HARD_SL', fee_type='taker')
            trade['sl_type'] = 'HARD_SL'
            trades.append(trade)
            pos['status'] = 'CLOSED'
            return trades

        # Update chandelier + smart SL
        self._update_chandelier_standard(pos, m15_ch_long, m15_ch_short, close_price, vol_current, vol_avg, ema200)

        # 2. CE trailing SL (close-price trigger, only if armed + validated)
        effective_trail = pos.get('trailing_sl')
        if effective_trail and pos.get('ce_armed', False):
            # Price validation: price must first be on safe side
            if not pos.get('ce_price_validated', False):
                price_safe = False
                if side == 'BUY' and close_price > effective_trail:
                    price_safe = True
                elif side == 'SELL' and close_price < effective_trail:
                    price_safe = True
                if price_safe:
                    pos['ce_price_validated'] = True

            if pos.get('ce_price_validated', False):
                trail_hit = False
                if side == 'BUY' and close_price <= effective_trail:
                    trail_hit = True
                elif side == 'SELL' and close_price >= effective_trail:
                    trail_hit = True

                if trail_hit:
                    remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
                    sl_type = 'CHANDELIER_SL'
                    # Determine if it was a breathing-turned-SL
                    if ema200 is not None:
                        if side == 'BUY' and close_price < ema200:
                            sl_type = 'EMA200_BREAK_SL'
                        elif side == 'SELL' and close_price > ema200:
                            sl_type = 'EMA200_BREAK_SL'

                    trade = self._close_trade(pos, effective_trail, remaining_ratio, 'CHANDELIER_SL', fee_type='maker')
                    trade['sl_type'] = sl_type
                    trades.append(trade)
                    pos['status'] = 'CLOSED'
                    return trades

        # 3. TP1 (intra-candle trigger)
        if not pos['tp1_hit']:
            tp1_hit = (side == 'BUY' and high_price >= pos['tp1_price']) or \
                      (side == 'SELL' and low_price <= pos['tp1_price'])
            if tp1_hit:
                if 'tp1_hit_tracked' not in pos:
                    pos['tp1_hit_tracked'] = True

                close_pct = pos.get('tp1_close_pct', 0.7)
                tf = pos['entry_type'].replace('STANDARD_', '').lower()
                tp1_roi = self._cfg_standard_exit.get(tf, {}).get('tp1_roi', 20)
                trade = self._close_trade(pos, pos['tp1_price'], close_pct, f'TP1_ROI_{tp1_roi}', fee_type='maker')
                trade['sl_type'] = ''
                trades.append(trade)
                pos['tp1_hit'] = True
                pos['remaining_size'] = pos['size'] * (1 - close_pct)
                pos['realized_pnl'] += trade['pnl']

                # TP1 closed 100% → no remaining size for TP2
                if close_pct >= 1.0:
                    pos['status'] = 'CLOSED'
                    return trades

        # 4. TP2 (intra-candle trigger)
        if pos['tp1_hit'] and pos.get('status') != 'CLOSED':
            tp2_hit = (side == 'BUY' and high_price >= pos['tp2_price']) or \
                      (side == 'SELL' and low_price <= pos['tp2_price'])
            if tp2_hit:
                if 'tp2_hit_tracked' not in pos:
                    pos['tp2_hit_tracked'] = True

                remaining_ratio = pos['remaining_size'] / pos['size']
                tf = pos['entry_type'].replace('STANDARD_', '').lower()
                tp2_roi = self._cfg_standard_exit.get(tf, {}).get('tp2_roi', 40)
                trade = self._close_trade(pos, pos['tp2_price'], remaining_ratio, f'TP2_ROI_{tp2_roi}', fee_type='maker')
                trade['sl_type'] = ''
                trades.append(trade)
                pos['status'] = 'CLOSED'

        return trades

    # ------------------------------------------------------------------
    # Exit Logic: EMA610 (with chandelier fallback chains)
    # ------------------------------------------------------------------

    def _update_chandelier_ema610(
        self, pos: Dict,
        primary_ch_long: Optional[float],
        primary_ch_short: Optional[float],
        close_price: float,
        fallback_ch_longs: List[Optional[float]],
        fallback_ch_shorts: List[Optional[float]],
    ):
        """
        Update chandelier SL for EMA610 position.
        BUY uses chandelier_long, SELL uses chandelier_short.
        Includes fallback chain when primary is on wrong side of entry.
        """
        # Skip entirely if Chandelier Exit is disabled
        if not self._cfg_chandelier.get('enabled', True):
            return

        side = pos['side']
        entry = pos['entry_price']

        # Update chandelier_sl — set to latest indicator value each candle
        # No ratchet: CE updates every candle to match TradingView behavior
        if side == 'BUY' and primary_ch_long is not None:
            pos['chandelier_sl'] = primary_ch_long
        elif side == 'SELL' and primary_ch_short is not None:
            pos['chandelier_sl'] = primary_ch_short

        ch_sl = pos['chandelier_sl']

        # Check if primary is on wrong side of entry
        wrong_side = True
        if ch_sl is not None:
            if side == 'SELL' and ch_sl > entry:
                wrong_side = False
            elif side == 'BUY' and ch_sl < entry:
                wrong_side = False

        if wrong_side:
            # Pick correct fallback list based on side
            fb_list = fallback_ch_shorts if side == 'SELL' else fallback_ch_longs
            if fb_list:
                for fb_val in fb_list:
                    if fb_val is None:
                        continue
                    correct_side = (
                        (side == 'SELL' and fb_val > entry) or
                        (side == 'BUY' and fb_val < entry)
                    )
                    if correct_side:
                        # Set fallback CE directly (no ratchet — match TV per candle)
                        pos['trailing_sl'] = fb_val
                        return
            # All fallbacks wrong side — skip trailing update
            return

        # Primary on correct side — check vs current price
        if ch_sl is not None:
            if close_price is not None:
                if side == 'BUY' and ch_sl > close_price:
                    return
                elif side == 'SELL' and ch_sl < close_price:
                    return
            # Set trailing_sl directly from CE (no ratchet — match TV per candle)
            pos['trailing_sl'] = ch_sl

    def _check_exits_ema610(
        self,
        pos: Dict,
        close_price: float,
        high_price: float,
        low_price: float,
        primary_ch_long: Optional[float],
        primary_ch_short: Optional[float],
        fallback_ch_longs: List[Optional[float]],
        fallback_ch_shorts: List[Optional[float]],
        tf_label: str,
    ) -> List[Dict]:
        """EMA610 exit: Hard SL → Chandelier (with fallback) → TP1 → TP2."""
        trades = []
        side = pos['side']
        cfg = self._cfg_ema610_exit[tf_label]

        # 1. Hard SL (intra-candle wick, taker)
        hard_sl = pos['sl_price']
        if (side == 'BUY' and low_price <= hard_sl) or (side == 'SELL' and high_price >= hard_sl):
            remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
            sl_label = f"HARD_SL_{cfg['hard_sl_roi']}PCT"
            trade = self._close_trade(pos, hard_sl, remaining_ratio, sl_label, fee_type='taker')
            trade['sl_type'] = sl_label
            trades.append(trade)
            pos['status'] = 'CLOSED'
            return trades

        # Update chandelier with fallback
        self._update_chandelier_ema610(
            pos, primary_ch_long, primary_ch_short, close_price,
            fallback_ch_longs, fallback_ch_shorts,
        )

        # 2. CE trailing SL (only if armed + validated)
        effective_trail = pos.get('trailing_sl')
        if effective_trail and pos.get('ce_armed', False):
            if not pos.get('ce_price_validated', False):
                price_safe = False
                if side == 'BUY' and close_price > effective_trail:
                    price_safe = True
                elif side == 'SELL' and close_price < effective_trail:
                    price_safe = True
                if price_safe:
                    pos['ce_price_validated'] = True

            if pos.get('ce_price_validated', False):
                trail_hit = False
                if side == 'BUY' and close_price <= effective_trail:
                    trail_hit = True
                elif side == 'SELL' and close_price >= effective_trail:
                    trail_hit = True

                if trail_hit:
                    remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
                    trade = self._close_trade(pos, effective_trail, remaining_ratio,
                                              f'CHANDELIER_{tf_label.upper()}', fee_type='maker')
                    trade['sl_type'] = f'CHANDELIER_{tf_label.upper()}'
                    trades.append(trade)
                    pos['status'] = 'CLOSED'
                    return trades

        # 3. TP1 (intra-candle trigger)
        if not pos['tp1_hit']:
            tp1_hit = (side == 'BUY' and high_price >= pos['tp1_price']) or \
                      (side == 'SELL' and low_price <= pos['tp1_price'])
            if tp1_hit:
                if 'tp1_hit_tracked' not in pos:
                    pos['tp1_hit_tracked'] = True

                close_pct = pos.get('tp1_close_pct', cfg['tp1_percent'] / 100)
                trade = self._close_trade(pos, pos['tp1_price'], close_pct,
                                          f'TP1_ROI_{cfg["tp1_roi"]}', fee_type='maker')
                trade['sl_type'] = ''
                trades.append(trade)
                pos['tp1_hit'] = True
                pos['remaining_size'] = pos['size'] * (1 - close_pct)
                pos['realized_pnl'] += trade['pnl']

                # TP1 closed 100% → no remaining size for TP2
                if close_pct >= 1.0:
                    pos['status'] = 'CLOSED'
                    return trades

        # 4. TP2 (intra-candle trigger)
        if pos['tp1_hit'] and pos.get('status') != 'CLOSED':
            tp2_hit = (side == 'BUY' and high_price >= pos['tp2_price']) or \
                      (side == 'SELL' and low_price <= pos['tp2_price'])
            if tp2_hit:
                if 'tp2_hit_tracked' not in pos:
                    pos['tp2_hit_tracked'] = True

                remaining_ratio = pos['remaining_size'] / pos['size']
                trade = self._close_trade(pos, pos['tp2_price'], remaining_ratio,
                                          f'TP2_ROI_{cfg["tp2_roi"]}', fee_type='maker')
                trade['sl_type'] = ''
                trades.append(trade)
                pos['status'] = 'CLOSED'

        return trades

    # ------------------------------------------------------------------
    # Exit Logic: RSI Divergence (CE without Smart SL, with fallback + EMA34 dynamic TP)
    # ------------------------------------------------------------------

    def _check_exits_rsi_div(
        self,
        pos: Dict,
        close_price: float,
        high_price: float,
        low_price: float,
        primary_ch_long: Optional[float],
        primary_ch_short: Optional[float],
        fallback_ch_longs: List[Optional[float]],
        fallback_ch_shorts: List[Optional[float]],
        tf_label: str,
        ema34_val: Optional[float] = None,
    ) -> List[Dict]:
        """RSI Div exit: Hard SL → Chandelier (with fallback) → TP1 → TP2.
        Includes EMA34 dynamic TP capping."""
        trades = []
        side = pos['side']
        cfg = self._cfg_rsi_div_exit[tf_label]

        # 1. Hard SL (intra-candle wick, taker)
        hard_sl = pos['sl_price']
        if (side == 'BUY' and low_price <= hard_sl) or (side == 'SELL' and high_price >= hard_sl):
            remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
            sl_label = f"HARD_SL_{cfg['hard_sl_roi']}PCT"
            trade = self._close_trade(pos, hard_sl, remaining_ratio, sl_label, fee_type='taker')
            trade['sl_type'] = sl_label
            trades.append(trade)
            pos['status'] = 'CLOSED'
            return trades

        # Update chandelier with fallback (reuse EMA610 chandelier logic — no Smart SL)
        self._update_chandelier_ema610(
            pos, primary_ch_long, primary_ch_short, close_price,
            fallback_ch_longs, fallback_ch_shorts,
        )

        # EMA34 dynamic TP: cap TP1 at EMA34 if closer than current TP1
        if ema34_val is not None and not pos['tp1_hit']:
            entry = pos['entry_price']
            min_tp_dist = entry * 0.003  # at least 0.3% from entry
            if side == 'BUY' and ema34_val > entry + min_tp_dist:
                if ema34_val < pos['tp1_price']:
                    pos['tp1_price'] = ema34_val
            elif side == 'SELL' and ema34_val < entry - min_tp_dist:
                if ema34_val > pos['tp1_price']:
                    pos['tp1_price'] = ema34_val

        # 2. CE trailing SL (only if armed + validated)
        effective_trail = pos.get('trailing_sl')
        if effective_trail and pos.get('ce_armed', False):
            if not pos.get('ce_price_validated', False):
                price_safe = False
                if side == 'BUY' and close_price > effective_trail:
                    price_safe = True
                elif side == 'SELL' and close_price < effective_trail:
                    price_safe = True
                if price_safe:
                    pos['ce_price_validated'] = True

            if pos.get('ce_price_validated', False):
                trail_hit = False
                if side == 'BUY' and close_price <= effective_trail:
                    trail_hit = True
                elif side == 'SELL' and close_price >= effective_trail:
                    trail_hit = True

                if trail_hit:
                    remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
                    trade = self._close_trade(pos, effective_trail, remaining_ratio,
                                              f'CHANDELIER_{tf_label.upper()}', fee_type='maker')
                    trade['sl_type'] = f'CHANDELIER_{tf_label.upper()}'
                    trades.append(trade)
                    pos['status'] = 'CLOSED'
                    return trades

        # 3. TP1 (intra-candle trigger)
        if not pos['tp1_hit']:
            tp1_hit = (side == 'BUY' and high_price >= pos['tp1_price']) or \
                      (side == 'SELL' and low_price <= pos['tp1_price'])
            if tp1_hit:
                if 'tp1_hit_tracked' not in pos:
                    pos['tp1_hit_tracked'] = True

                close_pct = pos.get('tp1_close_pct', cfg['tp1_percent'] / 100)
                trade = self._close_trade(pos, pos['tp1_price'], close_pct,
                                          f'TP1_ROI_{cfg["tp1_roi"]}', fee_type='maker')
                trade['sl_type'] = ''
                trades.append(trade)
                pos['tp1_hit'] = True
                pos['remaining_size'] = pos['size'] * (1 - close_pct)
                pos['realized_pnl'] += trade['pnl']

                if close_pct >= 1.0:
                    pos['status'] = 'CLOSED'
                    return trades

        # 4. TP2 (intra-candle trigger)
        if pos['tp1_hit'] and pos.get('status') != 'CLOSED':
            tp2_hit = (side == 'BUY' and high_price >= pos['tp2_price']) or \
                      (side == 'SELL' and low_price <= pos['tp2_price'])
            if tp2_hit:
                if 'tp2_hit_tracked' not in pos:
                    pos['tp2_hit_tracked'] = True

                remaining_ratio = pos['remaining_size'] / pos['size']
                trade = self._close_trade(pos, pos['tp2_price'], remaining_ratio,
                                          f'TP2_ROI_{cfg["tp2_roi"]}', fee_type='maker')
                trade['sl_type'] = ''
                trades.append(trade)
                pos['status'] = 'CLOSED'

        return trades

    # ------------------------------------------------------------------
    # Trade Recording
    # ------------------------------------------------------------------

    def _close_trade(
        self, pos: Dict, fill_price: float, close_ratio: float,
        close_type: str, fee_type: str = 'maker',
    ) -> Dict:
        entry = pos['entry_price']
        lev = pos['leverage']
        margin = pos['margin']

        if pos['side'] == 'BUY':
            price_change_pct = ((fill_price - entry) / entry) * 100
        else:
            price_change_pct = ((entry - fill_price) / entry) * 100

        pnl_pct = price_change_pct * lev
        margin_portion = margin * close_ratio
        pnl_usd = margin_portion * (pnl_pct / 100)

        position_value = margin_portion * lev
        entry_fee = position_value * self._cfg_fees['maker']
        exit_fee = position_value * self._cfg_fees[fee_type]
        total_fees = entry_fee + exit_fee

        pnl_after_fees = pnl_usd - total_fees

        return {
            'symbol': pos['symbol'],
            'side': pos['side'],
            'entry_price': entry,
            'close_price': fill_price,
            'leverage': lev,
            'margin': margin_portion,
            'margin_returned': margin_portion,
            'pnl': pnl_after_fees,
            'pnl_before_fees': pnl_usd,
            'pnl_percent': pnl_pct,
            'fees': total_fees,
            'entry_fee': entry_fee,
            'exit_fee': exit_fee,
            'close_type': close_type,
            'close_percent': close_ratio,
            'open_time': pos.get('open_time'),
            'entry_type': pos.get('entry_type', 'STANDARD_M15'),
            'ema610_tf': pos.get('ema610_tf', ''),
            'sl_type': '',
            '_tp1_hit_tracked': pos.get('tp1_hit_tracked', False),
            '_tp2_hit_tracked': pos.get('tp2_hit_tracked', False),
        }

    def _force_close(self, pos: Dict, price: float, reason: str) -> Dict:
        remaining_ratio = pos['remaining_size'] / pos['size'] if pos['size'] > 0 else 1.0
        trade = self._close_trade(pos, price, remaining_ratio, reason, fee_type='taker')
        pos['status'] = 'CLOSED'
        return trade

    # ------------------------------------------------------------------
    # Margin Constraint Check
    # ------------------------------------------------------------------

    def _can_open_with_margin(
        self,
        new_margin: float,
        all_positions: List[Dict],
        current_price: float,
    ) -> bool:
        """Check if new position would violate 50% equity constraint."""
        active_margin = 0.0
        unrealized_pnl = 0.0

        for pos in all_positions:
            if pos.get('status') == 'CLOSED':
                continue
            active_margin += pos['margin']
            if pos['side'] == 'BUY':
                pnl_pct = (current_price - pos['entry_price']) / pos['entry_price']
            else:
                pnl_pct = (pos['entry_price'] - current_price) / pos['entry_price']
            unrealized_pnl += pnl_pct * pos['margin'] * pos['leverage']

        equity = self.balance + unrealized_pnl
        max_allowed = equity * (self._cfg_risk.get('max_equity_usage_pct', 50) / 100)

        return (active_margin + new_margin) <= max_allowed

    # ------------------------------------------------------------------
    # Main Backtest Loop
    # ------------------------------------------------------------------

    def _backtest_symbol(
        self,
        symbol: str,
        since: datetime,
        until: datetime,
    ) -> Tuple[List[Dict], Dict]:
        """Backtest a single symbol — mirrors live bot logic exactly."""

        # -- Fetch data --
        warmup = timedelta(days=120)
        fetch_since = since - warmup

        logger.info(f"Fetching data for {symbol} (warmup from {fetch_since.date()})...")

        df_m5 = load_cached_ohlcv(self.client, symbol, '5m', fetch_since, until)
        df_m15 = load_cached_ohlcv(self.client, symbol, '15m', fetch_since, until)
        df_h1 = load_cached_ohlcv(self.client, symbol, '1h', fetch_since, until)
        df_h4 = load_cached_ohlcv(self.client, symbol, '4h', fetch_since, until)

        if df_m5.empty or df_m15.empty or df_h1.empty or df_h4.empty:
            logger.error(f"Insufficient data for {symbol}")
            return [], {}

        logger.info(f"Data loaded: M5={len(df_m5)}, M15={len(df_m15)}, H1={len(df_h1)}, H4={len(df_h4)} candles")

        # -- Pre-compute indicators --
        ch_period = self._cfg_chandelier.get('period', 34)
        ch_mult = self._cfg_chandelier.get('multiplier', 1.75)

        # M5
        df_m5['ema34'] = TechnicalIndicators.calculate_ema(df_m5['close'], 34)
        df_m5['ema89'] = TechnicalIndicators.calculate_ema(df_m5['close'], 89)
        ch_long_m5, ch_short_m5 = ATRIndicator.chandelier_exit(df_m5, ch_period, ch_mult)
        df_m5['chandelier_long'] = ch_long_m5
        df_m5['chandelier_short'] = ch_short_m5

        # M15
        df_m15['atr'] = ATRIndicator.calculate_atr(df_m15, 14)
        ch_long_m15, ch_short_m15 = ATRIndicator.chandelier_exit(df_m15, ch_period, ch_mult)
        df_m15['chandelier_long'] = ch_long_m15
        df_m15['chandelier_short'] = ch_short_m15
        df_m15['ema200'] = TechnicalIndicators.calculate_ema(df_m15['close'], 200)
        df_m15['vol_avg21'] = df_m15['volume'].rolling(window=21).mean()
        df_m15['ema34'] = TechnicalIndicators.calculate_ema(df_m15['close'], 34)
        df_m15['ema89'] = TechnicalIndicators.calculate_ema(df_m15['close'], 89)

        # H1
        if len(df_h1) >= 610:
            df_h1['ema610'] = TechnicalIndicators.calculate_ema(df_h1['close'], 610)
        else:
            df_h1['ema610'] = float('nan')
        ch_long_h1, ch_short_h1 = ATRIndicator.chandelier_exit(df_h1, ch_period, ch_mult)
        df_h1['chandelier_long'] = ch_long_h1
        df_h1['chandelier_short'] = ch_short_h1
        df_h1['ema34'] = TechnicalIndicators.calculate_ema(df_h1['close'], 34)
        df_h1['ema89'] = TechnicalIndicators.calculate_ema(df_h1['close'], 89)

        # H4
        if len(df_h4) >= 610:
            df_h4['ema610'] = TechnicalIndicators.calculate_ema(df_h4['close'], 610)
        else:
            df_h4['ema610'] = float('nan')
        ch_long_h4, ch_short_h4 = ATRIndicator.chandelier_exit(df_h4, ch_period, ch_mult)
        df_h4['chandelier_long'] = ch_long_h4
        df_h4['chandelier_short'] = ch_short_h4
        df_h4['ema34'] = TechnicalIndicators.calculate_ema(df_h4['close'], 34)
        df_h4['ema89'] = TechnicalIndicators.calculate_ema(df_h4['close'], 89)

        # Pre-compute RSI for M15/H1/H4 (used by rsi_filter + divergence + RSI div entries)
        rsi_period = self._cfg_indicators['rsi_period']
        df_m15['rsi'] = TechnicalIndicators.calculate_rsi(df_m15['close'], rsi_period)
        df_h1['rsi'] = TechnicalIndicators.calculate_rsi(df_h1['close'], rsi_period)
        df_h4['rsi'] = TechnicalIndicators.calculate_rsi(df_h4['close'], rsi_period)

        # Pre-compute ADX for H1 (used by ADX filter — blocks entries in sideways market)
        adx_period = self._cfg_entry.get('adx_period', 14)
        df_h1['adx'] = ADXIndicator.calculate_adx(df_h1, adx_period)

        # -- Simulation state --
        trades: List[Dict] = []

        # All positions in flat list (matching per-entry-type limits)
        all_positions: List[Dict] = []

        # Per-TF dedup tracking (matching signal_detector.py)
        signaled_m5: set = set()
        signaled_m15: set = set()
        signaled_h1: set = set()
        signaled_h4: set = set()
        last_scanned_m5: Optional[str] = None
        last_scanned_m15: Optional[str] = None
        last_scanned_h1: Optional[str] = None
        last_scanned_h4: Optional[str] = None
        sl_cooldown_m5: Dict[str, str] = {}
        sl_cooldown_m15: Dict[str, str] = {}
        sl_cooldown_h1: Dict[str, str] = {}
        sl_cooldown_h4: Dict[str, str] = {}

        # EMA610 dedup
        last_ema610_h1_entry_candle_ts: Optional[pd.Timestamp] = None
        last_ema610_h4_entry_candle_ts: Optional[pd.Timestamp] = None

        # RSI Div entry dedup: {tf: last_candle_ts} — one per coin per TF
        rsi_div_last_candle: Dict[str, Optional[str]] = {'m15': None, 'h1': None, 'h4': None}

        # M15 EMA blocking: blocked_direction or None (blocks EMA entries until RSI resets to 50)
        m15_ema_block: Optional[str] = None  # "BUY" or "SELL"

        # H1/H4 candle boundary tracking
        prev_h1_candle_ts: Optional[pd.Timestamp] = None
        prev_h4_candle_ts: Optional[pd.Timestamp] = None

        def _safe_float_iat(series, idx):
            val = series.iat[idx]
            return float(val) if not pd.isna(val) else None

        def _active_of_type(entry_type: str) -> List[Dict]:
            return [p for p in all_positions if p.get('status') != 'CLOSED' and p.get('entry_type') == entry_type]

        def _all_active() -> List[Dict]:
            return [p for p in all_positions if p.get('status') != 'CLOSED']

        m15_in_range = df_m15[df_m15.index >= since]

        # Pre-compute offset so m15_global_idx = m15_offset + idx (O(1) instead of get_loc O(n))
        m15_offset = df_m15.index.get_loc(m15_in_range.index[0])

        m5_timestamps = df_m5.index
        h1_timestamps = df_h1.index
        h4_timestamps = df_h4.index

        # Pre-extract numpy arrays for fast scalar access in hot loop
        m15_ch_long_arr = df_m15['chandelier_long'].values
        m15_ch_short_arr = df_m15['chandelier_short'].values
        m15_ema200_arr = df_m15['ema200'].values
        m15_vol_avg21_arr = df_m15['vol_avg21'].values

        for idx in range(len(m15_in_range)):
            current_ts = m15_in_range.index[idx]
            current_candle = m15_in_range.iloc[idx]
            close_price = float(current_candle['close'])
            high_price = float(current_candle['high'])
            low_price = float(current_candle['low'])

            m15_global_idx = m15_offset + idx

            # M15 indicators (numpy array access - fastest possible)
            _v = m15_ch_long_arr[m15_global_idx]
            m15_ch_long = float(_v) if not np.isnan(_v) else None
            _v = m15_ch_short_arr[m15_global_idx]
            m15_ch_short = float(_v) if not np.isnan(_v) else None
            _v = m15_ema200_arr[m15_global_idx]
            m15_ema200 = float(_v) if not np.isnan(_v) else None
            m15_vol = float(current_candle['volume'])
            _v = m15_vol_avg21_arr[m15_global_idx]
            m15_vol_avg = float(_v) if not np.isnan(_v) else None

            # H1/H4 end indices via searchsorted (O(log n))
            h1_end = h1_timestamps.searchsorted(current_ts, side='right')
            h4_end = h4_timestamps.searchsorted(current_ts, side='right')

            # Index-based timestamp access (no DataFrame slicing)
            current_h1_ts = df_h1.index[h1_end - 1] if h1_end > 0 else None
            current_h4_ts = df_h4.index[h4_end - 1] if h4_end > 0 else None

            h1_candle_closed = (
                current_h1_ts is not None
                and prev_h1_candle_ts is not None
                and current_h1_ts != prev_h1_candle_ts
            )
            h4_candle_closed = (
                current_h4_ts is not None
                and prev_h4_candle_ts is not None
                and current_h4_ts != prev_h4_candle_ts
            )

            # Get closed H1/H4 candle data using integer indices (no get_loc)
            def _get_closed_tf_data_fast(df_tf, tf_end_idx, candle_closed):
                if not candle_closed or tf_end_idx < 2:
                    return None, None, None, None, None
                ci = tf_end_idx - 2
                return (
                    float(df_tf['close'].iat[ci]),
                    float(df_tf['high'].iat[ci]),
                    float(df_tf['low'].iat[ci]),
                    _safe_float_iat(df_tf['chandelier_long'], ci),
                    _safe_float_iat(df_tf['chandelier_short'], ci),
                )

            h1_close, h1_high, h1_low, h1_ch_long, h1_ch_short = _get_closed_tf_data_fast(df_h1, h1_end, h1_candle_closed)
            h4_close, h4_high, h4_low, h4_ch_long, h4_ch_short = _get_closed_tf_data_fast(df_h4, h4_end, h4_candle_closed)

            # ===================================================
            # INCREMENT CE GRACE COUNTER + PROCESS EXITS
            # ===================================================

            for pos in all_positions:
                if pos.get('status') == 'CLOSED':
                    continue
                pos['candles_since_entry'] = pos.get('candles_since_entry', 0) + 1
                grace = CE_GRACE_CANDLES.get(pos['entry_type'], 1)
                if not pos.get('ce_armed', False) and pos['candles_since_entry'] >= grace:
                    # Only arm CE if Chandelier Exit is enabled
                    if self._cfg_chandelier.get('enabled', True):
                        pos['ce_armed'] = True

            # -- Standard exits (per-TF CE routing) --
            for pos in all_positions:
                if pos.get('status') == 'CLOSED':
                    continue
                if not pos['entry_type'].startswith('STANDARD_'):
                    continue

                # Route CE to correct timeframe (matches live bot)
                et = pos['entry_type']
                if et == 'STANDARD_H4' and h4_candle_closed and h4_close is not None:
                    # H4 entry → H4 CE primary, fallback H1→M15
                    cur_h1_ch_l = _safe_float_iat(df_h1['chandelier_long'], h1_end - 1) if h1_end > 0 else None
                    cur_h1_ch_s = _safe_float_iat(df_h1['chandelier_short'], h1_end - 1) if h1_end > 0 else None
                    self._update_chandelier_ema610(
                        pos, h4_ch_long, h4_ch_short, h4_close,
                        [cur_h1_ch_l, m15_ch_long], [cur_h1_ch_s, m15_ch_short],
                    )
                    exit_trades = self._check_exits_standard(
                        pos, h4_close, h4_high, h4_low,
                        h4_ch_long, h4_ch_short, m15_ema200, m15_vol, m15_vol_avg,
                    )
                elif et == 'STANDARD_H1' and h1_candle_closed and h1_close is not None:
                    # H1 entry → H1 CE primary, fallback M15
                    self._update_chandelier_ema610(
                        pos, h1_ch_long, h1_ch_short, h1_close,
                        [m15_ch_long], [m15_ch_short],
                    )
                    exit_trades = self._check_exits_standard(
                        pos, h1_close, h1_high, h1_low,
                        h1_ch_long, h1_ch_short, m15_ema200, m15_vol, m15_vol_avg,
                    )
                else:
                    # M5/M15 → M15 CE (default behavior)
                    exit_trades = self._check_exits_standard(
                        pos, close_price, high_price, low_price,
                        m15_ch_long, m15_ch_short, m15_ema200, m15_vol, m15_vol_avg,
                    )

                for et_trade in exit_trades:
                    et_trade['close_time'] = current_ts
                    trades.append(et_trade)
                    self.balance += et_trade['margin_returned'] + et_trade['pnl']

            # -- EMA610 H1 exits (on H1 candle close) --
            if h1_candle_closed and h1_close is not None:
                for pos in all_positions:
                    if pos.get('status') == 'CLOSED':
                        continue
                    if pos.get('entry_type') != 'EMA610_H1':
                        continue

                    # Fallback chain: H1 → [M15]
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

            # -- EMA610 H4 exits (on H4 candle close) --
            if h4_candle_closed and h4_close is not None:
                for pos in all_positions:
                    if pos.get('status') == 'CLOSED':
                        continue
                    if pos.get('entry_type') != 'EMA610_H4':
                        continue

                    # Fallback chain: H4 → [H1, M15]
                    cur_h1_ch_long = None
                    cur_h1_ch_short = None
                    if h1_end > 0:
                        cur_h1_idx = h1_end - 1
                        cur_h1_ch_long = _safe_float_iat(df_h1['chandelier_long'], cur_h1_idx)
                        cur_h1_ch_short = _safe_float_iat(df_h1['chandelier_short'], cur_h1_idx)
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

            # -- RSI Div exits (per-TF CE routing, EMA34 dynamic TP) --
            for pos in all_positions:
                if pos.get('status') == 'CLOSED':
                    continue
                if not pos['entry_type'].startswith('RSI_DIV_'):
                    continue

                rsi_div_tf = pos['entry_type'].replace('RSI_DIV_', '').lower()

                if rsi_div_tf == 'h4' and h4_candle_closed and h4_close is not None:
                    cur_h1_cl = _safe_float_iat(df_h1['chandelier_long'], h1_end - 1) if h1_end > 0 else None
                    cur_h1_cs = _safe_float_iat(df_h1['chandelier_short'], h1_end - 1) if h1_end > 0 else None
                    h4_ema34 = _safe_float_iat(df_h4['ema34'], h4_end - 2) if h4_end >= 2 else None
                    exit_trades = self._check_exits_rsi_div(
                        pos, h4_close, h4_high, h4_low,
                        h4_ch_long, h4_ch_short,
                        [cur_h1_cl, m15_ch_long], [cur_h1_cs, m15_ch_short],
                        'h4', ema34_val=h4_ema34,
                    )
                elif rsi_div_tf == 'h1' and h1_candle_closed and h1_close is not None:
                    h1_ema34 = _safe_float_iat(df_h1['ema34'], h1_end - 2) if h1_end >= 2 else None
                    exit_trades = self._check_exits_rsi_div(
                        pos, h1_close, h1_high, h1_low,
                        h1_ch_long, h1_ch_short,
                        [m15_ch_long], [m15_ch_short],
                        'h1', ema34_val=h1_ema34,
                    )
                else:
                    # M15 — uses M15 CE, no fallback
                    m15_ema34 = _safe_float_iat(df_m15['ema34'], m15_global_idx)
                    exit_trades = self._check_exits_rsi_div(
                        pos, close_price, high_price, low_price,
                        m15_ch_long, m15_ch_short, [], [],
                        'm15', ema34_val=m15_ema34,
                    )

                for et_trade in exit_trades:
                    et_trade['close_time'] = current_ts
                    trades.append(et_trade)
                    self.balance += et_trade['margin_returned'] + et_trade['pnl']

            # ===================================================
            # SIGNAL DETECTION & ENTRY
            # ===================================================

            if idx < 1:
                prev_h1_candle_ts = current_h1_ts
                prev_h4_candle_ts = current_h4_ts
                continue

            # Use integer counts instead of DataFrame slicing for length checks
            m15_end = m15_global_idx + 1  # number of M15 candles up to current_ts
            if h4_end < 100 or h1_end < 100 or m15_end < 100:
                prev_h1_candle_ts = current_h1_ts
                prev_h4_candle_ts = current_h4_ts
                continue

            # Step 1: H4 trend (required for ALL entries) — fast index-based
            h4_trend = self._detect_h4_trend_fast(df_h4, h4_end)

            # Step 1.5: ADX H1 filter — blocks ALL entries when market is sideways
            if h4_trend:
                adx_threshold = self._cfg_entry.get('adx_threshold', 0)
                if adx_threshold > 0 and h1_end >= 2:
                    adx_val = df_h1['adx'].iat[h1_end - 2]  # closed H1 candle
                    if pd.isna(adx_val) or adx_val < adx_threshold:
                        h4_trend = None  # Block all entries

            # ── M15 EMA blocking: update RSI check + apply block ──
            if m15_ema_block is not None and m15_global_idx >= 15:
                m15_rsi = df_m15['rsi'].iat[m15_global_idx]
                if not pd.isna(m15_rsi):
                    # Bearish block (blocks BUY): clear when RSI ≤ 50
                    # Bullish block (blocks SELL): clear when RSI ≥ 50
                    if m15_ema_block == "BUY" and m15_rsi <= 50:
                        m15_ema_block = None
                    elif m15_ema_block == "SELL" and m15_rsi >= 50:
                        m15_ema_block = None

            if h4_trend:
                # Convert BUY_TREND → BUY for EMA610 side
                ema610_side = 'BUY' if h4_trend == 'BUY_TREND' else 'SELL'

                # M15 EMA block: skip ALL standard/ema610 entries in blocked direction
                ema_blocked = (m15_ema_block == ema610_side)

                # ── H4 Standard Entry (on H4 candle close) ──
                if self._cfg_standard_entry['h4']['enabled'] and h4_candle_closed and h4_end >= 2 and not ema_blocked:
                    closed_h4_candle_ts_str = str(df_h4.index[h4_end - 2])
                    dedup_key = f"{symbol}:{closed_h4_candle_ts_str}"

                    if dedup_key not in signaled_h4:
                        new_scanned = closed_h4_candle_ts_str
                        if last_scanned_h4 is not None and new_scanned != last_scanned_h4:
                            signal = self._detect_wick_entry_fast(df_h4, h4_end, h4_trend, "standard_h4")
                            if signal:
                                active_h4_std = _active_of_type('STANDARD_H4')
                                if len(active_h4_std) == 0:
                                    margin = float(self._cfg_risk['fixed_margin'])
                                    if self._can_open_with_margin(margin, _all_active(), close_price):
                                        entry_price = float(df_h4['close'].iat[h4_end - 2])
                                        pos = self._open_standard_position(
                                            symbol, signal, entry_price, current_ts,
                                            entry_type='STANDARD_H4',
                                        )
                                        if pos:
                                            all_positions.append(pos)
                                            self.balance -= pos['margin']
                                            signaled_h4.add(dedup_key)
                                            logger.info(f"  OPEN STANDARD_H4 {pos['side']} {symbol} @ ${entry_price:,.2f}")
                        last_scanned_h4 = new_scanned

                # ── H1 trend check (required for H1 + M15 entries) — fast ──
                h1_trend_ok = self._check_tf_trend_fast(df_h1, h1_end, h4_trend)
                if h1_trend_ok:
                    rsi_ok = self._check_h1_rsi_filter_fast(df_h1, h1_end, h4_trend)
                    # Divergence still uses iloc slicing but only when rsi_ok (rare)
                    if rsi_ok:
                        div_ok = self._check_divergence_filter(
                            df_m15.iloc[:m15_global_idx + 1],
                            df_h1.iloc[:h1_end], df_h4.iloc[:h4_end], h4_trend,
                        )
                    else:
                        div_ok = False

                    # ── H1 Standard Entry (on H1 candle close) ──
                    if self._cfg_standard_entry['h1']['enabled'] and rsi_ok and div_ok and h1_candle_closed and h1_end >= 2 and not ema_blocked:
                        closed_h1_candle_ts_str = str(df_h1.index[h1_end - 2])
                        dedup_key = f"{symbol}:{closed_h1_candle_ts_str}"

                        if dedup_key not in signaled_h1:
                            new_scanned = closed_h1_candle_ts_str
                            if last_scanned_h1 is not None and new_scanned != last_scanned_h1:
                                signal = self._detect_wick_entry_fast(df_h1, h1_end, h4_trend, "standard_h1")
                                if signal:
                                    active_h1_std = _active_of_type('STANDARD_H1')
                                    if len(active_h1_std) == 0:
                                        margin = float(self._cfg_risk['fixed_margin'])
                                        if self._can_open_with_margin(margin, _all_active(), close_price):
                                            entry_price = float(df_h1['close'].iat[h1_end - 2])
                                            pos = self._open_standard_position(
                                                symbol, signal, entry_price, current_ts,
                                                entry_type='STANDARD_H1',
                                            )
                                            if pos:
                                                all_positions.append(pos)
                                                self.balance -= pos['margin']
                                                signaled_h1.add(dedup_key)
                                                logger.info(f"  OPEN STANDARD_H1 {pos['side']} {symbol} @ ${entry_price:,.2f}")
                            last_scanned_h1 = new_scanned

                    # ── M15 Standard Entry (needs M15 trend too) — fast ──
                    m15_trend_ok = self._check_tf_trend_fast(df_m15, m15_end, h4_trend)
                    if self._cfg_standard_entry['m15']['enabled'] and m15_trend_ok and rsi_ok and div_ok and not ema_blocked:
                        closed_m15_candle_ts_str = str(m15_in_range.index[idx - 1])
                        dedup_key = f"{symbol}:{closed_m15_candle_ts_str}"

                        if dedup_key not in signaled_m15:
                            new_scanned = closed_m15_candle_ts_str
                            if last_scanned_m15 is not None and new_scanned != last_scanned_m15:
                                # Check SL cooldown
                                sl_cd = sl_cooldown_m15.get(symbol)
                                if sl_cd and sl_cd == new_scanned:
                                    pass  # Cooldown active
                                else:
                                    if sl_cd and sl_cd != new_scanned:
                                        del sl_cooldown_m15[symbol]

                                    signal = self._detect_wick_entry_fast(df_m15, m15_end, h4_trend, "standard_m15")
                                    if signal:
                                        active_m15_std = _active_of_type('STANDARD_M15')
                                        if len(active_m15_std) == 0:
                                            margin = float(self._cfg_risk['fixed_margin'])
                                            if self._can_open_with_margin(margin, _all_active(), close_price):
                                                entry_price = float(df_m15['close'].iat[m15_global_idx - 1])
                                                pos = self._open_standard_position(
                                                    symbol, signal, entry_price, current_ts,
                                                    entry_type='STANDARD_M15',
                                                )
                                                if pos:
                                                    all_positions.append(pos)
                                                    self.balance -= pos['margin']
                                                    signaled_m15.add(dedup_key)
                                                    logger.info(f"  OPEN STANDARD_M15 {pos['side']} {symbol} @ ${entry_price:,.2f}")
                            last_scanned_m15 = new_scanned

                        # ── M5 Standard Entry (needs M15 trend + M5 wick) ──
                        if self._cfg_standard_entry['m5']['enabled'] and not ema_blocked:
                            # Sub-loop: check all M5 candles closed since last M15 iteration
                            m5_end_idx = int(m5_timestamps.searchsorted(current_ts, side='right'))
                            for m5_ci in range(max(50, m5_end_idx - 4), m5_end_idx - 1):
                                closed_m5_ts_str = str(df_m5.index[m5_ci])
                                m5_dedup_key = f"{symbol}:{closed_m5_ts_str}"
                                if m5_dedup_key in signaled_m5:
                                    continue
                                m5_new_scanned = closed_m5_ts_str
                                if last_scanned_m5 is not None and m5_new_scanned != last_scanned_m5:
                                    sl_cd_m5 = sl_cooldown_m5.get(symbol)
                                    if sl_cd_m5 and sl_cd_m5 == m5_new_scanned:
                                        pass  # Cooldown active
                                    else:
                                        if sl_cd_m5 and sl_cd_m5 != m5_new_scanned:
                                            del sl_cooldown_m5[symbol]

                                        m5_signal = self._detect_wick_entry_fast(df_m5, m5_ci + 2, h4_trend, "standard_m5")
                                        if m5_signal:
                                            active_m5_std = _active_of_type('STANDARD_M5')
                                            if len(active_m5_std) == 0:
                                                m5_margin = float(self._cfg_risk['fixed_margin'])
                                                if self._can_open_with_margin(m5_margin, _all_active(), close_price):
                                                    m5_entry_price = float(df_m5['close'].iat[m5_ci])
                                                    m5_pos = self._open_standard_position(
                                                        symbol, m5_signal, m5_entry_price, current_ts,
                                                        entry_type='STANDARD_M5',
                                                    )
                                                    if m5_pos:
                                                        all_positions.append(m5_pos)
                                                        self.balance -= m5_pos['margin']
                                                        signaled_m5.add(m5_dedup_key)
                                                        logger.info(f"  OPEN STANDARD_M5 {m5_pos['side']} {symbol} @ ${m5_entry_price:,.2f}")
                                last_scanned_m5 = m5_new_scanned

                # ── EMA610 Entries (separate trend check: EMA34+89 vs EMA610) ──
                ema610_h4_trend = self._detect_ema610_h4_trend_fast(df_h4, h4_end)
                max_dist = self._cfg_ema610_entry.get('max_distance_pct', 0.04)

                if ema610_h4_trend and self._cfg_ema610_entry.get('enabled', True) and not ema_blocked:

                    # ── EMA610 H4 Entry (on H4 candle close) ──
                    if h4_candle_closed and h4_end >= 2:
                        active_h4_ema = [p for p in all_positions
                                         if p.get('status') != 'CLOSED'
                                         and p.get('entry_type') == 'EMA610_H4']
                        can_open_h4 = len(active_h4_ema) == 0

                        closed_h4_candle_ts_val = df_h4.index[h4_end - 2]
                        if can_open_h4 and last_ema610_h4_entry_candle_ts is not None:
                            if closed_h4_candle_ts_val == last_ema610_h4_entry_candle_ts:
                                can_open_h4 = False

                        if can_open_h4:
                            ema610_h4_margin = self._cfg_risk['fixed_margin'] * self._cfg_risk.get('ema610_h4_margin_multiplier', 1)
                            can_open_h4 = self._can_open_with_margin(ema610_h4_margin, _all_active(), close_price)

                        # Distance filter: price within max_distance_pct of EMA610
                        if can_open_h4 and (h4_end - 1) >= 610:
                            if self._check_ema610_distance(df_h4, h4_end, max_dist):
                                ema610_signal = self._check_ema610_touch_fast(df_h4, h4_end - 1, 'h4', ema610_h4_trend)
                                if ema610_signal:
                                    pos = self._open_ema610_position(symbol, ema610_signal, current_ts, 'h4')
                                    if pos:
                                        all_positions.append(pos)
                                        last_ema610_h4_entry_candle_ts = closed_h4_candle_ts_val
                                        self.balance -= pos['margin']
                                        logger.info(f"  OPEN EMA610_H4 {pos['side']} {symbol} @ ${pos['entry_price']:,.2f}")

                    # ── EMA610 H1 Entry (on H1 candle close) — needs H1 EMA alignment ──
                    if h1_candle_closed and h1_end >= 2:
                        # H1 alignment check: H1 EMA34+89 vs H1 EMA610
                        h1_ema_aligned = self._check_ema610_h1_alignment_fast(df_h1, h1_end, ema610_h4_trend)

                        if h1_ema_aligned:
                            active_h1_ema = [p for p in all_positions
                                             if p.get('status') == 'OPEN'
                                             and p.get('entry_type') == 'EMA610_H1']
                            can_open_h1 = len(active_h1_ema) == 0

                            closed_h1_candle_ts_val = df_h1.index[h1_end - 2]
                            if can_open_h1 and last_ema610_h1_entry_candle_ts is not None:
                                if closed_h1_candle_ts_val == last_ema610_h1_entry_candle_ts:
                                    can_open_h1 = False

                            if can_open_h1:
                                ema610_h1_margin = self._cfg_risk['fixed_margin'] * self._cfg_risk.get('ema610_margin_multiplier', 1)
                                can_open_h1 = self._can_open_with_margin(ema610_h1_margin, _all_active(), close_price)

                            # Distance filter + touch check
                            if can_open_h1 and (h1_end - 1) >= 610:
                                if self._check_ema610_distance(df_h1, h1_end, max_dist):
                                    ema610_signal = self._check_ema610_touch_fast(df_h1, h1_end - 1, 'h1', ema610_h4_trend)
                                    if ema610_signal:
                                        pos = self._open_ema610_position(symbol, ema610_signal, current_ts, 'h1')
                                        if pos:
                                            all_positions.append(pos)
                                            last_ema610_h1_entry_candle_ts = closed_h1_candle_ts_val
                                            self.balance -= pos['margin']
                                            logger.info(f"  OPEN EMA610_H1 {pos['side']} {symbol} @ ${pos['entry_price']:,.2f}")

            # ===================================================
            # RSI DIVERGENCE ENTRIES (independent of h4_trend)
            # ===================================================

            # ── M15 RSI Divergence (on each M15 candle) ──
            if self._cfg_rsi_div_exit['m15'].get('enabled', True) and m15_global_idx >= 80:
                m15_candle_ts_str = str(current_ts)
                if rsi_div_last_candle['m15'] != m15_candle_ts_str:
                    rsi_div_last_candle['m15'] = m15_candle_ts_str
                    div_result = RSIDivergence.detect(
                        df_m15.iloc[:m15_global_idx + 1], timeframe='M15',
                        lookback=80, rsi_period=rsi_period,
                    )
                    if div_result.has_divergence and div_result.divergence_type in ('bearish', 'bullish'):
                        div_side = 'SELL' if div_result.divergence_type == 'bearish' else 'BUY'
                        # Wick rejection filter: 2/4 candles must have rejection wicks > 50% body
                        wick_ok = RSIDivergence.check_wick_rejection(
                            df_m15.iloc[:m15_global_idx + 1], div_side
                        )
                        # Check: max 1 RSI_DIV_M15 per coin
                        active_rsi_m15 = _active_of_type('RSI_DIV_M15')
                        if wick_ok and len(active_rsi_m15) == 0:
                            margin = float(self._cfg_risk['fixed_margin'])
                            if self._can_open_with_margin(margin, _all_active(), close_price):
                                # Close existing EMA positions
                                for p in list(all_positions):
                                    if p.get('status') == 'CLOSED':
                                        continue
                                    if p.get('entry_type', '').startswith('STANDARD_') or p.get('entry_type', '').startswith('EMA610_'):
                                        trade = self._force_close(p, close_price, 'RSI_DIV_OVERRIDE')
                                        trade['close_time'] = current_ts
                                        trades.append(trade)
                                        self.balance += trade['margin_returned'] + trade['pnl']
                                # Open RSI div position
                                pos = self._open_rsi_div_position(symbol, div_side, close_price, current_ts, 'm15')
                                if pos:
                                    all_positions.append(pos)
                                    self.balance -= pos['margin']
                                    logger.info(f"  OPEN RSI_DIV_M15 {div_side} {symbol} @ ${close_price:,.2f}")
                                # Set M15 EMA block
                                m15_ema_block = "BUY" if div_side == "SELL" else "SELL"

            # ── H1 RSI Divergence (on H1 candle close) ──
            if self._cfg_rsi_div_exit['h1'].get('enabled', True) and h1_candle_closed and h1_end >= 80:
                h1_candle_ts_str = str(df_h1.index[h1_end - 2])
                if rsi_div_last_candle['h1'] != h1_candle_ts_str:
                    rsi_div_last_candle['h1'] = h1_candle_ts_str
                    div_result = RSIDivergence.detect(
                        df_h1.iloc[:h1_end], timeframe='H1',
                        lookback=80, rsi_period=rsi_period,
                    )
                    if div_result.has_divergence and div_result.divergence_type in ('bearish', 'bullish'):
                        div_side = 'SELL' if div_result.divergence_type == 'bearish' else 'BUY'
                        # Wick rejection filter: 2/4 candles must have rejection wicks > 50% body
                        wick_ok = RSIDivergence.check_wick_rejection(
                            df_h1.iloc[:h1_end], div_side
                        )
                        active_rsi_h1 = _active_of_type('RSI_DIV_H1')
                        if wick_ok and len(active_rsi_h1) == 0:
                            margin = float(self._cfg_risk['fixed_margin'])
                            if self._can_open_with_margin(margin, _all_active(), close_price):
                                # Close existing EMA positions
                                for p in list(all_positions):
                                    if p.get('status') == 'CLOSED':
                                        continue
                                    if p.get('entry_type', '').startswith('STANDARD_') or p.get('entry_type', '').startswith('EMA610_'):
                                        trade = self._force_close(p, close_price, 'RSI_DIV_OVERRIDE')
                                        trade['close_time'] = current_ts
                                        trades.append(trade)
                                        self.balance += trade['margin_returned'] + trade['pnl']
                                # Open RSI div position
                                entry_price = float(df_h1['close'].iat[h1_end - 2])
                                pos = self._open_rsi_div_position(symbol, div_side, entry_price, current_ts, 'h1')
                                if pos:
                                    all_positions.append(pos)
                                    self.balance -= pos['margin']
                                    logger.info(f"  OPEN RSI_DIV_H1 {div_side} {symbol} @ ${entry_price:,.2f}")

            # ── H4 RSI Divergence (on H4 candle close) ──
            if self._cfg_rsi_div_exit['h4'].get('enabled', True) and h4_candle_closed and h4_end >= 80:
                h4_candle_ts_str = str(df_h4.index[h4_end - 2])
                if rsi_div_last_candle['h4'] != h4_candle_ts_str:
                    rsi_div_last_candle['h4'] = h4_candle_ts_str
                    div_result = RSIDivergence.detect(
                        df_h4.iloc[:h4_end], timeframe='H4',
                        lookback=80, rsi_period=rsi_period,
                    )
                    if div_result.has_divergence and div_result.divergence_type in ('bearish', 'bullish'):
                        div_side = 'SELL' if div_result.divergence_type == 'bearish' else 'BUY'
                        # Wick rejection filter: 2/4 candles must have rejection wicks > 50% body
                        wick_ok = RSIDivergence.check_wick_rejection(
                            df_h4.iloc[:h4_end], div_side
                        )
                        active_rsi_h4 = _active_of_type('RSI_DIV_H4')
                        if wick_ok and len(active_rsi_h4) == 0:
                            margin = float(self._cfg_risk['fixed_margin'])
                            if self._can_open_with_margin(margin, _all_active(), close_price):
                                # Close existing EMA positions
                                for p in list(all_positions):
                                    if p.get('status') == 'CLOSED':
                                        continue
                                    if p.get('entry_type', '').startswith('STANDARD_') or p.get('entry_type', '').startswith('EMA610_'):
                                        trade = self._force_close(p, close_price, 'RSI_DIV_OVERRIDE')
                                        trade['close_time'] = current_ts
                                        trades.append(trade)
                                        self.balance += trade['margin_returned'] + trade['pnl']
                                # Open RSI div position
                                entry_price = float(df_h4['close'].iat[h4_end - 2])
                                pos = self._open_rsi_div_position(symbol, div_side, entry_price, current_ts, 'h4')
                                if pos:
                                    all_positions.append(pos)
                                    self.balance -= pos['margin']
                                    logger.info(f"  OPEN RSI_DIV_H4 {div_side} {symbol} @ ${entry_price:,.2f}")

            # Update candle boundary trackers
            prev_h1_candle_ts = current_h1_ts
            prev_h4_candle_ts = current_h4_ts

        # -- Close remaining positions --
        last_price = float(m15_in_range.iloc[-1]['close']) if len(m15_in_range) > 0 else 0

        last_ts = m15_in_range.index[-1] if len(m15_in_range) > 0 else None
        for pos in all_positions:
            if pos.get('status') != 'CLOSED':
                trade = self._force_close(pos, last_price, 'END_OF_BACKTEST')
                trade['close_time'] = last_ts
                trades.append(trade)
                self.balance += trade['margin_returned'] + trade['pnl']

        # Mark TP hits on trades
        for trade in trades:
            source_pos = None
            for pos in all_positions:
                if pos.get('open_time') == trade.get('open_time') and pos.get('side') == trade.get('side'):
                    source_pos = pos
                    break
            if source_pos:
                trade['_tp1_hit_tracked'] = source_pos.get('tp1_hit_tracked', False)
                trade['_tp2_hit_tracked'] = source_pos.get('tp2_hit_tracked', False)

        # Build chart DataFrames
        chart_dfs = {
            'm5': df_m5[df_m5.index >= since].copy(),
            'm15': df_m15[df_m15.index >= since].copy(),
            'h1': df_h1[df_h1.index >= since].copy(),
            'h4': df_h4[df_h4.index >= since].copy(),
        }

        return trades, chart_dfs


# ==============================================================================
# CLI Entry Point
# ==============================================================================

def run_backtest():
    """Run backtest from command line."""
    import argparse

    parser = argparse.ArgumentParser(description='Backtest futures trading strategy V7.4')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT'], help='Symbols to test')
    parser.add_argument('--start', type=str, required=True, help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str, default=None, help='End date (YYYY-MM-DD)')
    parser.add_argument('--balance', type=float, default=10000, help='Initial balance')
    parser.add_argument('--no-divergence', action='store_true', help='Disable divergence filter')

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
    )

    end_date = args.end or datetime.now().strftime('%Y-%m-%d')

    backtester = FuturesBacktester(
        symbols=args.symbols,
        initial_balance=args.balance,
        enable_divergence=not args.no_divergence,
    )

    result = backtester.backtest(start_date=args.start, end_date=end_date)

    result.print_summary(
        symbol=', '.join(args.symbols),
        start=args.start,
        end=end_date,
    )
    result.print_trades()

    print(f"\n  Starting Balance: ${args.balance:,.2f}")
    print(f"  Final Balance:    ${backtester.balance:,.2f}")
    print(f"  Return:           ${backtester.balance - args.balance:+,.2f} "
          f"({(backtester.balance - args.balance) / args.balance * 100:+.1f}%)")


if __name__ == "__main__":
    run_backtest()
