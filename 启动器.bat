@echo off
setlocal
rem Visual launcher entry for Infinite Canvas (ASCII only).
rem Starts the local console (tools\launcher\launcher.py) without a console window;
rem launcher.py opens its own native WebView2 window (tools\launcher\window.py).
cd /d "%~dp0"
set "PYW=%~dp0python\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0python\python.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0tools\launcher\launcher.py"
