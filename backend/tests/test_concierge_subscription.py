"""Website/blank-canvas requests reach the same plans as uploaded footage."""
import os
import sys

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from routes import video  # noqa: E402


@pytest.fixture
def respond(monkeypatch):
    def run(balance, *, act=True):
        replies, jobs, events = [], [], []

        class Cursor:
            def execute(self, sql, params=None):
                if "INSERT INTO chat_messages" in sql:
                    replies.append((params[1], params[2].adapted))

            def fetchone(self):
                return None

        class Connection:
            committed = False

            def cursor(self):
                return Cursor()

            def commit(self):
                self.committed = True

            def close(self):
                pass

        conn = Connection()
        monkeypatch.setattr(video.psycopg2, "connect", lambda *a, **k: conn)
        monkeypatch.setattr(video, "_concierge_reply", lambda *a, **k:
                            ("I can help with that.", {"kind": "concierge"}, None, act))
        monkeypatch.setattr(video, "get_balance", lambda *a: balance)
        monkeypatch.setattr(video, "_running_jobs_count", lambda *a: 0)
        monkeypatch.setattr(video, "_enqueue", lambda *a: jobs.append(a))

        def record(*args, **kwargs):
            assert conn.committed, "analytics must not wait on its own chat transaction"
            events.append((args, kwargs))

        monkeypatch.setattr(video, "record_client_event", record)
        video._concierge_respond("unused", 7, {
            "stage": "no_video", "history": [], "can_act": True,
            "session_id": 10, "user_id": 12, "message_id": 20,
        }, [])
        assert conn.committed
        return replies, jobs, events
    return run


@pytest.mark.parametrize("balance", [0, 50])
def test_new_account_gets_offer_before_any_canvas_work(respond, balance):
    replies, jobs, events = respond({"balance": balance, "subscribe_gated": True})
    assert len(replies) == 1
    text, meta = replies[0]
    assert "credits" not in text
    assert meta["subscribe_required"] is True
    assert meta["request_message_id"] == 20
    assert meta["subscription_offer"]["code"] == "subscribe_required"
    assert {p["id"] for p in meta["subscription_offer"]["plans"]} == {"mcp_connect", "advanced"}
    assert jobs == []
    assert events[0][0] == (12, 7, "trial_gate_shown")
    assert events[0][1]["detail"]["surface"] == "blank_canvas"


def test_paid_account_still_starts_its_edit(respond):
    replies, jobs, events = respond({"balance": 25, "subscribe_gated": False})
    assert len(jobs) == 1
    assert jobs[0][3] == "agent_turn"
    assert not replies[0][1].get("subscribe_required")
    assert events == []


@pytest.mark.parametrize("state", [
    {"balance": 0, "subscribe_gated": False},
    {"balance": 0, "subscribe_gated": False, "trial_cap_reached": True},
    {"balance": 0, "subscribe_gated": True, "payment_failed": True,
     "payment_failed_message": "Update your payment method."},
])
def test_existing_credit_and_payment_walls_remain_distinct(respond, state):
    replies, jobs, events = respond(state)
    assert len(replies) == 1
    assert replies[0][1]["credits_exhausted"] is True
    assert not replies[0][1].get("subscribe_required")
    assert replies[0][1]["payment_failed"] is bool(state.get("payment_failed"))
    assert replies[0][1]["trial_cap_reached"] is bool(state.get("trial_cap_reached"))
    assert jobs == events == []


def test_a_pre_upload_question_does_not_open_plans(respond):
    replies, jobs, events = respond({"balance": 0, "subscribe_gated": True}, act=False)
    assert replies == [("I can help with that.", {"kind": "concierge"})]
    assert jobs == events == []
