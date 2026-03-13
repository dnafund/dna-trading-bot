"""
Telegram Multi-User Handler — routes messages by chat_id → user_id.

Wraps the existing TelegramCommandHandler to support multiple users.
One bot token, multiple authorized chats routed to their user context.

Architecture:
    User A sends /status → Telegram → Bot → lookup chat_id → User A's positions
    User B sends /status → Telegram → Bot → lookup chat_id → User B's positions

Linking flow:
    1. User registers on dashboard → gets 6-digit code
    2. User sends /link CODE to Telegram bot
    3. Bot verifies code → links chat_id to user_id

Usage:
    python -m src.services.telegram_multiuser
"""

import logging
import os
import random
import string
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

from src.database.connection import get_session, init_db
from src.database.models import Position, User
from src.database.repositories.position_repo import PositionRepo
from src.database.repositories.user_repo import UserRepo
from src.services.redis_client import get_redis

logger = logging.getLogger(__name__)

# Pending link codes: code → {user_id, expires_at}
_pending_links: dict[str, dict] = {}


class TelegramMultiUser:
    """Multi-user Telegram bot handler.

    Routes commands to the correct user based on chat_id lookup in DB.
    Supports user registration via /link command.
    """

    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0

        # Command registry
        self._commands = {
            "/start": self._cmd_start,
            "/link": self._cmd_link,
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/pnl": self._cmd_pnl,
            "/history": self._cmd_history,
            "/help": self._cmd_help,
            "/unlink": self._cmd_unlink,
        }

        init_db()
        logger.info("[TG-MULTI] Multi-user Telegram handler initialized")

    def start(self) -> None:
        """Start polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("[TG-MULTI] Polling started")

    def stop(self) -> None:
        """Stop polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[TG-MULTI] Polling stopped")

    def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
        """Send message to a specific chat."""
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            if not resp.ok:
                logger.warning(f"[TG-MULTI] Send failed: {resp.text}")
        except Exception as e:
            logger.error(f"[TG-MULTI] Send error: {e}")

    def send_notification(self, user_id: str, text: str) -> None:
        """Send notification to a user by user_id (resolves chat_id from DB)."""
        try:
            with get_session() as session:
                repo = UserRepo(session)
                user = repo.find_by_id(user_id)
                if user and user.telegram_chat_id:
                    self.send_message(user.telegram_chat_id, text)
        except Exception as e:
            logger.error(f"[TG-MULTI] Notification error for user={user_id}: {e}")

    # ── Polling ──────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Long-polling loop for Telegram updates."""
        while self._running:
            try:
                resp = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={
                        "offset": self._last_update_id + 1,
                        "timeout": 30,
                    },
                    timeout=35,
                )
                if resp.ok:
                    data = resp.json()
                    for update in data.get("result", []):
                        self._last_update_id = update["update_id"]
                        message = update.get("message")
                        if message:
                            self._process_message(message)
            except requests.exceptions.Timeout:
                continue
            except Exception as e:
                logger.error(f"[TG-MULTI] Poll error: {e}")
                time.sleep(5)

    def _process_message(self, message: dict) -> None:
        """Route incoming message to command handler."""
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        if not text or not chat_id:
            return

        # Parse command
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]  # Remove @botname suffix
        args = parts[1] if len(parts) > 1 else ""

        handler = self._commands.get(cmd)
        if handler:
            handler(chat_id, args)
        else:
            # Unknown command — check if user is linked
            user = self._get_user_by_chat(chat_id)
            if user:
                self.send_message(
                    chat_id, "Unknown command. Send /help for available commands."
                )
            else:
                self.send_message(
                    chat_id,
                    "Welcome! Please link your account first.\n"
                    "Get a link code from the dashboard, then send:\n"
                    "`/link YOUR_CODE`",
                )

    # ── User Lookup ──────────────────────────────────────────────

    def _get_user_by_chat(self, chat_id: str) -> Optional[User]:
        """Look up user by Telegram chat_id."""
        try:
            with get_session() as session:
                repo = UserRepo(session)
                user = repo.find_by_telegram_chat_id(chat_id)
                if user:
                    session.expunge(user)
                return user
        except Exception as e:
            logger.error(f"[TG-MULTI] User lookup error: {e}")
            return None

    def _require_linked(self, chat_id: str) -> Optional[User]:
        """Check if chat is linked to a user. Sends error if not."""
        user = self._get_user_by_chat(chat_id)
        if not user:
            self.send_message(
                chat_id,
                "Account not linked. Get a code from the dashboard "
                "and send `/link YOUR_CODE`",
            )
        return user

    # ── Commands ─────────────────────────────────────────────────

    def _cmd_start(self, chat_id: str, args: str) -> None:
        """Handle /start — welcome message."""
        user = self._get_user_by_chat(chat_id)
        if user:
            name = user.name or user.username or "trader"
            self.send_message(chat_id, f"Welcome back, {name}! Send /help for commands.")
        else:
            self.send_message(
                chat_id,
                "*DNA Trading Bot*\n\n"
                "Link your account to get started:\n"
                "1. Go to dashboard → Settings\n"
                "2. Click 'Link Telegram'\n"
                "3. Send `/link YOUR_CODE` here",
            )

    def _cmd_link(self, chat_id: str, args: str) -> None:
        """Handle /link CODE — link Telegram to dashboard account."""
        code = args.strip().upper()
        if not code or len(code) != 6:
            self.send_message(chat_id, "Usage: `/link ABC123`\nGet code from dashboard Settings.")
            return

        # Check pending codes
        link_data = _pending_links.get(code)
        if not link_data:
            self.send_message(chat_id, "Invalid or expired code. Get a new one from dashboard.")
            return

        # Check expiry
        if datetime.now(timezone.utc).timestamp() > link_data["expires_at"]:
            del _pending_links[code]
            self.send_message(chat_id, "Code expired. Get a new one from dashboard.")
            return

        user_id = link_data["user_id"]

        # Link chat_id to user
        try:
            with get_session() as session:
                repo = UserRepo(session)
                user = repo.find_by_id(user_id)
                if not user:
                    self.send_message(chat_id, "User not found.")
                    return

                user.telegram_chat_id = chat_id

            del _pending_links[code]
            name = user.name or user.username or "trader"
            self.send_message(
                chat_id,
                f"Linked successfully! Welcome, {name}.\n"
                f"Send /help for available commands.",
            )
            logger.info(f"[TG-MULTI] Linked: chat={chat_id} → user={user_id}")

        except Exception as e:
            logger.error(f"[TG-MULTI] Link error: {e}")
            self.send_message(chat_id, "Link failed. Please try again.")

    def _cmd_unlink(self, chat_id: str, args: str) -> None:
        """Handle /unlink — remove Telegram link."""
        user = self._require_linked(chat_id)
        if not user:
            return

        try:
            with get_session() as session:
                repo = UserRepo(session)
                db_user = repo.find_by_id(user.id)
                if db_user:
                    db_user.telegram_chat_id = None
            self.send_message(chat_id, "Account unlinked.")
            logger.info(f"[TG-MULTI] Unlinked: chat={chat_id}")
        except Exception as e:
            logger.error(f"[TG-MULTI] Unlink error: {e}")
            self.send_message(chat_id, "Unlink failed.")

    def _cmd_status(self, chat_id: str, args: str) -> None:
        """Handle /status — show user's open positions and balance."""
        user = self._require_linked(chat_id)
        if not user:
            return

        try:
            with get_session() as session:
                pos_repo = PositionRepo(session)
                positions = pos_repo.find_open_by_user(user.id)

                total_pnl = sum(p.pnl_usd for p in positions)
                total_margin = sum(p.margin for p in positions)

                msg = f"*Status — {user.name or user.username}*\n"
                msg += f"Mode: {user.mode.upper()}\n"
                msg += f"Open: {len(positions)} positions\n"
                msg += f"Margin used: ${total_margin:,.0f}\n"
                msg += f"Unrealized PNL: ${total_pnl:+,.2f}\n"

                if positions:
                    msg += "\n*Positions:*\n"
                    for i, p in enumerate(positions[:10], 1):
                        emoji = "🟢" if p.pnl_usd >= 0 else "🔴"
                        msg += (
                            f"{emoji} {p.side} {p.symbol} "
                            f"${p.pnl_usd:+,.2f} ({p.roi_percent:+.1f}%)\n"
                        )

                self.send_message(chat_id, msg)

        except Exception as e:
            logger.error(f"[TG-MULTI] Status error: {e}")
            self.send_message(chat_id, "Error fetching status.")

    def _cmd_positions(self, chat_id: str, args: str) -> None:
        """Handle /positions — detailed open positions."""
        user = self._require_linked(chat_id)
        if not user:
            return

        try:
            with get_session() as session:
                pos_repo = PositionRepo(session)
                positions = pos_repo.find_open_by_user(user.id)

                if not positions:
                    self.send_message(chat_id, "No open positions.")
                    return

                msg = f"*Open Positions ({len(positions)})*\n\n"
                for i, p in enumerate(positions, 1):
                    emoji = "🟢" if p.pnl_usd >= 0 else "🔴"
                    data = p.data or {}
                    msg += (
                        f"*{i}. {p.side} {p.symbol}* ({p.entry_type})\n"
                        f"  Entry: ${p.entry_price:,.2f} | Now: ${p.current_price:,.2f}\n"
                        f"  Size: {p.size:.6f} | Lev: {p.leverage}x\n"
                        f"  {emoji} PNL: ${p.pnl_usd:+,.2f} ({p.roi_percent:+.1f}%)\n"
                        f"  SL: ${data.get('stop_loss', 0):,.2f}"
                    )
                    tp1 = data.get("take_profit_1")
                    if tp1:
                        tp1_status = "✅" if p.tp1_closed else "⏳"
                        msg += f" | TP1 {tp1_status}: ${tp1:,.2f}"
                    msg += "\n\n"

                self.send_message(chat_id, msg)

        except Exception as e:
            logger.error(f"[TG-MULTI] Positions error: {e}")
            self.send_message(chat_id, "Error fetching positions.")

    def _cmd_pnl(self, chat_id: str, args: str) -> None:
        """Handle /pnl — PNL summary."""
        user = self._require_linked(chat_id)
        if not user:
            return

        try:
            with get_session() as session:
                pos_repo = PositionRepo(session)

                # Open positions PNL
                open_pos = pos_repo.find_open_by_user(user.id)
                unrealized = sum(p.pnl_usd for p in open_pos)

                # Closed positions PNL (last 30 trades)
                closed, total = pos_repo.find_closed_by_user(user.id, limit=30)
                realized = sum(p.pnl_usd for p in closed)
                wins = sum(1 for p in closed if p.pnl_usd > 0)
                losses = sum(1 for p in closed if p.pnl_usd <= 0)
                win_rate = (wins / len(closed) * 100) if closed else 0

                msg = (
                    f"*PNL Summary*\n\n"
                    f"Open positions: {len(open_pos)}\n"
                    f"Unrealized PNL: ${unrealized:+,.2f}\n\n"
                    f"Last {len(closed)} trades:\n"
                    f"Realized PNL: ${realized:+,.2f}\n"
                    f"Win rate: {win_rate:.0f}% ({wins}W / {losses}L)\n"
                    f"Total: ${unrealized + realized:+,.2f}"
                )
                self.send_message(chat_id, msg)

        except Exception as e:
            logger.error(f"[TG-MULTI] PNL error: {e}")
            self.send_message(chat_id, "Error calculating PNL.")

    def _cmd_history(self, chat_id: str, args: str) -> None:
        """Handle /history — recent closed trades."""
        user = self._require_linked(chat_id)
        if not user:
            return

        try:
            with get_session() as session:
                pos_repo = PositionRepo(session)
                closed, total = pos_repo.find_closed_by_user(user.id, limit=10)

                if not closed:
                    self.send_message(chat_id, "No trade history.")
                    return

                msg = f"*Recent Trades ({total} total)*\n\n"
                for p in closed:
                    emoji = "🟢" if p.pnl_usd > 0 else "🔴"
                    closed_str = p.closed_at.strftime("%m/%d %H:%M") if p.closed_at else "?"
                    msg += (
                        f"{emoji} {p.side} {p.symbol} | "
                        f"${p.pnl_usd:+,.2f} ({p.roi_percent:+.1f}%) | "
                        f"{p.close_reason or '?'} | {closed_str}\n"
                    )

                self.send_message(chat_id, msg)

        except Exception as e:
            logger.error(f"[TG-MULTI] History error: {e}")
            self.send_message(chat_id, "Error fetching history.")

    def _cmd_help(self, chat_id: str, args: str) -> None:
        """Handle /help — list commands."""
        user = self._get_user_by_chat(chat_id)
        if user:
            msg = (
                "*DNA Trading Bot — Commands*\n\n"
                "/status — Open positions + PNL\n"
                "/positions — Detailed position list\n"
                "/pnl — PNL summary\n"
                "/history — Recent closed trades\n"
                "/unlink — Remove Telegram link\n"
                "/help — This message"
            )
        else:
            msg = (
                "*DNA Trading Bot*\n\n"
                "Link your account:\n"
                "1. Dashboard → Settings → Link Telegram\n"
                "2. Send `/link YOUR_CODE` here\n\n"
                "/link CODE — Link your account\n"
                "/help — This message"
            )
        self.send_message(chat_id, msg)


# ── Link Code Management ────────────────────────────────────────


def generate_link_code(user_id: str, ttl_seconds: int = 600) -> str:
    """Generate a 6-digit link code for Telegram linking.

    Args:
        user_id: User ID to link.
        ttl_seconds: Code validity period (default 10 minutes).

    Returns:
        6-character alphanumeric code.
    """
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    _pending_links[code] = {
        "user_id": user_id,
        "expires_at": datetime.now(timezone.utc).timestamp() + ttl_seconds,
    }
    # Also store in Redis for multi-process access
    try:
        r = get_redis()
        r.setex(f"tg_link:{code}", ttl_seconds, user_id)
    except Exception:
        pass  # Fallback to in-memory only

    logger.info(f"[TG-MULTI] Link code generated: {code} → user={user_id}")
    return code


# ── Standalone Entry Point ───────────────────────────────────────


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        return

    init_db()
    handler = TelegramMultiUser(token)
    handler.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        handler.stop()


if __name__ == "__main__":
    main()
