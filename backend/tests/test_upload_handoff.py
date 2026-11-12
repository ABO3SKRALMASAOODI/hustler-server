"""An upload that carries on after its page is gone (#883, #401, 12 Nov 2026).

Background Fetch cannot send an Authorization header or anything that needs
a CORS preflight. A multipart transfer gets a handoff token that lets a plain
POST write parts of that one upload and finish it. Pinned here: the token is
scoped and is no login, relay-part takes it, handoff-complete finishes only
when storage holds every part, through the page's own completion code.

    cd backend && python -m pytest tests/test_upload_handoff.py -q
"""
import io
import json
import os
import sys

import jwt
import pytest
from flask import Flask

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage                                          # noqa: E402
from routes import video                                # noqa: E402
from routes.auth import token_required                  # noqa: E402
from video_services import upload_handoff               # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_upload_resume import _S3, _Cur, s3            # noqa: E402,F401

MIB = 1024 * 1024
SECRET = "k" * 40
KEY = "originals/7/a.mp4"


def _app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = SECRET
    return app


def _token(nbytes, ps, key=KEY, upload_id="u1", project=7, kind="original"):
    with _app().app_context():
        return upload_handoff.mint(60, project, key, upload_id, kind, nbytes, ps)


@pytest.fixture
def routes(monkeypatch):
    import contextlib
    events, completes, readies = [], [], []

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: _Cur()})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user",
                        lambda c, p, u: {"id": p} if u == 60 else None)
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append(a[2]))
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)

    def fake_complete(user_id, project_id, data, studio=False):
        completes.append((user_id, project_id, data))
        return {"asset_id": 1}, 200
    monkeypatch.setattr(video, "complete_upload_core", fake_complete)

    def fake_ready(user_id, project_id, data):
        readies.append((user_id, project_id, data))
        return video.jsonify({"upload_state": "ready"}), 200
    monkeypatch.setattr(video, "_original_ready", fake_ready)

    def call(fn, query, data=b"", headers=None, project=7):
        with _app().test_request_context("/x" + query, method="POST",
                                         data=data, headers=headers or {}):
            r = fn(project_id=project)
        if isinstance(r, tuple):
            r, status = r
        else:
            status = r.status_code
        return status, r.get_json()
    return call, events, completes, readies


def test_the_token_is_scoped_and_is_no_login():
    nbytes, ps = 100 * MIB, 8 * MIB
    tok = _token(nbytes, ps)
    with _app().app_context():
        c = upload_handoff.read(tok, 7, key=KEY, upload_id="u1")
        assert c["u"] == 60 and c["n"] == nbytes and c["s"] == ps
        assert upload_handoff.read(tok, 8) is None, "another project"
        assert upload_handoff.read(tok, 7, key="originals/7/b.mp4") is None
        assert upload_handoff.read(tok, 7, upload_id="u2") is None
        assert upload_handoff.read("x" + tok, 7) is None
    # A login token check refuses it: no subject, and an audience.
    app = _app()

    @token_required
    def guarded(user_id):
        return {"user": user_id}
    with app.test_request_context("/", headers={"Authorization": f"Bearer {tok}"}):
        _, status = guarded()
    assert status == 401
    # And a login token is no handoff token.
    login = jwt.encode({"sub": 60}, SECRET, algorithm="HS256")
    with _app().app_context():
        assert upload_handoff.read(login, 7) is None


def test_only_an_open_multipart_transfer_gets_one(monkeypatch):
    with _app().test_request_context("/", base_url="http://entrepreneur.test"):
        out = upload_handoff.attach({"mode": "multipart", "upload_id": "u1",
                                     "storage_key": KEY, "part_size": 8 * MIB},
                                    60, 7, "original", 100 * MIB)
        assert out["handoff_token"] and out["handoff_max_bytes"] > 0
        # The browser reaches the API directly, over https.
        assert out["handoff_base"] == "https://entrepreneur.test"
        assert "handoff_token" not in upload_handoff.attach(
            {"mode": "single", "storage_key": KEY}, 60, 7, "original", 1)
        assert "handoff_token" not in upload_handoff.attach(
            {"mode": "multipart", "upload_id": None, "assembled": True,
             "storage_key": KEY, "part_size": 8 * MIB}, 60, 7, "original", 1)
        monkeypatch.setenv("UPLOAD_HANDOFF", "0")
        assert "handoff_token" not in upload_handoff.attach(
            {"mode": "multipart", "upload_id": "u1", "storage_key": KEY,
             "part_size": 8 * MIB}, 60, 7, "original", 100 * MIB)


def test_a_plain_post_with_the_token_writes_a_part(routes, s3):
    call, events, *_ = routes
    nbytes, ps = 100 * MIB, 8 * MIB
    fake = s3(_S3(nbytes, ps, stored=[]))
    tok = _token(nbytes, ps)
    q = f"?key={KEY}&kind=original&upload_id=u1&part_number=3&ht={tok}"
    status, body = call(video.relay_upload_part_route, q, data=b"x" * MIB)
    assert status == 200 and body["etag"] == '"relayed3"'
    assert fake.uploaded == [(3, MIB)]
    # The same token on another upload id, key or project writes nothing.
    for bad in (q.replace("upload_id=u1", "upload_id=u2"),
                q.replace("a.mp4", "b.mp4")):
        status, _ = call(video.relay_upload_part_route, bad, data=b"x")
        assert status == 401
    status, _ = call(video.relay_upload_part_route, q, data=b"x", project=8)
    assert status == 401
    assert fake.uploaded == [(3, MIB)]


def test_without_any_token_relay_part_still_needs_a_login(routes, s3):
    call, *_ = routes
    s3(_S3(100 * MIB, 8 * MIB, stored=[]))
    status, _ = call(video.relay_upload_part_route,
                     f"?key={KEY}&upload_id=u1&part_number=1", data=b"x")
    assert status == 401


def test_handoff_complete_waits_for_every_part(routes, s3):
    call, events, completes, _ = routes
    nbytes, ps = 100 * MIB, 8 * MIB          # 13 parts
    s3(_S3(nbytes, ps, stored=range(1, 13)))
    tok = _token(nbytes, ps)
    body = json.dumps({"to": "complete",
                       "payload": {"filename": "a.mp4", "storage_key": "x"}})
    status, out = call(video.handoff_complete, f"?ht={tok}", data=body)
    assert status == 409 and out["parts_in_storage"] == 12
    assert completes == []


def test_handoff_complete_finishes_through_the_pages_own_code(routes, s3):
    call, events, completes, _ = routes
    nbytes, ps = 100 * MIB, 8 * MIB
    s3(_S3(nbytes, ps, stored=range(1, 14)))
    tok = _token(nbytes, ps)
    # The body may not move the key: the token's key wins.
    body = json.dumps({"to": "complete", "payload": {
        "filename": "a.mp4", "storage_key": "originals/7/other.mp4"}})
    status, out = call(video.handoff_complete, f"?ht={tok}", data=body)
    assert status == 200 and out == {"asset_id": 1}
    user, project, data = completes[0]
    assert (user, project) == (60, 7)
    assert data["storage_key"] == KEY and data["upload_id"] == "u1"
    assert [p["part_number"] for p in data["parts"]] == list(range(1, 14))
    assert data["filename"] == "a.mp4"
    assert "upload_handoff_landed" in events
    assert "upload_handoff_landed" in video.CLIENT_EVENT_KINDS
    assert "upload_handed_off" in video.CLIENT_EVENT_KINDS


def test_an_upload_the_page_already_finished_is_answered_idempotently(routes, s3):
    call, _, completes, _ = routes
    nbytes, ps = 100 * MIB, 8 * MIB
    s3(_S3(nbytes, ps, stored=[], gone=True, assembled=nbytes))
    tok = _token(nbytes, ps)
    status, _ = call(video.handoff_complete, f"?ht={tok}",
                     data=json.dumps({"payload": {"filename": "a.mp4"}}))
    assert status == 200
    assert completes[0][2]["upload_id"] is None and completes[0][2]["parts"] == []


def test_a_deferred_original_finishes_through_original_ready(routes, s3):
    call, _, completes, readies = routes
    nbytes, ps = 100 * MIB, 8 * MIB
    s3(_S3(nbytes, ps, stored=range(1, 14)))
    tok = _token(nbytes, ps)
    status, _ = call(video.handoff_complete, f"?ht={tok}", data=json.dumps(
        {"to": "original-ready", "payload": {"asset_id": 41}}))
    assert status == 200 and not completes
    assert readies[0][2]["asset_id"] == 41 and len(readies[0][2]["parts"]) == 13


def test_a_bad_token_finishes_nothing(routes, s3):
    call, _, completes, _ = routes
    s3(_S3(100 * MIB, 8 * MIB, stored=range(1, 14)))
    status, _ = call(video.handoff_complete, "?ht=nope", data=b"{}")
    assert status == 401 and not completes
    tok = _token(100 * MIB, 8 * MIB)
    status, _ = call(video.handoff_complete, f"?ht={tok}", data=b"{}", project=8)
    assert status == 401 and not completes


def test_a_handed_off_transfer_beats_for_the_intake(routes, s3):
    call, events, *_ = routes
    nbytes, ps = 100 * MIB, 8 * MIB
    s3(_S3(nbytes, ps, stored=[]))
    tok = _token(nbytes, ps)
    for n in (3, 4):
        q = f"?key={KEY}&kind=original&upload_id=u1&part_number={n}&ht={tok}"
        assert call(video.relay_upload_part_route, q, data=b"x")[0] == 200
    assert events.count("upload_progress") == 1
    from ops_loop import intake
    assert "upload_handed_off" in intake.UPLOAD_ALIVE_KINDS


def test_a_local_stack_keeps_its_own_scheme_and_the_env_wins(monkeypatch):
    with _app().test_request_context("/", base_url="http://127.0.0.1:5401"):
        assert upload_handoff.public_base() == "http://127.0.0.1:5401"
        monkeypatch.setenv("UPLOAD_HANDOFF_BASE", "https://api.example/")
        assert upload_handoff.public_base() == "https://api.example"
