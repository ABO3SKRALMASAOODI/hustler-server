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
