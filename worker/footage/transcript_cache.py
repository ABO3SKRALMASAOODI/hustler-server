"""Content-addressed speech results, shared across filenames and concurrent runs.

The Mac root is already capped by ops-loop/tools/janitor.sh. Other hosts use
their worker cache; prune here too so standalone box use stays bounded.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time


def root():
    override = os.environ.get("FOOTAGE_TRANSCRIPT_CACHE")
    base = Path(override) if override else (
        Path.home() / "Library/Caches/valmera/transcripts" if sys.platform == "darwin"
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "valmera/transcripts")
    base.mkdir(parents=True, exist_ok=True)
    return base


def digest(path, settings):
    h = hashlib.sha256(json.dumps(settings, sort_keys=True, separators=(",", ":")).encode())
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@contextlib.contextmanager
def locked(key, namespace="piece"):
    # Bounded striped locks, never unlinked while a process might hold one.
    p = root()
    with (p / (namespace + "-" + key[:2] + ".lock")).open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield p / (key + ".json")


def read(path):
    try:
        obj = json.loads(path.read_text())
        if obj.get("version") == 1 and isinstance(obj.get("rows"), list):
            return obj["rows"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def write(path, rows):
    # Failures are never memoized. An empty successful transcript (silence) is.
    if any(r.get("error") for r in rows):
        return
    fd, tmp = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(dict(version=1, rows=rows), f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    prune(path.parent)


def prune(directory, max_bytes=128 * 1024 * 1024, max_age=30 * 86400):
    files = []
    for p in directory.glob("*.json"):
        try:
            s = p.stat(); files.append((s.st_mtime, s.st_size, p))
        except FileNotFoundError:
            pass
    total = sum(s for _, s, _ in files)
    for stamp, size, p in sorted(files):
        if stamp < time.time() - max_age or total > max_bytes:
            try:
                p.unlink(); total -= size
            except FileNotFoundError:
                pass
