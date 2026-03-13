@echo off
REM ============================================
REM Futures Trading Bot - Windows Batch Launcher
REM ============================================

setlocal enabledelayedexpansion

REM Colors (Windows 10+)
set "GREEN=[32m"
set "YELLOW=[33m"
set "RED=[31m"
set "CYAN=[36m"
set "NC=[0m"

echo.
echo %GREEN%================================%NC%
echo %GREEN%  Futures Trading Bot%NC%
echo %GREEN%================================%NC%
echo.

REM Get mode from argument (default: paper)
set "MODE=%~1"
if "%MODE%"=="" set "MODE=paper"

REM Validate mode
if not "%MODE%"=="paper" if not "%MODE%"=="live" (
    echo %RED%Error: Invalid mode. Use 'paper' or 'live'%NC%
    echo Usage: start_futures_bot.bat [paper^|live]
    exit /b 1
)

REM Change to project root
cd /d "%~dp0..\.."
echo %CYAN%Project: %CD%%NC%

REM Check if src/trading/futures exists
if not exist "src\trading\futures" (
    echo %RED%Error: src\trading\futures not found!%NC%
    echo Make sure you're in the DNA-Trading-Bot directory
    exit /b 1
)

REM Check .env file
if not exist ".env" (
    echo %RED%Error: .env file not found!%NC%
    echo.
    echo %YELLOW%Create .env with:%NC%
    echo   BINANCE_API_KEY=your_key
    echo   BINANCE_SECRET_KEY=your_secret
    echo   LINEAR_API_KEY=your_linear_key
    exit /b 1
)

REM Check/Create virtual environment
set "VENV_PATH=.venv-win"
set "PYTHON_EXE=%VENV_PATH%\Scripts\python.exe"
set "PIP_EXE=%VENV_PATH%\Scripts\pip.exe"

if not exist "%VENV_PATH%" (
    echo.
    echo %YELLOW%Creating virtual environment...%NC%
    python -m venv %VENV_PATH%

    if not exist "%PYTHON_EXE%" (
        echo %RED%Failed to create virtual environment!%NC%
        echo Make sure Python is installed and in PATH
        exit /b 1
    )

    echo %GREEN%Virtual environment created%NC%

    REM Install dependencies
    echo.
    echo %YELLOW%Installing dependencies...%NC%
    "%PIP_EXE%" install -r requirements.txt

    if errorlevel 1 (
        echo %RED%Failed to install dependencies!%NC%
        exit /b 1
    )

    echo %GREEN%Dependencies installed%NC%
)

REM Create logs directory
if not exist "logs" mkdir logs

REM Mode confirmation
echo.
if "%MODE%"=="live" (
    echo %RED%WARNING: LIVE TRADING MODE%NC%
    echo %RED%Real money will be used!%NC%
    echo.
    set /p "CONFIRM=Are you sure? Type 'YES' to continue: "
    if not "!CONFIRM!"=="YES" (
        echo Cancelled.
        exit /b 0
    )
) else (
    echo %YELLOW%Paper Trading Mode%NC%
    echo %CYAN%No real money - Virtual $10,000 balance%NC%
)

REM Set environment variable
set "TRADING_MODE=%MODE%"

REM Display settings
echo.
echo %CYAN%Settings:%NC%
echo   Mode: %MODE%
echo   Symbols: BTCUSDT, ETHUSDT, SOLUSDT, ...
echo   Strategy: H4 trend - H1 filter - M15 entry
echo.

REM Run bot
echo %GREEN%Starting Futures Trading Bot...%NC%
echo %YELLOW%Press Ctrl+C to stop%NC%
echo.
echo ========================================

"%PYTHON_EXE%" -m src.trading.futures.futures_bot

echo.
echo ========================================
echo %YELLOW%Futures Trading Bot stopped%NC%

endlocal
