"""A sub-minute Shorts request becomes one direct edit instead of failing."""

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
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((" ".join(sql.split()), params or ()))


class _Conn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_sub_minute_start_shorts_routes_to_editor(monkeypatch):
    cur = _Cursor()

    @contextlib.contextmanager
    def fake_vdb():
        yield _Conn(cur)

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(
        video, "_project_for_user",
        lambda *_: {"id": 5, "kind": "shorts", "chat_session_id": 44})
    monkeypatch.setattr(
        video, "_active_original",
        lambda *_: {"sha256": "abc", "duration_s": 42.4})
    monkeypatch.setattr(video, "_index_row", lambda *_: {"id": 8})

    app = Flask(__name__)
    with app.test_request_context(json={}):
        response = video.start_shorts.__wrapped__(user_id=7, project_id=5)

    assert response.status_code == 200
    body = response.get_json()
    assert body == {"direct_edit": True, "kind": "edit",
                    "duration_s": 42.4}
    assert any(q.startswith("UPDATE projects SET kind = 'edit'")
               for q, _ in cur.queries)
    assert any(q.startswith("INSERT INTO chat_messages")
               for q, _ in cur.queries)
    assert not any("INSERT INTO video_jobs" in q for q, _ in cur.queries)
