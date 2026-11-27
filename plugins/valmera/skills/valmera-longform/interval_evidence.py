"""Edit remaining heard intervals from the cited originals on the recorded clock."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from idea_exhibits import acquire, read_source


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_passages(ctx, script, gaps):
    """Locate verbatim citations, including dossier facts outside the chapter's selected exhibits."""
    segs = script['script']['segments']
    wanted = {fid for gap in gaps for fid in segs[gap['seg']].get('facts', [])}
    exhibits = script.get('idea_full', {}).get('supply', {}).get('exhibits', [])
    facts = [f for f in script['dossier']['facts'] if f['id'] in wanted]
    root = Path(ctx.run_dir)/'idea-originals'
    root.mkdir(exist_ok=True)
    docs, sources = {}, {}
    for f in facts + [dict(e, id='original-'+str(i)) for i,e in enumerate(exhibits)]:
        url, quote = f.get('url'), ' '.join((f.get('quote') or '').split())
        if not url or not quote or (url,quote) in sources:
            continue
        if url not in docs:
            try:
                pages, _, kind = read_source(acquire(root,url,50_000_000),url)
                docs[url] = [' '.join(page.split()) for page in pages]
            except Exception as exc:
                ctx.log(f'original passage unavailable at {url}: {exc}')
                docs[url] = []
        page = next((i for i,p in enumerate(docs[url]) if quote in p),None)
        if page is not None:
            sources[url,quote] = dict(id=f['id'], url=url, quote=quote, page=page+1,
                                      title=f.get('title') or urlsplit(url).hostname,
                                      author=f.get('author'), date=f.get('date'))
    return {s['id']:s for s in sources.values()}


PROMPT = """Edit only these uncovered spoken intervals of a nonfiction film. Use the supplied original passages.
Create a specific explanatory graphic per interval: a paced close reading for a document, a worked diagram for
an abstract or mathematical explanation. It must show what the words MEAN. Distinguish hypothetical examples,
definitions, conditions, claims and verified outcomes. Never invent a historical scene, quotation, result,
person, number or measurement. A diagram is explicitly labelled editorial. Choose differentiated compositions
across adjacent beats. The sourced excerpt is visible beside your diagram. Copy a short, legible, continuous
verbatim substring from the chosen source including qualifications where needed. You may use the same original
for DIFFERENT concrete explanations. Do not merely change a highlight on an identical shot.
Return JSON {panels:[{id,source,excerpt,heading,lines:[{points:[[x,y],...],accent:true|false}],
dots:[{x,y,label}],labels:[{x,y,text}]}]}. Coordinates are percentages of a landscape diagram panel,
x increases right, y increases DOWN. Labels are centred; use adequate clearance from lines/dots/each other.
Design simple, precise drawings with 2-6 labels, each max 22 characters, and purposeful paths and points
that reveal the reasoning in order. For documentary evidence use short labels showing the actual attribution,
procedure, comparison or published finding. Heading = a specific explanatory caption of at most 6 words.
An illustrative example must read as such. No fake screenshots, decorative paths, invented quote or guessed data.
Read 'heard' on its exact interval, use 'context' only to resolve meaning. Source IDs are strings.
If the sources cannot support a truthful visual, omit that panel. Use verification feedback to repair a failed panel."""


def plan(ctx, root, requests, sources):
    import cinema as C
    material = dict(intervals=requests, sources={k:{f:s[f] for f in ('quote','url','author','date')} for k,s in sources.items()})
    receipt = root/(signature([PROMPT,material])+'.json')
    if receipt.exists():
        return C.read(receipt)
    text,usage = C.wf4.story.writer.ask(PROMPT,json.dumps(material,ensure_ascii=False),
                                         what='WF4 spoken interval illustration',effort='medium',max_tokens=4500)
    result = dict(C.wf4.story.writer.json_block(text),usage=usage)
    C.save(receipt,result)
    return result


def propose(ctx, script, gaps, root, feedback=None):
    sources = source_passages(ctx,script,gaps)
    if not sources:
        return
    pending=[]
    for g in sorted(gaps,key=lambda g:g['start']):
        row=dict(heard=g['heard'],context=g['words'],seconds=round(g['seconds'],2),
                 cited=script['script']['segments'][g['seg']].get('facts',[]),
                 feedback=(feedback or {}).get(str(round(g['start']*30)),[]))
        path=root/('choice-'+signature([row,script['title'],[(s['url'],s['quote']) for s in sources.values() if s['id'] in row['cited']]])+'.json')
        if path.exists():
            import cinema as C
            panel=C.read(path)
            if panel and (src:=sources.get(panel['source'])) and panel['excerpt'] in src['quote']:
                yield g,src,panel
                continue
        pending.append((g,row,path))
    for offset in range(0,len(pending),8):
        batch=pending[offset:offset+8]
        result=plan(ctx,root,[dict(row,id=i) for i,(_,row,_) in enumerate(batch)],sources)
        for panel in result.get('panels',[]):
            i=panel.get('id')
            if isinstance(i,str) and i.isdecimal():i=int(i)
            src=sources.get(panel.get('source'))
            if type(i) is int and 0<=i<len(batch) and src and panel.get('excerpt') and panel['excerpt'] in src['quote']:
                import cinema as C
                gap,_,path=batch[i]
                C.save(path,panel)
                yield gap,src,panel


def panel_shots(ctx,script,gaps,clock,feedback):
    import cinema as C
    from evidence_panel import render,fallback_faces
    root=Path(ctx.supply)/'interval-evidence'
    root.mkdir(exist_ok=True)
    shots=[]
    for gap,source,panel in propose(ctx,script,gaps,root,feedback):
        lo,hi=(round(gap[k]*C.FPS) for k in ('start','end'))
        artwork={k:v for k,v in panel.items() if k not in ('id','source')}
        identity=signature([source['url'],artwork])
        faces=fallback_faces(panel,source)
        file=root/(signature([identity,hi-lo,*faces])+'.mp4')
        if not file.exists():
            render(panel,source,hi-lo,file,C.PX.FF)
        credit=dict(url=source['url'],title=source['title'],credit=source['author'],date=source['date'],
                    presentation='Editorial explanatory diagram with verbatim source excerpt',
                    primary_context=[dict(source_url=source['url'],quote=source['quote'])])
        seconds=(hi-lo)/C.FPS
        clip=dict(path=str(file),kind='diagram',graphic_id=identity,w=1280,h=720,
                  seconds=seconds,time_range=[0,seconds],source=credit,shows=panel['heading'])
        spoken=[w for w in clock.words if w['t0']<hi/C.FPS and w['t1']>lo/C.FPS]
        context=' '.join(script['script']['segments'][i]['say'] for i in dict.fromkeys(w['seg'] for w in spoken))
        shots.append(dict(seg=gap['seg'],f=[lo,hi],src=[0,seconds],clip=clip,
                          say=gap['heard'] or gap['words'],context=context or gap['words']))
    return shots


def reviewed_edit(ctx,script,ft,words):
    """Keep good pairings on their anchors. Route the actual uncovered frames to an editor."""
    import cinema as C
    from timeline_repair import retain,identity
    sc=script['script']
    clock=C.F.Clock(str(words),sc)
    path=Path(ctx.supply)/'footage/footage.json'
    root=Path(ctx.supply)/'interval-evidence';root.mkdir(exist_ok=True)
    history=root/'feedback.json'
    feedback=C.read(history) if history.exists() else {}
    tried=set()
    while True:
        try:
            shots,clock,_=C.schedule(sc,words,ft)
            gaps=[]
        except C.CoverageError as partial:
            shots,gaps=partial.shots,partial.gaps
        # New reviews use the real timing, even for shots discovered on a provisional clock.
        # Include any decodable archive choices before spending on illustrations.
        ctx.log(f'recorded edit: {len(shots)} source pairings, {len(gaps)} uncovered intervals')
        report=C.source_review(ctx,shots,required=False)
        retain(ft,shots,clock,report)
        for i in report['rejected']:
            row=shots[i]
            evidence=report['evidence'].get(str(i),report['evidence'].get(i,{}))
            key=str(row['f'][0])
            note=dict(heard=row['say'],picture=row['clip'].get('shows'),why=evidence.get('reason'))
            if note not in feedback.setdefault(key,[]):feedback[key].append(note)
        C.save(history,feedback)
        C.save(path,ft)
        if not gaps and report['ok']:return shots,clock
        if report['rejected'] or report['missing']:
            # Replacement slots are word-local. A failed proposal must not override
            # retain's rejection and force the very same candidate back in.
            rejected={(shots[i]['seg'],identity(shots[i]['clip'])) for i in report['rejected']+report['missing']}
            for a in ft['editorial'].get('assignments',[]):
                a['candidates']=[c for c in a['candidates'] if (a['seg'],identity(c)) not in rejected]
            try:C.schedule(sc,words,ft)
            except C.CoverageError as remaining:gaps=remaining.gaps
            else:continue
        # A sub-word boundary can leave one frame between two checked cards.
        # Review the complete adjacent picture with that sliver attached; a
        # one-frame insert would be unreadable and buys no new evidence.
        slivers=[g for g in gaps if g['seconds']<.5]
        if slivers:
            from timeline_repair import continue_still
            if continue_still(ctx,script,ft,C.CoverageError(slivers,shots),words,max_hold=7.8):
                C.save(path,ft)
                continue
        # A long gap comprises multiple readable beats even without prior shot boundaries.
        beats=[]
        for g in gaps:
            start,end=(round(g[k]*C.FPS) for k in ('start','end'))
            count=(end-start+233)//234
            edges=[start+round(i*(end-start)/count) for i in range(count+1)]
            for a,b in zip(edges,edges[1:]):
                heard=' '.join(w['w'] for w in clock.words if w['t0']<b/C.FPS and w['t1']>a/C.FPS)
                beats.append(dict(g,start=a/C.FPS,end=b/C.FPS,seconds=(b-a)/C.FPS,heard=heard))
        ctx.log(f'editing {len(beats)} exact heard intervals ({sum(g["seconds"] for g in beats):.1f}s); {len(ft["editorial"]["pins"])} accepted shots retained')
        request=signature([beats,feedback])
        if request in tried:
            # No new evidence from the retained originals. The archive researcher
            # receives only these words and the rejected source observations.
            from timeline_repair import research
            research(ctx,script,ft,C.CoverageError(beats,shots),words=words)
            C.save(path,ft)
            from timeline_repair import repair
            return repair(ctx,script,ft,words)
        tried.add(request)
        panels=panel_shots(ctx,script,beats,clock,feedback)
        verdict=C.source_review(ctx,panels,required=False) if panels else dict(evidence={})
        from timeline_repair import anchor
        for i,row in enumerate(panels):
            result=verdict['evidence'].get(str(i),verdict['evidence'].get(i,{}))
            if result.get('matches') is True:
                ft['editorial']['pins'].append(dict(seg=row['seg'],clip=row['clip'],src=row['src'],
                                                    anchors=[anchor(clock,f/C.FPS) for f in row['f']]))
                credit=dict(line=row['seg'],**row['clip']['source'])
                if credit not in ft['credits']:ft['credits'].append(credit)
            else:
                note=dict(heard=row['say'],picture=row['clip']['shows'],why=result.get('reason'))
                dest=feedback.setdefault(str(row['f'][0]),[])
                if note not in dest:dest.append(note)
        C.save(history,feedback)
        C.save(path,ft)
