"""Evidence-based ranking for downloaded sound-effect candidates.

This is deliberately a waveform judge, not a semantic-listening claim.  The
catalog title says what a recording purports to be; measured transient shape
says whether it behaves like the editorial event requested.
"""

import json
import math
import re


def _words(text):
    return set(re.findall(r"[a-z0-9]+", str(text or "").lower()))


def _near(value, target, spread):
    return max(-2.0, 1.0 - abs(float(value) - target) / max(spread, 1e-6))


def judge(hit, analysis, purpose):
    intent = _words(purpose)
    title = _words(hit.get("title"))
    score = 0.0
    reasons = []
    duration = float(analysis.get("active_duration_s")
                     or analysis.get("duration_s") or 0.0)
    attack = float(analysis.get("attack_s") or 0.0)
    peak_pos = float(analysis.get("peak_position") or 0.0)
    crest = float(analysis.get("crest_db") or 0.0)
    bass = float(analysis.get("bass_ratio") or 0.0)
    centroid = float(analysis.get("spectral_centroid_hz") or 0.0)
    events = int(analysis.get("strong_event_count") or 0)
    lead = float(analysis.get("leading_silence_s") or 0.0)

    overlap = {w for w in intent & title if len(w) > 2}
    score += min(3.0, len(overlap) * 0.8)
    if overlap:
        reasons.append("catalog label matches " + ", ".join(sorted(overlap)[:3]))
    if lead <= 0.06:
        score += 1.0
        reasons.append("starts cleanly")
    else:
        score -= min(4.0, lead * 5.0)
        reasons.append(f"{lead:.2f}s leading silence")
    if events == 1:
        score += 1.2
        reasons.append("one clean event")
    elif events > 2 and not intent.intersection({"ambience", "loop", "texture"}):
        score -= min(4.0, (events - 2) * 0.8)
        reasons.append(f"contains {events} strong events")

    if intent.intersection({"click", "tap", "snap", "shutter", "pop", "beep"}):
        score += 1.8 * _near(duration, 0.45, 0.75)
        score += 1.5 if attack <= 0.12 else -min(2.5, attack * 3.0)
        score += min(1.5, crest / 8.0)
        reasons.append(f"{duration:.2f}s tight event, {attack:.2f}s attack")
    elif intent.intersection({"riser", "rise", "swell", "build"}):
        score += 1.4 * _near(math.log(max(duration, .05)), math.log(2.5), 1.0)
        score += 2.5 if peak_pos >= .65 else -2.0 * (.65 - peak_pos)
        score += min(1.5, attack / 1.2)
        reasons.append(f"build peaks at {peak_pos:.0%} of the sound")
    elif intent.intersection({"impact", "hit", "boom", "thud", "slam"}):
        score += 1.5 * _near(duration, 0.9, 1.3)
        score += 1.8 if attack <= .15 else -min(2.0, attack * 2.0)
        score += min(2.0, bass * 7.0)
        score += min(1.0, crest / 10.0)
        reasons.append(f"fast attack with {bass:.0%} bass energy")
    elif intent.intersection({"whoosh", "swoosh", "swish", "transition"}):
        score += 1.8 * _near(duration, 1.1, 1.2)
        score += 1.2 if .12 <= peak_pos <= .85 else -1.0
        score += 1.0 if 700 <= centroid <= 6500 else -0.5
        reasons.append(f"{duration:.2f}s sweep, peak at {peak_pos:.0%}")
    else:
        # General-purpose editorial accents should be concise, start promptly,
        # and avoid behaving like an accidental sound pack.
        score += 1.2 * _near(duration, 1.0, 2.0)
        score += min(1.0, crest / 10.0)
        reasons.append(f"{duration:.2f}s active event")

    return {
        "id": hit.get("id"),
        "title": hit.get("title") or "untitled",
        "score": round(score, 3),
        "reasons": reasons,
        "analysis": analysis,
    }


def rank(measured, purpose):
    rows = [judge(hit, analysis, purpose) for hit, analysis in measured]
    return sorted(rows, key=lambda row: (-row["score"], str(row["id"])))


def listener_choice(answer, candidate_ids, allow_none=True):
    """Parse an actual-audio supervisor's choice without guessing.

    Structured JSON is preferred, with a narrow legacy text fallback. An
    ambiguous answer returns None so the caller can retain deterministic
    waveform ranking; it must never pick whichever id happened to be named
    first in a comparison sentence.
    """
    if not isinstance(answer, str) or not answer.strip():
        return None
    allowed = {str(value): str(value) for value in candidate_ids or []}
    folded = {key.casefold(): value for key, value in allowed.items()}
    raw = None
    decoder = json.JSONDecoder()
    for index, char in enumerate(answer):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(answer[index:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            raw = value
            break
    if raw is not None:
        choice = str(raw.get("choice") or "").strip()
        reason = " ".join(str(raw.get("reason") or "").split())[:420]
        if allow_none and choice.casefold() in {
                "none", "no sfx", "silence", "keep silence", "dry"}:
            return {"choice": None, "abstain": True, "reason": reason}
        selected = folded.get(choice.casefold())
        if selected:
            return {"choice": selected, "abstain": False, "reason": reason}
        return None

    mentions = []
    for candidate_id in allowed:
        boundary = (r"(?<![A-Za-z0-9:_-])" + re.escape(candidate_id) +
                    r"(?![A-Za-z0-9:_-])")
        if re.search(boundary, answer, flags=re.IGNORECASE):
            mentions.append(candidate_id)
    if len(mentions) == 1:
        return {"choice": mentions[0], "abstain": False,
                "reason": " ".join(answer.split())[:420]}
    none_pattern = (
        r"(?:^|\b(?:choice|choose|winner|best|recommend(?:ation)?)\s*[:=\-]?\s*)"
        r"(?:none|no\s+sfx|silence|keep\s+(?:it\s+)?dry|keep\s+silence)\b")
    if allow_none and not mentions and re.search(
            none_pattern, answer.strip(), flags=re.IGNORECASE):
        return {"choice": None, "abstain": True,
                "reason": " ".join(answer.split())[:420]}
    return None
