"""Format packs: every genre (A-J) as data, in ONE schema and ONE loader.

Wave 2 (2F) built A, B, C and H, Wave 3 (3F) D, Wave 4 (4E) E, F, G, I and
J; the engines release merged them here (``format_packs`` keeps Wave 4's
trend grammars and reads its packs through this loader).

A pack is a STRUCTURE (story beats, each with the device cards that can
carry it, an optional ``share`` of the runtime — a number or a [low,
high] range — and a ``note``), a palette
(card id -> budget per short), the type voices it draws from, its
punctuation and junction families, its colour world (B&W only as a device:
every pack's ``colour_world.never`` holds ``whole_short_mono``) and footage
quotas. Optional craft-brief fields (``intensity``, ``type_voice``,
``look``, ``captions``, ``budgets`` per ledger family, ``devices_prefer``,
``devices_avoid``, ``never``, ``aspect``, ``notes``) give the agent the
genre's brief; ``apply`` says what applying the pack writes without picks
(``proposals``, the default: the best few; ``brief``: the frame and the
brief, proposals listed for the agent to pick — genres whose devices need
the user's data or sourced footage). Packs are a starting point for
invention, not a template: the story planner seeds its picks by the story,
so two shorts in one pack never receive the same plan. A pack never picks
music, a zoom or a sound.

``select`` follows grammar.py's classify-or-abstain rule, and the user's
words win: a pack the user names (or describes) beats any measurement; no
words and no confident grammar family is no pack.
"""

import json
import os
import re

_DIR = os.path.dirname(os.path.abspath(__file__))
IDS = ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")
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


def applies_brief(pack):
    """True when applying the pack without picks writes its frame and
    returns its brief (proposals listed, none written)."""
    return (pack or {}).get("apply") == "brief"


def brief(pack):
    """The pack as a few lines for the agent (any pack: the craft-brief
    fields when it has them, else what its beats, voices and colour world
    say)."""
    p = pack if isinstance(pack, dict) else load(pack)
    beats = []
    for s in p.get("structure") or []:
        b = str(s.get("beat") or "")
        sh = s.get("share")
        if isinstance(sh, (list, tuple)) and len(sh) == 2:
            b += (f" ({int(round(float(sh[0]) * 100))}-"
                  f"{int(round(float(sh[1]) * 100))}%)")
        elif isinstance(sh, (int, float)):
            b += f" ({int(round(float(sh) * 100))}%)"
        if s.get("note"):
            b += f": {s['note']}"
        beats.append(b)
    voice = p.get("type_voice") or ", ".join(p.get("voices") or [])
    look = (p.get("look") or {}).get("grade") or \
        (p.get("colour_world") or {}).get("rule")
    prefer = p.get("devices_prefer") or list(p.get("palette") or {})
    never = list(p.get("never") or [])
    if not any("black-and-white" in n or "B&W" in n for n in never):
        never.append("a whole-short black-and-white look")
    head = f"FORMAT {p['id']} — {p['name']}"
    if p.get("intensity"):
        head += f" ({p['intensity']})"
    return (f"{head}. Structure: {' -> '.join(beats)}. Type voice: {voice}. "
            f"Look: {look}. Prefer: {', '.join(prefer)}. Never: "
            f"{'; '.join(never)}." + (f" {p['notes']}" if p.get("notes")
                                      else ""))


def budgets(pack_id):
    """{family: int | ("every_s", s) | ("per_s", x) | None} — the
    ``taste.DEVICE_BUDGETS`` shape from a pack's ``budgets``; zooms and
    sounds are never a target. None for an unknown pack."""
    try:
        p = load(pack_id)
    except KeyError:
        return None
    out = {"zoom": None, "sfx": None}
    try:
        import taste
        fams = set(taste.LEDGER_FAMILIES) | set(taste._CARD_LEDGER.values())
    except Exception:  # noqa: BLE001 — budgets stay advice
        fams = None
    for fam, spec in (p.get("budgets") or {}).items():
        # a pack's budgets may also carry policy (D: per-line rules, the
        # dissolve's cap); only ledger families become ledger budgets
        if (fams is not None and fam not in fams) or fam in ("zoom", "sfx") \
                or isinstance(spec, (bool, str, list)):
            continue
        if isinstance(spec, dict):
            if "every_s" in spec:
                out[fam] = ("every_s", float(spec["every_s"]))
            elif "per_s" in spec:
                out[fam] = ("per_s", float(spec["per_s"]))
        elif spec is None:
            out[fam] = None
        else:
            out[fam] = int(spec)
    for fam in ("punctuation", "matte", "depth"):
        out.setdefault(fam, None)
    return out


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
    for beat in pack.get("structure") or []:
        if not beat.get("beat") or not beat.get("at"):
            out.append(f"a beat without beat/at: {beat}")
    if pack.get("apply") not in (None, "proposals", "brief"):
        out.append(f"apply is proposals or brief, not {pack.get('apply')!r}")
    blob = json.dumps({k: pack.get(k) for k in ("palette", "structure",
                                                "devices_prefer")}).lower()
    for word in ("zoom", "sfx", "music"):
        if f'"{word}' in blob:
            out.append(f"a pack never prescribes {word}")
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
