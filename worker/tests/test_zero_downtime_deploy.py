"""Zero-downtime deploys (Oct 2026, rel).

In the week to 15 Oct, 49 of 72 failed jobs were "Deploy in progress": Render
publishes the dispatcher minutes after a push while Cloudflare follows 25-60
minutes later (or not at all when its workflow fails), and the dispatcher
refused every call while the two source fingerprints differed. What is pinned:

  1. The dispatcher tolerates source skew only when the Worker advertises the
     skew guard; an older Worker keeps the strict wait.
  2. Every job names the release that wrote it (``dispatcher_source``).
  3. Under skew the executor refuses, before any work and retryably, exactly
     the jobs its release cannot run: unknown job type, unknown MCP tool or
     argument, an EDL its own schema rejects (previews degrade unknown fx the
     way the render path already does; finals wait for every device).
  4. A refusal is relaunched inside the claim on a fresh alternate identity,
     then returned to the queue, never surfaced as a failure while the
     rollout converges.
  5. A launch that never reached its Durable Object is fenced after
     CLOUDFLARE_MISSING_FENCE_S instead of waiting out a six-hour lease.
  6. SIGTERM drains the executor: in-flight calls finish, new ones are
     refused as a rollout wait, the process exits once idle.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import config  # noqa: E402
import db as dbx  # noqa: E402
import executor_runtime  # noqa: E402
import failure_policy  # noqa: E402
import remote  # noqa: E402
import skew_guard  # noqa: E402
import version  # noqa: E402


def _enable(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://executor.example")
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", False)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    remote._health_cache.clear()


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _clean_cache():
    remote._health_cache.clear()
    yield
    remote._health_cache.clear()


# ------------------------------------------------------- 1. preflight ---

def test_guarded_worker_on_another_release_is_used(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote.requests, "get", lambda *_a, **_k: _Response({
        "status": "ok", "provider": "cloudflare",
        "source_version": "older-source",
        "features": ["custom_filter", "stems", "skew_guard", "fence"]}))
    body = remote._cloudflare_preflight()
    assert body["source_version"] == "older-source"
    # The tolerant answer is cached like a matching one.
    monkeypatch.setattr(remote.requests, "get",
                        lambda *_a, **_k: pytest.fail("cached"))
    assert remote._cloudflare_preflight() is body
    assert "fence" in remote._cloudflare_features()


def test_unguarded_worker_on_another_release_still_waits(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote.requests, "get", lambda *_a, **_k: _Response({
        "status": "ok", "provider": "cloudflare",
        "source_version": "older-source",
        "features": ["custom_filter", "stems"]}))
    with pytest.raises(remote.CloudflareRolloutPending, match="does not match"):
        remote._cloudflare_preflight()
    assert remote._cloudflare_features() == []


def test_every_job_names_the_release_that_wrote_it():
    payload = remote._job_payload({"id": 1, "type": "preview",
                                   "project_id": 2, "payload": {}})
    assert payload["dispatcher_source"] == version.code_version()


def test_executor_advertises_the_guard():
    assert skew_guard.FEATURE in version.FEATURES


# ------------------------------------------------------ 3. skew guard ---

class _Db:
    """LeasedDb stand-in: run(fn, *args) with a fake connection."""

    def __init__(self, edls=None):
        self.edls = edls or {}
        self.calls = []

    def run(self, fn, *args, **kwargs):
        self.calls.append(fn.__name__)
        return fn(_Conn(self.edls), *args, **kwargs)


class _Conn:
    def __init__(self, edls):
        self.edls = edls

    def cursor(self):
        return _Cur(self.edls)


class _Cur:
    def __init__(self, edls):
        self.edls, self.row = edls, None

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def execute(self, sql, params):
        project = params[0]
        versions = self.edls.get(project) or {}
        if len(params) == 2:
            data = versions.get(params[1])
        else:
            data = versions[max(versions)] if versions else None
        self.row = None if data is None else {"json": data}

    def fetchone(self):
        return self.row


_VALID_EDL = {"keep": [[0.0, 5.0]]}


def _skewed(job):
    return dict(job, dispatcher_source="some-newer-release")


def _runners():
    return {"mcp_tool": object(), "preview": object(), "final": object(),
            "index": object(), "agent_turn": object()}


def test_same_release_never_checks_anything():
    db = _Db()
    job = {"id": 1, "type": "preview", "project_id": 7,
           "dispatcher_source": version.code_version(),
           "payload": {"edl_version": 3}}
    skew_guard.check(db, job, _runners())
    assert db.calls == []
    # A dispatcher that predates the guard sends no source at all.
    skew_guard.check(db, {"id": 1, "type": "zzz_unknown"}, _runners())
    assert db.calls == []


def test_unknown_job_type_waits_for_its_release():
    with pytest.raises(skew_guard.RolloutPending, match="job type sidecar_v9"):
        skew_guard.check(_Db(), _skewed({"id": 1, "type": "sidecar_v9",
                                         "project_id": 7}), _runners())


def test_compatible_jobs_run_on_the_older_release():
    db = _Db({7: {3: _VALID_EDL, 4: _VALID_EDL}})
    skew_guard.check(db, _skewed({"id": 1, "type": "preview",
                                  "project_id": 7,
                                  "payload": {"edl_version": 3}}), _runners())
    skew_guard.check(db, _skewed({"id": 2, "type": "mcp_tool",
                                  "project_id": 7,
                                  "payload": {"tool": "get_edl",
                                              "args": {}}}), _runners())
    skew_guard.check(db, _skewed({"id": 3, "type": "index", "project_id": 7,
                                  "payload": {"asset_id": 5}}), _runners())


def test_edl_with_fields_this_release_does_not_know_waits():
    newer = dict(_VALID_EDL, holographic_layer={"on": True})
    db = _Db({7: {9: newer}})
    for job in ({"type": "final", "payload": {"edl_version": 9}},
                {"type": "preview", "payload": {"edl_version": 9}},
                {"type": "mcp_tool", "payload": {"tool": "get_edl",
                                                 "args": {}}},
                {"type": "agent_turn", "payload": {}}):
        with pytest.raises(skew_guard.RolloutPending, match="EDL uses fields"):
            skew_guard.check(db, _skewed(dict(job, id=1, project_id=7)),
                             _runners())


def test_unknown_fx_degrades_previews_but_finals_wait():
    import schemas
    if not hasattr(schemas, "strip_unknown_fx"):
        pytest.skip("no fx registry")
    newer = dict(_VALID_EDL, fx=[{"id": "fx1", "type": "type_from_2027",
                                  "start": 0, "end": 1}])
    db = _Db({7: {9: newer}})
    skew_guard.check(db, _skewed({"id": 1, "type": "preview", "project_id": 7,
                                  "payload": {"edl_version": 9}}), _runners())
    with pytest.raises(skew_guard.RolloutPending):
        skew_guard.check(db, _skewed({"id": 1, "type": "final",
                                      "project_id": 7,
                                      "payload": {"edl_version": 9}}),
                         _runners())


def test_unknown_mcp_tool_or_argument_waits():
    db = _Db({7: {1: _VALID_EDL}})
    with pytest.raises(skew_guard.RolloutPending, match="not in this release"):
        skew_guard.check(db, _skewed({"id": 1, "type": "mcp_tool",
                                      "project_id": 7, "payload": {
                                          "tool": "add_hologram_2027",
                                          "args": {}}}), _runners())
    with pytest.raises(skew_guard.RolloutPending, match="no argument"):
        skew_guard.check(db, _skewed({"id": 1, "type": "mcp_tool",
                                      "project_id": 7, "payload": {
                                          "tool": "get_edl",
                                          "args": {"quantum_mode": True}}}),
                         _runners())
    # Control tools are mcp_exec's own and always present.
    skew_guard.check(db, _skewed({"id": 1, "type": "mcp_tool",
                                  "project_id": 7, "payload": {
                                      "tool": "__state__", "args": {}}}),
                     _runners())


def test_a_broken_check_never_blocks_a_job(monkeypatch):
    class Boom:
        def run(self, *_a, **_k):
            raise RuntimeError("database blip")
    skew_guard.check(Boom(), _skewed({"id": 1, "type": "preview",
                                      "project_id": 7,
                                      "payload": {"edl_version": 3}}),
                     _runners())


def test_refusal_is_a_retryable_pre_work_rollout_wait():
    exc = skew_guard._refuse({"type": "final"}, "why", ("new", "old"))
    decision = failure_policy.classify(exc, "final")
    assert decision.kind == "provider_rollout_pending"
    assert decision.retryable is True
    assert str(exc).startswith("Deploy in progress")
    assert "nothing ran" in str(exc)


def test_executor_returns_a_structured_refusal_without_running(monkeypatch):
    ran = []
    finished = []
    monkeypatch.setattr(executor_runtime, "_confirm_provider_ownership",
                        lambda _db, _job: None)

    class Leased:
        def __init__(self, *_a):
            pass

        def run(self, fn, *args, **kwargs):
            if fn is dbx.lease_is_current:
                return True
            if fn is dbx.finish_remote_execution:
                finished.append(args[2])
                return True
            if fn is skew_guard._edl_json:
                return dict(_VALID_EDL, holographic_layer=1)
            return None

        def reset(self):
            pass

        def release(self):
            pass

    monkeypatch.setattr(executor_runtime, "LeasedDb", Leased)
    job = _skewed({"id": 5, "type": "final", "project_id": 7,
                   "total_claims": 1, "payload": {"edl_version": 2}})
    out = executor_runtime.execute(job, {"final": lambda *_a: ran.append(1)})
    assert ran == []
    assert out["skew_refused"] is True
    assert out["retryable"] is True
    assert out["failure"]["kind"] == "provider_rollout_pending"
    assert finished == ["failed"]       # the claim's ledger row is closed
    # An unknown job type under skew is the same refusal, not a 400.
    out = executor_runtime.execute(_skewed({"id": 6, "type": "sidecar_v9"}),
                                   {"final": None})
    assert out["skew_refused"] is True
    assert out["failure"]["kind"] == "provider_rollout_pending"


# ------------------------------------------- 4. relaunch, then queue ---

class _LeaseProbe:
    def run(self, fn, *_args):
        assert fn is dbx.lease_is_current
        return True

    def reset(self):
        pass


def _skew_refusal():
    err = remote.CloudflareTerminalFailure("Deploy in progress: this final "
                                           "needs the release ...")
    err.failure_kind = "provider_rollout_pending"
    err.retryable = True
    err.max_attempts = 1
    return err


def test_refused_claim_relaunches_on_fresh_identities(monkeypatch):
    _enable(monkeypatch)
    now = [0.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep",
                        lambda d: now.__setitem__(0, now[0] + d))
    monkeypatch.setattr(dbx, "Db", _LeaseProbe)
    monkeypatch.setattr(dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote, "_cloudflare_features",
                        lambda: ["skew_guard", "alt_slots_32"])
    identities = []

    def launch(job):
        identities.append(remote._cloudflare_call_id(job))
        if len(identities) < 3:
            raise _skew_refusal()
        return {"ok": True}

    monkeypatch.setattr(remote, "_run_cloudflare", launch)
    job = {"id": 42, "type": "final", "project_id": 7, "total_claims": 1}
    assert remote._run_cloudflare_with_capacity_wait(job) == {"ok": True}
    assert identities[0].startswith("cf-final-")
    assert identities[1].startswith("cf-alt1-final-p7-")
    assert identities[2].startswith("cf-alt2-final-p7-")
    assert len(set(identities)) == 3
    assert 20 <= now[0] < config.CLOUDFLARE_ROLLOUT_WAIT_S


def test_refusals_end_in_a_queue_deferral_not_a_failure(monkeypatch):
    _enable(monkeypatch)
    now = [0.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep",
                        lambda d: now.__setitem__(0, now[0] + d))
    monkeypatch.setattr(dbx, "Db", _LeaseProbe)
    monkeypatch.setattr(dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote, "_cloudflare_features",
                        lambda: ["skew_guard", "alt_slots_32"])
    monkeypatch.setattr(remote, "_run_cloudflare",
                        lambda _job: (_ for _ in ()).throw(_skew_refusal()))
    job = {"id": 42, "type": "mcp_tool", "project_id": 7, "total_claims": 1}
    with pytest.raises(remote.CloudflareRolloutPending) as caught:
        remote._run_cloudflare_with_capacity_wait(job)
    assert now[0] <= config.CLOUDFLARE_ROLLOUT_WAIT_S + 1
    assert str(caught.value).startswith("Deploy in progress")
    # main.process_one returns this kind to the queue for every job type
    # (test_cloudflare_call_lifecycle pins that path).
    assert failure_policy.decision_for(caught.value, "mcp_tool").kind == \
        "provider_rollout_pending"


def test_wide_identities_need_a_worker_that_recognises_them(monkeypatch):
    monkeypatch.setattr(remote, "_cloudflare_features", lambda: ["skew_guard"])
    assert remote._next_rollout_slot({"_cloudflare_admission_slot": 1}) == 2
    assert remote._next_rollout_slot({"_cloudflare_admission_slot": 2}) is None
    monkeypatch.setattr(remote, "_cloudflare_features",
                        lambda: ["alt_slots_32"])
    assert remote._next_rollout_slot({"_cloudflare_admission_slot": 2}) == 3
    assert remote._next_rollout_slot({"_cloudflare_admission_slot": 32}) \
        is None
    job = {"id": 9, "type": "index", "project_id": 4, "total_claims": 2,
           "_cloudflare_admission_slot": 17,
           "_cloudflare_rollout_spread": True}
    assert remote._cloudflare_call_id(job).startswith("cf-alt17-index-p4-")


def test_children_do_not_wait_on_a_refusal(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote.time, "sleep",
                        lambda _s: pytest.fail("children must not wait"))
    monkeypatch.setattr(remote, "_run_cloudflare",
                        lambda _job: (_ for _ in ()).throw(_skew_refusal()))
    with pytest.raises(remote.CloudflareRolloutPending):
        remote._run_cloudflare_with_capacity_wait(
            {"id": None, "type": "frames", "project_id": 1, "payload": {}})


# ------------------------------------------------------------- 5. fence ---

def test_launch_that_never_arrived_is_fenced_not_awaited(monkeypatch):
    _enable(monkeypatch)
    now = [0.0]
    monkeypatch.setattr(remote.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(remote.time, "sleep",
                        lambda d: now.__setitem__(0, now[0] + d))
    monkeypatch.setattr(remote, "_cloudflare_features", lambda: ["fence"])
    monkeypatch.setattr(remote, "_cloudflare_status",
                        lambda *_a, **_k: {"status": "missing"})

    class NoJob:
        def run(self, fn, *_a):
            return None

        def reset(self):
            pass

    monkeypatch.setattr(dbx, "Db", NoJob)
    posts = []
    envelope = {"error": "never received", "retryable": True, "fenced": True,
                "failure": {"kind": "provider_start_abandoned",
                            "retryable": True}}

    def post(url, json=None, **_k):
        posts.append((url, json))
        return _Response({"status": "failed", "fenced": True,
                          "envelope": envelope})

    monkeypatch.setattr(remote.requests, "post", post)
    job = {"id": 3, "type": "mcp_tool", "project_id": 8, "total_claims": 1,
           "_cloudflare_launch_id": "launch-abc"}
    out = remote._recover_cloudflare_result("cf-mcp-p8-x" * 2, "mcp", job,
                                            deadline=6 * 3600)
    assert out == envelope
    assert len(posts) == 1
    assert posts[0][0].endswith("/fence")
    assert posts[0][1]["launch_id"] == "launch-abc"
    assert config.CLOUDFLARE_MISSING_FENCE_S <= now[0] < 120


def test_an_older_worker_is_never_asked_to_fence(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(remote, "_cloudflare_features", lambda: [])
    monkeypatch.setattr(remote.requests, "post",
                        lambda *_a, **_k: pytest.fail("no fence route"))
    assert remote._fence_cloudflare_call("cf-x-12345678", "mcp", {}, "l") \
        is None


# ------------------------------------------------------------- 6. drain ---

def test_sigterm_drains_in_flight_calls_then_exits(monkeypatch):
    import http_server
    exits = []
    monkeypatch.setattr(http_server.os, "_exit", lambda code: exits.append(code))
    monkeypatch.setattr(http_server, "_DRAINING", http_server.threading.Event())
    monkeypatch.setattr(http_server, "_INFLIGHT", 1)
    started = []

    class _Thread:
        def __init__(self, target, **_k):
            started.append(target)

        def start(self):
            pass

    monkeypatch.setattr(http_server.threading, "Thread", _Thread)
    http_server._on_sigterm(15, None)
    assert http_server._DRAINING.is_set()
    assert len(started) == 1
    # A /run that still arrives is a pre-work rollout wait, never a failure.
    envelope = http_server._draining_envelope({"type": "final"})
    assert envelope["skew_refused"] is True
    assert envelope["failure"]["kind"] == "provider_rollout_pending"
    # The in-flight call finishes; only then does the process exit.
    sleeps = []

    def fake_sleep(_s):
        sleeps.append(1)
        http_server._inflight(-1)

    monkeypatch.setattr(http_server.time, "sleep", fake_sleep)

    class _Stop(Exception):
        pass

    def exit_once(code):
        exits.append(code)
        raise _Stop()

    monkeypatch.setattr(http_server.os, "_exit", exit_once)
    with pytest.raises(_Stop):
        http_server._exit_when_idle(poll_s=0)
    assert sleeps == [1] and exits == [0]


def test_a_run_arriving_during_drain_is_counted_then_refused(monkeypatch):
    """Review fix: /run counts itself in flight BEFORE reading the drain
    flag, so the drain thread can never exit between an admitted call's
    check and its count; a refused call leaves the count balanced."""
    import json as _json
    import threading as _threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    import http_server
    monkeypatch.setattr(http_server, "_DRAINING", _threading.Event())
    monkeypatch.setattr(http_server, "_INFLIGHT", 0)
    monkeypatch.setattr(http_server, "_authorized", lambda _h: True)
    seen = []
    real_inflight = http_server._inflight

    def counting(delta):
        value = real_inflight(delta)
        seen.append((delta, http_server._DRAINING.is_set()))
        return value

    monkeypatch.setattr(http_server, "_inflight", counting)
    monkeypatch.setattr(http_server.executor_runtime, "execute",
                        lambda *_a, **_k: pytest.fail("nothing may run"))
    http_server._DRAINING.set()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), http_server.Handler)
    _threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        jtype = next(iter(http_server.RUNNERS))
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.server_address[1]}/run",
            data=_json.dumps({"job": {"type": jtype, "id": 7}}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = _json.loads(resp.read())
    finally:
        srv.shutdown()
        srv.server_close()
    assert body["failure"]["kind"] == "provider_rollout_pending"
    assert body["skew_refused"] is True
    assert seen == [(1, True), (-1, True)]
    assert http_server._INFLIGHT == 0
