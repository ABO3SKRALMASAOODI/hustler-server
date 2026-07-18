"""Burned-caption generation: EDL captions -> .ass subtitle file.

from_transcript mode builds caption lines from the index words that survive
the cut, timed to word boundaries (never invented times), max 2 lines x 42
chars — or chunks of max_words_per_caption words when set. Explicit caption
items are authored in source time and mapped to the output timeline here.

Styling: a CaptionStyle ({color, size, position, dynamic}) applies globally;
manual items may override per-item. color is #RRGGBB and becomes ASS
PrimaryColour in &H00BBGGRR order. dynamic renders word-by-word pop captions.

PREMIUM PRESETS (style.preset): named looks built on fonts bundled in
worker/fonts (rendered via the subtitles filter's fontsdir — nothing is
installed system-wide). Each preset drives font, layout, animation mode and
per-word EMPHASIS treatments (accent color, highlight box, serif italic,
oversized numbers). Emphasis words come from the agent (emphasis_words on the
from_transcript config); words containing digits are always emphasized.
Timing still comes ONLY from real transcript words. EDLs without a preset
render through the legacy path byte-identically.

The script's PlayRes is the OUTPUT FRAME (so top/middle/bottom land correctly
at any aspect ratio): font sizes scale with the LARGER frame dimension factor
(so 9:16 verticals get properly big text), vertical margins with height,
relative to the 1280x720 the base numbers were tuned on.
"""

import os

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

MAX_LINE_CHARS = 42
MAX_LINES = 2
MIN_EVENT_S = 0.6
# A paused player (mobile never autoplays) shows the frame at t=0. Reveal /
# karaoke / transcript captions otherwise start at the FIRST spoken word, so
# that opening frame is caption-LESS — a user who never hits play sees no text
# and concludes "captions didn't apply" (a real, repeated founder report). When
# speech begins within this lead-in, carry the first caption back to t=0 so the
# very first frame already shows text. A longer silent intro is left alone — a
# caption held over real silence would misrepresent the timing.
FIRST_CAPTION_LEAD_IN_S = 2.0

BASE_PLAY_RES = (1280, 720)
FONT_SIZES = {"s": 30, "m": 40, "l": 52, "xl": 68}
ALIGNMENTS = {"bottom": 2, "top": 8, "middle": 5}
# middle (Alignment 5) is vertically centered; libass ignores MarginV there.
MARGIN_V = {"bottom": 46, "top": 40, "middle": 0}

DEFAULT_STYLE = {"color": "#FFFFFF", "size": "m", "position": "bottom",
                 "dynamic": False, "highlight_color": None, "animation": None,
                 "size_scale": None, "preset": None, "uppercase": None}

# ── Premium presets ──────────────────────────────────────────────────────
# Every preset is one coherent, opinionated look. base_size is the 'm'
# font size at the 1280x720 reference frame (scaled exactly like legacy
# sizes); char_w approximates glyph width as a fraction of the font size
# for line budgeting; wpl = words per layout line. mode:
#   reveal  — words appear as they are spoken and STAY (left-anchored so
#             nothing ever shifts), the appearing word pops in
#   karaoke — the whole group is visible, the SPOKEN word lights up
#   static  — whole phrase at once with an entrance animation
PRESET_SIZE_MULT = {"s": 0.8, "m": 1.0, "l": 1.3, "xl": 1.6}
PREMIUM_MAX_LINES = 3
SERIF_FONT = "DM Serif Display"
DARK_TEXT = "&H101010&"          # text color inside highlight boxes
PRESETS = {
    "podcast": {
        # The reference reel look: bold white grotesque, left-aligned stack,
        # words land as spoken, keywords get yellow / a marker box / serif.
        "font": "Inter Display ExtraBold", "char_w": 0.56, "base_size": 44,
        "mode": "reveal", "align": "left", "uppercase": False,
        "max_words": 5, "wpl": 2, "outline": 1.5, "shadow": 2.2,
        "emph_scale": 1.28, "num_scale": 1.85,
        "treatments": ("accent", "box", "serif"),
        "active": "pop", "position": "middle",
    },
    "beast": {
        # Loud creator style: Anton caps, centered, spoken word pops in the
        # accent color. Big by default — 'm' here reads like legacy 'l/xl'.
        "font": "Anton", "char_w": 0.50, "base_size": 54,
        "mode": "karaoke", "align": "center", "uppercase": True,
        "max_words": 3, "wpl": 3, "outline": 3.0, "shadow": 2.6,
        "emph_scale": 1.1, "num_scale": 1.35,
        "treatments": ("accent",),
        "active": "accent", "position": "middle",
    },
    "karaoke": {
        # Submagic-style: the accent box FOLLOWS the spoken word.
        "font": "Inter Display ExtraBold", "char_w": 0.56, "base_size": 46,
        "mode": "karaoke", "align": "center", "uppercase": False,
        "max_words": 3, "wpl": 3, "outline": 2.0, "shadow": 2.0,
        "emph_scale": 1.08, "num_scale": 1.3,
        "treatments": ("accent",),
        "active": "box", "position": "bottom",
    },
    "elegant": {
        # Calm premium lower-third: bold sans with serif-italic accents.
        "font": "Inter Display Bold", "char_w": 0.54, "base_size": 38,
        "mode": "static", "align": "center", "uppercase": False,
        "max_words": 8, "wpl": 4, "outline": 1.3, "shadow": 1.6,
        "emph_scale": 1.2, "num_scale": 1.45,
        "treatments": ("serif", "accent"),
        "active": None, "position": "bottom", "animation": "fade",
    },
}
# Block-center anchor as a fraction of frame height, per position.
PREMIUM_ANCHOR_Y = {"top": 0.16, "middle": 0.50, "bottom": 0.80}
# Side margin as a fraction of frame width (left-aligned vs centered).
PREMIUM_MARGIN_X = {"left": 0.085, "center": 0.10}

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
    s["_pos_set"] = False
    if style:
        d = style if isinstance(style, dict) else style.model_dump()
        for k in ("color", "size", "position", "highlight_color",
                  "animation", "size_scale", "preset"):
            if d.get(k):
                s[k] = d[k]
        if d.get("position"):
            # remember an EXPLICIT position so presets only apply their own
            # default placement when the agent didn't choose one.
            s["_pos_set"] = True
        if d.get("dynamic") is not None:
            s["dynamic"] = bool(d["dynamic"])
        if d.get("uppercase") is not None:
            s["uppercase"] = bool(d["uppercase"])
    # 'classic' is the explicit name for the legacy look.
    if s.get("preset") == "classic":
        s["preset"] = None
    return s


def _preset_of(style):
    """The PRESETS entry for a style, or None (legacy path)."""
    s = style if isinstance(style, dict) and "_pos_set" in style \
        else _norm_style(style)
    return PRESETS.get(s.get("preset") or "")


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
    if _preset_of(s):
        return _premium_style_line(name, s, play_res)
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
    p = _preset_of(s)
    if p:
        return _premium_line_chars(p, s, play_res)
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


# ── Premium engine ───────────────────────────────────────────────────────

_STRIP_PUNCT = "\"'`“”‘’.,!?;:…()[]"


def _norm_word(w):
    return (w or "").strip().strip(_STRIP_PUNCT).casefold()


def _word_has_digit(w):
    return any(c.isdigit() for c in (w or ""))


def _display_word(w, upper):
    """Presentation form: captions in the premium looks drop trailing
    sentence punctuation (the reference style shows none)."""
    t = (w or "").strip().strip("\"'“”‘’").rstrip(".,!?;:…")
    if upper:
        t = t.upper()
    return t or (w or "").strip()


def _premium_font_px(p, s, play_res):
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    mult = PRESET_SIZE_MULT.get(s.get("size"), 1.0)
    return max(12, round(p["base_size"] * mult * max(fx, fy) * _size_scale(s)))


def _premium_line_chars(p, s, play_res):
    px = _premium_font_px(p, s, play_res)
    margin = PREMIUM_MARGIN_X[p["align"]] * play_res[0]
    usable = play_res[0] - 2 * margin
    return max(6, int(usable / (p["char_w"] * px)))


def _premium_style_line(name, s, play_res):
    """ASS style for a premium preset. Bold=0 — the bundled fonts are
    already heavy weights; synthetic emboldening would distort them."""
    p = _preset_of(s)
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    f = max(fx, fy)
    px = _premium_font_px(p, s, play_res)
    margin = round(PREMIUM_MARGIN_X[p["align"]] * play_res[0])
    outline = round(p["outline"] * f, 1)
    shadow = round(p["shadow"] * f, 1)
    return (f"Style: {name},{p['font']},{px},"
            f"{ass_color(s['color'])},&H00FFFFFF,&H00101010,&H96000000,"
            f"0,0,0,0,100,100,0,0,1,{outline},{shadow},5,{margin},{margin},"
            f"0,1")


def _base_tags(p, s, px, f):
    """Full per-word reset: every word segment restates ALL varying
    properties, so treatments never leak between words (safer than \\r,
    which also resets alignment in some renderers)."""
    outline = round(p["outline"] * f, 1)
    shadow = round(p["shadow"] * f, 1)
    return (rf"\fn{p['font']}\fs{px}\b0\i0\1c{_inline_hl(s['color'])}"
            rf"\3c&H101010&\bord{outline}\shad{shadow}\fscx100\fscy100")


def _treat_tags(kind, p, px, accent):
    if kind == "accent":
        return rf"\1c{accent}\fs{round(px * p['emph_scale'])}"
    if kind == "num":
        return rf"\1c{accent}\fs{round(px * p['num_scale'])}"
    if kind == "num_plain":
        # karaoke modes: numbers keep their size but not the accent —
        # the accent belongs to the word being SPOKEN, or the karaoke
        # read ("where are we?") falls apart.
        return rf"\fs{round(px * p['num_scale'])}"
    if kind == "box":
        bx, by = max(2, round(0.22 * px)), max(2, round(0.13 * px))
        return rf"\1c{DARK_TEXT}\3c{accent}\xbord{bx}\ybord{by}\shad0"
    if kind == "serif":
        return (rf"\fn{SERIF_FONT}\i1\1c{accent}"
                rf"\fs{round(px * p['emph_scale'] * 1.05)}")
    return ""


# entrance of the word being spoken (reveal mode / karaoke accent)
_POP_IN = (r"\fscx62\fscy62\t(0,100,\fscx108\fscy108)"
           r"\t(100,180,\fscx100\fscy100)")
_POP_ACTIVE = (r"\fscx58\fscy58\t(0,90,\fscx116\fscy116)"
               r"\t(90,170,\fscx104\fscy104)")


def _premium_anim_prefix(anim):
    """Entrance animation for premium STATIC events. slide_up would need
    \\move, which conflicts with the explicit \\pos geometry — it renders
    as a fade instead."""
    if anim == "pop":
        return (r"{\fscx70\fscy70\t(0,120,\fscx106\fscy106)"
                r"\t(120,200,\fscx100\fscy100)}")
    if anim in ("fade", "slide_up"):
        return r"{\fad(180,140)}"
    return ""


def _assign_treatments(chunk, emph, p, rot):
    """Per-word emphasis treatment. Digits are always emphasized (the huge
    '22' of the reference style — one per chunk, extras get the accent).
    Agent-chosen emphasis words rotate through the preset's treatments,
    with the counter carried ACROSS chunks so the look varies; at most one
    highlight box per chunk. Returns (treatments, rot)."""
    treats, num_used, box_used = [], False, False
    for w in chunk:
        token = w["w"]
        if _word_has_digit(token):
            treats.append("accent" if num_used else "num")
            num_used = True
        elif _norm_word(token) in emph:
            t = p["treatments"][rot % len(p["treatments"])]
            rot += 1
            if t == "box":
                if box_used:
                    t = "accent"
                box_used = True
            treats.append(t)
        else:
            treats.append(None)
    return treats, rot


def _premium_chunks(out_words, max_w, chunk_chars):
    """Group words into caption chunks: flush on a speech gap > 1.2s, the
    word cap, the char budget, or sentence-final punctuation."""
    chunks, cur, chars = [], [], 0
    for w in out_words:
        would = chars + (1 if chars else 0) + len(w["w"])
        if cur and (w["t0"] - cur[-1]["t1"] > 1.2 or len(cur) >= max_w
                    or would > chunk_chars):
            chunks.append(cur)
            cur, chars = [], 0
            would = len(w["w"])
        cur.append(w)
        chars = would
        if w["w"] and w["w"].rstrip("\"'”’")[-1:] in ".!?":
            chunks.append(cur)
            cur, chars = [], 0
    if cur:
        chunks.append(cur)
    return chunks


def _premium_layout(disp, wpl, line_chars):
    """Word indices -> lines (word-count AND width capped)."""
    lines, cur, chars = [], [], 0
    for i, t in enumerate(disp):
        would = chars + (1 if chars else 0) + len(t)
        if cur and (len(cur) >= wpl or would > line_chars):
            lines.append(cur)
            cur, chars = [], 0
            would = len(t)
        cur.append(i)
        chars = would
    if cur:
        lines.append(cur)
    return lines


def _geom_prefix(p, s, play_res, lines, treats, px):
    """Explicit \\an+\\pos so nothing ever jumps: left-aligned blocks are
    anchored top-left (words land in their final spot as they appear),
    centered blocks at the block center. The anchor is derived from the
    FINAL chunk layout, then clamped on-frame."""
    W, H = play_res
    pos_name = s["position"] if s.get("_pos_set") else p["position"]
    anchor = PREMIUM_ANCHOR_Y.get(pos_name, 0.5) * H
    scale_of = {"num": p["num_scale"], "num_plain": p["num_scale"],
                "accent": p["emph_scale"], "serif": p["emph_scale"],
                "box": 1.0}
    line_hs = [1.34 * px * max((scale_of.get(treats[i], 1.0) for i in ln),
                               default=1.0) for ln in lines]
    block_h = sum(line_hs)
    edge = 0.03 * H
    if p["align"] == "left":
        x = round(PREMIUM_MARGIN_X["left"] * W)
        y = max(edge, min(anchor - block_h / 2, H - block_h - edge))
        return rf"{{\an7\pos({x},{round(y)})}}"
    x = round(W / 2)
    y = max(block_h / 2 + edge, min(anchor, H - block_h / 2 - edge))
    return rf"{{\an5\pos({x},{round(y)})}}"


def events_premium(out_words, style=None, max_words=None,
                   play_res=BASE_PLAY_RES, emphasis_words=None):
    """from_transcript events for a premium preset. Timing comes ONLY from
    the real word timestamps; layout and treatments are deterministic, so
    the same EDL always renders the same frame."""
    s = _norm_style(style)
    p = _preset_of(s)
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    f = max(fx, fy)
    px = _premium_font_px(p, s, play_res)
    accent = _inline_hl(s.get("highlight_color") or DEFAULT_HIGHLIGHT)
    upper = s["uppercase"] if s["uppercase"] is not None else p["uppercase"]
    emph = {n for n in (_norm_word(w) for w in (emphasis_words or [])) if n}
    max_w = min(int(max_words), 12) if max_words else p["max_words"]
    line_chars = _premium_line_chars(p, s, play_res)
    chunks = _premium_chunks(out_words, max_w,
                             line_chars * PREMIUM_MAX_LINES)
    base = _base_tags(p, s, px, f)
    mode = p["mode"]
    anim = _premium_anim_prefix(s.get("animation") or p.get("animation")) \
        if mode == "static" else ""

    events, rot = [], 0
    for ci, chunk in enumerate(chunks):
        disp = [_display_word(w["w"], upper) for w in chunk]
        treats, rot = _assign_treatments(chunk, emph, p, rot)
        if mode == "karaoke":
            # only the SPOKEN word carries the accent in karaoke modes;
            # persistent keyword coloring would bury the highlight.
            treats = ["num_plain" if t in ("num", "accent") and
                      _word_has_digit(c["w"]) else None
                      for t, c in zip(treats, chunk)]
        lines = _premium_layout(disp, p["wpl"], line_chars)
        geom = _geom_prefix(p, s, play_res, lines, treats, px)
        nxt_t0 = chunks[ci + 1][0]["t0"] if ci + 1 < len(chunks) else None

        def text_upto(last_i, active_i=None, active_tags=""):
            """Chunk text; words after last_i omitted (reveal), active_i
            gets active_tags appended to its overrides."""
            out_lines = []
            for ln in lines:
                segs = []
                for i in ln:
                    if i > last_i:
                        continue
                    tags = base + _treat_tags(treats[i], p, px, accent)
                    if i == active_i:
                        tags += active_tags
                    segs.append("{" + tags + "}" + _esc(disp[i]))
                if segs:
                    out_lines.append(" ".join(segs))
            return geom + anim + r"\N".join(out_lines)

        def hold_end(w):
            if nxt_t0 is not None:
                return nxt_t0 if nxt_t0 - w["t1"] <= 1.2 \
                    else min(w["t1"] + 0.9, nxt_t0)
            return w["t1"] + 0.9

        if mode == "static":
            start = chunk[0]["t0"]
            events.append({"start": start,
                           "end": max(hold_end(chunk[-1]),
                                      start + MIN_EVENT_S),
                           "text": text_upto(len(chunk) - 1),
                           "premium": True})
            continue
        for i, w in enumerate(chunk):
            start = w["t0"]
            if i + 1 < len(chunk):
                end = max(chunk[i + 1]["t0"], start + 0.08)
            else:
                end = hold_end(w)
            if end <= start:
                end = start + 0.12
            if mode == "reveal":
                text = text_upto(i, i, _POP_IN)
            else:  # karaoke: whole chunk visible, spoken word lights up
                if p["active"] == "box" and treats[i] != "box":
                    bx = max(2, round(0.22 * px))
                    by = max(2, round(0.13 * px))
                    act = (rf"\1c{DARK_TEXT}\3c{accent}\xbord{bx}"
                           rf"\ybord{by}\shad0")
                elif treats[i] == "box":
                    act = _POP_ACTIVE
                else:
                    act = rf"\1c{accent}" + _POP_ACTIVE
                text = text_upto(len(chunk) - 1, i, act)
            events.append({"start": start, "end": end, "text": text,
                           "premium": True})

    # never overlap the next event (same rationale + logic as
    # events_dynamic: same-layer overlap stacks two copies of the phrase).
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
        ns = _norm_style(get("style"))
        p = _preset_of(ns)
        if p:
            # Dictated text in a premium look: preset font/uppercase/
            # geometry apply, but the words render VERBATIM — no emphasis
            # treatments are invented on text the agent wrote out.
            upper = ns["uppercase"] if ns["uppercase"] is not None \
                else p["uppercase"]
            text = get("text").upper() if upper else get("text")
            lines = _wrap(text, item_chars)[:PREMIUM_MAX_LINES]
            px = _premium_font_px(p, ns, play_res)
            geom = _geom_prefix(p, ns, play_res,
                                [[0]] * len(lines), [None], px)
            anim = _premium_anim_prefix(ns.get("animation")
                                        or p.get("animation"))
            events.append({"start": start,
                           "end": max(end, start + MIN_EVENT_S),
                           "text": geom + anim +
                           r"\N".join(_esc(l) for l in lines),
                           "item_style": get("style"), "premium": True})
            continue
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
        for k in ("color", "size", "position", "animation", "size_scale",
                  "preset", "uppercase"):
            if d.get(k):
                merged[k] = d[k]
        if merged.get("preset") == "classic":
            merged["preset"] = None
        key = tuple(sorted(merged.items()))
        if key not in seen:
            name = f"VS{len(seen)}"
            seen[key] = name
            styles.append((name, merged))
        ev["style_name"] = seen[key]
        ev["eff_style"] = merged

    # Entrance animation for static events. Dynamic karaoke events carry
    # their own inline tags and are excluded (build_ass strips animation
    # from the dynamic branch; this check is the backstop). Premium events
    # embed their own geometry + animation already.
    for ev in events:
        eff = ev.get("eff_style") or styles[0][1]
        if eff.get("animation") and not eff.get("dynamic") \
                and not ev.get("premium"):
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
        if _preset_of(_norm_style(global_style)):
            events = events_premium(
                out_words, style=global_style,
                max_words=captions.get("max_words_per_caption"),
                play_res=play_res,
                emphasis_words=captions.get("emphasis_words"))
        elif _norm_style(global_style)["dynamic"]:
            events = events_dynamic(
                out_words, style=global_style,
                max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res))
        else:
            events = events_from_transcript(
                out_words, max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res))
        # Make the opening frame carry a caption so a paused player isn't blank
        # (see FIRST_CAPTION_LEAD_IN_S). from_transcript only — dictated caption
        # items keep their authored timing. NOT when an inserted clip opens the
        # program: inserts aren't transcribed, their opening frames aren't blank,
        # and a main-footage word doesn't belong burned over someone's title card.
        opens_on_insert = any(fs <= 0.01 and fs + d > 0.01
                              for fs, d in tl.insert_positions())
        if events and not opens_on_insert \
                and 0.0 < events[0]["start"] <= FIRST_CAPTION_LEAD_IN_S:
            events[0]["start"] = 0.0
    elif isinstance(captions, list):
        events = events_from_items(captions, tl, play_res)
        global_style = None
    else:
        return None
    if not events:
        return None
    return write_ass(events, path, global_style, play_res)
