@echo off
chcp 65001 >nul
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" ( echo [afe] Setup failed with code %CODE% ) else ( echo [afe] Setup finished )
pause
exit /b %CODE%
