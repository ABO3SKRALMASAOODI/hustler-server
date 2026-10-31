"""What the user asked for in one Studio request, ask by ask, and whether the
turn delivered each (RS_HONESTY_BROLL, 31 Oct 2026).

Ticket #49 (30 Oct): user 2665 asked for new nature music, a cinematic grade
and an angle fix. The turn delivered the grade only; the reply filter
dropped both "not done" lines and the user read "Done: ..." while the job
said ``fulfilled``. User 2666 asked for b-roll throughout; the one line that
said two clips were added was dropped, and two clips over 1-7 s of a 15 s
talk were recorded as a fulfilled request. Ticket #51: nothing checked a
"b-roll throughout" request against the program before its one trial
preview.

Pure functions (EDL dicts and strings in), so trial_reply, agent_loop and the
tests share one reading of the request:

  asks(user_text)                    the asked topics (negated asks left out)
  ledger(user_text, before, after)   {topic: "done" | "partly" | "not_done"}
  broll_throughout(user_text)        the user wants cutaways across the video
  broll_gaps(edl)                    (program s, covered s, [bare spans])
  broll_shortfall(edl)               the bare spans a "throughout" ask leaves
"""
import re

import reply_grounding as rg

# (topic, what the user says, what a reply line about it says, EDL layers)
ASKS = (
    ("music",
     r"music|songs?|soundtrack|background tracks?|a tracks?|some tracks?|"
     r"play (?:something|some\w*)(?: else)?|beat",
     r"music|songs?|soundtrack|tracks?", ("music",)),
    ("broll",
     r"b-?roll|cutaways?|stock (?:footage|clips?|videos?)|real footage|"
     r"(?:relevant|real|some|authentic) (?:footage|clips|visuals|photos)",
     r"b-?roll|cutaways?|footage|clips?|pexels|stock|overlays?|photos?",
     ("broll",)),
    ("angle",
     r"angle|horizon|tilt(?:ed)?|straighten\w*|rotat\w*|crooked|level the (?:shot|horizon|picture)",
     r"angle|horizon|tilt(?:ed)?|straighten\w*|rotat\w*|crooked|level", ("frame",)),
    ("grade",
     r"colou?r ?grad\w*|grad(?:e|ing)|colou?r correct\w*|lut|cinematic (?:look|colou?rs?|vibes?|grade)",
     r"grad(?:e|ed|ing)|colou?rs?|look|lut", ("grade",)),
    ("captions", r"captions?|subtitles?", r"captions?|subtitles?", ("captions",)),
    ("clarity",
     r"clearer|sharp(?:er|en\w*)|crisp(?:er)?|denois\w*|less (?:grainy|noisy|blurry)|"
     r"(?:enhance|improve) (?:the )?(?:quality|picture|video)",
     r"sharp(?:er|en\w*)|clear(?:er)?|crisp(?:er)?|denois\w*|soft|noise|resolution",
     ("clarity",)),
    ("speed", r"slow[- ]?mo(?:tion)?|speed (?:it )?up|speed ramp|time[- ]?lapse",
     r"slow[- ]?mo(?:tion)?|speed|faster|slower|ramp", ("speed",)),
)
TOPICS = tuple(a[0] for a in ASKS)

# "do not add subtitles", "no music", "keep the colours", "without captions"
_NEG_BEFORE = re.compile(
    r"(?i)\b(?:don'?t|do not|never|no|without|not|keep(?:ing)?|leave|remove|"
    r"cut out|turn off|get rid of|delete|drop|lose)\b(?:\s+\S+){0,3}\s*$")
# "the captions look great", "captions are fine": praise, not an ask
_PRAISE_AFTER = re.compile(r"(?i)^\s*(?:are|is|look|looks|seem|seems)\s+(?:great|good|fine|perfect|nice|ok|okay)\b")
# "if possible" asks are still asks; "maybe" / "or not" are too
_IGNORE_CONTEXT = re.compile(r"(?i)\b(?:like|as in) (?:the|my) (?:reference|example)\b")


def _ask_rx(topic):
    for t, user_rx, _said, _layers in ASKS:
        if t == topic:
            return re.compile(r"(?i)\b(?:" + user_rx + r")\b")
    raise KeyError(topic)


def said_rx(topic):
    for t, _user, said, _layers in ASKS:
        if t == topic:
            return re.compile(r"(?i)\b(?:" + said + r")\b")
    raise KeyError(topic)


_CHANGE_WORDS = re.compile(r"(?i)\b(?:else|different|another|new|replace|swap|change|instead|"
                           r"other|correct|fix|straighten|redo)\b")


def asks_detail(user_text):
    """[(topic, change_required)] for the topics the user asked to add or
    change, in their order. A topic mentioned only in a negated phrase ("Do
    not add subtitles", "no music", "keep the colours") is not an ask to
    deliver. change_required: the ask is to make it different ("play
    something else", "a new grade"), so an existing layer does not meet it."""
    text = rg.straight(user_text or "")
    out = []
    for topic, _user_rx, _said, _layers in ASKS:
        for m in _ask_rx(topic).finditer(text):
            head = text[max(0, m.start() - 60):m.start()]
            # the clause the mention sits in
            head = re.split(r"[.!?;\n:,]|\band\b|\bthen\b|\balso\b", head)[-1]
            tail = re.split(r"[.!?;\n]", text[m.end():m.end() + 60])[0]
            if _NEG_BEFORE.search(head) or _IGNORE_CONTEXT.search(head) \
                    or _PRAISE_AFTER.match(tail):
                continue
            out.append((topic, bool(_CHANGE_WORDS.search(head + " " + m.group(0) + tail))))
            break
    return out


def asks(user_text):
    return [t for t, _c in asks_detail(user_text)]


def _on(v):
    if isinstance(v, dict):
        if v.get("mode") in ("off", "none") and not v.get("items"):
            return False
        return bool(v)
    if isinstance(v, str):
        return v not in ("", "none", "off", "cut")
    return bool(v)


_PRESENT = {
    "music": ("music",), "broll": ("inserts", "overlays"), "captions": ("captions",),
    "clarity": ("effects.stylize",), "grade": ("effects.grade", "effects.grade_custom", "effects.lut"),
    "speed": ("speed",), "angle": (),
}


def _present(edl, topic):
    if topic == "broll" and fx_broll(edl):
        return True
    for path in _PRESENT.get(topic, ()):
        cur = edl
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if _on(cur):
            return True
    return False


def fx_broll(edl):
    """The b-roll cutaways source_broll places as EDL.fx items (type
    "broll", program seconds), as (start, end)."""
    out = []
    for it in (edl or {}).get("fx") or []:
        if not isinstance(it, dict) or it.get("type") != "broll":
            continue
        try:
            a, b = float(it.get("start")), float(it.get("end"))
        except (TypeError, ValueError):
            continue
        if b > a:
            out.append((a, b))
    return out


def _layer_changed(before, after, layers):
    for layer in layers:
        if layer == "broll" and fx_broll(before) != fx_broll(after):
            return True
        if layer == "clarity":
            if not rg._same(((before or {}).get("effects") or {}).get("stylize"),
                            ((after or {}).get("effects") or {}).get("stylize")):
                return True
            continue
        if rg.layer_changed(before or {}, after or {}, layer):
            return True
    return False


_THROUGHOUT = re.compile(
    r"(?i)\b(?:throughout|the whole (?:video|clip|thing)|all (?:over|through)|"
    r"(?:the )?entire (?:video|clip)|every (?:moment|time|mention)|whenever|"
    r"when i (?:mention|talk|say)|at the (?:spoken|right|correct) moments?|"
    r"for each (?:topic|mention|part)|across the (?:video|clip))\b")


def broll_throughout(user_text):
    """The user asked for cutaways across the video, not one insert."""
    t = rg.straight(user_text or "")
    return "broll" in asks(t) and bool(_THROUGHOUT.search(t))


def broll_gaps(edl, min_scale=0.8):
    """(program seconds, seconds a cutaway covers, [(start, end)] bare spans
    in output time, longest first). A cutaway is an inserted clip or a
    video/image overlay big enough to replace the picture (a logo or a small
    PIP is not b-roll). None for an empty program."""
    import timeline
    try:
        blocks = timeline.program_blocks(edl or {})
    except Exception:  # noqa: BLE001
        return None
    if not blocks:
        return None
    total = float(blocks[-1]["out_end"])
    spans = [(float(b["out_start"]), float(b["out_end"]))
             for b in blocks if b["kind"] == "insert"]
    for o in (edl or {}).get("overlays") or []:
        if not isinstance(o, dict):
            continue
        try:
            start = float(o.get("start"))
            dur = float(o.get("duration_s") if o.get("duration_s") is not None
                        else float(o.get("end")) - start)
        except (TypeError, ValueError):
            continue
        # fit cover/picture fills the frame whatever its scale (user 2666's
        # clips were scale 0.4, fit cover)
        full = o.get("fit") in ("cover", "picture")
        if (full or timeline._peak(o.get("scale"), 0.4) >= min_scale) and dur > 0:
            spans.append((max(0.0, start), min(total, start + dur)))
    spans.extend((max(0.0, a), min(total, b)) for a, b in fx_broll(edl))
    merged = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    covered = sum(max(0.0, b - a) for a, b in merged)
    gaps, prev = [], 0.0
    for a, b in merged:
        if a - prev > 0.05:
            gaps.append((round(prev, 2), round(a, 2)))
        prev = max(prev, b)
    if total - prev > 0.05:
        gaps.append((round(prev, 2), round(total, 2)))
    gaps.sort(key=lambda g: g[0] - g[1])
    return round(total, 2), round(covered, 2), gaps


# The references' density for a talking head (REFERENCE_PLAYBOOK.md): b-roll
# on 30-60% of the program, no bare stretch longer than ~5 s once the video
# is past its hook.
MIN_SHARE = 0.30
MAX_BARE_S = 5.0
MAX_BARE_SHARE = 0.35


def broll_shortfall(edl):
    """The bare spans (longest first) that leave a "throughout" b-roll ask
    unmet, or [] when the coverage reaches the references' density."""
    cov = broll_gaps(edl)
    if not cov:
        return []
    total, covered, gaps = cov
    if total <= 0:
        return []
    limit = max(MAX_BARE_S, MAX_BARE_SHARE * total)
    long_gaps = [g for g in gaps if g[1] - g[0] > limit]
    if covered / total >= MIN_SHARE and not long_gaps:
        return []
    return long_gaps or [g for g in gaps if g[1] - g[0] >= 2.0]


def ledger(user_text, before, after, wrote=True):
    """{topic: "done" | "partly" | "not_done"} for every asked topic. Done
    means the layers that carry the topic changed this turn, or (for an
    add-type ask, not "something else") the video already carries it;
    "partly" is a b-roll-throughout ask whose cutaways leave long stretches
    bare."""
    out = {}
    for topic, change_required in asks_detail(user_text):
        layers = next(a[3] for a in ASKS if a[0] == topic)
        changed = bool(wrote) and _layer_changed(before, after, layers)
        if not changed and not change_required and _present(after or before, topic):
            changed = True
        state = "done" if changed else "not_done"
        if topic == "broll" and changed and broll_throughout(user_text) \
                and broll_shortfall(after or before):
            state = "partly"
        out[topic] = state
    return out


def unmet(led):
    return [t for t, s in (led or {}).items() if s != "done"]


def spoken_in(edl, index, span, limit=24):
    """The transcript words heard in an OUTPUT-time span (for a b-roll
    shortfall: what is said there, so the editor sources footage for it)."""
    import timeline
    try:
        blocks = timeline.program_blocks(edl or {})
    except Exception:  # noqa: BLE001
        return ""
    a, b = span
    words = [w for w in (index or {}).get("words") or [] if isinstance(w, dict)]
    out = []
    for blk in blocks:
        if blk.get("kind") != "footage":
            continue
        os_, oe = float(blk["out_start"]), float(blk["out_end"])
        lo, hi = max(a, os_), min(b, oe)
        if hi <= lo:
            continue
        ss, se = float(blk["src_start"]), float(blk["src_end"])
        rate = (se - ss) / (oe - os_) if oe > os_ else 1.0
        s0, s1 = ss + (lo - os_) * rate, ss + (hi - os_) * rate
        for w in words:
            try:
                t = float(w.get("t0", w.get("s")))
            except (TypeError, ValueError):
                continue
            if s0 - 0.05 <= t < s1:
                out.append(str(w.get("w") or "").strip())
    out = [w for w in out if w]
    if len(out) > limit:
        out = out[:limit] + ["..."]
    return " ".join(out)
