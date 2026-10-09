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
import sound_library  # noqa: E402

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


def _job(name, params, size=(1080, 1920), duration=None):
    spec = motion_templates.spec(name)
    item = {"id": name, "template": name, "start": 0.0, "end": float(duration or spec["duration"]),
            "params": motion_templates.check_params(name, params)}
    return motion_templates.build_job(item, size[0], size[1], 30)


def test_data_templates_have_complete_specs_and_library_cues():
    for name in DATA:
        spec = motion_templates.spec(name)
        assert spec["category"] == "data", name
        assert spec.get("description") and spec.get("example"), name
        params = motion_templates.check_params(name, spec["example"])
        for cue in spec["sfx"]:
            assert sound_library.catalog(cue["kind"]), (name, cue)
            rep = cue.get("repeat")
            if rep:
                assert spec["params"][rep["param"]]["type"] in ("list", "rows"), (name, rep)
        cues = motion_tools._sfx_cues(spec, params, 0.0, float(spec["duration"]))
        assert cues and all(0.0 <= t <= spec["duration"] for t, _, _ in cues), name


def test_timeline_pops_land_on_each_point_beat():
    spec = motion_templates.spec("timeline_steps")
    params = motion_templates.check_params("timeline_steps", {"rows": [
        {"label": "1983"}, {"label": "1997"}, {"label": "2007"}, {"label": "2024"}]})
    pops = [t for t, kind, _ in motion_tools._sfx_cues(spec, params, 10.0, 13.5) if kind == "pop_1"]
    assert pops == [10.2, 10.46, 10.72, 10.98]


async def _texts(jobs_and_queries, t_frac=0.92):
    """Load each job like the engine does, seek near the end, read DOM text
    (every match of the selector, joined with ' | ')."""
    return [r[0] for r in await _probe([(job, sel, [job.duration * t_frac]) for job, sel in jobs_and_queries])]


async def _probe(jobs_selectors_times, script=None):
    """For each (job, selector, times): seek to every time and evaluate
    `script` with the selector (default: joined textContent of its matches)."""
    from playwright.async_api import async_playwright
    script = script or "s => [...document.querySelectorAll(s)].map(e => e.textContent).join(' | ')"
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for job, selector, times in jobs_selectors_times:
                dw, dh = motion_engine.design_size(job.out_w, job.out_h)
                ctx = await browser.new_context(viewport={"width": dw, "height": dh})
                try:
                    await ctx.route("**/*", await motion_engine._route_factory(job))
                    page = await ctx.new_page()
                    await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
                    await page.evaluate("async () => { await document.fonts.ready;"
                                        " if (window.MG && MG.ready) await MG.ready; }")
                    got = []
                    for t in times:
                        await page.evaluate("t => window.__mgSeek(t)", t)
                        got.append(await page.evaluate(script, selector))
                    out.append(got)
                    assert not await page.evaluate("window.__mgErrors"), job.label
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


@needs_browser
def test_numbers_land_exactly_as_written():
    # negatives are typeset with a real minus sign (U+2212), never a hyphen
    cases = [("62,000+", "62,000+"), ("$1.2B", "$1.2B"), ("4.9", "4.9"), ("1M", "1M"),
             ("62000", "62,000"), ("€3,5", "€3,5"), ("1 000 000", "1 000 000"),
             ("-12%", "−12%"), ("N/A", "N/A")]
    jobs = [(_job("counter", {"value": v, "glow": 0}), ".num:not(.glow)") for v, _ in cases]
    bars = {"rows": [{"label": "a", "value": "$8.50"}, {"label": "b", "value": "$25", "highlight": "1"},
                     {"label": "c", "value": "41 days"}, {"label": "d", "value": "9.9"},
                     {"label": "e", "value": "1.25M"}]}
    tail = []
    # every data template shows its exact figures however short the editor made it
    for dur in (None, 1.0):
        tail += [
            (_job("stat_card", {"label": "Net revenue", "value": "$977,424", "delta": "+340%"}, duration=dur),
             ".val", "$977,424"),
            (_job("progress_ring", {"percent": 99.9}, duration=dur), ".num", "99.9%"),
            (_job("bar_compare", bars, duration=dur), ".val", "$8.50 | $25 | 41 days | 9.9 | 1.25M"),
            (_job("line_chart", {"values": ["12", "30", "61"], "value_label": "$61K"}, duration=dur),
             ".tag", "$61K"),
            (_job("counter", {"value": "62,000+", "glow": 0}, duration=dur), ".num:not(.glow)", "62,000+"),
        ]
    got = asyncio.run(_texts(jobs + [(j, s) for j, s, _ in tail]))
    assert got[:len(cases)] == [want for _, want in cases]
    for (job, _sel, want), text in zip(tail, got[len(cases):]):
        assert text == want, (job.label, job.duration, text)


_DRUM_READING = """() => {
  const cols = [...document.querySelectorAll('.num:not(.glow) .col')];
  const digits = cols.map(c => {
    let best = '', bd = 1e9;
    for (const d of c.querySelectorAll('.dg')) {
      if (d.style.opacity === '0') continue;
      const m = /translateY\\((-?[\\d.]+)px\\)/.exec(d.style.transform);
      const y = Math.abs(m ? parseFloat(m[1]) : 0);
      if (y < bd) { bd = y; best = d.textContent; }
    }
    return best;
  });
  const clipped = cols.every(c => getComputedStyle(c.querySelector('.win')).overflow === 'hidden');
  return [digits.join(''), clipped];
}"""


@needs_browser
def test_odometer_reads_real_numbers_and_stays_inside_its_drums():
    """Each drum shows the digit nearest its rest line; with every carry synced
    to the column below, each reading on the way is a real number in range."""
    times = [i / 30 for i in range(0, 40)]
    jobs = [(_job("counter", {"value": "2024", "from": 1983, "style": "odometer"}), None, times),
            (_job("counter", {"value": "87", "style": "odometer"}), None, times)]
    years, small = asyncio.run(_probe(jobs, script=_DRUM_READING))
    assert all(clipped for _, clipped in years + small)
    nums = [int(r) for r, _ in years]
    assert nums[0] == 1983 and nums[-1] == 2024
    assert nums == sorted(nums) and all(1983 <= v <= 2024 for v in nums), nums
    # leading zeros stay blank: '87' never reads '07'
    reads = [r for r, _ in small]
    assert reads[-1] == "87"
    assert all(not (len(r) == 2 and r[0] == "0") for r in reads), reads


@needs_browser
def test_counter_lockup_never_reflows_while_counting():
    where = """() => {
      const n = document.querySelector('.num:not(.glow)');
      const aff = n.querySelector(':scope > .aff'), lab = document.querySelector('.label');
      return [aff.offsetLeft, n.offsetWidth, lab.offsetLeft];
    }"""
    jobs = [(_job("counter", {"value": v, "label": "of the *revenue*"}), None, [0.1, 0.4, 0.77, 0.95, 1.5])
            for v in ("100%", "10x", "1,000,000+")]
    for positions in asyncio.run(_probe(jobs, script=where)):
        assert all(p == positions[0] for p in positions), positions


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
