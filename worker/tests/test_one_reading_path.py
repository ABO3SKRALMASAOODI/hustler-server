"""Text fidelity and one reading path (showcase judging, Oct 2026, round 3).

What is pinned here:
  1. A word whose midpoint a pause cut removed, but whose SOUND is in the
     kept footage, is captioned where its voice is (whisper hands the pause
     to the word after it: the Jobs hook lost "has", "a", "type"). With the
     index's speech envelope the voice decides; without one a long word's
     voice is its end. A word whose middle really was clipped stays out.
  2. The caption chunker keeps names and noun phrases on one card ("the
     Green / Revolution agriculture" was garbage).
  3. A graphic that shows a phrase owns it from its first shown word to its
     exit: the captions yield there (no two texts at once), the words said
     before it stay captioned and clear as it lands, a lockup (phrase_build)
     sets the phrase's other words in small type on their onsets, any other
     graphic leaves them to the sound and its write NOTE and the sound-off
     audit say so.
  4. phrase_build reveals in reading order: spoken rows on their spoken
     onsets, unspoken rows on their 'at' with the stagger squeezed so a row
     is complete before the next one starts ("GARBAGE" never before "were").
"""

import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import caption_carry  # noqa: E402
import captions  # noqa: E402
import config  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_layer  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
import renderer  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline, Voice  # noqa: E402

# The Jobs hook as whisper timed it (source seconds): the pauses before
# "has", "a" and "type" are inside those words' intervals.
JOBS = [("Every", 1691.74, 1692.0), ("computer", 1692.0, 1692.4), ("to", 1692.4, 1692.64),
        ("date", 1692.64, 1692.96), ("has", 1692.96, 1694.1), ("used", 1694.1, 1694.72),
        ("a", 1694.72, 1695.28), ("weird", 1695.28, 1695.92), ("type", 1695.92, 1697.32),
        ("on", 1697.32, 1697.58), ("the", 1697.58, 1697.7), ("screen,", 1697.7, 1698.02)]
# ...and the pause cut the showcase made of it
JOBS_KEEP = [[1691.7, 1693.12], [1693.96, 1694.72], [1695.12, 1696.12], [1697.06, 1698.1]]
# where the voice really is (measured on the source audio)
JOBS_VOICE = [(1691.74, 1692.93), (1693.96, 1694.62), (1695.12, 1695.95), (1697.12, 1697.33),
              (1697.36, 1698.0)]


def _w(rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


def _voice(spans, upto, fps=8.613):
    """A perception sidecar whose speech envelope is loud over ``spans``."""
    n = int(upto * fps) + 2
    env = [0.02] * n
    for a, b in spans:
        for k in range(n):
            s = k / fps + 1024 / 22050
            if s < b and s + 1 / fps > a:
                env[k] = 0.8
    return {"vb_env": env, "vb_env_fps": fps}


def _edl(keep, motion=(), look="editorial", words=None, **caps):
    dur = max(b for _a, b in keep) + 5
    edl = default_edl(dur)
    edl["keep"] = [list(k) for k in keep]
    edl["captions"] = dict({"mode": "from_transcript", "design_version": 2,
                            "style": {"motion_look": look} if look else {"preset": "stacked"}},
                           **caps)
    edl["motion"] = [dict(m) for m in motion]
    return validate_edl(edl, dur).model_dump()


def _index(words, voice=None):
    ix = {"video": {"width": 1080, "height": 1920}, "words": _w(words)}
    if voice:
        ix["perception"] = voice
    return ix


# ── 1. words a cut kept the sound of ─────────────────────────────────────

def test_a_pause_cut_keeps_every_word_whose_voice_survives():
    tl = Timeline(JOBS_KEEP)
    old = [w["w"] for w in tl.kept_words(_w(JOBS))]
    assert old == ["Every", "computer", "to", "date", "used", "weird", "on", "the", "screen,"]
    heard = tl.kept_words(_w(JOBS), rescue=True)
    assert [w["w"] for w in heard] == [w for w, _a, _b in JOBS]
    by = {w["w"]: w for w in heard}
    # placed in the kept part that holds the voice: "type" after the cut, not before
    assert by["has"]["src_t0"] >= 1693.96 and by["type"]["src_t0"] >= 1697.06
    assert by["has"]["heard"] and "heard" not in by["used"]
    # program order holds, and a rescued word never overlaps its neighbours
    for a, b in zip(heard, heard[1:]):
        assert a["t0"] <= b["t0"] + 1e-9


def test_the_speech_envelope_puts_the_word_where_its_voice_is():
    voice = Voice.from_index(_index(JOBS, _voice(JOBS_VOICE, 1700)))
    tl = Timeline(JOBS_KEEP)
    by = {w["w"]: w for w in tl.kept_words(_w(JOBS), rescue=True, voice=voice)}
    assert set(by) == {w for w, _a, _b in JOBS}
    assert by["type"]["src_t0"] == pytest.approx(1697.12, abs=0.12)
    assert by["has"]["src_t0"] == pytest.approx(1693.96, abs=0.12)


def test_a_word_whose_voice_was_cut_stays_out():
    # "say" ends 60 ms into the keep: a sliver of it, not the word
    words = [("say", 845.70, 845.84), ("the", 845.84, 846.22), ("1960s", 846.22, 847.26)]
    tl = Timeline([[845.78, 850.0]])
    voice = Voice.from_index(_index(words, _voice([(845.66, 845.84), (845.9, 847.2)], 860)))
    assert [w["w"] for w in tl.kept_words(_w(words), rescue=True, voice=voice)] == \
        ["the", "1960s"]
    assert [w["w"] for w in tl.kept_words(_w(words), rescue=True)] == ["the", "1960s"]
    # a normal-length word whose middle a cut took is clipped, not heard
    clipped = [("design", 10.0, 10.4)]
    assert Timeline([[10.3, 12.0]]).kept_words(_w(clipped), rescue=True) == []
    # an envelope that hears the word only inside the cut drops it...
    long = [("type", 20.0, 21.4)]
    tl = Timeline([[19.0, 20.1], [21.2, 22.0]])
    v = Voice.from_index(_index(long, _voice([(20.4, 20.75)], 30)))
    assert tl.kept_words(_w(long), rescue=True, voice=v) == []
    # ...and one that hears it in a kept part places it there
    v = Voice.from_index(_index(long, _voice([(21.25, 21.45)], 30)))
    got = tl.kept_words(_w(long), rescue=True, voice=v)
    assert got and got[0]["src_t0"] >= 21.2


def test_captions_and_the_kept_transcript_show_the_heard_words():
    edl = _edl(JOBS_KEEP)
    ix = _index(JOBS)
    tl = Timeline(edl["keep"])
    words = [w["w"] for w in captions.caption_words(edl, ix, tl)]
    assert words[:9] == ["Every", "computer", "to", "date", "has", "used", "a", "weird", "type"]
    cues = motion_captions.cues(edl, ix, tl)
    said = " ".join(w["t"] for c in cues for w in c["w"])
    assert "has used" in said and "a weird" in said and "type on the" in said
    # heard words are not "clipped" for a graphic quoting the line
    hw = {w["w"]: w for w in captions.heard_words(edl, ix, tl, 0.0, 10.0)}
    assert not hw["has"]["clipped"]


def test_the_caption_fingerprint_moves_only_where_a_word_is_rescued():
    edl = _edl(JOBS_KEEP)
    ix = _index(JOBS)
    fp = renderer._caption_index_fp(edl, ix)
    whole = _edl([[1691.7, 1698.1]])
    assert renderer._caption_index_fp(whole, ix) != fp
    # no rescue: the fingerprint is exactly what it was before (renders keep their cache)
    import hashlib
    h = hashlib.sha256()
    h.update(b"caption-compiler:program-end-clamp-v1;")
    for w in ix["words"]:
        h.update(f"{w.get('w', '')}|{w.get('t0')}|{w.get('t1')};".encode("utf-8"))
    assert renderer._caption_index_fp(whole, ix) == h.hexdigest()[:16]
    assert captions.rescued_words(ix, Timeline(edl["keep"])) and \
        not captions.rescued_words(ix, Timeline(whole["keep"]))


# ── 2. names and noun phrases on one card ────────────────────────────────

def _cards(text, mx=5, chars=26, target=3):
    ws = [{"w": w, "t0": 0.35 * i, "t1": 0.35 * i + 0.3} for i, w in enumerate(text.split())]
    p = {"mode": "reveal", "max_words": mx, "target_words": target, "max_chunk_s": 2.2}
    return [" ".join(w["w"] for w in ch) for ch in captions._chunk_region_v2(ws, mx, chars, p)]


def test_a_card_change_never_splits_a_name():
    for text, name in (("aviation and the Green Revolution agriculture and", "Green Revolution"),
                       ("I met Steve Jobs at the Apple campus in New York City last year",
                        "Steve Jobs"),
                       ("I met Steve Jobs at the Apple campus in New York City last year",
                        "New York City")):
        assert any(name in c for c in _cards(text)), (_cards(text), name)


def test_names_numbers_and_determiners_glue_softly():
    g = captions._glue
    w = lambda t: {"w": t}  # noqa: E731
    assert g(w("Green"), w("Revolution")) == captions.GLUE_NAME
    assert g(w("Revolution"), w("agriculture")) == captions.GLUE_NAME_TAIL
    assert g(w("140"), w("characters")) == captions.GLUE_NUMBER
    assert g(w("every"), w("computer")) == captions.GLUE_DETERMINER
    assert g(w("supersonic"), w("aviation")) == captions.GLUE_MODIFIER
    assert g(w("weird"), w("type"), w("a")) == captions.GLUE_MODIFIER
    # punctuation, function words and a sentence's own capital stay free
    assert g(w("Lisa,"), w("totally")) == 0.0
    assert g(w("Green"), w("and")) == 0.0
    assert g(w("And"), w("Steve")) == 0.0
    assert g(w("I"), w("Think")) == 0.0


# ── 3. one reading path ──────────────────────────────────────────────────

# "there is going to be no college student three or four years from now
# that's ever going to think of writing a paper without one of these things."
PAPER = [("there", 0.0, 0.2), ("is", 0.22, 0.38), ("going", 0.38, 0.46), ("to", 0.46, 0.54),
         ("be", 0.54, 0.7), ("no", 0.7, 0.92), ("college", 0.92, 1.28), ("student", 1.28, 1.6),
         ("three", 1.6, 1.8), ("or", 1.8, 1.9), ("four", 1.9, 2.02), ("years", 2.02, 2.2),
         ("from", 2.2, 2.36), ("now", 2.36, 2.48), ("that's", 2.48, 2.62), ("ever", 2.62, 2.76),
         ("going", 2.76, 2.86), ("to", 2.86, 3.0), ("think", 3.0, 3.14), ("of", 3.14, 3.28),
         ("writing", 3.28, 3.5), ("a", 3.5, 3.66), ("paper", 3.66, 3.9), ("without", 3.9, 4.2),
         ("one", 4.2, 4.36), ("of", 4.36, 4.46), ("these", 4.46, 4.58), ("things.", 4.58, 5.04),
         ("And", 5.6, 5.8), ("so", 5.8, 6.0), ("on.", 6.0, 6.4)]
PAPER_ROWS = [{"text": "no college student", "role": "sans", "size": "0.6", "at": "0.7"},
              {"text": "writing a paper", "role": "serif", "size": "0.8", "at": "3.28"},
              {"text": "WITHOUT *ONE*", "role": "condensed", "size": "1.4", "at": "3.9"}]


def _paper(start=0.0, end=5.3, **kw):
    m = {"id": "paper", "template": "phrase_build", "start": start, "end": end,
         "params": {"rows": PAPER_ROWS, "y": 0.145},
         "footprint": {"box": [0.1, 0.06, 0.9, 0.26], "ar": round(1080 / 1920, 4), "faces": []}}
    m.update(kw)
    return m


def test_a_lockup_owns_its_phrase_and_sets_the_words_its_rows_leave_out():
    edl = _edl([[0.0, 7.0]], [_paper()], words=PAPER)
    ix = _index(PAPER)
    tl = Timeline(edl["keep"])
    p = captions.caption_plan(edl, ix, tl)
    shown = [w["w"] for w in p.caption_words()]
    # the setup before its first shown word, and the next sentence, stay captioned
    assert shown == ["there", "is", "going", "to", "be", "And", "so", "on."]
    rep = p.report["paper"]
    assert rep["owns_from"] == pytest.approx(0.7)
    assert [caption_carry._said(r) for r in rep["joined"]] == [
        "three or four years from now that's ever going to think of", "of these things"]
    assert p.yield_spans == [[0.7, 5.3]]
    # the setup line clears as the lockup's first word lands (no two texts)
    cues = motion_captions.cues(edl, ix, tl)
    setup = [c for c in cues if c["s"] < 0.7]
    assert setup and all(c["e"] <= 0.7 + 1e-6 for c in setup)
    assert not any(0.7 <= c["s"] < 5.3 for c in cues)
    # the reading: rows on their onsets, bridges after the row before them
    rd = caption_carry.readings(edl, ix, tl)["paper"]
    assert rd["rows"] == [[0.7, 0.92, 1.28], [3.28, 3.5, 3.66], [3.9, 4.2]]
    assert [(b["after"], " ".join(w["t"] for w in b["words"]), b["words"][0]["s"])
            for b in rd["bridges"]] == [
        (0, "three or four years from now that's ever going to think of", 1.6),
        (2, "of these things", 4.36)]
    # the sound-off audit counts the set words as on screen
    assert not caption_carry.sound_off_gaps(edl, ix, tl)
    # the write NOTE says where captions yield and what the lockup sets
    notes = motion_tools._word_level_notes(edl, ix, tl, edl["motion"][0], canvas=(1080, 1920))
    assert any("One reading path" in n and "0.70s" in n and "of these things" in n
               for n in notes), notes


def test_a_graphic_that_is_no_lockup_leaves_the_rest_of_its_phrase_to_the_sound():
    words = [("but", 0.0, 0.2), ("it's", 0.2, 0.35), ("not", 0.62, 0.75),
             ("quite", 0.75, 0.9), ("been", 0.9, 1.05), ("enough", 1.3, 1.8),
             ("to", 1.9, 2.0), ("take", 2.0, 2.3), ("our", 2.3, 2.45),
             ("civilization", 2.45, 3.0), ("to", 3.0, 3.1), ("the", 3.4, 3.5),
             ("next", 3.5, 3.7), ("level.", 3.7, 4.1)]
    slam = {"id": "enough", "template": "word_slam", "start": 1.1, "end": 3.35,
            "params": {"text": "*enough*", "kicker": "it's not quite been"},
            "footprint": {"box": [0.1, 0.3, 0.9, 0.42], "ar": round(1080 / 1920, 4), "faces": []}}
    edl = _edl([[0.0, 6.0]], [slam], words=words)
    ix = _index(words)
    tl = Timeline(edl["keep"])
    shown = [w["w"] for w in captions.caption_words(edl, ix, tl)]
    assert shown == ["but", "it's", "the", "next", "level."]
    gaps = caption_carry.sound_off_gaps(edl, ix, tl)
    assert gaps and gaps[0]["owner"] == "enough" and "one reading path" in gaps[0]["cause"]
    notes = motion_tools._word_level_notes(edl, ix, tl, edl["motion"][0], canvas=(1080, 1920))
    assert any("never reads \"to take our civilization to\"" in n and "End it at 2.10s" in n
               for n in notes), notes
    # an explicit false keeps the captions running beside it (unchanged)
    edl = _edl([[0.0, 6.0]], [dict(slam, mute_captions=False)], words=words)
    assert "civilization" in [w["w"] for w in captions.caption_words(edl, ix, tl)]


def test_a_graphic_that_shows_no_spoken_words_owns_nothing():
    # a headline in the editor's words ("where did progress go?") never
    # takes the speech under it
    words = [("the", 0.1, 0.3), ("1960s", 0.3, 0.9), ("technology", 0.9, 1.5),
             ("meant", 1.5, 1.8), ("computers.", 1.8, 2.4)]
    hook = {"id": "hook", "template": "word_slam", "start": 0.0, "end": 2.3,
            "params": {"text": "where did *progress* go?"},
            "footprint": {"box": [0.1, 0.3, 0.9, 0.42], "ar": round(1080 / 1920, 4), "faces": []}}
    edl = _edl([[0.0, 4.0]], [hook], words=words)
    p = captions.caption_plan(edl, _index(words), Timeline(edl["keep"]))
    assert [w["w"] for w in p.caption_words()] == [w for w, _a, _b in words]
    assert p.report["hook"]["owns_from"] is None and not p.yield_spans


def test_phrases_end_at_sentences_clauses_and_jumps_not_commas():
    ws = [{"w": "computers,", "t0": 0.0, "t1": 0.3}, {"w": "internet,", "t0": 0.3, "t1": 0.6},
          {"w": "mobile.", "t0": 0.6, "t1": 0.9}, {"w": "It", "t0": 1.0, "t1": 1.1},
          {"w": "works;", "t0": 1.1, "t1": 1.4}, {"w": "then", "t0": 1.4, "t1": 1.6},
          {"w": "later", "t0": 1.6, "t1": 1.8, "src_t0": 30.0}]
    for w in ws:
        w.setdefault("src_t0", w["t0"])
        w["src_t1"] = w["src_t0"] + (w["t1"] - w["t0"])
    assert caption_carry.phrase_ids(ws) == [0, 0, 0, 1, 1, 2, 3]


def test_a_paraphrased_row_is_named_in_the_write_note():
    words = [("rockets", 0.2, 0.6), ("and", 0.6, 0.7), ("supersonic", 0.7, 1.2),
             ("aviation", 1.2, 1.7), ("and", 1.7, 1.8), ("new", 1.9, 2.1),
             ("medicines.", 2.1, 2.7)]
    rows = [{"text": "ROCKETS"}, {"text": "supersonic jets"}, {"text": "NEW *MEDICINES*"}]
    m = {"id": "list", "template": "phrase_build", "start": 0.0, "end": 3.0,
         "params": {"rows": rows}}
    edl = _edl([[0.0, 4.0]], [], words=words)

    class Ctx:
        index = _index(words)
        has_main_video = False
        workdir = "/tmp"

        def latest_edl(self):
            return {"json": edl}

        def write_edl(self, e, desc):
            edl.clear()
            edl.update(validate_edl(e, 9.0).model_dump())
            return "EDL v2: " + desc

        def proxy_path(self):
            return None
    out = motion_tools.add_motion_graphic(Ctx(), "phrase_build", 0.0, 3.0,
                                          params={"rows": rows}, id="list")
    assert "row 2 \"supersonic jets\" prints 'jets'" in out, out
    stored = edl["motion"][0]
    # the stored item carries its reading (rows on onsets, the bridge "aviation")
    assert stored["reading"]["rows"][0] == [0.2]
    assert [w["t"] for b in stored["reading"]["bridges"] for w in b["words"]] == ["aviation"]
    del m


def test_mute_true_and_false_keep_their_contracts_and_still_time_the_rows():
    for mute in (True, False):
        edl = _edl([[0.0, 7.0]], [_paper(mute_captions=mute)], words=PAPER)
        ix = _index(PAPER)
        tl = Timeline(edl["keep"])
        p = captions.caption_plan(edl, ix, tl)
        assert not p.yield_spans
        rd = caption_carry.readings(edl, ix, tl)["paper"]
        assert rd["rows"][0] == [0.7, 0.92, 1.28] and rd["bridges"] == []


def test_the_reading_is_stored_cleaned_and_handed_to_the_page():
    edl = _edl([[0.0, 7.0]], [_paper()], words=PAPER)
    ix = _index(PAPER)
    tl = Timeline(edl["keep"])
    motion_layer.fill_footprints(edl, 1080, 1920, 30.0, index=ix, tl=tl)
    item = edl["motion"][0]
    assert item["reading"]["bridges"]
    # a measured box drawn without its bridge lines is measured again
    assert item.get("footprint") is None or item["footprint"].get("estimated") or \
        motion_engine.available()
    again = validate_edl(edl, 20.0).model_dump()["motion"][0]["reading"]
    assert again == item["reading"]
    junk = validate_edl(dict(edl, motion=[dict(item, reading={"rows": "x"})]), 20.0)
    assert junk.model_dump()["motion"][0]["reading"] is None
    job = motion_templates.build_job(item, 1080, 1920, 30)
    assert '"_reading"' in job.html
    # a windowed item (a stitched piece) keeps the reading of its full program
    piece = dict(edl, keep=[[2.0, 7.0]],
                 motion=[dict(item, start=0.0, end=3.3, phase_s=2.0, full_duration_s=5.3)])
    caption_carry.attach_readings(piece, ix, Timeline(piece["keep"]))
    assert piece["motion"][0]["reading"] == item["reading"]


def test_renders_before_the_plan_are_stale_for_lockups_and_carried_captions():
    lockup = _edl([[0.0, 7.0]], [_paper()], look=None, words=PAPER)
    lockup["captions"] = None
    assert not renderer.carry_current({"carry_v": 1}, lockup)
    assert renderer.carry_current({"carry_v": config.CAPTION_CARRY_VERSION}, lockup)
    plain = _edl([[0.0, 7.0]], words=PAPER)
    plain["captions"] = None
    assert renderer.carry_current({}, plain)


# ── 4. phrase_build reveals in reading order ─────────────────────────────

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

_STATE = """() => [...document.querySelectorAll('.row')].map(r => ({
  bridge: r.classList.contains('bridge'),
  top: Math.round(r.getBoundingClientRect().top),
  fs: parseFloat(r.style.fontSize),
  words: [...r.querySelectorAll('.mg-w')].map(w => +getComputedStyle(w).opacity > 0.5 ? 1 : 0)}))"""


async def _states(job, times):
    from playwright.async_api import async_playwright
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
                out.append(await page.evaluate(_STATE))
            assert not await page.evaluate("window.__mgErrors")
            return out
        finally:
            await browser.close()


def _job(rows, reading=None, end=4.2, **params):
    item = {"id": "pb", "template": "phrase_build", "start": 0.0, "end": end,
            "params": motion_templates.check_params("phrase_build", dict(params, rows=rows))}
    if reading:
        item["reading"] = reading
    return motion_templates.build_job(item, 1080, 1920, 30)


@needs_browser
def test_unspoken_rows_reveal_in_reading_order_within_their_at_times():
    # the Jobs hook: rows at 0 / 0.15 / 0.3 with a 0.14 s stagger used to
    # show "GARBAGE" (0.3) before "were" (0.43)
    rows = [{"text": "Steve Jobs, 1983", "role": "sans", "size": "0.45", "at": "0"},
            {"text": "computer fonts were", "role": "serif", "size": "0.8", "at": "0.15"},
            {"text": "*GARBAGE*", "role": "condensed", "size": "1.5", "at": "0.3"}]
    times = [round(0.02 * k, 3) for k in range(0, 20)]
    states = asyncio.run(_states(_job(rows), times))
    for st in states:
        flat = [v for r in st for v in r["words"]]
        assert flat == sorted(flat, reverse=True), flat      # never a word before one above it
    assert [v for r in states[-1] for v in r["words"]] == [1] * 7   # complete by 0.38 s
    full = next(t for t, st in zip(times, states) if all(v for r in st for v in r["words"]))
    assert full <= 0.32


@needs_browser
def test_spoken_rows_land_on_their_onsets_and_bridges_sit_between_rows():
    reading = {"v": 1, "rows": [[0.7, 0.92, 1.28], [3.28, 3.5, 3.66], [3.9, 4.2]],
               "bridges": [{"after": 0, "words": [{"t": t, "s": 1.6 + 0.1 * i} for i, t in
                                                  enumerate("three or four years from now".split())]},
                           {"after": 2, "words": [{"t": "of", "s": 4.36}, {"t": "these", "s": 4.46},
                                                  {"t": "things", "s": 4.58}]}]}
    job = _job(PAPER_ROWS, reading, end=5.3, y=0.3)
    st = asyncio.run(_states(job, [0.65, 0.95, 1.65, 3.3, 4.5, 5.2]))
    kinds = [r["bridge"] for r in st[-1]]
    assert kinds == [False, True, False, False, True]       # reading order in the block
    assert st[0][0]["words"] == [0, 0, 0]                    # 'at' 0.7: nothing before the onset
    assert st[1][0]["words"] == [1, 1, 0]                    # "no college" at 0.7 / 0.92
    assert st[2][1]["words"][:1] == [1] and st[2][2]["words"] == [0, 0, 0]
    assert st[3][2]["words"] == [1, 0, 0]
    assert st[4][4]["words"] == [1, 1, 0] and st[5][4]["words"] == [1, 1, 1]
    # pre-laid-out: nothing moves while words land
    assert [r["top"] for r in st[0]] == [r["top"] for r in st[-1]]
    # bridges are small type, smaller than every row
    rows_fs = [r["fs"] for r in st[-1] if not r["bridge"]]
    assert all(r["fs"] < min(rows_fs) for r in st[-1] if r["bridge"])
