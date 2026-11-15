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
import threading

_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE = {}


def root():
    override = os.environ.get("FOOTAGE_TRANSCRIPT_CACHE")
    base = Path(override) if override else (
        Path.home() / "Library/Caches/valmera/transcripts" if sys.platform == "darwin"
        else Path("/var/cache/valmera/transcripts") if Path("/var/cache/valmera").is_dir()
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "valmera/transcripts")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
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
    try:
        p = root()
        lock_path = p / (namespace + "-" + key[:2] + ".lock")
        lock = lock_path.open("a")
        os.utime(lock_path, None)  # the janitor must not unlink an active lock
    except OSError as exc:
        print(f"[transcript-cache] unavailable: {type(exc).__name__}", file=sys.stderr)
        yield None
        return
    with lock as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield p / (key + ".json")


def read(path):
    if path is None:
        return None
    try:
        obj = json.loads(path.read_text())
        if (obj.get("version") == 1 and isinstance(obj.get("rows"), list)
                and all(isinstance(r, dict) and isinstance(r.get('text'), str)
                        and isinstance(r.get('t0'), (int, float)) and isinstance(r.get('t1'), (int, float))
                        for r in obj['rows'])):
            try:
                os.utime(path, None)
            except OSError:
                pass
            return obj["rows"]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def write(path, rows):
    # Failures are never memoized. An empty successful transcript (silence) is.
    if path is None or any(r.get("error") for r in rows):
        return
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=".transcript-", dir=path.parent)
        with os.fdopen(fd, "w") as f:
            json.dump(dict(version=1, rows=rows), f, ensure_ascii=False)
        os.replace(tmp, path)
        # Do not rescan thousands of JSON files on every 12-second piece.
        with _PRUNE_LOCK:
            now = time.monotonic()
            if now - _LAST_PRUNE.get(str(path.parent), -300) >= 300:
                _LAST_PRUNE[str(path.parent)] = now
                prune(path.parent)
    except OSError as exc:
        print(f"[transcript-cache] write failed: {type(exc).__name__}", file=sys.stderr)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def prune(directory, max_bytes=128 * 1024 * 1024, max_age=30 * 86400):
    for p in directory.glob(".transcript-*"):
        try:
            if p.stat().st_mtime < time.time() - 3600:
                p.unlink()
        except FileNotFoundError:
            pass
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
