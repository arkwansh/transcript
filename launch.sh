#!/bin/bash
# 稿 — 启动脚本
# 环境全在 ~/Library/Application Support/Transcript/ 下面（模型和 whisper.cpp 在那），
# 代码里没有任何写死的绝对路径，所以整个「稿」文件夹可以随便挪位置。

set -u

# 旧版本用的是 GaoZhi 目录，改名后第一次启动把整个目录搬过去，
# 不然老用户打开会看到空库、模型还得重下。
OLD_DATA="$HOME/Library/Application Support/GaoZhi"
NEW_DATA="$HOME/Library/Application Support/Transcript"
if [ -d "$OLD_DATA" ] && [ ! -d "$NEW_DATA" ]; then
  mv "$OLD_DATA" "$NEW_DATA" 2>/dev/null || true
fi

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$SELF" == *".app/Contents/Resources" ]]; then
  APP_DIR="$(dirname "$(dirname "$(dirname "$SELF")")")"
  CODE="$SELF/app"
else
  APP_DIR="$SELF"
  CODE="$SELF/app"
fi

# app/vendor 里带着 PySocks —— yt-dlp 走 SOCKS 代理时要它
export PYTHONPATH="$CODE/vendor${PYTHONPATH:+:$PYTHONPATH}"

DATA="$HOME/Library/Application Support/Transcript"
WDIR="$DATA/whisper"
PORT="${TRANSCRIPT_PORT:-${GAOZHI_PORT:-8765}}"
LOG="$APP_DIR/launch.log"
mkdir -p "$DATA"

say() { echo "[$(date '+%H:%M:%S')] $*" >>"$LOG"; }

alert() {
  osascript -e "display alert \"$1\" message \"$2\"" 2>/dev/null || true
}

exec >>"$LOG" 2>&1
say "启动  工作目录=$APP_DIR  代码=$CODE"

# ---------------------------------------------------------------- 找 python
pick_python() {
  local c
  for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$c" ]; then echo "$c"; return; fi
  done
}

PY="$(pick_python)"
if [ -z "$PY" ]; then
  alert "缺少 Python 3" "终端里运行 xcode-select --install，然后再双击 安装.command"
  exit 1
fi
say "python = $PY ($("$PY" -V 2>&1))"

# ---------------------------------------------------------------- 环境齐不齐
which_cli() {
  if [ -x "$WDIR/bin/whisper-cli" ]; then echo "$WDIR/bin/whisper-cli"; return; fi
  command -v whisper-cli 2>/dev/null && return
  [ -x /opt/homebrew/bin/whisper-cli ] && echo /opt/homebrew/bin/whisper-cli && return
  [ -x /usr/local/bin/whisper-cli ] && echo /usr/local/bin/whisper-cli
}

CLI="$(which_cli)"
# 有一个模型就算齐了：具体用哪个由设置里的「转写模型」决定
MODEL="$(ls "$WDIR"/ggml-*.bin 2>/dev/null | head -1)"

say "引擎 = ${CLI:-无}  模型 = ${MODEL:-无}"

env_ok() {
  "$PY" -c "import flask, yt_dlp" >/dev/null 2>&1 || return 1
  [ -n "$CLI" ] || return 1
  [ -n "$MODEL" ] || return 1
  return 0
}

if ! env_ok; then
  say "环境不齐，转入安装"
  # 从 .app 双击时没有终端，转去可见的安装窗口
  if [ ! -t 1 ]; then
    if [ -f "$APP_DIR/安装.command" ]; then
      say "打开 Terminal 运行 安装.command"
      open -a Terminal "$APP_DIR/安装.command"
    else
      alert "还没装好运行环境" "在这个文件夹里双击「安装.command」，跟着提示走一遍。"
    fi
    exit 0
  fi
  exec "$APP_DIR/安装.command" --no-pause
fi

# ---------------------------------------------------------------- ffmpeg
if ! command -v ffmpeg >/dev/null 2>&1; then
  for c in /opt/homebrew/bin/ffmpeg /usr/local/bin/ffmpeg; do
    if [ -x "$c" ]; then export PATH="$(dirname "$c"):$PATH"; break; fi
  done
fi
say "ffmpeg = $(command -v ffmpeg || echo 无)"

# ---------------------------------------------------------------- 已经在跑？
if curl -s --max-time 1 "http://127.0.0.1:$PORT/api/library" >/dev/null 2>&1; then
  say "服务已在运行，直接开浏览器"
  open "http://127.0.0.1:$PORT/"
  exit 0
fi

# ---------------------------------------------------------------- 起服务
say "起服务，端口 $PORT"
TRANSCRIPT_PORT="$PORT" TRANSCRIPT_DATA="$DATA" "$PY" "$CODE/server.py" &
SERVER_PID=$!

UP=0
for _ in $(seq 1 80); do
  if ! kill -0 $SERVER_PID 2>/dev/null; then break; fi
  if curl -s --max-time 1 "http://127.0.0.1:$PORT/api/library" >/dev/null 2>&1; then
    UP=1; break
  fi
  sleep 0.25
done

if [ $UP -eq 1 ]; then
  open "http://127.0.0.1:$PORT/"
  say "已打开浏览器"
else
  TAIL="$(tail -4 "$LOG" | sed 's/"/ /g' | tr '\n' ' ' | cut -c1-220)"
  say "启动失败"
  alert "服务没能起来" "$TAIL"
  exit 1
fi

wait $SERVER_PID
say "服务结束"
