"""Customer-facing edit/review/answer state survives tool and slice boundaries."""
import json
from types import SimpleNamespace

import pytest

import agent_loop
import agent_tools
import audit
import edl_read
import music_gate
import music_library
import preview_budget
import quality_verifier
import read_tools
import trial_reply
from schemas import default_edl
from w0c_support import tool_context


def context(version=30, count=0):
    row = {'version': version, 'json': default_edl(20)}
    return SimpleNamespace(latest_edl=lambda: row, trial_preview_cap=2,
                           trial_previews=count, last_preview=None,
                           verification_records={}, row=row)


def rendered(ctx, version, defects=()):
    ctx.row['version'] = version
    ctx.last_preview = {'edl_version': version, 'asset_id': 100 + version}
    ctx.verification_records[version] = {'unresolved_findings': [
        {'finding_id': f, 'code': 'review_finding', 'severity': 'error'}
        for f in defects]}
    agent_loop._count_trial_preview(ctx, 'Preview rendered')


def test_diagnostic_proof_and_full_preview_leave_room_for_repair():
    ctx = context()
    for reply in ['Changed-section verification completed', 'REJECTED: preflight',
                  'PREREQUISITE: waiting for queued preview', 'TRANSIENT_FAILURE']:
        agent_loop._count_trial_preview(ctx, reply)
    assert ctx.trial_previews == 0
    rendered(ctx, 30, ['quiet_audio', 'dead_air'])
    assert ctx.trial_previews == 1
    assert agent_loop._trial_render_refusal(ctx, 'set_master_loudness') is None
    # Re-reading the same completed job cannot consume another allowance.
    agent_loop._count_trial_preview(ctx, 'Preview rendered')
    assert ctx.trial_previews == 1
    rendered(ctx, 31)
    assert ctx.trial_previews == 2
    assert agent_loop._trial_render_refusal(ctx, 'add_text').startswith('REJECTED')


def test_proven_repair_reservation_survives_checkpoint_and_stops_no_progress():
    ctx = context()
    rendered(ctx, 17, ['speech', 'quiet_audio'])
    rendered(ctx, 21, ['speech'])
    assert agent_loop._trial_render_refusal(ctx, 'cut_range') is None
    ctx.row['version'] = 22  # a repair has been written, awaiting verification
    saved = json.loads(json.dumps(preview_budget.checkpoint(ctx)))
    next_ctx = context(22, ctx.trial_previews)
    preview_budget.restore(next_ctx, saved)
    assert agent_loop._trial_render_refusal(next_ctx, 'render_preview') is None
    rendered(next_ctx, 22, ['speech'])
    assert agent_loop._trial_render_refusal(next_ctx, 'cut_range').startswith('REJECTED')
    assert len(next_ctx.trial_preview_repairs['history']) == 1


def test_repair_cycles_require_strict_progress_and_have_a_hard_backstop():
    ctx = context(count=2)
    for version, remaining in [(1, 5), (2, 4), (3, 3)]:
        rendered(ctx, version, range(remaining))
        assert agent_loop._trial_render_refusal(ctx, 'cut_range') is None
    rendered(ctx, 4, range(2))
    assert agent_loop._trial_render_refusal(ctx, 'cut_range').startswith('REJECTED')


def test_legacy_checkpoint_uses_render_receipts_not_old_call_count():
    ctx = context(count=2)
    preview_budget.restore(ctx, {'trial_previews': 2, 'rendered_versions': [30]})
    assert ctx.trial_previews == 1
    assert agent_loop._trial_render_refusal(ctx, 'set_master_loudness') is None


def test_render_adopted_at_resume_is_counted_before_another_tool_runs():
    ctx = context()
    ctx.rendered_versions = {30, 31}
    preview_budget.restore(ctx, {'trial_preview_versions': [30], 'trial_previews': 1})
    assert ctx.trial_previews == 2 and ctx.trial_preview_versions == [30, 31]


def test_resume_closes_a_repair_reservation_whose_render_finished():
    ctx = context(count=2)
    rendered(ctx, 21, ['speech'])
    assert agent_loop._trial_render_refusal(ctx, 'cut_range') is None
    saved = preview_budget.checkpoint(ctx)
    resumed = context(22)
    resumed.rendered_versions = {21, 22}
    rendered(resumed, 22, ['speech'])
    preview_budget.restore(resumed, saved)
    assert agent_loop._trial_render_refusal(resumed, 'cut_range').startswith('REJECTED')
    assert agent_loop._trial_render_refusal(resumed, 'render_preview', {'complete': True}) is None


def test_exhausted_allowance_does_not_block_reading_a_stored_review():
    ctx = context(count=2)
    assert agent_loop._trial_render_refusal(ctx, 'render_preview', {'report': True}) is None


def test_prepared_edit_can_repair_a_proven_defect_but_not_an_advisory():
    ctx = context(count=2)
    ctx.claude_trial = object()
    ctx.trial_prep = {'intent': 'first_cut'}
    rendered(ctx, 2, ['quiet_audio'])
    assert agent_loop._trial_render_refusal(ctx, 'set_master_loudness') is None
    other = context(count=2)
    rendered(other, 2)
    other.verification_records[2] = {'unresolved_findings': [
        {'code': 'taste_advisory', 'severity': 'advisory'}]}
    assert agent_loop._trial_render_refusal(other, 'add_text').startswith('REJECTED')


def words(text, starts=None):
    return [{'w': w, 't0': t, 't1': t + .2}
            for w, t in zip(text.split(), starts or [1 + i * .25
                                                    for i in range(len(text.split()))])]


@pytest.mark.parametrize('tokens,starts', [
    ('water shortage water shortage', [1, 1, 1, 1]),
    ('water shortage water shortage', [1, 1.2, 1.25, 1.45]),
    ('पानी कहा पनी कहा', [1, 1.25, 1.5, 1.75]),
])
def test_ambiguous_alignment_and_different_unicode_words_never_prove_a_repeat(tokens, starts):
    rows = words(tokens, starts)
    assert audit.repeated_phrases([[0, 10]], rows) == []
    assert not quality_verifier._repeat_findings(default_edl(10), {'words': rows})


def test_real_in_sentence_repetition_is_removed_without_restoring_the_clause():
    ctx, db = tool_context(default_edl(10), duration=10)
    ctx.claude_trial = object()
    ctx._editor_tool = 'cut_range'
    ctx.index['words'] = words('We need water need water today.')
    hits = audit.repeated_phrases([[0, 10]], ctx.index['words'])
    assert hits and hits[0]['text'] == 'need water need water'
    result = agent_tools.execute(ctx, 'cut_range', {
        'start': hits[0]['cut'][0], 'end': hits[0]['cut'][1], 'snap_to_words': False})
    assert result.startswith('EDL v'), result
    after = ctx.latest_edl()['json']
    assert after['keep'] != [[0, 10]]
    assert not audit.repeated_phrases(after['keep'], ctx.index['words'])
    assert after['keep'][0][0] == 0 and after['keep'][-1][1] == 10


@pytest.mark.parametrize('draft', [
    "The edit is assembled, but it isn't finished. The loudness fix and dead-air "
    "trims were rejected, so the pauses remain. This version should not be handed off.",
    "The repeated-word stutters are still in the cut: my two attempts to remove "
    "them were rejected, so this version is not ready to hand off.",
])
def test_incomplete_execution_cannot_be_rewritten_as_done(draft):
    facts = trial_reply.Facts(before=default_edl(20), after=default_edl(20),
                              wrote=True, previewed=True, duration_s=20,
                              outcome='partial')
    text, report = trial_reply.finish(draft, facts)
    assert 'incomplete' in text
    assert 'rejected' in text
    assert 'Done' not in text and 'Want captions' not in text
    assert report['outcome'] == 'partial'


def test_music_number_is_bound_to_previous_offer_before_another_list_can_replace_it():
    ctx, db = tool_context(default_edl(20), user_message='2', job_id=1)
    tracks = music_library.catalog()[:3]
    assert len(tracks) == 3
    question = '; '.join(f'{i}. "{t["title"]}"' for i, t in enumerate(tracks, 1))
    music_gate.open_offer(ctx, question)
    ctx.job['id'] = 2
    note = music_gate.choice_note(ctx, '2', question)
    assert tracks[1]['slug'] in note
    result = agent_tools.ask_user(ctx, 'Choose: "' + tracks[0]['title'] + '"')
    assert result.startswith('REJECTED')
    # Even a later changed offer cannot change the provenance of this pick.
    music_gate.open_offer(ctx, '"' + tracks[0]['title'] + '"')
    pick, error = music_gate.resolve_pick(ctx, tracks[1]['slug'])
    assert error is None and pick['evidence'] == '2'
    resumed, _ = tool_context(default_edl(20), user_message='2', job_id=3)
    resumed.db = db
    assert music_gate.picked(resumed, tracks[1]['slug'])['evidence'] == '2'


@pytest.mark.parametrize('answer,previous_reply', [
    ('2', 'Choose a title style: 1. Bold; 2. Small'),
    ('2', None),
    ('Make 2 clips', 'music_offer'),
])
def test_a_stale_offer_or_unrelated_number_never_grants_a_music_pick(answer, previous_reply):
    ctx, db = tool_context(default_edl(20), user_message=answer, job_id=1)
    tracks = music_library.catalog()[:3]
    question = '; '.join(f'{i}. "{t["title"]}"' for i, t in enumerate(tracks, 1))
    music_gate.open_offer(ctx, question)
    ctx.job['id'] = 2
    assert not music_gate.choice_note(ctx, answer, question if previous_reply == 'music_offer'
                                     else previous_reply)
    assert not getattr(ctx, '_music_picks', None)


def test_naming_a_track_to_reject_it_is_not_a_selection():
    ctx, db = tool_context(default_edl(20), job_id=2)
    track = music_library.catalog()[0]
    assert not music_gate.choice_note(ctx, 'Do not use ' + track['title'])
    assert not getattr(ctx, '_music_picks', None)
    assert track['slug'] in music_gate.choice_note(ctx, 'Use ' + track['title'])


@pytest.mark.parametrize('selector', ['["volume", "inserts"]', '"volume"'])
def test_encoded_read_sections_return_the_actual_requested_state(selector):
    edl = default_edl(20)
    edl['volume'] = [{'start': 0, 'end': 20, 'gain_db': -60}]
    result = json.loads(edl_read.read_edl({'version': 28, 'json': edl}, 20,
                                        [], sections=selector))
    assert result['sections']['volume'] == edl['volume']
    assert not result.get('unknown_sections')


def test_uploaded_clip_word_read_uses_its_index_and_its_duration():
    calls = []
    def run(fn, *args):
        calls.append((fn, args))
        if fn is read_tools.asset_by_key:
            assert args == (7, 'clips/7/extra.mp4')
            return {'sha256': 'extra', 'duration_s': 300}
        assert fn is read_tools.get_index_by_sha and args == ('extra',)
        return {'json': {'video': {'duration': 300},
                         'words': [{'w': 'extra-clip-word', 't0': 240, 't1': 241}]}}
    ctx = SimpleNamespace(project_id=7, db=SimpleNamespace(run=run),
                          duration=20, index={'words': words('main source')})
    result = read_tools.get_words(ctx, start=238, end=250,
                                 asset_key='clips/7/extra.mp4')
    assert '240.00-241.00 extra-clip-word' in result
    assert 'CLIP seconds, not output seconds' in result
    assert len(calls) == 2


def test_repetition_cannot_cherry_pick_clean_tokens_inside_ambiguous_alignment():
    rows = words('unreliable water shortage water shortage',
                 [1, 1, 1.25, 1.5, 1.75])
    assert not audit.repeated_phrases([[0, 10]], rows)


@pytest.mark.parametrize('cache_state', ['replaced', 'expired', 'same_job'])
def test_a_retry_binds_the_original_visible_offer_even_after_cache_changes(cache_state):
    ctx, db = tool_context(default_edl(20), user_message='2', job_id=1)
    tracks = music_library.catalog()[:3]
    question = '; '.join(f'{i}. "{t["title"]}"' for i, t in enumerate(tracks, 1))
    music_gate.open_offer(ctx, question)
    ctx.job['id'] = 2
    if cache_state == 'expired':
        ctx._music_offer['at'] = 0
    else:
        music_gate.open_offer(ctx, '; '.join(t['title'] for t in tracks[1:] + tracks[:1]))
        if cache_state == 'replaced':
            ctx.job['id'] = 3
    note = music_gate.choice_note(ctx, '2', question, previous_reply_id=100)
    assert tracks[1]['slug'] in note
    picked, error = music_gate.resolve_pick(ctx, tracks[1]['slug'])
    assert error is None
    assert picked['offer_message_id'] == 100
    assert picked['evidence'] == '2'
