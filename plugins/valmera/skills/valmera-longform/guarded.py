"""Run one owned narration under the shared locks and a physical-memory ceiling.
The footprint tool includes Metal buffers that RSS omits. The guard only stops
its own process group. /usr/bin/time -l retains the independent peak as evidence.
"""
import argparse,fcntl,json,os,signal,subprocess,time
from pathlib import Path

ROOT=Path('/Users/masaoodi/Documents/Valmera')

def run(command,out,label,ceiling=6_000_000_000):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    tmp=out/'tmp';tmp.mkdir(exist_ok=True)
    env=dict(os.environ,TMPDIR=str(tmp),MallocLargeCache='0')
    peak=0;samples=[];stopped=None
    with open(ROOT/'.valmera/week-1106/.narrate.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        with open(out/'run.log','w') as log:
            p=subprocess.Popen(['zsh',str(ROOT/'.valmera/week-1106/heavy.sh'),label,'/usr/bin/time','-l',*command],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            while p.poll() is None:
                rows=[list(map(int,line.split())) for line in subprocess.check_output(['ps','-axo','pid=,ppid=,rss='],text=True).splitlines()]
                ids={p.pid}
                for _ in range(10):
                    more={pid for pid,parent,rss in rows if parent in ids}
                    if more<=ids:break
                    ids|=more
                state=tmp/'footprint.json'
                r=subprocess.run(['footprint','--noCategories','-j',str(state),*[str(i) for i in sorted(ids)]],capture_output=True,timeout=15)
                if state.exists():
                    j=json.loads(state.read_text());state.unlink()
                    for proc in j.get('processes',[]):
                        fp=max(proc.get('footprint',0),(proc.get('auxiliary') or {}).get('phys_footprint_peak',0))
                        peak=max(peak,fp);samples.append(dict(at=time.time(),pid=proc['pid'],bytes=fp))
                        if fp>ceiling:
                            stopped=dict(pid=proc['pid'],bytes=fp,ceiling=ceiling)
                    if stopped:
                        os.killpg(p.pid,signal.SIGTERM)
                        try:p.wait(timeout=15)
                        except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                        break
                time.sleep(2)
            code=p.wait()
    report=dict(exit_code=code,peak_process_footprint_bytes=peak,stopped=stopped,samples=samples,method='macOS footprint phys_footprint_peak, sampled every 2s; /usr/bin/time -l in run.log')
    (out/'memory.json').write_text(json.dumps(report,indent=1))
    print(json.dumps({k:v for k,v in report.items() if k!='samples'}),flush=True)
    return 6 if stopped else code

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--out',required=True);a.add_argument('--label',default='wf4-narration');a.add_argument('command',nargs=argparse.REMAINDER);ns=a.parse_args()
    command=ns.command[1:] if ns.command[:1]==['--'] else ns.command
    raise SystemExit(run(command,ns.out,ns.label))
