"""
One-time data migration: JSON files → PostgreSQL.

Run on VPS after setting up PostgreSQL:
    python -m src.database.migrate_data

Migrates:
1. data/users.json → users table
2. data/allowed_emails.json → users table (Google users)
3. data/positions.json → positions table (all historical positions)
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.database.connection import get_session, init_db
from src.database.models import Position, User

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_USER_ID = "owner"  # Single-user owner ID for Phase 1


def migrate_users() -> str:
    """Migrate users.json + allowed_emails.json → users table.
    Returns the owner user_id.
    """
    users_file = DATA_DIR / "users.json"
    emails_file = DATA_DIR / "allowed_emails.json"
    owner_id = None

    with get_session() as session:
        # Migrate password-based users
        if users_file.exists():
            with open(users_file, "r", encoding="utf-8") as f:
                users_data = json.load(f)

            for username, data in users_data.items():
                existing = session.query(User).filter(User.username == username).first()
                if existing:
                    logger.info(f"  User '{username}' already in DB, skipping")
                    if owner_id is None:
                        owner_id = existing.id
                    continue

                user = User(
                    username=username,
                    password_hash=data.get("password_hash"),
                    created_at=datetime.fromisoformat(data["created_at"])
                    if data.get("created_at")
                    else datetime.now(timezone.utc),
                )
                session.add(user)
                session.flush()
                if owner_id is None:
                    owner_id = user.id
                logger.info(f"  Migrated user: {username} → {user.id}")

        # Migrate allowed emails (create user stubs)
        if emails_file.exists():
            with open(emails_file, "r", encoding="utf-8") as f:
                emails = json.load(f)

            for email in emails:
                email_lower = email.lower()
                existing = session.query(User).filter(User.email == email_lower).first()
                if existing:
                    logger.info(f"  Email '{email}' already in DB, skipping")
                    if owner_id is None:
                        owner_id = existing.id
                    continue

                user = User(email=email_lower, name=email.split("@")[0])
                session.add(user)
                session.flush()
                if owner_id is None:
                    owner_id = user.id
                logger.info(f"  Migrated email: {email} → {user.id}")

    # If no users at all, create a default owner
    if owner_id is None:
        with get_session() as session:
            user = User(id=DEFAULT_USER_ID, username="admin", name="Owner")
            session.add(user)
            session.flush()
            owner_id = user.id
            logger.info(f"  Created default owner: {owner_id}")

    return owner_id


def migrate_positions(owner_id: str) -> int:
    """Migrate positions.json → positions table. Returns count migrated."""
    positions_file = DATA_DIR / "positions.json"
    if not positions_file.exists():
        logger.info("  No positions.json found, skipping")
        return 0

    with open(positions_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    count = 0
    with get_session() as session:
        for pid, pos_data in data.items():
            if not isinstance(pos_data, dict) or pid.startswith("_"):
                continue

            # Skip if already migrated
            existing = session.query(Position).filter(Position.id == pid).first()
            if existing:
                continue

            # Parse timestamp
            ts_str = pos_data.get("timestamp", "")
            try:
                created_at = datetime.fromisoformat(ts_str)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                created_at = datetime.now(timezone.utc)

            # Parse close_time
            closed_at = None
            ct_str = pos_data.get("close_time")
            if ct_str:
                try:
                    closed_at = datetime.fromisoformat(ct_str)
                    if closed_at.tzinfo is None:
                        closed_at = closed_at.replace(tzinfo=timezone.utc)
                except (ValueError, TypeError):
                    pass

            # Collect non-indexed fields into JSONB data
            extra_data = {}
            for key in (
                "stop_loss", "trailing_sl", "chandelier_sl",
                "take_profit_1", "take_profit_2",
                "tp1_cancelled", "tp2_cancelled",
                "entry_fee", "total_exit_fees", "pnl_percent",
                "ce_armed", "ce_price_validated",
                "entry_candle_ts", "entry_time",
                "last_m15_close",
                "ce_order_id", "ce_order_price",
                "tp1_order_id", "tp2_order_id", "hard_sl_order_id",
                "linear_issue_id",
            ):
                val = pos_data.get(key)
                if val is not None:
                    extra_data[key] = val

            position = Position(
                id=pid,
                user_id=owner_id,
                symbol=pos_data.get("symbol", ""),
                side=pos_data.get("side", "BUY"),
                entry_price=pos_data.get("entry_price", 0),
                size=pos_data.get("size", 0),
                leverage=pos_data.get("leverage", 1),
                margin=pos_data.get("margin", 0),
                entry_type=pos_data.get("entry_type", "standard"),
                current_price=pos_data.get("current_price", 0),
                pnl_usd=pos_data.get("pnl_usd", 0),
                roi_percent=pos_data.get("roi_percent", 0),
                status=pos_data.get("status", "OPEN"),
                close_reason=pos_data.get("close_reason"),
                tp1_closed=pos_data.get("tp1_closed", False),
                tp2_closed=pos_data.get("tp2_closed", False),
                realized_pnl=pos_data.get("realized_pnl", 0),
                remaining_size=pos_data.get("remaining_size", 0),
                created_at=created_at,
                closed_at=closed_at,
                data=extra_data,
            )
            session.add(position)
            count += 1

    return count


def main():
    logger.info("=" * 50)
    logger.info("Data Migration: JSON → PostgreSQL")
    logger.info("=" * 50)

    init_db()

    logger.info("\n[1/2] Migrating users...")
    owner_id = migrate_users()
    logger.info(f"  Owner user_id: {owner_id}")

    logger.info("\n[2/2] Migrating positions...")
    count = migrate_positions(owner_id)
    logger.info(f"  Migrated {count} positions")

    logger.info("\n✅ Migration complete!")


if __name__ == "__main__":
    main()
