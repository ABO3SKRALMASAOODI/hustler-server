"""Durable creative direction for one Valmera project.

The EDL is the executable edit; a blueprint is the editorial contract that
explains *why* its cuts, typography, footage and sound should belong together.
Keeping that contract separate from the EDL lets later turns refine an edit
without inventing a new visual language, and lets the agent close a request
against semantic work rather than an arbitrary number of tool calls.

This module is intentionally pure.  Persistence lives in chat-message JSONB
metadata (see db.latest_creative_blueprint), so deploying it needs no schema
migration and old projects simply have no blueprint until their next edit.
"""

import re


BLUEPRINT_VERSION = 1

_SCALAR_LIMITS = {
    "brief": 240,
    "treatment": 180,
    "format": 80,
    "intent": 200,
    "audience": 160,
    "platform": 80,
    "objective": 200,
    "style_family": 120,
    "caption_direction": 240,
    "motion_direction": 240,
    "broll_direction": 280,
    "music_direction": 240,
    "sfx_direction": 240,
    "color_direction": 200,
}

_LIST_LIMITS = {
    "must_keep": (16, 140),
    "must_avoid": (16, 140),
    "narrative_arc": (12, 180),
    "decision_basis": (12, 180),
    "reference_transfer": (10, 180),
    "coherence_rules": (12, 180),
    "alternatives_rejected": (4, 180),
    "acceptance_criteria": (16, 180),
}

_SEQUENCE_LIMIT = 16
_SEQUENCE_TEXT_LIMITS = {
    "role": 48,
    "anchor": 180,
    "purpose": 180,
    "visual": 220,
    "sound": 180,
}

_SEQUENCE_ACCEPTANCE = (
    "the rendered sequence realizes each planned beat in order: its anchor "
    "and audience purpose remain clear, while picture, sound and relative "
    "energy form one intentional progression"
)


def _text(value, limit):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit] or None


def _texts(value, count, width):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("must be an array of short strings")
    out = []
    for row in value:
        clean = _text(row, width)
        if clean and clean not in out:
            out.append(clean)
        if len(out) >= count:
            break
    return out


def _sequence_map(value, strict=False):
    """Normalize the cross-modal treatment of each meaningful story beat.

    A global style bible cannot stop a sequence from becoming a collection of
    unrelated local choices.  These rows bind an exact transcript/scene/card
    anchor to its audience purpose, picture treatment, sound treatment and
    relative energy.  All fields remain descriptive rather than prescribing
    a tool or fixed edit density, so the same contract works for a restrained
    podcast, a graphic explainer or an aggressive sports reel.
    """
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        if strict:
            raise ValueError("sequence_map must be an array of beat objects")
        return []
    out = []
    for index, raw in enumerate(value[:_SEQUENCE_LIMIT], 1):
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(
                    f"sequence_map[{index}] must be an object")
            continue
        row = {
            key: _text(raw.get(key), limit)
            for key, limit in _SEQUENCE_TEXT_LIMITS.items()
        }
        if not row["anchor"] and not row["purpose"]:
            if strict:
                raise ValueError(
                    f"sequence_map[{index}] needs anchor and/or purpose")
            continue
        row["role"] = row["role"] or f"beat {index}"

        energy = raw.get("energy")
        if energy is not None:
            try:
                energy = round(float(energy), 3)
            except (TypeError, ValueError):
                if strict:
                    raise ValueError(
                        f"sequence_map[{index}].energy must be 0 to 1")
                energy = None
            if energy is not None and not 0.0 <= energy <= 1.0:
                if strict:
                    raise ValueError(
                        f"sequence_map[{index}].energy must be 0 to 1")
                energy = None
        row["energy"] = energy

        start, end = raw.get("source_start_s"), raw.get("source_end_s")
        if (start is None) != (end is None):
            if strict:
                raise ValueError(
                    f"sequence_map[{index}] source range needs both start "
                    "and end")
            start = end = None
        elif start is not None:
            try:
                start, end = round(float(start), 3), round(float(end), 3)
            except (TypeError, ValueError):
                if strict:
                    raise ValueError(
                        f"sequence_map[{index}] source range must be numeric")
                start = end = None
            if start is not None and (start < 0 or end <= start):
                if strict:
                    raise ValueError(
                        f"sequence_map[{index}] source range must increase")
                start = end = None
        row["source_start_s"], row["source_end_s"] = start, end
        evidence_ids = raw.get("evidence_ids")
        try:
            row["evidence_ids"] = _texts(evidence_ids, 8, 48)
        except ValueError:
            if strict:
                raise ValueError(
                    f"sequence_map[{index}].evidence_ids must be an array "
                    "of transcript/shot ids")
            row["evidence_ids"] = []
        out.append(row)
    return out


def source_evidence_violations(sequence_map, index):
    """Return timed beat bindings that are not supported by the real index.

    Source seconds alone are too easy for a language model to invent.  A
    substantial treatment therefore copies the sentence/shot ids it used from
    PROJECT STATE or ``get_editorial_map``.  The check is deliberately about
    provenance, not taste: it proves the proposed window is covered by the
    cited source records and leaves the editor free to choose any treatment.
    """
    evidence = {}
    for prefix, rows, start_key, end_key in (
            ("speech", (index or {}).get("sentences") or [], "t0", "t1"),
            ("shot", (index or {}).get("shots") or [], "start", "end")):
        for number, raw in enumerate(rows, 1):
            if not isinstance(raw, dict):
                continue
            evidence_id = str(raw.get("id") or f"{prefix}{number}")
            try:
                start = float(raw.get(start_key))
                end = float(raw.get(end_key))
            except (TypeError, ValueError):
                continue
            if end > start:
                evidence[evidence_id] = (start, end)

    violations = []
    for number, beat in enumerate(sequence_map or [], 1):
        start, end = beat.get("source_start_s"), beat.get("source_end_s")
        ids = [str(value) for value in beat.get("evidence_ids") or []]
        if start is None:
            unknown = [value for value in ids if value not in evidence]
            if unknown:
                violations.append(
                    f"sequence_map[{number}] cites unknown source id(s): "
                    + ", ".join(unknown))
            continue
        if not ids:
            violations.append(
                f"sequence_map[{number}] has source seconds but no "
                "evidence_ids; copy the sentence/shot ids that prove this "
                "beat from PROJECT STATE or get_editorial_map")
            continue
        unknown = [value for value in ids if value not in evidence]
        if unknown:
            violations.append(
                f"sequence_map[{number}] cites unknown source id(s): "
                + ", ".join(unknown))
            continue
        windows = [evidence[value] for value in ids]
        envelope_start = min(row[0] for row in windows)
        envelope_end = max(row[1] for row in windows)
        # A small allowance lets word-safe cut points include breath/handles
        # around the cited sentence without turning an unrelated timestamp
        # into "evidence".
        if float(start) < envelope_start - 0.75 or \
                float(end) > envelope_end + 0.75:
            violations.append(
                f"sequence_map[{number}] range {start}-{end}s falls outside "
                f"its cited source evidence ({envelope_start:.3f}-"
                f"{envelope_end:.3f}s)")
            continue
        if not any(row_end > float(start) and row_start < float(end)
                   for row_start, row_end in windows):
            violations.append(
                f"sequence_map[{number}] cited source ids do not overlap "
                "its timed beat")
    return violations


def _step_key(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _default_acceptance(raw):
    """Evidence gates derived from the direction—not a fixed style recipe."""
    criteria = [
        "the latest complete preview has no high-confidence major visual "
        "defect, and deterministic caption/audio checks are clear or a "
        "specific deliberate exception is justified; when designed audio "
        "and an actual-audio reviewer are available, no unresolved FIX "
        "verdict remains",
    ]
    objective = raw.get("objective") or raw.get("intent")
    if objective:
        criteria.append(
            "the opening, progression and ending visibly serve the recorded "
            f"objective: {str(objective)[:100]}")
    if raw.get("narrative_arc") or any(
            word in str(raw.get("format") or "").lower()
            for word in ("podcast", "interview", "talking", "story", "vlog")):
        criteria.append(
            "the selected speech/story is self-contained and coherent: setup, "
            "references, speaker turns and resolution survive the cut")
    if raw.get("caption_direction"):
        criteria.append(
            "rendered captions are accurate, readable, rhythmically phrased, "
            "inside platform-safe space and do not collide with subjects or "
            "source text")
    if raw.get("broll_direction"):
        criteria.append(
            "every selected cutaway has a named narrative purpose, its actual "
            "downloaded pixels/motion were reviewed, and it adds specific "
            "evidence rather than generic wallpaper")
    if raw.get("motion_direction"):
        criteria.append(
            "motion and transitions form one coherent grammar, land on "
            "meaningful events and leave enough stillness for emphasis")
    if raw.get("music_direction"):
        criteria.append(
            "the chosen music was compared from measured candidate evidence, "
            "supports the intended energy arc and leaves speech intelligible")
    if raw.get("sfx_direction"):
        criteria.append(
            "each sound effect matches a nameable visible/structural event, "
            "uses a measured suitable transient and belongs to one sound family")
    if raw.get("color_direction"):
        criteria.append(
            "color treatment is coherent across shots, preserves important "
            "skin/product color and matches the recorded direction")
    if raw.get("sequence_map"):
        criteria.append(_SEQUENCE_ACCEPTANCE)
    if raw.get("treatment") or raw.get("coherence_rules"):
        criteria.append(
            "the finished edit realizes the chosen treatment as one coherent "
            "editorial system; local picture, type, motion and sound choices "
            "follow its recorded rules rather than competing styles")
    if raw.get("reference_transfer"):
        criteria.append(
            "reference influence is visible through the recorded transferable "
            "relationships, without copying raw counts or inserting the "
            "reference media")
    return criteria[:_LIST_LIMITS["acceptance_criteria"][0]]


def normalize_blueprint(raw):
    """Return a bounded, forward-compatible blueprint or ``None``.

    Stored chat metadata is untrusted historical input.  Normalizing on load
    prevents a malformed/oversized old row from becoming prompt context.
    Unknown future fields are deliberately ignored by an older worker.
    """
    if not isinstance(raw, dict):
        return None
    try:
        steps = _texts(raw.get("steps"), 24, 180)
    except ValueError:
        steps = []
    if not steps:
        return None

    out = {"version": BLUEPRINT_VERSION}
    for key, limit in _SCALAR_LIMITS.items():
        out[key] = _text(raw.get(key), limit)
    for key, (count, width) in _LIST_LIMITS.items():
        try:
            out[key] = _texts(raw.get(key), count, width)
        except ValueError:
            out[key] = []
    out["sequence_map"] = _sequence_map(raw.get("sequence_map"))
    out["steps"] = steps

    previous_states = raw.get("step_states") or []
    by_task = {}
    if isinstance(previous_states, list):
        for row in previous_states:
            if not isinstance(row, dict):
                continue
            task = _text(row.get("task"), 180)
            status = row.get("status")
            if task and status in {"pending", "completed", "blocked"}:
                by_task[_step_key(task)] = {
                    "status": status,
                    "evidence": _text(row.get("evidence"), 240),
                }
    out["step_states"] = []
    for i, task in enumerate(steps, 1):
        old = by_task.get(_step_key(task), {})
        out["step_states"].append({
            "id": i,
            "task": task,
            "status": old.get("status", "pending"),
            "evidence": old.get("evidence"),
        })

    checks = raw.get("acceptance_checks") or []
    by_criterion = {}
    if isinstance(checks, list):
        for row in checks:
            if not isinstance(row, dict):
                continue
            criterion = _text(row.get("criterion"), 180)
            status = row.get("status")
            if criterion and status in {"pending", "passed", "failed"}:
                by_criterion[_step_key(criterion)] = {
                    "status": status,
                    "evidence": _text(row.get("evidence"), 240),
                }
    out["acceptance_checks"] = []
    for i, criterion in enumerate(out["acceptance_criteria"], 1):
        old = by_criterion.get(_step_key(criterion), {})
        out["acceptance_checks"].append({
            "id": i,
            "criterion": criterion,
            "status": old.get("status", "pending"),
            "evidence": old.get("evidence"),
        })

    tools = raw.get("completed_tools") or []
    try:
        out["completed_tools"] = _texts(tools, 80, 80)
    except ValueError:
        out["completed_tools"] = []
    try:
        out["generation"] = max(1, int(raw.get("generation") or 1))
    except (TypeError, ValueError):
        out["generation"] = 1
    out["source_request"] = _text(raw.get("source_request"), 500)
    return out


def create_blueprint(*, steps, previous=None, source_request=None,
                     preserve_progress=False, sequence_map=None, **fields):
    """Create a new direction while carrying forward unspecified style.

    A later request often says only "make the captions smaller".  Throwing
    away the existing music/motion/color direction for that would make the
    project creatively amnesiac.  Explicit new values replace old ones;
    unspecified fields inherit the last project direction.  Execution steps
    are always the new request's steps, with matching completed states kept
    only when the agent legitimately replans inside the same run.
    """
    old = normalize_blueprint(previous) or {}
    raw = {"steps": steps}
    for key in _SCALAR_LIMITS:
        value = fields.get(key)
        raw[key] = old.get(key) if value is None else value
    for key in _LIST_LIMITS:
        value = fields.get(key)
        raw[key] = old.get(key, []) if value is None else value
    raw["sequence_map"] = (
        old.get("sequence_map", []) if sequence_map is None
        else _sequence_map(sequence_map, strict=True))
    if fields.get("acceptance_criteria") is None and \
            not raw.get("acceptance_criteria"):
        raw["acceptance_criteria"] = _default_acceptance(raw)
    elif raw["sequence_map"] and _SEQUENCE_ACCEPTANCE not in \
            raw["acceptance_criteria"]:
        # A structured sequence map is not decorative metadata.  Even when
        # the editor supplied custom checks, closure must prove that the
        # departments actually formed the planned progression.
        raw["acceptance_criteria"] = (
            list(raw["acceptance_criteria"])[:15] + [_SEQUENCE_ACCEPTANCE])
    raw["generation"] = int(old.get("generation") or 0) + 1
    raw["source_request"] = source_request
    # A new user request starts fresh execution even when it repeats similar
    # words; only an intentional same-turn replan carries progress forward.
    raw["step_states"] = ((old.get("step_states") or [])
                          if preserve_progress else [])
    raw["acceptance_checks"] = ((old.get("acceptance_checks") or [])
                                if preserve_progress else [])
    raw["completed_tools"] = ((old.get("completed_tools") or [])
                              if preserve_progress else [])
    return normalize_blueprint(raw)


def update_progress(blueprint, *, completed_steps=None, blocked_steps=None,
                    passed_criteria=None, failed_criteria=None,
                    evidence=None):
    """Update semantic execution/acceptance state using 1-based ids."""
    out = normalize_blueprint(blueprint)
    if not out:
        raise ValueError("no edit plan exists")
    evidence = _text(evidence, 240)

    def ids(value, upper, label):
        if value is None:
            return set()
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{label} must be an array of 1-based ids")
        parsed = set()
        for item in value:
            try:
                item = int(item)
            except (TypeError, ValueError):
                raise ValueError(f"{label} contains a non-integer id")
            if item < 1 or item > upper:
                raise ValueError(f"{label} id {item} is outside 1-{upper}")
            parsed.add(item)
        return parsed

    complete = ids(completed_steps, len(out["step_states"]),
                   "completed_steps")
    blocked = ids(blocked_steps, len(out["step_states"]), "blocked_steps")
    if complete & blocked:
        raise ValueError("one step cannot be completed and blocked")
    passed = ids(passed_criteria, len(out["acceptance_checks"]),
                 "passed_criteria")
    failed = ids(failed_criteria, len(out["acceptance_checks"]),
                 "failed_criteria")
    if passed & failed:
        raise ValueError("one criterion cannot be passed and failed")

    for row in out["step_states"]:
        if row["id"] in complete:
            row["status"] = "completed"
            row["evidence"] = evidence or row.get("evidence")
        elif row["id"] in blocked:
            row["status"] = "blocked"
            row["evidence"] = evidence or row.get("evidence")
    for row in out["acceptance_checks"]:
        if row["id"] in passed:
            row["status"] = "passed"
            row["evidence"] = evidence or row.get("evidence")
        elif row["id"] in failed:
            row["status"] = "failed"
            row["evidence"] = evidence or row.get("evidence")
    return out


def status(blueprint):
    bp = normalize_blueprint(blueprint)
    if not bp:
        return {"state": "none", "pending_steps": [],
                "blocked_steps": [], "failed_criteria": [],
                "pending_criteria": []}
    pending = [r["id"] for r in bp["step_states"]
               if r["status"] == "pending"]
    blocked = [r["id"] for r in bp["step_states"]
               if r["status"] == "blocked"]
    failed = [r["id"] for r in bp["acceptance_checks"]
              if r["status"] == "failed"]
    checks = [r["id"] for r in bp["acceptance_checks"]
              if r["status"] == "pending"]
    if blocked:
        state = "blocked"
    elif pending:
        state = "in_progress"
    elif failed:
        state = "needs_repair"
    elif checks:
        state = "needs_review"
    else:
        state = "complete"
    return {"state": state, "pending_steps": pending,
            "blocked_steps": blocked, "failed_criteria": failed,
            "pending_criteria": checks}


def editorial_family(blueprint, inferred_grammar=None, has_main_video=True,
                     request_text=None):
    """Return one coarse, privacy-safe format label for cohort comparison.

    This is deliberately an editorial family, not the user's brief and not a
    creative instruction.  It lets production compare like with like across
    releases without persisting a second copy of customer text in metrics.
    Unknown or hybrid work abstains into ``mixed_other`` rather than making a
    confident but misleading classification.
    """
    bp = normalize_blueprint(blueprint) or {}
    haystack = " ".join(str(bp.get(key) or "") for key in (
        "format", "intent", "style_family", "brief", "objective",
        "caption_direction", "motion_direction")) + " " + str(
            request_text or "")
    text = haystack.casefold()
    if bp.get("sequence_map"):
        text += " " + " ".join(
            " ".join(str(row.get(key) or "") for key in (
                "role", "anchor", "purpose", "visual", "sound"))
            for row in bp["sequence_map"])

    def hit(*needles):
        return any(needle in text for needle in needles)

    if hit("podcast", "interview", "conversation", "roundtable", "q&a"):
        return "podcast_conversation"
    if hit("tutorial", "product demo", "software demo", "screen recording",
           "saas", "walkthrough", "how-to", "explainer"):
        return "product_demo_explainer"
    if hit("sports", "workout", "fitness", "gameplay", "gaming", "match",
           "race", "action reel"):
        return "action_sports_gameplay"
    if hit("music video", "lyric", "performance", "dance", "beat-led",
           "music-led", "concert"):
        return "music_led_performance"
    if hit("brand film", "commercial", "product ad", "product film",
           "campaign", "luxury ad", "fashion ad"):
        return "commercial_brand"
    if hit("vlog", "documentary", "story", "travel film", "wedding",
           "narrative"):
        return "narrative_story"
    if hit("talking head", "founder reel", "creator reel", "ugc",
           "social reel", "instagram reel", "tiktok"):
        return "talking_head_social"

    grammar_map = {
        "podcast-conversation": "podcast_conversation",
        "talking-head-promo": "talking_head_social",
        "kinetic-typography-talking-head": "talking_head_social",
        "narrative-vlog": "narrative_story",
        "voiceover-montage": "voiceover_montage",
        "quote-reel": "voiceover_montage",
        "card-deck-explainer": "product_demo_explainer",
    }
    if inferred_grammar in grammar_map:
        return grammar_map[inferred_grammar]
    if not has_main_video:
        return "graphic_canvas"
    return "mixed_other"


def sequence_block(blueprint, include=("anchor", "purpose", "visual", "sound"),
                   max_rows=_SEQUENCE_LIMIT):
    """Compact the planned audience journey for authoring or review."""
    bp = normalize_blueprint(blueprint)
    if not bp or not bp.get("sequence_map"):
        return ""
    labels = {"anchor": "anchor", "purpose": "purpose",
              "visual": "picture", "sound": "sound"}
    lines = []
    for index, row in enumerate(bp["sequence_map"][:max_rows], 1):
        timing = ""
        if row.get("source_start_s") is not None:
            timing = (f" source {row['source_start_s']}-"
                      f"{row['source_end_s']}s")
        provenance = (" evidence=" + ",".join(row.get("evidence_ids") or [])
                      if row.get("evidence_ids") else "")
        energy = (f" energy={row['energy']:.2f}"
                  if row.get("energy") is not None else "")
        details = "; ".join(
            f"{labels[key]}={row[key]}" for key in include
            if key in labels and row.get(key))
        lines.append(
            f"Beat {index} [{row['role']}]{timing}{provenance}{energy}: "
            f"{details}")
    return "\n".join(lines)


def decision_block(blueprint):
    """Compact treatment-choice evidence for authoring and fresh critics."""
    bp = normalize_blueprint(blueprint)
    if not bp:
        return ""
    lines = []
    if bp.get("treatment"):
        lines.append("Chosen treatment: " + bp["treatment"])
    for label, key in (
            ("Observed decision basis", "decision_basis"),
            ("Reference relationships to transfer", "reference_transfer"),
            ("Cross-department coherence rules", "coherence_rules"),
            ("Weaker treatments rejected", "alternatives_rejected")):
        if bp.get(key):
            lines.append(label + ": " + " | ".join(bp[key]))
    return "\n".join(lines)


def prompt_block(blueprint):
    """Compact durable context for the next model turn."""
    bp = normalize_blueprint(blueprint)
    if not bp:
        return ""
    bits = ["CREATIVE BLUEPRINT — the durable editorial contract for this "
            "project. Preserve it unless the user's latest words override it."]
    for label, key in (
        ("Brief", "brief"), ("Treatment", "treatment"),
        ("Format", "format"),
        ("Audience", "audience"), ("Platform", "platform"),
        ("Objective", "objective"), ("Intent", "intent"),
        ("Style family", "style_family"),
        ("Captions", "caption_direction"),
        ("Motion", "motion_direction"), ("B-roll", "broll_direction"),
        ("Music", "music_direction"), ("SFX", "sfx_direction"),
        ("Color", "color_direction"),
    ):
        if bp.get(key):
            bits.append(f"{label}: {bp[key]}")
    for label, key in (
        ("Narrative arc", "narrative_arc"),
        ("Decision basis", "decision_basis"),
        ("Reference transfer", "reference_transfer"),
        ("Coherence rules", "coherence_rules"),
        ("Weaker routes rejected", "alternatives_rejected"),
        ("Must keep", "must_keep"), ("Must avoid", "must_avoid"),
        ("Acceptance", "acceptance_criteria"),
    ):
        if bp.get(key):
            bits.append(label + ": " + " | ".join(bp[key]))
    if bp.get("sequence_map"):
        bits.append(
            "SEQUENCE MAP — keep these beats causally and stylistically "
            "connected; it is a treatment, not a quota:")
        bits.extend(sequence_block(bp).splitlines())
    bits.append("Execution: " + " | ".join(
        f"{r['id']} [{r['status']}] {r['task']}"
        + (f" — {r['evidence']}" if r.get("evidence") else "")
        for r in bp["step_states"]))
    s = status(bp)
    bits.append("Blueprint state: " + s["state"] + ".")
    return "\n".join(bits)
