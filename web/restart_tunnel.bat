@echo off
echo Restarting Cloudflare Tunnel...
taskkill /F /IM cloudflared.exe 2>nul
timeout /t 2 /nobreak >nul
schtasks /Run /TN "CloudflareTunnel"
timeout /t 5 /nobreak >nul
echo.
echo Testing...
tasklist /FI "IMAGENAME eq cloudflared.exe" | findstr cloudflared
echo.
echo URL: https://dnatradingbot.com
pause
