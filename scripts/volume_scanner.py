#!/usr/bin/env python3
"""
Volume Spike Scanner — Detect tokens with abnormal volume in the last 1H.

Scans all USDT perpetual contracts on OKX, compares current 1H volume
to the 24H average. Flags tokens with volume significantly above average.

Usage:
    python scripts/volume_scanner.py              # Default: top 20, min 2x spike
    python scripts/volume_scanner.py --min 3      # Min 3x volume ratio
    python scripts/volume_scanner.py --top 50     # Show top 50
    python scripts/volume_scanner.py --min-vol 500000  # Min $500K 1H volume
"""

import sys
import os
import time
import argparse
import asyncio
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccxt.async_support as ccxt_async


async def scan_volume_spikes(
    min_ratio: float = 2.0,
    top_n: int = 20,
    min_vol_usd: float = 100_000,
    lookback: int = 24,
):
    """
    Scan all OKX USDT perps for 1H volume spikes.

    Args:
        min_ratio: Minimum volume ratio (current vs average) to flag
        top_n: Number of top results to show
        min_vol_usd: Minimum 1H volume in USD to consider
        lookback: Number of 1H candles for average calculation
    """
    exchange = ccxt_async.okx({
        'enableRateLimit': True,
        'options': {'defaultType': 'swap'},
    })

    try:
        print(f"\n{'='*70}")
        print(f"  Volume Spike Scanner — OKX USDT Perpetuals")
        print(f"  Min ratio: {min_ratio}x | Top: {top_n} | Min vol: ${min_vol_usd:,.0f}")
        print(f"  Lookback: {lookback}H average")
        print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}")

        # Load markets
        print(f"\n  Loading markets...", end="", flush=True)
        markets = await exchange.load_markets()
        # Filter USDT perpetual swaps only
        usdt_perps = [
            s for s, m in markets.items()
            if m.get('swap') and m.get('quote') == 'USDT'
            and m.get('active') and ':USDT' in s
        ]
        print(f" {len(usdt_perps)} USDT perps found")

        # Fetch 1H candles for each symbol
        print(f"  Scanning {len(usdt_perps)} symbols...\n")
        results = []
        errors = 0
        batch_size = 5  # concurrent requests

        for i in range(0, len(usdt_perps), batch_size):
            batch = usdt_perps[i:i + batch_size]
            tasks = []
            for symbol in batch:
                tasks.append(_fetch_and_analyze(exchange, symbol, lookback))

            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, res in enumerate(batch_results):
                if isinstance(res, Exception):
                    errors += 1
                    continue
                if res is not None:
                    results.append(res)

            # Progress
            done = min(i + batch_size, len(usdt_perps))
            pct = done / len(usdt_perps) * 100
            spikes = len([r for r in results if r['ratio'] >= min_ratio])
            print(f"\r  Progress: {done}/{len(usdt_perps)} ({pct:.0f}%) | "
                  f"Spikes found: {spikes}", end="", flush=True)

        print(f"\n\n  Scan complete: {len(results)} symbols analyzed, {errors} errors")

        # Filter and sort
        filtered = [
            r for r in results
            if r['ratio'] >= min_ratio and r['vol_usd'] >= min_vol_usd
        ]
        filtered.sort(key=lambda x: x['ratio'], reverse=True)
        top = filtered[:top_n]

        if not top:
            print(f"\n  No tokens found with volume >= {min_ratio}x average "
                  f"and >= ${min_vol_usd:,.0f}")
            return []

        # Print results
        print(f"\n  {'─'*70}")
        print(f"  TOP {len(top)} VOLUME SPIKES (1H vs {lookback}H avg)")
        print(f"  {'─'*70}")
        print(f"  {'#':<4} {'Symbol':<16} {'Ratio':>7} {'1H Vol':>12} "
              f"{'Avg Vol':>12} {'Price':>12} {'1H Chg':>8}")
        print(f"  {'─'*70}")

        for idx, r in enumerate(top, 1):
            ratio_bar = '█' * min(int(r['ratio']), 20)
            print(f"  {idx:<4} {r['symbol']:<16} {r['ratio']:>6.1f}x "
                  f"${r['vol_usd']:>10,.0f} "
                  f"${r['avg_vol_usd']:>10,.0f} "
                  f"${r['price']:>10.4f} "
                  f"{r['price_chg']:>+7.2f}%")
            print(f"       {ratio_bar}")

        print(f"  {'─'*70}")

        # Summary
        avg_ratio = sum(r['ratio'] for r in top) / len(top)
        total_vol = sum(r['vol_usd'] for r in top)
        print(f"\n  Avg ratio: {avg_ratio:.1f}x | "
              f"Total 1H vol (top {len(top)}): ${total_vol:,.0f}")

        return top

    finally:
        await exchange.close()


async def _fetch_and_analyze(exchange, symbol: str, lookback: int):
    """Fetch candles and compute volume ratio for one symbol."""
    try:
        candles = await exchange.fetch_ohlcv(
            symbol, '1h', limit=lookback + 2
        )
        if not candles or len(candles) < 4:
            return None

        # Last closed candle (index -2, since -1 may be forming)
        current = candles[-2]
        # Average of previous candles (excluding current and forming)
        prev_candles = candles[:-2]
        if len(prev_candles) < 3:
            return None

        current_vol = current[5]  # volume
        avg_vol = sum(c[5] for c in prev_candles) / len(prev_candles)

        if avg_vol <= 0:
            return None

        ratio = current_vol / avg_vol

        # Estimate USD volume (vol × close price)
        current_price = current[4]  # close
        vol_usd = current_vol * current_price
        avg_vol_usd = avg_vol * current_price

        # Price change (current candle open vs close)
        price_chg = ((current[4] - current[1]) / current[1]) * 100 if current[1] > 0 else 0

        # Clean symbol name
        clean_sym = symbol.replace(':USDT', '').replace('/USDT', '')

        return {
            'symbol': clean_sym,
            'ratio': ratio,
            'vol_usd': vol_usd,
            'avg_vol_usd': avg_vol_usd,
            'price': current_price,
            'price_chg': price_chg,
            'current_vol': current_vol,
            'avg_vol': avg_vol,
        }
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description='Volume Spike Scanner')
    parser.add_argument('--min', type=float, default=2.0,
                        help='Minimum volume ratio (default: 2.0)')
    parser.add_argument('--top', type=int, default=20,
                        help='Number of top results (default: 20)')
    parser.add_argument('--min-vol', type=float, default=100_000,
                        help='Minimum 1H volume in USD (default: 100000)')
    parser.add_argument('--lookback', type=int, default=24,
                        help='Hours for average calculation (default: 24)')
    args = parser.parse_args()

    asyncio.run(scan_volume_spikes(
        min_ratio=args.min,
        top_n=args.top,
        min_vol_usd=args.min_vol,
        lookback=args.lookback,
    ))


if __name__ == '__main__':
    main()
