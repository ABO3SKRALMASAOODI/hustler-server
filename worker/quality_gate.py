"""Non-negotiable edit-safety checks at the EDL commit boundary.

The renderer can faithfully execute a technically valid but visually absurd
EDL. Schema validation therefore is not a quality gate: a zoom aimed at
nothing and nine unrequested sound effects are both perfectly valid data.

This module deliberately checks the *delta*. Old projects may already carry
legacy edits which violate today's rules; removing or otherwise repairing
those edits must remain possible. Only newly introduced hazards block a
write. The checks here are objective invariants, not a second taste prompt:
subjective judgement remains in ``taste.py`` and the preview critic.
"""

from __future__ import annotations

from collections import Counter
import json
from typing import Any, Dict, Iterable, List


SFX_REQUEST_HINTS = (
    "sfx", "sound effect", "sound fx", "soundeffect", "whoosh", "swoosh",
    "impact", "boom", "riser", "sound design", "sounddesign",
    "add sound", "sounds on", "punchy sound", "hit sound", "click sound",
    "efecto de sonido", "efectos de sonido", "efek suara", "مؤثرات",
    "مؤثر صوتي",
)

SFX_MIN_SPACING_S = 0.35


def _items(edl: Dict[str, Any], lane: str) -> List[Dict[str, Any]]:
    if lane == "zooms":
        return list(((edl.get("effects") or {}).get("zooms") or []))
    return list(edl.get(lane) or [])


def _new_items(previous: Dict[str, Any], proposed: Dict[str, Any],
               lane: str) -> List[Dict[str, Any]]:
    """Items whose stable identity did not exist before this write.

    All agent placement tools mint stable ids. Looking only at new ids is
    intentional: a timing cut can remap every legacy effect, and that repair
    must not be rejected merely because an old effect lacks modern metadata.
    Very old EDLs can contain id-less items, however, so those use a multiset
    of their canonical payloads. An unchanged id-less legacy hazard is not
    falsely treated as new on every unrelated write, while adding or changing
    one still receives the modern checks.
    """
    def identity(item):
        if item.get("id") is not None:
            return ("id", str(item["id"]))
        return ("payload", json.dumps(item, sort_keys=True,
                                      separators=(",", ":"), default=str))

    remaining = Counter(identity(x) for x in _items(previous, lane))
    out = []
    for item in _items(proposed, lane):
        key = identity(item)
        if remaining[key] > 0:
            remaining[key] -= 1
        else:
            out.append(item)
    return out


def _sfx_requested(message: str) -> bool:
    ask = (message or "").casefold()
    return any(h.casefold() in ask for h in SFX_REQUEST_HINTS)


def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    try:
        return min(float(a["end"]), float(b["end"])) - max(
            float(a["start"]), float(b["start"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def blocking_findings(previous: Dict[str, Any], proposed: Dict[str, Any],
                      user_message: str = "") -> List[str]:
    """Return human/actionable reasons this EDL delta must not be committed."""
    findings: List[str] = []

    new_zooms = _new_items(previous, proposed, "zooms")
    all_zooms = _items(proposed, "zooms")
    for zoom in new_zooms:
        zid = zoom.get("id") or "new zoom"
        mode = zoom.get("mode") or "punch"
        if mode in ("follow", "path"):
            aimed = len(zoom.get("path") or []) >= 2
        else:
            # rect is audit metadata; the renderer is actually aimed by the
            # solved cx/cy pair. One coordinate is also not a target.
            aimed = (zoom.get("target_measured") is True or
                     (zoom.get("cx") is not None and
                      zoom.get("cy") is not None))
        if not aimed:
            findings.append(
                f"zoom {zid} has no measured visual target. A center default "
                "is not evidence that the subject is centered; inspect the "
                "exact frame and provide cx+cy or a tracked path/rect.")
        for other in all_zooms:
            if other is zoom or other.get("id") == zoom.get("id"):
                continue
            if _overlap(zoom, other) > 0.05:
                findings.append(
                    f"zoom {zid} overlaps zoom {other.get('id') or '?'}. "
                    "The renderer adds their magnification, producing an "
                    "unplanned stronger crop; keep one move for that moment.")
                break

    new_sfx = _new_items(previous, proposed, "sfx")
    if new_sfx and not _sfx_requested(user_message):
        ids = ", ".join(str(x.get("id") or "?") for x in new_sfx[:4])
        findings.append(
            f"sound effects {ids} were added without an explicit sound-design "
            "request. SFX are opt-in; do not invent them as decoration.")

    if new_sfx:
        all_sfx = sorted(_items(proposed, "sfx"),
                         key=lambda x: float(x.get("at") or 0.0))
        new_ids = {str(x.get("id")) for x in new_sfx}
        for a, b in zip(all_sfx, all_sfx[1:]):
            try:
                gap = float(b.get("at")) - float(a.get("at"))
            except (TypeError, ValueError):
                continue
            if gap < SFX_MIN_SPACING_S and (
                    str(a.get("id")) in new_ids or
                    str(b.get("id")) in new_ids):
                findings.append(
                    f"sound effects {a.get('id') or '?'} and "
                    f"{b.get('id') or '?'} are only {gap:.2f}s apart. They "
                    "will read as one muddy/accidental hit; keep one.")
                break

    new_texts = _new_items(previous, proposed, "texts")
    all_texts = _items(proposed, "texts")
    for item in new_texts:
        tid = item.get("id") or "new text"
        for other in all_texts:
            if other is item or other.get("id") == item.get("id"):
                continue
            if _overlap(item, other) <= 0.08:
                continue
            # A title + subtitle deliberately composed on one owned card is
            # one hierarchy, not two unrelated word layers. Ordinary footage
            # text gets the same narrow exception only when both exact
            # windows match; everything else is accidental stacking.
            templates = {str(item.get("template") or ""),
                         str(other.get("template") or "")}
            same_window = (abs(float(item.get("start") or 0.0)
                               - float(other.get("start") or 0.0)) <= 0.05
                           and abs(float(item.get("end") or 0.0)
                                   - float(other.get("end") or 0.0)) <= 0.05)
            same_anchor = (item.get("anchor_insert") is not None
                           and item.get("anchor_insert")
                           == other.get("anchor_insert"))
            if templates == {"title", "subtitle"} and \
                    (same_window or same_anchor):
                continue
            findings.append(
                f"text {tid} overlaps designed text "
                f"{other.get('id') or '?'}. Two independent word layers "
                "will compete or print over each other; use one hierarchy, "
                "separate their time windows, or remove the older item.")
            break

    return findings


def rejection_message(version: int, findings: Iterable[str]) -> str:
    rows = list(findings)
    return (f"REJECTED BY QUALITY GATE (EDL v{version} unchanged):\n- "
            + "\n- ".join(rows)
            + "\nFix the plan from measured visual/audio evidence; do not "
              "retry by merely filling a missing field with a guess.")
