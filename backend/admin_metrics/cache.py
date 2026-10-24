"""Two caches (R3, R9).

1. `local`: an in-process TTL cache for /admin/v2 responses, per gunicorn
   worker, keyed by endpoint + params + the admin-timezone date (so "today"
   rolls over at local midnight). `fresh=1` bypasses it at most once per 10 s
   per key, so a stuck refresh button cannot hammer the database.

2. `shared`: the 10-minute engineering-report cache in app_kv, shared by the
   three gunicorn processes and across deploys, with single-flight via
   pg_try_advisory_lock. Writes are best-effort: if app_kv is unwritable the
   report is still served, just uncached.
"""
import hashlib
import json
import threading
import time
from datetime import datetime, timezone

import psycopg2
import psycopg2.errors
from psycopg2.extras import RealDictCursor
from flask import current_app

from admin_metrics import ranges

_lock = threading.Lock()
_entries = {}          # key -> (expires_monotonic, computed_at_utc, value)
_fresh_at = {}         # key -> monotonic time of the last bypass
MAX_ENTRIES = 256
FRESH_MIN_INTERVAL_S = 10


def make_key(name, params):
    items = sorted((k, str(v)) for k, v in (params or {}).items()
                   if k not in ("fresh",))
    return json.dumps([name, ranges.local_today().isoformat(), items])


_refreshing = set()     # keys being recomputed in the background


def get_or_compute(name, params, ttl_s, compute, fresh=False, stale_s=0,
                   keep=None):
    """Return (value, computed_at, cache_age_s).

    stale_s > 0 serves an expired answer at once (stale-while-revalidate)
    when it is younger than ttl_s + stale_s, and recomputes it on a
    background thread, one per key. `keep(value)` decides whether a
    background answer may replace the cached one (e.g. not when a section
    failed); a page never waits on that refresh."""
    key = make_key(name, params)
    now = time.monotonic()
    stale_hit = None
    with _lock:
        hit = _entries.get(key)
        if fresh:
            last = _fresh_at.get(key, 0)
            if now - last < FRESH_MIN_INTERVAL_S and hit:
                fresh = False            # rate limited: serve the cache
            else:
                _fresh_at[key] = now
        if hit and not fresh and hit[0] > now:
            computed_at = hit[1]
            age = (datetime.now(timezone.utc) - computed_at).total_seconds()
            return hit[2], computed_at, max(0, int(age))
        if hit and not fresh and stale_s > 0 and hit[0] + stale_s > now:
            stale_hit = hit
            start_bg = key not in _refreshing
            if start_bg:
                _refreshing.add(key)
    if stale_hit is not None:
        if start_bg:
            _refresh_in_background(key, ttl_s, compute, keep)
        computed_at = stale_hit[1]
        age = (datetime.now(timezone.utc) - computed_at).total_seconds()
        return stale_hit[2], computed_at, max(0, int(age))
    value = compute()
    computed_at = datetime.now(timezone.utc)
    if ttl_s > 0:
        _store_local(key, ttl_s, computed_at, value)
    return value, computed_at, 0


def _store_local(key, ttl_s, computed_at, value):
    with _lock:
        if len(_entries) >= MAX_ENTRIES and key not in _entries:
            oldest = min(_entries, key=lambda k: _entries[k][0])
            _entries.pop(oldest, None)
        _entries[key] = (time.monotonic() + ttl_s, computed_at, value)


def _refresh_in_background(key, ttl_s, compute, keep):
    try:
        app = current_app._get_current_object()
    except RuntimeError:
        app = None

    def run():
        try:
            if app is not None:
                with app.app_context():
                    try:
                        value = compute()
                    finally:
                        from admin_metrics import db
                        db.close()
            else:
                value = compute()
            if keep is None or keep(value):
                _store_local(key, ttl_s, datetime.now(timezone.utc), value)
        except Exception:
            pass                         # the stale copy stays; next ask retries
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True, name="admin-cache-refresh").start()


def clear():
    with _lock:
        _entries.clear()
        _fresh_at.clear()
        _refreshing.clear()


def clear_key(name, params):
    with _lock:
        _entries.pop(make_key(name, params), None)


# ── Shared app_kv cache for heavy engineering reports ────────────────────
SHARED_TTL_S = 600
SHARED_FRESH_MIN_S = 30
_shared_fresh_at = {}


def shared_key(endpoint, params):
    raw = json.dumps([endpoint, sorted((k, str(v)) for k, v in
                                       (params or {}).items()
                                       if k != "fresh")])
    return "admin_cache:" + hashlib.sha1(raw.encode()).hexdigest()


def _writer():
    return psycopg2.connect(
        current_app.config["DATABASE_URL"], cursor_factory=RealDictCursor,
        connect_timeout=5,
        options="-c statement_timeout=5000 -c lock_timeout=2000")


def _read(conn, key):
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
        row = cur.fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row["value"])
        payload["computed_at"] = payload.get("computed_at")
        return payload
    except (TypeError, ValueError):
        return None


# A copy older than the TTL is still served at once (and refreshed behind
# it) for this long; the report says how old it is (`cache_age_s`) and that a
# refresh is running (`refreshing`).
SHARED_STALE_S = 7 * 24 * 3600
# A request with no usable copy waits this long for the computation, then
# answers {"pending": true} and the page asks again; the computation carries
# on in the background with BACKGROUND_TIMEOUT_MS instead of the request's
# 20 s limit (the 30-day reports took 10-25 s and failed with "This report
# took too long", 24 Oct 2026).
SHARED_WAIT_S = 2.5
BACKGROUND_TIMEOUT_MS = 120000
FAILURE_MEMORY_S = 60
_shared_lock = threading.Lock()
_shared_running = {}    # key -> threading.Event set when the computation ends
_shared_local = {}      # key -> payload, when app_kv could not be written
_shared_failed = {}     # key -> (monotonic time, message)
_background = threading.local()


class ReportUnavailable(Exception):
    """The report could not be computed; the admin answers 503, retryable."""


def in_background():
    """True on a background refresh thread: no one is waiting, so database
    time limits may be longer (routes.admin_video.adb, admin get_db)."""
    return bool(getattr(_background, "active", False))


def statement_timeout_ms(default_ms):
    return BACKGROUND_TIMEOUT_MS if in_background() else default_ms


def _newest(*payloads):
    best = None
    for p in payloads:
        if not p:
            continue
        if best is None or str(p.get("computed_at") or "") > \
                str(best.get("computed_at") or ""):
            best = p
    return best


def _start_shared(key, compute, logger):
    """Compute `key` on a background thread (one per key per process; the
    advisory lock makes it one across processes). Returns its Event."""
    with _shared_lock:
        running = _shared_running.get(key)
        if running is not None:
            return running
        done = _shared_running[key] = threading.Event()
    try:
        app = current_app._get_current_object()
    except RuntimeError:
        app = None
    lock_id = int(key[-15:], 16) & 0x7FFFFFFFFFFFFFFF

    def run():
        conn, got = None, True
        _background.active = True
        try:
            ctx = app.app_context() if app is not None else None
            if ctx is not None:
                ctx.push()
            try:
                try:
                    conn = _writer()
                    conn.autocommit = True
                    with conn.cursor() as cur:
                        cur.execute("SELECT pg_try_advisory_lock(%s) AS got",
                                    (lock_id,))
                        got = bool(cur.fetchone()["got"])
                except psycopg2.Error:
                    conn, got = None, True    # no shared cache: just compute
                if not got:
                    return                    # another process is on it
                value = compute()
                dumps = app.json.dumps if app is not None else \
                    (lambda v: json.dumps(v, default=str))
                payload = dict(json.loads(dumps(value)),
                               computed_at=_iso_now())
                with _shared_lock:
                    _shared_local[key] = payload
                    _shared_failed.pop(key, None)
                if conn is not None:
                    _store(conn, key, payload, logger)
            except Exception as e:
                msg = ("This report took too long to compute."
                       if isinstance(e, psycopg2.errors.QueryCanceled)
                       else "This report could not be computed.")
                with _shared_lock:
                    _shared_failed[key] = (time.monotonic(), msg)
                if logger:
                    logger.warning("admin report refresh failed (%s)",
                                   type(e).__name__)
            finally:
                if conn is not None:
                    try:
                        if got:
                            with conn.cursor() as cur:
                                cur.execute("SELECT pg_advisory_unlock(%s)",
                                            (lock_id,))
                        conn.close()
                    except Exception:
                        pass
                try:
                    from admin_metrics import db
                    db.close()
                except Exception:
                    pass
                if ctx is not None:
                    ctx.pop()
        finally:
            _background.active = False
            with _shared_lock:
                _shared_running.pop(key, None)
            done.set()

    threading.Thread(target=run, daemon=True,
                     name="admin-cache-refresh-shared").start()
    return done


def shared_report(endpoint, params, compute, fresh=False, logger=None):
    """Serve a heavy report from app_kv (shared by every gunicorn process and
    across deploys), never making the page wait on a long scan.

    * a copy younger than SHARED_TTL_S: served as is;
    * an older copy (up to SHARED_STALE_S): served at once with
      `refreshing: true` while a background thread recomputes it;
    * no copy: the request waits up to SHARED_WAIT_S for the computation,
      else answers {"pending": true, "retry_after_s": 5} and the computation
      continues in the background; a failed computation answers 503.

    Every answer carries `computed_at` (ISO UTC) and `cache_age_s`.
    `fresh` (rate limited) recomputes behind the current copy.
    """
    key = shared_key(endpoint, params)
    now = time.monotonic()
    if fresh:
        if now - _shared_fresh_at.get(key, 0) < SHARED_FRESH_MIN_S:
            fresh = False
        else:
            _shared_fresh_at[key] = now
    stored = None
    try:
        conn = _writer()
        try:
            conn.autocommit = True
            stored = _read(conn, key)
        finally:
            conn.close()
    except psycopg2.Error:
        # The cache is an optimisation: never let it take the report down.
        if logger:
            logger.warning("admin report cache unavailable; computing directly")
    with _shared_lock:
        local = _shared_local.get(key)
    best = _newest(stored, local)
    age = _age_s(best.get("computed_at")) if best else None
    if best and age is not None and age < SHARED_TTL_S and not fresh:
        return dict(best, cache_age_s=age)
    done = _start_shared(key, compute, logger)
    if best and age is not None and age < SHARED_STALE_S:
        return dict(best, cache_age_s=age, refreshing=True)
    done.wait(SHARED_WAIT_S)
    with _shared_lock:
        local = _shared_local.get(key)
        failed = _shared_failed.get(key)
    if local and (best is None or local is not best):
        return dict(local, cache_age_s=_age_s(local.get("computed_at")) or 0)
    if failed and time.monotonic() - failed[0] < FAILURE_MEMORY_S \
            and done.is_set():
        raise ReportUnavailable(failed[1])
    return {"pending": True, "refreshing": True, "retry_after_s": 5,
            "computed_at": None, "cache_age_s": None}


def _store(conn, key, payload, logger):
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO app_kv (key, value, updated_at)
                           VALUES (%s, %s, NOW())
                           ON CONFLICT (key) DO UPDATE
                             SET value = EXCLUDED.value,
                                 updated_at = NOW()""",
                        (key, json.dumps(payload, default=str)))
    except psycopg2.Error:
        if logger:
            logger.warning("admin report cache write skipped")


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() \
        .replace("+00:00", "Z")


def _age_s(iso):
    if not iso:
        return None
    try:
        at = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - at).total_seconds()))
