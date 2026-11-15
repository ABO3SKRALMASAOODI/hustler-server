"""Placeable search evidence. Descriptions come from pixels, never query words."""
import json
import os
import tempfile


UNDESCRIBED_BASIS = ('UNDESCRIBED and UNVERIFIED: no model could look at its picture; only the '
                     "library's own metadata is known")


def describe_missing(ctx, hits):
    """(hits, how many stay undescribed). Every real candidate is returned (#1278): one whose
    picture no model could describe keeps the library's own metadata, is marked
    ``undescribed`` and ``unverified``, and never gets an invented description."""
    import llm
    import net_fetch
    missing = [h for h in hits if not str(h.get('description') or '').strip()]
    if not missing:
        return hits, 0
    with tempfile.TemporaryDirectory(prefix='search_evidence_', dir=ctx.workdir) as work:
        paths, ids = [], []
        for h in missing:
            if not h.get('_thumb'):
                continue
            path = os.path.join(work, f'{len(paths)}.jpg')
            try:
                net_fetch.download(h['_thumb'], path, max_bytes=3 * 1024 * 1024, timeout_s=15)
            except Exception:
                continue
            paths.append(path)
            ids.append(h['id'])
        if paths:
            try:
                reply = llm.ask_vision(
                    'Describe only the visible content of each search thumbnail: subject, action, '
                    'framing and visible text. Do not infer identity or relevance from a query. '
                    'A thumbnail proves one frame, not the whole clip. Return a JSON array of '
                    '{"id": exact id, "description": concise factual description}; use null '
                    'if unreadable. Images in order: ' + json.dumps(ids),
                    paths, max_tokens=2000, purpose='search_descriptions',
                    image_names=ids, reasoning_effort='low')
                rows = llm.extract_json_array(reply or '') or []
            except Exception:  # A provider outage leaves evidence unknown, not fabricated.
                rows = []
            descriptions = {r.get('id'): r.get('description') for r in rows
                            if isinstance(r, dict) and isinstance(r.get('description'), str)}
            for h in missing:
                if (descriptions.get(h['id']) or '').strip():
                    h['description'] = descriptions[h['id']].strip()
                    h['description_basis'] = 'thumbnail pixels; inspect downloaded clip before delivery'
    undescribed = 0
    for h in hits:
        if str(h.get('description') or '').strip():
            continue
        undescribed += 1
        h['description'] = None          # never invented
        h['undescribed'] = True
        h['unverified'] = True
        h['description_basis'] = UNDESCRIBED_BASIS
    return hits, undescribed


def place(ctx, id, at_output_s, duration_s, mode='overlay', source_start_s=0):
    """Acquire the exact persisted result, then use the ordinary EDL writer."""
    import math
    import agent_tools as at
    if mode not in ('overlay', 'insert'):
        return 'REJECTED: mode must be overlay or insert.'
    try:
        start, duration, offset = float(at_output_s), float(duration_s), float(source_start_s)
        if not all(math.isfinite(v) for v in (start, duration, offset)) or min(start, offset) < 0 or duration < .2:
            raise ValueError()
    except (ValueError, TypeError):
        return 'REJECTED: finite nonnegative times and duration_s >= 0.2 are required.'
    count = len(ctx.stock_added)
    acquired = at.add_stock_media(ctx, id)
    if len(ctx.stock_added) == count:
        return acquired
    key = ctx.stock_added[-1]['storage_key']
    if mode == 'overlay':
        result = at.add_overlay(ctx, key, start, duration_s=duration,
                                source_start_s=offset, fit='cover')
    else:
        result = at.insert_media(ctx, key, start, duration_s=duration,
                                 clip_start_s=offset)
    return result + '\nAcquisition evidence: ' + acquired
