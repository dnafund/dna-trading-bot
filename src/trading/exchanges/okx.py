"""
OKX Futures API Client

Drop-in replacement for BinanceFuturesClient.
Handles CCXT OKX integration for isolated futures trading.
Symbol format: accepts 'BTCUSDT', converts internally to 'BTC/USDT:USDT'.
"""

import ccxt
import pandas as pd
import asyncio
import websockets
import json
import time
import functools
from typing import Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass
import logging

from src.trading.core.models import FuturesKline

logger = logging.getLogger(__name__)


def retry_on_error(max_retries: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """Decorator: retry API calls on network/exchange errors with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_error = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout) as e:
                    last_error = e
                    wait = delay * (backoff ** attempt)
                    logger.warning(f"[RETRY] {func.__name__} attempt {attempt + 1}/{max_retries} failed: {e}. Retrying in {wait:.1f}s...")
                    time.sleep(wait)
                except Exception:
                    raise  # Non-retryable errors propagate immediately
            logger.error(f"[RETRY] {func.__name__} failed after {max_retries} attempts: {last_error}")
            raise last_error
        return wrapper
    return decorator


class OKXFuturesClient:
    """
    OKX Futures API Client — drop-in replacement for BinanceFuturesClient.

    All public methods accept plain symbol format (e.g. 'BTCUSDT').
    Conversion to CCXT format ('BTC/USDT:USDT') happens internally.
    Uses isolated margin mode for all positions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        passphrase: Optional[str] = None
    ):
        """
        Initialize OKX Futures client

        Args:
            api_key: OKX API key (optional for market data)
            api_secret: OKX API secret (optional for market data)
            passphrase: OKX API passphrase (required for trading)
        """
        self.exchange = ccxt.okx({
            'apiKey': api_key,
            'secret': api_secret,
            'password': passphrase,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',             # OKX perpetual futures
                'defaultMarginMode': 'isolated',   # Isolated margin
            }
        })

        self.ws_connections: Dict[str, asyncio.Task] = {}
        self.price_callbacks: Dict[str, List[Callable]] = {}

        # Cache for margin mode setup (avoid repeated API calls)
        self._margin_mode_set: set = set()

        # Ensure one-way position mode (net_mode) — required for bot
        if api_key and api_secret and passphrase:
            self._ensure_net_mode()

    def _ensure_net_mode(self) -> None:
        """Ensure account is in one-way (net) position mode, not hedge mode."""
        try:
            config = self.exchange.privateGetAccountConfig()
            pos_mode = config['data'][0].get('posMode', '')
            if pos_mode == 'long_short_mode':
                self.exchange.privatePostAccountSetPositionMode({'posMode': 'net_mode'})
                logger.info("[OKX] Switched to net_mode (one-way position mode)")
            else:
                logger.debug("[OKX] Already in net_mode")
        except Exception as e:
            logger.warning(f"[OKX] Could not check/set position mode: {e}")

    # ── Symbol Conversion ─────────────────────────────────────────

    @staticmethod
    def _to_ccxt(symbol: str) -> str:
        """Convert plain symbol to CCXT format: BTCUSDT → BTC/USDT:USDT"""
        if '/' in symbol:
            return symbol  # Already CCXT format
        # Remove trailing 'USDT' and rebuild
        base = symbol.replace('USDT', '')
        return f"{base}/USDT:USDT"

    @staticmethod
    def _from_ccxt(symbol: str) -> str:
        """Convert CCXT format to plain: BTC/USDT:USDT → BTCUSDT"""
        if '/' not in symbol:
            return symbol  # Already plain format
        base = symbol.split('/')[0]
        return f"{base}USDT"

    @staticmethod
    def _to_okx_inst_id(symbol: str) -> str:
        """Convert plain symbol to OKX instrument ID: BTCUSDT → BTC-USDT-SWAP"""
        base = symbol.replace('USDT', '')
        return f"{base}-USDT-SWAP"

    # ── Contract Size Conversion ─────────────────────────────────

    def _base_to_contracts(self, symbol: str, base_amount: float) -> float:
        """Convert base currency amount to OKX contract count.

        OKX swap markets use contracts (not base currency).
        E.g. BTC/USDT:USDT has contractSize=0.01, so 0.1 BTC = 10 contracts.

        Args:
            symbol: Plain symbol (e.g. 'BTCUSDT')
            base_amount: Amount in base currency (e.g. 0.1 BTC)

        Returns:
            Number of contracts (floored to precision)
        """
        ccxt_symbol = self._to_ccxt(symbol)
        market = self.exchange.market(ccxt_symbol)
        contract_size = market['contractSize']
        precision = market['precision']['amount']

        contracts = base_amount / contract_size
        # Floor to exchange precision (avoid rounding up → insufficient margin)
        # Round after floor to fix floating point drift (e.g. 3.5100000000000002 → 3.51)
        import math
        step = precision
        decimals = max(0, -int(math.log10(step))) if step < 1 else 0
        contracts = round(math.floor(contracts / step) * step, decimals)

        if contracts < market['limits']['amount']['min']:
            logger.warning(
                f"{symbol}: Calculated {contracts} contracts < min {market['limits']['amount']['min']}. "
                f"Base amount {base_amount}, contractSize {contract_size}"
            )

        logger.info(
            f"{symbol}: {base_amount:.6f} base → {contracts} contracts "
            f"(contractSize={contract_size}, precision={precision})"
        )
        return contracts

    # ── Ensure Isolated Margin ────────────────────────────────────

    def _ensure_isolated_margin(self, symbol: str, leverage: int = 20) -> None:
        """Set isolated margin mode + leverage for a symbol (idempotent, cached).

        OKX requires setting margin mode and leverage together via the
        set-leverage endpoint with mgnMode param.
        """
        ccxt_symbol = self._to_ccxt(symbol)
        if ccxt_symbol in self._margin_mode_set:
            return
        try:
            # OKX: set leverage with isolated margin mode in one call
            # This implicitly sets the margin mode to isolated
            self.exchange.set_leverage(leverage, ccxt_symbol, params={
                'mgnMode': 'isolated',
            })
            self._margin_mode_set.add(ccxt_symbol)
            logger.info(f"[OKX] Set isolated margin + {leverage}x leverage for {symbol}")
        except (ccxt.ExchangeError, ccxt.BadRequest) as e:
            err_msg = str(e).lower()
            if 'margin mode' in err_msg or 'already' in err_msg or 'leverage' in err_msg:
                self._margin_mode_set.add(ccxt_symbol)
                logger.debug(f"[OKX] Margin mode already set for {symbol}")
            else:
                logger.error(f"[OKX] Error setting margin mode for {symbol}: {e}")
                raise

    # ── Market Data ───────────────────────────────────────────────

    # OKX returns max 300 candles per request
    _OKX_MAX_CANDLES = 300

    @retry_on_error(max_retries=3, delay=1.0)
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '4h',
        limit: int = 100,
        swap_only: bool = False
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a symbol. Auto-paginates if limit > 300.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            timeframe: Timeframe ('15m', '1h', '4h', etc.)
            limit: Number of candles to fetch
            swap_only: If True, only use SWAP data (no spot backfill).
                       Use for EMA610 to avoid mixing SWAP+SPOT price series.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)

            if limit <= self._OKX_MAX_CANDLES:
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol=ccxt_symbol,
                    timeframe=timeframe,
                    limit=limit
                )
            else:
                ohlcv = self._fetch_ohlcv_paginated(
                    ccxt_symbol, timeframe, limit, swap_only=swap_only
                )

            df = pd.DataFrame(
                ohlcv,
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )

            # Convert timestamp to datetime
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            return df

        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            raise

    # OKX timeframe mapping for raw API calls
    _OKX_TF_MAP = {
        '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
        '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6Hutc', '12h': '12Hutc',
        '1d': '1Dutc', '1w': '1Wutc', '1M': '1Mutc',
    }

    def _fetch_ohlcv_paginated(
        self,
        ccxt_symbol: str,
        timeframe: str,
        limit: int,
        swap_only: bool = False
    ) -> list:
        """Fetch more than 300 candles by paginating backwards.

        Uses market/candles first (~1440 max), then falls back to
        market/history-candles for older data beyond that limit.
        If swap_only=True, skips Phase 3 (spot/Binance backfill).
        """
        all_candles = []
        remaining = limit
        since = None  # Start from most recent

        # Phase 1: fetch from market/candles (recent data, ~1440 max)
        while remaining > 0:
            batch_size = min(remaining, self._OKX_MAX_CANDLES)
            params = {}
            if since is not None:
                params['until'] = since

            batch = self.exchange.fetch_ohlcv(
                symbol=ccxt_symbol,
                timeframe=timeframe,
                limit=batch_size,
                params=params,
            )

            if not batch:
                break

            all_candles = batch + all_candles
            remaining -= len(batch)

            if len(batch) < batch_size:
                # market/candles exhausted — switch to history-candles
                break

            since = batch[0][0]

        # Phase 2: if still need more, use market/history-candles
        if remaining > 0 and all_candles:
            oldest_ts = all_candles[0][0]  # oldest timestamp from phase 1
            inst_id = ccxt_symbol.replace('/', '-').replace(':USDT', '-SWAP')
            okx_bar = self._OKX_TF_MAP.get(timeframe, timeframe)

            while remaining > 0:
                batch_size = min(remaining, 100)  # history-candles max 100/request
                result = self.exchange.publicGetMarketHistoryCandles({
                    'instId': inst_id,
                    'bar': okx_bar,
                    'limit': str(batch_size),
                    'after': str(oldest_ts),
                })
                data = result.get('data', [])
                if not data:
                    break

                # Convert OKX raw format to ccxt format [ts, o, h, l, c, vol]
                # SWAP: c[5]=contracts, c[6]=volCcy (base currency).
                # CCXT fetch_ohlcv returns volCcy for SWAP, so use c[6] to match.
                batch = [
                    [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[6])]
                    for c in data
                ]
                # data is newest-first, reverse to oldest-first then prepend
                batch.reverse()
                all_candles = batch + all_candles
                remaining -= len(batch)

                if len(data) < batch_size:
                    break

                oldest_ts = int(data[-1][0])  # oldest in this batch

        # Phase 3: if still need more, backfill from spot data
        # Skip spot backfill when swap_only=True (EMA610 needs homogeneous data)
        if remaining > 0 and all_candles and not swap_only:
            all_candles = self._backfill_from_spot(
                ccxt_symbol, timeframe, remaining, all_candles
            )

        return all_candles

    def _backfill_from_spot(
        self,
        ccxt_symbol: str,
        timeframe: str,
        remaining: int,
        existing_candles: list,
    ) -> list:
        """Backfill older candles from OKX SPOT + Binance when SWAP data is limited.

        Priority: OKX spot market/candles → OKX spot history-candles → Binance spot.
        OKX SPOT often has much longer history than SWAP contracts.
        """
        oldest_ts = existing_candles[0][0]
        base = ccxt_symbol.split('/')[0]
        spot_inst_id = f"{base}-USDT"
        spot_ccxt = f"{base}/USDT"
        okx_bar = self._OKX_TF_MAP.get(timeframe, timeframe)

        all_backfill = []
        cursor_ts = oldest_ts

        # Phase 3a: OKX spot market/candles (recent ~1440 candles)
        try:
            while remaining > 0:
                batch_size = min(remaining, self._OKX_MAX_CANDLES)
                params = {'until': cursor_ts}
                batch = self.exchange.fetch_ohlcv(
                    symbol=spot_ccxt,
                    timeframe=timeframe,
                    limit=batch_size,
                    params=params,
                )
                if not batch:
                    break

                all_backfill = batch + all_backfill
                remaining -= len(batch)

                if len(batch) < batch_size:
                    break
                cursor_ts = batch[0][0]

        except Exception as e:
            logger.debug(f"OKX spot candles failed for {spot_ccxt}: {e}")

        # Phase 3b: OKX spot market/history-candles (older data)
        if remaining > 0:
            cursor_ts = all_backfill[0][0] if all_backfill else oldest_ts
            try:
                while remaining > 0:
                    batch_size = min(remaining, 100)
                    result = self.exchange.publicGetMarketHistoryCandles({
                        'instId': spot_inst_id,
                        'bar': okx_bar,
                        'limit': str(batch_size),
                        'after': str(cursor_ts),
                    })
                    data = result.get('data', [])
                    if not data:
                        break

                    batch = [
                        [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                        for c in data
                    ]
                    batch.reverse()
                    all_backfill = batch + all_backfill
                    remaining -= len(batch)

                    if len(data) < batch_size:
                        break
                    cursor_ts = int(data[-1][0])

            except Exception as e:
                logger.debug(f"OKX spot history failed for {spot_inst_id}: {e}")

        # Phase 3c: Binance spot fallback
        if remaining > 0:
            try:
                binance_symbol = ccxt_symbol.split(':')[0]
                if not hasattr(self, '_binance_public'):
                    self._binance_public = ccxt.binance({'enableRateLimit': True})

                end_time = all_backfill[0][0] if all_backfill else oldest_ts

                while remaining > 0:
                    batch_size = min(remaining, 1000)
                    batch = self._binance_public.fetch_ohlcv(
                        symbol=binance_symbol,
                        timeframe=timeframe,
                        limit=batch_size,
                        params={'endTime': end_time - 1},
                    )
                    if not batch:
                        break

                    all_backfill = batch + all_backfill
                    remaining -= len(batch)

                    if len(batch) < batch_size:
                        break
                    end_time = batch[0][0]

            except Exception as e:
                logger.debug(f"Binance backfill failed for {ccxt_symbol}: {e}")

        if all_backfill:
            logger.info(
                f"Backfilled {len(all_backfill)} candles from spot "
                f"for {ccxt_symbol} {timeframe}"
            )
            return all_backfill + existing_candles

        return existing_candles

    @retry_on_error(max_retries=3, delay=0.5)
    def get_current_price(self, symbol: str) -> float:
        """
        Get current market price

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')

        Returns:
            Current price
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)
            ticker = self.exchange.fetch_ticker(ccxt_symbol)
            return float(ticker['last'])

        except Exception as e:
            logger.error(f"Error fetching price for {symbol}: {e}")
            raise

    @retry_on_error(max_retries=3, delay=1.0)
    def get_account_balance(self) -> Dict[str, float]:
        """
        Get futures account balance

        Returns:
            Dict with asset balances (e.g., {'USDT': 1000.0})
        """
        try:
            balance = self.exchange.fetch_balance({'type': 'swap'})
            return balance['total']

        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise

    # ── Trading Operations ────────────────────────────────────────

    @retry_on_error(max_retries=2, delay=1.0)
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            leverage: Leverage value (1-125)

        Returns:
            True if successful
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)
            # _ensure_isolated_margin already sets leverage + margin mode together
            self._margin_mode_set.discard(ccxt_symbol)  # Force re-set with new leverage
            self._ensure_isolated_margin(symbol, leverage)
            logger.info(f"Set {symbol} leverage to {leverage}x")
            return True

        except Exception as e:
            logger.error(f"Error setting leverage for {symbol}: {e}")
            return False

    @retry_on_error(max_retries=2, delay=0.5)
    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        reduce_only: bool = False
    ) -> Dict:
        """
        Create market order

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            side: 'buy' or 'sell'
            amount: Order amount in base currency
            reduce_only: If True, only reduce position (for closing)

        Returns:
            Order info dict
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)
            # Only set margin mode when opening new positions, NOT when closing
            # (closing with _ensure_isolated_margin uses default 20x, corrupting leverage)
            if not reduce_only:
                self._ensure_isolated_margin(symbol)

            # OKX swap: amount is in contracts, not base currency
            contracts = self._base_to_contracts(symbol, amount)
            if contracts <= 0:
                raise ValueError(
                    f"{symbol}: 0 contracts from {amount} base amount — "
                    f"increase margin or check contractSize"
                )

            params = {}
            if reduce_only:
                params['reduceOnly'] = True

            order = self.exchange.create_market_order(
                symbol=ccxt_symbol,
                side=side,
                amount=contracts,
                params=params
            )

            logger.info(
                f"Market order created: {side.upper()} {contracts} contracts "
                f"({amount:.6f} base) {symbol}"
            )
            return order

        except Exception as e:
            logger.error(f"Error creating market order: {e}")
            raise

    def get_open_positions(self) -> List[Dict]:
        """
        Get all open futures positions

        Returns:
            List of position dicts
        """
        try:
            positions = self.exchange.fetch_positions()

            # Filter out positions with no amount
            open_positions = [
                p for p in positions
                if float(p.get('contracts', 0)) > 0
            ]

            return open_positions

        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            raise

    @retry_on_error(max_retries=2, delay=0.5)
    def get_positions_pnl(self) -> Dict[str, Dict]:
        """
        Fetch unrealizedPnl and mark price for all open positions from OKX.

        Returns:
            Dict keyed by plain symbol (e.g. 'BTCUSDT') with:
              - unrealized_pnl: float (USDT)
              - mark_price: float
              - margin: float (actual margin on exchange)
              - leverage: int
              - notional: float (position value)
              - side: str ('long' or 'short')
              - percentage: float (ROI % from exchange)
        """
        try:
            positions = self.exchange.fetch_positions()
            result = {}
            for p in positions:
                contracts = float(p.get('contracts', 0) or 0)
                if contracts <= 0:
                    continue

                symbol = self._from_ccxt(p.get('symbol', ''))
                info = p.get('info', {})

                result[symbol] = {
                    'unrealized_pnl': float(p.get('unrealizedPnl', 0) or 0),
                    'mark_price': float(p.get('markPrice', 0) or 0),
                    'entry_price': float(p.get('entryPrice', 0) or info.get('avgPx', 0) or 0),
                    'margin': float(p.get('collateral', 0) or info.get('margin', 0) or 0),
                    'leverage': int(float(p.get('leverage', 0) or 0)),
                    'notional': float(p.get('notional', 0) or 0),
                    'side': p.get('side', ''),
                    'percentage': float(p.get('percentage', 0) or 0),
                }

            return result

        except Exception as e:
            logger.error(f"Error fetching positions PNL: {e}")
            return {}

    def _parse_position_record(self, p: dict) -> dict:
        """Parse a single OKX position history record into our format."""
        from datetime import datetime, timezone

        inst_id = p.get('instId', '')
        symbol = inst_id.replace('-SWAP', '').replace('-', '')

        close_ts = p.get('uTime', '')
        close_time = ''
        if close_ts:
            close_time = datetime.fromtimestamp(
                int(close_ts) / 1000, tz=timezone.utc
            ).isoformat()

        open_ts = p.get('cTime', '')
        open_time = ''
        if open_ts:
            open_time = datetime.fromtimestamp(
                int(open_ts) / 1000, tz=timezone.utc
            ).isoformat()

        # OKX type: 1=partial close, 2=full close, 3=liquidation,
        # 4=partial liquidation, 5=ADL partial, 6=ADL full
        okx_type = p.get('type', '')
        reason_map = {
            '1': 'PARTIAL_CLOSE',
            '2': 'CLOSED',
            '3': 'LIQUIDATION',
            '4': 'PARTIAL_LIQUIDATION',
            '5': 'ADL_PARTIAL',
            '6': 'ADL_FULL',
        }
        close_reason = reason_map.get(okx_type, okx_type)

        return {
            'pos_id': p.get('posId', ''),
            'symbol': symbol,
            'side': p.get('direction', ''),
            'leverage': int(float(p.get('lever', 0) or 0)),
            'open_price': float(p.get('openAvgPx', 0) or 0),
            'close_price': float(p.get('closeAvgPx', 0) or 0),
            'realized_pnl': float(p.get('realizedPnl', 0) or 0),
            'pnl_ratio': float(p.get('pnlRatio', 0) or 0),
            'fee': float(p.get('fee', 0) or 0),
            'funding_fee': float(p.get('fundingFee', 0) or 0),
            'open_time': open_time,
            'close_time': close_time,
            'close_reason': close_reason,
        }

    @retry_on_error(max_retries=2, delay=0.5)
    def get_position_history(self) -> List[Dict]:
        """
        Fetch ALL closed position history from OKX with pagination.
        Each page returns up to 100 records. Paginates until no more data.

        Returns:
            List of dicts with: pos_id, symbol, side, leverage, open_price,
            close_price, realized_pnl, close_time, fee, funding_fee
        """
        import time as _time

        all_positions = []
        after_id = None

        try:
            for page in range(20):  # safety limit: max 2000 positions
                params = {'instType': 'SWAP', 'limit': '100'}
                if after_id:
                    params['after'] = after_id

                result = self.exchange.privateGetAccountPositionsHistory(params=params)
                batch = result.get('data', [])

                if not batch:
                    break

                all_positions.extend(batch)

                # OKX pagination: use last item's uTime (ms timestamp) as cursor
                last_utime = batch[-1].get('uTime', '')
                if not last_utime or len(batch) < 100:
                    break  # no more pages
                after_id = last_utime

                # Rate limit: small delay between pages
                if page > 0:
                    _time.sleep(0.2)

            history = [self._parse_position_record(p) for p in all_positions]
            logger.info(f"Fetched {len(history)} position history records from OKX ({page + 1} pages)")
            return history
        except Exception as e:
            logger.error(f"Error fetching position history: {e}")
            # Return what we have so far
            if all_positions:
                return [self._parse_position_record(p) for p in all_positions]
            return []

    def get_pnl_summary(self, days: int = 30) -> Dict:
        """
        Fetch accurate PnL summary using bills + bills-archive endpoints.
        These endpoints support pagination (unlike positions-history).

        Returns:
            Dict with: realized_pnl, total_fees, funding, net_pnl, trade_count
        """
        from datetime import datetime, timedelta, timezone
        import time as _time

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ms = int(cutoff.timestamp() * 1000)

        def _fetch_bills(endpoint_fn: callable) -> List[Dict]:
            """Paginate through a bills endpoint, stop at cutoff."""
            bills = []
            after_id = None
            for _ in range(100):  # safety limit
                params = {'instType': 'SWAP', 'limit': '100'}
                if after_id:
                    params['after'] = after_id
                try:
                    result = endpoint_fn(params=params)
                    batch = result.get('data', [])
                except Exception as e:
                    logger.warning(f"[OKX] Bills fetch error: {e}")
                    break
                if not batch:
                    break
                for b in batch:
                    if int(b.get('ts', 0)) >= cutoff_ms:
                        bills.append(b)
                if int(batch[-1].get('ts', 0)) < cutoff_ms:
                    break
                after_id = batch[-1].get('billId', '')
                _time.sleep(0.3)
            return bills

        try:
            # Recent bills (last ~7 days) + archive (7 days to 3 months)
            recent = _fetch_bills(self.exchange.privateGetAccountBills)
            archive = _fetch_bills(self.exchange.privateGetAccountBillsArchive)

            # Deduplicate by billId
            seen = set()
            all_bills = []
            for b in recent + archive:
                bid = b.get('billId', '')
                if bid not in seen:
                    seen.add(bid)
                    all_bills.append(b)

            # Aggregate by type: 2=trade, 8=funding
            trade_bills = [b for b in all_bills if b.get('type') == '2']
            funding_bills = [b for b in all_bills if b.get('type') == '8']

            trade_pnl = sum(float(b.get('pnl', 0) or 0) for b in trade_bills)
            trade_fee = sum(float(b.get('fee', 0) or 0) for b in trade_bills)
            funding = sum(float(b.get('pnl', 0) or 0) for b in funding_bills)

            # Count positions: unique instId+close fills (pnl != 0)
            close_positions = set()
            for b in trade_bills:
                pnl_val = float(b.get('pnl', 0) or 0)
                if pnl_val != 0:
                    # Group by instId + ordId to count unique position closes
                    close_positions.add(b.get('ordId', ''))

            net_pnl = trade_pnl + trade_fee + funding

            logger.info(
                f"[OKX] Bills summary ({days}D): "
                f"pnl={trade_pnl:.2f}, fees={trade_fee:.2f}, "
                f"funding={funding:.2f}, net={net_pnl:.2f}, "
                f"bills={len(all_bills)}"
            )

            return {
                'realized_pnl': round(trade_pnl, 2),
                'total_fees': round(abs(trade_fee), 2),
                'funding': round(funding, 4),
                'net_pnl': round(net_pnl, 2),
                'trade_count': len(close_positions),
            }
        except Exception as e:
            logger.error(f"Error fetching PnL summary: {e}")
            return {}

    def get_daily_pnl(self, days: int = 90) -> List[Dict]:
        """
        Fetch daily PNL from OKX bills endpoint (matches Trading Calendar exactly).
        Uses bills (recent ~7 days) + bills-archive (7 days to 3 months).

        Returns:
            List of dicts: [{"date": "2026-02-23", "pnl": 62.3, "fee": -1.5,
                             "funding": 0.12, "net_pnl": 60.92, "count": 5}, ...]
        """
        from datetime import datetime, timedelta, timezone
        from collections import defaultdict
        import time as _time

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_ms = int(cutoff.timestamp() * 1000)

        def _fetch_bills(endpoint_fn: callable) -> List[Dict]:
            """Paginate through a bills endpoint, stop at cutoff."""
            bills = []
            after_id = None
            for _ in range(100):
                params = {'instType': 'SWAP', 'limit': '100'}
                if after_id:
                    params['after'] = after_id
                try:
                    result = endpoint_fn(params=params)
                    batch = result.get('data', [])
                except Exception as e:
                    logger.warning(f"[OKX] Bills fetch error: {e}")
                    break
                if not batch:
                    break
                for b in batch:
                    if int(b.get('ts', 0)) >= cutoff_ms:
                        bills.append(b)
                if int(batch[-1].get('ts', 0)) < cutoff_ms:
                    break
                after_id = batch[-1].get('billId', '')
                _time.sleep(0.3)
            return bills

        try:
            recent = _fetch_bills(self.exchange.privateGetAccountBills)
            archive = _fetch_bills(self.exchange.privateGetAccountBillsArchive)

            # Deduplicate by billId
            seen = set()
            all_bills = []
            for b in recent + archive:
                bid = b.get('billId', '')
                if bid not in seen:
                    seen.add(bid)
                    all_bills.append(b)

            # Group by date in user's timezone (matches OKX Trading Calendar)
            from src.trading.core.config import TIMEZONE_OFFSET
            user_tz = timezone(timedelta(hours=TIMEZONE_OFFSET))
            daily: dict[str, dict] = defaultdict(
                lambda: {"pnl": 0.0, "fee": 0.0, "funding": 0.0, "orders": set()}
            )
            # Bill types that affect PNL: 2=trade, 5=liquidation, 8=funding, 9=ADL
            pnl_types = {'2', '5', '9'}  # trade + liquidation + ADL
            for b in all_bills:
                ts_ms = int(b.get('ts', 0))
                dt_local = datetime.fromtimestamp(ts_ms / 1000, tz=user_tz)
                date_key = dt_local.strftime("%Y-%m-%d")
                bill_type = b.get('type', '')
                pnl_val = float(b.get('pnl', 0) or 0)
                fee_val = float(b.get('fee', 0) or 0)

                if bill_type in pnl_types:
                    daily[date_key]["pnl"] += pnl_val
                    daily[date_key]["fee"] += fee_val
                    # Count unique orders (not individual fills)
                    ord_id = b.get('ordId', '')
                    if pnl_val != 0 and ord_id:
                        daily[date_key]["orders"].add(ord_id)
                elif bill_type == '8':  # funding
                    daily[date_key]["funding"] += pnl_val

            result = []
            for date_key in sorted(daily.keys()):
                d = daily[date_key]
                result.append({
                    "date": date_key,
                    "pnl": round(d["pnl"], 4),
                    "fee": round(d["fee"], 4),
                    "funding": round(d["funding"], 4),
                    "net_pnl": round(d["pnl"] + d["fee"] + d["funding"], 2),
                    "count": len(d["orders"]),
                })

            logger.info(f"[OKX] Daily PNL: {len(result)} days, {len(all_bills)} bills")
            return result
        except Exception as e:
            logger.error(f"Error fetching daily PNL: {e}")
            return []

    def get_position_close_fills(
        self, symbol: str, close_time_utc: str = '', **_kwargs
    ) -> list[dict]:
        """
        Fetch per-fill close data for a recently closed OKX position.
        Matches by instId + time range (OKX bills don't include posId).

        Args:
            symbol: Bot symbol (e.g. 'PEPEUSDT')
            close_time_utc: ISO UTC close time from OKX position history

        Returns list of close events (grouped by ordId) with:
            fill_price, total_size, pnl, fee, timestamp, order_id
        """
        from collections import defaultdict
        from datetime import datetime as _dt, timezone as _tz

        inst_id = self._to_okx_inst_id(symbol)

        # Parse close time for filtering (±30 min window)
        close_dt = None
        if close_time_utc:
            try:
                close_dt = _dt.fromisoformat(close_time_utc).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        try:
            # Fetch bills without instId filter (some coins have naming issues)
            # then filter client-side by instId
            close_bills = []
            for endpoint_fn in [
                self.exchange.privateGetAccountBills,
                self.exchange.privateGetAccountBillsArchive,
            ]:
                after = None
                for page in range(5):  # Max 500 bills per endpoint
                    params = {'instType': 'SWAP', 'type': '2', 'limit': '100'}
                    if after:
                        params['after'] = after
                    result = endpoint_fn(params=params)
                    batch = result.get('data', [])
                    if not batch:
                        break

                    for b in batch:
                        if b.get('instId') != inst_id:
                            continue
                        if abs(float(b.get('pnl', 0) or 0)) < 0.001:
                            continue  # Entry fill (pnl=0)
                        # Time filter if close_time provided
                        if close_dt:
                            bill_ts = int(b['ts']) / 1000
                            bill_dt = _dt.fromtimestamp(bill_ts, tz=_tz.utc).replace(
                                tzinfo=None
                            )
                            if abs((bill_dt - close_dt).total_seconds()) > 1800:
                                continue
                        close_bills.append(b)

                    after = batch[-1].get('billId', '')
                    if len(batch) < 100:
                        break
                    # Stop paginating if we've gone past the time window
                    if close_dt and batch:
                        oldest_ts = int(batch[-1]['ts']) / 1000
                        oldest_dt = _dt.fromtimestamp(oldest_ts, tz=_tz.utc).replace(
                            tzinfo=None
                        )
                        if (close_dt - oldest_dt).total_seconds() > 3600:
                            break

                if close_bills:
                    break  # Found in recent, skip archive

            if not close_bills:
                return []

            # Group by ordId (one order may have multiple partial fills)
            order_groups = defaultdict(lambda: {
                'notional': 0.0, 'total_size': 0.0, 'pnl': 0.0,
                'fee': 0.0, 'timestamp': '', 'order_id': ''
            })

            for b in close_bills:
                ord_id = b.get('ordId', '')
                g = order_groups[ord_id]
                px = float(b.get('px', 0) or 0)
                sz = float(b.get('sz', 0) or 0)
                g['notional'] += px * sz
                g['total_size'] += sz
                g['pnl'] += float(b.get('pnl', 0) or 0)
                g['fee'] += float(b.get('fee', 0) or 0)
                g['order_id'] = ord_id
                ts = b.get('ts', '')
                if ts > g['timestamp']:
                    g['timestamp'] = ts

            # Calculate weighted average fill price per order
            close_events = []
            for g in order_groups.values():
                if g['total_size'] > 0:
                    g['fill_price'] = g['notional'] / g['total_size']
                else:
                    g['fill_price'] = 0
                del g['notional']
                close_events.append(g)

            close_events.sort(key=lambda e: e['timestamp'])

            logger.info(
                f"[OKX] {symbol}: Found {len(close_events)} close events "
                f"(from {len(close_bills)} fills)"
            )
            return close_events

        except Exception as e:
            logger.error(f"[OKX] Error fetching close fills for {symbol}: {e}")
            return []

    @retry_on_error(max_retries=2, delay=0.5)
    def close_position(
        self,
        symbol: str,
        side: str,
        amount: float
    ) -> Dict:
        """
        Close a position

        Args:
            symbol: Trading pair
            side: 'buy' to close short, 'sell' to close long
            amount: Amount to close

        Returns:
            Order info
        """
        return self.create_market_order(
            symbol=symbol,
            side=side,
            amount=amount,
            reduce_only=True
        )

    # ── Position TP/SL (built-in, auto-cancels when position closes) ──

    @retry_on_error(max_retries=2, delay=0.5)
    def set_position_sl(
        self,
        symbol: str,
        side: str,
        sl_price: float,
        amount: float = 0,
    ) -> Dict:
        """
        Set built-in SL on a position using OKX's position TP/SL API.

        Unlike trigger orders, this SL is attached to the position itself:
        - Auto-cancels when position closes (no orphaned orders)
        - No duplicate order risk
        - Uses 'last' price as trigger type

        Args:
            symbol: Trading pair (e.g. 'BTCUSDT')
            side: 'sell' for long SL, 'buy' for short SL (close direction)
            sl_price: Stop loss trigger price
            amount: Position size in base currency (0 = query from exchange)
        """
        try:
            inst_id = self._to_okx_inst_id(symbol)

            sl_px = self.exchange.price_to_precision(
                self._to_ccxt(symbol), sl_price
            )

            # Get position size in contracts
            if amount > 0:
                contracts = self._base_to_contracts(symbol, amount)
            else:
                positions = self.exchange.fetch_positions([self._to_ccxt(symbol)])
                contracts = 0
                for p in positions:
                    if abs(float(p.get('contracts', 0))) > 0:
                        contracts = abs(int(float(p['contracts'])))
                        break
                if contracts <= 0:
                    raise ValueError(f"{symbol}: No open position found for SL")

            # OKX ordType='conditional' = position TP/SL
            # slOrdPx=-1 means market order when SL triggered
            # reduceOnly=true so it closes position
            result = self.exchange.privatePostTradeOrderAlgo({
                'instId': inst_id,
                'tdMode': 'isolated',
                'side': side,
                'ordType': 'conditional',
                'slTriggerPx': sl_px,
                'slOrdPx': '-1',
                'slTriggerPxType': 'last',
                'sz': str(contracts),
                'reduceOnly': True,
            })

            if result.get('code') != '0':
                error_msg = result.get('msg', '') or str(result.get('data', ''))
                raise Exception(
                    f"OKX set position SL failed: code={result.get('code')} msg={error_msg}"
                )

            algo_id = result['data'][0].get('algoId', '?')
            logger.info(
                f"[ORDER] POSITION SL {symbol} @ {sl_price} -> algoId={algo_id}"
            )
            return {'id': algo_id, 'info': result}

        except Exception as e:
            logger.error(f"[ORDER] Error setting position SL for {symbol}: {e}")
            raise

    def cancel_position_sl(self, symbol: str, algo_id: str) -> bool:
        """Cancel a built-in position SL by algoId."""
        try:
            inst_id = self._to_okx_inst_id(symbol)
            result = self.exchange.privatePostTradeCancelAlgos([{
                'instId': inst_id,
                'algoId': algo_id,
            }])
            if result.get('code') == '0':
                logger.info(f"[ORDER] Cancelled position SL {symbol} algoId={algo_id[:12]}")
                return True
            else:
                logger.warning(f"[ORDER] Cancel position SL failed: {result}")
                return False
        except Exception as e:
            logger.warning(f"[ORDER] Cancel position SL error {symbol}: {e}")
            return False

    # ── Stop / Trigger Orders (for Chandelier Exit trailing SL) ──

    @retry_on_error(max_retries=2, delay=0.5)
    def create_stop_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        reduce_only: bool = True
    ) -> Dict:
        """
        Create a trigger (stop-market) order on OKX Futures.

        Uses raw OKX API with reduceOnly=true so the child market order
        is treated as a position close (no new margin needed).
        Without reduceOnly, OKX treats the child order as opening a new
        position and auto-reduces size when margin is insufficient.

        Args:
            symbol: Trading pair (e.g. 'BTCUSDT')
            side: 'sell' for long SL, 'buy' for short SL
            stop_price: Trigger price
            amount: Position size to close (base currency)
            reduce_only: True = close position, False = open position
        """
        try:
            inst_id = self._to_okx_inst_id(symbol)
            contracts = self._base_to_contracts(symbol, amount)
            if contracts <= 0:
                raise ValueError(f"{symbol}: 0 contracts from {amount} base for stop order")

            # Use raw OKX API to pass reduceOnly directly
            # ccxt emulates reduceOnly via posSide which doesn't work in net_mode
            # Use limit order when triggered (maker fee 0.02% vs taker 0.05%)
            # Limit price = trigger price exactly (matches config hard_sl_roi %)
            algo_params = {
                'instId': inst_id,
                'tdMode': 'isolated',
                'side': side,
                'ordType': 'trigger',
                'triggerPx': self.exchange.price_to_precision(self._to_ccxt(symbol), stop_price),
                'triggerPxType': 'last',
                'orderPx': self.exchange.price_to_precision(self._to_ccxt(symbol), stop_price),
                'sz': str(contracts),
            }

            # Try with reduceOnly first, fallback to position SL if 51205
            # CRITICAL: Never drop reduceOnly — a non-reduceOnly trigger order
            # can OPEN a new position (ghost) if the original is already closed
            # by the bot's tick-based SL detection (race condition).
            if reduce_only:
                algo_params['reduceOnly'] = True
                try:
                    result = self.exchange.privatePostTradeOrderAlgo(algo_params)
                except Exception as e:
                    if '51205' in str(e):
                        logger.warning(
                            f"[ORDER] {symbol}: reduceOnly rejected (51205), "
                            f"falling back to position SL (auto-cancels on close)"
                        )
                        # Fallback: position-level SL auto-cancels when position
                        # closes, eliminating the race condition entirely
                        return self.set_position_sl(
                            symbol=symbol,
                            side=side,
                            sl_price=stop_price,
                            amount=amount,
                        )
                    else:
                        raise
            else:
                result = self.exchange.privatePostTradeOrderAlgo(algo_params)

            if result.get('code') != '0':
                error_msg = result.get('msg', '') or str(result.get('data', ''))
                raise Exception(f"OKX algo order failed: code={result.get('code')} msg={error_msg}")

            algo_id = result['data'][0].get('algoId', '?')
            logger.info(
                f"[ORDER] TRIGGER {side.upper()} {contracts} contracts "
                f"({amount:.6f} base) {symbol} "
                f"@ trigger={stop_price} reduceOnly={reduce_only} "
                f"-> algoId={algo_id}"
            )
            return {'id': algo_id, 'info': result}

        except Exception as e:
            logger.error(f"[ORDER] Error creating trigger order: {e}")
            raise

    def create_take_profit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        tp_price: float,
        reduce_only: bool = True
    ) -> Dict:
        """
        Create a take-profit limit order on OKX Futures.

        Places a plain limit order at tp_price (sits on orderbook immediately).
        Fills when price reaches tp_price. Used for TP1/TP2.

        Args:
            symbol: Trading pair (e.g. 'BTCUSDT')
            side: 'sell' for long TP, 'buy' for short TP
            tp_price: Take profit price
            amount: Position size to close
            reduce_only: Always True for TP orders
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)
            # OKX swap: convert base amount to contracts
            contracts = self._base_to_contracts(symbol, amount)
            if contracts <= 0:
                raise ValueError(f"{symbol}: 0 contracts from {amount} base for TP order")

            params = {
                'tdMode': 'isolated',
                'reduceOnly': True,
            }
            order = self.exchange.create_limit_order(
                symbol=ccxt_symbol,
                side=side,
                amount=contracts,
                price=tp_price,
                params=params
            )
            logger.info(
                f"[ORDER] TP {side.upper()} {contracts} contracts "
                f"({amount:.6f} base) {symbol} "
                f"@ {tp_price} -> id={order.get('id', '?')}"
            )
            return order
        except Exception as e:
            logger.error(f"[ORDER] Error creating TP order: {e}")
            raise

    @retry_on_error(max_retries=2, delay=0.5)
    def create_entry_limit_order(
        self,
        symbol: str,
        side: str,
        amount_usdt: float,
        price: float,
        leverage: int = 5
    ) -> Dict:
        """
        Create an entry limit order on OKX Futures (non-reduceOnly).

        Used for EMA610 pre-placed limit entries. Sets up isolated margin
        and leverage before placing the order.

        Args:
            symbol: Trading pair (e.g. 'BTCUSDT')
            side: 'buy' or 'sell'
            amount_usdt: Notional value in USDT (margin * leverage)
            price: Limit price to place order at
            leverage: Leverage for the position

        Returns:
            Order info dict with 'id'
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)
            self._ensure_isolated_margin(symbol, leverage)

            # Calculate base amount from USDT notional
            base_amount = amount_usdt / price
            contracts = self._base_to_contracts(symbol, base_amount)
            if contracts <= 0:
                raise ValueError(
                    f"{symbol}: 0 contracts from {amount_usdt} USDT / {price} price"
                )

            params = {
                'tdMode': 'isolated',
            }
            order = self.exchange.create_limit_order(
                symbol=ccxt_symbol,
                side=side,
                amount=contracts,
                price=price,
                params=params
            )
            logger.info(
                f"[ORDER] Entry limit {side.upper()} {contracts} contracts "
                f"{symbol} @ {price} -> id={order.get('id', '?')}"
            )
            return order
        except Exception as e:
            logger.error(f"[ORDER] Error creating entry limit order: {e}")
            raise

    @retry_on_error(max_retries=2, delay=0.5)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an order by ID. Tries normal cancel first, then algo cancel."""
        ccxt_symbol = self._to_ccxt(symbol)
        # Convert symbol to OKX instId format (BTC-USDT-SWAP)
        base = symbol.replace('USDT', '') if '/' not in symbol else symbol.split('/')[0]
        inst_id = f"{base}-USDT-SWAP"

        # Try normal order cancel first
        try:
            self.exchange.cancel_order(order_id, ccxt_symbol)
            logger.info(f"[ORDER] Cancelled {order_id} ({symbol})")
            return True
        except ccxt.OrderNotFound:
            pass
        except Exception:
            pass

        # Try algo order cancel (TP/SL/trigger orders)
        try:
            result = self.exchange.privatePostTradeCancelAlgos([{
                'instId': inst_id,
                'algoId': order_id,
            }])
            if result.get('code') == '0':
                logger.info(f"[ORDER] Cancelled algo {order_id} ({symbol})")
                return True
            else:
                logger.warning(f"[ORDER] Algo cancel response: {result}")
                return False
        except Exception as e:
            logger.warning(f"[ORDER] Cancel failed for {order_id}: {e}")
            return False

    @retry_on_error(max_retries=2, delay=0.5)
    def fetch_order(self, order_id: str, symbol: str) -> Dict:
        """Fetch order status. Tries normal order first, then algo order."""
        ccxt_symbol = self._to_ccxt(symbol)
        base = symbol.replace('USDT', '') if '/' not in symbol else symbol.split('/')[0]
        inst_id = f"{base}-USDT-SWAP"

        # Try normal order first
        try:
            return self.exchange.fetch_order(order_id, ccxt_symbol)
        except ccxt.OrderNotFound:
            pass
        except Exception:
            pass

        # Try algo order (live)
        try:
            result = self.exchange.privateGetTradeOrderAlgo({
                'algoId': order_id,
            })
            data = result.get('data', [])
            if data:
                algo = data[0]
                state = algo.get('state', '')
                status_map = {
                    'live': 'open',
                    'effective': 'filled',
                    'canceled': 'canceled',
                    'order_failed': 'canceled',
                }
                return {
                    'id': order_id,
                    'status': status_map.get(state, state),
                    'info': algo,
                }
        except Exception as e:
            logger.error(f"[ORDER] Error fetching algo {order_id}: {e}")

        # Try algo order HISTORY (filled/canceled algo orders move here)
        try:
            result = self.exchange.privateGetTradeOrdersAlgoHistory({
                'ordType': 'conditional',
                'algoId': order_id,
            })
            data = result.get('data', [])
            if data:
                algo = data[0]
                state = algo.get('state', '')
                status_map = {
                    'effective': 'filled',
                    'canceled': 'canceled',
                    'order_failed': 'canceled',
                }
                return {
                    'id': order_id,
                    'status': status_map.get(state, state),
                    'info': algo,
                }
        except Exception as e:
            logger.error(f"[ORDER] Error fetching algo history {order_id}: {e}")

        logger.warning(f"[ORDER] {order_id} not found (normal + algo)")
        return {'status': 'not_found', 'id': order_id}

    # ── WebSocket ─────────────────────────────────────────────────

    async def _websocket_stream(
        self,
        symbol: str,
        stream_type: str = 'ticker'
    ):
        """
        Internal WebSocket stream handler for OKX

        Args:
            symbol: Trading pair (plain format, e.g. 'BTCUSDT')
            stream_type: 'ticker' or 'kline'
        """
        url = "wss://ws.okx.com:8443/ws/v5/public"
        inst_id = self._to_okx_inst_id(symbol)

        if stream_type == 'ticker':
            subscribe_msg = {
                "op": "subscribe",
                "args": [{"channel": "tickers", "instId": inst_id}]
            }
        else:
            subscribe_msg = {
                "op": "subscribe",
                "args": [{"channel": "candle15m", "instId": inst_id}]
            }

        try:
            async with websockets.connect(url) as websocket:
                await websocket.send(json.dumps(subscribe_msg))
                logger.info(f"WebSocket connected: {symbol} ({stream_type}) on OKX")

                while True:
                    message = await websocket.recv()
                    data = json.loads(message)

                    # Skip subscription confirmations
                    if 'event' in data:
                        continue

                    # Process data messages
                    if 'data' in data and symbol in self.price_callbacks:
                        for callback in self.price_callbacks[symbol]:
                            if stream_type == 'ticker':
                                # OKX ticker: data[0]['last'] = last price
                                price = float(data['data'][0].get('last', 0))
                                callback(symbol, price)
                            else:
                                # Kline data
                                kline = data['data'][0]
                                callback(symbol, kline)

        except Exception as e:
            logger.error(f"WebSocket error for {symbol}: {e}")

    def subscribe_price(
        self,
        symbol: str,
        callback: Callable[[str, float], None]
    ):
        """
        Subscribe to real-time price updates

        Args:
            symbol: Trading pair (plain format)
            callback: Function to call with (symbol, price)
        """
        if symbol not in self.price_callbacks:
            self.price_callbacks[symbol] = []

        self.price_callbacks[symbol].append(callback)

        # Start WebSocket if not already running
        if symbol not in self.ws_connections:
            task = asyncio.create_task(self._websocket_stream(symbol, 'ticker'))
            self.ws_connections[symbol] = task
            logger.info(f"Started price stream for {symbol}")

    def unsubscribe_price(self, symbol: str):
        """
        Unsubscribe from price updates

        Args:
            symbol: Trading pair
        """
        if symbol in self.ws_connections:
            self.ws_connections[symbol].cancel()
            del self.ws_connections[symbol]

        if symbol in self.price_callbacks:
            del self.price_callbacks[symbol]

        logger.info(f"Stopped price stream for {symbol}")

    # ── Utilities ─────────────────────────────────────────────────

    def get_funding_rate(self, symbol: str) -> float:
        """
        Get current funding rate

        Args:
            symbol: Trading pair

        Returns:
            Funding rate (e.g., 0.0001 = 0.01%)
        """
        try:
            ccxt_symbol = self._to_ccxt(symbol)
            funding = self.exchange.fetch_funding_rate(ccxt_symbol)
            return float(funding['fundingRate'])

        except Exception as e:
            logger.error(f"Error fetching funding rate: {e}")
            return 0.0

    def calculate_position_size(
        self,
        balance: float,
        risk_percent: float,
        entry_price: float,
        leverage: int
    ) -> float:
        """
        Calculate position size based on risk

        Args:
            balance: Account balance in USDT
            risk_percent: Risk percentage (e.g., 5 for 5%)
            entry_price: Entry price
            leverage: Leverage multiplier

        Returns:
            Position size in base currency (e.g., BTC amount)
        """
        # Capital to risk
        risk_capital = balance * (risk_percent / 100)

        # With leverage, we can control more
        position_value = risk_capital * leverage

        # Convert to base currency amount
        position_size = position_value / entry_price

        return position_size
