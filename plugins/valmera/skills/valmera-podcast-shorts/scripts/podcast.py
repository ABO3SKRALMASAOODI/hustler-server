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
_LOCK = threading.Lock()


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
        self.state_path = os.path.join(work, "run.json")
        self.state = jload(self.state_path, None) or dict(link=link, video_id=video_id(link), started=now(), done=[], seconds={})
        self._log = os.path.join(work, "log.txt")

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


def find_run(link):
    """The unfinished run of this link when there is one (it continues), else a new work folder."""
    vid = video_id(link)
    os.makedirs(WORK_ROOT, exist_ok=True)
    for n in sorted(os.listdir(WORK_ROOT), reverse=True):
        st = jload(os.path.join(WORK_ROOT, n, "run.json"), {}) or {}
        if st.get("video_id") == vid and not st.get("delivered"):
            if alive(st.get("pid")) and st.get("pid") != os.getpid():
                raise SystemExit(f"this link is already running (pid {st['pid']}, {os.path.join(WORK_ROOT, n)})")
            return os.path.join(WORK_ROOT, n), True
    work = os.path.join(WORK_ROOT, f"{vid}-{dt.datetime.now():%Y%m%d-%H%M%S}")
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
    return out


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
    if not info:
        try:
            r = ladder(run, ["--skip-download", "--dump-single-json", run.link], lambda r: r.returncode == 0 and r.stdout.strip().startswith("{"),
                       timeout=300, what="read the link")
        except RuntimeError as e:
            raise RuntimeError(f"the link could not be read: {e}")
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


def lines_of(words, step=12.0):
    out, cur, t_start = [], [], None
    for t, w in words:
        if t_start is None:
            t_start = t
        if t - t_start >= step and cur:
            out.append(f"[{_fmt(t_start)}|{t_start:.0f}] " + " ".join(cur))
            cur, t_start = [], t
        cur.append(w)
    if cur:
        out.append(f"[{_fmt(t_start)}|{t_start:.0f}] " + " ".join(cur))
    return out


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
    "of the people, companies, places and events he names, a new picture about every second.")


def select_prompt(info, styles, lines, part=None):
    import wf1_film
    menu = "\n".join(f"- {s['id']}: {s['name']}. {s.get('look', '')} Needs: {s.get('needs', '')}." for s in styles if s["id"] in wf1_film.STYLES)
    return (f"# The source\n{identity_text(info)}\n\n# The styles a short can take\n{menu}\n\n"
            + (f"# This is part {part[0]} of {part[1]} of the transcript\n\n" if part else "")
            + "# Transcript, one line per ~12 s: [h:mm:ss|seconds] words\n" + "\n".join(lines) + "\n\n"
            "Walk the whole transcript in order and return every moment that clears the bar: a stranger stops within two "
            "seconds, stays to the end, and leaves with something. Grade each moment on its own. There is no target number, "
            "no minimum and no maximum: a weak hour gives few, a strong one many. A moment must stand alone without the "
            "episode, open on its hook in its first sentence, end on its payoff, run 18 to 55 seconds, and name at least "
            "two real things a camera has filmed (people, companies, products, places, events), because the film shows "
            "real footage of what is said. A moment of pure opinion with nothing to show is left out. Moments never overlap.\n\n"
            "Return JSON only: {\"podcast\": {\"speaker\": the main guest's full name, \"host\": the host's name or \"\", \"show\": the "
            "show's name or \"\", \"year\": the year it was recorded, \"short_title\": the episode in at most eight words}, "
            "\"moments\": [each {\"title\": a plain title in at most six words, \"t0\": seconds where the thought starts, \"t1\": "
            "seconds where the payoff ends, \"hook\": the first line a viewer hears, from the words, \"payoff\": the line it lands "
            "on, \"why\": why a stranger watches to the end, \"speakers\": how many people speak in it, \"names\": the people "
            "heard or named, \"shows\": the real filmed things a picture can show for it, most important first, \"styles\": "
            "[{\"id\": a style id from the list, \"why\": what in this moment that style needs}] best fit first, at least two, "
            "\"hook_score\": 1-10, \"payoff_score\": 1-10, \"story_score\": 1-10, \"strength\": 1-10 overall}], "
            "\"left_out\": [the strongest moments you left out, at most eight, each {\"t0\": seconds, \"title\": title, \"why\": the reason}]}")


def assign_styles(moments, order):
    """A style per short, in rank order: never the style of either short before it, the least-used style first, and
    among equals the moment's own best fit. Returns the moments with `style` set."""
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
    chunk = 45000
    parts = [words[i:i + chunk + 800] for i in range(0, len(words), chunk)] or [[]]
    rows, left, pod, spend = [], [], {}, []
    for k, ws in enumerate(parts):
        content = select_prompt(info, styles, lines_of(ws), part=(k + 1, len(parts)) if len(parts) > 1 else None)
        got = {}
        for _ in range(2):
            text, use = llm.ask(SELECT_SYSTEM, content, effort="high", max_tokens=48000)
            spend.append(dict(stage="moments", what="find and grade every moment", **use))
            try:
                got = llm.json_block(text)
            except ValueError:
                got = {}
            if isinstance(got, dict) and isinstance(got.get("moments"), list):
                break
        else:
            raise RuntimeError("the selection gave no moments")
        rows += [r for r in got["moments"] if isinstance(r, dict)]
        left += [r for r in got.get("left_out") or [] if isinstance(r, dict)]
        pod = pod or (got.get("podcast") if isinstance(got.get("podcast"), dict) else {})
    under = [dict(t0=r.get("t0"), title=r.get("title"), why=f"graded {r.get('strength')} of 10, under the bar of {BAR:g}") for r in rows
             if float(r.get("strength") or 0) < BAR]
    kept = assign_styles(rank(rows), list(wf1_film.ORDER))
    return dict(podcast=pod, found=len(rows), moments=kept, left_out=(under + left)[:40], bar=BAR, spend=spend)


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


def build_short(run, m, fetched, pod, tries=3):
    """One moment through wf1_short.py. Returns its record: {id, ok, folder | error}."""
    state = run.p("shorts", m["id"])
    os.makedirs(state, exist_ok=True)
    done = jload(os.path.join(state, "done.json"))
    if done and done.get("ok"):                          # a short that was not made is tried again when the link is run again
        return done
    info = fetched["info"]
    args = dict(moment=m, words=run.p("words.json"), style=m["style"], music_ledger=run.p("music_used.json"),      # no two shorts of a run play one track
                identity=(f"Speaker: {pod.get('speaker') or ''}. Host: {pod.get('host') or ''}. Show: {pod.get('show') or ''} "
                          f"({pod.get('year') or ''}).\n" + identity_text(info))[:1800],
                source_url=info.get("webpage_url") or run.link, video_id=run.state["video_id"], source_title=info.get("title"),
                channel=info.get("channel") or info.get("uploader"), upload_date=info.get("upload_date"))
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
        new = f"{st.get('stage') or 'start'}: {st.get('error') or (r.stdout or '')[-300:]}"
        run.log(f"{m['id']} stopped ({new[:200]})")
        if new == err:                                           # the same fault twice is not the network: this short cannot be made as planned
            break
        err = new
    rec = dict(id=m["id"], ok=False, error=err[:600])
    jsave(os.path.join(state, "done.json"), rec)
    return rec


def shorts(run, fetched, sel, lanes):
    ms = sel["moments"]
    with cf.ThreadPoolExecutor(max_workers=max(1, lanes)) as pool:
        recs = list(pool.map(lambda m: build_short(run, m, fetched, sel.get("podcast") or {}), ms))
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
            if p.get("kind") == "clip":
                c = by_clip.get((p.get("source"), tr)) or by_page.get(p.get("source")) or {}
                seen.add((p.get("source"), tr))
                span = f"{tr[0]:.1f}-{tr[1]:.1f} s of the source" if len(tr) == 2 else ""
                out.append(f"- Footage ({p.get('name')}): {c.get('title')} | {c.get('credit')} | {p.get('source')} | {span} | {at} | not licensed")
            else:
                c = by_page.get(p.get("source")) or {}
                out.append(f"- Photograph ({p.get('name')}): {c.get('title') or c.get('name')} | {c.get('credit')} | {c.get('licence')} | {p.get('source')} | {at}")
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


def deliver(run, fetched, sel, built, out_root):
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
                            sources=pc.get("sources"), spend_usd=f.get("spend_usd")))
    spend = round(sum(float(x.get("spend_usd") or 0) for x in listing) + sum(float(s.get("usd") or 0) for s in sel.get("spend") or []), 2)
    failed = [dict(id=r["id"], title=next((m.get("title") for m in sel["moments"] if m["id"] == r["id"]), None), error=r.get("error")) for r in built["failed"]]
    manifest = dict(contract=1, workflow="valmera-podcast-shorts", delivered=now(), published=False,
                    source=dict(link=link, title=info.get("title"), channel=info.get("channel") or info.get("uploader"), upload_date=info.get("upload_date"),
                                duration_s=fetched.get("duration"), acquired=fetched.get("how"), transcript=run.state.get("transcript")),
                    podcast=pod, moments_found=sel.get("found"), bar=sel.get("bar"), shorts=listing, not_made=failed, left_out=sel.get("left_out"),
                    spend_usd=spend, seconds=run.state.get("seconds"))
    jsave(os.path.join(part, "shorts.json"), manifest)
    pod_line = f"- {info.get('title')} | {info.get('channel') or info.get('uploader')} | {link} | published {info.get('upload_date')}"
    with open(os.path.join(part, "SOURCES.md"), "w") as fh:
        fh.write(_sources_md(pod_line, rows) + "\n")
    with open(os.path.join(part, "README.md"), "w") as fh:
        fh.write(f"# {os.path.basename(final)}\n\n{len(rows)} shorts from {link}, strongest first. Delivered once, complete: nothing is added to this folder later.\n\n"
                 "Not published. Each short keeps Valmera's watermark and end card. `clean/` holds the same shorts without music.\n"
                 "Rights: the real footage is not licensed, and the music needs its credit line wherever a short is posted. See SOURCES.md.\n\n"
                 "| # | Short | Style | Seconds | Pictures | Music |\n|---|---|---|---:|---:|---|\n"
                 + "\n".join(f"| {x['n']:02d} | {x['title']} | {x['style']} | {x['seconds'] or 0:.1f} | {x['pictures'] or 0} | {(x['music'] or {}).get('title') or 'none'} |" for x in listing)
                 + "\n\n" + (("Not made:\n" + "\n".join(f"- {x['title']}: {x['error']}" for x in failed) + "\n\n") if failed else "")
                 + f"Moments found: {sel.get('found')}; at or over the bar ({sel.get('bar'):g} of 10): {len(sel['moments'])}. shorts.json has every number.\n")
    os.rename(part, final)
    return dict(folder=final, shorts=len(rows), not_made=failed, spend_usd=spend)


def main():
    ap = argparse.ArgumentParser(description="A podcast or interview link in, one folder of finished shorts out.")
    ap.add_argument("link")
    ap.add_argument("--out", default=OUT_ROOT, help=argparse.SUPPRESS)          # where the one folder goes; the owner's Podcast Shorts by default
    ap.add_argument("--lanes", type=int, default=LANES, help=argparse.SUPPRESS)
    ap.add_argument("--keep-work", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    work, resumed = find_run(a.link)
    run = Run(a.link, work)
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
                r = deliver(run, results["fetch"], results["moments"], results["shorts"], os.path.abspath(a.out))
            results[stage] = r
            jsave(run.p(f"{stage}.json"), r)
            if stage not in run.state["done"]:
                run.state["done"].append(stage)
            run.state["seconds"][stage] = round(run.state["seconds"].get(stage, 0) + time.time() - t0, 1)
            run.save()
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
