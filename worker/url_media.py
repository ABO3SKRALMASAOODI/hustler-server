"""Turn a URL the user pasted into a file on disk we can trust.

The user says "add this song: <link>" or "cut in this clip: <link>". Two very
different kinds of URL arrive:

* A DIRECT file — Dropbox/Drive/S3/a CDN/a stock library. The bytes are at the
  end of the URL. net_fetch downloads it under the address, size and
  wall-clock limits documented there.
* A PAGE — YouTube, TikTok, Vimeo, SoundCloud. The media is behind a player,
  assembled from separate audio and video streams. Only an extractor
  (yt-dlp) can resolve that.

We do not ask the user which one they pasted; we work it out. The order is
"cheap check first": a HEAD-ish ranged GET tells us the content type without
pulling a body, and only a non-media answer escalates to the extractor. A URL
that looks direct but turns out not to be falls back too, because content-type
headers are frequently wrong and being wrong here would mean telling the user
their perfectly good link is broken.

CLASSIFICATION IS BY FFPROBE, NEVER BY EXTENSION OR CONTENT-TYPE. A server
that calls an mp4 `application/octet-stream` is ordinary; a URL ending `.mp3`
that serves an HTML error page is also ordinary. The only thing that knows
what a file actually is, is the decoder that has to open it. Everything else
is a hint used to route, never to decide.

SECURITY. The direct path is hardened in net_fetch (resolve-then-verify-peer,
per-hop redirect checks, byte and time caps) — read that module's docstring.
The extractor path is a genuinely weaker boundary and it is worth being
explicit about why: yt-dlp does its own networking, so our address checks
cover only the URL the user handed us, not the CDN URLs an extractor derives
from the page it fetched. A malicious page could in principle steer it at an
internal address. What we do about it: validate the user's URL up front,
refuse non-HTTP schemes (which also kills `file://` reads), pass
--ignore-config so no on-disk yt-dlp config can inject --exec or a proxy, and
bound the whole thing with a subprocess timeout and a byte cap. That is
mitigation, not elimination. The real fix is egress policy on the worker, which
does not exist today.
"""

import glob
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from urllib.parse import urlparse, unquote

import config
import media
import net_fetch


class FetchMediaError(Exception):
    pass


# Extensions that mean "the URL IS the file". Used only to skip the probe
# request on the common case — never to decide what the file is.
DIRECT_EXT = {
    ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mpg", ".mpeg", ".ts",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".aiff",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic", ".tiff",
}

# Container/codec names ffprobe reports for still images. An image arrives as
# a "video stream" with one frame, so without this list every PNG would be
# filed as a video clip.
_IMAGE_CODECS = {"png", "mjpeg", "jpeg", "webp", "bmp", "tiff", "apng",
                 "heic", "gif"}
# ffprobe names the GIF container "gif", NOT "gif_pipe" — the _pipe suffix
# only appears for the raw single-image demuxers. Getting that wrong filed
# every still GIF as a 0.04-second video clip.
_IMAGE_FORMATS = {"png_pipe", "image2", "jpeg_pipe", "webp_pipe", "bmp_pipe",
                  "tiff_pipe", "gif", "webp"}

# DB asset kinds this module can produce. Deliberately the EXISTING kinds —
# a fetched clip is a clip. Giving fetched media its own kind would mean a
# migration, a new CHECK constraint, and a branch in every surface that
# already knows how to show a video_clip, in exchange for nothing: where a
# file came from is provenance, and provenance belongs in meta.
KIND_VIDEO = "video_clip"
KIND_AUDIO = "music"
KIND_IMAGE = "image_ref"

# Per-kind byte ceilings, matching the upload limits in backend/storage.py so
# a link and a drag-and-drop of the same file behave the same way. The
# download itself is capped at the largest of these (we cannot know the kind
# before the bytes arrive), then the real ceiling is applied after ffprobe.
KIND_MAX_BYTES = {
    KIND_VIDEO: config.FETCH_CLIP_MAX_BYTES,
    KIND_AUDIO: config.FETCH_AUDIO_MAX_BYTES,
    KIND_IMAGE: config.FETCH_IMAGE_MAX_BYTES,
}

KIND_LABEL = {KIND_VIDEO: "video clip", KIND_AUDIO: "audio",
              KIND_IMAGE: "image"}

_EXT_FOR_KIND = {KIND_VIDEO: ".mp4", KIND_AUDIO: ".mp3", KIND_IMAGE: ".png"}


def _url_ext(url):
    try:
        path = unquote(urlparse(url).path or "")
    except Exception:
        return ""
    return os.path.splitext(path)[1].lower()


def looks_direct(url):
    """True when the URL path ends in a media extension we recognise."""
    return _url_ext(url) in DIRECT_EXT


def _ffprobe(path):
    out = media.run(["ffprobe", "-v", "error", "-print_format", "json",
                     "-show_format", "-show_streams", path], timeout=120)
    return json.loads(out)


def classify(path):
    """(kind, info) for a downloaded file, decided by what a decoder sees.

    The awkward case this exists to get right is an MP3 with embedded cover
    art. ffprobe reports it as having a video stream, so a naive
    "has a video stream => it's a video" test files every such track as a
    silent one-frame clip. The rule is therefore about the PICTURE: a video
    stream that is a still image, or that carries no more than a single frame,
    is artwork, not footage."""
    try:
        data = _ffprobe(path)
    except Exception as e:
        raise FetchMediaError(
            f"that file is not audio, video or an image we can read ({e})")

    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None and a is None:
        raise FetchMediaError("that file has no audio or video in it")

    fmt_names = set((fmt.get("format_name") or "").split(","))

    def _still(stream):
        """A single picture rather than footage.

        Two questions, both needed. Is the codec/container an image one at all
        (an h264 stream never is), and does it hold more than one frame? An
        animated GIF and a still GIF share a codec AND a container, so only
        the frame count separates them — and when ffprobe declines to report
        nb_frames, a real duration is the fallback signal, because a still has
        none worth speaking of."""
        if stream is None:
            return False
        # The container saying "this is cover art" is the most reliable signal
        # there is, and it is the one that settles an MP3 with embedded
        # artwork — where every other heuristic gets it wrong.
        if (stream.get("disposition") or {}).get("attached_pic"):
            return True
        codec = (stream.get("codec_name") or "").lower()
        if codec not in _IMAGE_CODECS and not (fmt_names & _IMAGE_FORMATS):
            return False
        try:
            frames = int(stream.get("nb_frames") or 0)
        except (TypeError, ValueError):
            frames = 0
        if frames > 1:
            return False
        if frames == 0:
            # The STREAM's runtime, never the container's. An MP3 with cover
            # art reports the audio's length as the container duration, so
            # asking the container files the artwork as a 3-minute video.
            try:
                if float(stream.get("duration") or 0) > 0.2:
                    return False          # no frame count, but it has runtime
            except (TypeError, ValueError):
                pass
        return True

    # An image never has an audio track, so `a is None` is part of the test —
    # it is also what keeps an MP3's embedded cover art from being mistaken
    # for a picture (that case falls through to the audio branch below).
    if v is not None and a is None and _still(v):
        return KIND_IMAGE, {
            "width": int(v.get("width") or 0),
            "height": int(v.get("height") or 0),
        }

    if a is not None and (v is None or _still(v)):
        try:
            duration = float(fmt.get("duration") or a.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        if duration <= 0:
            raise FetchMediaError("that audio file has no readable duration")
        return KIND_AUDIO, {"duration_s": round(duration, 3)}

    # Everything left is real footage — hand it to the same probe the indexer
    # uses so a fetched clip and an uploaded one describe themselves
    # identically (display dimensions, rotation applied, VFR detected).
    try:
        info = media.probe(path)
    except media.MediaError as e:
        raise FetchMediaError(f"could not read that video ({e})")
    return KIND_VIDEO, {
        "duration_s": info["duration"],
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "has_audio": info["has_audio"],
    }


_ADDRESSY = re.compile(
    r"https?://\S+"                       # URLs the extractor derived
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?"  # bare IPv4, optionally with a port
    r"|\[[0-9A-Fa-f:]+\](?::\d+)?"        # bracketed IPv6
    r"|:\d{2,5}\b")                       # a stray port


def _safe_detail(text):
    """An extractor's own words, with anything address-shaped removed.

    The reason for the scrub: yt-dlp's stderr is returned to the model and
    then to the user, and it names whatever host and port it tried. Since the
    URL is attacker-chosen, an unredacted error message turns this tool into a
    port scanner with a readable oracle — 'Connection refused' and 'timed out'
    on an internal address are different answers, and both are useful to
    someone mapping our network. The prose is what makes an error actionable
    ('Private video', 'Sign in to confirm your age'), so it survives; the
    addresses do not."""
    cleaned = _ADDRESSY.sub("…", text or "")
    cleaned = cleaned.replace("ERROR: ", "").strip()
    return cleaned or "no media found at that link"


def _ytdlp_available():
    if not config.URL_FETCH_EXTRACTOR:
        return False
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def _bot_walled(detail):
    """YouTube's datacenter-IP bot check — the one extractor failure that a
    different player client often gets past, so it is worth exactly one
    retry. Matched on the phrases yt-dlp surfaces for it."""
    d = (detail or "").lower()
    return ("sign in to confirm" in d or "not a bot" in d
            or "--cookies" in d)


def _extract(url, workdir, prefer=None, client_override=None):
    """Pull media out of a page with yt-dlp. Returns (path, info dict).

    Runs as a subprocess rather than through the python API so a hung or
    runaway extractor is killed by a timeout we control — the agent loop only
    checks its wall clock between tool calls, so an extractor that never
    returns would otherwise hang the whole turn.

    client_override: an --extractor-args player_client value for the retry
    path. YouTube bot-checks datacenter IPs on the default web client far
    more aggressively than on the tv/mweb clients, so a bot-walled first
    attempt earns ONE retry with alternate clients (see fetch below). This
    is best-effort — when YouTube blocks both, the honest error stands."""
    max_bytes = config.FETCH_MAX_BYTES
    # Cap the resolution rather than taking "best": a 4K source is a ~10x
    # bigger download and a slower render for a clip that gets composited into
    # a 1080p timeline anyway.
    fmt = (f"bv*[height<={config.FETCH_MAX_HEIGHT}]+ba/"
           f"b[height<={config.FETCH_MAX_HEIGHT}]/bv*+ba/b")
    cmd = [sys.executable, "-m", "yt_dlp",
           # No on-disk config may influence this run. A user-level yt-dlp
           # config could otherwise inject --exec or a proxy into a process
           # we are running against an untrusted URL.
           "--ignore-config",
           "--no-playlist",            # a channel link must not pull 500 videos
           "--no-progress", "--quiet", "--no-warnings",
           "--no-part", "--no-continue",
           "--no-mtime",
           "--socket-timeout", "20",
           "--retries", "2",
           # --max-filesize only checks a DECLARED size, and nothing is
           # declared for the fragmented (HLS/DASH) streams the big platforms
           # actually serve. It is a useful early-out, not a bound.
           "--max-filesize", str(max_bytes),
           # Refuse LIVE streams. This is the one genuinely unbounded case: a
           # 24/7 stream has no end, so it would download until the timeout
           # killed it, having burned the whole budget and the disk.
           #
           # Deliberately NOT also filtering on duration. yt-dlp rejects an
           # entry whose filter field is missing, and the Generic extractor —
           # which is what handles ordinary sites — reports no duration until
           # after the download. Adding `duration < N` here rejected every
           # such link, verified against a real page. The duration cap is
           # therefore applied in fetch() once ffprobe can actually see it,
           # with the subprocess timeout and the finished-file size check
           # bounding the download itself.
           "--match-filter", "!is_live",
           "--write-info-json",
           "-o", os.path.join(workdir, "dl.%(ext)s")]
    if client_override:
        cmd += ["--extractor-args", f"youtube:player_client={client_override}"]
    if prefer == KIND_AUDIO:
        # The user asked for a song. Pulling the video track and throwing it
        # away wastes the bulk of the download.
        cmd += ["-f", "ba/b", "--extract-audio", "--audio-format", "mp3"]
    else:
        cmd += ["-f", fmt, "--merge-output-format", "mp4"]
    cmd += ["--", url]

    try:
        # start_new_session puts yt-dlp in its OWN process group, so the
        # timeout below can kill the ffmpeg it spawns for the merge/transcode
        # too. Killing only yt-dlp orphans that ffmpeg, which then keeps
        # burning the single vCPU and holding disk long after the turn ended —
        # invisible, because nothing is waiting on it any more.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                start_new_session=True)
    except FileNotFoundError:
        raise FetchMediaError("the link extractor is not installed")
    try:
        out, err = proc.communicate(timeout=config.FETCH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()
        try:
            proc.communicate(timeout=10)
        except Exception:
            pass
        raise FetchMediaError(
            f"that link took longer than {config.FETCH_TIMEOUT_S:.0f}s to "
            "download")

    info = {}
    for meta_path in glob.glob(os.path.join(workdir, "*.info.json")):
        try:
            with open(meta_path) as f:
                info = json.load(f)
        except Exception:
            info = {}
        os.remove(meta_path)
        break

    files = [p for p in glob.glob(os.path.join(workdir, "dl.*"))
             if not p.endswith(".info.json")]
    if not files:
        # yt-dlp's stderr is the only place the real reason lives (private
        # video, region block, sign-in wall, extractor broken by a site
        # change). Surfacing its last line beats "download failed" — the user
        # can act on "Private video" and cannot act on a generic failure.
        detail = (err or out or "").strip().splitlines()
        tail = detail[-1][:200] if detail else "no media found at that link"
        raise FetchMediaError(_safe_detail(tail))
    # Largest file: when a merge is skipped we can be left with the separate
    # audio and video parts, and the video one is what was asked for.
    path = max(files, key=lambda p: os.path.getsize(p))
    if os.path.getsize(path) > max_bytes:
        raise FetchMediaError(
            f"that media is over the {max_bytes >> 20} MB limit")
    return path, info


def _extract_with_fallback(url, workdir, prefer=None):
    """_extract, with one alternate-client retry on YouTube's bot wall.

    Real user impact before this: a pasted youtu.be link failed with 'Sign
    in to confirm you're not a bot' on the very first customer who tried it —
    Render's egress IP is a datacenter address, which the default web client
    challenges. The tv/mweb clients are challenged far less; when they fail
    too, the original honest error is what the user sees."""
    try:
        return _extract(url, workdir, prefer=prefer)
    except FetchMediaError as e:
        if not _bot_walled(str(e)):
            raise
        # Drop any partial files so the largest-file pick after the retry
        # can never hand back a fragment of the failed attempt.
        for p in glob.glob(os.path.join(workdir, "dl.*")):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            return _extract(url, workdir, prefer=prefer,
                            client_override="tv,mweb")
        except FetchMediaError:
            raise e


def _download_direct(url, workdir):
    """Straight GET of a URL that points at the file itself."""
    ext = _url_ext(url) or ".bin"
    out = os.path.join(workdir, f"dl{ext}")
    _, final_url = net_fetch.download(
        url, out, max_bytes=config.FETCH_MAX_BYTES,
        timeout_s=config.FETCH_TIMEOUT_S)
    return out, final_url


def _title_from(url, info, kind, path=None):
    """A human filename for the media picker.

    Falls back through the extractor's title, then the URL's last path
    segment, then a generic name — every asset surface in the studio renders
    meta.filename, so an empty one shows as "attachment"."""
    title = (info or {}).get("title")
    if not title:
        seg = os.path.basename(unquote(urlparse(url).path or "")).strip()
        title = os.path.splitext(seg)[0] if seg else ""
    title = (title or f"fetched {KIND_LABEL.get(kind, 'media')}").strip()
    # Keep it filename-shaped without being precious about it; this is a
    # display label, not a path we ever open.
    title = "".join(c for c in title if c.isprintable()).strip()[:120]
    # An extractor title often already ends in the extension ("My Song.mp3"),
    # and we are about to append the extension of what we actually saved —
    # which is not always the same one, so "My Song.mp3.mp3" and the worse
    # "My Song.webm.mp4" both show up in the picker without this.
    stem, ext = os.path.splitext(title)
    if ext.lower() in DIRECT_EXT and stem.strip():
        title = stem.strip()
    # The extension of the file we ACTUALLY saved, not the kind's canonical
    # one — a fetched .wav labelled ".mp3" and a .mov labelled ".mp4" are both
    # small lies the studio then shows the user, and the download button hands
    # them a file whose name disagrees with its contents.
    real = os.path.splitext(path or "")[1].lower()
    suffix = real if real in DIRECT_EXT else _EXT_FOR_KIND.get(kind, "")
    return (title or "fetched media") + suffix


def fetch(url, workdir, prefer=None):
    """Download `url` into `workdir` and say what it is.

    Returns a dict: path, kind, filename, source_url, extractor, plus the
    probed fields (duration_s / width / height / fps / has_audio) for whatever
    kind it turned out to be. Raises FetchMediaError with a sentence fit to
    show a user."""
    # Validate before anything reaches the network, so a private-address URL
    # is refused without a request being made — and so `file://` and friends
    # die here rather than inside an extractor.
    try:
        net_fetch.check_url(url)
    except net_fetch.FetchError as e:
        raise FetchMediaError(str(e))

    info, extractor, final_url = {}, None, url
    path = None

    direct = looks_direct(url)
    if not direct:
        # One cheap ranged request. A media content-type means we can skip the
        # extractor entirely, which is both faster and a much smaller attack
        # surface.
        try:
            ctype, _ = net_fetch.head_kind(url)
        except net_fetch.FetchError as e:
            raise FetchMediaError(str(e))
        except Exception:
            ctype = None
        direct = bool(ctype and ctype.split("/")[0] in ("video", "audio",
                                                        "image"))

    direct_error = None
    if direct:
        try:
            path, final_url = _download_direct(url, workdir)
        except net_fetch.FetchError as e:
            # A URL can LOOK direct and not be one. Wikipedia and plenty of
            # CMSes serve an HTML page at a path ending ".webm"/".mp4", and a
            # bare GET of a platform's media URL is routinely 403/404 without
            # the player's session — so a failed direct download is a reason
            # to try the extractor, not a reason to stop. The original error
            # is kept: when BOTH routes fail it is the more actionable of the
            # two, because "HTTP 404" tells the user their link is dead while
            # a generic extractor message does not.
            direct_error = e

    if path is None or not os.path.exists(path):
        if not _ytdlp_available():
            raise FetchMediaError(
                str(direct_error) if direct_error else
                "that link is a web page rather than a media file, and this "
                "deployment cannot extract media from pages")
        try:
            path, info = _extract_with_fallback(url, workdir, prefer=prefer)
        except FetchMediaError:
            if direct_error is not None:
                raise FetchMediaError(str(direct_error))
            raise
        extractor = (info or {}).get("extractor_key") or "yt-dlp"

    try:
        kind, probed = classify(path)
    except FetchMediaError:
        # A "direct" URL that served HTML (an error page, a login wall, a
        # consent interstitial) lands here. The extractor can often still
        # resolve it, so this is a routing correction rather than a failure.
        if extractor is None and _ytdlp_available():
            try:
                os.remove(path)
            except OSError:
                pass
            path, info = _extract_with_fallback(url, workdir, prefer=prefer)
            extractor = (info or {}).get("extractor_key") or "yt-dlp"
            kind, probed = classify(path)
        else:
            raise

    nbytes = os.path.getsize(path)
    limit = KIND_MAX_BYTES.get(kind, config.FETCH_MAX_BYTES)
    if nbytes > limit:
        raise FetchMediaError(
            f"that {KIND_LABEL.get(kind, 'file')} is {nbytes >> 20} MB, over "
            f"the {limit >> 20} MB limit for {KIND_LABEL.get(kind, 'media')}")

    duration = probed.get("duration_s")
    if duration and duration > config.FETCH_MAX_DURATION_S:
        raise FetchMediaError(
            f"that media is {duration / 60:.0f} minutes long, over the "
            f"{config.FETCH_MAX_DURATION_S / 60:.0f}-minute limit")

    out = {"path": path, "kind": kind, "bytes": nbytes,
           "filename": _title_from(url, info, kind, path),
           "source_url": final_url or url, "extractor": extractor,
           "title": (info or {}).get("title"),
           "uploader": (info or {}).get("uploader")}
    out.update(probed)
    return out


def storage_key(project_id, kind, path):
    """Object key for fetched media.

    Its own `fetched/` prefix rather than reusing `clips/` or `music/`: those
    prefixes are what backend/storage.py's key-ownership check keys on for
    UPLOADS, and letting worker-written objects share them would blur the line
    between "the user sent us this" and "we went and got this". The prefix is
    registered in storage.DELETE_PREFIXES so project deletion reclaims it."""
    ext = os.path.splitext(path)[1].lower() or _EXT_FOR_KIND.get(kind, ".bin")
    return f"fetched/{project_id}/{uuid.uuid4().hex[:12]}{ext}"


CONTENT_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".m4v": "video/x-m4v",
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
}


def content_type(path):
    return CONTENT_TYPES.get(os.path.splitext(path)[1].lower(),
                             "application/octet-stream")
