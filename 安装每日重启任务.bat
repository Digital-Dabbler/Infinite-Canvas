@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PS1=%~dp0tools\daily-restart\install-task.ps1"
if not exist "%PS1%" (
  echo [ERROR] Missing "%PS1%"
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
