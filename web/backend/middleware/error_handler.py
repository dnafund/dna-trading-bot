"""
Error Handler — global exception handling + optional Sentry integration.

Features:
    - Catches unhandled exceptions → returns structured JSON error
    - Strips sensitive data from error responses
    - Optional Sentry integration (if SENTRY_DSN is set)
    - Logs full tracebacks server-side, returns safe messages to client
"""

import logging
import os
import traceback

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# ── Sentry Integration (optional) ─────────────────────────────────

_sentry_initialized = False
SENTRY_DSN = os.getenv("SENTRY_DSN", "")


def init_sentry() -> None:
    """Initialize Sentry error tracking if SENTRY_DSN is configured."""
    global _sentry_initialized
    if not SENTRY_DSN:
        logger.info("[ERROR_HANDLER] Sentry not configured (no SENTRY_DSN)")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[
                FastApiIntegration(transaction_style="endpoint"),
                SqlalchemyIntegration(),
            ],
            traces_sample_rate=0.1,  # 10% of requests for performance monitoring
            profiles_sample_rate=0.1,
            environment=os.getenv("ENVIRONMENT", "production"),
            release=os.getenv("APP_VERSION", "7.4.6"),
            send_default_pii=False,  # Don't send personally identifiable info
        )
        _sentry_initialized = True
        logger.info("[ERROR_HANDLER] Sentry initialized")
    except ImportError:
        logger.info("[ERROR_HANDLER] sentry-sdk not installed, skipping")
    except Exception as e:
        logger.warning(f"[ERROR_HANDLER] Sentry init failed: {e}")


# ── Error Response Helpers ─────────────────────────────────────────

# Sensitive field names to strip from error details
_SENSITIVE_FIELDS = {
    "password", "secret", "key", "token", "api_key",
    "passphrase", "credential", "authorization",
}


def _safe_error_message(exc: Exception) -> str:
    """Convert exception to a safe error message (no secrets leaked)."""
    msg = str(exc)
    # Redact any field that looks like it contains sensitive data
    for field in _SENSITIVE_FIELDS:
        if field.lower() in msg.lower():
            return "An internal error occurred. Check server logs for details."
    # Truncate very long messages
    if len(msg) > 200:
        return msg[:200] + "..."
    return msg


# ── Middleware ─────────────────────────────────────────────────────


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Catches unhandled exceptions and returns structured JSON errors."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> JSONResponse:
        try:
            response = await call_next(request)
            return response
        except Exception as exc:
            # Log full traceback server-side
            logger.error(
                f"[ERROR] Unhandled exception on {request.method} {request.url.path}: "
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            )

            # Report to Sentry if available
            if _sentry_initialized:
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                except Exception:
                    pass

            # Return safe JSON error to client
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": _safe_error_message(exc),
                    "detail": "Internal server error",
                },
            )
