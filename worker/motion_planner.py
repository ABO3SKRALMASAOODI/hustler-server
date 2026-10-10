"""suggest_motion_beats: a premium beat sheet from the kept transcript.

Top short-form editors interrupt the scroll in the first half-second and bind
2-4 hero moments to the exact words that carry the story (numbers, lists,
contrasts, the stressed claim, the call to action). This module finds those
anchors in the CURRENT program (kept words on the output clock, cuts, long
holds) and returns concrete, timed suggestions — template, program time,
extracted values and the reason — so the editor spends its turns on taste
and copy instead of searching the transcript.

ZOOMS AND SOUND EFFECTS ARE OPTIONAL, NEVER RULES (owner, Oct 10 2026): the
sheet suggests graphics only. Camera candidates (``camera=True``) and sound
candidates (``sounds=True``) are opt-in, and even then they are a short list
of moments that could earn one — never a landing per cut, a push per hold or
a sound per beat.

It never writes the EDL and never invents copy or numbers: values come only
from spoken words, and text fields are left for the editor to author
(``text_hint`` quotes the transcript it should paraphrase faithfully).
"""

import json
import re

from timeline import Timeline

_NUM_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "hundred": 100, "thousand": 1000, "million": 1e6, "billion": 1e9, "trillion": 1e12,
}
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9,
          "bn": 1e9, "b": 1e9, "trillion": 1e12, "t": 1e12}
_ORDINALS = ("first", "second", "third", "fourth", "fifth", "number one",
             "number two", "number three")
_CONTRAST = re.compile(r"\b(vs\.?|versus|compared to|instead of|rather than|"
                       r"more than|less than|better than|worse than|not .{1,24} but)\b", re.I)
_CTA = re.compile(r"\b(comment|follow|subscribe|save this|link in (?:my )?bio|share this|"
                  r"dm me|send it to)\b", re.I)
_STRIP = "\"'“”‘’.,!?;:…()[]"


def _clean(tok):
    return str(tok or "").strip().strip(_STRIP)


def _number_at(words, i):
    """(value, prefix, suffix, consumed) for a spoken number at words[i]."""
    raw = _clean(words[i]["w"]).lower()
    m = re.fullmatch(r"([$€£])?(\d[\d,]*(?:\.\d+)?)(%|x|k|m|bn|b|t)?", raw)
    if m:
        cur, num, unit = m.group(1) or "", m.group(2), (m.group(3) or "")
        val = float(num.replace(",", ""))
        prefix, suffix = cur, ""
        if unit == "%":
            suffix = "%"
        elif unit == "x":
            suffix = "x"
        elif unit in _SCALE:
            val *= _SCALE[unit]
        consumed = 1
        if i + 1 < len(words):
            nxt = _clean(words[i + 1]["w"]).lower()
            if nxt in ("percent", "per", "%"):
                suffix, consumed = "%", 2
            elif nxt in _SCALE and len(nxt) > 1:
                val *= _SCALE[nxt]
                consumed = 2
            elif nxt in ("times", "x"):
                suffix, consumed = "x", 2
            elif nxt in ("dollars", "bucks"):
                prefix, consumed = "$", 2
        return val, prefix, suffix, consumed
    if raw in _NUM_WORDS and raw not in ("one", "zero"):
        val = float(_NUM_WORDS[raw])
        consumed = 1
        if i + 1 < len(words):
            nxt = _clean(words[i + 1]["w"]).lower()
            if nxt in _SCALE and len(nxt) > 1:
                val *= _SCALE[nxt]
                consumed = 2
            elif nxt in ("percent",):
                return val, "", "%", 2
        return val, "", "", consumed
    return None


def _display_value(val):
    """(to, suffix_scale) — 2,300,000 -> (2.3, 'M')."""
    for div, lab in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(val) >= div:
            v = val / div
            return (round(v, 1) if v % 1 else int(v)), lab
    return (round(val, 2) if val % 1 else int(val)), ""


def _sentences(words):
    out, cur = [], []
    for w in words:
        cur.append(w)
        if str(w["w"]).rstrip("\"'”’")[-1:] in ".!?":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


# Library sounds a beat COULD take when the editor asked for sound
# candidates (sounds=True): owner-approved recordings only, each optional,
# and repeating cues (a tick per count or per item) stay silent. A podcast
# short carries 1-2 sounds at most, never a reflexive opening whoosh, never
# a bright sound on the onset of the word a graphic shows and never a pun
# on the spoken word (sfx_placement).
BEAT_SOUNDS = {
    "hook": "silent by default (no reflexive opening whoosh); whoosh_soft_1 (sfx=true) only if the title has a real entrance that earns it",
    "number": "silent by default; at most one tick_1 on a visual landing in the pause after the spoken number (something on screen must change there), never on its onset (a ding there masks it); the count itself stays silent",
    "number_cluster": "optional: pop_1 on the last value only (a sound per value repeats)",
    "list": "silent (a tick per item repeats within ~3 s); at most pop_1 on the last item",
    "contrast": "optional: swish_1 on the swap",
    "hero_word": "silent unless it is the payoff (impact_1, once per short)",
    "cta": "optional: click_1 on the visible press (sfx=true)",
}
# Camera candidates (camera=True) are a short list, never one per cut or
# hold: about one per this many program seconds, at least one.
CAMERA_EVERY_S = 15.0
# A hold this long with nothing designed on it is reported as a diagnostic.
LONG_HOLD_S = 3.5


def plan(edl, index, density="premium", camera=False, sounds=False):
    """Pure planner: returns a dict with beats and pacing diagnostics."""
    tl = Timeline(edl.get("keep") or [], edl.get("inserts") or [], edl.get("speed") or [])
    src_words = [w for w in (index.get("words") or [])
                 if not (w.get("filler") if isinstance(w, dict) else False)]
    words = tl.kept_words(src_words)
    prog = float(tl.out_duration)
    beats = []
    min_gap = {"premium": 2.2, "balanced": 3.5, "minimal": 6.0}.get(density, 2.2)
    taken = []

    def free(t, span=1.2):
        return all(abs(t - u) >= min(span, min_gap) for u in taken)

    def add(beat):
        beats.append(beat)
        taken.append(beat["at"])

    # 1. hook interrupt
    first = _sentences(words[:40])[0] if words else []
    hint = " ".join(w["w"] for w in first[:14])
    add({"at": 0.0, "template": "hook_title", "kind": "hook",
         "end": round(min(prog, max(1.8, (first[-1]["t1"] if first else 2.4) + 0.2), 3.0), 2),
         "params": {"text": None, "y": 0.2},
         "text_hint": hint,
         "why": "pattern interrupt + hook line on screen within 0.6 s (write a faithful 3-7 word hook; star one accent word)"})
    # 2. numbers -> counter (a cluster of numbers within ~5 s -> one stat stack)
    found = []
    i = 0
    while i < len(words):
        got = _number_at(words, i)
        if got:
            val, prefix, suffix, consumed = got
            if val not in (0, 1):
                found.append((i, val, prefix, suffix, consumed))
            i += consumed
            continue
        i += 1
    clusters = []
    for f in found:
        if clusters and float(words[f[0]]["t0"]) - float(words[clusters[-1][-1][0]]["t0"]) <= 5.0:
            clusters[-1].append(f)
        else:
            clusters.append([f])
    for cl in clusters:
        i0 = cl[0][0]
        t = float(words[i0]["t0"])
        if t >= prog - 0.6:
            continue
        last_i = cl[-1][0] + cl[-1][4]
        ctx_words = " ".join(w["w"] for w in words[max(0, i0 - 6): min(len(words), last_i + 8)])
        if len(cl) == 1:
            _i, val, prefix, suffix, consumed = cl[0]
            to, scale = _display_value(val)
            if free(t):
                add({"at": round(max(0.0, t - 0.08), 2), "template": "counter", "kind": "number",
                     "end": round(min(prog, t + 2.2), 2),
                     "params": {"to": to, "prefix": prefix, "suffix": (scale + suffix) or "",
                                "label": None},
                     "text_hint": ctx_words,
                     "why": f"spoken number '{' '.join(w['w'] for w in words[_i:_i + consumed])}' — make it land as a counter (label = what it measures, from the sentence)"})
        else:
            rows = []
            for _i, val, prefix, suffix, consumed in cl:
                to, scale = _display_value(val)
                rows.append({"value": f"{prefix}{to}{scale}{suffix}", "at": round(float(words[_i]["t0"]), 2),
                             "label": None})
            add({"at": round(max(0.0, t - 0.08), 2), "template": "bar_compare", "kind": "number_cluster",
                 "end": round(min(prog, float(words[min(len(words) - 1, last_i)]["t1"]) + 1.8), 2),
                 "params": {"rows": rows},
                 "text_hint": ctx_words,
                 "why": f"{len(cl)} spoken numbers within 5 s — one stat stack/bar comparison revealing each value on its cue (or stacked counters)"})
    # 3. enumerations -> checklist / phrase_build
    lowered = [_clean(w["w"]).lower() for w in words]
    for k, tok in enumerate(lowered):
        if tok in ("first", "firstly") and k + 1 < len(lowered):
            later = [j for j in range(k + 1, min(len(lowered), k + 120))
                     if lowered[j] in ("second", "secondly", "third", "thirdly", "next", "finally")]
            if later:
                t = float(words[k]["t0"])
                if free(t):
                    add({"at": round(t, 2), "template": "checklist", "kind": "list",
                         "end": round(min(prog, float(words[later[-1]]["t1"]) + 1.5), 2),
                         "params": {"items": None},
                         "item_times": [round(float(words[j]["t0"]), 2) for j in [k] + later],
                         "text_hint": " ".join(w["w"] for w in words[k:min(len(words), later[-1] + 8)]),
                         "why": "spoken enumeration — reveal each item on its cue"})
            break
    # 4. contrasts -> versus_split / word_slam
    for s in _sentences(words):
        text = " ".join(w["w"] for w in s)
        m = _CONTRAST.search(text)
        if m and s:
            t = float(s[0]["t0"])
            if free(t, 1.5):
                add({"at": round(t, 2), "template": "versus_split", "kind": "contrast",
                     "end": round(min(prog, float(s[-1]["t1"]) + 0.6), 2),
                     "params": {"left": None, "right": None},
                     "text_hint": text,
                     "why": f"contrast ('{m.group(0)}') — make the two sides visible"})
    # 5. emphasis -> word_slam (spaced)
    emph = {(_clean(w)).lower() for w in ((edl.get("captions") or {}).get("emphasis_words") or [])
            if isinstance(edl.get("captions"), dict)}
    for w in words:
        key = _clean(w["w"]).lower()
        t = float(w["t0"])
        if key in emph and free(t) and 1.0 < t < prog - 1.0:
            add({"at": round(max(0.0, t - 0.03), 2), "template": "word_slam", "kind": "hero_word",
                 "end": round(min(prog, t + 1.2), 2),
                 "params": {"text": _clean(w["w"])},
                 "why": "stressed/emphasis word — hero word on its onset"})
    # 6. CTA
    for s in _sentences(words):
        text = " ".join(w["w"] for w in s)
        m = _CTA.search(text)
        if m and s:
            word = m.group(1).lower()
            tpl = ("comment_cta" if word.startswith("comment") else
                   "follow_cta" if word in ("follow", "subscribe") else
                   "save_cta" if word.startswith("save") else "comment_cta")
            add({"at": round(float(s[0]["t0"]), 2), "template": tpl, "kind": "cta",
                 "end": round(min(prog, float(s[-1]["t1"]) + 1.0), 2),
                 "params": {}, "text_hint": text,
                 "why": "spoken call to action — show it as native UI"})
    beats.sort(key=lambda x: x["at"])
    if sounds:
        for b in beats:
            b["sound"] = BEAT_SOUNDS.get(b["kind"], "silent")
    # 7. long holds: a diagnostic, not a request for a camera move
    events = sorted({round(b["at"], 2) for b in beats}
                    | {round(float(w["t0"]), 2) for w in words[::3]})
    holds = []
    last = 0.0
    for t in events + [prog]:
        if t - last > LONG_HOLD_S:
            holds.append([round(last, 2), round(t, 2)])
        last = t
    # 8. camera candidates — only when asked (camera=True), and a short list
    cam = _camera_candidates(tl, index, beats, holds, prog) if camera else []
    per10 = (len(beats) + len(cam)) / max(prog / 10.0, 0.1)
    return {"program_s": round(prog, 2), "density": density, "beats": beats,
            "camera": cam, "long_holds": holds,
            "designed_events_per_10s": round(per10, 2),
            "note": ("Captions (motion_look) already change every word; these beats add the hero "
                     "layer. Write every text param yourself from the quoted transcript; never "
                     "invent numbers or claims.")}


def _camera_candidates(tl, index, beats, holds, prog):
    """A few moments that COULD earn a camera move, strongest first — never
    one per cut or per hold: about one per CAMERA_EVERY_S of program. Jump
    cuts inside one take are never candidates (a bare jump cut is fine)."""
    out = []
    order = {"number": 0, "hero_word": 1, "contrast": 2}
    for b in sorted((b for b in beats if b["kind"] in order),
                    key=lambda b: (order[b["kind"]], b["at"])):
        out.append({"at": b["at"], "end": round(min(prog, b["end"]), 2), "tool": "add_zoom",
                    "mode": "punch",
                    "why": f"optional: a punch on this {b['kind'].replace('_', ' ')} only if it is "
                           "the line the story turns on (the payoff, the biggest number)"})
    shots = (index or {}).get("shots") or []
    if shots:
        prev_end = None
        for i, (s0, s1) in enumerate(tl.segs):
            at = float(tl.offsets[i])
            if prev_end is not None and \
                    _shot_of(shots, float(s0) + 0.04) != _shot_of(shots, prev_end - 0.04):
                out.append({"at": round(at, 2), "end": round(min(prog, at + 0.45), 2),
                            "tool": "add_zoom", "mode": "landing",
                            "why": "optional: a landing on this camera change only if it opens "
                                   "a new idea (skip angle changes inside one thought)"})
            prev_end = float(s1)
    for a, b in holds:
        if b - a >= 2 * LONG_HOLD_S:
            out.append({"at": round(a + 0.3, 2), "end": round(b, 2), "tool": "add_zoom",
                        "mode": "push_in",
                        "why": f"optional: {b - a:.1f}s hold — a graphic or B-roll beat if the story "
                               "needs one; a slow push_in only if the line builds to something"})
    cap = max(1, int(prog / CAMERA_EVERY_S))
    return out[:cap]


def _shot_of(shots, t):
    for k, sh in enumerate(shots):
        try:
            a = float(sh.get("start", sh.get("t0")))
            b = float(sh.get("end", sh.get("t1")))
        except (TypeError, ValueError, AttributeError):
            continue
        if a - 1e-6 <= t < b + 1e-6:
            return sh.get("id", k)
    return None


def _flag(v):
    return v is True or str(v).strip().lower() in ("true", "1", "yes", "on")


def suggest_motion_beats(ctx, density="premium", camera=False, sounds=False):
    """READ: timed premium beat sheet for the current program."""
    if not getattr(ctx, "has_main_video", True):
        return "REJECTED: needs a transcribed main video (no words to plan from)."
    edl = ctx.latest_edl()["json"]
    camera, sounds = _flag(camera), _flag(sounds)
    try:
        result = plan(edl, ctx.index or {}, density=density, camera=camera, sounds=sounds)
    except Exception as e:  # noqa: BLE001
        return f"Could not plan beats ({str(e)[:160]})."
    lines = [f"Program {result['program_s']}s — {len(result['beats'])} hero beats"
             + (f", {len(result['camera'])} optional camera candidates" if camera else "")
             + f" (~{result['designed_events_per_10s']} designed events per 10 s; captions add more)."]
    for b in result["beats"]:
        p = {k: v for k, v in b["params"].items() if v is not None}
        lines.append(f"- {b['at']:.2f}-{b['end']:.2f}s {b['template']} [{b['kind']}] params={json.dumps(p)}"
                     + (f" item_times={b['item_times']}" if b.get("item_times") else "")
                     + f" — {b['why']}"
                     + (f" | sound: {b['sound']}" if b.get("sound") else "")
                     + (f" | transcript: \"{b['text_hint'][:140]}\"" if b.get("text_hint") else ""))
    for c in result["camera"]:
        lines.append(f"- {c['at']:.2f}-{c['end']:.2f}s {c['tool']} mode={c['mode']} — {c['why']}")
    if result["long_holds"]:
        lines.append(f"Long holds (a graphic or B-roll beat if the story needs one; a steady "
                     f"frame is fine): {result['long_holds']}")
    lines.append(result["note"] + " Place the beats you agree with via add_motion_graphic, one "
                 "call each (graphics are silent by default).")
    if not (camera and sounds):
        lines.append("Zooms and sound effects are optional, never rules: "
                     + ("camera=true lists a few moments that could earn a move; " if not camera else "")
                     + ("sounds=true names the library sound each beat could take; " if not sounds else "")
                     + "zero of either is a fine answer.")
    return "\n".join(lines)


TOOL_SPECS = {
    "suggest_motion_beats": (
        suggest_motion_beats,
        "READ: a timed premium beat sheet of GRAPHICS for the CURRENT program: hook interrupt at "
        "0 s, counters on spoken numbers (values parsed from the words), checklists on "
        "enumerations, versus cards on contrasts, hero word slams on emphasis words, native-UI "
        "CTAs on spoken calls to action — each with program times, template, extracted params "
        "and the transcript to paraphrase — plus long holds as a diagnostic. Zooms and sound "
        "effects are optional, never rules, so both are OFF by default: camera=true adds a short "
        "list of moments that could earn a camera move (never one per cut or hold), sounds=true "
        "names the approved library sound each beat could take. Call it after the cut and "
        "captions are set, then place the beats you agree with.",
        {"density": {"type": "string", "enum": ["premium", "balanced", "minimal"],
                     "description": "spacing between hero beats (premium ≈ every 2-3 s)"},
         "camera": {"type": "boolean",
                    "description": "opt in to a few optional camera candidates (default false)"},
         "sounds": {"type": "boolean",
                    "description": "opt in to optional library-sound suggestions per beat "
                                   "(default false)"}}),
}
