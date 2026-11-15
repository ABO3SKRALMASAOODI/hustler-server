"""Replace archive stills only with unused, independently checked moving views."""
from pathlib import Path
import copy,hashlib,json
from model_payload import source_table


def replacement(ctx,script,ft,words,shot,candidates):
    """An unsuccessful replacement leaves the complete original edit intact."""
    import cinema as C
    from timeline_repair import anchor,resolve
    clock=C.F.Clock(str(words),script['script'])
    a,b=shot['f'];trial=copy.deepcopy(ft);editorial=trial['editorial']
    def bounds(row):return [round(resolve(clock,x)*30) for x in row['anchors']]
    editorial['pins']=[p for p in editorial['pins'] if bounds(p)!=[a,b]]
    editorial['assignments']=[p for p in editorial.get('assignments',[]) if bounds(p)!=[a,b]]
    editorial['assignments'].append(dict(seg=shot['seg'],anchors=[anchor(clock,f/30) for f in (a,b)],candidates=candidates))
    for line in trial['lines']:
        if line['i']==shot['seg']:line['clips'].extend(candidates)
    try:rows,_,_=C.schedule(script['script'],words,trial)
    except C.CoverageError as partial:
        if any(g['start']<b/30 and g['end']>a/30 for g in partial.gaps):return None
        rows=partial.shots
    new=[r for r in rows if r['f'][0]<b and r['f'][1]>a]
    if not new or new[0]['f'][0]!=a or new[-1]['f'][1]!=b:return None
    if any(r['clip'].get('kind')=='image' for r in new):return None
    if any(x['f'][1]!=y['f'][0] for x,y in zip(new,new[1:])):return None
    report=C.source_review(ctx,new,required=False)
    if not report['ok'] or report['moving_share']<1:return None
    for r in new:
        editorial['pins'].append(dict(seg=r['seg'],clip=r['clip'],src=r['src'],anchors=[anchor(clock,f/30) for f in r['f']]))
    return trial


def improve(ctx,script,ft,words,shots,research=True):
    import cinema as C
    from timeline_repair import identity
    from wf4 import story
    stills=[r for r in shots if r['clip'].get('kind')=='image']
    used={}
    for row in shots:
        c=row['clip']
        if c.get('kind')!='image':
            used.setdefault(c['source']['url'],[]).append(tuple(c['time_range'][0]+t for t in row['src']))
    available={}
    for line in ft['lines']+[dict(clips=ft.get('editorial',{}).get('bank',[]))]:
        for c in line['clips']:
            if c.get('kind')!='image' and C.unused_seconds(used,c)>=.5:available[identity(c)]=c
    clips=list(available.values())
    if not stills:return 0
    candidates=[dict(i=i,seconds=C.unused_seconds(used,c),shows=c.get('shows'),source=c['source'],
                     rejected=[r for r in ft['editorial'].get('rejected',[]) if r['clip']==identity(c)]) for i,c in enumerate(clips)]
    slots=[dict(i=i,words=r['say'],context=r.get('context'),seconds=(r['f'][1]-r['f'][0])/30) for i,r in enumerate(stills)]
    payload=dict(version=2,title=script['title'],slots=slots,candidates=candidates,allow_new_research=research)
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    root=Path(ctx.supply)/'moving-alternatives';root.mkdir(exist_ok=True);path=root/(key+'.json')
    if path.exists():proposal=C.read(path)
    else:
        text,use=story.writer.ask('Edit a documentary using truthful original moving footage wherever it illustrates the words better than the current still. '
            'Every candidate is unused footage, but its metadata alone does not prove fit. Respect the recorded rejection reasons: '
            'do not substitute another mission, repair state, component or action. A genuine interior scene can support life in that interior; '
            'an unrelated interview cannot stand for a mechanical action. Pick enough suitable unused seconds to cover the complete slot. '
            'Do not propose a partial replacement or repeat source seconds. Multiple genuinely suitable alternatives are welcome. '
            'No picture target excuses a false match. Return JSON {replacements:[{slot:index,candidates:[indexes],why:string}],research:[slot indexes]}. '
            'Leave slots without suitable existing moving material alone. When new research is allowed, select at most six such slots '
            'whose actual event or activity is likely to survive in moving archive. Prefer well-documented observable work and daily life; '
            'do not request nonexistent cameras for unfilmed mechanisms or replace a precise technical figure with generic activity. '
            'Every proposed replacement will be checked on its actual spoken interval.',
            json.dumps(source_table(payload)),effort='high',max_tokens=5000,what='WF4 moving archive alternatives')
        proposal=story.writer.json_block(text);proposal['usage']=use;C.save(path,proposal)
    accepted=[]
    for row in proposal['replacements']:
        i=row['slot'];indexes=row['candidates']
        if not isinstance(i,int) or not 0<=i<len(stills) or any(not isinstance(j,int) or not 0<=j<len(clips) for j in indexes):
            raise ValueError('invalid moving archive alternative')
        if i in accepted:continue
        trial=replacement(ctx,script,ft,words,stills[i],[clips[j] for j in indexes])
        if trial is not None:
            ft.clear();ft.update(trial);accepted.append(i)
            C.save(Path(ctx.supply)/'footage/footage.json',ft)
    C.save(root/(key+'-checked.json'),dict(accepted_slots=accepted,proposed=len(proposal['replacements'])))
    ctx.log(f'moving archive replacements: {len(accepted)} independently checked of {len(proposal["replacements"])} proposed')
    if research:
        try:current,clock,_=C.schedule(script['script'],words,ft)
        except C.CoverageError as partial:
            current=partial.shots;clock=C.F.Clock(str(words),script['script'])
        report=C.source_review(ctx,current,required=False)
        testimony=0 if C.read(words).get('voices') else sum(v['seconds'] for v in ft.get('voices',[]))
        covered=sum((r['f'][1]-r['f'][0])/30 for r in current)
        planning_share=(covered*report['moving_share']+testimony)/(clock.dur+testimony)
        requested=proposal.get('research',[])
        if len(requested)>6 or any(not isinstance(i,int) or not 0<=i<len(stills) for i in requested):
            raise ValueError('invalid moving archive research slots')
        gaps=[dict(seg=stills[i]['seg'],seconds=slots[i]['seconds'],heard=stills[i]['say'],
                   words=script['script']['segments'][stills[i]['seg']]['say'],
                   start=stills[i]['f'][0]/30,end=stills[i]['f'][1]/30) for i in dict.fromkeys(requested) if i not in accepted]
        if planning_share<.55 and gaps:
            from timeline_repair import research as find_moving
            find_moving(ctx,script,ft,C.CoverageError(gaps,current));C.save(Path(ctx.supply)/'footage/footage.json',ft)
            return len(accepted)+improve(ctx,script,ft,words,current,research=False)
    return len(accepted)
