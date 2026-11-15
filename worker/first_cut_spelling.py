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


def context(index, frames=False):
    printed = inscriptions(index)
    if not printed and not frames:
        return ''
    words = [{'w': i, 'text': w.get('w'), 'at': w.get('t0')}
             for i, w in enumerate(index.get('words') or [])][:6000]
    return ('\nThe upload has these frame-read inscriptions and transcript word IDs:\n'
            + json.dumps({'inscriptions': printed[:60], 'word_ids': words}, ensure_ascii=False)
            + '\nIf an inscription establishes the spelling of the SAME person or thing being spoken about, '
            'audit every mentioned name, including confident transcript words, and correct every spelling mismatch. '
            'Use the established spelling in titles and return fixes:[{w:word_id,t:exact_corrected_word,evidence:"screen",'
            'read:exact_inscription}]. Read the attached source frames as well, including small name cards and labels; '
            'the earlier frame summary may omit them. Match the speech context, never faces. Do not replace one person '
            'with another whose name merely appears elsewhere. Omit uncertain fixes. This fixes '
            'array belongs beside start_id, end_id, hook and beats in the JSON answer.\n')


def content(ctx, transcript):
    """Let the existing scout read source text that the index summary omitted.

    At most three original frames, with no extra model call or persistent cache.
    Claims from this call may cite the attached pixels as well as index text.
    """
    import base64
    import io
    from score import boards
    images = []
    ctx.first_cut_spelling_frames = False
    try:
        shots = ctx.index.get('shots') or [{'start': 0, 'end': ctx.duration}]
        selected = shots if len(shots) <= 3 else [shots[0], shots[len(shots)//2], shots[-1]]
        source = ctx.proxy_path()
        for shot in selected:
            at = (float(shot.get('start', 0)) + float(shot.get('end', ctx.duration))) / 2
            frame = boards.grab(source, at, width=1280)
            if frame is None:
                continue
            buf = io.BytesIO()
            frame.save(buf, format='JPEG', quality=90)
            images.extend([{'type': 'text', 'text': f'Original upload frame at {at:.2f}s. Read its small printed names.'},
                           {'type': 'image_url', 'image_url': {
                               'url': 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode(),
                               'detail': 'high'}}])
    except (AttributeError, OSError, ValueError, TimeoutError):
        # Text evidence remains available when the source cannot be decoded.
        pass
    ctx.first_cut_spelling_frames = bool(images)
    text = transcript + context(ctx.index, frames=bool(images))
    return [{'type': 'text', 'text': text}, *images] if images else text


def apply(ctx, answer):
    from score.scout import printed_spellings
    printed = inscriptions(ctx.index)
    backed = []
    for fix in (answer or {}).get('fixes') or []:
        if not isinstance(fix, dict):
            continue
        read = str(fix.get('read') or '').strip()
        if read and (getattr(ctx, 'first_cut_spelling_frames', False)
                     or any(read.casefold() in text.casefold() for text in printed)):
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
