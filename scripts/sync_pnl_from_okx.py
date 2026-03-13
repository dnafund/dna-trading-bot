"""
Sync closed positions PNL with OKX actual realized PNL.

OKX merges all positions for same symbol into one. So matching strategy:
1. Group bot positions by (symbol, side, close_date_hour)
2. Find OKX history entry for that symbol+side within time range
3. If single bot position -> assign full OKX PNL
4. If multiple bot positions -> split OKX PNL proportionally by margin

Usage:
  python scripts/sync_pnl_from_okx.py          # Dry run (show changes)
  python scripts/sync_pnl_from_okx.py --apply   # Apply changes to positions.json
"""

import os
import sys
import json
import time
import shutil
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from src.trading.exchanges.okx import OKXFuturesClient

logging.basicConfig(level=logging.WARNING)

POSITIONS_FILE = project_root / "data" / "positions.json"
BACKUP_FILE = project_root / "data" / "positions_pre_sync_backup.json"

# Bot records close_time using datetime.now() = local time
# OKX returns UTC. Adjust bot times to UTC for matching.
BOT_TZ_OFFSET_HOURS = 7  # UTC+7 (Vietnam)


def load_positions():
    path = str(POSITIONS_FILE)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.strip():
        raise RuntimeError(f"positions.json is empty at {path}")
    return json.loads(content)


def fetch_all_okx_history(client, max_pages=10):
    """Fetch all position history from OKX with pagination + retry."""
    all_history = []
    after = None

    for page in range(max_pages):
        for attempt in range(3):
            try:
                params = {"instType": "SWAP", "limit": "100"}
                if after:
                    params["after"] = after

                result = client.exchange.privateGetAccountPositionsHistory(params=params)
                positions = result.get("data", [])

                if not positions:
                    print(f"  Page {page + 1}: no more data")
                    return all_history

                for p in positions:
                    inst_id = p.get("instId", "")
                    symbol = inst_id.replace("-SWAP", "").replace("-", "")

                    close_ts = p.get("uTime", "")
                    close_time = None
                    if close_ts:
                        close_time = datetime.fromtimestamp(
                            int(close_ts) / 1000, tz=timezone.utc
                        )

                    open_ts = p.get("cTime", "")
                    open_time = None
                    if open_ts:
                        open_time = datetime.fromtimestamp(
                            int(open_ts) / 1000, tz=timezone.utc
                        )

                    all_history.append({
                        "symbol": symbol,
                        "side": p.get("direction", ""),
                        "realized_pnl": float(p.get("realizedPnl", 0) or 0),
                        "fee": float(p.get("fee", 0) or 0),
                        "funding_fee": float(p.get("fundingFee", 0) or 0),
                        "close_price": float(p.get("closeAvgPx", 0) or 0),
                        "open_price": float(p.get("openAvgPx", 0) or 0),
                        "close_time": close_time,
                        "open_time": open_time,
                        "pos_id": p.get("posId", ""),
                    })

                last_pos_id = positions[-1].get("posId", "")
                if last_pos_id and last_pos_id != after:
                    after = last_pos_id
                else:
                    return all_history

                print(f"  Page {page + 1}: {len(positions)} entries (total: {len(all_history)})")
                time.sleep(2)  # Rate limit — OKX needs longer delay
                break  # Success, exit retry loop

            except Exception as e:
                print(f"  Page {page + 1} attempt {attempt + 1}: {e}")
                if attempt < 2:
                    time.sleep(5)
                else:
                    print(f"  Giving up on page {page + 1}")
                    return all_history

    return all_history


def match_positions(closed_positions, okx_history):
    """
    Match bot positions with OKX history.

    Strategy: allow ONE OKX entry to match MULTIPLE bot positions
    (because OKX merges same-symbol positions).
    Match by symbol + side + time proximity (within 30 min).
    """
    matches = []
    unmatched_bot = []

    # Index OKX history by (symbol, side) for fast lookup
    okx_by_key = defaultdict(list)
    for idx, h in enumerate(okx_history):
        key = (h["symbol"], h["side"])
        okx_by_key[key].append((idx, h))

    for pos in closed_positions:
        bot_symbol = pos["symbol"]
        bot_side_okx = "long" if pos["side"] == "BUY" else "short"

        bot_close_str = pos.get("close_time", "")
        if not bot_close_str:
            unmatched_bot.append(pos)
            continue

        try:
            bot_close = datetime.fromisoformat(bot_close_str)
            if bot_close.tzinfo is None:
                # Bot uses datetime.now() = local time, convert to UTC
                bot_close = bot_close.replace(tzinfo=timezone(timedelta(hours=BOT_TZ_OFFSET_HOURS)))
                bot_close = bot_close.astimezone(timezone.utc)
        except (ValueError, TypeError):
            unmatched_bot.append(pos)
            continue

        key = (bot_symbol, bot_side_okx)
        candidates = okx_by_key.get(key, [])

        # Find best match within 30 min tolerance
        best_match = None
        best_idx = None
        best_diff = timedelta(hours=999)

        for idx, h in candidates:
            if not h["close_time"]:
                continue
            time_diff = abs(bot_close - h["close_time"])
            if time_diff < best_diff and time_diff <= timedelta(minutes=30):
                best_diff = time_diff
                best_match = h
                best_idx = idx

        if best_match:
            matches.append({
                "position": pos,
                "okx": best_match,
                "okx_idx": best_idx,
                "time_diff_sec": best_diff.total_seconds(),
            })
        else:
            unmatched_bot.append(pos)

    return matches, unmatched_bot


def split_merged_pnl(matches):
    """
    When multiple bot positions match the same OKX entry,
    split OKX PNL proportionally by margin.
    """
    # Group matches by OKX entry index
    by_okx = defaultdict(list)
    for m in matches:
        by_okx[m["okx_idx"]].append(m)

    results = []
    for okx_idx, group in by_okx.items():
        okx = group[0]["okx"]
        total_okx_pnl = okx["realized_pnl"]
        total_okx_fee = abs(okx["fee"]) + abs(okx["funding_fee"])

        if len(group) == 1:
            # Single match — full PNL
            m = group[0]
            results.append({
                **m,
                "assigned_pnl": total_okx_pnl,
                "assigned_fees": total_okx_fee,
                "share": 1.0,
                "merged_count": 1,
            })
        else:
            # Multiple bot positions for one OKX entry — split by margin
            total_margin = sum(m["position"].get("margin", 100) for m in group)
            for m in group:
                margin = m["position"].get("margin", 100)
                share = margin / total_margin if total_margin > 0 else 1.0 / len(group)
                results.append({
                    **m,
                    "assigned_pnl": total_okx_pnl * share,
                    "assigned_fees": total_okx_fee * share,
                    "share": share,
                    "merged_count": len(group),
                })

    return results


def main():
    apply_mode = "--apply" in sys.argv

    print("=" * 80)
    print("PNL Sync: Bot positions -> OKX actual PNL")
    print("=" * 80)

    # Load positions
    data = load_positions()
    closed = []
    for pid, pos in data.items():
        if pid.startswith("_"):
            continue
        if pos.get("status") == "CLOSED":
            pos["_pid"] = pid
            closed.append(pos)

    print(f"Closed positions in bot: {len(closed)}")

    # Connect to OKX
    print("\nFetching OKX position history...")
    load_dotenv(project_root / ".env")

    api_key = os.getenv("OKX_API_KEY")
    api_secret = os.getenv("OKX_API_SECRET")
    passphrase = os.getenv("OKX_PASSPHRASE")

    if not api_key or not api_secret:
        print("ERROR: Set OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE in .env")
        return

    client = OKXFuturesClient(api_key, api_secret, passphrase)
    okx_history = fetch_all_okx_history(client)
    print(f"Total OKX history entries: {len(okx_history)}")

    if okx_history:
        oldest = min(h["close_time"] for h in okx_history if h["close_time"])
        newest = max(h["close_time"] for h in okx_history if h["close_time"])
        print(f"OKX history range: {oldest:%Y-%m-%d %H:%M} to {newest:%Y-%m-%d %H:%M} UTC")

    # Match (allow shared OKX entries)
    print(f"\nMatching (tolerance: 30 min, merged positions supported)...")
    matches, unmatched = match_positions(closed, okx_history)

    # Split PNL for merged positions
    results = split_merged_pnl(matches)

    print(f"Matched: {len(results)} bot positions -> {len(set(r['okx_idx'] for r in results))} OKX entries")
    print(f"Unmatched: {len(unmatched)}")

    # Show changes
    changes = []
    total_bot_pnl = 0
    total_okx_pnl = 0

    print(f"\n{'=' * 100}")
    print(f"{'Position':<32s} {'Bot PNL':>10s} {'OKX PNL':>10s} {'Diff':>10s}  {'Time':>5s} {'Share':>6s}  Note")
    print(f"{'-' * 100}")

    for r in sorted(results, key=lambda x: abs(x["assigned_pnl"] - x["position"].get("pnl_usd", 0)), reverse=True):
        pos = r["position"]
        bot_pnl = pos.get("pnl_usd", 0)
        okx_pnl = r["assigned_pnl"]
        diff = okx_pnl - bot_pnl
        time_diff = r["time_diff_sec"]
        share = r["share"]
        merged = r["merged_count"]

        total_bot_pnl += bot_pnl
        total_okx_pnl += okx_pnl

        note = ""
        if merged > 1:
            note = f"merged({merged})"
        if abs(diff) > 1.0:
            note += " ** DIFF"
            changes.append(r)
        elif abs(diff) > 0.1:
            note += " * small"
            changes.append(r)

        pid_short = pos["_pid"][:30]
        print(
            f"{pid_short:<32s} ${bot_pnl:>9.2f} ${okx_pnl:>9.2f} ${diff:>9.2f}  "
            f"{time_diff:>4.0f}s {share:>5.0%}  {note}"
        )

    print(f"{'-' * 100}")
    print(f"{'TOTAL':<32s} ${total_bot_pnl:>9.2f} ${total_okx_pnl:>9.2f} ${total_okx_pnl - total_bot_pnl:>9.2f}")
    print(f"\nPositions to update: {len(changes)}")

    if unmatched:
        print(f"\nUnmatched: {len(unmatched)} positions (no OKX history entry found)")

    # Apply changes
    if not changes:
        print("\nNo PNL differences found. Nothing to update.")
        return

    if not apply_mode:
        print(f"\nDry run. Use --apply to update positions.json")
        return

    # Backup
    print(f"\nBacking up to {BACKUP_FILE}...")
    shutil.copy2(POSITIONS_FILE, BACKUP_FILE)

    # Apply
    updated = 0
    for r in changes:
        pos = r["position"]
        pid = pos["_pid"]

        if pid in data:
            old_pnl = data[pid].get("pnl_usd", 0)
            new_pnl = r["assigned_pnl"]
            new_fees = r["assigned_fees"]
            close_price = r["okx"]["close_price"]

            data[pid]["pnl_usd"] = round(new_pnl, 4)
            data[pid]["roi_percent"] = round(
                (new_pnl / data[pid]["margin"]) * 100, 4
            ) if data[pid].get("margin", 0) > 0 else 0
            data[pid]["total_exit_fees"] = round(new_fees, 4)
            if close_price > 0:
                data[pid]["current_price"] = close_price

            updated += 1
            print(f"  {pid[:35]:35s} ${old_pnl:>8.2f} -> ${new_pnl:>8.2f}")

    # Remove _pid keys before saving
    for pid in list(data.keys()):
        if not pid.startswith("_") and isinstance(data[pid], dict):
            data[pid].pop("_pid", None)

    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Updated {updated} positions.")
    print(f"Backup: {BACKUP_FILE}")


if __name__ == "__main__":
    main()
