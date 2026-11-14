"""Unprompted word error on the delivered mix, excluding original-voice inserts.
The music and effects stay present. Pitch and pause statistics use the retained
narrator stem because a mixed soundtrack cannot measure a narrator's F0.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'_shell'/'narrate'))


def check(film, words, script, out, ffmpeg):
    import soundfile as sf
    import narrate as N
    import measure as M
    import spoken
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    wj=json.loads(Path(words).read_text());sj=json.loads(Path(script).read_text())
    expected=' '.join(s['text'] for s in sj['segments'])
    with open(film,'rb') as f:stamp=hashlib.file_digest(f,'sha256')
    stamp.update(Path(words).read_bytes());fingerprint=stamp.hexdigest()
    rp=out/'mix-wer.json'
    if rp.exists():
        r=json.loads(rp.read_text())
        if r.get('input_sha256')==fingerprint:return r
    wav=out/'delivered-mix.wav'
    subprocess.run([ffmpeg,'-v','error','-y','-i',film,'-vn','-ac','1','-ar','16000',str(wav)],check=True)
    x,sr=sf.read(wav,dtype='float32');wav.unlink()
    start=.1;end=start+wj['duration'];ranges=[]
    for v in sorted(wj.get('voices',[]),key=lambda v:v['start']):
        a,b=v['start']+.1,v['end']+.1
        if a>start:ranges.append((start,a))
        start=b
    if end>start:ranges.append((start,end))
    y=np.concatenate([x[round(a*sr):round(b*sr)] for a,b in ranges])
    N.VOCAB[:]=[];heard,heard_words=N.listen_long(y,sr)
    raw=M.wer(expected,heard)
    normalized=spoken.spoken_form(heard,expected,[])
    scored=M.wer(expected,normalized['text']) if normalized.get('ok') else raw
    r=dict(input_sha256=fingerprint,wer=scored['wer'],wer_raw=raw['wer'],errors=scored,heard=heard,
           words=heard_words,original_voice_inserts_excluded=len(wj.get('voices',[])),
           method='unprompted whisper large-v3 on final encoded film mix; music and SFX retained; original voice intervals excluded',
           ok=scored['wer']<.01)
    rp.write_text(json.dumps(r,indent=1));return r


if __name__=='__main__':
    a=argparse.ArgumentParser()
    for name in ('film','words','script','out','ffmpeg'):a.add_argument(name)
    j=a.parse_args();print(json.dumps(check(**vars(j))))
