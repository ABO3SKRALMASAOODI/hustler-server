"""Conform the story to inspected archive before spending the final voice read.

Models interpret the story and evidence. String checks only validate citations,
word anchors and catalogue identifiers; the encoded-film gates stay unchanged.
"""
from pathlib import Path
import copy,json,shutil


def preserve_takes(prior,current,new_groups):
    """Reindex unchanged spoken groups without resetting their take budget."""
    import cinema as C
    prior,current=Path(prior),Path(current)
    generations=prior/'_generations.json'
    if not generations.exists():return []
    old=C.read(generations);kept=[]
    for j,after in enumerate(new_groups['segments']):
        matches=[i for i,before in enumerate(old['segments']) if before['text']==after['text']]
        if not matches:continue
        # If the previous draft repeated this group, retain its fullest budget.
        i=max(matches,key=lambda i:len(list((prior/'_takes').glob(f's{i:02d}_t*.wav'))))
        files=list((prior/'_takes').glob(f's{i:02d}_t*.wav'))
        if not files:continue
        dest=current/'_takes';dest.mkdir(exist_ok=True)
        for src in files:
            target=dest/(f's{j:02d}_'+src.name.split('_',1)[1])
            if not target.exists():shutil.copy2(src,target)
        kept.append(dict(before=i,after=j,takes=len(files)))
    return kept


def patch_script(script,edits):
    revised=copy.deepcopy(script);seen=set()
    for row in edits.get('segments',[]):
        i=row['index'];changes=row['changes']
        if not isinstance(i,int) or not 0<=i<len(revised['segments']) or i in seen:
            raise ValueError('invalid screenplay repair index')
        if not set(changes)<=set(revised['segments'][i]):raise ValueError('unknown screenplay field')
        revised['segments'][i].update(changes);seen.add(i)
    chapters={c['id']:c for c in revised['chapters']};seen=set()
    for row in edits.get('chapters',[]):
        key=row['id'];changes=row['changes']
        if key not in chapters or key in seen or not set(changes)<=(set(chapters[key])-{'id'}):
            raise ValueError('invalid chapter repair')
        chapters[key].update(changes);seen.add(key)
    return revised


def propose(ctx,script,catalogue,coverage=None):
    import cinema as C
    from wf4 import story
    root=Path(ctx.supply)/'archive-script';root.mkdir(exist_ok=True)
    existing=root/'proposal.json'
    if existing.exists():return C.read(existing)
    material=[dict(id=r['id'],visible=r['visible'],form=r['form'],moving=r['real_moving_footage'],
                   seconds=r['clip']['seconds'],source=r['clip']['source'],limitations=r['limitations']) for r in catalogue if r['usable']]
    research=root/'additional-facts.json'
    if research.exists():supplement=C.read(research)
    else:
        text,use=story.writer.ask_web('Research at most eight additional primary-source facts that explain the stakes and consequential human work visible in this documentary archive. '
            'The story needs a reason to care: what its protagonist risked and what was lost if they failed, not more mechanical summaries. '
            'Use only claims directly supported by exact quotations on fetchable primary pages. Do not add unrelated history to fill duration. '
            'Return JSON {facts:[{id:"a1",claim,quote,url,year}]}. Keep each quote concise; name the source page accurately. '
            'Existing verified facts need not be repeated.',json.dumps(dict(title=script['title'],facts=script['dossier']['facts'],archive=material)),
            searches=8,fetches=8,max_tool_calls=12,effort='high',max_tokens=4000,what='WF4 archive-supported stakes')
        raw=story.writer.json_block(text);verified,proof=story.check_dossier(raw)
        supplement=dict(verified,proof=proof,usage=use);C.save(research,supplement)
    dossier=copy.deepcopy(script['dossier']);existing_ids={f['id'] for f in dossier['facts']}
    for fact in supplement['facts']:
        if fact['id'] in existing_ids:raise ValueError('additional fact ID collides with the dossier')
        dossier['facts'].append(fact);existing_ids.add(fact['id'])
    old_script=dict(title=script['script']['title'],chapters=script['script']['chapters'],
                    segments=[{k:s[k] for k in ('say','chapter','beat','facts') if k in s} for s in script['script']['segments']])
    for i,segment in enumerate(old_script['segments']):
        supply = (coverage or {}).get(str(i))
        if supply:
            segment['requested_visual'] = supply['requested']
            segment['retained_archive_ids'] = [r['id'] for r in catalogue if r['usable'] and
                any(r['clip']['source']['url']==c['url'] and r['clip']['time_range']==c['time_range']
                    for c in supply['retained'])]
    review=Path(ctx.run_dir)/'editorial-review.json'
    critique=C.read(review) if review.exists() else None
    if critique:critique=critique.get('diagnosis',critique)
    notes=None
    for attempt in range(3):
        checkpoint=root/f'attempt-{attempt}.json'
        if checkpoint.exists():
            saved=C.read(checkpoint);proposal={k:saved[k] for k in ('script','why')};use=saved['usage']
        elif attempt:
            text,use=story.writer.ask('Repair ONLY the stated audit issues in this documentary. Preserve every unaffected sentence, visual choice, chapter and their order. '
                'Return JSON {segments:[{index:zero-based segment index,changes:{only fields needing replacement}}], '
                'chapters:[{id,changes:{only fields needing replacement}}],why:string}. '
                'If spoken words change, keep the visual_beats word_start anchors valid for the revised wording. '
                'Use only supplied facts and literal catalogue IDs. Do not rewrite the whole screenplay.',
                json.dumps(dict(script=proposal['script'],issues=notes,facts=dossier['facts'],archive=material)),
                effort='high',max_tokens=16000,what='WF4 precise screenplay repair')
            C.save(root/f'response-{attempt}.json',dict(text=text,usage=use))
            if use.get('stop')!='completed':raise ValueError('screenplay repair response incomplete; preserved for diagnosis')
            edits=story.writer.json_block(text)
            proposal=dict(script=patch_script(proposal['script'],edits),why=edits['why'])
        else:
            text,use=story.writer.ask('Write the final documentary script from the inspected archive and verified facts. '
                'The old script is a research draft, not a structure to preserve. Its long repeated explanations and multiple endings must go. '
                'Tell it as a story, not an explainer: '+story.STORY_SHAPE+' '
                'The idea names its protagonist, dramatic question and stakes; keep them at the centre. Never add filler or turn the film into an archive inventory. '
                'Default to 10–20 minutes and 1900–2200 spoken words. A running time in the production brief takes precedence. '
                'Write in 55–90 complete-sentence segments, with a hook in the first ten seconds and a real turn in each story chapter. '
                'Each chapter comes from this story. Do not announce a question mechanically at every chapter or repeatedly explain what the audience will understand. '
                'For draft lines with retained_archive_ids, use those verified alternatives where they fit. A primary passage can show the claim directly. '
                'A requested_visual describes a search, never an acquired shot. For uncovered words choose a precise inspected replacement or rewrite around available evidence. '
                'Use the actual available moving archive for most of the picture time, with original illustrations and cited records only where they genuinely explain an unfilmed event. '
                'A picture may supply truthful context without visibly proving an invisible cause or motive; never imply that it depicts the wrong person, action, hardware, era or repair state. '
                'Do not claim every decisive action was filmed. When an original drawing or a rehearsal stands in for the real event, its on-screen label says so; the narration does not. '
                'No fabricated historical views, generic substitutes or reused pictures. Do not turn a claimed source description into a fact when the catalogue marks it uncertain. '
                'Give the human stakes enough space to matter. Keep the climax continuous and end at its payoff. '
                'Preserve excellent complete sentences from the old script when useful, including its hook, but remove repetition. '
                'All spoken numbers must be written out. Use clear spoken forms for difficult names and compounds; explain technical language simply. '
                'Write for the ear (the narrator reads only the words and punctuation; every full stop is a falling end): '+story.EAR+' '
                'Return JSON {script:{title,promise,names:[proper nouns],chapters:[{id,title,question,turn,music_mood,pace}], '
                'testimony_purpose:string,segments:[{chapter,say,beat:"hook"|"promise"|"setup"|"loop"|"turn"|"low"|"payoff"|"close", '
                'facts:[verified fact IDs],pictures:[named subjects],design:null or a cited document {kind:"document",fact,title,mark} '
                'or quotation {kind:"quote",fact,who,text} to show the original on the line invoking it,pace:"slow"|"steady"|"brisk",melody:"falling"|"rising"|"level"|"wide", '
                'emphasis:[one or two spoken words],pause_after:0.22,direction:string, '
                'visual_beats:[{word_start:zero-based whitespace-word index in this say,archive_ids:[catalogue IDs in intended order],label:optional truthful source-context label}]}]},why:string}. '
                'Every segment begins with a visual beat at word_start 0. Introduce a new beat when the pictured subject changes. '
                'Choose enough unseen source seconds for each beat at about three spoken words per second; each individual picture plays at most 7.8 seconds. '
                'Use unique actual source intervals, not new crops of one still. Prefer the moving clips when they fit. A label clarifies a verified source, never manufactures its identity. '
                'Keep archive_ids as literal catalogue IDs. Do not invent them. A segment facts list must support all its factual claims. '
                'Return the full revised script, not editing notes.',
                json.dumps(dict(idea={k:script.get('idea_full',{}).get(k) for k in ('title','protagonist','question','stakes','hook','turn','payoff','arc')},
                                old_script=old_script,facts=dossier['facts'],archive=material,editorial_review=critique,repair_notes=notes)) + story.production_brief(ctx.args.get('line')),
                effort='high',max_tokens=36000,what='WF4 archive-conformed screenplay')
            C.save(root/f'response-{attempt}.json',dict(text=text,usage=use))
            if use.get('stop')!='completed':raise ValueError('archive screenplay response incomplete; preserved for diagnosis')
            proposal=story.writer.json_block(text)
        sc=proposal['script'];check=story.check_script(sc,dossier)
        ids={r['id'] for r in catalogue if r['usable']};issues=list(check['problems'])
        for i,segment in enumerate(sc['segments']):
            beats=segment.get('visual_beats',[]);starts=[b.get('word_start') for b in beats]
            if not starts or starts[0]!=0 or any(not isinstance(n,int) for n in starts) or starts!=sorted(set(starts)) or starts[-1]>=len(segment['say'].split()):
                issues.append(f'segment {i} has invalid visual word anchors')
            for beat in beats:
                if not beat.get('archive_ids') or any(k not in ids for k in beat['archive_ids']):issues.append(f'segment {i} needs real usable catalogue IDs')
        C.save(root/f'attempt-{attempt}.json',dict(proposal,usage=use,check=check,issues=issues))
        if issues:notes=issues;continue
        if (root/f'audit-{attempt}.json').exists():audit=C.read(root/f'audit-{attempt}.json')
        else:
            text,audit_use=story.writer.ask('Audit this documentary narration against its cited verified source quotes and archive catalogue. '
                'Every factual claim, identity, chronology, causal statement, quantity and source-context label must be supported. '
                'Check that visual choices do not represent the wrong event, crew, hardware or repair state. A contextual image need not prove an invisible cause, '
                'but it must not contradict the words. A fix states the supported fact plainly or moves context to the picture label; '
                'it never asks the narration to add a disclaimer or a \'this was not X\' clarification. No quality score. Return JSON {faithful:bool,issues:[specific fixes],why:string}.',
                json.dumps(dict(script=sc,facts=dossier['facts'],archive=material)),effort='high',max_tokens=4000,what='WF4 conformed screenplay fact audit')
            audit=story.writer.json_block(text);C.save(root/f'audit-{attempt}.json',dict(audit,usage=audit_use))
        if audit.get('faithful') is not True:notes=audit['issues'];continue
        result=dict(script=sc,dossier=dossier,check=check,audit=audit,why=proposal['why'])
        C.save(existing,result);return result
    raise ValueError('archive-conformed script did not pass factual and coverage-identifier checks')


def apply(ctx,script,ft,proposal,catalogue):
    """Checkpoint the verified revision, preserving the prior read as evidence."""
    import cinema as C
    from wf4 import story
    root=Path(ctx.supply)/'archive-script';root.mkdir(exist_ok=True)
    after_script=root/'script-after.json';after_footage=root/'footage-after.json'
    if not after_script.exists():
        updated=copy.deepcopy(script);updated['script']=proposal['script'];updated['dossier']=proposal['dossier'];updated['check']=proposal['check']
        updated['title']=proposal['script']['title'];updated['words']=proposal['check']['words']
        updated['hook_line']=proposal['script']['segments'][0]['say'];updated['close_line']=proposal['script']['segments'][-1]['say']
        used={fid for s in proposal['script']['segments'] for fid in s['facts']}
        updated['facts_cited']=[f for f in proposal['dossier']['facts'] if f['id'] in used]
        available={r['id']:r['clip'] for r in catalogue if r['usable']}
        conformed=dict(lines=[],voices=[],credits=[],spend=ft.get('spend',[]),
                       editorial=dict(pins=[],rejected=[],bank=list(available.values())))
        for i,segment in enumerate(proposal['script']['segments']):
            clips=[]
            for beat in segment['visual_beats']:
                for key in beat['archive_ids']:
                    c=copy.deepcopy(available[key]);c['catalogue_id']=key
                    if beat.get('label'):c['editorial_label']=beat['label']
                    clips.append(c);conformed['credits'].append(dict(line=i,**c['source']))
            conformed['lines'].append(dict(i=i,clips=clips))
        pool=copy.deepcopy(ft['voices'])
        supplemental=Path(ctx.supply)/'footage/witnesses-additional/footage.json'
        if supplemental.exists():
            extra=dict(lines=[],voices=pool,credits=[],spend=[])
            C.add_footage(extra,C.read(supplemental),supplemental.parent);pool=extra['voices']
            conformed['spend']+=extra['spend']
        placement=root/'voices.json'
        if pool:
            if placement.exists():placed=C.read(placement)
            else:
                text,use=story.writer.ask('Place the available verified firsthand testimony in the revised documentary. '
                    'Choose only inserts that add something, not repeats of the narrator or adjacent interviews. '
                    'Preserve what each person actually meant, including prelaunch versus retrospective statements. '
                    'Return JSON {voices:[{index:input voice index,after:zero-based narration segment index,why:string}]}. '
                    'No insert after the last narration segment. Each source clip may occur once; selecting none is allowed when none fits.',
                    json.dumps(dict(segments=proposal['script']['segments'],voices=[dict(index=i,person=v['person'],said=v['said'],source=v['source']) for i,v in enumerate(pool)])),
                    effort='high',max_tokens=1600,what='WF4 revised testimony placement')
                placed=dict(story.writer.json_block(text),usage=use);C.save(placement,placed)
            selected=set();after=set()
            for row in placed['voices']:
                i,a=row['index'],row['after']
                if not 0<=i<len(pool) or not 0<=a<len(proposal['script']['segments'])-1 or i in selected or a in after:
                    raise ValueError('invalid testimony placement')
                v=dict(pool[i],after=a);conformed['voices'].append(v);selected.add(i);after.add(a)
                conformed['credits'].append(dict(voice=v['person'],**v['source']))
        C.save(root/'script-before.json',script);C.save(root/'footage-before.json',ft)
        C.save(after_footage,conformed);C.save(after_script,updated)
    # An interrupted apply resumes from the prepared pair. No audio is erased.
    old_voice=root/'narration-before';nd=Path(ctx.supply)/'narration'
    if nd.exists() and not old_voice.exists():nd.rename(old_voice)
    nd.mkdir(exist_ok=True)
    if not (root/'reused-takes.json').exists():
        import voice
        ns=story.narration_script(C.read(after_script)['script'])
        groups=voice.group_script(ns,voice.groups_of(ns['segments']))
        C.save(root/'reused-takes.json',preserve_takes(old_voice,nd,groups))
    for name in ('structure.json','hook_measured.json'):
        src=Path(ctx.run_dir)/name
        if src.exists():src.rename(root/('before-'+name))
    updated=C.read(after_script);conformed=C.read(after_footage)
    C.save(Path(ctx.run_dir)/'script.json',updated);C.save(Path(ctx.supply)/'footage/footage.json',conformed)
    C.save(root/'applied.json',dict(script=str(after_script),footage=str(after_footage),prior_read=str(old_voice)))
    script.clear();script.update(updated);ft.clear();ft.update(conformed)
    return script,ft


def conform(ctx,script,ft):
    import cinema as C
    root=Path(ctx.supply)/'archive-script'
    if (root/'applied.json').exists():return script,ft
    from archive_catalog import catalogue
    rows=catalogue(ctx,ft)
    proposal=propose(ctx,script,rows,ft.get('prefill_coverage'))
    return apply(ctx,script,ft,proposal,rows)
