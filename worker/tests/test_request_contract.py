"""The brief protects what the customer protected, measured on the EDL."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

import agent_loop
import request_contract as rc
import scope_guard
from test_turn_outcomes import _ctx, _changed


def context():
    before = {"keep": [[0.0, 16.0]], "frame": {"ratio": "9:16"},
              "captions": None, "overlays": [], "effects": {}}
    ctx = _changed(_ctx(versions_written=[2], rendered_versions={2},
                       last_preview={"edl_version": 2}, write_attempts=1))
    ctx.request_contract_required = True
    ctx.verification_request = "Preserve voice, subtitles and branding. Add animated typography."
    ctx.user_message = ctx.verification_request
    ctx.turn_start_edl = {"version": 1, "json": deepcopy(before)}
    ctx.latest_edl = lambda: {"version": 2, "json": before}
    ctx.semantic_contract = {
        "schema_version": 1, "request_digest": rc.digest(ctx), "mode": "edit", "output_count": 1,
        "timing_policy": "preserve", "music_requested": False,
        "requirements": [
            {"id": "subtitles", "action": "preserve", "lanes": ["captions"],
             "description": "Preserve original subtitles without duplication.", "quote": "Preserve voice, subtitles and branding."},
            {"id": "type", "action": "change", "lanes": [],
             "description": "Add animated typography.", "quote": "Add animated typography."}],
    }
    return ctx


def test_preservation_is_not_an_instruction_to_add_captions():
    ctx = context()
    before = ctx.latest_edl()["json"]
    after = deepcopy(before)
    after["captions"] = {"mode": "from_transcript"}
    violations = scope_guard.preservation_violations(before, after,
        ctx.user_message, protected=rc.protected_lanes(ctx))
    assert violations == [scope_guard.LANE_LABELS["captions"]]
    after = deepcopy(before)
    after["effects"]["grade_custom"] = {"temperature": .08}
    assert scope_guard.preservation_violations(before, after,
        protected=rc.protected_lanes(ctx)) == []


def test_focused_grade_cannot_silently_shorten_the_video():
    ctx = context()
    after = deepcopy(ctx.latest_edl()["json"])
    after["keep"] = [[0.0, 12.0]]
    assert "picture timing" in scope_guard.preservation_violations(
        ctx.latest_edl()["json"], after, protected=rc.protected_lanes(ctx))[0]


def test_explicit_channel_exclusion_allows_removal_but_not_recreation():
    ctx = context()
    ctx.semantic_contract["requirements"] = [{"id": "no_caps", "action": "avoid", "lanes": ["captions"]}]
    assert rc.forbidden_additions(ctx, {"captions": None}) == []
    assert rc.forbidden_additions(ctx, {"captions": {"mode": "from_transcript"}}) == ["captions"]


def test_semantic_multi_asset_review_is_not_rewritten_using_primary_only(monkeypatch):
    ctx = context()
    monkeypatch.setattr(agent_loop, "_grounding_violations", lambda *a:
                        pytest.fail("Single-source regex checker ran"))
    draft = 'In the uploaded FINAL, the line "rebuild collagen" needs a claims review.'
    assert agent_loop._enforce_honesty(ctx, None, [], [], draft, 1, {}) == draft


def test_repair_can_progress_without_approving_preexisting_damage():
    baseline = {"keep": [[0, 10]], "captions": None, "music": []}
    damaged = {"keep": [[0, 5]], "captions": {"mode": "from_transcript"}, "music": []}
    protected = {"cuts", "captions"}
    # Restore captions without first having to restore the entire timeline.
    one_repair = {**damaged, "captions": None}
    assert not scope_guard.recovery_update_violations(damaged, one_repair, baseline, protected)
    assert scope_guard.preservation_violations(baseline, one_repair, protected=protected) == ["picture cuts"]
    assert scope_guard.recovery_update_violations(
        damaged, {**one_repair, "keep": [[0, 3]]}, baseline, protected) == ["picture cuts"]
    assert not scope_guard.recovery_update_violations(one_repair, baseline, baseline, protected)


def test_new_intro_is_not_blocked_by_an_older_incomplete_episode_snapshot():
    historical = {"keep": [[16, 27], [129, 133]]}
    current = {"keep": [[0, 3600]], "inserts": []}
    with_intro = {**current, "inserts": [{"asset_key": "original", "at_output_s": 0,
                                         "duration_s": 11, "source_start_s": 16}]}
    assert not scope_guard.recovery_update_violations(current, with_intro, historical, {"cuts"})
    assert scope_guard.recovery_update_violations(
        current, {**with_intro, "keep": [[0, 40]]}, historical, {"cuts"})


def test_short_followup_retains_original_request_and_excludes_later_offers():
    rows = [{"id": 1, "role": "user", "content": "Build the fishing edit in the order I supplied."},
            {"id": 2, "role": "assistant", "content": "Analysis ready."},
            {"id": 3, "role": "assistant", "content": "Preview ready."},
            {"id": 4, "role": "user", "content": "You did not do what I asked."},
            {"id": 5, "role": "assistant", "content": "A later, incorrect offer."}]
    history = rc.history(rows, 4)
    assert history[0]["content"] == rows[0]["content"]
    assert all(row["content"] != rows[-1]["content"] for row in history)


def test_requirements_after_four_thousand_characters_are_not_discarded():
    text = "Please edit this. " * 300 + " Keep the original subtitles and do not add music."
    message = {"id": 4, "content": text}
    assert agent_loop._customer_verification_request(None, 1, message, {}) == text
    assert agent_loop._customer_verification_request(None, 1, message,
        {"verification_request": text[:4000]}) == text


def test_timing_protection_allows_replacing_a_shot_in_the_same_window():
    before = {"keep": [[0, 10]], "inserts": [{"id": "i1", "asset_key": "old", "at_output_s": 5, "duration_s": 2}]}
    after = deepcopy(before)
    after["inserts"][0]["asset_key"] = "replacement"
    assert not scope_guard.preservation_violations(before, after, protected={"timeline_timing"})
    after["inserts"][0]["duration_s"] = 3
    assert scope_guard.preservation_violations(before, after, protected={"timeline_timing"})


def test_source_and_explicit_identical_aspect_ratios_are_equivalent():
    assert not scope_guard.preservation_violations({"frame": {"ratio": "source"}},
        {"frame": {"ratio": "9:16"}}, source_shape=(720, 1280), protected={"frame_ratio"})


def test_composition_boundaries_do_not_change_picture_timing():
    before = {"keep": [[6.48, 10.1], [10.1, 14.88], [15.32, 18.44]]}
    after = {"keep": [[6.48, 14.88], [15.32, 18.44]]}
    assert not scope_guard.preservation_violations(before, after, protected={"timeline_timing"})
    after['keep'][0][1] = 14.87
    assert scope_guard.preservation_violations(before, after, protected={"timeline_timing"})


def test_live_steering_keeps_the_whole_brief_and_the_new_preservation_baseline(monkeypatch):
    ctx = context()
    ctx.editing_metrics = {}
    ctx.job = {'id': 91, 'project_id': 9, 'payload': {}}
    text = 'Detailed scene direction. ' * 220 + ' Preserve the subtitles and do not add music.'
    worker_db = NS(run=lambda *args: {'messages': [{'id': 11, 'content': text}], 'job_ids': [92]})
    monkeypatch.setattr(agent_loop, '_attachment_context', lambda *args: '')
    messages = []
    assert agent_loop._adopt_steering_messages(ctx, worker_db, ctx.job, 8, messages, 10) == 11
    assert ctx.verification_request.endswith(text)
    assert ctx.user_message.endswith(text)
    assert ctx.request_preservation_baseline == ctx.latest_edl()['json']


def test_recovery_history_is_bounded_before_limiting_later_retry_replies():
    import os
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import db
    dsn = os.getenv('QUEUE_TEST_DATABASE_URL')
    if not dsn:
        pytest.skip('requires the isolated queue test database')
    with psycopg2.connect(dsn, cursor_factory=RealDictCursor) as conn:
        with conn.cursor() as cursor:
            cursor.execute("CREATE TEMP TABLE chat_messages (id bigint, session_id bigint, role text, content text, meta jsonb) ON COMMIT DROP")
            cursor.executemany("INSERT INTO chat_messages VALUES (%s,%s,%s,%s,'{}')", [
                (98,7,'user','Add music'),(99,7,'assistant','Original offer'),
                (100,7,'user','2'),(99,8,'assistant','Other customer'),
                (99,7,'activity','Tool log'),
                *[(i,7,'assistant','Later retry offer') for i in range(101,140)]])
        past = db.recent_chat(conn,7,4,before_id=100)
        assert [row['id'] for row in past] == [98,99,100]
        assert past[-2]['content'] == 'Original offer'
        assert [row['id'] for row in db.recent_chat(conn,7,2)] == [138,139]


def test_caption_render_does_not_require_proof_that_only_rendering_can_create():
    ctx = context()
    ctx.semantic_contract["requirements"] = [{"id": "nature", "action": "change", "lanes": [],
        "description": "Aesthetically pleasing nature-like captions"}]
    ctx.latest_edl()["json"]["captions"] = {"mode": "from_transcript", "style": {
        "font": "Instrument Serif", "color": "#F3EFE4", "highlight_color": "#D9B26F"}}
    ctx.last_preview = None
    ctx.verification_records = {}
    assert rc.preflight(ctx) is None
    revised = deepcopy(ctx.latest_edl()["json"])
    revised["captions"]["style"]["color"] = "#CCD6B2"
    ctx.latest_edl = lambda: {"version": 3, "json": revised}
    assert rc.preflight(ctx) is None


def test_pre_render_still_rejects_concrete_preservation_and_exclusion_violations():
    ctx = context()
    ctx.latest_edl()["json"]["captions"] = {"mode": "from_transcript"}
    ctx.semantic_contract["requirements"].append({"id": "no_music", "action": "avoid", "lanes": ["music"]})
    ctx.latest_edl()["json"]["music"] = [{"asset_key": "not-requested.mp3"}]
    message = rc.preflight(ctx)
    assert "subtitles" in message and "no_music" in message
    ctx.latest_edl()["json"]["captions"] = None
    ctx.latest_edl()["json"]["music"] = []
    assert rc.preflight(ctx) is None


def test_an_answer_is_the_deliverable_even_with_nothing_on_the_timeline():
    ctx = context()
    ctx.has_main_video = False
    ctx.versions_written = []
    ctx.rendered_versions = set()
    ctx.last_preview = None
    ctx.write_attempts = 0
    ctx.turn_tool_outcomes = []
    ctx.semantic_contract["mode"] = "inspect"
    assert agent_loop._turn_completion(ctx)[0] == "fulfilled"


def test_analysis_requests_forbid_writes():
    ctx = context()
    ctx.semantic_contract["mode"] = "inspect"
    with pytest.raises(ValueError, match="analysis"):
        rc.protected_lanes(ctx)


def test_a_new_instruction_needs_its_own_brief_before_any_write():
    ctx = context()
    assert rc.protected_lanes(ctx)
    ctx.verification_request = "Now replace the soundtrack."
    with pytest.raises(ValueError, match="current request"):
        rc.protected_lanes(ctx)


def test_durable_checkpoint_keeps_the_brief():
    ctx = context()
    saved = agent_loop._preview_checkpoint(ctx)
    fresh = context()
    fresh.semantic_contract = None
    agent_loop._restore_preview_checkpoint(fresh, saved)
    assert fresh.semantic_contract == ctx.semantic_contract
