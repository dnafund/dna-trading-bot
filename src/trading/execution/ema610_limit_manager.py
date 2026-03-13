"""
EMA610 Limit Order Manager

Pre-places limit orders at EMA610 price for H1/H4 entries.
Updates orders when new candle closes (EMA610 value changes).
Replaces the legacy "scan realtime price → market order" approach.
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

# Persistence files
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
PENDING_ORDERS_FILE = _PROJECT_ROOT / "data" / "ema610_pending_orders.json"
CANCEL_COOLDOWN_FILE = _PROJECT_ROOT / "data" / "ema610_cancel_cooldown.json"

# Max age before auto-cancel (seconds)
MAX_ORDER_AGE_H1 = 48 * 3600   # 48 hours
MAX_ORDER_AGE_H4 = 96 * 3600   # 96 hours


@dataclass
class PendingEMA610Order:
    """A pending limit order waiting to fill at EMA610 price."""
    order_id: str
    symbol: str
    timeframe: str          # "h1" or "h4"
    side: str               # "BUY" or "SELL"
    ema610_price: float     # EMA610 value used for this order
    limit_price: float      # Actual limit price placed
    leverage: int
    margin: float           # Margin in USDT
    h4_trend: str           # "UPTREND" or "DOWNTREND"
    tp1_price: float
    tp2_price: float
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())


class EMA610LimitManager:
    """
    Manages lifecycle of pre-placed limit orders at EMA610 price.

    Responsibilities:
    - Place new limit orders for qualified symbols
    - Cancel & replace when EMA610 shifts beyond tolerance
    - Cancel when trend changes or symbol no longer qualifies
    - Detect fills and trigger position creation
    - Persist state to JSON for bot restart recovery
    """

    def __init__(self, exchange, position_manager, mode: str = "paper"):
        self.exchange = exchange
        self.position_manager = position_manager
        self.mode = mode
        self.pending_orders: Dict[str, PendingEMA610Order] = {}  # key: "SYMBOL_h1"
        self._cancel_cooldowns: Dict[str, float] = {}  # key: "SYMBOL_tf" → expiry timestamp
        self._load()
        self._load_cooldowns()

    # ── Public API ──────────────────────────────────────────────

    async def update(
        self,
        qualified_symbols: List[Dict[str, Any]],
        tolerance: float = 0.005
    ):
        """
        Update pending limit orders based on current EMA610 values.

        Args:
            qualified_symbols: List of dicts with keys:
                symbol, side, ema610_val, timeframe, leverage, margin,
                h4_trend, tp1_price, tp2_price
            tolerance: Min EMA610 change ratio to trigger re-place
        """
        # Build set of currently qualified keys
        qualified_keys = set()
        for info in qualified_symbols:
            key = f"{info['symbol']}_{info['timeframe']}"
            qualified_keys.add(key)

            # Check manual cancel cooldown
            remaining = self._cooldown_remaining(key)
            if remaining > 0:
                hours_left = remaining / 3600
                logger.debug(
                    f"[EMA610-LMT] {info['symbol']} {info['timeframe']}: "
                    f"Skipped — manual cancel cooldown ({hours_left:.1f}h remaining)"
                )
                continue

            existing = self.pending_orders.get(key)
            if existing:
                # Check if EMA610 changed enough to re-place
                price_change = abs(info['ema610_val'] - existing.ema610_price) / existing.ema610_price
                if price_change >= tolerance:
                    logger.info(
                        f"[EMA610-LMT] {info['symbol']} {info['timeframe']}: "
                        f"EMA610 changed {price_change:.4%} (>{tolerance:.4%}) — re-placing"
                    )
                    await self._cancel_order(key)
                    await self._place_order(key, info)
                # else: order still valid, keep it
            else:
                # New qualified symbol — place limit order
                await self._place_order(key, info)

        # Cancel orders for symbols no longer qualified
        stale_keys = [k for k in list(self.pending_orders.keys()) if k not in qualified_keys]
        for key in stale_keys:
            order = self.pending_orders[key]
            logger.info(
                f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                f"No longer qualified — cancelling"
            )
            await self._cancel_order(key)

        # Auto-cancel old orders
        await self._cancel_stale_orders()
        self._save()

    async def check_fills(self) -> List[Dict[str, Any]]:
        """
        Check all pending orders for fills.

        Returns:
            List of fill info dicts for orders that were filled.
        """
        fills = []
        keys_to_remove = []

        for key, order in list(self.pending_orders.items()):
            try:
                if self.mode == "paper":
                    filled = self._check_paper_fill(order)
                else:
                    filled = self._check_live_fill(order)

                logger.debug(
                    f"[EMA610-LMT] check_fills: {order.symbol} {order.timeframe} "
                    f"order={order.order_id[:12]} filled={filled}"
                )

                if filled:
                    logger.info(
                        f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                        f"FILLED @ {order.limit_price:.6f}"
                    )
                    fills.append({
                        'symbol': order.symbol,
                        'side': order.side,
                        'entry_price': order.limit_price,
                        'entry_type': f"ema610_{order.timeframe}",
                        'leverage': order.leverage,
                        'margin': order.margin,
                        'h4_trend': order.h4_trend,
                        'tp1_price': order.tp1_price,
                        'tp2_price': order.tp2_price,
                    })
                    keys_to_remove.append(key)
            except Exception as e:
                logger.error(f"[EMA610-LMT] Error checking fill for {key}: {e}")

        for key in keys_to_remove:
            self.pending_orders.pop(key, None)

        if keys_to_remove:
            self._save()

        return fills

    async def cancel_all(self, timeframe: Optional[str] = None):
        """Cancel all pending orders (optionally filtered by timeframe)."""
        keys = [
            k for k, o in list(self.pending_orders.items())
            if timeframe is None or o.timeframe == timeframe
        ]
        for key in keys:
            await self._cancel_order(key)
        if keys:
            self._save()

    async def cancel_for_symbol(self, symbol: str, timeframe: Optional[str] = None):
        """Cancel pending orders for a specific symbol."""
        keys = [
            k for k, o in list(self.pending_orders.items())
            if o.symbol == symbol and (timeframe is None or o.timeframe == timeframe)
        ]
        for key in keys:
            await self._cancel_order(key)
        if keys:
            self._save()

    async def verify_orders_on_startup(self) -> list:
        """
        Verify all pending orders still exist on exchange after bot restart.

        Returns:
            List of fill info dicts for orders that were filled while bot was down.
            Bot should create positions for these fills (same as check_fills return).
        """
        if self.mode == "paper" or not self.pending_orders:
            return []

        fills = []
        keys_to_remove = []
        for key, order in list(self.pending_orders.items()):
            try:
                status = self.exchange.fetch_order(order.order_id, order.symbol)
                order_status = status.get('status', '').lower()
                if order_status in ('closed', 'filled'):
                    # Order filled while bot was down — return as fill so bot creates position
                    actual_price = float(status.get('average', 0) or status.get('price', 0) or order.limit_price)
                    logger.info(
                        f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                        f"Order {order.order_id} FILLED while bot was down @ ${actual_price:.6f}"
                    )
                    fills.append({
                        'symbol': order.symbol,
                        'side': order.side,
                        'entry_price': actual_price,
                        'entry_type': f"ema610_{order.timeframe}",
                        'leverage': order.leverage,
                        'margin': order.margin,
                        'h4_trend': order.h4_trend,
                        'tp1_price': order.tp1_price,
                        'tp2_price': order.tp2_price,
                    })
                    keys_to_remove.append(key)
                elif order_status in ('canceled', 'cancelled', 'expired'):
                    logger.info(
                        f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                        f"Order {order.order_id} was cancelled/expired — removing"
                    )
                    keys_to_remove.append(key)
                else:
                    logger.info(
                        f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                        f"Order {order.order_id} still active (status={order_status})"
                    )
            except Exception as e:
                logger.warning(
                    f"[EMA610-LMT] Could not verify {order.symbol} order {order.order_id}: {e} — keeping"
                )

        for key in keys_to_remove:
            self.pending_orders.pop(key, None)

        if keys_to_remove:
            self._save()

        return fills

    def get_pending_orders_info(self) -> List[Dict[str, Any]]:
        """Get pending orders for dashboard display."""
        return [
            {
                'order_id': o.order_id,
                'symbol': o.symbol,
                'timeframe': o.timeframe,
                'side': o.side,
                'ema610_price': o.ema610_price,
                'limit_price': o.limit_price,
                'leverage': o.leverage,
                'margin': o.margin,
                'h4_trend': o.h4_trend,
                'tp1_price': o.tp1_price,
                'tp2_price': o.tp2_price,
                'created_at': o.created_at,
            }
            for o in self.pending_orders.values()
        ]

    # ── Private methods ─────────────────────────────────────────

    async def _place_order(self, key: str, info: Dict[str, Any]):
        """Place a new limit order at EMA610 price."""
        symbol = info['symbol']
        side = info['side']
        ema610_val = info['ema610_val']
        tf = info['timeframe']
        leverage = info['leverage']
        margin = info['margin']

        limit_price = ema610_val  # Place at exact EMA610

        try:
            if self.mode == "paper":
                order_id = f"paper_{symbol}_{tf}_{int(time.time())}"
                logger.info(
                    f"[EMA610-LMT] [PAPER] {symbol} {tf}: "
                    f"Placed {side} limit @ {limit_price:.6f} (EMA610={ema610_val:.6f})"
                )
            else:
                order = self.exchange.create_entry_limit_order(
                    symbol=symbol,
                    side=side.lower(),
                    amount_usdt=margin * leverage,
                    price=limit_price,
                    leverage=leverage
                )
                order_id = order.get('id', str(order.get('info', {}).get('ordId', '')))
                if not order_id:
                    raise ValueError(f"No order_id returned from OKX for {symbol} {tf}")
                logger.info(
                    f"[EMA610-LMT] {symbol} {tf}: "
                    f"Placed {side} limit @ {limit_price:.6f} → order_id={order_id}"
                )

            self.pending_orders[key] = PendingEMA610Order(
                order_id=order_id,
                symbol=symbol,
                timeframe=tf,
                side=side,
                ema610_price=ema610_val,
                limit_price=limit_price,
                leverage=leverage,
                margin=margin,
                h4_trend=info['h4_trend'],
                tp1_price=info['tp1_price'],
                tp2_price=info['tp2_price'],
            )
        except Exception as e:
            logger.error(f"[EMA610-LMT] Failed to place {side} limit for {symbol} {tf}: {e}")

    async def _cancel_order(self, key: str):
        """Cancel a pending order and remove from tracking."""
        order = self.pending_orders.pop(key, None)
        if not order:
            return

        if self.mode == "paper":
            logger.info(f"[EMA610-LMT] [PAPER] Cancelled {order.symbol} {order.timeframe}")
            return

        try:
            self.exchange.cancel_order(order.order_id, order.symbol)
            logger.info(
                f"[EMA610-LMT] Cancelled {order.symbol} {order.timeframe} "
                f"order {order.order_id}"
            )
        except Exception as e:
            # Order might have been filled during cancel attempt
            err_msg = str(e).lower()
            if 'not found' in err_msg or 'not exist' in err_msg:
                logger.info(
                    f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                    f"Order {order.order_id} already gone (may have filled)"
                )
            else:
                logger.error(
                    f"[EMA610-LMT] Error cancelling {order.symbol} order: {e}"
                )

    async def _cancel_stale_orders(self):
        """Cancel orders older than max age."""
        now = datetime.now()
        stale_keys = []
        for key, order in self.pending_orders.items():
            try:
                created = datetime.fromisoformat(order.created_at)
                age_seconds = (now - created).total_seconds()
                max_age = MAX_ORDER_AGE_H1 if order.timeframe == "h1" else MAX_ORDER_AGE_H4
                if age_seconds > max_age:
                    stale_keys.append(key)
                    logger.info(
                        f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                        f"Order stale ({age_seconds / 3600:.0f}h) — cancelling"
                    )
            except (ValueError, TypeError):
                pass

        for key in stale_keys:
            await self._cancel_order(key)

    def _check_paper_fill(self, order: PendingEMA610Order) -> bool:
        """Check if a paper order would have been filled based on recent candle data."""
        try:
            # Use 1m candle low/high for more accurate fill detection
            ohlcv = self.exchange.fetch_ohlcv(order.symbol, '1m', 3)
            if len(ohlcv) < 2:
                return False

            # Check last closed candle's range
            last_candle = ohlcv.iloc[-2]  # -1 is current (unclosed), -2 is last closed
            low = float(last_candle['low'])
            high = float(last_candle['high'])

            if order.side == "BUY":
                # BUY limit fills when price drops to or below limit price
                return low <= order.limit_price
            else:
                # SELL limit fills when price rises to or above limit price
                return high >= order.limit_price
        except Exception:
            return False

    def _check_live_fill(self, order: PendingEMA610Order) -> bool:
        """Check if a live order has been filled on exchange.

        Primary: fetch_order by order_id.
        Fallback: if order not found, check if there's an active OKX position
        for the same symbol+side (order may have been archived after fill).
        """
        try:
            status = self.exchange.fetch_order(order.order_id, order.symbol)
            order_status = status.get('status', '').lower()
            if order_status in ('closed', 'filled'):
                return True
            if order_status == 'not_found':
                # Order archived — fallback: check if OKX has an open position
                logger.info(
                    f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                    f"Order {order.order_id[:12]} not found on OKX — checking position fallback"
                )
                return self._check_position_fallback(order)
            # Order still open/pending
            return False
        except Exception as e:
            logger.warning(
                f"[EMA610-LMT] Could not check {order.symbol} order {order.order_id[:12]}: {e}"
            )
            return False

    def _check_position_fallback(self, order: PendingEMA610Order) -> bool:
        """Check if OKX has an open position matching this pending order.

        When fetch_order returns 'not_found', the limit order may have been
        filled and archived by OKX. If there's an active position for the same
        symbol and side, treat the order as filled.
        """
        try:
            positions = self.exchange.get_open_positions()
            expected_side = 'long' if order.side == "BUY" else 'short'
            for pos in positions:
                pos_symbol = (pos.get('symbol', '') or '').replace('/', '').replace(':USDT', '')
                pos_side = (pos.get('side', '') or '').lower()
                if pos_symbol == order.symbol and pos_side == expected_side:
                    logger.info(
                        f"[EMA610-LMT] {order.symbol} {order.timeframe}: "
                        f"Found matching OKX {expected_side} position — treating as filled"
                    )
                    return True
            return False
        except Exception as e:
            logger.warning(f"[EMA610-LMT] Position fallback check failed: {e}")
            return False

    # ── Manual Cancel Cooldown ────────────────────────────────────

    def set_manual_cooldown(self, symbol: str, timeframe: str, cooldown_seconds: int = 28800):
        """Set cooldown after manual cancel from web dashboard.

        Args:
            symbol: e.g. "BTCUSDT"
            timeframe: "h1" or "h4"
            cooldown_seconds: default 8h (28800s)
        """
        key = f"{symbol}_{timeframe}"
        expiry = time.time() + cooldown_seconds
        self._cancel_cooldowns[key] = expiry
        self._save_cooldowns()
        hours = cooldown_seconds / 3600
        logger.info(
            f"[EMA610-LMT] {symbol} {timeframe}: Manual cancel cooldown set — "
            f"skip {hours:.0f}h until {datetime.fromtimestamp(expiry).strftime('%H:%M')}"
        )

    def _cooldown_remaining(self, key: str) -> float:
        """Return remaining cooldown seconds for a key, 0 if expired/not set."""
        expiry = self._cancel_cooldowns.get(key, 0)
        remaining = expiry - time.time()
        if remaining <= 0:
            # Clean up expired entry
            self._cancel_cooldowns.pop(key, None)
            return 0
        return remaining

    def _save_cooldowns(self):
        """Save cooldown dict to JSON."""
        try:
            # Only save non-expired entries
            now = time.time()
            data = {k: v for k, v in self._cancel_cooldowns.items() if v > now}
            CANCEL_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CANCEL_COOLDOWN_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"[EMA610-LMT] Failed to save cooldowns: {e}")

    def _load_cooldowns(self):
        """Load cooldown dict from JSON, discard expired entries."""
        if not CANCEL_COOLDOWN_FILE.exists():
            return
        try:
            with open(CANCEL_COOLDOWN_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            now = time.time()
            self._cancel_cooldowns = {k: v for k, v in data.items() if v > now}
            if self._cancel_cooldowns:
                logger.info(
                    f"[EMA610-LMT] Loaded {len(self._cancel_cooldowns)} active cancel cooldowns"
                )
        except Exception as e:
            logger.error(f"[EMA610-LMT] Failed to load cooldowns: {e}")
            self._cancel_cooldowns = {}

    # ── Persistence ─────────────────────────────────────────────

    def _save(self):
        """Save pending orders to JSON."""
        try:
            data = {
                key: asdict(order)
                for key, order in self.pending_orders.items()
            }
            PENDING_ORDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PENDING_ORDERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"[EMA610-LMT] Failed to save pending orders: {e}")

    def _load(self):
        """Load pending orders from JSON."""
        if not PENDING_ORDERS_FILE.exists():
            return

        try:
            with open(PENDING_ORDERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            for key, order_data in data.items():
                self.pending_orders[key] = PendingEMA610Order(**order_data)

            if self.pending_orders:
                logger.info(
                    f"[EMA610-LMT] Loaded {len(self.pending_orders)} pending orders"
                )
        except Exception as e:
            logger.error(f"[EMA610-LMT] Failed to load pending orders: {e}")
            self.pending_orders = {}
