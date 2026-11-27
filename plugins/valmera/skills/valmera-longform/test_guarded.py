"""Fix 63 (26 Nov): a slow or broken macOS footprint read must never end a healthy narration.

Live run 11260010-2ab068 stopped after 1h54m and 338 saved takes because one footprint
read took over 15 s: the TimeoutExpired unwound the guard, whose finally stopped the
narrator's owned process group. These tests run the real guard over an owned stand-in
child that writes checkpointed takes through the shared reader's own take loop
(valmera-stories/voice.Read.generate_phonon, model replaced by a tiny tone), from an
exact copy of that run's saved _generations.json, with a scripted footprint on PATH.
"""
import hashlib,json,os,sys,textwrap,time
from pathlib import Path

import pytest

import guarded as G

HERE=Path(__file__).resolve().parent
DESCRIPTOR=HERE/'testdata'/'fix63_generations_11260010-2ab068.json'
TIME=['/usr/bin/time','-l'] if sys.platform=='darwin' and Path('/usr/bin/time').exists() else []

FAKE_FOOTPRINT='''#!{py}
import json,os,sys,time
from pathlib import Path
d=Path(os.environ['FIX63_FP'])
n=int((d/'count').read_text()) if (d/'count').exists() else 0
(d/'count').write_text(str(n+1))
plan=(d/'plan').read_text().split(',')
step=plan[n] if n<len(plan) else plan[-1]
args=sys.argv[1:];state=Path(args[args.index('-j')+1]);pids=[int(a) for a in args[args.index('-j')+2:]]
proc=lambda pid,b:dict(pid=pid,footprint=b//2,auxiliary=dict(phys_footprint_peak=b))
if step=='hang':time.sleep(30)
elif step=='truncated':state.write_text('{{"processes":[{{"pid":'+str(pids[0]))
elif step=='exit':print('footprint: no process with pid',pids[-1],file=sys.stderr);sys.exit(1)
elif step=='foreign':state.write_text(json.dumps(dict(processes=[proc(1,9_000_000_000)])))
elif step.startswith('ok'):state.write_text(json.dumps(dict(processes=[proc(p,int(step[2:] or 4096)) for p in pids])))
'''

STAND_IN='''
import json,os,sys,time,types,wave
from pathlib import Path
import numpy as np
def write(path,x,sr,format=None,subtype=None):
    with wave.open(str(path),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(sr);w.writeframes((np.clip(x,-1,1)*32767).astype('<i2').tobytes())
sys.modules['soundfile']=types.SimpleNamespace(write=write)
sys.path.insert(0,{stories!r})
import voice
desc,out,lines,mode=sys.argv[1],Path(sys.argv[2]),int(sys.argv[3]),sys.argv[4]
Path(out/'pid').write_text(str(os.getpid()))
class TTS:
    sample_rate=8000
    def set_conditions(self,c):pass
    def synth(self,text,**kw):
        time.sleep(0.1);return np.sin(np.arange(800)*(len(text)%13+1)/50.0)*0.2
LN=types.SimpleNamespace(TAKES=2,retake_dd=lambda k,b:0,phonon_tts=TTS)
rd=voice.Read(None,LN,None,None)
segs=json.loads(Path(desc).read_text())['segments'][:lines]
job=dict(voice='easton',takes=2,lines=[dict(id=f's{{i:02d}}',text=s['text']) for i,s in enumerate(segs)])
takes=out/'_takes';takes.mkdir(parents=True,exist_ok=True)
rd.generate_phonon(job,takes,print)
fp=Path(os.environ['FIX63_FP'])
until=time.time()+20
while mode=='wait' and time.time()<until and int((fp/'count').read_text() if (fp/'count').exists() else 0)<int((fp/'need').read_text()):
    time.sleep(0.05)
if mode=='long':time.sleep(60)
(out/'done.json').write_text(json.dumps(dict(generated=rd.generated_takes)))
if mode=='fail':
    raise ValueError('segment 7 has no words')
'''


@pytest.fixture
def rig(tmp_path,monkeypatch):
    root=tmp_path/'root';(root/'.valmera/week-1106').mkdir(parents=True)
    (root/'.valmera/week-1106/heavy.sh').write_text('shift\nexec "$@"\n')
    fp=tmp_path/'fp';fp.mkdir()
    bin_=tmp_path/'bin';bin_.mkdir()
    (bin_/'footprint').write_text(FAKE_FOOTPRINT.format(py=sys.executable));(bin_/'footprint').chmod(0o755)
    child=tmp_path/'stand_in.py';child.write_text(STAND_IN.format(stories=str(HERE.parent/'valmera-stories')))
    monkeypatch.setenv('PATH',f"{bin_}:{os.environ.get('PATH','/usr/bin:/bin')}")
    monkeypatch.setenv('FIX63_FP',str(fp))
    monkeypatch.delenv('CODEX_PROXY_LOCAL',raising=False)
    monkeypatch.setattr(G,'ROOT',root)
    monkeypatch.setattr(G,'TIME',TIME,raising=False)
    monkeypatch.setattr(G,'PROBE_TIMEOUT',0.5,raising=False)
    monkeypatch.setattr(G,'INTERVAL',0.05,raising=False)
    monkeypatch.setattr(G,'MAX_INTERVAL',0.2,raising=False)
    work=tmp_path/'narration';work.mkdir()
    def go(plan,need=0,lines=3,mode='wait',**kw):
        (fp/'plan').write_text(plan);(fp/'need').write_text(str(need));(fp/'count').unlink(missing_ok=True)
        (work/'done.json').unlink(missing_ok=True)
        out=tmp_path/f'memory-{time.time_ns()}'
        t0=time.time()
        code=G.run([sys.executable,str(child),str(DESCRIPTOR),str(work),str(lines),mode],out,'fix63-test',**kw)
        return code,json.loads((out/'memory.json').read_text()),time.time()-t0,out
    return go,work


def digests(work):
    return {f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted((work/'_takes').glob('*.wav'))}


def test_footprint_miss_keeps_healthy_narrator_and_its_takes(rig,capsys):
    go,work=rig
    plan='truncated,hang,exit,foreign,ok3000,ok226116232'
    code,memory,elapsed,out=go(plan,need=6)
    assert code==0 and memory['exit_code']==0 and memory['stopped'] is None
    assert json.loads((work/'done.json').read_text())['generated']==6
    assert len(digests(work))==6 and all(G_whole(f) for f in (work/'_takes').glob('*.wav'))
    probes=memory['probes']
    assert probes['ok']>=1 and probes['missed']>=3
    reasons=' | '.join(probes['reasons'])
    assert 'timed out' in reasons and 'unreadable' in reasons and 'no owned process' in reasons
    # The foreign 9 GB row was discarded; the verified owned peak is kept.
    assert memory['peak_process_footprint_bytes']==226116232
    assert not (out/'tmp'/'footprint.json').exists()
    assert 'footprint probe missed' in capsys.readouterr().out
    if TIME:assert memory.get('time_max_rss_bytes',0)>0
    assert elapsed<20

    # A watcher retry reuses every whole take byte for byte and remakes a partial one.
    before=digests(work)
    partial=work/'_takes'/'s01_t1.wav';partial.write_bytes(partial.read_bytes()[:300])
    code,memory,_,_=go('ok',need=1)
    assert code==0 and json.loads((work/'done.json').read_text())['generated']==1
    after=digests(work)
    assert {k:v for k,v in after.items() if k!='s01_t1.wav'}=={k:v for k,v in before.items() if k!='s01_t1.wav'}
    assert after['s01_t1.wav']==before['s01_t1.wav']  # same seed, same take, whole again
    assert not list((work/'_takes').glob('*.part'))


def G_whole(f):
    sys.path.insert(0,str(HERE.parent/'valmera-stories'))
    import voice
    return voice.whole_take(f)


def test_real_over_ceiling_sample_still_stops_owned_group(rig):
    go,work=rig
    code,memory,elapsed,_=go('hang,ok9000',lines=1,mode='long',ceiling=5000)
    assert code==6
    assert memory['stopped']['bytes']==9000 and memory['stopped']['ceiling']==5000
    assert 'ceiling 5000' in memory['reason']
    pid=int((work/'pid').read_text())
    with pytest.raises(ProcessLookupError):os.kill(pid,0)
    assert not (work/'done.json').exists() and elapsed<30


def test_child_exit_code_and_reason_are_reported(rig,capsys):
    go,work=rig
    code,memory,_,_=go('ok,exit',need=1,lines=1,mode='fail')
    assert code==1 and memory['exit_code']==1 and memory['stopped'] is None
    assert memory['reason'].startswith('narrator exit 1: ValueError') and 'segment 7 has no words' in memory['reason']
    assert 'segment 7 has no words' in capsys.readouterr().err
    assert (work/'done.json').exists()
