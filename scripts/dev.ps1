<#
.SYNOPSIS
    AndroidFileExplorer 前台开发模式（Windows / PowerShell）。
.DESCRIPTION
    对应 scripts/dev.sh。Ctrl+C 会同时结束后端与前端。
    三个模式：all（默认）/ backend / frontend。

    .\scripts\dev.ps1
    .\scripts\dev.ps1 backend
    .\scripts\dev.ps1 frontend
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('all', 'backend', 'frontend')]
    [string]$Mode = 'all',
    [string]$Root = ''
)

$ErrorActionPreference = 'Continue'
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path

$backendPy   = if ($env:VENV_PY) { $env:VENV_PY } else { Join-Path $Root '.venv\Scripts\python.exe' }
$backendPort = if ($env:SERVER_PORT)   { [int]$env:SERVER_PORT }   else { 8010 }
$frontendPort= if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 10012 }
$bindHost    = if ($env:BIND_HOST)     { $env:BIND_HOST }           else { '0.0.0.0' }
$displayHost = if ($bindHost -eq '0.0.0.0') { '127.0.0.1' } else { $bindHost }

function Write-Step ([string]$m) { Write-Host "[afe] $m" -ForegroundColor Cyan }

# 环境自检：仅后端模式跳过 Node 检查
$checkArgs = @{ Root = $Root }
if ($Mode -eq 'backend') { $checkArgs['BackendOnly'] = $true }
& (Join-Path $PSScriptRoot 'check-env.ps1') @checkArgs
if ($LASTEXITCODE -ne 0) { Write-Host '[error] 环境自检未通过，启动中止。' -ForegroundColor Red; exit 1 }

$procs = @()

function Start-Backend {
    Write-Step "后端 http://${displayHost}:${backendPort}  (docs: /docs)"
    $env:SERVER_HOST = $bindHost
    $env:SERVER_PORT = [string]$backendPort
    $p = Start-Process -FilePath $backendPy -ArgumentList @('-m', 'app.main') `
                       -WorkingDirectory (Join-Path $Root 'backend') `
                       -NoNewWindow -PassThru
    return $p
}

function Start-Frontend {
    $feDir = Join-Path $Root 'frontend'
    if (-not (Test-Path -LiteralPath (Join-Path $feDir 'node_modules'))) {
        Write-Step '前端依赖缺失，先执行 npm install …'
        & (Get-Command npm.cmd).Source install --prefix $feDir 2>&1 | Out-Host
    }
    Write-Step "前端 http://${displayHost}:${frontendPort}"
    $cmd = 'cd /d "{0}" && npm run dev -- --port {1} --host {2}' -f $feDir, $frontendPort, $bindHost
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList ('/c ' + $cmd) -NoNewWindow -PassThru
    return $p
}

function Stop-All {
    Write-Step '正在停止服务…'
    foreach ($p in $procs) {
        if ($p -and (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)) {
            & taskkill /PID $p.Id /T /F 2>$null | Out-Null
        }
    }
    Write-Step '已停止'
}

# try/finally 保证 Ctrl+C 或异常退出时清理整棵子进程树
try {
    switch ($Mode) {
        'backend'  { $procs += (Start-Backend) }
        'frontend' { $procs += (Start-Frontend) }
        'all' {
            $procs += (Start-Backend)
            Start-Sleep -Seconds 2
            $procs += (Start-Frontend)
        }
    }

    Write-Host ''
    Write-Step ("访问界面: http://{0}:{1}" -f $displayHost, $frontendPort)
    Write-Step '按 Ctrl+C 停止所有服务'
    Write-Host ''

    # 阻塞直到任一进程退出或用户中断；前台进程输出直接可见
    while ($true) {
        foreach ($p in $procs) {
            if ($p.HasExited) {
                Write-Host "[afe] 进程 $($p.Id) 已退出 (code $($p.ExitCode))" -ForegroundColor Yellow
                Stop-All
                exit 1
            }
        }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Stop-All
}
