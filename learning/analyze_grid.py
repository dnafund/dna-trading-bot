"""
Grid Search Results Analyzer
Parse optimization logs → find plateaus (stable regions) → generate report.

Plateau detection: Score = PF × 0.6 + neighbor_stability × 0.4
A combo's neighbors are combos with adjacent TP or SL multiplier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GridResult:
    tp1: float
    tp2: float
    sl: float
    trades: int
    win_rate: float
    pnl: float
    pf: float
    is_baseline: bool = False


# ─── Parse log files ───────────────────────────────────────────────

_LOG_PATTERN = re.compile(
    r"TP1=\s*(\d+)\s+TP2=\s*(\d+)\s+SL=\s*(\d+)\s+→\s*(\d+)\s+trades,\s+"
    r"WR=(\d+\.?\d*)%,\s+PNL=\$([+-]?\d+\.?\d*),\s+PF=(\d+\.?\d*)"
    r"(?:.*?(★ BASELINE))?"
)


def parse_log(path: Path) -> list[GridResult]:
    results: list[GridResult] = []
    text = path.read_text()
    for match in _LOG_PATTERN.finditer(text):
        results.append(GridResult(
            tp1=float(match.group(1)),
            tp2=float(match.group(2)),
            sl=float(match.group(3)),
            trades=int(match.group(4)),
            win_rate=float(match.group(5)),
            pnl=float(match.group(6)),
            pf=float(match.group(7)),
            is_baseline=match.group(8) is not None,
        ))
    return results


# ─── Plateau detection ─────────────────────────────────────────────

def _find_neighbors(target: GridResult, all_results: list[GridResult]) -> list[GridResult]:
    """Find combos that share the same TP scale or same SL."""
    neighbors: list[GridResult] = []
    for r in all_results:
        if r == target:
            continue
        same_tp = (r.tp1 == target.tp1 and r.tp2 == target.tp2)
        same_sl = (r.sl == target.sl)
        if same_tp or same_sl:
            neighbors.append(r)
    return neighbors


def _neighbor_stability(target: GridResult, neighbors: list[GridResult]) -> float:
    """How stable is the PF across neighbors? 1.0 = perfectly stable."""
    if not neighbors:
        return 0.0
    pfs = [n.pf for n in neighbors]
    avg_pf = sum(pfs) / len(pfs)
    if avg_pf == 0:
        return 0.0
    deviations = [abs(pf - target.pf) / max(avg_pf, 0.01) for pf in pfs]
    avg_deviation = sum(deviations) / len(deviations)
    return max(0.0, 1.0 - avg_deviation)


def score_results(results: list[GridResult]) -> list[tuple[GridResult, float, float, float]]:
    """
    Score each combo: (result, total_score, pf_component, stability_component)
    Score = PF × 0.6 + stability × 0.4
    """
    scored: list[tuple[GridResult, float, float, float]] = []
    for r in results:
        neighbors = _find_neighbors(r, results)
        stability = _neighbor_stability(r, neighbors)
        pf_norm = r.pf  # PF already on ~0-2 scale
        total = pf_norm * 0.6 + stability * 0.4
        scored.append((r, total, pf_norm, stability))
    return sorted(scored, key=lambda x: x[1], reverse=True)


# ─── Report generation ─────────────────────────────────────────────

ENTRY_BASELINES = {
    "standard_m15": {"tp1": 20, "tp2": 40, "sl": 20},
    "standard_h1":  {"tp1": 30, "tp2": 60, "sl": 25},
    "standard_h4":  {"tp1": 50, "tp2": 100, "sl": 40},
    "ema610_h1":    {"tp1": 40, "tp2": 80, "sl": 30},
    "ema610_h4":    {"tp1": 60, "tp2": 120, "sl": 50},
}


def _multiplier_str(value: float, baseline: float) -> str:
    mult = value / baseline if baseline else 0
    return f"{mult:.1f}x"


def generate_report(log_dir: Path) -> str:
    lines: list[str] = []
    lines.append("# TP/SL Grid Search — Plateau Analysis Report")
    lines.append(f"**Period**: BTC 2025-01-01 → 2026-02-20 (14 months)")
    lines.append(f"**Grid**: 5 TP scales × 5 SL levels = 25 combos/type (18 completed)")
    lines.append(f"**Methodology**: Score = PF × 0.6 + neighbor_stability × 0.4")
    lines.append("")

    all_entry_results: dict[str, list[GridResult]] = {}

    for log_file in sorted(log_dir.glob("log_*.txt")):
        entry_type = log_file.stem.replace("log_", "")
        results = parse_log(log_file)
        if not results:
            continue
        all_entry_results[entry_type] = results

        baseline_cfg = ENTRY_BASELINES.get(entry_type, {})
        baseline = next((r for r in results if r.is_baseline), None)
        scored = score_results(results)

        lines.append(f"---")
        lines.append(f"## {entry_type}")
        lines.append(f"**Baseline**: TP1={baseline_cfg.get('tp1')}, TP2={baseline_cfg.get('tp2')}, "
                      f"SL={baseline_cfg.get('sl')}")
        if baseline:
            lines.append(f"**Baseline PF**: {baseline.pf:.2f} | WR: {baseline.win_rate:.1f}% | "
                          f"PNL: ${baseline.pnl:+.2f} | Trades: {baseline.trades}")
        lines.append(f"**Combos tested**: {len(results)}")
        lines.append("")

        # Top 5 by plateau score
        lines.append("### Top 5 by Plateau Score")
        lines.append("| Rank | TP1 | TP2 | SL | TP mult | SL mult | Trades | WR | PNL | PF | Stability | Score |")
        lines.append("|------|-----|-----|----|---------|---------|--------|----|-----|----|-----------|-------|")
        for i, (r, score, pf_comp, stab) in enumerate(scored[:5], 1):
            tp_m = _multiplier_str(r.tp1, baseline_cfg.get("tp1", 1))
            sl_m = _multiplier_str(r.sl, baseline_cfg.get("sl", 1))
            marker = " ★" if r.is_baseline else ""
            lines.append(
                f"| {i} | {r.tp1:.0f} | {r.tp2:.0f} | {r.sl:.0f} | {tp_m} | {sl_m} | "
                f"{r.trades} | {r.win_rate:.1f}% | ${r.pnl:+.0f} | **{r.pf:.2f}** | "
                f"{stab:.2f} | **{score:.3f}**{marker} |"
            )
        lines.append("")

        # All results sorted by PF (heatmap-style)
        lines.append("### Full Grid (sorted by PF)")
        lines.append("| TP1 | TP2 | SL | TP mult | SL mult | Trades | WR | PNL | PF | Notes |")
        lines.append("|-----|-----|----|---------|---------|--------|----|-----|----|----|")
        for r in sorted(results, key=lambda x: x.pf, reverse=True):
            tp_m = _multiplier_str(r.tp1, baseline_cfg.get("tp1", 1))
            sl_m = _multiplier_str(r.sl, baseline_cfg.get("sl", 1))
            notes = ""
            if r.is_baseline:
                notes = "★ BASELINE"
            elif r.pf > (baseline.pf if baseline else 0) * 1.1:
                notes = "▲ Better"
            elif r.pf < (baseline.pf if baseline else 0) * 0.9:
                notes = "▼ Worse"
            lines.append(
                f"| {r.tp1:.0f} | {r.tp2:.0f} | {r.sl:.0f} | {tp_m} | {sl_m} | "
                f"{r.trades} | {r.win_rate:.1f}% | ${r.pnl:+.0f} | {r.pf:.2f} | {notes} |"
            )
        lines.append("")

        # SL sensitivity analysis (grouping by TP scale)
        lines.append("### SL Sensitivity (grouped by TP scale)")
        tp_groups: dict[tuple[float, float], list[GridResult]] = {}
        for r in results:
            key = (r.tp1, r.tp2)
            tp_groups.setdefault(key, []).append(r)

        for (tp1, tp2), group in sorted(tp_groups.items()):
            group_sorted = sorted(group, key=lambda x: x.sl)
            tp_m = _multiplier_str(tp1, baseline_cfg.get("tp1", 1))
            pfs_str = " → ".join(f"SL={r.sl:.0f}:{r.pf:.2f}" for r in group_sorted)
            pf_values = [r.pf for r in group_sorted]
            pf_range = max(pf_values) - min(pf_values) if pf_values else 0
            trend = "FLAT ✓" if pf_range < 0.10 else ("RISING" if pf_values[-1] > pf_values[0] else "FALLING")
            lines.append(f"- **TP {tp_m}** ({tp1:.0f}/{tp2:.0f}): {pfs_str} | Range: {pf_range:.2f} | {trend}")
        lines.append("")

    # ─── Cross-entry summary ─────────────────────────
    lines.append("---")
    lines.append("## Cross-Entry Summary")
    lines.append("")
    lines.append("| Entry Type | Baseline PF | Best PF | Best Params | Improvement | Recommended |")
    lines.append("|------------|-------------|---------|-------------|-------------|-------------|")

    for entry_type, results in all_entry_results.items():
        baseline_cfg = ENTRY_BASELINES.get(entry_type, {})
        baseline = next((r for r in results if r.is_baseline), None)
        scored = score_results(results)
        best = scored[0][0] if scored else None

        if baseline and best:
            improvement = ((best.pf - baseline.pf) / baseline.pf * 100) if baseline.pf > 0 else 0
            tp_m = _multiplier_str(best.tp1, baseline_cfg.get("tp1", 1))
            sl_m = _multiplier_str(best.sl, baseline_cfg.get("sl", 1))

            # Recommend if: PF > 1.0, improvement > 5%, and trades > 50
            recommend = "✅ YES" if (best.pf > 1.0 and improvement > 5 and best.trades > 50) else "❌ NO"
            if best.pf < 1.0:
                recommend = "❌ Losing"

            lines.append(
                f"| {entry_type} | {baseline.pf:.2f} | **{best.pf:.2f}** | "
                f"TP1={best.tp1:.0f} TP2={best.tp2:.0f} SL={best.sl:.0f} ({tp_m} TP, {sl_m} SL) | "
                f"{improvement:+.1f}% | {recommend} |"
            )

    lines.append("")

    # ─── Key findings ─────────────────────────
    lines.append("## Key Findings")
    lines.append("")

    # Analyze SL trend across all entries
    wider_sl_helps = 0
    total_entries = 0
    for entry_type, results in all_entry_results.items():
        baseline = next((r for r in results if r.is_baseline), None)
        if not baseline:
            continue
        total_entries += 1
        # Compare baseline SL vs wider SL at same TP
        same_tp = [r for r in results if r.tp1 == baseline.tp1 and r.tp2 == baseline.tp2 and r.sl > baseline.sl]
        if same_tp and any(r.pf > baseline.pf for r in same_tp):
            wider_sl_helps += 1

    lines.append(f"1. **Wider SL**: Helps in {wider_sl_helps}/{total_entries} entry types at same TP level")

    # Analyze TP trend
    bigger_tp_helps = 0
    for entry_type, results in all_entry_results.items():
        baseline = next((r for r in results if r.is_baseline), None)
        if not baseline:
            continue
        bigger_tp = [r for r in results if r.tp1 > baseline.tp1 and r.sl == baseline.sl]
        if bigger_tp and any(r.pf > baseline.pf for r in bigger_tp):
            bigger_tp_helps += 1

    lines.append(f"2. **Bigger TP**: Helps in {bigger_tp_helps}/{total_entries} entry types at same SL level")
    lines.append("")

    # Profitable vs unprofitable
    profitable = [et for et, rs in all_entry_results.items()
                  if any(r.pf > 1.0 for r in rs)]
    unprofitable = [et for et, rs in all_entry_results.items()
                    if all(r.pf < 1.0 for r in rs)]

    lines.append(f"3. **Profitable entries** (PF > 1.0 in at least one config): {', '.join(profitable) or 'None'}")
    lines.append(f"4. **Always losing** (PF < 1.0 in ALL configs): {', '.join(unprofitable) or 'None'}")
    lines.append("")

    # Sample size warning
    low_sample = []
    for entry_type, results in all_entry_results.items():
        min_trades = min(r.trades for r in results)
        if min_trades < 50:
            low_sample.append(f"{entry_type} (min {min_trades} trades)")
    if low_sample:
        lines.append(f"⚠️ **Low sample size warning**: {', '.join(low_sample)}")
        lines.append("")

    return "\n".join(lines)


# ─── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    log_dir = Path(__file__).parent / "output" / "optimization"
    report = generate_report(log_dir)

    output_path = log_dir / "analysis_report.md"
    output_path.write_text(report)
    print(report)
    print(f"\n✅ Report saved to {output_path}")
