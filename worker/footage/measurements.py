"""Content-addressed, run-local measurements of a retained original."""
import hashlib
import os
from functools import lru_cache
from pathlib import Path
import shutil


def stamp(path):
    st = os.stat(path)
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


@lru_cache(maxsize=128)
def _digest(path, stamp):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def record(proxy, family, settings, ffmpeg):
    keep = os.environ.get("FOOTAGE_KEEP_SOURCES")
    if not keep:
        return None
    digest = _digest(proxy, stamp(proxy))
    ff = shutil.which(ffmpeg)
    version = hashlib.sha256(repr((settings, stamp(ff) if ff else None)).encode()).hexdigest()[:16]
    return Path(keep) / family / version / (digest + ".json")
