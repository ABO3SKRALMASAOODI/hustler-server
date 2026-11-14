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

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE.parent/'valmera-stories'),str(HERE.parent/'_shell'/'narrate')]
import voice as shared
from measure_voice import measure


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


def shorten_joins(audio, wj, heard, script, out, ffmpeg, aim=16.0):
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
    candidates=[]
    for a,b in gaps:
        g=b['start']-a['end'];mid=(a['end']+b['start'])/2
        if g<=.26 and all(abs(mid-t)>.5 for t in protected):
            candidates.append((g,max(a['start'],a['end']-.06),min(b['end'],b['start']+.06)))
    selected=sorted(sorted(candidates)[:min(need,math.ceil(span/60*6))],key=lambda c:c[1])
    chunks=[];edits=[];pos=0
    for _,a,b in selected:
        ia,ib=round(a*sr),round(b*sr)
        if ia<pos or ib-ia<sr*.1:continue
        source=Path(out)/'join-in.wav';dest=Path(out)/'join-out.wav'
        sf.write(source,x[ia:ib],sr,subtype='FLOAT')
        subprocess.run([ffmpeg,'-v','error','-y','-i',str(source),'-af','atempo=1.5','-c:a','pcm_f32le',str(dest)],check=True)
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
    before=measure(origin,expected,out/'performance-metrics')
    if before['wer']>=.01: raise ValueError('Easton performance must pass word error before contour processing')
    factor=1.0
    gain=1.0 if before['targets']['pitch'] else 14.5/before['pitch_range_st']
    if not .85<=gain<=1.3: raise ValueError('pitch correction would exceed the natural performance limit; regenerate')
    for attempt in range(2):
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
        after=measure(audio,expected,out/'longform-metrics')
        joins=[]
        if after['pauses_per_min']>17.4:
            heard=json.loads((out/'longform-metrics/heard.json').read_text())
            ff=os.environ.get('NARRATE_FFMPEG') or '/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm/ffmpeg'
            wj,joins=shorten_joins(audio,wj,heard,script,out,ff)
            words.write_text(json.dumps(wj,indent=1))
            after=measure(audio,expected,out/'longform-metrics')
        result=dict(before=before,after=after,tempo=factor,contour_gain=gain,join_edits=joins,method='Easton + Praat overlap-add, existing pitch contour; short joins paced with WSOLA, phonemes retained')
        done.write_text(json.dumps(result,indent=1))
        if after['ok']:
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
        gain*=14.5/after['pitch_range_st']
        if not .85<=gain<=1.3: break
        if after['pauses_per_min']<=17.4:
            factor*=tempo_factor(after['pauses_per_min'],after['median_pause_s'])
        if not .92<=factor<=1.08: break
    raise ValueError('measured Easton delivery remains outside VO2: '+json.dumps(after))


def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest='cmd',required=True)
    for cmd in ('render','finish'):
        p=sub.add_parser(cmd);p.add_argument('script');p.add_argument('--out',required=True);p.add_argument('--band',default='04')
    p=sub.add_parser('level-voices');p.add_argument('dir')
    a=ap.parse_args()
    if not os.environ.get('OPENAI_API_KEY'):
        for line in Path('/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env').read_text().splitlines():
            if line.startswith('OPENAI_API_KEY='): os.environ['OPENAI_API_KEY']=line.split('=',1)[1].strip().strip('"').strip("'")
    if a.cmd=='level-voices': return shared.level_voices(a)
    if a.cmd=='render' and not (Path(a.out)/'narration_performance.wav').exists():
        os.environ['NARRATE_ENGINE']='phonon'
        shared.render(a)
        rendered=json.loads((Path(a.out)/'render.json').read_text())
        if rendered.get('engine')!='lines:phonon':raise RuntimeError('WF4 requires Easton; a different-engine fallback cannot be delivered')
    print(json.dumps(finish(a.out,json.loads(Path(a.script).read_text()))))


if __name__=='__main__':main()
