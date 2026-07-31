"""Round 69 — the index samples the picture on a clock, and asks the
transcriber for the two things it was already paying for.

Both halves are measurements off the production database, not hunches:

  * 81 of 177 indexed videos (46%) were a SINGLE shot, so the agent's whole
    visual description of them was ONE thumbnail. The worst case was 19.3
    minutes of footage summarised from one frame at 9:38, and the median was
    12.1s of footage per captioned frame. Meanwhile the budget that bounds
    this — MAX_VISION_SHEETS x PER_SHEET = 300 tiles — went almost entirely
    unspent, and 355 of 456 agent turns (78%) never called look_at once,
    because a look costs the user a 13-second round trip.
  * ZERO filler tokens across 12,263 transcribed words in 85 indexes. nova-3
    drops "um"/"uh" unless asked for them, and remove_filler_words cuts the
    video at word TIMESTAMPS — so that tool has been a guaranteed no-op on
    every video ever uploaded, while 5 users asked for exactly it.

What must not break in the process: shots still mean SCENE CHANGES (round 48
depends on it), shot.caption still works for every existing reader, and the
complete transcript still reaches the prompt on short videos.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_loop                                              # noqa: E402
import captions as captions_mod                                # noqa: E402
import config                                                  # noqa: E402
import sheets                                                  # noqa: E402
import transcribe                                              # noqa: E402
import visual                                                  # noqa: E402
from schemas import (PIPELINE_VERSION, Moment, Sentence, Shot,  # noqa: E402
                     ShotCaption, VideoIndex, VideoInfo, Word,
                     is_filler_token)


BUDGET = config.MAX_VISION_SHEETS * sheets.PER_SHEET


def _shot(i, a, b):
    return Shot(id=i, start=a, end=b)


def _cap(action, setting="room", people="one man", text="", subs=False):
    return ShotCaption(setting=setting, people=people, action=action,
                       on_screen_text=text, subtitles=subs)


# ── the defect: one shot was one frame ──────────────────────────────────

def test_a_single_shot_video_is_no_longer_a_single_frame():
    """The 19.3-minute one-shot video from prod. It used to yield exactly one
    sample point; the whole round is that it does not."""
    dur = 1156.2
    pts, capped = visual.sample_points([_shot(1, 0.0, dur)], dur, 4.0, BUDGET)
    assert not capped
    assert len(pts) > 200, f"only {len(pts)} sample points for 19 minutes"
    assert len(pts) <= BUDGET
    # ...and they are spread across the whole video, not clustered.
    assert pts[0]["t"] < 30 and pts[-1]["t"] > dur - 30
    assert all(p["shot"] == 1 for p in pts)


def test_the_median_prod_video_still_costs_exactly_one_sheet():
    """79.2s is the production mean duration. One contact sheet = one vision
    call = what indexing that video costs today. The round must not quietly
    multiply the bill for the common case."""
    pts, _ = visual.sample_points([_shot(1, 0.0, 79.2)], 79.2, 4.0, BUDGET)
    assert len(pts) <= sheets.PER_SHEET, f"{len(pts)} tiles = >1 vision call"
    assert len(pts) >= 15, "and it still has to be a real timeline"


def test_the_budget_is_never_exceeded():
    for dur in (60.0, 600.0, 3 * 3600.0):
        pts, _ = visual.sample_points([_shot(1, 0.0, dur)], dur, 4.0, BUDGET)
        assert len(pts) <= BUDGET, f"{dur}s produced {len(pts)} points"
    # A 3-hour video stretches its grid rather than blowing the budget.
    pts, _ = visual.sample_points([_shot(1, 0.0, 10800.0)], 10800.0, 4.0,
                                  BUDGET)
    gaps = [pts[i + 1]["t"] - pts[i]["t"] for i in range(len(pts) - 1)]
    assert min(gaps) > 20.0, "the grid must stretch, not the tile count"


def test_shot_midpoints_are_always_sampled_while_they_fit():
    shots = [_shot(1, 0.0, 10.0), _shot(2, 10.0, 12.0), _shot(3, 12.0, 40.0)]
    pts, capped = visual.sample_points(shots, 40.0, 4.0, BUDGET)
    assert not capped
    mids = {round(p["t"], 2) for p in pts if p["shot_mid"]}
    assert mids == {5.0, 11.0, 26.0}
    # every point belongs to the shot it actually falls in
    for p in pts:
        s = [s for s in shots if s.start <= p["t"] < s.end]
        assert not s or p["shot"] == s[0].id


def test_a_grid_point_never_duplicates_a_shot_midpoint():
    """Sampling the same picture twice spends two tiles of a hard budget."""
    shots = [_shot(i, i * 4.0, (i + 1) * 4.0) for i in range(25)]
    pts, _ = visual.sample_points(shots, 100.0, 4.0, BUDGET)
    ts = sorted(p["t"] for p in pts)
    assert all(b - a > 0.4 for a, b in zip(ts, ts[1:])), "duplicate instants"


def test_too_many_shots_thins_evenly_the_way_it_always_did():
    shots = [_shot(i + 1, i * 1.0, i * 1.0 + 1.0) for i in range(900)]
    pts, capped = visual.sample_points(shots, 900.0, 4.0, BUDGET)
    assert capped, "the caller must be able to warn about this"
    assert len(pts) <= BUDGET
    assert all(p["shot_mid"] for p in pts), "no grid fill once shots overflow"
    assert pts[0]["t"] < 5 and pts[-1]["t"] > 890, "sampled across, not head"


# ── shot.caption keeps working for every existing reader ────────────────

def test_shot_captions_are_derived_from_the_nearest_moment():
    """taste.critique, the burned-caption warning, get_shots and the turn
    prompt all read shot.caption. It must survive the sampling change."""
    shots = [_shot(1, 0.0, 20.0), _shot(2, 20.0, 30.0)]
    moments = [
        Moment(t=2.0, shot=1, caption=_cap("intro")),
        Moment(t=10.0, shot=1, caption=_cap("the midpoint one")),
        Moment(t=18.0, shot=1, caption=_cap("late")),
        Moment(t=25.0, shot=2, caption=_cap("second shot")),
    ]
    visual.derive_shot_captions(shots, moments)
    assert shots[0].caption.action == "the midpoint one"
    assert shots[1].caption.action == "second shot"


def test_a_shot_with_no_captioned_moment_keeps_its_existing_caption():
    s = _shot(1, 0.0, 10.0)
    s.caption = _cap("already here")
    visual.derive_shot_captions([s], [Moment(t=5.0, shot=9, caption=None)])
    assert s.caption.action == "already here"


def test_shots_are_still_scene_changes():
    """Round 48: a transition lands where two sides come from DIFFERENT shots.
    Sampling 300 frames must not manufacture 300 scene changes."""
    dur = 600.0
    pts, _ = visual.sample_points([_shot(1, 0.0, dur)], dur, 4.0, BUDGET)
    assert len({p["shot"] for p in pts}) == 1


# ── reading it back is a list of CHANGES ────────────────────────────────

def test_identical_consecutive_moments_collapse_into_one_span():
    ms = [Moment(t=float(i), shot=1, caption=_cap("man talking to camera"))
          for i in range(0, 40, 4)]
    spans = visual.collapse([m.model_dump() for m in ms])
    assert len(spans) == 1
    assert spans[0]["t0"] == 0.0 and spans[0]["t1"] == 36.0


def test_a_real_change_starts_a_new_span():
    ms = [Moment(t=0.0, shot=1, caption=_cap("man talking to camera")),
          Moment(t=4.0, shot=1, caption=_cap("man talking to camera")),
          Moment(t=8.0, shot=1, caption=_cap("he holds up a red book")),
          Moment(t=12.0, shot=1, caption=_cap("man talking to camera"))]
    spans = visual.collapse([m.model_dump() for m in ms])
    assert len(spans) == 3
    assert spans[1]["caption"]["action"] == "he holds up a red book"


def test_changed_on_screen_text_is_a_change():
    """A slide deck's framing never moves; the words on it are the edit."""
    ms = [Moment(t=0.0, shot=1, caption=_cap("slide", text="Chapter One")),
          Moment(t=4.0, shot=1, caption=_cap("slide", text="Chapter Two"))]
    assert len(visual.collapse([m.model_dump() for m in ms])) == 2


def test_the_timeline_drops_the_SHORTEST_spans_when_over_budget():
    ms = []
    for i in range(50):
        ms.append(Moment(t=i * 10.0, shot=1, caption=_cap(f"scene {i}")))
    # one long span at the end
    for j in range(10):
        ms.append(Moment(t=500.0 + j * 4.0, shot=1,
                         caption=_cap("the long final take")))
    lines = visual.timeline_lines([m.model_dump() for m in ms], max_lines=10)
    assert len(lines) == 11, "10 spans + the honest tail"
    assert "not listed" in lines[-1]
    assert any("the long final take" in ln for ln in lines), \
        "the longest span must survive the thinning"


def test_an_index_without_moments_reads_back_silent():
    assert visual.timeline_lines([]) == []
    assert agent_loop._visual_lines({"moments": []}, 40) == []


# ── the transcriber's two flags ─────────────────────────────────────────

def test_deepgram_asks_for_diarization_and_filler_words():
    sent = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"results": {"channels": [{"alternatives": [{"words": []}]}]}}

    def _post(url, params=None, **kw):
        sent.update(params or {})
        return _Resp()

    real = transcribe.requests.post
    transcribe.requests.post = _post
    try:
        path = os.path.join(os.path.dirname(__file__), "_r69.wav")
        open(path, "wb").write(b"RIFF0000WAVE")
        try:
            transcribe._transcribe_deepgram(path)
        finally:
            os.unlink(path)
    finally:
        transcribe.requests.post = real
    assert sent.get("diarize") == "true"
    assert sent.get("filler_words") == "true"
    # billed-extra features stay off unless explicitly configured
    assert "sentiment" not in sent and "topics" not in sent


def test_deepgram_words_carry_speaker_and_filler():
    payload = {"results": {"channels": [{
        "detected_language": "en",
        "alternatives": [{"words": [
            {"word": "so", "punctuated_word": "So,", "start": 0.0,
             "end": 0.3, "speaker": 0},
            {"word": "um", "punctuated_word": "um", "start": 0.4,
             "end": 0.7, "speaker": 0},
            {"word": "right", "punctuated_word": "Right.", "start": 1.0,
             "end": 1.4, "speaker": 1},
        ]}]}]}}
    words, lang = transcribe._parse_deepgram(payload)
    assert lang == "en"
    assert [w.speaker for w in words] == [0, 0, 1]
    assert [w.filler for w in words] == [False, True, False]


def test_no_speaker_field_means_None_not_zero():
    """'everyone is speaker 0' and 'nobody diarized this' are different
    facts, and only one of them can be acted on."""
    payload = {"results": {"channels": [{"alternatives": [{"words": [
        {"word": "hi", "start": 0.0, "end": 0.2}]}]}]}}
    words, _ = transcribe._parse_deepgram(payload)
    assert words[0].speaker is None


def test_filler_tagging_is_tighter_than_the_removal_list():
    """The tag decides what is silently withheld from captions, so a real
    word must never carry it — 'ah' and 'er' are words in real languages."""
    assert is_filler_token("um") and is_filler_token("Uh,")
    assert not is_filler_token("ah") and not is_filler_token("er")
    import agent_tools
    assert "ah" in agent_tools.FILLER_WORDS_DEFAULT, \
        "still removable when the user asks for it"


def test_sentences_break_on_a_speaker_change():
    ws = [Word(w="hello", t0=0.0, t1=0.3, speaker=0),
          Word(w="there", t0=0.3, t1=0.6, speaker=0),
          Word(w="hi", t0=0.7, t1=0.9, speaker=1),
          Word(w="back", t0=0.9, t1=1.1, speaker=1)]
    sents = transcribe.group_sentences(ws)
    assert len(sents) == 2
    assert sents[0].text == "hello there" and sents[0].speaker == 0
    assert sents[1].text == "hi back" and sents[1].speaker == 1


def test_undiarized_words_never_split_on_speaker():
    ws = [Word(w="one", t0=0.0, t1=0.2), Word(w="two", t0=0.2, t1=0.4)]
    sents = transcribe.group_sentences(ws)
    assert len(sents) == 1 and sents[0].speaker is None


# ── fillers are cut from the VIDEO, never burned on it ──────────────────

def test_captions_never_burn_filler_words():
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "captions.py")).read()
    assert 'get("filler")' in src, \
        "build_ass must filter fillers out of the caption source words"

    class _TL:
        def kept_words(self, words):
            return [{"w": w["w"], "t0": w["t0"], "t1": w["t1"]}
                    for w in words]

        def insert_positions(self):
            return []

        def out_duration(self):
            return 10.0

    index = {"words": [
        {"w": "So,", "t0": 0.0, "t1": 0.3, "filler": False},
        {"w": "um", "t0": 0.4, "t1": 0.7, "filler": True},
        {"w": "yeah.", "t0": 0.8, "t1": 1.1, "filler": False},
    ]}
    seen = {}
    real = captions_mod.events_from_transcript

    def _spy(out_words, **kw):
        seen["words"] = [w["w"] for w in out_words]
        return real(out_words, **kw)

    captions_mod.events_from_transcript = _spy
    try:
        captions_mod.build_ass({"captions": {"mode": "from_transcript"}},
                               index, _TL(),
                               os.path.join(os.path.dirname(__file__),
                                            "_r69.ass"))
    finally:
        captions_mod.events_from_transcript = real
        for p in ("_r69.ass",):
            fp = os.path.join(os.path.dirname(__file__), p)
            if os.path.exists(fp):
                os.unlink(fp)
    assert seen["words"] == ["So,", "yeah."], seen


def test_remove_filler_words_uses_the_tag_by_default_only():
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "agent_tools.py")).read()
    assert "use_tag = not (isinstance(words, list) and words)" in src, \
        "a custom word list must never silently cut untagged words"


# ── the index contract ──────────────────────────────────────────────────

def test_pipeline_version_bumped_so_stale_indexes_rebuild():
    assert PIPELINE_VERSION >= 9, \
        "both halves change index OUTPUT; without a bump nobody gets either"


def test_an_old_index_loads_unchanged():
    """Every index in prod predates this. They must read back exactly as
    they did — no moments, no speakers, no crash."""
    idx = VideoIndex(
        video=VideoInfo(duration=10.0, fps=30.0, width=1920, height=1080,
                        has_audio=True),
        shots=[Shot(id=1, start=0.0, end=10.0, caption=_cap("a man"))],
        words=[Word(w="hi", t0=0.0, t1=0.2)],
        sentences=[Sentence(id="s1", text="hi", t0=0.0, t1=0.2, wi0=0, wi1=0)],
    ).model_dump()
    assert idx["moments"] == [] and idx["speakers"] == 0
    assert idx["words"][0]["speaker"] is None
    assert idx["words"][0]["filler"] is False
    assert agent_loop._speaker_line(idx) is None
    assert agent_loop._filler_line(idx) is None
    assert agent_loop._index_summary(idx)          # never raises


# ── the sheets/vision plumbing ──────────────────────────────────────────

def test_a_tile_id_maps_back_to_its_sample_point(tmp_path):
    from PIL import Image
    pts = [{"t": float(i) * 4.0, "shot": 1, "shot_mid": False}
           for i in range(30)]
    frames = {}
    for i in range(30):
        fp = tmp_path / f"{i}.jpg"
        Image.new("RGB", (64, 36), (i * 8 % 255, 0, 0)).save(fp)
        frames[i] = str(fp)
    built = sheets.build_contact_sheets(pts, frames, str(tmp_path))
    assert len(built) == 2, "25 per sheet"
    assert built[0][1] == list(range(25))
    assert built[1][1] == list(range(25, 30))
    # the ids are POINT indexes, which is what ties a caption to an instant
    assert all(os.path.exists(p) for p, _ in built)


def test_caption_parsing_accepts_the_legacy_shot_key():
    """A model that has seen the old prompt shape must not silently produce
    an index with no captions in it."""
    import llm
    real_ok, real_ask = llm.vision_available, llm.ask_vision
    llm.vision_available = lambda *a, **k: True
    llm.ask_vision = lambda *a, **k: (
        '[{"shot": 0, "setting": "s", "people": "p", "action": "a",'
        ' "on_screen_text": "", "subtitles": false}]')
    try:
        got = sheets._caption_one("x.jpg", [0, 1], None)
    finally:
        llm.vision_available, llm.ask_vision = real_ok, real_ask
    assert 0 in got and got[0].action == "a"


def test_the_recorder_is_installed_on_the_pool_thread():
    """llm's recorder is THREAD-LOCAL. Captioning concurrently without
    re-installing it per thread is exactly the blind spot round 67 fixed."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "sheets.py")).read()
    assert "llm.set_recorder(recorder)" in src
    assert "THREAD-LOCAL" in src


def test_the_caption_pool_never_touches_the_db_connection():
    """WorkerDB holds ONE psycopg connection with no lock. A recorder that
    wrote from two sheets at once would corrupt the index's lane."""
    src = open(os.path.join(os.path.dirname(__file__), "..",
                            "indexer.py")).read()
    i = src.index("def _index_recorder")
    body = src[i:src.index("caps = {}", i)]
    assert "worker_db.run" not in body, "the recorder must only buffer"
    assert "recorded.append" in body


# ── perception arrives WITHOUT a round trip ─────────────────────────────

def _index_with_moments(n, dur, n_sentences=60):
    return {
        "video": {"duration": dur, "fps": 30.0, "width": 1920, "height": 1080,
                  "has_audio": True},
        "sentences": [{"id": f"s{i}", "text": "a sentence of words " * 3,
                       "t0": float(i), "t1": float(i) + 0.9}
                      for i in range(n_sentences)],
        "words": [], "shots": [{"id": 1, "start": 0.0, "end": dur}],
        "silences": [],
        "moments": [{"t": i * 2.0, "shot": 1,
                     "caption": {"setting": f"place {i}", "people": "a man",
                                 "action": f"distinct action number {i}",
                                 "on_screen_text": "", "subtitles": False}}
                    for i in range(n)],
    }


def test_the_visual_timeline_is_in_the_turn_prompt():
    idx = _index_with_moments(20, 60.0)
    block = agent_loop._full_index_block(idx)
    assert block and "WHAT IS ON SCREEN OVER TIME" in block
    assert "distinct action number 0" in block


def test_the_complete_transcript_outranks_the_timeline_under_the_cap():
    """Adding the timeline must never push a video off the full-index path —
    that would cost it the COMPLETE transcript, an older promise. The newest
    signal thins first, all the way to nothing, before anything is dropped."""
    idx = _index_with_moments(300, 600.0)
    # A transcript that on its own nearly fills the cap: with the timeline at
    # full size the block cannot fit, so the timeline must be what gives way.
    bare = agent_loop._full_index_block({**idx, "moments": []})
    assert bare is not None and len(bare) < config.FULL_INDEX_MAX_CHARS
    pad = config.FULL_INDEX_MAX_CHARS - len(bare) - 400
    assert pad > 0
    idx["sentences"] = idx["sentences"] + [
        {"id": "sPAD", "text": "x" * pad, "t0": 0.0, "t1": 0.1}]

    block = agent_loop._full_index_block(idx)
    assert block is not None, "fell back to the elided summary"
    assert "TRANSCRIPT — COMPLETE" in block
    assert "x" * pad in block, "the whole transcript survived"
    assert "WHAT IS ON SCREEN OVER TIME" not in block, \
        "the timeline should have thinned to nothing, not taken the block down"
    assert len(block) <= config.FULL_INDEX_MAX_CHARS


def test_a_video_that_fits_keeps_both():
    idx = _index_with_moments(300, 600.0)
    block = agent_loop._full_index_block(idx)
    assert block and "TRANSCRIPT — COMPLETE" in block
    assert "WHAT IS ON SCREEN OVER TIME" in block
    assert len(block) <= config.FULL_INDEX_MAX_CHARS


def test_long_videos_get_the_timeline_too():
    idx = _index_with_moments(40, 5000.0)
    summary = agent_loop._index_summary(idx)
    assert "WHAT IS ON SCREEN OVER TIME" in summary
    assert "get_shots(start, end)" in summary
