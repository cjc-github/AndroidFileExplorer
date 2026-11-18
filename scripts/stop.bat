@echo off
rem AndroidFileExplorer stopper - double-click to stop backend + frontend.
rem Delegates to start.ps1 stop (PID-file kill + process-tree /T /F + scoped fallback).
rem Pauses only on failure so the error stays visible.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" stop
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" ( pause )
endlocal & exit /b %CODE%
