"""Run the existing footage engine on the box, in a private scratch directory.
Only the funded OpenAI key crosses stdin over SSH; it is never saved or logged.
No service, switch, customer's project or production data is changed.
"""
from pathlib import Path
import io
import hashlib
import json
import os
import shlex
import subprocess
import tarfile
import tempfile
import uuid

HOST = 'root@13.140.241.231'
KEY = str(Path.home() / '.ssh/valmera_box_ed25519')
OFFLINE = r'''
for key in ('ANTHROPIC_API_KEY','CLAUDE_API_KEY'):os.environ.pop(key,None)
os.environ.update(NAMED_PICTURE_MODEL='gpt-6-sol',NAMED_CHECK_MODEL='gpt-6-sol',FOOTAGE_MODEL='gpt-6-sol',WRITER_PROVIDER='openai')
if os.environ.get('DATABASE_URL'):raise RuntimeError('private footage jobs must have no database credentials')
# Keep the engine's provider/budget fences. Its charge sink for this standalone
# job is a local cost ledger; no production connection is created or written.
import llm,threading,pathlib,time
cost_lock=threading.Lock()
pathlib.Path(sys.argv[2]).mkdir(parents=True,exist_ok=True)
def local_charge(purpose,request,response,usage):
    usd=llm._call_cost_usd(purpose,request,response,usage)
    with cost_lock,open(pathlib.Path(sys.argv[2])/'model-costs.jsonl','a') as f:
        f.write(json.dumps(dict(at=time.time(),purpose=purpose,model=(request or {}).get('model'),usd=usd))+'\n')
llm._charge=local_charge
'''
REMOTE = r'''import json, os, sys, pathlib, threading, hashlib
sys.path.insert(0, '/app')
os.environ['OPENAI_API_KEY'] = sys.stdin.readline().strip()
os.environ['VALMERA_FOOTAGE'] = '1'
os.environ['SKIP_DB_INIT'] = '1'
__OFFLINE__
scratch=pathlib.Path(sys.argv[1]).parent
os.environ['YUNET_MODEL']=str(scratch/'models/face_detection_yunet_2023mar.onnx')
os.environ['FOOTAGE_SFACE']=str(scratch/'models/face_recognition_sface_2021dec.onnx')
from footage import run, cut, plan, fetch, identity
if not identity.models():raise RuntimeError('identity models required for real speaker inserts')
cut.MAX_S = 7.8
run.SHOT_S = 4.5
# Long-form rescue/mechanism scenes often occur after the short-form scanner's
# first fifteen minutes. Read the complete ranked documentary (at most an hour).
fetch.PROXY_MAX_S=3600
run.HEAR_MAX_S=1800
# yt-dlp's native whole downloader works on the established box route where
# ffmpeg's remote section seek is refused. Cut those same source seconds locally.
fetch.WHOLE_H=1080
fetch.WHOLE_MAX='900M'
whole={};locks={};guard=threading.Lock();original_grab=cut.grab
def grab(stream,url,t0,t1,out):
    if not fetch.is_youtube(url):return original_grab(stream,url,t0,t1,out)
    with guard:lock=locks.setdefault(url,threading.Lock())
    with lock:
        if url not in whole:
            whole[url]=fetch.whole(url,str(scratch/'whole'/hashlib.sha256(url.encode()).hexdigest()[:16]))
    return cut.local(whole[url],t0,t1,out)
cut.grab=grab
plan.PLAN_SYSTEM += '\nThis run is a 16:9 long documentary. Average picture length is 4.5 seconds. Match every line exactly; exclude still montages.'
find = run.find
j=json.load(open(sys.argv[1]))
if j.get('_voices'):
    from footage import search,voice
    from concurrent.futures import ThreadPoolExecutor
    voice.MAX_S=22.0
    voice.PICK_SYSTEM=voice.PICK_SYSTEM.replace('about 2 to 9 seconds spoken','about 4 to 20 seconds spoken')
    voice.PICK_SYSTEM+='\nThe person field must exactly copy one canonical name from The people; never substitute an alias from a video title.'
    original_pick=voice.pick
    def logged_pick(*args,**kwargs):
        picks,cost=original_pick(*args,**kwargs)
        (pathlib.Path(sys.argv[2])/'voice-picks.json').write_text(json.dumps(picks,indent=1))
        return picks,cost
    voice.pick=logged_pick
    subjects=j['people'];flat=[(si,q) for si,s in enumerate(subjects) for q in s['searches']]
    found=search.run([q for si,q in flat]);hits={}
    for k,(si,q) in enumerate(flat):
        have={h['id'] for h in hits.get(si,[])}
        hits.setdefault(si,[]).extend(h for h in found.get(k,[]) if h['id'] not in have)
    for si in range(len(subjects)):
        urls={h['url'] for h in hits.get(si,[])}
        for h in j.get('known_sources',[]):
            if h['url'] not in urls:
                hits.setdefault(si,[]).append(dict(h,id=hashlib.sha256(h['url'].encode()).hexdigest(),where=h.get('where') or 'youtube',duration=None))
    print('witness sources found:',sum(len(v) for v in hits.values()),flush=True)
    ranked,usd=plan.rank(subjects,hits,per=3);sources=[];by={}
    for si,h,why in ranked:
        if h['id'] in by:by[h['id']]['subjects'].append(si);continue
        s=dict(h,key='w'+str(len(sources)),subjects=[si],why=why);sources.append(s);by[h['id']]=s
    work=str(scratch/'witness-work');pathlib.Path(work).mkdir(exist_ok=True)
    anchors={}
    for sub in subjects:
        portrait=dict(sub,must_show='A clear portrait of '+sub['name']+', the named person; an identity photograph, not a speaking moment.')
        anchors[sub['name']],cost=run.person_anchors(portrait,work);usd+=cost
        print('identity portraits:',sub['name'],len(anchors[sub['name']]),flush=True)
    (pathlib.Path(sys.argv[2])/'voice-anchors.json').write_text(json.dumps({k:len(v) for k,v in anchors.items()},indent=1))
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources=list(pool.map(lambda s:run._open(s,work,print,hear_s=3600,read_s=3600),sources))
    live=[s for s in sources if not s.get('error')]
    voices,cost=voice.find_voices(j['lines'],subjects,live,anchors,sys.argv[2],1920,1080,most=4)
    usd+=cost+sum(s.get('hear_usd',0) for s in live)
    result=dict(lines=[],voices=voices,credits=[dict(v['source'],time_range=v['time_range'],voice=v['person']) for v in voices],spend=dict(usd=usd),
        sources=[{k:v for k,v in s.items() if k not in ('proxy','shots','transcript')} for s in sources])
    (pathlib.Path(sys.argv[2])/'footage.json').write_text(json.dumps(result,indent=1))
    sys.exit(0)
find(j['lines'],j['named'],j['about'],sys.argv[2],1920,1080,
     max_sources=10, per=2, avoid=j.get('avoid'), log=lambda s:print(s,flush=True))
'''


def ssh(command, **kw):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-i', KEY, HOST, command], check=True, **kw)


PROBE = r'''import json, os, sys, pathlib
sys.path.insert(0,'/app')
os.environ['OPENAI_API_KEY']=sys.stdin.readline().strip()
os.environ['SKIP_DB_INIT']='1'
__OFFLINE__
from footage import plan,search
j=json.load(open(sys.argv[1]))['idea']
lines=[dict(i=0,say=j['story'],pictures=[n['name'] for n in j['named']])]
pl,usd=plan.plan(lines,j['named'],j['title']+'; long documentary, rich original archive footage required')
subjects=pl['subjects'];flat=[(si,q) for si,s in enumerate(subjects) for q in s['searches']]
found=search.run([q for si,q in flat]);hits={}
for k,(si,q) in enumerate(flat):
    have={h['id'] for h in hits.get(si,[])}
    hits.setdefault(si,[]).extend(h for h in found.get(k,[]) if h['id'] not in have)
kept,cost=plan.rank(subjects,hits,per=2);rows=[]
for si,s in enumerate(subjects):
    chosen=[dict(h,why=why) for ix,h,why in kept if ix==si]
    rows.append(dict(name=s['name'],found=len(chosen),sources=chosen))
covered=sum(r['found']>0 for r in rows)
result=dict(subjects=rows,covered=covered,clips=sum(r['found'] for r in rows),
    ok=bool(rows) and covered/len(rows)>=.6,usd=usd+cost,method='worker/footage planned and ranked real-video search results; fetched and visually verified during supply')
(pathlib.Path(sys.argv[2])/'probe.json').write_text(json.dumps(result,indent=1))
'''

REMOTE=REMOTE.replace('__OFFLINE__',OFFLINE)
PROBE=PROBE.replace('__OFFLINE__',OFFLINE)


RUNNER = r'''import json,os,sys,subprocess,pathlib
slot,scratch,cache,name=sys.argv[1:]
info=json.loads(subprocess.check_output(['docker','inspect',slot]))[0]
allowed={'YTDLP_PROXY','YTDLP_PROXY_FALLBACK','YTDLP_REMOTE_COMPONENTS','YTDLP_FALLBACK_CLIENTS','YTDLP_POT_SERVER_HOME'}
env=dict(os.environ)
for item in info['Config']['Env']:
    k,sep,v=item.partition('=')
    if k in allowed:env[k]=v
pathlib.Path(cache).mkdir(exist_ok=True)
job=json.load(open(scratch+'/input.json'));cpus,memory=(2,4) if job.get('_voices') else (4,8)
cmd=['docker','run','--rm','-i','--name',name,'--cpus',str(cpus),'--memory',str(memory)+'g',
     '-v',scratch+':'+scratch,'-v',cache+':/wf4-model-cache','-e','HF_HOME=/wf4-model-cache',
     '--entrypoint','python']
for k in sorted(allowed):
    if k in env:cmd.extend(['-e',k])
cmd.extend([info['Image'],'-u',scratch+'/fetch.py',scratch+'/input.json',scratch+'/out'])
code=subprocess.call(cmd,env=env)
out=pathlib.Path(scratch)/'out'
if out.exists():(out/'runner.json').write_text(json.dumps({'image':info['Image'],'image_tag':info['Config']['Image'],'private_container':name,'cpus':cpus,'memory_gb':memory}))
sys.exit(code)
'''


def cache_path(out_root):
    return '/tmp/wf4-model-'+hashlib.sha256(str(Path(out_root).resolve()).encode()).hexdigest()[:12]


def cleanup_cache(out_root):
    ssh('rm -rf -- '+shlex.quote(cache_path(out_root)),capture_output=True)


def probe(idea):
    """Footage search evidence before choosing a story, on the working box network."""
    with tempfile.TemporaryDirectory(prefix='wf4-idea-') as td:
        try:return fetch(dict(_probe=True,idea=idea),Path(td)/'scan')
        finally:cleanup_cache(td)


def fetch(payload, out, log=print):
    out = Path(out)
    probing=bool(payload.get('_probe'))
    rec = out / ('probe.json' if probing else 'footage.json')
    adapter=hashlib.sha256((PROBE if probing else REMOTE).encode()).hexdigest()
    if rec.exists():
        cached=json.loads(rec.read_text())
        if not (payload.get('_voices') and not cached.get('voices') and cached.get('adapter_sha256')!=adapter):return cached
        prior=cached.get('adapter_sha256','unversioned')[:12]
        rec.rename(out/('footage-rejected-'+prior+'.json'))
        if (out/'fetch.log').exists():(out/'fetch.log').rename(out/('fetch-rejected-'+prior+'.log'))
    out.mkdir(parents=True, exist_ok=True)
    names = ssh("docker ps --format '{{.Names}}'", capture_output=True, text=True).stdout.splitlines()
    slot = next((n for n in names if n.startswith('vbox-batch')), None)
    if not slot:
        raise RuntimeError('no box batch image available for footage')
    scratch = '/tmp/wf4-' + uuid.uuid4().hex
    q = shlex.quote
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode='w') as tf:
        for name, data in [('input.json', json.dumps(payload).encode()), ('fetch.py', (PROBE if probing else REMOTE).encode()),('runner.py',RUNNER.encode())]:
            t = tarfile.TarInfo(name); t.size = len(data); tf.addfile(t, io.BytesIO(data))
        models=Path('/Users/masaoodi/Documents/Valmera/.valmera/models')
        for name in (() if probing else ('face_detection_yunet_2023mar.onnx','face_recognition_sface_2021dec.onnx')):
            if not (models/name).is_file():raise RuntimeError('missing identity model: '+str(models/name))
            tf.add(models/name,arcname='models/'+name,recursive=False)
    ssh(f'mkdir -p {q(scratch)}', capture_output=True)
    container='wf4-private-'+scratch.rsplit('-',1)[-1]
    try:
        ssh(f'tar xf - -C {q(scratch)}', input=package.getvalue(), capture_output=True)
        cmd = f'python3 {q(scratch)}/runner.py {q(slot)} {q(scratch)} {q(cache_path(out.parent))} {q(container)}'
        with open(out / 'fetch.log', 'w') as f:
            ssh(cmd, input=os.environ['OPENAI_API_KEY']+'\n', text=True, stdout=f, stderr=subprocess.STDOUT, timeout=7200)
        archive = out / 'transfer.tar'
        with open(archive, 'wb') as f:
            ssh(f'tar cf - -C {q(scratch)}/out .', stdout=f, timeout=600)
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter='data')
        archive.unlink()
        result = json.loads(rec.read_text())
        result['adapter_sha256']=adapter
        rec.write_text(json.dumps(result,indent=1))
        log(f"box footage: {result.get('counts',dict(covered=result.get('covered'),clips=result.get('clips')))}")
        return result
    finally:
        # Only this invocation's private container and directory are touched.
        ssh(f'docker rm -f {q(container)} >/dev/null 2>&1 || true', capture_output=True)
        ssh(f'rm -rf -- {q(scratch)}', capture_output=True)
