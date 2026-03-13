"""
SQLAlchemy models — all tables for the multi-tenant trading bot.

Phase 1: User, Position (replaces positions.json + users.json)
Phase 3: UserApiKey, UserPreferences, Signal, Subscription, VolumeTracking
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all models."""
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


# ── Phase 1 Models ───────────────────────────────────────────────


class User(Base):
    """User account — replaces data/users.json."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid,
    )
    email: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True, index=True,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(
        String(100), unique=True, nullable=True, index=True,
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True,
    )
    telegram_chat_id: Mapped[str | None] = mapped_column(
        String(50), unique=True, nullable=True,
    )

    # Multi-tenant fields (Phase 3)
    mode: Mapped[str] = mapped_column(
        String(20), default="paper",  # "paper" or "live"
    )
    role: Mapped[str] = mapped_column(
        String(20), default="user",  # "admin" or "user"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now,
    )


class Position(Base):
    """Trading position — replaces positions.json entries.

    Mirrors the Position dataclass in position_manager.py.
    JSONB `data` field stores extra fields that don't need indexing
    (CE state, order IDs, etc.).
    """

    __tablename__ = "positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)  # BUY/SELL
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    size: Mapped[float] = mapped_column(Float, nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    margin: Mapped[float] = mapped_column(Float, nullable=False)
    entry_type: Mapped[str] = mapped_column(
        String(20), default="standard",
    )

    # Current state
    current_price: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    roi_percent: Mapped[float] = mapped_column(Float, default=0.0)

    # Status
    status: Mapped[str] = mapped_column(
        String(20), default="OPEN",  # OPEN, PARTIAL_CLOSE, CLOSED
    )
    close_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Take profit state
    tp1_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    tp2_closed: Mapped[bool] = mapped_column(Boolean, default=False)

    # PNL tracking
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    remaining_size: Mapped[float] = mapped_column(Float, default=0.0)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now,
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # Flexible storage for non-indexed fields:
    # stop_loss, trailing_sl, chandelier_sl, take_profit_1, take_profit_2,
    # tp1_cancelled, tp2_cancelled, entry_fee, total_exit_fees,
    # ce_armed, ce_price_validated, entry_candle_ts, entry_time,
    # last_m15_close, ce_order_id, ce_order_price, pnl_percent,
    # tp1_order_id, tp2_order_id, hard_sl_order_id, linear_issue_id
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=lambda: {})

    __table_args__ = (
        Index("ix_positions_user_status", "user_id", "status"),
        Index("ix_positions_user_symbol", "user_id", "symbol"),
        Index("ix_positions_closed_at", "closed_at"),
    )


# ── Phase 3 Models (stubs for migration planning) ───────────────


class UserApiKey(Base):
    """Encrypted exchange API keys — one per exchange per user."""

    __tablename__ = "user_api_keys"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    exchange: Mapped[str] = mapped_column(
        String(20), nullable=False,  # "okx", "binance"
    )
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    api_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    passphrase_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    permissions: Mapped[str | None] = mapped_column(
        String(100), nullable=True,  # "trade", "trade,read"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now,
    )


class UserPreferences(Base):
    """Per-user trading preferences — symbols, leverage, margins."""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbols: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,  # ["BTC/USDT", "ETH/USDT"]
    )
    leverage_overrides: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,  # {"BTC/USDT": 20, "ETH/USDT": 10}
    )
    max_positions: Mapped[int] = mapped_column(Integer, default=10)
    fixed_margin: Mapped[float] = mapped_column(Float, default=2000.0)
    notifications: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True,  # {"telegram": true, "email": false}
    )


class Signal(Base):
    """Trading signals published by the signal engine."""

    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid,
    )
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    signal_type: Mapped[str] = mapped_column(
        String(10), nullable=False,  # "BUY", "SELL"
    )
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    entry_type: Mapped[str] = mapped_column(
        String(20), nullable=False,  # "standard", "ema610_h1", "ema610_h4"
    )
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now,
    )


class Subscription(Base):
    """User billing subscription (Stripe)."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=_new_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tier: Mapped[str] = mapped_column(
        String(20), nullable=False,  # "free", "basic", "pro"
    )
    price_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    stripe_sub_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
    )


class VolumeTracking(Base):
    """Monthly volume tracking for fee billing."""

    __tablename__ = "volume_tracking"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    month: Mapped[str] = mapped_column(
        String(7), primary_key=True,  # "2026-02"
    )
    total_volume: Mapped[float] = mapped_column(Float, default=0.0)
    fee_rate: Mapped[float] = mapped_column(Float, default=0.0001)
    fee_due: Mapped[float] = mapped_column(Float, default=0.0)
    fee_paid: Mapped[bool] = mapped_column(Boolean, default=False)
