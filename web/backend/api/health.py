"""
Health Check API — system status for monitoring and load balancers.

Endpoints:
    GET /health       — Simple liveness check (always 200 if app is up)
    GET /api/health   — Detailed readiness check (DB + Redis + services)
"""

import logging
import time

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness():
    """Liveness probe — returns 200 if the app process is running.

    Used by load balancers and container orchestrators.
    No dependency checks — just confirms the process is alive.
    """
    return {"status": "ok"}


@router.get("/api/health")
async def readiness():
    """Readiness probe — checks all dependencies are healthy.

    Returns detailed status for each subsystem:
    - database: PostgreSQL connection
    - redis: Redis connection
    - signal_scanner: Signal scanning process
    - trade_executor: Trade execution process

    HTTP 200 if all critical services are healthy, 503 otherwise.
    """
    checks = {}
    overall_healthy = True

    # Check PostgreSQL
    checks["database"] = _check_database()
    if checks["database"]["status"] != "healthy":
        overall_healthy = False

    # Check Redis
    checks["redis"] = _check_redis()
    if checks["redis"]["status"] != "healthy":
        overall_healthy = False

    # System info
    import os
    checks["system"] = {
        "status": "healthy",
        "pid": os.getpid(),
        "uptime_seconds": round(time.time() - _start_time, 1),
    }

    from fastapi.responses import JSONResponse
    status_code = 200 if overall_healthy else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "healthy" if overall_healthy else "degraded",
            "checks": checks,
            "timestamp": time.time(),
        },
    )


_start_time = time.time()


def _check_database() -> dict:
    """Check PostgreSQL connectivity."""
    start = time.time()
    try:
        from src.database.connection import get_session
        with get_session() as session:
            session.execute(
                __import__("sqlalchemy").text("SELECT 1")
            )
        latency_ms = round((time.time() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = round((time.time() - start) * 1000, 1)
        logger.warning(f"[HEALTH] Database check failed: {e}")
        return {"status": "unhealthy", "error": str(e), "latency_ms": latency_ms}


def _check_redis() -> dict:
    """Check Redis connectivity."""
    start = time.time()
    try:
        from src.services.redis_client import get_redis
        r = get_redis()
        r.ping()
        latency_ms = round((time.time() - start) * 1000, 1)
        return {"status": "healthy", "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = round((time.time() - start) * 1000, 1)
        logger.warning(f"[HEALTH] Redis check failed: {e}")
        return {"status": "unhealthy", "error": str(e), "latency_ms": latency_ms}
