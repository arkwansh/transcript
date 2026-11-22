#!/usr/bin/env python3
"""稿 — 本地视频转文字与阅读工具

给一个链接，取音频，本地转写，存成可读的稿子。
不联网除下载音视频外；转写全在本机。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

APP_DIR = Path(__file__).resolve().parent
STATIC = APP_DIR / "static"

DATA = Path(os.environ.get("TRANSCRIPT_DATA") or os.environ.get("GAOZHI_DATA")
            or (Path.home() / "Library" / "Application Support" / "Transcript"))
ITEMS = DATA / "items"
LIB = DATA / "library.json"
CONF = DATA / "config.json"

ITEMS.mkdir(parents=True, exist_ok=True)

DEFAULT_CONF = {
    "model": "small",          # tiny / base / small / medium
    "model_precise": "medium",  # 「精确模式」用
    "prefer_subs": True,        # 平台上已有字幕就先抓字幕，不下载不转写
    "language": "auto",         # auto 自动判别，或 zh / en / ja …
    "paragraph_chars": 140,     # 合并到多长断一段
    "pause_seconds": 0.9,       # 停顿超过多久强制断段
    "concurrency": 2,           # 同时跑几个任务（1–3）
    "cookies_browser": "",      # 空=不用；chrome / safari / firefox / edge —— 抖音 TikTok 需要
    "proxy": "",                # 空=走系统；形如 socks5://127.0.0.1:7890
}

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------- 存储

_lock = threading.Lock()


def read_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return default


def write_json(path, obj):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def load_conf():
    c = dict(DEFAULT_CONF)
    c.update(read_json(CONF, {}))
    return c


def load_lib():
    lib = read_json(LIB, {"items": []})
    lib.setdefault("items", [])
    return lib


def save_lib(lib):
    write_json(LIB, lib)


def item_path(iid):
    return ITEMS / f"{iid}.json"


def touch_lib(item):
    """把条目摘要写进库索引。"""
    with _lock:
        lib = load_lib()
        brief = {
            "id": item["id"],
            "title": item.get("title") or "未命名",
            "author": item.get("author") or "",
            "duration": item.get("duration") or 0,
            "source": item.get("source") or "",
            "url": item.get("url") or "",
            "created": item.get("created") or time.time(),
            "chars": len(item.get("text") or ""),
            "status": item.get("status") or "done",
            "cover": item.get("cover") or "",
        }
        lib["items"] = [i for i in lib["items"] if i["id"] != brief["id"]]
        lib["items"].insert(0, brief)
        save_lib(lib)


# ---------------------------------------------------------------- 任务

JOBS = {}
JOBS_LOCK = threading.Lock()


class Cancelled(Exception):
    """任务被手动停掉。和真失败分开，界面上不该显示成红色报错。"""


def new_job(url="", local="", title=""):
    jid = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[jid] = {
            "id": jid,
            "url": url,
            "local": local,
            "src_key": "",
            "stage": "排队",
            "percent": 0,
            "title": title,
            "error": "",
            "item_id": "",
            "log": [],
            "done": False,
            "abort": False,      # 请求停止
            "ahead": 0,          # 排队时前面还有几个
            "created": time.time(),
            "started": time.time(),
        }
    return jid


def find_live(url="", src_key=""):
    """同一个东西已经在跑或排队了，就别再建一个。
    误点、连点、刷新后再点，都靠这一条挡住。"""
    with JOBS_LOCK:
        for j in JOBS.values():
            if j.get("done") or j.get("abort"):
                continue
            if url and j.get("url") == url:
                return j["id"]
            if src_key and j.get("src_key") == src_key:
                return j["id"]
    return ""


def is_aborted(jid):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        return bool(j and j.get("abort"))


def check_abort(jid):
    if is_aborted(jid):
        raise Cancelled("已取消")


def upd(jid, **kw):
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if not j:
            return
        msg = kw.pop("log", None)
        j.update(kw)
        if msg:
            j["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")
            del j["log"][:-60]


# ---------------------------------------------------------------- 排队

# 同时最多跑几个任务由 conf["concurrency"] 决定，运行中改设置下一个排队的就按新值走。
MAX_SLOTS = 4                 # 槽位上限，也是模型实例的上限
_SLOT_CV = threading.Condition()
_FREE_SLOTS = list(range(MAX_SLOTS))
_WAITING = []                 # 还在排队的 jid，先来先跑


def job_limit():
    try:
        n = int(load_conf().get("concurrency") or 2)
    except Exception:
        n = 2
    return max(1, min(MAX_SLOTS, n))


def _sync_queue_locked():
    """刷新每个排队任务「前面还有几个」。必须在持有 _SLOT_CV 时调用。"""
    with JOBS_LOCK:
        for i, jid in enumerate(list(_WAITING)):
            j = JOBS.get(jid)
            if j:
                j["ahead"] = i


def threads_per_job(limit=None):
    """并发几个，就把核数分几份。抢线程比少用线程慢得多。"""
    cores = os.cpu_count() or 4
    return max(2, cores // max(1, limit or job_limit()))


# ---------------------------------------------------------------- yt-dlp

def find_tool(name):
    for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"):
        c = Path(p) / name
        if c.exists():
            return str(c)
    return shutil.which(name)


def ytdlp_cmd():
    """优先用命令行 yt-dlp，没有就用 python 模块。"""
    exe = find_tool("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


# 读浏览器 cookie 失败时 yt-dlp 会直接报错退出。这类错误要能认出来，
# 否则一个没配好的 cookie 会把本来好好的 YouTube 一起拖垮。
COOKIE_TROUBLE = (
    "failed to decrypt", "could not copy", "operation not permitted",
    "cannot decrypt", "unsupported browser", "no keyring", "keyring",
    "keychain", "permission denied", "failed to open", "profile",
)


def net_args():
    c = load_conf()
    a = []
    b = (c.get("cookies_browser") or "").strip()
    if b and b != "none":
        a += ["--cookies-from-browser", b]
    p = (c.get("proxy") or "").strip()
    if p:
        a += ["--proxy", p]
    return a


def yt_run(extra, timeout=180):
    """跑一次 yt-dlp；cookie 读不出来就退回不带 cookie 再跑一次。"""
    base = net_args()
    r = subprocess.run(ytdlp_cmd() + base + extra, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0 and base:
        blob = ((r.stderr or "") + (r.stdout or "")).lower()
        if any(k in blob for k in COOKIE_TROUBLE):
            r = subprocess.run(ytdlp_cmd() + extra, capture_output=True, text=True, timeout=timeout)
    return r


def parse_json(stdout):
    """yt-dlp 有时会在 JSON 前面打提示行，从第一个左花括号读起。"""
    s = (stdout or "").strip()
    i = s.find("{")
    if i < 0:
        raise ValueError("没有 JSON 输出")
    return json.loads(s[i:])


def last_line(text, fallback="取信息失败"):
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1] if lines else fallback


def probe(url):
    r = yt_run(["-J", "--no-warnings", "--no-playlist", url], timeout=120)
    if r.returncode != 0:
        raise RuntimeError(last_line(r.stderr))
    info = parse_json(r.stdout)
    return {
        "title": info.get("title") or "",
        "author": info.get("uploader") or info.get("channel") or "",
        "duration": float(info.get("duration") or 0),
        "cover": info.get("thumbnail") or "",
        "source": info.get("extractor_key") or "",
        # 顺手把可用的字幕语言带回来：有现成字幕就不用下载音频、不用转写
        "sub_manual": sorted((info.get("subtitles") or {}).keys()),
        "sub_auto": sorted((info.get("automatic_captions") or {}).keys()),
    }


def run_cancellable(cmd, jid, timeout=1800):
    """下载得能被叫停。subprocess.run 一旦进去就只能等它跑完，
    长视频下到一半想停也停不了，这是干等着的浪费。"""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    start = time.time()
    while True:
        try:
            out, err = p.communicate(timeout=0.4)
            return subprocess.CompletedProcess(cmd, p.returncode, out, err)
        except subprocess.TimeoutExpired:
            if is_aborted(jid):
                p.kill()
                p.communicate()
                raise Cancelled("已取消")
            if time.time() - start > timeout:
                p.kill()
                p.communicate()
                return subprocess.CompletedProcess(cmd, -9, "", "下载超时")


def download_audio(url, dest, jid=""):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmpl = str(dest.with_suffix(".%(ext)s"))
    base = ["-f", "bestaudio/best", "--no-playlist", "--no-warnings", "--newline"]
    if find_tool("ffmpeg"):
        # 转成 m4a：体积小、解码快，whisper 读起来最省事
        cmd = ytdlp_cmd() + net_args() + base + [
            "-x", "--audio-format", "m4a", "--audio-quality", "5", "-o", tmpl, url]
    else:
        # 没有 ffmpeg：优先挑 m4a 单流（不需要合并），系统自带的 afconvert 就能转
        base = ["-f", "bestaudio[ext=m4a]/bestaudio/best",
                "--no-playlist", "--no-warnings", "--newline"]
        cmd = ytdlp_cmd() + net_args() + base + ["-o", tmpl, url]
    return run_cancellable(cmd, jid)


# ---------------------------------------------------------------- 字幕

# 这些不是台词，是弹幕或者聊天记录，别拿来当字幕
NOT_SUBS = {"danmaku", "live_chat", "rechat"}


def pick_sub(meta, want):
    """在可用字幕里挑一条，返回 (语言代码, 是否自动生成)，没有就 None。

    宁可要自动生成的中文，也不要人工做的外语：读的是内容，不是语种样本。
    """
    manual = [c for c in (meta.get("sub_manual") or []) if c.lower() not in NOT_SUBS]
    auto = [c for c in (meta.get("sub_auto") or []) if c.lower() not in NOT_SUBS]

    def rank(code):
        c = code.lower()
        if c in ("zh-hans", "zh-cn", "zh", "zh-sg", "zh-hans-cn"):
            return 0
        if c.startswith("zh"):
            return 1
        if c.startswith("en"):
            return 2
        return 5

    w = (want or "auto").lower()
    if w != "auto":
        for src, is_auto in ((manual, False), (auto, True)):
            hit = [c for c in src if c.lower().split("-")[0] == w]
            if hit:
                return hit[0], is_auto
    for src, is_auto in ((manual, False), (auto, True)):
        good = sorted([c for c in src if rank(c) < 5], key=rank)
        if good:
            return good[0], is_auto
    return None


_TAG = re.compile(r"<[^>]+>|\{\\[^}]*\}")


def _vtt_time(s):
    try:
        nums = [float(x) for x in s.strip().replace(",", ".").split(":")]
    except ValueError:
        return 0.0
    t = 0.0
    for n in nums:
        t = t * 60 + n
    return t


def parse_vtt(path):
    """VTT / SRT 读成 [{start,end,text}]，顺手拆掉自动字幕那种滚动重复。

    平台上自动生成的字幕有两种写法：一种每句都从头重说一遍（前缀包含），
    一种是「两行一块、第一行重复上一句」（后缀重合）。两种都按行拆。
    """
    try:
        raw = Path(path).read_text("utf-8", "replace")
    except Exception:
        return []
    out = []
    prev = ""
    for block in re.split(r"\n\s*\n", raw):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        i = 0 if "-->" in lines[0] else 1
        if i >= len(lines) or "-->" not in lines[i]:
            continue
        a, _, b = lines[i].partition("-->")
        try:
            start = _vtt_time(a.split()[0])
            end = _vtt_time(b.split()[0])
        except (IndexError, ValueError):
            continue
        for raw_line in lines[i + 1:]:
            t = re.sub(r"\s+", " ", _TAG.sub("", raw_line)).strip()
            if not t:
                continue
            if prev and (t == prev or prev.endswith(t)):
                # 已经说过的半句，只把时间往后延
                if out:
                    out[-1]["end"] = max(out[-1]["end"], end)
                continue
            if prev and t.startswith(prev):
                # 一句在慢慢长出来：用新的替换旧的
                if out:
                    out[-1]["text"] = t
                    out[-1]["end"] = max(out[-1]["end"], end)
                prev = t
                continue
            out.append({"start": start, "end": max(end, start + 0.2), "text": t})
            prev = t
    # 滚动字幕会把上一句的结束时间一起往后拖，跟下一句的开头叠上。
    # 时间只用来定位，压平一下，段落的停顿判断才不会永远是负的。
    for i in range(len(out) - 1):
        out[i]["end"] = max(out[i]["start"] + 0.2, min(out[i]["end"], out[i + 1]["start"]))
    return out


def fetch_subs(url, work, jid, meta):
    """只下字幕，不碰音频。拿不到就返回 None，让上游照常转写。"""
    pick = pick_sub(meta, load_conf().get("language"))
    if not pick:
        return None
    code, is_auto = pick
    work.mkdir(parents=True, exist_ok=True)
    flag = "--write-auto-subs" if is_auto else "--write-subs"
    extra = ["--convert-subs", "vtt"] if find_tool("ffmpeg") else []
    cmd = ytdlp_cmd() + net_args() + [
        "--skip-download", "--no-playlist", "--no-warnings",
        flag, "--sub-langs", code, "--sub-format", "vtt/srt/best",
    ] + extra + ["-o", str(work / "sub.%(ext)s"), url]
    r = run_cancellable(cmd, jid, timeout=180)
    files = sorted(list(work.glob("sub*.vtt")) + list(work.glob("sub*.srt")),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        if r.returncode != 0:
            upd(jid, log="字幕没取到，照常下载转写")
        return None
    segs = parse_vtt(files[0])
    if not segs:
        return None
    upd(jid, log=f"取到{'自动' if is_auto else ''}字幕（{code}），不用下载音频")
    # 字幕里没有“语言”这个字段，但代码就是语言代码，直接用
    return segs, code.split("-")[0].lower()


# ---------------------------------------------------------------- 转写

# 优先 whisper.cpp：一个二进制 + 一个模型文件，走 Metal，不碰 Python 环境。
# 找不到才退回 faster-whisper。两边吐出来的都是 [{start,end,text}]，下游不用管是谁转的。
WHISPER_DIR = DATA / "whisper"


def whisper_cli():
    for p in (WHISPER_DIR / "bin" / "whisper-cli",
              Path("/opt/homebrew/bin/whisper-cli"),
              Path("/usr/local/bin/whisper-cli")):
        if p.exists():
            return str(p)
    return shutil.which("whisper-cli") or ""


def ggml_model(name):
    for pat in (f"ggml-{name}-q5_1.bin", f"ggml-{name}-q8_0.bin", f"ggml-{name}.bin"):
        p = WHISPER_DIR / pat
        if p.exists():
            return str(p)
    return ""


def to_wav(audio, work, jid):
    """whisper.cpp 只吃 16k 单声道 wav，先转一道。

    有 ffmpeg 就用 ffmpeg；没有就用 macOS 自带的 afconvert（不用装任何东西）。
    afconvert 只认 CoreAudio 能解的格式，m4a/mp3/aac/wav 都行，webm/opus 不行——
    所以下载那一步在没有 ffmpeg 时会优先挑 m4a 单流。
    """
    audio = Path(audio)
    if audio.suffix.lower() == ".wav":
        return audio
    out = Path(work) / "audio16k.wav"
    ff = find_tool("ffmpeg")
    if ff:
        cmd = [ff, "-y", "-v", "error", "-i", str(audio), "-ar", "16000", "-ac", "1",
               "-c:a", "pcm_s16le", str(out)]
    else:
        af = find_tool("afconvert") or "/usr/bin/afconvert"
        if not Path(af).exists():
            raise RuntimeError("缺 ffmpeg，转不了 wav")
        cmd = [af, "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(audio), str(out)]
    r = run_cancellable(cmd, jid, timeout=900)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError("转 wav 失败：" + last_line(r.stderr))
    return out


def _run_streaming(cmd, jid, json_path):
    """跑 whisper-cli，一边读 stderr 一边更新进度，随时可停。"""
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         text=True, bufsize=1)
    lines = []

    def pump():
        for line in p.stderr:
            lines.append(line)
            m = re.search(r"progress\s*=\s*(\d+)%", line)
            if m:
                upd(jid, percent=min(96, 8 + int(0.88 * int(m.group(1)))))

    th = threading.Thread(target=pump, daemon=True)
    th.start()
    start = time.time()
    while p.poll() is None:
        if is_aborted(jid):
            p.kill()
            p.wait()
            raise Cancelled("已取消")
        if time.time() - start > 4 * 3600:
            p.kill()
            p.wait()
            raise RuntimeError("转写超时")
        time.sleep(0.4)
    th.join(timeout=2)

    err = "".join(lines)
    if p.returncode != 0 or not Path(json_path).exists():
        raise RuntimeError(last_line(err, "whisper.cpp 退出异常"))
    data = json.loads(Path(json_path).read_text("utf-8", "replace"))

    segs = []
    for t in (data.get("transcription") or []):
        off = t.get("offsets") or {}
        txt = (t.get("text") or "").strip()
        if not txt:
            continue
        segs.append({
            "start": float(off.get("from") or 0) / 1000.0,
            "end": float(off.get("to") or 0) / 1000.0,
            "text": txt,
        })
    m = re.search(r"auto-detected language:\s*([a-zA-Z_-]+)", err)
    detected = (m.group(1).lower() if m else "")
    if not detected:
        detected = (cmd_lang(cmd) or "zh")
    return segs, detected


def cmd_lang(cmd):
    try:
        return cmd[cmd.index("-l") + 1]
    except (ValueError, IndexError):
        return ""


def transcribe_cpp(audio, jid, conf, slot=0, limit=None):
    exe, model = whisper_cli(), ggml_model(conf["model"])
    wav = to_wav(audio, Path(audio).parent, jid)
    lang = conf.get("language") or "auto"
    threads = threads_per_job(limit)
    prefix = Path(wav).parent / "out"
    # 语言一律交给它自己判：写死中文去转英文会出乱码，这是踩过的坑
    cmd = [exe, "-m", model, "-f", str(wav), "-l", lang,
           "-t", str(threads), "-pp", "-oj", "-of", str(prefix)]
    upd(jid, log=f"whisper.cpp {Path(model).name}，{threads} 线程")
    segs, detected = _run_streaming(cmd, jid, str(prefix) + ".json")
    check_abort(jid)
    total = segs[-1]["end"] if segs else 0.0
    return segs, detected, total


# 每个槽位各持一个模型实例：两个任务共用一个 WhisperModel 会抢它内部的线程池，
# 结果不稳，速度也不见得更好。small 的 int8 大约五百兆，两三个还扛得住。
_MODEL_CACHE = {}


def get_model(name, slot=0, limit=None):
    key = (name, slot)
    m = _MODEL_CACHE.get(key)
    if m is not None:
        return m
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError(
            "这条走到了 faster-whisper 的回退路径，但这台机器上没装它。"
            "正常情况应该用 whisper.cpp——双击安装.command 把引擎补上。"
        )
    m = WhisperModel(name, device="cpu", compute_type="int8",
                     cpu_threads=threads_per_job(limit))
    # 同一个槽位换模型时把旧的丢掉，别让内存越攒越多
    for k in [k for k in _MODEL_CACHE if k[1] == slot and k[0] != name]:
        _MODEL_CACHE.pop(k, None)
    _MODEL_CACHE[key] = m
    return m


def transcribe_faster(audio_path, jid, conf, slot=0, limit=None):
    model = get_model(conf["model"], slot, limit)
    lang = conf.get("language") or "auto"
    segs, info = model.transcribe(
        str(audio_path),
        language=None if lang == "auto" else lang,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400},
        beam_size=1,
        condition_on_previous_text=False,
    )
    total = float(getattr(info, "duration", 0) or 0)
    detected = getattr(info, "language", None) or lang
    out = []
    for s in segs:
        # 每句都看一眼有没有被叫停；whisper 是惰性产出，停在这里下一句就不会再算了
        check_abort(jid)
        out.append({"start": float(s.start), "end": float(s.end), "text": s.text.strip()})
        if total > 0:
            upd(jid, percent=min(96, int(len(out) and 5 + 90 * (s.end / total))))
    return out, detected, total


def transcribe(audio_path, jid, conf, slot=0, limit=None):
    if whisper_cli():
        if not ggml_model(conf["model"]):
            raise RuntimeError(
                f"引擎在，但 {conf['model']} 的模型文件没装"
                f"（应在 {WHISPER_DIR}/{conf['model']}）。设置里换回 small，或重跑一遍安装脚本。")
        return transcribe_cpp(audio_path, jid, conf, slot, limit)
    return transcribe_faster(audio_path, jid, conf, slot, limit)


def build_paragraphs(segs, conf, lang="zh"):
    """把 whisper 的碎句合并成段落，保留每段起始时间。"""
    paras = []
    buf, buf_start, last_end = [], None, 0.0
    max_chars = int(conf["paragraph_chars"])
    pause = float(conf["pause_seconds"])

    cjk = (lang or "zh").startswith(("zh", "ja", "ko"))
    if cjk:
        sentence_end = "。！？…」』"
        joiner = ""          # 中文句间直接接
        soft_joiner = "，"    # 没有终止符时补逗号
    else:
        sentence_end = ".!?…"
        joiner = " "
        soft_joiner = ", "

    def flush():
        nonlocal buf, buf_start
        if buf:
            text = joiner.join(buf) if not cjk else "".join(buf)
            paras.append({"start": buf_start, "text": text.strip()})
        buf, buf_start = [], None

    for s in segs:
        t = (s["text"] or "").strip()
        if not t:
            continue
        gap = s["start"] - last_end if buf else 0
        if buf and (gap >= pause or sum(len(x) for x in buf) >= max_chars):
            flush()
        if not buf:
            buf_start = s["start"]
        elif not buf[-1].rstrip().endswith(tuple(sentence_end)) and not buf[-1].endswith(("，", ",", "、")):
            buf.append(soft_joiner)
        buf.append(t)
        last_end = s["end"]

        if t.endswith(tuple(sentence_end)) and sum(len(x) for x in buf) >= max_chars * 0.7:
            flush()

    flush()
    return paras


def fmt_ts(sec):
    sec = int(sec or 0)
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60:02d}:{sec % 60:02d}"


def run_job(jid):
    """先排队拿一个槽位，拿到了才真正开跑。"""
    slot = None
    with _SLOT_CV:
        _WAITING.append(jid)
        _sync_queue_locked()
        while True:
            # 排队期间被停掉就别等了，直接退出，别占着队列位置
            if is_aborted(jid) or jid not in JOBS:
                if jid in _WAITING:
                    _WAITING.remove(jid)
                _sync_queue_locked()
                _SLOT_CV.notify_all()
                return
            busy = MAX_SLOTS - len(_FREE_SLOTS)
            if _FREE_SLOTS and _WAITING and _WAITING[0] == jid and busy < job_limit():
                slot = _FREE_SLOTS.pop(0)
                _WAITING.pop(0)
                _sync_queue_locked()
                break
            _SLOT_CV.wait(0.4)

    limit = job_limit()
    try:
        _run_job(jid, slot, limit)
    finally:
        with _SLOT_CV:
            _FREE_SLOTS.append(slot)
            _FREE_SLOTS.sort()
            _SLOT_CV.notify_all()


def _run_job(jid, slot, limit):
    conf = load_conf()
    with JOBS_LOCK:
        job = dict(JOBS.get(jid) or {})
    url = job.get("url") or ""
    local = job.get("local") or ""
    work = ITEMS / jid
    segs, lang, engine = None, "", ""
    try:
        if local:
            # 本地文件：不碰网络，直接转。抖音/视频号 用别的工具抓下来的片子走这条路。
            audio = Path(local)
            if not audio.exists():
                raise RuntimeError("文件不在了：" + audio.name)
            title = (job.get("title") or audio.stem or "").strip() or "未命名"
            meta = {"title": title, "author": "", "duration": 0.0,
                    "cover": "", "source": "本地文件"}
            upd(jid, title=title, stage="读入文件", percent=8, log=f"读入 {audio.name}")
        else:
            upd(jid, stage="取信息", percent=2, log="读取视频信息")
            meta = probe(url)
            upd(jid, title=meta["title"], stage="查字幕", percent=6,
                log=f"《{meta['title']}》 {fmt_ts(meta['duration'])}")

            # 平台上有现成字幕就几秒钟拿到，不必下载音频再转一遍
            if conf.get("prefer_subs"):
                try:
                    got = fetch_subs(url, work, jid, meta)
                except Cancelled:
                    raise
                except Exception:
                    got = None          # 字幕只是顺路，坏了不该连累正经转写
                if got:
                    segs, lang = got
                    engine = "平台字幕"

            if not segs:
                upd(jid, stage="下载音频", percent=10, log="没有现成字幕，下载音频")
                r = download_audio(url, work / "audio", jid)
                if r.returncode != 0:
                    tail = [l for l in (r.stderr or "").strip().splitlines() if l.strip()][-3:]
                    raise RuntimeError("下载失败：" + (" / ".join(tail) or "未知原因"))

                files = sorted(list(work.rglob("audio.*")), key=lambda p: p.stat().st_size, reverse=True)
                if not files:
                    raise RuntimeError("下载后没找到音频文件")
                audio = files[0]

        if not segs:
            lane = f"，第 {slot + 1} 路" if limit > 1 else ""
            upd(jid, stage="转写", percent=8,
                log=f"音频 {audio.stat().st_size/1048576:.1f} MB，开始转写（本机{lane}）")

            engine = "whisper.cpp" if whisper_cli() else "faster-whisper"
            check_abort(jid)
            segs, lang, total = transcribe(audio, jid, conf, slot, limit)
            check_abort(jid)
        if not meta["duration"] and segs:
            meta["duration"] = segs[-1]["end"]
        upd(jid, stage="整理", percent=97, log=f"{len(segs)} 句，语言 {lang}")
        paras = build_paragraphs(segs, conf, lang)
        text = "\n\n".join(p["text"] for p in paras)

        check_abort(jid)
        item = {
            "id": jid,
            "url": url,
            "title": meta["title"],
            "author": meta["author"],
            "duration": meta["duration"],
            "cover": meta["cover"],
            "source": meta["source"],
            "created": time.time(),
            "model": conf["model"],
            "engine": engine,
            "lang": lang,
            "status": "done",
            "text": text,
            "paragraphs": paras,
            "segments": segs,
        }
        write_json(item_path(jid), item)
        touch_lib(item)

        # 顺手清掉大文件，只留文本
        try:
            shutil.rmtree(work)
        except Exception:
            pass

        upd(jid, stage="完成", percent=100, item_id=jid, done=True,
            log=f"完成，{len(text)} 字 / {len(paras)} 段")
    except Cancelled:
        try:
            shutil.rmtree(work)
        except Exception:
            pass
        # 取消不是出错：不算失败、不弹红字、不留半成品
        upd(jid, stage="已停止", error="", done=True, percent=0, log="已停止，没有继续占用")
    except Exception as e:
        detail = str(e) or e.__class__.__name__
        if isinstance(detail, list):
            detail = " ".join(map(str, detail))
        detail = hint_for(detail)
        upd(jid, stage="失败", error=detail, done=True, percent=100, log=detail)
        traceback.print_exc()
        try:
            shutil.rmtree(work)
        except Exception:
            pass


# ---------------------------------------------------------------- 路由

@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/library")
def api_library():
    return jsonify(load_lib())


# yt-dlp 没有提取器、媒体文件锁在前端播放器里的平台。
# 这类链接别耗上一分钟再报错，直接说清楚，把人送去「找同版」或本地文件。
LOCKED = {
    "channels.weixin.qq.com": "视频号",
    "finder.video.qq.com": "视频号",
    "weixin.qq.com": "微信",
    "mp.weixin.qq.com": "公众号",
    "xiaohongshu.com": "小红书",
    "xhslink.com": "小红书",
}


def locked_platform(url):
    host = re.sub(r"^https?://", "", url or "").split("/")[0].split(":")[0].lower()
    for k, v in LOCKED.items():
        if host == k or host.endswith("." + k):
            return v
    return ""


def hint_for(detail):
    """把 yt-dlp 的英文报错翻译成下一步该干什么。"""
    d = (detail or "").lower()
    if "no supported" in d or "unsupported url" in d:
        return detail + " ｜ 这个站没有提取器。贴标题走找同版，或者把片子拖进来。"
    if "cookie" in d or "sign in" in d or "login" in d or "403" in d or "forbidden" in d:
        return detail + " ｜ 多半要登录态。去设置里选一个登录着的浏览器 Cookie，或换个网络出口再试。"
    if "timed out" in d or "timeout" in d:
        return detail + " ｜ 连不上。设置里可以填代理。"
    if "ffmpeg" in d:
        return detail + " ｜ 转 wav 失败。装个 ffmpeg 更稳（brew install ffmpeg），现在退用的是系统 afconvert。"
    return detail


@app.post("/api/transcribe")
def api_transcribe():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "没给链接"}), 400
    name = locked_platform(url)
    if name:
        return jsonify({
            "locked": name,
            "error": f"{name}的视频抓不到媒体文件。",
        })
    dup = find_live(url=url)
    if dup:
        # 已经在跑或排队了。返回原来那条，别再造一个重复的。
        return jsonify({"job": dup, "dup": True})
    jid = new_job(url)
    threading.Thread(target=run_job, args=(jid,), daemon=True).start()
    return jsonify({"job": jid})


@app.post("/api/upload")
def api_upload():
    """本地文件直接转：拖进来的视频、别的工具抓下来的片子都走这里。"""
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "没收到文件"}), 400
    name = Path(f.filename).name
    ext = (Path(name).suffix or ".mp4").lower()
    size = int(request.content_length or 0)
    src_key = f"{name}:{size}"
    dup = find_live(src_key=src_key) if size else ""
    if dup:
        return jsonify({"job": dup, "dup": True})

    jid = new_job(title=Path(name).stem)
    work = ITEMS / jid
    work.mkdir(parents=True, exist_ok=True)
    dest = work / ("input" + ext)
    f.save(str(dest))
    size = dest.stat().st_size
    if size <= 0:
        shutil.rmtree(work, ignore_errors=True)
        return jsonify({"error": "文件是空的"}), 400
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid]["local"] = str(dest)
            JOBS[jid]["src_key"] = src_key
    threading.Thread(target=run_job, args=(jid,), daemon=True).start()
    return jsonify({"job": jid, "size": size})


@app.get("/api/jobs")
def api_jobs():
    """还没跑完的任务，页面刷新后能重新接管。"""
    with JOBS_LOCK:
        live = [dict(j) for j in JOBS.values() if not j.get("done")]
    live.sort(key=lambda j: j.get("created") or 0)
    return jsonify({"jobs": live})


@app.get("/api/job/<jid>")
def api_job(jid):
    with JOBS_LOCK:
        j = dict(JOBS.get(jid) or {})
    if not j:
        return jsonify({"error": "没有这个任务"}), 404
    return jsonify(j)


def cancel_one(jid):
    """停一个任务：排队中的立刻出队，跑着的打标记，下一句就停。"""
    with JOBS_LOCK:
        j = JOBS.get(jid)
        if not j or j.get("done"):
            return False
        j["abort"] = True
        if j.get("stage") == "排队":
            # 还没开始，直接收掉，连线程都不用等
            j.update(stage="已停止", done=True, percent=0, ahead=0)
            j["log"].append(f"{time.strftime('%H:%M:%S')}  已从队列移除")
        else:
            j["log"].append(f"{time.strftime('%H:%M:%S')}  正在停止…")
    return True


@app.post("/api/job/<jid>/cancel")
def api_cancel(jid):
    ok = cancel_one(jid)
    return jsonify({"ok": ok})


@app.post("/api/queue/clear")
def api_queue_clear():
    """一键清空排队中的任务，正在跑的不动。"""
    with JOBS_LOCK:
        waiting = [j for j in JOBS.values() if not j.get("done") and j.get("stage") == "排队"]
    n = sum(1 for j in waiting if cancel_one(j["id"]))
    return jsonify({"cleared": n})


@app.get("/api/item/<iid>")
def api_item(iid):
    p = item_path(iid)
    if not p.exists():
        return jsonify({"error": "没有这篇稿"}), 404
    return jsonify(read_json(p, {}))


@app.delete("/api/item/<iid>")
def api_delete(iid):
    p = item_path(iid)
    if p.exists():
        p.unlink()
    with _lock:
        lib = load_lib()
        lib["items"] = [i for i in lib["items"] if i["id"] != iid]
        save_lib(lib)
    return jsonify({"ok": True})


@app.get("/api/item/<iid>/export")
def api_export(iid):
    it = read_json(item_path(iid), {})
    if not it:
        return jsonify({"error": "没有这篇稿"}), 404
    head = f"# {it.get('title')}\n\n"
    if it.get("author"):
        head += f"作者：{it['author']}  \n"
    head += f"时长：{fmt_ts(it.get('duration'))}  \n来源：{it.get('url')}\n\n---\n\n"
    body = "\n\n".join(p["text"] for p in it.get("paragraphs", []))
    data = head + body + "\n"
    return app.response_class(data, mimetype="text/markdown",
                              headers={"Content-Disposition": f'attachment; filename="{iid}.md"'})


BILI_API = "https://api.bilibili.com/x/web-interface/search/type"


def _opener():
    """填了 http 代理就走代理；socks 交给 yt-dlp，这边直连（B站本来在国内，绕远反而慢）。"""
    p = (load_conf().get("proxy") or "").strip()
    if p.lower().startswith(("http://", "https://")):
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": p, "https": p}))
    return urllib.request.build_opener()


def _bili_secs(s):
    try:
        parts = [int(x) for x in str(s or "").split(":") if x.strip().isdigit()]
    except Exception:
        return 0.0
    total = 0
    for p in parts:
        total = total * 60 + p
    return float(total)


def bili_search(q, n=8):
    """直接问 B站 的搜索接口。
    yt-dlp 的 bilisearch 在 flat 模式下只吐链接、不给标题，列表里全是空白，没法用。"""
    qs = urllib.parse.urlencode({
        "search_type": "video", "keyword": q, "page": 1,
        "page_size": max(1, min(20, n)), "order": "totalrank",
        "duration": 0, "tids": 0,
    })
    req = urllib.request.Request(BILI_API + "?" + qs, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
        "Cookie": f"buvid3={uuid.uuid4().hex}infoc",
    })
    with _opener().open(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))

    out = []
    for v in ((data.get("data") or {}).get("result") or []):
        link = v.get("arcurl") or ""
        if "/video/" not in link:      # 课程、直播回放之类取不到音轨，跳过
            continue
        pic = v.get("pic") or ""
        if pic.startswith("//"):
            pic = "https:" + pic
        out.append({
            "title": re.sub(r"<[^>]+>", "", v.get("title") or "").strip(),
            "author": v.get("author") or "",
            "duration": _bili_secs(v.get("duration")),
            "cover": pic,
            "views": int(v.get("play") or 0),
            "url": link,
            "platform": "B站",
        })
    return out


def yt_search(q, n=6):
    """YouTube 走 yt-dlp 站内搜索。flat 模式下这几个字段都有，够用了。"""
    try:
        r = yt_run(["--flat-playlist", "--no-warnings", "-J", f"ytsearch{n}:{q}"], timeout=75)
        if r.returncode != 0:
            return []
        data = parse_json(r.stdout)
    except Exception:
        return []

    out = []
    for e in (data.get("entries") or []):
        if not e:
            continue
        thumb = e.get("thumbnail") or ""
        if not thumb:
            ts = e.get("thumbnails") or []
            if ts and isinstance(ts[-1], dict):
                thumb = ts[-1].get("url") or ""
        u = e.get("webpage_url") or e.get("url") or ""
        if not str(u).startswith("http"):
            u = f"https://www.youtube.com/watch?v={e.get('id')}"
        out.append({
            "title": e.get("title") or "",
            "author": e.get("uploader") or e.get("channel") or "",
            "duration": float(e.get("duration") or 0),
            "cover": thumb,
            "views": e.get("view_count") or 0,
            "url": u,
            "platform": "YouTube",
        })
    return out


@app.post("/api/search")
def api_search():
    """按标题去 B站 / YouTube 找同版。两边同时搜，谁先回谁先上。"""
    q = (request.json or {}).get("q", "").strip()
    if not q:
        return jsonify({"candidates": []})

    got = {}

    def one(key, fn):
        try:
            got[key] = fn()
        except Exception:
            got[key] = []

    ts = [threading.Thread(target=one, args=a, daemon=True) for a in (
        ("B站", lambda: bili_search(q, 8)),
        ("YouTube", lambda: yt_search(q, 6)),
    )]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=85)

    seen, cands = set(), []
    for platform in ("B站", "YouTube"):
        for c in got.get(platform) or []:
            if not c["url"] or c["url"] in seen:
                continue
            seen.add(c["url"])
            cands.append(c)
    return jsonify({"candidates": cands, "q": q})


@app.get("/api/config")
def api_config_get():
    return jsonify(load_conf())


@app.post("/api/config")
def api_config_post():
    conf = load_conf()
    conf.update({k: v for k, v in (request.json or {}).items() if k in DEFAULT_CONF})
    write_json(CONF, conf)
    return jsonify(conf)


def main():
    port = int(os.environ.get("TRANSCRIPT_PORT") or os.environ.get("GAOZHI_PORT") or "8765")
    print(f"稿 运行在 http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
