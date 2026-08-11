"""Durable batch handoff: no Render deploy can duplicate paid work."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import remote  # noqa: E402


JOB = {"id": 91, "type": "final", "project_id": 7, "user_id": 3,
       "attempts": 1, "total_claims": 2, "payload": {"edl_version": 5}}


class _Response:
    status_code = 200
    text = ""

    def json(self):
        return {"launched": True, "operation": "operations/op-1"}


class _Db:
    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def run(self, fn, *args):
        self.calls.append((fn, args))
        if fn is dbx.reserve_batch_launch:
            return True
        if fn is dbx.record_batch_launch:
            return True
        if fn is dbx.clear_batch_launch:
            return True
        if fn is dbx.get_job:
            return self.states.pop(0)
        raise AssertionError(fn)


def test_batch_success_is_read_from_authoritative_db(monkeypatch):
    db = _Db([{"state": "done", "result": {"render_asset_id": 44}}])
    monkeypatch.setattr(config, "REMOTE_BATCH_LAUNCHER_URL",
                        "https://launcher.invalid")
    monkeypatch.setattr(config, "REMOTE_BATCH_JOB_NAME", "valmera-batch")
    monkeypatch.setattr(remote.requests, "post", lambda *a, **k: _Response())
    untracked = []
    monkeypatch.setattr(dbx, "untrack_job", untracked.append)

    result = remote._launch_batch_and_wait(db, JOB)

    assert result["render_asset_id"] == 44
    assert result["_remote_job_completed"] is True
    assert result["_remote_job_terminal_state"] == "done"
    assert untracked == [91]
    assert any(fn is dbx.record_batch_launch for fn, _ in db.calls)


def test_definite_launcher_refusal_clears_mark_for_safe_fallback(monkeypatch):
    class Refused(_Response):
        status_code = 503

        def json(self):
            return {"error": "job does not exist", "safe_to_fallback": True}

    db = _Db([])
    monkeypatch.setattr(config, "REMOTE_BATCH_LAUNCHER_URL",
                        "https://launcher.invalid")
    monkeypatch.setattr(config, "REMOTE_BATCH_JOB_NAME", "valmera-batch")
    monkeypatch.setattr(remote.requests, "post", lambda *a, **k: Refused())

    try:
        remote._launch_batch_and_wait(db, JOB)
        assert False, "must fall back"
    except remote.BatchUnavailable:
        pass
    assert any(fn is dbx.clear_batch_launch for fn, _ in db.calls)


def test_ambiguous_response_never_clears_or_launches_a_duplicate(monkeypatch):
    db = _Db([])
    monkeypatch.setattr(config, "REMOTE_BATCH_LAUNCHER_URL",
                        "https://launcher.invalid")
    monkeypatch.setattr(config, "REMOTE_BATCH_JOB_NAME", "valmera-batch")
    monkeypatch.setattr(
        remote.requests, "post",
        lambda *a, **k: (_ for _ in ()).throw(
            remote.requests.ConnectionError("response lost")))
    untracked = []
    monkeypatch.setattr(dbx, "untrack_job", untracked.append)

    try:
        remote._launch_batch_and_wait(db, JOB)
        assert False, "must detach"
    except remote.RemoteBatchDetached:
        pass
    assert not any(fn is dbx.clear_batch_launch for fn, _ in db.calls)
    assert untracked == [91]
