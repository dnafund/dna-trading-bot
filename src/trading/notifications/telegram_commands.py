"""
Telegram Command Handler for Futures Trading Bot

Handles incoming commands via polling:
/status - Bot status overview
/positions - Open positions list
/detail <n> - Full position details (entry, TP1, TP2, SL, PNL)
/close <n> - Manually close position by number or symbol
/pnl - Total PNL summary
/history - Closed trade history
/stats - Detailed statistics
/strategy - Current trading strategy V8
/startbot - Start scanning
/stopbot - Stop scanning
/help - Command guide
"""

import math
import threading
import time
import re
import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def format_price(price: float) -> str:
    """Format price with enough significant digits for any coin.

    Examples: 65432.1234 → "65,432.1234", 0.00000852 → "0.00000852"
    """
    if price == 0:
        return "0"
    abs_price = abs(price)
    if abs_price >= 1:
        return f"{price:,.4f}"
    # Count leading zeros after decimal point, show 4 significant digits
    leading_zeros = -math.floor(math.log10(abs_price)) - 1
    decimals = leading_zeros + 4
    return f"{price:,.{decimals}f}"


class TelegramCommandHandler:
    """
    Handle Telegram commands using long polling

    Runs in a separate thread, polls for updates,
    and responds to commands using the bot's data.
    """

    def __init__(self, token: str, chat_id: str, bot_ref=None):
        """
        Initialize command handler

        Args:
            token: Telegram bot token
            chat_id: Authorized chat ID (only responds to this chat)
            bot_ref: Reference to FuturesTradingBot instance
        """
        self.token = token
        self.chat_id = str(chat_id)
        self.bot_ref = bot_ref
        self.base_url = f"https://api.telegram.org/bot{token}"

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0
        self._divergence_pages_data = {}  # message_id -> {results, header, per_page}
        self._pending_config_input = None  # {full_key, label, message_id}

        # Command registry
        self.commands = {
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/detail": self._cmd_detail,
            "/close": self._cmd_close,
            "/linear": self._cmd_linear,
            "/pnl": self._cmd_pnl,
            "/history": self._cmd_history,
            "/stats": self._cmd_stats,
            "/startbot": self._cmd_startbot,
            "/stopbot": self._cmd_stopbot,
            "/strategy": self._cmd_strategy,
            "/config": self._cmd_config,
            "/help": self._cmd_help,
            "/start": self._cmd_help,  # Telegram default
            "/zones": self._cmd_zones,
        }

    def _register_commands(self):
        """Register command menu with Telegram Bot API (setMyCommands)"""
        try:
            commands = [
                {"command": "status", "description": "Trang thai bot & vi tri dang mo"},
                {"command": "positions", "description": "Vi tri dang mo chi tiet"},
                {"command": "detail", "description": "Chi tiet lenh (vd: /detail 1)"},
                {"command": "close", "description": "Dong lenh (vd: /close 1)"},
                {"command": "pnl", "description": "Loi/lo tong hop"},
                {"command": "history", "description": "Lich su lenh da dong"},
                {"command": "stats", "description": "Thong ke chi tiet"},
                {"command": "strategy", "description": "Chien luoc dang chay"},
                {"command": "config", "description": "Chinh sua tham so bot"},
                {"command": "startbot", "description": "Bat bot trading"},
                {"command": "stopbot", "description": "Tat bot trading"},
                {"command": "zones", "description": "S/D zones (vd: /zones BTCUSDT)"},
                {"command": "help", "description": "Huong dan su dung"},
            ]
            url = f"{self.base_url}/setMyCommands"
            resp = requests.post(url, json={"commands": commands}, timeout=10)
            if resp.status_code == 200 and resp.json().get("ok"):
                logger.info("[TELEGRAM] Command menu registered successfully")
            else:
                logger.warning(f"[TELEGRAM] setMyCommands failed: {resp.text[:200]}")
        except Exception as e:
            logger.warning(f"[TELEGRAM] setMyCommands error: {e}")

    def start(self):
        """Start polling for commands in background thread"""
        if self._running:
            logger.warning("Telegram command handler already running")
            return

        self._register_commands()

        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="telegram-commands",
            daemon=True
        )
        self._thread.start()
        logger.info("[TELEGRAM] Command handler started (polling)")

    def stop(self):
        """Stop polling"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[TELEGRAM] Command handler stopped")

    def send_message(self, text: str, parse_mode: str = "Markdown",
                     reply_markup: dict = None, max_retries: int = 3) -> Optional[int]:
        """Send message to configured chat, with Markdown fallback and retry. Returns message_id or None."""
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        for attempt in range(1, max_retries + 1):
            try:
                resp = requests.post(url, json=payload, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result", {}).get("message_id")
                # Markdown parse error → retry without formatting (not a network issue)
                logger.warning(f"[TELEGRAM] Send failed ({resp.status_code}): {resp.text[:200]}")
                payload.pop("parse_mode", None)
                resp2 = requests.post(url, json=payload, timeout=10)
                if resp2.status_code == 200:
                    data = resp2.json()
                    return data.get("result", {}).get("message_id")
                return None
            except Exception as e:
                logger.error(f"[TELEGRAM] Send error (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    import time
                    time.sleep(2 * attempt)  # 2s, 4s backoff
        logger.error(f"[TELEGRAM] Send FAILED after {max_retries} attempts")
        return None

    def edit_message(self, message_id: int, text: str, parse_mode: str = "Markdown",
                     reply_markup: dict = None) -> bool:
        """Edit an existing message by message_id, with Markdown fallback"""
        try:
            url = f"{self.base_url}/editMessageText"
            payload = {
                "chat_id": self.chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            # Markdown parse error or other failure → retry without formatting
            logger.warning(f"[TELEGRAM] Edit failed ({resp.status_code}): {resp.text[:200]}")
            payload.pop("parse_mode", None)
            resp2 = requests.post(url, json=payload, timeout=10)
            return resp2.status_code == 200
        except Exception as e:
            logger.error(f"[TELEGRAM] Edit message error: {e}")
            return False

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False) -> bool:
        """Answer a callback query (acknowledge button press)"""
        try:
            url = f"{self.base_url}/answerCallbackQuery"
            payload = {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert
            }
            resp = requests.post(url, json=payload, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"[TELEGRAM] Answer callback error: {e}")
            return False

    # ==========================================
    # Polling Loop
    # ==========================================

    def _poll_loop(self):
        """Main polling loop - runs in background thread"""
        logger.info("[TELEGRAM] Polling loop started")

        while self._running:
            try:
                updates = self._get_updates()

                for update in updates:
                    self._process_update(update)

                time.sleep(1)  # Poll every 1 second

            except Exception as e:
                logger.error(f"[TELEGRAM] Poll error: {e}")
                time.sleep(5)  # Wait longer on error

    def _get_updates(self) -> list:
        """Fetch new updates from Telegram"""
        try:
            url = f"{self.base_url}/getUpdates"
            params = {
                "offset": self._last_update_id + 1,
                "timeout": 10,
                "allowed_updates": ["message", "callback_query"]
            }
            resp = requests.get(url, params=params, timeout=15)

            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    return data.get("result", [])

            return []

        except requests.exceptions.Timeout:
            return []  # Normal for long polling
        except Exception as e:
            logger.error(f"[TELEGRAM] Get updates error: {e}")
            return []

    def _process_update(self, update: dict):
        """Process a single update (message or callback_query)"""
        update_id = update.get("update_id", 0)
        self._last_update_id = max(self._last_update_id, update_id)

        if "callback_query" in update:
            self._process_callback(update["callback_query"])
        elif "message" in update:
            self._process_message(update["message"])

    def _process_message(self, message: dict):
        """Process a text message (slash commands)"""
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        # Security: Only respond to authorized chat
        if chat_id != self.chat_id:
            logger.warning(f"[TELEGRAM] Unauthorized chat: {chat_id}")
            return

        if not text:
            return

        # Handle pending config text input (non-command text, expires after 60s)
        if self._pending_config_input and not text.startswith("/"):
            elapsed = time.time() - self._pending_config_input.get("timestamp", 0)
            if elapsed > 60:
                self._pending_config_input = None  # Expired
            else:
                self._handle_config_text_input(text)
                return

        # Extract command (handle /command@botname format)
        command = text.split()[0].split("@")[0].lower()

        if command in self.commands:
            logger.info(f"[TELEGRAM] Command: {command} | Full: {text}")
            try:
                self.commands[command](text)
            except Exception as e:
                logger.error(f"[TELEGRAM] Command error ({command}): {e}")
                self.send_message(f"Error: {str(e)}")
        elif text.startswith("/"):
            self.send_message(
                "Unknown command. Send /help for available commands."
            )

    def _process_callback(self, callback_query: dict):
        """Process an inline keyboard button press"""
        callback_id = callback_query.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        message_id = message.get("message_id")
        chat_id = str(message.get("chat", {}).get("id", ""))

        # Security: Only respond to authorized chat
        if chat_id != self.chat_id:
            self.answer_callback(callback_id, "Unauthorized")
            return

        logger.info(f"[TELEGRAM] Callback: {data}")

        try:
            parts = data.split(":")
            action = parts[0]

            if action == "detail":
                idx = int(parts[1])
                self._cb_detail(message_id, idx)
            elif action == "close":
                idx = int(parts[1])
                self._cb_close_menu(message_id, idx)
            elif action == "partial":
                pos_id = parts[1]
                percent = float(parts[2])
                self._cb_partial_close(message_id, pos_id, percent)
            elif action == "close_confirm":
                pos_id = parts[1]
                self._cb_close_confirm(message_id, pos_id)
            elif action == "cancel_tp1":
                pos_id = parts[1]
                self._cb_cancel_tp(message_id, pos_id, "tp1")
            elif action == "cancel_tp2":
                pos_id = parts[1]
                self._cb_cancel_tp(message_id, pos_id, "tp2")
            elif action == "cancel_tp_all":
                self._cb_cancel_tp_all(message_id)
            elif action == "close_all_profit":
                self._cb_bulk_close(message_id, "profit")
            elif action == "close_all_loss":
                self._cb_bulk_close(message_id, "loss")
            elif action == "close_all":
                self._cb_bulk_close(message_id, "all")
            elif action == "confirm_bulk":
                bulk_type = parts[1]
                self._cb_confirm_bulk(message_id, bulk_type)
            elif action == "positions":
                page = int(parts[1])
                self._cb_positions_page(message_id, page)
            elif action == "refresh":
                self._cb_positions_page(message_id, 1)
            elif action == "back":
                self._cb_positions_page(message_id, 1)
            elif action == "cancel":
                self._cb_positions_page(message_id, 1)
            elif action == "div_page":
                page = int(parts[1])
                self._cb_divergence_page(message_id, page)
            # Config callbacks
            elif action == "cfg":
                category = parts[1] if len(parts) > 1 else "main"
                self._cb_config_category(message_id, category)
            elif action == "cfg_set":
                full_key = ":".join(parts[1:])  # Rejoin in case key has colons
                self._cb_config_set_prompt(message_id, full_key)
            elif action == "cfg_toggle":
                full_key = ":".join(parts[1:])
                self._cb_config_toggle(message_id, full_key)
            elif action == "cfg_reset":
                self._cb_config_reset_confirm(message_id)
            elif action == "cfg_do_reset":
                self._cb_config_do_reset(message_id)
            elif action == "noop":
                pass  # Disabled button, do nothing
            else:
                logger.warning(f"[TELEGRAM] Unknown callback: {data}")

            self.answer_callback(callback_id)

        except Exception as e:
            logger.error(f"[TELEGRAM] Callback error ({data}): {e}")
            self.answer_callback(callback_id, f"Error: {str(e)}")

    # ==========================================
    # Command Handlers
    # ==========================================

    def _cmd_status(self, text=""):
        """Show bot status overview"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        bot = self.bot_ref
        status = bot.get_status()

        running_icon = "🟢 RUNNING" if status["is_running"] else "🔴 STOPPED"
        mode_text = status["mode"].upper()

        open_pos = status["open_positions"]
        total_pnl = status["total_pnl"]
        total_margin = status["total_margin"]

        pnl_icon = "+" if total_pnl >= 0 else ""
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        # Balance info
        if bot.mode == "paper":
            account_balance = bot.position_manager.paper_balance
        else:
            try:
                balances = bot.binance.get_account_balance()
                account_balance = balances.get('USDT', 0)
            except Exception:
                account_balance = 0.0

        # Equity = Balance + Active Margin + Unrealized PNL
        open_positions = bot.position_manager.get_open_positions()
        unrealized = sum((p.pnl_usd - p.realized_pnl) for p in open_positions)
        equity = account_balance + total_margin + unrealized

        msg = f"""🤖 *BOT STATUS*

{running_icon}
🏷 Mode: {mode_text}
🔎 Symbols: {len(status['symbols'])}

📊 *Positions:* {open_pos}
💰 *Equity:* ${equity:,.2f}
🔒 *Margin:* ${total_margin:,.2f}
💵 *Available:* ${account_balance - total_margin:,.2f}
{pnl_emoji} *Total PNL:* {pnl_icon}${total_pnl:,.2f}

⏱ _Updated: {datetime.now().strftime('%H:%M:%S')}_"""

        self.send_message(msg)

    def _cmd_positions(self, text=""):
        """Show open positions with inline keyboard buttons"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        positions = self.bot_ref.position_manager.get_open_positions()

        if not positions:
            self.send_message("📭 No open positions")
            return

        # Calculate totals
        total_pnl = sum(p.pnl_usd for p in positions)
        total_margin = sum(
            p.margin * (p.remaining_size / p.size) if p.size > 0 else p.margin
            for p in positions
        )
        wins = len([p for p in positions if p.pnl_usd > 0])
        losses = len([p for p in positions if p.pnl_usd < 0])
        neutral = len([p for p in positions if p.pnl_usd == 0])

        # Balance info
        if self.bot_ref.mode == "paper":
            account_balance = self.bot_ref.position_manager.paper_balance
        else:
            try:
                balances = self.bot_ref.binance.get_account_balance()
                account_balance = balances.get('USDT', 0)
            except Exception:
                account_balance = 0.0

        # Show first page (max 10 per page)
        per_page = 10
        page = 1
        end = min(per_page, len(positions))

        msg = f"📊 *Open Positions ({len(positions)})*\n\n"

        for i in range(end):
            p = positions[i]
            idx = i + 1
            side_icon = "🟢" if p.side == "BUY" else "🔴"
            pnl_sign = "+" if p.pnl_usd >= 0 else ""
            pnl_icon = "🍀" if p.pnl_usd > 0 else ("🔥" if p.pnl_usd < 0 else "⚪")

            msg += (
                f"{idx}. *{p.symbol}* {side_icon} {pnl_icon} {p.leverage}x "
                f"| {pnl_sign}${p.pnl_usd:,.2f} ({pnl_sign}{p.pnl_percent:.2f}%)\n"
            )

        # Equity = Balance + Active Margin + Unrealized PNL
        unrealized = sum((p.pnl_usd - p.realized_pnl) for p in positions)
        equity = account_balance + total_margin + unrealized

        # Total summary
        total_icon = "🟩" if total_pnl >= 0 else "🟥"
        total_sign = "+" if total_pnl >= 0 else ""
        msg += f"\n{total_icon} *Total PNL:* {total_sign}${total_pnl:,.2f}"
        msg += f"\n💰 *Equity:* ${equity:,.2f}"
        msg += f"\n🔒 *Margin:* ${total_margin:,.2f}"
        msg += f"\n💵 *Available:* ${account_balance - total_margin:,.2f}"
        msg += f"\n📈 *W/L:* {wins}W / {losses}L / {neutral}N"
        msg += f"\n\n⏱ _Updated: {datetime.now().strftime('%H:%M:%S')}_"

        # Build inline keyboard
        keyboard = self._build_positions_keyboard(positions, page, per_page)
        self.send_message(msg, reply_markup=keyboard)

    def _get_open_positions_sorted(self):
        """Get open positions in consistent order (same as /positions display)"""
        return self.bot_ref.position_manager.get_open_positions()

    # ==========================================
    # Inline Keyboard Builders
    # ==========================================

    def _build_positions_keyboard(self, positions, page=1, per_page=10):
        """Build inline keyboard for positions list"""
        total_pages = max(1, (len(positions) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = min(start + per_page, len(positions))

        keyboard = []

        # Position buttons (1 per row → bấm = open close menu)
        for i in range(start, end):
            idx = i + 1  # 1-indexed
            p = positions[i]
            symbol_short = p.symbol.replace("USDT", "")
            pnl_sign = "+" if p.pnl_usd >= 0 else ""
            side_icon = "🟢" if p.side == "BUY" else "🔴"
            keyboard.append([
                {"text": f"{idx}. {symbol_short} {side_icon} {p.leverage}x | {pnl_sign}${p.pnl_usd:,.2f}", "callback_data": f"close:{idx}"},
            ])

        # Bulk action rows
        keyboard.append([
            {"text": "✅ Close Profit", "callback_data": "close_all_profit"},
            {"text": "❌ Close Loss", "callback_data": "close_all_loss"},
        ])
        keyboard.append([
            {"text": "⚠️ Close ALL", "callback_data": "close_all"},
            {"text": "🔄 Refresh", "callback_data": "refresh"},
        ])
        keyboard.append([
            {"text": "❌ Cancel TP ALL", "callback_data": "cancel_tp_all"},
        ])

        # Pagination row (only if needed)
        if total_pages > 1:
            nav_row = []
            if page > 1:
                nav_row.append({"text": "◀ Prev", "callback_data": f"positions:{page-1}"})
            nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "noop"})
            if page < total_pages:
                nav_row.append({"text": "Next ▶", "callback_data": f"positions:{page+1}"})
            keyboard.append(nav_row)

        return {"inline_keyboard": keyboard}

    def _build_close_keyboard(self, position_id, idx, position=None):
        """Build close options keyboard with Cancel TP + partial + detail"""
        keyboard = []

        # Row 1: Cancel TP1 / Cancel TP2 buttons
        tp_row = []
        if position:
            # TP1 button state
            if position.tp1_closed:
                tp1_label = "TP1 ✅ Hit"
                tp1_data = "noop"
            elif position.tp1_cancelled:
                tp1_label = "TP1 ❌ Off"
                tp1_data = "noop"
            else:
                tp1_label = "❌ Cancel TP1"
                tp1_data = f"cancel_tp1:{position_id}"
            tp_row.append({"text": tp1_label, "callback_data": tp1_data})

            # TP2 button state
            if position.tp2_closed:
                tp2_label = "TP2 ✅ Hit"
                tp2_data = "noop"
            elif position.tp2_cancelled:
                tp2_label = "TP2 ❌ Off"
                tp2_data = "noop"
            else:
                tp2_label = "❌ Cancel TP2"
                tp2_data = f"cancel_tp2:{position_id}"
            tp_row.append({"text": tp2_label, "callback_data": tp2_data})
        if tp_row:
            keyboard.append(tp_row)

        # Row 2: Partial close percentages
        keyboard.append([
            {"text": "25%", "callback_data": f"partial:{position_id}:25"},
            {"text": "50%", "callback_data": f"partial:{position_id}:50"},
            {"text": "75%", "callback_data": f"partial:{position_id}:75"},
        ])

        # Row 3: Full close
        keyboard.append([
            {"text": "💀 100% Close", "callback_data": f"close_confirm:{position_id}"},
        ])

        # Row 4: Detail + Cancel
        keyboard.append([
            {"text": "📊 Detail", "callback_data": f"detail:{idx}"},
            {"text": "❌ Cancel", "callback_data": "cancel"},
        ])

        return {"inline_keyboard": keyboard}

    def _build_detail_keyboard(self, idx, position_id):
        """Build detail view keyboard"""
        return {"inline_keyboard": [
            [
                {"text": "🔄 Refresh", "callback_data": f"detail:{idx}"},
            ],
            [
                {"text": "🔒 Close", "callback_data": f"close:{idx}"},
                {"text": "◀ Back", "callback_data": "back"},
            ],
        ]}

    def _build_bulk_confirm_keyboard(self, bulk_type):
        """Build bulk close confirmation keyboard"""
        labels = {"profit": "Close All Profit", "loss": "Close All Loss", "all": "Close ALL"}
        return {"inline_keyboard": [
            [
                {"text": f"✅ Confirm {labels.get(bulk_type, bulk_type)}", "callback_data": f"confirm_bulk:{bulk_type}"},
                {"text": "❌ Cancel", "callback_data": "cancel"},
            ],
        ]}

    # ==========================================
    # Callback Handlers
    # ==========================================

    def _cb_detail(self, message_id, idx):
        """Show detail for position #idx (edit existing message)"""
        positions = self._get_open_positions_sorted()
        if idx < 1 or idx > len(positions):
            self.edit_message(message_id, f"Position #{idx} not found")
            return

        p = positions[idx - 1]
        side_text = "LONG 🟢" if p.side == "BUY" else "SHORT 🔴"
        entry_label = {
            "standard_m15": "Std M15", "standard_h1": "Std H1", "standard_h4": "Std H4",
            "ema610_h1": "EMA610 H1", "ema610_h4": "EMA610 H4",
        }.get(getattr(p, 'entry_type', 'standard_m15'), 'Standard')

        tp1_status = "✅ HIT" if p.tp1_closed else ("❌ OFF" if p.tp1_cancelled else "⏳")
        tp2_status = "✅ HIT" if p.tp2_closed else ("❌ OFF" if p.tp2_cancelled else "⏳")
        tp1_price = self._fmt_price(p.take_profit_1) if p.take_profit_1 else ("Cancelled" if p.tp1_cancelled else "ROI")
        tp2_price = self._fmt_price(p.take_profit_2) if p.take_profit_2 else ("Cancelled" if p.tp2_cancelled else "ROI")
        sl_price = self._fmt_price(p.stop_loss)

        unrealized = p.pnl_usd - p.realized_pnl
        pnl_sign = "+" if p.pnl_usd >= 0 else ""
        unreal_sign = "+" if unrealized >= 0 else ""
        roi_sign = "+" if p.roi_percent >= 0 else ""

        duration = datetime.now() - p.timestamp
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        duration_text = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        size_ratio = p.remaining_size / p.size if p.size > 0 else 1.0
        active_margin = p.margin * size_ratio

        trail_str = f"${format_price(p.trailing_sl)}" if p.trailing_sl else "—"
        ch_str = f"${format_price(p.chandelier_sl)}" if getattr(p, 'chandelier_sl', None) else "—"
        msg = f"""🔍 *#{idx} POSITION DETAIL*

*{p.symbol}* {side_text} {p.leverage}x
Loai: {entry_label} | {p.status} | {duration_text}

📊 Entry: ${format_price(p.entry_price)}
📊 Current: ${format_price(p.current_price)}

🎯 TP1: {tp1_price} [{tp1_status}]
🎯 TP2: {tp2_price} [{tp2_status}]
🛑 Hard SL: {sl_price}
📉 Chandelier: {ch_str}
📉 Trail SL: {trail_str}

💰 Size: {p.remaining_size:.6f} / {p.size:.6f} ({size_ratio*100:.0f}%)
💰 Margin: ${active_margin:,.2f} / ${p.margin:,.2f}

📈 Realized: +${p.realized_pnl:,.2f}
📈 Unrealized: {unreal_sign}${unrealized:,.2f}
📈 Total: {pnl_sign}${p.pnl_usd:,.2f} (ROI: {roi_sign}{p.roi_percent:.2f}%)

_Updated: {datetime.now().strftime('%H:%M:%S')}_"""

        keyboard = self._build_detail_keyboard(idx, p.position_id)
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_close_menu(self, message_id, idx):
        """Show close options for position #idx"""
        positions = self._get_open_positions_sorted()
        if idx < 1 or idx > len(positions):
            self.edit_message(message_id, f"Position #{idx} not found")
            return

        p = positions[idx - 1]
        side_text = "LONG" if p.side == "BUY" else "SHORT"
        pnl_sign = "+" if p.pnl_usd >= 0 else ""
        size_ratio = p.remaining_size / p.size if p.size > 0 else 1.0

        msg = f"""🔒 *CLOSE POSITION #{idx}*

*{p.symbol}* {side_text} {p.leverage}x
Entry: ${format_price(p.entry_price)} | Current: ${format_price(p.current_price)}
PNL: {pnl_sign}${p.pnl_usd:,.2f}
Remaining: {size_ratio*100:.0f}%

_Choose how much to close:_"""

        keyboard = self._build_close_keyboard(p.position_id, idx, position=p)
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_partial_close(self, message_id, position_id, percent):
        """Execute partial close"""
        pm = self.bot_ref.position_manager
        result = pm.partial_close_manual(position_id, percent)

        if result:
            pnl_sign = "+" if result["realized_pnl"] >= 0 else ""
            msg = f"""✅ *PARTIAL CLOSE*

*{result['symbol']}* - Closed {result['percent']:.0f}%
PNL: {pnl_sign}${result['realized_pnl']:,.2f}
Remaining: {result['remaining_pct']:.0f}%
Status: {result['status']}

_Closed at {datetime.now().strftime('%H:%M:%S')}_"""

            keyboard = {"inline_keyboard": [
                [{"text": "◀ Back to Positions", "callback_data": "back"}],
            ]}
            self.edit_message(message_id, msg, reply_markup=keyboard)
        else:
            self.edit_message(message_id, "Failed to close position")

    def _cb_close_confirm(self, message_id, position_id):
        """Fully close a position"""
        pm = self.bot_ref.position_manager
        position = pm.get_position(position_id)

        if not position:
            self.edit_message(message_id, "Position not found")
            return

        side_text = "LONG" if position.side == "BUY" else "SHORT"
        pnl_sign = "+" if position.pnl_usd >= 0 else ""

        pm.close_position(position_id, reason="MANUAL")

        msg = f"""🔒 *POSITION CLOSED*

*{position.symbol}* {side_text} {position.leverage}x
Entry: ${format_price(position.entry_price)}
Exit: ${format_price(position.current_price)}
PNL: {pnl_sign}${position.pnl_usd:,.2f} ({pnl_sign}{position.roi_percent:.2f}%)

_Closed at {datetime.now().strftime('%H:%M:%S')}_"""

        keyboard = {"inline_keyboard": [
            [{"text": "◀ Back to Positions", "callback_data": "back"}],
        ]}
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_cancel_tp(self, message_id, position_id, tp_level):
        """Cancel auto TP for a position (disable auto take profit)"""
        pm = self.bot_ref.position_manager
        position = pm.get_position(position_id)

        if not position:
            self.edit_message(message_id, "Position not found")
            return

        side_text = "LONG" if position.side == "BUY" else "SHORT"
        success = pm.cancel_tp(position_id, tp_level)

        if success:
            level_text = "TP1" if tp_level == "tp1" else "TP2"
            msg = f"""❌ *{level_text} CANCELLED*

*{position.symbol}* {side_text} {position.leverage}x
Auto {level_text} disabled - position stays open.

_Cancelled at {datetime.now().strftime('%H:%M:%S')}_"""
        else:
            msg = f"Failed to cancel TP for {position.symbol}"

        keyboard = {"inline_keyboard": [
            [{"text": "◀ Back to Positions", "callback_data": "back"}],
        ]}
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_cancel_tp_all(self, message_id):
        """Cancel all TPs for all open positions"""
        pm = self.bot_ref.position_manager
        positions = self._get_open_positions_sorted()

        if not positions:
            self.edit_message(message_id, "No open positions")
            return

        cancelled_count = 0
        for p in positions:
            if not p.tp1_cancelled and not p.tp1_closed:
                cancelled_count += 1
            if not p.tp2_cancelled and not p.tp2_closed:
                cancelled_count += 1
            pm.cancel_tp(p.position_id, "all")

        msg = f"""❌ *CANCEL TP ALL*

Disabled auto TP for {len(positions)} positions
({cancelled_count} TP levels cancelled)

All positions stay open - no auto take profit.

_Cancelled at {datetime.now().strftime('%H:%M:%S')}_"""

        keyboard = {"inline_keyboard": [
            [{"text": "◀ Back to Positions", "callback_data": "back"}],
        ]}
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_bulk_close(self, message_id, bulk_type):
        """Show confirmation for bulk close"""
        positions = self._get_open_positions_sorted()
        if bulk_type == "profit":
            targets = [p for p in positions if p.pnl_usd > 0]
            label = "profitable"
        elif bulk_type == "loss":
            targets = [p for p in positions if p.pnl_usd < 0]
            label = "losing"
        else:
            targets = positions
            label = "ALL"

        if not targets:
            self.edit_message(message_id, f"No {label} positions to close")
            return

        total_pnl = sum(p.pnl_usd for p in targets)
        pnl_sign = "+" if total_pnl >= 0 else ""

        msg = f"""⚠️ *CONFIRM BULK CLOSE*

Close {len(targets)} {label} positions?
Total PNL: {pnl_sign}${total_pnl:,.2f}

_This cannot be undone!_"""

        keyboard = self._build_bulk_confirm_keyboard(bulk_type)
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_confirm_bulk(self, message_id, bulk_type):
        """Execute bulk close after confirmation"""
        pm = self.bot_ref.position_manager
        positions = self._get_open_positions_sorted()

        if bulk_type == "profit":
            targets = [p for p in positions if p.pnl_usd > 0]
        elif bulk_type == "loss":
            targets = [p for p in positions if p.pnl_usd < 0]
        else:
            targets = positions

        closed = 0
        total_pnl = 0
        for p in targets:
            total_pnl += p.pnl_usd
            pm.close_position(p.position_id, reason="MANUAL_BULK")
            closed += 1

        pnl_sign = "+" if total_pnl >= 0 else ""
        msg = f"""✅ *BULK CLOSE COMPLETE*

Closed: {closed} positions
Total PNL: {pnl_sign}${total_pnl:,.2f}

_Closed at {datetime.now().strftime('%H:%M:%S')}_"""

        keyboard = {"inline_keyboard": [
            [{"text": "◀ Back to Positions", "callback_data": "back"}],
        ]}
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_positions_page(self, message_id, page):
        """Show positions page (edit existing message)"""
        positions = self._get_open_positions_sorted()

        if not positions:
            self.edit_message(message_id, "📭 No open positions")
            return

        per_page = 10
        total_pages = max(1, (len(positions) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))
        start = (page - 1) * per_page
        end = min(start + per_page, len(positions))

        # Calculate totals
        total_pnl = sum(p.pnl_usd for p in positions)
        wins = len([p for p in positions if p.pnl_usd > 0])
        losses = len([p for p in positions if p.pnl_usd < 0])

        msg = f"📊 *Positions ({len(positions)})* | Page {page}/{total_pages}\n\n"

        for i in range(start, end):
            p = positions[i]
            idx = i + 1
            side_icon = "🟢" if p.side == "BUY" else "🔴"
            pnl_sign = "+" if p.pnl_usd >= 0 else ""
            pnl_icon = "🍀" if p.pnl_usd > 0 else ("🔥" if p.pnl_usd < 0 else "⚪")

            msg += (
                f"{idx}. *{p.symbol}* {side_icon} {pnl_icon} {p.leverage}x "
                f"| {pnl_sign}${p.pnl_usd:,.2f} ({pnl_sign}{p.pnl_percent:.2f}%)\n"
            )

        total_icon = "🟩" if total_pnl >= 0 else "🟥"
        total_sign = "+" if total_pnl >= 0 else ""
        msg += f"\n{total_icon} *Total PNL:* {total_sign}${total_pnl:,.2f}"
        msg += f"\n📈 *W/L:* {wins}W / {losses}L"
        msg += f"\n\n_Updated: {datetime.now().strftime('%H:%M:%S')}_"

        keyboard = self._build_positions_keyboard(positions, page, per_page)
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cmd_detail(self, text=""):
        """Show full details of a specific position: /detail <number>"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        # Parse argument
        parts = text.strip().split()
        if len(parts) < 2:
            self.send_message("Usage: /detail <number>\nExample: /detail 5")
            return

        arg = parts[1]

        positions = self._get_open_positions_sorted()
        if not positions:
            self.send_message("No open positions")
            return

        # Parse as number (1-indexed)
        try:
            idx = int(arg)
            if idx < 1 or idx > len(positions):
                self.send_message(f"Invalid position number. Range: 1-{len(positions)}")
                return
            position = positions[idx - 1]
        except ValueError:
            # Try as symbol name
            symbol = arg.upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            matches = [p for p in positions if p.symbol == symbol]
            if not matches:
                self.send_message(f"No open position found for {symbol}")
                return
            position = matches[0]  # Show first match

        p = position
        side_text = "LONG 🟢" if p.side == "BUY" else "SHORT 🔴"

        # TP/SL status
        tp1_status = "✅ HIT" if p.tp1_closed else ("❌ OFF" if p.tp1_cancelled else "⏳ Pending")
        tp2_status = "✅ HIT" if p.tp2_closed else ("❌ OFF" if p.tp2_cancelled else "⏳ Pending")

        tp1_price = self._fmt_price(p.take_profit_1) if p.take_profit_1 else ("Cancelled" if p.tp1_cancelled else "ROI +20%")
        tp2_price = self._fmt_price(p.take_profit_2) if p.take_profit_2 else ("Cancelled" if p.tp2_cancelled else "ROI +50%")
        sl_price = self._fmt_price(p.stop_loss)

        # PNL breakdown
        unrealized = p.pnl_usd - p.realized_pnl
        unreal_sign = "+" if unrealized >= 0 else ""
        pnl_sign = "+" if p.pnl_usd >= 0 else ""
        roi_sign = "+" if p.roi_percent >= 0 else ""

        # Duration
        duration = datetime.now() - p.timestamp
        hours = int(duration.total_seconds() // 3600)
        minutes = int((duration.total_seconds() % 3600) // 60)
        duration_text = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        # Size info
        size_ratio = p.remaining_size / p.size if p.size > 0 else 1.0
        active_margin = p.margin * size_ratio

        msg = f"""🔍 *POSITION DETAIL*

*{p.symbol}* {side_text} {p.leverage}x
Status: {p.status}
Duration: {duration_text}

📊 *Price*
Entry: ${format_price(p.entry_price)}
Current: ${format_price(p.current_price)}

🎯 *Targets*
TP1: {tp1_price} [{tp1_status}]
TP2: {tp2_price} [{tp2_status}]
SL: {sl_price}

💰 *Size & Margin*
Original Size: {p.size:.6f}
Remaining: {p.remaining_size:.6f} ({size_ratio*100:.0f}%)
Margin: ${active_margin:,.2f} / ${p.margin:,.2f}

📈 *PNL*
Realized: +${p.realized_pnl:,.2f}
Unrealized: {unreal_sign}${unrealized:,.2f}
Total PNL: {pnl_sign}${p.pnl_usd:,.2f}
ROI: {roi_sign}{p.roi_percent:.2f}%

🆔 {p.position_id}
_Updated: {datetime.now().strftime('%H:%M:%S')}_"""

        self.send_message(msg)

    def _cmd_close(self, text=""):
        """Close a position manually: /close <number> or /close <symbol>"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        # Parse argument
        parts = text.strip().split()
        if len(parts) < 2:
            self.send_message(
                "Usage:\n"
                "/close <number> - Close position by number\n"
                "/close <symbol> - Close all positions for symbol\n"
                "Example: /close 5 or /close BTCUSDT"
            )
            return

        arg = parts[1]
        pm = self.bot_ref.position_manager
        positions = self._get_open_positions_sorted()

        if not positions:
            self.send_message("No open positions to close")
            return

        to_close = []

        try:
            idx = int(arg)
            if idx < 1 or idx > len(positions):
                self.send_message(f"Invalid position number. Range: 1-{len(positions)}")
                return
            to_close = [positions[idx - 1]]
        except ValueError:
            # Treat as symbol name
            symbol = arg.upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
            to_close = [p for p in positions if p.symbol == symbol]
            if not to_close:
                self.send_message(f"No open position found for {symbol}")
                return

        # Close positions
        closed_count = 0
        for p in to_close:
            side_text = "LONG" if p.side == "BUY" else "SHORT"
            pnl_sign = "+" if p.pnl_usd >= 0 else ""

            pm.close_position(p.position_id, reason="MANUAL")
            closed_count += 1

            msg = f"""🔒 *MANUALLY CLOSED*

*{p.symbol}* {side_text} {p.leverage}x
Entry: ${format_price(p.entry_price)}
Exit: ${format_price(p.current_price)}
PNL: {pnl_sign}${p.pnl_usd:,.2f} ({pnl_sign}{p.roi_percent:.2f}%)

_Closed at {datetime.now().strftime('%H:%M:%S')}_"""

            self.send_message(msg)
            logger.info(f"[TELEGRAM] Manually closed {p.symbol} ({p.position_id}), PNL: ${p.pnl_usd:.2f}")

        if closed_count > 1:
            self.send_message(f"✅ Closed {closed_count} positions for {to_close[0].symbol}")

    def _parse_linear_position(self, issue):
        """
        Parse position data from Linear issue title and latest comment

        Title format: [FUTURES-BTCUSDT] SELL @ $88362.10 (20x)
        Comment format contains: **PNL**: $44.92 (+8.98%)

        Returns:
            dict with symbol, side, entry, leverage, pnl_usd, pnl_pct
        """
        title = issue.get("title", "")
        result = {
            "symbol": "",
            "side": "",
            "entry": 0.0,
            "leverage": 0,
            "pnl_usd": 0.0,
            "pnl_pct": 0.0,
            "identifier": issue.get("identifier", ""),
        }

        # Parse title: [FUTURES-BTCUSDT] SELL @ $88362.10 (20x)
        title_match = re.search(
            r'\[FUTURES-(\w+)\].*?(BUY|SELL)\s*@\s*\$?([\d,.]+)\s*\((\d+)x\)',
            title
        )
        if title_match:
            result["symbol"] = title_match.group(1)
            result["side"] = title_match.group(2)
            result["entry"] = float(title_match.group(3).replace(",", ""))
            result["leverage"] = int(title_match.group(4))

        # Parse latest comment for PNL
        comments = issue.get("comments", {}).get("nodes", [])
        if comments:
            body = comments[0].get("body", "")
            # Match: **PNL**: $44.92 (+8.98%)  or  **PNL**: $-59.86 (-11.97%)
            pnl_match = re.search(
                r'\*\*PNL\*\*:\s*\$?([-\d,.]+)\s*\(([-+]?[\d.]+)%\)',
                body
            )
            if pnl_match:
                result["pnl_usd"] = float(pnl_match.group(1).replace(",", ""))
                result["pnl_pct"] = float(pnl_match.group(2))

        return result

    def _cmd_linear(self, text=""):
        """Show all futures positions from Linear with PNL"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        try:
            issues = self.bot_ref.linear.search_open_futures_issues()

            if not issues:
                self.send_message("No open futures positions on Linear")
                return

            # Parse all positions
            positions = []
            for issue in issues:
                pos = self._parse_linear_position(issue)
                if pos["symbol"]:
                    positions.append(pos)

            if not positions:
                self.send_message("No futures positions found on Linear")
                return

            # Calculate totals
            total_pnl = sum(p["pnl_usd"] for p in positions)
            wins = len([p for p in positions if p["pnl_usd"] > 0])
            losses = len([p for p in positions if p["pnl_usd"] < 0])
            neutral = len([p for p in positions if p["pnl_usd"] == 0])

            # Build message
            msg = f"📋 *Linear Positions ({len(positions)})*\n\n"

            for i, p in enumerate(positions, 1):
                # Status icon
                if p["pnl_usd"] > 0:
                    pnl_icon = "🍀"
                elif p["pnl_usd"] < 0:
                    pnl_icon = "🔥"
                else:
                    pnl_icon = "⚪"

                side_icon = "🟢" if p["side"] == "BUY" else "🔴"
                pnl_sign = "+" if p["pnl_pct"] >= 0 else ""

                msg += (
                    f"{i}. *{p['symbol']}* {side_icon} {pnl_icon} {p['leverage']}x "
                    f"| {pnl_sign}${p['pnl_usd']:,.2f} ({pnl_sign}{p['pnl_pct']:.2f}%)\n"
                )

            # Total summary
            total_icon = "🟩" if total_pnl >= 0 else "🟥"
            total_sign = "+" if total_pnl >= 0 else ""
            msg += f"\n{total_icon} *Total PNL:* {total_sign}${total_pnl:,.2f}"
            msg += f"\n📈 *W/L:* {wins}W / {losses}L / {neutral}N"
            msg += f"\n\n⏱ _Updated: {datetime.now().strftime('%H:%M:%S')}_"

            self.send_message(msg)

        except Exception as e:
            logger.error(f"[TELEGRAM] Linear fetch error: {e}")
            self.send_message(f"Error fetching from Linear: {str(e)}")

    def _cmd_pnl(self, text=""):
        """Show PNL summary"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        pm = self.bot_ref.position_manager

        # Open positions
        open_positions = pm.get_open_positions()
        open_margin = sum(
            p.margin * (p.remaining_size / p.size) if p.size > 0 else p.margin
            for p in open_positions
        )

        # Unrealized PNL (only remaining portions)
        unrealized_pnl = sum(
            (p.pnl_usd - p.realized_pnl) for p in open_positions
        )

        # Realized PNL = closed trades + partial close profits
        closed = [p for p in pm.positions.values() if p.status == "CLOSED"]
        closed_pnl = sum(p.pnl_usd for p in closed)
        partial_realized = sum(p.realized_pnl for p in open_positions)
        total_realized = closed_pnl + partial_realized

        # Total PNL
        total_pnl = total_realized + unrealized_pnl

        total_icon = "+" if total_pnl >= 0 else ""
        unreal_icon = "+" if unrealized_pnl >= 0 else ""
        real_icon = "+" if total_realized >= 0 else ""

        msg = f"""*PNL SUMMARY*

*Open Positions:* {len(open_positions)}
Unrealized PNL: {unreal_icon}${unrealized_pnl:,.2f}
Margin Used: ${open_margin:,.2f}

*Realized PNL:* {real_icon}${total_realized:,.2f}
├ Closed Trades ({len(closed)}): {real_icon}${closed_pnl:,.2f}
└ Partial TP: +${partial_realized:,.2f}

*Total PNL:* {total_icon}${total_pnl:,.2f}

_Updated: {datetime.now().strftime('%H:%M:%S')}_"""

        self.send_message(msg)

    def _cmd_history(self, text=""):
        """Show closed trade history"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        pm = self.bot_ref.position_manager
        closed = [p for p in pm.positions.values() if p.status == "CLOSED"]

        if not closed:
            self.send_message("No closed trades yet")
            return

        # Show last 10 trades
        recent = sorted(closed, key=lambda p: p.timestamp, reverse=True)[:10]

        msg = f"*TRADE HISTORY* (Last {len(recent)})\n"

        for p in recent:
            side_icon = "LONG" if p.side == "BUY" else "SHORT"
            pnl_icon = "+" if p.pnl_usd >= 0 else ""
            result = "WIN" if p.pnl_usd > 0 else "LOSS"

            msg += f"""
{result} *{p.symbol}* {side_icon}
Entry: ${format_price(p.entry_price)}
Exit: ${format_price(p.current_price)}
PNL: {pnl_icon}${p.pnl_usd:,.2f}
Reason: {p.close_reason or 'N/A'}
Time: {p.timestamp.strftime('%m/%d %H:%M')}
"""

        total_pnl = sum(p.pnl_usd for p in closed)
        wins = len([p for p in closed if p.pnl_usd > 0])

        msg += f"""
*Summary:* {len(closed)} trades | {wins}W {len(closed)-wins}L
*Total Realized:* ${total_pnl:,.2f}"""

        self.send_message(msg)

    def _cmd_stats(self, text=""):
        """Show detailed statistics"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        pm = self.bot_ref.position_manager
        all_positions = list(pm.positions.values())

        if not all_positions:
            self.send_message("No trading data available yet")
            return

        open_pos = [p for p in all_positions if p.status in ["OPEN", "PARTIAL_CLOSE"]]
        closed = [p for p in all_positions if p.status == "CLOSED"]

        # Stats
        total_trades = len(all_positions)
        open_count = len(open_pos)
        closed_count = len(closed)

        # Win rate
        if closed:
            wins = len([p for p in closed if p.pnl_usd > 0])
            losses = len([p for p in closed if p.pnl_usd <= 0])
            win_rate = (wins / len(closed)) * 100 if closed else 0

            realized_pnl = sum(p.pnl_usd for p in closed)
            avg_win = sum(p.pnl_usd for p in closed if p.pnl_usd > 0) / max(wins, 1)
            avg_loss = sum(p.pnl_usd for p in closed if p.pnl_usd <= 0) / max(losses, 1)

            best_trade = max(closed, key=lambda p: p.pnl_usd)
            worst_trade = min(closed, key=lambda p: p.pnl_usd)
        else:
            wins = losses = 0
            win_rate = 0
            realized_pnl = 0
            avg_win = avg_loss = 0
            best_trade = worst_trade = None

        unrealized_pnl = sum(p.pnl_usd for p in open_pos)

        # Symbols traded
        symbols_traded = set(p.symbol for p in all_positions)

        msg = f"""*TRADING STATISTICS*

*Overview*
Total Trades: {total_trades}
Open: {open_count} | Closed: {closed_count}
Symbols Traded: {len(symbols_traded)}
Mode: {self.bot_ref.mode.upper()}

*Performance*
Win Rate: {win_rate:.1f}% ({wins}W / {losses}L)
Realized PNL: ${realized_pnl:,.2f}
Unrealized PNL: ${unrealized_pnl:,.2f}
Avg Win: ${avg_win:,.2f}
Avg Loss: ${avg_loss:,.2f}
"""

        if best_trade:
            msg += f"""
*Best Trade:* {best_trade.symbol} +${best_trade.pnl_usd:,.2f}
*Worst Trade:* {worst_trade.symbol} ${worst_trade.pnl_usd:,.2f}"""

        msg += f"\n\n_Updated: {datetime.now().strftime('%H:%M:%S')}_"
        self.send_message(msg)

    def _cmd_strategy(self, text=""):
        """Show current trading strategy summary"""
        from src.trading.core import config

        # Leverage tiers
        tier1 = [s.replace("USDT", "") for s, v in config.LEVERAGE.items() if v == 20 and s != "default"]
        tier2 = [s.replace("USDT", "") for s, v in config.LEVERAGE.items() if v == 10 and s != "default"]
        tier3 = [s.replace("USDT", "") for s, v in config.LEVERAGE.items() if v == 7 and s != "default"]
        default_lev = config.LEVERAGE.get("default", 5)

        # EMA610 status
        ema610_status = "ON" if config.EMA610_ENTRY.get("enabled") else "OFF"
        ema610_tol = config.EMA610_ENTRY.get("tolerance", 0.002) * 100

        # Chandelier
        ch_status = "ON" if config.CHANDELIER_EXIT.get("enabled") else "OFF"
        ch_period = config.CHANDELIER_EXIT.get("period", 22)
        ch_mult = config.CHANDELIER_EXIT.get("multiplier", 2.0)

        # Smart SL
        ssl_status = "ON" if config.SMART_SL.get("enabled") else "OFF"
        ssl_vol = config.SMART_SL.get("volume_threshold_pct", 80)
        ssl_ema = config.SMART_SL.get("ema_safety_period", 200)

        # Divergence
        div_status = "ON" if config.DIVERGENCE_CONFIG.get("enabled") else "OFF"
        div_h1 = config.DIVERGENCE_CONFIG.get("h1_lookback", 160)
        div_h4 = config.DIVERGENCE_CONFIG.get("h4_lookback", 80)

        # Risk
        rm = config.RISK_MANAGEMENT
        margin = rm.get("fixed_margin", 50)
        max_total = rm.get("max_total_positions", 20)
        wick_thr = config.INDICATORS.get('wick_threshold', 40)

        # Standard exit per-timeframe
        std_m15 = config.STANDARD_EXIT.get('m15', {})
        std_h1 = config.STANDARD_EXIT.get('h1', {})
        std_h4 = config.STANDARD_EXIT.get('h4', {})

        # EMA610 exit config
        ema_h1 = config.EMA610_EXIT.get('h1', {})
        ema_h4 = config.EMA610_EXIT.get('h4', {})

        # Dynamic pairs
        vol_24h = config.DYNAMIC_PAIRS.get('volume_windows', {}).get('24h', 30)

        msg = f"""*CHIẾN LƯỢC V8.1*
_Cascade Trend + ROI TP + Chandelier SL_

*5 LOẠI LỆNH VÀO:*

1️⃣ *Chuẩn M15* (H4→H1→M15 cascade)
   H4 trend → H1 trend → M15 trend
   RSI + Phân kỳ lọc → M15 wick test EMA34/89 >= {wick_thr}%

2️⃣ *Chuẩn H1* (H4→H1 cascade)
   H4 trend → H1 trend
   RSI + Phân kỳ lọc → H1 wick test EMA34/89 >= {wick_thr}%

3️⃣ *Chuẩn H4*
   H4 trend → H4 wick test EMA34/89 >= {wick_thr}%

4️⃣ *EMA610 H1* [{ema610_status}]
   H4 trend (EMA34 & 89 cùng phía EMA610)
   Giá H1 chạm EMA610 ±{ema610_tol:.1f}%

5️⃣ *EMA610 H4* [{ema610_status}]
   H4 trend (EMA34 & 89 cùng phía EMA610)
   Giá H4 chạm EMA610 ±{ema610_tol:.1f}%

*CHỐT LỜI (ROI-based):*
Chuẩn M15: +{std_m15.get('tp1_roi', 20)}% ({std_m15.get('tp1_percent', 70)}%) / +{std_m15.get('tp2_roi', 40)}%
Chuẩn H1: +{std_h1.get('tp1_roi', 30)}% ({std_h1.get('tp1_percent', 70)}%) / +{std_h1.get('tp2_roi', 60)}%
Chuẩn H4: +{std_h4.get('tp1_roi', 50)}% ({std_h4.get('tp1_percent', 70)}%) / +{std_h4.get('tp2_roi', 100)}%
EMA610 H1: +{ema_h1.get('tp1_roi', 40)}% ({ema_h1.get('tp1_percent', 50)}%) / +{ema_h1.get('tp2_roi', 80)}%
EMA610 H4: +{ema_h4.get('tp1_roi', 60)}% ({ema_h4.get('tp1_percent', 50)}%) / +{ema_h4.get('tp2_roi', 120)}%

*CẮT LỖ:*
Chandelier [{ch_status}]: P{ch_period} x{ch_mult}
  M15→M15 | H1→H1→M15 | H4→H4→H1→M15
Smart SL [{ssl_status}]: vol <= {ssl_vol}% TB → cho thở
  EMA{ssl_ema} phá vỡ → cắt ngay (chỉ Chuẩn M15)
Cứng: M15 -{std_m15.get('hard_sl_roi', 20)}% | H1 -{std_h1.get('hard_sl_roi', 25)}% | H4 -{std_h4.get('hard_sl_roi', 40)}%
EMA610: H1 -{ema_h1.get('hard_sl_roi', 30)}% | H4 -{ema_h4.get('hard_sl_roi', 50)}%

*QUẢN LÝ VỐN:*
Margin: ${margin:,}/lệnh | Tối đa: {max_total} lệnh
Equity tối đa: {rm.get('max_equity_usage_pct', 50)}%
Mỗi cặp: M15+H1+H4 độc lập + N EMA610

*ĐÒN BẨY:*
20x: {', '.join(tier1)}
10x: {', '.join(tier2)}
7x: {', '.join(tier3[:5])}...
Mặc định: {default_lev}x

*BỘ LỌC:*
Phân kỳ RSI [{div_status}]: H1 {div_h1} / H4 {div_h4} nến
Cặp động: Top {vol_24h} theo volume 24h

_Cập nhật: {datetime.now().strftime('%H:%M:%S')}_"""

        self.send_message(msg)

    # ==========================================
    # /config Command + Callbacks
    # ==========================================

    # Category definitions for config menu
    _CONFIG_CATEGORIES = {
        "capital": {
            "icon": "💰", "label": "Quản lý vốn",
            "keys": [
                "RISK_MANAGEMENT.fixed_margin",
                "RISK_MANAGEMENT.max_total_positions",
                "RISK_MANAGEMENT.max_equity_usage_pct",
            ]
        },
        "tp_std": {
            "icon": "📈", "label": "TP Chuẩn (M15/H1/H4)",
            "keys": [
                "STANDARD_EXIT.m15.tp1_roi", "STANDARD_EXIT.m15.tp2_roi", "STANDARD_EXIT.m15.tp1_percent",
                "STANDARD_EXIT.h1.tp1_roi", "STANDARD_EXIT.h1.tp2_roi", "STANDARD_EXIT.h1.tp1_percent",
                "STANDARD_EXIT.h4.tp1_roi", "STANDARD_EXIT.h4.tp2_roi", "STANDARD_EXIT.h4.tp1_percent",
            ]
        },
        "tp_ema": {
            "icon": "📈", "label": "TP EMA610 (H1/H4)",
            "keys": [
                "EMA610_EXIT.h1.tp1_roi", "EMA610_EXIT.h1.tp2_roi", "EMA610_EXIT.h1.tp1_percent",
                "EMA610_EXIT.h4.tp1_roi", "EMA610_EXIT.h4.tp2_roi", "EMA610_EXIT.h4.tp1_percent",
            ]
        },
        "sl": {
            "icon": "📉", "label": "Cắt lỗ (Hard SL)",
            "keys": [
                "STANDARD_EXIT.m15.hard_sl_roi",
                "STANDARD_EXIT.h1.hard_sl_roi",
                "STANDARD_EXIT.h4.hard_sl_roi",
                "EMA610_EXIT.h1.hard_sl_roi",
                "EMA610_EXIT.h4.hard_sl_roi",
            ]
        },
        "chandelier": {
            "icon": "🕯", "label": "Chandelier Exit",
            "keys": [
                "CHANDELIER_EXIT.enabled",
                "CHANDELIER_EXIT.period",
                "CHANDELIER_EXIT.multiplier",
            ]
        },
        "smartsl": {
            "icon": "🧠", "label": "Smart SL",
            "keys": [
                "SMART_SL.enabled",
                "SMART_SL.volume_threshold_pct",
                "SMART_SL.ema_safety_period",
            ]
        },
        "ema610": {
            "icon": "📊", "label": "EMA610 Entry",
            "keys": [
                "EMA610_ENTRY.enabled",
                "EMA610_ENTRY.tolerance",
            ]
        },
        "divergence": {
            "icon": "📐", "label": "Divergence",
            "keys": [
                "DIVERGENCE_CONFIG.enabled",
                "DIVERGENCE_CONFIG.scan_top_n",
                "DIVERGENCE_CONFIG.scan_interval",
            ]
        },
        "pairs": {
            "icon": "🔄", "label": "Dynamic Pairs",
            "keys": [
                "DYNAMIC_PAIRS.volume_windows.24h",
                "DYNAMIC_PAIRS.refresh_interval",
            ]
        },
        "indicators": {
            "icon": "📏", "label": "Indicators",
            "keys": [
                "INDICATORS.wick_threshold",
            ]
        },
    }

    def _build_config_main_menu(self):
        """Build config main menu message and keyboard (reusable for send/edit)."""
        from src.trading.core.config import get_all_overrides

        overrides = get_all_overrides()
        override_count = len(overrides)

        msg = "*⚙️ CẤU HÌNH BOT*\n\nChọn danh mục để xem/chỉnh sửa:"
        if override_count > 0:
            msg += f"\n_{override_count} tham số đã tùy chỉnh_"

        # Build 2-column button grid
        buttons = []
        row = []
        for cat_id, cat in self._CONFIG_CATEGORIES.items():
            btn = {"text": f"{cat['icon']} {cat['label']}", "callback_data": f"cfg:{cat_id}"}
            row.append(btn)
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Add reset button
        buttons.append([{"text": "🔄 Reset defaults", "callback_data": "cfg_reset:confirm"}])

        keyboard = {"inline_keyboard": buttons}
        return msg, keyboard

    def _cmd_config(self, text=""):
        """Show config main menu with category buttons"""
        msg, keyboard = self._build_config_main_menu()
        self.send_message(msg, reply_markup=keyboard)

    def _cb_config_category(self, message_id: int, category: str):
        """Show params for a config category with edit buttons"""
        from src.trading.core.config import CONFIG_PARAMS, get_config_value, get_all_overrides

        if category == "main":
            # Back to main menu — edit existing message, don't send new one
            msg, keyboard = self._build_config_main_menu()
            self.edit_message(message_id, msg, reply_markup=keyboard)
            return

        cat = self._CONFIG_CATEGORIES.get(category)
        if not cat:
            self.edit_message(message_id, "Unknown category")
            return

        overrides = get_all_overrides()
        lines = [f"*{cat['icon']} {cat['label']}*\n"]
        buttons = []

        for full_key in cat["keys"]:
            param = CONFIG_PARAMS.get(full_key)
            if not param:
                continue

            label = param["label"]
            value = get_config_value(full_key)
            is_override = full_key in overrides
            param_type = param["type"]

            # Format value display
            if param_type == bool:
                val_str = "🟢 ON" if value else "🔴 OFF"
            elif param_type == float and full_key.endswith("tolerance"):
                val_str = f"{value * 100:.1f}%"  # Show tolerance as percentage
            else:
                val_str = str(value)

            # Mark overridden values
            marker = " ✏️" if is_override else ""
            lines.append(f"• {label}: `{val_str}`{marker}")

            # Button: toggle for bool, text input for others
            if param_type == bool:
                new_state = "OFF" if value else "ON"
                btn_text = f"{'🔴' if value else '🟢'} {label} → {new_state}"
                buttons.append([{"text": btn_text, "callback_data": f"cfg_toggle:{full_key}"}])
            else:
                buttons.append([{"text": f"✏️ {label}", "callback_data": f"cfg_set:{full_key}"}])

        msg = "\n".join(lines)

        # Add back button
        buttons.append([{"text": "⬅️ Quay lại", "callback_data": "cfg:main"}])

        keyboard = {"inline_keyboard": buttons}
        self.edit_message(message_id, msg, reply_markup=keyboard)

    def _cb_config_set_prompt(self, message_id: int, full_key: str):
        """Prompt user to enter new value for a config param"""
        from src.trading.core.config import CONFIG_PARAMS, get_config_value

        param = CONFIG_PARAMS.get(full_key)
        if not param:
            self.edit_message(message_id, f"Unknown param: {full_key}")
            return

        label = param["label"]
        current = get_config_value(full_key)
        param_type = param["type"]

        # Format current value
        if param_type == float and full_key.endswith("tolerance"):
            current_str = f"{current * 100:.1f}% (nhập dạng decimal, vd: 0.005)"
        else:
            current_str = str(current)

        # Range hint
        hints = []
        if "min" in param:
            hints.append(f"min: {param['min']}")
        if "max" in param:
            hints.append(f"max: {param['max']}")
        range_str = f" ({', '.join(hints)})" if hints else ""

        msg = f"✏️ *{label}*\n\nGiá trị hiện tại: `{current_str}`{range_str}\n\n👉 Nhập giá trị mới:"

        # Store pending input state (expires after 60s)
        self._pending_config_input = {
            "full_key": full_key,
            "label": label,
            "message_id": message_id,
            "timestamp": time.time(),
        }

        self.edit_message(message_id, msg)

    def _handle_config_text_input(self, text: str):
        """Handle text input for config parameter"""
        from src.trading.core.config import validate_config_value, save_override

        pending = self._pending_config_input
        if not pending:
            return

        full_key = pending["full_key"]
        label = pending["label"]
        self._pending_config_input = None  # Clear pending state

        # Validate
        ok, result = validate_config_value(full_key, text)
        if not ok:
            self.send_message(f"❌ *{label}*: {result}\n\nDùng /config để thử lại.")
            return

        # Save
        if save_override(full_key, result):
            # Format display value
            if isinstance(result, float) and full_key.endswith("tolerance"):
                display = f"{result * 100:.1f}%"
            else:
                display = str(result)
            self.send_message(f"✅ *{label}* → `{display}`\n\n_Đã lưu. Áp dụng ngay, giữ qua restart._")
        else:
            self.send_message(f"❌ Lỗi lưu *{label}*. Thử lại /config.")

    def _cb_config_toggle(self, message_id: int, full_key: str):
        """Toggle a boolean config param"""
        from src.trading.core.config import CONFIG_PARAMS, get_config_value, save_override

        param = CONFIG_PARAMS.get(full_key)
        if not param or param["type"] != bool:
            self.edit_message(message_id, "Invalid toggle param")
            return

        current = get_config_value(full_key)
        new_value = not current

        if save_override(full_key, new_value):
            status = "🟢 ON" if new_value else "🔴 OFF"
            self.edit_message(
                message_id,
                f"✅ *{param['label']}* → {status}\n\n_Áp dụng ngay._"
            )
        else:
            self.edit_message(message_id, "❌ Lỗi toggle. Thử lại /config.")

    def _cb_config_reset_confirm(self, message_id: int):
        """Show reset confirmation"""
        from src.trading.core.config import CONFIG_PARAMS, get_all_overrides

        overrides = get_all_overrides()
        if not overrides:
            self.edit_message(message_id, "Không có tùy chỉnh nào để reset.")
            return

        lines = ["*⚠️ RESET VỀ MẶC ĐỊNH?*\n"]
        for key, val in overrides.items():
            param = CONFIG_PARAMS.get(key, {})
            label = param.get("label", key)
            lines.append(f"• {label}: `{val}`")
        lines.append("\n_Xóa tất cả tùy chỉnh và áp dụng ngay._")

        buttons = [
            [
                {"text": "✅ Xác nhận reset", "callback_data": "cfg_do_reset:yes"},
                {"text": "❌ Hủy", "callback_data": "cfg:main"},
            ]
        ]
        keyboard = {"inline_keyboard": buttons}
        self.edit_message(message_id, "\n".join(lines), reply_markup=keyboard)

    def _cb_config_do_reset(self, message_id: int):
        """Actually reset overrides"""
        from src.trading.core.config import reset_overrides

        if reset_overrides():
            self.edit_message(
                message_id,
                "✅ Đã reset về mặc định.\n\n_Áp dụng ngay, không cần restart._"
            )
        else:
            self.edit_message(message_id, "❌ Lỗi reset. Thử lại /config.")

    def _cmd_startbot(self, text=""):
        """Start the bot scanning"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        if self.bot_ref.is_running:
            self.send_message("Bot is already running!")
            return

        self.send_message("Starting bot... (use terminal to fully start)")

    def _cmd_stopbot(self, text=""):
        """Stop the bot scanning"""
        if not self.bot_ref:
            self.send_message("Bot reference not available")
            return

        if not self.bot_ref.is_running:
            self.send_message("Bot is already stopped!")
            return

        self.send_message("Stopping bot...")
        import asyncio
        asyncio.run_coroutine_threadsafe(
            self.bot_ref.stop(),
            asyncio.get_event_loop()
        )
        self.send_message("Bot stopped successfully!")

    def _cmd_zones(self, text=""):
        """Show active Supply/Demand zones for a symbol."""
        parts = text.strip().split()
        if len(parts) < 2:
            self.send_message("Usage: /zones BTCUSDT [timeframe]\nExample: /zones BTCUSDT h4")
            return

        symbol = parts[1].upper()
        if not symbol.endswith("USDT"):
            symbol += "USDT"

        tf_filter = parts[2].lower() if len(parts) > 2 else None

        if not self.bot_ref or not hasattr(self.bot_ref, 'sd_zone_cache'):
            self.send_message("S/D Zones not available (bot not running)")
            return

        # Get current price for context
        current_price = None
        positions = self.bot_ref.position_manager.get_active_positions()
        for pos in positions:
            if pos.symbol == symbol:
                current_price = pos.current_price
                break

        if tf_filter:
            # Show single timeframe
            zones = self.bot_ref.sd_zone_cache.get(symbol, tf_filter)
            if not zones:
                self.send_message(f"No S/D zones for {symbol} {tf_filter.upper()}")
                return

            supply = [z for z in zones if z.zone_type == "supply"]
            demand = [z for z in zones if z.zone_type == "demand"]

            lines = [f"*S/D Zones: {symbol} {tf_filter.upper()}*"]
            if current_price:
                lines.append(f"Price: {current_price:.4g}")
            lines.append("")
            if supply:
                lines.append("*Supply (resistance):*")
                for z in supply:
                    tested = " tested" if z.tested else ""
                    lines.append(f"  `{z.bottom:.4g}` - `{z.top:.4g}`{tested}")
            if demand:
                lines.append("*Demand (support):*")
                for z in demand:
                    tested = " tested" if z.tested else ""
                    lines.append(f"  `{z.bottom:.4g}` - `{z.top:.4g}`{tested}")

            self.send_message("\n".join(lines))
        else:
            # Show all timeframes
            msg = self.bot_ref.sd_zone_cache.format_telegram(symbol, current_price)
            self.send_message(msg)

    def _cmd_help(self, text=""):
        """Show command guide"""
        msg = """*FUTURES BOT COMMANDS*

📊 *Monitoring*
/status - Bot status overview
/positions - Open positions list
/detail <n> - Full position details
/pnl - PNL summary (open + closed)
/linear - Positions from Linear
/strategy - Current trading strategy

🔧 *Actions*
/close <n> - Close position by number
/close <symbol> - Close all for symbol

📜 *History & Stats*
/history - Closed trade history
/stats - Detailed statistics

⚙️ *Control*
/startbot - Start bot scanning
/stopbot - Stop bot scanning
/config - Edit trading parameters
/help - This help message

💡 /positions has *inline buttons* for Detail, Close (25/50/75/100%), and bulk actions!"""

        self.send_message(msg)

    # ==========================================
    # Alert Methods (called by bot)
    # ==========================================

    def _fmt_price(self, value):
        """Format price with smart significant digits or return N/A if None"""
        if value is None:
            return "N/A"
        return f"${format_price(value)}"

    def send_signal_alert(self, signal):
        """
        Send signal alert to Telegram

        Args:
            signal: TradingSignal object
        """
        side_icon = "BUY (LONG)" if signal.signal_type == "BUY" else "SELL (SHORT)"

        tp1 = self._fmt_price(signal.take_profit_1)
        tp2 = self._fmt_price(signal.take_profit_2)
        sl = self._fmt_price(signal.stop_loss)

        msg = f"""*SIGNAL ALERT*

*{signal.symbol}* - {side_icon}
Entry: ${format_price(signal.entry_price)}

*H4 Trend:* {signal.h4_trend}
EMA34: ${format_price(signal.h4_ema34)}
EMA89: ${format_price(signal.h4_ema89)}

*H1 Filter:* RSI = {signal.h1_rsi:.2f}

*M15 Entry:*
EMA34: ${format_price(signal.m15_ema34)}
EMA89: ${format_price(signal.m15_ema89)}
Wick: {signal.wick_ratio:.1f}%

*Targets:*
TP1: {tp1} (S/R)
TP2: {tp2} (Fibo 1.618)
SL: {sl}

_Time: {signal.timestamp.strftime('%Y-%m-%d %H:%M')}_"""

        self.send_message(msg)

    def send_position_opened(self, position):
        """
        Send position opened alert

        Args:
            position: Position object
        """
        side_text = "LONG" if position.side == "BUY" else "SHORT"
        tp1 = self._fmt_price(position.take_profit_1)
        tp2 = self._fmt_price(position.take_profit_2)
        sl = self._fmt_price(position.stop_loss)

        # Entry type label
        entry_type = (position.entry_type or "standard_m15").upper()
        entry_labels = {
            "STANDARD_M15": "📊 Standard M15",
            "STANDARD_H1": "📊 Standard H1",
            "STANDARD_H4": "📊 Standard H4",
            "EMA610_H1": "🎯 EMA610 H1 (Mean Reversion)",
            "EMA610_H4": "🎯 EMA610 H4 (Mean Reversion)",
            "SD_DEMAND_M15": "🟢 SD Demand M15",
            "SD_DEMAND_H1": "🟢 SD Demand H1",
            "SD_DEMAND_H4": "🟢 SD Demand H4",
            "SD_SUPPLY_M15": "🔴 SD Supply M15",
            "SD_SUPPLY_H1": "🔴 SD Supply H1",
            "SD_SUPPLY_H4": "🔴 SD Supply H4",
        }
        entry_label = entry_labels.get(entry_type, f"📊 {entry_type}")

        msg = f"""*POSITION OPENED*

*{position.symbol}* {side_text} {position.leverage}x
{entry_label}

Entry: ${format_price(position.entry_price)}
Size: {position.size:.6f}
Margin: ${position.margin:,.2f}

TP1: {tp1}
TP2: {tp2}
SL: {sl}

Mode: {self.bot_ref.mode.upper() if self.bot_ref else 'N/A'}
_Time: {datetime.now().strftime('%H:%M:%S')}_"""

        # Build TradingView + OKX inline buttons
        base_symbol = position.symbol.replace("USDT", "")
        tv_url = f"https://www.tradingview.com/chart/?symbol=OKX%3A{base_symbol}USDT.P"
        okx_url = f"https://www.okx.com/trade-swap/{base_symbol.lower()}-usdt-swap"
        keyboard = {
            "inline_keyboard": [[
                {"text": "📈 TradingView", "url": tv_url},
                {"text": "💱 OKX", "url": okx_url},
            ]]
        }

        self.send_message(msg, reply_markup=keyboard)

    def send_position_closed(self, position):
        """
        Send position closed alert

        Args:
            position: Position object
        """
        side_text = "LONG" if position.side == "BUY" else "SHORT"
        pnl_icon = "+" if position.pnl_usd >= 0 else ""
        result = "WIN" if position.pnl_usd > 0 else "LOSS"

        # Close reason with descriptive labels
        close_reason = position.close_reason or 'MANUAL'
        close_labels = {
            # Take Profit
            "TP1": "🎯 Take Profit 1 (70%)",
            "TP1_ATR": "🎯 TP1 ATR-based",
            "TP1_SR": "🎯 TP1 S/R-based",
            "TP1_ROI": "🎯 TP1 ROI Fallback",
            "TP2": "🎯 Take Profit 2 (30%)",
            "TP2_ATR": "🎯 TP2 ATR-based",
            "TP2_FIBO": "🎯 TP2 Fibonacci",
            "TP2_ROI": "🎯 TP2 ROI Fallback",
            # Stop Loss
            "CHANDELIER_M5": "📉 Chandelier Exit (M5)",
            "CHANDELIER_SL": "📉 Chandelier Exit (M15)",
            "CHANDELIER_H1": "📉 Chandelier Exit (H1)",
            "CHANDELIER_H4": "📉 Chandelier Exit (H4)",
            "HARD_SL": "🛑 Hard Stop Loss",
            "EMA200_BREAK_SL": "⚠️ EMA200 Break SL",
            # External
            "EXTERNAL_CLOSE": "🔄 Closed on Exchange",
            # Manual
            "MANUAL": "✋ Manual Close",
            "MANUAL_25": "✋ Manual 25%",
            "MANUAL_50": "✋ Manual 50%",
            "MANUAL_75": "✋ Manual 75%",
            "MANUAL_100": "✋ Manual 100%",
        }
        close_label = close_labels.get(close_reason, f"📊 {close_reason}")

        # Entry type for context
        entry_type = position.entry_type or "standard_m15"
        entry_labels = {
            "standard_m15": "Std M15",
            "standard_h1": "Std H1",
            "standard_h4": "Std H4",
            "ema610_h1": "EMA610 H1",
            "ema610_h4": "EMA610 H4",
            "rsi_div_m15": "RSI Div M15",
            "rsi_div_h1": "RSI Div H1",
            "rsi_div_h4": "RSI Div H4",
            "sd_demand_m15": "SD Demand M15",
            "sd_demand_h1": "SD Demand H1",
            "sd_demand_h4": "SD Demand H4",
            "sd_supply_m15": "SD Supply M15",
            "sd_supply_h1": "SD Supply H1",
            "sd_supply_h4": "SD Supply H4",
        }
        entry_label = entry_labels.get(entry_type, entry_type)

        # Use roi_percent (net PNL / margin) — NOT pnl_percent (raw price change * leverage)
        roi = position.roi_percent if hasattr(position, 'roi_percent') and position.roi_percent != 0 else position.pnl_percent
        roi_icon = "+" if roi >= 0 else ""

        msg = f"""*POSITION CLOSED* - {result}

*{position.symbol}* {side_text} {position.leverage}x
Entry Type: {entry_label}

Entry: ${format_price(position.entry_price)}
Exit: ${format_price(position.current_price)}
PNL: {pnl_icon}${position.pnl_usd:,.2f} ({roi_icon}{roi:.2f}%)

Close: {close_label}

_Time: {datetime.now().strftime('%H:%M:%S')}_"""

        # Build TradingView + OKX inline buttons
        base_symbol = position.symbol.replace("USDT", "")
        tv_url = f"https://www.tradingview.com/chart/?symbol=OKX%3A{base_symbol}USDT.P"
        okx_url = f"https://www.okx.com/trade-swap/{base_symbol.lower()}-usdt-swap"
        keyboard = {
            "inline_keyboard": [[
                {"text": "📈 TradingView", "url": tv_url},
                {"text": "💱 OKX", "url": okx_url},
            ]]
        }

        self.send_message(msg, reply_markup=keyboard)

    def send_position_partial_closed(self, position, percent: float, reason: str, realized_pnl: float):
        """
        Send partial close (TP1/TP2) alert

        Args:
            position: Position object
            percent: Percentage closed
            reason: Close reason (TP1, TP1_ATR, etc.)
            realized_pnl: Realized PNL from this partial close
        """
        side_text = "LONG" if position.side == "BUY" else "SHORT"
        pnl_icon = "+" if realized_pnl >= 0 else ""
        remaining_pct = (position.remaining_size / position.size * 100) if position.size > 0 else 0

        # Entry type for context
        entry_type = position.entry_type or "standard_m15"
        entry_labels = {
            "standard_m15": "Std M15",
            "standard_h1": "Std H1",
            "standard_h4": "Std H4",
            "ema610_h1": "EMA610 H1",
            "ema610_h4": "EMA610 H4",
            "rsi_div_m15": "RSI Div M15",
            "rsi_div_h1": "RSI Div H1",
            "rsi_div_h4": "RSI Div H4",
            "sd_demand_m15": "SD Demand M15",
            "sd_demand_h1": "SD Demand H1",
            "sd_demand_h4": "SD Demand H4",
            "sd_supply_m15": "SD Supply M15",
            "sd_supply_h1": "SD Supply H1",
            "sd_supply_h4": "SD Supply H4",
        }
        entry_label = entry_labels.get(entry_type, entry_type)

        # Reason labels
        reason_labels = {
            "TP1": "🎯 Take Profit 1",
            "TP1_ATR": "🎯 TP1 ATR-based",
            "TP1_SR": "🎯 TP1 S/R-based",
            "TP1_ROI": "🎯 TP1 ROI",
            "TP2": "🎯 Take Profit 2",
            "TP2_ATR": "🎯 TP2 ATR-based",
            "TP2_FIBO": "🎯 TP2 Fibonacci",
            "TP2_ROI": "🎯 TP2 ROI",
        }
        reason_label = reason_labels.get(reason, f"📊 {reason}")

        # ROI for partial close: use realized_pnl relative to the closed portion of margin
        partial_margin = position.margin * (percent / 100)
        partial_roi = (realized_pnl / partial_margin * 100) if partial_margin > 0 else position.pnl_percent
        partial_roi_icon = "+" if partial_roi >= 0 else ""

        msg = f"""*PARTIAL CLOSE* - {reason_label}

*{position.symbol}* {side_text} {position.leverage}x
Entry Type: {entry_label}

Entry: ${format_price(position.entry_price)}
Current: ${format_price(position.current_price)}
ROI: {partial_roi_icon}{partial_roi:.1f}%

Closed: {percent:.0f}% | Remaining: {remaining_pct:.0f}%
Realized PNL: {pnl_icon}${realized_pnl:,.2f}

_Remaining rides Chandelier trailing_
_Time: {datetime.now().strftime('%H:%M:%S')}_"""

        self.send_message(msg)

    def send_divergence_alert(self, symbol: str, divergences: list):
        """
        Send RSI divergence alert to Telegram.

        Args:
            symbol: Trading pair
            divergences: List of DivergenceResult objects
        """
        type_labels = {
            "bearish": "BEARISH DIVERGENCE (Phan Ky Giam)",
            "bullish": "BULLISH DIVERGENCE (Phan Ky Tang)",
            "hidden_bearish": "HIDDEN BEARISH (Hoi Tu Giam)",
            "hidden_bullish": "HIDDEN BULLISH (Hoi Tu Tang)",
        }

        type_icons = {
            "bearish": "🔴",
            "bullish": "🟢",
            "hidden_bearish": "🟠",
            "hidden_bullish": "🔵",
        }

        msg = f"*RSI DIVERGENCE DETECTED*\n\n*{symbol}*\n"

        for d in divergences:
            icon = type_icons.get(d.divergence_type, "⚠")
            label = type_labels.get(d.divergence_type, d.divergence_type)

            msg += f"\n{icon} *{d.timeframe}: {label}*\n"
            msg += f"Price: {format_price(d.price_swing_1)} -> {format_price(d.price_swing_2)}\n"
            msg += f"RSI: {d.rsi_swing_1:.1f} -> {d.rsi_swing_2:.1f}\n"
            if d.time_swing_1 and d.time_swing_2:
                try:
                    from datetime import datetime as dt, timedelta
                    offset_h = 7
                    def _parse_ts(s):
                        s = str(s).replace('Z', '+00:00')
                        return dt.fromisoformat(s)
                    t1_dt = _parse_ts(d.time_swing_1) + timedelta(hours=offset_h)
                    t2_dt = _parse_ts(d.time_swing_2) + timedelta(hours=offset_h)
                    msg += f"🕐 {t1_dt.strftime('%m/%d %H:%M')} → {t2_dt.strftime('%m/%d %H:%M')}\n"
                except Exception:
                    pass
            msg += f"Action: BLOCKS {d.blocks_direction} entries\n"

        msg += f"\n_{datetime.now().strftime('%H:%M:%S')}_"

        self.send_message(msg)

    def send_m15_divergence_alert(self, symbol: str, divergences: list):
        """Send M15 divergence notification (backward compat wrapper)."""
        self.send_divergence_alert(symbol, divergences, timeframe="M15")

    def send_divergence_alert(self, symbol: str, divergences: list, timeframe: str = "M15"):
        """
        Send divergence notification for any timeframe.

        Args:
            symbol: Trading pair
            divergences: List of DivergenceResult objects
            timeframe: "M15", "H1", or "H4"
        """
        type_labels = {
            "bearish": "Phan Ky Giam (Bearish)",
            "bullish": "Phan Ky Tang (Bullish)",
            "hidden_bearish": "Hoi Tu Giam (Hidden Bearish)",
            "hidden_bullish": "Hoi Tu Tang (Hidden Bullish)",
        }

        # Signal hint: what the divergence suggests
        type_hints = {
            "bearish": "co the dao chieu GIAM",
            "bullish": "co the dao chieu TANG",
            "hidden_bearish": "tiep tuc xu huong GIAM",
            "hidden_bullish": "tiep tuc xu huong TANG",
        }

        type_icons = {
            "bearish": "🔴", "bullish": "🟢",
            "hidden_bearish": "🟠", "hidden_bullish": "🔵",
        }

        msg = f"📊 *{timeframe} DIVERGENCE — {symbol}*\n"

        for d in divergences:
            icon = type_icons.get(d.divergence_type, "⚠")
            label = type_labels.get(d.divergence_type, d.divergence_type)
            hint = type_hints.get(d.divergence_type, "")

            msg += f"\n{icon} *{label}*\n"
            msg += f"Price: {format_price(d.price_swing_1)} → {format_price(d.price_swing_2)}\n"
            msg += f"RSI: {d.rsi_swing_1:.1f} → {d.rsi_swing_2:.1f}\n"
            # Show swing point times for cross-reference with chart
            if d.time_swing_1 and d.time_swing_2:
                try:
                    from datetime import datetime as dt, timedelta
                    offset_h = 7  # UTC+7
                    def _parse_ts(s):
                        """Parse timestamp string (ISO or pandas Timestamp)."""
                        s = str(s).replace('Z', '+00:00')
                        return dt.fromisoformat(s)
                    t1_dt = _parse_ts(d.time_swing_1) + timedelta(hours=offset_h)
                    t2_dt = _parse_ts(d.time_swing_2) + timedelta(hours=offset_h)
                    msg += f"🕐 {t1_dt.strftime('%m/%d %H:%M')} → {t2_dt.strftime('%m/%d %H:%M')}\n"
                except Exception:
                    msg += f"🕐 {d.time_swing_1} → {d.time_swing_2}\n"
            if hint:
                msg += f"💡 _{hint}_\n"

        msg += f"\n_{datetime.now().strftime('%H:%M:%S')}_"

        self.send_message(msg)

    def _build_divergence_page(self, results: dict, header: str, page: int, per_page: int = 15):
        """Build divergence message text and inline keyboard for a specific page."""
        type_icons = {
            "bearish": "🔴",
            "bullish": "🟢",
            "hidden_bearish": "🟠",
            "hidden_bullish": "🔵",
        }
        type_short = {
            "bearish": "Bearish",
            "bullish": "Bullish",
            "hidden_bearish": "H.Bearish",
            "hidden_bullish": "H.Bullish",
        }

        symbols = list(results.keys())
        total = len(symbols)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total)
        page_symbols = symbols[start_idx:end_idx]

        if header:
            msg = header
            msg += f"_{total} symbols detected_\n\n"
        else:
            msg = f"*RSI DIVERGENCE SCAN*\n"
            msg += f"_{total} symbols detected_\n\n"

        for symbol in page_symbols:
            divergences = results[symbol]
            parts = []
            for d in divergences:
                icon = type_icons.get(d.divergence_type, "⚠")
                short = type_short.get(d.divergence_type, d.divergence_type)
                parts.append(f"{icon}{d.timeframe} {short}")
            msg += f"*{symbol}* {' | '.join(parts)}\n"

        msg += f"\n_{datetime.now().strftime('%H:%M:%S')} | Trang {page}/{total_pages}_"

        # Navigation buttons
        nav_row = []
        if page > 1:
            nav_row.append({"text": "◀ Prev", "callback_data": f"div_page:{page - 1}"})
        nav_row.append({"text": f"📄 {page}/{total_pages}", "callback_data": "noop"})
        if page < total_pages:
            nav_row.append({"text": "Next ▶", "callback_data": f"div_page:{page + 1}"})

        keyboard = {"inline_keyboard": [nav_row]} if total_pages > 1 else None

        return msg, keyboard

    def send_divergence_summary(self, results: dict, header: str = None):
        """
        Send paginated summary message for all divergences found in a scan.

        Args:
            results: Dict mapping symbol to list of DivergenceResult objects
            header: Optional custom header (e.g. "NEW DIVERGENCES" for subsequent scans)
        """
        if not results:
            return

        msg, keyboard = self._build_divergence_page(results, header, page=1)
        msg_id = self.send_message(msg, reply_markup=keyboard)

        # Store data for pagination callbacks
        if msg_id:
            self._divergence_pages_data[msg_id] = {
                "results": results,
                "header": header,
            }
            # Keep only last 5 divergence messages to avoid memory leak
            if len(self._divergence_pages_data) > 5:
                oldest_key = next(iter(self._divergence_pages_data))
                del self._divergence_pages_data[oldest_key]

    def _cb_divergence_page(self, message_id: int, page: int):
        """Handle divergence page navigation callback."""
        data = self._divergence_pages_data.get(message_id)
        if not data:
            self.edit_message(message_id, "_Divergence data expired. Run /divergence again._")
            return

        msg, keyboard = self._build_divergence_page(
            data["results"], data["header"], page
        )
        self.edit_message(message_id, msg, reply_markup=keyboard)
