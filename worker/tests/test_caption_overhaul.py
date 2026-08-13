"""Caption visual-system regressions added after the full preset audit."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import captions
from schemas import CaptionStyle, validate_edl


def _words(text, step=0.35):
    out = []
    for i, token in enumerate(text.split()):
        out.append({"w": token, "t0": i * step, "t1": i * step + 0.25})
    return out


def test_semantic_emphasis_is_sparse_kept_and_unicode_safe():
    class Ctx:
        index = {"words": _words(
            "this is the ordinary setup but revenue jumped 300% overnight "
            "ثم تحسنت النتيجة بسرعة")}

    edl = {"keep": [[0, 20]]}
    chosen = agent_tools._auto_caption_emphasis(Ctx(), edl)
    keys = {agent_tools._caption_token_key(x) for x in chosen}
    assert "300" in " ".join(chosen)
    assert not keys & {"this", "is", "the", "but"}
    assert len(chosen) <= 3  # roughly one hero per 7 words, never a word salad
    assert agent_tools._caption_token_key("النتيجة") == "النتيجة"

    # The strongest word outside the cut must never leak into the EDL list.
    clipped = agent_tools._auto_caption_emphasis(Ctx(), {"keep": [[0, 1.5]]})
    assert all("300" not in x for x in clipped)


def test_production_controls_validate_and_plus_jakarta_is_real():
    st = CaptionStyle(
        preset="documentary", font="Plus Jakarta Sans",
        outline_color="#123456", outline_width=2.5, shadow=1,
        background_color="#101820", background_opacity=0.7,
        tracking=1.25, text_align="left")
    assert st.font == "Plus Jakarta Sans"
    assert st.background_opacity == 0.7
    assert st.text_align == "left"
    assert "PlusJakartaSans-ExtraBold.ttf" in os.listdir(captions.FONTS_DIR)


def test_documentary_preset_emits_one_vector_panel_behind_the_block():
    line = captions.style_line(
        "Default", {"preset": "documentary"}, (1080, 1920))
    fields = line.split(",")
    # Premium inline word styling cannot use BorderStyle 3: it makes one box
    # per word. The style stays outlined and a single drawing event is layered
    # under the complete phrase instead.
    assert fields[15] == "1"
    assert fields[1] == "Plus Jakarta Sans"
    evs = captions.events_premium(
        _words("one stable documentary subtitle panel"),
        style={"preset": "documentary"}, play_res=(1080, 1920),
        emphasis_words=["documentary"])
    panels = [e for e in evs if e.get("layer") == 0 and "\\p1" in e["text"]]
    assert panels
    assert "\\1a&H47&" in panels[0]["text"]  # 72% opacity, reverse ASS alpha


def test_explicit_emphasis_override_reaches_the_renderer():
    words = _words("make every caption feel impossible to ignore")
    evs = captions.events_premium(
        words, style={"preset": "podcast", "emphasis": "big"},
        emphasis_words=["impossible"], play_res=(1080, 1920))
    impossible = "\n".join(e["text"] for e in evs if "impossible" in e["text"])
    assert "\\fs" in impossible                 # size hierarchy exists
    assert "\\xbord" not in impossible          # no preset marker box
    assert "\\fnDM Serif Display" not in impossible


def test_every_registered_preset_has_a_renderer_definition():
    assert set(agent_tools.CAPTION_PRESETS) - {"classic"} <= set(captions.PRESETS)
    for name in ("clean", "documentary", "broadcast", "retro", "neon"):
        assert CaptionStyle(preset=name).preset == name


def test_flagship_reels_preset_has_elastic_hierarchy_and_clean_states():
    assert CaptionStyle(preset="reels", animation="elastic").preset == "reels"
    evs = captions.events_premium(
        _words("make every frame impossible to ignore", 0.34),
        style={"preset": "reels"}, emphasis_words=["impossible"],
        play_res=(1080, 1920),
        design_version=captions.CAPTION_DESIGN_VERSION)
    body = "\n".join(e["text"] for e in evs)
    # A three-stage overshoot/rebound/settle curve, not a binary scale pop.
    assert r"\fscx126\fscy126" in body
    assert r"\fscx94\fscy94" in body
    assert r"\1c&H5AE1FF&" in body       # #FFE15A in ASS BGR order
    states = sorted(set((e["start"], e["end"]) for e in evs))
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(states, states[1:]))


def test_every_new_caption_motion_validates_and_emits_real_tags(tmp_path):
    motions = ("elastic", "bounce", "swing", "zoom_blur")
    for motion in motions:
        assert CaptionStyle(animation=motion).animation == motion
        out = tmp_path / f"{motion}.ass"
        captions.write_ass(
            [{"start": 0, "end": 1, "text": "LAND"}], str(out),
            {"animation": motion}, play_res=(1080, 1920))
        body = out.read_text(encoding="utf-8")
        assert r"\t(" in body
    assert r"\blur" in (tmp_path / "zoom_blur.ass").read_text(encoding="utf-8")


def test_design_v2_is_explicit_and_historical_edls_stay_unversioned():
    old = validate_edl({"keep": [[0, 10]], "captions": {
        "mode": "from_transcript", "style": {"preset": "clean"}}}, 10)
    new = validate_edl({"keep": [[0, 10]], "captions": {
        "mode": "from_transcript", "design_version": 2,
        "style": {"preset": "clean"}}}, 10)
    assert old.model_dump()["captions"]["design_version"] is None
    assert new.model_dump()["captions"]["design_version"] == \
        captions.CAPTION_DESIGN_VERSION

    restrained = validate_edl({"keep": [[0, 10]], "captions": {
        "mode": "from_transcript", "design_version": 2,
        "emphasis_words": [], "emphasis_mode": "off",
        "style": {"preset": "clean"}}}, 10).model_dump()["captions"]
    assert restrained["emphasis_words"] is None
    assert restrained["emphasis_mode"] == "off"


def test_v2_phrase_grouping_obeys_breaths_and_avoids_connector_orphans():
    words = _words("this changes the result because it actually works", 0.28)
    # A real breath must reset the visual phrase even though it is below the
    # historical 1.2-second threshold.
    words[4]["t0"] += 0.8
    words[4]["t1"] += 0.8
    for i in range(5, len(words)):
        words[i]["t0"] += 0.8
        words[i]["t1"] += 0.8
    p = captions.PRESETS["clean"]
    chunks = captions._premium_chunks_v2(words, 4, 80, p)
    assert chunks[0][-1]["t1"] < chunks[1][0]["t0"] - 0.6
    assert all(captions._norm_word(chunk[-1]["w"])
               not in {"the", "because", "and", "to", "of"}
               for chunk in chunks[:-1])


def test_v2_stack_layout_balances_lines_instead_of_leaving_a_widow():
    p = captions.PRESETS["clean"]
    disp = ["World", "class", "captions", "matter"]
    mults = [1.0] * len(disp)
    lines = captions._stack_layout_v2(disp, mults, p, 48, 900)
    assert [len(line) for line in lines] == [2, 2]


def test_v2_long_hero_stays_larger_than_its_support_words():
    p = captions.PRESETS["stacked"]
    s = captions._norm_style({"preset": "stacked"})
    px = captions._premium_font_px(p, s, (1080, 1920))
    usable = 1080 * (1 - 2 * captions.PREMIUM_MARGIN_X["center"])
    mults = captions._stack_mults(
        ["feel", "intentional"], [None, "big"], p, s, px, usable,
        preserve_hierarchy=True)
    assert mults[1] >= mults[0] * 1.3


def test_v2_readable_families_keep_punctuation_and_round_the_panel():
    words = _words("Wait, is this real?", 0.35)
    evs = captions.events_premium(
        words, style={"preset": "documentary"}, play_res=(1080, 1920),
        design_version=captions.CAPTION_DESIGN_VERSION)
    text = "\n".join(e["text"] for e in evs)
    assert "Wait," in text and "real?" in text
    panel = next(e["text"] for e in evs if e.get("layer") == 0)
    assert " b " in panel  # cubic rounded corners, not a hard rectangle


def test_v2_clears_completed_phrases_on_a_breath():
    words = _words("first thought second thought", 0.35)
    # Force a sentence/breath boundary before "second".
    words[1]["w"] = "thought."
    words[2]["t0"], words[2]["t1"] = 2.0, 2.25
    words[3]["t0"], words[3]["t1"] = 2.35, 2.6
    old = captions.events_premium(
        words, style={"preset": "clean"}, play_res=(1080, 1920))
    new = captions.events_premium(
        words, style={"preset": "clean"}, play_res=(1080, 1920),
        design_version=2)
    assert old[0]["end"] > new[0]["end"]
    assert new[0]["end"] <= words[1]["t1"] + 0.43


def test_auto_emphasis_prefers_measured_vocal_landing(monkeypatch):
    ctx = agent_tools.ToolContext.__new__(agent_tools.ToolContext)
    ctx.index = {"words": _words("ordinary rareword but land now", 0.4)}
    ctx.duration = 10.0
    ctx._perception = None
    monkeypatch.setattr(agent_tools, "_get_perception", lambda _ctx: {})
    monkeypatch.setattr(
        agent_tools.perception, "word_stress",
        lambda _p, ws: [0.0, 0.0, 0.0, 1.0, 0.0])
    chosen = agent_tools._auto_caption_emphasis(ctx, {"keep": [[0, 10]]})
    assert [agent_tools._caption_token_key(x) for x in chosen] == ["land"]


def test_caption_band_hysteresis_ignores_one_safe_noisy_sample():
    span = (0.0, 6.0)
    picks = [
        {"span": span, "position": "bottom",
         "scores": {"top": 0.4, "middle": 0.8, "bottom": 0.0}},
        {"span": span, "position": "top",
         "scores": {"top": 0.2, "middle": 0.8, "bottom": 0.75}},
        {"span": span, "position": "bottom",
         "scores": {"top": 0.4, "middle": 0.8, "bottom": 0.0}},
    ]
    stable = agent_tools._stabilize_caption_positions(picks)
    assert [row["position"] for row in stable] == ["bottom"] * 3


def test_manual_caption_items_use_the_authored_timing_without_hidden_hold():
    """The production MCP failure authored sequential 50ms items, but the
    compiler silently stretched every one to 600ms and stacked them."""
    class TL:
        @staticmethod
        def span_to_out(start, end):
            return [(start, end)]

    evs = captions.events_from_items([
        {"text": "one", "start": 1.00, "end": 1.05},
        {"text": "two", "start": 1.05, "end": 1.10},
    ], TL())
    assert [(x["start"], x["end"]) for x in evs] == [
        (1.00, 1.05), (1.05, 1.10)]


def test_centisecond_span_validation_does_not_reject_binary_float_005():
    edl = validate_edl({
        "keep": [[0, 1600]],
        "captions": [{"text": "now", "start": 1577.83, "end": 1577.88}],
    }, 1600)
    assert edl.captions[0].end - edl.captions[0].start > 0.049


def test_animation_none_keeps_karaoke_colour_but_removes_all_motion_tags():
    evs = captions.events_dynamic(
        _words("calm captions"),
        style={"dynamic": True, "animation": "none",
               "highlight_color": "#00FFAA"})
    text = "\n".join(e["text"] for e in evs)
    assert "\\1c&HAAFF00&" in text
    assert "\\t(" not in text
    assert "\\fscx" not in text and "\\fscy" not in text


def test_exact_anchor_reaches_classic_ass_and_slide_target():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "classic.ass")
        captions.write_ass(
            [{"start": 0, "end": 1, "text": "safe",
              "item_style": {"anchor_y": 0.37}}], path,
            play_res=(1080, 1920))
        text = open(path, encoding="utf-8").read()
        assert r"\an5\pos(540,710)" in text

        slide = os.path.join(td, "slide.ass")
        captions.write_ass(
            [{"start": 0, "end": 1, "text": "safe",
              "item_style": {"anchor_y": 0.37,
                             "animation": "slide_up"}}], slide,
            play_res=(1080, 1920))
        moved = open(slide, encoding="utf-8").read()
        assert r"\an5\move(540,786,540,710,0,160)" in moved
        assert r"\pos(" not in moved


def test_design_v2_placement_falls_back_without_deleting_spoken_words():
    words = _words("never silently disappear")
    for word in words:
        word["src_t0"], word["src_t1"] = word["t0"], word["t1"]
    track = [{"t0": 0.0, "t1": 0.5, "position": "top",
              "anchor_y": 0.22}]
    modern = captions._placement_runs(
        words, track, fallback_position="bottom", fallback_anchor_y=0.78)
    assert [w["w"] for _p, _a, run in modern for w in run] == [
        "never", "silently", "disappear"]
    assert modern[-1][0:2] == ("bottom", 0.78)
