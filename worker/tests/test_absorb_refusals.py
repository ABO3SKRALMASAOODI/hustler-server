"""A call whose meaning is clear works (census of 7 Nov 2026, account 2855).

Of 424 customer tool refusals in 14 days, 48 were right to refuse, and each
refusal cost a full model call. One test per class in
.valmera/week-1106/refusals/classes.json; the class name is in the test name.
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import edit_batch                                             # noqa: E402
import edl_read                                               # noqa: E402
import motion_tools                                           # noqa: E402
import quality_verifier                                       # noqa: E402
import question_need                                          # noqa: E402
import tool_dialect                                           # noqa: E402
import tool_outcome                                           # noqa: E402
from schemas import default_edl                               # noqa: E402


def fx_edl(duration=60.0):
    edl = default_edl(duration)
    edl["effects"] = {}
    return edl


def norm(name, **args):
    return agent_tools._normalize_tool_call(name, args)


def ctx_with(edl=None, assets=()):
    row = {"version": 1, "json": edl or default_edl(60.0)}
    by_key = {a["storage_key"]: a for a in assets}

    def run(fn, *args):
        name = getattr(fn, "__name__", "")
        if name == "asset_by_key":
            return by_key.get(args[-1])
        if name == "assets_by_kinds":
            return list(assets)
        return None
    ctx = SimpleNamespace(
        latest_edl=lambda: row, project_id=1, db=SimpleNamespace(run=run),
        job={"id": 9, "user_id": 2}, project={}, duration=60.0, index={},
        versions_written=[], pending_images=[], _call_notes=[])

    def write(value, _description):
        row.update(version=row["version"] + 1, json=value)
        return f"EDL v{row['version']} saved"
    ctx.write_edl = write
    return ctx


def batch(edl, operations, duration=60.0, keys=()):
    notes = []
    return edit_batch.apply_batch(edl, operations, duration, list(keys),
                                  notes=notes), notes


# ── bad-arguments: names and types, from the tool's own signature ──

def test_bad_arguments_a_name_the_tool_does_not_take_is_dropped_and_said():
    name, args, notes = norm("set_junction", style="dissolve", duration=0.6,
                             cuts=["scene"], scope="scene")
    assert name == "set_junction" and "scope" not in args
    assert any("scope ignored" in n for n in notes)


@pytest.mark.parametrize("tool,sent,field", [
    ("find_silences", {"min_silence_s": "1.5"}, "min_seconds"),
    ("find_footage", {"topic": "a laptop"}, "query"),
    ("remove_text", {"text_id": "tx1"}, "id"),
    ("get_edl", {"section": "motion"}, "sections"),
    ("set_caption_mutes", {"mutes": "[[780.93, 781.14]]"}, "spans"),
])
def test_bad_arguments_a_spelling_reaches_the_real_argument(tool, sent, field):
    _name, args, _notes = norm(tool, **sent)
    assert field in args


def test_bad_arguments_numbers_and_arrays_arrive_as_their_type():
    _n, args, _ = norm("find_silences", min_seconds="1.5")
    assert args["min_seconds"] == 1.5
    _n, args, _ = norm("add_zoom", start="1.5", end="2", cx="0.4", cy=0.5)
    assert args["start"] == 1.5 and args["cx"] == 0.4


def test_stringified_array_is_read_as_its_array():
    _n, args, notes = norm("remove_punctuation", ids='["a", "b"]') \
        if "ids" in agent_tools.TOOLS["remove_punctuation"][2] else \
        norm("set_caption_mutes", spans='[[1, 2]]')
    assert isinstance(next(iter(args.values())), list)


def test_batch_stringified_ops_are_read_as_operations():
    _n, args, _ = norm("apply_edit_batch", base_version=1, operation_id="x" * 16,
                       operations='[{"action": "set", "layer": "keep", "value": [[0, 5]]}]')
    assert args["operations"][0]["layer"] == "keep"


def test_bad_arguments_a_real_gap_gets_the_exact_call_and_runs_nothing():
    text = tool_dialect.exact_call(
        "set_volume", agent_tools.TOOLS["set_volume"][0],
        agent_tools.TOOLS["set_volume"][2])
    assert text.startswith("set_volume(start=<number>, end=<number>, gain_db=")
    assert tool_dialect.missing(agent_tools.TOOLS["set_volume"][0], {}) == [
        "start", "end", "gain_db"]


def test_bad_arguments_ids_on_a_one_id_tool_runs_once_for_each(monkeypatch):
    seen = []
    fn = agent_tools.TOOLS["remove_text"]
    monkeypatch.setitem(agent_tools.TOOLS, "remove_text",
                        (lambda ctx, id: seen.append(id) or "EDL v2 ok",
                         fn[1], fn[2]))
    ctx = ctx_with()
    assert agent_tools._fan_out_ids(ctx, "remove_text", {"ids": '["a", "b"]'}) \
        is not None
    assert seen == ["a", "b"]


def test_motion_enum_none_on_an_optional_enum_means_leave_it_out():
    _n, args, notes = norm("insert_media", asset_key="clips/1/a.mp4",
                           at_output_s=1, motion="none")
    assert args.get("motion") in (None, "none")


def test_enum_case_and_separator_fold():
    _n, args, notes = norm("add_zoom", start=0, end=1, mode="Push-In")
    assert args["mode"] == "push_in" and notes


# ── batch: one shape for every layer ──

def test_batch_enum_none_clears_the_grade():
    edl = fx_edl(60.0)
    edl["effects"]["grade"] = "warm"
    out, notes = batch(edl, [{"action": "upsert", "layer": "effects",
                              "value": {"grade": "none"}}])
    assert not (out["effects"] or {}).get("grade") and notes


def test_batch_upsert_effects_patches_its_fields():
    edl = fx_edl(60.0)
    edl["effects"]["grade"] = "warm"
    out, _ = batch(edl, [{"action": "upsert", "layer": "effects",
                          "value": {"fade_in_s": 0.5}}])
    assert out["effects"]["fade_in_s"] == 0.5
    assert out["effects"]["grade"] == "warm"


def test_batch_upsert_effects_with_a_field_named_as_id():
    out, notes = batch(default_edl(60.0), [{
        "action": "upsert", "layer": "effects", "id": "transition",
        "value": {"style": "dip_white", "duration_s": 0.4}}])
    assert out["effects"]["transition"]["style"] == "dip_white"


def test_batch_effects_given_as_a_list_of_kinds():
    out, _ = batch(default_edl(60.0), [{
        "action": "set", "layer": "effects", "value": [
            {"kind": "grade", "preset": "cinematic"}]}])
    assert out["effects"]["grade"] == "cinematic"


def test_batch_captions_off_clears_them():
    edl = default_edl(60.0)
    edl["captions"] = {"mode": "from_transcript"}
    out, notes = batch(edl, [{"action": "set", "layer": "captions",
                              "value": {"mode": "off", "items": [], "style": {}}}])
    assert not out["captions"] and notes


def test_batch_captions_items_become_their_list():
    out, _ = batch(default_edl(60.0), [{
        "action": "set", "layer": "captions", "value": {
            "mode": "items",
            "items": [{"start": 1.0, "end": 2.1, "text": "WE REBUILT."}]}}])
    assert out["captions"][0]["text"] == "WE REBUILT."


def test_batch_op_shape_fields_at_the_top_of_an_operation():
    edl = default_edl(60.0)
    edl["overlays"] = [{"id": "ov2", "kind": "image", "asset_key": "a.png",
                        "start": 1.0, "duration_s": 3.0}]
    out, _ = batch(edl, [{"id": "ov2", "layer": "overlays", "action": "upsert",
                          "start": 14.0, "duration_s": 2.6}])
    assert out["overlays"][0]["start"] == 14.0


def test_batch_op_shape_other_spellings_of_the_keys():
    out, _ = batch(default_edl(60.0), [{"op": "replace", "target": "keep",
                                        "data": [[0, 5]]}])
    assert out["keep"] == [[0.0, 5.0]]


def test_batch_shape_other_unknown_field_is_left_out_and_said():
    edl = default_edl(60.0)
    edl["texts"] = []
    out, notes = batch(edl, [{"action": "set", "layer": "texts", "value": [{
        "id": "tx1", "text": "hi", "start": 0, "end": 2, "type": "title"}]}])
    assert "type" not in out["texts"][0]
    assert any("type" in n for n in notes)


def test_batch_shape_other_motion_set_as_one_object():
    out, _ = batch(default_edl(60.0), [{"action": "set", "layer": "motion",
                                        "value": {}}])
    assert out["motion"] == [] or out["motion"] is None


def test_batch_shape_other_canvas_from_a_ratio():
    op = edit_batch._fit_operation(
        {"layer": "canvas", "action": "set",
         "value": {"ratio": "9:16", "background": "#000000"}}, {}, [])
    assert op["value"] == {"width": 1080, "height": 1920, "bg_color": "#000000"}


def test_batch_patches_can_be_removed_by_id():
    edl = default_edl(60.0)
    edl["patches"] = []
    op = edit_batch._fit_operation({"id": "er2", "layer": "patches",
                                    "action": "remove"}, edl, [])
    assert op["layer"] in edit_batch.LAYERS


def test_batch_insert_off_boundary_snaps_within_a_second():
    edl = default_edl(60.0)
    edl["keep"] = [[0.0, 35.61], [40.0, 60.0]]
    out, notes = batch(edl, [{"action": "upsert", "layer": "inserts",
                              "id": "ins1", "value": {
        "kind": "image", "asset_key": "cards/a.png", "at_output_s": 35.0,
        "duration_s": 2.0}}], keys=["cards/a.png"])
    assert out["inserts"][0]["at_output_s"] == 35.61 and notes


def test_batch_insert_off_boundary_splits_the_keep_past_that():
    edl = default_edl(60.0)
    edl["keep"] = [[0.0, 61.0]]
    edl["keep"] = [[0.0, 50.0]]
    out, notes = batch(edl, [{"action": "upsert", "layer": "inserts",
                              "id": "ins1", "value": {
        "kind": "image", "asset_key": "cards/a.png", "at_output_s": 20.0,
        "duration_s": 2.0}}], keys=["cards/a.png"])
    assert out["keep"] == [[0.0, 20.0], [20.0, 50.0]]
    assert out["inserts"][0]["at_output_s"] == 20.0 and notes


def test_batch_keep_overlap_merges():
    out, notes = batch(default_edl(60.0), [{"action": "set", "layer": "keep",
        "value": [[0.0, 4.0], [0.0, 4.0], [10.0, 20.0], [15.0, 22.0]]}])
    assert out["keep"] == [[0.0, 4.0], [10.0, 22.0]] and notes


def test_end_past_program_in_a_batch_keep_is_held_to_the_source():
    out, notes = batch(default_edl(49.78), [{"action": "set", "layer": "keep",
        "value": [[0.0, 20.0], [30.0, 53.5]]}], duration=49.78)
    assert out["keep"][-1][1] == 49.78 and notes


def test_batch_items_past_end_are_ended_or_removed_and_named():
    edl = fx_edl(40.0)
    edl["effects"]["zooms"] = [
        {"id": "zm6", "start": 20.0, "end": 28.83, "strength": 1.2},
        {"id": "zm7", "start": 33.0, "end": 34.69, "strength": 1.2}]
    out, notes = batch(edl, [{"action": "set", "layer": "keep",
                              "value": [[0.0, 26.5]]}], duration=40.0)
    zooms = {z["id"]: z for z in out["effects"]["zooms"]}
    assert zooms["zm6"]["end"] == 26.5 and "zm7" not in zooms
    assert any("zm7" in n for n in notes) and any("zm6" in n for n in notes)


def test_batch_text_motion_entrance_drops_the_entrance():
    out, notes = batch(default_edl(60.0), [{"action": "set", "layer": "texts",
        "value": [{"id": "tx1", "text": "hi", "start": 0, "end": 2,
                   "entrance": "fade",
                   "motion": {"opacity": [{"t": 0, "v": 0}, {"t": 1, "v": 1}]}}]}])
    assert out["texts"][0]["entrance"] in (None, "none") and notes


def test_batch_overlay_none_entrance_and_missing_kind():
    out, _ = batch(default_edl(60.0), [{"action": "set", "layer": "overlays",
        "value": [{"id": "ov1", "asset_key": "clips/1/a.mov", "start": 0,
                   "duration_s": 2, "entrance": "none", "exit": "none"}]}],
        keys=["clips/1/a.mov"])
    assert out["overlays"][0]["kind"] == "video"
    assert out["overlays"][0]["entrance"] is None


def test_batch_insert_clip_start_and_motion_none():
    out, _ = batch(default_edl(60.0), [{"action": "set", "layer": "inserts",
        "value": [{"id": "i1", "asset_key": "clips/1/a.mov", "at_output_s": 0,
                   "duration_s": 2, "clip_start_s": 3.0, "motion": "none",
                   "fit": "cover"}]}], keys=["clips/1/a.mov"])
    assert out["inserts"][0]["source_start_s"] == 3.0
    assert out["inserts"][0]["motion"] is None
    assert out["inserts"][0]["fit"] == "crop"


# ── tools ──

def test_zoom_needs_both_takes_the_frames_centre_and_says_so(monkeypatch):
    ctx = ctx_with(fx_edl())
    edl = ctx.latest_edl()["json"]
    edl["effects"]["zooms"] = []
    monkeypatch.setattr(agent_tools, "_write_zooms", lambda *a, **k: "EDL v2",
                        raising=False)
    out = agent_tools.add_zoom(ctx, 1.0, 3.0, strength=1.2, cy=0.4)
    assert "needs BOTH" not in str(out)
    assert any("cx set to 0.5" in n for n in ctx._call_notes)


def test_junction_style_invalid_speaks_set_transitions_dialect():
    from tools_ext import punctuation_tools as pt
    assert pt._JUNCTION_DIALECT["dip_white"] in \
        __import__("fx_schemas.accents", fromlist=["x"]).JUNCTION_STYLES
    assert pt._JUNCTION_DIALECT["cross_dissolve"] == "dissolve"


def test_motif_invalid_never_stops_an_edit():
    ctx = ctx_with()
    assert agent_tools._motion_motif_value(ctx, "Slow Push In!") == (None, None)
    assert agent_tools._motion_motif_value(ctx, "hold") == (None, None)
    assert agent_tools._motion_motif_value(ctx, "") == (None, None)
    assert ctx._call_notes


def test_motion_id_exists_changes_the_graphic(monkeypatch):
    seen = {}
    monkeypatch.setattr(motion_tools, "set_motion_graphic",
                        lambda ctx, id, **kw: seen.update(id=id, **kw) or "EDL v2")
    edl = default_edl(60.0)
    edl["motion"] = [{"id": "headline", "template": "word_slam", "start": 0,
                      "end": 2, "params": {"text": "A"}}]
    ctx = ctx_with(edl)
    out = motion_tools.add_motion_graphic(
        ctx, "word_slam", 0, 2, params={"text": "B"}, id="headline")
    assert out == "EDL v2" and seen["params"] == {"text": "B"}


def test_unknown_tool_name_loads_the_good_names():
    ctx = ctx_with()
    out = agent_tools.load_tools(ctx, names=["add_zoom", "no_such_tool"])
    assert "add_zoom" in out and ctx._loaded_tool_names == {"add_zoom"}
    assert any("no_such_tool" in n for n in ctx._call_notes)


def test_get_edl_a_large_page_answers_with_a_page():
    edl = fx_edl(60.0)
    edl["effects"]["zooms"] = [{"id": f"zm{i}", "start": i * 0.1,
                                "end": i * 0.1 + 0.1, "strength": 1.2,
                                "mode": "ease", "purpose": "x" * 80}
                               for i in range(400)]
    text = edl_read.read_edl({"version": 3, "json": edl}, 60.0, [],
                             sections=["effects"])
    page = json.loads(text)
    assert "error" not in page
    assert page["sections"]["effects"]["zooms"]
    assert page["pagination"]["effects.zooms"]["total"] == 400
    nxt = json.loads(edl_read.read_edl(
        {"version": 3, "json": edl}, 60.0, [], sections=["effects.zooms"],
        offset=page["pagination"]["effects.zooms"]["next_offset"], limit=5))
    assert len(nxt["sections"]["effects.zooms"]) == 5


def test_list_motion_templates_reads_one_template():
    _n, args, notes = norm("list_motion_templates", template="word_slam")
    assert args == {"template": "word_slam"} and not notes


def test_justify_unknown_finding_named_by_its_sentence():
    row = {"finding_id": "vf_1", "code": "dead_air",
           "message": "dead air: 67.7-69.8s is silent for 2.1s"}
    assert quality_verifier._quoted_finding(
        row, {"dead air: 67.7-69.8s is silent for 2.1s — tighten"})
    assert not quality_verifier._quoted_finding(row, {"other"})


def test_outcome_false_failure_a_quoted_word_is_not_a_failure():
    text = 'Frames for "why did the render fail" and "it failed" attached.'
    assert tool_outcome.from_legacy(text).status == "success"


def test_no_change_is_counted_as_no_change():
    out = tool_outcome.from_legacy("NO CHANGE: nothing there.")
    assert out.status == "success" and out.evidence["no_change"] is True


def test_muted_captions_finding_can_clear():
    edl = default_edl(60.0)
    edl["captions"] = {"mode": "from_transcript"}
    edl["caption_mutes"] = [[0.0, 30.0], [30.0, 60.0]]
    assert quality_verifier._captions_all_muted(edl, {"duration_s": 60.0})
    edl["caption_mutes"] = [[0.0, 30.0]]
    assert not quality_verifier._captions_all_muted(edl, {"duration_s": 60.0})


# ── keys ──

def test_asset_key_trailing_dot_is_our_own_full_stop():
    assert tool_dialect.clean_key("clips/5/a.mp4.") == "clips/5/a.mp4"
    assert tool_dialect.clean_key("`clips/5/a.mp4`,") == "clips/5/a.mp4"


def test_asset_key_unknown_one_token_off_reaches_the_project_key():
    ctx = ctx_with(assets=[{"storage_key": "clips/5/abc123.mp4",
                            "kind": "video_clip"}])
    notes = []
    key, row = agent_tools._project_asset(ctx, "clips/9/abc123.mp4", notes)
    assert key == "clips/5/abc123.mp4" and row and notes


def test_original_as_asset_reads_through_look_at():
    ctx = ctx_with(assets=[{"storage_key": "originals/1/a.mov",
                            "kind": "original"}])
    notes = []
    name, args = agent_tools._resolve_from_project(
        ctx, "look_at_asset", {"asset_key": "originals/1/a.mov",
                               "times": [1.0]}, notes)
    assert name == "look_at" and args == {"times": [1.0]} and notes


def test_picture_card_patch_keeps_its_window():
    edl = fx_edl(60.0)
    edl["effects"]["picture_cards"] = [{"id": "c3", "start": 2.0, "end": 4.0}]
    ctx = ctx_with(edl)
    notes = []
    _name, args = agent_tools._resolve_from_project(
        ctx, "set_picture_card", {"id": "c3", "box": [0, 0, 1, 1]}, notes)
    assert args["start"] == 2.0 and args["end"] == 4.0


# ── ask_user ──

def test_ask_held_a_question_only_the_customer_can_answer_goes_through(monkeypatch):
    ctx = ctx_with()
    ctx.agent_lanes = [{"client": object(), "model": "m"}]
    monkeypatch.setattr(question_need, "needed", lambda *a: True)
    assert agent_tools._question_can_wait(ctx, "Which file?") is False


def test_ask_held_a_question_the_editor_can_decide_goes_back_once(monkeypatch):
    ctx = ctx_with()
    ctx.agent_lanes = [{"client": object(), "model": "m"}]
    monkeypatch.setattr(question_need, "needed", lambda *a: False)
    assert agent_tools._question_can_wait(ctx, "Which colour?") is True
    assert agent_tools._question_can_wait(ctx, "Which colour?") is False


def test_question_judge_any_doubt_lets_the_question_through():
    assert question_need.needed(None, "m", "edit", "which?") is True
    assert question_need.needed(object(), "m", "", "which?") is True
