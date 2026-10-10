"""Independent review of the admin rebuild: one test per defect found.

Each test pins a number the owner reads to the definition it claims, using
scripted cursors only (no database).
"""
from datetime import date, datetime, timezone

import pytest

import acquisition
from admin_metrics import (attention, channels_report, db, defs, health,
                           money, ranges)

AFTER = datetime(2026, 10, 9, 12, 0)


class Cur:
    """Answers by the first matching substring; records flattened SQL."""

    def __init__(self, routes=()):
        self.routes = list(routes)
        self.sql, self.params = [], []
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.sql.append(flat)
        self.params.append(params)
        for needle, rows in self.routes:
            if needle in flat:
                self._rows = rows(params) if callable(rows) else rows
                return
        self._rows = []

    def fetchall(self):
        return [dict(r) for r in self._rows]

    def fetchone(self):
        return dict(self._rows[0]) if self._rows else None


# ── Stopped paying: no false alarm before today's snapshot exists ────────
def test_stopped_paying_ignores_the_day_whose_next_snapshot_is_not_taken(
        monkeypatch):
    monkeypatch.setattr(money, "snapshots_since", lambda cur: date(2026, 10, 1))
    cur = Cur([("FROM steps s", [{"n": 0}])])
    period = ranges.make_period(
        "today", date(2026, 10, 11), date(2026, 10, 11),
        now=datetime(2026, 10, 10, 20, 30, tzinfo=timezone.utc))
    assert money.stopped_paying(cur, period) == 0
    sql = cur.sql[-1]
    # Between Dubai midnight and the first hourly tick there is no row for
    # today at all, and a day the billing tick never ran has none either:
    # only captured days are compared, each with the captured day before it,
    # so neither reads as every paying customer stopping (one query, no
    # "day + 1" that a missing day would turn into a false alarm).
    assert len(cur.sql) == 1
    assert "lag(day) OVER (ORDER BY day)" in sql
    assert "SELECT DISTINCT day FROM billing_daily_status" in sql
    assert "day + 1" not in sql
    assert cur.params[-1] == {"from": date(2026, 10, 11),
                              "to": date(2026, 10, 11)}


# ── Customers agree with Growth about where a signup came from ──────────
def test_customer_came_from_uses_the_first_page_like_growth():
    direct = {"source": "direct", "medium": "none", "at": 1}
    row = {"id": 5, "created_at": AFTER, "has_row": True,
           "attribution": {"first": direct, "last": direct},
           "tracking": "tracked", "first_page": "/mcp/authorize"}
    cur = Cur([("fp ON TRUE WHERE u.id = ANY", [row])])
    first, last, tracking = channels_report.customer_touches(cur, [5])[5]
    assert "LEFT JOIN LATERAL" in cur.sql[-1]
    assert (first["channel"], first["detail"]) == ("ai_assistant", "mcp")
    growth = channels_report.touch_for(dict(row), "first")
    assert growth["channel"] == first["channel"]
    assert tracking == "tracked"


# ── Sign-in and checkout pages are never a source ───────────────────────
@pytest.mark.parametrize("host", ["accounts.google.com", "appleid.apple.com",
                                  "checkout.paddle.com", "buy.paddle.com",
                                  "valmera.io", "www.valmera.io"])
def test_sign_in_and_checkout_hosts_are_not_sources(host):
    assert acquisition.channel({"source": host, "medium": "referral",
                                "at": 1})["channel"] == "not_recorded"
    row = {"created_at": AFTER, "has_row": True, "tracking": "tracked",
           "attribution": {"first": {"source": host, "at": 1},
                           "last": {"source": host, "at": 1}},
           "first_page": "/legal"}
    t = channels_report.touch_for(row, "first")
    assert (t["channel"], t["reason"]) == ("not_recorded", "landing_lost")


def test_real_google_hosts_are_still_classified():
    assert acquisition.channel({"source": "www.google.com",
                                "at": 1})["channel"] == "search"
    assert acquisition.channel({"source": "docs.google.com",
                                "at": 1})["channel"] == "other_website"


def test_people_from_a_sign_in_page_are_landing_lost(monkeypatch):
    from admin_metrics import visitors
    monkeypatch.setattr(visitors, "people_rows", lambda cur, p: [
        {"device_id": "a", "first_attribution": None,
         "first_referrer": "accounts.google.com", "first_page": "/legal"},
        {"device_id": "b", "first_attribution": None,
         "first_referrer": "www.google.com", "first_page": "/"}])
    out = channels_report.people_by_channel(None, None)
    assert out["not_recorded"] == {"people": 1,
                                   "details": {"landing_lost": 1}}
    assert out["search"]["people"] == 1


# ── Attention shows each customer's real status ─────────────────────────
def test_billing_mismatch_carries_the_customers_status():
    cur = Cur([("AS problem", [{
        "id": 9, "email": "customer@example.com", "plan": "ai",
        "problem": "canceled_but_subscribed", "status": "canceled",
        "billing_synced_at": datetime(2026, 10, 10, 11),
        "payment_failed_at": None}])])
    items = attention.billing_mismatches(cur)
    assert items[0]["customer"]["status"] == "canceled"
    assert "AS status" in cur.sql[0] and "{STATUS}" not in cur.sql[0]


def test_message_without_edit_carries_the_customers_status():
    rows = [{"id": 1, "created_at": AFTER, "project_id": 3, "user_id": 7,
             "email": "customer@example.com", "plan": "ai", "paying": False,
             "reply_kind": "subscription_required"}]
    cur = Cur([("reply_kind", rows),
               ("WHERE u.id = ANY", [{"id": 7,
                                      "status": "payment_failing"}])])
    m = health.messages_without_edit(
        cur, ranges.make_period("7d", date(2026, 10, 4), date(2026, 10, 10)))
    assert m["recent"][0]["customer"]["status"] == "payment_failing"
    assert m["rows"][0]["status"] == "payment_failing"


# ── "Already customers" is unknown, not 0, before the beacon sends it ───
def test_returning_customers_is_null_until_the_beacon_reports_it(monkeypatch):
    from routes import admin_v2
    period = ranges.make_period(
        "today", date(2026, 10, 12), date(2026, 10, 12),
        now=datetime(2026, 10, 12, 10, tzinfo=timezone.utc))
    monkeypatch.setattr(admin_v2, "_tracking_meta",
                        lambda cur=None: {"interaction_since": None})
    assert admin_v2._signed_in_known(period) is False
    monkeypatch.setattr(admin_v2, "_tracking_meta", lambda cur=None: {
        "interaction_since": "2026-10-11T09:00:00Z"})
    assert admin_v2._signed_in_known(period) is True
    monkeypatch.setattr(admin_v2, "_tracking_meta", lambda cur=None: {
        "interaction_since": "2026-10-12T09:00:00Z"})
    assert admin_v2._signed_in_known(period) is False


@pytest.fixture(autouse=True)
def _fresh_features():
    db.reset_features()
    yield
    db.reset_features()


# ── Integration review: attention rows say what happened in words ────────
def test_a_message_without_an_edit_says_what_the_customer_got_back():
    assert attention.reply_detail("concierge") == \
        "They got a concierge reply instead of an edit."
    assert attention.reply_detail("subscription_required") == \
        "They got the subscription notice instead of an edit."
    assert attention.reply_detail("other_kind") == \
        "They got a other kind reply instead of an edit."
    assert attention.reply_detail(None) == \
        "No editing work followed within 15 minutes."


# ── "Latest" model: people move with their latest touch, like signups ───
def test_latest_model_counts_people_by_their_latest_touch():
    g = {"source": "www.google.com", "medium": "organic", "at": 1}
    c = {"source": "chatgpt.com", "medium": "", "at": 2}
    row = {"first_attribution": {"first": g, "last": g},
           "last_attribution": {"first": g, "last": c},
           "first_referrer": "", "first_page": "/"}
    assert channels_report._people_touch(row, "first") == g
    assert channels_report._people_touch(row, "last") == c
    # A row without the latest attribution (older callers) still works.
    row.pop("last_attribution")
    assert channels_report._people_touch(row, "last") == g


# ── Live: a hit the tracker marked as touched is a person's ─────────────
def test_live_counts_an_interacted_hit_as_a_person(monkeypatch):
    from admin_metrics import live
    monkeypatch.setattr(db, "has_column", lambda c, t, col: True)
    monkeypatch.setattr(live.visitors, "internal_ids", lambda c: [])
    hit = {"visited_at": AFTER, "page": "/", "device_type": "mobile",
           "referrer": "", "attribution": None, "active_s": 4, "scroll": 0,
           "interacted": True, "clicked": False}
    cur = Cur([("count(DISTINCT pv.device_id)", [{"n": 1}]),
               ("u.last_seen_at >= NOW()", [{"n": 0}]),
               ("GROUP BY 1 ORDER BY 1", []),
               ("ORDER BY pv.visited_at DESC LIMIT 50", [hit])])
    out = live.live(cur)
    assert out["recent_hits"][0]["class"] == "person"
    assert "COALESCE(pv.interacted, FALSE) AS interacted" in cur.sql[-1]
    assert "user_agent" not in out["recent_hits"][0]
