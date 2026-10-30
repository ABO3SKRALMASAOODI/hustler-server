"""
S3-compatible object storage access (any provider by env; today Cloudflare
R2, moving to another S3 endpoint through storage_routing.py's modes).

Media bytes NEVER pass through this API server: browsers upload directly to
the bucket via presigned URLs minted here, and download via presigned GETs.
Key layout:
    originals/{project_id}/{uuid}.{ext}
    proxies/{project_id}/{sha}.mp4
    audio/{project_id}/{sha}.wav
    thumbs/{project_id}/{sha}/shot_{n}.jpg
    sheets/{project_id}/{sha}/sheet_{n}.jpg
    renders/{project_id}/{preview|final}_v{version}.mp4   (legacy, pre-2026-07-18)
    media/{project_id}/{job_id}-{rand}.mp4                (renders; opaque + unique
                                                           per render — see
                                                           worker/renderer.py)
"""

import hashlib
import os
import threading
import uuid

import boto3
from botocore.config import Config

import storage_creds as creds
import storage_routing as routing

PRESIGN_EXPIRY = 900          # short-lived links (admin GETs lean on this)
# UPLOAD presigns must OUTLIVE THE SLOWEST PLAUSIBLE UPLOAD, because a presign
# is validated when a request STARTS: at the old 15 minutes, any multipart
# whose later parts first went up after minute 15 — or any retry of a slow
# single PUT — earned a 403 the client correctly treats as non-retryable, and
# the whole transfer died. The measured 4 GB uploads were finishing in ~13
# minutes; they were one congestion event away from total loss. A real user on
# 2026-07-31 PUT 45 MB for 15m30s — had that one PUT blipped near the end, the
# retry would have found its URL expired. 12h covers a 14 GB file at 4 Mbps
# and the 100 GB cap at 20 Mbps; anything slower re-presigns its remaining
# parts through /uploads/resume (resume_multipart below), which is also how a
# reloaded tab or a woken laptop carries on. The URL is still minted per-key
# behind the owner's auth, and
# leaking one only lets someone overwrite the single key it names.
PRESIGN_UPLOAD_EXPIRY = int(os.getenv("PRESIGN_UPLOAD_EXPIRY", "43200"))
# Playback GETs live much longer: the studio fetches ONE url per asset and the
# user watches/chats across a long session — at 15 min the player's src went
# dead mid-session and every later play/seek was a silent black frame. The
# url is still minted per-request behind the user's own auth; 6h only bounds
# how long a leaked link would work, not who can mint one.
PRESIGN_GET_EXPIRY = int(os.getenv("PRESIGN_GET_EXPIRY", "21600"))

# ── Transfer geometry ────────────────────────────────────────────────────────
# A single PUT is ONE TCP stream, and one stream is exactly what a slow or
# lossy link cannot fill: the user this section is written for uploaded 45 MB
# in 15.5 minutes (48 KB/s effective) as a single PUT while other users pushed
# multipart files at 5-9 MB/s over six sockets. A single PUT also retries from
# byte zero — one blip at 90% resends everything. So the single-PUT range is
# only where the multipart handshake overhead would be a real fraction of the
# transfer: 16 MB, the same threshold S3's own transfer managers converged on.
SINGLE_PUT_LIMIT = 16 * 1024 * 1024

# Parts are sized so PARALLELISM ARRIVES WHERE THE FILES ARE, not only on
# multi-GB uploads. The old fixed 64 MB meant a 100 MB file was "multipart" in
# name and two sockets in practice — six sockets only lit up past ~384 MB.
# Keep parts in the 8-16 MiB range so modest uplinks commit durable progress
# before a browser request timeout. Even the 14 GiB maximum remains below
# S3's 10,000-part protocol limit.
MIN_PART_SIZE = 8 * 1024 * 1024
MAX_PART_SIZE = 16 * 1024 * 1024
TARGET_PARTS = 128
# One presign response carries one URL per part, so very large files grow
# their parts instead of their URL list: up to ~15.6 GiB nothing changes
# (16 MiB parts); a 60 GB OBS recording gets ~60 MiB parts and the same
# 1,000-URL response a 15 GB file always had.
MAX_TRANSFER_PARTS = 1000


def part_size_for(nbytes):
    """Multipart part size for a file of `nbytes`, in whole MiB."""
    raw = (int(nbytes) + TARGET_PARTS - 1) // TARGET_PARTS
    mib = 1024 * 1024
    rounded = ((raw + mib - 1) // mib) * mib
    size = max(MIN_PART_SIZE, min(MAX_PART_SIZE, rounded))
    floor = (int(nbytes) + MAX_TRANSFER_PARTS - 1) // MAX_TRANSFER_PARTS
    floor = ((floor + mib - 1) // mib) * mib
    return max(size, floor)

# Only these get presigned for upload. Everything else is rejected before a
# URL is ever minted.
ALLOWED_VIDEO_EXT = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
ALLOWED_MUSIC_EXT = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}
ALLOWED_IMAGE_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

# High-resolution campaign/product JPEGs routinely land in the 20-40 MiB
# range. This remains bounded well below video uploads.
#
# Audio is 500 MB: uncompressed WAV is how voice-overs and masters leave a
# DAW (44.1 kHz stereo 16-bit is 10 MB a minute), and the old 50 MB refused a
# paying customer's 81.6 MB voice-over and another user's 58.5 MB song
# (7 and 9 Oct 2026). 500 MB is about 47 minutes of CD-quality WAV; the
# indexer reads music down to a 16-kHz mono track either way.
MUSIC_MAX_BYTES = int(float(os.getenv("MUSIC_MAX_MB", "500")) * 1024 * 1024)
IMAGE_MAX_BYTES = 50 * 1024 * 1024

# A browser-built 540p proxy. Our own proxies average 0.70 Mbps across 202
# production files, so the 3-hour maximum lands near 950 MB even at the
# browser encoder's less efficient fixed bitrate. 2 GB is the abuse guard, not
# a working limit — a proxy anywhere near it is not a proxy.
PROXY_MAX_BYTES = 2 * 1024 * 1024 * 1024

# PROXY-FIRST UPLOADS SHIP OFF, AND THE REASON IS THE DEPLOY ORDER.
#
# Render redeploys this service on push. Cloud Run does NOT redeploy the
# executor — that is a manual `gcloud run deploy`, and it has bitten this
# product twice (41 of 41 exports hidden behind a stamp the executor predated;
# two users' previews rejected by a schema 19 hours older than the code that
# wrote them). An executor that has not been redeployed does not understand
# `client_proxy_key`: it would go looking for an original whose bytes are still
# in the browser, and FAIL EVERY LARGE UPLOAD.
#
# With this off, nothing changes for anyone. Turn it on only after the executor
# is redeployed — `/health` reports the fingerprint that proves which code it
# is running. The failure mode of "off" is "the old speed"; the failure mode of
# shipping it on against a stale executor is "no big video can be uploaded at
# all", so it shipped defaulting to the one that cannot hurt.
#
# Round 63: the default flips to ON. The executor has carried the
# client_proxy_key code since the round-58 redeploy (verified against
# /health's fingerprint), every piece is pinned by tests on both services,
# and the measured stake is a real customer waiting 24m30s before their
# first edit on a 6-minute 4K clip. The env var still works both ways —
# set PROXY_FIRST_UPLOADS=0 on Render to fall back to plain uploads.
PROXY_FIRST_UPLOADS = os.getenv("PROXY_FIRST_UPLOADS", "1").strip().lower() \
    in ("1", "true", "yes", "on")


def is_configured():
    return all(os.getenv(k) for k in (
        "S3_ENDPOINT", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET"))


def bucket():
    return os.environ["S3_BUCKET"]


# One client per process and configuration, reused by every request thread.
# Botocore clients are thread-safe to USE; creating one per call (the old
# behaviour) cost ~50-100 ms of CPU under the GIL on every presign, HEAD and
# frame fetch, and creating them concurrently from the shared default session
# is not thread-safe. Bounded timeouts and standard retries replace botocore's
# legacy defaults (60 s connect + 60 s read, 5 attempts), under which one
# stalled R2 read could hold a gunicorn thread for ten minutes: on Oct 11 the
# MCP endpoint answered nothing for 40 minutes while every job it had queued
# finished in under 225 s.
S3_CONNECT_TIMEOUT_S = float(os.getenv("S3_CONNECT_TIMEOUT_S", "10"))
S3_READ_TIMEOUT_S = float(os.getenv("S3_READ_TIMEOUT_S", "60"))
S3_MAX_ATTEMPTS = int(os.getenv("S3_MAX_ATTEMPTS", "3"))
_clients = {}
_clients_lock = threading.Lock()


def client(public=False, fast=False):
    """public=True mints URLs the BROWSER will hit. On R2 the two endpoints
    are the same; in docker-compose dev the browser reaches MinIO on
    localhost while containers use the internal hostname, so presigning uses
    S3_PUBLIC_ENDPOINT when set.

    fast=True is for small reads on a request thread that has a better
    answer than waiting (an MCP reply embeds a frame, or links it): 5 s to
    connect, 15 s to read, two attempts."""
    endpoint = os.environ["S3_ENDPOINT"]
    if public:
        endpoint = os.getenv("S3_PUBLIC_ENDPOINT") or endpoint
    key = (endpoint, os.environ["S3_ACCESS_KEY_ID"],
           hashlib.sha256(os.environ["S3_SECRET_ACCESS_KEY"].encode())
           .hexdigest(), os.getenv("S3_REGION", "auto"), boto3.client,
           bool(fast))
    with _clients_lock:
        cached = _clients.get(key)
        if cached is None:
            cached = boto3.client(
                "s3",
                endpoint_url=endpoint,
                aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
                aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
                region_name=os.getenv("S3_REGION", "auto"),
                config=Config(signature_version="s3v4",
                              s3={"addressing_style": "path"},
                              connect_timeout=(5.0 if fast else
                                               S3_CONNECT_TIMEOUT_S),
                              read_timeout=(15.0 if fast else
                                            S3_READ_TIMEOUT_S),
                              retries={"max_attempts": (
                                           2 if fast else S3_MAX_ATTEMPTS),
                                       "mode": "standard"},
                              max_pool_connections=32),
            )
            if len(_clients) >= 8:
                _clients.clear()
            _clients[key] = cached
        return cached


# ── Which store (storage_routing: the provider-neutral move off R2) ─────────
#
# Every function below asks the router which store(s) an operation uses. In
# the default "legacy" mode, and in any process without S3_NEXT_*, that is
# client()/bucket() exactly as before; the router never touches the database
# unless the next store is configured.

def _fetch_kv(key):
    import psycopg2
    url = os.environ.get("DIRECT_DATABASE_URL") or os.environ.get(
        "DATABASE_URL")
    if not url:
        return None
    conn = psycopg2.connect(url, connect_timeout=3,
                            options="-c statement_timeout=3000")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _fetch_routing_row():
    return _fetch_kv(routing.KV_KEY)


# The next store's keys: S3_NEXT_* from the environment, else the sealed
# app_kv row (storage_creds.py). creds.ENV is os.environ with that row filled
# in, so the router and every next-store client below see one configuration.
creds.install(lambda: _fetch_kv(creds.KV_KEY))
_router = routing.Router(fetch=_fetch_routing_row, env=creds.ENV)
_locator = routing.Locator()


def router():
    return _router


def next_config():
    """The next store's configuration (env first, then the sealed row), or
    None when it is not configured."""
    return routing.store_config(routing.NEXT, creds.ENV)


def routing_status():
    st = _router.status()
    try:
        st.update(creds.status())
    except Exception:  # noqa: BLE001 — a health route never fails on this
        pass
    return st


def _next_client(public=False, fast=False):
    cfg = next_config()
    if cfg is None:
        raise RuntimeError("the next store (S3_NEXT_*) is not configured")
    endpoint = cfg["public_endpoint"] if public else cfg["endpoint"]
    key = ("next", endpoint, cfg["access_key_id"],
           hashlib.sha256(cfg["secret_access_key"].encode()).hexdigest(),
           cfg["region"], cfg["addressing_style"], boto3.client, bool(fast))
    with _clients_lock:
        cached = _clients.get(key)
        if cached is None:
            cached = boto3.client(
                "s3", endpoint_url=endpoint,
                aws_access_key_id=cfg["access_key_id"],
                aws_secret_access_key=cfg["secret_access_key"],
                region_name=cfg["region"],
                config=Config(**routing.botocore_config_kwargs(
                    cfg,
                    connect_timeout=5.0 if fast else S3_CONNECT_TIMEOUT_S,
                    read_timeout=15.0 if fast else S3_READ_TIMEOUT_S,
                    retries={"max_attempts": 2 if fast else S3_MAX_ATTEMPTS,
                             "mode": "standard"},
                    max_pool_connections=32)))
            creds.watch_client(cached)
            if len(_clients) >= 8:
                _clients.clear()
            _clients[key] = cached
        return cached


def store_client(name, public=False, fast=False):
    if name == routing.NEXT:
        return _next_client(public=public, fast=fast)
    return client(public=public, fast=fast)


def store_bucket(name):
    if name == routing.NEXT:
        return next_config()["bucket"]
    return bucket()


def _plan():
    return _router.plan()


def _log(msg):
    print(f"[storage] {msg}", flush=True)


def _upload_order():
    """Stores an open multipart upload may live in: the one browser uploads
    go to now first, then the other (an upload started before a switch)."""
    plan = _plan()
    rest = [s for s in plan.read if s != plan.presign]
    return [plan.presign] + rest


def _locate(key, fast=False):
    """The store holding ``key`` among the read stores, or the first read
    store when none answers (then the caller behaves as it always did)."""
    plan = _plan()
    if len(plan.read) == 1:
        return plan.read[0]
    hit = _locator.get(plan.mode, key)
    if hit:
        return hit
    for name in plan.read:
        try:
            store_client(name, fast=fast).head_object(
                Bucket=store_bucket(name), Key=key)
        except Exception:  # noqa: BLE001 — missing or unreachable here
            continue
        _locator.put(plan.mode, key, name)
        return name
    return plan.read[0]


def _read_first(fn):
    """fn(store_name) over the read stores; the first non-None answer."""
    for name in _plan().read:
        out = fn(name)
        if out is not None:
            return out
    return None


def put_bytes(key, data, content_type="application/octet-stream",
              content_encoding=None, metadata=None):
    """A small object into every write store (the first one must succeed)."""
    extra = {"ContentEncoding": content_encoding} if content_encoding else {}
    if metadata:
        extra["Metadata"] = dict(metadata)
    for i, name in enumerate(_plan().write):
        try:
            store_client(name).put_object(
                Bucket=store_bucket(name), Key=key, Body=data,
                ContentType=content_type, **extra)
        except Exception as exc:  # noqa: BLE001
            if i == 0:
                raise
            _log(f"secondary write to {name} failed for {key}: {exc}")
    return key


def delete_key(key):
    """Delete one object from every store that may hold it."""
    _locator.forget(key)
    for i, name in enumerate(_plan().delete):
        try:
            store_client(name).delete_object(Bucket=store_bucket(name),
                                             Key=key)
        except Exception as exc:  # noqa: BLE001
            if i == 0:
                raise
            _log(f"secondary delete in {name} failed for {key}: {exc}")


def delete_targets():
    """[(store name, client, bucket)] a bulk delete must reach, primary
    first (the retention sweeper's batches)."""
    return [(name, store_client(name), store_bucket(name))
            for name in _plan().delete]


# THE MAIN VIDEO'S CAP IS AN ABUSE GUARD, NOT THE PRODUCT LIMIT.
#
# It was 2 GB, which contradicted the 3-hour duration limit it shipped beside:
# 2 GiB over 3 hours is 1.6 Mbps, and even ONE hour only fit under 4.7 Mbps.
# What actually costs us is DURATION — it drives index time and render time —
# and that is bounded separately by MAX_DURATION_S. Bytes only cost storage
# and the user's own upload time, so this number just has to be big enough to
# stop being the thing that says no.
#
# It was then 14 GB, sized for DOWNLOADING the whole source into a 32-GiB
# Cloud Run executor (32 / 2.2x headroom). That is no longer how a large
# source is processed: indexing range-reads originals from storage above
# worker/config.INDEX_STREAM_MIN_BYTES (scratch is computed from duration,
# not size), and renders range-read any original of 512 MB or more
# (CLOUDFLARE_STREAM_SOURCE_MIN_BYTES). Meanwhile 8 users in 60 days were
# refused real footage at 15-60 GB (OBS and game recordings, a 17.8 GB
# wedding, a paying customer's 24 GB camera file); four never got any video
# in. 100 GB covers an hour of 4K60 at 200 Mbps and three hours of 1080p60
# at 70 Mbps. The multipart protocol allows far more (part_size_for grows
# the parts past 15.6 GiB, S3 allows 10,000 parts of up to 5 GiB).
MAX_UPLOAD_GB = float(os.getenv("MAX_UPLOAD_GB", "100"))

# Mirrors worker/config.MAX_DURATION_S. Enforced for real in the indexer (it
# needs a probe); carried here so the API and the studio can refuse a 5-hour
# file BEFORE the user spends 40 minutes uploading it, which is the only point
# at which refusing is a kindness.
MAX_DURATION_S = float(os.getenv("MAX_DURATION_S", str(3 * 3600)))


def max_upload_bytes():
    return int(MAX_UPLOAD_GB * 1024 ** 3)


# A video remains the same storage/compute shape whether it starts the project
# or is attached later as B-roll. The studio and public docs advertise one
# 14-GB video-file ceiling; keeping a hidden 500-MB clip cap made the browser
# accept a large attachment only for the presign endpoint to refuse it.
CLIP_MAX_BYTES = max_upload_bytes()


def _size_label(nbytes):
    gb = nbytes / 1024 ** 3
    return f"{gb:.0f} GB" if gb >= 1 else f"{nbytes / 1024 ** 2:.0f} MB"


def upload_limits():
    """The caps, as the API reports them. ONE source of truth: the studio used
    to carry its own hardcoded 2 GiB literal, so raising the server's cap would
    have changed nothing a user could see."""
    return {
        "max_bytes": max_upload_bytes(),
        "max_bytes_label": _size_label(max_upload_bytes()),
        "max_duration_s": MAX_DURATION_S,
        "clip_max_bytes": CLIP_MAX_BYTES,
        "music_max_bytes": MUSIC_MAX_BYTES,
        "image_max_bytes": IMAGE_MAX_BYTES,
        "proxy_max_bytes": PROXY_MAX_BYTES,
        # Advertises that this server understands a browser-built proxy and a
        # deferred original. The studio checks it before spending the user's
        # CPU on a transcode this API would then have no way to accept.
        "proxy_first": PROXY_FIRST_UPLOADS,
        "video_ext": sorted(ALLOWED_VIDEO_EXT),
        "music_ext": sorted(ALLOWED_MUSIC_EXT),
        "image_ext": sorted(ALLOWED_IMAGE_EXT),
    }


def validate_upload(filename, nbytes, kind):
    """Returns (ext, content_type) or raises ValueError with a user-facing reason."""
    ext = os.path.splitext(filename or "")[1].lower()
    allowed, cap = {
        "music": (ALLOWED_MUSIC_EXT, MUSIC_MAX_BYTES),
        "image": (ALLOWED_IMAGE_EXT, IMAGE_MAX_BYTES),
        "clip": (ALLOWED_VIDEO_EXT, CLIP_MAX_BYTES),
        "proxy": (ALLOWED_VIDEO_EXT, PROXY_MAX_BYTES),
    }.get(kind, (ALLOWED_VIDEO_EXT, max_upload_bytes()))
    if ext not in allowed:
        raise ValueError(f"File type {ext or '(none)'} not supported. "
                         f"Allowed: {', '.join(sorted(allowed))}")
    if not isinstance(nbytes, int) or nbytes <= 0:
        raise ValueError("File size missing or invalid")
    if nbytes > cap:
        raise ValueError(f"File is larger than the {_size_label(cap)} limit "
                         f"for {kind or 'video'} uploads")
    return ext, allowed[ext]


KEY_PREFIX = {"original": "originals", "music": "music", "image": "images",
              "clip": "clips", "proxy": "clientproxies"}


def new_original_key(project_id, ext, kind="original"):
    prefix = KEY_PREFIX.get(kind, "originals")
    return f"{prefix}/{project_id}/{uuid.uuid4().hex[:12]}{ext}"


# ── The way in when the browser cannot reach storage (round 61) ─────────────
#
# Every upload goes browser -> object storage directly, on a presigned URL.
# That is right: the bytes never touch our web service, so a 4 GiB original
# costs us nothing but a signature. It has one failure mode, and on
# 2026-07-30 a real customer hit it for an hour:
#
#   registered 07:09:37, then EIGHT upload attempts across FOUR projects over
#   56 minutes, two different files (1.96 MB, then 564 KB — they tried
#   shrinking it), and NOT ONE BYTE LANDED. Zero assets, zero jobs. Then they
#   left.
#
# The server issued all eight presigns fine. Every PUT died in the browser at
# xhr.onerror with `status: null, offline: false` — the browser killing a
# cross-origin request, not a slow line. Their app, their API calls and their
# eight presigns all worked. Every other account in the system has at least one
# successful upload; this one had zero, forever, with no way out and no other
# route to try.
#
# So there is a second route. It costs us bandwidth and ties up a web worker
# for the duration, which is exactly why it is a FALLBACK and not the path —
# and why it is capped well below the direct limit. A cap means some users
# still cannot get in, but "your 4 GB file cannot be routed through us, try a
# different network" is a sentence someone can act on. Eight silent failures
# are not.
# The cap is 64 MB and it is MEASURED, not chosen. Posting identical bodies to
# the same backend route on 2026-07-30:
#
#   via the Vercel rewrite   20 MB ok, 40 MB ok, 50 MB -> 502
#                            (x-vercel-error: ROUTER_EXTERNAL_TARGET_
#                             CONNECTION_ERROR_CD8)
#   straight at Render       the SAME 50 MB body arrived fine
#
# So the client posts relays DIRECTLY at this service, past the rewrite. Above
# 50 MB the numbers stop being trustworthy — the probe route 404s without
# draining the request body, which can reset a connection on its own — so the
# ceiling beyond that is unmeasured and this stays below it rather than
# guessing. (It used to also equal SINGLE_PUT_LIMIT; that limit has since
# dropped to 16 MB for parallelism, while this cap stays at its measured
# ceiling — a blocked browser with a 40 MB multipart file still deserves the
# relay.) Bigger files get an honest refusal naming the real cause, which is
# still infinitely better than the eight silent failures that prompted this.
RELAY_MAX_BYTES = int(os.getenv("RELAY_MAX_UPLOAD_MB", "64")) * 1024 * 1024


class RelayTooLarge(ValueError):
    pass


def _capped_reader(stream, limit):
    """Wrap a request stream so it cannot deliver more than `limit` bytes.

    Content-Length is a claim by the client, so it is checked BEFORE we read
    and enforced again WHILE we read: a lying header must not be able to
    stream an unbounded body through a service sized for small ones.
    """
    class _Capped:
        def __init__(self):
            self.seen = 0

        def read(self, n=-1):
            chunk = stream.read(n) if n is not None and n >= 0 \
                else stream.read()
            if not chunk:
                return chunk
            self.seen += len(chunk)
            if self.seen > limit:
                raise RelayTooLarge(
                    f"upload body exceeded {_size_label(limit)}")
            return chunk

    return _Capped()


def relay_upload(key, stream, content_type, declared_bytes=None):
    """Stream a request body straight into object storage.

    upload_fileobj chunks internally (and does its own multipart against R2),
    so memory here is one part, not one file — the constraint this route lives
    under is TIME on a web worker, not RAM.
    """
    if declared_bytes is not None and declared_bytes > RELAY_MAX_BYTES:
        raise RelayTooLarge(
            f"file is larger than the {_size_label(RELAY_MAX_BYTES)} limit "
            f"for uploads routed through our servers")
    name = _plan().presign
    store_client(name).upload_fileobj(
        _capped_reader(stream, RELAY_MAX_BYTES), store_bucket(name), key,
        ExtraArgs={"ContentType": content_type or "application/octet-stream"})


# An agent's upload plan travels inside one tool reply. 125 presigned part
# URLs for a 1.43 GB file made a 103,332-character reply the connector could
# not carry (Oct 11 run, B11), so MCP plans use fewer, larger parts: a script
# on a workstation, not a browser on a phone uplink, PUTs them.
MCP_MAX_PARTS = int(os.getenv("MCP_UPLOAD_MAX_PARTS", "16"))
MAX_S3_PART_SIZE = 5 * 1024 ** 3


def part_size_for_parts(nbytes, max_parts):
    """Smallest whole-MiB part size that fits ``nbytes`` in ``max_parts``."""
    mib = 1024 * 1024
    need = (int(nbytes) + max(1, int(max_parts)) - 1) // max(1, int(max_parts))
    size = ((need + mib - 1) // mib) * mib
    return max(part_size_for(nbytes), min(MAX_S3_PART_SIZE, size))


def presign_upload(key, nbytes, content_type, max_parts=None):
    """Single presigned PUT for small files, presigned multipart for large.

    Returns a dict the browser can act on directly. ``max_parts`` caps the
    number of parts (larger parts) for a plan that travels in a tool reply.
    The store is the one browser uploads go to in the current routing mode.
    """
    name = _plan().presign
    c, b = store_client(name, public=True), store_bucket(name)
    if nbytes <= SINGLE_PUT_LIMIT:
        url = c.generate_presigned_url(
            "put_object",
            Params={"Bucket": b, "Key": key, "ContentType": content_type},
            ExpiresIn=PRESIGN_UPLOAD_EXPIRY,
        )
        return {"mode": "single", "storage_key": key, "url": url,
                "content_type": content_type}

    mpu = c.create_multipart_upload(
        Bucket=b, Key=key, ContentType=content_type)
    upload_id = mpu["UploadId"]
    part_size = (part_size_for_parts(nbytes, max_parts) if max_parts
                 else part_size_for(nbytes))
    n_parts = (nbytes + part_size - 1) // part_size
    urls = [
        {
            "part_number": i,
            "url": c.generate_presigned_url(
                "upload_part",
                Params={"Bucket": b, "Key": key,
                        "UploadId": upload_id, "PartNumber": i},
                ExpiresIn=PRESIGN_UPLOAD_EXPIRY,
            ),
        }
        for i in range(1, n_parts + 1)
    ]
    return {"mode": "multipart", "storage_key": key, "upload_id": upload_id,
            "part_size": part_size, "part_urls": urls,
            "content_type": content_type}


def multipart_part_size(nbytes, part_size=None):
    """The part size an upload of ``nbytes`` was presigned with.

    A resuming client reports the size it was given; it is accepted only if
    it is a size this module could have issued (whole MiB, within S3's 5 MiB
    - 5 GiB part range and its 10,000-part limit), otherwise today's
    part_size_for(nbytes) is the answer."""
    mib = 1024 * 1024
    try:
        size = int(part_size or 0)
    except (TypeError, ValueError):
        size = 0
    n = int(nbytes)
    if (size >= 5 * mib and size <= MAX_S3_PART_SIZE and size % mib == 0
            and (n + size - 1) // size <= 10_000):
        return size
    return part_size_for(n)


def expected_part_bytes(nbytes, part_size, part_number):
    """Bytes part ``part_number`` (1-based) of an ``nbytes`` upload holds."""
    start = (int(part_number) - 1) * int(part_size)
    return max(0, min(int(part_size), int(nbytes) - start))


class UploadGone(Exception):
    """The multipart upload id no longer exists (completed, aborted or
    expired by the bucket's lifecycle rule)."""


def resume_multipart(key, upload_id, nbytes, content_type=None,
                     part_size=None):
    """Carry on an open multipart upload from the parts storage already holds.

    51 users in 60 days started a video upload that never landed: a closed
    tab, a sleeping laptop, a locked phone. Every part they had sent was
    still sitting in the bucket under the same upload id, and the studio
    started again from byte zero (33 users restarted the same file). Storage
    is the authority on what arrived, so this lists the parts it holds,
    keeps every one whose size is exactly right, and presigns fresh URLs
    for only the rest (a part of the wrong size is simply sent again, which
    overwrites it). Fresh URLs also outlive a transfer slower than
    PRESIGN_UPLOAD_EXPIRY.

    Returns the same shape presign_upload does for multipart, plus
    ``completed_parts`` [{part_number, etag}] and ``resumed: True``. When the
    upload id is gone but the object is already assembled at the right size
    (the tab died between the last part and /uploads/complete), returns
    {"assembled": True}. Raises UploadGone otherwise."""
    n = int(nbytes)
    size = multipart_part_size(n, part_size)
    n_parts = (n + size - 1) // size
    store = _upload_store(key, upload_id)
    try:
        stored = list_parts(key, upload_id, store=store)
    except Exception as exc:  # noqa: BLE001 — classified below
        if error_code(exc) != "NoSuchUpload":
            raise
        if head_bytes(key) == n:
            return {"mode": "multipart", "storage_key": key,
                    "upload_id": None, "assembled": True,
                    "part_size": size, "part_urls": [],
                    "completed_parts": [], "content_type": content_type}
        raise UploadGone(str(exc)[:300]) from exc
    have = {}
    for p in stored:
        num = int(p["part_number"])
        if 1 <= num <= n_parts and \
                int(p.get("size") or 0) == expected_part_bytes(n, size, num):
            have[num] = {"part_number": num, "etag": p["etag"]}
    c, b = store_client(store, public=True), store_bucket(store)
    urls = []
    for i in range(1, n_parts + 1):
        if i in have:
            urls.append({"part_number": i, "url": None, "done": True})
            continue
        urls.append({"part_number": i, "url": c.generate_presigned_url(
            "upload_part",
            Params={"Bucket": b, "Key": key, "UploadId": upload_id,
                    "PartNumber": i},
            ExpiresIn=PRESIGN_UPLOAD_EXPIRY)})
    return {"mode": "multipart", "storage_key": key, "upload_id": upload_id,
            "part_size": size, "part_urls": urls,
            "completed_parts": [have[i] for i in sorted(have)],
            "content_type": content_type, "resumed": True,
            "bytes_done": sum(expected_part_bytes(n, size, i) for i in have)}


def find_multipart_upload(key):
    """The newest open multipart upload id for exactly ``key``, or None.

    A proxy-first original's key lives on its asset row, but its upload id
    lived only in the tab that started it. When that tab is gone (closed,
    reloaded, another device), this recovers the id from storage itself so
    the parts already sent are kept. Every store an upload may live in is
    searched, the one browser uploads go to now first."""
    for name in _upload_order():
        try:
            res = store_client(name).list_multipart_uploads(
                Bucket=store_bucket(name), Prefix=key)
        except Exception:  # noqa: BLE001 — unsupported or transient
            continue
        ups = [u for u in res.get("Uploads") or [] if u.get("Key") == key]
        if not ups:
            continue
        ups.sort(key=lambda u: str(u.get("Initiated") or ""))
        return ups[-1].get("UploadId")
    return None


def _upload_store(key, upload_id):
    """The store that holds the open multipart upload ``upload_id``.

    An upload id belongs to the store that issued it, and an upload started
    before a routing switch finishes after it. With one store this is that
    store, with no request; otherwise the first store whose ListParts
    accepts the id, else the current upload store (whose NoSuchUpload then
    reads as it always did)."""
    order = _upload_order()
    if len(order) == 1 or not upload_id:
        return order[0]
    for name in order:
        try:
            store_client(name).list_parts(
                Bucket=store_bucket(name), Key=key, UploadId=upload_id,
                MaxParts=1)
            return name
        except Exception:  # noqa: BLE001 — not this store's upload
            continue
    return order[0]


def relay_part(key, upload_id, part_number, stream, declared_bytes=None):
    """Write ONE part of an open multipart upload from a request body.

    The browser-cannot-reach-storage fallback used to relay a whole file
    through this service, capped at 64 MB, so a blocked 62 MB upload already
    failed it (2109) and nothing bigger ever had a way in. A part is at most
    a few dozen MiB whatever the file weighs, so relaying part by part has
    no file-size ceiling and keeps the same upload id, the same completion
    and the parts already sent directly. The body is spooled (memory up to
    8 MiB, then disk) because upload_part needs a seekable body; it is
    still bounded by RELAY_MAX_BYTES. Returns the part's ETag."""
    import tempfile
    if declared_bytes is not None and declared_bytes > RELAY_MAX_BYTES:
        raise RelayTooLarge(
            f"part is larger than the {_size_label(RELAY_MAX_BYTES)} limit "
            f"for uploads routed through our servers")
    reader = _capped_reader(stream, RELAY_MAX_BYTES)
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as body:
        while True:
            chunk = reader.read(1024 * 1024)
            if not chunk:
                break
            body.write(chunk)
        if body.tell() <= 0:
            raise ValueError("empty part body")
        body.seek(0)
        store = _upload_store(key, upload_id)
        res = store_client(store).upload_part(
            Bucket=store_bucket(store), Key=key, UploadId=upload_id,
            PartNumber=int(part_number), Body=body)
    return str(res.get("ETag") or "")


def complete_multipart(key, upload_id, parts, store=None):
    """parts: [{"part_number": int, "etag": str}] from the browser."""
    ordered = sorted(parts, key=lambda p: int(p["part_number"]))
    store = store or _upload_store(key, upload_id)
    store_client(store).complete_multipart_upload(
        Bucket=store_bucket(store), Key=key, UploadId=upload_id,
        MultipartUpload={"Parts": [
            {"PartNumber": int(p["part_number"]), "ETag": p["etag"]}
            for p in ordered
        ]},
    )


def error_code(exc):
    """The S3 error code of a botocore ClientError ('InvalidPart',
    'NoSuchUpload', ...), or None for anything else (a timeout, a reset)."""
    try:
        return str(exc.response["Error"]["Code"])
    except (AttributeError, KeyError, TypeError):
        return None


def list_parts(key, upload_id, store=None):
    """[{part_number, etag, size}] of the parts storage holds for an open
    multipart upload, in part order (every page). Raises like the client
    does, NoSuchUpload included, when the upload id is gone."""
    store = store or _upload_store(key, upload_id)
    c, b = store_client(store), store_bucket(store)
    out, marker = [], None
    for _page in range(20):                 # 10,000 parts at most
        kw = {"Bucket": b, "Key": key, "UploadId": upload_id,
              "MaxParts": 1000}
        if marker:
            kw["PartNumberMarker"] = marker
        page = c.list_parts(**kw)
        for p in page.get("Parts") or []:
            out.append({"part_number": int(p["PartNumber"]),
                        "etag": str(p.get("ETag") or ""),
                        "size": int(p.get("Size") or 0)})
        marker = page.get("NextPartNumberMarker")
        if not page.get("IsTruncated") or not marker:
            break
    return sorted(out, key=lambda p: p["part_number"])


def _etag(value):
    return str(value or "").strip().strip('"').lower()


# Errors the caller repairs with the SAME upload id: a mistyped or stale
# ETag, a part list out of order or missing a part. Storage keeps every
# uploaded part until the upload is completed or aborted, so none of them
# is a reason to throw the parts away.
REPAIRABLE_COMPLETE_ERRORS = frozenset((
    "InvalidPart", "InvalidPartOrder", "EntityTooSmall", "MalformedXML"))


def finish_multipart(key, upload_id, parts):
    """Complete a multipart upload WITHOUT ever aborting it (CHM 2013 run,
    23 Oct 2026: one mistyped ETag in a 16-part, 437 MB upload_finish
    answered InvalidPart, the failure path aborted the upload, the retry
    with the corrected list answered NoSuchUpload and every part was PUT
    again). Returns a dict:

    - {"ok": True, "repaired": [n, ...]}: completed. ``repaired`` names the
      parts whose listed ETag did not match the one storage computed when
      that part was PUT (a copy slip): the list named exactly the parts
      storage holds, so the upload is completed from storage's own ETags
      — the bytes are the ones the caller sent either way.
    - {"ok": False, "gone": True, ...}: the upload id no longer exists
      (completed already, aborted, or expired) — only a new upload_start
      helps, unless the object is already assembled (the caller checks).
    - {"ok": False, "gone": False, "missing": [...], "unlisted": [...],
      "stored": n, ...}: the upload is kept with every part it holds; the
      message says what to PUT or list and the retry uses the same
      upload_id.

    An incomplete upload left behind costs storage until the bucket's
    lifecycle rule clears it; aborting one cost the whole transfer."""
    store = _upload_store(key, upload_id)
    try:
        complete_multipart(key, upload_id, parts, store=store)
        return {"ok": True, "repaired": []}
    except Exception as exc:  # noqa: BLE001 — classified below
        first = exc
    code = error_code(first)
    if code == "NoSuchUpload":
        return {"ok": False, "gone": True, "code": code,
                "message": str(first)[:300]}
    try:
        stored = list_parts(key, upload_id, store=store)
    except Exception as exc:  # noqa: BLE001
        if error_code(exc) == "NoSuchUpload":
            return {"ok": False, "gone": True, "code": code or "NoSuchUpload",
                    "message": str(first)[:300]}
        return {"ok": False, "gone": False, "code": code,
                "message": str(first)[:300], "missing": [], "unlisted": [],
                "stored": None}
    have = {p["part_number"]: p for p in stored}
    sent = {}
    for p in parts or []:
        try:
            sent[int(p["part_number"])] = p.get("etag")
        except (KeyError, TypeError, ValueError):
            continue
    missing = sorted(set(sent) - set(have))
    unlisted = sorted(set(have) - set(sent))
    if have and not missing and not unlisted and \
            sorted(have) == list(range(1, len(have) + 1)):
        repaired = sorted(n for n, tag in sent.items()
                          if _etag(tag) != _etag(have[n]["etag"]))
        try:
            complete_multipart(key, upload_id, [
                {"part_number": n, "etag": have[n]["etag"]}
                for n in sorted(have)], store=store)
            return {"ok": True, "repaired": repaired}
        except Exception as exc:  # noqa: BLE001
            first, code = exc, error_code(exc) or code
    return {"ok": False, "gone": False, "code": code,
            "message": str(first)[:300], "missing": missing,
            "unlisted": unlisted, "stored": len(have),
            "gaps": sorted(set(range(1, max(set(have) | set(sent) or {0}) + 1))
                           - set(have))}


def finish_failure_text(res):
    """One sentence for a finish_multipart failure: what happened and the
    retry that works."""
    if res.get("gone"):
        return ("the multipart upload id no longer exists (it was completed, "
                "aborted or expired): start a new upload with upload_start "
                f"({res.get('code') or 'NoSuchUpload'})")
    bits = []
    if res.get("missing"):
        bits.append("storage holds no upload for part(s) "
                    + ", ".join(map(str, res["missing"][:20]))
                    + " — PUT them to their URLs from upload_start")
    gaps = [n for n in res.get("gaps") or [] if n not in (res.get("missing") or [])]
    if gaps:
        bits.append("part(s) " + ", ".join(map(str, gaps[:20]))
                    + " were never uploaded — PUT them")
    if res.get("unlisted"):
        bits.append("part(s) " + ", ".join(map(str, res["unlisted"][:20]))
                    + " are uploaded but missing from the parts list — "
                      "list every part")
    held = (f"the upload is KEPT with the {res['stored']} part(s) storage "
            "holds" if res.get("stored") is not None else "the upload is KEPT")
    return (f"storage refused to assemble it ({res.get('code') or 'error'}: "
            f"{res.get('message') or ''}); " + held
            + ("; " + "; ".join(bits) if bits else "")
            + " — then retry upload_finish with the SAME storage_key and "
              "upload_id (no new upload_start)")


def abort_multipart(key, upload_id):
    try:
        store = _upload_store(key, upload_id)
        store_client(store).abort_multipart_upload(
            Bucket=store_bucket(store), Key=key, UploadId=upload_id)
    except Exception:
        pass


def head_bytes(key, fast=False):
    """Size of an object, or None if it doesn't exist (in any read store)."""
    def one(name):
        try:
            return store_client(name, fast=fast).head_object(
                Bucket=store_bucket(name), Key=key)["ContentLength"]
        except Exception:
            return None
    return _read_first(one)


def get_range(key, nbytes=64):
    """First nbytes of an object (for magic-byte sniffing). None on failure."""
    def one(name):
        try:
            obj = store_client(name).get_object(
                Bucket=store_bucket(name), Key=key,
                Range=f"bytes=0-{max(0, int(nbytes) - 1)}")
            return obj["Body"].read()
        except Exception:
            return None
    return _read_first(one)


def get_range_at(key, offset, length):
    """`length` bytes from `offset`. None on failure.

    Used to read an MP4's header boxes without downloading the file — the moov
    atom of a 14 GB original is a few hundred bytes, and walking to it costs a
    handful of 16-byte reads.
    """
    if length <= 0:
        return b""

    def one(name):
        try:
            obj = store_client(name).get_object(
                Bucket=store_bucket(name), Key=key,
                Range=f"bytes={int(offset)}-{int(offset) + int(length) - 1}")
            return obj["Body"].read()
        except Exception:
            return None
    return _read_first(one)


def get_object_whole(key, max_bytes, fast=True):
    """A WHOLE object in memory, or None if it is bigger than max_bytes.

    The only caller is the MCP surface embedding a small video in a tool reply
    (routes/mcp.watch_video), and the cap is what keeps that honest: this
    service runs 3 SYNC gunicorn workers, so a body held here is a third of
    the API holding it. Size is checked with a HEAD first — reading and then
    discarding would already have paid for the download.

    Every caller embeds bytes in a reply that also carries a link, so the
    fast client is the default: a slow read degrades to the link instead of
    holding the request thread."""
    def one(name):
        try:
            c = store_client(name, fast=fast)
            n = c.head_object(Bucket=store_bucket(name),
                              Key=key)["ContentLength"]
        except Exception:
            return None
        if n is None or n > int(max_bytes):
            return False                    # found, too big: stop looking
        try:
            return c.get_object(Bucket=store_bucket(name),
                                Key=key)["Body"].read()
        except Exception:
            return None
    out = _read_first(one)
    return out if isinstance(out, (bytes, bytearray)) else None


def content_matches_kind(head, kind):
    """Best-effort magic-byte check on the first bytes of an upload. Returns
    True when the bytes match the declared kind, False on a CLEAR mismatch
    (e.g. a JPEG renamed to .mp4), and None when undetermined — callers must
    treat None as 'allow', never blocking on ambiguity."""
    b = head or b""
    if len(b) < 12:
        return None
    is_iso = b[4:8] == b"ftyp"                    # mp4/mov/m4a container
    is_riff = b[0:4] == b"RIFF"
    if kind in ("clip", "original", "video"):
        if is_iso or b[0:4] == b"\x1aE\xdf\xa3":  # iso-bmff / matroska-webm
            return True
        if is_riff and b[8:12] == b"AVI ":
            return True
        if b[0:3] == b"FLV" or b[0] == 0x47:      # flv / mpeg-ts
            return True
        # A still image or plain text renamed to a video extension: reject.
        if (b[0:3] == b"\xff\xd8\xff" or b[0:4] == b"\x89PNG"
                or b[0:3] == b"GIF"):
            return False
        return None
    if kind == "image":
        if (b[0:3] == b"\xff\xd8\xff" or b[0:4] == b"\x89PNG"
                or b[0:3] == b"GIF" or (is_riff and b[8:12] == b"WEBP")):
            return True
        return False
    if kind == "music":
        if b[0:3] == b"ID3" or b[0:2] in (
                b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xf1",
                b"\xff\xf9"):                     # mp3 / aac frame sync
            return True
        if (is_riff and b[8:12] == b"WAVE") or b[0:4] == b"OggS" \
                or b[0:4] == b"fLaC" or is_iso:   # wav / ogg / flac / m4a
            return True
        return None                              # audio formats vary — allow
    return None


# Every top-level key prefix a project's objects can live under. Used to wipe
# a project (GDPR/account deletion, storage reclaim) — keys are laid out
# {prefix}/{project_id}/..., so a full wipe iterates each prefix.
# "renders" stays for objects written before 2026-07-18; "media" is where every
# render lands now. A prefix missing here silently orphans bytes on delete.
DELETE_PREFIXES = ("originals", "proxies", "audio", "thumbs", "sheets",
                   "renders", "media", "music", "images", "clips", "generated",
                   "generated_sfx", "generated_video", "fetched")


def delete_project_objects(project_id):
    """Delete every stored object belonging to a project across all prefixes.
    Returns the number of objects deleted (in the primary delete store; the
    others are wiped too, best effort). Idempotent (missing keys are a no-op)
    so it is safe to re-run."""
    total = 0
    for i, name in enumerate(_plan().delete):
        try:
            n = _delete_project_objects_in(store_client(name),
                                           store_bucket(name), project_id)
        except Exception as exc:  # noqa: BLE001
            if i == 0:
                raise
            _log(f"secondary wipe of project {project_id} in {name} "
                 f"failed: {exc}")
            continue
        if i == 0:
            total = n
    _locator.clear()
    return total


def _delete_project_objects_in(c, b, project_id):
    total = 0
    for prefix in DELETE_PREFIXES:
        token = None
        while True:
            kw = {"Bucket": b, "Prefix": f"{prefix}/{project_id}/"}
            if token:
                kw["ContinuationToken"] = token
            resp = c.list_objects_v2(**kw)
            objs = resp.get("Contents") or []
            if objs:
                c.delete_objects(Bucket=b, Delete={
                    "Objects": [{"Key": o["Key"]} for o in objs]})
                total += len(objs)
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
    return total


def presign_get(key, expires=None, download_name=None):
    """A presigned GET on the store that holds ``key`` (one HEAD per new key
    while two stores are read; none in the default single-store mode)."""
    expires = expires or PRESIGN_GET_EXPIRY
    name = _locate(key)
    params = {"Bucket": store_bucket(name), "Key": key}
    if download_name:
        params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
    return store_client(name, public=True).generate_presigned_url(
        "get_object", Params=params, ExpiresIn=expires)
