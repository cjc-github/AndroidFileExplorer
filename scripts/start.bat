@echo off
rem AndroidFileExplorer launcher (Windows, pure batch - no PowerShell).
rem Starts backend :8010 + frontend :10012 detached, logs into logs\, PID in logs\*.pid,
rem and waits until both ports are actually listening before returning.
rem
rem Usage: start.bat [start^|stop^|status^|restart] [-tail]
rem   start.bat            start both services (default)
rem   start.bat stop       stop both services
rem   start.bat status     show PID / listening state
rem   start.bat restart    stop then start
rem   start.bat -tail      keep re-printing both logs (Ctrl+C exits the view only)
rem
rem Overridable via environment: SERVER_PORT / FRONTEND_PORT / BIND_HOST / VENV_PY
rem Note: the vite /api proxy in frontend\vite.config.ts is pinned to 127.0.0.1:8010,
rem so change that proxy too if you move SERVER_PORT.
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "CODE=0"
set "LOGDIR=%ROOT%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1

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

rem ---------- 参数解析 ----------
set "ACTION=start"
set "TAILFLAG="
set "NOPAUSE="
rem 无参数 = 双击启动：结束时保留窗口，便于阅读输出
set "INTERACTIVE="
if "%~1"=="" set "INTERACTIVE=1"
:ARGS
if "%~1"=="" goto :ARGS_DONE
if /I "%~1"=="start"    set "ACTION=start"
if /I "%~1"=="stop"     set "ACTION=stop"
if /I "%~1"=="status"   set "ACTION=status"
if /I "%~1"=="restart"  set "ACTION=restart"
if /I "%~1"=="tail"     set "TAILFLAG=1"
if /I "%~1"=="-tail"    set "TAILFLAG=1"
if /I "%~1"=="/tail"    set "TAILFLAG=1"
if /I "%~1"=="nopause"  set "NOPAUSE=1"
if /I "%~1"=="-nopause" set "NOPAUSE=1"
shift
goto :ARGS

:ARGS_DONE
if /I "%ACTION%"=="stop"   goto :DO_STOP
if /I "%ACTION%"=="status" goto :DO_STATUS
if /I "%ACTION%"=="restart" goto :DO_RESTART
goto :DO_START

rem ============================================================
rem  主流程
rem ============================================================
:DO_RESTART
rem :DO_START 自身会先停旧的，这里不再重复停止
:DO_START
rem ---- 环境自检（与服务相同的 nopause 模式，不额外暂停）----
call "%~dp0check-env.bat" nopause
if errorlevel 1 (
  echo [error] 环境自检未通过，启动中止。
  set "CODE=1"
  goto :FINISH
)

rem ---- 先停旧的，避免端口占用 ----
call :STOP_SERVICES

rem ---- 后端 ----
echo [afe] 启动后端 :%BPORT% (bind %HOST%)
if not exist "%PY%" (
  echo [error] 未找到 Python：%PY%
  echo        先执行 scripts\setup.bat 创建 .venv
  set "CODE=1"
  goto :FINISH
)
rem 环境变量直接写在辅助 bat 里，不改动本进程的环境
rem 用临时辅助 bat 启动，避免在 start 参数里嵌套引号（路径含空格时会断）
>"%TEMP%\afe-run-backend.bat" echo @echo off
>>"%TEMP%\afe-run-backend.bat" echo set "PYTHONIOENCODING=utf-8"
>>"%TEMP%\afe-run-backend.bat" echo set "SERVER_HOST=%HOST%"
>>"%TEMP%\afe-run-backend.bat" echo set "SERVER_PORT=%BPORT%"
>>"%TEMP%\afe-run-backend.bat" echo cd /d "%ROOT%\backend"
>>"%TEMP%\afe-run-backend.bat" echo "%PY%" -m app.main 1^>^> "%LOGDIR%\backend.log" 2^>^> "%LOGDIR%\backend.err.log"
start "AFE-Backend" /min cmd /c "%TEMP%\afe-run-backend.bat"

rem ---- 前端 ----
echo [afe] 启动前端 :%FPORT% (bind %HOST%)
if not exist "%ROOT%\frontend\node_modules" (
  echo [afe] 前端依赖缺失，先执行 npm install …
  pushd "%ROOT%\frontend"
  call npm install
  popd
)
>"%TEMP%\afe-run-frontend.bat" echo @echo off
>>"%TEMP%\afe-run-frontend.bat" echo cd /d "%ROOT%\frontend"
>>"%TEMP%\afe-run-frontend.bat" echo call npm run dev -- --port %FPORT% --host %HOST% 1^>^> "%LOGDIR%\frontend.log" 2^>^> "%LOGDIR%\frontend.err.log"
start "AFE-Frontend" /min cmd /c "%TEMP%\afe-run-frontend.bat"

rem ---- 等待端口就绪 ----
call :WAIT_PORT %BPORT% 35
if not "%WAITOK%"=="1" (
  echo [error] 后端未能在 %BPORT% 端口启动，日志尾部：
  call :TAIL_LINES "%LOGDIR%\backend.err.log" 30
  call :TAIL_LINES "%LOGDIR%\backend.log" 30
  set "CODE=1"
  goto :FINISH
)
call :PID_ON_PORT %BPORT%
rem 重定向前置：echo 1234>file 会被 cmd 当成句柄 4 的重定向
if defined PORT_PID (
  >"%LOGDIR%\backend.pid" echo %PORT_PID%
)
echo [afe] 后端就绪  http://%DHOST%:%BPORT%  (pid %PORT_PID%)

call :WAIT_PORT %FPORT% 35
if not "%WAITOK%"=="1" (
  echo [error] 前端未能在 %FPORT% 端口启动，日志尾部：
  call :TAIL_LINES "%LOGDIR%\frontend.err.log" 30
  call :TAIL_LINES "%LOGDIR%\frontend.log" 30
  set "CODE=1"
  goto :FINISH
)
call :PID_ON_PORT %FPORT%
if defined PORT_PID (
  >"%LOGDIR%\frontend.pid" echo %PORT_PID%
)
echo [afe] 前端就绪  http://%DHOST%:%FPORT%  (pid %PORT_PID%)

echo.
echo [afe] 访问界面: http://%DHOST%:%FPORT%
echo [afe] API 文档: http://%DHOST%:%BPORT%/docs
echo [afe] 日志目录: %LOGDIR%
echo [afe] 停止服务: scripts\stop.bat

if defined TAILFLAG goto :TAIL_VIEW
set "CODE=0"
goto :FINISH

rem ============================================================
rem  停止 / 状态
rem ============================================================
:DO_STOP
call :STOP_SERVICES
set "CODE=0"
goto :FINISH

:DO_STATUS
call :READ_PID backend
set "BPID=%RDPID%"
call :READ_PID frontend
set "FPID=%RDPID%"
set "BSTATE=未运行"
call :IS_PID_ALIVE "%BPID%"
if "%PIDALIVE%"=="1" set "BSTATE=运行中"
set "FSTATE=未运行"
call :IS_PID_ALIVE "%FPID%"
if "%PIDALIVE%"=="1" set "FSTATE=运行中"
call :IS_LISTENING %BPORT%
set "BLISTEN=[无监听]"
if "%LISTENING%"=="1" set "BLISTEN=[监听中]"
call :IS_LISTENING %FPORT%
set "FLISTEN=[无监听]"
if "%LISTENING%"=="1" set "FLISTEN=[监听中]"
if not defined BPID set "BPID=-"
if not defined FPID set "FPID=-"
echo 后端  :%BPORT%  PID=%BPID%  端口 %BPORT% %BLISTEN%  (%BSTATE%)
echo 前端  :%FPORT%  PID=%FPID%  端口 %FPORT% %FLISTEN%  (%FSTATE%)
echo 访问  : http://%DHOST%:%FPORT%
echo API   : http://%DHOST%:%BPORT%/docs
echo 日志  : %LOGDIR%
set "CODE=0"
goto :FINISH

rem ============================================================
rem  子过程
rem ============================================================
:STOP_SERVICES
echo [afe] 停止服务…
call :KILL_PIDFILE backend
call :KILL_PIDFILE frontend
rem 结束可能残留的启动窗口（cmd /c 包装进程；/T 不能与 /FI 同用，进程树由下面的端口兜底负责）
taskkill /FI "WINDOWTITLE eq AFE-Backend*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq AFE-Frontend*" /F >nul 2>&1
rem 兜底：只清理仍监听本项目端口、且进程名属于 python/node 的残留进程，
rem 避免误杀占用同端口的其他程序（IDE、宿主应用等）
call :KILL_PORT_OWNER %BPORT%
call :KILL_PORT_OWNER %FPORT%
del "%LOGDIR%\backend.pid" "%LOGDIR%\frontend.pid" >nul 2>&1
echo [afe] 服务已停止
goto :eof

:KILL_PIDFILE
if not exist "%LOGDIR%\%~1.pid" goto :eof
set "KP="
set /p KP=<"%LOGDIR%\%~1.pid"
if not defined KP goto :eof
call :IS_PID_ALIVE %KP%
if "%PIDALIVE%"=="1" goto :KP_KILL
echo [afe] PID %KP% 已不存在，跳过
goto :eof
:KP_KILL
rem /T 连同子进程一起结束（cmd -> npm -> node / python）
taskkill /PID %KP% /T /F >nul 2>&1
echo [afe] 已结束 PID %KP% (%~1)
goto :eof

:KILL_PORT_OWNER
call :PID_ON_PORT %~1
if not defined PORT_PID goto :eof
set "OWNER="
for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %PORT_PID%" /FO CSV /NH 2^>nul') do set "OWNER=%%~N"
if /I "%OWNER%"=="python.exe"  goto :KPO_KILL
if /I "%OWNER%"=="pythonw.exe" goto :KPO_KILL
if /I "%OWNER%"=="node.exe"    goto :KPO_KILL
goto :eof
:KPO_KILL
taskkill /PID %PORT_PID% /T /F >nul 2>&1
echo [afe] 兜底清理残留 PID %PORT_PID% (%OWNER%)
goto :eof

:READ_PID
set "RDPID="
if not exist "%LOGDIR%\%~1.pid" goto :eof
set /p RDPID=<"%LOGDIR%\%~1.pid"
goto :eof

:IS_PID_ALIVE
set "PIDALIVE=0"
if "%~1"=="" goto :eof
for /f "tokens=2 delims=," %%I in ('tasklist /FI "PID eq %~1" /FO CSV /NH 2^>nul') do (
  if "%%~I"=="%~1" set "PIDALIVE=1"
)
goto :eof

:IS_LISTENING
set "LISTENING=0"
netstat -ano -p TCP 2>nul | findstr /C:LISTENING | findstr /C:":%~1 " >nul
if not errorlevel 1 set "LISTENING=1"
goto :eof

:PID_ON_PORT
set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano -p TCP 2^>nul ^| findstr /C:LISTENING ^| findstr /C:":%~1 "') do (
  if not defined PORT_PID set "PORT_PID=%%P"
)
goto :eof

:WAIT_PORT
set "WAITOK=0"
set /a WP_TRIES=0
:WP_LOOP
call :IS_LISTENING %~1
if "%LISTENING%"=="1" set "WAITOK=1"
if "%WAITOK%"=="1" goto :eof
set /a WP_TRIES+=1
if %WP_TRIES% GEQ %~2 goto :eof
ping -n 2 127.0.0.1 >nul
goto :WP_LOOP

:TAIL_LINES
rem 打印文件末 %~2 行（纯批处理：先数总行数，再逐行输出最后 N 行）
if not exist "%~1" goto :eof
setlocal EnableDelayedExpansion
set /a TOT=0
for /f "usebackq delims=" %%L in ("%~1") do set /a TOT+=1
set /a SKIP=TOT-%~2
if !SKIP! LSS 0 set "SKIP=0"
set /a N=0
for /f "usebackq delims=" %%L in ("%~1") do (
  set "L=%%L"
  set /a N+=1
  if !N! GTR !SKIP! if defined L echo !L!
)
endlocal
goto :eof

:TAIL_VIEW
echo [afe] 持续刷新日志视图（Ctrl+C 退出跟踪，服务继续运行）
:TAIL_LOOP
cls
echo === backend.log (最后 20 行) ===
call :TAIL_LINES "%LOGDIR%\backend.log" 20
echo.
echo === frontend.log (最后 20 行) ===
call :TAIL_LINES "%LOGDIR%\frontend.log" 20
echo.
echo === 刷新中… Ctrl+C 退出跟踪 ===
ping -n 3 127.0.0.1 >nul
goto :TAIL_LOOP

rem ============================================================
rem  收尾
rem ============================================================
:FINISH
if not "%CODE%"=="0" (
  echo.
  echo [afe] 命令退出码 %CODE%。日志见 %LOGDIR%
  if not defined NOPAUSE pause
  endlocal & exit /b %CODE%
)
rem 双击启动（无参数）时，成功也停一下，否则窗口一闪而过看不到地址
if defined INTERACTIVE if not defined NOPAUSE pause
endlocal & exit /b %CODE%

