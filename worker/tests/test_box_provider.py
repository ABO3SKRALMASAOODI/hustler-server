"""The render box as an execution provider (worker/box_routing.py, remote).

The owner's rules pinned here: routing is app_kv-driven and OFF by default;
stages cut over owner -> MCP -> trial/free -> paying; a box job never falls
back to Cloudflare or Modal; busy, updating or unreachable means WAIT (in the
claim, then back in the queue unspent), never fail; a job runs only on box
code matching the dispatcher's fingerprint.
"""

import inspect
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import box_routing  # noqa: E402
import config  # noqa: E402
import db as dbx  # noqa: E402
import main  # noqa: E402
import remote  # noqa: E402
import version  # noqa: E402

BOX = "https://box.example"
ON = {"enabled": True, "url": BOX, "owner_user_ids": [60],
      "stages": {"owner": True, "mcp": False, "trial": False,
                 "free": False, "paying": False}}


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise remote.requests.HTTPError(str(self.status_code))


class _Ledger:
    events = []

    def run(self, fn, *args, **_kwargs):
        _Ledger.events.append((getattr(fn, "__name__", fn), args))
        if fn is dbx.get_job:
            return None
        if fn is dbx.lease_is_current:
            return True
        return True

    def reset(self):
        pass


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("BOX_ROUTE_ALL", raising=False)
    monkeypatch.delenv("BOX_EXECUTOR_URL", raising=False)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL",
                        "https://cloudflare.example")
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_PERCENT", 100)
    monkeypatch.setattr(config, "CLOUDFLARE_MODAL_FALLBACK", True)
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "WORKER_ROLE", "worker")
    monkeypatch.setattr(version, "code_version", lambda: "v-mine")
    _Ledger.events = []
    monkeypatch.setattr(remote.dbx, "Db", _Ledger)
    monkeypatch.setattr(remote.dbx, "mark_remote_owned", lambda _id: True)
    monkeypatch.setattr(remote.dbx, "remote_launch_recorded",
                        lambda _id: None)
    monkeypatch.setattr(remote.dbx, "unmark_remote_owned", lambda _id: None)
    monkeypatch.setattr(remote.time, "sleep", lambda _s: None)
    monkeypatch.setattr(remote, "owner_alarm", lambda *a, **k: True)
    monkeypatch.setattr(box_routing, "_user_row", lambda uid: {
        "is_subscribed": 1 if uid == 7 else 0,
        "plan": "ai" if uid == 7 else "free", "trial_status": None,
        "trial_live": False})
    remote._health_cache.clear()
    box_routing.set_routing_for_tests(ON)
    yield
    box_routing.set_routing_for_tests({})


def _job(job_id=11, jtype="preview", user=60, **payload):
    return {"id": job_id, "type": jtype, "project_id": 5, "user_id": user,
            "attempts": 1, "total_claims": 1, "payload": dict(payload)}


def _health(**extra):
    body = {"status": "ok", "provider": "box", "source_version": "v-mine",
            "code_version": "v-mine", "code_versions": ["v-mine"],
            "features": ["fence", "skew_guard", "box_admission"]}
    body.update(extra)
    return _Response(body)


def test_routing_is_off_without_the_app_kv_row():
    box_routing.set_routing_for_tests({})
    assert not box_routing.enabled()
    assert remote.desired_execution_provider(_job()) != "box"


def test_stages_cut_over_in_order():
    assert remote.desired_execution_provider(_job(user=60)) == "box"
    assert remote.desired_execution_provider(_job(user=7)) != "box"
    assert remote.desired_execution_provider(
        _job(user=8, jtype="mcp_tool")) != "box"
    box_routing.set_routing_for_tests(dict(ON, stages={
        "owner": True, "mcp": True}))
    assert remote.desired_execution_provider(
        _job(user=8, jtype="mcp_tool")) == "box"
    assert remote.desired_execution_provider(_job(user=8)) != "box"
    box_routing.set_routing_for_tests(dict(ON, stages={
        "owner": True, "mcp": True, "free": True, "trial": True}))
    assert remote.desired_execution_provider(_job(user=8)) == "box"
    assert remote.desired_execution_provider(_job(user=7)) != "box"
    box_routing.set_routing_for_tests(dict(ON, stages={
        s: True for s in box_routing.STAGES}))
    assert remote.desired_execution_provider(_job(user=7)) == "box"


def test_a_box_stamp_is_immutable():
    box_routing.set_routing_for_tests({})
    job = _job(execution_provider="box")
    assert remote.desired_execution_provider(job) == "box"


def test_priority_classes():
    assert box_routing.priority_class({"user_id": 60}) == "owner"
    assert box_routing.priority_class({"user_id": 7}) == "customer"
    assert box_routing.priority_class({"user_id": 8}) == "trial"


def test_box_call_ids_name_their_target():
    job = _job(execution_provider="box")
    call_id = remote._cloudflare_call_id(job)
    assert call_id.startswith("bx-preview-p5-")
    assert remote._call_target(call_id) == "box"
    assert remote._call_url(call_id) == BOX
    cf = remote._cloudflare_call_id(_job(execution_provider="cloudflare"))
    assert cf.startswith("cf-") and remote._call_target(cf) != "box"


def _wire(monkeypatch, posts, health=None):
    seen = {"posts": [], "gets": []}

    def post(url, json=None, headers=None, timeout=None):
        assert url.startswith(BOX), f"a box job reached {url}"
        seen["posts"].append((url, json))
        return posts.pop(0)

    def get(url, headers=None, timeout=None):
        assert url.startswith(BOX), f"a box job reached {url}"
        seen["gets"].append(url)
        if url.endswith("/health"):
            return (health.pop(0) if isinstance(health, list) and health
                    else _health())
        return _Response({"status": "missing"}, 404)

    monkeypatch.setattr(remote.requests, "post", post)
    monkeypatch.setattr(remote.requests, "get", get)
    monkeypatch.setattr(remote, "_run_modal", lambda *a, **k: (
        _ for _ in ()).throw(AssertionError("Modal fallback on a box job")))
    return seen


REFUSAL = {"error": "Box is at capacity: no free interactive slot within "
           "150s; nothing ran", "safe_to_fallback": True, "box_wait": True,
           "capacity_unavailable": True, "retryable": True,
           "failure": {"kind": "provider_capacity_unavailable",
                       "retryable": True}}
DONE = {"result": {"ok": True}, "job_completed": True}


def test_busy_box_means_wait_never_fallback(monkeypatch):
    seen = _wire(monkeypatch, [_Response(REFUSAL, 429),
                               _Response(REFUSAL, 429), _Response(DONE)])
    job = _job(execution_provider="box")
    out = remote._run_remote(job)
    assert out == {"ok": True, "_remote_job_completed": True} or \
        out.get("ok") is True or out.get("_remote_job_completed")
    assert len(seen["posts"]) == 3
    body = seen["posts"][0][1]
    assert body["priority_class"] == "owner"
    assert body["max_wait_s"] == config.BOX_ADMISSION_WAIT_S
    assert body["job"]["dispatcher_source"] == "v-mine"


def test_release_skew_holds_until_the_box_runs_this_code(monkeypatch):
    stale = _health(source_version="v-old", code_version="v-old",
                    code_versions=["v-old"])
    seen = _wire(monkeypatch, [_Response(DONE)], health=[stale, stale])
    monkeypatch.setattr(remote, "_BOX_HEALTH_CACHE_S", 0.0)
    job = _job(execution_provider="box")
    remote._run_remote(job)
    assert sum(1 for g in seen["gets"] if g.endswith("/health")) == 3
    assert len(seen["posts"]) == 1           # launched only once it matched


def test_unreachable_box_waits(monkeypatch):
    calls = {"n": 0}
    good = _wire(monkeypatch, [_Response(DONE)])

    def get(url, headers=None, timeout=None):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise remote.requests.ConnectionError("box rebooting")
        return _health()

    monkeypatch.setattr(remote.requests, "get", get)
    remote._run_remote(_job(execution_provider="box"))
    assert calls["n"] == 3 and len(good["posts"]) == 1


def test_queue_job_returns_to_the_queue_after_its_in_claim_window(
        monkeypatch):
    monkeypatch.setattr(config, "BOX_INCLAIM_WAIT_S", 0.0)
    _wire(monkeypatch, [_Response(REFUSAL, 429)])
    with pytest.raises(remote.BoxWaiting):
        remote._run_remote(_job(execution_provider="box"))


def test_process_one_defers_a_waiting_box_job_unspent(monkeypatch):
    calls = []

    class _Db:
        def run(self, fn, *args, **_k):
            calls.append(getattr(fn, "__name__", fn))
            return True

    monkeypatch.setattr(main.remote, "stamp_execution_provider",
                        lambda db, job: "box")

    def runner(db, job):
        raise remote.BoxWaiting("no room; nothing ran")

    monkeypatch.setattr(main, "_runner_for_job",
                        lambda job: (runner, "redesign"))
    monkeypatch.setattr(main.dbx, "mark_remote_dispatch", lambda *a: None)
    monkeypatch.setattr(main.dbx, "mark_settling", lambda *a: None)
    main.process_one(_Db(), dict(_job(), created_at=None))
    assert "defer_box_wait" in calls
    assert "finish_job" not in calls and "requeue_job" not in calls


def test_waiting_never_exhausts_the_claim_ceiling():
    assert "BOX_WAIT_CLAIMS_SQL" in inspect.getsource(dbx.claim_job)
    assert "BOX_WAIT_CLAIMS_SQL" in inspect.getsource(dbx.fail_ceilinged_jobs)
    assert "box_wait_claims" in inspect.getsource(dbx.defer_box_wait)
    assert "box_wait_claims" in inspect.getsource(dbx.release_unlaunched_jobs)


def test_children_inside_the_box_route_back_to_admission(monkeypatch):
    monkeypatch.setenv("BOX_ROUTE_ALL", "1")
    monkeypatch.setenv("BOX_EXECUTOR_URL", "http://172.30.0.1:8090")
    box_routing.set_routing_for_tests({})
    child = {"id": None, "type": "frames", "project_id": 5, "user_id": 8,
             "attempts": 0, "payload": {}}
    assert remote.desired_execution_provider(child) == "box"
    assert remote.frames_available() is True
    assert box_routing.url() == "http://172.30.0.1:8090"
