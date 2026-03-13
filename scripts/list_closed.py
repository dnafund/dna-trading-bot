"""List all closed positions with PNL."""
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
positions_file = project_root / "data" / "positions.json"

with open(positions_file, "r", encoding="utf-8") as f:
    data = json.load(f)

closed = []
for pid, pos in data.items():
    if pid.startswith("_"):
        continue
    if pos.get("status") == "CLOSED":
        closed.append(pos)

print(f"Total closed: {len(closed)}")
print()
for p in closed:
    pid = p["position_id"][:30]
    side = p["side"]
    etype = p.get("entry_type", "?")[:15]
    pnl = p.get("pnl_usd", 0)
    reason = p.get("close_reason", "?")
    print(f"{pid:30s} {side:4s} {etype:15s} PNL: ${pnl:>8.2f}  Reason: {reason}")
