"""Format packs (Wave 2, track 2F; D from Wave 3, 3F): genres A, B, C, D and H
as data.

A pack is a STRUCTURE (story beats, each with the device cards that can
carry it), a palette (card id -> budget per short), the type voices it
draws from, its punctuation and junction families, its colour world (B&W
only as a device: every pack's ``never`` holds ``whole_short_mono``) and
footage quotas. Packs are a starting point for invention, not a template:
the story planner seeds its picks by the story, so two shorts in one pack
never receive the same plan.

``select`` follows grammar.py's classify-or-abstain rule, and the user's
words win: a pack the user names (or describes) beats any measurement; no
words and no confident grammar family is no pack.
"""

import json
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
IDS = ("A", "B", "C", "D", "H")
REQUIRED = ("id", "name", "structure", "palette", "voices", "punctuation",
            "junctions", "colour_world", "footage_quotas")
_CACHE = {}


def load(pack_id):
    pid = str(pack_id).upper()
    if pid not in IDS:
        raise KeyError(f"unknown format pack {pack_id!r}; packs: {IDS}")
    if pid not in _CACHE:
        with open(os.path.join(_DIR, f"{pid}.json"), encoding="utf-8") as f:
            _CACHE[pid] = json.load(f)
    return _CACHE[pid]


def all_packs():
    return [load(p) for p in IDS]


def validate(pack, cards=None, voices=None):
    """[problems] of one pack: required fields, every device a real card,
    every voice a real voice, B&W never a whole-short look."""
    out = [f"missing {k}" for k in REQUIRED if k not in pack]
    if cards is not None:
        for beat in pack.get("structure") or []:
            for d in beat.get("devices") or []:
                if d not in cards:
                    out.append(f"beat {beat.get('beat')}: no card {d}")
        for d in pack.get("palette") or {}:
            if d not in cards:
                out.append(f"palette: no card {d}")
    if voices is not None:
        out += [f"no voice {v}" for v in pack.get("voices") or []
                if v not in voices]
    if "whole_short_mono" not in ((pack.get("colour_world") or {}).get(
            "never") or []):
        out.append("colour_world.never must hold whole_short_mono")
    return out


def _asked(text):
    t = str(text or "").lower()
    for p in all_packs():
        if re.search(r"\b(pack|format|genre)\s*" + p["id"].lower() + r"\b",
                     t) or p["name"].lower() in t:
            return p
    best = None
    for p in all_packs():
        n = sum(1 for a in p.get("asks") or [] if a in t)
        if n and (best is None or n > best[0]):
            best = (n, p)
    return best[1] if best else None


def select(index=None, edl=None, user_text="", shorts_child=False):
    """(pack | None, reason). The user's words win; else a confident
    grammar family maps to a pack; else abstain."""
    p = _asked(user_text)
    if p is not None:
        return p, f"the user asked for it ({p['name']})"
    if not index:
        return None, "no words naming a format and nothing measured"
    try:
        import grammar
        fam, why = grammar.classify(index, edl, shorts_child=shorts_child)
    except Exception as e:  # noqa: BLE001
        return None, f"no confident family ({type(e).__name__})"
    if not fam:
        return None, f"abstained: {why}"
    for p in all_packs():
        if fam in (p.get("grammars") or []):
            return p, f"{fam}: {why}"
    return None, f"{fam} has no pack (the agent composes freely)"
