"""MUSIC2 on the film's actual narrative chapters, with measured stem coverage."""
from pathlib import Path
import json
import os
import sys
import numpy as np


def chapter_lines(words, a, b, lead=0.0):
    """The chapter's narration as lines on its own clock [(second, text)], for the model that chooses its music by the
    story: a line ends at a sentence's end or after 16 words."""
    out, cur, t0 = [], [], None
    for w in words:
        t = float(w['start']) + lead
        if not a <= t < b:
            continue
        text = str(w.get('w') or w.get('word') or w.get('text') or '').strip()
        if not text:
            continue
        t0 = t - a if t0 is None else t0
        cur.append(text)
        if text[-1:] in '.?!' or len(cur) >= 16:
            out.append((round(t0, 2), ' '.join(cur)))
            cur, t0 = [], None
    if cur:
        out.append((round(t0, 2), ' '.join(cur)))
    return out


def bridge_rests(bed, continuation, plan, sr, phrase):
    """Bridge measured rests with the same recording, crossfading at both ends."""
    hop = max(1, sr//10)
    starts = np.arange(0, len(bed), hop)
    power = np.add.reduceat(np.mean(bed**2, axis=1), starts) / np.diff(np.r_[starts, len(bed)])
    quiet = power < 10**(-52/10)
    edges = np.diff(np.r_[False, quiet, False].astype(int))
    repairs = []
    for a, b in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        left, right = max(0,(a-2)*hop), min(len(bed),(b+2)*hop)
        pos = left/sr
        seg = next((s for s in plan['segments'] if s['film'][0] <= pos < s['film'][1]), None)
        tempo = seg['tempo'] if seg else continuation['tempo']
        at = (seg['song'][0]+(pos-seg['film'][0])*tempo if seg else
              continuation['at_s']+(pos-len(bed)/sr)*tempo)
        fill = phrase(continuation['path'], max(0,at), tempo, right-left, continuation['bar_s'])
        neighbors = np.r_[power[max(0,a-12):a], power[b:b+12]]
        level = max(10**(-36/10), min(10**(-27/10), np.median(neighbors) if len(neighbors) else 0))
        fill = fill * np.sqrt(level / max(1e-12,np.mean(fill**2)))
        index = np.arange(left,right)
        gain = np.ones(right-left)
        if left < a*hop:
            gain = np.minimum(gain,(index-left)/(a*hop-left))
        if right > b*hop:
            gain = np.minimum(gain,(right-1-index)/(right-b*hop))
        bed[left:right] = bed[left:right]*(1-gain[:,None])+fill*gain[:,None]
        repairs.append([a*hop/sr,min(b*hop,len(bed))/sr])
    return repairs


def retained_track(choice, cache):
    import hashlib
    import music_choose
    import music_film as MF
    candidates = music_choose.set_tracks()
    track = next((r for r in candidates if r['cid']==choice['cid']), choice)
    path = MF.track_file(track, cache)
    with open(path,'rb') as file:
        return path if hashlib.file_digest(file,'sha256').hexdigest()==choice.get('source_sha256') else None


def closing_recording(music, cache, bed_file=None):
    from score import music as MU
    row = music['chapters'][-1]
    try:
        path = retained_track(row['choice'], cache)
    except (OSError,ValueError):
        path = None
    if path:
        song = MU.analyse(path, sung=row['choice'].get('sung'))
        return dict(MU.card_continuation(dict(dur_s=row['film'][1]-row['film'][0]),song,row['plan'],path),
                    speech_room_db=10)

    if bed_file and Path(bed_file).is_file():
        import subprocess
        import cinema as C
        a,b = row['film']
        dest=Path(cache)/('closing-'+C.digest(bed_file)[:16]+'.flac')
        dest.parent.mkdir(exist_ok=True,parents=True)
        if not dest.exists():
            pending=dest.with_suffix('.part.flac')
            subprocess.run([C.PX.FF,'-v','error','-y','-ss',str(a),'-i',str(bed_file),
                            '-t',str(b-a),'-c:a','flac',str(pending)],check=True)
            pending.replace(dest)
        return dict(path=str(dest),at_s=b-a,tempo=1.,bar_s=row['plan'].get('bar_s'),speech_room_db=10)


def remember_choices(supply, stamp, narration):
    """Reuse a former mix's choices on an unchanged story and voice clock."""
    import cinema as C
    root=Path(supply)
    job, report=root/'score_music.json', root/'film_scored.mp4.json'
    if stamp.get('narration')!=narration or not job.exists() or not report.exists():
        return
    from interval_evidence import signature
    old=C.read(job)['l1']['chapters']
    choices=root/'chapter-choices.json'
    saved=C.read(choices) if choices.exists() else {}
    for ch,row in zip(old,C.read(report)['chapters']):
        if ch['id']==row['chapter'] and [ch['start'],ch['end']]==row['film']:
            saved[signature([narration,ch])]=row['choice']
    C.save(choices,saved)


def score(l1, pack, film, out, cache):
    import music_film as MF
    from score import music as MU
    import music_choose
    from stem_evidence import write_audio
    prof=MU.profile_for('longform')
    wd=json.loads(Path(pack['words']).read_text())
    lead=0.0
    import film as F
    lead=F.LEAD_F/F.FPS
    dur=l1['dur_f']/l1['fps']
    voice=MU._decode(str(Path(film).parent/'audio-stems/voice.flac'),MU.SR,2)
    whole=np.zeros((round(dur*MU.SR),2),np.float32)
    from cinema import digest
    narration=digest(pack['narration'])
    prior=Path(out).parent/'chapter-choices.json'
    choices=json.loads(prior.read_text()) if prior.exists() else {}
    used=[]; rows=[]; call=MF.openai_call(timeout=1205 if os.environ.get('CODEX_PROXY_LOCAL')=='1' else 90)
    for ch in l1['chapters']:
        a,b=ch['start'],ch['end']; d=b-a
        marks=dict(dur_s=d,words=[(max(0,w['start']+lead-a),min(d,w['end']+lead-a)) for w in wd['words'] if a<=w['start']+lead<b],
                   cuts=[it['f'][0]/30-a for it in l1['items'] if it['k']=='shot' and a<it['f'][0]/30<b],
                   landings=[],accents=[],turn_s=ch['turn_s']-a,payoff_s=ch['payoff_s']-a,end_hit_s=d-0.5,quiet=[])
        fact=MF.facts('longform',marks,brief=ch['title']+': '+ch['turn'],mood=ch.get('music_mood',''),lines=chapter_lines(wd['words'],a,b,lead))
        from interval_evidence import signature
        identity=signature([narration,ch])
        choice=choices.get(identity)
        try:
            path=retained_track(choice,cache) if choice else None
        except (OSError,ValueError):
            path=None
        if not path:
            choice,path,tried=MF.choose(fact,marks,cache,used=used,call=call,profile=prof)
        if not choice: raise RuntimeError('MUSIC2 found no track for '+ch['id'])
        from cinema import save
        choices[identity]=choice
        save(prior,choices)
        song=MU.analyse(path,sung=choice.get('sung'))
        plan=MU.arrange(song,MF.arranged(marks,choice),profile=prof)
        if not plan.get('ok'): raise RuntimeError('MUSIC2 arrangement failed for '+ch['id'])
        ia,ib=round(a*MU.SR),round(b*MU.SR)
        bed,rep=MU.track(path,song,plan,marks,voice=voice[ia:ib],profile=prof)
        continuation=MU.card_continuation(marks,song,plan,path)
        rep['bridged_rests']=bridge_rests(bed,continuation,plan,MU.SR,MU.card_phrase)
        whole[ia:ia+min(len(bed),len(whole)-ia)]+=bed[:min(len(bed),len(whole)-ia)]
        used.append(choice['cid'])
        # Measure an audible rise over 2-8 seconds from actual 0.5 s RMS windows.
        n=int(MU.SR/2); count=len(bed)//n
        db=10*np.log10(np.mean(bed[:count*n].reshape(count,n,2).astype(np.float64)**2,axis=(1,2))+1e-14)
        rises=[(float(db[i]-db[i-k]),float(a+i/2),k/2) for k in range(4,17) for i in range(k,len(db)) if db[i-k]>-60 and db[i]>-50]
        rise=max(rises,default=(0,a,0))
        row=dict(chapter=ch['id'],film=[a,b],choice=choice,plan={k:v for k,v in plan.items() if k!='grid'},levels=rep,
                 measured_rise_db=round(rise[0],2),rise_at_s=rise[1],rise_span_s=rise[2])
        if rise[0]<3: raise RuntimeError('no measured music rise >=3 dB in '+ch['id'])
        rows.append(row)
    n=int(MU.SR/10); count=len(whole)//n
    power=np.mean(whole[:count*n].reshape(count,n,2).astype(np.float64)**2,axis=(1,2))
    share=float(np.mean(10*np.log10(power+1e-14)>-50))
    report=dict(chapters=rows,card_bed=dict(continuation,speech_room_db=10),music_share=share,method='100ms music-stem RMS above -50 dBFS; chapter rises measured over 2-8s',
                credit='; '.join(dict.fromkeys(r['choice'].get('credit','') for r in rows)))
    if share<0.8: raise RuntimeError(f'music coverage {share:.3f} below 80%')
    from scene import audio as AU
    ceiling,limit=AU.CEIL_DBTP,AU.LIMIT_DBFS
    AU.CEIL_DBTP,AU.LIMIT_DBFS=min(ceiling,-2.0),min(limit,-2.6)
    try:
        from chapter_audio import mix
        report['mix']=mix(l1,voice,whole,film,out)
    finally:
        AU.CEIL_DBTP,AU.LIMIT_DBFS=ceiling,limit
    Path(out+'.json').write_text(json.dumps(report,indent=1))
    return report


if __name__=='__main__':
    import cinema
    cinema.worker()
    args=json.loads(Path(sys.argv[1]).read_text())
    score(**args)
