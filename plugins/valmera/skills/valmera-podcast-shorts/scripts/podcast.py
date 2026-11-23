#!/usr/bin/env python3
"""valmera-podcast-shorts: a podcast or interview link in, one folder of finished shorts out.

    python3 podcast.py <link>

That is the whole input. The run does the rest by itself and can be started again with the same link to continue
where it stopped:

  fetch     the video (1080p at most) and its captions; an earlier download of the same video on this Mac is used
            in place when it is still there
  hear      every word with its time: the platform's captions, else a local hearing of the whole source
  moments   one reading of the whole conversation finds every moment that clears the bar and ranks them. There is
            no count: the number of shorts follows the podcast
  styles    a style per short from the engine styles (wf1_film.STYLES): never the style of the short before it,
            spread evenly, the moment's own best fit first
  shorts    each moment is made by wf1_short.py on the workflow shell (real footage of what is said, the judge's
            pace and moves, the sound design, word-by-word captions, display words, finishes by section, the film's
            music), two at a time; a short that stops is resumed, and one that cannot be made is reported, never
            waited for
  deliver   once, complete, into one folder: every short with its music and without, SOURCES.md listing every
            clip, photograph and track, shorts.json, README.md. Then the work folder is deleted

Never publishes. The product's end card and watermark are on every short. Model calls run on the OpenAI rung.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

V = "/Users/masaoodi/Documents/Valmera"
VV = f"{V}/.valmera"
PY = f"{VV}/venv-worker/bin/python"
if os.path.exists(PY) and os.path.realpath(sys.executable) != os.path.realpath(PY) and not os.environ.get("PODCAST_NO_REEXEC"):
    os.execv(PY, [PY, os.path.realpath(__file__)] + sys.argv[1:])       # the engines' own interpreter, whoever started this

HERE = os.path.dirname(os.path.realpath(__file__))        # through the Claude skill link to the plugin itself
SKILLS = os.path.dirname(os.path.dirname(HERE))
SHELL = os.path.join(SKILLS, "_shell")
for _p in (SHELL, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WORK_ROOT = os.environ.get("PODCAST_WORK_ROOT", f"{VV}/workflow/podcast")
OUT_ROOT = os.environ.get("PODCAST_OUT_ROOT", f"{V}/Podcast Shorts")
EARLIER = f"{V}/.tmp/valmera-podcast-shorts"                # earlier runs' downloads, used in place when they hold this video
YTDLP = f"{VV}/tools/ytdlp-venv/bin/python"
DENO = f"{VV}/tools/deno"
POT = f"{VV}/tools/bgutil-pot/server"                       # the PO-token script yt-dlp's bgutil plugin runs (the product's own rung)
BIN = f"{V}/audit/subscriber-reliability-20260908/bin-arm"
FF, FP = BIN + "/ffmpeg", BIN + "/ffprobe"
HEAVY = f"{VV}/week-1106/heavy.sh"
ATLAS_PY = f"{VV}/week-1106/atlas/venv/bin/python"
WHISPER = f"{VV}/week-1106/atlas/models/whisper-large-v3-mlx"
BAR = 7.0                 # a moment is made when the selection grades it this strong or stronger (1-10); there is no count
LANES = 2                 # shorts made at once on this Mac (the footage search is the long part and waits on the network)
PART_S = 600.0            # the selection reads the conversation in ten-minute parts, one model reading each (see moments())
PART_OVERLAP_S = 60.0     # each part reads on a minute past its end, so a moment across the seam is seen whole
MOMENT_MAX_S = 55.0       # a short runs 18-55 s: a moment measured longer is tightened by the same model (tighten())
ROUTE_OUT = "the model route is out"   # the model provider stopped answering (usage limit, cap, outage): the run waits
# YouTube's wall on this Mac's address is a temporary outage of one input (19 Nov: from 17:43 it refused nearly every
# footage source for over an hour). A short that meets it fetches its exact refused sources again with backoff, then
# stops with wf1_film.WALL keeping its work; the run waits these seconds, one after another, and continues the walled
# shorts each time (their refused sources only: nothing is searched or asked anew), until none waits. Only then, still
# walled, does the run stop for a later run of the same link. PODCAST_WALL_WAITS="600,1200" sets them.
WALL_WAITS = tuple(float(x) for x in os.environ.get("PODCAST_WALL_WAITS", "600,1200,2400,3600,3600").split(",") if x.strip())
ENV_FILE = f"{V}/hustler-server/backend/.env"
ROUTE_KEYS = ("YTDLP_PROXY", "YTDLP_PROXY_FALLBACK", "FOOTAGE_FETCH_CMD")
_LOCK = threading.Lock()
import delivered as DL  # noqa: E402  the shared delivered ledger (_shell/delivered.py): the same podcast is never delivered twice


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jload(p, default=None):
    try:
        with open(p) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def jsave(p, obj):
    tmp = p + ".part"
    with open(tmp, "w") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def clean_name(s, n=80):
    """A string as a file or folder name: no path marks, single spaces."""
    out = "".join(c if (c.isalnum() or c in " -_'&,.()") else " " for c in str(s or "").replace("\u2019", "'").replace("\u2018", "'"))
    return " ".join(out.split())[:n].strip(" .") or "untitled"


def video_id(link):
    """The platform's own id for a YouTube link; a short hash of the link for anything else."""
    m = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})", link)
    return m.group(1) if m else "x" + hashlib.sha1(link.strip().encode()).hexdigest()[:10]


class Run:
    def __init__(self, link, work):
        self.link, self.work = link, work
        import openai_cache
        openai_cache.workflow("valmera-podcast-shorts", os.path.basename(work))
        self.state_path = os.path.join(work, "run.json")
        self.state = jload(self.state_path, None) or dict(link=link, video_id=video_id(link), started=now(), done=[], seconds={})
        self._log = os.path.join(work, "log.txt")
        self.walled = threading.Event()                  # set when a short finds YouTube refusing this Mac: the rest wait too
        self.route_out = threading.Event()               # set when the model provider stops answering: the rest wait too

    def save(self):
        with _LOCK:
            jsave(self.state_path, self.state)

    def log(self, msg):
        line = f"{now()} {msg}"
        with _LOCK:
            with open(self._log, "a") as f:
                f.write(line + "\n")
        print(line, flush=True)

    def p(self, *parts):
        return os.path.join(self.work, *parts)


def find_run(link, test=False):
    """The unfinished run of this link when there is one (it continues), else a new work folder. A test run and a
    delivery never continue each other."""
    vid = video_id(link)
    os.makedirs(WORK_ROOT, exist_ok=True)
    for n in sorted(os.listdir(WORK_ROOT), reverse=True):
        st = jload(os.path.join(WORK_ROOT, n, "run.json"), {}) or {}
        if st.get("video_id") == vid and not st.get("delivered") and bool(st.get("test")) == bool(test):
            if alive(st.get("pid")) and st.get("pid") != os.getpid():
                raise SystemExit(f"this link is already running (pid {st['pid']}, {os.path.join(WORK_ROOT, n)})")
            return os.path.join(WORK_ROOT, n), True
    work = os.path.join(WORK_ROOT, f"{vid}-{dt.datetime.now():%Y%m%d-%H%M%S}{'-test' if test else ''}")
    os.makedirs(work)
    return work, False


# ----------------------------------------------------------------- fetch

def _ytdlp(args, timeout=3600):
    py = YTDLP if os.path.exists(YTDLP) else PY
    env = dict(os.environ, PATH=os.pathsep.join([DENO, BIN, os.environ.get("PATH", "/usr/bin:/bin")]))
    cmd = [py, "-m", "yt_dlp", "--ignore-config", "--no-update", "--no-playlist", "--no-warnings", "--socket-timeout", "30", "--retries", "3",
           "--remote-components", "ejs:github", "--ffmpeg-location", BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def rungs():
    """The ways yt-dlp is asked, in order. YouTube walls a busy address as a bot one player client at a time, so a
    refusal moves to the next: the plain call, the embedded player, the mobile web player with a PO token (the
    product's own rung, when the token script is on this Mac), the TV and Safari players, the mobile web player
    without a token (360p, the last resort)."""
    pot = ["--extractor-args", f"youtubepot-bgutilscript:server_home={POT}"] if os.path.isdir(POT) else []
    out = [("plain", []), ("embedded", ["--extractor-args", "youtube:player_client=web_embedded"])]
    if pot:
        out.append(("mweb+token", pot + ["--extractor-args", "youtube:player_client=mweb"]))
    out += [("tv", ["--extractor-args", "youtube:player_client=tv"]), ("safari", ["--extractor-args", "youtube:player_client=web_safari"]),
            ("mweb", ["--extractor-args", "youtube:player_client=mweb"])]
    # 20 Nov (Alexandr Wang): YouTube walled this Mac's IPv6 range while its IPv4 address opened every source, and
    # yt-dlp takes the system's preferred family. Each rung is asked again on IPv4 at once; the ladder then leads
    # with whichever rung delivered (run.state["rung"]).
    return [r for name, extra in out for r in ((name, extra), (name + "+ipv4", ["--force-ipv4"] + extra))]


def ladder(run, args, done, timeout=3600, what="yt-dlp"):
    """yt-dlp with args down the rungs, the one that last worked for this run first. done(result) says a rung
    delivered. Returns the result; raises with yt-dlp's last words when no rung delivers."""
    rs = rungs()
    first = run.state.get("rung")
    rs.sort(key=lambda r: r[0] != first)
    err = "no rung ran"
    for name, extra in rs:
        try:
            r = _ytdlp(extra + args, timeout=timeout)
        except subprocess.TimeoutExpired:
            err = f"{name}: timed out"
            continue
        if done(r):
            if name != first:
                run.log(f"{what}: the {name} rung")
                run.state["rung"] = name
                run.save()
            return r
        err = f"{name}: " + " | ".join((r.stderr or "").strip().splitlines()[-1:])[:300]
    raise RuntimeError(err)


def probe_s(path):
    r = subprocess.run([FP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def earlier_copy(vid, duration):
    """A download of the same video an earlier run left on this Mac: (video path, captions path or None)."""
    if not os.path.isdir(EARLIER):
        return None, None
    for n in sorted(os.listdir(EARLIER)):
        d = os.path.join(EARLIER, n, "source")
        if vid not in n or not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            p = os.path.join(d, f)
            if f.endswith(".mp4") and os.path.getsize(p) > 5 << 20 and (not duration or abs(probe_s(p) - float(duration)) < 3.0):
                caps = [os.path.join(d, c) for c in sorted(os.listdir(d)) if c.endswith(".json3")]
                return p, (caps[0] if caps else None)
    return None, None


def fetch(run):
    src_dir = run.p("source")
    os.makedirs(src_dir, exist_ok=True)
    info = jload(os.path.join(src_dir, "info.json"))
    # A verified cached source must work even when the metadata endpoint is
    # walled. Do not require another network lookup before using those bytes.
    cached = os.environ.get("PODCAST_SOURCE_CACHE")
    if cached:
        ci = jload(os.path.join(cached, "info.json"), {})
        cv = os.path.join(cached, "podcast.mp4")
        if ci.get("id") == run.state["video_id"] and os.path.isfile(cv):
            duration = probe_s(cv)
            expected = float(ci.get("duration") or 0)
            if duration >= 90 and expected >= 90 and abs(duration - expected) < 3:
                caps = sorted(os.path.join(cached, n) for n in os.listdir(cached) if n.endswith(".json3"))
                return dict(video=cv, captions=caps[0] if caps else None, info=ci, duration=duration,
                            bytes=os.path.getsize(cv), how="verified cached source and metadata")
    if not info:
        try:
            r = ladder(run, ["--skip-download", "--dump-single-json", run.link], lambda r: r.returncode == 0 and r.stdout.strip().startswith("{"),
                       timeout=300, what="read the link")
        except RuntimeError as e:
            raise RuntimeError(f"the link could not be read: {e}. When YouTube is refusing this Mac as a bot it lifts by itself: run the same link again later")
        j = json.loads(r.stdout)
        info = {k: j.get(k) for k in ("id", "title", "channel", "uploader", "upload_date", "duration", "webpage_url", "extractor_key")}
        info["description"] = str(j.get("description") or "")[:3000]
        info["chapters"] = [dict(t=c.get("start_time"), title=c.get("title")) for c in j.get("chapters") or []][:60]
        jsave(os.path.join(src_dir, "info.json"), info)
    dur = float(info.get("duration") or 0)
    if dur and dur < 90:
        raise RuntimeError(f"the source is {dur:.0f} s long: nothing to cut shorts from")
    video, caps = earlier_copy(run.state["video_id"], dur)
    how = "an earlier download of the same video on this Mac, used in place"
    if not video:
        video = os.path.join(src_dir, "podcast.mp4")
        how = "downloaded for this run (deleted with the work folder)"
        if not os.path.exists(video):
            free = shutil.disk_usage(src_dir).free
            if free < 3 << 30:
                raise RuntimeError(f"{free >> 20} MB free on the disk: not enough to download the source")
            fmt = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/bv*[height<=1080]+ba/b[height<=1080]/b"

            def got(_r):
                return bool([f for f in os.listdir(src_dir) if f.startswith("podcast.mp4.part.") and not f.endswith((".part", ".ytdl"))])
            run.log("download")
            try:
                ladder(run, ["-f", fmt, "--merge-output-format", "mp4", "-N", "8", "-o", video + ".part.%(ext)s", run.link],
                       lambda r: r.returncode == 0 and got(r), timeout=3 * 3600, what="download")
            except RuntimeError as e:
                raise RuntimeError(f"the video could not be downloaded: {e}")
            os.replace(next(os.path.join(src_dir, f) for f in os.listdir(src_dir) if f.startswith("podcast.mp4.part.") and not f.endswith((".part", ".ytdl"))), video)
    if not caps:
        caps = next((os.path.join(src_dir, f) for f in sorted(os.listdir(src_dir)) if f.endswith(".json3")), None)
    if not caps and not os.path.exists(os.path.join(src_dir, "captions.none")):
        # its own call: a caption refusal never blocks the video
        try:
            ladder(run, ["--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en-orig,en", "--sub-format", "json3",
                         "-o", os.path.join(src_dir, "captions"), run.link],
                   lambda r: any(f.endswith(".json3") for f in os.listdir(src_dir)), timeout=300, what="captions")
        except RuntimeError as e:
            run.log(f"no captions from the platform ({str(e)[:160]}): the source will be heard locally")
        got = sorted((f for f in os.listdir(src_dir) if f.endswith(".json3")), key=lambda f: ("orig" not in f, f))
        caps = os.path.join(src_dir, got[0]) if got else None
        if not caps:
            open(os.path.join(src_dir, "captions.none"), "w").close()
    return dict(video=video, captions=caps, how=how, bytes=os.path.getsize(video), duration=round(probe_s(video), 2), info=info)


# ----------------------------------------------------------------- hear

def captions_words(path):
    """A json3 caption file as [(seconds, word)]. Automatic captions time every word; a written caption times a
    line, and its words share the line's span."""
    out = []
    for e in (jload(path, {}) or {}).get("events") or []:
        t0, span = e.get("tStartMs", 0) / 1000.0, e.get("dDurationMs", 0) / 1000.0
        segs = [s for s in e.get("segs") or [] if (s.get("utf8") or "").strip()]
        timed = any("tOffsetMs" in s for s in segs)
        if timed or len(segs) > 1:
            for s in segs:
                out.append((t0 + s.get("tOffsetMs", 0) / 1000.0, s["utf8"].strip()))
        elif segs:
            ws = segs[0]["utf8"].split()
            for k, w in enumerate(ws):
                out.append((t0 + span * k / max(len(ws), 1), w))
    out.sort()
    return [(round(t, 3), w) for t, w in out]


def whisper_words(run, video):
    """No usable captions: the whole source heard locally (mlx whisper large-v3, through heavy.sh), word-timed."""
    out = run.p("source", "hearing_full.json")
    if not os.path.exists(out):
        wav = run.p("source", "audio.m4a")
        subprocess.run([FF, "-v", "error", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "48k", wav], check=True)
        code = ("import json,sys,mlx_whisper;r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo=sys.argv[2],word_timestamps=True,"
                "condition_on_previous_text=False);json.dump([[w['start'],w['word'].strip()] for g in r['segments'] for w in g.get('words') or []],open(sys.argv[3],'w'))")
        r = subprocess.run(["zsh", HEAVY, "wf1-hear-podcast", ATLAS_PY, "-c", code, wav, WHISPER, out + ".part"], capture_output=True, text=True, timeout=6 * 3600)
        if r.returncode != 0 or not os.path.exists(out + ".part"):
            raise RuntimeError(f"the source could not be heard: {r.stderr.strip()[-400:]}")
        os.replace(out + ".part", out)
        os.remove(wav)
    return [(float(t), w) for t, w in jload(out, [])]


def hear(run, fetched):
    words, how = [], None
    cache = os.environ.get('PODCAST_SOURCE_CACHE')
    if cache and os.environ.get('PODCAST_REPLAY_SELECTION') == '1':
        info = jload(os.path.join(cache,'info.json'),{})
        prior = jload(os.path.join(cache,'words.json'))
        if info.get('id') == fetched['info'].get('id') and prior:
            # Cached alignment belongs to the same complete source, even when
            # its higher-resolution representation has different encoded bytes.
            # fetch() has already verified the source ID and full duration.
            jsave(run.p('words.json'), prior)
            return dict(words=len(prior), transcript='verified source replay word timings',path=run.p('words.json'))
    if fetched.get("captions"):
        words, how = captions_words(fetched["captions"]), f"the platform's captions ({os.path.basename(fetched['captions'])})"
    dur = float(fetched["duration"] or 0)
    thin = not words or (dur and (words[-1][0] < 0.85 * dur or len(words) / max(dur / 60, 1) < 40))
    if thin:
        words, how = whisper_words(run, fetched["video"]), "a local hearing of the whole source (whisper large-v3)"
    jsave(run.p("words.json"), words)
    return dict(words=len(words), transcript=how, path=run.p("words.json"))


# ----------------------------------------------------------------- moments

def _fmt(t):
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def sentence_lines(words, most_s=20.0, pause_s=1.2):
    """The transcript a sentence a line, each with its measured start and end: "[h:mm:ss|start-end] words". A line
    ends on its sentence's own closing mark (the captions' punctuation), before a new speaker's '>>', on a pause of
    pause_s or at most_s. 19 Nov: read in 12 s lines with no word times, 7 of 19 moments ran 61-129 s and hooks lost
    their subject; the model now sees where every sentence starts and ends."""
    out, cur = [], []

    def flush(nxt=None):
        if not cur:
            return
        a, b = cur[0][0], cur[-1][0] + (min(max(nxt - cur[-1][0], 0.15), 0.8) if nxt is not None else 0.6)
        out.append(f"[{_fmt(a)}|{a:.1f}-{b:.1f}] " + " ".join(w for _, w in cur))
        cur.clear()
    for k, (t, w) in enumerate(words):
        w = str(w)
        if cur and (w.lstrip().startswith(">>") or t - cur[-1][0] >= pause_s or t - cur[0][0] >= most_s):
            flush(t)
        cur.append((t, w))
        if w.rstrip('"\'\u201d\u2019)]').endswith((".", "?", "!")):
            flush(words[k + 1][0] if k + 1 < len(words) else None)
    flush()
    return out


TIGHTEN_SYSTEM = (
    "You are the selection stage of Valmera's podcast shorts workflow, tightening one moment you chose: measured on "
    "its sentences' own times it runs longer than a short can. Read its sentences and choose ONE continuous window of "
    "18 to 55 seconds that a stranger understands on its own: it opens on a complete hook whose subject is named in "
    "it (a stranger can tell who or what 'he', 'it' or 'that' is) and ends on a complete payoff. Keep the moment's own "
    "hook and payoff when both fit inside 55 seconds; otherwise choose the strongest self-contained arc inside it. A "
    "list announced anywhere needs its actual members. Choose a self-contained example with an explicit narrower premise when the rest lies outside. Resolve terms from the surrounding discussion.\n\n"
    'Return JSON only: {"t0": the start second of the window\'s first sentence (from its line), "t1": the end second of '
    'its last sentence, "title": a plain title in at most six words, true to what the window says, "why": one '
    'sentence}. Never quote the sentences: give seconds, and the code reads the words itself.')


TIGHTEN_SLACK_S = 2.0       # a tightened window keeps to its moment's own sentences (their measured ends, a word's slack)


def tighten(info, m, words, ask=None):
    """A moment measured over MOMENT_MAX_S, handed back to the selection model with its timed sentences to choose the
    tightest self-contained window (no clamp, no rejection: the moment keeps its hook and payoff or takes a whole arc
    inside it). Returns the spend. When no answer fits, the moment stays as chosen and the script stage cuts it."""
    import llm
    ask = ask or llm.ask
    t0, t1 = float(m["t0"]), float(m["t1"])
    near = [(t, w) for t, w in words if t0 - 10.0 <= t <= t1 + 10.0]
    moment = {k: m.get(k) for k in ("title", "t0", "t1", "hook", "payoff", "why", "names", "shows")}
    content = (f"# The source\n{identity_text(info)}\n\n# The moment, measured at {t1 - t0:.0f} s\n{json.dumps(moment, ensure_ascii=False)}\n\n"
               "# Its sentences: [h:mm:ss|start-end seconds] words ('>>' marks a new speaker)\n" + "\n".join(sentence_lines(near)))
    spend, note = [], ""
    for _ in range(2):
        text, use = ask(TIGHTEN_SYSTEM, content + note, effort="medium", max_tokens=6000)
        spend.append(dict(stage="moments", what=f"tighten {m.get('id') or m.get('title')}", **use))
        try:
            got = llm.json_block(text)
            a, b = float(got["t0"]), float(got["t1"])
        except (ValueError, KeyError, TypeError):
            note = "\n\nYour answer was not the JSON asked for."
            continue
        inside = t0 - TIGHTEN_SLACK_S <= a < b <= t1 + TIGHTEN_SLACK_S      # its own sentences: a neighbour's lines are never told twice
        if 8.0 <= b - a <= MOMENT_MAX_S + 5.0 and inside:
            m["tightened"] = dict(from_s=[t0, t1], why=got.get("why"))
            lines = sentence_lines(near)
            m.update(t0=round(a, 2), t1=round(b, 2), hook=line_at(lines, a, "start") or m.get("hook"),
                     payoff=line_at(lines, b, "end") or m.get("payoff"), title=got.get("title") or m.get("title"))
            break
        where = "inside" if inside else f"reaching outside (by {max(t0 - a, b - t1, 0):.1f} s) a moment that runs"
        note = (f"\n\nYour window {a:.1f}-{b:.1f} s runs {b - a:.0f} s {where} {t0:.1f}-{t1:.1f} s: choose one of "
                f"18-55 s inside it, from its own sentences.")
    m["duration_s"] = round(float(m["t1"]) - float(m["t0"]), 1)
    return spend


def identity_text(info):
    ch = "; ".join(f"{_fmt(c['t'] or 0)} {c['title']}" for c in info.get("chapters") or [] if c.get("title"))
    return (f"Title: {info.get('title')}\nChannel: {info.get('channel') or info.get('uploader')}\nPublished: {info.get('upload_date')}\n"
            f"Length: {_fmt(info.get('duration') or 0)}\nLink: {info.get('webpage_url')}\n"
            + (f"Chapters: {ch}\n" if ch else "") + f"Description: {str(info.get('description') or '')[:1200]}")


SELECT_SYSTEM = (
    "You are the selection stage of Valmera's podcast shorts workflow. You read a whole conversation and find every "
    "moment that becomes a great vertical short on its own: a story with a turn, a claim that stops the scroll, a number "
    "that surprises, a confession, a payoff that lands inside the clip. You find them yourself: nobody gives you a topic "
    "or a count. The films are cut the way the best story editors cut: over what he says, the picture shows real footage "
    "of the people, actions and places he describes. Read through the answer to distinguish the actual members of a list from illustrations of one member. In why, state exactly which the window pays off and unpack shorthand from the conversation.")


SELECT_CONTRACT = '\n\nWalk the whole transcript in order and return every moment that clears the bar: a stranger stops within two seconds, stays to the end, and leaves with something. Grade each moment on its own. There is no target number, no minimum and no maximum: a weak hour gives few, a strong one many. A moment must stand alone without the episode, open on its hook in its first sentence, end on its payoff, run 18 to 55 seconds, and be about at least one real thing a camera has filmed (a person, company, product, place or event, named or plainly meant), because the film shows real footage of what is said. A strong opinion, confession, prediction, number or funny exchange about real things is a moment like a story is. Only talk with nothing real in it at all is left out. Moments never overlap.\n\nNever quote the transcript in your answer: give seconds, and the code reads the words itself. Return JSON only: {"podcast": {"speaker": the main guest\'s full name, "host": the host\'s name or "", "show": the show\'s name or "", "year": the year it was recorded, "short_title": the episode in at most eight words}, "moments": [each {"title": a plain title in at most six words, "t0": the start second of the sentence the hook opens (from its line), "t1": the end second of the sentence the payoff closes (from its line), "why": why a stranger watches to the end, "speakers": how many people speak in it, "names": the people heard or named, "shows": the real filmed things a picture can show for it, most important first, "styles": [{"id": a style id from the list, "why": what in this moment that style needs}] best fit first, at least two, "hook_score": 1-10, "payoff_score": 1-10, "story_score": 1-10, "strength": 1-10 overall}], "left_out": [every other candidate you weighed and left out, each {"t0": seconds, "title": title, "why": the reason}]}'


def line_at(lines, t, edge):
    """The words of the sentence line a moment opens on (edge 'start': the line holding t, else the next) or closes on
    (edge 'end': the last line ending by t). Read from the transcript itself, so the model never quotes it."""
    import re as _re
    try:
        t = float(t)
    except (TypeError, ValueError):
        return ""
    rows = []
    for ln in lines:
        m = _re.match(r"\[[^|]*\|([0-9.]+)-([0-9.]+)\] (.*)", ln)
        if m:
            rows.append((float(m.group(1)), float(m.group(2)), m.group(3)))
    if not rows:
        return ""
    if edge == "start":
        hit = next((r for r in rows if r[0] - 0.3 <= t < r[1]), None) or next((r for r in rows if r[0] >= t - 0.3), rows[-1])
    else:
        before = [r for r in rows if r[1] <= t + 0.8]
        hit = before[-1] if before else rows[0]
    return hit[2]


def select_prompt(info, styles, lines, part=None):
    """part: (k, n, t0, t1): this reading returns the moments that start between t0 and t1 s; the lines after t1 are
    only there so a moment that starts before t1 is read to its payoff."""
    import wf1_film
    menu = "\n".join(f"- {s['id']}: {s['name']}. {s.get('look', '')} Needs: {s.get('needs', '')}." for s in styles if s["id"] in wf1_film.STYLES)
    head = ''
    if part:
        head = (f'# This is part {part[0]} of {part[1]} of the transcript, {_fmt(part[2])} to {_fmt(part[3])}\n'
                f'Return every moment that STARTS in this part (t0 from {part[2]:.0f} to {part[3]:.0f} s); the lines after '
                f'{_fmt(part[3])} are there only to read a moment that starts before it to its payoff.\n\n')
    return (f'# The source\n{identity_text(info)}\n\n# The styles a short can take\n{menu}\n\n' + head +
            "# Transcript, one sentence a line: [h:mm:ss|start-end seconds] words ('>>' marks a new speaker)\n" + '\n'.join(lines))


def parts_of(words, part_s=PART_S, overlap_s=PART_OVERLAP_S):
    """The conversation in parts of part_s seconds, each with overlap_s more after its end: [(t0, t1, words)].
    One reading of a whole hour returns about ten moments whatever the hour holds (Jensen Huang, CBS, 46 min, 15 Nov:
    10 kept, 7 more named as left out); a reading of ten minutes walks every minute of its part."""
    if not words:
        return [(0.0, 0.0, [])]
    end = float(words[-1][0])
    out, t = [], float(words[0][0])
    while t <= end:
        t1 = t + part_s
        if end - t1 < part_s / 3:                    # a short tail joins the part before it
            t1 = end + 1
        out.append((t, t1, [(a, w) for a, w in words if t <= a < t1 + overlap_s]))
        t = t1
    return out


def assign_styles(moments, order):
    """A style per short, in rank order: never the style of either short before it, the least-used style first, and
    among equals the moment's own best fit. Returns the moments with `style` set. A style is the short's type and colour
    identity (its display font, accent, grain), a starting palette: the devices, layouts, surfaces and pace of every
    moment are the short's Director's (wf1_short.direction_of), so no style fixes a device set."""
    used = {s: 0 for s in order}
    recent = []
    for m in moments:
        fits = [s.get("id") for s in m.get("styles") or [] if isinstance(s, dict) and s.get("id") in used]
        cands = list(dict.fromkeys(fits + order))
        free = [s for s in cands if s not in recent[-2:]] or [s for s in cands if s not in recent[-1:]] or cands
        low = min(used[s] for s in free)
        pick = next(s for s in free if used[s] == low)
        m["style"] = pick
        used[pick] += 1
        recent.append(pick)
    return moments


def rank(rows, bar=BAR):
    """Every moment at or over the bar, strongest first; one that overlaps a stronger one is dropped. No count."""
    good = []
    for r in rows:
        try:
            r["t0"], r["t1"], r["strength"] = float(r["t0"]), float(r["t1"]), float(r.get("strength") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if r["t1"] - r["t0"] >= 8 and r["strength"] >= bar and len(r.get("shows") or []) >= 1:
            good.append(r)
    good.sort(key=lambda r: (-r["strength"], -(float(r.get("hook_score") or 0) + float(r.get("payoff_score") or 0)), r["t0"]))
    kept = []
    for r in good:
        if not any(min(r["t1"], k["t1"]) - max(r["t0"], k["t0"]) > 2.0 for k in kept):
            kept.append(r)
    for i, r in enumerate(kept):
        r["rank"], r["id"] = i + 1, f"m{i + 1:02d}"
    return kept


def moments(run, fetched):
    import llm
    import shell
    import wf1_film
    words = [(float(t), w) for t, w in jload(run.p("words.json"), [])]
    styles = shell.registry()["wf1"]["styles"]
    info = fetched["info"]
    cache = os.environ.get("PODCAST_SOURCE_CACHE")
    if cache and os.environ.get("PODCAST_REPLAY_SELECTION") == "1":
        prior_info = jload(os.path.join(cache, "info.json"), {})
        prior_words = jload(os.path.join(cache, "words.json"))
        prior = jload(os.path.join(cache, "moments.json"))
        if prior_info.get("id") != info.get("id") or prior_words != jload(run.p("words.json")) or not prior:
            raise RuntimeError("selection replay requires identical source identity and word timings")
        # A regression run reuses the model's untouched selection, never a
        # hand-authored edit plan. Every short is rebuilt through the product.
        return dict(prior, spend=[], replayed_selection=True)
    parts = parts_of(words)
    rows, left, pod, spend = [], [], {}, []
    for k, (p0, p1, ws) in enumerate(parts):
        content = select_prompt(info, styles, sentence_lines(ws), part=(k + 1, len(parts), p0, p1) if len(parts) > 1 else None)
        got, note = {}, ""
        for _ in range(3):
            text, use = llm.ask(SELECT_SYSTEM + SELECT_CONTRACT, content + note, effort="high", max_tokens=48000)
            spend.append(dict(stage="moments", what="find and grade every moment", **use))
            try:
                got = llm.json_block(text)
            except ValueError:
                got = {}
            if isinstance(got, dict) and isinstance(got.get("moments"), list):
                break
            # 20 Nov (Alexandr Wang, YC): the Claude backend's output check stopped an answer that quoted the
            # transcript line by line, and the identical second ask came back from the proxy's cache. Asked again in
            # other words (so it is a new request), for seconds and titles only.
            note = ("\n\nYour last answer could not be used. Answer again with the JSON only: seconds, short titles of "
                    "your own and scores. Do not quote or paraphrase the transcript's sentences.")
        else:
            raise RuntimeError("the selection gave no moments")
        lines = sentence_lines(ws)
        rows += [dict(r, part=k + 1, hook=line_at(lines, r.get("t0"), "start"), payoff=line_at(lines, r.get("t1"), "end"))
                 for r in got["moments"] if isinstance(r, dict)]
        left += [r for r in got.get("left_out") or [] if isinstance(r, dict)]
        pod = pod or (got.get("podcast") if isinstance(got.get("podcast"), dict) else {})
    under = [dict(t0=r.get("t0"), title=r.get("title"), why=f"graded {r.get('strength')} of 10, under the bar of {BAR:g}") for r in rows
             if float(r.get("strength") or 0) < BAR]
    kept = rank(rows)
    for m in kept:                                   # measured on its sentences' times: a moment longer than a short is tightened
        if float(m["t1"]) - float(m["t0"]) > MOMENT_MAX_S:
            spend += tighten(info, m, words)
        m["duration_s"] = round(float(m["t1"]) - float(m["t0"]), 1)
    kept = assign_styles(kept, list(wf1_film.ORDER))
    return dict(podcast=pod, found=len(rows), parts=len(parts), moments=kept, left_out=under + left, bar=BAR, spend=spend)


# ----------------------------------------------------------------- the shorts

def _last_json(text):
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def route_out(err):
    """A model call the provider would not answer at all: rate or usage limited (HTTP 429), unavailable (503: the local
    adapter's cap or its latch after the subscription's usage limit), or the subscription's own "usage limit" answer
    passed through as a 502 on the first call that meets it. Not this short's fault, and it does not lift in a minute:
    the short is kept to continue, the shorts not started wait, and the podcast is never delivered short of them
    (16 Nov: the Codex limit was hit at m05 and m06-m10 were each marked failed within four seconds). Any other model
    error stays with its short, which is resumed once."""
    e = str(err or "")
    return "openai 429" in e or "openai 503" in e or ("openai 502" in e and "usage limit" in e)


def footage_routes(env_file=None):
    """The operator's footage egress, put where every footage fetch of the run reads it: wf1_short's process and the
    footage engine it starts inherit this process's environment (worker config.YTDLP_PROXY and YTDLP_PROXY_FALLBACK, the
    product's proxy routes down the player ladder; FOOTAGE_FETCH_CMD, the operator's own transport). A key already in
    the environment stands as it is, even empty (a test round blanks them); a key absent is read from the backend's
    .env, these keys only. Values are never printed. Returns the keys that carry a route."""
    path = env_file or ENV_FILE
    if any(k not in os.environ for k in ROUTE_KEYS) and os.path.isfile(path):
        try:
            for line in open(path):
                k, sep, v = line.strip().partition("=")
                k = k.strip().removeprefix("export ").strip()
                if sep and k in ROUTE_KEYS and k not in os.environ:
                    os.environ[k] = v.strip().strip('"').strip("'")
        except OSError:
            pass
    return [k for k in ROUTE_KEYS if os.environ.get(k)]


# One pace for every YouTube fetch of a run (footage.fetch.pace): the shorts a run makes at once, their footage engines
# and the refused-source retries draw from one file-locked bucket under the run folder, at most one request every
# FOOTAGE_PACE_S seconds after a burst of FOOTAGE_PACE_BURST (19 Nov: two lanes fired about 100 source requests in a
# few minutes, the burst that brought the wall down on this Mac). A key already in the environment stands.
FOOTAGE_PACE = (("FOOTAGE_PACE_S", "3"), ("FOOTAGE_PACE_BURST", "4"))


def footage_pace(run):
    """Set the run-wide pace of the run's YouTube fetches; every process the run starts inherits it. Returns its folder."""
    os.environ.setdefault("FOOTAGE_PACE_DIR", run.p("pace"))
    for k, v in FOOTAGE_PACE:
        os.environ.setdefault(k, v)
    return os.environ["FOOTAGE_PACE_DIR"]


def past_supply(state):
    """True when this short's footage is in hand (its run has finished the supply stage): it can go on while a wall
    keeps the shorts that still need footage waiting."""
    work = os.path.join(state, "work")
    if not os.path.isdir(work):
        return False
    return any("supply" in ((jload(os.path.join(work, n, "run.json"), {}) or {}).get("done") or []) for n in os.listdir(work))


def build_short(run, m, fetched, pod, tries=3):
    """One moment through wf1_short.py. Returns its record: {id, ok, folder | error}."""
    state = run.p("shorts", m["id"])
    if os.environ.get('CODEX_PROXY_LOCAL') == '1':     # on the subscription route a stopped short is resumed once from the stage
        tries=min(tries,2)                             # where it stopped (16 Nov: m03 failed in render, then was made on its resume)
    os.makedirs(state, exist_ok=True)
    import wf1_film
    done = jload(os.path.join(state, "done.json"))
    if done and done.get("ok"):                          # a short that was not made is tried again when the link is run again
        return done
    if run.walled.is_set() and not past_supply(state):   # footage cannot be fetched right now: a short that still needs it waits
        return dict(id=m["id"], ok=False, error=f"{wf1_film.WALL}: not started")
    if run.route_out.is_set():
        return dict(id=m["id"], ok=False, error=f"{ROUTE_OUT}: not started")
    info = fetched["info"]
    args = dict(moment=m, words=run.p("words.json"), style=m["style"], music_ledger=run.p("music_used.json"),      # no two shorts of a run play one track
                delivered_ledger="off",                  # an inner short; podcast.py records the podcast's one folder
                identity=(f"Speaker: {pod.get('speaker') or ''}. Host: {pod.get('host') or ''}. Show: {pod.get('show') or ''} "
                          f"({pod.get('year') or ''}).\n" + identity_text(info))[:1800],
                source_url=info.get("webpage_url") or run.link, video_id=run.state["video_id"], source_title=info.get("title"),
                channel=info.get("channel") or info.get("uploader"), upload_date=info.get("upload_date"))
    if run.state.get('test') and run.state.get('audit_root'):
        args['audit_out']=os.path.join(run.state['audit_root'],m['id'])
    films = run.p("films")
    os.makedirs(films, exist_ok=True)
    base = [PY, os.path.join(HERE, "wf1_short.py"), "run", "--state-root", state]
    err = ""
    for attempt in range(tries):
        work = os.path.join(state, "work")
        stopped = [n for n in sorted(os.listdir(work)) if jload(os.path.join(work, n, "run.json"))] if os.path.isdir(work) else []
        for n in stopped:                                        # a run of this short left by a start that died: let it end, then continue it
            st = jload(os.path.join(work, n, "run.json"), {})
            while alive(st.get("pid")):
                time.sleep(20)
                st = jload(os.path.join(work, n, "run.json"), {}) or {}
        led = (jload(os.path.join(state, "ledger.json"), {}) or {}).get("wf1") or []
        if led and os.path.exists(os.path.join(led[-1].get("delivered") or "", "film.json")):
            rec = dict(id=m["id"], ok=True, folder=led[-1]["delivered"], attempts=attempt)
            jsave(os.path.join(state, "done.json"), rec)
            return rec
        stopped = [n for n in stopped if os.path.isdir(os.path.join(work, n))]
        cmd = base + (["--resume", stopped[-1], "--out", films] if stopped else
                      ["--source", fetched["video"], "--out", films, "--args", json.dumps(args, ensure_ascii=False)])
        run.log(f"{m['id']} ({m['style']}) {'resume ' + stopped[-1] if stopped else 'start'}: {m.get('title')}")
        with open(os.path.join(state, "log.txt"), "a") as lf:
            r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=lf, text=True)
            lf.write(r.stdout or "")
        rep = _last_json(r.stdout)
        if r.returncode in (0, 3) and rep.get("delivered"):
            rec = dict(id=m["id"], ok=True, folder=rep["delivered"], checks_ok=rep.get("checks_ok"), defects=rep.get("defects"),
                       work_empty=rep.get("work_empty"), stage_seconds=rep.get("stage_seconds"), attempts=attempt + 1)
            jsave(os.path.join(state, "done.json"), rec)
            run.log(f"{m['id']} made: {os.path.basename(rep['delivered'])}")
            return rec
        st = rep.get("stopped") or {}
        if not st and not (r.stdout or "").strip():
            # The short died before its first stage could report (20 Nov 05:35 UTC: macOS withdrew this run's
            # access to ~/Documents and 17 shorts each "stopped (start: )" within a second, then the set was
            # delivered short of them). That is the machine, not the moment: the run holds, nothing is delivered
            # short, and the same link continues every short where it stopped.
            try:
                with open(os.path.join(state, "log.txt")) as lf:
                    why = " ".join(lf.read()[-600:].split())[-300:]
            except OSError as e:
                why = f"its log is unreadable too ({e})"
            run.log(f"{m['id']} could not start ({why[:200]})")
            run.route_out.set()
            err = f"{ROUTE_OUT}: the short could not start: {why}"
            break
        new = f"{st.get('stage') or 'start'}: {st.get('error') or (r.stdout or '')[-300:]}"
        run.log(f"{m['id']} stopped ({new[:200]})")
        if wf1_film.WALL in new:                                 # not this short's fault and not gone in a minute: the run waits for the wall to lift
            run.walled.set()
            err = new
            break
        if route_out(new):                                       # the same for the model route: its work folder is resumed next run
            run.route_out.set()
            err = f"{ROUTE_OUT}: {new}"
            break
        if new == err:                                           # the same fault twice is not the network: this short cannot be made as planned
            break
        err = new
    rec = dict(id=m["id"], ok=False, error=err[:600])
    jsave(os.path.join(state, "done.json"), rec)
    return rec


def shorts(run, fetched, sel, lanes):
    # The fetched full podcast has already passed identity/duration checks.
    # Let footage reuse that exact original when its research finds the same
    # URL; a network wall must not make us download our own source again.
    os.environ["FOOTAGE_LOCAL_SOURCES"] = json.dumps([
        dict(url=url, path=os.path.realpath(fetched["video"]))
        for url in dict.fromkeys([run.link, fetched["info"].get("webpage_url") or run.link])
    ])
    routes = footage_routes()
    footage_pace(run)
    run.log(f"footage fetches paced run-wide: one every {os.environ['FOOTAGE_PACE_S']} s after a burst of "
            f"{os.environ['FOOTAGE_PACE_BURST']}")
    run.log("footage routes: " + (", ".join(routes) if routes else "this Mac's own address (no proxy or transport configured)"))
    import wf1_film
    ms = sel["moments"]
    with cf.ThreadPoolExecutor(max_workers=max(1, lanes)) as pool:
        recs = list(pool.map(lambda m: build_short(run, m, fetched, sel.get("podcast") or {}), ms))
    for wait in WALL_WAITS:                                # the wall lifts by itself: wait, then continue the walled shorts
        waiting = [r["id"] for r in recs if wf1_film.WALL in str(r.get("error"))]
        if not waiting or run.route_out.is_set():
            break
        run.log(f"{len(waiting)} shorts wait for footage ({wf1_film.WALL}): trying their refused sources again in {wait / 60:.0f} min")
        time.sleep(wait)
        run.walled.clear()
        again = [m for m in ms if m["id"] in waiting]
        with cf.ThreadPoolExecutor(max_workers=max(1, lanes)) as pool:
            redone = {r["id"]: r for r in pool.map(lambda m: build_short(run, m, fetched, sel.get("podcast") or {}), again)}
        recs = [redone.get(r["id"], r) for r in recs]
    return dict(made=[r for r in recs if r.get("ok")], failed=[r for r in recs if not r.get("ok")])


# ----------------------------------------------------------------- deliver

def _sources_md(pod_line, rows):
    out = ["# Sources", "", "Every short is cut from one conversation:", "", pod_line, "",
           "Real footage is not licensed (the owner's decision, 13 Nov 2026): whoever publishes a short decides to keep it. "
           "Photographs carry their own licences. Music is from Valmera's licensed library: its credit line goes wherever the short is published.", ""]
    for r in rows:
        f = r["film"]
        ch = f.get("checks") or {}
        out += [f"## {r['n']:02d}. {r['title']}", "", f"- The podcast, {f['window'][0]:.1f}-{f['window'][1]:.1f} s ({r['style']})"]
        credits = (ch.get("stories") or {}).get("credits") or []
        by_clip = {(c.get("page"), tuple(c.get("time_range") or ())): c for c in credits if c.get("kind") == "footage"}
        by_page = {c.get("page"): c for c in credits}
        seen = set()
        for p in (ch.get("podcast") or {}).get("sources") or []:          # every picture in the film, in the order it is shown
            at = f"shown at {p['f'][0] / 30:.1f}-{p['f'][1] / 30:.1f} s" if p.get("f") else ""
            tr = tuple(p.get("time_range") or ())
            evidence = p.get("source_evidence") or {}
            subject = p.get("name_super") or p.get("attribution") or evidence.get("title") or p.get("name")
            if p.get("kind") == "clip":
                c = by_clip.get((p.get("source"), tr)) or by_page.get(p.get("source")) or {}
                seen.add((p.get("source"), tr))
                span = f"{tr[0]:.1f}-{tr[1]:.1f} s of the source" if len(tr) == 2 else ""
                out.append(f"- Footage ({subject}): {evidence.get('title') or c.get('title')} | {evidence.get('channel') or c.get('credit')} | {p.get('source')} | {span} | {at} | not licensed")
            else:
                c = by_page.get(p.get("source")) or {}
                out.append(f"- Photograph ({subject}): {c.get('title') or c.get('name')} | {c.get('credit')} | {c.get('licence')} | {p.get('source')} | {at}")
        spare = [c for k, c in by_clip.items() if k not in seen]
        if spare:
            out.append(f"- Found and not used: {len(spare)} more clips from " + ", ".join(sorted({str(c.get('page')) for c in spare})))
        mu = f.get("music") or {}
        if mu.get("title"):
            out.append(f"- Music: {mu.get('title')} by {mu.get('author')} | {mu.get('license')} | {mu.get('page_url')} | credit line: {mu.get('credit')}")
        snd = (ch.get("music") or {}).get("sounds")
        if snd:
            out.append(f"- Sound effects: {snd} recordings from Valmera's sound library")
        out.append("")
    return "\n".join(out)


# ----------------------------------------------------------------- never the same podcast twice

def link_ids(link):
    """The exact ids a link names before anything is fetched: yt:<video id> for YouTube, else its canonical URL."""
    sid = DL.source_id_of_link(link)
    return [sid] if sid else []


def source_ids(run, fetched):
    """The exact ids of the fetched source: the link's, the platform's own id, and the sha256 of the video's bytes
    (hashed once per run)."""
    ids = link_ids(run.link)
    info = fetched.get("info") or {}
    if str(info.get("extractor_key") or "").lower().startswith("youtube") and info.get("id"):
        ids.append(f"yt:{info['id']}")
    if info.get("webpage_url"):
        ids += link_ids(info["webpage_url"])
    if not run.state.get("source_sha256") and os.path.isfile(fetched.get("video") or ""):
        run.state["source_sha256"] = DL.sha256_file(fetched["video"])
        run.save()
    if run.state.get("source_sha256"):
        ids.append(run.state["source_sha256"])
    return sorted(set(ids))


def refused(hit, sid, link):
    """The one JSON line a refused link prints."""
    return json.dumps(dict(refused="already delivered: the same podcast is never delivered twice", link=link, source_id=sid,
                           delivered=hit.get("folder"), delivered_at=hit.get("delivered_at"), title=(hit.get("idea") or {}).get("title"),
                           note="another version of it needs another source, or --test-out <dir> for a test that never delivers"),
                      ensure_ascii=False)


def deliver(run, fetched, sel, built, out_root, test=False):
    import wf1_film
    if test and built['failed']:
        failed=', '.join(r['id'] for r in built['failed'])
        raise RuntimeError('Incomplete test set remains resumable; fix the recorded failures and resume: '+failed)
    out = [r for r in built["failed"] if ROUTE_OUT in str(r.get("error"))]
    if out:                                              # delivered once, complete: never short of the moments the model route left
        raise RuntimeError(f"{len(out)} of {len(sel['moments'])} shorts are waiting for the model route ({ROUTE_OUT}: usage limit, cap or outage). "
                           "Run the same link again when it answers: the shorts already made are kept, a stopped short continues "
                           "where it stopped, and nothing is delivered until the set is whole")
    waiting = [r for r in built["failed"] if wf1_film.WALL in str(r.get("error"))]
    if waiting:                                          # a folder is delivered once, complete: not while shorts wait for their footage
        raise RuntimeError(f"{len(waiting)} of {len(sel['moments'])} shorts are waiting for footage: {wf1_film.WALL} on every player. It lifts by "
                           "itself, usually within hours. Run the same link again then: the shorts already made are kept and nothing is delivered until the set is whole")
    pod, info = sel.get("podcast") or {}, fetched["info"]
    made = {r["id"]: r for r in built["made"]}
    rows = []
    for m in sel["moments"]:
        r = made.get(m["id"])
        f = jload(os.path.join(r["folder"], "film.json")) if r else None
        if not f or not os.path.exists(os.path.join(r["folder"], "film.mp4")):
            continue
        rows.append(dict(n=len(rows) + 1, id=m["id"], rank=m["rank"], title=(f.get("script") or {}).get("title") or m.get("title"),
                         style=(f.get("style") or {}).get("id"), folder=r["folder"], film=f, moment=m))
    if not rows:
        raise RuntimeError("no short was made: nothing to deliver (the work folder is kept; run the same link again)")
    year = pod.get("year") or str(info.get("upload_date") or "")[:4]
    name = clean_name(f"{pod.get('speaker') or info.get('channel') or 'Podcast'} - {pod.get('short_title') or info.get('title')} ({year}) - {len(rows)} Shorts", 150)
    final = os.path.join(out_root, name)
    if os.path.exists(final):
        final += f" - {dt.datetime.now():%Y%m%d-%H%M}"
    os.makedirs(out_root, exist_ok=True)
    part = os.path.join(out_root, f".{os.path.basename(final)}.partial")
    shutil.rmtree(part, ignore_errors=True)
    os.makedirs(os.path.join(part, "clean"))
    os.makedirs(os.path.join(part, "records"))
    link = info.get("webpage_url") or run.link
    listing = []
    for r in rows:
        f = r["film"]
        fn = f"{r['n']:02d} - {clean_name(r['title'], 70)}.mp4"
        shutil.copyfile(os.path.join(r["folder"], "film.mp4"), os.path.join(part, fn))
        clean = os.path.join(r["folder"], "film_clean.mp4")
        has_music = os.path.exists(clean)
        shutil.copyfile(clean if has_music else os.path.join(r["folder"], "film.mp4"), os.path.join(part, "clean", fn))
        jsave(os.path.join(part, "records", f"{r['n']:02d}.json"), f)
        ch = f.get("checks") or {}
        pc = ch.get("podcast") or {}
        mu = f.get("music") or {}
        listing.append(dict(n=r["n"], file=fn, clean=f"clean/{fn}", record=f"records/{r['n']:02d}.json", title=r["title"], rank=r["rank"], strength=r["moment"].get("strength"),
                            style=r["style"], window=f.get("window"), seconds=ch.get("final_s"), hook=(f.get("script") or {}).get("hook_line"),
                            close=(f.get("script") or {}).get("close_line"), why=r["moment"].get("why"), checks_ok=ch.get("ok"),
                            end_card=ch.get("endcard_ok"), watermark=ch.get("mark_ok"), lufs=ch.get("lufs"),
                            pictures=pc.get("pictures"), clips=pc.get("clips"), photographs=pc.get("photographs"), new_pictures_10s=pc.get("new_pictures_10s"),
                            display_words=pc.get("display_words"), finishes=pc.get("finishes"), alien=pc.get("alien"),
                            sounds=(ch.get("music") or {}).get("sounds"),
                            music=(dict(title=mu.get("title"), author=mu.get("author"), license=mu.get("license"), credit=mu.get("credit")) if has_music and mu.get("title") else None),
                            sources=pc.get("sources"), spend_usd=f.get("spend_usd"),
                            platform_music=(dict(f["platform_music"], upload=f"clean/{fn}") if f.get("platform_music") else None)))
    spend = round(sum(float(x.get("spend_usd") or 0) for x in listing) + sum(float(s.get("usd") or 0) for s in sel.get("spend") or []), 2)
    failed = [dict(id=r["id"], title=next((m.get("title") for m in sel["moments"] if m["id"] == r["id"]), None), error=r.get("error")) for r in built["failed"]]
    manifest = dict(contract=1, workflow="valmera-podcast-shorts", delivered=now(), published=False,
                    source=dict(link=link, title=info.get("title"), channel=info.get("channel") or info.get("uploader"), upload_date=info.get("upload_date"),
                                duration_s=fetched.get("duration"), acquired=fetched.get("how"), transcript=run.state.get("transcript")),
                    podcast=pod, moments_found=sel.get("found"), bar=sel.get("bar"), shorts=listing, not_made=failed, left_out=sel.get("left_out"),
                    spend_usd=spend, seconds=run.state.get("seconds"))
    jsave(os.path.join(part, "shorts.json"), manifest)
    songs = [x["platform_music"] for x in listing if x.get("platform_music")]
    if songs:   # the song each short is posted with, from the platform's own catalog (owner, 15 Nov 2026; _shell/platform_song.py)
        jsave(os.path.join(part, "platform_music.json"), dict(contract=1, how=songs[0].get("how"), shorts=[
            dict(n=x["n"], upload=x["platform_music"]["upload"], **{k: x["platform_music"].get(k) for k in (
                "song", "artist", "start", "original_sound_pct", "added_sound_pct", "length_s", "cover", "alternates", "why")})
            for x in listing if x.get("platform_music")]))
    pod_line = f"- {info.get('title')} | {info.get('channel') or info.get('uploader')} | {link} | published {info.get('upload_date')}"
    with open(os.path.join(part, "SOURCES.md"), "w") as fh:
        fh.write(_sources_md(pod_line, rows) + "\n")
    with open(os.path.join(part, "README.md"), "w") as fh:
        fh.write(f"# {os.path.basename(final)}\n\n{len(rows)} shorts from {link}, strongest first. Delivered once, complete: nothing is added to this folder later.\n\n"
                 "Not published. Each short keeps Valmera's watermark and end card. `clean/` holds the same shorts without music.\n"
                 + ("Post the `clean/` file of each short with the song platform_music.json names, added from the platform's own catalog.\n" if any(x.get("platform_music") for x in listing) else "")
                 + 
                 "Rights: the real footage is not licensed, and the music needs its credit line wherever a short is posted. See SOURCES.md.\n\n"
                 "| # | Short | Style | Seconds | Pictures | Music |\n|---|---|---|---:|---:|---|\n"
                 + "\n".join(f"| {x['n']:02d} | {x['title']} | {x['style']} | {x['seconds'] or 0:.1f} | {x['pictures'] or 0} | {(x['music'] or {}).get('title') or 'none'} |" for x in listing)
                 + "\n\n" + (("Not made:\n" + "\n".join(f"- {x['title']}: {x['error']}" for x in failed) + "\n\n") if failed else "")
                 + f"Moments found: {sel.get('found')}; at or over the bar ({sel.get('bar'):g} of 10): {len(sel['moments'])}. shorts.json has every number.\n")
    if test:                                             # a test run: its own folder, never the ledger
        os.rename(part, final)
    else:                                                # the rename and the ledger row in one lock; an exact source delivered meanwhile is refused
        idea = dict(title=info.get("title"), subject=pod.get("speaker") or info.get("channel"), angle=pod.get("short_title"),
                    style="podcast shorts", beats=[x["title"] for x in listing], sources=[link])
        try:
            DL.deliver_once(DL.make_row("wf1", final, source_ids(run, fetched), idea, shorts=len(rows)),
                            commit=lambda: os.rename(part, final))
        except DL.AlreadyDelivered:
            shutil.rmtree(part, ignore_errors=True)
            raise
    return dict(folder=final, shorts=len(rows), not_made=failed, spend_usd=spend, test=test)


def main():
    ap = argparse.ArgumentParser(description="A podcast or interview link in, one folder of finished shorts out.")
    ap.add_argument("link")
    ap.add_argument("--out", default=None, help=argparse.SUPPRESS)              # where the one folder goes; the owner's Podcast Shorts by default
    ap.add_argument("--test-out", default=None,
                    help="a test run: may reuse a delivered source, writes only here, never to a delivery folder or the ledger")
    ap.add_argument("--lanes", type=int, default=LANES, help=argparse.SUPPRESS)
    ap.add_argument("--keep-work", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.test_out and a.out:
        ap.error("--test-out cannot be combined with --out: a test run never delivers")
    if a.test_out and DL.in_delivery_root(a.test_out):
        ap.error(f"--test-out {a.test_out} is inside a delivery folder; a test run never writes there")
    out_root = os.path.abspath(a.test_out or a.out or OUT_ROOT)
    # only a folder inside the owner's delivery folders is a delivery (however its path is spelled); any other --out
    # is a test like --test-out: it may reuse a source and is never recorded
    test = bool(a.test_out) or not DL.records(out_root)
    if not test:                                         # before anything is fetched: the link's own id against the ledger
        hit, sid = DL.find(link_ids(a.link), "wf1")
        if hit:
            print(refused(hit, sid, a.link))
            return 4
    work, resumed = find_run(a.link, test)
    run = Run(a.link, work)
    run.state["test"] = test
    if test:run.state['audit_root']=os.path.join(out_root,'evidence')
    run.state["pid"] = os.getpid()
    run.save()
    run.log(f"{'continuing' if resumed else 'new run'}: {a.link} in {work}")
    results = {}
    try:
        for stage in ("fetch", "hear", "moments", "shorts", "deliver"):
            t0 = time.time()
            cached = jload(run.p(f"{stage}.json")) if stage in run.state["done"] else None
            if cached is not None and stage != "shorts":
                results[stage] = cached
                if stage == "fetch" and not test:
                    hit, sid = DL.find(source_ids(run, cached), "wf1")
                    if hit:
                        run.state["pid"] = None
                        run.save()
                        print(refused(hit, sid, a.link))
                        return 4
                continue
            run.log(f"stage {stage}")
            if stage == "fetch":
                r = fetch(run)
            elif stage == "hear":
                r = hear(run, results["fetch"])
                run.state["transcript"] = r["transcript"]
            elif stage == "moments":
                r = moments(run, results["fetch"])
                run.log(f"moments: {r['found']} found, {len(r['moments'])} at or over the bar: "
                        + "; ".join(f"{m['id']} {m['strength']:g} {m['style']} {m.get('title')}" for m in r["moments"]))
                if not r["moments"]:
                    raise RuntimeError("no moment of this source clears the bar")
            elif stage == "shorts":
                r = shorts(run, results["fetch"], results["moments"], a.lanes)
            else:
                r = deliver(run, results["fetch"], results["moments"], results["shorts"], out_root, test=test)
            results[stage] = r
            jsave(run.p(f"{stage}.json"), r)
            if stage == "fetch" and not test:            # the platform's id and the bytes' sha256, before any model is paid
                hit, sid = DL.find(source_ids(run, r), "wf1")
                if hit:
                    run.state["pid"] = None
                    run.state["refused"] = dict(source_id=sid, delivered=hit.get("folder"), at=now())
                    run.save()
                    print(refused(hit, sid, a.link))
                    if not a.keep_work:
                        shutil.rmtree(work, ignore_errors=True)
                    return 4
            if stage not in run.state["done"]:
                run.state["done"].append(stage)
            run.state["seconds"][stage] = round(run.state["seconds"].get(stage, 0) + time.time() - t0, 1)
            run.save()
    except DL.AlreadyDelivered as e:                     # delivered by another run meanwhile: nothing is delivered twice
        run.state["pid"] = None
        run.save()
        print(refused(e.row, e.source_id, a.link))
        return 4
    except Exception as e:  # noqa: BLE001 - a stopped run is kept and continues when the same link is given again
        run.log(f"STOPPED in {stage}: {e}")
        run.state["pid"] = None
        run.state["stopped"] = dict(stage=stage, error=str(e)[:600], at=now())
        run.save()
        print(json.dumps(dict(stopped=run.state["stopped"], work=work, resume=f"podcast.py {a.link}")))
        return 2
    d = results["deliver"]
    run.state["delivered"] = d["folder"]
    run.save()
    if not a.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    report = dict(delivered=d["folder"], shorts=d["shorts"], not_made=d["not_made"], spend_usd=d["spend_usd"],
                  work_empty=not os.path.exists(work), seconds=run.state["seconds"])
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["work_empty"] or a.keep_work else 3


if __name__ == "__main__":
    sys.exit(main() or 0)
