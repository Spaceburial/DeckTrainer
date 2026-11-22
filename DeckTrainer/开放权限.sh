#!/usr/bin/env bash
# 一次性开放调试权限（仅后台自动识别模式需要）
# 说明：SteamOS 默认的 deck 用户没有密码，本脚本会先用 sudo 设置 ptrace 权限；
#       如果提示要密码，请先在 Konsole 执行  passwd  设一个密码，再运行本脚本。
set -u
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
else
  SUDO="sudo"
fi

echo "== 当前状态 =="
cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null || echo "该内核没有 yama 限制"

if [ -n "$SUDO" ] && ! sudo -n true 2>/dev/null; then
  echo "[i] 接下来会要求输入密码（deck 用户默认没有密码，先执行 passwd 设置）。"
fi

$SUDO sysctl -w kernel.yama.ptrace_scope=0 || {
  echo "[X] 设置失败。可改用『游戏模式启动选项』方式，它不需要这个权限。"
  exit 1
}
echo "kernel.yama.ptrace_scope=0" | $SUDO tee /etc/sysctl.d/99-decktrainer.conf >/dev/null 2>&1 || true

echo "== 完成，当前状态 =="
cat /proc/sys/kernel/yama/ptrace_scope 2>/dev/null
echo "现在后台自动识别模式可以附加任何游戏进程。"
