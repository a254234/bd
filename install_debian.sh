#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$APP_DIR/.venv"
MIRROR="${PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "此脚本只适用于 Debian/Linux。" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3，请先安装：sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
  echo "缺少 Python 虚拟环境组件，请先安装：sudo apt update && sudo apt install -y python3-venv python3-pip"
  exit 1
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -i "$MIRROR" -r "$APP_DIR/requirements-debian.txt"

chmod +x "$APP_DIR/start_debian.sh"
echo "安装完成。启动命令："
echo "  $APP_DIR/start_debian.sh"
echo "默认监听端口：5000，局域网地址会在启动窗口中显示。"
