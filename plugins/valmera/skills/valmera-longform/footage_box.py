"""Run the existing footage engine on the box, in a private scratch directory.
Local subscription calls cross a private loopback SSH bridge when opted in.
Provider credentials otherwise cross stdin only; neither is saved or logged.
No service, switch, customer's project or production data is changed.
"""
from pathlib import Path
import io
import hashlib
import json
import os
import re
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
if os.environ.get('CODEX_PROXY_LOCAL')=='1':
    import footage.ask
    patch=pathlib.Path(sys.argv[1]).parent/'footage_ask.py'
    exec(compile(patch.read_text(),str(patch),'exec'),vars(footage.ask))
    import footage.hear
    patch=pathlib.Path(sys.argv[1]).parent/'footage_hear.py'
    exec(compile(patch.read_text(),str(patch),'exec'),vars(footage.hear))
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
from footage import run, cut, plan, fetch
if os.environ.get('CODEX_PROXY_LOCAL')=='1':
    import box_hear
    box_hear.install()
cut.MAX_S = 7.8
run.SHOT_S = 4.5
# Long-form rescue/mechanism scenes often occur after the short-form scanner's
# first fifteen minutes. Read the complete ranked documentary (at most an hour).
fetch.PROXY_MAX_S=3600
run.HEAR_MAX_S=1800
# yt-dlp's native whole downloader works on the established box route where
# ffmpeg's remote section seek is refused. Cut those same source seconds locally.
fetch.WHOLE_H=720
fetch.WHOLE_MAX='400M'
import fcntl
media=pathlib.Path('/wf4-model-cache/raw-media');media.mkdir(parents=True,exist_ok=True)
originals=media/'originals';originals.mkdir(exist_ok=True)
os.environ['FOOTAGE_KEEP_SOURCES']=str(originals)
manifest=originals/'manifest.json'
if manifest.exists():os.environ['FOOTAGE_LOCAL_SOURCES']=manifest.read_text()
original_av=fetch.whole_av
original_local=fetch.local_source
def cached_av(url):
    folder=media/'av'/hashlib.sha256(url.encode()).hexdigest()[:16]
    marker=folder/'complete.json'
    if marker.exists():
        rec=json.loads(marker.read_text());p=pathlib.Path(rec['path'])
        if rec['url']==url and p.is_file() and p.stat().st_size>2000:return str(p)
    return None
fetch.local_source=lambda url:original_local(url) or cached_av(url)
def whole_av(url,out_dir):
    key=hashlib.sha256(url.encode()).hexdigest()[:16]
    with open(media/('av-'+key+'.lock'),'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        known=fetch.local_source(url)
        if known:return known,'verified-local-source'
        folder=media/'av'/key;folder.mkdir(parents=True,exist_ok=True)
        path,rung=original_av(url,str(folder))
        (folder/'complete.json').write_text(json.dumps(dict(url=url,path=path,rung=rung)))
        return path,rung
fetch.whole_av=whole_av
original_proxy=fetch.proxy
def proxy(url,out_dir,duration=None,max_s=None):
    key=hashlib.sha256((url+':'+str(max_s or fetch.PROXY_MAX_S)).encode()).hexdigest()[:16]
    with open(media/('proxy-'+key+'.lock'),'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        # A previous chapter already fetched these original picture bytes.
        # Interviews still need the scanning copy's sound; picture research can
        # inspect the retained original without paying for another download.
        cached=list((media/'whole'/hashlib.sha256(url.encode()).hexdigest()[:16]).glob('whole.*'))
        cached=[p for p in cached if p.suffix in ('.mp4','.mkv','.webm')]
        if cached and not j.get('_voices'):return str(max(cached,key=lambda p:p.stat().st_size)),'cached original picture'
        folder=media/'proxy'/key
        # The shared downloader clears proxy.* before fetching. Reuse a
        # completed, decodable scan WITH audio before calling it; a later
        # network wall must not destroy this film's only interview recording.
        import subprocess
        for p in folder.glob('proxy.*'):
            if p.suffix not in ('.mp4','.mkv','.webm'):continue
            try:
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
                    'stream=codec_type:format=duration','-of','json',str(p)],stderr=subprocess.DEVNULL))
                kinds={s['codec_type'] for s in info['streams']}
                if {'video','audio'}<=kinds and float(info['format']['duration'])>0:
                    return str(p),'cached original audiovisual scan'
            except (subprocess.CalledProcessError,ValueError,KeyError):pass
        return original_proxy(url,str(folder),duration,max_s)
fetch.proxy=proxy
whole={};locks={};guard=threading.Lock();original_grab=cut.grab
def grab(stream,url,t0,t1,out):
    if not fetch.is_youtube(url):return original_grab(stream,url,t0,t1,out)
    with guard:lock=locks.setdefault(url,threading.Lock())
    with lock:
        if url not in whole:
            key=hashlib.sha256(url.encode()).hexdigest()[:16]
            with open(media/('whole-'+key+'.lock'),'a') as file_lock:
                fcntl.flock(file_lock,fcntl.LOCK_EX)
                folder=media/'whole'/key
                existing=[p for p in folder.glob('whole.*') if p.suffix in ('.mp4','.mkv','.webm') and p.stat().st_size>2000]
                whole[url]=str(max(existing,key=lambda p:p.stat().st_size)) if existing else fetch.whole(url,str(folder))
    return cut.local(whole[url],t0,t1,out)
cut.grab=grab
plan.PLAN_SYSTEM += '\nThis run is a 16:9 long documentary. Average picture length is 4.5 seconds. Match every line exactly; exclude still montages.'
plan.PLAN_SYSTEM += '\nThe named list is the whole-film reference catalogue, not a search checklist. Research ONLY the supplied lines. Include a subject only when one of these lines needs its footage; preserve the precise identity and repair state. Every subject must appear in a returned line subjects list.'
original_plan=plan.plan
def scoped_plan(*args,**kwargs):
    result,usd=original_plan(*args,**kwargs)
    referenced={name for line in result['lines'] for name in line['subjects']}
    result['subjects']=[subject for subject in result['subjects'] if subject['name'] in referenced]
    return result,usd
plan.plan=scoped_plan

find = run.find
j=json.load(open(sys.argv[1]))
if j.get('verification_owner')=='wf4-source-and-encoded-picture-check':
    # The engine explicitly supports caller-owned per-line verification.
    # These are research candidates, never shelf-certified or deliverable
    # pictures. WF4 checks their exact word placement before and after render.
    os.environ['FOOTAGE_VERIFY']='0'
from footage import search
original_search=search.run
def search_with_known(queries,*args,**kwargs):
    found=original_search(queries,*args,**kwargs)
    for k in range(len(queries)):
        urls={h['url'] for h in found.get(k,[])}
        for h in j.get('known_sources',[]):
            if h['url'] not in urls:
                urls.add(h['url'])
                found.setdefault(k,[]).append(dict(h,id=h.get('id') or hashlib.sha256(h['url'].encode()).hexdigest(),where=h.get('where') or 'youtube',duration=h.get('duration')))
    return found
search.run=search_with_known
if j.get('_voices'):
    from footage import search,voice
    from witness_evidence import verify as witness_verdict
    from concurrent.futures import ThreadPoolExecutor
    voice.MAX_S=45.0
    voice.PICK_SYSTEM=voice.PICK_SYSTEM.replace('short documentary','long documentary').replace('about 2 to 9 seconds spoken','about 4 to 40 seconds spoken')
    voice.PICK_SYSTEM+='\nA long-form witness can complete a consequential thought. Prefer personal experience, a decision, difficulty or discovery over a generic mission description. Length follows the thought; do not pad or repeat the narrator.'
    voice.PICK_SYSTEM+='\nThe person field must exactly copy one canonical name from The people; never substitute an alias from a video title.'
    heard_words=voice.heard_words
    def hear_complete_thought(path,start,end,work,*args,**kwargs):
        return heard_words(path,start,max(end,start+voice.MAX_S+8),work,*args,**kwargs)
    voice.heard_words=hear_complete_thought
    original_pick=voice.pick
    last_picks=[];attempt_id=0
    def logged_pick(lines,persons,sources,*args,**kwargs):
        avoid=j.get('avoid',[])
        lines=[ln for ln in lines if ln['i'] not in {v.get('after') for v in avoid}]
        fresh=[]
        for source in sources:
            # The shared picker strips URL from its transcript-only records.
            # Recover its exact source metadata, without fuzzy title matching.
            urls={s['url'] for s in live if s['title']==source['title'] and s.get('channel')==source.get('channel')}
            ranges=[v['time_range'] for v in avoid if v.get('source',{}).get('url',v.get('url')) in urls]
            transcript=[p for p in source.get('transcript',[]) if not any(min(p['t1'],b)>max(p['t0'],a) for a,b in ranges)]
            fresh.append(dict(source,transcript=transcript))
        picks,cost=original_pick(lines,persons,fresh,*args,**kwargs)
        (pathlib.Path(sys.argv[2])/'voice-picks.json').write_text(json.dumps(picks,indent=1))
        last_picks.clear()
        for g in picks:
            source=sources[g['src']]
            urls={s['url'] for s in live if s['title']==source['title'] and s.get('channel')==source.get('channel')}
            last_picks.append(dict(g,source_urls=sorted(urls)))
        (pathlib.Path(sys.argv[2])/('voice-picks-'+str(attempt_id)+'.json')).write_text(json.dumps(last_picks,indent=1))
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
                hits.setdefault(si,[]).append(dict(h,id=h.get('id') or hashlib.sha256(h['url'].encode()).hexdigest(),where=h.get('where') or 'youtube',duration=h.get('duration')))
    print('witness sources found:',sum(len(v) for v in hits.values()),flush=True)
    ranked,usd=plan.rank(subjects,hits,per=3);sources=[];by={}
    for si,h,why in ranked:
        if h['id'] in by:by[h['id']]['subjects'].append(si);continue
        s=dict(h,key='w'+str(len(sources)),subjects=[si],why=why);sources.append(s);by[h['id']]=s
    work=str(scratch/'witness-work');pathlib.Path(work).mkdir(exist_ok=True)
    anchors={sub['name']:[dict(person=sub['name'])] for sub in subjects}
    with ThreadPoolExecutor(max_workers=3) as pool:
        sources=list(pool.map(lambda s:run._open(s,work,print,hear_s=3600,read_s=3600),sources))
    live=[s for s in sources if not s.get('error')]
    def attributed_speaker(proxy,t0,t1,metadata,work):
        source=next((s for s in live if s['proxy']==proxy),None)
        if source is None or not metadata:return 0
        result=witness_verdict(source,metadata[0]['person'],t0,t1,sys.argv[2])
        return sum(x is True for x in result.get('on_camera',[])) if result['ok'] else 0
    # The legacy hook name is retained only for interface compatibility. This
    # verifier reads source attribution; no identity photos/models are used.
    voice.face_shows=attributed_speaker
    trials=[];rejected_ranges=[];voices=[]
    for attempt_id in range(3):
        tried=[]
        voices,cost=voice.find_voices(j['lines'],subjects,live,anchors,sys.argv[2],1920,1080,most=4,tried=tried)
        usd+=cost;trials.append(dict(attempt=attempt_id,picks=list(last_picks),tried=tried,accepted=len(voices)))
        if voices or not last_picks:break
        # Source attribution and the spoken-word gate remain binding. If a chosen
        # passage is off-camera, select another passage from the already-open
        # source, rather than treating that one failure as no witness existing.
        for g in last_picks:
            for url in g['source_urls']:
                rejected_ranges.append(dict(source=dict(url=url),time_range=[max(0,g['piece_t0']-3),g['piece_t0']+53]))
        j['avoid']=j.get('avoid',[])+rejected_ranges
    usd+=sum(s.get('hear_usd',0) for s in live)
    for v in voices:
        v['attribution_frames']=v.pop('face_frames',None)
        v['speaker_verification']='source_attribution'
    (pathlib.Path(sys.argv[2])/'voice-attempts.json').write_text(json.dumps(trials,indent=1))
    result=dict(lines=[],voices=voices,credits=[dict(v['source'],time_range=v['time_range'],voice=v['person']) for v in voices],spend=dict(usd=usd),
        rejected_ranges=rejected_ranges,sources=[{k:v for k,v in s.items() if k not in ('proxy','shots','transcript')} for s in sources])
    (pathlib.Path(sys.argv[2])/'footage.json').write_text(json.dumps(result,indent=1))
    sys.exit(0)
review_errors=[]
rank_rounds=[]
original_rank=plan.rank
def traced_rank(subjects,hits,*args,**kwargs):
    chosen,cost=original_rank(subjects,hits,*args,**kwargs)
    rank_rounds.append(dict(candidates=sum(len(group) for group in hits.values()),selected=len(chosen)))
    return chosen,cost
plan.rank=traced_rank
original_check=run.look.check
def checked_research(*args,**kwargs):
    try:return original_check(*args,**kwargs)
    except Exception as exc:
        review_errors.append(str(exc)[:300])
        raise
run.look.check=checked_research
result=find(j['lines'],j['named'],j['about'],sys.argv[2],1920,1080,
     max_sources=10, per=2, avoid=j.get('avoid'), strict_lines=True, motion_first=True, scout_motion=True,
     alloc='round', reassign_lost=True, whole_first=True, want_voices=False, log=lambda s:print(s,flush=True))
result['research_errors']=review_errors
result['rank_rounds']=rank_rounds
(pathlib.Path(sys.argv[2])/'footage.json').write_text(json.dumps(result,indent=1))
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
slot,scratch,cache,name,port,proof,run_id=sys.argv[1:]
info=json.loads(subprocess.check_output(['docker','inspect',slot]))[0]
allowed={'YTDLP_PROXY','YTDLP_PROXY_FALLBACK','YTDLP_REMOTE_COMPONENTS','YTDLP_FALLBACK_CLIENTS','YTDLP_POT_SERVER_HOME'}
env=dict(os.environ)
for item in info['Config']['Env']:
    k,sep,v=item.partition('=')
    if k in allowed:env[k]=v
pathlib.Path(cache).mkdir(exist_ok=True)
job=json.load(open(scratch+'/input.json'));cpus,memory=(2,4) if job.get('_voices') else (4,8)
cmd=['docker','run','--rm','-i','--name',name,'--cpus',str(cpus),'--memory',str(memory)+'g',
     '-v',scratch+':'+scratch,'-v',cache+':/wf4-model-cache',
     '-e','FOOTAGE_TRANSCRIPT_CACHE=/wf4-model-cache/transcripts',
     '--entrypoint','python']
if port!='off':
    cmd.extend(['--network','host','-e','CODEX_PROXY_LOCAL=1',
                '-e','HF_HOME=/opt/hf','-e','HF_HUB_OFFLINE=1',
                '-e','OPENAI_BASE_URL=http://127.0.0.1:'+port+'/v1',
                '-e','PYTHONPATH='+scratch+'/client',
                '-e','CODEX_PROXY_PROOF='+(proof or run_id or 'wf4-box-'+name)])
    if run_id:cmd.extend(['-e','VALMERA_RUN='+run_id])
for k in sorted(allowed):
    if k in env:cmd.extend(['-e',k])
cmd.extend([info['Image'],'-u',scratch+'/fetch.py',scratch+'/input.json',scratch+'/out'])
code=subprocess.call(cmd,env=env)
out=pathlib.Path(scratch)/'out'
if out.exists():(out/'runner.json').write_text(json.dumps({'image':info['Image'],'image_tag':info['Config']['Image'],'private_container':name,'cpus':cpus,'memory_gb':memory}))
sys.exit(code)
'''


def cache_path(out_root):
    # Nested witness retries share this film's archive bytes, too.
    out_root=next((p for p in (Path(out_root),*Path(out_root).parents) if p.name=='footage'),Path(out_root))
    return '/tmp/wf4-model-'+hashlib.sha256(str(Path(out_root).resolve()).encode()).hexdigest()[:12]


def cleanup_cache(out_root):
    paths={cache_path(out_root),'/tmp/wf4-model-'+hashlib.sha256(str(Path(out_root).resolve()).encode()).hexdigest()[:12]}
    ssh('rm -rf -- '+' '.join(shlex.quote(p) for p in paths),capture_output=True)


def probe(idea):
    """Footage search evidence before choosing a story, on the working box network."""
    with tempfile.TemporaryDirectory(prefix='wf4-idea-') as td:
        try:return fetch(dict(_probe=True,idea=idea),Path(td)/'scan')
        finally:cleanup_cache(td)


def request_key(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def rank_trace(logfile):
    """Read the engine's numerical progress records, including pre-checkpoint runs."""
    formats = (
        (r'plan: (\d+) subjects, (\d+) searches', ('subjects','searches')),
        (r'rank: (\d+) sources to open of (\d+) found', ('selected','candidates')),
        (r'archive round: (\d+) sources would not open, (\d+) subjects had none; (\d+) archive sources to open',
         ('open_failures','uncovered','archive_selected')),
        (r'look: (\d+) frames checked, (?:([0-9]+) reused, )?(\d+) pass', ('checked','reused','passed')),
    )
    trace = {}
    if logfile.exists():
        with logfile.open(errors='replace') as stream:
            for line in stream:
                for pattern, fields in formats:
                    match = re.fullmatch(pattern, line.rstrip())
                    if match:
                        trace.update(zip(fields,(int(n or 0) for n in match.groups())))
                        break
    return trace


def ranked_empty(trace):
    return (trace.get('subjects',0)>0 and trace.get('searches',0)>0 and trace.get('candidates',0)>0
            and trace.get('uncovered')==trace['subjects']
            and all(trace.get(k)==0 for k in ('selected','open_failures','archive_selected','checked','reused','passed')))


def legacy_empty_trace(logfile):
    if not logfile.exists():
        return {}
    with logfile.open('rb') as stream:
        stream.seek(max(0,stream.seek(0,os.SEEK_END)-512))
        last = stream.read().splitlines()
    if last[-1:] != [b'RuntimeError: Archive picture research incomplete; unavailable model checks cannot establish scarce footage: []']:
        return {}
    trace = rank_trace(logfile)
    return trace if ranked_empty(trace) else {}


class PictureResearchUnavailable(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


def completed_research(result):
    counts=result.get('counts',{})
    if result.get('research_errors') or result.get('research_complete') is False:
        return False
    if counts.get('sources_opened',0)>0 and counts.get('frames_checked',0)+counts.get('frames_reused',0)>0:
        return True
    rounds = result.get('rank_rounds',[])
    return (counts.get('sources_tried')==0 and counts.get('clips')==0
            and any(r['candidates'] for r in rounds) and all(r['selected']==0 for r in rounds))


def fetch(payload, out, log=print, _port=None):
    out = Path(out)
    probing=bool(payload.get('_probe'))
    rec = out / ('probe.json' if probing else 'footage.json')
    adapter=hashlib.sha256((PROBE if probing else REMOTE).encode()+
                          (b'' if probing else Path(__file__).with_name('witness_evidence.py').read_bytes())).hexdigest()
    if rec.exists():
        cached=json.loads(rec.read_text())
        stale_voice=payload.get('_voices') and not cached.get('voices') and cached.get('adapter_sha256')!=adapter
        incomplete=not probing and not payload.get('_voices') and not completed_research(cached)
        matches = not cached.get('request_sha256') or cached['request_sha256']==request_key(payload)
        if not stale_voice and not incomplete and matches:return cached
        prior=cached.get('adapter_sha256','unversioned')[:12]
        rec.rename(out/('footage-rejected-'+prior+'.json'))
        if (out/'fetch.log').exists():(out/'fetch.log').rename(out/('fetch-rejected-'+prior+'.log'))
    if os.environ.get('CODEX_PROXY_LOCAL')=='1' and _port is None:
        from subscription_box import bridge
        with bridge(HOST,KEY) as port:
            return fetch(payload,out,log,_port=port)
    out.mkdir(parents=True, exist_ok=True)
    names = ssh("docker ps --format '{{.Names}}'", capture_output=True, text=True).stdout.splitlines()
    slot = next((n for n in names if n.startswith('vbox-batch')), None)
    if not slot:
        raise PictureResearchUnavailable('no box batch image available for footage')
    scratch = '/tmp/wf4-' + uuid.uuid4().hex
    q = shlex.quote
    package = io.BytesIO()
    with tarfile.open(fileobj=package, mode='w') as tf:
        if _port is not None:
            from brand import worker_dir
            from subscription_box import client_files
            files=client_files(_port)
            if not probing:files['box_hear.py']=Path(__file__).with_name('box_hear.py').read_bytes()
            footage = Path(worker_dir()) / 'footage'
            for helper in ('ask.py', 'hear.py'):
                files['footage_' + helper] = (footage / helper).read_bytes()
            for name,data in files.items():
                t=tarfile.TarInfo(name);t.size=len(data);tf.addfile(t,io.BytesIO(data))
        for name, data in [('input.json', json.dumps(payload).encode()), ('fetch.py', (PROBE if probing else REMOTE).encode()),('runner.py',RUNNER.encode())]:
            t = tarfile.TarInfo(name); t.size = len(data); tf.addfile(t, io.BytesIO(data))
        if not probing:tf.add(Path(__file__).with_name('witness_evidence.py'),arcname='witness_evidence.py',recursive=False)
    ssh(f'mkdir -p {q(scratch)}', capture_output=True)
    container='wf4-private-'+scratch.rsplit('-',1)[-1]
    try:
        ssh(f'tar xf - -C {q(scratch)}', input=package.getvalue(), capture_output=True)
        cmd = shlex.join(['python3', scratch + '/runner.py', slot, scratch, cache_path(out.parent),
                          container, str(_port) if _port else 'off',
                          os.environ.get('CODEX_PROXY_PROOF', ''), os.environ.get('VALMERA_RUN', '')])
        try:
            with open(out / 'fetch.log', 'w') as f:
                ssh(cmd, input=os.environ['OPENAI_API_KEY']+'\n', text=True, stdout=f, stderr=subprocess.STDOUT, timeout=7200)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if probing or payload.get('_voices'):
                raise
            with (out/'fetch.log').open('rb') as stream:
                stream.seek(max(0,stream.seek(0,2)-2048))
                tail = stream.read().decode(errors='replace').strip().splitlines()
            detail = tail[-1][:500] if tail else type(exc).__name__
            raise PictureResearchUnavailable('Archive acquisition failed: '+detail) from exc
        archive = out / 'transfer.tar'
        with open(archive, 'wb') as f:
            ssh(f'tar cf - -C {q(scratch)}/out .', stdout=f, timeout=600)
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter='data')
        archive.unlink()
        result = json.loads(rec.read_text())
        result['adapter_sha256']=adapter
        picture = not probing and not payload.get('_voices')
        if picture:
            result['request_sha256'] = request_key(payload)
            result['rank_trace'] = rank_trace(out/'fetch.log')
            result['research_complete'] = completed_research(result)
        if payload.get('verification_owner'):
            result['verification_owner']=payload['verification_owner']
            result['research_candidates_only']=True
        if payload.get('interval_targets'):
            result['interval_targets']=payload['interval_targets']
        if payload.get('narration_context'):
            result['narration_context']=payload['narration_context']
        pending = rec.with_suffix('.tmp')
        pending.write_text(json.dumps(result,indent=1))
        pending.replace(rec)
        if picture and not result['research_complete']:
            reasons = result.get('research_errors') or [s['error'] for s in result.get('sources',[]) if s.get('error')]
            raise PictureResearchUnavailable('Archive research incomplete: '+str(reasons or result['counts']), result)
        log(f"box footage: {result.get('counts',dict(covered=result.get('covered'),clips=result.get('clips')))}")
        return result
    finally:
        # Only this invocation's private container and directory are touched.
        ssh(f'docker rm -f {q(container)} >/dev/null 2>&1 || true', capture_output=True)
        ssh(f'rm -rf -- {q(scratch)}', capture_output=True)
