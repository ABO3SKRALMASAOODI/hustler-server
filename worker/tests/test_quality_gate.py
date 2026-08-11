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
    findings = quality_gate.blocking_findings(previous, proposed)
    assert any("new" in x and "no measured visual target" in x
               for x in findings)

    # Delta-based: deleting an old blind zoom must never be blocked by the
    # fact that the saved EDL predates the rule.
    repaired = _with_zooms()
    assert quality_gate.blocking_findings(previous, repaired) == []


def test_unchanged_idless_legacy_hazard_does_not_block_unrelated_repair():
    legacy = _zoom(None)
    previous = _with_zooms(legacy)
    proposed = _with_zooms(dict(legacy))
    proposed["effects"]["grade"] = "warm"
    assert quality_gate.blocking_findings(previous, proposed) == []

    # A newly added id-less hazard is still a delta and must be refused.
    proposed["effects"]["zooms"].append(_zoom(None, 6.0, 7.0))
    assert any("no measured visual target" in finding for finding in
               quality_gate.blocking_findings(previous, proposed))


def test_aimed_zoom_passes_and_overlapping_magnification_does_not():
    previous = _with_zooms(_zoom("old", 2.0, 4.0, cx=0.4, cy=0.4))
    safe = _with_zooms(_zoom("old", 2.0, 4.0, cx=0.4, cy=0.4),
                       _zoom("new", 6.0, 7.0, cx=0.7, cy=0.4))
    assert quality_gate.blocking_findings(previous, safe) == []

    stacked = _with_zooms(_zoom("old", 2.0, 4.0, cx=0.4, cy=0.4),
                          _zoom("new", 3.0, 5.0, cx=0.7, cy=0.4))
    assert any("overlaps zoom old" in x
               for x in quality_gate.blocking_findings(previous, stacked))


def test_sfx_are_opt_in_and_cannot_be_stacked_into_one_muddy_hit():
    previous = default_edl(20.0)
    proposed = default_edl(20.0)
    proposed["sfx"] = [
        {"id": "sx1", "storage_key": "a.wav", "at": 5.0,
         "gain_db": -10.0},
        {"id": "sx2", "storage_key": "b.wav", "at": 5.2,
         "gain_db": -10.0},
    ]
    unasked = quality_gate.blocking_findings(previous, proposed,
                                              "make this edit nice")
    assert any("without an explicit sound-design request" in x for x in unasked)
    assert any("only 0.20s apart" in x for x in unasked)

    spaced = default_edl(20.0)
    spaced["sfx"] = [dict(proposed["sfx"][0]),
                     dict(proposed["sfx"][1], at=7.0)]
    assert quality_gate.blocking_findings(
        previous, spaced, "add sound effects to both visible clicks") == []


def test_selected_audio_requires_real_audition_unless_user_chose_it():
    class Ctx:
        enforce_spatial = True
        user_message = "add cinematic background music"
        _listened_asset_keys = set()

    ctx = Ctx()
    assert not agent_tools._audio_was_auditioned(
        ctx, "music/1/pick.mp3", "music/1/pick.mp3", "Good Track.mp3")
    ctx._pending_listened_asset_keys = {"music/1/pick.mp3"}
    assert not agent_tools._audio_was_auditioned(
        ctx, "music/1/pick.mp3", "music/1/pick.mp3", "Good Track.mp3")
    ctx._listened_asset_keys.add("music/1/pick.mp3")
    assert agent_tools._audio_was_auditioned(
        ctx, "music/1/pick.mp3", "music/1/pick.mp3", "Good Track.mp3")

    ctx.user_message = "go bring it and add it"
    assert agent_tools._audio_was_auditioned(
        ctx, "music/1/remix.mp3", "music/1/remix.mp3",
        "Drake - National Treasures (remix).mp3")

    ctx._listened_asset_keys.clear()
    ctx.user_message = "use this music I attached"
    assert agent_tools._audio_was_auditioned(
        ctx, "music/1/pick.mp3", "music/1/pick.mp3", "Good Track.mp3")

    ctx.user_message = "add a remix, it's fine"
    assert agent_tools._audio_was_auditioned(
        ctx, "music/1/remix.mp3", "music/1/remix.mp3",
        "Drake - National Treasures (remix).mp3")

    # Approval bypasses the editor's taste veto, not the user's literal order
    # to listen first. One real reviewer attempt then prevents an impossible
    # retry loop if the reviewer itself is temporarily unavailable.
    ctx.user_message = "listen to this exact remix first, then add it"
    assert not agent_tools._audio_was_auditioned(
        ctx, "music/1/remix.mp3", "music/1/remix.mp3",
        "Drake - National Treasures (remix).mp3")
    ctx._audio_review_attempted_asset_keys = {"music/1/remix.mp3"}
    assert agent_tools._audio_was_auditioned(
        ctx, "music/1/remix.mp3", "music/1/remix.mp3",
        "Drake - National Treasures (remix).mp3")


def test_asset_listen_wins_over_empty_optional_clock_arrays(monkeypatch,
                                                             tmp_path):
    """Regression for project 622's impossible audition loop.

    The model correctly supplied asset_key, but also emitted the optional
    output_times=[] schema field. Empty output_times must not route a project
    MP3 into the rendered-program branch.
    """
    class Ctx:
        agent_model = "hearing-model"
        direct_sight = True
        project_id = 622
        workdir = str(tmp_path)
        pending_audio = []
        _pending_listened_asset_keys = set()
        _asset_locals = {}

    asset = {
        "id": 41, "kind": "music", "storage_key": "fetched/622/remix.mp3",
        "duration_s": 120.0, "bytes": 1024,
        "meta": {"filename": "National Treasures remix.mp3"},
    }
    monkeypatch.setattr(agent_tools, "_hearing_on", lambda _ctx: True)
    monkeypatch.setattr(
        agent_tools, "_resolve_media_asset",
        lambda _ctx, _key, _kinds: (asset, None))
    monkeypatch.setattr(
        agent_tools, "_asset_local_path", lambda _ctx, _asset: "source.mp3")

    def fake_extract(_src, _start, _end, dest):
        with open(dest, "wb") as handle:
            handle.write(b"audition")

    monkeypatch.setattr(agent_tools.media, "extract_audio_clip", fake_extract)
    result = agent_tools.listen_to(
        Ctx(), times=[], output_times=[], asset_key=asset["storage_key"])
    assert result.startswith("Listening delivered:")
    assert "ASSET 'National Treasures remix.mp3'" in result


def test_dedicated_audio_reviewer_returns_real_judgment(monkeypatch,
                                                         tmp_path):
    class Ctx:
        agent_model = "text-image-only-model"
        direct_sight = True
        project_id = 622
        workdir = str(tmp_path)
        pending_audio = []
        _pending_listened_asset_keys = set()
        _listened_asset_keys = set()
        _audio_review_attempted_asset_keys = set()
        _asset_locals = {}
        last_audio_review = None

    asset = {
        "id": 42, "kind": "music", "storage_key": "music/622/remix.mp3",
        "duration_s": 120.0, "bytes": 1024,
        "meta": {"filename": "Approved remix.mp3"},
    }
    monkeypatch.setattr(agent_tools, "_hearing_on", lambda _ctx: False)
    monkeypatch.setattr(agent_tools.llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(
        agent_tools, "_resolve_media_asset",
        lambda _ctx, _key, _kinds: (asset, None))
    monkeypatch.setattr(
        agent_tools, "_asset_local_path", lambda _ctx, _asset: "source.mp3")

    def fake_extract(_src, _start, _end, dest):
        with open(dest, "wb") as handle:
            handle.write(b"review-me")

    seen = {}

    def fake_review(prompt, paths, labels, purpose):
        seen.update(prompt=prompt, paths=paths, labels=labels, purpose=purpose)
        return ("The remix is clean and energetic; start it at -18 dB with "
                "smooth speech ducking.")

    monkeypatch.setattr(agent_tools.media, "extract_audio_clip", fake_extract)
    monkeypatch.setattr(agent_tools.llm, "ask_audio", fake_review)
    ctx = Ctx()
    result = agent_tools.listen_to(
        ctx, times=[], output_times=[], asset_key=asset["storage_key"])
    assert result.startswith("Listening delivered and reviewed:")
    assert "clean and energetic" in result
    assert asset["storage_key"] in ctx._listened_asset_keys
    assert ctx.pending_audio == []
    assert seen["purpose"] == "audio_listen"


def test_program_listen_rebinds_a_stale_render_key_to_current_preview(
        monkeypatch, tmp_path):
    stale = {
        "id": 70, "kind": "render", "storage_key": "media/p/v2.mp4",
        "duration_s": 6.2, "bytes": 1024,
        "meta": {"edl_version": 2},
    }
    current = {
        "id": 71, "kind": "render", "storage_key": "media/p/v3.mp4",
        "duration_s": 6.2, "bytes": 1024,
        "meta": {"edl_version": 3},
    }

    class DB:
        def run(self, fn, *args):
            assert fn is dbx.get_asset and args == (71,)
            return current

    class Ctx:
        agent_model = "text-image-only-model"
        direct_sight = True
        project_id = 652
        workdir = str(tmp_path)
        pending_audio = []
        _pending_listened_asset_keys = set()
        _listened_asset_keys = set()
        _audio_review_attempted_asset_keys = set()
        _asset_locals = {}
        last_audio_review = None
        last_preview = {"edl_version": 3, "render_asset_id": 71,
                        "duration_s": 6.2}
        db = DB()

        def latest_edl(self):
            return {"version": 3, "json": default_edl(6.1)}

    used_assets = []
    monkeypatch.setattr(agent_tools, "_hearing_on", lambda _ctx: False)
    monkeypatch.setattr(agent_tools.llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(
        agent_tools, "_resolve_media_asset",
        lambda _ctx, _key, _kinds: (stale, None))

    def fake_local(_ctx, asset):
        used_assets.append(asset["id"])
        return "current-preview.mp4"

    def fake_extract(_src, _start, _end, dest):
        with open(dest, "wb") as handle:
            handle.write(b"current-mix")

    monkeypatch.setattr(agent_tools, "_asset_local_path", fake_local)
    monkeypatch.setattr(agent_tools.media, "extract_audio_clip", fake_extract)
    monkeypatch.setattr(
        agent_tools.llm, "ask_audio",
        lambda *_args, **_kwargs: "Music is audible and speech is clear.")

    result = agent_tools.listen_to(
        Ctx(), times=[0.5], output_times=[3.0],
        asset_key=stale["storage_key"], span_s=2.0)

    assert result.startswith("Listening delivered and reviewed:")
    assert "PROGRAM sound" in result
    assert "ASSET" not in result
    assert used_assets == [71]


def test_render_audio_review_cannot_pass_when_required_music_is_absent():
    edl = default_edl(6.1)
    edl["music"] = [{"id": "mus1", "storage_key": "music/x.mp3",
                     "start": 0.0, "end": 6.1, "gain_db": -18.0,
                     "duck": True}]
    prompt = agent_tools._render_audio_review_prompt(edl)
    assert "REQUIRED to contain music" in prompt
    assert "absent or effectively inaudible" in prompt
    assert "start with FIX, never PASS" in prompt


def test_user_approved_music_can_be_part_of_an_atomic_recipe(monkeypatch):
    ctx, fake = _real_ctx("go bring it and add it")
    monkeypatch.setattr(
        agent_tools, "_resolve_music",
        lambda _ctx, key: ({"name": "Approved remix.mp3",
                            "duration_s": 120.0, "storage_key": key}, None))
    monkeypatch.setattr(
        agent_tools, "_audio_was_auditioned", lambda *_args, **_kwargs: True)
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
               quality_gate.blocking_findings(previous, stacked))

    hierarchy = default_edl(20.0)
    hierarchy["texts"] = [
        {"id": "tx1", "text": "TITLE", "start": 2.0, "end": 5.0,
         "template": "title"},
        {"id": "tx2", "text": "SUBTITLE", "start": 2.0, "end": 5.0,
         "template": "subtitle"},
    ]
    assert quality_gate.blocking_findings(
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


def test_quality_gate_is_enforced_at_the_single_commit_boundary():
    ctx, fake = _real_ctx()
    edl = ctx.latest_edl()["json"]
    edl = {**edl, "effects": {"zooms": [_zoom("zm1")]}}
    result = ctx.write_edl(edl, "blind center punch")
    assert result.startswith("REJECTED BY QUALITY GATE")
    assert fake.inserts == 0 and ctx.latest_edl()["version"] == 1

    aimed = {**edl, "effects": {"zooms": [
        _zoom("zm1", cx=0.35, cy=0.42)]}}
    result = ctx.write_edl(aimed, "measured face punch")
    assert result.startswith("EDL v1 -> v2")
    assert fake.inserts == 1


def test_two_successful_previews_freeze_the_last_proven_edl():
    ctx, fake = _real_ctx()
    ctx.rendered_versions.update({1, 2})
    changed = dict(ctx.latest_edl()["json"])
    changed["frame"] = {"ratio": "9:16", "mode": "pad_blur"}
    result = ctx.write_edl(changed, "optional third-candidate polish")
    assert result.startswith("REJECTED")
    assert "unreviewed third candidate" in result
    assert fake.inserts == 0


def test_add_zoom_rejects_the_old_center_default_before_writing():
    class Ctx:
        duration = 20.0

        def latest_edl(self):
            return {"json": default_edl(20.0)}

        def write_edl(self, *_args):
            raise AssertionError("unsafe zoom reached write_edl")

    result = agent_tools.add_zoom(Ctx(), 2.0, 3.0)
    assert result.startswith("REJECTED") and "no visual target" in result


def test_real_agent_cannot_claim_guessed_zoom_coordinates_as_evidence():
    ctx, fake = _real_ctx()
    ctx._looked_output_times.clear()
    result = agent_tools.add_zoom(ctx, 8.0, 9.0, cx=0.5, cy=0.5)
    assert result.startswith("REJECTED")
    assert "look_at evidence" in result
    assert fake.inserts == 0

    # A look call in the same parallel tool batch is only pending; the model
    # has not received those pixels yet, so it still cannot aim from them.
    ctx._pending_looked_output_times.add(8.5)
    result = agent_tools.add_zoom(ctx, 8.0, 9.0, cx=0.5, cy=0.5)
    assert result.startswith("REJECTED") and fake.inserts == 0

    ctx._looked_output_times.add(8.5)
    result = agent_tools.add_zoom(ctx, 8.0, 9.0, cx=0.5, cy=0.5)
    assert result.startswith("EDL v1 -> v2")


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
        {"tool": "add_zoom", "args": {"start": 2.0, "end": 3.0}},
    ])
    assert result.startswith("RECIPE ABORTED")
    assert fake.inserts == 0
    assert fake.rows[-1]["json"].get("effects") is None


def test_edit_recipe_final_quality_gate_is_atomic():
    ctx, fake = _real_ctx()
    result = agent_tools.apply_edit_recipe(ctx, [
        {"tool": "add_zoom",
         "args": {"start": 2.0, "end": 4.0, "cx": .4, "cy": .4}},
        {"tool": "add_zoom",
         "args": {"start": 3.0, "end": 5.0, "cx": .7, "cy": .4}},
    ])
    assert result.startswith("REJECTED BY QUALITY GATE")
    assert fake.inserts == 0


def test_recipe_schema_is_exposed_to_the_agent_as_one_write_tool():
    tools = {t["function"]["name"]: t for t in agent_tools.openai_tools()}
    assert "apply_edit_recipe" in tools
    schema = tools["apply_edit_recipe"]["function"]["parameters"]
    assert schema["required"] == ["operations"]
    assert "apply_edit_recipe" in agent_tools.WRITE_TOOLS


def test_severe_dimension_change_cannot_guess_a_center_crop():
    ctx, fake = _real_ctx("make this vertical")
    result = agent_tools.set_frame(ctx, "9:16", "crop")
    assert result.startswith("REJECTED")
    assert "auto_reframe" in result and "discard" in result
    assert fake.inserts == 0

    # Literal intent is allowed; the safety rule must not argue with someone
    # who specifically chose the center rather than merely requesting 9:16.
    centered, centered_db = _real_ctx("use a center crop for the whole video")
    result = agent_tools.set_frame(centered, "9:16", "crop")
    assert result.startswith("EDL v1 -> v2")
    assert centered_db.inserts == 1


def test_binding_subject_aware_plan_cannot_collapse_to_uniform_fit():
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

    rejected = agent_tools.set_frame(ctx, "9:16", "pad_blur")
    assert rejected.startswith("REJECTED")
    assert "auto_reframe" in rejected and "uniform fit" in rejected
    assert fake.inserts == 0


def test_wide_then_closeup_request_cannot_use_uniform_frame_plus_zoom():
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

    rejected = agent_tools.set_frame(ctx, "9:16", "pad_blur", 0.5, 0.5)
    assert rejected.startswith("REJECTED")
    assert "shot-specific" in rejected and "auto_reframe" in rejected
    assert fake.inserts == 0


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


def test_unrelated_repair_cannot_erase_measured_per_shot_framing():
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
    assert result.startswith("REJECTED")
    assert "discard the existing measured per-shot focus_track" in result
    assert fake.inserts == 0
