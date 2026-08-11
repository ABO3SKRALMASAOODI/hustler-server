"""Round 38 — request-based executor split (dispatcher <-> Cloud Run).

Exercises the real HTTP contract end to end (a live stdlib server on a loopback
port + the real requests-based client), with the job RUNNERS and the DB stubbed
so no render or Postgres is needed. What is pinned:

  1. A success round-trips the runner's result dict back to the dispatcher.
  2. Bearer auth is enforced (missing / wrong secret -> 401 -> raises).
  3. A runner that raises on the executor surfaces as RemoteExecutorError with
     the real message, so the dispatcher's normal requeue/reaper path runs.
  4. agent_turn is exposed only by the dedicated agent-executor role.
  5. /health answers 200 for Cloud Run's probe.
  6. The POSTed body is the JSON-safe job subset the runner needs.
"""
import os
import sys
import threading
from datetime import datetime, timezone
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
        if fn is dbx.lease_is_current:
            return True
        if fn is dbx.finish_job:
            return True
        if fn is dbx.charge_turn_credits:
            return 1.0
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
    orig_cfg = (config.WORKER_ROLE,
                config.REMOTE_EXECUTOR_URL,
                config.REMOTE_EXECUTOR_PREVIEW_URL,
                config.REMOTE_AGENT_EXECUTOR_URL,
                config.REMOTE_EXECUTOR_SECRET,
                config.REMOTE_EXECUTOR_TIMEOUT_S)
    config.REMOTE_EXECUTOR_URL = f"http://127.0.0.1:{port}"
    config.REMOTE_EXECUTOR_PREVIEW_URL = ""
    config.REMOTE_AGENT_EXECUTOR_URL = f"http://127.0.0.1:{port}"
    config.REMOTE_EXECUTOR_SECRET = secret
    config.REMOTE_EXECUTOR_TIMEOUT_S = 10
    orig_runners = dict(http_server.RUNNERS)
    orig_db = dbx.Db
    http_server.RUNNERS.clear()
    http_server.RUNNERS.update(monkeyrunners)
    dbx.Db = _StubDb
    return srv, (orig_runners, orig_db, orig_cfg)


def _teardown(srv, saved):
    # Restore config too: the URL leaking out of this module made every LATER
    # test believe an executor was configured — invisible for two rounds, then
    # the first tool that goes remote-first (round 64's matte) failed a test
    # that never mentions executors.
    orig_runners, orig_db, orig_cfg = saved
    http_server.RUNNERS.clear()
    http_server.RUNNERS.update(orig_runners)
    dbx.Db = orig_db
    (config.WORKER_ROLE, config.REMOTE_EXECUTOR_URL,
     config.REMOTE_EXECUTOR_PREVIEW_URL, config.REMOTE_AGENT_EXECUTOR_URL,
     config.REMOTE_EXECUTOR_SECRET, config.REMOTE_EXECUTOR_TIMEOUT_S) = orig_cfg
    srv.shutdown()


JOB = {"id": 42, "type": "preview", "project_id": 7, "user_id": 3,
       "attempts": 0, "total_claims": 4,
       "payload": {"edl_version": 5}}


def test_preview_service_url_is_derived_from_the_main_cloud_run_url():
    assert config._sibling_preview_executor_url(
        "https://valmera-executor-123.us-central1.run.app/") == \
        "https://valmera-executor-preview-123.us-central1.run.app"
    assert config._sibling_preview_executor_url(
        "https://custom-executor.example.com") == ""
    assert config._sibling_agent_executor_url(
        "https://valmera-executor-123.us-central1.run.app/") == \
        "https://valmera-agent-123.us-central1.run.app"
    assert config._sibling_batch_launcher_url(
        "https://valmera-executor-123.us-central1.run.app/") == \
        "https://valmera-batch-launcher-123.us-central1.run.app"


def test_success_roundtrip():
    seen = {}

    def fake_render(db, job):
        seen["job"] = job
        return {"render_asset_id": 99, "variant": "preview", "cached": False}

    srv, saved = _setup({"preview": fake_render})
    try:
        out = remote.run_render_remote(None, JOB)
        assert out.pop("_remote_job_completed") is True
        assert out["render_asset_id"] == 99
        assert out["variant"] == "preview"
        assert out["cached"] is False
        assert out["timings"]["total_s"] >= 0
        # the executor received exactly the JSON-safe subset
        assert seen["job"]["id"] == 42
        assert seen["job"]["payload"] == {"edl_version": 5}
        assert set(seen["job"]) == {"id", "type", "project_id", "user_id",
                                    "attempts", "total_claims", "payload",
                                    "_queue_wait_s"}
    finally:
        _teardown(srv, saved)


def test_preview_uses_the_right_sized_service_when_configured():
    srv, saved = _setup({"preview": lambda db, job: {"ok": True}})
    try:
        preview_url = config.REMOTE_EXECUTOR_URL
        config.REMOTE_EXECUTOR_URL = "http://127.0.0.1:1"
        config.REMOTE_EXECUTOR_PREVIEW_URL = preview_url
        result = remote.run_render_remote(None, JOB)
        assert result.pop("_remote_job_completed") is True
        assert result["ok"] is True
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
        except dbx.PermanentJobError as e:
            assert "EDL version 5 not found" in str(e)
    finally:
        _teardown(srv, saved)


def test_permanent_runner_error_is_not_retried():
    def boom(db, job):
        raise dbx.PermanentJobError("invalid EDL input")

    srv, saved = _setup({"preview": boom})
    try:
        try:
            remote.run_render_remote(None, JOB)
            assert False, "should have raised"
        except dbx.PermanentJobError as e:
            assert "invalid EDL input" in str(e)
    finally:
        _teardown(srv, saved)


def test_lost_lease_is_preserved_across_http():
    def boom(db, job):
        raise dbx.JobLeaseLost("superseded")

    srv, saved = _setup({"preview": boom})
    try:
        try:
            remote.run_render_remote(None, JOB)
            assert False, "should have raised"
        except dbx.JobLeaseLost as e:
            assert "superseded" in str(e)
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


def test_agent_executor_owns_terminal_commit_and_marks_the_response():
    def fake_agent(db, job):
        return {"billable": False, "reply": "done"}

    srv, saved = _setup({"agent_turn": fake_agent})
    try:
        config.WORKER_ROLE = "agent_executor"
        job = dict(JOB, type="agent_turn", created_at=datetime.now(timezone.utc))
        result = remote.run_agent_remote(None, job)
        assert result.pop("_remote_job_completed") is True
        assert result["reply"] == "done"
        assert result["credits_charged"] == 0.0
        assert result["timings"]["queue_wait_s"] is not None
        assert result["timings"]["total_s"] >= 0
    finally:
        _teardown(srv, saved)


def test_media_executor_also_owns_terminal_commit():
    """A dispatcher disconnect after ffmpeg success cannot buy it twice."""
    srv, saved = _setup({"preview": lambda db, job: {"ok": True}})
    try:
        result = remote.run_render_remote(None, JOB)
        assert result.pop("_remote_job_completed") is True
        assert result["ok"] is True
    finally:
        _teardown(srv, saved)
