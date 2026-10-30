"""WAVE_C (31 Oct 2026): follow-up recipes on the new Claude lanes.

Request interpretation, the per-request scope and length guards, and the
REQUEST CHECK that compares the turn's before/after EDL with the request.
"""
import copy

import followup_recipes as fr


def _edl(keep=None, cap_scale=1.6, title_size=1.3, zooms=None, fx=None,
         slam_layer="above_captions", focus_x=0.56):
    return {
        "keep": keep or [[10.0, 20.0], [21.0, 31.0], [32.0, 42.0]],
        "speed": [], "inserts": [], "overlays": [], "music": [], "sfx": [],
        "voiceover": [], "volume": [], "texts": [], "vectors": [],
        "master": {"loudness": "social"},
        "captions": {"mode": "from_transcript",
                     "style": {"size_scale": cap_scale, "anchor_y": 0.6}},
        "motion": [
            {"id": "mg1", "template": "hook_title", "start": 0.1, "end": 2.9,
             "layer": "above_captions",
             "params": {"text": "Deep *learning* works", "size": title_size}},
            {"id": "mg2", "template": "word_slam", "start": 11.5, "end": 12.8,
             "layer": slam_layer, "params": {"text": "*2005*"}},
        ],
        "effects": {"grade": None, "zooms": zooms if zooms is not None else [
            {"id": "zm1", "start": 1.7, "end": 3.4, "strength": 0.38}]},
        "fx": fx if fx is not None else [
            {"id": "br1", "type": "broll", "start": 3.5, "end": 5.6}],
        "frame": {"mode": "crop", "ratio": "9:16", "focus_x": focus_x,
                  "focus_y": 0.39, "follow": [{"k": [[10.0, 0.5, 0.5]]}]},
    }


class Ctx:
    def __init__(self, edl):
        self.edl = edl
        self.followup = None

    def latest_edl(self):
        return {"version": 7, "json": copy.deepcopy(self.edl)}


# ── interpretation ────────────────────────────────────────────────────────

def test_the_common_requests_classify():
    cases = {
        "cut the boring parts": ["boring"],
        "make it punchier": ["punchier"],
        "remove the ums": ["ums"],
        "make the captions bigger": ["captions_bigger"],
        "captions are too big, make them smaller": ["captions_smaller"],
        'change the title to "3 THINGS NOBODY TELLS YOU"': ["title_change"],
        "add a title at the start": ["title_add"],
        "make the opening title a little bigger": ["title_bigger"],
        "add some b-roll": ["broll"],
        "make it shorter": ["shorter"],
        "make it longer": ["longer"],
        "add some zoom effects": ["zooms"],
        "cut the silences and remove the ums": ["ums", "silences"],
    }
    for text, keys in cases.items():
        assert fr.classify(text) == keys, text


def test_specific_or_long_requests_get_no_recipe():
    for text in ("cut 10 to 12 15 to 17", "remove the part at 0:12",
                 "make it a vertical short with captions",
                 "make the captions red", "hi", "don't change the title",
                 "record https://valmera.io",
                 "cut the boring parts and make it longer",
                 "make this a 30 second highlight reel of the funniest "
                 "moments with captions and a big hook title for tiktok"):
        assert fr.classify(text) == [], text


# ── lanes ─────────────────────────────────────────────────────────────────

def test_a_cut_that_slides_or_drops_a_graphic_is_only_a_cut():
    a = _edl()
    b = _edl(keep=[[10.0, 20.0], [32.0, 42.0]])
    b["motion"][1]["start"], b["motion"][1]["end"] = 5.0, 6.0
    b["fx"] = []                               # its span was cut away
    assert fr.changed_lanes(a, b) == ["cuts"]


def test_unasked_changes_are_named_by_lane():
    a = _edl()
    assert fr.changed_lanes(a, _edl(slam_layer="behind_subject")) == ["graphics"]
    assert fr.changed_lanes(a, _edl(focus_x=0.53)) == ["frame"]
    assert fr.changed_lanes(a, _edl(zooms=[])) == ["zooms"]
    assert fr.changed_lanes(a, _edl(title_size=1.5)) == ["title"]
    assert fr.changed_lanes(a, _edl(cap_scale=2.0)) == ["captions"]


# ── guards ────────────────────────────────────────────────────────────────

def test_boring_refuses_a_longer_cut_and_an_unasked_reframe():
    ctx = Ctx(_edl())
    assert fr.start(ctx, "cut the boring parts")["direction"] == "shorter"
    longer = _edl(keep=[[9.0, 20.0], [21.0, 31.0], [32.0, 42.0]])
    msg = fr.check_write(ctx, ctx.edl, longer, "keep_segments")
    assert msg and "LONGER" in msg
    reframe = _edl(focus_x=0.5)
    msg = fr.check_write(ctx, ctx.edl, reframe, "set_frame")
    assert msg and "NOT ASKED" in msg and "framing" in msg
    shorter = _edl(keep=[[10.0, 20.0], [32.0, 42.0]])
    assert fr.check_write(ctx, ctx.edl, shorter, "cut_output_range") is None


def test_tools_outside_the_request_are_refused_before_they_run():
    ctx = Ctx(_edl())
    fr.start(ctx, "make the captions bigger")
    assert fr.refusal(ctx, "set_caption_style", {"style": {}}) is None
    assert "NOT ASKED" in fr.refusal(ctx, "set_frame", {"ratio": "9:16"})
    assert "NOT ASKED" in fr.refusal(ctx, "remove_zoom", {"id": "zm1"})
    assert "NOT ASKED" in fr.refusal(ctx, "set_motion_graphic",
                                     {"id": "mg2", "layer": "behind_subject"})
    assert "NOT ASKED" in fr.refusal(ctx, "cut_silences", {})
    assert "NOT ASKED" in fr.refusal(ctx, "reset_edit", {})
    assert fr.refusal(ctx, "get_edl", {}) is None
    assert fr.refusal(ctx, "render_preview", {}) is None


def test_a_title_request_may_touch_only_the_title():
    ctx = Ctx(_edl())
    fr.start(ctx, "make the opening title a little bigger")
    assert fr.refusal(ctx, "set_motion_graphic", {"id": "mg1",
                                                  "params": {"size": 1.5}}) is None
    assert "NOT ASKED" in fr.refusal(ctx, "set_motion_graphic", {"id": "mg2"})
    assert "already has its opening title" in fr.refusal(
        ctx, "add_motion_graphic", {"template": "hook_title"})
    assert fr.check_write(ctx, ctx.edl, _edl(title_size=1.56)) is None
    assert "NOT ASKED" in fr.check_write(
        ctx, ctx.edl, _edl(title_size=1.56, cap_scale=1.2), "apply_edit_batch")


def test_no_recipe_no_guard():
    ctx = Ctx(_edl())
    assert fr.start(ctx, "make it a vertical short with captions") is None
    assert fr.refusal(ctx, "set_frame", {}) is None
    assert fr.check_write(ctx, ctx.edl, _edl(focus_x=0.1), "set_frame") is None
    assert fr.request_check(ctx) == ""
    assert fr.note(ctx, "x") == ""


# ── the recipe and the verification step ──────────────────────────────────

def test_the_note_carries_the_exact_call_from_the_current_edit():
    ctx = Ctx(_edl(cap_scale=1.6))
    fr.start(ctx, "make the captions bigger")
    note = fr.note(ctx, "make the captions bigger")
    assert "set_caption_style(style={'size_scale': 2.0})" in note
    assert "now 1.6" in note and "REQUEST CHECK" in note
    ctx = Ctx(_edl(title_size=1.3))
    fr.start(ctx, "make the opening title a little bigger")
    assert "set_motion_graphic(id='mg1', params={'size': 1.56})" in fr.note(
        ctx, "make the opening title a little bigger")
    ctx = Ctx(_edl())
    text = 'change the title to "3 THINGS NOBODY TELLS YOU"'
    fr.start(ctx, text)
    assert "'3 THINGS NOBODY TELLS YOU'" in fr.note(ctx, text)


def test_request_check_compares_before_and_after_against_the_ask():
    ctx = Ctx(_edl())
    fr.start(ctx, "cut the boring parts")
    out = fr.request_check(ctx)
    assert "30.0 s -> 30.0 s" in out and "NOT MET" in out
    ctx.edl = _edl(keep=[[10.0, 20.0], [32.0, 42.0]])
    out = fr.request_check(ctx)
    assert "30.0 s -> 20.0 s" in out and "MET:" in out and "NOT MET" not in out
    ctx = Ctx(_edl(cap_scale=1.6))
    fr.start(ctx, "make the captions bigger")
    ctx.edl = _edl(cap_scale=2.0, focus_x=0.4)
    out = fr.request_check(ctx)
    assert "captions 25% bigger" in out and "NOT ASKED and changed" in out


def test_cut_silences_never_brings_back_cut_footage():
    import agent_tools
    prev = [[10.0, 20.0], [21.0, 31.0]]
    widened = [[9.6, 15.0], [15.5, 20.3], [20.8, 31.0]]
    assert agent_tools._within_keep(widened, prev) == [
        [10.0, 15.0], [15.5, 20.0], [21.0, 31.0]]


def test_a_cut_is_never_told_as_added_b_roll_or_new_titles():
    import trial_reply
    before = _edl()
    after = _edl(keep=[[10.0, 20.0], [32.0, 42.0]])
    after["motion"][1]["start"], after["motion"][1]["end"] = 5.0, 6.0
    after["fx"] = []
    phrases = trial_reply.friendly_changes(before, after)
    assert not [p for p in phrases if "B-roll" in p or "titles" in p], phrases
    restyled = _edl(slam_layer="behind_subject")
    assert fr.changed_lanes(before, restyled) == ["graphics"]


def test_reply_lead_states_the_real_result():
    ctx = Ctx(_edl(cap_scale=1.6))
    fr.start(ctx, "make the captions bigger")
    ctx.edl = _edl(cap_scale=2.0)
    assert fr.reply_lead(ctx) == "Done: the captions are 25% bigger."
    ctx = Ctx(_edl())
    fr.start(ctx, "cut the boring parts")
    ctx.edl = _edl(keep=[[10.0, 20.0], [32.0, 42.0]])
    assert fr.reply_lead(ctx) == "Done: it is now 20 seconds, 10 seconds shorter than before."
    ctx = Ctx(_edl())
    fr.start(ctx, 'change the title to "SAVE THIS"')
    ctx.edl = _edl()
    ctx.edl["motion"][0]["params"]["text"] = "*SAVE* THIS"
    assert fr.reply_lead(ctx) == "Done: the opening title now reads “SAVE THIS”."
    ctx = Ctx(_edl())
    fr.start(ctx, "remove the ums")
    assert fr.reply_lead(ctx) == "There were no ums or filler words to remove."


def test_a_caption_style_sent_as_a_json_string_is_that_object():
    import agent_tools
    parsed = agent_tools._parse_style('{"size_scale": 2.0}')
    assert isinstance(parsed, dict) and parsed["size_scale"] == 2.0
    assert agent_tools._parse_style("default") is None


def test_the_request_check_never_reaches_the_reply():
    import trial_reply
    f = trial_reply.Facts(before=_edl(), after=_edl(title_size=1.5), wrote=True,
                          previewed=True, duration_s=30.0, user_text="make the title bigger")
    text, _ = trial_reply.finish("The opening title is bigger now. The render check "
                                 "marked the change as met.", f)
    assert "check" not in text.lower() and "met" not in text.split()


def test_a_cut_ask_the_head_start_did_not_answer_goes_to_the_recipe():
    import agent_loop
    empty = {"intent": "generic", "applied": [], "whole": False}
    assert agent_loop._cut_ask_not_answered(empty, {"content": "cut the boring parts"})
    assert not agent_loop._cut_ask_not_answered(empty, {"content": "make it a short"})
    assert not agent_loop._cut_ask_not_answered(
        {"intent": "tighten", "applied": ["x"]}, {"content": "cut the boring parts"})
    assert not agent_loop._cut_ask_not_answered(
        {"intent": "generic", "applied": ["removed an empty card"]},
        {"content": "cut the boring parts"})


def test_a_param_saved_at_its_empty_default_is_not_a_change():
    # WAVE_A's hook_title schema re-saves every title with base_size /
    # band_top / band_bottom = 0: nobody changed the title
    before = _edl()
    after = _edl()
    after["motion"][0]["params"].update({"base_size": 0, "band_top": 0.0,
                                         "band_bottom": 0})
    assert fr.changed_lanes(before, after) == []
    after["motion"][0]["params"]["size"] = 1.5
    assert fr.changed_lanes(before, after) == ["title"]


def test_raw_size_numbers_never_reach_the_reply():
    import trial_reply
    out, n = trial_reply.scrub_pixels(
        "The opening title is bigger: its size went from 1.49 to 1.79, and "
        "the length stays at 22.0 s.")
    assert n == 1 and "1.49" not in out and "22.0 s" in out
    ctx = Ctx(_edl())
    fr.start(ctx, "make the captions bigger")
    assert "never the raw size numbers" in fr.note(ctx, "make the captions bigger")
