"""An account's own MCP editors must not starve its uploads and shorts plans.

22-23 Oct 2026: the owner's parallel editors held all three
MAX_CONCURRENT_JOBS_PER_USER slots with mcp_tool jobs (render_preview,
look_at, keep_segments, ...), so upload_finish(kind='original') was refused
"Too many jobs running" ~25 times in 40 minutes on CHM 2013 (project 4348,
jobs 88357-88669 on projects 3792-3876) and 5 times on Diamandis (4412, jobs
93420-93446), and the refusal advised wait_for_job on another project's job,
which returns that job's full image payload.

    cd backend && python -m pytest tests/test_job_cap_starvation.py -q
"""
import contextlib
import inspect
import os
import sys

from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from routes import video                                # noqa: E402
from video_services import jobs                         # noqa: E402


class _Jobs:
    """A video_jobs table: answers the COUNT and the busy-list queries by
    the type filter the SQL passes, so a test sees which types were
    counted."""

    def __init__(self, rows):
        self.rows, self.sql, self._out = rows, [], []

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self.sql.append((q, params))
        live = [r for r in self.rows if r["state"] in ("queued", "running")]
        if "WHERE project_id = %s AND type = 'shorts_plan'" in q:
            live = [r for r in live if r["project_id"] == params[0]
                    and r["type"] == "shorts_plan"]
        if "type = ANY(%s)" in q:
            types = set(params[1])
            live = [r for r in live if r["type"] in types]
        if q.startswith("SELECT COUNT(*)"):
            self._out = [{"n": len(live)}]
        else:
            self._out = [dict(r) for r in live]

    def fetchone(self):
        return self._out[0] if self._out else None

    def fetchall(self):
        return self._out


def _editors_busy():
    return [
        {"id": 88357, "type": "mcp_tool", "state": "running",
         "project_id": 3876, "tool": "render_preview"},
        {"id": 88358, "type": "mcp_tool", "state": "running",
         "project_id": 3797, "tool": "look_at"},
        {"id": 88359, "type": "mcp_tool", "state": "queued",
         "project_id": 3836, "tool": "keep_segments"},
        {"id": 88360, "type": "mcp_tool", "state": "running",
         "project_id": 3787, "tool": "critique_short"},
    ]


def test_the_cap_still_counts_tool_calls_where_it_did_but_not_for_plans():
    cur = _Jobs(_editors_busy() + [
        {"id": 1, "type": "agent_turn", "state": "running", "project_id": 9},
        {"id": 2, "type": "preview", "state": "running", "project_id": 9},
        {"id": 3, "type": "shorts_plan", "state": "done", "project_id": 9}])
    assert jobs.running_orchestration_jobs(cur, 60) == 5
    assert jobs.running_started_jobs(cur, 60) == 1
    assert "mcp_tool" not in jobs.STARTED_JOB_TYPES


def test_upload_finish_never_consults_the_job_cap():
    """Finishing an upload registers bytes already in storage and queues
    the index job: the index lane is the capacity control."""
    source = inspect.getsource(video.complete_upload_core)
    assert "_running_jobs_count" not in source
    assert "_started_jobs_count" not in source
    assert "Too many jobs running" not in source
    assert "wait_for_job" not in source
    assert '_enqueue(cur, project_id, user_id, "index"' in source


class _UploadCur:
    """Just enough of the tables for an ordinary original upload."""

    def __init__(self):
        self.enqueued, self._one, self._next = [], None, 500

    def execute(self, sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT id FROM projects WHERE id"):
            self._one = {"id": params[0]}
        elif s.startswith("SELECT id, chat_session_id FROM projects"):
            self._one = {"id": params[0], "chat_session_id": 55}
        elif s.startswith("SELECT id, kind FROM assets"):
            self._one = None
        elif s.startswith("INSERT INTO assets"):
            self._next += 1
            self._one = {"id": self._next}
        elif s.startswith("INSERT INTO video_jobs"):
            self._next += 1
            self.enqueued.append(params[2])
            self._one = {"id": self._next}
        elif "FROM video_jobs" in s:
            raise AssertionError("upload_finish counted jobs: " + s[:120])
        else:
            self._one = None

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []


def test_an_original_upload_is_indexed_while_the_editors_hold_every_slot(
        monkeypatch):
    cur = _UploadCur()

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda c, p, u: {"id": p, "chat_session_id": 55})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: False)
    monkeypatch.setattr(video, "_running_jobs_count", lambda *a, **k: 99)
    monkeypatch.setattr(video, "_started_jobs_count", lambda *a, **k: 99)
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(video.storage, "head_bytes", lambda k: 2_000_000_000)
    monkeypatch.setattr(video.storage, "max_upload_bytes", lambda: 10 ** 11)
    monkeypatch.setattr(video.storage, "get_range",
                        lambda k, n=64: b"\x00" * 4 + b"ftypisom")
    monkeypatch.setattr(video.storage, "content_matches_kind",
                        lambda h, k: True)
    monkeypatch.setattr(video.storage, "complete_multipart", lambda *a: None)
    monkeypatch.setattr(video.storage, "PROXY_FIRST_UPLOADS", False)

    out, status = video.complete_upload_core(60, 4348, {
        "storage_key": "originals/4348/chm.mp4", "kind": "original",
        "filename": "chm2013.mp4", "upload_id": "u1",
        "parts": [{"part_number": 1, "etag": "e1"}]})
    assert status == 200, out
    assert cur.enqueued == ["index"]


def _start_shorts(monkeypatch, rows):
    cur = _Jobs(rows)
    enqueued = []

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 4412, "kind": "shorts",
                                    "chat_session_id": 44})
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video, "_active_original",
                        lambda *_: {"sha256": "abc", "duration_s": 7200.0})
    monkeypatch.setattr(video, "_index_row",
                        lambda *_: {"json": {"video": {"duration": 7200.0}}})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(video.plan_gate, "needs_plan", lambda *_: False)
    monkeypatch.setattr(video, "check_and_reserve", lambda *a, **k: True)
    monkeypatch.setattr(
        video, "_enqueue",
        lambda c, p, u, t, payload: enqueued.append(t) or 93500)
    app = Flask(__name__)
    with app.test_request_context(json={}):
        response = video.start_shorts.__wrapped__(user_id=60,
                                                  project_id=4412)
    if isinstance(response, tuple):
        response, status = response
    else:
        status = response.status_code
    return status, response.get_json(), enqueued


def test_a_shorts_plan_starts_while_the_editors_hold_every_slot(monkeypatch):
    status, body, enqueued = _start_shorts(monkeypatch, _editors_busy())
    assert status == 200, body
    assert body == {"job_id": 93500}
    assert enqueued == ["shorts_plan"]


def test_a_shorts_plan_still_waits_for_real_chat_turns_and_plans(monkeypatch):
    rows = _editors_busy() + [
        {"id": 93420 + i, "type": t, "state": "running", "project_id": 4400 + i}
        for i, t in enumerate(("agent_turn", "agent_turn", "shorts_plan"))]
    status, body, enqueued = _start_shorts(monkeypatch, rows)
    assert status == 429
    assert body["code"] == "busy_capacity"
    assert enqueued == []
    # It names the chat turns / plans holding the slots, never the editors'
    # tool calls, and never advises wait_for_job on another project's job.
    assert "job 93420 agent_turn on project 4400, running" in body["error"]
    assert "job 93422 shorts_plan on project 4402" in body["error"]
    assert "render_preview" not in body["error"]
    assert "wait_for_job" not in body["error"]
