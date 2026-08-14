"""Independent caption-treatment casting over the live renderer catalog.

The fallback caption director historically mapped brief keywords straight to
one preset.  That is deterministic, but it is not comparative judgment and it
ages badly as new renderer looks ship.  This module derives its slate directly
from ``captions.PRESETS`` and asks one stateless senior type director to choose
against measured program evidence and the durable creative contract.

The call can fail or abstain without withholding captions; ``agent_tools``
keeps its deterministic source-grounded fallback.  No renderer feature is
disabled and there is no hand-maintained candidate cap.
"""

import json
import math
import re

import captions
import llm


_FIELDS = (
    "font", "mode", "align", "uppercase", "max_words", "wpl",
    "position", "layout", "word_anim", "animation", "effect", "emphasis",
    "highlight", "background_color", "background_opacity", "tracking",
)
_POSITIONS = {"bottom", "middle", "top"}


def catalog():
    """The complete current caption slate, generated from renderer truth."""
    rows = [{"preset": "classic", "font": "bundled sans",
             "mode": "static", "align": "center", "uppercase": False,
             "position": "bottom", "layout": "flow",
             "note": "legacy plain subtitle; maximum restraint"}]
    for name, spec in captions.PRESETS.items():
        row = {"preset": name}
        for key in _FIELDS:
            value = spec.get(key)
            if value is not None:
                row[key] = value
        treatments = spec.get("treatments")
        if treatments:
            row["available_emphasis"] = list(treatments)
        rows.append(row)
    return rows


def _json_object(text):
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_choice(answer, allowed=None):
    """Validate a compact choice; invented renderer controls are discarded."""
    raw = _json_object(answer)
    if raw is None:
        return None
    allowed = set(allowed or [row["preset"] for row in catalog()])
    preset = str(raw.get("preset") or "").strip()
    reason = " ".join(str(raw.get("reason") or "").split())
    if preset not in allowed or not reason:
        return None
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    confidence = round(min(max(confidence, 0.0), 1.0), 3)

    style = {"preset": preset}
    emphasis = str(raw.get("emphasis") or "").strip()
    if preset != "classic" and emphasis in captions.TREATMENTS:
        style["emphasis"] = emphasis
    position = str(raw.get("position") or "").strip().lower()
    if position in _POSITIONS:
        style["position"] = position
    color = str(raw.get("highlight_color") or "").strip().upper()
    if re.fullmatch(r"#[0-9A-F]{6}", color):
        style["highlight_color"] = color
    rejected = raw.get("rejected") or []
    if not isinstance(rejected, list):
        rejected = []
    return {
        "preset": preset,
        "style": style,
        "confidence": confidence,
        "reason": reason[:420],
        "rejected": [" ".join(str(value).split())[:180]
                     for value in rejected[:4] if str(value).strip()],
    }


def review(context, proof_paths=None, proof_labels=None):
    """Cast one treatment from every caption grammar currently rendered.

    When supplied, proof pages are real libass burns over one common program
    frame.  A failed vision call deliberately falls through to the complete
    metadata slate, so captions are never withheld and a partial image slate
    never biases the decision.
    """
    slate = catalog()
    system = """You are an independent senior motion-typography director.
Choose ONE caption treatment for a finished edit from the complete renderer
catalog supplied by the caller. Judge the speaker/content, reading pace,
platform/aspect, durable creative treatment, source visual conditions and
cross-department coherence. Do not reward novelty or motion density. Prefer
readability, semantic hierarchy, safe composition and a consistent grammar;
restraint can win. A preset name is only an id—use its supplied mechanics.
Do not invent a preset, font, animation or effect. `position` should be null
to preserve shot-aware adaptive placement unless the recorded treatment
specifically requires top, middle or bottom. Return JSON only:
{"preset":"exact catalog id","emphasis":"available treatment or null",
"position":"top|middle|bottom|null","highlight_color":"#RRGGBB|null",
"confidence":0.0,"reason":"evidence-bound choice",
"rejected":["materially plausible loser and why"]}"""
    evidence = {
        "program_evidence": context,
        "complete_renderer_catalog": slate,
    }
    allowed = [row["preset"] for row in slate]
    if proof_paths and llm.vision_available():
        visual_prompt = """You are an independent senior motion-typography
director. The attached labeled pages show EVERY live caption preset rendered
through the production ASS compiler, bundled fonts and production PlayRes on
the SAME real output-geometry frame and SAME transcript landing. Compare the
visible pixels: font character, scale, wrapping, hierarchy, contrast,
placement, collision risk and coherence with the supplied edit. Choose ONE
preset. Do not reward novelty or motion density; restraint can win. The still
captures one real animation state, so use catalog mechanics to reason about
motion without inventing controls. Do not choose a preset absent from the
pages and do not add style overrides that were not rendered. Return JSON only:
{"preset":"exact catalog id","confidence":0.0,
"reason":"specific visible and program evidence",
"rejected":["materially plausible loser and why"]}

PROGRAM AND COMPLETE CATALOG:
""" + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
        answer = llm.ask_vision(
            visual_prompt[:24000], list(proof_paths), max_tokens=720,
            purpose="caption_treatment_visual_cast",
            image_names=(list(proof_labels) if proof_labels else None),
            reasoning_effort="low")
        choice = parse_choice(answer, allowed) if answer else None
        if choice:
            choice["visual_proof"] = True
            choice["proof_page_count"] = len(proof_paths)
            return choice

    user = json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    result = llm.ask_text(
        system, user[:18000], max_tokens=420, temperature=0.15,
        purpose="caption_treatment_cast")
    if not result:
        return None
    choice = parse_choice(result.get("text"), allowed)
    if choice:
        choice["visual_proof"] = False
    return choice
