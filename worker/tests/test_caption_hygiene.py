"""Caption hygiene and mute integrity (showcase judging, Oct 2026).

What is pinned here:
  1. Whisper's split-off tails ("non" + "-player", "32" + "%") are rejoined
     into one written word before grouping, in BOTH caption paths (libass
     presets and the browser motion looks), keeping timing and emphasis.
  2. A line whose words are all spoken clears ON the next program cut: a
     hold never survives a jump cut onto the new framing (motion looks and
     design-v2 libass tracks; historical tracks keep their bytes).
  3. Motion-look word reveals are crisp: legible on the first frame,
     settled within two.
  4. add/set_motion_graphic NOTE (never reject) a caption mute over speech
     the graphic does not carry, and a kicker/label that paraphrases a
     transcript phrase it could have quoted.
"""

import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import captions  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402


def _w(rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


# ── 1. split tokens ──────────────────────────────────────────────────────

def test_split_tails_rejoin_into_the_written_word():
    out = captions.rejoin_split_words(_w([
        ("their", 0.0, 0.2), ("non", 0.2, 0.5), ("-player", 0.5, 0.76),
        ("colleagues.", 0.76, 1.2), ("made", 1.3, 1.5), ("32", 1.5, 1.9),
        ("%", 1.9, 2.3), ("fewer", 2.3, 2.6), ("COVID", 2.7, 3.0),
        ("-19", 3.0, 3.3), ("3", 3.4, 3.5), (".5", 3.5, 3.7),
        ("and", 3.8, 3.9), ("/or", 3.9, 4.1)]))
    assert [w["w"] for w in out] == ["their", "non-player", "colleagues.", "made",
                                     "32%", "fewer", "COVID-19", "3.5", "and/or"]
    joined = out[1]
    assert (joined["t0"], joined["t1"]) == (0.2, 0.76)
    assert joined["parts"] == ["non", "-player"]


def test_only_genuine_continuations_rejoin():
    out = captions.rejoin_split_words(_w([
        ("is", 0.0, 0.2), ("-5", 0.2, 0.5),             # a spoken minus stays apart
        ("well,", 0.6, 0.9), ("-player", 0.9, 1.2),      # punctuation ends the word
        ("non", 1.3, 1.5), ("-stop", 1.8, 2.1),          # a real pause between them
        ("about", 2.2, 2.4), ("%", 2.4, 2.6)]))          # % only after a number
    assert [w["w"] for w in out] == ["is", "-5", "well,", "player", "non", "stop",
                                     "about", "%"]
    # an unjoinable hyphen-led token never starts a line with "-"
    assert out[3]["t0"] == 0.9 and out[3]["parts"] == ["-player"]
    brk = _w([("non", 0.0, 0.2), ("-player", 0.2, 0.4)])
    brk[1]["brk"] = True                                  # an insert sits between them
    assert [w["w"] for w in captions.rejoin_split_words(brk)] == ["non", "player"]


def test_a_clean_transcript_is_returned_untouched():
    words = _w([("a", 0.0, 0.1), ("well-known", 0.1, 0.5), ("fact.", 0.5, 0.9)])
    assert captions.rejoin_split_words(words) is words
    assert not captions.has_split_words(words)
    assert captions.has_split_words(_w([("non", 0, .2), ("-player", .2, .4)]))


ELON = [("better", 0.0, 0.3), ("than", 0.3, 0.6), ("their", 0.6, 0.8),
        ("non", 0.8, 1.1), ("-player", 1.1, 1.36), ("colleagues.", 1.36, 1.8),
        ("They", 2.4, 2.6), ("made", 2.6, 2.8), ("32", 2.8, 3.2), ("%", 3.2, 3.6),
        ("fewer", 3.6, 3.9), ("errors.", 3.9, 4.4)]


def _caption_edl(style, dur=6.0, keep=None, emphasis=None, design_version=2):
    edl = default_edl(dur)
    if keep:
        edl["keep"] = keep
    edl["captions"] = {"mode": "from_transcript", "style": style,
                       "emphasis_words": emphasis}
    if design_version:
        edl["captions"]["design_version"] = design_version
    return validate_edl(edl, dur).model_dump()


def _texts(events):
    import re
    return [re.sub(r"\{[^}]*\}", "", ev["text"]).replace("\\N", " ") for ev in events]


@pytest.mark.parametrize("style", [{"preset": "stacked"}, {"preset": "karaoke"},
                                   {"dynamic": True}, {}])
def test_libass_captions_never_show_a_split_tail(style):
    edl = _caption_edl(style)
    events, _ = captions.compiled_events(edl, {"words": _w(ELON)}, Timeline(edl["keep"]),
                                         (1080, 1920))
    texts = [t.lower() for t in _texts(events)]
    assert any("non-player" in t for t in texts), texts
    assert not any(t.lstrip().startswith("-") or " -" in t or t.strip() == "%"
                   for t in texts), texts
    assert any("32%" in t for t in texts), texts


@pytest.mark.parametrize("look", ["editorial", "clean", "pop"])
def test_motion_captions_carry_the_rejoined_word_with_its_emphasis(look):
    edl = _caption_edl({"motion_look": look}, emphasis=["player"])
    cues = motion_captions.cues(edl, {"words": _w(ELON)}, Timeline(edl["keep"]))
    words = [w for c in cues for w in c["w"]]
    texts = [w["t"] for w in words]
    assert "non-player" in texts and "32%" in texts, texts
    assert not any(t.startswith("-") or t == "%" for t in texts), texts
    np = next(w for w in words if w["t"] == "non-player")
    assert (np["s"], np["e"]) == (0.8, 1.36)       # timed across both halves
    assert np["x"] == 1                            # emphasis naming a half still lands
    assert next(w for w in words if w["t"] == "32%")["x"] == 1


def test_premium_emphasis_finds_a_rejoined_word():
    chunk = captions.rejoin_split_words(_w([("non", 0, .2), ("-player", .2, .4)]))
    p = captions._preset_of(captions._norm_style({"preset": "stacked"}))
    treats, _ = captions._assign_treatments(chunk, {"non"}, p, {}, 0)
    assert treats[0] is not None


def test_caption_fingerprint_moves_only_for_transcripts_with_split_tails():
    import renderer
    edl = {"captions": {"mode": "from_transcript"}}
    clean = {"words": _w([("a", 0, .1), ("b", .1, .2)])}
    split = {"words": _w(ELON)}
    salted = renderer._caption_index_fp(edl, split)
    import hashlib
    h = hashlib.sha256()
    h.update(b"caption-compiler:program-end-clamp-v1;")
    for w in split["words"]:
        h.update(f"{w['w']}|{w['t0']}|{w['t1']};".encode())
    assert salted != h.hexdigest()[:16]               # a cached "-player" render busts
    h = hashlib.sha256()
    h.update(b"caption-compiler:program-end-clamp-v1;")
    for w in clean["words"]:
        h.update(f"{w['w']}|{w['t0']}|{w['t1']};".encode())
    assert renderer._caption_index_fp(edl, clean) == h.hexdigest()[:16]   # others keep theirs


# ── 2. lines clear on the cut ────────────────────────────────────────────

# Source: "...some great companies." ends at 1.7; the keep jumps 2.0 -> 3.0,
# and the next sentence starts 0.15 s into the new shot (program 2.15) —
# close enough that the old hold ran straight across the cut.
CUT_WORDS = [("we", 0.6, 0.8), ("built", 0.8, 1.0), ("some", 1.0, 1.2),
             ("great", 1.2, 1.4), ("companies.", 1.4, 1.7),
             ("but", 3.15, 3.4), ("not", 3.4, 3.6), ("enough", 3.6, 4.1)]
KEEP = [[0.0, 2.0], [3.0, 6.0]]


def test_program_cuts_are_shot_changes_only():
    assert captions.program_cuts(Timeline(KEEP)) == [2.0]
    # a source-contiguous split (made for an insert that was later removed,
    # or a speed ramp) is the same shot
    assert captions.program_cuts(Timeline([[0.0, 2.0], [2.0, 6.0]])) == []
    ins = [{"id": "i1", "asset_key": "k", "kind": "image", "at_output_s": 2.0,
            "duration_s": 1.0}]
    assert captions.program_cuts(Timeline([[0.0, 2.0], [2.0, 6.0]], ins)) == [2.0, 3.0]


def test_motion_cue_hold_ends_on_the_cut_and_clears_hard():
    edl = _caption_edl({"motion_look": "editorial"}, keep=KEEP)
    tl = Timeline(edl["keep"])
    cues = motion_captions.cues(edl, {"words": _w(CUT_WORDS)}, tl)
    held = next(c for c in cues if c["w"][-1]["t"] == "companies")
    assert held["e"] == 2.0 and held["k"] == 1, held
    nxt = next(c for c in cues if c["s"] > 2.0)
    assert nxt["s"] == pytest.approx(2.15)


def test_a_line_still_speaking_runs_on_across_a_cut():
    words = [("one", 1.5, 1.8), ("long", 1.8, 1.98), ("line", 3.0, 3.3),
             ("continues", 3.3, 3.8)]
    edl = _caption_edl({"motion_look": "editorial"}, keep=KEEP)
    cues = motion_captions.cues(edl, {"words": _w(words)}, Timeline(edl["keep"]))
    spans = [c for c in cues if c["s"] < 2.0 < c["e"]]
    assert spans and spans[0]["w"][-1]["e"] > 2.0     # its words cross the cut


def test_the_one_frame_lead_never_crosses_a_cut(monkeypatch):
    monkeypatch.setattr(motion_engine, "available", lambda: True)
    words = CUT_WORDS[:5] + [("but", 3.02, 3.4), ("not", 3.4, 3.6), ("enough", 3.6, 4.1)]
    edl = _caption_edl({"motion_look": "editorial"}, keep=KEEP)
    tl = Timeline(edl["keep"])
    items = motion_captions.items(edl, {"words": _w(words)}, tl)
    absolute = [(it["start"] + c["s"], it["start"] + c["e"], c)
                for it in items for c in it["params"]["cues"]]
    before = next(a for a in absolute if a[2]["w"][-1]["t"] == "companies")
    after = next(a for a in absolute if a[2]["w"][0]["t"] == "but")
    assert before[1] == pytest.approx(2.0, abs=1e-3)   # clears ON the cut, not a frame early
    assert after[0] == pytest.approx(2.0, abs=1e-3)    # appears with the new shot
    # away from cuts the lead is unchanged
    first = absolute[0]
    assert first[0] == pytest.approx(0.6 - motion_captions.CAPTION_LEAD_S, abs=1e-3)


@pytest.mark.parametrize("style", [{"preset": "stacked"}, {"preset": "karaoke"},
                                   {"dynamic": True}, {"preset": "documentary"}])
def test_v2_libass_lines_clear_on_the_cut(style):
    edl = _caption_edl(style, keep=KEEP)
    tl = Timeline(edl["keep"])
    events, _ = captions.compiled_events(edl, {"words": _w(CUT_WORDS)}, tl, (1080, 1920))
    assert events
    for ev in events:
        if ev["start"] < 2.0:
            assert ev["end"] <= 2.0 + 1e-6, (style, ev)


def test_plain_v2_captions_floor_never_crosses_the_cut():
    # a lone short line just before the cut: MIN_EVENT_S used to carry it over
    edl = _caption_edl({}, keep=KEEP)
    words = [("Yes.", 1.8, 1.9), ("Then", 4.5, 4.8), ("more.", 4.8, 5.2)]
    events, _ = captions.compiled_events(edl, {"words": _w(words)}, Timeline(edl["keep"]),
                                         (1080, 1920))
    yes = next(ev for ev in events if "Yes" in ev["text"])
    assert yes["end"] == pytest.approx(2.0), events


def test_historical_tracks_keep_their_holds():
    edl = _caption_edl({"preset": "stacked"}, keep=KEEP, design_version=None)
    tl = Timeline(edl["keep"])
    events, _ = captions.compiled_events(edl, {"words": _w(CUT_WORDS)}, tl, (1080, 1920))
    assert any(ev["start"] < 2.0 < ev["end"] for ev in events)


# ── 4. caption integrity notes ───────────────────────────────────────────

PUNCH = [("they", 0.2, 0.4), ("promised", 0.4, 0.8), ("us", 0.8, 0.95),
         ("flying", 1.0, 1.4), ("cars", 1.4, 1.8), ("and", 1.8, 1.9),
         ("all", 1.9, 2.05), ("we", 2.05, 2.2), ("got", 2.2, 2.45),
         ("was", 2.45, 2.7), ("140", 2.8, 3.3), ("characters.", 3.3, 3.9),
         ("So", 4.6, 4.7), ("that", 4.7, 4.8), ("we", 4.8, 4.9), ("can", 4.9, 5.0),
         ("deal", 5.0, 5.3), ("in", 5.3, 5.4), ("pictures.", 5.4, 6.0)]


class _Ctx:
    project_id = 1

    def __init__(self, words=PUNCH, dur=8.0, captions_on=True):
        self.duration = dur
        edl = default_edl(dur)
        if captions_on:
            edl["captions"] = {"mode": "from_transcript", "design_version": 2,
                               "style": {"motion_look": "editorial"}}
        self._edl = validate_edl(edl, dur).model_dump()
        self.index = {"video": {"width": 1080, "height": 1920}, "words": _w(words)}
        self.writes = []

    def latest_edl(self):
        return {"version": len(self.writes) + 1, "json": self._edl}

    def write_edl(self, edl, description):
        self._edl = validate_edl(edl, self.duration).model_dump()
        self.writes.append(description)
        return f"EDL v{len(self.writes)} -> v{len(self.writes) + 1}: {description}"


@pytest.fixture
def no_probe(monkeypatch):
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W, H, fps=30.0:
                        {"errors": [], "visible_frames": 4, "samples": 4,
                         "bboxes": [[.1, .5, .9, .7]]})


def test_a_mute_over_the_setup_names_the_words_that_would_vanish(no_probe):
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(
        ctx, "counter", 1.0, 3.9, params={"value": "140", "label": "*characters*"},
        mute_captions=True, id="num")
    assert out.startswith("EDL v1"), out                       # a NOTE, never a rejection
    assert "NOTE (captions)" in out and '"flying cars and all we got was"' in out, out
    assert "mute_captions=false" in out and "start it at 2.80s" in out, out
    assert ctx.latest_edl()["json"]["motion"][0]["mute_captions"] is True
    # keeping the captions silences the note
    out = motion_tools.set_motion_graphic(ctx, "num", mute_captions=False)
    assert "NOTE (captions)" not in out, out
    # ...and so does a graphic that carries the words being spoken
    out = motion_tools.set_motion_graphic(
        ctx, "num", start=2.8, mute_captions=True)
    assert "NOTE (captions)" not in out, out


def test_a_template_default_mute_is_checked_too(no_probe):
    assert motion_templates.spec("word_slam").get("mutes_captions")
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(ctx, "word_slam", 1.0, 2.7,
                                          params={"text": "*rockets*"})
    assert "NOTE (captions)" in out and "flying cars" in out, out
    out = motion_tools.add_motion_graphic(_Ctx(), "word_slam", 1.0, 1.8,
                                          params={"text": "flying *cars*"})
    assert "NOTE (captions)" not in out, out


def test_no_mute_note_without_transcript_captions(no_probe):
    ctx = _Ctx(captions_on=False)
    out = motion_tools.add_motion_graphic(
        ctx, "counter", 1.0, 3.9, params={"value": "140"}, mute_captions=True)
    assert "NOTE (captions)" not in out, out


def test_a_paraphrased_kicker_is_flagged_with_the_exact_phrase(no_probe):
    ctx = _Ctx()
    out = motion_tools.add_motion_graphic(
        ctx, "word_slam", 5.3, 6.0, params={"text": "*pictures*", "kicker": "so we can deal in"})
    assert 'kicker "so we can deal in" paraphrases' in out, out
    assert 'kicker="So that we can deal in"' in out, out
    out = motion_tools.add_motion_graphic(
        ctx, "word_slam", 5.3, 6.0, params={"text": "*pictures*", "kicker": "so that we can deal in"})
    assert "paraphrases" not in out, out
    # a deliberate summary is not a paraphrase of any one phrase
    out = motion_tools.add_motion_graphic(
        ctx, "hook_title", 0.0, 1.0, params={"text": "Tech stalled / since *1969*"},
        mute_captions=False)
    assert "NOTE (captions)" not in out, out


def test_a_quote_may_keep_a_word_the_cut_clipped(no_probe):
    # "around" straddles the cut (its middle was removed): captions drop it,
    # but it is still heard, so quoting it is verbatim, not a paraphrase.
    words = [("a", 0.2, 0.3), ("narrow", 0.3, 0.7), ("cone", 0.7, 1.1),
             ("around", 1.9, 2.6), ("the", 3.1, 3.2), ("world", 3.2, 3.5),
             ("of", 3.5, 3.6), ("bits.", 3.6, 4.0)]
    ctx = _Ctx(words=words)
    ctx._edl["keep"] = [[0.0, 2.0], [2.9, 8.0]]
    out = motion_tools.add_motion_graphic(
        ctx, "marker_text", 0.2, 3.0, params={"text": "a narrow cone around the world of bits"},
        mute_captions=False)
    assert "paraphrases" not in out, out
    out = motion_tools.add_motion_graphic(
        ctx, "marker_text", 0.2, 3.0, params={"text": "a narrow cone across the world of bits"},
        mute_captions=False)
    assert "paraphrases" in out, out


def test_comparison_tokens_fold_case_plurals_and_number_words():
    assert motion_tools._tokens("This does *Characters* / it's FORTY 32%") == \
        ["this", "does", "character", "it's", "40", "32"]


def test_the_check_never_breaks_a_write(no_probe, monkeypatch):
    ctx = _Ctx()

    def boom(*_a, **_k):
        raise RuntimeError("index exploded")
    monkeypatch.setattr(captions, "transcript_words", boom)
    out = motion_tools.add_motion_graphic(ctx, "counter", 1.0, 3.9,
                                          params={"value": "140"}, mute_captions=True)
    assert out.startswith("EDL v1") and "NOTE (captions)" not in out, out


# ── 3. crisp reveals (pixels) ────────────────────────────────────────────

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

_STATE = """() => Array.from(document.querySelectorAll('.cue.on > .blk .mg-w')).map(e => {
    const cs = getComputedStyle(e);
    return [e.textContent, +cs.opacity, cs.transform, cs.filter]; })"""
_BLOCK = """() => { const b = document.querySelector('.cue.on > .blk');
    return b ? +getComputedStyle(b).opacity : null; }"""


async def _dom(job, times, script):
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
        await browser.close()
    return out


def _settled(transform, filt):
    return transform in ("none", "matrix(1, 0, 0, 1, 0, 0)") and filt in ("none", "blur(0px)")


@needs_browser
@pytest.mark.parametrize("look", ["clean", "serif"])
def test_rise_reveals_are_legible_at_once_and_settled_in_two_frames(look, tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    words = [("One", 0.40, 0.70), ("word", 1.00, 1.30), ("lands.", 1.60, 2.20)]
    edl = _caption_edl({"motion_look": look}, dur=4.0)
    item = motion_captions.items(edl, {"words": _w(words)}, Timeline(edl["keep"]))[0]
    job = motion_templates.build_job(item, 540, 960, 30)
    w = item["params"]["cues"][0]["w"][1]                  # "word", mid-phrase
    f = 1 / 30
    early, first, second, third = asyncio.run(_dom(
        job, [w["s"] - f + 1e-3, w["s"] + 1e-3, w["s"] + f + 1e-3, w["s"] + 2 * f + 1e-3],
        _STATE))
    assert {r[0]: r for r in early}["word"][1] == 0.0, early    # no pre-roll smear
    assert {r[0]: r for r in first}["word"][1] >= 0.7, first    # legible on its first frame
    assert {r[0]: r for r in second}["word"][1] == 1.0, second
    st = {r[0]: r for r in third}
    assert _settled(st["word"][2], st["word"][3]), third        # settled by the third


@needs_browser
def test_a_line_ending_on_a_cut_is_solid_until_the_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(motion_engine, "available", lambda: True)
    edl = _caption_edl({"motion_look": "clean"}, keep=KEEP)
    tl = Timeline(edl["keep"])
    item = motion_captions.items(edl, {"words": _w(CUT_WORDS)}, tl)[0]
    job = motion_templates.build_job(item, 540, 960, 30)
    cut = 2.0 - item["start"]
    before, after = asyncio.run(_dom(job, [cut - 0.04, cut + 0.01], _BLOCK))
    assert before == 1.0                                   # no fade into the cut
    assert after is None                                   # and gone on the new shot
