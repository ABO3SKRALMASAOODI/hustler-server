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

import copy
import difflib
import hashlib
import json
import math
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


def _clean(text):
    """The phrase as printed: one space between words, a transcript's
    detached sign or suffix joined to its word ("26 %" -> "26%")."""
    from typeset import roles
    return roles.clean_text(" ".join(str(text or "").split()))


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


# match_words: the share of the text's words a run must match to be where
# the text is said (2 of 2, 2 of 3, 4 of 6)
MATCH_SHARE = 0.6


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
    if n < math.ceil(MATCH_SHARE * len(want) - 1e-9):
        # one shared word is not where a line is said: Earnings 4562 v42's
        # editorial hook 'the chip secret Elon won't share' matched only
        # 'chip' at 4.68 s, and add_typeset(0.1-2.7) moved its start there
        # (job 100397: 'span [4.55, 2.7] is 0.000s')
        return None
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


def solve_into(ctx, edl, item, faces=None):
    """Solve ``item`` for this edit's canvas: returns (item with layer and
    footprint, doc, notes) or (None, doc, reason). ``faces`` ([] over a
    black card) overrides the faces measured on the footage."""
    import caption_carry
    import motion_tools
    from typeset import layoutdoc
    W, H = motion_tools._canvas_size(ctx, edl)
    s, e = float(item["start"]), float(item["end"])
    if faces is None:
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
    asked = item["params"].get("layout")
    if asked not in (None, "", "auto") and doc.get("family") and \
            doc.get("family") != asked:
        # what renders is what was solved: the family the solver fell back
        # to is written, so the render never re-tries a family that failed
        item["params"]["layout"] = doc["family"]
        notes.append(f"layout {asked} did not fit; set as {doc['family']}")
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


def build_item(ctx, edl, text, s, e, mid, voice="auto", depth="front",
               position="auto", faces=None, avoid=(), verb="auto"):
    """(typeset item solved for this canvas, None) or (None, reason): the
    write-free core of add_typeset for planners that fill type slots
    (plan_to_music's call-and-response, 2F formats)."""
    import creative
    import motion_templates
    text = _clean(text)
    if not text:
        return None, "no words"
    words = text.replace("*", " ").split()
    if len(words) > MAX_WORDS:
        text = " ".join(text.split()[:MAX_WORDS])
    prog = float(_tl(edl).out_duration or 0.0)
    if voice in (None, "", "auto"):
        pos = position if position in POSITIONS[1:] else _position(
            edl, s, e, prog)
        voice, _why = creative.pick_voice(
            text, position=pos, ledger=creative.variety_ledger(edl, upto=s),
            seed=_seed(ctx, text), avoid=tuple(avoid or ()))
    params = _params(text, voice, "auto", depth, verb or "auto", None, None,
                     None,
                     None, None, None, None, None, _seed(ctx, text) % 997,
                     None, None)
    if faces is not None and not faces:
        params["over"] = "card"          # no face here, not an unknown one
    try:
        params = motion_templates.check_params("typeset", params)
    except ValueError as err:
        return None, str(err)
    item = {"id": mid, "template": "typeset", "start": round(float(s), 2),
            "end": round(float(e), 2), "params": params,
            "layer": "above_captions",
            "purpose": "typeset lockup (replaces the captions for its words)"}
    item, doc, notes = solve_varied(ctx, edl, item, faces=faces)
    if item is None:
        return None, notes
    return item, None


# families any voice can set besides its own (the special ones, quote
# window, arc and slots, stay with their voices)
GENERAL_FAMILIES = ("side_column", "justified", "staircase", "spread",
                    "stack_up", "flank", "magazine", "stack",
                    "constellation")
VARY_TRIES = 5


def _recent_families(ctx, edl, s, n=2):
    """Families of the last ``n`` typeset lockups before program second s."""
    try:
        import motion_tools
        from typeset import layoutdoc
        W, H = motion_tools._canvas_size(ctx, edl)
        prev = sorted((m for m in edl.get("motion") or []
                       if m.get("template") == "typeset"
                       and float(m.get("start") or 0) < s),
                      key=lambda m: float(m.get("start") or 0))[-n:]
        return [layoutdoc.solve_item(m, W, H).get("family") for m in prev]
    except Exception:  # noqa: BLE001
        return []


def solve_varied(ctx, edl, item, faces=None):
    """solve_into with range: when the layout is 'auto' (and the item is
    not a callback, whose geometry IS the point), the solver's own best
    family is kept unless one of the last two lockups used it; then the
    other families are tried (the voice's own first) until one fits, within
    the solve budget, so consecutive lockups never share a family when
    another one fits. A family that fits is written into the params (what
    renders is what was solved; another aspect falls back to auto). One
    solve in the common case: a write on a 0.5 vCPU lane stays quick."""
    import time
    p = item.get("params") or {}
    if p.get("layout") not in (None, "", "auto") or p.get("callback_of"):
        return solve_into(ctx, edl, item, faces=faces)
    recent = [f for f in _recent_families(ctx, edl, float(item["start"]))
              if f]
    t0 = time.monotonic()
    base = solve_into(ctx, edl, copy.deepcopy(item), faces=faces)
    if not recent or base[0] is None or base[1].get("family") not in recent:
        return base
    from typeset import voices as V
    try:
        import config
        budget = 1.5 * float(config.TYPESET_SOLVE_BUDGET_S)
    except Exception:  # noqa: BLE001
        budget = 3.0
    own = list((V.VOICES.get(p.get("voice")) or {}).get("layouts") or ())
    order = [f for f in own if f not in recent] + \
        [f for f in GENERAL_FAMILIES if f not in own and f not in recent]
    for fam in order[:VARY_TRIES]:
        if time.monotonic() - t0 > budget:
            break
        trial = copy.deepcopy(dict(item, params=dict(p, layout=fam)))
        it, doc, notes = solve_into(ctx, edl, trial, faces=faces)
        if it is not None and doc.get("family") == fam:
            return it, doc, notes + [f"family {fam} (the last lockups were "
                                     f"{', '.join(recent)})"]
    return base


def trim_previous(edl, item, pw=None, min_s=0.4):
    """Ops ending any earlier typeset lockup still on screen when this one
    starts (two lockups solved independently would draw over each other);
    one that cannot keep ``min_s`` of its own is left for the user. A
    lockup is never ended before its own last word is out: the new one
    starts later instead (``item['start']`` moves, never past its first
    word's onset)."""
    s = float(item["start"])
    out = []
    for m in edl.get("motion") or []:
        if m.get("template") != "typeset" or m.get("id") == item.get("id"):
            continue
        a, b = float(m.get("start") or 0), float(m.get("end") or 0)
        if not a < s < b:
            continue
        last = None
        if pw:
            hit = match_words(str((m.get("params") or {}).get("text", "")),
                              pw)
            last = float(hit[1]["t1"]) if hit else None
        cut = s if last is None else max(s, last)
        if last is not None and last > s:
            item["start"] = round(min(last, float(item["end"]) - 0.4), 2)
            cut = float(item["start"])
        if cut - a >= min_s and cut < b:
            out.append({"action": "upsert", "layer": "motion", "id": m["id"],
                        "value": {"id": m["id"], "end": round(cut, 2)}})
    return out


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


def _compose(ctx, edl, mid, text, s, e, prog, pw, voice="auto",
             layout="auto", depth="front", verb="auto", exit=None,
             material=None, accent=None, ink=None, region=None,
             emphasis=None, flip_at=None, orbit=None, position="auto",
             callback_of=None, mirror=None, faces=None):
    """The write-free core of add_typeset (and of the planners that write
    lockups: apply_format_pack): (trim ops, solved item, doc, notes, voice
    reason) or a REJECTED line. The previous lockup still on screen ends
    where this one starts (never before its own last word); the voice is
    picked for the beat when 'auto'; a callback rhymes with its first
    telling; the item is solved for this canvas with the measured faces."""
    items = edl.get("motion") or []
    held = None
    hit = match_words(text, pw) if pw else None
    if hit and (float(hit[0]["t0"]) >= e - 1e-3
                or float(hit[1]["t1"]) <= s + 1e-3):
        hit = None          # said elsewhere: the window given is editorial
    if hit:
        # the hold after the last word ends where the next words are said:
        # a lockup held over them leaves their captions no band (they were
        # heard but never shown — AT-2A-1 on V01 with the faces measured)
        last_t1 = float(hit[1]["t1"])
        nxt = next((w for w in pw if float(w["t0"]) >= last_t1 - 1e-3
                    and w is not hit[1] and float(w["t0"]) > float(
                        hit[1]["t0"])), None)
        if nxt is not None and float(nxt["t0"]) < e - 1e-3:
            e2 = round(max(last_t1 + 0.12, float(nxt["t0"])), 2)
            if s + 0.4 <= e2 < e:
                held, e = e2, e2
        # and its lead-in (an entrance resolving onto the onset) never
        # covers the middle of the word before it: that word's caption is
        # read there
        first_t0 = float(hit[0]["t0"])
        prev = [w for w in pw if float(w.get("t1", w["t0"])) <= first_t0
                + 1e-3 and float(w["t0"]) < first_t0 - 1e-3]
        if prev:
            p_mid = (float(prev[-1]["t0"]) + float(prev[-1].get(
                "t1", prev[-1]["t0"]))) / 2.0
            if s < p_mid + 0.03:
                s = round(min(first_t0, p_mid + 0.03), 2)
    # no two lockups on screen at once: the previous one ends here (never
    # before its own last word), solved before this one is
    prov = {"id": mid, "start": s, "end": e}
    trims = trim_previous(edl, prov, pw)
    s = float(prov["start"])
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
            callback_voice=((cb or {}).get("params") or {}).get("voice"),
            batch=_batch_voices(ctx))
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
    item, doc, notes = solve_varied(ctx, edl, item, faces=faces)
    if item is None:
        return f"REJECTED: {notes} — " + _no_fit_options(params, s, e)
    if held is not None:
        notes = list(notes) + [f"held until {held:g}s, where the next "
                               "words are said (their captions need the "
                               "frame)"]
    return trims, item, doc, notes, reason


def _no_fit_options(params, s, e):
    """What to try when no typeset layout fits, leaving out what this call
    already tried: the same three suggestions were repeated to editors who
    had passed editorial_serif, side_column and depth front (project 4415
    jobs 93821/93828, 4413 job 93795: 16 refusals in the week to 23 Oct)."""
    p = params or {}
    opts = []
    words = len(str(p.get("text") or "").replace("*", "").split())
    if words > 2:
        opts.append("fewer words (the face moves less in a shorter window)")
    if e - s > 1.6:
        opts.append(f"a shorter window ({e - s:.1f}s now)")
    if p.get("voice") != "editorial_serif":
        opts.append("another voice (e.g. editorial_serif for a long line)")
    if p.get("layout") != "side_column":
        opts.append("layout side_column")
    if p.get("depth") != "front":
        opts.append("depth front")
    opts.append("a moment where the face sits elsewhere, or a word_slam "
                "lockup instead (it takes its own band)")
    return "try " + ", ".join(opts[:-1]) + (", or " if len(opts) > 1
                                            else "") + opts[-1]


def _batch_voices(ctx):
    """Type voices the sibling shorts of this batch set (freshness across a
    batch, not only inside one short; [] outside a batch)."""
    try:
        import creative
        return creative.batch_ledger(ctx).get("voices") or []
    except Exception:  # noqa: BLE001 — freshness is advice
        return []


def solve_planned(ctx, edl, op, pw=None):
    """A planner's typeset upsert (story_planner / format packs) solved the
    way add_typeset solves one: (ops, None) or ([], why it was not set).
    The planner proposes intent (words, window, voice, depth); this
    measures the faces, picks an 'auto' voice, varies the family, ends the
    lockup before it, and drops a line no layout fits rather than writing
    a lockup that would mute its captions and draw nothing."""
    import device_planner
    v = dict(op.get("value") or {})
    p = dict(v.get("params") or {})
    text = _clean(p.get("text"))
    if not text:
        return [], "no words"
    tl = _tl(edl)
    prog = float(tl.out_duration or 0.0)
    if pw is None:
        pw = device_planner.program_words(edl, getattr(ctx, "index", None)
                                          or {}, tl)
    try:
        s = round(min(max(0.0, float(v["start"])), max(0.0, prog - 0.4)), 2)
        e = round(min(max(float(v["end"]), s + 0.4), prog), 2)
    except (KeyError, TypeError, ValueError):
        return [], "no window"
    mid = str(v.get("id") or _next_id(edl.get("motion") or []))
    if any(m.get("id") == mid for m in edl.get("motion") or []):
        mid = _next_id(edl.get("motion") or [])
    got = _compose(ctx, edl, mid, text, s, e, prog, pw,
                   voice=p.get("voice") or "auto",
                   layout=p.get("layout") or "auto",
                   depth=p.get("depth") or "front",
                   verb=p.get("verb") or "auto",
                   position=p.get("position") or "auto",
                   callback_of=p.get("callback_of"))
    if isinstance(got, str):
        return [], got.replace("REJECTED: ", "")
    trims, item, _doc, _notes, _reason = got
    if v.get("purpose"):
        item["purpose"] = str(v["purpose"])[:300]
    return trims + [{"action": "upsert", "layer": "motion", "id": mid,
                     "value": item}], None


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
    text = _clean(text)
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
    got = _compose(ctx, edl, mid, text, s, e, prog, pw, voice=voice,
                   layout=layout, depth=depth, verb=verb, exit=exit,
                   material=material, accent=accent, ink=ink, region=region,
                   emphasis=emphasis, flip_at=flip_at, orbit=orbit,
                   position=position, callback_of=callback_of, mirror=mirror)
    if isinstance(got, str):
        return got
    trims, item, doc, notes, reason = got
    op = {"action": "upsert", "layer": "motion", "id": mid, "value": item}
    res = _write.commit(ctx, trims + [op], _summary(item, doc))
    if res.startswith("REJECTED") or res.startswith("NO CHANGE"):
        return res
    if trims:
        res += "\n" + "; ".join(f"{t['id']} now ends at {t['value']['end']:g}s"
                                 for t in trims) + " (no two lockups overlap)"
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
    if text is not None:
        text = _clean(text)
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
