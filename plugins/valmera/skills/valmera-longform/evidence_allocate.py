"""Offer genuinely unused archive material to the exact words still uncovered."""
from pathlib import Path
import hashlib,json


def allocate(ctx,script,ft,failure):
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
    for line in ft['lines']:
        for c in line['clips']:
            if C.unused_seconds(used,c)<.05:continue
            key=(c['path'],tuple(c['time_range']))
            available[key]=c
    candidates=list(available.values())
    rows=[dict(i=i,available_s=C.unused_seconds(used,c),shows=c.get('shows'),subject=c.get('subject') or c.get('name'),
               source=c['source'],kind=c.get('kind','video')) for i,c in enumerate(candidates)]
    payload=dict(gaps=failure.gaps,candidates=rows,title=script['title'])
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    root=Path(ctx.supply)/'evidence-allocation';root.mkdir(exist_ok=True);path=root/(key+'.json')
    if path.exists():allocation=C.read(path)
    else:
        text,usage=story.writer.ask('You are allocating UNUSED archive material to the final uncovered words of a documentary. '
            'The heard field is what is actually said during the gap; words supplies surrounding context. '
            'Pick only candidates that literally fit those words, correct person, event and era. Ground tests can explain hardware but cannot pose as a flight event. '
            'A phrase about the delayed crew launch can use the actual crew launch vehicle. Never fill a gap with unrelated generic pictures. '
            'No candidate has been used; the allocator will reserve its original frames and still perform the final visual check. '
            'Return JSON {lines:[{seg,candidates:[indexes],why}]}. Offer several suitable alternatives when available. Empty candidates is better than a false match.',
            json.dumps(payload),effort='high',max_tokens=3500,what='WF4 unused evidence allocation')
        allocation=story.writer.json_block(text);allocation['usage']=usage;C.save(path,allocation)
    by={r['i']:r for r in ft['lines']};allowed={g['seg'] for g in failure.gaps}
    for row in allocation['lines']:
        if row['seg'] not in allowed:continue
        for i in row['candidates']:
            if not isinstance(i,int) or not 0<=i<len(candidates):raise ValueError('invalid evidence candidate index')
            by[row['seg']]['clips'].append(candidates[i])
    ft['lines']=list(by.values())
