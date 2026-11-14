"""Live K8 regressions: promised shape, motivated camera, source spelling."""
import copy
import json
import random
from types import SimpleNamespace

import pytest
from score import compiler, pace, scout
from tests.test_score_compiler import wordless_index


@pytest.mark.parametrize("frame", ["16:9", "1:1", [1920, 1080]])
def test_first_film_contract_survives_a_landscape_model_answer(frame):
    idx = wordless_index(4)
    idx['first_film'] = {'frame': '9:16'}
    text = '\n'.join(json.dumps(x) for x in [
        {'k': 'film', 'frame': frame, 'fps': 30},
        {'k': 'b', 'id': 'b01', 'at': '@0..@4', 'pic': 'main'},
    ])
    film = compiler.compile_score(text, index=idx, govern=False)
    assert film.l1['frame'] == [1080, 1920]
    idx.pop('first_film')
    assert compiler.compile_score(text, index=idx, govern=False).l1['frame'] != [1080, 1920]


def test_source_text_spelling_is_not_optional_or_overwritten_by_director():
    idx = {'words': [{'w': 'fatima', 't0': 1, 't1': 1.4},
                     {'w': 'jadot', 't0': 1.4, 't1': 2}]}
    before = copy.deepcopy(idx)
    fixed = scout.printed_spellings(idx, {'fixes': [
        {'w': 0, 't': 'Fatima', 'evidence': 'screen', 'read': 'A conversation with Fatima Jakoet'},
        {'w': 1, 't': 'Jakoet', 'evidence': 'screen', 'read': 'A conversation with Fatima Jakoet'},
    ]})
    assert idx == before
    assert [(w['t0'], w['t1']) for w in fixed['words']] == [(1, 1.4), (1.4, 2)]
    ctx = compiler.Ctx(fixed)
    compiler.read_fix(ctx, {'w': '0-1', 't': 'fatima jadot'})
    assert ctx.text == {0: 'Fatima', 1: 'Jakoet'}


def test_conflicting_or_unsubstantiated_spellings_do_not_replace_the_transcript():
    idx = {'words': [{'w': 'name'}]}
    claims = [{'w': 0, 't': t, 'read': 'Alice Bob', 'evidence': 'screen'} for t in ['Alice', 'Bob']]
    assert scout.printed_spellings(idx, {'fixes': claims})['words'] == idx['words']
    claims = [{'w': 0, 't': 'Carol', 'read': 'Alice Bob', 'evidence': 'screen'}]
    assert not scout.printed_spellings(idx, {'fixes': claims})['caption_evidence']


@pytest.mark.parametrize('speaks', [False, True])
def test_a_pacing_gap_never_manufactures_hard_crop_steps(speaks):
    ctx = SimpleNamespace(wf_any={0: 2, 1: 45, 2: 100} if speaks else {},
                          wfe_any={0: 40, 1: 95, 2: 119}, stress={0: 1, 1: 1, 2: 1})
    ref = {'asset': 'main', 'sync': True}
    assert pace.take_parts(ctx, None, 'b01.s0', ref, 120, 0, random.Random(1)) == [('b01.s0', ref, 120)]
    assert pace.talk_parts(ctx, 'b01.s0', ref, 120, 0, random.Random(1)) == [('b01.s0', ref, 120)]


def test_legacy_chain_moves_ease_instead_of_alternating_hard_punches(monkeypatch):
    import device_planner
    import first_cut_visual as fcv
    import schemas
    words = [{'w': 'word', 't0': float(i), 't1': i + .7} for i in range(20)]
    monkeypatch.setattr(device_planner, 'program_words', lambda *a: words)
    monkeypatch.setattr(schemas, 'program_duration', lambda *a: 20)
    ctx = SimpleNamespace(index={}, latest_edl=lambda: {'json': {}})
    calls = []
    def run(ctx, steps, name, args, started):
        calls.append((name, args)); return 'EDL v2'
    assert fcv.cut_ins(ctx, [], 0, run, [], (.5, .5), 1) > 0
    assert all(args['mode'] == 'ease' and args['ramp_s'] > 0 and args['strength'] <= .1 for _, args in calls)
