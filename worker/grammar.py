"""The grammar library at runtime — how "just edit it" gets taste.

Round 82d measured 16 exemplar edits the owner considers perfect
(worker/grammars/, built by tools/measure_reference.py) and distilled them
into FAMILY grammars: identity, rules, and a measurable rubric each. This
module is the runtime half: classify what a user's FOOTAGE can become, and
hand the agent the matching family's rules as the house style.

Two findings from that corpus govern everything here:
  * every exemplar is heavily desaturated with ONE accent color
    (saturation 0.004-0.088 across all 16 — the single most universal
    taste marker), and
  * nothing beat-syncs: typography, cuts and props follow SPEECH and story
    beats; music is a bed. So the grammars are driven by the transcript —
    which is exactly the signal the index measures best.

The block is CONTEXT, not command: it tells the agent what this footage
most wants to become when the user gave no brief ("edit it", "make it
viral"), and steps aside the moment the user asks for anything specific.
The user's words always win — that rule is printed inside the block itself.
"""

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


def classify(index):
    """(family_slug, reason) for what this FOOTAGE can become, or
    (None, reason) when no family fits confidently.

    Deliberately coarse and honest: three families, decided by measured
    signals only (speech coverage, shot density, duration). A wrong
    confident guess costs more than no guess — the agent still has eyes
    and the user still has words.
    """
    if not index:
        return None, "no index"
    video = index.get("video") or {}
    dur = float(video.get("duration") or 0)
    if dur < 5:
        return None, "footage too short to classify"
    words = index.get("words") or []
    speech_s = sum(max(0.0, float(w.get("t1", 0)) - float(w.get("t0", 0)))
                   for w in words if isinstance(w, dict))
    speech_cov = speech_s / dur if dur else 0.0
    shots = index.get("shots") or []
    shots_per_min = len(shots) / (dur / 60.0) if dur else 0.0

    if speech_cov >= 0.35 and shots_per_min <= 20:
        # one (or few) continuous takes of someone talking — the corpus's
        # dominant family, and the footage every creator-promo skin starts
        # from
        return ("talking-head-promo",
                f"speech covers {speech_cov:.0%} of {dur:.0f}s across "
                f"{len(shots)} shot(s) — a talking take")
    if speech_cov >= 0.35 and dur >= 60:
        return ("narrative-vlog",
                f"speech covers {speech_cov:.0%} of {dur:.0f}s across "
                f"{len(shots)} shots — narrated story footage")
    if speech_cov < 0.15 and len(shots) >= 6:
        return ("voiceover-montage",
                f"almost no speech ({speech_cov:.0%}) and {len(shots)} "
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


def plan_block(index):
    """The HOUSE STYLE section of the agent's project state, or "" when no
    family classifies. Compact on purpose — identity, the rules that bind,
    the rubric the self-review will score against, and the user-wins rule."""
    slug, reason = classify(index)
    if not slug:
        return ""
    doc = library().get(slug)
    if not doc:
        return ""
    lines = [
        "HOUSE STYLE (measured from real top-performing edits — use it "
        "when the user asks for an edit WITHOUT a specific brief; their "
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
        lines.append("Build so these hold (they will be checked):")
        lines.append(_fmt(rubric, 1))
    lines.append(
        "Two corpus-wide laws: desaturate toward a muted base with ONE "
        "accent color, and sync text/cuts/props to the SPOKEN words and "
        "story beats — never to musical beats (music stays a bed). For "
        "speech-carried footage, add_kinetic_text is the one-call way to "
        "put the words on screen in this style.")
    return "\n".join(lines)
