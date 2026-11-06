# 一键启动 Web 界面（Windows）：自动准备 Python 虚拟环境、依赖和 mihomo 内核，然后执行 run.py --serve。
# 用法：双击 start.bat，或在终端执行 .\start.bat [run.py 的其他参数，如 --port 9000]
#
# 可选环境变量（与 start.sh 相同）：
#   NS_MIHOMO_BIN      已有的 mihomo 路径（设置后不再查找/下载）
#   NS_MIHOMO_VERSION  固定下载的 mihomo 版本，如 v1.19.33（默认自动跟随最新正式版）
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
# 查不到最新版本又没有任何可用内核时，才退回下载这个版本
$FallbackMihomoVersion = 'v1.19.33'
# 最新版本号的缓存：一天内只联网查一次，避免每次启动都等 GitHub
$LatestCache = Join-Path $BinDir '.mihomo-latest'

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
# 默认使用项目 bin\ 下的内核并保持为最新正式版（旧内核缺新协议，如 xhttp 需 v1.19.22+）；
# 设置 NS_MIHOMO_BIN 时完全尊重用户选择，不查找也不下载。
# Windows PowerShell 5.1 默认可能不启用 TLS 1.2，GitHub 要求 1.2+
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# 返回内核版本号（如 v1.19.33）；alpha 版或无法运行时返回 $null
function Get-MihomoVersion($exe) {
    try {
        $out = (& $exe -v 2>$null) -join "`n"
        if ($out -match 'Meta\s+v?(\d+\.\d+\.\d+)') { return "v$($Matches[1])" }
    } catch { }
    return $null
}

# 查询 GitHub 最新正式版的 tag：releases/latest 会 302 到 .../tag/vX.Y.Z，
# 不走 API，避免匿名调用的频率限制。失败时返回 $null
function Resolve-LatestMihomo {
    if ((Test-Path $LatestCache) -and ((Get-Item $LatestCache).LastWriteTime -gt (Get-Date).AddDays(-1))) {
        return (Get-Content $LatestCache -Raw).Trim()
    }
    $base = 'https://github.com/MetaCubeX/mihomo/releases/latest'
    # 设置了加速前缀时先走前缀，再直连兜底
    $prefixes = @()
    if ($env:NS_GITHUB_PROXY) { $prefixes += $env:NS_GITHUB_PROXY }
    $prefixes += ''
    foreach ($prefix in $prefixes) {
        try {
            $req = [Net.HttpWebRequest]::Create("$prefix$base")
            $req.AllowAutoRedirect = $false
            $req.Method = 'HEAD'
            $req.Timeout = 10000
            $resp = $req.GetResponse()
            $loc = $resp.Headers['Location']
            $resp.Close()
            if ($loc -match '/tag/(v\d+\.\d+\.\d+)$') {
                New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
                Set-Content -Path $LatestCache -Value $Matches[1]
                return $Matches[1]
            }
        } catch { }
    }
    return $null
}

# 下载指定版本到 bin\mihomo.exe；成功返回 $true，失败只打印原因，由调用方决定是否退出
function Get-Mihomo($version) {
    # 32 位 PowerShell 跑在 64 位系统上时，真实架构在 PROCESSOR_ARCHITEW6432 里
    $archRaw = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
    switch ($archRaw) {
        'AMD64' { $arch = 'amd64-compatible' }   # compatible 版不依赖较新的 CPU 指令集
        'ARM64' { $arch = 'arm64' }
        'x86'   { $arch = '386' }
        default { Write-Host "不支持的 CPU 架构 ${archRaw}，请手动下载 mihomo 后设置 NS_MIHOMO_BIN" -ForegroundColor Red; return $false }
    }
    $asset = "mihomo-windows-$arch-$version.zip"
    $url = "$($env:NS_GITHUB_PROXY)https://github.com/MetaCubeX/mihomo/releases/download/$version/$asset"

    Info "下载 mihomo ${version}（windows/${arch}）"
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $zip = Join-Path $BinDir '.mihomo-download.zip'
    $tmpDir = Join-Path $BinDir '.mihomo-extract'
    try {
        try {
            Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        } catch {
            Write-Host ("下载失败：$url`n  访问 GitHub 慢可设置加速前缀后重试（cmd 中执行）：`n" +
                        "    set `"NS_GITHUB_PROXY=https://ghfast.top/`"`n    start.bat") -ForegroundColor Red
            return $false
        }
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        try {
            Expand-Archive -Path $zip -DestinationPath $tmpDir -Force
        } catch {
            Write-Host '下载的压缩包损坏，无法解压' -ForegroundColor Red
            return $false
        }
        $exe = Get-ChildItem $tmpDir -Filter '*.exe' -Recurse | Select-Object -First 1
        if (-not $exe -or -not (Get-MihomoVersion $exe.FullName)) {
            Write-Host '下载的 mihomo 无法运行' -ForegroundColor Red
            return $false
        }
        # 内核正在被别的实例使用时替换会失败，此时保留旧内核继续用
        try {
            Move-Item $exe.FullName (Join-Path $BinDir 'mihomo.exe') -Force
        } catch {
            Write-Host "替换 bin\mihomo.exe 失败（可能正在运行），本次继续使用旧内核" -ForegroundColor Yellow
            return $false
        }
        return $true
    } finally {
        Remove-Item $zip, $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if ($env:NS_MIHOMO_BIN) {
    if (-not (Test-Path $env:NS_MIHOMO_BIN)) { Die "NS_MIHOMO_BIN 指向的文件不存在：$($env:NS_MIHOMO_BIN)" }
    $Mihomo = $env:NS_MIHOMO_BIN
} else {
    $local = Join-Path $BinDir 'mihomo.exe'
    $want = if ($env:NS_MIHOMO_VERSION) { $env:NS_MIHOMO_VERSION } else { Resolve-LatestMihomo }
    if ($want -match '^\d') { $want = "v$want" }   # 允许写成 1.19.33
    $have = if (Test-Path $local) { Get-MihomoVersion $local } else { $null }
    if ($want -and $have -ne $want) {
        if ($have) { Info "内核 $have 有新版本 ${want}，开始更新" }
        Get-Mihomo $want | Out-Null
    } elseif (-not $want -and -not (Test-Path $local)) {
        # 查不到最新版本（离线/GitHub 不可达）且本地没有内核：下载内置的兜底版本
        Get-Mihomo $FallbackMihomoVersion | Out-Null
    }

    if (Test-Path $local) {
        $Mihomo = $local
    } else {
        # 下载失败时退回系统里已装的内核，能用总比启动不了好
        $Mihomo = $null
        foreach ($name in @('mihomo', 'clash-meta')) {
            $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cmd) { $Mihomo = $cmd.Path; break }
        }
        if (-not $Mihomo) { Die '没有可用的 mihomo 内核。可设置 NS_GITHUB_PROXY 后重试，或手动下载后设置 NS_MIHOMO_BIN' }
        Write-Host '提示：未能下载最新内核，改用系统内核（版本过旧时 xhttp 等新协议不可用）' -ForegroundColor Yellow
    }
}
$env:NS_MIHOMO_BIN = $Mihomo
Info "使用内核 $Mihomo $(Get-MihomoVersion $Mihomo)"

# ---------- 4. 启动 ----------
$env:PYTHONUTF8 = '1'   # 避免中文 Windows 默认 GBK 编码读写文件出错
& $VenvPy (Join-Path $Root 'run.py') --serve @args
exit $LASTEXITCODE
