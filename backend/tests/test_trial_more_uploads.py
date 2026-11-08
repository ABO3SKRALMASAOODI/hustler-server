"""A trial account adds clips while its allowance lasts (p5565, 8 Nov 2026).

Account 2897's trial project had its First cut on the way and $0.40 of its
$0.71 allowance left when it tried to add two clips; create_upload's wall
("I received your video. Upgrade to start editing it.", 402) refused both
before a byte moved, because it asked only whether the account had a plan.
The owner's rule: no paywall before Download or before the trial runs out."""
import contextlib
import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SKIP_DB_INIT", "1")

import free_trial                                       # noqa: E402
from routes import video                                # noqa: E402


class _Cur:
    def __init__(self, keys=("projects/1/original.mp4",)):
        self.keys, self.rows, self.sql = list(keys), [], []

    def execute(self, sql, args=None):
        self.sql.append(sql)
        if "FROM assets" in sql and "kind = 'original'" in sql:
            self.rows = [{"storage_key": k} for k in self.keys]
        else:
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


@pytest.fixture
def gate(monkeypatch):
    state = {"gated": True, "allowance": {"allowed": True, "remaining_usd": 0.40}}
    monkeypatch.setattr(video, "_subscribe_gate_applies", lambda c, u: state["gated"])
    monkeypatch.setattr(free_trial, "request_allowance", lambda c, u, p: state["allowance"])
    return state


def test_a_trial_with_allowance_left_adds_clips(gate):
    assert video._more_uploads_walled(_Cur(), 2897, 5565) is False
    assert video._gate_refuses_more_uploads(_Cur(), 5565, 2897, "projects/1/clip.mp4") is False


def test_a_spent_trial_meets_the_wall(gate):
    gate["allowance"] = {"allowed": False, "reason": "trial_spent", "remaining_usd": 0.0}
    assert video._more_uploads_walled(_Cur(), 2897, 5565) is True
    assert video._gate_refuses_more_uploads(_Cur(), 5565, 2897, "projects/1/clip.mp4") is True


def test_a_project_that_is_not_the_trial_keeps_the_wall(gate):
    gate["allowance"] = {"allowed": False, "reason": "not_trial_project"}
    assert video._more_uploads_walled(_Cur(), 2897, 9999) is True


def test_a_subscriber_never_meets_it(gate):
    gate["gated"] = False
    gate["allowance"] = {"allowed": False}
    assert video._more_uploads_walled(_Cur(), 1, 2) is False


def test_a_failed_allowance_lookup_keeps_the_wall(gate, monkeypatch):
    def boom(*a):
        raise RuntimeError("db")
    monkeypatch.setattr(free_trial, "request_allowance", boom)
    assert video._more_uploads_walled(_Cur(), 2897, 5565) is True


def _post(monkeypatch, cur, body):
    app = Flask(__name__)
    app.register_blueprint(video.video_bp)

    @contextlib.contextmanager
    def fake_vdb():
        yield type("C", (), {"cursor": lambda self: cur, "commit": lambda self: None})()

    monkeypatch.setattr(video, "vdb", fake_vdb)
    monkeypatch.setattr(video, "_project_for_user", lambda c, p, u: {"id": p, "chat_session_id": 55})
    monkeypatch.setattr(video.storage, "is_configured", lambda: True)
    monkeypatch.setattr(video, "record_client_event", lambda *a, **k: None)
    reached = []

    def transfer(*a, **k):
        reached.append(a)
        raise LookupError("past the wall")   # create_upload answers its own presign failure
    monkeypatch.setattr(video, "_transfer_of_content", transfer)
    with app.test_request_context(f"/projects/5565/uploads", method="POST", json=body):
        out = video.create_upload.__wrapped__(2897, 5565)
    if reached:
        return "transfer", None          # the presign step ran: no wall
    resp, status = out if isinstance(out, tuple) else (out, 200)
    return status, resp.get_json()


def test_the_route_mints_a_transfer_for_the_trial_clip(gate, monkeypatch):
    status, _ = _post(monkeypatch, _Cur(), {"filename": "b.mp4", "bytes": 89041235, "kind": "clip", "staged": True})
    assert status == "transfer"


def test_the_route_still_walls_a_spent_trial(gate, monkeypatch):
    gate["allowance"] = {"allowed": False, "reason": "trial_spent"}
    status, body = _post(monkeypatch, _Cur(), {"filename": "b.mp4", "bytes": 89041235, "kind": "clip", "staged": True})
    assert status == 402 and body["subscribe_gated"] is True
