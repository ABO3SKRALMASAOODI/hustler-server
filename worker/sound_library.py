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

Usage policy (enforced by guidance and review, not by this module): sound
only where something meaningful happens on screen — a designed graphic
landing, a real section change/B-roll entry, the payoff, or a real-world
action shown on screen — never on captions, sparse (about one sound every
4-5 s at most), mixed under the voice.
"""

import hashlib
import json
import os

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sound_library")
REF_PREFIX = "sound:"

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
    return (f"{REF_PREFIX}{r['id']} [{r['role']}, {r['duration_s']:g}s, suggested gain "
            f"{r['gain_db']} dB] — {r['use']} (real recording, CC0)")
