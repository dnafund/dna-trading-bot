"""
Futures Trading Module -- backward-compat re-exports.

Canonical locations:
  FuturesTradingBot  -> src.trading.bot
  SignalDetector     -> src.trading.strategy.signal_detector
  PositionManager    -> src.trading.execution.position_manager
  RiskManager        -> src.trading.strategy.risk_manager
  TelegramCommandHandler -> src.trading.notifications.telegram_commands
"""

from src.trading.bot import FuturesTradingBot  # noqa: F401
from src.trading.strategy.signal_detector import SignalDetector  # noqa: F401
from src.trading.execution.position_manager import PositionManager  # noqa: F401
from src.trading.strategy.risk_manager import RiskManager  # noqa: F401
from src.trading.notifications.telegram_commands import TelegramCommandHandler  # noqa: F401

__all__ = [
    'FuturesTradingBot',
    'SignalDetector',
    'PositionManager',
    'RiskManager',
    'TelegramCommandHandler',
]
