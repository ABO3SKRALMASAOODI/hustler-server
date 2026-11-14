import json
from types import SimpleNamespace as NS

import required_broll
from score import asks


def test_every_named_subject_becomes_durable_ask_with_word_anchor(monkeypatch):
    words = [{'w': x, 't0': float(i), 't1': i + .7} for i, x in enumerate(['Nike', 'Coke', 'Disney'])]
    def record(ctx, name, schema, system, content, **kw):
        assert json.loads(content)['words'][2]['id'] == 2
        return {'pictures': [{'subject': w['w'], 'must_show': w['w'], 'query': w['w'],
                              'word_ids': [i, 999]} for i, w in enumerate(words)]}, {}
    monkeypatch.setattr(asks, '_call', record)
    ledger = {'asks': []}
    required_broll.record(NS(index={'words': words}), ledger, 'Show Nike, Coke and Disney pictures')
    assert [p['subject'] for p in ledger['required_pictures']] == ['Nike', 'Coke', 'Disney']
    assert len(ledger['asks']) == 3
    assert ledger['required_pictures'][2]['source_words'] == [words[2]]


def test_cut_and_insert_remap_picture_to_its_spoken_word():
    p = {'source_words': [{'w': 'Photoshop', 't0': 12, 't1': 12.6}]}
    edl = {'keep': [[10, 15]], 'inserts': [], 'speed': []}
    assert required_broll.anchors(p, edl) == [2.3000000000000007]
    # Dropping or clipping the named word is not silently reassigned to another word.
    assert required_broll.anchors(p, dict(edl, keep=[[12.3, 15]])) == []


def test_wrong_brand_in_render_overrides_general_ask_checker(monkeypatch, tmp_path):
    import db
    import media
    import llm
    import agent_tools
    p = {'id': 'picture1', 'subject': 'Nike', 'must_show': 'Nike product or mark',
         'source_words': [{'w': 'Nike', 't0': 2, 't1': 2.4}]}
    ledger = {'required_pictures': [p], 'lang': 'en'}
    ctx = NS(workdir=str(tmp_path), db=NS(run=lambda *a: {'kind': 'render'}), project_id=1)
    monkeypatch.setattr(agent_tools, '_asset_local_path', lambda *a: 'actual-render.mp4')
    sampled = []
    monkeypatch.setattr(media, 'frame_at', lambda path, t, out, **k: sampled.append((path, t)))
    monkeypatch.setattr(llm, 'image_part', lambda p: {'type': 'image', 'path': p})
    def check(ctx, name, schema, system, content, **kw):
        assert kw['model'] == 'gpt-6-sol'
        return {'shown': False, 'seen': 'Apple logo appears instead', 'line': 'Nike imagery was not delivered.'}, {}
    monkeypatch.setattr(asks, '_call', check)
    result = required_broll.check(ctx, ledger, {'keep': [[1, 5]]}, {'edl_version': 3})
    assert sampled == [('actual-render.mp4', 1.2000000000000002)]
    assert result['picture1']['state'] == 'not_done'
    assert 'Nike' in result['picture1']['line']


def test_missing_render_cannot_claim_picture_done(tmp_path):
    p = {'id': 'picture1', 'subject': 'crypto mining', 'source_words': [{'t0': 1, 't1': 2}]}
    ctx = NS(workdir=str(tmp_path), db=NS(run=lambda *a: None), project_id=1)
    result = required_broll.check(ctx, {'required_pictures': [p]}, {'keep': [[0, 5]]}, {})
    assert result['picture1']['state'] == 'cannot_tell'
    assert 'crypto mining' in result['picture1']['line']
