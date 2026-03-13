"""Build weekly context and run LLM reflection."""

import logging
from typing import Optional

from learning.adaptive.stats import StatsAnalyzer
from learning.reflection.llm_client import LLMClient
from learning.reflection.prompts import SYSTEM_PROMPT, build_reflection_prompt

logger = logging.getLogger(__name__)


def _format_stats_for_llm(analyzer: StatsAnalyzer, label: str) -> str:
    """Format StatsAnalyzer output as concise text for LLM context."""
    overall = analyzer.overall
    streaks = analyzer.streak_analysis()

    lines = [f"### {label}"]
    lines.append(
        f"Trades: {overall.count}, Win Rate: {overall.win_rate}%, "
        f"PNL: ${overall.total_pnl:+.2f}, Avg PNL: ${overall.avg_pnl:+.2f}, "
        f"PF: {overall.profit_factor}, Avg Duration: {overall.avg_duration_hours}h"
    )
    lines.append(
        f"Max Win Streak: {streaks['max_win_streak']}, "
        f"Max Loss Streak: {streaks['max_loss_streak']}"
    )

    # Entry types
    lines.append("\n**By Entry Type:**")
    for s in analyzer.by_entry_type():
        lines.append(
            f"- {s.label}: {s.count} trades, {s.win_rate}% WR, "
            f"${s.total_pnl:+.2f}, PF={s.profit_factor}"
        )

    # Top/bottom symbols
    symbols = analyzer.by_symbol()
    if symbols:
        lines.append("\n**Top Symbols:**")
        for s in symbols[:5]:
            lines.append(
                f"- {s.label}: {s.count} trades, {s.win_rate}% WR, "
                f"${s.total_pnl:+.2f}"
            )
        lines.append("\n**Bottom Symbols:**")
        for s in symbols[-3:]:
            lines.append(
                f"- {s.label}: {s.count} trades, {s.win_rate}% WR, "
                f"${s.total_pnl:+.2f}"
            )

    # Sides
    lines.append("\n**By Side:**")
    for s in analyzer.by_side():
        lines.append(
            f"- {s.label}: {s.count} trades, {s.win_rate}% WR, "
            f"${s.total_pnl:+.2f}"
        )

    # Close reasons
    lines.append("\n**By Close Reason:**")
    for s in analyzer.by_close_reason():
        lines.append(
            f"- {s.label}: {s.count} trades, {s.win_rate}% WR, "
            f"${s.total_pnl:+.2f}"
        )

    # TP/SL efficiency
    tp_eff = analyzer.tp_efficiency()
    if tp_eff:
        lines.append("\n**TP Efficiency:**")
        for et, data in tp_eff.items():
            lines.append(
                f"- {et}: TP1 hit {data['tp1_hit_rate']}%, "
                f"TP2 hit {data['tp2_hit_rate']}%, "
                f"TP1-only {data['tp1_only_rate']}%"
            )

    sl_eff = analyzer.sl_efficiency()
    lines.append(
        f"\n**SL Efficiency:** Hard SL: {sl_eff['hard_sl_count']} trades, "
        f"avg loss ${sl_eff['hard_sl_avg_loss']:+.2f} | "
        f"Chandelier: {sl_eff['chandelier_count']} trades, "
        f"avg PNL ${sl_eff['chandelier_avg_pnl']:+.2f}, "
        f"{sl_eff['chandelier_win_rate']}% WR"
    )

    return "\n".join(lines)


class ReflectionAnalyzer:
    """Build context and run LLM reflection on trading performance."""

    def __init__(self, llm: LLMClient):
        self._llm = llm

    def reflect(
        self,
        period_analyzer: StatsAnalyzer,
        lifetime_analyzer: StatsAnalyzer,
        patterns_text: Optional[str] = None,
        config_text: Optional[str] = None,
    ) -> str:
        """Run LLM reflection on recent trading performance.

        Args:
            period_analyzer: Stats for the analysis period (e.g., last 7 days).
            lifetime_analyzer: Stats for all time (context).
            patterns_text: Optional pre-formatted patterns text.
            config_text: Optional pre-formatted config text.

        Returns:
            LLM reflection text.
        """
        period_stats = _format_stats_for_llm(period_analyzer, "Recent Period")
        lifetime_stats = _format_stats_for_llm(lifetime_analyzer, "Lifetime")

        prompt = build_reflection_prompt(
            period_stats=period_stats,
            lifetime_stats=lifetime_stats,
            patterns=patterns_text or "No patterns detected yet.",
            current_config=config_text or "Config not available.",
        )

        logger.info("Sending reflection prompt to LLM (%d chars)", len(prompt))
        response = self._llm.generate(SYSTEM_PROMPT, prompt)
        logger.info("Received reflection response (%d chars)", len(response))

        return response
