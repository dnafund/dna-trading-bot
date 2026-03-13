"""
Grid search optimizer for TP/SL parameters per entry type.

Follows backtest-expert methodology:
- Find PLATEAUS (stable regions), not peaks (curve-fitted optima)
- Minimum 30 trades per combo for statistical significance
- Test at 50%, 75%, 100%, 125%, 150%, 175%, 200% of baseline

Performance: Pre-caches OHLCV data in memory so 280+ backtests
don't re-read Parquet files each time (~10x speedup).

Usage:
    python -m learning.optimizer --symbol BTCUSDT --start 2025-01-01 --end 2026-02-20
    python -m learning.optimizer --symbol BTCUSDT --start 2025-01-01 --end 2026-02-20 --entry-type standard_m15
"""

import argparse
import csv
import itertools
import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("learning.optimizer")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "learning" / "output" / "optimization"


# ── Baseline configs ──────────────────────────────────────────────

BASELINES = {
    "standard_m15": {"tp1_roi": 20, "tp2_roi": 40, "hard_sl_roi": 20, "tp1_percent": 70},
    "standard_h1":  {"tp1_roi": 30, "tp2_roi": 60, "hard_sl_roi": 25, "tp1_percent": 70},
    "standard_h4":  {"tp1_roi": 50, "tp2_roi": 100, "hard_sl_roi": 40, "tp1_percent": 70},
    "ema610_h1":    {"tp1_roi": 40, "tp2_roi": 80, "hard_sl_roi": 30, "tp1_percent": 50},
    "ema610_h4":    {"tp1_roi": 60, "tp2_roi": 120, "hard_sl_roi": 50, "tp1_percent": 50},
}

# Grid multipliers for coarse scan (5 levels to balance coverage vs runtime)
# Each backtest takes ~5 min for 14 months BTC data
GRID_MULTIPLIERS = [0.5, 1.0, 1.5, 2.0, 2.5]


@dataclass(frozen=True)
class GridResult:
    """Result of a single grid search run."""

    entry_type: str
    tp1_roi: float
    tp2_roi: float
    hard_sl_roi: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    profit_factor: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    tp1_hits: int
    tp2_hits: int


# ── In-memory OHLCV cache ────────────────────────────────────────

_OHLCV_CACHE: dict[str, "pd.DataFrame"] = {}


def _install_ohlcv_cache() -> None:
    """Monkey-patch load_cached_ohlcv to use in-memory cache.

    First call per (symbol, timeframe) reads from Parquet as normal,
    then caches the full DataFrame. Subsequent calls return a filtered
    copy from memory — no disk I/O.
    """
    import src.trading.backtest.engine as engine_mod

    _original_fn = engine_mod.load_cached_ohlcv

    def _cached_load(client, symbol, timeframe, since, until=None):
        import pandas as pd

        if until is None:
            until = datetime.now()

        cache_key = f"{symbol}_{timeframe}"

        if cache_key not in _OHLCV_CACHE:
            # First call — load from disk and store full DataFrame
            logger.info("  [cache] Loading %s %s from disk (first time)...", symbol, timeframe)
            df = _original_fn(client, symbol, timeframe, since, until)
            _OHLCV_CACHE[cache_key] = df
            return df.copy()

        # Subsequent calls — filter from memory cache
        full_df = _OHLCV_CACHE[cache_key]
        filtered = full_df[(full_df.index >= since) & (full_df.index <= until)]
        return filtered.copy()

    engine_mod.load_cached_ohlcv = _cached_load


def _clear_ohlcv_cache() -> None:
    """Release cached DataFrames."""
    _OHLCV_CACHE.clear()


# ── Grid search logic ────────────────────────────────────────────

def _build_override(entry_type: str, tp1: float, tp2: float, sl: float) -> dict:
    """Build config_overrides dict for a specific entry type."""
    tf = entry_type.split("_")[-1]  # m15, h1, h4
    params = {"tp1_roi": tp1, "tp2_roi": tp2, "hard_sl_roi": sl}

    if entry_type.startswith("standard"):
        params["tp1_percent"] = 70
        return {"STANDARD_EXIT": {tf: params}}
    elif entry_type.startswith("ema610"):
        params["tp1_percent"] = 50
        return {"EMA610_EXIT": {tf: params}}
    else:
        raise ValueError(f"Unknown entry type: {entry_type}")


def _extract_metrics(
    trades: list[dict],
    entry_type: str,
    tp1: float,
    tp2: float,
    sl: float,
    max_drawdown: float,
) -> Optional[GridResult]:
    """Extract per-entry-type metrics from backtest trades."""
    et_upper = entry_type.upper()
    trades_for_type = [t for t in trades if t["entry_type"] == et_upper]

    if not trades_for_type:
        return None

    wins = [t for t in trades_for_type if t["pnl"] > 0]
    losses = [t for t in trades_for_type if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades_for_type)

    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = round(gross_profit / gross_loss, 4) if gross_loss > 0 else (
        float("inf") if gross_profit > 0 else 0
    )

    avg_win = round(gross_profit / len(wins), 2) if wins else 0
    avg_loss = round(-gross_loss / len(losses), 2) if losses else 0

    tp1_hits = sum(1 for t in trades_for_type if t.get("_tp1_hit_tracked", False))
    tp2_hits = sum(1 for t in trades_for_type if t.get("_tp2_hit_tracked", False))

    return GridResult(
        entry_type=entry_type,
        tp1_roi=tp1,
        tp2_roi=tp2,
        hard_sl_roi=sl,
        total_trades=len(trades_for_type),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(len(wins) / max(len(trades_for_type), 1) * 100, 1),
        total_pnl=round(total_pnl, 2),
        profit_factor=pf,
        max_drawdown=round(max_drawdown, 2),
        avg_win=avg_win,
        avg_loss=avg_loss,
        tp1_hits=tp1_hits,
        tp2_hits=tp2_hits,
    )


def _run_single(
    symbol: str,
    start: str,
    end: str,
    balance: float,
    entry_type: str,
    tp1: float,
    tp2: float,
    sl: float,
) -> Optional[GridResult]:
    """Run a single backtest with specific TP/SL params.

    Suppresses backtest engine logging (1000+ lines per run) for performance.
    """
    from src.trading.backtest.engine import FuturesBacktester

    overrides = _build_override(entry_type, tp1, tp2, sl)

    # Suppress ALL output from backtest engine during grid search.
    # Engine uses both logger.info() AND print() for trade logs.
    # Redirecting stdout + suppressing loggers = ~10x speedup.
    import io
    import os

    _suppress_loggers = [
        logging.getLogger("src.trading.backtest.engine"),
        logging.getLogger("backtest"),
        logging.getLogger("trading"),
    ]
    prev_levels = [lg.level for lg in _suppress_loggers]
    for lg in _suppress_loggers:
        lg.setLevel(logging.ERROR)

    # Also suppress root logger to catch any unscoped logging
    root_logger = logging.getLogger()
    prev_root_level = root_logger.level
    root_logger.setLevel(logging.ERROR)

    # Redirect stdout to devnull (engine uses print() for trade logs)
    prev_stdout = sys.stdout
    sys.stdout = io.StringIO()

    try:
        bt = FuturesBacktester(
            symbols=[symbol],
            initial_balance=balance,
            config_overrides=overrides,
        )
        result = bt.backtest(start, end)
        return _extract_metrics(
            result.trades, entry_type, tp1, tp2, sl, result.max_drawdown,
        )
    except Exception as e:
        logger.error("Backtest failed for %s tp1=%s tp2=%s sl=%s: %s", entry_type, tp1, tp2, sl, e)
        return None
    finally:
        # Restore all output
        sys.stdout = prev_stdout
        root_logger.setLevel(prev_root_level)
        for lg, lvl in zip(_suppress_loggers, prev_levels):
            lg.setLevel(lvl)


def _generate_grid(entry_type: str) -> list[tuple[float, float, float]]:
    """Generate TP1, TP2, SL grid points for an entry type.

    Strategy: TP1 and TP2 scale together (preserve ratio), SL varies
    independently. This reduces 3D grid to 2D while capturing the
    most impactful parameter changes.

    For 5 scale levels × 5 SL levels = 25 combos per entry type.
    At ~5.5 min/backtest = ~2.3 hours/type, ~11.5 hours total.
    """
    baseline = BASELINES[entry_type]
    base_tp1 = baseline["tp1_roi"]
    base_tp2 = baseline["tp2_roi"]
    base_sl = baseline["hard_sl_roi"]

    combos = []
    for tp_mult in GRID_MULTIPLIERS:
        tp1 = round(base_tp1 * tp_mult)
        tp2 = round(base_tp2 * tp_mult)
        # Ensure TP2 > TP1 (always true when ratio preserved)
        if tp2 <= tp1:
            tp2 = tp1 + max(round(base_tp1 * 0.5), 5)

        for sl_mult in GRID_MULTIPLIERS:
            sl = round(base_sl * sl_mult)
            combos.append((float(tp1), float(tp2), float(sl)))

    return combos


def run_optimization(
    symbol: str,
    start: str,
    end: str,
    balance: float,
    entry_types: list[str],
) -> dict[str, list[GridResult]]:
    """Run grid search optimization with in-memory data caching."""
    # Install memory cache BEFORE any backtest runs
    _install_ohlcv_cache()

    all_results: dict[str, list[GridResult]] = {}

    for entry_type in entry_types:
        grid = _generate_grid(entry_type)
        baseline = BASELINES[entry_type]

        logger.info(
            "═══ Optimizing %s: %d combos (baseline: TP1=%d, TP2=%d, SL=%d) ═══",
            entry_type, len(grid),
            baseline["tp1_roi"], baseline["tp2_roi"], baseline["hard_sl_roi"],
        )

        results: list[GridResult] = []
        t0 = time.time()

        for i, (tp1, tp2, sl) in enumerate(grid):
            elapsed = time.time() - t0
            eta = (elapsed / max(i, 1)) * (len(grid) - i) if i > 0 else 0

            result = _run_single(symbol, start, end, balance, entry_type, tp1, tp2, sl)

            if result and result.total_trades >= 10:
                results.append(result)
                is_baseline = (
                    tp1 == baseline["tp1_roi"]
                    and tp2 == baseline["tp2_roi"]
                    and sl == baseline["hard_sl_roi"]
                )
                marker = " ★ BASELINE" if is_baseline else ""
                logger.info(
                    "  [%3d/%d] TP1=%3.0f TP2=%3.0f SL=%3.0f → "
                    "%3d trades, WR=%.1f%%, PNL=$%+.2f, PF=%.2f "
                    "(%.0fs elapsed, ETA %.0fs)%s",
                    i + 1, len(grid), tp1, tp2, sl,
                    result.total_trades, result.win_rate,
                    result.total_pnl, result.profit_factor,
                    elapsed, eta, marker,
                )
            else:
                if i % 20 == 0:
                    logger.info(
                        "  [%3d/%d] TP1=%3.0f TP2=%3.0f SL=%3.0f → "
                        "skipped (<10 trades) (%.0fs elapsed)",
                        i + 1, len(grid), tp1, tp2, sl, elapsed,
                    )

            # Flush stdout for real-time progress
            sys.stdout.flush()

        total_time = time.time() - t0
        logger.info(
            "  %s complete: %d valid results in %.0fs (%.1fs/combo)",
            entry_type, len(results), total_time,
            total_time / max(len(grid), 1),
        )

        results.sort(key=lambda r: r.profit_factor, reverse=True)
        all_results[entry_type] = results

        # Export per-entry-type results immediately
        _export_single_entry(entry_type, results, symbol, start, end)

    _clear_ohlcv_cache()
    return all_results


# ── Plateau detection ─────────────────────────────────────────────

def _find_plateaus(results: list[GridResult], top_n: int = 5) -> list[GridResult]:
    """Find stable plateaus — combos where neighbors also perform well.

    Score = PF * 0.6 + neighbor_stability * 0.4
    "Neighbor" = differs by ≤30% on each param axis.
    """
    if len(results) < 3:
        return results[:top_n]

    scored: list[tuple[float, GridResult]] = []

    for r in results:
        if r.profit_factor == float("inf") or r.total_trades < 30:
            continue

        # Check how many neighboring combos also profitable
        neighbor_pf_sum = 0.0
        neighbor_count = 0

        for other in results:
            if other is r:
                continue
            tp1_close = abs(other.tp1_roi - r.tp1_roi) <= (r.tp1_roi * 0.3)
            tp2_close = abs(other.tp2_roi - r.tp2_roi) <= (r.tp2_roi * 0.3)
            sl_close = abs(other.hard_sl_roi - r.hard_sl_roi) <= (r.hard_sl_roi * 0.3)

            if tp1_close and tp2_close and sl_close:
                neighbor_pf_sum += min(other.profit_factor, 10)
                neighbor_count += 1

        stability = (neighbor_pf_sum / max(neighbor_count, 1)) if neighbor_count > 0 else 0
        plateau_score = r.profit_factor * 0.6 + stability * 0.4

        scored.append((plateau_score, r))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored[:top_n]]


# ── Export ────────────────────────────────────────────────────────

def _export_single_entry(
    entry_type: str,
    results: list[GridResult],
    symbol: str,
    start: str,
    end: str,
) -> None:
    """Export results for a single entry type immediately (fault-tolerant)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{symbol.lower()}_{start.replace('-', '')}_{end.replace('-', '')}"

    csv_path = OUTPUT_DIR / f"grid_{tag}_{entry_type}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "entry_type", "tp1_roi", "tp2_roi", "hard_sl_roi",
            "trades", "wins", "losses", "win_rate",
            "total_pnl", "profit_factor", "max_drawdown",
            "avg_win", "avg_loss", "tp1_hits", "tp2_hits",
        ])
        for r in results:
            writer.writerow([
                r.entry_type, r.tp1_roi, r.tp2_roi, r.hard_sl_roi,
                r.total_trades, r.winning_trades, r.losing_trades, r.win_rate,
                r.total_pnl, r.profit_factor, r.max_drawdown,
                r.avg_win, r.avg_loss, r.tp1_hits, r.tp2_hits,
            ])

    logger.info("  Exported %d results to %s", len(results), csv_path)


def _export_results(
    all_results: dict[str, list[GridResult]],
    symbol: str,
    start: str,
    end: str,
) -> Path:
    """Export combined results + summary JSON with plateau analysis."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{symbol.lower()}_{start.replace('-', '')}_{end.replace('-', '')}"

    # Summary JSON with plateaus
    summary = {}
    for entry_type, results in all_results.items():
        baseline = BASELINES[entry_type]
        baseline_result = next(
            (r for r in results
             if r.tp1_roi == baseline["tp1_roi"]
             and r.tp2_roi == baseline["tp2_roi"]
             and r.hard_sl_roi == baseline["hard_sl_roi"]),
            None,
        )

        plateaus = _find_plateaus(results)

        summary[entry_type] = {
            "total_combos_tested": len(results),
            "baseline": {
                "tp1_roi": baseline["tp1_roi"],
                "tp2_roi": baseline["tp2_roi"],
                "hard_sl_roi": baseline["hard_sl_roi"],
                "profit_factor": baseline_result.profit_factor if baseline_result else None,
                "total_pnl": baseline_result.total_pnl if baseline_result else None,
                "trades": baseline_result.total_trades if baseline_result else None,
                "win_rate": baseline_result.win_rate if baseline_result else None,
            },
            "best_plateaus": [
                {
                    "tp1_roi": r.tp1_roi,
                    "tp2_roi": r.tp2_roi,
                    "hard_sl_roi": r.hard_sl_roi,
                    "profit_factor": r.profit_factor,
                    "total_pnl": r.total_pnl,
                    "trades": r.total_trades,
                    "win_rate": r.win_rate,
                    "avg_win": r.avg_win,
                    "avg_loss": r.avg_loss,
                }
                for r in plateaus
            ],
        }

    json_path = OUTPUT_DIR / f"summary_{tag}.json"
    json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    logger.info("Summary exported to %s", json_path)
    return json_path


def print_summary(all_results: dict[str, list[GridResult]]) -> None:
    """Print optimization summary to console."""
    for entry_type, results in all_results.items():
        baseline = BASELINES[entry_type]
        baseline_result = next(
            (r for r in results
             if r.tp1_roi == baseline["tp1_roi"]
             and r.tp2_roi == baseline["tp2_roi"]
             and r.hard_sl_roi == baseline["hard_sl_roi"]),
            None,
        )

        print(f"\n{'═'*70}")
        print(f"  {entry_type.upper()} — {len(results)} combos tested")
        print(f"{'═'*70}")

        if baseline_result:
            print(
                f"  Baseline:  TP1={baseline['tp1_roi']} TP2={baseline['tp2_roi']} "
                f"SL={baseline['hard_sl_roi']} → "
                f"PF={baseline_result.profit_factor:.2f}, "
                f"PNL=${baseline_result.total_pnl:+.2f}, "
                f"WR={baseline_result.win_rate}%, "
                f"{baseline_result.total_trades} trades"
            )

        plateaus = _find_plateaus(results)
        if plateaus:
            print(f"\n  Top {len(plateaus)} Plateaus (stable profitable regions):")
            print(f"  {'─'*64}")
            print(f"  {'TP1':>5} {'TP2':>5} {'SL':>5} │ {'Trades':>6} {'WR':>6} {'PNL':>10} {'PF':>6} {'AvgW':>7} {'AvgL':>7}")
            print(f"  {'─'*64}")
            for r in plateaus:
                pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 100 else "inf"
                print(
                    f"  {r.tp1_roi:5.0f} {r.tp2_roi:5.0f} {r.hard_sl_roi:5.0f} │ "
                    f"{r.total_trades:6d} {r.win_rate:5.1f}% "
                    f"${r.total_pnl:+9.2f} {pf_str:>6} "
                    f"${r.avg_win:+6.2f} ${r.avg_loss:+6.2f}"
                )

            best = plateaus[0]
            if baseline_result and baseline_result.profit_factor > 0:
                improvement = (
                    (best.profit_factor - baseline_result.profit_factor)
                    / baseline_result.profit_factor * 100
                )
                print(
                    f"\n  ★ Best plateau vs baseline: "
                    f"PF {baseline_result.profit_factor:.2f} → {best.profit_factor:.2f} "
                    f"({improvement:+.0f}%)"
                )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="TP/SL Grid Search Optimizer")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to backtest")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance")
    parser.add_argument(
        "--entry-type",
        default=None,
        help="Specific entry type (e.g., standard_m15). Omit for all.",
    )
    args = parser.parse_args()

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    if args.entry_type:
        entry_types = [args.entry_type]
    else:
        entry_types = list(BASELINES.keys())

    print(f"\n{'═'*70}")
    print(f"  TP/SL Grid Search Optimizer")
    print(f"  {args.symbol} | {args.start} → {end_date}")
    print(f"  Entry types: {', '.join(entry_types)}")
    total_combos = sum(len(_generate_grid(et)) for et in entry_types)
    print(f"  Total combos: {total_combos}")
    print(f"{'═'*70}\n")

    all_results = run_optimization(
        args.symbol, args.start, end_date, args.balance, entry_types,
    )

    json_path = _export_results(all_results, args.symbol, args.start, end_date)
    print_summary(all_results)

    print(f"\n  Summary JSON: {json_path}")
    print(f"  Per-entry CSVs: {OUTPUT_DIR}/grid_*.csv")


if __name__ == "__main__":
    main()
