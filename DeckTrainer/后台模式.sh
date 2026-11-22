#!/usr/bin/env bash
# DeckTrainer 后台自动识别模式（v2.2）
# 不需要 Steam 启动选项：先运行本脚本，再正常启动任何游戏，
# 修改器会自动发现并附加游戏，自动识别游戏/引擎并恢复收藏。
# 关闭：pkill -f 'DeckTrainer --hub'   （或用 诊断.sh 里的说明）
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${DECKTRAINER_PORT:-8766}"
LOG="$HERE/后台日志.txt"

RUN=()
if [ -x "$HERE/DeckTrainer" ]; then
  RUN=("$HERE/DeckTrainer")
elif command -v python3 >/dev/null 2>&1; then
  RUN=(python3 "$HERE/trainer.py")
else
  echo "[X] 缺少 DeckTrainer 可执行文件，也没有 python3。"
  exit 1
fi

if pgrep -f "DeckTrainer --hub" >/dev/null 2>&1 || pgrep -f "trainer.py --hub" >/dev/null 2>&1; then
  echo "[i] 后台修改器已经在运行。"
else
  nohup "${RUN[@]}" --hub --lan --port "$PORT" >>"$LOG" 2>&1 &
  sleep 1
fi

echo "===================================================="
if [ -f "$HERE/last_url.txt" ]; then
  echo " 后台修改器已启动，浏览器打开："
  sed 's/^/   /' "$HERE/last_url.txt"
else
  echo " 后台修改器已启动，端口 $PORT"
fi
echo " 现在正常启动游戏即可，会自动识别并附加。"
echo " 日志：$LOG"
echo "===================================================="
