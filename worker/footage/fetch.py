"""Fetching a source's footage through the product's YouTube ladder (worker/ytaccess.py, url_media).

Each yt-dlp call walks the same rungs a customer's pasted link does: PO tokens (ytaccess.pot_args),
the EJS challenge solver, the anonymous player clients (ytaccess.extraction_strategies), on the primary
egress (config.YTDLP_PROXY or direct) and then on each residential fallback route
(config.YTDLP_PROXY_FALLBACK). A rung that hits YouTube's wall moves to the next; any other failure is
the real answer and stops. archive.org takes yt-dlp's own extractor, direct.

FOOTAGE_YTDLP names another python whose yt_dlp is newer (the Mac's workflows: YouTube's player
changes faster than the image's pin); FOOTAGE_PATH puts a JS runtime (deno) on the PATH.
"""
import glob
import json
import os
import subprocess
import sys
import time

PROXY_H = 360                 # the scanning copy: frames for the look, audio for the transcript
FINAL_H = 1080                # the cut is taken from the best stream at or under this
PROXY_MAX_S = 900.0           # read at most the first 15 minutes of a source
CALL_S = 240.0


class FetchError(RuntimeError):
    pass


def local_source(url):
    """A caller-verified complete source, matched by exact source URL.

    The workflow verifies its downloaded media and provenance before passing
    this manifest. These are read-only originals, never an edited short or a
    similarly named video. Ordinary footage identity and placement checks still
    apply to every excerpt selected from them.
    """
    try:
        rows = json.loads(os.environ.get("FOOTAGE_LOCAL_SOURCES", "[]"))
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("url") == url:
                path = row.get("path")
                if isinstance(path, str) and os.path.isfile(path) and os.path.getsize(path) > 2000:
                    return os.path.realpath(path)
    except (OSError, ValueError, TypeError):
        pass
    return None


def ytdlp_cmd():
    py = os.environ.get("FOOTAGE_YTDLP") or sys.executable
    cmd = [py, "-m", "yt_dlp", "--ignore-config", "--no-update", "--no-playlist", "--no-warnings", "--quiet", "--no-progress",
           "--socket-timeout", "20", "--retries", "2"]
    try:
        import config
        if config.YTDLP_REMOTE_COMPONENTS:
            cmd += ["--remote-components", config.YTDLP_REMOTE_COMPONENTS]
    except Exception:  # noqa: BLE001 no config: yt-dlp's defaults
        cmd += ["--remote-components", "ejs:github"]
    return cmd


def env():
    e = dict(os.environ)
    extra = [p for p in (os.environ.get("FOOTAGE_PATH"), os.path.dirname(ffmpeg())) if p]
    e["PATH"] = os.pathsep.join(extra + [e.get("PATH", "/usr/bin:/bin")])
    return e


def ffmpeg():
    return os.environ.get("FOOTAGE_FFMPEG") or os.environ.get("FFMPEG") or "ffmpeg"


def ffprobe():
    f = ffmpeg()
    return os.path.join(os.path.dirname(f), "ffprobe") if os.path.dirname(f) else "ffprobe"


def is_youtube(url):
    try:
        import ytaccess
        return ytaccess.is_youtube_url(url)
    except Exception:  # noqa: BLE001
        return "youtube.com" in url or "youtu.be" in url


def _rungs(url):
    """[(extra args, label)] in order: the product's ladder for YouTube, one plain rung elsewhere."""
    if not is_youtube(url):
        return [([], "direct")]
    try:
        import config
        import ytaccess
        routes = [config.YTDLP_PROXY or None] + list(config.YTDLP_PROXY_FALLBACK)
        strategies = [s for s in ytaccess.extraction_strategies(have_cookies=False) if not s.get("cookies")]
        pot = ytaccess.pot_args()
    except Exception:  # noqa: BLE001 outside the worker: yt-dlp's own defaults
        routes, strategies, pot = [None], [], []
    out = []
    for route in routes:
        via = ["--proxy", route] if route else []
        out.append((via + pot, "default" + ("+proxy" if route else "")))
        for s in strategies:
            if s.get("client"):
                out.append((via + pot + ["--extractor-args", f"youtube:player_client={s['client']}"],
                            s["name"] + ("+proxy" if route else "")))
    return out


def _walled(text):
    try:
        import ytaccess
        return ytaccess.access_blocked(text)
    except Exception:  # noqa: BLE001
        t = (text or "").lower()
        return "403" in t or "not a bot" in t or "sign in" in t or "format is not available" in t


WAYBACK = "https://web.archive.org/web/2/"      # the Wayback Machine's newest capture of a page; yt-dlp reads its archived YouTube videos


def run_ladder(url, args, timeout_s=CALL_S, done=None):
    """Run yt-dlp with args on url down the ladder. Returns (stdout, rung label). done(stdout) says a
    rung delivered (default: it exited 0). Raises FetchError with yt-dlp's own last words.
    A YouTube video no rung could open (walled as a bot, removed, private) is asked of the Wayback Machine last: its
    archived copy is small (360p as a rule) and free of the wall, and for old footage it is often all there is."""
    last = "no rung ran"
    for extra, label in _rungs(url):
        try:
            r = subprocess.run(ytdlp_cmd() + extra + args + ["--", url], capture_output=True, text=True, timeout=timeout_s, env=env())
        except subprocess.TimeoutExpired:
            last = f"{label}: timed out"
            continue
        ok = r.returncode == 0 and (done(r.stdout) if done else True)
        if ok:
            return r.stdout, label
        last = f"{label}: " + " | ".join(ln for ln in (r.stderr or "").strip().splitlines()[-2:])[:300]
        if not _walled(r.stderr or "") and is_youtube(url):
            break
    if is_youtube(url) and os.environ.get("FOOTAGE_WAYBACK", "1") != "0":
        try:
            r = subprocess.run(ytdlp_cmd() + args + ["--", WAYBACK + url], capture_output=True, text=True, timeout=timeout_s, env=env())
            if r.returncode == 0 and (done(r.stdout) if done else True):
                return r.stdout, "wayback"
            last += " || wayback: " + " | ".join(ln for ln in (r.stderr or "").strip().splitlines()[-1:])[:160]
        except subprocess.TimeoutExpired:
            last += " || wayback: timed out"
    raise FetchError(last)


def proxy(url, out_dir, duration=None, max_s=None):
    """A small copy with sound for scanning: (path, rung). The first PROXY_MAX_S seconds of a long source, or the
    first max_s when the caller has less time than that (a first film's footage, score.film)."""
    cached = local_source(url)
    if cached:
        return cached, "verified-local-source"
    max_s = float(max_s) if max_s else PROXY_MAX_S
    os.makedirs(out_dir, exist_ok=True)
    for p in glob.glob(os.path.join(out_dir, "proxy.*")):
        os.remove(p)
    fmt = (f"b[height<={PROXY_H}][ext=mp4]/bv*[height<={PROXY_H}]+ba/b[height<={PROXY_H}]/"
           f"bv*[height<=480]+ba/b[height<=480]/b")
    args = ["-f", fmt, "--merge-output-format", "mp4", "--max-filesize", "400M", "-o", os.path.join(out_dir, "proxy.%(ext)s")]
    if duration and duration > max_s + 30:
        args += ["--download-sections", f"*0-{int(max_s)}"]

    def got(_):
        return bool([p for p in glob.glob(os.path.join(out_dir, "proxy.*")) if not p.endswith((".part", ".ytdl"))])
    _, rung = run_ladder(url, args, done=got)
    path = max((p for p in glob.glob(os.path.join(out_dir, "proxy.*")) if not p.endswith((".part", ".ytdl"))), key=os.path.getsize)
    return path, rung


def streams(url):
    """The best video stream at or under FINAL_H: {url, headers, w, h, fps, vcodec}, resolved once per source
    so every cut is a seek on it rather than another walk down the ladder."""
    cached = local_source(url)
    if cached:
        return dict(url=cached, headers={}, rung="verified-local-source", expires=time.time() + 4 * 3600)
    fmt = f"bv*[height<={FINAL_H}][vcodec^=avc1]/bv*[height<={FINAL_H}]/b[height<={FINAL_H}]/b"
    out, rung = run_ladder(url, ["-f", fmt, "-J"], timeout_s=120, done=lambda s: s.strip().startswith("{"))
    info = json.loads(out)
    fs = info.get("requested_formats") or [info]
    v = next((f for f in fs if f.get("vcodec") not in (None, "none")), fs[0])
    return dict(url=v.get("url"), headers=v.get("http_headers") or info.get("http_headers") or {}, w=v.get("width"), h=v.get("height"),
                fps=v.get("fps"), vcodec=v.get("vcodec"), rung=rung, expires=time.time() + 4 * 3600)


WHOLE_H = 720                 # a whole source fetched for its cuts: enough for a vertical film's cover crop, a tenth of 1080p's bytes
WHOLE_MAX = "160M"


def whole(url, out_dir):
    """The source's whole picture at or under WHOLE_H, no sound, by yt-dlp's own downloader: path. The rung for a
    short source whose sections will not come: a section is cut by ffmpeg on the resolved stream, which YouTube
    refuses from a proxy route more often than it allows (the box, 14 Nov: 5 of 15 cuts, then 4 of 4)."""
    cached = local_source(url)
    if cached:
        return cached
    os.makedirs(out_dir, exist_ok=True)
    for p in glob.glob(os.path.join(out_dir, "whole.*")):
        os.remove(p)
    fmt = f"bv*[height<={WHOLE_H}][vcodec^=avc1]/bv*[height<={WHOLE_H}]/b[height<={WHOLE_H}]/b"
    args = ["-f", fmt, "--max-filesize", WHOLE_MAX, "-o", os.path.join(out_dir, "whole.%(ext)s")]

    def got(_):
        return bool([p for p in glob.glob(os.path.join(out_dir, "whole.*")) if not p.endswith((".part", ".ytdl"))])
    run_ladder(url, args, done=got)
    return max((p for p in glob.glob(os.path.join(out_dir, "whole.*")) if not p.endswith((".part", ".ytdl"))), key=os.path.getsize)


def section(url, t0, t1, out_path):
    """The source's own section, cut by yt-dlp (the rung for a stream ffmpeg could not seek)."""
    d = os.path.dirname(out_path)
    base = os.path.splitext(os.path.basename(out_path))[0]
    fmt = f"bv*[height<={FINAL_H}][vcodec^=avc1]/bv*[height<={FINAL_H}]/b[height<={FINAL_H}]/b"
    args = ["-f", fmt, "--download-sections", f"*{t0:.2f}-{t1:.2f}", "--force-keyframes-at-cuts", "--merge-output-format", "mp4",
            "-o", os.path.join(d, base + ".%(ext)s")]
    run_ladder(url, args, done=lambda _: bool(glob.glob(os.path.join(d, base + ".*"))))
    got = [p for p in glob.glob(os.path.join(d, base + ".*")) if not p.endswith((".part", ".ytdl"))]
    if not got:
        raise FetchError("the section did not arrive")
    if got[0] != out_path:
        os.replace(got[0], out_path)
    return out_path
