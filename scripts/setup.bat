@echo off
rem AndroidFileExplorer first-time setup (Windows, pure batch - no PowerShell).
rem Creates .venv, installs backend + frontend dependencies, then runs the env check.
rem
rem Built-in guards:
rem   - aborts if 8010 / 10012 is already listening (vite holds esbuild.exe and
rem     npm install would then fail with EPERM: unlink)
rem   - wipes stale frontend\node_modules before installing (a half-written tree
rem     makes npm fail with ENOTEMPTY / TAR_ENTRY_ERROR and install incomplete deps)
rem   - validates vite / esbuild / react files after install (npm can exit 0 with
rem     an incomplete tree)
rem   - pip falls back to mirrors when the default index fails with TLS errors
chcp 65001 >nul
setlocal EnableExtensions

set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "VENVPY=%ROOT%\.venv\Scripts\python.exe"
set "BPORT=%SERVER_PORT%"
if not defined BPORT set "BPORT=8010"
set "FPORT=%FRONTEND_PORT%"
if not defined FPORT set "FPORT=10012"

rem ---- 0. 前置检查：服务在跑时不要动 node_modules ----
call :IS_LISTENING %BPORT%
set "BUSY="
if "%LISTENING%"=="1" set "BUSY=%BPORT%"
call :IS_LISTENING %FPORT%
if "%LISTENING%"=="1" (
  if defined BUSY (set "BUSY=%BUSY%, %FPORT%") else (set "BUSY=%FPORT%")
)
if defined BUSY goto :BUSY_ABORT

rem ---- 1. 虚拟环境 ----
if exist "%VENVPY%" (
  echo [afe] 虚拟环境已存在，跳过创建
  goto :PIP
)
echo [afe] 创建虚拟环境 .venv …
set "PYEXE="
for %%C in (py.exe python.exe python3.exe) do (
  if not defined PYEXE (
    for /f "delims=" %%P in ('where %%C 2^>nul') do if not defined PYEXE set "PYEXE=%%P"
  )
)
if not defined PYEXE goto :NO_PYTHON
rem py.exe 需要 -3 选择 Python 3；其他解释器直接调用
for %%F in ("%PYEXE%") do set "PYNAME=%%~nxF"
if /I "%PYNAME%"=="py.exe" (
  "%PYEXE%" -3 -m venv "%ROOT%\.venv"
) else (
  "%PYEXE%" -m venv "%ROOT%\.venv"
)
if errorlevel 1 goto :VENV_FAILED
if not exist "%VENVPY%" goto :VENV_FAILED

rem ---- 2. 后端依赖（pip 三级源回退）----
:PIP
echo [afe] 升级 pip …
call :PIP_RUN upgrade
if not errorlevel 1 goto :PIP_UPGRADED
echo [afe] pip 升级未成功，继续使用现有 pip 安装依赖（不中断）
:PIP_UPGRADED

echo [afe] 安装后端 Python 依赖 …
call :PIP_RUN req
if errorlevel 1 goto :PIP_FAILED

rem ---- 3. 前端依赖 ----
echo [afe] 安装前端 Node 依赖 …
set "NPM="
for /f "delims=" %%P in ('where npm.cmd 2^>nul') do if not defined NPM set "NPM=%%P"
if not defined NPM goto :NO_NPM

set "FEDIR=%ROOT%\frontend"
if exist "%FEDIR%\node_modules" (
  echo [afe] 清理旧的 node_modules …
  rmdir /s /q "%FEDIR%\node_modules" >nul 2>&1
  if exist "%FEDIR%\node_modules" goto :NM_LOCKED
)

pushd "%FEDIR%"
call "%NPM%" ci --prefix "%FEDIR%"
set "NPMCODE=%ERRORLEVEL%"
popd
if "%NPMCODE%"=="0" goto :NPM_CHECK
echo [afe] npm ci 失败，回退到 npm install …
pushd "%FEDIR%"
call "%NPM%" install --prefix "%FEDIR%"
set "NPMCODE=%ERRORLEVEL%"
popd
if not "%NPMCODE%"=="0" goto :NPM_FAILED

:NPM_CHECK
rem 安装结果校验：npm 可能在装出残缺依赖树时仍返回 0
set "MISSING="
if not exist "%FEDIR%\node_modules\vite\bin\vite.js" set "MISSING=%MISSING% node_modules\vite\bin\vite.js"
if not exist "%FEDIR%\node_modules\@esbuild\win32-x64\esbuild.exe" set "MISSING=%MISSING% node_modules\@esbuild\win32-x64\esbuild.exe"
if not exist "%FEDIR%\node_modules\react\package.json" set "MISSING=%MISSING% node_modules\react\package.json"
if defined MISSING goto :NPM_INCOMPLETE
echo [afe] 前端依赖校验通过

rem ---- 4. 环境自检 ----
echo [afe] 环境自检 …
call "%~dp0check-env.bat" nopause
if errorlevel 1 (
  echo [error] 环境自检未通过，请按上面的 [fail] 提示修复
  set "CODE=1"
  goto :FINISH
)

echo.
echo [afe] 安装完成。启动方式：
echo   后台启动: scripts\start.bat
echo   前台开发: scripts\dev.bat
echo   停止服务: scripts\stop.bat
echo.
set "CODE=0"
goto :FINISH

rem ============================================================
rem  pip 源回退：任一源成功即停；全部失败才返回非 0
rem  用法：call :PIP_RUN <mode>   mode = upgrade | req
rem ============================================================
:PIP_RUN
set "PIPEXTRA=--upgrade pip"
if /I "%~1"=="upgrade" goto :PIP_RUN_GO
set "PIPEXTRA=-r "%ROOT%\backend\requirements.txt""

:PIP_RUN_GO
echo [afe]   尝试 pip 源：默认源
"%VENVPY%" -m pip install %PIPEXTRA% --disable-pip-version-check >nul 2>&1
if not errorlevel 1 exit /b 0
echo [afe]   默认源不可用，切换阿里云镜像
"%VENVPY%" -m pip install %PIPEXTRA% --disable-pip-version-check -i http://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com >nul 2>&1
if not errorlevel 1 exit /b 0
echo [afe]   阿里云镜像不可用，切换清华镜像
"%VENVPY%" -m pip install %PIPEXTRA% --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
if not errorlevel 1 exit /b 0
exit /b 1

rem ============================================================
rem  辅助
rem ============================================================
:IS_LISTENING
set "LISTENING=0"
netstat -ano -p TCP 2>nul | findstr /C:LISTENING | findstr /C:":%~1 " >nul
if not errorlevel 1 set "LISTENING=1"
goto :eof

rem ============================================================
rem  错误出口
rem ============================================================
:BUSY_ABORT
echo.
echo [error] 检测到服务正在运行（端口 %BUSY%）。
echo        前端 dev server 会占用 node_modules 里的 esbuild.exe，
echo        此时安装依赖会失败（EPERM: unlink）。请先停止服务再安装：
echo.
echo        scripts\stop.bat
echo.
set "CODE=1"
goto :FINISH

:NO_PYTHON
echo [error] 未找到 Python，请先安装 Python 3.10+（https://www.python.org/downloads/）
set "CODE=1"
goto :FINISH

:VENV_FAILED
echo [error] 创建虚拟环境失败
set "CODE=1"
goto :FINISH

:PIP_FAILED
echo [error] 后端依赖安装失败（所有 pip 源均不可用）
set "CODE=1"
goto :FINISH

:NO_NPM
echo [error] 未找到 npm，请先安装 Node.js 20+
set "CODE=1"
goto :FINISH

:NM_LOCKED
echo [error] 无法删除 frontend\node_modules。
echo        请确认没有程序占用该目录（如 vite dev server、编辑器），或以管理员身份重试。
set "CODE=1"
goto :FINISH

:NPM_FAILED
echo [error] 前端依赖安装失败（npm ci 与 npm install 均失败），请检查网络后重试
set "CODE=1"
goto :FINISH

:NPM_INCOMPLETE
echo [error] 前端依赖安装不完整，缺少：%MISSING%
echo        请删除 frontend\node_modules 后重试
set "CODE=1"
goto :FINISH

rem ============================================================
rem  收尾
rem ============================================================
:FINISH
echo.
if not "%CODE%"=="0" ( echo [afe] Setup failed with code %CODE% ) else ( echo [afe] Setup finished )
pause
endlocal & exit /b %CODE%
