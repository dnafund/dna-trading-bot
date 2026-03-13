"""
Position Manager for Futures Trading

Manages open positions, tracks PNL, handles stop loss and take profit
"""

import time
import json
import os
import shutil
from collections import defaultdict
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime
import logging

from src.trading.core.models import Position
from src.trading.core.config import (LEVERAGE, RISK_MANAGEMENT, TAKE_PROFIT, TRAILING_SL,
                                     CHANDELIER_EXIT, SMART_SL, EMA610_EXIT, STANDARD_EXIT,
                                     RSI_DIV_EXIT, SD_ENTRY_CONFIG, FEES)

# File to persist positions across restarts
POSITIONS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'positions.json')
POSITIONS_BACKUP_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'data', 'positions_backup.json')

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Manage all open positions

    Rules:
    - Max 2 positions per symbol
    - Auto stop loss at -50% PNL
    - Take profit 70% at TP1, 30% at TP2
    """

    def __init__(self, binance_client, mode: str = "paper", db=None, trades_db=None):
        """
        Initialize position manager

        Args:
            binance_client: Exchange client instance (OKX or Binance)
            mode: "paper" or "live"
            db: Optional DatabaseManager instance for logging
            trades_db: Optional TradesDB instance for SQLite closed-trade storage
        """
        self.client = binance_client
        self.mode = mode
        self.db = db
        self.trades_db = trades_db
        self.positions: Dict[str, Position] = {}  # position_id -> Position
        self.symbol_positions: Dict[str, List[str]] = {}  # symbol -> [position_ids]

        # Paper trading balance
        self.paper_balance = 10000.0  # Starting balance

        # Exchange PNL cache (live mode): symbol -> {unrealized_pnl, mark_price, percentage, ...}
        self._exchange_pnl: Dict[str, Dict] = {}

        # Order sync: track last sync time per position to throttle API calls
        self._last_order_sync_ts: Dict[str, float] = {}
        self._ORDER_SYNC_INTERVAL = 120  # seconds between sync checks per position
        self._order_sync_failures: Dict[str, int] = {}  # position_id -> consecutive re-place failures
        self._MAX_SYNC_FAILURES = 3  # stop re-placing after this many consecutive failures

        # Load saved positions from file (also loads paper_balance)
        self._load_positions()

    # ==========================================
    # Persistence: Save/Load positions to JSON
    # ==========================================

    @staticmethod
    def _position_to_dict(pos) -> dict:
        """Convert a Position dataclass to a serializable dict."""
        return {
            "position_id": pos.position_id,
            "symbol": pos.symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "size": pos.size,
            "leverage": pos.leverage,
            "margin": pos.margin,
            "timestamp": pos.timestamp.isoformat() if hasattr(pos.timestamp, 'isoformat') else str(pos.timestamp),
            "entry_type": pos.entry_type,
            "stop_loss": pos.stop_loss,
            "trailing_sl": pos.trailing_sl,
            "chandelier_sl": pos.chandelier_sl,
            "take_profit_1": pos.take_profit_1,
            "take_profit_2": pos.take_profit_2,
            "exit_price": pos.exit_price,
            "current_price": pos.current_price,
            "pnl_usd": pos.pnl_usd,
            "pnl_percent": pos.pnl_percent,
            "roi_percent": pos.roi_percent,
            "tp1_closed": pos.tp1_closed,
            "tp2_closed": pos.tp2_closed,
            "tp1_cancelled": pos.tp1_cancelled,
            "tp2_cancelled": pos.tp2_cancelled,
            "remaining_size": pos.remaining_size,
            "realized_pnl": pos.realized_pnl,
            "entry_fee": pos.entry_fee,
            "total_exit_fees": pos.total_exit_fees,
            "status": pos.status,
            "close_reason": pos.close_reason,
            "ce_armed": pos.ce_armed,
            "ce_price_validated": pos.ce_price_validated,
            "entry_candle_ts": pos.entry_candle_ts,
            "entry_time": pos.entry_time,
            "linear_issue_id": pos.linear_issue_id,
            "last_m15_close": pos.last_m15_close,
            "close_time": pos.close_time,
            "tp1_order_id": pos.tp1_order_id,
            "tp2_order_id": pos.tp2_order_id,
            "hard_sl_order_id": pos.hard_sl_order_id,
            "okx_pnl_synced": getattr(pos, '_okx_pnl_synced', False),
            "sibling_reduce_pnl": getattr(pos, 'sibling_reduce_pnl', 0.0),
        }

    def _save_positions(self):
        """Save active positions to JSON file with automatic backup"""
        try:
            # Ensure data directory exists
            data_dir = os.path.dirname(POSITIONS_FILE)
            os.makedirs(data_dir, exist_ok=True)

            # Backup current file before overwriting
            if os.path.exists(POSITIONS_FILE):
                try:
                    shutil.copy2(POSITIONS_FILE, POSITIONS_BACKUP_FILE)
                except Exception as e:
                    logger.warning(f"[SAVE] Failed to create backup: {e}")

            # Convert positions to serializable dicts
            data = {}
            for pid, pos in self.positions.items():
                data[pid] = self._position_to_dict(pos)

            # Wrap with metadata
            save_data = {
                "_paper_balance": self.paper_balance,
                **data
            }

            with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)

            logger.debug(f"[SAVE] Saved {len(data)} positions to file (balance: ${self.paper_balance:,.2f})")

        except Exception as e:
            logger.error(f"[SAVE] Error saving positions: {e}")

    def _load_positions(self):
        """Load positions from JSON file on startup, with backup fallback"""
        load_file = POSITIONS_FILE
        if not os.path.exists(POSITIONS_FILE):
            if os.path.exists(POSITIONS_BACKUP_FILE):
                logger.warning("[LOAD] Main file missing, falling back to backup")
                load_file = POSITIONS_BACKUP_FILE
            else:
                logger.info("[LOAD] No saved positions file found, starting fresh")
                return

        try:
            with open(load_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Load paper balance
            saved_balance = data.pop("_paper_balance", None)

            count_open = 0
            count_closed = 0
            for pid, pos_data in data.items():
                # Skip non-dict entries (metadata)
                if not isinstance(pos_data, dict):
                    continue

                position = Position(
                    position_id=pos_data["position_id"],
                    symbol=pos_data["symbol"],
                    side=pos_data["side"],
                    entry_price=pos_data["entry_price"],
                    size=pos_data["size"],
                    leverage=pos_data["leverage"],
                    margin=pos_data["margin"],
                    timestamp=datetime.fromisoformat(pos_data["timestamp"]),
                    entry_type=pos_data.get("entry_type", "standard_m15"),
                    stop_loss=pos_data.get("stop_loss"),
                    trailing_sl=pos_data.get("trailing_sl"),
                    chandelier_sl=pos_data.get("chandelier_sl"),
                    take_profit_1=pos_data.get("take_profit_1"),
                    take_profit_2=pos_data.get("take_profit_2"),
                    current_price=pos_data.get("current_price", pos_data["entry_price"]),
                    pnl_usd=pos_data.get("pnl_usd", 0.0),
                    pnl_percent=pos_data.get("pnl_percent", 0.0),
                    roi_percent=pos_data.get("roi_percent", 0.0),
                    tp1_closed=pos_data.get("tp1_closed", False),
                    tp2_closed=pos_data.get("tp2_closed", False),
                    tp1_cancelled=pos_data.get("tp1_cancelled", False),
                    tp2_cancelled=pos_data.get("tp2_cancelled", False),
                    remaining_size=pos_data.get("remaining_size", pos_data["size"]),
                    realized_pnl=pos_data.get("realized_pnl", 0.0),
                    entry_fee=pos_data.get("entry_fee", 0.0),
                    total_exit_fees=pos_data.get("total_exit_fees", 0.0),
                    status=pos_data.get("status", "OPEN"),
                    close_reason=pos_data.get("close_reason"),
                    ce_armed=pos_data.get("ce_armed", True),  # Default True for old positions
                    ce_price_validated=pos_data.get("ce_price_validated", True),  # Default True for old positions
                    entry_candle_ts=pos_data.get("entry_candle_ts"),
                    entry_time=pos_data.get("entry_time"),
                    linear_issue_id=pos_data.get("linear_issue_id"),
                    last_m15_close=pos_data.get("last_m15_close"),
                    close_time=pos_data.get("close_time"),
                    tp1_order_id=pos_data.get("tp1_order_id"),
                    tp2_order_id=pos_data.get("tp2_order_id"),
                    hard_sl_order_id=pos_data.get("hard_sl_order_id"),
                    sibling_reduce_pnl=pos_data.get("sibling_reduce_pnl", 0.0),
                )

                # Only load active positions into memory (closed live in SQLite)
                if position.status in ["OPEN", "PARTIAL_CLOSE"]:
                    self.positions[pid] = position
                    symbol = position.symbol
                    if symbol not in self.symbol_positions:
                        self.symbol_positions[symbol] = []
                    self.symbol_positions[symbol].append(pid)
                    count_open += 1
                else:
                    count_closed += 1

            # Migration: "standard" → "standard_m15" for old positions
            for p in self.positions.values():
                if p.entry_type == "standard":
                    p.entry_type = "standard_m15"

            # Fix: if tp1_closed but remaining_size not reduced, fix it
            for p in self.positions.values():
                if p.tp1_closed and p.status == "PARTIAL_CLOSE" and p.remaining_size == p.size:
                    old_size = p.remaining_size
                    if p.entry_type.startswith("rsi_div_"):
                        tf = p.entry_type.replace("rsi_div_", "")
                        tp1_close_pct = RSI_DIV_EXIT.get(tf, {}).get('tp1_percent', 70) / 100
                    elif p.entry_type.startswith("sd_demand_") or p.entry_type.startswith("sd_supply_"):
                        tf = p.entry_type.split("_")[-1]
                        tp1_close_pct = SD_ENTRY_CONFIG.get(tf, {}).get('tp1_percent', 70) / 100
                    elif p.entry_type.startswith("ema610_"):
                        tf = p.entry_type.replace("ema610_", "")
                        tp1_close_pct = EMA610_EXIT.get(tf, {}).get('tp1_percent', 50) / 100
                    elif p.entry_type.startswith("standard_"):
                        tf = p.entry_type.replace("standard_", "")
                        tp1_close_pct = STANDARD_EXIT.get(tf, {}).get('tp1_percent', 70) / 100
                    else:
                        tp1_close_pct = TAKE_PROFIT.get('tp1_percent', 70) / 100
                    p.remaining_size = p.size * (1 - tp1_close_pct)
                    logger.info(f"[FIX] {p.symbol} remaining_size: {old_size:.4f} -> {p.remaining_size:.4f}")

            # Migration: backfill realized_pnl for positions that already had TP1 but no realized_pnl saved
            for p in self.positions.values():
                if p.tp1_closed and p.status == "PARTIAL_CLOSE" and p.realized_pnl == 0.0:
                    # TP1 closed 70% of original size
                    closed_ratio = 1.0 - (p.remaining_size / p.size)
                    # Estimate realized PNL: at time of TP1, price was at TP1 level
                    if p.take_profit_1 is not None:
                        entry = p.entry_price
                        tp1_price = p.take_profit_1
                        if p.side == "SELL":
                            price_change_pct = ((entry - tp1_price) / entry) * 100
                        else:
                            price_change_pct = ((tp1_price - entry) / entry) * 100
                        pnl_pct_at_tp1 = price_change_pct * p.leverage
                        p.realized_pnl = p.margin * closed_ratio * (pnl_pct_at_tp1 / 100)
                    else:
                        # ROI-based TP1 (no S/R level), estimate at +20% ROI
                        p.realized_pnl = p.margin * closed_ratio * 0.20
                    logger.info(f"[MIGRATION] {p.symbol}: backfilled realized_pnl=${p.realized_pnl:.2f} (closed {closed_ratio*100:.0f}%)")

            # Use saved balance if available, otherwise recalculate from positions
            if saved_balance is not None:
                self.paper_balance = saved_balance
                logger.info(f"[LOAD] Using saved paper balance: ${self.paper_balance:,.2f}")
            else:
                # Fallback: recalculate from positions (first-time migration)
                self.paper_balance = 10000.0
                for p in self.positions.values():
                    if p.status in ["OPEN", "PARTIAL_CLOSE"]:
                        active_ratio = p.remaining_size / p.size if p.size > 0 else 1.0
                        self.paper_balance -= p.margin * active_ratio
                        self.paper_balance += p.realized_pnl
                    elif p.status == "CLOSED":
                        self.paper_balance += p.pnl_usd
                logger.info(f"[LOAD] Calculated paper balance (no saved): ${self.paper_balance:,.2f}")

            logger.info(f"[LOAD] Paper balance: ${self.paper_balance:,.2f}")
            logger.info(f"[LOAD] Loaded {count_open} open + {count_closed} closed positions from file")

        except Exception as e:
            logger.error(f"[LOAD] Error loading positions from {load_file}: {e}")
            # Try backup if main file failed
            if load_file == POSITIONS_FILE and os.path.exists(POSITIONS_BACKUP_FILE):
                logger.warning("[LOAD] Main file corrupt, trying backup...")
                try:
                    load_file = POSITIONS_BACKUP_FILE
                    with open(POSITIONS_BACKUP_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # Re-run load logic with backup data
                    saved_balance = data.pop("_paper_balance", None)
                    for pid, pos_data in data.items():
                        if not isinstance(pos_data, dict):
                            continue
                        position = Position(
                            position_id=pos_data["position_id"],
                            symbol=pos_data["symbol"],
                            side=pos_data["side"],
                            entry_price=pos_data["entry_price"],
                            size=pos_data["size"],
                            leverage=pos_data["leverage"],
                            margin=pos_data["margin"],
                            timestamp=datetime.fromisoformat(pos_data["timestamp"]),
                            entry_type=pos_data.get("entry_type", "standard_m15"),
                            stop_loss=pos_data.get("stop_loss"),
                            trailing_sl=pos_data.get("trailing_sl"),
                            chandelier_sl=pos_data.get("chandelier_sl"),
                            take_profit_1=pos_data.get("take_profit_1"),
                            take_profit_2=pos_data.get("take_profit_2"),
                            current_price=pos_data.get("current_price", pos_data["entry_price"]),
                            pnl_usd=pos_data.get("pnl_usd", 0.0),
                            pnl_percent=pos_data.get("pnl_percent", 0.0),
                            roi_percent=pos_data.get("roi_percent", 0.0),
                            tp1_closed=pos_data.get("tp1_closed", False),
                            tp2_closed=pos_data.get("tp2_closed", False),
                            tp1_cancelled=pos_data.get("tp1_cancelled", False),
                            tp2_cancelled=pos_data.get("tp2_cancelled", False),
                            remaining_size=pos_data.get("remaining_size", pos_data["size"]),
                            realized_pnl=pos_data.get("realized_pnl", 0.0),
                            entry_fee=pos_data.get("entry_fee", 0.0),
                            total_exit_fees=pos_data.get("total_exit_fees", 0.0),
                            status=pos_data.get("status", "OPEN"),
                            close_reason=pos_data.get("close_reason"),
                            ce_armed=pos_data.get("ce_armed", True),
                            ce_price_validated=pos_data.get("ce_price_validated", True),
                            entry_candle_ts=pos_data.get("entry_candle_ts"),
                            entry_time=pos_data.get("entry_time"),
                            linear_issue_id=pos_data.get("linear_issue_id"),
                            last_m15_close=pos_data.get("last_m15_close"),
                            tp1_order_id=pos_data.get("tp1_order_id"),
                            tp2_order_id=pos_data.get("tp2_order_id"),
                            hard_sl_order_id=pos_data.get("hard_sl_order_id"),
                        )
                        self.positions[pid] = position
                        if position.status in ["OPEN", "PARTIAL_CLOSE"]:
                            symbol = position.symbol
                            if symbol not in self.symbol_positions:
                                self.symbol_positions[symbol] = []
                            self.symbol_positions[symbol].append(pid)
                    if saved_balance is not None:
                        self.paper_balance = saved_balance
                    logger.warning(f"[LOAD] Recovered {len(self.positions)} positions from backup")
                except Exception as e2:
                    logger.error(f"[LOAD] Backup also failed: {e2}")

    def can_open_position(self, symbol: str, entry_type: str = "standard_m15",
                          side: str = "") -> bool:
        """
        Check if can open new position for symbol (V7.2 pyramiding).

        Rules:
        - No opposite-side position on same symbol (one-way mode protection)
        - Max total active positions (max_total_positions)
        - Max equity usage % (max_equity_usage_pct)
        - Max EMA610 H1 positions (max_ema610_h1_positions, 0 = unlimited)
        - Per symbol: max 1 Standard + N EMA610_H1 + 1 EMA610_H4
        - EMA610_H1: new entry only when all existing H1 have hit TP1 (PARTIAL_CLOSE)
        - Others: OPEN + PARTIAL_CLOSE both count as occupied
        - Must have sufficient balance (>= min_balance_to_trade)

        Args:
            symbol: Trading pair
            entry_type: "standard_m15", "standard_h1", "standard_h4", "ema610_h1", or "ema610_h4"
            side: "BUY" or "SELL" — used to block opposite-side conflicts (one-way mode)

        Returns:
            True if can open position
        """
        all_active = [
            p for p in self.positions.values()
            if p.status in ["OPEN", "PARTIAL_CLOSE"]
        ]

        # Check 0a: M5↔M15 overlap — same symbol, same direction = redundant
        if entry_type == "standard_m15":
            m5_open = [
                p for p in all_active
                if p.symbol == symbol and p.entry_type == "standard_m5"
            ]
            if m5_open:
                logger.info(
                    f"{symbol}: BLOCKED standard_m15 — active M5 position exists "
                    f"(M5 candle is within the current M15 candle)"
                )
                return False

        if entry_type == "standard_m5":
            m15_open = [
                p for p in all_active
                if p.symbol == symbol and p.entry_type == "standard_m15"
            ]
            if m15_open:
                logger.info(
                    f"{symbol}: BLOCKED standard_m5 — active M15 position exists"
                )
                return False

        # Check 0b: Opposite-side conflict (one-way mode protection)
        # In OKX net_mode, opening LONG while SHORT exists will net out and close the SHORT.
        # Block this to prevent accidental position destruction.
        if side:
            opposite = "SELL" if side == "BUY" else "BUY"
            opposite_positions = [
                p for p in all_active
                if p.symbol == symbol and p.side == opposite
            ]
            if opposite_positions:
                sides_str = ", ".join(f"{p.side} {p.entry_type}" for p in opposite_positions)
                logger.warning(
                    f"{symbol}: BLOCKED {side} {entry_type} — opposite position exists "
                    f"({sides_str}). One-way mode would net out."
                )
                return False

        # Check 1: Total active positions cap
        max_total = RISK_MANAGEMENT.get('max_total_positions', 20)
        if len(all_active) >= max_total:
            logger.warning(f"{symbol}: Total active positions ({len(all_active)}) >= max ({max_total})")
            return False

        # Check 2: Max equity usage — total margin of active positions vs balance
        max_equity_pct = RISK_MANAGEMENT.get('max_equity_usage_pct', 50)
        if max_equity_pct < 100:
            total_margin_used = sum(p.margin for p in all_active)
            new_margin = RISK_MANAGEMENT.get('fixed_margin', 50)
            if entry_type.startswith("ema610_"):
                multiplier_key = 'ema610_h4_margin_multiplier' if entry_type == "ema610_h4" else 'ema610_margin_multiplier'
                new_margin = new_margin * RISK_MANAGEMENT.get(multiplier_key, 1)

            balance = self.paper_balance if self.mode == "paper" else self._get_live_balance()
            if balance > 0:
                max_margin = balance * (max_equity_pct / 100)
                if total_margin_used + new_margin > max_margin:
                    logger.warning(
                        f"{symbol}: Equity usage limit — used ${total_margin_used:.2f} + "
                        f"new ${new_margin:.2f} > max ${max_margin:.2f} "
                        f"({max_equity_pct}% of ${balance:.2f})"
                    )
                    return False

        # Check 3: Max EMA610 H1 positions (across all symbols)
        if entry_type == "ema610_h1":
            max_h1 = RISK_MANAGEMENT.get('max_ema610_h1_positions', 0)
            if max_h1 > 0:
                active_h1_total = sum(
                    1 for p in all_active if p.entry_type == "ema610_h1"
                )
                if active_h1_total >= max_h1:
                    logger.warning(
                        f"{symbol}: EMA610 H1 limit — {active_h1_total} active >= max {max_h1}"
                    )
                    return False

        # Check 4: Per-symbol per-entry_type limit
        # EMA610_H1: new entry only when ALL existing are PARTIAL_CLOSE (TP1 hit)
        # Others: max 1 (OPEN or PARTIAL_CLOSE)
        if symbol in self.symbol_positions:
            if entry_type == "ema610_h1":
                open_h1 = [
                    pid for pid in self.symbol_positions[symbol]
                    if self.positions[pid].status == "OPEN"
                    and self.positions[pid].entry_type == entry_type
                ]
                if len(open_h1) > 0:
                    logger.debug(f"{symbol}: Has {len(open_h1)} OPEN {entry_type} (waiting for TP1 before new entry)")
                    return False
            else:
                active_of_type = [
                    pid for pid in self.symbol_positions[symbol]
                    if self.positions[pid].status in ["OPEN", "PARTIAL_CLOSE"]
                    and self.positions[pid].entry_type == entry_type
                ]
                if len(active_of_type) >= 1:
                    logger.debug(f"{symbol}: Already has active {entry_type} position")
                    return False

        # Check 5: Sufficient balance
        if self.mode == "paper":
            min_balance = RISK_MANAGEMENT.get('min_balance_to_trade', 50)
            if self.paper_balance < min_balance:
                logger.warning(f"{symbol}: Insufficient balance ${self.paper_balance:,.2f} (min ${min_balance})")
                return False

        return True

    def _get_live_balance(self) -> float:
        """Get live balance from exchange client (cached, for equity checks)."""
        try:
            if hasattr(self, 'client') and self.client:
                balance_info = self.client.get_account_balance()
                return float(balance_info.get('total', 0))
        except Exception as e:
            logger.warning(f"Could not fetch live balance for equity check: {e}")
        return 0

    def calculate_position_size(
        self,
        symbol: str,
        account_balance: float,
        entry_price: float
    ) -> tuple[float, float, int]:
        """
        Calculate position size based on risk management

        Rules:
        - Use 5% of account balance per trade
        - Leverage: BTC 20x, ETH/SOL 10x, Altcoins 5x

        Args:
            symbol: Trading pair
            account_balance: Account balance in USDT
            entry_price: Entry price

        Returns:
            Tuple of (position_size, margin_used, leverage)
        """
        # Get leverage for symbol
        leverage = LEVERAGE.get(symbol, LEVERAGE['default'])

        # Fixed margin per trade
        margin = RISK_MANAGEMENT.get('fixed_margin', 500)

        # Calculate position value with leverage
        position_value = margin * leverage

        # Convert to base currency amount
        position_size = position_value / entry_price

        logger.info(
            f"{symbol}: Size={position_size:.6f}, Margin=${margin:.2f}, "
            f"Leverage={leverage}x, Value=${position_value:.2f}"
        )

        return position_size, margin, leverage

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        account_balance: float,
        tp1: Optional[float] = None,
        tp2: Optional[float] = None,
        entry_type: str = "standard_m15",
        skip_exchange_order: bool = False
    ) -> Optional[Position]:
        """
        Open a new position

        Args:
            symbol: Trading pair
            side: "BUY" or "SELL"
            entry_price: Entry price
            account_balance: Account balance in USDT
            tp1: Take profit 1 (S/R level)
            tp2: Take profit 2 (Fibo 1.618)
            skip_exchange_order: If True, skip market order (already filled via limit)

        Returns:
            Position object or None if failed
        """
        try:
            # Check if can open
            if not self.can_open_position(symbol, entry_type=entry_type, side=side):
                return None

            # Calculate position size
            size, margin, leverage = self.calculate_position_size(
                symbol, account_balance, entry_price
            )

            # RSI Divergence: apply leverage multiplier (ceil(default * 1.5))
            if entry_type.startswith("rsi_div_"):
                import math
                tf = entry_type.replace("rsi_div_", "")
                lev_mult = RSI_DIV_EXIT.get(tf, {}).get('leverage_multiplier', 1.5)
                leverage = min(math.ceil(leverage * lev_mult), 125)
                position_value = margin * leverage
                size = position_value / entry_price

            # EMA610: apply margin multiplier (H1: x2 = $4,000, H4: x1 = $2,000)
            if entry_type.startswith("ema610_"):
                if entry_type == "ema610_h4":
                    multiplier = RISK_MANAGEMENT.get('ema610_h4_margin_multiplier', 1)
                else:
                    multiplier = RISK_MANAGEMENT.get('ema610_margin_multiplier', 1)
                if multiplier > 1:
                    margin = margin * multiplier
                    position_value = margin * leverage
                    size = position_value / entry_price

            # Guard: Ensure balance can cover the margin
            if self.mode == "paper" and self.paper_balance < margin:
                logger.warning(
                    f"{symbol}: Cannot open - margin ${margin:,.2f} > available balance ${self.paper_balance:,.2f}"
                )
                return None

            # Only execute on exchange if live mode
            if self.mode == "live":
                # Set leverage on exchange
                self.client.set_leverage(symbol, leverage)

                if skip_exchange_order:
                    # EMA610 limit order already filled on exchange — skip market order
                    logger.info(f"{symbol}: Skipping market order (already filled via limit @ ${entry_price:.4f})")
                else:
                    # Create market order
                    order_side = 'buy' if side == "BUY" else 'sell'
                    order = self.client.create_market_order(
                        symbol=symbol,
                        side=order_side,
                        amount=size
                    )
            else:
                # Paper trading - simulate order
                logger.info(f"📝 PAPER TRADE: {side} {size:.6f} {symbol} @ ${entry_price:.2f}")

            # Calculate entry fee (maker rate for limit orders)
            position_value = margin * leverage
            entry_fee = position_value * FEES['maker']

            # Create position object
            position_id = f"{symbol}_{int(time.time() * 1000)}"

            position = Position(
                position_id=position_id,
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                size=size,
                leverage=leverage,
                margin=margin,
                current_price=entry_price,
                entry_type=entry_type,
                take_profit_1=tp1,
                take_profit_2=tp2,
                entry_fee=entry_fee
            )

            # Calculate stop loss (price at -50% PNL)
            position.stop_loss = self._calculate_stop_loss(position)

            # Store position
            self.positions[position_id] = position

            if symbol not in self.symbol_positions:
                self.symbol_positions[symbol] = []
            self.symbol_positions[symbol].append(position_id)

            # Deduct margin from paper balance
            if self.mode == "paper":
                self.paper_balance -= margin
                logger.info(f"{symbol}: Paper balance: -${margin:,.2f} margin -> ${self.paper_balance:,.2f}")

            logger.info(
                f"Position opened: {side} {size:.6f} {symbol} @ ${entry_price:.2f} "
                f"(Leverage: {leverage}x, Margin: ${margin:.2f})"
            )

            # Place TP/SL orders on exchange (live mode only)
            if self.mode == "live":
                self._place_initial_tp_sl(position)

            # Save to file
            self._save_positions()

            # Log to database
            if self.db:
                try:
                    self.db.log_operation(
                        operation_name='open_position',
                        risk_score=0,
                        status='success',
                        meta_data={
                            'position_id': position_id,
                            'symbol': symbol,
                            'side': side,
                            'entry_price': entry_price,
                            'size': size,
                            'leverage': leverage,
                            'margin': margin,
                            'tp1': tp1,
                            'tp2': tp2,
                            'stop_loss': position.stop_loss,
                        }
                    )
                except Exception as e:
                    logger.error(f"[DB] Error logging open_position: {e}")

            return position

        except Exception as e:
            logger.error(f"Error opening position for {symbol}: {e}")
            return None

    def _calculate_stop_loss(self, position: Position) -> float:
        """
        Calculate hard stop loss price (V7.2).

        Standard: -20% ROI (from RISK_MANAGEMENT)
        EMA610 H1: -30% ROI (from EMA610_EXIT)
        EMA610 H4: -50% ROI (from EMA610_EXIT)
        """
        entry = position.entry_price
        leverage = position.leverage

        # Get SL percent based on entry type
        if position.entry_type.startswith("ema610_"):
            tf = position.entry_type.replace("ema610_", "")  # "h1" or "h4"
            sl_roi = EMA610_EXIT.get(tf, {}).get('hard_sl_roi', 30)
        elif position.entry_type.startswith("rsi_div_"):
            tf = position.entry_type.replace("rsi_div_", "")  # "m15", "h1", or "h4"
            sl_roi = RSI_DIV_EXIT.get(tf, {}).get('hard_sl_roi', 15)
        elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
            tf = position.entry_type.split("_")[-1]  # "m15", "h1", or "h4"
            sl_roi = SD_ENTRY_CONFIG.get(tf, {}).get('hard_sl_roi', 20)
        elif position.entry_type.startswith("standard_"):
            tf = position.entry_type.replace("standard_", "")  # "m15", "h1", or "h4"
            sl_roi = STANDARD_EXIT.get(tf, {}).get('hard_sl_roi', 20)
        else:
            sl_roi = RISK_MANAGEMENT.get('hard_sl_percent', 20)

        sl_percent = sl_roi / 100
        price_change_percent = sl_percent / leverage

        if position.side == "BUY":
            sl = entry * (1 - price_change_percent)
        else:  # SELL
            sl = entry * (1 + price_change_percent)

        logger.info(f"{position.symbol}: Hard SL at ${sl:.2f} (-{sl_roi}% ROI, {position.entry_type})")
        return sl

    def update_chandelier_sl(self, position: Position,
                             chandelier_long: float = None,
                             chandelier_short: float = None,
                             vol_current: float = None,
                             vol_avg: float = None,
                             ema200: float = None,
                             close_price: float = None,
                             fallback_chandelier_longs: list = None,
                             fallback_chandelier_shorts: list = None):
        """
        Update trailing stop loss using Chandelier Exit (V7.2).

        Standard positions: Chandelier + Smart SL breathing
        EMA610 positions: Chandelier only (no breathing), with fallback chain
          - EMA610 H1: H1 → M15 (if H1 chandelier stuck wrong side of entry)
          - EMA610 H4: H4 → H1 → M15 (chain fallback)

        Chandelier only moves in favorable direction (ratchet).
        BUY uses chandelier_long (HH - ATR), SELL uses chandelier_short (LL + ATR).
        Matches TradingView Everget CE behavior.

        Args:
            position: Position object
            chandelier_long: Chandelier long exit = HH(n) - mult*ATR (SL for BUY)
            chandelier_short: Chandelier short exit = LL(n) + mult*ATR (SL for SELL)
            vol_current: Current candle volume
            vol_avg: Average volume (21-period)
            ema200: EMA200 value for Smart SL safety
            close_price: Current close price for EMA200 check
            fallback_chandelier_longs: List of fallback chandelier_long values
                from lower timeframes, ordered by priority.
                EMA610 H1: [m15_ch_long]
                EMA610 H4: [h1_ch_long, m15_ch_long]
            fallback_chandelier_shorts: List of fallback chandelier_short values
                from lower timeframes (same order as fallback_chandelier_longs).
        """
        if not CHANDELIER_EXIT.get('enabled', True):
            return

        # Update chandelier_sl — set to latest indicator value each closed candle
        # No ratchet: CE updates every candle to match TradingView realtime display
        # BUY uses chandelier_long (HH - ATR), SELL uses chandelier_short (LL + ATR)
        if position.side == "BUY" and chandelier_long is not None:
            position.chandelier_sl = chandelier_long
        elif position.side == "SELL" and chandelier_short is not None:
            position.chandelier_sl = chandelier_short

        # For EMA610/RSI-div/SD entries: chandelier directly as trailing SL (no breathing)
        # With fallback: if primary chandelier stuck on wrong side of entry,
        # try lower timeframes until one is on the correct side
        # Fallback chain: H1 → [M15], H4 → [H1, M15]
        _is_non_standard = (
            position.entry_type.startswith("ema610_")
            or position.entry_type.startswith("rsi_div_")
            or position.entry_type.startswith("sd_demand_")
            or position.entry_type.startswith("sd_supply_")
        )
        if _is_non_standard:
            ch_sl = position.chandelier_sl
            entry = position.entry_price

            # Check if primary chandelier is on the "wrong side" of entry
            # (i.e., not yet providing profit protection)
            # SELL: wrong if chandelier >= entry (SL above entry = no profit locked)
            # BUY: wrong if chandelier <= entry (SL below entry = no profit locked)
            # Also treat None as wrong side (no primary data yet)
            wrong_side = True
            if ch_sl is not None:
                if position.side == "SELL" and ch_sl < entry:
                    wrong_side = False
                elif position.side == "BUY" and ch_sl > entry:
                    wrong_side = False

            if wrong_side:
                # Pick correct fallback list based on side
                fallback_list = (
                    fallback_chandelier_shorts if position.side == "SELL" and fallback_chandelier_shorts
                    else fallback_chandelier_longs if position.side == "BUY" and fallback_chandelier_longs
                    else None
                )
                if fallback_list:
                    # Try each fallback (lower timeframe) in order
                    for fb_ch_val in fallback_list:
                        if fb_ch_val is None:
                            continue
                        # Check if this fallback is on the correct side
                        correct_side = (
                            (position.side == "SELL" and fb_ch_val > entry) or
                            (position.side == "BUY" and fb_ch_val < entry)
                        )
                        if correct_side:
                            # Set fallback CE directly (no ratchet — match TV per candle)
                            position.trailing_sl = fb_ch_val
                            logger.info(
                                f"[CE-FALLBACK] {position.symbol}: Using fallback chandelier "
                                f"${fb_ch_val:.4f} (primary ${ch_sl if ch_sl else 'None'} "
                                f"wrong side of entry ${entry:.4f})"
                            )
                            return
                # All fallbacks also wrong side — use primary anyway
                ch_label = f"${ch_sl:.4f}" if ch_sl is not None else "None"
                logger.debug(
                    f"[CE-FALLBACK] {position.symbol}: All fallbacks also wrong side, using primary {ch_label}"
                )

            # Primary chandelier is on correct side (or no fallback available)
            # But also check vs current price — don't set if CE is wrong side of price
            # BUY: CE must be below price. SELL: CE must be above price.
            if ch_sl is not None:
                if close_price is not None:
                    if position.side == "BUY" and ch_sl > close_price:
                        logger.debug(
                            f"[CE] {position.symbol}: EMA610 CE wrong side — BUY trail ${ch_sl:.4f} > "
                            f"price ${close_price:.4f}, skipping trailing_sl update"
                        )
                        return
                    elif position.side == "SELL" and ch_sl < close_price:
                        logger.debug(
                            f"[CE] {position.symbol}: EMA610 CE wrong side — SELL trail ${ch_sl:.4f} < "
                            f"price ${close_price:.4f}, skipping trailing_sl update"
                        )
                        return
                # Set trailing_sl directly from CE (no ratchet — match TV per candle)
                position.trailing_sl = ch_sl
            return

        # For Standard: Smart SL breathing logic
        ch_sl = position.chandelier_sl
        if ch_sl is None:
            return

        # Wrong-side check: don't set trailing_sl if CE is on wrong side of current price
        # BUY: CE should be BELOW price (protect from below). If CE > price → wrong side
        # SELL: CE should be ABOVE price (protect from above). If CE < price → wrong side
        # This prevents instant SL triggers when CE hasn't caught up after entry
        if close_price is not None:
            if position.side == "BUY" and ch_sl > close_price:
                logger.debug(
                    f"[CE] {position.symbol}: CE wrong side — BUY trail ${ch_sl:.4f} > "
                    f"price ${close_price:.4f}, skipping trailing_sl update"
                )
                return
            elif position.side == "SELL" and ch_sl < close_price:
                logger.debug(
                    f"[CE] {position.symbol}: CE wrong side — SELL trail ${ch_sl:.4f} < "
                    f"price ${close_price:.4f}, skipping trailing_sl update"
                )
                return

        # Check if chandelier would trigger
        ch_triggered = False
        if position.side == "BUY" and close_price and close_price <= ch_sl:
            ch_triggered = True
        elif position.side == "SELL" and close_price and close_price >= ch_sl:
            ch_triggered = True

        if ch_triggered and SMART_SL.get('enabled', True):
            # Smart SL: if volume low, allow breathing room
            if vol_current is not None and vol_avg is not None and vol_avg > 0:
                vol_threshold = SMART_SL.get('volume_threshold_pct', 80) / 100
                if vol_current <= vol_avg * vol_threshold:
                    # Low volume → breathing room, DON'T update trailing_sl
                    # But check EMA200 safety
                    if ema200 is not None and SMART_SL.get('hard_sl_on_ema_break', True):
                        if position.side == "BUY" and close_price < ema200:
                            position.trailing_sl = ch_sl  # EMA200 broken → SL
                            return
                        elif position.side == "SELL" and close_price > ema200:
                            position.trailing_sl = ch_sl  # EMA200 broken → SL
                            return
                    # Volume low + EMA200 safe → breathing, don't tighten SL
                    return

        # Normal: set trailing SL to chandelier level
        position.trailing_sl = ch_sl

    def _sync_sl_order_to_exchange(self, position) -> None:
        """
        Sync trailing_sl to exchange: if CE trailing_sl is tighter than current
        Hard SL order, cancel old SL and place new trigger order at trailing_sl.

        Called after update_chandelier_sl() updates trailing_sl.
        Only acts in live mode when trailing_sl is closer to entry than stop_loss.
        """
        if self.mode != "live":
            return
        if not position.trailing_sl or not position.ce_armed:
            return
        if not position.ce_price_validated:
            return

        # Determine if CE is tighter (closer to entry) than hard SL
        # BUY: higher SL = tighter. SELL: lower SL = tighter.
        hard_sl = position.stop_loss or 0
        ce_sl = position.trailing_sl

        if position.side == "BUY":
            ce_is_tighter = ce_sl > hard_sl
        else:
            ce_is_tighter = ce_sl < hard_sl

        if not ce_is_tighter:
            return

        # Check if SL order price already matches trailing_sl (avoid unnecessary re-place)
        current_order_price = getattr(position, '_last_synced_sl_price', None)
        if current_order_price and abs(current_order_price - ce_sl) / ce_sl < 0.001:
            return  # Already synced within 0.1%

        # Cancel existing SL order
        if position.hard_sl_order_id and position.hard_sl_order_id != "FAILED":
            try:
                self.client.cancel_order(position.hard_sl_order_id, position.symbol)
                logger.info(f"[SL-SYNC] {position.symbol}: Cancelled old SL {position.hard_sl_order_id[:12]}")
            except Exception as e:
                logger.warning(f"[SL-SYNC] {position.symbol}: Cancel old SL failed: {e}")

        # Place new trigger order at CE trailing_sl
        close_side = 'sell' if position.side == "BUY" else 'buy'
        try:
            order = self.client.create_stop_market_order(
                symbol=position.symbol,
                side=close_side,
                amount=position.remaining_size,
                stop_price=ce_sl,
                reduce_only=True,
            )
            position.hard_sl_order_id = order.get('id')
            position._last_synced_sl_price = ce_sl
            self._save_positions()
            logger.info(
                f"[SL-SYNC] {position.symbol}: SL moved to CE ${ce_sl:.4f} "
                f"(was Hard SL ${hard_sl:.4f})"
            )
        except Exception as e:
            logger.error(f"[SL-SYNC] {position.symbol}: Place CE SL failed: {e}")
            position.hard_sl_order_id = "FAILED"

    def update_position_price(self, symbol: str, current_price: float):
        """
        Update current price and PNL for all positions of a symbol

        Args:
            symbol: Trading pair
            current_price: Current market price
        """
        if symbol not in self.symbol_positions:
            return

        for position_id in self.symbol_positions[symbol]:
            position = self.positions[position_id]

            if position.status == "CLOSED":
                continue

            # Update price
            position.current_price = current_price

            # Calculate PNL
            self._calculate_pnl(position)

            # Check stop loss and take profit
            self._check_exit_conditions(position)

        # Save updated prices/PNL to file
        self._save_positions()

    def update_single_position_price(self, position: Position, current_price: float):
        """
        Update current price, PNL, and check exits for a single position.
        Avoids duplicate exit checks when multiple positions share the same symbol.
        """
        if position.status == "CLOSED":
            return

        position.current_price = current_price
        self._calculate_pnl(position)
        self._check_exit_conditions(position)
        self._save_positions()

    def sync_exchange_pnl(self) -> None:
        """Fetch unrealized PNL from exchange and sync margin/entry to positions (live mode only)."""
        if self.mode != "live":
            return
        try:
            if not hasattr(self.client, 'get_positions_pnl'):
                return
            self._exchange_pnl = self.client.get_positions_pnl()

            # Sync entry price from exchange to local positions
            # NOTE: Do NOT sync margin from exchange — exchange reports TOTAL margin
            # for the merged position (all local sub-positions combined).
            # Overwriting each local position's margin with the total would be wrong.
            # Local margin (from config fixed_margin) is the source of truth.
            changed = False

            # Group active local positions by symbol
            active_by_symbol: dict[str, list] = defaultdict(list)
            for position in self.positions.values():
                if position.status in ("OPEN", "PARTIAL_CLOSE"):
                    active_by_symbol[position.symbol].append(position)

            for symbol, local_positions in active_by_symbol.items():
                ex = self._exchange_pnl.get(symbol)
                if not ex:
                    continue

                # Sync entry price from exchange (only when single local position)
                # When multiple local positions exist, each has its own entry price
                if len(local_positions) == 1:
                    position = local_positions[0]
                    ex_entry = ex.get('entry_price', 0)
                    if ex_entry > 0 and abs(ex_entry - position.entry_price) / position.entry_price > 0.0001:
                        logger.info(
                            f"[PNL-SYNC] {position.symbol}: entry ${position.entry_price:.4f} → ${ex_entry:.4f} (from exchange)"
                        )
                        position.entry_price = ex_entry
                        changed = True

            if changed:
                self._save_positions()

        except Exception as e:
            logger.warning(f"[PNL-SYNC] Failed to fetch exchange PNL: {e}")

    def _calculate_pnl(self, position: Position):
        """
        Calculate PNL for a position.

        Live mode: uses unrealizedPnl directly from OKX (most accurate).
        Paper mode: self-calculates from entry/current price + fees.

        Args:
            position: Position object (modified in place)
        """
        entry = position.entry_price
        current = position.current_price
        leverage = position.leverage

        # Price change percentage (used for exit condition checks regardless of mode)
        if position.side == "BUY":
            price_change_pct = ((current - entry) / entry) * 100
        else:  # SELL
            price_change_pct = ((entry - current) / entry) * 100

        pnl_percent = price_change_pct * leverage
        position.pnl_percent = pnl_percent

        # ── Live mode: use exchange PNL directly ──
        exchange_data = self._exchange_pnl.get(position.symbol)
        if self.mode == "live" and exchange_data:
            exchange_upnl = exchange_data['unrealized_pnl']

            # Split exchange PNL proportionally by price diff × size
            # (exchange merges all sub-positions into one)
            active_same_symbol = [
                p for p in self.positions.values()
                if p.symbol == position.symbol
                and p.status in ("OPEN", "PARTIAL_CLOSE")
            ]

            if len(active_same_symbol) > 1:
                # Multiple positions → split by estimated PNL contribution
                mark = exchange_data.get('mark_price', current)
                raw_pnls = {}
                for p in active_same_symbol:
                    p_entry = p.entry_price or 0
                    p_margin = p.margin or 0
                    p_lev = p.leverage or 1
                    p_remaining_ratio = (p.remaining_size / p.size) if p.size > 0 else 0
                    if p_entry > 0 and p_margin > 0:
                        price_pct = (mark - p_entry) / p_entry
                        if p.side == "SELL":
                            price_pct = -price_pct
                        raw_pnls[id(p)] = price_pct * p_margin * p_lev * p_remaining_ratio
                    else:
                        raw_pnls[id(p)] = 0
                raw_total = sum(raw_pnls.values())
                if raw_total != 0:
                    position_share = raw_pnls[id(position)] / raw_total
                else:
                    position_share = 1.0 / len(active_same_symbol)
            else:
                position_share = 1.0

            shared_upnl = exchange_upnl * position_share

            # Total PNL = realized (from partial closes) + proportional unrealized
            pnl_usd = position.realized_pnl + shared_upnl

            # ROI: calculate from local margin (source of truth)
            if position.margin > 0:
                roi_percent = (pnl_usd / position.margin) * 100
            else:
                roi_percent = 0

            position.pnl_usd = pnl_usd
            position.roi_percent = roi_percent
            return

        # ── Paper mode: self-calculate with fee estimation ──
        size_ratio = position.remaining_size / position.size

        # Unrealized PNL before fees (remaining position only)
        unrealized_pnl_before_fees = position.margin * size_ratio * (pnl_percent / 100)

        # Estimate exit fee for remaining position
        remaining_position_value = position.margin * size_ratio * leverage
        estimated_exit_fee = remaining_position_value * FEES['maker']

        # Total fees = entry fee + already paid exit fees + estimated exit fee
        total_fees = position.entry_fee + position.total_exit_fees + estimated_exit_fee

        # Total PNL after fees = realized + unrealized - total fees
        pnl_usd = position.realized_pnl + unrealized_pnl_before_fees - total_fees

        # ROI percentage (total PNL / total margin)
        roi_percent = (pnl_usd / position.margin) * 100 if position.margin else 0

        position.pnl_usd = pnl_usd
        position.roi_percent = roi_percent

    def _check_exit_conditions(self, position: Position):
        """
        Check if position should be closed (V7.2).

        Priority order:
        1. Hard SL (ROI-based safety net)
        2. Chandelier Exit trailing SL (with Smart SL breathing for Standard)
        3. TP1: Price-based (ATR for Standard, ROI for EMA610) -> close 70%
        4. TP2: Price-based -> close remaining 30%

        Args:
            position: Position object
        """
        current = position.current_price

        # ── Sync TP/SL orders: verify they still exist on exchange ──
        if self.mode == "live":
            self._sync_tp_sl_orders(position)

        # ── Retry hard SL if missing (failed on initial placement) ──
        if (self.mode == "live" and position.stop_loss
                and position.hard_sl_order_id in (None, "FAILED")):
            try:
                close_side = 'sell' if position.side == "BUY" else 'buy'
                order = self.client.create_stop_market_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=position.remaining_size,
                    stop_price=position.stop_loss,
                    reduce_only=True,
                )
                position.hard_sl_order_id = order.get('id')
                self._save_positions()
                logger.info(
                    f"[SL-ORDER] {position.symbol}: Trigger SL re-set "
                    f"@ ${position.stop_loss:.4f} (id={order.get('id', '?')[:12]})"
                )
            except Exception as e:
                logger.debug(f"[SL-ORDER] {position.symbol}: Position SL retry failed: {e}")

        # ── 1. Hard SL check ─────────────────────────────────────────
        hard_sl = position.stop_loss
        if hard_sl:
            sl_hit = False
            if position.side == "BUY" and current <= hard_sl:
                sl_hit = True
            elif position.side == "SELL" and current >= hard_sl:
                sl_hit = True

            if sl_hit:
                logger.warning(
                    f"{position.symbol}: HARD_SL hit at ${current:.2f} "
                    f"(SL: ${hard_sl:.2f}, PNL: {position.pnl_percent:.1f}%)"
                )
                if self.db:
                    try:
                        self.db.log_operation(
                            operation_name='stop_loss_triggered',
                            risk_score=0, status='success',
                            meta_data={
                                'position_id': position.position_id,
                                'symbol': position.symbol,
                                'side': position.side,
                                'entry_price': position.entry_price,
                                'stop_loss': hard_sl,
                                'trigger_price': current,
                                'pnl_usd': position.pnl_usd,
                                'pnl_percent': position.pnl_percent,
                                'reason': 'HARD_SL',
                                'entry_type': position.entry_type,
                            }
                        )
                    except Exception as e:
                        logger.error(f"[DB] Error logging stop_loss: {e}")

                # Race condition guard: check if trigger SL already filled on OKX
                # before sending a duplicate close (which could open ghost position)
                skip_exchange = False
                if (self.mode == "live"
                        and position.hard_sl_order_id
                        and position.hard_sl_order_id != "FAILED"):
                    try:
                        order_info = self.client.fetch_order(
                            position.hard_sl_order_id, position.symbol
                        )
                        if order_info.get('status') in ('closed', 'filled'):
                            logger.info(
                                f"[SL-RACE] {position.symbol}: Trigger SL "
                                f"{position.hard_sl_order_id[:12]} already filled "
                                f"on OKX — skipping duplicate close"
                            )
                            skip_exchange = True
                    except Exception as e:
                        logger.debug(
                            f"[SL-RACE] {position.symbol}: Could not check "
                            f"trigger SL status: {e} — proceeding with close"
                        )

                self.close_position(
                    position.position_id,
                    reason="HARD_SL",
                    skip_exchange_close=skip_exchange,
                )
                return

        # ── 2. Chandelier Exit trailing SL ────────────────────────────
        # Trigger uses candle CLOSE price (not tick), matching backtest behavior.
        # This avoids false triggers from intra-candle wicks.
        # Grace period: skip CE SL until first new candle closes after entry
        effective_trail = position.trailing_sl
        if effective_trail and position.ce_armed:
            # Use candle close price for CE trigger (not realtime tick)
            # If no candle close available yet, skip CE check this cycle
            ce_price = getattr(position, 'last_m15_close', None)
            if ce_price is None:
                logger.debug(
                    f"[CE] {position.symbol}: No candle close price yet, skipping CE check"
                )
                # Skip entire CE block — no candle data to trigger on
                effective_trail = None

            # Validate CE: price must first be on the "safe side" of CE before
            # we allow triggers. This prevents instant close when CE is first
            # armed but price already breached during grace period.
            if effective_trail and ce_price and not position.ce_price_validated:
                price_safe = False
                if position.side == "BUY" and ce_price > effective_trail:
                    price_safe = True
                elif position.side == "SELL" and ce_price < effective_trail:
                    price_safe = True

                if price_safe:
                    position.ce_price_validated = True
                    logger.info(
                        f"[CE] {position.symbol}: CE validated — price ${ce_price:.4f} "
                        f"on safe side of trail ${effective_trail:.4f}"
                    )
                else:
                    # Price still on wrong side — skip CE trigger this cycle
                    logger.debug(
                        f"[CE] {position.symbol}: CE not yet validated — price ${ce_price:.4f} "
                        f"vs trail ${effective_trail:.4f}, waiting for recovery"
                    )

            trail_hit = False
            if effective_trail and ce_price and position.ce_price_validated:
                if position.side == "BUY" and ce_price <= effective_trail:
                    trail_hit = True
                elif position.side == "SELL" and ce_price >= effective_trail:
                    trail_hit = True

            if trail_hit:
                # Dynamic reason based on entry type's CE timeframe
                ce_reason_map = {
                    'standard_m5': 'CHANDELIER_M5',
                    'standard_m15': 'CHANDELIER_SL',
                    'standard_h1': 'CHANDELIER_H1',
                    'standard_h4': 'CHANDELIER_H4',
                    'ema610_h1': 'CHANDELIER_H1',
                    'ema610_h4': 'CHANDELIER_H4',
                    'sd_demand_m15': 'CHANDELIER_SL',
                    'sd_demand_h1': 'CHANDELIER_H1',
                    'sd_demand_h4': 'CHANDELIER_H4',
                    'sd_supply_m15': 'CHANDELIER_SL',
                    'sd_supply_h1': 'CHANDELIER_H1',
                    'sd_supply_h4': 'CHANDELIER_H4',
                }
                reason = ce_reason_map.get(position.entry_type, 'CHANDELIER_SL')
                logger.warning(
                    f"{position.symbol}: {reason} hit at ${current:.2f} "
                    f"(Trail: ${effective_trail:.2f}, PNL: {position.pnl_percent:.1f}%)"
                )
                if self.db:
                    try:
                        self.db.log_operation(
                            operation_name='stop_loss_triggered',
                            risk_score=0, status='success',
                            meta_data={
                                'position_id': position.position_id,
                                'symbol': position.symbol,
                                'trailing_sl': effective_trail,
                                'chandelier_sl': position.chandelier_sl,
                                'trigger_price': current,
                                'pnl_usd': position.pnl_usd,
                                'reason': reason,
                                'entry_type': position.entry_type,
                            }
                        )
                    except Exception as e:
                        logger.error(f"[DB] Error logging trailing_sl: {e}")
                self.close_position(position.position_id, reason=reason)
                return

        # ── 3. TP1: Price-based -> partial close ─────────
        # Standard: close 70% at TP1 (ATR-based)
        # EMA610 H1/H4: close 50% at TP1 (ROI-based, +40% ROI)
        if not position.tp1_closed and not position.tp1_cancelled:
            tp1_price = position.take_profit_1
            if tp1_price:
                tp1_hit = False
                if position.side == "BUY" and current >= tp1_price:
                    tp1_hit = True
                elif position.side == "SELL" and current <= tp1_price:
                    tp1_hit = True

                if tp1_hit:
                    # Get tp1_percent from per-entry-type config
                    if position.entry_type.startswith("rsi_div_"):
                        tf = position.entry_type.replace("rsi_div_", "")
                        tp1_pct = RSI_DIV_EXIT.get(tf, {}).get('tp1_percent', 70)
                    elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                        tf = position.entry_type.split("_")[-1]
                        tp1_pct = SD_ENTRY_CONFIG.get(tf, {}).get('tp1_percent', 70)
                    elif position.entry_type.startswith("ema610_"):
                        tf = position.entry_type.replace("ema610_", "")
                        tp1_pct = EMA610_EXIT.get(tf, {}).get('tp1_percent', 50)
                    elif position.entry_type.startswith("standard_"):
                        tf = position.entry_type.replace("standard_", "")
                        tp1_pct = STANDARD_EXIT.get(tf, {}).get('tp1_percent', 70)
                    else:
                        tp1_pct = TAKE_PROFIT.get('tp1_percent', 70)

                    logger.info(
                        f"{position.symbol}: TP1 hit at ${current:.2f} "
                        f"(TP1: ${tp1_price:.2f}, {position.entry_type}, close {tp1_pct}%)"
                    )
                    self._partial_close(position, percent=tp1_pct, reason="TP1")

        # ── 4. TP2: Price-based -> close remaining ──
        # All entry types: Standard (ATR-based TP2), EMA610 (ROI-based TP2)
        if position.tp1_closed and not position.tp2_closed and not position.tp2_cancelled:
            tp2_price = position.take_profit_2
            if tp2_price:
                tp2_hit = False
                if position.side == "BUY" and current >= tp2_price:
                    tp2_hit = True
                elif position.side == "SELL" and current <= tp2_price:
                    tp2_hit = True

                if tp2_hit:
                    logger.info(
                        f"{position.symbol}: TP2 hit at ${current:.2f} "
                        f"(TP2: ${tp2_price:.2f}, {position.entry_type})"
                    )
                    self.close_position(position.position_id, reason="TP2")

    def _partial_close(self, position: Position, percent: float, reason: str):
        """
        Close partial position

        Args:
            position: Position object
            percent: Percentage to close (e.g., 70)
            reason: Close reason
        """
        try:
            close_size = position.remaining_size * (percent / 100)

            # Close on exchange (skip in paper mode)
            # In live mode, check if TP1 exchange order already filled (avoid double close)
            exchange_close_ok = False
            if self.mode == "live":
                tp1_already_filled = False
                if reason in ("TP1", "TP1_SR", "TP1_ROI", "TP1_ATR") and position.tp1_order_id:
                    try:
                        order_info = self.client.fetch_order(position.tp1_order_id, position.symbol)
                        if order_info.get('status') in ('closed', 'filled'):
                            tp1_already_filled = True
                            exchange_close_ok = True
                            logger.info(
                                f"[TP-ORDER] {position.symbol}: TP1 order already filled on exchange, "
                                f"skipping duplicate close"
                            )
                    except Exception as e:
                        logger.warning(f"[TP-ORDER] {position.symbol}: Could not check TP1 order: {e}")

                if not tp1_already_filled:
                    try:
                        close_side = 'sell' if position.side == "BUY" else 'buy'
                        self.client.close_position(
                            symbol=position.symbol,
                            side=close_side,
                            amount=close_size
                        )
                        exchange_close_ok = True
                    except Exception as e:
                        logger.error(
                            f"[PARTIAL] {position.symbol}: Exchange partial close FAILED: {e}. "
                            f"Skipping internal PNL update to prevent desync."
                        )
                        return  # Don't update internal state if exchange failed
            else:
                exchange_close_ok = True  # Paper mode always succeeds

            # Calculate exit fee for this partial close (maker rate for TP limit orders)
            close_ratio = close_size / position.size
            closed_position_value = position.margin * close_ratio * position.leverage
            exit_fee = closed_position_value * FEES['maker']

            # Calculate realized PNL for closed portion (before fees)
            realized_pnl_before_fee = position.margin * close_ratio * (position.pnl_percent / 100)

            # Track exit fee
            position.total_exit_fees += exit_fee

            # Update position
            position.remaining_size -= close_size
            position.realized_pnl += realized_pnl_before_fee

            if reason in ("TP1", "TP1_SR", "TP1_ROI", "TP1_ATR"):
                position.tp1_closed = True
                position.status = "PARTIAL_CLOSE"
                # Clear TP1 order ID (already handled — prevent auto-sync false detection)
                if self.mode == "live" and position.tp1_order_id:
                    position.tp1_order_id = None

            # Built-in position SL (sz=0) auto-covers entire remaining position
            # No need to cancel & re-place after TP1 partial close

            # Update paper balance: return margin + PNL - fee for closed portion
            if self.mode == "paper":
                returned_margin = position.margin * close_ratio
                net_return = returned_margin + realized_pnl_before_fee - exit_fee
                self.paper_balance += net_return
                logger.info(
                    f"{position.symbol}: Paper balance updated: +${net_return:,.2f} "
                    f"(margin: ${returned_margin:,.2f}, PNL: ${realized_pnl_before_fee:,.2f}, fee: ${exit_fee:.2f}) "
                    f"-> ${self.paper_balance:,.2f}"
                )

            logger.info(
                f"{position.symbol}: Closed {percent}% ({close_size:.6f}) at ${position.current_price:.2f} "
                f"(Reason: {reason}, PNL: ${realized_pnl_before_fee:.2f}, Fee: ${exit_fee:.2f})"
            )

            # Send Telegram notification for partial close
            if hasattr(self, 'telegram') and self.telegram:
                try:
                    self.telegram.send_position_partial_closed(position, percent, reason, realized_pnl_before_fee)
                except Exception as e:
                    logger.error(f"Failed to send partial close notification: {e}")

            # Save after partial close
            self._save_positions()

            # Log to database
            if self.db:
                try:
                    self.db.log_operation(
                        operation_name='partial_close',
                        risk_score=0,
                        status='success',
                        meta_data={
                            'position_id': position.position_id,
                            'symbol': position.symbol,
                            'side': position.side,
                            'percent': percent,
                            'close_size': close_size,
                            'realized_pnl': realized_pnl_before_fee,
                            'remaining_size': position.remaining_size,
                            'close_reason': reason,
                            'close_price': position.current_price,
                        }
                    )
                except Exception as e:
                    logger.error(f"[DB] Error logging partial_close: {e}")

        except Exception as e:
            logger.error(f"Error closing partial position: {e}")

    def partial_close_manual(self, position_id: str, percent: float) -> Optional[dict]:
        """
        Manually close a percentage of remaining position size.

        Unlike _partial_close() (used by TP system), this:
        - Does NOT set tp1_closed/tp2_closed flags
        - Uses reason "MANUAL_PARTIAL"
        - Can be called from Telegram buttons (25%, 50%, 75%)

        Args:
            position_id: Position ID
            percent: Percentage of REMAINING size to close (e.g., 25, 50, 75)

        Returns:
            dict with close details or None if failed
        """
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found")
            return None

        position = self.positions[position_id]

        if position.status == "CLOSED":
            logger.warning(f"{position.symbol}: Position already closed")
            return None

        if position.remaining_size <= 0:
            logger.warning(f"{position.symbol}: No remaining size to close")
            return None

        try:
            close_size = position.remaining_size * (percent / 100)

            # Close on exchange (skip in paper mode)
            if self.mode == "live":
                close_side = 'sell' if position.side == "BUY" else 'buy'
                self.client.close_position(
                    symbol=position.symbol,
                    side=close_side,
                    amount=close_size
                )

            # Calculate realized PNL and fee for closed portion
            close_ratio = close_size / position.size
            realized_pnl = position.margin * close_ratio * (position.pnl_percent / 100)

            # Calculate exit fee (maker rate for manual limit orders)
            closed_position_value = position.margin * close_ratio * position.leverage
            exit_fee = closed_position_value * FEES['maker']
            position.total_exit_fees += exit_fee

            # Update position
            position.remaining_size -= close_size
            position.realized_pnl += realized_pnl
            position.status = "PARTIAL_CLOSE"

            # Update paper balance: return margin + PNL - fee for closed portion
            if self.mode == "paper":
                returned_margin = position.margin * close_ratio
                net_return = returned_margin + realized_pnl - exit_fee
                self.paper_balance += net_return
                logger.info(
                    f"{position.symbol}: Manual partial close: +${net_return:,.2f} "
                    f"(margin: ${returned_margin:,.2f}, PNL: ${realized_pnl:,.2f}, fee: ${exit_fee:.2f}) -> ${self.paper_balance:,.2f}"
                )

            # If remaining size is effectively zero, fully close
            if position.remaining_size / position.size < 0.01:
                position.status = "CLOSED"
                position.close_reason = "MANUAL_PARTIAL"
                position.remaining_size = 0

            logger.info(
                f"{position.symbol}: Manual partial close {percent}% ({close_size:.6f}) at ${position.current_price:.2f} "
                f"(PNL: ${realized_pnl:.2f})"
            )

            # Save after partial close
            self._save_positions()

            return {
                "symbol": position.symbol,
                "percent": percent,
                "close_size": close_size,
                "realized_pnl": realized_pnl,
                "remaining_size": position.remaining_size,
                "remaining_pct": (position.remaining_size / position.size * 100) if position.size > 0 else 0,
                "status": position.status,
            }

        except Exception as e:
            logger.error(f"Error in manual partial close: {e}")
            return None

    def cancel_tp(self, position_id: str, tp_level: str) -> bool:
        """
        Disable auto take profit for a position.
        Position stays open, but bot will NOT auto-close at TP level.

        Args:
            position_id: Position ID
            tp_level: "tp1", "tp2", or "all"

        Returns:
            True if cancelled successfully
        """
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found for cancel_tp")
            return False

        position = self.positions[position_id]

        if position.status == "CLOSED":
            logger.warning(f"{position.symbol}: Cannot cancel TP - position already closed")
            return False

        if tp_level in ("tp1", "all"):
            if not position.tp1_closed:
                position.take_profit_1 = None
                position.tp1_cancelled = True
                logger.info(f"{position.symbol}: TP1 cancelled (auto TP disabled)")

        if tp_level in ("tp2", "all"):
            if not position.tp2_closed:
                position.take_profit_2 = None
                position.tp2_cancelled = True
                logger.info(f"{position.symbol}: TP2 cancelled (auto TP disabled)")

        self._save_positions()
        return True

    def modify_tp(self, position_id: str, tp_level: str, new_price: float) -> bool:
        """
        Modify take-profit price for a position. Re-enables cancelled TPs.

        Args:
            position_id: Position ID
            tp_level: "tp1" or "tp2"
            new_price: New TP price (must be on correct side of current price)

        Returns:
            True if modified successfully
        """
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found for modify_tp")
            return False

        position = self.positions[position_id]

        if position.status == "CLOSED":
            logger.warning(f"{position.symbol}: Cannot modify TP - position already closed")
            return False

        if new_price <= 0:
            logger.warning(f"{position.symbol}: Invalid TP price {new_price}")
            return False

        close_side = 'sell' if position.side == "BUY" else 'buy'

        if tp_level == "tp1":
            old_tp = position.take_profit_1
            position.take_profit_1 = new_price
            position.tp1_cancelled = False
            # Cancel old OKX order if exists
            if self.mode == "live" and position.tp1_order_id:
                try:
                    self.client.cancel_order(position.symbol, position.tp1_order_id)
                    logger.info(f"{position.symbol}: Cancelled old TP1 order {position.tp1_order_id}")
                except Exception as e:
                    logger.warning(f"{position.symbol}: Failed to cancel old TP1 order: {e}")
                position.tp1_order_id = None
            # Place new TP1 limit order on OKX
            if self.mode == "live":
                if position.entry_type.startswith("rsi_div_"):
                    _tf = position.entry_type.replace("rsi_div_", "")
                    _tp1_pct = RSI_DIV_EXIT.get(_tf, {}).get('tp1_percent', 70)
                elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                    _tf = position.entry_type.split("_")[-1]
                    _tp1_pct = SD_ENTRY_CONFIG.get(_tf, {}).get('tp1_percent', 70)
                elif position.entry_type.startswith("ema610_"):
                    _tf = position.entry_type.replace("ema610_", "")
                    _tp1_pct = EMA610_EXIT.get(_tf, {}).get('tp1_percent', 50)
                elif position.entry_type.startswith("standard_"):
                    _tf = position.entry_type.replace("standard_", "")
                    _tp1_pct = STANDARD_EXIT.get(_tf, {}).get('tp1_percent', 70)
                else:
                    _tp1_pct = TAKE_PROFIT.get('tp1_percent', 70)
                tp1_size = position.remaining_size * (_tp1_pct / 100)
                try:
                    order = self.client.create_take_profit_order(
                        symbol=position.symbol,
                        side=close_side,
                        amount=tp1_size,
                        tp_price=new_price,
                    )
                    position.tp1_order_id = order.get('id')
                    logger.info(f"[TP-ORDER] {position.symbol}: New TP1 {close_side.upper()} {tp1_size:.6f} @ ${new_price:.4f} (modified via web)")
                except Exception as e:
                    logger.error(f"[TP-ORDER] {position.symbol}: Failed to place new TP1: {e}")
                    position.tp1_order_id = "FAILED"
            logger.info(f"{position.symbol}: TP1 modified {old_tp} → {new_price} (via web)")
        elif tp_level == "tp2":
            old_tp = position.take_profit_2
            position.take_profit_2 = new_price
            position.tp2_cancelled = False
            if self.mode == "live" and position.tp2_order_id:
                try:
                    self.client.cancel_order(position.symbol, position.tp2_order_id)
                    logger.info(f"{position.symbol}: Cancelled old TP2 order {position.tp2_order_id}")
                except Exception as e:
                    logger.warning(f"{position.symbol}: Failed to cancel old TP2 order: {e}")
                position.tp2_order_id = None
            # Place new TP2 limit order on OKX
            if self.mode == "live":
                if position.take_profit_1 and not position.tp1_closed and not position.tp1_cancelled:
                    if position.entry_type.startswith("rsi_div_"):
                        _tf2 = position.entry_type.replace("rsi_div_", "")
                        _tp1_pct2 = RSI_DIV_EXIT.get(_tf2, {}).get('tp1_percent', 70)
                    elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                        _tf2 = position.entry_type.split("_")[-1]
                        _tp1_pct2 = SD_ENTRY_CONFIG.get(_tf2, {}).get('tp1_percent', 70)
                    elif position.entry_type.startswith("ema610_"):
                        _tf2 = position.entry_type.replace("ema610_", "")
                        _tp1_pct2 = EMA610_EXIT.get(_tf2, {}).get('tp1_percent', 50)
                    elif position.entry_type.startswith("standard_"):
                        _tf2 = position.entry_type.replace("standard_", "")
                        _tp1_pct2 = STANDARD_EXIT.get(_tf2, {}).get('tp1_percent', 70)
                    else:
                        _tp1_pct2 = TAKE_PROFIT.get('tp1_percent', 70)
                    tp1_portion = position.remaining_size * (_tp1_pct2 / 100)
                    tp2_size = position.remaining_size - tp1_portion
                else:
                    tp2_size = position.remaining_size
                try:
                    order = self.client.create_take_profit_order(
                        symbol=position.symbol,
                        side=close_side,
                        amount=tp2_size,
                        tp_price=new_price,
                    )
                    position.tp2_order_id = order.get('id')
                    logger.info(f"[TP-ORDER] {position.symbol}: New TP2 {close_side.upper()} {tp2_size:.6f} @ ${new_price:.4f} (modified via web)")
                except Exception as e:
                    logger.error(f"[TP-ORDER] {position.symbol}: Failed to place new TP2: {e}")
                    position.tp2_order_id = "FAILED"
            logger.info(f"{position.symbol}: TP2 modified {old_tp} → {new_price} (via web)")
        else:
            logger.warning(f"{position.symbol}: Invalid TP level: {tp_level}")
            return False

        self._save_positions()
        return True

    def modify_sl(self, position_id: str, new_sl_price: float) -> bool:
        """
        Modify trailing stop-loss price for a position.

        Args:
            position_id: Position ID
            new_sl_price: New SL price (must be on correct side of current price)

        Returns:
            True if modified successfully
        """
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found for modify_sl")
            return False

        position = self.positions[position_id]

        if position.status == "CLOSED":
            logger.warning(f"{position.symbol}: Cannot modify SL - position already closed")
            return False

        if new_sl_price <= 0:
            logger.warning(f"{position.symbol}: Invalid SL price {new_sl_price}")
            return False

        old_sl = position.trailing_sl
        position.trailing_sl = new_sl_price
        logger.info(f"{position.symbol}: SL modified {old_sl} → {new_sl_price} (via web)")
        self._save_positions()
        return True

    # ── Order Sync: verify TP/SL orders still exist on exchange ──

    def _sync_tp_sl_orders(self, position: Position) -> None:
        """
        Verify TP1/TP2/Hard SL orders still exist on OKX. Re-place if canceled.

        OKX merges positions of the same symbol into one. When multiple local
        positions place reduce-only orders whose total exceeds the merged position
        size, OKX auto-cancels excess orders. This method detects and re-places them.

        Throttled to run at most once per _ORDER_SYNC_INTERVAL per position.
        """
        if self.mode != "live":
            return

        # Skip if too many consecutive re-place failures (OKX keeps canceling)
        failures = self._order_sync_failures.get(position.position_id, 0)
        if failures >= self._MAX_SYNC_FAILURES:
            return

        now = time.time()
        last_sync = self._last_order_sync_ts.get(position.position_id, 0)
        if now - last_sync < self._ORDER_SYNC_INTERVAL:
            return

        self._last_order_sync_ts[position.position_id] = now
        changed = False
        close_side = 'sell' if position.side == "BUY" else 'buy'

        # ── Check TP1 order ──
        if (position.tp1_order_id
                and position.tp1_order_id != "FAILED"
                and not position.tp1_closed
                and not position.tp1_cancelled):
            try:
                info = self.client.fetch_order(position.tp1_order_id, position.symbol)
                status = info.get('status', '')
                if status in ('canceled', 'cancelled', 'not_found'):
                    # Log cancel reason from exchange for debugging
                    cancel_reason = info.get('info', {}).get('cancelSource', '') if isinstance(info.get('info'), dict) else ''
                    logger.warning(
                        f"[ORDER-SYNC] {position.symbol}: TP1 order {position.tp1_order_id[:12]} "
                        f"was {status} on exchange (reason={cancel_reason}), re-placing..."
                    )
                    position.tp1_order_id = None
                    changed = True
            except Exception as e:
                logger.debug(f"[ORDER-SYNC] {position.symbol}: TP1 check failed: {e}")

        # ── Check TP2 order ──
        if (position.tp2_order_id
                and position.tp2_order_id != "FAILED"
                and not position.tp2_closed
                and not position.tp2_cancelled):
            try:
                info = self.client.fetch_order(position.tp2_order_id, position.symbol)
                status = info.get('status', '')
                if status in ('canceled', 'cancelled', 'not_found'):
                    cancel_reason = info.get('info', {}).get('cancelSource', '') if isinstance(info.get('info'), dict) else ''
                    logger.warning(
                        f"[ORDER-SYNC] {position.symbol}: TP2 order {position.tp2_order_id[:12]} "
                        f"was {status} on exchange (reason={cancel_reason}), re-placing..."
                    )
                    position.tp2_order_id = None
                    changed = True
            except Exception as e:
                logger.debug(f"[ORDER-SYNC] {position.symbol}: TP2 check failed: {e}")

        # ── Check Hard SL (built-in position SL) ──
        # Built-in SL can be checked via algo order status
        if (position.hard_sl_order_id
                and position.hard_sl_order_id != "FAILED"):
            try:
                info = self.client.fetch_order(position.hard_sl_order_id, position.symbol)
                status = info.get('status', '')
                if status in ('canceled', 'cancelled', 'not_found'):
                    cancel_reason = info.get('info', {}).get('cancelSource', '') if isinstance(info.get('info'), dict) else ''
                    logger.warning(
                        f"[ORDER-SYNC] {position.symbol}: Position SL {position.hard_sl_order_id[:12]} "
                        f"was {status} (reason={cancel_reason}), re-setting..."
                    )
                    position.hard_sl_order_id = None
                    changed = True
            except Exception as e:
                logger.debug(f"[ORDER-SYNC] {position.symbol}: Position SL check failed: {e}")

        # ── Re-place missing orders ──
        if not changed:
            # All orders still alive — reset failure counter
            self._order_sync_failures[position.position_id] = 0
            return

        re_placed = False

        # Re-place TP1
        if (not position.tp1_order_id
                and position.take_profit_1
                and not position.tp1_closed
                and not position.tp1_cancelled):
            if position.entry_type.startswith("rsi_div_"):
                _tf = position.entry_type.replace("rsi_div_", "")
                _tp1_pct = RSI_DIV_EXIT.get(_tf, {}).get('tp1_percent', 70)
            elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                _tf = position.entry_type.split("_")[-1]
                _tp1_pct = SD_ENTRY_CONFIG.get(_tf, {}).get('tp1_percent', 70)
            elif position.entry_type.startswith("ema610_"):
                _tf = position.entry_type.replace("ema610_", "")
                _tp1_pct = EMA610_EXIT.get(_tf, {}).get('tp1_percent', 50)
            elif position.entry_type.startswith("standard_"):
                _tf = position.entry_type.replace("standard_", "")
                _tp1_pct = STANDARD_EXIT.get(_tf, {}).get('tp1_percent', 70)
            else:
                _tp1_pct = TAKE_PROFIT.get('tp1_percent', 70)
            tp1_size = position.remaining_size * (_tp1_pct / 100)
            try:
                order = self.client.create_take_profit_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=tp1_size,
                    tp_price=position.take_profit_1,
                )
                position.tp1_order_id = order.get('id')
                re_placed = True
                logger.info(
                    f"[ORDER-SYNC] {position.symbol}: TP1 re-placed "
                    f"@ ${position.take_profit_1:.4f} (id={order.get('id', '?')[:12]})"
                )
            except Exception as e:
                logger.error(f"[ORDER-SYNC] {position.symbol}: TP1 re-place failed: {e}")
                position.tp1_order_id = "FAILED"

        # Re-place TP2
        if (not position.tp2_order_id
                and position.take_profit_2
                and not position.tp2_closed
                and not position.tp2_cancelled):
            if position.entry_type.startswith("rsi_div_"):
                _tf2 = position.entry_type.replace("rsi_div_", "")
                _tp1_pct2 = RSI_DIV_EXIT.get(_tf2, {}).get('tp1_percent', 70)
            elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                _tf2 = position.entry_type.split("_")[-1]
                _tp1_pct2 = SD_ENTRY_CONFIG.get(_tf2, {}).get('tp1_percent', 70)
            elif position.entry_type.startswith("ema610_"):
                _tf2 = position.entry_type.replace("ema610_", "")
                _tp1_pct2 = EMA610_EXIT.get(_tf2, {}).get('tp1_percent', 50)
            elif position.entry_type.startswith("standard_"):
                _tf2 = position.entry_type.replace("standard_", "")
                _tp1_pct2 = STANDARD_EXIT.get(_tf2, {}).get('tp1_percent', 70)
            else:
                _tp1_pct2 = TAKE_PROFIT.get('tp1_percent', 70)
            tp2_pct = 100 - _tp1_pct2
            tp2_size = position.remaining_size * (tp2_pct / 100)
            try:
                order = self.client.create_take_profit_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=tp2_size,
                    tp_price=position.take_profit_2,
                )
                position.tp2_order_id = order.get('id')
                re_placed = True
                logger.info(
                    f"[ORDER-SYNC] {position.symbol}: TP2 re-placed "
                    f"@ ${position.take_profit_2:.4f} (id={order.get('id', '?')[:12]})"
                )
            except Exception as e:
                logger.error(f"[ORDER-SYNC] {position.symbol}: TP2 re-place failed: {e}")
                position.tp2_order_id = "FAILED"

        # Re-place Hard SL (trigger + limit order)
        if not position.hard_sl_order_id and position.stop_loss:
            try:
                order = self.client.create_stop_market_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=position.remaining_size,
                    stop_price=position.stop_loss,
                    reduce_only=True,
                )
                position.hard_sl_order_id = order.get('id')
                re_placed = True
                logger.info(
                    f"[ORDER-SYNC] {position.symbol}: Trigger SL re-set "
                    f"@ ${position.stop_loss:.4f} (id={order.get('id', '?')[:12]})"
                )
            except Exception as e:
                logger.error(f"[ORDER-SYNC] {position.symbol}: Position SL re-set failed: {e}")
                position.hard_sl_order_id = "FAILED"

        # Track re-place success/failure for circuit breaker
        if re_placed:
            self._order_sync_failures[position.position_id] = 0
        else:
            self._order_sync_failures[position.position_id] = failures + 1
            if self._order_sync_failures[position.position_id] >= self._MAX_SYNC_FAILURES:
                logger.warning(
                    f"[ORDER-SYNC] {position.symbol}: {self._MAX_SYNC_FAILURES} consecutive "
                    f"re-place failures, pausing sync for this position"
                )

        self._save_positions()

    # ── CE Stop-Market Order Management (live mode only) ─────────

    def _place_initial_tp_sl(self, position) -> None:
        """Place TP1, TP2, and hard SL orders on exchange right after opening position."""
        close_side = 'sell' if position.side == "BUY" else 'buy'

        # ── TP1 order ──
        if position.take_profit_1 and not position.tp1_order_id and not position.tp1_closed and not position.tp1_cancelled:
            # Get tp1_percent per entry type
            if position.entry_type.startswith("rsi_div_"):
                _tf = position.entry_type.replace("rsi_div_", "")
                _tp1_pct = RSI_DIV_EXIT.get(_tf, {}).get('tp1_percent', 70)
            elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                _tf = position.entry_type.split("_")[-1]
                _tp1_pct = SD_ENTRY_CONFIG.get(_tf, {}).get('tp1_percent', 70)
            elif position.entry_type.startswith("ema610_"):
                _tf = position.entry_type.replace("ema610_", "")
                _tp1_pct = EMA610_EXIT.get(_tf, {}).get('tp1_percent', 50)
            elif position.entry_type.startswith("standard_"):
                _tf = position.entry_type.replace("standard_", "")
                _tp1_pct = STANDARD_EXIT.get(_tf, {}).get('tp1_percent', 70)
            else:
                _tp1_pct = TAKE_PROFIT.get('tp1_percent', 70)
            tp1_size = position.remaining_size * (_tp1_pct / 100)
            try:
                order = self.client.create_take_profit_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=tp1_size,
                    tp_price=position.take_profit_1,
                )
                position.tp1_order_id = order.get('id')
                logger.info(
                    f"[TP-ORDER] {position.symbol}: TP1 {close_side.upper()} "
                    f"{tp1_size:.6f} @ ${position.take_profit_1:.4f}"
                )
            except Exception as e:
                logger.error(f"[TP-ORDER] {position.symbol}: Failed TP1: {e}")
                position.tp1_order_id = "FAILED"

        # ── TP2 order ──
        if position.take_profit_2 and not position.tp2_order_id and not position.tp2_closed and not position.tp2_cancelled:
            # TP2 = remainder after TP1 (avoids rounding gap from separate % calc)
            if position.take_profit_1 and not position.tp1_closed and not position.tp1_cancelled:
                # TP1 is active → TP2 gets the exact remainder
                if position.entry_type.startswith("rsi_div_"):
                    _tf2 = position.entry_type.replace("rsi_div_", "")
                    _tp1_pct2 = RSI_DIV_EXIT.get(_tf2, {}).get('tp1_percent', 70)
                elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                    _tf2 = position.entry_type.split("_")[-1]
                    _tp1_pct2 = SD_ENTRY_CONFIG.get(_tf2, {}).get('tp1_percent', 70)
                elif position.entry_type.startswith("ema610_"):
                    _tf2 = position.entry_type.replace("ema610_", "")
                    _tp1_pct2 = EMA610_EXIT.get(_tf2, {}).get('tp1_percent', 50)
                elif position.entry_type.startswith("standard_"):
                    _tf2 = position.entry_type.replace("standard_", "")
                    _tp1_pct2 = STANDARD_EXIT.get(_tf2, {}).get('tp1_percent', 70)
                else:
                    _tp1_pct2 = TAKE_PROFIT.get('tp1_percent', 70)
                tp1_portion = position.remaining_size * (_tp1_pct2 / 100)
                tp2_size = position.remaining_size - tp1_portion
            else:
                # TP1 already closed → TP2 closes all remaining
                tp2_size = position.remaining_size
            try:
                order = self.client.create_take_profit_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=tp2_size,
                    tp_price=position.take_profit_2,
                )
                position.tp2_order_id = order.get('id')
                logger.info(
                    f"[TP-ORDER] {position.symbol}: TP2 {close_side.upper()} "
                    f"{tp2_size:.6f} @ ${position.take_profit_2:.4f}"
                )
            except Exception as e:
                logger.error(f"[TP-ORDER] {position.symbol}: Failed TP2: {e}")
                position.tp2_order_id = "FAILED"

        # ── Hard SL (trigger + limit order, independent per position) ──
        if position.stop_loss and not position.hard_sl_order_id:
            try:
                order = self.client.create_stop_market_order(
                    symbol=position.symbol,
                    side=close_side,
                    amount=position.remaining_size,
                    stop_price=position.stop_loss,
                    reduce_only=True,
                )
                position.hard_sl_order_id = order.get('id')
                logger.info(
                    f"[SL-ORDER] {position.symbol}: Trigger SL set "
                    f"@ ${position.stop_loss:.4f} (size={position.remaining_size:.6f})"
                )
            except Exception as e:
                logger.error(f"[SL-ORDER] {position.symbol}: Failed position SL: {e}")
                position.hard_sl_order_id = "FAILED"

    def _cancel_ce_stop_order(self, position):
        """Cancel leftover CE stop-market order on exchange (transition cleanup)."""
        ce_order_id = getattr(position, 'ce_order_id', None)
        if self.mode != "live" or not ce_order_id:
            return
        try:
            self.client.cancel_order(ce_order_id, position.symbol)
            logger.info(f"[CE-ORDER] {position.symbol}: Cancelled leftover CE order (cleanup)")
        except Exception as e:
            logger.warning(f"[CE-ORDER] {position.symbol}: Cancel cleanup failed: {e}")

    def _cancel_tp_sl_orders(self, position) -> None:
        """Cancel all TP/SL orders on exchange (cleanup before close)."""
        # Cancel TP trigger orders (these need manual cancel)
        for attr, label in [
            ('tp1_order_id', 'TP1'),
            ('tp2_order_id', 'TP2'),
        ]:
            order_id = getattr(position, attr, None)
            if order_id and order_id != "FAILED":
                try:
                    self.client.cancel_order(order_id, position.symbol)
                    logger.info(f"[ORDER] {position.symbol}: Cancelled {label} order (cleanup)")
                except Exception as e:
                    logger.warning(f"[ORDER] {position.symbol}: Cancel {label} failed: {e}")
            if order_id:
                setattr(position, attr, None)

        # Trigger SL must be cancelled manually (unlike built-in position SL)
        if position.hard_sl_order_id and position.hard_sl_order_id != "FAILED":
            try:
                self.client.cancel_order(position.hard_sl_order_id, position.symbol)
                logger.info(f"[ORDER] {position.symbol}: Cancelled trigger SL {position.hard_sl_order_id[:12]}")
            except Exception as e:
                logger.warning(f"[ORDER] {position.symbol}: Cancel trigger SL failed: {e}")
            position.hard_sl_order_id = None

    @staticmethod
    def _match_fills_to_positions(close_events: list, sharing_positions: list) -> dict | None:
        """
        Match OKX close fill events to bot positions for accurate per-position PnL.

        Strategy: try all permutations (N! is small for 2-3 positions),
        score by TP/SL price match (strong) + PnL estimate closeness (weak).

        Returns: dict {position_id: close_event} or None if can't match.
        """
        if len(close_events) != len(sharing_positions):
            return None  # Can't do 1:1 matching

        from itertools import permutations

        best_assignment = None
        best_score = float('inf')

        for perm in permutations(range(len(close_events))):
            score = 0.0

            for pos_idx, fill_idx in enumerate(perm):
                p = sharing_positions[pos_idx]
                f = close_events[fill_idx]
                fill_price = f['fill_price']

                # Check TP/SL price match (strong signal, -1000 bonus)
                for target in [
                    getattr(p, 'take_profit_1', None),
                    getattr(p, 'take_profit_2', None),
                ]:
                    if target and target > 0 and fill_price > 0:
                        if abs(fill_price - target) / target < 0.002:
                            score -= 1000
                            break

                sl = getattr(p, 'stop_loss', None)
                if sl and sl > 0 and fill_price > 0:
                    if abs(fill_price - sl) / sl < 0.003:
                        score -= 1000

                # PnL estimate closeness (smaller diff = better)
                p_entry = getattr(p, 'entry_price', 0) or 0
                p_margin = getattr(p, 'margin', 0) or 0
                p_lev = getattr(p, 'leverage', 1) or 1
                if p_entry > 0 and p_margin > 0:
                    price_pct = (fill_price - p_entry) / p_entry
                    if p.side == "SELL":
                        price_pct = -price_pct
                    estimated_pnl = price_pct * p_margin * p_lev
                    score += abs(estimated_pnl - f['pnl'])

            if score < best_score:
                best_score = score
                best_assignment = perm

        if best_assignment is None:
            return None

        return {
            sharing_positions[pos_idx].position_id: close_events[fill_idx]
            for pos_idx, fill_idx in enumerate(best_assignment)
        }

    def _sync_close_price_from_exchange(self, position) -> bool:
        """
        Query OKX position history to get actual close price and realized PNL.
        Updates position with real exit price AND OKX's realized PNL (overrides
        bot's internal PNL which may be wrong due to false TP1 detection etc).

        When multiple bot positions exist for the same symbol, OKX merges them
        into one position. This method splits OKX's realized PNL proportionally
        by remaining_size across all bot positions for that symbol.

        Returns True if successfully synced, False otherwise.
        """
        if self.mode != "live":
            return False

        # Already synced (handles double-call from bot.py + close_position)
        if getattr(position, '_okx_pnl_synced', False):
            return True

        try:
            history = self.client.get_position_history()
            expected_side = 'long' if position.side == 'BUY' else 'short'

            # Find best matching OKX entry: prefer matching by entry price (most accurate)
            # OKX history is sorted newest first, so naive loop picks wrong entry
            # when multiple trades exist for the same symbol.
            # CRITICAL: OKX merges all entries for same symbol into one position.
            # When bot closes one of multiple positions (partial reduce), OKX does NOT
            # create a history entry until the ENTIRE merged position closes.
            # Without close_time check, fallback matches WRONG old history entries.
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            best_match = None
            recent_fallback = None  # Only allow fallback for recent closes
            for h in history:
                if h['symbol'] != position.symbol or h.get('side', '') != expected_side:
                    continue
                if not h.get('close_price', 0):
                    continue
                # Exact entry price match (within 0.01% tolerance for float precision)
                okx_open = h.get('open_price', 0)
                if okx_open > 0 and position.entry_price > 0:
                    price_diff_pct = abs(okx_open - position.entry_price) / position.entry_price
                    if price_diff_pct < 0.0001:  # 0.01% tolerance
                        best_match = h
                        break
                # Fallback: only use if close_time is recent (within 10 minutes)
                # Prevents matching stale history entries from hours/days ago
                if recent_fallback is None:
                    h_close_time = h.get('close_time', '')
                    if h_close_time:
                        try:
                            h_close_dt = datetime.fromisoformat(h_close_time)
                            if h_close_dt.tzinfo is None:
                                h_close_dt = h_close_dt.replace(tzinfo=timezone.utc)
                            age_seconds = (now_utc - h_close_dt).total_seconds()
                            if age_seconds < 600:  # 10 minutes
                                recent_fallback = h
                            else:
                                logger.debug(
                                    f"[SYNC] {position.symbol}: Skipping stale history entry "
                                    f"(closed {age_seconds/60:.0f}min ago, open={okx_open:.4f})"
                                )
                        except (ValueError, TypeError):
                            pass  # Can't parse time, skip fallback for this entry
            if best_match is None:
                best_match = recent_fallback

            h = best_match
            if h:
                close_price = h.get('close_price', 0)
                if close_price > 0:
                    old_price = position.current_price
                    old_pnl = position.pnl_usd
                    position.current_price = close_price

                    # Override bot's PNL with OKX's actual realized PNL
                    # This corrects cases where bot falsely detected TP1
                    # partial close but OKX didn't actually execute it
                    okx_realized_pnl = h.get('realized_pnl', 0)
                    okx_fee = h.get('fee', 0)  # OKX fee is negative
                    okx_funding = h.get('funding_fee', 0)  # funding fee
                    if okx_realized_pnl != 0:
                        # ── Handle OKX merged positions ──
                        # OKX merges all positions for same symbol into one.
                        # Find all bot positions sharing this OKX position:
                        # - Still OPEN/PARTIAL_CLOSE
                        # - Or already synced in this batch (CLOSED but _okx_pnl_synced=True)
                        sharing_positions = [
                            p for p in self.positions.values()
                            if p.symbol == position.symbol
                            and (
                                p.status in ("OPEN", "PARTIAL_CLOSE")
                                or getattr(p, '_okx_pnl_synced', False)
                            )
                        ]

                        if len(sharing_positions) > 1:
                            # Multiple positions → try per-fill matching first,
                            # fall back to proportional split if fills unavailable
                            total_fees = abs(okx_fee) + abs(okx_funding)
                            okx_close_time = h.get('close_time', '')
                            fill_matched = False

                            # ── Strategy 1: Per-fill matching via OKX bills API ──
                            okx_close_time_utc = h.get('close_time', '')
                            if hasattr(self.client, 'get_position_close_fills'):
                                try:
                                    close_events = self.client.get_position_close_fills(
                                        position.symbol,
                                        close_time_utc=okx_close_time_utc,
                                    )
                                    if close_events:
                                        matches = self._match_fills_to_positions(
                                            close_events, sharing_positions
                                        )
                                        if matches:
                                            fill_pnl_total = sum(
                                                f['pnl'] for f in close_events
                                            )
                                            for p in sharing_positions:
                                                f = matches.get(p.position_id)
                                                if not f:
                                                    continue
                                                # Scale fill PnL to OKX net total
                                                if fill_pnl_total != 0:
                                                    p_share = f['pnl'] / fill_pnl_total
                                                else:
                                                    p_share = 1.0 / len(sharing_positions)

                                                p_pnl = okx_realized_pnl * p_share
                                                p_fees = total_fees * p_share
                                                p._pre_sync_remaining_size = p.remaining_size
                                                p.realized_pnl = 0
                                                p.remaining_size = 0
                                                p.pnl_usd = p_pnl
                                                p.roi_percent = (
                                                    (p_pnl / p.margin) * 100 if p.margin else 0
                                                )
                                                p.total_exit_fees = p_fees
                                                p.current_price = f['fill_price']
                                                p._close_fill_price = f['fill_price']
                                                p._okx_pnl_synced = True

                                                # Per-fill close time (ms timestamp)
                                                if f.get('timestamp'):
                                                    from datetime import (
                                                        datetime as _dt,
                                                        timezone as _tz,
                                                    )
                                                    ts_ms = int(f['timestamp'])
                                                    utc_dt = _dt.fromtimestamp(
                                                        ts_ms / 1000, tz=_tz.utc
                                                    )
                                                    p._okx_close_time = utc_dt.isoformat()
                                                elif okx_close_time:
                                                    p._okx_close_time = okx_close_time

                                                logger.info(
                                                    f"[SYNC] {p.symbol}: Fill-matched "
                                                    f"{p.entry_type} @ {f['fill_price']:.6f}, "
                                                    f"share {p_share:.1%} = PNL ${p_pnl:.2f} "
                                                    f"(total OKX: ${okx_realized_pnl:.2f})"
                                                )
                                            fill_matched = True
                                except Exception as e:
                                    logger.warning(
                                        f"[SYNC] {position.symbol}: Fill matching failed: {e}, "
                                        f"falling back to proportional split"
                                    )

                            # ── Strategy 2: Proportional split (fallback) ──
                            if not fill_matched:
                                raw_pnls = {}
                                for p in sharing_positions:
                                    p_margin = getattr(p, 'margin', 0) or 0
                                    p_entry = getattr(p, 'entry_price', 0) or 0
                                    p_lev = getattr(p, 'leverage', 1) or 1
                                    if p_entry > 0 and p_margin > 0:
                                        price_pct = (close_price - p_entry) / p_entry
                                        if p.side == "SELL":
                                            price_pct = -price_pct
                                        raw_pnls[id(p)] = price_pct * p_margin * p_lev
                                    else:
                                        raw_pnls[id(p)] = 0

                                raw_total = sum(raw_pnls.values())

                                for p in sharing_positions:
                                    if raw_total != 0:
                                        p_share = raw_pnls[id(p)] / raw_total
                                    else:
                                        p_share = 1.0 / len(sharing_positions)

                                    p_pnl = okx_realized_pnl * p_share
                                    p_fees = total_fees * p_share
                                    p._pre_sync_remaining_size = p.remaining_size
                                    p.realized_pnl = 0
                                    p.remaining_size = 0
                                    p.pnl_usd = p_pnl
                                    p.roi_percent = (
                                        (p_pnl / p.margin) * 100 if p.margin else 0
                                    )
                                    p.total_exit_fees = p_fees
                                    p.current_price = close_price
                                    p._okx_pnl_synced = True
                                    if okx_close_time:
                                        p._okx_close_time = okx_close_time

                                    logger.info(
                                        f"[SYNC] {p.symbol}: OKX merged "
                                        f"{len(sharing_positions)} positions, "
                                        f"{p.entry_type} share {p_share:.1%} "
                                        f"= PNL ${p_pnl:.2f} "
                                        f"(total OKX: ${okx_realized_pnl:.2f})"
                                    )
                        else:
                            # Single in-memory position. OKX's realized_pnl is cumulative —
                            # includes PnL from earlier reduces by sibling positions.
                            # sibling_reduce_pnl tracks how much was already allocated.
                            already_allocated = getattr(position, 'sibling_reduce_pnl', 0) or 0
                            shared_pnl = okx_realized_pnl - already_allocated
                            shared_fees = abs(okx_fee) + abs(okx_funding)
                            position._pre_sync_remaining_size = position.remaining_size

                            if already_allocated != 0:
                                logger.info(
                                    f"[SYNC] {position.symbol}: OKX total ${okx_realized_pnl:.2f} "
                                    f"- sibling reduces ${already_allocated:.2f} "
                                    f"= ${shared_pnl:.2f} for this position"
                                )

                            # Apply synced PNL
                            position.realized_pnl = 0
                            position.remaining_size = 0
                            position.pnl_usd = shared_pnl
                            position.roi_percent = (shared_pnl / position.margin) * 100 if position.margin else 0
                            position.total_exit_fees = shared_fees
                            position._okx_pnl_synced = True

                            # Store OKX close time for accurate close_time in close_position()
                            okx_close_time = h.get('close_time', '')
                            if okx_close_time:
                                position._okx_close_time = okx_close_time

                        logger.info(
                            f"[SYNC] {position.symbol}: Synced PNL from OKX: "
                            f"${old_pnl:.2f} -> ${position.pnl_usd:.2f} "
                            f"(fee: ${okx_fee:.4f}, funding: ${okx_funding:.4f})"
                        )
                    else:
                        self._calculate_pnl(position)

                    logger.info(
                        f"[SYNC] {position.symbol}: Updated close price from exchange: "
                        f"${old_price:.4f} -> ${close_price:.4f} (PNL: ${position.pnl_usd:.2f})"
                    )
                    return True
            return False
        except Exception as e:
            logger.warning(f"[SYNC] {position.symbol}: Could not fetch close price from exchange: {e}")
            return False

    def close_position(self, position_id: str, reason: str = "MANUAL",
                       skip_exchange_close: bool = False):
        """
        Close a position completely

        Args:
            position_id: Position ID
            reason: Close reason
        """
        if position_id not in self.positions:
            logger.error(f"Position {position_id} not found")
            return

        position = self.positions[position_id]

        try:
            # Cancel CE stop-market order first (prevent ghost order on exchange)
            self._cancel_ce_stop_order(position)

            # Cancel TP/SL orders on exchange (prevent ghost orders)
            if self.mode == "live":
                self._cancel_tp_sl_orders(position)

            # Close remaining size on exchange (skip in paper mode or if already closed by stop order)
            if self.mode == "live" and position.remaining_size > 0 and not skip_exchange_close:
                # Check if TP2 exchange order already filled (avoid double close)
                tp2_already_filled = False
                if reason == "TP2" and position.tp2_order_id:
                    try:
                        order_info = self.client.fetch_order(position.tp2_order_id, position.symbol)
                        if order_info.get('status') in ('closed', 'filled'):
                            tp2_already_filled = True
                            logger.info(
                                f"[TP-ORDER] {position.symbol}: TP2 order already filled on exchange, "
                                f"skipping duplicate close"
                            )
                    except Exception as e:
                        logger.warning(f"[TP-ORDER] {position.symbol}: Could not check TP2 order: {e}")

                if tp2_already_filled:
                    pass  # Exchange already closed by TP2 limit order
                else:
                    # Verify position still exists on exchange before sending close order
                    # (prevents opening NEW position if user already closed manually on OKX)
                    exchange_has_position = False
                    okx_position_base_size = 0.0
                    try:
                        exchange_positions = self.client.get_open_positions()
                        expected_side = 'long' if position.side == 'BUY' else 'short'
                        for ep in exchange_positions:
                            ep_symbol = ep.get('symbol', '').replace('/', '').replace(':USDT', '')
                            ep_side = (ep.get('side', '') or '').lower()
                            if ep_symbol == position.symbol and ep_side == expected_side:
                                exchange_has_position = True
                                # Capture OKX actual position size (contracts → base)
                                contracts = float(ep.get('contracts', 0) or 0)
                                contract_size = float(ep.get('contractSize', 0) or 0)
                                if contracts > 0 and contract_size > 0:
                                    okx_position_base_size = contracts * contract_size
                                break
                    except Exception as e:
                        logger.warning(f"[CLOSE] {position.symbol}: Could not verify exchange position: {e}")
                        exchange_has_position = True  # Assume exists if check fails

                    if exchange_has_position:
                        close_side = 'sell' if position.side == "BUY" else 'buy'

                        # Option 2: Last position for this symbol+side uses OKX actual size
                        # Prevents orphaned positions when bot tracks multiple entries
                        # but OKX merges them into one position
                        close_amount = position.remaining_size
                        if okx_position_base_size > 0:
                            other_open = [
                                p for p in self.positions.values()
                                if p.position_id != position_id
                                and p.symbol == position.symbol
                                and p.side == position.side
                                and p.status in ("OPEN", "PARTIAL_CLOSE")
                            ]
                            if not other_open:
                                # Last bot position — use OKX size to close everything
                                if abs(okx_position_base_size - close_amount) / max(close_amount, 1e-8) > 0.01:
                                    logger.info(
                                        f"[CLOSE] {position.symbol}: Last position for {position.side} — "
                                        f"using OKX size {okx_position_base_size:.6f} "
                                        f"instead of bot size {close_amount:.6f}"
                                    )
                                close_amount = okx_position_base_size

                        close_order = self.client.close_position(
                            symbol=position.symbol,
                            side=close_side,
                            amount=close_amount
                        )
                        # Extract fill price from order response (for PNL calc if sync fails)
                        if close_order and isinstance(close_order, dict):
                            fill_price = float(close_order.get('average', 0) or close_order.get('price', 0) or 0)
                            # ccxt often returns average=0 for market orders, fetch order to get fill
                            if fill_price <= 0:
                                order_id = close_order.get('id', '')
                                if order_id:
                                    try:
                                        time.sleep(0.5)
                                        fetched = self.client.fetch_order(order_id, position.symbol)
                                        fill_price = float(fetched.get('average', 0) or fetched.get('price', 0) or 0)
                                    except Exception as e:
                                        logger.warning(f"[CLOSE] {position.symbol}: Could not fetch order fill: {e}")
                            if fill_price > 0:
                                position._close_fill_price = fill_price
                                logger.info(
                                    f"[CLOSE] {position.symbol}: Order fill price: {fill_price:.6f}"
                                )
                    else:
                        logger.warning(
                            f"[CLOSE] {position.symbol}: Position not found on exchange "
                            f"(already closed manually?), skipping close order"
                        )

            # Sync actual close price + PNL from exchange (live mode)
            # ALWAYS sync OKX PNL for ALL close reasons — OKX is ground truth
            # Bot-calculated PNL is only fallback when sync fails or paper mode
            pnl_synced_from_exchange = False
            if self.mode == "live":
                time.sleep(1)
                pnl_synced_from_exchange = self._sync_close_price_from_exchange(position)

            if pnl_synced_from_exchange:
                # OKX PNL already set by _sync_close_price_from_exchange
                # Skip bot's PNL calculation — OKX is source of truth
                logger.info(
                    f"[CLOSE] {position.symbol}: Using OKX PNL: ${position.pnl_usd:.2f} "
                    f"(ROI: {position.roi_percent:.1f}%)"
                )
            else:
                # Bot calculates PNL (paper mode or OKX sync failed)
                # Use fill price from close order if available (much more accurate than mark price)
                fill_price = getattr(position, '_close_fill_price', 0)
                if fill_price > 0 and self.mode == "live":
                    logger.info(
                        f"[CLOSE] {position.symbol}: OKX sync failed (merged position still open), "
                        f"using order fill price {fill_price:.6f} for PNL calc"
                    )
                    position.current_price = fill_price
                    # Recalculate pnl_percent from fill price
                    if position.side == "BUY":
                        price_change_pct = ((fill_price - position.entry_price) / position.entry_price) * 100
                    else:
                        price_change_pct = ((position.entry_price - fill_price) / position.entry_price) * 100
                    position.pnl_percent = price_change_pct * position.leverage
                elif self.mode == "live":
                    logger.warning(
                        f"[CLOSE] {position.symbol}: OKX sync failed, no fill price available, "
                        f"using mark price for PNL calc — may differ from actual exchange PNL"
                    )

                remaining_ratio = position.remaining_size / position.size if position.size > 0 else 0
                remaining_position_value = position.margin * remaining_ratio * position.leverage

                # Use taker fee for stop-market orders (HARD_SL, CHANDELIER_SL), maker for others
                is_stop_order = reason in ("HARD_SL", "CHANDELIER_M5", "CHANDELIER_SL", "CHANDELIER_H1", "CHANDELIER_H4")
                fee_rate = FEES['taker'] if is_stop_order else FEES['maker']
                exit_fee = remaining_position_value * fee_rate

                # Calculate realized PNL for remaining portion (before fee)
                realized_pnl_before_fee = position.margin * remaining_ratio * (position.pnl_percent / 100)

                # Track exit fee
                position.total_exit_fees += exit_fee

                # Update paper balance: return remaining margin + PNL - fee
                if self.mode == "paper":
                    returned_margin = position.margin * remaining_ratio
                    net_return = returned_margin + realized_pnl_before_fee - exit_fee
                    self.paper_balance += net_return
                    logger.info(
                        f"{position.symbol}: Paper balance updated: +${net_return:,.2f} "
                        f"(margin: ${returned_margin:,.2f}, PNL: ${realized_pnl_before_fee:,.2f}, fee: ${exit_fee:.2f}) "
                        f"-> ${self.paper_balance:,.2f}"
                    )

                # Update PNL — calculate directly, NOT via _calculate_pnl()
                # _calculate_pnl would double-count: it adds exchange unrealized PNL
                # on top of realized_pnl, but realized_pnl already captures the same move
                position.realized_pnl += realized_pnl_before_fee
                total_fees = position.entry_fee + position.total_exit_fees
                position.pnl_usd = position.realized_pnl - total_fees
                position.roi_percent = (position.pnl_usd / position.margin * 100) if position.margin else 0

                # OKX merged position: sync failed → this was a reduce (OKX still open).
                # Track this PnL on remaining sibling positions so when the last one
                # syncs from OKX, it can subtract already-allocated PnL.
                if self.mode == "live":
                    for p in self.positions.values():
                        if (p.position_id != position_id
                                and p.symbol == position.symbol
                                and p.side == position.side
                                and p.status in ("OPEN", "PARTIAL_CLOSE")):
                            p.sibling_reduce_pnl += position.pnl_usd
                            logger.info(
                                f"[SYNC] {position.symbol}: Tracked reduce PnL ${position.pnl_usd:.2f} "
                                f"on sibling {p.entry_type} (total: ${p.sibling_reduce_pnl:.2f})"
                            )

            position.status = "CLOSED"
            position.close_reason = reason
            # Prefer fill price from close order over mark price
            fill_price = getattr(position, '_close_fill_price', 0)
            position.exit_price = fill_price if fill_price > 0 else position.current_price
            position.remaining_size = 0
            # Use OKX close time (UTC) when synced, fallback to local time
            okx_close_time = getattr(position, '_okx_close_time', '')
            if okx_close_time:
                # Convert UTC to local time (closed_trades stores local time)
                try:
                    from datetime import timezone as _tz
                    utc_dt = datetime.fromisoformat(okx_close_time)
                    if utc_dt.tzinfo is None:
                        utc_dt = utc_dt.replace(tzinfo=_tz.utc)
                    local_dt = utc_dt.astimezone()
                    position.close_time = local_dt.replace(tzinfo=None).isoformat()
                except (ValueError, TypeError):
                    position.close_time = datetime.now().isoformat()
            else:
                position.close_time = datetime.now().isoformat()

            # Clean up order sync tracking for closed position
            self._last_order_sync_ts.pop(position_id, None)
            self._order_sync_failures.pop(position_id, None)

            logger.info(
                f"{position.symbol}: Position closed at ${position.current_price:.2f} "
                f"(Reason: {reason}, Final PNL: ${position.pnl_usd:.2f}, ROI: {position.roi_percent:.2f}%)"
            )

            # Send Telegram notification (if telegram client is available)
            if hasattr(self, 'telegram') and self.telegram:
                try:
                    self.telegram.send_position_closed(position)
                except Exception as e:
                    logger.error(f"Failed to send position closed notification: {e}")

            # Persist closed trade to SQLite before removing from memory
            if self.trades_db:
                try:
                    self.trades_db.insert_closed_trade(self._position_to_dict(position))
                except Exception as e:
                    logger.error(f"[TradesDB] Failed to persist closed trade {position_id}: {e}")

            # Remove closed position from in-memory dict (only active positions stay)
            del self.positions[position_id]
            if position.symbol in self.symbol_positions:
                self.symbol_positions[position.symbol] = [
                    pid for pid in self.symbol_positions[position.symbol]
                    if pid != position_id
                ]

            # Save to file (now contains only active positions)
            self._save_positions()

            # Ghost position detection: check if OKX has an inverse position
            # that appeared due to trigger SL race condition
            if self.mode == "live" and not skip_exchange_close:
                self._check_and_close_ghost_position(
                    position.symbol, position.side, reason
                )

            # Log to database
            if self.db:
                try:
                    duration_hours = (datetime.now() - position.timestamp).total_seconds() / 3600
                    self.db.log_operation(
                        operation_name='close_position',
                        risk_score=0,
                        status='success',
                        meta_data={
                            'position_id': position_id,
                            'symbol': position.symbol,
                            'side': position.side,
                            'entry_price': position.entry_price,
                            'close_price': position.current_price,
                            'pnl_usd': position.pnl_usd,
                            'roi_percent': position.roi_percent,
                            'close_reason': reason,
                            'duration_hours': round(duration_hours, 2),
                            'leverage': position.leverage,
                            'margin': position.margin,
                        }
                    )
                except Exception as e:
                    logger.error(f"[DB] Error logging close_position: {e}")

        except Exception as e:
            logger.error(f"Error closing position {position_id}: {e}")

    def _check_and_close_ghost_position(
        self, symbol: str, closed_side: str, reason: str
    ):
        """
        Post-close guard: detect and kill ghost inverse positions on OKX.

        Race condition scenario:
          1. Trigger SL fires on OKX (BUY to close SHORT)
          2. Bot also detects SL tick → sends its own BUY (reduceOnly)
          3. One closes the SHORT, the other OPENS a new LONG

        This method checks OKX 1-2s after close_position() removes the
        position locally. If an inverse position exists, close it immediately.
        """
        try:
            time.sleep(1.5)  # Wait for OKX settlement

            exchange_positions = self.client.get_open_positions()
            for ep in exchange_positions:
                ep_symbol_raw = ep.get('symbol', '')
                ep_symbol = ep_symbol_raw.replace('/', '').replace(':USDT', '')
                if ep_symbol != symbol:
                    continue

                ep_side = ep.get('side', '').lower()  # 'long' or 'short'
                ep_contracts = float(ep.get('contracts', 0) or 0)
                if ep_contracts <= 0:
                    continue

                # Detect inverse: we closed a BUY (long) but OKX has a short,
                # or we closed a SELL (short) but OKX has a long
                closed_was_long = closed_side == "BUY"
                exchange_is_long = ep_side == "long"

                if closed_was_long and exchange_is_long:
                    continue  # Same side — might be another valid position
                if not closed_was_long and not exchange_is_long:
                    continue  # Same side

                # Also skip if we still track this position locally
                # (could be a different, legitimate position)
                already_tracked = any(
                    p.symbol == symbol and p.status in ("OPEN", "PARTIAL_CLOSE")
                    for p in self.positions.values()
                )
                if already_tracked:
                    logger.debug(
                        f"[GHOST] {symbol}: Found {ep_side} on OKX but "
                        f"it's tracked locally — not a ghost"
                    )
                    continue

                # ── Ghost detected! Close it immediately ──
                entry_px = float(ep.get('entryPrice', 0) or
                                 ep.get('info', {}).get('avgPx', 0) or 0)
                logger.critical(
                    f"[GHOST] {symbol}: Inverse {ep_side.upper()} detected "
                    f"on OKX after closing {closed_side} ({reason})! "
                    f"contracts={ep_contracts}, entry={entry_px:.4f} "
                    f"— closing immediately with reduceOnly"
                )

                # Close the ghost: sell to close long, buy to close short
                ghost_close_side = 'sell' if exchange_is_long else 'buy'
                contract_size = float(ep.get('contractSize', 1) or 1)
                ghost_amount = ep_contracts * contract_size

                self.client.close_position(
                    symbol=symbol,
                    side=ghost_close_side,
                    amount=ghost_amount,
                )
                logger.info(
                    f"[GHOST] {symbol}: Ghost {ep_side} closed successfully"
                )

                # Send Telegram alert
                if hasattr(self, 'telegram') and self.telegram:
                    try:
                        alert_msg = (
                            f"⚠️ <b>GHOST POSITION KILLED</b>\n\n"
                            f"Symbol: {symbol}\n"
                            f"Ghost side: {ep_side.upper()}\n"
                            f"Entry: ${entry_px:.4f}\n"
                            f"Contracts: {ep_contracts}\n"
                            f"Trigger: {closed_side} closed by {reason}\n\n"
                            f"🛡️ Auto-closed to prevent unintended exposure"
                        )
                        self.telegram.send_message(alert_msg)
                    except Exception as tg_err:
                        logger.warning(
                            f"[GHOST] {symbol}: Telegram alert failed: {tg_err}"
                        )
                break  # Only one ghost per symbol expected

        except Exception as e:
            logger.error(
                f"[GHOST] {symbol}: Ghost position check failed: {e}"
            )

    def get_open_positions(self) -> List[Position]:
        """
        Get all open positions

        Returns:
            List of open Position objects
        """
        return [
            p for p in self.positions.values()
            if p.status in ["OPEN", "PARTIAL_CLOSE"]
        ]

    def get_closed_positions(self) -> List[Position]:
        """Get all closed positions for history"""
        return [
            p for p in self.positions.values()
            if p.status == "CLOSED"
        ]

    def get_position(self, position_id: str) -> Optional[Position]:
        """
        Get position by ID

        Args:
            position_id: Position ID

        Returns:
            Position object or None
        """
        return self.positions.get(position_id)

    def get_symbol_positions(self, symbol: str) -> List[Position]:
        """
        Get all positions for a symbol

        Args:
            symbol: Trading pair

        Returns:
            List of Position objects
        """
        if symbol not in self.symbol_positions:
            return []

        return [
            self.positions[pid]
            for pid in self.symbol_positions[symbol]
            if pid in self.positions
        ]
