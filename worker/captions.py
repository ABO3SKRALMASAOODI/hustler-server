"""Burned-caption generation: EDL captions -> .ass subtitle file.

from_transcript mode builds sentence-shaped caption lines from the index
words that survive the cut, timed to word boundaries, max 2 lines x 42 chars.
Explicit caption items are authored in source time and mapped to the output
timeline here.
"""

MAX_LINE_CHARS = 42
MAX_LINES = 2
MIN_EVENT_S = 0.6

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,44,&H00FFFFFF,&H00FFFFFF,&H00101010,&H96000000,-1,0,0,0,100,100,0,0,1,2.4,0,2,60,60,46,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(text):
    return (text.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}")
            .replace("\n", r"\N"))


def _wrap(text):
    """Split into <= MAX_LINES lines of <= MAX_LINE_CHARS, word-boundary."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > MAX_LINE_CHARS:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def events_from_transcript(out_words):
    """out_words: [{'w','t0','t1'}] already in OUTPUT time (kept words only).
    Groups words into events of at most 2 lines x 42 chars, timed to word
    boundaries."""
    events = []
    group, chars = [], 0
    limit = MAX_LINE_CHARS * MAX_LINES

    def flush():
        nonlocal group, chars
        if not group:
            return
        text = " ".join(w["w"] for w in group)
        lines = _wrap(text)[:MAX_LINES]
        start = group[0]["t0"]
        end = max(group[-1]["t1"], start + MIN_EVENT_S)
        events.append({"start": start, "end": end,
                       "text": r"\N".join(_esc(l) for l in lines)})
        group, chars = [], 0

    for i, w in enumerate(out_words):
        gap = (w["t0"] - group[-1]["t1"]) if group else 0.0
        if group and (chars + 1 + len(w["w"]) > limit or gap > 1.2):
            flush()
        group.append(w)
        chars += (1 if chars else 0) + len(w["w"])
    flush()

    # never overlap the next event
    for i in range(len(events) - 1):
        events[i]["end"] = min(events[i]["end"], events[i + 1]["start"] - 0.01) \
            if events[i + 1]["start"] - 0.01 > events[i]["start"] else events[i]["end"]
    return events


def events_from_items(items, tl):
    """Explicit caption items (source time) -> output-time events. Items whose
    span is fully cut are dropped."""
    events = []
    for it in items:
        text = it["text"] if isinstance(it, dict) else it.text
        s = it["start"] if isinstance(it, dict) else it.start
        e = it["end"] if isinstance(it, dict) else it.end
        spans = tl.span_to_out(s, e)
        if not spans:
            continue
        start, end = spans[0][0], spans[-1][1]
        lines = _wrap(text)[:MAX_LINES]
        events.append({"start": start, "end": max(end, start + MIN_EVENT_S),
                       "text": r"\N".join(_esc(l) for l in lines)})
    events.sort(key=lambda ev: ev["start"])
    return events


def write_ass(events, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        for ev in events:
            f.write(f"Dialogue: 0,{_ass_time(ev['start'])},"
                    f"{_ass_time(ev['end'])},Default,,0,0,0,,{ev['text']}\n")
    return path


def build_ass(edl, index, tl, path):
    """EDL captions field -> .ass file (or None when captions are off)."""
    captions = edl.get("captions")
    if not captions:
        return None
    if isinstance(captions, dict) and captions.get("mode") == "from_transcript":
        out_words = tl.kept_words(index.get("words", []))
        events = events_from_transcript(out_words)
    elif isinstance(captions, list):
        events = events_from_items(captions, tl)
    else:
        return None
    if not events:
        return None
    return write_ass(events, path)
