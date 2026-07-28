"""Worker-side object storage: stream downloads to disk, upload artifacts.
Media bytes only ever live on the worker's ephemeral disk during a job."""

import os
import shutil

import boto3
from botocore.config import Config

import config


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


def object_bytes(key):
    """Size of one object, or None if it cannot be read."""
    try:
        return int(client().head_object(
            Bucket=config.S3_BUCKET, Key=key)["ContentLength"])
    except Exception:
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


def download_to(key, path, check_capacity=True):
    """Stream one object to local scratch, refusing up front if it cannot fit."""
    if check_capacity:
        check_workdir_capacity(object_bytes(key), os.path.dirname(path) or None)
    client().download_file(config.S3_BUCKET, key, path)


def upload_file(path, key, content_type):
    client().upload_file(path, config.S3_BUCKET, key,
                         ExtraArgs={"ContentType": content_type})


def presign_get(key, expires=3600):
    """A temporary public GET URL for one object — used to hand an asset to an
    external generation provider (e.g. fal.ai image-to-video) that must fetch
    it directly. Short-lived by default; the provider downloads it immediately."""
    return client().generate_presigned_url(
        "get_object",
        Params={"Bucket": config.S3_BUCKET, "Key": key},
        ExpiresIn=int(expires))


def copy_object(src_key, dst_key):
    client().copy_object(Bucket=config.S3_BUCKET,
                         CopySource={"Bucket": config.S3_BUCKET, "Key": src_key},
                         Key=dst_key)


def exists(key):
    try:
        client().head_object(Bucket=config.S3_BUCKET, Key=key)
        return True
    except Exception:
        return False


def delete_keys(keys):
    """Best-effort delete. Returns how many were requested; never raises —
    callers use this to reclaim superseded artifacts, and failing to free
    space must never fail the job that produced the new one."""
    keys = [k for k in (keys or []) if k]
    if not keys:
        return 0
    try:
        client().delete_objects(
            Bucket=config.S3_BUCKET,
            Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
    except Exception:
        return 0
    return len(keys)
