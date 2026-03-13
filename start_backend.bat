@echo off
REM ============================================
REM EMA-Trading-Bot Backend Launcher
REM ============================================

setlocal enabledelayedexpansion

REM Colors
set "GREEN=[32m"
set "YELLOW=[33m"
set "RED=[31m"
set "CYAN=[36m"
set "NC=[0m"

echo.
echo %GREEN%================================%NC%
echo %GREEN%  EMA-Trading-Bot Backend API%NC%
echo %GREEN%================================%NC%
echo.

REM Check/Set Virtual Environment
set "VENV_PATH=.venv-win"
set "PYTHON_EXE=%VENV_PATH%\Scripts\python.exe"

if exist "%PYTHON_EXE%" (
    echo %CYAN%Using virtual environment: %VENV_PATH%%NC%
) else (
    echo %YELLOW%Virtual environment not found, using system Python...%NC%
    set "PYTHON_EXE=python"
)

REM Run Backend
echo.
echo %GREEN%Starting FastAPI Server...%NC%
echo %YELLOW%Press Ctrl+C to stop%NC%
echo.

"%PYTHON_EXE%" -m web.backend.main

if errorlevel 1 (
    echo.
    echo %RED%Server crashed or failed to start.%NC%
    pause
)
endlocal
