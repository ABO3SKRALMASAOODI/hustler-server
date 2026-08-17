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
import re

from schemas import MAX_WORDS_PER_CAPTION

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# Stored on every newly authored transcript-caption track.  Historical EDLs
# omit it and deliberately stay on the frozen v1 grouping/layout path; never
# infer an engine version from a preset at render time.
CAPTION_DESIGN_VERSION = 2

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
# Round 52. A VERTICAL output is watched inside a platform's own furniture:
# TikTok prints the creator's caption, @handle and hashtags across the bottom
# of the frame, Reels and Shorts do the same, and the action rail sits over the
# right edge. 46/720 puts a bottom-anchored caption 6.4% from the bottom, i.e.
# UNDER all of it — the premium presets already anchor at 0.80 of the height
# (PREMIUM_ANCHOR_Y) and are clear, so this only ever moved the legacy/classic
# path, which was burning text into the one band of a 9:16 frame nobody can
# read. Landscape is untouched: there is no chrome there, and lifting it would
# change every existing 16:9 render for no reason.
VERTICAL_BOTTOM_SAFE_FRAC = 0.13


def bottom_margin_v(position, play_res):
    """MarginV in output pixels for `position`, honouring the vertical-video
    safe area. One function so the style line and slide_up's \\move anchor can
    never disagree about where the text actually sits."""
    fy = play_res[1] / BASE_PLAY_RES[1]
    base = round(MARGIN_V.get(position, 46) * fy)
    if position != "bottom" or play_res[1] <= play_res[0] * 1.05:
        return base
    return max(base, int(round(play_res[1] * VERTICAL_BOTTOM_SAFE_FRAC)))

DEFAULT_STYLE = {"color": "#FFFFFF", "size": "m", "position": "bottom",
                 "dynamic": False, "highlight_color": None, "animation": None,
                 "size_scale": None, "preset": None, "uppercase": None,
                 # composer fields (premium presets only; see the composer
                 # section below). None everywhere = "use the preset's own".
                 "font": None, "effect": None, "layout": None,
                 "leading": None, "emphasis": None, "emphasis_scale": None,
                 # Independent production controls. None means the preset's
                 # authored value (or the byte-compatible legacy default).
                 "outline_color": None, "outline_width": None,
                 "shadow": None, "background_color": None,
                 "background_opacity": None, "tracking": None,
                 "text_align": None, "anchor_y": None, "single_line": None}

# Every style key that flows from the EDL into a render. Kept as ONE tuple
# because it has to be applied in three places (_norm_style, write_ass's
# per-item merge, and the schema/tool allowlists mirror it) and the previous
# hand-copied lists were exactly how a new field got silently dropped: pydantic
# ignores undeclared fields, so the EDL signature never changed, write_edl
# reported "NO CHANGE", no version was created, no render ran — and the agent
# told the user their new look had been applied. Add a field HERE and to
# DEFAULT_STYLE, then to schemas.CaptionStyle and the tool allowlist.
STYLE_KEYS = ("color", "size", "position", "highlight_color", "animation",
              "size_scale", "preset", "font", "effect", "layout", "leading",
              "emphasis", "emphasis_scale", "outline_color", "outline_width",
              "shadow", "background_color", "background_opacity", "tracking",
              "text_align", "anchor_y", "single_line")
# Keys whose value is meaningful when falsy (0, 0.0) and so must NOT be copied
# with a truthiness test.
STYLE_KEYS_NUMERIC = ("leading", "emphasis_scale", "size_scale",
                      "outline_width", "shadow", "background_opacity",
                      "tracking", "anchor_y")
# Fields where False is an authored value rather than an omission. Keeping
# this separate from numeric fields makes partial restyles able to turn a
# previously enabled contract off without changing any historical default.
STYLE_KEYS_EXPLICIT = STYLE_KEYS_NUMERIC + ("single_line",)

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

# ── Composable per-word treatments ───────────────────────────────────────
# A treatment is a set of INDEPENDENT properties. The first engine welded
# size to colour — the only way to enlarge a word was to also recolour it —
# so the most common look in the reference reels (ONE WHITE WORD at ~2x its
# white neighbours, no colour change at all) could not be expressed. Scale,
# colour, font, box and effect are now orthogonal; a preset composes them.
#
#   scale  — multiplier on the base font px (None = preset emph_scale)
#   color  — "accent" | "dark" | None (keep the caption colour)
#   font   — font family override
#   italic — synthetic italic
#   box    — draw the accent as a filled box behind dark text
#   effect — layered treatment from EFFECTS (chrome/chroma/glow)
TREATMENTS = {
    "none":   {},
    # size-only emphasis: the reference look, and the reason this table exists
    "big":    {"scale": "emph"},
    "huge":   {"scale": "num"},
    "accent": {"scale": "emph", "color": "accent"},
    "pop":    {"scale": "num", "color": "accent"},
    "box":    {"box": True},
    "serif":  {"scale": "emph", "color": "accent", "font": SERIF_FONT,
               "italic": True, "serif_bump": True},
    # The lyric-edit hero word: a big WHITE italic display serif — same
    # skeleton as "serif" but keeping the caption colour, because in the
    # reference reels the script word is white like its neighbours and the
    # contrast is pure form ("we gotta be / excited").
    "script": {"scale": "emph", "font": "Playfair Display Black",
               "italic": True, "serif_bump": True},
    "chrome": {"scale": "emph", "effect": "chrome"},
    "glow":   {"scale": "emph", "color": "accent", "effect": "glow"},
    "chroma": {"scale": "emph", "effect": "chroma"},
    # numbers keep their size but not the accent in karaoke modes, where the
    # accent belongs to the word being SPOKEN or the read falls apart.
    "num_plain": {"scale": "num"},
}

# ── Layered text effects ─────────────────────────────────────────────────
# libass cannot gradient-fill or blur-shadow a glyph, but it CAN draw the
# same run several times on different layers with independent colour, alpha,
# offset and \clip. Each effect is a list of extra copies drawn UNDER the
# real text (or, for chrome, INSTEAD of it), as
# (dx_frac, dy_frac, tags) where the offsets are fractions of the font px.
# Verified against real libass, not assumed.
EFFECTS = {
    # RGB split — the purple/cyan fringe of the reference "could see".
    "chroma": {"under": [(-0.030, 0.0, r"\1c&H0000FF&\3a&HFF&\shad0\alpha&H40&"),
                         (0.030, 0.0, r"\1c&HFFFF00&\3a&HFF&\shad0\alpha&H40&")]},
    # Soft halo in the accent colour.
    "glow": {"under": [(0.0, 0.0, r"\blur{blur}\3a&HFF&\shad0\alpha&H70&")]},
    # Metallic ramp: horizontal bands of graduated grey, each \clip'd to its
    # own slice of the line box. Colours are &HBBGGRR&, so B >= G >= R reads
    # as COOL steel rather than muddy bronze. The sequence is a real chrome
    # profile, not a linear fade — bright crown, a dark "horizon" line about
    # 40% down, a hot specular bounce just under it, then a mid falloff. That
    # horizon is what makes it read as polished metal instead of grey text.
    "chrome": {"bands": ("&HFFFFFF&", "&HFAF7F2&", "&HEFEAE2&", "&HD6CEC2&",
                         "&H8F857A&", "&H6E645A&", "&HFFFEFB&", "&HF2EDE6&",
                         "&HD2CAC0&", "&HAAA096&", "&H8C8278&")},
}
CHROME_BAND_MIN_PX = 26   # below this the bands alias into mush
PRESETS = {
    # ROUND 67 — POSITION DEFAULTS MOVED OFF THE FACE. Every multi-word
    # preset used to anchor "middle", which on a talking head is a block of
    # text across the speaker's face — the owner's words: "put the captions
    # always to the bottom area, not the face area, unless it's a single
    # word at a time". Multi-word looks now default "bottom" (the burner's
    # safe-area logic already lifts them clear of platform chrome); ONLY the
    # single-word 'spotlight' keeps "middle", because one word at a time is
    # small enough to share the frame with a face. style.position still
    # overrides everything.
    "podcast": {
        # The reference reel look: bold white grotesque, left-aligned stack,
        # words land as spoken, keywords get yellow / a marker box / serif.
        "font": "Inter Display ExtraBold", "char_w": 0.56, "base_size": 44,
        "mode": "reveal", "align": "left", "uppercase": False,
        "max_words": 5, "wpl": 2, "outline": 1.5, "shadow": 2.2,
        "emph_scale": 1.28, "num_scale": 1.85,
        "treatments": ("accent", "box", "serif"),
        "active": "pop", "position": "bottom",
    },
    "reels": {
        # Flagship short-form system: tight two-line composition, elastic
        # word landings and one high-contrast hero word per phrase. The
        # grammar stays coherent (one face, one accent, one motion curve),
        # which makes it feel designed rather than like a random effects pack.
        "font": "Inter Display Black", "char_w": 0.56, "base_size": 50,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 4, "wpl": 2, "outline": 0.8, "shadow": 2.5,
        "emph_scale": 1.72, "num_scale": 2.05,
        "treatments": ("accent",), "emphasis": "accent",
        "number_treatment": "num_plain",
        "active": "pop", "position": "bottom",
        "layout": "stack", "leading": 0.88, "stagger": 0.045,
        "word_anim": "elastic", "highlight": "#FFE15A",
        "punctuation": "expressive", "target_words": 3,
    },
    "beast": {
        # Loud creator style: Anton caps, centered, spoken word pops in the
        # accent color. Big by default — 'm' here reads like legacy 'l/xl'.
        "font": "Anton", "char_w": 0.50, "base_size": 54,
        "mode": "karaoke", "align": "center", "uppercase": True,
        "max_words": 3, "wpl": 3, "outline": 3.0, "shadow": 2.6,
        "emph_scale": 1.1, "num_scale": 1.35,
        "treatments": ("accent",),
        "active": "accent", "position": "bottom",
    },
    "spotlight": {
        # ROUND 67 — ONE word at a time, dead centre, glowing. The modern
        # single-word look ("Hormozi-style") the owner asked for by name:
        # each word owns the frame for exactly as long as it is spoken, big
        # bold caps with a soft accent halo. mode 'karaoke' with 1-word
        # groups makes every word the ACTIVE word, so the glow treatment
        # rides the existing active-word machinery — no new emission path.
        # The ONLY preset that may sit "middle": a single word shares the
        # frame with a face; a paragraph does not.
        "font": "Inter Display Black", "char_w": 0.56, "base_size": 60,
        "mode": "karaoke", "align": "center", "uppercase": True,
        "max_words": 1, "wpl": 1, "outline": 0.0, "shadow": 2.6,
        "emph_scale": 1.22, "num_scale": 1.45,
        "treatments": ("glow",),
        "active": "glow", "position": "middle",
        # Stack emission, one word per line: the layered EFFECTS (the glow
        # under-copies) only exist on the stack path, and a single word is
        # trivially a one-line stack. effect='glow' applies the halo to
        # EVERY word, not just emphasis — the whole point of the look.
        "layout": "stack", "leading": 1.0, "stagger": 0.0,
        "effect": "glow", "word_anim": "punch",
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
        "punctuation": "full", "target_words": 6,
    },

    # ── Coherent production families ────────────────────────────────
    # These cover the common briefs that previously got forced through a
    # novelty social preset. Each has one visual grammar; none rotates among
    # unrelated boxes, serif faces and colours inside the same sentence.
    "clean": {
        # Safe creator default: the whole short phrase is readable at once,
        # with hierarchy carried by size only. White, mixed-case, no gimmick.
        "font": "Plus Jakarta Sans", "char_w": 0.55, "base_size": 36,
        "mode": "static", "align": "center", "uppercase": False,
        "max_words": 5, "wpl": 3, "outline": 1.5, "shadow": 2.0,
        "emph_scale": 1.22, "num_scale": 1.45,
        "treatments": ("big",), "emphasis": "big",
        "number_treatment": "num_plain",
        "active": None, "position": "bottom", "animation": "fade",
        "layout": "stack", "leading": 1.02, "stagger": 0.0,
        "word_anim": "fade",
        "punctuation": "expressive", "target_words": 4,
    },
    "documentary": {
        # Long-form/readability family: restrained two-line subtitles on a
        # translucent charcoal plate, suitable for interviews, education and
        # footage with changing/bright backgrounds.
        "font": "Plus Jakarta Sans", "char_w": 0.53, "base_size": 32,
        "mode": "static", "align": "center", "uppercase": False,
        "max_words": 12, "wpl": 6, "outline": 0.0, "shadow": 0.0,
        "emph_scale": 1.12, "num_scale": 1.2,
        "treatments": ("big",), "emphasis": "big",
        "number_treatment": "num_plain",
        "active": None, "position": "bottom", "animation": "fade",
        "background_color": "#111318", "background_opacity": 0.72,
        "box_pad": 8.0, "tracking": 0.0, "anchor_y": 0.76,
        "punctuation": "full", "target_words": 8,
    },
    "broadcast": {
        # News/explainer lower-third: confident left alignment and an opaque
        # enough plate to survive B-roll, charts and newsroom footage.
        "font": "Plus Jakarta Sans", "char_w": 0.55, "base_size": 30,
        "mode": "static", "align": "left", "uppercase": False,
        "max_words": 8, "wpl": 4, "outline": 0.0, "shadow": 0.0,
        "emph_scale": 1.18, "num_scale": 1.32,
        "treatments": ("accent",), "emphasis": "accent",
        "active": None, "position": "bottom", "animation": "slide_up",
        "background_color": "#0A0D14", "background_opacity": 0.82,
        "box_pad": 9.0, "tracking": 0.25, "anchor_y": 0.76,
        "punctuation": "full", "target_words": 6,
    },
    "retro": {
        # Condensed poster type with a deliberate thick keyline. Useful for
        # sports/history/comedy without the constant word-colour churn of a
        # karaoke preset.
        "font": "Bebas Neue", "char_w": 0.40, "base_size": 56,
        "mode": "static", "align": "center", "uppercase": True,
        "max_words": 5, "wpl": 3, "outline": 3.2, "shadow": 2.4,
        "emph_scale": 1.38, "num_scale": 1.6,
        "treatments": ("big",), "emphasis": "big",
        "number_treatment": "num_plain",
        "active": None, "position": "bottom", "animation": "pop",
        "tracking": 1.1,
    },
    "neon": {
        # A focused glow family rather than generic rainbow/chroma: two-word
        # groups, one cool accent, heavy Syne display type.
        "font": "Syne ExtraBold", "char_w": 0.58, "base_size": 50,
        "mode": "karaoke", "align": "center", "uppercase": True,
        "max_words": 2, "wpl": 2, "outline": 0.8, "shadow": 2.0,
        "emph_scale": 1.18, "num_scale": 1.4,
        "treatments": ("glow",), "emphasis": "glow",
        "active": "glow", "position": "bottom",
        "layout": "stack", "leading": 1.0, "stagger": 0.0,
        "effect": "glow", "word_anim": "punch",
        "highlight": "#7DEBFF", "tracking": -0.4,
    },

    # ── Composed looks (layout "stack") ──────────────────────────────
    # These use the per-line composer: every line is its own Dialogue with
    # its own \pos, which is what makes tight/overlapping leading and
    # per-line horizontal stagger possible. The four presets above keep the
    # original single-Dialogue emission byte-for-byte ("flow").
    "stacked": {
        # THE reference reel look: one phrase broken into 2-3 lines whose
        # sizes differ wildly ("Your" small / "VIDEOS" huge / "don't" small),
        # set tight enough to interlock. All one colour — the emphasis is
        # pure SCALE, which is why treatments had to stop implying colour.
        "font": "Inter Display Black", "char_w": 0.56, "base_size": 46,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 4, "wpl": 2, "outline": 0.0, "shadow": 2.4,
        "emph_scale": 2.05, "num_scale": 2.2,
        "treatments": ("big",), "emphasis": "big",
        "number_treatment": "num_plain",
        "active": "pop", "position": "bottom",
        "layout": "stack", "leading": 0.86, "stagger": 0.055,
        "word_anim": "punch",
    },
    "iridescent": {
        # Same architecture, with the RGB-split fringe of the reference
        # "could see" / "even built" frames.
        "font": "Inter Display Black", "char_w": 0.56, "base_size": 44,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 4, "wpl": 2, "outline": 0.0, "shadow": 2.0,
        "emph_scale": 1.85, "num_scale": 2.0,
        "treatments": ("chroma",), "emphasis": "chroma",
        "active": "pop", "position": "bottom",
        "layout": "stack", "leading": 0.84, "stagger": 0.06,
        "word_anim": "blur_in",
    },
    "chrome": {
        # Liquid-metal hero word over a small connector line — the "Love"
        # frame. Bands are \clip'd greys; see EFFECTS["chrome"].
        "font": "Inter Display Black", "char_w": 0.56, "base_size": 48,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 4, "wpl": 2, "outline": 0.0, "shadow": 2.6,
        "emph_scale": 2.0, "num_scale": 2.1,
        "treatments": ("chrome",), "emphasis": "chrome",
        "active": "pop", "position": "bottom",
        "layout": "stack", "leading": 0.88, "stagger": 0.05,
        "word_anim": "punch",
    },
    "editorial": {
        # The light, quiet counterpoint: thin high-contrast serif, generous
        # air, no outline. For luxury/interior/fashion footage where a heavy
        # grotesque would look cheap.
        "font": "Instrument Serif", "char_w": 0.44, "base_size": 46,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 5, "wpl": 3, "outline": 0.0, "shadow": 1.4,
        "emph_scale": 1.5, "num_scale": 1.7,
        "treatments": ("big",), "emphasis": "big",
        "number_treatment": "num_plain",
        "active": "fade", "position": "bottom",
        "layout": "stack", "leading": 1.06, "stagger": 0.0,
        "word_anim": "fade",
        "punctuation": "full", "target_words": 4,
    },
    "fashion": {
        # Wide, editorial, all-caps — magazine cover energy.
        "font": "Archivo Black", "char_w": 0.62, "base_size": 40,
        "mode": "reveal", "align": "center", "uppercase": True,
        "max_words": 4, "wpl": 2, "outline": 0.0, "shadow": 2.0,
        "emph_scale": 1.6, "num_scale": 1.8,
        "treatments": ("big", "accent"), "emphasis": "big",
        "active": "pop", "position": "bottom",
        "layout": "stack", "leading": 0.98, "stagger": 0.04,
        "word_anim": "rise",
    },
    "luxe": {
        # High-contrast Playfair display serif with gold accents — the
        # "expensive" ask, without shouting.
        "font": "Playfair Display Black", "char_w": 0.50, "base_size": 44,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 5, "wpl": 3, "outline": 0.0, "shadow": 1.8,
        "emph_scale": 1.55, "num_scale": 1.75,
        "treatments": ("accent", "big"), "emphasis": "accent",
        "active": "fade", "position": "bottom",
        "layout": "stack", "leading": 1.0, "stagger": 0.0,
        "word_anim": "fade",
        "punctuation": "full", "target_words": 4,
    },
    "impact": {
        # Bebas condensed caps, stacked tight — sports/hype without Anton's
        # width, so longer words still fit a vertical frame.
        "font": "Bebas Neue", "char_w": 0.40, "base_size": 58,
        "mode": "karaoke", "align": "center", "uppercase": True,
        "max_words": 4, "wpl": 2, "outline": 2.2, "shadow": 2.4,
        "emph_scale": 1.5, "num_scale": 1.7,
        "treatments": ("accent",), "emphasis": "accent",
        "active": "accent", "position": "bottom",
        "layout": "stack", "leading": 0.9, "stagger": 0.0,
        "word_anim": "punch",
    },
    "lyric": {
        # The mixed-face LYRIC EDIT (round 99b, built from the owner's "he
        # won" reference reel): heavy lowercase Poppins phrases dead centre,
        # words landing as spoken, and the stressed word blown up ~2x on its
        # own line in a white Playfair Black Italic ("we gotta be /
        # excited"). Mostly white; the gold DEFAULT_HIGHLIGHT is what the
        # 'accent' treatment tints whole phrases with ("do you wanna").
        # Middle placement is the look's SIGNATURE — the text deliberately
        # owns the frame between beats — so this is the second sanctioned
        # exception to the placement law (taste.py knows it by name, like
        # spotlight). style.position still overrides.
        "font": "Poppins Black", "char_w": 0.62, "base_size": 44,
        "mode": "reveal", "align": "center", "uppercase": False,
        "max_words": 4, "wpl": 3, "outline": 0.0, "shadow": 2.2,
        "emph_scale": 1.95, "num_scale": 2.15,
        "treatments": ("script", "big", "accent"), "emphasis": "script",
        "active": "pop", "position": "middle",
        "layout": "stack", "leading": 0.96, "stagger": 0.0,
        "word_anim": "punch",
    },
}
# Composer defaults for presets that don't set them (the four "flow" looks).
PRESET_DEFAULTS = {"layout": "flow", "leading": 1.34, "stagger": 0.0,
                   "emphasis": None, "word_anim": None, "effect": None}


def _pget(p, key):
    return p.get(key, PRESET_DEFAULTS.get(key))
# Block-center anchor as a fraction of frame height, per position.
PREMIUM_ANCHOR_Y = {"top": 0.16, "middle": 0.50, "bottom": 0.80}
# Side margin as a fraction of frame width (left-aligned vs centered).
PREMIUM_MARGIN_X = {"left": 0.085, "center": 0.10, "right": 0.085}


def _premium_anchor(p, position, style=None):
    """Preset-aware vertical anchor; panel subtitles sit slightly higher so
    their backing block also clears a vertical platform's bottom UI band."""
    if (style or {}).get("anchor_y") is not None:
        return min(max(float(style["anchor_y"]), 0.05), 0.95)
    if position == "bottom" and p.get("anchor_y") is not None:
        return float(p["anchor_y"])
    return PREMIUM_ANCHOR_Y.get(position, 0.5)

# Karaoke (dynamic) captions: groups of up to N words; the word being
# spoken pops and lights up in the highlight color. Groups larger than
# KARAOKE_HARD_MAX read as a wall of text, so max_words is clamped there.
# THIS CLAMP CAN NEVER MOVE: it is applied at RENDER time, and 3 stored
# prod EDLs (proj 13 v3-5, dynamic + max_words 6 — written in the round-7
# window before the tool-side clamp existed) render 4-word groups under it;
# raising it would make a fresh render of those versions differ from their
# cached previews. Group sizes above 4 ride the NEW captions.karaoke_group_n
# field instead, baked by the tools at write time (round 35).
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


def ass_color_alpha(hex_rgb, opacity=1.0):
    """#RRGGBB + human opacity -> ASS &HAABBGGRR.

    ASS alpha runs backwards (00 opaque, FF invisible), which is easy to get
    wrong and previously made a reusable caption panel impossible.
    """
    h = (hex_rgb or "#000000").lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    try:
        op = min(max(float(opacity), 0.0), 1.0)
    except (TypeError, ValueError):
        op = 1.0
    alpha = round((1.0 - op) * 255)
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _norm_style(style):
    s = dict(DEFAULT_STYLE)
    s["_pos_set"] = False
    if style:
        d = style if isinstance(style, dict) else style.model_dump()
        for k in STYLE_KEYS:
            v = d.get(k)
            # Numeric fields are meaningful at 0 and contract booleans at
            # False, so those fields are copied on presence, not truthiness.
            if v is not None if k in STYLE_KEYS_EXPLICIT else bool(v):
                s[k] = v
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


def _align_of(p, s):
    return (s or {}).get("text_align") or p.get("align", "center")


def _outline_of(p, s):
    v = (s or {}).get("outline_width")
    return p.get("outline", 0.0) if v is None else float(v)


def _shadow_of(p, s):
    v = (s or {}).get("shadow")
    return p.get("shadow", 0.0) if v is None else float(v)


def _tracking_of(p, s):
    v = (s or {}).get("tracking")
    return p.get("tracking", 0.0) if v is None else float(v)


def _background_of(p, s):
    """(colour, opacity, padding) for the event-level backing plate."""
    color = (s or {}).get("background_color") or p.get("background_color")
    opacity = (s or {}).get("background_opacity")
    if opacity is None:
        opacity = p.get("background_opacity", 0.0)
    try:
        opacity = min(max(float(opacity or 0.0), 0.0), 1.0)
    except (TypeError, ValueError):
        opacity = 0.0
    return color or "#000000", opacity, float(p.get("box_pad", 7.0))


def _highlight_of(p, s):
    return (s or {}).get("highlight_color") or p.get("highlight") \
        or DEFAULT_HIGHLIGHT


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
    if anim in ("slide_up", "rise", "drop"):
        # \move needs the real anchor point: derive it from the alignment
        # + margins exactly as style_line computes them.
        s = _norm_style(style)
        align = {"left": 4, "center": 5, "right": 6}.get(
            s.get("text_align") or "center", 5)
        margin_x = max(10, round(60 * play_res[0] / BASE_PLAY_RES[0]))
        if align == 4:
            cx = margin_x
        elif align == 6:
            cx = int(play_res[0]) - margin_x
        else:
            cx = int(play_res[0] / 2)
        if s.get("anchor_y") is not None:
            y = int(float(s["anchor_y"]) * play_res[1])
        else:
            margin_v = bottom_margin_v(s["position"], play_res)
            if s["position"] == "top":
                y = margin_v
            elif s["position"] == "middle":
                y = int(play_res[1] / 2)
            else:
                y = int(play_res[1]) - margin_v
        off = max(12, int(0.04 * play_res[1]))
        source_y = y - off if anim == "drop" else y + off
        move_ms = 160 if anim == "slide_up" else 180
        return (rf"{{\an{align}\move({cx},{source_y},{cx},{y},0,{move_ms})"
                r"\fad(120,0)}}")
    if anim in WORD_ANIMS:
        s = _norm_style(style)
        f = max(play_res[0] / BASE_PLAY_RES[0],
                play_res[1] / BASE_PLAY_RES[1])
        px = max(10, round(FONT_SIZES.get(s["size"], 40) * f
                           * _size_scale(s)))
        tags = _word_anim_tags(anim, px)
        return "{" + tags + "}" if tags else ""
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
    margin_v = bottom_margin_v(s["position"], play_res)
    raw_outline = 2.4 if s.get("outline_width") is None \
        else float(s["outline_width"])
    outline = max(0.0, round(raw_outline * f, 1))
    raw_shadow = 0.0 if s.get("shadow") is None else float(s["shadow"])
    shadow = max(0.0, round(raw_shadow * f, 1))
    tracking = round(float(s.get("tracking") or 0.0) * f, 2)
    if abs(tracking) < 0.001:
        tracking = 0
    bg_color = s.get("background_color") or "#000000"
    bg_opacity = float(s.get("background_opacity") or 0.0)
    border_style = 3 if bg_opacity > 0.001 else 1
    back_ass = ass_color_alpha(bg_color, bg_opacity) \
        if border_style == 3 else "&H96000000"
    outline_ass = ass_color_alpha(bg_color, bg_opacity) \
        if border_style == 3 \
        else ass_color(s.get("outline_color") or "#101010")
    if border_style == 3:
        # In BorderStyle 3 ASS uses Outline as the plate padding. Preserve a
        # useful panel even when the caller requested no text outline.
        outline = max(outline, round(7.0 * f, 1))
    align_name = s.get("text_align") or "center"
    align_map = {
        "bottom": {"left": 1, "center": 2, "right": 3},
        "middle": {"left": 4, "center": 5, "right": 6},
        "top": {"left": 7, "center": 8, "right": 9},
    }
    # Honour an explicit font on the plain (non-preset) path too. Without this
    # a bare `font` override rendered DejaVu Sans while the agent reported the
    # requested family — a silent font-drop. Montserrat's only application path
    # is a bare override (no preset uses it), so this closes a real honesty gap.
    fam = s.get("font") or "DejaVu Sans"
    return (f"Style: {name},{fam},{font},"
            f"{ass_color(s['color'])},&H00FFFFFF,"
            f"{outline_ass},"
            f"{back_ass},"
            f"-1,0,0,0,100,100,{tracking},0,{border_style},{outline},{shadow},"
            f"{align_map.get(s['position'], align_map['bottom']).get(align_name, 2)},"
            f"{margin_lr},{margin_lr},"
            f"{margin_v},-1")


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


def _clamp_events_to_inserts(events, tl):
    """Shorten any caption whose DISPLAY tail holds across an inserted screen.
    Grouping is broken at inserts (_mark_insert_breaks) so no event's WORDS
    span one, but premium/karaoke extend an event's end to hold until the next
    caption — that hold would stretch the last pre-insert caption over the
    insert. No word ever plays during an insert, so clamping the end down to
    the insert's start only trims dead hold time; word \\k timings (all before
    the insert) are untouched. Never moves a start."""
    windows = tl.insert_positions() if tl else []
    if not windows or not events:
        return events
    for ev in events:
        for ws, wd in windows:
            if ev["start"] < ws - 0.001 and ev["end"] > ws:
                ev["end"] = ws
    return [ev for ev in events if ev["end"] - ev["start"] > 0.02]


def _mark_insert_breaks(out_words, tl):
    """Flag the first spoken word after each spliced insert with brk=True, so
    caption grouping never packs words from both sides of an inserted screen
    into ONE caption event — that event would span the insert and burn over it.
    A LONG insert already forces a flush via the >1.2s output-time gap; a SHORT
    one (a 0.3s white flash) does not, which is the case this covers. Only
    inserts create the discontinuity here — a plain hard cut between kept
    segments is left grouping exactly as before, so no insert-free EDL's
    captions (or their render cache) change."""
    windows = tl.insert_positions() if tl else []
    if not windows or not out_words:
        return out_words
    prev_end = None
    for w in out_words:
        if prev_end is not None:
            for ws, wd in windows:
                if prev_end <= ws + 0.02 and ws + wd <= w["t0"] + 0.02:
                    w["brk"] = True
                    break
        prev_end = w["t1"]
    return out_words


def events_from_transcript(out_words, max_words=None, line_chars=MAX_LINE_CHARS,
                           single_line=False):
    """out_words: [{'w','t0','t1'}] already in OUTPUT time (kept words only).
    Groups words into events of at most 2 lines x line_chars chars — or at
    most max_words words per event when set — timed to word boundaries.
    ``single_line`` is an explicit new contract; absent/false preserves the
    historical two-line grouping and serialized ASS exactly."""
    events = []
    group, chars = [], 0
    max_lines = 1 if single_line else MAX_LINES
    limit = line_chars * max_lines

    def flush():
        nonlocal group, chars
        if not group:
            return
        text = " ".join(w["w"] for w in group)
        lines = _wrap(text, line_chars)[:max_lines]
        start = group[0]["t0"]
        end = max(group[-1]["t1"], start + MIN_EVENT_S)
        events.append({"start": start, "end": end,
                       "text": r"\N".join(_esc(l) for l in lines)})
        group, chars = [], 0

    for w in out_words:
        gap = (w["t0"] - group[-1]["t1"]) if group else 0.0
        full = (chars + 1 + len(w["w"]) > limit or
                (max_words and len(group) >= max_words))
        if group and (full or gap > 1.2 or w.get("brk")):
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
                   line_chars=MAX_LINE_CHARS, karaoke_group_n=None):
    """Karaoke captions (modern reels style): the phrase shows in groups of
    up to 3 words and the word being SPOKEN pops in and lights up in the
    highlight color; the others stay in the base caption color. One Dialogue
    per word — timing comes from the real transcript, never invented.
    Chunks are kept within one line's char budget so the pop animation never
    shifts a wrap point mid-word on narrow frames. An explicit
    karaoke_group_n (baked by the tools at write time) wins; without it the
    legacy interpretation of max_words is frozen forever (see
    KARAOKE_HARD_MAX)."""
    s = _norm_style(style)
    hl = _inline_hl(s.get("highlight_color") or DEFAULT_HIGHLIGHT)
    if karaoke_group_n:
        group_n = max(1, int(karaoke_group_n))
    else:
        group_n = min(int(max_words), KARAOKE_HARD_MAX) if max_words \
            else KARAOKE_MAX_WORDS
    # `animation=none` means exactly no motion. The active word still changes
    # colour—otherwise karaoke would stop communicating which word is being
    # spoken—but it never scales or eases. This branch used to ignore the
    # setting even though the tool reported it as accepted.
    active_pre = ((r"{\1c" + hl + "}") if s.get("animation") == "none" else
                  (r"{\1c" + hl + r"\fscx62\fscy62"
                   r"\t(0,90,\fscx114\fscy114)"
                   r"\t(90,170,\fscx106\fscy106)}"))
    chunks, cur, chars = [], [], 0
    for w in out_words:
        would = chars + (1 if chars else 0) + len(w["w"])
        if cur and (w["t0"] - cur[-1]["t1"] > 1.2 or len(cur) >= group_n
                    or would > line_chars or w.get("brk")):
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


def _split_affixes(tok):
    """('leading punct', 'core', 'trailing punct') for one transcript token."""
    core = (tok or "").strip()
    lead = ""
    while core and core[0] in _STRIP_PUNCT:
        lead, core = lead + core[0], core[1:]
    trail = ""
    while core and core[-1] in _STRIP_PUNCT:
        trail, core = core[-1] + trail, core[:-1]
    return lead, core, trail


def apply_text_fixes(words, fixes):
    """Rewrite caption TEXT (never timing) from [[from, to], ...] pairs.

    Round 52. The transcriber writes what it heard, in lower case, and users
    care about this more than about any effect: one asked twice, in two
    projects, for "dios" to read "Dios" and "jesus" to read "Jesús"; another
    corrected a misheard name ("Ushula" -> "Ujjwala"). The honest answer at the
    time was "the system burns the real words of the transcript and I have no
    control over their capitalization", which is a strange thing for a video
    editor to say about the words on screen.

    Only the DISPLAYED string changes. Word timings are untouched, so karaoke
    and reveal presets stay in sync to the frame, and the audio still says what
    it always said. Matching ignores case and surrounding punctuation, and the
    punctuation is put back — "dios," becomes "Dios,".

    Multi-word pairs match consecutive tokens and are only accepted when both
    sides have the same word count (the tool rejects the rest), because a
    2-into-1 replacement would have to delete a word that still has time on the
    clock — and an empty caption event with a duration is a blink, not a fix.
    """
    if not words or not fixes:
        return words
    rules = []
    for pair in fixes:
        try:
            src, dst = (pair[0], pair[1]) if not isinstance(pair, dict) \
                else (pair.get("from"), pair.get("to"))
        except (IndexError, TypeError):
            continue
        s_toks = [t for t in str(src or "").split() if t]
        d_toks = [t for t in str(dst or "").split() if t]
        if not s_toks or not d_toks or len(s_toks) != len(d_toks):
            continue
        rules.append(([_norm_word(t) for t in s_toks], d_toks))
    if not rules:
        return words
    # Longest match first: "espiritu santo" must win over a bare "espiritu".
    rules.sort(key=lambda r: -len(r[0]))
    out = [dict(w) for w in words]
    i = 0
    while i < len(out):
        for src_toks, dst_toks in rules:
            n = len(src_toks)
            if i + n > len(out):
                continue
            if all(_norm_word(out[i + k].get("w")) == src_toks[k]
                   for k in range(n)):
                for k in range(n):
                    lead, _core, trail = _split_affixes(out[i + k].get("w"))
                    out[i + k]["w"] = lead + dst_toks[k] + trail
                i += n - 1
                break
        i += 1
    return out


def _display_word(w, upper):
    """Presentation form: captions in the premium looks drop trailing
    sentence punctuation (the reference style shows none)."""
    t = (w or "").strip().strip("\"'“”‘’").rstrip(".,!?;:…")
    if upper:
        t = t.upper()
    return t or (w or "").strip()


def _display_word_v2(w, upper, punctuation="expressive"):
    """Presentation form for newly-authored caption tracks.

    V1 removed every punctuation mark from every premium family.  That works
    for poster-like hype text, but it makes interviews and explainers harder
    to parse and erases the speaker's question/exclamation intent.  V2 keeps
    full punctuation for subtitle/editorial families and at least expressive
    ``?``/``!`` marks for creator looks.  Quotes are stripped only when they
    wrap a token; apostrophes inside words remain untouched.
    """
    raw = (w or "").strip().strip("\"'“”‘’")
    if punctuation == "full":
        t = raw
    elif punctuation == "none":
        t = raw.rstrip(".,!?;:…")
    else:
        # Social captions look cleaner without commas/full stops, while ?/!
        # materially change the read and should survive.
        expressive = ""
        m = re.search(r"([!?]+)[\"'”’]*$", raw)
        if m:
            expressive = m.group(1)
        t = raw.rstrip(".,!?;:…") + expressive
    if upper:
        t = t.upper()
    return t or raw or (w or "").strip()


def _premium_font_px(p, s, play_res):
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    mult = PRESET_SIZE_MULT.get(s.get("size"), 1.0)
    return max(12, round(p["base_size"] * mult * max(fx, fy) * _size_scale(s)))


def _premium_line_chars(p, s, play_res):
    px = _premium_font_px(p, s, play_res)
    margin = PREMIUM_MARGIN_X[_align_of(p, s)] * play_res[0]
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
    align_name = _align_of(p, s)
    margin = round(PREMIUM_MARGIN_X[align_name] * play_res[0])
    outline = round(_outline_of(p, s) * f, 1)
    shadow = round(_shadow_of(p, s) * f, 1)
    tracking = round(_tracking_of(p, s) * f, 2)
    if abs(tracking) < 0.001:
        tracking = 0
    # Premium panels are a single vector layer behind the WHOLE block (see
    # _premium_panel). BorderStyle 3 cannot be used here: every inline-styled
    # word becomes its own opaque rectangle, creating an ugly patchwork.
    border_style = 1
    outline_color = s.get("outline_color") or "#101010"
    back_ass = "&H96000000"
    outline_ass = ass_color(outline_color)
    return (f"Style: {name},{_font_of(p, s)},{px},"
            f"{ass_color(s['color'])},&H00FFFFFF,{outline_ass},"
            f"{back_ass},"
            f"0,0,0,0,100,100,{tracking},0,{border_style},{outline},{shadow},"
            f"5,{margin},{margin},"
            f"0,-1")


def _base_tags(p, s, px, f):
    """Full per-word reset: every word segment restates ALL varying
    properties, so treatments never leak between words (safer than \\r,
    which also resets alignment in some renderers)."""
    outline = round(_outline_of(p, s) * f, 1)
    shadow = round(_shadow_of(p, s) * f, 1)
    tracking = round(_tracking_of(p, s) * f, 2)
    spacing = rf"\fsp{tracking}" if abs(tracking) >= 0.001 else ""
    outline_color = _inline_hl(s.get("outline_color") or "#101010")
    return (rf"\fn{_font_of(p, s)}\fs{px}\b0\i0\1c{_inline_hl(s['color'])}"
            rf"\3c{outline_color}\bord{outline}"
            rf"\shad{shadow}{spacing}"
            rf"\fscx100\fscy100")


def _emph_scale(s, p):
    """Emphasis size multiplier: the style's override, else the preset's."""
    v = s.get("emphasis_scale")
    if v is None:
        return p["emph_scale"]
    try:
        return min(max(float(v), 1.0), 3.0)
    except (TypeError, ValueError):
        return p["emph_scale"]


def _treat_props(name, p, s):
    """A treatment name -> its concrete, ORTHOGONAL properties, with the
    symbolic scale ("emph"/"num") resolved to a real multiplier."""
    t = dict(TREATMENTS.get(name or "none", {}))
    sc = t.get("scale")
    mult = (_emph_scale(s, p) if sc == "emph"
            else p["num_scale"] if sc == "num" else 1.0)
    if t.get("serif_bump"):
        mult *= 1.05
    t["mult"] = mult
    return t


def _treat_tags(kind, p, px, accent, s=None, mult=None):
    """Inline overrides for one treated word. Legacy names still resolve, so
    the four original presets emit exactly what they always did."""
    s = s if s is not None else {}
    if kind == "num":               # legacy alias: accent + number scale
        return rf"\1c{accent}\fs{round(px * p['num_scale'])}"
    t = _treat_props(kind, p, s)
    if not t:
        return ""
    # Tag ORDER is preserved from the original hand-written emitter (font,
    # italic, colour, then size) so the four "flow" presets keep producing
    # byte-identical .ass output — libass does not care, but the regression
    # tests pin exact substrings, and that pinning is what proves this
    # refactor changed nothing for existing EDLs.
    out = ""
    if t.get("font"):
        out += rf"\fn{t['font']}"
    if t.get("italic"):
        out += r"\i1"
    if t.get("box"):
        bx, by = max(2, round(0.22 * px)), max(2, round(0.13 * px))
        out += rf"\1c{DARK_TEXT}\3c{accent}\xbord{bx}\ybord{by}\shad0"
    elif t.get("color") == "accent":
        out += rf"\1c{accent}"
    m = t.get("mult", 1.0) if mult is None else mult
    if m != 1.0:
        out += rf"\fs{round(px * m)}"
    return out


# ── Entrance animations ──────────────────────────────────────────────────
# Applied to the word being spoken (reveal) or the whole line (static). Each
# is pure ASS override tags, verified rendering under real libass. "rise" and
# "drop" are absent here on purpose: they need \move, which cannot coexist
# with the \pos the composer relies on, so they are handled as LINE-level
# geometry in _stack_positions instead of pretending to be per-word.
WORD_ANIMS = {
    "none": "",
    "pop": (r"\fscx62\fscy62\t(0,100,\fscx108\fscy108)"
            r"\t(100,180,\fscx100\fscy100)"),
    "punch": (r"\fscx44\fscy44\t(0,90,\fscx113\fscy113)"
              r"\t(90,190,\fscx100\fscy100)"),
    "fade": r"\alpha&HFF&\t(0,170,\alpha&H00&)",
    "blur_in": r"\blur{b}\fscx88\fscy88\t(0,220,\blur0\fscx100\fscy100)",
    "whip": (r"\frz14\fscx66\fscy66"
             r"\t(0,150,\frz0\fscx104\fscy104)\t(150,220,\fscx100\fscy100)"),
    "flash": r"\1c&HFFFFFF&\fscx70\fscy70\t(0,110,\fscx100\fscy100)",
    # Multi-stage easing is the difference between "text got bigger" and a
    # designed landing. These remain pure libass geometry, so they are crisp
    # at every output size and cost no intermediate raster pass.
    "elastic": (r"\fscx34\fscy34"
                r"\t(0,90,0.45,\fscx126\fscy126)"
                r"\t(90,175,1.6,\fscx94\fscy94)"
                r"\t(175,250,\fscx100\fscy100)"),
    "bounce": (r"\fscx76\fscy42"
               r"\t(0,95,0.55,\fscx106\fscy122)"
               r"\t(95,175,1.4,\fscx98\fscy92)"
               r"\t(175,245,\fscx100\fscy100)"),
    "swing": (r"\frz-13\fscx78\fscy78"
              r"\t(0,105,0.6,\frz5\fscx104\fscy104)"
              r"\t(105,190,1.5,\frz-2\fscx99\fscy99)"
              r"\t(190,255,\frz0\fscx100\fscy100)"),
    "zoom_blur": (r"\alpha&H88&\blur{b}\fscx165\fscy165"
                  r"\t(0,210,0.7,\alpha&H00&\blur0"
                  r"\fscx100\fscy100)"),
}
LINE_ANIMS = ("rise", "drop")


def _word_anim_tags(name, px):
    a = WORD_ANIMS.get(name or "none", "")
    if "{b}" in a:
        a = a.replace("{b}", str(max(2, round(px * 0.13))))
    return a


# entrance of the word being spoken (reveal mode / karaoke accent)
_POP_IN = (r"\fscx62\fscy62\t(0,100,\fscx108\fscy108)"
           r"\t(100,180,\fscx100\fscy100)")
_POP_ACTIVE = (r"\fscx58\fscy58\t(0,90,\fscx116\fscy116)"
               r"\t(90,170,\fscx104\fscy104)")


def _premium_anim_prefix(anim, px=48):
    """Entrance animation for premium STATIC events. slide_up would need
    \\move, which conflicts with the explicit \\pos geometry — it renders
    as a fade instead."""
    if anim == "pop":
        return (r"{\fscx70\fscy70\t(0,120,\fscx106\fscy106)"
                r"\t(120,200,\fscx100\fscy100)}")
    if anim in ("fade", "slide_up"):
        return r"{\fad(180,140)}"
    if anim in WORD_ANIMS:
        tags = _word_anim_tags(anim, px)
        return "{" + tags + "}" if tags else ""
    return ""


def _assign_treatments(chunk, emph, p, s=None, rot=None):
    """Per-word emphasis treatment. Digits are always emphasized (the huge
    '22' of the reference style — one per chunk, extras get the accent).
    Agent-chosen emphasis words rotate through the preset's treatments,
    with the counter carried ACROSS chunks so the look varies; at most one
    highlight box per chunk. Returns (treatments, rot)."""
    # Backward compatibility for the original internal signature
    # _assign_treatments(chunk, emph, preset, rotation). Tests, diagnostics and
    # any warm worker importing the helper keep working across the deploy.
    if rot is None and isinstance(s, (int, float)):
        rot, s = int(s), {}
    elif rot is None:
        rot = 0
    s = s or {}
    # The explicit style override used to validate, persist and appear in the
    # EDL diff but was never read here — so "make emphasis size-only" could be
    # reported as applied while the renderer kept drawing boxes/serif/color.
    # One concrete treatment replaces the preset rotation when supplied.
    chosen = (s or {}).get("emphasis")
    palette = (chosen,) if chosen is not None else p["treatments"]
    treats, num_used, box_used = [], False, False
    for w in chunk:
        token = w["w"]
        if _word_has_digit(token):
            number_kind = p.get("number_treatment", "num")
            extra_kind = p.get("extra_number_treatment",
                               "num_plain" if number_kind == "num_plain"
                               else "accent")
            treats.append(extra_kind if num_used else number_kind)
            num_used = True
        elif _norm_word(token) in emph:
            t = palette[rot % len(palette)]
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
                    or would > chunk_chars or w.get("brk")):
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


# Words that should not be stranded at a phrase boundary.  Timing and
# punctuation remain the primary signals, so languages absent from this small
# list still segment correctly; the list only prevents conspicuously amateur
# English/Spanish/French orphans such as "the / result" and "because / it".
_WEAK_BOUNDARY_WORDS = {
    "a", "an", "and", "as", "at", "because", "but", "by", "for", "from",
    "if", "in", "is", "of", "on", "or", "that", "the", "to", "with",
    "your", "my", "our", "their", "un", "una", "el", "la", "los", "las",
    "de", "del", "en", "por", "para", "que", "y", "et", "le", "les",
    "des", "du", "avec", "pour",
}
_STRONG_END = ".!?…"
_SOFT_END = ",;:—–"


def _chunk_word_key(word):
    return _norm_word((word or {}).get("w") or "")


def _hard_phrase_break(prev, nxt):
    """Whether two consecutive timed words may never share one card."""
    if nxt.get("brk"):
        return True
    try:
        gap = float(nxt["t0"]) - float(prev["t1"])
    except (KeyError, TypeError, ValueError):
        gap = 0.0
    token = str(prev.get("w") or "").rstrip("\"'”’ ")
    # 620 ms is a real breath/beat in speech.  V1 waited 1.2 s, long enough
    # to glue separate thoughts into the same visual sentence.
    return gap >= 0.62 or token[-1:] in _STRONG_END


def _chunk_duration_cap(p):
    """Longest spoken span one v2 caption card should cover."""
    if p.get("max_chunk_s") is not None:
        return float(p["max_chunk_s"])
    if p["mode"] == "karaoke":
        return 1.85
    if p["mode"] == "reveal":
        return 2.45
    if p.get("max_words", 0) >= 10:       # documentary/accessibility
        return 4.6
    return 3.2


def _chunk_region_v2(words, max_w, chunk_chars, p):
    """Globally optimize phrase boundaries inside one breath/sentence.

    The old greedy splitter always filled to the cap.  A human editor instead
    balances card length, breath timing, punctuation and grammar, and will
    rebalance the previous card to avoid leaving a one-word widow.  Dynamic
    programming gives that result deterministically in O(words * max_w).
    """
    n_words = len(words)
    if not words:
        return []
    target = int(p.get("target_words") or (
        min(max_w, 2) if p["mode"] == "karaoke" else
        min(max_w, 3) if p["mode"] == "reveal" else
        min(max_w, 6 if max_w >= 8 else 4)))
    target = max(1, min(target, max_w))
    duration_cap = _chunk_duration_cap(p)
    # dp[i] = (cost from i onward, tuple of exclusive end indexes).
    dp = [(float("inf"), ()) for _ in range(n_words + 1)]
    dp[n_words] = (0.0, ())
    for i in range(n_words - 1, -1, -1):
        chars = 0
        for j in range(i, min(n_words, i + max_w)):
            token = str(words[j].get("w") or "")
            chars += len(token) + (1 if j > i else 0)
            if chars > chunk_chars and j > i:
                break
            span = max(0.0, float(words[j].get("t1", 0)) -
                       float(words[i].get("t0", 0)))
            if span > duration_cap and j > i:
                break
            rest_cost, rest_path = dp[j + 1]
            if rest_cost == float("inf"):
                continue
            count = j - i + 1
            # Prefer the authored target, but make a one-word card expensive
            # unless the preset itself is one-word (spotlight).
            cost = 0.34 * (count - target) ** 2
            if count == 1 and max_w > 1:
                cost += 2.4
            # Very short multi-word flashes read as flicker; excessively long
            # cards feel like subtitles pasted over a reel.
            ideal_s = (1.35 if p["mode"] == "karaoke" else
                       1.75 if p["mode"] == "reveal" else
                       2.7 if max_w >= 8 else 2.05)
            cost += 0.10 * (span - ideal_s) ** 2
            if count > 1 and span < 0.55:
                cost += 0.45
            if j + 1 < n_words:
                prev_raw = str(words[j].get("w") or "").rstrip("\"'”’ ")
                gap = max(0.0, float(words[j + 1].get("t0", 0)) -
                          float(words[j].get("t1", 0)))
                # Reward a natural micro-pause or clause mark.
                cost -= min(gap, 0.55) * 1.8
                if prev_raw[-1:] in _SOFT_END:
                    cost -= 0.85
                # Never willingly strand a connector/determiner on either
                # side of the card change when another valid split exists.
                if _chunk_word_key(words[j]) in _WEAK_BOUNDARY_WORDS:
                    cost += 2.1
                if _chunk_word_key(words[j + 1]) in _WEAK_BOUNDARY_WORDS:
                    cost += 0.8
            total = cost + rest_cost
            candidate = (total, (j + 1,) + rest_path)
            if candidate < dp[i]:
                dp[i] = candidate
    path = dp[0][1]
    if not path:
        return [words]
    out, start = [], 0
    for end in path:
        out.append(words[start:end])
        start = end
    return out


def _premium_chunks_v2(out_words, max_w, chunk_chars, p):
    """Prosody-aware caption cards for design_version 2."""
    regions, current = [], []
    for word in out_words:
        if current and _hard_phrase_break(current[-1], word):
            regions.append(current)
            current = []
        current.append(word)
    if current:
        regions.append(current)
    chunks = []
    for region in regions:
        chunks.extend(_chunk_region_v2(region, max_w, chunk_chars, p))
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
    anchor = _premium_anchor(p, pos_name, s) * H
    scale_of = {"num": p["num_scale"], "num_plain": p["num_scale"],
                "accent": p["emph_scale"], "serif": p["emph_scale"],
                "box": 1.0}
    line_hs = [1.34 * px * max((scale_of.get(treats[i], 1.0) for i in ln),
                               default=1.0) for ln in lines]
    block_h = sum(line_hs)
    edge = 0.03 * H
    align = _align_of(p, s)
    if align == "left":
        x = round(PREMIUM_MARGIN_X["left"] * W)
        y = max(edge, min(anchor - block_h / 2, H - block_h - edge))
        return rf"{{\an7\pos({x},{round(y)})}}"
    if align == "right":
        x = round(W - PREMIUM_MARGIN_X["right"] * W)
        y = max(edge, min(anchor - block_h / 2, H - block_h - edge))
        return rf"{{\an9\pos({x},{round(y)})}}"
    x = round(W / 2)
    y = max(block_h / 2 + edge, min(anchor, H - block_h / 2 - edge))
    return rf"{{\an5\pos({x},{round(y)})}}"


def _rounded_rect_path(x0, y0, x1, y1, radius):
    """ASS vector path for a rounded rectangle (cubic corner curves)."""
    r = max(0.0, min(float(radius), (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    if r < 1.0:
        return (f"m {round(x0)} {round(y0)} l {round(x1)} {round(y0)} "
                f"l {round(x1)} {round(y1)} l {round(x0)} {round(y1)} "
                f"l {round(x0)} {round(y0)}")
    # kappa gives a close cubic approximation of a quarter circle.
    k = r * 0.55228475
    q = lambda value: int(round(value))
    return (
        f"m {q(x0 + r)} {q(y0)} l {q(x1 - r)} {q(y0)} "
        f"b {q(x1 - r + k)} {q(y0)} {q(x1)} {q(y0 + r - k)} "
        f"{q(x1)} {q(y0 + r)} l {q(x1)} {q(y1 - r)} "
        f"b {q(x1)} {q(y1 - r + k)} {q(x1 - r + k)} {q(y1)} "
        f"{q(x1 - r)} {q(y1)} l {q(x0 + r)} {q(y1)} "
        f"b {q(x0 + r - k)} {q(y1)} {q(x0)} {q(y1 - r + k)} "
        f"{q(x0)} {q(y1 - r)} l {q(x0)} {q(y0 + r)} "
        f"b {q(x0)} {q(y0 + r - k)} {q(x0 + r - k)} {q(y0)} "
        f"{q(x0 + r)} {q(y0)}")


def _premium_panel(p, s, play_res, disp, treats, lines, px,
                   design_version=None, animation=None):
    """One translucent vector rectangle behind the complete caption block.

    ASS BorderStyle 3 looks acceptable only when an event has no inline word
    styling. Premium events reset font/size/colour on every word, so libass
    otherwise paints one overlapping box PER WORD. A dedicated drawing layer
    produces the stable documentary/news panel users actually expect.
    """
    bg, opacity, base_pad = _background_of(p, s)
    if opacity <= 0.001 or not lines:
        return None
    W, H = play_res
    f = max(W / BASE_PLAY_RES[0], H / BASE_PLAY_RES[1])
    pad_x = max(8.0, base_pad * f * 1.25)
    pad_y = max(6.0, base_pad * f * 0.72)
    char_w = p["char_w"] * px
    mults = [_treat_props(treats[i], p, s).get("mult", 1.0)
             for i in range(len(disp))]
    line_widths, line_heights = [], []
    for line in lines:
        width = sum(max(1, len(disp[i])) * char_w * mults[i] for i in line)
        width += max(0, len(line) - 1) * char_w * 0.4
        line_widths.append(width)
        line_heights.append(px * 1.28 * max((mults[i] for i in line),
                                            default=1.0))
    panel_w = min(W * 0.90, max(W * 0.42, max(line_widths) + 2 * pad_x))
    panel_h = min(H * 0.34, sum(line_heights) + 2 * pad_y)
    pos_name = s["position"] if s.get("_pos_set") else p["position"]
    anchor_y = _premium_anchor(p, pos_name) * H
    edge = 0.03 * H
    y0 = max(edge, min(anchor_y - panel_h / 2, H - panel_h - edge))
    y1 = y0 + panel_h
    align = _align_of(p, s)
    margin = PREMIUM_MARGIN_X[align] * W
    if align == "left":
        x0, x1 = margin - pad_x * 0.35, margin - pad_x * 0.35 + panel_w
    elif align == "right":
        x1, x0 = W - margin + pad_x * 0.35, W - margin + pad_x * 0.35 - panel_w
    else:
        x0, x1 = (W - panel_w) / 2, (W + panel_w) / 2
    x0, x1 = max(0, x0), min(W, x1)
    alpha = round((1.0 - opacity) * 255)
    if design_version == CAPTION_DESIGN_VERSION:
        path = _rounded_rect_path(x0, y0, x1, y1,
                                  max(8.0, min(panel_h * 0.16, 18.0 * f)))
    else:
        # Frozen v1 geometry for historical EDL reproducibility.
        path = (f"m {round(x0)} {round(y0)} l {round(x1)} {round(y0)} "
                f"l {round(x1)} {round(y1)} l {round(x0)} {round(y1)}")
    panel_anim = ""
    if animation in ("fade", "slide_up"):
        # Same fade clock as _premium_anim_prefix. The panel and glyph block
        # therefore enter/leave as one object instead of the rectangle
        # appearing before its caption or lingering after it.
        panel_anim = r"\fad(180,140)"
    elif animation == "pop":
        panel_anim = (
            rf"\org({round((x0 + x1) / 2)},{round((y0 + y1) / 2)})"
            r"\fscx70\fscy70\t(0,120,\fscx106\fscy106)"
            r"\t(120,200,\fscx100\fscy100)")
    elif animation in WORD_ANIMS:
        tags = _word_anim_tags(animation, px)
        if tags:
            panel_anim = (rf"\org({round((x0 + x1) / 2)},"
                          rf"{round((y0 + y1) / 2)})" + tags)
    return (rf"{{\an7\pos(0,0)\p1\1c{_inline_hl(bg)}"
            + panel_anim
            + rf"\1a&H{alpha:02X}&\bord0\shad0}}" + path)


# \clip takes absolute frame coords. The composer only ever bands horizontally
# across a whole line, so the x extent just has to exceed any frame we render
# (8K is 7680) — the meaningful bounds are the y ones.
_CLIP_W = 16384


def _font_of(p, s):
    """The family to set: an explicit style.font wins over the preset's."""
    return (s or {}).get("font") or p["font"]


def _line_top(geom, height):
    """Top edge of a line box from its \\pos/\\move prefix, for \\clip bands."""
    m = re.search(r"\\(?:pos|move)\((-?\d+),(-?\d+)", geom)
    cy = int(m.group(2)) if m else 0
    return cy - height / 2.0


def _shift(text, dx, dy):
    """Offset a rendered line's anchor — used to separate the RGB copies of
    the chroma effect without re-deriving the geometry."""
    def bump(m):
        return (f"\\{m.group(1)}({int(round(int(m.group(2)) + dx))},"
                f"{int(round(int(m.group(3)) + dy))}")
    return re.sub(r"\\(pos|move)\((-?\d+),(-?\d+)", bump, text, count=1)


def _leading(s, p):
    v = s.get("leading")
    if v is None:
        return _pget(p, "leading")
    try:
        # Below ~0.5 lines collapse onto each other illegibly; above ~2.2 the
        # block stops reading as one phrase.
        return min(max(float(v), 0.5), 2.2)
    except (TypeError, ValueError):
        return _pget(p, "leading")


def _line_mults(lines, mults):
    """Largest size multiplier on each line — what its height must clear."""
    return [max([mults[i] for i in ln] or [1.0]) for ln in lines]


def _stack_positions(p, s, play_res, lines, mults, px, anim):
    """One \\pos (or \\move) prefix per LINE.

    This is what the single-Dialogue "flow" emission cannot do. With every
    line independently placed, leading becomes a free parameter — including
    values below 1.0, where consecutive lines deliberately OVERLAP, which is
    how the reference frames interlock a small connector word into the
    negative space of the huge word above it.
    """
    W, H = play_res
    lead = _leading(s, p)
    lmults = _line_mults(lines, mults)
    line_hs = [lead * px * m for m in lmults]
    block_h = sum(line_hs)
    pos_name = s["position"] if s.get("_pos_set") else p["position"]
    anchor = _premium_anchor(p, pos_name, s) * H
    edge = 0.03 * H
    y0 = max(edge, min(anchor - block_h / 2, H - block_h - edge))
    stag = (_pget(p, "stagger") or 0.0) * W
    big = max(lmults) if lmults else 1.0
    align = _align_of(p, s)
    base_x = (PREMIUM_MARGIN_X["left"] * W if align == "left"
              else W - PREMIUM_MARGIN_X["right"] * W if align == "right"
              else W / 2)
    an = 4 if align == "left" else 6 if align == "right" else 5
    out, acc = [], 0.0
    for i in range(len(lines)):
        y = y0 + acc + line_hs[i] / 2
        acc += line_hs[i]
        # Only lines SMALLER than the block's hero line are pushed off-axis;
        # the hero stays centred. That is the reference composition — "Your"
        # and "don't" set against a centred "VIDEOS" — and it keeps the eye
        # on the big word instead of scattering the whole block.
        dx = stag * (-1 if i % 2 == 0 else 1) \
            if (stag and lmults[i] < big - 0.01) else 0.0
        x = round(base_x + dx)
        if anim in LINE_ANIMS:
            off = max(10, int(0.045 * H)) * (1 if anim == "rise" else -1)
            out.append(rf"{{\an{an}\move({x},{round(y + off)},{x},{round(y)}"
                       rf",0,180)\fad(120,0)}}")
        else:
            out.append(rf"{{\an{an}\pos({x},{round(y)})}}")
    return out


def _stack_mults(disp, treats, p, s, px, usable,
                 preserve_hierarchy=False):
    """Per-word size multipliers, clamped so no single word can overflow.

    A word wide enough to exceed the usable width makes libass WRAP the line
    — and a wrapped row is positioned by libass, not by us, so the leading,
    stagger and \\pos geometry the composer just computed silently stop
    applying to it. Shrinking the offending word instead keeps the composer
    authoritative over its own layout.
    """
    out, requested = [], []
    for i, t in enumerate(disp):
        m = _treat_props(treats[i], p, s).get("mult", 1.0)
        requested.append(m)
        w = max(1, len(t)) * p["char_w"] * px
        if w * m > usable:
            m = max(1.0, usable / w) if w <= usable else usable / w
        out.append(m)
    if preserve_hierarchy:
        # A long hero word cannot physically grow past the frame width.  V1
        # shrank only that word, perversely making the intended hero SMALLER
        # than its connector words.  V2 preserves the relative hierarchy by
        # stepping the untreated support words down whenever a treated hero
        # is width-capped near/below the base size.  The card still fits, but
        # the eye lands where the editor intended.
        capped_heroes = [i for i, (want, got) in enumerate(zip(requested, out))
                         if treats[i] is not None and want > 1.05
                         and got < min(want * 0.82, 1.30)]
        if capped_heroes:
            hero = max(out[i] for i in capped_heroes)
            support_cap = max(0.34, hero / 1.34)
            for i in range(len(out)):
                if treats[i] is None:
                    out[i] = min(out[i], support_cap)
    return out


def _fit_single_line_mults(disp, mults, p, px, usable):
    """Scale one composed row to its measured horizontal budget.

    ``single_line`` deliberately overrides a preset's authored stack. The
    normal stack fitter constrains individual words, but several individually
    valid words can still overflow as one row once emphasis sizes and spaces
    are combined. Scale the complete row uniformly so libass has no reason to
    add an implicit wrap behind the composer's back. No word is removed.
    """
    if not disp:
        return mults
    space = p["char_w"] * px * 0.4
    spaces = max(0, len(disp) - 1) * space
    widths = [max(1, len(t)) * p["char_w"] * px * mults[i]
              for i, t in enumerate(disp)]
    room = max(1.0, usable - spaces)
    total = sum(widths)
    if total <= room + 1e-6:
        return mults
    scale = room / max(total, 1e-6)
    return [m * scale for m in mults]


def _stack_layout(disp, mults, p, px, usable):
    """Break words into lines by REAL rendered width (per-word scale
    included), not by character count at the base size."""
    space = p["char_w"] * px * 0.4
    lines, cur, w = [], [], 0.0
    for i, t in enumerate(disp):
        ww = max(1, len(t)) * p["char_w"] * px * mults[i]
        add = ww + (space if cur else 0.0)
        if cur and (len(cur) >= p["wpl"] or w + add > usable):
            lines.append(cur)
            cur, w, add = [], 0.0, ww
        cur.append(i)
        w += add
    if cur:
        lines.append(cur)
    return lines


def _stack_layout_v2(disp, mults, p, px, usable):
    """Optically balanced line breaks for newly-authored stack captions.

    Greedy wrapping makes the last line inherit whatever words were left,
    which is how otherwise polished cards end up as a broad first line over a
    lonely connector.  Caption groups are deliberately small, so enumerate
    every legal 1-3 line partition and score the complete block: width balance,
    grammatical edges, widows, and intentional hero-word isolation.
    """
    n = len(disp)
    if n <= 1:
        return [list(range(n))] if n else []
    space = p["char_w"] * px * 0.4
    widths = [max(1, len(t)) * p["char_w"] * px * mults[i]
              for i, t in enumerate(disp)]

    def line_width(a, b):
        return sum(widths[a:b]) + max(0, b - a - 1) * space

    candidates = []

    def visit(start, lines):
        if start == n:
            candidates.append(lines)
            return
        if len(lines) >= PREMIUM_MAX_LINES:
            return
        max_end = min(n, start + int(p.get("wpl") or n))
        for end in range(start + 1, max_end + 1):
            if line_width(start, end) > usable + 1e-6:
                break
            visit(end, lines + [list(range(start, end))])

    visit(0, [])
    if not candidates:
        return _stack_layout(disp, mults, p, px, usable)

    align = p.get("align", "center")

    def score(lines):
        ws = [line_width(line[0], line[-1] + 1) for line in lines]
        mean = sum(ws) / len(ws)
        # Centred blocks expose ragged widths more than left-aligned ones.
        balance = sum(((w - mean) / max(usable, 1.0)) ** 2 for w in ws)
        cost = balance * (3.2 if align == "center" else 1.5)
        cost += 0.11 * (len(lines) - 1)
        for li, line in enumerate(lines):
            first, last = line[0], line[-1]
            one = len(line) == 1
            hero = mults[first] >= 1.38
            if one:
                # A large hero word on its own line is editorial intent; an
                # ordinary one-word widow is a layout accident.
                cost += -0.45 if hero else 1.65
                if li == len(lines) - 1 and not hero:
                    cost += 0.75
            if _norm_word(disp[last]) in _WEAK_BOUNDARY_WORDS:
                cost += 1.45
            if li > 0 and _norm_word(disp[first]) in _WEAK_BOUNDARY_WORDS:
                cost += 0.55
            fill = ws[li] / max(usable, 1.0)
            if fill > 0.96:
                cost += 0.35
        # Stable deterministic tie-break: prefer fewer lines, then later
        # breaks (a slightly fuller first line).
        breaks = tuple(line[-1] + 1 for line in lines)
        return cost, len(lines), tuple(-b for b in breaks)

    return min(candidates, key=score)


def _effect_of(name, p, s, global_effect):
    return _treat_props(name, p, s).get("effect") or global_effect


def _stack_state_events(disp, treats, mults, lines, geoms, p, s, px, accent, base,
                        last_i, active_i, active_tags, global_effect,
                        word_anim):
    """One visual state (a moment in time) -> [(layer, text)].

    Layered effects work by drawing the SAME line again underneath with every
    word that isn't the target made fully transparent. Because the copy is
    laid out identically, the visible word lands in exactly the right place —
    so a fringe or a metal ramp can be applied to ONE word without disturbing
    the line's spacing, which offsetting a standalone run could never do.
    """
    out = []
    for li, ln in enumerate(lines):
        idxs = [i for i in ln if i <= last_i]
        if not idxs:
            continue
        geom = geoms[li]

        def render(sel, extra="", drop_active=False):
            """The whole line, with words outside `sel` made invisible."""
            segs = []
            for i in idxs:
                tags = base + _treat_tags(treats[i], p, px, accent, s,
                                          mult=mults[i])
                if (active_i == "all" or i == active_i) and not drop_active:
                    tags += active_tags
                if i in sel:
                    # Reset alpha EXPLICITLY. ASS override tags persist across
                    # segments and _base_tags does not restate \alpha, so the
                    # mask below leaked forward and made every word after the
                    # first masked one invisible — the chrome word vanished
                    # entirely. `extra` is appended after, so an effect's own
                    # alpha still wins.
                    tags += r"\alpha&H00&" + extra
                else:
                    tags += r"\alpha&HFF&"
                segs.append("{" + tags + "}" + _esc(disp[i]))
            return geom + " ".join(segs)

        groups = {}
        for i in idxs:
            e = _effect_of(treats[i], p, s, global_effect)
            if e:
                groups.setdefault(e, set()).add(i)

        chrome_words = groups.get("chrome", set())
        for name, sel in sorted(groups.items()):
            spec = EFFECTS.get(name)
            if not spec:
                continue
            if name == "chrome":
                # Bands REPLACE the fill, so the main pass hides these words.
                # They also carry \shad0 (a shadow per band would print eleven
                # offset copies), which would leave chrome text with no
                # separation at all on bright footage — so one dark backing
                # copy is drawn underneath purely for its shadow and outline.
                out.append((3, render(sel, r"\1c&H1A1A1A&")))
                bands = spec["bands"]
                h = px * max([mults[i] for i in sel] or [1.0]) * 1.25
                top = _line_top(geoms[li], h)
                step = h / len(bands)
                for bi, col in enumerate(bands):
                    y0 = round(top + bi * step)
                    y1 = round(top + (bi + 1) * step) + 1
                    out.append((4, render(
                        sel, rf"\1c{col}\3a&HFF&\shad0"
                             rf"\clip(0,{y0},{_CLIP_W},{y1})",
                        drop_active=False)))
                continue
            for dx, dy, tags in spec["under"]:
                t = tags.replace("{blur}", str(max(2, round(px * 0.09))))
                out.append((1, _shift(render(sel, t, drop_active=True),
                                      dx * px, dy * px)))
        main_sel = set(idxs) - chrome_words
        if main_sel:
            out.append((5, render(main_sel)))
    return out


def events_premium(out_words, style=None, max_words=None,
                   play_res=BASE_PLAY_RES, emphasis_words=None,
                   design_version=None):
    """from_transcript events for a premium preset. Timing comes ONLY from
    the real word timestamps; layout and treatments are deterministic, so
    the same EDL always renders the same frame."""
    s = _norm_style(style)
    p = _preset_of(s)
    fx = play_res[0] / BASE_PLAY_RES[0]
    fy = play_res[1] / BASE_PLAY_RES[1]
    f = max(fx, fy)
    px = _premium_font_px(p, s, play_res)
    accent = _inline_hl(_highlight_of(p, s))
    upper = s["uppercase"] if s["uppercase"] is not None else p["uppercase"]
    emph = {n for n in (_norm_word(w) for w in (emphasis_words or [])) if n}
    # Clamped to the schema-wide max (16): the schema/tools advertise 1-16,
    # so a lower silent clamp here would misgroup an honest 13-16 request.
    # Safe for stored versions — pre-round-35 validation rejected >12, so no
    # stored EDL can carry one (verified against prod Jul 23 2026).
    max_w = min(int(max_words), MAX_WORDS_PER_CAPTION) if max_words \
        else p["max_words"]
    line_chars = _premium_line_chars(p, s, play_res)
    single_line = bool(s.get("single_line"))
    modern = design_version == CAPTION_DESIGN_VERSION
    chunks = (_premium_chunks_v2(out_words, max_w,
                                  line_chars * (1 if single_line else
                                                PREMIUM_MAX_LINES), p)
              if modern else
              _premium_chunks(out_words, max_w,
                              line_chars * (1 if single_line else
                                            PREMIUM_MAX_LINES)))
    base = _base_tags(p, s, px, f)
    mode = p["mode"]
    anim = _premium_anim_prefix(s.get("animation") or p.get("animation"), px) \
        if mode == "static" else ""

    # The composed looks place every LINE independently; the original four
    # presets keep the single-Dialogue emission they always had, so their
    # output is unchanged to the byte.
    layout = s.get("layout") or _pget(p, "layout")
    # The explicit production contract owns layout. Routing it through the
    # measured stack compositor gives one independently positioned row for
    # every preset, including presets whose authored default is multi-line
    # flow or a composed stack.
    stack = single_line or layout == "stack"
    global_effect = s.get("effect") or _pget(p, "effect")
    word_anim = s.get("animation") or _pget(p, "word_anim")
    motionless = s.get("animation") == "none"

    # Build the timeline of VISUAL STATES first, emit pixels second. The
    # no-overlap rule has to hold over states, not over Dialogue lines: in
    # stack mode one state legitimately emits several same-time Dialogues
    # (one per line, plus effect layers), so de-duplicating raw events would
    # delete parts of a caption instead of resolving an overlap.
    segs, ctx, rot = [], [], 0
    for ci, chunk in enumerate(chunks):
        disp = [(_display_word_v2(w["w"], upper,
                                  p.get("punctuation", "expressive"))
                 if modern else _display_word(w["w"], upper))
                for w in chunk]
        treats, rot = _assign_treatments(chunk, emph, p, s, rot)
        if mode == "karaoke":
            # only the SPOKEN word carries the accent in karaoke modes;
            # persistent keyword coloring would bury the highlight.
            treats = ["num_plain" if t in ("num", "accent") and
                      _word_has_digit(c["w"]) else None
                      for t, c in zip(treats, chunk)]
        if stack:
            # Lay out by REAL rendered width so libass never re-wraps a line
            # behind the composer's back (see _stack_mults / _stack_layout).
            usable = play_res[0] - 2 * PREMIUM_MARGIN_X[_align_of(p, s)] \
                * play_res[0]
            mults = _stack_mults(disp, treats, p, s, px, usable,
                                  preserve_hierarchy=modern)
            if single_line:
                mults = _fit_single_line_mults(
                    disp, mults, p, px, usable)
                lines = [list(range(len(disp)))] if disp else []
            else:
                lines = (_stack_layout_v2(disp, mults, p, px, usable)
                         if modern else
                         _stack_layout(disp, mults, p, px, usable))
            geom = _stack_positions(p, s, play_res, lines, mults, px,
                                    word_anim)
        else:
            mults = None
            lines = _premium_layout(disp, p["wpl"], line_chars)
            geom = _geom_prefix(p, s, play_res, lines, treats, px)
        panel = _premium_panel(
            p, s, play_res, disp, treats, lines, px,
            design_version=design_version,
            animation=s.get("animation") or p.get("animation"))
        ctx.append({"disp": disp, "treats": treats, "lines": lines,
                    "geom": geom, "mults": mults, "panel": panel})
        nxt_t0 = chunks[ci + 1][0]["t0"] if ci + 1 < len(chunks) else None

        def hold_end(w):
            if modern:
                # Keep continuous delivery visually continuous, but clear the
                # card promptly on a breath.  V1 could hold a completed phrase
                # through 1.2 s of silence, making the captions feel late even
                # though every word timestamp was technically correct.
                tail = 0.42 if mode == "static" else 0.30
                if nxt_t0 is not None:
                    gap = nxt_t0 - w["t1"]
                    return nxt_t0 if gap <= 0.28 else min(w["t1"] + tail,
                                                         nxt_t0)
                return w["t1"] + tail
            if nxt_t0 is not None:
                return nxt_t0 if nxt_t0 - w["t1"] <= 1.2 \
                    else min(w["t1"] + 0.9, nxt_t0)
            return w["t1"] + 0.9

        if mode == "static":
            start = chunk[0]["t0"]
            segs.append({"ci": ci, "start": start,
                         "end": max(hold_end(chunk[-1]), start + MIN_EVENT_S),
                         "last_i": len(chunk) - 1,
                         # stack+static has no single "spoken" word, so the
                         # entrance plays on the whole block at once.
                         "active_i": "all" if stack else None,
                         "active": _word_anim_tags(word_anim, px)
                         if stack else ""})
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
                act = ("" if motionless else
                       _word_anim_tags(word_anim, px)
                       if word_anim in WORD_ANIMS else _POP_IN)
                segs.append({"ci": ci, "start": start, "end": end,
                             "last_i": i, "active_i": i, "active": act})
            else:  # karaoke: whole chunk visible, spoken word lights up
                active_motion = (_word_anim_tags(word_anim, px)
                                 if word_anim in WORD_ANIMS else _POP_ACTIVE)
                if p["active"] == "box" and treats[i] != "box":
                    bx = max(2, round(0.22 * px))
                    by = max(2, round(0.13 * px))
                    act = (rf"\1c{DARK_TEXT}\3c{accent}\xbord{bx}"
                           rf"\ybord{by}\shad0")
                elif treats[i] == "box":
                    act = "" if motionless else active_motion
                else:
                    act = rf"\1c{accent}" + ("" if motionless else
                                              active_motion)
                segs.append({"ci": ci, "start": start, "end": end,
                             "last_i": len(chunk) - 1, "active_i": i,
                             "active": act})

    # Never overlap the next STATE. Same rationale as events_dynamic — a
    # same-layer overlap makes libass stack two copies of the phrase — but
    # applied to visual states, because one state can emit several Dialogue
    # lines at the same instant and de-duplicating those would erase parts of
    # a caption rather than resolve an overlap.
    kept = []
    for i, sg in enumerate(segs):
        nxt = segs[i + 1] if i + 1 < len(segs) else None
        if nxt and nxt["start"] <= sg["start"] + 0.01:
            continue
        if nxt and sg["end"] > nxt["start"]:
            sg["end"] = nxt["start"]
        kept.append(sg)

    events = []
    for sg in kept:
        c = ctx[sg["ci"]]
        if c.get("panel"):
            events.append({"start": sg["start"], "end": sg["end"],
                           "text": c["panel"], "layer": 0,
                           "premium": True})
        if stack:
            for layer, text in _stack_state_events(
                    c["disp"], c["treats"], c["mults"], c["lines"], c["geom"],
                    p, s, px, accent, base, sg["last_i"], sg["active_i"],
                    sg["active"], global_effect, word_anim):
                events.append({"start": sg["start"], "end": sg["end"],
                               "text": text, "layer": layer, "premium": True})
            continue
        out_lines = []
        for ln in c["lines"]:
            parts = []
            for i in ln:
                if i > sg["last_i"]:
                    continue
                tags = base + _treat_tags(c["treats"][i], p, px, accent, s)
                if i == sg["active_i"]:
                    tags += sg["active"]
                parts.append("{" + tags + "}" + _esc(c["disp"][i]))
            if parts:
                out_lines.append(" ".join(parts))
        events.append({"start": sg["start"], "end": sg["end"],
                       "text": c["geom"] + anim + r"\N".join(out_lines),
                       "layer": 5, "premium": True})
    return events


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
        # Keep authoring provenance on the exact compiled event. A manual
        # track may mix animated and static cards, so only the card whose
        # effective style actually moves may satisfy a motion-language beat.
        item_motion_mode = motion_mode([it])
        item_motion_motif = get("motion_motif") if item_motion_mode else None
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
                                        or p.get("animation"), px)
            # Manual/translated captions use the same single vector panel as
            # transcript captions. Without this, a documentary style applied
            # successfully to translated items but its contrast panel was
            # silently absent.
            disp = [_display_word(x, upper) for x in text.split()]
            panel_lines = _premium_layout(disp, p["wpl"], item_chars)
            panel = _premium_panel(
                p, ns, play_res, disp, [None] * len(disp), panel_lines, px,
                animation=ns.get("animation") or p.get("animation"))
            if panel:
                events.append({"start": start,
                               "end": end,
                               "text": panel, "item_style": get("style"),
                               "layer": 0, "premium": True,
                               "motion_mode": item_motion_mode,
                               "motion_motif": item_motion_motif})
            events.append({"start": start,
                           "end": end,
                           "text": geom + anim +
                           r"\N".join(_esc(l) for l in lines),
                           "item_style": get("style"), "layer": 5,
                           "premium": True,
                           "motion_mode": item_motion_mode,
                           "motion_motif": item_motion_motif})
            continue
        lines = _wrap(get("text"), item_chars)[:MAX_LINES]
        events.append({"start": start, "end": end,
                       "text": r"\N".join(_esc(l) for l in lines),
                       "item_style": get("style"),
                       "motion_mode": item_motion_mode,
                       "motion_motif": item_motion_motif})
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
        for k in STYLE_KEYS:
            v = d.get(k)
            if v is not None if k in STYLE_KEYS_EXPLICIT else bool(v):
                merged[k] = v
        if d.get("uppercase") is not None:
            merged["uppercase"] = bool(d["uppercase"])
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
        # Placement analysis can choose a precise safe vertical anchor, not
        # just top/middle/bottom. Premium captions already emit explicit
        # geometry. Give classic/static/dynamic captions the same promise.
        # slide_up's \move above owns the target position and already carries
        # the alignment, so adding \pos beside it would be contradictory ASS.
        if eff.get("anchor_y") is not None and not ev.get("premium") \
                and eff.get("animation") not in ("slide_up", "rise", "drop"):
            align = {"left": 4, "center": 5, "right": 6}.get(
                eff.get("text_align") or "center", 5)
            margin_x = max(10, round(60 * play_res[0] / BASE_PLAY_RES[0]))
            x = (margin_x if align == 4 else
                 int(play_res[0]) - margin_x if align == 6 else
                 int(play_res[0] / 2))
            y = int(float(eff["anchor_y"]) * play_res[1])
            ev["text"] = rf"{{\an{align}\pos({x},{y})}}" + ev["text"]

    with open(path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER_TOP.format(resx=int(play_res[0]),
                                      resy=int(play_res[1])))
        for name, st in styles:
            f.write(style_line(name, st, play_res) + "\n")
        f.write(EVENTS_HEADER)
        for ev in events:
            # Layer matters now: the composer draws effect copies UNDER the
            # real text (chroma fringes, chrome bands), and libass composites
            # by ascending layer.
            f.write(f"Dialogue: {int(ev.get('layer', 0))},"
                    f"{_ass_time(ev['start'])},"
                    f"{_ass_time(ev['end'])},{ev.get('style_name', 'Default')}"
                    f",,0,0,0,,{ev['text']}\n")
    return path


# A caption that only grazes the edge of a mute window (a few frames of
# overlap) is not what the user was pointing at when they said "no captions
# during the effect" — dropping it would silently delete words either side of
# the window. Anything visible for longer than this INSIDE the window goes.
MUTE_GRAZE_S = 0.15


def effective_caption_mutes(edl):
    """Explicit mute spans plus suppression owned by live text items.

    `caption_mutes` remains the user's/manual global control. Designed text
    uses ownership instead: its mute follows the item through timeline edits
    and disappears when the item does. The union is merged only for simpler
    word/event comparisons; historical EDLs with no owned mutes produce the
    same spans and therefore the same ASS bytes.
    """
    spans = []
    for raw in (edl.get("caption_mutes") or []):
        try:
            s, e = float(raw[0]), float(raw[1])
        except (IndexError, TypeError, ValueError):
            continue
        if e > s:
            spans.append([s, e])
    for text in (edl.get("texts") or []):
        if not isinstance(text, dict) or not text.get("mute_captions"):
            continue
        try:
            s, e = float(text["start"]), float(text["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if e > s:
            spans.append([s, e])
    merged = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1] + 0.001:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def apply_mutes(events, mutes):
    """Drop caption events that are on screen during a caption_mutes window
    (PROGRAM seconds). Events are DROPPED, never trimmed: premium/karaoke
    events carry inline \\k word timings measured from the event's own start,
    so moving a boundary would desync every word after it. Callers that want
    partial coverage should mute the exact window instead.

    Kept for DICTATED caption items (captions as a list) only. Transcript
    captions mute at the WORD level (_drop_muted_words) before grouping —
    dropping whole built events made a mute overshoot by an entire block:
    project 384's title mute covered 0-5.5s, the caption block straddling
    5.5s vanished with it, and the viewer read the first speech with no
    captions until seconds after the title was gone."""
    if not mutes or not events:
        return events
    kept = []
    for ev in events:
        s, e = float(ev["start"]), float(ev["end"])
        hidden = any(min(e, float(m1)) - max(s, float(m0)) > MUTE_GRAZE_S
                     for m0, m1 in mutes)
        if not hidden:
            kept.append(ev)
    return kept


def _drop_muted_words(words, mutes):
    """Remove the words whose midpoint lies inside a caption_mutes window,
    BEFORE grouping — the same stage that drops filler words, and for the
    same reason: every preset family then inherits the decision and the
    events build themselves around the gap. Captions resume with the first
    unmuted word instead of at the next whole block, and karaoke timings
    stay true because the groups are built from exactly the words shown."""
    if not mutes or not words:
        return words
    spans = [(float(m0), float(m1)) for m0, m1 in mutes]
    return [w for w in words
            if not any(m0 <= (w["t0"] + w["t1"]) / 2.0 <= m1
                       for m0, m1 in spans)]


def _clamp_event_ends_to_mutes(events, mutes):
    """Transcript events after word-level muting can still REACH into a mute
    window with display padding (an event holds on screen until the next one
    starts, and the next one now sits past the window). Pull those ends back
    to the window edge. Only the END moves — every word in such an event
    finished before the window (its inside words were dropped), so the
    inline \\k timings, measured from the untouched start, are unaffected.
    An event that spans the whole window with words on BOTH sides (a
    sub-second mute inside one breath group) is left alone rather than
    desynced."""
    if not mutes or not events:
        return events
    spans = [(float(m0), float(m1)) for m0, m1 in mutes]
    for ev in events:
        s, e = float(ev["start"]), float(ev["end"])
        for m0, m1 in spans:
            if s < m0 < e <= m1 + MUTE_GRAZE_S:
                ev["end"] = m0
                break
    return [ev for ev in events
            if float(ev["end"]) - float(ev["start"]) > 0.04]


def _placement_runs(out_words, track, fallback_position=None,
                    fallback_anchor_y=None):
    """Contiguous word runs sharing one measured caption position.

    A modern track always has a least-obstructed fallback. Historical EDLs
    preserve their original omission behaviour, while design-v2 never loses
    spoken words merely because every band carried some visual content.
    """
    if not track:
        return []
    spans = [(float(x.get("t0", 0)), float(x.get("t1", 0)),
              x.get("position"), x.get("anchor_y"))
             for x in track]
    runs, current, current_key = [], [], None
    for raw in out_words:
        word = dict(raw)
        smid = (float(word.get("src_t0", 0)) +
                float(word.get("src_t1", 0))) / 2.0
        placed = next(((p, ay) for a, b, p, ay in spans if a <= smid <= b),
                      None)
        key = placed or ((fallback_position, fallback_anchor_y)
                         if fallback_position else None)
        if key != current_key or word.get("brk"):
            if current and current_key:
                runs.append((current_key[0], current_key[1], current))
            current, current_key = [], key
        if key:
            if not current:
                word["brk"] = True
            current.append(word)
    if current and current_key:
        runs.append((current_key[0], current_key[1], current))
    return runs


def _positioned_events(out_words, captions, global_style, play_res):
    """Build transcript events per measured placement run."""
    modern = captions.get("design_version") == CAPTION_DESIGN_VERSION
    normalized = _norm_style(global_style)
    p = _preset_of(normalized)
    fallback = (normalized.get("position") if normalized.get("_pos_set") else
                p.get("position") if p else normalized.get("position"))
    runs = _placement_runs(
        out_words, captions.get("placement_track") or [],
        fallback_position=fallback if modern else None,
        fallback_anchor_y=normalized.get("anchor_y") if modern else None)
    events = []
    for pos, anchor_y, words in runs:
        run_style = dict(global_style or {})
        run_style["position"] = pos
        if anchor_y is not None:
            run_style["anchor_y"] = anchor_y
        if _preset_of(_norm_style(run_style)):
            made = events_premium(
                words, style=run_style,
                max_words=captions.get("max_words_per_caption"),
                play_res=play_res,
                emphasis_words=captions.get("emphasis_words"),
                design_version=captions.get("design_version"))
        elif _norm_style(run_style)["dynamic"]:
            made = events_dynamic(
                words, style=run_style,
                max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(run_style, play_res),
                karaoke_group_n=captions.get("karaoke_group_n"))
            for ev in made:
                ev["item_style"] = {"position": pos}
                if anchor_y is not None:
                    ev["item_style"]["anchor_y"] = anchor_y
        else:
            made = events_from_transcript(
                words, max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(run_style, play_res),
                single_line=bool(_norm_style(run_style).get("single_line")))
            for ev in made:
                ev["item_style"] = {"position": pos}
                if anchor_y is not None:
                    ev["item_style"]["anchor_y"] = anchor_y
        events.extend(made)
    events.sort(key=lambda x: (float(x.get("start", 0)),
                               int(x.get("layer", 0))))
    # Separate placement runs must obey the same no-overlap contract as each
    # individual caption family.
    starts = sorted({float(ev["start"]) for ev in events})
    for ev in events:
        nxt = next((s for s in starts if s > float(ev["start"]) + 0.001), None)
        if nxt is not None and float(ev["end"]) > nxt:
            ev["end"] = nxt
    return [ev for ev in events if float(ev["end"]) > float(ev["start"]) + 0.01]


def compiled_events(edl, index, tl, play_res=BASE_PLAY_RES):
    """EDL captions field -> exact timed events before ASS serialization.

    Captions come from the MAIN footage's transcript only — inserted clips
    are not transcribed (v1), so no events land inside spliced insert time
    (kept_words maps around inserts via the Timeline). Exposing this pure
    stage lets screening and execution audits reason about the same caption
    windows the renderer will burn instead of approximating word groups.
    Returns ``(events, global_style)``; no captions is ``([], None)``.
    """
    captions = edl.get("captions")
    if not captions:
        return [], None
    mutes = effective_caption_mutes(edl)
    if isinstance(captions, dict) and captions.get("mode") == "from_transcript":
        # Hesitation sounds are in the INDEX (round 69) so remove_filler_words
        # has real spans to cut — they were absent before, which is why that
        # tool had never removed anything. They are not BURNED, though: every
        # professional subtitle track omits them, and "So, um, uh, yeah" across
        # the bottom of a reel is the amateur look the whole caption system
        # exists to avoid. The audio is untouched either way; removing the
        # hesitations from the video is a separate, explicit edit.
        src_words = [w for w in (index.get("words") or [])
                     if not (w.get("filler") if isinstance(w, dict)
                             else getattr(w, "filler", False))]
        out_words = _mark_insert_breaks(tl.kept_words(src_words), tl)
        # Text corrections (round 52) are applied to the DISPLAYED words only,
        # before any grouping, so every preset family inherits them and the
        # timings they were grouped by never move.
        out_words = apply_text_fixes(out_words, captions.get("text_fixes"))
        # Mutes at the WORD level, same stage (round 96c): grouping then
        # builds events around the gap, so captions resume at the window's
        # edge instead of one whole block late.
        out_words = _drop_muted_words(out_words, mutes)
        global_style = captions.get("style")
        if captions.get("placement_track"):
            events = _positioned_events(
                out_words, captions, global_style, play_res)
        elif _preset_of(_norm_style(global_style)):
            events = events_premium(
                out_words, style=global_style,
                max_words=captions.get("max_words_per_caption"),
                play_res=play_res,
                emphasis_words=captions.get("emphasis_words"),
                design_version=captions.get("design_version"))
        elif _norm_style(global_style)["dynamic"]:
            events = events_dynamic(
                out_words, style=global_style,
                max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res),
                karaoke_group_n=captions.get("karaoke_group_n"))
        else:
            events = events_from_transcript(
                out_words, max_words=captions.get("max_words_per_caption"),
                line_chars=line_chars_for(global_style, play_res),
                single_line=bool(
                    _norm_style(global_style).get("single_line")))
        # Make the opening frame carry a caption so a paused player isn't blank
        # (see FIRST_CAPTION_LEAD_IN_S). from_transcript only — dictated caption
        # items keep their authored timing. NOT when an inserted clip opens the
        # program: inserts aren't transcribed, their opening frames aren't blank,
        # and a main-footage word doesn't belong burned over someone's title card.
        opens_on_insert = any(fs <= 0.01 and fs + d > 0.01
                              for fs, d in tl.insert_positions())
        # ...and never pull it into an opening mute window: with the title
        # muted over 0-5.5s, dragging the first caption to 0.0 would burn it
        # straight across the title it was muted to clear.
        mute0 = any(float(m0) <= 0.05 for m0, _m1 in
                    mutes)
        if events and not opens_on_insert and not mute0 \
                and 0.0 < events[0]["start"] <= FIRST_CAPTION_LEAD_IN_S:
            events[0]["start"] = 0.0
    elif isinstance(captions, list):
        events = events_from_items(captions, tl, play_res)
        global_style = None
    else:
        return [], None
    events = _clamp_events_to_inserts(events, tl)
    if isinstance(captions, dict) and \
            captions.get("mode") == "from_transcript":
        # Words inside mute windows are already gone (pre-grouping); only
        # display padding can still reach into a window — pull it back.
        events = _clamp_event_ends_to_mutes(events, mutes)
    else:
        events = apply_mutes(events, mutes)
    if not events:
        return [], global_style
    return events, global_style


def motion_mode(captions):
    """``continuous``/``entrance`` when captions author timed type motion.

    Plain static subtitles appearing at their authored boundaries do not make
    the motion department true. Named animation, karaoke/dynamic highlighting,
    reveal composition and preset word animation do. This is metadata only;
    ``compiled_events`` remains authoritative about where those states render.
    """
    if isinstance(captions, list):
        entrance = False
        for item in captions:
            style = item.get("style") if isinstance(item, dict) else \
                getattr(item, "style", None)
            if style is not None and not isinstance(style, dict):
                style = style.model_dump(exclude_none=True)
            normalized = _norm_style(style)
            if normalized.get("dynamic"):
                return "continuous"
            entrance = entrance or \
                normalized.get("animation") not in (None, "none")
        return "entrance" if entrance else None
    if not isinstance(captions, dict):
        return None
    style = _norm_style(captions.get("style"))
    if style.get("dynamic"):
        return "continuous"
    preset = _preset_of(style) or {}
    if not preset:
        return ("entrance" if
                style.get("animation") not in (None, "none") else None)
    # An explicit animation='none' cancels transforms but a reveal/karaoke
    # preset still authors word-state changes synchronized to speech.
    if preset.get("mode") in {"reveal", "karaoke"}:
        return "continuous"
    if style.get("animation") not in (None, "none") or \
            (style.get("animation") != "none" and bool(
                preset.get("animation") or preset.get("word_anim"))):
        return "entrance"
    return None


def motion_enabled(captions):
    """Whether a caption track deliberately changes type state over time."""
    return motion_mode(captions) is not None


def build_ass(edl, index, tl, path, play_res=BASE_PLAY_RES):
    """EDL captions field -> .ass file (or None when captions are off)."""
    events, global_style = compiled_events(edl, index, tl, play_res)
    if not events:
        return None
    return write_ass(events, path, global_style, play_res)
