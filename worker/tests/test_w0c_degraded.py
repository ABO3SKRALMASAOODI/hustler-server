"""AT-0C-1 — the DEGRADED gate (track 0C; ROADMAP A3; INTERFACES.md §6.3).

With RVM removed, a short with 6 matte devices resolves every one of them
to a substitute; the render's ``devices`` report lists all 6, the verifier
records one blocking finding per device and the export stays blocked until
the degradation is acknowledged — never shipped silently.

The substitutes' pixels arrive with Wave 1 (1B owns the subject fx); this
pins the contract layer every renderer report flows through: resolver ->
report rows -> verification record -> export handoff.
"""

import pytest

from w0c_support import six_matte_cards, use_cards

import agent_loop  # noqa: E402
import devices  # noqa: E402
import faultinject  # noqa: E402
import quality_verifier  # noqa: E402
from schemas import default_edl  # noqa: E402


def _short_items():
    """Six matte devices across a 30 s short (one per device card)."""
    stages = {"text_behind_head": "motion"}
    return [{"id": f"fx{i + 1}", "device": cid,
             "stage": stages.get(cid, "world"),
             "start": 2.0 + 4.5 * i, "end": 2.5 + 4.5 * i}
            for i, cid in enumerate(sorted(six_matte_cards()))]


def _report(lane, sidecar_status, budget_ms=None):
    return devices.plan_rows(_short_items(), lane=lane,
                             sidecar_status=sidecar_status,
                             budget_ms=budget_ms, fps=30.0)


class _Ctx:
    """Just what agent_loop._quality_handoff reads."""

    def __init__(self, edl, version, record, preview):
        self._row = {"version": version, "json": edl}
        self.verification_records = {version: record}
        self.last_preview = preview
        self.user_message = "make it a premium short"
        self.versions_written = [version]
        self.last_visual_critic = {}
        self.last_story_review = {}

    def latest_edl(self):
        return self._row


def test_rvm_removed_six_matte_devices_render_on_substitutes_and_block_export(
        monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    # RVM removed: the model is gone from the lane and the matte sidecar
    # cannot be produced (its runner fails without the model).
    with faultinject.inject("model_missing:matte.person"):
        assert "onnx.rvm" not in devices.lane_features("batch")
        rows = _report("batch", {"matte.person": "failed"})
    assert len(rows) == 6
    assert all(r["rung"] == "substitute" for r in rows), rows
    subs = {r["device"]: r["reason"].rsplit("-> ", 1)[-1] for r in rows}
    assert subs == {"background_swap": "colour_field_card",
                    "poster_silhouette": "colour_field_card",
                    "subject_clone": "static_placement",
                    "subject_fill_flash": "full_frame_flash",
                    "subject_glow": "halation",
                    "text_behind_head": "face_safe_slam"}
    assert all("matte.person failed" in r["reason"] for r in rows)

    edl = default_edl(30.0)
    preview = {"edl_version": 4, "storage_key": "renders/7/p.mp4",
               "duration_s": 30.0, "devices": rows}
    rec = quality_verifier.build_verification_record(
        7, 4, {}, edl, {}, preview=preview)
    deg = [r for r in rec["unresolved_findings"]
           if r["code"] == quality_verifier.DEGRADED_FINDING]
    assert len(deg) == 6                                  # all six listed
    assert sorted(r["evidence"]["device"] for r in deg) == sorted(subs)
    for r in deg:
        assert r["severity"] == "error" and quality_verifier.is_blocking(r)
        assert "rendered as a substitute" in r["message"]
    assert rec["status"] == "repair_required"
    assert rec["render_evidence"]["devices"] == rows

    # The export gate the backend reads (assistant meta) is closed.
    ctx = _Ctx(edl, 4, rec, preview)
    handoff = agent_loop._quality_handoff(ctx)
    assert handoff["quality_status"] == "repair_required"
    assert handoff["export_ready"] is False
    assert any("rendered as a substitute" in f
               for f in handoff["quality_findings"])

    # Acknowledged (justify by the finding code) -> exportable, and the
    # durable record keeps what degraded and why it was accepted.
    ack = quality_verifier.justify_findings(
        rec, [quality_verifier.DEGRADED_FINDING],
        "The user accepted flat substitutes: no matte model on this lane "
        "today, and they want the short now.")
    assert ack["status"] == "justified" and not ack["unresolved_findings"]
    assert len(ack["justifications"]) == 6
    ctx = _Ctx(edl, 4, ack, preview)
    assert agent_loop._quality_handoff(ctx)["export_ready"] is True


def test_warm_sidecars_with_rvm_present_render_full_and_pass(
        monkeypatch, tmp_path):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    rows = _report("batch", {"matte.person": "ready"})
    assert [r["rung"] for r in rows] == ["full"] * 6
    rec = quality_verifier.build_verification_record(
        7, 4, {}, default_edl(30.0), {},
        preview={"edl_version": 4, "storage_key": "r.mp4", "devices": rows})
    assert not [r for r in rec["findings"]
                if r["code"] == quality_verifier.DEGRADED_FINDING]
    assert rec["status"] == "passed"


@pytest.mark.parametrize("lane, status, rung", [
    ("interactive", {"matte.person": "pending"}, "substitute"),  # preview
    ("agent", {"matte.person": "ready"}, "substitute"),          # no browser
])
def test_any_non_full_rung_is_listed(monkeypatch, tmp_path, lane, status,
                                     rung):
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    rows = _report(lane, status)
    degraded = devices.degraded(rows)
    assert degraded and all(r["rung"] == rung for r in degraded)
    findings = quality_verifier.device_findings({"devices": rows})
    assert len(findings) == len(degraded)


def test_an_omitted_device_is_named_as_left_out():
    rows = [{"id": "fx9", "device": "strobe_section", "family":
             "punctuation", "stage": "over_type", "rung": "omit",
             "reason": "dark", "est_ms": 0.0}]
    f = quality_verifier.device_findings({"devices": rows})
    assert len(f) == 1 and "was left out: dark" in f[0]["message"]
    assert quality_verifier.device_findings({}) == []
    assert quality_verifier.device_findings({"devices": None}) == []


def test_a_final_ships_only_the_degradations_that_were_acknowledged(
        monkeypatch, tmp_path):
    """The export half of the gate: the final render's own devices report
    is checked against the version's record. What the preview's
    acknowledgement covered passes; a further degradation does not."""
    use_cards(monkeypatch, six_matte_cards(), tmp_path)
    with faultinject.inject("model_missing:matte.person"):
        rows = _report("batch", {"matte.person": "failed"})
    rec = quality_verifier.build_verification_record(
        7, 4, {}, default_edl(30.0), {},
        preview={"edl_version": 4, "storage_key": "p.mp4", "devices": rows})
    # Nothing acknowledged yet: every degraded device holds the final.
    assert quality_verifier.unacknowledged_degradations(rec, rows) == rows
    ack = quality_verifier.justify_findings(
        rec, [quality_verifier.DEGRADED_FINDING],
        "The user accepted the flat substitutes for this short today.")
    assert quality_verifier.unacknowledged_degradations(ack, rows) == []
    # The final degraded one device further (substitute -> omit) and drew
    # the rest as acknowledged: only the new degradation holds it.
    final = [dict(r) for r in rows]
    final[0] = dict(final[0], rung="omit", reason="sidecar timeout")
    held = quality_verifier.unacknowledged_degradations(
        {"record": ack}, final)
    assert held == [final[0]]
    full = [dict(r, rung="full") for r in rows]
    assert quality_verifier.unacknowledged_degradations({}, full) == []
