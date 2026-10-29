"""Content-addressed memo of a render's pure measurements (E3, 30 Oct 2026).

A factory short is rendered about 3.4 times as an approval preview and once
more as a final, and consecutive versions usually differ in one graphic or
one cut. Every render re-measured everything anyway: the picture check's
face and eye detection on the same source frames, the browser probes of the
same compositions. Measured on a real 30 s approval preview (project 5115,
quiet box, 2 CPUs): 174 CPU-seconds, of which the picture check took 66 and
the motion layer 39.

This module remembers the ANSWERS of pure functions, keyed by the exact
bytes they read plus the code and models that compute them, so an identical
question asked again (by the next version's render, by the final after its
preview, or by another slot of the same box) is answered from disk:

- the key holds a content digest of every input (the decoded frame's bytes,
  the composition's clip key, the probe times) AND a digest of the source
  files of the modules whose code computes the answer, plus the OpenCV
  version and model files. A release that changes any of them misses.
- a hit returns exactly what the computation returns (the callers rebuild
  their tuples; the tests compare hit and miss results for equality,
  including types).
- best effort both ways: a read or write error is a miss, never a failure.

Off unless RENDER_MEMO=1 (the box's slots set it, beside the shared
MOTION_CACHE_DIR it lives under), so tests and other lanes are untouched.
The store is plain JSON files, written atomically (temp file + rename) and
safe for many processes sharing one directory; it is bounded by count
(oldest first) and the box's cleaner can age it out like any cache.
"""

import hashlib
import json
import os
import tempfile
import threading
import time

VERSION = 1
MAX_FILES = int(os.getenv("RENDER_MEMO_MAX_FILES", "200000") or 200000)
PRUNE_EVERY_S = 1800.0         # a process checks the bound at most this often

_lock = threading.Lock()
_last_prune = [None]
_code_digests = {}
_stats = {"hits": 0, "misses": 0, "puts": 0}


def enabled():
    return os.getenv("RENDER_MEMO", "0") == "1"


def root():
    """<MOTION_CACHE_DIR or the motion cache's default>/memo, or
    RENDER_MEMO_DIR when set."""
    explicit = os.getenv("RENDER_MEMO_DIR", "").strip()
    if explicit:
        return explicit
    base = os.getenv("MOTION_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "valmera-motion-cache")
    return os.path.join(base, "memo")


def stats():
    with _lock:
        return dict(_stats)


def file_digest(path):
    """sha1 of a file's bytes (memoised per path, size and mtime)."""
    try:
        st = os.stat(path)
    except OSError:
        return "missing"
    memo = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    with _lock:
        hit = _code_digests.get(memo)
    if hit:
        return hit
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    out = h.hexdigest()
    with _lock:
        _code_digests[memo] = out
    return out


def code_digest(*modules):
    """One digest of the source files of ``modules`` (module objects or
    file paths): any edit to the code that computes an answer moves it."""
    parts = []
    for m in modules:
        path = m if isinstance(m, str) else getattr(m, "__file__", None)
        if path and path.endswith((".pyc", ".pyo")):
            path = path[:-1]
        parts.append(file_digest(path) if path else "none")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


def key(namespace, *parts):
    """sha256 over the namespace, VERSION and every part (bytes are hashed
    as-is, anything else as canonical JSON)."""
    h = hashlib.sha256()
    h.update(f"{VERSION}:{namespace}".encode())
    for p in parts:
        if isinstance(p, (bytes, bytearray, memoryview)):
            h.update(b"\x00b")
            h.update(bytes(p))
        else:
            h.update(b"\x00j")
            h.update(json.dumps(p, sort_keys=True, default=str,
                                separators=(",", ":")).encode())
    return h.hexdigest()[:48]


def array_part(a):
    """A numpy array as a key part: dtype, shape and its bytes."""
    import numpy as np
    a = np.ascontiguousarray(a)
    return str(a.dtype).encode() + repr(a.shape).encode() + b"|" + \
        a.tobytes()


def _path(namespace, k):
    return os.path.join(root(), namespace, k[:2], k + ".json")


_MISS = object()


def get(namespace, k):
    """The stored answer, or MISS (an answer may itself be None)."""
    if not enabled():
        return _MISS
    try:
        with open(_path(namespace, k)) as f:
            doc = json.load(f)
        if not isinstance(doc, dict) or doc.get("v") != VERSION:
            raise ValueError("foreign memo")
        with _lock:
            _stats["hits"] += 1
        return doc.get("a")
    except Exception:  # noqa: BLE001 — a miss
        with _lock:
            _stats["misses"] += 1
        return _MISS


def put(namespace, k, answer):
    if not enabled():
        return False
    try:
        p = _path(namespace, k)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w") as f:
            json.dump({"v": VERSION, "a": answer}, f,
                      separators=(",", ":"))
        os.replace(tmp, p)
        now = time.monotonic()
        with _lock:
            _stats["puts"] += 1
            if _last_prune[0] is None:
                _last_prune[0] = now     # the first check waits a period
            due = now - _last_prune[0] >= PRUNE_EVERY_S
            if due:
                _last_prune[0] = now
        if due:
            prune()
        return True
    except Exception:  # noqa: BLE001 — caching is best effort
        return False


def is_miss(value):
    return value is _MISS


def prune(max_files=None):
    """Keep at most ``max_files`` answers, the least recently written going
    first. Never raises (another process may prune the same files)."""
    limit = MAX_FILES if max_files is None else int(max_files)
    try:
        files = []
        base = root()
        for ns in os.listdir(base):
            nsdir = os.path.join(base, ns)
            if not os.path.isdir(nsdir):
                continue
            for shard in os.listdir(nsdir):
                sdir = os.path.join(nsdir, shard)
                try:
                    with os.scandir(sdir) as it:
                        for e in it:
                            if e.name.endswith(".json"):
                                try:
                                    files.append((e.stat().st_mtime, e.path))
                                except OSError:
                                    pass
                except OSError:
                    continue
        if len(files) <= limit:
            return 0
        files.sort()
        gone = 0
        for _m, p in files[:len(files) - limit]:
            try:
                os.remove(p)
                gone += 1
            except OSError:
                pass
        return gone
    except Exception:  # noqa: BLE001
        return 0


def pack(obj):
    """``obj`` as JSON-safe data that unpack() turns back into an EQUAL
    object of the same types: tuples, numpy arrays and scalars, dicts with
    non-string keys and sets are tagged ({"__t__": ...})."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return {"__t__": "float", "r": repr(obj)}
        return obj
    if isinstance(obj, list):
        return [pack(v) for v in obj]
    if isinstance(obj, tuple):
        return {"__t__": "tuple", "v": [pack(v) for v in obj]}
    if isinstance(obj, (set, frozenset)):
        return {"__t__": "frozenset" if isinstance(obj, frozenset)
                else "set", "v": sorted((pack(v) for v in obj),
                                        key=lambda x: json.dumps(x))}
    if isinstance(obj, dict):
        if all(isinstance(k_, str) for k_ in obj) and "__t__" not in obj:
            return {k_: pack(v) for k_, v in obj.items()}
        return {"__t__": "dict",
                "v": [[pack(k_), pack(v)] for k_, v in obj.items()]}
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        np = None
    if np is not None:
        if isinstance(obj, np.ndarray):
            import base64
            a = np.ascontiguousarray(obj)
            return {"__t__": "nd", "dtype": a.dtype.str,
                    "shape": list(a.shape),
                    "b": base64.b64encode(a.tobytes()).decode()}
        if isinstance(obj, np.generic):
            return {"__t__": "np", "dtype": obj.dtype.str,
                    "v": obj.item() if not isinstance(obj.item(), float)
                    else repr(obj.item())}
    raise TypeError(f"render_memo cannot store {type(obj).__name__}")


def unpack(obj):
    if isinstance(obj, list):
        return [unpack(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    t = obj.get("__t__")
    if t is None:
        return {k_: unpack(v) for k_, v in obj.items()}
    if t == "tuple":
        return tuple(unpack(v) for v in obj["v"])
    if t == "set":
        return {unpack(v) for v in obj["v"]}
    if t == "frozenset":
        return frozenset(unpack(v) for v in obj["v"])
    if t == "dict":
        return {unpack(k_): unpack(v) for k_, v in obj["v"]}
    if t == "float":
        return float(obj["r"])
    import numpy as np
    if t == "nd":
        import base64
        return np.frombuffer(base64.b64decode(obj["b"]),
                             dtype=np.dtype(obj["dtype"])).reshape(
                                 obj["shape"]).copy()
    if t == "np":
        v = obj["v"]
        return np.dtype(obj["dtype"]).type(float(v) if isinstance(v, str)
                                           else v)
    raise ValueError(f"unknown memo tag {t}")


def memoize(namespace, k, compute, encode=None, decode=None):
    """compute() once per key: the stored answer when there is one (through
    ``decode``, default unpack), else compute it, store ``encode(answer)``
    (default pack) and return it. ``encode`` may return MISS to keep an
    answer out of the store (a budget-cut or failed measurement)."""
    got = get(namespace, k)
    if not is_miss(got):
        try:
            return decode(got) if decode else unpack(got)
        except Exception:  # noqa: BLE001 — a damaged answer is a miss
            pass
    answer = compute()
    if enabled():
        try:
            stored = encode(answer) if encode else pack(answer)
        except Exception:  # noqa: BLE001
            stored = _MISS
        if not is_miss(stored):
            put(namespace, k, stored)
    return answer


MISS = _MISS
