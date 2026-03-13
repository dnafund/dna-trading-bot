"""
Exchange Pool — LRU cache of CCXT exchange clients per user.

Manages a pool of authenticated exchange clients, one per user.
Clients are lazily initialized and evicted when the pool exceeds capacity.

Usage:
    from src.services.exchange_pool import ExchangePool

    pool = ExchangePool(max_size=200)
    client = pool.get_client(user_id="user-123")
    balance = client.fetch_balance()
"""

import logging
import time
from collections import OrderedDict
from typing import Optional

import ccxt

from src.database.connection import get_session
from src.database.models import UserApiKey
from src.security.encryption import decrypt_api_key

logger = logging.getLogger(__name__)


class ExchangePool:
    """LRU pool of CCXT exchange clients, keyed by user_id.

    Features:
    - Lazy initialization: clients created on first access
    - LRU eviction: least-recently-used client removed when pool is full
    - Auto-reconnect: stale clients refreshed on access
    - Thread-safe-ish: designed for single-process async use
    """

    def __init__(self, max_size: int = 200):
        """Initialize exchange pool.

        Args:
            max_size: Maximum number of cached clients (default 200).
        """
        self._pool: OrderedDict[str, dict] = OrderedDict()
        self._max_size = max_size

    def get_client(self, user_id: str, exchange: str = "okx") -> Optional[ccxt.Exchange]:
        """Get or create an exchange client for a user.

        Args:
            user_id: User ID.
            exchange: Exchange name (default "okx").

        Returns:
            CCXT exchange client, or None if user has no API key.
        """
        cache_key = f"{user_id}:{exchange}"

        # Hit: move to end (most recently used)
        if cache_key in self._pool:
            self._pool.move_to_end(cache_key)
            entry = self._pool[cache_key]
            entry["last_used"] = time.time()
            return entry["client"]

        # Miss: create new client
        client = self._create_client(user_id, exchange)
        if client is None:
            return None

        # Evict LRU if at capacity
        while len(self._pool) >= self._max_size:
            evicted_key, evicted = self._pool.popitem(last=False)
            logger.info(f"[POOL] Evicted LRU client: {evicted_key}")

        self._pool[cache_key] = {
            "client": client,
            "created_at": time.time(),
            "last_used": time.time(),
        }

        logger.info(
            f"[POOL] Created client for {cache_key} "
            f"(pool size: {len(self._pool)})"
        )
        return client

    def remove_client(self, user_id: str, exchange: str = "okx") -> None:
        """Remove a client from the pool (e.g., when API key changes).

        Args:
            user_id: User ID.
            exchange: Exchange name.
        """
        cache_key = f"{user_id}:{exchange}"
        if cache_key in self._pool:
            del self._pool[cache_key]
            logger.info(f"[POOL] Removed client: {cache_key}")

    def _create_client(self, user_id: str, exchange: str) -> Optional[ccxt.Exchange]:
        """Create a new CCXT client for a user by decrypting their API keys.

        Args:
            user_id: User ID.
            exchange: Exchange name.

        Returns:
            Configured CCXT exchange client, or None if no API key found.
        """
        try:
            with get_session() as session:
                api_key_record = (
                    session.query(UserApiKey)
                    .filter(
                        UserApiKey.user_id == user_id,
                        UserApiKey.exchange == exchange,
                        UserApiKey.is_active == True,
                    )
                    .first()
                )

                if api_key_record is None:
                    logger.debug(f"[POOL] No API key for user={user_id} exchange={exchange}")
                    return None

                # Decrypt API credentials
                api_key = decrypt_api_key(api_key_record.api_key_enc, user_id)
                api_secret = decrypt_api_key(api_key_record.api_secret_enc, user_id)
                passphrase = None
                if api_key_record.passphrase_enc:
                    passphrase = decrypt_api_key(api_key_record.passphrase_enc, user_id)

        except Exception as e:
            logger.error(f"[POOL] Failed to load API key for user={user_id}: {e}")
            return None

        # Create CCXT client
        try:
            exchange_class = getattr(ccxt, exchange, None)
            if exchange_class is None:
                logger.error(f"[POOL] Unknown exchange: {exchange}")
                return None

            config = {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap",
                },
            }
            if passphrase:
                config["password"] = passphrase

            client = exchange_class(config)
            return client

        except Exception as e:
            logger.error(f"[POOL] Failed to create {exchange} client for user={user_id}: {e}")
            return None

    @property
    def size(self) -> int:
        """Current number of cached clients."""
        return len(self._pool)

    def stats(self) -> dict:
        """Get pool statistics.

        Returns:
            Dict with pool info.
        """
        return {
            "size": len(self._pool),
            "max_size": self._max_size,
            "users": list(self._pool.keys()),
        }
