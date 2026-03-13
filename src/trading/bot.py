"""
Futures Trading Bot - Main Entry Point

Multi-timeframe automated trading system for OKX Futures (Isolated)
"""

import asyncio
import time
import os
import json
from pathlib import Path
from typing import Dict, List
from datetime import datetime, timedelta
import logging
from dotenv import load_dotenv

from src.trading.exchanges.okx import OKXFuturesClient
from src.trading.strategy.signal_detector import SignalDetector
from src.trading.execution.position_manager import PositionManager
from src.database.trades_db import TradesDB
from src.trading.notifications.telegram_commands import TelegramCommandHandler
from src.trading.execution.ema610_limit_manager import EMA610LimitManager
from src.trading.core.config import (
    DEFAULT_SYMBOLS,
    UPDATE_INTERVALS,
    LEVERAGE,
    RISK_MANAGEMENT,
    DIVERGENCE_CONFIG,
    DYNAMIC_PAIRS,
    TRAILING_SL,
    CHANDELIER_EXIT,
    SMART_SL,
    EMA610_ENTRY,
    EMA610_EXIT,
    TAKE_PROFIT,
    STANDARD_EXIT,
    STANDARD_ENTRY,
    RSI_DIV_EXIT,
    FEES,
    ENTRY,
    SD_ZONES_CONFIG,
    SD_ENTRY_CONFIG,
    load_overrides,
)
from src.trading.core.models import Position
from src.trading.core.indicators import TechnicalIndicators, ATRIndicator
from src.trading.core.sd_zones import SupplyDemandZones, SDZoneCache, SDCandleCache, get_tf_rank, get_position_tf

# V3 Database logging (optional - bot works without it)
try:
    from src.core.database_manager import DatabaseManager
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/futures_bot.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class FuturesTradingBot:
    """
    Main Futures Trading Bot

    Features:
    - Multi-timeframe signal detection (H4, H1, M15)
    - Automatic position management
    - Real-time PNL tracking
    - Auto-save positions every 30s for crash recovery
    - Risk management (max 1 position per pair, trailing SL EMA89 M15, ROI-based TP)
    """

    def __init__(
        self,
        symbols: List[str] = None,
        api_key: str = None,
        api_secret: str = None,
        passphrase: str = None,
        mode: str = "paper"  # "paper" or "live"
    ):
        """
        Initialize Futures Trading Bot

        Args:
            symbols: List of symbols to trade (default: DEFAULT_SYMBOLS)
            api_key: OKX API key
            api_secret: OKX API secret
            passphrase: OKX API passphrase
            mode: Trading mode - "paper" (demo) or "live" (real money)
        """
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.mode = mode
        self.is_running = False

        # Get API credentials from env if not provided
        self.api_key = api_key or os.getenv('OKX_API_KEY')
        self.api_secret = api_secret or os.getenv('OKX_API_SECRET')
        self.passphrase = passphrase or os.getenv('OKX_PASSPHRASE')

        if mode == "live" and (not self.api_key or not self.api_secret or not self.passphrase):
            raise ValueError("OKX API credentials (key, secret, passphrase) required for live trading")

        # Load config overrides from previous session
        overrides_count = load_overrides()
        if overrides_count > 0:
            logger.info(f"[CONFIG] Applied {overrides_count} saved overrides from config_overrides.json")

        # Initialize components
        logger.info(f"Initializing Futures Trading Bot ({mode} mode) on OKX...")

        # Initialize database (optional - bot works without it)
        self.db = None
        if DB_AVAILABLE:
            try:
                db_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'database')
                os.makedirs(db_dir, exist_ok=True)
                db_path = os.path.join(db_dir, 'futures_trading.db')
                self.db = DatabaseManager(f'sqlite:///{db_path}')
                logger.info(f"[DB] Database initialized: {db_path}")
            except Exception as e:
                logger.error(f"[DB] Failed to initialize database: {e}")
                self.db = None

        self.binance = OKXFuturesClient(self.api_key, self.api_secret, self.passphrase)
        self.signal_detector = SignalDetector(self.binance, db=self.db)

        # SQLite storage for closed trades
        data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data')
        self.trades_db = TradesDB(os.path.join(data_dir, 'trades.db'))
        self._paused_symbols_file = Path(data_dir) / "paused_symbols.json"
        self._paused_symbols_cache: dict = {}
        self._paused_symbols_mtime: float = 0

        self.position_manager = PositionManager(self.binance, mode=mode, db=self.db, trades_db=self.trades_db)
        # Initialize Telegram command handler
        self.telegram = None
        tg_token = os.getenv('TELEGRAM_BOT_TOKEN')
        tg_chat = os.getenv('TELEGRAM_CHAT_ID')
        if tg_token and tg_chat:
            try:
                self.telegram = TelegramCommandHandler(
                    token=tg_token,
                    chat_id=tg_chat,
                    bot_ref=self
                )
                logger.info("[TELEGRAM] Command handler initialized")
                # Pass telegram to position_manager for close notifications
                self.position_manager.telegram = self.telegram
            except Exception as e:
                logger.error(f"[TELEGRAM] Init error: {e}")

        # Track last update times
        self.last_signal_scan = 0
        self.last_position_update = 0
        self.last_divergence_scan = 0
        self.last_pairs_refresh = 0  # Track dynamic pairs refresh
        self._divergence_symbols: List[str] = []  # Cached top symbols for divergence scan
        # EMA610 candle dedup (prevent multiple entries on same candle)
        self._last_ema610_h1_candle: Dict[str, str] = {}  # symbol -> candle_ts
        self._last_ema610_h4_candle: Dict[str, str] = {}  # symbol -> candle_ts
        self.last_ema610_scan = 0

        # S/D Zone cache + blocking
        self.sd_zone_cache = SDZoneCache()
        self.sd_candle_cache = SDCandleCache(
            self.binance,
            Path(__file__).resolve().parent.parent.parent / "data" / "sd_candle_cache",
        )
        self.last_sd_zone_scan = 0
        # Track which positions were already closed by SD zone blocking
        # Key: position_id, prevents re-logging on subsequent scans
        self._sd_zone_closed_positions: set = set()

        # SD zone entry tracking
        self._sd_entry_blocks: list = []  # [{symbol, blocked_side, blocked_tfs, zone_top, zone_bottom}]
        self._sd_last_entry_candle: dict = {}  # f"{symbol}_{tf}_{zone_key}" → candle_ts
        self._sd_zone_consumed: set = set()  # zone dedup_keys with active positions
        self._sd_skip_logged: set = set()  # dedup_keys already logged as skipped (log once)

        # EMA610 limit order manager (pre-place limits at EMA610 price)
        self.ema610_limit_manager = EMA610LimitManager(
            exchange=self.binance,
            position_manager=self.position_manager,
            mode=mode,
        )

        # Candle close detection for immediate CE check
        self._last_m5_boundary = self._get_current_candle_boundary(5)
        self._last_m15_boundary = self._get_current_candle_boundary(15)

        # H1/H4 candle boundaries for EMA610 limit order updates
        self._last_h1_boundary = self._get_current_candle_boundary(60)
        self._last_h4_boundary = self._get_current_candle_boundary(240)

        # Startup cooldown: skip first 2 signal scans to avoid mass opening on restart
        self._startup_scans_skipped = 0
        self._startup_cooldown_scans = 1  # Skip first scan (1 minute warmup)

        # Web notification dedup: {symbol_type: timestamp} to avoid spamming dashboard
        self._web_notif_sent: dict[str, float] = {}
        self._web_notif_cooldown = 3600  # 1 hour cooldown per symbol+type

        # Config hot-reload tracking
        self._config_file = Path(__file__).resolve().parent.parent.parent / "data" / "config.json"
        self._last_config_mtime = 0
        self._last_config_check = 0
        self._check_and_reload_config()  # Initial load

        logger.info(f"Bot initialized with {len(self.symbols)} symbols")

    def _check_and_reload_config(self):
        """Check if config.json has changed and reload if necessary."""
        try:
            if not self._config_file.exists():
                return

            mtime = self._config_file.stat().st_mtime
            if mtime > self._last_config_mtime:
                logger.info(f"[CONFIG] Detected config change, reloading...")
                
                with open(self._config_file, 'r') as f:
                    new_config = json.load(f)
                
                # Update global config dictionaries in-place
                # This works because we imported the objects from config.py
                # and we are modifying their contents.
                
                # Map section names to imported global variables
                config_map = {
                    "LEVERAGE": LEVERAGE,
                    "RISK_MANAGEMENT": RISK_MANAGEMENT,
                    "TAKE_PROFIT": TAKE_PROFIT,
                    "TRAILING_SL": TRAILING_SL,
                    "CHANDELIER_EXIT": CHANDELIER_EXIT,
                    "SMART_SL": SMART_SL,
                    "EMA610_ENTRY": EMA610_ENTRY,
                    "EMA610_EXIT": EMA610_EXIT,
                    "STANDARD_EXIT": STANDARD_EXIT,
                    "DIVERGENCE_CONFIG": DIVERGENCE_CONFIG,
                    "DYNAMIC_PAIRS": DYNAMIC_PAIRS,
                    "UPDATE_INTERVALS": UPDATE_INTERVALS,
                    "STANDARD_ENTRY": STANDARD_ENTRY,
                    "ENTRY": ENTRY,
                    "SD_ENTRY_CONFIG": SD_ENTRY_CONFIG,
                }
                
                updated_sections = []
                for section_name, config_obj in config_map.items():
                    if section_name in new_config:
                        # Clear and update to ensure removed keys are handled if necessary
                        # But mostly we just want to update values
                        # config_obj.clear() # DANGEROUS if new_config is partial?
                        # Assuming config.json is full snapshot for that section
                        
                        # Careful: deep update might be safer, but for now 
                        # let's assume flat dicts or simple nested ones.
                        # Using update() is safe for adding/modifying keys.
                        config_obj.update(new_config[section_name])
                        updated_sections.append(section_name)
                
                self._last_config_mtime = mtime
                logger.info(f"[CONFIG] Reloaded sections: {', '.join(updated_sections)}")
                
                # Special handling for Dynamic Pairs if updated
                if "DYNAMIC_PAIRS" in updated_sections:
                     self._refresh_trading_pairs()

        except Exception as e:
            logger.error(f"[CONFIG] Failed to reload config: {e}")

    def _check_force_refresh(self):
        """Check if web dashboard requested a force refresh of pairs."""
        try:
            flag_file = self._config_file.parent / "force_refresh_pairs.flag"
            if flag_file.exists():
                flag_file.unlink()
                logger.info("[PAIRS] Force refresh requested via web dashboard")
                self._refresh_trading_pairs()
                self.last_pairs_refresh = time.time()
        except Exception as e:
            logger.error(f"[PAIRS] Error checking force refresh flag: {e}")

    @staticmethod
    def _get_current_candle_boundary(minutes: int = 15) -> int:
        """Get the current candle boundary as unix timestamp.

        Returns the start time of the current candle (floored to N minutes).
        E.g. at 21:23 with minutes=15 → returns timestamp for 21:15.
        E.g. at 21:23 with minutes=5 → returns timestamp for 21:20.
        """
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        minute_floored = (now.minute // minutes) * minutes
        boundary = now.replace(minute=minute_floored, second=0, microsecond=0)
        return int(boundary.timestamp())

    async def _check_web_commands(self):
        """Process action commands from web dashboard (file-based IPC)."""
        try:
            commands_dir = self._config_file.parent / "web_commands"
            if not commands_dir.exists():
                return

            for cmd_file in sorted(commands_dir.glob("*.json")):
                try:
                    cmd = json.loads(cmd_file.read_text(encoding="utf-8"))
                    await self._execute_web_command(cmd)
                except Exception as e:
                    logger.error(f"[WEB_CMD] Error processing {cmd_file.name}: {e}")
                finally:
                    cmd_file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"[WEB_CMD] Error scanning commands dir: {e}")

    async def _execute_web_command(self, cmd: dict):
        """Execute a single web dashboard command."""
        command = cmd.get("command")
        position_id = cmd.get("position_id")
        params = cmd.get("params", {})
        pm = self.position_manager

        if command == "close":
            # Track recently closed symbol for orphan detection cooldown
            pos = pm.positions.get(position_id)
            if pos:
                if not hasattr(self, '_recently_closed'):
                    self._recently_closed = {}
                self._recently_closed[pos.symbol] = time.time()
                # Cancel any pending EMA610 limit orders for this symbol
                # to prevent immediate re-entry race condition
                if hasattr(self, 'ema610_limit_manager'):
                    await self.ema610_limit_manager.cancel_for_symbol(pos.symbol)
                    logger.info(f"[WEB_CMD] Cancelled EMA610 limit orders for {pos.symbol}")
            pm.close_position(position_id, reason="MANUAL_WEB")
            logger.info(f"[WEB_CMD] Closed {position_id}")

        elif command == "partial_close":
            percent = params.get("percent", 50)
            result = pm.partial_close_manual(position_id, percent)
            if result:
                logger.info(f"[WEB_CMD] Partial close {percent}% on {position_id}")
            else:
                logger.warning(f"[WEB_CMD] Partial close failed for {position_id}")

        elif command == "cancel_tp":
            level = params.get("level", "all")
            success = pm.cancel_tp(position_id, level)
            logger.info(f"[WEB_CMD] Cancel {level} on {position_id}: {'OK' if success else 'FAIL'}")

        elif command == "modify_sl":
            price = params.get("price")
            if price:
                success = pm.modify_sl(position_id, float(price))
                logger.info(f"[WEB_CMD] Modify SL {position_id} → {price}: {'OK' if success else 'FAIL'}")
            else:
                logger.warning(f"[WEB_CMD] modify_sl missing price param")

        elif command == "modify_tp":
            level = params.get("level")
            price = params.get("price")
            if level and price:
                success = pm.modify_tp(position_id, level, float(price))
                logger.info(f"[WEB_CMD] Modify {level.upper()} {position_id} → {price}: {'OK' if success else 'FAIL'}")
            else:
                logger.warning(f"[WEB_CMD] modify_tp missing level/price param")

        elif command == "cancel_ema610":
            symbol = params.get("symbol")
            timeframe = params.get("timeframe")
            if symbol and timeframe:
                await self.ema610_limit_manager.cancel_for_symbol(symbol, timeframe)
                # Set cooldown to prevent re-placing for 8h
                from src.trading.core.config import EMA610_ENTRY
                cooldown_sec = EMA610_ENTRY.get("manual_cancel_cooldown", 28800)
                self.ema610_limit_manager.set_manual_cooldown(symbol, timeframe, cooldown_sec)
                logger.info(f"[WEB_CMD] Cancelled EMA610 limit: {symbol} {timeframe} (cooldown {cooldown_sec // 3600}h)")
            else:
                logger.warning(f"[WEB_CMD] cancel_ema610 missing symbol/timeframe param")

        else:
            logger.warning(f"[WEB_CMD] Unknown command: {command}")

    def _refresh_trading_pairs(self):
        """
        Refresh trading pairs list from top volume on Binance Futures.
        Called every 30 minutes (configurable via DYNAMIC_PAIRS['refresh_interval']).

        Supports multi-window volume scanning (24h, 48h, 72h).
        Applies whitelist/blacklist filtering from config:
        - whitelist (non-empty): ONLY trade these symbols
        - blacklist: NEVER trade these symbols (shitcoins, pump & dump)

        Keeps existing positions safe: if a coin drops out of top N,
        its open position continues but no new positions will open.
        """
        if not DYNAMIC_PAIRS.get('enabled', False):
            return

        try:
            whitelist = DYNAMIC_PAIRS.get('whitelist', [])
            blacklist = set(DYNAMIC_PAIRS.get('blacklist', []))

            # Get volume_windows config (backward compat: fall back to top_n)
            volume_windows = DYNAMIC_PAIRS.get('volume_windows')
            if not volume_windows:
                top_n = DYNAMIC_PAIRS.get('top_n', 30)
                volume_windows = {"24h": top_n, "48h": 0, "72h": 0}

            # Fetch using multi-window method
            result = self.signal_detector.fetch_top_futures_symbols_multi(volume_windows)
            new_symbols = result.get("symbols", [])
            details = result.get("details", {})

            if not new_symbols:
                logger.warning("[PAIRS] Failed to fetch top volume pairs, keeping current list")
                return

            # Apply whitelist (if non-empty, only trade these)
            if whitelist:
                new_symbols = [s for s in new_symbols if s in whitelist]
                logger.info(f"[PAIRS] Whitelist applied: {len(new_symbols)} pairs remain")

            # Apply blacklist (always remove these)
            if blacklist:
                blocked = [s for s in new_symbols if s in blacklist]
                if blocked:
                    logger.info(f"[PAIRS] Blacklist removed: {blocked}")
                new_symbols = [s for s in new_symbols if s not in blacklist]

            old_symbols = set(self.symbols)
            new_symbols_set = set(new_symbols)

            added = new_symbols_set - old_symbols
            removed = old_symbols - new_symbols_set

            self.symbols = new_symbols

            logger.info(
                f"[PAIRS] Refreshed: {len(new_symbols)} pairs | "
                f"Added: {sorted(added) if added else 'none'} | "
                f"Removed: {sorted(removed) if removed else 'none'}"
            )

            # Write active_pairs.json for web dashboard
            self._write_active_pairs(new_symbols, details)

            # Notify via Telegram
            if self.telegram and (added or removed):
                total = sum(v for v in volume_windows.values() if v > 0)
                msg = f"*Pairs Updated*\n"
                msg += f"Scanning {len(new_symbols)} pairs (windows: {volume_windows}):\n"
                msg += ", ".join(new_symbols)
                if added:
                    msg += f"\n\n➕ Added: {', '.join(sorted(added))}"
                if removed:
                    msg += f"\n➖ Removed: {', '.join(sorted(removed))}"
                self.telegram.send_message(msg)

        except Exception as e:
            logger.error(f"[PAIRS] Error refreshing trading pairs: {e}")

    def _write_active_pairs(self, symbols: list, details: dict):
        """Write active pairs data to JSON for the web dashboard."""
        try:
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            filepath = data_dir / "active_pairs.json"

            pairs_data = []
            for sym in symbols:
                info = details.get(sym, {})
                pairs_data.append({
                    "symbol": sym,
                    "volume_24h": info.get("volume_24h", 0),
                    "volume_48h": info.get("volume_48h"),
                    "volume_72h": info.get("volume_72h"),
                    "source_windows": info.get("source_windows", []),
                })

            payload = {
                "pairs": pairs_data,
                "total": len(pairs_data),
                "last_refresh": datetime.now().isoformat(),
                "volume_windows": DYNAMIC_PAIRS.get("volume_windows", {}),
            }

            with open(filepath, 'w') as f:
                json.dump(payload, f, indent=2)

            logger.debug(f"[PAIRS] Wrote active_pairs.json ({len(pairs_data)} pairs)")
        except Exception as e:
            logger.error(f"[PAIRS] Failed to write active_pairs.json: {e}")

    def _scan_divergences(self):
        """
        Scan top 500 symbols for RSI divergence on H4+D1.
        First scan of the day: send full summary.
        Subsequent scans: only send new symbols not seen before today.
        """
        try:
            # Fetch top symbols (cached, refreshed each scan)
            top_n = DIVERGENCE_CONFIG.get('scan_top_n', 500)
            self._divergence_symbols = self.signal_detector.fetch_top_futures_symbols(limit=top_n)

            if not self._divergence_symbols:
                logger.warning("[DIVERGENCE] No symbols fetched, skipping scan")
                return

            is_first_scan = not hasattr(self, '_divergence_scanned_today') or not self._divergence_scanned_today
            logger.info(f"[DIVERGENCE] Scanning {len(self._divergence_symbols)} symbols for H4+D1 divergence...")

            all_results, new_results = self.signal_detector.scan_divergences(self._divergence_symbols)

            if self.telegram:
                if is_first_scan and all_results:
                    # First scan today: send full summary
                    self.telegram.send_divergence_summary(all_results)
                    self._divergence_scanned_today = True
                elif new_results:
                    # Subsequent scans: only send new symbols
                    header = f"*NEW DIVERGENCES* ({len(new_results)} new symbols)\n"
                    self.telegram.send_divergence_summary(new_results, header=header)

            # Log scan operation to database
            if self.db:
                try:
                    self.db.log_operation(
                        operation_name='scan_divergences',
                        risk_score=0,
                        status='success',
                        meta_data={
                            'symbols_scanned': len(self._divergence_symbols),
                            'total_divergences': len(all_results),
                            'new_divergences': len(new_results),
                            'new_symbols': list(new_results.keys()) if new_results else [],
                        }
                    )
                except Exception as e:
                    logger.error(f"[DB] Error logging divergence scan: {e}")
        except Exception as e:
            logger.error(f"Error in divergence scan: {e}")

    async def _scan_rsi_div_entries(self):
        """
        Scan RSI divergence on M15/H1/H4 and open positions when detected.
        1 position per coin per TF. Closes existing EMA positions before entry.
        M15 divergence also blocks EMA entries until RSI resets to 50.
        """
        try:
            for tf in ['m15', 'h1', 'h4']:
                if not RSI_DIV_EXIT.get(tf, {}).get('enabled', True):
                    continue

                signals = self.signal_detector.scan_divergence_entries(self.symbols, tf)
                if not signals:
                    continue

                for symbol, signal in signals.items():
                    # Check slot available (1 rsi_div per coin per TF)
                    if not self.position_manager.can_open_position(
                        symbol, signal.entry_type, side=signal.signal_type
                    ):
                        logger.debug(
                            f"[RSI-DIV] {symbol}: {signal.entry_type} {signal.signal_type} "
                            f"slot taken, skipping"
                        )
                        continue

                    # Close existing EMA positions on same coin before entering
                    await self._close_ema_positions_for_symbol(symbol)

                    # M15: set EMA block (bearish div → block BUY, bullish div → block SELL)
                    if tf == 'm15':
                        blocked_dir = "BUY" if signal.signal_type == "SELL" else "SELL"
                        self.signal_detector.set_m15_ema_block(symbol, blocked_dir)
                        logger.info(
                            f"[RSI-DIV-BLOCK] {symbol}: M15 {signal.signal_type} div → "
                            f"blocking EMA {blocked_dir} until RSI resets to 50"
                        )

                    await self._process_signal(signal)

                logger.info(f"[RSI-DIV] {tf.upper()} scan: {len(signals)} divergence signals found")

        except Exception as e:
            logger.error(f"[RSI-DIV] Error in RSI divergence entry scan: {e}")

    async def _close_ema_positions_for_symbol(self, symbol: str):
        """Close all standard_* and ema610_* positions for a symbol (RSI div override)."""
        try:
            positions = self.position_manager.get_open_positions()
            ema_positions = [
                p for p in positions
                if p.symbol == symbol
                and (p.entry_type.startswith("standard_") or p.entry_type.startswith("ema610_"))
                and p.status in ("OPEN", "PARTIAL_CLOSE")
            ]
            for pos in ema_positions:
                logger.info(
                    f"[RSI-DIV-OVERRIDE] Closing {pos.entry_type} {pos.side} {symbol} "
                    f"(making room for RSI divergence entry)"
                )
                # Track recently closed for orphan/EMA610 cooldown
                if not hasattr(self, '_recently_closed'):
                    self._recently_closed = {}
                self._recently_closed[symbol] = time.time()
                self.position_manager.close_position(
                    pos.position_id, reason="RSI_DIV_OVERRIDE"
                )
        except Exception as e:
            logger.error(f"[RSI-DIV-OVERRIDE] Error closing EMA positions for {symbol}: {e}")

    async def _check_sd_zone_blocking(self):
        """Check if price is inside any S/D zone and close opposite positions.

        Supply zone → close BUY positions with TF ≤ zone TF (keep higher TF)
        Demand zone → close SELL positions with TF ≤ zone TF (keep higher TF)
        Respects timeframe hierarchy: m5 < m15 < h1 < h4 < 1d
        """
        blocking_cfg = SD_ZONES_CONFIG.get('blocking', {})
        if not blocking_cfg.get('enabled', True):
            return

        close_prefixes = blocking_cfg.get('close_entry_types', ['standard_', 'ema610_'])
        min_zone_tf = blocking_cfg.get('min_zone_tf', '15m')
        min_zone_rank = get_tf_rank(min_zone_tf)

        positions = self.position_manager.get_open_positions()
        if not positions:
            return

        # Group positions by symbol for efficient lookup
        positions_by_symbol: Dict[str, list] = {}
        for pos in positions:
            positions_by_symbol.setdefault(pos.symbol, []).append(pos)

        closed_count = 0

        for symbol, symbol_positions in positions_by_symbol.items():
            all_zones = self.sd_zone_cache.get_all_for_symbol(symbol)
            if not all_zones:
                continue

            # Get current price from most recent position update or cache
            current_price = None
            for pos in symbol_positions:
                if pos.current_price > 0:
                    current_price = pos.current_price
                    break

            if current_price is None:
                continue

            # Check each timeframe's zones
            for tf, zones in all_zones.items():
                zone_rank = get_tf_rank(tf)
                if zone_rank < min_zone_rank:
                    continue  # Skip zones below minimum TF (e.g., 5m)

                for zone in zones:
                    # Is price inside this zone?
                    if not (zone.bottom <= current_price <= zone.top):
                        continue

                    # Supply zone → close BUY positions with TF ≤ zone TF
                    # Demand zone → close SELL positions with TF ≤ zone TF
                    target_side = "BUY" if zone.zone_type == "supply" else "SELL"

                    for pos in symbol_positions:
                        if pos.status not in ("OPEN", "PARTIAL_CLOSE"):
                            continue
                        if pos.side != target_side:
                            continue
                        if pos.position_id in self._sd_zone_closed_positions:
                            continue

                        # Check if entry_type matches close_prefixes
                        if not any(pos.entry_type.startswith(p) for p in close_prefixes):
                            continue

                        # Check TF hierarchy: only close if position TF ≤ zone TF
                        pos_tf = get_position_tf(pos.entry_type)
                        if pos_tf is None:
                            continue
                        pos_rank = get_tf_rank(pos_tf)
                        if pos_rank > zone_rank:
                            continue  # Higher TF position — keep it

                        logger.info(
                            f"[SD-BLOCK] Closing {pos.entry_type} {pos.side} {symbol} "
                            f"— price {current_price:.4g} inside {zone.zone_type} zone "
                            f"{zone.bottom:.4g}-{zone.top:.4g} ({tf.upper()})"
                        )
                        self._sd_zone_closed_positions.add(pos.position_id)

                        # Track recently closed for orphan/EMA610 cooldown
                        if not hasattr(self, '_recently_closed'):
                            self._recently_closed = {}
                        self._recently_closed[symbol] = time.time()

                        try:
                            self.position_manager.close_position(
                                pos.position_id, reason="SD_ZONE_BLOCK"
                            )
                            closed_count += 1
                        except Exception as e:
                            logger.error(
                                f"[SD-BLOCK] Failed to close {pos.position_id}: {e}"
                            )

        if closed_count > 0:
            logger.info(f"[SD-BLOCK] Closed {closed_count} position(s) due to S/D zone blocking")

        # Cleanup tracker: remove stale entries for closed positions
        active_ids = {p.position_id for p in positions}
        self._sd_zone_closed_positions = self._sd_zone_closed_positions & active_ids

    def _is_sd_zone_blocked(self, symbol: str, side: str, entry_type: str,
                            signal_price: float = None) -> bool:
        """Check if a new entry should be blocked by an active S/D zone.

        Returns True if current price is inside a zone that blocks this direction+TF.
        Supply zone blocks BUY entries with TF ≤ zone TF.
        Demand zone blocks SELL entries with TF ≤ zone TF.
        """
        blocking_cfg = SD_ZONES_CONFIG.get('blocking', {})
        if not blocking_cfg.get('enabled', True):
            return False

        block_prefixes = blocking_cfg.get('block_entry_types', ['standard_', 'ema610_'])
        if not any(entry_type.startswith(p) for p in block_prefixes):
            return False

        min_zone_tf = blocking_cfg.get('min_zone_tf', '15m')
        min_zone_rank = get_tf_rank(min_zone_tf)

        # Get entry TF rank
        entry_tf = get_position_tf(entry_type)
        if entry_tf is None:
            return False
        entry_rank = get_tf_rank(entry_tf)

        all_zones = self.sd_zone_cache.get_all_for_symbol(symbol)
        if not all_zones:
            return False

        # Get current price from signal or position
        current_price = None
        for pos in self.position_manager.get_open_positions():
            if pos.symbol == symbol and pos.current_price > 0:
                current_price = pos.current_price
                break

        if current_price is None and signal_price is not None:
            current_price = signal_price

        if current_price is None:
            # Try from OHLCV cache (signal detector has it)
            try:
                df = self.signal_detector._cache.fetch(symbol, '15m', 5)
                if df is not None and len(df) > 0:
                    current_price = df['close'].iloc[-1]
            except Exception:
                pass

        if current_price is None:
            return False

        # Check each timeframe's zones
        for tf, zones in all_zones.items():
            zone_rank = get_tf_rank(tf)
            if zone_rank < min_zone_rank:
                continue

            for zone in zones:
                if not (zone.bottom <= current_price <= zone.top):
                    continue

                # Supply blocks BUY, demand blocks SELL
                blocked_side = "BUY" if zone.zone_type == "supply" else "SELL"
                if side != blocked_side:
                    continue

                # Only block if entry TF ≤ zone TF
                if entry_rank <= zone_rank:
                    logger.info(
                        f"[SD-BLOCK] {symbol} {side} {entry_type} BLOCKED — "
                        f"price {current_price:.4g} in {zone.zone_type} zone "
                        f"{zone.bottom:.4g}-{zone.top:.4g} ({tf.upper()})"
                    )
                    return True

        return False

    def _scan_m15_divergences(self):
        """
        Scan M15 divergence for active trading pairs.
        Runs after each signal scan — uses cached OHLCV data so minimal overhead.
        Sends Telegram alert + web notification for new divergences (4h cooldown per symbol+type).
        """
        try:
            new_results = self.signal_detector.scan_m15_divergences(self.symbols)

            if new_results:
                # Telegram alerts
                if self.telegram:
                    for symbol, divergences in new_results.items():
                        self.telegram.send_m15_divergence_alert(symbol, divergences)

                # Web dashboard notifications (file-based IPC)
                self._queue_web_notifications(new_results)

                logger.info(f"[M15-DIV] Alerted {len(new_results)} symbols with new M15 divergences")

        except Exception as e:
            logger.error(f"[M15-DIV] Error in M15 divergence scan: {e}")

    def _scan_h1h4_divergences(self):
        """Scan H1 and H4 divergence and send Telegram alerts."""
        try:
            results = self.signal_detector.scan_h1h4_divergences(self.symbols)
            for tf_label, symbol_divs in results.items():
                if self.telegram:
                    for symbol, divergences in symbol_divs.items():
                        self.telegram.send_divergence_alert(symbol, divergences, timeframe=tf_label)
                logger.info(f"[{tf_label}-DIV] Alerted {len(symbol_divs)} symbols with new {tf_label} divergences")
        except Exception as e:
            logger.error(f"[H1H4-DIV] Error in H1/H4 divergence scan: {e}")

    def _queue_web_notifications(self, divergence_results: dict):
        """Write divergence alerts to notification queue for web dashboard."""
        try:
            queue_file = self._config_file.parent / "web_notifications.json"

            # Read existing queue
            existing = []
            if queue_file.exists():
                try:
                    existing = json.loads(queue_file.read_text(encoding="utf-8"))
                except Exception:
                    existing = []

            # Append new notifications
            type_labels = {
                "bearish": "Bearish Divergence",
                "bullish": "Bullish Divergence",
                "hidden_bearish": "Hidden Bearish",
                "hidden_bullish": "Hidden Bullish",
            }
            type_icons = {
                "bearish": "🔴", "bullish": "🟢",
                "hidden_bearish": "🟠", "hidden_bullish": "🔵",
            }

            now = datetime.now().isoformat()
            now_ts = datetime.now().timestamp()
            for symbol, divergences in divergence_results.items():
                for d in divergences:
                    # Dedup: skip if same symbol+type sent within cooldown
                    dedup_key = f"{symbol}_{d.divergence_type}"
                    last_sent = self._web_notif_sent.get(dedup_key, 0)
                    if now_ts - last_sent < self._web_notif_cooldown:
                        continue

                    label = type_labels.get(d.divergence_type, d.divergence_type)
                    icon = type_icons.get(d.divergence_type, "⚠")
                    existing.append({
                        "type": "divergence",
                        "symbol": symbol,
                        "message": f"{icon} {symbol} M15: {label} (RSI {d.rsi_swing_1:.1f}→{d.rsi_swing_2:.1f})",
                        "level": "warning",
                        "timestamp": now,
                    })
                    self._web_notif_sent[dedup_key] = now_ts

            # Keep max 50 notifications (prevent file bloat)
            existing = existing[-50:]
            queue_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")

        except Exception as e:
            logger.error(f"[M15-DIV] Error writing web notifications: {e}")

    async def start(self):
        """
        Start the trading bot

        Main loop:
        1. Scan for signals (every 60s)
        2. Update positions + auto-save (every 30s)
        """
        self.is_running = True
        logger.info("[BOT] Futures Trading Bot V8 started!")

        if self.mode == "paper":
            logger.warning("[BOT] PAPER TRADING MODE - No real orders will be executed")

        # Verify pending EMA610 limit orders from previous session
        if EMA610_ENTRY.get('use_limit_orders', True):
            startup_fills = await self.ema610_limit_manager.verify_orders_on_startup()
            if startup_fills:
                logger.info(f"[STARTUP] {len(startup_fills)} EMA610 orders filled while bot was down")
                for fill in startup_fills:
                    signal = type('Signal', (), {
                        'symbol': fill['symbol'],
                        'signal_type': fill['side'],
                        'entry_price': fill['entry_price'],
                        'entry_type': fill['entry_type'],
                        'take_profit_1': fill['tp1_price'],
                        'take_profit_2': fill['tp2_price'],
                        'h4_trend': fill['h4_trend'],
                        'h1_rsi': 0,
                        'm15_ema34': 0, 'm15_ema89': 0, 'wick_ratio': 0,
                    })()
                    logger.info(
                        f"[STARTUP] Creating position for filled EMA610: "
                        f"{fill['side']} {fill['symbol']} @ ${fill['entry_price']:.6f}"
                    )
                    await self._process_signal(signal)

        # Start Telegram command handler
        if self.telegram:
            self.telegram.start()
            vw = DYNAMIC_PAIRS.get('volume_windows', {})
            active_windows = {k: v for k, v in vw.items() if v > 0}
            pairs_mode = f"Dynamic {active_windows}" if DYNAMIC_PAIRS.get('enabled') else "Fixed"
            self.telegram.send_message(
                f"*Bot V8 Started*\nMode: {self.mode.upper()}\n"
                f"TP: ROI-based (M15 +{STANDARD_EXIT['m15']['tp1_roi']}%/+{STANDARD_EXIT['m15']['tp2_roi']}%, H1 +{STANDARD_EXIT['h1']['tp1_roi']}%/+{STANDARD_EXIT['h1']['tp2_roi']}%, H4 +{STANDARD_EXIT['h4']['tp1_roi']}%/+{STANDARD_EXIT['h4']['tp2_roi']}%)\n"
                f"Pairs: {pairs_mode} ({len(self.symbols)} symbols)\n"
                f"Max: 1 position/symbol\nScanning..."
            )

        try:
            while self.is_running:
                current_time = time.time()

                # Web commands: check every loop (1s) for instant response
                await self._check_web_commands()

                # ── PRIORITY: Candle close → immediate CE check ─────────
                # This MUST run before signal scans (which can take 10+ min).
                # Detects when a new M5/M15 boundary is crossed, waits 3s for
                # exchange data to finalize, then forces position update.
                has_open_positions = bool(self.position_manager.positions)
                position_updated = False

                # M5 boundary check (for standard_m5 CE)
                current_m5 = self._get_current_candle_boundary(5)
                if current_m5 != self._last_m5_boundary:
                    seconds_into_m5 = current_time - current_m5
                    has_m5 = has_open_positions and any(
                        p.entry_type == "standard_m5" for p in self.position_manager.positions.values()
                        if p.status in ("OPEN", "PARTIAL_CLOSE")
                    )
                    if has_m5 and 3 <= seconds_into_m5 <= 15:
                        logger.info(f"[CE] M5 candle closed — forcing position update ({seconds_into_m5:.0f}s)")
                        await self._update_positions()
                        self.last_position_update = current_time
                        position_updated = True
                        self._last_m5_boundary = current_m5
                    elif seconds_into_m5 > 15:
                        self._last_m5_boundary = current_m5

                # M15 boundary check (for all other CE)
                current_m15 = self._get_current_candle_boundary(15)
                if current_m15 != self._last_m15_boundary:
                    seconds_into_candle = current_time - current_m15
                    if has_open_positions and not position_updated and 3 <= seconds_into_candle <= 20:
                        logger.info(f"[CE] M15 candle closed — forcing position update ({seconds_into_candle:.0f}s)")
                        await self._update_positions()
                        self.last_position_update = current_time
                        self._last_m15_boundary = current_m15
                    elif seconds_into_candle > 20:
                        self._last_m15_boundary = current_m15

                # Task 0.5: Check for config updates (every 5s)
                if current_time - self._last_config_check >= 5:
                    self._check_and_reload_config()
                    self._check_force_refresh()
                    self._last_config_check = current_time

                # Task 0: Refresh trading pairs (every 30m, top N by volume)
                pairs_interval = DYNAMIC_PAIRS.get('refresh_interval', 1800)
                if current_time - self.last_pairs_refresh >= pairs_interval:
                    self._refresh_trading_pairs()
                    self.last_pairs_refresh = current_time

                # Task 1: Scan for trading signals (every 60s)
                if current_time - self.last_signal_scan >= UPDATE_INTERVALS['market_data']:
                    await self._scan_signals()
                    self.last_signal_scan = current_time

                # Task 1.5: EMA610 entry scan (every 60s, same as signal scan)
                if EMA610_ENTRY.get('enabled', True) and current_time - self.last_ema610_scan >= UPDATE_INTERVALS['market_data']:
                    # Check for filled limit orders first
                    if EMA610_ENTRY.get('use_limit_orders', True):
                        await self._process_ema610_limit_fills()

                    # H1/H4 boundary detection: re-scan when candle closes
                    h1_changed = False
                    h4_changed = False
                    current_h1 = self._get_current_candle_boundary(60)
                    if current_h1 != self._last_h1_boundary:
                        h1_changed = True
                        self._last_h1_boundary = current_h1
                    current_h4 = self._get_current_candle_boundary(240)
                    if current_h4 != self._last_h4_boundary:
                        h4_changed = True
                        self._last_h4_boundary = current_h4

                    await self._scan_ema610(
                        force_h1_update=h1_changed,
                        force_h4_update=h4_changed,
                    )
                    self.last_ema610_scan = current_time

                # Task 1.6: S/D Zone scan + blocking (every 5 min)
                sd_interval = SD_ZONES_CONFIG.get('scan_interval', 300)
                if SD_ZONES_CONFIG.get('enabled', True) and current_time - self.last_sd_zone_scan >= sd_interval:
                    await self._scan_sd_zones()
                    # SD zone entry scan (piggyback on zone scan — uses fresh candle data)
                    if SD_ENTRY_CONFIG.get('enabled', True):
                        await self._scan_sd_entries()
                    # After zones update, check if any positions need closing
                    await self._check_sd_zone_blocking()
                    self.last_sd_zone_scan = current_time

                # Task 1.7: Divergence scan (DISABLED - too resource intensive)
                # Scan 500 symbols every 4h was spamming logs and consuming resources
                # Only scan divergence for active trading pairs (done in signal detection)
                # div_interval = DIVERGENCE_CONFIG.get('scan_interval', 14400)
                # if current_time - self.last_divergence_scan >= div_interval:
                #     self._scan_divergences()
                #     self.last_divergence_scan = current_time

                # Task 2: Update positions (regular interval as fallback)
                if current_time - self.last_position_update >= UPDATE_INTERVALS['position_check']:
                    await self._update_positions()
                    self.last_position_update = current_time

                # Calculate next task time
                next_times = [
                    self._last_config_check + 5,
                    self.last_pairs_refresh + DYNAMIC_PAIRS.get('refresh_interval', 1800),
                    self.last_signal_scan + UPDATE_INTERVALS['market_data'],
                    self.last_position_update + UPDATE_INTERVALS['position_check'],
                ]
                if EMA610_ENTRY.get('enabled', True):
                    next_times.append(self.last_ema610_scan + UPDATE_INTERVALS['market_data'])
                sleep_until = min(next_times)
                sleep_duration = max(0.1, sleep_until - time.time())
                await asyncio.sleep(min(sleep_duration, 1))  # Cap at 1s for web commands responsiveness

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)
        finally:
            await self.stop()

    async def stop(self):
        """Stop the trading bot"""
        self.is_running = False

        # Stop Telegram handler
        if self.telegram:
            self.telegram.send_message("*Bot Stopped*")
            self.telegram.stop()

        logger.info("[BOT] Futures Trading Bot stopped")

    async def _scan_signals(self):
        """
        Scan all symbols for trading signals
        """
        # Startup cooldown: skip first N scans to avoid mass opening on restart
        if self._startup_scans_skipped < self._startup_cooldown_scans:
            self._startup_scans_skipped += 1
            logger.info(
                f"[SCAN] Startup cooldown: skipping scan {self._startup_scans_skipped}/{self._startup_cooldown_scans} "
                f"(warming up, no new positions)"
            )
            return

        logger.info(f"[SCAN] Scanning {len(self.symbols)} symbols for signals...")

        signals = self.signal_detector.scan_for_signals(self.symbols)

        # Process signals (scan returns Dict[str, List[TradingSignal]])
        for symbol, signal_list in signals.items():
            for signal in signal_list:
                await self._process_signal(signal)

        # Divergence scans (piggyback on signal scan, uses cached OHLCV)
        self._scan_m15_divergences()
        self._scan_h1h4_divergences()

        # Update M15 EMA blocks (clear when RSI resets to 50)
        self.signal_detector.update_m15_ema_blocks(self.symbols)

        # RSI divergence entry scan (M15/H1/H4 → open positions)
        await self._scan_rsi_div_entries()

        # S/D Zone blocking: close positions inside zones (runs every signal scan = 60s)
        # Entry blocking is handled in _process_signal() via _is_sd_zone_blocked()
        await self._check_sd_zone_blocking()

    def _is_symbol_paused(self, symbol: str) -> bool:
        """Check if a symbol is temporarily paused via dashboard.
        Reads data/paused_symbols.json — always re-reads file (no stale cache)."""
        try:
            if not self._paused_symbols_file.exists():
                self._paused_symbols_cache = {}
                return False
            # Always re-read on mtime change (handles web backend writes)
            mtime = self._paused_symbols_file.stat().st_mtime
            if mtime != self._paused_symbols_mtime:
                with open(self._paused_symbols_file, "r", encoding="utf-8") as f:
                    raw = f.read().strip()
                    self._paused_symbols_cache = json.loads(raw) if raw else {}
                self._paused_symbols_mtime = mtime
                logger.info(f"[PAUSE] Reloaded paused symbols: {list(self._paused_symbols_cache.keys())}")
            expiry_str = self._paused_symbols_cache.get(symbol)
            if not expiry_str:
                return False
            expiry = datetime.fromisoformat(expiry_str)
            if datetime.now() >= expiry:
                # Expired — clean up
                del self._paused_symbols_cache[symbol]
                with open(self._paused_symbols_file, "w", encoding="utf-8") as f:
                    json.dump(self._paused_symbols_cache, f, indent=2)
                self._paused_symbols_mtime = self._paused_symbols_file.stat().st_mtime
                logger.info(f"[PAUSE] {symbol} pause expired, resumed trading")
                return False
            remaining = expiry - datetime.now()
            logger.info(f"[PAUSE] {symbol} is PAUSED — {remaining.seconds // 3600}h {(remaining.seconds % 3600) // 60}m remaining")
            return True
        except Exception as e:
            logger.warning(f"[PAUSE] Error checking pause for {symbol}: {e} — treating as PAUSED for safety")
            return True  # Fail-safe: if error reading pause file, assume paused

    async def _process_signal(self, signal, skip_exchange_order: bool = False):
        """
        Process a trading signal (open position if conditions met)

        Args:
            signal: TradingSignal object
            skip_exchange_order: If True, skip placing market order on exchange
                (used for EMA610 limit fills where order already executed)
        """
        symbol = signal.symbol

        # Check if symbol is temporarily paused from dashboard
        if self._is_symbol_paused(symbol):
            logger.info(f"[PAUSE] {symbol} is paused — skipping signal")
            return

        logger.info(
            f"[SIGNAL] Signal detected: {signal.signal_type} {symbol} @ ${signal.entry_price:.2f}"
        )

        # Check if can open position (V7.2: per entry_type slot)
        entry_type = getattr(signal, 'entry_type', 'standard_m15')
        if not self.position_manager.can_open_position(symbol, entry_type, side=signal.signal_type):
            logger.debug(f"Cannot open {signal.signal_type} {entry_type} position for {symbol}: slot taken or max reached")
            return

        # Cancel nearby pending EMA610 limit orders (within 2% of current price)
        # Prevents OKX net mode from offsetting standard + EMA610 opposite positions
        if entry_type.startswith("standard_"):
            for key, pending in list(self.ema610_limit_manager.pending_orders.items()):
                if pending.symbol == symbol:
                    distance_pct = abs(signal.entry_price - pending.limit_price) / pending.limit_price * 100
                    if distance_pct <= 2.0:
                        logger.info(
                            f"[EMA610-CANCEL] {symbol}: Cancelling pending EMA610 {pending.side} "
                            f"limit @ {pending.limit_price:.4f} (distance {distance_pct:.2f}% <= 2%) "
                            f"— standard {signal.signal_type} entry @ {signal.entry_price:.4f}"
                        )
                        await self.ema610_limit_manager._cancel_order(key)
                        self.ema610_limit_manager._save()

        # M15 RSI divergence blocks EMA entries (standard_* and ema610_*)
        if entry_type.startswith("standard_") or entry_type.startswith("ema610_"):
            if self.signal_detector.check_m15_ema_block(symbol, signal.signal_type):
                logger.info(
                    f"[RSI-DIV-BLOCK] {symbol} {signal.signal_type} {entry_type} "
                    f"blocked by M15 RSI divergence"
                )
                return

        # S/D Zone blocking: block entries when price is inside a zone
        # Supply zone blocks BUY (TF ≤ zone TF), demand zone blocks SELL
        if self._is_sd_zone_blocked(symbol, signal.signal_type, entry_type,
                                    signal_price=signal.entry_price):
            return

        # Get account balance
        if self.mode == "paper":
            # Paper trading: Use tracked paper balance
            account_balance = self.position_manager.paper_balance
        else:
            # Live trading: Get real balance
            balances = self.binance.get_account_balance()
            account_balance = balances.get('USDT', 0)

        min_balance = RISK_MANAGEMENT.get('min_balance_to_trade', 200)
        if account_balance < min_balance:
            logger.warning(f"Insufficient balance: ${account_balance:.2f} (min ${min_balance})")
            return

        # Open position
        position = self.position_manager.open_position(
            symbol=symbol,
            side=signal.signal_type,
            entry_price=signal.entry_price,
            account_balance=account_balance,
            tp1=signal.take_profit_1,
            tp2=signal.take_profit_2,
            entry_type=signal.entry_type,
            skip_exchange_order=skip_exchange_order
        )

        if position:
            # Set entry time for CE grace period (time-based, no candle timestamp race conditions)
            from datetime import datetime
            position.entry_time = datetime.now().isoformat()
            logger.info(
                f"[OK] Position opened: {position.side} {position.size:.6f} {symbol} "
                f"@ ${position.entry_price:.2f} ({position.leverage}x) "
                f"[CE grace: {position.entry_time}]"
            )

            # Log signal to database
            if self.db:
                try:
                    self.db.store_trading_signal(
                        symbol=symbol,
                        signal=signal.signal_type,
                        rsi=signal.h1_rsi,
                        ema=signal.m15_ema34,
                        price=signal.entry_price,
                        volume=0.0,
                        metadata={
                            'h4_trend': signal.h4_trend,
                            'wick_ratio': signal.wick_ratio,
                            'ema89': signal.m15_ema89,
                            'tp1': signal.take_profit_1,
                            'tp2': signal.take_profit_2,
                            'leverage': position.leverage,
                            'margin': position.margin,
                        }
                    )
                except Exception as e:
                    logger.error(f"[DB] Error logging signal: {e}")

            # Send position opened alert to Telegram
            if self.telegram:
                self.telegram.send_position_opened(position)

            # Subscribe to real-time price updates
            self.binance.subscribe_price(symbol, self._on_price_update)

        else:
            logger.error(f"Failed to open position for {symbol}")

    def _on_price_update(self, symbol: str, price: float):
        """
        Callback for real-time price updates

        Args:
            symbol: Trading pair
            price: Current price
        """
        # Update position prices
        self.position_manager.update_position_price(symbol, price)

    async def _scan_sd_zones(self):
        """Scan all active symbols for Supply/Demand zones (standalone indicator)."""
        config = SD_ZONES_CONFIG
        if not config.get('enabled', True):
            return

        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        timeframes = config.get('timeframes', ['1h', '4h'])
        candle_limits = config.get('candle_limits', {})
        scan_start = time.time()
        symbols_with_zones = 0

        for symbol in self.symbols:
            symbol_has_zones = False
            for tf in timeframes:
                try:
                    limit = candle_limits.get(tf, 500)
                    df = self.sd_candle_cache.fetch(symbol, tf, limit)
                    if df is None or len(df) < config.get('atr_period', 200) + 10:
                        continue

                    zones = SupplyDemandZones.detect(
                        df,
                        timeframe=tf,
                        atr_period=config.get('atr_period', 200),
                        atr_multiplier=config.get('atr_multiplier', 2),
                        vol_lookback=config.get('vol_lookback', 1000),
                        max_zones=config.get('max_zones', 5),
                        cooldown_bars=config.get('cooldown_bars', 15),
                    )
                    self.sd_zone_cache.update(symbol, tf, zones)

                    # Log per-symbol per-tf detail
                    if zones:
                        symbol_has_zones = True
                        supply = [z for z in zones if z.zone_type == "supply"]
                        demand = [z for z in zones if z.zone_type == "demand"]
                        current_price = df['close'].iloc[-1]
                        parts = [f"[SD] {symbol} {tf.upper()}: price={current_price:.4g}"]
                        for z in supply:
                            tag = " TESTED" if z.tested else ""
                            parts.append(f"  Supply: {z.bottom:.4g}-{z.top:.4g} delta={z.delta:.0f}{tag}")
                        for z in demand:
                            tag = " TESTED" if z.tested else ""
                            parts.append(f"  Demand: {z.bottom:.4g}-{z.top:.4g} delta={z.delta:.0f}{tag}")
                        logger.info("\n".join(parts))

                except Exception as e:
                    logger.warning(f"[SD] {symbol} {tf} error: {e}")

            if symbol_has_zones:
                symbols_with_zones += 1

        # Save to file for dashboard
        try:
            self.sd_zone_cache.save_to_file(data_dir / "sd_zones.json")
        except Exception as e:
            logger.error(f"[SD] Failed to save zones file: {e}")

        # Log summary
        total_zones = sum(
            len(zones)
            for tf_zones in self.sd_zone_cache.get_all().values()
            for zones in tf_zones.values()
        )
        elapsed = time.time() - scan_start
        logger.info(
            f"[SD] Scan complete: {total_zones} zones across "
            f"{symbols_with_zones}/{len(self.symbols)} symbols ({elapsed:.1f}s)"
        )

    async def _scan_sd_entries(self):
        """
        Scan SD zones for entry signals: wick rejection + volume confirmation.

        For each symbol, for each TF (m15, h1, h4):
        1. Get active SD zones from zone cache
        2. Get last 2 closed candles from candle cache
        3. Check wick rejection (wick >= 50% of range, touching zone)
        4. Check volume confirmation (at least 1 of 2 has vol > 1.2x VMA(20))
        5. If triggered → market entry at 2nd rejection candle close

        Block mechanism:
        - H4 triggered → block opposite SD signals on H4 + H1 + M15
        - H1 triggered → block opposite on H1 + M15
        - M15 triggered → block M15 only
        - Duration: until price closes outside the zone
        """
        if self._startup_scans_skipped < self._startup_cooldown_scans:
            return

        wick_ratio_min = SD_ENTRY_CONFIG.get('wick_ratio_min', 0.50)
        vol_mult = SD_ENTRY_CONFIG.get('volume_multiplier', 1.2)
        vol_ma_period = SD_ENTRY_CONFIG.get('volume_ma_period', 20)

        # TF mapping: SD zone timeframe → entry_type suffix
        tf_map = {'15m': 'm15', '1h': 'h1', '4h': 'h4'}
        # Block cascade: source TF → which TFs get blocked
        block_cascade = {
            'h4': ['h4', 'h1', 'm15'],
            'h1': ['h1', 'm15'],
            'm15': ['m15'],
        }

        # --- Update existing blocks: clear if price left zone ---
        self._update_sd_entry_blocks()

        total_entries = 0

        for symbol in self.symbols:
            if self._is_symbol_paused(symbol):
                continue

            for sd_tf, entry_tf in tf_map.items():
                if not SD_ENTRY_CONFIG.get(entry_tf, {}).get('enabled', True):
                    continue

                zones = self.sd_zone_cache.get(symbol, sd_tf)
                if not zones:
                    continue

                # Get candle data from cache (already fresh from _scan_sd_zones)
                cache_key = f"{symbol}_{sd_tf}"
                df = self.sd_candle_cache._memory.get(cache_key)
                if df is None or len(df) < vol_ma_period + 3:
                    continue

                # Volume MA for confirmation
                vol_ma = df['volume'].rolling(vol_ma_period, min_periods=1).mean()

                # Last 2 CLOSED candles (index -3 and -2, since -1 is forming)
                if len(df) < 4:
                    continue
                candle_1 = df.iloc[-3]  # older
                candle_2 = df.iloc[-2]  # newer (entry at this candle close)
                vol_ma_1 = vol_ma.iloc[-3]
                vol_ma_2 = vol_ma.iloc[-2]
                candle_2_ts = str(df.index[-2])

                # Pre-collect active positions for this symbol (avoid repeated lookups)
                sym_active = [
                    p for p in self.position_manager.positions.values()
                    if p.symbol == symbol and p.status in ("OPEN", "PARTIAL_CLOSE")
                ]

                for zone in zones:
                    zone_key = f"{zone.zone_type}_{zone.top:.6f}_{zone.bottom:.6f}"
                    dedup_key = f"{symbol}_{entry_tf}_{zone_key}"

                    # Determine side based on zone type
                    if zone.zone_type == "demand":
                        side = "BUY"
                        entry_type = f"sd_demand_{entry_tf}"
                    else:
                        side = "SELL"
                        entry_type = f"sd_supply_{entry_tf}"

                    # --- Hard limit: max 1 SD position per entry_type per symbol ---
                    existing_same_type = any(
                        p for p in sym_active
                        if p.entry_type == entry_type and p.side == side
                    )
                    if existing_same_type:
                        continue

                    # --- Zone-consumed dedup: skip if this zone already triggered an active position ---
                    if dedup_key in self._sd_zone_consumed:
                        has_active = any(
                            p for p in sym_active if p.entry_type == entry_type
                        )
                        if has_active:
                            continue  # position still open from this zone
                        else:
                            self._sd_zone_consumed.discard(dedup_key)
                            self._sd_skip_logged.discard(dedup_key)
                            logger.info(
                                f"[SD] {symbol} {entry_type}: zone {zone.zone_type} "
                                f"{zone.bottom:.4g}-{zone.top:.4g} available again (position closed)"
                            )

                    # --- Opposite-side handling: close lower/equal TF, keep higher TF ---
                    # ONLY when price is actually IN the zone (or within 30% zone height tolerance)
                    # TF rank: m5=1, m15=2, h1=3, h4=4, 1d=5
                    tf_rank = {'m5': 1, 'm15': 2, 'h1': 3, 'h4': 4, '1d': 5}
                    current_rank = tf_rank.get(entry_tf, 2)

                    current_price = float(candle_2['close'])
                    zone_tol = (zone.top - zone.bottom) * 0.3
                    price_in_zone = (zone.bottom - zone_tol) <= current_price <= (zone.top + zone_tol)

                    opposite = "SELL" if side == "BUY" else "BUY"
                    opp_pos = [p for p in sym_active if p.side == opposite] if price_in_zone else []
                    if opp_pos:
                        # Split: positions to close vs positions to keep
                        to_close = []
                        to_keep = []
                        for p in opp_pos:
                            # Extract TF from entry_type (e.g. "standard_m15" → "m15")
                            p_tf = p.entry_type.rsplit('_', 1)[-1] if '_' in p.entry_type else 'm15'
                            p_rank = tf_rank.get(p_tf, 2)
                            if p_rank <= current_rank:
                                to_close.append(p)
                            else:
                                to_keep.append(p)

                        # If higher TF opposite exists, skip this entry entirely
                        if to_keep:
                            skip_key = f"{dedup_key}_opp_higher"
                            if skip_key not in self._sd_skip_logged:
                                keep_str = ", ".join(f"{p.side} {p.entry_type}" for p in to_keep)
                                logger.info(
                                    f"[SD] {symbol}: {side} {entry_type} skipped — "
                                    f"higher TF opposite position ({keep_str})"
                                )
                                self._sd_skip_logged.add(skip_key)
                            continue

                        # Close lower/equal TF opposite positions (SD zone override)
                        if to_close:
                            for p in to_close:
                                logger.info(
                                    f"[SD-OVERRIDE] Closing {p.entry_type} {p.side} {symbol} "
                                    f"(SD {zone.zone_type} zone override)"
                                )
                                if not hasattr(self, '_recently_closed'):
                                    self._recently_closed = {}
                                self._recently_closed[symbol] = time.time()
                                try:
                                    self.position_manager.close_position(
                                        p.position_id, reason="SD_ZONE_OVERRIDE"
                                    )
                                except Exception as e:
                                    logger.error(f"[SD-OVERRIDE] Failed to close {p.position_id}: {e}")
                            # Refresh sym_active after closing
                            sym_active = [
                                p for p in self.position_manager.positions.values()
                                if p.symbol == symbol and p.status in ("OPEN", "PARTIAL_CLOSE")
                            ]
                    else:
                        # Opposite cleared — allow re-logging next time
                        self._sd_skip_logged.discard(f"{dedup_key}_opp_higher")

                    # Check block mechanism
                    if self._is_sd_entry_blocked(symbol, side, entry_tf):
                        continue

                    # Check if already has active position for this entry type
                    if not self.position_manager.can_open_position(
                        symbol, entry_type, side=side
                    ):
                        continue

                    # --- Check wick rejection on both candles ---
                    rej_1 = self._check_wick_rejection(candle_1, zone, wick_ratio_min)
                    rej_2 = self._check_wick_rejection(candle_2, zone, wick_ratio_min)
                    if not (rej_1 and rej_2):
                        continue

                    # --- Volume confirmation: at least 1 of 2 candles ---
                    vol_ok = (
                        candle_1['volume'] > vol_mult * vol_ma_1
                        or candle_2['volume'] > vol_mult * vol_ma_2
                    )
                    if not vol_ok:
                        continue

                    # --- ENTRY TRIGGERED ---
                    entry_price = float(candle_2['close'])
                    leverage = LEVERAGE.get(symbol, LEVERAGE.get('default', 5))

                    # Calculate TP prices (ROI-based)
                    tf_config = SD_ENTRY_CONFIG.get(entry_tf, {})
                    tp1_roi = tf_config.get('tp1_roi', 20)
                    tp2_roi = tf_config.get('tp2_roi', 40)
                    tp1_move = entry_price * (tp1_roi / 100) / leverage
                    tp2_move = entry_price * (tp2_roi / 100) / leverage

                    if side == "BUY":
                        tp1_price = entry_price + tp1_move
                        tp2_price = entry_price + tp2_move
                    else:
                        tp1_price = entry_price - tp1_move
                        tp2_price = entry_price - tp2_move

                    signal = type('Signal', (), {
                        'symbol': symbol,
                        'signal_type': side,
                        'entry_price': entry_price,
                        'entry_type': entry_type,
                        'take_profit_1': tp1_price,
                        'take_profit_2': tp2_price,
                        'h4_trend': 'N/A',
                        'h1_rsi': 0,
                        'm15_ema34': 0,
                        'm15_ema89': 0,
                        'wick_ratio': rej_2,
                    })()

                    logger.info(
                        f"[SD-ENTRY] {symbol} {entry_type} {side}: "
                        f"Zone {zone.zone_type} {zone.bottom:.4g}-{zone.top:.4g} | "
                        f"Wick1={rej_1:.0%} Wick2={rej_2:.0%} | "
                        f"Entry={entry_price:.4g} TP1={tp1_price:.4g} TP2={tp2_price:.4g}"
                    )

                    await self._process_signal(signal)
                    total_entries += 1

                    # Mark zone as consumed (won't re-trigger until position closes)
                    self._sd_zone_consumed.add(dedup_key)
                    self._sd_last_entry_candle[dedup_key] = candle_2_ts

                    # Set block: cascade to lower TFs
                    blocked_side = "SELL" if side == "BUY" else "BUY"
                    blocked_tfs = block_cascade.get(entry_tf, [entry_tf])
                    self._sd_entry_blocks.append({
                        'symbol': symbol,
                        'blocked_side': blocked_side,
                        'blocked_tfs': blocked_tfs,
                        'zone_top': zone.top,
                        'zone_bottom': zone.bottom,
                        'source_tf': entry_tf,
                    })
                    logger.info(
                        f"[SD-BLOCK] {symbol}: {side} {entry_tf.upper()} → "
                        f"blocking {blocked_side} on {[t.upper() for t in blocked_tfs]} "
                        f"until price leaves zone {zone.bottom:.4g}-{zone.top:.4g}"
                    )

        if total_entries:
            logger.info(f"[SD-ENTRY] Scan complete: {total_entries} entries triggered")

    @staticmethod
    def _check_wick_rejection(candle, zone, wick_ratio_min: float) -> float:
        """Check if a candle has wick rejection touching the SD zone.

        Returns wick ratio (0.0 if no rejection, else the ratio).
        """
        o, h, l, c = float(candle['open']), float(candle['high']), float(candle['low']), float(candle['close'])
        candle_range = h - l
        if candle_range <= 0:
            return 0.0

        if zone.zone_type == "demand":
            # Demand: lower wick must touch zone, wick >= 50%
            lower_wick = min(o, c) - l
            wick_ratio = lower_wick / candle_range
            touches_zone = l <= zone.top  # wick reaches into demand zone
            if touches_zone and wick_ratio >= wick_ratio_min:
                return wick_ratio
        else:
            # Supply: upper wick must touch zone, wick >= 50%
            upper_wick = h - max(o, c)
            wick_ratio = upper_wick / candle_range
            touches_zone = h >= zone.bottom  # wick reaches into supply zone
            if touches_zone and wick_ratio >= wick_ratio_min:
                return wick_ratio

        return 0.0

    def _is_sd_entry_blocked(self, symbol: str, side: str, entry_tf: str) -> bool:
        """Check if an SD entry is blocked by the block mechanism."""
        for block in self._sd_entry_blocks:
            if (block['symbol'] == symbol
                    and block['blocked_side'] == side
                    and entry_tf in block['blocked_tfs']):
                logger.debug(
                    f"[SD-BLOCK] {symbol} {side} {entry_tf}: blocked by "
                    f"{block['source_tf']} entry (zone {block['zone_bottom']:.4g}-{block['zone_top']:.4g})"
                )
                return True
        return False

    def _update_sd_entry_blocks(self):
        """Clear SD entry blocks where price has left the zone."""
        if not self._sd_entry_blocks:
            return

        remaining = []
        for block in self._sd_entry_blocks:
            symbol = block['symbol']
            # Get current price
            try:
                price = self.binance.get_current_price(symbol)
            except Exception:
                remaining.append(block)
                continue

            # Block active while price is inside zone
            if block['zone_bottom'] <= price <= block['zone_top']:
                remaining.append(block)
            else:
                logger.info(
                    f"[SD-BLOCK] {symbol}: Block cleared — price {price:.4g} "
                    f"left zone {block['zone_bottom']:.4g}-{block['zone_top']:.4g}"
                )

        self._sd_entry_blocks = remaining

    async def _scan_ema610(self, force_h1_update: bool = False, force_h4_update: bool = False):
        """
        Scan all symbols for EMA610 entries (H1 + H4).

        Two modes:
        - Limit orders (use_limit_orders=True): Pre-place limit orders at EMA610,
          update on candle close. Exchange fills when price touches.
        - Legacy market (use_limit_orders=False): Scan realtime price every 60s,
          enter with market order if within tolerance zone.
        """
        if self._startup_scans_skipped < self._startup_cooldown_scans:
            return

        use_limit = EMA610_ENTRY.get('use_limit_orders', True)
        tolerance = EMA610_ENTRY.get('tolerance', 0.002)
        ema_period = EMA610_ENTRY.get('period', 610)
        ema_tf = EMA610_ENTRY.get('timeframe', '1h')
        # EMA610 needs MANY candles to converge: seed influence = (1 - 2/611)^n
        # For <1% seed influence: n > 1407 candles AFTER period start
        # Fetch 5x period (~3050) to ensure convergence for most coins
        candles_needed = ema_period * 5

        # Collect qualified symbols for limit order mode
        qualified_symbols = []

        for symbol in self.symbols:
            # Check if symbol is temporarily paused from dashboard
            if self._is_symbol_paused(symbol):
                continue

            # Skip symbols recently closed (prevent EMA610 re-entry race)
            if hasattr(self, '_recently_closed') and symbol in self._recently_closed:
                elapsed = time.time() - self._recently_closed[symbol]
                if elapsed < 300:  # 5 minute cooldown
                    logger.info(f"[EMA610] {symbol}: SKIP — recently closed {elapsed:.0f}s ago (cooldown 300s)")
                    continue

            try:
                # Get H4 trend: EMA34+89 vs EMA610
                # swap_only=True: use ONLY perpetual data, no spot backfill
                # Mixing SWAP+SPOT prices creates wrong EMA values
                df_h4_long = self.binance.fetch_ohlcv(symbol, '4h', candles_needed, swap_only=True)
                n_closed = len(df_h4_long) - 1
                min_required = int(ema_period * 1.5)  # 915 min candles (swap_only ensures data quality)
                if n_closed < min_required:
                    logger.info(
                        f"[EMA610] {symbol}: SKIP — Insufficient H4 history "
                        f"({n_closed}/{min_required} candles, first={df_h4_long.index[0].strftime('%Y-%m-%d')})"
                    )
                    continue

                # Calculate EMA34, EMA89, EMA610 on H4 (use only closed candles)
                ema34_h4 = TechnicalIndicators.calculate_ema(df_h4_long['close'].iloc[:-1], 34)
                ema89_h4 = TechnicalIndicators.calculate_ema(df_h4_long['close'].iloc[:-1], 89)
                ema610_h4 = TechnicalIndicators.calculate_ema(df_h4_long['close'].iloc[:-1], ema_period)

                ema34_val = float(ema34_h4.iloc[-1])
                ema89_val = float(ema89_h4.iloc[-1])
                ema610_h4_val = float(ema610_h4.iloc[-1])

                # Determine H4 trend: EMA34+89 vs EMA610
                if ema34_val > ema610_h4_val and ema89_val > ema610_h4_val:
                    h4_trend = 'UPTREND'  # BUY
                elif ema34_val < ema610_h4_val and ema89_val < ema610_h4_val:
                    h4_trend = 'DOWNTREND'  # SELL
                else:
                    logger.info(f"[EMA610] {symbol}: SKIP — H4 mixed (EMA34={ema34_val:.4f} EMA89={ema89_val:.4f} EMA610={ema610_h4_val:.4f})")
                    continue

                # Price-close override: H4 candle closed beyond EMA610 → flip trend
                h4_close_price = float(df_h4_long['close'].iloc[-2])
                if h4_trend == 'DOWNTREND' and h4_close_price > ema610_h4_val:
                    h4_trend = 'UPTREND'
                    logger.info(
                        f"[EMA610] {symbol}: H4 trend override DOWNTREND→UPTREND "
                        f"(close={h4_close_price:.4f} > EMA610={ema610_h4_val:.4f})"
                    )
                elif h4_trend == 'UPTREND' and h4_close_price < ema610_h4_val:
                    h4_trend = 'DOWNTREND'
                    logger.info(
                        f"[EMA610] {symbol}: H4 trend override UPTREND→DOWNTREND "
                        f"(close={h4_close_price:.4f} < EMA610={ema610_h4_val:.4f})"
                    )

                logger.info(f"[EMA610] {symbol}: H4 {h4_trend} (EMA34={ema34_val:.4f} EMA89={ema89_val:.4f} EMA610={ema610_h4_val:.4f})")

                # ADX H1 filter: block EMA610 entries when market is sideways
                if not self.signal_detector.check_adx_filter(symbol):
                    logger.info(f"[EMA610] {symbol}: SKIP — ADX filter failed")
                    continue

                leverage = LEVERAGE.get(symbol, LEVERAGE.get('default', 5))
                margin = RISK_MANAGEMENT.get('fixed_margin', 50)

                # ── EMA610 H4 ──
                if ENTRY.get('enable_ema610_h4', True):
                    existing_h4 = [p for p in self.position_manager.get_open_positions()
                                  if p.symbol == symbol and p.entry_type == "ema610_h4"]
                    if existing_h4:
                        logger.info(f"[EMA610] {symbol}: H4 SKIP — already has open ema610_h4 position")
                    if not existing_h4:
                        side = "BUY" if h4_trend == "UPTREND" else "SELL"
                        h4_exit = EMA610_EXIT.get('h4', {})
                        tp1_roi = h4_exit.get('tp1_roi', 60)
                        tp2_roi = h4_exit.get('tp2_roi', 120)
                        lev = LEVERAGE.get(symbol, LEVERAGE.get('default', 5))
                        tp1_move = ema610_h4_val * (tp1_roi / 100) / lev
                        tp2_move = ema610_h4_val * (tp2_roi / 100) / lev
                        if side == "BUY":
                            tp1_price = ema610_h4_val + tp1_move
                            tp2_price = ema610_h4_val + tp2_move
                        else:
                            tp1_price = ema610_h4_val - tp1_move
                            tp2_price = ema610_h4_val - tp2_move

                        h4_margin = margin * RISK_MANAGEMENT.get('ema610_h4_margin_multiplier', 1)

                        if use_limit:
                            # Distance filter: only place limit if price within max_distance of EMA610
                            max_dist = EMA610_ENTRY.get('max_distance_pct', 0.04)
                            current_price_h4 = self.binance.get_current_price(symbol)
                            dist_h4 = abs(current_price_h4 - ema610_h4_val) / ema610_h4_val

                            # Direction check: limit order must wait for price to reach EMA610
                            # BUY limit (below market) = price must be ABOVE EMA610
                            # SELL limit (above market) = price must be BELOW EMA610
                            wrong_side = (side == "BUY" and current_price_h4 < ema610_h4_val) or \
                                         (side == "SELL" and current_price_h4 > ema610_h4_val)

                            if wrong_side:
                                logger.info(
                                    f"[EMA610] {symbol}: H4 SKIP — price on wrong side of EMA610 "
                                    f"({side} but price={current_price_h4:.6g} {'>' if current_price_h4 > ema610_h4_val else '<'} ema610={ema610_h4_val:.6g})"
                                )
                            elif dist_h4 > max_dist:
                                logger.info(
                                    f"[EMA610] {symbol}: H4 SKIP — price too far from EMA610 "
                                    f"({dist_h4:.2%} > {max_dist:.0%}, price={current_price_h4:.6g} ema610={ema610_h4_val:.6g})"
                                )
                            else:
                                qualified_symbols.append({
                                    'symbol': symbol, 'side': side,
                                    'ema610_val': ema610_h4_val, 'timeframe': 'h4',
                                    'leverage': lev, 'margin': h4_margin,
                                    'h4_trend': h4_trend,
                                    'tp1_price': tp1_price, 'tp2_price': tp2_price,
                                })
                        else:
                            # Legacy: check realtime price
                            realtime_price_h4 = self.binance.get_current_price(symbol)
                            if h4_trend == "UPTREND":
                                trigger_h4 = ema610_h4_val * (1 - tolerance)
                                touches = realtime_price_h4 <= ema610_h4_val * (1 + tolerance) and realtime_price_h4 >= trigger_h4
                            else:
                                trigger_h4 = ema610_h4_val * (1 + tolerance)
                                touches = realtime_price_h4 >= ema610_h4_val * (1 - tolerance) and realtime_price_h4 <= trigger_h4
                            if touches:
                                signal = type('Signal', (), {
                                    'symbol': symbol, 'signal_type': side,
                                    'entry_price': ema610_h4_val, 'entry_type': 'ema610_h4',
                                    'take_profit_1': tp1_price, 'take_profit_2': tp2_price,
                                    'h4_trend': h4_trend, 'h1_rsi': 0,
                                    'm15_ema34': 0, 'm15_ema89': 0, 'wick_ratio': 0,
                                })()
                                logger.info(f"[EMA610 H4] {symbol}: {side} entry@EMA610={ema610_h4_val:.4f} (market={realtime_price_h4:.4f})")
                                await self._process_signal(signal)

                # ── EMA610 H1 (independent signal — can coexist with H4) ──
                if not ENTRY.get('enable_ema610_h1', True):
                    continue
                # Pyramiding: OPEN = hasn't hit TP1 → block
                h1_still_open = [p for p in self.position_manager.get_open_positions()
                                 if p.symbol == symbol and p.entry_type == "ema610_h1"
                                 and p.status == "OPEN"]
                if not h1_still_open:
                    df_h1 = self.binance.fetch_ohlcv(symbol, ema_tf, candles_needed, swap_only=True)
                    n_h1 = len(df_h1) - 1
                    if n_h1 < int(ema_period * 1.5):
                        logger.info(f"[EMA610] {symbol}: SKIP H1 — Insufficient history ({n_h1}/{int(ema_period * 1.5)} candles)")
                        continue
                    if len(df_h1) >= ema_period:
                        h1_closed = df_h1['close'].iloc[:-1]
                        ema610 = TechnicalIndicators.calculate_ema(h1_closed, ema_period)
                        ema610_val = float(ema610.iloc[-1])

                        # H1 EMA34/89 position filter
                        ema34_h1 = float(TechnicalIndicators.calculate_ema(h1_closed, 34).iloc[-1])
                        ema89_h1 = float(TechnicalIndicators.calculate_ema(h1_closed, 89).iloc[-1])

                        if h4_trend == "UPTREND":
                            h1_ema_valid = ema34_h1 > ema610_val and ema89_h1 > ema610_val
                        else:
                            h1_ema_valid = ema34_h1 < ema610_val and ema89_h1 < ema610_val

                        if not h1_ema_valid:
                            logger.info(f"[EMA610] {symbol}: H1 SKIP — EMA34/89 not aligned "
                                        f"(EMA34={ema34_h1:.4f} EMA89={ema89_h1:.4f} EMA610={ema610_val:.4f} trend={h4_trend})")
                            continue

                        side = "BUY" if h4_trend == "UPTREND" else "SELL"
                        h1_exit = EMA610_EXIT.get('h1', {})
                        tp1_roi_h1 = h1_exit.get('tp1_roi', 40)
                        tp2_roi_h1 = h1_exit.get('tp2_roi', 80)
                        lev_h1 = LEVERAGE.get(symbol, LEVERAGE.get('default', 5))
                        tp1_move_h1 = ema610_val * (tp1_roi_h1 / 100) / lev_h1
                        tp2_move_h1 = ema610_val * (tp2_roi_h1 / 100) / lev_h1
                        if side == "BUY":
                            tp1_price_h1 = ema610_val + tp1_move_h1
                            tp2_price_h1 = ema610_val + tp2_move_h1
                        else:
                            tp1_price_h1 = ema610_val - tp1_move_h1
                            tp2_price_h1 = ema610_val - tp2_move_h1

                        h1_margin = margin * RISK_MANAGEMENT.get('ema610_margin_multiplier', 1)

                        logger.info(f"[EMA610] {symbol}: H1 ✓ EMA aligned (EMA34={ema34_h1:.4f} EMA89={ema89_h1:.4f} EMA610={ema610_val:.4f} {side})")

                        if use_limit:
                            # Distance filter: only place limit if price within max_distance of EMA610
                            max_dist_h1 = EMA610_ENTRY.get('max_distance_pct', 0.04)
                            current_price_h1 = self.binance.get_current_price(symbol)
                            dist_h1 = abs(current_price_h1 - ema610_val) / ema610_val

                            # Direction check: limit order must wait for price to reach EMA610
                            # BUY limit (below market) = price must be ABOVE EMA610
                            # SELL limit (above market) = price must be BELOW EMA610
                            wrong_side_h1 = (side == "BUY" and current_price_h1 < ema610_val) or \
                                            (side == "SELL" and current_price_h1 > ema610_val)

                            if wrong_side_h1:
                                logger.info(
                                    f"[EMA610] {symbol}: H1 SKIP — price on wrong side of EMA610 "
                                    f"({side} but price={current_price_h1:.6g} {'>' if current_price_h1 > ema610_val else '<'} ema610={ema610_val:.6g})"
                                )
                            elif dist_h1 > max_dist_h1:
                                logger.info(
                                    f"[EMA610] {symbol}: H1 SKIP — price too far from EMA610 "
                                    f"({dist_h1:.2%} > {max_dist_h1:.0%}, price={current_price_h1:.6g} ema610={ema610_val:.6g})"
                                )
                            else:
                                # Check no existing position (for limit, check before placing)
                                existing = [p for p in self.position_manager.get_open_positions()
                                           if p.symbol == symbol and p.entry_type == "ema610_h1"]
                                if not existing:
                                    qualified_symbols.append({
                                        'symbol': symbol, 'side': side,
                                        'ema610_val': ema610_val, 'timeframe': 'h1',
                                        'leverage': lev_h1, 'margin': h1_margin,
                                        'h4_trend': h4_trend,
                                        'tp1_price': tp1_price_h1, 'tp2_price': tp2_price_h1,
                                    })
                        else:
                            # Legacy: check realtime price
                            realtime_price_h1 = self.binance.get_current_price(symbol)
                            if h4_trend == "UPTREND":
                                trigger_h1 = ema610_val * (1 - tolerance)
                                touches = realtime_price_h1 <= ema610_val * (1 + tolerance) and realtime_price_h1 >= trigger_h1
                            else:
                                trigger_h1 = ema610_val * (1 + tolerance)
                                touches = realtime_price_h1 >= ema610_val * (1 - tolerance) and realtime_price_h1 <= trigger_h1
                            if touches:
                                existing = [p for p in self.position_manager.get_open_positions()
                                           if p.symbol == symbol and p.entry_type == "ema610_h1"]
                                if not existing:
                                    signal = type('Signal', (), {
                                        'symbol': symbol, 'signal_type': side,
                                        'entry_price': ema610_val, 'entry_type': 'ema610_h1',
                                        'take_profit_1': tp1_price_h1, 'take_profit_2': tp2_price_h1,
                                        'h4_trend': h4_trend, 'h1_rsi': 0,
                                        'm15_ema34': 0, 'm15_ema89': 0, 'wick_ratio': 0,
                                    })()
                                    logger.info(f"[EMA610 H1] {symbol}: {side} entry@EMA610={ema610_val:.4f} (market={realtime_price_h1:.4f})")
                                    await self._process_signal(signal)
                    else:
                        logger.debug(f"[EMA610 H1] {symbol}: Insufficient data ({len(df_h1)} candles, need {ema_period}) - skipping")

            except Exception as e:
                logger.error(f"[EMA610] Error scanning {symbol}: {e}")

        # Log scan summary
        total_scanned = len(self.symbols)
        if qualified_symbols:
            qs_summary = ", ".join(f"{q['symbol']}({q['timeframe']} {q['side']} @{q['ema610_val']:.4f})" for q in qualified_symbols)
            logger.info(f"[EMA610] Scan: {len(qualified_symbols)}/{total_scanned} qualified — {qs_summary}")
        else:
            logger.info(f"[EMA610] Scan: 0/{total_scanned} qualified (mode={'limit' if use_limit else 'market'})")

        # Limit order mode: update pending orders with qualified symbols
        # Filter out symbols with opposite open positions (prevent netting on one-way mode)
        if use_limit and qualified_symbols:
            open_positions = self.position_manager.get_open_positions()
            filtered = []
            for q in qualified_symbols:
                sym = q['symbol']
                side = q['side']
                # Check for ANY opposite position on this symbol (not just ema610)
                opposite = [p for p in open_positions
                           if p.symbol == sym and (
                               (side == "BUY" and p.side == "SELL") or
                               (side == "SELL" and p.side == "BUY")
                           )]
                if opposite:
                    logger.info(
                        f"[EMA610-LMT] {sym} {q['timeframe']}: SKIP — "
                        f"opposite {opposite[0].side} position exists ({opposite[0].entry_type})"
                    )
                else:
                    filtered.append(q)
            qualified_symbols = filtered

        if use_limit and qualified_symbols:
            try:
                await self.ema610_limit_manager.update(qualified_symbols, tolerance)
                if force_h1_update or force_h4_update:
                    tf_label = []
                    if force_h1_update:
                        tf_label.append("H1")
                    if force_h4_update:
                        tf_label.append("H4")
                    logger.info(f"[EMA610-LMT] {'+'.join(tf_label)} candle closed — updated {len(qualified_symbols)} limit orders")
            except Exception as e:
                logger.error(f"[EMA610-LMT] Error updating limit orders: {e}")
        elif use_limit and not qualified_symbols:
            # No qualified symbols — cancel any stale orders
            try:
                await self.ema610_limit_manager.update([], tolerance)
            except Exception as e:
                logger.error(f"[EMA610-LMT] Error cleaning up limit orders: {e}")

    async def _process_ema610_limit_fills(self):
        """Check for filled EMA610 limit orders and open positions."""
        try:
            fills = await self.ema610_limit_manager.check_fills()
            for fill in fills:
                # Create signal and process it (reuse existing flow for position creation)
                signal = type('Signal', (), {
                    'symbol': fill['symbol'],
                    'signal_type': fill['side'],
                    'entry_price': fill['entry_price'],
                    'entry_type': fill['entry_type'],
                    'take_profit_1': fill['tp1_price'],
                    'take_profit_2': fill['tp2_price'],
                    'h4_trend': fill['h4_trend'],
                    'h1_rsi': 0,
                    'm15_ema34': 0, 'm15_ema89': 0, 'wick_ratio': 0,
                })()

                # Check for opposite position BEFORE processing (order already filled on exchange!)
                open_positions = self.position_manager.get_open_positions()
                opposite = [p for p in open_positions
                           if p.symbol == fill['symbol'] and (
                               (fill['side'] == "BUY" and p.side == "SELL") or
                               (fill['side'] == "SELL" and p.side == "BUY")
                           )]
                if opposite:
                    logger.warning(
                        f"[EMA610-LMT] ⚠️ FILL CONFLICT: {fill['side']} {fill['symbol']} "
                        f"@ {fill['entry_price']:.6f} FILLED on exchange but opposite "
                        f"{opposite[0].side} {opposite[0].entry_type} exists! "
                        f"Order already executed on OKX — may cause position netting. "
                        f"Manual cleanup may be needed."
                    )
                    continue

                logger.info(
                    f"[EMA610-LMT] FILL: {fill['side']} {fill['symbol']} "
                    f"@ {fill['entry_price']:.6f} ({fill['entry_type']})"
                )
                await self._process_signal(signal, skip_exchange_order=True)
        except Exception as e:
            logger.error(f"[EMA610-LMT] Error processing fills: {e}")

    def _detect_close_reason(self, position) -> str:
        """
        Detect why a position was closed externally by checking order statuses.
        Priority: TP orders filled → Hard SL filled → EXTERNAL_CLOSE

        Skip TP1 if already handled (tp1_closed=True) to avoid double-close attribution.
        """
        order_checks = [
            (getattr(position, 'tp1_order_id', None), "TP1"),
            (getattr(position, 'tp2_order_id', None), "TP2"),
            (getattr(position, 'hard_sl_order_id', None), "HARD_SL"),
        ]
        for order_id, reason in order_checks:
            if not order_id:
                continue
            # Skip TP1 check if already partially closed (avoid double attribution)
            if reason == "TP1" and getattr(position, 'tp1_closed', False):
                continue
            try:
                result = self.binance.fetch_order(order_id, position.symbol)
                if result.get('status') == 'filled':
                    logger.info(
                        f"[SYNC] {position.symbol}: Order {order_id} was filled → {reason}"
                    )
                    return reason
            except Exception as e:
                logger.warning(f"[SYNC] {position.symbol}: Error checking order {order_id}: {e}")
        return "EXTERNAL_CLOSE"

    def _infer_close_reason_from_price(self, position) -> str | None:
        """
        Infer close reason by comparing exit price to TP/SL levels.
        Used as fallback when order ID check fails (no order, fast close, API error).

        Returns inferred reason or None if no match.
        Tolerance: 0.15% for TP levels (limit order slippage), 0.3% for SL (market order slippage).
        """
        exit_price = getattr(position, 'exit_price', 0) or getattr(position, 'current_price', 0)
        if not exit_price or exit_price <= 0:
            return None

        tp1 = getattr(position, 'take_profit_1', None)
        tp2 = getattr(position, 'take_profit_2', None)
        sl = getattr(position, 'stop_loss', None)

        def price_match(target, tolerance_pct):
            if not target or target <= 0:
                return False
            return abs(exit_price - target) / target < tolerance_pct

        # Check TP1 first (most common), then TP2, then SL
        # Skip TP1 if already partially closed
        if not getattr(position, 'tp1_closed', False) and price_match(tp1, 0.0015):
            return "TP1"
        if price_match(tp2, 0.0015):
            return "TP2"
        if price_match(sl, 0.003):
            return "HARD_SL"

        # Check Chandelier Exit — last known CE value stored on position
        ce = getattr(position, 'chandelier_sl', None)
        if ce and ce > 0:
            # CE uses wider tolerance (0.5%) since it's trailing and may shift between candles
            if price_match(ce, 0.005):
                return "CHANDELIER_EXIT"

        return None

    async def _import_orphan_position(self, exchange_pos: dict) -> None:
        """
        Import an orphan position from OKX into positions.json and place TP/SL.

        Called when OKX has an open position that doesn't exist in positions.json.
        Uses standard_h4 config as default entry type with ROI-based TP/SL.
        """
        symbol = exchange_pos.get('symbol', '').replace('/', '').replace(':USDT', '')
        side_raw = exchange_pos.get('side', '').lower()
        side = "BUY" if side_raw == 'long' else "SELL"
        entry_price = float(exchange_pos.get('entryPrice', 0))
        contracts = float(exchange_pos.get('contracts', 0))
        leverage = int(float(exchange_pos.get('leverage', 5)))
        margin = float(
            exchange_pos.get('collateral', 0)
            or exchange_pos.get('initialMargin', 0)
        )
        mark_price = float(exchange_pos.get('markPrice', 0) or entry_price)

        if not all([symbol, entry_price, contracts]):
            logger.error(f"[ORPHAN] {symbol}: Missing critical data, skipping")
            return

        # Try to detect entry type by matching entry price against EMA610 values
        entry_type = "standard_h4"
        tf = "h4"
        detected = False

        if hasattr(self, 'ema610_limit_manager'):
            try:
                ema_period = EMA610_ENTRY.get('period', 610)
                candles_needed = ema_period * 5
                tolerance = EMA610_ENTRY.get('tolerance', 0.002)

                # Check H1 EMA610
                df_h1 = self.binance.fetch_ohlcv(symbol, '1h', candles_needed, swap_only=True)
                if df_h1 is not None and len(df_h1) > ema_period:
                    ema610_h1 = TechnicalIndicators.calculate_ema(df_h1['close'].iloc[:-1], ema_period)
                    ema610_h1_val = float(ema610_h1.iloc[-1])
                    if abs(entry_price - ema610_h1_val) / ema610_h1_val <= tolerance:
                        entry_type = "ema610_h1"
                        tf = "h1"
                        detected = True
                        logger.info(
                            f"[ORPHAN] {symbol}: Detected ema610_h1 "
                            f"(entry={entry_price:.4f} ≈ EMA610_H1={ema610_h1_val:.4f})"
                        )

                # Check H4 EMA610 if H1 didn't match
                if not detected:
                    df_h4 = self.binance.fetch_ohlcv(symbol, '4h', candles_needed, swap_only=True)
                    if df_h4 is not None and len(df_h4) > ema_period:
                        ema610_h4 = TechnicalIndicators.calculate_ema(df_h4['close'].iloc[:-1], ema_period)
                        ema610_h4_val = float(ema610_h4.iloc[-1])
                        if abs(entry_price - ema610_h4_val) / ema610_h4_val <= tolerance:
                            entry_type = "ema610_h4"
                            tf = "h4"
                            detected = True
                            logger.info(
                                f"[ORPHAN] {symbol}: Detected ema610_h4 "
                                f"(entry={entry_price:.4f} ≈ EMA610_H4={ema610_h4_val:.4f})"
                            )
            except Exception as e:
                logger.warning(f"[ORPHAN] {symbol}: EMA610 detection failed: {e}")

        if not detected:
            logger.info(f"[ORPHAN] {symbol}: No EMA610 match, defaulting to standard_h4")

        # Calculate TP1/TP2 from ROI config
        # Use EMA610_EXIT for ema610 types, STANDARD_EXIT otherwise
        if entry_type.startswith("ema610_"):
            exit_cfg = EMA610_EXIT.get(tf, {})
        else:
            exit_cfg = STANDARD_EXIT.get(tf, {})
        tp1_roi = exit_cfg.get('tp1_roi', 50)
        tp2_roi = exit_cfg.get('tp2_roi', 100)
        tp1_roi_pct = tp1_roi / 100 / leverage
        tp2_roi_pct = tp2_roi / 100 / leverage

        if side == "BUY":
            tp1 = entry_price * (1 + tp1_roi_pct)
            tp2 = entry_price * (1 + tp2_roi_pct)
        else:
            tp1 = entry_price * (1 - tp1_roi_pct)
            tp2 = entry_price * (1 - tp2_roi_pct)

        # Calculate entry fee
        position_value = margin * leverage
        entry_fee = position_value * FEES['maker']

        # Create Position object
        position_id = f"{symbol}_{int(time.time() * 1000)}"
        position = Position(
            position_id=position_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            size=contracts,
            leverage=leverage,
            margin=margin,
            current_price=mark_price,
            entry_type=entry_type,
            take_profit_1=tp1,
            take_profit_2=tp2,
            entry_fee=entry_fee,
        )

        # Use exchange timestamp if available, fallback to now
        exchange_ts = exchange_pos.get('timestamp')
        if exchange_ts:
            position.entry_time = datetime.fromtimestamp(
                exchange_ts / 1000
            ).isoformat()
        else:
            position.entry_time = datetime.now().isoformat()

        # Calculate hard SL (reuse existing method)
        position.stop_loss = self.position_manager._calculate_stop_loss(position)

        # Register in position_manager
        self.position_manager.positions[position_id] = position
        if symbol not in self.position_manager.symbol_positions:
            self.position_manager.symbol_positions[symbol] = []
        self.position_manager.symbol_positions[symbol].append(position_id)

        # Place TP/SL orders on OKX
        self.position_manager._place_initial_tp_sl(position)

        # Save immediately
        self.position_manager._save_positions()

        logger.critical(
            f"[ORPHAN] {symbol}: Imported untracked {side} position — "
            f"entry=${entry_price:.4f}, margin=${margin:.2f}, "
            f"TP1=${tp1:.4f}, TP2=${tp2:.4f}, SL=${position.stop_loss:.4f}. "
            f"Possible ghost from trigger SL race condition!"
        )

        # Notify via Telegram — CRITICAL alert so user investigates
        msg = (
            f"🚨 <b>ORPHAN POSITION IMPORTED</b>\n\n"
            f"Symbol: {symbol}\n"
            f"Side: {side} | Leverage: {leverage}x\n"
            f"Entry: ${entry_price:.4f}\n"
            f"Margin: ${margin:.2f}\n"
            f"TP1: ${tp1:.4f} (+{tp1_roi}% ROI)\n"
            f"TP2: ${tp2:.4f} (+{tp2_roi}% ROI)\n"
            f"SL: ${position.stop_loss:.4f}\n"
            f"Type: {entry_type} (default)\n\n"
            f"⚠️ Check if this is a ghost position from SL race condition!"
        )
        if hasattr(self, 'telegram') and self.telegram:
            await self.telegram.send_notification(msg, parse_mode="HTML")

    async def _update_positions(self):
        """
        Update all open positions (V7.2):
        1. Fetch current price
        2. Calculate Chandelier Exit on entry timeframe
        3. Smart SL breathing (Standard only)
        4. Check exit conditions (Hard SL / Chandelier / TP1 / TP2)
        """
        open_positions = self.position_manager.get_open_positions()

        # In live mode, always proceed to sync/orphan detection even if no local positions
        # (OKX may have positions that aren't tracked in positions.json)
        if not open_positions and self.mode != "live":
            return

        if open_positions:
            logger.info(f"[POS] Updating {len(open_positions)} positions...")

        # Collect unique symbols and determine timeframes needed
        symbols_to_update = set(p.symbol for p in open_positions)
        chandelier_cache: Dict[str, dict] = {}  # symbol -> {ch_long, ch_short, vol, vol_avg, ema200, close}

        ch_period = CHANDELIER_EXIT.get('period', 22)
        ch_mult = CHANDELIER_EXIT.get('multiplier', 2.0)
        vol_avg_period = SMART_SL.get('volume_avg_period', 21)
        ema_safety = SMART_SL.get('ema_safety_period', 200)

        if CHANDELIER_EXIT.get('enabled', True):
            # Determine which TFs each symbol needs based on open positions
            symbol_tfs_needed: Dict[str, set] = {}
            for p in open_positions:
                tfs = symbol_tfs_needed.setdefault(p.symbol, set())
                tfs.add('m15')  # M15 always needed (base + fallback)
                if p.entry_type == 'standard_m5':
                    tfs.add('m5')  # M5 CE for M5 entries
                elif p.entry_type == 'standard_h1':
                    tfs.add('h1')
                elif p.entry_type == 'standard_h4':
                    tfs.update(['h1', 'h4'])
                elif p.entry_type == 'ema610_h1':
                    tfs.add('h1')  # Primary H1, fallback M15
                elif p.entry_type == 'ema610_h4':
                    tfs.update(['h1', 'h4'])  # Primary H4, fallback H1→M15
                elif p.entry_type == 'rsi_div_m15':
                    pass  # M15 CE (already included via default)
                elif p.entry_type == 'rsi_div_h1':
                    tfs.add('h1')  # Primary H1, fallback M15
                elif p.entry_type == 'rsi_div_h4':
                    tfs.update(['h1', 'h4'])  # Primary H4, fallback H1→M15
                elif p.entry_type in ('sd_demand_m15', 'sd_supply_m15'):
                    pass  # M15 CE (already included via default)
                elif p.entry_type in ('sd_demand_h1', 'sd_supply_h1'):
                    tfs.add('h1')  # Primary H1, fallback M15
                elif p.entry_type in ('sd_demand_h4', 'sd_supply_h4'):
                    tfs.update(['h1', 'h4'])  # Primary H4, fallback H1→M15

            for symbol in symbols_to_update:
                try:
                    cache_entry = {}
                    tfs_needed = symbol_tfs_needed.get(symbol, {'m15'})
                    limit = max(ch_period, vol_avg_period, ema_safety) + 30

                    # M5 chandelier (for standard_m5)
                    if 'm5' in tfs_needed:
                        df_m5 = self.binance.fetch_ohlcv(symbol, '5m', limit)
                        if len(df_m5) >= ch_period:
                            ch_long_m5, ch_short_m5 = ATRIndicator.chandelier_exit(df_m5.iloc[:-1], ch_period, ch_mult)
                            cache_entry['m5'] = {
                                'ch_long': float(ch_long_m5.iloc[-1]) if not ch_long_m5.isna().iloc[-1] else None,
                                'ch_short': float(ch_short_m5.iloc[-1]) if not ch_short_m5.isna().iloc[-1] else None,
                                'vol': float(df_m5['volume'].iloc[-1]),
                                'vol_avg': float(df_m5['volume'].tail(vol_avg_period).mean()),
                                'ema200': float(TechnicalIndicators.calculate_ema(df_m5['close'], ema_safety).iloc[-1]) if len(df_m5) >= ema_safety else None,
                                'close': float(df_m5['close'].iloc[-1]),
                                'last_closed_m5': float(df_m5['close'].iloc[-2]) if len(df_m5) >= 2 else None,
                            }

                    # M15 chandelier (always fetched)
                    df_m15 = self.binance.fetch_ohlcv(symbol, '15m', limit)
                    if len(df_m15) >= ch_period:
                        ch_long, ch_short = ATRIndicator.chandelier_exit(df_m15.iloc[:-1], ch_period, ch_mult)
                        cache_entry['m15'] = {
                            'ch_long': float(ch_long.iloc[-1]) if not ch_long.isna().iloc[-1] else None,
                            'ch_short': float(ch_short.iloc[-1]) if not ch_short.isna().iloc[-1] else None,
                            'vol': float(df_m15['volume'].iloc[-1]),
                            'vol_avg': float(df_m15['volume'].tail(vol_avg_period).mean()),
                            'ema200': float(TechnicalIndicators.calculate_ema(df_m15['close'], ema_safety).iloc[-1]) if len(df_m15) >= ema_safety else None,
                            'close': float(df_m15['close'].iloc[-1]),
                            'last_closed_m15': float(df_m15['close'].iloc[-2]) if len(df_m15) >= 2 else None,
                            'candle_ts': str(df_m15.index[-1]),
                        }

                    # H1 chandelier (for standard_h1, standard_h4 fallback)
                    if 'h1' in tfs_needed:
                        df_h1 = self.binance.fetch_ohlcv(symbol, '1h', limit)
                        if len(df_h1) >= ch_period:
                            ch_long_h1, ch_short_h1 = ATRIndicator.chandelier_exit(df_h1.iloc[:-1], ch_period, ch_mult)
                            cache_entry['h1'] = {
                                'ch_long': float(ch_long_h1.iloc[-1]) if not ch_long_h1.isna().iloc[-1] else None,
                                'ch_short': float(ch_short_h1.iloc[-1]) if not ch_short_h1.isna().iloc[-1] else None,
                                'close': float(df_h1['close'].iloc[-1]),
                                'last_closed': float(df_h1['close'].iloc[-2]) if len(df_h1) >= 2 else None,
                            }

                    # H4 chandelier (for standard_h4)
                    if 'h4' in tfs_needed:
                        df_h4 = self.binance.fetch_ohlcv(symbol, '4h', limit)
                        if len(df_h4) >= ch_period:
                            ch_long_h4, ch_short_h4 = ATRIndicator.chandelier_exit(df_h4.iloc[:-1], ch_period, ch_mult)
                            cache_entry['h4'] = {
                                'ch_long': float(ch_long_h4.iloc[-1]) if not ch_long_h4.isna().iloc[-1] else None,
                                'ch_short': float(ch_short_h4.iloc[-1]) if not ch_short_h4.isna().iloc[-1] else None,
                                'close': float(df_h4['close'].iloc[-1]),
                                'last_closed': float(df_h4['close'].iloc[-2]) if len(df_h4) >= 2 else None,
                            }

                    chandelier_cache[symbol] = cache_entry

                except Exception as e:
                    logger.error(f"[CHANDELIER] Error fetching data for {symbol}: {e}")

        # Sync exchange PNL data (live mode: fetch unrealizedPnl from OKX)
        self.position_manager.sync_exchange_pnl()

        # Auto-sync: detect positions closed externally (e.g. manually on OKX)
        # Safety: require 2 consecutive misses before closing (avoid false positives
        # from network errors or API glitches)
        if self.mode == "live":
            try:
                exchange_positions = self.binance.get_open_positions()
                exchange_symbols = set()
                for ep in exchange_positions:
                    ep_symbol = ep.get('symbol', '').replace('/', '').replace(':USDT', '')
                    if ep_symbol:
                        exchange_symbols.add(ep_symbol)

                if not hasattr(self, '_sync_miss_count'):
                    self._sync_miss_count = {}

                for position in open_positions:
                    if position.symbol not in exchange_symbols:
                        pid = position.position_id
                        self._sync_miss_count[pid] = self._sync_miss_count.get(pid, 0) + 1

                        if self._sync_miss_count[pid] < 2:
                            logger.info(
                                f"[SYNC] {position.symbol}: Not found on exchange "
                                f"(miss {self._sync_miss_count[pid]}/2, waiting for confirmation)"
                            )
                            continue

                        # Confirmed missing after 2 consecutive checks
                        close_reason = self._detect_close_reason(position)
                        logger.warning(
                            f"[SYNC] {position.symbol}: Confirmed not on exchange after "
                            f"2 checks — closing (detected: {close_reason})"
                        )
                        # Try to get actual close price from exchange history
                        synced = self.position_manager._sync_close_price_from_exchange(position)
                        if not synced:
                            position.current_price = self.binance.get_current_price(position.symbol)
                            self.position_manager._calculate_pnl(position)

                        # Price-based close reason inference:
                        # When order ID check fails (no order placed, API error, fast close),
                        # infer close reason from exit price vs TP/SL levels
                        if close_reason == "EXTERNAL_CLOSE":
                            inferred = self._infer_close_reason_from_price(position)
                            if inferred:
                                logger.info(
                                    f"[SYNC] {position.symbol}: Inferred close reason "
                                    f"from exit price: {close_reason} → {inferred}"
                                )
                                close_reason = inferred

                        # TP1 detected but not yet partially closed locally →
                        # treat as partial close (exchange TP1 order filled)
                        # Skip if already synced from OKX (remaining_size already 0)
                        if close_reason == "TP1" and not position.tp1_closed and not getattr(position, '_okx_pnl_synced', False):
                            if position.entry_type.startswith("ema610_"):
                                _tf = position.entry_type.replace("ema610_", "")
                                _pct = EMA610_EXIT.get(_tf, {}).get('tp1_percent', 50)
                            elif position.entry_type.startswith("sd_demand_") or position.entry_type.startswith("sd_supply_"):
                                _tf = position.entry_type.split("_")[-1]
                                _pct = SD_ENTRY_CONFIG.get(_tf, {}).get('tp1_percent', 70)
                            elif position.entry_type.startswith("standard_"):
                                _tf = position.entry_type.replace("standard_", "")
                                _pct = STANDARD_EXIT.get(_tf, {}).get('tp1_percent', 70)
                            else:
                                _pct = TAKE_PROFIT.get('tp1_percent', 70)
                            logger.info(
                                f"[SYNC] {position.symbol}: TP1 filled on exchange, "
                                f"applying partial close ({_pct}%) locally"
                            )
                            self.position_manager._partial_close(
                                position, percent=_pct, reason="TP1"
                            )
                        else:
                            # Track recently closed for orphan/EMA610 cooldown
                            if not hasattr(self, '_recently_closed'):
                                self._recently_closed = {}
                            self._recently_closed[position.symbol] = time.time()
                            self.position_manager.close_position(
                                position.position_id,
                                reason=close_reason,
                                skip_exchange_close=True,
                            )
                        self._sync_miss_count.pop(pid, None)
                    else:
                        # Position found on exchange — reset miss counter
                        self._sync_miss_count.pop(position.position_id, None)

                # Refresh open_positions after sync
                open_positions = self.position_manager.get_open_positions()
                if not open_positions and not exchange_positions:
                    return

                # === ORPHAN DETECTION: OKX has position but bot doesn't ===
                local_symbols = {p.symbol for p in open_positions}

                if not hasattr(self, '_orphan_cooldown'):
                    self._orphan_cooldown = {}

                # Clean up expired cooldowns
                now = time.time()
                self._orphan_cooldown = {
                    k: v for k, v in self._orphan_cooldown.items()
                    if now - v < 60
                }

                # Recently closed symbols: skip orphan import for 5 minutes
                # after manual/any close to prevent EMA610 re-entry race condition
                if not hasattr(self, '_recently_closed'):
                    self._recently_closed = {}
                self._recently_closed = {
                    k: v for k, v in self._recently_closed.items()
                    if now - v < 300  # 5 minute cooldown
                }

                # Collect symbols with pending EMA610 limit orders
                ema610_pending_symbols = set()
                if hasattr(self, 'ema610_limit_manager'):
                    for key in self.ema610_limit_manager.pending_orders:
                        # key format: "SYMBOL_h1" or "SYMBOL_h4"
                        parts = key.rsplit('_', 1)
                        if parts:
                            ema610_pending_symbols.add(parts[0])

                for ep in exchange_positions:
                    ep_symbol = ep.get('symbol', '').replace('/', '').replace(':USDT', '')
                    if not ep_symbol or ep_symbol in local_symbols:
                        continue

                    # Skip if EMA610 limit order pending — let check_fills() handle it
                    if ep_symbol in ema610_pending_symbols:
                        logger.info(
                            f"[ORPHAN] {ep_symbol}: Skipped — has pending EMA610 limit order, "
                            f"will be handled by check_fills()"
                        )
                        continue

                    # Skip if recently closed (prevent re-entry race with EMA610 limit orders)
                    if ep_symbol in self._recently_closed:
                        elapsed = now - self._recently_closed[ep_symbol]
                        logger.info(
                            f"[ORPHAN] {ep_symbol}: Skipped — recently closed {elapsed:.0f}s ago "
                            f"(cooldown 300s). Likely EMA610 re-entry race."
                        )
                        continue

                    # Cooldown: skip if imported recently (60s)
                    last_import = self._orphan_cooldown.get(ep_symbol, 0)
                    if time.time() - last_import < 60:
                        continue

                    # Safety: skip tiny positions (< $10 margin)
                    ep_margin = float(ep.get('collateral', 0) or ep.get('initialMargin', 0))
                    if ep_margin < 10:
                        logger.debug(f"[ORPHAN] {ep_symbol}: Skipped (margin ${ep_margin:.2f} < $10)")
                        continue

                    logger.warning(
                        f"[ORPHAN] {ep_symbol}: Found on OKX but NOT in "
                        f"positions.json — importing"
                    )
                    await self._import_orphan_position(ep)
                    self._orphan_cooldown[ep_symbol] = time.time()

                # Re-refresh after orphan imports
                open_positions = self.position_manager.get_open_positions()
                if not open_positions:
                    return
            except Exception as e:
                logger.error(f"[SYNC] Exchange position sync failed: {e}")

        for position in open_positions:
            try:
                # Auto-close residual positions (margin < $10 after partial closes)
                # These are leftover fragments from TP order rounding
                if position.margin < 10 and position.tp1_closed:
                    logger.info(
                        f"[RESIDUAL] {position.symbol}: Margin ${position.margin:.2f} < $10 "
                        f"after TP1 — closing residual"
                    )
                    self.position_manager.close_position(
                        position.position_id,
                        reason="RESIDUAL_CLEANUP",
                    )
                    if hasattr(self, 'telegram') and self.telegram:
                        await self.telegram.send_notification(
                            f"🧹 *Residual Closed*\n"
                            f"{position.symbol}: ${position.margin:.2f} margin\n"
                            f"(leftover from TP rounding)",
                            parse_mode="Markdown"
                        )
                    continue

                # Get current price
                current_price = self.binance.get_current_price(position.symbol)

                # Update Chandelier Exit trailing SL — per entry timeframe
                # standard_m5 → M5 CE, standard_m15 → M15 CE,
                # standard_h1 → H1 CE (fallback M15),
                # standard_h4 → H4 CE (fallback H1→M15)
                # Skip CE update during grace period to avoid inheriting stale CE values
                cache = chandelier_cache.get(position.symbol, {})
                m15_data = cache.get('m15', {})
                if position.ce_armed:
                    # Determine which TF's chandelier to use
                    # BUY uses ch_long, SELL uses ch_short — check availability accordingly
                    ce_data = m15_data  # default fallback
                    ce_tf_label = "M15"
                    ce_key = 'ch_short' if position.side == "SELL" else 'ch_long'

                    if position.entry_type == "standard_m5":
                        m5_data = cache.get('m5', {})
                        if m5_data.get(ce_key) is not None:
                            ce_data = m5_data
                            ce_tf_label = "M5"
                        # else fallback to M15 (already set)
                    elif position.entry_type == "standard_h4":
                        h4_data = cache.get('h4', {})
                        if h4_data.get(ce_key) is not None:
                            ce_data = h4_data
                            ce_tf_label = "H4"
                        else:
                            # Fallback: H1 → M15
                            h1_data = cache.get('h1', {})
                            if h1_data.get(ce_key) is not None:
                                ce_data = h1_data
                                ce_tf_label = "H1(fb)"
                    elif position.entry_type == "standard_h1":
                        h1_data = cache.get('h1', {})
                        if h1_data.get(ce_key) is not None:
                            ce_data = h1_data
                            ce_tf_label = "H1"
                        # else fallback to M15 (already set)
                    elif position.entry_type == "rsi_div_h4":
                        h4_data = cache.get('h4', {})
                        if h4_data.get(ce_key) is not None:
                            ce_data = h4_data
                            ce_tf_label = "H4"
                        else:
                            h1_data = cache.get('h1', {})
                            if h1_data.get(ce_key) is not None:
                                ce_data = h1_data
                                ce_tf_label = "H1(fb)"
                    elif position.entry_type == "rsi_div_h1":
                        h1_data = cache.get('h1', {})
                        if h1_data.get(ce_key) is not None:
                            ce_data = h1_data
                            ce_tf_label = "H1"
                    # rsi_div_m15: uses M15 CE (default, already set)
                    # SD zone entries: same CE chain as RSI div
                    elif position.entry_type in ("sd_demand_h4", "sd_supply_h4"):
                        h4_data = cache.get('h4', {})
                        if h4_data.get(ce_key) is not None:
                            ce_data = h4_data
                            ce_tf_label = "H4"
                        else:
                            h1_data = cache.get('h1', {})
                            if h1_data.get(ce_key) is not None:
                                ce_data = h1_data
                                ce_tf_label = "H1(fb)"
                    elif position.entry_type in ("sd_demand_h1", "sd_supply_h1"):
                        h1_data = cache.get('h1', {})
                        if h1_data.get(ce_key) is not None:
                            ce_data = h1_data
                            ce_tf_label = "H1"
                    # sd_demand_m15/sd_supply_m15: uses M15 CE (default)

                    ch_long_val = ce_data.get('ch_long')
                    ch_short_val = ce_data.get('ch_short')
                    ce_active = ch_short_val if position.side == "SELL" else ch_long_val
                    logger.info(
                        f"[CE-DEBUG] {position.symbol} ({position.entry_type} {position.side} CE={ce_tf_label}): "
                        f"ce_active={ce_active}, "
                        f"chandelier_sl={position.chandelier_sl}, "
                        f"trailing_sl={position.trailing_sl}"
                    )

                    if position.entry_type.startswith("standard_"):
                        # Standard entries: full CE with Smart SL (volume breathing + EMA200)
                        self.position_manager.update_chandelier_sl(
                            position,
                            chandelier_long=ch_long_val,
                            chandelier_short=ch_short_val,
                            vol_current=m15_data.get('vol'),
                            vol_avg=m15_data.get('vol_avg'),
                            ema200=m15_data.get('ema200'),
                            close_price=ce_data.get('close', m15_data.get('close')),
                        )
                    else:
                        # EMA610/RSI-div entries: CE without Smart SL, with fallback chain
                        # H1: primary=H1, fallback=[M15]
                        # H4: primary=H4, fallback=[H1, M15]
                        # M15: primary=M15, no fallback
                        fb_longs = None
                        fb_shorts = None
                        if position.entry_type in ("ema610_h1", "rsi_div_h1", "sd_demand_h1", "sd_supply_h1"):
                            fb_longs = [m15_data.get('ch_long')]
                            fb_shorts = [m15_data.get('ch_short')]
                        elif position.entry_type in ("ema610_h4", "rsi_div_h4", "sd_demand_h4", "sd_supply_h4"):
                            h1_fb = cache.get('h1', {})
                            fb_longs = [h1_fb.get('ch_long'), m15_data.get('ch_long')]
                            fb_shorts = [h1_fb.get('ch_short'), m15_data.get('ch_short')]
                        self.position_manager.update_chandelier_sl(
                            position,
                            chandelier_long=ch_long_val,
                            chandelier_short=ch_short_val,
                            close_price=m15_data.get('close'),
                            fallback_chandelier_longs=fb_longs,
                            fallback_chandelier_shorts=fb_shorts,
                        )

                    # Sync trailing_sl to exchange (move SL order to CE when tighter)
                    self.position_manager._sync_sl_order_to_exchange(position)

                # Clear wrong-side trailing_sl: if trailing_sl is on wrong side of
                # current price, reset it to None. This handles existing positions that
                # had trailing_sl set before the wrong-side check was added.
                # IMPORTANT: Use same price source as CE trigger to avoid mismatch
                if position.entry_type == "standard_m5":
                    _ws_price = cache.get('m5', {}).get('close') or m15_data.get('close')
                elif position.entry_type in ("standard_h4", "sd_demand_h4", "sd_supply_h4"):
                    _ws_price = cache.get('h4', {}).get('close') or cache.get('h1', {}).get('close') or m15_data.get('close')
                elif position.entry_type in ("standard_h1", "sd_demand_h1", "sd_supply_h1"):
                    _ws_price = cache.get('h1', {}).get('close') or m15_data.get('close')
                else:
                    _ws_price = m15_data.get('close')
                if position.trailing_sl and _ws_price:
                    wrong_side_trail = False
                    if position.side == "BUY" and position.trailing_sl > _ws_price:
                        wrong_side_trail = True
                    elif position.side == "SELL" and position.trailing_sl < _ws_price:
                        wrong_side_trail = True
                    if wrong_side_trail:
                        logger.info(
                            f"[CE] {position.symbol}: Clearing wrong-side trailing_sl "
                            f"${position.trailing_sl:.4f} (price ${_ws_price:.4f}, "
                            f"side={position.side}, {position.entry_type})"
                        )
                        position.trailing_sl = None

                # Update last candle close price for Chandelier SL check
                # Each entry type uses its own TF's close for CE trigger
                if position.entry_type == "standard_m5":
                    ce_close = cache.get('m5', {}).get('last_closed_m5') or cache.get('m15', {}).get('last_closed_m15')
                elif position.entry_type in ("standard_h4", "sd_demand_h4", "sd_supply_h4"):
                    ce_close = cache.get('h4', {}).get('last_closed') or cache.get('h1', {}).get('last_closed') or cache.get('m15', {}).get('last_closed_m15')
                elif position.entry_type in ("standard_h1", "sd_demand_h1", "sd_supply_h1"):
                    ce_close = cache.get('h1', {}).get('last_closed') or cache.get('m15', {}).get('last_closed_m15')
                else:
                    ce_close = cache.get('m15', {}).get('last_closed_m15')
                if ce_close:
                    position.last_m15_close = ce_close

                # Arm CE after enough time has passed post-entry
                # Prevents instant SL when historical CE band is already breached at entry
                if not position.ce_armed:
                    grace_seconds = {
                        'standard_m5': 300,    # 5 min
                        'standard_m15': 900,   # 15 min
                        'standard_h1': 3600,   # 1 hour
                        'standard_h4': 14400,  # 4 hours
                        'ema610_h1': 900,
                        'ema610_h4': 900,
                        'rsi_div_m15': 900,    # 15 min
                        'rsi_div_h1': 3600,    # 1 hour
                        'rsi_div_h4': 14400,   # 4 hours
                    }.get(position.entry_type, 900)
                    if position.entry_time:
                        from datetime import datetime
                        elapsed = (datetime.now() - datetime.fromisoformat(position.entry_time)).total_seconds()
                        if elapsed >= grace_seconds:
                            position.ce_armed = True
                            logger.info(f"[CE] {position.symbol}: CE armed ({elapsed:.0f}s elapsed, grace={grace_seconds}s)")
                        else:
                            logger.info(f"[CE] {position.symbol}: CE grace period ({elapsed:.0f}s / {grace_seconds}s)")
                    else:
                        # No entry time recorded (old position) — arm immediately
                        position.ce_armed = True
                        logger.info(f"[CE] {position.symbol}: CE armed (no entry_time, arming immediately)")

                # ── EMA34 dynamic TP for RSI divergence positions ──
                # Cap TP1 at EMA34 if it's closer than the original TP
                if position.entry_type.startswith("rsi_div_") and position.take_profit_1 and not position.tp1_closed:
                    try:
                        tf_key = position.entry_type.replace("rsi_div_", "")
                        tf_map = {'m15': '15m', 'h1': '1h', 'h4': '4h'}
                        ohlcv_tf = tf_map.get(tf_key, '15m')
                        df_ema = self.binance.fetch_ohlcv(position.symbol, ohlcv_tf, 40)
                        if len(df_ema) >= 34:
                            ema34_val = float(TechnicalIndicators.calculate_ema(df_ema['close'], 34).iloc[-1])
                            entry = position.entry_price
                            old_tp1 = position.take_profit_1
                            min_tp_dist = entry * 0.003  # Minimum 0.3% distance from entry

                            if position.side == "BUY":
                                # BUY: TP1 is above entry. Cap if EMA34 < old_tp1 AND EMA34 > entry + min_dist
                                if ema34_val < old_tp1 and ema34_val > entry + min_tp_dist:
                                    position.take_profit_1 = round(ema34_val, 4)
                                    logger.info(
                                        f"[RSI-DIV-TP] {position.symbol}: TP1 capped at EMA34 "
                                        f"${ema34_val:.4f} (was ${old_tp1:.4f})"
                                    )
                            elif position.side == "SELL":
                                # SELL: TP1 is below entry. Cap if EMA34 > old_tp1 AND EMA34 < entry - min_dist
                                if ema34_val > old_tp1 and ema34_val < entry - min_tp_dist:
                                    position.take_profit_1 = round(ema34_val, 4)
                                    logger.info(
                                        f"[RSI-DIV-TP] {position.symbol}: TP1 capped at EMA34 "
                                        f"${ema34_val:.4f} (was ${old_tp1:.4f})"
                                    )

                            # Update TP1 on exchange if changed
                            if position.take_profit_1 != old_tp1 and self.mode == "live" and position.tp1_order_id:
                                try:
                                    self.position_manager.client.cancel_order(
                                        position.symbol, position.tp1_order_id
                                    )
                                    position.tp1_order_id = None
                                    self.position_manager._place_initial_tp_sl(position)
                                except Exception as e:
                                    logger.warning(f"[RSI-DIV-TP] {position.symbol}: Failed to update TP1 on exchange: {e}")
                    except Exception as e:
                        logger.warning(f"[RSI-DIV-TP] {position.symbol}: EMA34 TP update error: {e}")

                # ── Recover missing/failed TP/SL orders (LIVE mode only) ──
                if self.mode == "live" and position.ce_armed:
                    pm = self.position_manager
                    # Recover if missing (None) or previously failed ("FAILED")
                    if position.hard_sl_order_id in (None, "FAILED") and position.stop_loss:
                        if position.hard_sl_order_id == "FAILED":
                            logger.info(f"[RECOVER] {position.symbol}: Retrying FAILED Hard SL order")
                            position.hard_sl_order_id = None
                        else:
                            logger.info(f"[RECOVER] {position.symbol}: Missing exchange orders, placing TP/SL")
                        pm._place_initial_tp_sl(position)
                        pm._save_positions()

                # Update price + check exits (SL/TP) — per position to avoid duplicate checks
                self.position_manager.update_single_position_price(
                    position, current_price
                )

                # Log status
                trail_str = f" | Trail: ${position.trailing_sl:.2f}" if position.trailing_sl else ""
                ch_str = f" | Ch: ${position.chandelier_sl:.2f}" if position.chandelier_sl else ""
                logger.info(
                    f"[POS] {position.symbol} ({position.entry_type}): ${current_price:.2f} | "
                    f"PNL: ${position.pnl_usd:.2f} ({position.pnl_percent:+.2f}%){trail_str}{ch_str}"
                )

            except Exception as e:
                logger.error(f"Error updating {position.symbol}: {e}")

        # Register SL cooldown for positions that were just stopped out
        # This prevents whipsaw re-entry on the same candle (per timeframe)
        for position in open_positions:
            if position.status == "CLOSED" and position.close_reason in (
                "CHANDELIER_M5", "CHANDELIER_SL", "CHANDELIER_H1", "CHANDELIER_H4",
                "HARD_SL", "EMA200_BREAK_SL"
            ):
                # Determine which TF's cooldown to register
                if position.entry_type == "standard_h1":
                    tf = "h1"
                elif position.entry_type == "standard_h4":
                    tf = "h4"
                else:
                    tf = "m15"
                last_candle_ts = self.signal_detector._last_scanned_candle[tf].get(
                    position.symbol, ""
                )
                if last_candle_ts:
                    self.signal_detector.register_sl_cooldown(
                        position.symbol, last_candle_ts, timeframe=tf
                    )

    def get_status(self) -> Dict:
        """
        Get bot status

        Returns:
            Status dict with positions and stats
        """
        open_positions = self.position_manager.get_open_positions()

        total_pnl = sum(p.pnl_usd for p in open_positions)
        total_margin = sum(
            p.margin * (p.remaining_size / p.size) if p.size > 0 else p.margin
            for p in open_positions
        )

        return {
            "is_running": self.is_running,
            "mode": self.mode,
            "symbols": self.symbols,
            "open_positions": len(open_positions),
            "total_pnl": total_pnl,
            "total_margin": total_margin,
            "pending_ema610_orders": len(self.ema610_limit_manager.pending_orders),
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry": p.entry_price,
                    "current": p.current_price,
                    "pnl": p.pnl_usd,
                    "pnl_percent": p.pnl_percent,
                    "status": p.status
                }
                for p in open_positions
            ]
        }

    def print_status(self):
        """Print bot status to console"""
        status = self.get_status()

        print("\n" + "=" * 60)
        print(f"Futures Trading Bot Status ({self.mode.upper()} mode)")
        print("=" * 60)
        print(f"Status: {'RUNNING' if status['is_running'] else 'STOPPED'}")
        print(f"Symbols: {len(status['symbols'])}")
        print(f"Open Positions: {status['open_positions']}")
        print(f"Total Margin: ${status['total_margin']:.2f}")
        print(f"Total PNL: ${status['total_pnl']:.2f}")
        print("=" * 60)

        if status['positions']:
            print("\nActive Positions:")
            for p in status['positions']:
                pnl_mark = "+" if p['pnl'] > 0 else "-"
                print(
                    f"  [{pnl_mark}] {p['symbol']} {p['side']}: "
                    f"${p['current']:.2f} | PNL: ${p['pnl']:.2f} ({p['pnl_percent']:+.2f}%)"
                )
        else:
            print("\nNo active positions")

        print("=" * 60 + "\n")


async def main():
    """Main entry point"""
    # Get symbols from environment or use defaults
    symbols_env = os.getenv('TRADING_SYMBOLS')
    if symbols_env:
        symbols = symbols_env.split(',')
    elif DYNAMIC_PAIRS.get('enabled', False):
        # Dynamic pairs: start with defaults, will refresh on first loop iteration
        symbols = DEFAULT_SYMBOLS
        vw = DYNAMIC_PAIRS.get('volume_windows', {})
        logger.info(f"[MAIN] Dynamic pairs enabled (windows: {vw}), starting with defaults")
    else:
        symbols = DEFAULT_SYMBOLS

    # Get mode from environment
    mode = os.getenv('TRADING_MODE', 'paper')

    # Create bot
    bot = FuturesTradingBot(
        symbols=symbols,
        mode=mode
    )

    # Print initial status
    bot.print_status()

    # Start bot
    await bot.start()


if __name__ == "__main__":
    asyncio.run(main())
