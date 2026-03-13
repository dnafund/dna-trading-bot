"""Restore closed_trades and/or okx_history from JSON backups.

Usage:
    python scripts/restore_trades.py              # restore both tables
    python scripts/restore_trades.py closed_trades # restore only closed_trades
    python scripts/restore_trades.py okx_history   # restore only okx_history
"""
import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"
BACKUP_DIR = Path(__file__).parent.parent / "data" / "backups"


def restore_table(conn, table_name: str, backup_path: Path):
    if not backup_path.exists():
        print(f"No backup found: {backup_path}")
        return 0

    with open(backup_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        print(f"Backup is empty: {backup_path}")
        return 0

    # Get current count
    current = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]

    # Get table columns
    cols_info = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    valid_cols = {c[1] for c in cols_info}

    restored = 0
    for row in data:
        # Only insert columns that exist in the table
        filtered = {k: v for k, v in row.items() if k in valid_cols}
        columns = list(filtered.keys())
        placeholders = ",".join(["?" for _ in columns])
        col_str = ",".join(columns)

        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {table_name} ({col_str}) VALUES ({placeholders})",
                [filtered[c] for c in columns],
            )
            restored += 1
        except Exception as e:
            print(f"  Skip row: {e}")

    conn.commit()
    after = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    print(f"{table_name}: was {current}, backup has {len(data)}, now {after}")
    return restored


def main():
    tables = sys.argv[1:] if len(sys.argv) > 1 else ["closed_trades", "okx_history"]

    conn = sqlite3.connect(str(DB_PATH))

    for table in tables:
        backup_path = BACKUP_DIR / f"{table}.json"
        restore_table(conn, table, backup_path)

    conn.close()
    print("Restore complete.")


if __name__ == "__main__":
    main()
