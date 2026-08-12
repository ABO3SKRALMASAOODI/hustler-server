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


def test_major_current_finding_blocks_ready_and_is_disclosed():
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
    assert quality["quality_status"] == "repair"
    assert quality["export_ready"] is False
    assert "move the crop left" in quality["quality_findings"][0]
    reply = agent_loop._disclose_outstanding_quality(ctx, "Done.")
    assert "still needs another repair pass" in reply


def test_stale_preview_never_unlocks_the_export_handoff():
    quality = agent_loop._quality_handoff(
        _ctx(last_preview={"edl_version": 2}))
    assert quality == {"quality_status": "unchecked", "export_ready": False}


def test_audio_qc_finding_blocks_ready_even_after_visual_pass():
    quality = agent_loop._quality_handoff(
        _ctx(last_audio_qc_findings=["integrated loudness is clipping"]))
    assert quality["quality_status"] == "repair"
    assert quality["export_ready"] is False
    assert quality["quality_findings"][0].startswith("audio QC:")
