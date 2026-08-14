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

import json
import re

import captions as captionlib
import editorial_contracts


BLUEPRINT_VERSION = 3

_SCALAR_LIMITS = {
    "brief": 240,
    "treatment": 180,
    "format": 80,
    "intent": 200,
    "audience": 160,
    "platform": 80,
    "objective": 200,
    "style_family": 120,
    "editorial_family": 40,
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
    # Free-named relationship into motion_language. ``hold`` is reserved for
    # an intentionally settled beat; every other value must name one of the
    # treatment's motifs when a strict whole-program plan is authored.
    "motion_motif": 48,
}

_SEQUENCE_ASSET_KEY_LIMIT = 500

# A professional treatment is a set of choices, including deliberate
# restraint.  These are the renderer departments whose presence/absence can
# be checked from an EDL without pretending that a tool count measures taste.
# ``author`` means the finished EDL must visibly contain that department;
# ``preserve`` means the existing/source treatment is intentionally enough;
# ``omit`` means its absence is an authored choice.  This keeps "no music" or
# "keep the speaker on screen" first-class while preventing a blueprint from
# promising six departments and silently delivering one.
TREATMENT_DEPARTMENTS = (
    "captions", "motion", "broll", "music", "sfx", "color",
)
DEPARTMENT_MODES = ("author", "preserve", "omit")
_DEPARTMENT_PURPOSE_LIMIT = 220

MOTION_DOMAINS = (
    "camera", "type", "graphic", "media", "transition", "effect",
)
_MOTION_MOTIF_LIMIT = 12
_MOTION_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")

_SEQUENCE_ACCEPTANCE = (
    "the rendered sequence realizes each planned beat in order: its anchor "
    "and audience purpose remain clear, while picture, sound and relative "
    "energy form one intentional progression"
)
_DEPARTMENT_ACCEPTANCE = (
    "every department promised as author exists in the final EDL, every "
    "department promised as omit is absent, and preserve remains an "
    "intentional evidence-backed restraint choice"
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


def _unit_interval(value, label, strict=False, default=None):
    if value is None:
        if strict and default is None:
            raise ValueError(f"motion_language.{label} is required")
        return default
    try:
        number = round(float(value), 3)
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"motion_language.{label} must be 0 to 1")
        return default
    if not 0.0 <= number <= 1.0:
        if strict:
            raise ValueError(f"motion_language.{label} must be 0 to 1")
        return default
    return number


def _motion_language(value, strict=False):
    """Normalize a treatment-specific motion vocabulary, never a preset.

    Numeric controls express relationships rather than operation counts:
    density is the share of meaningful events that should move, intensity is
    relative magnitude, and contrast is how strongly peaks differ from
    support. Free-named motifs say what moves, when, and why; the renderer's
    general keyframes remain the executable language.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        if strict:
            raise ValueError("motion_language must be an object")
        return None
    principle = _text(value.get("principle"), 240)
    stillness_rule = _text(value.get("stillness_rule"), 240)
    if strict and not principle:
        raise ValueError(
            "motion_language.principle must name the unifying movement idea")
    if strict and not stillness_rule:
        raise ValueError(
            "motion_language.stillness_rule must say when the frame settles")
    density = _unit_interval(value.get("density"), "density", strict)
    intensity = _unit_interval(value.get("intensity"), "intensity", strict)
    contrast = _unit_interval(value.get("contrast"), "contrast", strict)
    raw_motifs = value.get("motifs")
    if not isinstance(raw_motifs, (list, tuple)):
        if strict:
            raise ValueError("motion_language.motifs must be an array")
        raw_motifs = []
    motifs, ids = [], set()
    for index, raw in enumerate(raw_motifs[:_MOTION_MOTIF_LIMIT], 1):
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(
                    f"motion_language.motifs[{index}] must be an object")
            continue
        motif_id = str(raw.get("id") or "").strip().lower()
        if not _MOTION_ID.fullmatch(motif_id) or motif_id == "hold":
            if strict:
                raise ValueError(
                    f"motion_language.motifs[{index}].id must be a short "
                    "lowercase identifier beginning with a letter; 'hold' "
                    "is reserved for intentional stillness")
            continue
        if motif_id in ids:
            if strict:
                raise ValueError(
                    f"motion_language motif id {motif_id!r} is duplicated")
            continue
        behavior = _text(raw.get("behavior"), 220)
        trigger = _text(raw.get("trigger"), 220)
        if strict and (not behavior or not trigger):
            raise ValueError(
                f"motion_language.motifs[{index}] needs behavior and trigger")
        raw_domains = raw.get("domains") or []
        if not isinstance(raw_domains, (list, tuple)):
            if strict:
                raise ValueError(
                    f"motion_language.motifs[{index}].domains must be an array")
            raw_domains = []
        domains = []
        for raw_domain in raw_domains:
            domain = str(raw_domain or "").strip().lower()
            if domain not in MOTION_DOMAINS:
                if strict:
                    raise ValueError(
                        f"motion_language.motifs[{index}] has unknown domain "
                        f"{raw_domain!r}; use " + ", ".join(MOTION_DOMAINS))
                continue
            if domain not in domains:
                domains.append(domain)
        if strict and not domains:
            raise ValueError(
                f"motion_language.motifs[{index}] needs at least one domain")
        if behavior and trigger and domains:
            motifs.append({"id": motif_id, "behavior": behavior,
                           "trigger": trigger, "domains": domains})
            ids.add(motif_id)
    if strict and not motifs:
        raise ValueError(
            "motion_language.motifs needs at least one executable motif")
    if not principle or not stillness_rule or density is None or \
            intensity is None or contrast is None or not motifs:
        return None
    return {
        "principle": principle,
        "density": density,
        "intensity": intensity,
        "contrast": contrast,
        "stillness_rule": stillness_rule,
        "motifs": motifs,
    }


def _department_plan(value, strict=False):
    """Normalize an explicit cross-department execution contract."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError("department_plan must be an object")
        return {}
    out = {}
    for raw_name, raw in value.items():
        name = str(raw_name or "").strip().lower()
        if name not in TREATMENT_DEPARTMENTS:
            if strict:
                raise ValueError(
                    "department_plan has unknown department "
                    f"{raw_name!r}; use " + ", ".join(TREATMENT_DEPARTMENTS))
            continue
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(
                    f"department_plan.{name} must be an object")
            continue
        mode = str(raw.get("mode") or "").strip().lower()
        if mode not in DEPARTMENT_MODES:
            if strict:
                raise ValueError(
                    f"department_plan.{name}.mode must be one of "
                    + ", ".join(DEPARTMENT_MODES))
            continue
        purpose = _text(raw.get("purpose"), _DEPARTMENT_PURPOSE_LIMIT)
        if not purpose:
            if strict:
                raise ValueError(
                    f"department_plan.{name}.purpose must name the editorial "
                    "reason for authoring, preserving, or omitting it")
            purpose = mode
        out[name] = {"mode": mode, "purpose": purpose}
    return out


def _direction_mode(value):
    """Best-effort compatibility for plans authored before v2.

    Explicit ``department_plan`` always wins.  This inference only upgrades a
    direction supplied in the current tool call, so an older agent model gets
    executable promises without turning an inherited style note into a new
    obligation.  Ambiguous/optional language stays ``preserve`` rather than
    forcing decoration.
    """
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    if not text:
        return "preserve"
    if any(phrase in text for phrase in (
            "optional", "if useful", "if it helps", "only if earned",
            "only when earned", "use if needed", "as needed",
            "preserve existing", "keep existing", "leave as is",
            "keep the source", "keep source", "natural color",
            "natural colour", "keep the base picture", "stay on speaker",
            "preserve stillness")):
        return "preserve"
    if any(phrase in text for phrase in (
            "no music", "without music", "preserve silence",
            "all silence", "keep it dry", "keep dry", "no sfx",
            "without sfx", "no sound effect",
            "no captions", "without captions", "no subtitles",
            "no b-roll", "no broll", "without b-roll", "without broll",
            "no motion", "without motion", "no animation",
            "no grade", "without grading", "no color grade",
            "no colour grade", "leave ungraded")):
        return "omit"
    return "author"


def department_plan_for_update(previous, explicit=None, *, substantial=False,
                               directions=None):
    """Merge one plan update without making narrow repairs creatively amnesic.

    A whole-program sequence accounts for every department.  A narrow later
    turn inherits the durable contract and changes only departments whose
    direction was supplied in that call.
    """
    old = (normalize_blueprint(previous) or {}).get("department_plan") or {}
    if explicit is not None:
        parsed = _department_plan(explicit, strict=True)
        current = ({} if substantial else {
            key: dict(row) for key, row in old.items()})
        current.update(parsed)
        if substantial:
            for name in TREATMENT_DEPARTMENTS:
                current.setdefault(name, {
                    "mode": "preserve",
                    "purpose": "preserve the established/source treatment",
                })
        return current
    current = ({} if substantial else {
        key: dict(row) for key, row in old.items()})
    directions = directions or {}
    for name in TREATMENT_DEPARTMENTS:
        if name in directions and directions[name] is not None:
            value = directions[name]
            current[name] = {
                "mode": _direction_mode(value),
                "purpose": (_text(value, _DEPARTMENT_PURPOSE_LIMIT)
                            or "preserve the established/source treatment"),
            }
        elif substantial:
            current[name] = {
                "mode": "preserve",
                "purpose": "preserve the established/source treatment",
            }
    return current


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

        # Source clocks are local to a file.  Historically every timed beat
        # was interpreted against the main upload, which made a perfectly
        # valid 3.2-5.0s moment in an auxiliary clip look like invented main-
        # video evidence.  Keep main implicit for compact/backward-compatible
        # plans; an explicit key scopes the same fields to that project asset.
        source_asset_key = _text(
            raw.get("source_asset_key"), _SEQUENCE_ASSET_KEY_LIMIT)
        if source_asset_key and source_asset_key.casefold() not in {
                "main", "main source", "__main__"}:
            row["source_asset_key"] = source_asset_key

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


def _source_evidence(index):
    """Exact sentence/shot windows exposed by one reusable media index."""
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
    return evidence


def source_evidence_ids_at(index, start, end=None, limit=8):
    """Exact index IDs overlapping one file-local source window."""
    try:
        start = float(start)
        end = float(start if end is None else end)
    except (TypeError, ValueError):
        return []
    if end <= start:
        # A sampled frame is an instant. Give it a tiny window so boundary
        # comparisons remain stable without pretending it spans a scene.
        end = start + 0.001
    out = []
    for evidence_id, (row_start, row_end) in _source_evidence(index).items():
        if row_end > start and row_start < end:
            out.append(evidence_id)
        if len(out) >= max(1, int(limit or 1)):
            break
    return out


def _evidence_correction(evidence, start=None, end=None):
    """A compact machine-usable set of real ids for a rejected beat."""
    if start is not None and end is not None:
        matches = [
            evidence_id for evidence_id, (row_start, row_end) in evidence.items()
            if row_end > float(start) and row_start < float(end)
        ]
    else:
        matches = list(evidence)
    if not matches:
        return ""
    # IDs are deliberately copied as JSON-shaped strings so the next tool call
    # can paste the correction without translating prose.
    shown = ", ".join(json.dumps(value) for value in matches[:8])
    suffix = ", ..." if len(matches) > 8 else ""
    return f" Set evidence_ids=[{shown}{suffix}] from the exact overlapping rows."


def source_evidence_violations(sequence_map, index, asset_indexes=None):
    """Return timed beat bindings that are not supported by the real index.

    Source seconds alone are too easy for a language model to invent.  A
    substantial treatment therefore copies the sentence/shot ids it used from
    PROJECT STATE or ``get_editorial_map``.  Each auxiliary upload has its own
    clock and index, selected by ``source_asset_key``.  The check is
    deliberately about provenance, not taste: it proves the proposed window
    is covered by the cited source records and leaves the editor free to
    choose any treatment.
    """
    violations = []
    for number, beat in enumerate(sequence_map or [], 1):
        asset_key = beat.get("source_asset_key")
        if asset_key:
            if asset_indexes is None or asset_key not in asset_indexes:
                violations.append(
                    f"sequence_map[{number}] source_asset_key={asset_key!r} "
                    "is not a resolved indexed project asset; copy the exact "
                    "storage_key from list_assets and inspect "
                    f"get_editorial_map(asset_key={asset_key!r})")
                continue
            scoped_index = asset_indexes.get(asset_key) or {}
            source_label = f"asset {asset_key!r}"
        else:
            scoped_index = index or {}
            source_label = "main source"
        evidence = _source_evidence(scoped_index)
        start, end = beat.get("source_start_s"), beat.get("source_end_s")
        ids = [str(value) for value in beat.get("evidence_ids") or []]
        if start is None:
            unknown = [value for value in ids if value not in evidence]
            if unknown:
                violations.append(
                    f"sequence_map[{number}] cites unknown id(s) for "
                    f"{source_label}: " + ", ".join(unknown)
                    + _evidence_correction(evidence))
            continue
        if not ids:
            violations.append(
                f"sequence_map[{number}] has source seconds but no "
                f"evidence_ids for {source_label}; copy the sentence/shot "
                "ids that prove this beat from PROJECT STATE or "
                "get_editorial_map"
                + _evidence_correction(evidence, start, end))
            continue
        unknown = [value for value in ids if value not in evidence]
        if unknown:
            violations.append(
                f"sequence_map[{number}] cites unknown id(s) for "
                f"{source_label}: " + ", ".join(unknown)
                + _evidence_correction(evidence, start, end))
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
                f"its cited {source_label} evidence ({envelope_start:.3f}-"
                f"{envelope_end:.3f}s)"
                + _evidence_correction(evidence, start, end))
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
    if raw.get("motion_direction") or raw.get("motion_language"):
        criteria.append(
            "the rendered motion realizes the recorded vocabulary and beat "
            "bindings: motifs land only on their named triggers, relative "
            "density/intensity/contrast are coherent, and the stillness rule "
            "creates intentional settling rather than uniform movement")
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
    if raw.get("department_plan"):
        criteria.append(_DEPARTMENT_ACCEPTANCE)
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
    out["department_plan"] = _department_plan(raw.get("department_plan"))
    out["motion_language"] = _motion_language(raw.get("motion_language"))
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
                     preserve_progress=False, sequence_map=None,
                     department_plan=None, motion_language=None, **fields):
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
    raw["department_plan"] = (
        old.get("department_plan", {}) if department_plan is None
        else _department_plan(department_plan, strict=True))
    raw["motion_language"] = (
        old.get("motion_language") if motion_language is None
        else _motion_language(motion_language, strict=True))
    motion_mode = ((raw.get("department_plan") or {}).get("motion") or {}).get(
        "mode")
    if raw["sequence_map"] and motion_mode == "author":
        if not raw.get("motion_language"):
            raise ValueError(
                "a substantial plan that authors motion needs motion_language "
                "with a principle, numeric density/intensity/contrast, a "
                "stillness_rule and free-named motifs")
        motif_ids = {row["id"] for row in raw["motion_language"]["motifs"]}
        missing = []
        unknown = []
        for index, beat in enumerate(raw["sequence_map"], 1):
            motif = str(beat.get("motion_motif") or "").strip().lower()
            if not motif:
                missing.append(index)
            elif motif != "hold" and motif not in motif_ids:
                unknown.append((index, motif))
        if missing:
            raise ValueError(
                "motion-authoring sequence beats must each set motion_motif "
                "to a declared motif id or 'hold'; missing on beat(s) "
                + ", ".join(map(str, missing)))
        if unknown:
            detail = ", ".join(
                f"beat {index}={motif!r}" for index, motif in unknown)
            raise ValueError(
                "sequence motion_motif must name a declared motif id or "
                f"'hold'; unknown {detail}")
    if fields.get("acceptance_criteria") is None and \
            not raw.get("acceptance_criteria"):
        raw["acceptance_criteria"] = _default_acceptance(raw)
    # Structured contracts are not decorative metadata. Even when the editor
    # supplies a full custom checklist, reserve space for every applicable
    # mechanical closure invariant rather than appending one and accidentally
    # truncating the other at the 16-row normalization boundary.
    required_checks = []
    if raw["sequence_map"]:
        required_checks.append(_SEQUENCE_ACCEPTANCE)
    if raw.get("department_plan"):
        required_checks.append(_DEPARTMENT_ACCEPTANCE)
    if required_checks:
        custom = [criterion for criterion in raw["acceptance_criteria"]
                  if criterion not in required_checks]
        room = _LIST_LIMITS["acceptance_criteria"][0] - len(required_checks)
        raw["acceptance_criteria"] = custom[:max(0, room)] + required_checks
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


def editorial_family_cast(blueprint, inferred_grammar=None,
                          has_main_video=True, request_text=None):
    """Return an evidence-honest format cast, including uncertainty.

    A platform container ("Instagram reel") and an energy adjective do not say
    whether the material is a performance, conversation, product demo, action
    sequence or narrated montage. This cast commits only from an explicit
    blueprint, format-specific language, or measured grammar; otherwise it
    exposes the full slate and abstains to ``mixed_other``.
    """
    bp = normalize_blueprint(blueprint) or {}
    explicit = str(bp.get("editorial_family") or "").strip()
    if explicit in editorial_contracts.FAMILIES:
        return {
            "family": explicit, "confidence": 1.0,
            "reason": "the creative blueprint explicitly selected it",
            "candidates": [explicit],
        }
    haystack = " ".join(str(bp.get(key) or "") for key in (
        "format", "intent", "style_family", "brief", "treatment", "objective",
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

    selected = reason = None
    if hit("podcast", "interview", "conversation", "roundtable", "q&a"):
        selected, reason = "podcast_conversation", "explicit conversation format"
    elif hit("tutorial", "product demo", "software demo", "screen recording",
             "saas", "walkthrough", "how-to", "explainer"):
        selected, reason = "product_demo_explainer", "explicit demo/explainer format"
    elif hit("sports", "workout", "fitness", "gameplay", "gaming", "match",
             "race", "action reel"):
        selected, reason = "action_sports_gameplay", "explicit action/sports/gameplay format"
    elif hit("music video", "lyric video", "live performance", "performance",
             "dance", "beat-led", "music-led", "concert"):
        selected, reason = "music_led_performance", "explicit music/performance format"
    elif hit("brand film", "commercial", "product ad", "product film",
             "campaign", "luxury ad", "fashion ad"):
        selected, reason = "commercial_brand", "explicit commercial/brand format"
    elif hit("vlog", "documentary", "travel story", "travel film", "wedding",
             "narrative story", "short film"):
        selected, reason = "narrative_story", "explicit narrative format"
    elif hit("voiceover montage", "voice-over montage", "narrated montage",
             "narration-led", "voiceover-led"):
        selected, reason = "voiceover_montage", "explicit narration-led montage format"
    elif hit("talking head", "founder reel", "creator talking", "ugc ad",
             "speaker-led", "speech-led creator"):
        selected, reason = "talking_head_social", "explicit speaker-led social format"
    elif hit("graphic canvas", "motion graphic", "animated cards",
             "typographic video", "slideshow"):
        selected, reason = "graphic_canvas", "explicit graphic/card format"
    if selected:
        return {"family": selected, "confidence": .9, "reason": reason,
                "candidates": [selected]}

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
        selected = grammar_map[inferred_grammar]
        return {
            "family": selected, "confidence": .8,
            "reason": f"measured source grammar classified as {inferred_grammar}",
            "candidates": [selected],
        }
    reason = ("no main source; uploaded media or cards must be inspected"
              if not has_main_video else
              "the brief/evidence does not identify one dominant format")
    return {
        "family": "mixed_other", "confidence": .25, "reason": reason,
        "candidates": list(editorial_contracts.FAMILIES),
    }


def editorial_family(blueprint, inferred_grammar=None, has_main_video=True,
                     request_text=None):
    """Privacy-safe coarse label for prompts, critics and production cohorts."""
    return editorial_family_cast(
        blueprint, inferred_grammar, has_main_video, request_text)["family"]


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
            source = (f"asset {row['source_asset_key']} CLIP"
                      if row.get("source_asset_key") else "main source")
            timing = (f" {source} {row['source_start_s']}-"
                      f"{row['source_end_s']}s")
        provenance = (" evidence=" + ",".join(row.get("evidence_ids") or [])
                      if row.get("evidence_ids") else "")
        energy = (f" energy={row['energy']:.2f}"
                  if row.get("energy") is not None else "")
        motion = (f" motion_motif={row['motion_motif']}"
                  if row.get("motion_motif") else "")
        details = "; ".join(
            f"{labels[key]}={row[key]}" for key in include
            if key in labels and row.get(key))
        lines.append(
            f"Beat {index} [{row['role']}]{timing}{provenance}{energy}"
            f"{motion}: "
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
    if bp.get("department_plan"):
        lines.append("Department decisions: " + " | ".join(
            f"{name}={row['mode']} ({row['purpose']})"
            for name, row in bp["department_plan"].items()))
    if bp.get("motion_language"):
        ml = bp["motion_language"]
        lines.append(
            "Motion language: " + ml["principle"]
            + f" [density={ml['density']:.2f}, intensity={ml['intensity']:.2f}, "
              f"contrast={ml['contrast']:.2f}]"
            + "; stillness=" + ml["stillness_rule"]
            + "; motifs=" + " | ".join(
                f"{row['id']}({','.join(row['domains'])}): "
                f"{row['behavior']} when {row['trigger']}"
                for row in ml["motifs"]))
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
        ("Format", "format"), ("Editorial family", "editorial_family"),
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
    if bp.get("motion_language"):
        ml = bp["motion_language"]
        bits.append(
            "MOTION LANGUAGE — one reusable vocabulary, not an effect quota: "
            + ml["principle"]
            + f"; density={ml['density']:.2f}; intensity={ml['intensity']:.2f}; "
              f"contrast={ml['contrast']:.2f}; stillness={ml['stillness_rule']}")
        bits.extend(
            f"- motif {row['id']} [{','.join(row['domains'])}]: "
            f"{row['behavior']} | trigger: {row['trigger']}"
            for row in ml["motifs"])
    if bp.get("sequence_map"):
        bits.append(
            "SEQUENCE MAP — keep these beats causally and stylistically "
            "connected; it is a treatment, not a quota:")
        bits.extend(sequence_block(bp).splitlines())
    if bp.get("department_plan"):
        bits.append(
            "DEPARTMENT EXECUTION CONTRACT — author must exist in the final "
            "EDL, preserve is deliberate restraint, omit must remain absent:")
        bits.extend(
            f"- {name}: {row['mode']} — {row['purpose']}"
            for name, row in bp["department_plan"].items())
    bits.append("Execution: " + " | ".join(
        f"{r['id']} [{r['status']}] {r['task']}"
        + (f" — {r['evidence']}" if r.get("evidence") else "")
        for r in bp["step_states"]))
    s = status(bp)
    bits.append("Blueprint state: " + s["state"] + ".")
    return "\n".join(bits)


def _authored_department_state(edl, has_main_video=True):
    """Renderer-grounded department presence, without evaluating taste."""
    edl = edl if isinstance(edl, dict) else {}
    effects = edl.get("effects") or {}
    captions_authored = bool(edl.get("captions"))
    music_authored = any(
        isinstance(row, dict) and not row.get("mute")
        for row in (edl.get("music") or []))
    sfx_authored = any(
        isinstance(row, dict) and not row.get("mute")
        for row in (edl.get("sfx") or []))

    transitions = effects.get("transition") or {}

    def changed_speed(row):
        if not isinstance(row, dict):
            return False
        try:
            return abs(float(row.get("factor") or 1.0) - 1.0) > 1e-6
        except (TypeError, ValueError):
            return False

    motion_authored = bool(
        effects.get("zooms") or effects.get("stylize") or
        effects.get("frame_shifts") or effects.get("custom") or
        effects.get("fade_in_s") or effects.get("fade_out_s") or
        (isinstance(transitions, dict) and transitions.get("style")) or
        any(isinstance(row, dict) and (
            row.get("motion") or row.get("entrance") not in (None, "none") or
            row.get("exit") not in (None, "none") or row.get("screen"))
            for key in ("texts", "vectors", "overlays")
            for row in (edl.get(key) or [])) or
        any(changed_speed(row) for row in (edl.get("speed") or [])) or
        captionlib.motion_enabled(edl.get("captions")))

    # On a main-video edit, inserts and full-cover overlays are cutaways.  On
    # a canvas montage, inserts are the base program itself, so only an
    # overlay proves a separate B-roll layer; authoring the montage remains
    # expressed by its sequence map and picture decisions.
    broll_authored = bool(
        any(isinstance(row, dict) and row.get("fit") == "cover"
            for row in (edl.get("overlays") or [])) or
        (has_main_video and bool(edl.get("inserts"))))
    color_authored = bool(
        effects.get("grade") or effects.get("grade_custom") or
        effects.get("custom"))
    return {
        "captions": captions_authored,
        "motion": motion_authored,
        "broll": broll_authored,
        "music": music_authored,
        "sfx": sfx_authored,
        "color": color_authored,
    }


def department_execution_gaps(blueprint, edl, has_main_video=True):
    """Promises contradicted by the final EDL.

    This deliberately does not score density, preset choice, or taste.  It
    answers one narrower but load-bearing question: did the editor actually
    author (or omit) the department it said it would?  ``preserve`` never
    creates a structural requirement.
    """
    bp = normalize_blueprint(blueprint)
    if not bp or not bp.get("department_plan"):
        return []
    present = _authored_department_state(edl, has_main_video)
    gaps = []
    for name, row in bp["department_plan"].items():
        mode = row["mode"]
        if mode == "author" and not present.get(name):
            gaps.append({
                "department": name, "mode": mode,
                "purpose": row["purpose"],
                "message": (f"{name} was promised as authored but the "
                            "current EDL contains no such treatment"),
            })
        elif mode == "omit" and present.get(name):
            gaps.append({
                "department": name, "mode": mode,
                "purpose": row["purpose"],
                "message": (f"{name} was deliberately omitted but the "
                            "current EDL still contains that treatment"),
            })
    return gaps


def department_execution_summary(blueprint, edl, has_main_video=True):
    """Compact benchmarkable promise/fulfillment counts."""
    bp = normalize_blueprint(blueprint) or {}
    plan = bp.get("department_plan") or {}
    gaps = department_execution_gaps(bp, edl, has_main_video)
    auditable = sum(row.get("mode") in {"author", "omit"}
                    for row in plan.values())
    return {
        "decisions": len(plan),
        "auditable_promises": auditable,
        "fulfilled_promises": max(0, auditable - len(gaps)),
        "gaps": gaps,
    }
