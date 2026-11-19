@echo off
rem AndroidFileExplorer stopper (Windows, pure batch - no PowerShell).
rem Double-click to stop backend :8010 + frontend :10012.
rem Kill order: PID files -> window-title match -> port-owner fallback that only
rem touches python.exe / pythonw.exe / node.exe still listening on our ports,
rem so an unrelated program on the same port is never killed blindly.
rem Pauses only on failure so the error stays visible.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
call "%~dp0start.bat" stop nopause
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" pause
endlocal & exit /b %CODE%
