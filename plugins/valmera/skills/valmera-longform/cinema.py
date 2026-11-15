"""WF4's footage-first film. Story chapters own the picture and sound clocks.

The old Ledger builder remains a legacy renderer. New runs use this module:
Easton through the shared reader, worker/footage per line, unique source ranges,
SND3, MUSIC2 per actual chapter, and hard release gates. Insufficient evidence
stops a resumable run; it never silently delivers the old stills-only fallback.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent/'valmera-stories'), str(HERE.parent/'_shell')]
import pictures as PX
import film as F
import chain
import brand
import wf4
import footage_box

FPS, W, H = 30, 1920, 1080


def read(p):
    return json.loads(Path(p).read_text())


def digest(p):
    with open(p,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def save(p, data):
    p=Path(p);p.parent.mkdir(parents=True, exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1));os.replace(tmp,p)


def worker():
    p = PX.worker_dir()
    # The shell also has an llm.py. PYTHONPATH may already contain the worker
    # later in the list; it must still precede the shell for worker imports.
    while p in sys.path:sys.path.remove(p)
    sys.path.insert(0,p)
    loaded=sys.modules.get('llm')
    if loaded is not None and Path(getattr(loaded,'__file__','')).resolve()!=(Path(p)/'llm.py').resolve():
        # Idea selection may already have loaded the shell helper. Existing
        # shell callers keep their module object; worker imports get theirs.
        del sys.modules['llm']
    os.environ['PATH'] = str(Path(PX.FF).parent)+os.pathsep+os.environ.get('PATH','')
    os.environ['FFMPEG_BIN'] = PX.FF
    if os.environ.get('WF4_SPEND_LOG'):
        from local_meter import install
        install(os.environ['WF4_SPEND_LOG'])
    return p


def footage(ctx, script):
    """Each chapter is a bounded search; every previous source range stays reserved."""
    sc = script['script']
    root = Path(ctx.supply)/'footage'
    if (root/'footage.json').exists():return read(root/'footage.json')
    rows, voices, credits, avoided, spends = [], [], [], [], []
    for ci, ch in enumerate(sc['chapters']):
        lines = [dict(i=i, say=s['say'], pictures=s.get('pictures',[]), dur=len(s['say'].split())/2.5+1)
                 for i,s in enumerate(sc['segments']) if s['chapter']==ch['id']]
        dest = root/f'chapter-{ci+1}'
        payload = dict(lines=lines, named=script['dossier']['named'],
                       about=sc['title']+': '+ch['title']+'. Landscape documentary; archive moving footage on every line.', avoid=avoided)
        # The Mac's repeated YouTube wall is established in this week's context.
        # Use the product's same engine on its supported box network.
        ctx.log(f"footage chapter {ci+1}/{len(sc['chapters'])}: {len(lines)} lines")
        r = footage_box.fetch(payload, dest, ctx.log)
        for ln in r['lines']:
            for c in ln['clips']:
                c['path'] = str(dest/c['file'])
                c['w'],c['h'] = W,H
                avoided.append(dict(url=c['source']['url'],time_range=c['time_range']))
            rows.append(ln)
        for v in r.get('voices',[]):
            if not v.get('file') or not v.get('wav'):continue
            if any(v['after']==q['after'] or (v['source']['url']==q['source']['url'] and min(v['time_range'][1],q['time_range'][1])>max(v['time_range'][0],q['time_range'][0])) for q in voices):continue
            for k in ('file','wav'):
                if v.get(k): v[k]=str(dest/v[k])
            voices.append(v)
            avoided.append(dict(url=v['source']['url'],time_range=v['time_range']))
        credits.extend(r.get('credits',[])); spends.append(r.get('spend',{}))
    result = dict(lines=rows, voices=voices, credits=credits, spend=spends)
    prefill_footage(ctx,script,result)
    save(root/'footage.json',result)
    return result


def prefill_footage(ctx,script,ft,pass_id=None):
    """Research obvious shortages while the narrator is still being made.
    This estimate only requests supply; the actual word clock decides coverage.
    """
    sc=script['script'];by={r['i']:r for r in ft['lines']}
    avoid=[dict(url=c['source']['url'],time_range=c['time_range']) for ln in ft['lines'] for c in ln['clips']]
    avoid.extend(dict(url=v['source']['url'],time_range=v['time_range']) for v in ft['voices'])
    for ch in sc['chapters']:
        lines=[]
        for i,s in enumerate(sc['segments']):
            if s['chapter']!=ch['id']:continue
            available=sum(min(7.8,c['seconds']) for c in by.get(i,{}).get('clips',[]))
            missing=len(s['say'].split())/2.8+.4-available
            if missing>=1.5:lines.append(dict(i=i,say=s['say'],pictures=s.get('pictures',[]),dur=missing+2))
        if not lines:continue
        dest=Path(ctx.supply)/'footage'/('prefill-'+ch['id']+('-'+pass_id if pass_id else ''))
        ctx.log(f"archive coverage research {ch['id']}: {len(lines)} lines")
        known=list({c['source']['url']:c['source'] for ln in ft['lines'] for c in ln['clips']}.values())
        extra=footage_box.fetch(dict(lines=lines,named=script['dossier']['named'],about=sc['title']+': '+ch['title']+'. Find additional literal archive evidence; previous source seconds are reserved.',avoid=avoid,known_sources=known,want_voices=not bool(ft.get('voices'))),dest,ctx.log)
        add_footage(ft,extra,dest)
        avoid.extend(dict(url=c['source']['url'],time_range=c['time_range']) for ln in extra['lines'] for c in ln['clips'])
        avoid.extend(dict(url=v['source']['url'],time_range=v['time_range']) for v in extra.get('voices',[]))


def verify_structure(ctx, script, attempt=0):
    """Model interpretation, never a word rule: point to the actual change in each chapter."""
    sc=script['script']
    report=Path(ctx.run_dir)/'structure.json'
    if report.exists():
        tagged=read(report)
    else:
        text,use=wf4.story.writer.ask('Read this documentary as an editor. Find the consequential factual reversal in EACH chapter. '
            'It must change what the audience expects, not merely announce a question. Point to an existing segment; never invent one. '
            'Use the supplied zero-based index for each segment. Judge whether its first twenty-five words create concrete curiosity or danger. Return JSON {hook:bool, '
            'chapters:[{id,turn_segment:int|null,why:string}], strongest_chapter:string}. A null turn is a failure, not permission to invent.',
            json.dumps(dict(sc,segments=[dict(s,index=i) for i,s in enumerate(sc['segments'])]),ensure_ascii=False),effort='high',max_tokens=4000,what='WF4 structure evidence')
        tagged=wf4.story.writer.json_block(text); tagged['usage']=use
        save(report,tagged)
    chs={c['id'] for c in sc['chapters']}
    rows={r['id']:r for r in tagged.get('chapters',[])}
    if tagged.get('hook') is not True or set(rows)!=chs:
        raise ValueError('the story lacks a verified hook or chapter turn')
    missing=[cid for cid,r in rows.items() if not isinstance(r.get('turn_segment'),int)]
    if missing and attempt<2:
        # A coda can belong to the chapter whose action earned it. Let the editor
        # resolve the structure; never manufacture a twist or change a fact.
        text,use=wf4.story.writer.ask('Repair this documentary chapter structure. The independent structural read found chapters without a consequential turn. '
            'You may merge a payoff-only coda into the preceding chapter, or move existing segment boundaries where the story supports it. '
            'Keep every spoken segment exactly once, in its existing order. Do not rewrite or invent facts. Preserve a meaningful multi-chapter story, '
            'never collapse the film just to pass a check. Return JSON {chapters:[{id,title,question,turn,music_mood,pace}], '
            'assignment:[one chapter id per segment, same order],why:string}.',
            json.dumps(dict(script=sc,diagnosis=tagged)),effort='high',max_tokens=5000,what='WF4 chapter repair')
        repair=wf4.story.writer.json_block(text)
        assignment=repair.get('assignment',[]); chapters=repair.get('chapters',[])
        ids=[c['id'] for c in chapters]
        if len(assignment)!=len(sc['segments']) or set(assignment)!=set(ids) or len(ids)<2:
            raise ValueError('chapter repair did not preserve the story')
        runs=[v for i,v in enumerate(assignment) if i==0 or assignment[i-1]!=v]
        if runs!=ids: raise ValueError('chapter repair interleaved the timeline')
        sc['chapters']=chapters
        for s,cid in zip(sc['segments'],assignment):s['chapter']=cid
        save(Path(ctx.run_dir)/f'structure_repair{attempt}.json',dict(repair,usage=use))
        save(Path(ctx.run_dir)/f'structure_rejected{attempt}.json',tagged)
        report.unlink();save(Path(ctx.run_dir)/'script.json',script)
        return verify_structure(ctx,script,attempt+1)
    for cid,r in rows.items():
        i=r.get('turn_segment')
        if not isinstance(i,int) or not 0<=i<len(sc['segments']) or sc['segments'][i]['chapter']!=cid:
            raise ValueError('no factual turn in chapter '+cid)
        sc['segments'][i]['beat']='turn'
        next(c for c in sc['chapters'] if c['id']==cid)['verified_turn_index']=i
    save(Path(ctx.run_dir)/'script.json',script)
    return sc


def guarded_voice(ctx, command, label):
    evidence=Path(ctx.supply)/'memory'/label
    return chain.sh(ctx,[chain.PY,str(HERE/'guarded.py'),'--out',str(evidence),
                         '--label',label,'--',*command])


def splice_voices(ctx,nd,voices,footage_dir):
    job=Path(ctx.supply)/'witness-audio.json'
    save(job,dict(narration_dir=str(nd),voices=voices,footage_dir=str(footage_dir)))
    guarded_voice(ctx,[PX.NARRATE_PY,str(HERE/'witness_audio.py'),str(job)],'wf4-witness-audio-'+ctx.run_id)


def narrated(ctx, ns, nd, reader):
    """Every model/listener process uses the shared locks and 6 GB stop."""
    nd=Path(nd);nd.mkdir(parents=True,exist_ok=True)
    save(nd/'narration_script.json',ns)
    report=nd/'longform_voice.json'
    stage=nd/'read-stage.json'
    same_read=stage.exists() and read(stage).get('script')==ns
    try:
        if not same_read or not report.exists() or not read(report)['after']['ok']:
            guarded_voice(ctx,[PX.NARRATE_PY,reader,'render',str(nd/'narration_script.json'),
                              '--out',str(nd),'--band','04'],'wf4-narration-'+ctx.run_id)
        if not (nd/'check.json').exists():
            guarded_voice(ctx,[PX.NARRATE_PY,PX.NARRATE,'check',str(nd),'--band','04'],
                          'wf4-voice-check-'+ctx.run_id)
        for attempt in range(2):
            checked=read(nd/'check.json')
            if checked.get('gate',{}).get('pass'):break
            if not checked.get('joins',{}).get('with_artefact'):break
            guarded_voice(ctx,[PX.NARRATE_PY,str(HERE/'join_repair.py'),str(nd)],
                          f'wf4-join-repair-{attempt}-'+ctx.run_id)
            guarded_voice(ctx,[PX.NARRATE_PY,PX.NARRATE,'check',str(nd),'--band','04'],
                          f'wf4-join-check-{attempt}-'+ctx.run_id)
        if not read(nd/'check.json').get('gate',{}).get('pass'):
            raise ValueError('shared narration gate remains failed: '+str(nd/'check.json'))
        # With both checkpoints present, the shared adapter only reads metadata.
        return PX.narrate(ns,str(nd),'04',f'wf4-{ctx.run_id}',dict(os.environ),18000,reader)
    except Exception as error:
        ctx.log('narrator stopped: '+str(error))
        save(nd/'failure.json',dict(error=str(error)))
        raise


def add_footage(ft,extra,dest):
    by={ln['i']:ln for ln in ft['lines']}
    for ln in extra['lines']:
        for c in ln['clips']:c.update(path=str(dest/c['file']),w=W,h=H)
        by.setdefault(ln['i'],dict(i=ln['i'],clips=[]))['clips'].extend(ln['clips'])
    ft['lines']=list(by.values());ft['spend'].append(extra.get('spend',{}));ft['credits'].extend(extra.get('credits',[]))
    for v in extra.get('voices',[]):
        if not v.get('file') or not v.get('wav'):continue
        a,b=v['time_range']
        if any(v['after']==q['after'] or (v['source']['url']==q['source']['url'] and min(b,q['time_range'][1])>max(a,q['time_range'][0])) for q in ft['voices']):continue
        ft['voices'].append(dict(v,**{k:str(dest/v[k]) for k in ('file','wav')}))


def witness_voices(ctx,script,ft,additional=False,chapter=None):
    """Seek firsthand testimony separately from mechanism pictures. A picture
    search need not rank an interview highly enough to hear its witness.
    """
    digest_script=dict(lines=[s['say'] for s in script['script']['segments']],
                                purpose=script['script'].get('testimony_purpose'),chapter=chapter)
    signature=hashlib.sha256(json.dumps(digest_script,sort_keys=True).encode()).hexdigest()[:12]
    root=Path(ctx.supply)/'footage'/('witnesses-additional' if additional else 'witnesses')/signature
    root.mkdir(parents=True,exist_ok=True)
    history=[]
    known=list({c['source']['url']:c['source'] for ln in ft['lines'] for c in ln['clips'] if c.get('kind')!='image'}.values())
    lines=[dict(i=i,say=s['say']) for i,s in enumerate(script['script']['segments']) if chapter is None or s['chapter']==chapter['id']]
    count=len(ft['voices'])
    for attempt in range(1 if additional else 3):
        dest=root if attempt==0 else root/f'attempt-{attempt+1}'
        job=dest/'research.json'
        if job.exists():research=read(job)
        else:
            text,use=wf4.story.writer.ask('Research firsthand interview or speech footage for this documentary. Choose up to three named people who lived its consequential events, '
                'and whose own words would add something after an existing narration line. Return JSON {people:[{name,kind:"person",must_show,purpose,searches:[{where:"youtube"|"archive",q:string}]}]}. '
                'Use the canonical person name. must_show is ONLY a simple identity requirement, not a demand that a video title prove an exact incident. '
                'Put the narrative purpose in purpose. Include concise broad interview searches and focused archive searches, each at most ten words. '
                'Read unsuccessful research evidence and broaden or change the sources; never repeat failed queries. Known archive films may carry interviews between their narration. '
                'No impersonation, AI voice or narrator quotes. If no recorded witness could exist, return an empty list.',
                json.dumps(dict(title=script['title'],named=script['dossier']['named'],lines=lines,known_sources=known,prior=history,
                                already_selected=ft['voices'],story_purpose=script['script'].get('testimony_purpose'),
                                chapter=chapter,
                                purpose='Add consequential first-person testimony which is missing from these existing inserts; no repetition. Prefer a complete account of what the witness did or experienced, not general praise or motivation.' if additional else 'Find the subject speaking.')),
                effort='high',max_tokens=3000,what='WF4 witness research')
            research=wf4.story.writer.json_block(text);research['usage']=use;save(job,research)
        if not research['people']:return
        rejected=[]
        for folder in (Path(ctx.supply)/'footage').glob('witnesses*'):
            for prior in folder.rglob('footage.json'):rejected.extend(read(prior).get('rejected_ranges',[]))
        extra=footage_box.fetch(dict(_voices=True,people=research['people'],lines=lines,avoid=ft['voices']+rejected,known_sources=known),dest,ctx.log)
        add_footage(ft,extra,dest)
        if len(ft['voices'])>count:return
        history.append(dict(research=research,result=extra))
    if not additional:raise RuntimeError('firsthand witnesses were identified, but no identity-verified speaking footage passed; research must continue')


def evidence_request(script,gaps):
    return hashlib.sha256(json.dumps(dict(gaps=gaps,narration={str(g['seg']):script['script']['segments'][g['seg']]['say'] for g in gaps}),sort_keys=True).encode()).hexdigest()[:16]


def prune_unchecked_photos(ft,archive,root=None,segments=None):
    checked={(p['seg'],p['clip']['path']) for p in ft.get('editorial',{}).get('pins',[])}
    def keep(c,seg):
        path=Path(c['path'])
        if (segments is not None and seg not in segments) or c.get('kind')!='image' or not path.is_relative_to(archive) or (seg,c['path']) in checked:return True
        return path.is_relative_to(root) if root else path.relative_to(archive).parts[0].startswith('request-')
    for line in ft['lines']:line['clips']=[c for c in line['clips'] if keep(c,line['i'])]
    editorial=ft.get('editorial',{})
    if 'assignments' in editorial:
        editorial['assignments']=[dict(row,candidates=cs) for row in editorial['assignments']
                                  if (cs:=[c for c in row['candidates'] if keep(c,row['seg'])])]


def evidence_for_gaps(ctx,script,ft,gaps):
    """Rare events can survive only as photographs. Use each evidence image once;
    the delivered moving-footage gate still applies and no photo fills a reel.
    """
    archive=Path(ctx.supply)/'evidence-photos';archive.mkdir(exist_ok=True)
    root=archive/('request-'+evidence_request(script,gaps));root.mkdir(exist_ok=True)
    job=root/'plan.json'
    history=None
    # Old segment-number plans can belong to a different draft. Keep checked
    # uses, but never let those unverified assignments silently fill new words.
    prune_unchecked_photos(ft,archive,root,{g['seg'] for g in gaps})
    if job.exists() and read(job).get('_version')!=2:
        history=dict(plan=read(job),reports=read(root/'search.json') if (root/'search.json').exists() else None)
        save(root/'research-before-v2.json',history)
        for name in ('plan.json','pictures.json','search.json'):
            if (root/name).exists():(root/name).rename(root/(Path(name).stem+'-before-v2.json'))
    if job.exists():plan=read(job)
    else:
        text,use=wf4.story.writer.ask('You are researching documentary evidence for specific narration lines after two archive-video searches left gaps. '
            'Find literal, authentic archive photographs or documents of the named things and the correct era. No generic stock, metaphors, lookalikes, '
            'AI pictures, or an invented historic camera view. Cover every supplied gap with enough unique images at no more than 7.8 seconds per image; '
            'the supplied gaps already fit the film-wide 45% still limit. An empty subject list leaves the film unfinished. '
            'must_show contains ONLY visible subject identity, relevant form and era. Never put reuse constraints, search instructions, timing quotas, '
            'or requirements to prove every spoken date in a picture into must_show. The allocator enforces uniqueness. '
            'Use actual relevant hardware or operations to illustrate a technical explanation; a narration reference to a report does not require '
            'showing its cover unless the words explicitly describe that page. A factual launch schedule can be illustrated by genuine prelaunch '
            'vehicles; do not invent a date card or demand printed dates on rockets. When a line explicitly describes a particular photograph, '
            'find that exact photograph. Keep different crews, repair states and ground rehearsals distinct. '
            'Return JSON {subjects:[{name,searches:[specific Wikimedia Commons searches],must_show,is}],lines:[{i,subjects:[names]}]}. '
            'Reuse a subject definition if several lines genuinely need it. The full film must remain majority moving footage.',
            json.dumps(dict(gaps=gaps,named=script['dossier']['named'],facts=script['dossier']['facts'],title=script['title'],prior=history)),effort='high',max_tokens=6000,what='WF4 evidence photo research')
        plan=wf4.story.writer.json_block(text);plan.update(usage=use,_version=2);save(job,plan)
    cache=root/'pictures.json'
    if cache.exists():pictures=read(cache)
    else:
        pictures,reports,usd=PX.pictures(plan['subjects'],str(root),per=10,min_px=1200)
        save(cache,pictures);save(root/'search.json',dict(reports=reports,usd=usd))
        ft['spend'].append(dict(usd=usd+plan.get('usage',{}).get('usd',0)))
    by={ln['i']:ln for ln in ft['lines']};assigned={r['i']:r['subjects'] for r in plan['lines']}
    # Search the primary archive too; Commons has only a subset of its scans.
    from archive_pictures import find
    needed={name for gap in gaps for name in assigned.get(gap['seg'],[])}
    sources=sorted({f['url'] for f in script['dossier']['facts'] if f.get('url')})
    extra,reports=find([s for s in plan['subjects'] if s['name'] in needed],root/'primary',sources)
    save(root/'primary/search.json',reports)
    for name,recs in extra.items():pictures.setdefault(name,[]).extend(recs)
    for gap in gaps:
        line=by.setdefault(gap['seg'],dict(i=gap['seg'],clips=[]))
        for name in assigned.get(gap['seg'],[]):
            for rec in pictures.get(name,[]):
                url=rec.get('url') or rec.get('page')
                if not url:continue
                c=dict(rec,kind='image',content_hash=digest(rec['path']),seconds=7.8,time_range=[0,7.8],
                       source=dict(url=url,title=rec.get('title'),credit=rec.get('credit'),licence=rec.get('licence'),licence_url=rec.get('licence_url')))
                line['clips'].append(c)
                ft['credits'].append(dict(line=gap['seg'],**c['source']))
    ft['lines']=list(by.values())


def video_search_counts(root,expected=None):
    """Count saved engine results, including uncovered lines, not attempts."""
    counts={}
    for path in Path(root).glob('*/footage.json'):
        result=read(path)
        if not footage_box.completed_research(result):continue
        context=result.get('narration_context',{})
        for i in {r['i'] for r in result.get('lines',[]) if expected is None or context.get(str(r['i']),r.get('say'))==expected.get(r['i'])}:
            counts[i]=counts.get(i,0)+1
    return counts


def photo_research_ready(root,gaps,duration,expected=None):
    """Two completed video searches may establish a genuinely scarce view.
    A failed download or interrupted search is not research evidence.
    """
    if sum(g['seconds'] for g in gaps)>duration*.45:return False
    counts=video_search_counts(root,expected)
    return all(counts.get(g['seg'],0)>=2 for g in gaps)


def supply(ctx, script):
    os.environ.update(TMPDIR=ctx.tmp,VALMERA_SKILL=ctx.workflow,VALMERA_RUN=ctx.run_id,
                      WF4_SPEND_LOG=str(Path(ctx.run_dir)/'model-spend.jsonl'))
    if (Path(ctx.run_dir)/'read-repair.pending.json').exists():
        from read_repair import finish_pending
        finish_pending(ctx.run_dir)
        script.clear();script.update(read(Path(ctx.run_dir)/'script.json'))
    worker()
    pk=Path(ctx.supply)/'pack.json'
    if pk.exists(): return read(pk)['record']
    Path(ctx.supply).mkdir(parents=True,exist_ok=True)
    # The researched draft finds sources. The final spoken script is committed
    # only after the actual archive has been inspected, avoiding blind claims
    # that no surviving picture can illustrate.
    ft=footage(ctx,script)
    from archive_script import conform
    conform(ctx,script,ft)
    sc=verify_structure(ctx,script)
    ns=wf4.story.narration_script(sc)
    reader=str(HERE/'longform_voice.py')
    nd=Path(ctx.supply)/'narration'
    from witness_audio import completed as witness_complete
    job=dict(narration_dir=str(nd),voices=ft['voices'],footage_dir=str(Path(ctx.supply)/'footage'))
    if (nd/'read-stage.json').exists() and read(nd/'read-stage.json').get('script')==ns and witness_complete(job,Path(ctx.supply)/'witness-audio.json'):
        ctx.log('resuming verified witness audio on its combined word clock')
        nar=narrated(ctx,ns,nd,reader)
        return finish_supply(ctx,script,ft,nar,nd)
    # Spoken wording has passed its factual audit and is now frozen. Picture
    # choices can be checked while the guarded reader works; they cannot edit
    # a sentence or reset any take budget.
    from archive_visual import prepare as prepare_visuals
    with ThreadPoolExecutor(max_workers=2) as pool:
        voice_job=pool.submit(narrated,ctx,ns,nd,reader)
        visual_job=pool.submit(prepare_visuals,ctx,script,ft)
        def completed(future):
            if future.exception() is not None:ctx.log('parallel supply stage failed: '+repr(future.exception()))
        voice_job.add_done_callback(completed);visual_job.add_done_callback(completed)
        nar=voice_job.result();visual_job.result()
    if nar['gate'].get('wer') is None or nar['gate']['wer']>=0.01:
        raise RuntimeError(f"narration WER must be <1%: {nar['gate']}")
    # Coverage is solved on the narrator-only clock. Inserted testimony brings
    # its own simultaneous picture and is spliced once after all research.
    clean_words=nd/'words_narrator.json'
    coverage_words=str(clean_words if clean_words.exists() else nd/'words.json')
    wj=read(coverage_words)
    expected={i:s['say'] for i,s in enumerate(sc['segments'])}
    if ft.get('editorial',{}).get('pins'):
        prune_unchecked_photos(ft,Path(ctx.supply)/'evidence-photos')
        save(Path(ctx.supply)/'footage'/'footage.json',ft)
    # Resume a recorded edit before commissioning more source research. A
    # newly assigned figure may cover an adjacent clause as one checked move.
    try:schedule(sc,coverage_words,ft)
    except CoverageError as partial:
        from timeline_repair import continue_still
        if continue_still(ctx,script,ft,partial,coverage_words):
            save(Path(ctx.supply)/'footage'/'footage.json',ft)
        if ft.get('editorial',{}).get('rejected'):
            try:schedule(sc,coverage_words,ft)
            except CoverageError as remaining:
                from evidence_allocate import allocate
                allocate(ctx,script,ft,remaining,coverage_words)
                save(Path(ctx.supply)/'footage'/'footage.json',ft)
    for attempt in range(2):
        try:
            schedule(sc,coverage_words,ft)
            break
        except CoverageError as failure:
            gaps=failure.gaps
            if photo_research_ready(Path(ctx.supply)/'footage',gaps,wj['duration'],expected):
                ctx.log('two completed video searches cover each remaining gap; researching unique archive photographs within the 45% limit')
                break
            research_gaps=gaps
            if sum(g['seconds'] for g in gaps)<=wj['duration']*.45:
                counts=video_search_counts(Path(ctx.supply)/'footage',expected)
                research_gaps=[g for g in gaps if counts.get(g['seg'],0)<2]
            avoid=[dict(url=c['source']['url'],time_range=c['time_range']) for ln in ft['lines'] for c in ln['clips']]
            avoid += [dict(url=v['source']['url'],time_range=v['time_range']) for v in ft['voices']]
            ctx.log(f"footage repair {attempt+1}: {len(gaps)} gaps, {sum(g['seconds'] for g in gaps):.1f}s; {len(research_gaps)} require more video research")
            for ch in sc['chapters']:
                lines=[dict(i=g['seg'],say=g.get('heard') or g.get('words') or sc['segments'][g['seg']]['say'],
                            pictures=sc['segments'][g['seg']].get('pictures',[]),dur=g['seconds']+2,
                            visual='Find a picture for only this missing spoken interval. Surrounding narration: '+sc['segments'][g['seg']]['say']+
                            '\nPreserve the exact mission, hardware and repair state; source attribution and truthful context labels must be supported by evidence.')
                       for g in research_gaps if sc['segments'][g['seg']]['chapter']==ch['id']]
                if not lines:continue
                verifier='wf4-source-and-encoded-picture-check'
                context={str(r['i']):sc['segments'][r['i']]['say'] for r in lines}
                request_id=hashlib.sha256(json.dumps(dict(lines=lines,verification_owner=verifier),sort_keys=True).encode()).hexdigest()[:12]
                dest=Path(ctx.supply)/'footage'/f"repair-{attempt+1}-{ch['id']}-{request_id}"
                known=list({c['source']['url']:c['source'] for ln in ft['lines'] for c in ln['clips']}.values())
                extra=footage_box.fetch(dict(lines=lines,named=script['dossier']['named'],about=sc['title']+': '+ch['title'],avoid=avoid,known_sources=known,
                                            want_voices=False,verification_owner=verifier,narration_context=context),dest,ctx.log)
                add_footage(ft,extra,dest)
                avoid.extend(dict(url=c['source']['url'],time_range=c['time_range']) for ln in extra['lines'] for c in ln['clips'])
                avoid.extend(dict(url=v['source']['url'],time_range=v['time_range']) for v in extra.get('voices',[]))
            save(Path(ctx.supply)/'footage'/'footage.json',ft)
    try:shots,clock,_=schedule(sc,coverage_words,ft)
    except CoverageError as failure:
        if sum(g['seconds'] for g in failure.gaps)>wj['duration']*.45:
            raise RuntimeError('moving footage still covers less than 55%; another real-video research pass is required')
        evidence_for_gaps(ctx,script,ft,failure.gaps)
        try:shots,clock,_=schedule(sc,coverage_words,ft)
        except CoverageError as remaining:
            from document_evidence import supply as original_figures
            original_figures(ctx,script,ft,remaining.gaps)
            save(Path(ctx.supply)/'footage/footage.json',ft)
            try:shots,clock,_=schedule(sc,coverage_words,ft)
            except CoverageError as tail:
                from evidence_allocate import allocate
                allocate(ctx,script,ft,tail,coverage_words)
                save(Path(ctx.supply)/'footage/footage.json',ft)
                shots,clock,_=schedule(sc,coverage_words,ft)
    from timeline_repair import repair as repair_timeline,retain,smooth
    shots,clock=repair_timeline(ctx,script,ft,coverage_words)
    report=source_review(ctx,shots)
    retain(ft,shots,clock,report)
    from moving_picture import improve
    improve(ctx,script,ft,coverage_words,shots)
    shots,clock,_=schedule(sc,coverage_words,ft)
    save(Path(ctx.supply)/'footage/footage.json',ft)
    still=sum((r['f'][1]-r['f'][0])/FPS for r in shots if r['clip'].get('kind')=='image')
    if not ft['voices']:witness_voices(ctx,script,ft)
    # Firsthand testimony follows the story's purpose, not a chapter quota.
    # A chapter about unfilmed hardware need not trigger another interview
    # search once consequential, verified witness inserts are already found.
    # Original on-camera testimony is picture time too. This is only a supply
    # check; actual motion and the final >=50% gate remain mandatory below.
    with_testimony=clock.dur+sum(v['seconds'] for v in ft['voices'])
    if still/with_testimony>.5:raise RuntimeError('evidence photos would displace the moving film, including its testimony')
    if ft['voices']:
        splice_voices(ctx,nd,ft['voices'],Path(ctx.supply)/'footage')
    return finish_supply(ctx,script,ft,nar,nd)


def finish_supply(ctx,script,ft,nar,nd):
    """Both new and resumed splices pass the same final source timing gate."""
    from timeline_repair import repair as repair_timeline,smooth
    sc=script['script']
    if nar['gate'].get('wer') is None or nar['gate']['wer']>=.01:
        raise RuntimeError(f"narration WER must be <1%: {nar['gate']}")
    wj=read(nd/'words.json')
    repair_timeline(ctx,script,ft,nar['words'])
    smooth(ctx,script,ft,nar['words'])
    save(Path(ctx.supply)/'footage'/'footage.json',ft)
    record=dict(narration_s=wj['duration'],voice=nar['voice'],gate=nar['gate'],footage_clips=sum(len(l['clips']) for l in ft['lines']),
                real_voices=len(wj.get('voices',[])),spend=[dict(stage='supply',provider='openai',what='footage',usd=sum(s.get('usd',0) for s in ft['spend']))])
    import film_long as legacy
    world=None
    if any((s.get('design') or {}).get('kind')=='map' for s in sc['segments']):
        world=PX.commons_file(legacy.WORLD,str(Path(ctx.supply)/'world'),want_w=4000)
    designs=legacy._designs(ctx,sc,script['dossier'],world,str(Path(ctx.supply)/'design'))
    pack=dict(designs=designs,sid=ctx.sid,narration=nar['wav'],words=nar['words'],ffmpeg=PX.FF,footage=ft,record=record)
    save(Path(ctx.supply)/'pack.json',dict(pack=pack,record=record))
    return record


def reserve(used, c, seconds):
    """Non-overlapping ranges on original source URLs, even across downloaded files."""
    url=c['source']['url']; a,b=map(float,c['time_range'])
    if c.get('kind')=='image':
        identity='image:'+c['content_hash']
        if used.get(url) or used.get(identity):return None
        used[url]=[(a,b)];used[identity]=[(a,b)]
        return 0.0
    b=min(b,a+float(c['seconds']))
    ranges=sorted(used.get(url,[]))
    cur=a
    for lo,hi in ranges:
        if hi<=cur: continue
        if lo-cur>=seconds: break
        if lo<b: cur=max(cur,hi)
    if b-cur+1e-6<seconds: return None
    used.setdefault(url,[]).append((cur,cur+seconds))
    return cur-a


class CoverageError(ValueError):
    def __init__(self, gaps, shots=()):
        self.gaps=gaps;self.shots=list(shots)
        super().__init__('footage gaps require a new search, never a repeated picture: '+json.dumps(gaps)[:1500])


def unused_seconds(used,c):
    """A partly reserved archive cut can still contain genuinely unseen frames."""
    url=c['source']['url'];a,b=map(float,c['time_range'])
    if c.get('kind')=='image':
        return 0 if used.get(url) or used.get('image:'+c['content_hash']) else float(c['seconds'])
    b=min(b,a+float(c['seconds']));cur=a;longest=0.
    for lo,hi in sorted(used.get(url,[])):
        if hi<=cur:continue
        longest=max(longest,min(lo,b)-cur)
        cur=max(cur,hi)
        if cur>=b:break
    return max(longest,b-cur,0.)


def unify_image_copies(ft):
    """Conservatively reserve alternate scans of a photograph as one picture.
    A second URL, JPEG size or mirrored scan does not make a new photograph.
    Final rendered sequences still receive the independent duplicate check.
    """
    import cv2
    import numpy as np
    from PIL import Image,ImageOps
    known={};groups=[]
    editorial=ft.get('editorial',{})
    extra=[dict(clips=editorial.get('bank',[])),dict(clips=[p['clip'] for p in editorial.get('pins',[])])]
    for line in ft['lines']+extra:
        for c in line['clips']:
            if c.get('kind')!='image':continue
            path=c['path']
            if path not in known:
                with Image.open(path) as im:
                    gray=np.asarray(ImageOps.exif_transpose(im).convert('L').resize((32,32)),dtype=np.float32)
                hs=[]
                for frame in (gray,gray[:,::-1]):
                    d=cv2.dct(frame.copy())[:8,:8].ravel()[1:]
                    hs.append(sum(int(v)<<i for i,v in enumerate(d>np.median(d))))
                identity=c['content_hash']
                for prior,hashes in groups:
                    if min((a^b).bit_count() for a in hs for b in hashes)<=6:
                        identity=prior;break
                else:groups.append((identity,hs))
                known[path]=identity
            c['content_hash']=known[path]


def encoded_durations(ft):
    """Reserve encoded frames, never the downloader's longer requested range."""
    editorial=ft.get('editorial',{});seen={}
    extra=[dict(clips=editorial.get('bank',[])),dict(clips=[p['clip'] for p in editorial.get('pins',[])])]
    for line in ft['lines']+extra:
        for c in line['clips']:
            if c.get('kind')=='image' or not c.get('file'):continue
            path=Path(c['path']);stat=path.stat();stamp=[stat.st_size,stat.st_mtime_ns]
            rec=seen.get(str(path)) or c.get('encoded')
            if not rec or rec['file']!=stamp:
                probe=json.loads(subprocess.check_output([PX.FP,'-v','error','-select_streams','v:0',
                    '-show_entries','stream=duration,nb_frames,avg_frame_rate','-of','json',str(path)]))['streams'][0]
                num,den=map(float,probe['avg_frame_rate'].split('/'))
                duration=float(probe['nb_frames'])*den/num if probe.get('nb_frames','N/A')!='N/A' and num else float(probe['duration'])
                rec=dict(file=stamp,seconds=duration)
            seen[str(path)]=rec;c['encoded']=rec;c['seconds']=min(c['seconds'],rec['seconds'])


def schedule(sc, words, ft):
    encoded_durations(ft)
    unify_image_copies(ft)
    if isinstance(words,Path):words=str(words)
    clock=F.Clock(words,sc)
    wj=read(words) if isinstance(words,(str,Path)) else words
    end=math.ceil(clock.dur*FPS)
    starts=[0]+[round(a*FPS) for a,b in clock.segs[1:]]+[end]
    by={l['i']:l for l in ft['lines']}
    used,shots,missing={},[],[]
    voice_spans=[]
    for v in wj.get('voices',[]):
        if not v.get('file'): raise ValueError('original voice lacks its on-camera footage')
        a=round((v['start']+F.LEAD_F/FPS)*FPS); b=round((v['end']+F.LEAD_F/FPS)*FPS)
        voice_spans.append((a,b))
        c=dict(path=v['file'],source=v['source'],time_range=v['time_range'],seconds=(b-a)/FPS,w=W,h=H,
               editorial_label=v['person']+' • interview')
        used.setdefault(c['source']['url'],[]).append(tuple(c['time_range']))
        pos=a
        while pos<b:
            stop=min(b,pos+234)
            shots.append(dict(f=[pos,stop],seg=v['after'],clip=c,src=[(pos-a)/FPS,(stop-a)/FPS],say=v['said'],context=v['said'],real_voice=v['person']))
            pos=stop
    from timeline_repair import resolve,identity
    rejected={(r['seg'],r['clip']) for r in ft.get('editorial',{}).get('rejected',[])}
    for pin in ft.get('editorial',{}).get('pins',[]):
        a,b=[round(resolve(clock,t)*FPS) for t in pin['anchors']]
        c=pin['clip']
        limit=12 if c.get('kind')=='image' and c.get('editorial_hold_move') else 8
        if a<0 or b>end or b<=a or (b-a)/FPS>limit:continue
        if any(min(b,hi)>max(a,lo) for lo,hi in voice_spans):continue
        offset=pin['src'][0];seconds=(b-a)/FPS
        if offset+seconds>c['seconds']+.025:continue
        url=c['source']['url'];lo=c['time_range'][0]+offset;hi=lo+seconds
        if c.get('kind')=='image':
            if reserve(used,c,seconds) is None:continue
        else:
            if any(min(hi,v)>max(lo,u)+.001 for u,v in used.get(url,[])):continue
            used.setdefault(url,[]).append((lo,hi))
        shots.append(dict(f=[a,b],seg=pin['seg'],clip=c,src=[offset,offset+seconds],say=sc['segments'][pin['seg']]['say']))
        voice_spans.append((a,b))
    tasks=[]
    for i,s in enumerate(sc['segments']):
        a,b=starts[i:i+2]
        beats=s.get('visual_beats',[]);beat_frames=[]
        spoken=clock.seg_words(i)
        for beat in beats:
            index=beat['word_start']
            if index>=len(spoken):raise ValueError(f'visual beat is beyond spoken segment {i}')
            beat_frames.append((a if index==0 else round(spoken[index]['t0']*FPS),beat))
        slots=[(round(resolve(clock,t['anchors'][0])*FPS),round(resolve(clock,t['anchors'][1])*FPS))
               for t in ft.get('editorial',{}).get('assignments',[]) if t['seg']==i]
        # A proposed replacement spans the former shot, even when an older
        # visual draft had a word-beat edge inside it. Its complete new pairing
        # still goes through source_review on the actual clock.
        edges=sorted({a,b,*[f for f,_ in beat_frames if a<f<b and not any(lo<f<hi for lo,hi in slots)],
                      *[f for span in slots for f in span if a<f<b]})
        intervals=list(zip(edges,edges[1:]))
        for va,vb in voice_spans:
            cut=[]
            for lo,hi in intervals:
                if vb<=lo or va>=hi: cut.append((lo,hi)); continue
                if lo<va: cut.append((lo,va))
                if vb<hi: cut.append((vb,hi))
            intervals=cut
        clips=[c for c in by.get(i,{}).get('clips',[]) if (i,identity(c)) not in rejected]
        for a,b in intervals:
            interval_clips=clips
            active=[beat for f,beat in beat_frames if f<=a]
            if active:
                ids=active[-1]['archive_ids'];order={key:k for k,key in enumerate(ids)}
                interval_clips=sorted((c for c in clips if not c.get('catalogue_id') or c['catalogue_id'] in order),
                                      key=lambda c:order.get(c.get('catalogue_id'),len(order)))
                interval_clips=[dict(c) for c in interval_clips]
                for c in interval_clips:
                    if c.get('catalogue_id') not in order:continue
                    c.pop('editorial_label',None)
                    if active[-1].get('label'):c['editorial_label']=active[-1]['label']
            for slot in ft.get('editorial',{}).get('assignments',[]):
                if slot['seg']!=i:continue
                sa,sb=[round(resolve(clock,t)*FPS) for t in slot['anchors']]
                if abs(sa-a)<=1 and abs(sb-b)<=1:
                    interval_clips=slot['candidates'];break
            # Allocate scarce evidence first. Chronological greed can spend a
            # unique image on a well-covered line and starve the only other
            # line it illustrates. Output chronology is restored below.
            unique={('image:'+c['content_hash'] if c.get('kind')=='image' else
                     c['source']['url']+':'+str(c['time_range'])):c for c in interval_clips}
            choices=list(unique.values())
            spare=sum(min(7.8,unused_seconds(used,c)) for c in choices)-(b-a)/FPS
            tasks.append((spare,i,s,a,b,choices))
    for _,i,s,a,b,clips in sorted(tasks,key=lambda t:t[0]):
        pos=a
        for ci,c in enumerate(clips):
            seconds=min(7.8,(b-pos)/FPS,unused_seconds(used,c))
            tail=(b-pos)/FPS-seconds
            if 0<tail<1.5 and seconds>3 and ci+1<len(clips):
                seconds-=1.5-tail
            if seconds<1/FPS: continue
            offset=reserve(used,c,seconds)
            if offset is None: continue
            n=min(round(seconds*FPS),b-pos)
            shots.append(dict(f=[pos,pos+n],seg=i,clip=c,src=[offset,offset+n/FPS],say=s['say']))
            pos+=n
            if pos>=b: break
        if pos<b:
            heard=' '.join(w['w'] for w in clock.words if w['t0']<b/FPS and w['t1']>pos/FPS)
            missing.append(dict(seg=i,seconds=(b-pos)/FPS,words=s['say'],heard=heard,start=pos/FPS,end=b/FPS))
    for r in shots:
        spoken=[w for w in clock.words if w['t0']<r['f'][1]/FPS and w['t1']>r['f'][0]/FPS]
        if not r.get('real_voice'):
            r['context']=' '.join(sc['segments'][i]['say'] for i in dict.fromkeys(w['seg'] for w in spoken)) if spoken else r['say']
        if spoken:r['say']=' '.join(w['w'] for w in spoken)
    if missing: raise CoverageError(missing,sorted(shots,key=lambda r:r['f'][0]))
    return sorted(shots,key=lambda r:r['f'][0]),clock,end


def chapter_clock(sc, clock, end):
    out=[]
    for ch in sc['chapters']:
        ids=[i for i,s in enumerate(sc['segments']) if s['chapter']==ch['id']]
        if not ids: raise ValueError('empty chapter '+ch['id'])
        start=0 if ids[0]==0 else clock.segs[ids[0]][0]
        stop=clock.segs[ids[-1]+1][0] if ids[-1]+1<len(clock.segs) else end/FPS
        verified=ch.get('verified_turn_index')
        turns=[clock.segs[verified][0]] if verified in ids else [clock.segs[i][0] for i in ids if sc['segments'][i].get('beat')=='turn']
        pays=[clock.segs[i][0] for i in ids if sc['segments'][i].get('beat') in ('payoff','close')]
        if not turns: raise ValueError('chapter has no timed turn: '+ch['id'])
        out.append(dict(ch,start=start,end=stop,turn_s=turns[0],payoff_s=pays[-1] if pays else stop-2))
    return out


def witness_crop(row):
    """One continuous interview push across a complete longer recollection.

    Internal source slices do not restart the move or pretend to be new shots.
    The final rater still measures the actual encoded cuts and camera motion.
    """
    duration=row['clip']['seconds']
    if not row.get('real_voice') or duration<=8:return [0,0,1,1]
    crops=[]
    for q in range(row['f'][1]-row['f'][0]):
        t=min(1.,(row['src'][0]+q/FPS)/duration)
        width=1/(1+.12*t)
        crops.append([.5-width/2,.5-width/2,.5+width/2,.5+width/2])
    return crops


def archive_label(clip,measure,max_width=.89*W):
    """Bibliographic titles are display text; full credits stay in the ledger.

    Ellipsis is typographic only. A factual context label must fit intact.
    """
    explicit=clip.get('editorial_label')
    text=explicit or (clip['source'].get('title') or 'Original technical document')+' • archive record'
    if measure(text)<=max_width:return text
    if explicit:raise ValueError('factual archive label exceeds its display width: '+text)
    suffix='… • archive record';title=clip['source'].get('title') or 'Original technical document'
    lo,hi=0,len(title)
    while lo<hi:
        mid=(lo+hi+1)//2
        if measure(title[:mid].rstrip()+suffix)<=max_width:lo=mid
        else:hi=mid-1
    return title[:lo].rstrip()+suffix


def build(sc,pack):
    worker()
    from film_long import Plan
    shots,clock,end=schedule(sc,pack['words'],pack['footage'])
    chapters=chapter_clock(sc,clock,end)
    voices={'cap':dict(font='InterDisplay-Bold.ttf',px=40,track=0,shadow={'a':0.9}),
            'chapter':dict(font='InterDisplay-Bold.ttf',px=48,track=0),
            'source':dict(font='InterDisplay-Bold.ttf',px=28,track=0,shadow={'a':0.9})}
    P=Plan(voices,{})
    for j,row in enumerate(shots):
        c=row['clip']
        if c.get('kind')=='image':
            key=P.image(f'evidence-photo-{j}',c)
            if c.get('document'):
                P.page(row['f'],'#14161A',grain=.005)
                n=row['f'][1]-row['f'][0]
                growth=.12 if n>240 else .025
                ratio=c['w']/c['h'];height=min(.66,.70/(1+growth),.84*W/H/ratio);width=height*ratio/(W/H)
                P.card(row['f'],key,[.5-width/2,.45-height/2,.5+width/2,.45+height/2],
                       s=[1+growth*q/max(n-1,1) for q in range(n)])
                P.word(row['f'],archive_label(c,lambda t:P.R.width('source',t)),'source',.055,.84,note=False,z=3)
            else:
                # Archive portraits and machinery must survive landscape
                # presentation whole; a centre crop can erase the evidence.
                P.page(row['f'],'#14161A',grain=.005)
                # Reserve independent chapter, source and caption seats even
                # at the end of the largest card move. Text renders above cards.
                n=row['f'][1]-row['f'][0]
                growth=.12 if n>240 else .035
                ratio=c['w']/c['h'];height=min(.66,.70/(1+growth),.88*W/H/ratio);width=height*ratio/(W/H)
                P.card(row['f'],key,[.5-width/2,.45-height/2,.5+width/2,.45+height/2],
                       s=[1+growth*q/max(n-1,1) for q in range(n)])
        else:
            key=P.video(f'archive-{j}',c)
            P.shot(row['f'],key,witness_crop(row),src=row['src'])
        if c.get('editorial_label') and not c.get('document'):
            label=archive_label(c,lambda t:P.R.width('source',t)) if c.get('kind')=='image' else c['editorial_label']
            P.word(row['f'],label,'source',.055,.84 if c.get('kind')=='image' else .78,note=False,z=3)
    # Subtitles remain legible and subordinate to the archive. No held number pages.
    for si in range(len(sc['segments'])):
        ws=clock.seg_words(si)
        for j in range(0,len(ws),7):
            chunk=ws[j:j+7]; a=round(chunk[0]['t0']*FPS); b=round(chunk[-1]['t1']*FPS)
            if b>a:
                P.word([a,b],' '.join(w['w'] for w in chunk),'cap',0.5,0.89,align='c',role='caption',note=False,z=3)
    for ch in chapters:
        a=round(ch['start']*FPS)
        P.word([a,min(a+75,end)],ch['title'],'chapter',0.055,0.08,z=3)
    for sid,rec in pack.get('designs',{}).items():
        si=int(sid); a=round(clock.segs[si][0]*FPS)
        b=min(round(clock.segs[si][1]*FPS),a+150)
        if b<=a: continue
        d=sc['segments'][si].get('design') or {}
        if rec['kind']=='number':
            # A number is an annotation on the evidence, never a held replacement card.
            P.word([a,min(a+72,b)],str(d.get('print','')),'chapter',0.055,0.72)
            continue
        key=P.image('evidence-'+sid,rec)
        n=b-a
        P.card([a,b],key,[0.52,0.15,0.95,0.80],z=2,
               op=[min(1,q/7) for q in range(n)],
               s=[round(1.04-0.04*min(1,q/10)**0.5,5) for q in range(n)])
    l1=P.done(pack['sid'],end,1114,dict(grain=0.007,vignette=0.12))
    l1['chapters']=chapters
    l1['music_marks']=dict(turn_s=chapters[0]['turn_s'],payoff_s=chapters[-1]['payoff_s'])
    audio=F.audio_scene(l1,pack)
    from score import sound as SD
    wd=read(pack['words'])
    sounds,rep=SD.design(audio,src=pack['narration'],index=dict(words=[dict(t0=w['start'],t1=w['end']) for w in wd['words']],video=dict(duration=wd['duration'])),
                         bed=True,supply=dict(np10=len(shots)/clock.dur*10,bed=True))
    if not sounds or rep.get('fault'): raise RuntimeError('SND3 returned no designed sound')
    l1['sound']=sounds; l1['sound_design']=rep
    return l1,shots,chapters


def score(ctx,script):
    pack=read(Path(ctx.supply)/'pack.json')['pack']
    l1,shots,chapters=build(script['script'],pack)
    source_review(ctx,shots)
    p=Path(ctx.supply)/'l1.json'; save(p,l1)
    save(Path(ctx.supply)/'shots.json',shots)
    return dict(builder=digest(HERE/'cinema.py'),l1=str(p),chapters=chapters,picture_time_s=sum((r['f'][1]-r['f'][0])/FPS for r in shots),
                pictures=len(shots),max_hold_s=max((r['f'][1]-r['f'][0])/FPS for r in shots),spend=[])


def source_review(ctx,shots,required=True):
    """Catch wrong archive material before spending a full-film render."""
    from picture_check import check
    from review_cache import checker_bytes
    from document_context import enrich
    enrich(ctx,[r['clip'] for r in shots])
    from caption_context import enrich as captions
    captions(ctx,[r['clip'] for r in shots])
    cache=Path(ctx.supply)/'source-evidence'/'checked-shots'
    signatures=[];digests={}
    for row in shots:
        file=row['clip']['path']
        if file not in digests:digests[file]=digest(file)
        # Placement/index changes do not alter the sampled original frames.
        # Words, context, label, source provenance, bytes or checker changes do.
        material={k:v for k,v in row.items() if k!='f'}
        material.update(seconds=(row['f'][1]-row['f'][0])/FPS,bytes_sha256=digests[file])
        signatures.append(hashlib.sha256(json.dumps(material,sort_keys=True).encode()+checker_bytes()).hexdigest())
    key=hashlib.sha256(json.dumps(dict(shots=shots,signatures=signatures),sort_keys=True).encode()).hexdigest()[:16]
    folder=Path(ctx.supply)/'source-evidence'/key;report=folder/'report.json'
    if report.exists():result=read(report)
    else:
        cached={i:read(cache/(signature+'.json')) for i,signature in enumerate(signatures) if (cache/(signature+'.json')).exists()}
        missing=[i for i in range(len(shots)) if i not in cached]
        fresh=check(None,[shots[i] for i in missing],folder/'new',PX.FF,source=True) if missing else dict(evidence={},spend_usd=0)
        evidence={i:dict(row['verdict'],i=i) for i,row in cached.items()}
        for j,i in enumerate(missing):
            verdict=fresh['evidence'].get(str(j),fresh['evidence'].get(j))
            if verdict is not None:evidence[i]=dict(verdict,i=i)
        total=sum((r['f'][1]-r['f'][0])/FPS for r in shots)
        moving=sum((r['f'][1]-r['f'][0])/FPS for i,r in enumerate(shots) if evidence.get(i,{}).get('real_moving_footage') is True)
        rejected=[i for i,v in evidence.items() if v.get('matches') is not True]
        absent=[i for i in range(len(shots)) if i not in evidence]
        result=dict(ok=not rejected and not absent,moving_share=moving/total if total else 0,checked=len(evidence),total=len(shots),
                    missing=absent,rejected=rejected,evidence=evidence,spend_usd=fresh['spend_usd'],cached_shots=len(cached))
        save(report,result)
    for i,signature in enumerate(signatures):
        verdict=result['evidence'].get(str(i),result['evidence'].get(i))
        if verdict is not None and not (cache/(signature+'.json')).exists():
            save(cache/(signature+'.json'),dict(verdict=verdict,evidence_report=str(report)))
    from encoded_repair import enforce
    result=enforce(ctx,shots,result)
    if required and not result['ok']:raise ValueError('source pictures require repair: '+str(report))
    return result


def render(ctx,script,sc):
    # Use the established scene renderer, unchanged brand finish, and SND3 master.
    import film_long as L
    if sc.get('builder')!=digest(HERE/'cinema.py'):
        ctx.log('rebuilding the score for the current picture builder')
        sc.clear();sc.update(score(ctx,script))
        save(Path(ctx.run_dir)/'score.json',sc)
    pack=read(Path(ctx.supply)/'pack.json')['pack']
    from version import code_version
    inputs=dict(engine=code_version(),narration=digest(pack['narration']),l1=digest(sc['l1']),mixer=digest(HERE/'film_long.py'),
                stem_capture=digest(HERE/'stem_evidence.py'),music=digest(HERE/'music_chapters.py'),chapter_mix=digest(HERE/'chapter_audio.py'))
    stamp=Path(ctx.supply)/'render-inputs.json'
    if not stamp.exists() or read(stamp)!=inputs:
        for name in ('sound.m4a','sound.report.json','film_engine.mp4','film_engine.mp4.log.json','film_scored.mp4','film_scored.mp4.json','film_scored.bed.wav'):
            (Path(ctx.supply)/name).unlink(missing_ok=True)
        save(stamp,inputs)
    result=L.render(ctx,script,sc)
    out=Path(ctx.supply)/'film_scored.mp4'
    if not out.exists():
        args=dict(l1=read(sc['l1']),pack=read(Path(ctx.supply)/'pack.json')['pack'],film=result['film'],out=str(out),cache=str(Path(ctx.supply)/'music-cache'))
        job=Path(ctx.supply)/'score_music.json'; save(job,args)
        chain.sh(ctx,[chain.PY,str(HERE/'music_chapters.py'),str(job)],label='wf4-music-'+ctx.run_id,heavy=True,timeout=7200)
    result['clean']=str(Path(ctx.supply)/'clean-final.flac'); result['film']=str(out)
    result['music']=read(str(out)+'.json')
    return result


def duplicate_ranges(shots):
    seen={}; repeats=[]
    for i,r in enumerate(shots):
        c=r['clip'];url=c['source']['url']
        a=c['time_range'][0]+r['src'][0]; b=c['time_range'][0]+r['src'][1]
        for j,lo,hi in seen.get(url,[]):
            if min(hi,b)-max(lo,a)>0.04: repeats.append([j,i])
        seen.setdefault(url,[]).append((i,a,b))
    return repeats


def check(ctx,rd,sc):
    import shutil
    import fcntl
    worker()
    pack=read(Path(ctx.supply)/'pack.json')['pack']
    final=Path(ctx.run_dir)/'final.mp4'
    fs=Path(ctx.run_dir)/'finish-input.json'; programme=digest(rd['film'])
    finish_inputs=dict(sha256=programme,brand=digest(Path(brand.__file__)),accent=False)
    if not fs.exists() or read(fs)!=finish_inputs:
        final.unlink(missing_ok=True)
    if not final.exists():
        # Brand's own watermark and end card, never recreated or altered here.
        job=Path(ctx.supply)/'finish.py'
        job.write_text('import sys; sys.path[:0]='+repr([str(HERE.parent/'_shell')])+"; import brand; brand.finish("+repr(rd['film'])+','+repr(str(final))+','+repr(ctx.tmp)+',accent=False)')
        chain.sh(ctx,[chain.PY,str(job)],label='wf4-brand-'+ctx.run_id,heavy=True,timeout=7200)
        save(fs,finish_inputs)
    base=brand.check(rd['film'],str(final),ctx.tmp)
    duration=brand.probe(str(final))['dur']
    base['recorded_sound_library']=chain.no_music(sc['l1'])
    base['ok']=base['ok'] and base['recorded_sound_library']['ok']
    from picture_check import check as pictures_check, motion, duplicate_check
    pr=Path(ctx.supply)/'picture_check.json'
    stamp=Path(ctx.supply)/'picture-input.json'
    from encoded_repair import review_shots
    review=review_shots(read(Path(ctx.supply)/'shots.json'),base,brand.probe(rd['film']),pack['words'])
    inputs=dict(film=digest(final),shots=digest(Path(ctx.supply)/'shots.json'),checker=digest(HERE/'picture_check.py'),
                presentation=hashlib.sha256(json.dumps(review,sort_keys=True).encode()).hexdigest())
    if not stamp.exists() or read(stamp)!=inputs:
        for name in ('picture_check.json','motion.json'):(Path(ctx.supply)/name).unlink(missing_ok=True)
        folder=Path(ctx.supply)/'picture-evidence'
        if folder.exists():
            prior=read(stamp)['film'][:12] if stamp.exists() else 'unversioned'
            rejected=Path(ctx.supply)/('picture-evidence-rejected-'+prior)
            if rejected.exists():shutil.rmtree(rejected)
            folder.rename(rejected)
        save(stamp,inputs)
    if not pr.exists(): save(pr,pictures_check(str(final),review,Path(ctx.supply)/'picture-evidence',PX.FF))
    pics=read(pr)
    mp=Path(ctx.supply)/'motion.json'
    if not mp.exists(): save(mp,motion(str(final),read(Path(ctx.supply)/'shots.json'),PX.FF))
    measured_motion=read(mp)
    duplicates=duplicate_check(measured_motion,read(Path(ctx.supply)/'shots.json'),Path(ctx.supply)/'picture-evidence')
    shot_rows=read(Path(ctx.supply)/'shots.json')
    real_ids={int(i) for i,r in pics['evidence'].items() if r.get('real_moving_footage') and shot_rows[int(i)]['clip'].get('kind')!='image'}
    # The viewer sees the whole encoded film, including its end card.
    measured_real=sum(r['seconds'] for r in measured_motion['rows'] if r['i'] in real_ids and r['moving_samples_share']>=0.25)/duration
    pics['moving_share']=measured_real
    nd=Path(pack['narration']).parent
    nar=nd/'narration_narrator.wav'
    if not nar.exists(): nar=Path(pack['narration'])
    metrics=Path(ctx.supply)/'voice-metrics'
    guarded_voice(ctx,[PX.NARRATE_PY,str(HERE/'measure_voice.py'),str(nar),str(nd/'narration_script.json'),str(metrics)],'wf4-voice-measure-'+ctx.run_id)
    guarded_voice(ctx,[PX.NARRATE_PY,str(HERE/'measure_mix.py'),str(final),pack['words'],str(nd/'narration_script.json'),str(metrics),PX.FF,
                       '--voice',str(Path(ctx.supply)/'audio-stems/voice.flac')],'wf4-mix-listen-'+ctx.run_id)
    voice=read(metrics/'metrics.json')
    mix=read(metrics/'mix-wer.json')
    sound_metrics=Path(ctx.supply)/'sound-measured.json'
    stems=Path(ctx.supply)/'audio-stems';bed=Path(rd['film']).with_suffix('.bed.wav')
    chain.sh(ctx,[chain.PY,str(HERE/'measure_sound.py'),str(final),rd['clean'],sc['l1'],pack['words'],str(sound_metrics),PX.FF,
                  '--stems',str(stems),'--bed',str(bed)],label='wf4-sound-measure-'+ctx.run_id,heavy=True,timeout=1800)
    heard_sound=read(sound_metrics)
    picture_metrics=Path(ctx.supply)/'picture-rater.json'
    chain.sh(ctx,[chain.PY,str(HERE/'measure_picture.py'),str(final),str(picture_metrics)],
             label='wf4-picture-measure-'+ctx.run_id,heavy=True,timeout=1800)
    actual_picture=read(picture_metrics)
    repeats=duplicate_ranges(read(Path(ctx.supply)/'shots.json'))
    hj=Path(ctx.run_dir)/'hook_measured.json'
    if not hj.exists():
        opening=' '.join(w['w'] for w in read(pack['words'])['words'] if w['start']+F.LEAD_F/FPS<10)
        text,use=wf4.story.writer.ask('Judge only whether the supplied words, heard in the first ten seconds of a documentary, establish concrete danger, a contradiction or an unanswered curiosity. Return JSON {hook:bool,why:string}. No numeric quality rating.',opening,effort='low',max_tokens=600,what='WF4 first ten seconds')
        save(hj,dict(wf4.story.writer.json_block(text),words=opening,usage=use))
    hook=read(hj)
    checks=dict(base,longform=dict(seconds=duration,pictures=sc['pictures'],planned_picture_changes_per_10s=sc['pictures']/(sc['picture_time_s']/10),rater_picture=actual_picture,
                real_moving_picture_time_share=pics['moving_share'],pictures_checked=pics['checked'],picture_matches=pics['ok'],
                duplicate_source_ranges=len(repeats),duplicate_pairs=repeats,visual_duplicates=duplicates,hook=hook,longest_shot_s=sc['max_hold_s'],music_share=heard_sound['music_share'],sound=heard_sound,
                chapter_rises=heard_sound['chapter_rises'],
                narration=voice,delivered_mix_wer=mix['wer'],real_voices=pack['record']['real_voices'],chapters=sc['chapters'],credits=pack['footage']['credits']))
    structure=read(Path(ctx.run_dir)/'structure.json')
    targets=dict(hook=hook.get('hook') is True,turns=len(structure['chapters'])==len(sc['chapters']),length=600<=duration<=1200,footage=pics['moving_share']>=0.5,unique=not repeats and duplicates['ok'],holds=actual_picture['holds_ok'],
                 pictures=pics['ok'],sound_fit=heard_sound['verified'],audible_sound=heard_sound['stems']['T2']>0,
                 music=heard_sound['music_share']>=0.8,rises=all(r['rise_db']>=3 for r in heard_sound['chapter_rises']),voice=voice['ok'],mix_wer=mix['ok'])
    checks['targets']=targets; checks['ok']=bool(base['ok'] and all(targets.values()))
    save(Path(ctx.run_dir)/'measured.json',checks)
    if not checks['ok']: raise RuntimeError('delivered-file gates failed: '+json.dumps(targets))
    evidence=Path(ctx.state_root).parent/'evidence'/ctx.run_id
    evidence.mkdir(parents=True,exist_ok=True)
    for src in [pr,mp,sound_metrics,picture_metrics,Path(ctx.run_dir)/'measured.json',Path(ctx.run_dir)/'structure.json',
                Path(sc['l1']),Path(ctx.supply)/'shots.json',Path(rd['film']+'.json'),Path(ctx.supply)/'sound-final.json']:
        shutil.copy2(src,evidence/src.name)
    shutil.copytree(Path(ctx.supply)/'picture-evidence',evidence/'pictures',dirs_exist_ok=True)
    for calibration in Path(ctx.supply).glob('picture-calibration-*'):
        shutil.copytree(calibration,evidence/calibration.name,dirs_exist_ok=True)
    shutil.copytree(metrics,evidence/'voice-metrics',dirs_exist_ok=True)
    from encoded_repair import retain_evidence
    retain_evidence(ctx,evidence)
    shutil.copytree(stems,evidence/'audio-stems',dirs_exist_ok=True)
    subprocess.run([PX.FF,'-v','error','-y','-i',str(bed),'-c:a','flac',str(evidence/'audio-stems/music.flac')],check=True)
    subprocess.run([PX.FF,'-v','error','-y','-i',rd['clean'],'-vn','-c:a','flac',str(evidence/'clean-audio.flac')],check=True)
    shutil.copy2(nar,evidence/'narration.wav')
    shutil.copy2(pack['words'],evidence/'words.json')
    shutil.copy2(nd/'narration_script.json',evidence/'narration_script.json')
    memory_folders=list((Path(ctx.supply)/'memory').glob('*'))+list(Path(ctx.run_dir).glob('voice-memory-*'))
    for folder in memory_folders:
        if not folder.is_dir():continue
        target=evidence/'memory'/folder.name;target.mkdir(parents=True,exist_ok=True)
        for name in ('memory.json','run.log'):
            if (folder/name).exists():shutil.copy2(folder/name,target/name)
    for folder in (Path(ctx.run_dir)/'read-repairs').glob('round-*'):
        target=evidence/'read-repairs'/folder.name;target.mkdir(parents=True,exist_ok=True)
        for name in ('proposal.json','fact-check.json','script-check.json','applied.json'):
            if (folder/name).exists():shutil.copy2(folder/name,target/name)

    archive_proof=Path(ctx.supply)/'archive-script'
    if archive_proof.exists():
        target=evidence/'archive-script';target.mkdir(exist_ok=True)
        for source in [archive_proof/'proposal.json',archive_proof/'additional-facts.json',
                       archive_proof/'reused-takes.json',*archive_proof.glob('audit-*.json')]:
            if source.exists():shutil.copy2(source,target/source.name)
        shutil.copy2(Path(ctx.run_dir)/'script.json',target/'final-script.json')
    if (Path(ctx.run_dir)/'model-spend.jsonl').exists():
        shutil.copy2(Path(ctx.run_dir)/'model-spend.jsonl',evidence/'model-spend.jsonl')

    footage_box.cleanup_cache(Path(ctx.supply)/'footage')
    footage_box.cleanup_cache(Path(ctx.supply)/'footage'/'witnesses')
    return dict(final=str(final),checks=checks,music=rd['music'],music_in_final=True,spend=[])
