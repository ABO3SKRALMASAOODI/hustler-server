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
                 "dynamic": False}

# Pop-in scale animation for dynamic word-by-word captions.
POP_TAG = r"{\fscx60\fscy60\t(0,110,\fscx108\fscy108)\t(110,220,\fscx100\fscy100)}"

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
        for k in ("color", "size", "position"):
            if d.get(k):
                s[k] = d[k]
        if d.get("dynamic") is not None:
            s["dynamic"] = bool(d["dynamic"])
    return s


def style_line(name, style, play_res=BASE_PLAY_RES):
    s = _norm_style(style)
    # Font size tracks the LARGER of the two frame scale factors so vertical
    # frames (tall, narrow) get captions sized to their height — width-only
    # scaling left 9:16 text at ~2.5% of frame height, unreadably small.
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    f = max(fx, fy)
    font = max(10, round(FONT_SIZES.get(s["size"], 40) * f))
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
    font = max(10, FONT_SIZES.get(s["size"], 40) * max(fx, fy))
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


def events_dynamic(out_words):
    """Word-by-word pop captions (TikTok style): every word is its own event
    with a scale-in animation, shown until the next word starts (or briefly
    past its own end at a pause). Word times come from the transcript — never
    invented."""
    events = []
    for i, w in enumerate(out_words):
        start = w["t0"]
        if i + 1 < len(out_words):
            nxt = out_words[i + 1]["t0"]
            end = nxt if nxt - w["t1"] <= 1.2 else min(w["t1"] + 0.35, nxt)
        else:
            end = w["t1"] + 0.35
        if end <= start:
            end = start + 0.12
        events.append({"start": start, "end": end,
                       "text": POP_TAG + _esc(w["w"])})
    return events


def events_from_items(items, tl, line_chars=MAX_LINE_CHARS):
    """Explicit caption items (source time) -> output-time events. A span
    crossing a cut boundary is clipped to its surviving pieces; items whose
    span is fully cut are dropped. Items may carry a per-item style."""
    events = []
    for it in items:
        get = (lambda k, d=None: it.get(k, d)) if isinstance(it, dict) \
            else (lambda k, d=None: getattr(it, k, d))
        spans = tl.span_to_out(get("start"), get("end"))
        if not spans:
            continue
        start, end = spans[0][0], spans[-1][1]
        lines = _wrap(get("text"), line_chars)[:MAX_LINES]
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
            continue
        merged = dict(_norm_style(global_style))
        d = ov if isinstance(ov, dict) else ov.model_dump()
        for k in ("color", "size", "position"):
            if d.get(k):
                merged[k] = d[k]
        key = tuple(sorted(merged.items()))
        if key not in seen:
            name = f"VS{len(seen)}"
            seen[key] = name
            styles.append((name, merged))
        ev["style_name"] = seen[key]

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
            events = events_dynamic(out_words)
        else:
            events = events_from_transcript(
                out_words, max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res))
    elif isinstance(captions, list):
        events = events_from_items(captions, tl,
                                   line_chars=line_chars_for(None, play_res))
        global_style = None
    else:
        return None
    if not events:
        return None
    return write_ass(events, path, global_style, play_res)
