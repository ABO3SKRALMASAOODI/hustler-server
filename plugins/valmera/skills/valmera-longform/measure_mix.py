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


def listen_mix(mixed, boundary_voice, sr):
    """Find real speech pauses without cutting words where the bed masks silence.

    Only boundary selection sees the aligned voice stem. The unprompted ASR
    receives every mixed sample, in order, with music and effects unchanged.
    """
    import narrate as N
    if len(mixed)!=len(boundary_voice):
        raise ValueError('mixed audio and boundary voice must share the same clock')
    cursor=0;windows=[]
    def hear(chunk, rate):
        nonlocal cursor
        stop=cursor+len(chunk)
        windows.append([cursor/sr,stop/sr])
        result=N.listen(mixed[cursor:stop],rate)
        cursor=stop
        return result
    text,words=N.listen_long(boundary_voice,sr,fn=hear)
    if cursor!=len(mixed):raise ValueError('listener did not cover the complete mix')
    return text,words,windows


def check(film, words, script, out, ffmpeg, voice):
    import soundfile as sf
    import narrate as N
    import measure as M
    import spoken
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    wj=json.loads(Path(words).read_text());sj=json.loads(Path(script).read_text())
    expected=' '.join(s['text'] for s in sj['segments'])
    with open(film,'rb') as f:stamp=hashlib.file_digest(f,'sha256')
    for path in (words,script,voice,__file__):stamp.update(Path(path).read_bytes())
    fingerprint=stamp.hexdigest()
    rp=out/'mix-wer.json'
    if rp.exists():
        r=json.loads(rp.read_text())
        if r.get('input_sha256')==fingerprint:return r
        history=out/'mix-listens';history.mkdir(exist_ok=True)
        (history/(r['input_sha256'][:16]+'.json')).write_bytes(rp.read_bytes())
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
    clean=np.frombuffer(subprocess.check_output([ffmpeg,'-v','error','-i',str(voice),
        '-vn','-ac','1','-ar',str(sr),'-f','f32le','-']),dtype='<f4')
    boundary=np.concatenate([clean[round(a*sr):round(b*sr)] for a,b in ranges])
    N.VOCAB[:]=[]
    try:heard,heard_words,windows=listen_mix(y,boundary,sr)
    finally:N.free_gate_listener();N.free_take_listener()
    raw=M.wer(expected,heard)
    normalized=spoken.spoken_form(heard,expected,[])
    scored=M.wer(expected,normalized['text']) if normalized.get('ok') else raw
    r=dict(input_sha256=fingerprint,wer=scored['wer'],wer_raw=raw['wer'],errors=scored,heard=heard,
           words=heard_words,original_voice_inserts_excluded=len(wj.get('voices',[])),
           method='unprompted whisper large-v3 on final encoded film mix; music and SFX retained; original voice intervals excluded; windows cut at aligned voice-stem silences, never mid-word because of the bed',
           windows=windows,boundary_voice_sha256=hashlib.sha256(Path(voice).read_bytes()).hexdigest(),
           ok=scored['wer']<.01)
    rp.write_text(json.dumps(r,indent=1));return r


if __name__=='__main__':
    a=argparse.ArgumentParser()
    for name in ('film','words','script','out','ffmpeg'):a.add_argument(name)
    a.add_argument('--voice',required=True,help='Aligned voice stem, used only to locate speech pauses')
    j=a.parse_args();print(json.dumps(check(**vars(j))))
