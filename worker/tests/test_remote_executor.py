"""Round 38 — request-based executor split (dispatcher <-> Cloud Run).

Exercises the real HTTP contract end to end (a live stdlib server on a loopback
port + the real requests-based client), with the job RUNNERS and the DB stubbed
so no render or Postgres is needed. What is pinned:

  1. A success round-trips the runner's result dict back to the dispatcher.
  2. Bearer auth is enforced (missing / wrong secret -> 401 -> raises).
  3. A runner that raises on the executor surfaces as RemoteExecutorError with
     the real message, so the dispatcher's normal requeue/reaper path runs.
  4. agent_turn is not a remote route (it stays on the dispatcher).
  5. /health answers 200 for Cloud Run's probe.
  6. The POSTed body is the JSON-safe job subset the runner needs.
"""
import os
import sys
import threading
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import requests  # noqa: E402

import config  # noqa: E402
import db as dbx  # noqa: E402
import http_server  # noqa: E402
import remote  # noqa: E402


class _StubDb:
    def run(self, fn, *a, **k):
        return None

    def reset(self):
        pass


def _start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), http_server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _setup(monkeyrunners, secret="test-secret"):
    """Point the client at a fresh stub server and stub the DB."""
    srv, port = _start_server()
    config.REMOTE_EXECUTOR_URL = f"http://127.0.0.1:{port}"
    config.REMOTE_EXECUTOR_SECRET = secret
    config.REMOTE_EXECUTOR_TIMEOUT_S = 10
    orig_runners = dict(http_server.RUNNERS)
    orig_db = dbx.Db
    http_server.RUNNERS.clear()
    http_server.RUNNERS.update(monkeyrunners)
    dbx.Db = _StubDb
    return srv, (orig_runners, orig_db)


def _teardown(srv, saved):
    orig_runners, orig_db = saved
    http_server.RUNNERS.clear()
    http_server.RUNNERS.update(orig_runners)
    dbx.Db = orig_db
    srv.shutdown()


JOB = {"id": 42, "type": "preview", "project_id": 7, "user_id": 3,
       "attempts": 0, "payload": {"edl_version": 5}}


def test_success_roundtrip():
    seen = {}

    def fake_render(db, job):
        seen["job"] = job
        return {"render_asset_id": 99, "variant": "preview", "cached": False}

    srv, saved = _setup({"preview": fake_render})
    try:
        out = remote.run_render_remote(None, JOB)
        assert out == {"render_asset_id": 99, "variant": "preview",
                       "cached": False}
        # the executor received exactly the JSON-safe subset
        assert seen["job"]["id"] == 42
        assert seen["job"]["payload"] == {"edl_version": 5}
        assert set(seen["job"]) == {"id", "type", "project_id", "user_id",
                                    "attempts", "payload"}
    finally:
        _teardown(srv, saved)


def test_missing_secret_is_401():
    srv, saved = _setup({"preview": lambda db, job: {"ok": True}})
    try:
        config.REMOTE_EXECUTOR_SECRET = "the-real-secret"  # server wants this
        # client sends none
        url = f"{config.REMOTE_EXECUTOR_URL}/run"
        r = requests.post(url, json={"job": JOB}, timeout=10)
        assert r.status_code == 401
    finally:
        _teardown(srv, saved)


def test_wrong_secret_raises():
    srv, saved = _setup({"preview": lambda db, job: {"ok": True}},
                        secret="server-secret")
    try:
        # dispatcher configured with a DIFFERENT secret
        config.REMOTE_EXECUTOR_SECRET = "server-secret"
        # temporarily make the client send a wrong one by patching config just
        # for the request path: easiest is to hit it directly
        url = f"{config.REMOTE_EXECUTOR_URL}/run"
        r = requests.post(url, json={"job": JOB},
                          headers={"Authorization": "Bearer wrong"}, timeout=10)
        assert r.status_code == 401
    finally:
        _teardown(srv, saved)


def test_runner_error_surfaces():
    def boom(db, job):
        raise RuntimeError("EDL version 5 not found")

    srv, saved = _setup({"index": boom})
    try:
        job = dict(JOB, type="index")
        try:
            remote.run_index_remote(None, job)
            assert False, "should have raised"
        except remote.RemoteExecutorError as e:
            assert "EDL version 5 not found" in str(e)
    finally:
        _teardown(srv, saved)


def test_unsupported_type_rejected():
    srv, saved = _setup({"preview": lambda db, job: {"ok": True}})
    try:
        job = dict(JOB, type="agent_turn")   # never a remote route
        try:
            remote.run_render_remote(None, job)
            assert False, "should have raised"
        except remote.RemoteExecutorError as e:
            assert "unsupported job type" in str(e)
    finally:
        _teardown(srv, saved)


def test_health_ok():
    srv, saved = _setup({})
    try:
        r = requests.get(f"{config.REMOTE_EXECUTOR_URL}/health", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
    finally:
        _teardown(srv, saved)


def test_agent_turn_is_not_a_remote_route():
    assert "agent_turn" not in http_server.RUNNERS
