"""Acoustic candidate comparison for the music workbench.

This is not a claim that a language model heard the song.  It converts the
actual waveform into stable editorial evidence (tempo, pulse confidence,
dynamics, brightness, bass and dialogue-band density), then scores those
measurements against the director's brief.  The model still owns taste and
can override the ranking; it no longer chooses from a title alone.
"""

import json
import re


def _has(text, *phrases):
    text = str(text or "").casefold()
    return any(phrase in text for phrase in phrases)


def _closeness(value, target, width):
    if value is None:
        return -1.0
    return max(-2.0, 2.0 - abs(float(value) - target) / width)


def judge(item, analysis, brief, *, speech_led=False):
    """Return a transparent score and reasons for one measured candidate."""
    title = str(item.get("title") or "untitled")
    text = " ".join([str(brief or ""), title]).casefold()
    bpm = analysis.get("bpm")
    conf = float(analysis.get("bpm_conf") or 0.0)
    dynamic = float(analysis.get("dynamic_range_db") or 0.0)
    centroid = float(analysis.get("spectral_centroid_hz") or 0.0)
    mid = float(analysis.get("midband_ratio") or 0.0)
    bass = float(analysis.get("bass_ratio") or 0.0)
    score, reasons = 0.0, []

    fast = _has(text, "fast", "upbeat", "energetic", "driving", "hype",
                "phonk", "action", "sport", "punchy")
    calm = _has(text, "slow", "calm", "gentle", "ambient", "meditative",
                "soft", "minimal", "intimate", "lofi")
    dark = _has(text, "dark", "moody", "ominous", "noir", "tense")
    bright = _has(text, "bright", "happy", "uplifting", "optimistic",
                  "playful")
    cinematic = _has(text, "cinematic", "emotional", "dramatic", "trailer",
                      "story", "documentary")

    if fast:
        tempo_score = _closeness(bpm, 130.0, 25.0)
        score += tempo_score + conf * 2.0
        reasons.append(f"{bpm or 'no stable'} BPM for a driving brief")
    elif calm:
        tempo_score = _closeness(bpm, 82.0, 25.0)
        score += tempo_score + (1.0 - conf) * 0.5
        reasons.append(f"{bpm or 'free-time'} BPM for a restrained brief")
    else:
        score += conf
        reasons.append(f"pulse confidence {conf:.2f}")

    if dark and centroid:
        score += _closeness(centroid, 1500.0, 900.0)
        reasons.append(f"darker spectral center {centroid:.0f}Hz")
    if bright and centroid:
        score += _closeness(centroid, 3200.0, 1200.0)
        reasons.append(f"brighter spectral center {centroid:.0f}Hz")
    if _has(text, "bass", "phonk", "impact", "heavy", "power"):
        score += min(2.5, bass * 8.0)
        reasons.append(f"bass energy {bass:.2f}")
    if cinematic:
        score += _closeness(dynamic, 12.0, 7.0)
        reasons.append(f"dynamic range {dynamic:.1f}dB")

    vocal_title = bool(re.search(r"\b(vocal|lyrics?|singer|feat\.?|song)\b",
                                 title.casefold()))
    if speech_led:
        # Mid-band density is a masking-risk proxy, never called vocals.
        score += max(-3.0, 2.0 - mid * 6.0)
        reasons.append(f"dialogue-band density {mid:.2f} (lower masks less)")
        if vocal_title:
            score -= 3.0
            reasons.append("title suggests vocals competing with speech")
    elif vocal_title:
        reasons.append("title suggests a vocal-led track")

    # Avoid pathological/flat files without inventing a subjective verdict.
    energy = analysis.get("energy") or []
    if energy and len(energy) >= 20:
        body = sorted(float(x) for x in energy)[:int(len(energy) * 0.98)]
        mid_db = body[len(body) // 2]
        spread = body[int(len(body) * 0.9)] - body[int(len(body) * 0.1)]
        if mid_db < -40.0 and spread < 6.0:
            score -= 20.0
            reasons.append("waveform appears broken/near-flat")

    return {
        "id": item.get("id"), "title": title,
        "provider": item.get("provider"), "score": round(score, 2),
        "bpm": bpm, "bpm_conf": conf,
        "dynamic_range_db": dynamic,
        "spectral_centroid_hz": centroid,
        "midband_ratio": mid, "bass_ratio": bass,
        "reasons": reasons,
    }


def rank(candidates, brief, *, speech_led=False):
    rows = [judge(item, analysis, brief, speech_led=speech_led)
            for item, analysis in candidates]
    return sorted(rows, key=lambda row: (-row["score"], row["title"],
                                         str(row.get("id") or "")))


def audition_windows(duration_s, span_s=6.0):
    """Representative opening/body/ending windows without pretending one
    arbitrary middle excerpt represents a whole track."""
    try:
        duration = max(0.0, float(duration_s))
        span = max(1.0, min(float(span_s), duration))
    except (TypeError, ValueError):
        return []
    if duration <= 0:
        return []
    last = max(0.0, duration - span)
    starts = [0.0, last * .42, last]
    out = []
    for start in starts:
        window = (round(start, 2), round(min(duration, start + span), 2))
        if window[1] <= window[0]:
            continue
        if not any(abs(window[0] - old[0]) < max(1.0, span * .45)
                   for old in out):
            out.append(window)
    return out


def listener_choice(answer, candidate_ids):
    """Parse the actual listener's candidate-or-no-music decision."""
    if not isinstance(answer, str):
        return None
    raw = None
    decoder = json.JSONDecoder()
    for i, char in enumerate(answer):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(answer[i:])
        except (ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            raw = value
            break
    if raw is None:
        return None
    allowed = {str(value) for value in candidate_ids}
    choice = str(raw.get("choice") or "").strip()
    reason = " ".join(str(raw.get("reason") or "").split())[:360]
    if choice.casefold() in {"none", "no music", "silence", "dry"}:
        return {"choice": None, "abstain": True, "reason": reason}
    if choice in allowed:
        return {"choice": choice, "abstain": False, "reason": reason}
    return None
