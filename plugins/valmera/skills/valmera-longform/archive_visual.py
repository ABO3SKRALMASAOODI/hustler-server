"""Resolve archive choices on a provisional word clock before the final read.

This is editorial preflight, not delivered-film measurement. The rendered film
still receives the unchanged picture, motion and duplicate checks.
"""
from pathlib import Path
import copy,json,hashlib


def work_dir(ctx,proposal,catalogue):
    inputs=dict(words=[s['say'] for s in proposal['script']['segments']],catalogue=catalogue,
                checker=Path(__file__).with_name('picture_check.py').read_text(),planner=Path(__file__).read_text())
    key=hashlib.sha256(json.dumps(inputs,sort_keys=True).encode()).hexdigest()[:16]
    return Path(ctx.supply)/'archive-visual'/key


def preview(sc,catalogue,wps=3.3):
    import cinema as C
    available={r['id']:r['clip'] for r in catalogue if r['usable']}
    words=[];t=0.;ft=dict(lines=[],voices=[])
    for i,s in enumerate(sc['segments']):
        for w in s['say'].split():
            words.append(dict(w=w,start=t,end=t+.9/wps,seg=i));t+=1/wps
        t+=s.get('pause_after',.22)
        clips=[]
        for beat in s['visual_beats']:
            for key in beat['archive_ids']:
                c=copy.deepcopy(available[key]);c['catalogue_id']=key
                if beat.get('label'):c['editorial_label']=beat['label']
                clips.append(c)
        ft['lines'].append(dict(i=i,clips=clips))
    try:shots,_,_=C.schedule(sc,dict(duration=t,words=words),ft);gaps=[]
    except C.CoverageError as e:shots=e.shots;gaps=e.gaps
    return shots,gaps,t


def refine(ctx,proposal,catalogue,rounds=1):
    import cinema as C
    from wf4 import story
    root=work_dir(ctx,proposal,catalogue);root.mkdir(parents=True,exist_ok=True)
    done=root/'proposal.json'
    if done.exists():return C.read(done)
    sc=copy.deepcopy(proposal['script'])
    material=[dict(id=r['id'],visible=r['visible'],form=r['form'],moving=r['real_moving_footage'],
                   seconds=r['clip']['seconds'],source=r['clip']['source'],limitations=r['limitations']) for r in catalogue if r['usable']]
    for attempt in range(rounds+1):
        if (root/f'script-{attempt}.json').exists():sc=C.read(root/f'script-{attempt}.json')
        else:C.save(root/f'script-{attempt}.json',sc)
        shots,gaps,duration=preview(sc,catalogue)
        report=C.source_review(ctx,shots,required=False)
        C.save(root/f'preview-{attempt}.json',dict(planning_only=True,estimated_seconds=duration,gaps=gaps,shots=shots,report=report))
        ctx.log(f"archive preflight {attempt}: {len(shots)} pictures, {len(report['rejected'])} mismatches, {sum(g['seconds'] for g in gaps):.1f}s short, {report['moving_share']:.1%} moving in covered preview")
        if report['ok'] and not gaps and report['moving_share']>=.55:
            result=dict(proposal,script=sc);C.save(done,result);return result
        if attempt==rounds:break
        edits=root/f'edit-{attempt}.json'
        if edits.exists():repair=C.read(edits)
        else:
            failures=[dict(seg=shots[i]['seg'],words=shots[i]['say'],archive=shots[i]['clip'].get('catalogue_id'),
                           reason=report['evidence'].get(str(i),report['evidence'].get(i))['reason']) for i in report['rejected']]
            from picture_check import REVIEW_PROMPT
            text,use=story.writer.ask('Offer truthful picture alternatives for this documentary using ONLY the inspected archive. '
                'Keep every spoken word and chapter unchanged. Correct the rejected picture/word pairings where possible. '
                'A label saying the action is not visible does not make irrelevant hardware illustrate that action. '
                'Original technical illustrations may explain an unfilmed mechanism, but must match its actual state and be clearly identified. '
                'Do not put an unrelated spacewalk, ground object or wrong stage of repair under a literal action. '
                'Also use more of the suitable unused moving archive where current stills can be replaced honestly. '
                'Prefer suitable moving footage, never by mislabelling stills. '
                'Do NOT solve global timeline coverage, duration arithmetic or source allocation. The scheduler handles those, with strict non-reuse. '
                'You may offer the same candidate in several alternative lists; the scheduler will only use its original frames once. '
                'Give several genuinely suitable alternatives per beat when available. Rank moving alternatives first when they illustrate the words equally well. '
                'Use short truthful source labels viewers can read, not internal review instructions. '
                'Return JSON {segments:[{index,visual_beats:[{word_start,archive_ids:[literal IDs],label:optional source label}]}],unresolved:[specific remaining limitations],why:string}. '
                'Return only segments whose visual choices change. Every listed segment must start at word_start zero. '
                'Return concrete corrections promptly. Leaving an unsupported passage unresolved is valid and required when no candidate fits; never fake coverage. '
                'Select pictures using this same distinction between truthful documentary context and false depiction: '+REVIEW_PROMPT,
                json.dumps(dict(script=sc,archive=material,failures=failures,gaps=gaps,
                                estimated_seconds=duration,covered_moving_share=report['moving_share'])),
                effort='medium',max_tokens=24000,what='WF4 exact archive visual repair')
            C.save(root/f'response-{attempt}.json',dict(text=text,usage=use))
            if use.get('stop')!='completed':raise ValueError('visual repair response incomplete')
            repair=dict(story.writer.json_block(text),usage=use);C.save(edits,repair)
        ids={r['id'] for r in catalogue if r['usable']};seen=set()
        for row in repair['segments']:
            i=row['index'];beats=row['visual_beats'];starts=[b['word_start'] for b in beats]
            if not isinstance(i,int) or not 0<=i<len(sc['segments']) or i in seen:raise ValueError('invalid visual repair segment')
            if not starts or starts[0]!=0 or starts!=sorted(set(starts)) or starts[-1]>=len(sc['segments'][i]['say'].split()):
                raise ValueError('invalid visual repair word anchor')
            if any(any(k not in ids for k in b['archive_ids']) for b in beats):raise ValueError('invalid archive ID')
            sc['segments'][i]['visual_beats']=beats;seen.add(i)
    # Estimated-clock preflight is an editorial input, not a delivery gate.
    # Empty candidate lists explicitly request fresh research on the recorded
    # clock. The supply stage and final encoded-film gates still require every
    # interval to be covered truthfully and all delivered targets to pass.
    result=dict(proposal,script=sc,preflight=dict(planning_only=True,complete=False,
                gaps=gaps,rejected=report['rejected'],moving_share=report['moving_share']))
    C.save(done,result);return result


def apply_choices(script,ft,revised,catalogue):
    """Only pictures change after the spoken script is frozen for recording."""
    before=script['script']['segments'];after=revised['script']['segments']
    if [s['say'] for s in before]!=[s['say'] for s in after]:raise ValueError('visual revision changed spoken words')
    available={r['id']:r['clip'] for r in catalogue if r['usable']}
    lines=[];credits=[]
    for i,(s,new) in enumerate(zip(before,after)):
        s['visual_beats']=copy.deepcopy(new['visual_beats']);clips=[]
        for beat in s['visual_beats']:
            for key in beat['archive_ids']:
                c=copy.deepcopy(available[key]);c['catalogue_id']=key
                if beat.get('label'):c['editorial_label']=beat['label']
                clips.append(c);credits.append(dict(line=i,**c['source']))
        lines.append(dict(i=i,clips=clips))
    credits.extend(dict(voice=v['person'],**v['source']) for v in ft.get('voices',[]))
    ft.update(lines=lines,credits=credits,editorial=dict(pins=[],rejected=[],bank=list(available.values())))


def prepare(ctx,script,ft):
    import cinema as C
    catalogue=C.read(Path(ctx.supply)/'archive-catalogue/catalogue.json')
    proposal=C.read(Path(ctx.supply)/'archive-script/proposal.json')
    proposal['script']=copy.deepcopy(script['script'])
    root=work_dir(ctx,proposal,catalogue);marker=root/'applied.json'
    if marker.exists():return
    revised=refine(ctx,proposal,catalogue)
    apply_choices(script,ft,revised,catalogue)
    C.save(Path(ctx.run_dir)/'script.json',script);C.save(Path(ctx.supply)/'footage/footage.json',ft)
    C.save(marker,dict(words_unchanged=True,planning=revised.get('preflight')))
