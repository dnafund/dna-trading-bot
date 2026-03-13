"""Statistical analysis of trading performance."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Sequence

from learning.config import ENTRY_TYPES, WIN_REASONS
from learning.data.trade_reader import Trade

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SegmentStats:
    """Stats for a segment (entry_type, symbol, side, etc.)."""

    label: str
    count: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    avg_roi: float
    profit_factor: float
    avg_duration_hours: float
    tp1_hit_rate: float
    tp2_hit_rate: float
    hard_sl_rate: float
    chandelier_rate: float


def _calc_profit_factor(trades: Sequence[Trade]) -> float:
    """Profit factor = gross profit / gross loss."""
    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return round(gross_profit / gross_loss, 2)


def _calc_segment(label: str, trades: list[Trade]) -> SegmentStats:
    """Calculate stats for a list of trades."""
    if not trades:
        return SegmentStats(
            label=label, count=0, wins=0, losses=0, win_rate=0,
            total_pnl=0, avg_pnl=0, avg_roi=0, profit_factor=0,
            avg_duration_hours=0, tp1_hit_rate=0, tp2_hit_rate=0,
            hard_sl_rate=0, chandelier_rate=0,
        )

    n = len(trades)
    wins = [t for t in trades if t.close_reason in WIN_REASONS or t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0 and t.close_reason not in WIN_REASONS]

    tp1_count = sum(1 for t in trades if t.tp1_closed)
    tp2_count = sum(1 for t in trades if t.tp2_closed)
    hard_sl_count = sum(1 for t in trades if t.close_reason == "HARD_SL")
    ce_count = sum(1 for t in trades if t.close_reason in ("CHANDELIER_TRIGGER", "CHANDELIER_SL"))

    return SegmentStats(
        label=label,
        count=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / n * 100, 1),
        total_pnl=round(sum(t.pnl_usd for t in trades), 2),
        avg_pnl=round(sum(t.pnl_usd for t in trades) / n, 2),
        avg_roi=round(sum(t.roi_percent for t in trades) / n, 1),
        profit_factor=_calc_profit_factor(trades),
        avg_duration_hours=round(sum(t.duration_hours for t in trades) / n, 1),
        tp1_hit_rate=round(tp1_count / n * 100, 1),
        tp2_hit_rate=round(tp2_count / n * 100, 1),
        hard_sl_rate=round(hard_sl_count / n * 100, 1),
        chandelier_rate=round(ce_count / n * 100, 1),
    )


class StatsAnalyzer:
    """Analyze trading performance by various dimensions."""

    def __init__(self, trades: list[Trade]):
        self._trades = trades

    @property
    def overall(self) -> SegmentStats:
        """Overall stats across all trades."""
        return _calc_segment("Overall", self._trades)

    def by_entry_type(self) -> list[SegmentStats]:
        """Stats grouped by entry type."""
        groups: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            groups[t.entry_type].append(t)
        return [
            _calc_segment(et, groups.get(et, []))
            for et in ENTRY_TYPES
            if groups.get(et)
        ]

    def by_symbol(self) -> list[SegmentStats]:
        """Stats grouped by symbol."""
        groups: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            groups[t.symbol].append(t)
        return sorted(
            [_calc_segment(sym, trades) for sym, trades in groups.items()],
            key=lambda s: s.total_pnl,
            reverse=True,
        )

    def by_side(self) -> list[SegmentStats]:
        """Stats grouped by side (BUY/SELL)."""
        groups: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            groups[t.side].append(t)
        return [_calc_segment(side, trades) for side, trades in groups.items()]

    def by_close_reason(self) -> list[SegmentStats]:
        """Stats grouped by close reason."""
        groups: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            groups[t.close_reason].append(t)
        return sorted(
            [_calc_segment(reason, trades) for reason, trades in groups.items()],
            key=lambda s: s.count,
            reverse=True,
        )

    def best_trades(self, n: int = 5) -> list[Trade]:
        """Top N trades by PNL."""
        return sorted(self._trades, key=lambda t: t.pnl_usd, reverse=True)[:n]

    def worst_trades(self, n: int = 5) -> list[Trade]:
        """Bottom N trades by PNL."""
        return sorted(self._trades, key=lambda t: t.pnl_usd)[:n]

    def streak_analysis(self) -> dict:
        """Analyze consecutive win/loss streaks."""
        if not self._trades:
            return {"max_win_streak": 0, "max_loss_streak": 0, "current_streak": 0}

        max_win = max_loss = current = 0
        current_type = None

        for t in self._trades:
            is_win = t.pnl_usd > 0
            if current_type == is_win:
                current += 1
            else:
                current = 1
                current_type = is_win

            if is_win:
                max_win = max(max_win, current)
            else:
                max_loss = max(max_loss, current)

        return {
            "max_win_streak": max_win,
            "max_loss_streak": max_loss,
            "current_streak": current if current_type else -current,
        }

    def tp_efficiency(self) -> dict:
        """Analyze TP1/TP2 hit rates and efficiency per entry type."""
        result = {}
        groups: dict[str, list[Trade]] = defaultdict(list)
        for t in self._trades:
            groups[t.entry_type].append(t)

        for et, trades in groups.items():
            n = len(trades)
            if n == 0:
                continue
            tp1_hits = sum(1 for t in trades if t.tp1_closed)
            tp2_hits = sum(1 for t in trades if t.tp2_closed)
            tp1_only = sum(1 for t in trades if t.tp1_closed and not t.tp2_closed)

            tp1_trades = [t for t in trades if t.close_reason == "TP1"]
            avg_pnl_tp1 = (
                sum(t.pnl_usd for t in tp1_trades) / len(tp1_trades)
                if tp1_trades else 0.0
            )

            tp2_trades = [t for t in trades if t.close_reason == "TP2"]
            avg_pnl_tp2 = (
                sum(t.pnl_usd for t in tp2_trades) / len(tp2_trades)
                if tp2_trades else 0.0
            )

            result[et] = {
                "total": n,
                "tp1_hit_rate": round(tp1_hits / n * 100, 1),
                "tp2_hit_rate": round(tp2_hits / n * 100, 1),
                "tp1_only_rate": round(tp1_only / n * 100, 1),
                "avg_pnl_tp1": round(avg_pnl_tp1, 2),
                "avg_pnl_tp2": round(avg_pnl_tp2, 2),
            }
        return result

    def sl_efficiency(self) -> dict:
        """Analyze stop loss effectiveness."""
        hard_sl = [t for t in self._trades if t.close_reason == "HARD_SL"]
        chandelier = [t for t in self._trades if t.close_reason in ("CHANDELIER_TRIGGER", "CHANDELIER_SL")]

        return {
            "hard_sl_count": len(hard_sl),
            "hard_sl_avg_loss": round(
                sum(t.pnl_usd for t in hard_sl) / max(len(hard_sl), 1), 2
            ),
            "chandelier_count": len(chandelier),
            "chandelier_avg_pnl": round(
                sum(t.pnl_usd for t in chandelier) / max(len(chandelier), 1), 2
            ),
            "chandelier_win_rate": round(
                sum(1 for t in chandelier if t.pnl_usd > 0)
                / max(len(chandelier), 1) * 100, 1
            ),
        }
