@echo off
echo ============================================
echo   Debug Cloudflare Tunnel
echo ============================================
echo.

echo Testing tunnel run directly...
echo.
"C:\Program Files (x86)\cloudflared\cloudflared.exe" --config "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml" tunnel run trading-dashboard
echo.
pause
