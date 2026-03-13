@echo off
REM ============================================
REM EMA-Trading-Bot - Start Web Dashboard
REM ============================================
REM Starts Cloudflare Tunnel + Backend (port 8000) + Frontend (port 5174)
REM Close this window to stop all services
REM ============================================

setlocal enabledelayedexpansion

set "GREEN=[32m"
set "YELLOW=[33m"
set "RED=[31m"
set "CYAN=[36m"
set "NC=[0m"

cd /d "%~dp0"

echo.
echo %GREEN%============================================%NC%
echo %GREEN%  EMA-Trading-Bot - Web Dashboard%NC%
echo %GREEN%============================================%NC%
echo.

REM ---- Kill stale processes ----
echo %YELLOW%[1/6] Cleaning up old processes...%NC%
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5174.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq Cloudflare-Tunnel*" /F >nul 2>&1
echo %GREEN%       Done%NC%

REM ---- Start Cloudflare Tunnel ----
echo %YELLOW%[2/6] Starting Cloudflare Tunnel...%NC%
set "CF=cloudflared"
where cloudflared >nul 2>&1
if not errorlevel 1 goto :cf_found
if exist "C:\Program Files (x86)\cloudflared\cloudflared.exe" (
    set "CF=C:\Program Files (x86)\cloudflared\cloudflared.exe"
    goto :cf_found
)
echo %RED%       cloudflared not found, skipping tunnel%NC%
goto :tunnel_done
:cf_found
start "Cloudflare-Tunnel" /MIN "%CF%" tunnel run
echo %GREEN%       Tunnel started (dnatradingbot.com)%NC%
:tunnel_done

REM ---- Detect Python ----
set "PY=.venv-win\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM ---- Start Backend in separate window ----
echo %YELLOW%[3/6] Starting Backend (port 8000)...%NC%
start "Dashboard-Backend" /MIN %PY% -m web.backend.main
echo %GREEN%       Backend started%NC%

REM ---- Wait for backend to be ready ----
timeout /t 3 /nobreak >nul

REM ---- Build Frontend for dnatradingbot.com ----
echo %YELLOW%[4/6] Building Frontend for dnatradingbot.com...%NC%
cd web\frontend
call npx vite build >nul 2>&1
if errorlevel 1 goto :build_fail
echo %GREEN%       Build OK (dist updated)%NC%
goto :build_done
:build_fail
echo %RED%       Build failed, dnatradingbot.com will use old build%NC%
:build_done
cd /d "%~dp0"

REM ---- Start auto-rebuild watcher ----
echo %YELLOW%       Starting auto-rebuild watcher...%NC%
start "Build-Watcher" /MIN "%~dp0web\frontend\watch-build.bat"
echo %GREEN%       Watcher started (auto-rebuilds on code changes)%NC%

REM ---- Start Frontend dev server ----
echo %YELLOW%[5/6] Starting Frontend dev server (port 5174)...%NC%
echo.

timeout /t 3 /nobreak >nul

echo %YELLOW%[6/6] All services started%NC%
echo.
echo %GREEN%  Dashboard: http://localhost:5174%NC%
echo %GREEN%  Public:    https://dnatradingbot.com%NC%
echo %GREEN%  Backend + Tunnel + Watcher minimized in taskbar%NC%
echo.

cd web\frontend
npx vite --port 5174 --strictPort

REM ---- If frontend exits, cleanup everything ----
echo.
echo %YELLOW%Frontend stopped. Cleaning up...%NC%
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000.*LISTENING" 2^>nul') do (
    taskkill /PID %%p /F >nul 2>&1
)
taskkill /FI "WINDOWTITLE eq Cloudflare-Tunnel*" /F >nul 2>&1
echo %GREEN%All stopped (backend + tunnel).%NC%
timeout /t 2 /nobreak >nul

endlocal
