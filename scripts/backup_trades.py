"""Backup closed_trades and okx_history from SQLite to JSON files.

Run manually or automatically via pre-push hook.
JSON backups are git-friendly and can be restored even if .db is corrupted.
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"
BACKUP_DIR = Path(__file__).parent.parent / "data" / "backups"


def backup():
    if not DB_PATH.exists():
        print("No trades.db found, skipping backup")
        return

    BACKUP_DIR.mkdir(exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Backup closed_trades
    rows = conn.execute("SELECT * FROM closed_trades ORDER BY close_time").fetchall()
    closed_data = [dict(r) for r in rows]

    closed_path = BACKUP_DIR / "closed_trades.json"
    with open(closed_path, "w", encoding="utf-8") as f:
        json.dump(closed_data, f, indent=2, default=str)

    # Backup okx_history
    rows = conn.execute("SELECT * FROM okx_history ORDER BY close_time").fetchall()
    okx_data = [dict(r) for r in rows]

    okx_path = BACKUP_DIR / "okx_history.json"
    with open(okx_path, "w", encoding="utf-8") as f:
        json.dump(okx_data, f, indent=2, default=str)

    conn.close()

    print(f"Backup complete: {len(closed_data)} closed_trades, {len(okx_data)} okx_history")
    print(f"  -> {closed_path}")
    print(f"  -> {okx_path}")


if __name__ == "__main__":
    backup()
