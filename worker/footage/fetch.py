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
import shlex
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


def pace():
    """Wait for this request's turn in a run-wide pace of YouTube fetches, when the caller asked for one.

    Opt-in: FOOTAGE_PACE_DIR names a folder every process of one run shares (a workflow run sets it; a customer's
    worker never does, so its fetches are unchanged) and FOOTAGE_PACE_S the seconds between two requests of that run
    (FOOTAGE_PACE_BURST of them may go at once after a quiet spell). The schedule is a file under flock, so the two
    shorts a run makes at once, their footage engines and the refused-source retries all draw from the same bucket
    instead of firing a hundred requests in a few minutes, the burst that trips YouTube's wall. Returns the seconds
    waited."""
    folder = os.environ.get("FOOTAGE_PACE_DIR")
    try:
        gap = float(os.environ.get("FOOTAGE_PACE_S") or 0)
        burst = max(1, int(os.environ.get("FOOTAGE_PACE_BURST") or 1))
    except ValueError:
        return 0.0
    if not folder or gap <= 0:
        return 0.0
    import fcntl
    try:
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "youtube.pace"), "a+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            try:
                due = float(f.read().strip() or 0)          # the bucket's theoretical arrival time (GCRA)
            except ValueError:
                due = 0.0
            now = time.time()
            due = max(due, now)
            start = max(now, due - (burst - 1) * gap)
            f.seek(0)
            f.truncate()
            f.write(f"{due + gap:.3f}")
            f.flush()
    except OSError:
        return 0.0
    wait = start - time.time()
    if wait > 0:
        time.sleep(wait)
    return max(0.0, wait)


WAYBACK = "https://web.archive.org/web/2/"      # the Wayback Machine's newest capture of a page; yt-dlp reads its archived YouTube videos


def run_ladder(url, args, timeout_s=CALL_S, done=None, lighter=None):
    """Run yt-dlp with args on url down the ladder. Returns (stdout, rung label). done(stdout) says a
    rung delivered (default: it exited 0). Raises FetchError with yt-dlp's own last words.
    A YouTube video no rung could open (walled as a bot, removed, private) is asked of the Wayback Machine last: its
    archived copy is small (360p as a rule) and free of the wall, and for old footage it is often all there is.
    lighter: the args of a smaller request. A rung on which args time out or fail for a reason other than the wall is
    asked again at once for lighter, and the rest of the ladder (and the Wayback Machine) asks for lighter: a big
    request that cannot arrive in time on a slow or proxied route never costs the source (round 1, 20 Nov: the 1080p
    whole-first request against a 240 s call)."""
    last = "no rung ran"

    def attempt(extra, use, label):
        if is_youtube(url):
            pace()                                        # one run-wide pace for YouTube rungs (opt-in, FOOTAGE_PACE_*)
        try:
            r = subprocess.run(ytdlp_cmd() + extra + use + ["--", url], capture_output=True, text=True, timeout=timeout_s, env=env())
        except subprocess.TimeoutExpired:
            return None, "timeout", f"{label}: timed out"
        if r.returncode == 0 and (done(r.stdout) if done else True):
            return r.stdout, "ok", label
        why = f"{label}: " + " | ".join(ln for ln in (r.stderr or "").strip().splitlines()[-2:])[:300]
        return None, ("walled" if _walled(r.stderr or "") else "failed"), why

    for extra, label in _rungs(url):
        out, status, last = attempt(extra, args, label)
        if status == "ok":
            return out, label
        if lighter is not None and args is not lighter and status in ("timeout", "failed"):
            args = lighter
            out, status, why = attempt(extra, args, label + "+lighter")
            if status == "ok":
                return out, label + "+lighter"
            last += " || " + why
        if status == "failed" and is_youtube(url):
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


def streams(url, local=True):
    """The best video stream at or under FINAL_H: {url, headers, w, h, fps, vcodec}, resolved once per source
    so every cut is a seek on it rather than another walk down the ladder. local=False resolves the live stream even
    for a source kept on disk (a kept copy that arrived small is cut sharper from the live stream: run.find)."""
    cached = local_source(url) if local else None
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


WHOLE_AV_MAX = "400M"
WHOLE_AV_H = FINAL_H          # a whole-first source is every cut's source: it comes at the film's own size when it can
WHOLE_AV_PART = "330M"        # the most a 1080p video stream may weigh, so it and its sound stay under WHOLE_AV_MAX
META = "meta"                 # <out_dir>/meta.info.json: the source's own title, uploader and description (provenance)


def whole_av_format():
    """The whole-first ladder: separate video and sound streams at or under WHOLE_AV_H first (H.264, then any codec),
    each video stream small enough to arrive under WHOLE_AV_MAX, then the 720p ladder as before. YouTube's only
    combined mp4 with sound at or under 720p is its 360p stream (round 1, 19 Nov: footage-cut and archive-reel clips
    arrived under 640 px tall and fell into window cards), so the combined file is no longer asked for first."""
    return (f"bv*[height<={WHOLE_AV_H}][vcodec^=avc1][filesize<?{WHOLE_AV_PART}]+ba/"
            f"bv*[height<={WHOLE_AV_H}][filesize<?{WHOLE_AV_PART}]+ba/"
            f"bv*[height<={WHOLE_H}][vcodec^=avc1]+ba/bv*[height<={WHOLE_H}]+ba/"
            f"b[height<={WHOLE_H}][ext=mp4]/b[height<={WHOLE_H}]/b")


def whole_av_lighter_format():
    """The whole-first ladder before 1080p (the combined mp4 at or under WHOLE_H first): what a rung asks for when the
    1080p streams time out or fail on it (run_ladder lighter)."""
    return (f"b[height<={WHOLE_H}][ext=mp4]/bv*[height<={WHOLE_H}][vcodec^=avc1]+ba/bv*[height<={WHOLE_H}]+ba/"
            f"b[height<={WHOLE_H}]/b")


def info_of(out_dir):
    """{title, uploader, channel, description, upload_date} a fetch wrote beside its file (META), or {}."""
    try:
        info = json.load(open(os.path.join(out_dir, META + ".info.json")))
    except (OSError, ValueError):
        return {}
    out = {k: info.get(k) for k in ("title", "uploader", "channel", "upload_date") if isinstance(info.get(k), str) and info.get(k)}
    if isinstance(info.get("description"), str) and info["description"].strip():
        out["description"] = info["description"].strip()[:1200]
    return out


def whole_av(url, out_dir):
    """The source whole, at or under WHOLE_AV_H WITH its sound, in one yt-dlp call: (path, rung). The one file a
    whole-first open (run.find whole_first) scans, hears and cuts from, so the source is never resolved or fetched again.
    A source a caller kept (local_source) is that file. The source's own metadata lands beside it (info_of)."""
    cached = local_source(url)
    if cached:
        return cached, "verified-local-source"
    os.makedirs(out_dir, exist_ok=True)
    adapter = os.environ.get('FOOTAGE_FETCH_CMD')
    if adapter:
        # An operator-controlled fetch transport can use the deployment's
        # egress without copying proxy credentials onto an editing machine.
        # It supplies original bytes only; identity and motion checks still run.
        result = subprocess.run(shlex.split(adapter), input=json.dumps(dict(url=url, out_dir=os.path.abspath(out_dir))),
                                text=True, capture_output=True, timeout=900)
        try:
            receipt = json.loads(result.stdout)
            if receipt.get('url') == url and receipt.get('ok') is False:
                # A structured operator receipt preserves the actual access
                # failure, rather than disguising it as a missing path/JSON bug.
                raise FetchError('external original fetch failed: '+str(receipt.get('error') or 'source unavailable')[:400])
            path = os.path.realpath(receipt['path'])
            if result.returncode or receipt.get('url') != url or os.path.commonpath([path,os.path.realpath(out_dir)]) != os.path.realpath(out_dir):
                raise ValueError('invalid external fetch receipt')
            if os.path.getsize(path) < 2000:
                raise ValueError('empty external source')
            rung='external-original:' + str(receipt.get('rung',''))
            from .run import keep_sources
            keep_sources([dict(whole=path,url=url,rung=rung)],log=lambda _:None)
            return path,rung
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise FetchError('external source transport failed: ' + type(exc).__name__) from exc
    for p in glob.glob(os.path.join(out_dir, "whole.*")):
        os.remove(p)
    def args_for(fmt):
        return ["-f", fmt, "--merge-output-format", "mp4", "--max-filesize", WHOLE_AV_MAX,
                "--write-info-json", "--no-write-playlist-metafiles", "-o", "infojson:" + os.path.join(out_dir, META),
                "-o", os.path.join(out_dir, "whole.%(ext)s")]

    def got(_):
        return bool([p for p in glob.glob(os.path.join(out_dir, "whole.*")) if not p.endswith((".part", ".ytdl"))])
    # 1080p streams first; a rung on which they time out or fail (not walled) is asked at once for the 720p ladder,
    # so a slow route still brings the source (any .part of either request is continued by yt-dlp on a later call).
    _, rung = run_ladder(url, args_for(whole_av_format()), timeout_s=CALL_S, done=got, lighter=args_for(whole_av_lighter_format()))
    # The merged file (with its sound) over a lone video stream a timed-out 1080p request left behind.
    merged = os.path.join(out_dir, "whole.mp4")
    path = merged if os.path.isfile(merged) else max(
        (p for p in glob.glob(os.path.join(out_dir, "whole.*")) if not p.endswith((".part", ".ytdl"))), key=os.path.getsize)
    return path, rung


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
