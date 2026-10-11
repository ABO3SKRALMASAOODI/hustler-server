"""Metric definitions that need no database: one meaning per number."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from admin_metrics import (channels_report, defs, health, projects, registry,
                           visitors)

BEFORE = datetime(2026, 9, 1, 12, 0)                 # before source tracking
AFTER = datetime(2026, 10, 9, 12, 0)                 # after 7 Oct 12:34 UTC
GOOGLE = {"source": "www.google.com", "medium": "organic", "at": 1_760_000_000_000}


def signup(created, has_row=True, tracking=None, first=None, last=None, told=None,
           page=None):
    return {"created_at": created, "has_row": has_row, "tracking": tracking,
            "attribution": None if first is None and last is None
            else {"first": first, "last": last},
            "first_page": page, "told": told, "paid": False, "cents": 0}


@pytest.mark.parametrize("row, channel, reason", [
    (signup(AFTER, first=GOOGLE, last=GOOGLE), "search", None),
    (signup(AFTER, tracking="privacy_signal"), "not_recorded", "privacy_browser"),
    (signup(AFTER, tracking="no_identity"), "not_recorded", "nothing_sent"),
    (signup(AFTER, tracking="tracked", first=None, last=GOOGLE), "not_recorded",
     "landing_lost"),
    (signup(AFTER, has_row=False), "not_recorded", "no_row_unknown"),
    (signup(BEFORE, has_row=False), "not_recorded", "before_tracking"),
    (signup(BEFORE, tracking=None), "not_recorded", "before_tracking"),
    (signup(AFTER, tracking="estimated_from_referrer",
            first={"source": "chatgpt.com", "medium": "estimated", "at": 1},
            last={"source": "chatgpt.com", "at": 1}), "ai_assistant", None),
])
def test_every_signup_has_a_channel_or_a_reason(row, channel, reason):
    t = channels_report.touch_for(row, "first")
    assert t["channel"] == channel
    assert t.get("reason") == reason
    if reason:
        assert t["reason_label"]
    complete = channels_report.complete_touch(t)
    assert set(complete) == {"channel", "channel_label", "detail",
                             "detail_label", "campaign", "campaign_label",
                             "code", "at", "estimated", "reason",
                             "reason_label"}


def test_latest_model_reads_the_latest_touch():
    row = signup(AFTER, first={"source": "direct", "at": 1}, last=GOOGLE)
    assert channels_report.touch_for(row, "first")["channel"] == "no_referrer"
    assert channels_report.touch_for(row, "last")["channel"] == "search"


def test_estimates_are_counted_in_their_channel_and_flagged():
    rows = [signup(AFTER, first=GOOGLE),
            signup(AFTER, tracking="estimated_from_referrer", first=GOOGLE),
            signup(AFTER, tracking="privacy_signal"),
            signup(BEFORE, has_row=False, told="ai_chatbot"),
            signup(BEFORE, has_row=False)]
    for r in rows:
        r["touch"] = channels_report.touch_for(r)
    cov = channels_report.coverage(rows)
    assert (cov["signups"], cov["tracked"], cov["estimated"]) == (5, 1, 1)
    assert {r["reason"]: r["signups"] for r in cov["not_recorded"]} == \
        {"before_tracking": 2, "privacy_browser": 1}
    before = channels_report.before_tracking(rows)
    assert before == {"signups": 2, "no_answer": 1,
                      "told": [{"told": "ai_chatbot",
                                "told_label": "ChatGPT / AI", "signups": 1}]}


def test_told_vs_measured_uses_only_signups_with_both():
    rows = [signup(AFTER, first=GOOGLE, told="ai_chatbot"),
            signup(AFTER, first=GOOGLE, told="ai_chatbot"),
            signup(AFTER, first={"source": "chatgpt.com", "at": 1}, told="youtube"),
            signup(AFTER, first=GOOGLE),                         # no answer
            signup(AFTER, has_row=False, told="google")]         # not measured
    for r in rows:
        r["touch"] = channels_report.touch_for(r)
    tvm = channels_report.told_vs_measured(rows)
    assert tvm["columns"] == ["search", "ai_assistant"]
    first = tvm["rows"][0]
    assert first["told"] == "ai_chatbot" and first["counts"]["search"] == 2
    assert sum(r["total"] for r in tvm["rows"]) == 3


def test_survey_includes_the_new_outreach_answer():
    from routes.onboarding import CHANNELS
    assert "valmera_message" in CHANNELS
    assert channels_report.survey_label("valmera_message") == \
        "A message from Valmera"


def test_people_fall_back_to_the_stored_referrer_before_labels_existed():
    row = {"first_attribution": None, "first_referrer": "chatgpt.com",
           "first_page": "/"}
    assert channels_report._people_touch(row, "first") == \
        {"source": "chatgpt.com"}
    row["first_referrer"] = ""
    assert channels_report._people_touch(row, "first") == {"source": "direct"}
    row["first_referrer"] = "valmera.io"
    assert channels_report._people_touch(row, "first") is None


def test_visitor_classes_follow_the_documented_order():
    sql = visitors.CLASS_SQL
    order = [sql.index(k) for k in ("'robot'", "'internal'", "d.interacted",
                                    "'link_preview'", "d.active_s > 30",
                                    "'no_signal'")]
    assert order == sorted(order)
    assert "d.scroll > 0" in sql and "d.pages >= 2" in sql and "d.clicked" in sql
    assert defs.ROBOT_UA_RE.search("Mozilla/5.0 (Linux; Android 6.0.1; "
                                   "Nexus 5X Build/MMB29P)")
    assert not defs.ROBOT_UA_RE.search("Mozilla/5.0 (iPhone) Instagram 300.0")


def test_customer_population_excludes_owner_and_test_accounts():
    sql = defs.customer("u")
    assert "u.is_verified = 1" in sql
    assert f"DATE '{defs.METRICS_EPOCH}'" in sql
    assert "'thevalmera@gmail.com'" in sql
    assert defs.population("u", include_owner=True).count("thevalmera") == 2
    assert defs.success("p") == \
        "(p.status IN ('completed','paid') AND p.amount_cents > 0)"


def test_every_registry_entry_is_complete_and_short():
    for key, d in registry.REGISTRY.items():
        assert set(d) == {"label", "how", "unit", "polarity", "section"}, key
        assert d["unit"] in ("count", "usd", "percent", "seconds"), key
        assert d["polarity"] in ("up_good", "down_good", "neutral"), key
        assert 0 < len(d["how"]) <= registry.HOW_MAX, key
        for banned in ("build", "trial", " job", "EDL", "egress", "wall"):
            assert banned not in d["how"] and banned not in d["label"], key


def test_unknown_is_never_zero():
    m = registry.metric("people", None)
    assert m["value"] is None and m["status"] == "unavailable"
    e = registry.error_metric("cash")
    assert e["value"] is None and e["status"] == "error" and e["note"]
    ok = registry.metric("cash", 0.0, 12.5)
    assert ok["value"] == 0.0 and ok["previous"] == 12.5 and ok["status"] == "ok"
    assert registry.metric("cash", 1, 2, comparable=False)["previous"] is None


# ── Project page: bounded, nothing copied twice ─────────────────────────
def test_sixty_slices_of_one_request_reference_activity_once():
    ordered = [(1, "user")] + [(i, "activity") for i in range(2, 1202)] + \
        [(1202, "assistant"), (1203, "user")]
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    jobs = [{"id": 100 + i, "state": "done", "created_at": base,
             "updated_at": base + timedelta(seconds=30), "error": None,
             "message_id": "1", "root_id": "100", "credits": None}
            for i in range(60)]
    turns = projects.group_turns(jobs, ordered)
    assert len(turns) == 1
    assert len(turns[0]["slices"]) == 60
    assert len(turns[0]["activity_message_ids"]) == 1201
    body = json.dumps(turns)
    assert "content" not in body and "text" not in body


def test_project_page_is_capped_by_dropping_the_largest_section():
    out = {"turns": [], "messages": {"rows": [{"text": "x" * 4000}] * 1200,
                                     "has_more": True, "before_id": 9,
                                     "total": 5000},
           "jobs": {"rows": [], "total": 0}, "versions": [], "shorts": [],
           "truncated": False, "truncated_reason": None}
    capped = projects.enforce_cap(out, cap=1024 * 1024)
    assert capped["truncated"] and "messages" in capped["truncated_reason"]
    assert capped["messages"]["rows"] == [] and capped["messages"]["total"] == 5000
    assert len(json.dumps(capped)) < 1024 * 1024


def test_messages_are_shortened_with_a_flag():
    m = projects.message_out({"id": 1, "role": "user", "content": "y" * 9000,
                              "meta": {"kind": "concierge", "tool": "cut",
                                       "args": {"secret": 1}},
                              "created_at": datetime(2026, 10, 1)})
    assert len(m["text"]) == projects.TEXT_MAX and m["text_truncated"]
    assert m["kind"] == "concierge" and m["meta"] == {"tool": "cut"}


# ── Health wording and rules ─────────────────────────────────────────────
@pytest.mark.parametrize("raw, key", [
    ("over size cap", "too_large"), ("File is larger than the 500 MB limit", "too_large"),
    ("empty or incomplete file", "empty_file"), ("over duration cap", "too_long"),
    ("Your browser couldn't reach our storage host — VPN", "network_blocked"),
    ("Upload timed out", "timed_out"), (None, "unknown"), ("weird", "other"),
])
def test_upload_reasons_are_grouped_in_plain_words(raw, key):
    assert health.reason_key(raw) == key
    assert health.REASON_LABELS[key]


def test_errors_never_show_a_stack_trace():
    err = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: bad frame"
    assert health.plain_error(err) == "ValueError: bad frame"
    assert health.plain_error("one\ntwo") == "one"
    assert health.plain_error(None) is None


def test_health_pills_use_the_documented_thresholds(monkeypatch):
    from admin_metrics import ranges
    period = ranges.parse({"range": "today"})
    monkeypatch.setattr(health, "failures", lambda *a, **k: {
        "metric": registry.metric("customer_failures", 5, breakdown=[
            registry.item("people_affected", 3)])})
    monkeypatch.setattr(health, "uploads", lambda *a, **k: {"failed_people": 2})
    monkeypatch.setattr(health, "messages_without_edit", lambda *a, **k: {
        "paying": 1, "free": 2})
    monkeypatch.setattr(health, "engine", lambda *a, **k: {
        "status": "good", "text": "Working", "queued": 0})
    items = {i["key"]: i for i in health.health_items(None, period, 1, 0)}
    assert list(items) == ["customer_failures", "failed_uploads",
                           "payment_problems", "messages_without_edit",
                           "editing_engine"]
    assert items["customer_failures"]["status"] == "critical"
    assert items["failed_uploads"]["status"] == "warning"
    assert items["payment_problems"]["status"] == "warning"
    assert items["messages_without_edit"]["status"] == "critical"
    assert items["editing_engine"]["status"] == "good"
    assert all(i["href"].startswith("/admin/") for i in items.values())


def test_billing_problems_and_money_use_the_customer_population():
    from admin_metrics import money

    class Cur:
        def __init__(self):
            self.sql = []

        def execute(self, sql, params=None):
            self.sql.append(" ".join(sql.split()))

        def fetchall(self):
            return []

        def fetchone(self):
            return {"t": None}
    cur = Cur()
    rows, last = money.billing_problems(cur)
    assert rows == [] and last is None
    assert "u.is_verified = 1" in cur.sql[0] and "{CUSTOMER}" not in cur.sql[0]
    # Paying and MRR are the same population by construction.
    assert defs.paying("u") in money.STATUS_SQL
    assert money.plan_tier("mcp_connect") == "current"
    assert money.plan_tier("ai") == "grandfathered"
    assert money.plan_tier("ultra") == "retired"
