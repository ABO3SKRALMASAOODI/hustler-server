"""The motion captions' legibility guard and type hygiene (round 4 judging).

What is pinned here (worker/motion/templates/caption_motion.html):
  1. Per word, in this order: slide inside the cue's clear zone to a darker
     spot; a tight dark halo for a plate a little too bright, and accent /
     emphasis words lifted toward white until they read 3:1 on what lies
     under them; a soft dark local scrim; only then the pocket (a box).
     Everything darkens, nothing lifts the plate; a dark plate changes
     nothing.
  2. A lone article stays inline with its serif emphasis word ('a weird'),
     never stacked above it as a superscript.
  3. A 9:16 caption keeps out of the feeds' ~9% side crop.
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


def _plate(luma_of_row):
    g = [int(luma_of_row(r)) for r in range(ROWS) for _c in range(plate.COLS)]
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
  const sc = blk.querySelector('.scrim');
  const acc = Array.from(blk.querySelectorAll('.mg-w')).filter(e => e.style.color)
    .map(e => getComputedStyle(e).color);
  return {top: r.top / innerHeight, bottom: r.bottom / innerHeight,
          left: r.left / innerWidth, right: r.right / innerWidth,
          halo: blk.classList.contains('halo'),
          soft: !!(sc && sc.classList.contains('soft')),
          pocket: !!(sc && sc.classList.contains('pocket')),
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


@needs_browser
def test_the_guard_darkens_in_order_and_never_touches_a_dark_plate(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    item = _item(LINE)
    none = asyncio.run(_state(item, None))
    dark = asyncio.run(_state(item, _plate(lambda r: 20)))
    mid = asyncio.run(_state(item, _plate(lambda r: 150)))
    white = asyncio.run(_state(item, _plate(lambda r: 238)))
    # a dark plate: exactly what no plate draws (red reads on near-black)
    assert dark == none and not dark["halo"] and not dark["soft"] and RED in dark["accents"]
    # a little too bright: a tight halo, no scrim
    assert mid["halo"] and not mid["soft"] and not mid["pocket"]
    # a white shirt: a soft dark scrim (never the box), and the red accent,
    # unreadable on the darkened plate, is lifted toward white
    assert white["soft"] and not white["pocket"] and not white["halo"]
    assert RED not in white["accents"] and white["accents"]


@needs_browser
def test_the_guard_first_slides_the_line_to_a_darker_spot_in_its_zone(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    # bright from y 0.5 down, dark above; the cue's clear zone spans both
    split = _plate(lambda r: 235 if r / ROWS >= 0.5 else 25)
    item = _item(LINE, y=0.52, b="m", z=(0.36, 0.66))
    moved = asyncio.run(_state(item, split))
    assert moved["bottom"] <= 0.5 + 0.02 and moved["top"] >= 0.36 - 0.01, moved
    assert not moved["soft"] and not moved["pocket"]
    # without a zone (its usual place, not proven clear) it slides at most a
    # short step and darkens instead
    still = asyncio.run(_state(_item(LINE, y=0.7), split))
    assert still["top"] > 0.6 and still["soft"]


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
