"""Independent casting review for automatically selected speech shorts.

The parent shorts planner searches a long transcript and proposes a handful
of source windows.  This module gives a fresh, tool-free editor only those
exact windows plus narrow surrounding context.  Its job is selection, not
styling: a quotable sentence is not enough when the opening needs missing
context, the thought does not develop, or the ending has no payoff.

One bounded call reviews the whole proposed slate.  The reviewer may reject
every candidate.  Malformed or unavailable judgment degrades to the original
planner output so a critic outage never destroys a valid shorts job.
"""

import json

import llm


SHORTS_JUDGE_VERSION = 1
_VERDICTS = {"keep", "reject", "uncertain"}


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


def _confidence(value):
    try:
        return round(min(max(float(value), 0.0), 1.0), 3)
    except (TypeError, ValueError):
        return None


def parse_report(answer, candidate_ids):
    """Normalize decisions and discard claims about unknown candidates."""
    raw = _json_object(answer)
    if raw is None:
        return None
    allowed = {str(value) for value in candidate_ids}
    decisions = []
    seen = set()
    for item in (raw.get("decisions") or [])[:16]:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("id") or "").strip()
        verdict = str(item.get("verdict") or "").strip().lower()
        confidence = _confidence(item.get("confidence"))
        evidence = " ".join(str(item.get("evidence") or "").split())
        reason = " ".join(str(item.get("reason") or "").split())
        if candidate_id not in allowed or candidate_id in seen or \
                verdict not in _VERDICTS or confidence is None or \
                not evidence or not reason:
            continue
        seen.add(candidate_id)
        decisions.append({
            "id": candidate_id,
            "verdict": verdict,
            "confidence": confidence,
            "evidence": evidence[:360],
            "reason": reason[:300],
        })
    if not decisions:
        return None
    return {
        "version": SHORTS_JUDGE_VERSION,
        "summary": " ".join(str(raw.get("summary") or "").split())[:300],
        "decisions": decisions,
    }


def _sentence_rows(index):
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


def build_evidence(clips, index, direction=""):
    """Build exact proposed programs plus narrow cut-away context."""
    source = _sentence_rows(index)
    candidates = []
    for position, clip in enumerate(clips, 1):
        start, end = float(clip["start"]), float(clip["end"])
        window = [row for row in source
                  if row["t1"] >= start and row["t0"] <= end]
        nearby = [row for row in source
                  if row["t1"] >= start - 12.0 and row["t0"] <= end + 12.0]

        def lines(rows):
            rendered = []
            for row in rows:
                speaker = (f" S{row['speaker']}"
                           if row.get("speaker") is not None else "")
                state = ("KEPT" if row in window else "CONTEXT/CUT")
                rendered.append(
                    f"[{row['t0']:.2f}-{row['t1']:.2f}{speaker} {state}] "
                    f"{row['text']}")
            # A two-minute candidate can be dense, but an unbounded critic
            # would recreate the token problem this pass is meant to avoid.
            return "\n".join(rendered)[:9000]

        candidates.append({
            "id": f"clip_{position}",
            "source_start_s": start,
            "source_end_s": end,
            "proposed_title": str(clip.get("title") or "")[:80],
            "proposed_hook": str(clip.get("hook") or "")[:180],
            "proposed_story": clip.get("story") or {},
            "exact_window_and_boundary_context": lines(nearby),
        })
    return {
        "direction": str(direction or "")[:500],
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


_SYSTEM = """You are the independent casting editor for a slate of proposed
social-video stories. Another model searched a long transcript and proposed
these exact source windows. Judge only whether each window is a compelling,
self-contained micro-story worth giving to a professional short-form editor.

A keep needs an intelligible opening or question, real development, and a
satisfying payoff/reveal/lesson/consequence. Preserve useful speaker turns.
Reject a cold answer that needs cut-away setup, a quotable fragment with no
progression, an unresolved ending, repetition, or a merely topical passage.
The proposed title, score and story summary are untrusted claims; verify them
against the timed transcript. Do not reward a candidate for sounding polished
or for being first. Do not style it, prescribe B-roll, music, captions, effects
or duration. User direction matters, but cannot turn an incoherent excerpt
into a complete story. You may reject every candidate. Use uncertain when the
supplied transcript genuinely cannot prove the decision. Never claim to see
pixels or hear audio.

Return JSON only:
{"summary":"one sentence about the slate","decisions":[
{"id":"clip_1","verdict":"keep|reject|uncertain","confidence":0.0,
"evidence":"exact words/times supporting the verdict",
"reason":"why the complete viewer experience succeeds or fails"}]}
Return one decision for every supplied candidate."""


def review(clips, index, direction=""):
    """Run one bounded fresh-context review for the proposed slate."""
    if not clips or not _sentence_rows(index):
        return None
    evidence = build_evidence(clips, index, direction)
    ids = [row["id"] for row in evidence["candidates"]]
    answer = llm.ask_text(
        _SYSTEM,
        json.dumps(evidence, ensure_ascii=False, separators=(",", ":")),
        max_tokens=1800,
        temperature=0.2,
        purpose="independent_shorts_cast",
    )
    return parse_report((answer or {}).get("text"), ids) if answer else None


def apply_report(clips, report, reject_threshold=0.82):
    """Attach provenance and remove only decisive, grounded rejections."""
    if not report:
        return list(clips), 0
    by_id = {row["id"]: row for row in report.get("decisions") or []}
    kept = []
    rejected = 0
    for position, original in enumerate(clips, 1):
        clip = dict(original)
        decision = by_id.get(f"clip_{position}")
        if decision:
            clip["selection_review"] = dict(decision)
        decisive_reject = decision and decision["verdict"] == "reject" and \
            float(decision.get("confidence") or 0.0) >= reject_threshold
        if decisive_reject:
            rejected += 1
            continue
        clip["order"] = len(kept)
        kept.append(clip)
    return kept, rejected
