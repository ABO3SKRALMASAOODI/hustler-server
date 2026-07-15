"""Burned-caption generation: EDL captions -> .ass subtitle file.

from_transcript mode builds caption lines from the index words that survive
the cut, timed to word boundaries (never invented times), max 2 lines x 42
chars — or chunks of max_words_per_caption words when set. Explicit caption
items are authored in source time and mapped to the output timeline here.

Styling: a CaptionStyle ({color, size, position, dynamic}) applies globally;
manual items may override per-item. color is #RRGGBB and becomes ASS
PrimaryColour in &H00BBGGRR order. dynamic renders word-by-word pop captions.

The script's PlayRes is the OUTPUT FRAME (so top/middle/bottom land correctly
at any aspect ratio): font sizes scale with the LARGER frame dimension factor
(so 9:16 verticals get properly big text), vertical margins with height,
relative to the 1280x720 the base numbers were tuned on.
"""

MAX_LINE_CHARS = 42
MAX_LINES = 2
MIN_EVENT_S = 0.6

BASE_PLAY_RES = (1280, 720)
FONT_SIZES = {"s": 30, "m": 40, "l": 52, "xl": 68}
ALIGNMENTS = {"bottom": 2, "top": 8, "middle": 5}
# middle (Alignment 5) is vertically centered; libass ignores MarginV there.
MARGIN_V = {"bottom": 46, "top": 40, "middle": 0}

DEFAULT_STYLE = {"color": "#FFFFFF", "size": "m", "position": "bottom",
                 "dynamic": False, "highlight_color": None, "animation": None,
                 "size_scale": None}

# Karaoke (dynamic) captions: groups of up to N words; the word being
# spoken pops and lights up in the highlight color. Groups larger than
# KARAOKE_HARD_MAX read as a wall of text, so max_words is clamped there
# (the caption tools disclose the clamp to the agent).
KARAOKE_MAX_WORDS = 3
KARAOKE_HARD_MAX = 4
DEFAULT_HIGHLIGHT = "#FFE14D"

ASS_HEADER_TOP = """[Script Info]
ScriptType: v4.00+
PlayResX: {resx}
PlayResY: {resy}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""

EVENTS_HEADER = """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ass_color(hex_rgb):
    """#RRGGBB -> &H00BBGGRR (ASS stores colours blue-green-red)."""
    h = (hex_rgb or "#FFFFFF").lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _norm_style(style):
    s = dict(DEFAULT_STYLE)
    if style:
        d = style if isinstance(style, dict) else style.model_dump()
        for k in ("color", "size", "position", "highlight_color",
                  "animation", "size_scale"):
            if d.get(k):
                s[k] = d[k]
        if d.get("dynamic") is not None:
            s["dynamic"] = bool(d["dynamic"])
    return s


def _size_scale(style):
    """The continuous caption-size multiplier, defaulting to 1.0 (neutral)
    and clamped to the schema bounds so a bad stored value can't blow up
    the font. Applied on top of the coarse `size` bucket everywhere the
    font size is computed."""
    try:
        v = float(style.get("size_scale") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return min(max(v, 0.5), 3.0)


def _anim_prefix(anim, style, play_res):
    """ASS override tags that animate a STATIC caption's entrance. Dynamic
    karaoke events never get these (they animate word-by-word already)."""
    if anim == "fade":
        return r"{\fad(160,120)}"
    if anim == "pop":
        return (r"{\fscx70\fscy70\t(0,120,\fscx106\fscy106)"
                r"\t(120,200,\fscx100\fscy100)}")
    if anim == "slide_up":
        # \move needs the real anchor point: derive it from the alignment
        # + margins exactly as style_line computes them.
        s = _norm_style(style)
        fy = play_res[1] / BASE_PLAY_RES[1]
        cx = int(play_res[0] / 2)
        margin_v = round(MARGIN_V.get(s["position"], 46) * fy)
        if s["position"] == "top":
            y = margin_v
        elif s["position"] == "middle":
            y = int(play_res[1] / 2)
        else:
            y = int(play_res[1]) - margin_v
        off = max(12, int(0.04 * play_res[1]))
        return rf"{{\move({cx},{y + off},{cx},{y},0,160)\fad(120,0)}}"
    return ""


def style_line(name, style, play_res=BASE_PLAY_RES):
    s = _norm_style(style)
    # Font size tracks the LARGER of the two frame scale factors so vertical
    # frames (tall, narrow) get captions sized to their height — width-only
    # scaling left 9:16 text at ~2.5% of frame height, unreadably small.
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    f = max(fx, fy)
    font = max(10, round(FONT_SIZES.get(s["size"], 40) * f * _size_scale(s)))
    margin_lr = max(10, round(60 * fx))
    margin_v = round(MARGIN_V.get(s["position"], 46) * fy)
    outline = max(1.2, round(2.4 * f, 1))
    return (f"Style: {name},DejaVu Sans,{font},"
            f"{ass_color(s['color'])},&H00FFFFFF,&H00101010,&H96000000,"
            f"-1,0,0,0,100,100,0,0,1,{outline},0,"
            f"{ALIGNMENTS.get(s['position'], 2)},{margin_lr},{margin_lr},"
            f"{margin_v},1")


def _ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text):
    return (text.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}")
            .replace("\n", r"\N"))


def _wrap(text, line_chars=MAX_LINE_CHARS):
    """Split into <= MAX_LINES lines of <= line_chars, word-boundary."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > line_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def line_chars_for(style, play_res=BASE_PLAY_RES):
    """How many characters fit on one caption line at this frame + font size.
    Narrow/vertical frames with large fonts fit far fewer than the 42 the
    base numbers were tuned on; chunking to the real width keeps libass from
    wrapping a 2-line chunk into 4+ lines."""
    s = _norm_style(style)
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    font = max(10, FONT_SIZES.get(s["size"], 40) * max(fx, fy) * _size_scale(s))
    usable = play_res[0] - 2 * max(10, round(60 * fx))
    return max(8, min(MAX_LINE_CHARS, int(usable / (0.52 * font))))


def events_from_transcript(out_words, max_words=None, line_chars=MAX_LINE_CHARS):
    """out_words: [{'w','t0','t1'}] already in OUTPUT time (kept words only).
    Groups words into events of at most 2 lines x line_chars chars — or at
    most max_words words per event when set — timed to word boundaries."""
    events = []
    group, chars = [], 0
    limit = line_chars * MAX_LINES

    def flush():
        nonlocal group, chars
        if not group:
            return
        text = " ".join(w["w"] for w in group)
        lines = _wrap(text, line_chars)[:MAX_LINES]
        start = group[0]["t0"]
        end = max(group[-1]["t1"], start + MIN_EVENT_S)
        events.append({"start": start, "end": end,
                       "text": r"\N".join(_esc(l) for l in lines)})
        group, chars = [], 0

    for w in out_words:
        gap = (w["t0"] - group[-1]["t1"]) if group else 0.0
        full = (chars + 1 + len(w["w"]) > limit or
                (max_words and len(group) >= max_words))
        if group and (full or gap > 1.2):
            flush()
        group.append(w)
        chars += (1 if chars else 0) + len(w["w"])
    flush()

    # never overlap the next event
    for i in range(len(events) - 1):
        events[i]["end"] = min(events[i]["end"], events[i + 1]["start"] - 0.01) \
            if events[i + 1]["start"] - 0.01 > events[i]["start"] else events[i]["end"]
    return events


def _inline_hl(hex_rgb):
    """#RRGGBB -> the &HBBGGRR& form inline \\1c override tags use."""
    h = (hex_rgb or DEFAULT_HIGHLIGHT).lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{b}{g}{r}&".upper()


def events_dynamic(out_words, style=None, max_words=None,
                   line_chars=MAX_LINE_CHARS):
    """Karaoke captions (modern reels style): the phrase shows in groups of
    up to 3 words and the word being SPOKEN pops in and lights up in the
    highlight color; the others stay in the base caption color. One Dialogue
    per word — timing comes from the real transcript, never invented.
    Chunks are kept within one line's char budget so the pop animation never
    shifts a wrap point mid-word on narrow frames."""
    s = _norm_style(style)
    hl = _inline_hl(s.get("highlight_color") or DEFAULT_HIGHLIGHT)
    group_n = min(int(max_words), KARAOKE_HARD_MAX) if max_words \
        else KARAOKE_MAX_WORDS
    active_pre = (r"{\1c" + hl + r"\fscx62\fscy62"
                  r"\t(0,90,\fscx114\fscy114)\t(90,170,\fscx106\fscy106)}")
    chunks, cur, chars = [], [], 0
    for w in out_words:
        would = chars + (1 if chars else 0) + len(w["w"])
        if cur and (w["t0"] - cur[-1]["t1"] > 1.2 or len(cur) >= group_n
                    or would > line_chars):
            chunks.append(cur)
            cur, chars = [], 0
            would = len(w["w"])
        cur.append(w)
        chars = would
    if cur:
        chunks.append(cur)
    events = []
    for ci, chunk in enumerate(chunks):
        nxt_t0 = chunks[ci + 1][0]["t0"] if ci + 1 < len(chunks) else None
        for i, w in enumerate(chunk):
            start = w["t0"]
            if i + 1 < len(chunk):
                end = max(chunk[i + 1]["t0"], start + 0.08)
            elif nxt_t0 is not None:
                end = nxt_t0 if nxt_t0 - w["t1"] <= 1.2 \
                    else min(w["t1"] + 0.35, nxt_t0)
            else:
                end = w["t1"] + 0.35
            if end <= start:
                end = start + 0.12
            text = " ".join(
                (active_pre + _esc(x["w"]) + r"{\r}") if j == i
                else _esc(x["w"])
                for j, x in enumerate(chunk))
            events.append({"start": start, "end": end, "text": text})
    # never overlap the next event — same-layer overlaps make libass stack
    # two copies of the phrase (fast speech pushes the +0.08 floor past the
    # next word's start; the degenerate-word fallback can cross chunks).
    # An event whose successor starts at (or within 10ms of) its own start
    # is dropped outright: clamping it would leave a sliver that still
    # renders one stacked frame.
    kept = []
    for i, ev in enumerate(events):
        nxt = events[i + 1] if i + 1 < len(events) else None
        if nxt and nxt["start"] <= ev["start"] + 0.01:
            continue
        if nxt and ev["end"] > nxt["start"]:
            ev["end"] = nxt["start"]
        kept.append(ev)
    return kept


def events_from_items(items, tl, play_res=BASE_PLAY_RES):
    """Explicit caption items (source time) -> output-time events. A span
    crossing a cut boundary is clipped to its surviving pieces; items whose
    span is fully cut are dropped. Items may carry a per-item style — each is
    wrapped at the line budget for ITS OWN rendered font (size + size_scale),
    not the default, so a large item doesn't get chunked at the small-font
    budget and then re-wrapped by libass into a frame-covering text wall."""
    events = []
    for it in items:
        get = (lambda k, d=None: it.get(k, d)) if isinstance(it, dict) \
            else (lambda k, d=None: getattr(it, k, d))
        spans = tl.span_to_out(get("start"), get("end"))
        if not spans:
            continue
        start, end = spans[0][0], spans[-1][1]
        item_chars = line_chars_for(get("style"), play_res)
        lines = _wrap(get("text"), item_chars)[:MAX_LINES]
        events.append({"start": start, "end": max(end, start + MIN_EVENT_S),
                       "text": r"\N".join(_esc(l) for l in lines),
                       "item_style": get("style")})
    events.sort(key=lambda ev: ev["start"])
    return events


def write_ass(events, path, global_style=None, play_res=BASE_PLAY_RES):
    """events may carry item_style (per-item override) and are written
    against a Default style built from global_style; each distinct override
    becomes an extra named style. play_res must be the output frame so
    positions are correct at any aspect ratio."""
    styles = [("Default", _norm_style(global_style))]
    seen = {tuple(sorted(styles[0][1].items())): "Default"}
    for ev in events:
        ov = ev.get("item_style")
        if not ov:
            ev["style_name"] = "Default"
            ev["eff_style"] = styles[0][1]
            continue
        merged = dict(_norm_style(global_style))
        d = ov if isinstance(ov, dict) else ov.model_dump()
        for k in ("color", "size", "position", "animation", "size_scale"):
            if d.get(k):
                merged[k] = d[k]
        key = tuple(sorted(merged.items()))
        if key not in seen:
            name = f"VS{len(seen)}"
            seen[key] = name
            styles.append((name, merged))
        ev["style_name"] = seen[key]
        ev["eff_style"] = merged

    # Entrance animation for static events. Dynamic karaoke events carry
    # their own inline tags and are excluded (build_ass strips animation
    # from the dynamic branch; this check is the backstop).
    for ev in events:
        eff = ev.get("eff_style") or styles[0][1]
        if eff.get("animation") and not eff.get("dynamic"):
            ev["text"] = _anim_prefix(eff["animation"], eff,
                                      play_res) + ev["text"]

    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER_TOP.format(resx=int(play_res[0]),
                                      resy=int(play_res[1])))
        for name, st in styles:
            f.write(style_line(name, st, play_res) + "\n")
        f.write(EVENTS_HEADER)
        for ev in events:
            f.write(f"Dialogue: 0,{_ass_time(ev['start'])},"
                    f"{_ass_time(ev['end'])},{ev.get('style_name', 'Default')}"
                    f",,0,0,0,,{ev['text']}\n")
    return path


def build_ass(edl, index, tl, path, play_res=BASE_PLAY_RES):
    """EDL captions field -> .ass file (or None when captions are off).
    Captions come from the MAIN footage's transcript only — inserted clips
    are not transcribed (v1), so no events land inside spliced insert time
    (kept_words maps around inserts via the Timeline)."""
    captions = edl.get("captions")
    if not captions:
        return None
    if isinstance(captions, dict) and captions.get("mode") == "from_transcript":
        out_words = tl.kept_words(index.get("words", []))
        global_style = captions.get("style")
        if _norm_style(global_style)["dynamic"]:
            events = events_dynamic(
                out_words, style=global_style,
                max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res))
        else:
            events = events_from_transcript(
                out_words, max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res))
    elif isinstance(captions, list):
        events = events_from_items(captions, tl, play_res)
        global_style = None
    else:
        return None
    if not events:
        return None
    return write_ass(events, path, global_style, play_res)
