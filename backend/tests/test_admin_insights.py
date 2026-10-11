"""Activation, engagement, audience and exports (admin_metrics.insights), the
parallel section runner and the stale-while-revalidate cache.

No database: cursors are scripted fakes, as in test_admin_v2_api.py.
"""
import threading
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from flask import Flask

from admin_metrics import cache, db, insights, ranges, registry
from routes import admin_v2
from tests.test_admin_v2_api import (ADMIN, FakeConn, FakeCursor, SECRET,
                                     assert_metric, get, token)
import website_analytics as wa


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SECRET, DATABASE_URL="postgresql://stub/stub",
                      ADMIN_PARALLEL_SECTIONS=False)
    app.register_blueprint(admin_v2.admin_v2_bp)
    cache.clear()
    db.reset_features()
    app.fake = FakeCursor()
    monkeypatch.setattr(db, "conn", lambda: FakeConn(app.fake))
    monkeypatch.setattr(admin_v2, "_tracking_meta", lambda cur=None: {
        "visits_since": "2026-10-03T16:07:35Z",
        "sources_since": "2026-10-07T12:34:12Z",
        "signup_link_since": "2026-10-03T19:29:50Z",
        "interaction_since": None, "reasons_since": None,
        "snapshots_since": None})
    yield app
    cache.clear()
    db.reset_features()


# ── The parallel section runner ──────────────────────────────────────────
def test_parallel_runs_each_task_once_on_its_own_connections(monkeypatch):
    app = Flask(__name__)
    app.config.update(DATABASE_URL="postgresql://stub/stub")
    opened = []

    class Conn(FakeConn):
        pass

    def open_readonly(timeout_ms=0):
        c = Conn(FakeCursor())
        opened.append(c)
        return c
    monkeypatch.setattr(db, "open_readonly", open_readonly)
    seen = []
    lock = threading.Lock()

    def task(n):
        def fn(cur):
            with lock:
                seen.append((n, threading.current_thread().name))
            time.sleep(0.01)
            if n == 3:
                raise ValueError("boom")
            return n * 10
        return fn

    with app.app_context():
        out = db.parallel([(f"t{n}", task(n)) for n in range(8)], workers=3)
    assert sorted(out) == [f"t{n}" for n in range(8)]
    assert out["t2"] == (True, 20)
    ok, err = out["t3"]
    assert not ok and isinstance(err, ValueError)     # isolated, not raised
    assert len(seen) == 8                              # each task ran once
    assert len(opened) == 3 and all(c.closed for c in opened)
    assert len({name for _, name in seen}) <= 3


def test_parallel_falls_back_to_the_request_connection_when_switched_off(
        monkeypatch):
    app = Flask(__name__)
    app.config.update(DATABASE_URL="x", ADMIN_PARALLEL_SECTIONS=False)
    cur = FakeCursor()
    monkeypatch.setattr(db, "conn", lambda: FakeConn(cur))
    monkeypatch.setattr(db, "open_readonly", lambda *a, **k: pytest.fail(
        "must not open extra connections"))
    with app.app_context():
        out = db.parallel([("a", lambda c: c is cur), ("b", lambda c: 2)])
    assert out == {"a": (True, True), "b": (True, 2)}


def test_a_connection_that_cannot_open_fails_only_its_tasks(monkeypatch):
    app = Flask(__name__)
    app.config.update(DATABASE_URL="x")
    monkeypatch.setattr(db, "open_readonly", lambda *a, **k: (_ for _ in ())
                        .throw(RuntimeError("db down")))
    with app.app_context():
        out = db.parallel([("a", lambda c: 1), ("b", lambda c: 2)])
    assert all(not ok for ok, _ in out.values())


# ── Stale-while-revalidate ───────────────────────────────────────────────
def test_an_expired_answer_is_served_at_once_and_refreshed_behind(monkeypatch):
    app = Flask(__name__)
    cache.clear()
    calls = []

    def compute():
        calls.append(1)
        return {"n": len(calls)}
    with app.app_context():
        v, _, _ = cache.get_or_compute("x", {}, 60, compute, stale_s=600)
        assert v == {"n": 1}
        # Expire it.
        key = cache.make_key("x", {})
        exp, at, val = cache._entries[key]
        cache._entries[key] = (time.monotonic() - 1, at, val)
        v, _, age = cache.get_or_compute("x", {}, 60, compute, stale_s=600)
        assert v == {"n": 1}                    # the old answer, no waiting
        for _ in range(100):
            if cache._entries[key][2] == {"n": 2}:
                break
            time.sleep(0.01)
        assert cache._entries[key][2] == {"n": 2}   # refreshed behind
        assert cache._entries[key][0] > time.monotonic()
    cache.clear()


def test_a_failed_background_refresh_keeps_the_last_good_answer():
    app = Flask(__name__)
    cache.clear()
    answers = iter([{"ok": 1}, {"ok": 0, "errors": ["x"]}])
    with app.app_context():
        cache.get_or_compute("y", {}, 60, lambda: next(answers), stale_s=600)
        key = cache.make_key("y", {})
        exp, at, val = cache._entries[key]
        cache._entries[key] = (time.monotonic() - 1, at, val)
        cache.get_or_compute("y", {}, 60, lambda: next(answers), stale_s=600,
                             keep=lambda v: not v.get("errors"))
        for _ in range(50):
            if key not in cache._refreshing:
                break
            time.sleep(0.01)
        assert cache._entries[key][2] == {"ok": 1}
    cache.clear()


def test_too_old_an_answer_is_recomputed_while_you_wait():
    app = Flask(__name__)
    cache.clear()
    n = iter(range(1, 10))
    with app.app_context():
        cache.get_or_compute("z", {}, 60, lambda: next(n), stale_s=5)
        key = cache.make_key("z", {})
        exp, at, val = cache._entries[key]
        cache._entries[key] = (time.monotonic() - 10, at, val)
        v, _, _ = cache.get_or_compute("z", {}, 60, lambda: next(n), stale_s=5)
    assert v == 2
    cache.clear()


# ── Activation ───────────────────────────────────────────────────────────
def test_firsts_count_in_the_period_they_happened():
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)   # 14:00 Dubai
    period = ranges.parse({"range": "today"}, now=now)
    comp = period.compare()
    firsts = [("upload", 1, datetime(2026, 10, 11, 9, tzinfo=timezone.utc)),
              ("upload", 2, datetime(2026, 10, 10, 9, tzinfo=timezone.utc)),
              # yesterday after "this time" is not in the comparison
              ("upload", 3, datetime(2026, 10, 10, 11, tzinfo=timezone.utc)),
              # 22:00 UTC on the 10th is 02:00 on the 11th in Dubai
              ("edit", 4, datetime(2026, 10, 10, 22, tzinfo=timezone.utc))]
    assert insights.count_firsts(firsts, period) == {
        "upload": 1, "edit": 1, "export": 0}
    assert insights.count_firsts(firsts, comp)["upload"] == 1
    days = [date(2026, 10, 10), date(2026, 10, 11)]
    by = insights.firsts_by_day(firsts, days)
    assert by["upload"] == {date(2026, 10, 11): 1, date(2026, 10, 10): 2}
    assert by["edit"] == {date(2026, 10, 11): 1}


def test_firsts_sql_reads_only_the_window_and_excludes_earlier_doers():
    sql = " ".join(insights._FIRSTS_SQL.split())
    assert "AND NOT CASE f.kind" in sql
    assert "a.created_at < %(since_tz)s" in sql
    assert "cm.created_at < %(since)s" in sql      # naive UTC column
    assert "vj.updated_at < %(since_tz)s" in sql


# ── Engagement ───────────────────────────────────────────────────────────
def test_engagement_dau_wau_mau_segments_and_retention(monkeypatch):
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    period = ranges.parse({"range": "30d"}, now=now)
    end = period.to_day
    pairs = {(1, end), (1, end - timedelta(days=1)), (2, end - timedelta(days=3)),
             (3, end - timedelta(days=20)), (4, end - timedelta(days=40))}
    # customer 5 is active on 12 days → "almost daily"
    pairs |= {(5, end - timedelta(days=i)) for i in range(12)}
    monkeypatch.setattr(insights, "active_pairs", lambda cur, p: pairs)
    joined = datetime(2026, 10, 6, 8)        # Tuesday of the current week
    cur = FakeCursor([("SELECT count(*) AS n FROM users u", [{"n": 100}]),
                      ("SELECT u.id, u.created_at FROM users u", [
                          {"id": 1, "created_at": joined},
                          {"id": 9, "created_at": joined}])])
    out = insights.engagement(cur, period)
    tiles = {t["key"]: t for t in out["tiles"]}
    for t in out["tiles"]:
        assert_metric(t)
    assert tiles["dau"]["value"] == 2                     # 1 and 5
    assert tiles["wau"]["value"] == 3                     # 1, 2, 5
    assert tiles["mau"]["value"] == 4                     # 1, 2, 3, 5
    seg = {s["key"]: s["customers"] for s in out["segments"]}
    assert seg == {"daily": 1, "regular": 0, "occasional": 1, "once": 2,
                   "inactive": 96}
    assert len(out["active_by_day"]) == 30
    assert out["active_by_day"][-1] == {"date": end.isoformat(), "active": 2}
    first = out["retention"]["rows"][0]
    assert first["signups"] == 2
    assert first["cells"][0]["active"] == 1 and first["cells"][0]["pct"] == 50.0
    assert first["cells"][0]["partial"] is True


def test_engagement_page_contract(app, monkeypatch):
    monkeypatch.setattr(insights, "engagement", lambda cur, p: {
        "tiles": [registry.metric("dau", 3, 2)], "active_by_day": [],
        "segments": [], "customers_total": 10,
        "retention": {"columns": 8, "rows": []}})
    monkeypatch.setattr(insights, "top_customers", lambda cur, p: [
        {"customer": {"id": 7, "email": "customer1@example.com",
                      "plan": "ai", "status": "paying"},
         "credits_used": 50.0, "turns": 4, "exports": 1, "projects": 2,
         "paid_usd": 15.0, "billing_period": "monthly",
         "joined_at": "2026-10-01T00:00:00Z"}])
    monkeypatch.setattr(insights, "activity_feed", lambda cur: [])
    r = get(app, "/admin/v2/engagement?range=30d")
    assert r.status_code == 200
    d = r.json["data"]
    assert set(d) == {"engagement", "top_customers", "activity"}
    assert d["top_customers"][0]["customer"]["email"] == \
        "customer1@example.com"
    assert r.json["meta"]["section_errors"] == []


def test_one_failed_engagement_part_never_blanks_the_others(app, monkeypatch):
    monkeypatch.setattr(insights, "engagement", lambda cur, p: 1 / 0)
    monkeypatch.setattr(insights, "top_customers", lambda cur, p: [])
    monkeypatch.setattr(insights, "activity_feed", lambda cur: [])
    r = get(app, "/admin/v2/engagement")
    assert r.status_code == 200
    assert r.json["data"]["engagement"] is None
    assert r.json["data"]["top_customers"] == []
    assert r.json["meta"]["section_errors"][0]["section"] == "engagement"


# ── Audience ─────────────────────────────────────────────────────────────
def test_audience_counts_people_only_and_hours_in_the_admin_timezone():
    now = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
    period = ranges.parse({"range": "7d"}, now=now)
    rows = [
        {"country": "AE", "device_type": "mobile", "browser": "Safari",
         "first_at": datetime(2026, 10, 10, 20, 30), "pages": 3,
         "active_s": 40},
        {"country": None, "device_type": "desktop", "browser": "Chrome",
         "first_at": datetime(2026, 10, 10, 5, 0), "pages": 1,
         "active_s": 0},
        {"country": "AE", "device_type": "desktop", "browser": "Chrome",
         "first_at": datetime(2026, 10, 9, 5, 10), "pages": 2,
         "active_s": 10}]
    cur = FakeCursor([("FROM d WHERE", rows), ("FROM page_visits", [])])
    db.reset_features()
    out = insights.audience(cur, period)
    sql = " ".join(cur.sql[-1].split())
    assert "= 'person'" in sql                      # robots never counted
    assert out["people"] == 3
    assert out["countries"] == [{"key": "AE", "people": 2}]
    assert out["countries_unknown"] == 1
    assert out["devices"][0] == {"key": "desktop", "people": 2}
    hours = {h["hour"]: h["people"] for h in out["hours"] if h["people"]}
    assert hours == {0: 1, 9: 2}                    # Dubai is UTC+4
    assert out["pages_per_person"] == 2.0 and out["median_active_s"] == 25


# ── Exports ──────────────────────────────────────────────────────────────
def exports_routes():
    t0 = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)
    return [
        ("GROUPING SETS", lambda p: [
            {"day": None, "done": 3, "failed": 1, "people": 2, "seconds": 180.0,
             "render_median_s": 61.0},
            {"day": date(2026, 10, 10), "done": 3, "failed": 1, "people": 2,
             "seconds": 180.0, "render_median_s": 61.0}]),
        ("AS people FROM video_jobs vj JOIN users u ON u.id = vj.user_id "
         "WHERE vj.type = 'final' AND vj.state IN", [
             {"done": 1, "failed": 0, "people": 1}]),
        ("fs_rows FROM video_jobs vj", [{
            "id": 90, "user_id": 7, "project_id": 11, "created_at": t0,
            "updated_at": t0, "result": {"timings": {"total_s": 61.2}},
            "title": "Real edit", "parent_id": None,
            "email": "customer1@example.com", "plan": "ai", "status": "paying",
            "storage_key": "media/11/90-x.mp4", "duration_s": 32.5,
            "fs_key": None, "fs_cols": None, "fs_rows": None}]),
        ("AS retried_ok", [{
            "id": 91, "user_id": 7, "project_id": 11, "updated_at": t0,
            "error": "ffmpeg exited 1", "attempts": 2, "title": "Real edit",
            "email": "customer1@example.com", "plan": "ai", "status": "paying",
            "retried_ok": True}]),
        ("vj.type = 'preview'", [{"projects": 5, "people": 4}]),
        ("SELECT ce.kind, count(*) AS events", [
            {"kind": "export_clicked", "events": 6, "people": 3,
             "projects": 3}]),
    ]


def test_exports_page_contract(app, monkeypatch):
    app.fake.routes = exports_routes()
    import routes.admin_video as av
    monkeypatch.setattr(av, "_presign", lambda key: f"https://r2/{key}")
    r = get(app, "/admin/v2/exports?range=7d")
    assert r.status_code == 200 and r.json["meta"]["section_errors"] == []
    d = r.json["data"]
    tiles = {t["key"]: t for t in d["tiles"]}
    for t in d["tiles"]:
        assert_metric(t)
    assert tiles["exports"]["value"] == 3 and tiles["exports"]["previous"] == 1
    assert tiles["failed_exports"]["value"] == 1
    assert tiles["export_success_rate"]["value"] == 75.0
    assert tiles["exported_minutes"]["value"] == 3.0
    assert len(d["by_day"]) == 7
    row = d["recent"][0]
    assert row["customer"]["email"] == "customer1@example.com"
    assert row["url"] == "https://r2/media/11/90-x.mp4"
    assert row["thumb"] is None and row["length_s"] == 32.5
    assert d["failed"][0]["fixed_by_retry"] is True
    assert [s["key"] for s in d["funnel"]][:2] == ["previewed",
                                                  "export_clicked"]
    assert d["funnel"][0] == {"key": "previewed", "label": "Saw a preview",
                              "people": 4, "projects": 5}
    assert d["render_median_s"] == 61.0


# ── Country from the edge, never from an IP ──────────────────────────────
def test_edge_country_reads_only_vercels_header():
    assert wa.edge_country({"X-Vercel-IP-Country": "ae"}) == "AE"
    assert wa.edge_country({"CF-IPCountry": "US"}) is None
    assert wa.edge_country({"X-Vercel-IP-Country": "XX"}) is None
    assert wa.edge_country({"X-Vercel-IP-Country": "United States"}) is None
    assert wa.edge_country({}) is None


def test_the_tracker_stores_the_country_code_and_no_ip(monkeypatch):
    from tests.test_website_analytics import (MIGRATED, _SchemaConn,
                                              _SchemaCursor, data)
    cur = _SchemaCursor(MIGRATED)
    monkeypatch.setattr(wa, "connect", lambda: _SchemaConn(cur))
    wa.reset_schema_cache()
    assert wa.save_visit(data(interacted=True), "Mozilla/5.0", "AE") is True
    sql, params = next((s, p) for s, p in cur.sql
                       if "INSERT INTO page_visits" in s)
    assert "'Unknown'" not in sql and "NULL" in sql      # ip stays NULL
    assert params[5] == "AE"
    assert wa.save_visit(data(interacted=True), "Mozilla/5.0", "<x>") is True
    params = [p for s, p in cur.sql if "INSERT INTO page_visits" in s][-1]
    assert params[5] == "Unknown"
    wa.reset_schema_cache()


def test_activity_feed_contract(app):
    t0 = datetime(2026, 10, 10, 9)
    app.fake.routes = [
        ("AS kind, u.created_at", [
            {"kind": "signup", "at": t0, "user_id": 7, "project_id": None,
             "title": None, "amount": None, "detail": "google"},
            {"kind": "payment", "at": t0, "user_id": 7, "project_id": None,
             "title": "ai", "amount": 15.0, "detail": "USD"},
            {"kind": "upload", "at": t0, "user_id": 7, "project_id": 11,
             "title": "Trip", "amount": 61.04, "detail": "trip.mp4"}]),
        ("FROM users u WHERE u.id = ANY", [
            {"id": 7, "email": "customer1@example.com", "plan": "ai",
             "status": "paying"}]),
    ]
    r = get(app, "/admin/v2/activity?limit=10")
    rows = r.json["data"]["rows"]
    assert [x["kind"] for x in rows] == ["signup", "payment", "upload"]
    assert rows[0]["customer"]["email"] == "customer1@example.com"
    assert rows[1]["amount"] == 15.0 and rows[1]["currency"] == "USD"
    assert rows[2]["detail"] == "trip.mp4" and rows[2]["length_s"] == 61.0
    assert get(app, "/admin/v2/activity?limit=0").status_code == 400
