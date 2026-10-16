"""MCP pure reads answered in the backend process: no queue, no container.

Every MCP read used to become a video_jobs row, a dispatcher claim and a
Cloudflare container call: get_words took 4-8 s at the median (p95 42 s) for
about two seconds of executor time, nearly all of it Postgres round trips from
a container in another region. Editors make thousands of reads a run.

The reads in worker/read_tools.FAST_READ_TOOLS need only Postgres rows (the
project's index, EDL and asset rows), so the backend runs them itself, with
the SAME code the executor runs (worker/read_tools.py, loaded standalone) and
the same answer: ``answer`` returns the result dict the queued call would have
stored, in its stored (JSONB) form, and writes the same activity-feed row.

Correctness first. Anything outside the proven path returns None and the call
takes the queued path unchanged: arguments the tool does not take, a tool that
raises, an EDL with fields this release's schema does not know (the skew
guard's rule), a project whose analysis is not ready, an index row that
changes while it is read, or a busy process. Auth, project scoping and index
readiness are decided by routes/mcp.py before this runs, exactly as for a
queued call.

Bounded on Render: no media is touched; index sections are read as JSON text
(only the keys the tool reads), cached per process by the row's xmin up to
MCP_FAST_READS_CACHE_MB, parsed per call; at most MCP_FAST_READS_CONCURRENCY
reads run at once per process; every statement is capped at 5 s inside a
savepoint, so a failure leaves the request's transaction usable.
"""

import copy
import importlib.util
import json
import math
import os
import sys
import threading
import time
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parents[2] / "worker"

ENABLED = os.getenv("MCP_FAST_READS", "1").strip().lower() not in {
    "0", "off", "false", "no"}
CACHE_BYTES = max(0, int(float(os.getenv("MCP_FAST_READS_CACHE_MB", "24"))
                         * 1048576))
CONCURRENCY = max(1, int(os.getenv("MCP_FAST_READS_CONCURRENCY", "3")))
# One index section larger than this is not decoded here (the largest seen in
# production, 31,606 words, is ~3 MB of JSON).
SECTION_MAX_CHARS = 12 * 1048576
# An uploaded clip's whole index row (get_transcript(asset_key=...)) is read
# only when its stored (compressed) size is at most this.
ASSET_INDEX_MAX_BYTES = 2 * 1048576
STATEMENT_TIMEOUT_MS = 5000
ACQUIRE_TIMEOUT_S = 1.0

# Worker modules read_tools reaches through its _mod(): loaded standalone
# under worker_<name> unless this process already has them (a worker test
# process imports them by their plain names).
_SHARED = ("schemas", "timeline", "visual", "edl_read", "tool_outcome",
           "music_rights", "read_tools")
_load_lock = threading.Lock()


def _worker(name):
    # Always under the lock: a module is registered before it finishes
    # executing, and another request thread must never use it half-loaded.
    with _load_lock:
        module = sys.modules.get("worker_" + name)
        if module is None and name in _SHARED:
            module = sys.modules.get(name)
        if module is not None:
            return module
        spec = importlib.util.spec_from_file_location(
            "worker_" + name, str(_WORKER_DIR / f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules["worker_" + name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop("worker_" + name, None)
            raise
        return module


def read_tools():
    if "read_tools" not in sys.modules:
        # Standalone (the backend): read_tools resolves these by worker_<name>.
        for name in ("schemas", "timeline", "visual", "edl_read",
                     "tool_outcome", "music_rights"):
            _worker(name)
    return _worker("read_tools")


_load_failed = []


def eligible(name):
    """Whether this tool may be answered here. A worker module that cannot
    load (a broken checkout) turns the fast path off, never the call."""
    if not ENABLED or _load_failed:
        return False
    try:
        return name in read_tools().FAST_READ_TOOLS
    except Exception as exc:  # noqa: BLE001
        _load_failed.append(f"{type(exc).__name__}: {str(exc)[:200]}")
        print(f"[mcp-fast-reads] disabled: {_load_failed[0]}", flush=True)
        return False


class _NotLoaded(KeyError):
    """A read touched an index section this path did not load."""


class _Index(dict):
    """The index sections one read needs; touching any other one falls back
    to the queued path rather than answering from a partial index."""

    def __init__(self, loaded, values):
        super().__init__(values)
        self._loaded = frozenset(loaded)

    def _check(self, key):
        if key not in self._loaded:
            raise _NotLoaded(key)

    def get(self, key, default=None):
        self._check(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._check(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self._check(key)
        return super().__contains__(key)

    def __iter__(self):
        raise _NotLoaded("*")

    def keys(self):
        raise _NotLoaded("*")

    def items(self):
        raise _NotLoaded("*")

    def values(self):
        raise _NotLoaded("*")


class _Db:
    """ToolContext.db for this request's connection."""

    def __init__(self, conn):
        self.conn = conn

    def run(self, fn, *args, **kwargs):
        if fn is read_tools().get_index_by_sha:
            # An uploaded clip's whole index row: bounded before it is read.
            with self.conn.cursor() as cur:
                cur.execute("""SELECT pg_column_size(json) AS size
                               FROM indexes WHERE video_sha256 = %s""",
                            (args[0],))
                row = cur.fetchone()
            if row and int(row["size"] or 0) > ASSET_INDEX_MAX_BYTES:
                raise _NotLoaded("asset index too large for the fast path")
        return fn(self.conn, *args, **kwargs)


class _ReadContext:
    """What a read tool sees of agent_tools.ToolContext on a fresh MCP call."""

    def __init__(self, conn, project, index, has_main_video, edl_row):
        self.db = _Db(conn)
        self.project = project
        self.project_id = project["id"]
        self.session_id = project.get("chat_session_id")
        self.job = None
        self.index = index
        self.has_main_video = has_main_video
        self.duration = (float(index["video"]["duration"])
                         if has_main_video else 0.0)
        self._row = copy.deepcopy(dict(edl_row))

    def clamp(self, t):
        return read_tools().clamp_seconds(self.duration, t)

    def latest_edl(self):
        # mcp_exec's per-call cache hands out private copies of one row.
        return copy.deepcopy(self._row)


# ── Index sections, cached per process by the row's version ───────────────

_MISS = object()
_cache = OrderedDict()          # (sha, xmin, key) -> JSON text or None
_cache_size = 0
_cache_lock = threading.Lock()
_running = threading.BoundedSemaphore(CONCURRENCY)


def _cached(key):
    with _cache_lock:
        value = _cache.get(key, _MISS)
        if value is not _MISS:
            _cache.move_to_end(key)
        return value


def _remember(key, text):
    global _cache_size
    size = len(text or "")
    if size > CACHE_BYTES:
        return
    with _cache_lock:
        if key in _cache:
            return
        _cache[key] = text
        _cache_size += size
        while _cache_size > CACHE_BYTES and _cache:
            _old, dropped = _cache.popitem(last=False)
            _cache_size -= len(dropped or "")


def _index_sections(conn, sha, keys):
    """{key: value} of the index row's sections ``keys`` (absent keys are
    left out), or None when the row is missing or changed while read."""
    with conn.cursor() as cur:
        cur.execute("SELECT xmin::text AS gen FROM indexes "
                    "WHERE video_sha256 = %s", (sha,))
        row = cur.fetchone()
        if not row:
            return None
        gen = row["gen"]
        texts, missing = {}, []
        for key in keys:
            text = _cached((sha, gen, key))
            if text is _MISS:
                missing.append(key)
            else:
                texts[key] = text
        if missing:
            columns = ", ".join(f"(json->%s)::text AS s{i}"
                                for i in range(len(missing)))
            cur.execute(f"SELECT xmin::text AS gen, {columns} FROM indexes "
                        "WHERE video_sha256 = %s", (*missing, sha))
            fetched = cur.fetchone()
            if not fetched or fetched["gen"] != gen:
                return None
            for i, key in enumerate(missing):
                text = fetched[f"s{i}"]
                if text is not None and len(text) > SECTION_MAX_CHARS:
                    raise _NotLoaded(f"index section {key} is too large")
                texts[key] = text
                _remember((sha, gen, key), text)
    return {key: json.loads(text) for key, text in texts.items()
            if text is not None}


# ── The stored form of a queued result ─────────────────────────────────────

def _json_safe(value):
    """worker/db._json_safe: what the executor writes into video_jobs."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _jsonb_order(value):
    """The value as PostgreSQL JSONB hands it back: object keys shorter first,
    then bytewise. The queued result reaches the caller through that column,
    so the MCP reply's nested objects carry this order."""
    if isinstance(value, dict):
        ordered = sorted(value, key=lambda k: (len(k.encode("utf-8")),
                                               k.encode("utf-8")))
        return {key: _jsonb_order(value[key]) for key in ordered}
    if isinstance(value, list):
        return [_jsonb_order(item) for item in value]
    return value


def stored_form(result):
    """A result dict exactly as a queued call's row would return it."""
    return _jsonb_order(json.loads(json.dumps(_json_safe(result))))


# ── The fast path ──────────────────────────────────────────────────────────

def _answer(conn, user_id, project, project_id, name, args, started):
    tools = read_tools()
    edl_row = tools.latest_edl(conn, project_id)
    if not edl_row:
        # The executor would create the default EDL first: a write.
        return None
    if tools.edl_problem(edl_row["json"]):
        # Written by a release whose fields this one does not know: the
        # queued path's skew guard decides where it runs.
        return None
    keys = tools.FAST_READ_INDEX_KEYS[name]
    original = tools.latest_asset(conn, project_id, "original")
    if original:
        sha = original.get("sha256")
        if not sha:
            return None
        values = _index_sections(conn, sha, keys)
        if values is None:
            return None
        has_main_video = bool(values.get("video"))
    else:
        values, has_main_video = {}, False
    ctx = _ReadContext(conn, project, _Index(keys, values), has_main_video,
                       edl_row)
    ran = tools.run_read(ctx, name, args)
    if ran is None:
        return None
    text, structured = ran
    version = edl_row["version"]
    _subscribed, plan, _trialing = tools.user_billing(conn, user_id)
    preview = tools.find_render_asset(conn, project_id, "preview", version)
    result = tools.mcp_read_result(text, structured, version, plan, preview)
    content, meta = tools.activity_row(name, args, text, source="mcp",
                                       edl_version=version)
    meta["served_by"] = "backend"
    meta["elapsed_ms"] = int(round((time.monotonic() - started) * 1000))
    tools.add_message(conn, project["chat_session_id"], "activity", content,
                      meta)
    return stored_form(result)


def answer(conn, user_id, project, project_id, name, args, started=None):
    """The stored result of this read, answered here, or None (queue it).

    ``conn`` is the request's open connection (RealDictCursor); the work runs
    in a savepoint so a fallback leaves its transaction usable.
    """
    started = time.monotonic() if started is None else started
    if not eligible(name) or not isinstance(args, dict):
        return None
    if name == "get_shots" and os.getenv("VISUAL_SAMPLE_S"):
        # The executors never set it; a backend override would print a
        # different sampling step than the queued answer.
        return None
    if not _running.acquire(timeout=ACQUIRE_TIMEOUT_S):
        return None
    savepoint = False
    try:
        try:
            cur = conn.cursor()
            cur.execute("SAVEPOINT mcp_fast_read")
            savepoint = True
            cur.execute("SET LOCAL statement_timeout = %s",
                        (STATEMENT_TIMEOUT_MS,))
            result = _answer(conn, user_id, project, project_id, name, args,
                             started)
        except Exception:
            result = None
        if savepoint:
            # Undo everything a fallback touched (an aborted statement, a
            # half-written activity row) so the queued path below runs on a
            # clean transaction; keep a success.
            cur = conn.cursor()
            if result is None:
                cur.execute("ROLLBACK TO SAVEPOINT mcp_fast_read")
            cur.execute("RELEASE SAVEPOINT mcp_fast_read")
        return result
    finally:
        _running.release()
