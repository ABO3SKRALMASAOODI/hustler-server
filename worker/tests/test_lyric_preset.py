"""'lyric' caption preset (round 99b) — the mixed-face lyric edit.

Registration across every surface that must agree (captions PRESETS, the
schema Literal, the tool enum, the taste exception), plus one end-to-end
.ass emission: Poppins phrases with the emphasized word switched to the
white Playfair italic at ~2x.

Run from worker/:  python3 -m pytest tests/test_lyric_preset.py -q
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import captions as caplib                                    # noqa: E402
from timeline import Timeline                                # noqa: E402


def test_registered_everywhere():
    import agent_tools
    from schemas import CaptionStyle
    assert "lyric" in caplib.PRESETS
    assert "script" in caplib.TREATMENTS
    assert "lyric" in agent_tools.CAPTION_PRESETS
    # schema accepts it (a preset missing here is silently dropped by
    # pydantic and the write reports NO CHANGE — the classic trap)
    assert CaptionStyle(preset="lyric").preset == "lyric"
    assert CaptionStyle(emphasis="script").emphasis == "script"


def test_lyric_look_is_the_reference():
    p = caplib.PRESETS["lyric"]
    assert p["font"] == "Poppins Black"
    assert p["uppercase"] is False           # lowercase phrases
    assert p["align"] == "center" and p["position"] == "middle"
    assert p["layout"] == "stack"
    assert p["emphasis"] == "script"         # white italic serif hero word
    assert p["emph_scale"] >= 1.8            # the hero word dominates
    # the script treatment must NOT recolour — white like its neighbours
    assert "color" not in caplib.TREATMENTS["script"]


def test_taste_allows_lyric_mid_frame_but_not_others():
    import taste
    def critique(preset):
        edl = {"keep": [[0.0, 10.0]], "effects": {},
               "captions": {"mode": "from_transcript",
                            "style": {"preset": preset,
                                      "position": "middle"}}}
        index = {"video": {"duration": 10.0},
                 "words": [{"w": "hi", "t0": 0.5, "t1": 0.8}]}
        return taste.critique(edl, index, Timeline(edl["keep"]),
                              720, 1280, "add captions")
    assert not any("mid-frame" in f for f in critique("lyric"))
    assert any("mid-frame" in f for f in critique("podcast"))


def test_ass_emission_uses_both_fonts():
    words = [{"w": "we", "t0": 0.0, "t1": 0.2},
             {"w": "gotta", "t0": 0.25, "t1": 0.5},
             {"w": "be", "t0": 0.55, "t1": 0.7},
             {"w": "excited", "t0": 0.8, "t1": 1.4}]
    edl = {"keep": [[0.0, 5.0]],
           "captions": {"mode": "from_transcript",
                        "style": {"preset": "lyric"},
                        "emphasis_words": ["excited"]}}
    index = {"video": {"duration": 5.0}, "words": words}
    tl = Timeline(edl["keep"])
    with tempfile.TemporaryDirectory() as td:
        path = caplib.build_ass(edl, index, tl, os.path.join(td, "l.ass"),
                                play_res=(720, 1280))
        content = open(path).read()
    assert "Poppins Black" in content
    assert "Playfair Display Black" in content     # the script hero word
    assert "excited" in content
    # no uppercase transform snuck in
    assert "EXCITED" not in content
