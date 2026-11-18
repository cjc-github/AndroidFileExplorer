@echo off
chcp 65001 >nul
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-env.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" ( echo [afe] Environment check failed with code %CODE% ) else ( echo [afe] Environment check passed )
pause
exit /b %CODE%
