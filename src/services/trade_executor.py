"""
Trade Executor — subscribes to Redis signals and executes trades per-user.

Consumes signals published by SignalScanner and executes trades
for each user based on their preferences, API keys, and position limits.

Architecture:
    SignalScanner → Redis pub/sub → TradeExecutor (this)
                                      ├─ User 1: check prefs → place order
                                      ├─ User 2: check prefs → place order
                                      └─ User N: ...

Usage:
    python -m src.services.trade_executor
"""

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.database.connection import get_session, init_db
from src.database.models import Position, User, UserApiKey, UserPreferences
from src.database.repositories.position_repo import PositionRepo
from src.database.repositories.user_repo import UserRepo
from src.services.redis_client import (
    subscribe_signals,
    cache_price,
    get_cached_price,
    increment_volume,
    check_rate_limit,
    get_redis,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/trade_executor.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


class TradeExecutor:
    """Subscribes to Redis signals and executes trades for all active users.

    For each incoming signal:
    1. Query active users subscribed to that symbol
    2. For each user: check position limits, balance, preferences
    3. Place order via user's API key (or paper trade)
    4. Store position in DB
    5. Send notification
    """

    def __init__(self):
        self.is_running = False
        self._exchange_pool: dict[str, object] = {}  # user_id → CCXT client

        # Position update timing
        self._last_position_update = 0
        self._position_update_interval = 30  # seconds

        init_db()
        logger.info("[EXECUTOR] Trade Executor initialized")

    async def start(self) -> None:
        """Main loop — subscribe to all signals and process them."""
        self.is_running = True

        # Verify Redis
        try:
            r = get_redis()
            r.ping()
            logger.info("[EXECUTOR] Redis connection verified")
        except Exception as e:
            logger.error(f"[EXECUTOR] Redis connection failed: {e}")
            raise

        # Get all symbols any user is subscribed to
        all_symbols = self._get_all_subscribed_symbols()
        if not all_symbols:
            # Fallback: subscribe to all default symbols
            from src.trading.core.config import DEFAULT_SYMBOLS
            all_symbols = list(DEFAULT_SYMBOLS)

        logger.info(f"[EXECUTOR] Subscribing to {len(all_symbols)} symbols")

        # Run signal consumer and position updater concurrently
        try:
            await asyncio.gather(
                self._consume_signals(all_symbols),
                self._position_update_loop(),
            )
        except KeyboardInterrupt:
            logger.info("[EXECUTOR] Stopped by user")
        except Exception as e:
            logger.error(f"[EXECUTOR] Fatal error: {e}", exc_info=True)
        finally:
            self.is_running = False
            logger.info("[EXECUTOR] Trade Executor stopped")

    async def _consume_signals(self, symbols: list[str]) -> None:
        """Subscribe to Redis and process incoming signals."""
        async for signal_data in subscribe_signals(symbols):
            if not self.is_running:
                break
            try:
                await self._process_signal(signal_data)
            except Exception as e:
                logger.error(
                    f"[EXECUTOR] Error processing signal: {e}", exc_info=True
                )

    async def _process_signal(self, signal_data: dict) -> None:
        """Process a signal for all eligible users.

        Args:
            signal_data: Signal dict from Redis.
        """
        symbol = signal_data.get("symbol", "")
        signal_type = signal_data.get("signal_type", "")
        entry_price = float(signal_data.get("entry_price", 0))
        entry_type = signal_data.get("entry_type", "standard")

        logger.info(
            f"[EXECUTOR] Received: {signal_type} {symbol} "
            f"@ ${entry_price:.2f} ({entry_type})"
        )

        # Get eligible users for this symbol
        eligible_users = self._get_eligible_users(symbol)

        for user in eligible_users:
            try:
                await self._execute_for_user(user, signal_data)
            except Exception as e:
                logger.error(
                    f"[EXECUTOR] Error executing for user {user.id}: {e}"
                )

    def _get_eligible_users(self, symbol: str) -> list:
        """Get active users subscribed to this symbol.

        Returns list of User objects that:
        1. Are active
        2. Have an active subscription (or free tier)
        3. Are subscribed to this symbol
        """
        try:
            with get_session() as session:
                repo = UserRepo(session)
                users = repo.list_active()

                eligible = []
                for user in users:
                    # Check user preferences for symbol subscription
                    prefs = (
                        session.query(UserPreferences)
                        .filter(UserPreferences.user_id == user.id)
                        .first()
                    )

                    # If no prefs, user trades all symbols (default)
                    if prefs is None or prefs.symbols is None:
                        eligible.append(user)
                        continue

                    # Check if symbol is in user's subscribed list
                    if symbol in prefs.symbols:
                        eligible.append(user)

                # Detach from session to use outside
                for u in eligible:
                    session.expunge(u)

                return eligible

        except Exception as e:
            logger.error(f"[EXECUTOR] Error getting eligible users: {e}")
            return []

    async def _execute_for_user(self, user, signal_data: dict) -> None:
        """Execute a trade for a specific user.

        Args:
            user: User object.
            signal_data: Signal dict from Redis.
        """
        user_id = user.id
        symbol = signal_data["symbol"]
        signal_type = signal_data["signal_type"]
        entry_price = float(signal_data["entry_price"])
        entry_type = signal_data.get("entry_type", "standard")

        # Rate limit check
        if not check_rate_limit(user_id, "trade", max_per_minute=5):
            logger.warning(f"[EXECUTOR] Rate limited: user={user_id}")
            return

        with get_session() as session:
            pos_repo = PositionRepo(session)

            # Check position limits
            open_count = pos_repo.count_open_by_user(user_id)
            prefs = (
                session.query(UserPreferences)
                .filter(UserPreferences.user_id == user_id)
                .first()
            )
            max_positions = prefs.max_positions if prefs else 10
            if open_count >= max_positions:
                logger.debug(
                    f"[EXECUTOR] Max positions reached for user={user_id}"
                )
                return

            # Check duplicate: already has position in this symbol+entry_type?
            existing = pos_repo.find_by_user_and_symbol(
                user_id, symbol, status="OPEN"
            )
            if entry_type == "standard" and any(
                p.entry_type == "standard" for p in existing
            ):
                return  # Already has standard position

            # Calculate position size
            fixed_margin = prefs.fixed_margin if prefs else 2000.0
            leverage_overrides = prefs.leverage_overrides if prefs else {}

            from src.trading.core.config import LEVERAGE

            leverage = leverage_overrides.get(symbol, LEVERAGE.get(symbol, LEVERAGE.get("default", 5)))
            margin = min(fixed_margin, fixed_margin)  # Use fixed margin
            size = (margin * leverage) / entry_price

            # Create position in DB
            position_id = f"{symbol}_{int(time.time() * 1000)}"
            tp1 = signal_data.get("take_profit_1")
            tp2 = signal_data.get("take_profit_2")

            from src.trading.core.config import RISK_MANAGEMENT

            hard_sl_pct = RISK_MANAGEMENT.get("hard_sl_percent", 20)
            if signal_type == "BUY":
                stop_loss = entry_price * (1 - hard_sl_pct / 100 / leverage)
            else:
                stop_loss = entry_price * (1 + hard_sl_pct / 100 / leverage)

            extra_data = {
                "stop_loss": stop_loss,
                "take_profit_1": float(tp1) if tp1 else None,
                "take_profit_2": float(tp2) if tp2 else None,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "ce_armed": False,
                "ce_price_validated": False,
            }

            position = pos_repo.create(
                id=position_id,
                user_id=user_id,
                symbol=symbol,
                side=signal_type,
                entry_price=entry_price,
                size=size,
                leverage=leverage,
                margin=margin,
                entry_type=entry_type,
                remaining_size=size,
                data=extra_data,
            )

            # Track volume for billing
            volume_usd = size * entry_price
            month = datetime.now(timezone.utc).strftime("%Y-%m")
            increment_volume(user_id, month, volume_usd)

            logger.info(
                f"[EXECUTOR] Position opened: {signal_type} {symbol} "
                f"@ ${entry_price:.2f} size={size:.6f} "
                f"user={user_id} ({entry_type})"
            )

            # TODO Phase 3: Execute on exchange via user's API key
            # TODO Phase 4: Send Telegram notification to user

    async def _position_update_loop(self) -> None:
        """Periodically update all open positions with current prices."""
        while self.is_running:
            current_time = time.time()
            if current_time - self._last_position_update >= self._position_update_interval:
                await self._update_all_positions()
                self._last_position_update = current_time
            await asyncio.sleep(1)

    async def _update_all_positions(self) -> None:
        """Fetch current prices and update all open positions."""
        try:
            with get_session() as session:
                # Get all open positions across all users
                open_positions = (
                    session.query(Position)
                    .filter(Position.status.in_(["OPEN", "PARTIAL_CLOSE"]))
                    .all()
                )

                if not open_positions:
                    return

                # Group by symbol to minimize API calls
                symbols = set(p.symbol for p in open_positions)
                prices = {}
                for symbol in symbols:
                    cached = get_cached_price(symbol)
                    if cached:
                        prices[symbol] = cached
                    else:
                        try:
                            # Fetch from exchange
                            ticker = self._get_exchange_client().fetch_ticker(symbol)
                            if ticker and "last" in ticker:
                                price = float(ticker["last"])
                                prices[symbol] = price
                                cache_price(symbol, price)
                        except Exception as e:
                            logger.debug(f"[EXECUTOR] Price fetch failed for {symbol}: {e}")

                # Update each position
                pos_repo = PositionRepo(session)
                for pos in open_positions:
                    price = prices.get(pos.symbol)
                    if price is None:
                        continue

                    # Calculate PNL
                    if pos.side == "BUY":
                        pnl_pct = (price - pos.entry_price) / pos.entry_price
                    else:
                        pnl_pct = (pos.entry_price - price) / pos.entry_price

                    pnl_usd = pnl_pct * pos.margin * pos.leverage
                    roi_pct = pnl_pct * pos.leverage * 100

                    pos_repo.update_pnl(
                        pos.id,
                        current_price=price,
                        pnl_usd=pnl_usd + pos.realized_pnl,
                        roi_percent=roi_pct,
                    )

                logger.debug(
                    f"[EXECUTOR] Updated {len(open_positions)} positions "
                    f"across {len(symbols)} symbols"
                )

        except Exception as e:
            logger.error(f"[EXECUTOR] Position update error: {e}")

    def _get_exchange_client(self):
        """Get a shared exchange client for price fetching (read-only)."""
        if not hasattr(self, "_shared_exchange"):
            from src.trading.exchanges.okx import OKXFuturesClient

            self._shared_exchange = OKXFuturesClient(
                os.getenv("OKX_API_KEY", ""),
                os.getenv("OKX_API_SECRET", ""),
                os.getenv("OKX_PASSPHRASE", ""),
            )
        return self._shared_exchange

    def _get_all_subscribed_symbols(self) -> list[str]:
        """Get union of all symbols any user is subscribed to."""
        try:
            with get_session() as session:
                all_prefs = session.query(UserPreferences).all()
                symbols = set()
                for pref in all_prefs:
                    if pref.symbols:
                        symbols.update(pref.symbols)
                return list(symbols) if symbols else []
        except Exception as e:
            logger.debug(f"[EXECUTOR] Error getting subscribed symbols: {e}")
            return []


async def main() -> None:
    executor = TradeExecutor()
    await executor.start()


if __name__ == "__main__":
    asyncio.run(main())
