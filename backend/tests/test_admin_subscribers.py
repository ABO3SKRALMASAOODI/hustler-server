"""Paid subscriber visibility keeps active and canceled customers together."""

import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import admin_video  # noqa: E402


class _Cursor:
    def __init__(self):
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def fetchall(self):
        now = datetime(2026, 8, 26, tzinfo=timezone.utc)
        return [{
            "id": 7, "email": "former@example.com",
            "subscriber_state": "canceled", "is_subscribed": 0,
            "plan": "free", "billing_plan": "ai",
            "billing_status": "canceled", "billing_period": "monthly",
            "created_at": now, "first_paid_at": now, "last_paid_at": now,
            "paid_cents": 1500, "payment_count": 1,
            "subscription_expiry": None, "billing_synced_at": now,
            "payment_failed_at": None, "payment_failed_reason": None,
            "projects": 1, "child_projects": 0, "latest_project_id": 99,
            "uploads": 1, "clips": 3, "renders": 2,
            "storage_bytes": 1234, "jobs": 5, "failed_jobs": 1,
            "exports": 1, "feedback_up": 4, "feedback_down": 1,
            "last_activity": now,
            "matched_subscribers": 1,
        }]

    def fetchone(self):
        return {"ever_paid": 10, "entitled_now": 9, "active": 6,
                "canceled": 1, "past_due": 0, "attention": 3,
                "no_project": 3}


class _Conn:
    def __init__(self):
        self.cur = _Cursor()

    def cursor(self):
        return self.cur


def test_subscribers_keeps_churn_and_distinguishes_access_from_health(
        monkeypatch):
    conn = _Conn()

    @contextmanager
    def fake_adb():
        yield conn

    monkeypatch.setattr(admin_video, "adb", fake_adb)
    app = Flask(__name__)
    with app.test_request_context(
            "/admin/video/subscribers?status=canceled&per_page=999"):
        response = admin_video.video_subscribers.__wrapped__()
        body = response.get_json()

    row_sql, row_params = conn.cur.sql[0]
    summary_sql, _ = conn.cur.sql[1]
    assert "pay.status = 'completed'" in row_sql
    assert "pay.amount_cents > 0" in row_sql
    assert "s.subscriber_state = %s" in row_sql
    assert row_params[-3:] == ["canceled", 100, 0]
    assert "billing_status = 'canceled'" in summary_sql
    assert "m.meta->>'feedback' = 'up'" in row_sql
    assert "m.meta->>'feedback' = 'down'" in row_sql
    assert body["summary"] == {
        "ever_paid": 10, "entitled_now": 9, "active": 6,
        "canceled": 1, "past_due": 0, "attention": 3,
        "no_project": 3,
    }
    assert body["subscribers"][0]["state"] == "canceled"
    assert body["subscribers"][0]["canceled_recorded_at"] is not None
    assert body["subscribers"][0]["feedback_up"] == 4
    assert body["subscribers"][0]["feedback_down"] == 1
