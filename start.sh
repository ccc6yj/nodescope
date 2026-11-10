#!/usr/bin/env bash
# 一键启动 Web 界面：自动准备 Python 虚拟环境、依赖和 mihomo 内核，然后执行 run.py --serve。
# 用法：./start.sh [run.py 的其他参数，如 --port 9000]
#
# 可选环境变量：
#   NS_MIHOMO_BIN      已有的 mihomo 路径（设置后不再查找/下载）
#   NS_MIHOMO_VERSION  固定下载的 mihomo 版本，如 v1.19.33（默认自动跟随最新正式版）
#   NS_GITHUB_PROXY    GitHub 下载加速前缀，如 https://ghfast.top/
#   NS_MIHOMO_SHA256   手动指定下载包（.gz）的 SHA256；默认从 api.github.com 取官方值做校验
#   PIP_INDEX_URL      pip 镜像，如 https://pypi.tuna.tsinghua.edu.cn/simple
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
VENV="$ROOT/.venv"
BIN_DIR="$ROOT/bin"
# 查不到最新版本又没有任何可用内核时，才退回下载这个版本
FALLBACK_MIHOMO_VERSION="v1.19.33"
# 最新版本号的缓存：一天内只联网查一次，避免每次启动都等 GitHub
LATEST_CACHE="$BIN_DIR/.mihomo-latest"

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
die()  { printf '\033[31m错误：\033[0m%s\n' "$*" >&2; exit 1; }

# ---------- 1. Python ----------
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 &&
     "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  case "$(uname -s)" in
    Darwin) die "未找到 Python 3.8+。可执行 xcode-select --install 或 brew install python 后重试" ;;
    *)      die "未找到 Python 3.8+。请用系统包管理器安装，如 sudo apt install python3 python3-venv" ;;
  esac
fi

# ---------- 2. 虚拟环境与依赖 ----------
if [ ! -x "$VENV/bin/python" ]; then
  info "创建虚拟环境 .venv"
  "$PY" -m venv "$VENV" || die "创建虚拟环境失败。Debian/Ubuntu 需先 sudo apt install python3-venv"
fi
# requirements.txt 没变就跳过安装，只在首次或依赖变更时联网
STAMP="$VENV/.requirements.installed"
if ! cmp -s "$ROOT/requirements.txt" "$STAMP"; then
  info "安装 Python 依赖"
  "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$ROOT/requirements.txt" \
    || die "依赖安装失败。网络慢可设置 PIP_INDEX_URL 使用国内镜像后重试"
  cp "$ROOT/requirements.txt" "$STAMP"
fi

# ---------- 3. mihomo 内核 ----------
# 默认使用项目 bin/ 下的内核并保持为最新正式版（旧内核缺新协议，如 xhttp 需 v1.19.22+）；
# 设置 NS_MIHOMO_BIN 时完全尊重用户选择，不查找也不下载。

# 打印内核版本号（如 v1.19.33）；alpha 版或无法运行时输出空
mihomo_version() {
  { "$1" -v 2>/dev/null || true; } |
    sed -n 's/.*Meta v\{0,1\}\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/v\1/p' | head -n 1 || true
}

# 查询 GitHub 最新正式版的 tag：releases/latest 会 302 到 .../tag/vX.Y.Z，
# 不走 API，避免匿名调用的频率限制。失败时输出空
resolve_latest() {
  local base="https://github.com/MetaCubeX/mihomo/releases/latest" prefix loc tag
  if [ -f "$LATEST_CACHE" ] && [ -n "$(find "$LATEST_CACHE" -mmin -1440 2>/dev/null)" ]; then
    cat "$LATEST_CACHE"; return
  fi
  command -v curl >/dev/null 2>&1 || return 0
  # 设置了加速前缀时先走前缀，再直连兜底
  for prefix in ${NS_GITHUB_PROXY:+"$NS_GITHUB_PROXY"} ""; do
    loc="$(curl -sS -o /dev/null --connect-timeout 5 -m 10 -w '%{redirect_url}' "$prefix$base" 2>/dev/null || true)"
    tag="${loc##*/tag/}"
    if [ "$tag" != "$loc" ] && printf '%s' "$tag" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$'; then
      mkdir -p "$BIN_DIR" && printf '%s\n' "$tag" > "$LATEST_CACHE"
      echo "$tag"; return
    fi
  done
  return 0
}

# 输出官方发布的下载包 SHA256（小写十六进制），取不到时输出空。
# 直连 api.github.com 读 release 资产的 digest，刻意不走 NS_GITHUB_PROXY：
# 校验值和文件若来自同一个第三方加速站，对方改了文件也能一并改掉校验值。
expected_sha256() {
  local version="$1" asset="$2"
  if [ -n "${NS_MIHOMO_SHA256:-}" ]; then
    printf '%s\n' "$NS_MIHOMO_SHA256" | tr 'A-F' 'a-f'; return
  fi
  curl -fsS --connect-timeout 5 -m 15 -H 'Accept: application/vnd.github+json' \
      "https://api.github.com/repos/MetaCubeX/mihomo/releases/tags/$version" 2>/dev/null |
    "$PY" -c '
import json, sys
try:
    assets = json.load(sys.stdin).get("assets") or []
except Exception:
    sys.exit(0)
for a in assets:
    digest = str(a.get("digest") or "")
    if a.get("name") == sys.argv[1] and digest.startswith("sha256:"):
        print(digest[7:].lower())
        break
' "$asset" 2>/dev/null || true
}

file_sha256() {
  "$PY" -c '
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
print(h.hexdigest())
' "$1"
}

# 下载指定版本到 bin/mihomo；成功返回 0，失败只打印原因并返回 1，由调用方决定是否退出
download_mihomo() {
  local version="$1" os arch asset url tmp want_sum got_sum
  case "$(uname -s)" in
    Darwin) os=darwin ;;
    Linux)  os=linux ;;
    *) echo "当前系统不支持自动下载 mihomo，请手动下载后设置 NS_MIHOMO_BIN" >&2; return 1 ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    # compatible 版不依赖较新的 CPU 指令集，老机器也能跑
    x86_64|amd64)  arch=amd64-compatible ;;
    *) echo "不支持的 CPU 架构 $(uname -m)，请手动下载 mihomo 后设置 NS_MIHOMO_BIN" >&2; return 1 ;;
  esac
  command -v curl >/dev/null 2>&1 || { echo "需要 curl 来下载 mihomo" >&2; return 1; }
  asset="mihomo-$os-$arch-$version.gz"
  url="${NS_GITHUB_PROXY:-}https://github.com/MetaCubeX/mihomo/releases/download/$version/$asset"

  info "下载 mihomo ${version}（$os/${arch}）"
  mkdir -p "$BIN_DIR"
  tmp="$(mktemp "$BIN_DIR/.mihomo.XXXXXX")"
  # 下载中途 Ctrl+C 或解压失败时，不在 bin/ 里留下半成品
  trap 'rm -f "$tmp" "$tmp.gz"' EXIT
  if ! curl -fL --retry 2 --connect-timeout 15 --progress-bar -o "$tmp.gz" "$url"; then
    rm -f "$tmp" "$tmp.gz"; trap - EXIT
    echo "下载失败：$url
  访问 GitHub 慢可设置加速前缀后重试，如：NS_GITHUB_PROXY=https://ghfast.top/ ./start.sh" >&2
    return 1
  fi
  # 解压、试运行之前先校验：下载到的是要在本机执行的二进制，经第三方加速站时尤其不能直接信
  want_sum="$(expected_sha256 "$version" "$asset")"
  if [ -n "$want_sum" ]; then
    got_sum="$(file_sha256 "$tmp.gz")"
    if [ "$got_sum" != "$want_sum" ]; then
      rm -f "$tmp" "$tmp.gz"; trap - EXIT
      echo "mihomo 下载包校验失败，已丢弃（文件可能被篡改或下载不完整）：
  期望 SHA256 $want_sum
  实际 SHA256 $got_sum
  来源 $url" >&2
      return 1
    fi
  else
    printf '\033[33m提示：\033[0m未能从 api.github.com 取得官方 SHA256，本次下载未做完整性校验（可用 NS_MIHOMO_SHA256 手动指定）\n' >&2
  fi
  if ! gunzip -c "$tmp.gz" > "$tmp" || ! chmod +x "$tmp" || ! "$tmp" -v >/dev/null 2>&1; then
    rm -f "$tmp" "$tmp.gz"; trap - EXIT
    echo "下载的 mihomo 无法运行" >&2
    return 1
  fi
  rm -f "$tmp.gz"
  # 先写临时文件再整体替换，替换前即使失败也不会破坏原有可用的内核
  mv "$tmp" "$BIN_DIR/mihomo"
  trap - EXIT
}

if [ -n "${NS_MIHOMO_BIN:-}" ]; then
  [ -x "$NS_MIHOMO_BIN" ] || die "NS_MIHOMO_BIN 指向的文件不存在或不可执行：$NS_MIHOMO_BIN"
  MIHOMO="$NS_MIHOMO_BIN"
else
  LOCAL="$BIN_DIR/mihomo"
  WANT="${NS_MIHOMO_VERSION:-$(resolve_latest)}"
  case "$WANT" in [0-9]*) WANT="v$WANT" ;; esac  # 允许写成 1.19.33
  HAVE=""
  [ -x "$LOCAL" ] && HAVE="$(mihomo_version "$LOCAL")"
  if [ -n "$WANT" ] && [ "$HAVE" != "$WANT" ]; then
    [ -n "$HAVE" ] && info "内核 $HAVE 有新版本 ${WANT}，开始更新"
    download_mihomo "$WANT" || true
  elif [ -z "$WANT" ] && [ ! -x "$LOCAL" ]; then
    # 查不到最新版本（离线/GitHub 不可达）且本地没有内核：下载内置的兜底版本
    download_mihomo "$FALLBACK_MIHOMO_VERSION" || true
  fi

  if [ -x "$LOCAL" ]; then
    MIHOMO="$LOCAL"
  else
    # 下载失败时退回系统里已装的内核，能用总比启动不了好
    MIHOMO=""
    for name in mihomo clash-meta; do
      if command -v "$name" >/dev/null 2>&1; then MIHOMO="$(command -v "$name")"; break; fi
    done
    [ -n "$MIHOMO" ] || die "没有可用的 mihomo 内核。可设置 NS_GITHUB_PROXY 后重试，或手动安装（macOS：brew install mihomo）"
    printf '\033[33m提示：\033[0m未能下载最新内核，改用系统内核（版本过旧时 xhttp 等新协议不可用）\n' >&2
  fi
fi
export NS_MIHOMO_BIN="$MIHOMO"
info "使用内核 $MIHOMO $(mihomo_version "$MIHOMO")"

# ---------- 4. 启动 ----------
# macOS 自带 Python 链接的是 LibreSSL，urllib3 v2 会打印一条无害的兼容性警告，这里屏蔽掉
export PYTHONWARNINGS="${PYTHONWARNINGS:+$PYTHONWARNINGS,}ignore:urllib3 v2 only supports OpenSSL"
exec "$VENV/bin/python" "$ROOT/run.py" --serve "$@"
