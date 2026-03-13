"""
Signal Detector for Futures Trading

Multi-timeframe strategy with cascade trend confirmation:
- H4 entry: H4 trend → H4 wick touch EMA + rejection
- H1 entry: H4 trend → H1 trend → RSI + Divergence → H1 wick touch EMA + rejection
- M15 entry: H4 trend → H1 trend → M15 trend → RSI + Divergence → M15 wick touch EMA + rejection

Trend check: EMA34 > EMA89 AND price > EMA89 (BUY) / EMA34 < EMA89 AND price < EMA89 (SELL)
"""

import json
import os
import pandas as pd
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from src.trading.core.models import TradingSignal
from src.trading.core.indicators import TechnicalIndicators, ADXIndicator, RSIDivergence, DivergenceResult
from src.trading.core.config import (INDICATORS, ENTRY, STANDARD_ENTRY, TIMEFRAMES,
                                     DIVERGENCE_CONFIG, STANDARD_EXIT, EMA610_ENTRY,
                                     EMA610_EXIT, LEVERAGE, RSI_DIV_EXIT)
from src.trading.core.ohlcv_cache import OHLCVCache

logger = logging.getLogger(__name__)

# EMA warm-up: EMA89 needs ~300 candles to converge (seed influence < 0.1%).
# Without enough warmup, EMA values diverge significantly from TradingView.
EMA_WARMUP = 300


class SignalDetector:
    """
    Detect trading signals using multi-timeframe analysis
    """

    def __init__(self, binance_client, db=None):
        """
        Initialize signal detector

        Args:
            binance_client: Exchange client instance (OKX or Binance)
            db: Optional DatabaseManager instance for logging
        """
        self.client = binance_client
        self.db = db
        self._cache = OHLCVCache(binance_client, default_ttl=55.0)
        # Per-timeframe dedup tracking — keyed by timeframe ("m5", "m15", "h1", "h4")
        self._signaled_candles: Dict[str, set] = {
            "m5": set(), "m15": set(), "h1": set(), "h4": set(),
        }
        self._last_scanned_candle: Dict[str, Dict[str, str]] = {
            "m5": {}, "m15": {}, "h1": {}, "h4": {},
        }
        self._sl_cooldown: Dict[str, Dict[str, str]] = {
            "m5": {}, "m15": {}, "h1": {}, "h4": {},
        }
        # RSI Divergence entry dedup: {symbol: {tf: candle_ts}} — avoid repeat signals on same candle
        self._rsi_div_last_candle: Dict[str, Dict[str, str]] = {}
        # M15 EMA blocking: {symbol: "BUY"|"SELL"} — blocks EMA entries until RSI resets to 50
        self._m15_div_ema_block: Dict[str, str] = {}
        # Callback for divergence notifications (set by futures_bot.py)
        self._on_divergence_detected = None
        # Daily dedup: track "SYMBOL:YYYY-MM-DD" to alert only once per day per symbol
        # Persisted to data/divergence_alerts.json so restart doesn't re-alert
        self._divergence_alerts_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            'data', 'divergence_alerts.json'
        )
        self._divergence_alerted_today: set = self._load_divergence_alerts()

    def register_sl_cooldown(self, symbol: str, candle_ts: str, timeframe: str = "m15"):
        """Register that a symbol was stopped out during this candle.
        Blocks re-entry on same candle to prevent whipsaw losses.

        Args:
            symbol: Trading pair
            candle_ts: Candle timestamp string
            timeframe: "m15", "h1", or "h4"
        """
        self._sl_cooldown[timeframe][symbol] = candle_ts
        logger.info(f"{symbol}: SL cooldown registered for {timeframe.upper()} candle {candle_ts}")

    def _load_divergence_alerts(self) -> set:
        """Load today's divergence alerts from file (survives restart)"""
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            if os.path.exists(self._divergence_alerts_file):
                with open(self._divergence_alerts_file, 'r') as f:
                    data = json.load(f)
                # Only keep today's entries
                alerts = {k for k in data if k.endswith(today)}
                if alerts:
                    logger.info(f"[DIVERGENCE] Loaded {len(alerts)} alerts from file (won't re-alert)")
                return alerts
        except Exception as e:
            logger.error(f"Error loading divergence alerts: {e}")
        return set()

    def _save_divergence_alerts(self):
        """Save today's divergence alerts to file"""
        try:
            with open(self._divergence_alerts_file, 'w') as f:
                json.dump(list(self._divergence_alerted_today), f)
        except Exception as e:
            logger.error(f"Error saving divergence alerts: {e}")

    def detect_h4_trend(self, symbol: str) -> Optional[str]:
        """
        Detect trend on H4 timeframe

        Rules:
        - BUY_TREND: EMA34 > EMA89 AND Price > EMA89
        - SELL_TREND: EMA34 < EMA89 AND Price < EMA89
        - NO_TREND: Otherwise (sideways)

        Args:
            symbol: Trading pair

        Returns:
            "BUY_TREND", "SELL_TREND", or None
        """
        try:
            # Fetch H4 data (extra candles for EMA warm-up)
            df_h4 = self._cache.fetch(
                symbol=symbol,
                timeframe=TIMEFRAMES['trend'],
                limit=100 + EMA_WARMUP
            )

            # Calculate indicators (use closed candles only to prevent repainting)
            indicators = TechnicalIndicators.get_all_indicators(
                df_h4,
                ema_fast=INDICATORS['ema_fast'],
                ema_slow=INDICATORS['ema_slow'],
                use_closed_candle=True  # Exclude forming candle in Live Bot
            )

            ema34 = indicators.ema34
            ema89 = indicators.ema89
            price = indicators.current_price

            # Check trend conditions
            if ema34 > ema89 and price > ema89:
                logger.info(f"{symbol} H4: BUY_TREND (EMA34={ema34:.6g} > EMA89={ema89:.6g}, Price={price:.6g})")
                return "BUY_TREND"

            elif ema34 < ema89 and price < ema89:
                logger.info(f"{symbol} H4: SELL_TREND (EMA34={ema34:.6g} < EMA89={ema89:.6g}, Price={price:.6g})")
                return "SELL_TREND"

            else:
                logger.debug(f"{symbol} H4: NO_TREND (sideways)")
                return None

        except Exception as e:
            logger.error(f"Error detecting H4 trend for {symbol}: {e}")
            return None

    def check_h1_trend(self, symbol: str, h4_trend: str) -> bool:
        """
        Check H1 EMA34/89 trend alignment.

        Rules:
        - BUY_TREND: EMA34 > EMA89 AND price > EMA89
        - SELL_TREND: EMA34 < EMA89 AND price < EMA89

        Returns:
            True if H1 trend aligns with h4_trend
        """
        try:
            df_h1 = self._cache.fetch(
                symbol=symbol,
                timeframe=TIMEFRAMES['filter'],
                limit=100 + EMA_WARMUP
            )
            indicators = TechnicalIndicators.get_all_indicators(
                df_h1,
                ema_fast=INDICATORS['ema_fast'],
                ema_slow=INDICATORS['ema_slow'],
                use_closed_candle=True
            )
            ema34 = indicators.ema34
            ema89 = indicators.ema89
            price = indicators.current_price

            if h4_trend == "BUY_TREND":
                passed = ema34 > ema89 and price > ema89
                logger.info(f"{symbol} H1 trend: EMA34={ema34:.6g} EMA89={ema89:.6g} Price={price:.6g} {'PASS' if passed else 'FAIL'}")
                return passed
            elif h4_trend == "SELL_TREND":
                passed = ema34 < ema89 and price < ema89
                logger.info(f"{symbol} H1 trend: EMA34={ema34:.6g} EMA89={ema89:.6g} Price={price:.6g} {'PASS' if passed else 'FAIL'}")
                return passed
            return False
        except Exception as e:
            logger.error(f"Error checking H1 trend for {symbol}: {e}")
            return False

    def check_h1_rsi_filter(self, symbol: str, h4_trend: str) -> bool:
        """
        Check H1 RSI filter (overbought/oversold).

        Rules:
        - BUY_TREND: RSI < 70
        - SELL_TREND: RSI > 30
        """
        try:
            df_h1 = self._cache.fetch(
                symbol=symbol,
                timeframe=TIMEFRAMES['filter'],
                limit=100 + EMA_WARMUP
            )
            indicators = TechnicalIndicators.get_all_indicators(
                df_h1,
                rsi_period=INDICATORS['rsi_period'],
                use_closed_candle=True
            )
            rsi_h1 = indicators.rsi

            if h4_trend == "BUY_TREND":
                passed = rsi_h1 < ENTRY['rsi_overbought']
                logger.info(f"{symbol} H1: RSI={rsi_h1:.2f} {'PASS' if passed else 'FAIL'} (< 70)")
                return passed
            elif h4_trend == "SELL_TREND":
                passed = rsi_h1 > ENTRY['rsi_oversold']
                logger.info(f"{symbol} H1: RSI={rsi_h1:.2f} {'PASS' if passed else 'FAIL'} (> 30)")
                return passed
            return False
        except Exception as e:
            logger.error(f"Error checking H1 RSI for {symbol}: {e}")
            return False

    def check_adx_filter(self, symbol: str) -> bool:
        """
        Check ADX on H1 timeframe. Blocks entry when market is sideways.

        Returns True if ADX >= threshold (trending), False if below (sideways).
        Returns True if threshold is 0 (filter disabled).
        """
        threshold = ENTRY.get('adx_threshold', 0)
        if threshold <= 0:
            return True  # Filter disabled

        try:
            df_h1 = self.client.fetch_ohlcv(
                symbol=symbol,
                timeframe=TIMEFRAMES['filter'],  # H1
                limit=100
            )
            adx_series = ADXIndicator.calculate_adx(df_h1, period=ENTRY.get('adx_period', 14))
            # Use closed candle (iloc[-2]) to avoid look-ahead bias
            adx_val = float(adx_series.iloc[-2])

            passed = adx_val >= threshold
            logger.info(f"{symbol} H1: ADX={adx_val:.1f} {'PASS' if passed else 'FAIL'} (>= {threshold})")
            return passed
        except Exception as e:
            logger.error(f"Error checking ADX for {symbol}: {e}")
            return True  # Allow entry on error (fail-open)

    def check_m15_trend(self, symbol: str, h4_trend: str) -> bool:
        """
        Check M15 EMA34/89 trend alignment.

        Rules:
        - BUY_TREND: EMA34 > EMA89 AND price > EMA89
        - SELL_TREND: EMA34 < EMA89 AND price < EMA89
        """
        try:
            df_m15 = self._cache.fetch(
                symbol=symbol,
                timeframe=TIMEFRAMES['entry'],
                limit=100 + EMA_WARMUP
            )
            indicators = TechnicalIndicators.get_all_indicators(
                df_m15,
                ema_fast=INDICATORS['ema_fast'],
                ema_slow=INDICATORS['ema_slow'],
                use_closed_candle=True
            )
            ema34 = indicators.ema34
            ema89 = indicators.ema89
            price = indicators.current_price

            if h4_trend == "BUY_TREND":
                passed = ema34 > ema89 and price > ema89
                logger.info(f"{symbol} M15 trend: EMA34={ema34:.6g} EMA89={ema89:.6g} Price={price:.6g} {'PASS' if passed else 'FAIL'}")
                return passed
            elif h4_trend == "SELL_TREND":
                passed = ema34 < ema89 and price < ema89
                logger.info(f"{symbol} M15 trend: EMA34={ema34:.6g} EMA89={ema89:.6g} Price={price:.6g} {'PASS' if passed else 'FAIL'}")
                return passed
            return False
        except Exception as e:
            logger.error(f"Error checking M15 trend for {symbol}: {e}")
            return False

    def check_divergence_filter(self, symbol: str, h4_trend: str) -> Tuple[bool, List[DivergenceResult]]:
        """
        Check RSI divergence on M15, H1 and H4 timeframes.

        If any timeframe shows divergence blocking the trade direction,
        the entry is blocked.

        Args:
            symbol: Trading pair
            h4_trend: "BUY_TREND" or "SELL_TREND"

        Returns:
            Tuple of (passed, divergences_found)
        """
        if not DIVERGENCE_CONFIG.get('enabled', True):
            return True, []

        signal_direction = "BUY" if h4_trend == "BUY_TREND" else "SELL"
        divergences = []
        cfg = DIVERGENCE_CONFIG

        try:
            # Check M15 divergence
            rsi_warmup = 200  # Extra candles for RSI warm-up (match TradingView accuracy)
            if cfg.get('m15_scan_enabled', True):
                df_m15 = self._cache.fetch(
                    symbol=symbol,
                    timeframe=TIMEFRAMES['entry'],
                    limit=cfg.get('m15_lookback', 120) + rsi_warmup
                )

                if df_m15 is not None and len(df_m15) >= 30:
                    m15_result = RSIDivergence.detect(
                        df=df_m15.iloc[:-1],
                        timeframe="M15",
                        lookback=cfg.get('m15_lookback', 120),
                        rsi_period=INDICATORS['rsi_period'],
                        swing_window=cfg['swing_window'],
                        min_swing_distance=cfg['min_swing_distance'],
                        max_swing_pairs=cfg['max_swing_pairs'],
                        min_retracement_pct=cfg.get('min_retracement_pct', 1.5)
                    )

                    if m15_result.has_divergence:
                        divergences.append(m15_result)
                        logger.info(f"{symbol} {m15_result.description}")

            # Check H1 divergence
            df_h1 = self._cache.fetch(
                symbol=symbol,
                timeframe=TIMEFRAMES['filter'],
                limit=cfg['h1_lookback'] + rsi_warmup
            )

            # Use only CLOSED candles for divergence detection (exclude last forming candle)
            h1_result = RSIDivergence.detect(
                df=df_h1.iloc[:-1],  # Exclude last candle (forming)
                timeframe="H1",
                lookback=cfg['h1_lookback'],
                rsi_period=INDICATORS['rsi_period'],
                swing_window=cfg['swing_window'],
                min_swing_distance=cfg['min_swing_distance'],
                max_swing_pairs=cfg['max_swing_pairs'],
                min_retracement_pct=cfg.get('min_retracement_pct', 1.5)
            )

            if h1_result.has_divergence:
                divergences.append(h1_result)
                logger.info(f"{symbol} {h1_result.description}")

            # Check H4 divergence
            df_h4 = self._cache.fetch(
                symbol=symbol,
                timeframe=TIMEFRAMES['trend'],
                limit=cfg['h4_lookback'] + rsi_warmup
            )

            # Use only CLOSED candles for divergence detection (exclude last forming candle)
            h4_result = RSIDivergence.detect(
                df=df_h4.iloc[:-1],  # Exclude last candle (forming)
                timeframe="H4",
                lookback=cfg['h4_lookback'],
                rsi_period=INDICATORS['rsi_period'],
                swing_window=cfg['swing_window'],
                min_swing_distance=cfg['min_swing_distance'],
                max_swing_pairs=cfg['max_swing_pairs'],
                min_retracement_pct=cfg.get('min_retracement_pct', 1.5)
            )

            if h4_result.has_divergence:
                divergences.append(h4_result)
                logger.info(f"{symbol} {h4_result.description}")

            # Check if any divergence blocks the current signal direction
            blocked = any(
                d.blocks_direction == signal_direction
                for d in divergences
            )

            if blocked:
                blocking = [d for d in divergences if d.blocks_direction == signal_direction]
                for d in blocking:
                    logger.warning(
                        f"{symbol}: BLOCKED {signal_direction} - {d.divergence_type} divergence on {d.timeframe}"
                    )
                # Log divergence block to database
                if self.db:
                    try:
                        self.db.log_operation(
                            operation_name='divergence_blocked',
                            risk_score=0,
                            status='blocked',
                            meta_data={
                                'symbol': symbol,
                                'blocked_direction': signal_direction,
                                'divergences': [
                                    {'type': d.divergence_type, 'timeframe': d.timeframe}
                                    for d in blocking
                                ],
                            }
                        )
                    except Exception as e:
                        logger.error(f"[DB] Error logging divergence block: {e}")
                return False, divergences

            if divergences:
                logger.info(f"{symbol}: Divergence found but does not block {signal_direction}")

            return True, divergences

        except Exception as e:
            logger.error(f"Error checking divergence for {symbol}: {e}")
            return True, []  # Fail open - don't block trades on error

    def _detect_entry_on_timeframe(
        self,
        symbol: str,
        h4_trend: str,
        timeframe: str,
        entry_type: str,
        signaled_candles: set,
        last_scanned_candle: Dict[str, str],
        sl_cooldown: Dict[str, str],
    ) -> Optional[TradingSignal]:
        """
        Shared entry detection logic for any timeframe.

        Wick must touch EMA34 or EMA89 (±tolerance), close on correct side,
        rejection wick >= threshold. Uses per-TF dedup tracking.

        Args:
            symbol: Trading pair
            h4_trend: "BUY_TREND" or "SELL_TREND"
            timeframe: OHLCV timeframe string ("15m", "1h", "4h")
            entry_type: "standard_m15", "standard_h1", "standard_h4"
            signaled_candles: Per-TF dedup set
            last_scanned_candle: Per-TF last scanned candle dict
            sl_cooldown: Per-TF SL cooldown dict

        Returns:
            TradingSignal if valid entry found, else None
        """
        tf_label = entry_type.replace("standard_", "").upper()  # "M15", "H1", "H4"

        try:
            df = self._cache.fetch(
                symbol=symbol,
                timeframe=timeframe,
                limit=100
            )

            indicators = TechnicalIndicators.get_all_indicators(
                df,
                ema_fast=INDICATORS['ema_fast'],
                ema_slow=INDICATORS['ema_slow'],
                use_closed_candle=True
            )

            # Use CLOSED candle (iloc[-2]), not forming one (iloc[-1])
            latest_candle = df.iloc[-2]

            candle_ts = str(latest_candle.name) if hasattr(latest_candle, 'name') else ""
            dedup_key = f"{symbol}:{candle_ts}"

            # Dedup: skip if this candle already triggered a signal
            if dedup_key in signaled_candles:
                logger.debug(f"{symbol} {tf_label}: Already signaled on candle {candle_ts}, skipping")
                return None

            # SL Cooldown: if stopped out on this candle, skip re-entry
            sl_cooldown_ts = sl_cooldown.get(symbol)
            if sl_cooldown_ts and sl_cooldown_ts == candle_ts:
                logger.info(f"{symbol} {tf_label}: SL cooldown active on candle {candle_ts}, skipping re-entry")
                return None
            if sl_cooldown_ts and sl_cooldown_ts != candle_ts:
                del sl_cooldown[symbol]

            # New candle check: only signal when a NEW candle has closed
            last_ts = last_scanned_candle.get(symbol)
            last_scanned_candle[symbol] = candle_ts
            if last_ts is None:
                logger.info(f"{symbol} {tf_label}: First scan, recording candle {candle_ts} (no signal on first scan)")
                return None
            if candle_ts == last_ts:
                logger.debug(f"{symbol} {tf_label}: Same candle {candle_ts}, waiting for new candle")
                return None
            logger.info(f"{symbol} {tf_label}: New candle detected {candle_ts} (prev: {last_ts})")

            open_price = latest_candle['open']
            high = latest_candle['high']
            low = latest_candle['low']
            close = latest_candle['close']

            price = indicators.current_price
            ema34 = indicators.ema34
            ema89 = indicators.ema89

            # Per-timeframe tolerance from STANDARD_ENTRY
            tf_key = entry_type.replace("standard_", "")  # "standard_m15" → "m15"
            tolerance = STANDARD_ENTRY.get(tf_key, {}).get('tolerance', 0.002)
            wick_threshold = INDICATORS['wick_threshold']

            if h4_trend == "BUY_TREND":
                touches_ema34 = low <= ema34 * (1 + tolerance) and close > ema34
                touches_ema89 = low <= ema89 * (1 + tolerance) and close > ema89

                if not (touches_ema34 or touches_ema89):
                    logger.debug(f"{symbol} {tf_label}: BUY - Low not touching EMA (Low={low:.2f}, Close={close:.2f}, EMA34={ema34:.6g}, EMA89={ema89:.6g})")
                    return None

                is_valid = TechnicalIndicators.is_bullish_rejection(
                    open_price, high, low, close, threshold=wick_threshold
                )
                if is_valid:
                    _, wick_ratio = TechnicalIndicators.calculate_candle_wick_ratio(
                        open_price, high, low, close
                    )
                    touched = "EMA34" if touches_ema34 else "EMA89"
                    logger.info(f"{symbol} {tf_label}: BUY signal! Lower wick={wick_ratio:.1f}% touches {touched}, close={close:.2f} above {touched}")
                    signaled_candles.add(dedup_key)

                    return self._create_signal(
                        symbol=symbol,
                        signal_type="BUY",
                        entry_price=price,
                        h4_trend=h4_trend,
                        indicators=indicators,
                        wick_ratio=wick_ratio,
                        entry_type=entry_type,
                        df_h1=self._cache.fetch(symbol, TIMEFRAMES['filter'], 100 + EMA_WARMUP),
                        df_m15=df if timeframe == TIMEFRAMES['entry'] else None,
                    )

            elif h4_trend == "SELL_TREND":
                touches_ema34 = high >= ema34 * (1 - tolerance) and close < ema34
                touches_ema89 = high >= ema89 * (1 - tolerance) and close < ema89

                if not (touches_ema34 or touches_ema89):
                    logger.debug(f"{symbol} {tf_label}: SELL - High not touching EMA (High={high:.2f}, Close={close:.2f}, EMA34={ema34:.6g}, EMA89={ema89:.6g})")
                    return None

                is_valid = TechnicalIndicators.is_bearish_rejection(
                    open_price, high, low, close, threshold=wick_threshold
                )
                if is_valid:
                    wick_ratio, _ = TechnicalIndicators.calculate_candle_wick_ratio(
                        open_price, high, low, close
                    )
                    touched = "EMA34" if touches_ema34 else "EMA89"
                    logger.info(f"{symbol} {tf_label}: SELL signal! Upper wick={wick_ratio:.1f}% touches {touched}, close={close:.2f} below {touched}")
                    signaled_candles.add(dedup_key)

                    return self._create_signal(
                        symbol=symbol,
                        signal_type="SELL",
                        entry_price=price,
                        h4_trend=h4_trend,
                        indicators=indicators,
                        wick_ratio=wick_ratio,
                        entry_type=entry_type,
                        df_h1=self._cache.fetch(symbol, TIMEFRAMES['filter'], 100 + EMA_WARMUP),
                        df_m15=df if timeframe == TIMEFRAMES['entry'] else None,
                    )

            return None

        except Exception as e:
            logger.error(f"Error detecting {tf_label} entry for {symbol}: {e}")
            return None

    def detect_m5_entry(
        self,
        symbol: str,
        h4_trend: str
    ) -> Optional[TradingSignal]:
        """Detect entry signal on M5 timeframe (wick touch EMA + rejection)."""
        return self._detect_entry_on_timeframe(
            symbol=symbol,
            h4_trend=h4_trend,
            timeframe="5m",
            entry_type="standard_m5",
            signaled_candles=self._signaled_candles["m5"],
            last_scanned_candle=self._last_scanned_candle["m5"],
            sl_cooldown=self._sl_cooldown["m5"],
        )

    def detect_m15_entry(
        self,
        symbol: str,
        h4_trend: str
    ) -> Optional[TradingSignal]:
        """Detect entry signal on M15 timeframe (wick touch EMA + rejection)."""
        return self._detect_entry_on_timeframe(
            symbol=symbol,
            h4_trend=h4_trend,
            timeframe=TIMEFRAMES['entry'],
            entry_type="standard_m15",
            signaled_candles=self._signaled_candles["m15"],
            last_scanned_candle=self._last_scanned_candle["m15"],
            sl_cooldown=self._sl_cooldown["m15"],
        )

    def detect_h1_entry(
        self,
        symbol: str,
        h4_trend: str
    ) -> Optional[TradingSignal]:
        """Detect entry signal on H1 timeframe (wick touch EMA + rejection)."""
        return self._detect_entry_on_timeframe(
            symbol=symbol,
            h4_trend=h4_trend,
            timeframe=TIMEFRAMES['filter'],
            entry_type="standard_h1",
            signaled_candles=self._signaled_candles["h1"],
            last_scanned_candle=self._last_scanned_candle["h1"],
            sl_cooldown=self._sl_cooldown["h1"],
        )

    def detect_h4_entry(
        self,
        symbol: str,
        h4_trend: str
    ) -> Optional[TradingSignal]:
        """Detect entry signal on H4 timeframe (wick touch EMA + rejection)."""
        return self._detect_entry_on_timeframe(
            symbol=symbol,
            h4_trend=h4_trend,
            timeframe=TIMEFRAMES['trend'],
            entry_type="standard_h4",
            signaled_candles=self._signaled_candles["h4"],
            last_scanned_candle=self._last_scanned_candle["h4"],
            sl_cooldown=self._sl_cooldown["h4"],
        )

    def _create_signal(
        self,
        symbol: str,
        signal_type: str,
        entry_price: float,
        h4_trend: str,
        indicators,
        wick_ratio: float,
        entry_type: str = "standard_m15",
        df_h1: pd.DataFrame = None,
        df_m15: pd.DataFrame = None
    ) -> TradingSignal:
        """
        Create complete trading signal with targets.

        TP values read from STANDARD_EXIT[tf] for standard_* entries.

        Args:
            symbol: Trading pair
            signal_type: "BUY" or "SELL"
            entry_price: Entry price
            h4_trend: H4 trend
            indicators: Indicator values from entry timeframe
            wick_ratio: Wick ratio percentage
            entry_type: "standard_m15", "standard_h1", "standard_h4"
            df_h1: H1 dataframe (optional, fetched if needed)
            df_m15: M15 dataframe (optional)

        Returns:
            Complete TradingSignal
        """
        # Fetch H1 if not provided (for RSI)
        if df_h1 is None:
            df_h1 = self._cache.fetch(symbol, TIMEFRAMES['filter'], 100 + EMA_WARMUP)
        # Fetch M15 if not provided
        if df_m15 is None:
            df_m15 = self._cache.fetch(symbol, TIMEFRAMES['entry'], 100 + EMA_WARMUP)

        # Get H4 indicators (use closed candles only)
        df_h4 = self._cache.fetch(symbol, TIMEFRAMES['trend'], 100 + EMA_WARMUP)
        h4_indicators = TechnicalIndicators.get_all_indicators(df_h4, use_closed_candle=True)

        # Get H1 RSI (use closed candles only)
        h1_indicators = TechnicalIndicators.get_all_indicators(df_h1, use_closed_candle=True)

        # ROI-based TP from STANDARD_EXIT per timeframe
        leverage = LEVERAGE.get(symbol, LEVERAGE.get('default', 5))
        tf = entry_type.replace("standard_", "")  # "m15", "h1", "h4"
        exit_cfg = STANDARD_EXIT.get(tf, STANDARD_EXIT.get('m15', {}))
        tp1_roi = exit_cfg.get('tp1_roi', 20)
        tp2_roi = exit_cfg.get('tp2_roi', 40)

        tp1_roi_pct = tp1_roi / 100 / leverage
        tp2_roi_pct = tp2_roi / 100 / leverage

        if signal_type == "BUY":
            tp1 = entry_price * (1 + tp1_roi_pct)
            tp2 = entry_price * (1 + tp2_roi_pct)
        else:
            tp1 = entry_price * (1 - tp1_roi_pct)
            tp2 = entry_price * (1 - tp2_roi_pct)

        # SL: Will be calculated in position_manager (ROI-based hard SL)
        sl = None

        # Log TP distances
        tp1_dist = abs(tp1 - entry_price) / entry_price * 100
        tp2_dist = abs(tp2 - entry_price) / entry_price * 100
        logger.info(
            f"[SIGNAL] {symbol} [{entry_type}]: TP1=${tp1:.4f} (+{tp1_roi}% ROI, {tp1_dist:.2f}% move) | "
            f"TP2=${tp2:.4f} (+{tp2_roi}% ROI, {tp2_dist:.2f}% move) | Lev={leverage}x"
        )

        return TradingSignal(
            symbol=symbol,
            signal_type=signal_type,
            entry_price=entry_price,
            timestamp=datetime.now(),
            h4_trend=h4_trend,
            h4_ema34=h4_indicators.ema34,
            h4_ema89=h4_indicators.ema89,
            h1_rsi=h1_indicators.rsi,
            m15_ema34=indicators.ema34,
            m15_ema89=indicators.ema89,
            wick_ratio=wick_ratio,
            entry_type=entry_type,
            stop_loss=sl,
            take_profit_1=tp1,
            take_profit_2=tp2
        )

    def scan_for_signals(self, symbols: list) -> Dict[str, List[TradingSignal]]:
        """
        Scan multiple symbols for trading signals across M15, H1, H4.

        Cascade trend confirmation (big TF → small TF):
        - H4 entry: H4 trend → ADX H1 → H4 wick touch
        - H1 entry: H4 trend → ADX H1 → H1 trend → RSI + Divergence → H1 wick touch
        - M15 entry: H4 trend → ADX H1 → H1 trend → M15 trend → RSI + Divergence → M15 wick touch
        - M5 entry:  H4 trend → ADX H1 → H1 trend → M15 trend → RSI + Divergence → M5 wick touch

        Args:
            symbols: List of trading pairs

        Returns:
            Dict mapping symbol to list of signals (may contain 0-3 signals per symbol)
        """
        signals: Dict[str, List[TradingSignal]] = {}

        # Clear cache at start of scan cycle for fresh data
        self._cache.invalidate()
        self._cache.reset_stats()

        for symbol in symbols:
            symbol_signals: List[TradingSignal] = []

            try:
                logger.info(f"Scanning {symbol}...")

                # Step 1: H4 trend (required for ALL entry types)
                h4_trend = self.detect_h4_trend(symbol)
                if not h4_trend:
                    signals[symbol] = []
                    continue

                # Step 1.5: ADX H1 filter (required for ALL entry types)
                # Blocks entries when market is sideways (ADX < threshold)
                if not self.check_adx_filter(symbol):
                    signals[symbol] = []
                    continue

                # --- H4 entry: only needs H4 trend ---
                if STANDARD_ENTRY['h4']['enabled']:
                    h4_signal = self.detect_h4_entry(symbol, h4_trend)
                    if h4_signal:
                        symbol_signals.append(h4_signal)

                # --- H1 entry: needs H4 trend + H1 trend ---
                h1_trend_ok = self.check_h1_trend(symbol, h4_trend)
                if h1_trend_ok:
                    # RSI + Divergence filter for H1 entry
                    rsi_ok = self.check_h1_rsi_filter(symbol, h4_trend)
                    if rsi_ok:
                        div_ok, _ = self.check_divergence_filter(symbol, h4_trend)
                        if div_ok:
                            if STANDARD_ENTRY['h1']['enabled']:
                                h1_signal = self.detect_h1_entry(symbol, h4_trend)
                                if h1_signal:
                                    symbol_signals.append(h1_signal)

                    # --- M15 entry: needs H4 trend + H1 trend + M15 trend ---
                    m15_trend_ok = self.check_m15_trend(symbol, h4_trend)
                    if m15_trend_ok:
                        # RSI + Divergence filter for M15 entry (reuse H1 RSI check)
                        m15_rsi_ok = rsi_ok if rsi_ok else self.check_h1_rsi_filter(symbol, h4_trend)
                        if m15_rsi_ok:
                            m15_div_ok, _ = self.check_divergence_filter(symbol, h4_trend)
                            if m15_div_ok:
                                if STANDARD_ENTRY['m15']['enabled']:
                                    m15_signal = self.detect_m15_entry(symbol, h4_trend)
                                    if m15_signal:
                                        symbol_signals.append(m15_signal)

                                # --- M5 entry: needs H4 + H1 + M15 trend + M5 wick touch ---
                                if STANDARD_ENTRY['m5']['enabled']:
                                    m5_signal = self.detect_m5_entry(symbol, h4_trend)
                                    if m5_signal:
                                        symbol_signals.append(m5_signal)

                signals[symbol] = symbol_signals

            except Exception as e:
                logger.error(f"Error scanning {symbol}: {e}")
                signals[symbol] = []

        # Log cache performance
        stats = self._cache.stats
        logger.info(
            f"[CACHE] Scan complete: {stats['hits']} hits, {stats['misses']} misses "
            f"({stats['hit_rate']} hit rate, {stats['cached_entries']} entries)"
        )

        return signals

    def scan_divergences(self, symbols: list) -> tuple:
        """
        Independently scan symbols for RSI divergence on H4 and D1.
        Returns all results + new-only results (symbols not seen in previous scans today).

        Args:
            symbols: List of trading pairs (e.g. top 500 by market cap)

        Returns:
            Tuple of (all_results, new_results) where each is Dict[symbol, List[DivergenceResult]]
            - all_results: every symbol with divergence this scan
            - new_results: only symbols not previously alerted today
        """
        if not DIVERGENCE_CONFIG.get('enabled', True):
            return {}, {}

        today = datetime.now().strftime("%Y-%m-%d")
        cfg = DIVERGENCE_CONFIG
        scan_timeframes = cfg.get('scan_timeframes', ['4h', '1d'])
        all_results = {}
        new_results = {}

        # Map timeframe string to (label, lookback)
        tf_config = {
            '4h': ('H4', cfg['h4_lookback']),
            '1d': ('D1', cfg.get('d1_lookback', 30)),
        }

        for symbol in symbols:
            dedup_key = f"{symbol}:{today}"

            try:
                divergences = []

                for tf in scan_timeframes:
                    if tf not in tf_config:
                        continue
                    label, lookback = tf_config[tf]

                    df = self._cache.fetch(
                        symbol=symbol,
                        timeframe=tf,
                        limit=lookback
                    )
                    result = RSIDivergence.detect(
                        df=df,
                        timeframe=label,
                        lookback=lookback,
                        rsi_period=INDICATORS['rsi_period'],
                        swing_window=cfg['swing_window'],
                        min_swing_distance=cfg['min_swing_distance'],
                        max_swing_pairs=cfg['max_swing_pairs'],
                        min_retracement_pct=cfg.get('min_retracement_pct', 1.5)
                    )
                    if result.has_divergence:
                        divergences.append(result)

                if divergences:
                    all_results[symbol] = divergences
                    for d in divergences:
                        logger.info(f"[DIVERGENCE] {symbol}: {d.description}")

                    # Track new vs already-alerted
                    if dedup_key not in self._divergence_alerted_today:
                        new_results[symbol] = divergences
                        self._divergence_alerted_today.add(dedup_key)

                    # Log divergences to database
                    if self.db:
                        try:
                            for d in divergences:
                                self.db.store_trading_signal(
                                    symbol=symbol,
                                    signal=d.blocks_direction or 'NEUTRAL',
                                    rsi=d.rsi_swing_2 or 0.0,
                                    ema=0.0,
                                    price=d.price_swing_2 or 0.0,
                                    volume=0.0,
                                    metadata={
                                        'type': 'divergence_scan',
                                        'divergence_type': d.divergence_type,
                                        'timeframe': d.timeframe,
                                        'blocks_direction': d.blocks_direction,
                                        'price_swing_1': d.price_swing_1,
                                        'rsi_swing_1': d.rsi_swing_1,
                                    }
                                )
                        except Exception as e:
                            logger.error(f"[DB] Error logging divergence scan: {e}")

            except Exception as e:
                logger.error(f"Error scanning divergence for {symbol}: {e}")

        # Clean old dedup keys (keep only today)
        self._divergence_alerted_today = {
            k for k in self._divergence_alerted_today if k.endswith(today)
        }

        # Save dedup file once after full scan
        if new_results:
            self._save_divergence_alerts()

        return all_results, new_results

    def scan_m15_divergences(self, symbols: list) -> Dict[str, List[DivergenceResult]]:
        """
        Scan M15 divergence for active trading pairs.
        Returns only NEW divergences (cooldown 4h per symbol+type combo).
        If divergence persists after 4h, re-alerts.

        Args:
            symbols: List of trading pairs currently being scanned

        Returns:
            Dict mapping symbol to list of DivergenceResult (new alerts only)
        """
        if not DIVERGENCE_CONFIG.get('enabled', True):
            return {}
        if not DIVERGENCE_CONFIG.get('m15_scan_enabled', True):
            return {}

        now = datetime.now()
        cfg = DIVERGENCE_CONFIG
        lookback = cfg.get('m15_lookback', 120)
        cooldown_minutes = cfg.get('m15_div_cooldown_minutes', 15)
        new_results = {}

        # Lazy init: swing-point dedup dict {key: (swing_sig, alert_time)}
        if not hasattr(self, '_m15_div_cooldown'):
            self._m15_div_cooldown: Dict[str, tuple] = {}

        for symbol in symbols:
            try:
                # Fetch extra candles for RSI warm-up (need ~200 candles before lookback window)
                rsi_warmup = 200
                df_m15 = self._cache.fetch(
                    symbol=symbol,
                    timeframe=TIMEFRAMES['entry'],  # "15m"
                    limit=lookback + rsi_warmup
                )

                if df_m15 is None or len(df_m15) < 30:
                    continue

                result = RSIDivergence.detect(
                    df=df_m15.iloc[:-1],  # Exclude forming candle
                    timeframe="M15",
                    lookback=lookback,
                    rsi_period=INDICATORS['rsi_period'],
                    swing_window=cfg['swing_window'],
                    min_swing_distance=cfg['min_swing_distance'],
                    max_swing_pairs=cfg['max_swing_pairs']
                )

                if result.has_divergence:
                    # Skip stale divergences (swing point 2 older than 24h)
                    if result.time_swing_2:
                        try:
                            t2_str = str(result.time_swing_2).replace('Z', '+00:00')
                            t2_utc = datetime.fromisoformat(t2_str)
                            if t2_utc.tzinfo:
                                t2_utc = t2_utc.replace(tzinfo=None)
                            age_hours = (now - t2_utc).total_seconds() / 3600
                            if age_hours > 24:
                                logger.debug(f"[M15-DIV] {symbol}: skipped stale divergence ({age_hours:.0f}h old)")
                                continue
                        except Exception:
                            pass  # If parsing fails, don't filter

                    dedup_key = f"{symbol}:M15:{result.divergence_type}"
                    swing_sig = (str(result.time_swing_1), str(result.time_swing_2))
                    last_entry = self._m15_div_cooldown.get(dedup_key)

                    should_alert = False
                    is_repeat = ""
                    if last_entry is None:
                        should_alert = True
                    else:
                        last_swing, last_time = last_entry
                        if swing_sig != last_swing:
                            # Divergence evolved (new swing points) → re-alert after cooldown
                            if (now - last_time) >= timedelta(minutes=cooldown_minutes):
                                should_alert = True
                                is_repeat = " (evolving)"
                            else:
                                logger.debug(f"[M15-DIV] {symbol}: new swings but cooldown active ({cooldown_minutes}m)")
                        # Same swing points → already alerted, skip entirely

                    if should_alert:
                        self._m15_div_cooldown[dedup_key] = (swing_sig, now)
                        if symbol not in new_results:
                            new_results[symbol] = []
                        new_results[symbol].append(result)
                        logger.info(f"[M15-DIV] {symbol}: {result.description}{is_repeat}")

            except Exception as e:
                logger.error(f"[M15-DIV] Error scanning {symbol}: {e}")

        # Clean expired cooldowns (older than 24h)
        cutoff = now - timedelta(hours=24)
        self._m15_div_cooldown = {
            k: v for k, v in self._m15_div_cooldown.items() if v[1] > cutoff
        }

        return new_results

    def scan_h1h4_divergences(self, symbols: list) -> Dict[str, Dict[str, List[DivergenceResult]]]:
        """
        Scan H1 and H4 divergence for Telegram alerts.
        Returns {timeframe: {symbol: [DivergenceResult]}} with only NEW alerts.
        Uses same cooldown logic as M15 (4h per symbol+type+tf combo).
        """
        if not DIVERGENCE_CONFIG.get('enabled', True):
            return {}

        now = datetime.now()
        cfg = DIVERGENCE_CONFIG
        cooldown_map = {
            'H1': cfg.get('h1_div_cooldown_minutes', 60),
            'H4': cfg.get('h4_div_cooldown_minutes', 240),
        }

        if not hasattr(self, '_h1h4_div_cooldown'):
            self._h1h4_div_cooldown: Dict[str, tuple] = {}

        tf_configs = {
            'H1': {'ohlcv': '1h', 'lookback': 80},
            'H4': {'ohlcv': '4h', 'lookback': 60},
        }

        results = {}  # {tf_label: {symbol: [DivergenceResult]}}

        for tf_label, tf_cfg in tf_configs.items():
            new_results = {}
            for symbol in symbols:
                try:
                    rsi_warmup = 200
                    df = self._cache.fetch(
                        symbol=symbol,
                        timeframe=tf_cfg['ohlcv'],
                        limit=tf_cfg['lookback'] + rsi_warmup
                    )
                    if df is None or len(df) < 30:
                        continue

                    result = RSIDivergence.detect(
                        df=df.iloc[:-1],
                        timeframe=tf_label,
                        lookback=tf_cfg['lookback'],
                        rsi_period=INDICATORS['rsi_period'],
                        swing_window=cfg['swing_window'],
                        min_swing_distance=cfg['min_swing_distance'],
                        max_swing_pairs=cfg['max_swing_pairs']
                    )

                    if result.has_divergence:
                        # Skip stale divergences (>48h for H1/H4)
                        if result.time_swing_2:
                            try:
                                t2_str = str(result.time_swing_2).replace('Z', '+00:00')
                                t2_utc = datetime.fromisoformat(t2_str)
                                if t2_utc.tzinfo:
                                    t2_utc = t2_utc.replace(tzinfo=None)
                                age_hours = (now - t2_utc).total_seconds() / 3600
                                if age_hours > 48:
                                    continue
                            except Exception:
                                pass

                        dedup_key = f"{symbol}:{tf_label}:{result.divergence_type}"
                        swing_sig = (str(result.time_swing_1), str(result.time_swing_2))
                        cooldown_minutes = cooldown_map.get(tf_label, 60)
                        last_entry = self._h1h4_div_cooldown.get(dedup_key)

                        should_alert = False
                        is_repeat = ""
                        if last_entry is None:
                            should_alert = True
                        else:
                            last_swing, last_time = last_entry
                            if swing_sig != last_swing:
                                if (now - last_time) >= timedelta(minutes=cooldown_minutes):
                                    should_alert = True
                                    is_repeat = " (evolving)"
                                else:
                                    logger.debug(f"[{tf_label}-DIV] {symbol}: new swings but cooldown active ({cooldown_minutes}m)")

                        if should_alert:
                            self._h1h4_div_cooldown[dedup_key] = (swing_sig, now)
                            if symbol not in new_results:
                                new_results[symbol] = []
                            new_results[symbol].append(result)
                            logger.info(f"[{tf_label}-DIV] {symbol}: {result.description}{is_repeat}")

                except Exception as e:
                    logger.error(f"[{tf_label}-DIV] Error scanning {symbol}: {e}")

            if new_results:
                results[tf_label] = new_results

        # Clean expired cooldowns (older than 48h)
        cutoff = now - timedelta(hours=48)
        self._h1h4_div_cooldown = {
            k: v for k, v in self._h1h4_div_cooldown.items() if v[1] > cutoff
        }

        return results

    # ── RSI Divergence Entry Signals ──────────────────────────────────

    def scan_divergence_entries(
        self, symbols: list, tf: str
    ) -> Dict[str, TradingSignal]:
        """
        Scan for RSI divergence entry signals on a specific timeframe.

        Args:
            symbols: Trading pairs to scan
            tf: Timeframe key — "m15", "h1", or "h4"

        Returns:
            Dict mapping symbol to TradingSignal (at most 1 per symbol)
        """
        import math

        tf_map = {"m15": "15m", "h1": "1h", "h4": "4h"}
        ohlcv_tf = tf_map.get(tf)
        if not ohlcv_tf:
            return {}

        exit_cfg = RSI_DIV_EXIT.get(tf, {})
        if not exit_cfg.get('enabled', False):
            return {}

        lookback_map = {"m15": 200, "h1": 160, "h4": 80}
        lookback = DIVERGENCE_CONFIG.get(f'{tf.replace("m15", "m15")}_lookback',
                                         lookback_map.get(tf, 120))
        # M15 uses m15_lookback, H1 uses h1_lookback, H4 uses h4_lookback
        if tf == "m15":
            lookback = DIVERGENCE_CONFIG.get('m15_lookback', 200)
        elif tf == "h1":
            lookback = DIVERGENCE_CONFIG.get('h1_lookback', 160)
        else:
            lookback = DIVERGENCE_CONFIG.get('h4_lookback', 80)

        rsi_warmup = 200
        results: Dict[str, TradingSignal] = {}

        for symbol in symbols:
            try:
                df = self._cache.fetch(
                    symbol=symbol,
                    timeframe=ohlcv_tf,
                    limit=lookback + rsi_warmup
                )
                if df is None or len(df) < 30:
                    continue

                # Exclude forming candle
                df_closed = df.iloc[:-1]

                result = RSIDivergence.detect(
                    df=df_closed,
                    timeframe=tf.upper(),
                    lookback=lookback,
                    rsi_period=INDICATORS['rsi_period'],
                    swing_window=DIVERGENCE_CONFIG.get('swing_window', 3),
                    min_swing_distance=DIVERGENCE_CONFIG.get('min_swing_distance', 8),
                    max_swing_pairs=DIVERGENCE_CONFIG.get('max_swing_pairs', 3),
                )

                if not result.has_divergence:
                    continue

                # Only regular bearish/bullish (not hidden)
                if result.divergence_type not in ("bearish", "bullish"):
                    continue

                # Dedup: skip if same candle already triggered
                last_candle_ts = str(df_closed.index[-1]) if hasattr(df_closed.index, '__getitem__') else str(len(df_closed))
                symbol_dedup = self._rsi_div_last_candle.get(symbol, {})
                if symbol_dedup.get(tf) == last_candle_ts:
                    continue

                # Skip stale divergences (swing_2 older than 24h)
                if result.time_swing_2:
                    try:
                        t2_str = str(result.time_swing_2).replace('Z', '+00:00')
                        t2 = datetime.fromisoformat(t2_str)
                        if t2.tzinfo:
                            t2 = t2.replace(tzinfo=None)
                        age_hours = (datetime.now() - t2).total_seconds() / 3600
                        if age_hours > 24:
                            continue
                    except (ValueError, TypeError):
                        pass

                # Record dedup
                if symbol not in self._rsi_div_last_candle:
                    self._rsi_div_last_candle[symbol] = {}
                self._rsi_div_last_candle[symbol][tf] = last_candle_ts

                # Build signal
                signal_type = "SELL" if result.divergence_type == "bearish" else "BUY"

                # Wick rejection filter: 2/4 candles must have rejection wicks > 50% body
                if not RSIDivergence.check_wick_rejection(df_closed, signal_type):
                    logger.info(
                        f"[RSI-DIV-ENTRY] {symbol} {tf} {signal_type}: "
                        f"skipped — wick rejection filter failed (need 2/4 candles with wick > 50% body)"
                    )
                    continue

                entry_price = float(df_closed['close'].iloc[-1])
                entry_type = f"rsi_div_{tf}"

                # Leverage with multiplier
                default_lev = LEVERAGE.get(symbol, LEVERAGE.get('default', 5))
                lev_mult = exit_cfg.get('leverage_multiplier', 1.5)
                leverage = min(math.ceil(default_lev * lev_mult), 125)

                # TP from ROI config
                tp1_roi = exit_cfg.get('tp1_roi', 15)
                tp2_roi = exit_cfg.get('tp2_roi', 30)
                tp1_pct = tp1_roi / 100 / leverage
                tp2_pct = tp2_roi / 100 / leverage

                if signal_type == "BUY":
                    tp1 = entry_price * (1 + tp1_pct)
                    tp2 = entry_price * (1 + tp2_pct)
                else:
                    tp1 = entry_price * (1 - tp1_pct)
                    tp2 = entry_price * (1 - tp2_pct)

                logger.info(
                    f"[RSI-DIV-ENTRY] {symbol} [{entry_type}] {signal_type}: "
                    f"{result.description} | TP1=${tp1:.4f} TP2=${tp2:.4f} Lev={leverage}x"
                )

                # Get indicator values for TradingSignal required fields
                indicators = TechnicalIndicators.get_all_indicators(df_closed, use_closed_candle=True)

                results[symbol] = TradingSignal(
                    symbol=symbol,
                    signal_type=signal_type,
                    entry_price=entry_price,
                    timestamp=datetime.now(),
                    h4_trend=signal_type,  # align with divergence direction
                    h4_ema34=indicators.ema34,
                    h4_ema89=indicators.ema89,
                    h1_rsi=indicators.rsi,
                    m15_ema34=indicators.ema34,
                    m15_ema89=indicators.ema89,
                    wick_ratio=0.0,
                    entry_type=entry_type,
                    stop_loss=None,  # Calculated by position_manager
                    take_profit_1=tp1,
                    take_profit_2=tp2,
                    leverage=leverage,
                )

            except Exception as e:
                logger.error(f"[RSI-DIV-ENTRY] Error scanning {symbol} {tf}: {e}")

        return results

    # ── M15 EMA Blocking ──────────────────────────────────────────────

    def set_m15_ema_block(self, symbol: str, blocked_direction: str):
        """Block EMA entries for a symbol in a direction (M15 divergence override).

        Args:
            symbol: Trading pair
            blocked_direction: "BUY" or "SELL" — the direction to block
        """
        self._m15_div_ema_block[symbol] = blocked_direction
        logger.info(f"[RSI-DIV-BLOCK] {symbol}: EMA {blocked_direction} blocked by M15 divergence")

    def check_m15_ema_block(self, symbol: str, direction: str) -> bool:
        """Check if EMA entry is blocked by M15 RSI divergence.

        Returns True if the given direction is blocked for the symbol.
        """
        return self._m15_div_ema_block.get(symbol) == direction

    def update_m15_ema_blocks(self, symbols: list):
        """Clear M15 EMA blocks when RSI resets to neutral (crosses 50).

        - Bearish block (blocks BUY): clear when M15 RSI ≤ 50
        - Bullish block (blocks SELL): clear when M15 RSI ≥ 50
        """
        if not self._m15_div_ema_block:
            return

        to_clear = []
        for symbol, blocked_dir in self._m15_div_ema_block.items():
            try:
                df = self._cache.fetch(symbol, TIMEFRAMES['entry'], 30)
                if df is None or len(df) < 15:
                    continue
                rsi = TechnicalIndicators.calculate_rsi(df['close'], INDICATORS['rsi_period'])
                current_rsi = float(rsi.iloc[-1])

                if blocked_dir == "BUY" and current_rsi <= 50:
                    to_clear.append(symbol)
                    logger.info(f"[RSI-DIV-BLOCK] {symbol}: BUY block cleared (RSI={current_rsi:.1f} ≤ 50)")
                elif blocked_dir == "SELL" and current_rsi >= 50:
                    to_clear.append(symbol)
                    logger.info(f"[RSI-DIV-BLOCK] {symbol}: SELL block cleared (RSI={current_rsi:.1f} ≥ 50)")
            except Exception as e:
                logger.error(f"[RSI-DIV-BLOCK] Error checking {symbol}: {e}")

        for symbol in to_clear:
            del self._m15_div_ema_block[symbol]

    def fetch_top_futures_symbols(self, limit: int = 500) -> List[str]:
        """
        Fetch top futures symbols by 24h quote volume from exchange.

        Note: Using 24h volume as proxy for stability. Exchange API doesn't provide
        multi-day aggregated volume directly, but 24h volume from high-liquidity
        exchanges like OKX is reliable for top pairs selection.
        Top pairs by 24h volume tend to remain stable over 72h period.

        Validation: Only includes symbols that exist as active linear USDT
        perpetual futures in exchange markets data (filters out spot-only,
        delisted, or non-futures pairs like PORT3USDT, LAUSDT, etc.)

        Args:
            limit: Max number of symbols to return

        Returns:
            List of symbol strings (e.g. ['BTCUSDT', 'ETHUSDT', ...])
        """
        try:
            markets = self.client.exchange.load_markets()

            # Build set of valid futures symbols from markets data
            # Only include active linear perpetual USDT futures
            valid_futures = set()
            for mkt_symbol, mkt_info in markets.items():
                if (mkt_info.get('swap', False)           # Is perpetual swap
                    and mkt_info.get('linear', False)     # Linear (USDT-margined)
                    and mkt_info.get('active', False)     # Currently active
                    and mkt_info.get('quote') == 'USDT'): # USDT quote
                    # Convert CCXT format (BTC/USDT:USDT) to plain (BTCUSDT)
                    plain = mkt_info.get('base', '') + 'USDT'
                    valid_futures.add(plain)

            logger.info(f"[PAIRS] Valid active futures on OKX: {len(valid_futures)} pairs")

            # Filter tickers: must be USDT futures AND exist in valid_futures
            futures_pairs = []
            tickers = self.client.exchange.fetch_tickers()
            skipped = []

            for symbol, ticker in tickers.items():
                if '/USDT' in symbol and ':USDT' in symbol:
                    # Convert CCXT symbol (BTC/USDT:USDT) to plain (BTCUSDT)
                    plain = symbol.split('/')[0] + 'USDT'

                    # Validate against actual futures markets
                    if plain not in valid_futures:
                        skipped.append(plain)
                        continue

                    # OKX: quoteVolume is None for swaps, use volCcy24h * last price
                    quote_volume = ticker.get('quoteVolume', 0) or 0
                    if not quote_volume:
                        info = ticker.get('info', {})
                        vol_ccy = float(info.get('volCcy24h', 0) or 0)
                        last_price = float(ticker.get('last', 0) or 0)
                        quote_volume = vol_ccy * last_price
                    futures_pairs.append((plain, quote_volume))

            if skipped:
                logger.debug(f"[PAIRS] Skipped {len(skipped)} non-futures pairs: {skipped[:10]}...")

            # Sort by volume descending
            futures_pairs.sort(key=lambda x: x[1], reverse=True)

            symbols = [pair[0] for pair in futures_pairs[:limit]]
            logger.info(f"[PAIRS] Fetched top {len(symbols)} futures symbols by 24h volume (stable proxy)")
            return symbols

        except Exception as e:
            logger.error(f"Error fetching top futures symbols: {e}")
            return []

    def fetch_top_futures_symbols_multi(self, volume_windows: dict) -> dict:
        """
        Fetch top futures symbols by volume across multiple time windows.

        Args:
            volume_windows: {"24h": 30, "48h": 15, "72h": 10}
                           Values are number of pairs per window (0 = disabled)

        Returns:
            {
                "symbols": ["BTCUSDT", ...],  # merged deduplicated list
                "details": {
                    "BTCUSDT": {"volume_24h": 1234.5, "volume_48h": 2345.6, "volume_72h": 3456.7, "source_windows": ["24h"]},
                    ...
                }
            }
        """
        try:
            markets = self.client.exchange.load_markets()

            # Build set of valid futures symbols
            valid_futures = set()
            for mkt_symbol, mkt_info in markets.items():
                if (mkt_info.get('swap', False)
                    and mkt_info.get('linear', False)
                    and mkt_info.get('active', False)
                    and mkt_info.get('quote') == 'USDT'):
                    plain = mkt_info.get('base', '') + 'USDT'
                    valid_futures.add(plain)

            # Step 1: Get 24h volume from tickers (always needed for base ranking)
            tickers = self.client.exchange.fetch_tickers()
            volume_24h = {}
            ccxt_map = {}  # plain -> CCXT symbol for kline fetching

            for symbol, ticker in tickers.items():
                if '/USDT' in symbol and ':USDT' in symbol:
                    plain = symbol.split('/')[0] + 'USDT'
                    if plain not in valid_futures:
                        continue
                    # OKX: quoteVolume is None for swaps, use volCcy24h * last price
                    vol = ticker.get('quoteVolume', 0) or 0
                    if not vol:
                        info = ticker.get('info', {})
                        vol_ccy = float(info.get('volCcy24h', 0) or 0)
                        last_price = float(ticker.get('last', 0) or 0)
                        vol = vol_ccy * last_price
                    volume_24h[plain] = vol
                    ccxt_map[plain] = symbol

            # Sort by 24h volume
            sorted_24h = sorted(volume_24h.items(), key=lambda x: x[1], reverse=True)

            # Step 2: Compute multi-day volumes if needed
            need_48h = volume_windows.get('48h', 0) > 0
            need_72h = volume_windows.get('72h', 0) > 0
            volume_48h = {}
            volume_72h = {}

            if need_48h or need_72h:
                # Fetch daily klines for top ~200 symbols (covers any reasonable top_n)
                fetch_count = min(200, len(sorted_24h))
                top_symbols = [s for s, _ in sorted_24h[:fetch_count]]
                days_needed = 3 if need_72h else 2

                logger.info(f"[PAIRS] Fetching {days_needed}d klines for {fetch_count} symbols...")

                for plain in top_symbols:
                    ccxt_sym = ccxt_map.get(plain)
                    if not ccxt_sym:
                        continue
                    try:
                        ohlcv = self.client.exchange.fetch_ohlcv(
                            symbol=ccxt_sym,
                            timeframe='1d',
                            limit=days_needed + 1  # +1 for safety (current incomplete day)
                        )
                        if not ohlcv or len(ohlcv) < 2:
                            continue

                        # Each candle: [ts, open, high, low, close, volume]
                        # QuoteVolume ≈ close * volume (approximation)
                        # Skip the last candle if it's the current (incomplete) day
                        daily_vols = []
                        for candle in ohlcv[:-1]:  # exclude current incomplete day
                            quote_vol = candle[4] * candle[5]  # close * base_volume
                            daily_vols.append(quote_vol)

                        # Take last N days
                        if need_48h and len(daily_vols) >= 2:
                            volume_48h[plain] = sum(daily_vols[-2:])
                        if need_72h and len(daily_vols) >= 3:
                            volume_72h[plain] = sum(daily_vols[-3:])

                    except Exception as e:
                        logger.debug(f"[PAIRS] Failed kline for {plain}: {e}")
                        continue

                logger.info(f"[PAIRS] Multi-day volumes: 48h={len(volume_48h)}, 72h={len(volume_72h)} symbols")

            # Step 3: Build per-window rankings
            n_24h = volume_windows.get('24h', 0)
            n_48h = volume_windows.get('48h', 0)
            n_72h = volume_windows.get('72h', 0)

            top_by_24h = [s for s, _ in sorted_24h[:n_24h]] if n_24h > 0 else []

            sorted_48h = sorted(volume_48h.items(), key=lambda x: x[1], reverse=True)
            top_by_48h = [s for s, _ in sorted_48h[:n_48h]] if n_48h > 0 else []

            sorted_72h = sorted(volume_72h.items(), key=lambda x: x[1], reverse=True)
            top_by_72h = [s for s, _ in sorted_72h[:n_72h]] if n_72h > 0 else []

            # Step 4: Merge (union, ordered by 24h volume for consistency)
            all_symbols = set(top_by_24h) | set(top_by_48h) | set(top_by_72h)
            # Order by 24h volume (fallback to 0 for symbols only in 48h/72h)
            merged = sorted(all_symbols, key=lambda s: volume_24h.get(s, 0), reverse=True)

            # Step 5: Build details
            details = {}
            for s in merged:
                source_windows = []
                if s in top_by_24h:
                    source_windows.append("24h")
                if s in top_by_48h:
                    source_windows.append("48h")
                if s in top_by_72h:
                    source_windows.append("72h")
                details[s] = {
                    "volume_24h": round(volume_24h.get(s, 0), 2),
                    "volume_48h": round(volume_48h.get(s, 0), 2) if s in volume_48h else None,
                    "volume_72h": round(volume_72h.get(s, 0), 2) if s in volume_72h else None,
                    "source_windows": source_windows,
                }

            logger.info(
                f"[PAIRS] Multi-window result: 24h={len(top_by_24h)}, "
                f"48h={len(top_by_48h)}, 72h={len(top_by_72h)}, "
                f"merged={len(merged)} unique pairs"
            )

            return {"symbols": merged, "details": details}

        except Exception as e:
            logger.error(f"Error in fetch_top_futures_symbols_multi: {e}")
            return {"symbols": [], "details": {}}
