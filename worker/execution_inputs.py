"""Capacity accounting for the inputs a job will actually stage on disk."""
import os

import config


STREAM_ASSET_BYTES = 32 * 1024 * 1024
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.avif'}
FILMSTRIP_ASSETS = 14
FILMSTRIP_WORKERS = max(1, min(3, int(os.getenv('FILMSTRIP_ASSET_WORKERS', '2'))))
FILMSTRIP_KINDS = {'video_clip': 'video', 'image_ref': 'image',
                   'music': 'audio', 'audio': 'audio'}


def streams_asset(key, size):
    return (os.path.splitext(key)[1].lower() not in IMAGE_EXTENSIONS
            and int(size or 0) >= STREAM_ASSET_BYTES)


def streams_source(asset):
    try:
        duration = float((asset or {}).get('duration_s') or 0)
        size = int((asset or {}).get('bytes') or 0)
    except (TypeError, ValueError):
        return False
    return ((config.CLOUDFLARE_STREAM_SOURCE_MIN_DURATION_S > 0
             and duration >= config.CLOUDFLARE_STREAM_SOURCE_MIN_DURATION_S)
            or (config.CLOUDFLARE_STREAM_SOURCE_MIN_BYTES > 0
                and size >= config.CLOUDFLARE_STREAM_SOURCE_MIN_BYTES))


def filmstrip_inputs(rows, edl, limit=FILMSTRIP_ASSETS):
    """Same bounded, newest-first unique selection for admission and execution."""
    selected, seen = [], set()
    by_key = {row['storage_key']: row for row in reversed(rows)}
    for row in rows:
        key = row.get('storage_key')
        if row.get('kind') in FILMSTRIP_KINDS and key and key not in seen:
            selected.append(row)
            seen.add(key)
    for music in edl.get('music') or []:
        key = music.get('storage_key')
        if key and key not in seen:
            selected.append({**by_key.get(key, {}), 'storage_key': key, 'kind': 'music'})
            seen.add(key)
    return selected[:limit] if limit is not None else selected


def render_references(value, path=()):
    """Include nested masks, patches and clean sources as well as visible clips."""
    refs = {}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {'asset_key', 'storage_key', 'proxy_key'} and child:
                refs.setdefault(str(child), set()).add(path)
            else:
                for ref, paths in render_references(child, path + (key,)).items():
                    refs.setdefault(ref, set()).update(paths)
    elif isinstance(value, list):
        for child in value:
            for ref, paths in render_references(child, path).items():
                refs.setdefault(ref, set()).update(paths)
    return refs


def job_shape(rows, edl, job_type, payload):
    """Keep limits intact; unknown sizes remain inadmissible, never zero bytes."""
    by_key = {row['storage_key']: row for row in reversed(rows)}
    latest = lambda kind: next((r for r in rows if r.get('kind') == kind), None)
    original, proxy = latest('original'), latest('proxy')
    unknown = config.CLOUDFLARE_MAX_INPUT_BYTES + 1

    def size(row):
        return int(row['bytes']) if row and row.get('bytes') is not None else unknown

    if job_type == 'filmstrip':
        source = proxy or original
        selected = filmstrip_inputs(rows, edl)
        staged = [0 if streams_asset(r['storage_key'], size(r)) and r.get('bytes') is not None
                  else size(r) for r in selected]
        source_bytes = size(source) if source else 0
        if source and streams_source(source):
            source_bytes = 0
        # Main input is deleted before the bounded parallel secondary batch.
        total = max(source_bytes, sum(sorted(staged, reverse=True)[:FILMSTRIP_WORKERS]))
        used = ([source] if source else []) + selected
    else:
        source = None if edl.get('canvas') else original
        if job_type in {'preview', 'preview_check'} and payload.get('quality', 'draft') != 'approval':
            source = None if edl.get('canvas') else (proxy or original)
        refs = render_references(edl)
        staged = {}
        for key, paths in refs.items():
            row = by_key.get(key)
            count = size(row)
            # Only these two renderer inputs use the inserted-video stream path.
            if (row and row.get('bytes') is not None
                    and all(p in {('inserts',), ('overlays',)} for p in paths)
                    and streams_asset(key, count)):
                count = 0
            staged[key] = count
        if source:
            staged[source['storage_key']] = 0 if streams_source(source) else size(source)
        total = sum(staged.values())
        used = [by_key[k] for k in staged if k in by_key]
    return {'shape_version': 2, 'total_bytes': total, 'staged_bytes': total,
            'assets': len(used), 'max_bytes': max([size(r) for r in used] or [0]),
            'max_duration_s': max([float(r.get('duration_s') or 0) for r in used] or [0])}
