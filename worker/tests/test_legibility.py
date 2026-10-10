"""Automatic legibility: the measured plate under motion graphics and
captions (worker/plate.py -> MG.plate), the bright-plate backings / dark ink
the templates derive from it, and the secondary-text size floor."""

import asyncio
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import config  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import plate  # noqa: E402
import renderer  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

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


CHROMIUM = _chromium_ok()
needs_browser = pytest.mark.skipif(not (CHROMIUM and FFMPEG),
                                   reason="headless Chromium + ffmpeg required")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg required")


class _UniformProbe:
    """A plate probe that sees one flat luma everywhere."""

    def __init__(self, luma, W=1080, H=1920):
        self.cols, self.rows = plate.COLS, plate.grid_rows(W, H)
        self.luma = luma
        self.calls = []

    def __call__(self, times):
        self.calls.append(list(times))
        return [[self.luma] * (self.cols * self.rows) for _ in times]


# ── the measurement (no browser) ────────────────────────────────────────

def test_pgm_frames_parse_a_concatenated_stream():
    a = b"P5\n3 2\n255\n" + bytes([0, 10, 20, 30, 40, 50])
    b = b"P5 2 1 255\n" + bytes([7, 9])
    frames = plate._pgm_frames(a + b)
    assert frames == [(3, 2, bytes([0, 10, 20, 30, 40, 50])), (2, 1, bytes([7, 9]))]
    assert plate._pgm_frames(a[:-2]) == []          # truncated: nothing invented


def test_canvas_grid_follows_the_render_geometry():
    from PIL import Image
    img = Image.new("L", (320, 180), 0)
    img.paste(255, (0, 0, 160, 180))                 # left half white, right black
    rows = plate.grid_rows(1080, 1920)
    left = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop", focus=(0.2, 0.5))
    right = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop", focus=(0.8, 0.5))
    assert len(left) == plate.COLS * rows
    assert min(left) >= 250 and max(right) <= 5      # the crop window follows the focus
    # letterboxed: bars above and below are black, the picture band is half white
    pad = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="pad")
    assert max(pad[:plate.COLS]) <= 5 and max(pad[-plate.COLS:]) <= 5
    mid = pad[(rows // 2) * plate.COLS:(rows // 2 + 1) * plate.COLS]
    assert mid[0] >= 250 and mid[-1] <= 5
    # the camera's viewport: a 2x zoom on the left edge of a centre crop
    centre = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop")
    zoomed = plate.canvas_grid(img, (1920, 1080), 1080, 1920, mode="crop",
                               zoom=(2.0, 0.0, 0.5))
    assert sum(zoomed) > sum(centre)                 # more of the white half on screen


def test_plate_moments_sit_inside_the_item_on_its_composition_clock():
    m = motion_layer.plate_moments({"template": "word_slam", "start": 10.0, "end": 11.0})
    assert [round(p, 3) for p, _c in m] == [10.25, 10.75]
    assert [round(c, 3) for _p, c in m] == [0.25, 0.75]
    # a windowed fragment's composition clock carries its phase
    m = motion_layer.plate_moments({"template": "word_slam", "start": 10.0, "end": 11.0,
                                    "phase_s": 2.0})
    assert [round(c, 3) for _p, c in m] == [2.25, 2.75]
    long = motion_layer.plate_moments({"template": "quote_card", "start": 0.0, "end": 10.0})
    assert len(long) == motion_layer.PLATE_MAX_PER_ITEM
    # the caption track: one moment per short cue, two for a long one
    cap = {"template": "caption_motion", "start": 4.0, "end": 9.0, "params": {"cues": [
        {"s": 0.0, "e": 1.0, "w": []}, {"s": 1.0, "e": 3.0, "w": []}]}}
    m = motion_layer.plate_moments(cap)
    assert [round(c, 3) for _p, c in m] == [0.5, 1.6, 2.5]
    assert [round(p, 3) for p, _c in m] == [4.5, 5.6, 6.5]


def test_measure_plates_attaches_grids_and_fails_open():
    items = [{"id": "a", "template": "word_slam", "start": 1.0, "end": 2.0},
             {"id": "b", "template": "word_slam", "start": 1.0, "end": 2.0}]
    probe = _UniformProbe(200)
    out = motion_layer.measure_plates(items, probe)
    assert set(out) == {0, 1}
    assert out[0]["c"] == plate.COLS and out[0]["r"] == plate.grid_rows(1080, 1920)
    assert [s["t"] for s in out[0]["s"]] == [0.25, 0.75]
    assert len(probe.calls) == 1 and len(probe.calls[0]) == 2   # shared moments measured once

    class Broken(_UniformProbe):
        def __call__(self, times):
            raise RuntimeError("decoder died")
    assert motion_layer.measure_plates(items, Broken(0)) == {}

    class Blind(_UniformProbe):
        def __call__(self, times):
            return [None for _ in times]
    assert motion_layer.measure_plates(items, Blind(0)) == {}
    assert motion_layer.measure_plates(items, None) == {}


def test_clusters_stay_bounded_and_long_programs_spread_their_samples():
    # dense moments across a minute: several bounded decodes, not one that a
    # 4K original would run past its timeout
    ts = [0.4 + 0.8 * i for i in range(75)]
    cls = plate._clusters(ts)
    assert len(cls) > 1 and sum(len(c) for c in cls) == len(ts)
    assert all(c[-1] - c[0] <= plate.MAX_SPAN_S for c in cls)
    assert plate._clusters([1.0, 2.0, 9.0]) == [[1.0, 2.0], [9.0]]
    # an even spread keeps both ends (never just the first N moments)
    assert plate._spread(10, 3) == [0, 4, 9]
    assert plate._spread(3, 5) == [0, 1, 2]


def test_grids_reach_the_page_compactly_and_never_break_the_size_cap(monkeypatch):
    import base64
    # a dense one-word caption segment: one grid per cue
    cues = [{"s": round(0.25 * i, 3), "e": round(0.25 * i + 0.24, 3),
             "w": [{"t": "w", "s": round(0.25 * i, 3), "e": round(0.25 * i + 0.24, 3)}]}
            for i in range(48)]
    item = {"id": "__captions_0", "template": "caption_motion", "start": 0.0, "end": 12.0,
            "params": {"look": "pop", "cues": cues}}
    probe = _UniformProbe(231)
    p = motion_layer.measure_plates([item], probe)[0]
    assert len(p["s"]) == 48
    assert list(base64.b64decode(p["s"][0]["g"])) == [231] * (p["c"] * p["r"])
    job = motion_templates.build_job(item, 1080, 1920, 30, plate=p)
    assert '"plate"' in job.html
    # the grids themselves are compact (a quarter of the cap for 48 cues),
    # and the whole page stays well under it
    bare = motion_templates.build_job(item, 1080, 1920, 30)
    assert len(job.html.encode()) - len(bare.html.encode()) < motion_engine.MAX_HTML_BYTES * 0.25
    assert len(job.html.encode()) < motion_engine.MAX_HTML_BYTES * 0.65
    # a composition the plate would push over the cap renders without one
    monkeypatch.setattr(motion_engine, "MAX_HTML_BYTES", len(bare.html.encode()) + 100)
    capped = motion_templates.build_job(item, 1080, 1920, 30, plate=p)
    assert capped.html == bare.html


def test_a_document_without_a_plate_is_unchanged():
    body = "<div class='mg-root'></div>"
    assert motion_engine.build_document(body) == motion_engine.build_document(body, plate=None)
    assert '"plate"' not in motion_engine.build_document(body)
    with_plate = motion_engine.build_document(body, plate={"c": 1, "r": 1, "s": [{"t": 0, "g": [9]}]})
    assert '"plate"' in with_plate


@needs_ffmpeg
def test_probe_measures_the_program_through_the_timeline(tmp_path, monkeypatch):
    monkeypatch.setattr(plate, "CACHE_DIR", str(tmp_path / "plates"))
    src = str(tmp_path / "src.mp4")
    subprocess.run([FFMPEG, "-v", "error", "-y",
                    "-f", "lavfi", "-i", "color=white:s=320x180:d=2:r=25",
                    "-f", "lavfi", "-i", "color=black:s=320x180:d=2:r=25",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", src], check=True)
    edl = {"keep": [[0.0, 1.0], [3.0, 4.0]], "frame": {"ratio": "9:16", "mode": "crop"}}
    tl = Timeline(edl["keep"])
    probe = plate.Probe(edl, tl, src, (320, 180), 540, 960, tl.out_duration,
                        frame_mode="crop")
    white, black = probe([0.5, 1.5])
    assert len(white) == probe.cols * probe.rows
    assert min(white) >= 245 and max(black) <= 10   # program 1.5 s plays source 3.5 s
    assert probe.stats["decoded"] == 2
    again = plate.Probe(edl, tl, src, (320, 180), 540, 960, tl.out_duration,
                        frame_mode="crop")
    assert again([0.5, 1.5]) == [white, black]
    assert again.stats["cached"] == 2 and again.stats["decoded"] == 0
    # an unmappable moment (no footage, no insert) is unmeasured, not fatal
    assert probe([5.0]) == [None]
    # a final (the original, full size) reuses what the preview measured on
    # the proxy: the render job names the source, the grid lives in design space
    proxy = str(tmp_path / "proxy.mp4")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", src, "-vf", "scale=160:90",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", proxy], check=True)
    tok = plate.source_scope("sha-of-the-original:")
    try:
        prev = plate.Probe(edl, tl, proxy, (160, 90), 540, 960, tl.out_duration, frame_mode="crop")
        prev([0.25])
        final = plate.Probe(edl, tl, src, (320, 180), 1080, 1920, tl.out_duration, frame_mode="crop")
        final([0.25])
    finally:
        plate.end_source_scope(tok)
    assert prev.stats["decoded"] == 1 and final.stats["cached"] == 1
    # more moments than the per-render cap: an even spread over the whole
    # program, answered in place (the list keeps its length)
    monkeypatch.setattr(plate, "MAX_SAMPLES", 2)
    spread = plate.Probe(edl, tl, src, (320, 180), 540, 960, tl.out_duration,
                         frame_mode="crop")
    got = spread([0.3, 0.6, 1.2, 1.7])
    assert len(got) == 4 and got[1] is None and got[2] is None
    assert min(got[0]) >= 245 and max(got[3]) <= 10


def test_renderer_builds_a_probe_only_when_the_motion_engine_draws(monkeypatch):
    monkeypatch.setattr(motion_engine, "available", lambda: True)
    edl = {"keep": [[0.0, 4.0]]}
    tl = Timeline(edl["keep"])
    assert renderer._plate_probe(edl, tl, "x.mp4", (16, 9), 1080, 1920,
                                 "crop", None, {}) is None
    assert isinstance(renderer._plate_probe(dict(edl, motion=[{"id": "m"}]), tl, "x.mp4",
                                            (16, 9), 1080, 1920, "crop", None, {}),
                      plate.Probe)
    assert isinstance(renderer._plate_probe(edl, tl, "x.mp4", (16, 9), 1080, 1920,
                                            "crop", None, {}, captions=True), plate.Probe)
    # a lane that cannot draw graphics decodes nothing for them
    monkeypatch.setattr(motion_engine, "available", lambda: False)
    assert renderer._plate_probe(dict(edl, motion=[{"id": "m"}]), tl, "x.mp4", (16, 9),
                                 1080, 1920, "crop", None, {}) is None


def test_legibility_stamp_busts_only_renders_the_motion_engine_drew():
    plain = {"keep": [[0, 4]]}
    assert renderer.legibility_current({}, plain)
    graphic = dict(plain, motion=[{"id": "m", "template": "word_slam"}])
    assert not renderer.legibility_current({}, graphic)
    assert not renderer.legibility_current({"legib_v": config.LEGIBILITY_VERSION - 1}, graphic)
    assert renderer.legibility_current({"legib_v": config.LEGIBILITY_VERSION}, graphic)
    looked = dict(plain, captions={"mode": "from_transcript",
                                   "style": {"motion_look": "editorial"}})
    assert not renderer.legibility_current({}, looked)
    assert renderer.legibility_current({"legib_v": config.LEGIBILITY_VERSION}, looked)


# ── the runtime (real Chromium) ─────────────────────────────────────────

def _grid(luma, top_rows_black=0, W=1080, H=1920):
    cols, rows = plate.COLS, plate.grid_rows(W, H)
    return [0 if r < top_rows_black else luma for r in range(rows) for _c in range(cols)]


async def _eval(job, scripts, t=None):
    """Load a composition (fonts + MG.ready), optionally seek, run scripts."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        dw, dh = motion_engine.design_size(job.out_w, job.out_h)
        ctx = await browser.new_context(viewport={"width": dw, "height": dh})
        await ctx.route("**/*", await motion_engine._route_factory(job))
        page = await ctx.new_page()
        await page.goto(motion_engine.ORIGIN + "/")
        await page.evaluate("async () => { await document.fonts.ready; if (MG.ready) await MG.ready; }")
        if t is not None:
            await page.evaluate("t => window.__mgSeek(t)", t)
        out = [await page.evaluate(s) for s in scripts]
        errs = await page.evaluate("window.__mgErrors")
        await browser.close()
    assert not errs, errs
    return out


def _doc_job(plate_dict, body="<div class='mg-root'><span id='k' style=\"font:800 40px 'Inter Display'\">Kicker</span></div>"):
    html = motion_engine.build_document(body, plate=plate_dict, duration=2.0)
    return motion_engine.RenderJob(html=html, out_w=540, out_h=960, fps=30, duration=2.0)


@needs_browser
def test_need_reaches_the_contrast_target_and_never_touches_dark_plates():
    rect = "[100, 900, 980, 1000]"
    bright = {"c": plate.COLS, "r": plate.grid_rows(1080, 1920), "s": [{"t": 1.0, "g": _grid(240)}]}
    dark = {"c": plate.COLS, "r": plate.grid_rows(1080, 1920), "s": [{"t": 1.0, "g": _grid(30)}]}
    split = {"c": plate.COLS, "r": plate.grid_rows(1080, 1920),
             "s": [{"t": 1.0, "g": _grid(240, top_rows_black=15)}]}
    lin = "v => v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)"
    (b_need, b_contrast, b_display, b_have, b_dark_ok, b_dark_need, b_cap) = asyncio.run(_eval(_doc_job(bright), [
        f"MG.need({rect}, {{ ink: '#FFFFFF', ratio: 4.5 }})",
        f"(() => {{ const a = MG.need({rect}, {{ ink: '#FFFFFF' }}); const lin = {lin};"
        f" return MG.contrast(1, lin(240 / 255 * (1 - a))); }})()",
        f"MG.need({rect}, {{ ink: '#FFFFFF', ratio: 3 }})",
        f"MG.need({rect}, {{ ink: '#FFFFFF', have: MG.need({rect}, {{}}) }})",
        f"MG.darkInkOK({rect}, {{ ratio: 4.5 }})",
        f"MG.need({rect}, {{ ink: '#141414' }})",
        "(() => { const k = document.getElementById('k'); MG.floorType(k);"
        " return parseFloat(k.style.fontSize) * MG.capRatio(k); })()",
    ]))
    assert 0.3 < b_need < 0.9
    assert b_contrast >= 4.5 - 0.01                  # body text reaches 4.5:1 exactly
    assert 0 < b_display < b_need                    # display type asks for less
    assert b_have == 0                               # darkening already there counts
    assert b_dark_ok and b_dark_need == 0            # dark ink reads; never darkened for
    assert abs(b_cap - config_cap()) < 1.0           # the floor is a cap height of 2.2% FH
    d_need, d_dark_ok = asyncio.run(_eval(_doc_job(dark), [
        f"MG.need({rect}, {{ ink: '#FFFFFF' }})", f"MG.darkInkOK({rect}, {{}})"]))
    assert d_need == 0 and not d_dark_ok
    s_need, s_dark_ok, s_lower = asyncio.run(_eval(_doc_job(split), [
        "MG.need([0, 0, 1080, 1920], { ink: '#FFFFFF' })", "MG.darkInkOK([0, 0, 1080, 1920], {})",
        "MG.need([0, 1500, 1080, 1700], { ink: '#FFFFFF' })"]))
    assert s_need > 0 and not s_dark_ok and s_lower > 0   # mixed plate: a pocket, not dark ink
    none_need, none_ok = asyncio.run(_eval(_doc_job(None), [
        f"MG.need({rect}, {{}})", f"MG.darkInkOK({rect}, {{}})"]))
    assert none_need == 0 and not none_ok


def config_cap():
    return 0.022 * 1920


def _caption_item():
    words = [("We", 0.30, 0.45), ("went", 0.46, 0.70), ("from", 0.72, 0.90),
             ("zero", 0.92, 1.30), ("to", 1.32, 1.40), ("$500,000", 1.42, 2.10),
             ("in", 2.12, 2.22), ("four", 2.24, 2.48), ("months.", 2.50, 3.00)]
    edl = default_edl(4.0)
    edl["captions"] = {"mode": "from_transcript", "style": {"motion_look": "editorial"}}
    edl = validate_edl(edl, 4.0).model_dump()
    index = {"words": [{"w": w, "t0": s, "t1": e} for w, s, e in words]}
    return motion_captions.items(edl, index, Timeline(edl["keep"]))[0]


LEGIBLE = [
    ("hook_title", {"text": "Computer fonts / have been *garbage*"}),
    ("glow_title", {"text": "What am I / looking at?", "subline": "after effects // day 01"}),
    ("phrase_build", None),
    ("word_slam", {"text": "*32%* / fewer errors", "kicker": "out of the", "fit": "justify"}),
    ("marker_text", {"text": "You should be *required* / in medical school"}),
    ("counter", None),
    ("lower_third", {"name": "Elon Musk", "role": "on gamers in the operating room"}),
    ("checklist", None),
    ("quote_card", None),
    ("versus_split", None),
]


def _items():
    out = []
    for name, params in LEGIBLE:
        spec = motion_templates.spec(name)
        p = motion_templates.check_params(name, params or spec["example"])
        dur = float(spec.get("duration") or 3.0)
        out.append({"id": name, "template": name, "start": 0.0, "end": dur, "params": p})
    out.append(_caption_item())
    return out


def _frames(clip):
    import numpy as np
    r = subprocess.run([FFMPEG, "-v", "error", "-i", clip.path, "-f", "rawvideo",
                        "-pix_fmt", "rgba", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, clip.h, clip.w, 4).astype("int16")


def _changed(a, b):
    """Fraction of pixels (all frames) whose RGBA moved by more than 24 levels;
    1.0 when the clips do not even share a capture box. Headless Chromium's
    raster is not bit-exact between runs, so identity is 'below the noise'."""
    if (a.x, a.y, a.w, a.h, a.frames) != (b.x, b.y, b.w, b.h, b.frames):
        return 1.0
    return float((abs(_frames(a) - _frames(b)).max(axis=3) > 24).mean())


@needs_browser
def test_dark_plates_render_identically_and_bright_plates_firm_up(tmp_path, monkeypatch):
    """The contract every template keeps: a dark plate renders byte-for-byte
    what no plate renders; a bright plate changes the light type's backing."""
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    items = _items()
    W, H, fps = 270, 480, 10
    runs = {}
    for tag, probe in (("none", None), ("dark", _UniformProbe(25, W, H)),
                       ("bright", _UniformProbe(235, W, H))):
        plates = motion_layer.measure_plates(items, probe)
        if probe is not None:
            assert set(plates) == set(range(len(items)))
        runs[tag] = [motion_templates.build_job(it, W, H, fps, plate=plates.get(k))
                     for k, it in enumerate(items)]
    jobs = runs["none"] + runs["dark"] + runs["bright"]
    clips = motion_engine.render_jobs(jobs, str(tmp_path / "out"), pages=4)
    n = len(items)
    for k, it in enumerate(items):
        none, dark, bright = clips[k], clips[n + k], clips[2 * n + k]
        assert not none.empty, it["template"]
        assert _changed(none, dark) < 0.001, it["template"]
        assert _changed(none, bright) > 0.01, it["template"]


@needs_browser
def test_secondary_text_keeps_a_phone_readable_cap_height():
    cases = [
        ("word_slam", {"text": "*garbage*", "kicker": "the fonts have been just"}, ".kick"),
        ("lower_third", {"name": "Elon Musk", "role": "on gamers in the operating room"}, ".role"),
        ("lower_third", {"name": "Elon Musk", "role": "on gamers", "style": "glass_pill"}, ".role"),
        ("quote_card", {"text": "Stay *hungry*", "author": "Steve Jobs, 2005"}, ".by span:last-child"),
        ("counter", {"value": "40", "label": "*fonts* on one screen"}, ".label"),
        ("versus_split", {"left": "Tesla", "right": "Ford", "left_sub": "rockets, jets, medicine"}, ".s"),
        ("glow_title", {"text": "Hello", "subline": "after effects // day 01"}, "#sub"),
        ("stat_card", {"label": "Net revenue", "value": "$977", "caption": "vs. last year"}, ".cap"),
    ]
    for name, params, sel in cases:
        p = motion_templates.check_params(name, params)
        dur = float(motion_templates.spec(name).get("duration") or 3.0)
        job = motion_templates.build_job({"id": name, "template": name, "start": 0, "end": dur,
                                          "params": p}, 540, 960, 30)
        cap, overflow = asyncio.run(_eval(job, [
            f"(() => {{ const e = document.querySelector('{sel}');"
            " return parseFloat(getComputedStyle(e).fontSize) * MG.capRatio(e); })()",
            f"(() => {{ const r = document.querySelector('{sel}').getBoundingClientRect();"
            " return r.left < -1 || r.right > MG.W + 1; })()"], t=dur * 0.6))
        assert cap >= config_cap() - 0.5, (name, sel, cap)
        assert not overflow, (name, sel)


@needs_browser
def test_glass_pill_keeps_its_name_on_top_and_its_text_inside():
    """The floored role must not outgrow the name, and a role that wraps at
    the floor grows the pill instead of spilling out of it."""
    for role in ("CEO", "on gamers in the operating room"):
        p = motion_templates.check_params("lower_third", {"name": "Elon Musk", "role": role,
                                                          "style": "glass_pill"})
        job = motion_templates.build_job({"id": "lt", "template": "lower_third", "start": 0,
                                          "end": 3.5, "params": p}, 540, 960, 30)
        name_fs, role_fs, inside = asyncio.run(_eval(job, [
            "parseFloat(getComputedStyle(document.querySelector('.name')).fontSize)",
            "parseFloat(getComputedStyle(document.querySelector('.role')).fontSize)",
            "(() => { const p = document.querySelector('.plate').getBoundingClientRect();"
            " return ['.name', '.role'].every(s => { const r = document.querySelector(s).getBoundingClientRect();"
            " return r.top >= p.top + 4 && r.bottom <= p.bottom - 4; }); })()"], t=2.0))
        assert name_fs > role_fs * 1.1, (role, name_fs, role_fs)
        assert inside, role
