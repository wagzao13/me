#!/bin/bash
set -u
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
  echo "没有找到 python3。请先在 Mac 上安装 Python 3。"
  read -n 1 -s -r -p "按任意键关闭窗口…"
  exit 1
fi

if lsof -nP -iTCP:8787 -sTCP:LISTEN >/dev/null 2>&1; then
  echo ""
  echo "端口 8787 已经被占用了。"
  echo "请先关掉正在运行的旧服务，或修改 server.py / start.command 里的端口。"
  echo ""
  read -n 1 -s -r -p "按任意键关闭窗口…"
  exit 1
fi

# 服务启动后自动打开主页面
( sleep 1.2; open "http://127.0.0.1:8787" >/dev/null 2>&1 ) &
OPENER_PID=$!

cleanup() {
  kill "$OPENER_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

python3 server.py --host 0.0.0.0 --port 8787

echo ""
echo "服务已停止。"
read -n 1 -s -r -p "按任意键关闭窗口…"
