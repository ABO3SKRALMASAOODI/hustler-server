import numpy as np
import pytest
from score import compiler, source_guard
import render_qc


def test_literal_counter_labels_are_not_numbered_twice():
    for label, n in [('01 stretch', 1), ('02 wash', 2), ('03 brush', 3)]:
        assert compiler.counter_text(label, n, True) == label
    assert compiler.counter_text('{n:02d} stretch', 1, True) == '01 stretch'
    assert compiler.counter_text('chapter', 3, True) == 'chapter'


def test_dark_caption_on_black_is_reported_even_without_motion_graphics(monkeypatch):
    edl = {'captions': [{'start': 6.9, 'end': 7.7, 'text': 'reputation.',
        'style': {'color': '#111111', 'anchor_y': .72}}]}
    cue = render_qc.reading_plan(edl)[0]
    contrast = render_qc.reading_contrast(np.zeros((284, 160), dtype=np.uint8), cue)
    assert contrast < 1.2
    found = render_qc.findings({'reading_contrast': [dict(cue, contrast=contrast)],
        'letterbox': {'share': .79}}, {'W':720, 'H':1280, 'fps':30})
    assert any('UNREADABLE CAPTION' in x for x in found)
    assert any('LETTERBOX' in x for x in found)
    cue['inks'] = ['#FFFFFF']
    assert render_qc.reading_contrast(np.zeros((284,160), dtype=np.uint8), cue) > 20


def test_safety_crop_waits_if_no_local_or_prior_signal():
    l1={'frame':[720,1280], 'items':[{'id':'shot','pic':'main','src':[0,1]}]}
    index={'_first_film_source_guard':True,'video':{'width':1920,'height':1080,'duration':1}}
    with pytest.raises(ValueError, match='awaits local'):
        source_guard.apply(l1,index,{})


def test_floor_comparison_is_private_until_it_wins(monkeypatch):
    from types import SimpleNamespace
    import first_cut_floor as floor
    import first_cut
    row={'version':2,'json':{'keep':[[0,10]],'texts':[{'text':'crafted'}]}}
    ctx=SimpleNamespace(latest_edl=lambda:row, director_version=2, project_id=9,
                        last_preview={'render_asset_id':44,'edl_version':2})
    def apply(edl, note):
        row.update(version=row['version']+1,json=edl);return 'EDL v'+str(row['version'])
    ctx.apply_score=apply
    reads=iter([{'class':'light_touch','program_s':10}, {'class':'trim_only','program_s':10}])
    monkeypatch.setattr(floor,'measure',lambda *a:next(reads))
    monkeypatch.setattr(first_cut,'_render_cut',lambda *a:'restored')
    def build():
        assert ctx.first_film_comparison is True
        return [],[],{},'private'
    result=floor.choose(ctx,None,0,{'json':{'keep':[[0,10]]}},[],[],{'how':'director'},'visible',build)
    assert ctx.first_film_comparison is False
    assert row['json']['texts'][0]['text']=='crafted'
    assert result[-1]=='restored'


def test_even_a_better_trim_only_floor_is_not_publishable():
    from first_cut_floor import prefer_floor
    assert not prefer_floor({'class':'same_upload'}, {'class':'trim_only'})


def test_caption_contrast_uses_the_rendered_clock_after_a_cut():
    from timeline import Timeline
    edl={'captions':[{'start':10,'end':11,'text':'still','style':{'color':'#111111'}}]}
    cue=render_qc.reading_plan(edl,Timeline([[9,12]]))[0]
    assert cue['time']==pytest.approx(1.6)
