<#
.SYNOPSIS
    AndroidFileExplorer 首次安装（Windows / PowerShell）。
.DESCRIPTION
    一步完成：创建 .venv → 安装后端依赖 → 安装前端依赖 → 环境自检。
    之后即可 .\scripts\start.ps1 或 .\scripts\dev.ps1。
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#>
[CmdletBinding()]
param([string]$Root = '')

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Root)) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = (Resolve-Path -LiteralPath $Root).Path
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'

function Write-Step ([string]$m) { Write-Host "[afe] $m" -ForegroundColor Cyan }

# ---- 1. 虚拟环境 ----
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Step '创建虚拟环境 .venv …'
    $py = $null
    foreach ($cand in @('py', 'python', 'python3')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) { $py = $cmd.Source; break }
    }
    if (-not $py) { throw '未找到 Python，请先安装 Python 3.10+（https://www.python.org/downloads/）' }
    if ($py -match 'py\.exe$') {
        & $py -3 -m venv (Join-Path $Root '.venv')
    } else {
        & $py -m venv (Join-Path $Root '.venv')
    }
    if ($LASTEXITCODE -ne 0) { throw '创建虚拟环境失败' }
} else {
    Write-Step '虚拟环境已存在，跳过创建'
}

# ---- 2. 后端依赖 ----
Write-Step '安装后端 Python 依赖 …'
& $venvPy -m pip install --upgrade pip 2>&1 | Out-Host
& $venvPy -m pip install -r (Join-Path $Root 'backend\requirements.txt') 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw '后端依赖安装失败' }

# ---- 3. 前端依赖 ----
Write-Step '安装前端 Node 依赖 …'
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { throw '未找到 npm，请先安装 Node.js 20+' }
& $npm.Source ci --prefix (Join-Path $Root 'frontend') 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) {
    Write-Step 'npm ci 失败，回退到 npm install …'
    & $npm.Source install --prefix (Join-Path $Root 'frontend') 2>&1 | Out-Host
}

# ---- 4. 环境自检 ----
Write-Step '环境自检 …'
& (Join-Path $PSScriptRoot 'check-env.ps1') -Root $Root
if ($LASTEXITCODE -ne 0) { throw '环境自检未通过，请按上面的 [fail] 提示修复' }

Write-Host ''
Write-Step '安装完成。启动方式：'
Write-Host '  后台启动: .\scripts\start.cmd'
Write-Host '  前台开发: .\scripts\dev.cmd'
Write-Host '  停止服务: .\scripts\start.cmd stop'
Write-Host ''
