"""Mechanical enforcement of explicit per-turn preservation instructions.

The creative agent is intentionally free to make broad editorial decisions.
That freedom stops where the user clearly says that an existing lane must not
change.  Prompt reminders are not sufficient for that boundary: a long tool
loop can forget the first sentence of a brief while every individual write
still looks locally reasonable.

This module is deliberately narrow.  It recognizes only high-confidence
phrases such as ``do not change the music`` or ``preserve the current text
overlays``.  Ordinary creative language (``keep it energetic``, ``nice
captions``) creates no constraint.  When a structural edit changes the program
clock, timeline coordinates may remap while the protected creative identity
and settings remain fixed.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Set


LANE_LABELS = {
    "music": "music selection and mix",
    "sfx": "sound effects",
    "voiceover": "voiceover",
    "audio_mix": "audio mix",
    "captions": "captions",
    "texts": "designed text overlays",
    "effects": "visual effects",
    "grade": "color grade",
    "cuts": "picture cuts",
}


# Targets are intentionally concrete EDL concepts.  In particular, generic
# words such as "style", "look", "everything", and "video" are excluded:
# interpreting those as immutable would turn a taste request into a hard cap.
_TARGETS = {
    "music": r"(?:music|soundtrack|music\s+(?:bed|track|choice)|song)",
    "sfx": r"(?:sfx|sound\s+effects?|sound\s+design)",
    "voiceover": r"(?:voice[ -]?over|narration)",
    "audio_mix": r"(?:audio(?:\s+mix)?|sound\s+mix|mixing)",
    "captions": r"(?:captions?|subtitles?)",
    "texts": r"(?:designed\s+text|text\s+overlays?|graphic\s+text)",
    "effects": r"(?:visual\s+effects?|video\s+effects?|effects)",
    "grade": r"(?:colou?r\s+grad(?:e|ing)|colou?r\s+treatment)",
    "cuts": r"(?:picture\s+cuts?|existing\s+cuts?|current\s+cuts?|cuts?)",
}

_DET = (r"(?:(?:all|any|the|my|our|its|this|that|current|existing|"
        r"approved|chosen|selected|original|present)\s+)*")


def _normalise_message(message: str) -> str:
    text = str(message or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\b(?:don't|dont)\b", "do not", text)
    text = re.sub(r"\b(?:can't|cant)\b", "cannot", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def protected_lanes(message: str) -> Set[str]:
    """Return lanes the user explicitly said must stay unchanged.

    The grammar favors false negatives over false positives.  ``Do not add
    or modify text overlays`` is treated as a freeze; ``do not overlay large
    text on the homepage`` is not, because ``overlay`` is an editing action,
    not an instruction to preserve the existing text lane.
    """
    text = _normalise_message(message)
    if not text:
        return set()

    lanes: Set[str] = set()
    for lane, target in _TARGETS.items():
        patterns = (
            # do not change/touch X; do not add or modify X
            rf"\b(?:do\s+not|never)\s+(?:(?:add|remove)\s+"
            rf"(?:or|and)\s+)?(?:change|modify|alter|touch)\s+{_DET}"
            rf"{target}\b",
            # leave X alone / unchanged / as-is
            rf"\bleave\s+{_DET}{target}\s+(?:alone|unchanged|as[ -]?is)\b",
            # preserve/retain X; "keep" requires evidence that this is the
            # existing choice, rather than a vague request to include X.
            rf"\b(?:preserve|retain)\s+{_DET}{target}\b",
            rf"\bkeep\s+(?:(?:all|the|my|our|its)\s+)*"
            rf"(?:current|existing|approved|chosen|selected|original)\s+"
            rf"{target}\b",
            rf"\bkeep\s+{_DET}{target}\s+"
            rf"(?:exactly|unchanged|as[ -]?is)\b",
        )
        if any(re.search(pattern, text) for pattern in patterns):
            lanes.add(lane)
    return lanes


def _canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str)


def _program_structure(edl: Dict[str, Any]) -> str:
    """The fields that can legitimately move program-time annotations."""
    inserts = []
    for item in edl.get("inserts") or []:
        inserts.append({k: item.get(k) for k in (
            "id", "asset_key", "kind", "at_output_s", "duration_s",
            "source_start_s", "rate")})
    return _canon({"keep": edl.get("keep") or [], "inserts": inserts})


def _items(edl: Dict[str, Any], lane: str, timeline_changed: bool) -> Any:
    rows = copy.deepcopy(edl.get(lane) or [])
    if not timeline_changed:
        return rows
    time_keys = {
        "music": ("start", "end"),
        "sfx": ("at",),
        "voiceover": ("start_output_s",),
        "texts": ("start", "end"),
    }.get(lane, ())
    for row in rows:
        for key in time_keys:
            row.pop(key, None)
    return rows


def _caption_state(edl: Dict[str, Any], timeline_changed: bool) -> Any:
    state = {"captions": copy.deepcopy(edl.get("captions"))}
    mutes = copy.deepcopy(edl.get("caption_mutes") or [])
    # Caption definitions are source-timed and must stay exact. Anonymous
    # mute windows are program-timed, so a real cut may move them while their
    # count (the preservation intent) remains unchanged.
    state["caption_mutes"] = len(mutes) if timeline_changed else mutes
    return state


def _effects_state(edl: Dict[str, Any], timeline_changed: bool) -> Any:
    effects = copy.deepcopy(edl.get("effects"))
    if not effects or not timeline_changed:
        return effects
    for lane in ("zooms", "regions", "stylize", "custom"):
        for item in effects.get(lane) or []:
            item.pop("start", None)
            item.pop("end", None)
    for item in effects.get("frame_shifts") or []:
        item.pop("at", None)
    return effects


def _grade_state(edl: Dict[str, Any]) -> Any:
    effects = edl.get("effects") or {}
    return {"grade": effects.get("grade"),
            "grade_custom": effects.get("grade_custom")}


def _lane_state(edl: Dict[str, Any], lane: str,
                timeline_changed: bool) -> Any:
    if lane in {"music", "sfx", "voiceover", "texts"}:
        return _items(edl, lane, timeline_changed)
    if lane == "captions":
        return _caption_state(edl, timeline_changed)
    if lane == "effects":
        return _effects_state(edl, timeline_changed)
    if lane == "grade":
        return _grade_state(edl)
    if lane == "cuts":
        return edl.get("keep") or []
    if lane == "audio_mix":
        # Insert positions may move, but their mute state is part of the mix.
        insert_audio = [{"id": item.get("id"), "mute": item.get("mute")}
                        for item in edl.get("inserts") or []]
        return {
            "music": _items(edl, "music", timeline_changed),
            "sfx": _items(edl, "sfx", timeline_changed),
            "voiceover": _items(edl, "voiceover", timeline_changed),
            "volume": copy.deepcopy(edl.get("volume") or []),
            "master": copy.deepcopy(edl.get("master")),
            "stem_mix": copy.deepcopy(edl.get("stem_mix")),
            "insert_audio": insert_audio,
        }
    raise KeyError(lane)


def _audio_mix_preserved(previous: Dict[str, Any], proposed: Dict[str, Any],
                         timeline_changed: bool) -> bool:
    """Preserve authored sound without making picture deletion impossible.

    An inserted video's own audio is structurally attached to that picture.
    Removing the insert therefore removes its sound too; treating that as an
    audio-mix violation made unwanted tray clips undeletable. Surviving insert
    mute choices must remain exact, and a newly added insert may enter only
    muted while the user has frozen the mix.
    """
    before = _lane_state(previous, "audio_mix", timeline_changed)
    after = _lane_state(proposed, "audio_mix", timeline_changed)
    before_inserts = {str(row.get("id")): bool(row.get("mute"))
                      for row in before.pop("insert_audio", [])
                      if row.get("id") is not None}
    after_inserts = {str(row.get("id")): bool(row.get("mute"))
                     for row in after.pop("insert_audio", [])
                     if row.get("id") is not None}
    if _canon(before) != _canon(after):
        return False
    for insert_id in set(before_inserts) & set(after_inserts):
        if before_inserts[insert_id] != after_inserts[insert_id]:
            return False
    # Removing picture is allowed. Adding a new audible source is not.
    return all(after_inserts[insert_id]
               for insert_id in set(after_inserts) - set(before_inserts))


def preservation_violations(previous: Dict[str, Any],
                            proposed: Dict[str, Any],
                            user_message: str = "") -> List[str]:
    """Human labels for explicitly protected lanes changed by a proposal."""
    protected = protected_lanes(user_message)
    if not protected:
        return []
    timeline_changed = _program_structure(previous) != \
        _program_structure(proposed)
    violations = []
    for lane in sorted(protected):
        if lane == "audio_mix":
            if not _audio_mix_preserved(previous, proposed,
                                        timeline_changed):
                violations.append(LANE_LABELS[lane])
            continue
        before = _lane_state(previous, lane, timeline_changed)
        after = _lane_state(proposed, lane, timeline_changed)
        if _canon(before) != _canon(after):
            violations.append(LANE_LABELS[lane])
    return violations


def rejection_message(version: int, violations: Iterable[str]) -> str:
    labels = list(violations)
    return (
        f"REJECTED (EDL v{version} unchanged): this proposal changes "
        + ", ".join(labels)
        + ", which the user explicitly asked to preserve in this turn. "
          "Use a narrower edit that leaves those lanes intact. If changing "
          "one is genuinely necessary, explain the conflict and ask the user "
          "before doing it."
    )
