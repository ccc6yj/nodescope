# 一键启动 Web 界面（Windows）：自动准备 Python 虚拟环境、依赖和 mihomo 内核，然后执行 run.py --serve。
# 用法：双击 start.bat，或在终端执行 .\start.bat [run.py 的其他参数，如 --port 9000]
#
# 可选环境变量（与 start.sh 相同）：
#   NS_MIHOMO_BIN      已有的 mihomo 路径（设置后不再查找/下载）
#   NS_MIHOMO_VERSION  下载的 mihomo 版本（默认见下方 $MihomoVersion）
#   NS_GITHUB_PROXY    GitHub 下载加速前缀，如 https://ghfast.top/
#   PIP_INDEX_URL      pip 镜像，如 https://pypi.tuna.tsinghua.edu.cn/simple
#
# 注意：本文件须保存为 UTF-8 with BOM，否则 Windows PowerShell 5.1 会按 ANSI 读取导致中文乱码/解析失败。
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # 关掉 Invoke-WebRequest 的进度条，否则下载会慢几十倍
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = $PSScriptRoot
$Venv = Join-Path $Root '.venv'
$VenvPy = Join-Path $Venv 'Scripts\python.exe'
$BinDir = Join-Path $Root 'bin'
$MihomoVersion = if ($env:NS_MIHOMO_VERSION) { $env:NS_MIHOMO_VERSION } else { 'v1.19.31' }

function Info($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "错误：$msg" -ForegroundColor Red; exit 1 }

# ---------- 1. Python ----------
# 依次尝试 py 启动器和 python；应用商店的 python.exe 占位程序会返回非 0，自然被跳过
$PyExe = $null
$PyArgs = @()
foreach ($cand in @('py -3', 'python', 'python3')) {
    $parts = $cand -split ' '
    if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
    $extra = @($parts | Select-Object -Skip 1)
    try {
        & $parts[0] @extra -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $PyExe = $parts[0]; $PyArgs = $extra; break }
    } catch { }
}
if (-not $PyExe) {
    Die "未找到 Python 3.8+。请从 https://www.python.org/downloads/ 安装（勾选 Add python.exe to PATH），或执行 winget install Python.Python.3.12"
}

# ---------- 2. 虚拟环境与依赖 ----------
if (-not (Test-Path $VenvPy)) {
    Info '创建虚拟环境 .venv'
    & $PyExe @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { Die '创建虚拟环境失败' }
}
# requirements.txt 没变就跳过安装，只在首次或依赖变更时联网
$Req = Join-Path $Root 'requirements.txt'
$Stamp = Join-Path $Venv '.requirements.installed'
$reqHash = (Get-FileHash $Req).Hash
$oldHash = if (Test-Path $Stamp) { (Get-Content $Stamp -Raw).Trim() } else { '' }
if ($reqHash -ne $oldHash) {
    Info '安装 Python 依赖'
    & $VenvPy -m pip install -q --disable-pip-version-check -r $Req
    if ($LASTEXITCODE -ne 0) { Die '依赖安装失败。网络慢可设置 PIP_INDEX_URL 使用国内镜像后重试' }
    Set-Content -Path $Stamp -Value $reqHash
}

# ---------- 3. mihomo 内核 ----------
function Find-Mihomo {
    if ($env:NS_MIHOMO_BIN -and (Test-Path $env:NS_MIHOMO_BIN)) { return $env:NS_MIHOMO_BIN }
    foreach ($name in @('mihomo', 'clash-meta')) {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) { return $cmd.Path }
    }
    $local = Join-Path $BinDir 'mihomo.exe'
    if (Test-Path $local) { return $local }
    return $null
}

function Get-Mihomo {
    # 32 位 PowerShell 跑在 64 位系统上时，真实架构在 PROCESSOR_ARCHITEW6432 里
    $archRaw = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($archRaw) {
        'AMD64' { $arch = 'amd64-compatible' }   # compatible 版不依赖较新的 CPU 指令集
        'ARM64' { $arch = 'arm64' }
        'x86'   { $arch = '386' }
        default { Die "不支持的 CPU 架构 $archRaw，请手动下载 mihomo 后设置 NS_MIHOMO_BIN" }
    }
    $asset = "mihomo-windows-$arch-$MihomoVersion.zip"
    $url = "$($env:NS_GITHUB_PROXY)https://github.com/MetaCubeX/mihomo/releases/download/$MihomoVersion/$asset"

    Info "下载 mihomo $MihomoVersion（windows/$arch）"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $zip = Join-Path $BinDir '.mihomo-download.zip'
    $tmpDir = Join-Path $BinDir '.mihomo-extract'
    # Windows PowerShell 5.1 默认可能不启用 TLS 1.2，GitHub 要求 1.2+
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    } catch {
        Remove-Item $zip -Force -ErrorAction SilentlyContinue
        Die ("下载失败：$url`n  访问 GitHub 慢可设置加速前缀后重试（cmd 中执行）：`n" +
             "    set `"NS_GITHUB_PROXY=https://ghfast.top/`"`n    start.bat")
    }
    Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $zip -DestinationPath $tmpDir -Force
    $exe = Get-ChildItem $tmpDir -Filter '*.exe' -Recurse | Select-Object -First 1
    if (-not $exe) { Die '下载的压缩包里没有找到 mihomo 可执行文件' }
    & $exe.FullName -v | Out-Null
    if ($LASTEXITCODE -ne 0) { Die '下载的 mihomo 无法运行，请手动安装后设置 NS_MIHOMO_BIN' }
    Move-Item $exe.FullName (Join-Path $BinDir 'mihomo.exe') -Force
    Remove-Item $zip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
}

$Mihomo = Find-Mihomo
if (-not $Mihomo) {
    Get-Mihomo
    $Mihomo = Join-Path $BinDir 'mihomo.exe'
}
$env:NS_MIHOMO_BIN = $Mihomo
Info "使用内核 $Mihomo"

# ---------- 4. 启动 ----------
$env:PYTHONUTF8 = '1'   # 避免中文 Windows 默认 GBK 编码读写文件出错
& $VenvPy (Join-Path $Root 'run.py') --serve @args
exit $LASTEXITCODE
