"""
BTC Config Optimizer — Grid search for best parameters
Period: 2025-01-01 to 2026-02-21
Symbol: BTCUSDT only

Searches across key parameters:
- Chandelier Exit: period, multiplier
- EMA610 tolerance
"""

import sys
import json
import logging
import itertools
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.trading.backtest.engine import FuturesBacktester

logging.basicConfig(level=logging.WARNING)
logging.getLogger("src.trading").setLevel(logging.WARNING)

# ─── Parameter Grid ─────────────────────────────────────────────
PARAM_GRID = {
    # Baseline 1.75, plus higher multipliers (session showed higher = better)
    "ce_period": [22, 34, 44],
    "ce_multiplier": [1.75, 2.5, 3.0, 3.5, 4.0, 5.0],
    "ema610_tolerance": [0.005],
}

SYMBOL = "BTCUSDT"
START_DATE = "2025-01-01"
END_DATE = "2026-02-21"
INITIAL_BALANCE = 10000
OUTPUT_FILE = project_root / "data" / "optimize_btc_results.json"


def build_overrides(ce_period, ce_multiplier, ema610_tolerance):
    return {
        "CHANDELIER_EXIT": {
            "period": ce_period,
            "multiplier": ce_multiplier,
        },
        "EMA610_ENTRY": {
            "tolerance": ema610_tolerance,
        },
    }


def run_single(params):
    ce_period, ce_mult, ema_tol = params
    overrides = build_overrides(ce_period, ce_mult, ema_tol)
    label = f"CE({ce_period},{ce_mult}) tol={ema_tol}"

    try:
        engine = FuturesBacktester(
            symbols=[SYMBOL],
            initial_balance=INITIAL_BALANCE,
            enable_divergence=True,
            config_overrides=overrides,
        )
        result = engine.backtest(START_DATE, END_DATE)

        return {
            "label": label,
            "ce_period": ce_period,
            "ce_multiplier": ce_mult,
            "ema610_tolerance": ema_tol,
            "total_pnl": round(result.total_pnl, 2),
            "total_trades": result.total_trades,
            "win_rate": round(result.win_rate, 1),
            "profit_factor": round(result.profit_factor, 2),
            "max_drawdown": round(result.max_drawdown, 2),
            "avg_win": round(result.avg_win, 2),
            "avg_loss": round(result.avg_loss, 2),
            "total_fees": round(result.total_fees, 2),
        }
    except Exception as e:
        return {
            "label": label,
            "error": str(e),
            "total_pnl": float("-inf"),
        }


def save_results(results, partial=False):
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["total_pnl"], reverse=True)
    tag = "partial" if partial else "complete"
    with open(OUTPUT_FILE, "w") as f:
        json.dump({"results": valid, "status": tag, "run_date": datetime.now().isoformat()}, f, indent=2)


def main():
    combos = list(itertools.product(
        PARAM_GRID["ce_period"],
        PARAM_GRID["ce_multiplier"],
        PARAM_GRID["ema610_tolerance"],
    ))

    total = len(combos)
    workers = max(1, multiprocessing.cpu_count() - 1)
    print(f"={'=' * 69}")
    print(f"BTC Config Optimizer - {total} combinations ({workers} workers)")
    print(f"Period: {START_DATE} to {END_DATE} | Symbol: {SYMBOL}")
    print(f"CE periods: {PARAM_GRID['ce_period']}")
    print(f"CE multipliers: {PARAM_GRID['ce_multiplier']}")
    print(f"={'=' * 69}")
    print()

    results = []
    start_time = datetime.now()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_params = {executor.submit(run_single, p): p for p in combos}
        for i, future in enumerate(as_completed(future_to_params), 1):
            r = future.result()
            results.append(r)

            elapsed = (datetime.now() - start_time).total_seconds()
            eta = (elapsed / i) * (total - i)

            if "error" in r:
                print(f"[{i}/{total}] {r['label']:30s} ERROR: {r['error']}")
            else:
                print(
                    f"[{i}/{total}] {r['label']:30s}"
                    f" PNL: ${r['total_pnl']:>10,.2f} | "
                    f"Trades: {r['total_trades']:>4} | "
                    f"WR: {r['win_rate']:>5.1f}% | "
                    f"PF: {r['profit_factor']:>5.2f} | "
                    f"MDD: ${r['max_drawdown']:>8,.2f} | "
                    f"ETA: {eta/60:.0f}m"
                )

            save_results(results, partial=(i < total))

    # Final summary
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["total_pnl"], reverse=True)

    print(f"\n{'=' * 70}")
    print(f"TOP 10 CONFIGS BY PNL")
    print(f"{'=' * 70}")
    for rank, r in enumerate(valid[:10], 1):
        print(
            f"  #{rank:2d}  {r['label']:30s}  "
            f"PNL: ${r['total_pnl']:>10,.2f}  "
            f"Trades: {r['total_trades']:>4}  "
            f"WR: {r['win_rate']:>5.1f}%  "
            f"PF: {r['profit_factor']:>5.2f}  "
            f"MDD: ${r['max_drawdown']:>8,.2f}"
        )

    print(f"\n{'=' * 70}")
    print(f"BOTTOM 3 CONFIGS (worst)")
    print(f"{'=' * 70}")
    for rank, r in enumerate(valid[-3:], 1):
        print(f"  #{rank}  {r['label']:30s}  PNL: ${r['total_pnl']:>10,.2f}  WR: {r['win_rate']:.1f}%  PF: {r['profit_factor']:.2f}")

    # Baseline comparison
    baseline = next((r for r in valid if r["ce_period"] == 34 and r["ce_multiplier"] == 1.75), None)
    if baseline:
        print(f"\n{'=' * 70}")
        print(f"CURRENT CONFIG (baseline): CE(34,1.75)")
        print(f"{'=' * 70}")
        print(f"  PNL: ${baseline['total_pnl']:>10,.2f} | Trades: {baseline['total_trades']} | WR: {baseline['win_rate']:.1f}% | PF: {baseline['profit_factor']:.2f}")
        if valid[0]["total_pnl"] > baseline["total_pnl"]:
            diff = valid[0]["total_pnl"] - baseline["total_pnl"]
            print(f"  Best config ({valid[0]['label']}) is ${diff:,.2f} better")

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Total time: {total_time/60:.1f} minutes ({total_time/total:.1f}s per combo)")


if __name__ == "__main__":
    main()
