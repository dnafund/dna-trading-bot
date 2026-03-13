"""Pattern detection from trade history — time, entry combos, streaks."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from learning.data.trade_reader import Trade
from learning.memory.store import MemoryStore


def _is_win(trade: Trade) -> bool:
    """Determine win/loss by PNL, not close_reason (more accurate)."""
    return trade.pnl_usd > 0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Pattern:
    """A detected trading pattern."""

    category: str       # e.g. "time_of_day", "entry_type", "streak", "symbol"
    label: str          # Human-readable description
    detail: str         # Detailed explanation
    confidence: float   # 0.0 - 1.0
    sample_size: int
    impact_pnl: float   # Total PNL impact


class PatternDetector:
    """Detect recurring patterns from trade history and memory."""

    def __init__(self, memory: MemoryStore, trades: list[Trade]):
        self._memory = memory
        self._trades = trades

    def detect_all(self) -> list[Pattern]:
        """Run all pattern detectors and return significant findings."""
        patterns: list[Pattern] = []
        patterns.extend(self._time_of_day_patterns())
        patterns.extend(self._day_of_week_patterns())
        patterns.extend(self._entry_type_patterns())
        patterns.extend(self._duration_patterns())
        patterns.extend(self._streak_patterns())
        patterns.extend(self._symbol_concentration_patterns())
        patterns.extend(self._close_reason_patterns())

        # Sort by absolute PNL impact (most significant first)
        return sorted(patterns, key=lambda p: abs(p.impact_pnl), reverse=True)

    # ── Time-of-Day ───────────────────────────────────────────────

    def _time_of_day_patterns(self) -> list[Pattern]:
        """Detect patterns based on trade entry hour (UTC)."""
        hour_buckets: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            if not t.entry_time:
                continue
            hour = t.entry_time.hour
            if 0 <= hour < 6:
                bucket = "00-06 (Asia close)"
            elif 6 <= hour < 12:
                bucket = "06-12 (EU open)"
            elif 12 <= hour < 18:
                bucket = "12-18 (US open)"
            else:
                bucket = "18-24 (US close)"
            hour_buckets[bucket].append(t)

        patterns = []
        for bucket, trades in hour_buckets.items():
            if len(trades) < 5:
                continue

            wins = sum(1 for t in trades if _is_win(t))
            total_pnl = sum(t.pnl_usd for t in trades)
            win_rate = wins / len(trades) if trades else 0

            # Flag poor-performing time windows
            if win_rate < 0.40 and total_pnl < 0:
                patterns.append(Pattern(
                    category="time_of_day",
                    label=f"Weak period: {bucket}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} PNL. Consider reducing exposure."
                    ),
                    confidence=min(len(trades) / 30, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))
            elif win_rate > 0.60 and total_pnl > 0:
                patterns.append(Pattern(
                    category="time_of_day",
                    label=f"Strong period: {bucket}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} PNL. Favorable conditions."
                    ),
                    confidence=min(len(trades) / 30, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

        return patterns

    # ── Day-of-Week ───────────────────────────────────────────────

    def _day_of_week_patterns(self) -> list[Pattern]:
        """Detect which days perform best/worst."""
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        day_buckets: dict[int, list[Trade]] = defaultdict(list)

        for t in self._trades:
            if not t.entry_time:
                continue
            day_buckets[t.entry_time.weekday()].append(t)

        patterns = []
        for dow, trades in day_buckets.items():
            if len(trades) < 5:
                continue

            wins = sum(1 for t in trades if _is_win(t))
            total_pnl = sum(t.pnl_usd for t in trades)
            win_rate = wins / len(trades)

            if win_rate < 0.35 and total_pnl < 0:
                patterns.append(Pattern(
                    category="day_of_week",
                    label=f"Worst day: {day_names[dow]}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} PNL."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))
            elif win_rate > 0.65 and total_pnl > 0:
                patterns.append(Pattern(
                    category="day_of_week",
                    label=f"Best day: {day_names[dow]}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} PNL."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

        return patterns

    # ── Entry Type Combos ─────────────────────────────────────────

    def _entry_type_patterns(self) -> list[Pattern]:
        """Detect entry type + side combinations that over/underperform."""
        combos: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            key = f"{t.entry_type}_{t.side}"
            combos[key].append(t)

        avg_pnl_all = (
            sum(t.pnl_usd for t in self._trades) / len(self._trades)
            if self._trades else 0
        )

        patterns = []
        for combo, trades in combos.items():
            if len(trades) < 5:
                continue

            wins = sum(1 for t in trades if _is_win(t))
            total_pnl = sum(t.pnl_usd for t in trades)
            avg_pnl = total_pnl / len(trades)
            win_rate = wins / len(trades)

            # Significantly worse than average
            if avg_pnl < avg_pnl_all * 1.5 and total_pnl < -20:
                patterns.append(Pattern(
                    category="entry_type",
                    label=f"Underperforming: {combo}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"avg ${avg_pnl:+.2f}/trade vs overall ${avg_pnl_all:+.2f}."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))
            elif avg_pnl > 0 and total_pnl > 20:
                patterns.append(Pattern(
                    category="entry_type",
                    label=f"Outperforming: {combo}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"avg ${avg_pnl:+.2f}/trade."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

        return patterns

    # ── Duration ──────────────────────────────────────────────────

    def _duration_patterns(self) -> list[Pattern]:
        """Detect if short/long-duration trades perform differently."""
        short: list[Trade] = []  # < 1h
        medium: list[Trade] = []  # 1-6h
        long: list[Trade] = []  # > 6h

        for t in self._trades:
            if t.duration_hours < 1:
                short.append(t)
            elif t.duration_hours < 6:
                medium.append(t)
            else:
                long.append(t)

        patterns = []
        for label, trades in [("< 1h", short), ("1-6h", medium), ("> 6h", long)]:
            if len(trades) < 5:
                continue

            wins = sum(1 for t in trades if _is_win(t))
            total_pnl = sum(t.pnl_usd for t in trades)
            win_rate = wins / len(trades)

            if total_pnl < -30:
                patterns.append(Pattern(
                    category="duration",
                    label=f"Losing duration: {label}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} total PNL."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))
            elif total_pnl > 30:
                patterns.append(Pattern(
                    category="duration",
                    label=f"Profitable duration: {label}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} total PNL."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

        return patterns

    # ── Streak ────────────────────────────────────────────────────

    def _streak_patterns(self) -> list[Pattern]:
        """Detect loss streaks and their characteristics."""
        if len(self._trades) < 10:
            return []

        # Build streak data
        streaks: list[dict] = []
        current_streak = 0
        streak_trades: list[Trade] = []

        for trade in self._trades:
            is_win = _is_win(trade)
            if not is_win:
                current_streak -= 1
                streak_trades.append(trade)
            else:
                if current_streak <= -3:
                    streaks.append({
                        "length": abs(current_streak),
                        "trades": list(streak_trades),
                        "total_pnl": sum(t.pnl_usd for t in streak_trades),
                    })
                current_streak = 0
                streak_trades = []

        # Check last streak
        if current_streak <= -3:
            streaks.append({
                "length": abs(current_streak),
                "trades": list(streak_trades),
                "total_pnl": sum(t.pnl_usd for t in streak_trades),
            })

        patterns = []
        if streaks:
            worst = max(streaks, key=lambda s: s["length"])
            total_streak_pnl = sum(s["total_pnl"] for s in streaks)

            # Analyze entry types during streaks
            streak_entry_types: dict[str, int] = defaultdict(int)
            for s in streaks:
                for t in s["trades"]:
                    streak_entry_types[t.entry_type] += 1

            dominant_entry = max(streak_entry_types, key=streak_entry_types.get) if streak_entry_types else "unknown"

            patterns.append(Pattern(
                category="streak",
                label=f"Loss streaks: {len(streaks)} occurrences (≥3)",
                detail=(
                    f"Worst streak: {worst['length']} losses (${worst['total_pnl']:+.2f}). "
                    f"Total streak damage: ${total_streak_pnl:+.2f}. "
                    f"Most common entry during streaks: {dominant_entry}."
                ),
                confidence=min(len(streaks) / 5, 1.0),
                sample_size=sum(s["length"] for s in streaks),
                impact_pnl=total_streak_pnl,
            ))

        return patterns

    # ── Symbol Concentration ──────────────────────────────────────

    def _symbol_concentration_patterns(self) -> list[Pattern]:
        """Detect symbols with concentrated losses or profits."""
        symbol_pnl: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            symbol_pnl[t.symbol].append(t)

        patterns = []
        for symbol, trades in symbol_pnl.items():
            if len(trades) < 3:
                continue

            total_pnl = sum(t.pnl_usd for t in trades)
            wins = sum(1 for t in trades if _is_win(t))
            win_rate = wins / len(trades)

            # Big losers
            if total_pnl < -50:
                patterns.append(Pattern(
                    category="symbol",
                    label=f"Bleeding symbol: {symbol}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} total PNL. Consider blacklisting."
                    ),
                    confidence=min(len(trades) / 10, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))
            elif total_pnl > 50:
                patterns.append(Pattern(
                    category="symbol",
                    label=f"Strong symbol: {symbol}",
                    detail=(
                        f"{len(trades)} trades, {win_rate:.0%} WR, "
                        f"${total_pnl:+.2f} total PNL."
                    ),
                    confidence=min(len(trades) / 10, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

        return patterns

    # ── Close Reason Analysis ─────────────────────────────────────

    def _close_reason_patterns(self) -> list[Pattern]:
        """Detect dominant close reasons and their efficiency."""
        reason_data: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            reason_data[t.close_reason].append(t)

        patterns = []
        total_trades = len(self._trades) if self._trades else 1

        for reason, trades in reason_data.items():
            ratio = len(trades) / total_trades
            total_pnl = sum(t.pnl_usd for t in trades)
            avg_pnl = total_pnl / len(trades) if trades else 0

            # Hard SL is dominant exit — SL too tight?
            if reason == "HARD_SL" and ratio > 0.30:
                patterns.append(Pattern(
                    category="close_reason",
                    label=f"High HARD_SL rate: {ratio:.0%}",
                    detail=(
                        f"{len(trades)} trades ({ratio:.0%} of all), "
                        f"avg loss ${avg_pnl:+.2f}. Consider widening stop loss."
                    ),
                    confidence=min(len(trades) / 20, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

            # Chandelier trigger — CE too tight?
            if reason in ("CHANDELIER_TRIGGER", "CHANDELIER_SL") and ratio > 0.20:
                avg_duration = (
                    sum(t.duration_hours for t in trades) / len(trades)
                    if trades else 0
                )
                patterns.append(Pattern(
                    category="close_reason",
                    label=f"Frequent Chandelier exits: {ratio:.0%}",
                    detail=(
                        f"{len(trades)} trades ({ratio:.0%}), avg duration {avg_duration:.1f}h, "
                        f"avg PNL ${avg_pnl:+.2f}. Chandelier may be too sensitive."
                    ),
                    confidence=min(len(trades) / 15, 1.0),
                    sample_size=len(trades),
                    impact_pnl=total_pnl,
                ))

            # TP2 hit rate low — TP2 too aggressive?
            if reason == "TP2" and ratio < 0.05 and len(self._trades) > 30:
                patterns.append(Pattern(
                    category="close_reason",
                    label=f"Very low TP2 hit rate: {ratio:.0%}",
                    detail=(
                        f"Only {len(trades)} TP2 exits out of {total_trades} trades. "
                        f"TP2 target may be too aggressive."
                    ),
                    confidence=0.6,
                    sample_size=len(trades),
                    impact_pnl=0,  # Opportunity cost, not direct loss
                ))

        return patterns
