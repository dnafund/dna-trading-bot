"""
Request Logging Middleware — structured access logs for API monitoring.

Logs: method, path, status, duration, client IP, user (if authenticated).
Skips static assets and WebSocket upgrade requests.
"""

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger("api.access")

# Paths to skip logging (noisy / static)
_SKIP_PREFIXES = ("/assets/", "/health", "/favicon.ico")
_SKIP_EXTENSIONS = (".js", ".css", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2")


class RequestLoggerMiddleware(BaseHTTPMiddleware):
    """Logs API requests with timing, status, and user identification."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path

        # Skip static assets and health checks
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            return await call_next(request)
        if any(path.endswith(ext) for ext in _SKIP_EXTENSIONS):
            return await call_next(request)

        # Skip WebSocket upgrades
        if request.headers.get("upgrade", "").lower() == "websocket":
            return await call_next(request)

        start = time.time()
        method = request.method
        client_ip = _get_client_ip(request)

        try:
            response = await call_next(request)
            duration_ms = round((time.time() - start) * 1000, 1)

            # Determine log level by status code
            status = response.status_code
            if status >= 500:
                log_fn = logger.error
            elif status >= 400:
                log_fn = logger.warning
            else:
                log_fn = logger.info

            log_fn(
                f"{method} {path} {status} {duration_ms}ms "
                f"ip={client_ip}"
            )

            # Add timing header
            response.headers["X-Response-Time"] = f"{duration_ms}ms"
            return response

        except Exception as e:
            duration_ms = round((time.time() - start) * 1000, 1)
            logger.error(
                f"{method} {path} 500 {duration_ms}ms "
                f"ip={client_ip} error={type(e).__name__}: {e}"
            )
            raise


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    if client:
        return client.host
    return "unknown"
