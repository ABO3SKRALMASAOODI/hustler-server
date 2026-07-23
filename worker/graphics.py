"""Motion-graphics text layer: EDL.texts -> a SECOND .ass file.

Why a second file instead of folding these events into the caption .ass:
captions own their file AND its cache fingerprint — caption emission is
deliberately byte-stable across renders (regression tests pin exact
substrings; the render cache keys on the caption output), and the four
original presets are guaranteed to keep producing byte-identical .ass. Text
graphics are PROGRAM-anchored (FINAL-program seconds — they never remap
through the Timeline the way transcript captions do) and change on a
completely different cadence: adding a title card must never disturb caption
emission or invalidate a caption cache. Two files, two burns, zero coupling —
the renderer burns captions first, then this file on top.

Each TextItem compiles through a TEMPLATE (data in TEMPLATES, not code
branches): one designed look per schemas.TEXT_TEMPLATES entry, built on the
fonts bundled in worker/fonts and the same 1280x720 scaling convention
captions.py uses (sizes scale with max(fx, fy), so 9:16 verticals get
properly big text). Every event carries its own inline geometry (\\an +
\\pos/\\move) exactly like the caption composer, and WrapStyle is 2 with
lines pre-wrapped here — libass never re-wraps or re-places anything behind
the compiler's back, so the geometry we compute is the geometry that renders.

Deterministic by construction: the same EDL dict + frame produces a
byte-identical file. All decisions (timing, position, colors, animation) are
already concrete data in the EDL; this module only compiles them — it reads
no index, no perception, no clock.
"""

# Proven caption mechanics are IMPORTED, not duplicated: color conversion,
# time formatting, escaping, word wrapping and the fonts directory all keep a
# single implementation. FONTS_DIR is re-exported so the renderer can pass
# the same fontsdir to this file's burn without reaching into captions.
from captions import (BASE_PLAY_RES, DARK_TEXT, DEFAULT_HIGHLIGHT,  # noqa: F401
                      FONTS_DIR, _ass_time, _esc, _inline_hl, _wrap,
                      ass_color)

# Graphics may legitimately be short accents (a 0.5s callout pop); 0.3s is
# the floor below which an entrance animation cannot even finish.
GFX_MIN_EVENT_S = 0.3
# Per-glyph typewriter windows are tag-heavy (~40 bytes each). 40 animated
# glyphs keeps the worst 200-char item at a Dialogue line libass parses
# without complaint; everything past the cap appears with the last window.
TYPEWRITER_MAX_GLYPHS = 40
# Backing-panel fill for box=True on non-accent templates (\3c form —
# near-black, slightly translucent so footage reads through the edges).
PANEL_FILL = "&H141414&"
PANEL_ALPHA = "&H20&"
# Entrances that need \move; \move and \pos are mutually exclusive on one
# event, so entrance claims it first and an exit that also wants to move
# falls back to a fade (same rule captions' LINE_ANIMS live by).
MOVE_ANIMS = ("slide_up", "rise", "drop")

# ── Templates ────────────────────────────────────────────────────────────
# One designed look per schemas.TEXT_TEMPLATES entry — data, not code
# branches. base_size is the px at the 1280x720 reference frame (scaled by
# max(fx, fy) exactly like caption sizes); char_w approximates glyph width
# as a fraction of the font px for the wrap budget (mirrors PRESETS);
# spacing is \fsp letter-spacing px at the reference frame; y is the
# BLOCK-CENTER anchor as a fraction of frame height; deck2 styles the
# secondary deck a "\n" or " | " separator introduces (None = the template
# treats newlines as plain line breaks and " | " as literal text).
# box_kind: "accent" fills the box with the accent color and sets the text
# dark (the caption box treatment); "panel" backs the text with a dark bar.
TEMPLATES = {
    "title": {
        # Hero card: Inter Display Black caps, tight leading, heavy shadow —
        # the same hierarchy the premium caption "stacked" look leads with.
        "font": "Inter Display Black", "base_size": 66, "char_w": 0.56,
        "x": 0.5, "y": 0.42, "align": "center", "uppercase": True,
        "spacing": 1.0, "leading": 1.08, "outline": 0.0, "shadow": 2.6,
        "box": False, "box_kind": "panel", "entrance": "rise",
        "exit": "fade", "deck2": None,
    },
    "subtitle": {
        # Supporting line / kicker: small tracked-out caps, quiet fade.
        "font": "Inter Display Bold", "base_size": 30, "char_w": 0.54,
        "x": 0.5, "y": 0.60, "align": "center", "uppercase": True,
        "spacing": 3.2, "leading": 1.30, "outline": 0.0, "shadow": 1.8,
        "box": False, "box_kind": "panel", "entrance": "fade",
        "exit": "fade", "deck2": None,
    },
    "lower_third": {
        # Left-anchored two-deck: bold name line; the separator deck drops
        # to a small tracked accent line (title/role) underneath.
        "font": "Inter Display ExtraBold", "base_size": 38, "char_w": 0.56,
        "x": 0.085, "y": 0.84, "align": "left", "uppercase": False,
        "spacing": 0.0, "leading": 1.16, "outline": 0.0, "shadow": 2.0,
        "box": False, "box_kind": "panel", "entrance": "slide_up",
        "exit": "fade",
        "deck2": {"scale": 0.62, "color": "accent", "uppercase": True,
                  "spacing": 2.4, "font": None},
    },
    "callout": {
        # Accent-filled box with dark text — the caption box treatment as a
        # standalone label, upper third where pointed-at things live.
        "font": "Inter Display ExtraBold", "base_size": 38, "char_w": 0.56,
        "x": 0.5, "y": 0.28, "align": "center", "uppercase": True,
        "spacing": 0.6, "leading": 1.34, "outline": 0.0, "shadow": 0.0,
        "box": True, "box_kind": "accent", "entrance": "pop",
        "exit": "pop", "deck2": None,
    },
    "big_number": {
        # The huge Anton figure of the premium looks; the separator deck is
        # a tiny tracked accent label under it ("22 | DAYS LEFT").
        "font": "Anton", "base_size": 118, "char_w": 0.50,
        "x": 0.5, "y": 0.44, "align": "center", "uppercase": True,
        "spacing": 2.0, "leading": 1.02, "outline": 0.0, "shadow": 3.0,
        "box": False, "box_kind": "panel", "entrance": "pop",
        "exit": "fade",
        "deck2": {"scale": 0.24, "color": "accent", "uppercase": True,
                  "spacing": 4.5, "font": "Inter Display ExtraBold"},
    },
    "quote": {
        # Serif italic with accent quote marks; the separator deck is the
        # attribution line in a small sans.
        "font": "DM Serif Display", "base_size": 44, "char_w": 0.46,
        "x": 0.5, "y": 0.40, "align": "center", "uppercase": False,
        "spacing": 0.0, "leading": 1.28, "outline": 0.0, "shadow": 1.6,
        "box": False, "box_kind": "panel", "entrance": "fade",
        "exit": "fade", "italic": True, "quote_marks": True,
        "deck2": {"scale": 0.50, "color": "accent", "uppercase": False,
                  "spacing": 1.2, "font": "Inter Display Bold"},
    },
    "chapter": {
        # Top-of-frame marker: condensed caps, wide tracking; the separator
        # deck is the chapter name under the number.
        "font": "Bebas Neue", "base_size": 52, "char_w": 0.40,
        "x": 0.5, "y": 0.14, "align": "center", "uppercase": True,
        "spacing": 5.0, "leading": 1.20, "outline": 0.0, "shadow": 2.0,
        "box": False, "box_kind": "panel", "entrance": "whip",
        "exit": "fade",
        "deck2": {"scale": 0.55, "color": "accent", "uppercase": True,
                  "spacing": 3.0, "font": "Inter Display Bold"},
    },
}

# Independent writer: graphics events are geometry-complete, so the styles
# section is one nominal style per FONT (family selection only — size,
# color, borders, alignment are all inline per event).
GFX_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {resx}
PlayResY: {resy}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
"""

GFX_EVENTS_HEADER = """
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _gfx_style_line(name, fam, play_res):
    """Nominal style: family only matters (Bold=0 — the bundled fonts are
    already heavy weights; synthetic emboldening would distort them)."""
    f = max(play_res[0] / BASE_PLAY_RES[0], play_res[1] / BASE_PLAY_RES[1])
    px = max(12, round(48 * f))
    return (f"Style: {name},{fam},{px},"
            f"{ass_color('#FFFFFF')},&H00FFFFFF,&H00101010,&H96000000,"
            f"0,0,0,0,100,100,0,0,1,0,{round(2.0 * f, 1)},5,0,0,0,1")


# ── Animation recipes ────────────────────────────────────────────────────
# Adapted from captions.WORD_ANIMS / _premium_anim_prefix — those exact tag
# shapes are libass-verified in production. Entrances play in the first
# ~220ms; exits mirror them inside the (o0, o1) ms window at the event tail.

def _entrance_extra(anim, px):
    if anim == "pop":
        return (r"\fscx70\fscy70\t(0,120,\fscx106\fscy106)"
                r"\t(120,200,\fscx100\fscy100)")
    if anim == "blur_in":
        b = max(2, round(px * 0.13))
        return rf"\blur{b}\fscx88\fscy88\t(0,220,\blur0\fscx100\fscy100)"
    if anim == "whip":
        return (r"\frz14\fscx66\fscy66"
                r"\t(0,150,\frz0\fscx104\fscy104)\t(150,220,\fscx100\fscy100)")
    return ""


def _exit_extra(anim, px, o0, o1):
    # \alpha inside \t fades every component (fill, border, shadow) so a
    # boxed event leaves with its box instead of stranding an empty bar.
    if anim == "pop":
        return rf"\t({o0},{o1},\fscx60\fscy60\alpha&HFF&)"
    if anim == "blur_in":
        b = max(2, round(px * 0.13))
        return rf"\t({o0},{o1},\blur{b}\alpha&HFF&)"
    if anim == "whip":
        return rf"\t({o0},{o1},\frz-14\fscx70\fscy70\alpha&HFF&)"
    return ""


class _Typist:
    """Per-glyph typewriter reveal, carried across lines and decks so the
    cadence is continuous through the whole item. Each visible glyph up to
    TYPEWRITER_MAX_GLYPHS opens its own \\alpha window; spaces and post-cap
    glyphs ride the current segment, so tag bloat stays bounded no matter
    how long the (schema-capped 200-char) text is."""

    def __init__(self, n_glyphs, dur_ms, exit_ms):
        n_anim = max(1, min(n_glyphs, TYPEWRITER_MAX_GLYPHS))
        # Reveal inside the first 60% of the event, never into the exit
        # window, never slower than the event allows.
        total = max(240, min(1500, int(dur_ms * 0.6) - exit_ms))
        self.step = max(18, int(total / n_anim))
        self.i = 0
        self.open = False

    def emit(self, text):
        out = []
        for ch in text:
            if ch != " " and self.i < TYPEWRITER_MAX_GLYPHS:
                t0 = self.i * self.step
                out.append(rf"{{\alpha&HFF&\t({t0},{t0 + 90},\alpha&H00&)}}")
                self.i += 1
                self.open = True
            elif not self.open:
                # Leading space before any window: open the current window
                # (don't advance) so no un-alpha'd segment ever renders —
                # with box=True a bare space would flash its backing box.
                t0 = min(self.i, TYPEWRITER_MAX_GLYPHS - 1) * self.step
                out.append(rf"{{\alpha&HFF&\t({t0},{t0 + 90},\alpha&H00&)}}")
                self.open = True
            out.append(_esc(ch))
        return "".join(out)


# ── Compilation ──────────────────────────────────────────────────────────

def _split_decks(text, tpl):
    """The first "\\n" or " | " separates main deck from the secondary deck
    on deck-aware templates. Templates without a deck2 keep "\\n" as plain
    line breaks and " | " as literal text — nothing is reinterpreted."""
    if not tpl.get("deck2"):
        return text, None
    if "\n" in text:
        main, sec = text.split("\n", 1)
        return main.strip(), (sec.replace("\n", " ").strip() or None)
    if " | " in text:
        main, sec = text.split(" | ", 1)
        return main.strip(), (sec.strip() or None)
    return text, None


def _wrap_hard(text, line_chars):
    """Wrap honouring explicit newlines as hard breaks. Never truncates —
    dropping the user's words to fit a template would be a silent lie; the
    shrink pass below scales the font down instead."""
    lines = []
    for para in text.split("\n"):
        para = para.strip()
        if para:
            lines.extend(_wrap(para, line_chars))
    return lines or [""]


def _compile_item(tx, out_dur, play_res):
    """One TextItem dict -> event dict {start, end, text, font} or None
    (fully outside the program). All geometry is resolved HERE, at compile
    time, from concrete EDL data — nothing is left for the renderer."""
    W, H = play_res
    fx, fy = W / BASE_PLAY_RES[0], H / BASE_PLAY_RES[1]
    f = max(fx, fy)
    tpl = TEMPLATES.get(tx.get("template") or "title") or TEMPLATES["title"]

    # ── time window: clamp into [0, out_dur], floor at GFX_MIN_EVENT_S ──
    start = max(0.0, float(tx.get("start") or 0.0))
    end = min(float(tx.get("end") or 0.0), out_dur)
    if start >= out_dur - 0.05 or end <= 0:
        return None
    if end - start < GFX_MIN_EVENT_S:
        end = min(out_dur, start + GFX_MIN_EVENT_S)
        if end - start < GFX_MIN_EVENT_S:
            start = max(0.0, end - GFX_MIN_EVENT_S)
    dur = end - start
    dur_ms = int(round(dur * 1000))

    # ── resolved look: template defaults, item overrides on top ──
    try:
        size_scale = min(max(float(tx.get("size_scale") or 1.0), 0.4), 3.0)
    except (TypeError, ValueError):
        size_scale = 1.0
    px = max(12, round(tpl["base_size"] * f * size_scale))
    sp = round(tpl["spacing"] * f, 1)
    upper = tx.get("uppercase")
    upper = tpl["uppercase"] if upper is None else bool(upper)
    accent = _inline_hl(tx.get("accent_color") or DEFAULT_HIGHLIGHT)
    box = tx.get("box")
    box = tpl["box"] if box is None else bool(box)
    box_kind = tpl.get("box_kind", "panel")
    # In an accent box the text defaults to dark (the caption box look); an
    # explicit item color always wins.
    if tx.get("color"):
        text_c = _inline_hl(tx["color"])
    elif box and box_kind == "accent":
        text_c = DARK_TEXT
    else:
        text_c = _inline_hl("#FFFFFF")
    font = tx.get("font") or tpl["font"]
    entrance = tx.get("entrance") or tpl["entrance"]
    exit_a = tx.get("exit") or tpl["exit"]
    typewriter = entrance == "typewriter"
    if typewriter:
        # Per-glyph \alpha segments override a line-level exit \t placed
        # before them, so any non-fade exit would silently not render —
        # \fad is composited after per-segment alpha and is the one exit
        # that provably works here.
        exit_a = "fade"

    # ── decks + wrapping (pre-wrapped; WrapStyle 2 keeps us authoritative)
    text = (tx.get("text") or "").strip()
    main_text, sec_text = _split_decks(text, tpl)
    if upper:
        main_text = main_text.upper()
    d2 = tpl.get("deck2")
    px2 = sp2 = 0
    sec_lines = []
    if sec_text is not None and d2:
        if d2["uppercase"]:
            sec_text = sec_text.upper()
        px2 = max(8, round(px * d2["scale"]))
        sp2 = round(d2["spacing"] * f, 1)

    edge_x, edge_y = round(0.04 * W), round(0.03 * H)
    # Wrap budget from the REAL usable width at the item's own anchor —
    # mirrors captions.line_chars_for (font px * char_w glyph estimate).
    x_frac = tx.get("x")
    x_frac = tpl["x"] if x_frac is None else min(max(float(x_frac), 0.0), 1.0)
    y_frac = tx.get("y")
    y_frac = tpl["y"] if y_frac is None else min(max(float(y_frac), 0.0), 1.0)
    if tpl["align"] == "left":
        usable = max(1.0, W - x_frac * W - edge_x)
    else:
        usable = max(1.0, 2 * min(x_frac * W, W - x_frac * W) - 2 * edge_x)

    def budget(p, s):
        return max(4, int(usable / (tpl["char_w"] * p + s)))

    main_lines = _wrap_hard(main_text, budget(px, sp))
    if sec_text is not None and d2:
        sec_lines = _wrap_hard(sec_text, budget(px2, sp2))

    # ── measure, then shrink-to-fit instead of clipping or truncating ──
    def measure():
        widths = [len(l) * (tpl["char_w"] * px + sp) for l in main_lines]
        widths += [len(l) * (tpl["char_w"] * px2 + sp2) for l in sec_lines]
        bh = len(main_lines) * tpl["leading"] * px
        if sec_lines:
            bh += 0.35 * px2 + len(sec_lines) * 1.25 * px2
        return max(widths or [1.0]), bh

    max_w, block_h = measure()
    shrink = min(1.0, (W - 2 * edge_x) / max_w, (H - 2 * edge_y) / block_h)
    if shrink < 1.0:
        px = max(10, int(px * shrink))
        sp = round(sp * shrink, 1)
        if sec_lines:
            px2 = max(8, int(px2 * shrink))
            sp2 = round(sp2 * shrink, 1)
        max_w, block_h = measure()

    # ── anchor: clamped fully on-frame (\an5 block-center / \an7 top-left)
    cy = y_frac * H
    lo, hi = edge_y + block_h / 2, H - edge_y - block_h / 2
    cy = H / 2 if lo > hi else min(max(cy, lo), hi)
    if tpl["align"] == "left":
        an = 7
        ax = round(min(max(x_frac * W, edge_x),
                       max(edge_x, W - edge_x - max_w)))
        ay = round(min(max(cy - block_h / 2, edge_y),
                       max(edge_y, H - edge_y - block_h)))
    else:
        an = 5
        lo, hi = edge_x + max_w / 2, W - edge_x - max_w / 2
        ax = round(W / 2 if lo > hi else min(max(x_frac * W, lo), hi))
        ay = round(cy)

    # ── exit window + \pos-vs-\move arbitration ──
    exit_s = min(0.4, max(0.25, 0.3 * dur), max(0.1, dur - 0.05))
    o0, o1 = dur_ms - int(exit_s * 1000), dur_ms
    off = max(10, int(0.045 * H))
    fad_in = fad_out = 0
    ent_move = entrance in MOVE_ANIMS
    exit_move = (not ent_move) and exit_a in MOVE_ANIMS
    if ent_move:
        sgn = -1 if entrance == "drop" else 1       # drop enters from above
        pos = rf"\move({ax},{ay + sgn * off},{ax},{ay},0,180)"
        fad_in = 120
    elif exit_move:
        sgn = 1 if exit_a == "drop" else -1         # drop falls away; else up
        pos = rf"\move({ax},{ay},{ax},{ay + sgn * off},{o0},{o1})"
        fad_out = 160
    else:
        pos = rf"\pos({ax},{ay})"
    if entrance == "fade":
        fad_in = 180
    if typewriter:
        fad_in = 0
    if exit_a == "fade":
        fad_out = 220
    elif ent_move and exit_a in MOVE_ANIMS:
        # entrance claimed \move: the moving exit degrades to a fade
        # instead of silently dropping the exit entirely.
        fad_out = 220

    # ── inline tags: geometry, fade, base style, box, animations ──
    head = rf"\an{an}" + pos
    if fad_in or fad_out:
        head += rf"\fad({fad_in},{fad_out})"
    head += rf"\fs{px}"
    if sp:
        head += rf"\fsp{sp}"
    head += rf"\1c{text_c}\3c&H101010&"
    head += rf"\bord{round(tpl['outline'] * f, 1)}"
    head += rf"\shad{round(tpl['shadow'] * f, 1)}"
    if tpl.get("italic"):
        head += r"\i1"
    if box:
        bx, by = max(2, round(0.22 * px)), max(2, round(0.13 * px))
        fill = accent if box_kind == "accent" else PANEL_FILL + rf"\3a{PANEL_ALPHA}"
        head += rf"\3c{fill}\xbord{bx}\ybord{by}\shad0"
    head += _entrance_extra(entrance, px)
    if not exit_move:
        head += _exit_extra(exit_a, px, o0, o1)

    # ── body: pre-wrapped lines, deck2 restated inline ──
    typist = None
    if typewriter:
        n_glyphs = sum(1 for ch in "".join(main_lines + sec_lines)
                       if ch != " ")
        typist = _Typist(n_glyphs, dur_ms, int(exit_s * 1000))

    def seg(t, tags=""):
        body = typist.emit(t) if typist else _esc(t)
        return ("{" + tags + "}" + body) if tags else body

    out_lines = []
    quote_marks = tpl.get("quote_marks") and not typewriter \
        and main_text[:1] not in "\"“'‘"
    for i, line in enumerate(main_lines):
        s = ""
        if quote_marks and i == 0:
            # Accent quote marks are template decoration (like the box), not
            # invented content; skipped when the text already opens quoted.
            s += seg("“", rf"\1c{accent}") + seg("", rf"\1c{text_c}")
        s += seg(line)
        if quote_marks and i == len(main_lines) - 1:
            s += seg("”", rf"\1c{accent}")
        out_lines.append(s)
    if sec_lines:
        d2c = accent if d2["color"] == "accent" else text_c
        tags = rf"\fs{px2}"
        if sp2:
            tags += rf"\fsp{sp2}"
        tags += rf"\1c{d2c}\i0"
        if d2.get("font"):
            tags += rf"\fn{d2['font']}"
        out_lines.append(seg(sec_lines[0], tags))
        out_lines.extend(seg(l) for l in sec_lines[1:])

    return {"start": start, "end": end, "font": font,
            "text": "{" + head + "}" + r"\N".join(out_lines)}


def build_gfx_ass(edl, out_duration_s, path, play_res=BASE_PLAY_RES):
    """EDL texts field -> a second .ass file for the graphics burn.

    Returns path, or None when the EDL has no texts (or none of them lands
    inside the program window — an empty file would still cost a subtitles
    filter pass for nothing). out_duration_s bounds every event's end.
    play_res must be the output frame so positions are exact at any aspect.
    """
    texts = edl.get("texts") or []
    if not texts or out_duration_s is None or out_duration_s <= GFX_MIN_EVENT_S:
        return None
    events = []
    # validate_edl already sorts texts by (start, id); re-sorting here makes
    # the output independent of dict-source ordering (determinism backstop).
    for tx in sorted(texts, key=lambda t: (float(t.get("start") or 0.0),
                                           str(t.get("id") or ""))):
        ev = _compile_item(tx, float(out_duration_s), play_res)
        if ev:
            events.append(ev)
    if not events:
        return None

    # One named style per font, in first-use order over the sorted events —
    # a stable, deterministic assignment.
    styles, names = [], {}
    for ev in events:
        if ev["font"] not in names:
            names[ev["font"]] = f"G{len(names) + 1}"
            styles.append((names[ev["font"]], ev["font"]))

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(GFX_HEADER.format(resx=int(play_res[0]),
                                   resy=int(play_res[1])))
        for name, fam in styles:
            fh.write(_gfx_style_line(name, fam, play_res) + "\n")
        fh.write(GFX_EVENTS_HEADER)
        for ev in events:
            fh.write(f"Dialogue: 0,{_ass_time(ev['start'])},"
                     f"{_ass_time(ev['end'])},{names[ev['font']]}"
                     f",,0,0,0,,{ev['text']}\n")
    return path
