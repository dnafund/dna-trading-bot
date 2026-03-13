"""Generate conservative parameter suggestions from trade statistics."""

import logging
from dataclasses import dataclass
from typing import Optional

from learning.adaptive.stats import StatsAnalyzer
from learning.config import (
    MAX_PARAM_CHANGE_PCT,
    MIN_CONFIDENCE,
    MIN_TRADES_FOR_SUGGESTION,
)
from learning.data.config_reader import ConfigReader

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParameterSuggestion:
    """A conservative parameter adjustment suggestion."""

    config_key: str
    current_value: float
    suggested_value: float
    change_pct: float
    confidence: float
    reason: str
    category: str  # "tp", "sl", "chandelier", "general"


def _clamp_change(current: float, suggested: float, max_pct: float) -> float:
    """Clamp the suggested value to max_pct change from current."""
    if current == 0:
        return suggested
    max_change = abs(current * max_pct)
    delta = suggested - current
    clamped_delta = max(-max_change, min(max_change, delta))
    return round(current + clamped_delta, 4)


class SuggestionEngine:
    """Generate parameter suggestions from statistical analysis.

    Rules:
    - Minimum 30 trades sample size
    - Minimum 0.6 confidence score
    - Maximum 20% change from current value
    """

    def __init__(self, analyzer: StatsAnalyzer, config_reader: ConfigReader):
        self._analyzer = analyzer
        self._config = config_reader
        self._exit_params = config_reader.get_exit_params()

    def generate_all(self) -> list[ParameterSuggestion]:
        """Generate all applicable suggestions."""
        overall = self._analyzer.overall
        if overall.count < MIN_TRADES_FOR_SUGGESTION:
            logger.info(
                "Only %d trades (need %d), skipping suggestions.",
                overall.count, MIN_TRADES_FOR_SUGGESTION,
            )
            return []

        suggestions: list[ParameterSuggestion] = []
        suggestions.extend(self._tp_suggestions())
        suggestions.extend(self._sl_suggestions())
        suggestions.extend(self._chandelier_suggestions())

        # Filter by confidence
        return [s for s in suggestions if s.confidence >= MIN_CONFIDENCE]

    # ── TP Suggestions ────────────────────────────────────────────

    def _tp_suggestions(self) -> list[ParameterSuggestion]:
        """Suggest TP adjustments based on hit rates."""
        suggestions = []
        tp_eff = self._analyzer.tp_efficiency()

        for entry_type, data in tp_eff.items():
            total = data["total"]
            if total < 15:
                continue

            tp1_rate = data["tp1_hit_rate"]
            tp2_rate = data["tp2_hit_rate"]

            # TP1 hit rate too low → target too aggressive
            if tp1_rate < 15:
                timeframe = entry_type.split("_")[-1] if "_" in entry_type else "m15"
                key = f"standard_{timeframe}_tp1"
                current = self._exit_params.get(key)
                if current is not None:
                    suggested = _clamp_change(
                        current, current * 0.85, MAX_PARAM_CHANGE_PCT
                    )
                    confidence = min(total / 50, 1.0) * 0.7
                    suggestions.append(ParameterSuggestion(
                        config_key=key,
                        current_value=current,
                        suggested_value=suggested,
                        change_pct=round((suggested - current) / abs(current) * 100, 1) if current else 0,
                        confidence=round(confidence, 2),
                        reason=(
                            f"TP1 hit rate only {tp1_rate}% for {entry_type} "
                            f"({total} trades). Reducing target to capture more wins."
                        ),
                        category="tp",
                    ))

            # TP2 very rarely hit → too aggressive
            if tp2_rate < 5 and tp1_rate > 10:
                timeframe = entry_type.split("_")[-1] if "_" in entry_type else "m15"
                key = f"standard_{timeframe}_tp2"
                current = self._exit_params.get(key)
                if current is not None:
                    suggested = _clamp_change(
                        current, current * 0.80, MAX_PARAM_CHANGE_PCT
                    )
                    confidence = min(total / 40, 1.0) * 0.65
                    suggestions.append(ParameterSuggestion(
                        config_key=key,
                        current_value=current,
                        suggested_value=suggested,
                        change_pct=round((suggested - current) / abs(current) * 100, 1) if current else 0,
                        confidence=round(confidence, 2),
                        reason=(
                            f"TP2 hit rate only {tp2_rate}% for {entry_type} "
                            f"({total} trades). Target appears too aggressive."
                        ),
                        category="tp",
                    ))

        return suggestions

    # ── SL Suggestions ────────────────────────────────────────────

    def _sl_suggestions(self) -> list[ParameterSuggestion]:
        """Suggest SL adjustments based on stop loss efficiency."""
        suggestions = []
        sl_eff = self._analyzer.sl_efficiency()

        hard_sl_count = sl_eff["hard_sl_count"]
        hard_sl_avg = sl_eff["hard_sl_avg_loss"]
        overall = self._analyzer.overall

        if hard_sl_count < 5:
            return suggestions

        # Hard SL avg loss much larger than avg win
        avg_win_pnl = (
            sum(t.pnl_usd for t in self._analyzer._trades if t.pnl_usd > 0)
            / max(sum(1 for t in self._analyzer._trades if t.pnl_usd > 0), 1)
        )

        if abs(hard_sl_avg) > avg_win_pnl * 3:
            # SL is too wide — losers are much larger than winners
            for tf in ("m15", "h1", "h4"):
                key = f"standard_{tf}_hard_sl"
                current = self._exit_params.get(key)
                if current is not None and current < 0:
                    # Tighten by 15% (make less negative = tighter)
                    suggested = _clamp_change(
                        current, current * 0.85, MAX_PARAM_CHANGE_PCT
                    )
                    confidence = min(hard_sl_count / 10, 1.0) * 0.7
                    suggestions.append(ParameterSuggestion(
                        config_key=key,
                        current_value=current,
                        suggested_value=suggested,
                        change_pct=round((suggested - current) / abs(current) * 100, 1),
                        confidence=round(confidence, 2),
                        reason=(
                            f"Hard SL avg loss ${hard_sl_avg:.2f} is "
                            f"{abs(hard_sl_avg / avg_win_pnl):.1f}x larger than avg win "
                            f"${avg_win_pnl:.2f}. Tightening to improve R:R ratio."
                        ),
                        category="sl",
                    ))

        return suggestions

    # ── Chandelier Suggestions ────────────────────────────────────

    def _chandelier_suggestions(self) -> list[ParameterSuggestion]:
        """Suggest Chandelier Exit adjustments."""
        suggestions = []
        sl_eff = self._analyzer.sl_efficiency()
        overall = self._analyzer.overall

        ce_count = sl_eff["chandelier_count"]
        ce_avg_pnl = sl_eff["chandelier_avg_pnl"]
        ce_win_rate = sl_eff["chandelier_win_rate"]

        if ce_count < 10:
            return suggestions

        ce_ratio = ce_count / max(overall.count, 1)

        # Chandelier exits too frequent (> 30%) with low avg PNL
        if ce_ratio > 0.30 and ce_avg_pnl < 1.0:
            current_mult = self._exit_params.get("chandelier_multiplier", 1.75)
            # Increase multiplier to give more room
            suggested = _clamp_change(
                current_mult, current_mult * 1.10, MAX_PARAM_CHANGE_PCT
            )
            confidence = min(ce_count / 30, 1.0) * 0.7
            suggestions.append(ParameterSuggestion(
                config_key="chandelier_multiplier",
                current_value=current_mult,
                suggested_value=suggested,
                change_pct=round((suggested - current_mult) / current_mult * 100, 1),
                confidence=round(confidence, 2),
                reason=(
                    f"Chandelier exits {ce_ratio:.0%} of trades with avg PNL "
                    f"${ce_avg_pnl:+.2f}. Increasing multiplier to give trends "
                    f"more room to develop."
                ),
                category="chandelier",
            ))

        # Chandelier win rate low — too sensitive
        if ce_win_rate < 40 and ce_count >= 15:
            current_period = self._exit_params.get("chandelier_period", 34)
            # Increase period for smoother trailing
            suggested = _clamp_change(
                current_period, current_period * 1.15, MAX_PARAM_CHANGE_PCT
            )
            confidence = min(ce_count / 25, 1.0) * 0.65
            suggestions.append(ParameterSuggestion(
                config_key="chandelier_period",
                current_value=current_period,
                suggested_value=suggested,
                change_pct=round((suggested - current_period) / current_period * 100, 1),
                confidence=round(confidence, 2),
                reason=(
                    f"Chandelier win rate only {ce_win_rate}% ({ce_count} trades). "
                    f"Increasing period for smoother trailing stop."
                ),
                category="chandelier",
            ))

        return suggestions
