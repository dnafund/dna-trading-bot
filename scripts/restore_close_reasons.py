"""Restore close_reason and tp1/tp2 flags from git backup to SQLite closed_trades."""
import json
import sqlite3
import subprocess

DB_PATH = "data/trades.db"
GIT_REF = "bd179bc0:data/positions.json"

def main():
    # Load git backup
    raw = subprocess.check_output(["git", "show", GIT_REF])
    backup = json.loads(raw)
    if isinstance(backup, dict):
        closed_backup = [v for v in backup.values() if isinstance(v, dict) and v.get("status") == "CLOSED"]
    elif isinstance(backup, list):
        closed_backup = [p for p in backup if isinstance(p, dict) and p.get("status") == "CLOSED"]
    else:
        closed_backup = []

    print(f"Git backup: {len(closed_backup)} closed trades")

    # Build lookup by symbol + side + entry_price (fuzzy)
    backup_lookup = []
    for p in closed_backup:
        side_raw = (p.get("side") or "").upper()
        side = "long" if side_raw == "BUY" else "short" if side_raw == "SELL" else side_raw.lower()
        backup_lookup.append({
            "symbol": p.get("symbol", ""),
            "side": side,
            "entry_price": float(p.get("entry_price", 0)),
            "close_reason": p.get("close_reason", ""),
            "tp1_closed": 1 if p.get("tp1_closed") else 0,
            "tp2_closed": 1 if p.get("tp2_closed") else 0,
        })

    # Read SQLite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT position_id, symbol, side, entry_price, close_reason, tp1_closed, tp2_closed FROM closed_trades").fetchall()
    print(f"SQLite: {len(rows)} closed trades")

    updated = 0
    for row in rows:
        sym = row["symbol"]
        side = (row["side"] or "").lower()
        ep = float(row["entry_price"] or 0)

        # Find best match in backup
        best = None
        best_diff = float("inf")
        for b in backup_lookup:
            if b["symbol"] != sym or b["side"] != side:
                continue
            if b["entry_price"] == 0 or ep == 0:
                continue
            diff = abs(b["entry_price"] - ep) / ep
            if diff < best_diff and diff < 0.01:  # 1% tolerance
                best_diff = diff
                best = b

        if best and best["close_reason"]:
            conn.execute(
                "UPDATE closed_trades SET close_reason=?, tp1_closed=?, tp2_closed=? WHERE position_id=?",
                (best["close_reason"], best["tp1_closed"], best["tp2_closed"], row["position_id"])
            )
            # Remove used entry to prevent double matching
            backup_lookup.remove(best)
            updated += 1

    conn.commit()

    # Verify
    result = conn.execute(
        "SELECT close_reason, COUNT(*) as cnt FROM closed_trades GROUP BY close_reason ORDER BY cnt DESC"
    ).fetchall()
    print(f"\nUpdated {updated} trades. Distribution:")
    for r in result:
        print(f"  {r['close_reason']}: {r['cnt']}")

    conn.close()

if __name__ == "__main__":
    main()
