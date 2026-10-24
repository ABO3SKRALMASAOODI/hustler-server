"""Admin read paths stay fast (24 Oct 2026 probe): indexes that match the
queries exactly, heavy reports that never make the page wait, bounded
per-user pages, and the probe that watches all of it."""
import inspect
import os
import re
import sys
import threading
import time
from pathlib import Path

import psycopg2
import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_metrics import cache, visitors  # noqa: E402
from routes import admin, admin_video  # noqa: E402
from routes.admin_video import (  # noqa: E402
    _mcp_non_success_sql, _mcp_refusal_sql, video_projects,
    video_user_detail)

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = (BACKEND / "migrations" / "039_admin_read_indexes.sql").read_text()


def _norm(sql):
    return " ".join(sql.replace("%%", "%").split())


def _index_predicate(name):
    m = re.search(rf"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name}\s+ON video_jobs \(project_id\)\s+"
                  r"WHERE type = 'mcp_tool'\s+AND (.*?);", MIGRATION, re.S)
    assert m, name
    return _norm(m.group(1))


def test_tool_outcome_indexes_match_the_admin_predicates_exactly():
    # The planner uses a partial index only when the query repeats its
    # predicate; if the predicate changes, the migration must change with it.
    assert _index_predicate("idx_video_jobs_mcp_non_success") == \
        _norm(_mcp_non_success_sql(""))
    assert _index_predicate("idx_video_jobs_mcp_refusal") == \
        _norm(_mcp_refusal_sql(""))
    assert "ON video_jobs ((payload->>'message_id'))" in MIGRATION
    assert "WHERE type = 'agent_turn'" in MIGRATION
    assert "vj.type = 'agent_turn'" in admin_video.UNSERVED_EXISTS
    assert "vj.payload->>'message_id' = cm.id::text" in admin_video.UNSERVED_EXISTS


def test_project_list_counts_tool_outcomes_with_where_not_filter():
    source = inspect.getsource(inspect.unwrap(video_projects))
    assert "COUNT(*) FILTER" not in source.split("tool_lateral", 2)[1].split(
        "tool_cols", 1)[0]
    assert "AND {_mcp_non_success_sql('mt')}) AS tool_failed" in source
    assert "AND {_mcp_refusal_sql('mt')}) AS tool_rejected" in source


def test_user_detail_is_bounded_and_reports_the_total():
    source = inspect.getsource(inspect.unwrap(video_user_detail))
    assert "LIMIT %s" in source and "USER_DETAIL_PROJECTS" in source
    assert '"project_total"' in source
    assert admin_video.USER_DETAIL_PROJECTS == 200


def test_robot_names_are_tested_once_per_distinct_browser():
    class Cur:
        def execute(self, *a, **k):
            raise AssertionError("feature probes are cached")
    cte = visitors.browsers_cte.__wrapped__(Cur()) if hasattr(
        visitors.browsers_cte, "__wrapped__") else None
    source = inspect.getsource(visitors.browsers_cte)
    assert "SELECT DISTINCT v.user_agent FROM v" in source
    assert "bool_or(ua.robot) AS robot" in source
    assert "v.user_agent ~*" not in source
    assert "ua AS MATERIALIZED" in source
    assert cte is None or "%(robot_ua)s" in cte


# ── shared heavy reports never make the page wait ──────────────────────────

class _Conn:
    def __init__(self, stored=None):
        self.stored = stored
        self.autocommit = False
        self.writes = []

    def cursor(self, *a, **k):
        conn = self

        class C:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, sql, params=None):
                self.sql = sql
                if "INSERT INTO app_kv" in sql:
                    conn.writes.append(params)

            def fetchone(self):
                if "pg_try_advisory_lock" in self.sql:
                    return {"got": True}
                if "FROM app_kv" in self.sql:
                    return {"value": conn.stored} if conn.stored else None
                return None
        return C()

    def close(self):
        pass


@pytest.fixture
def app():
    a = Flask(__name__)
    a.config.update(SECRET_KEY="k" * 40, DATABASE_URL="postgresql://stub/stub")
    cache._shared_local.clear()
    cache._shared_failed.clear()
    cache._shared_running.clear()
    yield a
    for ev in list(cache._shared_running.values()):
        ev.wait(5)
    cache._shared_local.clear()
    cache._shared_failed.clear()


def _stored(age_s, value=1):
    import json
    from datetime import datetime, timedelta, timezone
    at = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")
    return json.dumps({"n": value, "computed_at": at})


def test_an_old_copy_is_served_at_once_and_refreshed_behind_it(app, monkeypatch):
    conn = _Conn(_stored(3600, value=1))
    monkeypatch.setattr(cache, "_writer", lambda: conn)
    started = threading.Event()
    release = threading.Event()

    def compute():
        started.set()
        assert cache.in_background()
        assert cache.statement_timeout_ms(20000) == cache.BACKGROUND_TIMEOUT_MS
        release.wait(5)
        return {"n": 2}
    with app.app_context():
        t0 = time.monotonic()
        out = cache.shared_report("/r", {}, compute)
        assert time.monotonic() - t0 < 1
        assert out["n"] == 1 and out["refreshing"] and out["cache_age_s"] >= 3600
        assert started.wait(5)
        release.set()
        for ev in list(cache._shared_running.values()):
            ev.wait(5)
        fresh = cache.shared_report("/r", {}, compute)
    assert fresh["n"] == 2 and not fresh.get("refreshing")
    assert conn.writes, "the new copy is stored for every process"


def test_no_copy_and_a_slow_report_answers_pending(app, monkeypatch):
    monkeypatch.setattr(cache, "_writer", lambda: _Conn())
    monkeypatch.setattr(cache, "SHARED_WAIT_S", 0.05)
    release = threading.Event()

    def compute():
        release.wait(5)
        return {"n": 3}
    with app.app_context():
        out = cache.shared_report("/slow", {}, compute)
        assert out["pending"] and out["retry_after_s"] == 5
        release.set()
        for ev in list(cache._shared_running.values()):
            ev.wait(5)
        assert cache.shared_report("/slow", {}, compute)["n"] == 3


def test_a_report_that_cannot_compute_is_a_retryable_503(app, monkeypatch):
    monkeypatch.setattr(cache, "_writer", lambda: _Conn())

    def compute():
        raise psycopg2.errors.QueryCanceled("canceling statement due to timeout")
    with app.app_context():
        with pytest.raises(cache.ReportUnavailable):
            cache.shared_report("/broken", {}, compute)
        status = admin.legacy_error_response(cache.ReportUnavailable("x."))
        assert status[1] == 503 and status[0].json["retryable"]


def test_background_connections_stay_read_only():
    # No request context: the admin connection helpers pick read-only for a
    # background refresh, never a write connection.
    seen = {}

    def fake_connect(dsn, **kw):
        seen.update(kw)
        raise psycopg2.OperationalError("stop")
    app = Flask(__name__)
    app.config["DATABASE_URL"] = "postgresql://stub/stub"
    orig = psycopg2.connect
    psycopg2.connect = fake_connect
    try:
        with app.app_context():
            cache._background.active = True
            try:
                with pytest.raises(psycopg2.OperationalError):
                    admin.get_db()
                assert "default_transaction_read_only=on" in seen["options"]
                assert "statement_timeout=%d" % cache.BACKGROUND_TIMEOUT_MS in seen["options"]
                seen.clear()
                with pytest.raises(psycopg2.OperationalError):
                    with admin_video.adb():
                        pass
                assert "default_transaction_read_only=on" in seen["options"]
            finally:
                cache._background.active = False
    finally:
        psycopg2.connect = orig


# ── the probe ───────────────────────────────────────────────────────────────

def test_probe_covers_every_admin_tab_and_forces_read_only():
    sys.path.insert(0, str(BACKEND / "scripts"))
    import admin_probe
    tabs = {tab for tab, _path, _check in admin_probe.ENDPOINTS}
    for tab in ("Overview", "Projects", "Subscribers", "Subscriber Projects",
                "Exports", "Waits", "People", "Agent I/O", "Redesign", "Costs",
                "Cohorts", "Survey", "Users", "Traffic", "Revenue",
                "Newsletter", "Live"):
        assert tab in tabs, tab
    assert all(not p.startswith("/newsletter/campaigns")
               for _t, p, _c in admin_probe.ENDPOINTS), "calls Brevo"
    seen = {}

    def fake_connect(dsn=None, connection_factory=None, cursor_factory=None, **kw):
        seen.update(kw)
        return None
    orig = psycopg2.connect
    psycopg2.connect = fake_connect
    try:
        admin_probe.force_read_only()
        psycopg2.connect("postgresql://stub/stub",
                         options="-c default_transaction_read_only=off")
        assert seen["options"].endswith("-c default_transaction_read_only=on")
    finally:
        psycopg2.connect = orig


def test_probe_sanity_checks_reject_fake_zeros():
    sys.path.insert(0, str(BACKEND / "scripts"))
    import admin_probe
    truth = {"customers": 10, "subscribed_flag": 2, "page_visits_48h": 5}
    good = {"users": {"total": 10}, "subscriptions": {"total": 2, "paddle": {}},
            "visits": {"unique_today": 0, "unique_week": 4}}
    assert "users 10" in admin_probe.check_overview(good, truth)
    for bad in ({"users": {"total": 0}, "subscriptions": {"total": 2}, "visits": {}},
                dict(good, visits={"unique_today": None, "unique_week": 4}),
                dict(good, subscriptions={"total": 0, "paddle": {}})):
        with pytest.raises(AssertionError):
            admin_probe.check_overview(bad, truth)
