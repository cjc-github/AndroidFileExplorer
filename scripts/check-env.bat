@echo off
rem AndroidFileExplorer environment self-check - Python / deps / Node / adb.
rem Prints [ok] / [warn] / [fail] per item; exit code 1 when any [fail] is present.
rem Read-only and safe to run anytime. Implementation lives in check-env.ps1.
rem Always pauses so double-click users can read the output.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check-env.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" ( echo [afe] Environment check failed with code %CODE% ) else ( echo [afe] Environment check passed )
pause
endlocal & exit /b %CODE%
