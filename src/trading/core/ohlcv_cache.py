"""
OHLCV Cache — TTL-based in-memory cache for exchange OHLCV data.

Eliminates redundant API calls within a single scan cycle.
SignalDetector fetches H4/H1/M15 data multiple times per symbol;
this cache deduplicates those calls.

Architecture:
    OHLCVCache wraps exchange.fetch_ohlcv with a TTL cache.
    Key: (symbol, timeframe, limit)
    TTL: Configurable per-call, default 60s (one scan cycle).
"""

import time
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class OHLCVCache:
    """TTL-based in-memory cache for OHLCV data.

    Usage:
        cache = OHLCVCache(exchange_client)
        df = cache.fetch(symbol, timeframe, limit)  # Fetches from exchange
        df = cache.fetch(symbol, timeframe, limit)  # Returns cached copy
    """

    def __init__(self, exchange_client, default_ttl: float = 55.0):
        """
        Args:
            exchange_client: Exchange client with fetch_ohlcv method.
            default_ttl: Default cache TTL in seconds. Set slightly below
                         scan interval (60s) to avoid stale data.
        """
        self._exchange = exchange_client
        self._default_ttl = default_ttl
        self._cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
        self._hits = 0
        self._misses = 0

    def fetch(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        ttl: Optional[float] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data with caching.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            timeframe: Candle timeframe (e.g. "4h", "1h", "15m")
            limit: Number of candles to fetch
            ttl: Cache TTL in seconds (overrides default)

        Returns:
            DataFrame with OHLCV data
        """
        key = (symbol, timeframe, limit)
        now = time.monotonic()
        cache_ttl = ttl if ttl is not None else self._default_ttl

        cached = self._cache.get(key)
        if cached is not None:
            cached_time, cached_df = cached
            if now - cached_time < cache_ttl:
                self._hits += 1
                return cached_df

        # Cache miss — fetch from exchange
        df = self._exchange.fetch_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            limit=limit,
        )
        self._cache[key] = (now, df)
        self._misses += 1
        return df

    def invalidate(self, symbol: Optional[str] = None) -> None:
        """Remove cached entries.

        Args:
            symbol: If provided, only invalidate entries for this symbol.
                    If None, clear entire cache.
        """
        if symbol is None:
            self._cache.clear()
        else:
            keys_to_remove = [k for k in self._cache if k[0] == symbol]
            for k in keys_to_remove:
                del self._cache[k]

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        now = time.monotonic()
        expired = [
            k for k, (cached_time, _) in self._cache.items()
            if now - cached_time >= self._default_ttl
        ]
        for k in expired:
            del self._cache[k]
        return len(expired)

    @property
    def stats(self) -> dict:
        """Cache hit/miss statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total * 100:.1f}%" if total > 0 else "0%",
            "cached_entries": len(self._cache),
        }

    def reset_stats(self) -> None:
        """Reset hit/miss counters."""
        self._hits = 0
        self._misses = 0
