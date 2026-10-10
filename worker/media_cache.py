"""Per-container on-disk LRU of immutable media the editor tools read.

look_at re-downloaded the ~180 MB 540p proxy on most calls (about 100 GB in
14 days, 18-23 s per call). The proxy lived in the MCP context's work dir, and
contexts are evicted constantly: a shard serves several projects (a parent's
shorts, each sharing the parent's proxy object) and kept only two contexts.

This cache is keyed by STORAGE KEY, independent of any context, so every child
short on a container shares one download and a re-opened context starts warm.
Only immutable objects belong here: proxies (``proxies/{project}/{sha}.mp4``)
and the source-audio sidecar (``audio/{project}/{sha}.wav``) are
content-addressed by construction.

Callers receive a hardlink inside their own work dir, never the cache path:
pruning may then reclaim a cache name while a job still reads the bytes (the
inode lives until the job's own cleanup), which is the renderer's source-cache
lease rule. The cache is never a reservation on scratch: an object is cached
only when TOOL_MEDIA_CACHE_MIN_FREE_BYTES stay free afterwards, the LRU evicts
to keep that floor, and any failure falls back to the caller's ordinary
download.
"""

import hashlib
import os
import threading
import time
import uuid

import config
import storage

_TTL_S = 6 * 3600
_LOCK = threading.Lock()
_KEY_LOCKS = {}


def cache_dir():
    return os.path.join(config.TMP_DIR, "toolcache")


def _key_lock(name):
    with _LOCK:
        lock = _KEY_LOCKS.get(name)
        if lock is None:
            lock = _KEY_LOCKS[name] = threading.Lock()
        return lock


def _entry_name(storage_key):
    ext = os.path.splitext(storage_key)[1].lower()[:8]
    return hashlib.sha256(storage_key.encode("utf-8")).hexdigest()[:32] + ext


def _entries(root, protect=None):
    """[(mtime, size, path)] of complete entries; drops stale/partial ones."""
    now = time.time()
    out = []
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if path == protect:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        partial_age = now - st.st_mtime
        if ".part" in name:
            # A live download keeps writing (fresh mtime); an abandoned one
            # has no reuse value.
            if partial_age > 3600:
                _unlink(path)
            continue
        if partial_age > _TTL_S or \
                st.st_size > config.TOOL_MEDIA_CACHE_MAX_ITEM_BYTES:
            _unlink(path)
            continue
        out.append((st.st_mtime, st.st_size, path))
    return out


def _unlink(path):
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def prune(need_bytes=0, protect=None):
    """Evict least-recently-used entries until the total fits the cap and
    ``need_bytes`` more can land while MIN_FREE scratch stays free."""
    root = cache_dir()
    entries = sorted(_entries(root, protect))
    total = sum(size for _m, size, _p in entries)
    if protect:
        try:
            total += os.path.getsize(protect)
        except OSError:
            pass
    free = storage.free_workdir_bytes(root)
    for _mtime, size, path in entries:
        over_cap = total + need_bytes > config.TOOL_MEDIA_CACHE_MAX_BYTES
        low_disk = (free is not None and
                    free - need_bytes < config.TOOL_MEDIA_CACHE_MIN_FREE_BYTES)
        if not over_cap and not low_disk:
            break
        if _unlink(path):
            total -= size
            if free is not None:
                free += size
    return total, free


def _lease(cached, dest_dir, name):
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, name)
    try:
        os.link(cached, target)
    except FileExistsError:
        if os.path.getsize(target) <= 0:
            return None
    return target


def resident(storage_key, dest_dir, name):
    """lease()'s hit path alone: a hardlink of an object this container
    already holds, or None — never a download (a caller that can range-read
    the object instead decides what a miss costs)."""
    if not storage_key:
        return None
    entry = os.path.join(cache_dir(), _entry_name(storage_key))
    try:
        with _key_lock(entry):
            if os.path.exists(entry) and os.path.getsize(entry) > 0:
                os.utime(entry, None)              # LRU touch
                return _lease(entry, dest_dir, name)
    except Exception:  # noqa: BLE001 — a cache never fails a tool
        return None
    return None


def lease(storage_key, dest_dir, name):
    """A job-owned hardlink of an immutable object at ``dest_dir/name``,
    from the cache (downloading it once on a miss), or None when the cache
    cannot serve it — the caller then downloads as before."""
    if not storage_key:
        return None
    root = cache_dir()
    entry = os.path.join(root, _entry_name(storage_key))
    try:
        os.makedirs(root, exist_ok=True)
        with _key_lock(entry):
            if os.path.exists(entry) and os.path.getsize(entry) > 0:
                os.utime(entry, None)              # LRU touch
                return _lease(entry, dest_dir, name)
            size = storage.object_bytes(storage_key)
            if not size or size > config.TOOL_MEDIA_CACHE_MAX_ITEM_BYTES \
                    or size > config.TOOL_MEDIA_CACHE_MAX_BYTES:
                return None
            _total, free = prune(need_bytes=size)
            if free is not None and \
                    free - size < config.TOOL_MEDIA_CACHE_MIN_FREE_BYTES:
                return None
            tmp = f"{entry}.part{uuid.uuid4().hex[:6]}"
            try:
                storage.download_to(storage_key, tmp)
                os.replace(tmp, entry)
            finally:
                if os.path.exists(tmp):
                    _unlink(tmp)
            prune(protect=entry)
            return _lease(entry, dest_dir, name)
    except Exception as exc:  # noqa: BLE001 — a cache never fails a tool
        print(f"[media-cache] {storage_key}: {str(exc)[:160]} — "
              "falling back to a direct download", flush=True)
        return None
