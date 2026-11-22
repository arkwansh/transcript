---
name: transcript
description: 把视频链接或本地音视频转成可读的 Markdown 文字稿：先取平台现成字幕，取不到再用本机 whisper.cpp 转写，全程不上传任何音频。适合 YouTube、B站、小宇宙等公开链接，也适合讲座、访谈、播客的整理。
---

# 稿 · 视频转文字稿

链接进去，文字稿出来。平台上本来就有字幕的，几秒钟出稿；没有的，下载音频在本机转一遍，音频不出这台机器。

## 什么时候用

用户给了一个视频或播客链接，说想读它的内容；或者要整理讲座、访谈、长视频；或者要把本地录音、录屏变成能搜能摘的文本。触发语大致是：「把这个视频转成文字」「这期讲了什么，我要文字版」「帮我把链接里的内容整理出来」。

不适用：用户只要摘要（也先要转写，见「结果怎么交」）；用户要的是一份分析或观点，不是原文。

用户只丢一个链接、没别的要求时，直接做，不要先问一堆偏好。做完说一句走了哪条路即可。

## 一、环境（一台机器只需做一次）

先探一下，三行都通就不用管这一节：

```bash
D="$HOME/Library/Application Support/Transcript"
ls "$D/whisper/bin/whisper-cli" "$D"/whisper/ggml-*.bin 2>&1
"$(cat "$D/skill/python" 2>/dev/null || echo python3)" -c 'import yt_dlp' && echo yt-dlp-ok
```

不全就装机：把下面的 `setup.sh` 原样存到 `~/Library/Application Support/Transcript/skill/setup.sh`，然后 `bash` 它。它会一步步打印进度，全在用户目录，不要密码。可以重复跑，装到一半断了重跑就是。

装完会记下一个 Python 路径到 `$D/skill/python`，之后所有命令都用这个解释器。

**给用户看的时候**：这一步可能要三到八分钟（从源码编 whisper.cpp 最慢），一定要让输出滚在屏幕上，不要静默跑——看不到进度，用户会以为卡死然后关窗口。编译前它会先花两秒试编一个 C++ 小程序；报「C++ 头不在默认位置」是正常的自动修正，不是错误。

```bash
#!/bin/bash
# 稿 · 转写环境（macOS，幂等，想跑几次跑几次）
# 装的是：yt-dlp、whisper.cpp、一个 small 模型。全在用户目录，不碰系统、不要密码。

DATA="$HOME/Library/Application Support/Transcript"
WH="$DATA/whisper"
# 旧版本的数据目录（叫 GaoZhi 那会儿），改名后第一次跑就整个搬过来
if [ -d "$HOME/Library/Application Support/GaoZhi" ] && [ ! -d "$DATA" ]; then
  mv "$HOME/Library/Application Support/GaoZhi" "$DATA" 2>/dev/null || true
fi
mkdir -p "$WH/bin"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n[%s] %s\n' "$1" "$2"; }

# ---------------------------------------------------------------- 1. python
step 1 "Python"
PY=""
for c in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  bad "找不到 python3。终端里跑 xcode-select --install 装命令行工具，再重来。"
  exit 1
fi
ok "$PY $("$PY" -V 2>&1)"
# 把选定的解释器记下来：gz.py 要用同一个，不然 yt-dlp import 不到
mkdir -p "$DATA/skill" && echo "$PY" > "$DATA/skill/python"

# ---------------------------------------------------------------- 2. yt-dlp
step 2 "yt-dlp"
if "$PY" -c 'import yt_dlp' >/dev/null 2>&1; then
  ok "已经有了"
else
  "$PY" -m pip install --user -q --upgrade yt-dlp 2>/dev/null \
    || "$PY" -m pip install --user -q --upgrade --break-system-packages yt-dlp 2>/dev/null
  if "$PY" -c 'import yt_dlp' >/dev/null 2>&1; then
    ok "装好了"
  else
    bad "装不上，多半是网络。"
    exit 1
  fi
fi

# ---------------------------------------------------------------- 3. whisper.cpp
step 3 "whisper.cpp"
CLI="$WH/bin/whisper-cli"
if [ -x "$CLI" ]; then
  ok "已经在 $CLI"
else
  SYS=""
  for c in /opt/homebrew/bin/whisper-cli /usr/local/bin/whisper-cli; do
    [ -x "$c" ] && SYS="$c" && break
  done
  if [ -n "$SYS" ]; then
    CLI="$SYS"; ok "系统里已有 $CLI"
  elif command -v brew >/dev/null 2>&1; then
    echo "  用 Homebrew 装（一两分钟）"
    brew install whisper-cpp >/dev/null 2>&1 || true
    [ -x /opt/homebrew/bin/whisper-cli ] && CLI=/opt/homebrew/bin/whisper-cli
    [ -x /usr/local/bin/whisper-cli ] && CLI=/usr/local/bin/whisper-cli
    [ -x "$CLI" ] && ok "装好了"
  fi

  if [ ! -x "$CLI" ]; then
    if ! xcode-select -p >/dev/null 2>&1; then
      bad "没有 C++ 编译器。终端里跑这一句（不要密码，下一两个 G，几分钟）："
      echo "     xcode-select --install"
      echo "     装完重新跑本脚本。"
      exit 1
    fi

    # 先花两秒确认 C++ 真能编。已知坑：命令行工具装残时 clang 在，
    # 但它那份 libc++ 头（CommandLineTools/usr/include/c++/v1）没装上，
    # 于是 #include <array> 直接找不到；SDK 里另有一份完整的，挂上去就行。
    CXXF=""
    TP="$(mktemp -d)"
    printf '#include <array>\n#include <vector>\n#include <string>\n#include <thread>\nint main(){std::array<int,2> a{}; std::vector<int> v{1}; std::string s="x"; return a[0]+v[0]+(int)s.size()-1;}\n' > "$TP/t.cpp"
    if ! /usr/bin/clang++ -std=c++17 "$TP/t.cpp" -o "$TP/t" >/dev/null 2>&1; then
      for CAND in \
        "$(xcrun --show-sdk-path 2>/dev/null)/usr/include/c++/v1" \
        /Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1 ; do
        [ -f "$CAND/array" ] || continue
        if /usr/bin/clang++ -std=c++17 -isystem "$CAND" "$TP/t.cpp" -o "$TP/t" >/dev/null 2>&1; then
          CXXF="-isystem $CAND"; echo "  C++ 头不在默认位置，改用 SDK 里那份"; break
        fi
      done
      if [ -z "$CXXF" ]; then
        rm -rf "$TP"
        bad "C++ 编不了。命令行工具坏了，终端里跑这两句重装（要密码）："
        echo "     sudo rm -rf /Library/Developer/CommandLineTools"
        echo "     xcode-select --install"
        exit 1
      fi
    fi
    rm -rf "$TP"

    CMAKE=""
    for c in /opt/homebrew/bin/cmake /usr/local/bin/cmake; do
      [ -x "$c" ] && CMAKE="$c" && break
    done
    [ -z "$CMAKE" ] && CMAKE="$(command -v cmake 2>/dev/null || true)"
    if [ -z "$CMAKE" ]; then
      echo "  cmake 没有，用 pip 装一个"
      "$PY" -m pip install --user -q --upgrade cmake 2>/dev/null \
        || "$PY" -m pip install --user -q --upgrade --break-system-packages cmake 2>/dev/null
      UB="$("$PY" -m site --user-base 2>/dev/null || echo)"
      [ -n "$UB" ] && [ -x "$UB/bin/cmake" ] && CMAKE="$UB/bin/cmake"
    fi
    if [ -z "$CMAKE" ]; then
      bad "cmake 装不上，没法从源码编。装个 Homebrew 再来： https://brew.sh"
      exit 1
    fi

    TMP="$(mktemp -d)"
    echo "  下源码"
    if ! curl -L --fail --connect-timeout 20 --max-time 600 -o "$TMP/src.tgz" \
         "https://github.com/ggml-org/whisper.cpp/archive/refs/tags/v1.9.5.tar.gz"; then
      bad "源码没下下来。GitHub 有时连不上，换个网络或挂梯子再试。"
      rm -rf "$TMP"; exit 1
    fi
    tar -xzf "$TMP/src.tgz" -C "$TMP" || { bad "解压失败"; rm -rf "$TMP"; exit 1; }
    SRCD="$(ls -d "$TMP"/whisper.cpp-*/ 2>/dev/null | head -1)"
    [ -z "$SRCD" ] && { bad "解压后找不到源码目录"; rm -rf "$TMP"; exit 1; }

    echo "  编译（三到五分钟，输出滚是正常的）"
    # BUILD_SHARED_LIBS=OFF：不然拷走主程序就找不到 libwhisper.dylib
    if ! ( cd "$SRCD" && "$CMAKE" -B build -DCMAKE_BUILD_TYPE=Release \
             -DBUILD_SHARED_LIBS=OFF -DCMAKE_CXX_FLAGS="$CXXF" >/dev/null \
           && "$CMAKE" --build build --config Release --target whisper-cli -j 4 ); then
      bad "编译失败。把最后几十行贴出来看。"
      rm -rf "$TMP"; exit 1
    fi
    cp "$SRCD/build/bin/whisper-cli" "$WH/bin/whisper-cli" && chmod +x "$WH/bin/whisper-cli"
    rm -rf "$TMP"
    CLI="$WH/bin/whisper-cli"
    ok "编译好了"
  fi
fi

# ---------------------------------------------------------------- 4. 模型
step 4 "识别模型"
MODEL="$WH/ggml-small-q5_1.bin"
if [ -s "$MODEL" ]; then
  ok "已经有了（$(du -m "$MODEL" | cut -f1) MB）"
else
  echo "  ggml-small-q5_1.bin，约 181 MB，只下一次"
  GOT=""
  for H in https://huggingface.co https://hf-mirror.com; do
    echo "  从 $H 取"
    if curl -L --fail --connect-timeout 20 --max-time 1800 --progress-bar \
         -o "$MODEL.part" "$H/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin"; then
      mv "$MODEL.part" "$MODEL"; GOT=1; break
    fi
    rm -f "$MODEL.part"
  done
  if [ -z "$GOT" ]; then
    bad "模型没下下来。两个源都不行，通常是网络。"
    echo "  手动下载后放到 $WH/"
    exit 1
  fi
  ok "$(du -m "$MODEL" | cut -f1) MB"
fi

# ---------------------------------------------------------------- 5. 转 wav
step 5 "音频转 wav"
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg"
elif [ -x /usr/bin/afconvert ]; then
  ok "用系统自带的 afconvert（不用装 ffmpeg）"
else
  bad "两种都没有，转写前的 wav 转不出来"
  exit 1
fi

printf '\n装好了。\n'
```

## 二、转写脚本

同样存一份到 `~/Library/Application Support/Transcript/skill/gz.py`，一次就够，以后直接调。

```bash
#!/usr/bin/env python3
# 稿 · 一个链接 → 一份能读的 Markdown
# 用法： python3 gz.py <视频链接 或 本地文件> [-o 输出目录] [--no-subs] [--model small] [--proxy socks5://...]
import argparse, json, os, re, shutil, subprocess, sys, tempfile
from pathlib import Path

DATA = Path.home() / "Library/Application Support/Transcript"
WH = DATA / "whisper"

LOCKED = ("channels.weixin.qq.com", "finder.video.qq.com",
          "xiaohongshu.com", "xhslink.com")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def find_tool(name):
    for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        c = Path(p) / name
        if c.exists():
            return str(c)
    return shutil.which(name)


def whisper_cli():
    for p in (WH / "bin/whisper-cli", Path("/opt/homebrew/bin/whisper-cli"),
              Path("/usr/local/bin/whisper-cli")):
        if p.exists():
            return str(p)
    return shutil.which("whisper-cli") or ""


def ggml_model(name):
    for pat in (f"ggml-{name}-q5_1.bin", f"ggml-{name}-q8_0.bin", f"ggml-{name}.bin"):
        p = WH / pat
        if p.exists():
            return str(p)
    return ""


def yt(url, *args, proxy=""):
    cmd = [sys.executable, "-m", "yt_dlp", "--no-warnings"]
    if proxy:
        cmd += ["--proxy", proxy]
    return cmd + list(args) + [url]


def run(cmd, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(cmd, **kw)


# ---------------------------------------------------------------- 探信息
def probe(url, proxy=""):
    r = run(yt(url, "-J", "--no-playlist", proxy=proxy))
    if r.returncode != 0:
        return None, (r.stderr or "").strip().splitlines()[-1:] or ["yt-dlp 取不到这条"]
    try:
        return json.loads(r.stdout), ""
    except Exception as e:
        return None, f"解析视频信息失败：{e}"


# ---------------------------------------------------------------- 字幕
SUB_RANK = ["zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK", "en", "en-US"]


def pick_sub(info):
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}
    for bucket, auto in ((subs, False), (autos, True)):
        keys = [k for k in bucket if k not in ("danmaku", "live_chat")]
        for want in SUB_RANK:
            for k in keys:
                if k.lower() == want.lower() or k.lower().startswith(want.lower() + "-"):
                    return k, auto
        if keys:
            return keys[0], auto
    return None, False


def fetch_subs(url, code, auto, work, proxy=""):
    flag = "--write-auto-subs" if auto else "--write-subs"
    cmd = yt(url, "--skip-download", "--no-playlist", flag,
             "--sub-langs", code, "--sub-format", "vtt/srt/best",
             "-o", str(work / "sub.%(ext)s"), proxy=proxy)
    r = run(cmd)
    for p in work.glob("sub.*"):
        if p.suffix.lower() in (".vtt", ".srt"):
            return p
    return None


TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{3})")


def _clean(s):
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'")):
        s = s.replace(a, b)
    return s.strip()


def _join(a, b):
    if not a:
        return b
    if CJK.search(a[-1:]) or CJK.search(b[:1]):
        return a + b
    return a + " " + b


def _overlap(prev, t):
    """prev 的结尾和 t 的开头重合多少字。滚动字幕就靠它去重。"""
    n = min(len(prev), len(t))
    for k in range(n, 3, -1):
        if prev.endswith(t[:k]):
            return k
    return 0


def parse_vtt(path):
    """VTT 会把同一句话重复吐好几遍，两种真实格式：
       ① 每句从头重说；② 两行一块、第一行重复上一句。
       统一用「前后缀重合」合并，最后再把重叠的 end 压平。"""
    items = []
    text = Path(path).read_text("utf-8", errors="ignore")
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.splitlines() if l.strip()]
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        m = TS.search(lines[ti])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        txt = ""
        for l in lines[ti + 1:]:
            txt = _join(txt, _clean(l))
        if txt.strip():
            items.append([st, en, txt.strip()])

    out = []
    for st, en, t in items:
        if not out:
            out.append([st, en, t]); continue
        prev = out[-1][2]
        if t == prev:
            out[-1][1] = en
            continue
        ov = _overlap(prev, t)
        if ov >= 4 and ov >= 0.5 * min(len(prev), len(t)):
            out[-1][1] = en
            out[-1][2] = _join(prev, t[ov:])
        elif t.startswith(prev):
            out[-1][1] = en
            out[-1][2] = t
        else:
            out.append([st, en, t])
    for i in range(len(out) - 1):
        if out[i][1] > out[i + 1][0]:
            out[i][1] = out[i + 1][0]
    return out


# ---------------------------------------------------------------- 音频
def download_audio(url, work, proxy=""):
    has_ff = bool(find_tool("ffmpeg"))
    tmpl = str(work / "audio.%(ext)s")
    if has_ff:
        base = ["-f", "bestaudio/best", "-x", "--audio-format", "m4a", "--audio-quality", "5"]
    else:
        base = ["-f", "bestaudio[ext=m4a]/bestaudio/best"]
    cmd = yt(url, "--no-playlist", *base, "-o", tmpl, proxy=proxy)
    r = run(cmd)
    for p in sorted(work.glob("audio.*")):
        if p.suffix not in (".part", ".ytdl"):
            return p
    return None


def to_wav(audio, work):
    audio = Path(audio)
    if audio.suffix.lower() == ".wav":
        return audio
    out = work / "audio16k.wav"
    ff = find_tool("ffmpeg")
    if ff:
        cmd = [ff, "-y", "-v", "error", "-i", str(audio), "-ar", "16000", "-ac", "1",
               "-c:a", "pcm_s16le", str(out)]
    else:
        af = find_tool("afconvert") or "/usr/bin/afconvert"
        if not Path(af).exists():
            raise RuntimeError("没有 ffmpeg 也没有 afconvert，转不出 wav")
        cmd = [af, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(audio), str(out)]
    r = run(cmd)
    if r.returncode != 0 or not out.exists():
        tail = ((r.stderr or "").strip().splitlines() or ["没有输出"])[-1]
        raise RuntimeError("转 wav 失败：" + tail)
    return out


def transcribe(wav, work, model="small", threads=0):
    exe = whisper_cli()
    if not exe:
        raise RuntimeError("没找到 whisper-cli")
    mp = ggml_model(model)
    if not mp:
        raise RuntimeError(f"没找到模型 ggml-{model}-q5_1.bin")
    t = threads or max(2, (os.cpu_count() or 4) // 2)
    prefix = work / "out"
    cmd = [exe, "-m", mp, "-f", str(wav), "-l", "auto", "-t", str(t),
           "-pp", "-oj", "-of", str(prefix)]
    log(f"  转写中（{model}，{t} 线程）…")
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    lang = ""
    for line in p.stderr:
        m = re.search(r"auto-detected language:\s*(\w+)", line)
        if m:
            lang = m.group(1)
            log(f"  语言：{lang}")
        elif "progress" in line and re.search(r"progress\s*=\s*\d{3}%", line):
            log("  " + line.strip())
    p.wait()
    jp = Path(str(prefix) + ".json")
    if not jp.exists():
        raise RuntimeError("转写没有产出 json")
    data = json.loads(jp.read_text("utf-8"))
    segs = []
    for it in data.get("transcription", []):
        o = it.get("offsets") or {}
        segs.append([o.get("from", 0) / 1000.0, o.get("to", 0) / 1000.0,
                     (it.get("text") or "").strip()])
    return [s for s in segs if s[2]], lang


# ---------------------------------------------------------------- 组装
CJK = re.compile(r"[一-鿿]")


def assemble(segs, gap=0.9, limit=140):
    paras, cur = [], None
    for st, en, t in segs:
        if cur is None:
            cur = [st, en, t]
            continue
        gap_now = st - cur[1]
        too_long = len(cur[2]) + len(t) > limit
        if gap_now > gap or too_long:
            paras.append(cur); cur = [st, en, t]
        else:
            joiner = "" if CJK.search(cur[2][-1:]) else " "
            cur[2] = cur[2] + joiner + t
            cur[1] = en
    if cur:
        paras.append(cur)
    return paras


def hhmmss(s):
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def write_md(paras, info, src, out, stamps=False):
    title = (info.get("title") or Path(src).stem).strip()
    up = info.get("uploader") or info.get("channel") or ""
    dur = info.get("duration") or (paras[-1][1] if paras else 0)
    body = []
    for st, en, t in paras:
        body.append(f"**{hhmmss(st)}** {t}" if stamps else t)
    lines = [f"# {title}", ""]
    if up:
        lines.append(f"作者：{up}  ")
    if dur:
        lines.append(f"时长：{hhmmss(dur)}  ")
    if src.startswith("http"):
        lines.append(f"来源：{src}  ")
    lines += ["", "---", ""]
    lines.append("\n\n".join(body))
    out.write_text("\n".join(lines) + "\n", "utf-8")
    return title


def safe(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()[:80] or "transcript"


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--outdir", default=".")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--model", default="small")
    ap.add_argument("--proxy", default=os.environ.get("TRANSCRIPT_PROXY")
                                or os.environ.get("GZ_PROXY") or "")
    ap.add_argument("--stamps", action="store_true")
    a = ap.parse_args()

    outdir = Path(a.outdir).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="gz-"))
    try:
        if a.src.startswith("http"):
            low = a.src.lower()
            for d in LOCKED:
                if d in low:
                    log(f"这条链接取不到：{d} 的媒体地址是播放器里现签的，几分钟就过期。")
                    log("换成：按标题去 B站/YouTube 搜同版，或者自己录屏后把本地文件给我。")
                    return 2

            log("探信息 …")
            info, err = probe(a.src, a.proxy)
            if info is None:
                log(f"取不到：{err}")
                return 2
            log(f"  {info.get('title')}  ({hhmmss(info.get('duration') or 0)})")

            segs, lang = [], ""
            if not a.no_subs:
                code, auto = pick_sub(info)
                if code:
                    log(f"有现成字幕（{code}{'，自动生成' if auto else ''}），先试这个")
                    vtt = fetch_subs(a.src, code, auto, work, a.proxy)
                    if vtt:
                        segs = parse_vtt(vtt)
                        lang = code
                        log(f"  取到 {len(segs)} 句")

            if not segs:
                log("没有可用字幕，下载音频再本机转写")
                audio = download_audio(a.src, work, a.proxy)
                if not audio:
                    log("音频没下下来。把 yt-dlp 的报错原文给你。")
                    return 2
                wav = to_wav(audio, work)
                segs, lang = transcribe(wav, work, a.model)
        else:
            p = Path(a.src).expanduser()
            if not p.exists():
                log(f"文件不存在：{p}")
                return 2
            info = {"title": p.stem, "duration": 0}
            wav = to_wav(p, work)
            segs, lang = transcribe(wav, work, a.model)

        segs = [s for s in segs if s[2] and not s[2].startswith("&#")]
        if not segs:
            log("一句都没出来。")
            return 2
        paras = assemble(segs)
        dst = outdir / (safe(info.get("title") or "transcript") + ".md")
        title = write_md(paras, info, a.src, dst, a.stamps)
        log(f"完成：{dst.name}  语言 {lang or '?'}  {len(paras)} 段  {sum(len(p[2]) for p in paras)} 字")
        print(dst)
        return 0
    except RuntimeError as e:
        log(f"失败：{e}")
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
```

## 三、怎么用

```bash
D="$HOME/Library/Application Support/Transcript"
PY="$(cat "$D/skill/python")"
"$PY" "$D/skill/gz.py" "<链接 或 本地文件路径>" -o <输出目录>
```

常用参数：

| 参数 | 作用 |
|---|---|
| `-o 目录` | 稿子写到哪，默认当前目录 |
| `--no-subs` | 不用平台字幕，一律本机转写。用户嫌字幕是机翻时用 |
| `--model medium` | 换更准的模型（要另外下一个文件，没下就别用） |
| `--stamps` | 每段前面带时间戳，用户要对着视频找位置时用 |
| `--proxy socks5://127.0.0.1:7890` | 走代理。也可以先 `export TRANSCRIPT_PROXY=...` |

## 它会走哪条路

先探视频信息，然后：

1. **有现成字幕** → 只下字幕、不碰音频，几秒钟。日志里有「有现成字幕（zh-Hans）」和取到多少句。
2. **没有字幕** → 下载音频 → 转 16k 单声道 wav → whisper.cpp 转写。一小时的视频几分钟。

抓字幕时中文优先于英文，人工字幕优先于自动生成。弹幕和直播聊天会排除掉，不会混进正文。

字幕的重复是真实存在的：有的平台每句从头重说一遍，有的是两行一块、第一行重复上一句。脚本按前后缀重合去重，并压平重叠的时间轴，所以不会出现一句话连着说两遍。如果用户还是看到明显重复，把那段原文发出来看。

## 结果怎么交

脚本最后一行会打印 md 的路径，把它交给用户（present_files）。说清楚这几件事就够：标题、时长、走的哪条路（字幕还是转写）、判别的语言、段数、字数。

**不要加摘要、不要改写、不要替用户划重点。** 他要的是能自己读完的原文，摘要会让他跳过原文。想帮忙可以问一句要不要接着做点别的，但正文原样给。

语言一律自动判别（`-l auto`）。写死中文去转英文视频会得到一串看着像中文的乱码，这是最容易踩的坑。

## 拿不到的链接

微信视频号、小红书、公众号：媒体地址是播放器里现签的，几分钟就过期，任何工具拿到的都是死链。脚本会直接报「这条链接取不到」，不再白等。

遇到就直说拿不到，给两条路：一是按标题去 B站、YouTube 搜同版（搬运过的概率不低），二是自己录屏或用开源工具把片子抓下来，再把本地文件给这个脚本转。不要假装能转，也不要试图绕平台限制。

抖音、TikTok 不预拦，让 yt-dlp 试——有时候能拿到。失败了多半是出口 IP 或登录态，提示两句就够。

## 出问题怎么对

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `ERROR: ... Sign in to confirm`、403 | 要登录态或地区限制 | 换网络/挂代理；或让用户用浏览器登录后导出 cookie |
| `Unsupported URL` | 这个站没有提取器 | 换成能拿到的来源，或本地文件 |
| `C++ 头不在默认位置` | 命令行工具装残了，转了另一个位置 | 正常，脚本自己修正了 |
| `没有 C++ 编译器` | 没装命令行工具 | 让用户跑一次 `xcode-select --install`（不要密码） |
| `转 wav 失败` | 音频格式解不了 | 装 `ffmpeg` 更稳；macOS 上一般靠系统 afconvert 兜住 |
| `没找到模型` | 模型没下完 | 重跑 setup.sh |
| 转写很慢 | 在跑 small 的 CPU 路径 | 确认是真 Mac；Apple Silicon 会走 Metal |

报错时把最后几行原样贴给用户，不要自己编原因。脚本已经尽量把 yt-dlp 的英文报错翻译成人话了。

## 速度与精度

默认 small，一小时视频几分钟，学习用途足够。用户抱怨错字多再换 medium（慢两三倍，模型文件另下），先讲清楚代价。用户抱怨慢就换 tiny——快很多，错字也明显多，适合只是想看个大概。

有现成字幕的视频不用转，别让用户在能几秒出稿的情况下等几分钟。

## 批量

多个链接时一个一个跑，不要并行。whisper.cpp 会吃满核心，并行反而更慢，还会让每个都卡。脚本每次只处理一个，跑完一个再起下一个；中途失败不影响已经落盘的。
