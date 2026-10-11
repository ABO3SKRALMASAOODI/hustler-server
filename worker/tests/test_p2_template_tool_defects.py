"""P2 template and tool defects from the Diamandis run (Oct 2026, 0/9 shipped).

Templates:
- phrase_build row sizes 's'/'l'/'xl' were accepted and rendered at the
  default size: a row field the template reads as a number must be one;
- headline printed ' / ' literally; it now breaks the line there;
- chapter_title showed '01' by default; it shows no number unless given;
- timeline_steps and glow_title had no size; counter ignored its roll and
  its label ignored `color`; image_card had no x, and allow_face_overlap
  passed inside params was refused;
- list_motion_templates(category='type') left out headline, marker_text
  and counter;
- EARN ITS PLACE flagged Headline Pro's own lockups as re-typesets.

Tools:
- erase_region: a box repaint judged by stroke ink (grain read as surviving
  text) and filled by TELEA wedges / a stale plate (the dark pentagon);
- set_caption_fixes refused an empty replacement (deleting a stutter);
- keep_segments snap_to_words pulled in the neighbouring word;
- set_frame said a bare NO CHANGE for a focus nudge lost to rounding;
- find_silences took no range; review_audio heard 3 windows and centred
  them; audit_captions ignored graphic text and captions under graphics;
  get_editorial_map(focus='peaks') was unranked.

Browser checks read the laid-out DOM with relative comparisons (no pixel or
font-metric margins), so they hold on macOS and on the Linux CI fonts.

Run:  python -m pytest tests/test_p2_template_tool_defects.py -q   (from worker/)
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402
import audit  # noqa: E402
import captions as caplib  # noqa: E402
import cut_audio  # noqa: E402
import edit_batch  # noqa: E402
import edit_review  # noqa: E402
import editorial_index  # noqa: E402
import inpaint  # noqa: E402
import keepout  # noqa: E402
import llm  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import number_reveal  # noqa: E402
from schemas import (CaptionsFromTranscript, Frame, PatchItem,  # noqa: E402
                     default_edl, patch_fingerprint, validate_edl)


# ── 1. phrase_build rows ─────────────────────────────────────────────────

def _rows(*sizes, **extra):
    return [dict({"text": f"row {i}", "role": "sans", "size": s}, **extra)
            for i, s in enumerate(sizes)]


def test_phrase_build_refuses_a_row_size_it_cannot_read():
    for bad in ("s", "xl", "large", "l"):
        with pytest.raises(ValueError, match=r"size must be a number"):
            motion_templates.check_params("phrase_build", {"rows": _rows("1", bad)})
    with pytest.raises(ValueError, match=r"role must be one of"):
        motion_templates.check_params("phrase_build", {"rows": [
            {"text": "Your", "role": "handwriting", "size": "1"}]})
    with pytest.raises(ValueError, match=r"accent must be 1 or 0"):
        motion_templates.check_params("phrase_build", {"rows": [
            {"text": "Your", "size": "1", "accent": "red"}]})
    with pytest.raises(ValueError, match=r"has no field\(s\) \['weight'\]"):
        motion_templates.check_params("phrase_build", {"rows": [
            {"text": "Your", "weight": "bold"}]})
    ok = motion_templates.check_params("phrase_build", {"rows": [
        {"text": "a", "size": 0.62, "at": "0.25"}, {"text": "b", "size": "1.6x"},
        {"text": "c", "role": "Serif", "accent": "1", "at": "0.9s"}]})
    # a number with its unit is stored bare: the Python readers (the size
    # ladder merge) read it as the template's parseFloat does
    assert [r["size"] for r in ok["rows"]] == ["0.62", "1.6", ""]
    assert [r["at"] for r in ok["rows"]] == ["0.25", "", "0.9"]
    # a stored EDL keeps rendering as before (the lenient path never raises)
    old = motion_templates.normalize_params("phrase_build", {"rows": _rows("s", "xl")})
    assert [r["size"] for r in old["rows"]] == ["s", "xl"]


def test_an_edit_batch_checks_the_motion_it_adds_but_not_what_it_leaves():
    item = {"id": "lock", "template": "phrase_build", "start": 1.0, "end": 3.0,
            "params": {"rows": _rows("1", "xl")}}
    with pytest.raises(ValueError, match=r"motion 'lock'.*size must be a number"):
        edit_batch.apply_batch(default_edl(20), [
            {"action": "upsert", "layer": "motion", "id": "lock", "value": item}], 20, {})
    # an EDL stored before the check keeps its row; a batch that only moves
    # the item is not re-judged
    edl = default_edl(20)
    edl["motion"] = [item]
    edl = validate_edl(edl, 20).model_dump()
    moved = edit_batch.apply_batch(edl, [
        {"action": "upsert", "layer": "motion", "id": "lock",
         "value": {"id": "lock", "end": 2.5}}], 20, {})
    assert moved["motion"][0]["end"] == 2.5
    # the write's allow_face_overlap passed inside params is honoured here
    # too (the strict check would otherwise refuse it as a template param)
    card = {"id": "card", "template": "image_card", "start": 4.0, "end": 6.0,
            "params": {"caption": "Grid", "y": 0.47, "allow_face_overlap": "true"}}
    got = edit_batch.apply_batch(default_edl(20), [
        {"action": "upsert", "layer": "motion", "id": "card", "value": card}], 20, {})
    placed = got["motion"][0]
    assert placed["allow_face_overlap"] is True and "allow_face_overlap" not in placed["params"]


def test_the_template_list_shows_a_rows_shape_and_type_finds_its_lockups():
    out = motion_tools.list_motion_templates(None, "type")
    for name in ("headline", "marker_text", "counter", "word_slam", "phrase_build"):
        assert f"- {name} [" in out, name
    assert "- counter [data/type," in out
    assert "rows*=[{text,role:sans|serif|script|condensed|mono,size:number," \
           "accent:flag,at:number}]" in out
    miss = motion_tools.list_motion_templates(None, "typography")
    assert "No motion templates match category 'typography'" in miss and "type" in miss


# ── 2. defaults and new knobs ────────────────────────────────────────────

def test_chapter_title_shows_no_number_unless_given():
    assert motion_templates.check_params("chapter_title", {"title": "The lesson"})["number"] == ""
    assert motion_templates.check_params(
        "chapter_title", {"title": "Step", "number": "02"})["number"] == "02"
    # a stored item carries the number it was written with
    stored = {"title": "First", "number": "01"}
    assert motion_templates.normalize_params("chapter_title", stored)["number"] == "01"


def test_timeline_glow_and_image_card_take_size_and_x_and_the_estimate_follows():
    for name, key in (("timeline_steps", "size"), ("glow_title", "size"), ("image_card", "x")):
        p = motion_templates.spec(name)["params"][key]
        assert p["type"] == "float" and p["default"] in (1.0, 0.5), name
    tl = motion_templates.spec("timeline_steps")
    base = {"rows": [{"label": "1983"}, {"label": "2007"}]}
    full = keepout.nominal_ink("timeline_steps", tl, dict(base, y=0.5))
    small = keepout.nominal_ink("timeline_steps", tl, dict(base, y=0.5, size=0.6))
    assert (small[2] - small[0]) == pytest.approx(0.6 * (full[2] - full[0]), rel=1e-6)
    # ...but a narrower layout is never a shorter box (its notes wrap), and
    # 4+ stops with notes stack in a column (measured 0.40 of the frame at
    # 0.75): the browserless keep-out must not see a small band there
    assert small[3] - small[1] >= full[3] - full[1]
    four = dict(rows=[{"label": str(1980 + i), "sub": "a note"} for i in range(4)], y=0.5)
    col = keepout.nominal_ink("timeline_steps", tl, dict(four, size=0.75), frame=(1080, 1920))
    assert col[3] - col[1] >= 0.4 and 0.08 - 1e-9 <= col[1] and col[3] <= 0.80 + 1e-9
    # ...and the keep-out solver never "shrinks" it off a face
    cands = keepout.candidates("timeline_steps", tl, dict(four, size=1.0), full, [],
                               [(0.3, 0.3, 0.7, 0.6)], 1080, 1920)
    assert cands and all("size" not in patch for _c, patch, _b in cands)
    # glow_title: a type scale below 1 (the template now scales a width-bound
    # title too); above 1 its box stays the width it already fills
    glow = motion_templates.spec("glow_title")
    g1, g6, g13 = (keepout.nominal_ink("glow_title", glow, {"text": "x", "y": 0.4, "size": z})
                   for z in (1.0, 0.6, 1.3))
    assert g13 == g1 and (g6[2] - g6[0]) == pytest.approx(0.6 * (g1[2] - g1[0]))
    spec = motion_templates.spec("image_card")
    mid = keepout.nominal_ink("image_card", spec, {"y": 0.4, "size": 0.5})
    left = keepout.nominal_ink("image_card", spec, {"y": 0.4, "size": 0.5, "x": 0.35})
    assert left[0] == pytest.approx(mid[0] - 0.15) and left[2] == pytest.approx(mid[2] - 0.15)
    # the card stops 60 design px inside the frame (image_card.html), so the
    # estimate of a card pushed to the edge stops there too: a face to its
    # right is still under it (a narrow card set beside the face, s05/s06)
    edge = keepout.nominal_ink("image_card", spec, {"y": 0.4, "size": 0.5, "x": 0.2})
    assert edge[0] == pytest.approx(60 / 1080 - 0.006) and \
        edge[2] - edge[0] == pytest.approx(mid[2] - mid[0])


def test_allow_face_overlap_inside_params_is_the_writes_argument():
    spec = motion_templates.spec("image_card")
    params, allow = motion_tools._hoist_face_flag(
        spec, {"y": 0.47, "allow_face_overlap": True}, None)
    assert params == {"y": 0.47} and allow is True
    assert motion_tools._hoist_face_flag(spec, {}, "false")[1] is False
    assert motion_tools._hoist_face_flag(spec, {"allow_face_overlap": "true"}, False)[1] is False
    assert motion_tools._hoist_face_flag(spec, {}, None)[1] is None


# ── 3. the counter's roll ────────────────────────────────────────────────

class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, text, t0=10.0, duration=40.0):
        self.duration = duration
        edl = default_edl(duration)
        edl["keep"] = [[0.0, duration]]
        words, t = [], t0
        for w in text.split():
            words.append({"w": w, "t0": round(t, 3), "t1": round(t + 0.2, 3)})
            t += 0.25
        self.index = {"video": {"width": 1080, "height": 1920, "duration": duration},
                      "words": words}
        self._edl = validate_edl(edl, duration).model_dump()
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": json.loads(json.dumps(self._edl))}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"

    def item(self, mid):
        return next(m for m in self._edl["motion"] if m["id"] == mid)


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4,
                         "bboxes": [[.2, .5, .8, .6]], "ink": [[.2, .5, .8, .6]] * 4})


def test_a_counters_roll_sets_how_long_before_its_word_it_counts(probe):
    ctx = _Ctx("the grid steps it down from three hundred thousand volts to one volt")
    onset = next(w["t0"] for w in ctx.index["words"] if w["w"] == "one")
    out = motion_tools.add_motion_graphic(
        ctx, "counter", onset - 3.0, onset + 1.2, id="volts",
        params={"value": "1", "from": 300000, "roll": 2.4, "label": "volts"})
    it = ctx.item("volts")
    roll = it["params"]["roll"]
    assert roll == 2.4                      # within the template's range now
    target = onset - number_reveal.LEAD_S
    # the window opens with the roll (+ the entrance), not 0.45 s before
    assert it["start"] == pytest.approx(target - (roll + 0.05), abs=0.002)
    assert it["params"]["land"] == pytest.approx(roll + 0.05, abs=0.002)
    assert "over the last ~2.4 s" in out, out
    # an editor's allow_face_overlap inside params is taken, not refused
    out = motion_tools.add_motion_graphic(
        ctx, "counter", 2.0, 4.0, id="plain",
        params={"value": "62,000+", "allow_face_overlap": True})
    assert not out.startswith("REJECTED"), out
    assert ctx.item("plain").get("allow_face_overlap") is True


# ── 4. rendered compositions ─────────────────────────────────────────────

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


needs_browser = pytest.mark.skipif(not (_chromium_ok() and shutil.which("ffmpeg")),
                                   reason="headless Chromium + ffmpeg required")


async def _run(cases, size=(1080, 1920)):
    """[(template, params, duration, t, js_expr)] -> the expression's value
    per case at item second t (after MG.ready)."""
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for name, params, dur, t, expr in cases:
                clean = motion_templates.check_params(name, params)
                job = motion_templates.build_job({"id": name, "template": name, "start": 0.0,
                                                  "end": dur, "params": clean}, size[0], size[1], 30)
                dw, dh = motion_engine.design_size(job.out_w, job.out_h)
                ctx = await browser.new_context(viewport={"width": dw, "height": dh})
                try:
                    await ctx.route("**/*", await motion_engine._route_factory(job))
                    page = await ctx.new_page()
                    await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
                    await page.evaluate("async () => { await document.fonts.ready;"
                                        " if (window.MG && MG.ready) await MG.ready; }")
                    await page.evaluate("t => window.__mgSeek(t)", float(t))
                    out.append(await page.evaluate(expr))
                    assert not await page.evaluate("window.__mgErrors"), name
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


HEADLINE = """(() => { const c = document.querySelector('.claim');
  const words = Array.from(c.querySelectorAll('.mg-w')).map(w => w.textContent);
  const tops = Array.from(c.querySelectorAll('.mg-w')).map(w => Math.round(w.getBoundingClientRect().top));
  return {words, brs: c.querySelectorAll('br').length,
          second: tops[words.indexOf('you')], first: tops[0]}; })()"""
LABEL = "getComputedStyle(document.querySelector('.label')).color"
GLOW = "parseFloat(document.querySelector('#core').style.fontSize)"
STAGE = "document.querySelector('#stage').getBoundingClientRect().width"
CARD = "parseFloat(document.querySelector('.card').style.left)"


@needs_browser
def test_headline_breaks_at_the_slash_and_the_counter_label_takes_its_ink():
    head, label, glow_big, glow_small, wide_big, wide_small, tl_full, tl_small = asyncio.run(_run([
        ("headline", {"text": "Elon Musk: If it's not your goal, / you won't achieve it",
                      "y": 0.15}, 4.0, 1.0, HEADLINE),
        ("counter", {"value": "10x", "label": "every six months", "color": "#0F1B33",
                     "accent": "#6D93D6", "glow": 0}, 3.0, 2.5, LABEL),
        ("glow_title", {"text": "2001", "subline": "1968", "flicker": False}, 2.6, 1.5, GLOW),
        ("glow_title", {"text": "2001", "subline": "1968", "flicker": False, "size": 0.6},
         2.6, 1.5, GLOW),
        ("glow_title", {"text": "The economy of abundance", "flicker": False}, 2.6, 1.5, GLOW),
        ("glow_title", {"text": "The economy of abundance", "flicker": False, "size": 0.6},
         2.6, 1.5, GLOW),
        ("timeline_steps", {"rows": [{"label": "2025"}, {"label": "2027"}]}, 3.5, 2.0, STAGE),
        ("timeline_steps", {"rows": [{"label": "2025"}, {"label": "2027"}], "size": 0.6},
         3.5, 2.0, STAGE),
    ]))
    assert "/" not in head["words"] and head["brs"] == 1
    assert head["second"] > head["first"]           # 'you' starts the second line
    assert label.replace(" ", "") in ("rgba(15,27,51,0.92)", "rgba(15,27,51,.92)")
    assert glow_small < glow_big
    # a width-bound title follows its size too (the keep-out estimate scales
    # the box by it): 0.6 of the type, give or take the 40 px floor
    assert wide_small <= 0.65 * wide_big
    assert tl_small < tl_full


@needs_browser
def test_an_image_card_moves_sideways_with_x():
    centre, left = asyncio.run(_run([
        ("image_card", {"caption": "Grid substation", "size": 0.5, "y": 0.47}, 3.2, 2.0,
         CARD),
        ("image_card", {"caption": "Grid substation", "size": 0.5, "y": 0.47, "x": 0.3},
         3.2, 2.0,
         CARD),
    ]))
    assert left < centre


# ── 5. EARN ITS PLACE: re-typesets stay flagged; an html image is imagery ─

SPEECH = ("the goal of SpaceX is making life multiplanetary and at least you have a chance "
          "of achieving it so a self sustaining city on Mars is at least possible " + "and then " * 40)


def _w(text, start=0.2, step=0.3):
    out, t = [], start
    for tok in text.split():
        out.append({"w": tok, "t0": round(t, 3), "t1": round(t + 0.25, 3)})
        t += step
    return out


def _edl(motion, dur=30.0):
    e = default_edl(dur)
    e["keep"] = [[0.0, dur]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["motion"] = motion
    return e


def _mg(mid, tpl, s, e, **params):
    return {"id": mid, "template": tpl, "start": s, "end": e, "params": params}


def _codes(edl, words):
    idx = {"words": words, "video": {"width": 1080, "height": 1920, "duration": 30.0}}
    return {n["code"]: n for n in edit_review.review(edl, idx, force=True)}


def test_a_short_starred_lockup_of_heard_words_is_still_a_retypeset():
    # The s09 editor called its band lockups the Look's own; its reviewer
    # answered "EARN ITS PLACE is right" and the s07/s08 reviews killed on
    # the same re-typeset, so the flag stands for a starred two-row lockup
    # and for a payoff lockup beside its image.
    words = _w(SPEECH)
    t = next(w["t0"] for w in words if w["w"] == "multiplanetary")
    lockup = _mg("goal", "phrase_build", t - 0.6, t + 1.5, rows=[
        {"text": "making life"}, {"text": "*multiplanetary*"}])
    hit = _codes(_edl([lockup]), words).get("restates_captions")
    assert hit and "'goal'" in hit["message"]
    m = next(w["t0"] for w in words if w["w"] == "Mars")
    payoff = _mg("payoff", "phrase_build", m - 1.0, 30.0, rows=[
        {"text": "a self sustaining"}, {"text": "city on Mars"}, {"text": "is at least possible"}])
    image = dict(_mg("mars", "html", m - 0.5, 30.0, asset_mars="fetched/1/mars.jpg"),
                 html="<img src='assets/asset_mars'>")
    hit = _codes(_edl([lockup, payoff, image]), words).get("restates_captions")
    assert hit and "'goal'" in hit["message"] and "'payoff'" in hit["message"]


def test_an_html_graphic_placing_a_project_image_is_imagery():
    words = _w(SPEECH)
    t = next(w["t0"] for w in words if w["w"] == "Mars")
    payoff = _mg("payoff", "phrase_build", t - 1.0, 30.0, rows=[
        {"text": "a self sustaining"}, {"text": "city on Mars"}, {"text": "is at least possible"}])
    image = dict(_mg("mars", "html", t - 0.5, 30.0, asset_mars="fetched/1/mars.jpg"),
                 html="<img src='assets/asset_mars'>")
    m = edit_review.moments(_edl([payoff, image]))
    assert edit_review._html_image(next(x for x in m if x["id"] == "mars"))
    # s09: the NASA Mars photo was there, yet 'Mars' read as set only as type
    shows = _codes(_edl([payoff, image]), words).get("showable_moment")
    assert not shows or "'Mars'" not in shows["message"]
    shows = _codes(_edl([payoff]), words).get("showable_moment")
    assert shows and "'Mars'" in shows["message"]


# ── 6. the erase: repaint v2 and its honesty check ───────────────────────

FF = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FF, reason="ffmpeg required")


def _scene(path, n=36, W=320, H=240, pan=3, obj=True):
    """A handheld shot: a textured wall panning `pan` px a frame, with a
    picture-in-picture window (its own texture) fixed at x 120-180, y
    60-160 — the PiP of a call that the editors erased with fill='box'."""
    rng = np.random.default_rng(7)
    wall = (rng.random((H, W + n * pan + 8)) * 255).astype(np.float32)
    import cv2
    wall = cv2.GaussianBlur(wall, (0, 0), 3)
    wall = (wall - wall.min()) / (wall.max() - wall.min()) * 120 + 110
    pip = (rng.random((100, 60)) * 255).astype(np.float32)
    pip = cv2.GaussianBlur(pip, (0, 0), 1.2) * 0.6 + 20
    enc = subprocess.Popen([FF, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
                            "-s", f"{W}x{H}", "-r", "12", "-i", "pipe:0", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", "12", "-pix_fmt", "yuv420p", path],
                           stdin=subprocess.PIPE)
    for k in range(n):
        f = wall[:, k * pan:k * pan + W].copy()
        if obj:
            f[60:160, 120:180] = pip
        enc.stdin.write(np.clip(f, 0, 255).astype(np.uint8).tobytes())
    enc.stdin.close()
    assert enc.wait() == 0


BOX = {"id": "er1", "x": 120 / 320, "y": 60 / 240, "w": 60 / 320, "h": 100 / 240,
       "fill": "box", "start": 0.5, "end": 2.5}


@needs_ffmpeg
def test_a_box_repaint_is_judged_by_what_survives_of_the_box(tmp_path):
    src = str(tmp_path / "src.mp4")
    _scene(src)
    out = str(tmp_path / "patch.mp4")
    st = inpaint.build_patch(src, [BOX], (0.0, 3.0), out, repaint=inpaint.REPAINT_VERSION)
    before, after, metric = inpaint.erase_measure(src, out, inpaint._clamped([BOX], st),
                                                  st["src_start"])
    assert metric == ["pattern"] and before == [100.0]
    assert after[0] <= 35.0                          # gone: the PiP's picture is not there
    # the same check on an untouched copy says the box is all still there
    same = inpaint.pattern_kept(src, src, (BOX["x"], BOX["y"], BOX["w"], BOX["h"]), [1.0, 2.0])
    assert same >= 90.0
    # a text fill is still judged by its ink
    text = dict(BOX, fill="text")
    assert inpaint.erase_measure(src, src, [text])[2] == ["ink"]


def test_a_static_plate_is_pasted_only_on_frames_that_still_match_it():
    region = inpaint._Region({"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5, "fill": "box"}, 80, 80)
    band = np.full((region.y1 - region.y0, region.x1 - region.x0, 3), 150, np.uint8)
    region.plate = band.copy()
    region.plate_ok = np.ones(band.shape[:2], bool)
    mask = region.mask_for(band)
    assert inpaint._plate_fits(band, mask, region)
    moved = band.copy()
    moved[:, : moved.shape[1] // 2] = 90             # the camera moved: the wall changed
    assert not inpaint._plate_fits(moved, mask, region)


def test_a_whole_box_hole_is_a_smooth_membrane_not_a_dark_wedge():
    import cv2
    band = np.full((120, 120, 3), 200, np.uint8)
    band[:10, 60:] = 30                              # a dark fixture over one corner of the ring
    m = np.zeros((120, 120), bool)
    m[10:110, 10:110] = True
    tel = cv2.inpaint(band, m.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA).astype(np.float32)
    mem = inpaint._membrane(band, m).astype(np.float32)
    inner = np.zeros_like(m)
    inner[30:90, 30:90] = True

    def edge(img):
        g = cv2.cvtColor(img.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
        return float(np.hypot(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))[inner].max())
    assert edge(mem) < 0.5 * edge(tel)               # no hard-edged wedge inside the fill
    assert (mem[~m] == band[~m]).all()               # the surroundings are untouched


def test_a_patch_records_its_repaint_and_old_patches_keep_their_fingerprint():
    regs = [dict(BOX)]
    legacy = patch_fingerprint("sha", regs, (0.0, 3.0))
    assert patch_fingerprint("sha", regs, (0.0, 3.0), None) == legacy
    assert patch_fingerprint("sha", regs, (0.0, 3.0), 2) != legacy
    item = PatchItem(id="pa1", asset_key="patches/1/x.mp4", fp=legacy, src_start=0.0,
                     src_end=3.0, regions=regs)
    assert item.repaint is None
    assert PatchItem(**dict(item.model_dump(), repaint=2)).repaint == 2


# ── 7. captions: deleting a word ─────────────────────────────────────────

def test_an_empty_caption_fix_deletes_the_words():
    caps = CaptionsFromTranscript(corrections=[
        {"from": "we're not breaking any", "to": "", "start": 21.5, "end": 22.4},
        {"from": "on on", "to": "on"}])
    assert caps.corrections[0]["to"] == ""
    words = [{"w": "growth", "t0": 1.0, "t1": 1.3},
             {"w": "on", "t0": 1.3, "t1": 1.4, "brk": True},
             {"w": "on", "t0": 1.4, "t1": 1.5},
             {"w": "growth.", "t0": 1.5, "t1": 1.9}]
    out = caplib.apply_scoped_fixes(words, [{"from": "on on", "to": ""}])
    assert [w["w"] for w in out] == ["growth", "growth."]
    assert out[1].get("brk") is True                  # the insert break moves on
    with pytest.raises(ValueError):
        CaptionsFromTranscript(corrections=[{"from": " ", "to": "x"}])


# ── 8. keep edges: never pull in the neighbour ───────────────────────────

# 'What what is the constraint', each word touching the next (the index
# times words to 5 ms; the EDL keeps hundredths)
STUTTER = [{"w": "What", "t0": 974.58, "t1": 974.90}, {"w": "what", "t0": 974.90, "t1": 975.06},
           {"w": "is", "t0": 975.06, "t1": 975.22}, {"w": "the", "t0": 975.22, "t1": 975.30},
           {"w": "constraint", "t0": 975.30, "t1": 975.62}]


def test_the_snap_pad_never_reaches_into_a_touching_word():
    # a start ON 'what' gets no lead-in out of 'What' it touches
    assert audit.snap_keep_to_words([[974.90, 975.62]], STUTTER, 2000.0)[0][0] == 974.90
    # an end on 'What's end gets no breath into 'what'
    assert audit.snap_keep_to_words([[970.0, 974.90]], STUTTER, 2000.0)[0][1] == 974.90


def test_an_edge_keeping_a_sliver_of_a_word_cuts_that_word():
    # 974.85 keeps 16% of 'What': the editor was cutting it
    assert audit.snap_keep_to_words([[974.85, 975.62]], STUTTER, 2000.0)[0][0] == 974.90
    # ...and mid-word edges that keep most of their word still keep it whole
    assert audit.snap_keep_to_words([[974.65, 975.62]], STUTTER, 2000.0)[0][0] < 974.58
    # the audio-safe placement, without the sound: past the word, not before it
    got = cut_audio.place_edge("start", 974.85, STUTTER)
    assert got["why"] == "word" and 974.89 <= got["t"] <= 974.90 and got.get("dropped")
    # with the sound: loud through the stutter, a pause nowhere near
    def level(t):
        return -15.0
    got = cut_audio.place_edge("start", 974.85, STUTTER, level)
    assert 974.89 <= got["t"] <= 974.90
    new, moves, checked = cut_audio.refine_keep([[960.0, 970.0], [974.85, 975.62]],
                                                STUTTER, 2000.0, None, level)
    assert new[1][0] >= 974.89
    assert any("past 'What'" in line for line in cut_audio.report(moves, checked))
    # an end keeping a sliver of 'what' ends before it
    got = cut_audio.place_edge("end", 974.93, STUTTER)
    assert got["why"] == "word" and 974.90 <= got["t"] <= 974.91


def test_a_sliver_edge_its_span_cannot_carry_past_the_word_keeps_it_whole():
    # [974.85, 974.92]: the span's own limits stop the start inside 'What'
    # (it used to recurse until RecursionError, and the write then lost the
    # audio-safe placement of every edge)
    for level in (None, lambda t: -15.0):
        new, moves, _n = cut_audio.refine_keep([[960.0, 970.0], [974.85, 974.92]],
                                               STUTTER, 2000.0, None, level)
        start = next(m for m in moves if m["side"] == "start" and m["old"] == 974.85)
        assert not start.get("dropped")
        assert new[-1][0] <= 974.85


def test_an_edge_written_at_a_5ms_word_boundary_never_reaches_across_the_word():
    # 'pulling' ends 1574.155 and 'a' starts there; a start written at
    # 1574.15 reached 0.24 s back through 'pulling' for a quiet point
    words = [{"w": "For", "t0": 1573.755, "t1": 1573.835},
             {"w": "pulling", "t0": 1573.835, "t1": 1574.155},
             {"w": "a", "t0": 1574.155, "t1": 1574.315}]

    def level(t):
        return -60.0 if t < 1573.92 else -17.0
    got = cut_audio.place_edge("start", 1574.15, words, level)
    assert got["t"] >= 1574.14


# ── 9. set_frame, find_silences, review_audio ────────────────────────────

def test_a_focus_nudge_lost_to_rounding_says_so():
    frame = Frame.model_validate({"ratio": "9:16", "mode": "crop", "focus_x": 0.5304})
    edl = default_edl(30)
    edl["frame"] = frame.model_dump()
    ctx = SimpleNamespace(
        index={"video": {"width": 1920, "height": 1080}}, has_main_video=False,
        latest_edl=lambda: {"version": 26, "json": json.loads(json.dumps(edl))},
        write_edl=lambda e, d: "NO CHANGE — the EDL is identical to v26; the requested "
                               "change may need a different tool or may not be supported.")
    out = agent_tools._set_frame(ctx, "9:16", "crop", 0.5304, None)
    assert out.startswith("NO CHANGE") and "FOCUS ROUNDING: focus_x 0.5304 is stored as 0.53" in out
    assert "~2 px" in out


def _silence_ctx(keep):
    words = []
    t = 0.0
    for i in range(200):                              # a word every 0.5 s, a 1 s pause every 10
        words.append({"w": f"w{i}", "t0": t, "t1": t + 0.3})
        t += 1.3 if i % 10 == 9 else 0.5
    edl = default_edl(200.0)
    edl["keep"] = keep
    return SimpleNamespace(duration=200.0, index={"words": words, "silences": [],
                                                 "video": {"duration": 200.0}},
                           latest_edl=lambda: {"version": 2, "json": edl})


def test_find_silences_lists_only_the_range_asked_for():
    ctx = _silence_ctx([[0.0, 200.0]])
    whole = agent_tools.find_silences(ctx, 0.7)
    part = agent_tools.find_silences(ctx, 0.7, start=20, end=40)
    n_whole = int(whole.split(" gap")[0])
    n_part = int(part.split(" gap")[0])
    assert 0 < n_part < n_whole and "within 20.00-40.00s" in part
    for line in part.splitlines()[1:]:
        s, e = (float(x) for x in line.split(" ")[0].split("-"))
        assert e > 20 and s < 40
    assert agent_tools.find_silences(ctx, 0.7, start=40, end=20).startswith("REJECTED")
    # a short cut from a long source is told how to ask for its own span
    short = agent_tools.find_silences(_silence_ctx([[50.0, 70.0], [80.0, 90.0]]), 0.7)
    assert "find_silences(start=50, end=90)" in short


def test_review_audio_hears_six_windows_in_batches_anchored_where_asked(monkeypatch, tmp_path):
    cut, asked = [], []
    monkeypatch.setattr(llm, "audio_review_available", lambda: True)
    monkeypatch.setattr(agent_tools.media, "extract_audio_clip",
                        lambda src, a, b, dst: cut.append((round(a, 2), round(b, 2))))
    froms = []
    monkeypatch.setattr(llm, "ask_audio", lambda prompt, paths, labels, **kw: (
        froms.append(kw.get("number_from", 1)),
        asked.append((prompt, list(labels), kw.get("max_tokens"))))[-1] or "clean joins")
    ctx = SimpleNamespace(has_main_video=True, duration=60.0, project_id=7, workdir=str(tmp_path),
                          proxy_path=lambda: "/cache/proxy.mp4",
                          db=SimpleNamespace(run=lambda *a, **k: None), edit_plan={})
    out = agent_tools.review_audio(ctx, times=[5, 10, 15, 20, 25, 30, 35], span_s=2,
                                   anchor="start")
    assert cut == [(5.0, 7.0), (10.0, 12.0), (15.0, 17.0), (20.0, 22.0), (25.0, 27.0), (30.0, 32.0)]
    assert [len(labels) for _p, labels, _m in asked] == [3, 3]
    assert "CLIPS 4-6 of 6" in asked[1][0]
    # ...and the listener sees them as CLIP 4-6 too, not a second CLIP 1-3
    assert froms == [1, 4]
    assert "CLIP 6: main-video SOURCE sound (from the proxy) 30.0-32.0s" in out
    assert "NOT HEARD" in out and "35s" in out
    cut.clear()
    agent_tools.review_audio(ctx, times=[10], span_s=2)          # a join: centred
    assert cut == [(9.0, 11.0)]
    cut.clear()
    agent_tools.review_audio(ctx, times=[10], span_s=2, anchor="end")
    assert cut == [(8.0, 10.0)]
    assert agent_tools.review_audio(ctx, times=[10], anchor="middle").startswith("REJECTED")


def test_the_listener_numbers_a_later_calls_clips_on(monkeypatch, tmp_path):
    import config
    clips = []
    for i in range(2):
        c = tmp_path / f"c{i}.mp3"
        c.write_bytes(b"bounded-audio")
        clips.append(str(c))
    sent = {}

    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "CLIP 4: clean. CLIP 5: clean."}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 4}}

    monkeypatch.setattr(config, "AUDIO_REVIEW_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_audio_review_dead", False)
    monkeypatch.setattr(llm.requests, "post",
                        lambda url, **kw: (sent.update(kw) or Response()))
    monkeypatch.setattr(llm, "record", lambda *a, **k: None)
    llm.ask_audio("Judge the joins.", clips, ["a 1.0-3.0s", "b 5.0-7.0s"], number_from=4)
    texts = [p.get("text") for p in sent["json"]["messages"][0]["content"] if p.get("type") == "text"]
    assert "CLIP 4: a 1.0-3.0s" in texts and "CLIP 5: b 5.0-7.0s" in texts


# ── 10. audit_captions sees the graphics ─────────────────────────────────

def test_the_caption_audit_names_graphic_text_and_captions_under_a_graphic():
    edl = _edl([
        _mg("card", "image_card", 3.0, 6.5, caption="AI chips", chip="2023"),
        _mg("head", "headline", 0.0, 30.0, text="After chips, AI ran into a *voltage problem*"),
        dict(_mg("pun", "html", 8.0, 9.0, asset_photo="fetched/7/stock/ab12.jpg"),
             html="<img src='assets/asset_photo'><b>the power-grid kind</b>")])
    edl["texts"] = [{"id": "t1", "text": "PEOPLE KEEP GUESSING", "start": 0.0, "end": 2.0}]
    words = [{"w": "were", "t0": 4.0, "t1": 4.2, "src_t0": 4.0},
             {"w": "growth.", "t0": 4.3, "t1": 4.6, "src_t0": 4.3}]
    plan = SimpleNamespace(report={
        "card": {"carried": [{"w": "AI"}, {"w": "chips"}], "kept": words,
                 "box": [0.0, 0.2, 0.7, 0.9], "estimated": False},
        "head": {"carried": [], "kept": words, "box": [0.09, 0.13, 0.9, 0.2],
                 "estimated": False}})
    shown, under = agent_tools._caption_graphics_report(edl, plan)
    by_id = {r["id"]: r for r in shown}
    assert by_id["card"]["text"] == "AI chips / 2023" or "AI chips" in by_id["card"]["text"]
    assert by_id["card"]["shows_caption_words"] == "AI chips"
    assert by_id["t1"]["text"] == "PEOPLE KEEP GUESSING"
    assert by_id["pun"]["text"] == "the power-grid kind"      # its photo's key is no text
    # the card's box meets the caption band; the headline band above does not
    assert [u["id"] for u in under] == ["card"] and under[0]["words"] == "were growth."


# ── 11. the editorial map ranks its peaks ────────────────────────────────

def _row(i, text, tags=(), stress=None):
    audio = {"measured": True, "level": "medium", "mean_db": -12.0, "trend": "steady"}
    if stress is not None:
        audio.update(vocal_stress=stress, stressed_word=text.split()[0], stress_at_s=i * 10.0)
    return {"id": f"s{i}", "kind": "speech", "t0": i * 10.0, "t1": i * 10.0 + 4.0,
            "text": text, "shots": {}, "audio": audio, "picture": {"measured": False},
            "pauses": {"speech_gap_before_s": 0.0, "speech_gap_after_s": 0.0},
            "tags": list(tags)}


def test_editorial_peaks_are_ranked_by_measured_evidence(monkeypatch):
    rows = [_row(0, "and so we went on and on about it", ("energy_rising",), 0.3),
            _row(1, "Yeah.", ("energy_peak", "pause_before", "pause_after"), 0.97),
            _row(2, "it is not bad for a bunch of monkeys", ("energy_peak", "pause_after"), 0.93),
            _row(3, "we only have one shot at this future", ("vocal_emphasis",), 0.8)]
    ranked = editorial_index.rank_peaks(rows)
    # a lone 'Yeah.' with every tag counts as the fragment it is
    assert [r["id"] for _k, _s, _w, r in ranked] == ["s2", "s3", "s0", "s1"]
    emap = {"duration_s": 60.0, "rows": rows, "measured": {"speech": True}}
    monkeypatch.setattr(agent_tools, "_editorial_map_for",
                        lambda ctx, key=None: (emap, "main source", None))
    out = agent_tools.get_editorial_map(SimpleNamespace(), focus="peaks", limit=2)
    assert "PEAKS RANKED" in out and "the strongest 2 of 4, in source order" in out
    body = [ln for ln in out.splitlines() if ln.startswith("peak #")]
    assert [ln.split()[1] for ln in body] == ["#1", "#2"]       # s2 then s3, in time
    assert "[s2 " in body[0] and "[s3 " in body[1] and "[s1 " not in out
    assert "2 weaker peak row(s) not shown" in out
