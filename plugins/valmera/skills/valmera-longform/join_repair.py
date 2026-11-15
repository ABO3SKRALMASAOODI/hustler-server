"""Bounded, click-free level rides on measured narration seams; no time edits."""
from pathlib import Path
import hashlib,json,shutil,subprocess,sys
import numpy as np


def ride(x,sr,times,db):
    a=max(0,round(times[0]*sr));b=min(len(x),round(times[-1]*sr))
    if b<=a:return
    t=np.arange(a,b,dtype=np.float64)/sr
    gain=np.zeros(len(t),np.float32)
    for i,(lo,hi) in enumerate(zip(times,times[1:])):
        selected=(t>=lo)&(t<=hi)
        phase=np.clip((t[selected]-lo)/(hi-lo),0,1)
        gain[selected]=db[i]+(db[i+1]-db[i])*(.5-.5*np.cos(np.pi*phase))
    x[a:b]*=10**(gain/20)


def correct(x,sr,check):
    sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'_shell/narrate'))
    import measure
    edits=[];summary=check['joins']['summary']
    level_limit=(summary.get('level_step_db',{}).get('natural') or {}).get('max',5)
    floor_limit=max(3.,(summary.get('floor_step_db',{}).get('natural') or {}).get('max',0))
    for row in check['joins']['boundaries']:
        if not row.get('artefacts'):continue
        a,b=row.get('gap') or [row['t'],row['t']];middle=(a+b)/2
        if (row.get('level_step_db') or 0)>level_limit+1:
            before=measure._speech_db(x[max(0,round((a-1.5)*sr)):round(a*sr)],sr)
            after=measure._speech_db(x[round(b*sr):round((b+1.5)*sr)],sr)
            delta=min(3.,max(0.,abs(after-before)-max(2.,level_limit-1.)))
            if after>before:times=[middle-.025,b+.025,b+1.5,b+2.3]
            else:times=[a-2.3,a-1.5,a-.025,middle+.025]
            ride(x,sr,times,[0,-delta,-delta,0]);edits.append(dict(kind='speech_level',times=times,db=-delta))
        if row.get('floor_db') and (row.get('floor_step_db') or 0)>floor_limit and b-a>=.2:
            left,right=row['floor_db'];target=min(-54.,max(left,right))
            gains=[float(np.clip(target-left,-6,6)),float(np.clip(target-right,-6,6))]
            # Equalize the existing quiet floor, preserving every sample and
            # all phonemes. Raised floor stays at least 40 dB below the voice.
            times=[a,a+.02,middle-.02,middle+.02,b-.02,b]
            ride(x,sr,times,[0,gains[0],gains[0],gains[1],gains[1],0])
            edits.append(dict(kind='pause_floor',times=times,db=gains))
    return edits


def repair(out):
    import soundfile as sf
    out=Path(out);audio=out/'narration.wav';check=json.loads((out/'check.json').read_text())
    signature=hashlib.sha256(audio.read_bytes()).hexdigest();evidence=out/'join-repair'/signature[:16];evidence.mkdir(parents=True,exist_ok=True)
    shutil.copy2(audio,evidence/'before.wav');shutil.copy2(out/'check.json',evidence/'check-before.json')
    x,sr=sf.read(audio,dtype='float32');edits=correct(x,sr,check)
    if not edits:raise ValueError('No bounded level repair applies; narration requires a new take')
    sf.write(audio,x,sr,subtype='PCM_24');seconds=len(x)/sr;del x
    (evidence/'edits.json').write_text(json.dumps(dict(seconds_unchanged=seconds,edits=edits),indent=1))
    rep=json.loads((out/'render.json').read_text());rep['duration_s']=seconds
    (out/'render.json').write_text(json.dumps(rep,indent=1));(out/'check.json').unlink()
    # A fresh unprompted VO2 listen/pitch pass must still pass after the repair.
    subprocess.run([sys.executable,str(Path(__file__).with_name('measure_voice.py')),str(audio),str(out/'narration_script.json'),str(out/'longform-metrics')],check=True)
    after=json.loads((out/'longform-metrics/metrics.json').read_text())
    result=json.loads((out/'longform_voice.json').read_text());result['after']=after;result['join_repair']=str(evidence)
    (out/'longform_voice.json').write_text(json.dumps(result,indent=1))
    if not after['ok']:raise ValueError('VO2 failed after join-level repair')


if __name__=='__main__':repair(sys.argv[1])
