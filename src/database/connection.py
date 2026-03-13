"""
Database connection — Engine, session factory, connection pool.

Usage:
    from src.database.connection import get_session, init_db

    # On startup
    init_db()

    # In request handlers
    with get_session() as session:
        user = session.query(User).first()
"""

import logging
import os
from contextlib import contextmanager
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)

# Load env vars
load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///data/app.db",
)

# Build engine with appropriate settings per database type
_engine_kwargs = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL pool settings
    _engine_kwargs.update(
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

engine = create_engine(DATABASE_URL, **_engine_kwargs)

SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a transactional session that auto-commits on success, rollbacks on error."""
    session = SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Verify database connection on startup."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("[DB] PostgreSQL connection verified")
    except Exception as e:
        logger.error(f"[DB] Failed to connect to PostgreSQL: {e}")
        raise


def close_db() -> None:
    """Dispose engine pool (for graceful shutdown)."""
    engine.dispose()
    logger.info("[DB] Connection pool disposed")
