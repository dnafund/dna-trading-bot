"""
Authentication module — JWT token management, password hashing, Google OAuth.

Primary store: PostgreSQL (via UserRepo).
Fallback: JSON files (data/users.json, data/allowed_emails.json) — for backward compat.
"""

import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt

logger = logging.getLogger(__name__)


# ── Token Payload ────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenPayload:
    """Immutable JWT payload — carried through every authenticated request."""
    username: str
    user_id: str
    role: str  # "admin" or "user"

# ── Config ───────────────────────────────────────────────────────

SECRET_KEY = os.environ.get("JWT_SECRET", secrets.token_urlsafe(32))
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# ── Password Hashing ────────────────────────────────────────────

security = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


# ── JWT Tokens ───────────────────────────────────────────────────

def create_access_token(
    username: str,
    user_id: str = "",
    role: str = "user",
    expires_hours: int = TOKEN_EXPIRE_HOURS,
) -> str:
    """Create JWT with username, user_id, and role claims."""
    expire = datetime.now(timezone.utc) + timedelta(hours=expires_hours)
    payload = {
        "sub": username,
        "user_id": user_id,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> Optional[TokenPayload]:
    """Verify JWT token and return TokenPayload, or None if invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return TokenPayload(
            username=username,
            user_id=payload.get("user_id", ""),
            role=payload.get("role", "user"),
        )
    except JWTError:
        return None


# ── Database-backed User Store ───────────────────────────────────

def _get_db_session():
    """Get a database session, or None if DB is not available."""
    try:
        from src.database.connection import get_session
        return get_session()
    except Exception as e:
        logger.debug(f"[AUTH] DB not available, using JSON fallback: {e}")
        return None


def _get_user_repo(session):
    """Create UserRepo from session."""
    from src.database.repositories.user_repo import UserRepo
    return UserRepo(session)


# ── JSON File Fallback (backward compat) ─────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
USERS_FILE = PROJECT_ROOT / "data" / "users.json"
ALLOWED_EMAILS_FILE = PROJECT_ROOT / "data" / "allowed_emails.json"


def _load_users_json() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_users_json(users: dict) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def _load_allowed_emails() -> list[str]:
    """Load whitelist of allowed Google emails."""
    if not ALLOWED_EMAILS_FILE.exists():
        return []
    try:
        with open(ALLOWED_EMAILS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


# ── Auth Functions ───────────────────────────────────────────────

def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Verify username/password. Tries DB first, falls back to JSON."""
    # Try DB
    ctx = _get_db_session()
    if ctx is not None:
        try:
            with ctx as session:
                repo = _get_user_repo(session)
                user = repo.find_by_username(username)
                if user and user.password_hash:
                    if verify_password(password, user.password_hash):
                        return {
                            "username": username,
                            "user_id": user.id,
                            "role": getattr(user, "role", "user"),
                            "created_at": user.created_at.isoformat() if user.created_at else None,
                        }
                    return None  # wrong password
                # User not in DB — fall through to JSON
        except Exception as e:
            logger.warning(f"[AUTH] DB auth failed, trying JSON: {e}")

    # Fallback: JSON file
    users = _load_users_json()
    user_data = users.get(username)
    if not user_data:
        return None
    if not verify_password(password, user_data["password_hash"]):
        return None
    return {"username": username, "created_at": user_data.get("created_at")}


def create_user(username: str, password: str) -> dict:
    """Create user in DB (primary) and JSON (fallback)."""
    pw_hash = hash_password(password)

    # Try DB
    ctx = _get_db_session()
    if ctx is not None:
        try:
            with ctx as session:
                repo = _get_user_repo(session)
                user = repo.create(username=username, password_hash=pw_hash)
                logger.info(f"[AUTH] User created in DB: {username}")
                return {"username": username, "user_id": user.id}
        except Exception as e:
            logger.warning(f"[AUTH] DB create failed, using JSON: {e}")

    # Fallback: JSON
    users = _load_users_json()
    users[username] = {
        "password_hash": pw_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_users_json(users)
    return {"username": username}


def change_password(username: str, old_password: str, new_password: str) -> bool:
    """Change password. Tries DB first, falls back to JSON."""
    # Try DB
    ctx = _get_db_session()
    if ctx is not None:
        try:
            with ctx as session:
                repo = _get_user_repo(session)
                user = repo.find_by_username(username)
                if user and user.password_hash:
                    if not verify_password(old_password, user.password_hash):
                        return False
                    repo.update_password(user.id, hash_password(new_password))
                    return True
        except Exception as e:
            logger.warning(f"[AUTH] DB password change failed, trying JSON: {e}")

    # Fallback: JSON
    users = _load_users_json()
    user_data = users.get(username)
    if not user_data or not verify_password(old_password, user_data["password_hash"]):
        return False
    users[username]["password_hash"] = hash_password(new_password)
    _save_users_json(users)
    return True


# ── Google OAuth ─────────────────────────────────────────────────

def verify_google_token(token: str) -> Optional[dict]:
    """Verify Google ID token and return user info if email is whitelisted.

    Tries DB first (any user with matching email), then JSON whitelist.
    Returns {"email": ..., "name": ..., "picture": ...} or None.
    """
    try:
        idinfo = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), GOOGLE_CLIENT_ID,
            clock_skew_in_seconds=10,
        )
        email = idinfo.get("email", "").lower()
        name = idinfo.get("name", email.split("@")[0])
        picture = idinfo.get("picture")
        logger.info(f"Google token verified for: {email}")
        if not email:
            logger.warning("Google token has no email")
            return None

        # Check DB for existing user
        ctx = _get_db_session()
        if ctx is not None:
            try:
                with ctx as session:
                    repo = _get_user_repo(session)
                    user = repo.find_by_email(email)
                    if user:
                        return {
                            "email": email,
                            "name": name,
                            "picture": picture,
                            "user_id": user.id,
                            "role": getattr(user, "role", "user"),
                        }
                    # Not in DB — check JSON whitelist, then auto-create
            except Exception as e:
                logger.warning(f"[AUTH] DB check failed for Google user: {e}")

        # Check JSON whitelist
        allowed = _load_allowed_emails()
        logger.info(f"Allowed emails: {allowed}")
        if allowed and email not in [e.lower() for e in allowed]:
            logger.warning(f"Google login rejected: {email} not in whitelist")
            return None

        # Auto-create user in DB if whitelisted
        created_user_id = ""
        created_role = "user"
        if ctx is not None:
            try:
                with _get_db_session() as session:
                    repo = _get_user_repo(session)
                    user = repo.find_or_create_google_user(email, name)
                    created_user_id = user.id if user else ""
                    created_role = getattr(user, "role", "user") if user else "user"
                    logger.info(f"[AUTH] Google user auto-created in DB: {email}")
            except Exception as e:
                logger.warning(f"[AUTH] Failed to auto-create Google user: {e}")

        return {
            "email": email,
            "name": name,
            "picture": picture,
            "user_id": created_user_id,
            "role": created_role,
        }

    except Exception as e:
        logger.error(f"Google token verification failed: {type(e).__name__}: {e}")
        return None


# ── FastAPI Dependency ───────────────────────────────────────────

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """FastAPI dependency: extract and validate JWT from Authorization header.
    Returns username string (backward compatible).
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_payload = verify_token(credentials.credentials)
    if token_payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_payload.username


async def get_current_user_obj(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> TokenPayload:
    """FastAPI dependency: returns full TokenPayload (username, user_id, role).

    Use this for new endpoints that need user_id or role checking.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_payload = verify_token(credentials.credentials)
    if token_payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_payload


async def require_admin(
    current_user: TokenPayload = Depends(get_current_user_obj),
) -> TokenPayload:
    """FastAPI dependency: requires admin role. Returns TokenPayload."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user
