"""suggest_motion_beats: a premium beat sheet from the kept transcript.

Top short-form editors make something change on screen every 0.3-0.6 s,
interrupt the scroll in the first half-second, and bind 2-4 hero moments to
the exact words that carry the story (numbers, lists, contrasts, the
stressed claim, the call to action). This module finds those anchors in the
CURRENT program (kept words on the output clock, cuts, long static holds)
and returns concrete, timed suggestions — template, program time, extracted
values and the reason — so the editor spends its turns on taste and copy
instead of searching the transcript.

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


def plan(edl, index, density="premium"):
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
         "why": "pattern interrupt + hook line on screen within 0.6 s (write a faithful 3-7 word hook; star one accent word)",
         "sound": "template cues (whoosh_soft + pop_soft)"})
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
                     "why": f"spoken number '{' '.join(w['w'] for w in words[_i:_i + consumed])}' — make it land as a counter (label = what it measures, from the sentence)",
                     "sound": "template cues (tick/ding)"})
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
                 "why": f"{len(cl)} spoken numbers within 5 s — one stat stack/bar comparison revealing each value on its cue (or stacked counters)",
                 "sound": "pop/tick per value"})
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
                         "why": "spoken enumeration — reveal each item on its cue",
                         "sound": "tick per item"})
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
                     "why": f"contrast ('{m.group(0)}') — make the two sides visible",
                     "sound": "whoosh_hard + kick"})
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
                 "why": "stressed/emphasis word — hero word on its onset",
                 "sound": "kick on the landing"})
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
                 "why": "spoken call to action — show it as native UI", "sound": "template cues"})
    # 7. cuts -> landing zooms; long holds -> pulse/push
    camera = []
    blocks = []
    acc = 0.0
    for (s0, s1), L in zip(tl.segs, tl.seg_out_len):
        blocks.append((acc, acc + L))
        acc += L
    for a, b in blocks[1:]:
        if b - a >= 1.0:
            camera.append({"at": round(a, 2), "end": round(min(b, a + 0.45), 2), "tool": "add_zoom",
                           "mode": "landing", "why": "eased landing zoom hides the jump cut and adds energy"})
    events = sorted(set(round(b["at"], 2) for b in beats) | {round(c["at"], 2) for c in camera}
                    | {round(float(w["t0"]), 2) for w in words[::3]})
    holds = []
    last = 0.0
    for t in events + [prog]:
        if t - last > 3.5:
            holds.append([round(last, 2), round(t, 2)])
        last = t
    for a, b in holds:
        camera.append({"at": round(a + 0.3, 2), "end": round(b, 2), "tool": "add_zoom",
                       "mode": "push", "why": f"{b - a:.1f}s without a visual change — slow push or a B-roll/graphic beat"})
    beats.sort(key=lambda x: x["at"])
    per10 = (len(beats) + len(camera)) / max(prog / 10.0, 0.1)
    return {"program_s": round(prog, 2), "density": density, "beats": beats, "camera": camera,
            "long_holds": holds,
            "designed_events_per_10s": round(per10, 2),
            "note": ("Captions (motion_look) already change every word; these beats add the hero "
                     "layer. Write every text param yourself from the quoted transcript; never "
                     "invent numbers or claims.")}


def suggest_motion_beats(ctx, density="premium"):
    """READ: timed premium beat sheet for the current program."""
    if not getattr(ctx, "has_main_video", True):
        return "REJECTED: needs a transcribed main video (no words to plan from)."
    edl = ctx.latest_edl()["json"]
    try:
        result = plan(edl, ctx.index or {}, density=density)
    except Exception as e:  # noqa: BLE001
        return f"Could not plan beats ({str(e)[:160]})."
    lines = [f"Program {result['program_s']}s — {len(result['beats'])} hero beats, "
             f"{len(result['camera'])} camera moves suggested "
             f"(~{result['designed_events_per_10s']} designed events per 10 s; captions add more)."]
    for b in result["beats"]:
        p = {k: v for k, v in b["params"].items() if v is not None}
        lines.append(f"- {b['at']:.2f}-{b['end']:.2f}s {b['template']} [{b['kind']}] params={json.dumps(p)}"
                     + (f" item_times={b['item_times']}" if b.get("item_times") else "")
                     + f" — {b['why']}" + (f" | transcript: \"{b['text_hint'][:140]}\"" if b.get("text_hint") else ""))
    for c in result["camera"]:
        lines.append(f"- {c['at']:.2f}-{c['end']:.2f}s {c['tool']} mode={c['mode']} — {c['why']}")
    if result["long_holds"]:
        lines.append(f"Long static holds: {result['long_holds']}")
    lines.append(result["note"] + " Place beats with add_motion_graphic (one call each, "
                 "sound cues included) and camera moves with add_zoom.")
    return "\n".join(lines)


TOOL_SPECS = {
    "suggest_motion_beats": (
        suggest_motion_beats,
        "READ: a timed premium beat sheet for the CURRENT program: hook interrupt at 0 s, "
        "counters on spoken numbers (values parsed from the words), checklists on enumerations, "
        "versus cards on contrasts, hero word slams on emphasis words, native-UI CTAs on spoken "
        "calls to action, landing zooms after cuts and pushes over long static holds — each with "
        "program times, template, extracted params and the transcript to paraphrase. Call it "
        "after the cut and captions are set, then place the beats you agree with.",
        {"density": {"type": "string", "enum": ["premium", "balanced", "minimal"],
                     "description": "spacing between hero beats (premium ≈ every 2-3 s)"}}),
}
