"""The render box's admission layer (box/admission.py) and secrets intake
(box/provision.py): priority, owner caps, child exemption, quick slots,
memory reservations, strict release matching, relaunch-after-nothing-ran and
GitHub-OIDC-only provisioning."""

import base64
import importlib.util
import json
import os
import sys
import time

import pytest

BOX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "..", "box")


def _load(name, tmp_path, monkeypatch):
    monkeypatch.setenv("BOX_STATE_DB", str(tmp_path / f"{name}.sqlite"))
    monkeypatch.setenv("BOX_RELEASE", str(tmp_path / "release.json"))
    monkeypatch.setenv("BOX_STEAL_LOG", str(tmp_path / "steal.log"))
    monkeypatch.setenv("BOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BOX_ENV_FILE", str(tmp_path / "executor.env"))
    spec = importlib.util.spec_from_file_location(
        f"box_{name}_{time.monotonic_ns()}", os.path.join(BOX_DIR,
                                                          f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def adm(tmp_path, monkeypatch):
    module = _load("admission", tmp_path, monkeypatch)
    monkeypatch.setattr(module, "mem_available_gib", lambda: 20.0)
    monkeypatch.setattr(module, "docker_state", lambda _c: (True, "t0"))
    monkeypatch.setattr(module, "docker_cpu_shares", lambda _c, _s: True)
    return module


def _box(adm, **overrides):
    raw = {"owner_user_ids": [60], "owner_max_cpus": 99,
           "owner_max_by_lane": {"interactive": 1},
           "mem_budget_gib": 17.0, "disk_min_free_gib": 0, "slots": [
               {"name": "i1", "lane": "interactive", "port": 9101,
                "cpus": 2},
               {"name": "i2", "lane": "interactive", "port": 9102,
                "cpus": 2},
               {"name": "look1", "lane": "interactive", "port": 9103,
                "cpus": 2, "quick": True},
               {"name": "m1", "lane": "mcp", "port": 9104, "cpus": 1}]}
    raw.update(overrides)
    box = adm.Box(adm.Config(raw), adm.EnvSecret("/nonexistent"), "tok")
    for slot in box.slots:
        slot.ready, slot.release = True, "v1"
    return box


def _submit(box, call_id, lane="interactive", priority="customer",
            jtype="preview", child=False, source="v1", at=None):
    t = time.time() if at is None else at
    box.put({"call_id": call_id, "lane": lane, "job_type": jtype,
             "status": "submitted", "slot": None,
             "job": json.dumps({"id": None if child else 1, "type": jtype,
                                "project_id": 7, "total_claims":
                                    None if child else 1}),
             "launch_id": "launch-aaaa", "read_only": 0,
             "priority": priority, "user_id": 60 if priority == "owner"
             else 5, "envelope": None, "error": None, "created_at": t,
             "active_until": t + 300, "started_at": None,
             "container_started": None, "disconnected_at": None,
             "terminal_at": None, "abandon_reason": None})
    box.waiting[call_id] = {"call_id": call_id, "lane": lane,
                            "priority": priority, "enqueued": t,
                            "slot": None, "job_type": jtype, "child": child,
                            "source": source, "affinity": None}
    return box.waiting[call_id]


def _assign(box):
    with box.lock:
        return box._assign()


def test_customers_are_admitted_before_the_owner_factory(adm):
    box = _box(adm)
    owner = _submit(box, "bx-preview-p1-a", priority="owner", at=1.0)
    trial = _submit(box, "bx-preview-p2-b", priority="trial", at=2.0)
    customer = _submit(box, "bx-preview-p3-c", priority="customer", at=3.0)
    _assign(box)
    # Two ordinary interactive slots: customer and trial win, owner waits.
    assert customer["slot"] and trial["slot"]
    assert owner["slot"] is None


def test_owner_cap_per_lane_holds_but_children_are_exempt(adm):
    box = _box(adm)
    first = _submit(box, "bx-preview-p1-a", priority="owner", at=1.0)
    second = _submit(box, "bx-preview-p1-b", priority="owner", at=2.0)
    child = _submit(box, "bx-frames-c", priority="owner", jtype="frames",
                    child=True, at=3.0)
    _assign(box)
    assert first["slot"] in {"i1", "i2"}
    assert second["slot"] is None          # owner_max_by_lane interactive=1
    # A parent's child never deadlocks on the cap, and takes the quick slot.
    assert child["slot"] == "look1"


def test_quick_slots_serve_only_synchronous_children(adm):
    box = _box(adm)
    a = _submit(box, "bx-preview-p1-a", at=1.0)
    b = _submit(box, "bx-preview-p2-b", at=2.0)
    c = _submit(box, "bx-preview-p3-c", at=3.0)
    _assign(box)
    assert {a["slot"], b["slot"]} == {"i1", "i2"}
    assert c["slot"] is None               # look1 is reserved for children


def test_memory_reservation_blocks_lower_classes(adm):
    box = _box(adm, mem_budget_gib=4.0)
    first = _submit(box, "bx-preview-p1-a", at=1.0)       # 3 GiB
    _assign(box)
    assert first["slot"]
    blocked = _submit(box, "bx-preview-p2-b", at=2.0)     # would exceed 4
    owner_small = _submit(box, "bx-mcp-p3-c", lane="mcp", priority="owner",
                          jtype="mcp_tool", at=3.0)      # 0.8 would fit
    _assign(box)
    assert blocked["slot"] is None
    # The owner factory may not jump a customer that waits on memory.
    assert owner_small["slot"] is None


def test_strict_release_matching(adm):
    box = _box(adm)
    box.by_name["i1"].release = "v2"
    new = _submit(box, "bx-preview-p1-a", source="v2", at=1.0)
    old = _submit(box, "bx-preview-p2-b", source="v1", at=2.0)
    stranger = _submit(box, "bx-preview-p3-c", source="v9", at=3.0)
    _assign(box)
    assert new["slot"] == "i1" and old["slot"] == "i2"
    assert stranger["slot"] is None
    assert "v9" not in box.lane_releases("interactive")


def test_relaunch_allowed_only_after_nothing_ran(adm):
    box = _box(adm)
    env = adm.refusal_envelope("preview", "restart before /run",
                               capacity=False)
    box.put({"call_id": "bx-preview-p1-a", "lane": "interactive",
             "job_type": "preview", "status": "failed", "slot": None,
             "job": "{}", "launch_id": "launch-old1", "read_only": 0,
             "priority": "customer", "user_id": 5,
             "envelope": json.dumps(env), "error": env["error"],
             "created_at": 1.0, "active_until": 1.0, "started_at": None,
             "container_started": None, "disconnected_at": None,
             "terminal_at": 1.0, "abandon_reason": None})
    job = {"id": 1, "type": "preview", "project_id": 1, "total_claims": 1}
    # The same (fenced/lost) launch id gets its stored refusal back.
    code, body = box.execute("interactive", "bx-preview-p1-a", {
        "job": job, "launch_id": "launch-old1", "max_wait_s": 0})
    assert code == 200 and body.get("box_wait")
    # A new launch of the same claim is accepted again (and here waits).
    code, body = box.execute("interactive", "bx-preview-p1-a", {
        "job": dict(job, dispatcher_source="v-none"),
        "launch_id": "launch-new2", "max_wait_s": 0})
    assert code == 429 and body["box_wait"] and body.get("rollout_pending")


def test_execution_lease_starts_when_a_slot_takes_the_call(adm, monkeypatch):
    box = _box(adm)
    monkeypatch.setattr(adm, "container_health", lambda port, timeout=3.0: (
        200, {"status": "ok", "role": "executor", "code_version": "v1"}))

    class _Conn:
        def __init__(self, *a, **k):
            pass

        def connect(self):
            pass

        def request(self, *a, **k):
            rec = box.get("bx-preview-p1-a")
            assert rec["active_until"] > time.time() + 3000

        def getresponse(self):
            class R:
                status = 200

                def read(self):
                    return json.dumps({"result": {"ok": True},
                                       "job_completed": True}).encode()
            return R()

        def close(self):
            pass

    monkeypatch.setattr(adm.http.client, "HTTPConnection", _Conn)
    code, body = box.execute("interactive", "bx-preview-p1-a", {
        "job": {"id": 1, "type": "preview", "project_id": 1,
                "total_claims": 1, "dispatcher_source": "v1"},
        "launch_id": "launch-aaaa", "timeout_s": 3600})
    assert code == 200 and body["job_completed"] is True
    assert box.get("bx-preview-p1-a")["status"] == "done"


def test_health_reports_capacity_steal_memory_and_releases(adm):
    box = _box(adm)
    health = box.health()
    assert health["provider"] == "box"
    assert health["code_versions"] == ["v1"]
    assert health["slots"]["free"] == 4
    assert "committed_gib" in health["memory"]
    assert "depth" in health["queue"] and "oldest_wait_s" in health["queue"]
    assert "last_60s" in health["cpu"]


# ── provisioning: GitHub OIDC only ─────────────────────────────────────────
def _b64(data):
    raw = data if isinstance(data, bytes) else json.dumps(data).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@pytest.fixture
def prov(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    return _load("provision", tmp_path, monkeypatch)


def _token(prov, key, **claims):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    now = time.time()
    body = {"iss": prov.ISSUER, "aud": "valmera-box",
            "repository": prov.REPOSITORY, "ref": "refs/heads/main",
            "event_name": "push", "jti": f"j{time.monotonic_ns()}",
            "job_workflow_ref": (f"{prov.REPOSITORY}/{prov.WORKFLOW_PATH}"
                                 "@refs/heads/main"),
            "nbf": now - 5, "iat": now - 5, "exp": now + 300}
    body.update(claims)
    signing = f"{_b64({'alg': 'RS256', 'kid': 'k1'})}.{_b64(body)}"
    sig = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing}.{_b64(sig)}"


def _rsa():
    from cryptography.hazmat.primitives.asymmetric import rsa
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_provision_accepts_only_this_repos_main_workflow(prov):
    key = _rsa()
    keys = {"k1": key.public_key()}
    assert prov.verify_token(_token(prov, key), keys=keys)["ref"]
    for bad in ({"repository": "someone/fork"}, {"ref": "refs/heads/x"},
                {"aud": "other"}, {"event_name": "pull_request"},
                {"job_workflow_ref": "ABO3SKRALMASAOODI/hustler-server/"
                 ".github/workflows/evil.yml@refs/heads/main"},
                {"exp": time.time() - 1}):
        with pytest.raises(ValueError):
            prov.verify_token(_token(prov, key, **bad), keys=keys)
    other = _rsa()
    with pytest.raises(ValueError, match="bad signature"):
        prov.verify_token(_token(prov, other), keys=keys)


def test_provision_token_is_single_use(prov):
    key = _rsa()
    claims = prov.verify_token(_token(prov, key, jti="once"),
                               keys={"k1": key.public_key()})
    prov.claim_jti(claims)
    with pytest.raises(ValueError, match="already used"):
        prov.claim_jti(claims)


def test_provision_writes_only_listed_names_private(prov, tmp_path):
    values = {n: f"value-{n}" for n in prov.REQUIRED}
    values["NOT_ALLOWED"] = "x"
    assert prov.write_env({k: v for k, v in values.items()
                           if k in prov.ALLOWED}) is True
    path = tmp_path / "executor.env"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    text = path.read_text()
    assert "NOT_ALLOWED" not in text and "DATABASE_URL=value-" in text
    assert (tmp_path / "env-changed").exists()
    with pytest.raises(ValueError, match="missing required"):
        prov.write_env({"DATABASE_URL": "x"})
