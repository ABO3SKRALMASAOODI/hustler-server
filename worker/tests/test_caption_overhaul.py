"""Caption visual-system regressions added after the full preset audit."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools
import captions
from schemas import CaptionStyle


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
