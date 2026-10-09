"""Tool schemas advertise the validators' real bounds, layout tools repair
instead of bouncing, and every rejection lists all problems at once."""
import copy
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import db as dbx
import editorial_graphics
import graphics
import schemas
import shorts
import skill_validator
import typography_scenes as scenes
from schemas import default_edl, describe_edl, validate_edl


class _Ctx:
    """Minimal ToolContext: validates every write like the real one."""

    def __init__(self, W=1080, H=1920, duration=12.0):
        self.index = {"video": {"width": W, "height": H}}
        self.duration = duration
        self.edl = default_edl(duration)
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self.edl}

    def write_edl(self, edl, description):
        self.edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def _schema(name):
    return agent_tools.TOOLS[name][2]


def _scene(**kw):
    args = dict(id="focus", start=0, end=4,
                lines=[{"runs": [{"text": "Your"}, {"text": "only", "at": .5}]},
                       {"runs": [{"text": "focus", "at": 1, "font": "Instrument Serif",
                                  "italic": True}]}],
                font_size=.09, box=[.08, .2, .92, .6])
    args.update(kw)
    return args


def _inside(texts, box, W, H, end=4, slack=8):
    x0, y0, x1, y1 = box
    for t in texts:
        b = graphics._compile_item(t, end, (W, H))
        assert x0 * W - slack <= b["left"] < b["right"] <= x1 * W + slack, (b, box)
        assert y0 * H - slack <= b["top"] < b["bottom"] <= y1 * H + slack, (b, box)


# ── 1. Schemas carry the validators' own constants ──────────────────────────

def test_typography_schema_bounds_come_from_validator_constants():
    props = _schema("set_typography_scene")
    line = props["lines"]["items"]["properties"]
    run = line["runs"]["items"]["properties"]
    pairs = [(props["font_size"], scenes.FONT_SIZE_RANGE),
             (props["leading"], scenes.LEADING_RANGE),
             (line["size"], scenes.LINE_SIZE_RANGE),
             (run["scale"], scenes.RUN_SCALE_RANGE)]
    for prop, (low, high) in pairs:
        assert (prop["minimum"], prop["maximum"]) == (low, high)
    assert props["lines"]["maxItems"] == scenes.MAX_LINES
    assert line["runs"]["maxItems"] == scenes.MAX_RUNS
    assert run["text"]["maxLength"] == scenes.MAX_RUN_CHARS
    assert props["box"]["items"] == {"type": "number", "minimum": 0, "maximum": 1}
    assert props["fit"]["enum"] == ["auto", "strict"]


@pytest.mark.parametrize("field,path", [
    ("font_size", ()), ("leading", ()),
    ("size", ("lines",)), ("scale", ("lines", "runs"))])
def test_typography_schema_edges_match_strict_validator(field, path):
    props = _schema("set_typography_scene")
    if not path:
        prop = props[field]
    elif path == ("lines",):
        prop = props["lines"]["items"]["properties"][field]
    else:
        prop = props["lines"]["items"]["properties"]["runs"]["items"]["properties"][field]

    def call(value):
        args = _scene(lines=[{"runs": [{"text": "Hi"}]}], box=[.08, .1, .92, .9],
                      font_size=.05)
        if field in ("font_size", "leading"):
            args[field] = value
        elif field == "size":
            args["lines"][0]["size"] = value
        else:
            args["lines"][0]["runs"][0]["scale"] = value
        return scenes.compose(**args, fit="strict")

    call(prop["minimum"])
    call(prop["maximum"])
    for outside in (prop["minimum"] - .01, prop["maximum"] + .01):
        with pytest.raises(scenes.LayoutRejected, match="must be between"):
            call(outside)


def test_editorial_schema_bounds_come_from_validator_constants():
    props = _schema("set_editorial_graphic")
    assert (props["font_size"]["minimum"], props["font_size"]["maximum"]) == \
        editorial_graphics.HEADLINE_FONT_RANGE
    assert props["text"]["maxLength"] == editorial_graphics.MAX_TEXT_CHARS
    assert props["secondary"]["maxLength"] == editorial_graphics.MAX_TEXT_CHARS
    assert props["eyebrow"]["maxLength"] == editorial_graphics.MAX_EYEBROW_CHARS
    assert props["speaker"]["maxLength"] == editorial_graphics.MAX_SPEAKER_CHARS
    assert props["fit"]["enum"] == ["auto", "strict"]
    low, high = editorial_graphics.HEADLINE_FONT_RANGE
    base = dict(id="h", kind="headline", text="A clear idea", speaker="Ada",
                start=0, end=8, fit="strict")
    for ok in (low, high):
        editorial_graphics.compose(**base, font_size=ok, box=[.08, .1, .92, .4])
    with pytest.raises(ValueError, match="font_size"):
        editorial_graphics.compose(**base, font_size=high + .01)


def test_caption_and_typography_leading_each_advertise_their_real_range():
    for tool in ("set_caption_style", "add_captions"):
        props = _schema(tool)["style"]["properties"]
        assert (props["leading"]["minimum"], props["leading"]["maximum"]) == \
            schemas.CAPTION_LEADING_RANGE
    schemas.CaptionStyle(leading=schemas.CAPTION_LEADING_RANGE[0])
    with pytest.raises(ValueError):
        schemas.CaptionStyle(leading=schemas.CAPTION_LEADING_RANGE[0] - .01)
    # The typography tool states its own, different range in prose and schema
    # so the caption range can no longer be mistaken for it.
    desc = agent_tools.TOOLS["set_typography_scene"][1]
    low, high = scenes.LEADING_RANGE
    assert f"leading is row spacing {low:g}-{high:g}" in desc
    assert "not caption leading" in desc


def test_keyframe_schema_carries_the_validator_keyframe_limit():
    curve = agent_tools._ANIM_FLOAT_PROP["anyOf"][1]
    assert curve["maxItems"] == schemas.ANIM_MAX_KEYFRAMES
    assert curve["items"]["properties"]["t"]["minimum"] == 0
    too_many = [{"t": i * .01, "v": .5} for i in range(schemas.ANIM_MAX_KEYFRAMES + 1)]
    with pytest.raises(schemas.EDLValidationError, match="at most"):
        schemas._norm_anim(too_many, "x", 0, 1)


# ── 2. Typography fit=auto ─────────────────────────────────────────────────

@pytest.mark.parametrize("dims", [(1080, 1920), (1920, 1080), (1080, 1080)])
def test_auto_fit_shrinks_oversized_rows_and_reports_applied_size(dims):
    W, H = dims
    args = _scene(font_size=.18, W=W, H=H,
                  lines=[{"runs": [{"text": "Your money"}]},
                         {"runs": [{"text": "or your time", "at": 1}]}])
    with pytest.raises(scenes.LayoutRejected, match="exceed"):
        scenes.compose(**args)
    result = scenes.compose(**args, fit="auto")
    fit = result["fit"]
    assert .18 * (1 - scenes.AUTO_MAX_SHRINK) <= fit["font_size"] < .18
    assert any("font_size 0.18→" in n and "fits the box" in n for n in fit["notes"])
    _inside(result["texts"], fit["box"], W, H)
    e = default_edl(4)
    e["texts"] = result["texts"]
    validate_edl(e, 4)


def test_auto_fit_never_silently_makes_small_type():
    # Needs ~61% smaller type: auto stops at its 40% cap and names the size
    # that would fit, so shrinking that far is the editor's explicit choice.
    args = _scene(font_size=.18,
                  lines=[{"runs": [{"text": "Everything you think you know"}]},
                         {"runs": [{"text": "about money", "at": 1}]}])
    with pytest.raises(scenes.LayoutRejected) as capped:
        scenes.compose(**args, fit="auto")
    problems = capped.value.problems
    assert any("Line exceeds its box" in p and "at font_size 0.108;" in p
               and "shrinks at most 40%" in p for p in problems), problems
    fits = [p for p in problems if p.startswith("As written it fits only at font_size")]
    assert len(fits) == 1, problems
    size = float(fits[0].split("font_size ")[1].split(" ")[0])
    assert size < .18 * (1 - scenes.AUTO_MAX_SHRINK)
    assert f"pass font_size={size:g}" in fits[0]
    chosen = scenes.compose(**{**args, "font_size": size}, fit="auto")
    assert chosen["fit"]["font_size"] == size and chosen["fit"]["notes"] == []
    _inside(chosen["texts"], chosen["fit"]["box"], 1080, 1920)

    # The default size never drops below AUTO_MIN_FONT_SIZE, and a size the
    # editor already chose below it is not shrunk at all.
    for authored, floor in ((.075, scenes.AUTO_MIN_FONT_SIZE), (.045, .045)):
        long = _scene(font_size=authored, box=[.3, .2, .7, .6],
                      lines=[{"runs": [{"text": "this is longer than the box"}]}])
        with pytest.raises(scenes.LayoutRejected, match=f"at font_size {floor:g};"):
            scenes.compose(**long, fit="auto")
    with pytest.raises(scenes.LayoutRejected, match="does not shrink type authored at 0.05"):
        scenes.compose(**long, fit="auto")


def test_auto_fit_clamps_box_and_styling_into_range_and_says_so():
    args = _scene(box=[0, .7, 1, 1.0], leading=.9,
                  lines=[{"runs": [{"text": "Big", "scale": 2.4}]}], font_size=.05)
    with pytest.raises(scenes.LayoutRejected) as strict:
        scenes.compose(**args)
    # strict lists every problem in one pass
    assert len(strict.value.problems) == 3
    assert any("leading must be between 1.05 and 1.8" in p for p in strict.value.problems)
    assert any("run scale must be between" in p for p in strict.value.problems)
    assert any("safe area" in p for p in strict.value.problems)

    result = scenes.compose(**args, fit="auto")
    fit = result["fit"]
    assert fit["box"] == [.045, .7, .955, .96]
    assert fit["leading"] == scenes.LEADING_RANGE[0]
    notes = " ".join(fit["notes"])
    assert "leading 0.9→1.05" in notes
    assert "run scale (line 1 run 1) 2.4→1.8" in notes
    assert "box [0,0.7,1,1]→[0.045,0.7,0.955,0.96]" in notes
    _inside(result["texts"], fit["box"], 1080, 1920)


def test_auto_fit_repairs_reversed_and_thin_boxes_but_never_guesses_pixels():
    reversed_box = scenes.compose(**_scene(box=[.92, .6, .08, .2]), fit="auto")
    assert reversed_box["fit"]["box"] == [.08, .2, .92, .6]
    thin = scenes.compose(**_scene(box=[.08, .5, .92, .52], font_size=.03,
                                   lines=[{"runs": [{"text": "a"}]}]), fit="auto")
    y0, y1 = thin["fit"]["box"][1], thin["fit"]["box"][3]
    assert y1 - y0 == pytest.approx(scenes.MIN_BOX_SPAN)
    with pytest.raises(scenes.LayoutRejected, match="looks like pixels"):
        scenes.compose(**_scene(box=[86, 538, 994, 1267]), fit="auto")


def test_auto_fit_only_snaps_pre_start_cues_and_lists_everything_left():
    args = _scene(end=1.2, font_size=.25, leading=.5,
                  lines=[{"runs": [{"text": "too many words", "at": .8}]},
                         {"runs": [{"text": "bad", "font": "Comic Sans"}]}])
    with pytest.raises(scenes.LayoutRejected) as auto:
        scenes.compose(**args, fit="auto")
    problems = auto.value.problems
    assert any(p.startswith('Allow 0.72s after the cue for "too many words')
               for p in problems), problems
    assert any("bundled font" in p for p in problems)
    # styling was repaired rather than reported; the cue was not moved
    assert not any("leading" in p or "font_size" in p for p in problems)

    # The single cue repair: a cue before the scene start shows at the start
    # (reported). Every other cue keeps its authored program time.
    early = scenes.compose(**_scene(
        start=2, end=6, lines=[{"runs": [{"text": "Early", "at": 1},
                                         {"text": "on", "at": 2.5},
                                         {"text": "time", "at": 3.25}]}]),
        fit="auto")
    assert [t["start"] for t in early["texts"]] == [2, 2.5, 3.25]
    assert early["fit"]["notes"] == [
        "line 1 run 1 at 1→2 (cues cannot precede the scene start)"]
    with pytest.raises(scenes.LayoutRejected, match="at must be between 2 and"):
        scenes.compose(**_scene(start=2, end=6, lines=[{"runs": [
            {"text": "Early", "at": 1}]}]))
    tool = agent_tools.TOOLS["set_typography_scene"][1]
    assert "A cue before start snaps to start" in tool
    assert "no other cue time is moved" in tool


def test_messages_name_the_authored_line_and_run_after_skipped_ones():
    with pytest.raises(scenes.LayoutRejected) as rejected:
        scenes.compose(**_scene(font_size=.05, box=[.3, .2, .7, .6], lines=[
            {"runs": []},
            {"runs": [{"text": "x", "bogus": 1},
                      {"text": "y", "scale": .61}]},
            {"runs": [{"text": "a sentence far too long for this box"}]}]))
    problems = " ".join(rejected.value.problems)
    assert "at least one run (line 1)" in problems
    assert "(line 2 run 1)" in problems
    assert "line 2 run 2 is 0.0305" not in problems      # .05*.61 is in range
    assert "(line 3 needs" in problems, problems
    assert "(line 2 needs" not in problems


def test_overflowing_rows_are_one_numbered_problem_not_one_per_line():
    long = {"runs": [{"text": "This is a fairly long line of words"}]}
    with pytest.raises(scenes.LayoutRejected) as rejected:
        scenes.compose(**_scene(font_size=.12, box=[.08, .3, .92, .6],
                                lines=[long] * 3))
    wide = [p for p in rejected.value.problems if "Line exceeds its box" in p]
    assert len(wide) == 1
    assert all(f"line {i} needs" in wide[0] for i in (1, 2, 3))


def test_run_size_floor_keeps_every_size_the_pixel_check_accepted():
    # .045 * .664 = .02988 authored, but 33px/1080 = .0306 rounded: the old
    # pixel check accepted it, so neither mode may reject or "hold" it now.
    args = dict(id="r", start=0, end=4, W=1920, H=1080, font_size=.045,
                lines=[{"runs": [{"text": "Hello", "scale": .664}]}])
    strict = scenes.compose(**args)
    auto = scenes.compose(**args, fit="auto")
    assert auto["fit"]["notes"] == []
    assert strict["texts"] == auto["texts"]
    # The advertised minimum stays valid although it rounds to 32px (.0296).
    scenes.compose(**{**args, "font_size": scenes.FONT_SIZE_RANGE[0],
                      "lines": [{"runs": [{"text": "Hello"}]}]})
    # An authored .29996 that rounds to 325px (.3009) is emitted at the
    # .3 maximum, never as a layer the EDL rejects.
    top = scenes.compose(id="t", start=0, end=4, W=1920, H=1080, font_size=.2,
                         box=[.05, .1, .95, .9],
                         lines=[{"size": .836, "runs": [{"text": "Hi", "scale": 1.794}]}])
    assert max(t["font_size"] for t in top["texts"]) <= .3
    e = default_edl(4)
    e["texts"] = top["texts"]
    validate_edl(e, 4)


def test_valid_scene_is_byte_identical_in_auto_and_strict():
    strict = scenes.compose(**_scene())
    auto = scenes.compose(**_scene(), fit="auto")
    assert auto["fit"]["notes"] == []
    assert {k: v for k, v in auto.items() if k != "fit"} == \
        {k: v for k, v in strict.items() if k != "fit"}


def test_typography_tool_defaults_to_auto_and_reports_applied_values():
    ctx = _Ctx()
    out = agent_tools.set_typography_scene(
        ctx, "hook", 0, 4, [{"runs": [{"text": "Think bigger"}]}],
        box=[0, .1, 1, .3], font_size=.2, leading=2.5)
    assert out.startswith("EDL v"), out
    assert "fit=auto adjusted:" in out
    assert "leading 2.5→1.8" in out and "font_size 0.2→" in out
    assert "Applied font_size=" in out and "box=[0.045, 0.1, 0.955, 0.3]" in out
    assert all(t["id"].startswith("ts_hook__") for t in ctx.edl["texts"])

    clean = agent_tools.set_typography_scene(
        ctx, "calm", 5, 9, [{"runs": [{"text": "Calm"}]}])
    assert clean.startswith("EDL v") and "fit=auto" not in clean


def test_layout_tools_treat_null_fit_as_the_auto_default():
    ctx = _Ctx()
    out = agent_tools.set_typography_scene(
        ctx, "hook", 0, 4, [{"runs": [{"text": "Think bigger"}]}],
        box=[0, .1, 1, .3], font_size=.2, fit=None)
    assert out.startswith("EDL v") and "fit=auto adjusted:" in out, out
    out = agent_tools.set_editorial_graphic(
        ctx, "card", "statement", "Build it", 5, 11, box=[-.1, .2, 1.1, .7],
        fit=None)
    assert out.startswith("EDL v") and "fit=auto adjusted:" in out, out


def test_typography_tool_strict_rejects_with_every_problem_numbered():
    ctx = _Ctx()
    out = agent_tools.set_typography_scene(
        ctx, "bad id!", 0, 4,
        [{"runs": [{"text": "x", "scale": 9}]}, {"runs": []}],
        box=[0, 0, 1, 1], leading=.2, fit="strict")
    assert out.startswith("REJECTED (nothing saved): fix all of these together:")
    for fragment in ("(1)", "(2)", "(3)", "(4)", "(5)", "id must be",
                     "leading must be between", "run scale must be between",
                     "at least one run", "safe area"):
        assert fragment in out, (fragment, out)
    assert ctx.writes == []


# ── 3. Editorial fit=auto ──────────────────────────────────────────────────

def test_headline_auto_steps_size_down_to_fit_three_lines():
    args = dict(id="h", kind="headline", speaker="Ada Lovelace",
                text="Engines will compose elaborate music of any complexity",
                start=0, end=12, box=[.08, .1, .55, .3], font_size=.085)
    with pytest.raises(ValueError, match="three lines"):
        editorial_graphics.compose(**args)
    result = editorial_graphics.compose(**args, fit="auto")
    fit = result["fit"]
    assert fit["font_size"] < .085
    assert any("Headline font_size 0.085→" in n for n in fit["notes"])
    rows = {round(t["y"], 4) for t in result["texts"]}
    assert len(rows) <= editorial_graphics.HEADLINE_MAX_LINES
    _inside(result["texts"], fit["box"], 1080, 1920, end=12)


def test_editorial_auto_clamps_box_and_lists_all_unfixable_problems():
    statement = editorial_graphics.compose(
        id="s", kind="statement", text="Build something people need", start=0,
        end=6, box=[-.1, .2, 1.1, .7], fit="auto")
    assert statement["fit"]["box"] == [0, .2, 1, .7]
    assert "box [-0.1,0.2,1.1,0.7]→[0,0.2,1,0.7]" in statement["fit"]["notes"][0]
    with pytest.raises(ValueError, match="picture must be"):
        editorial_graphics.compose(id="s", kind="statement", text="Build",
                                   start=0, end=6, box=[-.1, .2, 1.1, .7])

    with pytest.raises(scenes.LayoutRejected) as many:
        editorial_graphics.compose(id="h", kind="headline", text="A claim",
                                   secondary="extra", start=0, end=.5,
                                   fit="auto")
    joined = " ".join(many.value.problems)
    assert "Allow at least" in joined
    assert "without extra supporting labels" in joined
    assert "verified speaker name" in joined


CARD_KINDS = [k for k in editorial_graphics.KINDS if k != "headline"]
_CARD_COPY = {"comparison": dict(text="Rent", secondary="Own"),
              "metric": dict(text="42%")}


@pytest.mark.parametrize("kind", CARD_KINDS)
@pytest.mark.parametrize("box", [[.5, .2, .52, .7], [.1, .5, .215, .9],
                                 [.95, .3, .99, .45]])
def test_auto_fit_thin_cards_always_compose_a_valid_edl(kind, box):
    copy = _CARD_COPY.get(kind, dict(text="Build it"))
    result = editorial_graphics.compose(id="g", kind=kind, start=0, end=6,
                                        box=box, fit="auto", **copy)
    x0, _, x1, _ = result["fit"]["box"]
    assert x1 - x0 >= editorial_graphics.CARD_MIN_WIDTH - 1e-9
    assert "card box [" in " ".join(result["fit"]["notes"])
    e = default_edl(7)
    e["texts"], e["vectors"] = result["texts"], result["vectors"]
    validate_edl(e, 7)
    with pytest.raises(scenes.LayoutRejected, match="card box must be at least 0.12 wide"):
        editorial_graphics.compose(id="g", kind=kind, start=0, end=6,
                                   box=[.1, .5, .215, .9], **copy)


def test_thin_card_through_the_tool_is_saved_not_bounced_by_the_edl():
    ctx = _Ctx()
    out = agent_tools.set_editorial_graphic(
        ctx, "g", "statement", "Build it", 0, 6, box=[.5, .2, .52, .7])
    assert out.startswith("EDL v"), out
    assert "fit=auto adjusted: card box [0.5,0.2,0.52,0.7]→[0.45,0.2,0.57,0.7]" in out


def test_rejected_write_still_names_the_auto_repairs():
    fit = {"notes": ["box [0,0,1,1]→[0.045,0.04,0.955,0.96]"], "box": [.045, .04, .955, .96]}
    out = agent_tools._fit_report("REJECTED (EDL v3 unchanged): texts.0 bad", fit)
    assert out.startswith("REJECTED (EDL v3 unchanged)")
    assert "Nothing was saved" in out and "box [0,0,1,1]→" in out
    assert "fit=auto adjusted" not in out
    assert agent_tools._fit_report("REJECTED: x", {"notes": []}) == "REJECTED: x"


def test_editorial_tool_defaults_to_auto_and_reports():
    ctx = _Ctx()
    out = agent_tools.set_editorial_graphic(
        ctx, "topic", "headline", "Engines will compose elaborate music of any complexity",
        0, 12, speaker="Ada Lovelace", box=[.08, .1, .55, .3], font_size=.085)
    assert out.startswith("EDL v"), out
    assert "fit=auto adjusted: Headline font_size 0.085→" in out
    bad = agent_tools.set_editorial_graphic(
        ctx, "x", "statement", "Build", 0, .5, speaker="Nobody", fit="strict")
    assert bad.startswith("REJECTED (nothing saved)")
    assert "Allow at least" in bad and "speaker and font_size belong" in bad


# ── 4. Overlay motion ──────────────────────────────────────────────────────

def test_describe_edl_handles_animated_overlay_scale():
    e = default_edl(5)
    e["overlays"] = [{"id": "ov1", "asset_key": "images/logo.png", "kind": "image",
                      "start": 0, "duration_s": 2,
                      "scale": [{"t": 0, "v": .2}, {"t": 1, "v": .5}]}]
    text = describe_edl(validate_edl(e, 5).model_dump(), 5)
    assert "logo.png@0s 0.2-0.5w*" in text


def test_cover_overlay_motion_applies_what_it_can_and_names_what_it_dropped():
    class Ctx(_Ctx):
        pass
    ctx = Ctx(duration=5)
    ctx.edl["overlays"] = [{"id": "ov1", "asset_key": "image.png", "kind": "image",
                            "start": 0.0, "duration_s": 2.0, "fit": "cover"}]
    ctx.edl = validate_edl(ctx.edl, 5).model_dump()
    out = agent_tools.set_overlay_motion(ctx, "ov1", {
        "scale": [{"t": 0, "v": 1}, {"t": 1, "v": 1.2}],
        "opacity": [{"t": 0, "v": 0}, {"t": .2, "v": 1}]})
    assert out.startswith("EDL v"), out
    assert "Not applied: scale — overlay ov1 has fit=cover, which always fills the whole frame" in out
    ov = ctx.edl["overlays"][0]
    assert isinstance(ov["opacity"], list) and not isinstance(ov["scale"], list)
    # A move that changes nothing still names what it dropped.
    agent_tools.set_overlay_motion(ctx, "ov1", {"opacity": .5})
    again = agent_tools.set_overlay_motion(ctx, "ov1", {"x": .2, "opacity": .5})
    assert again.startswith("NO CHANGE") and "Not applied: x" in again, again


def test_picture_overlay_motion_names_the_picture_rectangle():
    ctx = _Ctx(duration=5)
    ctx.edl["overlays"] = [{"id": "ov2", "asset_key": "image.png", "kind": "image",
                            "start": 0.0, "duration_s": 2.0, "fit": "picture"}]
    ctx.edl = validate_edl(ctx.edl, 5).model_dump()
    out = agent_tools.set_overlay_motion(ctx, "ov2", {"x": .3, "opacity": .5})
    assert out.startswith("EDL v"), out
    assert "fit=picture, which always fills the program's picture rectangle" in out
    assert "full-frame" not in out and "whole frame" not in out
    only = agent_tools.set_overlay_motion(ctx, "ov2", {"y": .3})
    assert only.startswith("REJECTED") and "fit=picture" in only and "ignore y" in only


# ── 5. make_shorts: one reply, optional sentence snap ──────────────────────

_SENTENCES = [
    {"t0": 10.0, "t1": 13.0, "speaker": 0, "text": "Why did you decide to start?"},
    {"t0": 13.4, "t1": 30.0, "speaker": 1, "text": "Because the problem kept getting worse."},
    {"t0": 31.0, "t1": 52.0, "speaker": 0, "text": "So what changed for you after that year?"},
    {"t0": 60.0, "t1": 64.0, "speaker": 1, "text": "Short aside."},
    {"t0": 90.0, "t1": 140.0, "speaker": 1, "text": "A long story."},
]


def _shorts_ctx(mcp=True, captured=None):
    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.has_active_job:
                return False
            if fn is dbx.enqueue_job:
                if captured is None:
                    raise AssertionError("must not enqueue")
                captured["payload"] = args[3]
                return 901
            raise AssertionError(fn)
    return SimpleNamespace(
        project={}, has_main_video=True, duration=300.0,
        index={"words": [{"w": "x"}], "sentences": _SENTENCES},
        db=FakeDb(), project_id=7,
        job={"user_id": 60, "type": "mcp_tool"} if mcp else {"user_id": 60})


def test_mcp_snaps_near_boundaries_and_reports_exactly_what_moved():
    captured = {}
    out = agent_tools.make_shorts(_shorts_ctx(captured=captured), clips=[
        {"start": 13.7, "end": 29.2, "title": "The answer"},
        {"start": 30.2, "end": 52.9, "title": "What changed"}])
    assert "job 901" in out
    assert "Snapped to sentence boundaries" in out
    assert "clip 1 start 13.7s→13.4s (-0.30s, sentence start: \"Because the problem kept getting worse.\")" in out
    assert "clip 1 end 29.2s→30s (+0.80s" in out
    assert "clip 2 start 30.2s→31s (+0.80s" in out
    assert "clip 2 end 52.9s→52s (-0.90s" in out
    queued = captured["payload"]["clips"]
    assert [(c["start"], c["end"]) for c in queued] == [(13.4, 30.0), (31.0, 52.0)]
    materialized = shorts._caller_planned_clips(
        captured["payload"], {"sentences": _SENTENCES}, 300.0)
    assert [(c["start"], c["end"]) for c in materialized] == [(13.4, 30.0), (31.0, 52.0)]


def test_aligned_mcp_clips_report_no_moves():
    captured = {}
    out = agent_tools.make_shorts(_shorts_ctx(captured=captured), clips=[
        {"start": 13.4, "end": 30.0, "title": "The answer"}])
    assert "job 901" in out and "Snapped" not in out


def test_every_clip_problem_is_reported_in_one_reply():
    out = agent_tools.make_shorts(_shorts_ctx(), clips=[
        {"start": 7.0, "end": 30.0, "title": "Far start"},          # 3s off
        {"start": 60.0, "end": 64.0, "title": "Too short"},
        {"start": 90.0, "end": 140.0, "title": ""},                 # no title
        {"start": 13.4, "end": 30.0, "title": "Overlaps clip 1", "score": 500},
    ])
    assert out.startswith("REJECTED (nothing was created): 5 problems")
    for fragment in (
            "clip 1 starts mid-thought at 7s; use the sentence boundary 10s "
            "(beyond the 1.5s snap tolerance",
            "clip 2 must be 10-120 seconds, got 4.0s",
            "clip 3 title must be a non-empty string",
            "clip 4 score must be an integer from 0 to 100",
            "must not overlap: clip 1 (7-30s) and clip 4 (13.4-30s)"):
        assert fragment in out, (fragment, out)


def test_in_app_agent_keeps_frozen_boundaries_unless_it_opts_in():
    out = agent_tools.make_shorts(_shorts_ctx(mcp=False), clips=[
        {"start": 13.7, "end": 30.0, "title": "The answer"}])
    assert out.startswith("REJECTED: clip 1 starts mid-thought at 13.7s")
    captured = {}
    out = agent_tools.make_shorts(_shorts_ctx(mcp=False, captured=captured), clips=[
        {"start": 13.7, "end": 30.0, "title": "The answer"}], snap="sentence")
    assert "clip 1 start 13.7s→13.4s" in out
    assert captured["payload"]["source"] == "agent_direct"
    bad = agent_tools.make_shorts(_shorts_ctx(), clips=[
        {"start": 13.4, "end": 30.0, "title": "x"}], snap="word")
    assert bad == "REJECTED: snap must be one of sentence, none"


def test_make_shorts_schema_and_bounds_match_the_worker():
    props = _schema("make_shorts")
    assert props["snap"]["enum"] == list(agent_tools.SHORT_SNAP_MODES)
    assert agent_tools.SHORT_CLIP_MIN_S == shorts.CLIP_MIN_S
    assert agent_tools.SHORT_CLIP_MAX_S == shorts.CLIP_MAX_S
    item = props["clips"]["items"]["properties"]
    assert item["start"]["minimum"] == 0 and item["title"]["minLength"] == 1
    desc = agent_tools.TOOLS["make_shorts"][1]
    assert "10-120s" in desc and "within 1.5s" in desc


# ── 6. Routing metadata and compact contracts ──────────────────────────────

# Backend MCP session tools (backend/routes/mcp.py SESSION_TOOLS). They are
# named in the shorts department for orientation and filtered out of the
# in-house catalog because the worker registry does not serve them.
_MCP_SESSION_TOOLS = {"shorts_status", "open_short", "open_project"}


def test_every_domain_tool_exists():
    named = set().union(*agent_tools.TOOL_DOMAINS.values())
    missing = named - set(agent_tools.TOOLS) - _MCP_SESSION_TOOLS
    assert not missing, sorted(missing)
    assert "find_repetitions" not in named and "suggest_segments" not in named


def test_look_at_compact_contract_matches_behaviour():
    compact = next(row["function"]["description"]
                   for row in agent_tools.openai_tools(compact=True)
                   if row["function"]["name"] == "look_at")
    assert "no render needed" in compact
    assert "times=[...] are SOURCE seconds" in compact
    assert "output_times" in compact
    assert "rendered=true" in compact and "needs render_preview first" in compact
    assert "Requires render_preview for the latest" not in compact


def test_look_at_source_and_program_paths_do_not_require_a_preview(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_tools, "_look_at_output",
                        lambda ctx, times, question: calls.append(times) or "ok")

    class Ctx:
        def latest_edl(self):
            raise AssertionError("program/source looks must not need a render")
    assert agent_tools.look_at(Ctx(), output_times=[1.0]) == "ok"
    assert calls == [[1.0]]


# ── 7. Skills may only name live tools ─────────────────────────────────────

# Skills that STILL name retired/missing tools. Another phase rewrites these
# skills; this baseline may only shrink. Remove an entry once its skill is
# fixed (the test fails until you do, so the list never goes stale).
KNOWN_STALE_SKILL_TOOLS = {
    "audio.md": ["fetch_music", "research_music", "search_music"],
    "broll-inserts.md": ["generate_image"],
    "generate-fetch.md": ["generate_image"],
    "music.md": ["audition_music_candidates", "fetch_music",
                 "research_music", "search_music"],
    "short-form-direction.md": ["research_music"],
    "text-graphics.md": ["generate_image"],
}


def test_ci_static_registry_matches_runtime_registry():
    # CI validates skills before worker dependencies exist, so it reads the
    # registry statically. A new registration pattern that it cannot see
    # must be taught to skill_validator.registered_tool_names.
    static = skill_validator.live_tool_names()
    runtime = set(agent_tools.TOOLS)
    assert static == runtime, ("static registry differs from agent_tools.TOOLS: "
                               f"missing {sorted(runtime - static)}, "
                               f"extra {sorted(static - runtime)}")
    assert skill_validator.retired_tool_names() >= {"research_music", "generate_image"}


def test_static_registry_follows_every_merge_pattern(tmp_path):
    (tmp_path / "extra_tools.py").write_text(
        "BASE = {'base_tool': 1}\nTOOL_SPECS = {**BASE, 'extra_tool': 2}\n",
        encoding="utf-8")
    tools = tmp_path / "agent_tools.py"
    tools.write_text(
        "import extra_tools\nLOCAL = {'local_tool': 1}\n"
        "TOOLS = {'core_tool': 1, 'old_tool': 2}\n"
        "TOOLS.update(extra_tools.TOOL_SPECS)\nTOOLS.update(LOCAL)\n"
        "TOOLS['late_tool'] = 3\n"
        "for _retired_tool in ('old_tool',):\n    TOOLS.pop(_retired_tool)\n",
        encoding="utf-8")
    assert skill_validator.live_tool_names(tools) == {
        "core_tool", "base_tool", "extra_tool", "local_tool", "late_tool"}


def test_skills_reference_only_tools_in_TOOLS():
    # Judged against the imported registry, so a registration the static
    # reader does not understand cannot fail this ratchet.
    found = skill_validator.stale_tool_references(live=set(agent_tools.TOOLS))
    new = {skill: sorted(set(names) - set(KNOWN_STALE_SKILL_TOOLS.get(skill, [])))
           for skill, names in found.items()}
    new = {k: v for k, v in new.items() if v}
    assert not new, f"skills reference tools missing from TOOLS: {new}"
    fixed = {skill: sorted(set(names) - set(found.get(skill, [])))
             for skill, names in KNOWN_STALE_SKILL_TOOLS.items()}
    fixed = {k: v for k, v in fixed.items() if v}
    assert not fixed, ("these skills no longer reference these tools; delete "
                       f"them from KNOWN_STALE_SKILL_TOOLS: {fixed}")


def test_stale_reference_scan_catches_bare_and_called_names(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "x.md").write_text(
        "Use research_music for a bed, then add_music. Try find_repetitions(). "
        "Then add_motion_graphic(template='x'). Fields like duration_s are fine.",
        encoding="utf-8")
    found = skill_validator.stale_tool_references(skills, live=set(agent_tools.TOOLS))
    assert found == {"x.md": ["find_repetitions", "research_music"]}
    assert skill_validator.stale_tool_references(skills) == found
