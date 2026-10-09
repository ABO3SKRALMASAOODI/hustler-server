"""Data/number motion templates: specs, sound cues, landed values, every enum."""

import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import sfx_kit  # noqa: E402

DATA = ["counter", "bar_compare", "line_chart", "stat_card", "progress_ring", "timeline_steps"]


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


def _job(name, params, size=(1080, 1920)):
    spec = motion_templates.spec(name)
    item = {"id": name, "template": name, "start": 0.0, "end": float(spec["duration"]),
            "params": motion_templates.check_params(name, params)}
    return motion_templates.build_job(item, size[0], size[1], 30)


def test_data_templates_have_complete_specs_and_kit_cues():
    for name in DATA:
        spec = motion_templates.spec(name)
        assert spec["category"] == "data", name
        assert spec.get("description") and spec.get("example"), name
        params = motion_templates.check_params(name, spec["example"])
        for cue in spec["sfx"]:
            assert cue["kind"] in sfx_kit.KINDS, (name, cue)
            rep = cue.get("repeat")
            if rep:
                assert spec["params"][rep["param"]]["type"] in ("list", "rows"), (name, rep)
        cues = motion_tools._sfx_cues(spec, params, 0.0, float(spec["duration"]))
        assert cues and all(0.0 <= t <= spec["duration"] for t, _, _ in cues), name


def test_timeline_pops_land_on_each_point_beat():
    spec = motion_templates.spec("timeline_steps")
    params = motion_templates.check_params("timeline_steps", {"rows": [
        {"label": "1983"}, {"label": "1997"}, {"label": "2007"}, {"label": "2024"}]})
    pops = [t for t, kind, _ in motion_tools._sfx_cues(spec, params, 10.0, 13.5) if kind == "pop_soft"]
    assert pops == [10.2, 10.46, 10.72, 10.98]


async def _texts(jobs_and_queries, t_frac=0.92):
    """Load each job like the engine does, seek near the end, read DOM text."""
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for job, selector in jobs_and_queries:
                dw, dh = motion_engine.design_size(job.out_w, job.out_h)
                ctx = await browser.new_context(viewport={"width": dw, "height": dh})
                try:
                    await ctx.route("**/*", await motion_engine._route_factory(job))
                    page = await ctx.new_page()
                    await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
                    await page.evaluate("async () => { await document.fonts.ready;"
                                        " if (window.MG && MG.ready) await MG.ready; }")
                    await page.evaluate("t => window.__mgSeek(t)", job.duration * t_frac)
                    out.append(await page.evaluate(
                        "s => { const e = document.querySelector(s); return e ? e.textContent : null; }",
                        selector))
                    assert not await page.evaluate("window.__mgErrors"), job.label
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


@needs_browser
def test_numbers_land_exactly_as_written():
    cases = [("62,000+", "62,000+"), ("$1.2B", "$1.2B"), ("4.9", "4.9"), ("1M", "1M"),
             ("62000", "62,000"), ("€3,5", "€3,5"), ("1 000 000", "1 000 000"),
             ("-12%", "-12%"), ("N/A", "N/A")]
    jobs = [(_job("counter", {"value": v, "glow": 0}), ".num:not(.glow)") for v, _ in cases]
    jobs.append((_job("stat_card", {"label": "Net revenue", "value": "$977,424", "delta": "+340%"}), ".val"))
    jobs.append((_job("progress_ring", {"percent": 99.9}), ".num"))
    got = asyncio.run(_texts(jobs))
    assert got[:len(cases)] == [want for _, want in cases]
    assert got[len(cases)] == "$977,424"
    assert got[len(cases) + 1] == "99.9%"


@needs_browser
def test_every_enum_value_and_aspect_renders():
    jobs, times = [], []
    for name in DATA:
        spec = motion_templates.spec(name)
        ex = spec["example"]
        variants = [dict(ex)]
        for key, p in spec["params"].items():
            if p.get("type") == "enum":
                variants += [dict(ex, **{key: v}) for v in p["values"] if v != p.get("default")]
        dur = float(spec["duration"])
        for params in variants:
            jobs.append(_job(name, params, (540, 960)))
            times.append([0.05, dur * 0.5, dur * 0.9])
        for size in ((1080, 1080), (1920, 1080)):
            jobs.append(_job(name, ex, size))
            times.append([0.05, dur * 0.5, dur * 0.9])
    reports = motion_engine.probe(jobs, times, budget_s=300)
    for job, rep in zip(jobs, reports):
        assert not rep["errors"], (job.label, rep["errors"])
        assert rep["visible_frames"] == 3, (job.label, rep)
