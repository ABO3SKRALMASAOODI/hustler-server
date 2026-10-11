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


def get_or_compute(name, params, ttl_s, compute, fresh=False):
    """Return (value, computed_at, cache_age_s)."""
    key = make_key(name, params)
    now = time.monotonic()
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
    value = compute()
    computed_at = datetime.now(timezone.utc)
    if ttl_s > 0:
        with _lock:
            if len(_entries) >= MAX_ENTRIES:
                oldest = min(_entries, key=lambda k: _entries[k][0])
                _entries.pop(oldest, None)
            _entries[key] = (now + ttl_s, computed_at, value)
    return value, computed_at, 0


def clear():
    with _lock:
        _entries.clear()
        _fresh_at.clear()


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


def shared_report(endpoint, params, compute, fresh=False, logger=None):
    """Serve a heavy report from app_kv when younger than 10 minutes.

    `compute()` returns a JSON-able dict. The response is that dict plus
    `computed_at` (ISO UTC) and `cache_age_s`. Concurrency: only the worker
    holding the advisory lock recomputes; others serve the stale copy if one
    exists, or compute without storing when there is none yet.
    """
    key = shared_key(endpoint, params)
    now = time.monotonic()
    if fresh:
        if now - _shared_fresh_at.get(key, 0) < SHARED_FRESH_MIN_S:
            fresh = False
        else:
            _shared_fresh_at[key] = now
    conn, got = None, False
    lock_id = int(key[-15:], 16) & 0x7FFFFFFFFFFFFFFF
    try:
        conn = _writer()
        conn.autocommit = True
        cached = _read(conn, key)
        if cached and not fresh:
            age = _age_s(cached.get("computed_at"))
            if age is not None and age < SHARED_TTL_S:
                conn.close()
                return dict(cached, cache_age_s=age)
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS got", (lock_id,))
            got = bool(cur.fetchone()["got"])
        if not got and cached:
            conn.close()
            return dict(cached, cache_age_s=_age_s(cached.get("computed_at")))
    except psycopg2.Error:
        # The cache is an optimisation: never let it take the report down.
        if logger:
            logger.warning("admin report cache unavailable; computing directly")
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        conn, got = None, False
    try:
        value = compute()            # report errors propagate to the caller
        # Serialise exactly as Flask would, so a cached copy and a fresh one
        # are byte-for-byte the same shape (dates, decimals).
        value = json.loads(current_app.json.dumps(value))
        payload = dict(value, computed_at=_iso_now())
        if got and conn is not None:
            _store(conn, key, payload, logger)
        return dict(payload, cache_age_s=0)
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
