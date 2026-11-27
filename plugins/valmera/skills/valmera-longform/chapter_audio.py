"""One stem-aware SND3/MUSIC2 master, preserving the verified narration."""
from pathlib import Path
import json
import numpy as np


def editorial_cues(sounds, landings, fps):
    """Use a single authored accent on an actual change at a story turn."""
    if landings is None:
        return sounds
    result = {}
    for frame in landings:
        options = [s for s in sounds if abs(s['event_f']-frame) <= 2 and s.get('layer') != 'lead']
        if options:
            cue = min(options, key=lambda s: abs(s['event_f']-frame))
            result[cue['event_f']] = cue
    return list(result.values())


def speech_room(sounds, voice, effects, sr, margin=7):
    """Measure the rendered cue against speech over coincident 50 ms windows."""
    hop = sr//20
    for cue in sounds:
        start = max(0,round(cue['start_s']*sr))
        stop = min(len(voice),len(effects),start+round((cue.get('dur_s') or 1)*sr))
        n = (stop-start)//hop
        if n <= 0:
            continue
        def power(stem):
            return np.mean(stem[start:start+n*hop].reshape(n,hop,-1)**2,axis=(1,2))
        v,fx = power(voice),power(effects)
        spoken = v > 10**(-38/10)
        if not spoken.any():
            continue
        excess = float(10*np.log10(np.max(fx[spoken]/v[spoken])+1e-12))
        reduction = max(0.,excess+margin)
        effects[start:stop] *= 10**(-reduction/20)
        cue.update(db=cue['db']-reduction,over_voice_db=round(excess-reduction,2),
                   measured_reduction_db=round(reduction,2))
        for field in ('over_bed_db','heard_db'):
            if field in cue:
                cue[field] -= reduction


def mix(l1,voice,bed,film,out):
    from scene import audio as AU
    from score import sound as SD,music as MU
    from stem_evidence import write_audio
    root=Path(out).parent;tmp=root/'chapter-master';tmp.mkdir(exist_ok=True)
    # MUSIC2 now sees the levelled voice alone. SND3 gets the actual score,
    # which the former finished-film mixer could not provide to either engine.
    selected = editorial_cues(l1['sound'], l1.get('evidence_changes'), l1['fps'])
    sounds = SD.present_over(selected, MU.to_ar(voice), MU.to_ar(bed), l1['fps'])
    plan=MU._Plan(dict(l1,spine=[],sound=sounds),None)
    silence,fx,render=AU.split_audio(plan);del silence
    ducks=getattr(fx,'ducks',None);duck_report=None
    if ducks:
        gain,duck_report=AU.duck_curve(bed,fx,ducks,len(fx))
        bed=bed*gain[:,None]
    effects=np.asarray(fx)
    if 'evidence_changes' in l1:
        speech_room(sounds,voice,effects,AU.SR)
    clean=voice+effects
    stems=root/'audio-stems'
    write_audio(stems/'voice.flac',voice,AU.SR,AU.FF)
    write_audio(stems/'sfx.flac',effects,AU.SR,AU.FF)
    write_audio(root/'clean-final.flac',clean,AU.SR,AU.FF)
    write_audio(Path(out).with_suffix('.bed.wav'),bed,AU.SR,AU.FF)
    combined=clean+bed
    audio=tmp/'scored.m4a'
    # The voice has already passed the shared leveler. Master the three
    # lossless stems once; never remaster an AAC voice+SFX submix as narration.
    report=AU.master(None,combined,str(audio),str(tmp),l1['dur_f']/l1['fps'])
    AU.mux(film,str(audio),out)
    result=dict(master=report,render=render,ducks=duck_report,final_sounds=sounds,
                lifted_for_bed=sum(bool(s.get('lifted_for_bed_db')) for s in sounds),
                method='SND3 presence against actual MUSIC2 bed, hero-hit bed ducking, one master of lossless stems')
    (root/'sound-final.json').write_text(json.dumps(result,indent=1))
    return result
