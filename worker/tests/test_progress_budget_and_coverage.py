import sys
from copy import deepcopy

import pytest

import media
import renderer
import render_qc
import visual_coverage
from schemas import default_edl


def test_productive_slow_final_gets_finish_time_within_lease():
    # Progress from the production timeout, without customer media.
    next_deadline = media._progress_deadline(4118, 20000, 4119, 1715.8, 1759.5, 1)
    assert 4224 < next_deadline < 4500
    assert media._progress_deadline(4118, 4150, 4119, 1715.8, 1759.5, 1) == 4118
    assert media._progress_deadline(4118, 20000, 4119, 1715.8, 1759.5, 31) == 4118
    assert media._progress_deadline(4118, 20000, 4119, 0, 1759.5, 1) == 4118
    assert media._progress_deadline(4118, 20000, 20001, 1750, 1759.5, 1) == 4118


def test_run_survives_soft_estimate_but_not_a_stationary_clock():
    script = ("import time\nfor i in range(35):\n"
              " print(f'out_time_ms={i*100000}', flush=True)\n time.sleep(.1)\n")
    media.run([sys.executable, "-c", script], timeout=1, hard_timeout=8,
              progress_cb=lambda x: None, expected_out_s=3.5)
    frozen = "import time; print('out_time_ms=0',flush=True); time.sleep(10)"
    with pytest.raises(media.MediaError, match="wall-clock"):
        media.run([sys.executable, "-c", frozen], timeout=1, hard_timeout=8,
                  progress_cb=lambda x: None, expected_out_s=3.5)


def test_only_durable_final_wrapper_opts_into_progress_grace(monkeypatch):
    monkeypatch.setenv("EXECUTOR_PROVIDER", "box")
    captured = []
    monkeypatch.setattr(media, "run", lambda cmd, **kw: captured.append(kw))
    for kind in ("final", "preview"):
        token = renderer._RENDER_JOB_TYPE.set(kind)
        try:
            renderer._render_media_run([], timeout=4118, expected_out_s=1759.5,
                                      progress_cb=lambda x: None)
        finally:
            renderer._RENDER_JOB_TYPE.reset(token)
    assert 4118 < captured[0]["hard_timeout"] <= 20000
    assert "hard_timeout" not in captured[1]


def test_visual_counts_and_gaps_are_measured_without_double_counting_coverage():
    edl = default_edl(40)
    edl["overlays"] = [{"id": "archive", "start": 4, "duration_s": 4}]
    edl["texts"] = [{"id": "source_credit", "start": 4, "end": 8}]
    edl["effects"] = {"zooms": [{"id": "gentle", "start": 20, "end": 25, "strength": .1}]}
    result = visual_coverage.measure(edl)
    assert result["counts_by_kind"] == {"overlay": 1, "text": 1, "zoom": 1}
    assert result["gaps_without_authored_treatment"] == [
        {"start": 0, "end": 4, "duration_s": 4},
        {"start": 8, "end": 20, "duration_s": 12},
        {"start": 25, "end": 40, "duration_s": 15}]
    # A new treatment changes the evidence and does not keep the old gap.
    changed = deepcopy(edl)
    changed["motion"] = [{"id": "fact", "start": 29, "end": 34}]
    assert visual_coverage.measure(changed)["counts_by_kind"]["motion"] == 1
    assert max(g["duration_s"] for g in visual_coverage.measure(changed)["gaps_without_authored_treatment"]) == 12
    changed["texts"].append({"id": "legacy_without_timing", "text": "Legacy"})
    assert visual_coverage.measure(changed)["unmeasured_items"] == [
        {"kind": "text", "id": "legacy_without_timing"}]


def test_authored_reading_card_is_not_a_frozen_source_or_black_frame():
    edl = default_edl(20)
    edl["vectors"] = [{"id": "panel", "kind": "rectangle", "start": 15, "end": 20,
                       "x": .5, "y": .5, "width": 1, "height": 1, "opacity": 1}]
    edl["texts"] = [{"id": "credit", "text": "Archive sources", "start": 15, "end": 20}]
    plan = {"program_s": 20, "tail": render_qc.tail_plan(edl)}
    cause = render_qc._stall_cause("held", 18.5, 20, plan)
    assert cause[0] == "graphic"
    assert not render_qc.stall_findings([["held", 18.5, 20, *cause]], plan)
    import motion_critic
    findings, evidence = motion_critic.lens_ending(
        "unused", edl, {}, final=False,
        report={"picture_qc": {"stall": [["held", 18.5, 20, *cause]]}})
    assert not findings
    assert evidence["stall"]
    old_row = ["held", 18.5, 20, "footage", "old source-motion advice"]
    cached = {"version": 7, "stall": [old_row],
              "findings": render_qc.stall_findings([old_row]) + ["Unrelated real defect"]}
    refreshed = render_qc.refresh_tail_classification(cached, edl)
    assert refreshed["findings"] == ["Unrelated real defect"]
    assert refreshed["stall"][0][3] == "graphic"
    assert cached["stall"][0][3] == "footage"
    assert render_qc.refresh_tail_classification(refreshed, edl) == refreshed
    assert render_qc._stall_cause("black", 18.5, 20, plan)[0] == "picture"
    assert render_qc._stall_cause("held", 19.5, 20.2, plan)[0] == "join"
    edl["texts"][0]["end"] = 18
    plan["tail"] = render_qc.tail_plan(edl)
    assert render_qc._stall_cause("held", 18.5, 20, plan)[0] == "footage"


def test_insert_measurement_uses_program_clock_after_prior_insert():
    edl = default_edl(10)
    edl["keep"] = [[0, 5], [5, 10]]
    edl["inserts"] = [{"id": "first", "at_output_s": 0, "duration_s": 3},
                      {"id": "second", "at_output_s": 5, "duration_s": 2}]
    result = visual_coverage.measure(edl)
    assert result["duration_s"] == 15
    assert [(r["start"], r["end"]) for r in result["placements"]] == [(0, 3), (8, 10)]
