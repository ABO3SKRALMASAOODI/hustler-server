"""box/admission.py: one box for every customer plus 1-2 podcast runs (X1).

The owner factory's customer reserve (owner_leave_free_by_lane), its memory
budget and headroom, the class CPU weights and the slot plan in
box/host/box.json. Run locally: python -m pytest box/tests (box/ is not part
of the worker suite; a box-only push releases nothing but the box itself).
"""

import importlib.util
import json
import os
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
PLAN = os.path.join(BOX_DIR, "host", "box.json")


@pytest.fixture
def adm(tmp_path, monkeypatch):
    for key, name in (("BOX_STATE_DB", "admission.sqlite"),
                      ("BOX_RELEASE", "release.json"),
                      ("BOX_STEAL_LOG", "steal.log"),
                      ("BOX_ENV_FILE", "executor.env"),
                      ("BOX_CONFIG", "box.json")):
        monkeypatch.setenv(key, str(tmp_path / name))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        f"box_admission_{time.monotonic_ns()}",
        os.path.join(BOX_DIR, "admission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._mem = {"gib": 20.0}
    monkeypatch.setattr(module, "mem_available_gib",
                        lambda: module._mem["gib"])
    monkeypatch.setattr(module, "docker_state", lambda _c: (True, "t0"))
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    return module


def _box(adm, slots=None, **overrides):
    raw = {"owner_user_ids": [60], "owner_max_cpus": 99,
           "owner_max_slots": 16, "mem_budget_gib": 44.0,
           "disk_min_free_gib": 0,
           "owner_max_by_lane": {"interactive": 4, "batch": 2},
           "owner_leave_free_by_lane": {"interactive": 2, "batch": 3},
           "slots": slots or (
               [{"name": f"i{n}", "lane": "interactive", "port": 9100 + n,
                 "cpus": 2} for n in range(1, 9)]
               + [{"name": "look1", "lane": "interactive", "port": 9120,
                   "cpus": 2, "quick": True},
                  {"name": "look2", "lane": "interactive", "port": 9121,
                   "cpus": 2, "quick": True}]
               + [{"name": f"b{n}", "lane": "batch", "port": 9130 + n,
                   "cpus": 4} for n in range(1, 7)]
               + [{"name": "bquick1", "lane": "batch", "port": 9140,
                   "cpus": 3, "quick": True}])}
    raw.update(overrides)
    box = adm.Box(adm.Config(raw), adm.EnvSecret("/nonexistent"), "tok")
    for slot in box.slots:
        slot.ready, slot.release = True, "v1"
    return box


_SEQ = [0]


def _submit(box, lane="interactive", priority="owner", jtype="preview",
            child=False):
    _SEQ[0] += 1
    call_id = f"bx-{jtype}-p{_SEQ[0]}-{priority}"
    t = 1000.0 + _SEQ[0]
    box.put({"call_id": call_id, "lane": lane, "job_type": jtype,
             "status": "submitted", "slot": None,
             "job": json.dumps({"id": None if child else _SEQ[0],
                                "type": jtype, "project_id": _SEQ[0],
                                "total_claims": None if child else 1}),
             "launch_id": "launch-aaaa", "read_only": 0,
             "priority": priority,
             "user_id": 60 if priority == "owner" else 5,
             "envelope": None, "error": None, "created_at": t,
             "active_until": t + 300, "started_at": None,
             "container_started": None, "disconnected_at": None,
             "terminal_at": None, "abandon_reason": None})
    box.waiting[call_id] = {"call_id": call_id, "lane": lane,
                            "priority": priority, "enqueued": t,
                            "slot": None, "job_type": jtype, "child": child,
                            "source": "v1", "affinity": None}
    return box.waiting[call_id]


def _assign(box):
    with box.lock:
        box._assign()


def _finish(box, waiter):
    with box.lock:
        rec = box.get(waiter["call_id"])
        box.terminalize(rec, "done", {"ok": True})


def _ordinary_free(box, lane):
    return sum(1 for s in box._free_slots(lane) if not s.quick)


def test_two_podcasts_saturate_their_share_and_customers_still_start(adm):
    box = _box(adm)
    # Two podcasts' editors ask for far more than their share.
    owner = [_submit(box) for _ in range(8)]
    _assign(box)
    assert sum(1 for w in owner if w["slot"]) == 4      # the owner cap
    # The reserve is untouched: four ordinary slots wait for customers.
    assert _ordinary_free(box, "interactive") == 4
    customers = [_submit(box, priority="customer") for _ in range(4)]
    _assign(box)
    assert all(c["slot"] for c in customers)
    assert all(not box.by_name[c["slot"]].quick for c in customers)


def test_customers_in_flight_shrink_the_factory_share(adm):
    box = _box(adm)
    customers = [_submit(box, priority="customer") for _ in range(3)]
    _assign(box)
    assert all(c["slot"] for c in customers)
    owner = [_submit(box) for _ in range(6)]
    _assign(box)
    # 8 ordinary - 3 customers - 2 reserved = 3 for the factory, not its 4.
    assert sum(1 for w in owner if w["slot"]) == 3
    assert _ordinary_free(box, "interactive") == 2
    late = _submit(box, priority="customer")
    _assign(box)
    assert late["slot"] and not box.by_name[late["slot"]].quick
    # As the customers finish, the factory gets its share back.
    for c in customers + [late]:
        _finish(box, c)
    _assign(box)
    assert sum(1 for w in owner if w["slot"]) == 4


def test_trial_users_use_the_reserve_too_and_rank_below_customers(adm):
    box = _box(adm)
    owner = [_submit(box) for _ in range(6)]
    _assign(box)
    trial = [_submit(box, priority="trial") for _ in range(4)]
    _assign(box)
    assert sum(1 for w in owner if w["slot"]) == 4
    assert all(t["slot"] for t in trial)


def test_owner_children_keep_the_quick_slots_but_not_the_reserve(adm):
    box = _box(adm)
    owner = [_submit(box) for _ in range(4)]
    customers = [_submit(box, priority="customer") for _ in range(2)]
    _assign(box)
    assert all(w["slot"] for w in owner + customers)
    assert _ordinary_free(box, "interactive") == 2      # exactly the reserve
    kids = [_submit(box, jtype="frames", child=True) for _ in range(3)]
    _assign(box)
    # Two take the quick slots; the third waits for one instead of taking
    # an ordinary slot a customer may need.
    placed = [k["slot"] for k in kids if k["slot"]]
    assert sorted(placed) == ["look1", "look2"]
    assert _ordinary_free(box, "interactive") == 2
    _finish(box, kids[0])
    _assign(box)
    assert kids[2]["slot"] == kids[0]["slot"]


def test_owner_children_still_use_ordinary_slots_beyond_the_reserve(adm):
    box = _box(adm)
    kids = [_submit(box, jtype="frames", child=True) for _ in range(4)]
    _assign(box)
    # Quick first, then ordinary slots while more than the reserve is free.
    assert all(k["slot"] for k in kids)
    assert {"look1", "look2"} <= {k["slot"] for k in kids}


def test_customer_children_are_not_limited_by_the_reserve(adm):
    box = _box(adm)
    owner = [_submit(box) for _ in range(4)]
    customers = [_submit(box, priority="customer") for _ in range(2)]
    _assign(box)
    kids = [_submit(box, priority="customer", jtype="frames", child=True)
            for _ in range(4)]
    _assign(box)
    assert all(k["slot"] for k in kids)
    assert all(w["slot"] for w in owner + customers)


def test_batch_reserve_for_a_customers_multi_file_upload(adm):
    box = _box(adm)
    owner = [_submit(box, lane="batch", jtype="final") for _ in range(3)]
    _assign(box)
    assert sum(1 for w in owner if w["slot"]) == 2      # batch cap 2
    # One user's 8-file upload: 4 indexes start at once beside the factory.
    uploads = [_submit(box, lane="batch", priority="customer", jtype="index")
               for _ in range(8)]
    _assign(box)
    assert sum(1 for u in uploads if u["slot"]) == 4
    assert all(not box.by_name[u["slot"]].quick for u in uploads
               if u["slot"])
    # A finishing factory final hands its slot to the upload, not to the
    # factory's third final.
    _finish(box, next(w for w in owner if w["slot"]))
    _assign(box)
    assert sum(1 for u in uploads if u["slot"]) == 5
    assert sum(1 for w in owner if w["slot"]) == 2


def test_no_reserve_configured_is_the_old_behaviour(adm):
    box = _box(adm, owner_leave_free_by_lane={})
    owner = [_submit(box) for _ in range(8)]
    _assign(box)
    assert sum(1 for w in owner if w["slot"]) == 4
    kids = [_submit(box, jtype="frames", child=True) for _ in range(6)]
    _assign(box)
    assert all(k["slot"] for k in kids)   # children may fill every slot


def test_owner_memory_budget_holds_the_factory_not_customers(adm):
    box = _box(adm, owner_mem_budget_gib=9.5,
               owner_max_by_lane={"interactive": 8, "batch": 2},
               owner_leave_free_by_lane={})
    owner = [_submit(box) for _ in range(5)]               # 3 GiB each
    _assign(box)
    assert sum(1 for w in owner if w["slot"]) == 3         # 9 <= 9.5 < 12
    customers = [_submit(box, priority="customer") for _ in range(3)]
    _assign(box)
    assert all(c["slot"] for c in customers)


def test_a_lone_owner_call_always_fits_its_own_budget(adm):
    box = _box(adm, owner_mem_budget_gib=4.0)
    final = _submit(box, lane="batch", jtype="final")      # 6.5 GiB
    _assign(box)
    assert final["slot"]


def test_owner_memory_headroom_keeps_real_memory_for_customers(adm):
    box = _box(adm, mem_reserve_gib=3.5, owner_mem_headroom_gib=3.0)
    running = _submit(box)                # the factory already renders
    _assign(box)
    assert running["slot"]
    adm._mem["gib"] = 8.0
    # Its next final needs 6.5: 8 - 6.5 < 3.5 + 3 -> the factory waits ...
    owner = _submit(box, lane="batch", jtype="final")
    _assign(box)
    assert owner["slot"] is None
    # ... while a customer's final still starts (4.75 >= 3.5).
    customer = _submit(box, lane="batch", priority="customer",
                       jtype="final")
    _assign(box)
    assert customer["slot"]
    # A light owner call still fits: 8 - 0.4 >= 6.5.
    mcp_box = _box(adm, mem_reserve_gib=3.5, owner_mem_headroom_gib=3.0,
                   slots=[{"name": "m1", "lane": "mcp", "port": 9150}])
    light = _submit(mcp_box, lane="mcp", jtype="mcp_tool")
    _assign(mcp_box)
    assert light["slot"] == "m1"


def test_class_cpu_weights_put_customers_well_above_the_factory(adm):
    assert adm.CPU_SHARES["customer"] > adm.CPU_SHARES["trial"] > \
        adm.CPU_SHARES["owner"]
    # cgroup v2 (runc): 8192 -> 532, 4096 -> 303, 1024 -> 100.
    assert adm.CPU_SHARES["customer"] >= 8 * 1024
    box = _box(adm)
    assert box.cfg.cpu_shares == adm.CPU_SHARES


def test_health_reports_the_reserve_and_the_factory_memory(adm):
    box = _box(adm, owner_mem_budget_gib=30.0, owner_mem_headroom_gib=3.0)
    _submit(box)
    _assign(box)
    owner = box.health()["owner"]
    assert owner["leave_free_by_lane"] == {"interactive": 2, "batch": 3}
    assert owner["committed_gib"] == 3.0
    assert owner["mem_budget_gib"] == 30.0
    assert owner["mem_headroom_gib"] == 3.0
    assert box.health()["cpu_shares"]["customer"] == 8192


def test_reserve_settings_reload_live(adm, tmp_path):
    box = _box(adm)
    path = tmp_path / "box.json"
    path.write_text(json.dumps({"owner_leave_free_by_lane": {}}))
    assert box.reload_config() is False
    path.write_text(json.dumps({
        "owner_leave_free_by_lane": {"interactive": 3},
        "owner_mem_budget_gib": 20, "owner_mem_headroom_gib": 2,
        "cpu_shares": {"customer": 16384}}))
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert box.reload_config() is True
    assert box.cfg.owner_leave_free_by_lane == {"interactive": 3}
    assert box.cfg.owner_mem_budget_gib == 20.0
    assert box.cfg.owner_mem_headroom_gib == 2.0
    assert box.cfg.cpu_shares["customer"] == 16384
    assert box.cfg.cpu_shares["owner"] == 1024


# ── the slot plan the box runs (box/host/box.json) ─────────────────────────
def _plan():
    with open(PLAN, encoding="utf-8") as fh:
        return json.load(fh)


def _ordinary(plan, lane):
    return [s for s in plan["slots"] if s["lane"] == lane
            and not s.get("quick")]


def test_plan_leaves_every_lane_a_customer_reserve():
    plan = _plan()
    caps = plan["owner_max_by_lane"]
    leave = plan["owner_leave_free_by_lane"]
    # 30 days of real customer concurrency (to 30 Oct 2026), per lane: the
    # p99 (batch: its p95 2 plus one; its p99 7 was one user's 8-file
    # upload, which memory, not slots, bounds). The factory at its cap
    # leaves at least that free.
    customer_p99 = {"interactive": 2, "batch": 3, "mcp": 4, "agent": 1,
                    "shorts": 1}
    for lane, need in customer_p99.items():
        n = len(_ordinary(plan, lane))
        assert caps[lane] < n, lane
        assert n - caps[lane] >= need, lane
        assert leave[lane] >= 1, lane
        assert caps[lane] + leave[lane] <= n, lane
    assert sum(caps.values()) <= plan["owner_max_slots"]


def test_plan_sizes_the_factory_for_two_podcasts():
    plan = _plan()
    caps = plan["owner_max_by_lane"]
    # Two runs: 2 coordinators + 6 editors; previews are 1 in ~13 calls.
    assert caps["interactive"] >= 4
    assert caps["mcp"] >= 6
    assert caps["batch"] >= 2
    assert plan["owner_mem_budget_gib"] < plan["mem_budget_gib"]
    assert plan["mem_budget_gib"] - plan["owner_mem_budget_gib"] >= 12


def test_plan_keeps_render_parity_cpusets_and_unique_ports():
    plan = _plan()
    ports = [s["port"] for s in plan["slots"]]
    names = [s["name"] for s in plan["slots"]]
    assert len(set(ports)) == len(ports)
    assert len(set(names)) == len(names)
    for s in plan["slots"]:
        if s.get("quick"):
            continue
        if s["lane"] == "interactive":
            assert s["cpus"] == 2.0 and len(
                _cpus(s["cpuset"])) == 2, s["name"]
        if s["lane"] == "batch":
            assert s["cpus"] == 4.0 and len(
                _cpus(s["cpuset"])) == 4, s["name"]
    # The slots of 29 Oct keep their names and ports (no slot is renamed or
    # renumbered, so none rolls for the plan change).
    first = {"interactive1": 9101, "look1": 9107, "batch1": 9109,
             "bquick1": 9112, "mcp1": 9113, "agent1": 9121,
             "shorts2": 9124}
    for name, port in first.items():
        assert dict(zip(names, ports))[name] == port
    assert plan["cpu_shares"]["customer"] > plan["cpu_shares"]["owner"]


def _cpus(text):
    out = set()
    for part in text.split(","):
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


# ── a drain lets running calls finish ──────────────────────────────────────
def test_a_drain_stops_new_work_but_children_of_running_calls_still_start(
        adm):
    box = _box(adm)
    parent = _submit(box, priority="customer")
    _assign(box)
    assert parent["slot"]
    box.set_draining(True)
    late = _submit(box, priority="customer")
    owner = _submit(box)
    child = _submit(box, priority="customer", jtype="frames", child=True)
    owner_child = _submit(box, jtype="frames", child=True)
    _assign(box)
    assert late["slot"] is None and owner["slot"] is None
    # The running parents' children run, so the box can reach idle.
    assert child["slot"] and owner_child["slot"]
    box.set_draining(False)
    _assign(box)
    assert late["slot"] and owner["slot"]


def test_a_drained_launch_refuses_top_level_calls_at_once(adm):
    box = _box(adm)
    box.set_draining(True)
    code, body = box.execute("interactive", "bx-preview-p9-zzzzzzzz", {
        "job": {"id": 9, "type": "preview", "project_id": 9,
                "total_claims": 1, "dispatcher_source": "v1"},
        "launch_id": "launch-bbbb", "timeout_s": 600})
    assert code == 429 and "draining" in body.get("error", "")


def test_plan_batch_slots_hold_a_final_as_large_as_cloudflare_did():
    # Owner finals peaked at 10.2 GiB on Cloudflare's 12 GiB batch lane.
    for s in _plan()["slots"]:
        if s["lane"] == "batch" and not s.get("quick"):
            assert s["memory"] == "12g", s["name"]



# ── light calls and the factory's real memory (L4, 30 Oct) ────────────────
def _mixed_box(adm, **overrides):
    slots = ([{"name": f"i{n}", "lane": "interactive", "port": 9100 + n,
               "cpus": 2} for n in range(1, 5)]
             + [{"name": f"b{n}", "lane": "batch", "port": 9110 + n,
                 "cpus": 4} for n in range(1, 4)]
             + [{"name": f"m{n}", "lane": "mcp", "port": 9120 + n}
                for n in range(1, 7)])
    cfg = {"light_gib": 1.0, "mem_reserve_gib": 3.5,
           "owner_leave_free_by_lane": {},
           "owner_max_by_lane": {"interactive": 4, "batch": 2, "mcp": 6}}
    cfg.update(overrides)
    return _box(adm, slots=slots, **cfg)


def test_a_customer_waiting_for_memory_never_stalls_factory_mcp_calls(adm):
    box = _mixed_box(adm)
    running = _submit(box, priority="customer")
    _assign(box)
    assert running["slot"]
    adm._mem["gib"] = 6.0
    # A customer final (6.5) cannot start: 6 - 3.25 < 3.5 ...
    final = _submit(box, lane="batch", priority="customer", jtype="final")
    preview = _submit(box, priority="owner")
    mcp = [_submit(box, lane="mcp", jtype="mcp_tool") for _ in range(3)]
    _assign(box)
    assert final["slot"] is None
    # ... it holds the factory's heavy preview back, but its light MCP
    # calls still run (6 - 0.4 >= 3.5): the agents keep working.
    assert preview["slot"] is None
    assert all(m["slot"] for m in mcp)


def test_light_calls_wait_only_for_real_memory(adm):
    box = _mixed_box(adm)
    adm._mem["gib"] = 3.6
    mcp = _submit(box, lane="mcp", priority="customer", jtype="mcp_tool")
    _assign(box)
    assert mcp["slot"] is None           # 3.6 - 0.4 < 3.5: it waits
    adm._mem["gib"] = 8.0
    _assign(box)
    assert mcp["slot"]


def test_light_calls_are_not_held_by_the_committed_estimate_budget(adm):
    box = _mixed_box(adm, mem_budget_gib=7.0)
    final = _submit(box, lane="batch", priority="customer", jtype="final")
    _assign(box)
    assert final["slot"]                 # 6.5 of 7 committed
    mcp = [_submit(box, lane="mcp", priority="customer", jtype="mcp_tool")
           for _ in range(4)]
    _assign(box)
    assert all(m["slot"] for m in mcp)   # 6.5 + 3.2 > 7, real memory fine
    heavy = _submit(box, priority="customer")
    _assign(box)
    assert heavy["slot"] is None         # a heavy call still respects it


def test_factory_headroom_counts_a_render_s_full_estimate(adm):
    box = _mixed_box(adm, owner_mem_headroom_gib=2.5)
    first = _submit(box)                  # the factory's first render
    _assign(box)
    assert first["slot"]
    # A second factory final (6.5) needs 3.5 + 2.5 + 6.5 = 12.5 GiB.
    adm._mem["gib"] = 12.0
    final = _submit(box, lane="batch", jtype="final")
    _assign(box)
    assert final["slot"] is None
    adm._mem["gib"] = 12.6
    _assign(box)
    assert final["slot"]
    # Another factory preview (3.0) needs 9.0; a customer preview only 5.0.
    adm._mem["gib"] = 8.0
    owner_preview = _submit(box)
    customer_preview = _submit(box, priority="customer")
    _assign(box)
    assert owner_preview["slot"] is None and customer_preview["slot"]


def test_the_factory_s_first_render_always_starts_on_a_quiet_box(adm):
    # 30 Oct, L4b: test containers and a migration held ~4.8 GiB outside
    # the slots; MemAvailable 10.2 on an idle box, and both factory finals
    # waited 35 minutes. The first heavy call answers to box-wide rules.
    box = _mixed_box(adm, owner_mem_headroom_gib=2.5,
                     owner_mem_budget_gib=16.0)
    adm._mem["gib"] = 10.2
    finals = [_submit(box, lane="batch", jtype="final") for _ in range(2)]
    _assign(box)
    assert finals[0]["slot"] and finals[1]["slot"] is None
    # Below the box-wide reserve even the first one waits.
    box2 = _mixed_box(adm, owner_mem_headroom_gib=2.5)
    adm._mem["gib"] = 3.0
    held = _submit(box2, lane="batch", jtype="final")
    _assign(box2)
    assert held["slot"] is None


def test_factory_budget_counts_its_heavy_calls_only(adm):
    box = _mixed_box(adm, owner_mem_budget_gib=6.0)
    mcp = [_submit(box, lane="mcp", jtype="mcp_tool") for _ in range(6)]
    _assign(box)
    assert all(m["slot"] for m in mcp)   # 4.8 GiB of light calls
    previews = [_submit(box) for _ in range(3)]
    _assign(box)
    # The light calls never count: two previews (6.0) fit, a third not.
    assert sum(1 for p in previews if p["slot"]) == 2


def test_without_light_gib_every_call_is_heavy_as_before(adm):
    box = _mixed_box(adm, light_gib=0.0)
    running = _submit(box, priority="customer")
    _assign(box)
    adm._mem["gib"] = 6.0
    final = _submit(box, lane="batch", priority="customer", jtype="final")
    mcp = _submit(box, lane="mcp", jtype="mcp_tool")
    _assign(box)
    assert final["slot"] is None and mcp["slot"] is None


def test_plan_marks_mcp_agent_and_looks_light_and_renders_heavy(adm):
    plan = _plan()
    cfg = adm.Config(plan)
    light = cfg.light_gib
    for jtype in ("mcp_tool", "agent_turn", "shorts_plan", "frames"):
        assert cfg.estimate(jtype) <= light, jtype
    for jtype in ("preview", "preview_check", "final", "index"):
        assert cfg.estimate(jtype) > light, jtype


# ── boxctl: a slot dropped from the plan leaves no idle container ──────────
def test_orphan_slot_containers_are_only_dropped_unadmitted_slots(tmp_path,
                                                                 monkeypatch):
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        f"box_boxctl_{time.monotonic_ns()}",
        os.path.join(BOX_DIR, "boxctl.py"))
    ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ctl)
    names = ["vbox-mcp1", "vbox-mcp11", "vbox-batch6", "qb-run", "qb-pg",
             "vbox-mcp12"]
    plan = {"vbox-mcp1"}
    # Admission still serves mcp12 (its restart is pending): kept.
    admitted = {"vbox-mcp1", "vbox-mcp12"}
    assert ctl.orphan_containers(names, plan, admitted) == [
        "vbox-batch6", "vbox-mcp11"]


def test_an_orphan_slot_holding_a_call_is_kept(tmp_path, monkeypatch, adm):
    box = _box(adm)
    running = _submit(box, priority="customer")
    _assign(box)
    db = os.environ["BOX_STATE_DB"]
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location(
        f"box_boxctl_{time.monotonic_ns()}",
        os.path.join(BOX_DIR, "boxctl.py"))
    ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ctl)
    assert ctl.slot_has_live_call(running["slot"], db) is True
    assert ctl.slot_has_live_call("i8", db) is False
    assert ctl.slot_has_live_call("x", str(tmp_path / "missing.db")) is True
