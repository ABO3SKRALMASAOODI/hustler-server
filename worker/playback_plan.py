"""Media-independent edit plan. Used by API, evidence and incremental export.

All intervals are half open, in seconds. Source and programme clocks are
explicit; an organizational split must never alter effect phase or audio.
The browser compiler is checked against the same serialized fixtures.
"""
import copy
import hashlib
import json

try:
    from schemas import master_loudness, speed_pieces
    from render_plan import canonical_program
except ImportError:
    from worker_schemas import master_loudness, speed_pieces
    from worker_render_plan import canonical_program

PLAN_VERSION = 1
AUDIO_LAYERS = ("music", "sfx", "voiceover", "volume", "master", "stem_mix")

# The layers the Studio's browser player draws or names in playback_limitations.
# Any other layer has no drawing code there, so playing without it would delete
# part of the edit with no message: it asks for a render by default, and a layer
# added later is safe before anyone remembers this file. The same contract is in
# the frontend's src/lib/playbackPlan.js, proved by the shared fixture.
PLAYER_LAYERS = frozenset(("keep", "split_keep_boundaries", "canvas", "frame",
    "captions", "caption_mutes", "music", "sfx", "voiceover", "volume", "master",
    "stem_mix", "inserts", "speed", "effects", "texts", "vectors", "overlays",
    "source_clean", "patches"))
PLAYER_EFFECTS = frozenset(("grade", "grade_custom", "zooms", "fade_in_s", "fade_out_s",
    "transition", "regions", "stylize", "screen_frame", "frame_shifts", "picture_cards",
    "custom", "freeze_frames", "cursor"))
RENDER_ONLY = {"motion": "motion graphics", "fx": "layered effects",
               "scene": "the designed scene"}
EDITORIAL_TYPE = ("font_size", "max_width", "text_align", "italic", "tracking")


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


def _holds_unread(layers, read):
    return any(k not in read and v not in (None, [], {}) for k, v in (layers or {}).items())


def render_only_layers(edl):
    found = [name for key, name in RENDER_ONLY.items()
             if (edl or {}).get(key) not in (None, [], {})]
    if (_holds_unread(edl, PLAYER_LAYERS | set(RENDER_ONLY))
            or _holds_unread((edl or {}).get("effects"), PLAYER_EFFECTS)):
        found.append("effects that need a full render")
    return found


def playback_limitations(edl):
    """What the browser player cannot show of this EDL, in the same words and
    order as playbackLimitations. Non-empty means a render, never a player
    that quietly leaves part of the edit out."""
    if not edl:
        return ["no edit"]
    missing, fx = render_only_layers(edl), edl.get("effects") or {}
    frame, texts = edl.get("frame") or {}, edl.get("texts") or []
    inserts, music = edl.get("inserts") or [], edl.get("music") or []

    def note(present, phrase):
        if present:
            missing.append(phrase)

    note(frame.get("picture"), "the composed picture layout")
    note(frame.get("follow"), "the moving crop")
    note(edl.get("source_clean") or edl.get("patches"), "erased areas")
    note(edl.get("overlays"), "overlays")
    note(fx.get("transition"), "transitions")
    note(fx.get("regions"), "blurred regions")
    note(fx.get("screen_frame") or fx.get("frame_shifts") or edl.get("screen_takeovers"),
         "screen effects")
    note(fx.get("picture_cards"), "animated picture cards")
    note(any(t.get(k) is not None for t in texts for k in EDITORIAL_TYPE), "editorial typography")
    note(fx.get("custom"), "custom effects")
    note(fx.get("stylize") or fx.get("freeze_frames") or fx.get("cursor"), "special effects")
    note(any(t.get("behind") for t in texts), "text behind the subject")
    note(any(i.get("motion") or i.get("motion_motif") or i.get("rotation") for i in inserts),
         "clip motion")
    note(edl.get("stem_mix"), "separated audio")
    note((edl.get("master") or {}).get("loudness"), "loudness mastering")
    note(any(not m.get("mute") and m.get("duck") is not False
             and m.get("duck_mode") == "smooth" for m in music), "automatic audio ducking")
    note(any(not m.get("mute") and m.get("loop") for m in music), "looped music")
    note(len(music) + len(edl.get("voiceover") or []) + len(edl.get("sfx") or []) > 8,
         "a complex audio mix")
    note(any(s.get("factor") != 1 for s in edl.get("speed") or [])
         or any(not i.get("mute") and i.get("rate") and i.get("rate") != 1 for i in inserts),
         "pitch-preserving speed")
    return missing


def _canon(value, top=False):
    """edl_signature's rule: nested None-valued keys dropped; at the top
    level an absent layer and its empty default are the same."""
    if top and value in (None, [], {}):
        return None
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def changed_work(before, after):
    """Conservative dependency scopes; never treats unrecognized fields as free.

    Shifted timeline content changes the corresponding programme intervals.
    Global filters and temporal effects widen the scope instead of reusing
    pixels that depend on a different neighborhood.
    """
    a, b = canonical_program(before), canonical_program(after)
    old, new = compile_plan(a), compile_plan(b)
    duration = max(old["duration"], new["duration"])
    # An absent field and its empty default are the same programme (the
    # rule edl_signature uses): an EDL stored before a field existed (fx,
    # patches, a motion item's composite, ...) must not report that layer as
    # changed against its own re-validated dump.
    changed = sorted(k for k in set(a) | set(b)
                     if _canon(a.get(k), True) != _canon(b.get(k), True))
    visual = [k for k in changed if k not in AUDIO_LAYERS]
    # A new frame can switch the format-default mastering on or off.
    audio_changed = any(k in changed for k in (*AUDIO_LAYERS, "keep", "inserts", "speed")) \
        or master_loudness(a) != master_loudness(b)
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
