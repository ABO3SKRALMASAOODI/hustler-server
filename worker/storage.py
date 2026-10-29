"""Worker-side object storage: stream downloads to disk, upload artifacts.
Media bytes only ever live on the worker's ephemeral disk during a job."""

import hashlib
import os
import shutil
import threading

import boto3
from botocore.config import Config
from boto3.s3.transfer import TransferConfig

import config
import io_telemetry
import storage_routing as routing


TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=config.STORAGE_MULTIPART_THRESHOLD_MB * 1024 * 1024,
    multipart_chunksize=config.STORAGE_MULTIPART_CHUNK_MB * 1024 * 1024,
    max_concurrency=config.STORAGE_MULTIPART_CONCURRENCY,
    use_threads=config.STORAGE_MULTIPART_CONCURRENCY > 1,
)


def client():
    return boto3.client(
        "s3",
        endpoint_url=config.S3_ENDPOINT,
        aws_access_key_id=config.S3_ACCESS_KEY_ID,
        aws_secret_access_key=config.S3_SECRET_ACCESS_KEY,
        region_name=config.S3_REGION,
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": "path"},
                      retries={"max_attempts": 3}),
    )


# ── Which store (storage_routing.py: the provider-neutral move off R2) ─────
#
# client()/config.S3_BUCKET are the legacy store and stay exactly as they
# were. Every function below asks the router which store(s) an operation
# uses; in the default "legacy" mode, and in any process without S3_NEXT_*,
# that is the legacy store alone and the router never reads the database.

def _fetch_routing_row():
    import db
    conn = db.connect()
    try:
        return db.kv_get(conn, routing.KV_KEY)
    finally:
        try:
            conn.rollback()
            conn.close()
        except Exception:  # noqa: BLE001
            pass


_router = routing.Router(fetch=_fetch_routing_row)
_locator = routing.Locator()
_next_lock = threading.Lock()
_next_cached = {}


def router():
    return _router


def routing_status():
    return _router.status()


def _next_client():
    cfg = routing.store_config(routing.NEXT)
    if cfg is None:
        raise RuntimeError("the next store (S3_NEXT_*) is not configured")
    key = (cfg["endpoint"], cfg["access_key_id"],
           hashlib.sha256(cfg["secret_access_key"].encode()).hexdigest(),
           cfg["region"], cfg["addressing_style"], boto3.client)
    with _next_lock:
        c = _next_cached.get(key)
        if c is None:
            c = boto3.client(
                "s3", endpoint_url=cfg["endpoint"],
                aws_access_key_id=cfg["access_key_id"],
                aws_secret_access_key=cfg["secret_access_key"],
                region_name=cfg["region"],
                config=Config(**routing.botocore_config_kwargs(
                    cfg, retries={"max_attempts": 3})))
            _next_cached.clear()
            _next_cached[key] = c
        return c


def store_client(name):
    return _next_client() if name == routing.NEXT else client()


def store_bucket(name):
    if name == routing.NEXT:
        return routing.store_config(routing.NEXT)["bucket"]
    return config.S3_BUCKET


def _plan():
    return _router.plan()


def _log(msg):
    print(f"[storage] {msg}", flush=True)


def _head(name, key):
    """ContentLength of ``key`` in store ``name``, or None."""
    try:
        return int(store_client(name).head_object(
            Bucket=store_bucket(name), Key=key)["ContentLength"])
    except Exception:
        return None


def locate(key):
    """The read store that holds ``key``, or the first read store when none
    answers (the caller then fails exactly as it always did)."""
    plan = _plan()
    if len(plan.read) == 1:
        return plan.read[0]
    hit = _locator.get(plan.mode, key)
    if hit:
        return hit
    for name in plan.read:
        if _head(name, key) is not None:
            _locator.put(plan.mode, key, name)
            return name
    return plan.read[0]


def object_bytes(key):
    """Size of one object, or None if it cannot be read (in any read store)."""
    for name in _plan().read:
        n = _head(name, key)
        if n is not None:
            return n
    return None


def workdir_fstype(path=None):
    """Filesystem backing the workdir ('tmpfs', 'overlay', 'ext4', ...).

    This decides whether staging a file costs RAM or disk, and it is not a
    detail: the deploy doc assumed Cloud Run's /tmp is always an in-memory
    tmpfs, but the live executor reports a **755 GB** filesystem there, which
    no 32 GiB instance could back with memory. Guessing either way is wrong in
    one of the two worlds — on tmpfs, statvfs is a memory budget; on a real
    disk, bounding by memory would refuse work that fits fine.
    """
    target = os.path.realpath(path or config.TMP_DIR)
    best, best_type = "", None
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount, fstype = parts[1], parts[2]
                # Longest matching mount point wins — "/" matches everything.
                if (target == mount or target.startswith(mount.rstrip("/") + "/")) \
                        and len(mount) >= len(best):
                    best, best_type = mount, fstype
    except Exception:
        return None
    return best_type


def _cgroup_memory_available():
    """Bytes of RAM this container may still use, or None if not in a cgroup."""
    for limit_p, usage_p in (
            ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
            ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
             "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
        try:
            with open(limit_p) as f:
                raw = f.read().strip()
            if raw == "max":
                continue
            limit = int(raw)
            # cgroup v1 writes a huge sentinel for "unlimited".
            if limit <= 0 or limit >= 1 << 50:
                continue
            with open(usage_p) as f:
                used = int(f.read().strip())
            return max(0, limit - used)
        except Exception:
            continue
    return None


def free_workdir_bytes(path=None):
    """Bytes a job may actually stage in the workdir.

    On a MEMORY-BACKED workdir (tmpfs) the real ceiling is the container's
    memory limit, not what statvfs advertises — and it is stricter, because
    ffmpeg's own buffers and any resident model are spending the same budget.
    On a real disk, statvfs is the truth and memory is irrelevant.
    """
    try:
        free = shutil.disk_usage(path or config.TMP_DIR).free
    except Exception:
        free = None
    if workdir_fstype(path) != "tmpfs":
        return free
    mem = _cgroup_memory_available()
    if mem is None:
        return free
    return mem if free is None else min(free, mem)


class WorkdirTooSmall(RuntimeError):
    """Raised INSTEAD of being OOM-killed. See check_workdir_capacity."""


def check_workdir_capacity(need_bytes, path=None, headroom=None):
    """Refuse a job we cannot physically stage, with a message that names the fix.

    ON CLOUD RUN `/tmp` IS AN IN-MEMORY FILESYSTEM: every byte a job writes
    there is RAM, counted against the instance's `--memory`. Exceeding it does
    not raise — the kernel kills the container, the request dies with no body,
    and the dispatcher records "Worker died and retries are exhausted". That is
    indistinguishable from a crash, so the one failure that is trivially fixable
    by an operator (raise --memory, or mount a volume for WORKER_TMP_DIR) has
    been arriving as the least actionable error we produce.

    So: measure first, and if it will not fit, say exactly that. `headroom`
    covers the artifacts written BESIDE the source — a proxy, a wav, the render
    output — which are small next to the original but not nothing.
    """
    headroom = config.WORKDIR_HEADROOM if headroom is None else headroom
    if not need_bytes:
        return
    free = free_workdir_bytes(path)
    if free is None:
        return                      # cannot measure: never block on a guess
    need = int(need_bytes * headroom)
    if free >= need:
        return
    gb = 1024 ** 3
    raise WorkdirTooSmall(
        f"this job needs about {need / gb:.1f} GB of scratch space in "
        f"{path or config.TMP_DIR} and only {free / gb:.1f} GB is free. On "
        f"Cloud Run that space is RAM: raise the executor's --memory (32Gi is "
        f"the maximum) or mount a volume for WORKER_TMP_DIR. See "
        f"worker/DEPLOY_EXECUTOR.md.")


def evict_source_cache(cache_dir=None):
    """Drop the executor's optional render-source cache; bytes released.

    renderer._cached_source keeps recent sources in TMP_DIR/srccache (up to
    SOURCE_CACHE_MAX_BYTES) on the same disk an index stages into. That cache
    is an optimisation and never a reservation, so a job that is short of
    scratch reclaims it before refusing. A render that is using a cached
    source holds its own hardlink, so unlinking the cache name cannot pull
    an input from under it (those bytes are not counted as released), and
    in-progress downloads (".part") are left alone.
    """
    cache_dir = cache_dir or os.path.join(config.TMP_DIR, "srccache")
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return 0
    released = 0
    for name in names:
        if ".part" in name:
            continue
        path = os.path.join(cache_dir, name)
        try:
            st = os.stat(path)
            if not os.path.isfile(path):
                continue
            os.remove(path)
        except OSError:
            continue
        if st.st_nlink <= 1:
            released += st.st_size
    return released


def download_to(key, path, check_capacity=True):
    """Stream one object to local scratch, refusing up front if it cannot fit."""
    name = locate(key)
    if check_capacity:
        check_workdir_capacity(_head(name, key) if len(_plan().read) > 1
                               else object_bytes(key),
                               os.path.dirname(path) or None)
    store_client(name).download_file(store_bucket(name), key, path,
                                     Config=TRANSFER_CONFIG)
    try:
        io_telemetry.add_downloaded(os.path.getsize(path))
    except OSError:
        pass


def upload_file(path, key, content_type):
    """Into every write store of the routing mode; the first must succeed,
    a second (dual_write) is best effort: the copier's next pass repairs a
    miss."""
    for i, name in enumerate(_plan().write):
        try:
            store_client(name).upload_file(
                path, store_bucket(name), key,
                ExtraArgs={"ContentType": content_type},
                Config=TRANSFER_CONFIG)
        except Exception as exc:  # noqa: BLE001
            if i == 0:
                raise
            _log(f"secondary upload to {name} failed for {key}: {exc}")
            continue
        if i == 0:
            try:
                io_telemetry.add_uploaded(os.path.getsize(path))
            except OSError:
                pass


def sha256_object(key, expected_bytes, progress_cb=None, cancelled_cb=None):
    """Hash a complete immutable upload without staging it on local disk."""
    if cancelled_cb and cancelled_cb():
        raise RuntimeError("job was cancelled or handed to another worker")
    name = locate(key)
    response = store_client(name).get_object(Bucket=store_bucket(name),
                                             Key=key)
    body = response["Body"]
    total, digest = 0, hashlib.sha256()
    try:
        if int(response["ContentLength"]) != int(expected_bytes):
            raise RuntimeError("Stored upload size differs from the completed upload")
        for chunk in body.iter_chunks(chunk_size=8 * 1024 * 1024):
            if cancelled_cb and cancelled_cb():
                raise RuntimeError("job was cancelled or handed to another worker")
            total += len(chunk)
            if total > expected_bytes:
                raise RuntimeError("Stored upload grew while being read")
            digest.update(chunk)
            io_telemetry.add_downloaded(len(chunk))
            if progress_cb:
                progress_cb(total / expected_bytes)
        if total != expected_bytes:
            raise RuntimeError("Stored upload read ended before all bytes arrived")
        return digest.hexdigest()
    finally:
        body.close()


def presign_get(key, expires=3600):
    """A temporary public GET URL for one object — used to hand an asset to an
    external generation provider (e.g. fal.ai image-to-video) that must fetch
    it directly, and to let ffmpeg range-read a long source. Short-lived by
    default; the provider downloads it immediately. Signed by the store that
    holds the object."""
    name = locate(key)
    return store_client(name).generate_presigned_url(
        "get_object",
        Params={"Bucket": store_bucket(name), "Key": key},
        ExpiresIn=int(expires))


def copy_object(src_key, dst_key):
    """Server-side copy, any size. boto3's managed copy stays a single
    CopyObject for small objects and switches itself to multipart
    (UploadPartCopy) past its threshold — a plain CopyObject caps at 5 GB,
    and the dedup path (round 67d) copies originals up to the 16 GB upload
    limit without the bytes ever leaving the bucket.

    Across stores (storage_routing): each write store copies server-side
    when it holds the source. When the PRIMARY write store does not (the
    source predates a switch and the backlog copy has not reached it), the
    bytes stream from the store that has it, part by part, never staged on
    disk. A secondary write store without the source is skipped: the
    copier's next pass brings the destination over."""
    plan = _plan()
    for i, name in enumerate(plan.write):
        c, b = store_client(name), store_bucket(name)
        if len(plan.write) == 1 and len(plan.read) == 1:
            c.copy({"Bucket": b, "Key": src_key}, b, dst_key,
                   Config=TRANSFER_CONFIG)
            return
        if _head(name, src_key) is not None:
            try:
                c.copy({"Bucket": b, "Key": src_key}, b, dst_key,
                       Config=TRANSFER_CONFIG)
            except Exception as exc:  # noqa: BLE001
                if i == 0:
                    raise
                _log(f"secondary copy in {name} failed for {dst_key}: {exc}")
            continue
        if i > 0:
            continue
        src_store = next((s for s in plan.read
                          if s != name and _head(s, src_key) is not None),
                         None)
        if src_store is None:
            # Nowhere else either: the error the copy itself raises.
            c.copy({"Bucket": b, "Key": src_key}, b, dst_key,
                   Config=TRANSFER_CONFIG)
            return
        _stream_copy(src_store, src_key, name, dst_key)


def _stream_copy(src_store, src_key, dst_store, dst_key):
    """One object from one store into another, through memory only."""
    obj = store_client(src_store).get_object(Bucket=store_bucket(src_store),
                                             Key=src_key)
    extra = {"ContentType": obj.get("ContentType")
             or "application/octet-stream"}
    try:
        store_client(dst_store).upload_fileobj(
            obj["Body"], store_bucket(dst_store), dst_key,
            ExtraArgs=extra, Config=TRANSFER_CONFIG)
    finally:
        try:
            obj["Body"].close()
        except Exception:  # noqa: BLE001
            pass


def exists(key):
    for name in _plan().read:
        try:
            store_client(name).head_object(Bucket=store_bucket(name), Key=key)
            return True
        except Exception:
            continue
    return False


def delete_keys(keys):
    """Best-effort delete. Returns how many were requested; never raises —
    callers use this to reclaim superseded artifacts, and failing to free
    space must never fail the job that produced the new one. Reaches every
    store the key may live in, so a fallback read cannot resurrect it."""
    keys = [k for k in (keys or []) if k]
    if not keys:
        return 0
    ok = False
    for name in _plan().delete:
        try:
            store_client(name).delete_objects(
                Bucket=store_bucket(name),
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
            ok = True
        except Exception:
            continue
    for k in keys:
        _locator.forget(k)
    return len(keys) if ok else 0
