#!/usr/bin/env python3
"""
One-time migration: positions.json + okx_history_cache.json → SQLite trades.db

Usage:
  python scripts/migrate_to_sqlite.py           # dry run (read-only)
  python scripts/migrate_to_sqlite.py --apply   # apply migration

Run with bot STOPPED.
"""

import json
import shutil
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database.trades_db import TradesDB

DATA_DIR = PROJECT_ROOT / "data"
POSITIONS_FILE = DATA_DIR / "positions.json"
OKX_CACHE_FILE = DATA_DIR / "okx_history_cache.json"
TRADES_DB_FILE = DATA_DIR / "trades.db"
BACKUP_FILE = DATA_DIR / "positions_pre_sqlite_backup.json"


def load_positions() -> dict:
    if not POSITIONS_FILE.exists():
        print(f"[ERROR] {POSITIONS_FILE} not found")
        sys.exit(1)
    with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_okx_cache() -> list:
    if not OKX_CACHE_FILE.exists():
        print(f"[WARN] {OKX_CACHE_FILE} not found, skipping OKX cache migration")
        return []
    with open(OKX_CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    apply = "--apply" in sys.argv
    mode = "APPLY" if apply else "DRY RUN"
    print(f"=== SQLite Migration ({mode}) ===\n")

    # 1. Load positions.json
    raw = load_positions()
    paper_balance = raw.get("_paper_balance", 1000.0)

    active = {}
    closed = []
    for key, val in raw.items():
        if key.startswith("_") or not isinstance(val, dict):
            continue
        status = val.get("status", "OPEN")
        if status in ("OPEN", "PARTIAL_CLOSE"):
            active[key] = val
        else:
            closed.append(val)

    print(f"Positions file: {len(active)} active, {len(closed)} closed")
    print(f"Paper balance: ${paper_balance:,.2f}")

    # 2. Load OKX cache
    okx_records = load_okx_cache()
    print(f"OKX history cache: {len(okx_records)} records")

    # 3. Init SQLite
    db = TradesDB(TRADES_DB_FILE)
    existing_count = db.get_closed_count()
    print(f"Existing trades in DB: {existing_count}")

    # 4. Insert closed trades
    inserted = db.insert_closed_trades_batch(closed)
    print(f"Closed trades inserted: {inserted} (skipped {len(closed) - inserted} duplicates)")

    # 5. Insert OKX history
    okx_inserted = db.upsert_okx_history(okx_records)
    print(f"OKX history upserted: {okx_inserted}")

    # 6. Verify
    final_count = db.get_closed_count()
    stats = db.get_stats()
    print(f"\n--- Post-migration ---")
    print(f"Total closed trades in DB: {final_count}")
    print(f"Total PNL: ${stats['total_pnl']:,.2f}")
    print(f"Win rate: {stats['win_rate']}%")
    print(f"Profit factor: {stats['profit_factor']}")

    if apply:
        # 7. Backup original
        shutil.copy2(POSITIONS_FILE, BACKUP_FILE)
        print(f"\nBackup saved: {BACKUP_FILE}")

        # 8. Rewrite positions.json with active-only
        active_data = {"_paper_balance": paper_balance, **active}
        with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(active_data, f, indent=2, ensure_ascii=False)
        print(f"Positions.json rewritten: {len(active)} active positions + balance")
        print("\n=== Migration complete! ===")
    else:
        print(f"\n[DRY RUN] No files modified. Run with --apply to execute.")


if __name__ == "__main__":
    main()
