"""Capture the actual mixer inputs and use the independent WF1-r4 stem ruler.

The builder does not decide which planned effects count. Blind stem onsets only
count after a least-squares fit to the final encoded film, then the rater's T1
and T2 audibility tests. All 101 delay candidates use the full 48 kHz programme.
"""
from pathlib import Path
import subprocess
import numpy as np

SR=48000


def write_audio(path,x,sr,ffmpeg):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=path.with_suffix('.f32')
    np.asarray(x,np.float32).tofile(raw)
    try:
        subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-f','f32le','-ar',str(sr),
                        '-ac',str(x.shape[1] if x.ndim==2 else 1),'-i',str(raw),
                        '-c:a','flac' if path.suffix=='.flac' else 'pcm_s24le',str(path)],check=True)
    finally:raw.unlink(missing_ok=True)


def capture(mixer,folder):
    """Instrument this private mixer process; do not change its audio."""
    folder=Path(folder)
    original_level,original_master=mixer.level_voice,mixer.master
    def level(*args,**kwargs):
        voice,report=original_level(*args,**kwargs)
        write_audio(folder/'voice.flac',voice,mixer.SR,mixer.FF)
        return voice,report
    def master(voice,fx,*args,**kwargs):
        write_audio(folder/'sfx.flac',np.asarray(fx),mixer.SR,mixer.FF)
        return original_master(voice,fx,*args,**kwargs)
    mixer.level_voice,mixer.master=level,master


def pcm(path,ffmpeg):
    raw=subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(path),'-map','0:a:0',
                        '-ac','1','-ar',str(SR),'-f','f32le','-'],capture_output=True,check=True).stdout
    return np.frombuffer(raw,np.float32).astype(np.float64)


def lv(x,win=.05,hop=.01):
    n,h=int(win*SR),int(hop*SR);k=1+(len(x)-n)//h
    cumulative=np.concatenate([[0],np.cumsum(x*x)])
    starts=np.arange(k)*h
    return 10*np.log10((cumulative[starts+n]-cumulative[starts])/n+1e-12)


def fitted(m,stems,P):
    """The rater's full-sample least squares, accumulated in bounded blocks.
    Normal equations avoid allocating a whole-film N-by-3 matrix 101 times.
    """
    best=None
    for lag in range(-2400,2401,48):
        a0=max(0,lag);length=P-2400
        gram=np.zeros((3,3));cross=np.zeros(3);power=0.
        for pos in range(0,length,480000):
            n=min(480000,length-pos)
            y=m[a0+pos:a0+pos+n]
            x=np.column_stack([s[a0-lag+pos:a0-lag+pos+n] for s in stems])
            gram+=x.T@x;cross+=x.T@y;power+=float(y@y)
        coef=np.linalg.lstsq(gram,cross,rcond=None)[0]
        error=max(1e-20,power-2*float(coef@cross)+float(coef@gram@coef))
        q=10*np.log10(error/max(power,1e-20))
        if best is None or q<best[0]:best=(float(q),lag,coef)
    return best


def audible(v,s,bed):
    Lv,Ls,Lb,Lvb=lv(v),lv(s),lv(bed),lv(v+bed)
    Lnv=lv(s+bed);on=[]
    for i in range(30,len(Ls)-3):
        if Ls[i]<-60:continue
        pre=10*np.log10(np.mean(10**(Ls[i-30:i-2]/10))+1e-12)
        if Ls[i]-pre>=6 and Ls[i]==Ls[max(0,i-8):i+8].max():
            if on and i-on[-1]<12:continue
            on.append(i)
    events=[]
    for i in on:
        lo,hi=max(0,i-100),min(len(Lnv),i+100)
        median=float(np.median(Lnv[lo:hi]));t1=Ls[i]-median>=6
        events.append(dict(t=round(i*.01+.025,2),sfx_db=round(float(Ls[i]),1),
                           over_bed2s_db=round(float(Ls[i]-median),1),
                           vs_concurrent_voice_bed_db=round(float(Ls[i]-Lvb[i]),1),
                           vs_voice_p90_db=round(float(Ls[i]-np.percentile(Lv[lo:hi],90)),1),
                           T1=bool(t1),T2=bool(t1 and Ls[i]>=Lvb[i]-6)))
    voiced=Lv>np.percentile(Lv,90)-18
    return events,float(np.median((Lv-Lb)[voiced]))


def measure(final,voice,sfx,bed,plan,ffmpeg):
    m=pcm(final,ffmpeg);stems=[pcm(p,ffmpeg) for p in (voice,sfx,bed)]
    dur=plan['dur_f']/plan['fps'];P=min(round(dur*SR),len(m),*(len(s) for s in stems))
    q,lag,coef=fitted(m,stems,P)
    # The independent rater accepted fits of -12 to -17 dB. A worse fit cannot
    # establish what a viewer heard, so retain the diagnostics and fail closed.
    verified=q<=-12 and bool(np.all(coef>0))
    v,s,b=[c*x[:P] for c,x in zip(coef,stems)]
    events,under=audible(v,s,b)
    n1=sum(e['T1'] for e in events);n2=sum(e['T2'] for e in events)
    def rms_windows(x,seconds):
        n=int(seconds*SR);k=len(x)//n
        return 10*np.log10((x[:k*n].reshape(k,n)**2).mean(1)+1e-12)
    music=rms_windows(b,.1);rises=[]
    for ch in plan['chapters']:
        db=rms_windows(b[round(ch['start']*SR):round(ch['end']*SR)],.5)
        candidates=[(float(db[i]-db[i-k]),ch['start']+i*.5,k*.5) for k in range(4,17)
                    for i in range(k,len(db)) if db[i-k]>-60 and db[i]>-50]
        rise=max(candidates,default=(0,ch['start'],0))
        rises.append(dict(chapter=ch['id'],rise_db=round(rise[0],2),at_s=rise[1]+lag/SR,span_s=rise[2]))
    return dict(verified=verified,fit_residual_db=q,lag_ms=1000*lag/SR,gains=coef.tolist(),
                sfx_onsets=len(events),T1=n1,T1_per_10s=10*n1/dur,T2=n2,T2_per_10s=10*n2/dur,
                bed_under_voice_db=under,music_share=float(np.sum(music>-50)*.1/(len(m)/SR)),
                chapter_rises=rises,events=events,
                method='WF1-r4 stems.py: full 48kHz least-squares fit, -50..50ms delay; blind 50ms/10ms SFX onsets; T1 >=6dB over 2s non-vocal median; T2 also within 6dB of concurrent voice+bed',
                rater_source='week-1112/lanes/RATINGS/work1114WF1-r4/stems.py')
