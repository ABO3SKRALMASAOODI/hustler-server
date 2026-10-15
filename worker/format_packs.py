"""Track 4E: format packs E, F, G, I, J and trend grammars (ROADMAP 4E;
INTERFACES_W4.md §5.3).

A pack (``formats/<code>.json``) is a genre's craft brief: structure,
device palette and budgets, type voice, look, captions, the never-rules and
the aspect. ``budgets(code)`` feeds ``taste.ledger`` (the format argument
accepts these codes; Wave 2 owns A-D and H). A pack never prescribes music,
a zoom or a sound.

A trend grammar (``formats/trends/<id>.json``) is STRUCTURE only: sections
in beats relative to the drop of the user's own track, the devices of each
section. ``recognise(rhythm_map, energy=None)`` places a grammar on the
USER's supplied audio (its beat grid and energy envelope); nothing about
any reference audio is stored.
"""

import json
import os

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formats")
CODES = ("E", "F", "G", "I", "J")
_CACHE = {}


def load(code):
    code = str(code or "").strip().upper()
    if code not in CODES:
        raise ValueError(f"format pack is one of {CODES} (Wave 4)")
    if code not in _CACHE:
        with open(os.path.join(DIR, f"{code}.json")) as f:
            _CACHE[code] = json.load(f)
    return _CACHE[code]


def budgets(code):
    """{family: int | ("every_s", s) | ("per_s", x) | None} — the
    ``taste.DEVICE_BUDGETS`` shape. None for an unknown code."""
    try:
        p = load(code)
    except ValueError:
        return None
    out = {"zoom": None, "sfx": None}      # optional, never a target
    for fam, spec in (p.get("budgets") or {}).items():
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


def brief(code):
    """The pack as a few lines for the agent."""
    p = load(code)
    st = " -> ".join(f"{s['section']} ({int(s['share'] * 100)}%): "
                     f"{s['what']}" for s in p.get("structure") or [])
    return (f"FORMAT {p['code']} — {p['name']} ({p['intensity']}). "
            f"Structure: {st}. Type voice: {p.get('type_voice')}. Look: "
            f"{(p.get('look') or {}).get('grade')}. Prefer: "
            f"{', '.join(p.get('devices_prefer') or [])}. Never: "
            f"{'; '.join(p.get('never') or [])}. {p.get('notes') or ''}")


# ── trend grammars ───────────────────────────────────────────────────────

def trends():
    d = os.path.join(DIR, "trends")
    out = {}
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if f.endswith(".json"):
            with open(os.path.join(d, f)) as fh:
                g = json.load(fh)
            out[g["id"]] = g
    return out


def find_drop(beats, energy, bin_s, *, rise_db=8.0, quiet_s=2.5):
    """(drop second, rise dB) — the beat where the track's energy rises
    most after a quieter stretch (the drop), or (None, 0)."""
    if not energy or not beats:
        return None, 0.0
    best, best_t = 0.0, None
    q = max(1, int(round(quiet_s / bin_s)))
    a = max(1, int(round(1.5 / bin_s)))
    for b in beats:
        k = int(round(b / bin_s))
        if k - q < 0 or k + a > len(energy):
            continue
        before = sorted(energy[k - q:k])[len(energy[k - q:k]) // 2]
        after = sum(energy[k:k + a]) / float(a)
        rise = after - before
        if rise > best:
            best, best_t = rise, b
    if best < rise_db:
        return None, best
    return best_t, best


def recognise(rhythm_map, energy=None, bin_s=0.5, grammar_id=None):
    """The best-fitting grammar placed on the user's track:
    {id, drop_s, beat_s, rise_db, sections [{name, t0, t1, devices}]} or
    None (abstain: the structure is not there)."""
    beats = [float(b) for b in (rhythm_map or {}).get("beats") or []]
    bpm = (rhythm_map or {}).get("bpm")
    if len(beats) < 8:
        return None
    period = (beats[-1] - beats[0]) / float(len(beats) - 1)
    best = None
    for gid, g in trends().items():
        if grammar_id and gid != grammar_id:
            continue
        req = g.get("requires") or {}
        lo, hi = req.get("bpm_range") or [0, 999]
        tempo = float(bpm) if bpm else 60.0 / period
        if not (lo <= tempo <= hi or lo <= tempo * 2 <= hi
                or lo <= tempo / 2 <= hi):
            continue
        drops = list((rhythm_map or {}).get("drops") or [])
        t, rise = (drops[0], 99.0) if drops else find_drop(
            beats, energy or [], bin_s, rise_db=float(req.get("rise_db", 8)),
            quiet_s=float(req.get("quiet_s", 2.5)))
        if t is None:
            continue
        # snap the drop to the beat grid
        t = min(beats, key=lambda b: abs(b - t))
        secs = [{"name": s["name"], "t0": round(t + s["from_beat"] * period,
                                                3),
                 "t1": round(t + s["to_beat"] * period, 3),
                 "devices": s.get("devices") or []}
                for s in g.get("sections") or []]
        cand = {"id": gid, "drop_s": round(t, 3),
                "beat_s": round(period, 4), "rise_db": round(rise, 2),
                "sections": secs}
        if best is None or rise > best["rise_db"]:
            best = cand
    return best
