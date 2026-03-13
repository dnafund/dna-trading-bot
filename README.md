# DNA Trading Bot

Automated crypto futures trading bot with multi-timeframe EMA analysis, RSI divergence detection, Supply/Demand zone entries, and Chandelier Exit trailing stop — built for OKX Futures with Telegram alerts and a React web dashboard.

**14,000+ lines** of production-tested Python. Running live since early 2026.

---

## Features

### 3 Entry Strategies

**Standard EMA** (M5 / M15 / H1 / H4)
- Cascade filter: H4 trend > ADX > H1 trend > RSI > M15 trend > Wick touch EMA34/89 with rejection wick >= 40%
- ROI-based TP/SL per timeframe

**EMA610 Limit Orders** (H1 / H4)
- Pre-placed limit orders at EMA610 level, updated every candle close
- Trend alignment: EMA34 + EMA89 vs EMA610 direction check
- Max 4% distance filter, 0.5% touch tolerance

**RSI Divergence** (M15 / H1 / H4)
- Regular divergence detection (bullish/bearish)
- Auto-closes opposing positions on same symbol
- 1.5x leverage boost, EMA34 dynamic TP cap

### Risk Management

- **Chandelier Exit** trailing stop (period=34, mult=1.75) — matches TradingView exactly
- **CE fallback chain**: H4 > H1 > M15 when primary TF stuck on wrong side of entry
- **Smart SL breathing**: Skip CE exit when volume < 80% average
- **Partial close**: TP1 closes 50-70% of position, trails remainder to TP2
- **Tiered leverage**: BTC 20x, ETH/SOL 10x, mid-caps 7x, small-caps 5x

### Web Dashboard

- Real-time positions with WebSocket updates
- Performance analytics (PNL, win rate, profit factor, drawdown)
- Trade history with OKX-synced PNL data
- Backtest UI with equity chart
- Config editor for live parameter changes

### Backtest Engine

- Full strategy simulation matching live bot logic
- All 3 entry types (Standard, EMA610, RSI Divergence)
- Chandelier Exit with proper fallback chains
- Equity curve, trade-by-trade breakdown, exit stats

---

## Architecture

```
src/trading/
    bot.py                     # Main loop, signal processing
    core/
        config.py              # All strategy parameters
        indicators.py          # EMA, RSI, ATR, Chandelier Exit
        models.py              # Position, TradingSignal dataclasses
        sd_zones.py            # Supply/Demand zone detection
    strategy/
        signal_detector.py     # Multi-timeframe signal detection
    execution/
        position_manager.py    # Position lifecycle, TP/SL, trailing
        ema610_limit_manager.py
    exchanges/
        okx.py                 # OKX adapter (3050-candle pipeline)
    notifications/
        telegram_commands.py   # Telegram UI + alerts
    backtest/
        engine.py              # Backtest engine

web/
    backend/                   # FastAPI + WebSocket
    frontend/                  # React + TailwindCSS

scripts/                       # Backtest runner, data tools
pinescript/                    # TradingView indicators
```

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/dnafund/dna-trading-bot.git
cd dna-trading-bot
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your credentials
```

Required environment variables:

| Variable | Source |
|----------|--------|
| `OKX_API_KEY` | [OKX API Management](https://www.okx.com/account/my-api) |
| `OKX_API_SECRET` | Same as above |
| `OKX_PASSPHRASE` | Same as above |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) on Telegram |
| `TELEGRAM_CHAT_ID` | [@userinfobot](https://t.me/userinfobot) on Telegram |

### 3. Run

```bash
# Start trading bot
python -m src.trading.bot

# Start web dashboard (optional, separate terminal)
cd web/frontend && npm install && npm run build && cd ../..
pip install -r web/backend/requirements.txt
cd web/backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

### 4. Backtest

```bash
python scripts/run_backtest.py --symbol BTCUSDT --start 2025-01-01 --end 2026-01-01
```

---

## Strategy Parameters

Configurable in `src/trading/core/config.py`:

| Entry Type | Timeframe | TP1 | TP2 | SL | TP1 Close |
|------------|-----------|-----|-----|----|-----------|
| Standard | M5/M15 | 20% | 40% | 20% | 70% |
| Standard | H1 | 30% | 60% | 25% | 70% |
| Standard | H4 | 50% | 100% | 40% | 70% |
| EMA610 | H1 | 40% | 80% | 30% | 50% |
| EMA610 | H4 | 60% | 120% | 50% | 50% |
| RSI Div | M15 | 15% | 30% | 15% | 70% |
| RSI Div | H1 | 25% | 50% | 20% | 70% |
| RSI Div | H4 | 40% | 80% | 30% | 70% |

All values are ROI-based (percentage of margin).

---

## Data Pipeline

The bot needs 3050 candles per coin for EMA610 calculation. It cascades through multiple sources:

1. **OKX SWAP** `market/candles` — recent ~1440 candles
2. **OKX SWAP** `market/history-candles` — older SWAP data
3. **OKX SPOT** `market/candles` — spot has longer history
4. **OKX SPOT** `market/history-candles` — oldest available
5. **Binance SPOT** fallback — public API, no auth needed

Minimum 915 closed candles required (1.5x EMA period). Below = skip coin.

---

## Tech Stack

- **Python 3.10+** — async/await, type hints, dataclasses
- **OKX API** — SWAP futures, spot data backfill
- **FastAPI** — dashboard backend with WebSocket
- **React + TailwindCSS** — dashboard frontend
- **SQLite** — trade history, closed trades
- **Telegram Bot API** — notifications and commands
- **Pine Script** — TradingView indicator companion

---

## Disclaimer

This software is for **educational and research purposes only**. Cryptocurrency futures trading involves substantial risk of loss. Past performance (including backtest results) does not guarantee future results. Use at your own risk.

---

## License

MIT License — see [LICENSE](LICENSE) for details.
