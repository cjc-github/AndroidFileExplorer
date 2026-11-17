<#
.SYNOPSIS
    AndroidFileExplorer 本地环境自检（Windows / PowerShell）。
.DESCRIPTION
    对应 scripts/check-env.sh，由 start.ps1 / dev.ps1 启动前自动调用。
    必需：.venv 虚拟环境（Python 3.10+）与后端依赖；前端另需 Node 20+ / npm。
    adb / asyncssh 为可选项，缺失只报警告——仅用远程机器时不依赖本机 adb。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1
    powershell -ExecutionPolicy Bypass -File scripts\check-env.ps1 -BackendOnly
#>
[CmdletBinding()]
param(
    [switch]$BackendOnly,
    [string]$Root = ''
)

$ErrorActionPreference = 'Continue'
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path

$script:Failed = $false
$script:Warned = $false

function Write-Ok   ([string]$m) { Write-Host "[ok]   $m" }
function Write-Warn ([string]$m) { Write-Host "[warn] $m" -ForegroundColor Yellow; $script:Warned = $true }
function Write-Fail ([string]$m) { Write-Host "[fail] $m" -ForegroundColor Red;    $script:Failed = $true }

function Find-VenvPython {
    if ($env:VENV_PY -and (Test-Path -LiteralPath $env:VENV_PY)) { return $env:VENV_PY }
    $p = Join-Path $Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $p) { return $p }
    $p2 = Join-Path $Root '.venv\bin\python.exe'
    if (Test-Path -LiteralPath $p2) { return $p2 }
    return $null
}

Write-Host '=== 本地环境检查 (Windows) ==='
Write-Host "工程目录: $Root"
Write-Host ''

# ---- Python venv ----
$venvPy = Find-VenvPython
if ($venvPy) {
    # 用 -V 取版本：比 `-c "print(...)"` 在 Windows PowerShell 5.1 下更稳定
    # （后者会把 stdout 与 stderr 合并，版本串可能取不到）
    $pyVer = ((& $venvPy -V 2>&1) | Select-Object -First 1)
    if (-not $pyVer) { $pyVer = '未知' }
    & $venvPy -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok "$pyVer  ($venvPy)" }
    else { Write-Fail "$pyVer 版本过低，需要 3.10+；重建：py -3 -m venv .venv" }
} else {
    Write-Fail '缺少虚拟环境：执行 .\scripts\setup.ps1，或 py -3 -m venv .venv 后安装依赖'
}

if ($venvPy) {
    & $venvPy -c 'import fastapi, uvicorn, sqlmodel, aiosqlite, dotenv, loguru' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok '后端 Python 依赖完整' }
    else { Write-Fail '后端依赖缺失：.venv\Scripts\python.exe -m pip install -r backend\requirements.txt' }

    & $venvPy -c 'import asyncssh' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Ok 'SSH 直连依赖 asyncssh 可用' }
    else { Write-Warn '未安装 asyncssh：SSH 直连不可用；.venv\Scripts\python.exe -m pip install asyncssh' }
}

# ---- adb（可选，带 5 秒超时，避免挂起）----
$adbBin = if ($env:ADB_PATH) { $env:ADB_PATH } else { 'adb' }
$adbCmd = Get-Command $adbBin -ErrorAction SilentlyContinue
if ($adbCmd) {
    $outFile = Join-Path $env:TEMP ("afe-adb-{0}.out.txt" -f $PID)
    $errFile = Join-Path $env:TEMP ("afe-adb-{0}.err.txt" -f $PID)
    try {
        $p = Start-Process -FilePath $adbCmd.Source -ArgumentList 'version' -WindowStyle Hidden -PassThru `
                           -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        if ($p.WaitForExit(5000)) {
            $line = Get-Content -LiteralPath $outFile -TotalCount 1 -ErrorAction SilentlyContinue
            if ($line) { Write-Ok "本机 adb 可用：$line" }
            else { Write-Warn "adb 存在但无法执行 version：检查 $adbBin" }
        } else {
            $p.Kill()
            Write-Warn "adb version 超时（5 秒）：检查 $adbBin"
        }
    } catch {
        Write-Warn "adb version 执行失败：$($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
} else {
    Write-Warn "未找到 adb（ADB_PATH=$adbBin）：本机设备发现不可用；仅使用远程机器时可忽略"
}

# ---- Node / npm / 前端依赖 ----
if (-not $BackendOnly) {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if ($nodeCmd) {
        $nodeVer = (& node -v 2>$null | Select-Object -First 1)
        $major = 0
        if ($nodeVer -match '^v?(\d+)') { $major = [int]$Matches[1] }
        if ($major -ge 20) { Write-Ok "Node $nodeVer" }
        else { Write-Fail "Node $nodeVer 需要 20 或更高版本" }
    } else {
        Write-Fail '未找到 node：安装 Node.js 20+（https://nodejs.org）'
    }

    $npmCmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($npmCmd) {
        $npmVer = (& $npmCmd.Source -v 2>$null | Select-Object -First 1)
        Write-Ok "npm $npmVer"
    } else {
        Write-Fail '未找到 npm：安装 Node.js 20+'
    }

    if (Test-Path -LiteralPath (Join-Path $Root 'frontend\node_modules')) {
        Write-Ok '前端依赖已安装'
    } else {
        Write-Warn '缺少 frontend\node_modules：执行 .\scripts\setup.ps1 或 在 frontend 目录执行 npm install'
    }
}

Write-Host ''
if ($script:Failed) {
    Write-Host '环境检查未通过：请先修复上面的 [fail] 项。' -ForegroundColor Red
    exit 1
}
if ($script:Warned) {
    Write-Host '环境检查通过（存在警告项；对应能力可能不可用）。' -ForegroundColor Yellow
    exit 0
}
Write-Host '环境检查全部通过。' -ForegroundColor Green
exit 0
