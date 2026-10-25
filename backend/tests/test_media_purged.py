"""A project whose upload the R2 retention sweep removed (meta.r2_purged_at).

The sweep (r2_retention.py, owner decision D4) deletes a never-paid
account's files 7 days after its last activity and keeps the rows. Coming
back to such a project used to mean a player pointed at a deleted object and
self-heals queueing index and render jobs that could only fail. Now the
project payload says so in one field, nothing is queued against the missing
file, and the subscribe wall in front of it is exactly what it was.
"""
import contextlib
import os
import sys

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask  # noqa: E402

from routes import video  # noqa: E402
from video_services import project_state  # noqa: E402

PURGED_AT = "2026-10-25T11:57:00.585529+00:00"
MESSAGE = ("Your uploaded video was removed after 7 days without activity. "
           "Upload it again to keep editing.")


def original(purged=True, asset_id=41):
    meta = {"filename": "talk.mp4"}
    if purged:
        meta.update(r2_purged_at=PURGED_AT, r2_purge_policy="d4-2026-10-25",
                    r2_purge_category="never_paid_source")
    return {"id": asset_id, "kind": "original", "sha256": "s" * 64,
            "storage_key": f"originals/9/{asset_id}.mp4", "bytes": 10,
            "duration_s": 12.0, "width": 1080, "height": 1920, "fps": 30,
            "meta": meta, "created_at": None}


class Cursor:
    """Answers the statements a route issues by their opening words."""

    def __init__(self, answers):
        self.answers = answers
        self.sql = []
        self._one = None
        self._all = []

    def execute(self, sql, params=None):
        command = " ".join(sql.split())
        self.sql.append(command)
        for prefix, answer in self.answers:
            if prefix in command:
                answer = answer(params) if callable(answer) else answer
                if isinstance(answer, list):
                    self._all, self._one = answer, (answer[0] if answer
                                                    else None)
                else:
                    self._one, self._all = answer, ([answer] if answer
                                                    else [])
                return
        self._one, self._all = None, []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def _fake_db(monkeypatch, cursor):
    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cursor,
                             "commit": lambda self: None,
                             "rollback": lambda self: None})()
    monkeypatch.setattr(video, "vdb", fake_vdb)


# ── the notice itself ──────────────────────────────────────────────────────

def test_the_notice_is_the_owners_sentence_and_names_the_file():
    assert project_state.MEDIA_PURGED_MESSAGE == MESSAGE
    notice = project_state.media_purged(original())
    assert notice == {"purged_at": PURGED_AT, "asset_id": 41,
                      "filename": "talk.mp4", "message": MESSAGE}


def test_a_live_or_missing_original_has_no_notice():
    assert project_state.media_purged(original(purged=False)) is None
    assert project_state.media_purged(None) is None
    assert project_state.media_purged({"id": 1, "meta": None}) is None
    assert not project_state.is_purged({"id": 1, "meta": "not a dict"})


def test_a_swept_render_gets_its_own_sentence():
    render = {"id": 5, "kind": "render", "meta": {"r2_purged_at": PURGED_AT}}
    assert project_state.purged_message(render) == \
        project_state.PURGED_RENDER_MESSAGE
    assert project_state.purged_message(original()) == MESSAGE


# ── /state: the payload says it, and nothing is queued against it ─────────

def _state(monkeypatch, src, gated=False, stale_index=True):
    enqueued = []
    cursor = Cursor([
        ("FROM chat_messages WHERE session_id = %s AND role = 'activity'",
         None),
        ("SELECT DISTINCT ON (type) id, type, state", []),
        ("SELECT id FROM projects WHERE id = %s FOR UPDATE", {"id": 9}),
        ("SELECT id, role, content, meta, created_at FROM chat_messages",
         [{"id": 3, "role": "user", "content": "cut the pauses",
           "meta": {}, "created_at": video.datetime(2026, 10, 10)}]),
        ("SELECT version, created_by, created_at FROM edls", []),
        ("SELECT id, kind, storage_key, duration_s, sha256, meta", []),
    ])
    _fake_db(monkeypatch, cursor)
    monkeypatch.setattr(video, "_project_for_user", lambda *_: {
        "id": 9, "title": "talk", "kind": "edit", "parent_project_id": None,
        "chat_session_id": 3, "meta": {}})
    monkeypatch.setattr(video, "_active_original", lambda *_: src)
    monkeypatch.setattr(video, "_index_row", lambda *_: {
        "id": 1, "created_at": None,
        "pipeline_version": (video.PIPELINE_VERSION - 1 if stale_index
                             else video.PIPELINE_VERSION)})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: gated)
    monkeypatch.setattr(video, "_latest_edl", lambda *_: {
        "version": 4, "created_by": "user", "json": {"keep": []},
        "created_at": None})
    monkeypatch.setattr(video, "_should_heal_preview", lambda *a, **k: True)
    monkeypatch.setattr(video, "_index_job_state", lambda *_: {
        "id": 70, "state": "done", "progress": 100, "error": None,
        "updated_at": None, "active": False, "recent_count": 0})
    monkeypatch.setattr(video, "_final_gate", lambda *_: (lambda *a: True))
    monkeypatch.setattr(video, "_enqueue", lambda c, p, u, t, payload:
                        enqueued.append((t, payload)) or 777)
    app = Flask(__name__)
    with app.test_request_context("/projects/9/state"):
        body = video.project_state.__wrapped__(user_id=8, project_id=9)
    return body.get_json(), enqueued, cursor


def test_state_names_the_removal_and_queues_nothing(monkeypatch):
    body, enqueued, cursor = _state(monkeypatch, original())
    assert body["media_purged"] == {"purged_at": PURGED_AT, "asset_id": 41,
                                    "filename": "talk.mp4",
                                    "message": MESSAGE}
    # A stale pipeline would re-index and the newest edit has no render:
    # both heals would run against a file that is gone.
    assert enqueued == []
    # The chat and the history stay.
    assert [m["content"] for m in body["messages"]] == ["cut the pauses"]
    assert body["video"]["id"] == 41 and body["indexed"] is True
    # Swept renders, proxies and clips never reach the player or pickers.
    extra = next(s for s in cursor.sql
                 if s.startswith("SELECT id, kind, storage_key, duration_s"))
    assert "? 'r2_purged_at'" in extra and "NOT (COALESCE(meta" in extra


def test_a_live_upload_still_heals_as_before(monkeypatch):
    body, enqueued, _ = _state(monkeypatch, original(purged=False))
    assert body["media_purged"] is None
    assert [t for t, _ in enqueued] == ["preview", "index"]


def test_the_wall_still_answers_a_never_paid_account(monkeypatch):
    body, enqueued, _ = _state(monkeypatch, original(), gated=True)
    assert body["subscribe_gated"] is True
    assert body["media_purged"]["message"] == MESSAGE
    assert enqueued == []


# ── sending a request ──────────────────────────────────────────────────────

def _send(monkeypatch, src, gated):
    enqueued = []
    cursor = Cursor([
        ("SELECT COUNT(*) AS n FROM chat_messages", {"n": 0}),
    ])
    _fake_db(monkeypatch, cursor)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 9, "chat_session_id": 3})
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video, "_active_original", lambda *_: src)
    monkeypatch.setattr(video, "_index_row", lambda *_: {"id": 1})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: gated)
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)
    monkeypatch.setattr(video, "_record_plans_message",
                        lambda *a, **k: {"id": 55})
    monkeypatch.setattr(video, "_enqueue", lambda c, p, u, t, payload:
                        enqueued.append((t, payload)) or 777)
    app = Flask(__name__)
    with app.test_request_context(json={"text": "cut the pauses"}):
        out = video.post_message.__wrapped__(user_id=8, project_id=9)
    response, status = out if isinstance(out, tuple) else (out, 200)
    return response.get_json(), status, enqueued


def test_a_subscriber_on_a_swept_project_is_told_before_any_turn(monkeypatch):
    body, status, enqueued = _send(monkeypatch, original(), gated=False)
    assert status == 409
    assert body["code"] == "media_purged" and body["error"] == MESSAGE
    assert enqueued == []


def test_the_subscribe_wall_is_unchanged_on_a_swept_project(monkeypatch):
    body, status, enqueued = _send(monkeypatch, original(), gated=True)
    assert status == 402
    assert body.get("code") != "media_purged"
    assert body["subscription_message"] == {"id": 55}
    assert enqueued == []


# ── after checkout ────────────────────────────────────────────────────────

def test_checkout_never_starts_analysis_of_a_swept_upload(monkeypatch):
    cursor = Cursor([
        ("SELECT id, role, meta, created_at >",
         {"id": 50, "role": "assistant", "recent": True,
          "meta": {"kind": "subscription_required"}}),
    ])
    _fake_db(monkeypatch, cursor)
    enqueued = []
    monkeypatch.setattr(video, "_project_for_user",
                        lambda *_: {"id": 9, "chat_session_id": 3})
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda *_: False)
    monkeypatch.setattr(video, "_studio_ai_gate", lambda *_: None)
    monkeypatch.setattr(video, "_active_original", lambda *_: original())
    monkeypatch.setattr(video, "_index_row", lambda *_: None)
    monkeypatch.setattr(video, "_running_jobs_count", lambda *_: 0)
    monkeypatch.setattr(video, "_enqueue", lambda c, p, u, t, payload:
                        enqueued.append((t, payload)) or 777)
    app = Flask(__name__)
    with app.test_request_context(json={}):
        body = video.replay_blocked.__wrapped__(user_id=8, project_id=9)
    assert body.get_json() == {"replay": None, "reason": "media_purged"}
    assert enqueued == []


# ── links to the files ────────────────────────────────────────────────────

def test_a_swept_file_gets_a_sentence_not_a_dead_link(monkeypatch):
    cursor = Cursor([("SELECT a.* FROM assets a", dict(original(),
                                                        project_id=9))])
    _fake_db(monkeypatch, cursor)
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    signed = []
    monkeypatch.setattr(video.storage, "presign_get",
                        lambda key, **k: signed.append(key) or "https://x")
    app = Flask(__name__)
    with app.test_request_context("/assets/41/url"):
        response, status = video.asset_url.__wrapped__(user_id=8,
                                                        asset_id=41)
    assert status == 410
    assert response.get_json() == {"error": MESSAGE, "code": "media_purged"}
    assert signed == []


def test_playback_leaves_a_swept_source_out(monkeypatch):
    edl = video.wschemas.default_edl(12.0)
    src = original()
    cursor = Cursor([("SELECT id,kind,storage_key,duration_s", [src])])
    _fake_db(monkeypatch, cursor)
    monkeypatch.setattr(video, "_project_for_user", lambda *_: {"id": 9})
    monkeypatch.setattr(video, "_latest_edl",
                        lambda *_: {"version": 2, "json": edl})
    monkeypatch.setattr(video, "_active_original", lambda *_: src)
    monkeypatch.setattr(video.storage, "presign_get",
                        lambda key, **k: f"https://r2/{key}")
    app = Flask(__name__)
    with app.test_request_context("/projects/9/playback"):
        body = video.playback_endpoint.__wrapped__(user_id=8, project_id=9)
    assert body.get_json()["sources"] == {}
    live = original(purged=False)
    cursor.answers = [("SELECT id,kind,storage_key,duration_s", [live])]
    monkeypatch.setattr(video, "_active_original", lambda *_: live)
    with app.test_request_context("/projects/9/playback"):
        body = video.playback_endpoint.__wrapped__(user_id=8, project_id=9)
    assert set(body.get_json()["sources"]) == {"source"}


# ── direct timeline edits ─────────────────────────────────────────────────

def test_edits_on_a_swept_project_are_refused_after_the_wall():
    import inspect
    batch = inspect.getsource(video.apply_edit_batch_core)
    wall = batch.index("A subscription is required to edit this project.")
    purged = batch.index("_media_purged(_active_original(cur, project_id))")
    assert wall < purged, "the subscribe wall answers first, unchanged"
    write = inspect.getsource(video.user_edl_write)
    assert "_media_purged(original)" in write
    assert '"code": "media_purged"' in write
