"""/admin/v2 contracts (plan §6.4–6.6) on fixtures: shapes, errors, budgets.

No database is touched: the per-request connection is a scripted fake and
the metric modules are replaced by fixtures where a test is about assembly.
The SQL itself is exercised by tests/test_admin_metrics_postgres.py (CI
PostgreSQL) and was validated read-only against production.
"""
import json
from datetime import date, datetime, timedelta, timezone

import jwt
import psycopg2
import pytest
from flask import Flask

from admin_metrics import (attention as attention_mod, cache, channels_report,
                           db, funnel as funnel_mod, health, money, outreach,
                           projects, ranges, registry, visitors)
from routes import admin_v2

SECRET = "s" * 40
ADMIN = "thevalmera@gmail.com"
MB = 1024 * 1024
T0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


class FakeCursor:
    """Answers SQL by the first matching substring route; records the SQL."""

    def __init__(self, routes=()):
        self.routes = list(routes)
        self.sql = []
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        for needle, rows in self.routes:
            if needle in flat:
                self._rows = rows(params) if callable(rows) else rows
                return
        self._rows = []

    def fetchall(self):
        return [dict(r) for r in self._rows]

    def fetchone(self):
        return dict(self._rows[0]) if self._rows else None


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.closed = False

    def cursor(self):
        return self.cur

    def close(self):
        self.closed = True


def token(email=ADMIN):
    return {"Authorization": "Bearer " + jwt.encode({"email": email}, SECRET,
                                                    algorithm="HS256")}


@pytest.fixture
def app(monkeypatch):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SECRET, DATABASE_URL="postgresql://stub/stub")
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


def get(app, path, **kw):
    r = app.test_client().get(path, headers=kw.pop("headers", token()))
    assert len(r.data) < MB, f"{path} over the 1 MB budget"
    return r


META_KEYS = {"generated_at", "cache_age_s", "timezone", "utc_offset", "range",
             "compare", "scope", "tracking", "section_errors"}
METRIC_KEYS = {"key", "label", "how", "unit", "polarity", "value", "previous",
               "status", "note", "breakdown", "spark", "href"}


def assert_metric(m, key=None):
    assert set(m) == METRIC_KEYS
    if key:
        assert m["key"] == key
    d = registry.definition(m["key"])
    assert (m["label"], m["how"], m["unit"]) == (d["label"], d["how"], d["unit"])


CLASSES = {"person": 81, "link_preview": 0, "no_signal": 103, "robot": 6,
           "internal": 0, "signed_in": 2, "browsers": 190}


def summary_fixtures(monkeypatch, cash_error=None):
    monkeypatch.setattr(visitors, "classify", lambda cur, p: dict(CLASSES))
    monkeypatch.setattr(visitors, "classify_by_day",
                        lambda cur, p: {d: dict(CLASSES) for d in p.day_list()})
    monkeypatch.setattr(admin_v2, "signups_count", lambda cur, p: 21)
    monkeypatch.setattr(admin_v2, "signups_by_day", lambda cur, p: {})
    monkeypatch.setattr(money, "new_paying", lambda cur, p: 1)
    monkeypatch.setattr(money, "new_paying_by_day", lambda cur, p: {})

    def cash(cur, p):
        if cash_error:
            raise cash_error
        return {"usd": 15.0, "payments": 1, "non_usd": 0}
    monkeypatch.setattr(money, "cash", cash)
    monkeypatch.setattr(money, "cash_by_day", lambda cur, p: {})
    monkeypatch.setattr(money, "mrr_and_paying", lambda cur: (
        412.5, 21, [{"plan": "ai", "period": "monthly", "n": 14}]))
    monkeypatch.setattr(money, "mrr_at", lambda cur, d: None)
    monkeypatch.setattr(money, "mrr_history", lambda cur, p: {
        "available": False, "since": None, "rows": []})
    monkeypatch.setattr(admin_v2, "active_customers", lambda cur, p: 13)
    monkeypatch.setattr(admin_v2, "active_by_day", lambda cur, p: {})
    monkeypatch.setattr(admin_v2, "exports_count", lambda cur, p: (1, 1))
    monkeypatch.setattr(admin_v2, "exports_by_day", lambda cur, p: {})
    monkeypatch.setattr(money, "status_counts", lambda cur: {
        "paying": 21, "payment_failing": 1, "canceled": 12, "free": 1650,
        "ever_paid": 34})
    monkeypatch.setattr(money, "failed_payments", lambda cur, p: {
        "payments": 0, "usd": 0.0})
    monkeypatch.setattr(health, "failures", lambda *a, **k: {
        "metric": registry.metric("customer_failures", 0, breakdown=[
            registry.item("people_affected", 0)]), "by_type": [], "recent": []})
    monkeypatch.setattr(health, "uploads", lambda *a, **k: {
        "failed_people": 2, "started": 5, "landed": 3, "reasons": [],
        "recent": []})
    monkeypatch.setattr(health, "messages_without_edit", lambda *a, **k: {
        "paying": 0, "free": 3, "people": 3, "rows": [], "recent": []})
    monkeypatch.setattr(health, "engine", lambda *a, **k: {
        "status": "good", "text": "Working", "queued": 0, "running": 1,
        "last_activity_at": None, "oldest_queued_s": None, "stuck": 0})
    monkeypatch.setattr(channels_report, "summary_channels", lambda cur, p: {
        "model": "first",
        "rows": [{"channel": "search", "label": "Search", "signups": 9,
                  "people": 30}],
        "not_recorded": {"signups": 2, "reasons": [
            {"reason": "privacy_browser", "label": "Privacy browser",
             "signups": 2}]}})


# ── Auth, errors, parameters ─────────────────────────────────────────────
def test_admin_only(app):
    assert get(app, "/admin/v2/meta", headers={}).status_code == 401
    assert get(app, "/admin/v2/meta",
               headers=token("someone@example.com")).status_code == 403


@pytest.mark.parametrize("path", [
    "/admin/v2/summary?range=bogus", "/admin/v2/summary?range=all",
    "/admin/v2/trend?metrics=visits", "/admin/v2/customers?per_page=abc",
    "/admin/v2/customers?page=0", "/admin/v2/projects?include_owner=2",
    "/admin/v2/acquisition?model=middle", "/admin/v2/attention?limit=999",
    "/admin/v2/funnel/cohorts?weeks=60",
    "/admin/v2/revenue/payments?status=maybe",
    "/admin/v2/acquisition/outreach/code?code=not%20a%20code",
    "/admin/v2/summary?range=custom&from=2026-10-05&to=2026-10-01",
])
def test_bad_parameters_are_plain_400s(app, path):
    r = get(app, path)
    assert r.status_code == 400
    assert r.json["error"]["code"] == "bad_request"
    assert r.json["error"]["message"] and not r.json["error"]["retryable"]


@pytest.mark.parametrize("exc, status, code", [
    (psycopg2.errors.QueryCanceled("canceling statement"), 503, "timeout"),
    (psycopg2.OperationalError("server closed the connection"), 503,
     "unavailable"),
    (RuntimeError("boom with a secret path /etc/x"), 500, "internal"),
])
def test_failures_are_json_never_html(app, monkeypatch, exc, status, code):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(channels_report, "acquisition_report", boom)
    r = get(app, "/admin/v2/acquisition?range=7d")
    assert r.status_code == status and r.is_json
    assert r.json["error"]["code"] == code and r.json["error"]["retryable"]
    assert "secret" not in r.json["error"]["message"]


def test_unknown_ids_are_404_json(app):
    r = get(app, "/admin/v2/projects/123")
    assert r.status_code == 404 and r.json["error"]["code"] == "not_found"
    r = get(app, "/admin/v2/customers/123")
    assert r.status_code == 404 and r.json["error"]["code"] == "not_found"


# ── 1. meta ──────────────────────────────────────────────────────────────
def test_meta_carries_the_registry_and_the_fixed_lists(app):
    r = get(app, "/admin/v2/meta")
    assert r.status_code == 200 and set(r.json["meta"]) == META_KEYS
    d = r.json["data"]
    assert [c["key"] for c in d["channels"]] == [
        "search", "ai_assistant", "outreach", "social", "email",
        "other_website", "no_referrer", "not_recorded"]
    assert [x["key"] for x in d["ranges"]] == list(ranges.RANGE_KEYS)
    assert {x["key"] for x in d["not_recorded_reasons"]} == {
        "before_tracking", "privacy_browser", "nothing_sent", "landing_lost",
        "no_row_unknown"}
    assert set(d["definitions"]) == set(registry.REGISTRY)
    assert r.json["meta"]["timezone"] == "Asia/Dubai"
    assert r.json["meta"]["scope"]["label"].startswith("Customers since 6 Jul")


# ── 2. summary ───────────────────────────────────────────────────────────
def test_summary_has_eight_kpis_five_health_items_and_sources(app, monkeypatch):
    summary_fixtures(monkeypatch)
    r = get(app, "/admin/v2/summary")
    assert r.status_code == 200
    meta, d = r.json["meta"], r.json["data"]
    assert set(meta) == META_KEYS and meta["section_errors"] == []
    assert meta["range"]["key"] == "today" and meta["range"]["partial"]
    assert meta["compare"]["label"] == "vs yesterday at this time"
    keys = [k["key"] for k in d["kpis"]]
    assert keys == ["people", "signups", "signup_rate", "new_paying", "cash",
                    "mrr", "active_customers", "exports"]
    for k in d["kpis"]:
        assert_metric(k)
    people = d["kpis"][0]
    assert people["value"] == 81 and people["previous"] == 81
    assert [b["key"] for b in people["breakdown"]] == [
        "link_previews", "robots", "no_signal_loads", "internal",
        "returning_customers"]
    assert len(people["spark"]) == 14
    assert people["href"] == "/admin/growth?range=today"
    assert d["kpis"][2]["value"] == round(100 * 21 / 81, 1)
    assert d["kpis"][5]["previous"] is None and d["kpis"][5]["note"]
    assert d["kpis"][7]["breakdown"][0]["key"] == "exporters"
    assert [h["key"] for h in d["health"]] == [
        "customer_failures", "failed_uploads", "payment_problems",
        "messages_without_edit", "editing_engine"]
    assert d["channels"]["not_recorded"]["signups"] == 2


def test_one_failed_section_never_blanks_the_page(app, monkeypatch):
    summary_fixtures(monkeypatch,
                     cash_error=psycopg2.errors.QueryCanceled("slow"))
    r = get(app, "/admin/v2/summary")
    assert r.status_code == 200
    cash = r.json["data"]["kpis"][4]
    assert cash["key"] == "cash" and cash["status"] == "error"
    assert cash["value"] is None                     # never a fake 0
    assert r.json["meta"]["section_errors"][0]["section"] == "cash"
    assert r.json["data"]["kpis"][0]["value"] == 81  # the rest still renders


def test_summary_is_cached_and_fresh_bypasses(app, monkeypatch):
    summary_fixtures(monkeypatch)
    calls = []
    monkeypatch.setattr(admin_v2, "signups_count",
                        lambda cur, p: calls.append(1) or 21)
    get(app, "/admin/v2/summary")
    n = len(calls)
    r = get(app, "/admin/v2/summary")
    assert len(calls) == n                          # served from cache
    get(app, "/admin/v2/summary?fresh=1")
    assert len(calls) == 2 * n                      # recomputed
    assert r.json["meta"]["cache_age_s"] >= 0


def test_a_failed_section_is_not_cached(app, monkeypatch):
    summary_fixtures(monkeypatch, cash_error=RuntimeError("x"))
    get(app, "/admin/v2/summary")
    summary_fixtures(monkeypatch)
    r = get(app, "/admin/v2/summary")
    assert r.json["meta"]["section_errors"] == []


# ── 3. trend ─────────────────────────────────────────────────────────────
def test_trend_rows_and_seven_day_averages_align(app, monkeypatch):
    def by_day(cur, p):
        tracked = ranges.local_day(datetime(2026, 10, 3, 16, 7,
                                            tzinfo=timezone.utc))
        return {d: (dict(CLASSES) if d >= tracked else None)
                for d in p.day_list()}
    monkeypatch.setattr(visitors, "classify_by_day", by_day)
    monkeypatch.setattr(admin_v2, "signups_by_day",
                        lambda cur, p: {d: 10 for d in p.day_list()})
    monkeypatch.setattr(money, "new_paying_by_day", lambda cur, p: {})
    monkeypatch.setattr(money, "cash_by_day", lambda cur, p: {})
    r = get(app, "/admin/v2/trend?range=30d")
    d = r.json["data"]
    assert d["grain"] == "day" and len(d["rows"]) == 30
    for k, v in d["averages"].items():
        assert len(v) == 30, k
    assert d["averages"]["signups_7d"][0] == 10.0
    assert d["rows"][-1]["signups"] == 10 and d["rows"][0]["cash_usd"] == 0.0
    # Days before tracking began are unknown, never 0.
    before = [row for row in d["rows"] if row["date"] < "2026-10-03"]
    assert before and all(row["people"] is None for row in before)


def test_long_custom_ranges_switch_to_weeks():
    rows = [{"date": (date(2026, 1, 5) + timedelta(days=i)).isoformat(),
             "people": None if i < 3 else 1, "signups": 2}
            for i in range(14)]
    weeks = admin_v2._weekly(rows, ("people", "signups"))
    assert [w["date"] for w in weeks] == ["2026-01-05", "2026-01-12"]
    assert weeks[0]["people"] == 4 and weeks[0]["signups"] == 14
    assert admin_v2._avg7([1] * 6 + [None, 1]) == [None] * 8
    assert admin_v2._avg7([7] * 8)[6:] == [7.0, 7.0]


# ── 4. attention ─────────────────────────────────────────────────────────
def test_attention_is_prioritised_and_counted(app, monkeypatch):
    def item(t, sev, at):
        return {"id": f"{t}:1", "type": t, "type_label": "x", "severity": sev,
                "title": "t", "detail": None, "customer": None,
                "project_id": None, "occurred_at": at, "href": "/admin"}
    monkeypatch.setattr(attention_mod, "SOURCES", {
        "failed_job": lambda cur: [item("failed_job", "serious",
                                        "2026-10-10T10:00:00Z")],
        "stuck_job": lambda cur: [],
        "upload_failed": lambda cur: [item("upload_failed", "warning",
                                           "2026-10-10T11:00:00Z")],
        "payment_failing": lambda cur: [item("payment_failing", "critical",
                                             "2026-10-01T10:00:00Z")],
        "billing_mismatch": lambda cur: (_ for _ in ()).throw(
            RuntimeError("down")),
        "message_no_edit": lambda cur: [item("message_no_edit", "critical",
                                             "2026-10-09T10:00:00Z")],
    })
    r = get(app, "/admin/v2/attention?limit=3")
    d = r.json["data"]
    assert [i["type"] for i in d["items"]] == [
        "message_no_edit", "payment_failing", "failed_job"]
    assert d["total"] == 4 and d["counts"]["upload_failed"] == 1
    assert r.json["meta"]["section_errors"][0]["section"] == \
        "attention.billing_mismatch"


# ── 5–8. acquisition ─────────────────────────────────────────────────────
def signup_row(uid, created, att=None, tracking=None, has_row=True, told=None,
               paid=False, cents=0):
    return {"id": uid, "created_at": created, "has_row": has_row,
            "attribution": att, "tracking": tracking, "device_id": None,
            "told": told, "paid": paid, "cents": cents, "first_page": None}


def acquisition_fixtures(monkeypatch):
    g = {"source": "www.google.com", "medium": "organic", "at": 1}
    c = {"source": "chatgpt.com", "at": 1}
    monkeypatch.setattr(channels_report, "signup_rows", lambda cur, p: [
        signup_row(1, datetime(2026, 10, 9), {"first": g, "last": c},
                   told="ai_chatbot", paid=True, cents=2500),
        signup_row(2, datetime(2026, 10, 9), {"first": c, "last": c}),
        signup_row(3, datetime(2026, 10, 9), tracking="privacy_signal"),
        signup_row(4, datetime(2026, 9, 1), has_row=False, told="youtube"),
    ])
    monkeypatch.setattr(visitors, "people_rows", lambda cur, p: [
        {"device_id": "a", "first_attribution": {"first": g, "last": g},
         "first_referrer": "www.google.com", "first_page": "/"},
        {"device_id": "b", "first_attribution": None,
         "first_referrer": "chatgpt.com", "first_page": "/"},
    ])
    monkeypatch.setattr(visitors, "classify", lambda cur, p: dict(
        CLASSES, link_preview=971))


def test_acquisition_lists_all_channels_with_coverage(app, monkeypatch):
    acquisition_fixtures(monkeypatch)
    r = get(app, "/admin/v2/acquisition?range=90d")
    d = r.json["data"]
    assert [c["channel"] for c in d["channels"]] == [
        "search", "ai_assistant", "outreach", "social", "email",
        "other_website", "no_referrer", "not_recorded"]
    by = {c["channel"]: c for c in d["channels"]}
    assert (by["search"]["signups"], by["search"]["people"],
            by["search"]["paying"], by["search"]["collected_usd"]) == \
        (1, 1, 1, 25.0)
    assert by["search"]["signup_rate"] == 100.0
    assert by["outreach"]["link_previews"] == 971
    assert by["not_recorded"]["signup_rate"] is None
    assert d["coverage"] == {"signups": 4, "tracked": 2, "estimated": 0,
                             "not_recorded": [
                                 {"reason": "before_tracking",
                                  "label": "Before tracking", "signups": 1},
                                 {"reason": "privacy_browser",
                                  "label": "Privacy browser (not tracked by "
                                           "design)", "signups": 1}]}
    assert d["before_tracking"]["told"][0]["told"] == "youtube"
    last = get(app, "/admin/v2/acquisition?range=90d&model=last").json["data"]
    assert {c["channel"]: c["signups"] for c in last["channels"]}[
        "ai_assistant"] == 2


def test_outreach_separates_previews_from_people(app, monkeypatch):
    rows = [{"device_id": f"p{i}", "code": f"code{i:012d}", "campaign":
             "instagram_outreach", "network": "instagram",
             "first_at": datetime(2026, 10, 9), "last_at": datetime(2026, 10, 9),
             "pages": 1, "class": "link_preview", "browser_pages": 1}
            for i in range(5)]
    rows.append({"device_id": "human", "code": "humancode0000000",
                 "campaign": "ig_creators_2026_10", "network": "instagram",
                 "first_at": datetime(2026, 10, 10),
                 "last_at": datetime(2026, 10, 10), "pages": 3,
                 "class": "person", "browser_pages": 3})
    monkeypatch.setattr(outreach, "coded_browsers", lambda cur, p, code=None: [
        r for r in rows if code is None or r["code"] == code])
    monkeypatch.setattr(outreach, "coded_signups", lambda cur, p, code=None: [])
    d = get(app, "/admin/v2/acquisition/outreach?range=30d").json["data"]
    by = {c["campaign"]: c for c in d["campaigns"]}
    assert by["untagged"]["link_previews"] == 5 and by["untagged"]["people"] == 0
    assert by["ig_creators_2026_10"]["label"] == "Ig creators 2026 10"
    assert by["ig_creators_2026_10"]["people"] == 1
    assert d["totals"]["links_opened"] == 6
    assert d["untagged_share"] == round(100 * 5 / 6, 1)
    code = get(app, "/admin/v2/acquisition/outreach/code?"
                    "code=humancode0000000").json["data"]
    assert (code["link_previews"], code["people"],
            code["pages_viewed_by_people"]) == (0, 1, 3)
    assert code["campaign_label"] == "Ig creators 2026 10"
    missing = get(app, "/admin/v2/acquisition/outreach/code?"
                       "code=ZZZZZZZZZZZZZZZZ")
    assert missing.status_code == 404


def test_landing_pages_contract(app, monkeypatch):
    monkeypatch.setattr(channels_report, "landing_pages",
                        lambda cur, p, limit: {
                            "rows": [{"page": "/", "people": 84,
                                      "active_median_s": 15, "signups": 46,
                                      "signup_share": 54.8}],
                            "app_only_people": 9})
    d = get(app, "/admin/v2/acquisition/pages?range=30d&limit=5").json["data"]
    assert set(d["rows"][0]) == {"page", "people", "active_median_s",
                                 "signups", "signup_share"}
    assert d["app_only_people"] == 9


# ── 9–10. funnel ─────────────────────────────────────────────────────────
def test_funnel_stages_never_lose_a_negative_number(app, monkeypatch):
    monkeypatch.setattr(visitors, "classify", lambda cur, p: dict(CLASSES))
    monkeypatch.setattr(funnel_mod, "stage_counts", lambda cur, p: {
        "signed_up": 100, "uploaded": 60, "asked_edit": 30, "exported": 5,
        "paid": 8, "lost": {"uploaded": 40, "asked_edit": 32, "exported": 26,
                            "paid": 2}})
    monkeypatch.setattr(funnel_mod, "blockers", lambda cur, p: {
        "upload_failed": 3, "paywall_upload": 40, "plans_seen": 50,
        "paywall_chat": 20})
    monkeypatch.setattr(funnel_mod, "checkout", lambda cur, p: {
        "since": "2026-10-02", "stages": [
            {"key": k, "label": l, "people": 1}
            for k, l in funnel_mod.CHECKOUT_STAGES]})
    d = get(app, "/admin/v2/funnel?range=30d").json["data"]
    stages = {s["key"]: s for s in d["stages"]}
    assert [s["key"] for s in d["stages"]] == [
        "signed_up", "uploaded", "asked_edit", "exported", "paid"]
    assert stages["uploaded"]["pct_of_first"] == 60.0
    assert stages["uploaded"]["lost"] == 40
    # 5 exported: 3 of them paid (2 lost here), and 5 paid without exporting.
    assert (stages["paid"]["lost"], stages["paid"]["continued"],
            stages["paid"]["skipped"], stages["paid"]["pct_of_prev"]) == \
        (2, 3, 5, 60.0)
    assert stages["asked_edit"]["pct_of_prev"] == round(28 / 60 * 100, 1)
    assert all(s["pct_of_prev"] is None or s["pct_of_prev"] <= 100
               for s in d["stages"])
    assert stages["signed_up"]["lost"] is None
    assert "not_reached=uploaded" in stages["uploaded"]["lost_href"]
    assert_metric(d["people_same_period"], "people")
    assert d["people_same_period"]["previous"] is None
    assert {b["key"] for b in d["blockers"]} == {
        "upload_failed", "paywall_upload", "paywall_chat", "plans_seen"}
    assert [s["key"] for s in d["checkout"]["stages"]] == [
        "opened", "loaded", "payment_selected", "attempted", "recorded"]


def test_cohorts_are_weeks_newest_first(app):
    d = get(app, "/admin/v2/funnel/cohorts?weeks=4").json["data"]
    assert len(d["rows"]) == 4
    starts = [r["week_start"] for r in d["rows"]]
    assert starts == sorted(starts, reverse=True)
    assert all(date.fromisoformat(s).weekday() == 0 for s in starts)
    assert d["rows"][0]["maturing"] and not d["rows"][-1]["maturing"]
    assert d["rows"][0]["label"].startswith("Week of ")


# ── 11–12. customers ─────────────────────────────────────────────────────
def customer_routes():
    return [
        ("SELECT b.status, count(*) AS n", [
            {"status": "paying", "n": 2, "ever_paid": 2},
            {"status": "free", "n": 310, "ever_paid": 0}]),
        (", agg AS (", [
            {"id": 7, "email": "customer1@example.com", "plan": "ai",
             "billing_plan": None, "billing_period": "monthly",
             "created_at": datetime(2026, 10, 9, 8), "last_seen_at": None,
             "device_type": "phone", "status": "paying", "projects": 2,
             "exports": 1, "paid_cents": 1500,
             "last_active_at": datetime(2026, 10, 10, 9, tzinfo=timezone.utc)}]),
        ("fp ON TRUE WHERE u.id = ANY", [
            {"id": 7, "created_at": datetime(2026, 10, 9, 8), "has_row": True,
             "attribution": {"first": {"source": "chatgpt.com", "at": 1},
                             "last": None},
             "tracking": None, "first_page": None}]),
        ("FROM onboarding_responses WHERE user_id = ANY", [
            {"user_id": 7, "channel": "ai_chatbot"}]),
        ("max(billing_synced_at)", [{"t": None}]),
        ("u.last_seen_at >=", [{"n": 25}]),
    ]


def test_customers_list_contract_and_true_total(app):
    app.fake.routes = customer_routes()
    r = get(app, "/admin/v2/customers?range=today&date_field=joined")
    d = r.json["data"]
    assert d["summary"]["all"] == 312 and d["summary"]["paying"] == 2
    assert_metric(d["summary"]["signed_in"], "signed_in_customers")
    assert d["summary"]["signed_in"]["value"] == 25
    row = d["list"]["rows"][0]
    assert set(row) == {"id", "email", "plan", "billing_period", "status",
                        "joined_at", "last_active_at", "device", "came_from",
                        "told_us", "projects", "exports", "paid_usd"}
    assert row["came_from"]["channel"] == "ai_assistant"
    assert row["told_us"] == {"key": "ai_chatbot", "label": "ChatGPT / AI"}
    assert row["paid_usd"] == 15.0
    assert d["list"]["total"] == 312 and d["list"]["per_page"] == 50
    # A page past the end still reports the true total.
    app.fake.routes = [(n, rows if n != ", agg AS (" else [])
                       for n, rows in customer_routes()]
    past = get(app, "/admin/v2/customers?page=99").json["data"]["list"]
    assert past["rows"] == [] and past["total"] == 312


def test_customer_filters_validate(app):
    app.fake.routes = customer_routes()
    for q in ("filter=rich", "sort=age", "date_field=born",
              "reached=famous", "channel=tv"):
        assert get(app, "/admin/v2/customers?" + q).status_code == 400


def test_customer_page_contract(app):
    app.fake.routes = [
        ("FROM users u WHERE u.id = %s", [{
            "id": 7, "email": "customer1@example.com",
            "created_at": datetime(2026, 10, 9, 8), "is_verified": 1,
            "auth_provider": "google", "device_type": "phone", "plan": "ai",
            "billing_plan": "ai", "billing_status": "active",
            "billing_period": "monthly", "last_seen_at": T0,
            "credits_balance": 900, "credits_monthly": 1000,
            "payment_recovered_at": None, "payment_failed_at": None,
            "billing_synced_at": T0, "status": "paying",
            "last_active_at": T0}]),
        ("FROM job_credits", [{"used": 12.5}]),
        ("fp ON TRUE WHERE u.id = ANY", []),
        ("FROM onboarding_responses WHERE user_id = %s", []),
        ("FROM payments p WHERE p.user_id = %s", [
            {"id": 1, "at": datetime(2026, 10, 9, 9), "amount_cents": 1500,
             "currency": "USD", "status": "completed", "plan": "ai",
             "origin": "web", "error_code": None, "ok": True, "failed": False},
            {"id": 2, "at": datetime(2026, 10, 1), "amount_cents": 0,
             "currency": "USD", "status": "completed", "plan": "ai",
             "origin": "web", "error_code": None, "ok": False,
             "failed": False}]),
        ("AS edit_requests", [{"projects": 2, "uploads": 3,
                               "last_upload_at": T0, "edit_requests": 4,
                               "exports": 1}]),
    ]
    d = get(app, "/admin/v2/customers/7").json["data"]
    assert set(d) == {"customer", "acquisition", "survey", "billing", "usage",
                      "projects"}
    assert d["customer"]["is_internal"] is False
    assert d["customer"]["credits"]["used_30d"] == 12.5
    assert d["acquisition"]["tracking"] == "unknown"
    assert d["billing"]["paid_usd"] == 15.0
    assert [p["status_label"] for p in d["billing"]["payments"]] == [
        "Successful", "Free trial start ($0)"]
    assert d["billing"]["events"][0]["kind"] == "started_paying"
    assert d["usage"]["edit_requests"] == 4


# ── 13–21. projects ──────────────────────────────────────────────────────
def project_list_routes(page_rows=True):
    row = {"id": 11, "title": "Real edit", "kind": "edit",
           "created_at": T0, "user_id": 7, "email": "customer1@example.com",
           "plan": "ai", "status": "paying", "messages": 3,
           "last_activity_at": T0, "shorts": 0, "versions": 2, "exports": 1,
           "failed_jobs": 0, "stuck_jobs": 0, "paywall_hits": 1,
           "duration_s": 61.0, "source_bytes": 10, "width": 1080,
           "height": 1920, "upload_s": 30.0, "upload_measured": False,
           "index_s": 12.0, "index_cached": False, "edit_s": 8.0,
           "previews": 2, "turn_s": None, "turns": 0, "led_to_payment": True}
    return [
        ("AS all_projects", [{"customers": 120, "paying": 30, "mine": 900,
                              "problems": 4, "not_exported": 100,
                              "all_projects": 120}]),
        ("page AS MATERIALIZED", [row] if page_rows else []),
    ]


def test_projects_list_contract(app):
    app.fake.routes = project_list_routes()
    d = get(app, "/admin/v2/projects?filter=paying").json["data"]
    assert d["counts"] == {"customers": 120, "paying": 30, "mine": 900,
                           "problems": 4, "not_exported": 100}
    assert d["total"] == 30
    row = d["rows"][0]
    assert set(row) == {"id", "title", "kind", "created_at", "customer",
                        "video_length_s", "upload_wait_s",
                        "upload_wait_estimated", "analysis_wait_s",
                        "analysis_reused", "edit_wait_median_s", "messages",
                        "shorts", "versions", "exports", "problems",
                        "paywall_hits", "led_to_payment", "last_activity_at"}
    assert row["upload_wait_estimated"] is True
    # No tool-outcome columns in the list (they cost 95% of the old list).
    assert "tool" not in json.dumps(row)
    page_sql = app.fake.sql[-1]
    assert "mcp_tool" not in page_sql
    app.fake.routes = project_list_routes(page_rows=False)
    past = get(app, "/admin/v2/projects?page=50").json["data"]
    assert past["rows"] == [] and past["total"] == 120


def big_project_routes(slices=60, messages=2000, versions=200):
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    msgs = [{"id": i, "role": "user" if i in (1, messages) else "activity",
             "content": f"message body {i} " + "x" * 300, "meta": {"tool": "cut"},
             "created_at": datetime(2026, 10, 1)} for i in range(1, messages + 1)]

    def latest(params):
        limit = params[-1]
        return list(reversed(msgs))[:limit]
    return [
        ("FROM projects p JOIN users u ON u.id = p.user_id", [{
            "id": 5, "title": "Long session", "kind": "edit",
            "created_at": base, "parent_project_id": None,
            "chat_session_id": 9, "user_id": 7,
            "email": "customer1@example.com", "plan": "ai",
            "status": "paying", "last_activity_at": base,
            "duration_s": 600.0, "source_bytes": 10, "width": 1920,
            "height": 1080, "upload_s": 30.0, "upload_measured": True,
            "index_s": 60.0, "index_cached": False, "edit_s": 9.0,
            "previews": 3, "turn_s": None, "turns": 0}]),
        ("LEFT(content, 8001)", latest),
        ("count(*) FILTER (WHERE role = 'user') AS requests",
         [{"n": messages, "requests": 2}]),
        ("SELECT id, role FROM chat_messages", [
            {"id": m["id"], "role": m["role"]} for m in msgs]),
        ("type = 'agent_turn'", [
            {"id": 100 + i, "state": "done", "created_at": base,
             "updated_at": base + timedelta(minutes=1), "error": None,
             "message_id": "1", "root_id": "100", "credits": 2}
            for i in range(slices)]),
        ("(result IS NOT NULL) AS has_result", [
            {"id": 100 + i, "type": "agent_turn", "state": "done",
             "progress": 100, "error": None, "attempts": 1,
             "created_at": base, "updated_at": base, "has_result": True,
             "tool": None} for i in range(slices)]),
        ("SELECT count(*) AS n FROM video_jobs WHERE project_id", [{"n": slices}]),
        ("pg_column_size(json) AS size_bytes", [
            {"version": v, "created_by": "agent", "created_at": base,
             "size_bytes": 5000} for v in range(versions, 0, -1)]),
        ("SELECT count(*) AS n FROM edls", [{"n": versions}]),
        ("AS paywall_hits", [{"exports": 1, "failed_jobs": 0,
                              "stuck_jobs": 0, "paywall_hits": 0}]),
        ("WHERE c.parent_project_id = %s", []),
    ]


def test_project_page_is_bounded_and_never_repeats_a_body(app):
    app.fake.routes = big_project_routes()
    r = get(app, "/admin/v2/projects/5")
    assert r.status_code == 200 and len(r.data) < 2 * MB
    d = r.json["data"]
    assert set(d) == {"project", "summary", "messages", "turns", "jobs",
                      "versions", "shorts", "truncated", "truncated_reason"}
    assert len(d["messages"]["rows"]) == 300 and d["messages"]["has_more"]
    assert d["messages"]["before_id"] == d["messages"]["rows"][0]["id"]
    assert d["messages"]["total"] == 2000
    assert len(d["turns"]) == 1 and len(d["turns"][0]["slices"]) == 60
    body = r.data.decode()
    # Each message body appears at most once in the whole response.
    for m in d["messages"]["rows"]:
        assert body.count(f'message body {m["id"]} ') == 1
    assert body.count("message body 5 ") == 0      # older page: ids only
    for j in d["jobs"]["rows"]:
        assert "payload" not in j and "result" not in j and j["has_result"]
    assert all("json" not in v for v in d["versions"])
    assert len(d["versions"]) == 200 and d["summary"]["versions"] == 200
    assert d["project"]["video"]["length_s"] == 600.0


def test_project_tabs_load_on_demand(app, monkeypatch):
    app.fake.routes = [
        ("SELECT chat_session_id FROM projects", [{"chat_session_id": 9}]),
        ("LEFT(content, 8001)", lambda params: [
            {"id": i, "role": "assistant", "content": "hi", "meta": None,
             "created_at": datetime(2026, 10, 1)}
            for i in range(params[1] - 1, max(params[1] - 4, 0), -1)]),
        ("FROM video_jobs WHERE id = %s AND project_id", [{
            "id": 3, "type": "final", "state": "failed",
            "payload": {"edl_version": 4}, "result": {"timings": {"total_s": 9}},
            "error": "render failed"}]),
        ("FROM edls WHERE project_id = %s AND version = %s", [{
            "version": 4, "created_by": "user", "created_at": T0,
            "json": {"clips": []}}]),
        ("version < %s", [{"version": 3, "json": {"clips": [1]}}]),
        ("FROM indexes i", []),
        ("FROM assets WHERE project_id", [{
            "id": 1, "kind": "original", "storage_key": "k", "bytes": 5,
            "duration_s": 3.0, "width": 1, "height": 1, "created_at": T0}]),
    ]
    msgs = get(app, "/admin/v2/projects/5/messages?before_id=10&limit=3")
    assert [m["id"] for m in msgs.json["data"]["rows"]] == [7, 8, 9]
    job = get(app, "/admin/v2/projects/5/jobs/3").json["data"]
    assert job["payload"] == {"edl_version": 4} and job["timings"]["total_s"] == 9
    ver = get(app, "/admin/v2/projects/5/versions/4?with_previous=1").json["data"]
    assert ver["previous"]["version"] == 3 and ver["created_by"] == "user"
    import routes.admin_video as av
    monkeypatch.setattr(av, "_presign", lambda key: "https://signed/" + key)
    assets = get(app, "/admin/v2/projects/5/assets").json["data"]["rows"]
    assert assets[0]["preview_url"] == "https://signed/k"
    idx = get(app, "/admin/v2/projects/5/index").json["data"]
    assert idx == {"index": None, "created_at": None, "size_bytes": 0,
                   "truncated": False}
    assert get(app, "/admin/v2/projects/5/messages?before_id=x").status_code \
        == 400


def test_llm_call_previews_are_short(app):
    app.fake.routes = [
        ("SELECT chat_session_id FROM projects", [{"chat_session_id": 9}]),
        ("SELECT count(*) AS n FROM llm_calls", [{"n": 1}]),
        ("LEFT(lc.request::text", [{
            "id": 1, "created_at": T0, "purpose": "agent", "model": "m",
            "job_id": 3, "prompt_tokens": 10, "completion_tokens": 5,
            "cost": 0.0012, "request": {"messages": [
                {"role": "user", "content": "z" * 5000}]},
            "response": {"reply": "r" * 5000}, "request_text": None,
            "response_text": None}]),
    ]
    d = get(app, "/admin/v2/projects/5/llm_calls").json["data"]
    row = d["rows"][0]
    assert len(row["request_preview"]) <= 500
    assert len(row["response_preview"]) <= 500
    assert d["per_page"] == 20 and d["total"] == 1


# ── 22–24. revenue ───────────────────────────────────────────────────────
def test_revenue_contract(app, monkeypatch):
    summary_fixtures(monkeypatch)
    monkeypatch.setattr(money, "stopped_paying", lambda cur, p: None)
    monkeypatch.setattr(money, "snapshots_since", lambda cur: None)
    monkeypatch.setattr(money, "ledger", lambda cur: {
        "total_usd": 1584.21, "customers_usd": 794.58,
        "pre_relaunch_usd": 59.97, "unlinked_usd": 729.66,
        "excluded_usd": 0.0, "unlinked_payments": 35,
        "unlinked_note": "35 payments"})
    monkeypatch.setattr(admin_v2, "costs_report", lambda fresh=False: {
        "economics_30d": {"cash_revenue_usd": 577.08,
                          "executor_ceiling_usd": 10.0,
                          "database_storage_usd": 1.5,
                          "cash_gross_margin_pct": 80.1},
        "split": {"model_customers_usd": 3.0, "model_owner_usd": 40.0,
                  "storage_customers_usd_month": 1.0,
                  "storage_owner_usd_month": 2.0,
                  "margin_excl_owner_pct": 95.2},
        "computed_at": "2026-10-10T12:00:00Z"})
    d = get(app, "/admin/v2/revenue?range=30d").json["data"]
    assert [t["key"] for t in d["tiles"]] == [
        "mrr", "paying_now", "cash", "new_paying", "stopped_paying",
        "payment_failing"]
    for t in d["tiles"]:
        assert_metric(t)
    assert d["tiles"][0]["value"] == 412.5 and d["tiles"][1]["value"] == 21
    assert d["tiles"][4]["status"] == "unavailable" and d["tiles"][4]["note"]
    assert d["avg_plan_value"]["value"] == round(412.5 / 21, 2)
    assert d["ever_paid"]["value"] == 34 and d["canceled_ever"]["value"] == 12
    assert d["cash_series"]["grain"] == "day"
    assert len(d["cash_series"]["rows"]) == 30
    assert len(d["cash_series"]["average"]) == 30
    assert d["by_plan"][0]["tier"] == "grandfathered"
    ledger = d["ledger"]
    assert round(ledger["customers_usd"] + ledger["pre_relaunch_usd"]
                 + ledger["unlinked_usd"] + ledger["excluded_usd"], 2) == \
        ledger["total_usd"]
    assert d["costs"]["model_owner_usd"] == 40.0
    assert d["costs"]["margin_excl_owner_pct"] == 95.2


def test_payments_ledger_pages_and_labels_accounts(app):
    app.fake.routes = [
        ("SELECT count(*) AS n FROM payments", [{"n": 3}]),
        ("ORDER BY COALESCE(p.occurred_at, p.created_at) DESC", [
            {"id": 1, "at": datetime(2026, 8, 3), "amount_cents": 2000,
             "currency": "USD", "status": "completed", "plan": "plus",
             "origin": "subscription_recurring", "error_code": None,
             "ok": True, "failed": False, "user_id": None, "email": None,
             "joined": None, "user_plan": None, "user_status": None},
            {"id": 2, "at": datetime(2026, 10, 1), "amount_cents": 1500,
             "currency": "USD", "status": "past_due", "plan": "ai",
             "origin": "web", "error_code": "declined", "ok": False,
             "failed": True, "user_id": 7, "email": "customer1@example.com",
             "joined": datetime(2026, 9, 1), "user_plan": "ai",
             "user_status": "payment_failing"},
        ]),
    ]
    d = get(app, "/admin/v2/revenue/payments").json["data"]
    assert d["total"] == 3 and len(d["rows"]) == 2
    assert d["rows"][0]["account_note"] == "Deleted account"
    assert d["rows"][0]["customer"] is None
    assert d["rows"][1]["status_label"] == "Failed"


def test_billing_problems_are_plain_sentences(app, monkeypatch):
    monkeypatch.setattr(money, "billing_problems", lambda cur: ([{
        "id": 9, "email": "customer2@example.com", "plan": "ai",
        "problem": "not_in_paddle", "payment_failed_at": None,
        "billing_synced_at": datetime(2026, 10, 10, 11)}],
        datetime(2026, 10, 10, 11)))
    d = get(app, "/admin/v2/revenue/billing-problems").json["data"]
    assert d["last_checked_at"] == "2026-10-10T11:00:00Z"
    row = d["rows"][0]
    assert row["problem"] == "Paddle doesn't recognise this subscription"
    assert (row["ours"], row["paddle"]) == ("Subscribed", "Not found")


# ── 25–27. health and live ───────────────────────────────────────────────
def test_health_contract_and_owner_toggle(app, monkeypatch):
    seen = []
    summary_fixtures(monkeypatch)
    monkeypatch.setattr(health, "export_steps", lambda cur, p, o=False: {
        "stages": [], "failures": []})
    monkeypatch.setattr(health, "waits", lambda cur, p, o=False: (
        seen.append(o) or {"points": [], "worst": [], "truncated": False,
                           "medians": {"upload_s": 30.0, "analysis_s": None,
                                       "edit_s": 9.0}}))
    monkeypatch.setattr(health, "projects_exported_share", lambda cur, o=False:
                        registry.metric("projects_exported_share", 25.0))
    d = get(app, "/admin/v2/health?range=7d").json["data"]
    assert set(d) == {"engine", "failures", "messages_without_edit", "uploads",
                      "export_steps", "waits", "projects_exported_share"}
    assert d["waits"]["upload_median_s"] == 30.0
    assert set(d["messages_without_edit"]) == {"paying", "free", "recent"}
    r = get(app, "/admin/v2/health?range=7d&include_owner=1")
    assert r.json["meta"]["scope"]["include_owner"] is True
    assert seen == [False, True]


def test_live_never_exposes_ips_or_user_agents(app, monkeypatch):
    monkeypatch.setattr(visitors, "internal_ids", lambda cur: [])
    app.fake.routes = [
        ("count(DISTINCT pv.device_id) AS n", [{"n": 3}]),
        ("u.last_seen_at >= NOW()", [{"n": 1}]),
        ("GROUP BY 1 ORDER BY 1", [{"type": "preview", "running": 1,
                                    "queued": 0}]),
        ("ORDER BY pv.visited_at DESC LIMIT 50", [{
            "visited_at": datetime(2026, 10, 10, 18), "page": "/",
            "device_type": "mobile", "referrer": "www.facebook.com",
            "attribution": {"first": {"source": "instagram",
                                      "code": "c" * 16}},
            "active_s": 0, "scroll": 0, "clicked": False}]),
    ]
    d = get(app, "/admin/v2/live").json["data"]
    assert_metric(d["people_now"], "people_now")
    assert d["people_now"]["value"] == 3
    hit = d["recent_hits"][0]
    assert set(hit) == {"at", "page", "device_type", "channel", "active_s",
                        "class"}
    assert hit["class"] == "link_preview" and hit["channel"] == "outreach"
    assert d["running"][0]["label"] == "Preview"


def test_every_v2_route_is_read_only_and_admin_gated():
    app = Flask(__name__)
    app.config.update(SECRET_KEY=SECRET)
    app.register_blueprint(admin_v2.admin_v2_bp)
    rules = [r for r in app.url_map.iter_rules()
             if r.rule.startswith("/admin/v2")]
    assert len(rules) == 28
    for r in rules:
        assert r.methods - {"HEAD", "OPTIONS"} == {"GET"}, r.rule
        view = app.view_functions[r.endpoint]
        assert view.__wrapped__ is not None, r.rule      # admin_required


# ── Integration review: numbers that must agree with their own definitions ──
def test_signup_rate_never_divides_all_signups_by_tracked_visitors(
        app, monkeypatch):
    """6 Jul → today used to read 357%: 1,687 signups ÷ people counted only
    since 3 Oct. The rate now covers the tracked part on both sides."""
    summary_fixtures(monkeypatch)
    monkeypatch.setattr(visitors, "classify",
                        lambda cur, p: dict(CLASSES, person=500))
    seen = []

    def signups(cur, p):
        seen.append(p.start)
        return 100 if p.start >= datetime(2026, 10, 3, 16, 7, 35,
                                          tzinfo=timezone.utc) else 1687
    monkeypatch.setattr(admin_v2, "signups_count", signups)
    today = ranges.local_today()
    d = get(app, "/admin/v2/summary?range=custom&from=2026-07-06"
                 f"&to={today.isoformat()}").json["data"]
    by = {k["key"]: k for k in d["kpis"]}
    assert by["signups"]["value"] == 1687          # the whole period
    rate = by["signup_rate"]
    assert rate["value"] == 20.0                   # 100 since 3 Oct ÷ 500
    assert rate["status"] == "partial" and "3 Oct" in rate["note"]
    assert rate["previous"] is None
    assert rate["value"] <= 100


def test_channel_rates_use_one_window_when_the_period_starts_before_sources(
        app, monkeypatch):
    acquisition_fixtures(monkeypatch)
    since = datetime(2026, 10, 7, 12, 34, 12, tzinfo=timezone.utc)
    g = {"source": "www.google.com", "medium": "organic", "at": 1}
    whole = [{"device_id": f"w{i}", "first_attribution": {"first": g,
                                                         "last": g},
              "first_referrer": "www.google.com", "first_page": "/"}
             for i in range(4)]
    monkeypatch.setattr(visitors, "people_rows", lambda cur, p: (
        whole[:1] if p.start is not None and p.start >= since else whole))
    d = get(app, "/admin/v2/acquisition?range=30d").json["data"]
    by = {c["channel"]: c for c in d["channels"]}
    assert by["search"]["people"] == 4             # the whole period
    assert by["search"]["signup_rate"] == 100.0    # 1 signup ÷ 1 person since 7 Oct
    assert d["rate_since"] == "2026-10-07T12:34:12Z"
    today = get(app, "/admin/v2/acquisition?range=today").json["data"]
    assert today["rate_since"] is None


def test_span_labels_say_the_year_when_it_is_not_obvious():
    assert ranges.span_label(date(2025, 9, 6), date(2026, 10, 10)) == \
        "6 Sep 2025 – 10 Oct 2026"
    assert ranges.span_label(date(2026, 9, 28), date(2026, 10, 3), 2026) == \
        "28 Sep – 3 Oct"
    assert ranges.span_label(date(2025, 3, 1), date(2025, 3, 9), 2026) == \
        "1–9 Mar 2025"
    p = ranges.parse({"range": "custom", "from": "2025-09-06",
                      "to": "2026-10-10"}, now=T0)
    assert p.label == "6 Sep 2025 – 10 Oct 2026"


def test_landing_pages_skip_sign_in_and_app_pages():
    import re
    assert re.search(channels_report.APP_PAGES, "/google-callback/[redacted]")
    for page in ("/login", "/verify", "/studio", "/paddle-checkout/x",
                 "/purchase-success"):
        assert re.search(channels_report.APP_PAGES, page), page
    for page in ("/", "/tools/color-grade-video", "/subscribe", "/mcp",
                 "/login-help", "/blog/studio-tips"):
        assert not re.search(channels_report.APP_PAGES, page), page
    cur = FakeCursor([("array_agg(v.page", [
        {"page": None, "people": 9, "active_median_s": None, "signups": 0},
        {"page": "/", "people": 85, "active_median_s": 15.2, "signups": 46},
        {"page": "/tools/x", "people": 70, "active_median_s": 84.0,
         "signups": 4}])])
    db.reset_features()
    import admin_metrics.visitors as v
    orig = v.internal_ids
    v.internal_ids = lambda c: []
    try:
        period = ranges.make_period("30d", date(2026, 9, 11),
                                    date(2026, 10, 10), now=T0)
        out = channels_report.landing_pages(cur, period, limit=2)
    finally:
        v.internal_ids = orig
    assert out["app_only_people"] == 9
    assert [r["page"] for r in out["rows"]] == ["/", "/tools/x"]
    sql = next(s for s in cur.sql if "array_agg" in s)
    assert "FILTER (WHERE v.page !~ %(app_pages)s)" in sql


def test_a_large_project_says_what_still_opens():
    out = {"turns": [], "messages": {"rows": [], "has_more": False,
                                     "before_id": None, "total": 0},
           "jobs": {"rows": [], "total": 0},
           "versions": [{"version": i, "summary": "x" * 3000}
                        for i in range(600)],
           "shorts": [], "truncated": False, "truncated_reason": None}
    capped = projects.enforce_cap(out, cap=256 * 1024)
    assert capped["truncated"] and capped["versions"] == []
    assert "the version list" in capped["truncated_reason"]
    assert "a version still opens by its number" in \
        capped["truncated_reason"].lower()
    assert "Open their tabs" not in capped["truncated_reason"]


def test_a_shortened_body_keeps_the_whole_response_under_1_mb():
    """A 1.1 MB video analysis came back as 1.07 MiB: the cut JSON text was
    escaped again inside a string. The cut now accounts for that."""
    quoted = {"words": [{"w": '"quoted"', "t": i} for i in range(60000)]}
    body, cut = projects._capped(quoted)
    assert cut and set(body) == {"_truncated_json"}
    sent = json.dumps({"meta": {"x": "y" * 2000}, "data": {"index": body}})
    assert len(sent) < MB
    small, cut_small = projects._capped({"a": 1})
    assert small == {"a": 1} and not cut_small


def test_trend_series_the_page_did_not_ask_for_are_null_not_zero(
        app, monkeypatch):
    monkeypatch.setattr(visitors, "classify_by_day",
                        lambda cur, p: {d: dict(CLASSES) for d in p.day_list()})
    monkeypatch.setattr(admin_v2, "signups_by_day",
                        lambda cur, p: {d: 2 for d in p.day_list()})
    r = get(app, "/admin/v2/trend?range=7d&metrics=people,signups")
    rows = r.json["data"]["rows"]
    assert rows and all(row["signups"] == 2 for row in rows)
    assert all(row["new_paying"] is None and row["cash_usd"] is None
               for row in rows)
    assert all(v is None for v in r.json["data"]["averages"]["cash_7d"])


def test_digits_search_ids_and_emails(app):
    """"1987" finds customer 1987 and jo1987@…; it used to find only the id."""
    app.fake.routes = customer_routes()
    assert get(app, "/admin/v2/customers?q=1987").status_code == 200
    sql = next(s for s in app.fake.sql if ", agg AS (" in s)
    assert "(u.id = %(q_id)s OR u.email ILIKE %(q_like)s)" in sql
    app.fake.sql.clear()
    app.fake.routes = project_list_routes()
    assert get(app, "/admin/v2/projects?q=1987").status_code == 200
    assert any("(p.id = %(q_id)s OR p.title ILIKE %(q_like)s OR u.email "
               "ILIKE %(q_like)s)" in s for s in app.fake.sql)
    # A very long number is text, never an id that overflows.
    app.fake.sql.clear()
    app.fake.routes = customer_routes()
    assert get(app, "/admin/v2/customers?q=" + "9" * 30).status_code == 200
    assert not any("q_id" in s for s in app.fake.sql)
