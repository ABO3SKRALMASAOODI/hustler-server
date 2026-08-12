"""Regression gate for the edit defects that caused immediate churn."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import db as dbx
import quality_gate
from schemas import default_edl


def _zoom(zid, start=2.0, end=3.0, **extra):
    return {"id": zid, "start": start, "end": end, "strength": 0.15,
            **extra}


def _with_zooms(*items):
    edl = default_edl(20.0)
    edl["effects"] = {"zooms": list(items)}
    return edl


def test_new_zoom_needs_a_real_target_but_legacy_zoom_can_be_repaired():
    previous = _with_zooms(_zoom("legacy"))
    proposed = _with_zooms(_zoom("legacy"), _zoom("new", 5.0, 6.0))
    findings = quality_gate.advisory_findings(previous, proposed)
    assert any("new" in x and "no measured visual target" in x
               for x in findings)

    # Delta-based: deleting an old blind zoom must never be blocked by the
    # fact that the saved EDL predates the rule.
    repaired = _with_zooms()
    assert quality_gate.advisory_findings(previous, repaired) == []


def test_unchanged_idless_legacy_hazard_does_not_block_unrelated_repair():
    legacy = _zoom(None)
    previous = _with_zooms(legacy)
    proposed = _with_zooms(dict(legacy))
    proposed["effects"]["grade"] = "warm"
    assert quality_gate.advisory_findings(previous, proposed) == []

    # A newly added id-less hazard is still a delta and must be refused.
    proposed["effects"]["zooms"].append(_zoom(None, 6.0, 7.0))
    assert any("no measured visual target" in finding for finding in
               quality_gate.advisory_findings(previous, proposed))


def test_aimed_zoom_passes_and_overlapping_magnification_does_not():
    previous = _with_zooms(_zoom("old", 2.0, 4.0, cx=0.4, cy=0.4))
    safe = _with_zooms(_zoom("old", 2.0, 4.0, cx=0.4, cy=0.4),
                       _zoom("new", 6.0, 7.0, cx=0.7, cy=0.4))
    assert quality_gate.advisory_findings(previous, safe) == []

    stacked = _with_zooms(_zoom("old", 2.0, 4.0, cx=0.4, cy=0.4),
                          _zoom("new", 3.0, 5.0, cx=0.7, cy=0.4))
    assert any("overlaps zoom old" in x
               for x in quality_gate.advisory_findings(previous, stacked))


def test_sfx_permission_is_unrestricted_and_spacing_is_advisory():
    previous = default_edl(20.0)
    proposed = default_edl(20.0)
    proposed["sfx"] = [
        {"id": "sx1", "storage_key": "a.wav", "at": 5.0,
         "gain_db": -10.0},
        {"id": "sx2", "storage_key": "b.wav", "at": 5.2,
         "gain_db": -10.0},
    ]
    unasked = quality_gate.advisory_findings(previous, proposed,
                                              "make this edit nice")
    assert not any("explicit sound-design request" in x for x in unasked)
    assert any("only 0.20s apart" in x for x in unasked)

    spaced = default_edl(20.0)
    spaced["sfx"] = [dict(proposed["sfx"][0]),
                     dict(proposed["sfx"][1], at=7.0)]
    assert quality_gate.advisory_findings(
        previous, spaced, "add sound effects to both visible clicks") == []


def test_audio_audition_is_not_a_permission_helper():
    assert not hasattr(agent_tools, "_audio_was_auditioned")


def test_user_approved_music_can_be_part_of_an_atomic_recipe(monkeypatch):
    ctx, fake = _real_ctx("go bring it and add it")
    monkeypatch.setattr(
        agent_tools, "_resolve_music",
        lambda _ctx, key: ({"name": "Approved remix.mp3",
                            "duration_s": 120.0, "storage_key": key}, None))
    result = agent_tools.apply_edit_recipe(ctx, [
        {"tool": "set_frame",
         "args": {"ratio": "9:16", "mode": "pad_blur"}},
        {"tool": "add_music",
         "args": {"storage_key": "fetched/622/remix.mp3"}},
    ], brief="fit frame and add the approved remix")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1
    assert fake.rows[-1]["json"]["music"][0]["storage_key"] == \
        "fetched/622/remix.mp3"


def test_independent_text_layers_cannot_stack_but_title_hierarchy_can():
    previous = default_edl(20.0)
    previous["texts"] = [{"id": "tx1", "text": "OLD", "start": 2.0,
                          "end": 5.0, "template": "callout"}]
    stacked = default_edl(20.0)
    stacked["texts"] = list(previous["texts"]) + [
        {"id": "tx2", "text": "NEW", "start": 3.0, "end": 4.0,
         "template": "big_number"}]
    assert any("Two independent word layers" in finding for finding in
               quality_gate.advisory_findings(previous, stacked))

    hierarchy = default_edl(20.0)
    hierarchy["texts"] = [
        {"id": "tx1", "text": "TITLE", "start": 2.0, "end": 5.0,
         "template": "title"},
        {"id": "tx2", "text": "SUBTITLE", "start": 2.0, "end": 5.0,
         "template": "subtitle"},
    ]
    assert quality_gate.advisory_findings(
        default_edl(20.0), hierarchy) == []


class _Db:
    def __init__(self):
        self.rows = [{"version": 1, "json": default_edl(20.0)}]
        self.inserts = 0

    def run(self, fn, *args):
        if fn is dbx.latest_edl:
            return self.rows[-1]
        if fn is dbx.insert_edl:
            self.inserts += 1
            row = {"version": self.rows[-1]["version"] + 1, "json": args[1]}
            self.rows.append(row)
            return row["version"]
        if fn is dbx.get_edl_version:
            return next(x for x in self.rows if x["version"] == args[1])
        raise AssertionError(f"unexpected DB call: {fn}")


def _real_ctx(message="make it engaging"):
    fake = _Db()
    ctx = agent_tools.ToolContext(
        fake, {"id": 9, "user_id": 3},
        {"id": 7, "chat_session_id": 11},
        {"video": {"duration": 20.0, "width": 1920, "height": 1080,
                   "fps": 30.0}, "words": []},
        tempfile.mkdtemp())
    ctx.user_message = message
    # Tests that stage aimed zooms model the exact-frame observation required
    # of the production agent. Missing-target tests still fail independently.
    ctx._looked_output_times.add(2.5)
    ctx._looked_output_times.add(3.5)
    ctx._looked_output_times.add(4.5)
    return ctx, fake


def test_quality_findings_are_reported_after_the_single_commit_boundary():
    ctx, fake = _real_ctx()
    edl = ctx.latest_edl()["json"]
    edl = {**edl, "effects": {"zooms": [_zoom("zm1")]}}
    result = ctx.write_edl(edl, "blind center punch")
    assert result.startswith("EDL v1 -> v2")
    assert "QUALITY ADVISORY" in result
    assert fake.inserts == 1 and ctx.latest_edl()["version"] == 2

    aimed = {**edl, "effects": {"zooms": [
        _zoom("zm1", cx=0.35, cy=0.42)]}}
    result = ctx.write_edl(aimed, "measured face punch")
    assert result.startswith("EDL v2 -> v3")
    assert fake.inserts == 2


def test_successful_previews_never_freeze_a_requested_edl_write():
    ctx, fake = _real_ctx()
    ctx.rendered_versions.update({1, 2, 3, 4})
    changed = dict(ctx.latest_edl()["json"])
    changed["frame"] = {"ratio": "9:16", "mode": "pad_blur"}
    result = ctx.write_edl(changed, "requested direct replacement")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1


def test_additional_writes_do_not_need_reviewer_permission():
    ctx, fake = _real_ctx()
    ctx.rendered_versions.update({1, 2, 3})
    ctx.last_taste = []
    changed = dict(ctx.latest_edl()["json"])
    changed["frame"] = {"ratio": "9:16", "mode": "pad_blur"}
    result = ctx.write_edl(changed, "direct user-requested replacement")
    assert result.startswith("EDL v1 -> v2")
    again = dict(ctx.latest_edl()["json"])
    again["effects"] = {"grade": "warm"}
    result = ctx.write_edl(again, "follow-up requested adjustment")
    assert result.startswith("EDL v2 -> v3")
    assert fake.inserts == 2


def test_manual_preview_has_no_fixed_per_turn_candidate_ceiling(
        monkeypatch, tmp_path):
    class PreviewDb:
        def run(self, fn, *_args):
            if fn is dbx.get_or_enqueue_preview_job:
                return 71, True
            if fn is dbx.get_job:
                return {"state": "done", "result": {
                    "duration_s": 20.0, "audio_qc": {}}}
            raise AssertionError(f"unexpected DB call: {fn}")

    class Ctx:
        project_id = 9
        job = {"id": 70, "user_id": 3}
        duration = 20.0
        db = PreviewDb()
        workdir = str(tmp_path)
        index = {"video": {"width": 1920, "height": 1080}, "words": []}
        user_message = "replace that zoom directly"
        rendered_versions = set(range(1, 22))
        failed_preview_versions = {}
        spec_preview_jobs = {}
        last_preview = None
        last_visual_critic = None
        last_audio_review = None
        last_audio_qc_findings = []
        last_taste = []
        last_taste_version = None
        last_selfcheck = None
        audio_reviewed_versions = set()

        @staticmethod
        def latest_edl():
            return {"version": 22, "json": default_edl(20.0)}

        @staticmethod
        def preview_limit():
            raise AssertionError("the removed preview lock was consulted")

    monkeypatch.setattr(agent_tools, "_grade_strip_shortcut",
                        lambda *_args: None)
    monkeypatch.setattr(agent_tools, "_verify_plan_for", lambda *_args: None)
    monkeypatch.setattr(agent_tools, "_queue_check_frames",
                        lambda *_args: False)
    monkeypatch.setattr(agent_tools, "_independent_preview_review",
                        lambda *_args: None)
    monkeypatch.setattr(agent_tools, "_self_check", lambda *_args: None)
    monkeypatch.setattr(agent_tools.taste, "critique", lambda *_args, **_kw: [])
    monkeypatch.setattr(agent_tools.time, "sleep", lambda *_args: None)

    result = agent_tools.render_preview(Ctx())
    assert result.startswith("Preview v22 rendered:")
    assert 22 in Ctx.rendered_versions


def test_add_zoom_uses_center_default_and_advises_after_writing():
    class Ctx:
        duration = 20.0

        def latest_edl(self):
            return {"json": default_edl(20.0)}

        def write_edl(self, edl, _desc):
            self.written = edl
            return "EDL v1 -> v2"

    result = agent_tools.add_zoom(Ctx(), 2.0, 3.0)
    assert result.startswith("EDL v1 -> v2")
    assert "QUALITY ADVISORY" in result


def test_real_agent_can_commit_zoom_coordinates_without_look_evidence():
    ctx, fake = _real_ctx()
    ctx._looked_output_times.clear()
    result = agent_tools.add_zoom(ctx, 8.0, 9.0, cx=0.5, cy=0.5)
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1


def test_source_face_target_maps_through_a_vertical_crop():
    class Ctx:
        index = {"video": {"width": 1920, "height": 1080}}

    edl = default_edl(20.0)
    edl["frame"] = {"ratio": "9:16", "mode": "crop",
                    "focus_x": 0.75, "focus_y": 0.5}
    # A face at the crop focus maps to the middle of the output, while a face
    # on the discarded left side is correctly refused.
    mapped = agent_tools._source_point_to_output(Ctx(), edl, 3.0, (0.75, 0.4))
    assert mapped is not None and 0.45 <= mapped[0] <= 0.55
    assert agent_tools._source_point_to_output(
        Ctx(), edl, 3.0, (0.05, 0.4)) is None


def test_edit_recipe_commits_multiple_safe_moves_as_one_version():
    ctx, fake = _real_ctx()
    result = agent_tools.apply_edit_recipe(ctx, [
        {"tool": "set_frame",
         "args": {"ratio": "9:16", "mode": "pad_blur"}},
        {"tool": "set_color_grade", "args": {"preset": "warm"}},
    ], brief="measured vertical talking-head treatment")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1
    assert fake.rows[-1]["json"]["frame"]["ratio"] == "9:16"
    assert fake.rows[-1]["json"]["effects"]["grade"] == "warm"
    assert ctx.edit_plan["brief"] == "measured vertical talking-head treatment"


def test_existing_audio_gain_is_transaction_safe_recipe_work():
    assert "set_audio_gain" in agent_tools.RECIPE_TOOLS


def test_common_repair_moves_are_transaction_safe_recipe_work():
    for name in ("add_text", "remove_text", "set_insert_window",
                 "remove_insert", "add_overlay", "remove_overlay",
                 "enhance_video", "add_custom_filter", "beat_align_cuts"):
        assert name in agent_tools.RECIPE_TOOLS


def test_dispatch_normalizes_obvious_tool_argument_aliases(monkeypatch):
    seen = {}

    def fake_overlay(_ctx, **kwargs):
        seen.update(kwargs)
        return "ok"

    original = agent_tools.TOOLS["add_overlay"]
    monkeypatch.setitem(agent_tools.TOOLS, "add_overlay",
                        (fake_overlay, original[1], original[2]))
    ctx, _fake = _real_ctx()
    assert agent_tools.execute(
        ctx, "add_overlay", {"asset_key": "x", "start": 1,
                             "duration": 2}) == "ok"
    assert seen["duration_s"] == 2 and "duration" not in seen


def test_structured_edit_brief_survives_atomic_execution():
    ctx, _fake = _real_ctx()
    planned = agent_tools.set_edit_plan(
        ctx,
        ["preserve the full gameplay frame", "apply a restrained finish"],
        brief="clean gameplay highlight",
        format="gameplay montage",
        intent="make the win readable without hiding the HUD",
        style_family="clean high-energy",
        must_keep=["HUD", "winning move"],
        must_avoid=["blind center crop", "decorative SFX"],
    )
    assert planned.startswith("Plan recorded")
    result = agent_tools.apply_edit_recipe(ctx, [
        {"tool": "set_frame",
         "args": {"ratio": "9:16", "mode": "pad_blur"}},
        {"tool": "set_color_grade", "args": {"preset": "vibrant"}},
    ])
    assert result.startswith("EDL v1 -> v2")
    assert ctx.edit_plan["format"] == "gameplay montage"
    assert ctx.edit_plan["must_keep"] == ["HUD", "winning move"]
    assert ctx.edit_plan["must_avoid"] == ["blind center crop",
                                            "decorative SFX"]
    assert ctx.edit_plan["steps"] == [
        "preserve the full gameplay frame", "apply a restrained finish"]
    assert ctx.edit_plan["completed_tools"] == [
        "set_frame", "set_color_grade"]


def test_edit_recipe_aborts_every_staged_move_on_late_rejection():
    ctx, fake = _real_ctx()
    result = agent_tools.apply_edit_recipe(ctx, [
        {"tool": "set_color_grade", "args": {"preset": "warm"}},
        {"tool": "set_color_grade", "args": {"preset": "not-a-grade"}},
    ])
    assert result.startswith("RECIPE ABORTED")
    assert fake.inserts == 0
    assert fake.rows[-1]["json"].get("effects") is None


def test_edit_recipe_quality_advisories_do_not_abort_atomic_commit():
    ctx, fake = _real_ctx()
    result = agent_tools.apply_edit_recipe(ctx, [
        {"tool": "add_zoom",
         "args": {"start": 2.0, "end": 4.0, "cx": .4, "cy": .4}},
        {"tool": "add_zoom",
         "args": {"start": 3.0, "end": 5.0, "cx": .7, "cy": .4}},
    ])
    assert result.startswith("EDL v1 -> v2")
    assert "QUALITY ADVISORY" in result
    assert fake.inserts == 1


def test_recipe_schema_is_exposed_to_the_agent_as_one_write_tool():
    tools = {t["function"]["name"]: t for t in agent_tools.openai_tools()}
    assert "apply_edit_recipe" in tools
    schema = tools["apply_edit_recipe"]["function"]["parameters"]
    assert schema["required"] == ["operations"]
    names = schema["properties"]["operations"]["items"]["properties"] \
        ["tool"]["enum"]
    assert set(names) == set(agent_tools.RECIPE_TOOLS)
    assert "apply_edit_recipe" in agent_tools.WRITE_TOOLS


def test_severe_dimension_change_can_use_editor_chosen_center_crop():
    ctx, fake = _real_ctx("make this vertical")
    result = agent_tools.set_frame(ctx, "9:16", "crop")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1

    # Literal intent is allowed; the safety rule must not argue with someone
    # who specifically chose the center rather than merely requesting 9:16.
    centered, centered_db = _real_ctx("use a center crop for the whole video")
    result = agent_tools.set_frame(centered, "9:16", "crop")
    assert result.startswith("EDL v1 -> v2")
    assert centered_db.inserts == 1


def test_recorded_plan_does_not_block_uniform_fit():
    ctx, fake = _real_ctx(
        "Keep the wide two-person shot visible, then intentionally frame "
        "the close-up for a vertical social clip.")
    agent_tools.set_edit_plan(
        ctx,
        ["Reframe to 9:16 with automatic subject-aware framing.",
         "Keep the wide composition and tightly compose the close-up."],
        brief="Shot-specific vertical treatment",
        intent="A deliberate composition for each shot",
    )

    result = agent_tools.set_frame(ctx, "9:16", "pad_blur")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1


def test_wide_then_closeup_request_does_not_withhold_uniform_frame_tool():
    """Production project 641: measured zoom did not repair global padding."""
    ctx, fake = _real_ctx(
        "Keep the wide two-person shot fully visible, then make the close-up "
        "feel intentionally framed. Use at most one measured subtle zoom.")
    agent_tools.set_edit_plan(
        ctx,
        ["Fit the full wide interview into vertical.",
         "Use the inspected close-up framing to decide on one subtle zoom."],
        brief="Preserve the wide setup and make the close-up deliberate",
        intent="Full context first, intentional close-up finish",
    )

    result = agent_tools.set_frame(ctx, "9:16", "pad_blur", 0.5, 0.5)
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1


def test_literal_whole_program_fit_overrides_subject_aware_plan_guard():
    ctx, fake = _real_ctx(
        "Fit every shot and keep every frame fully visible; never crop any "
        "shot.")
    agent_tools.set_edit_plan(
        ctx, ["Try automatic subject-aware framing."],
        brief="Uniform lossless vertical fit")
    fake.rows[-1]["json"]["frame"] = {
        "ratio": "9:16", "mode": "crop", "focus_x": 0.5,
        "focus_y": 0.4,
        "focus_track": [
            {"t0": 0.0, "t1": 10.0, "x": 0.4, "y": 0.4,
             "mode": "pad_blur"},
            {"t0": 10.0, "t1": 20.0, "x": 0.7, "y": 0.4,
             "mode": "crop"},
        ],
    }

    result = agent_tools.set_frame(ctx, "9:16", "pad_blur")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1


def test_editor_can_replace_measured_per_shot_framing():
    ctx, fake = _real_ctx("make the captions more consistent")
    fake.rows[-1]["json"]["frame"] = {
        "ratio": "9:16", "mode": "crop", "focus_x": 0.55,
        "focus_y": 0.25,
        "focus_track": [
            {"t0": 0.0, "t1": 15.0, "x": 0.5, "y": 0.3,
             "mode": "pad_blur"},
            {"t0": 15.0, "t1": 20.0, "x": 0.7, "y": 0.3,
             "mode": "crop"},
        ],
    }

    result = agent_tools.set_frame(ctx, "9:16", "pad_blur")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1
    assert not fake.rows[-1]["json"]["frame"].get("focus_track")
