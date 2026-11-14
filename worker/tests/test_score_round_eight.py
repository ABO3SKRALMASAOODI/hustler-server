"""Regressions from K7: wrong shelf identity, invisible cuts, masked sounds."""
import json
from pathlib import Path

from PIL import Image
from footage import verify, cache, seam
from score import compiler, sound
from tests.test_score_round_seven import shelf, _offer


def test_unverified_or_changed_clips_never_enter_shelf(tmp_path, shelf):
    offer = _offer(tmp_path, 'Oscar Piastri', 1)
    offer.pop('verification')
    assert cache.put('Oscar Piastri', [offer]) == 0
    offer = _offer(tmp_path, 'Oscar Piastri', 2)
    Path(offer['local_file']).write_bytes(b'another clip')
    assert cache.put('Oscar Piastri', [offer]) == 0


def test_person_requires_visible_attribution_and_every_frame(tmp_path, monkeypatch):
    video = tmp_path / 'clip.mp4'; video.write_bytes(b'clip')
    photo = tmp_path / 'frame.jpg'; Image.new('RGB', (40, 40)).save(photo)
    monkeypatch.setattr(verify.shots, 'frames', lambda *a: {0.15:str(photo), .75:str(photo), 1.35:str(photo)})
    seen = []
    result = dict(ok=True, frames_match=[True]*3, visible_subject='person')
    def check(*a, **k):
        seen.append(k)
        return json.dumps(result), .01
    monkeypatch.setattr(verify.ask, 'ask', check)
    rec, _ = verify.clip(str(video), dict(name='Oscar Piastri', kind='person'), 1.5)
    assert not rec['ok'] and seen[0]['model'] == 'gpt-6-sol'
    result.update(visible_attribution=['OSCAR PIASTRI']*3, frames_match=[True,False,True])
    rec, _ = verify.clip(str(video), dict(name='Oscar Piastri', kind='person'), 1.5)
    assert not rec['ok']


def test_legacy_shelf_is_empty_and_tampered_download_is_rejected(tmp_path, shelf):
    offer = _offer(tmp_path, 'Greenland shark', 1)
    assert cache.put('Greenland shark', [offer]) == 1
    got = cache.get('Greenland shark')[0]
    Path(shelf, got['cache_key']).write_bytes(b'tampered')
    import pytest
    with pytest.raises(ValueError, match='verification'):
        seam.acquire(got, str(tmp_path/'output.mp4'), 1080, 1920)
    assert not (tmp_path/'output.mp4').exists()


def test_small_reframe_is_not_a_cut_even_if_punch_flag_is_set():
    a=dict(id='a',k='shot',pic='main',f=[0,30],src=[0,1],crop=[0,0,1,1])
    b=dict(id='b',k='shot',pic='main',f=[30,60],src=[1,2],crop=[.02,.02,.98,.98],punch=1)
    assert compiler.distinct_cuts([a,b]) == []
    assert sound.l1_events(dict(fps=30,dur_f=60,items=[a,b])) == []
    b['crop']=[.15,.15,.85,.85]
    assert compiler.distinct_cuts([a,b]) == [30]


def test_short_film_can_sound_more_than_just_the_hook():
    assert sound.allowance('M', 3, 4.1, True)[0] >= 2
    assert sound.allowance('M', 3, 9.9, False)[0] >= 3


def test_print_collision_counts_occupied_area_once_across_sampled_frames():
    import pytest
    title = [0, 0, 1, 1]
    moving_label = [[.9, y, 1, y + .6] for y in (.1, .11, .12, .13)]
    assert compiler.on_print(title, moving_label) == pytest.approx(.063)
    assert compiler.on_print(title, moving_label + [[0, 0, .1, 1]]) == pytest.approx(.163)
    assert compiler.on_print(title, [[-1, -1, 2, 2]]) == 1


def test_first_film_cut_never_reopens_a_scanned_source(tmp_path, monkeypatch):
    import pytest
    from footage import cut, fetch
    proxy=tmp_path/'scan.mp4'; proxy.write_bytes(b'scanned')
    def network(*a, **k):
        raise AssertionError('first-film cut must not reopen the network')
    monkeypatch.setattr(cut, 'grab', network)
    monkeypatch.setattr(cut, 'local', lambda p,a,z,out:(out,'local'))
    src=dict(proxy=str(proxy),dur_read=30,url='https://example.test/video')
    assert cut.source(src,None,10,12,'out',whole=network,downloaded_only=True)[1]=='scanned_proxy'
    with pytest.raises(fetch.FetchError,match='outside'):
        cut.source(src,None,29,32,'out',whole=network,downloaded_only=True)


def test_supply_requires_an_explicit_word_match_and_uses_pair_numbers(tmp_path, monkeypatch):
    import time, types
    from score import supply
    monkeypatch.setattr(supply, 'pair_board', lambda *a, **k: 2)
    monkeypatch.setattr(supply.brief, 'image_block', lambda *a: {'type':'text','text':'board'})
    answer = {'pairs':[{'n':2,'post':'film','exact_match':True,'why':'exact object'},
                       {'n':1,'post':'film','why':'nice image but no literal evidence'}]}
    monkeypatch.setattr(supply.call, 'stream', lambda *a, **k: types.SimpleNamespace(text=json.dumps(answer),usd=0))
    items=[dict(id=i,wish=i,path='test.jpg',named='shark',what='meat') for i in range(2)]
    wishes=[dict(at=0,want='shark meat'),dict(at=1,want='shark')]
    kept,_,report=supply.check_fit(str(tmp_path),items,wishes,time.monotonic()+5)
    assert [x['id'] for x in kept] == [1]
    answer['pairs'][0]['exact_match']=False
    assert supply.check_fit(str(tmp_path),items,wishes,time.monotonic()+5)[0] == []


def test_late_footage_must_pass_the_words_before_it_can_be_acquired(tmp_path, monkeypatch):
    from score import film,supply
    (tmp_path/'bin.json').write_text('{"items":[]}')
    monkeypatch.setattr(supply,'check_fit',lambda *a,**k: ([],0.01,[{'ok':False,'why':'live shark is not shark meat'}]))
    called=[]
    box=dict(wishes=[dict(w='0-2',want='shark meat')],offers=[dict(kind='video',_wish=0,_img=Image.new('RGB',(20,20)),shows='a live shark')])
    assert film.footage_into(str(tmp_path),'',box,lambda c:called.append(c)) is None
    assert not called and box['word_check'][0]['ok'] is False


def test_word_check_keeps_the_named_subject_beyond_the_local_phrase(tmp_path, monkeypatch):
    import time, types
    from score import supply
    words='Greenland sharks mature slowly . Years later this animal is still growing'.split()
    (tmp_path/'index.json').write_text(json.dumps(dict(words=[dict(w=w,t0=i) for i,w in enumerate(words)])))
    monkeypatch.setattr(supply,'pair_board',lambda *a,**k:1)
    monkeypatch.setattr(supply.brief,'image_block',lambda *a:{'type':'text','text':'board'})
    seen=[]
    def check(model,system,content,**kw):
        seen.append(content[-1]['text'])
        return types.SimpleNamespace(text='{"pairs":[{"n":1,"post":"upload","exact_match":false,"why":"species unproven","remove":["b0.s1"]}]}',usd=0)
    monkeypatch.setattr(supply.call,'stream',check)
    items=[dict(id='generic',wish=0,path='shark.jpg',what='a shark')]
    assert supply.check_fit(str(tmp_path),items,[dict(w='7-8',want='this animal')],time.monotonic()+5)[0]==[]
    assert 'Greenland sharks mature slowly' in seen[0]
    assert 'this animal' in seen[0]


def test_picture_naming_scopes_context_to_the_requested_subject(tmp_path, monkeypatch):
    import broll_source
    from score import supply
    seen=[]
    def names(texts,context='',system=None):
        seen.append((texts,context,system))
        return [[]],0
    monkeypatch.setattr(broll_source,'name_many',names)
    wishes=[dict(at=1.8,want='fresh turmeric root',mine=False)]
    assert supply.name_wishes(str(tmp_path),wishes,'A branded container')[0]==[[]]
    assert seen[0][0]==['the editor wants: fresh turmeric root']
    assert seen[0][1]=='A branded container' and seen[0][2]==broll_source.NAME_SYSTEM+supply.WISH_NAME_SCOPE


def test_source_cut_measure_ignores_flash_and_finds_real_picture_change():
    import numpy as np
    from score import picture_cuts
    frames=np.zeros((60,32,32,3),np.uint8)
    frames[10]=255  # a flash returns to the same picture
    frames[30:]=[0,150,230]
    cuts,flashes=picture_cuts.boundaries(frames,15)
    assert cuts==[2.0] and flashes==[round(10/15,3)]


def test_cut_refinement_is_local_and_keeps_the_index_on_measurement_failure(monkeypatch):
    from score import picture_cuts
    index=dict(video=dict(duration=10),shots=[dict(id=1,start=0,end=10)])
    monkeypatch.setattr(picture_cuts,'measure',lambda *a:dict(cuts=[3,6,8],flashes=[],method='judge3_pixel_cuts'))
    local=picture_cuts.refine(index,'source.mp4')
    assert len(local['shots'])==4 and len(index['shots'])==1
    def unavailable(*a):raise TimeoutError()
    monkeypatch.setattr(picture_cuts,'measure',unavailable)
    assert picture_cuts.refine(index,'source.mp4')['shots']==index['shots']


def test_footage_retry_keeps_the_original_time_budget(monkeypatch):
    import types
    from footage import ask
    from score import call
    clock=[100.0];deadlines=[]
    monkeypatch.setattr(ask.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(ask.time,'sleep',lambda dt:clock.__setitem__(0,clock[0]+dt))
    def stream(*a,**k):
        deadlines.append(k['deadline'])
        if len(deadlines)==1:
            clock[0]+=10
            raise RuntimeError('transient')
        return types.SimpleNamespace(text='{}',usd=0)
    monkeypatch.setattr(call,'stream',stream)
    ask.ask('check',[], 'test',budget_s=35)
    assert deadlines==[135,135]



def test_constant_chapter_counter_survives_lowering_at_phone_readable_size(tmp_path):
    from tests.test_score_taste import comp, film
    from tests.test_score_compiler import wordless_index
    from score import lower
    c=comp(film([dict(k='b',id='b01',at='@0..@3',job='action',pic='main',do=[dict(g='counter',**{'from':2,'to':2},fmt='{n:02d} RINSE',size=.04,seat=[.5,.68],**{'for':'b01.s0'})])]),wordless_index(3))
    words=[w for w in c.l1['items'] if w.get('k')=='word' and 'counter' in w.get('id','')]
    assert words
    assert min(c.l1['voices'][w['v']]['px']/c.l1['frame'][1] for w in words)>=.054
    edl,_,_=lower.lower(c.l1,str(tmp_path))
    assert any('RINSE' in str(t.get('text',t.get('t',''))) for t in edl['texts'])


def test_first_film_on_a_cold_worker_reads_its_existing_footage_permission(monkeypatch):
    import threading,time
    monkeypatch.setattr(seam,'_CFG',dict(cfg=None,at=0,busy=False))
    monkeypatch.setattr(seam,'_READY',threading.Event())
    def read():
        with seam._LOCK:
            seam._CFG.update(cfg=dict(mode='off',test_user_ids=[42]),at=time.monotonic(),busy=False)
        seam._READY.set()
    monkeypatch.setattr(seam,'_read_cfg',read)
    cfg=seam.customer_cfg()
    assert seam.switch.allowed('customer',42,cfg)
    assert not seam.switch.allowed('customer',43,cfg)
