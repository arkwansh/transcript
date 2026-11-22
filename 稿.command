#!/bin/bash
# 稿 — 双击启动
# 浏览器会自己开。这个窗口显示启动过程和结果，看到「已打开」就可以关掉。
#
# 注意：这里刻意不开 set -u，也不做「取 PID / 解析 lsof 输出」这类操作。
# macOS 自带的是 bash 3.2，在中文全角字符旁边处理变量名会出岔子，
# 宁可写得笨一点，也不要半夜被一句 unbound variable 打断。

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 旧版本的数据在 GaoZhi 目录里，改名后先搬过去再判断环境
OLD_DATA="$HOME/Library/Application Support/GaoZhi"
if [ -d "$OLD_DATA" ] && [ ! -d "$HOME/Library/Application Support/Transcript" ]; then
  mv "$OLD_DATA" "$HOME/Library/Application Support/Transcript" 2>/dev/null || true
fi

DATA="$HOME/Library/Application Support/Transcript"
WDIR="$DATA/whisper"
PORT="${TRANSCRIPT_PORT:-${GAOZHI_PORT:-8765}}"
LOG="$SELF/launch.log"

B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'

hr() { printf "%s──────────────────────────────────────────────%s\n" "$DIM" "$N"; }

wait_key() {
  echo
  printf "%s按回车关闭这个窗口%s" "$DIM" "$N"
  read -r _
}

up() { curl -s --max-time 1 "http://127.0.0.1:$PORT/api/library" >/dev/null 2>&1; }

echo
printf "%s稿%s\n" "$B" "$N"
hr

# ---------------------------------------------------------------- 环境在不在
PY="$(command -v python3 || echo /usr/bin/python3)"
CLI=""
if [ -x "$WDIR/bin/whisper-cli" ]; then CLI="$WDIR/bin/whisper-cli"; fi
if [ -z "$CLI" ]; then CLI="$(command -v whisper-cli 2>/dev/null || true)"; fi
MODEL="$(ls "$WDIR"/ggml-*.bin 2>/dev/null | head -1)"

if ! "$PY" -c "import flask, yt_dlp" >/dev/null 2>&1 || [ -z "$CLI" ] || [ -z "$MODEL" ]; then
  echo "${R}运行环境不完整。${N}"
  if [ -z "$CLI" ]; then echo "${DIM}  缺 whisper.cpp${N}"; fi
  if [ -z "$MODEL" ]; then echo "${DIM}  缺识别模型${N}"; fi
  echo "给你打开安装窗口，装完再双击本文件。"
  open -a Terminal "$SELF/安装.command"
  wait_key
  exit 1
fi

# ---------------------------------------------------------------- 关掉旧的
# 直接按解释器路径杀，不去解析端口和 PID。旧服务不关掉，改动就永远不生效。
if up; then
  echo "关掉正在跑的旧服务"
  pkill -f "$SELF/app/server.py" 2>/dev/null
  i=0
  while [ "$i" -lt 24 ]; do
    up || break
    sleep 0.25
    i=$((i + 1))
  done
  if up; then
    echo
    echo "${R}旧服务一直没退出，端口 ${PORT} 还被占着。${N}"
    echo "${DIM}  最省事的办法：注销一次，或者重启 Mac，再双击本文件。${N}"
    hr
    wait_key
    exit 1
  fi
fi

# ---------------------------------------------------------------- 起新的
echo "启动中…"
echo "${DIM}  第一次起会慢几秒，后面就快了${N}"
: >"$LOG"
nohup "$SELF/launch.sh" >/dev/null 2>&1 &

UP=0
i=0
while [ "$i" -lt 80 ]; do
  if up; then UP=1; break; fi
  sleep 0.5
  i=$((i + 1))
done

echo
if [ "$UP" = "1" ]; then
  echo "${G}已打开${N}  ${DIM}http://127.0.0.1:$PORT/${N}"
  echo "${DIM}服务在后台跑，关掉这个窗口不影响。${N}"
  hr
  wait_key
  exit 0
fi

# ---------------------------------------------------------------- 没起来
echo "${R}服务没起来。${N}"
echo "${DIM}下面是最后几行日志：${N}"
echo
if [ -s "$LOG" ]; then
  tail -15 "$LOG" | sed 's/^/  /'
else
  echo "  ${Y}日志是空的 —— 说明启动脚本根本没执行到写日志那一步。${N}"
fi
echo
hr
wait_key
exit 1
