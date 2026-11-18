@echo off
chcp 65001 >nul
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" stop
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" ( pause )
exit /b %CODE%
