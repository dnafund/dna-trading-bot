"""
BTC Config Optimizer Round 2 — TP/SL grid search
Period: 2025-01-01 to 2026-02-21
Symbol: BTCUSDT only

Uses top 3 CE configs from Round 1 as base, sweeps TP/SL parameters.
Round 1 results: CE(22,3.0), CE(34,4.0), CE(44,5.0) all ~-$290 PF 0.95
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

# ─── Fixed from Round 1 top 3 ───────────────────────────────────
# We test each top CE config with each TP/SL combo
CE_CONFIGS = [
    (22, 3.0),   # #2 from round 1
    (34, 4.0),   # #3 from round 1 (best MDD)
    (44, 5.0),   # #1 from round 1 (best PNL)
]

# ─── TP/SL Grid ─────────────────────────────────────────────────
# EMA610 H1 is biggest PNL contributor, focus there
# Current: TP1=40, TP2=80, SL=30, TP1%=50
# Standard H1: TP1=30, TP2=60, SL=25, TP1%=70

TPSL_PROFILES = [
    # label, ema_h1, ema_h4, std_h1, std_h4
    # Format per tf: (tp1_roi, tp2_roi, hard_sl_roi, tp1_percent)

    # === Current baseline ===
    {
        "label": "CURRENT",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 70},
        },
    },

    # === Tighter TP (take profit sooner) ===
    {
        "label": "TIGHT_TP",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 20, "tp2_roi": 50, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 30, "tp2_roi": 70, "hard_sl_roi": 50, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 10, "tp2_roi": 25, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1": {"tp1_roi": 15, "tp2_roi": 35, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4": {"tp1_roi": 25, "tp2_roi": 60, "hard_sl_roi": 40, "tp1_percent": 70},
        },
    },

    # === Wider TP (let winners run more) ===
    {
        "label": "WIDE_TP",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 80, "tp2_roi": 160, "hard_sl_roi": 50, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4": {"tp1_roi": 80, "tp2_roi": 160, "hard_sl_roi": 40, "tp1_percent": 70},
        },
    },

    # === Tighter SL (cut losses faster) ===
    {
        "label": "TIGHT_SL",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 15, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 30, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 10, "tp1_percent": 70},
            "h1": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 15, "tp1_percent": 70},
            "h4": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 25, "tp1_percent": 70},
        },
    },

    # === Wider SL (give more room) ===
    {
        "label": "WIDE_SL",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 50, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 70, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 30, "tp1_percent": 70},
            "h1": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 40, "tp1_percent": 70},
            "h4": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 60, "tp1_percent": 70},
        },
    },

    # === Tight TP + Tight SL (scalp-style) ===
    {
        "label": "TIGHT_BOTH",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 20, "tp2_roi": 50, "hard_sl_roi": 15, "tp1_percent": 50},
            "h4": {"tp1_roi": 30, "tp2_roi": 70, "hard_sl_roi": 30, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 10, "tp2_roi": 25, "hard_sl_roi": 10, "tp1_percent": 70},
            "h1": {"tp1_roi": 15, "tp2_roi": 35, "hard_sl_roi": 15, "tp1_percent": 70},
            "h4": {"tp1_roi": 25, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 70},
        },
    },

    # === Wide TP + Tight SL (high R:R) ===
    {
        "label": "HIGH_RR",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 15, "tp1_percent": 50},
            "h4": {"tp1_roi": 80, "tp2_roi": 160, "hard_sl_roi": 30, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 10, "tp1_percent": 70},
            "h1": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 15, "tp1_percent": 70},
            "h4": {"tp1_roi": 80, "tp2_roi": 160, "hard_sl_roi": 25, "tp1_percent": 70},
        },
    },

    # === TP1 close more volume (70% instead of 50% for EMA) ===
    {
        "label": "BIG_TP1",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 70},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 70},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 80},
            "h1": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 80},
            "h4": {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 80},
        },
    },

    # === Tight TP + Wide SL (high WR approach) ===
    {
        "label": "HIGH_WR",
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 20, "tp2_roi": 50, "hard_sl_roi": 50, "tp1_percent": 50},
            "h4": {"tp1_roi": 30, "tp2_roi": 70, "hard_sl_roi": 70, "tp1_percent": 50},
        },
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 10, "tp2_roi": 25, "hard_sl_roi": 30, "tp1_percent": 70},
            "h1": {"tp1_roi": 15, "tp2_roi": 35, "hard_sl_roi": 40, "tp1_percent": 70},
            "h4": {"tp1_roi": 25, "tp2_roi": 60, "hard_sl_roi": 60, "tp1_percent": 70},
        },
    },
]

SYMBOL = "BTCUSDT"
START_DATE = "2025-01-01"
END_DATE = "2026-02-21"
INITIAL_BALANCE = 10000
OUTPUT_FILE = project_root / "data" / "optimize_btc_tpsl_results.json"


def run_single(ce_period, ce_mult, tpsl_profile):
    label = f"CE({ce_period},{ce_mult}) + {tpsl_profile['label']}"
    overrides = {
        "CHANDELIER_EXIT": {"period": ce_period, "multiplier": ce_mult},
        "EMA610_ENTRY": {"tolerance": 0.005},
        "EMA610_EXIT": tpsl_profile["EMA610_EXIT"],
        "STANDARD_EXIT": tpsl_profile["STANDARD_EXIT"],
    }

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
            "tpsl_profile": tpsl_profile["label"],
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
        return {"label": label, "error": str(e), "total_pnl": float("-inf")}


def save_results(results, partial=False):
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["total_pnl"], reverse=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump({"results": valid, "status": "partial" if partial else "complete", "run_date": datetime.now().isoformat()}, f, indent=2)


def _run_tpsl_wrapper(args):
    """Wrapper for ProcessPoolExecutor (needs single arg)."""
    ce_p, ce_m, profile = args
    return run_single(ce_p, ce_m, profile)


def main():
    combos = [(ce_p, ce_m, profile) for ce_p, ce_m in CE_CONFIGS for profile in TPSL_PROFILES]
    total = len(combos)
    workers = max(1, multiprocessing.cpu_count() - 1)

    print(f"={'=' * 74}")
    print(f"BTC TP/SL Optimizer - {total} combinations ({workers} workers)")
    print(f"Period: {START_DATE} to {END_DATE} | Symbol: {SYMBOL}")
    print(f"CE configs: {CE_CONFIGS}")
    print(f"TP/SL profiles: {[p['label'] for p in TPSL_PROFILES]}")
    print(f"={'=' * 74}")
    print()

    results = []
    start_time = datetime.now()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(_run_tpsl_wrapper, c): c for c in combos}
        for i, future in enumerate(as_completed(future_map), 1):
            r = future.result()
            results.append(r)

            elapsed = (datetime.now() - start_time).total_seconds()
            eta = (elapsed / i) * (total - i)

            if "error" in r:
                print(f"[{i}/{total}] {r['label']:40s} ERROR: {r['error']}")
            else:
                print(
                    f"[{i}/{total}] {r['label']:40s}"
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

    print(f"\n{'=' * 75}")
    print(f"TOP 10 CONFIGS BY PNL")
    print(f"{'=' * 75}")
    for rank, r in enumerate(valid[:10], 1):
        print(
            f"  #{rank:2d}  {r['label']:40s}  "
            f"PNL: ${r['total_pnl']:>10,.2f}  "
            f"WR: {r['win_rate']:>5.1f}%  "
            f"PF: {r['profit_factor']:>5.2f}  "
            f"MDD: ${r['max_drawdown']:>8,.2f}"
        )

    # Compare TP/SL profiles (average across CE configs)
    print(f"\n{'=' * 75}")
    print(f"TP/SL PROFILE COMPARISON (averaged across CE configs)")
    print(f"{'=' * 75}")
    for profile in TPSL_PROFILES:
        matches = [r for r in valid if r["tpsl_profile"] == profile["label"]]
        if matches:
            avg_pnl = sum(r["total_pnl"] for r in matches) / len(matches)
            avg_wr = sum(r["win_rate"] for r in matches) / len(matches)
            avg_pf = sum(r["profit_factor"] for r in matches) / len(matches)
            avg_mdd = sum(r["max_drawdown"] for r in matches) / len(matches)
            print(
                f"  {profile['label']:15s}  "
                f"Avg PNL: ${avg_pnl:>10,.2f}  "
                f"Avg WR: {avg_wr:>5.1f}%  "
                f"Avg PF: {avg_pf:>5.2f}  "
                f"Avg MDD: ${avg_mdd:>8,.2f}"
            )

    # Baseline
    baseline = next((r for r in valid if r["ce_period"] == 34 and r["ce_multiplier"] == 4.0 and r["tpsl_profile"] == "CURRENT"), None)
    if baseline and valid:
        print(f"\n{'=' * 75}")
        print(f"BASELINE: CE(34,4.0) + CURRENT TP/SL")
        print(f"{'=' * 75}")
        print(f"  PNL: ${baseline['total_pnl']:>10,.2f} | WR: {baseline['win_rate']:.1f}% | PF: {baseline['profit_factor']:.2f} | MDD: ${baseline['max_drawdown']:>8,.2f}")
        if valid[0]["total_pnl"] > baseline["total_pnl"]:
            diff = valid[0]["total_pnl"] - baseline["total_pnl"]
            print(f"  Best ({valid[0]['label']}) is ${diff:,.2f} better")

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Total time: {total_time/60:.1f} minutes ({total_time/total:.1f}s per combo)")


if __name__ == "__main__":
    main()
