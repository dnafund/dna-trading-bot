#!/bin/bash
# Weekly self-learning cron — runs every Sunday 23:00
# Analyzes live trades + generates report + LLM reflection
#
# Install: ./learning/cron_weekly.sh --install
# Uninstall: ./learning/cron_weekly.sh --uninstall
# Manual run: ./learning/cron_weekly.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${PROJECT_DIR}/learning/output/logs"
PLIST_NAME="com.ema-bot.weekly-learning"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

mkdir -p "$LOG_DIR"

# ── Install/Uninstall ─────────────────────────────────────────

if [[ "${1:-}" == "--install" ]]; then
    cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${PROJECT_DIR}/learning/cron_weekly.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>0</integer>
        <key>Hour</key>
        <integer>23</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/weekly_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/weekly_stderr.log</string>
    <key>WorkingDirectory</key>
    <string>${PROJECT_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
PLIST

    launchctl load "$PLIST_PATH" 2>/dev/null || true
    echo "✅ Installed weekly learning cron (Sunday 23:00)"
    echo "   Plist: $PLIST_PATH"
    echo "   Logs:  $LOG_DIR/"
    exit 0
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✅ Uninstalled weekly learning cron"
    exit 0
fi

# ── Main: Weekly Learning Run ─────────────────────────────────

cd "$PROJECT_DIR"

TIMESTAMP=$(date +"%Y-%m-%d_%H%M")
LOG_FILE="${LOG_DIR}/weekly_${TIMESTAMP}.log"

echo "═══════════════════════════════════════════════════" | tee "$LOG_FILE"
echo "  Weekly Learning Run — $(date)"                     | tee -a "$LOG_FILE"
echo "═══════════════════════════════════════════════════" | tee -a "$LOG_FILE"

# Run weekly analysis with LLM reflection
python3 -m learning.run weekly --days 7 2>&1 | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "  Completed: $(date)" | tee -a "$LOG_FILE"
echo "═══════════════════════════════════════════════════" | tee -a "$LOG_FILE"
