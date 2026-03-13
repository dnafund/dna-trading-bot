# ============================================
# DNA-Trading-Bot Backend Launcher (PowerShell)
# ============================================

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "  DNA-Trading-Bot Backend API" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Get script directory and project root
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# If script is in root, ProjectRoot is ScriptDir
if (Test-Path "$ScriptDir\web\backend\main.py") {
    $ProjectRoot = $ScriptDir
}
else {
    # If script is in scripts/ or scripts/trading/
    $ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
    if (-not (Test-Path "$ProjectRoot\web\backend\main.py")) {
        $ProjectRoot = Split-Path -Parent $ScriptDir
    }
}

# Ensure we are in project root
Set-Location $ProjectRoot
Write-Host "[INFO] Project Root: $ProjectRoot" -ForegroundColor Cyan

# Check/Set Virtual Environment
$VenvPath = ".venv-win"
$PythonExe = "$VenvPath\Scripts\python.exe"
$PipExe = "$VenvPath\Scripts\pip.exe"

if (Test-Path $PythonExe) {
    Write-Host "[INFO] Using virtual environment: $VenvPath" -ForegroundColor Cyan
}
else {
    Write-Host "[WARN] Virtual environment not found, using system Python..." -ForegroundColor Yellow
    $PythonExe = "python"
    $PipExe = "pip"
}

# Check dependencies
Write-Host ""
Write-Host "[CHECK] Verifying dependencies..." -ForegroundColor Cyan
try {
    & $PythonExe -c "import fastapi, uvicorn; print('OK')" 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Missing dependencies" }
    Write-Host "[OK] Dependencies verified" -ForegroundColor Green
}
catch {
    Write-Host "[WARN] Missing dependencies. Installing..." -ForegroundColor Yellow
    if (Test-Path "requirements.txt") {
        & $PipExe install -r requirements.txt
    }
    else {
        & $PipExe install fastapi uvicorn
    }
}

# Run Backend
Write-Host ""
Write-Host "[START] Starting FastAPI Server..." -ForegroundColor Green
Write-Host "        Press Ctrl+C to stop" -ForegroundColor Yellow
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan

try {
    & $PythonExe -m web.backend.main
}
catch {
    Write-Host ""
    Write-Host "[ERROR] Server crashed or failed to start: $_" -ForegroundColor Red
}
finally {
    Write-Host ""
    Write-Host "========================================" -ForegroundColor Cyan
    Write-Host "[STOP] Backend server stopped" -ForegroundColor Yellow
}
