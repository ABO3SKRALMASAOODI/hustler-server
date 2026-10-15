"""The classic admin page (the owner's original design) on corrected data:
people instead of browsers, Dubai days, outreach codes traced to signups, a
lean project inspector, and no reads of the retired app builder."""
import gzip
import json
import os
import sys
from datetime import date, datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_metrics import cache, classic, outreach, ranges, visitors  # noqa: E402
from routes import admin, admin_video  # noqa: E402
from tests.test_admin_legacy_reliability import (  # noqa: E402
    Conn, Cur, H, app_with, fake_adb)


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def _cls(person=0, link_preview=0, robot=0, internal=0, no_signal=0):
    return {"person": person, "link_preview": link_preview, "robot": robot,
            "internal": internal, "no_signal": no_signal, "signed_in": 0,
            "browsers": person + link_preview + robot + internal + no_signal}


# ── people, not browsers ─────────────────────────────────────────────────
def test_people_start_on_the_first_whole_dubai_day():
    # The tracker started at 20:07 Dubai on 3 Oct; that evening is not a day.
    assert ranges.local_day(classic.PEOPLE_SINCE) == date(2026, 10, 4)
    assert ranges.local_midnight(date(2026, 10, 4)) == classic.PEOPLE_SINCE


def test_left_out_names_what_was_not_a_person():
    out = classic.left_out(_cls(person=5, link_preview=7, robot=2,
                                internal=1, no_signal=3))
    assert out == {"robots": 2, "link_checks": 7, "internal": 1,
                   "no_signal": 3, "total": 13}
    assert classic.left_out(None) is None


def test_overview_visits_are_people_with_left_out_beside(monkeypatch):
    seen = []

    def fake_classify(cur, period):
        seen.append(period)
        return _cls(person=12, link_preview=40, robot=3)
    monkeypatch.setattr(visitors, "classify", fake_classify)
    cur = Cur([("FROM analytics_page_visits",
                {"views_today": 90, "views_week": 600})])
    out = classic.overview_visits(cur)
    assert out["unique_today"] == 12 and out["today"] == 90
    assert out["excluded_today"]["link_checks"] == 40
    assert out["measure"] == "people"
    # Every classified window starts no earlier than people were counted.
    assert all(p.start >= classic.PEOPLE_SINCE for p in seen)


def test_visits_report_never_mixes_the_old_browser_count_into_people(monkeypatch):
    tracked = ranges.local_day(classic.PEOPLE_SINCE)
    today = ranges.local_today()

    def by_day(cur, period):
        return {d: _cls(person=10, link_preview=5)
                for d in period.day_list()}
    monkeypatch.setattr(visitors, "classify_by_day", by_day)
    monkeypatch.setattr(visitors, "classify",
                        lambda cur, period: _cls(person=50, link_preview=9))
    monkeypatch.setattr(classic, "first_visit_day",
                        lambda cur: date(2026, 9, 1))
    old_day = date(2026, 9, 20)
    cur = Cur([("count(DISTINCT COALESCE", [
                   {"day": old_day, "views": 30, "browsers": 25},
                   {"day": today, "views": 40, "browsers": 33}]),
               ("FROM users u", [{"day": today, "n": 4}])])
    out = classic.visits_report(cur)
    rows = {r["day"]: r for r in out["data"]}
    old = rows[old_day.isoformat()]
    assert old["unique_visitors"] is None and old["legacy_visitors"] == 25
    now = rows[today.isoformat()]
    assert now["unique_visitors"] == 10 and now["legacy_visitors"] is None
    assert now["signups"] == 4 and now["conversion_rate"] == 40.0
    assert now["left_out"] == 5
    assert out["people_since"] == tracked.isoformat()
    assert out["periods"]["30"]["unique_visitors"] == 50
    assert out["periods"]["30"]["left_out"]["link_checks"] == 9


# ── outreach codes → people → signups ────────────────────────────────────
def test_outreach_codes_trace_each_link_to_people_and_signups(monkeypatch):
    at = datetime(2026, 10, 12, 9, 0)
    browsers = [
        {"device_id": "a", "code": "C" * 20, "campaign": "dm_oct",
         "network": "instagram", "first_at": at, "last_at": at,
         "class": "person", "browser_pages": 3},
        {"device_id": "b", "code": "C" * 20, "campaign": "dm_oct",
         "network": "instagram", "first_at": at, "last_at": at,
         "class": "link_preview", "browser_pages": 1},
        {"device_id": "c", "code": "D" * 20, "campaign": "",
         "network": "instagram", "first_at": at, "last_at": at,
         "class": "link_preview", "browser_pages": 1},
    ]
    signups = [{"id": 7, "email": "a@example.com", "plan": "free",
                "created_at": at, "code": "C" * 20, "campaign": "dm_oct",
                "paid": True, "cents": 2500, "status": "paying"}]
    monkeypatch.setattr(outreach, "coded_browsers", lambda cur, p: browsers)
    monkeypatch.setattr(outreach, "coded_signups", lambda cur, p: signups)
    out = classic.outreach_codes(Cur(), 30)
    assert out["totals"] == {"codes_opened": 2, "codes_opened_by_people": 1,
                             "codes_only_link_checks": 1, "people": 1,
                             "link_checks": 2, "signups": 1, "paid": 1}
    # Codes opened only by link checks are counted but not listed.
    assert [r["code"] for r in out["codes"]] == ["C" * 20]
    row = out["codes"][0]
    assert row["people"] == 1 and row["link_checks"] == 1
    assert row["pages_by_people"] == 3
    assert row["signups"][0]["email"] == "a@example.com"
    assert row["campaign_label"] == "Dm oct"


def test_outreach_codes_route_is_admin_only_and_cached(monkeypatch):
    calls = []
    monkeypatch.setattr(classic, "outreach_codes",
                        lambda cur, days: calls.append(days) or {"days": days})
    monkeypatch.setattr(admin, "get_db", lambda readonly=None: Conn(Cur()))
    client = app_with((admin.admin_bp, "/admin")).test_client()
    assert client.get("/admin/outreach-codes?days=7").status_code == 401
    assert client.get("/admin/outreach-codes?days=7",
                      headers=H()).json == {"days": 7}
    client.get("/admin/outreach-codes?days=7", headers=H())
    assert calls == [7]


# ── the users list answers the owner's questions ────────────────────────
@pytest.mark.parametrize("flt, needle", [
    ("subscribed", "u.u_status IN ('paying', 'payment_failing')"),
    ("canceled", "u.u_status = 'canceled'"),
    ("exported", "u.u_exports > 0"),
    ("free", "u.u_status = 'free'"),
])
def test_users_filters_and_no_app_builder(monkeypatch, flt, needle):
    cur = Cur([("AS all_", {"all_": 10, "subscribed": 2, "paid": 3,
                            "canceled": 1, "exported": 4, "free": 7}),
               ("AS today", {"today": 1, "week": 5}),
               ("LIMIT", [])])
    monkeypatch.setattr(admin, "get_db", lambda readonly=None: Conn(cur))
    monkeypatch.setattr(admin.trial_state, "columns_ready", lambda conn: True)
    r = app_with((admin.admin_bp, "/admin")).test_client().get(
        f"/admin/users?filter={flt}", headers=H())
    assert r.status_code == 200
    assert r.json["counts"]["exported"] == 4 and r.json["new_today"] == 1
    listing = next(s for s in cur.sql if "LIMIT" in s)
    assert needle in listing
    joined = " ".join(cur.sql)
    assert " jobs " not in joined and "FROM jobs" not in joined


def test_activity_and_live_read_only_the_video_editor(monkeypatch):
    cur = Cur([("to_regclass", {"t": None}),
               ("AS active_now", {"active_now": 1}),
               ("AS active_15m", {"active_15m": 2})])
    monkeypatch.setattr(admin, "get_db", lambda readonly=None: Conn(cur))
    client = app_with((admin.admin_bp, "/admin")).test_client()
    assert client.get("/admin/activity", headers=H()).status_code == 200
    assert client.get("/admin/realtime", headers=H()).status_code == 200
    assert client.get("/admin/top-users", headers=H()).status_code == 200
    joined = " ".join(cur.sql)
    assert "FROM jobs" not in joined and "JOIN jobs" not in joined
    assert "subscription_expiry AS ts" not in joined


def test_mrr_chart_sends_numbers_not_strings(monkeypatch):
    from decimal import Decimal
    cur = Cur([("information_schema.columns", {"n": 3}),
               ("generate_series", [{"day": "2026-10-01",
                                     "mrr": Decimal("40"),
                                     "subscribers": 2}])])
    monkeypatch.setattr(admin, "get_db", lambda readonly=None: Conn(cur))
    r = app_with((admin.admin_bp, "/admin")).test_client().get(
        "/admin/charts/mrr", headers=H())
    assert r.json["data"][0]["mrr"] == 40.0


# ── the project inspector: whole chat, nothing twice ─────────────────────
def _project_cur():
    t0 = datetime(2026, 10, 1, 8, 0)
    messages = [
        {"id": 1, "role": "user", "content": "cut the intro", "meta": {},
         "created_at": t0},
        {"id": 2, "role": "activity", "content": "trim → ok",
         "meta": {"tool": "trim"}, "created_at": t0},
        {"id": 3, "role": "assistant", "content": "Done", "meta": {},
         "created_at": t0},
    ]
    return Cur([
        ("FROM projects p JOIN users u", {
            "id": 9, "title": "Demo", "email": "c@example.com", "user_id": 4,
            "kind": None, "created_at": t0, "chat_session_id": 77,
            "trial_status": None, "trial_started_at": None,
            "trial_plan": None, "trial_started_after_wall": False,
            "parent_project_id": None, "meta": {}}),
        ("FROM chat_messages WHERE session_id", messages),
        ("FROM edls WHERE project_id", [{"version": 2, "created_by": "agent",
                                         "created_at": t0}]),
        ("type = 'agent_turn'", [{"id": 50, "state": "done", "error": None,
                                  "payload": {"message_id": 1},
                                  "result": {"edl_version": 2},
                                  "created_at": t0, "updated_at": t0}]),
        ("type = 'mcp_tool'", [{"id": 60, "state": "done", "error": None,
                                "tool": "add_text", "args": "{}",
                                "text": "EDL v2 -> v3", "is_error": False,
                                "edl_changed": "true", "created_at": t0,
                                "updated_at": t0}]),
        ("FROM video_jobs WHERE project_id", []),
        ("SELECT * FROM assets", []),
        ("COUNT(*) AS n FROM llm_calls", {"n": 0}),
    ])


def test_lean_inspector_names_turn_messages_instead_of_copying(monkeypatch):
    cur = _project_cur()
    fake_adb(monkeypatch, cur)
    monkeypatch.setattr(admin_video, "_presign", lambda key: None)
    r = app_with((admin_video.admin_video_bp, None)).test_client().get(
        "/admin/video/projects/9?lean=1", headers=H())
    assert r.status_code == 200
    d = r.json
    assert d["lean"] is True and d["project"]["user_id"] == 4
    turn = d["turns"][0]
    assert turn["activity_ids"] == [2] and turn["assistant_ids"] == [3]
    assert "activity" not in turn and "assistant_messages" not in turn
    assert d["edls"][0]["json"] is None
    assert d["mcp_calls"][0]["tool"] == "add_text"
    assert d["mcp_calls"][0]["edl_changed"] is True
    # Times are UTC with a Z, so the page shows the viewer's local time.
    assert d["messages"][0]["created_at"].endswith("Z")
    # No version bodies or job payloads were read for the lean page.
    joined = " ".join(cur.sql)
    assert "SELECT version, json" not in joined


def test_version_and_job_bodies_load_on_demand(monkeypatch):
    t0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    cur = Cur([("FROM edls WHERE project_id", {
                   "version": 3, "json": {"clips": []}, "created_by": "agent",
                   "created_at": t0}),
               ("FROM video_jobs WHERE project_id", {
                   "id": 5, "type": "preview", "state": "done",
                   "progress": 100, "error": None, "payload": {"a": 1},
                   "result": {"b": 2}, "attempts": 1, "created_at": t0,
                   "updated_at": t0})])
    fake_adb(monkeypatch, cur)
    client = app_with((admin_video.admin_video_bp, None)).test_client()
    e = client.get("/admin/video/projects/9/edls/3", headers=H())
    assert e.json["json"] == {"clips": []}
    j = client.get("/admin/video/projects/9/jobs/5", headers=H())
    assert j.json["payload"] == {"a": 1} and j.json["result"] == {"b": 2}


def test_big_admin_json_is_gzipped_when_the_browser_accepts_it(monkeypatch):
    from flask import Flask, jsonify
    app = Flask(__name__)

    @app.route("/big")
    def big():
        return jsonify({"rows": ["conversation text " * 20] * 400})
    app.after_request(admin.gzip_large_json)
    client = app.test_client()
    plain = client.get("/big")
    assert "Content-Encoding" not in plain.headers
    zipped = client.get("/big", headers={"Accept-Encoding": "gzip, br"})
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert len(zipped.data) < len(plain.data) / 5
    assert json.loads(gzip.decompress(zipped.data)) == plain.json


# ── exports: play and download ──────────────────────────────────────────
def test_exports_list_carries_play_and_download_links(monkeypatch):
    t0 = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)
    cur = Cur([("AS projects", {"done": 3, "failed": 1, "people": 2,
                                "projects": 2}),
               ("count(*) AS n", {"n": 1}),
               ("ORDER BY vj.updated_at DESC", [{
                   "id": 11, "user_id": 4, "project_id": 9,
                   "created_at": t0, "updated_at": t0, "result": {},
                   "title": "My trip", "parent_id": None,
                   "email": "c@example.com", "plan": "ai",
                   "status": "paying", "storage_key": "media/9/x.mp4",
                   "duration_s": 21.0, "fs_key": None, "fs_cols": None,
                   "fs_rows": None}])])
    fake_adb(monkeypatch, cur)
    monkeypatch.setattr(admin_video, "_presign", lambda key: "https://p/" + key)
    monkeypatch.setattr(admin_video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(admin_video.storage, "presign_get",
                        lambda key, expires=None, download_name=None:
                        f"https://d/{key}?name={download_name}")
    r = app_with((admin_video.admin_video_bp, None)).test_client().get(
        "/admin/video/exports?days=30", headers=H())
    assert r.status_code == 200
    assert r.json["totals"]["exports"] == 3
    assert r.json["totals"]["success_rate"] == 75.0
    row = r.json["exports"][0]
    assert row["url"] == "https://p/media/9/x.mp4"
    assert row["download_url"].endswith("name=My trip.mp4")
