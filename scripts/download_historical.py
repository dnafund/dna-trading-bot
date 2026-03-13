"""
Download historical OHLCV data from Binance Futures for backtesting.

Saves Parquet files to data/ohlcv/ for instant backtest loading.

Usage:
    python scripts/download_historical.py                     # Download all
    python scripts/download_historical.py --symbols BTCUSDT   # Specific symbols
    python scripts/download_historical.py --update            # Append new candles only
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trading.exchanges.binance import BinanceFuturesClient
from src.trading.backtest.engine import fetch_full_ohlcv

OHLCV_DIR = PROJECT_ROOT / "data" / "ohlcv"

# Earliest available data on Binance Futures (approximate listing dates)
SYMBOL_START_DATES = {
    "BTCUSDT": "2019-09-01",
    "ETHUSDT": "2019-09-01",
    "SOLUSDT": "2020-09-01",
}

TIMEFRAMES = ["15m", "1h", "4h"]


def _parquet_path(symbol: str, timeframe: str) -> Path:
    return OHLCV_DIR / f"{symbol}_{timeframe}.parquet"


def _load_existing(path: Path) -> pd.DataFrame:
    if path.exists():
        df = pd.read_parquet(path)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        return df
    return pd.DataFrame()


def download_symbol(
    client: BinanceFuturesClient,
    symbol: str,
    start_date: str,
    update_only: bool = False,
):
    """Download all timeframes for a single symbol."""
    for tf in TIMEFRAMES:
        path = _parquet_path(symbol, tf)
        since = datetime.strptime(start_date, "%Y-%m-%d")

        if update_only:
            existing = _load_existing(path)
            if not existing.empty:
                last_ts = existing.index[-1].to_pydatetime()
                candle_count = len(existing)
                print(f"  {symbol} {tf}: {candle_count} cached candles, last={last_ts.date()}")
                since = last_ts  # fetch from last cached candle onward
            else:
                print(f"  {symbol} {tf}: no cache, full download")

        until = datetime.now()
        print(f"  {symbol} {tf}: fetching {since.date()} → {until.date()}...", end=" ", flush=True)

        t0 = time.time()
        df_new = fetch_full_ohlcv(client, symbol, tf, since, until)

        if df_new.empty:
            print("no data returned")
            continue

        # Merge with existing if updating
        if update_only:
            existing = _load_existing(path)
            if not existing.empty:
                df_merged = pd.concat([existing, df_new])
                df_merged = df_merged[~df_merged.index.duplicated(keep="last")]
                df_merged = df_merged.sort_index()
                df_new = df_merged

        # Save
        OHLCV_DIR.mkdir(parents=True, exist_ok=True)
        df_new.to_parquet(path, engine="pyarrow")

        elapsed = time.time() - t0
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"{len(df_new)} candles, {size_mb:.1f}MB, {elapsed:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="Download historical OHLCV data")
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=list(SYMBOL_START_DATES.keys()),
        help="Symbols to download (default: BTCUSDT ETHUSDT SOLUSDT)",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Only fetch new candles since last cached data",
    )
    args = parser.parse_args()

    print("Initializing Binance Futures client...")
    client = BinanceFuturesClient()

    total_start = time.time()
    for symbol in args.symbols:
        start_date = SYMBOL_START_DATES.get(symbol, "2020-01-01")
        print(f"\n{'='*50}")
        print(f"Downloading {symbol} (from {start_date})")
        print(f"{'='*50}")
        download_symbol(client, symbol, start_date, update_only=args.update)

    elapsed = time.time() - total_start
    print(f"\nDone! Total time: {elapsed:.0f}s")

    # Summary
    print(f"\nFiles in {OHLCV_DIR}/:")
    for f in sorted(OHLCV_DIR.glob("*.parquet")):
        size_mb = f.stat().st_size / (1024 * 1024)
        df = pd.read_parquet(f)
        print(f"  {f.name}: {len(df)} candles, {size_mb:.1f}MB, {df.index[0].date()} → {df.index[-1].date()}")


if __name__ == "__main__":
    main()
