"""Which recording answers which moment (sound engine lane, 13 Nov).

The references do not put one sound on every cut and they do not put the same sound on every cut either. Read
from their stems (lanes/S study, 132 films: the 41 owner picks and the 91 of the reference library), a placed sound
belongs to one of a few classes by its shape (a click, a hit, an impact, a whoosh, a riser), each class answers
some moments more than others, and each has a level against the voice. This module holds that as numbers
(score/sound_profiles.py) and chooses with them:

    1. the moment (the hook, the payoff, a hard cut to a new picture, a moving cut, a flash, a landing inside a
       shot, a line of display type, a counter) names the classes the references use there, with their shares;
    2. the class is the one this film has used least against those shares, among the classes the moment has room
       and weight for (a riser needs its climb free before the event, an impact needs a moment that carries it);
    3. the recording is the approved one nearest that class's reference shape on the signal ruler
       (sound_signal.distance), leaning away from the speech band when a word is spoken there, and never one the
       one-recording rules have used up.

Only the owner's approved library is ever read (sound_library/manifest.json). A recording that joins it after an
audition is described from its samples the first time it is seen, so the choice widens with the library and no
table here names a file. Nothing reads a title or a tag.
"""
import os

import numpy as np

from . import sound_signal as SS
from . import sound_profiles as SP

# roles that may answer any picture event; the others (a cash register, a ding, a heartbeat, a glitch, typing)
# mean something and are placed only where the Director's Score names them
GENERAL = ("whoosh", "swish", "impact", "riser", "shutter", "click", "tick", "pop", "hit")
HEAVY = 0.6                         # an impact needs a moment at least this heavy
_desc = {}


def describe_library(lib, sounds_dir, pcm):
    """{id: description} for the general recordings of the library. Known shas come from the measured table;
    anything else is read from its samples once per process."""
    out = {}
    for sid, x in lib.items():
        if x.get("role") not in GENERAL or x.get("align") == "start":
            continue
        key = (sid, x.get("sha"))
        if key not in _desc:
            d = (SP.LIBRARY.get(sid) or {}) if (SP.LIBRARY.get(sid) or {}).get("sha") == x.get("sha") else None
            if not d:
                try:
                    a = pcm(os.path.join(sounds_dir, x["file"]), SS.SR, 2)
                    d = SS.describe(a.mean(axis=1)) if len(a) else None
                except Exception:  # noqa: BLE001 - a recording that cannot be read is not chosen
                    d = None
            _desc[key] = d
        if _desc[key]:
            out[sid] = _desc[key]
    return out


def weight_of(ev, part):
    """How heavy the moment is, 0-1: what the picture does there, not what anyone called it."""
    if part in ("hook", "payoff"):
        return 1.0
    if ev.get("size") is not None:                  # a measured picture change (the share of the frame that moved)
        return float(min(1.0, 0.3 + 0.55 * float(ev["size"])))
    k = ev.get("kind")
    if k == "page":
        return 0.7
    if k == "picture":
        return 0.5 if ev.get("still") else 0.6
    if k == "card":
        return float(min(0.9, 0.35 + float(ev.get("area") or 0.1)))
    if k == "flash":
        return 0.55
    return {"display": 0.3, "counter": 0.2, "cut": 0.4}.get(k, 0.4)


def moment_of(ev, part):
    if part == "hook":
        return "hook"
    if part == "payoff":
        return "payoff"
    k = ev.get("kind")
    if k in ("picture", "cut"):
        return "moving_cut" if ev.get("moving") else "cut"
    if k == "flash":
        return "flash"
    if k in ("card", "page"):
        return "landing"
    if k == "display":
        return "type"
    return "counter" if k == "counter" else "cut"


def _room_ok(cls, room_s):
    need = SP.CLASSES.get(cls, {}).get("lead_s", 0.0)
    return room_s >= need


def class_for(moment, weight, room_s, tally, spoken=None):
    """The class this moment gets: the references' shares for the moment (leaning to what they place under a word
    or in a breath, when that is known: short sounds under words, sweeps in breaths), less what the film has
    already used, among the classes the moment can carry. Returns (class, lead class or None)."""
    shares = dict(SP.MOMENTS.get(moment) or SP.MOMENTS["cut"])
    if spoken is not None and moment not in ("hook", "payoff"):
        tbl = SP.MOMENTS.get("under_a_word" if spoken else "in_a_breath") or {}
        shares = {k: 0.65 * shares.get(k, 0.0) + 0.35 * tbl.get(k, 0.0) for k in set(shares) | set(tbl)}
    total = sum(tally.values()) + 1.0
    best, best_s = None, -1e9
    for cls, sh in shares.items():
        if cls == "riser" or cls not in SP.CLASSES:   # a riser is a lead into a hit, never the hit
            continue
        if cls == "impact" and weight < HEAVY:
            continue
        if cls in ("click",) and weight >= 0.85:      # the film's biggest moments are not ticked
            continue
        if not _room_ok(cls, room_s):
            continue
        s = sh - tally.get(cls, 0) / total
        if s > best_s:
            best, best_s = cls, s
    if best is None:
        best = "whoosh" if _room_ok("whoosh", room_s) else "click"
    lead = None
    riser_share = shares.get("riser", 0.0)
    if moment in ("hook", "payoff") or (weight >= 0.8 and riser_share >= 0.1):
        if room_s >= SP.CLASSES["riser"]["lead_s"] and best in ("impact", "hit"):
            lead = "riser"
    elif best in ("hit", "click") and moment == "landing" and weight >= 0.55 and room_s >= SP.CLASSES["whoosh"]["lead_s"] + 0.2:
        lead = "whoosh"                               # something flies in, then lands
    return best, lead


def nearest(cls, desc, spoken, free, exclude=(), want_s=None, used=None):
    """The approved recording nearest the class's reference shape. spoken: a word sits on the moment, so energy in
    the speech band counts against a recording. free(sid) says whether the one-recording rules still allow it.
    want_s: the climb the moment has room for (a riser no longer than the room). used: {id: times heard so far}."""
    prof = SP.CLASSES.get(cls)
    if not prof:
        return None
    ref = np.asarray(prof["vec"], np.float32)
    scale = np.asarray(SP.SCALE, np.float32)
    rows = []
    for sid, d in desc.items():
        if sid in exclude or not free(sid):
            continue
        dist = SS.distance(SS.vector(d), ref, scale)
        if d["kind"] != cls:
            dist += 0.6                               # another class by shape: only when the class has nothing left
        if spoken:                                    # bands 300-800, 800-2000, 2000-4000: the voice's own
            sp = sum(10 ** (d["bands"][i] / 10) for i in (2, 3, 4))
            dist += 0.5 * sp
        if want_s is not None and d["attack_s"] > want_s:
            dist += 2.0 * (d["attack_s"] - want_s)
        if used:
            dist += 0.3 * used.get(sid, 0)            # the film's ear: a recording heard before gives way to its neighbour
        rows.append((dist, sid))
    if not rows:
        return None
    rows.sort()
    return rows[0][1]


def layers(ev, part, state):
    """[(recording, 'hit' | 'lead')] for one moment, and the class it was given. state: {desc, tally, free, spoken, used}."""
    desc = state["desc"]
    if not desc or not SP.CLASSES:
        return None, None
    room = float(ev.get("room_s", 0.0))
    w = weight_of(ev, part)
    m = moment_of(ev, part)
    if m == "counter":
        sid = nearest("click", desc, state["spoken"], state["free"], used=state.get("used"))
        return ([(sid, "hit")] if sid else None), "click"
    cls, lead = class_for(m, w, room, state["tally"], state.get("spoken"))
    sid = nearest(cls, desc, state["spoken"], state["free"], used=state.get("used"))
    if sid is None:
        return None, cls
    out = []
    if lead:
        lid = nearest(lead, desc, False, state["free"], exclude=(sid,), want_s=max(0.2, room - 0.1), used=state.get("used"))
        if lid and desc[lid]["kind"] == lead:
            out.append((lid, "lead"))
    out.append((sid, "hit"))
    return out, cls


def breath_lift(cls):
    """dB a class comes up when it lands in a breath of the voice instead of under a word (the references' own
    difference, capped: in a breath a sound is heard, it does not need to shout)."""
    c = SP.CLASSES.get(cls) or {}
    return float(min(4.0, max(0.0, c.get("breath_lift_db", 0.0))))
