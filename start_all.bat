@echo off
REM ============================================
REM DNA-Trading-Bot - Start All Services
REM ============================================
REM Starts Backend (port 8000) + Frontend dev (port 5174)
REM Close this window to stop all services
REM ============================================

setlocal enabledelayedexpansion

set "GREEN=[32m"
set "YELLOW=[33m"
set "RED=[31m"
set "NC=[0m"

cd /d "%~dp0"

echo.
echo %GREEN%============================================%NC%
echo %GREEN%  DNA-Trading-Bot - Web Dashboard%NC%
echo %GREEN%============================================%NC%
echo.

REM ---- Kill stale processes ----
echo %YELLOW%[1/4] Cleaning up old processes...%NC%
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5174.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
echo %GREEN%       Done%NC%

REM ---- Detect Python ----
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=.venv-win\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM ---- Start Backend ----
echo %YELLOW%[2/4] Starting Backend (port 8000)...%NC%
start "Dashboard-Backend" /MIN %PY% -m web.backend.main
echo %GREEN%       Backend started%NC%

timeout /t 3 /nobreak >nul

REM ---- Build Frontend ----
echo %YELLOW%[3/4] Building Frontend...%NC%
cd web\frontend
call npx vite build >nul 2>&1
if errorlevel 1 (
    echo %RED%       Build failed, using existing build%NC%
) else (
    echo %GREEN%       Build OK%NC%
)
cd /d "%~dp0"

REM ---- Start Frontend dev server ----
echo %YELLOW%[4/4] Starting Frontend (port 5174)...%NC%
echo.
echo %GREEN%  Dashboard: http://localhost:5174%NC%
echo %GREEN%  API:       http://localhost:8000%NC%
echo.

cd web\frontend
npx vite --port 5174 --strictPort

REM ---- Cleanup on exit ----
echo.
echo %YELLOW%Frontend stopped. Cleaning up...%NC%
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
echo %GREEN%All stopped.%NC%
timeout /t 2 /nobreak >nul

endlocal
