# 🚀 Futures Trading Bot

Automated multi-timeframe futures trading system for Binance with Linear integration.

## 📋 Features

### ✅ Core Features
- **Multi-timeframe Strategy**: H4 (trend) → H1 (filter) → M15 (entry)
- **Auto Signal Detection**: EMA34/89 crossover with RSI filter
- **Risk Management**:
  - 5% account balance per trade
  - Max 2 positions per symbol
  - Auto stop loss at -50% PNL
  - Take profit: 70% at S/R, 30% at Fibo 1.618
- **Leverage**: BTC 20x, ETH/SOL 10x, Altcoins 5x
- **Real-time PNL Tracking**: WebSocket price updates
- **Linear Integration**: Auto-create issues, update PNL, close positions

### 🎯 Strategy

#### Step 1: H4 Trend Detection
```
BUY_TREND: EMA34 > EMA89 AND Price > EMA89
SELL_TREND: EMA34 < EMA89 AND Price < EMA89
```

#### Step 2: H1 RSI Filter
```
If BUY_TREND: RSI < 70 (not overbought)
If SELL_TREND: RSI > 30 (not oversold)
```

#### Step 3: M15 Entry Signal
```
- Price tests EMA34 or EMA89 (within 0.2% tolerance)
- Candle shows rejection wick >= 40% of body
  - BUY: Long lower wick (bullish rejection)
  - SELL: Long upper wick (bearish rejection)
```

#### Step 4: Position Management
```
Entry: Open position with calculated leverage
TP1 (70%): Close at nearest S/R level
TP2 (30%): Close at Fibonacci 1.618 extension
SL: Auto close at -50% PNL
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
cd /path/to/dna-trading-bot
pip install -r requirements.txt
```

Required packages:
- `ccxt` - Binance API
- `pandas` - Data processing
- `websockets` - Real-time price updates
- `python-dotenv` - Environment variables

### 2. Configure Environment

Create/update `.env` file:

```bash
# Binance Futures API (required for live trading)
BINANCE_API_KEY=your_binance_api_key
BINANCE_SECRET_KEY=your_binance_secret_key

# Linear API (for tracking)
LINEAR_API_KEY=your_linear_api_key
LINEAR_TEAM_ID=your_linear_team_id

# Trading configuration (optional)
TRADING_MODE=paper  # "paper" or "live"
TRADING_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
```

### 3. Run Bot

**Paper Trading (Recommended First):**
```bash
./scripts/trading/start_futures_bot.sh paper
```

**Live Trading (Real Money):**
```bash
./scripts/trading/start_futures_bot.sh live
```

---

## 📁 Project Structure

```
src/trading/futures/
├── __init__.py                  # Module exports
├── config.py                    # Configuration (leverage, risk, etc.)
├── indicators.py                # Technical indicators (EMA, RSI, S/R, Fibo)
├── binance_futures.py           # Binance Futures API client
├── signal_detector.py           # Multi-timeframe signal detection
├── position_manager.py          # Position management & PNL tracking
├── linear_integration.py        # Linear API integration
├── futures_bot.py               # Main bot (entry point)
└── README.md                    # This file
```

---

## ⚙️ Configuration

### Leverage Settings

Edit `config.py`:

```python
LEVERAGE = {
    "BTCUSDT": 20,    # Bitcoin: 20x
    "ETHUSDT": 10,    # Ethereum: 10x
    "SOLUSDT": 10,    # Solana: 10x
    "default": 5      # Other altcoins: 5x
}
```

### Risk Management

```python
RISK_MANAGEMENT = {
    "position_size_percent": 5,    # 5% of balance per trade
    "stop_loss_percent": 50,       # -50% PNL triggers SL
    "max_positions_per_pair": 2,   # Max 2 concurrent positions
}
```

### Take Profit

```python
TAKE_PROFIT = {
    "tp1_percent": 70,     # Close 70% at S/R
    "tp2_percent": 30,     # Close 30% at Fibo 1.618
}
```

### Symbols to Trade

```python
DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    # Add more symbols...
]
```

---

## 📊 Usage Examples

### Example 1: Start Paper Trading

```bash
cd /path/to/dna-trading-bot
./scripts/trading/start_futures_bot.sh paper
```

Output:
```
================================
🤖 Futures Trading Bot
================================

📝 Paper trading mode (no real money)

Starting bot...
🚀 Futures Trading Bot started!
⚠️  PAPER TRADING MODE - No real orders will be executed

📡 Scanning 10 symbols for signals...
🎯 Signal detected: BUY BTCUSDT @ $60,000.00
✅ Position opened: BUY 0.2 BTCUSDT @ $60,000.00 (20x)
```

### Example 2: Check Bot Status

```python
from src.trading.futures import FuturesTradingBot

bot = FuturesTradingBot(mode="paper")
bot.print_status()
```

Output:
```
============================================================
🤖 Futures Trading Bot Status (PAPER mode)
============================================================
Status: 🟢 RUNNING
Symbols: 10
Open Positions: 2
Total Margin: $1,000.00
Total PNL: +$150.00
============================================================

📊 Active Positions:
  ✅ BTCUSDT BUY: $60,500.00 | PNL: $100.00 (+10.00%)
  ✅ ETHUSDT BUY: $3,050.00 | PNL: $50.00 (+5.00%)
============================================================
```

### Example 3: Monitor in Linear

Linear issues are created automatically:

```
[FUTURES-BTCUSDT] 🟢 LONG @ $60,000 (20x)

📊 Entry Details
- Entry Price: $60,000.00
- Position Size: 0.2 BTC
- Leverage: 20x
- Margin Used: $500.00

📈 Current Status
- Current Price: $60,500.00
- PNL: +$100.00 (+10.00%)
- ROI: +20.00% (with leverage)

🎯 Targets
- TP1 (70%): $61,200.00 (S/R level)
- TP2 (30%): $62,000.00 (Fibo 1.618)
- Stop Loss: $57,000.00 (-50% PNL)
```

---

## 🧪 Testing

### Test Individual Modules

```bash
cd /path/to/dna-trading-bot

# Test indicators
python3 -c "from src.trading.futures.indicators import TechnicalIndicators; print('OK')"

# Test Binance connection
python3 -c "from src.trading.futures.binance_futures import BinanceFuturesClient; client = BinanceFuturesClient(); print(client.get_current_price('BTC/USDT'))"

# Test signal detection
python3 -c "from src.trading.futures.signal_detector import SignalDetector; print('OK')"
```

### Paper Trading Test (1 Week)

1. Run bot in paper mode:
```bash
./scripts/trading/start_futures_bot.sh paper
```

2. Monitor for 1 week
3. Review performance in Linear
4. Adjust strategy if needed
5. Then switch to live mode

---

## 📝 Linear Integration

### Auto-Created Issues

When bot opens position:
- Creates Linear issue with title: `[FUTURES-BTCUSDT] 🟢 LONG @ $60,000 (20x)`
- Labels: `futures`, `trading`, `long`/`short`
- Priority: Medium

### Real-time Updates

Every 5 minutes, bot adds comment with:
- Current price
- PNL (USD and %)
- ROI with leverage
- Status (OPEN, PARTIAL_CLOSE, CLOSED)

### Auto-Close

When position closes:
- Adds final comment with results
- Updates issue status to "Done"
- Includes close reason (STOP_LOSS, TP1, TP2, MANUAL)

---

## ⚠️ Risk Warnings

### IMPORTANT

1. **Start with Paper Trading**: Test strategy for at least 1 week before going live
2. **Use Only Risk Capital**: Never trade with money you can't afford to lose
3. **Leverage Amplifies Risk**: 20x leverage means 20x profit BUT also 20x loss
4. **Market Volatility**: Crypto markets are extremely volatile, especially with leverage
5. **Funding Rates**: Futures positions pay funding rates every 8 hours
6. **Liquidation Risk**: High leverage = higher liquidation risk
7. **Bot Failures**: Technical issues can prevent stop loss execution
8. **API Limits**: Binance has rate limits that can affect bot performance

### Risk Management Rules

- ✅ Never risk more than 5% per trade
- ✅ Max 2 positions per symbol
- ✅ Always use stop loss (-50% auto SL)
- ✅ Don't overtrade (wait for quality setups)
- ✅ Monitor funding rates
- ✅ Keep sufficient margin to avoid liquidation
- ✅ Review bot logs daily
- ✅ Start with small position sizes

---

## 🔧 Troubleshooting

### Issue: "Connection failed"

**Solution**: Run on local machine, not Claude sandbox
```bash
# On your local Terminal
cd ~/Documents/Claude\ Work/DNA-Trading-Bot
./scripts/trading/start_futures_bot.sh paper
```

### Issue: "API credentials required"

**Solution**: Add Binance API keys to `.env`
```bash
BINANCE_API_KEY=your_key
BINANCE_SECRET_KEY=your_secret
```

Enable Futures trading on Binance API settings.

### Issue: "Insufficient balance"

**Solution**:
- Paper mode: Bot uses $10,000 virtual balance
- Live mode: Deposit USDT to Binance Futures account

### Issue: "No signals detected"

**Possible reasons**:
1. Market is sideways (no clear H4 trend)
2. RSI filter blocking entries (RSI > 70 for BUY or < 30 for SELL)
3. No valid M15 entry signal (price not testing EMA or wick < 40%)

**Solution**: Be patient, wait for quality setups. Bot is designed to avoid overtrading.

### Issue: "Position not opening"

**Possible reasons**:
1. Already have 2 positions for that symbol
2. Insufficient balance
3. API error

**Solution**: Check bot logs at `logs/futures_bot.log`

---

## 📈 Performance Metrics

Track these metrics in Linear:

- **Win Rate**: % of profitable trades
- **Average Win**: Average profit per winning trade
- **Average Loss**: Average loss per losing trade
- **Risk/Reward Ratio**: Avg Win / Avg Loss
- **Max Drawdown**: Largest loss from peak
- **Sharpe Ratio**: Risk-adjusted returns

---

## 🚧 Roadmap

### Phase 1 (Current)
- [x] Multi-timeframe signal detection
- [x] Position management
- [x] Risk management
- [x] Linear integration
- [x] Paper trading mode

### Phase 2 (Next)
- [ ] Backtesting framework
- [ ] Performance dashboard
- [ ] Telegram alerts
- [ ] Multiple strategy support
- [ ] Advanced order types (limit orders)

### Phase 3 (Future)
- [ ] Machine learning for signal optimization
- [ ] Multi-exchange support
- [ ] Portfolio rebalancing
- [ ] Advanced risk models

---

## 📞 Support

- **Documentation**: This README
- **Logs**: `logs/futures_bot.log`
- **Issues**: Track in Linear
- **Strategy**: See `knowledge/trading_strategies/margin_agent_vsa_wyckoff.md`

---

## ⚖️ Disclaimer

**This bot is for educational purposes only.**

- Trading futures involves substantial risk of loss
- Past performance does not guarantee future results
- Use at your own risk
- Not financial advice
- Always test with paper trading first
- Only trade with money you can afford to lose

**The developers are not responsible for any trading losses.**

---

## 📜 License

MIT License - Use at your own risk.

---

**Built with ❤️ for disciplined futures trading**

*"The trend is your friend, until it bends."*
