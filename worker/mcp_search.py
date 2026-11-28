"""Placeable search evidence. Descriptions come from pixels, never query words."""
import json
import os
import tempfile


UNDESCRIBED_BASIS = ('UNDESCRIBED and UNVERIFIED: no model could look at its picture; only the '
                     "library's own metadata is known")
LIBRARY_TITLE_BASIS = ("the library's own title for it (its creator's words); no model looked at "
                       'its picture')


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
            rubric = ('Describe only the visible content of each search thumbnail: subject, action, '
                      'framing and visible text. Do not infer identity or relevance from a query. '
                      'A thumbnail proves one frame, not the whole clip. Return a JSON array of '
                      '{"id": exact id, "description": concise factual description}; use null '
                      'if unreadable.')
            prompt = 'Images in order: ' + json.dumps(ids)
            try:
                reply = llm.ask_vision(prompt, paths, max_tokens=2000, system=rubric,
                                       purpose='search_descriptions',
                                       image_names=ids, reasoning_effort='low')
                rows = llm.extract_json_array(reply or '') or []
            except Exception as exc:  # A provider outage leaves evidence unknown, not fabricated.
                print(f'[search] descriptions failed: {type(exc).__name__}: {str(exc)[:200]}',
                      flush=True)
                rows = []
            if not rows:
                # F25 (harness v2): the cause is logged, never swallowed, and the turn's own
                # editing lane (a second, multimodal provider) looks once before anything is
                # left undescribed.
                print(f'[search] descriptions: no answer from the vision provider '
                      f'({str(llm.last_error() or "empty reply")[:200]}); asking the turn lane',
                      flush=True)
                rows = _second_look(rubric + " " + prompt, paths, ids)
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
        title = str(h.get('title') or '').strip()
        if title:
            # A Pexels video has no alt text, but its page carries the creator's own title
            # (stock._page_title). That is the same evidence a photo's alt text is: real words
            # about this exact clip, never the query's, and still marked unverified.
            h['description'] = title
            h['unverified'] = True
            h['description_basis'] = LIBRARY_TITLE_BASIS
            continue
        undescribed += 1
        h['description'] = None          # never invented
        h['undescribed'] = True
        h['unverified'] = True
        h['description_basis'] = UNDESCRIBED_BASIS
    return hits, undescribed


def _second_look(prompt, paths, ids):
    """The same question on the turn's editing lane (a second provider), or []. Recorded like
    every model call; a failure is logged with its cause."""
    import llm
    lane = llm.turn_lane()
    if not lane or not lane.get('client') or not llm.agent_sees(lane.get('model')):
        print('[search] descriptions: no second multimodal lane on this turn', flush=True)
        return []
    content = [{'type': 'text', 'text': prompt}]
    try:
        for name, path in zip(ids, paths):
            content.append({'type': 'text', 'text': f'[{name}]'})
            content.append(llm.image_part(path))
        resp = llm.create_with_dialect(lane['client'], lane['model'],
                                       [{'role': 'user', 'content': content}],
                                       max_tokens=2000)
        answer = (resp.choices[0].message.content or '').strip()
        llm.record('search_descriptions', {'model': lane['model'], 'images': ids,
                                           'second_provider': True},
                   {'answer': answer}, getattr(resp, 'usage', None))
        return llm.extract_json_array(answer) or []
    except Exception as exc:  # noqa: BLE001 - logged; the library text or the board stands
        print(f'[search] descriptions: second lane failed: {type(exc).__name__}: '
              f'{str(exc)[:200]}', flush=True)
        return []


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


PLACE_SCENES_BUDGET_S = float(os.getenv('MCP_PLACE_SCENES_BUDGET_S', '600'))
PLACE_SCENES_MAX = 400


def _scene_rows(scenes):
    """(rows in time order, notes for entries that could not be read)."""
    import math
    rows, notes = [], []
    for n, raw in enumerate(scenes if isinstance(scenes, (list, tuple)) else [], 1):
        if not isinstance(raw, dict):
            notes.append(f'scene {n}: not an object')
            continue
        ref = str(raw.get('id') or raw.get('asset_key') or '').strip()
        try:
            start, end = float(raw.get('start_s')), float(raw.get('end_s'))
            offset = float(raw.get('source_start_s') or 0)
            if not all(math.isfinite(v) for v in (start, end, offset)) or start < 0 \
                    or offset < 0 or end - start < .2:
                raise ValueError()
        except (TypeError, ValueError):
            notes.append(f'scene {n}: needs numeric start_s < end_s (at least 0.2 s apart)')
            continue
        if not ref:
            notes.append(f'scene {n}: needs id (a search result) or asset_key (an upload)')
            continue
        rows.append({'n': n, 'id': raw.get('id'), 'asset_key': raw.get('asset_key'),
                     'start': round(start, 2), 'end': round(end, 2),
                     'offset': round(offset, 2), 'motion': raw.get('motion')})
    rows.sort(key=lambda r: (r['start'], r['n']))
    return rows, notes


def _acquire(ctx, row):
    """(storage_key, note): a saved search result is downloaded, an upload is used as it is."""
    import agent_tools as at
    if row.get('asset_key') and not row.get('id'):
        return str(row['asset_key']).strip(), ''
    count = len(ctx.stock_added)
    acquired = at.add_stock_media(ctx, str(row['id']).strip())
    if len(ctx.stock_added) == count:
        return None, acquired
    return ctx.stock_added[-1]['storage_key'], ''


def _first_line(text, n=180):
    return ' '.join(str(text or '').split())[:n]


def place_scenes(ctx, scenes, narration_asset_key=None):
    """Lay a narration's whole picture in one call, through the ordinary EDL writers.

    The caller (an outside AI that read the transcript and searched) decides every picture
    and its narration time; this tool does the mechanical part: download each chosen result,
    build the picture sequence along the narration on an empty canvas (or cut away over an
    existing programme), and lay the narration under it. Nothing is chosen here, and a scene
    the writers do not take is reported while every other scene still lands: the next scene
    stretches back to cover its time, so the picture stays in step with the words.
    """
    import time
    import agent_tools as at
    from schemas import program_duration
    rows, notes = _scene_rows(scenes)
    if not rows:
        return ('Nothing placed: pass scenes=[{id or asset_key, start_s, end_s}] in narration '
                'seconds, e.g. one per transcript sentence or beat from get_transcript. '
                + ('; '.join(notes[:6]) if notes else ''))
    if len(rows) > PLACE_SCENES_MAX:
        notes.append(f'scenes after the first {PLACE_SCENES_MAX} wait for the next call')
    started = time.monotonic()
    version0 = ctx.latest_edl()['version']
    placed, failed, left = [], [], []
    for row in rows[:PLACE_SCENES_MAX]:
        if time.monotonic() - started > PLACE_SCENES_BUDGET_S:
            left.append(row)
            continue
        edl = ctx.latest_edl()['json']
        program_end = program_duration(edl)
        canvas = not getattr(ctx, 'has_main_video', False)
        key, why = _acquire(ctx, row)
        if not key:
            failed.append((row, why))
            continue
        before = ctx.latest_edl()['version']
        if canvas and row['end'] > program_end + .2:
            # Grow the sequence to this scene's end; a gap left by an earlier miss is covered.
            duration = round(row['end'] - program_end, 2)
            kwargs = {'duration_s': duration, 'clip_start_s': row['offset']}
            if row.get('motion') in at.INSERT_MOTIONS:
                kwargs['motion'] = row['motion']
            if program_end <= .05 or not (edl.get('inserts') or []):
                result = at.start_media_sequence(ctx, key, **kwargs)
            else:
                result = at.insert_media(ctx, key, program_end, **kwargs)
        else:
            result = at.add_overlay(ctx, key, row['start'],
                                    duration_s=round(row['end'] - row['start'], 2),
                                    source_start_s=row['offset'], fit='cover')
        if ctx.latest_edl()['version'] != before:
            placed.append(row)
        else:
            failed.append((row, result))
    lines = []
    narration_note = ''
    if narration_asset_key:
        key = str(narration_asset_key).strip()
        edl = ctx.latest_edl()['json']
        laid = any(v.get('asset_key') == key for v in edl.get('voiceover') or [])
        if laid:
            narration_note = f'Narration {key} is already on the timeline.'
        elif program_duration(edl) <= 0:
            narration_note = (f'Narration {key} is not laid yet: the picture is empty, and the '
                              'narration plays over the picture.')
        else:
            before = ctx.latest_edl()['version']
            result = at.add_voiceover(ctx, key, start_output_s=0)
            narration_note = (f'Narration {key} laid under the picture from 0 s.'
                              if ctx.latest_edl()['version'] != before else
                              f'Narration {key} was not laid: {_first_line(result)}')
    edl_row = ctx.latest_edl()
    total = program_duration(edl_row['json'])
    lines.append(f'PLACED {len(placed)} of {len(rows)} scene(s); EDL v{version0} -> '
                 f'v{edl_row["version"]}; the programme is now {total:.1f}s.')
    if narration_note:
        lines.append(narration_note)
    if narration_asset_key:
        try:
            asset, err = at._resolve_media_asset(ctx, str(narration_asset_key).strip(), ('music',))
            spoken = at._asset_media_duration(ctx, asset) if asset else None
        except Exception:  # noqa: BLE001 - a report line, never a reason to fail the call
            spoken = None
        if spoken and total + .5 < spoken:
            lines.append(f'The narration runs {spoken:.1f}s, so {spoken - total:.1f}s from '
                         f'{total:.1f}s still has no picture: place scenes for it.')
    for row, why in failed[:24]:
        lines.append(f"  not placed: scene {row['n']} ({row['start']:g}-{row['end']:g}s, "
                     f"{row.get('id') or row.get('asset_key')}): {_first_line(why)}")
    if len(failed) > 24:
        lines.append(f'  ... and {len(failed) - 24} more not placed')
    for note in notes[:12]:
        lines.append(f'  skipped: {note}')
    if left or len(rows) > PLACE_SCENES_MAX:
        rest = left + rows[PLACE_SCENES_MAX:]
        lines.append(f'{len(rest)} scene(s) from {rest[0]["start"]:g}s were not reached in this '
                     'call: call place_scenes again with exactly those scenes (the same times); '
                     'the sequence continues from where it ends.')
    elif failed:
        lines.append('Each miss above is covered by the scene after it. To give a missed moment '
                     'its own picture, search again and call place_scenes with that scene.')
    lines.append('NEXT: render_preview(complete=true) to render the cut, look_at or watch_video to '
                 'check it, then export_final(project_id, edl_version='
                 f'{edl_row["version"]}) and download_url(kind=\'final\') for the finished MP4.')
    return '\n'.join(lines)
