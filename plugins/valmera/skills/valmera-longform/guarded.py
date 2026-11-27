"""Run one owned narration under the shared locks and a physical-memory ceiling.
The footprint tool includes Metal buffers that RSS omits. The guard only stops
its own process group. /usr/bin/time -l retains the independent peak as evidence.

The footprint read is an OS probe, not part of the narration (fix 63, 26 Nov: a
15 s footprint timeout unwound a healthy 1h54m read). A slow, failed, truncated
or foreign read is a missed sample: it is discarded, counted with its reason, and
the next read comes after a bounded back-off. Only the ceiling, measured on a
verified sample of an owned process, or the guard itself being stopped, ends the
owned process group.
"""
from contextlib import ExitStack
import argparse,fcntl,json,os,signal,subprocess,time
from pathlib import Path

ROOT=Path('/Users/masaoodi/Documents/Valmera')
TIME=['/usr/bin/time','-l']
PROBE_TIMEOUT=10.0   # one footprint read; slower is a missed sample
INTERVAL=2.0         # between verified samples
MAX_INTERVAL=30.0    # back-off ceiling while the probe keeps missing
TIME_ROWS={'maximum resident set size':'time_max_rss_bytes','peak memory footprint':'time_peak_footprint_bytes'}


def owned_ids(root,known=()):
    """The owned tree under root. A failed ps keeps the ids already known."""
    try:
        out=subprocess.run(['ps','-axo','pid=,ppid='],capture_output=True,text=True,timeout=10).stdout
    except (OSError,subprocess.SubprocessError):
        return {root,*known}
    rows=[]
    for line in out.splitlines():
        parts=line.split()
        if len(parts)==2 and parts[0].isdigit() and parts[1].isdigit():rows.append((int(parts[0]),int(parts[1])))
    ids={root}
    for _ in range(10):
        more={pid for pid,parent in rows if parent in ids}
        if more<=ids:break
        ids|=more
    return ids


def _discard(state):
    try:state.unlink()
    except FileNotFoundError:pass


def probe(ids,state,timeout=None):
    """One footprint read of the owned ids: ([(pid,bytes)], None) or (None, reason).
    The state file is removed first, so only this call's output is read, and only
    rows for pids this call asked for are kept."""
    timeout=PROBE_TIMEOUT if timeout is None else timeout
    try:_discard(state)
    except OSError as e:return None,'stale footprint file kept: '+type(e).__name__
    try:
        f=subprocess.Popen(['footprint','--noCategories','-j',str(state),*[str(i) for i in sorted(ids)]],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    except OSError as e:
        return None,'footprint unavailable: '+type(e).__name__
    try:
        _,err=f.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        f.terminate()
        try:f.communicate(timeout=2)
        except subprocess.TimeoutExpired:f.kill();f.communicate()
        _discard(state)
        return None,f'footprint timed out after {timeout:g}s'
    try:
        if not state.exists():
            tail=(err or b'').decode(errors='replace').strip().splitlines()[-1:]
            return None,f'footprint exit {f.returncode}, no sample'+(': '+tail[0][:120] if tail else '')
        try:j=json.loads(state.read_text())
        except (OSError,ValueError):return None,'footprint sample unreadable'
    finally:
        _discard(state)
    rows=[]
    for proc in (j.get('processes') if isinstance(j,dict) else None) or []:
        if not isinstance(proc,dict) or proc.get('pid') not in ids:continue
        aux=proc.get('auxiliary') if isinstance(proc.get('auxiliary'),dict) else {}
        values=[v for v in (proc.get('footprint'),aux.get('phys_footprint_peak')) if isinstance(v,int) and v>=0]
        if values:rows.append((proc['pid'],max(values)))
    if not rows:return None,'footprint sample held no owned process'
    return rows,None


def sample(root,known,state):
    """A read of the current owned tree. A cheap miss (a child that left the tree,
    a truncated or empty sample) is retried once on a fresh tree; a timeout is not,
    so one tick costs at most one probe timeout."""
    ids=owned_ids(root,known)
    rows,reason=probe(ids,state)
    misses=[reason] if reason else []
    if rows is None and 'timed out' not in reason:
        ids=owned_ids(root,ids)
        rows,reason=probe(ids,state)
        if reason:misses.append(reason)
    return ids,rows,misses


def time_evidence(log):
    """The independent /usr/bin/time -l peak rows from the end of run.log."""
    found={}
    for line in _tail(log,65536):
        parts=line.strip().split(None,1)
        if len(parts)==2 and parts[0].isdigit() and parts[1] in TIME_ROWS:found[TIME_ROWS[parts[1]]]=int(parts[0])
    return found


def _tail(path,size):
    try:
        with open(path,'rb') as f:
            f.seek(max(0,os.path.getsize(path)-size))
            return f.read().decode(errors='replace').splitlines()
    except OSError:
        return []


def failure_reason(log,code,stopped):
    if stopped:
        return f"stopped owned group: pid {stopped['pid']} footprint {stopped['bytes']} > ceiling {stopped['ceiling']}"
    # The narrator's own last words: a Python traceback's final line when there is
    # one, else the last line, without /usr/bin/time's numeric rows.
    lines=[l.rstrip() for l in _tail(log,16384) if l.strip() and not l.split()[0].replace('.','',1).isdigit()]
    heads=[i for i,l in enumerate(lines) if l.startswith('Traceback (most recent call last)')]
    if heads:
        tail=[l for l in lines[heads[-1]+1:] if not l[:1].isspace()]
        lines=tail or lines
    return f'narrator exit {code}'+(': '+lines[-1].strip()[:300] if lines else '')


def _write(path,data):
    try:
        part=path.with_name(path.name+'.part');part.write_text(json.dumps(data));os.replace(part,path)
    except OSError:
        pass


def run(command,out,label,ceiling=6_000_000_000,*,measure_only=False):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    tmp=out/'tmp';tmp.mkdir(exist_ok=True)
    env=dict(os.environ,TMPDIR=str(tmp),MallocLargeCache='0')
    # This is a separate narrator environment. A worker PYTHONPATH leaks
    # worker/coverage.py into numba's optional coverage-package import.
    # Each narrator entry point already establishes its own local imports.
    env.pop('PYTHONPATH',None)
    if env.get('CODEX_PROXY_LOCAL')=='1':
        client=ROOT/'.valmera/tools/codex-proxy/client'
        if not (client/'sitecustomize.py').is_file():
            raise RuntimeError('Local subscription network guard is unavailable')
        env['PYTHONPATH']=str(client)
    peak=0;samples=[];stopped=None
    probes=dict(ok=0,missed=0,reasons={},longest_gap_s=0.0)
    def stop_owned(p):
        if p.poll() is not None:return
        os.killpg(p.pid,signal.SIGTERM)
        try:p.wait(timeout=15)
        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
    state=tmp/'footprint.json'
    with ExitStack() as resources:
        if not measure_only:
            lock=resources.enter_context(open(ROOT/'.valmera/week-1106/.narrate.lock','a'))
            fcntl.flock(lock,fcntl.LOCK_EX)
        with open(out/'run.log','w') as log:
            p=subprocess.Popen(['zsh',str(ROOT/'.valmera/week-1106/heavy.sh'),label,*TIME,*command],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            started=last_ok=time.time();interval=INTERVAL;ids={p.pid}
            try:
                while p.poll() is None:
                    try:
                        ids,rows,misses=sample(p.pid,ids,state)
                    except Exception as e:  # noqa: BLE001 - the guard's own fault is a missed sample, never the narrator's end
                        rows,misses=None,['guard probe error: '+type(e).__name__]
                    now=time.time()
                    if rows is None and p.poll() is not None:break  # the narrator finished during the read
                    for reason in misses:
                        probes['missed']+=1
                        if reason in probes['reasons'] or len(probes['reasons'])<8:
                            probes['reasons'][reason]=probes['reasons'].get(reason,0)+1
                    if rows is None:
                        interval=min(interval*2,MAX_INTERVAL)
                    else:
                        probes['ok']+=1;probes['longest_gap_s']=round(max(probes['longest_gap_s'],now-last_ok),1)
                        last_ok=now;interval=INTERVAL
                        for pid,fp in rows:
                            peak=max(peak,fp);samples.append(dict(at=now,pid=pid,bytes=fp))
                            if fp>ceiling and not stopped:
                                stopped=dict(pid=pid,bytes=fp,ceiling=ceiling)
                        if stopped:
                            stop_owned(p)
                            break
                    _write(out/'live.json',dict(pid=p.pid,peak_process_footprint_bytes=peak,sampled_at=last_ok,
                                                probes_missed=probes['missed']))
                    try:p.wait(timeout=interval)
                    except subprocess.TimeoutExpired:pass
            finally:
                stop_owned(p)
            code=p.wait()
    probes['longest_gap_s']=round(max(probes['longest_gap_s'],time.time()-last_ok),1)
    evidence=time_evidence(out/'run.log')
    report=dict(exit_code=code,peak_process_footprint_bytes=peak,stopped=stopped,probes=probes,**evidence,samples=samples,
                method='macOS footprint phys_footprint_peak, sampled every 2s (missed reads back off to 30s and are counted); /usr/bin/time -l in run.log')
    if code or stopped:report['reason']=failure_reason(out/'run.log',code,stopped)
    (out/'memory.json').write_text(json.dumps(report,indent=1))
    print(json.dumps({k:v for k,v in report.items() if k!='samples'}),flush=True)
    if probes['missed']:
        print(f"footprint probe missed {probes['missed']} of {probes['ok']+probes['missed']} reads; longest gap "
              f"{probes['longest_gap_s']}s; {evidence or 'no /usr/bin/time row'}: "+'; '.join(f'{k} x{v}' for k,v in probes['reasons'].items()),flush=True)
    if 'reason' in report:
        import sys
        print(report['reason'],file=sys.stderr,flush=True)
    return 6 if stopped else code

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--measure-only',action='store_true');a.add_argument('--out',required=True);a.add_argument('--label',default='wf4-narration');a.add_argument('command',nargs=argparse.REMAINDER);ns=a.parse_args()
    command=ns.command[1:] if ns.command[:1]==['--'] else ns.command
    raise SystemExit(run(command,ns.out,ns.label,measure_only=ns.measure_only))
