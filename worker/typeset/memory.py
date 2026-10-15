"""Callback memory: a repeated line reuses (or mirrors) its geometry.

ROADMAP 2A "callback memory" and AT-2A-8: when a sentence comes back (the
rowing joke told twice; 29's TODAY lockups repeated), the second telling
should RHYME with the first — the same family, place and scale — rather
than be solved fresh. The first item's solve reports a ``hint`` (canvas
fractions: family, region, hero cap, options); a callback item carries it
as its ``hint`` param, the solver tries it first and keeps it when it still
passes every constraint on this frame (else it solves normally and says
so). ``mirror`` reflects it left-right (29's mirrored lockups).

The hint is intent-level (fractions of the canvas, a family name), so a
re-solve for another aspect simply tries it and falls back.
"""

import json

HINT_MAX = 300


def encode(hint):
    blob = json.dumps(hint, separators=(",", ":"), sort_keys=True)
    return blob if len(blob) <= HINT_MAX else ""


def decode(text):
    try:
        h = json.loads(text) if text else None
    except (TypeError, ValueError):
        return None
    if not isinstance(h, dict) or "family" not in h:
        return None
    return h


def mirrored(hint):
    h = dict(hint)
    r = h.get("region")
    if r and len(r) == 4:
        h["region"] = [round(1.0 - r[2], 4), r[1], round(1.0 - r[0], 4), r[3]]
    o = dict(h.get("opts") or {})
    if o.get("align") in ("left", "right"):
        o["align"] = "right" if o["align"] == "left" else "left"
    if o.get("start") in ("left", "right"):
        o["start"] = "right" if o["start"] == "left" else "left"
    if "angle" in o:
        o["angle"] = -float(o["angle"])
    h["opts"] = o
    return h


def callback_params(params, first_doc, mirror=False):
    """``params`` for a repeat of the line ``first_doc`` was solved for:
    the first telling's voice and its geometry hint (mirrored on ask)."""
    rep = (first_doc or {}).get("solver_report") or {}
    hint = rep.get("hint")
    out = dict(params)
    if first_doc and first_doc.get("voice"):
        out["voice"] = first_doc["voice"]
    if hint:
        out["hint"] = encode(mirrored(hint) if mirror else hint)
    return out


def prefer_of(params):
    return decode((params or {}).get("hint"))
