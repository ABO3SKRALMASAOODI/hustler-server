"""Independent semantic finishing review for speech-led edits.

The visual critic can prove that a crop, caption or cutaway looks wrong and
the audio reviewer can hear the finished mix.  Neither can decide whether a
podcast answer lost its question, whether two retained thoughts join
coherently, or whether the ending resolves the promise made by the opening.
This module gives a fresh, tool-free model only the *assembled program*
transcript plus narrow source context around edit boundaries.  It is scoped to
meaningful speech cuts so a caption/color/crop-only turn buys no extra call.

The critic never writes an EDL.  Malformed, unavailable or low-confidence
judgment degrades to no finding, and subjective hook notes require more
evidence than a concrete missing-context defect before they can trigger the
agent's one repair decision.
"""

import json

import llm
from timeline import Timeline


STORY_CRITIC_VERSION = 1

_CATEGORIES = {
    "missing_context", "abrupt_open", "unresolved_end",
    "incoherent_sequence", "redundant_thought", "weak_hook",
    "instruction_miss", "other",
}
_SEVERITIES = {"blocker", "major", "minor"}
_STORY_FAMILIES = {
    "podcast_conversation", "talking_head_social",
    "product_demo_explainer", "narrative_story", "voiceover_montage",
}


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _json_object(text):
    if not isinstance(text, str):
        return None
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[i:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def parse_report(answer):
    """Normalize the small critic contract; discard ungrounded claims."""
    raw = _json_object(answer)
    if raw is None:
        return None
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in {"pass", "repair"}:
        return None
    findings = []
    for item in (raw.get("findings") or [])[:6]:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").strip().lower()
        category = str(item.get("category") or "other").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        repair = str(item.get("repair") or "").strip()
        try:
            confidence = min(max(float(item.get("confidence")), 0.0), 1.0)
        except (TypeError, ValueError):
            continue
        try:
            program_s = float(item["program_s"])
            if program_s < 0:
                program_s = None
        except (KeyError, TypeError, ValueError):
            program_s = None
        if severity not in _SEVERITIES or not evidence or not repair:
            continue
        if category not in _CATEGORIES:
            category = "other"
        findings.append({
            "severity": severity,
            "category": category,
            "program_s": program_s,
            "evidence": evidence[:320],
            "repair": repair[:260],
            "confidence": round(confidence, 3),
        })
    if any(row["severity"] in {"blocker", "major"} and
           row["confidence"] >= 0.82 for row in findings):
        verdict = "repair"
    return {"verdict": verdict, "findings": findings,
            "summary": str(raw.get("summary") or "")[:300]}


def should_review(edl, index, family):
    """Whether a final edit made a semantic decision worth another call.

    This is an attention allocator, not a creative restriction.  We review
    only speech-rich narrative families whose EDL actually removed or joined
    material.  A full-length video with a color grade or captions has no new
    story decision for an independent model to judge.
    """
    words = (index or {}).get("words") or []
    if family not in _STORY_FAMILIES or len(words) < 24:
        return False
    keep = (edl or {}).get("keep") or []
    if not keep:
        return False
    source_duration = _float(((index or {}).get("video") or {}).get(
        "duration"))
    kept = sum(max(0.0, _float(row[1]) - _float(row[0]))
               for row in keep if isinstance(row, (list, tuple)) and
               len(row) >= 2)
    removed_share = (max(0.0, 1.0 - kept / source_duration)
                     if source_duration > 0 else 0.0)
    return len(keep) >= 2 or removed_share >= 0.08


def _sentences(index):
    rows = []
    for raw in (index or {}).get("sentences") or []:
        text = " ".join(str(raw.get("text") or "").split())
        try:
            t0, t1 = float(raw["t0"]), float(raw["t1"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and t1 > t0:
            rows.append({"t0": t0, "t1": t1, "text": text,
                         "speaker": raw.get("speaker")})
    rows.sort(key=lambda row: row["t0"])
    return rows


def _spread(rows, count):
    if len(rows) <= count:
        return list(rows)
    if count <= 1:
        return [rows[len(rows) // 2]]
    return [rows[round(i * (len(rows) - 1) / (count - 1))]
            for i in range(count)]


def build_evidence(edl, index, family, user_request="", plan=None):
    """Build bounded program-order speech plus source boundary context."""
    timeline = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                        edl.get("speed") or [])
    source = _sentences(index)
    program = []
    for row in source:
        midpoint = (row["t0"] + row["t1"]) / 2.0
        out = timeline.src_to_out(midpoint)
        if out is None:
            continue
        program.append(dict(row, program_s=round(out, 2)))

    # Whole-program evidence stays chronological.  Very long outputs are
    # sampled evenly and explicitly labeled; joins themselves remain fully
    # represented in boundary context below, so the reviewer cannot mistake
    # an omitted middle for a real jump in the edit.
    sampled = len(program) > 140
    program_rows = _spread(program, 140)
    program_lines = []
    for row in program_rows:
        speaker = (f" S{row['speaker']}" if row.get("speaker") is not None
                   else "")
        program_lines.append(
            f"[program {row['program_s']:.2f}s | source {row['t0']:.2f}-"
            f"{row['t1']:.2f}{speaker}] {row['text']}")

    boundary_rows = []
    seen = set()
    for boundary_n, keep in enumerate(edl.get("keep") or [], 1):
        try:
            start, end = float(keep[0]), float(keep[1])
        except (TypeError, ValueError, IndexError):
            continue
        near = [row for row in source
                if row["t1"] >= start - 12.0 and row["t0"] <= end + 12.0
                and (abs(row["t1"] - start) <= 12.0 or
                     abs(row["t0"] - end) <= 12.0)]
        for row in near:
            key = (boundary_n, row["t0"], row["t1"])
            if key in seen:
                continue
            seen.add(key)
            kept = timeline.src_to_out((row["t0"] + row["t1"]) / 2.0)
            boundary_rows.append(
                f"[keep {boundary_n}={start:.2f}-{end:.2f}; "
                f"source {row['t0']:.2f}-{row['t1']:.2f}; "
                f"{'KEPT at program ' + format(kept, '.2f') + 's' if kept is not None else 'CUT'}] "
                f"{row['text']}")
    # Pathological micro-cut timelines can create hundreds of boundaries.
    # Preserve evidence across the whole edit rather than the first page.
    boundary_rows = _spread(boundary_rows, 120)

    direction = plan or {}
    anchors = {
        key: direction.get(key) for key in (
            "brief", "treatment", "format", "intent", "decision_basis",
            "reference_transfer", "coherence_rules",
            "alternatives_rejected", "narrative_arc", "sequence_map",
            "must_keep", "must_avoid", "acceptance_checks")
        if direction.get(key)
    }
    return {
        "family": family,
        "user_request": str(user_request or "")[:1200],
        "direction": anchors,
        "program_duration_s": round(timeline.out_duration, 2),
        "program_sentence_count": len(program),
        "program_sampled_evenly": sampled,
        "program_transcript": "\n".join(program_lines),
        "source_context_around_every_keep_boundary": "\n".join(
            boundary_rows),
    }


_SYSTEM = """You are an independent senior story editor reviewing the exact
assembled transcript of a finished speech-led video. Another editor made the
cut. Be adversarial but evidence-bound: judge whether a viewer can understand
the opening, each join, the progression, and the ending without context that
was cut away. For interviews/podcasts, an answer needs its nearby question or
setup when the answer is not independently intelligible. Preserve intentional
open loops, rhetorical repetition and jump-cut energy when they work. Do not
reward feature count and do not rewrite the speaker's ideas merely to make them
generic. Do not infer a defect from evenly sampled omissions: use the complete
boundary context to judge joins, and abstain when the supplied evidence cannot
prove the claim. User instructions outrank your preference.

Return JSON only:
{"verdict":"pass|repair","summary":"one sentence","findings":[
{"severity":"blocker|major|minor","category":"missing_context|abrupt_open|unresolved_end|incoherent_sequence|redundant_thought|weak_hook|instruction_miss|other","program_s":12.3,"evidence":"exact words/times proving the issue","repair":"one source-boundary-aware action","confidence":0.0}]}
At most four findings. A major means a likely viewer rejection, not optional
polish. A merely less-than-perfect hook is minor unless the brief explicitly
requires a high-retention short and the first words visibly fail that promise.
Never claim to have seen pixels or heard audio."""


def review(edl, index, family, user_request="", plan=None):
    """Run the bounded independent story review, best effort."""
    if not should_review(edl, index, family):
        return None
    evidence = build_evidence(edl, index, family, user_request, plan)
    answer = llm.ask_text(
        _SYSTEM, json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
        max_tokens=1100, temperature=0.2,
        purpose="independent_story_critic")
    report = parse_report((answer or {}).get("text")) if answer else None
    if report is not None:
        report["story_critic_v"] = STORY_CRITIC_VERSION
        report["program_sentence_count"] = evidence[
            "program_sentence_count"]
        report["sampled_evenly"] = evidence["program_sampled_evenly"]
    return report


def repair_lines(report):
    """High-confidence semantic defects eligible for one repair decision."""
    if not report:
        return []
    lines = []
    for finding in report.get("findings") or []:
        if finding.get("severity") not in {"blocker", "major"}:
            continue
        threshold = 0.90 if finding.get("category") in {
            "weak_hook", "redundant_thought"} else 0.82
        if float(finding.get("confidence") or 0.0) < threshold:
            continue
        at = finding.get("program_s")
        where = f" at program {at:.1f}s" if at is not None else ""
        lines.append(
            f"independent story review [{finding.get('category')}]"
            f"{where}: {finding.get('evidence')} Repair: "
            f"{finding.get('repair')}")
    return lines


def summary_line(report):
    if report is None:
        return ""
    lines = repair_lines(report)
    if lines:
        return " INDEPENDENT STORY REVIEW: repair — " + "; ".join(lines[:3])
    summary = str(report.get("summary") or "").strip()
    if report.get("verdict") == "pass":
        return (" INDEPENDENT STORY REVIEW: pass"
                + (" — " + summary if summary else "."))
    findings = report.get("findings") or []
    if findings:
        return (" INDEPENDENT STORY REVIEW: advisory — "
                + str(findings[0].get("evidence") or "")[:260])
    return ""
