"""
Rate Limiting Middleware — Redis-based sliding window per IP/user.

Limits:
    - Anonymous (by IP): 30 requests/minute
    - Authenticated (by user_id): 60 requests/minute
    - Webhook endpoints: 100 requests/minute (Stripe retries)
    - Auth endpoints: 10 requests/minute (brute-force protection)

Uses Redis INCR + EXPIRE for atomic sliding window counting.
Falls back to in-memory counter if Redis is unavailable.
"""

import logging
import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# ── Rate Limit Configuration ──────────────────────────────────────

RATE_LIMITS = {
    "default": {"max_requests": 60, "window_seconds": 60},
    "auth": {"max_requests": 10, "window_seconds": 60},
    "webhook": {"max_requests": 100, "window_seconds": 60},
    "anonymous": {"max_requests": 30, "window_seconds": 60},
}

# Paths that map to specific rate limit groups
_AUTH_PATHS = {"/api/auth/login", "/api/auth/google", "/api/auth/register"}
_WEBHOOK_PATHS = {"/api/billing/webhook"}
_EXEMPT_PATHS = {"/health", "/api/health", "/ws"}
_EXEMPT_PREFIXES = ("/api/backtest/",)


# ── In-memory Fallback ────────────────────────────────────────────

_memory_counters: dict[str, list[float]] = defaultdict(list)


def _check_limit_memory(key: str, max_requests: int, window: int) -> tuple[bool, int]:
    """In-memory rate check (fallback when Redis is down)."""
    now = time.time()
    cutoff = now - window
    # Clean old entries
    _memory_counters[key] = [t for t in _memory_counters[key] if t > cutoff]
    current = len(_memory_counters[key])
    if current >= max_requests:
        return False, max_requests - current
    _memory_counters[key].append(now)
    return True, max_requests - current - 1


def _check_limit_redis(key: str, max_requests: int, window: int) -> tuple[bool, int]:
    """Redis-based rate check (primary)."""
    try:
        from src.services.redis_client import get_redis
        r = get_redis()
        redis_key = f"ratelimit:{key}"
        current = r.incr(redis_key)
        if current == 1:
            r.expire(redis_key, window)
        remaining = max(0, max_requests - current)
        return current <= max_requests, remaining
    except Exception:
        return _check_limit_memory(key, max_requests, window)


# ── Middleware ─────────────────────────────────────────────────────


class RateLimitMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for request rate limiting."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path

        # Skip rate limiting for exempt paths
        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        # Skip static assets
        if path.startswith("/assets/") or path.endswith((".js", ".css", ".png", ".ico")):
            return await call_next(request)

        # Determine rate limit group
        if path in _WEBHOOK_PATHS:
            limit_config = RATE_LIMITS["webhook"]
            key = f"webhook:{_get_client_ip(request)}"
        elif path in _AUTH_PATHS:
            limit_config = RATE_LIMITS["auth"]
            key = f"auth:{_get_client_ip(request)}"
        else:
            # Try to extract user from JWT for per-user limiting
            user_key = _extract_user_key(request)
            if user_key:
                limit_config = RATE_LIMITS["default"]
                key = f"user:{user_key}"
            else:
                limit_config = RATE_LIMITS["anonymous"]
                key = f"anon:{_get_client_ip(request)}"

        allowed, remaining = _check_limit_redis(
            key,
            limit_config["max_requests"],
            limit_config["window_seconds"],
        )

        if not allowed:
            logger.warning(f"[RATE_LIMIT] Blocked: {key} path={path}")
            return Response(
                content='{"detail": "Rate limit exceeded. Please try again later."}',
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(limit_config["window_seconds"]),
                    "X-RateLimit-Limit": str(limit_config["max_requests"]),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(limit_config["max_requests"])
        response.headers["X-RateLimit-Remaining"] = str(remaining)

        return response


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For behind reverse proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    if client:
        return client.host
    return "unknown"


def _extract_user_key(request: Request) -> str | None:
    """Try to extract username from JWT in Authorization header."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    try:
        from web.backend.auth import verify_token
        return verify_token(token)
    except Exception:
        return None
