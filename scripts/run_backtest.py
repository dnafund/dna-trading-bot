"""
Standalone backtest runner — run in any terminal, independent of Claude Code.

Usage:
    python scripts/run_backtest.py                          # default: BTCUSDT, full year
    python scripts/run_backtest.py --symbols BTCUSDT ETHUSDT
    python scripts/run_backtest.py --start 2025-06-01 --end 2025-12-31
    python scripts/run_backtest.py --no-divergence
"""

import sys
import argparse
import logging
from pathlib import Path
from collections import Counter

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.trading.backtest.engine import FuturesBacktester


def main():
    parser = argparse.ArgumentParser(description='EMA Trading Bot — Backtest Runner')
    parser.add_argument('--symbols', nargs='+', default=['BTCUSDT'], help='Symbols to backtest')
    parser.add_argument('--start', default='2025-01-01', help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', default='2025-12-31', help='End date (YYYY-MM-DD)')
    parser.add_argument('--balance', type=float, default=10000, help='Initial balance')
    parser.add_argument('--no-divergence', action='store_true', help='Disable divergence detection')
    parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed logs')
    args = parser.parse_args()

    level = logging.INFO if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger("src.trading").setLevel(level)

    print(f"{'='*60}")
    print(f"  EMA Trading Bot — Backtest")
    print(f"  Symbols:    {', '.join(args.symbols)}")
    print(f"  Period:     {args.start} -> {args.end}")
    print(f"  Balance:    ${args.balance:,.0f}")
    print(f"  Divergence: {'ON' if not args.no_divergence else 'OFF'}")
    print(f"{'='*60}")

    bt = FuturesBacktester(
        symbols=args.symbols,
        initial_balance=args.balance,
        enable_divergence=not args.no_divergence,
    )

    r = bt.backtest(args.start, args.end)

    types = Counter(
        t.get('entry_type', '?')
        for t in r.trades
    )

    print(f"\n{'='*60}")
    print(f"  RESULTS")
    print(f"{'='*60}")
    print(f"  Total Trades:   {r.total_trades}")
    print(f"  PNL:            ${r.total_pnl:,.2f}")
    print(f"  Win Rate:       {r.win_rate:.1f}%")
    print(f"  Profit Factor:  {r.profit_factor:.2f}")
    print(f"  Max Drawdown:   ${r.max_drawdown:,.2f}")
    print(f"  Final Balance:  ${r.final_balance:,.2f}")
    print(f"\n  Trade Breakdown:")
    for t, count in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {t}: {count}")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
