"""The explicit locked-card -> fresh child editor handoff."""

import contextlib
import os
import sys

from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                      # noqa: E402


class _Cursor:
    def __init__(self):
        self.one = None
        self.prompts = []
        self.updates = []

    def execute(self, sql, params=None):
        statement = " ".join(sql.split())
        params = params or ()
        if statement.startswith("SELECT id, state, progress, error"):
            self.one = None
        elif statement.startswith("SELECT id, type, state"):
            self.one = None
        elif statement.startswith("INSERT INTO chat_messages"):
            self.prompts.append({"session_id": params[0], "text": params[1],
                                 "meta": dict(params[2].adapted)})
            self.one = {"id": 812}
        elif statement.startswith("UPDATE projects SET meta"):
            self.updates.append(params)
            self.one = None
        else:                                                   # pragma: no cover
            raise AssertionError(statement)

    def fetchone(self):
        return self.one


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self):
        return self.cur


def test_edit_press_boots_one_fresh_child_agent(monkeypatch):
    cur = _Cursor()
    clip = {
        "child_project_id": 22, "title": "The launch mistake",
        "start": 120, "end": 177, "hook": "We ignored the warning",
        "story": {"setup": "The bet", "development": "The warning",
                  "payoff": "The failed launch changes the plan"},
    }
    parent = {"id": 7, "title": "Podcast", "meta": {
        "shorts": {"clips": [clip], "reference_asset_id": 91,
                   "style_profile": {"source": "reference"}}}}
    child = {"id": 22, "title": clip["title"], "parent_project_id": 7,
             "chat_session_id": 44}

    @contextlib.contextmanager
    def fake_vdb():
        yield _Conn(cur)

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda _c, pid, _uid: parent if pid == 7 else child)
    monkeypatch.setattr(video, "_active_original", lambda *_: {"sha256": "s"})
    monkeypatch.setattr(video, "_index_row", lambda *_: {"id": 3})
    monkeypatch.setattr(video, "_trial_gate_applies", lambda *_: False)
    monkeypatch.setattr(video.plan_gate, "needs_plan", lambda *_: False)
    monkeypatch.setattr(video, "check_and_reserve", lambda *_a, **_k: True)
    monkeypatch.setattr(video, "_running_jobs_count", lambda *_: 0)
    monkeypatch.setattr(video, "_latest_edl", lambda *_: {"version": 1})
    queued = {}

    def fake_enqueue(_cur, pid, uid, kind, payload):
        queued.update(project_id=pid, user_id=uid, kind=kind,
                      payload=payload)
        return 900

    monkeypatch.setattr(video, "_enqueue", fake_enqueue)
    monkeypatch.setattr(video, "_queue_depth_notice", lambda *_: None)
    monkeypatch.setattr(video, "record_client_event", lambda *_a, **_k: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    app = Flask(__name__)
    with app.test_request_context(json={}):
        response = video.start_short_editor.__wrapped__(
            user_id=5, project_id=7, child_project_id=22)

    assert response.status_code == 200
    assert response.get_json()["job_id"] == 900
    assert queued == {
        "project_id": 22, "user_id": 5, "kind": "agent_turn",
        "payload": {"message_id": 812, "shorts_boot": True,
                    "parent_project_id": 7},
    }
    assert len(cur.prompts) == 1
    prompt = cur.prompts[0]["text"]
    assert "fresh lead editor" in prompt
    assert "read_skill for short-form-direction" in prompt
    assert "Do not apply a formula" in prompt
    assert "render a complete preview" in prompt
    assert cur.prompts[0]["meta"]["shorts_boot"] is True
    assert len(cur.updates) == 1
