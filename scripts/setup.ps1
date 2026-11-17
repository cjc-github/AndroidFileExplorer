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

# ---- 0. 前置检查：服务在跑时不要动 node_modules ----
# vite dev server 会持有 node_modules\@esbuild\win32-x64\esbuild.exe 的句柄，
# 此时 npm ci / npm install 会报 EPERM unlink。先提示用户停服务，避免英文堆栈。
function Test-PortBusy ([int]$Port) {
    $client = $null
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return $iar.AsyncWaitHandle.WaitOne(400) -and $client.Connected
    } catch { return $false }
    finally { if ($client) { $client.Close() } }
}

# 与 start.ps1 保持同一套端口口径，避免改了端口后守卫失效
$guardPorts = @(
    if ($env:SERVER_PORT)   { [int]$env:SERVER_PORT }   else { 8010 }
    if ($env:FRONTEND_PORT) { [int]$env:FRONTEND_PORT } else { 10012 }
)
$busyPorts = @($guardPorts | Where-Object { Test-PortBusy $_ })
if ($busyPorts.Count -gt 0) {
    Write-Host ''
    Write-Host '[error] 检测到服务正在运行（端口 ' -NoNewline -ForegroundColor Red
    Write-Host ($busyPorts -join ', ') -NoNewline -ForegroundColor Red
    Write-Host '）。' -ForegroundColor Red
    Write-Host '       前端 dev server 会占用 node_modules 里的 esbuild.exe，' -ForegroundColor Yellow
    Write-Host '       此时安装依赖会失败（EPERM: unlink）。请先停止服务再安装：' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '       .\scripts\stop.cmd          # 或 .\scripts\start.ps1 stop' -ForegroundColor Cyan
    Write-Host ''
    exit 1
}

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
# pip 在部分网络环境（企业代理 / TLS 中间设备）下会报 SSLError，
# 因此按「默认源 → HTTP 镜像 + trusted-host」逐级回退，不因单源失败就中断。
$PIP_INDEX_FALLBACKS = @(
    @{ Name = '默认源';     Args = @() },
    @{ Name = '阿里云镜像'; Args = @('-i', 'http://mirrors.aliyun.com/pypi/simple/', '--trusted-host', 'mirrors.aliyun.com') },
    @{ Name = '清华镜像';   Args = @('-i', 'https://pypi.tuna.tsinghua.edu.cn/simple', '--trusted-host', 'pypi.tuna.tsinghua.edu.cn') }
)

function Invoke-Native {
    # 包装原生命令：合并 stdout/stderr、保留退出码，且不让 stderr 触发 ErrorActionPreference=Stop
    param([string]$Exe, [string[]]$NativeArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $Exe @NativeArgs 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    foreach ($line in $out) { Write-Host $line }
    return $code
}

Write-Step '升级 pip …'
$pipUpgraded = $false
foreach ($idx in $PIP_INDEX_FALLBACKS) {
    Write-Step ("  尝试 pip 源：{0}" -f $idx.Name)
    $code = Invoke-Native $venvPy (@('-m', 'pip', 'install', '--upgrade', 'pip', '--disable-pip-version-check') + $idx.Args)
    if ($code -eq 0) { $pipUpgraded = $true; break }
    Write-Step ("  {0} 不可用（退出码 {1}），切换下一个源" -f $idx.Name, $code)
}
if (-not $pipUpgraded) { Write-Step 'pip 升级未成功，继续使用现有 pip 安装依赖（不中断）' }

Write-Step '安装后端 Python 依赖 …'
$reqFile = Join-Path $Root 'backend\requirements.txt'
$installed = $false
foreach ($idx in $PIP_INDEX_FALLBACKS) {
    Write-Step ("  尝试 pip 源：{0}" -f $idx.Name)
    $code = Invoke-Native $venvPy (@('-m', 'pip', 'install', '-r', $reqFile, '--disable-pip-version-check') + $idx.Args)
    if ($code -eq 0) { $installed = $true; break }
    Write-Step ("  {0} 安装失败（退出码 {1}），切换下一个源" -f $idx.Name, $code)
}
if (-not $installed) { throw '后端依赖安装失败（所有 pip 源均不可用）' }

# ---- 3. 前端依赖 ----
Write-Step '安装前端 Node 依赖 …'
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) { throw '未找到 npm，请先安装 Node.js 20+' }

$feDir = Join-Path $Root 'frontend'
$nmDir = Join-Path $feDir 'node_modules'

# Windows 上 npm 反复在同一目录操作会留下半成品目录，
# 导致下一次安装报 ENOTEMPTY / TAR_ENTRY_ERROR 并装出残缺的 node_modules。
# 这里先清空再装，保证装出来的依赖树是完整的。
if (Test-Path -LiteralPath $nmDir) {
    Write-Step '清理旧的 node_modules …'
    try {
        Remove-Item -LiteralPath $nmDir -Recurse -Force -ErrorAction Stop
    } catch {
        # 删不掉通常意味着有进程占用（编辑器、杀毒软件、残留的 vite）。
        # 此时继续安装只会装出残缺依赖树，直接中止并说明原因。
        throw ('无法删除 frontend\node_modules：' + $_.Exception.Message.Split("`n")[0] +
               '。请确认没有程序占用该目录（如 vite dev server、编辑器），或以管理员身份重试')
    }
}

$npmCode = Invoke-Native $npm.Source (@('ci', '--prefix', $feDir))
if ($npmCode -ne 0) {
    Write-Step 'npm ci 失败，回退到 npm install …'
    $npmCode = Invoke-Native $npm.Source (@('install', '--prefix', $feDir))
}
if ($npmCode -ne 0) {
    throw '前端依赖安装失败（npm ci 与 npm install 均失败），请检查网络后重试'
}

# 安装结果校验：只看退出码不够，npm 可能在装出残缺依赖树时仍返回 0
$requiredFiles = @(
    'node_modules\vite\bin\vite.js',
    'node_modules\@esbuild\win32-x64\esbuild.exe',
    'node_modules\react\package.json'
)
$missing = @($requiredFiles | Where-Object { -not (Test-Path -LiteralPath (Join-Path $feDir $_)) })
if ($missing.Count -gt 0) {
    throw ('前端依赖安装不完整，缺少：' + ($missing -join ', ') + '。请删除 frontend\node_modules 后重试')
}
Write-Step '前端依赖校验通过'

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
