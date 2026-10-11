"""Content-addressed persistence for motion-graphic clips.

Wave 0 track 0A (storage); track 0B wires it into motion_engine's
``_cache_get`` / ``_cache_put`` (worker/devices/INTERFACES.md §2.7).

motion_engine keys every rendered clip by a content hash of its document,
runtime and capture settings, and keeps it in a container-local 3 GB LRU.
This is the shared tier behind it: a cold interactive container fetches the
clip instead of starting Chromium (AT-0A-2).

Contract: best effort, never raises, never blocks a render on the network
for long (short timeouts, one retry). ``get`` returns None on any miss or
error; ``put`` returns False on any error. The R2 objects are
``object_key(key)`` (the transparent .mov) and ``<object_key>.json`` (the
capture box meta motion_engine stores locally), written in that order: the
meta's presence means the clip is complete. Lifecycle: 90 days after last
use (a hit older than 30 days refreshes both objects; see
worker/cloudflare/r2-lifecycle.json).

Development and tests set VALMERA_CLIP_STORE_DIR (a local directory laid out
like the bucket); CLIP_STORE_ENABLED=0 switches the tier off.
"""

import json
import os
import re
import time
import uuid

PREFIX = "motion-clips"
_KEY = re.compile(r"^[0-9a-f]{16,128}$")
_TOUCH_AFTER_S = 30 * 86400
_STORE = None
_OVERRIDE = None


def object_key(key):
    return f"{PREFIX}/{key[:2]}/{key}.mov"


def meta_key(key):
    return object_key(key) + ".json"


def use_store(store):
    """Install a store (tests/tools); None restores the configured one and
    False disables the tier. Returns the previous value."""
    global _OVERRIDE
    prev, _OVERRIDE = _OVERRIDE, store
    return prev


def _store():
    global _STORE
    if _OVERRIDE is not None:
        return _OVERRIDE or None
    try:
        import config
        import sidecars
    except Exception:  # noqa: BLE001
        return None
    if not getattr(config, "CLIP_STORE_ENABLED", True):
        return None
    root = os.getenv("VALMERA_CLIP_STORE_DIR", "").strip() or \
        getattr(config, "CLIP_STORE_DIR", "")
    if root:
        return sidecars.LocalStore(root)
    if not (config.S3_ENDPOINT and config.S3_BUCKET and
            config.S3_ACCESS_KEY_ID):
        return None
    if _STORE is None:
        # A render waits on this: tighter than the sidecar reads.
        _STORE = sidecars.R2Store(connect_timeout=2, read_timeout=10,
                                  attempts=1)
    return _STORE


def _log(msg):
    print(f"[clip-store] {msg}", flush=True)


def get(key, dest_dir):
    """(local .mov path, meta dict) for a stored clip, or None."""
    try:
        if not isinstance(key, str) or not _KEY.match(key):
            return None
        store = _store()
        if store is None:
            return None
        raw = store.get_bytes(meta_key(key))
        if not raw:
            return None
        meta = json.loads(raw)
        if not isinstance(meta, dict) or meta.get("key") not in (None, key):
            return None
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{key}.mov")
        tmp = f"{path}.{uuid.uuid4().hex[:6]}.part"
        try:
            if not store.get_file(object_key(key), tmp):
                return None
            want = int(meta.get("bytes") or 0)
            if want and os.path.getsize(tmp) != want:
                _log(f"{key[:12]}: clip is {os.path.getsize(tmp)} bytes, "
                     f"meta says {want}; ignored")
                return None
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if time.time() - float(meta.get("stored_at") or 0) > _TOUCH_AFTER_S:
            _refresh(store, key, meta)
        clean = {k: v for k, v in meta.items()
                 if k not in ("key", "bytes", "stored_at", "store_v")}
        return path, clean
    except Exception as e:  # noqa: BLE001 — a cache never fails a render
        _log(f"get {str(key)[:12]} failed ({str(e)[:120]})")
        return None


def put(key, mov_path, meta):
    """Persist a rendered clip and its meta. True when stored."""
    try:
        if not isinstance(key, str) or not _KEY.match(key) \
                or not isinstance(meta, dict):
            return False
        store = _store()
        if store is None or not os.path.isfile(mov_path):
            return False
        size = os.path.getsize(mov_path)
        import config
        cap = int(getattr(config, "CLIP_STORE_MAX_BYTES", 256 << 20))
        if size <= 0 or size > cap:
            return False
        body = dict(meta)
        body.update(key=key, bytes=size, stored_at=time.time(), store_v=1)
        store.put_file(object_key(key), mov_path, "video/quicktime")
        store.put_bytes(meta_key(key), json.dumps(body).encode("utf-8"),
                        "application/json")
        return True
    except Exception as e:  # noqa: BLE001
        _log(f"put {str(key)[:12]} failed ({str(e)[:120]})")
        return False


def _refresh(store, key, meta):
    """Last use is the lifecycle clock: re-stamp both objects."""
    try:
        store.touch(object_key(key))
        body = dict(meta)
        body["stored_at"] = time.time()
        store.put_bytes(meta_key(key), json.dumps(body).encode("utf-8"),
                        "application/json")
    except Exception:  # noqa: BLE001
        pass
