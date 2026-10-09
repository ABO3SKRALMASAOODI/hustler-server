"""Callout motion templates: arrow, circle, marker text, lower third, emoji,
checklist and versus. Specs, sound cues and real-Chromium renders."""

import asyncio
import os
import re
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import sfx_kit  # noqa: E402

CALLOUTS = ["arrow_callout", "circle_highlight", "marker_text", "lower_third",
            "emoji_pop", "checklist", "versus_split"]


def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(args=motion_engine.CHROME_ARGS)
            b.close()
        return True
    except Exception:
        return False


CHROMIUM = _chromium_ok()
needs_browser = pytest.mark.skipif(not (CHROMIUM and shutil.which("ffmpeg")),
                                   reason="headless Chromium + ffmpeg required")


def test_callout_specs_are_complete():
    for name in CALLOUTS:
        spec = motion_templates.spec(name)
        assert spec["category"] == "callout", name
        assert spec.get("description") and spec.get("duration"), name
        motion_templates.check_params(name, spec["example"])
        assert spec["sfx"], f"{name} has no sound cue"
        for cue in spec["sfx"]:
            assert cue["kind"] in sfx_kit.KINDS, (name, cue)
    # aiming templates must be told where to aim
    for name, keys in (("arrow_callout", ("target_x", "target_y")), ("circle_highlight", ("cx", "cy"))):
        for k in keys:
            assert motion_templates.spec(name)["params"][k].get("required"), (name, k)
    assert motion_templates.spec("marker_text")["mutes_captions"] is True
    # the circle's note is white by default (the loop keeps the accent colour)
    circle = motion_templates.spec("circle_highlight")["params"]
    assert circle["label_color"]["default"].upper() == "#FFFFFF"
    assert circle["color"]["default"].upper() != "#FFFFFF"


def test_checklist_ticks_follow_each_rows_time():
    spec = motion_templates.spec("checklist")
    params = motion_templates.check_params("checklist", {"items": [
        {"text": "One", "at": "0.5"}, {"text": "Two"}, {"text": "Three", "at": "2,25"}]})
    cues = motion_tools._sfx_cues(spec, params, 10.0, 14.0)
    assert [(t, k) for t, k, _ in cues] == [(10.62, "tick"), (10.87, "tick"), (12.37, "tick")]
    # no 'at' anywhere: an even cadence that matches the template's default spacing
    params = motion_templates.check_params("checklist", spec["example"])
    assert [t for t, _, _ in motion_tools._sfx_cues(spec, params, 0.0, 4.0)] == [0.32, 0.87, 1.42]
    # times are read like the template's parseFloat: '1.2s' is 1.2, junk falls back to the cadence
    params = motion_templates.check_params("checklist", {"items": [
        {"text": "One", "at": "1.2s"}, {"text": "Two", "at": " 2"}, {"text": "Three", "at": "soon"}]})
    assert [t for t, _, _ in motion_tools._sfx_cues(spec, params, 0.0, 4.0)] == [1.32, 2.12, 1.42]


def test_checklist_cadence_fits_short_items_and_skips_blank_rows():
    spec = motion_templates.spec("checklist")
    # 1.2 s item, 3 rows: the cadence tightens so every row (and its tick) lands
    params = motion_templates.check_params("checklist", spec["example"])
    assert [t for t, _, _ in motion_tools._sfx_cues(spec, params, 0.0, 1.2)] == [0.32, 0.54, 0.76]
    # 2 s item, 5 rows: none dropped
    rows = {"items": [{"text": f"Row {i}"} for i in range(5)]}
    cues = motion_tools._sfx_cues(spec, motion_templates.check_params("checklist", rows), 0.0, 2.0)
    assert len(cues) == 5 and cues[-1][0] < 2.0 - 0.6
    # a blank row is dropped by the template, so it gets no tick and no slot
    params = motion_templates.check_params("checklist", {"items": [
        {"text": "One"}, {"text": " "}, {"text": "Three"}]})
    assert [t for t, _, _ in motion_tools._sfx_cues(spec, params, 0.0, 4.0)] == [0.32, 0.87]


def test_js_float_matches_parsefloat():
    f = motion_tools._js_float
    assert [f("1.2s"), f("2,25"), f(".5"), f(" 3"), f("-1"), f(1.5), f("1e1")] == [1.2, 2.25, 0.5, 3.0, -1.0, 1.5, 10.0]
    assert [f(""), f(None), f("soon"), f("Infinity"), f(True)] == [None] * 5


def test_emoji_pop_sounds_once_per_emoji():
    spec = motion_templates.spec("emoji_pop")
    params = motion_templates.check_params("emoji_pop", {"emoji": ["🔥", "💸", "🚀"]})
    assert [(t, k) for t, k, _ in motion_tools._sfx_cues(spec, params, 2.0, 4.0)] == [
        (2.04, "pop_bright"), (2.16, "pop_bright"), (2.28, "pop_bright")]


VARIANTS = [
    ("arrow_callout", {"label": "this is the trick", "target_x": 0.62, "target_y": 0.4}),
    ("arrow_callout", {"label": "a long label that has to wrap", "target_x": 0.05, "target_y": 0.05, "font": "sans"}),
    ("arrow_callout", {"label": "here", "target_x": 0.95, "target_y": 0.95, "side": "below", "font": "serif"}),
    ("arrow_callout", {"label": "him", "target_x": 0.5, "target_y": 0.5, "side": "right", "size": 1.5}),
    ("arrow_callout", {"label": "look up here", "target_x": 0.5, "target_y": 0.1, "side": "above"}),
    ("circle_highlight", {"cx": 0.5, "cy": 0.42, "label": "watch this", "style": "double"}),
    ("circle_highlight", {"cx": 0.97, "cy": 0.03, "w": 0.9, "h": 0.6, "label": "too big", "label_side": "left", "glow": False}),
    ("circle_highlight", {"cx": 0.2, "cy": 0.8, "w": 0.04, "h": 0.02, "label_side": "right"}),
    ("marker_text", {"text": "Most people *quit* right before it works"}),
    ("marker_text", {"text": "*Build* / something / *people want*", "style": "box", "uppercase": True}),
    ("marker_text", {"text": "No stars here, but a rather long line that has to wrap onto three lines somehow",
                     "style": "underline", "font": "serif"}),
    ("marker_text", {"text": "*ten* times", "font": "condensed", "y": 0.1}),
    ("lower_third", {"name": "Peter Thiel", "role": "Co-founder, PayPal"}),
    ("lower_third", {"name": "Alexandria Ocasio-Cortez", "role": "U.S. Representative, New York 14th",
                     "style": "glass_pill", "side": "right"}),
    ("lower_third", {"name": "Elon Musk", "style": "minimal", "y": 0.8, "size": 1.4}),
    ("emoji_pop", {"emoji": ["🤯"]}),
    ("emoji_pop", {"emoji": ["💸", "📉", "?!"], "x": 0.08, "y": 0.82, "burst": False}),
    ("checklist", {"items": [{"text": "Talk to users"}, {"text": "Ship"}], "panel": False}),
    ("checklist", {"items": [{"text": "A row that is quite long indeed for a list", "at": "0.1"}] * 5,
                   "title": "Five myths", "mark": "cross", "strike": True, "y": 0.8}),
    ("versus_split", {"left": "Tesla", "right": "Ford", "left_sub": "$800B", "right_sub": "$45B"}),
    ("versus_split", {"left": "Independent media", "right": "TV", "tint": False, "y": 0.15}),
    ("versus_split", {"left": "Startup", "right": "Big Tech", "left_sub": "3 people", "right_sub": "30,000"}),
    ("checklist", {"items": [{"text": "Write down the one metric that matters"},
                             {"text": "Talk to ten customers every week"}, {"text": "Cut meetings"}],
                   "strike": True}),
    ("marker_text", {"text": "Make something *people want* and the rest follows"}),
]


@needs_browser
def test_callout_variants_render_without_errors():
    jobs, times = [], []
    for name, params in VARIANTS:
        clean = motion_templates.check_params(name, params)
        dur = float(motion_templates.spec(name)["duration"])
        item = {"id": name, "template": name, "start": 0, "end": dur, "params": clean}
        jobs.append(motion_templates.build_job(item, 540, 960, 30))
        times.append([0.12, dur * 0.5, dur - 0.4])
    reports = motion_engine.probe(jobs, times)
    for (name, params), rep in zip(VARIANTS, reports):
        assert not rep["errors"], (name, params, rep["errors"])
        assert rep["visible_frames"] >= 2, (name, params, rep)
        for bb in rep["bboxes"]:
            # never spill past the frame (fractions, quarter-scale probe)
            assert -0.01 <= bb[0] <= bb[2] <= 1.01 and -0.01 <= bb[1] <= bb[3] <= 1.01, (name, bb)


@needs_browser
def test_callouts_survive_short_long_and_landscape_items():
    jobs, times, labels = [], [], []
    for name in CALLOUTS:
        params = motion_templates.check_params(name, motion_templates.spec(name)["example"])
        for dur, size in ((0.8, (540, 960)), (8.0, (540, 960)), (3.0, (960, 540)), (3.0, (720, 720))):
            item = {"id": name, "template": name, "start": 0, "end": dur, "params": params}
            jobs.append(motion_templates.build_job(item, size[0], size[1], 30))
            times.append([dur * 0.5, dur - 0.1])
            labels.append((name, dur, size))
    reports = motion_engine.probe(jobs, times)
    for label, rep in zip(labels, reports):
        assert not rep["errors"], (label, rep["errors"])
        assert rep["visible_frames"] >= 1, (label, rep)


@needs_browser
def test_arrow_and_circle_land_on_the_requested_spot():
    cases = [("arrow_callout", {"label": "this", "target_x": 0.7, "target_y": 0.3}, (0.7, 0.3)),
             ("arrow_callout", {"label": "that", "target_x": 0.2, "target_y": 0.66, "side": "above"}, (0.2, 0.66)),
             ("circle_highlight", {"cx": 0.3, "cy": 0.55, "w": 0.2, "h": 0.08}, (0.3, 0.55))]
    jobs = []
    for name, params, _ in cases:
        item = {"id": name, "template": name, "start": 0, "end": 2.4,
                "params": motion_templates.check_params(name, params)}
        jobs.append(motion_templates.build_job(item, 540, 960, 30))
    reports = motion_engine.probe(jobs, [[1.2]] * len(jobs))
    for (name, params, (x, y)), rep in zip(cases, reports):
        assert not rep["errors"] and rep["bboxes"], (name, rep)
        x0, y0, x1, y1 = rep["bboxes"][0]
        assert x0 - 0.03 <= x <= x1 + 0.03 and y0 - 0.03 <= y <= y1 + 0.03, (name, (x, y), rep["bboxes"][0])


async def _eval_all(cases):
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for name, params, t, expr in cases:
                clean = motion_templates.check_params(name, params)
                dur = float(motion_templates.spec(name)["duration"])
                item = {"id": name, "template": name, "start": 0, "end": dur, "params": clean}
                job = motion_templates.build_job(item, 1080, 1920, 30)
                ctx = await browser.new_context(viewport={"width": 1080, "height": 1920})
                try:
                    await ctx.route("**/*", await motion_engine._route_factory(job))
                    page = await ctx.new_page()
                    await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
                    await page.evaluate("async () => { await document.fonts.ready; if (MG.ready) await MG.ready; }")
                    await page.evaluate("t => window.__mgSeek(t)", float(t))
                    out.append((await page.evaluate(expr), await page.evaluate("window.__mgErrors || []")))
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


ARROW_GEOM = """() => {
  const l = document.querySelector('#lab');
  return {box: [l.offsetLeft, l.offsetTop, l.offsetLeft + l.offsetWidth, l.offsetTop + l.offsetHeight],
          d: document.querySelector('#shaft').getAttribute('d') || ''};
}"""


@needs_browser
def test_arrow_never_runs_through_its_label():
    """An explicit side that the frame edge cannot honour falls back to a side that
    works, and the shaft always leaves from the label edge facing the target."""
    cases = [{"label": "look up here", "target_x": 0.5, "target_y": 0.1, "side": "above"},
             {"label": "here", "target_x": 0.95, "target_y": 0.95, "side": "below", "font": "serif"},
             {"label": "the mug", "target_x": 0.62, "target_y": 0.78, "side": "below"},
             {"label": "the mug", "target_x": 0.12, "target_y": 0.5, "side": "left"},
             {"label": "logo", "target_x": 0.9, "target_y": 0.12, "side": "right", "font": "sans"},
             {"label": "this is the trick", "target_x": 0.62, "target_y": 0.4}]
    res = asyncio.run(_eval_all([("arrow_callout", c, 1.2, ARROW_GEOM) for c in cases]))
    for params, (geom, errors) in zip(cases, res):
        assert not errors, (params, errors)
        x0, y0, x1, y1 = geom["box"]
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", geom["d"])]
        pts = list(zip(nums[0::2], nums[1::2]))
        assert len(pts) > 20, (params, geom)
        inside = [p for p in pts if x0 + 4 < p[0] < x1 - 4 and y0 + 4 < p[1] < y1 - 4]
        assert not inside, (params, geom["box"], inside[:3])
        # and it still ends at the target
        tx, ty = params["target_x"] * 1080, params["target_y"] * 1920
        assert min((px - tx) ** 2 + (py - ty) ** 2 for px, py in pts) ** 0.5 < 40, (params, geom["box"])


@needs_browser
def test_layout_keeps_short_names_and_starred_runs_whole():
    lines_of = """sel => Array.from(document.querySelectorAll(sel)).map(e => {
        const r = document.createRange(); r.selectNodeContents(e);
        return new Set(Array.from(r.getClientRects()).map(b => Math.round(b.top))).size; })"""
    run_lines = """() => { const ls = Array.from(document.querySelectorAll('.line'));
        return Array.from(document.querySelectorAll('.line > .mg-w'))
          .filter(w => /^(people|want|ten|times)$/i.test(w.firstChild.textContent))
          .map(w => ls.indexOf(w.parentElement)); }"""
    cases = [("versus_split", {"left": "Startup", "right": "Big Tech"}, 1.2, f"() => ({lines_of})('.lab .t')"),
             ("versus_split", {"left": "Independent media", "right": "TV"}, 1.2, f"() => ({lines_of})('.lab .t')"),
             ("marker_text", {"text": "Make something *people want* and the rest follows"}, 1.2, run_lines),
             ("marker_text", {"text": "You need *ten times* better than everyone else", "font": "condensed"}, 1.2, run_lines)]
    (vs1, e1), (vs2, e2), (m1, e3), (m2, e4) = asyncio.run(_eval_all(cases))
    assert not (e1 or e2 or e3 or e4)
    assert vs1 == [1, 1], vs1          # 'Big Tech' is not broken into 'Big' / 'Tech'
    assert vs2 == [2, 1], vs2          # a long name still takes two balanced lines
    assert len(m1) == 2 and len(set(m1)) == 1, m1   # 'people want' stays on one line
    assert len(m2) == 2 and len(set(m2)) == 1, m2
