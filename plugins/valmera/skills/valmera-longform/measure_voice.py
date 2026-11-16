"""Signal-only VO2-compatible measures; the ASR listener receives no name prompt."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'_shell'/'narrate'))

# Voiced p95-p5 pitch range of the long-form narrators (ref_band.json band 04, p10..p90 of 20 long-form reference films).
# Until 16 Nov this was the metromedia reels' 13.79-15.33 st, which Easton reaches only by resynthesising its contour.
PITCH_BAND=tuple(json.loads((Path(__file__).resolve().parent.parent/'_shell'/'narrate'/'ref_band.json').read_text())
                 ['band']['04']['f0_range_st'][k] for k in ('p10','p90'))


def measure(path, expected, cache):
    import librosa
    import soundfile as sf
    import narrate as N
    import measure as M
    import spoken
    cache=Path(cache); cache.mkdir(parents=True,exist_ok=True)
    fingerprint=hashlib.sha256(Path(path).read_bytes()+expected.encode()).hexdigest()
    stamp=cache/'input.sha256'
    if not stamp.exists() or stamp.read_text()!=fingerprint:
        for name in ('heard.json','f0.npy','metrics.json'):
            (cache/name).unlink(missing_ok=True)
        stamp.write_text(fingerprint)
    x,sr=sf.read(path,dtype='float32',always_2d=True); x=x.mean(1)
    N.VOCAB[:]=[]
    wp=cache/'heard.json'
    if wp.exists(): heard=json.loads(wp.read_text())
    else:
        text,words=N.listen_long(x,sr)
        heard=dict(text=text,words=words); wp.write_text(json.dumps(heard))
    N.free_gate_listener();N.free_take_listener()
    words=[w for w in heard['words'] if w['end']>w['start']]
    span=words[-1]['end']-words[0]['start']
    gaps=[b['start']-a['end'] for a,b in zip(words,words[1:]) if b['start']-a['end']>=0.15]
    fp=cache/'f0.npy'
    if fp.exists(): f0=np.load(fp)
    else:
        y=librosa.resample(x,orig_sr=sr,target_sr=16000)
        f0,v,_=librosa.pyin(y,fmin=60,fmax=400,sr=16000,frame_length=1024,hop_length=160)
        f0=np.where(v & np.isfinite(f0),f0,np.nan); np.save(fp,f0)
    st=12*np.log2(f0/100)
    raw=M.wer(expected,heard['text'])
    normalized=spoken.spoken_form(heard['text'],expected,[])
    # Keep the exact normalization and its letter-preservation guard for the
    # independent reviewer; raw WER stays visible beside the spoken-form WER.
    (cache/'wer-spoken.json').write_text(json.dumps(normalized,indent=1))
    final=M.wer(expected,normalized['text']) if normalized.get('ok') else raw
    r=dict(seconds=len(x)/sr,words=len(words),wer=final['wer'],wer_raw=raw['wer'],errors=final,
           pauses_per_min=round(len(gaps)/span*60,5),median_pause_s=round(float(np.median(gaps)),5) if gaps else None,
           pitch_range_st=float(np.nanpercentile(st,95)-np.nanpercentile(st,5)),
           method='unprompted whisper large-v3; gaps >=0.15s; pyin 60..400Hz, 10ms, voiced p95-p5; VO2 independent-check method')
    r['targets']=dict(wer=r['wer']<0.01,pause_rate=11.7<=r['pauses_per_min']<=17.4,
                      pause_median=r['median_pause_s'] is not None and 0.22<=r['median_pause_s']<=0.28,
                      pitch=PITCH_BAND[0]<=r['pitch_range_st']<=PITCH_BAND[1])
    r['ok']=all(r['targets'].values())
    (cache/'metrics.json').write_text(json.dumps(r,indent=1))
    return r


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('audio'); ap.add_argument('script'); ap.add_argument('cache')
    a=ap.parse_args(); j=json.loads(Path(a.script).read_text())
    print(json.dumps(measure(a.audio,' '.join(s.get('text',s.get('say','')) for s in j['segments']),a.cache)))
