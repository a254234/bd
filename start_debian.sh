#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$APP_DIR/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "尚未安装依赖，请先运行：$APP_DIR/install_debian.sh" >&2
  exit 1
fi

exec "$PYTHON" "$APP_DIR/webapp.py" --no-browser "$@"
