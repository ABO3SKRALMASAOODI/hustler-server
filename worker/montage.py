"""The montage planner: shots on the user's track (Wave 2, track 2D).

ROADMAP 2D. A beam-searched dynamic programme over beat slots:

* slots come from a PROGRAM-clock RhythmMap (a user's track moved onto the
  edit by rhythm_types.to_program, or the speech prosody grid): one mark per
  beat (the kick it carries when there is one), sections set the mode —
  ``strobe`` (the 1D strobe: 2-frame holds), ``pulse`` (a cut on every
  mark), ``hold`` (>= HOLD_MIN_S per shot) — and a mode switches exactly on
  the section line;
* every cut lands ``lead_f`` frames BEFORE its mark (1 by default: the eye
  takes the cut as the ear takes the hit; 0-2 allowed), written as the
  centisecond FLOOR of that frame's time so the renderer's ``gte(t, start)``
  opens on exactly that frame;
* no shot cluster repeats within NO_REPEAT cuts, scale alternates where the
  pool allows, and a long shot is walked through (each reuse starts further
  in: cuts inside long shots, never the same frames twice);
* call-and-response / density arcs: slots alternate type on black with
  B-roll on the hits (2A fills the type slots), or run words-on-black ->
  words-over-picture -> picture-only.

The plan is ONE apply_edit_batch (the verb builds it): full-frame b-roll
overlays (``fit`` cover; the program's audio keeps playing), still images as
``still_motion`` moving stills (defect #21), strobe fx items, black cards
(a one-colour strobe frame) under typeset items, and an ``arrange``
``montage_intent`` record so a song swap re-plans deterministically
(``on_swap``, registered on hooks ``music_swap`` by the verb module).

Never chooses music: slots come from a track the user supplied, or from
the speech.
"""

import hashlib
import math

NO_REPEAT = 6
LEAD_F = 1
LEAD_RANGE = (0, 2)
MODES = ("strobe", "pulse", "hold")
SECTION_MODE = {"intro": "hold", "verse": "pulse", "build": "pulse",
                "drop": "pulse", "break": "hold", "outro": "hold",
                "phrase": "pulse"}
HOLD_MIN_S = 1.5
OVERLAY_MIN_S = 0.2           # the EDL's shortest overlay
STROBE_HOLD_F = 2
BEAM = 12
STYLES = ("broll", "call_response", "arc")
MAX_OPS = 60


def frame(t, fps):
    return int(math.floor(float(t) * fps + 0.5 + 1e-9))


def ftime(k, fps):
    """The centisecond time that OPENS program frame ``k`` under
    ``gte(t, start)``: strictly between frame k-1's time and frame k's
    (a frame time that is itself a whole centisecond, as every 25 fps frame
    is, would hang on float rounding), so the renderer opens on exactly
    frame k for fps < 100."""
    if k <= 0:
        return 0.0
    return max(0.0, math.floor(k / float(fps) * 100.0 - 1e-6) / 100.0)


def _marks(rmap, t0, t1, on):
    """([(t, strength)], beat period, per-section fallback marks): the
    hits the cuts land on in [t0, t1). For a drum class the marks ARE its
    hits (a cut on every kick, never on a beat the kick skips), at least
    half a beat apart; ``fallback`` (the beats) serves a section with no
    such hit (a sub-less intro)."""
    beats = [b for b in rmap.get("beats") or [] if t0 - 1e-6 <= b < t1]
    onsets = rmap.get("onsets") or []
    if on in ("stress", "word", "phrase_end", "pause_end"):
        ms = [(o["t"], float(o.get("strength") or 0.5)) for o in onsets
              if o.get("cls") == on and t0 - 1e-6 <= o["t"] < t1]
        gaps = [b[0] - a[0] for a, b in zip(ms, ms[1:])]
        period = sorted(gaps)[len(gaps) // 2] if gaps else 0.6
        return ms, period, []
    if on == "downbeat":
        db = [b for b in rmap.get("downbeats") or [] if t0 - 1e-6 <= b < t1]
        gaps = [b - a for a, b in zip(db, db[1:])]
        return [(b, 1.0) for b in db], (sorted(gaps)[len(gaps) // 2]
                                        if gaps else 2.0), []
    if len(beats) < 2:
        return [], 0.5, []
    period = (beats[-1] - beats[0]) / max(1, len(beats) - 1)
    grid = [(b, 0.7) for b in beats]
    if on == "beat":
        return grid, period, []
    cls = on if on in ("kick", "snare", "hat") else "kick"
    gap = max(OVERLAY_MIN_S, period / 2.0)
    out = []
    for o in onsets:
        if o.get("cls") != cls or not t0 - 1e-6 <= o["t"] < t1:
            continue
        if out and o["t"] - out[-1][0] < gap:
            continue
        out.append((float(o["t"]), float(o.get("strength") or 0.5)))
    return out, period, grid


def slots(rmap_prog, t0, t1, *, fps=30.0, lead_f=LEAD_F, modes=None,
          on="kick", every=1):
    """[{start, end, mode, cut_f, onset, section}] contiguous slots over
    [t0, t1) of a PROGRAM-clock rhythm map. A slot opens ``lead_f`` frames
    before its mark; a section's mode starts on its own line."""
    m = dict(SECTION_MODE, **(modes or {}))
    marks, period, fallback = _marks(rmap_prog, t0, t1, on)
    if not marks and not fallback:
        return []
    secs = [s for s in rmap_prog.get("sections") or []
            if s["t1"] > t0 and s["t0"] < t1] or \
        [{"t0": t0, "t1": t1, "label": "verse"}]
    bounds = []                 # (t, mode, label, is_mark)
    for k, s in enumerate(secs):
        a, b = max(t0, s["t0"]), min(t1, s["t1"])
        if b - a < 1e-3:
            continue
        md = m.get(s.get("label"), "pulse")
        if md not in MODES:
            md = "pulse"
        # a mark within a quarter beat of the NEXT line opens that section
        tail = b - (period / 4 if b < t1 - 1e-6 else 1e-6)
        inside = [x for x in marks if a - period / 4 <= x[0] < tail]
        if not inside:
            inside = [x for x in fallback if a - period / 4 <= x[0] < tail]
        # the section opens on its line: a mark within a quarter beat of
        # it, else the line itself
        first = inside[0][0] if inside and abs(inside[0][0] - a) <= \
            period / 4 else a
        rest = [x for x in inside if x[0] > first + min(period / 4, 0.1)]
        if md == "strobe":
            pts = [first]
        elif md == "hold":
            pts = [first]
            for x in rest:
                if x[0] - pts[-1] >= HOLD_MIN_S - 1e-6:
                    pts.append(x[0])
            if len(pts) > 1 and b - pts[-1] < HOLD_MIN_S:
                pts.pop()       # the remainder joins the last hold
        else:
            pts = [first] + [x[0] for x in rest[every - 1::every]]
        for p in pts:
            bounds.append((p, md, s.get("label")))
    bounds.sort(key=lambda x: x[0])
    out = []
    for k, (p, md, lab) in enumerate(bounds):
        cut = max(0, frame(p, fps) - int(lead_f))
        nxt = (frame(bounds[k + 1][0], fps) - int(lead_f)
               if k + 1 < len(bounds) else frame(t1, fps))
        if nxt - cut < 1:
            continue
        out.append({"start": ftime(cut, fps),
                    "end": ftime(nxt, fps), "mode": md, "cut_f": cut,
                    "end_f": nxt, "onset": round(p, 4), "section": lab})
    # pulses shorter than an overlay can be join their neighbour
    merged = []
    for s in out:
        if merged and s["mode"] != "strobe" and merged[-1]["mode"] == \
                s["mode"] and s["end"] - s["start"] < OVERLAY_MIN_S:
            merged[-1]["end"], merged[-1]["end_f"] = s["end"], s["end_f"]
            continue
        merged.append(s)
    return merged


def _rank(seed, key):
    h = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return h[0] / 255.0


def assign(slot_list, pool, *, seed=0, no_repeat=NO_REPEAT, beam=BEAM):
    """Shots for slots by beam search (the DP's best paths): maximise shot
    score + scale alternation - reuse, with no cluster inside the last
    ``no_repeat - 1`` picks. Returns [(slot, shot, src_t)].

    ``pool``: [{id, asset_key, kind, t0, t1, cluster, scale, score}]
    (t0/t1: the usable window inside the shot, seconds)."""
    paths = [(0.0, [], {})]       # (score, picks, uses)
    for k, sl in enumerate(slot_list):
        need = sl["end"] - sl["start"]
        nxt = []
        for sc, picks, uses in paths:
            recent = [p["cluster"] for p in picks[-(no_repeat - 1):]] \
                if no_repeat > 1 else []
            last_scale = picks[-1].get("scale") if picks else None
            cands = []
            for sh in pool:
                span = float(sh["t1"]) - float(sh["t0"])
                if sh.get("kind") == "video" and span < need * 0.98:
                    continue
                v = float(sh.get("score", 0.5))
                v += 0.15 * _rank(seed, f"{k}:{sh['id']}")
                if last_scale and sh.get("scale") and \
                        sh["scale"] != last_scale:
                    v += 0.25
                v -= 0.2 * uses.get(sh["id"], 0)
                if sh.get("cluster") in recent:
                    v -= 10.0             # only when nothing else fits
                cands.append((v, sh))
            cands.sort(key=lambda c: -c[0])
            for v, sh in cands[:beam]:
                u = dict(uses)
                u[sh["id"]] = u.get(sh["id"], 0) + 1
                nxt.append((sc + v, picks + [sh], u))
        if not nxt:
            break
        nxt.sort(key=lambda p: -p[0])
        paths = nxt[:beam]
    best = paths[0][1] if paths else []
    out, used = [], {}
    for sl, sh in zip(slot_list, best):
        need = sl["end"] - sl["start"]
        n = used.get(sh["id"], 0)
        span = float(sh["t1"]) - float(sh["t0"])
        room = span - need
        off = ((n * (need + 0.37)) % room) if room > 0.05 else 0.0
        out.append((sl, sh, round(float(sh["t0"]) + off, 3)))
        used[sh["id"]] = n + 1
    return out


def roles(slot_list, style):
    """Per slot: 'broll' | 'type_black' | 'type_over' (density arcs and
    call-and-response)."""
    n = len(slot_list)
    if style == "call_response":
        return ["type_black" if k % 2 == 0 else "broll" for k in range(n)]
    if style == "arc":
        a, b = n // 3, (2 * n) // 3
        return ["type_black" if k < a else "type_over" if k < b else "broll"
                for k in range(n)]
    return ["broll"] * n


def repeats_within(assignments, n=NO_REPEAT):
    """Indices whose cluster repeats inside the last n-1 picks."""
    bad = []
    cl = [sh.get("cluster") for _sl, sh, _t in assignments]
    for i, c in enumerate(cl):
        if c is not None and c in cl[max(0, i - (n - 1)):i]:
            bad.append(i)
    return bad


def broll_value(sl, sh, src_t, oid, still_params=None):
    """The layer + value of one b-roll slot: a cover overlay for a clip, a
    moving still (``still_motion``) for an image."""
    dur = round(sl["end"] - sl["start"], 2)
    if sh.get("kind") == "image":
        p = {"image": sh["asset_key"], "path": "push_in", "amount": 0.08,
             "ease": "linear"}
        p.update(still_params or {})
        return "motion", {"id": oid, "template": "still_motion",
                          "start": sl["start"],
                          "end": round(sl["start"] + max(OVERLAY_MIN_S, dur),
                                       2),
                          "params": p, "layer": "below_captions",
                          "mute_captions": False,
                          "purpose": "montage b-roll (moving still)"}
    return "overlays", {"id": oid, "asset_key": sh["asset_key"],
                        "kind": "video", "start": sl["start"],
                        "duration_s": max(OVERLAY_MIN_S, dur),
                        "fit": "cover", "source_start_s": src_t}


def black_card(sl, oid, fps):
    """A black card over a type slot: one colour strobe frame held for the
    whole slot (pre_captions, under the words)."""
    n = max(1, int(sl.get("end_f", 0) - sl.get("cut_f", 0)))
    return {"id": oid, "type": "strobe", "stage": "pre_captions",
            "start": round(sl["cut_f"] / fps, 4) if "cut_f" in sl
            else sl["start"],
            "end": round(sl["end_f"] / fps, 4) if "end_f" in sl
            else sl["end"],
            "frames": [{"source": {"kind": "colour",
                                   "colour": "#000000"},
                        "hold_f": min(8, n)}],
            "fit": "cover"}


def strobe_item(run, oid, fps, shots):
    """One strobe over a strobe slot: every pool shot a 2-frame hold."""
    sl = run
    frames = []
    for sh in shots[:64]:
        if sh.get("kind") == "image":
            src = {"kind": "asset", "asset_key": sh["asset_key"]}
        else:
            src = {"kind": "asset", "asset_key": sh["asset_key"],
                   "offset_s": round(float(sh.get("t0") or 0.0), 3)}
        frames.append({"source": src, "hold_f": STROBE_HOLD_F})
    return {"id": oid, "type": "strobe", "stage": "pre_captions",
            "start": round(sl["cut_f"] / fps, 4),
            "end": round(sl["end_f"] / fps, 4), "frames": frames,
            "pattern": "random", "seed": 7, "fit": "cover",
            "device": "strobe_frames"}


def intent(prefix, music_id, params):
    return {"id": f"{prefix}_in", "type": "arrange", "stage": "music_bus",
            "kind": "montage_intent", "music_id": music_id,
            "params": dict(params, prefix=prefix)}


def owned(edl, prefix):
    """(layer, id) of every item a montage with this prefix wrote."""
    out = []
    for layer in ("overlays", "motion", "fx"):
        for it in edl.get(layer) or []:
            iid = str(it.get("id") or "")
            if iid.startswith(prefix + "_") or iid == prefix:
                out.append((layer, iid))
    return out


# ── the swap re-solver (hooks 'music_swap') ──────────────────────────────
# The verb module registers a resolver that can read the new track's map
# and the stored pool; this module stays pure.
_SWAP_RESOLVER = None


def set_swap_resolver(fn):
    global _SWAP_RESOLVER
    _SWAP_RESOLVER = fn


def on_swap(edl, old_music_id, new_music_id):
    """hooks 'music_swap': re-plan each montage recorded on the swapped
    track (same pool, seed, modes, lead) on the new one. Without a resolver
    (no track analysis on this lane) the montage stays and a note says
    plan_to_music re-plans it."""
    intents = [i for i in edl.get("fx") or [] if i.get("type") == "arrange"
               and i.get("kind") == "montage_intent"
               and i.get("music_id") in (old_music_id, new_music_id)]
    if not intents:
        return edl, []
    if _SWAP_RESOLVER is None:
        return edl, [f"montage {i['params'].get('prefix')} was cut to "
                     f"'{old_music_id}': plan_to_music re-plans it on the "
                     "new track" for i in intents]
    return _SWAP_RESOLVER(edl, intents, new_music_id)
