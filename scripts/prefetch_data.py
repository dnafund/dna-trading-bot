"""Pre-download OHLCV data for backtesting.

Usage:
    python -m scripts.prefetch_data
    python -m scripts.prefetch_data --symbols BTCUSDT ETHUSDT --days 90
"""
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# Add project root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.trading.backtest.engine import fetch_full_ohlcv, load_cached_ohlcv
from src.trading.exchanges.binance import BinanceFuturesClient
import pandas as pd
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
DEFAULT_TIMEFRAMES = ["5m", "15m", "1h", "4h"]
CACHE_DIR = ROOT / "data" / "ohlcv"


def prefetch(symbols: list, timeframes: list, days: int = 120):
    """Download and cache OHLCV data for given symbols and timeframes."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    client = BinanceFuturesClient()

    since = datetime.now() - timedelta(days=days + 120)  # extra warmup
    until = datetime.now()

    total = len(symbols) * len(timeframes)
    done = 0

    for symbol in symbols:
        for tf in timeframes:
            done += 1
            cache_file = CACHE_DIR / f"{symbol}_{tf}.parquet"

            logger.info(f"[{done}/{total}] Fetching {symbol} {tf}...")

            try:
                df = fetch_full_ohlcv(client, symbol, tf, since, until)
                if df is not None and not df.empty:
                    df.to_parquet(cache_file)
                    logger.info(
                        f"  Saved {len(df)} candles -> {cache_file.name} "
                        f"({df.index[0].date()} to {df.index[-1].date()})"
                    )
                else:
                    logger.warning(f"  No data returned for {symbol} {tf}")
            except Exception as e:
                logger.error(f"  Failed: {e}")

    logger.info(f"\nDone! {done} files cached in {CACHE_DIR}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-download OHLCV data")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES)
    parser.add_argument("--days", type=int, default=120)
    args = parser.parse_args()

    prefetch(args.symbols, args.timeframes, args.days)
