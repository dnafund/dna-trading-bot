"""
BTC Combo Optimizer — CE rộng + SL rộng + TP rộng
Period: 2025-01-01 to 2026-02-21
Symbol: BTCUSDT only

Test all 3 dimensions together to find best combo.
"""

import sys
import json
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.trading.backtest.engine import FuturesBacktester

logging.basicConfig(level=logging.WARNING)
logging.getLogger("src.trading").setLevel(logging.WARNING)

# ─── Building blocks ───────────────────────────────────────────

SL_CURRENT = {
    "STANDARD_EXIT": {
        "m15": {"hard_sl_roi": 20},
        "h1":  {"hard_sl_roi": 25},
        "h4":  {"hard_sl_roi": 40},
    },
    "EMA610_EXIT": {
        "h1": {"hard_sl_roi": 30},
        "h4": {"hard_sl_roi": 50},
    },
}

SL_WIDE = {
    "STANDARD_EXIT": {
        "m15": {"hard_sl_roi": 30},
        "h1":  {"hard_sl_roi": 40},
        "h4":  {"hard_sl_roi": 60},
    },
    "EMA610_EXIT": {
        "h1": {"hard_sl_roi": 50},
        "h4": {"hard_sl_roi": 70},
    },
}

TP_CURRENT = {
    "STANDARD_EXIT": {
        "m15": {"tp1_roi": 20, "tp2_roi": 40, "tp1_percent": 70},
        "h1":  {"tp1_roi": 30, "tp2_roi": 60, "tp1_percent": 70},
        "h4":  {"tp1_roi": 50, "tp2_roi": 100, "tp1_percent": 70},
    },
    "EMA610_EXIT": {
        "h1": {"tp1_roi": 40, "tp2_roi": 80, "tp1_percent": 50},
        "h4": {"tp1_roi": 60, "tp2_roi": 120, "tp1_percent": 50},
    },
}

TP_DOUBLE = {  # TP1 x2, TP2 x1.5
    "STANDARD_EXIT": {
        "m15": {"tp1_roi": 40, "tp2_roi": 60, "tp1_percent": 70},
        "h1":  {"tp1_roi": 60, "tp2_roi": 90, "tp1_percent": 70},
        "h4":  {"tp1_roi": 100, "tp2_roi": 150, "tp1_percent": 70},
    },
    "EMA610_EXIT": {
        "h1": {"tp1_roi": 80, "tp2_roi": 120, "tp1_percent": 50},
        "h4": {"tp1_roi": 120, "tp2_roi": 180, "tp1_percent": 50},
    },
}

TP_WIDER = {  # TP1 x1.5, TP2 x1.5
    "STANDARD_EXIT": {
        "m15": {"tp1_roi": 30, "tp2_roi": 60, "tp1_percent": 70},
        "h1":  {"tp1_roi": 45, "tp2_roi": 90, "tp1_percent": 70},
        "h4":  {"tp1_roi": 75, "tp2_roi": 150, "tp1_percent": 70},
    },
    "EMA610_EXIT": {
        "h1": {"tp1_roi": 60, "tp2_roi": 120, "tp1_percent": 50},
        "h4": {"tp1_roi": 90, "tp2_roi": 180, "tp1_percent": 50},
    },
}

TP_DOUBLE_LESS_CLOSE = {  # TP1 x2, TP2 x1.5, close 30%
    "STANDARD_EXIT": {
        "m15": {"tp1_roi": 40, "tp2_roi": 60, "tp1_percent": 30},
        "h1":  {"tp1_roi": 60, "tp2_roi": 90, "tp1_percent": 30},
        "h4":  {"tp1_roi": 100, "tp2_roi": 150, "tp1_percent": 30},
    },
    "EMA610_EXIT": {
        "h1": {"tp1_roi": 80, "tp2_roi": 120, "tp1_percent": 30},
        "h4": {"tp1_roi": 120, "tp2_roi": 180, "tp1_percent": 30},
    },
}


def merge_exit(tp_cfg, sl_cfg):
    """Merge TP config with SL config into complete exit overrides."""
    result = {"STANDARD_EXIT": {}, "EMA610_EXIT": {}}
    for section in ["STANDARD_EXIT", "EMA610_EXIT"]:
        for tf in tp_cfg[section]:
            merged = {**tp_cfg[section][tf]}
            if tf in sl_cfg[section]:
                merged.update(sl_cfg[section][tf])
            result[section][tf] = merged
    return result


# ─── Combos to test ────────────────────────────────────────────
COMBOS = [
    # label, ce_period, ce_mult, tp_name, sl_name
    # Baseline
    ("CE1.75_SL_CUR_TP_CUR",     34, 1.75, TP_CURRENT, SL_CURRENT),

    # CE(34,4.0) combos (round 2 winner CE)
    ("CE4.0_SL_CUR_TP_CUR",      34, 4.0,  TP_CURRENT, SL_CURRENT),
    ("CE4.0_SL_WIDE_TP_CUR",     34, 4.0,  TP_CURRENT, SL_WIDE),     # round 2 winner
    ("CE4.0_SL_WIDE_TP_WIDER",   34, 4.0,  TP_WIDER,   SL_WIDE),
    ("CE4.0_SL_WIDE_TP_DBL",     34, 4.0,  TP_DOUBLE,  SL_WIDE),
    ("CE4.0_SL_WIDE_TP_DBL_C30", 34, 4.0,  TP_DOUBLE_LESS_CLOSE, SL_WIDE),

    # CE(34,3.0) combos (middle ground)
    ("CE3.0_SL_WIDE_TP_CUR",     34, 3.0,  TP_CURRENT, SL_WIDE),
    ("CE3.0_SL_WIDE_TP_DBL",     34, 3.0,  TP_DOUBLE,  SL_WIDE),

    # CE(34,5.0) combos (very wide CE)
    ("CE5.0_SL_WIDE_TP_CUR",     34, 5.0,  TP_CURRENT, SL_WIDE),
    ("CE5.0_SL_WIDE_TP_DBL",     34, 5.0,  TP_DOUBLE,  SL_WIDE),

    # CE(34,1.75) + WIDE SL (is wider SL alone enough?)
    ("CE1.75_SL_WIDE_TP_CUR",    34, 1.75, TP_CURRENT, SL_WIDE),
    ("CE1.75_SL_WIDE_TP_DBL",    34, 1.75, TP_DOUBLE,  SL_WIDE),
]

SYMBOL = "BTCUSDT"
START_DATE = "2025-01-01"
END_DATE = "2026-02-21"
INITIAL_BALANCE = 10000
OUTPUT_FILE = project_root / "data" / "optimize_btc_combo_results.json"


def run_single(label, ce_period, ce_mult, tp_cfg, sl_cfg):
    exit_cfg = merge_exit(tp_cfg, sl_cfg)

    overrides = {
        "CHANDELIER_EXIT": {"period": ce_period, "multiplier": ce_mult},
        "EMA610_ENTRY": {"tolerance": 0.005},
        "EMA610_EXIT": exit_cfg["EMA610_EXIT"],
        "STANDARD_EXIT": exit_cfg["STANDARD_EXIT"],
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
            "ce_mult": ce_mult,
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
        json.dump({
            "results": valid,
            "status": "partial" if partial else "complete",
            "run_date": datetime.now().isoformat(),
        }, f, indent=2)


def _run_combo_wrapper(args):
    """Wrapper for ProcessPoolExecutor (needs single arg)."""
    label, ce_p, ce_m, tp_cfg, sl_cfg = args
    return run_single(label, ce_p, ce_m, tp_cfg, sl_cfg)


def main():
    total = len(COMBOS)
    workers = max(1, multiprocessing.cpu_count() - 1)
    print(f"{'=' * 80}")
    print(f"BTC COMBO Optimizer — {total} combos ({workers} workers)")
    print(f"Period: {START_DATE} to {END_DATE} | Symbol: {SYMBOL}")
    print(f"{'=' * 80}")
    print()

    results = []
    start_time = datetime.now()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_label = {executor.submit(_run_combo_wrapper, combo): combo[0] for combo in COMBOS}
        for i, future in enumerate(as_completed(future_to_label), 1):
            r = future.result()
            results.append(r)

            elapsed = (datetime.now() - start_time).total_seconds()
            eta = (elapsed / i) * (total - i)

            if "error" in r:
                print(f"[{i}/{total}] {r['label']:30s} ERROR: {r['error']}")
            else:
                marker = " *" if r["total_pnl"] > 0 else ""
                print(
                    f"[{i}/{total}] {r['label']:30s}"
                    f" PNL: ${r['total_pnl']:>10,.2f} | "
                    f"Trades: {r['total_trades']:>4} | "
                    f"WR: {r['win_rate']:>5.1f}% | "
                    f"PF: {r['profit_factor']:>5.2f} | "
                    f"MDD: ${r['max_drawdown']:>8,.2f} | "
                    f"ETA: {eta/60:.0f}m{marker}"
                )

            save_results(results, partial=(i < total))

    # Final ranking
    valid = [r for r in results if "error" not in r]
    valid.sort(key=lambda x: x["total_pnl"], reverse=True)

    print(f"\n{'=' * 80}")
    print(f"RANKING — ALL COMBOS")
    print(f"{'=' * 80}")
    for rank, r in enumerate(valid, 1):
        marker = " * PROFITABLE" if r["total_pnl"] > 0 else ""
        base = " <<< BASELINE" if r["label"] == "CE1.75_SL_CUR_TP_CUR" else ""
        print(
            f"  #{rank:2d}  {r['label']:30s}  "
            f"PNL: ${r['total_pnl']:>10,.2f}  "
            f"WR: {r['win_rate']:>5.1f}%  "
            f"PF: {r['profit_factor']:>5.2f}  "
            f"MDD: ${r['max_drawdown']:>8,.2f}"
            f"{marker}{base}"
        )

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Total time: {total_time/60:.1f} minutes ({total_time/total:.1f}s per combo)")


if __name__ == "__main__":
    main()
