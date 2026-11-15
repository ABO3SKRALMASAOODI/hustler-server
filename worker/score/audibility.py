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


def verified_mix(voice, sfx, bed, encoded):
    """The rater's stem fit against decoded audio, followed by its T1/T2 ruler.

    Planned gains cannot certify audibility after limiting and AAC. Search the
    same +/-50 ms alignment at 1 ms steps, fitting all three retained stems.
    """
    v,s,b,m = [mono(x) for x in (voice,sfx,bed,encoded)]
    n=min(map(len,(v,s,b,m)));best=None
    if n<=4800:raise ValueError('not enough encoded audio to verify stems')
    for lag in range(-2400,2401,48):
        a0=max(0,lag);target=m[a0:a0+n-2400]
        X=np.stack([x[a0-lag:a0-lag+len(target)] for x in (v,s,b)],1)
        coef,*_=np.linalg.lstsq(X,target,rcond=None)
        residual=target-X@coef
        q=10*np.log10(max(np.mean(residual**2),1e-20)/max(np.mean(target**2),1e-20))
        if best is None or q<best[0]:best=(q,lag,coef)
    q,lag,coef=best
    report=measure(v[:n]*coef[0],s[:n]*coef[1],b[:n]*coef[2])
    hits=[e['db'] for e in report['events'] if e['T2']]
    report.update(fit_residual_db=float(q),lag_ms=lag*1000/SR,gains=coef.tolist(),
                  seconds=n/SR,T2_per_10s=report['T2']*10/(n/SR),
                  audible_range_db=float(np.ptp(hits)) if hits else 0.,
                  audible_iqr_db=float(np.percentile(hits,75)-np.percentile(hits,25)) if hits else 0.)
    return report


def recording_alternatives(item):
    """Nearby approved recordings of the same gesture, with the original landing preserved."""
    from . import sound
    sf = sound.SF()
    old = sf.row(item.get('s'), sound.SOUNDS)
    if not old:
        return []
    result = []
    for _distance, sid in sf.ranking(item['s'], sound.SOUNDS):
        new = sf.row(sid, sound.SOUNDS)
        if new.get('role') != old.get('role') or (new.get('family') and new.get('family') != old.get('family')):
            continue
        peak = float(item.get('peak_s') or 0.)
        hit = float(item['start_s'])-float(item.get('trim_s') or 0.)+peak
        new_peak = float(new.get('hit_s') or new.get('peak_s') or 0.) + peak-float(old.get('hit_s') or old.get('peak_s') or 0.)
        start = hit-new_peak
        candidate = dict(item, s=sid, file=new['file'], start_s=max(0.,start), trim_s=max(0.,-start), peak_s=new_peak,
                         recording_substitution=dict(original=item['s'], reason='original attack cannot clear the bed within sample headroom'))
        candidate.pop('dur_s',None)
        if new.get('max_s'):
            candidate['dur_s'] = max(.05,float(new['max_s'])-candidate['trim_s'])
        result.append(candidate)
        if len(result)==4:
            break
    return result


def present_hits(sounds, voice, bed, render, alternatives=None):
    """Make already-earned attacks audible; no added events, repeated ticks or quota filling.

    render(items) supplies the renderer's actual SFX stem. Broad transient gain
    is limited by nearby voice peaks and sample headroom; unresolved events stay
    failures in the measured report rather than fabricated audible counts.
    """
    out = [dict(s) for s in sounds]
    n = min(len(voice),len(bed))
    v,b = mono(voice[:n]),mono(bed[:n])
    lb,lvb,lv = levels(b),levels(v+b),levels(v)
    speech_ceiling = float(np.percentile(lv,90))+3.5 if len(lv) else -20.
    surroundings = mono(render(out))[:n]+b
    for position,it in enumerate(out):
        if it.get('layer')!='hit' or it.get('start_s') is None: continue
        own = mono(render([it]))[:n]
        lo = max(0,int(float(it['start_s'])*100)-3)
        # The recording can start well before its actual attack (a whoosh or
        # impact with a long lead). Inspect through that measured attack.
        peak_s = float(it.get('peak_s') or 0)-float(it.get('trim_s') or 0)
        hi = min(len(lb),max(lo+50,int((float(it['start_s'])+max(0.,peak_s)+.15)*100)))
        if hi<=lo: continue
        ls = levels(own)
        i = lo+int(np.argmax(ls[lo:hi]))
        # The teardown compares against the entire non-vocal track, including
        # neighbouring effects' tails, not the music stem in isolation.
        ln = levels(surroundings)
        med = np.median(ln[max(0,i-100):min(len(ln),i+100)])
        # The final master attenuates short effects more than the bed. Leave
        # real transient headroom, and clear the preceding effects' tail as
        # well as the two-second baseline. Otherwise a nominally loud tick
        # following a whoosh never becomes a distinct audible onset.
        prior = levels(surroundings-b)
        pre = 10*np.log10(np.mean(10**(prior[max(0,i-30):max(1,i-2)]/10))+1e-12)
        # Broad body gestures need less lift than tiny clicks; forcing both to
        # one floor erased the recording hierarchy. Accents keep their contrast.
        presence = {'body':8.,'tick':9.,'accent':12.,'hero':15.}.get(it.get('tier'),9.)
        target = max(med+presence,lvb[i]-3.,pre+8.)
        # A short attack may rise above speech, but never become a sustained competing voice.
        # A pause has no local speech peak: it must not force the effect below
        # a still-playing bed. Bound attacks against the film's spoken level.
        ceiling = speech_ceiling
        peak = np.max(np.abs(own))
        headroom = 20*np.log10(.7/max(peak,1e-9))
        lift = max(0.,min(target-ls[i],ceiling-ls[i],headroom,24.))
        original = own
        if alternatives and ls[i]+lift < min(target,ceiling)-.5:
            # A needle-like click can hit the sample ceiling yet remain inaudible
            # over a 50 ms window. Choose a fuller recording of that same gesture;
            # never invent another event or clip the original into audibility.
            for candidate in alternatives(it):
                sample = mono(render([candidate]))[:n]
                measured = levels(sample)
                peak_i = lo+int(np.argmax(measured[lo:hi]))
                room = 20*np.log10(.7/max(np.max(np.abs(sample)),1e-9))
                # The old recording may already carry a large gain. A fuller
                # replacement must be allowed to turn DOWN before it is tested;
                # otherwise copying that gain overloads the master and masks
                # neighboring attacks despite the new sample's headroom limit.
                gain = min(target-measured[peak_i],ceiling-measured[peak_i],room,24.)
                if measured[peak_i]+gain >= min(target,ceiling)-.5:
                    it,own,lift = candidate,sample,gain
                    out[position] = it
                    break
        it['db'] = round(float(it['db'])+lift,2)
        it['broadband_lift_db'] = round(lift,2)
        surroundings += own*10**(lift/20)-original
    return out
