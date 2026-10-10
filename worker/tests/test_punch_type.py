"""Punchlines and type craft (showcase judging, Oct 2026).

- Numbers land on the word: a counter completes on the spoken number's onset
  (0-40 ms early at most, never sooner), a punchline number hard-cuts on
  ('reveal'), and the write tools set or move the landing from the transcript.
- Stacked type never collides: phrase_build rows and word_slam lines/kickers
  are spaced by the measured ink of their glyphs.
- The typewriter's plate arrives with its first glyph.
- versus_split's values share one size and one line count, its divider is no
  taller than the lettering, and a typographic 'vs' is available.

Browser checks read the laid-out DOM (relative comparisons, no pixel or
font-metric margins), so they hold on macOS and on the Linux CI fonts alike.
"""

import asyncio
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import number_reveal  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402


# ── spoken numbers ──────────────────────────────────────────────────────

def _words(text, t0=0.0, step=0.25):
    out, t = [], t0
    for w in text.split():
        out.append({"w": w, "t0": round(t, 3), "t1": round(t + 0.2, 3)})
        t += step
    return out


def _said(text):
    return [(sorted(n["values"]), n["said"]) for n in number_reveal.spoken_numbers(_words(text))]


def test_spoken_numbers_read_digits_and_words():
    assert _said("all we got was 140 characters.") == [([140.0], "140")]
    assert _said("We have 30, 40 fonts") == [([30.0], "30,"), ([40.0], "40")]
    assert _said("thirty, forty fonts") == [([30.0], "thirty,"), ([40.0], "forty")]
    assert _said("one hundred and forty characters") == [([140.0], "one hundred and forty")]
    assert _said("sixty-two thousand creators") == [([62000.0], "sixty-two thousand")]
    assert _said("a million users") == [([1e6], "a million")]
    assert _said("four point nine stars") == [([4.9], "four point nine")]
    assert _said("in nineteen eighty-three we") == [([1983.0], "nineteen eighty-three")]
    assert _said("it's $1.2 billion") == [([1.2, 1.2e9], "$1.2 billion")]
    assert _said("made 32% fewer errors") == [([32.0], "32%")]
    assert _said("ten times better") == [([10.0], "ten times")]
    assert _said("62,000 people") == [([62000.0], "62,000")]


def test_target_values_match_the_whole_amount():
    tv = number_reveal.target_values
    assert tv("140") == {140.0}
    assert tv("62,000+") == {62000.0}
    assert tv("$1.2B") == {1.2e9}
    assert tv("1M users") == {1e6}           # never a bare 'one'
    assert tv("340%") == {340.0}
    assert tv("€3,5") == {3.5}
    assert tv("1 000 000") == {1e6}
    assert tv("N/A") is None
    assert number_reveal._slam_figure("*32%* / fewer errors") == "32%"
    assert number_reveal._slam_figure("*garbage*") is None


def test_counter_landing_mirrors_the_template():
    cl = number_reveal.counter_landing
    assert cl({"value": "140"}, 3.0) == 1.0
    assert cl({"value": "140"}, 1.4) == pytest.approx(0.77)
    assert cl({"value": "140", "style": "reveal"}, 2.0) == 0.0
    assert cl({"value": "140", "land": 0.45}, 2.0) == 0.45
    # an explicit landing never runs into the exit
    assert cl({"value": "140", "land": 1.9}, 2.0) == pytest.approx(2.0 - 0.2 - 0.1)


# ── the write-time landing ──────────────────────────────────────────────

THIEL = ("you know they promised us flying cars and all we got was 140 characters. "
         "It's not an anti-Twitter argument")


class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, text=THIEL, t0=28.0, duration=60.0):
        self.duration = duration
        edl = default_edl(duration)
        edl["keep"] = [[0.0, duration]]
        self.index = {"video": {"width": 1080, "height": 1920, "duration": duration},
                      "words": _words(text, t0)}
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


def _onset(ctx, word):
    return next(w["t0"] for w in ctx.index["words"] if w["w"] == word)


def test_a_count_completes_on_its_spoken_number(probe):
    ctx = _Ctx()
    onset = _onset(ctx, "140")
    # placed by eye 0.6 s early: the landing (auto 1.0 s in) would beat the word
    out = motion_tools.add_motion_graphic(ctx, "counter", onset - 1.6, onset + 1.4, id="num",
                                          params={"value": "140"})
    assert "NUMBER LANDED" in out, out
    it = ctx.item("num")
    landing = it["start"] + number_reveal.counter_landing(it["params"], it["end"] - it["start"])
    assert onset - number_reveal.MAX_LEAD_S <= landing <= onset
    assert landing == pytest.approx(onset - number_reveal.LEAD_S, abs=0.002)
    # the roll is 1.58 s over the setup: the reply says so and offers reveal
    assert "rolls for" in out and "style='reveal'" in out, out
    # a count that would have no time to roll starts on the lead-in instead
    out = motion_tools.set_motion_graphic(ctx, "num", start=onset - 0.1)
    it = ctx.item("num")
    assert it["start"] == pytest.approx(onset - number_reveal.LEAD_S - number_reveal.ROLL_S, abs=0.002)
    assert it["params"]["land"] == pytest.approx(number_reveal.ROLL_S, abs=0.002)
    assert "rolls for" not in out


def test_a_reveal_cuts_on_with_the_word_and_nothing_shows_during_the_setup(probe):
    ctx = _Ctx()
    onset = _onset(ctx, "140")
    end = onset + 1.4
    motion_tools.add_motion_graphic(ctx, "counter", onset - 1.0, end, id="num",
                                    params={"value": "140", "style": "reveal"})
    it = ctx.item("num")
    assert it["start"] == pytest.approx(onset - number_reveal.LEAD_S, abs=0.002)
    assert it["end"] == pytest.approx(end) and it["params"]["land"] == 0
    # a late one is pulled forward onto the word
    motion_tools.set_motion_graphic(ctx, "num", start=onset + 0.3)
    assert ctx.item("num")["start"] == pytest.approx(onset - number_reveal.LEAD_S, abs=0.002)


def test_on_time_numbers_and_numbers_nobody_says_are_left_alone(probe):
    ctx = _Ctx()
    onset = _onset(ctx, "140")
    out = motion_tools.add_motion_graphic(ctx, "counter", onset - 1.0, onset + 1.5, id="num",
                                          params={"value": "140"})
    assert "NUMBER LANDED" not in out
    assert ctx.item("num")["start"] == pytest.approx(onset - 1.0)
    assert ctx.item("num")["params"]["land"] == 0
    out = motion_tools.add_motion_graphic(ctx, "counter", 30.0, 32.0, id="other",
                                          params={"value": "87%"})
    assert "NUMBER" not in out and ctx.item("other")["start"] == 30.0
    # no transcript: nothing to compare against
    bare = _Ctx()
    bare.index["words"] = []
    motion_tools.add_motion_graphic(bare, "counter", onset - 1.6, onset + 1.4, id="num",
                                    params={"value": "140"})
    assert bare.item("num")["params"]["land"] == 0


def test_a_number_slam_moves_onto_its_word(probe):
    ctx = _Ctx("look at this, 32% fewer errors", t0=8.0)
    onset = _onset(ctx, "32%")
    out = motion_tools.add_motion_graphic(ctx, "word_slam", onset - 0.6, onset + 1.2, id="st",
                                          params={"text": "*32%* / fewer errors", "entrance": "slam"})
    it = ctx.item("st")
    assert "NUMBER LANDED" in out, out
    assert it["start"] + 0.2 == pytest.approx(onset - number_reveal.LEAD_S, abs=0.002)
    assert it["end"] - it["start"] == pytest.approx(1.8, abs=0.002)
    # a word slam without a figure is the editor's own timing
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 9.0, 10.0, id="w",
                                          params={"text": "*errors*"})
    assert "NUMBER" not in out and ctx.item("w")["start"] == 9.0


def test_counter_cues_follow_the_landing():
    spec = motion_templates.spec("counter")

    def cues(params, s=10.0, e=12.0):
        clean = motion_templates.normalize_params("counter", params)
        return [(round(t, 3), k) for t, k, _g in motion_tools._sfx_cues(spec, clean, s, e)]
    assert [t for t, k in cues({"value": "140", "land": 0.45}) if k.startswith("impact")] == [10.45]
    rev = cues({"value": "140", "style": "reveal"})
    assert [t for t, _k in rev] == [10.0]                     # one hit on the cut, no tick run
    assert [t for t, _k in cues({"value": "140", "style": "reveal", "land": 0.3})] == [10.3]


# ── rendered compositions ───────────────────────────────────────────────

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
    """[(template, params, duration, [(t, js_expr), ...])] -> per case the
    expression values at each time (after MG.ready, seeking in order)."""
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            for name, params, dur, probes in cases:
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
                    got = []
                    for t, expr in probes:
                        await page.evaluate("t => window.__mgSeek(t)", float(t))
                        got.append(await page.evaluate(expr))
                    assert not await page.evaluate("window.__mgErrors"), name
                    out.append(got)
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


NUM = "document.querySelector('.num:not(.glow)').textContent"
SHOWN = "(() => { const s = document.querySelector('#stage'); return [getComputedStyle(s).opacity, " + NUM + "]; })()"


@needs_browser
def test_a_count_never_reads_its_figure_before_the_landing_and_a_reveal_cuts_on():
    land = 0.5
    frames = [i / 30 for i in range(0, 25)]
    count, odo, reveal = asyncio.run(_run([
        ("counter", {"value": "140", "land": land, "glow": 0}, 1.6, [(t, NUM) for t in frames]),
        ("counter", {"value": "62,000+", "land": land, "glow": 0}, 1.6, [(t, NUM) for t in frames]),
        ("counter", {"value": "140", "style": "reveal", "land": land}, 1.6, [(t, SHOWN) for t in frames]),
    ]))
    for t, text in zip(frames, count):
        assert (text == "140") == (t >= land - 1e-6), (t, text)
    for t, text in zip(frames, odo):
        assert (text.startswith("62,000")) == (t >= land - 1e-6), (t, text)
    for t, (opacity, text) in zip(frames, reveal):
        assert text == "140"
        assert (float(opacity) > 0.99) == (t >= land - 1e-6), (t, opacity)


# the smallest vertical gap between glyph ink of consecutive stacked elements
# that share a column (negative = they overlap)
GAPS = """sel => {
  const els = Array.from(document.querySelectorAll(sel));
  const out = [];
  for (let i = 1; i < els.length; i++) {
    const need = MG.stackGap(els[i - 1], els[i]);
    out.push(need === -Infinity ? null : -need);
  }
  return out;
}"""


@needs_browser
def test_stacked_rows_never_collide():
    jobs_rows = [
        [{"text": "Lisa:", "role": "script", "size": "1.0", "at": "0"},
         {"text": "*totally* proportionally", "role": "serif", "size": "0.7", "at": "0"},
         {"text": "SPACED TEXT", "role": "condensed", "size": "1.2", "at": "0"}],
        [{"text": "no college student", "role": "sans", "size": "0.6", "at": "0"},
         {"text": "writing a paper", "role": "serif", "size": "0.8", "at": "0"},
         {"text": "WITHOUT *ONE*", "role": "condensed", "size": "1.4", "at": "0"}],
        [{"text": "Steve Jobs, 1983", "role": "sans", "size": "0.45", "at": "0"},
         {"text": "computer fonts were", "role": "serif", "size": "0.8", "at": "0"},
         {"text": "*GARBAGE*", "role": "condensed", "size": "1.5", "at": "0"}],
    ]
    cases = [("phrase_build", {"rows": r, "y": 0.3}, 2.0, [(1.0, f"({GAPS})('.row')")]) for r in jobs_rows]
    cases.append(("phrase_build", {"rows": jobs_rows[0], "y": 0.3, "leading": "overlap"}, 2.0,
                  [(1.0, f"({GAPS})('.row')")]))
    cases += [("word_slam", {"text": "*garbage*", "kicker": "the fonts have been just", "role": "condensed"},
               1.5, [(1.0, f"({GAPS})('.kick, .line')")]),
              ("word_slam", {"text": "proper / typography", "role": "serif", "kicker": "it's just"},
               1.5, [(1.0, f"({GAPS})('.kick, .line')")])]
    res = asyncio.run(_run(cases))
    clear, overlap, slams = res[:3], res[3], res[4:]
    for (gaps,) in clear + slams:
        assert gaps and all(g is not None and g > 0 for g in gaps), gaps
    # 'overlap' keeps the designed negative leading: the script swash crosses
    assert any(g is not None and g < 0 for g in overlap[0]), overlap


@needs_browser
def test_typewriter_plate_arrives_with_its_first_glyph():
    expr = """(() => {
      const shown = Array.from(document.querySelectorAll('.ch')).filter(c => c.style.display !== 'none'
        && c.style.visibility !== 'hidden').length;
      return [shown, parseFloat(getComputedStyle(document.querySelector('#plate')).opacity)];
    })()"""
    (plate,), = asyncio.run(_run([("typewriter", {"text": "computers. internet. mobile.", "plate": True},
                                   2.3, [(0.0, expr)])]))
    shown, opacity = plate
    assert shown >= 1 and opacity == 1.0, plate


VS = """(() => {
  const subs = Array.from(document.querySelectorAll('.lab .s'));
  const lines = subs.map(e => { const r = document.createRange(); r.selectNodeContents(e);
    return new Set(Array.from(r.getClientRects()).map(b => Math.round(b.top))).size; });
  const labs = Array.from(document.querySelectorAll('.lab')).map(e => e.getBoundingClientRect());
  const dv = document.querySelector('.div');
  const d = dv ? dv.getBoundingClientRect() : null;
  return {sizes: subs.map(e => getComputedStyle(e).fontSize), lines,
          text: [Math.min(...labs.map(r => r.top)), Math.max(...labs.map(r => r.bottom))],
          div: d && [d.top, d.bottom], badge: !!document.querySelector('.badge'),
          vs: !!document.querySelector('.vst'), H: MG.H};
})()"""


@needs_browser
def test_versus_values_are_balanced_and_the_divider_stays_with_the_lettering():
    thiel = {"left": "1960s", "right": "TODAY", "left_sub": "rockets, cures",
             "right_sub": "information tech", "size": 0.8}
    res = asyncio.run(_run([
        ("versus_split", thiel, 3.0, [(1.5, VS)]),
        ("versus_split", dict(thiel, vs="serif"), 3.0, [(1.5, VS)]),
        ("versus_split", {"left": "Tesla", "right": "Ford", "left_sub": "$800B", "right_sub": "$45B"},
         3.0, [(1.5, VS)]),
    ]))
    (badge,), (serif,), (short,) = res
    for r in (badge, serif, short):
        assert len(set(r["sizes"])) == 1 and len(set(r["lines"])) == 1, r
    assert short["lines"] == [1, 1]
    # the divider spans the lettering (a little past the disc), not a third of the frame
    top, bottom = badge["text"]
    assert badge["div"][1] - badge["div"][0] < (bottom - top) * 1.6 + 0.02 * badge["H"], badge
    assert badge["badge"] and not badge["vs"]
    assert serif["vs"] and not serif["badge"] and serif["div"] is None
