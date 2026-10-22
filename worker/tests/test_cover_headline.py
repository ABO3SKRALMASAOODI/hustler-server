"""The cover headline (owner, Oct 2026): "make some of the shorts start with
a headline like this picture" — one huge centred title in tall condensed
caps on frame 0 ('THE RUTHLESS FOCUS OF / MARK ZUCKERBERG' over the
speaker), held 2-3 s, then faded into the talk, so the frame a feed shows
before anyone presses play is a thumbnail that sells the story.

Pinned here: the measured layout (cover.py, the browserless lanes' mirror of
motion/templates/cover_headline.html), that it owns its zone like the hook
tier (the captions wait until it exits, a standing headline band yields to
it), that ONE call (add_motion_graphic) or ONE apply_edit_batch upsert makes
it, and that the page draws it complete on frame 0 and fades it out.
"""

import asyncio
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import captions as caplib  # noqa: E402
import cover  # noqa: E402
import edit_batch  # noqa: E402
import keepout  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
from schemas import validate_edl  # noqa: E402
from test_beat_coverage import _Ctx, _edl, _run, needs_browser, probe  # noqa: E402,F401

ZUCK = "The ruthless focus of *Mark Zuckerberg*"


def _cover(start=0.0, end=2.6, **params):
    params.setdefault("text", ZUCK)
    return {"id": "cover", "template": "cover_headline", "start": start, "end": end,
            "params": motion_templates.check_params("cover_headline", params)}


# ── the layout ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [ZUCK, "Why Peter Thiel never hires from Harvard",
                                  "Sleeps at the factory", "Garry Tan on the YC rule nobody breaks"])
def test_a_cover_is_huge_balanced_and_inside_the_safe_band(text):
    lay = cover.layout({"text": text})
    assert 1 <= len(lay["lines"]) <= cover.MAX_LINES
    # one size, the widest line fills the asked width, the block bounded
    assert lay["width"] == pytest.approx(0.72, abs=0.005) or lay["fs"] >= cover.LINE_MAX * 1920 - 1
    assert lay["block_h"] <= cover.BLOCK_MAX + 1e-6
    # caps far above caption size (captions ~2.5-3% of the frame height)
    assert lay["cap"] >= 0.04, lay
    x0, y0, x1, y1 = lay["box"]
    assert cover.SAFE_TOP - 0.01 <= y0 < y1 <= cover.SAFE_BOTTOM + 0.01
    assert x0 >= 0.1 and x1 <= 0.9
    # a name is never broken across lines ('MARK / ZUCKERBERG')
    joined = [" ".join(ln) for ln in lay["lines"]]
    for name in ("MARK ZUCKERBERG", "PETER THIEL", "GARRY TAN"):
        if name in " ".join(joined):
            assert any(name in ln for ln in joined), joined


def test_forced_breaks_and_the_estimate_follow_the_title_and_y():
    lay = cover.layout({"text": "The ruthless focus of / Mark Zuckerberg"})
    assert lay["lines"] == [["THE", "RUTHLESS", "FOCUS", "OF"], ["MARK", "ZUCKERBERG"]]
    sp = motion_templates.spec("cover_headline")
    mid = keepout.nominal_ink("cover_headline", sp, {"text": ZUCK, "y": 0.5})
    low = keepout.nominal_ink("cover_headline", sp, {"text": ZUCK, "y": 0.7})
    assert tuple(mid) == tuple(cover.layout({"text": ZUCK, "y": 0.5})["box"])
    assert low[1] > mid[1] + 0.15 and low[3] <= cover.SAFE_BOTTOM + 0.01
    narrow = keepout.nominal_ink("cover_headline", sp, {"text": ZUCK, "width": 0.6})
    assert narrow[2] - narrow[0] < mid[2] - mid[0]


# ── it owns its zone ──────────────────────────────────────────────────────

def test_the_cover_owns_its_zone_the_captions_wait_until_it_exits():
    c = _cover()
    assert caplib.hook_owns_zone(c)
    assert caplib.effective_caption_mutes(_edl([c])) == [[0.0, 2.6]]
    # the editor can keep the captions running beside it; a 'cover' late in
    # the short owns nothing
    assert caplib.effective_caption_mutes(_edl([dict(c, mute_captions=False)])) == []
    assert caplib.effective_caption_mutes(_edl([dict(c, start=6.0, end=8.0)])) == []


def test_a_standing_headline_band_yields_to_the_cover_and_returns_as_it_exits():
    band = {"id": "hl", "template": "headline", "start": 0.0, "end": 30.0,
            "params": motion_templates.check_params(
                "headline", {"text": "Zuckerberg on *focus*", "y": 0.18})}
    c = _cover(end=2.6, y=0.55)
    assert motion_layer.yield_windows(band, [band, c], 1080, 1920) == [[0.0, 2.6]]
    assert motion_layer.yield_windows(band, [band], 1080, 1920) == []


# ── one call ─────────────────────────────────────────────────────────────

def test_one_add_motion_graphic_call_makes_the_cover(probe):
    ctx = _Ctx("and then " * 40, t0=0.2)
    out = motion_tools.add_motion_graphic(ctx, "cover_headline", 0.3, 2.6, id="cover",
                                          params={"text": ZUCK})
    assert out.startswith("EDL v"), out
    it = ctx.item("cover")
    # frame 0 is the cover: a start inside the opening is pinned to 0
    assert it["start"] == 0.0 and it["end"] == pytest.approx(2.6)
    assert "COVER: starts at 0" in out and "COVER: complete on frame 0" in out, out
    assert caplib.effective_caption_mutes(ctx._edl) == [[0.0, 2.6]]
    # held too long, a second cover and a hook title over it are named
    out = motion_tools.set_motion_graphic(ctx, "cover", end=5.0)
    assert "holds 5.0s" in out, out
    out = motion_tools.add_motion_graphic(ctx, "cover_headline", 0.0, 2.5, id="again",
                                          params={"text": "Why he never sleeps"})
    assert "already this short's cover headline" in out, out
    motion_tools.set_motion_graphic(ctx, "cover", end=2.6)
    ctx._edl["motion"] = [m for m in ctx._edl["motion"] if m["id"] != "again"]
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 0.0, 2.0, id="hook",
                                          params={"text": "They promised us / *flying cars*",
                                                  "tier": "hook"})
    assert "cover headline, the opening title already" in out, out


def test_the_cover_without_an_end_holds_its_default_three_seconds(probe):
    ctx = _Ctx("and then " * 40, t0=0.2)
    out = motion_tools.add_motion_graphic(ctx, "cover_headline", 0.0, id="cover",
                                          params={"text": ZUCK})
    assert out.startswith("EDL v"), out
    assert ctx.item("cover")["end"] == pytest.approx(3.0)
    assert "NOTE (cover)" not in out, out


def test_one_apply_edit_batch_upsert_makes_the_cover():
    before = validate_edl(_edl([]), 30.0).model_dump()
    op = {"action": "upsert", "layer": "motion", "id": "cover",
          "value": {"id": "cover", "template": "cover_headline", "start": 0, "end": 2.6,
                    "params": {"text": ZUCK}}}
    after = edit_batch.apply_batch(before, [op], 30.0, set())
    edl = validate_edl(after, 30.0).model_dump()
    (c,) = [m for m in edl["motion"] if m["id"] == "cover"]
    assert c["template"] == "cover_headline" and c["start"] == 0
    assert caplib.effective_caption_mutes(edl) == [[0.0, 2.6]]


# ── the page ─────────────────────────────────────────────────────────────

PAGE = """(() => { const b = document.querySelector('#blk');
  const lines = Array.from(document.querySelectorAll('#blk .line'));
  return [Number(getComputedStyle(b).opacity), lines.length,
          lines.map(l => parseFloat(l.style.fontSize)),
          lines.map(l => l.getBoundingClientRect().width / MG.W),
          lines.map(l => Array.from(l.querySelectorAll('.mg-w')).map(w => w.textContent).join(' '))]; })()"""


@needs_browser
def test_the_page_draws_the_cover_complete_on_frame_0_and_fades_into_the_talk():
    (states,) = asyncio.run(_run([
        ("cover_headline", {"text": ZUCK}, 2.6, None,
         [(0.0, PAGE), (1.2, PAGE), (2.5, PAGE), (2.59, PAGE)]),
    ]))
    first, mid, late, last = states
    assert first[0] == 1 and mid[0] == 1, (first, mid)        # complete on frame 0
    assert 2 <= first[1] <= cover.MAX_LINES
    assert len(set(round(f, 1) for f in first[2])) == 1          # one size
    assert max(first[3]) == pytest.approx(0.72, abs=0.03)        # the widest fills the width
    assert last[0] < late[0] < 1 or last[0] < 0.5, (late, last)  # it fades out
    # the browserless estimate breaks the title the way the page does
    assert [" ".join(ln) for ln in cover.layout({"text": ZUCK})["lines"]] == \
        first[4]
