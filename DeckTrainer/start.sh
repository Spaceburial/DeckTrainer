#!/usr/bin/env bash
# DeckTrainer 启动脚本
# 用法A（推荐，无需密码）：Steam → 游戏属性 → 启动选项：
#   bash "/home/deck/DeckTrainer/start.sh" %command%
# 用法B（桌面模式）：直接运行本脚本；附加已运行的游戏需要 sudo 密码
#   （SteamOS 默认 deck 用户没有密码，需先在 Konsole 执行 passwd 设置）
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${DECKTRAINER_PORT:-8766}"

# 优先使用自带的可执行文件（不需要系统 Python）
if [ -x "$HERE/DeckTrainer" ]; then
  RUN="$HERE/DeckTrainer"
elif command -v python3 >/dev/null 2>&1; then
  RUN="python3 $HERE/trainer.py"
else
  echo "[X] 本目录缺少可执行文件 DeckTrainer，系统也没有 python3。"
  echo "    请确认解压完整（DeckTrainer 文件夹里应有名为 DeckTrainer 的可执行文件）。"
  echo "    或安装 python：sudo steamos-readonly disable && sudo pacman -S --noconfirm python"
  read -r -p "按回车退出..." _
  exit 1
fi

if [ "$#" -gt 0 ]; then
  # 来自 Steam 启动选项：后面是游戏启动命令（无需 sudo，修改器是游戏父进程）
  exec $RUN --port "$PORT" --lan --launch "$@"
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "[i] 桌面模式附加已运行的游戏需要管理员权限。"
  echo "    提示密码错误/没有密码时：先在 Konsole 执行  passwd  设置一个密码，"
  echo "    或者直接用『游戏模式启动选项』方式（完全不需要密码）。"
  exec sudo -E $RUN --port "$PORT" --lan
fi
exec $RUN --port "$PORT" --lan
