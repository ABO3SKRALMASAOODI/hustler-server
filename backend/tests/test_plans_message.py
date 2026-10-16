"""An edit request without a plan is answered by one agent message with plans.

The Studio used to open full-page plan cards on its own. The answer is now a
persisted assistant reply carrying the server's offer; repeated blocked sends
reuse it instead of stacking cards, and a refresh reads the same row back.
"""
import datetime
import inspect
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video  # noqa: E402


class Chat:
    """A one-session chat_messages table."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self._result = None
        self.inserts = 0

    def execute(self, sql, params=None):
        command = " ".join(sql.split())
        if command.startswith("SELECT id, role, content, meta, created_at FROM chat_messages"):
            rows = [r for r in self.rows if r["role"] in ("user", "assistant")]
            self._result = rows[-1] if rows else None
        elif command.startswith("INSERT INTO chat_messages"):
            self.inserts += 1
            row = {"id": 100 + len(self.rows), "role": "assistant",
                   "content": params[1], "meta": params[2].adapted,
                   "created_at": datetime.datetime(2026, 10, 16, 12, 0)}
            self.rows.append(row)
            self._result = row
        else:
            raise AssertionError(command)

    def fetchone(self):
        return self._result


def test_reply_opens_with_the_free_plan_line_and_carries_server_plans():
    chat = Chat([{"id": 7, "role": "assistant", "content": "I received your video.",
                  "meta": {"kind": "subscription_required"}}])
    message = video._record_plans_message(chat, 3, "chat")
    assert message["content"] == "You’re on the free plan. Upgrade to start editing."
    assert message["role"] == "assistant"
    assert message["created_at"] == "2026-10-16T12:00:00"
    meta = message["meta"]
    assert meta["kind"] == "subscription_plans"
    assert meta["subscribe_required"] is True
    plans = {p["id"]: p for p in meta["subscription_offer"]["plans"]}
    assert list(plans) == ["plus_2026", "pro_2026", "max_2026"]
    assert plans["plus_2026"]["monthly"] == 15
    assert plans["plus_2026"]["credits"] == 500


def test_repeated_blocked_sends_reuse_the_same_reply():
    chat = Chat()
    first = video._record_plans_message(chat, 3, "chat")
    second = video._record_plans_message(chat, 3, "chat")
    assert chat.inserts == 1
    assert second["id"] == first["id"]


def test_a_new_request_after_the_reply_gets_its_own_answer():
    chat = Chat()
    first = video._record_plans_message(chat, 3, "chat")
    chat.rows.append({"id": 900, "role": "user", "content": "Make it shorter",
                      "meta": {}})
    second = video._record_plans_message(chat, 3, "blank_canvas",
                                         request_message_id=900)
    assert chat.inserts == 2
    assert second["id"] != first["id"]
    assert second["meta"]["request_message_id"] == 900


def test_send_gate_returns_the_persisted_reply_with_the_offer():
    source = inspect.getsource(video.post_message)
    gate = source[source.index("THE SUBSCRIBE GATE"):]
    gate = gate[:gate.index("), 402")]
    assert "_record_plans_message(" in gate
    assert 'body["subscription_message"]' in gate
