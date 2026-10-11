"""Flash safety (track 0C; owner decision ROADMAP §10.1): automatic
proposals keep at most 3 full-frame flashes in any one second; past that
only on the user's opt-in, and then the export is flagged
contains_flashing. Windows are frame-exact on every rate."""

import pytest

import config  # noqa: E402
import flash_safety  # noqa: E402
import quality_verifier  # noqa: E402
from schemas import default_edl  # noqa: E402

RATES = (25.0, 30000 / 1001, 24.0, 60000 / 1001)


def _burst(n, start=2.0, step=0.1, strength=None):
    return [{"id": f"f{i}", "t": round(start + i * step, 3),
             "strength": (strength[i] if strength else 1.0)}
            for i in range(n)]


def test_the_limit_is_the_owner_default():
    assert config.FLASH_SAFE_MAX_PER_S == 3


@pytest.mark.parametrize("fps", RATES)
def test_a_burst_is_thinned_to_three_per_second_on_every_rate(fps):
    events = _burst(10, step=0.08)               # 10 flashes in 0.72 s
    kept, thinned = flash_safety.plan(events, fps)
    assert len(kept) == 3 and len(thinned) == 7
    frames = [round(e["t"] * fps) for e in kept]
    assert flash_safety.violations(frames, fps, 3) == []
    assert [e["t"] for e in kept] == sorted(e["t"] for e in kept)


def test_the_strongest_hits_survive_deterministically():
    strength = [0.2, 0.9, 0.3, 1.0, 0.1, 0.8, 0.4]
    events = _burst(7, step=0.1, strength=strength)
    kept, _ = flash_safety.plan(events, 30.0)
    assert sorted(e["id"] for e in kept) == ["f1", "f3", "f5"]
    assert flash_safety.plan(events, 30.0) == flash_safety.plan(
        list(reversed(events)), 30.0)


def test_spacing_of_exactly_one_second_is_allowed():
    events = [{"t": t} for t in (1.0, 1.34, 1.67, 2.0, 2.34, 2.67, 3.0)]
    kept, thinned = flash_safety.plan(events, 30.0)
    assert not thinned
    # four inside one second is not
    kept, thinned = flash_safety.plan([{"t": t} for t in
                                       (1.0, 1.2, 1.4, 1.6)], 30.0)
    assert len(kept) == 3 and len(thinned) == 1


def test_opt_in_keeps_everything_and_local_flashes_pass():
    events = _burst(8, step=0.05)
    kept, thinned = flash_safety.plan(events, 30.0, opted_in=True)
    assert len(kept) == 8 and thinned == []
    local = [dict(e, full_frame=False) for e in _burst(6, step=0.05)]
    kept, thinned = flash_safety.plan(local + _burst(1, start=9.0), 30.0)
    assert len(kept) == 7 and thinned == []


@pytest.mark.parametrize("text, want", [
    ("add a strobe on the drop", True),
    ("flash him white on every kick", True),
    ("make it flashy", True),
    ("make it like 19", True),
    ("no flashing please, I'm photosensitive", False),
    ("fewer flashes", False),
    ("make it calm and clean", False),
    ("", False),
])
def test_only_the_users_own_request_opts_in(text, want):
    assert flash_safety.opted_in(text) is want


def _flashy_edl(n=5, step=0.15):
    edl = default_edl(12.0)
    edl["effects"] = {"stylize": [
        {"id": f"st{i}", "kind": "flash", "start": round(2.0 + i * step, 2),
         "end": round(2.0 + i * step + 0.05, 2), "intensity": 0.8}
        for i in range(n)]}
    return edl


def test_assess_reads_the_peak_from_the_edl():
    a = flash_safety.assess(_flashy_edl(5), {}, "")
    assert a["events"] == 5 and a["peak_per_s"] == 5
    assert a["over_limit"] and not a["opted_in"]
    assert not a["contains_flashing"]
    a = flash_safety.assess(_flashy_edl(5), {}, "strobe it on the drop")
    assert a["contains_flashing"]
    a = flash_safety.assess(_flashy_edl(3), {}, "")
    assert not a["over_limit"]
    motion = default_edl(12.0)
    motion["motion"] = [{"id": f"m{i}", "template": "flash_transition",
                         "start": 1.0 + i * 0.2, "end": 1.3 + i * 0.2}
                        for i in range(4)]
    assert flash_safety.assess(motion, {}, "")["peak_per_s"] == 4


def test_the_verifier_blocks_unasked_flashing_and_flags_opt_in():
    edl = _flashy_edl(5)
    preview = {"edl_version": 3, "storage_key": "r.mp4"}
    rec = quality_verifier.build_verification_record(
        7, 3, {}, edl, {}, preview=preview, request_text="make a reel")
    codes = [r["code"] for r in rec["unresolved_findings"]]
    assert quality_verifier.FLASH_FINDING in codes
    assert rec["status"] == "repair_required"
    # the program flashes past the limit: whatever lets it export (thinning
    # aside) is an opt-in, so the record carries the export flag already
    assert rec["contains_flashing"] is True
    rec = quality_verifier.build_verification_record(
        7, 3, {}, edl, {}, preview=preview,
        request_text="strobe him white on every kick")
    assert quality_verifier.FLASH_FINDING not in [
        r["code"] for r in rec["findings"]]
    assert rec["contains_flashing"] is True
    calm = quality_verifier.build_verification_record(
        7, 3, {}, _flashy_edl(2), {}, preview=preview, request_text="")
    assert calm["contains_flashing"] is False
    assert quality_verifier.FLASH_FINDING not in [
        r["code"] for r in calm["findings"]]


def test_the_export_gate_reads_the_same_rule_loaded_by_path():
    """backend/routes/video.py loads flash_safety by path (no worker tree on
    sys.path) and flags a final past the limit contains_flashing."""
    import importlib.util
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "flash_safety.py")
    spec = importlib.util.spec_from_file_location("worker_flash_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.assess(_flashy_edl(5))["over_limit"] is True
    assert mod.assess(_flashy_edl(2))["over_limit"] is False
    src = open(os.path.join(os.path.dirname(path), os.pardir, "backend",
                            "routes", "video.py"), encoding="utf-8").read()
    assert 'wflash = _pure_worker_module("flash_safety")' in src
    assert 'flash_flag["contains_flashing"] = True' in src


def test_an_earlier_opt_in_is_acknowledged_not_thinned():
    """Opt-in is read from the current request; a follow-up turn on an edit
    the user asked to strobe ("fix the typo") gets a finding the editor
    acknowledges by naming the earlier request — it never has to strip the
    user's own flashing to export."""
    edl = _flashy_edl(5)
    preview = {"edl_version": 4, "storage_key": "r.mp4"}
    rec = quality_verifier.build_verification_record(
        7, 4, {}, edl, {}, preview=preview, request_text="fix the typo")
    row = next(r for r in rec["unresolved_findings"]
               if r["code"] == quality_verifier.FLASH_FINDING)
    assert "earlier message" in row["repair"]
    assert "asked for it earlier" in row["message"]     # what the editor reads
    assert "justify_verification_findings" in row["repair"]
    assert quality_verifier.FLASH_FINDING not in \
        quality_verifier.NON_JUSTIFIABLE_FINDINGS
    done = quality_verifier.justify_findings(
        rec, [row["finding_id"]],
        "The user asked for 'strobe him white on every kick' two messages "
        "ago; this turn only fixed a typo.")
    assert done["status"] == "justified"
    assert done["contains_flashing"] is True
