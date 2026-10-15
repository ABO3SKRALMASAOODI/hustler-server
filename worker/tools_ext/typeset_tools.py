"""Department ``type``: the typesetter's verbs (Wave 2 tracks 2A/2B).

  add_typeset     poster-scale type for a spoken phrase (or a window of the
                  transcript), solved for this frame: a type voice picked
                  for the story beat, a layout family in the free space, every
                  word on its spoken onset; it REPLACES the captions for the
                  words it shows
  set_typeset     change voice / layout / verb / depth / palette / window
  remove_typeset  remove typeset items by id
  import_font     validate a user's brand font (fonts_brand.py) for typeset

Each write SOLVES the intent into one apply_edit_batch operation (a motion
item, template ``typeset``) and commits it through tools_ext._write.commit,
so an MCP client with a stale tool list writes the very same item through
apply_edit_batch (skills/typesetting.md lists the shape). Geometry is never
stored: the render re-solves it on every canvas (INTERFACES_W2.md §3).

Browserless: shaping is Python (typeset.shaping); nothing opens Chromium.
"""

import difflib
import hashlib
import json
import re

from tools_ext import _write

DEPARTMENT = "type"
VERBS = ("add_typeset", "set_typeset", "remove_typeset", "import_font")

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_TOK = re.compile(r"[^\w%$']+")
MAX_WORDS = 24
LEAD_S = 0.30                 # the window opens before the first word
HOLD_S = 0.8                  # and holds after the last (the payoff hold)
POSITIONS = ("auto", "hook", "build", "turn", "payoff", "mid")


def _norm(w):
    return _TOK.sub("", str(w or "").lower().replace("’", "'"))


def _latest(ctx):
    return json.loads(json.dumps(ctx.latest_edl()["json"]))


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


def _next_id(items, prefix="ts"):
    have = {str(i.get("id")) for i in items or []}
    k = 1
    while f"{prefix}{k}" in have:
        k += 1
    return f"{prefix}{k}"


def match_words(text, pw):
    """Where ``text`` is said: (first program word, last program word,
    matched count) for the longest run of the text's tokens found in the
    program words ``pw`` (device_planner.program_words), or None."""
    want = [_norm(w) for w in str(text or "").replace("*", " ").split()]
    want = [w for w in want if w]
    have = [_norm(w.get("w")) for w in pw]
    if not want or not have:
        return None
    sm = difflib.SequenceMatcher(None, have, want, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return None
    # the cluster of blocks around the biggest one, in program order
    main = max(blocks, key=lambda b: b.size)
    lo, hi = main.a, main.a + main.size - 1
    n = main.size
    for b in blocks:
        if b is main:
            continue
        if b.a + b.size - 1 < lo and lo - (b.a + b.size - 1) <= 4:
            lo, n = b.a, n + b.size
        elif b.a > hi and b.a - hi <= 4:
            hi, n = b.a + b.size - 1, n + b.size
    return pw[lo], pw[hi], n


def _window_text(pw, s, e):
    ws = [w for w in pw if s - 1e-3 <= w["t0"] < e]
    return " ".join(str(w["w"]).strip() for w in ws), ws


def _position(edl, s, e, prog):
    if s <= 3.0:
        return "hook"
    if e >= prog - 4.0:
        return "payoff"
    return "mid"


def _seed(ctx, text):
    h = hashlib.sha256(f"{getattr(ctx, 'project_id', '')}:{text}".encode())
    return int(h.hexdigest()[:6], 16)


def _faces(ctx, edl, W, H, s, e):
    try:
        import keepout
        import motion_tools
        track = []
        if getattr(ctx, "has_main_video", True):
            track = keepout.face_track(edl, getattr(ctx, "index", None) or {},
                                       W, H, s, e,
                                       measure=motion_tools._face_measure(ctx))
        return [tuple(z[:4]) for z in keepout.zones_of(track)]
    except Exception:  # noqa: BLE001 — the solver assumes a face (prior)
        return []


def _composite_live():
    try:
        import schemas
        return "MotionItem.composite" in (getattr(schemas, "_FX_SLOT_TYPES",
                                                  {}) or {})
    except Exception:  # noqa: BLE001
        return False


def solve_into(ctx, edl, item):
    """Solve ``item`` for this edit's canvas: returns (item with layer and
    footprint, doc, notes) or (None, doc, reason)."""
    import caption_carry
    import motion_tools
    from typeset import layoutdoc
    W, H = motion_tools._canvas_size(ctx, edl)
    s, e = float(item["start"]), float(item["end"])
    faces = _faces(ctx, edl, W, H, s, e)
    # the reading the render will use (caption_carry: reads_onsets)
    try:
        trial = dict(edl, motion=[m for m in edl.get("motion") or []
                                  if m.get("id") != item["id"]] + [item])
        rd = caption_carry.readings(trial, getattr(ctx, "index", None) or {},
                                    _tl(trial)).get(item["id"])
        if rd:
            item["reading"] = rd
    except Exception:  # noqa: BLE001 — the solver spaces unsaid words
        pass
    fps = 30.0
    try:
        import device_planner
        fps = device_planner.program_fps(edl, getattr(ctx, "index", None))
    except Exception:  # noqa: BLE001
        pass
    doc = layoutdoc.solve_item(item, W, H, fps, faces=faces)
    rep = doc.get("solver_report") or {}
    if not doc.get("runs"):
        return None, doc, rep.get("error") or "no layout fits"
    item["layer"] = ("behind_subject" if rep.get("depth") == "behind"
                     else "above_captions")
    notes = []
    if rep.get("voice_fallback"):
        item["params"]["voice"] = doc.get("voice")
        notes.append("voice " + rep["voice_fallback"])
    if rep.get("callback"):
        notes.append("rhymes with its first telling (same geometry)")
    if rep.get("depth_fallback"):
        notes.append("set in front: " + rep["depth_fallback"])
    if item["layer"] == "behind_subject" and _composite_live():
        item["composite"] = {"ghost": "auto"}
    else:
        item.pop("composite", None)
    box = layoutdoc.bbox_fraction(doc)
    if box:
        geo = None
        try:
            geo = caption_carry.face_geometry(edl)
        except Exception:  # noqa: BLE001
            pass
        item["footprint"] = caption_carry.make_footprint(box, W, H, faces,
                                                         geo=geo)
    return item, doc, notes


def _summary(item, doc):
    rep = doc.get("solver_report") or {}
    p = item["params"]
    return (f"typeset '{p['text'][:60]}' voice {p.get('voice')} "
            f"{doc.get('family')} (payload {rep.get('payload_cap_pct')}% "
            f"of the height, {rep.get('depth')}) at {item['start']:g}-"
            f"{item['end']:g}s [{item['id']}]")


def _params(text, voice, layout, depth, verb, exit, material, accent, ink,
            region, emphasis, flip_at, orbit, seed, callback_of, mirror):
    p = {"text": text, "voice": voice, "layout": layout or "auto",
         "depth": depth or "front", "verb": verb or "auto"}
    for k, v in (("exit", exit), ("material", material), ("accent", accent),
                 ("ink", ink), ("region", region), ("flip_at", flip_at),
                 ("orbit", orbit), ("seed", seed),
                 ("callback_of", callback_of), ("mirror", mirror)):
        if v not in (None, ""):
            p[k] = v
    if emphasis:
        p["emphasis"] = ([emphasis] if isinstance(emphasis, str)
                         else list(emphasis))[:4]
    return p


def add_typeset(ctx, text=None, start=None, end=None, voice="auto",
                layout="auto", depth="front", verb="auto", exit=None,
                material=None, accent=None, ink=None, region=None,
                emphasis=None, flip_at=None, orbit=None, position="auto",
                callback_of=None, mirror=None, id=None):
    """Poster-scale type for a spoken phrase, solved for this frame."""
    import device_planner
    edl = _latest(ctx)
    index = getattr(ctx, "index", None) or {}
    tl = _tl(edl)
    prog = float(tl.out_duration or 0.0)
    if prog <= 0.3:
        return "REJECTED: there is no program yet — place footage first."
    pw = device_planner.program_words(edl, index, tl)
    try:
        s = None if start is None else float(start)
        e = None if end is None else float(end)
    except (TypeError, ValueError):
        return "REJECTED: start and end are program seconds."
    if not text:
        if s is None or e is None:
            return ("REJECTED: give the phrase (text) or a window (start, "
                    "end) whose spoken words the type should set.")
        text, ws = _window_text(pw, s, e)
        if not text:
            return (f"REJECTED: nothing is said between {s:g}s and {e:g}s; "
                    "give the words as text.")
    text = " ".join(str(text).split())
    n = len(text.replace("*", " ").split())
    if n > MAX_WORDS:
        return (f"REJECTED: {n} words is a paragraph, not a lockup — set "
                f"the phrase that matters (at most {MAX_WORDS} words); the "
                "captions carry the rest.")
    hit = match_words(text, pw)
    if s is None:
        s = (hit[0]["t0"] - LEAD_S) if hit else None
    if e is None:
        e = (hit[1]["t1"] + HOLD_S) if hit else None
    if s is None or e is None:
        return ("REJECTED: those words are not said in this edit — give "
                "start and end for editorial type.")
    s = round(min(max(0.0, s), max(0.0, prog - 0.4)), 2)
    e = round(min(max(e, s + 0.4), prog), 2)
    items = edl.get("motion") or []
    if id is not None and not _ID_RE.match(str(id)):
        return "REJECTED: id must be 1-40 letters, digits, '_' or '-'."
    mid = str(id) if id else _next_id(items)
    if any(m.get("id") == mid for m in items):
        return (f"REJECTED: '{mid}' already exists — use set_typeset to "
                "change it.")
    import creative
    reason = None
    cb = None
    if callback_of:
        cb = next((m for m in items if m.get("id") == callback_of and
                   m.get("template") == "typeset"), None)
        if cb is None:
            return f"REJECTED: callback_of names no typeset item '{callback_of}'."
    if voice in (None, "", "auto"):
        pos = position if position in POSITIONS[1:] else _position(
            edl, s, e, prog)
        voice, reason = creative.pick_voice(
            text, position=pos, ledger=creative.variety_ledger(edl, upto=s),
            seed=_seed(ctx, text),
            callback_voice=((cb or {}).get("params") or {}).get("voice"))
    seed = _seed(ctx, text) % 997
    if cb is not None:
        cp = cb.get("params") or {}
        layout = cp.get("layout") if layout in (None, "", "auto") else layout
        seed = int(cp.get("seed") or seed)
        region = region or cp.get("region")
    params = _params(text, voice, layout, depth, verb, exit, material, accent,
                     ink, region, emphasis, flip_at, orbit, seed, callback_of,
                     mirror)
    try:
        import motion_templates
        params = motion_templates.check_params("typeset", params)
    except ValueError as err:
        return f"REJECTED: {err}"
    if cb is not None:
        try:
            import motion_tools
            from typeset import layoutdoc, memory
            W, H = motion_tools._canvas_size(ctx, edl)
            first = layoutdoc.solve_item(cb, W, H)
            params = memory.callback_params(params, first, bool(mirror))
        except Exception:  # noqa: BLE001 — solved fresh instead
            pass
    item = {"id": mid, "template": "typeset", "start": s, "end": e,
            "params": params, "layer": "above_captions",
            "purpose": "typeset lockup (replaces the captions for its words)"}
    item, doc, notes = solve_into(ctx, edl, item)
    if item is None:
        return (f"REJECTED: {notes} — try fewer words, another voice "
                "(e.g. editorial_serif for a long line), layout "
                "side_column, or depth front.")
    op = {"action": "upsert", "layer": "motion", "id": mid, "value": item}
    res = _write.commit(ctx, [op], _summary(item, doc))
    if res.startswith("REJECTED") or res.startswith("NO CHANGE"):
        return res
    extra = []
    if reason:
        extra.append(f"voice: {reason}")
    extra += notes
    if not hit:
        extra.append("these words are not in the transcript: the type is "
                     "editorial (captions keep running)")
    return res + ("\nNOTE: " + "; ".join(extra) + "." if extra else "")


def set_typeset(ctx, id, text=None, start=None, end=None, voice=None,
                layout=None, depth=None, verb=None, exit=None, material=None,
                accent=None, ink=None, region=None, emphasis=None,
                flip_at=None, orbit=None, mirror=None):
    """Change a typeset item (any field); it is solved again."""
    edl = _latest(ctx)
    items = edl.get("motion") or []
    cur = next((m for m in items if m.get("id") == id), None)
    if cur is None or cur.get("template") != "typeset":
        have = ", ".join(m["id"] for m in items
                         if m.get("template") == "typeset") or "none"
        return f"REJECTED: '{id}' is not a typeset item (typeset: {have})."
    item = json.loads(json.dumps(cur))
    p = dict(item.get("params") or {})
    for k, v in (("text", text), ("voice", voice), ("layout", layout),
                 ("depth", depth), ("verb", verb), ("exit", exit),
                 ("material", material), ("accent", accent), ("ink", ink),
                 ("region", region), ("flip_at", flip_at), ("orbit", orbit),
                 ("mirror", mirror)):
        if v is not None:
            p[k] = v
    if emphasis is not None:
        p["emphasis"] = ([emphasis] if isinstance(emphasis, str)
                         else list(emphasis))[:4]
    try:
        import motion_templates
        p = motion_templates.check_params("typeset", p)
        if start is not None:
            item["start"] = round(float(start), 2)
        if end is not None:
            item["end"] = round(float(end), 2)
    except (TypeError, ValueError) as err:
        return f"REJECTED: {err}"
    item["params"] = p
    item.pop("reading", None)
    item.pop("footprint", None)
    item, doc, notes = solve_into(ctx, edl, item)
    if item is None:
        return f"REJECTED: {notes}."
    op = {"action": "upsert", "layer": "motion", "id": id, "value": item}
    res = _write.commit(ctx, [op], "re-set " + _summary(item, doc))
    if notes and not res.startswith("REJECTED"):
        res += "\nNOTE: " + "; ".join(notes) + "."
    return res


def remove_typeset(ctx, ids):
    """Remove typeset items by id (their words return to the captions)."""
    edl = _latest(ctx)
    if isinstance(ids, str):
        ids = [p for p in re.split(r"[\s,]+", ids) if p]
    have = {m.get("id"): m for m in edl.get("motion") or []}
    bad = [i for i in ids or [] if (have.get(i) or {}).get("template")
           != "typeset"]
    if not ids or bad:
        return (f"REJECTED: not typeset items: {', '.join(bad) or '(none)'}.")
    ops = [{"action": "remove", "layer": "motion", "id": i} for i in ids]
    return _write.commit(ctx, ops, f"removed typeset {', '.join(ids)}")


def import_font(ctx, asset_key, licence, family=None, attest=False):
    """Validate a brand font the user uploaded for typeset."""
    try:
        import fonts_brand
    except Exception as e:  # noqa: BLE001
        return f"REJECTED: brand fonts are unavailable here ({e})."
    return fonts_brand.import_for_project(ctx, asset_key, licence,
                                          family=family, attest=attest)


_TEXT = {"type": "string", "description": "the phrase as printed, in spoken "
         "order; *star* the one accent word. Omit to set the words said in "
         "start..end"}
_VOICE = {"type": "string", "enum": ["auto", "magazine", "whisper_shout",
                                     "staircase_red", "slot_blackletter",
                                     "neon_script", "editorial_serif",
                                     "quote_window", "condensed_poster",
                                     "lamp_karaoke", "hairline_wide",
                                     "marker_note", "swiss_grid",
                                     "techno_crt"]}
_LAYOUT = {"type": "string", "enum": ["auto", "stack", "magazine",
                                      "staircase", "stack_up", "justified",
                                      "side_column", "slots", "flank",
                                      "constellation", "spread", "tilt",
                                      "quote_window", "arc"]}
_VERB = {"type": "string", "enum": ["auto", "cut", "fade", "blur_resolve",
                                    "settle", "scramble_lock",
                                    "mosaic_resolve", "stretch_smear",
                                    "duration_wipe", "strobe_flip",
                                    "emboss_ink", "echo_stack",
                                    "outline_fill", "glyph_morph", "lamp",
                                    "typewriter", "drop"]}
_DEPTH = {"type": "string", "enum": ["front", "behind", "ghost", "auto"]}
_COMMON = {
    "voice": _VOICE, "layout": _LAYOUT, "depth": _DEPTH, "verb": _VERB,
    "exit": {"type": "string", "enum": ["cut", "fade", "decay", "hold"]},
    "material": {"type": "string", "enum": ["auto", "flat", "glow", "neon",
                                            "crt", "outline", "ghost",
                                            "emboss"]},
    "accent": {"type": "string", "description": "#rrggbb"},
    "region": {"type": "string", "enum": ["auto", "above", "below", "left",
                                          "right"]},
    "emphasis": {"type": "array", "items": {"type": "string"}},
    "flip_at": {"type": "number", "description": "item s: ink/accent swap"},
    "orbit": {"type": "number", "description": "arc: rad/s"},
}

TOOL_SPECS = {
    "add_typeset": (
        add_typeset,
        "Poster-scale type for one spoken phrase, solved for this frame: a "
        "type voice chosen for the story beat (never the same voice twice "
        "running), a layout family in the free space around the face, each "
        "word landing on its spoken onset. It replaces the captions for its "
        "words. Use it for a hook, a turn or a payoff, not for dense "
        "explanations. depth=behind sets it behind the subject.",
        {"text": _TEXT,
         "start": {"type": "number", "description": "program s"},
         "end": {"type": "number", "description": "program s"},
         **_COMMON,
         "position": {"type": "string", "enum": list(POSITIONS)},
         "callback_of": {"type": "string", "description": "the typeset id a "
                         "repeated line rhymes with"},
         "mirror": {"type": "boolean"},
         "id": {"type": "string"}}),
    "set_typeset": (
        set_typeset,
        "Change a typeset item (voice, layout, verb, depth, palette, words "
        "or window); it is solved again for this frame.",
        {"id": {"type": "string"}, "text": {"type": "string"},
         "start": {"type": "number"}, "end": {"type": "number"},
         **_COMMON, "mirror": {"type": "boolean"}}),
    "remove_typeset": (
        remove_typeset,
        "Remove typeset items by id; their words return to the captions.",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
    "import_font": (
        import_font,
        "Validate a brand font the user uploaded (TTF/OTF asset) so typeset "
        "can set it: embedding rights checked, sanitised, subset. licence is "
        "the user's statement of their right to use it (OFL, Apache, "
        "commercial licence they hold); attest=true confirms it.",
        {"asset_key": {"type": "string"},
         "licence": {"type": "string"},
         "family": {"type": "string"},
         "attest": {"type": "boolean"}}),
}
REQUIRED_ARGS = {"set_typeset": ["id"], "remove_typeset": ["ids"],
                 "import_font": ["asset_key", "licence"]}
WRITE_TOOLS = {"add_typeset", "set_typeset", "remove_typeset"}
COMPACT = {
    "add_typeset": "Poster-scale type for a spoken phrase (text, or start/end) "
                   "solved for this frame; voice auto picks per beat; "
                   "replaces those captions. depth behind|front.",
    "set_typeset": "Change a typeset item (voice/layout/verb/depth/palette/"
                   "words/window); re-solved.",
    "remove_typeset": "Remove typeset items by id.",
    "import_font": "Validate an uploaded brand font for typeset (licence "
                   "attestation).",
}
