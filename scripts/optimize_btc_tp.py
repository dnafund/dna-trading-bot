"""
BTC TP Optimizer — Find optimal Take Profit levels
Period: 2025-01-01 to 2026-02-21
Symbol: BTCUSDT only

Fixed: CE(34, 1.75) + current Hard SL (unchanged)
Test: 15 TP profiles covering tight→wide TP1, TP2, close %
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

# ─── TP Profiles ───────────────────────────────────────────────
# Format: Standard(m15/h1/h4) + EMA610(h1/h4)
# Each entry: (tp1_roi, tp2_roi, hard_sl_roi, tp1_percent)
# Hard SL is FIXED at current values throughout

TP_PROFILES = [
    # === BASELINE (current config) ===
    {
        "label": "CURRENT",
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },

    # === TP1 variations (when to take first profit) ===
    {
        "label": "TP1_HALF",  # TP1 at half current
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 10, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 15, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 25, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 20, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 30, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "TP1_x1.5",  # TP1 50% wider
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 30, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 45, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 75, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 60, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 90, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "TP1_DOUBLE",  # TP1 doubled
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 40, "tp2_roi": 60, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 60, "tp2_roi": 90, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 100, "tp2_roi": 150, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 80, "tp2_roi": 120, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 120, "tp2_roi": 180, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },

    # === TP2 variations (when to close remaining) ===
    {
        "label": "TP2_HALF",  # TP2 at half (close faster)
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 25, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 30, "tp2_roi": 40, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 50, "tp2_roi": 65, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 50, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 75, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "TP2_x1.5",  # TP2 50% wider (let winners run)
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 60, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 30, "tp2_roi": 90, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 50, "tp2_roi": 150, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 120, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 180, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "TP2_DOUBLE",  # TP2 doubled
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 80, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 30, "tp2_roi": 120, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 50, "tp2_roi": 200, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 160, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 240, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "NO_TP2",  # TP2 = TP1 (close 100% at TP1, let chandelier handle the rest)
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 999, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 30, "tp2_roi": 999, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 50, "tp2_roi": 999, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 999, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 999, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },

    # === TP1 close % variations ===
    {
        "label": "CLOSE_30PCT",  # Close only 30% at TP1 (keep 70% running)
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 30},
            "h1":  {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 30},
            "h4":  {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 30},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 30},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 30},
        },
    },
    {
        "label": "CLOSE_50PCT",  # Close 50% at TP1
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 50},
            "h1":  {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 50},
            "h4":  {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 50},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "CLOSE_90PCT",  # Close 90% at TP1 (almost all early)
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 90},
            "h1":  {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 90},
            "h4":  {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 90},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 90},
            "h4": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 90},
        },
    },

    # === Combined best-guess combos ===
    {
        "label": "TIGHT_TP1_WIDE_TP2",  # Quick partial + let rest run far
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 10, "tp2_roi": 60, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 15, "tp2_roi": 90, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 25, "tp2_roi": 150, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 20, "tp2_roi": 120, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 30, "tp2_roi": 180, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "BOTH_WIDER",  # TP1 x1.5, TP2 x1.5
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 45, "tp2_roi": 90, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 75, "tp2_roi": 150, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 90, "tp2_roi": 180, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "BOTH_TIGHTER",  # TP1 x0.75, TP2 x0.75
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 15, "tp2_roi": 30, "hard_sl_roi": 20, "tp1_percent": 70},
            "h1":  {"tp1_roi": 22, "tp2_roi": 45, "hard_sl_roi": 25, "tp1_percent": 70},
            "h4":  {"tp1_roi": 38, "tp2_roi": 75, "hard_sl_roi": 40, "tp1_percent": 70},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 30, "tp1_percent": 50},
            "h4": {"tp1_roi": 45, "tp2_roi": 90, "hard_sl_roi": 50, "tp1_percent": 50},
        },
    },
    {
        "label": "SCALP",  # Very tight TP1, close 90%, wide TP2
        "STANDARD_EXIT": {
            "m15": {"tp1_roi": 10, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 90},
            "h1":  {"tp1_roi": 15, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 90},
            "h4":  {"tp1_roi": 25, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 90},
        },
        "EMA610_EXIT": {
            "h1": {"tp1_roi": 20, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 90},
            "h4": {"tp1_roi": 30, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 90},
        },
    },
]

# Fixed CE config (current live)
CE_PERIOD = 34
CE_MULT = 1.75

SYMBOL = "BTCUSDT"
START_DATE = "2025-01-01"
END_DATE = "2026-02-21"
INITIAL_BALANCE = 10000
OUTPUT_FILE = project_root / "data" / "optimize_btc_tp_results.json"


def run_single(profile):
    label = profile["label"]
    overrides = {
        "CHANDELIER_EXIT": {"period": CE_PERIOD, "multiplier": CE_MULT},
        "EMA610_ENTRY": {"tolerance": 0.005},
        "EMA610_EXIT": profile["EMA610_EXIT"],
        "STANDARD_EXIT": profile["STANDARD_EXIT"],
    }

    try:
        engine = FuturesBacktester(
            symbols=[SYMBOL],
            initial_balance=INITIAL_BALANCE,
            enable_divergence=True,
            config_overrides=overrides,
        )
        result = engine.backtest(START_DATE, END_DATE)

        std_h1 = profile["STANDARD_EXIT"]["h1"]
        ema_h1 = profile["EMA610_EXIT"]["h1"]

        return {
            "label": label,
            "std_m15_tp1": profile["STANDARD_EXIT"]["m15"]["tp1_roi"],
            "std_h1_tp1": std_h1["tp1_roi"],
            "std_h1_tp2": std_h1["tp2_roi"],
            "std_h4_tp1": profile["STANDARD_EXIT"]["h4"]["tp1_roi"],
            "std_h4_tp2": profile["STANDARD_EXIT"]["h4"]["tp2_roi"],
            "ema_h1_tp1": ema_h1["tp1_roi"],
            "ema_h1_tp2": ema_h1["tp2_roi"],
            "tp1_close_pct": std_h1["tp1_percent"],
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
            "ce_config": f"CE({CE_PERIOD},{CE_MULT})",
            "run_date": datetime.now().isoformat(),
        }, f, indent=2)


def main():
    total = len(TP_PROFILES)
    workers = max(1, multiprocessing.cpu_count() - 1)
    print(f"={'=' * 74}")
    print(f"BTC TP Optimizer — {total} profiles ({workers} workers)")
    print(f"Period: {START_DATE} to {END_DATE} | Symbol: {SYMBOL}")
    print(f"Fixed: CE({CE_PERIOD},{CE_MULT}) | Hard SL: unchanged")
    print(f"={'=' * 74}")
    print()

    results = []
    start_time = datetime.now()

    with ProcessPoolExecutor(max_workers=workers) as executor:
        future_to_label = {executor.submit(run_single, p): p['label'] for p in TP_PROFILES}
        for i, future in enumerate(as_completed(future_to_label), 1):
            r = future.result()
            results.append(r)

            elapsed = (datetime.now() - start_time).total_seconds()
            eta = (elapsed / i) * (total - i)

            if "error" in r:
                print(f"[{i}/{total}] {r['label']:25s} ERROR: {r['error']}")
            else:
                print(
                    f"[{i}/{total}] {r['label']:25s}"
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

    print(f"\n{'=' * 80}")
    print(f"ALL RESULTS RANKED BY PNL")
    print(f"{'=' * 80}")
    for rank, r in enumerate(valid, 1):
        marker = " <<<" if r["label"] == "CURRENT" else ""
        print(
            f"  #{rank:2d}  {r['label']:25s}  "
            f"PNL: ${r['total_pnl']:>10,.2f}  "
            f"WR: {r['win_rate']:>5.1f}%  "
            f"PF: {r['profit_factor']:>5.2f}  "
            f"MDD: ${r['max_drawdown']:>8,.2f}  "
            f"H1: TP1={r['std_h1_tp1']}/TP2={r['std_h1_tp2']} close{r['tp1_close_pct']}%"
            f"{marker}"
        )

    total_time = (datetime.now() - start_time).total_seconds()
    print(f"\nSaved: {OUTPUT_FILE}")
    print(f"Total time: {total_time/60:.1f} minutes ({total_time/total:.1f}s per combo)")


if __name__ == "__main__":
    main()
