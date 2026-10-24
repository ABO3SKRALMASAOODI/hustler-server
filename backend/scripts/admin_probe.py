#!/usr/bin/env python3
"""Admin probe: call every admin endpoint the way /admin calls it, report
status, latency and whether the numbers are plausible, and fail loudly.

    DATABASE_URL=<production URL> python scripts/admin_probe.py [--json]

Wrapper (reads DATABASE_URL from the main checkout's backend/.env without
printing it): /Users/masaoodi/Documents/Valmera/.valmera/admin_probe.sh

Safety, by construction:
  * a minimal Flask app with only the admin blueprints: no create_app(), so
    no newsletter/billing/paid-alert scheduler can start;
  * every psycopg2 connection gets `-c default_transaction_read_only=on`
    appended (Postgres keeps the last value), so nothing can write;
  * only GET requests, through the Flask test client;
  * a random local SECRET_KEY signs a local admin token. No production
    secret is read, printed or used, and nothing signs in anywhere.

Exit status: 0 when every endpoint returned 200, passed its sanity check and
answered within --budget seconds (default 3); 1 otherwise; 2 on setup errors.

Latency is measured from this machine, so it includes one network round trip
to the database per query; the probe prints that round-trip time (`db rtt`)
so a slow result can be told apart from a slow network.
"""
import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

ADMIN_EMAIL = "thevalmera@gmail.com"
READ_ONLY = "-c default_transaction_read_only=on"


# Per-thread network accounting, so a result measured from Dubai can be
# turned into what the same request costs next to the database on Render.
import threading
_stats_lock = threading.Lock()
_stats = {}          # thread ident -> {"name", "connects", "connect_s", "execs", "exec_s"}


def _note(field, seconds):
    t = threading.current_thread()
    if t.name.startswith("admin-cache-refresh"):
        return                      # background refreshes are not this request
    with _stats_lock:
        s = _stats.setdefault(t.ident, {"name": t.name, "connects": 0,
                                        "connect_s": 0.0, "execs": 0,
                                        "exec_s": 0.0})
        s[field + "s"] += 1
        s[field + "_s"] += seconds


def reset_stats():
    with _stats_lock:
        _stats.clear()


def snapshot_stats():
    with _stats_lock:
        return [dict(v) for v in _stats.values()]


_timed_cursor_classes = {}


def _timed(factory):
    cls = _timed_cursor_classes.get(factory)
    if cls is None:
        class Timed(factory):
            def execute(self, *a, **kw):
                t0 = time.perf_counter()
                try:
                    return super().execute(*a, **kw)
                finally:
                    _note("exec", time.perf_counter() - t0)
        cls = _timed_cursor_classes[factory] = Timed
    return cls


def force_read_only():
    """Every connection this process opens is read-only, whatever the
    handler asked for, and its connects and statements are timed."""
    import psycopg2
    import psycopg2.extensions as ext
    original = psycopg2.connect
    if getattr(original, "_admin_probe_read_only", False):
        return

    class TimedConnection(ext.connection):
        def cursor(self, *a, **kw):
            factory = kw.get("cursor_factory") or self.cursor_factory or ext.cursor
            kw["cursor_factory"] = _timed(factory)
            return super().cursor(*a, **kw)

    def connect(dsn=None, connection_factory=None, cursor_factory=None, **kw):
        kw["options"] = ((kw.get("options") or "") + " " + READ_ONLY).strip()
        t0 = time.perf_counter()
        try:
            return original(dsn, connection_factory=connection_factory or TimedConnection,
                            cursor_factory=cursor_factory, **kw)
        finally:
            _note("connect", time.perf_counter() - t0)

    connect._admin_probe_read_only = True
    psycopg2.connect = connect


# Render: the web service and the database share a region.
SERVER_CONNECT_S = 0.02


def server_estimate(wall_s, stats, rtt_s):
    """Wall time minus this machine's extra network: connect handshakes and
    one round trip per statement. Sections that run on parallel threads
    overlap, so only the slowest thread's network is taken off."""
    main = threading.main_thread().ident
    main_net, worker_net, connects = 0.0, 0.0, 0
    for s in stats:
        net = (s["connect_s"] - s["connects"] * SERVER_CONNECT_S
               + s["execs"] * rtt_s)
        connects += s["connects"]
        if s["name"] == "MainThread" or s.get("ident") == main:
            main_net += net
        else:
            worker_net = max(worker_net, net)
    return max(0.0, wall_s - main_net - worker_net), connects, \
        sum(s["execs"] for s in stats)


def build_app():
    from flask import Flask
    app = Flask("admin_probe")
    app.config["SECRET_KEY"] = secrets.token_urlsafe(48)
    app.config["DATABASE_URL"] = os.environ["DATABASE_URL"]
    app.config["TESTING"] = True
    from routes.admin import admin_bp
    from routes.admin_video import admin_video_bp
    from routes.admin_v2 import admin_v2_bp
    from routes.onboarding import onboarding_bp
    from routes.marketing_preferences import marketing_bp
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(admin_video_bp)
    app.register_blueprint(admin_v2_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(marketing_bp, url_prefix="/newsletter")
    return app


def admin_token(app, admin_id):
    import jwt
    return jwt.encode({"sub": str(admin_id), "email": ADMIN_EMAIL,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      app.config["SECRET_KEY"], algorithm="HS256")


# ── Ground truth, straight from the tables (read-only) ──────────────────────

def ground_truth(dsn):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from admin_metrics import defs
    t0 = time.perf_counter()
    conn = psycopg2.connect(dsn, cursor_factory=RealDictCursor,
                            connect_timeout=10,
                            options="-c statement_timeout=20000 " + READ_ONLY)
    conn.autocommit = True
    connect_s = time.perf_counter() - t0
    out = {"connect_s": round(connect_s, 3)}
    try:
        with conn.cursor() as cur:
            t0 = time.perf_counter()
            cur.execute("SELECT 1")
            cur.fetchone()
            out["rtt_s"] = round(time.perf_counter() - t0, 3)
            cur.execute("SHOW default_transaction_read_only")
            out["read_only"] = cur.fetchone()["default_transaction_read_only"]
            cur.execute("SELECT id FROM users WHERE LOWER(email) = %s",
                        (ADMIN_EMAIL,))
            row = cur.fetchone()
            out["admin_id"] = row["id"] if row else 0
            cur.execute(f"SELECT COUNT(*) AS n FROM users u WHERE {defs.customer('u')}")
            out["customers"] = cur.fetchone()["n"]
            cur.execute(f"""SELECT COUNT(DISTINCT p.user_id) AS n
                              FROM payments p JOIN users u ON u.id = p.user_id
                             WHERE {defs.success('p')} AND {defs.customer('u')}""")
            out["ever_paid"] = cur.fetchone()["n"]
            cur.execute(f"""SELECT COUNT(*) AS n FROM users u
                             WHERE u.is_subscribed = 1 AND {defs.scope('u')}""")
            out["subscribed_flag"] = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM projects")
            out["projects"] = cur.fetchone()["n"]
            cur.execute("""SELECT COUNT(*) AS n FROM page_visits
                            WHERE visited_at >= NOW() - INTERVAL '2 days'""")
            out["page_visits_48h"] = cur.fetchone()["n"]
    except Exception as e:                       # truth is advisory
        out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        conn.close()
    return out


# ── Checks ──────────────────────────────────────────────────────────────────

def _get(d, *path):
    for p in path:
        if isinstance(d, dict):
            d = d.get(p)
        elif isinstance(d, list) and isinstance(p, int) and -len(d) <= p < len(d):
            d = d[p]
        else:
            return None
    return d


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def need(cond, why):
    if not cond:
        raise AssertionError(why)


def first_list(body, *keys):
    for k in keys:
        v = body.get(k) if isinstance(body, dict) else None
        if isinstance(v, list):
            return v
    return None


def check_overview(b, t):
    need(_num(_get(b, "users", "total")) and b["users"]["total"] > 0,
         f"users.total={_get(b, 'users', 'total')!r}")
    if t.get("customers"):
        need(b["users"]["total"] == t["customers"],
             f"users.total {b['users']['total']} != customers {t['customers']}")
    need(_num(_get(b, "subscriptions", "total")),
         "subscriptions.total missing")
    if t.get("subscribed_flag") is not None:
        need(b["subscriptions"]["total"] == t["subscribed_flag"],
             f"subscriptions.total {b['subscriptions']['total']} != "
             f"is_subscribed {t['subscribed_flag']}")
    paddle = _get(b, "subscriptions", "paddle") or {}
    need(paddle.get("available") is not False, "paddle split unavailable")
    v = b.get("visits") or {}
    need(v.get("unique_today") is not None, "visits.unique_today is null")
    if t.get("page_visits_48h"):
        need(_num(v.get("unique_week")) and v["unique_week"] > 0,
             f"visits.unique_week={v.get('unique_week')!r} with "
             f"{t['page_visits_48h']} page visits in 48 h")
    return (f"users {b['users']['total']} · subs {b['subscriptions']['total']}"
            f" · visitors today {v.get('unique_today')} · week {v.get('unique_week')}")


def check_visits(b, t):
    rows = b.get("data") or b.get("days") or []
    need(isinstance(rows, list) and rows, "no visit days")
    return f"{len(rows)} days"


def check_rows(*keys, min_rows=1, label="rows"):
    def check(b, t):
        rows = first_list(b, *keys)
        need(rows is not None, f"no list under {keys}")
        need(len(rows) >= min_rows, f"{len(rows)} {label}")
        return f"{len(rows)} {label}"
    return check


def check_total(*keys, positive=True):
    def check(b, t):
        for k in keys:
            v = _get(b, *k.split("."))
            if _num(v):
                need(v > 0 or not positive, f"{k}={v}")
                return f"{k}={v}"
        raise AssertionError(f"none of {keys} present")
    return check


def check_revenue(b, t):
    need(_num(b.get("mrr")), f"mrr={b.get('mrr')!r}")
    if t.get("ever_paid"):
        collected = _get(b, "collected", "collected_usd")
        need(_num(collected) and collected > 0,
             f"lifetime payments {collected!r} with {t['ever_paid']} payers")
    return f"mrr {b.get('mrr')} · lifetime {_get(b, 'collected', 'collected_usd')}"


def check_subscribers(b, t):
    rows = first_list(b, "subscribers", "rows", "users")
    need(rows is not None, "no subscriber list")
    total = b.get("total")
    if t.get("ever_paid"):
        need(rows, f"empty subscriber list with {t['ever_paid']} payers")
    return f"{len(rows)} rows · total {total}"


def check_video_overview(b, t):
    need(isinstance(b, dict) and b, "empty")
    need("error" not in b, f"error: {b.get('error')}")
    return f"{len(b)} sections"


def check_dict(b, t):
    need(isinstance(b, dict) and b, "empty body")
    need(not b.get("error"), f"error: {b.get('error')}")
    return f"{len(b)} keys"


def check_users(b, t):
    rows = b.get("users") or []
    need(rows, "no users")
    if t.get("customers"):
        need(b.get("real_total") in (None, t["customers"]) or b.get("total"),
             f"real_total {b.get('real_total')} vs {t['customers']}")
    return f"{len(rows)} rows · total {b.get('total')}"


# ── The endpoint list: what /admin's tabs request on open ───────────────────
# (tab, path, check). `{project}` / `{user}` / `{job}` / `{version}` are filled
# from earlier answers.
ENDPOINTS = [
    ("Overview", "/admin/video/overview?summary=1", check_video_overview),
    ("Overview", "/admin/video/overview", check_video_overview),
    ("Overview", "/admin/video/upload_failures?days=30&limit=100", check_dict),
    ("Overview", "/admin/video/export_funnel?days=30&limit=50", check_dict),
    ("Overview", "/admin/video/reliability", check_dict),
    ("Overview", "/admin/video/settings", check_dict),
    ("Overview", "/admin/overview", check_overview),
    ("Overview", "/admin/charts/visits", check_visits),
    ("Overview", "/admin/charts/mrr", check_rows("data", label="days")),
    ("Overview", "/admin/top-users", check_rows("users")),
    ("Overview", "/admin/activity", check_rows("events")),
    ("Projects", "/admin/video/projects?search=&fields=list", check_rows("projects")),
    ("Projects", "/admin/video/projects?search=", check_rows("projects")),
    ("Projects", "/admin/video/projects/{project}?lean=1", check_dict),
    ("Projects", "/admin/video/projects/{project}/llm_calls?page=1", check_dict),
    ("Projects", "/admin/video/projects/{project}/index", None),
    ("Subscribers", "/admin/video/subscribers?search=&status=all&page=1&per_page=50", check_subscribers),
    ("Subscriber Projects", "/admin/video/subscriber-projects?search=&page=1&per_page=50", check_rows("projects", "rows")),
    ("Exports", "/admin/video/exports?days=30&page=1&per_page=50&search=", check_dict),
    ("Waits", "/admin/video/timings?days=90", check_dict),
    ("People", "/admin/video/users?search=&page=1&per_page=100", check_rows("users")),
    ("People", "/admin/video/users/{user}", check_dict),
    ("People", "/admin/v2/customers/{user}", check_dict),
    ("Agent I/O", "/admin/video/projects", check_rows("projects")),
    ("Redesign", "/admin/video/redesign-observability?days=30", check_dict),
    ("Costs", "/admin/video/costs", check_dict),
    ("Cohorts", "/admin/video/cohorts?period=week", check_dict),
    ("Survey", "/admin/onboarding", check_dict),
    ("Survey", "/admin/plan-intents", check_dict),
    ("Users", "/admin/users?page=1&per_page=100&filter=all&search=", check_users),
    ("Users", "/admin/charts/growth", check_rows("data", label="days")),
    ("Traffic", "/admin/engagement", check_dict),
    ("Traffic", "/admin/retention", check_dict),
    ("Traffic", "/admin/page-analytics", check_dict),
    ("Traffic", "/admin/country-stats", check_dict),
    ("Traffic", "/admin/session-stats", check_dict),
    ("Traffic", "/admin/outreach-codes?days=30", check_dict),
    ("Revenue", "/admin/billing", check_dict),
    ("Revenue", "/admin/revenue", check_revenue),
    ("Newsletter", "/newsletter/kit-status", check_dict),
    ("Live", "/admin/realtime", check_dict),
]


def pick_ids(bodies):
    ids = {}
    projects = (bodies.get("/admin/video/projects?search=") or {}).get("projects") or []
    for p in projects:
        pid = p.get("id") or p.get("project_id")
        if pid:
            ids["project"] = pid
            break
    users = (bodies.get("/admin/video/users?search=&page=1&per_page=100") or {}).get("users") or []
    for u in users:
        uid = u.get("id") or u.get("user_id")
        if uid:
            ids["user"] = uid
            break
    return ids


def _call(client, path, headers, rtt_s, measure):
    reset_stats()
    t0 = time.perf_counter()
    try:
        resp = client.get(path, headers=headers)
        status, raw = resp.status_code, resp.get_data()
    except Exception as e:
        status, raw = None, f"{type(e).__name__}: {e}".encode()
    wall = time.perf_counter() - t0
    est, connects, execs = server_estimate(wall, snapshot_stats(), rtt_s)
    body = None
    try:
        body = json.loads(raw) if raw else None
    except Exception:
        pass
    return {"status": status, "raw": raw, "body": body, "wall": wall,
            "seconds": est if measure == "server" else wall,
            "connects": connects, "execs": execs}


def _with_fresh(path):
    return path + ("&" if "?" in path else "?") + "fresh=1"


def _wait_for_background(limit_s=150):
    """Heavy reports compute on a background thread (admin_metrics.cache).
    Wait for it, so it neither slows the next measurement nor hides a
    report that cannot be computed. Returns (seconds waited, failures)."""
    from admin_metrics import cache
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < limit_s:
        with cache._shared_lock:
            running = list(cache._shared_running.values())
        if not running:
            break
        running[0].wait(1.0)
    with cache._shared_lock:
        failed = {k: v[1] for k, v in cache._shared_failed.items()}
        still = bool(cache._shared_running)
    return time.perf_counter() - t0, failed, still


ALLOWED_STATUS = {"/index": (200, 404)}     # a project may have no index yet


def run(budget_s, only=None, repeat=1, measure="server", retries=2):
    force_read_only()
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn or "stub" in dsn:
        print("admin_probe: DATABASE_URL is not set to the production database",
              file=sys.stderr)
        sys.exit(2)
    truth = ground_truth(dsn)
    if truth.get("read_only") != "on":
        print("admin_probe: refusing to run, the session is not read-only",
              file=sys.stderr)
        sys.exit(2)
    app = build_app()
    client = app.test_client()
    headers = {"Authorization": "Bearer " + admin_token(app, truth.get("admin_id") or 0)}
    rtt = truth.get("rtt_s") or 0
    results, bodies = [], {}
    for tab, path, check in ENDPOINTS:
        if only and only.lower() not in (tab + " " + path).lower():
            continue
        if "{" in path:
            try:
                path = path.format(**pick_ids(bodies))
            except KeyError as missing:
                results.append({"tab": tab, "path": path, "status": None,
                                "seconds": None, "ok": False,
                                "note": f"no {missing} id to try"})
                continue
        first = _call(client, path, headers, rtt, measure)
        best, attempts = first, 1
        # The shared database's speed swings 2-5x from minute to minute; an
        # endpoint is slow only if it stays over budget when recomputed
        # (?fresh=1 bypasses the admin caches).
        while best["seconds"] > budget_s and attempts <= retries:
            _wait_for_background()
            if attempts > 1:
                time.sleep(10.5)     # ?fresh=1 recomputes at most every 10 s
            again = _call(client, _with_fresh(path), headers, rtt, measure)
            attempts += 1
            # an answer served from a cache measures nothing
            if again["execs"] and again["status"] == best["status"] \
                    and again["seconds"] < best["seconds"]:
                best = again
        warm = [_call(client, path, headers, rtt, measure)["seconds"]
                for _ in range(max(0, repeat - 1))]
        waited, failed, still = _wait_for_background()
        body, status = first["body"], first["status"]
        bodies[path] = body
        allowed = next((v for k, v in ALLOWED_STATUS.items() if k in path), (200,))
        ok, note = status in allowed, ""
        if status != 200:
            msg = body.get("error") if isinstance(body, dict) else first["raw"][:120]
            note = f"HTTP {status}: {msg}"
        elif isinstance(body, dict) and body.get("pending"):
            # The page shows "computing" and asks again; judge what it gets.
            if failed:
                ok, note = False, "background compute failed: " + \
                    "; ".join(sorted(set(failed.values())))
            elif still:
                ok, note = False, f"still computing after {waited:.0f}s"
            else:
                again = _call(client, path, headers, rtt, measure)
                body = again["body"]
                note = f"computed in the background in {waited:.1f}s"
                if again["status"] != 200 or (isinstance(body, dict) and body.get("pending")):
                    ok, note = False, note + ", but the next answer was not ready"
        if ok and status == 200 and check is not None:
            try:
                extra = check(body, truth) or ""
                note = (note + " · " if note else "") + extra
            except AssertionError as e:
                ok, note = False, f"implausible: {e}"
            except Exception as e:
                ok, note = False, f"check crashed: {type(e).__name__}: {e}"
        if ok and isinstance(body, dict) and body.get("cache_age_s") is not None:
            age = int(body["cache_age_s"])
            note += f" · cached {age // 60} min ago" + (
                ", refreshing" if body.get("refreshing") else "")
            if failed and body.get("refreshing"):
                ok, note = False, note + " · its refresh failed: " + \
                    "; ".join(sorted(set(failed.values())))
        if best["seconds"] > budget_s:
            ok = False
            note = (note + " · " if note else "") + (
                f"over {budget_s:g}s" + (f" in all {attempts} tries" if attempts > 1 else ""))
        elif attempts > 1:
            note += f" · {attempts} tries, first {first['seconds']:.1f}s"
        results.append({"tab": tab, "path": path, "status": status,
                        "seconds": round(best["seconds"], 3),
                        "wall_seconds": round(best["wall"], 3),
                        "queries": best["execs"], "connections": best["connects"],
                        "warm_seconds": round(min(warm), 3) if warm else None,
                        "bytes": len(first["raw"] or b""), "ok": ok,
                        "note": note})
        from admin_metrics import cache
        with cache._shared_lock:
            cache._shared_failed.clear()
    return truth, results


def production_commit():
    try:
        import urllib.request
        with urllib.request.urlopen(
                "https://entrepreneur-bot-backend.onrender.com/healthz",
                timeout=10) as r:
            return json.loads(r.read() or b"{}").get("commit")
    except Exception as e:
        try:
            return json.loads(e.read() or b"{}").get("commit")   # 503 body
        except Exception:
            return None


def local_commit():
    try:
        import subprocess
        return subprocess.run(["git", "-C", BACKEND, "rev-parse", "--short=12", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budget", type=float, default=3.0,
                    help="seconds an endpoint may take (default 3)")
    ap.add_argument("--only", help="substring of tab or path to run")
    ap.add_argument("--repeat", type=int, default=1,
                    help="call each endpoint N times (first = cold)")
    ap.add_argument("--retries", type=int, default=2,
                    help="recompute an over-budget endpoint up to N times "
                         "(?fresh=1) before calling it slow (default 2)")
    ap.add_argument("--measure", choices=("server", "wall"), default="server",
                    help="judge the budget on the estimated time next to the "
                         "database (default) or on this machine's wall clock")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    started = time.perf_counter()
    truth, results = run(args.budget, args.only, args.repeat, args.measure,
                         args.retries)
    failed = [r for r in results if not r["ok"]]
    meta = {"code": local_commit(), "production": production_commit(),
            "db_rtt_s": truth.get("rtt_s"), "db_connect_s": truth.get("connect_s"),
            "truth": {k: v for k, v in truth.items()
                      if k not in ("rtt_s", "connect_s", "read_only", "admin_id")},
            "budget_s": args.budget, "measure": args.measure,
            "endpoints": len(results),
            "failed": len(failed),
            "total_s": round(time.perf_counter() - started, 1)}
    if args.json:
        print(json.dumps({"meta": meta, "results": results}, indent=1, default=str))
    else:
        print(f"admin probe · code {meta['code']} · production {meta['production']}"
              f" · db rtt {meta['db_rtt_s']}s · connect {meta['db_connect_s']}s")
        print(f"truth: {json.dumps(meta['truth'], default=str)}")
        for r in results:
            mark = "PASS" if r["ok"] else "FAIL"
            secs = "  -  " if r["seconds"] is None else f"{r['seconds']:5.2f}s"
            wall = ("" if r.get("wall_seconds") is None else
                    f" (wall {r['wall_seconds']:5.2f}s {r['queries']:>3}q {r['connections']}c)")
            warm = f" warm {r['warm_seconds']:.2f}s" if r.get("warm_seconds") is not None else ""
            print(f"{mark} {secs}{wall}{warm} {str(r['status'] or '-'):>3} "
                  f"{r['tab']:<19} {r['path'][:70]:<70} {r['note']}")
        print(f"{len(results) - len(failed)}/{len(results)} passed in "
              f"{meta['total_s']}s (budget {args.budget:g}s per endpoint, "
              f"{'estimated next to the database' if args.measure == 'server' else 'wall clock here'})")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
