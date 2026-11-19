@echo off
rem AndroidFileExplorer dev launcher (Windows, pure batch - no PowerShell).
rem Runs backend + frontend in the foreground so their logs stream straight to a console.
rem
rem Usage: dev.bat [all^|backend^|frontend]
rem   dev.bat            backend + frontend (default)
rem   dev.bat backend    backend only (skips the Node check)
rem   dev.bat frontend   frontend only
rem
rem Each service runs in its own titled console window, in the foreground:
rem Ctrl+C in that window stops that service, closing the window kills its whole
rem process tree. Nothing is detached and no PID files are written - use start.bat
rem for background/detached running.
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PY=%VENV_PY%"
if not defined PY set "PY=%ROOT%\.venv\Scripts\python.exe"
set "BPORT=%SERVER_PORT%"
if not defined BPORT set "BPORT=8010"
set "FPORT=%FRONTEND_PORT%"
if not defined FPORT set "FPORT=10012"
set "HOST=%BIND_HOST%"
if not defined HOST set "HOST=0.0.0.0"
set "DHOST=%HOST%"
if "%HOST%"=="0.0.0.0" set "DHOST=127.0.0.1"

set "MODE=%~1"
if not defined MODE set "MODE=all"
if /I "%MODE%"=="all"      goto :CHECK
if /I "%MODE%"=="backend"  goto :CHECK
if /I "%MODE%"=="frontend" goto :CHECK
echo [error] 未知模式：%MODE%（可选 all / backend / frontend）
set "CODE=1"
goto :FINISH

rem ---------- 环境自检（仅后端模式跳过 Node 检查）----------
:CHECK
if /I "%MODE%"=="backend" (
  call "%~dp0check-env.bat" backend nopause
) else (
  call "%~dp0check-env.bat" nopause
)
if errorlevel 1 (
  echo [error] 环境自检未通过，启动中止。
  set "CODE=1"
  goto :FINISH
)

rem ---------- 后端 ----------
if /I "%MODE%"=="frontend" goto :FRONTEND
if not exist "%PY%" (
  echo [error] 未找到 Python：%PY%
  echo        先执行 scripts\setup.bat 创建 .venv
  set "CODE=1"
  goto :FINISH
)
echo [afe] 后端 http://%DHOST%:%BPORT%  (docs: /docs)
rem 写临时辅助 bat 再 start：避免在 start 参数里嵌套引号（路径含空格时会断）。
rem 窗口关闭 = cmd /k 结束 = 整棵进程树结束。
set "BHELPER=%TEMP%\afe-dev-backend.bat"
>"%BHELPER%" echo @echo off
>>"%BHELPER%" echo chcp 65001 ^>nul
>>"%BHELPER%" echo set "PYTHONIOENCODING=utf-8"
>>"%BHELPER%" echo set "SERVER_HOST=%HOST%"
>>"%BHELPER%" echo set "SERVER_PORT=%BPORT%"
>>"%BHELPER%" echo cd /d "%ROOT%\backend"
>>"%BHELPER%" echo "%PY%" -m app.main
start "AFE-Backend (dev)" cmd /k "%BHELPER%"
if /I "%MODE%"=="backend" goto :DONE

rem ---------- 前端 ----------
:FRONTEND
echo [afe] 前端 http://%DHOST%:%FPORT%
if not exist "%ROOT%\frontend\node_modules" (
  echo [afe] 前端依赖缺失，先执行 npm install …
  pushd "%ROOT%\frontend"
  call npm install
  popd
)
set "FHELPER=%TEMP%\afe-dev-frontend.bat"
>"%FHELPER%" echo @echo off
>>"%FHELPER%" echo chcp 65001 ^>nul
>>"%FHELPER%" echo cd /d "%ROOT%\frontend"
>>"%FHELPER%" echo call npm run dev -- --port %FPORT% --host %HOST%
start "AFE-Frontend (dev)" cmd /k "%FHELPER%"

:DONE
echo.
if /I "%MODE%"=="frontend" goto :DONE_FE
echo [afe] 后端地址: http://%DHOST%:%BPORT%   (API 文档 %DHOST%:%BPORT%/docs)
:DONE_FE
if /I "%MODE%"=="backend" goto :DONE_TIP
echo [afe] 前端地址: http://%DHOST%:%FPORT%
:DONE_TIP
echo [afe] 各服务在独立窗口中前台运行；在该窗口按 Ctrl+C 或直接关闭窗口即停止对应服务。
echo [afe] 需要后台常驻请改用 scripts\start.bat。
set "CODE=0"

:FINISH
if not "%CODE%"=="0" pause
endlocal & exit /b %CODE%
