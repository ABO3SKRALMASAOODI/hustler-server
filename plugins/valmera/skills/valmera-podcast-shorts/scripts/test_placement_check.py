import os,sys
sys.path.insert(0,os.path.dirname(__file__))
from placement_check import accepted, propose, words_under


def test_missing_duplicate_and_truthy_model_values_cannot_approve():
    rows={'placements':[{'id':0,'ok':True},{'id':1,'ok':'true'}, {'id':2,'ok':True},{'id':2,'ok':False}]}
    got=accepted(rows,[0,1,2,3])
    assert [got[i]['ok'] for i in range(4)] == [True,False,False,False]


def test_proposals_do_not_borrow_footage_from_another_sentence_or_repeat_it():
    class Clock:
        segs=[(0,3),(3,6),(6,9)]
    clip=dict(path='a.mp4',seconds=2,subject='a')
    script={'segments':[{'say':'a'},{'say':'unrelated'},{'say':'a again'}]}
    rows=propose(script,Clock(),{0:[clip],2:[clip]}, {},0,270,[])
    assert len(rows)==1 and rows[0][1]<=90
    assert abs(rows[0][4]['src'][1]-rows[0][4]['src'][0]-(rows[0][1]-rows[0][0])/30)<0.001


def test_words_use_overlap_with_displayed_interval():
    class Clock:
        words=[dict(w='Earth',t0=1,t1=1.3),dict(w='Sun',t0=1.4,t1=1.8)]
    assert words_under(Clock(),30,40)=='Earth'


def test_source_provenance_reaches_checker_and_invalidates_cached_decision(tmp_path,monkeypatch):
    import json
    import placement_check as pc
    path=tmp_path/'clip.mp4';path.write_bytes(b'fixture')
    monkeypatch.setattr(pc,'evidence',lambda *a:b'pixels')
    class Clock:
        words=[dict(w='Google',t0=0,t1=1)]
        segs=[(0,1)]
    calls=[]
    def call(system,content,**kwargs):
        row=json.loads(content[0]['text']);calls.append(row['source_evidence'])
        return json.dumps({'placements':[{'id':0,'ok':False,'reason':'test'}]}),{}
    rec=dict(path=str(path),src=[0,1],source=dict(url='https://example.org/film',channel='Original publisher'))
    args=([[0,30,'clip','Google',rec]],{'segments':[{'say':'Google'}]},Clock(),str(tmp_path),'unused')
    pc.check(*args,call=call);pc.check(*args,call=call)
    rec['source']['channel']='Different publisher'
    pc.check(*args,call=call)
    assert [x['channel'] for x in calls]==['Original publisher','Different publisher']


def test_composed_frame_evidence_preserves_phone_resolution_and_half_open_cut(tmp_path,monkeypatch):
    import io
    from PIL import Image
    import placement_check as pc
    from types import SimpleNamespace
    buffer=io.BytesIO();Image.new('RGB',(1080,1920),'red').save(buffer,format='JPEG')
    commands=[]
    def run(cmd,**kwargs):
        commands.append(cmd);return SimpleNamespace(stdout=buffer.getvalue())
    monkeypatch.setattr(pc.subprocess,'run',run)
    frames=pc.evidence([0,60,'clip','subject',dict(path='composed.mp4',src=[0,2],check_times=[0,1,59/30],composed_fps=30)],'ffmpeg')
    assert len(frames)==3
    assert all(Image.open(io.BytesIO(x)).size==(720,1280) for x in frames)
    assert float(commands[-1][commands[-1].index('-ss')+1])<59/30
    assert float(commands[0][commands[0].index('-ss')+1])==0


def test_caption_context_is_separate_from_display_claim_and_rechecked_on_change(tmp_path,monkeypatch):
    import json
    import placement_check as pc
    path=tmp_path/'film.mp4';path.write_bytes(b'fixture')
    monkeypatch.setattr(pc,'evidence',lambda *a:b'pixels')
    class Clock:
        words=[dict(w='the',t0=0,t1=.3),dict(w='Sun',t0=.3,t1=1)]
        segs=[(0,1)]
    calls=[]
    def call(system,content,**kwargs):
        calls.append(json.loads(content[0]['text']))
        return json.dumps({'placements':[dict(id=0,ok=False,reason='fixture review')]}),{}
    rec=dict(path=str(path),src=[.3,1],caption_words=[dict(f=[0,30],text='the')],display_words=[dict(f=[9,30],text='SUN')])
    args=([[9,30,'clip','Sun',rec]],dict(segments=[dict(say='the Sun')]),Clock(),str(tmp_path),'unused')
    pc.check(*args,call=call,review='transition');pc.check(*args,call=call,review='transition')
    rec['caption_words']=[dict(f=[0,30],text='Earth')]
    pc.check(*args,call=call,review='transition')
    assert len(calls)==2
    assert calls[0]['caption_words'][0]['text']=='the'
    assert calls[1]['caption_words'][0]['text']=='Earth'
    assert calls[0]['display_words']==calls[1]['display_words']


def test_rejected_frame_evidence_survives_replacing_the_render(tmp_path,monkeypatch):
    import json
    import placement_check as pc
    path=tmp_path/'film.mp4';path.write_bytes(b'old-render')
    monkeypatch.setattr(pc,'evidence',lambda *a:[b'exact-frame-one',b'exact-frame-two'])
    class Clock:
        words=[dict(w='Sun',t0=0,t1=1)]
        segs=[(0,1)]
    def call(*a,**kw):return json.dumps({'placements':[dict(id=0,ok=False,reason='wrong object')]}),{}
    _,report=pc.check([[0,30,'clip','Sun',dict(path=str(path),src=[0,1])]],dict(segments=[dict(say='Sun')]),Clock(),str(tmp_path),'unused',call=call)
    path.unlink()
    saved=tmp_path/'rejected-frames'/report['placements'][0]['key']
    assert (saved/'00.jpg').read_bytes()==b'exact-frame-one'
    assert (saved/'01.jpg').read_bytes()==b'exact-frame-two'
    assert json.loads((saved/'finding.json').read_text())['reason']=='wrong object'
