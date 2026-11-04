@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PS1=%~dp0tools\daily-restart\restart-server.ps1"
if not exist "%PS1%" (
  echo [ERROR] Missing "%PS1%"
  pause
  exit /b 1
)

echo Restarting Infinite Canvas service...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -NoBrowser %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Restarted. Open http://127.0.0.1:3000/ if you need the page.
) else (
  echo Restart failed with exit code %RC%.
  echo See logs\daily_restart.log for details.
)
echo.
pause
exit /b %RC%
