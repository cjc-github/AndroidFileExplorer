@echo off
rem AndroidFileExplorer environment self-check (Windows, pure batch - no PowerShell).
rem Prints [ok] / [warn] / [fail] per item; exit code 1 when any [fail] is present.
rem Read-only and safe to run anytime.
rem Usage: check-env.bat [backend] [nopause]
rem   backend = skip Node / frontend checks
rem   nopause = do not wait for a key (used when called from other .bat launchers)
rem
rem All Windows launchers in this folder are plain .bat: there is no .ps1 helper.
chcp 65001 >nul
setlocal EnableExtensions
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "FAILED=0"
set "WARNED=0"
set "MODE=%~1"
set "NOPAUSE="
if /I "%~2"=="nopause" set "NOPAUSE=1"
if /I "%MODE%"=="nopause" set "NOPAUSE=1"
if /I "%MODE%"=="nopause" set "MODE="

echo === 本地环境检查 (Windows) ===
echo 工程目录: %ROOT%
echo.

rem ---------- Python 虚拟环境 ----------
set "PY="
if defined VENV_PY if exist "%VENV_PY%" set "PY=%VENV_PY%"
if not defined PY if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if not defined PY if exist "%ROOT%\.venv\bin\python.exe" set "PY=%ROOT%\.venv\bin\python.exe"
if not defined PY goto :PY_MISSING

rem 版本号先落到临时文件再读取：for /f 里直接跑 "带空格路径\python.exe" 会被 cmd 吞掉首尾引号
set "PYVERFILE=%TEMP%\afe-pyver-%RANDOM%%RANDOM%.txt"
"%PY%" -V >"%PYVERFILE%" 2>&1
set "PYVER="
if exist "%PYVERFILE%" for /f "usebackq delims=" %%V in ("%PYVERFILE%") do if not defined PYVER set "PYVER=%%V"
del "%PYVERFILE%" >nul 2>&1
if not defined PYVER set "PYVER=未知"
"%PY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 goto :PY_TOO_OLD
call :ok "%PYVER%  (%PY%)"
goto :PY_DEPS

:PY_TOO_OLD
call :fail "%PYVER% 版本过低，需要 3.10+；重建：py -3 -m venv .venv"
goto :PY_DEPS
:PY_MISSING
call :fail "缺少虚拟环境：执行 scripts\setup.bat，或 py -3 -m venv .venv 后安装依赖"
goto :ADB

:PY_DEPS
"%PY%" -c "import fastapi,uvicorn,sqlmodel,aiosqlite,dotenv,loguru" >nul 2>&1
if errorlevel 1 goto :PY_DEPS_MISSING
call :ok "后端 Python 依赖完整"
goto :PY_ASYNCSSH
:PY_DEPS_MISSING
call :fail "后端依赖缺失：.venv\Scripts\python.exe -m pip install -r backend\requirements.txt"

:PY_ASYNCSSH
"%PY%" -c "import asyncssh" >nul 2>&1
if errorlevel 1 goto :PY_NO_SSH
call :ok "SSH 直连依赖 asyncssh 可用"
goto :ADB
:PY_NO_SSH
call :warn "未安装 asyncssh：SSH 直连不可用；.venv\Scripts\python.exe -m pip install asyncssh"

rem ---------- adb（可选，5 秒超时避免挂起）----------
:ADB
set "ADB=%ADB_PATH%"
if not defined ADB set "ADB=adb"
rem ADB_PATH 常被设成完整路径，此时 where 反而找不到，直接用文件存在性判断
set "ADBFOUND="
if defined ADB_PATH if exist "%ADB_PATH%" set "ADBFOUND=1"
if defined ADBFOUND goto :ADB_RUN
where "%ADB%" >nul 2>&1
if errorlevel 1 goto :ADB_MISSING
:ADB_RUN
rem Run adb version in the background and wait at most 5s for the done marker,
rem so a wedged adb cannot hang the whole environment check.
set "ADBOUT=%TEMP%\afe-adb-%RANDOM%%RANDOM%.out"
set "ADBDONE=%ADBOUT%.done"
set "ADBHELPER=%TEMP%\afe-adb-%RANDOM%%RANDOM%.bat"
>"%ADBHELPER%" echo @echo off
>>"%ADBHELPER%" echo "%ADB%" version ^> "%ADBOUT%" 2^>^&1
>>"%ADBHELPER%" echo echo done^> "%ADBDONE%"
start "" /b cmd /c "%ADBHELPER%"
set /a ADBWAIT=0
:ADB_WAIT
if exist "%ADBDONE%" goto :ADB_READY
set /a ADBWAIT+=1
if %ADBWAIT% GEQ 5 goto :ADB_TIMEOUT
ping -n 2 127.0.0.1 >nul
goto :ADB_WAIT
:ADB_TIMEOUT
call :warn "adb version 超时（5 秒）：检查 %ADB%"
goto :ADB_CLEAN
:ADB_READY
set "ADBLINE="
for /f "usebackq delims=" %%L in ("%ADBOUT%") do if not defined ADBLINE set "ADBLINE=%%L"
if not defined ADBLINE goto :ADB_BAD
call :ok "本机 adb 可用：%ADBLINE%"
goto :ADB_CLEAN
:ADB_BAD
call :warn "adb 存在但无法执行 version：检查 %ADB%"
:ADB_CLEAN
del "%ADBOUT%" "%ADBDONE%" "%ADBHELPER%" >nul 2>&1
goto :NODE
:ADB_MISSING
call :warn "未找到 adb（ADB_PATH=%ADB%）：本机设备发现不可用；仅使用远程机器时可忽略"
goto :NODE

rem ---------- Node / npm / 前端依赖 ----------
:NODE
if /I "%MODE%"=="backend" goto :SUMMARY
set "NODEV="
for /f "delims=" %%V in ('node -v 2^>nul') do set "NODEV=%%V"
if not defined NODEV goto :NODE_MISSING
set "NODEMAJOR=%NODEV:v=%"
for /f "tokens=1 delims=." %%M in ("%NODEMAJOR%") do set "NODEMAJOR=%%M"
if not defined NODEMAJOR set "NODEMAJOR=0"
if %NODEMAJOR% GEQ 20 goto :NODE_OK
call :fail "Node %NODEV% 需要 20 或更高版本"
goto :NPM
:NODE_OK
call :ok "Node %NODEV%"
goto :NPM
:NODE_MISSING
call :fail "未找到 node：安装 Node.js 20+（https://nodejs.org）"

:NPM
set "NPMV="
for /f "delims=" %%V in ('call npm -v 2^>nul') do set "NPMV=%%V"
if not defined NPMV goto :NPM_MISSING
call :ok "npm %NPMV%"
goto :NODE_MODULES
:NPM_MISSING
call :fail "未找到 npm：安装 Node.js 20+"

:NODE_MODULES
if exist "%ROOT%\frontend\node_modules" goto :NODE_MODULES_OK
call :warn "缺少 frontend\node_modules：执行 scripts\setup.bat 或 在 frontend 目录执行 npm install"
goto :SUMMARY
:NODE_MODULES_OK
call :ok "前端依赖已安装"

rem ---------- 总结 ----------
:SUMMARY
echo.
if not "%FAILED%"=="0" goto :RESULT_FAIL
if not "%WARNED%"=="0" goto :RESULT_WARN
echo 环境检查全部通过。
set "CODE=0"
goto :FINISH
:RESULT_WARN
echo 环境检查通过（存在警告项；对应能力可能不可用）。
set "CODE=0"
goto :FINISH
:RESULT_FAIL
echo 环境检查未通过：请先修复上面的 [fail] 项。
set "CODE=1"

:FINISH
if defined NOPAUSE exit /b %CODE%
rem Called directly (double-click): always pause so the output stays readable.
echo.
pause
exit /b %CODE%

rem ---------- 输出辅助 ----------
:ok
echo [ok]   %~1
goto :eof

:warn
echo [warn] %~1
set "WARNED=1"
goto :eof

:fail
echo [fail] %~1
set "FAILED=1"
goto :eof
