"""Subscriber Projects shows paid work, not merely a subscription flag."""

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
        now = datetime(2026, 8, 23, tzinfo=timezone.utc)
        return [{
            "id": 7, "title": "Real edit", "kind": "video",
            "email": "paid@example.com", "plan": "ai",
            "billing_plan": "ai", "billing_status": "active",
            "created_at": now, "first_paid_at": now, "last_paid_at": now,
            "paid_cents": 1500, "messages": 3, "turns": 2,
            "versions": 2, "exports": 1, "failed_jobs": 0,
            "uploads": 1, "shorts_count": 4, "duration_s": 60,
            "storage_bytes": 1_000_000, "last_activity": now,
            "matched_projects": 1,
        }]

    def fetchone(self):
        return {"n": 1}


class _Conn:
    def __init__(self):
        self.cur = _Cursor()

    def cursor(self):
        return self.cur


def test_subscriber_projects_requires_real_payment_and_is_bounded(monkeypatch):
    conn = _Conn()

    @contextmanager
    def fake_adb():
        yield conn

    monkeypatch.setattr(admin_video, "adb", fake_adb)
    app = Flask(__name__)
    with app.test_request_context(
            "/admin/video/subscriber-projects?per_page=999"):
        response = admin_video.video_subscriber_projects.__wrapped__()
        body = response.get_json()

    first_sql, first_params = conn.cur.sql[0]
    assert "COALESCE(u.is_subscribed, 0) = 1" in first_sql
    assert "pay.status = 'completed'" in first_sql
    assert "pay.amount_cents > 0" in first_sql
    assert "LIMIT %s OFFSET %s" in first_sql
    assert first_params[-2:] == (100, 0)
    assert body["subscriber_count"] == 1
    assert body["projects"][0]["title"] == "Real edit"
    assert body["projects"][0]["paid_usd"] == 15.0
