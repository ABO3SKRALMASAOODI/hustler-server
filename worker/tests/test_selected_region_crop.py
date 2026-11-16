import copy
import json

import pytest

from score import source_guard
from tests.test_named_detail_crop import scene


def selected_scene():
    film, index, _ = scene()
    film['items'][0].pop('view')
    film['items'][0]['source_region'] = [.70, .08, .9, .65]
    return film, index


def test_explicit_selected_region_survives_unrelated_desktop_saliency():
    film, index = selected_scene()
    before = copy.deepcopy(film)
    report = source_guard.apply(film, index, {})
    assert film == before
    assert report[0]['reason'] == 'selected_region_crop'


@pytest.mark.parametrize('fault', ['missing', 'full', 'outside', 'narrow', 'aspect', 'cut', 'face', 'invalid'])
def test_bad_selected_region_cannot_bypass_source_guard(fault):
    film, index = selected_scene()
    it = film['items'][0]
    if fault == 'missing': it.pop('source_region')
    elif fault == 'full': it['source_region'] = [0, 0, 1, 1]
    elif fault == 'outside': it['source_region'][2] = 1.1
    elif fault == 'narrow': it['source_region'][2] = .8
    elif fault == 'aspect': it['crop'][1] = [.72, .1, .89, .6]
    elif fault == 'cut': index['shots'] = [{'start': 0, 'end': 3.2}, {'start': 3.2, 'end': 8}]
    elif fault == 'face': index['spatial']['samples'][0]['faces'] = [[.85, .2, .95, .5]]
    elif fault == 'invalid': it['source_region'] = ['.7', .08, .9, .65]
    report = source_guard.apply(film, index, {})
    assert report[0]['reason'] != 'selected_region_crop'


def test_explicit_card_regions_survive_compilation_and_lowering(tmp_path):
    from score import compiler, lower
    from tests.test_score_taste import film as score
    _, index = selected_scene()
    index.update(words=[], sentences=[])
    index['video'].update(fps=30, has_audio=False)
    (tmp_path/'index.json').write_text(json.dumps(index))
    text = score([{'k': 'b', 'id': 'b01', 'at': '@3.0..@4.0',
                  'job': 'show the selected preview and stack', 'pic': 'main',
                  'do': [{'g': 'split', 'pic': [
                      {'src': 'main@3-4', 'crop': 'preview', 'cam': 'hold'},
                      {'src': 'main@3-4', 'crop': 'stack', 'cam': 'hold'}],
                      'at': 'beat', 'dir': 'v', 'gap': .025}]}])
    rows=[json.loads(x) for x in text.splitlines()]
    dna=next(x for x in rows if x['k']=='dna')
    dna['crops']={'preview': [.575,.105,.995,.53], 'stack': [.12,.555,.995,.925]}
    result=compiler.compile_score('\n'.join(json.dumps(x) for x in rows),index=index,
                                  work_dir=str(tmp_path),perception={},govern=False)
    assert not compiler.validate_l1(result.l1)
    cards=[x for x in result.l1['items'] if x['k']=='card' and x.get('pic')=='main']
    assert len(cards)==2
    report={x['id']:x for x in result.meter['source_guard']}
    for card in cards:
        assert report[card['id']]['reason']=='selected_region_crop'
        region=card['source_region'];c=card['crop']
        assert region[0]<=c[0]<c[2]<=region[2]
        assert region[1]<=c[1]<c[3]<=region[3]
    edl,_,_=lower.lower(result.l1,str(tmp_path))
    overlays={x['id']: x for x in edl['overlays']}
    for card in cards:
        assert overlays['cd.'+card['id']]['source_crop'] == card['crop']


def test_displaced_full_frame_selection_reaches_existing_repair_round(tmp_path):
    from score import compiler, direct
    from tests.test_score_taste import film as score
    from types import SimpleNamespace
    _, index = selected_scene()
    index.update(words=[], sentences=[])
    index['video'].update(fps=30, has_audio=False)
    (tmp_path/'index.json').write_text(json.dumps(index))
    text = score([{'k': 'b', 'id': 'b01', 'at': '@3.0..@4.0',
                  'job': 'show the preview',
                  'pic': {'src': 'main@3-4', 'crop': [.7,.08,.9,.65]}}])
    result=compiler.compile_score(text,index=index,work_dir=str(tmp_path),perception={})
    findings=[x for x in result.meter['findings'] if x.startswith('source region b01.s0:')]
    assert len(findings)==1
    assert 'local_saliency_crop' in findings[0]
    assert findings[0] in result.meter_text
    # Existing repair selection sees the geometric failure; no new call path.
    without=SimpleNamespace(meter={**result.meter,'findings':[
        x for x in result.meter['findings'] if x not in findings]})
    assert direct.quality(result,[])[1] == direct.quality(without,[])[1]+1
