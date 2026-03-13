@echo off
REM Production startup script for MyLifeOS Dashboard
REM Builds frontend, then starts FastAPI serving both API + static files

echo [1/2] Building frontend...
cd /d "%~dp0frontend"
call npm run build
if errorlevel 1 (
    echo Frontend build failed!
    pause
    exit /b 1
)

echo [2/2] Starting production server on port 8000...
cd /d "%~dp0.."
python -m uvicorn web.backend.main:app --host 0.0.0.0 --port 8000
