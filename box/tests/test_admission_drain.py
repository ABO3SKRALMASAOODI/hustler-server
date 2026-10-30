"""box/admission.py, R1 chaos (30 Oct 2026): a drain stops NEW work only.

A drain (maint.sh reboot, a planned admission restart) waits for the box to
go idle. It refused every call, synchronous children included, so a parent
already running when the drain began (an MCP look_at waiting on its frames
child) could not finish: its child waited 180 s and failed, and the tool
answered the customer's agent with an error. Children of running calls are
now admitted during a drain; top-level work is still refused (nothing ran).

Run locally: python -m pytest box/tests
"""

import importlib.util
import json
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


@pytest.fixture
def adm(tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / "adm.sqlite"))
    monkeypatch.setenv("BOX_RELEASE", str(tmp_path / "release.json"))
    monkeypatch.setenv("BOX_STEAL_LOG", str(tmp_path / "steal.log"))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_ENV_FILE", str(tmp_path / "executor.env"))
    monkeypatch.setenv("BOX_CONFIG", str(tmp_path / "box.json"))
    spec = importlib.util.spec_from_file_location(
        f"box_r1d_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                        "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "mem_available_gib", lambda: 20.0)
    monkeypatch.setattr(module, "docker_state", lambda _c: (True, "t0"))
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    monkeypatch.setattr(module, "disk_report",
                        lambda path=None: {"free_gb": 80, "total_gb": 144})
    monkeypatch.setattr(module, "container_health", lambda port, timeout=3.0:
                        (200, {"status": "ok", "role": "executor",
                               "code_version": "v1"}))
    return module


def _box(adm):
    raw = {"owner_user_ids": [60], "disk_min_free_gib": 0, "slots": [
        {"name": "i1", "lane": "interactive", "port": 9101, "cpus": 2},
        {"name": "look1", "lane": "interactive", "port": 9107, "cpus": 2,
         "quick": True}]}
    box = adm.Box(adm.Config(raw), adm.EnvSecret("/nonexistent"), "tok")
    for slot in box.slots:
        slot.ready, slot.release = True, "v1"
    return box


class _Conn:
    def __init__(self, *a, **k):
        pass

    def connect(self):
        pass

    def request(self, *a, **k):
        pass

    def getresponse(self):
        class R:
            status = 200

            def read(self):
                return json.dumps({"result": {"frames": 3}}).encode()
        return R()

    def close(self):
        pass


def test_a_running_calls_child_is_admitted_during_a_drain(adm, monkeypatch):
    box = _box(adm)
    box.set_draining(True)
    monkeypatch.setattr(adm.http.client, "HTTPConnection", _Conn)
    code, body = box.execute("interactive", "bx-frames-child-0001", {
        "job": {"id": None, "type": "frames", "project_id": 7,
                "total_claims": None, "dispatcher_source": "v1"},
        "launch_id": "launch-aaaa", "max_wait_s": 5})
    assert code == 200 and body["result"]["frames"] == 3
    assert box.get("bx-frames-child-0001")["slot"] == "look1"


def test_new_top_level_work_is_still_refused_with_proof(adm):
    box = _box(adm)
    box.set_draining(True)
    code, body = box.execute("interactive", "bx-preview-p7-aaaa", {
        "job": {"id": 5, "type": "preview", "project_id": 7,
                "total_claims": 1, "user_id": 9, "dispatcher_source": "v1"},
        "launch_id": "launch-bbbb", "max_wait_s": 5})
    assert code == 429
    assert body["box_wait"] is True and "draining" in body["error"]


def test_a_drain_assigns_only_children(adm):
    box = _box(adm)
    box.set_draining(True)
    t = time.time()
    for call_id, child in (("top", False), ("kid", True)):
        box.put({"call_id": call_id, "lane": "interactive",
                 "job_type": "preview" if not child else "frames",
                 "status": "submitted", "slot": None,
                 "job": json.dumps({"id": None if child else 1,
                                    "type": "frames" if child else "preview",
                                    "project_id": 7,
                                    "total_claims": None if child else 1}),
                 "launch_id": "launch-x", "read_only": 0,
                 "priority": "customer", "user_id": 9, "envelope": None,
                 "error": None, "created_at": t, "active_until": t + 300,
                 "started_at": None, "container_started": None,
                 "disconnected_at": None, "terminal_at": None,
                 "abandon_reason": None})
        box.waiting[call_id] = {"call_id": call_id, "lane": "interactive",
                                "priority": "customer", "enqueued": t,
                                "slot": None, "child": child,
                                "job_type": "frames" if child else "preview",
                                "source": "v1", "affinity": None}
    with box.lock:
        box._assign()
    assert box.waiting["kid"]["slot"] and box.waiting["top"]["slot"] is None


def test_a_running_studio_turns_render_is_admitted_during_a_drain(
        adm, monkeypatch):
    """X1, 30 Oct: the drain never reached idle because an agent turn
    waited on its own preview, which the drain refused."""
    box = _box(adm)
    t = time.time()
    box.put({"call_id": "bx-agent_turn-abc", "lane": "interactive",
             "job_type": "agent_turn", "status": "running", "slot": "i1",
             "job": json.dumps({"id": 900, "type": "agent_turn",
                                "project_id": 7, "total_claims": 1}),
             "launch_id": "launch-t", "read_only": 0, "priority": "customer",
             "user_id": 9, "envelope": None, "error": None,
             "created_at": t, "active_until": t + 3000, "started_at": t,
             "container_started": "t0", "disconnected_at": None,
             "terminal_at": None, "abandon_reason": None})
    box.set_draining(True)
    box.slots[0].ready = True
    monkeypatch.setattr(adm.http.client, "HTTPConnection", _Conn)

    def launch(call_id, parent):
        return box.execute("interactive", call_id, {
            "job": {"id": 901, "type": "preview", "project_id": 7,
                    "total_claims": 1, "user_id": 9,
                    "dispatcher_source": "v1",
                    "payload": {"agent_job_id": parent}},
            "launch_id": "launch-p", "max_wait_s": 3})
    code, _ = launch("bx-preview-p7-orphan", 12345)
    assert code == 429                     # nobody waits for it: refused
    # Its parent turn holds i1; the quick slot serves children only, so
    # free a second ordinary slot for the render.
    box.slots.append(adm.Slot({"name": "i2", "lane": "interactive",
                               "port": 9102, "cpus": 2}))
    box.by_name["i2"] = box.slots[-1]
    box.slots[-1].ready, box.slots[-1].release = True, "v1"
    code, body = launch("bx-preview-p7-dep", 900)
    assert code == 200
    assert box.get("bx-preview-p7-dep")["slot"] == "i2"
