"""Independent pre-execution judgment for ambiguous whole-program treatments.

The editing model sees the pixels and authors the candidate plan.  On a vague
brief, however, that same model can mistake its first plausible idea for a
decision and spend many versions polishing the wrong route.  This reviewer is
deliberately narrower: it receives the source-grounded decision record, the
format contract and the competing routes, then checks specificity, internal
coherence and evidence support before any expensive recipe/render work.

It never invents a style, selects tools, or requires decoration.  A revision
is actionable only when the reviewer cites exact labeled evidence from the
submitted plan at high confidence.  Outages, malformed answers and subjective
preference degrade to no gate.
"""

import hashlib
import json
import math

import llm


TREATMENT_JUDGE_VERSION = 1
_VERDICTS = {"accept", "revise", "abstain"}


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


def evidence_packet(plan, user_request, family_contract):
    """Bounded labeled evidence; labels make invented citations rejectable."""
    plan = plan if isinstance(plan, dict) else {}
    basis = [str(value)[:240] for value in plan.get("decision_basis") or []]
    alternatives = [str(value)[:240]
                    for value in plan.get("alternatives_rejected") or []]
    references = [str(value)[:240]
                  for value in plan.get("reference_transfer") or []]
    sequence = []
    for index, row in enumerate(plan.get("sequence_map") or [], 1):
        if not isinstance(row, dict):
            continue
        sequence.append({
            "ref": f"S{index}",
            "role": row.get("role"), "anchor": row.get("anchor"),
            "purpose": row.get("purpose"), "visual": row.get("visual"),
            "sound": row.get("sound"), "energy": row.get("energy"),
            "source_asset_key_present": bool(row.get("source_asset_key")),
            "evidence_ids": list(row.get("evidence_ids") or [])[:8],
        })
    allowed = {"PLAN", "USER"}
    allowed.update(f"B{i}" for i in range(1, len(basis) + 1))
    allowed.update(f"A{i}" for i in range(1, len(alternatives) + 1))
    allowed.update(f"R{i}" for i in range(1, len(references) + 1))
    allowed.update(row["ref"] for row in sequence)
    return {
        "allowed_evidence_refs": sorted(allowed),
        "USER": str(user_request or "")[:1000],
        "PLAN": {
            key: plan.get(key) for key in (
                "editorial_family", "format", "treatment", "objective",
                "narrative_arc", "coherence_rules", "department_plan",
                "motion_language",
                "caption_direction", "motion_direction", "broll_direction",
                "music_direction", "sfx_direction", "color_direction")
            if plan.get(key) not in (None, "", [], {})
        },
        "decision_basis": [
            {"ref": f"B{i}", "fact": value}
            for i, value in enumerate(basis, 1)],
        "rejected_routes": [
            {"ref": f"A{i}", "route_and_reason": value}
            for i, value in enumerate(alternatives, 1)],
        "reference_transfers": [
            {"ref": f"R{i}", "relationship": value}
            for i, value in enumerate(references, 1)],
        "sequence": sequence,
        "family_contract": str(family_contract or "")[:4200],
    }


def parse_report(answer, allowed_refs):
    raw = _json_object(answer)
    if raw is None:
        return None
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in _VERDICTS:
        return None
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence):
        return None
    confidence = round(min(max(confidence, 0.0), 1.0), 3)
    reason = " ".join(str(raw.get("reason") or "").split())[:520]
    revision = " ".join(str(raw.get("revision") or "").split())[:520]
    refs = []
    allowed = set(allowed_refs or [])
    cited = raw.get("evidence_refs") or []
    if not isinstance(cited, (list, tuple)):
        return None
    for value in cited:
        ref = str(value or "").strip().upper()
        if ref not in allowed:
            return None
        if ref not in refs:
            refs.append(ref)
    if verdict in {"accept", "revise"} and (not reason or not refs):
        return None
    if verdict == "revise" and not revision:
        return None
    return {
        "treatment_judge_v": TREATMENT_JUDGE_VERSION,
        "verdict": verdict, "confidence": confidence,
        "evidence_refs": refs[:12], "reason": reason,
        "revision": revision,
    }


def review(plan, user_request, family_contract):
    """Run one bounded text review; availability failure is honest abstention."""
    packet = evidence_packet(plan, user_request, family_contract)
    system = """You are an independent senior creative director reviewing a
whole-program treatment BEFORE expensive editing begins. The editing model has
already inspected the media; you may rely only on the labeled evidence packet
and must not pretend to see pixels or hear sound.

Judge whether the chosen treatment is a specific, executable editorial idea
supported by the observed facts, whether its story/picture/type/motion/sound
decisions form one system, and whether materially credible alternatives were
actually distinguished. The user's explicit direction wins. Silence,
stillness, natural color, the base picture and no captions/B-roll/SFX are valid
winners. Do not reward feature count, effect density or fashionable words.

REVISE only for a high-confidence load-bearing problem: the route contradicts
the user or its cited evidence, the family/sequence/department decisions are
internally incompatible, the treatment is merely platform/energy adjectives
with no executable idea, or the first plausible route was accepted without
distinguishing a materially credible alternative. Do not revise because you
personally prefer another valid aesthetic. ABSTAIN when the packet cannot
settle the issue.

Every ACCEPT or REVISE must cite one or more exact refs from
allowed_evidence_refs. Return JSON only:
{"verdict":"accept|revise|abstain","confidence":0.0,
"evidence_refs":["PLAN","B1"],"reason":"bounded judgment",
"revision":"one concrete plan-level correction, or empty"}"""
    try:
        result = llm.ask_text(
            system,
            json.dumps(packet, ensure_ascii=False, separators=(",", ":")),
            max_tokens=500, temperature=0.15,
            purpose="independent_treatment_judge")
    except Exception:
        return None
    if not result:
        return None
    return parse_report(result.get("text"), packet["allowed_evidence_refs"])


def actionable_revision(report):
    """Only exact, confident evidence may stop the wrong route pre-write."""
    return bool(report and report.get("verdict") == "revise" and
                float(report.get("confidence") or 0.0) >= .86 and
                report.get("evidence_refs") and report.get("revision"))


def fingerprint(plan, user_request):
    """Stable same-turn cache identity without storing media or hidden text."""
    payload = {
        "user": str(user_request or ""),
        "plan": plan if isinstance(plan, dict) else {},
        "v": TREATMENT_JUDGE_VERSION,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"),
        default=str).encode()).hexdigest()


def summary(report):
    if not report:
        return ""
    refs = ",".join(report.get("evidence_refs") or []) or "none"
    text = ("INDEPENDENT TREATMENT REVIEW: "
            + str(report.get("verdict") or "abstain").upper()
            + f" ({float(report.get('confidence') or 0):.0%}; refs={refs})")
    if report.get("reason"):
        text += " — " + report["reason"]
    if report.get("revision"):
        text += "; revision: " + report["revision"]
    return text
