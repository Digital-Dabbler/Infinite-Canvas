@echo off
setlocal
chcp 65001 >nul
set "SOURCE=%~dp0"
set "TARGET=%APPDATA%\Adobe\CEP\extensions\com.daxiong.infinitecanvas.bridge"

tasklist /FI "IMAGENAME eq Photoshop.exe" 2>nul | find /I "Photoshop.exe" >nul
if not errorlevel 1 (
    echo Photoshop is still running.
    echo Close every Photoshop window and end all Photoshop.exe processes, then run this installer again.
    pause
    exit /b 2
)

echo Installing Infinite Canvas Bridge 0.2.3...
if not exist "%TARGET%" mkdir "%TARGET%"
xcopy "%SOURCE%*" "%TARGET%\" /E /I /Y /Q >nul
if errorlevel 1 (
    echo Installation failed. Check access to: %TARGET%
    pause
    exit /b 1
)
if not exist "%TARGET%\CSXS\manifest.xml" (
    echo Installation is incomplete: CSXS\manifest.xml was not copied.
    pause
    exit /b 1
)

for %%V in (7 8 9 10 11 12) do (
    reg add "HKCU\Software\Adobe\CSXS.%%V" /v PlayerDebugMode /t REG_SZ /d 1 /f >nul
)

echo.
echo Installed to:
echo %TARGET%
echo.
echo Restart Photoshop, then open:
echo Window ^> Extensions ^> Infinite Canvas Bridge
echo or Window ^> Extensions (Legacy) ^> Infinite Canvas Bridge
echo In Photoshop CC 2018 Chinese UI, use Window ^> Extensions.
echo.
pause
