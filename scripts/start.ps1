<#
.SYNOPSIS
    AndroidFileExplorer 后台启动 / 停止（Windows / PowerShell）。
.DESCRIPTION
    对应 scripts/start.sh。启动后端 :8010 与前端 :10012（均绑定 0.0.0.0），
    日志写入 <工程根目录>\logs\，PID 记录在 logs\*.pid，等待两个端口就绪后才返回。

    .\scripts\start.ps1            启动两个服务
    .\scripts\start.ps1 stop      停止两个服务
    .\scripts\start.ps1 status    查看状态
    .\scripts\start.ps1 -Tail     启动后持续跟踪日志（Ctrl+C 退出，不停止服务）

    可用环境变量覆盖：SERVER_PORT / FRONTEND_PORT / BIND_HOST / VENV_PY
    注意：vite 的 /api 代理在 frontend\vite.config.ts 中固定指向 127.0.0.1:8010，
    若改动 SERVER_PORT，请同步修改该代理或使用后端固定 8010 端口。
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status', 'restart')]
    [string]$Action = 'start',
    [switch]$Tail,
    [string]$Root = ''
)

$ErrorActionPreference = 'Continue'
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path

$logDir    = Join-Path $Root 'logs'
$pidDir    = $logDir
$backendPy = if ($env:VENV_PY) { $env:VENV_PY } else { Join-Path $Root '.venv\Scripts\python.exe' }
$backendPort  = if ($env:SERVER_PORT)    { [int]$env:SERVER_PORT }    else { 8010 }
$frontendPort = if ($env:FRONTEND_PORT)  { [int]$env:FRONTEND_PORT }  else { 10012 }
$bindHost     = if ($env:BIND_HOST)      { $env:BIND_HOST }            else { '0.0.0.0' }
$displayHost  = if ($bindHost -eq '0.0.0.0') { '127.0.0.1' } else { $bindHost }

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

# ---------- 工具函数 ----------
function Write-Step ([string]$m) { Write-Host "[afe] $m" -ForegroundColor Cyan }

function Test-PortListening ([int]$Port) {
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return $iar.AsyncWaitHandle.WaitOne(400) -and $client.Connected
    } catch { return $false }
    finally { if ($client) { $client.Close() } }
}

function Wait-Port ([int]$Port, [int]$TimeoutSec = 35) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if (Test-PortListening $Port) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Read-PidFile ([string]$Name) {
    $f = Join-Path $pidDir "$Name.pid"
    if (-not (Test-Path -LiteralPath $f)) { return $null }
    $raw = (Get-Content -LiteralPath $f -Raw).Trim()
    $v = 0
    if ([int]::TryParse($raw, [ref]$v)) { return $v }
    return $null
}

function Write-PidFile ([string]$Name, [int]$Id) {
    Set-Content -LiteralPath (Join-Path $pidDir "$Name.pid") -Value $Id -Encoding ascii -NoNewline
}

function Remove-PidFile ([string]$Name) {
    Remove-Item -LiteralPath (Join-Path $pidDir "$Name.pid") -Force -ErrorAction SilentlyContinue
}

function Get-PidProcess ([int]$Id) {
    if (-not $Id) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId = $Id" -ErrorAction SilentlyContinue
}

function Stop-ByPid ([int]$Id) {
    if (-not $Id) { return }
    $p = Get-PidProcess $Id
    if (-not $p) {
        Write-Step "PID $Id 已不存在，跳过"
        return
    }
    # /T 连同子进程一起结束（npm.cmd -> node -> vite）
    & taskkill /PID $Id /T /F 2>$null | Out-Null
    Write-Step "已结束 PID $Id ($($p.Name))"
}

function Stop-ScopedProcesses {
    # 兜底清理：只认「进程名属于 node/python 系」且命令行明确指向本工程的服务进程。
    # 不加进程名限制会误杀 IDE / 宿主应用（它们的命令行里也可能出现 vite 或工程路径）。
    $allowedNames = @('node.exe', 'python.exe', 'pythonw.exe')
    $owned = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $cl = $_.CommandLine
        if (-not $cl) { return $false }
        if ($allowedNames -notcontains $_.Name.ToLowerInvariant()) { return $false }
        $isBackend = $cl -match 'app\.main' -and $cl -match [regex]::Escape($Root)
        # 只认 vite 的 node 进程；不要匹配宽泛的 npm，否则会误杀同工程的 npm test / npm install
        $isVite = $cl -match 'vite' -and $cl -match '--port' -and $cl -match [regex]::Escape([string]$frontendPort)
        return ($isBackend -or $isVite)
    }
    foreach ($p in $owned) {
        & taskkill /PID $p.ProcessId /T /F 2>$null | Out-Null
        Write-Step "兜底清理残留 PID $($p.ProcessId) ($($p.Name))"
    }
}

function Stop-Services {
    Write-Step '停止服务…'
    Stop-ByPid (Read-PidFile 'backend')
    Stop-ByPid (Read-PidFile 'frontend')
    Start-Sleep -Seconds 1
    Stop-ScopedProcesses
    Remove-PidFile 'backend'
    Remove-PidFile 'frontend'
    Write-Step '服务已停止'
}

function Show-Status {
    $b = Read-PidFile 'backend'
    $f = Read-PidFile 'frontend'
    $bAlive = $b -and (Get-PidProcess $b)
    $fAlive = $f -and (Get-PidProcess $f)
    Write-Host ("后端  :{0}  PID={1}  端口 {2} {3}" -f $(if ($bAlive) {'运行中'} else {'未运行'}), $(if ($b) {$b} else {'-'}), $backendPort,  $(if (Test-PortListening $backendPort)  {'[监听中]'} else {'[无监听]'}))
    Write-Host ("前端  :{0}  PID={1}  端口 {2} {3}" -f $(if ($fAlive) {'运行中'} else {'未运行'}), $(if ($f) {$f} else {'-'}), $frontendPort, $(if (Test-PortListening $frontendPort) {'[监听中]'} else {'[无监听]'}))
    Write-Host ("访问  : http://{0}:{1}" -f $displayHost, $frontendPort)
    Write-Host ("API   : http://{0}:{1}/docs" -f $displayHost, $backendPort)
    Write-Host ("日志  : {0}" -f $logDir)
}

# ---------- 命令分派 ----------
switch ($Action) {
    'stop'   { Stop-Services; exit 0 }
    'status' { Show-Status; exit 0 }
    'restart'{ Stop-Services; $Action = 'start' }
}

# ---------- 环境自检 ----------
$checker = Join-Path $PSScriptRoot 'check-env.ps1'
if (-not (Test-Path -LiteralPath $checker)) {
    Write-Host "[error] 缺少 $checker" -ForegroundColor Red
    exit 1
}
& $checker -Root $Root
if ($LASTEXITCODE -ne 0) { Write-Host '[error] 环境自检未通过，启动中止。' -ForegroundColor Red; exit 1 }

# ---------- 先停旧的，避免端口占用 ----------
Stop-Services

# ---------- 后端 ----------
# PS 5.1 的 Start-Process 没有 -Environment，但子进程会继承父会话的 $env:；
# 这里临时设置并在启动后还原，避免污染调用者会话。
Write-Step "启动后端 :$backendPort (bind $bindHost)"
$prevHost = $env:SERVER_HOST
$prevPort = $env:SERVER_PORT
$env:SERVER_HOST = $bindHost
$env:SERVER_PORT = [string]$backendPort
try {
    $bp = Start-Process -FilePath $backendPy -ArgumentList @('-m', 'app.main') `
                        -WorkingDirectory (Join-Path $Root 'backend') `
                        -WindowStyle Hidden -PassThru `
                        -RedirectStandardOutput (Join-Path $logDir 'backend.log') `
                        -RedirectStandardError  (Join-Path $logDir 'backend.err.log')
} finally {
    if ($null -eq $prevHost) { Remove-Item Env:SERVER_HOST -ErrorAction SilentlyContinue } else { $env:SERVER_HOST = $prevHost }
    if ($null -eq $prevPort) { Remove-Item Env:SERVER_PORT -ErrorAction SilentlyContinue } else { $env:SERVER_PORT = $prevPort }
}
Write-PidFile 'backend' $bp.Id

# ---------- 前端 ----------
# npm.cmd 是批处理，直接 Start-Process 会走 ShellExecute 且 Hidden 不稳定；
# 统一用 cmd /c 包装，显式 cd 到 frontend 目录。
Write-Step "启动前端 :$frontendPort (bind $bindHost)"
$feDir = Join-Path $Root 'frontend'
if (-not (Test-Path -LiteralPath (Join-Path $feDir 'node_modules'))) {
    Write-Step '前端依赖缺失，先执行 npm install …'
    & (Get-Command npm.cmd).Source install --prefix $feDir 2>&1 | Out-Host
}
$feCmd = 'cd /d "{0}" && npm run dev -- --port {1} --host {2}' -f $feDir, $frontendPort, $bindHost
$fp = Start-Process -FilePath $env:ComSpec -ArgumentList ('/c ' + $feCmd) `
                    -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput (Join-Path $logDir 'frontend.log') `
                    -RedirectStandardError  (Join-Path $logDir 'frontend.err.log')
Write-PidFile 'frontend' $fp.Id

# ---------- 等待就绪 ----------
if (Wait-Port $backendPort) {
    Write-Step ("后端就绪  http://{0}:{1}  (pid {2})" -f $displayHost, $backendPort, $bp.Id)
} else {
    Write-Host "[error] 后端未能在 $backendPort 端口启动，日志尾部：" -ForegroundColor Red
    Get-Content -LiteralPath (Join-Path $logDir 'backend.err.log') -Tail 30 -ErrorAction SilentlyContinue | Out-Host
    Get-Content -LiteralPath (Join-Path $logDir 'backend.log')     -Tail 30 -ErrorAction SilentlyContinue | Out-Host
    exit 1
}

if (Wait-Port $frontendPort) {
    Write-Step ("前端就绪  http://{0}:{1}  (pid {2})" -f $displayHost, $frontendPort, $fp.Id)
} else {
    Write-Host "[error] 前端未能在 $frontendPort 端口启动，日志尾部：" -ForegroundColor Red
    Get-Content -LiteralPath (Join-Path $logDir 'frontend.err.log') -Tail 30 -ErrorAction SilentlyContinue | Out-Host
    Get-Content -LiteralPath (Join-Path $logDir 'frontend.log')     -Tail 30 -ErrorAction SilentlyContinue | Out-Host
    exit 1
}

Write-Host ''
Write-Step ("访问界面: http://{0}:{1}" -f $displayHost, $frontendPort)
Write-Step ("API 文档: http://{0}:{1}/docs" -f $displayHost, $backendPort)
Write-Step ("日志目录: {0}" -f $logDir)
Write-Step '停止服务: .\scripts\start.ps1 stop'

if ($Tail) {
    Write-Step '持续跟踪日志（Ctrl+C 退出跟踪，服务继续运行）'
    Get-Content -LiteralPath (Join-Path $logDir 'backend.log'), (Join-Path $logDir 'frontend.log') -Wait -Tail 20
}
