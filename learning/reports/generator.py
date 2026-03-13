"""Generate markdown report and JSON suggestions."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from learning.adaptive.stats import SegmentStats, StatsAnalyzer
from learning.data.trade_reader import Trade

logger = logging.getLogger(__name__)


def _format_pnl(value: float) -> str:
    """Format PNL with + prefix for positive values."""
    prefix = "+" if value > 0 else ""
    return f"{prefix}${value:.2f}"


def _format_pct(value: float) -> str:
    """Format percentage with + prefix for positive values."""
    prefix = "+" if value > 0 else ""
    return f"{prefix}{value:.1f}%"


def _segment_table(segments: list[SegmentStats]) -> str:
    """Render a list of SegmentStats as a markdown table."""
    if not segments:
        return "*No data*\n"

    lines = [
        "| Segment | Trades | Win Rate | PNL | Avg PNL | PF | TP1% | TP2% | SL% | CE% |",
        "|---------|--------|----------|-----|---------|-----|------|------|-----|-----|",
    ]
    for s in segments:
        pf = f"{s.profit_factor:.1f}" if s.profit_factor != float("inf") else "inf"
        lines.append(
            f"| {s.label} | {s.count} | {s.win_rate}% | "
            f"{_format_pnl(s.total_pnl)} | {_format_pnl(s.avg_pnl)} | {pf} | "
            f"{s.tp1_hit_rate}% | {s.tp2_hit_rate}% | "
            f"{s.hard_sl_rate}% | {s.chandelier_rate}% |"
        )
    return "\n".join(lines) + "\n"


def _trade_table(trades: list[Trade], title: str) -> str:
    """Render a list of trades as markdown table."""
    if not trades:
        return f"### {title}\n*No data*\n"

    lines = [
        f"### {title}",
        "| Symbol | Side | Entry Type | PNL | ROI | Close Reason | Duration |",
        "|--------|------|------------|-----|-----|--------------|----------|",
    ]
    for t in trades:
        dur = f"{t.duration_hours:.1f}h" if t.duration_hours else "N/A"
        lines.append(
            f"| {t.symbol} | {t.side} | {t.entry_type} | "
            f"{_format_pnl(t.pnl_usd)} | {_format_pct(t.roi_percent)} | "
            f"{t.close_reason} | {dur} |"
        )
    return "\n".join(lines) + "\n"


class ReportGenerator:
    """Generate weekly learning report."""

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        analyzer: StatsAnalyzer,
        period: str,
        reflection_text: Optional[str] = None,
        patterns: Optional[list[dict]] = None,
        suggestions: Optional[list[dict]] = None,
    ) -> tuple[Path, Path]:
        """Generate markdown report + JSON suggestions.

        Returns (report_path, json_path).
        """
        overall = analyzer.overall
        streaks = analyzer.streak_analysis()
        tp_eff = analyzer.tp_efficiency()
        sl_eff = analyzer.sl_efficiency()

        # ── Build markdown ───────────────────────────────────
        sections = []

        # Header
        sections.append(
            f"# Trading Learning Report — {period}\n\n"
            f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"
        )

        # Overall summary
        pf = f"{overall.profit_factor:.1f}" if overall.profit_factor != float("inf") else "inf"
        sections.append(
            "## Performance Summary\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Trades | {overall.count} |\n"
            f"| Win Rate | {overall.win_rate}% |\n"
            f"| Total PNL | {_format_pnl(overall.total_pnl)} |\n"
            f"| Avg PNL/Trade | {_format_pnl(overall.avg_pnl)} |\n"
            f"| Avg ROI | {_format_pct(overall.avg_roi)} |\n"
            f"| Profit Factor | {pf} |\n"
            f"| Max Win Streak | {streaks['max_win_streak']} |\n"
            f"| Max Loss Streak | {streaks['max_loss_streak']} |\n"
            f"| Avg Duration | {overall.avg_duration_hours}h |\n"
        )

        # By entry type
        sections.append("## By Entry Type\n\n")
        sections.append(_segment_table(analyzer.by_entry_type()))

        # By symbol
        sections.append("## By Symbol\n\n")
        sections.append(_segment_table(analyzer.by_symbol()))

        # By side
        sections.append("## By Side (BUY vs SELL)\n\n")
        sections.append(_segment_table(analyzer.by_side()))

        # By close reason
        sections.append("## By Close Reason\n\n")
        sections.append(_segment_table(analyzer.by_close_reason()))

        # TP efficiency
        if tp_eff:
            sections.append("## TP Efficiency\n\n")
            lines = [
                "| Entry Type | Total | TP1 Hit% | TP2 Hit% | TP1 Only% | Avg PNL@TP1 | Avg PNL@TP2 |",
                "|------------|-------|----------|----------|-----------|-------------|-------------|",
            ]
            for et, data in tp_eff.items():
                lines.append(
                    f"| {et} | {data['total']} | {data['tp1_hit_rate']}% | "
                    f"{data['tp2_hit_rate']}% | {data['tp1_only_rate']}% | "
                    f"{_format_pnl(data['avg_pnl_tp1'])} | {_format_pnl(data['avg_pnl_tp2'])} |"
                )
            sections.append("\n".join(lines) + "\n")

        # SL efficiency
        sections.append(
            "## SL Efficiency\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Hard SL Count | {sl_eff['hard_sl_count']} |\n"
            f"| Hard SL Avg Loss | {_format_pnl(sl_eff['hard_sl_avg_loss'])} |\n"
            f"| Chandelier Count | {sl_eff['chandelier_count']} |\n"
            f"| Chandelier Avg PNL | {_format_pnl(sl_eff['chandelier_avg_pnl'])} |\n"
            f"| Chandelier Win Rate | {sl_eff['chandelier_win_rate']}% |\n"
        )

        # Best / worst trades
        sections.append(_trade_table(analyzer.best_trades(5), "Best Trades"))
        sections.append(_trade_table(analyzer.worst_trades(5), "Worst Trades"))

        # LLM reflection
        if reflection_text:
            sections.append(f"## LLM Reflection\n\n{reflection_text}\n")

        # Patterns (accepts Pattern dataclass or dict)
        if patterns:
            sections.append("## Detected Patterns\n\n")
            for p in patterns:
                if hasattr(p, "category"):
                    # Pattern dataclass
                    conf = f"{p.confidence:.0%}" if hasattr(p, "confidence") else ""
                    sections.append(
                        f"- **[{p.category}]** {p.label} "
                        f"(confidence: {conf}, n={p.sample_size}, "
                        f"PNL impact: {_format_pnl(p.impact_pnl)})\n"
                        f"  {p.detail}\n"
                    )
                else:
                    sections.append(f"- **{p.get('type', 'unknown')}**: {p.get('description', '')}\n")
            sections.append("")

        # Suggestions
        if suggestions:
            sections.append("## Parameter Suggestions\n\n")
            lines = [
                "| Parameter | Current | Suggested | Confidence | Validated |",
                "|-----------|---------|-----------|------------|-----------|",
            ]
            for s in suggestions:
                validated = "YES" if s.get("backtest_validated") else "NO"
                lines.append(
                    f"| {s['config_key']} | {s['current_value']} | "
                    f"{s['suggested_value']} | {s['confidence']:.0%} | {validated} |"
                )
            sections.append("\n".join(lines) + "\n")

        # ── Write files ──────────────────────────────────────
        report_content = "\n".join(sections)
        report_path = self._output_dir / f"report_{period}.md"
        report_path.write_text(report_content, encoding="utf-8")

        json_data = {
            "period": period,
            "generated_at": datetime.now().isoformat(),
            "overall": {
                "trades": overall.count,
                "win_rate": overall.win_rate,
                "total_pnl": overall.total_pnl,
                "profit_factor": overall.profit_factor if overall.profit_factor != float("inf") else None,
            },
            "suggestions": suggestions or [],
        }
        json_path = self._output_dir / f"suggestions_{period}.json"
        json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")

        logger.info("Report written to %s", report_path)
        logger.info("Suggestions written to %s", json_path)
        return report_path, json_path
