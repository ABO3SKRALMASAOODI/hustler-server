"""timeline(schema=...): every op's shape, and the long tail on demand.

``schema='ops'`` lists every edit op with an example and one line per write
verb reachable through ``{"tool": name, "args": {...}}`` (built from today's
own descriptions, so it never drifts from the code). ``schema=<layer>``
gives one EDL layer's field names, from the schema itself.
"""
import json

OPS = [
    ("keep", '{"keep": [[0.75, 36.0], [43.25, 58.25]]}',
     "source seconds kept, in order; edges move out of words onto quiet "
     "points; add \"exact\": true inside {\"spans\": [...]} to keep times"),
    ("cut", '{"cut": [[193, 294]]}', "source seconds removed"),
    ("restore", '{"restore": [[2384.5, 2447.75]]}',
     "source seconds put back"),
    ("speed", '{"speed": [{"start": 6, "end": 7.3, "factor": 0.6}]}',
     "a constant speed over FILM seconds (add \"at_source\": true to give "
     "source seconds); the result measures where it plays"),
    ("fit_length", '{"fit_length": {"max_s": 3590, "min_s": 3480}}',
     "trims the least active seconds at piece edges (or restores the most "
     "active), never inside a sentence; pieces under 2 s are dropped"),
    ("card", '{"card": {"text": "Set 2", "at_source": 912.0, "dur": 2.5, '
     '"bg": "#000000"}}', "a title card at a cut; 'at' is film seconds, "
     "'at_source' anchors it to footage"),
    ("text", '{"text": {"text": "Hello", "start": 1.0, "end": 3.0, '
     '"template": "title"}}', "on-screen text, film seconds"),
    ("captions", '{"captions": {"style": {...}}}',
     "captions from the transcript (args of add_captions)"),
    ("place", '{"place": {"id": "pexels:video:123", "at": 12.0, "dur": 4.0, '
     '"mode": "overlay"}}', "a find() result (or 'key' of an upload) on the "
     "film; mode overlay covers the cut, insert splices at a cut; a failed "
     "download takes the next result of the same search"),
    ("scenes", '{"scenes": {"narration": "<asset key>", "items": [{"id": '
     '"...", "from": 0, "to": 6.2}]}}', "pictures laid along a narration in "
     "one call; a miss is covered by the next scene"),
    ("voiceover", '{"voiceover": {"key": "<asset key>", "at": 0}}',
     "an uploaded voice under the film"),
    ("music", '{"music": {"id": "<find(kind=music) id>", "gain_db": -18, '
     '"duck": true}}', "a track from find(kind='music') or the user's own "
     "'key'; provenance is stamped"),
    ("sfx", '{"sfx": [{"id": "<find(kind=sfx) id>", "at": 3.2}]}',
     "one-shot sounds at film seconds"),
    ("audio", '{"audio": {"range": [0, 11.3], "gain_db": -60}}',
     "gain over source seconds; {\"separate\": {\"voice_gain_db\": 0, "
     "\"music_gain_db\": -30}} rebalances the footage's own music; "
     "{\"clean\": 0.8} takes the background noise (hiss, hum, wind, fan, "
     "room tone) out of the footage's own sound, 0 restores it"),
    ("frame", '{"frame": {"ratio": "9:16", "follow": true}}',
     "reframe; letterboxed sources are detected"),
    ("export", '{"export": {"max_size_gb": 4.5}}', "the exported file's "
     "size (or video_kbps) target: the export encodes under the bitrate that "
     "fits the film's length and is measured and re-encoded until it fits"),
    ("grade", '{"grade": {"preset": "warm"}}', "a grade preset or "
     "{\"look\": name}"),
    ("transition", '{"transition": {"style": "crossfade", "duration_s": 0.3}}',
     "transitions at cuts; {\"at\": t} sets one junction"),
    ("zoom", '{"zoom": {"start": 4, "end": 6, "mode": "push_in"}}',
     "a camera move, film seconds"),
    ("layer set/upsert/remove", '{"upsert": "texts", "id": "tx1", "value": '
     '{"end": 5}}', "any EDL layer by id (no size cap); set replaces a "
     "layer, remove deletes an item"),
    ("clips", '{"clips": {"items": [{"range": [612, 655], "title": "..."}], '
     '"frame": "9:16"}}', "new child projects, one per clip"),
    ("tool", '{"tool": "add_typeset", "args": {...}}',
     "any write verb below, on the same staged edit"),
]


def _verbs():
    import agent_tools
    lines = []
    for name in sorted(agent_tools.WRITE_TOOLS):
        entry = agent_tools.TOOLS.get(name)
        if not entry:
            continue
        params = {}
        if len(entry) > 2 and isinstance(entry[2], dict):
            params = entry[2].get("properties") or entry[2]
        args = ", ".join(list(params)[:8]) if isinstance(params, dict) \
            else ""
        lines.append(f"  {name}({args}): "
                     f"{agent_tools._compact_description(entry[1])}")
    return lines


def _layer(name):
    import schemas
    edl = schemas.EDL.model_fields
    if name not in edl:
        return (f"no layer named {name!r}; layers: "
                + ", ".join(sorted(edl)))
    field = edl[name]
    annotation = str(field.annotation)
    out = [f"layer {name}: {annotation[:400]}"]
    for cls_name in dir(schemas):
        cls = getattr(schemas, cls_name)
        fields = getattr(cls, "model_fields", None)
        if isinstance(fields, dict) and cls_name in annotation:
            out.append(f"  {cls_name}: " + ", ".join(
                f"{k}" for k in fields)[:1500])
    return "\n".join(out)


def describe(schema):
    schema = str(schema or "ops").strip()
    if schema != "ops":
        return _layer(schema)
    lines = ["edit ops (keep/cut/restore/fit_length run first, in "
             "source seconds; speed and everything placed on the film use "
             "the resulting film clock unless it says at_source):"]
    for name, example, contract in OPS:
        lines.append(f"- {name}: {example}\n    {contract}")
    lines.append("write verbs for {\"tool\": name, \"args\": {...}}:")
    lines.extend(_verbs())
    return "\n".join(lines)


def op_names():
    return [name for name, _e, _c in OPS]


def as_json():
    return json.dumps([{"op": n, "example": e, "does": c}
                       for n, e, c in OPS])
