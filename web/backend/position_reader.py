"""
Position data reader — reads active positions from positions.json,
closed trades from SQLite (TradesDB).
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class PositionReader:
    """Read position data from positions.json (active) and SQLite (closed)."""

    def __init__(self, positions_file: Path, trades_db=None):
        self.positions_file = positions_file
        self.trades_db = trades_db
        self._cache: dict = {}
        self._cache_mtime: float = 0

    def _load(self) -> dict:
        """Load positions from file, with mtime caching."""
        try:
            mtime = self.positions_file.stat().st_mtime
            if mtime != self._cache_mtime:
                with open(self.positions_file, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                self._cache_mtime = mtime
        except FileNotFoundError:
            logger.warning(f"Positions file not found: {self.positions_file}")
            self._cache = {}
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in positions file: {e}")
        return self._cache

    def _all_positions(self) -> list[dict]:
        """Get all positions from JSON as list of dicts (skip metadata keys).
        After migration, this only returns active positions."""
        data = self._load()
        if not isinstance(data, dict):
            return []
        return [v for v in data.values() if isinstance(v, dict)]

    def get_paper_balance(self) -> float:
        """Get paper trading balance."""
        data = self._load()
        return data.get("_paper_balance", 0.0) if isinstance(data, dict) else 0.0

    def get_open_positions(self) -> list[dict]:
        """Get all open/partial positions, sorted by PNL desc."""
        positions = [
            p for p in self._all_positions()
            if p.get("status") in ("OPEN", "PARTIAL_CLOSE")
        ]
        positions.sort(key=lambda p: p.get("roi_percent", 0), reverse=True)
        return positions

    def get_closed_positions(
        self,
        limit: int = 50,
        offset: int = 0,
        symbol: Optional[str] = None,
        entry_type: Optional[str] = None,
        result: Optional[str] = None,
        sort_by: str = "close_time",
        sort_order: str = "desc",
    ) -> dict:
        """Get closed positions from SQLite with filters, sorting, and pagination."""
        if self.trades_db:
            return self.trades_db.get_closed_trades(
                limit=limit, offset=offset, symbol=symbol,
                entry_type=entry_type, result=result,
                sort_by=sort_by, sort_order=sort_order,
            )

        # Fallback: read from JSON (pre-migration)
        positions = [
            p for p in self._all_positions()
            if p.get("status") in ("CLOSED",)
        ]
        if symbol:
            positions = [p for p in positions if p.get("symbol") == symbol]
        if entry_type:
            positions = [p for p in positions if p.get("entry_type") == entry_type]
        if result == "win":
            positions = [p for p in positions if p.get("pnl_usd", 0) > 0]
        elif result == "loss":
            positions = [p for p in positions if p.get("pnl_usd", 0) <= 0]

        reverse = sort_order == "desc"
        positions.sort(key=lambda p: p.get(sort_by, 0) or "", reverse=reverse)
        total = len(positions)
        return {"positions": positions[offset:offset + limit], "total": total}

    def get_stats(self) -> dict:
        """Calculate trading statistics from JSON (open) + SQLite (closed)."""
        open_pos = self.get_open_positions()

        # Open position stats
        unrealized_pnl = sum(p.get("pnl_usd", 0) for p in open_pos)
        realized_from_open = sum(p.get("realized_pnl", 0) for p in open_pos)
        total_margin = sum(p.get("margin", 0) for p in open_pos)

        # Closed trade stats from SQLite
        if self.trades_db:
            closed_stats = self.trades_db.get_stats()
        else:
            closed_stats = {
                "total_trades": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "win_rate": 0, "profit_factor": 0,
                "avg_win": 0, "avg_loss": 0, "best_trade": 0, "worst_trade": 0,
                "total_fees": 0,
            }

        realized_pnl = closed_stats["total_pnl"] + realized_from_open
        balance = self.get_paper_balance()

        return {
            "balance": round(balance, 2),
            "open_count": len(open_pos),
            "closed_count": closed_stats["total_trades"],
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(realized_pnl, 2),
            "total_pnl": round(unrealized_pnl + realized_pnl, 2),
            "total_margin": round(total_margin, 2),
            "win_rate": closed_stats["win_rate"],
            "profit_factor": closed_stats["profit_factor"],
            "avg_win": closed_stats["avg_win"],
            "avg_loss": closed_stats["avg_loss"],
            "total_trades": closed_stats["total_trades"],
            "wins": closed_stats["wins"],
            "losses": closed_stats["losses"],
            "best_trade": closed_stats.get("best_trade"),
            "worst_trade": closed_stats.get("worst_trade"),
        }

    def get_equity_curve(self) -> list[dict]:
        """Build equity curve from closed trades (cumulative PNL)."""
        if self.trades_db:
            return self.trades_db.get_equity_curve()

        # Fallback
        closed = [p for p in self._all_positions() if p.get("status") == "CLOSED"]
        closed.sort(key=lambda p: (p.get("close_time") or p.get("timestamp") or ""))
        curve = []
        cumulative = 0
        for p in closed:
            cumulative += p.get("pnl_usd", 0)
            curve.append({
                "time": p.get("close_time") or p.get("timestamp") or "",
                "pnl": cumulative,
                "symbol": p.get("symbol", ""),
                "trade_pnl": p.get("pnl_usd", 0),
            })
        return curve

    def get_profit_stats(self, period: str = "daily") -> list[dict]:
        """Aggregate PnL by time period."""
        if self.trades_db:
            return self.trades_db.get_profit_stats(period)

        # Fallback
        from collections import defaultdict
        from datetime import datetime, timedelta

        closed = [p for p in self._all_positions() if p.get("status") == "CLOSED"]
        if not closed:
            return []

        buckets: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})
        for p in closed:
            ts_str = p.get("close_time") or p.get("timestamp")
            if not ts_str:
                continue
            try:
                dt = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue
            pnl = p.get("pnl_usd", 0.0)
            if period == "monthly":
                key = dt.strftime("%Y-%m")
            elif period == "weekly":
                monday = dt - timedelta(days=dt.weekday())
                key = monday.strftime("%Y-%m-%d")
            else:
                key = dt.strftime("%Y-%m-%d")
            buckets[key]["pnl"] += pnl
            buckets[key]["count"] += 1

        return [
            {"time": key, "pnl": round(b["pnl"], 2), "count": b["count"], "timestamp": f"{key}T00:00:00"}
            for key, b in sorted(buckets.items())
        ]

    def get_recent_activity(self, limit: int = 10) -> list[dict]:
        """Get recent trading activity (closed trades)."""
        if self.trades_db:
            recent = self.trades_db.get_recent_activity(limit)
        else:
            closed = [p for p in self._all_positions() if p.get("status") == "CLOSED"]
            closed.sort(key=lambda p: (p.get("close_time") or ""), reverse=True)
            recent = closed[:limit]

        activity = []
        for p in recent:
            pnl = p.get("pnl_usd", 0)
            activity.append({
                "type": "trade",
                "symbol": p.get("symbol", "Unknown"),
                "action": "PROFIT" if pnl >= 0 else "LOSS",
                "amount": pnl,
                "time": p.get("close_time") or p.get("timestamp"),
                "strategy": p.get("entry_type", "Standard"),
                "side": p.get("side", ""),
                "entry_price": p.get("entry_price", 0),
                "entry_time": p.get("entry_time") or p.get("timestamp", ""),
                "close_time": p.get("close_time", ""),
                "tp1": p.get("take_profit_1"),
                "tp2": p.get("take_profit_2"),
                "tp1_closed": p.get("tp1_closed", False),
                "tp2_closed": p.get("tp2_closed", False),
                "tp1_cancelled": p.get("tp1_cancelled", False),
                "tp2_cancelled": p.get("tp2_cancelled", False),
                "hard_sl": p.get("stop_loss"),
                "trailing_sl": p.get("trailing_sl"),
                "close_reason": p.get("close_reason", ""),
                "roi": p.get("roi_percent", 0),
            })
        return activity
