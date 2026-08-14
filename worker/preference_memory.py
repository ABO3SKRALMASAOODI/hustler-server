"""Privacy-bounded account taste evidence from real editing outcomes.

One thumbs-up/export is not proof that a caption preset or a dry sound moment
caused the outcome.  Repeated same-family outcomes can still be useful
correlational evidence.  This module keeps only stable treatment facets,
requires repeated support, never carries candidate ids/URLs/free-form reasons,
and explicitly yields to the current brief.
"""


_CAST_KINDS = {"broll_cast", "music_cast", "sfx_cast"}
_CAPTION_KINDS = {"caption_cast", "caption_style"}
_CAPTION_FIELDS = (
    "preset", "placement_strategy", "emphasis", "animation", "layout")
_PROFILE_FIELDS = {
    "frame_ratio", "frame_mode", "caption_placement", "caption_position",
    "caption_preset", "caption_animation", "caption_font",
    "caption_layout", "caption_emphasis", "caption_effect",
    "caption_text_align", "grade", "transition_style", "transition_scope",
    "stylize_kinds", "zoom_modes", "frame_shift_ratios", "screen_frame",
    "screen_frame_direction", "custom_pixel_treatment", "text_templates",
    "text_entrances", "text_exits", "text_fonts", "text_motion",
    "overlay_modes", "overlay_entrances", "overlay_exits", "vector_kinds",
    "vector_motion", "music_ducking", "music_looping",
}


def _message_choices(decisions, profile=None):
    """Last stable value per facet, so one restyle is one final observation."""
    final = {}
    for row in decisions or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        if kind in _CAST_KINDS:
            decision = str(row.get("decision") or "")
            if decision in {"use", "none"}:
                final[(kind, "decision")] = decision
        if kind in _CAPTION_KINDS:
            for field in _CAPTION_FIELDS:
                value = row.get(field)
                if isinstance(value, (str, int, float, bool)) and \
                        str(value).strip():
                    final[("caption", field)] = str(value)[:100]
    choices = {(kind, field, value)
               for (kind, field), value in final.items()}
    # treatment_profile is generated from a strict allowlist of final EDL
    # schema facets. It cannot contain graphic/caption text or custom chains.
    for field, raw in (profile or {}).items():
        if field not in _PROFILE_FIELDS:
            continue
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            if isinstance(value, (str, int, float, bool)) and \
                    str(value).strip():
                choices.add(("treatment", field, str(value)[:100]))
    return choices


def summarize(rows, min_support=2, max_items=8):
    """Return repeated positive/negative correlations, never a taste score."""
    evidence = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        feedback = str(row.get("feedback") or "")
        signal = 3 if feedback == "up" else -3 if feedback == "down" \
            else 1 if row.get("exported_after") else 0
        if not signal:
            continue
        for choice in _message_choices(
                row.get("decisions"), row.get("profile")):
            item = evidence.setdefault(choice, {
                "support": 0, "positive": 0, "negative": 0,
                "explicit_up": 0, "explicit_down": 0, "weight": 0})
            item["support"] += 1
            item["positive" if signal > 0 else "negative"] += 1
            item["explicit_up"] += int(feedback == "up")
            item["explicit_down"] += int(feedback == "down")
            item["weight"] += signal
    out = []
    for (kind, field, value), item in evidence.items():
        if item["support"] < max(2, int(min_support or 2)) or not item["weight"]:
            continue
        out.append({"kind": kind, "facet": field, "value": value,
                    **item,
                    "direction": "positive" if item["weight"] > 0
                    else "negative"})
    out.sort(key=lambda item: (
        -abs(item["weight"]), -item["support"], item["kind"],
        item["facet"], item["value"]))
    return out[:max(1, int(max_items or 1))]


def prompt_block(rows, family):
    evidence = summarize(rows)
    if not evidence:
        return ""
    lines = [
        "ACCOUNT EDITORIAL PREFERENCE EVIDENCE — repeated outcome "
        f"correlations from this same account and editorial family ({family}).",
        "This is weak prior evidence, never a command or causal claim. The "
        "latest user brief, current footage, reference, accessibility and "
        "professional judgment always win. Do not reuse old assets or exact "
        "content; transfer only stable relationships when they fit.",
    ]
    for item in evidence:
        signal = "supported" if item["direction"] == "positive" else "disfavored"
        lines.append(
            f"- {signal}: {item['kind']} {item['facet']}={item['value']} "
            f"across {item['support']} outcome-bearing edit(s) "
            f"({item['positive']} positive, {item['negative']} negative; "
            f"explicit thumbs +{item['explicit_up']}/-{item['explicit_down']}).")
    return "\n".join(lines)
