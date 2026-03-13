@echo off
echo ============================================
echo   Installing Cloudflare Tunnel as Service
echo ============================================
echo.

:: Step 1: Stop and uninstall existing service
echo Removing old service if exists...
net stop cloudflared 2>nul
"C:\Program Files (x86)\cloudflared\cloudflared.exe" service uninstall 2>nul
timeout /t 3 /nobreak >nul
echo.

:: Step 2: Copy config to system-level location
echo Copying config files...
if not exist "C:\Windows\System32\config\systemprofile\.cloudflared" (
    mkdir "C:\Windows\System32\config\systemprofile\.cloudflared"
)

copy "C:\Users\Admin\.cloudflared\config.yml" "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml" /Y
copy "C:\Users\Admin\.cloudflared\817b651f-9a83-4ac5-bb5f-7ab36a21d739.json" "C:\Windows\System32\config\systemprofile\.cloudflared\817b651f-9a83-4ac5-bb5f-7ab36a21d739.json" /Y
copy "C:\Users\Admin\.cloudflared\cert.pem" "C:\Windows\System32\config\systemprofile\.cloudflared\cert.pem" /Y

echo.
echo Config files copied to system profile.
echo.

:: Step 3: Install service fresh
echo Installing cloudflared service...
"C:\Program Files (x86)\cloudflared\cloudflared.exe" service install
echo.

:: Step 4: Start service
echo Starting service...
net start cloudflared
echo.

:: Step 5: Verify
echo Checking service status...
sc query cloudflared | findstr STATE
echo.

echo ============================================
echo   Done! Tunnel service installed.
echo   URL: https://dash.dnatradingbot.com
echo ============================================
pause
