#!/bin/bash
# 稿 — 安装运行环境
# 双击运行。全程有进度，失败会说清楚是哪一步、怎么修。
#
# 现在的转写引擎是 whisper.cpp：一个二进制 + 一个模型文件，走 Metal。
# 不再需要 venv、ctranslate2、av 这一整套，装完就是装完了。

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SELF"
CODE="$APP_DIR/app"

# 旧版本（叫 GaoZhi 那会儿）的数据目录，改名后第一次跑就整个搬过来，
# 模型和转好的稿子都不用重下。
OLD_DATA="$HOME/Library/Application Support/GaoZhi"
if [ -d "$OLD_DATA" ] && [ ! -d "$HOME/Library/Application Support/Transcript" ]; then
  mv "$OLD_DATA" "$HOME/Library/Application Support/Transcript" 2>/dev/null || true
fi

DATA="$HOME/Library/Application Support/Transcript"
WDIR="$DATA/whisper"
WBIN="$WDIR/bin"
MODEL="ggml-small-q5_1.bin"
MODEL_MB=181
mkdir -p "$DATA" "$WBIN"

# app/vendor 里带着 PySocks —— yt-dlp 走 SOCKS 代理时要它
export PYTHONPATH="$CODE/vendor${PYTHONPATH:+:$PYTHONPATH}"

B=$'\033[1m'; DIM=$'\033[2m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'

NOPAUSE=0
if [ "${1:-}" = "--no-pause" ]; then NOPAUSE=1; fi

pause() {
  if [ "$NOPAUSE" = "1" ]; then return; fi
  echo
  printf "%s按回车关闭这个窗口%s" "$DIM" "$N"
  read -r _
}

hr() { printf "%s──────────────────────────────────────────────%s\n" "$DIM" "$N"; }

# 三个都行：3.9 就够，因为重活已经交给 whisper.cpp 了
py_ok() {
  [ -x "${1:-}" ] || return 1
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' >/dev/null 2>&1
}

find_python() {
  local c
  for c in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3 ; do
    if py_ok "$c"; then echo "$c"; return 0; fi
  done
  return 1
}

# 装包时用的代理（设置里填过就跟着走，没填就直连）
read_proxy() {
  "$PY" - <<'PYX' 2>/dev/null
import json, os, pathlib
p = pathlib.Path(os.path.expanduser("~/Library/Application Support/Transcript/config.json"))
try:
    print((json.loads(p.read_text("utf-8")) or {}).get("proxy", "") or "")
except Exception:
    print("")
PYX
}

# 把 http:// 或 socks5:// 的代理变成 curl 参数
curl_proxy_args() {
  local p="$1"
  case "$p" in
    socks5h://*|socks5://*) echo "--socks5-hostname ${p#*://}" ;;
    http://*|https://*)     echo "--proxy $p" ;;
  esac
}

echo
printf "%s稿 · 安装运行环境%s\n" "$B" "$N"
hr
echo "转写引擎：whisper.cpp（本机 Metal，不联网、不用 Python 环境）"
echo "模型放在：$WDIR"
echo "跟「稿」文件夹分开存，以后整个文件夹随便挪都不会坏。"
hr
echo

# ---------------------------------------------------------------- 1. python
echo "${B}[1/4]${N} 找 Python"
PY="$(find_python || true)"
if [ -z "$PY" ]; then
  echo "${R}没找到 python3。${N}"
  echo "装一下 Xcode 命令行工具就行，终端里运行："
  echo "  xcode-select --install"
  pause
  exit 1
fi
echo "  $PY  $("$PY" -V 2>&1)"
if ! "$PY" -m pip -V >/dev/null 2>&1; then
  echo "${R}这个 Python 没有 pip。${N}"
  echo "换一个 Python，或者先运行： xcode-select --install"
  pause
  exit 1
fi
echo "  ${G}够用${N}"
echo

# ---------------------------------------------------------------- 2. flask + yt-dlp
echo "${B}[2/4]${N} 装 flask 和 yt-dlp"
echo "${DIM}  很小，通常十几秒。装到用户目录，不碰系统。${N}"
if ! "$PY" -m pip install --user --quiet --upgrade flask yt-dlp 2>/dev/null; then
  if ! "$PY" -m pip install --user --quiet --upgrade --break-system-packages flask yt-dlp 2>/dev/null; then
    echo
    echo "${R}没装上，多半是网络。${N}"
    echo "先确认能上网，然后重新双击本文件。"
    pause
    exit 1
  fi
fi
"$PY" -c "import flask, yt_dlp" >/dev/null 2>&1 || {
  echo "${R}装是装完了，但 import 不进来。${N}"
  echo "手动看一眼： $PY -c \"import flask, yt_dlp\""
  pause
  exit 1
}
echo "  ${G}完成${N}"
echo

# ---------------------------------------------------------------- 3. whisper.cpp
echo "${B}[3/4]${N} 准备 whisper.cpp"
CLI=""
if [ -x "$WBIN/whisper-cli" ]; then
  CLI="$WBIN/whisper-cli"
  echo "  已经在 ${WBIN}，跳过"
else
  for c in /opt/homebrew/bin/whisper-cli /usr/local/bin/whisper-cli; do
    if [ -x "$c" ]; then CLI="$c"; break; fi
  done
  if [ -n "$CLI" ]; then
    echo "  系统里已经有了：$CLI"
  fi
fi

if [ -z "$CLI" ]; then
  if command -v brew >/dev/null 2>&1; then
    echo "${DIM}  → 用 Homebrew 装，一两分钟${N}"
    if brew install whisper-cpp; then
      CLI="$(command -v whisper-cli || true)"
      if [ -z "$CLI" ] && [ -x /opt/homebrew/bin/whisper-cli ]; then
        CLI=/opt/homebrew/bin/whisper-cli
      fi
    fi
  fi
fi

if [ -z "$CLI" ]; then
  echo "  ${DIM}没有 Homebrew，改从源码编译一个（约三到五分钟）${N}"
  echo "  ${DIM}编译输出会一直滚，属于正常，不要关窗口。${N}"
  echo

  if ! xcode-select -p >/dev/null 2>&1; then
    echo "${R}缺编译工具（Xcode 命令行工具）。${N}"
    echo "终端里运行这一句，装完再双击本文件："
    echo "  xcode-select --install"
    pause
    exit 1
  fi

  # 先确认 C++ 真能编（几秒钟），别下载编译到一半才炸。
  # 已知坑：有的机器上 clang 在，但它自己那份 libc++ 头
  # （CommandLineTools/usr/include/c++/v1）没装上，于是 <array> 找不到；
  # 而 SDK 里另有一份完整的，把它加进搜索路径就能编。
  CXXFLAGS_EXTRA=""
  TPROBE="$(mktemp -d)"
  printf '#include <array>\n#include <vector>\n#include <string>\n#include <thread>\nint main(){std::array<int,2> a{}; std::vector<int> v{1}; std::string s="x"; return a[0]+v[0]+(int)s.size();}\n' > "$TPROBE/t.cpp"
  if ! /usr/bin/clang++ -std=c++17 "$TPROBE/t.cpp" -o "$TPROBE/t" >/dev/null 2>&1; then
    for CAND in \
      "$(xcrun --show-sdk-path 2>/dev/null)/usr/include/c++/v1" \
      /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1 \
      /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX.sdk/usr/include/c++/v1 ; do
      [ -f "$CAND/array" ] || continue
      if /usr/bin/clang++ -std=c++17 -isystem "$CAND" "$TPROBE/t.cpp" -o "$TPROBE/t" >/dev/null 2>&1; then
        CXXFLAGS_EXTRA="-isystem $CAND"
        echo "  ${DIM}→ C++ 头文件不在默认位置，改用 SDK 里那份（不影响结果）${N}"
        break
      fi
    done
    if [ -z "$CXXFLAGS_EXTRA" ]; then
      rm -rf "$TPROBE"
      echo "${R}C++ 编译不了：找不到标准库头文件。${N}"
      echo "这台机器的 Xcode 命令行工具是坏的。终端里跑这两句重装（要输密码）："
      echo "  sudo rm -rf /Library/Developer/CommandLineTools"
      echo "  xcode-select --install"
      echo "装完重新双击本文件。"
      pause
      exit 1
    fi
  fi
  rm -rf "$TPROBE"

  CMAKE=""
  for c in /opt/homebrew/bin/cmake /usr/local/bin/cmake; do
    if [ -x "$c" ]; then CMAKE="$c"; break; fi
  done
  if [ -z "$CMAKE" ]; then
    CMAKE="$(command -v cmake || true)"
  fi
  if [ -z "$CMAKE" ]; then
    echo "  ${DIM}→ cmake 没有，用 pip 装一个（几十兆）${N}"
    "$PY" -m pip install --user --quiet --upgrade cmake \
      || "$PY" -m pip install --user --quiet --upgrade --break-system-packages cmake
    UB="$("$PY" -m site --user-base 2>/dev/null || echo)"
    if [ -n "$UB" ] && [ -x "$UB/bin/cmake" ]; then CMAKE="$UB/bin/cmake"; fi
    if [ -z "$CMAKE" ]; then CMAKE="$(command -v cmake || true)"; fi
  fi
  if [ -z "$CMAKE" ]; then
    echo "${R}cmake 装不上，没法编译。${N}"
    echo "也可以自己装一个 Homebrew 再来： https://brew.sh"
    pause
    exit 1
  fi
  echo "  cmake = $CMAKE  $("$CMAKE" --version | head -1)"

  TMPD="$(mktemp -d)"
  SRC="https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v1.9.5.tar.gz"
  echo "  ${DIM}→ 下载源码${N}"
  PROXY="$(read_proxy)"
  if ! curl -L --fail --connect-timeout 20 --max-time 600 $(curl_proxy_args "$PROXY") \
        -o "$TMPD/src.tar.gz" "$SRC"; then
    echo "${R}源码没下下来。${N}"
    echo "GitHub 有时连不上。设置里填个代理，或者换网络再试。"
    rm -rf "$TMPD"
    pause
    exit 1
  fi

  echo "  ${DIM}→ 解压${N}"
  tar -xzf "$TMPD/src.tar.gz" -C "$TMPD" || {
    echo "${R}解压失败。${N}"; rm -rf "$TMPD"; pause; exit 1
  }
  SRC_DIR="$(ls -d "$TMPD"/whisper.cpp-*/ 2>/dev/null | head -1)"
  if [ -z "$SRC_DIR" ]; then
    echo "${R}解压后找不到源码目录。${N}"; rm -rf "$TMPD"; pause; exit 1
  fi

  echo "  ${DIM}→ 编译（这一步最慢）${N}"
  # BUILD_SHARED_LIBS=OFF 很关键：不然编出来是个只带主程序的壳，
  # 一拷走就找不到 libwhisper.dylib，自检那一步会「跑不起来」。
  if ! ( cd "$SRC_DIR" && "$CMAKE" -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
           -DCMAKE_CXX_FLAGS="$CXXFLAGS_EXTRA" >/dev/null \
         && "$CMAKE" --build build --config Release --target whisper-cli -j 4 ); then
    echo
    echo "${R}编译失败。${N}"
    echo "把上面最后几十行发给我，能看出是哪一步。"
    echo "也可以先装 Homebrew 再重来： https://brew.sh"
    rm -rf "$TMPD"
    pause
    exit 1
  fi

  cp "$SRC_DIR/build/bin/whisper-cli" "$WBIN/whisper-cli" 2>/dev/null || true
  chmod +x "$WBIN/whisper-cli" 2>/dev/null || true
  rm -rf "$TMPD"
  if [ -x "$WBIN/whisper-cli" ]; then
    CLI="$WBIN/whisper-cli"
  fi
fi

if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
  echo "${R}whisper.cpp 没准备好。${N}"
  pause
  exit 1
fi
echo "  ${G}完成${N}  $CLI"
echo

# ---------------------------------------------------------------- 4. 模型 + 自检
echo "${B}[4/4]${N} 下识别模型"
MPATH="$WDIR/$MODEL"
if [ -s "$MPATH" ]; then
  echo "  模型已经有了（$(du -m "$MPATH" | cut -f1) MB），跳过"
else
  echo "  ${DIM}  ${MODEL}，约 ${MODEL_MB} MB。只下这一次，以后离线可用。${N}"
  PROXY="$(read_proxy)"
  CP="$(curl_proxy_args "$PROXY")"
  OK=0
  for HOST in https://huggingface.co https://hf-mirror.com; do
    echo "  ${DIM}  从 ${HOST} 取${N}"
    if curl -L --fail --connect-timeout 20 --max-time 1800 $CP --progress-bar \
         -o "$MPATH.part" "$HOST/ggerganov/whisper.cpp/resolve/main/$MODEL"; then
      mv "$MPATH.part" "$MPATH"
      OK=1
      break
    fi
    rm -f "$MPATH.part"
  done
  if [ "$OK" != "1" ]; then
    echo
    echo "${R}模型没下下来。${N}"
    echo "两个源都失败，通常是网络。设置里填个代理，或者手动下载："
    echo "  $HOST/ggerganov/whisper.cpp/resolve/main/$MODEL"
    echo "下完放到： $WDIR/"
    pause
    exit 1
  fi
  echo "  ${G}完成${N}  $(du -m "$MPATH" | cut -f1) MB"
fi
echo

echo "${B}自检${N}"
FAIL=0
if "$PY" -c "import flask" >/dev/null 2>&1; then echo "  ${G}✓${N} flask"; else echo "  ${R}✗${N} flask"; FAIL=1; fi
if "$PY" -c "import yt_dlp" >/dev/null 2>&1; then echo "  ${G}✓${N} yt-dlp"; else echo "  ${R}✗${N} yt-dlp"; FAIL=1; fi
if "$CLI" --help >/dev/null 2>&1; then
  echo "  ${G}✓${N} whisper.cpp（$("$CLI" --help 2>&1 | grep -m1 -v '^[[:space:]]*$' | cut -c1-44)）"
else
  echo "  ${R}✗${N} whisper.cpp 跑不起来"
  FAIL=1
fi
if [ -s "$MPATH" ]; then echo "  ${G}✓${N} 模型 $MODEL"; else echo "  ${R}✗${N} 模型"; FAIL=1; fi
if command -v ffmpeg >/dev/null 2>&1; then
  echo "  ${G}✓${N} ffmpeg（转 wav 用它）"
elif [ -x /usr/bin/afconvert ]; then
  echo "  ${G}✓${N} 转 wav 用系统自带的 afconvert（不用装 ffmpeg）"
else
  echo "  ${R}✗${N} 没有能把音频转成 wav 的工具"
  FAIL=1
fi

echo
hr
if [ "$FAIL" = "0" ]; then
  printf "%s装好了。%s\n" "$G" "$N"
  echo
  echo "以后有现成字幕的视频几秒钟出稿，没有再本机转写。"
  hr
  if [ "$NOPAUSE" = "0" ] && [ -t 1 ]; then
    printf "%s直接回车，现在就打开稿%s" "$B" "$N"
    read -r _
    open "$APP_DIR/稿.command" 2>/dev/null || true
    exit 0
  fi
  exit 0
fi

printf "%s还有东西没装好，往上翻看看红字。%s\n" "$R" "$N"
hr
pause
