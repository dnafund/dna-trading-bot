"""
Technical Indicators for Futures Trading

Implements EMA, RSI, and other indicators across multiple timeframes
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass

from src.trading.core.models import IndicatorValues, DivergenceResult
from src.trading.core.config import SR_CONFIG

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """Calculate technical indicators"""

    @staticmethod
    def calculate_ema(data: pd.Series, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average (TradingView-compatible).

        Uses SMA of the first `period` values as the seed, then applies
        standard EMA formula from there.  This matches TradingView's
        ta.ema() behaviour and is critical for high-period EMAs (e.g. 610)
        where the seed value dominates for a long time.

        Args:
            data: Price series
            period: EMA period

        Returns:
            EMA series (NaN for the first period-1 values)
        """
        values = data.values.astype(float)
        n = len(values)

        if n < period:
            # Not enough data — fall back to pandas (will be mostly NaN)
            return data.ewm(span=period, adjust=False).mean()

        result = np.full(n, np.nan)
        # Seed = SMA of first `period` values
        result[period - 1] = np.mean(values[:period])

        k = 2.0 / (period + 1)
        for i in range(period, n):
            result[i] = values[i] * k + result[i - 1] * (1 - k)

        return pd.Series(result, index=data.index)

    @staticmethod
    def calculate_rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Relative Strength Index using Wilder's smoothing.

        Args:
            data: Price series
            period: RSI period (default 14)

        Returns:
            RSI series (0-100)
        """
        delta = data.diff()

        # Wilder's smoothing (matches TradingView RSI)
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, min_periods=period, adjust=False).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        return rsi

    @staticmethod
    def calculate_candle_wick_ratio(
        open_price: float,
        high: float,
        low: float,
        close_price: float
    ) -> Tuple[float, float]:
        """
        Calculate upper and lower wick ratios relative to candle range (high - low)

        Args:
            open_price: Open price
            high: High price
            low: Low price
            close_price: Close price

        Returns:
            Tuple of (upper_wick_ratio, lower_wick_ratio) in percentage
        """
        candle_range = high - low

        if candle_range == 0:
            return 0.0, 0.0

        upper_wick = high - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low

        upper_wick_ratio = (upper_wick / candle_range) * 100  # 0-100%
        lower_wick_ratio = (lower_wick / candle_range) * 100  # 0-100%

        return upper_wick_ratio, lower_wick_ratio

    @staticmethod
    def is_bullish_rejection(
        open_price: float,
        high: float,
        low: float,
        close_price: float,
        threshold: float = 30.0
    ) -> bool:
        """
        Check if candle shows bullish rejection (long lower wick)

        Args:
            open_price: Open price
            high: High price
            low: Low price
            close_price: Close price
            threshold: Minimum wick ratio % of candle range (default 30%)

        Returns:
            True if bullish rejection confirmed
        """
        _, lower_wick_ratio = TechnicalIndicators.calculate_candle_wick_ratio(
            open_price, high, low, close_price
        )

        return lower_wick_ratio >= threshold

    @staticmethod
    def is_bearish_rejection(
        open_price: float,
        high: float,
        low: float,
        close_price: float,
        threshold: float = 30.0
    ) -> bool:
        """
        Check if candle shows bearish rejection (long upper wick)

        Args:
            open_price: Open price
            high: High price
            low: Low price
            close_price: Close price
            threshold: Minimum wick ratio % of candle range (default 30%)

        Returns:
            True if bearish rejection confirmed
        """
        upper_wick_ratio, _ = TechnicalIndicators.calculate_candle_wick_ratio(
            open_price, high, low, close_price
        )

        return upper_wick_ratio >= threshold

    @staticmethod
    def get_all_indicators(
        df: pd.DataFrame,
        ema_fast: int = 34,
        ema_slow: int = 89,
        rsi_period: int = 14,
        use_closed_candle: bool = False
    ) -> IndicatorValues:
        """
        Calculate all indicators for a dataframe

        Args:
            df: DataFrame with OHLCV data
            ema_fast: Fast EMA period (default 34)
            ema_slow: Slow EMA period (default 89)
            rsi_period: RSI period (default 14)
            use_closed_candle: If True, use only closed candles (exclude iloc[-1])
                              This prevents repainting in Live Bot (default False for backward compatibility)

        Returns:
            IndicatorValues object with latest values
        """
        if len(df) < max(ema_slow, rsi_period) + 1:
            raise ValueError(f"Need at least {max(ema_slow, rsi_period) + 1} candles")

        # For Live Bot: Remove forming candle to prevent repainting
        # iloc[-1] in Live is the current forming candle (not closed yet)
        # We should use iloc[-2] which is the last CLOSED candle
        if use_closed_candle and len(df) > 0:
            df_calc = df.iloc[:-1]  # Exclude forming candle
        else:
            df_calc = df

        if len(df_calc) < max(ema_slow, rsi_period) + 1:
            raise ValueError(f"Need at least {max(ema_slow, rsi_period) + 1} closed candles")

        # Calculate indicators
        ema34 = TechnicalIndicators.calculate_ema(df_calc['close'], ema_fast)
        ema89 = TechnicalIndicators.calculate_ema(df_calc['close'], ema_slow)
        rsi = TechnicalIndicators.calculate_rsi(df_calc['close'], rsi_period)

        # Calculate S/R levels
        current_price = float(df_calc['close'].iloc[-1])
        swing_highs, swing_lows = SupportResistance.find_swing_highs_lows(df_calc, lookback=1000)

        # Separate into resistance (above price) and support (below price)
        resistances = [h for h in swing_highs if h > current_price]
        supports = [l for l in swing_lows if l < current_price]

        # Return latest values
        return IndicatorValues(
            ema34=float(ema34.iloc[-1]),
            ema89=float(ema89.iloc[-1]),
            rsi=float(rsi.iloc[-1]),
            current_price=current_price,
            timestamp=df_calc.index[-1],
            resistance_levels=resistances if resistances else [],
            support_levels=supports if supports else []
        )


class SupportResistance:
    """Detect support and resistance levels"""

    @staticmethod
    def find_swing_highs_lows(
        df: pd.DataFrame,
        lookback: int = None,
        threshold: float = None
    ) -> Tuple[list, list]:
        """
        Find swing highs and lows in price data

        Uses 5-bar fractal pattern with minimum threshold filter.
        Lookback and threshold default from SR_CONFIG.

        Args:
            df: DataFrame with OHLCV data
            lookback: Number of candles to look back (default from SR_CONFIG)
            threshold: Minimum price change % to qualify as swing (default from SR_CONFIG)

        Returns:
            Tuple of (swing_highs, swing_lows) as lists of prices
        """
        if lookback is None:
            lookback = SR_CONFIG.get('lookback_periods', 1000)
        if threshold is None:
            threshold = SR_CONFIG.get('swing_threshold', 0.02)
        if len(df) < lookback:
            lookback = len(df)

        recent_data = df.tail(lookback)

        swing_highs = []
        swing_lows = []

        # Use numpy arrays to avoid pandas .iloc[] crash on Python 3.14
        highs = recent_data['high'].values
        lows = recent_data['low'].values

        # Use 5-bar fractal for stronger S/R levels (2 bars each side)
        window = 2
        for i in range(window, len(highs) - window):
            # Check for swing high (higher than 2 bars on each side)
            is_swing_high = True
            for j in range(1, window + 1):
                if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                    is_swing_high = False
                    break

            if is_swing_high:
                # Check minimum threshold vs average of neighbors
                avg_neighbor = (highs[i - 1] + highs[i + 1]) / 2
                if avg_neighbor > 0:
                    change_pct = abs(highs[i] - avg_neighbor) / avg_neighbor
                    if change_pct >= threshold:
                        swing_highs.append(highs[i])

            # Check for swing low (lower than 2 bars on each side)
            is_swing_low = True
            for j in range(1, window + 1):
                if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                    is_swing_low = False
                    break

            if is_swing_low:
                avg_neighbor = (lows[i - 1] + lows[i + 1]) / 2
                if avg_neighbor > 0:
                    change_pct = abs(lows[i] - avg_neighbor) / avg_neighbor
                    if change_pct >= threshold:
                        swing_lows.append(lows[i])

        return swing_highs, swing_lows

    @staticmethod
    def find_nearest_resistance(
        current_price: float,
        df: pd.DataFrame,
        lookback: int = 1000
    ) -> Optional[float]:
        """
        Find nearest resistance level above current price

        Args:
            current_price: Current market price
            df: DataFrame with OHLCV data (H1 recommended, 1000 candles)
            lookback: Number of candles to analyze

        Returns:
            Nearest resistance price or None
        """
        swing_highs, _ = SupportResistance.find_swing_highs_lows(df, lookback)

        # Filter highs above current price
        resistances = [h for h in swing_highs if h > current_price]

        if not resistances:
            return None

        # Return nearest (lowest) resistance
        return min(resistances)

    @staticmethod
    def find_nearest_support(
        current_price: float,
        df: pd.DataFrame,
        lookback: int = 1000
    ) -> Optional[float]:
        """
        Find nearest support level below current price

        Args:
            current_price: Current market price
            df: DataFrame with OHLCV data (H1 recommended, 1000 candles)
            lookback: Number of candles to analyze

        Returns:
            Nearest support price or None
        """
        _, swing_lows = SupportResistance.find_swing_highs_lows(df, lookback)

        # Filter lows below current price
        supports = [l for l in swing_lows if l < current_price]

        if not supports:
            return None

        # Return nearest (highest) support
        return max(supports)

    @staticmethod
    def _cluster_levels(levels: list, cluster_pct: float = None) -> List[Tuple[float, int]]:
        """
        Cluster nearby price levels and return (avg_price, touch_count) tuples.

        Levels within cluster_pct of each other are merged.
        Strength = number of fractals merged into cluster.

        Args:
            levels: List of price levels
            cluster_pct: Max % distance to merge (default from SR_CONFIG)

        Returns:
            List of (cluster_avg_price, touch_count) sorted by price
        """
        if not levels:
            return []

        if cluster_pct is None:
            cluster_pct = SR_CONFIG.get('cluster_threshold', 0.005)

        sorted_levels = sorted(levels)
        clusters: List[Tuple[float, int]] = []
        current_cluster = [sorted_levels[0]]

        for level in sorted_levels[1:]:
            cluster_avg = sum(current_cluster) / len(current_cluster)
            if abs(level - cluster_avg) / cluster_avg <= cluster_pct:
                current_cluster.append(level)
            else:
                clusters.append((
                    sum(current_cluster) / len(current_cluster),
                    len(current_cluster)
                ))
                current_cluster = [level]

        # Don't forget the last cluster
        clusters.append((
            sum(current_cluster) / len(current_cluster),
            len(current_cluster)
        ))

        return clusters

    @staticmethod
    def find_strong_support(
        current_price: float,
        df: pd.DataFrame,
        min_distance_pct: float = None,
        min_touches: int = None,
        lookback: int = None
    ) -> Optional[float]:
        """
        Find the nearest STRONG support level below current price.

        Strong = clustered level with >= min_touches, at least min_distance_pct away.
        Falls back to any clustered level if none meet min_touches.
        Returns None if nothing qualifies (caller uses ROI fallback).

        Args:
            current_price: Current/entry price
            df: DataFrame with OHLCV data (H1 recommended)
            min_distance_pct: Minimum distance from entry (default from SR_CONFIG)
            min_touches: Minimum touch count for "strong" (default from SR_CONFIG)
            lookback: Candle lookback (default from SR_CONFIG)

        Returns:
            Strong support price or None
        """
        if min_distance_pct is None:
            min_distance_pct = SR_CONFIG.get('min_tp_distance_pct', 0.01)
        if min_touches is None:
            min_touches = SR_CONFIG.get('min_touches', 2)
        if lookback is None:
            lookback = SR_CONFIG.get('lookback_periods', 1000)

        _, swing_lows = SupportResistance.find_swing_highs_lows(df, lookback)

        supports = [l for l in swing_lows if l < current_price]
        if not supports:
            return None

        clusters = SupportResistance._cluster_levels(supports)

        # Filter: must be at least min_distance_pct below entry
        min_price = current_price * (1 - min_distance_pct)
        valid = [(price, touches) for price, touches in clusters if price <= min_price]

        if not valid:
            logger.debug(f"No support levels >= {min_distance_pct*100:.1f}% from {current_price:.4f}")
            return None

        # Prefer strong levels (>= min_touches), pick nearest
        strong = [(p, t) for p, t in valid if t >= min_touches]

        if strong:
            best = max(strong, key=lambda x: x[0])
            logger.info(f"Strong support: ${best[0]:.4f} ({best[1]} touches)")
            return best[0]

        # Fallback: nearest of any valid clustered level
        best = max(valid, key=lambda x: x[0])
        logger.info(f"Support (weak): ${best[0]:.4f} ({best[1]} touch)")
        return best[0]

    @staticmethod
    def find_strong_resistance(
        current_price: float,
        df: pd.DataFrame,
        min_distance_pct: float = None,
        min_touches: int = None,
        lookback: int = None
    ) -> Optional[float]:
        """
        Find the nearest STRONG resistance level above current price.
        Mirror logic of find_strong_support.

        Args:
            current_price: Current/entry price
            df: DataFrame with OHLCV data (H1 recommended)
            min_distance_pct: Minimum distance from entry (default from SR_CONFIG)
            min_touches: Minimum touch count for "strong" (default from SR_CONFIG)
            lookback: Candle lookback (default from SR_CONFIG)

        Returns:
            Strong resistance price or None
        """
        if min_distance_pct is None:
            min_distance_pct = SR_CONFIG.get('min_tp_distance_pct', 0.01)
        if min_touches is None:
            min_touches = SR_CONFIG.get('min_touches', 2)
        if lookback is None:
            lookback = SR_CONFIG.get('lookback_periods', 1000)

        swing_highs, _ = SupportResistance.find_swing_highs_lows(df, lookback)

        resistances = [h for h in swing_highs if h > current_price]
        if not resistances:
            return None

        clusters = SupportResistance._cluster_levels(resistances)

        # Filter: must be at least min_distance_pct above entry
        max_price = current_price * (1 + min_distance_pct)
        valid = [(price, touches) for price, touches in clusters if price >= max_price]

        if not valid:
            logger.debug(f"No resistance levels >= {min_distance_pct*100:.1f}% from {current_price:.4f}")
            return None

        # Prefer strong levels (>= min_touches), pick nearest
        strong = [(p, t) for p, t in valid if t >= min_touches]

        if strong:
            best = min(strong, key=lambda x: x[0])
            logger.info(f"Strong resistance: ${best[0]:.4f} ({best[1]} touches)")
            return best[0]

        # Fallback: nearest of any valid clustered level
        best = min(valid, key=lambda x: x[0])
        logger.info(f"Resistance (weak): ${best[0]:.4f} ({best[1]} touch)")
        return best[0]


class FibonacciCalculator:
    """Calculate Fibonacci extensions for targets"""

    @staticmethod
    def calculate_fibo_extension(
        df: pd.DataFrame,
        trend: str,
        level: float = 1.618
    ) -> Optional[float]:
        """
        Calculate Fibonacci extension level based on recent trend

        For UPTREND: Uses swing low → swing high
        For DOWNTREND: Uses swing high → swing low

        Args:
            df: DataFrame with OHLCV data (H1 timeframe)
            trend: "BUY_TREND" or "SELL_TREND"
            level: Fibonacci level (default 1.618)

        Returns:
            Fibonacci extension price or None
        """
        swing_highs, swing_lows = SupportResistance.find_swing_highs_lows(df, lookback=1000)

        if trend == "BUY_TREND":
            if not swing_lows or not swing_highs:
                return None

            # Find most recent swing low and high
            swing_low = min(swing_lows[-5:]) if len(swing_lows) >= 5 else min(swing_lows)
            swing_high = max(swing_highs[-5:]) if len(swing_highs) >= 5 else max(swing_highs)

            # Calculate extension
            diff = swing_high - swing_low
            fibo_target = swing_high + (diff * (level - 1))

            return fibo_target

        elif trend == "SELL_TREND":
            if not swing_highs or not swing_lows:
                return None

            # Find most recent swing high and low
            swing_high = max(swing_highs[-5:]) if len(swing_highs) >= 5 else max(swing_highs)
            swing_low = min(swing_lows[-5:]) if len(swing_lows) >= 5 else min(swing_lows)

            # Calculate extension
            diff = swing_high - swing_low
            fibo_target = swing_low - (diff * (level - 1))

            return fibo_target

        return None


class ADXIndicator:
    """Calculate Average Directional Index (ADX) using Wilder's smoothing."""

    @staticmethod
    def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculate ADX using Wilder's smoothing (RMA).
        Matches TradingView's ta.adx() exactly.

        ADX measures trend strength (0-100):
        - < 20: weak/sideways
        - 20-40: trending
        - > 40: strong trend

        Args:
            df: DataFrame with 'high', 'low', 'close' columns
            period: ADX period (default 14)

        Returns:
            ADX series
        """
        high = df['high']
        low = df['low']
        prev_close = df['close'].shift(1)

        # True Range
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # Directional Movement
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = pd.Series(0.0, index=df.index)
        minus_dm = pd.Series(0.0, index=df.index)

        plus_dm[(up_move > down_move) & (up_move > 0)] = up_move
        minus_dm[(down_move > up_move) & (down_move > 0)] = down_move

        # Wilder's smoothing (RMA) — alpha = 1/period
        alpha = 1 / period
        atr = true_range.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        plus_dm_smooth = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
        minus_dm_smooth = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

        # +DI and -DI
        plus_di = 100 * plus_dm_smooth / atr
        minus_di = 100 * minus_dm_smooth / atr

        # DX = |+DI - -DI| / (+DI + -DI) * 100
        di_sum = plus_di + minus_di
        dx = (plus_di - minus_di).abs() / di_sum.replace(0, np.nan) * 100

        # ADX = RMA of DX
        adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

        return adx


class ATRIndicator:
    """Calculate Average True Range and related indicators"""

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Calculate Average True Range using RMA (Wilder's smoothing).

        Matches TradingView's ATR calculation exactly.
        RMA = EMA with alpha = 1/period (Wilder's smoothing method).
        First value is SMA of first `period` true ranges, then RMA thereafter.

        True Range = max(H-L, |H-prevC|, |L-prevC|)

        Args:
            df: DataFrame with 'high', 'low', 'close' columns
            period: ATR period (default 14)

        Returns:
            ATR series (RMA-based, matching TradingView)
        """
        high = df['high']
        low = df['low']
        prev_close = df['close'].shift(1)

        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

        # RMA (Wilder's smoothing) = EMA with alpha = 1/period
        # This matches TradingView's ta.rma() / ta.atr() exactly
        atr = true_range.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

        return atr

    @staticmethod
    def calculate_atr_pinescript(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        ATR with PineScript-exact initialization (SMA seed + Wilder's RMA).

        PineScript ta.rma() behavior:
        - Bars 0 to period-2: NaN
        - Bar period-1: SMA of first `period` true ranges
        - Bar period onwards: alpha * TR + (1 - alpha) * prev_ATR

        Bar 0 TR = high - low (no prev close available, matching PineScript).

        This differs from pandas ewm() which uses the first value as seed
        instead of SMA. The difference cascades through FIFO/overlap decisions
        in Supply & Demand zone detection.

        Used ONLY by SD zones. Chandelier Exit uses calculate_atr().
        """
        high = df['high'].values
        low = df['low'].values
        close = df['close'].values
        n = len(df)

        # True Range — bar 0: H-L (no prev close), bar 1+: standard TR
        tr = np.empty(n)
        tr[0] = high[0] - low[0]  # PineScript: close[1] = na → TR = H-L
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )

        # RMA with SMA seed
        atr = np.full(n, np.nan)
        if n >= period:
            atr[period - 1] = np.mean(tr[:period])  # SMA seed
            alpha = 1.0 / period
            for i in range(period, n):
                atr[i] = alpha * tr[i] + (1.0 - alpha) * atr[i - 1]

        return pd.Series(atr, index=df.index)

    @staticmethod
    def chandelier_exit(
        df: pd.DataFrame,
        period: int = 22,
        multiplier: float = 2.0,
    ) -> Tuple[pd.Series, pd.Series]:
        """
        Calculate Chandelier Exit with trailing ratchet (matches TradingView Everget).

        Raw values:
            Long exit  = Highest Close(period) - multiplier * ATR(period)
            Short exit = Lowest Close(period)  + multiplier * ATR(period)

        Trailing ratchet (TradingView logic):
            - longStop only increases when prev close > prev longStop (uptrend)
            - shortStop only decreases when prev close < prev shortStop (downtrend)
            - Direction flips when close crosses the opposite stop

        Args:
            df: DataFrame with OHLCV data
            period: Lookback period (default 22)
            multiplier: ATR multiplier (default 2.0)

        Returns:
            Tuple of (chandelier_long, chandelier_short) Series
            - chandelier_long: trailing SL for BUY positions (price must stay above)
            - chandelier_short: trailing SL for SELL positions (price must stay below)
        """
        import numpy as np

        atr = ATRIndicator.calculate_atr(df, period)
        close = df['close'].values
        atr_vals = atr.values

        # Raw CE values (useClose=true)
        highest_close = df['close'].rolling(window=period).max().values
        lowest_close = df['close'].rolling(window=period).min().values

        n = len(close)
        long_stop = np.full(n, np.nan)
        short_stop = np.full(n, np.nan)
        direction = np.ones(n, dtype=int)  # 1 = bullish, -1 = bearish

        # Initialize first valid index
        start = period  # Need enough data for rolling + ATR
        if start >= n:
            return pd.Series(long_stop, index=df.index), pd.Series(short_stop, index=df.index)

        long_stop[start] = highest_close[start] - multiplier * atr_vals[start]
        short_stop[start] = lowest_close[start] + multiplier * atr_vals[start]

        for i in range(start + 1, n):
            if np.isnan(atr_vals[i]):
                continue

            raw_long = highest_close[i] - multiplier * atr_vals[i]
            raw_short = lowest_close[i] + multiplier * atr_vals[i]

            # Trailing ratchet: longStop only goes UP during uptrend
            prev_long = long_stop[i - 1] if not np.isnan(long_stop[i - 1]) else raw_long
            if close[i - 1] > prev_long:
                long_stop[i] = max(raw_long, prev_long)
            else:
                long_stop[i] = raw_long

            # Trailing ratchet: shortStop only goes DOWN during downtrend
            prev_short = short_stop[i - 1] if not np.isnan(short_stop[i - 1]) else raw_short
            if close[i - 1] < prev_short:
                short_stop[i] = min(raw_short, prev_short)
            else:
                short_stop[i] = raw_short

            # Direction: close crosses opposite stop → flip
            prev_dir = direction[i - 1]
            if close[i] > short_stop[i - 1] if not np.isnan(short_stop[i - 1]) else False:
                direction[i] = 1
            elif close[i] < long_stop[i - 1] if not np.isnan(long_stop[i - 1]) else False:
                direction[i] = -1
            else:
                direction[i] = prev_dir

        return (
            pd.Series(long_stop, index=df.index),
            pd.Series(short_stop, index=df.index),
        )


class CandlestickPatterns:
    """Detect candlestick patterns for instant entry signals"""

    @staticmethod
    def is_shooting_star(
        open_price: float,
        high: float,
        low: float,
        close_price: float,
        min_upper_wick_ratio: float = 60.0,
        max_lower_wick_ratio: float = 15.0,
    ) -> bool:
        """
        Detect Shooting Star pattern (bearish reversal).

        Characteristics:
        - Long upper wick (>= 60% of candle range)
        - Small or no lower wick (<= 15% of candle range)
        - Small real body near the low of the candle

        Args:
            open_price, high, low, close_price: OHLC values
            min_upper_wick_ratio: Min upper wick as % of range
            max_lower_wick_ratio: Max lower wick as % of range

        Returns:
            True if Shooting Star detected
        """
        candle_range = high - low
        if candle_range == 0:
            return False

        upper_wick = high - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low

        upper_ratio = (upper_wick / candle_range) * 100
        lower_ratio = (lower_wick / candle_range) * 100

        return upper_ratio >= min_upper_wick_ratio and lower_ratio <= max_lower_wick_ratio

    @staticmethod
    def is_hammer(
        open_price: float,
        high: float,
        low: float,
        close_price: float,
        min_lower_wick_ratio: float = 60.0,
        max_upper_wick_ratio: float = 15.0,
    ) -> bool:
        """
        Detect Hammer pattern (bullish reversal).

        Characteristics:
        - Long lower wick (>= 60% of candle range)
        - Small or no upper wick (<= 15% of candle range)
        - Small real body near the high of the candle

        Args:
            open_price, high, low, close_price: OHLC values
            min_lower_wick_ratio: Min lower wick as % of range
            max_upper_wick_ratio: Max upper wick as % of range

        Returns:
            True if Hammer detected
        """
        candle_range = high - low
        if candle_range == 0:
            return False

        upper_wick = high - max(open_price, close_price)
        lower_wick = min(open_price, close_price) - low

        upper_ratio = (upper_wick / candle_range) * 100
        lower_ratio = (lower_wick / candle_range) * 100

        return lower_ratio >= min_lower_wick_ratio and upper_ratio <= max_upper_wick_ratio

    @staticmethod
    def is_evening_star(
        candle1: Dict,
        candle2: Dict,
        candle3: Dict,
        min_body_ratio_c1: float = 50.0,
        max_body_ratio_c2: float = 30.0,
        min_body_ratio_c3: float = 50.0,
    ) -> bool:
        """
        Detect Evening Star (3-candle bearish reversal).

        Pattern:
        - Candle 1: Large bullish body (>= 50% of range)
        - Candle 2: Small body (doji-like, <= 30% of range), gaps up or near top
        - Candle 3: Large bearish body (>= 50% of range), closes below C1 midpoint

        Args:
            candle1, candle2, candle3: dicts with 'open', 'high', 'low', 'close'
            min_body_ratio_c1: Min body % for candle 1
            max_body_ratio_c2: Max body % for candle 2
            min_body_ratio_c3: Min body % for candle 3

        Returns:
            True if Evening Star detected
        """
        o1, h1, l1, c1 = candle1['open'], candle1['high'], candle1['low'], candle1['close']
        o2, h2, l2, c2 = candle2['open'], candle2['high'], candle2['low'], candle2['close']
        o3, h3, l3, c3 = candle3['open'], candle3['high'], candle3['low'], candle3['close']

        range1 = h1 - l1
        range2 = h2 - l2
        range3 = h3 - l3

        if range1 == 0 or range3 == 0:
            return False

        body1 = abs(c1 - o1)
        body2 = abs(c2 - o2) if range2 > 0 else 0
        body3 = abs(c3 - o3)

        body_ratio1 = (body1 / range1) * 100
        body_ratio2 = (body2 / range2) * 100 if range2 > 0 else 0
        body_ratio3 = (body3 / range3) * 100

        # C1 must be bullish (close > open)
        if c1 <= o1:
            return False

        # C3 must be bearish (close < open)
        if c3 >= o3:
            return False

        # Body size checks
        if body_ratio1 < min_body_ratio_c1:
            return False
        if body_ratio2 > max_body_ratio_c2:
            return False
        if body_ratio3 < min_body_ratio_c3:
            return False

        # C3 must close below C1 midpoint
        c1_mid = (o1 + c1) / 2
        if c3 > c1_mid:
            return False

        return True

    @staticmethod
    def is_morning_star(
        candle1: Dict,
        candle2: Dict,
        candle3: Dict,
        min_body_ratio_c1: float = 50.0,
        max_body_ratio_c2: float = 30.0,
        min_body_ratio_c3: float = 50.0,
    ) -> bool:
        """
        Detect Morning Star (3-candle bullish reversal).

        Pattern:
        - Candle 1: Large bearish body (>= 50% of range)
        - Candle 2: Small body (doji-like, <= 30% of range), gaps down or near bottom
        - Candle 3: Large bullish body (>= 50% of range), closes above C1 midpoint

        Args:
            candle1, candle2, candle3: dicts with 'open', 'high', 'low', 'close'

        Returns:
            True if Morning Star detected
        """
        o1, h1, l1, c1 = candle1['open'], candle1['high'], candle1['low'], candle1['close']
        o2, h2, l2, c2 = candle2['open'], candle2['high'], candle2['low'], candle2['close']
        o3, h3, l3, c3 = candle3['open'], candle3['high'], candle3['low'], candle3['close']

        range1 = h1 - l1
        range2 = h2 - l2
        range3 = h3 - l3

        if range1 == 0 or range3 == 0:
            return False

        body1 = abs(c1 - o1)
        body2 = abs(c2 - o2) if range2 > 0 else 0
        body3 = abs(c3 - o3)

        body_ratio1 = (body1 / range1) * 100
        body_ratio2 = (body2 / range2) * 100 if range2 > 0 else 0
        body_ratio3 = (body3 / range3) * 100

        # C1 must be bearish (close < open)
        if c1 >= o1:
            return False

        # C3 must be bullish (close > open)
        if c3 <= o3:
            return False

        # Body size checks
        if body_ratio1 < min_body_ratio_c1:
            return False
        if body_ratio2 > max_body_ratio_c2:
            return False
        if body_ratio3 < min_body_ratio_c3:
            return False

        # C3 must close above C1 midpoint
        c1_mid = (o1 + c1) / 2
        if c3 < c1_mid:
            return False

        return True

    @staticmethod
    def price_pierces_emas(
        candle_high: float,
        candle_low: float,
        ema34: float,
        ema89: float,
        side: str,
    ) -> bool:
        """
        Check if price 'vọt' (pierced) through EMA lines.

        For SELL setup (bearish): candle high pierced above both EMAs
        For BUY setup (bullish): candle low pierced below both EMAs

        Args:
            candle_high, candle_low: High and low of the candle
            ema34, ema89: EMA values
            side: 'SELL' or 'BUY' (the expected trade direction)

        Returns:
            True if price pierced through EMAs
        """
        if side == 'SELL':
            # Price spiked above both EMAs (bearish reversal setup)
            return candle_high > ema34 and candle_high > ema89
        else:
            # Price spiked below both EMAs (bullish reversal setup)
            return candle_low < ema34 and candle_low < ema89


class RSIDivergence:
    """Detect RSI divergence patterns on price data"""

    @staticmethod
    def find_swing_points(
        prices: pd.Series,
        rsi_values: pd.Series,
        swing_type: str,
        window: int = 2,
        min_distance: int = 5,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0
    ) -> List[Tuple[int, float, float]]:
        """
        Find fractal swing highs or lows with corresponding RSI values.
        Collects ALL valid swing points (no RSI threshold filter).
        RSI extreme check is done later at pair comparison level:
        only the FIRST (older) swing needs to be in extreme zone.

        Args:
            prices: Price series (high for swing highs, low for swing lows)
            rsi_values: RSI series aligned with prices
            swing_type: "high" or "low"
            window: Fractal window (2 = 5-bar pattern)
            min_distance: Minimum bars between swing points
            rsi_overbought: RSI threshold (unused here, kept for API compat)
            rsi_oversold: RSI threshold (unused here, kept for API compat)

        Returns:
            List of (index, price_value, rsi_value) sorted by index
        """
        swings = []
        price_arr = prices.values
        rsi_arr = rsi_values.values

        for i in range(window, len(price_arr) - window):
            if np.isnan(rsi_arr[i]):
                continue

            rsi_val = float(rsi_arr[i])

            if swing_type == "high":
                is_swing = all(
                    price_arr[i] > price_arr[i - j] and
                    price_arr[i] > price_arr[i + j]
                    for j in range(1, window + 1)
                )
            else:
                is_swing = all(
                    price_arr[i] < price_arr[i - j] and
                    price_arr[i] < price_arr[i + j]
                    for j in range(1, window + 1)
                )

            if is_swing:
                if swings and (i - swings[-1][0]) < min_distance:
                    continue
                swings.append((i, float(price_arr[i]), rsi_val))

        return swings

    @staticmethod
    def detect(
        df: pd.DataFrame,
        timeframe: str,
        lookback: int = 80,
        rsi_period: int = 14,
        swing_window: int = 3,
        min_swing_distance: int = 8,
        max_swing_pairs: int = 3,
        min_retracement_pct: float = 1.5
    ) -> DivergenceResult:
        """
        Detect RSI divergence on given OHLCV dataframe.

        Checks recent swing highs for bearish/hidden-bearish divergence,
        and recent swing lows for bullish/hidden-bullish divergence.

        Args:
            df: DataFrame with OHLCV data
            timeframe: "H1" or "H4" (for labeling)
            lookback: Number of candles to analyze
            rsi_period: RSI calculation period
            swing_window: Fractal window size
            min_swing_distance: Min bars between swings
            max_swing_pairs: Number of recent swing pairs to check

        Returns:
            DivergenceResult
        """
        no_divergence = DivergenceResult(
            has_divergence=False,
            divergence_type=None,
            timeframe=timeframe,
            description="No divergence detected"
        )

        if len(df) < rsi_period + swing_window * 2:
            return no_divergence

        if len(df) < lookback:
            lookback = len(df)

        recent = df.tail(lookback).reset_index(drop=False)
        # Keep timestamps: index name is 'timestamp' (from klines) or 'open_time' might be a column
        _ts_col = 'timestamp' if 'timestamp' in recent.columns else ('open_time' if 'open_time' in recent.columns else None)
        recent = recent.reset_index(drop=True)  # numeric index for swing detection

        close = recent['close']
        high = recent['high']
        low = recent['low']

        # Use pre-computed RSI if available, otherwise calculate
        if 'rsi' in recent.columns and not recent['rsi'].isna().all():
            rsi = recent['rsi']
        else:
            rsi = TechnicalIndicators.calculate_rsi(close, rsi_period)

        # Find swing highs (using high prices) for bearish divergence checks
        swing_highs = RSIDivergence.find_swing_points(
            high, rsi, "high", swing_window, min_swing_distance
        )

        # Find swing lows (using low prices) for bullish divergence checks
        swing_lows = RSIDivergence.find_swing_points(
            low, rsi, "low", swing_window, min_swing_distance
        )

        # RSI thresholds (same as find_swing_points defaults)
        rsi_overbought = 70.0
        rsi_oversold = 30.0

        # Check if divergence is resolved: current RSI outside extreme zone
        # If RSI exits then re-enters extreme zone → divergence still active (new swing forming)
        current_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        def _divergence_resolved(zone: str) -> bool:
            """
            Divergence resolved when current RSI crosses neutral (50).
            zone='oversold': resolved if current RSI > 50 (bullish div played out)
            zone='overbought': resolved if current RSI < 50 (bearish div played out)
            Using 50 instead of 70/30 prevents premature resolution —
            e.g. HYPE H1 RSI at 63 would kill valid bearish div with old threshold.
            """
            if zone == 'oversold':
                return current_rsi > 50
            if zone == 'overbought':
                return current_rsi < 50
            return False

        def _get_time(idx: int) -> str | None:
            """Get timestamp string from swing index."""
            if not _ts_col or idx >= len(recent):
                return None
            val = recent[_ts_col].iloc[idx]
            return str(val) if pd.notna(val) else None

        def _rsi_reset_between(idx1: int, idx2: int, swing_type: str, reset_level: float = 50.0) -> bool:
            """Check if RSI crossed neutral level between two swing points.

            For bearish (swing highs): reset if RSI drops below reset_level between peaks.
            For bullish (swing lows): reset if RSI rises above reset_level between troughs.

            If RSI resets, the two swings belong to different cycles → not a valid divergence pair.
            """
            if idx2 <= idx1 + 1:
                return False

            between_rsi = rsi.iloc[idx1 + 1:idx2].dropna()
            if len(between_rsi) == 0:
                return False

            if swing_type == "high":
                return float(between_rsi.min()) < reset_level
            else:
                return float(between_rsi.max()) > reset_level

        def _has_retracement(idx1: int, idx2: int, swing_type: str) -> bool:
            """Check if price has minimum retracement between two swing points.

            For swing highs: lowest low between them must be min_retracement_pct% below the lower peak.
            For swing lows: highest high between them must be min_retracement_pct% above the higher trough.
            """
            if min_retracement_pct <= 0 or idx2 <= idx1 + 1:
                return True

            between_start = idx1 + 1
            between_end = idx2

            if swing_type == "high":
                min_low = float(low.iloc[between_start:between_end].min())
                ref_price = min(high.iloc[idx1], high.iloc[idx2])
                retracement = (ref_price - min_low) / ref_price * 100
            else:
                max_high = float(high.iloc[between_start:between_end].max())
                ref_price = max(low.iloc[idx1], low.iloc[idx2])
                retracement = (max_high - ref_price) / ref_price * 100

            return retracement >= min_retracement_pct

        # Check swing highs: bearish + hidden bearish
        if len(swing_highs) >= 2:
            pairs_to_check = min(max_swing_pairs, len(swing_highs) - 1)
            for i in range(1, pairs_to_check + 1):
                prev = swing_highs[-1 - i]
                curr = swing_highs[-1]

                prev_price, prev_rsi = prev[1], prev[2]
                curr_price, curr_rsi = curr[1], curr[2]

                # Skip if current RSI already exited overbought → divergence resolved
                if _divergence_resolved('overbought'):
                    continue

                # Skip if no clear retracement between peaks (sideways noise)
                if not _has_retracement(prev[0], curr[0], "high"):
                    continue

                # Skip if RSI reset to neutral between peaks (different cycle)
                if _rsi_reset_between(prev[0], curr[0], "high"):
                    continue

                # Regular bearish: Price HH, RSI LH
                # Only OLDER swing needs RSI in overbought zone (≥70)
                # Newer swing can be anywhere — divergence = RSI failing to confirm price
                if curr_price > prev_price and curr_rsi < prev_rsi and prev_rsi >= rsi_overbought:
                    return DivergenceResult(
                        has_divergence=True,
                        divergence_type="bearish",
                        timeframe=timeframe,
                        description=f"Bearish divergence {timeframe}: Price HH ({prev_price:.4f}->{curr_price:.4f}) RSI LH ({prev_rsi:.1f}->{curr_rsi:.1f})",
                        price_swing_1=prev_price,
                        price_swing_2=curr_price,
                        rsi_swing_1=prev_rsi,
                        rsi_swing_2=curr_rsi,
                        time_swing_1=_get_time(prev[0]),
                        time_swing_2=_get_time(curr[0]),
                        blocks_direction="BUY"
                    )

                # Hidden bearish: Price LH, RSI HH — DISABLED (too noisy)
                # if curr_price < prev_price and curr_rsi > prev_rsi:

        # Check swing lows: bullish + hidden bullish
        if len(swing_lows) >= 2:
            pairs_to_check = min(max_swing_pairs, len(swing_lows) - 1)
            for i in range(1, pairs_to_check + 1):
                prev = swing_lows[-1 - i]
                curr = swing_lows[-1]

                prev_price, prev_rsi = prev[1], prev[2]
                curr_price, curr_rsi = curr[1], curr[2]

                # Skip if current RSI already exited oversold → divergence resolved
                if _divergence_resolved('oversold'):
                    continue

                # Skip if no clear retracement between troughs (sideways noise)
                if not _has_retracement(prev[0], curr[0], "low"):
                    continue

                # Skip if RSI reset to neutral between troughs (different cycle)
                if _rsi_reset_between(prev[0], curr[0], "low"):
                    continue

                # Regular bullish: Price LL, RSI HL
                # Only OLDER swing needs RSI in oversold zone (≤30)
                if curr_price < prev_price and curr_rsi > prev_rsi and prev_rsi <= rsi_oversold:
                    return DivergenceResult(
                        has_divergence=True,
                        divergence_type="bullish",
                        timeframe=timeframe,
                        description=f"Bullish divergence {timeframe}: Price LL ({prev_price:.4f}->{curr_price:.4f}) RSI HL ({prev_rsi:.1f}->{curr_rsi:.1f})",
                        price_swing_1=prev_price,
                        price_swing_2=curr_price,
                        rsi_swing_1=prev_rsi,
                        rsi_swing_2=curr_rsi,
                        time_swing_1=_get_time(prev[0]),
                        time_swing_2=_get_time(curr[0]),
                        blocks_direction="SELL"
                    )

                # Hidden bullish: Price HL, RSI LL — DISABLED (too noisy)
                # if curr_price > prev_price and curr_rsi < prev_rsi:

        return no_divergence

    @staticmethod
    def check_wick_rejection(
        df: pd.DataFrame,
        side: str,
        min_ratio: float = 0.5,
        min_candles: int = 2,
        lookback: int = 4
    ) -> bool:
        """Check if recent candles show wick rejection confirming divergence.

        For BUY (bullish div): lower wicks show buying rejection.
        For SELL (bearish div): upper wicks show selling rejection.

        Args:
            df: OHLCV DataFrame
            side: "BUY" or "SELL"
            min_ratio: Minimum wick/range ratio (0.5 = 50%)
            min_candles: Minimum candles with rejection (2 of 4)
            lookback: Number of recent candles to check

        Returns:
            True if at least min_candles have rejection wicks >= min_ratio
        """
        if len(df) < lookback:
            return True  # Not enough data, don't block

        recent = df.tail(lookback)
        rejection_count = 0

        for _, candle in recent.iterrows():
            o = float(candle['open'])
            h = float(candle['high'])
            l = float(candle['low'])
            c = float(candle['close'])
            candle_range = h - l
            if candle_range <= 0:
                continue

            if side == "BUY":
                # Bullish: lower wick = rejection of lower prices
                wick = min(o, c) - l
            else:
                # Bearish: upper wick = rejection of higher prices
                wick = h - max(o, c)

            if wick / candle_range >= min_ratio:
                rejection_count += 1

        return rejection_count >= min_candles
