"""Offer genuinely unused archive material to the exact words still uncovered."""
from pathlib import Path
import hashlib,json


def allocate(ctx,script,ft,failure,words=None):
    import cinema as C
    from wf4 import story
    used={}
    for row in failure.shots:
        c=row['clip'];url=c['source']['url']
        if c.get('kind')=='image':
            used[url]=[(0,c['seconds'])];used['image:'+c['content_hash']]=[(0,c['seconds'])]
        else:
            a=c['time_range'][0]
            used.setdefault(url,[]).append((a+row['src'][0],a+row['src'][1]))
    available={}
    for line in ft['lines']+[dict(clips=ft.get('editorial',{}).get('bank',[]))]:
        for c in line['clips']:
            if C.unused_seconds(used,c)<.05:continue
            key=(c['path'],tuple(c['time_range']))
            available[key]=c
    candidates=list(available.values())
    rows=[dict(i=i,available_s=C.unused_seconds(used,c),shows=c.get('shows'),subject=c.get('subject') or c.get('name'),
               source=c['source'],kind=c.get('kind','video')) for i,c in enumerate(candidates)]
    from timeline_repair import identity
    rejected=ft.get('editorial',{}).get('rejected',[])
    for i,c in enumerate(candidates):rows[i]['rejected']=[r for r in rejected if r['clip']==identity(c)]
    payload=dict(version=2,gaps=[dict(g,id=i) for i,g in enumerate(failure.gaps)],candidates=rows,title=script['title'])
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    root=Path(ctx.supply)/'evidence-allocation';root.mkdir(exist_ok=True);path=root/(key+'.json')
    if path.exists():allocation=C.read(path)
    else:
        text,usage=story.writer.ask('You are allocating UNUSED archive material to the final uncovered words of a documentary. '
            'The heard field is what is actually said during the gap; words supplies surrounding context. '
            'Pick only candidates that literally fit the heard words, correct person, event and era. A rejection describes the earlier words under that picture: '
            'never repeat that mismatch. A genuine picture may fit a DIFFERENT clause of that segment, but a wrong identity or repair state remains wrong. '
            'Read rejection reasons as visual observations, including when the original source metadata was wrong. Ground tests can explain hardware but cannot pose as a flight event. '
            'A phrase about the delayed crew launch can use the actual crew launch vehicle. Never fill a gap with unrelated generic pictures. '
            'No candidate has been used; the allocator will reserve its original frames and still perform the final visual check. '
            'Return JSON {allocations:[{gap:gap id,candidates:[indexes in display order],why}]}. Keep different gaps of the same segment separate. '
            'Offer several suitable alternatives when available. Empty candidates is better than a false match.',
            json.dumps(payload),effort='high',max_tokens=3500,what='WF4 unused evidence allocation')
        allocation=story.writer.json_block(text);allocation['usage']=usage;C.save(path,allocation)
    from timeline_repair import anchor
    if words is None:words=Path(ctx.supply)/'narration/words.json'
    clock=C.F.Clock(str(words) if isinstance(words,Path) else words,script['script'])
    by={r['i']:r for r in ft['lines']};assigned=[]
    for row in allocation['allocations']:
        if not isinstance(row['gap'],int) or not 0<=row['gap']<len(failure.gaps):raise ValueError('invalid evidence gap index')
        gap=failure.gaps[row['gap']];selected=[]
        for i in row['candidates']:
            if not isinstance(i,int) or not 0<=i<len(candidates):raise ValueError('invalid evidence candidate index')
            selected.append(candidates[i]);by[gap['seg']]['clips'].append(candidates[i])
        if selected:assigned.append(dict(seg=gap['seg'],anchors=[anchor(clock,gap[k]) for k in ('start','end')],candidates=selected))
    ft.setdefault('editorial',dict(pins=[],rejected=[],bank=[]))['assignments']=assigned
    ft['lines']=list(by.values())
