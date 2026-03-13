"""Learning module configuration. Reads from environment variables."""

import os
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSITIONS_FILE = PROJECT_ROOT / "data" / "positions.json"
CONFIG_FILE = PROJECT_ROOT / "data" / "config.json"
CONFIG_OVERRIDES_FILE = PROJECT_ROOT / "data" / "config_overrides.json"
OHLCV_DIR = PROJECT_ROOT / "data" / "ohlcv"
LEARNING_DB = Path(__file__).parent / "db" / "learning.db"
OUTPUT_DIR = Path(__file__).parent / "output"

# ── LLM Configuration ───────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LEARNING_LLM_PROVIDER", "gemini")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL_CLAUDE = os.environ.get("LEARNING_LLM_MODEL", "claude-sonnet-4-20250514")
LLM_MODEL_GEMINI = os.environ.get("LEARNING_GEMINI_MODEL", "gemini-2.5-pro")

# ── Analysis Thresholds ─────────────────────────────────────────
MIN_TRADES_FOR_SUGGESTION = 30
MIN_CONFIDENCE = 0.6
MAX_PARAM_CHANGE_PCT = 0.20
BACKTEST_VALIDATION_DAYS = 90

# ── Known Values ─────────────────────────────────────────────────
ENTRY_TYPES = (
    "standard_m15",
    "standard_h1",
    "standard_h4",
    "ema610_h1",
    "ema610_h4",
)

CLOSE_REASONS = (
    "TP1",
    "TP2",
    "HARD_SL",
    "CHANDELIER_SL",
    "CHANDELIER_TRIGGER",
    "SMART_SL",
    "EMA200_BREAK",
    "EXTERNAL_CLOSE",
    "MANUAL_WEB",
    "MANUAL_BULK",
    "END_OF_BACKTEST",
)

# Note: WIN_REASONS by close_reason is unreliable since many close reasons
# (CHANDELIER_SL, EXTERNAL_CLOSE, MANUAL_WEB) can be either wins or losses.
# Prefer using `trade.pnl_usd > 0` for win/loss classification.
WIN_REASONS = {"TP1", "TP2"}
LOSS_REASONS = {"HARD_SL", "CHANDELIER_TRIGGER", "SMART_SL", "EMA200_BREAK"}
