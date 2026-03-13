"""
Redis client — pub/sub for signals, price cache, rate limiting.

Channels:
    signals:{symbol}  — published by SignalScanner, consumed by TradeExecutor
    prices:{symbol}    — cached current prices

Usage:
    from src.services.redis_client import get_redis, publish_signal, subscribe_signals

    publish_signal("BTCUSDT", signal_data)
    async for signal in subscribe_signals(["BTCUSDT", "ETHUSDT"]):
        process(signal)
"""

import asyncio
import json
import logging
import os
from typing import AsyncIterator, Optional

import redis
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Sync client (for simple operations)
_sync_client: Optional[redis.Redis] = None

# Async client (for pub/sub)
_async_client: Optional[aioredis.Redis] = None


def get_redis() -> redis.Redis:
    """Get synchronous Redis client (lazy init)."""
    global _sync_client
    if _sync_client is None:
        _sync_client = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
            retry_on_timeout=True,
        )
    return _sync_client


async def get_async_redis() -> aioredis.Redis:
    """Get async Redis client (lazy init)."""
    global _async_client
    if _async_client is None:
        _async_client = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=5,
        )
    return _async_client


def publish_signal(symbol: str, signal_data: dict) -> int:
    """Publish a trading signal to Redis channel.

    Args:
        symbol: Trading pair (e.g., "BTCUSDT")
        signal_data: Signal dict with symbol, signal_type, entry_price, etc.

    Returns:
        Number of subscribers that received the message.
    """
    channel = f"signals:{symbol}"
    payload = json.dumps(signal_data, default=str)
    r = get_redis()
    count = r.publish(channel, payload)
    logger.info(f"[REDIS] Published signal on {channel} → {count} subscribers")
    return count


async def subscribe_signals(
    symbols: list[str],
) -> AsyncIterator[dict]:
    """Subscribe to signal channels for given symbols.

    Yields signal dicts as they arrive.

    Args:
        symbols: List of trading pairs to subscribe to.
    """
    r = await get_async_redis()
    pubsub = r.pubsub()
    channels = [f"signals:{s}" for s in symbols]
    await pubsub.subscribe(*channels)
    logger.info(f"[REDIS] Subscribed to {len(channels)} signal channels")

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                data = json.loads(message["data"])
                yield data
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning(f"[REDIS] Invalid signal message: {e}")
    finally:
        await pubsub.unsubscribe(*channels)
        await pubsub.close()


def cache_price(symbol: str, price: float, ttl: int = 30) -> None:
    """Cache current price in Redis with TTL.

    Args:
        symbol: Trading pair.
        price: Current price.
        ttl: Time-to-live in seconds (default 30s).
    """
    r = get_redis()
    r.setex(f"prices:{symbol}", ttl, str(price))


def get_cached_price(symbol: str) -> Optional[float]:
    """Get cached price from Redis.

    Returns:
        Price float or None if not cached/expired.
    """
    r = get_redis()
    val = r.get(f"prices:{symbol}")
    if val is not None:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return None


def increment_volume(user_id: str, month: str, volume_usd: float) -> float:
    """Atomically increment user's monthly trading volume.

    Args:
        user_id: User ID.
        month: Month string (e.g., "2026-02").
        volume_usd: Volume to add in USD.

    Returns:
        New total volume.
    """
    r = get_redis()
    key = f"volume:{user_id}:{month}"
    new_total = r.incrbyfloat(key, volume_usd)
    r.expire(key, 60 * 60 * 24 * 45)  # 45-day TTL
    return new_total


def check_rate_limit(user_id: str, action: str, max_per_minute: int = 10) -> bool:
    """Check if user is within rate limit.

    Args:
        user_id: User ID.
        action: Action type (e.g., "trade", "api_call").
        max_per_minute: Maximum actions per minute.

    Returns:
        True if within limit, False if rate limited.
    """
    r = get_redis()
    key = f"ratelimit:{user_id}:{action}"
    current = r.incr(key)
    if current == 1:
        r.expire(key, 60)
    return current <= max_per_minute


def close_redis() -> None:
    """Close Redis connections (for graceful shutdown)."""
    global _sync_client, _async_client
    if _sync_client is not None:
        _sync_client.close()
        _sync_client = None
    if _async_client is not None:
        asyncio.get_event_loop().run_until_complete(_async_client.close())
        _async_client = None
    logger.info("[REDIS] Connections closed")
