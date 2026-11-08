@echo off
title Infinite Canvas
cd /d "%~dp0"

rem Thin launcher shell. Foreground server mode:
rem   arg "run" OR IC_NO_BROWSER defined  -> run the server in THIS window.
rem   "run" is passed by the menu launcher and by main.py schedule_self_restart().
rem   IC_NO_BROWSER is set by tools\daily-restart (daily 06:00 task): silent, no pause.
rem Otherwise show the interactive menu. The Chinese menu UI lives in
rem tools\launcher\launcher.ps1 because this batch file must stay pure ASCII.
if /i "%~1"=="run" goto :run
if defined IC_NO_BROWSER goto :run

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\launcher\launcher.ps1"
exit /b %ERRORLEVEL%

:run
set "PYEXE=%~dp0python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

set "LAN_IP=127.0.0.1"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ip=(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress; if(-not $ip){$ip=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress)}; if($ip){$ip}else{'127.0.0.1'}"`) do set "LAN_IP=%%I"
set "APP_URL=http://%LAN_IP%:3000/"

echo Starting ComfyUI-API-Modelscope...
echo Visit: %APP_URL%
echo Local: http://127.0.0.1:3000/
echo Press Ctrl+C to stop.
echo.

rem Daily restart (tools\daily-restart) sets IC_NO_BROWSER=1 to start silently.
if not defined IC_NO_BROWSER start "" /b powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process '%APP_URL%'"
"%PYEXE%" main.py

echo.
echo Server stopped.
rem Daily restart starts the server with IC_NO_BROWSER=1: no pause, so the window closes by itself.
if not defined IC_NO_BROWSER pause
