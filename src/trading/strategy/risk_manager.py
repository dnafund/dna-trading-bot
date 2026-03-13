"""
Risk Manager for Futures Trading

Handles risk calculations and validations
"""

from typing import Optional
from dataclasses import dataclass
import logging

from src.trading.core.models import RiskCalculation
from src.trading.core.config import LEVERAGE, RISK_MANAGEMENT

logger = logging.getLogger(__name__)


class RiskManager:
    """
    Manage trading risk

    Rules:
    - Fixed margin per trade (default $50)
    - Max positions per pair (configurable)
    - Hard stop loss at configurable ROI %
    """

    @staticmethod
    def calculate_position_risk(
        symbol: str,
        account_balance: float,
        entry_price: float
    ) -> RiskCalculation:
        """
        Calculate position size and risk

        Args:
            symbol: Trading pair
            account_balance: Account balance in USDT
            entry_price: Entry price

        Returns:
            RiskCalculation object
        """
        # Get leverage
        leverage = LEVERAGE.get(symbol, LEVERAGE['default'])

        # Use fixed margin from config
        margin_required = RISK_MANAGEMENT.get('fixed_margin', 50)

        # Position value (with leverage)
        position_value = margin_required * leverage

        # Position size in base currency
        position_size = position_value / entry_price

        # Max loss (at -50% PNL)
        max_loss = margin_required * (RISK_MANAGEMENT.get('hard_sl_percent', 20) / 100)

        return RiskCalculation(
            position_size=position_size,
            margin_required=margin_required,
            leverage=leverage,
            risk_amount=margin_required,
            max_loss=max_loss
        )

    @staticmethod
    def validate_position(
        symbol: str,
        account_balance: float,
        open_positions_count: int
    ) -> tuple[bool, Optional[str]]:
        """
        Validate if can open position

        Args:
            symbol: Trading pair
            account_balance: Account balance in USDT
            open_positions_count: Number of open positions for this symbol

        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check max positions per pair
        max_positions = RISK_MANAGEMENT['max_positions_per_pair']
        if open_positions_count >= max_positions:
            return False, f"Max {max_positions} positions per symbol reached"

        # Check sufficient balance for fixed margin
        required_margin = RISK_MANAGEMENT.get('fixed_margin', 50)
        if account_balance < required_margin:
            return False, f"Insufficient balance: ${account_balance:.2f} < ${required_margin:.2f}"

        return True, None

    @staticmethod
    def calculate_stop_loss_price(
        entry_price: float,
        leverage: int,
        side: str
    ) -> float:
        """
        Calculate stop loss price for -50% PNL

        Args:
            entry_price: Entry price
            leverage: Leverage multiplier
            side: "BUY" or "SELL"

        Returns:
            Stop loss price
        """
        sl_percent = RISK_MANAGEMENT.get('hard_sl_percent', 20) / 100
        price_change_percent = sl_percent / leverage

        if side == "BUY":
            # For LONG: SL below entry
            sl_price = entry_price * (1 - price_change_percent)
        else:
            # For SHORT: SL above entry
            sl_price = entry_price * (1 + price_change_percent)

        return sl_price

    @staticmethod
    def calculate_liquidation_price(
        entry_price: float,
        leverage: int,
        side: str,
        maintenance_margin_rate: float = 0.004  # 0.4% for most futures
    ) -> float:
        """
        Calculate liquidation price

        Args:
            entry_price: Entry price
            leverage: Leverage multiplier
            side: "BUY" or "SELL"
            maintenance_margin_rate: Maintenance margin rate

        Returns:
            Liquidation price
        """
        # Simplified liquidation calculation
        # Actual formula varies by exchange

        if side == "BUY":
            # For LONG
            liq_price = entry_price * (1 - (1 / leverage) + maintenance_margin_rate)
        else:
            # For SHORT
            liq_price = entry_price * (1 + (1 / leverage) - maintenance_margin_rate)

        return liq_price

    @staticmethod
    def get_risk_level(pnl_percent: float) -> str:
        """
        Get risk level based on PNL

        Args:
            pnl_percent: PNL percentage

        Returns:
            Risk level: "LOW", "MEDIUM", "HIGH", "CRITICAL"
        """
        if pnl_percent >= 0:
            return "LOW"  # Profitable
        elif pnl_percent > -25:
            return "MEDIUM"  # Down but manageable
        elif pnl_percent > -40:
            return "HIGH"  # Approaching stop loss
        else:
            return "CRITICAL"  # Near or at stop loss

    @staticmethod
    def should_reduce_position(
        pnl_percent: float,
        leverage: int
    ) -> bool:
        """
        Check if should reduce position size

        Args:
            pnl_percent: Current PNL percentage
            leverage: Leverage used

        Returns:
            True if should reduce position
        """
        # If losing and high leverage, consider reducing
        if pnl_percent < -30 and leverage >= 10:
            return True

        return False
