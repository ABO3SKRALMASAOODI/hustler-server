"""Recovery preserves exact source identity and the original measurement intervals."""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import wf1_coverage as coverage
import wf1_primary_sources as primary


def decision(updates):
    return SimpleNamespace(ask=lambda *a,**k:(json.dumps({'updates':updates}),{}),json_block=json.loads)


def story():
    return dict(words=[dict(w=str(i),start=i,end=i+.8) for i in range(8)],
                windows=[dict(name='A',first=1,last=2,t0=1,t1=2.8,say='1 2'),
                         dict(name='B',first=6,last=7,t0=6,t1=7.8,say='6 7')])


def test_context_extension_keeps_all_named_words_and_source_identity(tmp_path):
    data=story()
    changed,_=coverage.expand_context(data,[],str(tmp_path),decision([dict(i=0,first=0,last=4,why='The explanation continues')]))
    assert changed
    assert data['windows'][0]==dict(name='A',first=0,last=4,t0=0,t1=4.8,say='0 1 2 3 4')
    assert data['windows'][1]==story()['windows'][1]


@pytest.mark.parametrize('first,last',[(2,2),(1,1),(0,6)])
def test_context_rejects_shrinking_ruler_or_overlapping_subjects(tmp_path,first,last):
    data=story()
    with pytest.raises(RuntimeError):
        coverage.expand_context(data,[],str(tmp_path),decision([dict(i=0,first=first,last=last,why='Candidate')]))
    assert data['windows']==story()['windows']


@pytest.mark.parametrize('shared,valid',[(False,True),(True,True),(True,False),(False,False)])
def test_publisher_page_uses_transport_and_preserves_shared_original(tmp_path,monkeypatch,shared,valid):
    import writer,net_fetch
    from footage import fetch,shots
    corpus=tmp_path/'corpus.mp4';corpus.write_bytes(b'x'*3000)
    item=dict(window=0,kind='page',url='https://publisher.example/video',page_url='https://publisher.example/video',
              title='Original demonstration',publisher='Research lab')
    monkeypatch.setattr(writer,'ask_openai',lambda *a,**k:(json.dumps({'sources':[item]}),{}))
    monkeypatch.setattr(net_fetch,'check_url',lambda _:None)
    def transport(url,out):
        assert url==item['url']
        if shared:return str(corpus),'verified-local-source'
        path=os.path.join(out,'whole.mp4')
        with open(path,'wb') as f:f.write(b'y'*3000)
        return path,'external-original'
    monkeypatch.setattr(fetch,'whole_av',transport)
    monkeypatch.setattr(shots,'probe',lambda _:dict(dur=20 if valid else 700,w=1280,h=720))
    work=tmp_path/'sources'
    result=primary.acquire([dict(i=0,name='Demo')],str(work),SimpleNamespace(openai_key=lambda:None,json_block=json.loads))
    assert corpus.read_bytes()==b'x'*3000
    assert not list(work.glob('publisher-*'))
    if valid:
        assert result['sources']['Demo'][0]['url']==item['url']
        manifest=json.loads((work/'manifest.json').read_text())
        assert os.path.isfile(manifest[0]['path']) and manifest[0]['sha256']
    else:
        assert not result['sources'] and result['errors']
        assert not list(work.glob('*.mp4'))
