"""Signal-only VO2-compatible measures; the ASR listener receives no name prompt."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import tempfile
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'_shell'/'narrate'))

# Voiced p95-p5 pitch range of the long-form narrators (ref_band.json band 04, p10..p90 of 20 long-form reference films).
# Until 16 Nov this was the metromedia reels' 13.79-15.33 st, which Easton reaches only by resynthesising its contour.
PITCH_BAND=tuple(json.loads((Path(__file__).resolve().parent.parent/'_shell'/'narrate'/'ref_band.json').read_text())
                 ['band']['04']['f0_range_st'][k] for k in ('p10','p90'))


JUDGE_SYSTEM=("You check a narration's word errors. Each item has the scripted words, the words a speech recogniser wrote "
              "for the same place, and the sentence around it. Say which items are the scripted words written another way: "
              "a spelling of a name or a term, an initialism or number written differently, a hyphen, space or apostrophe, "
              "a possessive written without its apostrophe. A different word, another ending or tense ('clearly' for 'clear', "
              "'use' for 'used'), or a dropped or added word is a real error and is not on the list. Answer JSON only: "
              "{\"same\": [the item numbers]}")


def judged_equivalents(details, expected, cache, judge=None):
    """The word errors a model reads as the scripted word written another way (the listener's spelling, not a misread).
    {item index: True}; asked once per distinct list of errors (kept beside the measurement), and an unanswered
    question leaves every error standing."""
    if not details:
        return set()
    cache=Path(cache);memo=cache/'equivalents.json'
    key=hashlib.sha256(json.dumps(details,sort_keys=True).encode()).hexdigest()
    if memo.exists():
        saved=json.loads(memo.read_text())
        if saved.get('key')==key:return set(saved['same'])
    words=expected.split()
    items=[]
    for i,d in enumerate(details):
        around=' '.join(words[max(0,d['at']-6):d['at']+8])
        items.append(f"{i}. scripted: {d['script'] or '(nothing)'} | heard: {d['heard'] or '(nothing)'} | around: {around}")
    try:
        if judge is None:
            import spoken
            r=spoken._call(JUDGE_SYSTEM,"\n".join(items),spoken.MODEL)
            text="".join(c.get('text','') for it in r.get('output') or [] if it.get('type')=='message'
                         for c in it.get('content') or [] if c.get('type')=='output_text')
        else:text=judge(JUDGE_SYSTEM,"\n".join(items))
        same={int(i) for i in json.loads(text[text.index('{'):text.rindex('}')+1])['same'] if 0<=int(i)<len(details)}
    except Exception:
        return set()
    memo.write_text(json.dumps(dict(key=key,same=sorted(same))))
    return same


def line_of(at, segments):
    """The script line holding the normalised word at index `at`."""
    import measure as M
    n=0
    for i,s in enumerate(segments):
        n+=len(M.norm_words(s['text']))
        if at<n:return i
    return len(segments)-1


def share_evidence(source, cache, fingerprint):
    """Reuse the finishing pass only for the identical waveform and scripted words."""
    stamp=source/'input.sha256'
    if source==cache or not stamp.is_file() or stamp.read_text()!=fingerprint:
        return
    for name in ('heard.json','f0.npy','wer-spoken.json','equivalents.json'):
        src,dst=source/name,cache/name
        if src.is_file() and not dst.exists():
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as tmp:
                temp=Path(tmp.name)
            try:
                shutil.copyfile(src,temp)
                if stamp.read_text()==fingerprint:
                    os.replace(temp,dst)
            finally:
                temp.unlink(missing_ok=True)


def measure(path, expected, cache, segments=None, judge=None, reuse=None):
    import soundfile as sf
    import narrate as N
    import measure as M
    import spoken
    cache=Path(cache); cache.mkdir(parents=True,exist_ok=True)
    with open(path,'rb') as stream: digest=hashlib.file_digest(stream,'sha256')
    digest.update(expected.encode());fingerprint=digest.hexdigest()
    stamp=cache/'input.sha256'
    if not stamp.exists() or stamp.read_text()!=fingerprint:
        for name in ('heard.json','f0.npy','metrics.json'):
            (cache/name).unlink(missing_ok=True)
        stamp.write_text(fingerprint)
    if reuse:share_evidence(Path(reuse),cache,fingerprint)
    wp,fp=cache/'heard.json',cache/'f0.npy'
    info=sf.info(path)
    if not wp.is_file() or not fp.is_file():
        x,sr=sf.read(path,dtype='float32',always_2d=True); x=x.mean(1)
    N.VOCAB[:]=[]
    if wp.exists(): heard=json.loads(wp.read_text())
    else:
        text,words=N.listen_long(x,sr)
        heard=dict(text=text,words=words); wp.write_text(json.dumps(heard))
    N.free_gate_listener();N.free_take_listener()
    words=[w for w in heard['words'] if w['end']>w['start']]
    span=words[-1]['end']-words[0]['start']
    gaps=[b['start']-a['end'] for a,b in zip(words,words[1:]) if b['start']-a['end']>=0.15]
    if fp.exists(): f0=np.load(fp)
    else:
        import librosa
        y=librosa.resample(x,orig_sr=sr,target_sr=16000)
        f0,v,_=librosa.pyin(y,fmin=60,fmax=400,sr=16000,frame_length=1024,hop_length=160)
        f0=np.where(v & np.isfinite(f0),f0,np.nan); np.save(fp,f0)
    st=12*np.log2(f0/100)
    raw=M.wer(expected,heard['text'])
    sp=cache/'wer-spoken.json'
    asked=hashlib.sha256(json.dumps([heard['text'],expected]).encode()).hexdigest()
    normalized=None
    if sp.exists():
        # the same heard words against the same script were already written in spoken form: read it back, no call
        saved=json.loads(sp.read_text())
        if saved.get('ok') and saved.get('asked')==asked:normalized=saved
    if normalized is None:normalized=dict(spoken.spoken_form(heard['text'],expected,[]),asked=asked)
    # Keep the exact normalization and its letter-preservation guard for the
    # independent reviewer; raw WER stays visible beside the spoken-form WER.
    (cache/'wer-spoken.json').write_text(json.dumps(normalized,indent=1))
    final=M.wer(expected,normalized['text']) if normalized.get('ok') else raw
    equivalent=[]
    if final['wer']>=0.01:
        # Past the gate: ask a model which of these words are the scripted word spelled another way by the listener.
        details=M.wer_detail(expected,normalized['text'] if normalized.get('ok') else heard['text'])
        for i in sorted(judged_equivalents(details,expected,cache,judge)):
            d=details[i];d['line']=line_of(d['at'],segments) if segments else None
            equivalent.append(d)
        gone=sum(max(len(d['script'].split()),len(d['heard'].split())) for d in equivalent)
        if gone:final=dict(final,wer=round(max(0,final['S']+final['D']+final['I']-gone)/max(final['ref_words'],1),4))
    r=dict(seconds=info.frames/info.samplerate,words=len(words),wer=final['wer'],wer_raw=raw['wer'],errors=final,equivalent_spellings=equivalent,
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
    ap=argparse.ArgumentParser(); ap.add_argument('audio'); ap.add_argument('script'); ap.add_argument('cache'); ap.add_argument('--reuse')
    a=ap.parse_args(); j=json.loads(Path(a.script).read_text())
    print(json.dumps(measure(a.audio,' '.join(s.get('text',s.get('say','')) for s in j['segments']),a.cache,reuse=a.reuse)))
