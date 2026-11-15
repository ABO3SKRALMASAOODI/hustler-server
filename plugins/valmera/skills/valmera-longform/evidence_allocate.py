"""Offer genuinely unused archive material to the exact words still uncovered."""
from pathlib import Path
import hashlib,json
from model_payload import source_table


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
    # A scarce evidence image may already illustrate a broader passage. Offer
    # an atomic swap, never a second use: the former passage must receive an
    # unused replacement in the same proposal.
    occupied={}
    for shot in failure.shots:
        c=shot['clip']
        if c.get('kind')=='image':occupied.setdefault(c['content_hash'],[]).append(shot)
    borrowed={}
    for uses in occupied.values():
        if len(uses)!=1:continue
        borrowed[len(candidates)]=uses[0];candidates.append(uses[0]['clip'])
    rows=[dict(i=i,available_s=C.unused_seconds(used,c),shows=c.get('shows'),subject=c.get('subject') or c.get('name'),
               source=c['source'],kind=c.get('kind','video')) for i,c in enumerate(candidates)]
    for i,shot in borrowed.items():
        rows[i].update(available_s=0,move_only=True,seconds_if_moved=shot['clip']['seconds'],
                       current_use=dict(seg=shot['seg'],words=shot['say'],seconds=(shot['f'][1]-shot['f'][0])/30))
    from timeline_repair import identity
    rejected=ft.get('editorial',{}).get('rejected',[])
    for i,c in enumerate(candidates):rows[i]['rejected']=[r for r in rejected if r['clip']==identity(c)]
    payload=dict(version=3,gaps=[dict(g,id=i) for i,g in enumerate(failure.gaps)],candidates=rows,title=script['title'])
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    root=Path(ctx.supply)/'evidence-allocation';root.mkdir(exist_ok=True);path=root/(key+'.json')
    if path.exists():allocation=C.read(path)
    else:
        text,usage=story.writer.ask('You are allocating archive material to the final uncovered words of a documentary, with no repeated image or source interval. '
            'The heard field is what is actually said during the gap; words supplies surrounding context. '
            'Pick only candidates that literally fit the heard words, correct person, event and era. A rejection describes the earlier words under that picture: '
            'never repeat that mismatch. A genuine picture may fit a DIFFERENT clause of that segment, but a wrong identity or repair state remains wrong. '
            'Read rejection reasons as visual observations, including when the original source metadata was wrong. Ground tests can explain hardware but cannot pose as a flight event. '
            'A phrase about the delayed crew launch can use the actual crew launch vehicle. Never fill a gap with unrelated generic pictures. '
            'Ordinary candidates are unused. A move_only image is already displayed at current_use: you may move it to a gap ONLY if '
            'the same proposal gives unused replacements that truthfully cover all of its former words and duration. Do not trade one unsupported interval for another. '
            'Both new pairings will be checked; the allocator reserves original frames so the moved image cannot appear twice. '
            'Return JSON {allocations:[{gap:gap id,candidates:[indexes in display order],why}], '
            'swaps:[{move:move_only candidate index,replacements:[UNUSED candidate indexes in display order],why}]}. '
            'Use swaps only when a specific later passage needs an image already used as broader context. No recursive swaps. Keep different gaps of the same segment separate. '
            'Offer several suitable alternatives when available. Empty candidates is better than a false match.',
            json.dumps(source_table(payload)),effort='high',max_tokens=3500,what='WF4 unused evidence allocation')
        allocation=story.writer.json_block(text);allocation['usage']=usage;C.save(path,allocation)
    from timeline_repair import anchor
    if words is None:words=Path(ctx.supply)/'narration/words.json'
    clock=C.F.Clock(str(words) if isinstance(words,Path) else words,script['script'])
    by={r['i']:r for r in ft['lines']};assigned=[];moved=set()
    editorial=ft.setdefault('editorial',dict(pins=[],rejected=[],bank=[]))
    for swap in allocation.get('swaps',[]):
        i=swap['move'];indexes=swap['replacements']
        if i not in borrowed or i in moved or not indexes or any(not isinstance(k,int) or not 0<=k<len(candidates) or k in borrowed for k in indexes):
            raise ValueError('invalid evidence image swap')
        if not any(i in row['candidates'] for row in allocation['allocations']):raise ValueError('moved image has no destination')
        shot=borrowed[i];selected=[candidates[k] for k in indexes]
        capacity=sum(min(7.8,C.unused_seconds(used,c)) for c in {identity(c):c for c in selected}.values())
        if capacity+.04<(shot['f'][1]-shot['f'][0])/30:raise ValueError('image swap leaves its former passage uncovered')
        by[shot['seg']]['clips'].extend(selected)
        assigned.append(dict(seg=shot['seg'],anchors=[anchor(clock,f/30) for f in shot['f']],candidates=selected))
        editorial['pins']=[p for p in editorial['pins'] if p['clip'].get('content_hash')!=shot['clip']['content_hash']]
        moved.add(i)
    for row in allocation['allocations']:
        if not isinstance(row['gap'],int) or not 0<=row['gap']<len(failure.gaps):raise ValueError('invalid evidence gap index')
        gap=failure.gaps[row['gap']];selected=[]
        for i in row['candidates']:
            if not isinstance(i,int) or not 0<=i<len(candidates):raise ValueError('invalid evidence candidate index')
            if i in borrowed and i not in moved:raise ValueError('an occupied image cannot be reused without replacing its original use')
            selected.append(candidates[i]);by[gap['seg']]['clips'].append(candidates[i])
        if selected:assigned.append(dict(seg=gap['seg'],anchors=[anchor(clock,gap[k]) for k in ('start','end')],candidates=selected))
    editorial['assignments']=assigned
    ft['lines']=list(by.values())
