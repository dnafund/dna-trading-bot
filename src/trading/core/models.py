"""
Core dataclass models for the trading system.

All shared dataclasses live here to avoid circular imports
and duplication across modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd


@dataclass
class FuturesKline:
    """Kline (candlestick) data for futures"""
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class IndicatorValues:
    """Container for indicator values"""
    ema34: float
    ema89: float
    rsi: float
    current_price: float
    timestamp: pd.Timestamp
    resistance_levels: list = None
    support_levels: list = None


@dataclass
class DivergenceResult:
    """Result of RSI divergence detection"""
    has_divergence: bool
    divergence_type: Optional[str]  # "bearish", "bullish", "hidden_bearish", "hidden_bullish"
    timeframe: str                   # "H1" or "H4"
    description: str                 # Human-readable description
    price_swing_1: Optional[float] = None
    price_swing_2: Optional[float] = None
    rsi_swing_1: Optional[float] = None
    rsi_swing_2: Optional[float] = None
    time_swing_1: Optional[str] = None  # Timestamp of first swing point
    time_swing_2: Optional[str] = None  # Timestamp of second swing point
    blocks_direction: Optional[str] = None  # "BUY" or "SELL"


@dataclass
class RiskCalculation:
    """Risk calculation results"""
    position_size: float
    margin_required: float
    leverage: int
    risk_amount: float
    max_loss: float


@dataclass
class TradingSignal:
    """Trading signal with all details"""
    symbol: str
    signal_type: str  # "BUY" or "SELL"
    entry_price: float
    timestamp: datetime

    # Trend info (H4)
    h4_trend: str
    h4_ema34: float
    h4_ema89: float

    # Filter info (H1)
    h1_rsi: float

    # Entry info (M15)
    m15_ema34: float
    m15_ema89: float
    wick_ratio: float

    # Entry type: "standard_m15", "standard_h1", "standard_h4", "ema610_h1", "ema610_h4"
    entry_type: str = "standard_m15"

    # Targets
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None  # ATR-based (standard) or ROI-based (EMA610)
    take_profit_2: Optional[float] = None  # ATR-based (standard) or ROI-based (EMA610)

    # Risk info
    leverage: int = 5
    position_size: float = 0.0


@dataclass
class Position:
    """Represents an open futures position"""
    position_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    entry_price: float
    size: float  # Position size in base currency (e.g., BTC amount)
    leverage: int
    margin: float  # Margin used (USDT)

    timestamp: datetime = field(default_factory=datetime.now)

    # Entry type: "standard_m15", "standard_h1", "standard_h4", "ema610_h1", "ema610_h4"
    entry_type: str = "standard_m15"

    # Targets
    stop_loss: Optional[float] = None       # Hard SL (ROI-based safety net)
    trailing_sl: Optional[float] = None     # Trailing SL (Chandelier Exit)
    chandelier_sl: Optional[float] = None   # Chandelier Exit value (tracks best level)
    take_profit_1: Optional[float] = None   # TP1 price (ATR-based for standard, ROI-based for EMA610)
    take_profit_2: Optional[float] = None   # TP2 price (ATR-based for standard, ROI-based for EMA610)

    # Current state
    current_price: float = 0.0
    exit_price: float = 0.0
    pnl_usd: float = 0.0
    pnl_percent: float = 0.0
    roi_percent: float = 0.0  # ROI with leverage

    # Partial closes
    tp1_closed: bool = False
    tp2_closed: bool = False
    tp1_cancelled: bool = False  # True = auto TP1 disabled (position stays open)
    tp2_cancelled: bool = False  # True = auto TP2 disabled (position stays open)
    remaining_size: float = 0.0
    realized_pnl: float = 0.0  # PNL already locked in from partial closes

    # Fees tracking
    entry_fee: float = 0.0  # Entry fee paid (maker 0.02%)
    total_exit_fees: float = 0.0  # Accumulated exit fees from partial closes

    # Status
    status: str = "OPEN"  # "OPEN", "PARTIAL_CLOSE", "CLOSED"
    close_reason: Optional[str] = None

    # CE grace period: skip Chandelier SL until enough time has passed after entry
    # Prevents instant SL when historical CE band is already breached at entry
    ce_armed: bool = False
    entry_candle_ts: Optional[str] = None  # Candle timestamp when position was opened
    entry_time: Optional[str] = None  # ISO timestamp when position was actually opened (wall clock)

    # Linear tracking
    linear_issue_id: Optional[str] = None

    # M15 close price for Chandelier SL trigger (persisted across restarts)
    last_m15_close: Optional[float] = None

    # CE price validation: True once price has been on the "correct side" of CE
    # Prevents instant trigger when CE is first set but price already breached
    ce_price_validated: bool = False

    # Exchange order IDs for TP/SL (live mode only)
    tp1_order_id: Optional[str] = None      # TP1 trigger-limit order on exchange
    tp2_order_id: Optional[str] = None      # TP2 trigger-limit order on exchange
    hard_sl_order_id: Optional[str] = None  # Hard SL stop-market order on exchange

    # Time tracking
    close_time: Optional[str] = None  # ISO timestamp when position was closed

    # OKX merged position tracking: when a sibling position closes via reduce
    # (OKX sync fails because merged position still open), its bot-calculated
    # PnL is accumulated here so the last position can subtract it from OKX total.
    sibling_reduce_pnl: float = 0.0

    def __post_init__(self):
        # Only set remaining_size if not explicitly provided (default 0.0 means new position)
        if self.remaining_size == 0.0:
            self.remaining_size = self.size
