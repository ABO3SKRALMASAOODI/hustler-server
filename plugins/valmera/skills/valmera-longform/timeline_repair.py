"""Preserve checked picture/word pairings; repair only unverified intervals."""
import math
import hashlib,json
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


def research(ctx,script,ft,failure,merge=True):
    """A failed literal match triggers new evidence research, not a looser gate."""
    import cinema as C
    import footage_box
    rows=[]
    for gap in failure.gaps:
        i=gap['seg'];segment=script['script']['segments'][i]
        rejected=[r['why'] for r in ft.get('editorial',{}).get('rejected',[]) if r['seg']==i]
        rows.append(dict(i=i,say=gap['heard'] or gap['words'],dur=gap['seconds']+1,pictures=segment.get('pictures',[]),
                         visual='Show the correct named people, location or hardware in its accurate state. Truthful context may support an unfilmed action, '
                         'but must never imply a different mission, component or repair state is that event. Prior rejected views: '+'; '.join(dict.fromkeys(rejected))))
    # A segment can have several gaps; research each as a distinct shot request.
    combined={}
    for row in rows:
        if row['i'] in combined:
            combined[row['i']]['say']+=' '+row['say'];combined[row['i']]['dur']+=row['dur']
        else:combined[row['i']]=row
    avoid=[dict(url=r['clip']['source']['url'],time_range=[r['clip']['time_range'][0]+t for t in r['src']]) for r in failure.shots]
    known=list({c['source']['url']:c['source'] for line in ft['lines'] for c in line['clips'] if c.get('kind')!='image'}.values())
    verifier='wf4-source-and-encoded-picture-check'
    key=hashlib.sha256(json.dumps(dict(rows=rows,avoid=avoid,verification_owner=verifier),sort_keys=True).encode()).hexdigest()[:12]
    dest=Path(ctx.supply)/'footage'/('timing-research-'+key)
    ctx.log(f'exact picture research: {len(combined)} lines in {dest.name}')
    extra=footage_box.fetch(dict(lines=list(combined.values()),named=script['dossier']['named'],about=script['title'],
                                 avoid=avoid,known_sources=known,want_voices=False,verification_owner=verifier),dest,ctx.log)
    if merge:C.add_footage(ft,extra,dest)
    return dest


def release_borrowed_images(ft,failure):
    """A broad checked line must not monopolize a scarce line's only picture."""
    by={r['i']:r['clips'] for r in ft['lines']}
    wanted={}
    for gap in failure.gaps:
        for c in by.get(gap['seg'],[]):
            if c.get('kind')=='image':wanted.setdefault(c['content_hash'],set()).add(gap['seg'])
    editorial=ft.get('editorial',{});keep=[];released=[]
    for pin in editorial.get('pins',[]):
        c=pin['clip'];claimants=wanted.get(c.get('content_hash'),set())
        if c.get('kind')=='image' and claimants and pin['seg'] not in claimants:released.append(pin)
        else:keep.append(pin)
    if released:editorial['pins']=keep
    return released


def repair(ctx,script,ft,words,rounds=3):
    import cinema as C
    from evidence_allocate import allocate
    path=Path(ctx.supply)/'footage/footage.json'
    for attempt in range(rounds):
        # A nearly covered edit can still contain wrong pictures. Review those
        # now, so research sees every unsupported interval, not just a few
        # fractional gaps that would otherwise block the first visual review.
        incomplete=False
        try:initial,initial_clock,_=C.schedule(script['script'],words,ft)
        except C.CoverageError as partial:
            initial=partial.shots;initial_clock=C.F.Clock(str(words) if isinstance(words,Path) else words,script['script'])
            incomplete=True
        if initial:
            initial_report=C.source_review(ctx,initial,required=False)
            if initial_report['ok'] and not incomplete:return initial,initial_clock
            retain(ft,initial,initial_clock,initial_report);C.save(path,ft)
            ctx.log(f"partial edit review: {len(initial_report['rejected'])} mismatches; retaining {len(ft['editorial']['pins'])} checked intervals before research")
        try:shots,clock,_=C.schedule(script['script'],words,ft)
        except C.CoverageError as failure:
            if release_borrowed_images(ft,failure):
                C.save(path,ft)
                try:shots,clock,_=C.schedule(script['script'],words,ft)
                except C.CoverageError as rebalanced:failure=rebalanced
                else:
                    report=C.source_review(ctx,shots,required=False)
                    if report['ok']:return shots,clock
                    retain(ft,shots,clock,report);C.save(path,ft)
                    continue
            allocate(ctx,script,ft,failure,words);C.save(path,ft)
            try:shots,clock,_=C.schedule(script['script'],words,ft)
            except C.CoverageError as uncovered:
                research(ctx,script,ft,uncovered);C.save(path,ft)
                try:shots,clock,_=C.schedule(script['script'],words,ft)
                except C.CoverageError as rare:
                    from evidence_web import supply as exact_archive
                    from document_evidence import supply as original_figures
                    exact_archive(ctx,script,ft,rare.gaps);C.save(path,ft)
                    try:shots,clock,_=C.schedule(script['script'],words,ft)
                    except C.CoverageError as documented:
                        original_figures(ctx,script,ft,documented.gaps);C.save(path,ft)
                        shots,clock,_=C.schedule(script['script'],words,ft)
        report=C.source_review(ctx,shots,required=False)
        if report['ok']:return shots,clock
        retain(ft,shots,clock,report);C.save(path,ft)
        ctx.log(f"picture timing: {len(report['rejected'])} mismatches; retaining {len(ft['editorial']['pins'])} checked intervals")
    raise ValueError('source picture timing remains unverified after bounded repairs')
