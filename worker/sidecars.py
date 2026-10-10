"""Sidecar store: analyse once, in source space, cache forever (ROADMAP A1).

Wave 0 track 0A, contract pinned in worker/devices/INTERFACES.md §2. The
signatures, constants, key formula and chunk grid are the round lead's; the
bodies (storage, launching, waiting) are 0A's.

WHAT A SIDECAR IS. A model's output over a window of the ORIGINAL upload,
measured on the source clock (the job reads the proxy), stored as an
immutable object keyed by

    sha256(source_sha, kind, model_id, model_version, params, src_range)

so the same footage hits the same key whoever asks, for every short, aspect
and re-edit cut from it. The renderer maps a sidecar into program space
through the picture's own geometry chain (Wave 1); nothing here knows about
programs.

HOW IT BEHAVES
  * ensure() returns at once. It reads the store (one LIST), and launches
    only the chunks nobody has: not ready, not leased by a live launch, not
    failed twice recently. Writes never block on analysis; a write says
    "measuring (~N s)" from estimate_s().
  * A chunk is READY when its manifest exists and this reader knows the
    manifest's model version and format (the generalised matte handshake).
    An unknown version reads as ABSENT, never as ready: a lane on other code
    cannot poison a key, and the executor refuses a payload whose model
    version is not its own.
  * A failed launch is retried ONCE, on a different shard. A second failure
    marks only that chunk failed; its window degrades (black, inert) while
    every other chunk stays usable (AT-0A-3).
  * Missing chunks are black to every consumer. Nothing here raises into a
    render or a write: every public call degrades to "absent".

STORAGE (INTERFACES.md §2.4), the product bucket, or VALMERA_SIDECAR_DIR in
development and tests:

    sidecars/<sha[:2]>/<sha>/<kind>/<key>.<ext>           data (written first)
    sidecars/<sha[:2]>/<sha>/<kind>/<key>.json            manifest (LAST: presence == ready)
    sidecars/<sha[:2]>/<sha>/<kind>/<key>.pending.json    {lease_until, attempt, lane, started_at}
    sidecars/<sha[:2]>/<sha>/<kind>/<key>.failed.json     {reason, attempts, at, retry_after}

A kind whose data is JSON uses a two-part extension (``faces.json``) so its
data object never collides with its manifest.

LANES. Jobs run as the one ``sidecar`` kind (jobs_ext/sidecar.py) on the
batch lane, held to SIDECAR_SHARDS shards; the ValmeraVision lane exists as
code and stays dark (INTERFACES.md §2.6). Top-level imports are the standard
library only, so the backend can load this file for summary().
"""

import hashlib
import itertools
import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

STATUSES = ("ready", "pending", "failed", "absent")
PRIORITIES = ("interactive", "prefetch", "backfill")   # queue order, §8
CHUNK_MAX_S = 15.0

# Pinned kind names. Wave 0 ships the first two; the rest are reserved so
# later tracks cannot pick a colliding name. A kind's model id/version live
# with its runner (0A: models/*.json + models.lock), never at a call site.
KINDS = ("matte.person", "faces.yunet")
RESERVED_KINDS = (
    "rhythm.music", "rhythm.speech",           # 2C (speech = prosody grid)
    "embed.siglip2", "embed.dinov2",           # 2E
    "landmarks.face", "landmarks.hand", "landmarks.pose", "track.points",
    "camera.motion",                           # 3A
    "objects.sam2", "depth.small",             # 3B / 4A
    "planes.h", "plate.clean",                 # 3C
    "ocr.text",                                # 0D refbench / 2F
)

PREFIX = "sidecars"
MANIFEST_VERSION = 1
# One launch plus one retry elsewhere; then that window degrades (AT-0A-3).
MAX_ATTEMPTS = 2
FINAL_WAIT_MAX_S = 90.0
_PRIORITY_RANK = {p: i for i, p in enumerate(PRIORITIES)}

_MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "models")

# How each shipped kind is produced and stored. ``model`` names the
# models/<name>.json that pins model_id + model_version; ``formats`` are the
# data formats a reader of THIS code understands (anything else reads as
# absent); ``defaults`` are merged under the caller's params before keying,
# so None and the explicit defaults share one cache entry. ``ms_per_frame``
# is the batch-lane cost (RVM measured, config.py MATTE_RVM_BUDGET note) and
# feeds estimate_s; ``group_s`` is how much footage one call measures.
_KIND_SPECS = {
    "matte.person": {
        "model": "rvm_mobilenetv3",
        "format": "mask.h264.v1", "formats": ("mask.h264.v1",),
        "ext": "mp4", "content_type": "video/mp4",
        # RVM first; u2net (per-frame, unpinned file keyed by its own sha)
        # is the in-job fallback rung when RVM is absent on the lane.
        "fallback_models": ("u2net_human_seg",),
        "defaults": {"fps_max": 30.0, "max_w": 960, "warmup_s": 1.0},
        "rate": "fps_max", "ms_per_frame": 128.0, "group_s": 15.0,
    },
    "faces.yunet": {
        "model": "yunet",
        "format": "faces.json.v1", "formats": ("faces.json.v1",),
        "ext": "faces.json", "content_type": "application/json",
        "fallback_models": (),
        "defaults": {"fps": 10.0, "det_w": 320, "score": 0.6, "nms": 0.3,
                     "top_k": 50},
        "rate": "fps", "ms_per_frame": 6.0, "group_s": 600.0,
    },
}
# Seconds a launch costs beyond compute (admission, cold start, staging).
_CALL_OVERHEAD_S = 20.0
_LIST_TTL_S = 1.0
_TOUCH_AFTER_S = 30 * 86400        # refresh last-use for the 90-day rule


@dataclass(frozen=True)
class SourceRef:
    """The footage a sidecar measures. ``sha256`` is the ORIGINAL upload's
    content hash (assets.sha256 / render meta src_sha256); ``storage_key``
    the file the job reads (the 540p/720p proxy for model work)."""
    sha256: str
    storage_key: str
    duration_s: float
    fps: float
    width: int
    height: int
    project_id: int = None
    shots: tuple = ()          # index shot start times, SOURCE seconds


@dataclass(frozen=True)
class ChunkRef:
    key: str
    t0: float                  # SOURCE seconds, [t0, t1)
    t1: float


@dataclass(frozen=True)
class Handle:
    kind: str
    source_sha: str
    chunks: tuple = field(default_factory=tuple)
    priority: str = "interactive"
    params: str = "{}"         # canonical JSON of the params


def canonical_params(params):
    return json.dumps(params or {}, sort_keys=True, separators=(",", ":"),
                      default=str)


def sidecar_key(kind, source_sha, model_id, model_version, params,
                src_range):
    """The immutable object key. ``src_range`` is (t0, t1) in SOURCE seconds,
    folded to whole milliseconds so float noise never forks a cache entry."""
    t0, t1 = src_range
    blob = json.dumps([source_sha, kind, model_id, str(model_version),
                       canonical_params(params),
                       [int(round(float(t0) * 1000)),
                        int(round(float(t1) * 1000))]],
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def chunk_ranges(shots, duration_s, max_s=CHUNK_MAX_S):
    """Deterministic chunk grid: every index shot [a, b) is split into
    ceil((b - a) / max_s) EQUAL pieces. A range request maps onto the chunks
    it overlaps, so the same footage always hits the same keys whoever asks.
    ``shots`` are shot start times (SOURCE seconds); none means one shot."""
    duration_s = float(duration_s)
    starts = sorted({0.0} | {float(s) for s in shots or ()
                             if 0.0 < float(s) < duration_s})
    edges = starts + [duration_s]
    out = []
    for a, b in zip(edges, edges[1:]):
        if b - a <= 1e-6:
            continue
        n = max(1, math.ceil((b - a) / max_s - 1e-9))
        step = (b - a) / n
        for i in range(n):
            out.append((round(a + i * step, 3),
                        round(b if i == n - 1 else a + (i + 1) * step, 3)))
    return out


def chunks_for(shots, duration_s, ranges, max_s=CHUNK_MAX_S):
    """The grid chunks that overlap any of ``ranges`` (SOURCE seconds)."""
    grid = chunk_ranges(shots, duration_s, max_s)
    want = [(float(a), float(b)) for a, b in ranges or ()]
    return [(a, b) for a, b in grid
            if any(b > s + 1e-6 and a < e - 1e-6 for s, e in want)]


# ── kinds, models and params ─────────────────────────────────────────────

_MODEL_CACHE = {}


def model_info(name, models_dir=None):
    """models/<name>.json (the pinned model record), cached."""
    path = os.path.join(models_dir or _MODELS_DIR, f"{name}.json")
    hit = _MODEL_CACHE.get(path)
    if hit is None:
        with open(path, encoding="utf-8") as f:
            hit = json.load(f)
        _MODEL_CACHE[path] = hit
    return hit


def spec(kind):
    """{kind, model, model_id, model_version, format, formats, ext, ...} for
    a shipped kind. KeyError for a reserved or unknown one."""
    base = _KIND_SPECS[kind]
    info = model_info(base["model"])
    out = dict(base)
    out.update(kind=kind, model_id=info["model_id"],
               model_version=str(info["model_version"]),
               model_sha256=info.get("sha256"))
    return out


def resolve_params(kind, params=None):
    """The params a key is made from: the kind's defaults under the caller's,
    numbers normalised so 30 and 30.0 key the same."""
    out = dict(_KIND_SPECS.get(kind, {}).get("defaults") or {})
    for k, v in (params or {}).items():
        out[str(k)] = v
    for k, v in list(out.items()):
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and float(v) == int(v):
            out[k] = int(v)
    return out


def _rate(kind, params, source_fps=None):
    sp = _KIND_SPECS[kind]
    fps = float(params.get(sp["rate"]) or 10.0)
    if sp["rate"] == "fps_max" and source_fps:
        fps = min(fps, float(source_fps))
    return max(1.0, fps)


def chunk_key(kind, source_sha, params, span, model_id=None,
              model_version=None):
    """The key of one chunk for this code's model of ``kind``."""
    if model_id is None:
        sp = spec(kind)
        model_id, model_version = sp["model_id"], sp["model_version"]
    return sidecar_key(kind, source_sha, model_id, model_version,
                       resolve_params(kind, params), span)


def source_from(sha256, storage_key, info, project_id=None, shots=()):
    """A SourceRef from a probe/asset-like dict (duration|duration_s, fps,
    width, height) and index shots (dicts, models or start floats)."""
    info = info or {}
    starts = []
    for s in shots or ():
        v = s if isinstance(s, (int, float)) else (
            s.get("start") if isinstance(s, dict) else getattr(s, "start", None))
        try:
            if v is not None and float(v) > 0.0:
                starts.append(round(float(v), 3))
        except (TypeError, ValueError):
            continue
    return SourceRef(
        sha256=str(sha256), storage_key=str(storage_key),
        duration_s=float(info.get("duration") or info.get("duration_s") or 0.0),
        fps=float(info.get("fps") or 0.0) or 25.0,
        width=int(info.get("width") or 0), height=int(info.get("height") or 0),
        project_id=project_id, shots=tuple(sorted(set(starts))))


_SHA_IN_KEY = re.compile(r"(?:^|/)([0-9a-f]{64})(?:/|\.|$)")


def index_source_sha(index):
    """The source sha an index was built from, recovered from its content-
    addressed artifact keys (tiles/<project>/<sha>/...), or None."""
    index = index or {}
    keys = list(index.get("tile_keys") or []) + list(
        index.get("sheet_keys") or [])
    for s in index.get("shots") or []:
        k = s.get("thumb_key") if isinstance(s, dict) else None
        if k:
            keys.append(k)
    for k in keys:
        m = _SHA_IN_KEY.search(str(k))
        if m:
            return m.group(1)
    return None


# ── object stores ────────────────────────────────────────────────────────

class LocalStore:
    """A directory laid out exactly like the bucket prefix (dev, tests)."""
    name = "local"

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def _p(self, key):
        return os.path.join(self.root, *key.split("/"))

    def list(self, prefix):
        out = {}
        base = self._p(prefix.rstrip("/"))
        if not os.path.isdir(base):
            return out
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.startswith(".") or f.endswith(".tmp"):
                    continue
                p = os.path.join(root, f)
                rel = os.path.relpath(p, self.root).replace(os.sep, "/")
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[rel] = {"size": st.st_size, "mtime": st.st_mtime}
        return out

    def get_bytes(self, key):
        try:
            with open(self._p(key), "rb") as f:
                return f.read()
        except OSError:
            return None

    def _atomic(self, key, write):
        dst = self._p(key)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        tmp = f"{dst}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            write(tmp)
            os.replace(tmp, dst)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def put_bytes(self, key, data, content_type="application/json"):
        def w(tmp):
            with open(tmp, "wb") as f:
                f.write(data)
        self._atomic(key, w)

    def put_file(self, key, path, content_type="application/octet-stream"):
        import shutil
        self._atomic(key, lambda tmp: shutil.copyfile(path, tmp))

    def get_file(self, key, dest):
        import shutil
        src = self._p(key)
        if not os.path.exists(src):
            return False
        shutil.copyfile(src, dest)
        return True

    def delete(self, key):
        try:
            os.remove(self._p(key))
        except OSError:
            pass

    def delete_many(self, keys):
        for k in keys:
            self.delete(k)

    def touch(self, key):
        try:
            os.utime(self._p(key), None)
        except OSError:
            pass


class R2Store:
    """The product bucket (R2 over the S3 API), with SHORT timeouts: a
    status read on an agent lane must cost milliseconds, never a turn."""
    name = "r2"

    def __init__(self, connect_timeout=3, read_timeout=20, attempts=2):
        self._opts = (connect_timeout, read_timeout, attempts)
        self._client = None
        self._lock = threading.Lock()

    def client(self):
        with self._lock:
            if self._client is None:
                import boto3
                from botocore.config import Config
                import config
                ct, rt, att = self._opts
                self._client = boto3.client(
                    "s3", endpoint_url=config.S3_ENDPOINT,
                    aws_access_key_id=config.S3_ACCESS_KEY_ID,
                    aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
                    region_name=config.S3_REGION,
                    config=Config(signature_version="s3v4",
                                  s3={"addressing_style": "path"},
                                  connect_timeout=ct, read_timeout=rt,
                                  retries={"max_attempts": att}))
            return self._client

    @property
    def bucket(self):
        import config
        return config.S3_BUCKET

    def list(self, prefix):
        out = {}
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            r = self.client().list_objects_v2(**kw)
            for o in r.get("Contents") or []:
                lm = o.get("LastModified")
                out[o["Key"]] = {"size": int(o.get("Size") or 0),
                                 "mtime": lm.timestamp() if lm else 0.0}
            if not r.get("IsTruncated"):
                return out
            token = r.get("NextContinuationToken")

    def get_bytes(self, key):
        try:
            return self.client().get_object(
                Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as e:  # noqa: BLE001
            if _missing(e):
                return None
            raise

    def put_bytes(self, key, data, content_type="application/json"):
        self.client().put_object(Bucket=self.bucket, Key=key, Body=data,
                                 ContentType=content_type)

    def put_file(self, key, path, content_type="application/octet-stream"):
        with open(path, "rb") as f:
            self.client().put_object(Bucket=self.bucket, Key=key, Body=f,
                                     ContentType=content_type)

    def get_file(self, key, dest):
        try:
            self.client().download_file(self.bucket, key, dest)
            return True
        except Exception as e:  # noqa: BLE001
            if _missing(e):
                return False
            raise

    def delete(self, key):
        try:
            self.client().delete_object(Bucket=self.bucket, Key=key)
        except Exception:  # noqa: BLE001 — a stale marker only costs a read
            pass

    def delete_many(self, keys):
        keys = list(keys)
        for i in range(0, len(keys), 1000):
            try:
                self.client().delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]],
                            "Quiet": True})
            except Exception:  # noqa: BLE001
                pass

    def touch(self, key):
        """Refresh LastModified (copy in place) for the lifecycle rule
        "90 days since last use". Best effort."""
        try:
            self.client().copy_object(
                Bucket=self.bucket, Key=key,
                CopySource={"Bucket": self.bucket, "Key": key},
                MetadataDirective="REPLACE")
        except Exception:  # noqa: BLE001
            pass


def _missing(exc):
    code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
    return code in ("NoSuchKey", "404", "NotFound") or "Not Found" in str(exc)


_STORE_OVERRIDE = None
_R2 = None
_STORE_LOCK = threading.Lock()


def use_store(store):
    """Install a store for this process (tests and tools); None restores the
    configured one, False disables the store. Returns the previous value."""
    global _STORE_OVERRIDE
    prev = _STORE_OVERRIDE
    _STORE_OVERRIDE = store
    _reset_caches()
    return prev


def _store():
    """The configured store: an override, VALMERA_SIDECAR_DIR, the product
    bucket, or None (then every sidecar reads as absent and nothing runs)."""
    global _R2
    if _STORE_OVERRIDE is not None:
        return _STORE_OVERRIDE or None
    root = os.getenv("VALMERA_SIDECAR_DIR", "").strip()
    if root:
        return LocalStore(root)
    try:
        import config
    except Exception:  # noqa: BLE001 — loaded outside the worker tree
        return None
    if not (config.S3_ENDPOINT and config.S3_BUCKET
            and config.S3_ACCESS_KEY_ID):
        return None
    with _STORE_LOCK:
        if _R2 is None:
            _R2 = R2Store()
        return _R2


def kind_dir(kind, source_sha):
    return f"{PREFIX}/{source_sha[:2]}/{source_sha}/{kind}/"


def data_key(kind, source_sha, key, ext=None):
    ext = ext or _KIND_SPECS[kind]["ext"]
    return f"{kind_dir(kind, source_sha)}{key}.{ext}"


def manifest_key(kind, source_sha, key):
    return f"{kind_dir(kind, source_sha)}{key}.json"


def pending_key(kind, source_sha, key):
    return f"{kind_dir(kind, source_sha)}{key}.pending.json"


def failed_key(kind, source_sha, key):
    return f"{kind_dir(kind, source_sha)}{key}.failed.json"


_NAME = re.compile(r"^([0-9a-f]{64})\.(.+)$")


def _classify(listing, prefix):
    """{chunk key: {"manifest"|"pending"|"failed"|"data": meta}}"""
    out = {}
    for full, meta in listing.items():
        if not full.startswith(prefix):
            continue
        m = _NAME.match(full[len(prefix):])
        if not m:
            continue
        key, suffix = m.groups()
        role = {"json": "manifest", "pending.json": "pending",
                "failed.json": "failed"}.get(suffix, "data")
        out.setdefault(key, {})[role] = meta
    return out


# ── per-process caches (manifests are immutable; markers are short-lived) ─

_READY = {}          # chunk key -> validated manifest
_LISTINGS = {}       # prefix -> (fetched_at, classified)
_MARKERS = {}        # marker object key -> (fetched_at, dict | None)
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 8192    # entries per cache: a long-lived lane re-reads, never grows


def _bounded(cache):
    if len(cache) > _CACHE_MAX:
        cache.clear()


def _reset_caches():
    with _CACHE_LOCK:
        _READY.clear()
        _LISTINGS.clear()
        _MARKERS.clear()


def _listing(store, kind, source_sha, fresh=False):
    prefix = kind_dir(kind, source_sha)
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _LISTINGS.get(prefix)
        if hit and not fresh and now - hit[0] < _LIST_TTL_S:
            return hit[1]
    got = _classify(store.list(prefix), prefix)
    with _CACHE_LOCK:
        _bounded(_LISTINGS)
        _LISTINGS[prefix] = (time.monotonic(), got)
    return got


def _forget_listing(kind, source_sha):
    with _CACHE_LOCK:
        _LISTINGS.pop(kind_dir(kind, source_sha), None)


def _read_json(store, key, ttl=_LIST_TTL_S):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _MARKERS.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    raw = store.get_bytes(key)
    try:
        val = json.loads(raw) if raw else None
    except ValueError:
        val = None
    with _CACHE_LOCK:
        _bounded(_MARKERS)
        _MARKERS[key] = (time.monotonic(), val)
    return val


def valid_manifest(m, kind, key, fallback=False):
    """The version handshake on the read side: a manifest is usable only if
    THIS code knows its kind, key, model and format. Anything else is
    absent, never ready (cache poisoning impossible by construction)."""
    if not isinstance(m, dict) or kind not in _KIND_SPECS:
        return False
    sp = spec(kind)
    if m.get("manifest_version") != MANIFEST_VERSION or m.get("key") != key \
            or m.get("kind") != kind or m.get("format") not in sp["formats"]:
        return False
    if fallback:
        return m.get("model_id") in sp["fallback_models"]
    return (m.get("model_id") == sp["model_id"]
            and str(m.get("model_version")) == sp["model_version"])


def _chunk_state(store, handle, chunk, listing, now):
    """(status, manifest | None, failed marker | None) for one chunk."""
    with _CACHE_LOCK:
        m = _READY.get(chunk.key)
    if m is not None:
        return "ready", m, None
    roles = listing.get(chunk.key) or {}
    if "manifest" in roles:
        m = _read_json(store, manifest_key(handle.kind, handle.source_sha,
                                           chunk.key), ttl=3600)
        if valid_manifest(m, handle.kind, chunk.key):
            with _CACHE_LOCK:
                _bounded(_READY)
                _READY[chunk.key] = m
            return "ready", m, None
    if _LAUNCHER is not None and _LAUNCHER.inflight(chunk.key):
        return "pending", None, None
    if "pending" in roles:
        p = _read_json(store, pending_key(handle.kind, handle.source_sha,
                                          chunk.key))
        if isinstance(p, dict) and float(p.get("lease_until") or 0) > time.time():
            return "pending", None, None
    if "failed" in roles:
        f = _read_json(store, failed_key(handle.kind, handle.source_sha,
                                         chunk.key))
        if isinstance(f, dict):
            terminal = (not f.get("retryable", True)
                        or int(f.get("attempts") or 0) >= MAX_ATTEMPTS)
            if terminal and float(f.get("retry_after") or 0) > now:
                return "failed", None, f
    return "absent", None, None


def _states(handle, fresh=False):
    """{chunk key: (status, manifest, failed marker)}; all absent without a
    store or for a kind this code does not produce."""
    store = _store()
    if store is None or handle.kind not in _KIND_SPECS or not handle.chunks:
        return {c.key: ("absent", None, None) for c in handle.chunks}
    try:
        listing = _listing(store, handle.kind, handle.source_sha, fresh)
    except Exception as e:  # noqa: BLE001 — unknown is not ready
        _log(f"list failed ({str(e)[:120]}); reading {handle.kind} as absent")
        return {c.key: ("absent", None, None) for c in handle.chunks}
    now = time.time()
    with _CACHE_LOCK:
        cold = [manifest_key(handle.kind, handle.source_sha, c.key)
                for c in handle.chunks
                if c.key not in _READY
                and "manifest" in (listing.get(c.key) or {})]
    if len(cold) > 1:
        # A fresh process reading a warm source: fetch the manifests side by
        # side so a repeat request stays well under a second.
        _parallel(lambda k: _read_json(store, k, ttl=3600), cold)
    out = {}
    for c in handle.chunks:
        try:
            out[c.key] = _chunk_state(store, handle, c, listing, now)
        except Exception as e:  # noqa: BLE001
            _log(f"chunk {c.key[:12]} unreadable ({str(e)[:120]})")
            out[c.key] = ("absent", None, None)
    return out


def _log(msg):
    print(f"[sidecars] {msg}", flush=True)


# ── the public API (signatures pinned in INTERFACES.md §2.3) ─────────────

def ensure(kind, source, ranges, priority="interactive", params=None):
    """Request a sidecar over SOURCE ranges; returns at once with a Handle.
    Launches the chunks nobody has (absent ones) in the background."""
    if priority not in PRIORITIES:
        raise ValueError(f"priority must be one of {PRIORITIES}")
    if kind not in KINDS and kind not in RESERVED_KINDS:
        raise ValueError(f"unknown sidecar kind {kind!r}; pinned: "
                         f"{KINDS + RESERVED_KINDS}")
    resolved = resolve_params(kind, params)
    spans = chunks_for(source.shots, source.duration_s, ranges)
    try:
        sp = spec(kind) if kind in _KIND_SPECS else None
    except (OSError, ValueError, KeyError) as e:
        _log(f"{kind}: model record unreadable ({e}); reading as absent")
        sp = None
    model_id, version = (sp["model_id"], sp["model_version"]) if sp \
        else ("unproduced", "0")
    chunks = tuple(ChunkRef(sidecar_key(kind, source.sha256, model_id,
                                        version, resolved, span), *span)
                   for span in spans)
    handle = Handle(kind=kind, source_sha=source.sha256, chunks=chunks,
                    priority=priority, params=canonical_params(resolved))
    if sp is None or not chunks:
        return handle
    try:
        if _store() is None:
            return handle
        states = _states(handle)
        todo = [c for c in chunks if states[c.key][0] == "absent"]
        if todo:
            _launcher().submit(handle, source, todo)
    except Exception as e:  # noqa: BLE001 — a write never fails on analysis
        _log(f"ensure {kind} failed to launch ({str(e)[:160]})")
    return handle


def chunk_status(handle):
    """{chunk key: status}. A degrade affects only its own window."""
    return {k: v[0] for k, v in _states(handle).items()}


def status(handle):
    """Aggregate: ready only when every chunk is; pending if any is; failed
    if any failed and none is pending; otherwise absent."""
    states = set(chunk_status(handle).values())
    if states and states == {"ready"}:
        return "ready"
    for state in ("pending", "failed"):
        if state in states:
            return state
    return "absent"


def wait(handles, budget_s):
    """Block up to ``budget_s`` (previews pass 0; finals at most 90) and
    return {chunk key: status}. With the ``sidecar_timeout`` fault the
    budget is spent at once — the same answer a real timeout gives."""
    try:
        budget = max(0.0, min(float(budget_s or 0.0), FINAL_WAIT_MAX_S))
    except (TypeError, ValueError):
        budget = 0.0
    try:
        import faultinject
        if faultinject.fault("sidecar_timeout"):
            budget = 0.0
    except ImportError:
        pass
    deadline = time.monotonic() + budget
    poll = 0.25
    while True:
        out = {}
        for handle in handles or ():
            out.update({k: v[0] for k, v in
                        _states(handle, fresh=budget > 0).items()})
        left = deadline - time.monotonic()
        if "pending" not in out.values() or left <= 0:
            return out
        time.sleep(min(poll, left))
        poll = min(poll * 1.5, 3.0)


def fetch(handle, dest_dir):
    """Ready chunks as local files: [{key, t0, t1, path, meta}], ordered by
    t0. Missing chunks are simply absent from the list (consumers treat
    them as black / inert). A chunk whose primary model failed but whose
    fallback model (e.g. u2net under RVM) produced data is returned with
    that manifest, so ``meta["model_id"]`` says which rung it is."""
    store = _store()
    if store is None or handle.kind not in _KIND_SPECS:
        return []
    out = []
    for c in handle.chunks:
        try:
            st, m, failed = _states_one(handle, c)
            if st != "ready":
                m = _fallback_manifest(store, handle, failed)
            if not m:
                continue
            path = _download(store, handle, c if st == "ready" else None, m,
                             dest_dir)
            if path:
                out.append({"key": c.key, "t0": c.t0, "t1": c.t1,
                            "path": path, "meta": m})
        except Exception as e:  # noqa: BLE001 — a missing chunk is inert
            _log(f"fetch {c.key[:12]} failed ({str(e)[:120]})")
    out.sort(key=lambda r: r["t0"])
    return out


def _states_one(handle, chunk):
    sub = Handle(kind=handle.kind, source_sha=handle.source_sha,
                 chunks=(chunk,), priority=handle.priority,
                 params=handle.params)
    return _states(sub)[chunk.key]


def _fallback_manifest(store, handle, failed):
    fb = (failed or {}).get("fallback") or {}
    key = fb.get("key")
    if not key:
        return None
    m = _read_json(store, manifest_key(handle.kind, handle.source_sha, key),
                   ttl=3600)
    return m if valid_manifest(m, handle.kind, key, fallback=True) else None


def _download(store, handle, chunk, m, dest_dir):
    key = m["key"]
    ext = m.get("ext") or _KIND_SPECS[handle.kind]["ext"]
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{key}.{ext}")
    want = int(m.get("bytes") or 0)
    if os.path.exists(path) and (not want or os.path.getsize(path) == want):
        return path
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.part"
    try:
        if not store.get_file(data_key(handle.kind, handle.source_sha, key,
                                       ext), tmp):
            return None
        if want and os.path.getsize(tmp) != want:
            _log(f"{key[:12]}: data is {os.path.getsize(tmp)} bytes, "
                 f"manifest says {want}; treating as absent")
            return None
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    try:
        if time.time() - float(m.get("created_at") or 0) > _TOUCH_AFTER_S:
            for k in (data_key(handle.kind, handle.source_sha, key, ext),
                      manifest_key(handle.kind, handle.source_sha, key)):
                store.touch(k)
    except Exception:  # noqa: BLE001
        pass
    return path


def estimate_s(handle):
    """Seconds until ready if launched now (the write's "measuring" note):
    compute on the batch lane's measured rate, spread over SIDECAR_SHARDS,
    plus admission/cold-start overhead. 0 when everything is ready."""
    if handle.kind not in _KIND_SPECS:
        return 0.0
    states = chunk_status(handle)
    todo = [c for c in handle.chunks if states.get(c.key) != "ready"]
    if not todo:
        return 0.0
    sp = _KIND_SPECS[handle.kind]
    try:
        params = json.loads(handle.params or "{}")
    except ValueError:
        params = {}
    fps = _rate(handle.kind, params)
    warm = float(params.get("warmup_s") or 0.0)
    groups = _group(todo, sp["group_s"])
    costs = sorted((sum((c.t1 - c.t0 + warm) * fps for c in g)
                    * sp["ms_per_frame"] / 1000.0 for g in groups),
                   reverse=True)
    shards = _shards()
    waves = [costs[i:i + shards] for i in range(0, len(costs), shards)]
    return round(sum(max(w) for w in waves) + _CALL_OVERHEAD_S, 1)


def summary(source_sha):
    """{kind: {"ready": n, "pending": n, "failed": n}} for project_state —
    one LIST, counted by marker presence (cheap; versions not re-read)."""
    store = _store()
    if store is None or not source_sha:
        return {}
    prefix = f"{PREFIX}/{source_sha[:2]}/{source_sha}/"
    try:
        listing = store.list(prefix)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for full in listing:
        rest = full[len(prefix):]
        if "/" not in rest:
            continue
        kind, name = rest.split("/", 1)
        m = _NAME.match(name)
        if not m:
            continue
        role = {"json": "ready", "pending.json": "pending",
                "failed.json": "failed"}.get(m.group(2))
        if role:
            row = out.setdefault(kind, {"ready": 0, "pending": 0,
                                        "failed": 0})
            row[role] += 1
    return out


# ── reading ready data without launching anything ───────────────────────

_JSON_DATA = {}
_JSON_DATA_MAX = 256


def ready_chunks(kind, source_sha, shots, duration_s, ranges, params=None):
    """[(ChunkRef, manifest)] for the READY chunks over ``ranges`` — a
    status check only (agent lanes never launch from a read)."""
    try:
        sp = spec(kind)
    except (KeyError, OSError, ValueError):
        return []
    resolved = resolve_params(kind, params)
    chunks = tuple(ChunkRef(sidecar_key(kind, source_sha, sp["model_id"],
                                        sp["model_version"], resolved, span),
                            *span)
                   for span in chunks_for(shots, duration_s, ranges))
    handle = Handle(kind=kind, source_sha=source_sha, chunks=chunks,
                    params=canonical_params(resolved))
    states = _states(handle)
    return [(c, states[c.key][1]) for c in chunks
            if states[c.key][0] == "ready"]


def read_json_chunk(kind, source_sha, manifest):
    """The decoded JSON data of a ready JSON-format chunk (cached)."""
    key = manifest.get("key")
    with _CACHE_LOCK:
        if key in _JSON_DATA:
            return _JSON_DATA[key]
    store = _store()
    if store is None:
        return None
    raw = store.get_bytes(data_key(kind, source_sha, key,
                                   manifest.get("ext")))
    if not raw or (manifest.get("bytes") and len(raw) != int(manifest["bytes"])):
        return None
    try:
        val = json.loads(raw)
    except ValueError:
        return None
    with _CACHE_LOCK:
        if len(_JSON_DATA) >= _JSON_DATA_MAX:
            _JSON_DATA.pop(next(iter(_JSON_DATA)))
        _JSON_DATA[key] = val
    return val


def faces_near(source_sha, shots, duration_s, src_t, near_s=1.0):
    """Face boxes ([x0, y0, x1, y1] fractions) of the ready faces.yunet
    sample nearest ``src_t`` within ``near_s``, as [(box, score)]; None when
    no ready sidecar covers the moment (unknown is not "no face")."""
    lo, hi = max(0.0, src_t - near_s), src_t + near_s
    best = None
    for _c, m in ready_chunks("faces.yunet", source_sha, shots, duration_s,
                              [(lo, hi)]):
        data = read_json_chunk("faces.yunet", source_sha, m) or {}
        for s in data.get("samples") or []:
            dt = abs(float(s.get("t", -1e9)) - src_t)
            if dt <= near_s and (best is None or dt < best[0]):
                best = (dt, s)
    if best is None:
        return None
    return [([float(v) for v in f[:4]], float(f[4]) if len(f) > 4 else 1.0)
            for f in best[1].get("faces") or [] if len(f) >= 4]


# ── prefetch policy (ROADMAP 0A: after index faces; after ranking mattes) ─

def prefetch_after_index(project_id, sha, proxy_key, proxy_info, shots,
                         duration=None):
    """After a successful index: faces over the whole source, at prefetch
    priority. Gated (config.SIDECAR_PREFETCH or config.YUNET_KEEPOUT); never
    raises; returns the Handle or None.

    ``duration`` is the INDEX's video duration: the chunk grid (and so every
    key) is defined on the index's duration and shot starts, which is what
    every later reader has (keepout.yunet_faces reads them off the index)."""
    try:
        import config
        if not (config.SIDECAR_PREFETCH or config.YUNET_KEEPOUT):
            return None
        info = dict(proxy_info or {})
        if duration:
            info["duration"] = duration
        src = source_from(sha, proxy_key, info, project_id, shots)
        if src.duration_s <= 0:
            return None
        return ensure("faces.yunet", src, [(0.0, src.duration_s)],
                      priority="prefetch")
    except Exception as e:  # noqa: BLE001 — an index never fails on this
        _log(f"index prefetch skipped ({str(e)[:160]})")
        return None


def prefetch_top_shorts(project_id, original, proxy, index, clips):
    """After make_shorts ranking: the person matte over the kept ranges of
    the top-ranked shorts (config.SIDECAR_PREFETCH_TOP_SHORTS). Gated by
    config.SIDECAR_PREFETCH; never raises; returns the Handle or None."""
    try:
        import config
        if not config.SIDECAR_PREFETCH or not proxy or not original:
            return None
        top = sorted(clips or [], key=lambda c: c.get("order", 1e9))[
            :config.SIDECAR_PREFETCH_TOP_SHORTS]
        ranges = [(float(c["start"]), float(c["end"])) for c in top
                  if float(c.get("end", 0)) > float(c.get("start", 0))]
        if not ranges:
            return None
        video = (index or {}).get("video") or {}
        # the grid is defined on the index's duration (see prefetch_after_index)
        info = {"duration": video.get("duration") or proxy.get("duration_s"),
                "fps": proxy.get("fps") or video.get("fps"),
                "width": proxy.get("width"), "height": proxy.get("height")}
        src = source_from(original.get("sha256"), proxy.get("storage_key"),
                          info, project_id, (index or {}).get("shots"))
        return ensure("matte.person", src, ranges, priority="prefetch")
    except Exception as e:  # noqa: BLE001
        _log(f"shorts prefetch skipped ({str(e)[:160]})")
        return None


# ── the launcher: background calls, one retry elsewhere ─────────────────

class SidecarUnavailable(RuntimeError):
    """No lane can run sidecar analysis from this process."""


def _shards():
    try:
        import config
        return max(1, int(config.SIDECAR_SHARDS))
    except Exception:  # noqa: BLE001
        return 3


def _group(chunks, group_s):
    """Consecutive chunks packed into calls of at most ``group_s`` seconds
    of footage (one per call for mattes, ~10 min for faces)."""
    groups, cur, size = [], [], 0.0
    for c in sorted(chunks, key=lambda c: c.t0):
        d = c.t1 - c.t0
        if cur and size + d > group_s + 1e-6:
            groups.append(cur)
            cur, size = [], 0.0
        cur.append(c)
        size += d
    if cur:
        groups.append(cur)
    return groups


def _default_execute(source, payload):
    """Run one group: the remote sidecar lane when configured, else this
    process (a single-box setup with no remote compute at all)."""
    import config
    import remote
    if remote.sidecar_available():
        return remote.run_sidecar_remote(source.project_id, payload)
    if not (config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED
            or (config.CLOUDFLARE_EXECUTOR_ENABLED
                and config.CLOUDFLARE_EXECUTOR_URL)):
        import analysis_jobs
        return analysis_jobs.run_sidecar_job(
            None, {"id": None, "type": "sidecar", "payload": payload})
    raise SidecarUnavailable("no lane here runs sidecar analysis")


def _not_run(exc):
    """A refusal proven to be before anything ran (busy shard, no
    capacity, a rollout): requeue without spending an attempt."""
    names = {c.__name__ for c in type(exc).__mro__}
    return bool(names & {"CloudflareLaunchUnavailable",
                         "CloudflareCapacityBusy",
                         "CloudflareCapacityUnavailable",
                         "CloudflareRolloutPending"})


class _Launcher:
    """Process-wide background launcher. Interactive work runs before
    prefetch, prefetch before backfill; at most SIDECAR_MAX_INFLIGHT calls
    run at once; each group gets a shard slot round-robin and a retry takes
    the next slot. ``execute(source, payload) -> result dict`` is the call.
    Nothing here blocks the caller: store writes happen on the worker."""

    MAX_REQUEUES = 6
    RETRY_SPLIT = 8

    def __init__(self, execute=None, workers=None, lease_s=None):
        self._execute = execute or _default_execute
        self._workers = workers
        self._lease_s = lease_s
        self._queue = []
        self._cv = threading.Condition()
        self._inflight = {}
        self._threads = []
        self._seq = itertools.count()
        self._slot = itertools.count()
        self.stats = {"calls": 0, "retries": 0, "requeues": 0, "failed": 0}

    # -- bookkeeping
    def inflight(self, key):
        with self._cv:
            return key in self._inflight

    def join(self, timeout=None):
        """Wait until nothing is queued or running (tests and tools)."""
        end = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while self._inflight or self._queue:
                left = None if end is None else end - time.monotonic()
                if left is not None and left <= 0:
                    return False
                self._cv.wait(timeout=0.2 if left is None else min(left, 0.2))
        return True

    def _n_workers(self):
        if self._workers:
            return self._workers
        try:
            import config
            return int(config.SIDECAR_MAX_INFLIGHT)
        except Exception:  # noqa: BLE001
            return 3

    def _lease(self):
        if self._lease_s:
            return float(self._lease_s)
        try:
            import config
            return float(config.executor_timeout_for("sidecar")) + 120.0
        except Exception:  # noqa: BLE001
            return 1020.0

    # -- submit (never touches the network)
    def submit(self, handle, source, chunks, attempt=1, slot=None, delay=0.0,
               requeues=0):
        sp = _KIND_SPECS[handle.kind]
        first = attempt == 1 and requeues == 0 and slot is None
        if first:
            groups = _group(chunks, sp["group_s"])
        elif attempt > 1:
            # A retry splits its group (at most RETRY_SPLIT calls), so one
            # chunk that kills its shard twice takes down only its window.
            groups = _split(list(chunks), self.RETRY_SPLIT)
        else:
            groups = [list(chunks)]
        shards = _shards()
        with self._cv:
            for i, g in enumerate(groups):
                if first:
                    g = [c for c in g if c.key not in self._inflight]
                if not g:
                    continue
                if slot is None:
                    s = next(self._slot) % shards
                else:
                    # spread split retries, never back onto the refused slot
                    s = (slot + i) % shards
                    if shards > 1 and s == (slot - 1) % shards:
                        s = (s + 1) % shards
                task = {"handle": handle, "source": source, "chunks": g,
                        "attempt": attempt, "requeues": requeues,
                        "slot": s,
                        "when": time.monotonic() + max(0.0, delay),
                        "rank": _PRIORITY_RANK[handle.priority],
                        "seq": next(self._seq)}
                for c in g:
                    self._inflight[c.key] = True
                self._queue.append(task)
            self._start()
            self._cv.notify_all()

    def _start(self):
        self._threads = [t for t in self._threads if t.is_alive()]
        for _ in range(self._n_workers() - len(self._threads)):
            t = threading.Thread(target=self._loop, name="sidecar-launcher",
                                 daemon=True)
            t.start()
            self._threads.append(t)

    def _next(self):
        """The best due task (priority, then age), or None; with the soonest
        not-yet-due time when none is due."""
        now = time.monotonic()
        due = [t for t in self._queue if t["when"] <= now]
        if due:
            task = min(due, key=lambda t: (t["rank"], t["seq"]))
            self._queue.remove(task)
            return task, None
        soonest = min((t["when"] for t in self._queue), default=None)
        return None, soonest

    def _loop(self):
        while True:
            with self._cv:
                while True:
                    task, soonest = self._next()
                    if task is not None:
                        break
                    if soonest is None:
                        self._cv.wait(timeout=30.0)
                        if not self._queue:
                            me = threading.current_thread()
                            if me in self._threads:
                                self._threads.remove(me)
                            return
                    else:
                        self._cv.wait(timeout=max(0.05, min(
                            1.0, soonest - time.monotonic())))
            try:
                self._run(task)
            except Exception as e:  # noqa: BLE001 — never kill the worker
                _log(f"launcher error ({str(e)[:160]})")
                self._release(task["chunks"])

    def _release(self, chunks):
        with self._cv:
            for c in chunks:
                self._inflight.pop(c.key, None)
            self._cv.notify_all()

    def _mark_pending(self, store, task):
        if store is None:
            return
        h = task["handle"]
        body = json.dumps({"lease_until": time.time() + self._lease(),
                           "attempt": task["attempt"], "lane": _lane_name(),
                           "started_at": time.time(), "priority": h.priority,
                           "slot": task["slot"]}).encode("utf-8")
        keys = [pending_key(h.kind, h.source_sha, c.key)
                for c in task["chunks"]]
        _parallel(lambda k: store.put_bytes(k, body), keys)

    # -- one call
    def _run(self, task):
        h, src = task["handle"], task["source"]
        sp = spec(h.kind)
        store = _store()
        self._mark_pending(store, task)
        payload = {
            "kind": h.kind, "source": _source_dict(src),
            "chunks": [{"key": c.key, "t0": c.t0, "t1": c.t1}
                       for c in task["chunks"]],
            "params": json.loads(h.params or "{}"),
            "model_id": sp["model_id"], "model_version": sp["model_version"],
            "format": sp["format"], "attempt": task["attempt"],
            "slot": task["slot"], "priority": h.priority,
        }
        self.stats["calls"] += 1
        try:
            result = self._execute(src, payload)
        except Exception as e:  # noqa: BLE001
            if _not_run(e) and task["requeues"] < self.MAX_REQUEUES:
                # Proven not to have run (busy shard, no capacity, rollout):
                # try again later, on the next slot, without an attempt.
                self.stats["requeues"] += 1
                self._release(task["chunks"])
                self.submit(h, src, task["chunks"], attempt=task["attempt"],
                            slot=task["slot"] + 1,
                            delay=min(60.0, 5.0 * 2 ** task["requeues"]),
                            requeues=task["requeues"] + 1)
                return
            if isinstance(e, SidecarUnavailable) or _not_run(e):
                # Nothing ran and nothing can from here: leave them absent.
                _log(f"{h.kind}: {str(e)[:160]}")
                _delete_many(store, [pending_key(h.kind, h.source_sha, c.key)
                                     for c in task["chunks"]])
                self._release(task["chunks"])
                _forget_listing(h.kind, h.source_sha)
                return
            # Anything else may have died mid-chunk (a killed shard, a lost
            # connection): what has no manifest is retried elsewhere once.
            result = {"ok": False, "why": str(e)[:300], "chunks": {
                c.key: {"status": "failed", "reason": "lost",
                        "retryable": True, "why": str(e)[:200]}
                for c in task["chunks"]}}
        self._settle(task, result if isinstance(result, dict) else {})

    def _settle(self, task, result):
        h, src = task["handle"], task["source"]
        store = _store()
        per = result.get("chunks") or {}
        retry, gave_up = [], []
        for c in task["chunks"]:
            r = per.get(c.key) or {"status": "failed", "reason": "no_result",
                                   "retryable": True}
            if r.get("status") == "ready":
                continue
            if r.get("retryable", True) and task["attempt"] < MAX_ATTEMPTS:
                retry.append(c)
            else:
                gave_up.append((c, r))
        for c, r in gave_up:
            self.stats["failed"] += 1
            if not r.get("marked"):
                _write_failed(store, h, c, r, task["attempt"])
        _delete_many(store, [pending_key(h.kind, h.source_sha, c.key)
                             for c, _r in gave_up])
        self._release(task["chunks"])
        _forget_listing(h.kind, h.source_sha)
        if retry:
            self.stats["retries"] += 1
            self.submit(h, src, retry, attempt=task["attempt"] + 1,
                        slot=task["slot"] + 1)


def _split(items, parts):
    """``items`` in at most ``parts`` contiguous, nearly equal groups."""
    n = max(1, min(int(parts), len(items)))
    size, extra = divmod(len(items), n)
    out, i = [], 0
    for k in range(n):
        j = i + size + (1 if k < extra else 0)
        if j > i:
            out.append(items[i:j])
        i = j
    return out


def _parallel(fn, items, workers=8):
    items = list(items)
    if not items:
        return
    if len(items) == 1:
        try:
            fn(items[0])
        except Exception as e:  # noqa: BLE001
            _log(f"store write failed ({str(e)[:100]})")
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as pool:
        for fut in [pool.submit(fn, i) for i in items]:
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001
                _log(f"store write failed ({str(e)[:100]})")


def _delete_many(store, keys):
    if store is None or not keys:
        return
    try:
        store.delete_many(keys)
    except Exception:  # noqa: BLE001 — a stale marker only costs a read
        pass


def _source_dict(src):
    d = asdict(src)
    d["shots"] = list(d.get("shots") or ())
    return d


def _lane_name():
    try:
        import config
        return "vision" if config.VISION_LANE_ENABLED else "batch"
    except Exception:  # noqa: BLE001
        return "batch"


def failed_marker(reason, attempts, retryable=True, why=None, fallback=None):
    import_ok = True
    try:
        import config
        retry_s = (config.SIDECAR_SKEW_RETRY_S if reason == "executor_skew"
                   else config.SIDECAR_FAILED_RETRY_S)
    except Exception:  # noqa: BLE001
        import_ok = False
        retry_s = 600.0 if reason == "executor_skew" else 21600.0
    body = {"reason": reason, "attempts": int(attempts),
            "retryable": bool(retryable), "at": time.time(),
            "retry_after": time.time() + float(retry_s)}
    if why:
        body["why"] = str(why)[:300]
    if fallback:
        body["fallback"] = fallback
    if not import_ok:
        body["note"] = "config unavailable"
    return body


def _write_failed(store, handle, chunk, r, attempt):
    if store is None:
        return
    try:
        store.put_bytes(
            failed_key(handle.kind, handle.source_sha, chunk.key),
            json.dumps(failed_marker(
                r.get("reason") or "failed", max(attempt, MAX_ATTEMPTS)
                if r.get("retryable", True) else attempt,
                retryable=r.get("retryable", True), why=r.get("why"),
                fallback=r.get("fallback"))).encode("utf-8"))
    except Exception as e:  # noqa: BLE001
        _log(f"failed marker not written ({str(e)[:100]})")


_LAUNCHER = None
_LAUNCHER_LOCK = threading.Lock()


def _launcher():
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        if _LAUNCHER is None:
            _LAUNCHER = _Launcher()
        return _LAUNCHER


def use_launcher(launcher):
    """Install a launcher (tests/tools); None resets to the default on next
    use. Returns the previous one."""
    global _LAUNCHER
    with _LAUNCHER_LOCK:
        prev, _LAUNCHER = _LAUNCHER, launcher
    return prev
