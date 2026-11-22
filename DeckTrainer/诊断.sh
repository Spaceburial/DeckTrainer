#!/usr/bin/env bash
# DeckTrainer 诊断脚本：在桌面模式 Konsole 里执行  bash ~/DeckTrainer/诊断.sh
# 结果同时写入 诊断报告.txt，可直接发回排查。
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$HERE/诊断报告.txt"
: > "$OUT"

say() { echo "$*" | tee -a "$OUT"; }
run() { echo "---- $* ----" | tee -a "$OUT"; "$@" 2>&1 | tee -a "$OUT"; }

say "DeckTrainer 诊断 $(date '+%F %T')"
say ""

run uname -a
if [ -f /etc/os-release ]; then run grep -E "^(NAME|VERSION|BUILD_ID)=" /etc/os-release; fi

say ""
say "== 目录文件 =="
run ls -la "$HERE"

say ""
say "== Python =="
if command -v python3 >/dev/null 2>&1; then
  run python3 -V
else
  say "python3: 不存在（将依赖自带可执行文件 DeckTrainer）"
fi
if [ -x "$HERE/DeckTrainer" ]; then
  say "自带可执行文件: 存在"
  run "$HERE/DeckTrainer" --version
else
  say "自带可执行文件: 缺失"
fi

say ""
say "== ptrace 限制 =="
run cat /proc/sys/kernel/yama/ptrace_scope

say ""
say "== 端口 =="
run ss -ltn
run cat "$HERE/last_url.txt"

say ""
say "== 当前 .exe 进程（Proton/Wine 游戏） =="
run bash -c "ps -e -o pid,user,rss,comm,args --sort=-rss | grep -iE '\.exe|wine|proton' | head -20"

say ""
say "== 最近运行日志 =="
run bash -c "tail -40 '$HERE/运行日志.txt'"

say ""
say "== Steam 启动选项（含 DeckTrainer 的行） =="
for f in "$HOME/.steam/steam/userdata"/*/config/localconfig.vdf; do
  [ -f "$f" ] || continue
  echo "-- $f" | tee -a "$OUT"
  grep -n -B2 -A1 "DeckTrainer" "$f" 2>/dev/null | tee -a "$OUT" || say "（未找到 DeckTrainer 启动项）"
done

say ""
say "报告已保存：$OUT"
read -r -p "按回车关闭..." _
