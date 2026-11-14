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
import uuid

HOST = 'root@13.140.241.231'
KEY = str(Path.home() / '.ssh/valmera_box_ed25519')
REMOTE = r'''import json, os, sys, pathlib, threading, hashlib
sys.path.insert(0, '/app')
os.environ['OPENAI_API_KEY'] = sys.stdin.readline().strip()
os.environ['VALMERA_FOOTAGE'] = '1'
os.environ['SKIP_DB_INIT'] = '1'
scratch=pathlib.Path(sys.argv[1]).parent
os.environ['YUNET_MODEL']=str(scratch/'models/face_detection_yunet_2023mar.onnx')
os.environ['FOOTAGE_SFACE']=str(scratch/'models/face_recognition_sface_2021dec.onnx')
from footage import run, cut, plan, fetch, identity
if not identity.models():raise RuntimeError('identity models required for real speaker inserts')
cut.MAX_S = 7.8
run.SHOT_S = 4.5
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
find(j['lines'],j['named'],j['about'],sys.argv[2],1920,1080,
     max_sources=10, per=2, avoid=j.get('avoid'), log=lambda s:print(s,flush=True))
'''


def ssh(command, **kw):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20', '-i', KEY, HOST, command], check=True, **kw)


PROBE = r'''import json, os, sys
sys.path.insert(0,'/app')
os.environ['OPENAI_API_KEY']=sys.stdin.readline().strip()
os.environ['SKIP_DB_INIT']='1'
from footage import plan,search
j=json.loads(sys.stdin.read())
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
print(json.dumps(dict(subjects=rows,covered=covered,clips=sum(r['found'] for r in rows),
    ok=bool(rows) and covered/len(rows)>=.6,usd=usd+cost,method='worker/footage planned and ranked real-video search results; fetched and visually verified during supply')))
'''


RUNNER = r'''import json,os,sys,subprocess,pathlib
slot,scratch,cache,name=sys.argv[1:]
info=json.loads(subprocess.check_output(['docker','inspect',slot]))[0]
allowed={'YTDLP_PROXY','YTDLP_PROXY_FALLBACK','YTDLP_REMOTE_COMPONENTS','YTDLP_FALLBACK_CLIENTS','YTDLP_POT_SERVER_HOME'}
env=dict(os.environ)
for item in info['Config']['Env']:
    k,sep,v=item.partition('=')
    if k in allowed:env[k]=v
pathlib.Path(cache).mkdir(exist_ok=True)
cmd=['docker','run','--rm','-i','--name',name,'--cpus','4','--memory','8g',
     '-v',scratch+':'+scratch,'-v',cache+':/wf4-model-cache','-e','HF_HOME=/wf4-model-cache',
     '--entrypoint','python']
for k in sorted(allowed):
    if k in env:cmd.extend(['-e',k])
cmd.extend([info['Image'],'-u',scratch+'/fetch.py',scratch+'/input.json',scratch+'/out'])
code=subprocess.call(cmd,env=env)
out=pathlib.Path(scratch)/'out'
if out.exists():(out/'runner.json').write_text(json.dumps({'image':info['Image'],'image_tag':info['Config']['Image'],'private_container':name,'cpus':4,'memory_gb':8}))
sys.exit(code)
'''


def cache_path(out_root):
    return '/tmp/wf4-model-'+hashlib.sha256(str(Path(out_root).resolve()).encode()).hexdigest()[:12]


def cleanup_cache(out_root):
    ssh('rm -rf -- '+shlex.quote(cache_path(out_root)),capture_output=True)


def probe(idea):
    """Footage search evidence before choosing a story, on the working box network."""
    names=ssh("docker ps --format '{{.Names}}'",capture_output=True,text=True).stdout.splitlines()
    slot=next((n for n in names if n.startswith('vbox-batch')),None)
    if not slot:raise RuntimeError('no box batch image available for footage discovery')
    command=f'docker exec -i {shlex.quote(slot)} python -c {shlex.quote(PROBE)}'
    r=ssh(command,input=os.environ['OPENAI_API_KEY']+'\n'+json.dumps(idea),text=True,capture_output=True,timeout=1200)
    return json.loads(r.stdout.splitlines()[-1])


def fetch(payload, out, log=print):
    out = Path(out)
    rec = out / 'footage.json'
    if rec.exists():
        return json.loads(rec.read_text())
    out.mkdir(parents=True, exist_ok=True)
    names = ssh("docker ps --format '{{.Names}}'", capture_output=True, text=True).stdout.splitlines()
    slot = next((n for n in names if n.startswith('vbox-batch')), None)
    if not slot:
        raise RuntimeError('no box batch image available for footage')
    scratch = '/tmp/wf4-' + uuid.uuid4().hex
    q = shlex.quote
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode='w') as tf:
        for name, data in [('input.json', json.dumps(payload).encode()), ('fetch.py', REMOTE.encode()),('runner.py',RUNNER.encode())]:
            t = tarfile.TarInfo(name); t.size = len(data); tf.addfile(t, io.BytesIO(data))
        models=Path('/Users/masaoodi/Documents/Valmera/.valmera/models')
        for name in ('face_detection_yunet_2023mar.onnx','face_recognition_sface_2021dec.onnx'):
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
        log(f"box footage: {result['counts']}")
        return result
    finally:
        # Only this invocation's private container and directory are touched.
        ssh(f'docker rm -f {q(container)} >/dev/null 2>&1 || true', capture_output=True)
        ssh(f'rm -rf -- {q(scratch)}', capture_output=True)
