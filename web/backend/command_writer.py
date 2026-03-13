"""
Command Writer — file-based IPC to send commands from web dashboard to the live bot.

Pattern: Web writes individual command JSON files to data/web_commands/.
Bot polls the directory every 5s, executes, and deletes processed files.
"""

import json
import os
import time
import uuid
from pathlib import Path

# Commands directory (same level as positions.json)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
COMMANDS_DIR = DATA_DIR / "web_commands"


def _ensure_dir():
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)


def write_command(command: str, position_id: str, params: dict = None) -> str:
    """Write a command file for the bot to pick up.

    Args:
        command: One of "close", "partial_close", "cancel_tp", "modify_sl"
        position_id: Target position ID
        params: Extra parameters (percent, level, price, etc.)

    Returns:
        Unique command ID
    """
    _ensure_dir()

    cmd_id = f"cmd_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    payload = {
        "id": cmd_id,
        "command": command,
        "position_id": position_id,
        "params": params or {},
        "timestamp": time.time(),
    }

    # Atomic write: write to temp file then rename (prevents partial reads)
    tmp_path = COMMANDS_DIR / f"{cmd_id}.tmp"
    final_path = COMMANDS_DIR / f"{cmd_id}.json"

    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(str(tmp_path), str(final_path))

    return cmd_id
