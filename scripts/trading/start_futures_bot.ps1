# ============================================
# Futures Trading Bot - PowerShell Launcher
# ============================================

param(
    [Parameter(Position=0)]
    [ValidateSet("paper", "live")]
    [string]$Mode = "paper"
)

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "  FUTURES TRADING BOT" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Get script directory and project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)

# Change to project root
Set-Location $ProjectRoot
Write-Host "[INFO] Project: $ProjectRoot" -ForegroundColor Cyan

# Check if src/trading/futures exists
if (-not (Test-Path "src\trading\futures")) {
    Write-Host "[ERROR] src\trading\futures not found!" -ForegroundColor Red
    Write-Host "        Make sure you are in the DNA-Trading-Bot directory" -ForegroundColor Yellow
    exit 1
}

# Check .env file
if (-not (Test-Path ".env")) {
    Write-Host "[ERROR] .env file not found!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Create .env with:" -ForegroundColor Yellow
    Write-Host "  BINANCE_API_KEY=your_key (optional for paper trading)"
    Write-Host "  BINANCE_SECRET_KEY=your_secret (optional for paper trading)"
    Write-Host "  LINEAR_API_KEY=your_linear_key"
    Write-Host "  LINEAR_TEAM_ID=your_team_id"
    exit 1
}

# Check/Create virtual environment
$VenvPath = ".venv-win"
$PythonExe = "$VenvPath\Scripts\python.exe"
$PipExe = "$VenvPath\Scripts\pip.exe"

if (-not (Test-Path $VenvPath)) {
    Write-Host ""
    Write-Host "[SETUP] Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $VenvPath

    if (-not (Test-Path $PythonExe)) {
        Write-Host "[ERROR] Failed to create virtual environment!" -ForegroundColor Red
        Write-Host "        Make sure Python is installed and in PATH" -ForegroundColor Yellow
        exit 1
    }

    Write-Host "[OK] Virtual environment created" -ForegroundColor Green

    # Install dependencies
    Write-Host ""
    Write-Host "[SETUP] Installing dependencies..." -ForegroundColor Yellow
    & $PipExe install -r requirements.txt

    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install dependencies!" -ForegroundColor Red
        exit 1
    }

    Write-Host "[OK] Dependencies installed" -ForegroundColor Green
}

# Check dependencies
Write-Host ""
Write-Host "[CHECK] Verifying dependencies..." -ForegroundColor Cyan
$CheckResult = & $PythonExe -c "import ccxt, pandas, dotenv; print('OK')" 2>&1

if ($CheckResult -ne "OK") {
    Write-Host "[WARN] Missing dependencies. Installing..." -ForegroundColor Yellow
    & $PipExe install -r requirements.txt
}
Write-Host "[OK] Dependencies verified" -ForegroundColor Green

# Create logs directory
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}

# Mode confirmation
Write-Host ""
if ($Mode -eq "live") {
    Write-Host "!!! WARNING: LIVE TRADING MODE !!!" -ForegroundColor Red
    Write-Host "    Real money will be used!" -ForegroundColor Red
    Write-Host ""
    $Confirm = Read-Host "Are you sure? Type 'YES' to continue"
    if ($Confirm -ne "YES") {
        Write-Host "Cancelled." -ForegroundColor Yellow
        exit 0
    }
}
else {
    Write-Host "[MODE] Paper Trading" -ForegroundColor Yellow
    Write-Host "       No real money - Virtual 10,000 USD balance" -ForegroundColor Cyan
}

# Set environment variable
$env:TRADING_MODE = $Mode

# Display settings
Write-Host ""
Write-Host "[CONFIG] Settings:" -ForegroundColor Cyan
Write-Host "         Mode: $Mode"
Write-Host "         Symbols: BTCUSDT, ETHUSDT, SOLUSDT, ..."
Write-Host "         Strategy: H4 trend -> H1 filter -> M15 entry"
Write-Host ""

# Run bot
Write-Host "[START] Launching Futures Trading Bot..." -ForegroundColor Green
Write-Host "        Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

try {
    & $PythonExe -m src.trading.futures.futures_bot
}
catch {
    Write-Host ""
    Write-Host "Bot stopped." -ForegroundColor Yellow
}
finally {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "[STOP] Futures Trading Bot stopped" -ForegroundColor Yellow
}
