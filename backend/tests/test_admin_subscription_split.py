"""One subscriber status everywhere, and the split Paddle's dashboard counts.

Paddle's "Active subscribers" (23 on 16 Oct 2026) was 8 renewing + 12
cancelled-but-still-in-their-paid-period + 3 payment failing; the admin said
"active" for all 20 Paddle-active ones. These tests pin the split, both MRRs
and the single status vocabulary that rows, filters and counts share.
"""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_metrics import cache, subscriptions as subs  # noqa: E402
from admin_metrics.subscriptions import (  # noqa: E402
    ids_in, plan_of, snapshot_state, split, summarize)
from routes import admin, admin_video  # noqa: E402
from tests.test_admin_legacy_reliability import Conn, Cur, H, app_with  # noqa: E402


def dt(day):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


def sub(status, action=None, plan="ai", period="monthly", user=1, **kw):
    row = {"status": status, "scheduled_action": action,
           "next_billed_at": dt("2026-11-14") if status in ("active", "past_due")
           and action is None else None,
           "scheduled_effective_at": dt("2026-11-08") if action else None,
           "canceled_at": dt("2026-09-27") if status == "canceled" else None,
           "period_ends_at": dt("2026-11-08"), "observed_at": dt("2026-10-16"),
           "user_id": user, "account_plan": plan if user else None,
           "account_period": period if user else None,
           "paid_plan": plan, "paid_at": dt("2026-10-08")}
    row.update(kw)
    return row


def production_shape():
    """16 Oct 2026, as read from admin_subscription_snapshots."""
    rows = [sub("active", plan="ai") for _ in range(5)]
    rows += [sub("active", plan="mcp_connect") for _ in range(3)]
    rows += [sub("active", "cancel", plan="ai") for _ in range(5)]
    rows += [sub("active", "cancel", plan="mcp_connect") for _ in range(4)]
    rows += [sub("active", "cancel", plan="ai_max"),
             sub("active", "cancel", plan="ai", period="yearly"),
             # The account was deleted; Paddle still counts the subscription.
             sub("active", "cancel", plan="ai", user=None,
                 paid_at=dt("2026-09-24"), period_ends_at=dt("2026-10-24"))]
    rows += [sub("past_due", plan="ai") for _ in range(3)]
    rows += [sub("canceled", user=None) for _ in range(13)]
    return rows


def test_split_matches_paddles_dashboard_and_both_mrrs():
    result = split(production_shape())
    assert (result["renewing"], result["cancelled_with_access"],
            result["payment_failing"], result["paddle_active"]) == (8, 12, 3, 23)
    assert result["ended"] == 13
    # 5 x $15 + 3 x $25
    assert result["renewing_mrr_usd"] == 150.0
    # + 5 x $15 + 4 x $25 + $50 + $150/12 + $15 (the deleted account)
    assert result["paddle_mrr_usd"] == 402.5
    assert result["payment_failing_mrr_usd"] == 45.0
    assert result["without_account"] == {
        "renewing": 0, "cancelled_with_access": 1, "payment_failing": 0}
    assert result["checked_at"] == "2026-10-16T00:00:00Z"


def test_a_subscription_without_an_account_takes_its_plan_from_payments():
    monthly = sub("active", "cancel", user=None, paid_at=dt("2026-09-24"),
                  period_ends_at=dt("2026-10-24"))
    yearly = sub("active", "cancel", user=None, paid_at=dt("2026-09-28"),
                 period_ends_at=dt("2027-09-28"))
    assert plan_of(monthly) == ("ai", "monthly")
    assert plan_of(yearly) == ("ai", "yearly")
    # A failing account is downgraded to free; the plan it pays for counts.
    assert plan_of(sub("past_due", account_plan="free")) == ("ai", "monthly")


def test_one_state_vocabulary_from_the_snapshot():
    assert snapshot_state(sub("active")) == ("renews", dt("2026-11-14"))
    assert snapshot_state(sub("active", "cancel")) == ("cancelled", dt("2026-11-08"))
    assert snapshot_state(sub("past_due"))[0] == "past_due"
    assert snapshot_state(sub("canceled")) == ("ended", dt("2026-09-27"))
    assert snapshot_state(sub("active", "pause")) == ("pausing", dt("2026-11-08"))
    assert snapshot_state(sub("paused"))[0] == "paused"
    assert snapshot_state(sub("trialing", next_billed_at=dt("2026-10-20"))) == (
        "trialing", dt("2026-10-20"))
    assert snapshot_state({"status": "active"})[0] == "unknown"


def group(sid="sub_1", cents=1500):
    return dict(subscription_id=sid, first_paid_at=dt("2026-09-08"),
                last_paid_at=dt("2026-10-08"), payment_count=2,
                paid_cents=cents)


def test_rows_date_failing_and_ended_accounts_without_guessing():
    failed = datetime(2026, 9, 26, 9, 27)
    # Paddle says past_due: the date is when the renewal was refused.
    result = summarize([group()], {"sub_1": {"status": "past_due"}}, "sub_1",
                       now=dt("2026-10-16"),
                       account={"payment_failed_at": failed})
    assert (result["renewal_state"], result["renewal_at"]) == (
        "past_due", "2026-09-26T09:27:00Z")
    # No snapshot: an account Paddle cancelled reads ended, dated when noticed.
    synced = datetime(2026, 9, 27, 9, 7)
    ended = summarize([group(sid=None)], {}, None, now=dt("2026-10-16"),
                      account={"billing_status": "canceled",
                               "billing_synced_at": synced})
    assert (ended["renewal_state"], ended["renewal_at"]) == (
        "ended", "2026-09-27T09:07:00Z")
    # A cancelled trial that never paid is not an ended subscription.
    trial = summarize([], {}, None, now=dt("2026-10-16"),
                      account={"billing_status": "canceled"})
    assert trial["renewal_state"] == "none"
    # Without a snapshot or a billing status nothing is claimed.
    assert summarize([group()], {}, "sub_1", now=dt("2026-10-16"),
                     account={"billing_status": "active"})["renewal_state"] == "unknown"


def test_filter_ids_are_integers_and_empty_matches_nothing():
    states = {5: "renews", 6: "cancelled", 7: "past_due", 8: "ended", 9: "cancelled"}
    assert ids_in(states, "cancelled_access") == "u.id = ANY(ARRAY[6,9]::int[])"
    assert ids_in(states, "renewing", "s") == "s.id = ANY(ARRAY[5]::int[])"
    assert ids_in({}, "ended") == "FALSE"


STATES = {5: "renews", 6: "cancelled", 7: "past_due", 8: "ended"}


@pytest.mark.parametrize("flt, needle", [
    ("renewing", "u.id = ANY(ARRAY[5]::int[])"),
    ("cancelled_access", "u.id = ANY(ARRAY[6]::int[])"),
    ("payment_failing", "u.id = ANY(ARRAY[7]::int[])"),
    ("ended", "u.id = ANY(ARRAY[8]::int[])"),
])
def test_users_filters_split_subscribed_now(monkeypatch, flt, needle):
    counts = {"all_": 10, "subscribed": 2, "paid": 4, "canceled": 1,
              "exported": 4, "free": 6, "renewing": 1, "cancelled_access": 1,
              "payment_failing": 1, "ended": 1}
    cur = Cur([("AS all_", counts), ("AS today", {"today": 1, "week": 5}),
               ("LIMIT %s OFFSET", [{"id": 6, "email": "c@example.com",
                                     "paid_cents": 1500,
                                     "subscription_id": "sub_6"},
                                    {"id": 11, "email": "free@example.com",
                                     "paid_cents": 0}])])
    monkeypatch.setattr(admin, "get_db", lambda readonly=None: Conn(cur))
    monkeypatch.setattr(admin.trial_state, "columns_ready", lambda conn: True)
    monkeypatch.setattr(subs, "account_states", lambda c: STATES)
    monkeypatch.setattr(subs, "for_customers", lambda c, rows: {
        r["id"]: {"renewal_state": STATES[r["id"]],
                  "renewal_at": "2026-11-08T00:00:00Z",
                  "subscriber_kind": "existing", "returned_at": None}
        for r in rows})
    r = app_with((admin.admin_bp, "/admin")).test_client().get(
        f"/admin/users?filter={flt}", headers=H())
    assert r.status_code == 200
    assert r.json["filter"] == flt and r.json["total"] == 1
    assert r.json["counts"]["cancelled_access"] == 1
    listing = next(s for s in cur.sql if "LIMIT %s OFFSET" in s)
    assert needle in listing
    count_sql = next(s for s in cur.sql if "AS all_" in s)
    assert "b.id = ANY(ARRAY[6]::int[])) AS cancelled_access" in count_sql
    assert "b.u_status IN ('paying', 'payment_failing')) AS subscribed" in count_sql
    first, second = r.json["users"]
    assert (first["renewal_state"], first["renewal_at"]) == (
        "cancelled", "2026-11-08T00:00:00Z")
    assert "subscription_id" not in first
    assert second["renewal_state"] == "none"


def test_overview_and_revenue_carry_the_paddle_split(monkeypatch):
    from admin_metrics import classic
    split_value = {"available": True, "renewing": 8, "paddle_active": 23}
    monkeypatch.setattr(subs, "paddle_split", lambda c: split_value)
    monkeypatch.setattr(admin, "_live_mrr", lambda cur, scope: 387.5)
    monkeypatch.setattr(admin, "_billing_ready", lambda cur: True)
    monkeypatch.setattr(admin, "_collected", lambda cur, since_days=None: {})
    monkeypatch.setattr(admin, "_plan_cohorts", lambda cur, scope: [])
    monkeypatch.setattr(classic, "overview_visits", lambda cur: {})
    monkeypatch.setattr(classic, "people_count", lambda cur, days: (0, None))
    cur = Cur([("AS prev_week", {"total": 10, "today": 1, "week": 2,
                                 "month": 3, "prev_week": 1}),
               ("AS total FROM users WHERE is_subscribed", {"total": 19}),
               ("AS total FROM users WHERE is_verified", {"total": 10}),
               ("SELECT COUNT(*) AS n FROM users u WHERE", {"n": 19})])
    app = app_with((admin.admin_bp, "/admin"))
    with app.app_context():
        overview = admin._overview(cur)
        revenue = admin._revenue(cur)
    assert overview["subscriptions"]["paddle"] == split_value
    # The old fields stay for clients that have not updated.
    assert overview["subscriptions"]["mrr"] == 387.5
    assert revenue["paddle"] == split_value and revenue["mrr"] == 387.5

    def boom(c):
        raise RuntimeError("snapshots unreadable")
    monkeypatch.setattr(subs, "paddle_split", boom)
    with app.app_context():
        assert admin._paddle_split(cur) == {"available": False}


def test_subscribers_filter_by_status_and_return_paddles_count(monkeypatch):
    cur = Cur([("AS ever_paid", {"ever_paid": 37, "entitled_now": 22,
                                 "active": 19, "canceled": 12, "past_due": 3,
                                 "attention": 3, "no_project": 5,
                                 "renewing": 8, "cancelled_access": 11,
                                 "payment_failing": 3, "ended": 12}),
               ("matched_subscribers", [])])

    @contextmanager
    def fake_adb():
        yield Conn(cur)
    monkeypatch.setattr(admin_video, "adb", fake_adb)
    monkeypatch.setattr(subs, "account_states", lambda c: STATES)
    monkeypatch.setattr(subs, "paddle_split",
                        lambda c: {"available": True, "paddle_active": 23})
    r = app_with((admin_video.admin_video_bp, None)).test_client().get(
        "/admin/video/subscribers?status=cancelled_access", headers=H())
    assert r.status_code == 200
    listing = next(s for s in cur.sql if "matched_subscribers" in s)
    assert "AND s.id = ANY(ARRAY[6]::int[])" in listing
    assert "s.subscriber_state = %s" not in listing
    assert r.json["status"] == "cancelled_access"
    assert r.json["state_counts"] == {"renewing": 8, "cancelled_access": 11,
                                      "payment_failing": 3, "ended": 12}
    assert r.json["paddle"] == {"available": True, "paddle_active": 23}
    summary_sql = next(s for s in cur.sql if "AS ever_paid" in s)
    assert "paid_users.id = ANY(ARRAY[7]::int[])) AS payment_failing" in summary_sql
