@echo off
echo ============================================
echo   Fixing Cloudflare Tunnel Service
echo ============================================
echo.

:: Copy updated config with correct path
echo Copying updated config...
copy "C:\Users\Admin\.cloudflared\config.yml" "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml" /Y
echo.

:: Restart service
echo Starting service...
net start cloudflared
echo.

:: Wait and check
timeout /t 5 /nobreak >nul
echo Checking service status...
sc query cloudflared | findstr STATE
echo.

echo ============================================
echo   URL: https://dash.dnatradingbot.com
echo ============================================
pause
