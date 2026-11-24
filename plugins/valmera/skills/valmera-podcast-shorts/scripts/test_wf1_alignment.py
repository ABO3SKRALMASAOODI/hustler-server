import copy
import json
from types import SimpleNamespace
import pytest
import wf1_alignment as A


def index():
    words=[dict(w=w,t0=a,t1=b) for w,a,b in [('sense',0,.2),('The',.3,.5),('mote',.6,.9),('last.',9.5,9.8)]]
    return dict(video=dict(duration=10),words=words,sentences=[dict(id='s1',wi0=0,wi1=3,t0=0,t1=9.8,text='sense The mote last.')],shots=[])


def test_hook_trim_and_spelling_keep_measured_interior_timing():
    original=index();before=copy.deepcopy(original)
    out,(a,b)=A.transform(original,dict(first=1,last=3,corrections=[dict(id=2,text='moat')]))
    assert original==before
    assert (a,b)==pytest.approx((.24,9.92))
    assert [w['w'] for w in out['words']]==['The','moat','last.']
    assert out['words'][1]['t0']==pytest.approx(.6-a)
    assert out['sentences'][0]['text']=='The moat last.'
    assert out['sentences'][0]['wi1']==2


@pytest.mark.parametrize('decision',[
    dict(first=3,last=3),dict(first=0,last=1),
    dict(first=0,last=3,corrections=[dict(id=1,text='a new claim')]),
    dict(first=1,last=3,corrections=[dict(id=0,text='hidden')])])
def test_alignment_cannot_rewrite_or_remove_the_interior(decision):
    with pytest.raises(ValueError):A.transform(index(),decision)


def test_reconcile_recuts_product_source_and_reuses_bound_result(tmp_path):
    src=tmp_path/'source.mp4';src.write_bytes(b'old source')
    (tmp_path/'coverage.json').write_text('{}')
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path))
    script=dict(source='original.mp4',window=[100,110],hook_line='The moat',close_line='last.')
    calls=[]
    def ask(*args,**kwargs):
        calls.append(1)
        return json.dumps(dict(first=1,last=3,corrections=[dict(id=2,text='moat')])),dict(usd=0)
    def cut(ctx,source,a,b,out):
        assert source=='original.mp4' and (a,b)==pytest.approx((100.24,110.8))
        from pathlib import Path
        Path(out).write_bytes(b'aligned source')
    llm=SimpleNamespace(ask=ask,json_block=json.loads)
    chain=SimpleNamespace(cut=cut,brand=SimpleNamespace(probe=lambda _:dict(dur=9.68)),shots_of=lambda *a:[])
    out,changed=A.reconcile(ctx,script,index(),[],llm,chain)
    assert changed and script['window']==pytest.approx([100.24,110.8])
    assert (tmp_path/'coverage.json.before-alignment').exists()
    assert not (tmp_path/'coverage.json').exists()
    again,changed=A.reconcile(ctx,script,out,[],llm,chain)
    assert not changed and again==out and len(calls)==1


def test_out_point_keeps_a_breath_after_the_last_word():
    """19 Nov: the card dissolved over the payoff word in all 12 films; the clip now holds a breath after it."""
    end,fade=A.out_point(index(),dict(first=1,last=3),[],100)
    assert end==pytest.approx(100+9.8+A.BREATH_S) and fade is None


def test_out_point_fades_out_before_a_word_the_clip_does_not_keep():
    idx=index();idx['words'].append(dict(w='And',t0=10.1,t1=10.3))
    end,fade=A.out_point(idx,dict(first=1,last=3),[],100)
    assert end==pytest.approx(110.8)
    assert 109.8<fade<110.1-A.FADE_S+1e-6
    # past the heard window, the source captions say where the next word starts
    end,fade=A.out_point(index(),dict(first=1,last=3),[(109.0,'last.'),(110.3,'Next')],100)
    assert end==pytest.approx(110.8) and 109.8<fade<110.3-A.FADE_S+1e-6


def test_reconcile_passes_the_fade_to_the_cut(tmp_path):
    src=tmp_path/'source.mp4';src.write_bytes(b'old source')
    ctx=SimpleNamespace(supply=str(tmp_path),run_dir=str(tmp_path))
    script=dict(source='original.mp4',window=[100,110],hook_line='The moat',close_line='last.')
    seen={}
    def ask(*args,**kwargs):
        return json.dumps(dict(first=1,last=3)),dict(usd=0)
    def cut(ctx,source,a,b,out,fade_at=None):
        seen.update(a=a,b=b,fade_at=fade_at)
        from pathlib import Path
        Path(out).write_bytes(b'aligned source')
    llm=SimpleNamespace(ask=ask,json_block=json.loads)
    chain=SimpleNamespace(cut=cut,brand=SimpleNamespace(probe=lambda _:dict(dur=10.56)),shots_of=lambda *a:[])
    A.reconcile(ctx,script,index(),[(109.0,'last.'),(110.2,'And')],llm,chain)
    assert seen['b']==pytest.approx(110.8)
    assert seen['fade_at'] is not None and 0<seen['fade_at']<110.2-100.24


def test_run_tokens_join_a_split_token_on_the_run_transcripts_boundary():
    """Round1 default-design: the window heard '49' '%' and 'self' '-critical.'; the run transcript has '49%' and
    'self-critical.'. The caption tokens become the run's, timed from the words they cover; nothing else moves."""
    words=[dict(w=w,t0=a,t1=b) for w,a,b in [('And',0.0,.2),('49',.3,.5),('%',.5,.7),('are',.8,.9),
                                              ('very',1.0,1.2),('self',1.3,1.5),('-critical.',1.5,1.9),('in',2.0,2.1),('to',2.1,2.2)]]
    idx=dict(video=dict(duration=3),words=words,sentences=[dict(id='s1',wi0=0,wi1=4,t0=0,t1=1.2,text='x'),
                                                           dict(id='s2',wi0=5,wi1=8,t0=1.3,t1=2.2,text='y')])
    run=[(100.0,'And'),(100.32,'49%'),(100.8,'are'),(101.0,'very'),(101.35,'self-critical.'),(102.0,'in'),(102.1,'to')]
    out,n=A.run_tokens(idx,run,100.0)
    assert n==2
    assert [w['w'] for w in out['words']]==['And','49%','are','very','self-critical.','in','to']
    assert out['words'][1]['t0']==.3 and out['words'][1]['t1']==.7
    assert out['words'][4]['t0']==1.3 and out['words'][4]['t1']==1.9
    assert [(s['wi0'],s['wi1']) for s in out['sentences']]==[(0,3),(4,6)]
    assert out['sentences'][1]['text']=='self-critical. in to'
    assert idx['words'][1]['w']=='49'                       # the input is not touched
    # words the run transcript keeps apart stay apart; a token far in time is not this one
    same,n0=A.run_tokens(idx,[(100.32,'49'),(100.5,'%'),(105.0,'self-critical.')],100.0)
    assert n0==0 and same is idx
    # a token split across two sentences ('self' ends one, '-critical.' opens the next) is captioned once, in the first
    words2=[dict(w=w,t0=a,t1=b) for w,a,b in [('very',0.0,.2),('self',.3,.5),('-critical.',.5,.9),('Then',1.0,1.2)]]
    idx2=dict(video=dict(duration=2),words=words2,sentences=[dict(id='s1',wi0=0,wi1=1,t0=0,t1=.5,text='very self'),
                                                             dict(id='s2',wi0=2,wi1=3,t0=.5,t1=1.2,text='-critical. Then')])
    out2,n2=A.run_tokens(idx2,[(100.0,'very'),(100.3,'self-critical.'),(101.0,'Then')],100.0)
    assert n2==1 and [(s['wi0'],s['wi1'],s['text']) for s in out2['sentences']]==[(0,1,'very self-critical.'),(2,2,'Then')]
    assert out2['sentences'][0]['t1']==.9 and out2['sentences'][1]['t0']==1.0
    flat=[out2['words'][q]['w'] for s in out2['sentences'] for q in range(s['wi0'],s['wi1']+1)]
    assert flat==['very','self-critical.','Then']                              # each word once (supply's words.json)
    # a sentence that was only the tail of the token is folded into the one before it
    idx3=dict(video=dict(duration=2),words=words2,sentences=[dict(id='s1',wi0=0,wi1=1,t0=0,t1=.5,text='a'),dict(id='s2',wi0=2,wi1=2,t0=.5,t1=.9,text='b'),
                                                             dict(id='s3',wi0=3,wi1=3,t0=1.0,t1=1.2,text='c')])
    out3,_=A.run_tokens(idx3,[(100.0,'very'),(100.3,'self-critical.'),(101.0,'Then')],100.0)
    assert [(s['id'],s['wi0'],s['wi1']) for s in out3['sentences']]==[('s1',0,1),('s3',2,2)] and out3['sentences'][0]['t1']==.9


def test_m04_verified_term_reaches_the_visible_caption_on_its_original_frames(tmp_path):
    from wf1_film import F

    tokens = 'If it can solve Navy or Stokes, very difficult math problems, maybe'.split()
    # The staged film's caption placements ink Navy/or/Stokes at frames 29/39/43.
    starts = [0, .06, .14, .28, .90, 1.12, 1.26, 1.76, 2.22, 2.54, 2.76, 9.]
    ends = [.06, .14, .28, .90, 1.12, 1.26, 1.60, 2.22, 2.54, 2.76, 3.10, 9.5]
    words = [dict(w=w, t0=a, t1=b) for w, a, b in zip(tokens, starts, ends)]
    idx = dict(video=dict(duration=10.5), words=words,
               sentences=[dict(wi0=0, wi1=6), dict(wi0=7, wi1=11)], shots=[])
    script = dict(source='interview', window=[824.19, 834.69],
                  hook_line='If it can solve Navier-Stokes very difficult math problems,',
                  close_line='maybe', names=['Navier–Stokes equations', 'Terence Tao'])
    decision = dict(first=0, last=11, terms=[dict(first=4, last=6, text='Navier-Stokes'),
                                             dict(first=11, last=11, text='definitely')])
    src = tmp_path/'source.mp4'
    src.write_bytes(b'original spoken interview')
    llm = SimpleNamespace(ask=lambda *a, **kw: (json.dumps(decision), {}), json_block=json.loads)
    chain = SimpleNamespace(brand=SimpleNamespace(probe=lambda _:dict(dur=10.5)), shots_of=lambda *a:[])
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path))
    out, _ = A.reconcile(ctx, script, idx, [(825.099,'Navier-Stokes')], llm, chain)

    expected = tokens[:4] + ['Navier-Stokes,'] + tokens[7:]
    assert [w['w'] for w in out['words']] == expected
    assert out['words'][4] == dict(w='Navier-Stokes,', t0=.9, t1=1.6)
    assert out['words'][5:] == words[7:]
    assert [(s['wi0'], s['wi1']) for s in out['sentences']] == [(0,4),(5,9)]
    segments = [dict(say=s['text']) for s in out['sentences']]
    clock = F.Clock(dict(duration=10.5, words=A.caption_words(out)), dict(segments=segments))
    plan = F.Plan({'cap':dict(font='InterDisplay-Bold.ttf',px=58,track=0)}, {})
    F.caption_build(plan, clock, 'cap', .65)
    caps = [it for it in plan.items if it['k']=='word']
    assert [it['t'] for it in caps] == expected
    term = caps[4]
    assert term['f'][0] == 29 and term['f'][1] > 45


def test_spelling_across_sentence_break_and_fused_name_preserve_heard_edges():
    words = [dict(w=w,t0=a,t1=b) for w,a,b in [
        ('dot',0,.16), ('net',.16,.31), ('and',.40,.52), ('TerryTao,',.60,1.), ('maybe',8.8,9.)]]
    idx = dict(video=dict(duration=10),words=words,sentences=[dict(wi0=0,wi1=0),dict(wi0=1,wi1=4)])
    decision = dict(first=0,last=4,terms=[dict(first=0,last=1,text='.NET'),dict(first=3,last=3,text='Terry Tao')])
    facts = dict(names=['.NET','Terence Tao'],hook='',close='',source_captions=[(100.6,'Terry'),(100.84,'Tao')])
    spans = A.term_spellings(decision,facts,words,100.)
    out,_ = A.transform(idx,decision,spans,keep_window=True)
    assert [(w['w'],w['t0'],w['t1']) for w in out['words']] == [
        ('.NET',0,.31), ('and',.40,.52), ('Terry',.60,.80), ('Tao,',.80,1.), ('maybe',8.8,9.)]
    assert [s['text'] for s in out['sentences']] == ['.NET','and Terry Tao, maybe']


def replay_opening(tmp_path, idx, script, caps, answer, old_error):
    """A legacy failed decision beside the original source. Only the new aligner can answer."""
    import hashlib
    from pathlib import Path

    src = tmp_path/'source.mp4'
    src.write_bytes(b'old take')
    script.update(source='episode.mp4')
    content = dict(names=[], hook=script['hook_line'], close=script['close_line'],
                   source_captions=caps, window_start=script['window'][0],
                   timed_hearing=[dict(id=i, **w) for i,w in enumerate(idx['words'])])
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    key = hashlib.sha256(json.dumps([4,digest,content],sort_keys=True).encode()).hexdigest()[:16]
    (tmp_path/f'alignment-{key}.json').write_text(json.dumps(dict(decision=dict(error=old_error),spend={})))
    cuts = []
    def cut(ctx, source, start, end, out, **kw):
        assert source == 'episode.mp4'
        cuts.append((start, end))
        Path(out).write_bytes(b'new take')
    def ask(system, evidence, **kw):
        reply = answer(json.loads(evidence)) if callable(answer) else answer
        return json.dumps(reply), {}
    llm = SimpleNamespace(ask=ask, json_block=json.loads)
    chain = SimpleNamespace(cut=cut, brand=SimpleNamespace(probe=lambda _: dict(dur=cuts[0][1]-cuts[0][0])),
                            shots_of=lambda *a: [])
    ctx = SimpleNamespace(supply=str(tmp_path), run_dir=str(tmp_path))
    out, changed = A.reconcile(ctx, script, idx, caps, llm, chain)
    assert changed and len(cuts) == 1
    assert json.loads((tmp_path/'script.json').read_text()) == script
    # An interrupted script save is restored from the digest-bound aligned index.
    again = dict(script, window=[0,0],
                 hook_line='previous hook')
    assert A.reconcile(ctx,again,out,caps,None,None) == (out,False)
    assert again == script
    return out, cuts[0]


def test_m17_restart_uses_second_we_and_preserves_every_remaining_heard_edge(tmp_path):
    from pathlib import Path

    f = json.loads((Path(__file__).parent/'fixtures/m17-opening.json').read_text())
    idx = dict(words=[], sentences=[], video=dict(duration=f['duration']), shots=[])
    for line in f['hearing']:
        first = len(idx['words'])
        idx['words'].extend(dict(w=text, t0=a, t1=b) for a,b,text in line)
        idx['sentences'].append(dict(wi0=first, wi1=len(idx['words'])-1))
    script = dict(window=f['window'], hook_line=f['hook'], close_line=f['close'])
    caps = f['source_captions']
    start = caps.index([2442.117, '>> We'])
    stop = caps.index([2447.417, 'provide.'])
    # A blended ASR onset, and an aligner that names the start rather than the continuation as first.
    answer = dict(source_hook=[start, stop], heard_start=0, first=0, last=102)
    out, (cut_in,cut_out) = replay_opening(tmp_path, idx, script, caps, answer, f['error'])
    assert (cut_in,cut_out) == pytest.approx((2442.657,2473.68))
    assert script['hook_line'] == f['hook'].split('I mean, ')[1]
    captions = A.caption_words(out)
    hook = [w for w in captions if w['seg'] == 0]
    assert ' '.join(w['w'] for w in hook) == script['hook_line']
    assert [cut_in+w['start'] for w in hook] == pytest.approx([t for t,w in caps[start+4:stop+1]], abs=.0001)
    rest = captions[len(hook):]
    # The existing display pass joins 70/-80%, and 1/.7 on the episode caption's
    # spelling. Every retained ASR ID contributes to exactly one display span.
    i = 17
    for w in rest:
        start = i
        text = idx['words'][i]['w']
        while text != w['w']:
            assert w['w'].startswith(text)
            i += 1
            text += idx['words'][i]['w']
        assert (cut_in+w['start'],cut_in+w['end']) == pytest.approx(
            (f['window'][0]+idx['words'][start]['t0'],f['window'][0]+idx['words'][i]['t1']), abs=.0001)
        i += 1
    assert i == 103
    assert script['close_line'] == f['close']


def test_restore_meaningful_lead_before_old_window_without_moving_the_close(tmp_path):
    idx = index()
    idx['video']['duration'] = 10.8
    script = dict(window=[100,110.8], hook_line='Only if rates fall.', close_line='last.')
    # Two occurrences. The local one supplies a true qualification absent from the cut.
    caps = [(80.,'Only'),(80.3,'if'),(80.6,'rates'),(81.,'fall.'),
            (96.5,'>> Only'),(97.,'if'),(98.,'rates'),(99.,'fall.'),(100.,'sense')]
    out, edges = replay_opening(tmp_path,idx,script,caps,dict(source_hook=[4,7], first=0,last=3),'missing premise')
    assert edges == pytest.approx((96.44,110.8))
    assert script['hook_line'] == out['sentences'][0]['text'] == 'Only if rates fall.'
    assert [w['w'] for w in out['words'][4:]] == [w['w'] for w in idx['words']]
    assert [w['t0']+edges[0] for w in out['words'][4:]] == pytest.approx([100+w['t0'] for w in idx['words']])
    assert out['words'][-1]['t1']+edges[0] == pytest.approx(109.8)


def test_hearing_alone_reauthors_only_the_opening(tmp_path):
    idx = index()
    script = dict(window=[100,110], hook_line='Well I mean The moat', close_line='last.')
    calls = []
    def reply(evidence):
        calls.append(evidence)
        if 'repair' not in evidence:
            return dict(error='untimed filler')
        return dict(first=1,hook_last=2,last=3,corrections=[dict(id=2,text='moat')])
    out, _ = replay_opening(tmp_path,idx,script,[],reply,'untimed filler')
    assert len(calls) == 2 and calls[-1]['repair']['mismatch'] == 'untimed filler'
    assert script['hook_line'] == 'The moat'
    assert [w['w'] for w in A.caption_words(out)] == ['The','moat','last.']


def test_heard_compound_uses_verified_spelling_across_source_capitalization():
    words = [dict(w=w,t0=i/5,t1=(i+1)/5) for i,w in enumerate(["pay","per","click","maximizer."])]
    content = dict(hook="", close="", names=["Paperclip maximizer"], source_captions=[(0.,"Paper Clip Maximizer.")])
    decision = dict(first=0,last=3,terms=[dict(first=0,last=3,text="paperclip maximizer")])
    assert A.term_spellings(decision, content, words, 0) == {0:(3,["paperclip","maximizer."])}
