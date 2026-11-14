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

Only the approved library is ever read (sound_library/manifest.json). A recording that joined by reference match
(sound_approval, owner decision 13 Nov 2026) carries its shape and its reference class in its manifest row; any
other is described from its samples the first time it is seen, so the choice widens with the library and no
table here names a file. Nothing reads a title or a tag.

SND2 (14 Nov): the library now holds several recordings of each class, in several lengths, so the choice has
two more parts. The moment's weight picks the length inside the class (a light landing gets the class's short
quartile, the hook, the turn and the payoff its long one). And a recording that means something (paper, a camera
shutter, a glitch, a braam: its manifest `family`) answers only the picture that shows it: paper for a page or a
document landing, a shutter for a still photograph, a glitch for a glitched flash, a braam for the film's
structural landings. A drone is never a hit: the Director's Score names it.
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


FAMILIES = ("paper", "shutter", "glitch", "braam", "drone", "type")


def describe_library(lib, sounds_dir, pcm):
    """{id: description} for the recordings that may answer a picture event. A recording approved by reference match
    carries its own description (manifest `shape`, `reference.class`, `family`); known shas of the first library
    come from the measured table; anything else is read from its samples once per process."""
    out = {}
    for sid, x in lib.items():
        ref = x.get("reference") if isinstance(x.get("reference"), dict) else None
        if x.get("align") == "start" or (not ref and x.get("role") not in GENERAL):
            continue
        if x.get("family") == "drone":
            continue
        key = (sid, x.get("sha"))
        if key not in _desc:
            d = None
            if ref and isinstance(x.get("shape"), dict):
                try:
                    d = dict(x["shape"], kind=ref["class"])
                    SS.vector(d)
                except Exception:  # noqa: BLE001 - a row that cannot be read falls back to its samples
                    d = None
            if not d:
                d = (SP.LIBRARY.get(sid) or {}) if (SP.LIBRARY.get(sid) or {}).get("sha") == x.get("sha") else None
            if not d:
                try:
                    a = pcm(os.path.join(sounds_dir, x["file"]), SS.SR, 2)
                    d = SS.describe(a.mean(axis=1)) if len(a) else None
                except Exception:  # noqa: BLE001 - a recording that cannot be read is not chosen
                    d = None
            if d:
                d = dict(d, family=x.get("family") if x.get("family") in FAMILIES else None, matched=bool(ref))
            _desc[key] = d
        if _desc[key]:
            out[sid] = _desc[key]
    return out


def fits_of(ev, part):
    """The families of meaning this moment's picture can carry: what is on screen, never what anyone called it."""
    k = ev.get("kind")
    f = set()
    if k == "page" or (k == "card" and not ev.get("photo")):
        f.add("paper")
    if (k == "picture" and ev.get("still")) or (k == "card" and ev.get("photo")):
        f |= {"shutter", "paper"}
    if k == "flash" and ev.get("glitch"):
        f.add("glitch")
    if part in ("hook", "payoff") or ev.get("turn"):
        f.add("braam")
    if k in ("display", "counter"):
        f.add("type")
    return f


def _target(cls, weight):
    """The class's reference shape at the length this moment's weight asks for: its short quartile for a light
    landing, its median, its long quartile for the film's heavy moments."""
    prof = SP.CLASSES[cls]
    ref = np.asarray(prof["vec"], np.float32).copy()
    q = prof.get("dur_s")
    if weight is None or not q or len(q) != 3 or q[1] <= 0:
        return ref
    want = q[0] if weight <= 0.4 else q[2] if weight >= 0.75 else q[1]
    shift = float(np.log2(max(want, 0.02) / max(q[1], 0.02)))
    ref[0] += shift
    ref[2 if cls in ("impact", "hit", "click") else 1] += shift      # a longer hit rings longer; a longer sweep climbs longer
    return ref


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
    if k == "cut" and ev.get("designed"):
        return min(0.85, 0.35 + 0.5 * float(ev.get("change") or 0.0))
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


def nearest(cls, desc, spoken, free, exclude=(), want_s=None, used=None, weight=None, fits=None):
    """The approved recording nearest the class's reference shape. spoken: a word sits on the moment, so energy in
    the speech band counts against a recording. free(sid) says whether the one-recording rules still allow it.
    want_s: the climb the moment has room for (a riser no longer than the room). used: {id: times heard so far}.
    weight: the moment's weight (the length inside the class). fits: the families the moment's picture carries."""
    prof = SP.CLASSES.get(cls)
    if not prof:
        return None
    ref = _target(cls, weight)
    scale = np.asarray(SP.SCALE, np.float32)
    fits = fits or ()
    rows = []
    for sid, d in desc.items():
        if sid in exclude or not free(sid):
            continue
        fam = d.get("family")
        if fam and fam not in fits:
            continue                                  # a sound that means something answers only the picture that shows it
        dist = SS.distance(SS.vector(d), ref, scale)
        if d["kind"] != cls:
            dist += 0.6                               # another class by shape: only when the class has nothing left
        if fam:
            dist -= 0.12                              # the picture shows what this recording is
        if not d.get("matched"):
            dist += 0.1                               # between two near recordings, the one the references vouch for
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
    fits = fits_of(ev, part)
    if m == "counter":
        sid = nearest("click", desc, state["spoken"], state["free"], used=state.get("used"), weight=0.2, fits=fits)
        return ([(sid, "hit")] if sid else None), "click"
    cls, lead = class_for(m, w, room, state["tally"], state.get("spoken"))
    if ev.get("turn") and "impact" in SP.CLASSES and _room_ok("impact", room):
        # the story's turn, named by the caller, is the film's heaviest landing: the hit with its climb, whatever
        # the film has used before it (a hook that took the impact left the turn a sweep nobody heard)
        cls, lead = "impact", ("riser" if "riser" in SP.CLASSES and room >= SP.CLASSES["riser"]["lead_s"] else None)
    sid = nearest(cls, desc, state["spoken"], state["free"], used=state.get("used"), weight=w, fits=fits)
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
