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


# 16 Nov cost lane: the frames per placement of every composed review of the three shorts the Jensen Huang run made
# (staging-1115/xCUala5j7aQ/evidence/mNN/render_reviews.json; 3 frames, plus 3 round each display word's edges), and
# the calls those reviews cost then (batches of 3 on the proxy, a second transition pass after a clean first one).
JENSEN_RENDER = {
    'm02': [[3, 3, 3, 3, 3, 3, 3, 6, 6, 6]],
    'm03': [[3, 6, 6, 3, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 6, 6, 3, 3, 3, 3, 3, 3],
            [3, 6, 6, 3, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 6, 6, 3, 3, 3, 3, 3, 3],
            [3, 6, 6, 3, 6, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 6, 6, 3, 6, 6, 3, 3, 3, 3, 3, 3]],
    'm04': [[3, 3, 3, 3, 3, 3, 3, 3, 6, 5, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3]],
}
JENSEN_RENDER_CALLS = {'m02': 8, 'm03': 10 + 18 + 18, 'm04': 18}


def _counting(monkeypatch, frames):
    """A fake model that approves every placement it is shown, counting calls and images; evidence gives each row its
    own frames (frames[i] of them, tagged by the row's name and the render it came from)."""
    import json
    import placement_check as pc
    monkeypatch.setattr(pc, 'evidence', lambda row, ffmpeg: [f"{row[4]['tag']}|{row[3]}|{k}".encode() for k in range(row[4]['n'])])
    calls = []

    def call(system, content, **kwargs):
        ids = [json.loads(c['text'])['id'] for c in content if c['type'] == 'text' and c['text'].startswith('{')]
        calls.append(dict(system=system, ids=ids, images=sum(c['type'] == 'image' for c in content)))
        return json.dumps({'placements': [dict(id=i, ok=True, reason='fits', motion_ok=True) for i in ids]}), {}
    return pc, calls, call


def _rows(path, frames, tag='render-1'):
    return [[i * 30, i * 30 + 30, 'clip', f'p{i}', dict(path=str(path), src=[i, i + 1], composed_fps=30, n=n, tag=tag)]
            for i, n in enumerate(frames)]


class _Clock:
    words = [dict(w='word', t0=0, t1=1)]
    segs = [(0, 1000)]


def test_recorded_composed_reviews_cost_under_a_third_of_their_calls(tmp_path, monkeypatch):
    """Every placement of every recorded review is still shown to the model with all its frames, in one composed
    pass, packed MAX_PLACEMENTS / MAX_IMAGES a call: 72 calls on the Jensen run's three made shorts become 21."""
    pc, calls, call = _counting(monkeypatch, None)
    film = tmp_path / 'film.mp4'
    film.write_bytes(b'render')
    got = {}
    for short, reviews in JENSEN_RENDER.items():
        before = len(calls)
        for n, frames in enumerate(reviews):
            # each recorded review was a fresh render: no frame of it is reused here
            _, report = pc.check(_rows(film, frames, tag=f'{short}-{n}'), dict(segments=[dict(say='word')]), _Clock(),
                                 str(tmp_path / short), 'unused', call=call, review='composed')
            assert report['accepted'] == len(frames)
        got[short] = len(calls) - before
    shown = [i for c in calls for i in c['ids']]
    assert len(shown) == sum(len(f) for r in JENSEN_RENDER.values() for f in r)        # nothing skipped
    assert all(c['images'] <= pc.MAX_IMAGES and len(c['ids']) <= pc.MAX_PLACEMENTS for c in calls)
    assert all(pc.COMPOSED in c['system'] for c in calls)
    assert got == {'m02': 2, 'm03': 15, 'm04': 4}
    assert sum(got.values()) * 3 <= sum(JENSEN_RENDER_CALLS.values())


def test_a_rerender_reuses_the_verdicts_of_unchanged_frames_and_checks_changed_ones(tmp_path, monkeypatch):
    pc, calls, call = _counting(monkeypatch, None)
    film = tmp_path / 'film.mp4'
    film.write_bytes(b'render one')
    frames = JENSEN_RENDER['m04'][0]
    args = (dict(segments=[dict(say='word')]), _Clock(), str(tmp_path / 'review'), 'unused')
    pc.check(_rows(film, frames), *args, call=call, review='composed')
    first = len(calls)
    film.write_bytes(b'render two, the same programme')            # a rebuild re-encodes the film
    pc.check(_rows(film, frames), *args, call=call, review='composed')
    assert len(calls) == first
    rows = _rows(film, frames)
    rows[4][4]['tag'] = 'render-2'                                  # one placement's pixels changed
    _, report = pc.check(rows, *args, call=call, review='composed')
    assert len(calls) == first + 1 and calls[-1]['ids'] == [4] and report['accepted'] == len(frames)


def test_batches_keep_each_placement_whole_and_in_order():
    import placement_check as pc
    items = [(dict(id=i), None, [b'x'] * n) for i, n in enumerate([3, 30, 3, 3, 3, 3, 3, 3, 3, 3, 3])]
    got = [[e['id'] for e, _, _ in b] for b in pc.batches(items)]
    assert got == [[0], [1], [2, 3, 4, 5, 6, 7, 8, 9], [10]]
