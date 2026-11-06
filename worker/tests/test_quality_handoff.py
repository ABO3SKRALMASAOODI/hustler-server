"""The chat/export handoff must reflect the latest proved preview only."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_loop  # noqa: E402


def _ctx(**overrides):
    values = {
        "last_preview": {"edl_version": 3},
        "last_visual_critic": {"verdict": "pass", "findings": []},
        "last_audio_qc_findings": [],
        "last_audio_review": None,
        "last_taste": [],
        "last_taste_version": 3,
    }
    values.update(overrides)
    ctx = SimpleNamespace(**values)
    ctx.latest_edl = lambda: {"version": 3}
    return ctx


def test_clean_current_preview_is_export_ready():
    assert agent_loop._quality_handoff(_ctx()) == {
        "quality_status": "pass",
        "quality_findings": [],
        "export_ready": True,
    }


def test_a_critic_opinion_is_no_open_finding_on_the_handoff():
    report = {
        "verdict": "repair",
        "findings": [{
            "severity": "major", "category": "crop", "time_s": 4.2,
            "evidence": "the speaker's face is cut off",
            "repair": "move the crop left", "confidence": 0.94,
        }],
    }
    ctx = _ctx(last_visual_critic=report)
    quality = agent_loop._quality_handoff(ctx)
    assert quality["quality_status"] != "repair_required"
    assert quality["quality_findings"] == []
    assert agent_loop._disclose_outstanding_quality(ctx, "Done.") == "Done."


def test_a_measured_finding_keeps_verification_and_export_open():
    ctx = _ctx(last_picture_qc_findings=["FACE AT THE LEFT EDGE 4.2-5.0s"])
    quality = agent_loop._quality_handoff(ctx)
    assert quality["quality_status"] == "repair_required"
    assert quality["export_ready"] is False
    assert "FACE AT THE LEFT EDGE" in quality["quality_findings"][0]
    assert "Verification remains open" in \
        agent_loop._disclose_outstanding_quality(ctx, "Done.")


def test_stale_preview_is_unchecked_but_does_not_lock_export():
    quality = agent_loop._quality_handoff(
        _ctx(last_preview={"edl_version": 2}))
    assert quality == {"quality_status": "unchecked", "export_ready": True}


def test_edit_without_write_is_pushed_once():
    ctx = SimpleNamespace(semantic_contract={"mode": "edit"}, versions_written=[])
    messages = []
    assert agent_loop._edit_without_write_pushback(ctx, messages, False)
    assert "reach the same result another way" in messages[0]["content"]
    assert not agent_loop._edit_without_write_pushback(ctx, messages, True)


def test_edit_pushback_skips_once_the_edl_moved_or_for_an_answer():
    messages = []
    for ctx in (SimpleNamespace(semantic_contract={"mode": "edit"}, versions_written=[3]),
                SimpleNamespace(semantic_contract={"mode": "edit"}, versions_written=[],
                                rendered_versions={2}),
                SimpleNamespace(semantic_contract={"mode": "edit"}, versions_written=[],
                                images_generated=["a.png"]),
                SimpleNamespace(semantic_contract={"mode": "inspect"}, versions_written=[]),
                SimpleNamespace(semantic_contract=None, versions_written=[])):
        assert not agent_loop._edit_without_write_pushback(ctx, messages, False)
    assert messages == []


def test_audio_qc_finding_requires_repair_even_after_visual_pass():
    quality = agent_loop._quality_handoff(
        _ctx(last_audio_qc_findings=["integrated loudness is clipping"]))
    assert quality["quality_status"] == "repair_required"
    assert quality["export_ready"] is False
    assert quality["quality_findings"][0].startswith("audio QC:")


def test_actual_audio_fix_keeps_completion_open():
    quality = agent_loop._quality_handoff(_ctx(last_audio_review={
        "edl_version": 3, "verdict": "fix",
        "text": "FIX — music masks the question; lower it 4 dB."}))
    assert quality["quality_status"] == "repair_required"
    assert quality["export_ready"] is False
    assert any("music masks" in line for line in quality["quality_findings"])


def test_exact_durable_verification_clears_superseded_critic_findings():
    """A repair proof is newer truth than the critic that requested it.

    Production project 1144 passed/justified the flash-frame finding on the
    exact immutable version, then appended that stale finding to the user's
    final reply anyway.  Once durable verification closes every finding, old
    critic snapshots are history rather than an outstanding blocker.
    """
    report = {
        "verdict": "repair",
        "findings": [{
            "severity": "major", "category": "flash", "time_s": 5.2,
            "evidence": "a bright frame appears at the cut",
            "repair": "inspect the cut and remove the flash",
            "confidence": 0.96,
        }],
    }
    ctx = _ctx(
        last_visual_critic=report,
        verification_records={
            3: {"status": "justified", "unresolved_findings": []},
        },
    )
    assert agent_loop._quality_handoff(ctx) == {
        "quality_status": "justified",
        "quality_findings": [],
        "export_ready": True,
    }
    assert agent_loop._disclose_outstanding_quality(ctx, "Done.") == "Done."
