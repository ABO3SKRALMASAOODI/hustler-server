"""Final-mix signals using the independent WF1-r4 rater's audio method.
Source: week-1112/lanes/RATINGS/work1114WF1-r4/audio.py.
The same 32 kHz, 50 ms/10 ms windows, gain-fitted clean subtraction and
6 dB audibility tests run in bounded batches for a full documentary.
"""
import argparse,hashlib,json,subprocess
from pathlib import Path
import numpy as np

SR=32000

def pcm(path,ffmpeg):
    raw=subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(path),'-map','0:a:0','-ac','1','-ar',str(SR),'-f','f32le','-'],capture_output=True,check=True).stdout
    return np.frombuffer(raw,np.float32).astype(np.float64)

def lev(x):return 10*np.log10(np.mean(x*x)+1e-12) if len(x) else -120.

def levels(x,band=None):
    n,h=int(.05*SR),int(.01*SR);k=max(0,1+(len(x)-n)//h)
    out=np.empty(k);window=np.hanning(n)
    freq=np.fft.rfftfreq(n,1/SR)
    mask=(freq>=band[0])&(freq<band[1]) if band else None
    for a in range(0,k,1000):
        b=min(k,a+1000);idx=np.arange(n)[None,:]+h*np.arange(a,b)[:,None]
        y=x[idx]
        if band:
            p=np.abs(np.fft.rfft(y*window,axis=1))**2
            out[a:b]=10*np.log10(p[:,mask].sum(1)+1e-12)
        else:out[a:b]=10*np.log10((y*y).mean(1)+1e-12)
    return out

def windows(x,seconds=.1):
    n=int(seconds*SR);k=len(x)//n
    return 10*np.log10((x[:k*n].reshape(k,n)**2).mean(1)+1e-12)

def measure(final,clean,plan,words,out,ffmpeg,stem_dir=None,music_stem=None):
    if not stem_dir or not music_stem:raise ValueError('actual mixer stems are required for the rater audibility test')
    stem_files=[Path(stem_dir)/'voice.flac',Path(stem_dir)/'sfx.flac',Path(music_stem)]
    fingerprint=hashlib.sha256(Path(__file__).read_bytes())
    for source in (final,clean,plan,words,Path(__file__).with_name('stem_evidence.py'),*stem_files):
        with open(source,'rb') as f:fingerprint.update(hashlib.file_digest(f,'sha256').digest())
    signature=fingerprint.hexdigest()
    if Path(out).exists():
        prior=json.loads(Path(out).read_text())
        if prior.get('input_sha256')==signature:return prior
    plan=json.loads(Path(plan).read_text());wj=json.loads(Path(words).read_text())
    dur=plan['dur_f']/plan['fps'];P=round(dur*SR)
    final_pcm=pcm(final,ffmpeg);film_seconds=len(final_pcm)/SR
    m,c=final_pcm[:P],pcm(clean,ffmpeg)[:P]
    L=min(len(m),len(c));m,c=m[:L],c[:L]
    gain=float(np.dot(m,c)/(np.dot(c,c)+1e-12));cc=gain*c;bed=m-cc
    vc,bd=windows(cc),windows(bed);on=bd>-50
    voiced=vc>np.percentile(vc,90)-18
    under=float(np.median(vc[voiced&on]-bd[voiced&on])) if (voiced&on).any() else None
    chapters=[]
    for ch in plan['chapters']:
        x=bed[round(ch['start']*SR):round(ch['end']*SR)];db=windows(x,.5)
        rises=[(float(db[i]-db[i-k]),ch['start']+i*.5,k*.5) for k in range(4,17) for i in range(k,len(db)) if db[i-k]>-60 and db[i]>-50]
        rise=max(rises,default=(0,ch['start'],0))
        chapters.append(dict(chapter=ch['id'],rise_db=round(rise[0],2),at_s=rise[1],span_s=rise[2]))
    sd=plan['sound_design']
    evs=sorted([e for kind in ('placed','body','ticks') for e in sd.get(kind,[])],key=lambda e:e['t'])
    fl,cl=levels(m),levels(cc)
    placed=[]
    def step(ls,x,t):
        a,b=round(max(t-.5,0)*SR),round(max(t-.03,0)*SR)
        pre=lev(x[a:b]) if b-a>800 else -90
        lo=int(max(t-.03,0)/.01);hi=min(int((t+.3)/.01),len(ls)-1)
        pk=float(ls[lo:hi+1].max()) if hi>=lo else -90
        return pk-pre,pk
    for e in evs:
        t=e['t']
        if t>=dur:continue
        sf,pk=step(fl,m,t);sc,_=step(cl,cc,t)
        lo,hi=int(max(t-1.5,0)/.01),int(min(t+1.5,dur)/.01)
        vp=float(np.percentile(cl[lo:hi],90))
        placed.append(dict(t=t,final_step_db=round(sf,1),clean_step_db=round(sc,1),peak_vs_voice_p90_db=round(pk-vp,1)))
    starts=[w['start']+.1 for i,w in enumerate(wj['words']) if i==0 or w['start']-wj['words'][i-1]['end']>=.12]
    testimony=[(v['start']+.1,v['end']+.1) for v in wj.get('voices',[])]
    events=[]
    for name,band in [('full',None),('low',(30,90)),('high',(7000,14000))]:
        lm,lc=(fl,cl) if band is None else (levels(m,band),levels(cc,band))
        for i in range(50,len(lm)-5):
            pre=10*np.log10(np.mean(10**(lm[i-50:i-3]/10))+1e-12)
            if lm[i]-pre<(6 if name=='full' else 9) or lm[i]!=lm[max(0,i-10):i+10].max():continue
            t=i*.01+.025
            pc=10*np.log10(np.mean(10**(lc[i-50:i-3]/10))+1e-12)
            if lc[i]-pc<6 or any(-.06<=t-s<=.15 for s in starts) or any(a<=t<=b for a,b in testimony):continue
            events.append((round(t,2),name))
    merged=[]
    for t,name in sorted(events):
        if merged and t-merged[-1][0]<.25:merged[-1][1].add(name)
        else:merged.append([t,{name}])
    audible=[r for r in placed if r['final_step_db']>=6]
    r=dict(input_sha256=signature,programme_s=dur,film_s=film_seconds,gain_fit=gain,music_share=float(on.sum()*.1/film_seconds),programme_music_share=float(on.mean()),music_under_voice_db=under,
           chapter_rises=chapters,placed=len(placed),audible_placed=len(audible),audible_placed_per_10s=len(audible)*10/dur,
           blind_events=len(merged),blind_per_10s=len(merged)*10/dur,placed_measurements=placed,
           blind=[dict(t=t,bands=sorted(ns)) for t,ns in merged],
           method='Independent WF1-r4 audio rater: final minus gain-fitted clean; bed 100ms RMS > -50 dBFS, verified programme bed divided by full film duration (tail excluded conservatively); audible final-mix 50ms/10ms transient >=6 dB over preceding .5s; blind events present in clean, speech starts excluded',
           rater_source='week-1112/lanes/RATINGS/work1114WF1-r4/audio.py')
    from stem_evidence import measure as measured_stems
    strict=measured_stems(final,*stem_files,plan,ffmpeg)
    r['residual_music_share']=r['music_share'];r['residual_chapter_rises']=r['chapter_rises']
    r['diagnostic_method']=r['method'];r['diagnostic_source']=r['rater_source']
    r['method']=strict['method'];r['rater_source']='week-1112/lanes/RATINGS/work1114WF1-r4/stems.py'
    r.update(stems=strict,music_share=strict['music_share'],chapter_rises=strict['chapter_rises'],
             music_under_voice_db=strict['bed_under_voice_db'],verified=strict['verified'])
    Path(out).write_text(json.dumps(r,indent=1));return r

if __name__=='__main__':
    a=argparse.ArgumentParser()
    for key in ('final','clean','plan','words','out','ffmpeg'):a.add_argument(key)
    a.add_argument('--stems',dest='stem_dir',required=True);a.add_argument('--bed',dest='music_stem',required=True)
    print(json.dumps(measure(**vars(a.parse_args()))))
