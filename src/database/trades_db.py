"""
TradesDB — SQLite storage for closed trades and OKX history cache.

Replaces JSON files for scalability (handles 100k+ trades efficiently).
Uses WAL mode for concurrent bot writes + dashboard reads.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# All columns for closed_trades table
CLOSED_TRADES_COLUMNS = [
    "position_id", "symbol", "side", "entry_price", "size", "leverage", "margin",
    "entry_type", "status", "close_reason",
    "pnl_usd", "pnl_percent", "roi_percent", "realized_pnl",
    "remaining_size", "entry_fee", "total_exit_fees",
    "tp1_closed", "tp2_closed", "tp1_cancelled", "tp2_cancelled",
    "take_profit_1", "take_profit_2",
    "stop_loss", "trailing_sl", "chandelier_sl",
    "current_price", "exit_price",
    "timestamp", "close_time", "entry_time", "entry_candle_ts",
    "linear_issue_id", "last_m15_close",
    "tp1_order_id", "tp2_order_id", "hard_sl_order_id",
    "ce_armed", "ce_price_validated",
    "okx_pnl_synced",
]

# Boolean fields stored as INTEGER in SQLite
_BOOL_FIELDS = {"tp1_closed", "tp2_closed", "tp1_cancelled", "tp2_cancelled", "ce_armed", "ce_price_validated", "okx_pnl_synced"}

_CREATE_CLOSED_TRADES = """
CREATE TABLE IF NOT EXISTS closed_trades (
    position_id     TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    entry_price     REAL NOT NULL DEFAULT 0.0,
    size            REAL NOT NULL DEFAULT 0.0,
    leverage        INTEGER NOT NULL DEFAULT 1,
    margin          REAL NOT NULL DEFAULT 0.0,
    entry_type      TEXT NOT NULL DEFAULT 'standard_m15',
    status          TEXT NOT NULL DEFAULT 'CLOSED',
    close_reason    TEXT,

    pnl_usd         REAL DEFAULT 0.0,
    pnl_percent     REAL DEFAULT 0.0,
    roi_percent     REAL DEFAULT 0.0,
    realized_pnl    REAL DEFAULT 0.0,
    remaining_size  REAL DEFAULT 0.0,
    entry_fee       REAL DEFAULT 0.0,
    total_exit_fees REAL DEFAULT 0.0,

    tp1_closed      INTEGER DEFAULT 0,
    tp2_closed      INTEGER DEFAULT 0,
    tp1_cancelled   INTEGER DEFAULT 0,
    tp2_cancelled   INTEGER DEFAULT 0,
    take_profit_1   REAL,
    take_profit_2   REAL,

    stop_loss       REAL,
    trailing_sl     REAL,
    chandelier_sl   REAL,

    exit_price      REAL DEFAULT 0.0,
    current_price   REAL DEFAULT 0.0,

    timestamp       TEXT NOT NULL,
    close_time      TEXT,
    entry_time      TEXT,
    entry_candle_ts TEXT,

    linear_issue_id TEXT,
    last_m15_close  TEXT,
    tp1_order_id    TEXT,
    tp2_order_id    TEXT,
    hard_sl_order_id TEXT,
    ce_armed        INTEGER DEFAULT 0,
    ce_price_validated INTEGER DEFAULT 0,
    okx_pnl_synced  INTEGER DEFAULT 0
);
"""

_CREATE_CLOSED_TRADES_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ct_close_time ON closed_trades(close_time DESC);",
    "CREATE INDEX IF NOT EXISTS idx_ct_symbol ON closed_trades(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_ct_entry_type ON closed_trades(entry_type);",
    "CREATE INDEX IF NOT EXISTS idx_ct_symbol_timestamp ON closed_trades(symbol, timestamp);",
]

_CREATE_OKX_HISTORY = """
CREATE TABLE IF NOT EXISTS okx_history (
    dedup_key       TEXT PRIMARY KEY,
    pos_id          TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    leverage        INTEGER,
    open_price      REAL,
    close_price     REAL,
    realized_pnl    REAL DEFAULT 0.0,
    pnl_ratio       REAL DEFAULT 0.0,
    fee             REAL DEFAULT 0.0,
    funding_fee     REAL DEFAULT 0.0,
    open_time       TEXT,
    close_time      TEXT,
    close_reason    TEXT
);
"""

_CREATE_OKX_HISTORY_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_okx_close_time ON okx_history(close_time DESC);",
    "CREATE INDEX IF NOT EXISTS idx_okx_symbol_open ON okx_history(symbol, open_time);",
]


class TradesDB:
    """SQLite storage for closed trades and OKX history cache."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        try:
            conn.execute(_CREATE_CLOSED_TRADES)
            for idx in _CREATE_CLOSED_TRADES_INDEXES:
                conn.execute(idx)
            conn.execute(_CREATE_OKX_HISTORY)
            for idx in _CREATE_OKX_HISTORY_INDEXES:
                conn.execute(idx)
            # Migrations: add columns if missing
            self._migrate(conn)
            conn.commit()
            logger.info(f"[TradesDB] Initialized at {self.db_path}")
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection):
        """Add missing columns to existing tables."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(closed_trades)").fetchall()}
        if "exit_price" not in existing:
            conn.execute("ALTER TABLE closed_trades ADD COLUMN exit_price REAL DEFAULT 0.0")
            # Backfill from current_price
            conn.execute("UPDATE closed_trades SET exit_price = current_price WHERE exit_price IS NULL OR exit_price = 0.0")
            logger.info("[TradesDB] Migrated: added exit_price column, backfilled from current_price")
        if "okx_pnl_synced" not in existing:
            conn.execute("ALTER TABLE closed_trades ADD COLUMN okx_pnl_synced INTEGER DEFAULT 0")
            logger.info("[TradesDB] Migrated: added okx_pnl_synced column")

    # ── closed_trades ─────────────────────────────────────────────

    def insert_closed_trade(self, trade: dict) -> None:
        """Insert a single closed trade. Ignores duplicates by position_id."""
        cols = []
        vals = []
        for col in CLOSED_TRADES_COLUMNS:
            if col in trade:
                cols.append(col)
                val = trade[col]
                if col in _BOOL_FIELDS:
                    val = 1 if val else 0
                vals.append(val)

        placeholders = ", ".join(["?"] * len(cols))
        col_names = ", ".join(cols)
        sql = f"INSERT OR IGNORE INTO closed_trades ({col_names}) VALUES ({placeholders})"

        conn = self._get_conn()
        try:
            conn.execute(sql, vals)
            conn.commit()
        finally:
            conn.close()

    def insert_closed_trades_batch(self, trades: list[dict]) -> int:
        """Batch insert closed trades. Returns count of rows inserted."""
        if not trades:
            return 0
        conn = self._get_conn()
        try:
            inserted = 0
            for trade in trades:
                cols = []
                vals = []
                for col in CLOSED_TRADES_COLUMNS:
                    if col in trade:
                        cols.append(col)
                        val = trade[col]
                        if col in _BOOL_FIELDS:
                            val = 1 if val else 0
                        vals.append(val)
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                sql = f"INSERT OR IGNORE INTO closed_trades ({col_names}) VALUES ({placeholders})"
                cur = conn.execute(sql, vals)
                inserted += cur.rowcount
            conn.commit()
            return inserted
        finally:
            conn.close()

    def get_closed_trades(
        self,
        limit: int = 50,
        offset: int = 0,
        symbol: str | None = None,
        entry_type: str | None = None,
        result: str | None = None,
        sort_by: str = "close_time",
        sort_order: str = "desc",
    ) -> dict:
        """Get paginated closed trades with optional filters."""
        where_clauses = []
        params = []

        if symbol:
            where_clauses.append("symbol = ?")
            params.append(symbol)
        if entry_type:
            where_clauses.append("entry_type = ?")
            params.append(entry_type)
        if result == "win":
            where_clauses.append("pnl_usd > 0")
        elif result == "loss":
            where_clauses.append("pnl_usd <= 0")

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Validate sort column
        allowed_sort = {"close_time", "pnl_usd", "roi_percent", "symbol", "margin", "timestamp"}
        if sort_by not in allowed_sort:
            sort_by = "close_time"
        sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"

        conn = self._get_conn()
        try:
            # Count total
            count_row = conn.execute(
                f"SELECT COUNT(*) as cnt FROM closed_trades WHERE {where_sql}", params
            ).fetchone()
            total = count_row["cnt"] if count_row else 0

            # Fetch page
            rows = conn.execute(
                f"SELECT * FROM closed_trades WHERE {where_sql} ORDER BY {sort_by} {sort_dir} LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

            positions = [self._row_to_dict(r) for r in rows]
            return {"positions": positions, "total": total}
        finally:
            conn.close()

    def get_all_closed_for_lookup(self) -> list[dict]:
        """Get all closed trades for _build_entry_type_lookup(). Returns full rows."""
        conn = self._get_conn()
        try:
            rows = conn.execute("SELECT * FROM closed_trades ORDER BY timestamp ASC").fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_closed_count(self) -> int:
        conn = self._get_conn()
        try:
            row = conn.execute("SELECT COUNT(*) as cnt FROM closed_trades").fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    def get_stats(self) -> dict:
        """Get aggregated stats from closed trades via SQL."""
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
                    SUM(pnl_usd) as total_pnl,
                    SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) as gross_profit,
                    SUM(CASE WHEN pnl_usd <= 0 THEN ABS(pnl_usd) ELSE 0 END) as gross_loss,
                    AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win,
                    AVG(CASE WHEN pnl_usd <= 0 THEN pnl_usd END) as avg_loss,
                    MAX(pnl_usd) as best_trade,
                    MIN(pnl_usd) as worst_trade,
                    SUM(entry_fee + total_exit_fees) as total_fees
                FROM closed_trades
            """).fetchone()

            if not row or row["total_trades"] == 0:
                return {
                    "total_trades": 0, "wins": 0, "losses": 0,
                    "total_pnl": 0, "win_rate": 0, "profit_factor": 0,
                    "avg_win": 0, "avg_loss": 0, "best_trade": 0, "worst_trade": 0,
                    "total_fees": 0,
                }

            total = row["total_trades"]
            wins = row["wins"] or 0
            gross_profit = row["gross_profit"] or 0
            gross_loss = row["gross_loss"] or 0

            return {
                "total_trades": total,
                "wins": wins,
                "losses": row["losses"] or 0,
                "total_pnl": round(row["total_pnl"] or 0, 2),
                "win_rate": round(wins / total * 100, 1) if total else 0,
                "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999,
                "avg_win": round(row["avg_win"] or 0, 2),
                "avg_loss": round(row["avg_loss"] or 0, 2),
                "best_trade": round(row["best_trade"] or 0, 2),
                "worst_trade": round(row["worst_trade"] or 0, 2),
                "total_fees": round(row["total_fees"] or 0, 2),
            }
        finally:
            conn.close()

    def get_okx_pnl_totals(self) -> dict:
        """Get accurate PNL totals from okx_history (has fee + funding_fee).

        OKX positions-history fields:
        - realized_pnl: trading PNL from price movement (BEFORE fees)
        - fee: trading fees (negative number)
        - funding_fee: funding fees received/paid

        Net PNL = realized_pnl + fee + funding_fee
        """
        conn = self._get_conn()
        try:
            row = conn.execute("""
                SELECT
                    COALESCE(SUM(realized_pnl), 0) as trade_pnl,
                    COALESCE(SUM(fee), 0) as total_fee,
                    COALESCE(SUM(funding_fee), 0) as total_funding
                FROM okx_history
            """).fetchone()
            if not row:
                return {}

            trade_pnl = row["trade_pnl"]
            total_fee = row["total_fee"]
            total_funding = row["total_funding"]

            return {
                "realized_pnl": round(trade_pnl, 2),
                "total_fees": round(abs(total_fee), 2),
                "total_funding_fees": round(total_funding, 4),
                "net_pnl": round(trade_pnl + total_fee + total_funding, 2),
            }
        finally:
            conn.close()

    def get_profit_stats(self, period: str = "daily") -> list[dict]:
        """Get PNL aggregated by time period."""
        if period == "monthly":
            group_expr = "strftime('%Y-%m', close_time)"
        elif period == "weekly":
            # SQLite: get Monday of the week
            group_expr = "date(close_time, 'weekday 0', '-6 days')"
        else:
            group_expr = "date(close_time)"

        conn = self._get_conn()
        try:
            rows = conn.execute(f"""
                SELECT
                    {group_expr} as period_key,
                    SUM(pnl_usd) as pnl,
                    COUNT(*) as count
                FROM closed_trades
                WHERE close_time IS NOT NULL
                GROUP BY period_key
                ORDER BY period_key ASC
            """).fetchall()

            return [
                {
                    "time": r["period_key"],
                    "pnl": round(r["pnl"], 2),
                    "count": r["count"],
                    "timestamp": f"{r['period_key']}T00:00:00",
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_equity_curve(self) -> list[dict]:
        """Get cumulative PNL over time, ordered by close_time."""
        conn = self._get_conn()
        try:
            rows = conn.execute("""
                SELECT symbol, pnl_usd, close_time
                FROM closed_trades
                WHERE close_time IS NOT NULL
                ORDER BY close_time ASC
            """).fetchall()

            cumulative = 0.0
            result = []
            for r in rows:
                cumulative += r["pnl_usd"]
                result.append({
                    "time": r["close_time"],
                    "pnl": round(cumulative, 2),
                    "symbol": r["symbol"],
                    "trade_pnl": round(r["pnl_usd"], 2),
                })
            return result
        finally:
            conn.close()

    def get_recent_activity(self, limit: int = 10) -> list[dict]:
        """Get most recent closed trades."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM closed_trades WHERE close_time IS NOT NULL ORDER BY close_time DESC LIMIT ?",
                [limit],
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def update_trade_pnl(self, position_id: str, pnl_usd: float, realized_pnl: float = None) -> bool:
        """Update PNL for a specific closed trade. Returns True if updated."""
        conn = self._get_conn()
        try:
            if realized_pnl is not None:
                conn.execute(
                    "UPDATE closed_trades SET pnl_usd = ?, realized_pnl = ? WHERE position_id = ?",
                    [pnl_usd, realized_pnl, position_id],
                )
            else:
                conn.execute(
                    "UPDATE closed_trades SET pnl_usd = ? WHERE position_id = ?",
                    [pnl_usd, position_id],
                )
            conn.commit()
            return conn.total_changes > 0
        finally:
            conn.close()

    def update_trades_pnl_batch(self, updates: list[dict]) -> int:
        """Batch update PNL. Each dict: {position_id, pnl_usd, realized_pnl?}."""
        if not updates:
            return 0
        conn = self._get_conn()
        try:
            count = 0
            for u in updates:
                pid = u["position_id"]
                pnl = u["pnl_usd"]
                rpnl = u.get("realized_pnl")
                if rpnl is not None:
                    conn.execute(
                        "UPDATE closed_trades SET pnl_usd = ?, realized_pnl = ? WHERE position_id = ?",
                        [pnl, rpnl, pid],
                    )
                else:
                    conn.execute(
                        "UPDATE closed_trades SET pnl_usd = ? WHERE position_id = ?",
                        [pnl, pid],
                    )
                count += conn.total_changes
            conn.commit()
            return count
        finally:
            conn.close()

    # ── okx_history ───────────────────────────────────────────────

    def upsert_okx_history(self, records: list[dict]) -> int:
        """Upsert OKX history records keyed by dedup_key. Returns count inserted/updated."""
        if not records:
            return 0
        conn = self._get_conn()
        try:
            count = 0
            for rec in records:
                pos_id = rec.get("pos_id", "")
                dedup_key = rec.get("dedup_key") or f"{rec.get('symbol', '')}|{rec.get('open_time', '')}|{rec.get('side', '')}"

                conn.execute("""
                    INSERT OR REPLACE INTO okx_history
                    (dedup_key, pos_id, symbol, side, leverage, open_price, close_price,
                     realized_pnl, pnl_ratio, fee, funding_fee,
                     open_time, close_time, close_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    dedup_key,
                    pos_id,
                    rec.get("symbol", ""),
                    rec.get("side", ""),
                    rec.get("leverage"),
                    rec.get("open_price"),
                    rec.get("close_price"),
                    rec.get("realized_pnl", 0),
                    rec.get("pnl_ratio", 0),
                    rec.get("fee", 0),
                    rec.get("funding_fee", 0),
                    rec.get("open_time"),
                    rec.get("close_time"),
                    rec.get("close_reason"),
                ])
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def load_okx_history(self) -> list[dict]:
        """Load all OKX history records, newest first."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM okx_history ORDER BY close_time DESC"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── helpers ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert sqlite3.Row to dict, restoring booleans."""
        d = dict(row)
        for field in _BOOL_FIELDS:
            if field in d:
                d[field] = bool(d[field])
        return d
