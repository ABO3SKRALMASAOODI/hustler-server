#!/usr/bin/env python3
"""Easton's shared performance, then bounded signal correction against VO2.

Pitch follows the performance's existing contour, expanded around its median
with Praat overlap-add. No syllable, word or invented intonation is inserted.
A small, pitch-preserving tempo correction is allowed only when both pause
measures can remain in band. Every correction is heard again without a prompt.
Invoke under the same narrator lock as the shared reader.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import math
import hashlib

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE.parent/'valmera-stories'),str(HERE.parent/'_shell'/'narrate')]
import voice as shared
from measure_voice import measure,line_of


from read_cache import bounded_generation, cache_gate_listening, cache_take_analysis


def read_max_tempo():
    """The tempo ceiling the read runs under (the shared lines module's), for a stage written before it was kept."""
    import lines as LN
    return LN.MAX_TEMPO


def tempo_factor(rate, median):
    if 11.7<=rate<=17.4 and .22<=median<=.28: return 1.0
    lo=max(.92,11.9/rate,median/.278)
    hi=min(1.08,17.2/rate,median/.222)
    if lo>hi: raise ValueError(f'pause measures require a new performance: {rate:.2f}/min, {median:.3f}s')
    return round((lo+hi)/2,5)


def retime(j, factor):
    for kind in ('words','phrases'):
        for w in j.get(kind,[]):
            for k in ('start','end'): w[k]=round(w[k]/factor,4)
    j['cut_points']=[round(t/factor,4) for t in j.get('cut_points',[])]
    return j


def map_time(t, edits):
    shift=0.0
    for a,b,new_s in edits:
        if t<a:break
        if t<b:return a-shift+(t-a)*new_s/(b-a)
        shift+=(b-a)-new_s
    return t-shift


def shorten_joins(audio, wj, heard, script, out, ffmpeg, aim=14.0):
    """Speed only short inter-word joins with WSOLA; retain all phonemes.

    A gap in ASR timestamps includes unvoiced consonants. Cutting it as silence
    would truncate words. A bounded local tempo change keeps that sound intact.
    Turns stay protected; word/phrase clocks follow the actual sample counts.
    """
    import numpy as np
    import soundfile as sf
    x,sr=sf.read(audio,dtype='float32');words=heard['words']
    span=words[-1]['end']-words[0]['start']
    gaps=[(a,b) for a,b in zip(words,words[1:]) if b['start']-a['end']>=.15]
    need=max(0,len(gaps)-math.floor(span/60*aim))
    protected=[w['start'] for w in wj['words'] if w.get('seg',0)<len(script['segments'])
               and script['segments'][w.get('seg',0)].get('beat') in ('turn','low','payoff')
               and not any(q.get('seg')==w.get('seg') and q['start']<w['start'] for q in wj['words'])]
    candidates=[];ordinary=[]
    for a,b in gaps:
        g=b['start']-a['end'];mid=(a['end']+b['start'])/2
        if all(abs(mid-t)>.5 for t in protected):
            bounds=(max(a['start'],a['end']-.06),min(b['end'],b['start']+.06))
            if g<=.34:candidates.append((g,*bounds,min(2.0,(g+.12)/(.09+.12))))
            elif .34<g<=.65:
                ordinary.append((abs(g-.32),*bounds,min(1.7,(g+.12)/(.24+.12))))
    selected=sorted(sorted(candidates)[:min(need,math.ceil(span/60*9))]
                    +sorted(ordinary)[:math.ceil(span/60*5)],key=lambda c:c[1])
    chunks=[];edits=[];pos=0
    for _,a,b,speed in selected:
        ia,ib=round(a*sr),round(b*sr)
        if ia<pos or ib-ia<sr*.1:continue
        source=Path(out)/'join-in.wav';dest=Path(out)/'join-out.wav'
        sf.write(source,x[ia:ib],sr,subtype='FLOAT')
        subprocess.run([ffmpeg,'-v','error','-y','-i',str(source),'-af',f'atempo={speed:.6f}','-c:a','pcm_f32le',str(dest)],check=True)
        y,_=sf.read(dest,dtype='float32')
        if not .5*(ib-ia)<=len(y)<ib-ia:continue
        fade=min(round(.006*sr),len(y)//4)
        ramp=np.linspace(0,1,fade,dtype=np.float32)
        y[:fade]=x[ia:ia+fade]*(1-ramp)+y[:fade]*ramp
        y[-fade:]=y[-fade:]*(1-ramp)+x[ib-fade:ib]*ramp
        chunks.extend([x[pos:ia],y]);pos=ib;edits.append((ia/sr,ib/sr,len(y)/sr))
    chunks.append(x[pos:]);y=np.concatenate(chunks)
    for kind in ('words','phrases'):
        for w in wj.get(kind,[]):
            for k in ('start','end'):w[k]=round(map_time(w[k],edits),4)
    wj['cut_points']=[round(map_time(t,edits),4) for t in wj.get('cut_points',[])]
    wj['duration']=len(y)/sr
    sf.write(audio,y,sr,subtype='PCM_24')
    for name in ('join-in.wav','join-out.wav'):(Path(out)/name).unlink(missing_ok=True)
    return wj,edits


def unheard(audio,expected,cache,segments):
    """The words still counted against the read, line by line: scripted against heard."""
    import measure as M
    spoken=json.loads((Path(cache)/'wer-spoken.json').read_text())
    heard=json.loads((Path(cache)/'heard.json').read_text())['text']
    rows=M.wer_detail(expected,spoken['text'] if spoken.get('ok') else heard)
    return [dict(line='s%02d'%line_of(r['at'],segments),scripted=r['script'],heard=r['heard']) for r in rows]


def finish(out, script):
    import numpy as np
    import soundfile as sf
    import parselmouth
    from parselmouth.praat import call
    import master
    out=Path(out); expected=' '.join(s['text'] for s in script['segments'])
    done=out/'longform_voice.json'
    if done.exists():
        prior=json.loads(done.read_text())
        if prior['after']['ok']: return prior
    audio=out/'narration.wav'; words=out/'words.json'
    origin=out/'narration_performance.wav'; origin_words=out/'words_performance.json'
    origin_render=out/'render_performance.json'
    if not origin.exists():
        shutil.copy2(audio,origin); shutil.copy2(words,origin_words)
    if not origin_render.exists() and (out/'render.json').exists():shutil.copy2(out/'render.json',origin_render)
    before=measure(origin,expected,out/'performance-metrics',script['segments'])
    if before['wer']>=.01: raise ValueError('Easton performance must pass word error before contour processing; the words heard against the script: '+json.dumps(unheard(origin,expected,out/'performance-metrics',script['segments']),ensure_ascii=False))
    factor=1.0
    # No pitch resynthesis (16 Nov). Expanding the whole contour toward the short reels' 14.5 st (gain 1.24 on the 16 Nov
    # Skylab read) made every sentence end fall deeper (78% of line ends over 2 st, 4% rising; metromedia 53-55% and
    # 20-29%) and gave the voice an overlap-add sheen. Range comes from the performance: the sentence shape the writer
    # gives Phonon and the takes chosen for range and restart. Pitch is measured against the long-form narrators' band.
    gain=1.0
    # Pace (16 Nov): Easton reads long-form text at 224-241 words a minute while speaking (pyin/whisper, lane
    # longform-lane), above the metromedia narrators' 183-213 and the long-form narrators' median 213, and the pause
    # correction below used to speed it up further (x1.08) to pull the pause rate into its band. A read over 216 is
    # brought to 208, and no correction takes it past 214.
    artic0=shared.artic(json.loads(origin_words.read_text()).get('words') or [])
    factor=round(max(.90,208/artic0),5) if artic0 and artic0>216 else 1.0
    fastest=max(1.0,214/artic0) if artic0 else 1.08
    # A clear native take can still rush through evidence. Measure elapsed time,
    # including breaths, against the two Madigan reads. Preserve natural pitch
    # and carry the identical tempo map into captions and chapter boundaries.
    rates = {"slow": 150, "steady": 159, "brisk": 180}
    target_seconds = sum(
        60 * len(seg["text"].split()) / rates.get(seg.get("pace", script.get("pace")), 159)
        for seg in script["segments"]
    )
    if target_seconds and before["seconds"] < target_seconds:
        reference_tempo = max(.84, before["seconds"] / target_seconds)
        factor = round(min(factor, reference_tempo), 5)
        fastest = min(fastest, factor)
    join_aim=14.0
    for attempt in range(3):
        x,sr=sf.read(origin,dtype='float32')
        if abs(gain-1)>.005:
            snd=parselmouth.Sound(x,sampling_frequency=sr)
            manipulation=call(snd,'To Manipulation',.01,60,400)
            tier=call(manipulation,'Extract pitch tier')
            f=snd.to_pitch_ac(time_step=.01,pitch_floor=60,pitch_ceiling=400).selected_array['frequency']
            median=float(np.median(f[f>0]))
            call(tier,'Formula',f'{median} * (self / {median}) ^ {gain}')
            call([tier,manipulation],'Replace pitch tier')
            x=call(manipulation,'Get resynthesis (overlap-add)').values[0].astype(np.float32)
            del snd,manipulation,tier,f
        temp=out/'contour.wav'; sf.write(temp,x,sr,subtype='FLOAT')
        if factor!=1:
            ff=os.environ.get('NARRATE_FFMPEG') or str(Path('/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg'))
            paced=out/'paced.wav'
            subprocess.run([ff,'-v','error','-y','-i',str(temp),'-af',f'atempo={factor}','-c:a','pcm_f32le',str(paced)],check=True)
            x,sr=sf.read(paced,dtype='float32'); paced.unlink()
        temp.unlink(missing_ok=True)
        x=master.loudness_set(x,sr,master.LUFS,-1.0)
        sf.write(audio,x,sr,subtype='PCM_24')
        wj=retime(json.loads(origin_words.read_text()),factor);wj['duration']=len(x)/sr
        words.write_text(json.dumps(wj,indent=1))
        del x
        import gc
        gc.collect()
        joins=[]
        if before['pauses_per_min']*factor>17.4:
            # Pitch resynthesis preserves time. The already measured original
            # word clock is enough to locate joins; only the completed edit
            # needs another independent listen and pitch pass.
            heard=retime(json.loads((out/'performance-metrics/heard.json').read_text()),factor)
            ff=os.environ.get('NARRATE_FFMPEG') or '/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg'
            wj,joins=shorten_joins(audio,wj,heard,script,out,ff,aim=join_aim)
            words.write_text(json.dumps(wj,indent=1))
        after=measure(audio,expected,out/'longform-metrics',script['segments'])
        result=dict(before=before,after=after,tempo=factor,contour_gain=gain,join_edits=joins,method='Easton performance as read (no pitch resynthesis); short joins paced with WSOLA, phonemes retained')
        last=attempt==2
        nxt=factor
        if not after['ok'] and after['pauses_per_min']<=17.4:
            try:nxt=min(factor*tempo_factor(after['pauses_per_min'],after['median_pause_s']),fastest)
            except ValueError:nxt=0
        if not after['ok'] and after['wer']<.01:
            if last or not .88<=nxt<=1.08 or (nxt==factor and after['pauses_per_min']<=17.4):
                # Every word is right and the read is the performance itself: deliver it and record which pause or
                # range measure sits outside its band (16 Nov: a 0.29 s median pause against 0.28 stopped the film).
                result.update(accepted=True,outside=sorted(k for k,v in after['targets'].items() if not v))
        done.write_text(json.dumps(result,indent=1))
        if after['ok'] or result.get('accepted'):
            if origin_render.exists():
                rj=json.loads(origin_render.read_text())
                for b in rj.get('boundaries',[]):
                    b['t']=round(map_time(b['t']/factor,joins),4)
                    if b.get('gap'):b['gap']=[round(map_time(t/factor,joins),4) for t in b['gap']]
                rj['longform_read']=result
                (out/'render.json').write_text(json.dumps(rj,indent=1))
            (out/'check.json').unlink(missing_ok=True)
            return result
        if after['wer']>=.01: break
        if after['pauses_per_min']<=17.4:factor=nxt
        else:join_aim=max(10.,join_aim-max(1.,(after['pauses_per_min']-16.5)*1.6))
    # Only a word the listener does not hear stops here; read_repair.py rewrites that line and the run resumes.
    raise ValueError('Easton read has word errors after its corrections: '+json.dumps(after))


def raw_checkpoint(out,script,groups,picks=None,tone_fn=None,max_tempo=1.0):
    """Commit the read before the shared mastering FFT allocates its buffers.

    It stands in for voice.finish inside the read, so it takes exactly what voice.finish takes
    (test_longform_voice.py compares the two signatures). The mastering that follows is the shared finish
    in its own process, which gets the read's tempo ceiling back from read-stage.json; tone_fn belongs to
    that later call, which passes room_tone itself.
    """
    import narrate as N
    N.free_gate_listener();N.free_take_listener()
    out=Path(out)
    for name in ('narration.wav','words.json','render.json'):
        shutil.copy2(out/name,out/(Path(name).stem+'_raw'+Path(name).suffix))
    for name in ('narration_performance.wav','words_performance.json','render_performance.json','longform_voice.json','check.json'):
        (out/name).unlink(missing_ok=True)
    (out/'read-stage.json').write_text(json.dumps(dict(script=script,groups=groups,picks=picks or [],max_tempo=max_tempo)))


def room_tone(n,sr,db):
    """The shared pink floor, using a fast padded FFT rather than a costly
    prime-length transform of twelve minutes. Float32 is ample for this floor.
    """
    import numpy as np
    from scipy import fft
    size=fft.next_fast_len(n,real=True)
    rng=np.random.default_rng(1114)
    spectrum=fft.rfft(rng.standard_normal(size,dtype=np.float32),overwrite_x=True)
    frequencies=fft.rfftfreq(size,1/sr).astype(np.float32)
    spectrum*=1/np.sqrt(np.maximum(frequencies,60.)/60.)
    spectrum[frequencies>9000]*=.3
    tone=fft.irfft(spectrum,size,overwrite_x=True)[:n].copy()
    del spectrum,frequencies
    tone*=10**(db/20)/(float(np.sqrt((tone*tone).mean()))+1e-12)
    return tone


def master_version():
    return hashlib.sha256(Path(__file__).read_bytes()+Path(shared.__file__).read_bytes()).hexdigest()


def isolated_render(a):
    """Each phase exits before the next loads models or whole-film FFT buffers.
    The outer guard observes every child; there is one shared lock for the job.
    """
    out=Path(a.out);script=json.loads(Path(a.script).read_text())
    stage=out/'read-stage.json'
    ready=stage.exists() and json.loads(stage.read_text())['script']==script
    if not ready or a.rerender:
        subprocess.run([sys.executable,str(Path(__file__).resolve()),'_render',a.script,'--out',a.out,'--band',a.band],check=True)
    rj=out/'render.json'
    stamp=out/'shared-finish.sha256'
    mastered=rj.exists() and json.loads(rj.read_text()).get('stories_read') and stamp.exists() and stamp.read_text()==master_version()
    if a.rerender or not mastered:
        subprocess.run([sys.executable,str(Path(__file__).resolve()),'_shared-finish',a.script,'--out',a.out,'--band',a.band],check=True)
    subprocess.run([sys.executable,str(Path(__file__).resolve()),'finish',a.script,'--out',a.out,'--band',a.band],check=True)


def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='cmd',required=True)
    for cmd in ('render','finish','_render','_shared-finish'):
        p=sub.add_parser(cmd);p.add_argument('script');p.add_argument('--out',required=True);p.add_argument('--band',default='04')
        if cmd=='render':p.add_argument('--rerender',action='store_true')
    p=sub.add_parser('level-voices');p.add_argument('dir')
    a=ap.parse_args()
    import narrate as N
    N.malloc_returns_memory()
    if not os.environ.get('OPENAI_API_KEY'):
        for line in Path('/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env').read_text().splitlines():
            if line.startswith('OPENAI_API_KEY='): os.environ['OPENAI_API_KEY']=line.split('=',1)[1].strip().strip('"').strip("'")
    if a.cmd=='level-voices': return shared.level_voices(a)
    if a.cmd=='render':return isolated_render(a)
    if a.cmd=='_shared-finish':
        out=Path(a.out);stage=json.loads((out/'read-stage.json').read_text())
        if stage['script']!=json.loads(Path(a.script).read_text()):raise ValueError('raw read belongs to a different script')
        for name in ('narration.wav','words.json','render.json'):
            shutil.copy2(out/(Path(name).stem+'_raw'+Path(name).suffix),out/name)
        for name in ('narration_performance.wav','words_performance.json','render_performance.json','longform_voice.json','check.json'):
            (out/name).unlink(missing_ok=True)
        import narrate as N
        N.malloc_returns_memory()
        shared.finish(out,stage['script'],stage['groups'],stage['picks'],tone_fn=room_tone,max_tempo=stage.get('max_tempo',read_max_tempo()))
        (out/'shared-finish.sha256').write_text(master_version())
        return
    if a.cmd=='_render':
        import narrate as N
        import datetime
        original_log=N.log
        def progress(*messages):
            original_log(*messages)
            with open(Path(a.out)/'progress.jsonl','a') as f:
                f.write(json.dumps(dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),message=' '.join(map(str,messages))))+'\n')
        Path(a.out).mkdir(parents=True,exist_ok=True)
        N.log=progress
        os.environ['NARRATE_ENGINE']='phonon'
        os.environ['NARRATE_KEEP_TAKES']='1'
        cache_take_analysis(a.out)
        os.environ.pop('NARRATE_ALLOW_FALLBACK',None)
        shared.Read.generate_phonon=bounded_generation(shared.Read.generate_phonon)
        cache_gate_listening(a.out)
        shared.finish=raw_checkpoint
        shared.render(a)
        rendered=json.loads((Path(a.out)/'render.json').read_text())
        if rendered.get('engine')!='lines:phonon':raise RuntimeError('WF4 requires Easton; a different-engine fallback cannot be delivered')
        return
    print(json.dumps(finish(a.out,json.loads(Path(a.script).read_text()))))


if __name__=='__main__':main()
