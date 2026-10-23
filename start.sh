#!/usr/bin/env bash
# 一键启动 Web 界面：自动准备 Python 虚拟环境、依赖和 mihomo 内核，然后执行 run.py --serve。
# 用法：./start.sh [run.py 的其他参数，如 --port 9000]
#
# 可选环境变量：
#   NS_MIHOMO_BIN      已有的 mihomo 路径（设置后不再查找/下载）
#   NS_MIHOMO_VERSION  下载的 mihomo 版本（默认见下方 MIHOMO_VERSION）
#   NS_GITHUB_PROXY    GitHub 下载加速前缀，如 https://ghfast.top/
#   PIP_INDEX_URL      pip 镜像，如 https://pypi.tuna.tsinghua.edu.cn/simple
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
VENV="$ROOT/.venv"
BIN_DIR="$ROOT/bin"
MIHOMO_VERSION="${NS_MIHOMO_VERSION:-v1.19.31}"

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
find_mihomo() {
  if [ -n "${NS_MIHOMO_BIN:-}" ] && [ -x "$NS_MIHOMO_BIN" ]; then echo "$NS_MIHOMO_BIN"; return; fi
  for name in mihomo clash-meta; do
    if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return; fi
  done
  if [ -x "$BIN_DIR/mihomo" ]; then echo "$BIN_DIR/mihomo"; fi
  return 0
}

download_mihomo() {
  local os arch asset url tmp
  case "$(uname -s)" in
    Darwin) os=darwin ;;
    Linux)  os=linux ;;
    *) die "当前系统不支持自动下载 mihomo，请手动下载后设置 NS_MIHOMO_BIN" ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    # compatible 版不依赖较新的 CPU 指令集，老机器也能跑
    x86_64|amd64)  arch=amd64-compatible ;;
    *) die "不支持的 CPU 架构 $(uname -m)，请手动下载 mihomo 后设置 NS_MIHOMO_BIN" ;;
  esac
  asset="mihomo-$os-$arch-$MIHOMO_VERSION.gz"
  url="${NS_GITHUB_PROXY:-}https://github.com/MetaCubeX/mihomo/releases/download/$MIHOMO_VERSION/$asset"
  command -v curl >/dev/null 2>&1 || die "需要 curl 来下载 mihomo"

  info "下载 mihomo $MIHOMO_VERSION（$os/$arch）"
  mkdir -p "$BIN_DIR"
  tmp="$(mktemp "$BIN_DIR/.mihomo.XXXXXX")"
  # 下载中途 Ctrl+C 或解压失败时，不在 bin/ 里留下半成品
  trap 'rm -f "$tmp" "$tmp.gz"' EXIT
  if ! curl -fL --retry 2 --connect-timeout 15 --progress-bar -o "$tmp.gz" "$url"; then
    rm -f "$tmp" "$tmp.gz"
    die "下载失败：$url
  访问 GitHub 慢可设置加速前缀后重试，如：NS_GITHUB_PROXY=https://ghfast.top/ ./start.sh
  也可以手动安装（macOS：brew install mihomo）"
  fi
  gunzip -c "$tmp.gz" > "$tmp" && rm -f "$tmp.gz"
  chmod +x "$tmp"
  "$tmp" -v >/dev/null 2>&1 || { rm -f "$tmp"; die "下载的 mihomo 无法运行，请手动安装后设置 NS_MIHOMO_BIN"; }
  mv "$tmp" "$BIN_DIR/mihomo"
  trap - EXIT
}

MIHOMO="$(find_mihomo)"
if [ -z "$MIHOMO" ]; then
  download_mihomo
  MIHOMO="$BIN_DIR/mihomo"
fi
export NS_MIHOMO_BIN="$MIHOMO"
info "使用内核 $MIHOMO"

# ---------- 4. 启动 ----------
# macOS 自带 Python 链接的是 LibreSSL，urllib3 v2 会打印一条无害的兼容性警告，这里屏蔽掉
export PYTHONWARNINGS="${PYTHONWARNINGS:+$PYTHONWARNINGS,}ignore:urllib3 v2 only supports OpenSSL"
exec "$VENV/bin/python" "$ROOT/run.py" --serve "$@"
