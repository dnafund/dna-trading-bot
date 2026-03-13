"""
Binance Futures API Client

Handles WebSocket connections and API calls for futures trading
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


class BinanceFuturesClient:
    """
    Binance Futures API Client with WebSocket support
    """

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        """
        Initialize Binance Futures client

        Args:
            api_key: Binance API key (optional for market data)
            api_secret: Binance API secret (optional for market data)
        """
        self.exchange = ccxt.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # Use futures
            }
        })

        self.ws_connections: Dict[str, asyncio.Task] = {}
        self.price_callbacks: Dict[str, List[Callable]] = {}

    # Binance API max candles per request
    _MAX_CANDLES_PER_REQUEST = 1500

    # Timeframe → milliseconds lookup for pagination
    _TF_MS = {
        '1m': 60_000, '3m': 180_000, '5m': 300_000,
        '15m': 900_000, '30m': 1_800_000,
        '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000,
        '6h': 21_600_000, '8h': 28_800_000, '12h': 43_200_000,
        '1d': 86_400_000, '3d': 259_200_000, '1w': 604_800_000,
    }

    @retry_on_error(max_retries=3, delay=0.5)
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '4h',
        limit: int = 100
    ) -> pd.DataFrame:
        """
        Fetch OHLCV data for a symbol with automatic pagination.

        When ``limit`` exceeds the Binance per-request cap (1500), the
        method fetches in multiple batches going backwards in time and
        concatenates the results.

        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            timeframe: Timeframe ('15m', '1h', '4h', etc.)
            limit: Number of candles to fetch (supports >1500 via pagination)

        Returns:
            DataFrame with columns: open, high, low, close, volume
            indexed by timestamp (datetime)
        """
        try:
            if limit <= self._MAX_CANDLES_PER_REQUEST:
                # Simple single request
                ohlcv = self.exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=limit,
                )
            else:
                # Paginated fetch: walk backwards in time
                tf_ms = self._TF_MS.get(timeframe, 3_600_000)
                all_candles: list = []
                remaining = limit

                # First request: latest candles (no `since`)
                batch = self.exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    limit=min(remaining, self._MAX_CANDLES_PER_REQUEST),
                )
                if not batch:
                    ohlcv = []
                else:
                    all_candles = batch
                    remaining -= len(batch)

                    # Subsequent requests: go further back
                    while remaining > 0 and batch:
                        earliest_ts = batch[0][0]
                        since = earliest_ts - min(remaining, self._MAX_CANDLES_PER_REQUEST) * tf_ms
                        batch = self.exchange.fetch_ohlcv(
                            symbol=symbol,
                            timeframe=timeframe,
                            since=since,
                            limit=min(remaining, self._MAX_CANDLES_PER_REQUEST),
                        )
                        if not batch:
                            break
                        # Keep only candles older than what we already have
                        batch = [c for c in batch if c[0] < earliest_ts]
                        if not batch:
                            break
                        all_candles = batch + all_candles
                        remaining -= len(batch)

                    ohlcv = all_candles

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

    @retry_on_error(max_retries=3, delay=0.5)
    def get_current_price(self, symbol: str) -> float:
        """
        Get current market price

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')

        Returns:
            Current price
        """
        try:
            ticker = self.exchange.fetch_ticker(symbol)
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
            balance = self.exchange.fetch_balance()
            return balance['total']

        except Exception as e:
            logger.error(f"Error fetching balance: {e}")
            raise

    @retry_on_error(max_retries=2, delay=1.0)
    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        Set leverage for a symbol

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            leverage: Leverage value (1-125)

        Returns:
            True if successful
        """
        try:
            self.exchange.set_leverage(leverage, symbol)
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
            symbol: Trading pair (e.g., 'BTC/USDT')
            side: 'buy' or 'sell'
            amount: Order amount in base currency
            reduce_only: If True, only reduce position (for closing)

        Returns:
            Order info dict
        """
        try:
            params = {}
            if reduce_only:
                params['reduceOnly'] = True

            order = self.exchange.create_market_order(
                symbol=symbol,
                side=side,
                amount=amount,
                params=params
            )

            logger.info(f"Market order created: {side.upper()} {amount} {symbol}")
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

    # ── Stop-Market / Order Management (for CE trailing SL) ────────

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
        Create a STOP_MARKET order on Binance Futures.

        Triggers a market sell/buy when price reaches stop_price.
        Used for Chandelier Exit trailing SL.

        Args:
            symbol: Trading pair (e.g. 'BTCUSDT')
            side: 'sell' for long SL, 'buy' for short SL
            amount: Position size to close
            stop_price: Trigger price
            reduce_only: Always True for SL orders
        """
        try:
            params = {
                'stopPrice': stop_price,
                'reduceOnly': reduce_only,
            }
            order = self.exchange.create_order(
                symbol=symbol,
                type='STOP_MARKET',
                side=side,
                amount=amount,
                params=params
            )
            logger.info(
                f"[ORDER] STOP_MARKET {side.upper()} {amount} {symbol} "
                f"@ stop={stop_price} → id={order.get('id', '?')}"
            )
            return order
        except Exception as e:
            logger.error(f"[ORDER] Error creating stop-market: {e}")
            raise

    @retry_on_error(max_retries=2, delay=0.5)
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        """Cancel an order by ID. Returns True if cancelled."""
        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"[ORDER] Cancelled {order_id} ({symbol})")
            return True
        except ccxt.OrderNotFound:
            logger.warning(f"[ORDER] {order_id} not found (already filled/cancelled)")
            return False
        except Exception as e:
            logger.error(f"[ORDER] Error cancelling {order_id}: {e}")
            raise

    @retry_on_error(max_retries=2, delay=0.5)
    def fetch_order(self, order_id: str, symbol: str) -> Dict:
        """Fetch order status. Returns dict with 'status' field."""
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except ccxt.OrderNotFound:
            logger.warning(f"[ORDER] {order_id} not found")
            return {'status': 'not_found', 'id': order_id}
        except Exception as e:
            logger.error(f"[ORDER] Error fetching {order_id}: {e}")
            raise

    async def _websocket_stream(
        self,
        symbol: str,
        stream_type: str = 'ticker'
    ):
        """
        Internal WebSocket stream handler

        Args:
            symbol: Trading pair
            stream_type: 'ticker' or 'kline'
        """
        # Convert symbol format (BTC/USDT -> btcusdt)
        ws_symbol = symbol.replace('/', '').lower()

        if stream_type == 'ticker':
            url = f"wss://fstream.binance.com/ws/{ws_symbol}@ticker"
        else:
            url = f"wss://fstream.binance.com/ws/{ws_symbol}@kline_15m"

        try:
            async with websockets.connect(url) as websocket:
                logger.info(f"WebSocket connected: {symbol} ({stream_type})")

                while True:
                    message = await websocket.recv()
                    data = json.loads(message)

                    # Call registered callbacks
                    if symbol in self.price_callbacks:
                        for callback in self.price_callbacks[symbol]:
                            if stream_type == 'ticker':
                                price = float(data.get('c', 0))  # Close price
                                callback(symbol, price)
                            else:
                                # Kline data
                                kline = data.get('k', {})
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
            symbol: Trading pair
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

    def get_funding_rate(self, symbol: str) -> float:
        """
        Get current funding rate

        Args:
            symbol: Trading pair

        Returns:
            Funding rate (e.g., 0.0001 = 0.01%)
        """
        try:
            funding = self.exchange.fetch_funding_rate(symbol)
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
