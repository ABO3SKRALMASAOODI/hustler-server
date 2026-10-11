"""The old admin keeps working while it is replaced, but can no longer hurt:
time limits, JSON errors, no DDL on GET, bounded pages, honest pagers."""
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import jwt
import psycopg2
import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_metrics import cache  # noqa: E402
from routes import admin, admin_video  # noqa: E402
import routes.newsletter as newsletter  # noqa: E402
import routes.onboarding as onboarding  # noqa: E402

SECRET = "k" * 40
DDL = ("CREATE ", "ALTER ", "DROP ")


def H():
    return {"Authorization": "Bearer " + jwt.encode(
        {"email": admin.ADMIN_EMAIL}, SECRET, algorithm="HS256")}


class Cur:
    def __init__(self, answers=None):
        self.sql = []
        self.answers = answers or []
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self._row = None
        for needle, value in self.answers:
            if needle in flat:
                self._row = value
                return

    def fetchone(self):
        if isinstance(self._row, list):
            return self._row[0] if self._row else None
        return self._row

    def fetchall(self):
        if isinstance(self._row, list):
            return self._row
        return [self._row] if self._row else []

    def close(self):
        pass


class Conn:
    def __init__(self, cur):
        self.cur = cur
        self.autocommit = False

    def cursor(self, *a, **k):
        return self.cur

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def app_with(*blueprints):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SECRET, DATABASE_URL="postgresql://stub/stub")
    for bp, prefix in blueprints:
        app.register_blueprint(bp, url_prefix=prefix) if prefix else \
            app.register_blueprint(bp)
    return app


def fake_adb(monkeypatch, cur):
    @contextmanager
    def adb():
        yield Conn(cur)
    monkeypatch.setattr(admin_video, "adb", adb)


def test_watermark_settings_get_is_a_plain_read(monkeypatch):
    cur = Cur([("to_regclass", {"t": "video_settings"}),
               ("FROM video_settings", {"watermark_enabled": False,
                                        "watermark_force": True,
                                        "watermark_scene_top": False,
                                        "watermark_lower": True,
                                        "updated_at": None})])
    fake_adb(monkeypatch, cur)
    app = app_with((admin_video.admin_video_bp, None))
    r = app.test_client().get("/admin/video/settings", headers=H())
    assert r.status_code == 200
    assert r.json["watermark_position"] == "lower"
    assert not any(s.upper().startswith(DDL) for s in cur.sql)


def test_newsletter_gets_never_issue_ddl_when_the_schema_exists(monkeypatch):
    cur = Cur([("AS ready", {"ready": True}),
               ("FROM newsletter_settings WHERE id = 1", {"?column?": 1}),
               ("COUNT(*) FILTER", {"verified": 3}),
               ("SELECT COUNT(*) AS count", {"count": 3})])
    monkeypatch.setattr(newsletter, "get_db", lambda: Conn(cur))
    monkeypatch.setattr(newsletter, "_nl_schema_ready", False)
    monkeypatch.setattr(newsletter, "_token_email", lambda: admin.ADMIN_EMAIL)
    app = app_with((newsletter.newsletter_bp, "/newsletter"))
    client = app.test_client()
    assert client.get("/newsletter/segments").status_code == 200
    assert client.get("/newsletter/subscribers").status_code == 200
    assert not any(s.upper().startswith(DDL) for s in cur.sql)
    seg = next(s for s in cur.sql if "COUNT(*) FILTER" in s)
    # Audience sizes leave the owner's account out.
    assert "lower(u.email)='thevalmera@gmail.com'" not in seg


def test_newsletter_today_uses_an_index_range():
    assert "::date = CURRENT_DATE" not in newsletter.NOT_TODAY
    assert "s.sent_at >= CURRENT_DATE" in newsletter.NOT_TODAY


@pytest.mark.parametrize("exc, status, code", [
    (psycopg2.errors.QueryCanceled("canceling statement due to timeout"),
     503, "timeout"),
    (psycopg2.OperationalError("could not connect"), 503, "unavailable"),
    (KeyError("boom"), 500, "internal"),
])
def test_old_admin_errors_are_json_not_html(monkeypatch, exc, status, code):
    def broken(*a, **k):
        raise exc
    monkeypatch.setattr(admin, "get_db", broken)
    fake_adb(monkeypatch, None)
    monkeypatch.setattr(admin_video, "adb", broken)
    app = app_with((admin.admin_bp, "/admin"), (admin_video.admin_video_bp, None))
    client = app.test_client()
    for path in ("/admin/revenue", "/admin/video/reliability"):
        r = client.get(path, headers=H())
        assert r.status_code == status and r.is_json, path
        assert r.json["code"] == code and r.json["retryable"] is True
        assert "boom" not in r.json["error"]


@pytest.mark.parametrize("path", [
    "/admin/engine-stats", "/admin/builds", "/admin/jobs",
    "/admin/job/abc/conversation", "/admin/charts/jobs",
    "/admin/charts/credits"])
def test_retired_app_builder_endpoints_are_gone(path):
    app = app_with((admin.admin_bp, "/admin"))
    assert app.test_client().get(path, headers=H()).status_code == 404


def test_overview_no_longer_reads_the_app_builder(monkeypatch):
    cur = Cur([("GROUP BY plan", [{"plan": "ai", "count": 2}]),
               ("FROM users WHERE is_verified = 1", {
                    "total": 10, "today": 1, "week": 2, "month": 3,
                    "prev_week": 1}),
               ("information_schema.columns", {"n": 3}),
               ("FROM users u WHERE", []),
               ("is_subscribed = 1 AND", {"total": 2}),
               ("FROM analytics_page_visits", {
                    "today": 1, "unique_today": 1, "week": 2,
                    "unique_week": 2, "month": 3, "unique_month": 3,
                    "prev_week": 1})])
    monkeypatch.setattr(admin, "get_db", lambda: Conn(cur))
    app = app_with((admin.admin_bp, "/admin"))
    r = app.test_client().get("/admin/overview", headers=H())
    assert r.status_code == 200
    assert "jobs" not in r.json and "credits" not in r.json
    joined = " ".join(cur.sql)
    assert " jobs " not in joined and "job_credits" not in joined
    assert "::date = CURRENT_DATE" not in joined
    assert r.json["users"]["new_today"] == 1


def test_subscribers_pager_reports_the_true_total_past_the_last_page(monkeypatch):
    calls = []

    class PagerCur(Cur):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            calls.append(params)
            if "matched_subscribers" in sql:
                self._row = [] if params[-1] > 0 else [
                    {"matched_subscribers": 37}]
            elif "ever_paid" in sql:
                self._row = {"ever_paid": 37}
    fake_adb(monkeypatch, PagerCur())
    app = app_with((admin_video.admin_video_bp, None))
    r = app.test_client().get("/admin/video/subscribers?page=9&per_page=50",
                              headers=H())
    assert r.status_code == 200
    assert r.json["subscribers"] == [] and r.json["total"] == 37


def test_subscriber_projects_pager_reports_the_true_total(monkeypatch):
    class PagerCur(Cur):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "matched_projects" in sql:
                self._row = [] if params[-1] > 0 else [{"matched_projects": 56}]
            else:
                self._row = {"n": 22}
    fake_adb(monkeypatch, PagerCur())
    app = app_with((admin_video.admin_video_bp, None))
    r = app.test_client().get("/admin/video/subscriber-projects?page=5",
                              headers=H())
    assert r.json["projects"] == [] and r.json["total"] == 56


def test_light_project_list_skips_the_tool_outcome_scan(monkeypatch):
    cur = Cur([("base_projects", [])])
    fake_adb(monkeypatch, cur)
    app = app_with((admin_video.admin_video_bp, None))
    client = app.test_client()
    assert client.get("/admin/video/projects?fields=list",
                      headers=H()).status_code == 200
    assert "tool_activity" not in cur.sql[-1] and "mcp_tool" not in cur.sql[-1]
    client.get("/admin/video/projects", headers=H())
    assert "tool_activity" in cur.sql[-1]          # the old admin's default


def test_legacy_project_inspector_is_capped_at_four_megabytes():
    activity = [{"id": i, "content": "a" * 900, "meta": {},
                 "created_at": "2026-10-01T00:00:00"} for i in range(1200)]
    turns = [{"job_id": i, "activity": activity, "assistant_messages": [],
              "user_message": None} for i in range(60)]
    out = {"turns": turns, "messages": [], "edls": [], "jobs": [],
           "assets": [], "children": [], "shorts_board": None,
           "upload_events": []}
    capped = admin_video._cap_legacy_detail(out)
    assert capped["truncated"] and "repeated activity" in capped["truncated_reason"]
    assert len(json.dumps(capped)) < admin_video.LEGACY_DETAIL_CAP
    assert len(capped["turns"][0]["activity"]) == 1200
    assert capped["turns"][1]["activity"] == []
    assert capped["turns"][1]["activity_shown_in_first_slice"]
    small = {"turns": [], "messages": [{"id": 1}], "edls": [], "jobs": [],
             "assets": [], "children": [], "shorts_board": None,
             "upload_events": []}
    assert "truncated" not in admin_video._cap_legacy_detail(small)


def test_heavy_reports_are_cached_and_say_when(monkeypatch):
    cache_writes = []

    def no_cache():
        raise psycopg2.OperationalError("cache offline")
    monkeypatch.setattr(cache, "_writer", no_cache)
    import admin_metrics.costs as costs
    monkeypatch.setattr(costs, "compute", lambda cur: {
        "economics_30d": {"cash_revenue_usd": 1.0}, "split": {}})
    fake_adb(monkeypatch, Cur())
    app = app_with((admin_video.admin_video_bp, None))
    r = app.test_client().get("/admin/video/costs", headers=H())
    assert r.status_code == 200 and r.json["computed_at"].endswith("Z")
    assert r.json["cache_age_s"] == 0 and not cache_writes


def test_reliability_has_one_non_success_number(monkeypatch):
    now = datetime.now(timezone.utc)
    cur = Cur([("scoped_jobs", {"starts_at": now - timedelta(hours=24),
                                "ends_at": now, "jobs_total": 10,
                                "tools_total": 4, "tool_failed": 1,
                                "tool_refused": 1})])
    fake_adb(monkeypatch, cur)
    app = app_with((admin_video.admin_video_bp, None))
    c = app.test_client().get("/admin/video/reliability",
                              headers=H()).json["counters"]
    assert "tool_failed" not in c and c["tool_non_success"]["count"] == 1


def test_survey_answers_are_paged_and_customers_only(monkeypatch):
    cur = Cur([("SELECT count(*) AS n FROM onboarding_responses", {"n": 1300}),
               ("ORDER BY o.created_at DESC", [{"id": 1}]),
               ("GROUP BY 1 ORDER BY n DESC", []),
               ("AS answered", {"answered": 1, "skipped": 0, "users": 2})])
    monkeypatch.setattr(onboarding, "get_db", lambda: Conn(cur))
    app = app_with((onboarding.onboarding_bp, None))
    r = app.test_client().get("/admin/onboarding?page=2&per_page=100",
                              headers=H())
    assert r.status_code == 200
    assert (r.json["page"], r.json["per_page"], r.json["total"]) == (2, 100, 1300)
    assert all("is_verified = 1" in s for s in cur.sql if "onboarding" in s)
    cur2 = Cur([("GROUP BY i.plan", [{"plan": "ai", "billing": "monthly",
                                      "presses": 3, "people": 2}])])
    monkeypatch.setattr(onboarding, "get_db", lambda: Conn(cur2))
    r = app.test_client().get("/admin/plan-intents?days=30", headers=H())
    assert r.json["summary"][0]["people"] == 2 and r.json["days"] == 30
    assert "i.user_id IS NULL OR" in cur2.sql[0]


def test_retired_tracker_answers_410_and_stores_nothing(monkeypatch):
    from unittest.mock import Mock
    app = Flask(__name__)
    app.register_blueprint(admin.admin_bp, url_prefix='/admin')
    called = Mock()
    monkeypatch.setattr(admin, 'get_db', called)
    r = app.test_client().post('/admin/track', json={'page': '/', 'device_id': 'x'})
    assert r.status_code == 410 and r.data == b''
    called.assert_not_called()
