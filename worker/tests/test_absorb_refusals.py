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
                             cuts=["scene"], urgency="high")
    assert name == "set_junction" and "urgency" not in args
    assert any("urgency ignored" in n for n in notes)


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
    out, _ = batch(edl, [{"id": "er2", "layer": "patches", "action": "remove"}])
    assert not out.get("patches")


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
    text = ("Captured 3 frame(s): @0.08s, @24.90s, @32.80s — 3 full-detail "
            "images follow. LOOK AT THEM YOURSELF (your question: FAILED "
            "complete6 saved read-only native evidence)")
    assert tool_outcome.from_legacy(text).status == "success"
    assert tool_outcome.from_legacy(
        "Changed-section verification for EDL v12 covered every planned risk "
        "window, and one earlier proof failed.").status == "success"
    assert tool_outcome.from_legacy(
        "The repaint failed (no room).").status == "transient_failure"


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


# ── the turn's tool list and cache (TURN_COST.md) ──

def _tool(name):
    return {"type": "function", "function": {
        "name": name, "description": name,
        "parameters": {"type": "object", "properties": {}}}}


def test_a_turns_tool_list_never_changes_and_a_new_tool_rides_a_reference():
    import claude_lane
    plan = claude_lane.tool_plan([_tool("get_edl")],
                                 [_tool("get_edl"), _tool("add_zoom"),
                                  _tool("add_text")], "medium")
    msgs = [{"role": "user", "content": "go"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "load_tools", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "loaded"}]
    assert claude_lane.surface_tools(
        plan, msgs, [_tool("get_edl"), _tool("add_zoom")]) == ["add_zoom"]
    assert claude_lane.surface_tools(
        plan, msgs, [_tool("get_edl"), _tool("add_zoom")]) == []
    first = claude_lane._plan_tools(plan, "claude-haiku-5-5")
    assert [t["name"] for t in first] == ["get_edl", "add_text", "add_zoom"]
    assert "defer_loading" not in first[0]
    assert first[1]["defer_loading"] and first[2]["defer_loading"]
    _sys, out, _t = claude_lane.to_anthropic(msgs, None, "claude-haiku-5-5",
                                             plan["refs"])
    last = out[-1]["content"]
    assert last[0]["type"] == "tool_result"
    assert last[0]["content"] == [{"type": "tool_reference",
                                   "tool_name": "add_zoom"}]
    assert last[1] == {"type": "text", "text": "loaded"}


def test_no_cache_marker_lands_on_a_deferred_tool():
    import claude_lane
    tools = [{"name": "a"}, {"name": "b", "defer_loading": True}]
    msgs = [{"role": "user", "content": [{"type": "text", "text": "x"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "y"}]},
            {"role": "user", "content": [{"type": "text", "text": "z"}]}]
    claude_lane._mark_cache([], msgs, tools, "claude-haiku-5-5")
    assert "cache_control" in tools[0] and "cache_control" not in tools[1]


def test_the_cache_write_stops_in_front_of_the_first_calls_pixels():
    import claude_lane
    content = [{"type": "text", "text": "the request"},
               {"type": "text", "text": "FILMSTRIPS & STILLS - a view"},
               {"type": "image", "source": {}}]
    assert claude_lane._last_kept_block(content) == 0


def test_create_holds_one_effort_and_raises_it_in_a_message(monkeypatch):
    import claude_lane
    seen = []

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            raise SystemExit

    class Client:
        def __init__(self):
            self.messages = SimpleNamespace(
                stream=lambda **body: seen.append(body) or Stream())

        def with_options(self, **kw):
            return self
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "_sdk", Client)
    import claude_trial
    monkeypatch.setattr(claude_trial, "guard_call", lambda: None)
    plan = claude_lane.tool_plan([_tool("get_edl")], [_tool("get_edl"),
                                                      _tool("add_zoom")], "medium")
    with pytest.raises(SystemExit):
        claude_lane.create("claude-haiku-5-5",
                           [{"role": "user", "content": "go"}],
                           tools=[_tool("get_edl")], effort="xhigh",
                           tool_plan=plan)
    body = seen[0]
    assert body["output_config"] == {"effort": "medium"}
    assert body["messages"][-1] == {"role": "system", "content": [],
                                    "output_config": {"effort": "xhigh"}}
    assert [t["name"] for t in body["tools"]] == ["get_edl", "add_zoom"]


def test_a_refused_tool_plan_retries_the_old_way_within_the_same_call(monkeypatch):
    import claude_lane
    monkeypatch.setattr(claude_lane, "_plan_off", False)
    seen = []

    class Stream:
        def __enter__(self):
            if len(seen) == 1:
                raise RuntimeError("400: defer_loading is not supported")
            raise SystemExit

        def __exit__(self, *a):
            return False

    class Client:
        def __init__(self):
            self.messages = SimpleNamespace(
                stream=lambda **body: seen.append(body) or Stream())

        def with_options(self, **kw):
            return self
    monkeypatch.setattr(claude_lane, "available", lambda: True)
    monkeypatch.setattr(claude_lane, "configured", lambda: True)
    monkeypatch.setattr(claude_lane, "_sdk", Client)
    import claude_trial
    monkeypatch.setattr(claude_trial, "guard_call", lambda: None)
    plan = claude_lane.tool_plan([_tool("get_edl")], [_tool("get_edl"),
                                                      _tool("add_zoom")], "medium")
    with pytest.raises(SystemExit):
        claude_lane.create("claude-haiku-5-5",
                           [{"role": "user", "content": "go"}],
                           tools=[_tool("get_edl")], effort="medium",
                           tool_plan=plan)
    assert len(seen) == 2
    assert [t["name"] for t in seen[1]["tools"]] == ["get_edl"]
    assert "defer_loading" not in seen[1]["tools"][0]
    monkeypatch.setattr(claude_lane, "_plan_off", False)


def test_a_slideshows_scene_map_is_one_line():
    import timeline
    keep = [[float(i), float(i + 1)] for i in range(83)]
    text = timeline.describe_program({"keep": keep})
    assert len(text.splitlines()) == 2
    assert "scenes 1-83" in text and "output time = source time" in text
    assert ", 41, 42," in text
    short = timeline.describe_program({"keep": [[0.0, 1.0], [5.0, 6.0]]})
    assert "scene 2  1-2s: main footage 5-6s" in short


def test_touching_keep_spans_read_as_one():
    import agent_loop
    line = agent_loop._keep_line([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0],
                                  [3.0, 4.0], [4.0, 5.0]])
    assert line.startswith("5 touching spans covering 0-5s")
    assert agent_loop._keep_line([[0.0, 1.0], [2.0, 3.0]]) == \
        "[[0.0, 1.0], [2.0, 3.0]]"


def test_the_digest_has_one_line_for_each_stretch_of_footage():
    import visual_index
    text = visual_index.compact_text({
        "version": 1, "evidence": [
            {"evidence_id": "a", "covered_ranges": [[0, 4]],
             "semantic_description": "A silver SUV."},
            {"evidence_id": "b", "covered_ranges": [[0, 4]],
             "semantic_description": "The same SUV again."},
            {"evidence_id": "c", "covered_ranges": [[4, 8]],
             "semantic_description": None}]})
    assert "A silver SUV." in text and "same SUV" not in text
    assert "1 sampled moments carry no description" in text


def test_frames_extract_failed_one_more_run_past_a_lease(monkeypatch):
    import remote
    calls = []

    def run(job):
        calls.append(job["type"])
        if len(calls) == 1:
            raise RuntimeError("Box frames call exceeded its executor lease "
                               "while failed")
        return {"ok": True}
    monkeypatch.setattr(remote, "_run_remote", run)
    assert remote.run_frames_remote(1, {}) == {"ok": True} and len(calls) == 2


def test_erase_repaint_failed_one_more_run_past_a_lease(monkeypatch):
    import remote
    calls = []

    def run(job):
        calls.append(job["type"])
        if len(calls) == 1:
            raise RuntimeError("Box clean call exceeded its executor lease "
                               "while stopping")
        return {"ok": True}
    monkeypatch.setattr(remote, "_run_remote", run)
    assert remote.run_clean_remote(1, {}) == {"ok": True} and calls == ["clean"] * 2


def test_any_other_remote_failure_is_not_retried(monkeypatch):
    import remote

    def run(job):
        raise RuntimeError("something else")
    monkeypatch.setattr(remote, "_run_remote", run)
    with pytest.raises(RuntimeError):
        remote.run_frames_remote(1, {})


def test_media_copy_failed_keeps_the_real_error(monkeypatch):
    import mcp_media
    seen = {}
    monkeypatch.setattr(mcp_media.media, "run",
                        lambda cmd, timeout=None: seen.update(cmd=cmd))
    mcp_media._encode("a.mp4", "b.mp4", start=0, duration=1, height=480,
                      video_kbps=500, fps=24.0, has_audio=False)
    assert seen["cmd"][:5] == ["ffmpeg", "-y", "-hide_banner", "-loglevel",
                               "error"]


def test_no_change_says_why_a_cut_found_nothing(monkeypatch):
    edl = fx_edl()
    edl["keep"] = [[0.0, 10.0], [20.0, 30.0]]
    ctx = ctx_with(edl)
    ctx.clamp = float
    out = agent_tools.cut_range(ctx, 12.0, 13.0)
    assert out.startswith("NO CHANGE") and "already out of the edit" in out


def test_get_words_main_alias_on_the_fast_path():
    import read_tools
    args = {"asset_key": "main", "start": 1}
    read_tools.main_asset_alias("get_words", args, [])
    assert args == {"start": 1}


def test_end_past_program_a_single_tool_runs_again_at_the_limit():
    out = "REJECTED (EDL v3 unchanged): effects.regions[0]: end 25.3 exceeds the limit 25.0s."
    assert agent_tools._end_inside_limit({"start": 20, "end": 25.3}, out) == 25.0
    assert agent_tools._end_inside_limit({"start": 25.5, "end": 25.3}, out) is None
    assert agent_tools._end_inside_limit({"start": 20, "end": 30}, out) is None
    assert agent_tools._end_inside_limit({"start": 20, "end": 25.3}, "EDL v4") is None


def test_style_invalid_unknown_field_and_range_do_not_refuse():
    ctx = ctx_with()
    style = {"emphasis_scale": 0.75, "max_words_per_caption": 5, "size": "l"}
    out = agent_tools._parse_partial_style(style, ctx)
    assert "ERR" not in str(out)
    assert any("max_words_per_caption" in n for n in ctx._call_notes)


def test_motion_param_invalid_a_parameter_the_template_lacks_is_left_out():
    import motion_templates
    out = motion_templates.normalize_params(
        "word_slam", {"text": "HI", "nope": 1}, strict=True)
    assert "nope" not in out and out["text"] == "HI"


def test_batch_media_upsert_with_another_id_uses_the_operations_id():
    edl = fx_edl()
    edl["texts"] = [{"id": "tx1", "text": "a", "start": 0.0, "end": 1.0}]
    out, _ = batch(edl, [{"action": "upsert", "layer": "texts", "id": "tx1",
                          "value": {"id": "other", "end": 2.0}}])
    assert out["texts"][0]["id"] == "tx1" and out["texts"][0]["end"] == 2.0


def test_batch_remove_in_the_wrong_layer_finds_the_item():
    edl = fx_edl()
    edl["effects"]["zooms"] = [{"id": "zm6", "start": 1.0, "end": 2.0,
                                "strength": 1.2}]
    out, notes = batch(edl, [{"action": "remove", "layer": "motion", "id": "zm6"}])
    assert not (out["effects"] or {}).get("zooms")
    assert any("zm6" in n for n in notes)


def test_batch_caption_mutes_are_held_to_the_programme():
    edl = fx_edl(30.21)
    out, notes = batch(edl, [{"action": "set", "layer": "caption_mutes",
                              "value": [[0.0, 40.0]]}], duration=30.21)
    assert out["caption_mutes"][0][1] == 30.21 or \
        out["caption_mutes"][0]["end"] == 30.21


def test_batch_a_list_inside_effects_can_be_named_as_a_layer():
    edl = fx_edl()
    edl["effects"]["zooms"] = [{"id": "zm1", "start": 1.0, "end": 2.0,
                                "strength": 1.2}]
    out, _ = batch(edl, [{"action": "upsert", "layer": "zooms", "id": "zm1",
                          "value": {"id": "zm1", "end": 3.0}}])
    assert out["effects"]["zooms"][0]["end"] == 3.0
    out, _ = batch(edl, [{"action": "remove", "layer": "zooms", "id": "zm1"}])
    assert not (out["effects"] or {}).get("zooms")


def test_batch_a_near_spelling_reaches_the_real_field():
    edl = fx_edl()
    out, notes = batch(edl, [{"action": "set", "layer": "texts", "value": [{
        "id": "tx1", "text": "hi", "start": 0, "end": 2, "colr": "#FFFFFF"}]}])
    assert out["texts"][0]["color"] == "#FFFFFF"
    assert any("read colr as color" in n for n in notes)


def test_batch_a_value_the_schema_refuses_on_a_styling_field_is_left_out():
    out, notes = batch(fx_edl(), [{"action": "set", "layer": "texts", "value": [{
        "id": "tx1", "text": "hi", "start": 0, "end": 2, "entrance": "swoosh"}]}])
    assert out["texts"][0]["entrance"] in (None, "none")
    assert any("swoosh" in n for n in notes)


def test_batch_upsert_on_a_list_layer_sets_it():
    out, _ = batch(default_edl(60.0), [{"action": "upsert", "layer": "keep",
                                        "value": [[0.0, 5.0]]}])
    assert out["keep"] == [[0.0, 5.0]]


def test_batch_media_not_attached_names_the_key_and_the_choices():
    with pytest.raises(ValueError) as err:
        batch(default_edl(60.0), [{"action": "set", "layer": "inserts", "value": [{
            "id": "i", "kind": "image", "asset_key": "gone.png", "at_output_s": 0,
            "duration_s": 2}]}], keys=["cards/a.png"])
    assert "gone.png" in str(err.value) and "cards/a.png" in str(err.value)


def test_batch_a_fraction_of_an_overlay_at_the_end_is_removed():
    edl = default_edl(60.0)
    edl["overlays"] = [{"id": "ov1", "kind": "image", "asset_key": "a.png",
                        "start": 59.84, "duration_s": 0.16}]
    out, notes = batch(edl, [{"action": "upsert", "layer": "overlays",
                              "id": "ov1", "value": {"x": 0.5}}])
    assert not out["overlays"] and notes


def test_a_junction_run_is_one_line_in_every_receipt():
    import schemas

    def junction(at, style="dissolve"):
        return SimpleNamespace(stage="junction", style=style, at=float(at),
                               describe=lambda: f"junction {at} {style}")
    items = [junction(at) for at in range(1, 60)]
    one = schemas._fx_lines(items)
    assert len(one) == 1 and "junction x59 (dissolve x59) on cuts 1-59s" in one[0]
    items[3] = junction(4, "wipe_left")
    assert schemas._fx_lines(items) != one
    few = [junction(at) for at in range(1, 4)]
    assert schemas._fx_lines(few) == ["junction 1 dissolve", "junction 2 dissolve",
                                      "junction 3 dissolve"]


def test_load_tools_by_department_lists_names_and_loads_none():
    ctx = ctx_with()
    ctx._tools_by_name = True
    out = agent_tools.load_tools(ctx, domains=["looks"])
    assert "load_tools(names=" in out and not getattr(
        ctx, "_loaded_tool_domains", None)


def test_a_department_never_surfaces_by_itself_on_a_stable_list():
    ctx = SimpleNamespace(edit_plan={"tool_domains": ["looks"]},
                          _loaded_tool_domains={"motion"},
                          _loaded_tool_names={"add_zoom"}, _tools_by_name=True)
    names = agent_tools.compact_tool_names(ctx)
    assert "add_zoom" in names
    assert names == agent_tools.planning_tool_names() | {"add_zoom"}


def test_a_write_stops_in_front_of_look_frames_and_the_next_reads_it_back():
    import claude_lane
    frames = {"type": "text", "text": "Frames for your own eyes - see them"}
    content = [{"type": "tool_result", "tool_use_id": "a", "content": "ok"},
               frames, {"type": "image", "source": {}}]
    assert claude_lane._last_kept_block(content) == 0
    released = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "ok"},
        {"type": "text", "text": "Frames for your own eyes — inspected before "
                                 "a committed EDL change."}]},
        {"role": "assistant", "content": [{"type": "text", "text": "y"}]},
        {"role": "user", "content": [{"type": "text", "text": "z"}]}]
    block, index = claude_lane._released_pixels_anchor(released)
    assert block is released[0]["content"] and index == 0


def test_a_junction_is_a_transition_the_customer_asked_for():
    edl = default_edl(60.0)
    assert not quality_verifier._has_transition(edl)
    edl["fx"] = [{"type": "junction", "at": 4.0}]
    assert quality_verifier._has_transition(edl)


def test_a_trim_keeps_the_cuts_baked_into_the_footage():
    before = [[0.0, 4.6], [4.6, 9.4], [9.4, 14.0]]
    assert agent_tools._keep_exposed_cuts(before, [[0.0, 13.67]]) == [
        [0.0, 4.6], [4.6, 9.4], [9.4, 13.67]]
    assert agent_tools._keep_exposed_cuts([[0.0, 4.0], [6.0, 8.0]],
                                          [[0.0, 3.0]]) == [[0.0, 3.0]]


def test_a_read_page_carries_no_nulls():
    edl = fx_edl()
    edl["effects"]["zooms"] = [{"id": "zm1", "start": 1.0, "end": 2.0,
                                "note": None}]
    page = json.loads(edl_read.read_edl({"version": 3, "json": edl}, 60.0, [],
                                        sections=["effects"]))
    assert page["sections"]["effects"]["zooms"] == [
        {"id": "zm1", "start": 1.0, "end": 2.0}]


def test_a_stable_list_loads_by_name():
    tools = agent_tools.names_only_load(agent_tools.openai_tools(
        "claude-haiku-5-5", compact=True, names={"load_tools"}))
    props = tools[0]["function"]["parameters"]["properties"]
    assert "names" in props and "domains" not in props


def test_a_tool_that_takes_ids_is_never_fanned_out():
    """It recursed for ever on a tool whose own argument is ids (the replay's
    remove_punctuation, 'maximum recursion depth exceeded')."""
    ctx = ctx_with()
    assert agent_tools._fan_out_ids(ctx, "remove_punctuation",
                                    {"ids": ["jn1", "jn2"]}) is None


def test_a_breath_of_black_at_the_end_is_no_finding():
    import render_qc
    assert render_qc.black_tail_finding((347.3, 347.7, True), {}) is None
    assert "BLACK TAIL" in render_qc.black_tail_finding((340.0, 347.7, True), {})


def test_batch_remove_on_an_object_layer_clears_a_field_or_the_layer():
    edl = fx_edl()
    edl["effects"]["grade"] = "warm"
    edl["effects"]["fade_in_s"] = 0.5
    out, _ = batch(edl, [{"action": "remove", "layer": "effects", "id": "grade"}])
    assert not (out["effects"] or {}).get("grade")
    assert out["effects"]["fade_in_s"] == 0.5
    out, _ = batch(edl, [{"action": "remove", "layer": "effects"}])
    assert not out["effects"]


# ── what the first replay of the whole release still refused ──

def test_an_fx_change_on_an_id_that_is_gone_names_the_ids_and_the_tool():
    edl = {"keep": [[0.0, 60.0]], "fx": [
        {"id": "jn1", "type": "junction", "stage": "junction", "at": 4.0,
         "style": "dissolve", "duration_s": 1.0, "device": "junction_dissolve"}]}
    with pytest.raises(ValueError) as e:
        batch(edl, [{"action": "upsert", "layer": "fx", "id": "jn3",
                     "value": {"style": "slide_left", "duration_s": 1.0}}])
    text = str(e.value)
    assert "jn3" in text and "jn1" in text and "set_junction(at=" in text
    assert "validation error" not in text


class _ZoomCtx:
    def __init__(self):
        self.edl = {"keep": [[0.0, 60.0]], "effects": {}}
        self.notes = []

    def latest_edl(self):
        return {"version": 1, "json": self.edl}

    def write_edl(self, edl, note):
        self.edl = edl
        return f"EDL v1 -> v2: {note}"


@pytest.fixture
def zoom_ctx(monkeypatch):
    monkeypatch.setattr(agent_tools, "_zoom_target_problem", lambda *a, **k: None,
                        raising=False)
    return _ZoomCtx()


def test_a_zoom_path_copied_from_get_edl_keeps_its_strength(zoom_ctx):
    out = agent_tools.add_zoom_path(zoom_ctx, [
        {"f": 0.0, "s": 0.0, "cx": 0.4, "cy": 0.5},
        {"f": 0.5, "s": 0.05, "cx": 0.6, "cy": 0.5},
        {"f": 1.0, "s": 0.0, "cx": 0.5, "cy": 0.5}], start=10.0, end=20.0)
    assert out.startswith("EDL"), out
    zoom = zoom_ctx.edl["effects"]["zooms"][-1]
    assert (zoom["start"], zoom["end"]) == (10.0, 20.0)
    assert max(p["s"] for p in zoom["path"]) == pytest.approx(0.05, abs=1e-6)


def test_a_fraction_with_no_window_says_what_it_needs(zoom_ctx):
    out = agent_tools.add_zoom_path(zoom_ctx, [
        {"f": 0.0, "s": 0.0, "cx": 0.4, "cy": 0.5},
        {"f": 1.0, "s": 0.0, "cx": 0.5, "cy": 0.5}])
    assert out.startswith("REJECTED") and "start=" in out


def test_a_zoom_named_by_its_id_on_the_effects_layer_changes():
    edl = {"keep": [[0.0, 60.0]], "effects": {"zooms": [
        {"id": "zm2", "start": 0.0, "end": 4.6, "strength": 0.1, "mode": "ease"},
        {"id": "zm3", "start": 5.0, "end": 9.0, "strength": 0.1, "mode": "ease"}]}}
    out, notes = batch(edl, [{"action": "upsert", "layer": "effects", "id": "zm2",
                              "value": {"id": "zm2", "strength": 0.07}}])
    zooms = out["effects"]["zooms"]
    assert [z["strength"] for z in zooms] == [0.07, 0.1]
    assert any("zm2" in n for n in notes)


def test_an_effects_id_that_is_nowhere_says_which_are():
    edl = {"keep": [[0.0, 60.0]], "effects": {"zooms": [
        {"id": "zm2", "start": 0.0, "end": 4.6, "strength": 0.1, "mode": "ease"}]}}
    with pytest.raises(ValueError) as e:
        batch(edl, [{"action": "upsert", "layer": "effects", "id": "zm9",
                     "value": {"strength": 0.03}}])
    assert "zm9" in str(e.value) and "zm2" in str(e.value)
