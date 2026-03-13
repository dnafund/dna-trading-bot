"""SQLite-backed memory store for trade context, insights, and suggestions."""

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from learning.data.trade_reader import Trade

logger = logging.getLogger(__name__)

# ── Schema ────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_contexts (
    position_id  TEXT PRIMARY KEY,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL,
    entry_type   TEXT NOT NULL,
    pnl_usd      REAL NOT NULL,
    roi_percent  REAL NOT NULL,
    close_reason TEXT NOT NULL,
    duration_hours REAL NOT NULL,
    entry_time   TEXT,
    close_time   TEXT,
    leverage     INTEGER NOT NULL,
    margin       REAL NOT NULL,
    fees         REAL NOT NULL,
    is_win       INTEGER NOT NULL,
    hour_of_day  INTEGER,
    day_of_week  INTEGER,
    streak_at_close INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS insights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL,
    content     TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 0.5,
    source      TEXT NOT NULL DEFAULT 'statistical',
    period      TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    config_key      TEXT NOT NULL,
    current_value   REAL,
    suggested_value REAL,
    confidence      REAL NOT NULL DEFAULT 0.5,
    reason          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    backtest_result TEXT,
    period          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tc_symbol ON trade_contexts(symbol);
CREATE INDEX IF NOT EXISTS idx_tc_entry_type ON trade_contexts(entry_type);
CREATE INDEX IF NOT EXISTS idx_tc_close_time ON trade_contexts(close_time);
CREATE INDEX IF NOT EXISTS idx_tc_is_win ON trade_contexts(is_win);
CREATE INDEX IF NOT EXISTS idx_insights_category ON insights(category);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);
"""


# ── Dataclasses ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Insight:
    """A stored analytical insight."""

    category: str
    content: str
    confidence: float
    source: str
    period: Optional[str] = None


@dataclass(frozen=True)
class Suggestion:
    """A stored parameter suggestion."""

    config_key: str
    current_value: Optional[float]
    suggested_value: Optional[float]
    confidence: float
    reason: str
    status: str = "pending"
    backtest_result: Optional[str] = None
    period: Optional[str] = None


# ── MemoryStore ───────────────────────────────────────────────────

class MemoryStore:
    """SQLite memory store — append-only, no mutations."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ── Trade Context ─────────────────────────────────────────────

    def sync_trades(self, trades: list[Trade]) -> int:
        """Sync trades into trade_contexts table. Returns count of new inserts."""
        existing = {
            row[0]
            for row in self._conn.execute(
                "SELECT position_id FROM trade_contexts"
            ).fetchall()
        }

        new_count = 0
        streak = 0

        for trade in trades:
            # Track streak using PNL (more accurate than close_reason)
            is_win = trade.pnl_usd > 0
            if is_win:
                streak = max(streak, 0) + 1
            else:
                streak = min(streak, 0) - 1

            if trade.position_id in existing:
                continue

            hour = trade.entry_time.hour if trade.entry_time else None
            dow = trade.entry_time.weekday() if trade.entry_time else None

            self._conn.execute(
                """INSERT INTO trade_contexts
                   (position_id, symbol, side, entry_type, pnl_usd, roi_percent,
                    close_reason, duration_hours, entry_time, close_time,
                    leverage, margin, fees, is_win, hour_of_day, day_of_week,
                    streak_at_close)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    trade.position_id, trade.symbol, trade.side,
                    trade.entry_type, trade.pnl_usd, trade.roi_percent,
                    trade.close_reason, trade.duration_hours,
                    trade.entry_time.isoformat() if trade.entry_time else None,
                    trade.close_time.isoformat() if trade.close_time else None,
                    trade.leverage, trade.margin, trade.fees,
                    int(is_win), hour, dow, streak,
                ),
            )
            new_count += 1

        self._conn.commit()
        logger.info("Synced %d new trades to memory (total: %d)", new_count, len(existing) + new_count)
        return new_count

    def get_trade_contexts(
        self,
        symbol: Optional[str] = None,
        entry_type: Optional[str] = None,
        limit: int = 500,
    ) -> list[dict]:
        """Query trade contexts with optional filters."""
        query = "SELECT * FROM trade_contexts WHERE 1=1"
        params: list = []

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if entry_type:
            query += " AND entry_type = ?"
            params.append(entry_type)

        query += " ORDER BY close_time DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    # ── Insights ──────────────────────────────────────────────────

    def add_insight(self, insight: Insight) -> int:
        """Store an insight. Returns the new row ID."""
        cursor = self._conn.execute(
            """INSERT INTO insights (category, content, confidence, source, period)
               VALUES (?, ?, ?, ?, ?)""",
            (insight.category, insight.content, insight.confidence,
             insight.source, insight.period),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_insights(
        self,
        category: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[Insight]:
        """Query stored insights."""
        query = "SELECT * FROM insights WHERE confidence >= ?"
        params: list = [min_confidence]

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [
            Insight(
                category=row["category"],
                content=row["content"],
                confidence=row["confidence"],
                source=row["source"],
                period=row["period"],
            )
            for row in rows
        ]

    # ── Suggestions ───────────────────────────────────────────────

    def add_suggestion(self, suggestion: Suggestion) -> int:
        """Store a parameter suggestion. Returns the new row ID."""
        cursor = self._conn.execute(
            """INSERT INTO suggestions
               (config_key, current_value, suggested_value, confidence,
                reason, status, backtest_result, period)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                suggestion.config_key, suggestion.current_value,
                suggestion.suggested_value, suggestion.confidence,
                suggestion.reason, suggestion.status,
                suggestion.backtest_result, suggestion.period,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid or 0

    def get_suggestions(
        self,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[Suggestion]:
        """Query stored suggestions."""
        query = "SELECT * FROM suggestions WHERE 1=1"
        params: list = []

        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(query, params).fetchall()
        return [
            Suggestion(
                config_key=row["config_key"],
                current_value=row["current_value"],
                suggested_value=row["suggested_value"],
                confidence=row["confidence"],
                reason=row["reason"],
                status=row["status"],
                backtest_result=row["backtest_result"],
                period=row["period"],
            )
            for row in rows
        ]

    # ── Stats ─────────────────────────────────────────────────────

    def trade_count(self) -> int:
        """Total trades in memory."""
        row = self._conn.execute("SELECT COUNT(*) FROM trade_contexts").fetchone()
        return row[0] if row else 0

    def insight_count(self) -> int:
        """Total insights in memory."""
        row = self._conn.execute("SELECT COUNT(*) FROM insights").fetchone()
        return row[0] if row else 0
