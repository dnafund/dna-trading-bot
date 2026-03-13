"""
Futures Trading Configuration
"""

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Path to overrides file (relative to project root)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_OVERRIDES_FILE = _PROJECT_ROOT / "data" / "config_overrides.json"

# Thread safety for config mutations + file I/O
_config_lock = threading.Lock()

# Exchange selection
EXCHANGE = "okx"  # "okx" or "binance"

# Timezone offset (hours from UTC) — used for daily PNL boundaries, charts
# Must match your OKX account timezone so dashboard dates align with Trading Calendar
TIMEZONE_OFFSET = 7  # UTC+7 (Vietnam)

# Leverage configuration
# Higher leverage for high-liquidity coins, lower for altcoins
LEVERAGE: Dict[str, int] = {
    # Tier 1: Major coins (20x)
    "BTCUSDT": 20,

    # Tier 2: Large caps (10x)
    "ETHUSDT": 10,
    "SOLUSDT": 10,
    "XRPUSDT": 10,
    "BNBUSDT": 10,

    # Tier 3: Mid caps (7x)
    "DOGEUSDT": 7,
    "ADAUSDT": 7,
    "LINKUSDT": 7,
    "AVAXUSDT": 7,
    "DOTUSDT": 7,
    "MATICUSDT": 7,
    "UNIUSDT": 7,
    "ATOMUSDT": 7,
    "LTCUSDT": 7,
    "ETCUSDT": 7,
    "FILUSDT": 7,
    "APTUSDT": 7,
    "ARBUSDT": 7,
    "OPUSDT": 7,
    "NEARUSDT": 7,

    # Tier 4: Volatile altcoins (5x)
    "INJUSDT": 5,
    "SEIUSDT": 5,
    "SUIUSDT": 5,
    "RNDRUSDT": 5,
    "WLDUSDT": 5,
    "PEPEUSDT": 5,
    "TIAUSDT": 5,
    "JUPUSDT": 5,
    "HYPEUSDT": 5,
    "XAUUSDT": 30,

    # Default for all others (5x)
    "default": 5
}

# Trading fees (Binance Futures standard rates)
FEES = {
    "maker": 0.0002,  # 0.02% for limit orders (entry, TP, trailing SL)
    "taker": 0.0005,  # 0.05% for market/stop orders (hard SL)
}

# Risk management
RISK_MANAGEMENT = {
    "fixed_margin": 50,          # Fixed $50 margin per trade (OKX live)
    "hard_sl_percent": 20,       # -20% ROI hard SL (safety net, trailing SL usually exits first)
    "max_positions_per_pair": 1, # Maximum 1 concurrent position per symbol (per entry_type, default)
    "max_ema610_h1_positions": 0, # EMA610 H1: 0 = unlimited (only candle dedup limits)
    "ema610_margin_multiplier": 1, # EMA610 H1: margin x1 ($50, same as standard)
    "ema610_h4_margin_multiplier": 1, # EMA610 H4: margin x1 ($50, same as standard)
    "max_total_positions": 20,   # Maximum total active positions across all symbols
    "min_balance_to_trade": 50,  # Minimum $50 balance required to open new positions
    "initial_deposit": 300,      # Initial deposit for growth % calculation
    "max_equity_usage_pct": 50,  # Max 50% of total equity can be in active margin
}

# Exit strategy V8: ROI-based TP + Chandelier Exit SL + Smart SL
# TP: Based on ROI % (accounts for leverage automatically)
# SL: Chandelier Exit M15 (trailing) + Smart volume breathing + Hard SL -20% ROI
TAKE_PROFIT = {
    "tp1_percent": 70,        # Close 70% volume at TP1
    "tp2_percent": 30,        # Close 30% volume at TP2 (remaining)
    "tp1_roi": 20,            # TP1 at +20% ROI
    "tp2_roi": 40,            # TP2 at +40% ROI
}

# Trailing SL configuration (V7: Chandelier Exit replaces EMA89)
TRAILING_SL = {
    "enabled": True,
    "ema_period": 89,          # EMA89 on M15 (legacy, kept for backward compat)
    "timeframe": "15m",        # M15 timeframe
}

# Chandelier Exit configuration (V7.4.7 - Feb 2026)
# Close-price trigger (not intra-candle high/low) to avoid wick false triggers
CHANDELIER_EXIT = {
    "enabled": True,
    "period": 34,              # Longer lookback = more breathing room
    "multiplier": 1.75,        # ATR multiplier for trailing stop
}

# Smart SL: Volume breathing room
SMART_SL = {
    "enabled": True,
    "volume_avg_period": 21,       # Average volume over 21 candles
    "volume_threshold_pct": 80,    # If volume <= 80% of avg, allow breathing room
    "ema_safety_period": 200,      # EMA200 M15 for hard safety check
    "hard_sl_on_ema_break": True,  # If price closes beyond EMA200 → SL immediately
}

# Standard exit config: per-timeframe TP/SL for standard entries
STANDARD_EXIT = {
    'm5': {
        'tp1_roi': 20,         # TP1 at +20% ROI
        'tp2_roi': 40,         # TP2 at +40% ROI
        'hard_sl_roi': 20,     # Hard SL at -20% ROI
        'tp1_percent': 70,     # Close 70% at TP1
    },
    'm15': {
        'tp1_roi': 20,         # TP1 at +20% ROI
        'tp2_roi': 40,         # TP2 at +40% ROI
        'hard_sl_roi': 20,     # Hard SL at -20% ROI
        'tp1_percent': 70,     # Close 70% at TP1
    },
    'h1': {
        'tp1_roi': 30,         # TP1 at +30% ROI
        'tp2_roi': 60,         # TP2 at +60% ROI
        'hard_sl_roi': 25,     # Hard SL at -25% ROI
        'tp1_percent': 70,     # Close 70% at TP1
    },
    'h4': {
        'tp1_roi': 50,         # TP1 at +50% ROI
        'tp2_roi': 100,        # TP2 at +100% ROI
        'hard_sl_roi': 40,     # Hard SL at -40% ROI
        'tp1_percent': 70,     # Close 70% at TP1
    },
}

# EMA610 exit config: TP1 (partial close) + Chandelier trailing + Hard SL
EMA610_EXIT = {
    'h1': {
        'tp1_roi': 40,         # TP1 at +40% ROI
        'tp2_roi': 80,         # TP2 at +80% ROI
        'hard_sl_roi': 30,     # Hard SL at -30% ROI
        'tp1_percent': 50,     # Close 50% at TP1
    },
    'h4': {
        'tp1_roi': 60,         # TP1 at +60% ROI
        'tp2_roi': 120,        # TP2 at +120% ROI
        'hard_sl_roi': 50,     # Hard SL at -50% ROI
        'tp1_percent': 50,     # Close 50% at TP1
    },
}

# EMA610 instant entry (bypasses all rules, 1 position at a time)
# V7.4 OPTIMIZED: Tolerance 0.2% → 0.5% (+61.4% PNL improvement)
# Grid search 7 configs: 0.1%-0.5%, best = 0.5%
# Results: EMA610_H1 $687k → $1.1M (+$422k), 581 → 1,005 trades
EMA610_ENTRY = {
    "enabled": True,
    "period": 610,              # EMA 610 on H1
    "tolerance": 0.005,         # ±0.5% touch zone / EMA610 change threshold for limit re-place
    "timeframe": "1h",          # H1 timeframe
    "use_limit_orders": True,   # True = pre-place limit orders at EMA610, False = legacy market scan
    "max_distance_pct": 0.04,   # Max price-to-EMA610 distance (4%) to place limit order
    "manual_cancel_cooldown": 28800,  # 8h cooldown after manual cancel from web (seconds)
}

# Candlestick pattern instant entry (bypasses all rules, 1 position at a time)
PATTERN_ENTRY = {
    "enabled": True,
    "shooting_star_upper_wick_min": 60,  # Min upper wick % for Shooting Star
    "shooting_star_lower_wick_max": 15,  # Max lower wick % for Shooting Star
    "hammer_lower_wick_min": 60,         # Min lower wick % for Hammer
    "hammer_upper_wick_max": 15,         # Max upper wick % for Hammer
    "evening_star_body_c1_min": 50,      # Min body % for Evening Star C1
    "evening_star_body_c2_max": 30,      # Max body % for Evening Star C2
    "evening_star_body_c3_min": 50,      # Min body % for Evening Star C3
    "morning_star_body_c1_min": 50,      # Min body % for Morning Star C1
    "morning_star_body_c2_max": 30,      # Max body % for Morning Star C2
    "morning_star_body_c3_min": 50,      # Min body % for Morning Star C3
}

# Technical indicators
INDICATORS = {
    "ema_fast": 34,
    "ema_slow": 89,
    "rsi_period": 14,
    "wick_threshold": 40,  # Minimum wick % for entry confirmation
}

# Timeframes
TIMEFRAMES = {
    "trend": "4h",     # H4 for trend detection
    "filter": "1h",    # H1 for RSI filter
    "entry": "15m",    # M15 for entry signals
}

# Standard Entry (per-timeframe settings)
STANDARD_ENTRY = {
    "m5": {"enabled": True, "tolerance": 0.002},    # M5: 0.2% tolerance
    "m15": {"enabled": True, "tolerance": 0.002},   # M15: 0.2% tolerance
    "h1": {"enabled": True, "tolerance": 0.003},    # H1: 0.3% tolerance (bigger candles)
    "h4": {"enabled": True, "tolerance": 0.005},    # H4: 0.5% tolerance (biggest candles)
}

# Entry conditions (global settings)
ENTRY = {
    "wick_threshold": 40,          # Wick must be >= 40% of candle range (high-low) — note: bot uses INDICATORS['wick_threshold']
    "rsi_overbought": 70,
    "rsi_oversold": 30,
    "adx_threshold": 20,       # ADX H1 must be >= this to allow entries (0 = disabled)
    "adx_period": 14,          # ADX calculation period (Wilder's smoothing)
    "enable_ema610_h1": True,  # EMA610 H1 entries
    "enable_ema610_h4": True,  # EMA610 H4 entries
}

# Support/Resistance detection
SR_CONFIG = {
    "lookback_periods": 1000,       # H1 candles lookback (~41 days)
    "swing_threshold": 0.02,        # 2% minimum swing prominence to filter noise
    "cluster_threshold": 0.005,     # Merge levels within 0.5% of each other
    "min_touches": 2,               # Minimum touches to be a "strong" level
    "min_tp_distance_pct": 0.01,    # TP1 must be at least 1% from entry
}

# Supply & Demand Zones (standalone display, no entry/exit logic)
SD_ZONES_CONFIG = {
    "enabled": True,
    "atr_period": 200,              # ATR lookback for zone height
    "atr_multiplier": 2,            # Zone height = ATR * multiplier
    "vol_lookback": 1000,           # Rolling window for volume average
    "max_zones": 5,                 # Max zones per type per timeframe
    "cooldown_bars": 12,            # BigBeluga effective gap = 13 bars
    "timeframes": ["5m", "15m", "1h", "4h", "1d"],
    "candle_limits": {
        "5m": 3000,                 # ~10 days — need 1000 warm-up for vol avg
        "15m": 3000,                # ~31 days
        "1h": 3000,                 # ~125 days — fixes FIFO chain divergence
        "4h": 3000,                 # ~500 days
        "1d": 1500,                 # ~4 years — daily data limited
    },
    "scan_interval": 300,           # 5 minutes
    # Blocking: close opposite positions & block entries when price enters zone
    "blocking": {
        "enabled": True,
        # Which entry types to close when price enters a zone
        # Supply zone → close BUY positions with TF ≤ zone TF
        # Demand zone → close SELL positions with TF ≤ zone TF
        "close_entry_types": ["standard_", "ema610_"],  # prefixes to match
        # Which entry types to block (prevent new entries) while in zone
        "block_entry_types": ["standard_", "ema610_"],  # prefixes to match
        # Minimum zone TF for blocking (skip 5m zones — too noisy)
        "min_zone_tf": "15m",
    },
}

# SD Zone Entry Config (6 types: sd_demand_m15/h1/h4 + sd_supply_m15/h1/h4)
SD_ENTRY_CONFIG = {
    "enabled": True,
    "wick_ratio_min": 0.50,        # Wick must be >= 50% of candle range
    "rejection_candles": 2,         # 2 consecutive rejection candles needed
    "volume_multiplier": 1.2,      # Volume > 1.2x VolumeMA(20)
    "volume_ma_period": 20,        # VolumeMA lookback period
    "m15": {
        "enabled": True,
        "tp1_roi": 15,             # TP1 at +15% ROI
        "tp2_roi": 30,             # TP2 at +30% ROI
        "hard_sl_roi": 15,         # Hard SL at -15% ROI
        "tp1_percent": 70,         # Close 70% at TP1
    },
    "h1": {
        "enabled": True,
        "tp1_roi": 25,             # TP1 at +25% ROI
        "tp2_roi": 50,             # TP2 at +50% ROI
        "hard_sl_roi": 20,         # Hard SL at -20% ROI
        "tp1_percent": 70,         # Close 70% at TP1
    },
    "h4": {
        "enabled": True,
        "tp1_roi": 40,             # TP1 at +40% ROI
        "tp2_roi": 80,             # TP2 at +80% ROI
        "hard_sl_roi": 30,         # Hard SL at -30% ROI
        "tp1_percent": 70,         # Close 70% at TP1
    },
}

# RSI Divergence orders (entry + exit config per timeframe)
RSI_DIV_EXIT = {
    'm15': {
        'enabled': True,
        'tp1_roi': 15,           # TP1 at +15% ROI
        'tp2_roi': 30,           # TP2 at +30% ROI
        'hard_sl_roi': 15,       # Hard SL at -15% ROI
        'tp1_percent': 70,       # Close 70% at TP1
        'leverage_multiplier': 1.5,  # ceil(default_leverage * 1.5)
    },
    'h1': {
        'enabled': True,
        'tp1_roi': 25,
        'tp2_roi': 50,
        'hard_sl_roi': 20,
        'tp1_percent': 70,
        'leverage_multiplier': 1.5,
    },
    'h4': {
        'enabled': True,
        'tp1_roi': 40,
        'tp2_roi': 80,
        'hard_sl_roi': 30,
        'tp1_percent': 70,
        'leverage_multiplier': 1.5,
    },
}

# RSI Divergence detection
DIVERGENCE_CONFIG = {
    "enabled": True,
    # Entry blocking filter (H1 + H4 for trade symbols)
    "h1_lookback": 160,       # 160 H1 candles (~6.7 days)
    "h4_lookback": 80,        # 80 H4 candles (~13.3 days)
    "min_swing_distance": 8,  # Min bars between swing points (avoid noise)
    "swing_window": 3,        # Fractal window (7-bar pattern, clearer peaks)
    "max_swing_pairs": 3,     # Check last N swing pairs for divergence
    "min_retracement_pct": 1.5,  # Min % retracement between 2 swings (filter sideways noise)
    # M15 divergence scan (active trading pairs only)
    "m15_scan_enabled": True,     # Enable M15 divergence alerts
    "m15_lookback": 200,          # 200 M15 candles (~50 hours, better RSI warmup)
    "m15_div_cooldown_minutes": 15,   # M15: re-alert after 15m if NEW swing points
    "h1_div_cooldown_minutes": 60,    # H1: re-alert after 1h if NEW swing points
    "h4_div_cooldown_minutes": 240,   # H4: re-alert after 4h if NEW swing points
    # Independent divergence scan (H4 + D1, top market cap) — DISABLED
    "scan_top_n": 500,        # Scan top N symbols by market cap
    "scan_timeframes": ["4h", "1d"],  # H4 and D1
    "d1_lookback": 30,        # 30 D1 candles (~1 month)
    "scan_interval": 14400,   # Scan every 4 hours (seconds)
}

# Update intervals (seconds)
UPDATE_INTERVALS = {
    "market_data": 60,      # Update market data every 60s
    "position_check": 30,   # Check positions every 30s
}

# Dynamic pairs configuration
DYNAMIC_PAIRS = {
    "enabled": True,              # True = auto fetch top volume pairs
    "volume_windows": {
        "24h": 30,                # Scan top 30 pairs by 24h volume
        "48h": 0,                 # 0 = disabled
        "72h": 0,                 # 0 = disabled
    },
    "refresh_interval": 1800,     # Refresh pairs list every 30 minutes (seconds)
    # Whitelist: Empty = allow all (catch trending coins automatically)
    # Non-empty = only trade these. Leave empty to catch hot trends!
    "whitelist": [],
    # Blacklist: Never trade these (shitcoins, low liquidity, pump & dump)
    "blacklist": [
        "RIVERUSDT", "ALPACAUSDT", "PAXGUSDT",
    ],
}

# Fallback symbols (used when dynamic pairs disabled or API fails)
DEFAULT_SYMBOLS = [
    # Top 30 by 24h volume (updated 2026-02-05)
    # Tier 1: Major coins
    "BTCUSDT",
    "ETHUSDT",

    # Tier 2: Large caps
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",

    # Tier 3: High volume altcoins
    "LINKUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "MATICUSDT",
    "UNIUSDT",
    "ATOMUSDT",
    "LTCUSDT",
    "ETCUSDT",
    "FILUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "INJUSDT",
    "SEIUSDT",
    "SUIUSDT",
    "NEARUSDT",
    "RNDRUSDT",
    "WLDUSDT",
    "PEPEUSDT",
    "TIAUSDT",
    "JUPUSDT",
    "HYPEUSDT",
]


# ==========================================
# Config Override System
# ==========================================

# Store original defaults (deep copy) for reset functionality
_DEFAULTS = {
    "RISK_MANAGEMENT": copy.deepcopy(RISK_MANAGEMENT),
    "STANDARD_EXIT": copy.deepcopy(STANDARD_EXIT),
    "STANDARD_ENTRY": copy.deepcopy(STANDARD_ENTRY),
    "EMA610_EXIT": copy.deepcopy(EMA610_EXIT),
    "CHANDELIER_EXIT": copy.deepcopy(CHANDELIER_EXIT),
    "SMART_SL": copy.deepcopy(SMART_SL),
    "EMA610_ENTRY": copy.deepcopy(EMA610_ENTRY),
    "RSI_DIV_EXIT": copy.deepcopy(RSI_DIV_EXIT),
    "DIVERGENCE_CONFIG": copy.deepcopy(DIVERGENCE_CONFIG),
    "DYNAMIC_PAIRS": copy.deepcopy(DYNAMIC_PAIRS),
    "INDICATORS": copy.deepcopy(INDICATORS),
    "ENTRY": copy.deepcopy(ENTRY),
    "SD_ENTRY_CONFIG": copy.deepcopy(SD_ENTRY_CONFIG),
}

# Registry: section.key -> {type, min, max, label}
# Used for validation and Telegram UI display
CONFIG_PARAMS: Dict[str, Dict[str, Any]] = {
    # Risk Management
    "RISK_MANAGEMENT.fixed_margin": {"type": float, "min": 10, "max": 500, "label": "Margin/lệnh ($)"},
    "RISK_MANAGEMENT.max_total_positions": {"type": int, "min": 1, "max": 50, "label": "Max lệnh"},
    "RISK_MANAGEMENT.max_equity_usage_pct": {"type": float, "min": 10, "max": 100, "label": "Max equity (%)"},
    # Standard Exit M5
    "STANDARD_EXIT.m5.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "M5 TP1 ROI (%)"},
    "STANDARD_EXIT.m5.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "M5 TP2 ROI (%)"},
    "STANDARD_EXIT.m5.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "M5 Hard SL (%)"},
    "STANDARD_EXIT.m5.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "M5 TP1 size (%)"},
    # Standard Exit M15
    "STANDARD_EXIT.m15.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "M15 TP1 ROI (%)"},
    "STANDARD_EXIT.m15.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "M15 TP2 ROI (%)"},
    "STANDARD_EXIT.m15.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "M15 Hard SL (%)"},
    "STANDARD_EXIT.m15.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "M15 TP1 size (%)"},
    # Standard Exit H1
    "STANDARD_EXIT.h1.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "H1 TP1 ROI (%)"},
    "STANDARD_EXIT.h1.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "H1 TP2 ROI (%)"},
    "STANDARD_EXIT.h1.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "H1 Hard SL (%)"},
    "STANDARD_EXIT.h1.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "H1 TP1 size (%)"},
    # Standard Exit H4
    "STANDARD_EXIT.h4.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "H4 TP1 ROI (%)"},
    "STANDARD_EXIT.h4.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "H4 TP2 ROI (%)"},
    "STANDARD_EXIT.h4.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "H4 Hard SL (%)"},
    "STANDARD_EXIT.h4.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "H4 TP1 size (%)"},
    # EMA610 Exit H1
    "EMA610_EXIT.h1.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "EMA H1 TP1 ROI (%)"},
    "EMA610_EXIT.h1.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "EMA H1 TP2 ROI (%)"},
    "EMA610_EXIT.h1.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "EMA H1 Hard SL (%)"},
    "EMA610_EXIT.h1.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "EMA H1 TP1 size (%)"},
    # EMA610 Exit H4
    "EMA610_EXIT.h4.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "EMA H4 TP1 ROI (%)"},
    "EMA610_EXIT.h4.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "EMA H4 TP2 ROI (%)"},
    "EMA610_EXIT.h4.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "EMA H4 Hard SL (%)"},
    "EMA610_EXIT.h4.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "EMA H4 TP1 size (%)"},
    # Chandelier Exit
    "CHANDELIER_EXIT.enabled": {"type": bool, "label": "Chandelier Exit"},
    "CHANDELIER_EXIT.period": {"type": int, "min": 10, "max": 100, "label": "CE Period"},
    "CHANDELIER_EXIT.multiplier": {"type": float, "min": 0.5, "max": 5.0, "label": "CE Multiplier"},
    # Smart SL
    "SMART_SL.enabled": {"type": bool, "label": "Smart SL"},
    "SMART_SL.volume_threshold_pct": {"type": float, "min": 20, "max": 200, "label": "Vol threshold (%)"},
    "SMART_SL.ema_safety_period": {"type": int, "min": 50, "max": 500, "label": "EMA Safety period"},
    # EMA610 Entry
    "EMA610_ENTRY.enabled": {"type": bool, "label": "EMA610 Entry"},
    "EMA610_ENTRY.tolerance": {"type": float, "min": 0.001, "max": 0.02, "label": "Tolerance (decimal)"},
    "EMA610_ENTRY.use_limit_orders": {"type": bool, "label": "EMA610 Limit Orders"},
    "EMA610_ENTRY.max_distance_pct": {"type": float, "min": 0.01, "max": 0.20, "label": "Max Distance (%)"},
    # RSI Divergence Exit M15
    "RSI_DIV_EXIT.m15.enabled": {"type": bool, "label": "RSI Div M15"},
    "RSI_DIV_EXIT.m15.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "RSI M15 TP1 ROI (%)"},
    "RSI_DIV_EXIT.m15.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "RSI M15 TP2 ROI (%)"},
    "RSI_DIV_EXIT.m15.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "RSI M15 Hard SL (%)"},
    "RSI_DIV_EXIT.m15.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "RSI M15 TP1 size (%)"},
    "RSI_DIV_EXIT.m15.leverage_multiplier": {"type": float, "min": 1.0, "max": 3.0, "label": "RSI M15 Lev mult"},
    # RSI Divergence Exit H1
    "RSI_DIV_EXIT.h1.enabled": {"type": bool, "label": "RSI Div H1"},
    "RSI_DIV_EXIT.h1.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "RSI H1 TP1 ROI (%)"},
    "RSI_DIV_EXIT.h1.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "RSI H1 TP2 ROI (%)"},
    "RSI_DIV_EXIT.h1.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "RSI H1 Hard SL (%)"},
    "RSI_DIV_EXIT.h1.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "RSI H1 TP1 size (%)"},
    "RSI_DIV_EXIT.h1.leverage_multiplier": {"type": float, "min": 1.0, "max": 3.0, "label": "RSI H1 Lev mult"},
    # RSI Divergence Exit H4
    "RSI_DIV_EXIT.h4.enabled": {"type": bool, "label": "RSI Div H4"},
    "RSI_DIV_EXIT.h4.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "RSI H4 TP1 ROI (%)"},
    "RSI_DIV_EXIT.h4.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "RSI H4 TP2 ROI (%)"},
    "RSI_DIV_EXIT.h4.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "RSI H4 Hard SL (%)"},
    "RSI_DIV_EXIT.h4.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "RSI H4 TP1 size (%)"},
    "RSI_DIV_EXIT.h4.leverage_multiplier": {"type": float, "min": 1.0, "max": 3.0, "label": "RSI H4 Lev mult"},
    # Divergence
    "DIVERGENCE_CONFIG.enabled": {"type": bool, "label": "RSI Divergence"},
    "DIVERGENCE_CONFIG.scan_top_n": {"type": int, "min": 10, "max": 1000, "label": "Scan top N"},
    "DIVERGENCE_CONFIG.scan_interval": {"type": int, "min": 600, "max": 86400, "label": "Scan interval (s)"},
    # Dynamic Pairs
    "DYNAMIC_PAIRS.volume_windows.24h": {"type": int, "min": 5, "max": 100, "label": "Top N (24h vol)"},
    "DYNAMIC_PAIRS.refresh_interval": {"type": int, "min": 300, "max": 7200, "label": "Refresh interval (s)"},
    # Indicators
    "INDICATORS.wick_threshold": {"type": int, "min": 10, "max": 80, "label": "Wick threshold (%)"},
    # Standard Entry (per-timeframe)
    "STANDARD_ENTRY.m5.enabled": {"type": bool, "label": "M5 Enabled"},
    "STANDARD_ENTRY.m5.tolerance": {"type": float, "min": 0, "max": 0.02, "label": "M5 Tolerance"},
    "STANDARD_ENTRY.m15.enabled": {"type": bool, "label": "M15 Enabled"},
    "STANDARD_ENTRY.m15.tolerance": {"type": float, "min": 0.001, "max": 0.02, "label": "M15 Tolerance"},
    "STANDARD_ENTRY.h1.enabled": {"type": bool, "label": "H1 Enabled"},
    "STANDARD_ENTRY.h1.tolerance": {"type": float, "min": 0.001, "max": 0.02, "label": "H1 Tolerance"},
    "STANDARD_ENTRY.h4.enabled": {"type": bool, "label": "H4 Enabled"},
    "STANDARD_ENTRY.h4.tolerance": {"type": float, "min": 0.001, "max": 0.02, "label": "H4 Tolerance"},
    # SD Zone Entry
    "SD_ENTRY_CONFIG.enabled": {"type": bool, "label": "SD Entry"},
    "SD_ENTRY_CONFIG.wick_ratio_min": {"type": float, "min": 0.2, "max": 0.9, "label": "SD Wick Ratio"},
    "SD_ENTRY_CONFIG.volume_multiplier": {"type": float, "min": 0.5, "max": 3.0, "label": "SD Vol Mult"},
    "SD_ENTRY_CONFIG.m15.enabled": {"type": bool, "label": "SD M15"},
    "SD_ENTRY_CONFIG.m15.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "SD M15 TP1 ROI (%)"},
    "SD_ENTRY_CONFIG.m15.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "SD M15 TP2 ROI (%)"},
    "SD_ENTRY_CONFIG.m15.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "SD M15 Hard SL (%)"},
    "SD_ENTRY_CONFIG.m15.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "SD M15 TP1 size (%)"},
    "SD_ENTRY_CONFIG.h1.enabled": {"type": bool, "label": "SD H1"},
    "SD_ENTRY_CONFIG.h1.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "SD H1 TP1 ROI (%)"},
    "SD_ENTRY_CONFIG.h1.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "SD H1 TP2 ROI (%)"},
    "SD_ENTRY_CONFIG.h1.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "SD H1 Hard SL (%)"},
    "SD_ENTRY_CONFIG.h1.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "SD H1 TP1 size (%)"},
    "SD_ENTRY_CONFIG.h4.enabled": {"type": bool, "label": "SD H4"},
    "SD_ENTRY_CONFIG.h4.tp1_roi": {"type": float, "min": 5, "max": 200, "label": "SD H4 TP1 ROI (%)"},
    "SD_ENTRY_CONFIG.h4.tp2_roi": {"type": float, "min": 10, "max": 400, "label": "SD H4 TP2 ROI (%)"},
    "SD_ENTRY_CONFIG.h4.hard_sl_roi": {"type": float, "min": 5, "max": 100, "label": "SD H4 Hard SL (%)"},
    "SD_ENTRY_CONFIG.h4.tp1_percent": {"type": int, "min": 10, "max": 100, "label": "SD H4 TP1 size (%)"},
    # ADX Filter + EMA610 toggles
    "ENTRY.adx_threshold": {"type": float, "min": 0, "max": 50, "label": "ADX min (0=off)"},
    "ENTRY.enable_ema610_h1": {"type": bool, "label": "EMA610 H1"},
    "ENTRY.enable_ema610_h4": {"type": bool, "label": "EMA610 H4"},
}


def _get_config_dict(section: str) -> Optional[Dict]:
    """Get the live config dict for a section name."""
    section_map = {
        "RISK_MANAGEMENT": RISK_MANAGEMENT,
        "STANDARD_EXIT": STANDARD_EXIT,
        "STANDARD_ENTRY": STANDARD_ENTRY,
        "EMA610_EXIT": EMA610_EXIT,
        "CHANDELIER_EXIT": CHANDELIER_EXIT,
        "SMART_SL": SMART_SL,
        "EMA610_ENTRY": EMA610_ENTRY,
        "RSI_DIV_EXIT": RSI_DIV_EXIT,
        "DIVERGENCE_CONFIG": DIVERGENCE_CONFIG,
        "DYNAMIC_PAIRS": DYNAMIC_PAIRS,
        "INDICATORS": INDICATORS,
        "ENTRY": ENTRY,
        "SD_ENTRY_CONFIG": SD_ENTRY_CONFIG,
    }
    return section_map.get(section)


def _apply_override(full_key: str, value: Any) -> bool:
    """Apply a single override to the live config dict. Returns True on success."""
    parts = full_key.split(".")
    if len(parts) < 2:
        return False

    section = parts[0]
    config_dict = _get_config_dict(section)
    if config_dict is None:
        return False

    # Navigate nested dicts: e.g. STANDARD_EXIT.h1.tp1_roi
    target = config_dict
    for part in parts[1:-1]:
        if isinstance(target, dict) and part in target:
            target = target[part]
        else:
            return False

    final_key = parts[-1]
    if isinstance(target, dict):
        target[final_key] = value
        return True
    return False


def get_config_value(full_key: str) -> Any:
    """Get current live value for a dotted config key."""
    parts = full_key.split(".")
    if len(parts) < 2:
        return None

    section = parts[0]
    config_dict = _get_config_dict(section)
    if config_dict is None:
        return None

    target = config_dict
    for part in parts[1:]:
        if isinstance(target, dict) and part in target:
            target = target[part]
        else:
            return None
    return target


def load_overrides() -> int:
    """Load saved overrides from JSON file and apply to live config.

    Returns number of overrides applied.
    """
    if not CONFIG_OVERRIDES_FILE.exists():
        return 0

    try:
        with open(CONFIG_OVERRIDES_FILE, "r", encoding="utf-8") as f:
            overrides = json.load(f)

        applied = 0
        for full_key, value in overrides.items():
            # Validate key exists in registry
            if full_key not in CONFIG_PARAMS:
                logger.warning(f"[CONFIG] Skipping unknown override key: {full_key}")
                continue

            # Validate value range
            param = CONFIG_PARAMS[full_key]
            min_val = param.get("min")
            max_val = param.get("max")
            if min_val is not None and value < min_val:
                logger.warning(f"[CONFIG] Override {full_key}={value} below min {min_val}, skipping")
                continue
            if max_val is not None and value > max_val:
                logger.warning(f"[CONFIG] Override {full_key}={value} above max {max_val}, skipping")
                continue

            if _apply_override(full_key, value):
                applied += 1
            else:
                logger.warning(f"[CONFIG] Failed to apply override: {full_key}={value}")

        if applied > 0:
            logger.info(f"[CONFIG] Loaded {applied} overrides from {CONFIG_OVERRIDES_FILE.name}")
        return applied
    except Exception as e:
        logger.error(f"[CONFIG] Failed to load overrides: {e}")
        return 0


def save_override(full_key: str, value: Any) -> bool:
    """Save a single override to JSON and apply to live config.

    Args:
        full_key: Dotted key like "RISK_MANAGEMENT.fixed_margin"
        value: The new value (already validated/cast)

    Returns True on success.
    """
    with _config_lock:
        try:
            # Apply to live config first
            if not _apply_override(full_key, value):
                return False

            # Load existing overrides
            overrides = {}
            if CONFIG_OVERRIDES_FILE.exists():
                with open(CONFIG_OVERRIDES_FILE, "r", encoding="utf-8") as f:
                    overrides = json.load(f)

            # Update
            overrides[full_key] = value

            # Atomic write: write to temp file then rename
            os.makedirs(CONFIG_OVERRIDES_FILE.parent, exist_ok=True)
            tmp_file = CONFIG_OVERRIDES_FILE.with_suffix(".tmp")
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(overrides, f, indent=2, ensure_ascii=False)
            tmp_file.replace(CONFIG_OVERRIDES_FILE)

            logger.info(f"[CONFIG] Saved override: {full_key} = {value}")
            return True
        except Exception as e:
            logger.error(f"[CONFIG] Failed to save override: {e}")
            return False


def reset_overrides() -> bool:
    """Delete overrides file and revert live config to defaults."""
    with _config_lock:
        try:
            if CONFIG_OVERRIDES_FILE.exists():
                CONFIG_OVERRIDES_FILE.unlink()

            # Revert live config dicts to stored defaults
            for section_name, defaults in _DEFAULTS.items():
                config_dict = _get_config_dict(section_name)
                if config_dict is not None:
                    config_dict.clear()
                    config_dict.update(copy.deepcopy(defaults))

            logger.info("[CONFIG] Overrides reset. Live config reverted to defaults.")
            return True
        except Exception as e:
            logger.error(f"[CONFIG] Failed to reset overrides: {e}")
            return False


def get_all_overrides() -> Dict[str, Any]:
    """Read current overrides from file (for display)."""
    if not CONFIG_OVERRIDES_FILE.exists():
        return {}
    try:
        with open(CONFIG_OVERRIDES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def validate_config_value(full_key: str, raw_value: str) -> tuple:
    """Validate and cast a raw string value for a config param.

    Returns (success: bool, value_or_error: Any)
    """
    param = CONFIG_PARAMS.get(full_key)
    if not param:
        return False, f"Unknown param: {full_key}"

    param_type = param["type"]

    if param_type == bool:
        lower = raw_value.strip().lower()
        if lower in ("1", "true", "on", "yes", "bật"):
            return True, True
        if lower in ("0", "false", "off", "no", "tắt"):
            return True, False
        return False, "Nhập on/off hoặc true/false"

    try:
        value = param_type(raw_value.strip())
    except (ValueError, TypeError):
        type_name = "số nguyên" if param_type == int else "số thực"
        return False, f"Cần nhập {type_name}"

    min_val = param.get("min")
    max_val = param.get("max")
    if min_val is not None and value < min_val:
        return False, f"Tối thiểu: {min_val}"
    if max_val is not None and value > max_val:
        return False, f"Tối đa: {max_val}"

    return True, value
