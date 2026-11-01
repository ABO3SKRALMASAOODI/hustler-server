"""box/admission.py, ticket #109 (1 Nov 2026): the owner factory's CPU
weight is ONE class total split across its busy containers.

Before: every call set its own container's weight from its class, so ten
factory calls (cpu.weight 100 each) together outweighed one new user's
index (trial, 303). Under contention on the 8-CPU box the index got
303 / 1303 of the CPUs, 1.8 of its 4 (job 131963: 465 CPU-s in 262 s), and
the user left before the first cut arrived. The pool keeps the factory,
however many calls it runs, below a single customer or trial call.

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
        f"box_pool_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                        "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.applied = {}
    monkeypatch.setattr(module, "docker_cpu_shares",
                        lambda c, s: module.applied.__setitem__(c, s) or True)
    return module


SLOTS = ([{"name": "batch1", "lane": "batch", "port": 9201, "cpus": 4}]
         + [{"name": f"interactive{i}", "lane": "interactive",
             "port": 9100 + i, "cpus": 2} for i in range(1, 5)]
         + [{"name": f"mcp{i}", "lane": "mcp", "port": 9150 + i, "cpus": 2}
            for i in range(1, 7)])


def _box(adm):
    raw = {"owner_user_ids": [60], "trial_user_ids": [2703],
           "disk_min_free_gib": 0, "slots": SLOTS}
    return adm.Box(adm.Config(raw), adm.EnvSecret("/nonexistent"), "tok")


def _running(box, slot, priority, user, jtype, job_id):
    t = time.time()
    lane = box.by_name[slot].lane
    with box.lock:
        box.put({"call_id": f"c-{job_id}", "lane": lane, "job_type": jtype,
                 "status": "running", "slot": slot, "priority": priority,
                 "user_id": user, "read_only": 0,
                 "job": json.dumps({"id": job_id, "type": jtype}),
                 "created_at": t, "started_at": t, "active_until": t + 600})


def _the_1528_window(box):
    """1 Nov 15:28-15:33 UTC: the new user's index beside the factory."""
    _running(box, "batch1", "trial", 2703, "index", 131963)
    for i in range(1, 5):
        _running(box, f"interactive{i}", "owner", 60, "preview", 131965 + i)
    for i in range(1, 7):
        _running(box, f"mcp{i}", "owner", 60, "mcp_tool", 131970 + i)


def _weight_share(adm, shares, slot):
    w = {n: adm.cgroup_v2_weight(v) for n, v in shares.items()}
    return w[slot] / sum(w.values())


def test_runc_weight_mapping_matches_the_box(adm):
    # Measured on the box 30 Oct (CPU_SHARES comment).
    assert [adm.cgroup_v2_weight(s) for s in (1024, 4096, 8192)] == \
        [100, 303, 532]
    for w in (1, 10, 33, 100, 303):
        assert adm.cgroup_v2_weight(adm.shares_for_weight(w)) >= w
        assert adm.cgroup_v2_weight(adm.shares_for_weight(w) - 1) < w or \
            adm.shares_for_weight(w) == 2


def test_a_new_users_index_outweighs_the_whole_factory(adm):
    box = _box(adm)
    _the_1528_window(box)
    shares = box.target_shares()
    # The old per-container weights gave the index 303 / 1303 = 23 %.
    old = {n: (4096 if n == "batch1" else 1024) for n in shares}
    assert _weight_share(adm, old, "batch1") < 0.25
    # Pooled: all ten factory calls together weigh one owner call (100),
    # so the index gets ~3/4 of a contended box: its full 4 of 8 CPUs.
    assert _weight_share(adm, shares, "batch1") > 0.7
    owner_total = sum(adm.cgroup_v2_weight(v) for n, v in shares.items()
                      if n != "batch1")
    assert owner_total <= 110
    assert shares["batch1"] == adm.CPU_SHARES["trial"]


def test_a_lone_factory_call_and_customers_keep_their_class_weight(adm):
    box = _box(adm)
    _running(box, "interactive1", "owner", 60, "preview", 1)
    _running(box, "mcp1", "customer", 7, "mcp_tool", 2)
    _running(box, "mcp2", "customer", 8, "mcp_tool", 3)
    shares = box.target_shares()
    assert shares == {"interactive1": 1024, "mcp1": 8192, "mcp2": 8192}


def test_apply_resplits_the_pool_on_every_start_and_end(adm):
    box = _box(adm)
    _running(box, "interactive1", "owner", 60, "preview", 1)
    box._apply_shares()
    assert adm.applied == {"vbox-interactive1": 1024}
    _running(box, "interactive2", "owner", 60, "preview", 2)
    box._apply_shares()
    half = adm.shares_for_weight(50)
    assert adm.applied == {"vbox-interactive1": half,
                           "vbox-interactive2": half}
    with box.lock:
        box.terminalize(box.get("c-2"), "done", {"ok": True})
    box._apply_shares()
    assert adm.applied["vbox-interactive1"] == 1024
