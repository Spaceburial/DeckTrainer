#!/usr/bin/env bash
# DeckTrainer 启动脚本（两种用法）
# 用法A（游戏模式，推荐）：Steam → 游戏属性 → 启动选项：
#   bash "/home/deck/DeckTrainer/start.sh" %command%
#   这样修改器会作为游戏父进程自动启动，边玩边用手机网页控制，无需 sudo。
# 用法B（桌面模式）：直接双击/执行本脚本，会自动请求 sudo 后启动。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${DECKTRAINER_PORT:-8766}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[X] 未找到 python3。请在 Konsole 执行："
  echo "    sudo steamos-readonly disable && sudo pacman -S --noconfirm python"
  echo "    然后重新运行本脚本。"
  read -r -p "按回车退出..." _
  exit 1
fi

if [ "$#" -gt 0 ]; then
  # 来自 Steam 启动选项：后面是游戏启动命令
  exec python3 "$HERE/trainer.py" --port "$PORT" --lan --launch "$@"
fi

# 桌面模式：附加已在运行的游戏需要 root 权限
if [ "$(id -u)" -ne 0 ]; then
  echo "[i] 桌面模式下附加 Steam 游戏需要管理员权限。"
  exec sudo -E python3 "$HERE/trainer.py" --port "$PORT" --lan
fi
exec python3 "$HERE/trainer.py" --port "$PORT" --lan
