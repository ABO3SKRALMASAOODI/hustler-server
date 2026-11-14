"""Preserve checked picture/word pairings; repair only unverified intervals."""
import math
from pathlib import Path


def identity(c):
    return c['path']+':'+str(c['time_range'])


def anchor(clock,t):
    if t<1/60:return dict(edge='start')
    if abs(t-clock.dur)<.05:return dict(edge='end')
    rows=[];ordinals={}
    for word in clock.words:
        seg=word['seg'];ordinal=ordinals.get(seg,0);ordinals[seg]=ordinal+1
        for field in ('t0','t1'):rows.append((abs(word[field]-t),seg,ordinal,field,t-word[field]))
    _,seg,ordinal,field,offset=min(rows)
    return dict(seg=seg,ordinal=ordinal,field=field,offset=offset)


def resolve(clock,a):
    if a.get('edge')=='start':return 0.
    if a.get('edge')=='end':return math.ceil(clock.dur*30)/30
    word=clock.seg_words(a['seg'])[a['ordinal']]
    return word[a['field']]+a['offset']


def retain(ft,shots,clock,report):
    """Rejected material stays in the bin for other genuinely fitting words."""
    editorial=ft.setdefault('editorial',dict(pins=[],rejected=[],bank=[]))
    bank={identity(c):c for c in editorial['bank']}
    for row in ft['lines']:
        for c in row['clips']:bank[identity(c)]=c
    editorial['bank']=list(bank.values())
    rejected={(r['seg'],r['clip']) for r in editorial['rejected']}
    pins=[]
    for i,row in enumerate(shots):
        if row.get('real_voice'):continue
        verdict=report['evidence'].get(str(i),report['evidence'].get(i,{}))
        if verdict.get('matches') is True:
            pins.append(dict(seg=row['seg'],clip=row['clip'],src=row['src'],
                             anchors=[anchor(clock,f/30) for f in row['f']]))
        else:
            key=(row['seg'],identity(row['clip']))
            if key not in rejected:
                editorial['rejected'].append(dict(seg=row['seg'],clip=key[1],words=row['say'],why=verdict.get('reason','No visual verdict')))
                rejected.add(key)
    editorial['pins']=pins
    for line in ft['lines']:
        line['clips']=[c for c in line['clips'] if (line['i'],identity(c)) not in rejected]


def repair(ctx,script,ft,words,rounds=3):
    import cinema as C
    from evidence_allocate import allocate
    path=Path(ctx.supply)/'footage/footage.json'
    for attempt in range(rounds):
        try:shots,clock,_=C.schedule(script['script'],words,ft)
        except C.CoverageError as failure:
            allocate(ctx,script,ft,failure);C.save(path,ft)
            shots,clock,_=C.schedule(script['script'],words,ft)
        report=C.source_review(ctx,shots,required=False)
        if report['ok']:return shots,clock
        retain(ft,shots,clock,report);C.save(path,ft)
        ctx.log(f"picture timing: {len(report['rejected'])} mismatches; retaining {len(ft['editorial']['pins'])} checked intervals")
    raise ValueError('source picture timing remains unverified after bounded repairs')
