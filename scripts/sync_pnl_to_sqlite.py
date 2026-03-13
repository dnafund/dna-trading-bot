#!/usr/bin/env python3
"""
Sync closed_trades PNL from okx_history table (already in SQLite).

No OKX API calls needed — uses okx_history records already saved in trades.db.
OKX realized_pnl is NET PNL (already after fees).

Matching strategy (in order of priority):
  1. Symbol + side + entry_price (within 0.01% tolerance)
  2. Falls back to close_time matching (UTC-normalized, 2-min window)

Usage:
  python scripts/sync_pnl_to_sqlite.py           # dry run
  python scripts/sync_pnl_to_sqlite.py --apply    # apply updates
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.database.trades_db import TradesDB

TRADES_DB = project_root / "data" / "trades.db"

# Local timezone offset (Vietnam = UTC+7)
LOCAL_TZ_OFFSET = timedelta(hours=7)


def normalize_side(side: str) -> str:
    """Normalize side to 'long'/'short' (OKX format)."""
    s = side.strip().lower()
    if s in ("buy", "long"):
        return "long"
    if s in ("sell", "short"):
        return "short"
    return s


def parse_datetime_to_utc(dt_str: str) -> datetime | None:
    """Parse datetime string to UTC datetime. Handles both local and UTC+offset formats."""
    if not dt_str:
        return None
    try:
        # OKX format: "2026-02-25T04:54:39.109000+00:00" (already UTC)
        if "+" in dt_str or dt_str.endswith("Z"):
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        # Bot format: "2026-02-25T11:54:35.451899" (local time, no tz info)
        dt = datetime.fromisoformat(dt_str)
        # Convert local → UTC by subtracting offset
        return dt - LOCAL_TZ_OFFSET
    except (ValueError, TypeError):
        return None


def find_okx_match(trade: dict, okx_list: list[dict], used_indices: set) -> dict | None:
    """Find best OKX match for a closed trade using entry price + symbol + side."""
    symbol = trade["symbol"]
    side = normalize_side(trade["side"])
    entry_price = trade.get("entry_price", 0) or 0
    trade_close_utc = parse_datetime_to_utc(trade.get("close_time", ""))

    best_idx = None
    best_entry = None
    best_score = float("inf")

    for i, okx in enumerate(okx_list):
        if i in used_indices:
            continue
        if okx["symbol"] != symbol or okx["side"] != side:
            continue

        okx_open = okx.get("open_price", 0) or 0
        okx_close_utc = parse_datetime_to_utc(okx.get("close_time", ""))

        # Strategy 1: Entry price match (best — 0.01% tolerance)
        if entry_price > 0 and okx_open > 0:
            price_diff_pct = abs(okx_open - entry_price) / entry_price
            if price_diff_pct < 0.0001:
                # Exact entry price match — verify close time is close too
                if trade_close_utc and okx_close_utc:
                    time_diff = abs((trade_close_utc - okx_close_utc).total_seconds())
                    if time_diff < 120:  # Within 2 minutes
                        if time_diff < best_score:
                            best_score = time_diff
                            best_idx = i
                            best_entry = okx
                else:
                    # No close time to verify, trust entry price match
                    best_idx = i
                    best_entry = okx
                    best_score = 0
                    break

        # Strategy 2: Close time match (fallback — within 2 min window)
        if best_entry is None and trade_close_utc and okx_close_utc:
            time_diff = abs((trade_close_utc - okx_close_utc).total_seconds())
            if time_diff < 120:
                if time_diff < best_score:
                    best_score = time_diff
                    best_idx = i
                    best_entry = okx

    if best_idx is not None:
        used_indices.add(best_idx)
    return best_entry


def main():
    apply = "--apply" in sys.argv
    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== Sync closed_trades PNL from okx_history ({mode}) ===\n")

    db = TradesDB(TRADES_DB)

    # Load both tables
    conn = db._get_conn()
    try:
        closed_rows = conn.execute(
            "SELECT * FROM closed_trades ORDER BY close_time ASC"
        ).fetchall()
        okx_rows = conn.execute(
            "SELECT * FROM okx_history ORDER BY close_time ASC"
        ).fetchall()
    finally:
        conn.close()

    closed = [dict(r) for r in closed_rows]
    okx = [dict(r) for r in okx_rows]

    print(f"closed_trades: {len(closed)} records")
    print(f"okx_history:   {len(okx)} records\n")

    # Match closed_trades to okx_history
    matched = 0
    unmatched = 0
    already_correct = 0
    updates = []
    used_okx_indices: set[int] = set()

    for trade in closed:
        okx_entry = find_okx_match(trade, okx, used_okx_indices)

        if not okx_entry:
            unmatched += 1
            continue

        matched += 1

        okx_pnl = okx_entry["realized_pnl"]
        okx_fees = abs(okx_entry.get("fee", 0)) + abs(okx_entry.get("funding_fee", 0))
        okx_close_price = okx_entry.get("close_price", 0) or 0

        old_pnl = trade.get("pnl_usd", 0) or 0
        margin = trade.get("margin", 0) or 0
        new_roi = round((okx_pnl / margin) * 100, 4) if margin > 0 else 0

        if abs(old_pnl - okx_pnl) > 0.005:
            updates.append({
                "position_id": trade["position_id"],
                "symbol": trade["symbol"],
                "close_reason": trade.get("close_reason", ""),
                "old_pnl": old_pnl,
                "pnl_usd": round(okx_pnl, 4),
                "roi_percent": new_roi,
                "total_exit_fees": round(okx_fees, 4),
                "current_price": okx_close_price,
            })
        else:
            already_correct += 1

    # Report
    print(f"Matched: {matched}/{len(closed)}")
    print(f"  Already correct: {already_correct}")
    print(f"  Need update: {len(updates)}")
    print(f"Unmatched: {unmatched} (no OKX history entry)\n")

    if updates:
        total_old = sum(u["old_pnl"] for u in updates)
        total_new = sum(u["pnl_usd"] for u in updates)

        print(f"{'Symbol':<12s} {'Reason':<16s} {'Bot PNL':>10s} {'OKX PNL':>10s} {'Diff':>10s}")
        print("-" * 65)

        for u in sorted(updates, key=lambda x: abs(x["pnl_usd"] - x["old_pnl"]), reverse=True):
            diff = u["pnl_usd"] - u["old_pnl"]
            print(
                f"{u['symbol']:<12s} {u['close_reason']:<16s} "
                f"${u['old_pnl']:>9.2f} ${u['pnl_usd']:>9.2f} ${diff:>9.2f}"
            )

        print("-" * 65)
        print(f"{'TOTAL':<29s} ${total_old:>9.2f} ${total_new:>9.2f} ${total_new - total_old:>9.2f}")

    if apply and updates:
        conn = db._get_conn()
        try:
            count = 0
            for u in updates:
                conn.execute(
                    """UPDATE closed_trades
                       SET pnl_usd = ?, roi_percent = ?, total_exit_fees = ?, current_price = ?
                       WHERE position_id = ?""",
                    [u["pnl_usd"], u["roi_percent"], u["total_exit_fees"],
                     u["current_price"], u["position_id"]],
                )
                count += 1
            conn.commit()
            print(f"\nUpdated {count} trades in SQLite")
        finally:
            conn.close()

        stats = db.get_stats()
        print(f"New total PNL: ${stats['total_pnl']:,.2f}")
        print(f"Win rate: {stats['win_rate']}%")
    elif not apply and updates:
        print(f"\n[DRY RUN] No changes. Run with --apply to update.")
    else:
        print("All matched trades already have correct PNL. Nothing to update.")


if __name__ == "__main__":
    main()
