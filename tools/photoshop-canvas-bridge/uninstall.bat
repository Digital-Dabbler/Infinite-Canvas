@echo off
setlocal
chcp 65001 >nul
set "TARGET=%APPDATA%\Adobe\CEP\extensions\com.daxiong.infinitecanvas.bridge"
if exist "%TARGET%" (
    rmdir /S /Q "%TARGET%"
    echo Infinite Canvas Bridge removed.
) else (
    echo Infinite Canvas Bridge is not installed.
)
echo PlayerDebugMode registry values were left unchanged because other CEP extensions may use them.
pause
