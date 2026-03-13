"""Backtest API endpoints with async job pattern to avoid proxy timeouts."""

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from web.backend.auth import get_current_user, TokenPayload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backtest", tags=["backtest"])

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OHLCV_DIR = PROJECT_ROOT / "data" / "ohlcv"

# ── In-memory job store ──────────────────────────────────────────
_jobs: dict[str, dict] = {}
_MAX_JOBS = 20  # auto-cleanup oldest when exceeded


class BacktestRequest(BaseModel):
    symbol: str = Field(default="BTCUSDT")
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    initial_balance: float = Field(default=10000)
    enable_divergence: bool = Field(default=True)
    config_overrides: Optional[dict] = Field(default=None, description="Custom config for this run only")


@router.get("/symbols")
async def get_available_symbols(user: TokenPayload = Depends(get_current_user)):
    """Return symbols that have cached OHLCV data."""
    symbols = set()
    if OHLCV_DIR.exists():
        for f in OHLCV_DIR.glob("*_15m.parquet"):
            sym = f.stem.replace("_15m", "")
            if (OHLCV_DIR / f"{sym}_1h.parquet").exists() and \
               (OHLCV_DIR / f"{sym}_4h.parquet").exists():
                symbols.add(sym)
    return {"success": True, "data": sorted(symbols)}


def _categorize_close_type(close_type: str) -> str:
    """Map raw close_type to display bucket."""
    ct = close_type.upper()
    if ct.startswith("TP1"):
        return "TP1"
    if ct.startswith("TP2"):
        return "TP2"
    if "HARD_SL" in ct:
        return "Hard SL"
    if "CHANDELIER" in ct:
        return "Chandelier SL"
    if ct == "END_OF_BACKTEST":
        return "End of Backtest"
    return "Other"


ENTRY_TYPE_LABELS = {
    "STANDARD_M15": "Std M15",
    "STANDARD_H1": "Std H1",
    "STANDARD_H4": "Std H4",
    "EMA610_H1": "EMA610 H1",
    "EMA610_H4": "EMA610 H4",
}


def _build_exit_stats(trades: list) -> list:
    """Aggregate trades by exit type category, with sub-breakdown by entry_type."""
    # Top-level buckets + per-entry_type sub-buckets
    buckets: dict[str, dict] = {}
    sub_buckets: dict[str, dict[str, dict]] = {}

    for t in trades:
        cat = _categorize_close_type(t.get("close_type", "UNKNOWN"))
        et = t.get("entry_type", "STANDARD_M15")
        pnl = t.get("pnl", 0)
        roi = t.get("pnl_percent", 0)
        is_win = pnl > 0

        # Top-level
        if cat not in buckets:
            buckets[cat] = {"count": 0, "wins": 0, "total_pnl": 0.0, "total_roi": 0.0}
        b = buckets[cat]
        b["count"] += 1
        if is_win:
            b["wins"] += 1
        b["total_pnl"] += pnl
        b["total_roi"] += roi

        # Sub-level by entry_type
        if cat not in sub_buckets:
            sub_buckets[cat] = {}
        if et not in sub_buckets[cat]:
            sub_buckets[cat][et] = {"count": 0, "wins": 0, "total_pnl": 0.0, "total_roi": 0.0}
        sb = sub_buckets[cat][et]
        sb["count"] += 1
        if is_win:
            sb["wins"] += 1
        sb["total_pnl"] += pnl
        sb["total_roi"] += roi

    total = len(trades) or 1
    result = []
    for cat, b in buckets.items():
        n = b["count"]

        # Build sub-rows sorted by count desc
        subs = []
        for et, sb in sorted(sub_buckets.get(cat, {}).items(), key=lambda x: x[1]["count"], reverse=True):
            sn = sb["count"]
            subs.append({
                "entry_type": ENTRY_TYPE_LABELS.get(et, et),
                "count": sn,
                "pct_of_parent": round(sn / n * 100, 1) if n else 0,
                "win_rate": round(sb["wins"] / sn * 100, 1) if sn else 0,
                "avg_pnl": round(sb["total_pnl"] / sn, 2) if sn else 0,
                "total_pnl": round(sb["total_pnl"], 2),
                "avg_roi": round(sb["total_roi"] / sn, 1) if sn else 0,
            })

        result.append({
            "type": cat,
            "count": n,
            "pct_of_total": round(n / total * 100, 1),
            "win_rate": round(b["wins"] / n * 100, 1) if n else 0,
            "avg_pnl": round(b["total_pnl"] / n, 2) if n else 0,
            "total_pnl": round(b["total_pnl"], 2),
            "avg_roi": round(b["total_roi"] / n, 1) if n else 0,
            "by_entry_type": subs,
        })

    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def _run_backtest_sync(symbol: str, initial_balance: float, enable_divergence: bool,
                       start_date: str, end_date: str | None,
                       config_overrides: dict | None = None) -> dict:
    """Run backtest in sync context (for thread pool)."""
    from src.trading.backtest.engine import FuturesBacktester

    backtester = FuturesBacktester(
        symbols=[symbol],
        initial_balance=initial_balance,
        enable_divergence=enable_divergence,
        config_overrides=config_overrides,
    )

    result = backtester.backtest_with_chart_data(
        start_date=start_date,
        end_date=end_date,
    )

    summary = {
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": round(result.win_rate, 1),
        "total_pnl": round(result.total_pnl, 2),
        "total_fees": round(result.total_fees, 2),
        "max_drawdown": round(result.max_drawdown, 2),
        "profit_factor": round(result.profit_factor, 2) if result.profit_factor != float('inf') else 999.99,
        "risk_reward": round(result.risk_reward, 2) if result.risk_reward != float('inf') else 999.99,
        "avg_win": round(result.avg_win, 2),
        "avg_loss": round(result.avg_loss, 2),
        "initial_balance": initial_balance,
        "final_balance": round(backtester.balance, 2),
        "return_pct": round((backtester.balance - initial_balance) / initial_balance * 100, 1),
        "tp1_hits": result.tp1_hits,
        "tp2_hits": result.tp2_hits,
        "exit_stats": _build_exit_stats(result.trades),
    }

    return {"summary": summary, "chart": result.chart_json or {}}


def _cleanup_old_jobs():
    """Remove oldest finished jobs when store exceeds max."""
    if len(_jobs) <= _MAX_JOBS:
        return
    finished = [(jid, j) for jid, j in _jobs.items() if j["status"] != "running"]
    finished.sort(key=lambda x: x[1].get("started_at", 0))
    while len(_jobs) > _MAX_JOBS and finished:
        jid, _ = finished.pop(0)
        _jobs.pop(jid, None)


def _extract_chart_for_tf(chart: dict, timeframe: str) -> dict:
    """Extract chart data for a specific timeframe from full chart result."""
    tf_data = chart.get("timeframes", {}).get(timeframe)
    if not tf_data:
        return {}
    return {
        "timeframe": timeframe,
        "candles": tf_data.get("candles", []),
        "indicators": tf_data.get("indicators", {}),
        "trades": chart.get("trades", []),
        "available_timeframes": chart.get("available_timeframes", []),
        "default_timeframe": chart.get("default_timeframe", "1h"),
    }


async def _run_job(job_id: str, req: BacktestRequest):
    """Run backtest in background thread, store result in job store."""
    try:
        data = await asyncio.to_thread(
            _run_backtest_sync,
            req.symbol, req.initial_balance, req.enable_divergence,
            req.start_date, req.end_date, req.config_overrides,
        )
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["result"] = data
        _jobs[job_id]["finished_at"] = time.time()
    except Exception as e:
        logger.error(f"Backtest job {job_id} failed: {e}", exc_info=True)
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)
        _jobs[job_id]["finished_at"] = time.time()


@router.post("/run")
async def run_backtest(req: BacktestRequest, user: TokenPayload = Depends(get_current_user)):
    """Start backtest as background job, return job ID immediately."""
    _cleanup_old_jobs()

    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "status": "running",
        "started_at": time.time(),
        "symbol": req.symbol,
        "start_date": req.start_date,
        "end_date": req.end_date,
    }

    # Fire and forget — runs in background
    asyncio.create_task(_run_job(job_id, req))

    return {"success": True, "data": {"job_id": job_id, "status": "running"}}


@router.get("/status/{job_id}")
async def get_backtest_status(
    job_id: str,
    timeframe: Optional[str] = Query(default=None, description="15m, 1h, or 4h"),
    user: TokenPayload = Depends(get_current_user),
):
    """Poll backtest job status. Returns chart for requested timeframe when completed."""
    job = _jobs.get(job_id)
    if not job:
        return {"success": False, "error": "Job not found"}

    if job["status"] == "running":
        elapsed = time.time() - job["started_at"]
        return {
            "success": True,
            "data": {
                "job_id": job_id,
                "status": "running",
                "elapsed": round(elapsed, 1),
            },
        }

    if job["status"] == "failed":
        return {"success": False, "error": job.get("error", "Unknown error")}

    # completed — extract chart for requested TF
    result = job["result"]
    chart = result.get("chart", {})
    tf = timeframe or chart.get("default_timeframe", "1h")
    chart_data = _extract_chart_for_tf(chart, tf)

    return {"success": True, "data": {
        "job_id": job_id,
        "status": "completed",
        "elapsed": round(job.get("finished_at", 0) - job["started_at"], 1),
        "summary": result.get("summary", {}),
        "chart": chart_data,
    }}
