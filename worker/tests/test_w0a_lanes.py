"""Track 0A: lanes, routing, images and the dark vision lane.

The `sidecar` kind is routed to the batch lane on its own shard slots; the
ValmeraVision lane exists as code but nothing deploys or probes it; every
model is pinned and licence-checked; R2 lifecycle covers the new prefixes.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pytest

import config
import remote

WORKER = Path(__file__).resolve().parents[1]
ROOT = WORKER.parent


# ── config and routing ──────────────────────────────────────────────────

def test_sidecar_is_a_synchronous_cloudflare_type_with_its_own_timeout():
    assert "sidecar" in config.CLOUDFLARE_EXECUTOR_TYPES
    assert "sidecar" in config.CLOUDFLARE_SYNCHRONOUS_TYPES
    assert config.execution_class_for("sidecar") == "heavy_media"
    t = config.executor_timeout_for("sidecar")
    assert t == 900 and t < config.EXECUTOR_REQUEST_TIMEOUT_S
    # the MCP lease floor follows the longest synchronous child (stems);
    # sidecar must not raise it
    assert config.cloudflare_timeout_for("mcp_tool") == 1800
    assert config.VISION_LANE_ENABLED is False
    assert config.SIDECAR_PREFETCH is False and config.YUNET_KEEPOUT is False
    assert config.SIDECAR_SHARDS == 3


def test_sidecar_routes_to_batch_until_the_vision_lane_is_enabled(
        monkeypatch):
    assert remote._cloudflare_lane("sidecar") == "batch"
    monkeypatch.setattr(config, "VISION_LANE_ENABLED", True)
    assert remote._cloudflare_lane("sidecar") == "vision"
    assert remote._cloudflare_lane("matte") == "batch"


def test_sidecar_available_only_through_cloudflare(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", False)
    assert remote.sidecar_available() is False
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://cf.test")
    assert remote.sidecar_available() is True
    monkeypatch.setattr(config, "CLOUDFLARE_SYNCHRONOUS_TYPES",
                        frozenset({"matte"}))
    assert remote.sidecar_available() is False


@pytest.mark.parametrize("slot", [0, 1, 2])
def test_the_slot_nonce_lands_on_the_worker_formula(slot):
    nonce = remote._sidecar_nonce(slot)
    assert remote.sidecar_slot(nonce) == slot
    job = {"id": None, "type": "sidecar", "project_id": 1,
           "_cloudflare_sync_nonce": nonce}
    call_id = remote._cloudflare_call_id(job)
    assert call_id.startswith("cf-sidecar-")
    # cloudflare/src/index.ts sidecarSlot(): first 8 hex of the digest
    head = call_id[len("cf-sidecar-"):len("cf-sidecar-") + 8]
    assert int(head, 16) % 3 == slot


def test_run_sidecar_remote_falls_back_from_a_dark_vision_lane(monkeypatch):
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://cf.test")
    monkeypatch.setattr(config, "VISION_LANE_ENABLED", True)
    monkeypatch.setattr(remote, "desired_execution_provider",
                        lambda job: "cloudflare")
    calls = []

    def launch(job):
        calls.append((remote._cloudflare_lane_for(job),
                      remote.sidecar_slot(job["_cloudflare_sync_nonce"]),
                      job["id"], job["type"]))
        if len(calls) == 1:
            raise remote.CloudflareLaunchUnavailable(
                "Cloudflare Container route is not deployed")
        return {"ok": True, "chunks": {}}

    monkeypatch.setattr(remote, "_run_cloudflare_with_capacity_wait", launch)
    out = remote.run_sidecar_remote(7, {"slot": 2, "chunks": []})
    assert out == {"ok": True, "chunks": {}}
    assert calls == [("vision", 2, None, "sidecar"),
                     ("batch", 2, None, "sidecar")]
    # on the batch lane a refusal is the caller's to handle (requeue)
    monkeypatch.setattr(config, "VISION_LANE_ENABLED", False)
    calls.clear()

    def busy(job):
        calls.append(remote._cloudflare_lane_for(job))
        raise remote.CloudflareCapacityBusy("busy")
    monkeypatch.setattr(remote, "_run_cloudflare_with_capacity_wait", busy)
    with pytest.raises(remote.CloudflareCapacityBusy):
        remote.run_sidecar_remote(7, {"slot": 0, "chunks": []})
    assert calls == ["batch"]


def test_a_provider_other_than_cloudflare_is_a_refusal_not_a_loss(
        monkeypatch, tmp_path):
    """Percent routing or a stale type list can pick another provider for a
    call: nothing ran, so the chunks stay absent (no attempts spent, no
    6-hour failed marker) and the next ensure() can launch them."""
    import sidecars
    monkeypatch.setattr(remote, "desired_execution_provider",
                        lambda job: "modal")
    monkeypatch.setattr(remote, "_run_cloudflare_with_capacity_wait",
                        lambda job: pytest.fail("must not launch"))
    with pytest.raises(sidecars.SidecarUnavailable):
        remote.run_sidecar_remote(7, {"slot": 0, "chunks": []})

    store = sidecars.LocalStore(str(tmp_path / "store"))
    sidecars.use_store(store)
    launcher = sidecars._Launcher(
        execute=lambda src, payload: remote.run_sidecar_remote(7, payload),
        workers=1)
    sidecars.use_launcher(launcher)
    try:
        src = sidecars.SourceRef("cd" * 32, "k.mp4", 10.0, 25.0, 320, 180, 7)
        h = sidecars.ensure("faces.yunet", src, [(0, 10)])
        assert launcher.join(30)
        assert launcher.stats["calls"] == 1
        assert launcher.stats["retries"] == 0 and launcher.stats["failed"] == 0
        assert sidecars.status(h) == "absent"
        assert not [k for k in store.list("sidecars/")
                    if k.endswith((".failed.json", ".pending.json"))]
    finally:
        sidecars.use_launcher(None)
        sidecars.use_store(None)


def test_executor_skew_fault_answers_like_a_skewed_lane(monkeypatch):
    import faultinject
    monkeypatch.setattr(remote, "_run_cloudflare_with_capacity_wait",
                        lambda job: pytest.fail("must not launch"))
    with faultinject.inject("executor_skew"):
        out = remote.run_sidecar_remote(
            7, {"chunks": [{"key": "k1"}, {"key": "k2"}]})
    assert out["skew"] and set(out["chunks"]) == {"k1", "k2"}
    assert {r["reason"] for r in out["chunks"].values()} == {"executor_skew"}


def test_the_launcher_runs_locally_only_without_remote_compute(monkeypatch):
    import sidecars
    seen = []
    import analysis_jobs
    monkeypatch.setattr(analysis_jobs, "run_sidecar_job",
                        lambda db, job: seen.append("local") or {})
    monkeypatch.setattr(remote, "run_sidecar_remote",
                        lambda pid, p: seen.append("remote") or {})
    for name in ("REMOTE_EXECUTOR_URL", "CLOUDFLARE_EXECUTOR_URL"):
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "MODAL_EXECUTOR_ENABLED", False)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", False)
    src = sidecars.SourceRef("ab" * 32, "k", 10.0, 25.0, 320, 180, 7)
    sidecars._default_execute(src, {})
    monkeypatch.setattr(config, "REMOTE_EXECUTOR_URL", "https://run.test")
    with pytest.raises(sidecars.SidecarUnavailable):
        sidecars._default_execute(src, {})
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_ENABLED", True)
    monkeypatch.setattr(config, "CLOUDFLARE_EXECUTOR_URL", "https://cf.test")
    sidecars._default_execute(src, {})
    assert seen == ["local", "remote"]


# ── the Worker adapter and its config ───────────────────────────────────

ADAPTER = (WORKER / "cloudflare" / "src" / "index.ts").read_text()
WRANGLER = (WORKER / "cloudflare" / "wrangler.jsonc").read_text()


def test_the_adapter_lists_sidecar_everywhere_routing_needs_it():
    def block(name):
        m = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", ADAPTER, re.S)
        return m.group(1)
    assert '"sidecar"' in block("BATCH_TYPES")
    assert '"sidecar"' in block("SYNCHRONOUS_TYPES")
    assert block("VISION_TYPES").strip() == '"sidecar"'
    env_lists = re.findall(r"CLOUDFLARE_(?:EXECUTOR|SYNCHRONOUS)_TYPES: \[(.*?)\]",
                           ADAPTER, re.S)
    assert len(env_lists) == 2 and all('"sidecar"' in b for b in env_lists)
    assert "const SIDECAR_SHARDS = 3;" in ADAPTER
    assert "VISION?: DurableObjectNamespace<ValmeraVision>;" in ADAPTER
    assert "export class ValmeraVision extends ValmeraContainer" in ADAPTER
    assert 'VISION_LANE_ENABLED: this.env.VISION ? "1" : "0"' in ADAPTER
    # the deployed config and the deploy probes are untouched
    assert "ValmeraVision" not in WRANGLER and '"VISION"' not in WRANGLER
    assert "interactive: 20, batch: 8, agent: 5, mcp: 20, shorts: 8" in \
        ADAPTER
    assert "/^\\/probes\\/(interactive|batch|agent|mcp|shorts)$/" in ADAPTER


def test_the_deploy_workflow_merges_the_overlay_only_on_the_variable():
    wf = (ROOT / ".github" / "workflows" /
          "deploy-cloudflare-executor.yml").read_text()
    assert "VALMERA_VISION_LANE: ${{ vars.VALMERA_VISION_LANE }}" in wf
    assert 'if (process.env.VALMERA_VISION_LANE === "1") {' in wf
    assert wf.count("vision-overlay.cjs") == 1
    # same probe list and verify set
    assert ("for spec in interactive:executor batch:executor "
            "agent:agent_executor mcp:mcp_executor shorts:shorts_executor; do"
            ) in wf
    assert "vision:" not in wf
    build = (ROOT / ".github" / "workflows" /
             "build-vision-image.yml").read_text()
    assert "workflow_dispatch" in build and "push:" not in build
    assert "wrangler" not in build and "secrets." not in build
    assert "docker push" not in build


def test_the_overlay_is_the_dark_lane_only():
    overlay = json.loads((WORKER / "cloudflare" / "vision.jsonc").read_text())
    (c,) = overlay["containers"]
    assert c["class_name"] == "ValmeraVision"
    assert c["image"] == "../Dockerfile.vision"
    assert c["instance_type"] == "standard-4"
    assert overlay["durable_objects"]["bindings"] == [
        {"name": "VISION", "class_name": "ValmeraVision"}]
    assert overlay["migrations"] == [
        {"tag": "v3", "new_sqlite_classes": ["ValmeraVision"]}]
    tags = [m["tag"] for m in json.loads(WRANGLER)["migrations"]]
    assert "v3" not in tags


# ── images and models ───────────────────────────────────────────────────

def test_every_image_runs_the_baker_and_vision_is_onnx_only():
    cf = (WORKER / "Dockerfile.cloudflare").read_text()
    agent = (WORKER / "Dockerfile.agent-base").read_text()
    vision = (WORKER / "Dockerfile.vision").read_text()
    assert "RUN python /app/models/bake.py --dest /app/models" in cf
    assert 'echo batch; else echo interactive' in cf
    assert "RUN python /app/models/bake.py --image agent --dest /app/models" \
        in agent
    assert "RUN python /app/models/bake.py --image vision --dest /app/models" \
        in vision
    for text in (cf, agent, vision):
        assert "COPY models.lock /app/models.lock" in text
        assert "COPY requirements.d/ /app/requirements.d/" in text
        # baked before the source copy so a code change keeps the layer
        assert text.index("models/bake.py") < text.index("COPY . .")
    assert "playwright install" not in vision and "demucs" not in vision
    assert "download_model" not in vision
    ignore = (WORKER / ".dockerignore").read_text().split()
    assert "models/" not in ignore and "tools/" in ignore


def test_the_modal_agent_image_context_carries_the_baker_inputs():
    """Modal builds Dockerfile.agent-base from a context filtered by an
    ignore list. Every file the baker step COPYs must survive that filter,
    or `modal deploy` fails at the COPY (the DR fallback could not build);
    ordinary source must still stay out of the dependency layers."""
    modal = pytest.importorskip("modal")
    import modal_app
    # Modal turns an ignore list into exactly this matcher (dockerignore
    # rules, `!` re-includes); True means the file is left out.
    ignored = modal.FilePatternMatcher(*modal_app.AGENT_CONTEXT_IGNORE)
    needed = ["requirements.txt", "models.lock", "models/bake.py"]
    needed += [f"models/{p.name}" for p in (WORKER / "models").glob("*.json")]
    needed += [f"requirements.d/{p.name}"
               for p in (WORKER / "requirements.d").glob("*.txt")]
    assert [p for p in needed if ignored(Path(p))] == []
    for source in ("config.py", "renderer.py", "models/__init__.py",
                   "tests/test_w0a_lanes.py"):
        assert ignored(Path(source)), source
    # the executor image's dependency context is unchanged
    assert modal_app.DEPENDENCY_CONTEXT_IGNORE == ["**", "!requirements.txt"]


def _bake():
    sys.path.insert(0, str(WORKER / "models"))
    try:
        import bake
    finally:
        sys.path.pop(0)
    return bake


def test_models_lock_is_clean_and_matches_the_specs():
    bake = _bake()
    assert bake.check(str(WORKER / "models")) == []
    specs = bake.load_specs(str(WORKER / "models"))
    assert set(specs) == {"rvm_mobilenetv3", "yunet"}
    y = specs["yunet"]
    assert y["licence"] == "MIT" and y["bytes"] == 232589
    assert set(y["images"]) >= {"interactive", "batch", "agent", "vision"}
    assert "f12e12798e8314f7c074a6656816c048dcc95b7a" in y["url"]
    r = specs["rvm_mobilenetv3"]
    assert r["licence"] == "GPL-3.0" and r["server_side_only"] is True
    # batch serves matte.person while the vision lane is dark: its RVM (from
    # the FULL_COMPUTE step) is verified against the pin at build
    assert r["images"] == ["batch", "vision"]
    import sidecars
    assert sidecars.spec("faces.yunet")["model_version"] == "2023mar"
    assert config.YUNET_MODEL.endswith(y["file"])
    assert config.MATTE_RVM_MODEL.endswith(r["file"])


@pytest.mark.parametrize("licence,extra,ok", [
    ("MIT", {}, True), ("Apache-2.0", {}, True),
    ("CC-BY-NC-4.0", {}, False), ("CC BY-NC-SA 4.0", {}, False),
    ("AGPL-3.0", {}, False), ("GPL-3.0", {}, False),
    ("GPL-3.0", {"server_side_only": True}, True),
    ("Proprietary", {}, False), ("", {}, False),
    ("MIT", {"commercial_use": False}, False),
])
def test_the_baker_refuses_non_commercial_licences(licence, extra, ok):
    bake = _bake()
    spec = dict(licence=licence, **extra)
    assert (bake.licence_problem(spec) is None) is ok


def test_the_baker_verifies_every_byte(tmp_path):
    bake = _bake()
    blob = b"pinned model bytes"
    spec = {"name": "m", "file": "m.onnx", "url": "https://github.com/x/m",
            "sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob)}

    def good(url, dest):
        open(dest, "wb").write(blob)

    def evil(url, dest):
        open(dest, "wb").write(b"something else......")
    path, how = bake.bake_one(spec, str(tmp_path / "a"), fetch=good)
    assert how == "downloaded" and open(path, "rb").read() == blob
    assert bake.bake_one(spec, str(tmp_path / "a"), fetch=evil)[1] == "present"
    with pytest.raises(bake.BakeError, match="pinned"):
        bake.bake_one(spec, str(tmp_path / "b"), fetch=evil)
    assert not os.listdir(tmp_path / "b")            # nothing half-written
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "m.onnx").write_bytes(b"tampered")
    with pytest.raises(bake.BakeError, match="exists"):
        bake.bake_one(spec, str(tmp_path / "c"), fetch=good)


def test_the_baker_refuses_bad_specs_and_unpinned_requirements(tmp_path):
    bake = _bake()
    good = {"name": "x", "model_id": "x", "model_version": "1",
            "kinds": ["faces.yunet"], "file": "x.onnx",
            "url": "https://github.com/a/b/releases/download/v1/x.onnx",
            "source": "https://github.com/a/b", "licence": "MIT",
            "sha256": "0" * 64, "bytes": 1, "images": ["vision"]}
    assert bake.spec_problems(good) == []
    for bad, frag in (({"url": "http://github.com/a"}, "https"),
                      ({"url": "https://mirror.example/x.onnx"}, "official"),
                      ({"url": "https://github.com/a/b/raw/main/x.onnx"},
                       "pinned"),
                      ({"sha256": "abc"}, "sha256"),
                      ({"images": ["gpu"]}, "unknown images"),
                      ({"file": "../x.onnx"}, "bare file")):
        assert any(frag in p for p in bake.spec_problems(dict(good, **bad)))
    req = tmp_path / "vision.txt"
    req.write_text("# ok\nonnx==1.17.0\nmediapipe>=0.10\n-e git+x\nfoo\n")
    problems = bake.requirement_problems(str(req))
    assert len(problems) == 3


def test_the_vision_requirements_are_pinned_and_empty_in_wave_0():
    bake = _bake()
    req = WORKER / "requirements.d" / "vision.txt"
    assert bake.requirement_problems(str(req)) == []
    assert not bake._has_requirements(str(req))


# ── storage lifecycle ───────────────────────────────────────────────────

def test_r2_lifecycle_rules_cover_the_new_prefixes():
    rules = json.loads((WORKER / "cloudflare" /
                        "r2-lifecycle.json").read_text())["rules"]
    by_prefix = {r["conditions"]["prefix"]: r for r in rules}
    assert set(by_prefix) == {"sidecars/", "motion-clips/"}
    for r in rules:
        assert r["enabled"] is True
        cond = r["deleteObjectsTransition"]["condition"]
        assert cond == {"maxAge": 90 * 86400, "type": "Age"}
    import clip_store
    import sidecars
    assert sidecars.PREFIX + "/" in by_prefix
    assert clip_store.PREFIX + "/" in by_prefix
    # the GCP Artifact Registry policy file keeps its own (image) schema
    gcp = json.loads((WORKER / "artifact-cleanup-policy.json").read_text())
    assert [p["name"] for p in gcp] == ["delete-older-than-2d",
                                       "keep-3-most-recent"]
