@echo off
rem AndroidFileExplorer dev launcher - run backend + frontend in the foreground.
rem Ctrl+C stops both services; logs stream straight to this console.
rem Also accepts "dev.bat backend" or "dev.bat frontend" for single-service mode.
rem Implementation lives in dev.ps1; this is the native double-click entry.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [afe] Command exited with code %CODE%. Check logs\ for details.
  pause >nul
)
endlocal & exit /b %CODE%
