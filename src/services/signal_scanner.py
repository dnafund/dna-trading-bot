"""
Signal Scanner — standalone process that scans for trading signals.

Extracts signal detection from FuturesTradingBot into independent service.
Publishes detected signals to Redis pub/sub for consumption by TradeExecutor.

Usage:
    python -m src.services.signal_scanner

Architecture:
    SignalScanner (1 process) → Redis pub/sub → TradeExecutor (per-user)
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.trading.exchanges.okx import OKXFuturesClient
from src.trading.strategy.signal_detector import SignalDetector
from src.trading.core.config import (
    DEFAULT_SYMBOLS,
    UPDATE_INTERVALS,
    DYNAMIC_PAIRS,
    EMA610_ENTRY,
)
from src.trading.core.indicators import TechnicalIndicators
from src.services.redis_client import publish_signal, get_redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/signal_scanner.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class SignalScanner:
    """Standalone signal scanning process.

    Scans all symbols for trading signals and publishes them to Redis.
    No user data, no API keys, no position management.
    """

    def __init__(self):
        api_key = os.getenv("OKX_API_KEY", "")
        api_secret = os.getenv("OKX_API_SECRET", "")
        passphrase = os.getenv("OKX_PASSPHRASE", "")

        self.exchange = OKXFuturesClient(api_key, api_secret, passphrase)
        self.signal_detector = SignalDetector(self.exchange)
        self.symbols = list(DEFAULT_SYMBOLS)
        self.is_running = False

        # Timing
        self._last_signal_scan = 0
        self._last_ema610_scan = 0
        self._last_pairs_refresh = 0
        self._startup_scans_skipped = 0
        self._startup_cooldown = 1  # Skip first scan

        # EMA610 candle dedup
        self._last_ema610_h1_candle: dict[str, str] = {}
        self._last_ema610_h4_candle: dict[str, str] = {}

        # Config hot-reload
        self._config_file = PROJECT_ROOT / "data" / "config.json"
        self._last_config_mtime = 0
        self._last_config_check = 0

        logger.info(f"[SCANNER] Initialized with {len(self.symbols)} symbols")

    async def start(self) -> None:
        """Main scanning loop — runs forever, publishes signals to Redis."""
        self.is_running = True
        logger.info("[SCANNER] Signal Scanner started")

        # Verify Redis connection
        try:
            r = get_redis()
            r.ping()
            logger.info("[SCANNER] Redis connection verified")
        except Exception as e:
            logger.error(f"[SCANNER] Redis connection failed: {e}")
            raise

        try:
            while self.is_running:
                current_time = time.time()

                # Config reload (every 5s)
                if current_time - self._last_config_check >= 5:
                    self._check_config_reload()
                    self._last_config_check = current_time

                # Refresh trading pairs (every 30m)
                pairs_interval = DYNAMIC_PAIRS.get("refresh_interval", 1800)
                if current_time - self._last_pairs_refresh >= pairs_interval:
                    self._refresh_pairs()
                    self._last_pairs_refresh = current_time

                # Scan standard signals (every 60s)
                scan_interval = UPDATE_INTERVALS.get("market_data", 60)
                if current_time - self._last_signal_scan >= scan_interval:
                    await self._scan_standard_signals()
                    self._last_signal_scan = current_time

                # Scan EMA610 signals (every 60s)
                if (
                    EMA610_ENTRY.get("enabled", True)
                    and current_time - self._last_ema610_scan >= scan_interval
                ):
                    await self._scan_ema610_signals()
                    self._last_ema610_scan = current_time

                # Calculate next task time
                next_times = [
                    self._last_config_check + 5,
                    self._last_pairs_refresh + DYNAMIC_PAIRS.get('refresh_interval', 1800),
                    self._last_signal_scan + scan_interval,
                ]
                if EMA610_ENTRY.get('enabled', True):
                    next_times.append(self._last_ema610_scan + scan_interval)
                sleep_until = min(next_times)
                sleep_duration = max(0.1, sleep_until - time.time())
                await asyncio.sleep(min(sleep_duration, 1))

        except KeyboardInterrupt:
            logger.info("[SCANNER] Stopped by user")
        except Exception as e:
            logger.error(f"[SCANNER] Fatal error: {e}", exc_info=True)
        finally:
            self.is_running = False
            logger.info("[SCANNER] Signal Scanner stopped")

    async def _scan_standard_signals(self) -> None:
        """Scan all symbols for standard (H4→H1→M15) signals."""
        if self._startup_scans_skipped < self._startup_cooldown:
            self._startup_scans_skipped += 1
            logger.info(
                f"[SCANNER] Startup cooldown: scan "
                f"{self._startup_scans_skipped}/{self._startup_cooldown}"
            )
            return

        logger.info(f"[SCANNER] Scanning {len(self.symbols)} symbols...")
        signals = self.signal_detector.scan_for_signals(self.symbols)

        for symbol, signal in signals.items():
            if signal is not None:
                self._publish_signal(signal)

    async def _scan_ema610_signals(self) -> None:
        """Scan all symbols for EMA610 touch entries (H1 + H4)."""
        if self._startup_scans_skipped < self._startup_cooldown:
            return

        tolerance = EMA610_ENTRY.get("tolerance", 0.002)

        for symbol in self.symbols:
            try:
                # H1 EMA610 scan
                signal_h1 = self._check_ema610_touch(
                    symbol, "1H", tolerance, self._last_ema610_h1_candle, "ema610_h1"
                )
                if signal_h1:
                    self._publish_signal_dict(signal_h1)

                # H4 EMA610 scan
                signal_h4 = self._check_ema610_touch(
                    symbol, "4H", tolerance, self._last_ema610_h4_candle, "ema610_h4"
                )
                if signal_h4:
                    self._publish_signal_dict(signal_h4)

            except Exception as e:
                logger.debug(f"[SCANNER] EMA610 scan error for {symbol}: {e}")

    def _check_ema610_touch(
        self,
        symbol: str,
        timeframe: str,
        tolerance: float,
        candle_cache: dict,
        entry_type: str,
    ) -> Optional[dict]:
        """Check if price touches EMA610 on given timeframe.

        Returns signal dict or None.
        """
        try:
            df = self.exchange.fetch_ohlcv(symbol, timeframe, limit=620)
            if df is None or len(df) < 610:
                return None

            ema610 = TechnicalIndicators.ema(df["close"], 610)
            if ema610 is None or len(ema610) == 0:
                return None

            ema610_val = float(ema610.iloc[-1])
            last_candle = df.iloc[-1]
            candle_ts = str(last_candle.name)

            # Dedup: skip if same candle already processed
            if candle_cache.get(symbol) == candle_ts:
                return None

            high = float(last_candle["high"])
            low = float(last_candle["low"])
            close = float(last_candle["close"])
            open_price = float(last_candle["open"])

            # Check touch: wick must touch EMA610
            touch_zone = ema610_val * tolerance
            touched = low <= ema610_val + touch_zone and high >= ema610_val - touch_zone

            if not touched:
                return None

            # Determine direction
            ema34 = TechnicalIndicators.ema(df["close"], 34)
            ema89 = TechnicalIndicators.ema(df["close"], 89)

            if ema34 is None or ema89 is None:
                return None

            ema34_val = float(ema34.iloc[-1])
            ema89_val = float(ema89.iloc[-1])

            # BUY: EMA34 > EMA89 (uptrend) + close above EMA610
            if ema34_val > ema89_val and close > ema610_val:
                side = "BUY"
            # SELL: EMA34 < EMA89 (downtrend) + close below EMA610
            elif ema34_val < ema89_val and close < ema610_val:
                side = "SELL"
            else:
                return None

            candle_cache[symbol] = candle_ts

            return {
                "symbol": symbol,
                "signal_type": side,
                "entry_price": close,
                "entry_type": entry_type,
                "timeframe": timeframe,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "ema610": ema610_val,
                "ema34": ema34_val,
                "ema89": ema89_val,
            }

        except Exception as e:
            logger.debug(f"[SCANNER] EMA610 {timeframe} check error {symbol}: {e}")
            return None

    def _publish_signal(self, signal) -> None:
        """Convert TradingSignal dataclass to dict and publish to Redis."""
        signal_data = {
            "symbol": signal.symbol,
            "signal_type": signal.signal_type,
            "entry_price": signal.entry_price,
            "entry_type": getattr(signal, "entry_type", "standard"),
            "timeframe": "M15",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "take_profit_1": getattr(signal, "take_profit_1", None),
            "take_profit_2": getattr(signal, "take_profit_2", None),
            "h4_trend": getattr(signal, "h4_trend", None),
            "h1_rsi": getattr(signal, "h1_rsi", None),
            "m15_ema34": getattr(signal, "m15_ema34", None),
            "m15_ema89": getattr(signal, "m15_ema89", None),
            "wick_ratio": getattr(signal, "wick_ratio", None),
            "leverage": getattr(signal, "leverage", 5),
        }
        self._publish_signal_dict(signal_data)

    def _publish_signal_dict(self, signal_data: dict) -> None:
        """Publish signal dict to Redis."""
        symbol = signal_data["symbol"]
        publish_signal(symbol, signal_data)
        logger.info(
            f"[SCANNER] Published: {signal_data['signal_type']} {symbol} "
            f"@ ${signal_data['entry_price']:.2f} ({signal_data['entry_type']})"
        )

        # Also store in DB for historical tracking
        self._store_signal_db(signal_data)

    def _store_signal_db(self, signal_data: dict) -> None:
        """Store signal in PostgreSQL for history."""
        try:
            from src.database.connection import get_session
            from src.database.models import Signal

            with get_session() as session:
                sig = Signal(
                    symbol=signal_data["symbol"],
                    signal_type=signal_data["signal_type"],
                    entry_price=signal_data["entry_price"],
                    entry_type=signal_data.get("entry_type", "standard"),
                    timeframe=signal_data.get("timeframe", "M15"),
                    data={
                        k: v
                        for k, v in signal_data.items()
                        if k not in ("symbol", "signal_type", "entry_price", "entry_type", "timeframe")
                    },
                )
                session.add(sig)
        except Exception as e:
            logger.debug(f"[SCANNER] Failed to store signal in DB: {e}")

    def _refresh_pairs(self) -> None:
        """Refresh trading pairs (dynamic or static)."""
        if not DYNAMIC_PAIRS.get("enabled", False):
            return

        try:
            volume_windows = DYNAMIC_PAIRS.get("volume_windows", {})
            max_pairs = DYNAMIC_PAIRS.get("max_pairs", 30)
            min_volume = DYNAMIC_PAIRS.get("min_24h_volume", 50_000_000)

            # Fetch top pairs by volume from exchange
            tickers = self.exchange.fetch_tickers()
            if not tickers:
                return

            pairs_with_volume = []
            for sym, ticker in tickers.items():
                if not sym.endswith("USDT"):
                    continue
                vol_24h = float(ticker.get("quoteVolume", 0) or 0)
                if vol_24h >= min_volume:
                    pairs_with_volume.append((sym, vol_24h))

            pairs_with_volume.sort(key=lambda x: x[1], reverse=True)
            new_symbols = [p[0] for p in pairs_with_volume[:max_pairs]]

            if new_symbols:
                self.symbols = new_symbols
                logger.info(
                    f"[SCANNER] Refreshed pairs: {len(self.symbols)} symbols"
                )

        except Exception as e:
            logger.error(f"[SCANNER] Pair refresh failed: {e}")

    def _check_config_reload(self) -> None:
        """Check if config.json changed and reload."""
        try:
            if not self._config_file.exists():
                return
            mtime = self._config_file.stat().st_mtime
            if mtime > self._last_config_mtime:
                with open(self._config_file, "r", encoding="utf-8") as f:
                    new_config = json.load(f)

                from src.trading.core.config import (
                    LEVERAGE,
                    RISK_MANAGEMENT,
                    TAKE_PROFIT,
                    TRAILING_SL,
                    CHANDELIER_EXIT,
                    SMART_SL,
                    EMA610_ENTRY as _ema610,
                    EMA610_EXIT,
                    DIVERGENCE_CONFIG,
                    DYNAMIC_PAIRS as _dp,
                    UPDATE_INTERVALS as _ui,
                )

                config_map = {
                    "LEVERAGE": LEVERAGE,
                    "RISK_MANAGEMENT": RISK_MANAGEMENT,
                    "TAKE_PROFIT": TAKE_PROFIT,
                    "TRAILING_SL": TRAILING_SL,
                    "CHANDELIER_EXIT": CHANDELIER_EXIT,
                    "SMART_SL": SMART_SL,
                    "EMA610_ENTRY": _ema610,
                    "EMA610_EXIT": EMA610_EXIT,
                    "DIVERGENCE_CONFIG": DIVERGENCE_CONFIG,
                    "DYNAMIC_PAIRS": _dp,
                    "UPDATE_INTERVALS": _ui,
                }

                for section_name, config_obj in config_map.items():
                    if section_name in new_config:
                        config_obj.update(new_config[section_name])

                self._last_config_mtime = mtime
                logger.info("[SCANNER] Config reloaded")

        except Exception as e:
            logger.error(f"[SCANNER] Config reload failed: {e}")


async def main() -> None:
    scanner = SignalScanner()
    await scanner.start()


if __name__ == "__main__":
    asyncio.run(main())
