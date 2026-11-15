"""One stem-aware SND3/MUSIC2 master, preserving the verified narration."""
from pathlib import Path
import json
import numpy as np


def mix(l1,voice,bed,film,out):
    from scene import audio as AU
    from score import sound as SD,music as MU
    from stem_evidence import write_audio
    root=Path(out).parent;tmp=root/'chapter-master';tmp.mkdir(exist_ok=True)
    # MUSIC2 now sees the levelled voice alone. SND3 gets the actual score,
    # which the former finished-film mixer could not provide to either engine.
    sounds=SD.present_over(l1['sound'],MU.to_ar(voice),MU.to_ar(bed),l1['fps'])
    plan=MU._Plan(dict(l1,spine=[],sound=sounds),None)
    silence,fx,render=AU.split_audio(plan);del silence
    ducks=getattr(fx,'ducks',None);duck_report=None
    if ducks:
        gain,duck_report=AU.duck_curve(bed,fx,ducks,len(fx))
        bed=bed*gain[:,None]
    effects=np.asarray(fx);clean=voice+effects
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
