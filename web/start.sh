#!/bin/bash
# Start Trading Dashboard (Backend + Frontend)
# Usage: ./web/start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "⚡ Starting Trading Dashboard..."
echo ""

# Start backend
echo "🔧 Starting FastAPI backend on :8000..."
cd "$PROJECT_ROOT"
python3 -m uvicorn web.backend.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir web/backend &
BACKEND_PID=$!

# Start frontend
echo "🎨 Starting Vite frontend on :5173..."
cd "$SCRIPT_DIR/frontend"
npx vite --host 127.0.0.1 &
FRONTEND_PID=$!

echo ""
echo "✅ Dashboard running!"
echo "   Frontend: http://localhost:5173"
echo "   Backend:  http://localhost:8000"
echo "   API Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop both servers"

# Trap Ctrl+C to kill both
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" SIGINT SIGTERM

wait
