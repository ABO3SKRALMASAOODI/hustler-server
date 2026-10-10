"""One type system, word-timed reveals, cut-snapped graphics and the hero
tier (showcase judging, Oct 2026, round 4).

What is pinned here:
  1. The short's Look (motion_look): one accent (the captions' highlight, else
     the accent its graphics wear) and at most three type roles. A new graphic
     takes the Look's accent and ink by default; a second accent, a fourth role
     and broadcast furniture in an editorial Look are NOTEd with the fix.
  2. A word-timed graphic's window starts on its first visible word, and an
     entrance or exit within 0.15 s of a cut moves onto the cut (a number keeps
     its entrance on its spoken number; a lockup never ends before its last word).
  3. phrase_build: at most 3 sizes; marker_text reveals a spoken line on its
     onsets.
  4. A run of parallel word_slams is one series: one size per line across it.
  5. Tiers: 'payoff' locks number and noun up in the accent; 'hero' is the giant
     word behind the speaker, falling back to a face-safe display slam when no
     person matte can be had. The typewriter is a band-wide hero.
  6. MG.accentInk: an accent the plate would sink is lifted (APCA), unchanged
     with no plate.

Run:  python -m pytest tests/test_type_system.py -q     (from worker/)
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import caption_carry  # noqa: E402
import keepout  # noqa: E402
import motion_engine  # noqa: E402
import motion_look  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import plate as plate_mod  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

W, H = 1080, 1920


def _mg(mid, template, start, end, **params):
    return {"id": mid, "template": template, "start": start, "end": end,
            "params": motion_templates.check_params(template, params)}


def _edl(motion=(), keep=((0.0, 30.0),), accent=None, look="editorial"):
    edl = default_edl(40.0)
    edl["keep"] = [list(k) for k in keep]
    style = {"motion_look": look} if look else {}
    if accent:
        style["highlight_color"] = accent
    edl["captions"] = {"mode": "from_transcript", "design_version": 2, "style": style}
    edl["motion"] = [dict(m) for m in motion]
    return validate_edl(edl, 40.0).model_dump()


class _Ctx:
    project_id = 1
    has_main_video = True

    def __init__(self, edl, words=()):
        self.duration = 40.0
        self.index = {"video": {"width": 1080, "height": 1920, "duration": 40.0},
                      "words": [{"w": w, "t0": a, "t1": b} for w, a, b in words]}
        self._edl = validate_edl(edl, self.duration).model_dump()
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


# ── 1. the Look ──────────────────────────────────────────────────────────

ELON = [_mg("hook", "hook_title", 0.0, 2.2, text="Gamers make / *better surgeons?*",
            accent="#FF3B30"),
        _mg("name", "lower_third", 2.4, 5.6, name="Elon Musk", role="on JRE",
            accent="#FF3B30"),
        _mg("st1", "word_slam", 8.7, 10.56, text="*32%* / fewer errors", role="condensed",
            accent="#FF3B30", fit="justify")]


def test_the_looks_accent_is_the_captions_highlight_and_a_second_one_is_noted():
    edl = _edl(ELON, accent="#FF3B30")
    look = motion_look.short_look(edl)
    assert look["accent"] == "#FF3B30" and "captions" in look["accent_from"]
    marker = _mg("end", "marker_text", 18.9, 21.9,
                 text="You should be *required* / in medical school", accent="#FFD84D")
    notes = motion_look.coherence_notes(edl, marker)
    assert any("second accent" in n and "'accent': '#FF3B30'" in n for n in notes), notes
    # editorial furniture: the highlighter and the broadcast bar are named
    assert any("highlighter marker in an editorial Look" in n for n in notes)
    assert any("broadcast clean_bar" in n
               for n in motion_look.coherence_notes(edl, ELON[1]))
    # in the Look's accent, with an underline, nothing is said
    ok = _mg("end", "marker_text", 18.9, 21.9, text="You should be *required*",
             accent="#FF3B30", style="underline")
    assert motion_look.coherence_notes(edl, ok) == []


def test_without_a_caption_highlight_the_accent_is_the_one_most_graphics_wear():
    items = [_mg("a", "word_slam", 1, 2, text="*one*", accent="#FFC940"),
             _mg("b", "word_slam", 3, 4, text="*two*", accent="#FFC940"),
             _mg("c", "word_slam", 5, 6, text="*three*", accent="#ED080D"),
             # an accent nothing on it wears does not vote
             _mg("d", "word_slam", 7, 8, text="plain", accent="#00FF00")]
    look = motion_look.short_look(_edl(items, look=None))
    assert look["accent"] == "#FFC940"


def test_type_roles_are_counted_and_a_fourth_is_noted():
    items = [_mg("hook", "word_slam", 0, 2, text="where did *progress* go?", kicker="Peter Thiel"),
             _mg("list", "phrase_build", 3, 9, rows=[{"text": "ROCKETS", "role": "condensed"},
                                                     {"text": "supersonic", "role": "serif"}])]
    edl = _edl(items)
    assert motion_look.short_look(edl)["roles"] == ["grotesk", "condensed", "serif"]
    bits = _mg("bits", "typewriter", 20, 22, text="computers. internet.", font="mono")
    notes = motion_look.coherence_notes(edl, bits)
    assert any("mono type, making 4 type roles" in n for n in notes), notes
    # its default role is swapped for one the short uses when the editor
    # did not ask for mono
    fill = motion_look.look_defaults(edl, "typewriter", motion_templates.spec("typewriter"),
                                     {"text": "x"})
    assert fill.get("font") == "sans"
    assert "font" not in motion_look.look_defaults(
        edl, "typewriter", motion_templates.spec("typewriter"), {"text": "x", "font": "mono"})


def test_a_new_graphic_takes_the_looks_accent_and_ink(probe):
    items = [_mg("a", "word_slam", 1, 2, text="*one*", accent="#FFC940", color="#F8F6F2"),
             _mg("b", "word_slam", 3, 4, text="*two*", accent="#FFC940", color="#F8F6F2")]
    ctx = _Ctx(_edl(items, look=None))
    out = motion_tools.add_motion_graphic(ctx, "marker_text", 10.0, 12.0,
                                          params={"text": "the *key* line"}, id="m")
    assert ctx.item("m")["params"]["accent"] == "#FFC940"     # not the template's #FFD84D
    assert ctx.item("m")["params"]["color"] == "#F8F6F2"
    assert "LOOK: from the short's Look: accent #FFC940, color #F8F6F2." in out, out
    # an accent the editor passes is theirs (and is noted when it is a second one)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 14.0, 15.0,
                                          params={"text": "*red*", "accent": "#ED080D"}, id="r")
    assert ctx.item("r")["params"]["accent"] == "#ED080D"
    assert "NOTE (look): 'r' wears accent #ED080D" in out, out


# ── 2. the window: first visible word, the cut ───────────────────────────

# keep [0, 5] + [6, 12]: a cut at program 5.0
CUT_WORDS = [("we", 4.2, 4.4), ("wanted", 4.4, 4.9), ("flying", 6.08, 6.4),
             ("cars", 6.4, 6.9), ("and", 7.0, 7.1), ("got", 7.1, 7.3),
             ("characters", 7.4, 8.0)]


def test_a_lockup_starts_on_its_first_word_and_snaps_onto_a_cut(probe):
    ctx = _Ctx(_edl(keep=((0.0, 5.0), (6.0, 12.0))), CUT_WORDS)
    rows = [{"text": "flying cars", "role": "sans", "size": "1"}]
    out = motion_tools.add_motion_graphic(ctx, "phrase_build", 4.0, 7.0,
                                          params={"rows": rows}, id="pb")
    it = ctx.item("pb")
    # the first word 'flying' is said at program 5.08, 80 ms after the cut:
    # the window starts on the cut and the word shows on it (one event)
    assert it["start"] == pytest.approx(5.0)
    assert it["reading"]["rows"][0][0] == 0.0
    assert "WINDOW: starts on its first visible word at 5.08s" in out, out
    assert "entrance 5.08 → 5s" in out, out


def test_entrances_and_exits_near_a_cut_snap_but_a_number_keeps_its_word(probe):
    ctx = _Ctx(_edl(keep=((0.0, 5.0), (6.0, 12.0))), CUT_WORDS)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 5.1, 6.9,
                                          params={"text": "*flying*"}, id="ws")
    assert ctx.item("ws")["start"] == pytest.approx(5.0) and "CUT-SNAP" in out
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.0, 4.88,
                                          params={"text": "*wanted*"}, id="w2")
    assert ctx.item("w2")["end"] == pytest.approx(5.0) and "exit 4.88 → 5s" in out
    # far from any cut: untouched
    motion_tools.add_motion_graphic(ctx, "word_slam", 7.0, 8.0, params={"text": "*got*"}, id="w3")
    assert (ctx.item("w3")["start"], ctx.item("w3")["end"]) == (7.0, 8.0)
    # a counter's entrance is its spoken number's: never snapped
    assert motion_tools._number_timed(_mg("c", "counter", 0, 1, value="140"))
    assert motion_tools._number_timed(_mg("s", "word_slam", 0, 1, text="*32%*"))
    assert not motion_tools._number_timed(_mg("s", "word_slam", 0, 1, text="*flying*"))


def test_cut_points_include_camera_cuts_and_card_edges():
    ctx = _Ctx(_edl(keep=((0.0, 5.0), (6.0, 12.0))))
    ctx.index["shots"] = [{"id": 1, "start": 0.0, "end": 8.0}, {"id": 2, "start": 8.0, "end": 99.0}]
    edl = ctx.latest_edl()["json"]
    edl["effects"] = {"picture_cards": [{"id": "c", "start": 9.0, "end": 10.5}]}
    assert motion_tools._program_cuts(ctx, edl) == [5.0, 7.0, 9.0, 10.5]


# ── 3. phrase_build sizes, marker_text onsets ────────────────────────────

def test_a_lockup_is_capped_at_three_sizes():
    item = _mg("list", "phrase_build", 0, 6, rows=[
        {"text": "ROCKETS", "size": "1.2"}, {"text": "supersonic", "size": "0.8"},
        {"text": "underwater", "size": "0.75"}, {"text": "MEDICINES", "size": "1"}])
    note = motion_tools._cap_size_levels(item)
    assert [r["size"] for r in item["params"]["rows"]] == ["1.2", "0.77", "0.77", "1"]
    assert "4 size levels" in note and "merged to 3" in note
    assert motion_tools._cap_size_levels(item) == ""            # already a 3-size ladder
    # four rows stays the template's own limit
    with pytest.raises(ValueError):
        motion_templates.check_params("phrase_build", {"rows": [{"text": "x"}] * 5})


MARKER_WORDS = [("You", 18.9, 19.1), ("should", 19.4, 19.6), ("be", 19.6, 19.7),
                ("required", 19.8, 20.3), ("in", 20.4, 20.5), ("medical", 20.5, 20.8),
                ("school", 20.8, 21.2)]


def test_marker_text_is_timed_to_its_spoken_onsets_and_starts_on_its_first_word(probe):
    ctx = _Ctx(_edl(keep=((0.0, 30.0),)), MARKER_WORDS)
    out = motion_tools.add_motion_graphic(
        ctx, "marker_text", 17.0, 21.9,
        params={"text": "You should be *required* / in medical school",
                "accent": "#FF3B30", "style": "underline"}, id="end")
    it = ctx.item("end")
    assert it["start"] == pytest.approx(18.84)      # 'You' at 18.9, its 60 ms rise
    assert it["reading"]["rows"][0][:4] == [0.0, 0.56, 0.76, 0.96]
    assert "WINDOW" in out
    reveals = caption_carry.onset_reveals(it)
    assert reveals[0] == 0.0 and reveals == sorted(reveals)
    # a line mostly nobody says keeps its authored build
    unsaid = dict(it, reading={"v": 1, "rows": [[0.5, None, None, None, None, None]]})
    assert caption_carry.onset_reveals(unsaid) == []


# ── 4. series ────────────────────────────────────────────────────────────

def _stat(mid, a, b, text, **kw):
    return _mg(mid, "word_slam", a, b, text=text, role="condensed", fit="justify", **kw)


def test_a_run_of_parallel_slams_is_one_series():
    items = [_stat("st1", 8.7, 10.56, "*32%* / fewer errors"),
             _stat("st2", 10.56, 12.42, "*24%* / faster"),
             _stat("st3", 12.42, 15.38, "*26%* / better overall"),
             _stat("far", 20.0, 21.0, "*9%* / later")]               # 4.6 s later
    runs = motion_look.attach_series(items)
    assert [[m["id"] for m in r] for r in runs] == [["st1", "st2", "st3"]]
    assert items[1]["series"] == {"texts": ["*32%* / fewer errors", "*24%* / faster",
                                            "*26%* / better overall"], "i": 1,
                                  "ids": ["st1", "st2", "st3"]}
    assert "series" not in items[3]
    # another role (or line count) is another design: no series
    items[1]["params"]["role"] = "serif"
    motion_look.attach_series(items)
    assert not any("series" in m for m in items)
    # the series survives validation and reaches the page
    items[1]["params"]["role"] = "condensed"
    motion_look.attach_series(items)
    edl = _edl(items)
    assert edl["motion"][0]["series"]["ids"] == ["st1", "st2", "st3"]
    assert '"_series"' in motion_templates.build_job(edl["motion"][0], W, H, 30).html
    assert validate_edl(dict(edl, motion=[dict(items[0], series={"texts": "x"})]),
                        40.0).model_dump()["motion"][0]["series"] is None


def test_adding_a_member_writes_the_series_onto_its_siblings(probe):
    ctx = _Ctx(_edl([_stat("st1", 8.7, 10.56, "*32%* / fewer errors", y=0.18)]))
    motion_tools.add_motion_graphic(ctx, "word_slam", 10.56, 12.42, id="st2",
                                    params={"text": "*24%* / faster", "role": "condensed",
                                            "fit": "justify", "y": 0.18})
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 12.42, 15.38, id="st3",
                                          params={"text": "*26%* / better overall",
                                                  "role": "condensed", "fit": "justify",
                                                  "y": 0.3})
    assert ctx.item("st1")["series"]["ids"] == ["st1", "st2", "st3"]
    assert "SERIES: st1, st2, st3" in out and "NOTE (series): st1, st2" in out, out


# ── 5. tiers and the hero fallback ───────────────────────────────────────

def test_a_hero_word_without_a_matte_falls_back_to_a_face_safe_display_slam(probe):
    ctx = _Ctx(_edl())
    ctx.has_main_video = False            # nothing to measure a subject in
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 10.0, 11.5, id="hero",
                                          params={"text": "*enough*", "tier": "hero"})
    it = ctx.item("hero")
    assert it["layer"] == "above_captions" and it["params"]["tier"] == "display"
    assert "HERO FALLBACK" in out, out


def test_a_hero_word_goes_behind_only_on_a_person_mask(probe, monkeypatch):
    import agent_tools
    got = {}

    def fake(method, coverage):
        def measure(ctx, edl, s, e, box, words=None):
            got["box"] = box
            return ({"asset_key": "matte/1/x.mp4", "src_start": s, "src_end": e, "fp": "x",
                     "coverage": coverage, "method": method},
                    {"ok": True, "coverage": coverage, "method": method}, None)
        return measure
    ctx = _Ctx(_edl())
    monkeypatch.setattr(agent_tools, "_measure_subject_matte", fake("plate", 0.04))
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 10.0, 11.5, id="h1",
                                          params={"text": "*enough*", "tier": "hero"})
    assert ctx.item("h1")["layer"] == "above_captions" and "photometric" in out, out
    monkeypatch.setattr(agent_tools, "_measure_subject_matte", fake("person", 0.31))
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 20.0, 21.5, id="h2",
                                          params={"text": "*more*", "tier": "hero"})
    it = ctx.item("h2")
    assert it["layer"] == "behind_subject" and it["behind"]["method"] == "person"
    assert it["params"]["y"] == motion_tools.HERO_Y_DEFAULT     # no face measured: head height
    assert got["box"] is not None          # the crossing numbers are measured
    # a second hero in the short is named
    out = motion_tools.set_motion_graphic(ctx, "h1", params={"tier": "hero"})
    assert "NOTE (tier): 'h2' is already this short's hero word" in out, out


def test_the_payoff_is_named_when_it_is_not_the_largest_lockup(probe):
    big = _mg("enough", "word_slam", 5.0, 6.0, text="*enough*")
    big["footprint"] = caption_carry.make_footprint([0.1, 0.4, 0.9, 0.65], W, H, [])
    ctx = _Ctx(_edl([big]))
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 20.0, 21.5, id="pay",
                                          params={"text": "*140* / characters", "tier": "payoff"})
    assert "NOTE (payoff): 'pay' draws 0.10" in out and "'enough' draws 0.25" in out, out


def test_a_tall_accumulating_list_is_rows_not_a_bigger_lockup(probe):
    """Integration (beats + type system): a five-row list_build is taller
    than the payoff in small type; the payoff check compares an item set
    two lines deep at the list's type size, not the whole stack."""
    lst = _mg("list", "list_build", 3.0, 9.0,
              rows=[{"text": t} for t in ("rockets", "supersonic aviation",
                    "green revolution agriculture", "underwater cities", "new medicines")],
              role="condensed", y=0.64, width=0.8)
    lst["footprint"] = caption_carry.make_footprint([0.16, 0.48, 0.84, 0.80], W, H, [])
    ctx = _Ctx(_edl([lst]))
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 20.0, 21.5, id="pay",
                                          params={"text": "*140* / characters", "tier": "payoff"})
    assert "NOTE (payoff)" not in out, out


def test_tier_and_typewriter_estimates_for_browserless_lanes():
    spec = motion_templates.spec("word_slam")
    hero = keepout.nominal_ink("word_slam", spec, {"text": "x", "tier": "hero", "y": 0.3})
    assert hero[3] - hero[1] == pytest.approx(0.3) and hero[2] - hero[0] > 0.9
    pay = keepout.nominal_ink("word_slam", spec, {"text": "x", "tier": "payoff", "y": 0.6,
                                                   "width": 0.85})
    assert pay[3] - pay[1] > 0.3
    tw = keepout.nominal_ink("typewriter", motion_templates.spec("typewriter"),
                             {"text": "computers. internet. mobile.", "y": 0.66}, frame=(W, H))
    # band-wide (0.76 of the width, clear of the button rail), three lines of
    # >= 5% type on its plate
    assert tw[0] == pytest.approx(0.12) and tw[2] == pytest.approx(0.88)
    assert tw[3] - tw[1] > 3 * 0.05 * 1.3


# ── browser: what the pages draw ─────────────────────────────────────────

def _chromium_ok():
    try:
        return motion_engine.available()
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(not _chromium_ok(), reason="headless Chromium required")


def _plate(level, cols=18, rows=32):
    return {"c": cols, "r": rows,
            "s": [{"t": 0.5, "g": plate_mod.encode_grid([level] * (cols * rows))}]}


async def _eval(item, times, expr, plate=None):
    from playwright.async_api import async_playwright
    job = motion_templates.build_job(item, W, H, 30, plate=plate)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=motion_engine.CHROME_ARGS)
        try:
            dw, dh = motion_engine.design_size(job.out_w, job.out_h)
            ctx = await browser.new_context(viewport={"width": dw, "height": dh})
            await ctx.route("**/*", await motion_engine._route_factory(job))
            page = await ctx.new_page()
            await page.goto(motion_engine.ORIGIN + "/", wait_until="load")
            await page.evaluate("async () => { await document.fonts.ready;"
                                " if (window.MG && MG.ready) await MG.ready; }")
            out = []
            for t in times:
                await page.evaluate("t => window.__mgSeek(t)", t)
                out.append(await page.evaluate(expr))
            assert not await page.evaluate("window.__mgErrors")
            return out
        finally:
            await browser.close()


_LINES = "() => [...document.querySelectorAll('.line')].map(l => parseFloat(l.style.fontSize))"
_BLK_TOP = "() => document.getElementById('blk').getBoundingClientRect().top"


@needs_browser
def test_series_members_share_one_size_per_line_and_one_top():
    items = [_stat("st1", 8.7, 10.56, "*32%* / fewer errors", y=0.18),
             _stat("st2", 10.56, 12.42, "*24%* / faster", y=0.18),
             _stat("st3", 12.42, 15.38, "*26%* / better overall", y=0.18)]
    alone = asyncio.run(_eval(items[1], [1.0], _LINES))[0]
    motion_look.attach_series(items)
    sizes = [asyncio.run(_eval(m, [1.0], _LINES))[0] for m in items]
    tops = [asyncio.run(_eval(m, [1.0], _BLK_TOP))[0] for m in items]
    assert sizes[0] == pytest.approx(sizes[1], rel=1e-3)
    assert sizes[2] == pytest.approx(sizes[1], rel=1e-3)
    assert max(tops) - min(tops) < 0.004 * H
    # justified alone, 'FASTER' towered over the other labels
    assert alone[1] > 1.4 * sizes[1][1]


@needs_browser
def test_payoff_sets_number_and_noun_in_the_accent_and_hero_goes_giant():
    acc = "() => [...document.querySelectorAll('.line .mg-w')].map(w => w.style.color)"
    pay = _mg("p", "word_slam", 0, 1.5, text="140 / characters", tier="payoff", accent="#FFC940")
    colors = asyncio.run(_eval(pay, [1.0], acc))[0]
    assert colors and all(c in ("#FFC940", "rgb(255, 201, 64)") for c in colors), colors
    disp = _mg("d", "word_slam", 0, 1.5, text="more", role="condensed")
    hero = _mg("h", "word_slam", 0, 1.5, text="more", role="condensed", tier="hero")
    d = asyncio.run(_eval(disp, [1.0], _LINES))[0][0]
    h = asyncio.run(_eval(hero, [1.0], _LINES))[0][0]
    assert h > 1.1 * d and h * 0.9 <= 0.3 * H + 1


@needs_browser
def test_the_typewriter_is_a_band_wide_hero():
    tw = _mg("t", "typewriter", 0, 3, text="computers. internet. mobile.", font="mono", size=0.8)
    fs, plate = asyncio.run(_eval(tw, [2.9], "() => [parseFloat(document.querySelector('.ln')"
                                              ".style.fontSize), document.getElementById('plate')"
                                              ".getBoundingClientRect().width]"))[0]
    assert fs >= 0.05 * H - 1
    assert plate == pytest.approx(0.76 * W, rel=0.03)


@needs_browser
def test_marker_text_reveals_each_word_on_its_onset():
    item = _mg("m", "marker_text", 0, 3.2, text="You should be *required*",
               style="underline", accent="#FF3B30")
    shown = "() => [...document.querySelectorAll('.wrap .mg-w')].map(w => +getComputedStyle(w).opacity > 0.5 ? 1 : 0)"
    timed = dict(item, reading={"v": 1, "rows": [[0.0, 0.8, 1.2, 1.6]], "bridges": []})
    st = asyncio.run(_eval(timed, [0.3, 0.9, 1.3, 1.75], shown))
    assert st == [[1, 0, 0, 0], [1, 1, 0, 0], [1, 1, 1, 0], [1, 1, 1, 1]]
    # no reading: the authored build, complete in its first 0.35 s
    assert asyncio.run(_eval(item, [0.4], shown))[0] == [1, 1, 1, 1]


@needs_browser
def test_an_accent_the_plate_would_sink_is_lifted_and_unchanged_without_a_plate():
    hook = _mg("h", "hook_title", 0, 2.2, text="Gamers make / *better surgeons?*",
               accent="#FF3B30", y=0.5)
    col = "() => getComputedStyle(document.querySelector('.acc-serif')).color"
    none, grey = (asyncio.run(_eval(hook, [1.5], col, p))[0] for p in (None, _plate(40)))
    assert none == "rgb(255, 59, 48)"
    r, g, b = (int(v) for v in grey[4:-1].split(","))
    assert r == 255 and g > 59 and b > 48          # the same hue, lifted
    # a heavy accent on a dark plate already reads: unchanged
    slam = _mg("s", "word_slam", 0, 1.5, text="*garbage*", role="condensed", accent="#FF5A36")
    acc = "() => document.querySelector('.line .mg-w').style.color"
    assert asyncio.run(_eval(slam, [1.0], acc, _plate(12)))[0] in ("#FF5A36",
                                                                   "rgb(255, 90, 54)")


# ── review fixes (round 4 motion track) ──────────────────────────────────

def test_the_looks_ink_is_a_light_type_ink():
    # a circle's stroke, a flash and an app card's brand colour are not type:
    # two red circle highlights never make a new hook title red
    items = [_mg("c1", "circle_highlight", 1, 2, cx=0.5, cy=0.5),
             _mg("c2", "circle_highlight", 3, 4, cx=0.5, cy=0.5),
             _mg("w", "word_slam", 5, 6, text="hi", color="#F8F6F2")]
    edl = _edl(items, look=None)
    assert motion_look.short_look(edl)["ink"] == "#F8F6F2"
    assert "color" not in motion_look.look_defaults(
        edl, "hook_title", motion_templates.spec("hook_title"), {"text": "x"})   # one wearer
    # a white caption highlight is no accent for the graphics
    assert motion_look.short_look(_edl(items, accent="#FFFFFF"))["accent"] is None
    # a dark ink (a paper card's) is not the short's ink over footage
    dark = [_mg("a", "word_slam", 1, 2, text="a", color="#111111"),
            _mg("b", "word_slam", 3, 4, text="b", color="#111111")]
    assert motion_look.short_look(_edl(dark, look=None))["ink"] is None
    # and a non-type template's colour is never filled from the Look
    two = _edl([_mg("a", "word_slam", 1, 2, text="a", color="#F8F6F2"),
                _mg("b", "word_slam", 3, 4, text="b", color="#F8F6F2")], look=None)
    assert motion_look.look_defaults(two, "hook_title", motion_templates.spec("hook_title"),
                                     {"text": "x"}).get("color") == "#F8F6F2"
    assert "color" not in motion_look.look_defaults(
        two, "notification", motion_templates.spec("notification"), {})


def test_removing_a_member_rederives_its_runs_series(probe):
    ctx = _Ctx(_edl([_stat("st1", 8.7, 10.56, "*32%* / fewer errors"),
                     _stat("st2", 10.56, 12.42, "*24%* / faster"),
                     _stat("st3", 12.42, 15.38, "*26%* / better overall")]))
    motion_tools.set_motion_graphic(ctx, "st1")
    assert ctx.item("st3")["series"]["ids"] == ["st1", "st2", "st3"]
    motion_tools.remove_motion_graphic(ctx, "st2")
    # st1 and st3 are 1.86 s apart: no longer a run, and not sized as one
    assert ctx.item("st1").get("series") is None and ctx.item("st3").get("series") is None


def test_a_changed_series_is_measured_again_before_the_captions_are_placed():
    import motion_layer
    items = [_stat("st1", 8.7, 10.56, "*32%* / fewer errors"),
             _stat("st3", 12.42, 15.38, "*26%* / better overall")]
    stale = {"texts": ["*32%* / fewer errors", "*24%* / faster"], "i": 0,
             "ids": ["st1", "st2"]}
    items[0]["series"] = stale
    items[0]["footprint"] = caption_carry.make_footprint([0.3, 0.1, 0.7, 0.25], W, H, [])
    edl = _edl(items)
    edl["captions"] = None                      # only the series pass runs
    motion_layer_edl = motion_layer.fill_footprints(edl, W, H)
    st1 = motion_layer_edl["motion"][0]
    assert "series" not in st1 and st1["footprint"]["estimated"] is True


def test_a_hero_on_a_layer_above_the_picture_is_a_display_slam(probe):
    ctx = _Ctx(_edl())
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 10.0, 11.5, id="h",
                                          layer="above_captions",
                                          params={"text": "*enough*", "tier": "hero"})
    it = ctx.item("h")
    assert it["layer"] == "above_captions" and it["params"]["tier"] == "display"
    assert "HERO: the hero tier is a word behind the speaker" in out, out
    out = motion_tools.set_motion_graphic(ctx, "h", params={"tier": "hero"},
                                          layer="below_captions")
    assert ctx.item("h")["params"]["tier"] == "display" and "HERO:" in out, out


def _fake_matte(monkeypatch, method="person", coverage=0.31):
    import agent_tools

    def measure(ctx, edl, s, e, box, words=None):
        return ({"asset_key": "matte/1/x.mp4", "src_start": s, "src_end": e, "fp": "x",
                 "coverage": coverage, "method": method},
                {"ok": True, "coverage": coverage, "method": method}, None)
    monkeypatch.setattr(agent_tools, "_measure_subject_matte", measure)


def test_a_hero_behind_the_speaker_stores_its_face_safe_display_placement(probe, monkeypatch):
    _fake_matte(monkeypatch)
    ctx = _Ctx(_edl())
    motion_tools.add_motion_graphic(ctx, "word_slam", 20.0, 21.5, id="h",
                                    params={"text": "*more*", "tier": "hero"})
    it = ctx.item("h")
    assert it["layer"] == "behind_subject"
    fb = it["behind"]["fallback"]
    assert set(fb) == {"x", "y", "width"} and 0.0 <= fb["y"] <= 1.0


def test_a_hero_the_render_could_not_set_behind_falls_back_at_write(probe, monkeypatch):
    import follow
    _fake_matte(monkeypatch)
    # a crop that follows the speaker across the window: the renderer would
    # drop the depth, so the write draws the face-safe display slam instead
    monkeypatch.setattr(follow, "moves_during", lambda edl, spans: True)
    ctx = _Ctx(_edl())
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 20.0, 21.5, id="h",
                                          params={"text": "*more*", "tier": "hero"})
    it = ctx.item("h")
    assert it["layer"] == "above_captions" and it["params"]["tier"] == "display"
    assert "HERO FALLBACK" in out and "follows the speaker" in out, out


def test_the_render_never_draws_a_hero_over_the_face(monkeypatch):
    import motion_engine as me
    import motion_layer
    hero = _mg("h", "word_slam", 1.0, 2.5, text="*more*", tier="hero", y=0.3)
    hero.update(layer="behind_subject",
                behind={"asset_key": "matte/1/x.mp4", "src_start": 1.0, "src_end": 2.5,
                        "fp": "x", "method": "person",
                        "fallback": {"x": 0.5, "y": 0.66, "width": 0.8}})
    bare = dict(hero, id="h2", behind=dict(hero["behind"], fallback=None))
    rendered = []

    def fake_render(jobs, out_dir):
        rendered.extend(jobs)
        return [me.RenderedClip(path=f"/tmp/{i}.mov", x=0, y=0, w=10, h=10, frames=1,
                                captured=1, cached=True, seconds=0.0) for i in range(len(jobs))]
    monkeypatch.setattr(me, "render_jobs", fake_render)
    edl = {"motion": [hero, bare]}
    inputs, _ = motion_layer.prepare_inputs(edl, "/tmp", W, H, 30, 10.0, [], 1,
                                            behind_why=lambda m: "a test reason")
    # the stored placement, as a display slam above the picture; the hero with
    # none is not drawn at all
    assert [(it["id"], it["layer"], it["params"]["tier"], it["params"]["y"])
            for _i, it, _c in inputs] == [("h", "above_captions", "display", 0.66)]
    assert '"tier": "display"' in rendered[0].html
    # a hero whose mask fails after its clip was drawn is left out, never
    # demoted over the face; any other graphic still demotes
    assert motion_layer.demote(hero, "mask unavailable") is None
    other = dict(_mg("o", "word_slam", 1, 2, text="x"), layer="behind_subject")
    assert motion_layer.demote(other, "x")["layer"] == "above_captions"
    clip = inputs[0][2]
    assert [it["id"] for _i, it, _c in motion_layer.demote_behind(
        [(1, hero, clip), (2, other, clip)], "canvas")] == ["o"]


def test_behind_why_holds_the_renderers_rules(monkeypatch):
    import follow
    import motion_layer
    item = {"layer": "behind_subject", "behind": {"asset_key": "k", "src_start": 6.2,
                                                  "src_end": 7.0, "fp": "x"}}
    tl = Timeline([[0.0, 5.0], [6.0, 12.0]])
    assert motion_layer.behind_why({}, tl, item) is None
    assert "cut now falls" in motion_layer.behind_why(
        {}, Timeline([[0.0, 6.5], [6.6, 12.0]]), item)
    assert "no longer in the edit" in motion_layer.behind_why({}, Timeline([[0.0, 5.0]]), item)
    assert "no subject mask" in motion_layer.behind_why({}, tl, {"layer": "behind_subject"})
    monkeypatch.setattr(follow, "moves_during", lambda edl, spans: True)
    assert "follows the speaker" in motion_layer.behind_why({}, tl, item)


@needs_browser
def test_a_spoken_marker_line_on_a_white_shirt_takes_ink_not_a_growing_box():
    """Judged (round 5): Elon's closing line sat in a grey box that grew on
    every word over his white T-shirt. On a plate bright under every word
    the line takes dark ink and no box at all; where a box is unavoidable
    (a dim ink over a bright-and-dark plate) it is the final block's from
    the first word on, never grown per word."""
    item = _mg("m", "marker_text", 0, 3.2, text="You should be *required*",
               style="underline", accent="#FF3B30")
    timed = dict(item, reading={"v": 1, "rows": [[0.0, 0.8, 1.2, 1.6]], "bridges": []})
    probe = ("() => { const b = document.querySelector('.mg-backing');"
             " const l = document.querySelector('.wrap .line');"
             " return {box: b ? [b.getBoundingClientRect().left, b.getBoundingClientRect().width] : null,"
             " ink: getComputedStyle(l).color}; }")
    first, done = asyncio.run(_eval(timed, [0.4, 2.6], probe, _plate(225)))
    assert first["box"] is None and done["box"] is None
    assert first["ink"] == done["ink"] == "rgb(20, 20, 20)"
    cols, rows = 18, 32
    split = {"c": cols, "r": rows, "s": [{"t": 0.5, "g": plate_mod.encode_grid(
        [235 if c < cols // 2 else 20 for _r in range(rows) for c in range(cols)])}]}
    dim = dict(timed, params=dict(timed["params"], color="#999999"))
    first, done = asyncio.run(_eval(dim, [0.4, 2.6], probe, split))
    assert first["box"] and done["box"]
    assert abs(first["box"][0] - done["box"][0]) < 0.5 and abs(first["box"][1] - done["box"][1]) < 0.5


def test_a_hero_under_a_camera_zoom_is_narrowed_to_stay_inside_the_frame(probe, monkeypatch):
    # composited before the zoom stage: a 1.12x step punch-in over its window
    # would push a 94%-wide word past both sides of the frame
    _fake_matte(monkeypatch)
    edl = _edl()
    edl["effects"] = dict(edl.get("effects") or {}, zooms=[
        {"id": "z5", "start": 19.5, "end": 22.0, "strength": 0.12, "cy": 0.3, "ramp_s": 0.0}])
    ctx = _Ctx(edl)
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 20.0, 21.5, id="h",
                                          params={"text": "*enough*", "tier": "hero"})
    it = ctx.item("h")
    assert it["layer"] == "behind_subject" and "zooms to 1.12x" in out, out
    cam = motion_tools._camera_at(ctx, ctx.latest_edl()["json"])
    z, x0, _y0 = cam(20.7)
    band = motion_tools.HERO_BAND * it["params"]["width"] / 0.85
    left, right = it["params"]["x"] - band / 2, it["params"]["x"] + band / 2
    assert (left - x0) * z >= motion_tools.HERO_EDGE - 1e-3
    assert (right - x0) * z <= 1 - motion_tools.HERO_EDGE + 1e-3
    # the estimate the browserless lanes use narrows with it
    est = keepout.nominal_ink("word_slam", motion_templates.spec("word_slam"), it["params"])
    assert est[2] - est[0] == pytest.approx(band, abs=0.01)
    # no zoom: the full band, untouched
    ctx2 = _Ctx(_edl())
    out = motion_tools.add_motion_graphic(ctx2, "word_slam", 20.0, 21.5, id="h",
                                          params={"text": "*enough*", "tier": "hero"})
    assert ctx2.item("h")["params"]["width"] == 0.85 and "zooms" not in out


@needs_browser
def test_an_accent_on_a_plate_brighter_than_it_is_never_washed_out():
    # a yellow hero word over a lit projector screen: lifting it toward white
    # only loses contrast (round 4 review: ENOUGH rendered #FFE7AB)
    slam = _mg("s", "word_slam", 0, 1.5, text="*enough*", role="condensed", accent="#FFC940")
    acc = "() => document.querySelector('.line .mg-w').style.color"
    assert asyncio.run(_eval(slam, [1.0], acc, _plate(225)))[0] in ("#FFC940",
                                                                    "rgb(255, 201, 64)")


def test_band_graphics_can_shrink_into_the_band_above_the_larger_archival_card():
    """Integration (panels + type system): the archival card (y .27-.70)
    leaves a ~13% headline band; a short slam with its kicker and a counter
    with its label must be able to narrow into it (word_slam width down to
    0.3, counter size down to 0.4), as the band NOTE tells the agent to."""
    assert motion_templates.spec("word_slam")["params"]["width"]["min"] <= 0.3
    assert motion_templates.spec("counter")["params"]["size"]["min"] <= 0.4
    slam = motion_templates.check_params("word_slam", {"text": "*Lisa*", "width": 0.3,
                                                      "kicker": "Apple's 1983 computer"})
    assert slam["width"] == pytest.approx(0.3)
    ctr = motion_templates.check_params("counter", {"value": "30–40", "size": 0.4,
                                                   "label": "fonts on the screen"})
    assert ctr["size"] == pytest.approx(0.4)
    box = keepout.nominal_ink("counter", motion_templates.spec("counter"), dict(ctr, y=0.19),
                              (1080, 1920))
    assert box[3] - box[1] < 0.13
