"""
Admin API — admin-only endpoints for user management and revenue overview.

All endpoints require admin role (require_admin dependency).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.database.connection import get_session
from src.database.models import User
from src.database.repositories.user_repo import UserRepo
from web.backend.auth import TokenPayload, require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Request/Response Models ──────────────────────────────────────


class UserRoleUpdate(BaseModel):
    role: str = Field(..., pattern="^(admin|user)$")


class UserStatusUpdate(BaseModel):
    is_active: bool


# ── Users Management ─────────────────────────────────────────────


@router.get("/users")
async def list_users(
    limit: int = Query(default=50, ge=1, le=250),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = None,
    admin: TokenPayload = Depends(require_admin),
):
    """List all users with pagination and optional search."""
    with get_session() as session:
        repo = UserRepo(session)
        query = session.query(User)

        if search:
            from sqlalchemy import or_
            pattern = f"%{search}%"
            query = query.filter(
                or_(
                    User.username.ilike(pattern),
                    User.email.ilike(pattern),
                    User.name.ilike(pattern),
                )
            )

        total = query.count()
        users = (
            query
            .order_by(User.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        user_list = [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "name": u.name,
                "role": getattr(u, "role", "user"),
                "mode": u.mode,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in users
        ]

        return {
            "success": True,
            "data": {
                "users": user_list,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        }


@router.get("/users/{user_id}")
async def get_user_detail(
    user_id: str,
    admin: TokenPayload = Depends(require_admin),
):
    """Get detailed info for a specific user."""
    with get_session() as session:
        repo = UserRepo(session)
        user = repo.find_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Get subscription info
        sub_info = None
        try:
            from src.database.models import Subscription
            sub = (
                session.query(Subscription)
                .filter(
                    Subscription.user_id == user_id,
                    Subscription.is_active == True,
                )
                .first()
            )
            if sub:
                sub_info = {
                    "tier": sub.tier,
                    "stripe_sub_id": sub.stripe_sub_id,
                    "is_active": sub.is_active,
                    "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
                }
        except Exception as e:
            logger.debug(f"[ADMIN] Subscription query failed: {e}")

        return {
            "success": True,
            "data": {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "name": user.name,
                "role": getattr(user, "role", "user"),
                "mode": user.mode,
                "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "subscription": sub_info,
            },
        }


@router.patch("/users/{user_id}/role")
async def update_user_role(
    user_id: str,
    body: UserRoleUpdate,
    admin: TokenPayload = Depends(require_admin),
):
    """Change a user's role (admin/user)."""
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot change your own role",
        )

    with get_session() as session:
        repo = UserRepo(session)
        user = repo.find_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.role = body.role
        logger.info(f"[ADMIN] Role changed: {user.username} -> {body.role} (by {admin.username})")
        return {"success": True, "data": {"user_id": user_id, "role": body.role}}


@router.patch("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    body: UserStatusUpdate,
    admin: TokenPayload = Depends(require_admin),
):
    """Activate or deactivate a user."""
    if user_id == admin.user_id:
        raise HTTPException(
            status_code=400,
            detail="Cannot deactivate yourself",
        )

    with get_session() as session:
        repo = UserRepo(session)
        user = repo.find_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.is_active = body.is_active
        status_str = "activated" if body.is_active else "deactivated"
        logger.info(f"[ADMIN] User {status_str}: {user.username} (by {admin.username})")
        return {"success": True, "data": {"user_id": user_id, "is_active": body.is_active}}


# ── Revenue Overview ──────────────────────────────────────────────


@router.get("/revenue")
async def get_revenue_overview(
    admin: TokenPayload = Depends(require_admin),
):
    """Get revenue overview — subscriptions by tier, total MRR."""
    with get_session() as session:
        try:
            from src.database.models import Subscription
            from sqlalchemy import func

            # Count active subscriptions by tier
            tier_counts = (
                session.query(
                    Subscription.tier,
                    func.count(Subscription.id).label("count"),
                )
                .filter(Subscription.is_active == True)
                .group_by(Subscription.tier)
                .all()
            )

            from src.billing.stripe_service import TIERS

            tier_data = []
            total_mrr = 0.0
            for tier_name, count in tier_counts:
                tier_config = TIERS.get(tier_name, {})
                price = tier_config.get("price_usd", 0)
                mrr = price * count
                total_mrr += mrr
                tier_data.append({
                    "tier": tier_name,
                    "count": count,
                    "price_usd": price,
                    "mrr": round(mrr, 2),
                })

            # Total users count
            total_users = session.query(func.count()).select_from(
                session.query(UserRepo(session).model).subquery()
            ).scalar()

            return {
                "success": True,
                "data": {
                    "total_mrr": round(total_mrr, 2),
                    "total_users": total_users or 0,
                    "tiers": tier_data,
                },
            }
        except Exception as e:
            logger.error(f"[ADMIN] Revenue query failed: {e}")
            return {
                "success": True,
                "data": {"total_mrr": 0, "total_users": 0, "tiers": []},
            }


# ── System Stats ──────────────────────────────────────────────────


@router.get("/system")
async def get_system_stats(
    admin: TokenPayload = Depends(require_admin),
):
    """Get system health stats (DB, Redis, bot status)."""
    result = {"db": "unknown", "redis": "unknown", "bot": "unknown"}

    # DB check
    try:
        from sqlalchemy import text
        with get_session() as session:
            session.execute(text("SELECT 1"))
        result["db"] = "healthy"
    except Exception:
        result["db"] = "unhealthy"

    # Redis check
    try:
        import redis
        import os
        r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
        r.ping()
        result["redis"] = "healthy"
    except Exception:
        result["redis"] = "unavailable"

    # Bot check (file-based heartbeat)
    try:
        from pathlib import Path
        heartbeat = Path(__file__).resolve().parent.parent.parent.parent / "data" / "heartbeat.json"
        if heartbeat.exists():
            import json
            from datetime import datetime, timezone
            with open(heartbeat, "r") as f:
                hb = json.load(f)
            last = datetime.fromisoformat(hb.get("timestamp", ""))
            age = (datetime.now(timezone.utc) - last.replace(tzinfo=timezone.utc)).total_seconds()
            result["bot"] = "healthy" if age < 120 else "stale"
        else:
            result["bot"] = "no heartbeat"
    except Exception:
        result["bot"] = "unknown"

    return {"success": True, "data": result}
