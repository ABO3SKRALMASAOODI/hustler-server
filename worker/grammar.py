"""The grammar library at runtime — how "just edit it" gets taste.

Round 82d measured 16 exemplar edits the owner considers strong
(worker/grammars/, built by tools/measure_reference.py) and distilled them
into FAMILY grammars: identity, rules, and a measurable rubric each. This
module is the runtime half: classify what a user's FOOTAGE can become, and
hand the agent the matching family's rules as the house style.

Those exemplars are references, not universal laws. Their muted grade and
speech-led rhythm are useful inside the families that exhibited them, but are
wrong defaults for music videos, gameplay, product UI, nature and other
formats. Runtime classification therefore opts into a family only from
measured evidence and declines when the evidence is ambiguous.

The block is CONTEXT, not command: it tells the agent what this footage
most wants to become when the user gave no brief ("edit it", "make it
viral"), and steps aside the moment the user asks for anything specific.
The user's words always win — that rule is printed inside the block itself.
"""

import bisect
import json
import os

_GRAMMAR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "grammars")
_cache = None


def library():
    """{slug: grammar dict} for every family doc in worker/grammars/.
    corpus.json is the exemplar manifest, not a grammar — skipped."""
    global _cache
    if _cache is None:
        out = {}
        try:
            for fn in sorted(os.listdir(_GRAMMAR_DIR)):
                if not fn.endswith(".json") or fn == "corpus.json":
                    continue
                try:
                    with open(os.path.join(_GRAMMAR_DIR, fn)) as f:
                        doc = json.load(f)
                    if doc.get("slug"):
                        out[doc["slug"]] = doc
                except Exception as e:
                    print(f"[grammar] {fn} unreadable: {e}", flush=True)
        except FileNotFoundError:
            pass
        _cache = out
    return _cache


# A vertical/square program this short is a REEL whatever its source was.
# Matches taste.REEL_MAX_S and the podcast_reel editorial contract.
REEL_MAX_S = 120.0


def _speech_seconds(words):
    return sum(max(0.0, float(w.get("t1", 0)) - float(w.get("t0", 0)))
               for w in words if isinstance(w, dict))


def _shot_span(shot):
    start = shot.get("start", shot.get("t0", 0))
    end = shot.get("end", shot.get("t1", 0))
    return float(start or 0), float(end or 0)


def _merged_spans(spans):
    """Sorted, disjoint union of (start, end) spans; spans of 1ms or less
    are dropped (nothing can overlap them by more than 1ms)."""
    out = []
    for a, b in sorted((float(a), float(b)) for a, b in spans):
        if b - a <= .001:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def program_shape(index, edl=None, shorts_child=False):
    """Measured shape of the KEPT program (falls back to the source).

    A Shorts child shares its parent's full index: classifying that index
    called a 45-second reel cut from a 60-minute two-person podcast a
    "long-form conversation" and handed it a restraint grammar. Duration,
    speech coverage, shot density, speakers and output aspect are measured
    from what survives the cut whenever an EDL is supplied.
    """
    index = index or {}
    video = index.get("video") or {}
    src_dur = float(video.get("duration") or 0)
    words = [w for w in index.get("words") or [] if isinstance(w, dict)]
    shots = [s for s in index.get("shots") or [] if isinstance(s, dict)]
    src_speakers = int(index.get("speakers") or 0)
    shape = {
        "shorts_child": bool(shorts_child), "from_program": False,
        "duration": src_dur, "source_duration": src_dur,
        "speech_s": _speech_seconds(words), "shots": len(shots),
        "speakers": src_speakers, "source_speakers": src_speakers,
        "vertical": False, "square": False,
    }
    if isinstance(edl, dict) and edl.get("keep") is not None:
        try:
            from timeline import Timeline
            tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [],
                          edl.get("speed") or [])
            out = float(tl.out_duration or 0)
        except Exception:
            tl, out = None, 0.0
        if tl is not None and out > 0:
            # Membership by bisection over the merged keep spans: a long
            # podcast with hundreds of silence cuts has ~10k words and ~1k
            # shots, and this runs on every state block and review, so no
            # per-word or per-shot linear segment scan.
            segs = _merged_spans(tl.segs)
            starts = [a for a, _b in segs]
            ends = [b for _a, b in segs]

            def kept_mid(word):
                mid = (float(word.get("t0", 0)) + float(word.get("t1", 0))) / 2
                i = bisect.bisect_right(starts, mid + 1e-6) - 1
                return i >= 0 and mid <= segs[i][1] + 1e-6

            def kept_shot(shot):
                # The first span ending after the shot starts is the only
                # one that can overlap it by more than 1ms (spans are
                # disjoint, sorted and longer than 1ms).
                s0, s1 = _shot_span(shot)
                j = bisect.bisect_right(ends, s0 + .001)
                return j < len(segs) and \
                    min(s1, segs[j][1]) > max(s0, segs[j][0]) + .001
            kept = [w for w in words if kept_mid(w)]
            kept_shots = [s for s in shots if kept_shot(s)]
            labelled = {w.get("speaker") for w in kept
                        if w.get("speaker") is not None}
            shape.update({
                "from_program": True, "duration": out,
                "speech_s": _speech_seconds(kept),
                "shots": len(kept_shots) if shots else 0,
                "speakers": len(labelled) if labelled else src_speakers,
            })
    ratio = str(((edl or {}).get("frame") or {}).get("ratio") or "")
    w = h = None
    if ratio and ratio != "source" and ":" in ratio:
        try:
            w, h = (float(x) for x in ratio.split(":"))
        except (TypeError, ValueError):
            w = h = None
    if not (w and h):
        w, h = video.get("width"), video.get("height")
    try:
        w, h = float(w or 0), float(h or 0)
    except (TypeError, ValueError):
        w = h = 0.0
    if w and h:
        shape["vertical"] = h > w * 1.05
        shape["square"] = abs(w - h) <= w * .05
    shape["reel"] = bool(shape["shorts_child"] or (
        (shape["vertical"] or shape["square"])
        and 0 < shape["duration"] <= REEL_MAX_S))
    return shape


def _source_conversation(index):
    """The SOURCE is a conversation/long talk (diarized speakers, or a long
    dense-speech recording with few shots) — what a podcast reel is cut from."""
    video = index.get("video") or {}
    dur = float(video.get("duration") or 0)
    if dur <= 0:
        return False
    words = index.get("words") or []
    cov = _speech_seconds(words) / dur
    shots_per_min = len(index.get("shots") or []) / (dur / 60.0)
    return cov >= 0.30 and (int(index.get("speakers") or 0) >= 2 or
                            (dur >= 8 * 60 and shots_per_min <= 12))


def classify(index, edl=None, shorts_child=False, shape=None):
    """(family_slug, reason) for what this FOOTAGE can become, or
    (None, reason) when no family fits confidently.

    Deliberately coarse and honest, decided by measured signals only
    (speech coverage, shot density, duration, output aspect). A wrong
    confident guess costs more than no guess — the agent still has eyes
    and the user still has words.

    A REEL (a Shorts child, or a vertical/square program of REEL_MAX_S or
    less) is its own program, so its KEPT program is measured (see
    program_shape): a speech-led reel cut from a conversation, with two
    speakers, or any speech-led Shorts child, is a ``podcast-reel``. Every
    other edit is classified from the SOURCE, exactly as before: the house
    style describes the footage, and a landscape podcast must keep its
    family while the cut is trimmed below the long-form threshold.

    ``shape`` is a precomputed program_shape(index, edl, shorts_child) for
    callers that already measured it (it is the expensive part).
    """
    if not index:
        return None, "no index"
    program = shape
    if not (isinstance(program, dict) and "duration" in program
            and "speech_s" in program):
        program = program_shape(index, edl, shorts_child=shorts_child)
    reel = bool(program.get("reel"))
    shape = program
    if not reel and program.get("from_program"):
        shape = program_shape(index)
    dur = float(shape["duration"] or 0)
    if dur < 5:
        return None, "footage too short to classify"
    speech_cov = shape["speech_s"] / dur if dur else 0.0
    if reel and speech_cov >= 0.30 and (
            shape.get("shorts_child") or int(shape.get("speakers") or 0) >= 2
            or _source_conversation(index)):
        origin = ("a Shorts child of a longer recording"
                  if shape.get("shorts_child") else
                  "cut from a conversation/long talk")
        return ("podcast-reel",
                f"{dur:.0f}s {'vertical' if shape.get('vertical') else 'short'}"
                f" program, speech covers {speech_cov:.0%}, {origin} — a "
                "short-form podcast reel, not a long-form conversation")
    shots_per_min = shape["shots"] / (dur / 60.0) if dur else 0.0

    speakers = int(shape["speakers"] or 0)
    n_shots = int(shape["shots"] or 0)

    # A conversation is not a creator promo. Preserve complete exchanges and
    # speaker logic instead of spraying kinetic typography over an hour-long
    # interview. Whisper-only indexes may not diarize, so duration + dense
    # speech + low shot count is a conservative second route.
    if speech_cov >= 0.30 and (speakers >= 2 or
                              (dur >= 8 * 60 and shots_per_min <= 12)):
        return ("podcast-conversation",
                f"{speakers or 'multiple/unknown'} speaker evidence, speech "
                f"covers {speech_cov:.0%} of {dur / 60:.0f} minutes across "
                f"{n_shots} shot(s) — long-form conversation")
    if speech_cov >= 0.35 and dur <= 180 and shots_per_min <= 20:
        # one (or few) continuous takes of someone talking — the corpus's
        # dominant family, and the footage every creator-promo skin starts
        # from
        return ("talking-head-promo",
                f"speech covers {speech_cov:.0%} of {dur:.0f}s across "
                f"{n_shots} shot(s) — a talking take")
    if speech_cov >= 0.30 and 45 <= dur <= 10 * 60 \
            and shots_per_min > 4:
        return ("narrative-vlog",
                f"speech covers {speech_cov:.0%} of {dur:.0f}s across "
                f"{n_shots} shots — narrated story footage")
    if speech_cov < 0.15 and n_shots >= 6:
        return ("voiceover-montage",
                f"almost no speech ({speech_cov:.0%}) and {n_shots} "
                "shots — montage material (needs a voiceover or music to "
                "carry it)")
    return None, (f"mixed signals (speech {speech_cov:.0%}, "
                  f"{shots_per_min:.0f} shots/min) — no confident family")


def _fmt(v, indent=0):
    pad = "  " * indent
    if isinstance(v, dict):
        return "\n".join(f"{pad}{k}: " + (_fmt(x, indent + 1).lstrip()
                                          if not isinstance(x, (dict, list))
                                          else "\n" + _fmt(x, indent + 1))
                         for k, x in v.items())
    if isinstance(v, list):
        return "\n".join(f"{pad}- " + _fmt(x, indent + 1).lstrip()
                         for x in v)
    return f"{pad}{v}"


def plan_block(index, edl=None, shorts_child=False, shape=None):
    """The HOUSE STYLE section of the agent's project state, or "" when no
    family classifies. Compact on purpose — identity, the rules that bind,
    the rubric the self-review will score against, and the user-wins rule.
    ``shape``: an already-measured program_shape (see classify)."""
    slug, reason = classify(index, edl, shorts_child=shorts_child,
                            shape=shape)
    if not slug:
        return ""
    doc = library().get(slug)
    if not doc:
        return ""
    lines = [
        "HOUSE STYLE HYPOTHESIS (measured from a matching reference family "
        "— use it when the user asks for an edit WITHOUT a specific brief; their "
        "explicit instructions ALWAYS override it):",
        f"This footage classifies as: {slug} ({reason}).",
        f"What that is: {doc.get('identity', '')}",
    ]
    rules = doc.get("rules") or doc.get("shared_rules")
    if rules:
        lines.append("The rules that matter:")
        lines.append(_fmt(rules, 1))
    rubric = doc.get("rubric")
    if rubric:
        lines.append("Reference-family rubric (apply only where it suits this "
                     "footage and the user's goal; it will be checked):")
        lines.append(_fmt(rubric, 1))
    lines.append(
        "Rhythm and color are FORMAT decisions, not global laws. For "
        "dialogue-led footage, speech/story turns normally drive cuts and "
        "text; for montage, music performance, gameplay and sports, musical "
        "phrases, beats and visible action may drive them. Preserve natural "
        "or product color unless this family/brief earns a stylized grade. "
        "For speech-carried footage that genuinely calls for kinetic type, "
        "add_kinetic_text can choreograph phrases in one pass; do not apply "
        "it merely because speech exists.")
    return "\n".join(lines)
