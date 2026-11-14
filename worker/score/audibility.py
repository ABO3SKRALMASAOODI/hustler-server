"""Broadband final-mix ruler, matching the metromedia teardown (50 ms / 10 ms).

This is distinct from spectral masking: a sound can clear one narrow band and
still disappear in the whole mix. Never count planned events as audible onsets.
"""
import numpy as np

SR = 48000


def mono(x):
    x = np.asarray(x, np.float64)
    return x.mean(axis=1) if x.ndim == 2 else x


def levels(x):
    x = mono(x)
    n, h = 2400, 480
    k = max(0, 1+(len(x)-n)//h)
    c = np.concatenate([[0], np.cumsum(x*x)])
    at = np.arange(k)*h
    return 10*np.log10((c[at+n]-c[at])/n+1e-12)


def measure(voice, sfx, bed):
    n = min(len(voice),len(sfx),len(bed))
    v,s,b = [mono(x[:n]) for x in (voice,sfx,bed)]
    lv,ls,lb,lvb,lnv = [levels(x) for x in (v,s,b,v+b,s+b)]
    on, events = [], []
    for i in range(30,len(ls)-3):
        pre = 10*np.log10(np.mean(10**(ls[i-30:i-2]/10))+1e-12)
        if ls[i]<-60 or ls[i]-pre<6 or ls[i]!=ls[max(0,i-8):i+8].max(): continue
        if on and i-on[-1]<12: continue
        on.append(i)
        med = float(np.median(lnv[max(0,i-100):min(len(lnv),i+100)]))
        t1 = bool(ls[i]-med>=6)
        events.append(dict(t=round(i*.01+.025,3),db=float(ls[i]),over_bed=float(ls[i]-med),
                           over_mix=float(ls[i]-lvb[i]),T1=t1,T2=bool(t1 and ls[i]>=lvb[i]-6)))
    voiced = lv > np.percentile(lv,90)-18 if len(lv) else np.array([],bool)
    return dict(events=events, T1=sum(e['T1'] for e in events),T2=sum(e['T2'] for e in events),
                bed_under_voice_db=float(np.median((lv-lb)[voiced])) if voiced.any() else None)


def calibrate_bed(voice, bed, target=5.5):
    """One global gain after EQ, preserving the song's dynamics and payoff hole."""
    n = min(len(voice),len(bed))
    lv,lb = levels(voice[:n]),levels(bed[:n])
    active = lv>np.percentile(lv,90)-18
    delta = float(np.median((lv-lb)[active]))-target if active.any() else 0.
    return bed*(10**(delta/20)), delta


def present_hits(sounds, voice, bed, render):
    """Make already-earned attacks audible; no added events, repeated ticks or quota filling.

    render(items) supplies the renderer's actual SFX stem. Broad transient gain
    is limited by nearby voice peaks and sample headroom; unresolved events stay
    failures in the measured report rather than fabricated audible counts.
    """
    out = [dict(s) for s in sounds]
    n = min(len(voice),len(bed))
    v,b = mono(voice[:n]),mono(bed[:n])
    lb,lvb,lv = levels(b),levels(v+b),levels(v)
    for it in out:
        if it.get('layer')!='hit' or it.get('start_s') is None: continue
        own = mono(render([it]))[:n]
        lo = max(0,int(float(it['start_s'])*100)-3)
        hi = min(len(lb),lo+50)
        if hi<=lo: continue
        ls = levels(own)
        i = lo+int(np.argmax(ls[lo:hi]))
        med = np.median(lb[max(0,i-100):min(len(lb),i+100)])
        target = max(med+7.,lvb[i]-5.)
        # A short attack may rise above speech, but never become a sustained competing voice.
        ceiling = np.percentile(lv[max(0,i-100):min(len(lv),i+100)],90)+3.5
        peak = np.max(np.abs(own))
        headroom = 20*np.log10(.7/max(peak,1e-9))
        lift = max(0.,min(target-ls[i],ceiling-ls[i],headroom,12.))
        it['db'] = round(float(it['db'])+lift,2)
        it['broadband_lift_db'] = round(lift,2)
    return out
