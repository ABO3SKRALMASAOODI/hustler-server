"""Defects the owner's podcast runs (6-16 Oct 2026) reported, fixed here.

Each test names the run, short and project the defect showed on. The triage
(every distinct defect, its evidence and status) is
.valmera/engines/owner_runs_defects.md in the owner's workspace.

Run:  PYTHONPATH=.. python -m pytest tests/test_owner_runs_fixes.py -q   (from worker/)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import renderer                                               # noqa: E402
from timeline import Timeline                                 # noqa: E402


# ── the last frame before the end card ──────────────────────────────────

def _edl_to_end(end):
    return {
        "keep": [[100.0, 120.0], [130.0, 148.33]],
        "motion": [{"id": "headline", "start": 0.0, "end": end},
                   {"id": "mid", "start": 2.0, "end": 9.0}],
        "texts": [{"id": "t", "start": 30.0, "end": end}],
        "vectors": [],
        "overlays": [{"id": "ov", "start": 36.0, "duration_s": end - 36.0}],
        "effects": {"picture_cards": [{"id": "card", "start": 0.0, "end": end}],
                    "zooms": [{"id": "z", "start": 1.0, "end": 4.0}],
                    "stylize": [{"id": "g", "start": None, "end": None}]},
    }


def test_layers_that_run_to_the_end_hold_to_the_last_rendered_frame():
    """Zuckerberg s24 (project 3635 v16): the standing headline and the
    payoff ended at the programme end, so their exits played in its last
    frames and the frame before the end card showed neither; Steve Jobs MIT
    s18 'flashing back to raw footage', s16 a card 'layout shift during the
    ending hold' (the card's own exit)."""
    edl = _edl_to_end(38.33)
    tl = Timeline(edl["keep"])
    fps = 29.97
    assert tl.out_duration == pytest.approx(38.33)
    render_end = renderer.program_render_s(tl, fps)
    bound = renderer.layers_to_render_end(edl, tl, fps)
    # past the last frame by more than any exit animation (a card's 0.45 s)
    assert bound >= render_end + 0.5
    for item in (edl["motion"][0], edl["texts"][0],
                 edl["effects"]["picture_cards"][0]):
        assert item["end"] == bound
    ov = edl["overlays"][0]
    assert ov["start"] + ov["duration_s"] == pytest.approx(bound, abs=1e-3)
    # what ends earlier, or has no end, keeps its window
    assert edl["motion"][1]["end"] == 9.0
    assert edl["effects"]["zooms"][0]["end"] == 4.0
    assert edl["effects"]["stylize"][0]["end"] is None


def test_an_item_a_frame_short_of_the_end_still_holds_to_it():
    # Jobs MIT s18: the hold overlay ended 0.03 s before the programme end
    edl = _edl_to_end(38.30)
    bound = renderer.layers_to_render_end(edl, Timeline(edl["keep"]), 29.97)
    assert edl["motion"][0]["end"] == bound
    # ...but not one that ends visibly earlier
    edl = _edl_to_end(38.2)
    renderer.layers_to_render_end(edl, Timeline(edl["keep"]), 29.97)
    assert edl["motion"][0]["end"] == 38.2


def test_every_layer_builder_is_clamped_to_the_render_end_not_the_programme_end():
    import inspect
    src = inspect.getsource(renderer)
    assert src.count("layer_end = layers_to_render_end(edl, tl, fps)") == 3
    assert "fps, tl.out_duration, extra_inputs, next_idx,\n            fetch_asset" \
        not in src


# ── a sentence-case claim measured at its cap height ───────────────────

def _type_png(lines, px=92, W=1080, H=1920):
    from io import BytesIO
    from PIL import Image, ImageDraw, ImageFont
    import band_type
    font = ImageFont.truetype(band_type.font_path("Inter Display", 800), px)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y = 250
    for text in lines:
        d.text((120, y), text, font=font, fill=(245, 243, 239, 255))
        y += int(px * 1.04)
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_a_sentence_case_line_is_measured_at_its_capitals_not_its_x_height():
    """Jensen s24 (project 3761, renders 70572/70582): the page set the
    claim 'Jensen on dinner etiquette.' at 92 px (cap 3.5% of 1920), the
    render's MEASURES read 3.1% off its x-height and 't' height, called the
    hook smaller than the captions and the short was failed for it."""
    import band_type
    import render_qc
    cap = 92 * band_type.cap_ratio("Inter Display", 800) / 1920
    m = render_qc.ink_lines(_type_png(["Jensen on", "dinner etiquette."]))
    assert len(m["lines"]) == 2
    for line in m["lines"]:
        assert line["cap"] == pytest.approx(cap, rel=0.06)
    # an all-caps line and a line of digits are measured as before
    caps = render_qc.ink_lines(_type_png(["HELLO WORLD"]))
    assert caps["cap"] == pytest.approx(cap, rel=0.06)
    digits = render_qc.ink_lines(_type_png(["30-40% 2029"]))
    assert digits["cap"] == pytest.approx(cap, rel=0.08)
    # a line with no capital or ascender is as small as it looks
    low = render_qc.ink_lines(_type_png(["a race / were near"]))
    assert low["cap"] < 0.85 * cap


# ── a batch that shortens the programme ─────────────────────────────────

def _held_edl():
    import schemas
    edl = schemas.default_edl(4000.0)
    edl["keep"] = [[3638.56, 3651.64], [3677.61, 3695.88]]      # 31.35 s
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    edl["inserts"] = [{"id": "ins1", "kind": "image", "asset_key": "generated/1/freeze.png",
                       "at_output_s": 31.35, "duration_s": 1.0}]
    edl["motion"] = [
        {"id": "context", "template": "headline", "start": 0.0, "end": 32.35,
         "params": {"text": "Jensen on *dinner*"}},
        {"id": "beat", "template": "word_slam", "start": 4.0, "end": 6.0,
         "params": {"text": "TSMC"}}]
    edl["effects"] = {"picture_cards": [{"id": "main", "start": 0.0, "end": 32.35,
                                         "box": [0.0, 0.24, 1.0, 0.794]}]}
    return schemas.validate_edl(edl, 4000.0).model_dump()


def test_removing_a_hold_ends_what_ran_to_the_end_with_the_programme():
    """Jensen s25 (project 3762 v12/v13): removing the payoff hold in a batch
    failed on 'motion[0]: end 32.35 exceeds the limit 31.35s', then, once
    the headline was re-ended by hand, on 'picture card: end 32.35 exceeds
    the limit 31.35s'; the short was abandoned."""
    import edit_batch
    before = _held_edl()
    keys = {"generated/1/freeze.png"}
    notes = []
    after = edit_batch.apply_batch(
        before, [{"action": "remove", "layer": "inserts", "id": "ins1"}],
        4000.0, keys, notes=notes)
    assert not after["inserts"]
    ends = {m["id"]: m["end"] for m in after["motion"]}
    assert ends == {"context": 31.35, "beat": 6.0}
    assert after["effects"]["picture_cards"][0]["end"] == 31.35
    assert notes and "PROGRAMME END" in notes[0] and "picture card 'main'" in notes[0]
    # the default call (no notes list) behaves the same
    again = edit_batch.apply_batch(
        before, [{"action": "remove", "layer": "inserts", "id": "ins1"}], 4000.0, keys)
    assert again["effects"]["picture_cards"][0]["end"] == 31.35


def test_an_item_the_batch_names_is_never_moved_and_every_overrun_is_named():
    import edit_batch
    before = _held_edl()
    keys = {"generated/1/freeze.png"}
    # the batch authors the headline's times itself: they stand as written,
    # so the overrun is refused, naming every item at once
    ops = [{"action": "remove", "layer": "inserts", "id": "ins1"},
           {"action": "upsert", "layer": "motion", "id": "context",
            "value": {"end": 32.0}},
           {"action": "set", "layer": "effects",
            "value": {"picture_cards": [{"id": "main", "start": 0.0, "end": 32.2,
                                         "box": [0.0, 0.24, 1.0, 0.794]}]}}]
    with pytest.raises(ValueError) as err:
        edit_batch.apply_batch(before, ops, 4000.0, keys)
    msg = str(err.value)
    assert "motion 'context' ends at 32" in msg and "picture card 'main' ends at 32.2" in msg
    assert "remove_insert" in msg
    # a batch that does not shorten the programme is untouched by the rule
    same = edit_batch.apply_batch(
        before, [{"action": "upsert", "layer": "motion", "id": "beat",
                  "value": {"end": 7.0}}], 4000.0, keys)
    assert {m["id"]: m["end"] for m in same["motion"]}["context"] == 32.35


# ── a windowed watch shows the window ────────────────────────────────────

def test_a_windowed_filmstrip_reads_the_window_not_the_start_of_the_file(
        monkeypatch, tmp_path):
    """Jensen run (source/viewer-defect.json): watch_video over 3402.36-3437.1
    s of the source returned the intro's first 35 s labelled 3403-3435,
    while look_at at 3410 showed the restaurant."""
    import mcp_media

    seen = []

    def frame_at(path, t, out, width=None):
        seen.append(t)
        with open(out, "wb") as f:
            f.write(b"jpg")

    monkeypatch.setattr(mcp_media.media, "frame_at", frame_at)
    monkeypatch.setattr(mcp_media.sheets, "build_timestamp_sheet",
                        lambda tiles, out: open(out, "wb").write(b"sheet"))

    class Ctx:
        workdir = str(tmp_path)
        pending_images = []

    n = mcp_media._filmstrip(Ctx(), "/src/full.mp4", duration=34.74,
                             start=3402.36, count=6)
    assert n == 6
    assert all(3402.36 <= t <= 3437.1 for t in seen), seen
    # a whole-file watch (start 0) reads exactly where it always did
    seen.clear()
    mcp_media._filmstrip(Ctx(), "/src/full.mp4", duration=12.0, start=0.0, count=4)
    assert seen == pytest.approx([1.5, 4.5, 7.5, 10.5])


# ── a story that opens on a visual setup the index has no words for ─────

def _shorts_ctx(captured):
    from types import SimpleNamespace
    import db as dbx

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.has_active_job:
                return False
            if fn is dbx.enqueue_job:
                captured["payload"] = args[3]
                return 781
            raise AssertionError(fn)

    words = [{"w": "So", "t0": 1525.1, "t1": 1525.4},
             {"w": "anyway.", "t0": 1525.4, "t1": 1526.0},
             # 1526.0-1546.54: Jensen walks to the rack, nothing indexed
             {"w": "This", "t0": 1546.54, "t1": 1546.8},
             {"w": "is", "t0": 1546.8, "t1": 1546.95},
             {"w": "my", "t0": 1546.95, "t1": 1547.1},
             {"w": "jacket.", "t0": 1547.1, "t1": 1553.54}]
    index = {"words": words, "sentences": [
        {"t0": 1525.1, "t1": 1526.0, "speaker": 0, "text": "So anyway."},
        {"t0": 1546.54, "t1": 1553.54, "speaker": 0, "text": "This is my jacket."}]}
    return SimpleNamespace(project={}, has_main_video=True, duration=5400.0,
                           index=index, db=FakeDb(), project_id=7,
                           job={"user_id": 60, "type": "mcp_tool"})


def test_a_story_may_open_on_unindexed_visual_setup():
    """Jensen run s10: the opening 1538.52-1546.54 is a visual setup the
    index has no speech for; make_shorts moved the start to the first
    sentence (7 s left) and refused the story at the 10 s minimum."""
    import agent_tools
    captured = {}
    result = agent_tools.make_shorts(_shorts_ctx(captured), clips=[{
        "start": 1538.52, "end": 1553.54, "title": "Jensen corrects a joke",
        "score": 80}])
    assert "job 781" in result, result
    clip = captured["payload"]["clips"][0]
    assert (clip["start"], clip["end"]) == (1538.52, 1553.54)


def test_a_start_inside_speech_is_still_refused_or_snapped():
    import agent_tools
    captured = {}
    result = agent_tools.make_shorts(_shorts_ctx(captured), clips=[{
        "start": 1546.9, "end": 1553.54, "title": "Mid-word", "score": 80}],
        snap="none")
    assert result.startswith("REJECTED") and "mid-thought at 1546.9s" in result


# ── captions never lose words to the line limit ──────────────────────────

def _shown_words(events):
    import re
    out = []
    for ev in events:
        if ev.get("layer") == 0:            # a premium look's backing panel
            continue
        text = re.sub(r"\{[^}]*\}", "", ev["text"]).replace(r"\N", " ")
        out += text.split()
    return out


@pytest.mark.parametrize("style", [None, {"preset": "karaoke"}, {"preset": "editorial"}])
def test_a_long_dictated_caption_is_paged_never_cut(style):
    """Jensen s29 (project 3764 v8, render 70528): long add_captions items
    lost every word past their second line on screen; audit_captions passed
    them because the item 'covered' the words."""
    import captions as caplib
    text = ("So you said yeah, you were looking for a foundry. He's telling "
            "the story of how he first returned back to Taiwan and he built "
            "the relationship with TSMC.")
    item = {"text": text, "start": 4307.52, "end": 4318.36}
    if style:
        item["style"] = style
    tl = Timeline([[4300.0, 4340.0]])
    events = caplib.events_from_items([item], tl, play_res=(1080, 1920))
    shown = _shown_words(events)
    want = text.upper().split() if style and caplib._preset_of(
        caplib._norm_style(style)) and caplib._preset_of(
        caplib._norm_style(style)).get("uppercase") else text.split()
    assert [w.strip(".,").lower() for w in shown] == \
        [w.strip(".,").lower() for w in want]
    texts = [e for e in events if e.get("layer") != 0]
    assert len(texts) >= 2
    # the pages share the item's own span, in order, without gaps
    assert texts[0]["start"] == pytest.approx(7.52, abs=1e-3)
    assert texts[-1]["end"] == pytest.approx(18.36, abs=1e-3)
    for a, b in zip(texts, texts[1:]):
        assert b["start"] == pytest.approx(a["end"], abs=1e-3)


def test_a_short_dictated_caption_is_one_event_exactly_as_before():
    import captions as caplib
    tl = Timeline([[0.0, 30.0]])
    events = caplib.events_from_items(
        [{"text": "This is too important", "start": 2.0, "end": 3.0}], tl)
    assert [(e["start"], e["end"], e["text"]) for e in events] == \
        [(2.0, 3.0, "This is too important")]


def test_a_transcript_group_that_wraps_past_its_lines_keeps_every_word():
    import captions as caplib
    # 38 characters fit the 2 x 20 budget, but the long words wrap onto a
    # third line: its word used to vanish from the caption
    words = [{"w": w, "t0": 1.0 + 0.3 * i, "t1": 1.25 + 0.3 * i}
             for i, w in enumerate(["aaaaaaaaaaaaaa", "bbbbbbbbbbbb", "cccccccc"])]
    events = caplib.events_from_transcript(words, line_chars=20)
    assert _shown_words(events) == [w["w"] for w in words]
    assert all(e["text"].count(r"\N") <= 1 for e in events)


# ── a caption correction that matches nothing says so ────────────────────

def _caption_ctx(tmp_path):
    from types import SimpleNamespace
    from schemas import default_edl
    edl = default_edl(200.0)
    edl["keep"] = [[100.0, 115.0]]
    edl["captions"] = {"mode": "from_transcript"}
    words = [{"w": "Oh", "t0": 110.0, "t1": 110.2},
             {"w": "it", "t0": 110.2, "t1": 110.4},
             {"w": "coach", "t0": 112.82, "t1": 113.0},
             {"w": "you", "t0": 113.0, "t1": 113.14},
             {"w": "between", "t0": 113.2, "t1": 113.5}]
    row = {"version": 1, "json": edl}
    ctx = SimpleNamespace(latest_edl=lambda: row, project_id=1,
                          index={"words": words, "video": {"width": 1920, "height": 1080}},
                          db=SimpleNamespace(run=lambda *_: None), workdir=str(tmp_path),
                          job={"id": 9}, pending_images=[])

    def write(value, _description):
        row.update(version=row["version"] + 1, json=value)
        return f"EDL v{row['version']} saved"
    ctx.write_edl = write
    return ctx


def test_a_correction_scoped_in_source_seconds_is_reported_not_applied(tmp_path):
    """Zuckerberg s01 (project 3612 v29): 'coach you' -> 'coaches you' scoped
    by its SOURCE time (112.82-113.14) matched nothing in program time, was
    stored silently, and the caption still read 'coach you'."""
    import agent_tools
    ctx = _caption_ctx(tmp_path)
    result = agent_tools.set_caption_fixes(ctx, [
        {"from": "coach you", "to": "coaches you", "start": 112.82, "end": 113.14}])
    assert "NOT APPLIED" in result and "'coach you'" in result
    assert "PROGRAM seconds" in result and "12.82" in result
    # the same rule in program seconds applies, with no warning
    ok = agent_tools.set_caption_fixes(ctx, [
        {"from": "coach you", "to": "coaches you", "start": 12.8, "end": 13.2}])
    assert "NOT APPLIED" not in ok and "coaches you" in ok
    # a phrase no caption reads is named too
    miss = agent_tools.set_caption_fixes(ctx, [["coached ya", "coached you"]])
    assert "NOT APPLIED" in miss and "no caption reads it" in miss


# ── a slate of any size is reviewed whole ────────────────────────────────

def test_the_story_judge_reviews_a_large_slate_in_batches(monkeypatch):
    """With no cap on how many stories a source yields, a slate of 30 is
    judged in bounded batches, every candidate under its slate id."""
    import json as _json
    import shorts_judge
    sents = [{"t0": i * 40.0, "t1": i * 40.0 + 30, "text": f"story {i} told",
              "speaker": 0} for i in range(30)]
    clips = [{"start": s["t0"], "end": s["t1"], "title": f"s{i}"}
             for i, s in enumerate(sents)]
    calls = []

    def ask_text(system, user, **kw):
        ids = [c["id"] for c in _json.loads(user)["candidates"]]
        calls.append(ids)
        return {"text": _json.dumps({"summary": "ok", "decisions": [
            {"id": i, "verdict": "reject" if i == "clip_25" else "keep",
             "confidence": 0.95, "evidence": "e", "reason": "r"} for i in ids]})}
    monkeypatch.setattr(shorts_judge.llm, "ask_text", ask_text)
    report = shorts_judge.review(clips, {"sentences": sents})
    assert len(calls) == 3 and max(len(c) for c in calls) <= shorts_judge.JUDGE_BATCH
    assert [d["id"] for d in report["decisions"]] == [f"clip_{i}" for i in range(1, 31)]
    kept, rejected = shorts_judge.apply_report(clips, report)
    assert rejected == 1 and len(kept) == 29
    assert "s24" not in [c["title"] for c in kept]


# ── a start that keeps a word from inside its sound ──────────────────────

# 'but' and 'Galaxy' as the index timed them (JRE #2190 part 2, project
# 3706), and the source's measured level around the cut (dBFS, 24 ms
# windows, read on the uploaded part): a -37 dB pause at 3931.84, the word
# rising from 3931.86, -13 dB at the index's onset 3931.95.
GALAXY_WORDS = [{"w": "story,", "t0": 3931.63, "t1": 3931.87},
                {"w": "but", "t0": 3931.87, "t1": 3931.95},
                {"w": "Galaxy", "t0": 3931.95, "t1": 3932.27},
                {"w": "Quest", "t0": 3932.27, "t1": 3932.59}]
GALAXY_DB = {3931.79: -33.6, 3931.80: -33.6, 3931.81: -34.6, 3931.82: -35.2,
             3931.83: -35.8, 3931.84: -37.2, 3931.85: -36.7, 3931.86: -35.8,
             3931.87: -25.7, 3931.88: -20.3, 3931.89: -16.5, 3931.90: -16.3,
             3931.91: -15.3, 3931.92: -14.6, 3931.93: -13.3, 3931.94: -12.9,
             3931.95: -12.8, 3931.96: -12.9, 3931.97: -13.3, 3931.98: -13.8}


def _level(profile, floor=-48.0):
    def lv(t):
        t = round(t, 2)
        if t in profile:
            return profile[t]
        return -14.0 if t > max(profile) else floor
    return lv


def test_a_start_on_a_contiguous_boundary_reaches_back_to_the_pause():
    """JRE #2190 s06 (project 3709): 'Galaxy Quest' kept from the index's
    onset 3931.95 sounded 'Policy Quest'; the reviewer restored 3931.83."""
    import cut_audio
    got = cut_audio.place_edge("start", 3931.95, GALAXY_WORDS, _level(GALAXY_DB))
    assert got["t"] == pytest.approx(3931.84, abs=0.015)
    found = cut_audio.clipped_onsets([[3931.95, 3963.15]], GALAXY_WORDS,
                                     6034.87, _level(GALAXY_DB))
    assert len(found) == 1 and found[0]["word"] == "Galaxy"
    line = cut_audio.onset_lines(found)[0]
    assert "clips the onset of 'Galaxy'" in line and "start at 3931.84" in line


def test_a_loud_contiguous_start_is_never_moved_later_into_the_next_gap():
    """JRE #2190 s10 (project 3721): 'there' sounds 911.16-911.20 under an
    index onset of 911.21; the quiet after it is the gap before 'probably',
    and moving the start there dropped the word."""
    import cut_audio
    words = [{"w": "know,", "t0": 911.05, "t1": 911.21},
             {"w": "there", "t0": 911.21, "t1": 911.37},
             {"w": "probably", "t0": 911.37, "t1": 911.61}]
    db = {911.13: -35.1, 911.14: -34.9, 911.15: -34.2, 911.16: -27.5,
          911.17: -23.7, 911.18: -23.0, 911.19: -24.6, 911.20: -30.6,
          911.21: -34.4, 911.22: -40.3, 911.23: -47.1, 911.24: -48.5,
          911.25: -48.2, 911.26: -48.5}
    got = cut_audio.place_edge("start", 911.21, words, _level(db, floor=-48.5))
    assert got["t"] <= 911.21


def test_the_render_audit_and_exact_keeps_name_a_clipped_onset(monkeypatch):
    import audit
    import cut_audio
    monkeypatch.setattr(cut_audio, "source_levels",
                        lambda *_a, **_k: _level(GALAXY_DB))
    lines = audit.midword_audit([[3931.95, 3963.15]], GALAXY_WORDS, 6034.87,
                                source="/src/part2.mp4")
    assert any("clips the onset of 'Galaxy'" in ln for ln in lines)
    # without the source's sound, the audit says nothing it cannot measure
    assert audit.midword_audit([[3931.95, 3963.15]], GALAXY_WORDS, 6034.87) == []


def test_an_exact_time_keep_is_written_as_given_with_the_onset_advisory(monkeypatch):
    from types import SimpleNamespace
    import agent_tools
    import cut_audio
    monkeypatch.setattr(cut_audio, "source_levels",
                        lambda *_a, **_k: _level(GALAXY_DB))
    ctx = SimpleNamespace(duration=6034.87, cut_sound_source=lambda: "/src/part2.mp4")
    notes = agent_tools._exact_keep_onset_notes(
        ctx, [[3931.95, 3963.15]], None, GALAXY_WORDS)
    assert len(notes) == 1 and notes[0].startswith("ADVISORY (exact times kept")
    assert "start at 3931.84" in notes[0]
    # no sound to read: nothing guessed
    ctx.cut_sound_source = lambda: None
    assert agent_tools._exact_keep_onset_notes(
        ctx, [[3931.95, 3963.15]], None, GALAXY_WORDS) == []


def test_the_pause_a_start_was_moved_back_to_is_not_called_a_mid_word_cut(monkeypatch):
    """The reviewer's repaired start 3931.83 sits in a -37 dB pause that the
    index assigns to 'story,' (3931.63-3931.87): the audit flagged the very
    edge the repair (and now keep_segments) chose."""
    import audit
    import cut_audio
    monkeypatch.setattr(cut_audio, "source_levels",
                        lambda *_a, **_k: _level(GALAXY_DB))
    assert audit.midword_audit([[3931.84, 3963.15]], GALAXY_WORDS, 6034.87,
                               source="/src/part2.mp4") == []
