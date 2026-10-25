"""Oct 2026: quality gates block real defects only.

Taste heuristics, subjective listening opinions and motion judged from stills
used to make a premium reel repair_required, push repair after repair and
spawn up to three continuation slices before refusing export. These tests pin
the contract: advisories are visible but never gate; a request keeps working
on NEW findings and stops only when the same findings come back after a full
repair slice (25 Oct: no preview/time cap on a request).
"""
import json
import time
from types import SimpleNamespace as NS

import agent_loop
import agent_tools
import finishing_review
from schemas import canvas_edl


def _handoff_ctx(**overrides):
    values = {
        "last_preview": {"edl_version": 3},
        "last_visual_critic": {"verdict": "pass", "findings": []},
        "last_audio_qc_findings": [],
        "last_audio_review": None,
        "last_taste": [],
        "last_taste_version": 3,
        "last_taste_advisory": ["13 sound events in 45s — ..."],
    }
    values.update(overrides)
    ctx = NS(**values)
    ctx.latest_edl = lambda: {"version": 3}
    return ctx


def test_advisory_taste_notes_never_close_export():
    quality = agent_loop._quality_handoff(_handoff_ctx())
    assert quality["quality_status"] == "pass"
    assert quality["export_ready"] is True


def test_subjective_listening_opinion_is_advisory_but_masking_blocks():
    opinion = {"edl_version": 3, "verdict": "fix", "category": "sfx_choice",
               "text": "FIX [sfx_choice] at 4.0s: the whoosh feels busy."}
    quality = agent_loop._quality_handoff(
        _handoff_ctx(last_audio_review=opinion))
    assert quality["export_ready"] is True
    assert not agent_tools.audio_review_blocks(opinion)

    masking = dict(opinion, category="speech_masking",
                   text="FIX [speech_masking] at 4.0s: music buries the line.")
    quality = agent_loop._quality_handoff(
        _handoff_ctx(last_audio_review=masking))
    assert quality["quality_status"] == "repair_required"
    assert agent_tools.audio_review_blocks(masking)


def test_audio_verification_findings_keep_only_objective_defects():
    ctx = NS(last_audio_qc_findings=[],
             last_audio_review={"verdict": "fix", "category": "tone_mismatch",
                                "text": "FIX [tone_mismatch]: music too upbeat"})
    assert agent_tools._audio_verification_findings(ctx) == []
    assert agent_tools._audio_review_advisories(ctx)[0].startswith(
        "actual-audio review (advisory: keep if intentional)")
    ctx.last_audio_review = dict(ctx.last_audio_review, category="level")
    assert len(agent_tools._audio_verification_findings(ctx)) == 1


def test_quality_repair_pushback_is_once_per_version_with_a_backstop():
    report = {"verdict": "repair", "findings": [{
        "severity": "major", "category": "caption_collision", "time_s": 2.0,
        "target_id": None, "motion_motif": None,
        "evidence": "captions sit across the speaker's mouth",
        "repair": "move the captions below the chin", "confidence": .95}]}
    version = {"v": 6}
    ctx = NS(last_visual_critic=report, last_audio_review=None,
             last_story_review=None, last_preview={"edl_version": 6},
             latest_edl=lambda: {"version": version["v"]})
    messages, pushed = [], set()
    assert agent_loop._quality_repair_pushback(
        ctx, messages, time.monotonic(), pushed)
    # The same version is never pushed twice.
    assert not agent_loop._quality_repair_pushback(
        ctx, messages, time.monotonic(), pushed)
    for v in range(7, 7 + agent_loop.MAX_QUALITY_REPAIR_PUSHBACKS + 1):
        version["v"] = v
        ctx.last_preview = {"edl_version": v}
        agent_loop._quality_repair_pushback(
            ctx, messages, time.monotonic(), pushed)
    assert len(messages) == agent_loop.MAX_QUALITY_REPAIR_PUSHBACKS


def _loop_ctx(monkeypatch):
    from test_director_blueprint import _tool_ctx

    ctx, db = _tool_ctx()
    edl = canvas_edl('9:16')
    edl['keep'] = []
    edl['inserts'] = [dict(id='ins1', kind='video', asset_key='clip1',
                           at_output_s=0, duration_s=20, source_start_s=0)]
    edl['texts'] = [dict(id='tx1', text='bad � title', start=0.0,
                         end=1.0)]
    row = {'version': 73, 'json': edl}
    db.rows = [row]
    ctx.versions_written = [73]
    ctx.rendered_versions.add(73)
    ctx.last_preview = {'edl_version': 73, 'duration_s': 20, 'asset_id': 5}
    ctx.verification_request = 'Make a reel'
    ctx.user_message = ctx.verification_request
    ctx.turn_start_edl = row
    ctx.turn_baseline_digest = 'before'
    ctx.over_budget = lambda: False
    ctx.adopted_steer_job_ids = set()
    ctx._proof_ranges_by_version = {}
    ctx.verification_records[73] = dict(
        status='repair_required', complete_preview_passed=True,
        unresolved_findings=[dict(code='corrupt_glyph', severity='error',
                                  message='Text contains replacement/null '
                                          'glyphs and cannot ship.')])
    monkeypatch.setattr(agent_loop, '_build_messages', lambda *a, **k: [
        {'role': 'user', 'content': ctx.user_message}])
    monkeypatch.setattr(agent_loop, '_adopt_steering_messages', lambda *a: 1)
    monkeypatch.setattr(agent_loop, '_activity', lambda *a, **k: None)
    monkeypatch.setattr(agent_loop.llm, 'responses_available',
                        lambda *a, **k: False)
    monkeypatch.setattr(agent_loop.llm, 'record', lambda *a: None)
    monkeypatch.setattr(agent_loop, '_auto_render_if_needed',
                        lambda *a: (ctx.latest_edl(), ''))
    monkeypatch.setattr(agent_loop, '_enforce_honesty',
                        lambda _c, _cl, _m, _t, draft, *a, **k: draft)
    monkeypatch.setattr(agent_loop, '_enforce_reply_language',
                        lambda _c, _cl, _m, _t, draft, **k: draft)

    def create(**kwargs):
        return NS(choices=[NS(message=NS(content='Here is your reel.',
                                         tool_calls=[]),
                              finish_reason='stop')], usage=None)
    ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    posted, checkpoint = [], {}
    original_run = db.run

    def run(fn, *args):
        import db as dbx
        if fn is dbx.enqueue_agent_continuation:
            checkpoint.update(args[4]['continuation_state'])
            return 99
        if fn is dbx.add_message:
            posted.append((args[2], args[3]))
        return original_run(fn, *args) or 0
    db.run = run
    return ctx, db, posted, checkpoint


def test_unresolved_real_defect_gets_a_continuation_slice(monkeypatch):
    ctx, db, posted, checkpoint = _loop_ctx(monkeypatch)
    result = agent_loop._run_loop(
        ctx, db, {'id': 3, 'project_id': 9, 'user_id': 8, 'payload': {}},
        2, {'id': 1, 'content': ctx.user_message})
    assert result['status'] == 'continued'
    assert checkpoint['why'] == 'verification repair remains'
    assert checkpoint['verification_slices'] == 1
    # The one in-slice repair pushback was spent on v73 first.
    assert checkpoint['quality_repair_versions'] == [73]
    assert posted == []


def test_new_findings_after_a_repair_slice_keep_working(monkeypatch):
    ctx, db, posted, checkpoint = _loop_ctx(monkeypatch)
    cont = {'why': 'verification repair remains', 'verification_slices': 1,
            'blocker_fingerprint': 'a different unresolved set',
            'work_slices': 1, 'n': 1, 'steps': 0, 'start_version': 72,
            'quality_repair_versions': [72, 73]}
    result = agent_loop._run_loop(
        ctx, db, {'id': 4, 'project_id': 9, 'user_id': 8, 'payload': {}},
        2, {'id': 1, 'content': ctx.user_message}, _cont=cont)
    assert result['status'] == 'continued'
    assert checkpoint['verification_slices'] == 2
    assert posted == []


def _stuck_cont(ctx, **extra):
    fingerprint, _state = agent_loop._objective_blocker_fingerprint(
        getattr(ctx, 'edit_plan', None), ctx.verification_records[73])
    cont = {'why': 'verification repair remains', 'verification_slices': 1,
            'blocker_fingerprint': fingerprint, 'blocker_repeats': 1,
            'work_slices': 1, 'n': 1, 'steps': 0, 'start_version': 72,
            'quality_repair_versions': [72, 73]}
    cont.update(extra)
    return cont


def test_same_findings_after_a_repair_slice_hold_only_for_safety(
        monkeypatch):
    ctx, db, posted, checkpoint = _loop_ctx(monkeypatch)
    # The previous slice repaired and came back with exactly this set.
    cont = _stuck_cont(ctx)
    result = agent_loop._run_loop(
        ctx, db, {'id': 4, 'project_id': 9, 'user_id': 8, 'payload': {}},
        2, {'id': 1, 'content': ctx.user_message}, _cont=cont)
    assert result['status'] == 'replied'
    assert checkpoint == {}
    assert result['export_ready'] is False
    text, meta = posted[-1]
    assert text.startswith('Here is your reel.')
    assert 'Verification remains open' in text
    assert meta['quality_status'] == 'repair_required'


def test_finishing_directive_labels_taste_lines_advisory():
    edl = canvas_edl('9:16')
    edl['keep'] = []
    edl['inserts'] = [dict(id='ins1', kind='video', asset_key='clip1',
                           at_output_s=0, duration_s=40, source_start_s=0)]
    edl['effects'] = {'fade_in_s': 1.0}
    row = {'version': 5, 'json': edl}
    ctx = NS(versions_written=[5], index={'video': {'width': 1080,
                                                    'height': 1920}},
             verification_request='make a reel', verification_records={},
             latest_edl=lambda: row)
    note = finishing_review.directive(ctx)
    assert 'fade from BLACK' in note
    assert 'advisory: keep if intentional' in note
    assert 'Repair these current findings' not in note
    assert json.dumps(finishing_review.current_findings(ctx, row)) == '[]'


# 25 Oct 2026: no preview/time cap on a request. When the same findings come
# back after a full repair slice, the best version is delivered with an open
# export; the request is fulfilled when the customer's own contract is met.

def _advisory_loop_ctx(monkeypatch, capture=None):
    ctx, db, posted, checkpoint = _loop_ctx(monkeypatch)
    edl = ctx.latest_edl()['json']
    edl['texts'] = [dict(id='tx1', text='clean title', start=0.0, end=1.0)]
    ctx.verification_records[73] = dict(
        status='repair_required', complete_preview_passed=True,
        unresolved_findings=[dict(code='story_gap', severity='error',
                                  message='The middle beat drags.')])
    if capture is not None:
        def create(**kwargs):
            capture.append(kwargs)
            return NS(choices=[NS(message=NS(content='Here is your reel.',
                                             tool_calls=[]),
                                  finish_reason='stop')], usage=None)
        ctx.llm_client = NS(chat=NS(completions=NS(create=create)))
    return ctx, db, posted, checkpoint


def test_stuck_reviewer_notes_deliver_a_fulfilled_request_with_export(
        monkeypatch):
    ctx, db, posted, checkpoint = _advisory_loop_ctx(monkeypatch)
    result = agent_loop._run_loop(
        ctx, db, {'id': 4, 'project_id': 9, 'user_id': 8, 'payload': {}},
        2, {'id': 1, 'content': ctx.user_message}, _cont=_stuck_cont(ctx))
    assert result['status'] == 'replied'
    assert checkpoint == {}
    assert result['export_ready'] is True
    text, meta = posted[-1]
    assert text == 'Here is your reel.'          # no disclaimer
    assert meta['quality_status'] == 'advisory'
    assert meta['contract_met'] is True
    assert meta['quality_findings'] == ['The middle beat drags.']
    assert result['outcome'] == 'fulfilled'


def test_unmet_customer_contract_is_named_and_partial(monkeypatch):
    ctx, db, posted, checkpoint = _advisory_loop_ctx(monkeypatch)
    ctx.verification_request = 'Make a 45 second reel'
    result = agent_loop._run_loop(
        ctx, db, {'id': 4, 'project_id': 9, 'user_id': 8, 'payload': {}},
        2, {'id': 1, 'content': ctx.user_message},
        _cont=_stuck_cont(ctx, verification_slices=4))
    assert result['status'] == 'replied'
    text, meta = posted[-1]
    assert 'Still open from your request' in text
    assert 'duration target' in meta['quality_findings'][0]
    assert meta['contract_met'] is False and meta['export_ready'] is True
    assert result['outcome'] == 'partial'


def test_no_preview_or_time_cap_on_a_request(monkeypatch):
    calls = []
    ctx, db, posted, checkpoint = _advisory_loop_ctx(monkeypatch, calls)
    # A long request: 40 previews and two hours in. Older checkpoints may
    # still carry the removed budget counters; they change nothing.
    cont = {'why': 'productive work remains', 'work_slices': 1, 'n': 1,
            'steps': 3, 'start_version': 72, 'request_previews': 40,
            'request_t0': time.time() - 7200}
    agent_loop._run_loop(
        ctx, db, {'id': 5, 'project_id': 9, 'user_id': 8, 'payload': {}},
        2, {'id': 1, 'content': ctx.user_message}, _cont=cont)
    assert calls and calls[0].get('tools')
    assert all('REQUEST BUDGET' not in str(m.get('content'))
               for m in calls[-1]['messages'])
    assert not hasattr(agent_tools, 'request_preview_refusal')
    assert not hasattr(agent_loop.config, 'AGENT_REQUEST_MAX_PREVIEWS')


def test_flash_and_corrupt_glyph_findings_still_hold_export():
    for code in ('flash_rate_exceeded', 'corrupt_glyph'):
        ctx = _handoff_ctx(
            critics_advisory=True,
            last_visual_critic={"verdict": "pass", "findings": []},
            verification_records={3: {
                "status": "repair_required",
                "unresolved_findings": [{"code": code, "message": "x"}]}})
        assert agent_loop._quality_handoff(ctx)["quality_status"] \
            == "repair_required"
