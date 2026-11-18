@echo off
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
