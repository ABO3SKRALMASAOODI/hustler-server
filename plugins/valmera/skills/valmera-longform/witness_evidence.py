"""Attribute an interview from source text; never identify or compare faces."""
from pathlib import Path
import hashlib,json

SYSTEM='''Verify documentary interview attribution without identifying or comparing faces or voices.
The requested person's name is a claim. Establish attribution from readable names in the frames,
an explicit spoken introduction in the supplied transcript, or an unambiguous original interview
title/caption identifying its sole interview subject. Quote the exact attribution text used.
A compilation title naming several people, an unrelated mention, or a requested label is insufficient.
Do not infer identity from appearance, clothing, setting, or how a voice sounds.
Separately check whether each frame shows an on-camera interview speaker rather than unrelated
B-roll, a photograph, an audience or a presenter introducing somebody else. This is a presentation
check, not facial recognition. Uncertain attribution fails.
Return JSON {attributed:bool,attribution_text:string,on_camera:[bool,bool,bool],why:string}.'''


def verify(source,person,t0,t1,out):
    from footage import ask,shots
    payload=dict(person=person,source={k:source.get(k) for k in ('url','title','channel')},
                 introduction=[p for p in source.get('transcript',[]) if p['t0']<120],
                 passage=[p for p in source.get('transcript',[]) if p['t0']<t1+4 and p['t1']>t0-4],range=[t0,t1])
    key=hashlib.sha256((json.dumps(payload,sort_keys=True)+SYSTEM).encode()).hexdigest()[:20]
    root=Path(out)/'witness-attribution'/key;root.mkdir(parents=True,exist_ok=True);record=root/'verdict.json'
    if record.exists():return json.loads(record.read_text())
    frames=shots.frames(source['proxy'],[t0+(t1-t0)*q for q in (.1,.5,.9)],str(root))
    if len(frames)!=3:return dict(ok=False,on_camera=[],why='Missing source frames')
    content=[dict(type='text',text=json.dumps(payload))]
    for t,frame in sorted(frames.items()):content.extend([dict(type='text',text=f'Frame at {t} seconds'),ask.image_block(frame)])
    text,usd=ask.ask(SYSTEM,content,'wf4_witness_attribution',max_tokens=1000)
    result=ask.json_obj(text)
    result['ok']=(result.get('attributed') is True and bool(str(result.get('attribution_text') or '').strip())
                  and len(result.get('on_camera',[]))==3 and sum(x is True for x in result['on_camera'])>=2)
    result.update(usd=usd,source=payload['source'],person=person,time_range=[t0,t1],method='source text attribution; on-camera presentation, no facial comparison')
    record.write_text(json.dumps(result,indent=1));return result
