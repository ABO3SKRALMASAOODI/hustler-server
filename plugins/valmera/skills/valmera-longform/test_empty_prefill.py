"""A discovery verdict, an unavailable check and a verified substitute are distinct."""
import io
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest

import cinema as C
import footage_box as box


TRACE = """plan: 2 subjects, 8 searches
rank: 0 sources to open of 33 found
archive round: 0 sources would not open, 2 subjects had none; 0 archive sources to open
look: 0 frames checked, 0 pass
"""


def test_empty_rank_transfers_and_resumes(tmp_path, monkeypatch):
    """Execute the staged remote find/finish and local transfer with the C2 engine trace."""
    monkeypatch.delenv('CODEX_PROXY_LOCAL', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY','test')
    remote = tmp_path/'remote'
    remote.mkdir()
    staged = {}
    calls = []
    def ssh(command, **kw):
        if command.startswith('docker ps'):
            return SimpleNamespace(stdout='vbox-batch-test\n')
        if command.startswith('tar xf'):
            with tarfile.open(fileobj=io.BytesIO(kw['input'])) as package:
                for key in ('fetch.py','input.json'):
                    staged[key] = package.extractfile(key).read()
        elif '/runner.py ' in command:
            job = json.loads(staged['input.json'])
            calls.append(job)
            plan = SimpleNamespace(rank=lambda *a:([],0))
            def find(lines,*args,log,**kwargs):
                plan.rank(['paper','presentation'],{0:list(range(17)),1:list(range(16))})
                for line in TRACE.splitlines():
                    log(line)
                return dict(lines=[dict(r,clips=[]) for r in lines], sources=[], voices=[],credits=[],
                            counts=dict(sources_tried=0,sources_opened=0,frames_checked=0,frames_passed=0,clips=0))
            program = staged['fetch.py'].decode()
            exec(program[program.index('review_errors=[]'):],dict(j=job,json=json,pathlib=__import__('pathlib'),
                 sys=SimpleNamespace(argv=['fetch.py','input.json',str(remote)]),find=find,plan=plan,
                 print=lambda s,**kw2: kw['stdout'].write(s+'\n'),
                 run=SimpleNamespace(look=SimpleNamespace(check=lambda:None))))
        elif command.startswith('tar cf'):
            with tarfile.open(fileobj=kw['stdout'],mode='w') as tar:
                tar.add(remote/'footage.json',arcname='footage.json')
    monkeypatch.setattr(box,'ssh',ssh)
    out = tmp_path/'prefill-c2'
    job = dict(lines=[dict(i=17,say='The qualification.')],named=[],about='The chapter')
    first = box.fetch(job,out)
    assert box.completed_research(first) and not first['lines'][0]['clips']
    assert first['rank_rounds']==[dict(candidates=33,selected=0)]
    assert first['rank_trace']==dict(subjects=2,searches=8,selected=0,candidates=33,open_failures=0,
                                    uncovered=2,archive_selected=0,checked=0,reused=0,passed=0)
    assert box.fetch(job,out)==first and len(calls)==1
    box.fetch(dict(job,known_sources=[dict(url='different')]),out)
    assert len(calls)==2


def test_unavailable_check_uses_only_real_retained_evidence(tmp_path,monkeypatch):
    from hashlib import sha256
    asset = tmp_path/'source.png'
    from PIL import Image
    Image.new('RGB',(32,32),'white').save(asset)
    clip = dict(path=str(asset),source=dict(url='original',primary_context=[dict(quote='verified words')]),
                content_hash=sha256(asset.read_bytes()).hexdigest(),primary_passage=True,time_range=[0,7.8])
    line = dict(i=17,say='The qualification.',visual='A recorded walkthrough.')
    ft = dict(lines=[dict(line,clips=[clip])],spend=[],voices=[],credits=[])
    report = dict(lines=[dict(line,clips=[])],counts=dict(sources_tried=1,sources_opened=1,frames_checked=4),
                  research_errors=['review unavailable'],research_complete=False)
    assert not box.completed_research(report)
    ctx=SimpleNamespace(log=lambda s:None)
    job=dict(lines=[line])
    dest=tmp_path/'prefill-c2'
    def fail(*args):
        C.save(dest/'footage.json',report)
        raise box.PictureResearchUnavailable('review unavailable',report)
    monkeypatch.setattr(box,'fetch',fail)
    C.prefill_search(ctx,ft,job,dest)
    saved=C.read(dest/'coverage.json')
    assert saved['status']=='retained_after_failure' and saved['error']=='review unavailable'
    assert ft['lines'][0]['clips']==[clip]
    assert ft['prefill_coverage']['17']['retained'][0]['kind']=='primary_document'
    asset.write_bytes(b'changed')
    with pytest.raises(box.PictureResearchUnavailable,match='A recorded walkthrough'):
        C.prefill_search(ctx,ft,job,dest)


def test_legacy_log_resumes_with_current_verified_exhibit(tmp_path,monkeypatch):
    """An unbound historical log can justify the alternate only while its actual exhibit exists."""
    from PIL import Image
    dest=tmp_path/'prefill-c2'
    dest.mkdir()
    log=dest/'fetch.log'
    history=TRACE+'RuntimeError: Archive picture research incomplete; unavailable model checks cannot establish scarce footage: []\n'
    log.write_text(history)
    asset=tmp_path/'passage.png'
    Image.new('RGB',(24,24),'white').save(asset)
    clip=dict(path=str(asset),content_hash=C.digest(asset),primary_passage=True,time_range=[0,7.8],
              source=dict(url='paper#page=1',primary_context=[dict(quote='qualifying words')]))
    line=dict(i=17,say='The result.',visual='A walkthrough, if recorded.',dur=5)
    def aggregate():
        return dict(lines=[dict(line,clips=[clip])],spend=[],voices=[],credits=[])
    ctx=SimpleNamespace(log=lambda s:None)
    job=dict(lines=[line])
    calls=[]
    def search(payload,folder,_log):
        calls.append(folder)
        return dict(lines=[dict(line,clips=[])],sources=[],voices=[],counts=dict(clips=0))
    monkeypatch.setattr(box,'fetch',search)
    for _ in range(2):
        result=C.prefill_search(ctx,aggregate(),job,dest)
        assert not result['lines'] and not calls
    saved=C.read(dest/'coverage.json')
    assert saved['status']=='retained_after_legacy_search' and saved['rank_trace']['candidates']==33
    assert saved['lines']['17']['retained']==[dict(kind='primary_document',url='paper#page=1',time_range=[0,7.8])]
    C.prefill_search(ctx,aggregate(),dict(job,known_sources=[dict(url='new')]),dest)
    assert len(calls)==1 and calls[0]!=dest and log.read_text()==history
