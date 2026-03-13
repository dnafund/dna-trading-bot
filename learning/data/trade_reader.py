"""Read-only access to positions.json — parse trades into frozen dataclasses."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trade:
    """Immutable trade record parsed from positions.json."""

    position_id: str
    symbol: str
    side: str
    entry_type: str
    entry_price: float
    pnl_usd: float
    roi_percent: float
    leverage: int
    margin: float
    close_reason: str
    tp1_closed: bool
    tp2_closed: bool
    entry_time: Optional[datetime]
    close_time: Optional[datetime]
    duration_hours: float
    chandelier_sl: Optional[float]
    trailing_sl: Optional[float]
    stop_loss: Optional[float]
    realized_pnl: float
    fees: float
    status: str


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string, return None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def _parse_float(value, default: float = 0.0) -> float:
    """Safely parse float from JSON value."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _calc_duration(entry: Optional[datetime], close: Optional[datetime]) -> float:
    """Calculate trade duration in hours."""
    if not entry or not close:
        return 0.0
    delta = close - entry
    return max(delta.total_seconds() / 3600.0, 0.0)


class TradeReader:
    """Read-only access to positions.json."""

    def __init__(self, positions_path: Path):
        self._path = positions_path

    def _load_raw(self) -> dict:
        """Load raw JSON from positions.json."""
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error("Failed to read positions.json: %s", e)
            return {}

    def _parse_trade(self, key: str, data: dict) -> Optional[Trade]:
        """Parse a single position dict into a Trade dataclass."""
        if key.startswith("_"):
            return None

        status = data.get("status", "OPEN")
        close_reason = data.get("close_reason", "")
        if status != "CLOSED" or not close_reason:
            return None

        # Support both live positions.json and backtest export formats
        entry_time = _parse_datetime(
            data.get("entry_time") or data.get("created_at") or data.get("timestamp")
        )
        close_time = _parse_datetime(
            data.get("close_time") or data.get("closed_at")
        )
        entry_fee = _parse_float(data.get("entry_fee"))
        exit_fees = _parse_float(data.get("total_exit_fees"))
        direct_fees = _parse_float(data.get("fees"))

        # PNL: try pnl_usd first, then realized_pnl (backtest format)
        pnl = _parse_float(data.get("pnl_usd")) or _parse_float(data.get("realized_pnl"))

        return Trade(
            position_id=data.get("position_id", key),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            entry_type=data.get("entry_type", "unknown"),
            entry_price=_parse_float(data.get("entry_price")),
            pnl_usd=pnl,
            roi_percent=_parse_float(data.get("roi_percent")),
            leverage=int(_parse_float(data.get("leverage"), 1)),
            margin=_parse_float(data.get("margin")),
            close_reason=close_reason,
            tp1_closed=bool(data.get("tp1_closed", False)),
            tp2_closed=bool(data.get("tp2_closed", False)),
            entry_time=entry_time,
            close_time=close_time,
            duration_hours=_calc_duration(entry_time, close_time),
            chandelier_sl=data.get("chandelier_sl"),
            trailing_sl=data.get("trailing_sl"),
            stop_loss=data.get("stop_loss"),
            realized_pnl=_parse_float(data.get("realized_pnl")),
            fees=direct_fees if direct_fees else entry_fee + exit_fees,
            status=status,
        )

    def load_closed_trades(self) -> list[Trade]:
        """Parse all CLOSED positions into Trade objects."""
        raw = self._load_raw()
        trades = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            trade = self._parse_trade(key, value)
            if trade is not None:
                trades.append(trade)
        return sorted(trades, key=lambda t: t.close_time or datetime.min)

    def load_trades_since(self, since: datetime) -> list[Trade]:
        """Get closed trades since a specific date."""
        return [
            t for t in self.load_closed_trades()
            if t.close_time and t.close_time >= since
        ]

    def load_recent_trades(self, days: int = 7) -> list[Trade]:
        """Get closed trades from the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return self.load_trades_since(cutoff)

    def get_paper_balance(self) -> float:
        """Read current paper balance."""
        raw = self._load_raw()
        return _parse_float(raw.get("_paper_balance"), 0.0)
