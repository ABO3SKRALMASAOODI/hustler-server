"""box/admission.py, R1 chaos (30 Oct 2026): a slot container the kernel's
OOM killer ended is a memory verdict, not "safe to retry" on the same slot.

Before: every container exit was "Box lost the executor ... it is safe to
retry" (transient_infrastructure), so an out-of-memory preview was replayed
with the identical graph on the identical 5 GiB slot, then the customer was
told our service had a problem. Now the exit is checked against Docker's
``oom`` event: renders get ``executor_memory`` (the dispatcher re-runs a
preview once on the 10 GiB batch lane; a final states its limit), other
types keep their retry, an unknown-outcome mutation stays unknown.

Run locally: python -m pytest box/tests (box/ is not part of the worker
suite; a box-only push releases nothing but the box itself).
"""

import importlib.util
import json
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _load(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / "adm.sqlite"))
    monkeypatch.setenv("BOX_RELEASE", str(tmp_path / "release.json"))
    monkeypatch.setenv("BOX_STEAL_LOG", str(tmp_path / "steal.log"))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_ENV_FILE", str(tmp_path / "executor.env"))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"box_r1_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                       "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def adm(tmp_path, monkeypatch):
    module = _load(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "mem_available_gib", lambda: 20.0)
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    return module


def _box(adm):
    raw = {"owner_user_ids": [60], "disk_min_free_gib": 0, "slots": [
        {"name": "i1", "lane": "interactive", "port": 9101, "cpus": 2,
         "memory": "5g"},
        {"name": "b1", "lane": "batch", "port": 9109, "cpus": 4,
         "memory": "10g"},
        {"name": "m1", "lane": "mcp", "port": 9113, "cpus": 2,
         "memory": "2g"}]}
    box = adm.Box(adm.Config(raw), adm.EnvSecret("/nonexistent"), "tok")
    for slot in box.slots:
        slot.ready, slot.release = True, "v1"
    return box


def _unknown_call(box, call_id, jtype, slot, read_only=False):
    t = time.time()
    box.put({"call_id": call_id, "lane": box.by_name[slot].lane,
             "job_type": jtype, "status": "unknown", "slot": slot,
             "job": json.dumps({"id": 1, "type": jtype, "project_id": 7,
                                "total_claims": 1}),
             "launch_id": "launch-aaaa", "read_only": 1 if read_only else 0,
             "priority": "customer", "user_id": 5, "envelope": None,
             "error": "RemoteDisconnected", "created_at": t - 60,
             "active_until": t + 3000, "started_at": t - 50,
             "container_started": "t0", "disconnected_at": t,
             "terminal_at": None, "abandon_reason": None})


def _died(adm, monkeypatch, oom):
    # The unless-stopped restart already happened: new StartedAt.
    monkeypatch.setattr(adm, "docker_state", lambda _c: (True, "t1"))
    seen = []

    def events(container, since):
        seen.append((container, since))
        return oom
    monkeypatch.setattr(adm, "docker_oom_since", events)
    return seen


@pytest.mark.parametrize("jtype,slot", (("preview", "i1"), ("final", "b1"),
                                         ("preview_check", "i1")))
def test_an_oom_killed_render_is_a_memory_verdict(adm, monkeypatch, jtype,
                                                  slot):
    box = _box(adm)
    seen = _died(adm, monkeypatch, True)
    _unknown_call(box, f"bx-{jtype}-x1", jtype, slot)
    rec = box.status(f"bx-{jtype}-x1")
    assert rec["status"] == "failed"
    env = json.loads(rec["envelope"])
    assert env["retryable"] is False and env["oom"] is True
    assert env["failure"]["kind"] == "executor_memory"
    assert "out of memory" in env["error"]
    assert box.by_name[slot].memory in env["error"]
    # Asked about THIS container since the call started on it.
    assert seen and seen[0][0] == f"vbox-{slot}"


def test_a_container_exit_without_an_oom_event_stays_a_safe_retry(
        adm, monkeypatch):
    box = _box(adm)
    _died(adm, monkeypatch, False)
    _unknown_call(box, "bx-preview-x2", "preview", "i1")
    env = json.loads(box.status("bx-preview-x2")["envelope"])
    assert env["failure"]["kind"] == "transient_infrastructure"
    assert env["retryable"] is True
    assert "Box container exited during preview" in env["error"]


def test_unknown_docker_events_never_guess_memory(adm, monkeypatch):
    box = _box(adm)
    _died(adm, monkeypatch, None)
    _unknown_call(box, "bx-final-x3", "final", "b1")
    env = json.loads(box.status("bx-final-x3")["envelope"])
    assert env["failure"]["kind"] == "transient_infrastructure"


def test_an_oom_killed_index_keeps_its_retry_and_says_why(adm, monkeypatch):
    box = _box(adm)
    _died(adm, monkeypatch, True)
    _unknown_call(box, "bx-index-x4", "index", "b1")
    env = json.loads(box.status("bx-index-x4")["envelope"])
    assert env["failure"]["kind"] == "transient_infrastructure"
    assert "out of memory" in env["error"]


def test_an_oom_killed_mutation_stays_outcome_unknown(adm, monkeypatch):
    box = _box(adm)
    _died(adm, monkeypatch, True)
    _unknown_call(box, "bx-mcp-p7-x5", "mcp_tool", "m1", read_only=False)
    env = json.loads(box.status("bx-mcp-p7-x5")["envelope"])
    assert env["failure"]["kind"] == "outcome_unknown"
    assert env["retryable"] is False


def test_the_docker_events_query_is_bounded_and_scoped(adm, monkeypatch):
    calls = []

    class _Out:
        returncode = 0
        stdout = "1793350000\n"

    def run(args, **kw):
        calls.append((args, kw))
        return _Out()
    monkeypatch.setattr(adm.subprocess, "run", run)
    assert adm.docker_oom_since("vbox-i1", 1000.0) is True
    args, kw = calls[0]
    assert args[:2] == ["docker", "events"]
    assert "--until" in args and kw["timeout"] <= 15
    assert "container=vbox-i1" in args and "event=oom" in args
    _Out.stdout = ""
    assert adm.docker_oom_since("vbox-i1", 1000.0) is False
    _Out.returncode = 1
    assert adm.docker_oom_since("vbox-i1", 1000.0) is None
