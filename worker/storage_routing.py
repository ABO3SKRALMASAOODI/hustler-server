"""Which object store serves each storage call (the R2 exit, Oct 2026).

THIS FILE EXISTS TWICE, byte for byte: backend/storage_routing.py and
worker/storage_routing.py (the web service and the executors are separate
images). backend/tests/test_storage_routing.py fails when they differ.

Media lives in ONE S3-compatible bucket today (Cloudflare R2). Moving it to
another provider (Backblaze B2, Contabo, MinIO, AWS: anything that speaks the
S3 API with SigV4 presigning, multipart and ranged GETs) must not stop a
single upload, preview or export, so the move runs as modes over two stores,
both configured purely by environment:

    legacy   S3_ENDPOINT, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_BUCKET,
             S3_REGION, S3_PUBLIC_ENDPOINT, S3_ADDRESSING_STYLE  (today: R2)
    next     the same names prefixed S3_NEXT_ (the replacement)

The MODE is the app_kv row ``storage_routing`` (JSON ``{"mode": ...}`` or the
bare word), read at most every STORAGE_ROUTING_TTL_S seconds and ONLY by a
process that has the next store configured. Without S3_NEXT_* nothing reads
the row and every call goes to the legacy store exactly as before: shipping
this changes nothing until both the keys and the row exist.

    mode        server writes    browser uploads   reads, in order   deletes
    legacy      legacy           legacy            legacy            legacy
    copy        legacy           legacy            next, legacy      both
    dual_write  legacy + next    legacy            next, legacy      both
    next        next             next              next, legacy      both
    rollback    legacy           legacy            legacy, next      both
    next_only   next             next              next              next

* ``copy``: the backlog copy runs (backend/storage_migrate.py, from the box).
  R2 still receives every write, so it stays complete and ``legacy`` is an
  instant, lossless rollback. Reads try the new store first, which proves its
  read path on real traffic while the copy fills it.
* ``dual_write``: server-side writes land in both stores (legacy first, the
  second is best effort and the copier's next pass repairs a miss), so the
  new store is complete except for browser uploads, which the copier's last
  incremental pass brings over before the switch.
* ``next``: THE SWITCH. New writes and browser uploads go to the new store;
  anything not found there is still read from R2.
* ``rollback``: back to R2 for writes while everything written to the new
  store stays readable. ``storage_migrate.py copy --reverse`` can then bring
  those objects home before ``legacy``.
* ``next_only``: R2 retired. (The final clean-up can instead move the S3_NEXT_*
  values into S3_* and delete the row: then ``legacy`` means the new store.)

Deletes always reach every store that may hold the key, so a deleted object
never comes back through a fallback read. A process that is told to run a
mode it cannot (``next``, ``rollback`` or ``next_only`` without S3_NEXT_*)
keeps using the legacy store and reports ``degraded`` on its health route;
the migration runbook checks every health route before the switch.
"""

import json
import os
import re
import threading
import time
from collections import namedtuple

LEGACY, NEXT = "legacy", "next"
STORES = (LEGACY, NEXT)
KV_KEY = "storage_routing"
DEFAULT_MODE = "legacy"
MODES = ("legacy", "copy", "dual_write", "next", "rollback", "next_only")

Plan = namedtuple("Plan", "mode write presign read delete")

_PLANS = {
    "legacy": Plan("legacy", (LEGACY,), LEGACY, (LEGACY,), (LEGACY,)),
    "copy": Plan("copy", (LEGACY,), LEGACY, (NEXT, LEGACY), (LEGACY, NEXT)),
    "dual_write": Plan("dual_write", (LEGACY, NEXT), LEGACY, (NEXT, LEGACY),
                       (LEGACY, NEXT)),
    "next": Plan("next", (NEXT,), NEXT, (NEXT, LEGACY), (NEXT, LEGACY)),
    "rollback": Plan("rollback", (LEGACY,), LEGACY, (LEGACY, NEXT),
                     (LEGACY, NEXT)),
    "next_only": Plan("next_only", (NEXT,), NEXT, (NEXT,), (NEXT,)),
}
# Modes a process without the next store may run as plain legacy without
# losing anything: R2 still receives every write in them.
LEGACY_SAFE = frozenset(("legacy", "copy", "dual_write"))

_PREFIX = {LEGACY: "S3_", NEXT: "S3_NEXT_"}


def plan_for(mode):
    return _PLANS.get(mode) or _PLANS[DEFAULT_MODE]


def parse_mode(raw):
    """The mode named by an app_kv value, or None when it names none."""
    if raw is None:
        return None
    text = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
    text = text.strip()
    if not text:
        return None
    if text.startswith("{"):
        try:
            text = str((json.loads(text) or {}).get("mode") or "")
        except (TypeError, ValueError, AttributeError):
            return None
    text = text.strip().strip('"').lower()
    return text if text in MODES else None


# Endpoint host -> SigV4 region, for providers whose signature must name the
# region the endpoint serves. R2 signs with "auto"; Backblaze, AWS and Wasabi
# reject a presigned URL scoped to the wrong region.
_REGION_FROM_HOST = (
    re.compile(r"^s3\.([a-z0-9-]+)\.backblazeb2\.com$"),
    re.compile(r"^s3[.-]([a-z0-9-]+)\.amazonaws\.com$"),
    re.compile(r"^s3\.([a-z0-9-]+)\.wasabisys\.com$"),
    re.compile(r"^([a-z0-9-]+)\.digitaloceanspaces\.com$"),
)


def region_for(endpoint, configured=None):
    """The signing region: the configured one unless it is empty or "auto"
    and the endpoint names its region; otherwise "auto" (R2's, and what
    every deployment used before this module)."""
    configured = (configured or "").strip()
    if configured and configured.lower() != "auto":
        return configured
    host = re.sub(r"^[a-z]+://", "", (endpoint or "").strip().lower())
    host = host.split("/", 1)[0].split(":", 1)[0]
    for pattern in _REGION_FROM_HOST:
        m = pattern.match(host)
        if m and m.group(1) not in ("external-1",):
            return m.group(1)
    return configured or "auto"


def store_config(name, env=None):
    """{endpoint, public_endpoint, access_key_id, secret_access_key, bucket,
    region, addressing_style, host} for one store, or None when any of the
    four required values is missing."""
    env = os.environ if env is None else env
    p = _PREFIX[name]
    endpoint = (env.get(p + "ENDPOINT") or "").strip()
    key_id = (env.get(p + "ACCESS_KEY_ID") or "").strip()
    secret = (env.get(p + "SECRET_ACCESS_KEY") or "").strip()
    bucket = (env.get(p + "BUCKET") or "").strip()
    if not (endpoint and key_id and secret and bucket):
        return None
    style = (env.get(p + "ADDRESSING_STYLE") or "path").strip().lower()
    if style not in ("path", "virtual", "auto"):
        style = "path"
    host = re.sub(r"^[a-z]+://", "", endpoint.lower()).split("/", 1)[0]
    return {
        "name": name,
        "endpoint": endpoint,
        "public_endpoint": (env.get(p + "PUBLIC_ENDPOINT") or "").strip()
        or endpoint,
        "access_key_id": key_id,
        "secret_access_key": secret,
        "bucket": bucket,
        "region": region_for(endpoint, env.get(p + "REGION")),
        "addressing_style": style,
        "host": host,
    }


def botocore_config_kwargs(cfg, **extra):
    """Keyword arguments for botocore.config.Config for one store.

    botocore 1.36 started sending CRC32 checksum headers on every upload by
    default, which several S3-compatible providers rejected at the time
    ("Unsupported header x-amz-sdk-checksum-algorithm"). Pinned 1.35 never
    sends them; if the pin moves, "when_required" keeps the old wire format
    for every provider."""
    kw = {"signature_version": "s3v4",
          "s3": {"addressing_style": (cfg or {}).get("addressing_style")
                 or "path"}}
    kw.update(extra)
    try:
        from botocore.config import Config
        defaults = getattr(Config, "OPTION_DEFAULTS", {}) or {}
        if "request_checksum_calculation" in defaults:
            kw.setdefault("request_checksum_calculation", "when_required")
        if "response_checksum_validation" in defaults:
            kw.setdefault("response_checksum_validation", "when_required")
    except Exception:  # noqa: BLE001 — botocore absent: plain kwargs
        pass
    return kw


class Router:
    """The mode, cached per process. ``fetch()`` returns the raw app_kv value
    (or None when the row is absent) and may raise; the last mode that was
    read successfully survives a failed read, so a database blip never flips
    a running process between stores."""

    def __init__(self, fetch=None, env=None, ttl_s=None, clock=None,
                 log=None):
        self._fetch = fetch
        self._env = os.environ if env is None else env
        self._ttl = float(ttl_s if ttl_s is not None else
                          self._env.get("STORAGE_ROUTING_TTL_S") or 30)
        self._clock = clock or time.monotonic
        self._log = log or (lambda msg: print(f"[storage] {msg}", flush=True))
        self._lock = threading.Lock()
        self._mode = None           # last successfully read mode
        self._read_at = None
        self._error = None
        self._warned = set()

    # -- configuration ------------------------------------------------------
    def config(self, name):
        return store_config(name, self._env)

    def next_configured(self):
        return self.config(NEXT) is not None

    def default_mode(self):
        return parse_mode(self._env.get("STORAGE_ROUTING_MODE")) \
            or DEFAULT_MODE

    # -- mode ---------------------------------------------------------------
    def requested_mode(self):
        """The mode the row asks for (fetching when stale), or the default."""
        if not self.next_configured() or self._fetch is None:
            return self._mode or self.default_mode()
        now = self._clock()
        stale = self._read_at is None or now - self._read_at >= self._ttl
        if stale and self._lock.acquire(blocking=self._mode is None):
            try:
                if self._read_at is None or \
                        self._clock() - self._read_at >= self._ttl:
                    self._refresh()
            finally:
                self._lock.release()
        return self._mode or self.default_mode()

    def _refresh(self):
        try:
            raw = self._fetch()
        except Exception as exc:  # noqa: BLE001 — keep the last good mode
            self._error = f"{type(exc).__name__}: {str(exc)[:160]}"
            self._read_at = self._clock() - self._ttl + min(self._ttl, 5.0)
            if "fetch" not in self._warned:
                self._warned.add("fetch")
                self._log(f"routing row unreadable, keeping "
                          f"{self._mode or self.default_mode()}: {self._error}")
            return
        self._warned.discard("fetch")
        self._error = None
        self._read_at = self._clock()
        mode = parse_mode(raw)
        if raw is not None and mode is None:
            self._log(f"routing row names no mode ({str(raw)[:60]!r}); "
                      f"keeping {self._mode or self.default_mode()}")
            return
        mode = mode or self.default_mode()
        if mode != self._mode:
            if self._mode is not None:
                self._log(f"routing mode {self._mode} -> {mode}")
            self._mode = mode
        return

    def mode(self):
        """The mode this process actually runs."""
        want = self.requested_mode()
        if want == "legacy":
            return want
        if not self.next_configured():
            if want not in LEGACY_SAFE and "degraded" not in self._warned:
                self._warned.add("degraded")
                self._log(f"mode {want} needs S3_NEXT_*; this process has "
                          f"none and keeps using the legacy store")
            return "legacy"
        if self.config(LEGACY) is None and want != "next_only":
            return "next_only"
        return want

    def plan(self):
        return plan_for(self.mode())

    def status(self):
        """For health routes: never a secret, never raises."""
        try:
            want = self.requested_mode()
            mode = self.mode()
            nxt = self.config(NEXT)
            leg = self.config(LEGACY)
            out = {"mode": mode, "requested": want,
                   "degraded": mode != want,
                   "legacy": bool(leg), "next": bool(nxt),
                   "legacy_host": leg["host"] if leg else None,
                   "next_host": nxt["host"] if nxt else None}
            if self._error:
                out["error"] = self._error
            return out
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}


class Locator:
    """key -> the store that holds it, for presigned GETs that must name
    one store. Positive answers are cached (objects do not move out of a
    store; a deleted one 404s from either), misses are not."""

    def __init__(self, ttl_s=600.0, max_entries=20000, clock=None):
        self._ttl = float(ttl_s)
        self._max = int(max_entries)
        self._clock = clock or time.monotonic
        self._hits = {}
        self._lock = threading.Lock()

    def get(self, mode, key):
        hit = self._hits.get((mode, key))
        if hit and hit[1] > self._clock():
            return hit[0]
        return None

    def put(self, mode, key, store):
        with self._lock:
            if len(self._hits) >= self._max:
                self._hits.clear()
            self._hits[(mode, key)] = (store, self._clock() + self._ttl)

    def forget(self, key):
        with self._lock:
            for k in [k for k in self._hits if k[1] == key]:
                self._hits.pop(k, None)

    def clear(self):
        with self._lock:
            self._hits.clear()


def is_missing(exc):
    """True when a botocore error means "no such object/upload here"."""
    try:
        code = str(exc.response["Error"]["Code"])
    except (AttributeError, KeyError, TypeError):
        code = ""
    return code in ("NoSuchKey", "404", "NotFound", "NoSuchUpload") \
        or "Not Found" in str(exc)
