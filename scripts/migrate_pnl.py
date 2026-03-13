"""
One-time migration: Update ALL positions.json with OKX actual NET PNL.

What this does:
1. Fetches ALL OKX position history (paginated, no 100-trade limit)
2. Matches each bot position by symbol + side + close_time (±30 min)
3. Handles merged positions (OKX merges same-symbol → split by margin ratio)
4. Updates pnl_usd with NET PNL (realized_pnl + fee + funding_fee)
5. Sets okx_synced = true

Usage:
  python scripts/migrate_pnl.py          # Dry run (show changes)
  python scripts/migrate_pnl.py --apply  # Apply changes to positions.json
"""

import os
import sys
import json
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
BACKUP_FILE = project_root / "data" / "positions_pre_migration_backup.json"

# Bot records close_time using datetime.now() = local time (UTC+7 Vietnam)
# OKX returns UTC. Must adjust bot times to UTC for matching.
BOT_TZ_OFFSET_HOURS = 7


def load_positions():
    with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    if not content.strip():
        raise RuntimeError(f"positions.json is empty at {POSITIONS_FILE}")
    return json.loads(content)


def bot_close_to_utc(close_str: str):
    """Convert bot close_time (local time, naive) to UTC datetime."""
    try:
        dt = datetime.fromisoformat(close_str)
        if dt.tzinfo is None:
            # Bot uses datetime.now() = local time, add timezone then convert to UTC
            dt = dt.replace(tzinfo=timezone(timedelta(hours=BOT_TZ_OFFSET_HOURS)))
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def okx_close_to_utc(close_str: str):
    """Convert OKX close_time (ISO string, already UTC) to UTC datetime."""
    if not close_str:
        return None
    try:
        dt = datetime.fromisoformat(close_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


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

        bot_close = bot_close_to_utc(bot_close_str)
        if not bot_close:
            unmatched_bot.append(pos)
            continue

        key = (bot_symbol, bot_side_okx)
        candidates = okx_by_key.get(key, [])

        # Find best match within 30 min tolerance
        best_match = None
        best_idx = None
        best_diff = timedelta(hours=999)

        for idx, h in candidates:
            okx_close = okx_close_to_utc(h.get("close_time", ""))
            if not okx_close:
                continue
            time_diff = abs(bot_close - okx_close)
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
    split OKX NET PNL proportionally by margin.
    """
    by_okx = defaultdict(list)
    for m in matches:
        by_okx[m["okx_idx"]].append(m)

    results = []
    for okx_idx, group in by_okx.items():
        okx = group[0]["okx"]
        gross_pnl = okx["realized_pnl"]
        raw_fee = okx["fee"]           # negative from OKX
        funding = okx["funding_fee"]   # can be +/-

        # NET PNL = gross + fee (negative) + funding — matches OKX app
        total_net_pnl = gross_pnl + raw_fee + funding
        total_fees = abs(raw_fee) + abs(funding)

        if len(group) == 1:
            m = group[0]
            results.append({
                **m,
                "assigned_pnl": total_net_pnl,
                "assigned_fees": total_fees,
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
                    "assigned_pnl": total_net_pnl * share,
                    "assigned_fees": total_fees * share,
                    "share": share,
                    "merged_count": len(group),
                })

    return results


def main():
    apply_mode = "--apply" in sys.argv

    print("=" * 80)
    print("PNL Migration: Bot positions -> OKX actual NET PNL")
    print("NET PNL = realized_pnl + fee + funding_fee (matches OKX app)")
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

    already_synced = [p for p in closed if p.get("okx_synced")]
    unsynced = [p for p in closed if not p.get("okx_synced")]
    print(f"\nClosed positions: {len(closed)} total")
    print(f"  Already synced: {len(already_synced)}")
    print(f"  Need migration: {len(unsynced)}")

    if not unsynced:
        print("\nAll positions already synced. Nothing to migrate.")
        return

    # Connect to OKX
    print("\nFetching ALL OKX position history (paginated)...")
    load_dotenv(project_root / ".env")

    api_key = os.getenv("OKX_API_KEY")
    api_secret = os.getenv("OKX_API_SECRET")
    passphrase = os.getenv("OKX_PASSPHRASE")

    if not api_key or not api_secret:
        print("ERROR: Set OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE in .env")
        return

    client = OKXFuturesClient(api_key, api_secret, passphrase)
    okx_history = client.get_all_position_history(max_pages=10)
    print(f"Total OKX history entries: {len(okx_history)}")

    if okx_history:
        close_times = [
            okx_close_to_utc(h["close_time"])
            for h in okx_history if h.get("close_time")
        ]
        close_times = [t for t in close_times if t]
        if close_times:
            oldest = min(close_times)
            newest = max(close_times)
            print(f"OKX history range: {oldest:%Y-%m-%d %H:%M} to {newest:%Y-%m-%d %H:%M} UTC")

    # Match
    print(f"\nMatching (tolerance: 30 min, merged positions supported)...")
    matches, unmatched = match_positions(unsynced, okx_history)

    # Split PNL for merged positions
    results = split_merged_pnl(matches)

    unique_okx = len(set(r["okx_idx"] for r in results))
    print(f"Matched: {len(results)} bot positions -> {unique_okx} OKX entries")
    print(f"Unmatched: {len(unmatched)}")

    # Show changes
    print(f"\n{'=' * 110}")
    print(
        f"{'Position':<32s} {'Bot PNL':>10s} {'OKX NET':>10s} {'Diff':>10s}  "
        f"{'Fee':>8s} {'Time':>5s} {'Share':>6s}  Note"
    )
    print(f"{'-' * 110}")

    total_bot_pnl = 0
    total_okx_pnl = 0
    total_fees = 0
    changes = []

    for r in sorted(results, key=lambda x: x["position"].get("close_time", ""), reverse=True):
        pos = r["position"]
        bot_pnl = pos.get("pnl_usd", 0)
        okx_net = r["assigned_pnl"]
        fees = r["assigned_fees"]
        diff = okx_net - bot_pnl
        time_diff = r["time_diff_sec"]
        share = r["share"]
        merged = r["merged_count"]

        total_bot_pnl += bot_pnl
        total_okx_pnl += okx_net
        total_fees += fees

        note = ""
        if merged > 1:
            note = f"merged({merged})"
        if abs(diff) > 1.0:
            note += " ** BIG DIFF"
        elif abs(diff) > 0.01:
            note += " * diff"

        changes.append(r)

        pid_short = pos["_pid"][:30]
        print(
            f"{pid_short:<32s} ${bot_pnl:>9.2f} ${okx_net:>9.2f} ${diff:>9.2f}  "
            f"${fees:>7.2f} {time_diff:>4.0f}s {share:>5.0%}  {note}"
        )

    print(f"{'-' * 110}")
    print(
        f"{'TOTAL':<32s} ${total_bot_pnl:>9.2f} ${total_okx_pnl:>9.2f} "
        f"${total_okx_pnl - total_bot_pnl:>9.2f}  ${total_fees:>7.2f}"
    )
    print(f"\nTotal positions to update: {len(changes)}")

    if unmatched:
        print(f"\n--- Unmatched ({len(unmatched)}) ---")
        for pos in unmatched:
            pid = pos["_pid"][:35]
            pnl = pos.get("pnl_usd", 0)
            ct = pos.get("close_time", "no close_time")
            print(f"  {pid:<35s} ${pnl:>8.2f}  {ct}")

    if not changes:
        print("\nNo positions to update.")
        return

    if not apply_mode:
        print(f"\n*** DRY RUN. Use --apply to update positions.json ***")
        return

    # Backup
    print(f"\nBacking up to {BACKUP_FILE}...")
    shutil.copy2(POSITIONS_FILE, BACKUP_FILE)

    # Apply changes
    updated = 0
    for r in changes:
        pos = r["position"]
        pid = pos["_pid"]

        if pid not in data:
            continue

        old_pnl = data[pid].get("pnl_usd", 0)
        new_pnl = r["assigned_pnl"]
        new_fees = r["assigned_fees"]
        close_price = r["okx"].get("close_price", 0)

        data[pid]["pnl_usd"] = round(new_pnl, 4)
        margin = data[pid].get("margin", 0)
        data[pid]["roi_percent"] = round(
            (new_pnl / margin) * 100, 4
        ) if margin > 0 else 0
        data[pid]["total_exit_fees"] = round(new_fees, 4)
        if close_price and close_price > 0:
            data[pid]["current_price"] = close_price
        data[pid]["okx_synced"] = True

        updated += 1
        print(f"  {pid[:40]:40s} ${old_pnl:>8.2f} -> ${new_pnl:>8.2f}")

    # Remove _pid keys before saving
    for pid in list(data.keys()):
        if not pid.startswith("_") and isinstance(data[pid], dict):
            data[pid].pop("_pid", None)

    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Updated {updated} positions with OKX NET PNL.")
    print(f"Backup: {BACKUP_FILE}")


if __name__ == "__main__":
    main()
