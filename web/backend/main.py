"""
Web Dashboard API Server
FastAPI + WebSocket for real-time trading dashboard
"""

import asyncio
import json
import logging
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from pydantic import BaseModel

from web.backend.auth import (
    GOOGLE_CLIENT_ID,
    TokenPayload,
    authenticate_user,
    change_password,
    create_access_token,
    get_current_user,
    get_current_user_obj,
    require_admin,
    verify_google_token,
    verify_token,
)
from web.backend.command_writer import write_command
from web.backend.config_reader import ConfigReader
from web.backend.position_reader import PositionReader
from src.database.trades_db import TradesDB

logger = logging.getLogger(__name__)

# ── Coin Logo Cache ──────────────────────────────────────────────
# Fetches from Binance once, serves to frontend (avoids CORS issues)
_coin_logos: dict[str, str] = {}
_coin_logos_fetched = False


# ── Lifespan ─────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database connection
    try:
        from src.database.connection import init_db, close_db
        init_db()
        logger.info("Dashboard API started (DB connected)")
    except Exception as e:
        logger.warning(f"Dashboard API started (DB unavailable: {e})")

    # Pre-warm OKX caches in background so first WebSocket connect is instant
    async def _warmup():
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _fetch_exchange_data)
            await loop.run_in_executor(None, _fetch_position_history)
            await loop.run_in_executor(None, _fetch_daily_pnl)
            await loop.run_in_executor(None, _fetch_bills_summary, 365)
            await loop.run_in_executor(None, _fetch_bills_summary, 30)
            logger.info("[STARTUP] OKX caches pre-warmed (history + daily PNL + bills)")
        except Exception as e:
            logger.warning(f"[STARTUP] Cache warmup failed (non-fatal): {e}")

    asyncio.create_task(_warmup())

    yield
    try:
        from src.database.connection import close_db
        close_db()
    except Exception:
        pass
    logger.info("Dashboard API shutting down")


app = FastAPI(title="Trading Dashboard API", version="2.0.0", lifespan=lifespan)

# ── Middleware (order matters: outermost first) ───────────────────

# Error handler (outermost — catches everything)
try:
    from web.backend.middleware.error_handler import ErrorHandlerMiddleware, init_sentry
    app.add_middleware(ErrorHandlerMiddleware)
    init_sentry()
except ImportError:
    logger.warning("Error handler middleware not available")

# Request logging
try:
    from web.backend.middleware.request_logger import RequestLoggerMiddleware
    app.add_middleware(RequestLoggerMiddleware)
except ImportError:
    logger.warning("Request logger middleware not available")

# Rate limiting
try:
    from web.backend.middleware.rate_limiter import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
except ImportError:
    logger.warning("Rate limiter middleware not available")

# CORS for frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Mount API Routes ──────────────────────────────────────────────

# Health checks (no auth required)
try:
    from web.backend.api.health import router as health_router
    app.include_router(health_router)
except ImportError as e:
    logger.warning(f"Health check routes not available: {e}")

# Multi-tenant API routes
try:
    from web.backend.api.users import router as users_router
    app.include_router(users_router)
except ImportError as e:
    logger.warning(f"User API routes not available: {e}")

try:
    from web.backend.api.billing import router as billing_router
    app.include_router(billing_router)
except ImportError as e:
    logger.warning(f"Billing API routes not available: {e}")

try:
    from web.backend.api.admin import router as admin_router
    app.include_router(admin_router)
except ImportError as e:
    logger.warning(f"Admin API routes not available: {e}")

try:
    from web.backend.api.backtest import router as backtest_router
    app.include_router(backtest_router)
except ImportError as e:
    logger.warning(f"Backtest API routes not available: {e}")

# Data readers
DATA_DIR = PROJECT_ROOT / "data"
trades_db = TradesDB(DATA_DIR / "trades.db")
position_reader = PositionReader(DATA_DIR / "positions.json", trades_db=trades_db)
config_reader = ConfigReader()

# Auto-migrate closed trades from positions.json → SQLite on first run
try:
    if trades_db.get_closed_count() == 0:
        _positions_file = DATA_DIR / "positions.json"
        if _positions_file.exists():
            with open(_positions_file, "r", encoding="utf-8") as _f:
                _raw = json.load(_f)
            _closed = [
                v for k, v in _raw.items()
                if isinstance(v, dict) and v.get("status") == "CLOSED"
            ]
            if _closed:
                _inserted = trades_db.insert_closed_trades_batch(_closed)
                logger.info(f"[AUTO-MIGRATE] Seeded {_inserted} closed trades from positions.json → SQLite")
except Exception as _e:
    logger.warning(f"[AUTO-MIGRATE] Failed (non-fatal): {_e}")

# ── Exchange client (lazy-init for live balance/PNL) ─────────────
_exchange_client = None
_exchange_pnl_cache: dict = {}
_exchange_balance_cache: float = 0.0
_exchange_cache_ts: float = 0.0
EXCHANGE_CACHE_TTL = 5.0  # seconds

# Separate cache for position history (less frequent updates)
_exchange_history_cache: list = []
_exchange_history_ts: float = 0.0
EXCHANGE_HISTORY_TTL = 10.0  # seconds

# Cache for bills-based daily PNL (matches OKX Trading Calendar)
_daily_pnl_cache: list = []
_daily_pnl_ts: float = 0.0
DAILY_PNL_TTL = 300.0  # 5 minutes — bills data is stable

# Bills-based PnL summary cache (keyed by period days, per-key TTL)
_bills_summary_cache: dict = {}  # {days: summary}
_bills_summary_ts: dict = {}    # {days: timestamp} — per-key, NOT shared
BILLS_SUMMARY_TTL = 60.0  # 1 min — keep data fresh

INITIAL_DEPOSIT = 300.0  # Initial deposit for growth % calculation


def _filter_history_by_period(history: list[dict], period: str) -> list[dict]:
    """Filter position history by time period (24h, 7d, 30d, all)."""
    if period == "all" or not history:
        return history

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    deltas = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
    cutoff = now - deltas.get(period, timedelta(days=36500))

    filtered = []
    for h in history:
        ct = h.get("close_time", "")
        if not ct:
            continue
        try:
            trade_dt = datetime.fromisoformat(ct.replace("Z", "+00:00"))
            if trade_dt >= cutoff:
                filtered.append(h)
        except (ValueError, TypeError):
            continue
    return filtered


def _get_exchange_client():
    """Lazy-init OKX exchange client for dashboard balance/PNL queries."""
    global _exchange_client
    if _exchange_client is not None:
        return _exchange_client
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env")
        api_key = os.getenv("OKX_API_KEY")
        api_secret = os.getenv("OKX_API_SECRET")
        passphrase = os.getenv("OKX_PASSPHRASE")
        if api_key and api_secret and passphrase:
            from src.trading.futures.okx_futures import OKXFuturesClient
            _exchange_client = OKXFuturesClient(api_key, api_secret, passphrase)
            logger.info("[DASHBOARD] OKX exchange client initialized for live data")
        else:
            logger.warning("[DASHBOARD] OKX credentials not found, using paper data only")
    except Exception as e:
        logger.error(f"[DASHBOARD] Failed to init exchange client: {e}")
    return _exchange_client


def _fetch_exchange_data() -> tuple[float, dict]:
    """Fetch balance + positions PNL from OKX, with TTL cache."""
    import time
    global _exchange_pnl_cache, _exchange_balance_cache, _exchange_cache_ts

    now = time.time()
    if now - _exchange_cache_ts < EXCHANGE_CACHE_TTL:
        return _exchange_balance_cache, _exchange_pnl_cache

    client = _get_exchange_client()
    if not client:
        return 0.0, {}

    try:
        balance_data = client.get_account_balance()
        balance = float(balance_data.get("USDT", 0) or 0)
        pnl_data = client.get_positions_pnl()
        _exchange_balance_cache = balance
        _exchange_pnl_cache = pnl_data
        _exchange_cache_ts = now
        return balance, pnl_data
    except Exception as e:
        logger.warning(f"[DASHBOARD] Exchange data fetch failed: {e}")
        return _exchange_balance_cache, _exchange_pnl_cache


def _fetch_bills_summary(days: int) -> dict:
    """Fetch PnL summary from OKX bills endpoints, with per-key TTL cache."""
    import time
    global _bills_summary_cache, _bills_summary_ts

    now = time.time()
    key_ts = _bills_summary_ts.get(days, 0.0)
    if now - key_ts < BILLS_SUMMARY_TTL and days in _bills_summary_cache:
        return _bills_summary_cache[days]

    client = _get_exchange_client()
    if not client:
        return {}

    try:
        summary = client.get_pnl_summary(days=days)
        _bills_summary_cache[days] = summary
        _bills_summary_ts[days] = now
        return summary
    except Exception as e:
        logger.warning(f"[DASHBOARD] Bills summary fetch failed: {e}")
        return _bills_summary_cache.get(days, {})


# Backfill cutoff: exclude positions.json trades closed on or before this time
# (early trades had wrong position sizes, causing incorrect PNL)
BACKFILL_CUTOFF = "2026-02-17T15:10:00"

# Performance Analysis reset: only show trades closed AFTER this date in the
# analysis breakdown (By Strategy/Exit/Symbol). Does NOT affect Total PNL,
# win rate, trade count, or history — those always use all trades.
ANALYSIS_RESET_DATE = "2026-02-25T00:00:00"


def _merge_history(fresh: list[dict], cached: list[dict]) -> list[dict]:
    """Merge fresh OKX records into cached history, dedup by pos_id or symbol+open_time+side."""
    seen: set[str] = set()
    merged: list[dict] = []

    def _key(record: dict) -> str:
        # Always use symbol+open_time+side for uniqueness.
        # pos_id alone is NOT unique: OKX reuses the same pos_id when a position
        # is closed and reopened (e.g., XAU merged trades).
        return f"{record.get('symbol', '')}|{record.get('open_time', '')}|{record.get('side', '')}"

    # Fresh records take priority (newer data from OKX)
    for r in fresh:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            merged.append(r)

    # Add cached records not already in fresh
    for r in cached:
        k = _key(r)
        if k not in seen:
            seen.add(k)
            merged.append(r)

    # Sort by close_time descending (newest first)
    merged.sort(key=lambda r: r.get("close_time", ""), reverse=True)
    return merged


OKX_HISTORY_CACHE_FILE = DATA_DIR / "okx_history_cache.json"


def _load_json_history_fallback() -> list[dict]:
    """Fallback: load OKX history from legacy JSON file if SQLite is empty."""
    try:
        if OKX_HISTORY_CACHE_FILE.exists():
            with open(OKX_HISTORY_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    logger.info(f"[CACHE] Loaded {len(data)} records from JSON fallback")
                    # Migrate to SQLite on-the-fly
                    trades_db.upsert_okx_history(data)
                    return data
    except Exception as e:
        logger.warning(f"[CACHE] JSON fallback failed: {e}")
    return []


def _fetch_position_history() -> list[dict]:
    """Fetch closed position history from OKX, merged with SQLite cache."""
    import time
    global _exchange_history_cache, _exchange_history_ts

    now = time.time()
    if now - _exchange_history_ts < EXCHANGE_HISTORY_TTL:
        return _exchange_history_cache

    cached = trades_db.load_okx_history()
    # Fallback to JSON file if SQLite empty (pre-migration)
    if not cached:
        cached = _load_json_history_fallback()

    client = _get_exchange_client()
    if not client:
        _exchange_history_cache = cached
        return cached

    try:
        fresh = client.get_position_history()  # max 100 from OKX
        merged = _merge_history(fresh, cached)
        trades_db.upsert_okx_history(merged)
        _exchange_history_cache = merged
        _exchange_history_ts = now
        return merged
    except Exception as e:
        logger.warning(f"[DASHBOARD] Position history fetch failed: {e}")
        if cached and not _exchange_history_cache:
            _exchange_history_cache = cached
        return _exchange_history_cache or cached


def _fetch_daily_pnl() -> list[dict]:
    """Fetch daily PNL from OKX bills endpoint (matches Trading Calendar exactly)."""
    import time
    global _daily_pnl_cache, _daily_pnl_ts

    now = time.time()
    if now - _daily_pnl_ts < DAILY_PNL_TTL:
        return _daily_pnl_cache

    client = _get_exchange_client()
    if not client:
        return _daily_pnl_cache

    try:
        daily = client.get_daily_pnl(days=365)
        if daily:
            _daily_pnl_cache = daily
            _daily_pnl_ts = now
            logger.info(f"[DASHBOARD] Daily PNL from bills: {len(daily)} days")
        return _daily_pnl_cache
    except Exception as e:
        logger.warning(f"[DASHBOARD] Daily PNL fetch failed: {e}")
        return _daily_pnl_cache


_lookup_cache: list = []
_lookup_cache_ts: float = 0.0
LOOKUP_CACHE_TTL = 5.0  # seconds


def _build_entry_type_lookup() -> list:
    """Build lookup from active positions (JSON) + closed trades (SQLite).

    Returns list of dicts with symbol, entry_type, close_reason, and open_ts (UTC epoch).
    Match by symbol + closest timestamp within 10 min window.
    Cached for 5 seconds to avoid repeated full-table scans.
    """
    import time
    from datetime import datetime, timezone
    global _lookup_cache, _lookup_cache_ts

    now = time.time()
    if now - _lookup_cache_ts < LOOKUP_CACHE_TTL:
        return _lookup_cache

    try:
        # Active from JSON + closed from SQLite
        active_pos = position_reader._all_positions()
        closed_pos = trades_db.get_all_closed_for_lookup() if trades_db else []
        all_pos = active_pos + closed_pos
        entries = []
        for p in all_pos:
            # Only include CLOSED positions for history matching.
            # OPEN/PARTIAL_CLOSE positions should NOT appear in trade history.
            if p.get("status") != "CLOSED":
                continue
            sym = p.get("symbol", "")
            et = p.get("entry_type", "standard_m15")
            cr = p.get("close_reason", "")
            tp1 = p.get("tp1_closed", False)
            tp2 = p.get("tp2_closed", False)
            ts = p.get("timestamp", "")
            if not (sym and ts):
                continue
            # Enrich close_reason with TP status
            if not cr or cr in ("EXTERNAL_CLOSE", "CLOSED"):
                if tp2:
                    cr = "TP2"
                elif tp1:
                    cr = "TP1"
            # Parse bot timestamp (naive = local time) to epoch for comparison
            try:
                bot_dt = datetime.fromisoformat(ts)
                # Naive datetime: .timestamp() uses system local TZ (same as bot)
                epoch = bot_dt.timestamp()
            except (ValueError, TypeError):
                continue
            entries.append({
                "position_id": p.get("position_id", f"{sym}_{epoch}"),
                "symbol": sym,
                "side": p.get("side", ""),
                "entry_type": et,
                "close_reason": cr,
                "tp1_closed": tp1,
                "tp2_closed": tp2,
                "epoch": epoch,
                "take_profit_1": p.get("take_profit_1"),
                "take_profit_2": p.get("take_profit_2"),
                "stop_loss": p.get("stop_loss"),
                "trailing_sl": p.get("trailing_sl"),
                "chandelier_sl": p.get("chandelier_sl"),
                "margin": p.get("margin", 0),
                "timestamp": ts,
                "close_time": p.get("close_time"),
                "entry_price": p.get("entry_price"),
                "realized_pnl": p.get("realized_pnl"),
                "pnl_usd": p.get("pnl_usd"),
                "entry_fee": p.get("entry_fee", 0),
                "total_exit_fees": p.get("total_exit_fees", 0),
                "size": p.get("size", 0),
                "remaining_size": p.get("remaining_size", 0),
                "exit_price": p.get("exit_price"),
            })
        _lookup_cache = entries
        _lookup_cache_ts = now
        return entries
    except Exception:
        return []


def _match_position_data(lookup: list, symbol: str, open_time: str) -> dict:
    """Find entry_type + close_reason from positions.json for an OKX trade.

    Uses epoch-based matching (±10 min window) to handle timezone differences.
    """
    matches = _match_all_positions(lookup, symbol, open_time)
    return matches[0] if matches else {
        "side": "", "entry_type": "standard_m15", "close_reason": "MANUAL_CLOSE",
        "tp1_closed": False, "tp2_closed": False,
        "take_profit_1": None, "take_profit_2": None,
        "stop_loss": None, "trailing_sl": None, "chandelier_sl": None,
        "realized_pnl": None, "entry_fee": 0, "total_exit_fees": 0,
        "margin": 0, "size": 0, "remaining_size": 0,
    }


def _classify_exit(entry: dict) -> str:
    """Classify a trade's exit type from close_reason + tp flags."""
    cr = entry.get("close_reason", "") or ""
    tp1 = entry.get("tp1_closed", False)
    tp2 = entry.get("tp2_closed", False)
    if cr == "TP2" or tp2:
        return "TP2"
    if cr == "TP1" or (tp1 and cr in ("CLOSED", "EXTERNAL_CLOSE", "")):
        return "TP1"
    if "CHANDELIER" in cr:
        return "Chandelier"
    if cr == "HARD_SL":
        return "Hard SL"
    if cr in ("MANUAL_WEB", "MANUAL_BULK"):
        return "Manual"
    if cr == "EXTERNAL_CLOSE":
        return "External"
    return "Other"


def _match_all_positions(
    lookup: list, symbol: str, open_time: str, close_time: str = "",
    used_ids: set | None = None,
) -> list[dict]:
    """Find ALL matching bot positions for an OKX merged trade.

    OKX merges multiple bot positions (e.g., m15 + h1) for the same symbol
    into one exchange position. This returns all bot positions whose entry
    falls between OKX open_time and close_time, so they can be displayed as
    separate rows with correct entry_type, timestamps, and margin.

    Args:
        used_ids: Optional set of position_ids already matched to other OKX trades.
                  Matched positions are added to this set to prevent duplicates.
    """
    from datetime import datetime, timezone

    if not lookup or not open_time:
        return []
    try:
        okx_open = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
        if okx_open.tzinfo is None:
            okx_open = okx_open.replace(tzinfo=timezone.utc)
        okx_open_epoch = okx_open.timestamp()
    except (ValueError, TypeError):
        return []

    # Parse close_time for upper bound (fallback: open + 48h)
    okx_close_epoch = okx_open_epoch + 172800  # 48h default
    if close_time:
        try:
            okx_close = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            if okx_close.tzinfo is None:
                okx_close = okx_close.replace(tzinfo=timezone.utc)
            okx_close_epoch = okx_close.timestamp()
        except (ValueError, TypeError):
            pass

    # Match bot positions whose entry is within [okx_open - 1min, okx_close]
    # Tight window to avoid matching positions that belong to the NEXT OKX trade
    OPEN_TOLERANCE = 60   # 1 min before open (clock drift)
    CLOSE_TOLERANCE = 0   # entry must be BEFORE close (not after)
    candidates = [e for e in lookup if e["symbol"] == symbol]
    matches = []
    for entry in candidates:
        # Skip positions already matched to another OKX trade
        pid = entry.get("position_id", "")
        if used_ids is not None and pid in used_ids:
            continue

        entry_epoch = entry["epoch"]
        if (okx_open_epoch - OPEN_TOLERANCE) <= entry_epoch <= (okx_close_epoch + CLOSE_TOLERANCE):
            matches.append({
                "position_id": pid,
                "side": entry.get("side", ""),
                "entry_type": entry["entry_type"],
                "close_reason": entry["close_reason"],
                "tp1_closed": entry.get("tp1_closed", False),
                "tp2_closed": entry.get("tp2_closed", False),
                "take_profit_1": entry.get("take_profit_1"),
                "take_profit_2": entry.get("take_profit_2"),
                "stop_loss": entry.get("stop_loss"),
                "trailing_sl": entry.get("trailing_sl"),
                "chandelier_sl": entry.get("chandelier_sl"),
                "margin": entry.get("margin", 0),
                "entry_time": entry.get("timestamp"),
                "close_time": entry.get("close_time"),
                "entry_price": entry.get("entry_price"),
                "realized_pnl": entry.get("realized_pnl"),
                "pnl_usd": entry.get("pnl_usd"),
                "entry_fee": entry.get("entry_fee", 0),
                "total_exit_fees": entry.get("total_exit_fees", 0),
                "size": entry.get("size", 0),
                "remaining_size": entry.get("remaining_size", 0),
                "exit_price": entry.get("exit_price"),
            })

    # Sort by entry time
    matches.sort(key=lambda m: m.get("entry_time", ""))

    # Mark matched positions as used
    if used_ids is not None:
        for m in matches:
            pid = m.get("position_id", "")
            if pid:
                used_ids.add(pid)

    return matches


def _inject_exchange_pnl(positions: list[dict], exchange_pnl: dict) -> None:
    """Enrich position dicts with live exchange data (price, PNL).

    Local margin is the source of truth (per-trade from config).
    OKX merges multiple local positions for the same symbol into one
    exchange position, so we split unrealized_pnl proportionally by
    remaining_size.  Margin and entry_price are NEVER overwritten.
    """
    if not exchange_pnl:
        return

    # Group positions by symbol to split exchange PNL proportionally
    from collections import defaultdict
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for pos in positions:
        sym = pos.get("symbol", "")
        if sym in exchange_pnl:
            by_sym[sym].append(pos)

    for sym, sym_positions in by_sym.items():
        ex = exchange_pnl[sym]
        total_upnl = ex["unrealized_pnl"]
        mark_price = ex["mark_price"]

        if len(sym_positions) > 1 and mark_price > 0:
            # Multiple positions for same symbol → split by price diff (normalized)
            # so positions with better entry get proportionally more PNL
            raw_pnls = {}
            for i, pos in enumerate(sym_positions):
                p_entry = pos.get("entry_price", 0) or 0
                p_margin = pos.get("margin", 0) or 0
                p_lev = pos.get("leverage", 1) or 1
                p_remaining = pos.get("remaining_size", pos.get("size", 0))
                p_size = pos.get("size", 0) or 1
                remaining_ratio = p_remaining / p_size if p_size > 0 else 0
                if p_entry > 0 and p_margin > 0:
                    p_side = pos.get("side", "BUY")
                    price_pct = (mark_price - p_entry) / p_entry
                    if p_side == "SELL":
                        price_pct = -price_pct
                    raw_pnls[i] = price_pct * p_margin * p_lev * remaining_ratio
                else:
                    raw_pnls[i] = 0

            raw_total = sum(raw_pnls.values())

            for i, pos in enumerate(sym_positions):
                if raw_total != 0:
                    ratio = raw_pnls[i] / raw_total
                else:
                    ratio = 1.0 / len(sym_positions)
                pos_upnl = total_upnl * ratio
                pos["pnl_usd"] = round(pos.get("realized_pnl", 0) + pos_upnl, 2)
                pos["current_price"] = mark_price
                local_margin = pos.get("margin", 0)
                if local_margin > 0:
                    pos["roi_percent"] = round(pos["pnl_usd"] / local_margin * 100, 2)
        else:
            # Single position — use exchange PNL directly
            for pos in sym_positions:
                pos_upnl = total_upnl
                pos["pnl_usd"] = round(pos.get("realized_pnl", 0) + pos_upnl, 2)
                pos["current_price"] = mark_price
                local_margin = pos.get("margin", 0)
                if local_margin > 0:
                    pos["roi_percent"] = round(pos["pnl_usd"] / local_margin * 100, 2)

# ── Static files for production build ────────────────────────────
FRONTEND_DIST = PROJECT_ROOT / "web" / "frontend" / "dist"


# ── Auth Models ──────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class GoogleLoginRequest(BaseModel):
    credential: str  # Google ID token from GSI


# ── Auth Endpoints (PUBLIC — no token required) ──────────────────

@app.post("/api/auth/login")
async def login(req: LoginRequest):
    """Authenticate user and return JWT token."""
    user = authenticate_user(req.username, req.password)
    if not user:
        return {"success": False, "error": "Invalid username or password"}
    token = create_access_token(
        username=user["username"],
        user_id=user.get("user_id", ""),
        role=user.get("role", "user"),
    )
    return {
        "success": True,
        "data": {
            "token": token,
            "username": user["username"],
            "role": user.get("role", "user"),
        },
    }


@app.post("/api/auth/google")
async def google_login(req: GoogleLoginRequest):
    """Authenticate via Google ID token and return JWT."""
    logger.info(f"Google login attempt, credential length: {len(req.credential) if req.credential else 0}")
    user_info = verify_google_token(req.credential)
    if not user_info:
        logger.error("Google login FAILED - verify_google_token returned None")
        return {"success": False, "error": "Google authentication failed or email not authorized"}
    token = create_access_token(
        username=user_info["email"],
        user_id=user_info.get("user_id", ""),
        role=user_info.get("role", "user"),
    )
    return {
        "success": True,
        "data": {
            "token": token,
            "username": user_info["name"],
            "email": user_info["email"],
            "picture": user_info.get("picture"),
            "role": user_info.get("role", "user"),
        },
    }


@app.get("/api/auth/google-client-id")
async def get_google_client_id():
    """Return Google Client ID for frontend GSI initialization."""
    return {"success": True, "data": {"client_id": GOOGLE_CLIENT_ID}}


@app.get("/api/auth/me")
async def auth_me(current_user: TokenPayload = Depends(get_current_user_obj)):
    """Verify token and return current user info."""
    return {
        "success": True,
        "data": {
            "username": current_user.username,
            "user_id": current_user.user_id,
            "role": current_user.role,
        },
    }


@app.post("/api/auth/change-password")
async def auth_change_password(
    req: ChangePasswordRequest,
    username: str = Depends(get_current_user),
):
    """Change password for the authenticated user."""
    if len(req.new_password) < 6:
        return {"success": False, "error": "New password must be at least 6 characters"}
    ok = change_password(username, req.old_password, req.new_password)
    if not ok:
        return {"success": False, "error": "Current password is incorrect"}
    return {"success": True, "data": {"message": "Password changed"}}


# ── Protected REST Endpoints ─────────────────────────────────────


@app.get("/api/positions")
async def get_positions(_user: str = Depends(get_current_user)):
    """Get all open positions (with live PNL from OKX)."""
    try:
        positions = position_reader.get_open_positions()
        _, exchange_pnl = _fetch_exchange_data()
        _inject_exchange_pnl(positions, exchange_pnl)
        return {"success": True, "data": positions}
    except Exception as e:
        logger.error(f"Error reading positions: {e}")
        return {"success": False, "error": "Internal server error", "data": []}



def _sqlite_closed_to_trade(row: dict) -> dict:
    """Convert a SQLite closed_trades row to the same format as OKX-based trades."""
    pnl = row.get("pnl_usd", 0) or 0
    margin = row.get("margin", 0) or 0
    roi = round(pnl / margin * 100, 2) if margin > 0 else (row.get("roi_percent", 0) or 0)
    return {
        "position_id": row.get("position_id", ""),
        "symbol": row.get("symbol", ""),
        "side": row.get("side", ""),
        "entry_price": row.get("entry_price", 0) or 0,
        "current_price": row.get("exit_price", 0) or row.get("current_price", 0) or 0,
        "close_price": row.get("exit_price", 0) or row.get("current_price", 0) or 0,
        "pnl_usd": round(pnl, 2),
        "roi_percent": roi,
        "status": "CLOSED",
        "timestamp": row.get("timestamp", "") or row.get("entry_time", ""),
        "close_time": row.get("close_time", ""),
        "close_reason": row.get("close_reason", ""),
        "entry_type": row.get("entry_type", "standard_m15"),
        "tp1_closed": bool(row.get("tp1_closed", 0)),
        "tp2_closed": bool(row.get("tp2_closed", 0)),
        "leverage": row.get("leverage", 0) or 0,
        "margin": round(margin, 2),
        "take_profit_1": row.get("take_profit_1"),
        "take_profit_2": row.get("take_profit_2"),
        "stop_loss": row.get("stop_loss"),
        "trailing_sl": row.get("trailing_sl"),
        "chandelier_sl": row.get("chandelier_sl"),
        "entry_fee": row.get("entry_fee", 0) or 0,
        "total_exit_fees": row.get("total_exit_fees", 0) or 0,
    }


def _build_closed_trades_list() -> list[dict]:
    """Build unified closed trades list from OKX history + bot metadata.

    Priority: OKX position history (ground truth PNL) > SQLite closed_trades (fallback).
    - OKX API available: use OKX data, backfill recent from positions.json
    - OKX API down or trades older than OKX retention (~3 months): fallback to SQLite
    """
    history = _fetch_position_history()
    if not history:
        # OKX API unavailable — full fallback to SQLite
        if trades_db:
            logger.info("OKX API unavailable, falling back to SQLite closed_trades")
            sqlite_rows = trades_db.get_all_closed_for_lookup()
            return [_sqlite_closed_to_trade(r) for r in sqlite_rows]
        return []

    lookup = _build_entry_type_lookup()
    used_ids: set[str] = set()
    trades = []
    for idx, h in enumerate(history):
        pnl = h["realized_pnl"]
        side_map = {"long": "BUY", "short": "SELL"}
        okx_side = side_map.get(h["side"], h["side"].upper())
        leverage = h["leverage"]

        # Find ALL bot positions that overlap with this OKX trade
        all_matches = _match_all_positions(
            lookup, h["symbol"], h.get("open_time", ""), h.get("close_time", ""),
            used_ids=used_ids,
        )

        if len(all_matches) > 1:
            # Multiple bot positions merged into one OKX trade
            logger.debug(
                f"[TRADE-LIST] {h['symbol']}: OKX pnl=${pnl:.4f} close={h['close_price']} "
                f"matches={len(all_matches)} entries=[{', '.join(m.get('entry_type','?') + '@' + str(m.get('entry_price','?')) for m in all_matches)}]"
            )

            # Strategy 1: Use SQLite per-position data if bot already did fill matching
            # Bot's fill matching writes accurate per-fill PnL — sum should match OKX total
            sqlite_sum = sum(m.get("pnl_usd", 0) or 0 for m in all_matches)
            use_sqlite = abs(sqlite_sum - pnl) < 0.10  # Within $0.10 tolerance

            if use_sqlite:
                # Bot's fill-matched data is accurate — use directly
                sub_pnls = [round(m.get("pnl_usd", 0) or 0, 2) for m in all_matches]
                # Fix rounding drift
                if sub_pnls:
                    sub_pnls[-1] = round(pnl - sum(sub_pnls[:-1]), 2)
                logger.debug(
                    f"[TRADE-LIST] {h['symbol']}: Using SQLite fill-matched PnL "
                    f"(sum=${sqlite_sum:.2f} ≈ OKX ${pnl:.2f})"
                )
            else:
                # Strategy 2: Proportional split from OKX average close price (fallback)
                raw_pnls = []
                for m in all_matches:
                    m_entry = m.get("entry_price") or h["open_price"]
                    m_margin = m.get("margin", 0)
                    m_exit = h["close_price"]
                    is_long = (m.get("side") or okx_side) in ("BUY", "long")
                    if m_entry > 0 and m_margin > 0:
                        price_pct = (m_exit - m_entry) / m_entry
                        if not is_long:
                            price_pct = -price_pct
                        raw_pnls.append(price_pct * m_margin * (m.get("leverage") or leverage))
                    else:
                        raw_pnls.append(0)

                raw_total = sum(raw_pnls)
                sub_pnls = []
                for si in range(len(all_matches)):
                    if raw_total != 0:
                        sub_pnls.append(round(pnl * (raw_pnls[si] / raw_total), 2))
                    else:
                        sub_pnls.append(round(pnl / len(all_matches), 2))
                if sub_pnls:
                    sub_pnls[-1] = round(pnl - sum(sub_pnls[:-1]), 2)

            for sub_idx, m in enumerate(all_matches):
                m_margin = m.get("margin", 0)
                m_entry_price = m.get("entry_price") or h["open_price"]
                m_pnl = sub_pnls[sub_idx]
                m_roi = round(m_pnl / m_margin * 100, 2) if m_margin > 0 else 0
                # Use per-position exit price if bot did fill matching,
                # otherwise fall back to OKX average close price
                m_exit_price = (
                    m.get("exit_price") if use_sqlite and m.get("exit_price")
                    else h["close_price"]
                )
                logger.debug(
                    f"[TRADE-LIST] {h['symbol']} sub[{sub_idx}]: "
                    f"{m.get('entry_type','?')}@{m_entry_price:.1f} pnl=${m_pnl:.2f} "
                    f"id=okx_{idx}_{sub_idx}"
                )
                trades.append({
                    "position_id": f"okx_{idx}_{sub_idx}",
                    "_bot_position_id": m.get("position_id", ""),
                    "symbol": h["symbol"],
                    "side": m.get("side") or okx_side,
                    "entry_price": m_entry_price,
                    "current_price": m_exit_price,
                    "close_price": m_exit_price,
                    "pnl_usd": m_pnl,
                    "roi_percent": m_roi,
                    "status": "CLOSED",
                    "timestamp": m.get("entry_time") or h.get("open_time", h["close_time"]),
                    "close_time": m.get("close_time") or h["close_time"],
                    "close_reason": m["close_reason"],
                    "entry_type": m["entry_type"],
                    "tp1_closed": m.get("tp1_closed", False),
                    "tp2_closed": m.get("tp2_closed", False),
                    "leverage": leverage,
                    "margin": round(m_margin, 2),
                    "take_profit_1": m.get("take_profit_1"),
                    "take_profit_2": m.get("take_profit_2"),
                    "stop_loss": m.get("stop_loss"),
                    "trailing_sl": m.get("trailing_sl"),
                    "chandelier_sl": m.get("chandelier_sl"),
                    "entry_fee": m.get("entry_fee", 0),
                    "total_exit_fees": m.get("total_exit_fees", 0),
                    "_fill_matched": use_sqlite,
                })
        else:
            # Single position or no match
            matched = all_matches[0] if all_matches else {
                "side": "", "entry_type": "standard_m15", "close_reason": "MANUAL_CLOSE",
                "tp1_closed": False, "tp2_closed": False,
                "take_profit_1": None, "take_profit_2": None,
                "stop_loss": None, "trailing_sl": None, "chandelier_sl": None,
            }

            # Always use OKX PNL as ground truth (bot pnl_usd may be double-counted)
            m_margin = matched.get("margin", 0)
            m_pnl = round(pnl, 2)
            if m_margin > 0:
                m_roi = round(m_pnl / m_margin * 100, 2)
            else:
                m_roi = round(h.get("pnl_ratio", 0) * 100, 2)
                m_margin = abs(pnl / h["pnl_ratio"]) if h.get("pnl_ratio") else 0

            trades.append({
                "position_id": f"okx_{idx}",
                "_bot_position_id": matched.get("position_id", ""),
                "symbol": h["symbol"],
                "side": matched.get("side") or okx_side,
                "entry_price": matched.get("entry_price") or h["open_price"],
                "current_price": h["close_price"],
                "close_price": h["close_price"],
                "pnl_usd": m_pnl,
                "roi_percent": m_roi,
                "status": "CLOSED",
                "timestamp": matched.get("entry_time") or h.get("open_time", h["close_time"]),
                "close_time": matched.get("close_time") or h["close_time"],
                "close_reason": matched["close_reason"],
                "entry_type": matched["entry_type"],
                "tp1_closed": matched.get("tp1_closed", False),
                "tp2_closed": matched.get("tp2_closed", False),
                "leverage": leverage,
                "margin": round(m_margin, 2),
                "take_profit_1": matched.get("take_profit_1"),
                "take_profit_2": matched.get("take_profit_2"),
                "stop_loss": matched.get("stop_loss"),
                "trailing_sl": matched.get("trailing_sl"),
                "chandelier_sl": matched.get("chandelier_sl"),
                "entry_fee": matched.get("entry_fee", 0),
                "total_exit_fees": matched.get("total_exit_fees", 0),
            })

    # Backfill from positions.json for trades not covered by OKX history
    for entry in lookup:
        if entry["position_id"] in used_ids:
            continue
        if not entry.get("close_time"):
            continue
        if entry.get("close_time", "") <= BACKFILL_CUTOFF:
            continue
        p_pnl = entry.get("pnl_usd", 0) or 0
        p_margin = entry.get("margin", 0)
        p_roi = round(p_pnl / p_margin * 100, 2) if p_margin > 0 else 0
        trades.append({
            "position_id": entry["position_id"],
            "symbol": entry["symbol"],
            "side": entry.get("side", ""),
            "entry_price": entry.get("entry_price", 0),
            "current_price": 0,
            "close_price": 0,
            "pnl_usd": round(p_pnl, 2),
            "roi_percent": p_roi,
            "status": "CLOSED",
            "timestamp": entry.get("timestamp", ""),
            "close_time": entry.get("close_time", ""),
            "close_reason": entry.get("close_reason", ""),
            "entry_type": entry["entry_type"],
            "tp1_closed": entry.get("tp1_closed", False),
            "tp2_closed": entry.get("tp2_closed", False),
            "leverage": 0,
            "margin": round(p_margin, 2),
            "take_profit_1": entry.get("take_profit_1"),
            "take_profit_2": entry.get("take_profit_2"),
            "stop_loss": entry.get("stop_loss"),
            "trailing_sl": entry.get("trailing_sl"),
            "chandelier_sl": entry.get("chandelier_sl"),
            "entry_fee": entry.get("entry_fee", 0),
            "total_exit_fees": entry.get("total_exit_fees", 0),
        })

    # Backfill older trades from SQLite that OKX no longer returns (~3 month limit)
    if trades_db:
        okx_position_ids = {t["position_id"] for t in trades}
        oldest_okx_time = ""
        for t in trades:
            ct = t.get("close_time", "")
            if ct and (not oldest_okx_time or ct < oldest_okx_time):
                oldest_okx_time = ct

        if oldest_okx_time:
            sqlite_rows = trades_db.get_all_closed_for_lookup()
            for row in sqlite_rows:
                row_ct = row.get("close_time", "") or ""
                if not row_ct or row_ct >= oldest_okx_time:
                    continue  # Already covered by OKX data
                pid = row.get("position_id", "")
                if pid in okx_position_ids or pid in used_ids:
                    continue
                trades.append(_sqlite_closed_to_trade(row))

    # ── Auto-sync: write correct OKX PNL back to SQLite closed_trades ──
    # Dashboard already has ground-truth OKX data matched to bot positions.
    # Sync any mismatched PNL back to SQLite so it stays accurate.
    _auto_sync_pnl_to_sqlite(trades, used_ids)

    return trades


def _auto_sync_pnl_to_sqlite(trades: list[dict], used_ids: set[str]):
    """Sync OKX-matched PNL back to closed_trades SQLite table.

    Only updates trades that have a real bot position_id (not okx_* generated IDs)
    and where PNL differs by more than $0.005.
    """
    if not trades_db:
        return

    try:
        conn = trades_db._get_conn()
        updates = []
        for t in trades:
            # Use _bot_position_id (real bot ID) instead of okx_* generated ID
            pid = t.get("_bot_position_id", "") or t.get("position_id", "")
            if not pid or pid.startswith("okx_"):
                continue

            # Skip fill-matched trades — bot's per-fill data is more accurate
            # than dashboard's proportional split
            if t.get("_fill_matched"):
                continue

            pnl = t.get("pnl_usd", 0) or 0
            close_price = t.get("close_price") or t.get("current_price", 0) or 0
            roi = t.get("roi_percent", 0) or 0

            # Check current SQLite value
            row = conn.execute(
                "SELECT pnl_usd, okx_pnl_synced FROM closed_trades WHERE position_id = ?", [pid]
            ).fetchone()
            if not row:
                continue

            old_pnl = row["pnl_usd"] or 0
            if abs(old_pnl - pnl) > 0.005:
                updates.append((pnl, roi, close_price, pid))

        if updates:
            for pnl, roi, close_price, pid in updates:
                conn.execute(
                    """UPDATE closed_trades
                       SET pnl_usd = ?, roi_percent = ?, current_price = ?
                       WHERE position_id = ?""",
                    [pnl, roi, close_price, pid],
                )
            conn.commit()
            logger.info(f"[AUTO-SYNC] Updated {len(updates)} trades in SQLite from OKX data")
        conn.close()
    except Exception as e:
        logger.warning(f"[AUTO-SYNC] Failed to sync PNL to SQLite: {e}")


@app.get("/api/positions/closed")
async def get_closed_positions(
    limit: int = 50,
    offset: int = 0,
    symbol: Optional[str] = None,
    entry_type: Optional[str] = None,
    result: Optional[str] = None,
    sort_by: Optional[str] = "close_time",
    sort_order: Optional[str] = "desc",
    _user: str = Depends(get_current_user),
):
    """Get closed positions with optional filters and sorting."""
    try:
        trades = _build_closed_trades_list()
        if trades:
            # Apply filters
            if symbol:
                trades = [t for t in trades if t["symbol"] == symbol]
            if entry_type:
                trades = [t for t in trades if t.get("entry_type") == entry_type]
            if result == "win":
                trades = [t for t in trades if t["pnl_usd"] > 0]
            elif result == "loss":
                trades = [t for t in trades if t["pnl_usd"] <= 0]

            # Sort
            reverse = sort_order == "desc"
            key_field = sort_by if sort_by in ("close_time", "pnl_usd", "roi_percent") else "close_time"
            trades.sort(key=lambda t: t.get(key_field, 0), reverse=reverse)

            total = len(trades)
            paginated = trades[offset:offset + limit]
            return {"success": True, "data": {"positions": paginated, "total": total, "limit": limit, "offset": offset}}

        # Fallback to file
        positions = position_reader.get_closed_positions(
            limit=limit,
            offset=offset,
            symbol=symbol,
            entry_type=entry_type,
            result=result,
            sort_by=sort_by or "close_time",
            sort_order=sort_order or "desc",
        )
        return {"success": True, "data": positions}
    except Exception as e:
        logger.error(f"Error reading closed positions: {e}")
        return {"success": False, "error": "Internal server error", "data": {"positions": [], "total": 0}}


@app.get("/api/stats")
async def get_stats(
    period: str = Query(default="all", pattern="^(24h|7d|30d|all)$"),
    _user: str = Depends(get_current_user),
):
    """Get trading statistics (live data from OKX). Optionally filter by period."""
    try:
        stats = position_reader.get_stats()

        # Override with real exchange data (always current, not filtered)
        balance, exchange_pnl = _fetch_exchange_data()
        if balance > 0:
            stats["balance"] = round(balance, 2)
            stats["growth_pct"] = round((balance - INITIAL_DEPOSIT) / INITIAL_DEPOSIT * 100, 2)

        # Recalculate unrealized PNL and margin from exchange
        if exchange_pnl:
            total_upnl = sum(d["unrealized_pnl"] for d in exchange_pnl.values())
            total_margin = sum(d["margin"] for d in exchange_pnl.values())
            stats["unrealized_pnl"] = round(total_upnl, 2)
            stats["total_margin"] = round(total_margin, 2)

        # Position history (100 most recent) — filtered by period
        history = _filter_history_by_period(_fetch_position_history(), period)

        # PNL source depends on period:
        # - "all": closed_trades SQLite (stable, all trades) + okx_history fees
        # - "30d": bills endpoint (matches OKX app)
        # - "24h"/"7d": positions-history (fast)
        total_upnl = stats.get("unrealized_pnl", 0)

        if period == "all" and trades_db:
            # All-time: closed_trades PNL is ground truth ($70.54)
            # okx_history has fee/funding breakdown (best available)
            okx_totals = trades_db.get_okx_pnl_totals()
            realized = stats.get("total_pnl", 0)  # from position_reader.get_stats()
            stats["realized_pnl"] = round(realized, 2)
            stats["total_fees"] = okx_totals.get("total_fees", 0)
            stats["total_funding_fees"] = okx_totals.get("total_funding_fees", 0)
            stats["total_pnl"] = round(realized + total_upnl, 2)
        elif period == "30d":
            bills_summary = await asyncio.get_event_loop().run_in_executor(
                None, _fetch_bills_summary, 30
            )
            if bills_summary:
                stats["realized_pnl"] = bills_summary.get("realized_pnl", 0)
                stats["total_fees"] = bills_summary.get("total_fees", 0)
                stats["total_funding_fees"] = bills_summary.get("funding", 0)
                stats["total_pnl"] = round(
                    bills_summary.get("net_pnl", 0) + total_upnl, 2
                )
            elif history:
                okx_realized = sum(h["realized_pnl"] for h in history)
                total_fee = sum(h.get("fee", 0) for h in history)
                total_funding = sum(h.get("funding_fee", 0) for h in history)
                stats["realized_pnl"] = round(okx_realized, 2)
                stats["total_fees"] = round(abs(total_fee), 2)
                stats["total_funding_fees"] = round(total_funding, 4)
                stats["total_pnl"] = round(
                    okx_realized + total_fee + total_funding + total_upnl, 2
                )
        elif history:
            # Positions-based totals (fast, for 24h/7d)
            okx_realized = sum(h["realized_pnl"] for h in history)
            total_fee = sum(h.get("fee", 0) for h in history)
            total_funding = sum(h.get("funding_fee", 0) for h in history)
            stats["realized_pnl"] = round(okx_realized, 2)
            stats["total_fees"] = round(abs(total_fee), 2)
            stats["total_funding_fees"] = round(total_funding, 4)
            stats["total_pnl"] = round(
                okx_realized + total_fee + total_funding + total_upnl, 2
            )

        # Trade stats (win/loss/profit_factor/avg) — source depends on period
        if period == "all":
            # "All" period: use closed_trades from SQLite (complete, 225+ trades)
            # position_reader.get_stats() already computed these from SQLite — keep them
            pass
        elif history:
            # 24h/7d/30d: compute from OKX history + backfill (period-filtered)
            lookup = _build_entry_type_lookup()
            used_ids: set[str] = set()
            expanded_pnls: list[float] = []
            for h in history:
                pnl = h["realized_pnl"]
                all_matches = _match_all_positions(
                    lookup, h["symbol"], h.get("open_time", ""), h.get("close_time", ""),
                    used_ids=used_ids,
                )
                if len(all_matches) > 1:
                    total_margin = sum(m.get("margin", 0) for m in all_matches) or 1
                    for m in all_matches:
                        ratio = m.get("margin", 0) / total_margin
                        expanded_pnls.append(round(pnl * ratio, 4))
                else:
                    expanded_pnls.append(pnl)

            # Backfill from positions.json (with cutoff + period filter)
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            _period_deltas = {"24h": _td(hours=24), "7d": _td(days=7), "30d": _td(days=30)}
            _period_cutoff = (
                (_dt.now(_tz.utc) - _period_deltas[period]).isoformat()
                if period in _period_deltas else ""
            )
            for entry in lookup:
                if entry["position_id"] in used_ids:
                    continue
                ct = entry.get("close_time", "")
                if not ct:
                    continue
                if ct <= BACKFILL_CUTOFF:
                    continue
                if _period_cutoff and ct < _period_cutoff:
                    continue
                p_pnl = entry.get("pnl_usd", 0) or 0
                expanded_pnls.append(round(p_pnl, 4))

            # Win/loss stats from expanded rows (consistent with trade history)
            wins_list = [p for p in expanded_pnls if p > 0]
            losses_list = [p for p in expanded_pnls if p <= 0]
            total_trades = len(expanded_pnls)
            stats["total_trades"] = total_trades
            stats["closed_count"] = total_trades
            stats["wins"] = len(wins_list)
            stats["losses"] = len(losses_list)
            stats["win_rate"] = round(len(wins_list) / total_trades * 100, 1) if total_trades else 0
            gross_profit = sum(wins_list)
            gross_loss = abs(sum(losses_list))
            stats["avg_win"] = round(gross_profit / len(wins_list), 2) if wins_list else 0
            stats["avg_loss"] = round(gross_loss / len(losses_list), 2) if losses_list else 0
            stats["profit_factor"] = (
                round(gross_profit / gross_loss, 2) if gross_loss > 0 else "∞"
            )
            best = max(history, key=lambda h: h["realized_pnl"])
            worst = min(history, key=lambda h: h["realized_pnl"])
            stats["best_trade"] = {"symbol": best["symbol"], "pnl": round(best["realized_pnl"], 2)}
            stats["worst_trade"] = {"symbol": worst["symbol"], "pnl": round(worst["realized_pnl"], 2)}

        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"Error reading stats: {e}")
        return {"success": False, "error": "Internal server error", "data": {}}


@app.get("/api/stats/analysis")
async def get_performance_analysis(
    period: str = Query(default="all", pattern="^(24h|7d|30d|all)$"),
    _user: str = Depends(get_current_user),
):
    """Get performance breakdown by entry_type — uses same OKX data as trade list."""
    try:
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        # Use same data source as /api/positions/closed (OKX ground truth)
        all_trades = _build_closed_trades_list()

        # Filter by period
        if period != "all":
            _period_deltas = {"24h": _td(hours=24), "7d": _td(days=7), "30d": _td(days=30)}
            cutoff = (_dt.now(_tz.utc) - _period_deltas[period]).isoformat()
            all_trades = [t for t in all_trades if (t.get("close_time", "") or "") >= cutoff]

        # Filter by ANALYSIS_RESET_DATE (for "all" period, start fresh)
        if period == "all":
            all_trades = [t for t in all_trades if (t.get("close_time", "") or "") >= ANALYSIS_RESET_DATE]

        # Convert to rows for aggregation
        rows = []
        for t in all_trades:
            pnl = t.get("pnl_usd", 0) or 0
            fee = (t.get("entry_fee", 0) or 0) + (t.get("total_exit_fees", 0) or 0)
            rows.append({
                "entry_type": t.get("entry_type", "standard_m15"),
                "symbol": t.get("symbol", ""),
                "pnl": pnl,
                "fee": fee,
                "margin": t.get("margin", 0) or 0,
                "is_win": pnl > 0,
                "exit_type": _classify_exit(t),
            })

        if not rows:
            return {"success": True, "data": {
                "by_entry_type": [], "by_symbol": [], "by_exit": [],
                "totals": {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                           "total_pnl": 0, "avg_pnl": 0, "profit_factor": 0, "total_fee": 0},
            }}

        # Aggregate by entry_type
        by_et: dict[str, dict] = {}
        for r in rows:
            et = r["entry_type"]
            if et not in by_et:
                by_et[et] = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
                             "total_fee": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
                             "total_margin": 0.0}
            b = by_et[et]
            b["trades"] += 1
            b["total_pnl"] += r["pnl"]
            b["total_fee"] += r["fee"]
            b["total_margin"] += r["margin"]
            if r["is_win"]:
                b["wins"] += 1
                b["gross_profit"] += r["pnl"]
            else:
                b["losses"] += 1
                b["gross_loss"] += abs(r["pnl"])

        et_result = []
        for et, b in sorted(by_et.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
            n = b["trades"]
            pf = round(b["gross_profit"] / b["gross_loss"], 2) if b["gross_loss"] > 0 else 999
            avg_margin = b["total_margin"] / n if n else 0
            et_result.append({
                "entry_type": et,
                "trades": n,
                "wins": b["wins"],
                "losses": b["losses"],
                "win_rate": round(b["wins"] / n * 100, 1) if n else 0,
                "total_pnl": round(b["total_pnl"], 2),
                "avg_pnl": round(b["total_pnl"] / n, 2) if n else 0,
                "avg_roi": round(b["total_pnl"] / n / avg_margin * 100, 1) if avg_margin > 0 else 0,
                "profit_factor": pf,
                "total_fee": round(b["total_fee"], 2),
            })

        # Aggregate by symbol
        by_sym: dict[str, dict] = {}
        for r in rows:
            sym = r["symbol"]
            if sym not in by_sym:
                by_sym[sym] = {"trades": 0, "wins": 0, "total_pnl": 0.0,
                               "gross_profit": 0.0, "gross_loss": 0.0}
            b = by_sym[sym]
            b["trades"] += 1
            b["total_pnl"] += r["pnl"]
            if r["is_win"]:
                b["wins"] += 1
                b["gross_profit"] += r["pnl"]
            else:
                b["gross_loss"] += abs(r["pnl"])

        sym_result = []
        for sym, b in sorted(by_sym.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
            n = b["trades"]
            pf = round(b["gross_profit"] / b["gross_loss"], 2) if b["gross_loss"] > 0 else 999
            sym_result.append({
                "symbol": sym,
                "trades": n,
                "wins": b["wins"],
                "win_rate": round(b["wins"] / n * 100, 1) if n else 0,
                "total_pnl": round(b["total_pnl"], 2),
                "avg_pnl": round(b["total_pnl"] / n, 2) if n else 0,
                "profit_factor": pf,
            })

        # Totals
        total_trades = len(rows)
        total_wins = sum(1 for r in rows if r["is_win"])
        total_pnl_from_rows = sum(r["pnl"] for r in rows)
        total_fee_from_rows = sum(r["fee"] for r in rows)
        gross_profit = sum(r["pnl"] for r in rows if r["is_win"])
        gross_loss = sum(abs(r["pnl"]) for r in rows if not r["is_win"])
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999

        total_pnl = total_pnl_from_rows
        total_fee = total_fee_from_rows

        # Aggregate by exit type
        by_exit: dict[str, dict] = {}
        for r in rows:
            ex = r.get("exit_type", "Other")
            if ex not in by_exit:
                by_exit[ex] = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0,
                               "gross_profit": 0.0, "gross_loss": 0.0}
            b = by_exit[ex]
            b["trades"] += 1
            b["total_pnl"] += r["pnl"]
            if r["is_win"]:
                b["wins"] += 1
                b["gross_profit"] += r["pnl"]
            else:
                b["losses"] += 1
                b["gross_loss"] += abs(r["pnl"])

        exit_result = []
        for ex, b in sorted(by_exit.items(), key=lambda x: x[1]["trades"], reverse=True):
            n = b["trades"]
            pf_ex = round(b["gross_profit"] / b["gross_loss"], 2) if b["gross_loss"] > 0 else 999
            exit_result.append({
                "exit_type": ex,
                "trades": n,
                "wins": b["wins"],
                "losses": b["losses"],
                "win_rate": round(b["wins"] / n * 100, 1) if n else 0,
                "total_pnl": round(b["total_pnl"], 2),
                "avg_pnl": round(b["total_pnl"] / n, 2) if n else 0,
                "profit_factor": pf_ex,
            })

        totals = {
            "trades": total_trades,
            "wins": total_wins,
            "losses": total_trades - total_wins,
            "win_rate": round(total_wins / total_trades * 100, 1) if total_trades else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl": round(total_pnl / total_trades, 2) if total_trades else 0,
            "profit_factor": pf,
            "total_fee": round(total_fee, 2),
        }

        return {"success": True, "data": {
            "by_entry_type": et_result,
            "by_symbol": sym_result,
            "by_exit": exit_result,
            "totals": totals,
        }}
    except Exception as e:
        import traceback
        logger.error(f"Error in performance analysis: {e}\n{traceback.format_exc()}")
        return {"success": False, "error": "Performance analysis failed", "data": {}}


@app.get("/api/equity")
async def get_equity(_user: str = Depends(get_current_user)):
    """Get equity curve data (from OKX bills if available, then position history)."""
    try:
        # Primary: bills-based daily PNL (accurate, cumulative)
        daily_pnl = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_daily_pnl
        )
        if daily_pnl:
            curve = []
            cumulative = 0.0
            for day in daily_pnl:
                cumulative += day.get("net_pnl", 0)
                curve.append({
                    "time": f"{day['date']}T23:59:59",
                    "pnl": round(cumulative, 2),
                    "symbol": "",
                    "trade_pnl": round(day.get("net_pnl", 0), 2),
                })
            return {"success": True, "data": curve}

        # Fallback: OKX position history (per-trade granularity)
        history = _fetch_position_history()
        if history:
            sorted_history = sorted(history, key=lambda h: h["close_time"])
            curve = []
            cumulative = 0.0
            for h in sorted_history:
                cumulative += h["realized_pnl"]
                curve.append({
                    "time": h["close_time"],
                    "pnl": round(cumulative, 2),
                    "symbol": h["symbol"],
                    "trade_pnl": round(h["realized_pnl"], 2),
                })
            return {"success": True, "data": curve}

        # Final fallback
        equity = position_reader.get_equity_curve()
        return {"success": True, "data": equity}
    except Exception as e:
        logger.error(f"Error reading equity: {e}")
        return {"success": False, "error": "Internal server error", "data": []}


@app.get("/api/ema610-orders")
async def get_ema610_orders(_user: str = Depends(get_current_user)):
    """Get pending EMA610 limit orders."""
    try:
        orders_file = DATA_DIR / "ema610_pending_orders.json"
        if orders_file.exists():
            data = json.loads(orders_file.read_text(encoding="utf-8"))
            orders = list(data.values()) if isinstance(data, dict) else []
        else:
            orders = []
        return {"success": True, "data": orders}
    except Exception as e:
        logger.error(f"Error reading EMA610 orders: {e}")
        return {"success": False, "error": "Internal server error", "data": []}


@app.post("/api/ema610-orders/cancel")
async def cancel_ema610_order(request: Request, _user: str = Depends(get_current_user)):
    """Cancel a pending EMA610 limit order via bot command."""
    try:
        body = json.loads(await request.body())
        symbol = body.get("symbol")
        timeframe = body.get("timeframe")
        if not symbol or not timeframe:
            return {"success": False, "error": "symbol and timeframe required"}

        cmd_id = write_command("cancel_ema610", f"{symbol}_{timeframe}", {
            "symbol": symbol,
            "timeframe": timeframe,
        })
        return {
            "success": True,
            "message": f"Cancel queued for {symbol} {timeframe.upper()}",
            "command_id": cmd_id,
        }
    except Exception as e:
        logger.error(f"Error cancelling EMA610 order: {e}")
        return {"success": False, "error": "Internal server error"}


@app.get("/api/config")
async def get_config(_user: str = Depends(get_current_user)):
    """Get current strategy configuration."""
    try:
        config = config_reader.get_all()
        return {"success": True, "data": config}
    except Exception as e:
        logger.error(f"Error reading config: {e}")
        return {"success": False, "error": "Internal server error", "data": {}}


@app.get("/api/activity")
async def get_activity(_user: str = Depends(get_current_user)):
    """Get recent activity (from OKX position history if available)."""
    try:
        history = _fetch_position_history()
        if history:
            lookup = _build_entry_type_lookup()
            activity = []
            for h in history[:20]:
                pnl = h["realized_pnl"]
                roi = round(h.get("pnl_ratio", 0) * 100, 2)
                matched = _match_position_data(lookup, h["symbol"], h.get("open_time", ""))
                bot_close_reason = matched["close_reason"]
                activity.append({
                    "type": "trade",
                    "symbol": h["symbol"],
                    "action": "PROFIT" if pnl >= 0 else "LOSS",
                    "amount": round(pnl, 2),
                    "time": h["close_time"],
                    "strategy": matched["entry_type"],
                    "side": matched.get("side") or (h["side"].upper() if h["side"] else ""),
                    "entry_price": h["open_price"],
                    "entry_time": h.get("open_time", ""),
                    "close_time": h["close_time"],
                    "close_reason": bot_close_reason or h.get("close_reason", ""),
                    "roi": roi,
                })
            return {"success": True, "data": activity}

        # Fallback to file-based data
        activity = position_reader.get_recent_activity()
        return {"success": True, "data": activity}
    except Exception as e:
        logger.error(f"Error reading activity: {e}")
        return {"success": False, "error": "Internal server error", "data": []}



@app.get("/api/config/defaults")
async def get_config_defaults(_user: str = Depends(get_current_user)):
    """Get original default config values."""
    try:
        defaults = config_reader.get_defaults()
        return {"success": True, "data": defaults}
    except Exception as e:
        logger.error(f"Error reading config defaults: {e}")
        return {"success": False, "error": "Internal server error", "data": {}}


@app.get("/api/config/rules")
async def get_config_rules(_user: str = Depends(get_current_user)):
    """Get validation rules (min/max ranges) for config fields."""
    try:
        rules = config_reader.get_validation_rules()
        return {"success": True, "data": rules}
    except Exception as e:
        logger.error(f"Error reading config rules: {e}")
        return {"success": False, "error": "Internal server error", "data": {}}


MAX_REQUEST_BODY = 65536  # 64KB max for config updates


@app.put("/api/config")
async def update_config(request: Request, _user: str = Depends(get_current_user)):
    """Update config values at runtime with validation."""
    try:
        body_bytes = await request.body()
        if len(body_bytes) > MAX_REQUEST_BODY:
            return {"success": False, "error": "Request body too large", "data": {}}

        updates = json.loads(body_bytes)
        if not isinstance(updates, dict):
            return {"success": False, "error": "Request body must be a JSON object", "data": {}}

        result = config_reader.update(updates)
        return {"success": result["success"], "data": result}
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        return {"success": False, "error": "Failed to update config", "data": {}}


@app.post("/api/config/reset")
async def reset_config(request: Request, _user: str = Depends(get_current_user)):
    """Reset config sections to defaults."""
    try:
        body_bytes = await request.body()
        if len(body_bytes) > MAX_REQUEST_BODY:
            return {"success": False, "error": "Request body too large", "data": {}}

        body = json.loads(body_bytes)
        if not isinstance(body, dict):
            return {"success": False, "error": "Request body must be a JSON object", "data": {}}

        sections = body.get("sections")
        result = config_reader.reset_to_defaults(sections)
        return {"success": result["success"], "data": result}
    except Exception as e:
        logger.error(f"Error resetting config: {e}")
        return {"success": False, "error": "Failed to reset config", "data": {}}


# ── Coin Logos Proxy ─────────────────────────────────────────────


async def _fetch_coin_logos():
    """Fetch coin logos from Binance (server-side, no CORS)."""
    global _coin_logos, _coin_logos_fetched
    if _coin_logos_fetched:
        return _coin_logos
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.binance.com/bapi/composite/v1/public/marketing/symbol/list"
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            for item in data:
                logo = item.get("logo")
                if not logo:
                    continue
                name = item.get("name", "")
                if name:
                    _coin_logos[name.lower()] = logo
                symbol = item.get("symbol", "")
                if symbol:
                    base = re.sub(r"(USDT|BUSD|USD)$", "", symbol, flags=re.IGNORECASE).lower()
                    if base and base not in _coin_logos:
                        _coin_logos[base] = logo
        _coin_logos_fetched = True
        logger.info(f"Loaded {len(_coin_logos)} coin logos from Binance")
    except Exception as e:
        logger.error(f"Failed to fetch coin logos: {e}")
        _coin_logos_fetched = True
    return _coin_logos


@app.get("/api/coin-logos")
async def get_coin_logos(_user: str = Depends(get_current_user)):
    """Return symbol->logo URL mapping (proxied from Binance)."""
    logos = await _fetch_coin_logos()
    return {"success": True, "data": logos}


_image_cache: dict[str, tuple[bytes, str]] = {}
# symbol -> timestamp of when it was marked as not found (only for genuine 404s)
_coingecko_not_found: dict[str, float] = {}
_COINGECKO_RETRY_SECS = 3600  # retry after 1 hour


async def _fetch_coingecko_image(symbol: str) -> str | None:
    """Search CoinGecko for a coin's image URL by symbol."""
    import time

    cached_at = _coingecko_not_found.get(symbol)
    if cached_at and (time.time() - cached_at) < _COINGECKO_RETRY_SECS:
        return None

    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(
                f"https://api.coingecko.com/api/v3/search?query={symbol}"
            )
            resp.raise_for_status()
            coins = resp.json().get("coins", [])
            for coin in coins:
                if coin.get("symbol", "").lower() == symbol:
                    thumb = coin.get("large") or coin.get("thumb")
                    if thumb:
                        return thumb
            # Symbol genuinely not found in CoinGecko
            _coingecko_not_found[symbol] = time.time()
            return None
    except Exception:
        # Network/rate-limit error — do NOT cache, allow retry next request
        return None


@app.get("/api/coin-logo/{symbol}")
async def get_coin_logo(symbol: str, _user: str = Depends(get_current_user)):
    """Proxy a single coin logo image (avoids CORS + SSL issues in browser)."""
    from fastapi.responses import Response

    sym = symbol.lower()

    if sym in _image_cache:
        img_bytes, content_type = _image_cache[sym]
        return Response(content=img_bytes, media_type=content_type,
                        headers={"Cache-Control": "public, max-age=86400"})

    logos = await _fetch_coin_logos()
    url = logos.get(sym)

    if not url:
        url = await _fetch_coingecko_image(sym)

    if not url:
        return Response(status_code=404)

    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/png")
            _image_cache[sym] = (resp.content, content_type)
            return Response(content=resp.content, media_type=content_type,
                            headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        return Response(status_code=502)


# ── Position Actions (file-based IPC to bot) ────────────────────


def _find_position(position_id: str) -> dict | None:
    """Find an open/partial position by ID."""
    positions = position_reader.get_open_positions()
    for p in positions:
        if p.get("position_id") == position_id:
            return p
    return None


@app.post("/api/positions/close-all")
async def close_all_positions(_user: str = Depends(get_current_user)):
    """Queue close commands for ALL open positions."""
    positions = position_reader.get_open_positions()
    if not positions:
        return {"success": False, "error": "No open positions to close"}

    closed_symbols = []
    for pos in positions:
        pid = pos.get("position_id")
        if pid:
            write_command("close", pid)
            closed_symbols.append(pos.get("symbol", pid))

    return {
        "success": True,
        "message": f"Close queued for {len(closed_symbols)} positions: {', '.join(closed_symbols)}",
        "count": len(closed_symbols),
    }


@app.post("/api/positions/{position_id}/close")
async def close_position(position_id: str, _user: str = Depends(get_current_user)):
    """Queue a full close command for a position."""
    pos = _find_position(position_id)
    if not pos:
        return {"success": False, "error": "Position not found or already closed"}

    cmd_id = write_command("close", position_id)
    symbol = pos.get("symbol", position_id)
    return {"success": True, "message": f"Close command queued for {symbol}", "command_id": cmd_id}


@app.post("/api/positions/{position_id}/partial-close")
async def partial_close_position(
    position_id: str,
    request: Request,
    _user: str = Depends(get_current_user),
):
    """Queue a partial close command (25%, 50%, or 75%)."""
    pos = _find_position(position_id)
    if not pos:
        return {"success": False, "error": "Position not found or already closed"}

    try:
        body = json.loads(await request.body())
        percent = body.get("percent")
    except Exception:
        return {"success": False, "error": "Invalid JSON body"}

    if percent not in (25, 50, 75):
        return {"success": False, "error": "percent must be 25, 50, or 75"}

    cmd_id = write_command("partial_close", position_id, {"percent": percent})
    symbol = pos.get("symbol", position_id)
    return {"success": True, "message": f"Partial close {percent}% queued for {symbol}", "command_id": cmd_id}


@app.post("/api/positions/{position_id}/cancel-tp")
async def cancel_tp(
    position_id: str,
    request: Request,
    _user: str = Depends(get_current_user),
):
    """Queue a cancel take-profit command."""
    pos = _find_position(position_id)
    if not pos:
        return {"success": False, "error": "Position not found or already closed"}

    try:
        body = json.loads(await request.body())
        level = body.get("level")
    except Exception:
        return {"success": False, "error": "Invalid JSON body"}

    if level not in ("tp1", "tp2", "all"):
        return {"success": False, "error": "level must be tp1, tp2, or all"}

    cmd_id = write_command("cancel_tp", position_id, {"level": level})
    symbol = pos.get("symbol", position_id)
    return {"success": True, "message": f"Cancel {level.upper()} queued for {symbol}", "command_id": cmd_id}


@app.post("/api/positions/{position_id}/modify-sl")
async def modify_sl(
    position_id: str,
    request: Request,
    _user: str = Depends(get_current_user),
):
    """Queue a modify stop-loss command (set new trailing SL price)."""
    pos = _find_position(position_id)
    if not pos:
        return {"success": False, "error": "Position not found or already closed"}

    try:
        body = json.loads(await request.body())
        price = body.get("price")
    except Exception:
        return {"success": False, "error": "Invalid JSON body"}

    if not isinstance(price, (int, float)) or price <= 0:
        return {"success": False, "error": "price must be a positive number"}

    cmd_id = write_command("modify_sl", position_id, {"price": float(price)})
    symbol = pos.get("symbol", position_id)
    return {"success": True, "message": f"Modify SL to {price} queued for {symbol}", "command_id": cmd_id}


@app.post("/api/positions/{position_id}/modify-tp")
async def modify_tp(
    position_id: str,
    request: Request,
    _user: str = Depends(get_current_user),
):
    """Queue a modify take-profit command (set new TP price or re-enable cancelled TP)."""
    pos = _find_position(position_id)
    if not pos:
        return {"success": False, "error": "Position not found or already closed"}

    try:
        body = json.loads(await request.body())
        level = body.get("level")
        price = body.get("price")
    except Exception:
        return {"success": False, "error": "Invalid JSON body"}

    if level not in ("tp1", "tp2"):
        return {"success": False, "error": "level must be tp1 or tp2"}

    if not isinstance(price, (int, float)) or price <= 0:
        return {"success": False, "error": "price must be a positive number"}

    cmd_id = write_command("modify_tp", position_id, {"level": level, "price": float(price)})
    symbol = pos.get("symbol", position_id)
    return {"success": True, "message": f"Modify {level.upper()} to {price} queued for {symbol}", "command_id": cmd_id}


@app.get("/api/active-pairs")
async def get_active_pairs(_user: str = Depends(get_current_user)):
    """Get currently active trading pairs with volume details."""
    try:
        active_pairs_file = PROJECT_ROOT / "data" / "active_pairs.json"
        if not active_pairs_file.exists():
            return {"success": True, "data": {"pairs": [], "total": 0, "last_refresh": None, "volume_windows": {}}}

        with open(active_pairs_file, "r") as f:
            data = json.load(f)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Error reading active pairs: {e}")
        return {"success": False, "error": "Internal server error", "data": {"pairs": [], "total": 0}}


@app.post("/api/force-refresh-pairs")
async def force_refresh_pairs(_user: str = Depends(get_current_user)):
    """Signal the bot to force-refresh trading pairs immediately."""
    try:
        flag_file = PROJECT_ROOT / "data" / "force_refresh_pairs.flag"
        flag_file.parent.mkdir(parents=True, exist_ok=True)
        flag_file.write_text("refresh")
        return {"success": True, "message": "Refresh signal sent. Bot will pick up within ~5 seconds."}
    except Exception as e:
        logger.error(f"Error creating force refresh flag: {e}")
        return {"success": False, "error": str(e)}


# ── Supply & Demand Zones ─────────────────────────────────────

@app.get("/api/sd-zones/{symbol}")
async def get_sd_zones(symbol: str, _user: str = Depends(get_current_user)):
    """Get active S/D zones for a symbol across all timeframes."""
    try:
        zones_file = PROJECT_ROOT / "data" / "sd_zones.json"
        if not zones_file.exists():
            return {"success": True, "data": {}}

        with open(zones_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        symbol_upper = symbol.upper()
        if not symbol_upper.endswith("USDT"):
            symbol_upper += "USDT"

        symbol_data = data.get(symbol_upper, {})
        return {"success": True, "data": symbol_data}
    except Exception as e:
        logger.error(f"Error reading S/D zones: {e}")
        return {"success": False, "error": "Internal server error", "data": {}}


@app.get("/api/sd-zones")
async def get_all_sd_zones(_user: str = Depends(get_current_user)):
    """Get S/D zone summary for all symbols."""
    try:
        zones_file = PROJECT_ROOT / "data" / "sd_zones.json"
        if not zones_file.exists():
            return {"success": True, "data": {}, "updated_at": None}

        with open(zones_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        updated_at = data.pop("_updated_at", None)
        return {"success": True, "data": data, "updated_at": updated_at}
    except Exception as e:
        logger.error(f"Error reading S/D zones: {e}")
        return {"success": False, "error": "Internal server error", "data": {}}


# ── Symbol Pause (temporary trading halt per pair) ────────────
PAUSED_SYMBOLS_FILE = DATA_DIR / "paused_symbols.json"


def _load_paused_symbols() -> dict:
    """Load paused symbols, auto-clean expired entries."""
    try:
        if PAUSED_SYMBOLS_FILE.exists():
            with open(PAUSED_SYMBOLS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}
            # Clean expired entries
            now = datetime.now().isoformat()
            active = {k: v for k, v in data.items() if v > now}
            if len(active) != len(data):
                _save_paused_symbols(active)
            return active
    except Exception as e:
        logger.warning(f"[PAUSE] Failed to load paused symbols: {e}")
    return {}


def _save_paused_symbols(data: dict):
    """Save paused symbols to JSON file."""
    try:
        PAUSED_SYMBOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PAUSED_SYMBOLS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"[PAUSE] Failed to save paused symbols: {e}")


@app.get("/api/symbols/paused")
async def get_paused_symbols(_user: str = Depends(get_current_user)):
    """Get currently paused symbols with remaining time."""
    try:
        paused = _load_paused_symbols()
        now = datetime.now()
        result = {}
        for symbol, expiry_str in paused.items():
            expiry = datetime.fromisoformat(expiry_str)
            remaining = (expiry - now).total_seconds()
            if remaining > 0:
                result[symbol] = {
                    "expiry": expiry_str,
                    "remaining_seconds": int(remaining),
                    "remaining_display": f"{int(remaining // 3600)}h {int((remaining % 3600) // 60)}m",
                }
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"[PAUSE] Error: {e}")
        return {"success": False, "error": str(e)}


@app.post("/api/symbols/{symbol}/pause")
async def pause_symbol(symbol: str, request: Request, _user: str = Depends(get_current_user)):
    """Pause trading for a symbol for N hours (default 8)."""
    try:
        body = await request.json()
        hours = body.get("hours", 8)
        hours = max(1, min(24, hours))  # clamp 1-24h

        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"

        paused = _load_paused_symbols()
        expiry = datetime.now() + timedelta(hours=hours)
        paused[sym] = expiry.isoformat()
        _save_paused_symbols(paused)

        logger.info(f"[PAUSE] {sym} paused for {hours}h until {expiry.isoformat()}")
        return {"success": True, "message": f"{sym} paused for {hours}h", "expiry": expiry.isoformat()}
    except Exception as e:
        logger.error(f"[PAUSE] Error pausing {symbol}: {e}")
        return {"success": False, "error": str(e)}


@app.delete("/api/symbols/{symbol}/pause")
async def unpause_symbol(symbol: str, _user: str = Depends(get_current_user)):
    """Unpause (resume) trading for a symbol."""
    try:
        sym = symbol.upper()
        if not sym.endswith("USDT"):
            sym += "USDT"

        paused = _load_paused_symbols()
        if sym in paused:
            del paused[sym]
            _save_paused_symbols(paused)
            logger.info(f"[PAUSE] {sym} unpaused")
            return {"success": True, "message": f"{sym} unpaused"}
        return {"success": True, "message": f"{sym} was not paused"}
    except Exception as e:
        logger.error(f"[PAUSE] Error unpausing {symbol}: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/stats/profit")
async def get_profit_stats(
    period: str = "daily",
    time_range: str = "30d",
    _user: str = Depends(get_current_user),
):
    """Get profit stats aggregated by period.

    time_range='all': SQLite closed_trades (permanent, all-time data).
    time_range='30d': OKX bills endpoint (matches Trading Calendar exactly).
    Fallback: position history + closed_trades backfill.
    """
    try:
        from collections import defaultdict
        from datetime import datetime as dt, timedelta, timezone as _tz2
        from src.trading.core.config import TIMEZONE_OFFSET
        _user_tz = _tz2(timedelta(hours=TIMEZONE_OFFSET))

        # ── All Time: use SQLite closed_trades (ground truth) ──
        if time_range == "all" and trades_db:
            sqlite_rows = trades_db.get_all_closed_for_lookup()
            if sqlite_rows:
                buckets: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})
                for row in sqlite_rows:
                    # closed_trades stores local time in close_time
                    ct = row.get("close_time", "") or row.get("timestamp", "")
                    pnl = row.get("pnl_usd", 0) or 0
                    if not ct:
                        continue
                    try:
                        d = dt.fromisoformat(ct.replace("Z", "+00:00"))
                        # close_time in closed_trades is local time (no tz info)
                        # just parse as-is for bucketing
                        if d.tzinfo is not None:
                            d = d.astimezone(_user_tz)
                    except (ValueError, TypeError):
                        continue

                    if period == "monthly":
                        key = d.strftime("%Y-%m")
                    elif period == "weekly":
                        monday = d - timedelta(days=d.weekday())
                        key = monday.strftime("%Y-%m-%d")
                    else:
                        key = d.strftime("%Y-%m-%d")

                    buckets[key]["pnl"] += pnl
                    buckets[key]["count"] += 1

                result = []
                for key in sorted(buckets.keys()):
                    b = buckets[key]
                    result.append({
                        "time": key,
                        "pnl": round(b["pnl"], 2),
                        "count": b["count"],
                        "timestamp": f"{key}T00:00:00",
                    })
                logger.info(f"[PROFIT-CHART] All Time from SQLite: {len(result)} buckets, {sum(b['count'] for b in buckets.values())} trades")
                return {"success": True, "data": result, "source": "sqlite"}

        # ── 30d / default: OKX bills endpoint (accurate) ──
        daily_pnl = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_daily_pnl
        )
        if daily_pnl:
            buckets: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})
            for day in daily_pnl:
                date_str = day.get("date", "")
                if not date_str:
                    continue
                try:
                    d = dt.strptime(date_str, "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

                if period == "monthly":
                    key = d.strftime("%Y-%m")
                elif period == "weekly":
                    monday = d - timedelta(days=d.weekday())
                    key = monday.strftime("%Y-%m-%d")
                else:
                    key = date_str

                buckets[key]["pnl"] += day.get("net_pnl", 0)
                buckets[key]["count"] += day.get("count", 0)

            result = []
            for key in sorted(buckets.keys()):
                b = buckets[key]
                result.append({
                    "time": key,
                    "pnl": round(b["pnl"], 2),
                    "count": b["count"],
                    "timestamp": f"{key}T00:00:00",
                })
            return {"success": True, "data": result, "source": "bills"}

        # ── Fallback: position history + backfill ──
        history = _fetch_position_history()
        if history:
            lookup = _build_entry_type_lookup()
            used_ids: set[str] = set()

            trade_rows: list[dict] = []
            for h in history:
                pnl = h.get("realized_pnl", 0)
                ct = h.get("close_time", "")
                all_matches = _match_all_positions(
                    lookup, h["symbol"], h.get("open_time", ""), ct,
                    used_ids=used_ids,
                )
                if len(all_matches) > 1:
                    tm = sum(m.get("margin", 0) for m in all_matches) or 1
                    for m in all_matches:
                        trade_rows.append({
                            "close_time": m.get("close_time", ct),
                            "pnl": round(pnl * m.get("margin", 0) / tm, 4),
                        })
                else:
                    trade_rows.append({"close_time": ct, "pnl": pnl})

            for entry in lookup:
                if entry["position_id"] in used_ids:
                    continue
                if not entry.get("close_time"):
                    continue
                if entry.get("close_time", "") <= BACKFILL_CUTOFF:
                    continue
                trade_rows.append({
                    "close_time": entry["close_time"],
                    "pnl": entry.get("pnl_usd", 0) or 0,
                })

            buckets: dict[str, dict] = defaultdict(lambda: {"pnl": 0.0, "count": 0})
            for row in trade_rows:
                ct = row.get("close_time", "")
                if not ct:
                    continue
                try:
                    d = dt.fromisoformat(ct.replace("+00:00", "+00:00"))
                    if d.tzinfo is None:
                        d = d.replace(tzinfo=_tz2.utc)
                    d = d.astimezone(_user_tz)
                except (ValueError, TypeError):
                    continue

                if period == "monthly":
                    key = d.strftime("%Y-%m")
                elif period == "weekly":
                    monday = d - timedelta(days=d.weekday())
                    key = monday.strftime("%Y-%m-%d")
                else:
                    key = d.strftime("%Y-%m-%d")

                buckets[key]["pnl"] += row["pnl"]
                buckets[key]["count"] += 1

            result = []
            for key in sorted(buckets.keys()):
                b = buckets[key]
                result.append({
                    "time": key,
                    "pnl": round(b["pnl"], 2),
                    "count": b["count"],
                    "timestamp": f"{key}T00:00:00",
                })
            return {"success": True, "data": result}

        # Fallback
        stats = position_reader.get_profit_stats(period)
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"Error getting profit stats: {e}")
        return {"success": False, "error": str(e)}


# ── WebSocket ───────────────────────────────────────────────────


class ConnectionManager:
    """Manage WebSocket connections."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WS connected, active={len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WS disconnected, active={len(self.active_connections)}")

    async def broadcast(self, data: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(data)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            if conn in self.active_connections:
                self.active_connections.remove(conn)
                logger.info(f"Removed dead WS, active={len(self.active_connections)}")


manager = ConnectionManager()

# Shared notification queue (read once, broadcast to all)
_notif_file = PROJECT_ROOT / "data" / "web_notifications.json"
_notif_last_mtime = 0.0


async def _check_and_broadcast_notifications():
    """Read bot notification queue, broadcast to ALL clients, then clear."""
    global _notif_last_mtime
    try:
        if not _notif_file.exists():
            return
        mtime = _notif_file.stat().st_mtime
        if mtime <= _notif_last_mtime:
            return
        _notif_last_mtime = mtime

        raw = _notif_file.read_text(encoding="utf-8").strip()
        if not raw:
            return
        notifications = json.loads(raw)
        if not notifications:
            return

        # Broadcast each notification to ALL connected clients
        for notif in notifications:
            await manager.broadcast({
                "type": "notification",
                "level": notif.get("level", "info"),
                "message": notif.get("message", ""),
                "symbol": notif.get("symbol", ""),
                "timestamp": notif.get("timestamp", ""),
            })

        # Clear queue and update mtime tracking AFTER clear
        _notif_file.write_text("[]", encoding="utf-8")
        _notif_last_mtime = _notif_file.stat().st_mtime

    except Exception as e:
        logger.debug(f"Notification check error: {e}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(default=None)):
    """WebSocket endpoint for real-time position updates.
    Requires ?token=<jwt> query param for authentication.
    """
    # Validate token before accepting
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    username = verify_token(token)
    if not username:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return

    await manager.connect(websocket)

    async def _send_snapshot():
        """WS only sends real-time data: positions, balance, unrealized PNL, margin.
        PNL/fees/trade stats are handled by REST /api/stats (bills-based, accurate)."""
        try:
            positions = position_reader.get_open_positions()

            # Real-time stats from exchange (balance, unrealized PNL, margin)
            stats = {}
            balance, exchange_pnl = _fetch_exchange_data()
            if balance > 0:
                stats["balance"] = round(balance, 2)
                stats["growth_pct"] = round((balance - INITIAL_DEPOSIT) / INITIAL_DEPOSIT * 100, 2)
            if exchange_pnl:
                total_upnl = sum(d["unrealized_pnl"] for d in exchange_pnl.values())
                total_margin = sum(d["margin"] for d in exchange_pnl.values())
                stats["unrealized_pnl"] = round(total_upnl, 2)
                stats["total_margin"] = round(total_margin, 2)
                _inject_exchange_pnl(positions, exchange_pnl)

            stats["open_count"] = len(positions)
        except Exception as e:
            logger.error(f"WS data read error: {e}")
            positions = []
            stats = {}
        await websocket.send_json({
            "type": "update",
            "timestamp": datetime.now().isoformat(),
            "positions": positions,
            "stats": stats,
        })

    async def _push_loop():
        """Send full updates every 2s + check for bot notifications."""
        while True:
            await _send_snapshot()
            await _check_and_broadcast_notifications()
            await asyncio.sleep(2)

    async def _recv_loop():
        """Listen for client messages (e.g. manual refresh)."""
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "refresh":
                    await _send_snapshot()
            except Exception:
                pass

    try:
        await asyncio.gather(_push_loop(), _recv_loop())
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# ── SPA Fallback (production) ────────────────────────────────────
# Mount static files and SPA fallback LAST so they don't interfere with API routes

if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():

    @app.get("/assets/{file_path:path}")
    async def serve_asset(file_path: str):
        """Serve hashed static assets with long cache (Vite filenames include content hash)."""
        asset = FRONTEND_DIST / "assets" / file_path
        if asset.exists() and asset.is_file():
            return FileResponse(
                str(asset),
                headers={"Cache-Control": "public, max-age=31536000, immutable"},
            )
        return FileResponse(
            str(FRONTEND_DIST / "index.html"),
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """Serve static files from dist/ if they exist, otherwise index.html for SPA routing."""
        # Check if a real file exists in dist (e.g., logo.jpg, favicon.ico)
        if full_path and not full_path.startswith("api/"):
            file_path = FRONTEND_DIST / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(
                    str(file_path),
                    headers={"Cache-Control": "public, max-age=3600"},
                )
        # SPA fallback — serve index.html with no-cache
        return FileResponse(
            str(FRONTEND_DIST / "index.html"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )


if __name__ == "__main__":
    import uvicorn

    use_reload = os.environ.get("DASHBOARD_RELOAD", "").lower() in ("1", "true")
    uvicorn.run(
        "web.backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=use_reload,
        reload_dirs=[str(Path(__file__).parent)] if use_reload else None,
    )
