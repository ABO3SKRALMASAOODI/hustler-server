"""Browser-drawn motion captions: cue grammar, placement, defaults, pixels."""

import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agent_tools  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402


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
needs_browser = pytest.mark.skipif(not (CHROMIUM and shutil.which("ffmpeg")),
                                   reason="headless Chromium + ffmpeg required")

WORDS = [("I", 0.30, 0.37), ("wouldn't", 0.38, 0.52), ("say", 0.54, 0.76),
         ("for", 0.78, 0.92), ("sure", 0.94, 1.16), ("it", 1.34, 1.41),
         ("is", 1.42, 1.49), ("the", 1.50, 1.57), ("most", 1.58, 1.73),
         ("important,", 1.74, 2.12), ("but", 2.34, 2.50), ("it", 2.52, 2.60),
         ("could", 2.62, 2.84), ("be", 2.86, 3.00), ("the", 3.02, 3.09),
         ("most", 3.10, 3.33), ("important.", 3.34, 3.72),
         ("We", 4.50, 4.62), ("went", 4.64, 4.80), ("from", 4.82, 4.95),
         ("zero", 4.97, 5.25), ("to", 5.27, 5.36), ("$500,000", 5.38, 6.10),
         ("in", 6.12, 6.22), ("four", 6.24, 6.48), ("months.", 6.50, 7.00)]


def _setup(look, style=None, placement=None, dur=8.0, **caps):
    edl = default_edl(dur)
    st = {"motion_look": look}
    st.update(style or {})
    edl["captions"] = dict({"mode": "from_transcript", "style": st,
                            "emphasis_words": ["important"]}, **caps)
    if placement:
        edl["captions"]["placement_track"] = placement
    edl = validate_edl(edl, dur).model_dump()
    index = {"words": [{"w": w, "t0": s, "t1": e} for w, s, e in WORDS]}
    return edl, index, Timeline(edl["keep"])


# ── grammar ─────────────────────────────────────────────────────────────

def test_every_look_is_a_schema_value_and_a_template_enum():
    spec = motion_templates.spec("caption_motion")
    assert set(spec["params"]["look"]["values"]) == set(motion_captions.LOOKS)
    for look in motion_captions.LOOKS:
        edl, index, tl = _setup(look)
        assert motion_captions.look_of(edl) == look
        assert motion_captions.cues(edl, index, tl)
    assert {"editorial", "lockup"} <= set(motion_captions.LOOKS)


def test_cues_keep_every_word_flag_numbers_and_mark_swaps_vs_pauses():
    edl, index, tl = _setup("editorial")
    cues = motion_captions.cues(edl, index, tl)
    flat = [w["t"] for c in cues for w in c["w"]]
    assert len(flat) == len(WORDS)
    assert all(len(c["w"]) <= motion_captions.LOOKS["editorial"]["max_words"] for c in cues)
    xs = {w["t"] for c in cues for w in c["w"] if w["x"]}
    assert any("500" in t for t in xs) and any("important" in t.lower() for t in xs)
    # contiguous speech swaps hard (k=1); a real pause fades (k=0)
    for a, b in zip(cues, cues[1:]):
        assert a["e"] <= b["s"] + 1e-6
        assert a["k"] == (1 if b["s"] - a["e"] < motion_captions.CONTIGUOUS_S else 0)
    assert cues[-1]["k"] == 0
    assert all(c["b"] == "b" for c in cues)


def test_stored_max_words_narrows_but_never_widens_a_look():
    edl, index, tl = _setup("clean", max_words_per_caption=2)
    assert max(len(c["w"]) for c in motion_captions.cues(edl, index, tl)) <= 2
    edl, index, tl = _setup("lockup", max_words_per_caption=12)
    cap = motion_captions.LOOKS["lockup"]["max_words"]
    assert max(len(c["w"]) for c in motion_captions.cues(edl, index, tl)) <= cap


def test_placement_track_sets_anchor_and_band_per_cue():
    track = [{"t0": 0.0, "t1": 4.0, "position": "top", "anchor_y": 0.16},
             {"t0": 4.0, "t1": 8.0, "position": "middle", "anchor_y": 0.5}]
    edl, index, tl = _setup("lockup", placement=track)
    cues = motion_captions.cues(edl, index, tl)
    early = [c for c in cues if c["s"] < 3.8]
    late = [c for c in cues if c["s"] > 4.2]
    assert early and all(c["b"] == "t" and c["y"] == 0.16 for c in early)
    assert late and all(c["b"] == "m" and c["y"] == 0.5 for c in late)
    edl, index, tl = _setup("clean", style={"anchor_y": 0.3})
    assert {c["b"] for c in motion_captions.cues(edl, index, tl)} == {"t"}


def test_items_land_one_frame_early_and_segments_never_overlap(monkeypatch):
    monkeypatch.setattr(motion_captions, "SEGMENT_TARGET_S", 2.0)
    monkeypatch.setattr(motion_captions, "SEGMENT_MAX_S", 3.0)
    edl, index, tl = _setup("editorial")
    cues = motion_captions.cues(edl, index, tl)
    items = motion_captions.items(edl, index, tl)
    assert len(items) >= 2
    lead = motion_captions.CAPTION_LEAD_S
    first = items[0]["params"]["cues"][0]["w"][0]
    assert abs(items[0]["start"] + first["s"] - (cues[0]["w"][0]["s"] - lead)) < 0.002
    for a, b in zip(items, items[1:]):
        assert a["end"] <= b["start"] + 1e-6
    for it in items:
        assert it["template"] == "caption_motion" and it["layer"] == "below_captions"
        for c in it["params"]["cues"]:
            assert 0 <= c["s"] < c["e"] <= it["end"] - it["start"] + 1e-6
            assert all(c["s"] - 1e-6 <= w["s"] for w in c["w"])


def test_segments_split_where_the_placement_moves():
    track = [{"t0": 0.0, "t1": 4.0, "position": "top", "anchor_y": 0.16},
             {"t0": 4.0, "t1": 8.0, "position": "bottom", "anchor_y": 0.8}]
    edl, index, tl = _setup("clean", placement=track)
    items = motion_captions.items(edl, index, tl)
    assert len(items) >= 2
    for it in items:   # one capture box per placement band
        assert len({c["b"] for c in it["params"]["cues"]}) == 1


def test_style_params_map_fonts_animation_and_accent():
    edl, _i, _t = _setup("editorial", style={"font": "Inter Display Black",
                                             "animation": "none",
                                             "highlight_color": "#ed080d"})
    sp = motion_captions.style_params(edl)
    assert (sp["font"], sp["font_weight"]) == ("Inter Display", 900)
    assert sp["anim"] == "none" and sp["accent"] == "#ED080D"
    edl, _i, _t = _setup("clean", style={"font": "Montserrat"})
    sp = motion_captions.style_params(edl)
    assert (sp["font"], sp["font_weight"]) == ("Montserrat", 700)
    assert sp["anim"] == "auto" and sp["accent"] is None
    assert motion_captions._css_font("Comic Sans") == (None, None)


# ── defaults ────────────────────────────────────────────────────────────

class _BriefCtx:
    edit_plan = None

    def __init__(self, message, duration=30.0, words=80, speakers=1):
        self.user_message = message
        self.index = {"video": {"duration": duration, "width": 1920, "height": 1080},
                      "speakers": speakers,
                      "words": [{"w": "word", "t0": i * duration / words,
                                 "t1": i * duration / words + 0.1} for i in range(words)]}


def _vertical_edl(dur=30.0):
    edl = default_edl(dur)
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    return edl


def test_premium_short_form_briefs_default_to_motion_looks(monkeypatch):
    monkeypatch.setattr(agent_tools, "_motion_captions_on", lambda: True)
    look = lambda msg, **kw: agent_tools._direct_caption_style(  # noqa: E731
        _BriefCtx(msg, **kw), _vertical_edl())[0]
    assert look("make premium captions")["motion_look"] == "editorial"
    assert look("podcast clip")["motion_look"] == "editorial"
    assert look("viral reel")["motion_look"] == "editorial"
    assert look("two-person chat", speakers=2)["motion_look"] == "editorial"
    assert look("add captions")["motion_look"] == "editorial"      # vertical short-form
    assert look("simple captions please")["motion_look"] == "clean"
    assert look("high energy gym hype edit")["motion_look"] == "pop"
    assert look("calm cinematic travel film")["motion_look"] == "serif"
    # the libass preset stays as the fallback grammar
    assert look("podcast clip")["preset"] == "stacked"
    # explicit legacy / subtitle / news requests keep their presets untouched
    assert "motion_look" not in look("classic subtitles")
    assert "motion_look" not in look("closed captions for accessibility")
    assert "motion_look" not in look("news explainer")
    long_form, _ = agent_tools._direct_caption_style(
        _BriefCtx("add captions", duration=600.0, words=0), default_edl(600.0))
    assert long_form == {"preset": "documentary"}


def test_motion_default_needs_the_engine_and_honours_the_kill_switch(monkeypatch):
    monkeypatch.setattr(agent_tools, "_motion_captions_on", lambda: False)
    style, _ = agent_tools._direct_caption_style(_BriefCtx("podcast clip"), _vertical_edl())
    assert "motion_look" not in style
    monkeypatch.undo()
    monkeypatch.setenv("MOTION_CAPTIONS_DEFAULT", "0")
    assert agent_tools._motion_captions_on() is False


def test_director_preset_cast_maps_onto_the_motion_look():
    assert agent_tools._motion_look_for_preset("beast", "editorial") == "pop"
    assert agent_tools._motion_look_for_preset("documentary", "editorial") is None
    assert agent_tools._motion_look_for_preset("reels", "editorial") == "editorial"


class _ToolCtx:
    duration = 8.0
    user_message = "premium podcast captions"
    edit_plan = None

    def __init__(self, edl=None):
        self.index = {"video": {"duration": 8.0, "width": 1080, "height": 1920},
                      "words": [{"w": w, "t0": s, "t1": e} for w, s, e in WORDS]}
        self.edl = edl or default_edl(8.0)

    def latest_edl(self):
        return {"version": 1, "json": self.edl}

    def write_edl(self, edl, _summary):
        self.edl = edl
        return "EDL v1 -> v2"


def test_add_captions_with_a_motion_look_keeps_phrases_and_emphasis():
    ctx = _ToolCtx()
    out = agent_tools.add_captions(ctx, mode="from_transcript",
                                   style={"motion_look": "editorial"})
    assert out.startswith("EDL v1 -> v2"), out
    caps = ctx.edl["captions"]
    assert caps["style"]["motion_look"] == "editorial"
    assert caps["max_words_per_caption"] is None        # the look owns phrasing
    assert caps.get("emphasis_words")                  # hierarchy auto-selected
    edl = validate_edl(ctx.edl, 8.0).model_dump()
    cues = motion_captions.cues(edl, ctx.index, Timeline(edl["keep"]))
    assert max(len(c["w"]) for c in cues) > 2


def test_switching_on_a_motion_look_lifts_the_stacked_two_word_default():
    edl = default_edl(8.0)
    edl["captions"] = {"mode": "from_transcript", "max_words_per_caption": 2,
                       "style": {"preset": "stacked"}}
    ctx = _ToolCtx(edl)
    out = agent_tools.set_caption_style(ctx, {"motion_look": "clean"})
    assert out.startswith("EDL v1 -> v2"), out
    assert ctx.edl["captions"]["max_words_per_caption"] is None
    assert "own grammar" in out
    # a later restyle keeps whatever phrasing is stored
    ctx.edl["captions"]["max_words_per_caption"] = 3
    agent_tools.set_caption_style(ctx, {"motion_look": "editorial"})
    assert ctx.edl["captions"]["max_words_per_caption"] == 3


# ── pixels ──────────────────────────────────────────────────────────────

def _jobs_for(look, size=(540, 960), style=None, placement=None):
    edl, index, tl = _setup(look, style=style, placement=placement)
    items = motion_captions.items(edl, index, tl)
    return items, [motion_templates.build_job(it, size[0], size[1], 30) for it in items]


async def _dom(job, times, script):
    """Seek the real composition and evaluate `script` (a JS arrow fn) at each
    time, after fonts and layout are ready — geometry without shadow spill."""
    from playwright.async_api import async_playwright
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
            out.append(await page.evaluate(script))
        errs = await page.evaluate("window.__mgErrors")
        await browser.close()
    assert not errs, errs
    return out


_BLOCK = """() => Array.from(document.querySelectorAll('.cue.on .blk')).map(b => {
    const r = b.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })"""


@needs_browser
@pytest.mark.parametrize("size", [(540, 960), (540, 540), (960, 540)])
def test_every_look_renders_inside_the_safe_area(size, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    dw, dh = motion_engine.design_size(*size)
    if dh / dw >= 1.6:
        x0, x1, y0, y1 = 60, dw - 60, 0.08 * dh, 0.80 * dh   # platform UI band
    else:
        x0, x1, y0, y1 = 0.05 * dw, 0.95 * dw, 0.05 * dh, 0.93 * dh
    jobs, times = [], []
    for look in motion_captions.LOOKS:
        items, js = _jobs_for(look, size)
        jobs.append(js[0])
        # just after each cue's last word lands (whole block visible)
        times.append([round(c["w"][-1]["s"] + 0.2, 3) for c in items[0]["params"]["cues"]][:5])
    reps = motion_engine.probe(jobs, times)
    for look, job, rep, ts in zip(motion_captions.LOOKS, jobs, reps, times):
        assert not rep["errors"], (look, rep)
        assert rep["visible_frames"] == len(ts), (look, rep)
        for blocks in asyncio.run(_dom(job, ts, _BLOCK)):
            assert len(blocks) == 1, (look, blocks)
            bx0, by0, bx1, by1 = blocks[0]
            assert bx0 >= x0 - 1 and bx1 <= x1 + 1, (look, size, blocks)
            assert by0 >= y0 - 1 and by1 <= y1 + 1, (look, size, blocks)


@needs_browser
def test_tall_looks_stay_inside_their_measured_band(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    track = [{"t0": 0.0, "t1": 8.0, "position": "top", "anchor_y": 0.16}]
    for look in ("lockup", "stack", "editorial", "clean"):
        items, jobs = _jobs_for(look, placement=track)
        ts = [round(c["w"][-1]["s"] + 0.2, 3) for c in items[0]["params"]["cues"]]
        for blocks in asyncio.run(_dom(jobs[0], ts, _BLOCK)):
            # the top band ends at 0.31 of the frame: the face below stays clear
            assert blocks and blocks[0][3] <= 0.31 * 1920 + 1, (look, blocks)
            assert blocks[0][1] >= 0.08 * 1920 - 1, (look, blocks)


async def _word_states(job, times):
    from playwright.async_api import async_playwright
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
            out.append(await page.evaluate("""() => Array.from(document.querySelectorAll('.cue.on .mg-w'))
                .map(e => { const r = e.getBoundingClientRect();
                            return [e.textContent, +getComputedStyle(e).opacity, Math.round(r.left), Math.round(r.top)]; })"""))
        out.append(await page.evaluate("document.querySelectorAll('.cue.on').length"))
        await browser.close()
    return out


@needs_browser
@pytest.mark.parametrize("look", ["clean", "editorial", "lockup"])
def test_words_reveal_in_place_and_animation_none_is_a_hard_pop(look, tmp_path):
    for anim in ("auto", "none"):
        items, jobs = _jobs_for(look, style={"animation": "none"} if anim == "none" else None)
        # a word that starts well after its predecessor, inside one cue
        cue, k = next((c, k) for c in items[0]["params"]["cues"]
                      for k in range(1, len(c["w"]))
                      if c["w"][k]["s"] - c["w"][k - 1]["s"] >= 0.25
                      and c["e"] - c["w"][k]["s"] > 0.4)
        wb = cue["w"][k]
        f = 1 / 30
        times = [wb["s"] - 3 * f, wb["s"] + f, wb["s"] + 0.3]
        before, onset, after, live = asyncio.run(_word_states(jobs[0], times))
        assert live == 1
        # no reflow: every word keeps its position while others appear
        assert [r[0] for r in before] == [r[0] for r in after]
        assert all(abs(p[2] - q[2]) <= 1 and abs(p[3] - q[3]) <= 1
                   for p, q in zip(before, after)), (before, after)
        by_text = {r[0]: r for r in before}
        prev_text, text = cue["w"][k - 1]["t"], cue["w"][k]["t"]
        if look == "lockup":
            prev_text, text = prev_text.upper(), text.upper()
        prev_text, text = prev_text.rstrip(",;:"), text.rstrip(",;:")
        assert by_text[prev_text][1] == 1.0 and by_text[text][1] == 0.0, before
        assert {r[0]: r for r in after}[text][1] == 1.0, after
        if anim == "none":
            assert {r[0]: r for r in onset}[text][1] == 1.0, onset


def _frames(path, out_dir):
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    subprocess.run([shutil.which("ffmpeg"), "-v", "error", "-y", "-i", path,
                    os.path.join(out_dir, "%04d.png")], check=True)
    return sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir))


def _changed_pixels(pa, pb):
    """Pixels that differ visibly (Chrome's partial raster can move one
    anti-aliased edge column; a dropped caption change moves thousands)."""
    from PIL import Image, ImageChops
    with Image.open(pa) as a, Image.open(pb) as b:
        d = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
        bands = d.split()
        worst = bands[0]
        for band in bands[1:]:
            worst = ImageChops.lighter(worst, band)
        return worst.point([0] * 41 + [1] * 215).histogram()[1]


@needs_browser
@pytest.mark.parametrize("anim", ["auto", "none"])
def test_static_frame_reuse_never_drops_a_caption_change(anim, tmp_path, monkeypatch):
    """Frames outside the declared motion windows are re-used, so every
    visual change must sit inside a window: the windowed render has to be
    pixel-identical to rendering every frame."""
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    style = {"animation": "none"} if anim == "none" else None
    jobs = []
    for look in motion_captions.LOOKS:
        edl, index, tl = _setup(look, style=style)
        item = motion_captions.items(edl, index, tl)[0]
        item = dict(item, end=item["start"] + 3.6)       # swaps, pauses, emphasis
        job = motion_templates.build_job(item, 270, 480, 30)
        jobs += [job, motion_engine.RenderJob(**dict(job.__dict__, html=job.html.replace(
            "</body>", "<script>MG.always();</script></body>")))]
    clips = motion_engine.render_jobs(jobs, str(tmp_path / "clips"), pages=4)
    for k, look in enumerate(motion_captions.LOOKS):
        a, b = clips[2 * k], clips[2 * k + 1]
        assert a.captured < b.captured, look
        assert (a.x, a.y, a.w, a.h) == (b.x, b.y, b.w, b.h)
        fa = _frames(a.path, str(tmp_path / f"{look}_a"))
        fb = _frames(b.path, str(tmp_path / f"{look}_b"))
        assert len(fa) == len(fb)
        bad = [(i, n) for i, (x, y) in enumerate(zip(fa, fb))
               if (n := _changed_pixels(x, y)) > 40]
        assert not bad, (look, anim, bad[:10])


def _cast_ctx():
    import spatial
    ctx = object.__new__(agent_tools.ToolContext)
    ctx.index = {"video": {"duration": 10, "width": 1080, "height": 1920},
                 "words": [{"w": w, "t0": i, "t1": i + .5} for i, w in enumerate(
                     ("this", "measured", "proof", "deserves", "quiet", "type"))],
                 "shots": [], "speakers": 1}
    ctx.duration = 10
    ctx.has_main_video = True
    ctx.edit_plan = {"steps": ["caption the proof"]}
    ctx.user_message = "make the typography feel considered"
    ctx.editing_metrics = {}
    ctx._last_caption_cast = None
    ctx._spatial = {"v": spatial.SPATIAL_VERSION, "samples": []}
    ctx._perception = {"vb_env": []}
    ctx.latest_edl = lambda: {"version": 1, "json": {"keep": [[0, 10]], "inserts": [], "speed": []}}
    ctx.written = None

    def write(edl, _desc):
        ctx.written = edl
        return "EDL v1 -> v2: captions"
    ctx.write_edl = write
    return ctx


@pytest.mark.parametrize("preset,look", [("beast", "pop"), ("reels", "editorial"),
                                         ("documentary", None)])
def test_director_cast_renders_through_the_matching_motion_look(preset, look, monkeypatch):
    monkeypatch.setattr(agent_tools, "_motion_captions_on", lambda: True)
    monkeypatch.setattr(agent_tools.caption_judge, "review", lambda context: {
        "preset": preset, "style": {"preset": preset}, "confidence": .9,
        "reason": "fits the brief", "rejected": []})
    ctx = _cast_ctx()
    out = agent_tools.add_captions(ctx)
    style = ctx.written["captions"]["style"]
    assert style["preset"] == preset
    assert style.get("motion_look") == look
    if look:
        assert f"motion caption look '{look}'" in out
        assert ctx.written["captions"]["max_words_per_caption"] is None
