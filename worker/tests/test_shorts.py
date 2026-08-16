"""Shorts mode (round 99) — the pure logic of the pipeline.

The plan parser/validator, the reference→style mappings, the count
heuristic, and the wiring facts main.py relies on (job type registered,
charged like a turn, notes present). No DB, no LLM, no media.

Run from worker/:  python3 -m pytest tests/test_shorts.py -q
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import shorts                                                # noqa: E402


@pytest.fixture(autouse=True)
def _disable_independent_cast_by_default(monkeypatch):
    """Plan unit tests isolate the first scout unless they opt into casting."""
    monkeypatch.setattr(shorts.shorts_judge, "review",
                        lambda *_args, **_kwargs: None)


# ------------------------------------------------------------------ helpers
def test_default_count_scales_with_duration_and_caps():
    assert shorts._default_count(120) == 1          # 2 min -> still 1
    assert shorts._default_count(1500) == 5         # 25 min -> 5
    assert shorts._default_count(6 * 3600) == shorts.config.SHORTS_MAX_CLIPS


def test_json_from_tolerates_fences_and_prose():
    assert shorts._json_from('```json\n{"a": 1}\n```') == {"a": 1}
    assert shorts._json_from('Here you go: {"clips": []}') == {"clips": []}
    assert shorts._json_from("no json here") is None
    assert shorts._json_from("") is None


# ------------------------------------------------------------------ style
def test_caption_preset_mapping():
    pick = shorts._pick_caption_preset
    assert pick({"captions": {"style_words": "word-by-word karaoke pop"}}) \
        == "karaoke"
    assert pick({"captions": {"style_words": "bold yellow impact hype"}}) \
        == "beast"
    assert pick({"captions": {"style_words": "thin elegant serif"}}) \
        == "elegant"
    # Uncertain reference analysis defaults to coherent white typography,
    # not a moving yellow box or a novelty mixed-font treatment.
    assert pick({"captions": {"style_words": "clean white phrases"}}) \
        == "clean"
    assert pick(None) == "clean"                    # no vision -> safe default
    assert pick({"captions": {"style_words": "one word at a time, huge"}}) \
        == "spotlight"


def test_grade_mapping_only_returns_real_presets():
    from schemas import GRADE_PRESETS
    cases = ["black and white", "teal cinematic", "warm golden", "cool blue",
             "vintage film", "vibrant punchy", "natural", ""]
    for words in cases:
        got = shorts._pick_grade({"grade_words": words})
        assert got is None or got in GRADE_PRESETS
    assert shorts._pick_grade({"grade_words": "black and white"}) == "bw"
    assert shorts._pick_grade({"grade_words": "natural look"}) is None


def test_reference_profile_carries_compact_measured_grammar(tmp_path):
    import db as dbx

    index = {
        "video": {"duration": 40.0, "width": 1080, "height": 1920},
        "shots": [{"start": i * 5.0, "end": (i + 1) * 5.0}
                  for i in range(8)],
        "words": [{"w": "word", "t0": i * .5, "t1": i * .5 + .2}
                  for i in range(40)],
        "perception": {"bpm": 120, "bpm_conf": .9,
                       "beats": [i * .5 for i in range(81)],
                       "energy": [-24] * 10 + [-18] * 20 + [-22] * 10},
        "motion": {"intensity": "moderate"},
    }

    class Db:
        def run(self, fn, *_args):
            if fn is dbx.get_asset:
                return {"id": 5, "sha256": "abc",
                        "meta": {"indexed": True}}
            if fn is dbx.get_index_by_sha:
                return {"json": index}
            raise AssertionError(fn)

    profile = shorts._reference_profile(
        Db(), {"id": 9}, {"id": 5, "meta": {"filename": "style.mp4"}},
        str(tmp_path))

    assert profile["measured_grammar"]["rhythm"]["shot_median_s"] == 5
    assert "cut_times_s" not in profile["measured_grammar"]["rhythm"]
    assert "transfer its relationships and hierarchy" in \
        profile["measured_grammar_text"]


# ------------------------------------------------------------------ the plan
def _fake_plan(monkeypatch, clips):
    monkeypatch.setattr(shorts, "_ask_json",
                        lambda *a, **k: {"clips": clips})


def _index(sentences):
    return {"sentences": sentences,
            "video": {"duration": 600.0}}


SENTS = [{"t0": float(i * 30), "t1": float(i * 30 + 28),
          "text": f"sentence number {i} with a full thought in it",
          "speaker": None} for i in range(20)]


def test_plan_drops_short_long_and_overlapping_clips(monkeypatch):
    _fake_plan(monkeypatch, [
        {"start": 0, "end": 4, "title": "too short", "score": 99},
        {"start": 30, "end": 58, "title": "keeper", "score": 90},
        {"start": 35, "end": 60, "title": "overlaps keeper", "score": 80},
        {"start": 120, "end": 260, "title": "too long gets trimmed",
         "score": 70},
        {"start": 400, "end": 430, "title": "second keeper", "score": 60},
    ])
    out = shorts._plan_clips(None, {"user_id": 1}, _index(SENTS), 600.0,
                             None, {}, False, "free")
    titles = [c["title"] for c in out]
    assert "too short" not in titles
    assert "keeper" in titles and "second keeper" in titles
    assert "overlaps keeper" not in titles
    trimmed = next(c for c in out if c["title"].startswith("too long"))
    assert trimmed["end"] - trimmed["start"] <= shorts.CLIP_MAX_S
    # orders are assigned contiguously by score
    assert [c["order"] for c in out] == list(range(len(out)))


def test_plan_respects_requested_count(monkeypatch):
    _fake_plan(monkeypatch, [
        {"start": i * 60.0, "end": i * 60.0 + 30, "title": f"c{i}",
         "score": 100 - i} for i in range(8)])
    out = shorts._plan_clips(None, {"user_id": 1}, _index(SENTS), 600.0,
                             None, {"count": 3}, False, "free")
    assert len(out) == 3
    assert [c["score"] for c in out] == sorted(
        [c["score"] for c in out], reverse=True)


def test_independent_cast_can_reject_every_proposed_fragment(monkeypatch):
    _fake_plan(monkeypatch, [
        {"start": 30, "end": 58, "title": "Isolated quote", "score": 99},
    ])
    monkeypatch.setattr(
        shorts.shorts_judge, "review",
        lambda *_a, **_k: {"decisions": [{
            "id": "clip_1", "verdict": "reject", "confidence": .96,
            "evidence": "the question and resolution are outside the cut",
            "reason": "the excerpt is not self-contained",
        }]})
    try:
        shorts._plan_clips(None, {"user_id": 1}, _index(SENTS), 600.0,
                           None, {}, False, "free")
        assert False, "a decisive all-reject cast must abstain"
    except shorts.NoWorthyStories as exc:
        assert "rejected every proposed window" in str(exc)


def test_valid_empty_story_scout_is_abstention_not_retryable_parse_failure(
        monkeypatch):
    _fake_plan(monkeypatch, [])
    try:
        shorts._plan_clips(None, {"user_id": 1}, _index(SENTS), 600.0,
                           None, {}, False, "free")
        assert False, "a deliberate empty slate must be distinguished"
    except shorts.NoWorthyStories as exc:
        assert "no complete story" in str(exc)


def test_story_treatment_survives_plan_validation():
    raw = [{
        "start": 30, "end": 62, "title": "The launch mistake",
        "hook": "We lost the launch", "score": 94, "music": False,
        "story": {"setup": "The team bets on launch day",
                  "development": "Customer warnings are ignored",
                  "payoff": "The failure changes the plan"},
        "visual_direction": "Clean evidence-led captions and restrained motion",
        "broll": [{"at": 42, "query": "failed mobile app launch screen",
                   "purpose": "prove the product failure", "duration_s": 3}],
    }]
    out = shorts._validated_clips(raw, 600.0, 1, index=_index(SENTS))

    assert out[0]["story"]["payoff"] == "The failure changes the plan"
    assert out[0]["visual_direction"].startswith("Clean evidence")
    assert out[0]["broll"][0]["query"] == "failed mobile app launch screen"
    assert "MICRO-STORY, NOT A TRANSCRIPT EXCERPT" in shorts._PLAN_SYSTEM
    assert "You do not style, caption, reframe" in shorts._PLAN_SYSTEM
    assert shorts._caller_planned_clips({"clips": raw}, _index(SENTS),
                                        600.0)[0]["story"]["setup"] \
        == "The team bets on launch day"
    assert shorts._caller_planned_clips({}, _index(SENTS), 600.0) is None


def test_shorts_scout_keeps_reference_out_of_story_selection(
        monkeypatch):
    seen = {}

    def fake_ask(*args, **_kwargs):
        seen["user"] = args[5]
        return {"clips": [{"start": 30, "end": 58,
                            "title": "A complete lesson", "score": 90}]}

    monkeypatch.setattr(shorts, "_ask_json", fake_ask)
    style = {"analyzed": True, "duration_s": 35, "cuts_per_min": 28,
             "energy": "hype",
             "measured_grammar_text": (
                 "pacing p10/median/p90=0.8/2.0/6.0s; "
                 "energy=late_peak_then_release")}
    out = shorts._plan_clips(
        None, {"user_id": 1}, _index(SENTS), 600.0, style, {},
        False, "free")

    assert out[0]["title"] == "A complete lesson"
    assert "editing reference belongs to the later child editor" in seen["user"]
    assert "late_peak_then_release" not in seen["user"]


def test_plan_without_speech_fails_honestly(monkeypatch):
    _fake_plan(monkeypatch, [])
    try:
        shorts._plan_clips(None, {"user_id": 1}, _index([]), 600.0,
                           None, {}, False, "free")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "no transcribed speech" in str(e)


def test_plan_without_speech_uses_visual_filmstrips(monkeypatch, tmp_path):
    monkeypatch.setattr(shorts.llm, "vision_available", lambda: True)
    monkeypatch.setattr(shorts.storage, "download_to", lambda *_a: None)
    monkeypatch.setattr(
        shorts.llm, "ask_vision",
        lambda *_a, **_k: '{"clips":[{"start":31,"end":58,'
        '"title":"Clean knockout sequence","hook":"fighter closes in",'
        '"score":92,"music":true}]}')
    index = _index([])
    index.update({
        "tile_keys": ["tiles/a.jpg", "tiles/b.jpg"],
        "shots": [{"start": 30.0, "end": 60.0}],
    })
    out = shorts._plan_clips(
        None, {"id": 9, "user_id": 1}, index, 600.0, None,
        {"count": 1, "style_note": "prioritize decisive action"},
        False, "free", workdir=str(tmp_path))
    assert len(out) == 1
    assert out[0]["start"] == 30.0 and out[0]["end"] == 60.0
    assert out[0]["title"] == "Clean knockout sequence"


def test_thin_signoff_transcript_routes_to_visual_tiles(monkeypatch, tmp_path):
    monkeypatch.setattr(shorts.llm, "vision_available", lambda: True)
    monkeypatch.setattr(shorts.storage, "download_to", lambda *_a: None)
    monkeypatch.setattr(
        shorts.llm, "ask_vision",
        lambda *_a, **_k: '{"clips":[{"start":30,"end":55,'
        '"title":"Visual highlight","score":90}]}')
    monkeypatch.setattr(
        shorts, "_ask_json",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("thin transcript must not use speech planner")))
    index = _index([{"t0": 226, "t1": 228,
                     "text": "Thanks for watching!"}])
    index.update({"words": [{"w": "Thanks"}, {"w": "for"},
                             {"w": "watching"}],
                  "tile_keys": ["tiles/a.jpg"],
                  "shots": [{"start": 30.0, "end": 55.0}]})
    out = shorts._plan_clips(
        None, {"id": 704, "user_id": 1}, index, 228.0, None, {},
        False, "free", workdir=str(tmp_path))
    assert [c["title"] for c in out] == ["Visual highlight"]


def test_empty_transcript_plan_falls_back_to_vision(monkeypatch, tmp_path):
    monkeypatch.setattr(shorts.llm, "vision_available", lambda: True)
    monkeypatch.setattr(shorts.storage, "download_to", lambda *_a: None)
    monkeypatch.setattr(shorts, "_ask_json", lambda *_a, **_k: {"clips": []})
    monkeypatch.setattr(
        shorts.llm, "ask_vision",
        lambda *_a, **_k: '{"clips":[{"start":60,"end":90,'
        '"title":"Seen in tiles","score":88}]}')
    index = _index(SENTS)
    index.update({"tile_keys": ["tiles/a.jpg"],
                  "shots": [{"start": 60.0, "end": 90.0}]})
    out = shorts._plan_clips(
        None, {"id": 9, "user_id": 1}, index, 600.0, None, {},
        False, "free", workdir=str(tmp_path))
    assert [c["title"] for c in out] == ["Seen in tiles"]


def test_transcript_block_truncates():
    sents = [{"t0": i, "t1": i + 1, "text": "word " * 30, "speaker": 0}
             for i in range(3000)]
    block = shorts._transcript_block({"sentences": sents}, max_chars=5000)
    assert len(block) <= 5000 + 30
    assert "truncated" in block


def _long_podcast_index():
    rows = []
    for i in range(720):
        text = ("A complete but ordinary discussion sentence about building "
                "a company and learning from customers over time.")
        speaker = i % 2
        if i == 650:
            text = "What was the launch mistake that cost 900000 dollars?"
            speaker = 0
        elif i == 651:
            text = ("The launch failed because we ignored customer evidence, "
                    "and the lesson changed our entire plan.")
            speaker = 1
        rows.append({"t0": i * 10.0, "t1": i * 10.0 + 8.0,
                     "text": text, "speaker": speaker})
    return {"sentences": rows, "speakers": 2,
            "video": {"duration": 7200.0}}


def test_long_podcast_shortlist_spans_full_source_and_keeps_qa_context():
    index = _long_podcast_index()
    full = shorts._transcript_block(index)
    compact, meta = shorts._shortlist_transcript_arcs(
        index, 7200.0, 6, "launch lessons and customer evidence")

    assert meta["source_sentences"] == 720
    assert 16 <= meta["candidates"] <= 64
    assert len(compact) < len(full)
    assert "CANDIDATE ARC" in compact
    assert "6500.0-6508.0" in compact
    assert "What was the launch mistake" in compact
    assert "The launch failed because" in compact


def test_long_podcast_planner_uses_ranked_arcs_not_opening_truncation(
        monkeypatch):
    seen = {}

    def fake_ask(*args, **kwargs):
        seen["user"] = args[5]
        return {"clips": [{"start": 6500, "end": 6548,
                            "title": "The $900K Launch Mistake",
                            "hook": "What was the launch mistake",
                            "score": 96, "music": False}]}

    monkeypatch.setattr(shorts, "_ask_json", fake_ask)
    out = shorts._plan_clips(
        None, {"user_id": 1}, _long_podcast_index(), 7200.0, None,
        {"count": 1, "style_note": "launch lessons"}, False, "free")

    assert out[0]["title"] == "The $900K Launch Mistake"
    assert "RANKED COMPLETE-ARC EVIDENCE" in seen["user"]
    assert "not a chronological truncation" in seen["user"]
    assert "6500.0-6508.0" in seen["user"]
    assert "[transcript truncated]" not in seen["user"]


# ------------------------------------------------------------------ wiring
def test_job_type_is_registered_everywhere():
    import main
    assert "shorts_plan" in main.SHORTS_TYPES
    assert "shorts_plan" not in main.AGENT_TYPES
    assert main.RUNNERS.get("shorts_plan") is shorts.run_shorts_plan
    assert "shorts_plan" in main.FAIL_NOTES
    assert "shorts_plan" in main.REAPER_NOTES


def test_seed_child_only_keeps_the_selected_story(monkeypatch, tmp_path):
    """The scout must never sneak the old caption/crop/zoom recipe back in."""
    import agent_tools
    import db as dbx

    calls = []

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.get_project:
                return {"id": args[0]}
            raise AssertionError(fn)

    class FakeContext:
        def latest_edl(self):
            return {"version": 2}

    monkeypatch.setattr(agent_tools, "ToolContext",
                        lambda *_args, **_kwargs: FakeContext())
    monkeypatch.setattr(
        agent_tools, "execute",
        lambda _ctx, name, args: calls.append((name, args)) or "kept story")

    version, note = shorts._seed_story_child(
        FakeDb(), {"id": 9}, 71, {}, {"start": 32, "end": 88},
        str(tmp_path))

    assert version == 2 and note == "kept story"
    assert calls == [("keep_segments", {
        "segments": [[32, 88]], "snap_to_words": True,
    })]


def test_make_shorts_is_an_agent_tool():
    import agent_tools
    assert "make_shorts" in agent_tools.TOOLS
    assert "make_shorts" in agent_tools.REQUIRED_ARGS
    # It never writes the EDL, so the honesty layer must not count it.
    assert "make_shorts" not in agent_tools.WRITE_TOOLS


def test_sub_minute_shorts_route_to_direct_edit():
    """A short source is a valid one-short project, not a failed extractor."""
    from types import SimpleNamespace
    import agent_tools

    ctx = SimpleNamespace(has_main_video=True, duration=42.0)
    result = agent_tools.make_shorts(ctx)
    assert result.startswith("DIRECT SHORT:")
    assert "do the edit now" in result
    assert "REJECTED" not in result


def test_indexed_shorts_wait_for_a_brief_before_planning():
    """Mode selection alone must not make the creative decisions."""
    import indexer

    assert indexer._shorts_index_route("shorts", 59.9) == "direct_edit"
    assert indexer._shorts_index_route("shorts", 60.0) == "await_brief"
    assert indexer._shorts_index_route("shorts", 1800.0) == "await_brief"
    assert indexer._shorts_index_route("edit", 1800.0) is None
    assert indexer._shorts_index_route("shorts", 1800.0, reindex=True) is None


def test_index_auto_resume_rechecks_account_gate_and_retires_prompt():
    """Qualification on another project cannot race a saved upload brief."""
    import db as dbx
    import indexer

    calls = []

    class FakeDb:
        def run(self, fn, *args):
            calls.append((fn, args))
            if fn is dbx.resolve_pending_auto_resume:
                return {"state": "gated", "job_id": None}
            if fn is dbx.record_client_event:
                return None
            raise AssertionError(fn)

    resolved = indexer._resolve_pending_auto_resume(
        FakeDb(), 9, 10, 11, 12, "index_auto_resume")

    assert resolved == {"state": "gated", "job_id": None}
    assert [fn for fn, _args in calls] == [
        dbx.resolve_pending_auto_resume,
        dbx.record_client_event,
    ]
    assert calls[0][1] == (9, 10, 11, 12, {})
    assert calls[1][1][0:3] == (11, 9, "trial_gate_shown")


def test_index_auto_resume_gate_lookup_fails_open():
    import db as dbx
    import indexer

    class FakeDb:
        def run(self, fn, *_args):
            assert fn is dbx.resolve_pending_auto_resume
            raise RuntimeError("temporary database error")

    assert indexer._resolve_pending_auto_resume(
        FakeDb(), 9, 10, 11, 12, "index_auto_resume") == {
            "state": "error", "job_id": None}


def test_make_shorts_returns_the_background_job_id():
    """MCP has no Shorts board UI, so the caller needs the exact job to poll."""
    from types import SimpleNamespace
    import agent_tools
    import db as dbx

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.has_active_job:
                return False
            if fn is dbx.enqueue_job:
                assert args[0:3] == (7, 60, "shorts_plan")
                return 321
            raise AssertionError(fn)

    ctx = SimpleNamespace(
        has_main_video=True, duration=180.0,
        index={"words": [{"word": "hello", "t0": 0.0, "t1": 0.5}]},
        db=FakeDb(), project_id=7, job={"user_id": 60})
    result = agent_tools.make_shorts(ctx, count=3, style_note="clean")
    assert "job 321" in result
    assert "wait_for_job(job_id=321)" in result
    assert "shorts_status" in result


def test_mcp_must_author_story_arcs_instead_of_calling_valmera_planner():
    from types import SimpleNamespace
    import agent_tools
    import db as dbx

    class FakeDb:
        def run(self, fn, *_args):
            if fn is dbx.has_active_job:
                return False
            raise AssertionError("MCP without explicit clips must not enqueue")

    ctx = SimpleNamespace(
        project={}, has_main_video=True, duration=1800.0,
        index={"words": [{"w": "hello"}]}, db=FakeDb(), project_id=7,
        job={"user_id": 60, "type": "mcp_tool"})
    result = agent_tools.make_shorts(ctx, style_note="make it engaging")

    assert result.startswith("MCP DIRECT PLANNING REQUIRED")
    assert "useful title, hook, and story summary" in result
    assert "directly editing the opened child" in result


def test_mcp_explicit_story_arcs_queue_locked_story_children():
    from types import SimpleNamespace
    import agent_tools
    import db as dbx

    captured = {}

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.has_active_job:
                return False
            if fn is dbx.enqueue_job:
                captured["payload"] = args[3]
                return 777
            raise AssertionError(fn)

    clips = [{
        "start": 120, "end": 162, "title": "The launch mistake",
        "hook": "What went wrong?", "score": 96,
        "story": {"setup": "The host asks about the launch",
                  "development": "The founder explains the ignored warning",
                  "payoff": "The failure produces a durable lesson"},
        # Creative treatment deliberately is not required at selection time.
    }]
    ctx = SimpleNamespace(
        project={}, has_main_video=True, duration=1800.0,
        index={"words": [{"w": "hello"}]}, db=FakeDb(), project_id=7,
        job={"user_id": 60, "type": "mcp_tool"})
    result = agent_tools.make_shorts(
        ctx, style_note="premium founder podcast", clips=clips)

    assert "job 777" in result
    assert "uses the story arcs you selected" in result
    assert captured["payload"]["source"] == "mcp_direct"
    assert captured["payload"]["clips"][0]["story"]["payoff"].startswith(
        "The failure")
    assert "broll" not in captured["payload"]["clips"][0]
    assert "visual_direction" not in captured["payload"]["clips"][0]
    assert "LOCKED child" in result


def test_parent_agent_cannot_boot_children_without_card_press():
    from types import SimpleNamespace
    import agent_tools

    result = agent_tools.edit_shorts(
        SimpleNamespace(), "make all of these cinematic")
    assert result.startswith("LOCKED CARD BOUNDARY")
    assert "Edit button" in result


def test_make_shorts_can_queue_a_speechless_visual_plan(monkeypatch):
    """The chat tool must expose the worker's visual fallback, not reject it
    at the dispatcher with the old talking-video-only contract."""
    from types import SimpleNamespace
    import agent_tools
    import db as dbx

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.has_active_job:
                return False
            if fn is dbx.enqueue_job:
                return 654
            raise AssertionError(fn)

    monkeypatch.setattr(agent_tools.llm, "vision_available", lambda: True)
    ctx = SimpleNamespace(
        project={}, has_main_video=True, duration=180.0,
        index={"words": [], "tile_keys": ["tiles/full-video.jpg"]},
        db=FakeDb(), project_id=7, job={"user_id": 60})
    result = agent_tools.make_shorts(ctx, count=2)
    assert "job 654" in result
    assert "visual filmstrips" in result
    assert not result.startswith("REJECTED")


def test_flat_clip_charge_rides_the_turn_charge():
    """charge_turn_credits grew extra_credits — the shorts surcharge must be
    additive and never able to go negative."""
    import inspect
    import db as dbx
    sig = inspect.signature(dbx.charge_turn_credits)
    assert "extra_credits" in sig.parameters
    assert sig.parameters["extra_credits"].default == 0.0


def test_locked_story_selection_has_no_render_surcharge():
    import db as dbx
    import job_completion

    seen = {}

    class FakeWorkerDb:
        def run(self, fn, *args):
            if fn is dbx.finish_accounted_job:
                seen["extra"] = args[5]
                return {"committed": True, "charged": 1.0,
                        "billing_error": None,
                        "qualification_error": None}
            raise AssertionError(fn)

    result = {"clips": 6, "rendered_clips": 0, "billable": True}
    assert job_completion.finalize_success(
        FakeWorkerDb(), {"id": 7, "type": "shorts_plan", "user_id": 3},
        result, "lease") is True
    assert seen["extra"] == 0
