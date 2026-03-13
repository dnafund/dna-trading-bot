@echo off
echo ============================================
echo   Setup Cloudflare Tunnel Auto-Start
echo ============================================
echo.

:: Remove old service if exists
echo Removing old service...
net stop cloudflared 2>nul
"C:\Program Files (x86)\cloudflared\cloudflared.exe" service uninstall 2>nul
echo.

:: Delete old scheduled task if exists
schtasks /Delete /TN "CloudflareTunnel" /F 2>nul

:: Create scheduled task to run at boot under Admin user
echo Creating scheduled task...
schtasks /Create /TN "CloudflareTunnel" /TR "\"C:\Program Files (x86)\cloudflared\cloudflared.exe\" --config \"C:\Users\Admin\.cloudflared\config.yml\" tunnel run trading-dashboard" /SC ONSTART /RU Admin /RL HIGHEST /DELAY 0000:30
echo.

:: Fix config back to user path
echo Updating config...
echo tunnel: 817b651f-9a83-4ac5-bb5f-7ab36a21d739> "C:\Users\Admin\.cloudflared\config.yml"
echo credentials-file: C:\Users\Admin\.cloudflared\817b651f-9a83-4ac5-bb5f-7ab36a21d739.json>> "C:\Users\Admin\.cloudflared\config.yml"
echo.>> "C:\Users\Admin\.cloudflared\config.yml"
echo ingress:>> "C:\Users\Admin\.cloudflared\config.yml"
echo   - hostname: dash.dnatradingbot.com>> "C:\Users\Admin\.cloudflared\config.yml"
echo     service: http://localhost:8000>> "C:\Users\Admin\.cloudflared\config.yml"
echo   - service: http_status:404>> "C:\Users\Admin\.cloudflared\config.yml"

echo.
echo Starting tunnel now...
schtasks /Run /TN "CloudflareTunnel"

timeout /t 5 /nobreak >nul
echo.
echo Checking if tunnel is running...
tasklist /FI "IMAGENAME eq cloudflared.exe" | findstr cloudflared
echo.

echo ============================================
echo   Done! Tunnel will auto-start on boot.
echo   URL: https://dash.dnatradingbot.com
echo ============================================
pause
