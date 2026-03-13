#!/bin/bash
# Start Futures Trading Bot

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}🤖 Futures Trading Bot${NC}"
echo -e "${GREEN}================================${NC}"
echo ""

# Check if in correct directory
if [ ! -d "src/trading/futures" ]; then
    echo -e "${RED}Error: Must run from DNA-Trading-Bot root directory${NC}"
    exit 1
fi

# Check if .env exists
if [ ! -f ".env" ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Create .env with:"
    echo "  BINANCE_API_KEY=your_key"
    echo "  BINANCE_SECRET_KEY=your_secret"
    echo "  LINEAR_API_KEY=your_linear_key"
    exit 1
fi

# Check mode
MODE=${1:-paper}

if [ "$MODE" != "paper" ] && [ "$MODE" != "live" ]; then
    echo -e "${RED}Error: Invalid mode. Use 'paper' or 'live'${NC}"
    echo "Usage: ./start_futures_bot.sh [paper|live]"
    exit 1
fi

if [ "$MODE" == "live" ]; then
    echo -e "${RED}⚠️  WARNING: LIVE TRADING MODE${NC}"
    echo -e "${RED}Real money will be used!${NC}"
    echo ""
    read -p "Are you sure? Type 'YES' to continue: " confirm
    if [ "$confirm" != "YES" ]; then
        echo "Cancelled"
        exit 0
    fi
else
    echo -e "${YELLOW}📝 Paper trading mode (no real money)${NC}"
fi

echo ""

# Create logs directory if not exists
mkdir -p logs

# Set mode in environment
export TRADING_MODE=$MODE

# Run bot
echo -e "${GREEN}Starting bot...${NC}"
python3 -m src.trading.futures.futures_bot

# Note: Use Ctrl+C to stop
