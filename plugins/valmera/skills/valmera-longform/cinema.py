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
    if p not in sys.path:
        sys.path.insert(0, p)
    os.environ['PATH'] = str(Path(PX.FF).parent)+os.pathsep+os.environ.get('PATH','')
    os.environ['FFMPEG_BIN'] = PX.FF
    return p


def footage(ctx, script):
    """Each chapter is a bounded search; every previous source range stays reserved."""
    sc = script['script']
    root = Path(ctx.supply)/'footage'
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


def prefill_footage(ctx,script,ft):
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
        dest=Path(ctx.supply)/'footage'/('prefill-'+ch['id'])
        ctx.log(f"archive coverage research {ch['id']}: {len(lines)} lines")
        known=list({c['source']['url']:c['source'] for ln in ft['lines'] for c in ln['clips']}.values())
        extra=footage_box.fetch(dict(lines=lines,named=script['dossier']['named'],about=sc['title']+': '+ch['title']+'. Find additional literal archive evidence; previous source seconds are reserved.',avoid=avoid,known_sources=known),dest,ctx.log)
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


def narrated(ctx, ns, nd, reader):
    """Every model/listener process uses the shared locks and 6 GB stop."""
    nd=Path(nd);nd.mkdir(parents=True,exist_ok=True)
    save(nd/'narration_script.json',ns)
    report=nd/'longform_voice.json'
    try:
        if not report.exists() or not read(report)['after']['ok']:
            guarded_voice(ctx,[PX.NARRATE_PY,reader,'render',str(nd/'narration_script.json'),
                              '--out',str(nd),'--band','04'],'wf4-narration-'+ctx.run_id)
        if not (nd/'check.json').exists():
            guarded_voice(ctx,[PX.NARRATE_PY,PX.NARRATE,'check',str(nd),'--band','04'],
                          'wf4-voice-check-'+ctx.run_id)
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


def witness_voices(ctx,script,ft):
    """Seek firsthand testimony separately from mechanism pictures. A picture
    search need not rank an interview highly enough to hear its witness.
    """
    root=Path(ctx.supply)/'footage'/'witnesses';root.mkdir(parents=True,exist_ok=True)
    history=[]
    known=list({c['source']['url']:c['source'] for ln in ft['lines'] for c in ln['clips'] if c.get('kind')!='image'}.values())
    lines=[dict(i=i,say=s['say']) for i,s in enumerate(script['script']['segments'])]
    for attempt in range(3):
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
                json.dumps(dict(title=script['title'],named=script['dossier']['named'],lines=lines,known_sources=known,prior=history)),
                effort='high',max_tokens=3000,what='WF4 witness research')
            research=wf4.story.writer.json_block(text);research['usage']=use;save(job,research)
        if not research['people']:return
        extra=footage_box.fetch(dict(_voices=True,people=research['people'],lines=lines,avoid=ft['voices'],known_sources=known),dest,ctx.log)
        add_footage(ft,extra,dest)
        if ft['voices']:return
        history.append(dict(research=research,result=extra))
    raise RuntimeError('firsthand witnesses were identified, but no identity-verified speaking footage passed; research must continue')


def evidence_for_gaps(ctx,script,ft,gaps):
    """Rare events can survive only as photographs. Use each evidence image once;
    the delivered moving-footage gate still applies and no photo fills a reel.
    """
    root=Path(ctx.supply)/'evidence-photos';root.mkdir(exist_ok=True)
    job=root/'plan.json'
    if job.exists():plan=read(job)
    else:
        text,use=wf4.story.writer.ask('You are researching documentary evidence for specific narration lines after two archive-video searches left gaps. '
            'Find literal, authentic archive photographs or documents of the named things and the correct era. No generic stock, metaphors, lookalikes, '
            'AI pictures, or an invented historic camera view. Each image will appear once, briefly, with a restrained camera move. '
            'Return JSON {subjects:[{name,searches:[specific Wikimedia Commons searches],must_show,is}],lines:[{i,subjects:[names]}]}. '
            'Reuse a subject definition if several lines genuinely need it. The full film must remain majority moving footage.',
            json.dumps(dict(gaps=gaps,named=script['dossier']['named'],title=script['title'])),effort='high',max_tokens=5000,what='WF4 evidence photo research')
        plan=wf4.story.writer.json_block(text);plan['usage']=use;save(job,plan)
    cache=root/'pictures.json'
    if cache.exists():pictures=read(cache)
    else:
        pictures,reports,usd=PX.pictures(plan['subjects'],str(root),per=10,min_px=1200)
        save(cache,pictures);save(root/'search.json',dict(reports=reports,usd=usd))
        ft['spend'].append(dict(usd=usd+plan.get('usage',{}).get('usd',0)))
    by={ln['i']:ln for ln in ft['lines']};assigned={r['i']:r['subjects'] for r in plan['lines']}
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


def supply(ctx, script):
    worker()
    pk=Path(ctx.supply)/'pack.json'
    if pk.exists(): return read(pk)['record']
    Path(ctx.supply).mkdir(parents=True,exist_ok=True)
    sc=script['script']
    sc=verify_structure(ctx,script)
    ns=wf4.story.narration_script(sc)
    reader=str(HERE/'longform_voice.py')
    nd=Path(ctx.supply)/'narration'
    with ThreadPoolExecutor(max_workers=2) as pool:
        nj=pool.submit(narrated,ctx,ns,nd,reader)
        # Independent network work overlaps the serialized local narrator.
        try:ft=footage(ctx,script)
        except Exception as e:
            ctx.log('footage stopped; the independent narrator will finish its checkpoint: '+str(e)[:500])
            raise
        nar=nj.result()
    if nar['gate'].get('wer') is None or nar['gate']['wer']>=0.01:
        raise RuntimeError(f"narration WER must be <1%: {nar['gate']}")
    # Coverage is solved on the narrator-only clock. Inserted testimony brings
    # its own simultaneous picture and is spliced once after all research.
    clean_words=nd/'words_narrator.json'
    coverage_words=str(clean_words if clean_words.exists() else nd/'words.json')
    wj=read(coverage_words)
    for attempt in range(2):
        try:
            schedule(sc,coverage_words,ft)
            break
        except CoverageError as failure:
            gaps=failure.gaps
            avoid=[dict(url=c['source']['url'],time_range=c['time_range']) for ln in ft['lines'] for c in ln['clips']]
            avoid += [dict(url=v['source']['url'],time_range=v['time_range']) for v in ft['voices']]
            ctx.log(f"footage repair {attempt+1}: {len(gaps)} gaps, {sum(g['seconds'] for g in gaps):.1f}s")
            for ch in sc['chapters']:
                lines=[dict(i=g['seg'],say=sc['segments'][g['seg']]['say'],pictures=sc['segments'][g['seg']].get('pictures',[]),dur=g['seconds']+2)
                       for g in gaps if sc['segments'][g['seg']]['chapter']==ch['id']]
                if not lines:continue
                dest=Path(ctx.supply)/'footage'/f"repair-{attempt+1}-{ch['id']}"
                known=list({c['source']['url']:c['source'] for ln in ft['lines'] for c in ln['clips']}.values())
                extra=footage_box.fetch(dict(lines=lines,named=script['dossier']['named'],about=sc['title']+': '+ch['title'],avoid=avoid,known_sources=known),dest,ctx.log)
                add_footage(ft,extra,dest)
                avoid.extend(dict(url=c['source']['url'],time_range=c['time_range']) for ln in extra['lines'] for c in ln['clips'])
                avoid.extend(dict(url=v['source']['url'],time_range=v['time_range']) for v in extra.get('voices',[]))
            save(Path(ctx.supply)/'footage'/'footage.json',ft)
    try:shots,clock,_=schedule(sc,coverage_words,ft)
    except CoverageError as failure:
        if sum(g['seconds'] for g in failure.gaps)>wj['duration']*.45:
            raise RuntimeError('moving footage still covers less than 55%; another real-video research pass is required')
        evidence_for_gaps(ctx,script,ft,failure.gaps)
        shots,clock,_=schedule(sc,coverage_words,ft)
    still=sum((r['f'][1]-r['f'][0])/FPS for r in shots if r['clip'].get('kind')=='image')
    if still/clock.dur>.45:raise RuntimeError('evidence photos would displace the moving film')
    if not ft['voices']:witness_voices(ctx,script,ft)
    sys.path.insert(0,str(HERE.parent/'_shell'/'narrate'))
    import realvoice
    if ft['voices']:
        placed=realvoice.splice(nd,ft['voices'],Path(ctx.supply)/'footage')
        save(nd/'footage_voices.json',placed)
        subprocess.run([PX.NARRATE_PY,reader,'level-voices',str(nd)],check=True,capture_output=True)
    wj=read(nd/'words.json')
    schedule(sc,nar['words'],ft)
    save(Path(ctx.supply)/'footage'/'footage.json',ft)
    record=dict(narration_s=wj['duration'],voice=nar['voice'],gate=nar['gate'],footage_clips=sum(len(l['clips']) for l in ft['lines']),
                real_voices=len(wj.get('voices',[])),spend=[dict(stage='supply',provider='openai',what='footage',usd=sum(s.get('usd',0) for s in ft['spend']))])
    import film_long as legacy
    world=None
    if any((s.get('design') or {}).get('kind')=='map' for s in sc['segments']):
        world=PX.commons_file(legacy.WORLD,str(Path(ctx.supply)/'world'),want_w=4000)
    designs=legacy._designs(ctx,sc,script['dossier'],world,str(Path(ctx.supply)/'design'))
    pack=dict(designs=designs,sid=ctx.sid,narration=nar['wav'],words=nar['words'],ffmpeg=PX.FF,footage=ft,record=record)
    save(pk,dict(pack=pack,record=record))
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
    def __init__(self, gaps):
        self.gaps=gaps
        super().__init__('footage gaps require a new search, never a repeated picture: '+json.dumps(gaps)[:1500])


def schedule(sc, words, ft):
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
        c=dict(path=v['file'],source=v['source'],time_range=v['time_range'],seconds=(b-a)/FPS,w=W,h=H)
        used.setdefault(c['source']['url'],[]).append(tuple(c['time_range']))
        pos=a
        while pos<b:
            stop=min(b,pos+234)
            shots.append(dict(f=[pos,stop],seg=v['after'],clip=c,src=[(pos-a)/FPS,(stop-a)/FPS],say=v['said'],real_voice=v['person']))
            pos=stop
    for i,s in enumerate(sc['segments']):
        a,b=starts[i:i+2]
        intervals=[(a,b)]
        for va,vb in voice_spans:
            cut=[]
            for lo,hi in intervals:
                if vb<=lo or va>=hi: cut.append((lo,hi)); continue
                if lo<va: cut.append((lo,va))
                if vb<hi: cut.append((vb,hi))
            intervals=cut
        clips=by.get(i,{}).get('clips',[])
        for a,b in intervals:
            pos=a
            for ci,c in enumerate(clips):
                seconds=min(7.8,(b-pos)/FPS,float(c['seconds']))
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
                missing.append(dict(seg=i,seconds=(b-pos)/FPS,words=s['say']))
    if missing: raise CoverageError(missing)
    for r in shots:
        r['context']=r['say']
        spoken=[w['w'] for w in clock.words if w['t0']<r['f'][1]/FPS and w['t1']>r['f'][0]/FPS]
        if spoken:r['say']=' '.join(spoken)
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


def build(sc,pack):
    worker()
    from film_long import Plan
    shots,clock,end=schedule(sc,pack['words'],pack['footage'])
    chapters=chapter_clock(sc,clock,end)
    voices={'cap':dict(font='InterDisplay-Bold.ttf',px=40,track=0,shadow={'a':0.9}),
            'chapter':dict(font='InterDisplay-Bold.ttf',px=48,track=0)}
    P=Plan(voices,{})
    for j,row in enumerate(shots):
        c=row['clip']
        if c.get('kind')=='image':
            key=P.image(f'evidence-photo-{j}',c)
            crop=F.cover_crop(c['w'],c['h'],W/H,(.5,.5),(.5,.5))
            P.shot(row['f'],key,F.push(crop,row['f'][1]-row['f'][0],.035,(.5,.5)))
        else:
            key=P.video(f'archive-{j}',c)
            P.shot(row['f'],key,[0,0,1,1],src=row['src'])
    # Subtitles remain legible and subordinate to the archive. No held number pages.
    for si in range(len(sc['segments'])):
        ws=clock.seg_words(si)
        for j in range(0,len(ws),7):
            chunk=ws[j:j+7]; a=round(chunk[0]['t0']*FPS); b=round(chunk[-1]['t1']*FPS)
            if b>a:
                P.word([a,b],' '.join(w['w'] for w in chunk),'cap',0.5,0.89,align='c',role='caption',note=False)
    for ch in chapters:
        a=round(ch['start']*FPS)
        P.word([a,min(a+75,end)],ch['title'],'chapter',0.055,0.08)
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
    p=Path(ctx.supply)/'l1.json'; save(p,l1)
    save(Path(ctx.supply)/'shots.json',shots)
    return dict(l1=str(p),chapters=chapters,picture_time_s=sum((r['f'][1]-r['f'][0])/FPS for r in shots),
                pictures=len(shots),max_hold_s=max((r['f'][1]-r['f'][0])/FPS for r in shots),spend=[])


def render(ctx,script,sc):
    # Use the established scene renderer, unchanged brand finish, and SND3 master.
    import film_long as L
    pack=read(Path(ctx.supply)/'pack.json')['pack']
    inputs=dict(narration=digest(pack['narration']),l1=digest(sc['l1']),mixer=digest(HERE/'film_long.py'),music=digest(HERE/'music_chapters.py'))
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
    result['clean']=result['film']; result['film']=str(out)
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
    if not fs.exists() or read(fs).get('sha256')!=programme:
        final.unlink(missing_ok=True)
    if not final.exists():
        # Brand's own watermark and end card, never recreated or altered here.
        job=Path(ctx.supply)/'finish.py'
        job.write_text('import sys; sys.path[:0]='+repr([str(HERE.parent/'_shell')])+"; import brand; brand.finish("+repr(rd['film'])+','+repr(str(final))+','+repr(ctx.tmp)+',accent=False)')
        chain.sh(ctx,[chain.PY,str(job)],label='wf4-brand-'+ctx.run_id,heavy=True,timeout=7200)
        save(fs,dict(sha256=programme))
    base=brand.check(rd['film'],str(final),ctx.tmp)
    base['recorded_sound_library']=chain.no_music(sc['l1'])
    base['ok']=base['ok'] and base['recorded_sound_library']['ok']
    from picture_check import check as pictures_check, motion, duplicate_check
    pr=Path(ctx.supply)/'picture_check.json'
    stamp=Path(ctx.supply)/'picture-input.json'
    inputs=dict(film=digest(final),shots=digest(Path(ctx.supply)/'shots.json'))
    if not stamp.exists() or read(stamp)!=inputs:
        for name in ('picture_check.json','motion.json'):(Path(ctx.supply)/name).unlink(missing_ok=True)
        folder=Path(ctx.supply)/'picture-evidence'
        if folder.exists():
            prior=read(stamp)['film'][:12] if stamp.exists() else 'unversioned'
            rejected=Path(ctx.supply)/('picture-evidence-rejected-'+prior)
            if rejected.exists():shutil.rmtree(rejected)
            folder.rename(rejected)
        save(stamp,inputs)
    if not pr.exists(): save(pr,pictures_check(str(final),read(Path(ctx.supply)/'shots.json'),Path(ctx.supply)/'picture-evidence',PX.FF))
    pics=read(pr)
    mp=Path(ctx.supply)/'motion.json'
    if not mp.exists(): save(mp,motion(str(final),read(Path(ctx.supply)/'shots.json'),PX.FF))
    measured_motion=read(mp)
    duplicates=duplicate_check(measured_motion,read(Path(ctx.supply)/'shots.json'),Path(ctx.supply)/'picture-evidence')
    shot_rows=read(Path(ctx.supply)/'shots.json')
    real_ids={int(i) for i,r in pics['evidence'].items() if r.get('real_moving_footage') and shot_rows[int(i)]['clip'].get('kind')!='image'}
    measured_real=sum(r['seconds'] for r in measured_motion['rows'] if r['i'] in real_ids and r['moving_samples_share']>=0.25)/sc['picture_time_s']
    pics['moving_share']=measured_real
    nd=Path(pack['narration']).parent
    nar=nd/'narration_narrator.wav'
    if not nar.exists(): nar=Path(pack['narration'])
    metrics=Path(ctx.supply)/'voice-metrics'
    guarded_voice(ctx,[PX.NARRATE_PY,str(HERE/'measure_voice.py'),str(nar),str(nd/'narration_script.json'),str(metrics)],'wf4-voice-measure-'+ctx.run_id)
    guarded_voice(ctx,[PX.NARRATE_PY,str(HERE/'measure_mix.py'),str(final),pack['words'],str(nd/'narration_script.json'),str(metrics),PX.FF],'wf4-mix-listen-'+ctx.run_id)
    voice=read(metrics/'metrics.json')
    mix=read(metrics/'mix-wer.json')
    sound_metrics=Path(ctx.supply)/'sound-measured.json'
    chain.sh(ctx,[chain.PY,str(HERE/'measure_sound.py'),str(final),rd['clean'],sc['l1'],pack['words'],str(sound_metrics),PX.FF],label='wf4-sound-measure-'+ctx.run_id,heavy=True,timeout=1800)
    heard_sound=read(sound_metrics)
    duration=brand.probe(str(final))['dur']
    repeats=duplicate_ranges(read(Path(ctx.supply)/'shots.json'))
    hj=Path(ctx.run_dir)/'hook_measured.json'
    if not hj.exists():
        opening=' '.join(w['w'] for w in read(pack['words'])['words'] if w['start']+F.LEAD_F/FPS<10)
        text,use=wf4.story.writer.ask('Judge only whether the supplied words, heard in the first ten seconds of a documentary, establish concrete danger, a contradiction or an unanswered curiosity. Return JSON {hook:bool,why:string}. No numeric quality rating.',opening,effort='low',max_tokens=600,what='WF4 first ten seconds')
        save(hj,dict(wf4.story.writer.json_block(text),words=opening,usage=use))
    hook=read(hj)
    checks=dict(base,longform=dict(seconds=duration,pictures=sc['pictures'],planned_picture_changes_per_10s=sc['pictures']/(sc['picture_time_s']/10),
                real_moving_picture_time_share=pics['moving_share'],pictures_checked=pics['checked'],picture_matches=pics['ok'],
                duplicate_source_ranges=len(repeats),duplicate_pairs=repeats,visual_duplicates=duplicates,hook=hook,longest_shot_s=sc['max_hold_s'],music_share=heard_sound['music_share'],sound=heard_sound,
                chapter_rises=heard_sound['chapter_rises'],
                narration=voice,delivered_mix_wer=mix['wer'],real_voices=pack['record']['real_voices'],chapters=sc['chapters'],credits=pack['footage']['credits']))
    structure=read(Path(ctx.run_dir)/'structure.json')
    targets=dict(hook=hook.get('hook') is True,turns=len(structure['chapters'])==len(sc['chapters']),length=600<=duration<=1200,footage=pics['moving_share']>=0.5,unique=not repeats and duplicates['ok'],holds=sc['max_hold_s']<=8,
                 pictures=pics['ok'],music=heard_sound['music_share']>=0.8,rises=all(r['rise_db']>=3 for r in heard_sound['chapter_rises']),voice=voice['ok'],mix_wer=mix['ok'])
    checks['targets']=targets; checks['ok']=bool(base['ok'] and all(targets.values()))
    save(Path(ctx.run_dir)/'measured.json',checks)
    if not checks['ok']: raise RuntimeError('delivered-file gates failed: '+json.dumps(targets))
    evidence=Path(ctx.state_root).parent/'evidence'/ctx.run_id
    evidence.mkdir(parents=True,exist_ok=True)
    for src in [pr,mp,sound_metrics,Path(ctx.run_dir)/'measured.json',Path(ctx.supply)/'shots.json',Path(rd['film']+'.json')]:
        shutil.copy2(src,evidence/src.name)
    shutil.copytree(Path(ctx.supply)/'picture-evidence',evidence/'pictures',dirs_exist_ok=True)
    shutil.copytree(metrics,evidence/'voice-metrics',dirs_exist_ok=True)
    subprocess.run([PX.FF,'-v','error','-y','-i',rd['clean'],'-vn','-c:a','flac',str(evidence/'clean-audio.flac')],check=True)
    shutil.copy2(nar,evidence/'narration.wav')
    shutil.copy2(pack['words'],evidence/'words.json')
    footage_box.cleanup_cache(Path(ctx.supply)/'footage')
    footage_box.cleanup_cache(Path(ctx.supply)/'footage'/'witnesses')
    return dict(final=str(final),checks=checks,music=rd['music'],music_in_final=True,spend=[])

