"""The headline's measured type (Diamandis run, Oct 2026: in 7 of 9 shorts
the standing claim rendered SMALLER than the captions under it — a thin
band, the kicker and its gap taking a third of it, headline.html shrinking
the claim to fit — and nothing said so before a reviewer did).

band_type measures what headline.html draws from the same font files the
motion engine serves Chromium (no browser: the agent, MCP and shorts lanes
have none), and the captions' cap height; the headline write
(motion_tools._persistent_contract) reports the claim's cap height, sets
the kicker beside the corner mark / a larger size when that is free, and
refuses a claim smaller than the captions with the card top, claim length
or kicker that fixes it, and the largest card that band leaves.

Run:  python -m pytest tests/test_headline_type.py -q     (from worker/)
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import band_type  # noqa: E402
import keepout  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

W, H = 1080, 1920


def _browser_ok():
    try:
        return motion_engine.available()
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(not _browser_ok(), reason="no headless Chromium")


def _p(**kw):
    return motion_templates.check_params("headline", kw)


# The run's headlines as written (projects 3442-3450, the Jobs v5/v8 bands)
# and the claim's font size Chromium laid out for each (MG layout, the
# bundled fonts): the estimate is the template's own layout pass.
ELON = "ELON MUSK · MARCH 2024"
RUN = [
    (dict(text="Forcing an AI to *lie* is how 2001 went wrong", kicker=ELON, size=1.3,
          width=0.88, y=0.163, height=0.075), 48.24),
    (dict(text="Elon Musk: Some chance superintelligence *ends humanity*", style="condensed",
          y=0.1806, height=0.1048), 67.87),
    (dict(text="The year AI could *surpass all of us*", kicker=ELON, size=1.4, width=0.86,
          y=0.214, height=0.16), 97.96),
    (dict(text="Elon Musk: Brain interfaces may be a form of *immortality*",
          kicker="March 2024", accent_style="serif", y=0.1781, height=0.0998), 48.55),
    (dict(text="Kurzweil may have been *too conservative* on AI", kicker=ELON, size=1.4,
          width=0.88, y=0.1753, height=0.0954), 43.93),
    (dict(text="*Elon Musk:* If it's not your goal, you won't achieve it", y=0.169,
          height=0.08), 72.13),
    (dict(text="The case for *beautiful* type on computers", y=0.1886, height=0.2072), 84.0),
    (dict(text="The case for *beautiful* type on computers", kicker="Steve Jobs, 1983",
          y=0.1931, height=0.1298), 79.04),
    # the kicker set beside the mark (kicker_y): the band is the claim's
    (dict(text="Elon Musk: Brain interfaces may be a form of *immortality*",
          kicker="March 2024", accent_style="serif", y=0.1781, height=0.0998,
          kicker_y=0.1066), 72.13),
    (dict(text="Kurzweil may have been *too conservative* on AI", kicker="ELON MUSK", size=1.4,
          width=0.88, y=0.1756, height=0.0948, kicker_y=0.1066), 86.70),
    (dict(text="After chips, AI ran into a *voltage problem*", kicker=ELON, style="condensed",
          y=0.1836, height=0.1108), 65.80),
    (dict(text="*Elon Musk:* If you cannot beat AI, join it", size=1.12, y=0.1776,
          height=0.0988), 91.30),
]


# ── the measurement ───────────────────────────────────────────────────────

def test_cap_heights_come_from_the_bundled_font_files():
    assert band_type.font_path("Inter Display", 800).endswith("InterDisplay-ExtraBold.ttf")
    assert band_type.font_path("Anton").endswith("Anton-Regular.ttf")
    # Inter Display's H is 1490 of 2048 units; Anton's caps are taller
    assert band_type.cap_ratio("Inter Display", 800) == pytest.approx(1490 / 2048, abs=2e-3)
    assert band_type.cap_ratio("Anton") > 0.8


@pytest.mark.parametrize("params,chromium_px", RUN)
def test_the_layout_pass_matches_the_page(params, chromium_px):
    lay = band_type.headline_layout(_p(**params), W, H)
    # within a few percent, and never larger than the page draws it (kerning
    # the estimate leaves out only widens its lines)
    assert chromium_px * 0.96 <= lay["claim_px"] <= chromium_px * 1.005, lay
    assert lay["claim_cap"] == pytest.approx(
        lay["claim_px"] * band_type.cap_ratio(
            "Anton" if params.get("style") == "condensed" else "Inter Display",
            400 if params.get("style") == "condensed" else 800) / H, rel=1e-6)


def test_where_the_type_draws_is_measured_too():
    # Chromium's ink (word and kicker boxes) for the run's s04 as written,
    # and for the Jobs v8 band: the balanced lines, the block on its y
    s04 = band_type.headline_layout(_p(**RUN[3][0]), W, H)["ink"]
    assert s04 == pytest.approx([0.2176, 0.1284, 0.7824, 0.2278], abs=0.004)
    jobs = band_type.headline_layout(_p(**RUN[7][0]), W, H)["ink"]
    assert jobs == pytest.approx([0.1425, 0.1284, 0.8575, 0.2578], abs=0.004)


def test_the_kicker_and_its_gap_took_the_band_and_aside_it_is_the_claims():
    stacked = band_type.headline_layout(_p(**RUN[3][0]), W, H)
    assert stacked["kicker_px"] > stacked["claim_px"]           # the run's s04: kicker > claim
    assert stacked["limit"] == "band"
    aside = band_type.headline_layout(_p(**RUN[8][0]), W, H)
    assert aside["kicker_beside"] and aside["claim_px"] > 1.4 * stacked["claim_px"]
    # left-aligned blocks never set the kicker aside (the template does not)
    left = band_type.headline_layout(_p(**dict(RUN[8][0], align="left")), W, H)
    assert not left["kicker_beside"]


def test_what_caps_the_claim_is_named():
    assert band_type.headline_layout(_p(**RUN[6][0]), W, H)["limit"] == "size"
    assert band_type.headline_layout(_p(**RUN[4][0]), W, H)["limit"] == "band"
    # a two-line claim whose words fill the column: a taller band adds nothing
    wide = _p(text="*Elon Musk:* If you cannot beat AI, join it", size=1.3, y=0.2, height=0.3)
    lay = band_type.headline_layout(wide, W, H)
    assert lay["limit"] == "width"
    assert band_type.needed_band(wide, W, H, lay["claim_cap"] * 1.05) is None


def test_caption_cap_heights_follow_the_track():
    def cap(style):
        return band_type.caption_cap({"captions": {"mode": "from_transcript", "style": style}})
    assert cap({}) is not None and band_type.caption_cap({"captions": None}) is None
    # motion looks: the look's base type x size (caption_motion.html CFG) —
    # the run's measured caption rows
    assert cap({"motion_look": "clean"})[0] == pytest.approx(0.0265, abs=3e-4)
    assert cap({"motion_look": "clean", "size": "l",
                "font": "Inter Display"})[0] == pytest.approx(0.0318, abs=3e-4)
    assert cap({"motion_look": "editorial", "size": "l"})[0] == pytest.approx(0.0327, abs=3e-4)
    assert cap({"motion_look": "serif"})[0] == pytest.approx(0.0250, abs=3e-4)
    # libass presets: sized by the font's Windows ascent + descent (libass
    # set_font_metrics) — the bundled faces measured on libass renders
    assert cap({"preset": "reels"})[0] == pytest.approx(0.0417, abs=5e-4)
    assert cap({"preset": "lyric"})[0] == pytest.approx(0.0245, abs=5e-4)
    assert cap({"preset": "beast"})[0] == pytest.approx(0.0375, abs=5e-4)
    assert cap({"preset": "fashion"})[0] == pytest.approx(0.0286, abs=5e-4)


def test_the_strip_beside_the_mark_and_the_kickers_that_fit_it():
    mark = keepout.watermark_zone(W, H)
    strip = band_type.mark_strip(W, H, top=motion_tools.feed_top(W, H))
    assert strip[0] > mark[2] and strip[1] == motion_tools.HEADLINE_SAFE_TOP
    assert strip[3] == pytest.approx(mark[3]) and strip[2] <= keepout.SAFE_X1
    ok, ky = band_type.kicker_beside_fits(_p(text="x y z", kicker="ELON MUSK"), W, H, strip)
    assert ok and strip[1] < ky < strip[3]
    assert not band_type.kicker_beside_fits(_p(text="x", kicker=ELON), W, H, strip)[0]
    assert not band_type.kicker_beside_fits(_p(text="x", kicker="ELON MUSK", align="left"),
                                            W, H, strip)[0]
    p = _p(text="x y z", kicker="ELON MUSK", kicker_y=ky)
    box = band_type.kicker_box(p, W, H)
    assert box[0] > mark[2] and box[1] >= strip[1] and box[3] <= strip[3]
    assert box[2] == pytest.approx(0.92)                    # right-aligned to the column
    assert 9 < band_type.kicker_chars_beside(p, W, H, strip) < 22


def test_the_browserless_estimate_holds_a_kicker_set_aside():
    spec = motion_templates.spec("headline")
    stacked = keepout.nominal_ink("headline", spec, _p(text="x", kicker="ELON MUSK", y=0.18,
                                                       height=0.1))
    aside = keepout.nominal_ink("headline", spec, _p(text="x", kicker="ELON MUSK", y=0.18,
                                                     height=0.1, kicker_y=0.1066))
    assert stacked[1] == pytest.approx(0.13)
    assert aside[1] <= 0.1066 - 0.016 and aside[3] == stacked[3]
    # the template stacks a left-aligned block's kicker whatever kicker_y says
    left = keepout.nominal_ink("headline", spec, _p(text="x", kicker="ELON MUSK", y=0.18,
                                                    height=0.1, kicker_y=0.1066, align="left"))
    assert left[1] == pytest.approx(0.13)


# A kicker too long for one line wraps at the floor size, max-width the
# column: its box is the whole column, and set aside its lines start at the
# column's LEFT edge — onto the corner mark.
LONG_KICKER = "PAUL GRAHAM ON WRITING, THINKING CLEARLY"


def test_a_wrapped_kicker_is_the_whole_column():
    # (0.6: each line well clear of the column, so the page wraps it the same)
    p = _p(text="Founders should *write*", kicker=LONG_KICKER, width=0.6, y=0.2, height=0.2)
    lay = band_type.headline_layout(p, W, H)
    assert lay["kicker_lines"] == 3 and lay["kicker_w"] == pytest.approx(0.6)
    box = band_type.kicker_box(dict(p, kicker_y=0.1066), W, H)
    assert box[0] == pytest.approx(0.2) and box[2] == pytest.approx(0.8)
    assert not band_type.kicker_beside_fits(p, W, H, band_type.mark_strip(W, H, top=0.085))[0]


def test_the_largest_card_for_a_band_and_a_floor():
    card = band_type.largest_card(0.25, W, H, floor=0.54)
    assert card["box"] == [0.0, 0.25, 1.0, band_type.CARD_BOTTOM]
    assert card["meets_floor"] and card["area"] == pytest.approx(0.544)
    assert card["floor_top"] == pytest.approx(band_type.CARD_BOTTOM - 0.54)
    assert not band_type.largest_card(0.30, W, H, floor=0.54)["meets_floor"]
    # a small source window at 2x or less: a narrower, shorter card
    fed = band_type.largest_card(0.25, W, H, floor=0.54, src_px=(300, 400))
    assert fed["box"][2] - fed["box"][0] == pytest.approx(600 / 1080, abs=2e-3)
    assert not fed["meets_floor"] and fed["floor_top"] is None


def test_needed_band_and_size_reach_the_target():
    p = _p(**RUN[4][0])
    target = 0.0265
    need = band_type.needed_band(p, W, H, target)
    assert band_type.headline_layout(dict(p, height=need), W, H)["claim_cap"] >= target
    assert band_type.headline_layout(dict(p, height=need - 0.006), W, H)["claim_cap"] < target
    size = band_type.needed_size(_p(**RUN[6][0]), W, H, 0.034)
    assert size == pytest.approx(0.034 * H / (84 * band_type.cap_ratio("Inter Display", 800)),
                                 abs=0.011)
    assert band_type.needed_size(_p(text="x"), W, H, 0.06) is None     # past size 1.4


# ── the write ─────────────────────────────────────────────────────────────

class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, edl, duration=60.0):
        self.duration = duration
        self.index = {"video": {"width": 1920, "height": 1080, "fps": 30.0},
                      "words": [], "shots": [{"id": 1, "start": 0.0, "end": 60.0}]}
        self._edl = validate_edl(edl, duration).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


def _probe(item, W_, H_, fps=30.0):
    p = item.get("params") or {}
    y, h = float(p.get("y", 0.5)), float(p.get("height", 0.12))
    box = [0.2, round(y - h / 2, 4), 0.8, round(y + h / 2, 4)]
    return {"errors": [], "visible_frames": 4, "samples": 4, "bboxes": [box], "ink": [box] * 4}


def _edl(card_top=0.24, look="clean", size="m", source=None):
    edl = default_edl(60.0)
    edl["keep"] = [[0.0, 30.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    card = {"id": "card", "start": 0.0, "end": 30.0, "box": [0.06, card_top, 0.94, 0.794],
            "entrance": "none", "exit": "none"}
    if source:
        card["source"] = source
    edl["effects"] = {"picture_cards": [card]}
    if look:
        edl["captions"] = {"mode": "from_transcript", "style": {"motion_look": look, "size": size}}
    return edl


def _stored(ctx, mid="hl"):
    return next(m for m in ctx.latest_edl()["json"]["motion"] if m["id"] == mid)["params"]


@pytest.fixture(autouse=True)
def _no_browser_probe(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", _probe)


def test_a_claim_smaller_than_the_captions_is_refused_with_the_fix():
    # the run's s06 (3447): a long kicker over a 0.095 band, clean captions
    ctx = _Ctx(_edl(card_top=0.235, source=[0.40, 0.25, 0.62, 0.70]))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Kurzweil may have been *too conservative* on AI", "kicker": ELON,
        "size": 1.4}, id="hl")
    assert out.startswith("REJECTED"), out
    assert "SMALLER than the 'clean' captions (0.027" in out
    assert "after the kicker and its gap" in out
    assert "FIX, any one of: (1) a card top at y" in out
    assert "the largest card under it is [0.0," in out and "picture floor" in out
    # this card's source window cannot fill a full-width card at 2x
    assert "this card's source window fills [" in out
    assert "a kicker of at most about" in out and "no kicker" in out
    assert not ctx.writes


def test_the_fix_it_names_is_accepted():
    ctx = _Ctx(_edl(card_top=0.235))
    params = {"text": "Kurzweil may have been *too conservative* on AI", "kicker": ELON,
              "size": 1.4}
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params=dict(params), id="hl")
    top = float(out.split("(1) a card top at y ")[1].split(" ")[0])
    ok = _Ctx(_edl(card_top=top))
    out = motion_tools.add_motion_graphic(ok, "headline", 0.0, params=dict(params), id="hl")
    assert out.startswith("EDL v1"), out
    assert "HEADLINE TYPE (measured from the font files)" in out


def test_a_kicker_that_fits_beside_the_mark_is_set_there():
    ctx = _Ctx(_edl(card_top=0.235))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Kurzweil may have been *too conservative* on AI", "kicker": "ELON MUSK",
        "size": 1.4}, id="hl")
    assert out.startswith("EDL v1"), out
    p = _stored(ctx)
    assert p["kicker_y"] > 0 and "Set for it: kicker_y" in out
    lay = band_type.headline_layout(p, W, H)
    target = band_type.caption_cap(ctx.latest_edl()["json"])[0]
    stacked = band_type.headline_layout(dict(p, kicker_y=0), W, H)
    assert lay["claim_cap"] >= target > stacked["claim_cap"]
    box = band_type.kicker_box(p, W, H, lay)
    mark = keepout.watermark_zone(W, H)
    assert box[0] > mark[2] and box[1] >= motion_tools.feed_top(W, H)
    assert box[3] <= p["y"] - lay["block_h"] / 2           # above the claim


def test_a_passed_kicker_y_is_the_editors():
    ctx = _Ctx(_edl(card_top=0.235))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Kurzweil may have been *too conservative* on AI", "kicker": "ELON MUSK",
        "size": 1.4, "kicker_y": 0}, id="hl")
    # kept in the stack: the claim is then too small, and refused
    assert out.startswith("REJECTED") and "SMALLER than" in out, out
    # a kicker set aside onto the free-tier mark is refused
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Kurzweil was *too conservative*", "kicker": ELON, "kicker_y": 0.1066},
        id="hl")
    assert out.startswith("REJECTED") and "free-tier mark's zone" in out, out
    assert "characters fit there" in out


def test_a_size_capped_claim_is_raised_to_own_its_band():
    ctx = _Ctx(_edl(card_top=0.31, look="editorial"))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "The case for *beautiful* type on computers"}, id="hl")
    assert out.startswith("EDL v1") and "Set for it: size" in out, out
    p = _stored(ctx)
    target = band_type.caption_cap(ctx.latest_edl()["json"])[0]
    lay = band_type.headline_layout(p, W, H)
    assert lay["claim_cap"] >= motion_tools.HEADLINE_OWNS_RATIO * target - 1e-6
    # the smallest size that does it
    smaller = band_type.headline_layout(dict(p, size=p["size"] - 0.01), W, H)
    assert smaller["claim_cap"] < motion_tools.HEADLINE_OWNS_RATIO * target
    # a size the editor passed is kept
    ctx = _Ctx(_edl(card_top=0.31, look="editorial"))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "The case for *beautiful* type on computers", "size": 1.0}, id="hl")
    assert out.startswith("EDL v1") and _stored(ctx)["size"] == 1.0
    assert "Under 1.2x it reads as the captions' size" in out


def test_a_card_under_the_floor_is_told_the_largest_card_its_band_leaves():
    edl = _edl(card_top=0.30)
    edl["effects"]["picture_cards"][0]["box"] = [0.2, 0.30, 0.8, 0.794]
    ctx = _Ctx(edl)
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "The case for *beautiful* type"}, id="hl")
    assert out.startswith("EDL v1"), out
    assert "NOTE (card): the picture card covers 0.30 of the frame" in out
    assert "the largest card is [0.0, 0.3, 1.0, 0.794], area 0.49 (also under it)" in out
    # a card that meets the floor hears nothing
    edl = _edl(card_top=0.24)
    edl["effects"]["picture_cards"][0]["box"] = [0.0, 0.24, 1.0, 0.794]
    ctx = _Ctx(edl)
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "The case for *beautiful* type"}, id="hl")
    assert out.startswith("EDL v1") and "NOTE (card)" not in out


def test_without_captions_the_measure_is_a_note():
    ctx = _Ctx(_edl(card_top=0.235, look=None))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Kurzweil may have been *too conservative* on AI", "kicker": ELON,
        "size": 1.4}, id="hl")
    assert out.startswith("EDL v1"), out
    assert "NOTE (headline type)" in out and "a clean caption track (none here)" in out


def test_an_edit_that_leaves_the_type_alone_is_only_noted():
    edl = _edl(card_top=0.235)
    # an old EDL's headline, written before the measure (the run's s06)
    edl["motion"] = [{"id": "hl", "template": "headline", "start": 0.0, "end": 30.0,
                      "params": _p(text="Kurzweil may have been *too conservative* on AI",
                                   kicker=ELON, size=1.4, y=0.1756, height=0.0948)}]
    ctx = _Ctx(edl)
    out = motion_tools.set_motion_graphic(ctx, "hl", end=29.0)
    assert out.startswith("EDL v1") and "NOTE (headline type)" in out, out
    text = "Kurzweil may have been far *too conservative* on AI"
    out = motion_tools.set_motion_graphic(ctx, "hl", params={"text": text})
    assert out.startswith("REJECTED") and "SMALLER than" in out, out
    # the same edit with a kicker that fits beside the mark is accepted
    out = motion_tools.set_motion_graphic(ctx, "hl", params={"text": text,
                                                             "kicker": "ELON MUSK"})
    assert out.startswith("EDL v2"), out
    assert _stored(ctx)["kicker_y"] > 0
    # a kicker edited too long for the strip goes back above the claim
    out = motion_tools.set_motion_graphic(ctx, "hl", params={"kicker": ELON,
                                                             "text": "Kurzweil was *wrong*"})
    assert out.startswith("EDL v3") and "kicker_y 0 (the kicker no longer fits" in out, out
    assert _stored(ctx)["kicker_y"] == 0


def test_a_thin_band_is_refused_with_the_measured_fix():
    ctx = _Ctx(_edl(card_top=0.195))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Forcing an AI to *lie* is how 2001 went wrong", "kicker": ELON,
        "size": 1.3, "width": 0.88}, id="hl")
    assert out.startswith("REJECTED") and "a headline needs 0.06" in out
    assert "FIX, any one of: (1) a card top at y" in out, out
    # the band itself is refused: a shorter claim or kicker in it is no fix
    assert "(2)" not in out and "characters" not in out, out


def test_every_fix_named_measures_at_the_captions():
    # the run's s01 (3442) over a 0.075 band: with the kicker beside the mark
    # the claim still sets two lines far under the 'l' captions, so a shorter
    # kicker is no fix; no kicker and a one-line claim is
    ctx = _Ctx(_edl(card_top=0.215, size="l"))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Forcing an AI to *lie* is how 2001 went wrong", "kicker": ELON,
        "size": 1.3, "width": 0.88}, id="hl")
    assert out.startswith("REJECTED") and "SMALLER than" in out, out
    assert "a kicker of at most" not in out
    assert "no kicker and a claim of about" in out
    # a card top never leaves a band the write refuses (HEADLINE_MIN_BAND)
    edl = ctx.latest_edl()["json"]
    item = {"start": 0.0, "end": 30.0, "params": _p(text="AI *wins*", y=0.16, height=0.06)}
    fit = motion_tools.headline_card(ctx, edl, item, (0.128, 0.188, "the picture card"), 0.01)
    assert fit["band"] >= motion_tools.HEADLINE_MIN_BAND
    assert fit["top"] == pytest.approx(0.128 + motion_tools.HEADLINE_MIN_BAND
                                       + motion_tools.HEADLINE_GAP, abs=1e-3)


def test_a_wrapped_kicker_set_aside_onto_the_mark_is_refused():
    ctx = _Ctx(_edl(card_top=0.30))
    out = motion_tools.add_motion_graphic(ctx, "headline", 0.0, params={
        "text": "Founders should *write*", "kicker": LONG_KICKER, "width": 0.6,
        "x": 0.62, "kicker_y": 0.1066}, id="hl")
    assert out.startswith("REJECTED") and "free-tier mark's zone" in out, out


# ── the editorial headline (set_editorial_graphic kind='headline') ───────

def test_the_editorial_headline_is_measured_against_the_captions():
    import agent_tools
    # libass sizes its Inter Display Bold rows by the Windows ascent +
    # descent: font_size 0.052 (the default) drew a 34 px (0.0177) cap on a
    # 9:16 render, 0.08 a 52 px one — two thirds of clean captions' 0.0265
    assert band_type.ass_text_cap(0.052, W, H) == pytest.approx(0.0177, abs=4e-4)
    assert band_type.ass_text_cap(0.08, W, H) == pytest.approx(0.0271, abs=6e-4)
    ctx = _Ctx(_edl(card_top=0.30))
    out = agent_tools.set_editorial_graphic(
        ctx, "hl", "headline", "Kurzweil was too conservative", 0.0, 8.0,
        speaker="Elon Musk", box=[0.08, 0.1, 0.92, 0.27])
    assert out.startswith("EDL v1"), out
    assert "HEADLINE TYPE (measured from the font files)" in out
    assert "SMALLER than the captions" in out and "template='headline'" in out
    need = float(out.split("SMALLER than the captions")[1].split("font_size ")[1].split(" ")[0])
    target = band_type.caption_cap(ctx.latest_edl()["json"])[0]
    assert band_type.ass_text_cap(need, W, H) >= target > band_type.ass_text_cap(need - 0.001, W, H)
    # 'l' captions: no font_size the tool allows reaches them
    ctx = _Ctx(_edl(card_top=0.30, size="l"))
    out = agent_tools.set_editorial_graphic(
        ctx, "hl", "headline", "Kurzweil was too conservative", 0.0, 8.0,
        speaker="Elon Musk", box=[0.08, 0.1, 0.92, 0.27], font_size=0.085)
    assert "no font_size up to 0.085 reaches them" in out, out
    # no captions: nothing to hold it to
    ctx = _Ctx(_edl(card_top=0.30, look=None))
    out = agent_tools.set_editorial_graphic(
        ctx, "hl", "headline", "Kurzweil was too conservative", 0.0, 8.0,
        speaker="Elon Musk", box=[0.08, 0.1, 0.92, 0.27])
    assert out.startswith("EDL v1") and "HEADLINE TYPE" not in out


# ── the page ──────────────────────────────────────────────────────────────

_JS = """async () => {
  await document.fonts.ready; if (window.MG && MG.ready) await MG.ready;
  const claim = document.querySelector('.claim'), kick = document.querySelector('.kick');
  const k = kick ? kick.getBoundingClientRect() : null;
  const rs = Array.from(claim.querySelectorAll('.mg-w')).map(w => w.getBoundingClientRect())
    .concat(k ? [k] : []);
  return {fs: parseFloat(getComputedStyle(claim).fontSize),
          ink: [Math.min(...rs.map(r => r.left)), Math.min(...rs.map(r => r.top)),
                Math.max(...rs.map(r => r.right)), Math.max(...rs.map(r => r.bottom))],
          claim: (r => [r.left, r.top, r.right, r.bottom])(claim.getBoundingClientRect()),
          kick: k ? [k.left, k.top, k.right, k.bottom] : null,
          kfs: kick ? parseFloat(getComputedStyle(kick).fontSize) : 0};
}"""


async def _lay_out(cases):
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for params in cases:
                dw, dh = motion_engine.design_size(W, H)
                html = motion_engine.build_document(
                    motion_engine.template_body("headline"), params=params, duration=5.0,
                    fps=30.0, design_w=dw, design_h=dh)
                job = motion_engine.RenderJob(html=html, out_w=W, out_h=H, fps=30.0,
                                              duration=5.0)
                ctx = await browser.new_context(viewport={"width": dw, "height": dh},
                                                device_scale_factor=1)
                await ctx.route("**/*", await motion_engine._route_factory(job))
                page = await ctx.new_page()
                await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
                out.append(await page.evaluate(_JS))
                await ctx.close()
        finally:
            await browser.close()
    return out


@needs_browser
def test_a_wrapped_kicker_set_aside_draws_the_column():
    p = _p(text="Founders should *write*", kicker=LONG_KICKER, width=0.6, x=0.62,
           y=0.2, height=0.2, kicker_y=0.1066)
    got = asyncio.run(_lay_out([p]))[0]
    k = [v / s for v, s in zip(got["kick"], (W, H, W, H))]
    assert k == pytest.approx(band_type.kicker_box(p, W, H), abs=0.01)


@needs_browser
def test_the_page_draws_what_was_measured():
    cases = [_p(**params) for params, _px in RUN]
    for params, got in zip(cases, asyncio.run(_lay_out(cases))):
        lay = band_type.headline_layout(params, W, H)
        assert got["fs"] * 0.96 <= lay["claim_px"] <= got["fs"] * 1.005, (params, got, lay)
        # where its type draws: the balanced lines and the kicker
        ink = [v / s for v, s in zip(got["ink"], (W, H, W, H))]
        assert lay["ink"] == pytest.approx(ink, abs=0.015), (params, ink, lay["ink"])
        if lay["kicker_px"]:
            assert lay["kicker_px"] == pytest.approx(got["kfs"], rel=0.01)
        if lay["kicker_beside"]:
            # beside the corner mark, right-aligned to the column, on its line
            mark = keepout.watermark_zone(W, H)
            k = [v / s for v, s in zip(got["kick"], (W, H, W, H))]
            assert k[0] > mark[2] and k[3] <= got["claim"][1] / H
            assert k[2] == pytest.approx(params["x"] + params["width"] / 2, abs=2e-3)
            assert (k[1] + k[3]) / 2 == pytest.approx(params["kicker_y"], abs=2e-3)
