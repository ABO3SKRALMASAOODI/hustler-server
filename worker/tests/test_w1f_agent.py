"""Track 1F: the agent surface — the verify department, look_at overlays,
project_state, the critic's corroboration rule, the final-export DEGRADED
gate and the close-out guidance (ROADMAP 1F; INTERFACES_W1.md §4, §6.3,
§8)."""

import json
import os
import re
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
ROOT = os.path.dirname(WORKER)
sys.path.insert(0, WORKER)

import agent_prompt     # noqa: E402
import agent_tools      # noqa: E402
import job_completion   # noqa: E402
import motion_qa        # noqa: E402
import preview_critic   # noqa: E402
import quality_verifier  # noqa: E402
import tools_ext        # noqa: E402
from tools_ext import verify_tools  # noqa: E402

VERBS = ("suggest_devices", "set_intensity", "proof_device", "audit_motion")


# ── the verify department ────────────────────────────────────────────────

def test_verify_department_is_live_with_its_pinned_verbs():
    assert set(verify_tools.TOOL_SPECS) == set(VERBS)
    from w3_pins import W3_VERIFY_VERBS   # Wave 3 joins the department
    assert tools_ext.EXT_DEPARTMENTS.get("verify") - W3_VERIFY_VERBS == \
        set(VERBS)
    for verb in VERBS:
        assert verb in agent_tools.TOOLS
        assert verb not in tools_ext.core_names(agent_tools.TOOLS)
    assert agent_tools.WRITE_TOOLS & set(VERBS) == {"set_intensity"}
    assert agent_tools.REQUIRED_ARGS["set_intensity"] == ["level"]
    enum = agent_tools.TOOLS["load_tools"][2]["domains"]["items"]["enum"]
    assert "verify" in enum


def test_verify_department_budgets():
    compact = agent_tools.openai_tools("any-model", compact=True,
                                       names=list(VERBS))
    size = len(json.dumps(compact, separators=(",", ":")))
    assert size <= 5000, size                     # 1F's share
    for verb in VERBS:
        _fn, desc, _props = agent_tools.TOOLS[verb]
        assert len(desc) <= 900, (verb, len(desc))
        assert len(verify_tools.COMPACT[verb]) <= tools_ext.COMPACT_MAX_CHARS


def test_mcp_classifies_the_read_verbs_read_only():
    with open(os.path.join(ROOT, "backend", "routes", "mcp.py")) as fh:
        src = fh.read()
    block = src[src.index("_READ_ONLY_EXACT = {"):]
    block = block[:block.index("}")]
    for verb in ("suggest_devices", "proof_device", "audit_motion"):
        assert f'"{verb}"' in block
    assert '"set_intensity"' not in block


def test_core_catalog_growth_is_within_1fs_share():
    core = tools_ext.core_names(agent_tools.TOOLS)
    size = len(json.dumps(agent_tools.openai_tools(
        "any-model", compact=True, names=core), separators=(",", ":")))
    # Merged Wave 1: every department name and track skill grows the enums
    # automatically (test_w1_seams sets that growth aside byte for byte);
    # what is left over the Wave 0 base is 1F's own share (<= 250 bytes),
    # and the whole stays under the Wave 1 ceiling.
    import test_w1_seams as seams
    stripped = len(json.dumps(seams._strip_w1_growth(agent_tools.openai_tools(
        "any-model", compact=True, names=core)), separators=(",", ":")))
    assert stripped - 81688 <= 250, stripped
    assert size <= seams.W1_CORE_CEILING < 83_000, size
    look = agent_tools.TOOLS["look_at"][2]["overlay"]
    assert look["enum"] == ["matte", "free_space", "beats"]


# ── look_at(overlay=...) ─────────────────────────────────────────────────

class _Ctx:
    def __init__(self, tmp_path, edl=None):
        self.workdir = str(tmp_path)
        self.index = {"video": {"duration": 30.0, "fps": 25.0},
                      "words": []}
        self.has_main_video = True
        self.project_id = 1
        self._edl = edl or {"keep": [[0.0, 10.0], [12.0, 20.0]]}

    def latest_edl(self):
        return {"version": 3, "json": self._edl}


def test_look_at_rejects_an_unknown_overlay_and_rendered_overlays(tmp_path):
    ctx = _Ctx(tmp_path)
    assert agent_tools.look_at(ctx, times=[1.0], overlay="xray").startswith(
        "REJECTED: overlay is one of matte, free_space, beats")
    assert agent_tools.look_at(ctx, output_times=[1.0], rendered=True,
                               overlay="beats").startswith("REJECTED")


def _frame(tmp_path, w=360, h=640, name="f.jpg"):
    from PIL import Image
    p = os.path.join(str(tmp_path), name)
    Image.fromarray(np.full((h, w, 3), 90, np.uint8)).save(p)
    return p


def test_free_space_shades_ui_bands_and_returns_free_rects(tmp_path):
    p = _frame(tmp_path)
    out, label = motion_qa.overlay_free_space(p, str(tmp_path / "o.jpg"),
                                              output=True)
    assert os.path.exists(out) and "free rects" in label
    rects = motion_qa.free_rects([[0, 0, 1, 0.06], [0, 0.8, 1, 1]])
    assert rects and rects[0][1] >= 0.06 and rects[0][3] <= 0.8
    assert motion_qa.free_rects([[0, 0, 1, 1]]) == []


def test_beats_ruler_marks_moments_and_devices(tmp_path):
    p = _frame(tmp_path, 320, 180)
    rows = [(4.9, "stress", "big"), (5.2, "cut", None),
            (5.5, "device", "ac1"), (9.0, "beat", None)]
    out, label = motion_qa.overlay_beats(
        p, str(tmp_path / "b.jpg"), 5.0, rows,
        payoff={"t": 5.4, "hold_end": 6.2})
    from PIL import Image
    assert Image.open(out).size[1] > 180            # the ruler band
    assert "stress=big@4.90" in label and "device=ac1@5.50" in label
    assert "9.00" not in label                      # outside ±1.5 s


def test_overlay_on_source_frames_maps_program_time(tmp_path, monkeypatch):
    ctx = _Ctx(tmp_path)
    p = _frame(tmp_path, 320, 180)
    seen = []

    def fake(ctx_, kind, path, **kw):
        seen.append((kind, kw.get("stage"), kw.get("src_t"),
                     kw.get("out_t")))
        return path, f" [{kind}:{kw.get('stage')}]"
    monkeypatch.setattr(motion_qa, "apply_overlay", fake)
    frames, names = agent_tools._overlay_source_frames(
        ctx, "beats", [p, p], ["@4.00s", "@13.00s"])
    assert names[0].endswith("[beats:raw] [beats:post]")
    assert seen[2][3] == pytest.approx(11.0)        # 13 s source -> 11 s
    _f, names = agent_tools._overlay_source_frames(
        ctx, "beats", [p], ["@11.00s"])             # cut out of the edit
    assert seen[-1][3] is None


def test_matte_overlay_without_a_store_falls_back_to_faces(tmp_path):
    ctx = _Ctx(tmp_path)
    ctx.proxy_path = lambda: None
    p = _frame(tmp_path, 320, 180)
    out, label = motion_qa.overlay_matte(ctx, p, 2.0,
                                         str(tmp_path / "m.jpg"))
    assert os.path.exists(out)
    assert label.startswith(" [") and "matte" in label


# ── project_state ────────────────────────────────────────────────────────

def test_project_state_stays_silent_until_devices_exist(tmp_path):
    ctx = _Ctx(tmp_path)
    assert agent_tools.project_state(ctx) == {}
    assert agent_tools.project_state_line(ctx) == ""
    ctx.has_main_video = False
    assert agent_tools.project_state(ctx) == {}


def test_project_state_reports_matte_and_intensity_when_fx_exist(
        tmp_path, monkeypatch):
    ctx = _Ctx(tmp_path, {"keep": [[0.0, 10.0]], "fx": [{"id": "a"}]})
    monkeypatch.setattr(verify_tools, "matte_state",
                        lambda c, e: {"status": "pending", "estimate_s": 30})
    monkeypatch.setattr(verify_tools, "project_intensity", lambda c: "calm")
    state = agent_tools.project_state(ctx)
    assert state["matte_status"]["status"] == "pending"
    line = agent_tools.project_state_line(ctx)
    assert "Person matte on the current cut: pending" in line
    assert "Device intensity: calm" in line


# ── the critic: motion findings need a deterministic metric ─────────────

def _report(findings):
    return {"verdict": "pass", "findings": findings, "rubric": {}}


def test_motion_findings_block_only_when_a_metric_corroborates():
    f_motion = {"severity": "major", "category": "motion_trigger",
                "time_s": 4.0, "target_id": "ac1", "confidence": 0.8,
                "evidence": "the hit lands late", "repair": "move it"}
    f_effect = {"severity": "blocker", "category": "effect", "time_s": 9.0,
                "target_id": None, "confidence": 0.95,
                "evidence": "harsh flash", "repair": "remove"}
    rep = _report([dict(f_motion), dict(f_effect)])
    assert not any(preview_critic.blocks(f) for f in rep["findings"])
    evidence = [{"id": "ac1", "time_s": 4.02, "metric": "land_err_f",
                 "value": 3.0, "bar": "<=1", "pass": False},
                {"id": "zz", "time_s": 20.0, "metric": "flash_rate_exceeded",
                 "value": None, "bar": None, "pass": False},
                {"id": "ok", "time_s": 9.0, "metric": "legibility",
                 "value": 70.0, "bar": ">=60", "pass": True}]
    out = preview_critic.corroborate(rep, evidence)
    motion, effect = out["findings"]
    assert motion["corroborated_by"][0]["metric"] == "land_err_f"
    assert preview_critic.blocks(motion)
    assert "corroborated_by" not in effect and not preview_critic.blocks(
        effect)
    assert out["verdict"] == "repair"
    lines = preview_critic.repair_lines(out)
    assert len(lines) == 1 and "corroborated by land_err_f 3" in lines[0]
    # a minor corroborated motion note stays advisory
    minor = dict(f_motion, severity="minor")
    out2 = preview_critic.corroborate(_report([minor]), evidence)
    assert out2["verdict"] == "pass"
    assert preview_critic.corroborate(None, evidence) is None


def test_critic_evidence_reads_the_audit_and_failed_proof_metrics():
    edl = {"keep": [[0.0, 10.0]], "fx": [
        {"id": f"n{k}", "type": "accent", "stage": "pre_captions",
         "start": round(2.0 + 0.08 * k, 2), "end": round(2.04 + 0.08 * k, 2)}
        for k in range(5)]}
    proof = {"devices": [{"id": "n1", "metrics": {
        "land_err_f": {"value": 2.0, "bar": "<=1", "pass": False},
        "flash_risk": {"value": 1.0, "pass": True}}}]}
    rows = motion_qa.critic_evidence(edl, {}, proof_result=proof)
    metrics = {r["metric"] for r in rows}
    assert "flash_rate_exceeded" in metrics and "land_err_f" in metrics
    lerr = [r for r in rows if r["metric"] == "land_err_f"][0]
    assert lerr["time_s"] == pytest.approx(2.08)


# ── the final-export DEGRADED gate ──────────────────────────────────────

ROW = {"id": "sx1", "device": "subject_fill_flash", "family": "matte_fx",
       "stage": "world", "rung": "substitute",
       "reason": "matte.person pending -> full_frame_flash", "est_ms": 1.0}


def test_hold_final_records_a_justifiable_finding():
    held = quality_verifier.unacknowledged_degradations(None, [ROW])
    assert held == [ROW]
    rec = quality_verifier.hold_final(None, held, project_id=5, version=9)
    assert rec["status"] == "repair_required"
    f = rec["unresolved_findings"][0]
    assert f["code"] == "device_degraded" and f["evidence"]["final"] is True
    assert quality_verifier.hold_final(rec, held) == rec      # idempotent
    acked = quality_verifier.justify_findings(
        rec, ["device_degraded"], "the user accepted the flash substitute "
        "while the matte measures")
    assert quality_verifier.unacknowledged_degradations(acked, [ROW]) == []
    # a further degrade of the same device is NOT covered
    omit = dict(ROW, rung="omit")
    assert quality_verifier.unacknowledged_degradations(acked, [omit]) == \
        [omit]
    assert "held" in quality_verifier.final_hold_message(held)


class _WDB:
    def __init__(self, record=None):
        self.record, self.meta, self.calls = record, {}, []

    def run(self, fn, *a):
        name = fn.__name__
        self.calls.append(name)
        if name == "get_verification_record":
            return self.record
        if name == "upsert_verification_record":
            self.record = a[2]
            return True
        if name == "update_asset_meta":
            self.meta.update(a[1])
            return None
        raise AssertionError(name)


def test_the_worker_export_path_holds_an_unacknowledged_degraded_final():
    db = _WDB()
    job = {"id": 1, "type": "final", "project_id": 5,
           "payload": {"edl_version": 9}}
    result = {"render_asset_id": 77, "devices": [ROW, dict(ROW, id="ok",
                                                           rung="full")]}
    held = job_completion.hold_degraded_final(db, job, result)
    assert [r["id"] for r in held] == ["sx1"]
    assert db.meta["degraded_hold"] == held
    assert result["export_held"].startswith("This export is held")
    assert db.record["status"] == "repair_required"
    # acknowledged: the next final is not held
    db2 = _WDB(quality_verifier.justify_findings(
        db.record, ["device_degraded"],
        "the substitute flash is what the user approved on the preview"))
    result2 = {"render_asset_id": 78, "devices": [ROW]}
    assert job_completion.hold_degraded_final(db2, job, result2) == []
    assert "export_held" not in result2
    # ...and is stamped judged-and-released: the backend holds an UNSTAMPED
    # final with degraded rows (routes/video.final_degraded_hold)
    assert db2.meta == {"degraded_hold": []}
    # previews and clean finals are untouched
    assert job_completion.hold_degraded_final(
        _WDB(), dict(job, type="preview"), {"devices": [ROW]}) == []
    assert job_completion.hold_degraded_final(
        _WDB(), job, {"devices": [dict(ROW, rung="full")]}) == []


def test_acknowledging_releases_exactly_the_held_final():
    rec = quality_verifier.hold_final(None, [ROW], project_id=5, version=9)

    class DB:
        meta = {"devices": [ROW], "degraded_hold": [ROW]}

        def run(self, fn, *a):
            if fn.__name__ == "find_render_asset":
                assert a[1:] == ("final", 9)
                return {"id": 77, "meta": dict(self.meta)}
            if fn.__name__ == "update_asset_meta":
                self.meta.update(a[1])
                return None
            raise AssertionError(fn.__name__)
    db = DB()
    assert job_completion.refresh_final_hold(db, 5, 9, rec) == [ROW]
    acked = quality_verifier.justify_findings(
        rec, ["device_degraded"], "confirmed with the user: the flash "
        "substitute reads fine for this short")
    assert job_completion.refresh_final_hold(db, 5, 9, acked) == []
    assert db.meta["degraded_hold"] == []
    # a final whose commit never stamped it (the stamp failed) is judged on
    # the next acknowledgement from its own device rows
    db.meta = {"devices": [ROW, dict(ROW, id="ok", rung="full")]}
    assert job_completion.refresh_final_hold(db, 5, 9, rec) == [ROW]
    assert db.meta["degraded_hold"] == [ROW]
    db.meta = {"devices": [ROW]}
    assert job_completion.refresh_final_hold(db, 5, 9, acked) == []
    assert db.meta["degraded_hold"] == []
    # a final with nothing degraded is left alone
    db.meta = {"devices": [dict(ROW, rung="full")]}
    assert job_completion.refresh_final_hold(db, 5, 9, rec) == []
    assert "degraded_hold" not in db.meta


# ── close-out guidance ───────────────────────────────────────────────────

def test_prompt_and_skills_teach_when_devices_earn_their_place():
    core = agent_prompt.CORE_PROMPT
    assert "CREATIVE DEVICES" in core and "suggest_devices" in core
    assert "proof_device" in core and "read_skill devices" in core
    assert "devices" in agent_prompt.skill_names()
    text = agent_prompt.read_skill_text("devices")
    for phrase in ("Through apply_edit_batch", "payoff", "one punctuation "
                   "family per section", "3 full-frame flashes",
                   "ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES"):
        assert phrase in text, phrase
    for name in ("motion-design", "transitions", "music", "effects-grades",
                 "text-graphics"):
        assert "read_skill devices" in agent_prompt.read_skill_text(name), \
            name
    batch = agent_tools.TOOLS["apply_edit_batch"][1]
    assert "Layer fx" in batch and "read_skill('devices')" in batch


def test_follow_crop_caveats_are_gone():
    desc = agent_tools.TOOLS["add_text_behind"][1]
    assert "inside ONE take" not in desc and "no speed ramp" not in desc
    assert "Follow crops, cuts" in desc
    tg = agent_prompt.read_skill_text("text-graphics")
    assert "a window crossing a cut" not in tg
    md = agent_prompt.read_skill_text("motion-design")
    assert "a crop that follows the speaker)" not in md
    plugin = os.path.join(ROOT, "plugins", "valmera", "skills",
                          "valmera-podcast-shorts", "references")
    with open(os.path.join(plugin, "editing.md")) as fh:
        editing = fh.read()
    assert "only inside one continuous shot" not in editing
    assert "proof_device" in editing and "suggest_devices" in editing
    with open(os.path.join(plugin, "looks.md")) as fh:
        looks = fh.read()
    # strobes stay out of a talking short's body except as its ONE recipe
    # (Wave 1 integration: recipes, one signature move per short)
    assert "### Devices" in looks and re.search(
        r"Strobe.*\| 0, or the recipe \|", looks)
