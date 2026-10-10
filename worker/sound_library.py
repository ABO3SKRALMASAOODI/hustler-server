"""Valmera's sound library: real recordings the owner approved by ear.

Every sound in worker/sound_library/ is a real CC0 recording (Freesound) that
passed the owner's listening audition; synthesized sounds were rejected and
must never be shipped. manifest.json carries each file's role, when to use
it, a recommended mix level under speech and the licence trail
(LICENSES.md).

Roles are what templates and editors ask for ("whoosh", "click", "impact");
a role can hold several approved recordings and ``pick`` chooses one
deterministically so a short does not repeat the same file mechanically.
Template sound cues written with the older role names are mapped through
ROLE_ALIASES; a cue whose role has no approved recording is skipped.

Usage policy (guidance, review and sfx_placement's advisory checks): sound
only where something meaningful happens on screen within ~50 ms of the hit —
a designed graphic landing, a real section change/B-roll entry, the payoff,
or a real-world action shown on screen — never on captions, never a bright
sound on a payoff word's onset, never a literal pun on the spoken word;
zero by default in a podcast short and at most 1-2 (about one every 4-5 s
at most elsewhere).

Levels: ``hit_lufs`` / ``hit_lufs_hp150`` are each recording's measured hit
loudness (sfx_mix.measure_recording); add_sfx and template cues set the gain
from the voice at the hit (sfx_mix). ``gain_db`` is the older absolute
suggestion under a ~-20 LUFS voice: the scale template cue gains were
authored on (sfx_mix.cue_gain keeps their offsets from it) and the gain an
unmeasured recording falls back to.

Timing: an editor (or template cue) names the moment a recording should
HIT; ``place`` turns that into the EDL's physical placement. ``peak_s`` is
the measured loudest moment (the loudest 10 ms), so a whoosh or impact
starts that much early (skipping into the file when the hit is too close to
0 s). ``hit_s`` overrides it where the ear hears the hit clearly earlier:
impact_1's boom reaches full level ~0.69 s in (where the judges heard it)
and only fluctuates to its loudest 10 ms at 0.755 s. A recording
with ``"align": "start"`` (typing) plays UNDER its action from its first
sound instead. ``max_s`` is the measured end of the audible tail (the
A-weighted envelope 25 dB under its peak, and no sooner than 0.12 s — the
renderer's stop fade — after it falls 20 dB under): the default play
length when the editor sets none, so a long boom does not ring under the
next line. Recordings whose tail already ends with the file (risers end on
their peak; typing runs to its last keystroke) carry none. The approved
audio files themselves are never altered.
"""

import hashlib
import json
import os
import re

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sound_library")
REF_PREFIX = "sound:"
# Project storage key of an uploaded library recording (see asset_key).
_KEY_RE = re.compile(r"^sfx/[^/]+/lib-([A-Za-z0-9_]+)-([0-9a-f]{10})\.flac$")

# Older/template role names -> library roles.
ROLE_ALIASES = {
    "whoosh_soft": "whoosh", "whoosh_hard": "whoosh", "swoosh_up": "whoosh",
    "whoosh": "whoosh", "swish_short": "swish", "swipe": "swish", "swish": "swish",
    "pop_soft": "pop", "pop_bright": "pop", "pop": "pop",
    "click_ui": "click", "click": "click", "tick": "tick",
    "kick": "impact", "impact_soft": "impact", "impact_hard": "impact",
    "sub_drop": "impact", "impact": "impact",
    "ding": "ding", "chime": "ding", "notification": "ding",
    "coin": "cash", "cash": "cash",
    "riser_short": "riser", "riser_long": "riser", "riser": "riser",
    "glitch": "glitch", "shutter": "shutter", "typing": "typing",
    "heartbeat": "heartbeat",
}

_CACHE = {}


def _load():
    path = os.path.join(LIB_DIR, "manifest.json")
    try:
        st = os.stat(path)
    except OSError:
        return []
    key = (path, st.st_mtime_ns)
    if key not in _CACHE:
        with open(path) as f:
            rows = json.load(f)
        _CACHE.clear()
        _CACHE[key] = [r for r in rows
                       if os.path.exists(os.path.join(LIB_DIR, r.get("file", "")))]
    return _CACHE[key]


def catalog(role=None):
    rows = _load()
    if role:
        role = ROLE_ALIASES.get(role, role)
        rows = [r for r in rows if r["role"] == role]
    return rows


def get(sound_id):
    """Exact whitelist lookup (never a prefix match)."""
    for r in _load():
        if r["id"] == sound_id:
            return r
    return None


def path(sound_id):
    r = get(sound_id)
    return os.path.join(LIB_DIR, r["file"]) if r else None


def peak_s(sound_id):
    """Seconds from the sound's start to its loudest moment (so a whoosh can
    PEAK on a cut instead of starting there)."""
    r = get(sound_id)
    return float(r.get("peak_s") or 0.0) if r else 0.0


def hit_s(sound_id):
    """Seconds from the file start to the moment that lands ON a requested
    time: the peak for a hit (whoosh, impact, pop) or its measured attack
    (``hit_s``), 0 for a recording that plays under its action from the
    first sound ("align": "start")."""
    r = get(sound_id)
    if not r or r.get("align") == "start":
        return 0.0
    return float(r.get("hit_s") or r.get("peak_s") or 0.0)


def max_s(sound_id):
    """Seconds of the file worth playing by default (its measured audible
    tail end), or None for the whole recording."""
    v = (get(sound_id) or {}).get("max_s")
    return float(v) if v else None


def place(sound_id, hit_at, offset_s=0.0, dur_s=None):
    """EDL placement that lands the recording's hit ON program time hit_at.

    ``at`` is where playback starts (the renderer's clock), hit_s - offset_s
    before the hit. When that would be before 0 s, playback starts at 0 and
    skips further into the file so the hit still lands on time. at and
    offset_s sit on the EDL's 10 ms grid, so the hit lands within 5 ms.
    dur_s (play length from the start) defaults to the measured tail cap.
    Returns {"at", "offset_s", "dur_s", "lead_s"}; offset_s/dur_s are None
    when the file plays from its start / to its natural end."""
    offset_s = max(0.0, float(offset_s or 0.0))
    hs = hit_s(sound_id)
    at = float(hit_at) - max(0.0, hs - offset_s)
    if at < 0:
        offset_s -= at
        at = 0.0
    at, offset_s = round(at, 2), round(offset_s, 2)
    if dur_s is None:
        cap = max_s(sound_id)
        if cap is not None and cap - offset_s >= 0.05:
            dur_s = cap - offset_s
    return {"at": at, "offset_s": offset_s or None,
            "dur_s": round(dur_s, 3) if dur_s else None,
            "lead_s": round(max(0.0, hs - offset_s), 3)}


def retime(item, hit, keep_length=False):
    """Move an EDL sfx dict (in place) so its hit lands on program time hit.

    A library recording is re-placed from its hit: the automatic skip into
    the file (a hit too close to 0 s) and the default tail cap follow the new
    position, while a deliberate offset_s, or the point in the file a
    deliberate dur_s stops at, is kept. keep_length (a mechanical re-anchor
    after a cut or an insert, not an edit of the sound) also leaves a sound
    with no length set playing to its end, as it did before the move. Any
    other sound simply starts at hit. Returns the seconds it starts before
    its hit."""
    hit = max(0.0, float(hit))
    sid = id_for_key(item.get("storage_key"))
    if not sid:
        item["at"] = round(hit, 2)
        return 0.0
    off = float(item.get("offset_s") or 0.0)
    # Only a hit too close to 0 s forces playback to 0 with a skip no further
    # than the hit; an offset past the hit (or on a later start) was chosen.
    base = 0.0 if float(item.get("at") or 0.0) <= 1e-3 and off <= hit_s(sid) + 1e-6 else off
    cap, dur = max_s(sid), item.get("dur_s")
    if dur is None:
        auto = not keep_length
    else:
        auto = cap is not None and abs(float(dur) - (cap - off)) < 2e-3
    pl = place(sid, hit, base, None if auto or dur is None else float(dur))
    if not auto:
        # a deliberate length stops at the same point in the file (the same
        # ring after the hit) however far the skip into it moved
        pl["dur_s"] = (None if dur is None else
                       round(max(0.05, off + float(dur) - (pl["offset_s"] or 0.0)), 3))
    item["at"] = pl["at"]
    for k in ("offset_s", "dur_s"):
        if pl[k]:
            item[k] = pl[k]
        else:
            item.pop(k, None)
    return pl["lead_s"]


def asset_key(project_id, sound_id):
    """The project storage key an approved recording is uploaded under."""
    return f"sfx/{project_id}/lib-{sound_id}-{get(sound_id)['sha'][:10]}.flac"


def id_for_key(storage_key):
    """The approved recording behind a project storage key, or None. The sha
    prefix must match, so an upload that happens to be named like one is
    never mistaken for the library recording."""
    m = _KEY_RE.match(str(storage_key or ""))
    r = get(m.group(1)) if m else None
    return r["id"] if r and r["sha"][:10] == m.group(2) else None


def hit_at(item):
    """Program time an EDL sfx item's hit lands (its ``at`` unless it is a
    library recording that starts early to peak on time)."""
    at = float(item.get("at") or 0.0)
    sid = id_for_key(item.get("storage_key"))
    if not sid:
        return at
    return round(at + max(0.0, hit_s(sid) - float(item.get("offset_s") or 0.0)), 3)


def roles():
    return sorted({r["role"] for r in _load()})


def pick(role, seed=""):
    """A deterministic approved recording for a role, or None."""
    rows = catalog(role)
    if not rows:
        return None
    h = int(hashlib.sha256(f"{role}:{seed}".encode()).hexdigest()[:8], 16)
    return rows[h % len(rows)]


def search(query, limit=6):
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in str(query).lower()).split()
             if len(w) > 2 and w not in {"the", "and", "for", "sound", "sfx", "effect", "with"}]
    syn = {"woosh": "whoosh", "swoosh": "whoosh", "transition": "whoosh", "boom": "impact",
           "hit": "impact", "thud": "impact", "bubble": "pop", "button": "click", "mouse": "click",
           "keyboard": "typing", "type": "typing", "typewriter": "typing", "bell": "ding",
           "notification": "ding", "chime": "ding", "money": "cash", "register": "cash",
           "camera": "shutter", "photo": "shutter", "build": "riser", "tension": "riser",
           "swell": "riser", "static": "glitch", "digital": "glitch", "heart": "heartbeat",
           "whip": "swish", "swipe": "swish", "clock": "tick", "counter": "tick"}
    words = [syn.get(w, w) for w in words]
    scored = []
    for r in _load():
        hay = set((r["role"] + " " + r["use"] + " " + r["title"]).lower().replace("/", " ").split())
        score = sum(3 if w == r["role"] else 1 for w in words if w == r["role"] or w in hay)
        if score:
            scored.append((score, r["id"], r))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [r for _s, _i, r in scored[:limit]]


def describe(r):
    if r.get("align") == "start":
        timing = "plays from its first sound"
    else:
        timing = f"hits {hit_s(r['id']):g}s in"
    if r.get("max_s"):
        timing += f", stops by default {float(r['max_s']):g}s in"
    if r.get("hit_lufs") is not None:
        # add_sfx sets the gain from the voice at the hit (sfx_mix); the
        # listing gives the recording's own level so the range is legible
        level = f"hit {float(r['hit_lufs']):g} LUFS, levelled against the voice"
    else:
        level = f"suggested gain {r['gain_db']} dB"
    return (f"{REF_PREFIX}{r['id']} [{r['role']}, {r['duration_s']:g}s, {timing}, {level}] "
            f"— {r['use']} (real recording, CC0)")
