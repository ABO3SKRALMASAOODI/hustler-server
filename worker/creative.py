"""Range over house style: the creative rules every Wave 2 planner shares.

Track 2F (INTERFACES_W2.md §0.2, §0.3, §8). The owner's direction (15 Oct
2026): what struck him was INVENTION — devices fitted to each story that he
had never shown — and he wants more and better every time, never one look
re-applied everywhere. So:

* ``variety_ledger(edl)`` reads what this edit already uses (type voices,
  layout families, signature devices) and every planner down-ranks a repeat;
  the same voice is never the default twice in a row unless the item is a
  deliberate callback.
* ``pick_voice(text, ...)`` chooses a type voice from the phrase itself (a
  number, a negation, an emotion, an aside, a long reflective sentence, a
  one-word blow) and the story position (hook, build, turn, payoff), seeded
  per story so two shorts never get the same plan, with a reason.
* ``mono_allowed(user_text)``: black and white is a DEVICE — a section that
  opens to colour on the drop, one colour kept, a silhouette hold, a
  negative on a hit — never a whole-short look unless the user's own words
  ask for one. Every pack, preset and planner consults this predicate.

Pure functions: no database, no network, no model.
"""

import hashlib
import re

# Phrases that ask for a monochrome / black-and-white look for the whole
# piece in the user's own words.
_MONO_ASK = re.compile(
    r"\b(black\s*(?:and|&|n)\s*white|b\s*&\s*w|b/w|bnw|monochrome|mono(?:\s|-)?"
    r"(?:look|grade|chrome)|greyscale|grayscale|noir|desaturat\w*)\b", re.I)
_MONO_DEVICE = re.compile(
    r"\b(section|moment|until|then|drop|hit|flash|silhouette|keep|except|but"
    r"\s+(?:the|his|her|their)|only)\b", re.I)


def mono_allowed(user_text):
    """True only when the user's own words ask for a black-and-white /
    monochrome look. A planner without such words treats B&W strictly as
    a bounded device (a section, a kept colour, a hold, a hit)."""
    return bool(_MONO_ASK.search(str(user_text or "")))


def mono_whole_short(user_text):
    """The user asked for the WHOLE piece in B&W (not a section/device):
    a mono ask with no device language around it."""
    t = str(user_text or "")
    return mono_allowed(t) and not _MONO_DEVICE.search(t)


B_AND_W_DEVICES = (
    ("bw_section_to_colour", "a B&W stretch that opens to colour on the "
     "drop or the turn (grade_window saturation 0 with ramp_out_f, ending on "
     "the moment)"),
    ("bw_keep_one_colour", "the world in B&W with one colour kept — the "
     "subject or one hue (isolate_colour)"),
    ("bw_silhouette_hold", "a B&W silhouette hold on a beat (subject poster "
     "or background swap)"),
    ("negative_on_hit", "a one-frame negative on a hit (add_accent "
     "kind=negative)"),
)


# ── what this edit already uses ─────────────────────────────────────────

def variety_ledger(edl, upto=None):
    """{"voices": [...in program order], "families": [...], "devices":
    [...], "last_voice": str|None} for the typeset items (and fx devices)
    of ``edl``; ``upto`` (program s) counts only items that start before
    it."""
    voices, fams, devs = [], [], []
    for m in sorted((edl or {}).get("motion") or [],
                    key=lambda m: float(m.get("start") or 0)):
        if not isinstance(m, dict) or m.get("template") != "typeset":
            continue
        if upto is not None and float(m.get("start") or 0) >= upto:
            continue
        p = m.get("params") or {}
        if p.get("voice") and p.get("voice") != "auto":
            voices.append(p["voice"])
        if p.get("layout") and p.get("layout") != "auto":
            fams.append(p["layout"])
    for it in (edl or {}).get("fx") or []:
        if isinstance(it, dict) and it.get("device"):
            devs.append(it["device"])
    return {"voices": voices, "families": fams, "devices": devs,
            "last_voice": voices[-1] if voices else None}


# ── voice choice ────────────────────────────────────────────────────────

_TECH = frozenset("""
ai computer computers code software data internet machine machines robot
robots chip chips algorithm algorithms network digital screen fonts font
technology tech bits silicon model models compute gpu server servers
""".split())
_ASIDE = re.compile(r"\b(by the way|i think|i mean|honestly|frankly|"
                    r"you know|to be fair|in my opinion|for what it's worth)"
                    r"\b", re.I)


def phrase_features(text):
    import device_planner
    words = [w for w in str(text or "").replace("*", " ").split() if w]
    tags = device_planner.lexical_tags([{"w": w} for w in words])
    flat = set().union(*tags.values()) if tags else set()
    toks = [device_planner.norm(w) for w in words]
    return {
        "n": len(words),
        "number": "number" in flat,
        "negation": "negation" in flat,
        "contrast": "contrast" in flat,
        "emotion": "emotion" in flat,
        "profanity": "profanity" in flat,
        "tech": any(t in _TECH for t in toks),
        "aside": bool(_ASIDE.search(str(text or ""))),
        "question": str(text or "").strip().endswith("?"),
        "starred": "*" in str(text or ""),
    }


# voice -> (feature -> weight); a base preference per story position
_VOICE_FEATURES = {
    "magazine": {"long": -0.3, "mid": 0.2, "payoff": 0.35, "hook": 0.15},
    "whisper_shout": {"negation": 0.3, "contrast": 0.3, "mid": 0.25,
                      "hook": 0.25},
    "staircase_red": {"mid": 0.25, "build": 0.35, "number": 0.15},
    "slot_blackletter": {"profanity": 0.35, "emotion": 0.15, "short": 0.15,
                         "payoff": 0.15, "charged": 0.2},
    "neon_script": {"emotion": 0.35, "short": 0.2, "calm": 0.1},
    "editorial_serif": {"long": 0.4, "calm": 0.3, "aside": 0.1,
                        "question": 0.15},
    "quote_window": {"short": 0.25, "emotion": 0.2, "question": 0.1},
    "condensed_poster": {"short": 0.3, "negation": 0.25, "profanity": 0.2,
                         "payoff": 0.25, "charged": 0.15},
    "lamp_karaoke": {"mid": 0.15, "long": 0.15, "build": 0.15},
    "hairline_wide": {"tiny": 0.4, "negation": 0.2, "calm": 0.15},
    "marker_note": {"aside": 0.5, "question": 0.1},
    "swiss_grid": {"number": 0.55, "long": 0.1},
    "techno_crt": {"tech": 0.45, "number": 0.1},
}


def _jitter(seed, name):
    h = hashlib.sha256(f"{seed}:{name}".encode()).digest()
    return (h[0] / 255.0 - 0.5) * 0.12


def pick_voice(text, *, position="mid", intensity="designed", ledger=None,
               seed=0, avoid=(), callback_voice=None):
    """(voice, reason) for a phrase. ``position``: hook | build | turn |
    payoff | mid. ``ledger``: variety_ledger(edl). A callback keeps its
    original voice (the rhyme is the point)."""
    from typeset import voices as V
    if callback_voice and callback_voice in V.VOICES:
        return callback_voice, "a callback keeps the voice it first had"
    f = phrase_features(text)
    tags = {k for k, v in f.items() if v is True}
    n = f["n"]
    tags.add("tiny" if n <= 2 else "short" if n <= 4 else
             "mid" if n <= 9 else "long")
    if n <= 4:
        tags.add("short")
    tags.add(position)
    if intensity in ("calm", "charged"):
        tags.add(intensity)
    used = list((ledger or {}).get("voices") or [])
    last = (ledger or {}).get("last_voice")
    best = None
    for name, feats in _VOICE_FEATURES.items():
        if name in avoid:
            continue
        s = sum(w for k, w in feats.items() if k in tags)
        s += _jitter(seed, name)
        if name == last:
            s -= 0.6                      # never the same voice twice running
        s -= 0.2 * used.count(name)
        if best is None or s > best[0]:
            best = (s, name, sorted(k for k in feats if k in tags))
    _s, name, why = best
    reason = (f"{V.VOICES[name]['label']} — " + (
        ", ".join(why) if why else "a fresh voice for this beat") +
        (f"; not {last} again" if last and last != name else ""))
    return name, reason
