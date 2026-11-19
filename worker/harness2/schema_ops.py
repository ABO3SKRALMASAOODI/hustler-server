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
    ("speed", '{"speed": [{"start": 6, "factor": 0.5}]}',
     "a constant speed over FILM seconds from start to end; no end (or "
     "\"end\": \"end\") = to the film's end, so 'after 6 s' is the rest of "
     "the film; \"ramp\": true eases in and out of the factor (a speed ramp); "
     "add \"at_source\": true to give source seconds; the result measures "
     "where it plays against the film's length"),
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
     '"duck": true}}', "a track from find(kind='music') (lib:<id>, or "
     "cc0:<slug> for a track the person named) or the user's own 'key'; it "
     "REPLACES the film's bed in place ('add': true lays a second track); "
     "provenance is stamped"),
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
    ("grade", '{"grade": {"preset": "warm", "custom": {"exposure": 0.1, '
     '"temperature": 0.2, "shadows": 0.1}}}', "a grade preset, a {\"look\": "
     "name}, and/or the continuous controls (exposure, contrast, saturation, "
     "temperature, tint, shadows, highlights; -1..1, contrast and saturation "
     "around 1); the envelope measures the result's lightness and colour"),
    ("transition", '{"transition": {"style": "crossfade", "duration_s": 0.3}}',
     "transitions at cuts; {\"at\": t} sets one junction"),
    ("zoom", '{"zoom": [{"start": 4, "end": 4.4, "mode": "landing"}, '
     '{"start": 6, "end": 9, "mode": "push_in", "strength": 0.08}]}',
     "camera moves, film seconds, one row or many: punch (snap in on a "
     "word), landing (arrive pushed in on a cut), push_in / pull_out "
     "(slow, across a hold), ease, pulse, shake"),
    ("element", '{"element": {"kind": "bubbles", "start": 2, "end": 8}}',
     "a drawn element over the picture: particles (embers, sparks, dust, "
     "confetti, snow, fireworks, bubbles; emit edge|point|box|frame), light "
     "(light_dissolve, light_trail, light_rim), doodles (arrow, underline, "
     "circle, scribble, burst, star ...), lightning, orbit"),
    ("finish", '{"finish": {"intensity": "designed"}}',
     "the craft departments over the current cut in one op, each one the "
     "cut lacks: look (designed caption system, grade, texture, "
     "transitions; 'look': editorial | creator_punch | cinematic_doc | "
     "mono_noir | clean_minimal ...), captions, moves (punch-ins on stressed "
     "words, or an alternating camera on footage without speech), pictures "
     "(B-roll cut in on the word: 'pictures': [{word, query, depicts?}]), "
     "music (a reference-grade track; 'music': 'auto' | id | 'none'), sound "
     "(designed sound on what lands on screen), master (-14 LUFS); "
     "'departments': [...] runs only those; 'sport': true (or 'moves': "
     "'alternate') adds the action devices: wide and digital close-up "
     "alternating piece by piece, speed (slow motion on the measured peaks, "
     "speed-ups on the quiet stretches) and beats (cuts slid onto the "
     "song's beats), leaving type and look as they are; 'captions': false "
     "keeps the film without captions whatever a look would draw"),
    ("direct", '{"direct": {"brief": "a 30 s reel that opens on the save"}}',
     "the Director's whole film from the full upload (Scout, real pictures, "
     "bed, type, sound) written as its own version before the other ops of "
     "the same call; where it cannot run, the envelope says why and the "
     "same film is reached with finish + place/scenes"),
    ("layer set/upsert/remove", '{"upsert": "texts", "id": "tx1", "value": '
     '{"end": 5}}', "any EDL layer by id (no size cap); set replaces a "
     "layer, remove deletes an item"),
    ("clips", '{"clips": {"items": [{"range": [612, 655], "title": "..."}], '
     '"frame": "9:16"}}', "new child projects, one per clip, each with its "
     "title on screen over the opening and captions, rendered so it plays "
     "on its card ('title_on_screen': false, 'captions': false or a caption "
     "style, 'render': false change that); the handle settles when the "
     "clips exist"),
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
