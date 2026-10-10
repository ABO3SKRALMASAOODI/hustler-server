"""The motion captions' legibility guard and type hygiene (round 4 judging,
round 7: legibility without boxes).

What is pinned here (worker/motion/templates/caption_motion.html):
  1. Legibility decisions instead of boxes, from the plate under the GLYPHS:
     a run of cues slides inside its clear zone to a darker spot; then dark
     ink where the plate is bright under every word (the accent kept in its
     hue, deepened — never paled toward white); then a glyph scrim that
     follows the letterforms; a box only past what that carries. Nothing
     lifts the plate; a dark plate changes nothing.
  2. A lone article stays inline with its serif emphasis word ('a weird'),
     never stacked above it as a superscript.
  3. A 9:16 caption keeps out of the feeds' ~9% side crop.
  4. The reading line holds still: every cue sharing a place keeps its
     first line's baseline on one row, whatever its line count, in every
     segment of the track.
"""

import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import plate  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(args=motion_engine.CHROME_ARGS).close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _chromium_ok(), reason="headless Chromium required")
ROWS = plate.grid_rows(1080, 1920)


def _plate(luma_of_row, luma_of_cell=None):
    g = [int(luma_of_cell(r, c) if luma_of_cell else luma_of_row(r))
         for r in range(ROWS) for c in range(plate.COLS)]
    return {"c": plate.COLS, "r": ROWS, "s": [{"t": 0.5, "g": g}, {"t": 1.5, "g": g}]}


def _item(words, y=0.74, b="b", z=None, accent="#FF3B30", look="editorial"):
    ws = [{"t": t, "s": round(0.1 + 0.3 * k, 3), "e": round(0.35 + 0.3 * k, 3), "x": x}
          for k, (t, x) in enumerate(words)]
    cue = {"s": 0.1, "e": 2.0, "y": y, "b": b, "k": 0, "w": ws}
    if z:
        cue["z"] = list(z)
    return {"id": "__captions_0", "template": "caption_motion", "start": 0.0, "end": 2.2,
            "params": {"look": look, "accent": accent, "cues": [cue]}}


STATE = """() => {
  const cue = document.querySelector('.cue.on'); if (!cue) return null;
  const blk = cue.querySelector('.blk'), r = blk.getBoundingClientRect();
  const acc = Array.from(blk.querySelectorAll('.mg-w')).filter(e => e.style.color)
    .map(e => getComputedStyle(e).color);
  return {top: r.top / innerHeight, bottom: r.bottom / innerHeight,
          left: r.left / innerWidth, right: r.right / innerWidth,
          dark: blk.classList.contains('dk'), ink: getComputedStyle(blk).color,
          glyph: blk.classList.contains('gscrim'),
          pocket: !!blk.querySelector('.pocket'),
          boxes: document.querySelectorAll('.scrim, .mg-backing').length,
          base: !!blk.querySelector('.row.base'), inline: !!blk.querySelector('.srf.inl'),
          accents: acc};
}"""


async def _state(item, pl, t=1.6):
    from playwright.async_api import async_playwright
    job = motion_templates.build_job(item, 540, 960, 30, plate=pl)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        dw, dh = motion_engine.design_size(job.out_w, job.out_h)
        ctx = await browser.new_context(viewport={"width": dw, "height": dh})
        await ctx.route("**/*", await motion_engine._route_factory(job))
        page = await ctx.new_page()
        await page.goto(motion_engine.ORIGIN + "/")
        await page.evaluate("async () => { await document.fonts.ready; await MG.ready; }")
        await page.evaluate("t => window.__mgSeek(t)", t)
        out = await page.evaluate(STATE)
        errs = await page.evaluate("window.__mgErrors")
        await browser.close()
    assert not errs, errs
    return out


RED = "rgb(255, 59, 48)"
LINE = [("That's", 0), ("incredible", 1)]


def _rgb(css):
    return [int(float(v)) for v in css[css.index("(") + 1:css.index(")")].split(",")[:3]]


@needs_browser
def test_the_guard_decides_ink_before_any_box_and_never_touches_a_dark_plate(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    item = _item(LINE)
    none = asyncio.run(_state(item, None))
    dark = asyncio.run(_state(item, _plate(lambda r: 20)))
    mid = asyncio.run(_state(item, _plate(lambda r: 150)))
    white = asyncio.run(_state(item, _plate(lambda r: 238)))
    # a dark plate: exactly what no plate draws (red reads on near-black)
    assert dark == none and not dark["dark"] and not dark["glyph"] and RED in dark["accents"]
    # a plate the look's own soft shadow still carries: unchanged
    assert mid == none
    # a white shirt: dark ink, the red accent kept in its hue — deepened,
    # never paled toward white — and no box anywhere
    assert white["dark"] and not white["glyph"] and not white["pocket"] and white["boxes"] == 0
    assert max(_rgb(white["ink"])) < 60
    (r, g, b), = [_rgb(c) for c in white["accents"]]
    assert r > 2.5 * max(g, b, 1) and r + g + b <= 255 + 59 + 48      # red, never paler
    # with no accent set the serif hero carries the base ink inline: it goes
    # dark with the rest (never a near-white word left bare on the shirt)
    plain = asyncio.run(_state(_item(LINE, accent=None), _plate(lambda r: 238)))
    assert plain["dark"] and plain["accents"], plain
    assert all(max(_rgb(c)) < 60 for c in plain["accents"]), plain
    # bright under one word, dark under the other (a shirt and a microphone):
    # no dark ink (it would vanish over the dark part), a glyph scrim that
    # follows the letterforms, still no box
    half = _plate(None, lambda r, c: 240 if c < plate.COLS * 0.5 else 15)
    mixed = asyncio.run(_state(_item([("Thatissomething", 0), ("incredible", 1)], accent=None), half))
    assert not mixed["dark"] and mixed["glyph"] and not mixed["pocket"] and mixed["boxes"] == 0


@needs_browser
def test_the_guard_first_slides_the_line_to_a_darker_spot_in_its_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    # bright from y 0.5 down, dark above; the cue's clear zone spans both
    split = _plate(lambda r: 235 if r / ROWS >= 0.5 else 25)
    item = _item(LINE, y=0.52, b="m", z=(0.36, 0.66))
    moved = asyncio.run(_state(item, split))
    assert moved["bottom"] <= 0.5 + 0.02 and moved["top"] >= 0.36 - 0.01, moved
    assert not moved["dark"] and not moved["glyph"] and not moved["pocket"]
    # without a zone (its usual place, not proven clear) it slides at most a
    # short step and takes dark ink on the bright plate instead
    still = asyncio.run(_state(_item(LINE, y=0.7), split))
    assert still["top"] > 0.6 and still["dark"] and still["boxes"] == 0


@needs_browser
def test_a_lone_article_stays_inline_with_its_serif_word(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    lone = asyncio.run(_state(_item([("a", 0), ("weird", 1)]), None))
    assert lone["inline"] and not lone["base"]
    # a content word still makes the stacked lockup
    stacked = asyncio.run(_state(_item([("really", 0), ("weird", 1)]), None))
    assert stacked["base"] and not stacked["inline"]


@needs_browser
def test_a_reel_caption_keeps_out_of_the_side_crop(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    wide = [("unbelievably", 0), ("incomprehensible", 0), ("extraordinarily", 0),
            ("disproportionate", 0)]
    st = asyncio.run(_state(_item(wide, accent=None), None))
    assert st["left"] >= 0.09 - 0.003 and st["right"] <= 0.91 + 0.003, st


BASELINE = """() => {
  const cue = document.querySelector('.cue.on'); if (!cue) return null;
  const cx = document.createElement('canvas').getContext('2d'), rg = document.createRange();
  let best = null;
  cue.querySelectorAll('.mg-w').forEach(w => {
    const n = w.firstChild; if (!n || !n.data) return;
    rg.setStart(n, 0); rg.setEnd(n, 1);
    const q = rg.getClientRects()[0]; if (!q) return;
    const cs = getComputedStyle(w);
    cx.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
    const b = q.top + cx.measureText('H').fontBoundingBoxAscent;
    if (best === null || b < best) best = b;
  });
  const r = cue.querySelector('.blk').getBoundingClientRect();
  return {base: best, h: r.height, top: r.top / innerHeight, bottom: r.bottom / innerHeight};
}"""


def _cues_item(cues, y=0.755, b="b", size=None):
    out = []
    for k, (t0, words) in enumerate(cues):
        ws = [{"t": t, "s": round(t0 + 0.1 * j, 3), "e": round(t0 + 0.1 * j + 0.08, 3), "x": x}
              for j, (t, x) in enumerate(words)]
        out.append({"s": t0, "e": round(t0 + 0.9, 3), "y": y, "b": b, "k": 0, "w": ws})
    params = {"look": "editorial", "accent": "#FF3B30", "cues": out}
    if size is not None:
        params["size"] = size
    return {"id": "__captions_0", "template": "caption_motion", "start": 0.0,
            "end": round(cues[-1][0] + 1.0, 3), "params": params}


async def _baselines(item, times):
    from playwright.async_api import async_playwright
    job = motion_templates.build_job(item, 540, 960, 30)
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        dw, dh = motion_engine.design_size(job.out_w, job.out_h)
        ctx = await browser.new_context(viewport={"width": dw, "height": dh})
        await ctx.route("**/*", await motion_engine._route_factory(job))
        page = await ctx.new_page()
        await page.goto(motion_engine.ORIGIN + "/")
        await page.evaluate("async () => { await document.fonts.ready; await MG.ready; }")
        for t in times:
            await page.evaluate("t => window.__mgSeek(t)", t)
            out.append(await page.evaluate(BASELINE))
        errs = await page.evaluate("window.__mgErrors")
        await browser.close()
    assert not errs, errs
    return out


@needs_browser
def test_the_reading_line_holds_still_across_line_counts_and_segments(tmp_path, monkeypatch):
    """Judged (round 5): blocks were centred on their anchor, so a cue with
    a stacked serif hero sat ~50 px above the one-line cues around it and
    the captions hopped. Every cue sharing a place now keeps its first
    baseline on one row — in every segment (each its own page)."""
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    one = [("We", 0), ("went", 0), ("home", 0)]
    lockup = [("Totally", 0), ("utterly", 0), ("incredible", 1)]   # a sans row over its serif hero
    mixed = _cues_item([(0.1, one), (1.1, lockup), (2.1, one)])
    plain = _cues_item([(0.1, one), (1.1, [("then", 0), ("we", 0), ("left", 0)])])
    a = asyncio.run(_baselines(mixed, [0.7, 1.7, 2.7]))
    b = asyncio.run(_baselines(plain, [0.7, 1.7]))
    assert a[1]["h"] > 1.6 * a[0]["h"]                       # the lockup really is taller
    rows = [s["base"] for s in a + b]
    assert max(rows) - min(rows) <= 1.5, rows


@needs_browser
def test_a_steady_row_stays_inside_the_band_the_plan_cleared(tmp_path, monkeypatch):
    """Review (round 7): the steady row was raised until the look's tallest
    block fit the template's zone, so at size l every bottom-band caption
    rose ~70 px past the band the caption plan had proven clear of the
    face (caption_carry.CAP_HALF_H around its anchor) — toward the chin.
    A cue the plan did not solve a zone for keeps inside that band on the
    face's side: below the anchor's band top for a bottom band, above its
    band bottom for a top band; one-line cues still share one row."""
    import caption_carry
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    half = caption_carry.CAP_HALF_H
    one = [("We", 0), ("went", 0), ("home", 0)]
    lockup = [("Totally", 0), ("utterly", 0), ("incredible", 1)]
    hero_first = [("incredible", 1), ("it", 0), ("was", 0)]
    cues = [(0.1, one), (1.1, lockup), (2.1, hero_first), (3.1, [("then", 0), ("we", 0), ("left", 0)])]
    times = [0.7, 1.7, 2.7, 3.7]
    for y, size in ((0.77, 1.2), (0.74, 1.45)):
        got = asyncio.run(_baselines(_cues_item(cues, y=y, size=size), times))
        # (a lockup taller than the band down to the 9:16 safe bottom sits
        # on that bottom, as it always has)
        # (give or take a line box's leading above the ink: 0.006)
        assert all(g["top"] >= y - half - 0.007 or abs(g["bottom"] - 0.80) < 0.003
                   for g in got), (y, size, got)
        assert got[0]["top"] >= y - half - 0.007 and got[2]["top"] >= y - half - 0.007, got
        assert abs(got[0]["base"] - got[3]["base"]) <= 1.5, got
    top = asyncio.run(_baselines(_cues_item(cues, y=0.2, b="t"), times))
    assert all(g["top"] >= 0.2 - half - 0.007 and g["bottom"] <= 0.2 + half + 0.002 for g in top), top
    rows = [top[k]["base"] for k in (0, 1, 3)]
    assert max(rows) - min(rows) <= 1.5, rows
