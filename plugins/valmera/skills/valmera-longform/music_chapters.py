"""MUSIC2 on the film's actual narrative chapters, with measured stem coverage."""
from pathlib import Path
import json
import os
import sys
import numpy as np


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
    voice=MU._decode(film,MU.SR,2)
    whole=np.zeros((round(dur*MU.SR),2),np.float32)
    used=[]; rows=[]; call=MF.openai_call(timeout=185 if os.environ.get('CODEX_PROXY_LOCAL')=='1' else 90)
    for ch in l1['chapters']:
        a,b=ch['start'],ch['end']; d=b-a
        marks=dict(dur_s=d,words=[(max(0,w['start']+lead-a),min(d,w['end']+lead-a)) for w in wd['words'] if a<=w['start']+lead<b],
                   cuts=[it['f'][0]/30-a for it in l1['items'] if it['k']=='shot' and a<it['f'][0]/30<b],
                   landings=[],accents=[],turn_s=ch['turn_s']-a,payoff_s=ch['payoff_s']-a,end_hit_s=d-0.5,quiet=[])
        fact=MF.facts('longform',marks,brief=ch['title']+': '+ch['turn'],mood=ch.get('music_mood',''))
        choice,path,tried=MF.choose(fact,marks,cache,used=used,call=call,profile=prof)
        if not choice: raise RuntimeError('MUSIC2 found no track for '+ch['id'])
        song=MU.analyse(path,sung=choice.get('sung'))
        plan=MU.arrange(song,marks,profile=prof)
        if not plan.get('ok'): raise RuntimeError('MUSIC2 arrangement failed for '+ch['id'])
        ia,ib=round(a*MU.SR),round(b*MU.SR)
        bed,rep=MU.track(path,song,plan,marks,voice=voice[ia:ib],profile=prof)
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
    report=dict(chapters=rows,music_share=share,method='100ms music-stem RMS above -50 dBFS; chapter rises measured over 2-8s',
                credit='; '.join(dict.fromkeys(r['choice'].get('credit','') for r in rows)))
    if share<0.8: raise RuntimeError(f'music coverage {share:.3f} below 80%')
    write_audio(Path(out).with_suffix('.bed.wav'),whole,MU.SR,os.environ['FFMPEG_BIN'])
    marks=dict(dur_s=dur,words=[(w['start']+lead,w['end']+lead) for w in wd['words']])
    from scene import audio as AU
    ceiling,limit=AU.CEIL_DBTP,AU.LIMIT_DBFS
    AU.CEIL_DBTP,AU.LIMIT_DBFS=min(ceiling,-2.0),min(limit,-2.6)
    try:
        report['mix']=MU.mix_film(film,'chapter score',marks,out=out,kind='longform',supplied=False,music=(whole,dict(report)))
    finally:
        AU.CEIL_DBTP,AU.LIMIT_DBFS=ceiling,limit
    Path(out+'.json').write_text(json.dumps(report,indent=1))
    return report


if __name__=='__main__':
    import cinema
    cinema.worker()
    args=json.loads(Path(sys.argv[1]).read_text())
    score(**args)
