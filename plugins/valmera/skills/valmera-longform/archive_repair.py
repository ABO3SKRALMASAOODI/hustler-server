"""Edit an audited archive screenplay in place on its existing segment and evidence clock."""
import copy
import hashlib
import json
from difflib import SequenceMatcher
from urllib.parse import urldefrag


def word_clock(before, after):
    """Map old word boundaries into revised speech, including replaced phrases."""
    old, new = before.split(), after.split()
    clock = {}
    for _, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        for i in range(a, b):
            clock[i] = min(len(new) - 1, c + (i - a) * (d - c) // (b - a))
    clock[0] = 0
    return clock


def source_label(row):
    source = row['clip']['source']
    return ' · '.join(filter(None, [source.get('credit'), source.get('title'),
                                    source.get('presentation') or row['form']]))


def exhibit_facts(catalogue, facts):
    """Join only attributable quotations or an explicit exhibit-to-fact link."""
    by_source = {}
    for fact in facts:
        if fact.get('url') and fact.get('quote'):
            by_source.setdefault(urldefrag(fact['url']).url, []).append(fact)
    matches = {}
    for key, row in catalogue.items():
        clip = row['clip']
        ids = {clip['fact']} if clip.get('fact') else set()
        for passage in clip['source'].get('primary_context', []):
            url = passage.get('source_url') or clip['source'].get('url', '')
            quote = ' '.join(passage.get('quote', '').casefold().split())
            for fact in by_source.get(urldefrag(url).url, []):
                if ' '.join(fact['quote'].casefold().split()) in quote:
                    ids.add(fact['id'])
        matches[key] = ids
    return matches


def merge_beats(old, revised, incoming, catalogue, evidence, *, complete=False):
    """Apply a list patch on the same beats; holes carry no editorial selection."""
    clock = word_clock(old['say'], revised['say'])
    length = len(revised['say'].split())
    prior = old.get('visual_beats', [])
    remaining = set(range(len(prior)))
    assigned = {k for beat in incoming for k in beat.get('archive_ids', [])}
    slots = []
    for index, beat in enumerate(incoming):
        start = beat.get('word_start')
        ids = [k for k in beat.get('archive_ids', []) if k in catalogue]
        if len(incoming) == len(prior):
            slot = index
        else:
            slot = next((i for i in sorted(remaining) if set(ids).intersection(prior[i].get('archive_ids', []))), None)
            if slot is None:
                slot = next((i for i in sorted(remaining) if start == clock.get(prior[i].get('word_start'))), None)
        previous = prior[slot] if slot is not None else {}
        remaining.discard(slot)
        old_start = previous.get('word_start')
        if previous and (not isinstance(start, int) or (start == old_start and not beat.get('measured'))):
            start = clock.get(old_start, start)
        if not isinstance(start, int) or not length:
            continue
        fixed = dict(beat, archive_ids=ids, word_start=min(max(0, start), length - 1))
        fixed.pop('measured', None)
        requested = fixed.pop('source_fact', None)
        if not ids:
            retained = [k for k in previous.get('archive_ids', []) if k in catalogue]
            if requested or not retained:
                facts = {requested} if requested else set(revised.get('facts', []))
                matches = [k for k in catalogue if facts.intersection(evidence[k])]
                matches.sort(key=lambda k: k in assigned)
                retained = matches[:1] or retained
            if retained:
                row = catalogue[retained[0]]
                label = context_label(row) if requested and requested not in evidence[retained[0]] else source_label(row)
                fixed.update(archive_ids=retained, label=label)
        assigned.update(fixed['archive_ids'])
        slots.append(fixed)
    if complete and incoming and all(any(k in catalogue for k in b.get('archive_ids', [])) for b in incoming):
        # A complete, populated replacement can deliberately retire a wrong
        # exhibit. Sparse indexed beat edits already include their untouched slots.
        remaining.clear()
    for i in sorted(remaining):
        beat = prior[i]
        if beat.get('word_start') in clock:
            slots.append(dict(beat, word_start=clock[beat['word_start']]))
    merged = {}
    for beat in sorted(slots, key=lambda b: b['word_start']):
        start = beat['word_start'] if merged else 0
        if start in merged:
            group = merged[start]
            group['archive_ids'] = list(dict.fromkeys(group['archive_ids'] + beat['archive_ids']))
            if group.get('label') != beat.get('label'):
                group.pop('label', None)  # each clip keeps its own source attribution
        else:
            merged[start] = dict(beat, word_start=start)
    return list(merged.values())


def context_beat(revised, prior, catalogue, evidence, documents):
    """An omitted visual still needs a real plate. Bind it to the cited
    passage when available, otherwise identify the retained source as context."""
    retained = [k for b in prior for k in b.get('archive_ids', []) if k in catalogue]
    cited = set(revised.get('facts', []))
    design = revised.get('design') or {}
    preferred = {design['fact']} if design.get('fact') else cited
    alternatives = [k for k in catalogue if k not in retained]
    key = next((k for k in alternatives if preferred.intersection(evidence[k])), None)
    if key is None:
        key = next((k for k in alternatives if cited.intersection(evidence[k])), None)
    if key is None:
        urls = {documents[f] for f in preferred if f in documents}
        key = next((k for k in alternatives if urldefrag(catalogue[k]['clip']['source'].get('url', '')).url in urls), None)
    if key is None and retained:
        key = retained[0]
    if key is None:
        return []
    return [dict(word_start=0, archive_ids=[key], label=context_label(catalogue[key]))]


def context_label(row):
    clip = row['clip']
    source = clip['source']
    passage = next((p['quote'] for p in source.get('primary_context', []) if p.get('quote')), '')
    mark = clip.get('mark') or ' '.join(passage.split()[:10])
    label = 'Source context · ' + (source.get('title') or row['form'])
    if mark:
        label += ' · “' + mark + '”'
    return label


def apply_edits(script, edits, catalogue, draft, evidence=None, documents=None):
    """Sparse changes keep unmentioned rows, fields and beat selections verbatim."""
    from archive_script import patch_script
    if evidence is None:
        evidence = exhibit_facts(catalogue, [])
    edits = copy.deepcopy(edits)
    for row in edits.get('segments', []):
        old = script['segments'][row['index']]
        changes = row.setdefault('changes', {})
        if 'draft_index' in row:
            source = draft[row['draft_index']]
            # A selected draft sentence may anchor a longer, cited correction.
            # Keep the rest for factual review when the extract is verbatim.
            spoken = changes.get('say', '')
            if source['say'] in spoken and spoken != source['say']:
                changes['facts'] = list(dict.fromkeys(source['facts'] + changes.get('facts', old.get('facts', []))))
            else:
                changes.update(say=source['say'], facts=source['facts'])
        beats = changes.pop('visual_beats', None)
        complete = beats is not None
        omitted = beats == []
        if beats is None and ('say' in changes or row.get('beats')):
            beats = copy.deepcopy(old.get('visual_beats', []))
        if beats is not None:
            for edit in row.get('beats', []):
                beat = beats[edit['index']]
                if 'source_fact' in edit and 'archive_ids' not in edit:
                    beat['archive_ids'] = [k for k in beat.get('archive_ids', [])
                                           if edit['source_fact'] in evidence.get(k, set())]
                beat.update({k: v for k, v in edit.items() if k != 'index'})
            revised = dict(old, **changes)
            if omitted:
                merged = context_beat(revised, old.get('visual_beats', []), catalogue, evidence, documents or {})
            else:
                merged = merge_beats(old, revised, beats, catalogue, evidence, complete=complete)
            changes['visual_beats'] = merged
            if omitted or any(not any(k in catalogue for k in b.get('archive_ids', [])) for b in beats):
                # A proposed drawing with no acquired plate cannot describe the
                # picture we actually retained. Rebuild only that row's shot cues.
                selected = [catalogue[k] for b in merged for k in b['archive_ids'] if k in catalogue]
                if selected and 'pictures' in old:
                    changes['pictures'] = [r['visible'] for r in selected]
                    if 'direction' in old:
                        cue = 'Show the inspected sources at their spoken anchors, in order: '
                        changes['direction'] = cue + ' / '.join(source_label(r) for r in selected) + '.'
                        if omitted:
                            changes['direction'] = 'Use the labelled archive excerpt as source context. ' + selected[0]['visible']
                        if revised.get('design'):
                            changes['direction'] += ' Present the cited design separately from the archive.'
        row['changes'] = {k: v for k, v in changes.items() if old.get(k) != v}
    return patch_script(script, edits)


def check(sc, dossier, catalogue):
    from wf4 import story
    report = story.check_script(sc, dossier)
    issues = list(report['problems'])
    for i, segment in enumerate(sc['segments']):
        beats = segment.get('visual_beats', [])
        starts = [b.get('word_start') for b in beats]
        if (not starts or starts[0] != 0 or any(type(n) is not int for n in starts)
                or starts != sorted(set(starts)) or starts[-1] >= len(segment['say'].split())):
            issues.append(f'segment {i} has invalid visual word anchors')
        for beat in beats:
            if not beat.get('archive_ids') or any(k not in catalogue for k in beat['archive_ids']):
                issues.append(f'segment {i} needs real usable catalogue IDs')
    return report, issues


def cached(root, system, payload, what, tokens):
    import cinema as C
    from wf4 import story
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha256((system + '\n' + content).encode()).hexdigest()
    path = root / 'recovery' / (key + '.json')
    if path.exists():
        return C.read(path)['result']
    text, usage = story.writer.ask(system, content, effort='high', max_tokens=tokens, what=what)
    result = story.writer.json_block(text)
    C.save(path, dict(what=what, result=result, usage=usage))
    return result


REPAIR = """Repair the outstanding documentary findings using the supplied inspected exhibits and exact verified quotes.
Return sparse JSON {segments:[{index,changes:{only changed fields},beats:[{index,label and/or archive_ids,optional source_fact}],
optional draft_index}],chapters:[{id,changes:{...}}],why}. Use the supplied global segment and local beat indices.
Keep the existing story, its chapter turns, hook, ending and length. Preserve unaffected rows,
good spoken sentences, explicit accurate IDs on their own beats, and all correctly described drawings or footage.
Resolve the findings also in chapter turns, picture directions and labels. Ground every correction in a quoted fact.
Disentangle different actors, events, dated checks and attributed outcomes. Distinguish publication/update dates from events.
For unresolved wording choose draft_index to reuse one directly supported accepted line AND its citations verbatim;
choose it by meaning and sources, never by position. Expand short disputed passages from that draft or quoted facts to
meet the target without filler, duplication, losing another chapter's payoff, or changing the segment count.
For a new fact include its verified fact ID. For new beats use changes.visual_beats with real IDs and current word_start.
For the same beats just use beats; the editor transfers their word anchors after wording changes. An empty ID list
retains the previous inspected picture and its actual source label. It cannot request a new drawing. If a prior picture
depicts another event, select a literal replacement ID or source_fact identifying an exact inspected passage. Prefer an exact catalogue
passage for the intended fact. If no such asset exists, use a correctly labelled related source as context and a cited
document or quotation design for the exact passage. Never describe an absent drawing, title, page, quote or action as
visible. Make pictures/direction match the available picture and any separately specified overlay. A source can illustrate
the context for an explanation without containing the entire spoken claim. Do not regenerate the script."""


AUDIT = """Continue the documentary's factual audit on the supplied rows and chapter text, using only their verified
fact quotations and the inspected archive. Check the actual wording, cited evidence, chronology, attribution, directions
and source-context labels, including whether a reported relationship between separate runs is supported. Distinguish a
source used as labelled context from a claimed depiction. A separately attributed design can supply an exact quote.
Verify each outstanding finding against its correction. An omitted correction remains outstanding.
Report all concrete remaining errors together, with their global indices. Do not request aesthetic changes, extra
research, footage or an unacquired illustration. Return JSON {faithful:bool,issues:[{segments:[indices],chapters:[ids],
fix:specific supported correction}],why:string}. Include every affected row or chapter in each issue."""


SOURCE_EDIT = """Resolve the outstanding documentary passage by editing FROM the evidence. The existing
wording and shot plan have not worked together. Choose a real inspected exhibit first, then tell the part
of the story that it documents. Keep each passage's job in the chapter. Do not bring forward results already
told elsewhere in the chapter or repeat the payoff to fill time. An archive picture is on screen from the FIRST word of EVERY segment.
A separately authored design appears OVER that picture from word zero. There is no design-only,
empty, or delayed opening shot. Do not promise a fade, illustration, later entrance or replacement
that this format cannot perform. Work only on affected rows and keep the chapter turns, ending,
target length and order. Read the adjacent narration so you do not repeat it.

Return {segments:[{index,passages:[{archive_id, draft_index OR say, facts:[ids], label}],design:optional,
direction:optional}],chapters:[{id,changes:{...}}],why}. Every passage starts with words ABOUT its chosen
exhibit. Prefer a faithful spoken line from the accepted draft (draft_index). Alternatively give a
complete spoken sentence (say) grounded in a quoted source. Multiple passages are joined and
each exhibit is anchored at its own first word. Preserve the important factual payoff even when
the original record is contextual: introduce precisely what is shown before speaking the attributed
conclusion. If keeping a quote design, identify what the background record shows and what the separate
foreground quotation says. Never claim the background carries a missing quote. Never return a shot-only
patch when the words require unavailable pictures. Keep the combined spoken length of the affected rows
near its current length and preserve any necessary source qualifications. Use only the supplied IDs."""


def source_edits(plan, script, draft, catalogue, evidence, facts):
    """Build words, citations and cuts from the same selected source passages."""
    edits = dict(segments=[], chapters=plan.get('chapters', []), why=plan.get('why', ''))
    for row in plan.get('segments', []):
        old = script['segments'][row['index']]
        words, beats, citations, pictures = [], [], [], []
        for passage in row.get('passages', []):
            source = draft[passage['draft_index']] if 'draft_index' in passage else passage
            spoken = source.get('say', '').split()
            key = passage.get('archive_id')
            if key not in catalogue:
                # A transposed catalogue ID need not discard a supported line.
                # Resolve the explicit fact against the inspected passage itself.
                cited = set(source.get('facts', [])) | set(passage.get('facts', []))
                key = max(catalogue, key=lambda k:len(cited & evidence[k]), default=None)
                if key is None or not cited.intersection(evidence[key]):
                    continue
                passage = dict(passage, label=source_label(catalogue[key]))
            if not spoken:
                continue
            beats.append(dict(word_start=len(words), archive_ids=[key], measured=True,
                              label=passage.get('label') or source_label(catalogue[key])))
            words.extend(spoken)
            citations.extend(source.get('facts', []))
            citations.extend(sorted(evidence[key] & facts))
            pictures.append(catalogue[key]['visible'])
        if words:
            changes = dict(say=' '.join(words), facts=list(dict.fromkeys(citations)), visual_beats=beats,
                           pictures=pictures, direction=row.get('direction') or 'Show each attributed excerpt on its spoken passage.')
            if 'emphasis' in old:
                heard = {w.casefold().strip('.,?!“”‘’:') for w in words}
                changes['emphasis'] = [w for w in old['emphasis'] if w.casefold() in heard]
            if 'design' in row:
                changes['design'] = row['design']
            design = changes.get('design', old.get('design')) or {}
            if design.get('fact') in facts and design['fact'] not in changes['facts']:
                changes['facts'].append(design['fact'])
            edits['segments'].append(dict(index=row['index'],changes=changes))
    return edits


def scope(sc, notes, before=None, *, unindexed=True):
    rows = sc['segments']
    full = before is None or unindexed and any(not isinstance(note, dict) or not (note.get('segments') or note.get('chapters')) for note in notes)
    indices = {i for note in notes if isinstance(note, dict) for i in note.get('segments', [])}
    ids = {cid for note in notes if isinstance(note, dict) for cid in note.get('chapters', [])}
    segments = [dict(index=i, **s) for i, s in enumerate(rows)
                if full or i in indices or (before and before['segments'][i] != s)]
    chapters = [c for j, c in enumerate(sc['chapters']) if full or c['id'] in ids or
                (before and before['chapters'][j] != c)]
    return dict(segments=segments, chapters=chapters)


def recover(root, base, edits, notes, dossier, catalogue, draft, *, previously_audited=True):
    from wf4 import story
    usable = {r['id']: r for r in catalogue if r['usable']}
    facts = dossier['facts']
    evidence = exhibit_facts(usable, facts)
    documents = {f['id']:urldefrag(f['url']).url for f in facts if f.get('url')}
    material = {}
    for key, row in usable.items():
        clip = row['clip']
        material[key] = dict(facts=sorted(evidence[key]), source=source_label(row), url=clip['source'].get('url'),
                             date=clip['source'].get('date'), visible=row['visible'],
                             limitations=row['limitations'], quote=[p.get('quote') for p in clip['source'].get('primary_context', [])])
    script = apply_edits(base, edits, usable, draft, evidence, documents) if edits else base
    target = max((dossier.get('runtime_words') or story.FORM['words'])[0], story.writer.words_of(base))
    audited = base if previously_audited else None
    why = (edits or {}).get('why', '')
    previous_attempts = []
    reviews = []
    while True:
        report, problems = check(script, dossier, usable)
        disputed = scope(script, notes + problems, audited)
        payload = dict(**disputed, issues=notes, structural=problems, words=report['words'], target_words=target,
                       facts=facts, archive=material, accepted_draft=draft)
        instruction = REPAIR
        if previous_attempts:
            payload['previous_repairs'] = previous_attempts
            indices = {s['index'] for s in disputed['segments']}
            neighbors = sorted({j for i in indices for j in (i-1, i+1)
                                if 0 <= j < len(script['segments']) and j not in indices})
            payload['adjacent_narration_for_context'] = [
                dict(index=j, say=script['segments'][j]['say']) for j in neighbors]
            instruction += (' For each spoken claim still disputed, use draft_index wherever the accepted draft '
                            'covers it and adds something beyond the adjacent narration. Copy that supported line and its citations.')
        if len(previous_attempts) > 1:
            active_chapters = {s['chapter'] for s in disputed['segments']}
            payload['chapter_narration_for_context'] = [
                dict(index=i, say=s['say']) for i,s in enumerate(script['segments'])
                if s['chapter'] in active_chapters and i not in indices]
            plan = cached(root, SOURCE_EDIT, payload, 'WF4 archive passage selection', 6500)
            patch = source_edits(plan, script, draft, usable, evidence, {f['id'] for f in facts})
        else:
            patch = cached(root, instruction, payload, 'WF4 evidence-backed screenplay repair', 16000)
        script = apply_edits(script, patch, usable, draft, evidence, documents)
        why = patch.get('why', why)
        report, problems = check(script, dossier, usable)
        if problems:
            previous_attempts.append(dict(edit=why, remaining=problems))
            continue
        # The saved audit already covered the rest. Legacy prose findings have
        # no row numbers; carry their actual text as open work for this audit.
        review = scope(script, notes, audited, unindexed=False)
        selected = {k for s in review['segments'] for b in s['visual_beats'] for k in b['archive_ids']}
        audit_instruction = AUDIT
        if len(previous_attempts) > 1:
            audit_instruction += (' The spoken passage itself was re-edited from inspected evidence. Test each '
                                  'original factual or labelling problem against the CURRENT wording and picture. '
                                  'Earlier proposed cuts are alternatives: a retained source that the new line '
                                  'accurately introduces is valid. Assess the separately attributed foreground '
                                  'design and background passage as two simultaneous items, each on its own evidence.')
        audit = cached(root, audit_instruction, dict(**review, outstanding=notes, facts=facts,
                       archive={k:material[k] for k in sorted(selected)}),
                       'WF4 conformed screenplay fact audit', 5500)
        reviews.append(dict(segments=[r['index'] for r in review['segments']],
                            chapters=[c['id'] for c in review['chapters']], issues=audit.get('issues', [])))
        if audit.get('faithful') is True:
            return dict(script=script, dossier=dossier, check=report, audit=dict(audit, reviews=reviews), why=why)
        audited = script
        notes = audit['issues']
        previous_attempts.append(dict(edit=why, remaining=notes))
