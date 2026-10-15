"""suggest_devices v3: world and layout candidates (Wave 3, 3F).

ROADMAP Wave 3 / 3F; contract in devices/INTERFACES_W3.md §9.

device_planner.suggest (1F) builds fx candidates from the cards' params.
World devices need what only measurements know — is a hand up on that
word, is the face measured there, is there an object or a plane the edit
already defined — so this module proposes them from the sidecars (status
reads only: browserless, model-free), in the same Candidate shape, each
with the exact apply_edit_batch operations that write it (``op`` the first,
``ops`` all of them).

What it may propose, and when (the taste is the rule, not the reach):
  * type behind an object the edit defined — on the payoff or a number
    word inside the object's window;
  * type on a plane the edit defined — on a payoff / section word in the
    plane's window, multiplied into the surface;
  * a glass card on the fingertip — on a number or payoff word while a hand
    is measurably UP (wrist above the elbow line, visible) for >= 0.8 s;
  * an orbit of words round the head — on the strongest emotion / payoff
    phrase, only on a face of >= 7% of the frame height, once a piece;
  * a light dissolve with embers — on the LAST phrase end, the person
    matte measured (an ending), at most once.

Per-line no-repeat budget (ref 21): within one sentence no world device
repeats, and across the piece no world device repeats on consecutive
sentences. Never a zoom, never a sound, never music.
"""

import numpy as np

MAX_WORLD = 3


def _device_of(m):
    """The world device an anchored motion item already is."""
    a = m.get("anchor") or {}
    to = str(a.get("to") or "")
    if m.get("template") == "el_orbit":
        return "element_orbit"
    if to.startswith("plane:"):
        return "plane_type"
    if any(str(o).startswith("object:") for o in a.get("occluders") or []):
        return "object_occluder"
    fam = to.split(".")[0].split(":")[0]
    return {"hand": "anchor_hand", "face": "anchor_face",
            "head": "anchor_face", "pose": "anchor_pose",
            "track": "anchor_track"}.get(fam, "world")
HAND_UP_MIN_S = 0.8
STRESS_MIN = 0.6
_FUNCTION = {"THIS", "THAT", "THEN", "THERE", "WITH", "FROM", "INTO", "WHAT",
             "WHEN", "WILL", "WOULD", "COULD", "SHOULD", "HAVE", "BEEN",
             "THEY", "THEM", "YOUR", "JUST", "LIKE", "VERY", "ALSO", "SOME",
             "TIME", "THING", "THINGS", "KIND", "SORT"}


def _weak(m):
    try:
        import device_planner
        if device_planner.weak_word(m.get("word_raw") or m.get("word")):
            return True
    except Exception:  # noqa: BLE001
        pass
    return _word_text(m) in _FUNCTION
FACE_MIN_H = 0.07


def _src(index):
    try:
        import matte_track
        return matte_track.read_source(index)
    except Exception:  # noqa: BLE001
        return None


def _wctx(edl, index, src):
    """A world_layer.Ctx for program geometry (no render), or None."""
    try:
        import renderer
        import world_layer
        v = (index or {}).get("video") or {}
        W, H = renderer.frame_dims(float(v.get("width") or 1920),
                                   float(v.get("height") or 1080),
                                   (edl.get("frame") or {}).get("ratio"))
        tl = _tl(edl)
        return world_layer.Ctx(edl, W, H, float(v.get("fps") or 30.0),
                               tl=tl, source=src, index=index,
                               out_duration=tl.out_duration)
    except Exception:  # noqa: BLE001
        return None


def world_layer_box(wctx, box, t):
    try:
        import world_layer
        return world_layer.program_box(wctx, box, t)
    except Exception:  # noqa: BLE001
        return None


def _tl(edl):
    from timeline import Timeline
    return Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                    edl.get("speed") or [])


_END = ".?!…"


def _line_starts(plan):
    """Program seconds where each sentence (a "line") starts, from the
    plan's kept words; [0.0] when the plan has none."""
    words = plan.get("words") or []
    starts = []
    for k, w in enumerate(words):
        prev = str(words[k - 1].get("w") or "").rstrip("\"'”’)") if k else ""
        if k == 0 or (prev and prev[-1] in _END):
            starts.append(float(w["t0"]))
    return starts or [0.0]


def _line_of(starts, t):
    """Index of the sentence that holds program second ``t``."""
    import bisect
    return max(0, bisect.bisect_right(starts, float(t) + 1e-6) - 1)


def _hand_up(src, tl, t, dur=HAND_UP_MIN_S):
    """Is a hand measurably up and visible over program [t, t + dur]?"""
    from anchors import query
    a = tl.out_to_src(t)
    b = tl.out_to_src(min(t + dur, tl.out_duration - 1e-3))
    if a is None or b is None:
        return False
    samples, _sh, _m = query.load("landmarks.hand", src, [(a, b)])
    inside = [s for s in samples if a - 1e-3 <= float(s["t"]) <= b + 1e-3]
    if len(inside) < 3:
        return False
    up = 0
    for s in inside:
        for h in s.get("hands") or []:
            p = np.array(h["p"]).reshape(-1, 2)
            # fingers above the wrist, the hand in the upper 80% of frame
            if p[8, 1] < p[0, 1] and p[8, 1] < 0.8 and h.get("s", 0) > 0.6:
                up += 1
                break
    return up >= 0.8 * len(inside)


def _face_ok(src, tl, t):
    from anchors import query
    a = tl.out_to_src(t)
    if a is None:
        return False
    samples, _sh, _m = query.load("landmarks.face", src, [(a - 0.3, a + 0.3)])
    return any((f["b"][3] - f["b"][1]) >= FACE_MIN_H
               for s in samples for f in s.get("faces") or [])


RING_FIT = 1.35            # el_orbit's anchor_fit: box = 1.35 x the head


def _ring_fits(wctx, src, tl, t):
    """Does a ring round the head (el_orbit attached to the head) stay in
    the program frame at program second ``t``?"""
    from anchors import query
    import world_layer
    a = tl.out_to_src(t)
    if wctx is None or a is None:
        return False
    samples, _sh, _m = query.load("landmarks.face", src, [(a - 0.3, a + 0.3)])
    faces = [f for s_ in samples for f in s_.get("faces") or []
             if f.get("he")]
    if not faces:
        return False
    f = max(faces, key=lambda f: f["he"][3])
    cx, cy, _rx, ry, _d = (float(v) for v in f["he"])
    A = world_layer.program_affine(wctx.geometry("world"), t, a)
    if A is None:
        return False
    half_h = RING_FIT * ry * A[1, 1]                # program height units
    half_w = half_h * wctx.H / float(wctx.W)        # a round ring
    px, py = A[0, 0] * cx + A[0, 2], A[1, 1] * cy + A[1, 2]
    return (px - half_w >= -0.03 and px + half_w <= 1.03
            and py - half_h >= -0.02)


def _matte_ready(src, tl, t0, t1):
    try:
        import matte_track
        import sidecars
        spans = matte_track.program_to_source(tl, t0, t1)
        if not spans:
            return False
        rng = [(a, b) for a, b in spans]
        chunks = sidecars.chunks_for(src.shots, src.duration_s, rng)
        ready = sidecars.ready_chunks("matte.person", src.sha256, src.shots,
                                      src.duration_s, rng,
                                      matte_track.sidecar_params())
        return bool(chunks) and len(ready) == len(chunks)
    except Exception:  # noqa: BLE001
        return False


# words of a last line that a figure turning to light answers (it is the
# line's own image, never a default ending)
ENDING_WORDS = frozenset((
    "gone", "disappear", "disappears", "disappeared", "vanish", "vanishes",
    "vanished", "fade", "fades", "faded", "fading", "light", "glow", "burn",
    "burns", "burned", "burnt", "ash", "ashes", "dust", "forever", "goodbye",
    "die", "dies", "died", "dead", "death", "dissolve", "dissolves",
    "evaporate", "evaporates", "erase", "erased", "ghost", "spirit", "soul",
    "stars", "energy", "transcend", "transcends", "immortal", "eternity"))


def _ending_earned(plan, starts):
    """Does the last sentence speak of vanishing, light or an end?"""
    words = plan.get("words") or []
    if not words:
        return False
    last = starts[-1] if starts else 0.0
    for w in words:
        if float(w.get("t0", 0.0)) + 1e-6 < last:
            continue
        t = str(w.get("w") or "").strip(" .,!?;:\"'“”‘’…()").lower()
        if t in ENDING_WORDS:
            return True
    return False


def _word_text(m):
    w = m.get("word_raw") or m.get("word") or ""
    return str(w).strip(" .,!?;:\"'“”‘’…()").upper()[:28]


def _valid(edl, ops, duration):
    """The write path would accept these ops (apply_batch on a copy)."""
    try:
        import edit_batch
        edit_batch.apply_batch(edl, ops, duration,
                               set(edit_batch.media_keys(edl)))
        return True
    except Exception:  # noqa: BLE001
        return False


def _uid(prefix, used):
    k = 1
    while f"{prefix}{k}" in used:
        k += 1
    used.add(f"{prefix}{k}")
    return f"{prefix}{k}"


def _cand(device, family, verb, ops, at, bind, reason, exemplar):
    return {"device": device, "family": family, "verb": verb,
            "op": ops[0], "ops": ops, "at": at, "bind": bind,
            "reason": reason, "exemplar": exemplar, "est_ms": None,
            "owner_flags": {"adds_sound": False, "adds_zoom": False,
                            "flash": False},
            "rank": None, "detail": {"source": "world_planner"}}


def candidates(edl, index, plan, *, intensity="designed", max_n=MAX_WORLD,
               asked=(), existing=()):
    """World / layout candidates (<= max_n), strongest first."""
    edl = edl or {}
    src = _src(index)
    if src is None or not plan:
        return []
    tl = _tl(edl)
    prog = float(tl.out_duration)
    src_dur = float(((index or {}).get("video") or {}).get("duration")
                    or src.duration_s)
    used = {str(i.get("id")) for i in edl.get("fx") or []} | {
        str(m.get("id")) for m in edl.get("motion") or []}
    moments = sorted(plan.get("moments") or [],
                     key=lambda m: -float(m.get("strength") or 0))
    payoff = plan.get("payoff") or {}
    words = [m for m in moments if m.get("word")]
    defs = {f.get("id"): f for f in edl.get("fx") or []
            if f.get("type") in ("object", "plane")}
    out, per_line = [], {}
    starts = _line_starts(plan)
    # devices the edit already carries count against the budget too
    for m in edl.get("motion") or []:
        if isinstance(m, dict) and m.get("anchor") and m.get("start") \
                is not None:
            per_line.setdefault(_line_of(starts, m["start"]), set()).add(
                _device_of(m))

    def take(c, t):
        """The per-line no-repeat budget: a world device never twice in a
        sentence, never on the sentence before or after its last use."""
        line = _line_of(starts, t)
        for k in (line - 1, line, line + 1):
            if c["device"] in per_line.get(k, set()):
                return False
        if not _valid(edl, c["ops"], src_dur):
            return False
        per_line.setdefault(line, set()).add(c["device"])
        c["detail"]["line"] = line
        out.append(c)
        return True

    # 1. type behind a defined object / on a defined plane
    for fid, d in defs.items():
        a, b = float(d["start"]), float(d["end"])
        inside = [m for m in words if a <= m["t"] < b - 0.6]
        if not inside:
            continue
        m = inside[0]
        text = _word_text(m) or "THIS"
        mid = _uid("ww", used)
        s, e = round(max(a, m["t"] - 0.2), 2), round(min(b, m["t"] + 2.2), 2)
        if d["type"] == "object":
            # the word where the object hides part of it (a wide object's
            # top edge, a tall one's middle), from the object's own box
            where = {"width": 0.8, "y": 0.72}
            pin = [0.5, 0.6]
            pb = (d.get("prompt") or {}).get("box")
            wctx = _wctx(edl, index, src)
            prog_box = world_layer_box(wctx, pb, m["t"]) if pb else None
            if prog_box:
                import attachment
                where = attachment.behind_params(prog_box, wctx.W, wctx.H)
                pin = attachment.behind_pin(prog_box, wctx.W, wctx.H)
            item = {"id": mid, "template": "word_slam", "start": s, "end": e,
                    "params": dict({"text": text, "entrance": "rise",
                                    "role": "condensed"}, **where),
                    "layer": "above_captions",
                    "anchor": {"to": "screen", "pin": pin, "occluders": [
                        f"object:{fid}"]}}
            c = _cand("object_occluder", "world", "place_in_world",
                      [{"action": "upsert", "layer": "motion", "id": mid,
                        "value": item}], s,
                      m.get("bind"),
                      f"'{text}' tucked behind the {d.get('label') or 'object'}"
                      " it names — the room carries the word",
                      "08@1.03")
        else:
            item = {"id": mid, "template": "word_slam", "start": s, "end": e,
                    "params": {"text": text, "entrance": "rise",
                               "role": "condensed", "width": 0.8},
                    "layer": "below_captions",
                    "anchor": {"to": f"plane:{fid}"},
                    "composite": {"blend": "multiply"}
                    if d.get("surface") in ("wall", "screen") else None}
            c = _cand("plane_type", "world", "pin_to_plane",
                      [{"action": "upsert", "layer": "motion", "id": mid,
                        "value": item}], s,
                      m.get("bind"),
                      f"'{text}' laid on the {d.get('surface')} — type that "
                      "belongs to the room", "21@0.2")
        take(c, s)
        if len(out) >= max_n:
            return out
    # 2. a fingertip card on a number / payload / payoff word, or a strong
    #    stressed content word, while a hand is up (the gesture IS the
    #    moment: a word the speaker shows with the hand)
    for m in words:
        tags = set(m.get("tags") or [])
        strong = (m.get("kind") == "stress"
                  and float(m.get("strength") or 0) >= STRESS_MIN
                  and len(_word_text(m)) >= 4 and not _weak(m))
        if not (tags & {"number", "payload"} or m.get("kind") == "word.payoff"
                or strong):
            continue
        if m["t"] + HAND_UP_MIN_S > prog or not _hand_up(src, tl, m["t"]):
            continue
        mid = _uid("gc", used)
        s, e = round(m["t"] - 0.1, 2), round(min(prog, m["t"] + 2.0), 2)
        item = {"id": mid, "template": "glass_card", "start": s, "end": e,
                "params": {"title": _word_text(m) or "THIS", "width": 0.26},
                "layer": "above_captions",
                "anchor": {"to": "hand.index_tip", "pin": [0.5, 0.72],
                           "lost": "hide"}}
        if take(_cand("anchor_hand", "world", "attach",
                      [{"action": "upsert", "layer": "motion", "id": mid,
                        "value": item}], s,
                      m.get("bind"),
                      f"the hand is up on '{_word_text(m) or 'this'}': the "
                      "word rides the fingertip that's showing it",
                      "21@0.5"), s):
            break
    if len(out) >= max_n:
        return out
    # 3. an orbit round the head on the strongest payoff / emotion phrase
    for m in words:
        tags = set(m.get("tags") or [])
        if not (tags & {"emotion", "payload"}):
            continue
        if not _face_ok(src, tl, m["t"]):
            continue
        if not _ring_fits(_wctx(edl, index, src), src, tl, m["t"]):
            # a close-up without headroom: the ring would leave the frame
            continue
        mid = _uid("orb", used)
        s, e = round(m["t"] - 0.2, 2), round(min(prog, m["t"] + 2.6), 2)
        item = {"id": mid, "template": "el_orbit", "start": s, "end": e,
                "params": {"text": _word_text(m) or "THIS", "arc": 210},
                "layer": "below_captions",
                "anchor": {"to": "head", "follow": ["position", "scale"]}}
        take(_cand("element_orbit", "layout", "add_element",
                   [{"action": "upsert", "layer": "motion", "id": mid,
                     "value": item}], s,
                   m.get("bind"),
                   "the idea circles the person who has it", "38@≈15.0"), s)
        break
    if len(out) >= max_n:
        return out
    # 4. an ending: the figure turns to light on the last phrase end —
    #    only where the last line earns it (it speaks of vanishing, light,
    #    an end) or the user asked: a dissolve closing every short of a
    #    batch would be a house style, not a device
    ends = [m for m in plan.get("moments") or []
            if m.get("kind") == "phrase_end"]
    if ends and not (_ending_earned(plan, starts) or set(
            str(a).lower() for a in asked or ()) & {"light", "dissolve",
                                                   "ending", "embers"}):
        ends = []
    if ends:
        last = max(ends, key=lambda m: m["t"])
        s = round(max(0.0, last["t"] - 0.3), 2)
        e = round(min(prog, s + 2.6), 2)
        if e - s >= 1.2 and _matte_ready(src, tl, s, e):
            lid, em = _uid("lt", used), _uid("em", used)
            ops = [{"action": "upsert", "layer": "fx", "id": lid, "value": {
                "id": lid, "type": "light", "stage": "world", "start": s,
                "end": e, "kind": "dissolve",
                "duration_s": round(e - s, 2), "device": "light_dissolve"}},
                {"action": "upsert", "layer": "motion", "id": em, "value": {
                    "id": em, "template": "el_particles", "start": s,
                    "end": e, "params": {"kind": "embers", "emit": "edge",
                                         "count": 420},
                    "composite": {"blend": "screen"}}}]
            take(_cand("light_dissolve", "matte_fx", "add_element", ops, s,
                       last.get("bind"),
                       "the last line ends and the speaker becomes light — an "
                       "ending, once", "41@≈25–32"), s)
    return out[:max_n]
