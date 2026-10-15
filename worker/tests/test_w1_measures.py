"""MEASURES: the render's size report (render_qc) on the lockups the
Oct 11 podcast run (JRE #2054) judged by hand because the report was wrong.

Each case is the run's own failure, rebuilt on a synthetic composition so
it holds on CI's fonts: the defect is in what the report picks and how it
reads lines, not in a pixel margin.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont

import render_qc

FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "fonts", "InterDisplay-Black.ttf")


def _comp(rows, W=540, H=960):
    """A transparent composition: rows of (text, px size, y) in white, or
    ("BLOCK", (x0, y0, x1, y1)) for a filled rectangle."""
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    for row in rows:
        if row[0] == "BLOCK":
            d.rectangle(row[1], fill=(255, 255, 255, 255))
            continue
        text, size, y = row
        f = ImageFont.truetype(FONT, size)
        w = d.textlength(text, font=f)
        d.text(((W - w) / 2, y), text, font=f, fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_a_counter_reads_at_its_number_not_its_label():
    """s06: a 13.7% counter '50%' over 'HEAVIER THAN ME' read 2.8%: the
    two-glyph number line was dropped as a stray next to the label's 13
    letters, so the counter outgrew the payoff with no warning."""
    m = render_qc.ink_lines(_comp([("50", 150, 300),
                                   ("HEAVIER THAN ME", 26, 520)]))
    assert m["cap"] > 0.09
    assert len(m["lines"]) == 2


def test_a_lone_giant_glyph_is_a_line_and_a_wide_block_is_not():
    """s04/s03: the payoff's giant '1' over two lines of words read at the
    words' size; a cell grid's frame is not a glyph."""
    m = render_qc.ink_lines(_comp([("1", 200, 200),
                                   ("THAT SETS YOUR", 28, 470),
                                   ("PRODUCTION RATE", 28, 520)]))
    assert m["cap"] > 0.12
    g = render_qc.ink_lines(_comp([("BLOCK", (40, 300, 500, 400)),
                                   ("ONE CELL", 20, 450),
                                   ("TURNS RED", 20, 480)]))
    assert g["cap"] < 0.03


def _items(*rows):
    return [{"id": i, "template": t, "start": s, "end": e, "tier": tier,
             "purpose": p, "persistent": False, "layer": "above_captions"}
            for i, t, s, e, tier, p in rows]


def test_the_payoff_is_named_by_tier_or_id_never_by_a_mention():
    """s05: 'smartest' (purpose: 'sets up the payoff's WAY SMARTER') was
    read as the payoff and the advice told the editor to shrink the real
    one; s04: an html cell grid ('payoff image: ...') was read as the
    payoff at 0.3%."""
    s05 = _items(("smartest", "phrase_build", 6.1, 7.5, None,
                  "hero: thesis; sets up the payoff's WAY SMARTER"),
                 ("payoff", "phrase_build", 28.2, 30.9, None,
                  "payoff: the question as the largest red lockup"))
    assert render_qc._payoff_id(s05, 31.0) == "payoff"
    s04 = _items(("grid", "html", 22.4, 26.2, None, "payoff image: cells"),
                 ("closer", "phrase_build", 26.1, 28.9, None, "the close"))
    assert render_qc._payoff_id(s04, 29.0) == "closer"
    tiered = _items(("a", "word_slam", 1, 2, None, "payoff: x"),
                    ("b", "word_slam", 20, 22, "payoff", "y"))
    assert render_qc._payoff_id(tiered, 25.0) == "b"


def test_the_hook_slam_is_measured_before_the_band_headline():
    """s01: the persistent band headline was reported as the hook (1.18x
    the captions) while the 6.6% hook slam that owned frame 0 was never
    measured. The slam is the hook; the band is reported beside it."""
    items = [
        {"id": "hook", "template": "word_slam", "start": 0.0, "end": 1.95,
         "tier": "hook", "persistent": False, "layer": "above_captions"},
        {"id": "headline", "template": "headline", "start": 1.95,
         "end": 26.0, "tier": None, "persistent": True,
         "layer": "above_captions"},
        {"id": "odds", "template": "counter", "start": 1.95, "end": 3.9,
         "tier": None, "persistent": False, "layer": "above_captions"},
        {"id": "payoff", "template": "word_slam", "start": 18.8,
         "end": 20.1, "tier": "payoff", "persistent": False,
         "layer": "above_captions"},
    ]
    tplan = {"items": items, "hook": "hook", "band": "headline",
             "payoff": "payoff"}
    tres = {"items": {"hook": {"cap": 0.0672}, "headline": {"cap": 0.0349},
                      "odds": {"cap": 0.0922}, "payoff": {"cap": 0.1016}},
            "cues": [{"cap": 0.0297, "box": [0.1, 0.8, 0.9, 0.85], "t": 5.0,
                      "text": "x"}]}
    tq = render_qc.type_findings(tres, tplan, [])
    assert tq["caps"]["hook"] == ("hook", 0.0672)
    assert tq["caps"]["band"] == ("headline", 0.0349)
    assert [i for i, _c in tq["caps"]["lockups"]] == ["odds", "payoff"]
    line = render_qc.measures_line({"type": tq, "picture": {
        "windows": [{"id": None, "t0": 0, "t1": 26, "area": 1.0,
                     "upscale": 1.8}], "floor": 0.54}})
    assert "hook 'hook' 6.7%" in line
    assert "band headline 'headline' 3.5%" in line
    assert "'odds' 9.2%" in line            # every lockup, counters included
    assert "picture area 1.00 (full frame" in line   # full-bleed reported
