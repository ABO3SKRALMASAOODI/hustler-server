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
