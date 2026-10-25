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
        elif command.startswith("UPDATE chat_messages SET meta"):
            patch, row_id = params[0].adapted, params[1]
            for row in self.rows:
                if row["id"] == row_id:
                    row["meta"] = dict(row.get("meta") or {}, **patch)
            self._result = None
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


def test_the_blocked_request_is_kept_on_the_plans_reply():
    # P1 (25 Oct 2026): 210 blocked first prompts in 14 days were discarded.
    chat = Chat()
    first = video._record_plans_message(
        chat, 3, "chat",
        blocked_prompt=video._blocked_prompt("Make it 9:16", [5], "c1"))
    assert first["meta"]["blocked_prompt"]["text"] == "Make it 9:16"
    assert first["meta"]["blocked_prompt"]["attachments"] == [5]
    # A resend replaces it on the same reply instead of stacking cards.
    again = video._record_plans_message(
        chat, 3, "chat",
        blocked_prompt=video._blocked_prompt("Make it 9:16, 20 s", [], "c2"))
    assert chat.inserts == 1 and again["id"] == first["id"]
    assert again["meta"]["blocked_prompt"]["text"] == "Make it 9:16, 20 s"
    assert chat.rows[-1]["meta"]["blocked_prompt"]["client_msg_id"] == "c2"


def test_send_gate_persists_the_blocked_text():
    source = inspect.getsource(video.post_message)
    gate = source[source.index("THE SUBSCRIBE GATE"):]
    gate = gate[:gate.index("), 402")]
    assert "blocked_prompt=_blocked_prompt(text" in gate
    assert '"prompt": text[:300]' in gate


class Replay:
    """chat_messages + video_jobs for /replay-blocked."""

    def __init__(self, last, agent_live=False, indexed=True):
        self.last, self.agent_live, self.indexed = last, agent_live, indexed
        self.updates = []
        self._result = None

    def execute(self, sql, params=None):
        command = " ".join(sql.split())
        if command.startswith("SELECT pg_advisory_xact_lock"):
            self._result = None
        elif command.startswith("SELECT id, role, meta, created_at >"):
            self._result = self.last
        elif "type = 'agent_turn'" in command:
            self._result = {"?": 1} if self.agent_live else None
        elif "type = 'index'" in command:
            self._result = None
        elif command.startswith("UPDATE chat_messages"):
            self.updates.append(params[0].adapted)
        else:
            raise AssertionError(command)

    def fetchone(self):
        return self._result


def _replay(monkeypatch, cursor, gated=False, mcp_only=False):
    import contextlib
    from flask import Flask
    enqueued, events = [], []

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cursor,
                             "commit": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 9, "chat_session_id": 3})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: gated)
    monkeypatch.setattr(video, "_studio_ai_gate",
                        lambda *_: ("403",) if mcp_only else None)
    monkeypatch.setattr(video, "_running_jobs_count", lambda *_: 0)
    monkeypatch.setattr(video, "check_and_reserve", lambda *a, **k: True)
    monkeypatch.setattr(video, "_active_original",
                        lambda *_: {"id": 41, "sha256": "s"})
    monkeypatch.setattr(video, "_index_row",
                        lambda *_: {"json": {}} if cursor.indexed else None)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append(a[2]))
    monkeypatch.setattr(
        video, "_enqueue",
        lambda c, p, u, t, payload: enqueued.append((t, payload)) or 777)
    app = Flask(__name__)
    with app.test_request_context(json={}):
        response = video.replay_blocked.__wrapped__(user_id=8, project_id=9)
    return response.get_json(), enqueued, events


def _plans(meta):
    return {"id": 55, "role": "assistant", "recent": True,
            "meta": {"kind": "subscription_plans", **meta}}


def test_replay_returns_the_blocked_chat_request_for_the_send_path(monkeypatch):
    body, enqueued, events = _replay(monkeypatch, Replay(_plans({
        "surface": "chat",
        "blocked_prompt": {"text": "Cut the pauses", "attachments": [3]}})))
    assert body == {"replay": "send", "text": "Cut the pauses",
                    "attachments": [3], "client_msg_id": "replay-55"}
    assert enqueued == [] and events == ["blocked_prompt_replayed"]


def test_replay_runs_a_blank_canvas_request_once(monkeypatch):
    cursor = Replay(_plans({"surface": "blank_canvas",
                            "blocked_prompt": {"message_id": 900,
                                               "text": "Record my site"}}))
    body, enqueued, _ = _replay(monkeypatch, cursor)
    assert body["replay"] == "enqueued"
    assert enqueued == [("agent_turn", {"message_id": 900,
                                        "replayed_blocked": True})]
    assert cursor.updates and "replayed_at" in cursor.updates[0]
    cursor.last["meta"]["replayed_at"] = "now"
    body, enqueued, _ = _replay(monkeypatch, cursor)
    assert body["replay"] is None and enqueued == []


def test_replay_never_runs_for_a_gated_or_moved_on_conversation(monkeypatch):
    plans = _plans({"surface": "chat", "blocked_prompt": {"text": "x"}})
    body, _, _ = _replay(monkeypatch, Replay(plans), gated=True)
    assert body == {"replay": None, "reason": "subscription_required"}
    body, _, _ = _replay(monkeypatch, Replay(plans), mcp_only=True)
    assert body == {"replay": None, "reason": "mcp_only"}
    moved = {"id": 60, "role": "user", "recent": True, "meta": {}}
    body, enqueued, _ = _replay(monkeypatch, Replay(moved))
    assert body == {"replay": None, "reason": "nothing_blocked"}
    stale = dict(plans, recent=False)
    body, _, _ = _replay(monkeypatch, Replay(stale))
    assert body["replay"] is None


def test_replay_starts_analysis_of_a_walled_upload(monkeypatch):
    walled = {"id": 50, "role": "assistant", "recent": True,
              "meta": {"kind": "subscription_required"}}
    body, enqueued, _ = _replay(monkeypatch, Replay(walled, indexed=False))
    assert body == {"replay": "indexing", "job_id": 777}
    assert enqueued == [("index", {"asset_id": 41,
                                   "subscription_unlocked": True})]
