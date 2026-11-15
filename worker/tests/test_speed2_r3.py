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


def test_crop_uses_nearby_saliency_not_a_later_face_or_empty_index_row():
    index={'_first_film_source_guard':True,
           'video':{'width':1280,'height':720,'duration':12},
           'shots':[{'start':0,'end':12}],
           'spatial':{'samples':[{'t':7,'faces':[]},
                                 {'t':10,'faces':[[.8,.2,.95,.5]]}]}}
    l1={'frame':[720,1280], 'items':[{'id':'quay','pic':'main','src':[6.5,7.5],
                                   'f':[0,30]}]}
    source_guard.apply(l1,index,{'samples':[{'t':7,'focus':[.52,.5]}]})
    crops=l1['items'][0]['crop']
    assert len(crops)==30
    for c in crops:
        assert (c[0]+c[2])/2==pytest.approx(.52)
        assert (c[2]-c[0])/(c[3]-c[1])==pytest.approx((9/16)/(16/9))


def test_master_rejects_unmeasurable_corrected_audio(monkeypatch,tmp_path):
    import mastering
    seq=iter([{'i':-18,'tp':-2},None])
    monkeypatch.setattr(mastering,'measure',lambda _:next(seq))
    monkeypatch.setattr(mastering.subprocess,'run',lambda *a,**k:None)
    monkeypatch.setattr(mastering.true_peak,'guard',lambda *a,**k:None)
    with pytest.raises(ValueError,match='could not be measured'):
        mastering.guard(str(tmp_path/'original.mp4'),str(tmp_path))


def test_master_leaves_compliant_audio_untouched(monkeypatch,tmp_path):
    import mastering
    monkeypatch.setattr(mastering,'measure',lambda _:{'i':-14.4,'tp':-1.5})
    def forbidden(*a,**k):raise AssertionError('unnecessary re-encode')
    monkeypatch.setattr(mastering.subprocess,'run',forbidden)
    assert mastering.guard('original',str(tmp_path))['passes']==0


def test_first_cut_fallback_uses_local_subject_reframe_before_render(monkeypatch):
    from types import SimpleNamespace
    import first_cut
    calls=[]
    def execute(ctx,name,args):
        calls.append((name,args));return 'REJECTED: local observations unavailable'
    monkeypatch.setattr(first_cut.agent_tools,'execute',execute)
    ctx=SimpleNamespace(versions_written=1,latest_edl=lambda:{'json':{}},index={})
    result=first_cut._render_cut(ctx,[],{'how':'finished_piece'})
    assert result.startswith('REJECTED')
    assert calls==[('auto_reframe',{'ratio':'9:16','mode':'crop'})]
