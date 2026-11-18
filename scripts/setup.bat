@echo off
rem AndroidFileExplorer first-time setup - creates .venv, installs backend + frontend deps.
rem Built-in guards: aborts if services are running (esbuild.exe file lock), wipes stale
rem node_modules before install, validates vite/esbuild files after install.
rem Implementation lives in setup.ps1; pip falls back to mirrors when the default
rem index fails with TLS errors. Always pauses so double-click users can read the output.
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" ( echo [afe] Setup failed with code %CODE% ) else ( echo [afe] Setup finished )
pause
endlocal & exit /b %CODE%
