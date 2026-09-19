"""Media-independent edit plan. Used by API, evidence and incremental export.

All intervals are half open, in seconds. Source and programme clocks are
explicit; an organizational split must never alter effect phase or audio.
The browser compiler is checked against the same serialized fixtures.
"""
import copy
import hashlib
import json

try:
    from schemas import speed_pieces
    from render_plan import canonical_program
except ImportError:
    from worker_schemas import speed_pieces
    from worker_render_plan import canonical_program

PLAN_VERSION = 1
AUDIO_LAYERS = ("music", "sfx", "voiceover", "volume", "master", "stem_mix")


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def compile_plan(edl, source_key="source"):
    edl = canonical_program(edl)
    inserts = sorted(edl.get("inserts") or [], key=lambda i: i["at_output_s"])
    clips, cursor, pre, pending = [], 0., 0., 0

    def insert(item):
        nonlocal cursor
        start = float(item.get("source_start_s") or 0)
        rate = float(item.get("rate") or 1)
        length = float(item["duration_s"])
        clips.append(dict(id=item["id"], kind=item["kind"], source=item["asset_key"],
            start=round(cursor, 6), end=round(cursor + length, 6),
            source_start=start, source_end=round(start + length * rate, 6),
            rate=rate, mute=bool(item.get("mute")),
            frame={"mode": item.get("fit") or (edl.get("frame") or {}).get("mode") or "crop",
                   "crop": item.get("crop"), "rotation": item.get("rotation") or 0,
                   "motion": item.get("motion"), "motion_motif": item.get("motion_motif")}))
        cursor += length

    for ordinal, (start, end) in enumerate(edl.get("keep") or []):
        while pending < len(inserts) and inserts[pending]["at_output_s"] <= pre + 1e-6:
            insert(inserts[pending])
            pending += 1
        frame = copy.deepcopy(edl.get("frame") or {})
        for span in frame.pop("focus_track", None) or []:
            if span["t0"] <= (start + end) / 2 < span["t1"]:
                frame.update({k: span[v] for k, v in
                    (("focus_x", "x"), ("focus_y", "y"), ("mode", "mode"))
                    if span.get(v) is not None})
                break
        for s, e, rate in speed_pieces(start, end, edl.get("speed") or []):
            length = (e - s) / rate
            clips.append(dict(id=f"source:{ordinal}:{s:g}", kind="video", source=source_key,
                start=round(cursor, 6), end=round(cursor + length, 6),
                source_start=s, source_end=e, rate=rate, mute=False,
                frame=frame, source_footage=True))
            cursor += length
            pre += length
    for item in inserts[pending:]:
        insert(item)
    duration = cursor  # Music overhang never extends the exported picture clock.
    return dict(schema=PLAN_VERSION, clips=clips, duration=round(duration, 6),
                picture_duration=round(cursor, 6), canvas=edl.get("canvas"),
                frame=edl.get("frame"), audio={k: edl.get(k) for k in AUDIO_LAYERS},
                captions=edl.get("captions"), caption_mutes=edl.get("caption_mutes") or [],
                texts=edl.get("texts") or [], vectors=edl.get("vectors") or [],
                effects=edl.get("effects") or {}, overlays=edl.get("overlays") or [])


def changed_work(before, after):
    """Conservative dependency scopes; never treats unrecognized fields as free.

    Shifted timeline content changes the corresponding programme intervals.
    Global filters and temporal effects widen the scope instead of reusing
    pixels that depend on a different neighborhood.
    """
    a, b = canonical_program(before), canonical_program(after)
    old, new = compile_plan(a), compile_plan(b)
    duration = max(old["duration"], new["duration"])
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    visual = [k for k in changed if k not in AUDIO_LAYERS]
    audio_changed = any(k in changed for k in (*AUDIO_LAYERS, "keep", "inserts", "speed"))
    ranges = []
    local = {"texts", "vectors", "caption_mutes", "volume"}
    if visual and all(k in local for k in visual):
        for layer in visual:
            for item in (a.get(layer) or []) + (b.get(layer) or []):
                if item not in (b.get(layer) or []) or item not in (a.get(layer) or []):
                    if layer == "caption_mutes" and isinstance(item, (list, tuple)) and len(item) == 2:
                        ranges.append([float(item[0]), float(item[1])])
                        continue
                    if not isinstance(item, dict) or "start" not in item or "end" not in item:
                        ranges = [[0., duration]]
                        break
                    ranges.append([float(item["start"]), float(item["end"])])
    elif visual:
        ranges = [[0., duration]]
    merged = []
    for s, e in sorted(ranges):
        s, e = max(0., s), min(duration, e)
        if e <= s:
            continue
        if merged and s <= merged[-1][1] + .001:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return dict(changed_layers=changed, picture_ranges=merged,
                picture_unchanged=not visual, audio_changed=audio_changed,
                audio_ranges=[[0., duration]] if audio_changed else [],
                full_review_required=bool(changed), duration=new["duration"])


def chunk_key(edl, source_identity, quality, renderer_version, start, end):
    """A final-quality chunk is reusable only under exact media dependencies."""
    return identity(dict(edl=canonical_program(edl), source=source_identity,
                         quality=quality, renderer=renderer_version, start=start, end=end))
