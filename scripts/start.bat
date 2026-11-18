@echo off
rem AndroidFileExplorer launcher - double-click to start backend :8010 + frontend :10012.
rem Heavy lifting (env check, detached spawn, port wait, PID files) lives in start.ps1;
rem this batch is only the native double-click entry with UTF-8 console setup.
rem Usage: start.bat [start|stop|status|restart] [-Tail]
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo [afe] Command exited with code %CODE%. Check logs\ for details.
  pause >nul
)
endlocal & exit /b %CODE%
