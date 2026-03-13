"""
User management API — registration, API key CRUD, preferences.

All endpoints require JWT authentication (get_current_user dependency).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.database.connection import get_session
from src.database.models import UserApiKey, UserPreferences
from src.database.repositories.user_repo import UserRepo
from src.security.encryption import encrypt_api_key, decrypt_api_key
from web.backend.auth import TokenPayload, get_current_user_obj

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


# ── Request/Response Models ─────────────────────────────────────


class ApiKeyCreate(BaseModel):
    exchange: str = Field(..., pattern="^(okx|binance)$")
    api_key: str = Field(..., min_length=10)
    api_secret: str = Field(..., min_length=10)
    passphrase: Optional[str] = None


class ApiKeyResponse(BaseModel):
    id: str
    exchange: str
    api_key_masked: str  # Last 4 chars only
    is_active: bool
    permissions: Optional[str] = None
    created_at: str


class PreferencesUpdate(BaseModel):
    symbols: Optional[list[str]] = None
    leverage_overrides: Optional[dict[str, int]] = None
    max_positions: Optional[int] = Field(None, ge=1, le=50)
    fixed_margin: Optional[float] = Field(None, ge=10, le=100000)
    notifications: Optional[dict] = None


class PreferencesResponse(BaseModel):
    symbols: Optional[list[str]] = None
    leverage_overrides: Optional[dict] = None
    max_positions: int = 10
    fixed_margin: float = 2000.0
    notifications: Optional[dict] = None


# ── Profile ──────────────────────────────────────────────────────


@router.get("/me")
async def get_profile(current_user: TokenPayload = Depends(get_current_user_obj)):
    """Get current user's profile."""
    with get_session() as session:
        repo = UserRepo(session)
        user = repo.find_by_username(current_user.username)
        if not user:
            # Try by email (Google OAuth users)
            user = repo.find_by_email(current_user.username)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "name": user.name,
            "mode": user.mode,
            "role": getattr(user, "role", "user"),
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        }


# ── API Keys ────────────────────────────────────────────────────


@router.get("/api-keys")
async def list_api_keys(current_user: TokenPayload = Depends(get_current_user_obj)):
    """List user's API keys (masked)."""
    user_id = current_user.user_id

    with get_session() as session:
        keys = (
            session.query(UserApiKey)
            .filter(UserApiKey.user_id == user_id)
            .order_by(UserApiKey.created_at.desc())
            .all()
        )
        return [
            ApiKeyResponse(
                id=k.id,
                exchange=k.exchange,
                api_key_masked=f"****{_unmask_last4(k.api_key_enc, user_id)}",
                is_active=k.is_active,
                permissions=k.permissions,
                created_at=k.created_at.isoformat() if k.created_at else "",
            )
            for k in keys
        ]


@router.post("/api-keys", status_code=201)
async def add_api_key(
    body: ApiKeyCreate,
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Add a new API key (encrypted at rest)."""
    user_id = current_user.user_id

    with get_session() as session:
        # Check for existing key on same exchange
        existing = (
            session.query(UserApiKey)
            .filter(
                UserApiKey.user_id == user_id,
                UserApiKey.exchange == body.exchange,
                UserApiKey.is_active == True,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Active API key already exists for {body.exchange}. "
                "Deactivate it first.",
            )

        # Encrypt credentials
        key_record = UserApiKey(
            user_id=user_id,
            exchange=body.exchange,
            api_key_enc=encrypt_api_key(body.api_key, user_id),
            api_secret_enc=encrypt_api_key(body.api_secret, user_id),
            passphrase_enc=(
                encrypt_api_key(body.passphrase, user_id)
                if body.passphrase
                else None
            ),
            is_active=True,
            permissions="trade,read",
        )
        session.add(key_record)
        session.flush()

        logger.info(
            f"[API] API key added: user={user_id} exchange={body.exchange}"
        )
        return {"id": key_record.id, "exchange": body.exchange, "status": "created"}


@router.delete("/api-keys/{key_id}")
async def deactivate_api_key(
    key_id: str,
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Deactivate an API key (soft delete)."""
    user_id = current_user.user_id

    with get_session() as session:
        key_record = (
            session.query(UserApiKey)
            .filter(UserApiKey.id == key_id, UserApiKey.user_id == user_id)
            .first()
        )
        if not key_record:
            raise HTTPException(status_code=404, detail="API key not found")

        key_record.is_active = False
        logger.info(f"[API] API key deactivated: {key_id} user={user_id}")
        return {"status": "deactivated"}


# ── Preferences ─────────────────────────────────────────────────


@router.get("/preferences")
async def get_preferences(current_user: TokenPayload = Depends(get_current_user_obj)):
    """Get user's trading preferences."""
    user_id = current_user.user_id

    with get_session() as session:
        prefs = (
            session.query(UserPreferences)
            .filter(UserPreferences.user_id == user_id)
            .first()
        )
        if not prefs:
            return PreferencesResponse()

        return PreferencesResponse(
            symbols=prefs.symbols,
            leverage_overrides=prefs.leverage_overrides,
            max_positions=prefs.max_positions,
            fixed_margin=prefs.fixed_margin,
            notifications=prefs.notifications,
        )


@router.put("/preferences")
async def update_preferences(
    body: PreferencesUpdate,
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Update user's trading preferences."""
    user_id = current_user.user_id

    with get_session() as session:
        prefs = (
            session.query(UserPreferences)
            .filter(UserPreferences.user_id == user_id)
            .first()
        )

        if not prefs:
            prefs = UserPreferences(user_id=user_id)
            session.add(prefs)

        if body.symbols is not None:
            prefs.symbols = body.symbols
        if body.leverage_overrides is not None:
            prefs.leverage_overrides = body.leverage_overrides
        if body.max_positions is not None:
            prefs.max_positions = body.max_positions
        if body.fixed_margin is not None:
            prefs.fixed_margin = body.fixed_margin
        if body.notifications is not None:
            prefs.notifications = body.notifications

        logger.info(f"[API] Preferences updated: user={user_id}")
        return {"status": "updated"}


# ── Telegram Linking ─────────────────────────────────────────────


@router.post("/telegram/link-code")
async def generate_telegram_link_code(
    current_user: TokenPayload = Depends(get_current_user_obj),
):
    """Generate a 6-digit code to link Telegram account."""
    user_id = current_user.user_id

    from src.services.telegram_multiuser import generate_link_code

    code = generate_link_code(user_id, ttl_seconds=600)
    return {
        "code": code,
        "expires_in": 600,
        "instructions": "Send /link CODE to the bot on Telegram",
    }


# ── Helpers ──────────────────────────────────────────────────────


def _unmask_last4(encrypted_key: str, user_id: str) -> str:
    """Decrypt API key and return last 4 characters."""
    try:
        decrypted = decrypt_api_key(encrypted_key, user_id)
        return decrypted[-4:] if len(decrypted) >= 4 else "****"
    except Exception:
        return "****"
