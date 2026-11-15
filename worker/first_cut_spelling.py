"""Re-use frame-read title evidence in the legacy first-cut scout call."""
import json


def inscriptions(index):
    rows = ((index or {}).get('visual_storyboard') or {}).get('evidence') or []
    out = []
    for row in rows:
        text = row.get('visible_text') if isinstance(row, dict) else None
        if isinstance(text, str) and text.strip():
            out.append(text.strip())
        elif isinstance(text, list):
            out.extend(t.strip() for t in text if isinstance(t, str) and t.strip())
    return out


def context(index):
    printed = inscriptions(index)
    if not printed:
        return ''
    words = [{'w': i, 'text': w.get('w'), 'at': w.get('t0')}
             for i, w in enumerate(index.get('words') or [])][:6000]
    return ('\nThe upload has these frame-read inscriptions and transcript word IDs:\n'
            + json.dumps({'inscriptions': printed[:60], 'word_ids': words}, ensure_ascii=False)
            + '\nIf an inscription establishes the spelling of the SAME person or thing being spoken about, '
            'use it in titles and return fixes:[{w:word_id,t:exact_corrected_word,evidence:"screen",'
            'read:exact_inscription}]. Match the speech context, never faces. Do not replace one person '
            'with another whose name merely appears elsewhere. Omit uncertain fixes. This optional fixes '
            'array belongs beside start_id, end_id, hook and beats in the JSON answer.\n')


def apply(ctx, answer):
    from score.scout import printed_spellings
    printed = inscriptions(ctx.index)
    backed = []
    for fix in (answer or {}).get('fixes') or []:
        if not isinstance(fix, dict):
            continue
        read = str(fix.get('read') or '').strip()
        if read and any(read.casefold() in text.casefold() for text in printed):
            backed.append(fix)
    if backed:
        ctx.index = printed_spellings(ctx.index, {'fixes': backed})


def persist(ctx):
    """Carry proved spellings into the EDL read by the separate renderer.

    The renderer loads the immutable source index, not this scout's working
    copy. Scope each correction to the same word's assembled program time.
    """
    import copy
    from timeline import Timeline, Voice
    index = getattr(ctx, 'index', None) or {}
    proof = index.get('caption_evidence') or {}
    edl = copy.deepcopy((ctx.latest_edl() or {}).get('json') or {})
    caps = edl.get('captions') or {}
    if not proof or not isinstance(caps, dict) or caps.get('mode') != 'from_transcript':
        return None
    tl = Timeline(edl.get('keep') or [], edl.get('inserts'), edl.get('speed'))
    voice = Voice.from_index(index)
    words = index.get('words') or []
    rules = list(caps.get('corrections') or [])
    for key, claim in proof.items():
        wid = int(key)
        if not 0 <= wid < len(words) or claim.get('evidence') != 'screen':
            continue
        before, after = claim.get('before'), claim.get('text')
        if not before or not after or before == after:
            continue
        word = dict(words[wid], w=before)
        for kept in tl.kept_words([word], rescue=True, voice=voice):
            rule = {'from': before, 'to': after,
                    'start': kept['t0'], 'end': kept['t1']}
            if rule not in rules:
                rules.append(rule)
    if rules == list(caps.get('corrections') or []):
        return None
    caps['corrections'] = rules
    return ctx.apply_score(edl, 'preserve upload-backed caption spelling in the rendered film')
