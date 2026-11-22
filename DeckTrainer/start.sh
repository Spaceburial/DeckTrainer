#!/usr/bin/env bash
# DeckTrainer 启动脚本 v2.2
# 用法A（游戏模式，推荐）：Steam → 游戏属性 → 启动选项：
#   bash /home/deck/DeckTrainer/start.sh %command%
#   即使修改器自身出问题，游戏也一定会照常启动。
# 用法B（桌面模式）：直接运行本脚本；附加已运行游戏通常需要密码
#   （SteamOS 默认 deck 用户没有密码，可先执行 passwd 设置）
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${DECKTRAINER_PORT:-8766}"
LOG="$HERE/运行日志.txt"

log() { echo "[$(date '+%F %T')] $*" >>"$LOG" 2>/dev/null || true; }

# 选择运行方式：优先自带可执行文件，其次系统 python3
RUN=()
if [ -x "$HERE/DeckTrainer" ]; then
  RUN=("$HERE/DeckTrainer")
elif command -v python3 >/dev/null 2>&1; then
  RUN=(python3 "$HERE/trainer.py")
fi

usable() {
  [ ${#RUN[@]} -gt 0 ] || return 1
  if command -v timeout >/dev/null 2>&1; then
    timeout 15 "${RUN[@]}" --version >/dev/null 2>&1
  else
    "${RUN[@]}" --version >/dev/null 2>&1
  fi
}

OK=1
usable || OK=0
if [ "$OK" -eq 0 ]; then
  log "修改器不可用（可执行文件无法运行且没有 python3），游戏将直接启动"
  echo "[!] 修改器不可用：$HERE/DeckTrainer 无法执行，系统也没有 python3。"
  echo "    游戏仍会正常启动，但本次没有修改器。请查看 $LOG 或运行 诊断.sh。"
fi

if [ "$#" -gt 0 ]; then
  # 来自 Steam 启动选项：先由修改器把游戏拉起来（它是游戏父进程，免密码）
  log "游戏模式启动：$*"
  if [ "$OK" -eq 1 ]; then
    # 修改器内部保证：先启动游戏，再初始化界面；端口冲突不会影响游戏
    exec "${RUN[@]}" --port "$PORT" --lan --launch "$@"
  fi
  exec "$@"
fi

# 桌面/后台模式
if [ "$OK" -eq 0 ]; then
  echo "[X] 找不到可用的修改器。请确认解压完整：$HERE/DeckTrainer 应存在且可执行。"
  read -r -p "按回车退出..." _ || true
  exit 1
fi

log "桌面模式启动"
if [ "$(id -u)" -ne 0 ]; then
  if sudo -n true >/dev/null 2>&1; then
    exec sudo -E "${RUN[@]}" --port "$PORT" --lan
  fi
  echo "[i] 当前用户没有免密 sudo。"
  echo "    如果附加进程提示权限不足：先在 Konsole 执行  passwd  设置密码，再重新运行本脚本；"
  echo "    或者用游戏模式启动选项（完全不需要密码）。"
  exec "${RUN[@]}" --port "$PORT" --lan
fi
exec "${RUN[@]}" --port "$PORT" --lan
