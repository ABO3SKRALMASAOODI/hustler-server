"""Failure regressions from a paid customer's repeated repair chain."""
import copy
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import agent_tools
import agent_loop
import db
import edl_read
import edit_batch
from schemas import default_edl


def test_compact_catalog_keeps_batch_and_animation_contracts(monkeypatch):
    monkeypatch.setattr(agent_tools, '_tool_disabled', lambda *a: False)
    schemas = {s['function']['name']: s['function'] for s in
               agent_tools.openai_tools(compact=True, names={
                   'apply_edit_batch', 'add_text', 'set_text_motion'})}
    batch = schemas['apply_edit_batch']
    assert 'set replaces one complete layer' in batch['description']
    props = batch['parameters']['properties']['operations']['items']['properties']
    assert set(props['layer']['enum']) == edit_batch.LAYERS
    assert 'upsert' in props['value']['description']
    assert 'bounded by end-start' in schemas['add_text']['description']


def test_section_csv_and_overview_aliases_are_lossless():
    edl = default_edl(30)
    result = json.loads(edl_read.read_edl(
        {'version': 2, 'json': edl}, 30, None, sections='texts, frames, program_map'))
    assert result['sections']['texts'] == edl['texts']
    assert result['sections']['frame'] == edl['frame']
    assert result['overview']['program_duration_s'] == 30


def test_item_set_alias_keeps_siblings_and_atomic_validation():
    edl = default_edl(30)
    edl['texts'] = [{'id': 't1', 'text': 'A', 'start': 0, 'end': 5},
                    {'id': 't2', 'text': 'B', 'start': 5, 'end': 10}]
    before = copy.deepcopy(edl)
    operations = [{'action': 'set', 'layer': 'text', 'id': 't1',
                   'value': {'text': 'NEW'}}]
    out = edit_batch.apply_batch(edl, operations, 30, {})
    assert [t['text'] for t in out['texts']] == ['NEW', 'B']
    assert edl == before and operations[0]['layer'] == 'text'
    with pytest.raises(ValueError):
        edit_batch.apply_batch(edl, operations + [{'action': 'set', 'layer': 'bad'}], 30, {})
    assert edl == before


def test_silent_source_is_not_downloaded_and_extracted_on_every_retry(monkeypatch):
    calls = []
    ctx = SimpleNamespace(project_id=1, workdir='/unused', tool_failure_memory={},
                          db=SimpleNamespace(run=lambda *a, **kw: None))
    asset = {'id': 4, 'sha256': 'one', 'storage_key': 'clips/one.mp4'}
    monkeypatch.setattr(agent_tools, '_asset_local_path', lambda *a: calls.append(1) or '/silent')
    def silent(*a):
        raise agent_tools.media.MediaError('no audio stream')
    monkeypatch.setattr(agent_tools.media, 'extract_audio_track', silent)
    assert 'silent video' in agent_tools._audio_from_clip(ctx, asset)[2]
    # Recreate a worker context using only JSON-safe checkpoint state.
    resumed = copy.copy(ctx)
    resumed.tool_failure_memory = json.loads(json.dumps(ctx.tool_failure_memory))
    assert 'silent video' in agent_tools._audio_from_clip(resumed, asset)[2]
    assert len(calls) == 1
    agent_tools._audio_from_clip(resumed, dict(asset, sha256='replacement'))
    assert len(calls) == 2


def test_authentication_failure_is_not_retried_with_another_query(monkeypatch):
    calls = []
    ctx = SimpleNamespace(tool_failure_memory={})
    monkeypatch.setattr(agent_tools.sfx_search, 'available', lambda: True)
    def failed(*a, **kw):
        calls.append(1)
        raise agent_tools.sfx_search.SfxSearchError('HTTP 401 Unauthorized')
    monkeypatch.setattr(agent_tools.sfx_search, 'search', failed)
    assert agent_tools.search_sfx(ctx, 'wind in trees').startswith('UNAVAILABLE')
    assert agent_tools.search_sfx(ctx, 'forest ambience').startswith('UNAVAILABLE')
    assert len(calls) == 1


def test_stopped_parent_cannot_enqueue_a_continuation():
    queries = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, args): queries.append(sql)
        def fetchone(self): return None
    with pytest.raises(db.JobLeaseLost):
        db.enqueue_agent_continuation(SimpleNamespace(cursor=Cursor),
                                      1, 2, 3, 4, {}, 8, 2)
    assert not any('INSERT' in sql for sql in queries)


def test_stopped_parent_cannot_write_a_timeline_version():
    queries = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, args): queries.append(sql)
        def fetchone(self): return None
    with pytest.raises(db.JobLeaseLost):
        db.insert_edl(SimpleNamespace(cursor=Cursor), 1, {}, 'agent',
                      job_id=8, before_version=4, total_claims=2)
    assert not any('INSERT' in sql for sql in queries)


def test_activity_records_agent_outcome_for_reliability_counting():
    calls = []
    agent_loop._activity(SimpleNamespace(run=lambda *a: calls.append(a)),
                         2, 'add_text', {}, 'REJECTED: invalid time range')
    meta = calls[0][-1]
    assert meta['source'] == 'agent'
    assert meta['tool_outcome']['status'] == 'correction_needed'


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='requires ffmpeg')
def test_audio_presence_rejects_zero_samples_but_keeps_quiet_late_sound(tmp_path):
    silent = str(tmp_path / 'silent.wav')
    audible = str(tmp_path / 'quiet.wav')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'anullsrc=r=48000:cl=stereo', '-t', '1', silent], check=True)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=1', '-af',
                    'volume=-40dB,adelay=1500', audible], check=True)
    assert not agent_tools.media.audio_has_signal(silent)
    assert agent_tools.media.audio_has_signal(audible)
    with pytest.raises(agent_tools.media.MediaError, match='silent samples'):
        agent_tools.media.extract_audio_track(silent, str(tmp_path / 'empty.m4a'))
    assert not (tmp_path / 'empty.m4a').exists()
