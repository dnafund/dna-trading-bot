"""
Position repository — CRUD operations for the positions table.

Replaces data/positions.json file-based storage.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from src.database.models import Position

logger = logging.getLogger(__name__)


class PositionRepo:
    """Database operations for Position model."""

    def __init__(self, session: Session):
        self.session = session

    def find_by_id(self, position_id: str) -> Optional[Position]:
        return (
            self.session.query(Position)
            .filter(Position.id == position_id)
            .first()
        )

    def find_open_by_user(self, user_id: str) -> list[Position]:
        """Get all OPEN/PARTIAL_CLOSE positions for a user, sorted by ROI desc."""
        return (
            self.session.query(Position)
            .filter(
                Position.user_id == user_id,
                Position.status.in_(["OPEN", "PARTIAL_CLOSE"]),
            )
            .order_by(desc(Position.roi_percent))
            .all()
        )

    def find_closed_by_user(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        symbol: Optional[str] = None,
        entry_type: Optional[str] = None,
        result: Optional[str] = None,
        sort_by: str = "closed_at",
        sort_order: str = "desc",
    ) -> tuple[list[Position], int]:
        """Get closed positions with filters. Returns (positions, total_count)."""
        query = self.session.query(Position).filter(
            Position.user_id == user_id,
            Position.status.in_(["CLOSED", "PARTIAL_CLOSE"]),
        )

        if symbol:
            query = query.filter(Position.symbol == symbol)
        if entry_type:
            query = query.filter(Position.entry_type == entry_type)
        if result == "win":
            query = query.filter(Position.pnl_usd > 0)
        elif result == "loss":
            query = query.filter(Position.pnl_usd <= 0)

        total = query.count()

        # Sort — whitelist only to prevent injection
        _SORT_COLS = {
            "closed_at": Position.closed_at,
            "close_time": Position.closed_at,
            "pnl_usd": Position.pnl_usd,
            "roi_percent": Position.roi_percent,
        }
        sort_col = _SORT_COLS.get(sort_by)
        if sort_col is None:
            logger.warning(f"[DB] Invalid sort column '{sort_by}', defaulting to closed_at")
            sort_col = Position.closed_at
        if sort_order == "desc":
            query = query.order_by(desc(sort_col))
        else:
            query = query.order_by(sort_col)

        positions = query.offset(offset).limit(limit).all()
        return positions, total

    def find_by_user_and_symbol(
        self, user_id: str, symbol: str, status: Optional[str] = None,
    ) -> list[Position]:
        """Find positions for a user+symbol, optionally filtered by status."""
        query = self.session.query(Position).filter(
            Position.user_id == user_id,
            Position.symbol == symbol,
        )
        if status:
            query = query.filter(Position.status == status)
        return query.all()

    def create(self, **kwargs) -> Position:
        """Create a new position."""
        pos = Position(**kwargs)
        self.session.add(pos)
        self.session.flush()
        logger.info(
            f"[DB] Position created: {pos.id} {pos.symbol} {pos.side} "
            f"user={pos.user_id}"
        )
        return pos

    def update_pnl(
        self,
        position_id: str,
        *,
        current_price: float,
        pnl_usd: float,
        roi_percent: float,
    ) -> None:
        """Update current price and PNL fields."""
        pos = self.find_by_id(position_id)
        if pos:
            pos.current_price = current_price
            pos.pnl_usd = pnl_usd
            pos.roi_percent = roi_percent

    def close_position(
        self,
        position_id: str,
        *,
        close_reason: str,
        pnl_usd: float,
        roi_percent: float,
    ) -> Optional[Position]:
        """Mark position as CLOSED."""
        pos = self.find_by_id(position_id)
        if not pos:
            return None
        pos.status = "CLOSED"
        pos.close_reason = close_reason
        pos.pnl_usd = pnl_usd
        pos.roi_percent = roi_percent
        pos.closed_at = datetime.now(timezone.utc)
        logger.info(
            f"[DB] Position closed: {pos.id} {pos.symbol} "
            f"reason={close_reason} pnl=${pnl_usd:.2f}"
        )
        return pos

    def update_data(self, position_id: str, data_updates: dict) -> None:
        """Merge updates into the JSONB `data` field."""
        pos = self.find_by_id(position_id)
        if pos:
            current = pos.data or {}
            pos.data = {**current, **data_updates}
            flag_modified(pos, "data")

    def count_open_by_user(self, user_id: str) -> int:
        """Count open positions for a user (for position limits)."""
        return (
            self.session.query(Position)
            .filter(
                Position.user_id == user_id,
                Position.status.in_(["OPEN", "PARTIAL_CLOSE"]),
            )
            .count()
        )

    def get_all_by_user(self, user_id: str) -> list[Position]:
        """Get all positions (any status) for a user."""
        return (
            self.session.query(Position)
            .filter(Position.user_id == user_id)
            .order_by(desc(Position.created_at))
            .all()
        )
