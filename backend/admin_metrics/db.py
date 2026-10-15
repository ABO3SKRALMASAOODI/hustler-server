"""One read-only, time-limited database connection per admin request (R5, R10).

The connection lives on flask.g and is closed at teardown. It runs in
autocommit, so one failed statement never poisons the rest of the request
(R8), and every statement is bounded by statement_timeout. Read-only is
enforced by the server (default_transaction_read_only), not by convention.
"""
import os
import sys
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app, g

READ_TIMEOUT_MS = 8000
HEAVY_TIMEOUT_MS = 20000
_OPTIONS = ("-c statement_timeout={timeout} -c lock_timeout=2000 "
            "-c idle_in_transaction_session_timeout=30000 "
            "-c default_transaction_read_only=on")


def open_readonly(timeout_ms=READ_TIMEOUT_MS):
    conn = psycopg2.connect(current_app.config["DATABASE_URL"],
                            cursor_factory=RealDictCursor, connect_timeout=5,
                            options=_OPTIONS.format(timeout=int(timeout_ms)))
    conn.autocommit = True
    return conn


def conn():
    """The request's connection (opened on first use)."""
    c = g.get("_admin_metrics_conn")
    if c is None or c.closed:
        c = open_readonly()
        g._admin_metrics_conn = c
    return c


def cursor():
    return conn().cursor()


def close(_exc=None):
    c = g.pop("_admin_metrics_conn", None)
    if c is not None:
        try:
            c.close()
        except Exception:
            pass


def set_timeout(cur, ms):
    cur.execute(f"SET statement_timeout = {int(ms)}")


# ── Feature detection (deploy order never matters) ───────────────────────
# New columns/tables from migrations 031–033 are applied by hand in a
# controlled release. Until they exist every reader behaves as before; the
# answer is cached per process for five minutes.
_FEATURE_TTL_S = 300
_features = {}
_features_lock = threading.Lock()


def _cached(key, compute):
    now = time.monotonic()
    with _features_lock:
        hit = _features.get(key)
        if hit and now - hit[0] < _FEATURE_TTL_S:
            return hit[1]
    value = compute()
    with _features_lock:
        _features[key] = (now, value)
    return value


def has_column(cur, table, column):
    def compute():
        cur.execute("""SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = %s
                          AND column_name = %s""", (table, column))
        return cur.fetchone() is not None
    return _cached(("col", table, column), compute)


def has_table(cur, table):
    def compute():
        cur.execute("SELECT to_regclass(%s) AS t", ("public." + table,))
        row = cur.fetchone()
        return bool(row and row["t"])
    return _cached(("table", table), compute)


def cached_value(key, ttl_s, compute):
    """Small per-process memo for slow-changing facts (e.g. tracking dates)."""
    now = time.monotonic()
    with _features_lock:
        hit = _features.get(("memo", key))
        if hit and now - hit[0] < ttl_s:
            return hit[1]
    value = compute()
    with _features_lock:
        _features[("memo", key)] = (now, value)
    return value


def reset_features():
    with _features_lock:
        _features.clear()


# ── Independent sections in parallel (one connection per worker) ──────────
# A summary page asks the database a dozen independent questions. Asked one
# after another, the wall time is their sum plus one round trip each; asked
# on a few read-only connections at once, it is about the slowest one.
# Tests (and any caller without a real database) set
# app.config["ADMIN_PARALLEL_SECTIONS"] = False to run them in order on the
# request's own connection.
PARALLEL_WORKERS = 6


_TIMING = bool(os.environ.get("ADMIN_SECTION_TIMING"))


def _run_one(fn, cur, name=""):
    t0 = time.perf_counter()
    try:
        return True, fn(cur)
    except Exception as e:          # reported per task, never raised here
        return False, e
    finally:
        if _TIMING:
            sys.stderr.write(f"SECTION {name} "
                             f"{(time.perf_counter() - t0) * 1000:.0f}ms\n")


def parallel(tasks, workers=PARALLEL_WORKERS, timeout_ms=READ_TIMEOUT_MS):
    """Run [(name, fn(cur)), ...] and return {name: (ok, value_or_error)}.

    Each worker thread opens its own read-only connection and takes tasks
    from a shared list until none are left, so a slow task never holds up
    the others. Order of results does not depend on timing."""
    import queue
    app = current_app._get_current_object()
    tasks = list(tasks)
    if (not app.config.get("ADMIN_PARALLEL_SECTIONS", True)
            or len(tasks) < 2 or workers < 2):
        cur = cursor()
        return {name: _run_one(fn, cur, name) for name, fn in tasks}

    pending = queue.Queue()
    for t in tasks:
        pending.put(t)
    results = {}
    lock = threading.Lock()

    def worker():
        with app.app_context():
            conn_ = None
            try:
                conn_ = open_readonly(timeout_ms)
                cur = conn_.cursor()
            except Exception as e:
                cur, err = None, e
            while True:
                try:
                    name, fn = pending.get_nowait()
                except queue.Empty:
                    break
                outcome = _run_one(fn, cur, name) if cur is not None \
                    else (False, err)
                with lock:
                    results[name] = outcome
            if conn_ is not None:
                try:
                    conn_.close()
                except Exception:
                    pass
            close()      # anything a task opened on this thread's context

    threads = [threading.Thread(target=worker, daemon=True,
                                name=f"admin-section-{i}")
               for i in range(min(workers, len(tasks)))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results
