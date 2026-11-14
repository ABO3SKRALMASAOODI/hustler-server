"""The Score compiler. A model's decisions in, exact frames out.

    compile_score(text, work_dir=...) -> Compiled(.l1, .meter, .meter_text)
    validate_l1(l1)            the contract, checked

The Score is JSONL (Director DESIGN 2). Everything the engine would have
to think about (curves, seats, line breaks, polarity, sound offsets) is solved and baked here.

Three clocks stay separate. Captions run on the voice. Cuts run on their own pulse, drawn
from the style's measured shot distribution with the film's seed. Display words land 0-6
frames after the voice. Nothing here snaps a cut to a word.

The parsers read machine syntax a model wrote. Nothing reads what a word means, and nothing
judges an edit: the meter counts and the ceiling line is the rubric's own arithmetic.

No model, no network, no ToolContext. Same inputs give byte-identical l1.json.
"""
import bisect
import hashlib
import json
import math
import os
import random
import re
import statistics
import subprocess
import sys
import time
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
FONTS = os.environ.get("VALMERA_FONTS", WORKER + "/fonts")
SOUNDS = os.environ.get("VALMERA_SOUNDS", WORKER + "/sound_library")
# The worker's set has no Arabic script (a Persian line drew nothing). The images carry Debian's
# fonts-noto-core (SIL OFL): Noto Sans, Naskh and Kufi Arabic. Nothing font-like ships from the slice.
EXTRA_FONTS = os.environ.get("VALMERA_SCRIPT_FONTS", "/usr/share/fonts/truetype/noto")
KEEPOUT = (0.5594, 0.115, 0.9146, 0.1923)      # watermark, DESIGN 2.6
FRAMES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "4:5": (1080, 1350), "16:9": (1920, 1080), "4:3": (1440, 1080)}
PAD_IN, PAD_OUT = 0.05, 0.07                   # seconds of air around a jump in the voice
SAFE_X = 0.05                                  # type keeps this far from the side edges
IDENTITY = {"lift": 0.0, "gamma": 1.0, "gain": 1.0, "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0}
MONO_SAT, MONO_SHARE = 0.15, 0.55              # B&W stays a device (mono_policy: 0.15 over 0.6)

# Vertical-row and band numbers, medians of the references named in ATLAS 3 (numbers.json).
BANDS = {
    "P1": {"med": 5.1, "p10": 1.39, "p90": 16.67, "cuts10": 0.95, "wos": 3.86, "cap_h": 0.023, "type10": 24.0, "sfx10": 1.9},
    "P2": {"med": 0.74, "p10": 0.24, "p90": 1.54, "cuts10": 9.79, "wos": 3.49, "cap_h": 0.039, "type10": 18.7, "sfx10": 13.42},
    "P3": {"med": 2.48, "p10": 0.78, "p90": 12.95, "cuts10": 2.11, "wos": 3.5, "cap_h": 0.097, "type10": 4.8, "sfx10": 0.75},
    "P4": {"med": 10.44, "p10": 1.47, "p90": 38.5, "cuts10": 0.63, "wos": 4.1, "cap_h": 0.079, "type10": 15.35, "sfx10": 1.15},
    "P5": {"med": 3.1, "p10": 0.78, "p90": 13.0, "cuts10": 1.81, "wos": 6.62, "cap_h": 0.05, "type10": 6.7, "sfx10": 0.67},
    "P6": {"med": 1.46, "p10": 0.39, "p90": 3.99, "cuts10": 5.22, "wos": 2.79, "cap_h": 0.049, "type10": 27.9, "sfx10": 12.7},
    "M1": {"med": 1.41, "p10": 0.38, "p90": 2.84, "cuts10": 6.48, "wos": 3.05, "cap_h": 0.027, "type10": 29.6, "sfx10": 17.3},
    "M2": {"med": 0.7, "p10": 0.12, "p90": 2.5, "cuts10": 10.27, "wos": 3.24, "cap_h": 0.065, "type10": 26.7, "sfx10": 13.6},
    "M3": {"med": 0.46, "p10": 0.083, "p90": 1.29, "cuts10": 16.7, "wos": 1.29, "cap_h": 0.119, "type10": 24.6, "sfx10": 9.67},
    "M4": {"med": 0.4, "p10": 0.145, "p90": 1.72, "cuts10": 14.3, "wos": 3.28, "cap_h": 0.069, "type10": 24.5, "sfx10": 17.0},
    "M5": {"med": 0.23, "p10": 0.074, "p90": 1.12, "cuts10": 23.3, "wos": 0.0, "cap_h": 0.113, "type10": 9.4, "sfx10": 13.6},
    "M6": {"med": 0.88, "p10": 0.12, "p90": 3.5, "cuts10": 6.33, "wos": 3.17, "cap_h": 0.066, "type10": 25.1, "sfx10": 10.5},
    "M7": {"med": 0.9, "p10": 0.33, "p90": 2.28, "cuts10": 6.94, "wos": 2.48, "cap_h": 0.058, "type10": 16.1, "sfx10": 8.06},
    "M8": {"med": 0.9, "p10": 0.3, "p90": 3.1, "cuts10": 7.23, "wos": 1.71, "cap_h": 0.052, "type10": 27.5, "sfx10": 13.9},
    "S1": {"med": 0.54, "p10": 0.1, "p90": 1.24, "cuts10": 15.4, "wos": 2.15, "cap_h": 0.058, "type10": 27.2, "sfx10": 12.9},
    "S2": {"med": 0.62, "p10": 0.084, "p90": 1.22, "cuts10": 16.0, "wos": 1.83, "cap_h": 0.03, "type10": 27.3, "sfx10": 19.6},
    "S3": {"med": 0.27, "p10": 0.1, "p90": 0.94, "cuts10": 22.0, "wos": 1.98, "cap_h": 0.076, "type10": 25.8, "sfx10": 18.0},
    "S4": {"med": 0.63, "p10": 0.1, "p90": 1.67, "cuts10": 12.5, "wos": 2.54, "cap_h": 0.034, "type10": 23.6, "sfx10": 4.81},
    "S5": {"med": 1.1, "p10": 0.29, "p90": 2.21, "cuts10": 8.14, "wos": 3.47, "cap_h": 0.03, "type10": 22.3, "sfx10": 7.36},
    "S6": {"med": 0.67, "p10": 0.125, "p90": 1.08, "cuts10": 14.5, "wos": 2.25, "cap_h": 0.05, "type10": 20.7, "sfx10": 16.3},
    "S7": {"med": 0.15, "p10": 0.1, "p90": 1.44, "cuts10": 20.3, "wos": 2.24, "cap_h": 0.094, "type10": 26.9, "sfx10": 11.1},
    "S8": {"med": 0.72, "p10": 0.16, "p90": 1.92, "cuts10": 10.6, "wos": 3.0, "cap_h": 0.069, "type10": 24.8, "sfx10": 14.5},
    "S9": {"med": 1.1, "p10": 0.48, "p90": 2.64, "cuts10": 7.8, "wos": 2.77, "cap_h": 0.037, "type10": 22.6, "sfx10": 11.0},
    "S10": {"med": 1.0, "p10": 0.5, "p90": 2.15, "cuts10": 8.36, "wos": 3.92, "cap_h": 0.023, "type10": 24.7, "sfx10": 7.72},
    "S11": {"med": 0.21, "p10": 0.1, "p90": 0.67, "cuts10": 29.6, "wos": 1.35, "cap_h": 0.136, "type10": 21.0, "sfx10": 8.34},
    "S12": {"med": 0.43, "p10": 0.1, "p90": 2.0, "cuts10": 12.4, "wos": 4.27, "cap_h": 0.088, "type10": 20.5, "sfx10": 13.6},
    "L1": {"med": 4.33, "p10": 0.33, "p90": 10.8, "cuts10": 1.86, "wos": 3.61, "cap_h": 0.085, "type10": 9.8, "sfx10": 10.3},
    "L2": {"med": 2.48, "p10": 0.78, "p90": 12.95, "cuts10": 2.11, "wos": 3.5, "cap_h": 0.097, "type10": 4.8, "sfx10": 0.75},
    "L3": {"med": 2.14, "p10": 0.6, "p90": 6.0, "cuts10": 3.45, "wos": 4.4, "cap_h": 0.047, "type10": 5.0, "sfx10": 1.63},
    "L4": {"med": 2.89, "p10": 1.03, "p90": 4.34, "cuts10": 3.39, "wos": 5.67, "cap_h": 0.053, "type10": 7.4, "sfx10": 0.78},
    "L5": {"med": 3.23, "p10": 0.98, "p90": 6.28, "cuts10": 2.98, "wos": 7.0, "cap_h": 0.047, "type10": 8.0, "sfx10": 1.64},
    "L6": {"med": 9.17, "p10": 1.49, "p90": 27.8, "cuts10": 0.53, "wos": 4.34, "cap_h": 0.081, "type10": 16.0, "sfx10": 1.11},
    "L7": {"med": 3.1, "p10": 0.78, "p90": 13.0, "cuts10": 1.81, "wos": 6.62, "cap_h": 0.05, "type10": 6.7, "sfx10": 0.67},
    "L8": {"med": 2.67, "p10": 1.0, "p90": 7.53, "cuts10": 2.56, "wos": 9.05, "cap_h": 0.106, "type10": 2.7, "sfx10": 1.06},
}
SAFETY_BAND = {"med": 0.9, "p10": 0.25, "p90": 2.5}   # only so a film with no band still draws

# Font classes the Director may name, by what the atlas calls them, plus every file stem.
FONT_CLASS = {
    "grotesk": "InterDisplay-Bold", "grotesk-bold": "InterDisplay-Bold", "grotesk-tight-bold": "InterDisplay-Bold",
    "grotesk-black": "InterDisplay-Black", "grotesk-extrabold": "InterDisplay-ExtraBold",
    "grotesk-italic": "InterDisplay-BoldItalic", "serif-italic": "InstrumentSerif-Italic", "serif": "InstrumentSerif-Regular",
    "didone": "DMSerifDisplay-Regular", "didone-italic": "DMSerifDisplay-Italic", "serif-black": "PlayfairDisplay-Black",
    "serif-black-italic": "PlayfairDisplay-BlackItalic", "condensed": "Anton-Regular", "condensed-caps": "BebasNeue-Regular",
    "wide": "Syne-ExtraBold", "heavy": "ArchivoBlack-Regular", "geometric": "Poppins-Black",
    "geometric-bold": "Montserrat-Bold", "humanist": "PlusJakartaSans-ExtraBold",
    "arabic-black": "NotoSansArabic-Bold", "arabic-bold": "NotoSansArabic-Bold", "arabic-light": "NotoSansArabic-Regular",
    "arabic-condensed": "NotoSansArabic-Bold", "nastaliq": "NotoNaskhArabic-Regular", "nastaliq-bold": "NotoNaskhArabic-Bold",
}
PLATE_NAMES = {"black": "#151515", "paper": "#DFDFDD", "white": "#F4F4F2", "red": "#C2080D", "cream": "#F2EDE1"}
LOOKS = {   # numbers, not adjectives: a Director may name one or write its own
    "warm_low": {"gamma": 0.94, "gain": 1.02, "sat": 0.92, "contrast": 1.1, "temp": 0.03},
    "cold_doc": {"gamma": 0.97, "sat": 0.82, "contrast": 1.12, "temp": -0.03},
    "bleach": {"lift": 0.02, "sat": 0.55, "contrast": 1.22},
    "teal_bleach": {"lift": 0.015, "sat": 0.7, "contrast": 1.18, "temp": -0.04, "tint": 0.01},
    "night": {"gamma": 0.86, "gain": 0.95, "sat": 0.85, "contrast": 1.16, "temp": -0.02},
    "print": {"lift": 0.03, "gamma": 0.96, "sat": 0.88, "contrast": 1.06, "temp": 0.015},
}
SETTLE = {"s_inf": 0.84, "tau": 6.9}                  # ATLAS 8.5
FLICKER = [0.5, 0.9, 0.25, 0.85, 0.3, 1.0]            # ATLAS 8.3, DfFsUfUx0dZ
BURN_TH = [0.24, 0.39, 0.5, 0.7, 0.86, 0.94, 0.98]    # near-white 76, 61, 50, 30, 14, 6, 2 %, ATLAS 8.6
BOUNCE_BLUR = [10, 7, 4, 2, 1, 0]
CELLS_X = (0.31, 0.375, 0.315)                        # unequal 3x3, ATLAS 8.3
CELLS_Y = (0.31, 0.373, 0.317)
RELAY_WALK = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (0, 1), (1, 1)]    # anticlockwise, ends centre

# The taste governor (score/taste.py, week-1112 TASTE.md): a device exists only where an event earns it, and its
# count follows the supply of new pictures. On for every film; a test of how one gesture compiles turns it off.
GOVERN = True
GOVERN_ROUNDS = 3

_font_cache = {}
_sound_cache = {}


def clamp(x, a, b):
    return a if x < a else b if x > b else x


def rf(x):
    """Stable numbers: 4 places, ints stay ints."""
    if isinstance(x, float):
        r = round(x, 4)
        return int(r) if r == int(r) else r
    if isinstance(x, list):
        return [rf(v) for v in x]
    if isinstance(x, dict):
        return {k: rf(v) for k, v in x.items()}
    return x


def rng_for(seed, *parts):
    """One stream per purpose, so changing a beat leaves every other beat's draws alone."""
    h = hashlib.sha256("|".join(str(p) for p in (seed,) + parts).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def hex_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_hex(r, g, b):
    return "#%02X%02X%02X" % tuple(int(round(clamp(v, 0, 1) * 255)) for v in (r, g, b))


def luma(c):
    r, g, b = hex_rgb(c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def hex_sat(c):
    r, g, b = hex_rgb(c)
    return (max(r, g, b) - min(r, g, b)) / max(r, g, b, 1)


def is_hex(c):
    return isinstance(c, str) and re.fullmatch(r"#?[0-9A-Fa-f]{6}", c) is not None


def norm_hex(c):
    return "#" + c.lstrip("#").upper()


def rects_touch(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# ---------------------------------------------------------------- type metrics

def font_file(name):
    """A font class or a file stem to a file in worker/fonts, or None."""
    if not name:
        return None
    stem = FONT_CLASS.get(str(name).lower().strip(), str(name).strip())
    stem = stem[:-4] if stem.endswith(".ttf") else stem
    p = f"{stem}.ttf"
    if font_path(p):
        return p
    low = {f.lower(): f for f in _font_dir()}
    return low.get(p.lower())


def _font_dir():
    if "dir" not in _font_cache:
        out = []
        for d in (FONTS, EXTRA_FONTS):
            try:
                out += [f for f in os.listdir(d) if f.endswith(".ttf")]
            except OSError:
                pass
        _font_cache["dir"] = sorted(out)
    return _font_cache["dir"]


def font_path(font):
    for d in (FONTS, EXTRA_FONTS):
        p = os.path.join(d, font or "")
        if font and os.path.exists(p):
            return p
    return None


def _face(font, px):
    key = (font, int(round(px)))
    if key not in _font_cache:
        try:
            from PIL import ImageFont
            _font_cache[key] = ImageFont.truetype(font_path(font), key[1])
        except Exception:
            _font_cache[key] = None
    return _font_cache[key]


def joined_script(t):
    """True for a script whose letters join: spacing them apart breaks the word, one at a time they don't shape."""
    return any(0x0590 <= ord(c) <= 0x08FF or 0xFB1D <= ord(c) <= 0xFDFF or 0xFE70 <= ord(c) <= 0xFEFF for c in str(t))


def _hb(font):
    key = ("hb", font)
    if key not in _font_cache:
        try:
            import uharfbuzz as hb
            f = hb.Face(hb.Blob.from_file_path(font_path(font)))
            _font_cache[key] = (hb, hb.Font(f), float(f.upem))
        except Exception:
            _font_cache[key] = None
    return _font_cache[key]


def font_covers(font, t):
    """Does this font hold a glyph for every letter of t? Read from the font."""
    h = _hb(font)
    if h is None:
        return True
    return all(h[1].get_nominal_glyph(ord(c)) for c in str(t) if c.isalpha())


def shaped(font, t):
    """HarfBuzz's reading of a string: (advance in em, top of ink in em above the baseline, bottom in em below it)."""
    key = ("shaped", font, t)
    if key in _font_cache:
        return _font_cache[key]
    h = _hb(font)
    if h is None:
        return None
    hb, f, upem = h
    buf = hb.Buffer()
    buf.add_str(str(t))
    buf.guess_segment_properties()
    hb.shape(f, buf, {"kern": True})
    x, top, bot = 0.0, 0.0, 0.0
    for g, q in zip(buf.glyph_infos, buf.glyph_positions):
        e = f.get_glyph_extents(g.codepoint)
        if e:
            top = max(top, e.y_bearing + q.y_offset)
            bot = min(bot, e.y_bearing + e.height + q.y_offset)
        x += q.x_advance
    _font_cache[key] = (x / upem, top / upem, -bot / upem)
    return _font_cache[key]


def script_font(font, t):
    """The Director names a face; a face with no glyph for this script would draw nothing. The nearest weight that has one."""
    if not joined_script(t) or font_covers(font, t):
        return font
    return "NotoSansArabic-Bold.ttf"


def text_px(t, voice):
    """Advance width in pixels at the output frame, the way design-a Face measures it."""
    px, track = voice["px"], voice.get("track", 0) * voice["px"] / 100.0
    if joined_script(t):
        q = shaped(voice["font"], t)
        if q:
            return q[0] * px
    f = _face(voice["font"], px)
    if f is None:
        return len(t) * px * 0.56
    adv = _font_cache.setdefault(("adv", voice["font"], int(round(px))), {})
    w = 0.0
    for c in t:
        a = adv.get(c)
        if a is None:
            a = adv[c] = f.getlength(c)
        w += a + track
    return w - track


def cap_height(font):
    """Height of a capital, in em, read from the font."""
    key = ("cap", font)
    if key not in _font_cache:
        f = _face(font, 200)
        try:
            b = f.getbbox("H", anchor="ls")
            _font_cache[key] = (b[3] - b[1]) / 200.0
        except Exception:
            _font_cache[key] = 0.74
    return _font_cache[key]


def parse_font_spec(spec):
    """'grotesk-bold@.03:-3' to (file, size fraction, track). Machine syntax a model wrote."""
    s = str(spec)
    track, size = None, None
    if ":" in s:
        s, t = s.rsplit(":", 1)
        try:
            track = float(t)
        except ValueError:
            track = None
    if "@" in s:
        s, h = s.split("@", 1)
        try:
            size = float(h)
        except ValueError:
            size = None
    return font_file(s), size, track


# ---------------------------------------------------------------- parse

KNOWN = {"treat", "film", "dna", "bind", "pics", "b", "item", "tool", "def", "fix", "drop", "cut"}


def parse_score(text):
    """Lines to objects. Anything unreadable drops only itself."""
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("utf-8", "ignore")
    good, bad = [], []
    dec = json.JSONDecoder()
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("```") or s.startswith("#"):
            continue
        pos = 0
        while pos < len(s):
            try:
                obj, end = dec.raw_decode(s, pos)
            except ValueError:
                bad.append((n, "not JSON", s[pos:pos + 70]))
                break
            pos = end
            while pos < len(s) and s[pos] in " ,\t":
                pos += 1
            if not isinstance(obj, dict) or not isinstance(obj.get("k"), str):
                bad.append((n, "no k", str(obj)[:70]))
                continue
            if obj["k"] not in KNOWN:
                bad.append((n, f"unknown k {obj['k']}", s[:70]))
                continue
            obj["_line"] = n
            good.append(obj)
    return good, bad


ANCHOR_RE = re.compile(r"""^(?:
    w(?P<w>\d+)(?P<we>\.e)? |
    s(?P<s>\d+)(?P<se>\.e)? |
    k(?P<k>\d+)(?:\+(?P<ks>\d*\.?\d+)s?)? |
    @(?P<at>\d*\.?\d+) |
    d(?P<d>\d+) |
    (?P<id>[A-Za-z][\w.\-]*?)\.(?P<io>in|out)
)(?P<off>[+\-]\d+f)?$""", re.X)


def parse_range(v):
    """A word range in any of the shapes models write: "33-41", 33, [33,41], "w33..w41"."""
    if isinstance(v, bool):
        raise ValueError("bad range")
    if isinstance(v, (int, float)):
        return int(v), int(v)
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return int(v[0]), int(v[1])
    if isinstance(v, str):
        t = v.replace("w", "").replace(".e", "").replace("..", "-").strip()
        parts = [p for p in t.split("-") if p.strip() != ""]
        if len(parts) == 1:
            return int(parts[0]), int(parts[0])
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    raise ValueError(f"bad range {v!r}")


def parse_cap(cap):
    """chunk ('|' chunk)*, term := INT['*'] | INT['*']-INT['*']. A bad term drops only itself."""
    chunks = []
    for ch in str(cap).split("|"):
        ws = []
        for part in ch.split():
            star = part.endswith("*")
            part = part.rstrip("*")
            try:
                if "-" in part:
                    a, b = part.split("-", 1)
                    a_star = a.endswith("*")
                    a = int(a.rstrip("*"))
                    b = int(b.rstrip("*"))
                    if 0 <= b - a <= 64:
                        ws += [(i, star or a_star) for i in range(a, b + 1)]
                else:
                    ws.append((int(part), star))
            except ValueError:
                continue
        if ws:
            chunks.append(ws)
    return chunks


# ---------------------------------------------------------------- compile state

class Beat:
    def __init__(self, raw, idx):
        self.raw, self.idx = raw, idx
        self.id = raw.get("id")
        self.ws, self.src, self.silent = [], None, None
        self.auto = False
        self.f0 = self.f1 = self.pf0 = self.pf1 = 0
        self.wf, self.wfe = {}, {}
        self.pics = []
        self.shots = []
        self.lead = None
        self.cut = {}


class Ctx:
    def __init__(self, index, bin_items=None, tray=None, faces=None, sid=None, owner=False, seed=None):
        self.index = index or {}
        v = self.index.get("video") or {}
        self.src_w, self.src_h = int(v.get("width") or 1080), int(v.get("height") or 1920)
        self.src_dur = float(v.get("duration") or 0.0)
        self.src_fps = float(v.get("fps") or 30.0)
        self.sid = sid
        self.owner = owner
        self.words = []
        for w in self.index.get("words") or []:
            t0, t1 = float(w.get("t0", 0)), float(w.get("t1", 0))
            self.words.append({"w": str(w.get("w", "")), "t0": t0, "t1": max(t1, t0 + 0.01), "filler": bool(w.get("filler"))})
        self.text = {i: w["w"] for i, w in enumerate(self.words)}
        self.fixed = set()
        self.sents = {}
        for s in self.index.get("sentences") or []:
            try:
                self.sents[int(str(s["id"]).lstrip("s"))] = (int(s["wi0"]), int(s["wi1"]))
            except (KeyError, ValueError):
                continue
        self.kshots = {}
        for k in self.index.get("shots") or []:
            try:
                self.kshots[int(k["id"])] = (float(k["start"]), float(k["end"]))
            except (KeyError, ValueError):
                continue
        if not self.src_dur:
            self.src_dur = max([w["t1"] for w in self.words] + [e for _, e in self.kshots.values()] + [1.0])
        self.bin = bin_items or {}
        self.tray = tray or {}
        self.faces = faces if faces is not None else self._faces_from_index()
        self.lumas = [(float(s.get("t", 0)), float(s.get("mean_luma", 0))) for s in ((self.index.get("spatial") or {}).get("samples") or [])
                      if s.get("mean_luma") is not None]
        self.findings, self.notes = [], []
        self.items, self.byid = [], {}
        self.sound = []
        self.assets = {}
        self.sets = {}
        self.beats, self.beat_by = [], {}
        self.spine = []
        self.seen = {}
        self.cursor = {}
        self.wf_any, self.wfe_any = {}, {}
        self.voices = {}
        self.g_snd = []
        self.seed = seed
        self.film, self.dna, self.treat, self.binds, self.tools = {}, {}, {}, [], []
        self.fps, self.W, self.H = 30, 1080, 1920
        self.dur_f = 0
        self.band = SAFETY_BAND
        self.band_name = None
        self.drop = set()
        self.base_grade = dict(IDENTITY)
        self.rejects = []
        self.gap = None
        self.shots = []
        self.blocks = []
        self.traffic = []          # what type_traffic and display did to type that asked for a taken seat
        self.cur_gid = None        # the gesture running now: every item it adds carries its id as _g
        self.boxes = {}
        self.own = {}
        self.views = {}
        self.matte = None
        self.max_up = 6.0
        self.work = None
        self.thumb_cache = {}
        self.waiting = None
        self.perc = {}
        self.soft = {}
        self.soft_at = 3.4
        self.dead_air = 0.0
        # taste (score/taste.py)
        self.governed = False
        self.plan = {}             # decisions of the reading before this compile: what to drop, move, hold
        self.devices = {}          # every device line of the Score: its department and the event it answers
        self.taste_rows = []       # what this compile itself removed or repaired, with the reason
        self.asks = {}             # the Scout's ask ledger, id -> ask
        self.stress = {}           # word id -> measured stress of the voice
        self.system = {}           # the film's one design system, from the treat line
        self.cuts = {}             # ids the last look named, id -> why
        self.calm = False
        self.cap_y = None          # the film's one caption seat
        self.seats = None          # the film's display seats
        self.used_seats = []       # the display seats lines have taken so far, film seats and span seats alike
        self.span_seats = {}       # gesture id -> the seat it was given for its own span, or None when the film's seat stood
        self.cam_land, self.cam_len = {}, {}
        self.cam_marks = []        # (frame, rect, source second): the framing an earned camera line left the take in
        self.wrote = {}            # device lines the Score wrote, by department
        self.flip_frames = None    # programme frames where a speech edit changes the take's framing by a tenth
        self.spine_talk = []       # per spine stretch: does it carry the voice?
        self.run_crops, self.flip_runs = {}, {}       # one framing per run of a closer take; which runs took their tenth
        self.step_once = False
        self.offered = None        # style cards the Brief offered for this footage

    def _faces_from_index(self):
        out = []
        for s in (self.index.get("spatial") or {}).get("samples") or []:
            if s.get("faces"):
                out.append({"t": float(s.get("t", 0)), "boxes": s["faces"]})
        return out

    def find(self, msg):
        if msg not in self.findings:
            self.findings.append(msg)

    def add(self, it):
        """Every item has a stable id. One with none, or a clash, does not compile."""
        i = it.get("id")
        if not i or i in self.byid:
            self.find(f"item dropped: id {i!r} is missing or used twice")
            return None
        if self.cur_gid and "_g" not in it:
            it["_g"] = self.cur_gid
        self.items.append(it)
        self.byid[i] = it
        return it


def t0(ctx, w):
    return ctx.words[w]["t0"]


def t1(ctx, w):
    return ctx.words[w]["t1"]


def frame_src(ctx, t):
    """Source seconds to the first programme frame showing them, through the spine."""
    for s in ctx.spine:
        if s["src"][0] - 1e-6 <= t < s["src"][1]:
            return s["f"][0] + int(round((t - s["src"][0]) * ctx.fps))
    return None


def frame_near(ctx, t):
    """Source seconds to the nearest programme frame: a beat of the track can fall in a pause the film closed."""
    best, gap = None, 1e9
    for s in ctx.spine:
        q = clamp(t, s["src"][0], s["src"][1] - 1 / ctx.fps)
        if abs(q - t) < gap:
            best, gap = s["f"][0] + int(round((q - s["src"][0]) * ctx.fps)), abs(q - t)
    return best


def frame_of(ctx, a, b=None, end=False):
    """One anchor to a programme frame, or None. `end` means a bare word closes at its end."""
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return int(round(float(a) * ctx.fps))
    if not isinstance(a, str):
        return None
    m = ANCHOR_RE.match(a.strip())
    if not m:
        return None
    off = int(m["off"][:-1]) if m["off"] else 0
    f = None
    if m["w"] is not None:
        w = int(m["w"])
        useend = bool(m["we"]) or end
        maps = ((b.wfe if useend else b.wf) if b else {}, ctx.wfe_any if useend else ctx.wf_any)
        for mp in maps:
            if w in mp:
                f = mp[w]
                break
    elif m["s"] is not None:
        rng = ctx.sents.get(int(m["s"]))
        if rng:
            use_end = bool(m["se"]) or end
            w = rng[1] if use_end else rng[0]
            for mp in (((b.wfe if use_end else b.wf) if b else {}), ctx.wfe_any if use_end else ctx.wf_any):
                if w in mp:
                    f = mp[w]
                    break
    elif m["k"] is not None:
        k = ctx.kshots.get(int(m["k"]))
        if k:
            f = frame_src(ctx, k[0] + float(m["ks"] or 0))
    elif m["at"] is not None:
        f = int(round(float(m["at"]) * ctx.fps))
    elif m["d"] is not None:
        beats = (ctx.index.get("perception") or {}).get("beats") or []
        d = int(m["d"])
        if d < len(beats):
            f = frame_near(ctx, float(beats[d]))
    elif m["id"]:
        name, io = m["id"], m["io"]
        if name in ctx.beat_by:
            bb = ctx.beat_by[name]
            f = bb.pf0 if io == "in" else bb.pf1
        elif name in ctx.byid:
            f = ctx.byid[name]["f"][0 if io == "in" else 1]
        elif any(i.get("_g") == name for i in ctx.items):      # a gesture by its id: where what it drew lands, and leaves
            grp = [i["f"] for i in ctx.items if i.get("_g") == name]
            f = min(g[0] for g in grp) if io == "in" else max(g[1] for g in grp)
        else:
            s = next((x for x in ctx.shots if x["id"] == name), None)
            if s is None:
                s = next((x for x in ctx.shots if x["id"].startswith(name + ".")), None)
            if s:
                f = s["f"][0 if io == "in" else 1]
            else:                # a gesture may have replaced that shot; the plan still knows where it stood
                q = planned_span(ctx, name)
                if q:
                    f = q[0 if io == "in" else 1]
    return None if f is None else f + off


def planned_span(ctx, name):
    """A shot as the beat planned it. Shots number from 0, and a model counting from 1 means the last one when it runs past."""
    plan = getattr(ctx, "planned", None) or {}
    if name in plan:
        return plan[name]
    m = re.fullmatch(r"(.+\.s)(\d+)", name)
    return plan.get(f"{m[1]}{int(m[2]) - 1}") if m else None


def span_of(ctx, b, at, default=None):
    """'w5..w8', 'b01.in..b02.in', a single anchor, 'beat' or nothing, to (a, z). z may be None."""
    if at in (None, "beat", ""):
        return default if default else (b.pf0, b.pf1)
    if isinstance(at, str) and ".." in at:
        l, r = at.split("..", 1)
        a, z = frame_of(ctx, l, b), frame_of(ctx, r, b, end=True)
        return a, z
    whole = whole_span(ctx, at)
    return whole if whole else (frame_of(ctx, at, b), None)


def whole_span(ctx, name):
    """A bare beat, shot or item id names its whole span: a model writes `b05.s1` and means that shot."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if name in ctx.beat_by:
        bb = ctx.beat_by[name]
        return (bb.pf0, bb.pf1)
    s = next((x for x in ctx.shots if x["id"] == name), None) or ctx.byid.get(name)
    return (s["f"][0], s["f"][1]) if s and "f" in s else None


def src_anchor(ctx, a):
    """A source-side anchor, for a beat's own span, to source seconds."""
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        return float(a)
    m = ANCHOR_RE.match(str(a).strip())
    if not m:
        raise ValueError(f"bad anchor {a!r}")
    off = int(m["off"][:-1]) / ctx.fps if m["off"] else 0.0
    if m["w"] is not None:
        w = int(m["w"])
        if not 0 <= w < len(ctx.words):
            raise ValueError(f"no word {w}")
        return (ctx.words[w]["t1"] if m["we"] else ctx.words[w]["t0"]) + off
    if m["s"] is not None:
        s = ctx.sents.get(int(m["s"]))
        if not s:
            raise ValueError(f"no sentence {m['s']}")
        return (ctx.words[s[1]]["t1"] if m["se"] else ctx.words[s[0]]["t0"]) + off
    if m["k"] is not None:
        k = ctx.kshots.get(int(m["k"]))
        if not k:
            raise ValueError(f"no shot k{m['k']}")
        return k[0] + float(m["ks"] or 0) + off
    if m["at"] is not None:
        return float(m["at"]) + off
    raise ValueError(f"anchor {a!r} has no source time")


# ---------------------------------------------------------------- film level

def read_film(ctx, lines):
    film = {}
    for l in lines:
        if l["k"] == "film":
            film.update({k: v for k, v in l.items() if not k.startswith("_")})
        elif l["k"] == "dna":
            ctx.dna.update({k: v for k, v in l.items() if not k.startswith("_")})
        elif l["k"] == "treat":
            ctx.treat = {k: v for k, v in l.items() if not k.startswith("_")}
        elif l["k"] == "bind":
            ctx.binds.append({k: v for k, v in l.items() if not k.startswith("_")})
        elif l["k"] == "tool":
            ctx.tools.append({k: v for k, v in l.items() if not k.startswith("_")})
        elif l["k"] == "pics":
            if l.get("id"):
                ctx.sets[str(l["id"])] = l
        elif l["k"] == "def" and not ctx.owner:
            ctx.find(f"line {l['_line']}: def is refused on a customer's Score")
        elif l["k"] == "cut" and l.get("id"):
            ctx.cuts[str(l["id"])] = str(l.get("why") or "the last look")
    ctx.film = film
    try:
        ctx.fps = int(round(float(film.get("fps", 30)))) or 30
    except (TypeError, ValueError):
        ctx.fps = 30
    fr = film.get("frame", "9:16")
    ctx.W, ctx.H = FRAMES.get(str(fr), FRAMES["9:16"]) if not isinstance(fr, list) else (int(fr[0]), int(fr[1]))
    if ctx.seed is None:
        try:
            ctx.seed = int(film.get("seed", 7))
        except (TypeError, ValueError):
            ctx.seed = 7
    own = film.get("own")
    if own is None and isinstance(film.get("band"), dict):      # models write the band's numbers in either place
        own = film["band"].get("own", film["band"])
        film["band"] = "own"
    name = str(film.get("band") or "")
    if isinstance(own, dict) and all(isinstance(own.get(k), (int, float)) and own[k] > 0 for k in ("med", "p10", "p90")):
        ctx.band, ctx.band_name = own, "own"
    elif name in BANDS:
        ctx.band, ctx.band_name = BANDS[name], name
    else:
        ctx.find("no band in the film line: pulse falls back to a safety spread" if not name else f"band {name} unknown")
    gap = film.get("gap")
    if isinstance(gap, (int, float)) and not isinstance(gap, bool) and gap >= 0:
        ctx.gap = float(gap)
    if not ctx.dna:
        ctx.find("no dna line: safety fonts, seat and ink")
    if ctx.governed:
        read_system(ctx)
    voices(ctx)
    film_look(ctx)


def read_system(ctx):
    """The film's one design system, named in the treat line:
    {"card":"P1","devices":["big","card"],"sounds":["impact"],"seats":[[.5,.26],[.5,.72]]}. Score syntax only.
    A Score with none is not refused: its own most used devices stand in (taste.decide), and the meter says so."""
    from . import taste
    sy = ctx.treat.get("system")
    named = isinstance(sy, dict)
    sy = sy if named else {}
    card = str(sy.get("card") or ctx.film.get("band") or "")
    if ctx.offered and card not in ctx.offered and ctx.band_name != "own":
        fit = next((c for c in ctx.offered if c in BANDS), None)
        if fit:
            ctx.find(f"system card {card or 'none'} is not one this footage can supply ({' '.join(ctx.offered)}): {fit} stands in")
            card = fit
    if card in BANDS and ctx.band_name != "own":
        ctx.band, ctx.band_name = BANDS[card], card
    devices = [str(d.get("g") if isinstance(d, dict) else d) for d in (sy.get("devices") or []) if d]
    _, roles = sound_table()
    table, _ = sound_table()
    sounds = []
    for x in sy.get("sounds") or []:
        x = str(x.get("s") if isinstance(x, dict) else x)
        role = x if x in roles or x in VOICE_LAYERS else (table.get(x) or {}).get("role")
        if role and role not in sounds:
            sounds.append(role)
    ctx.system = {"named": named, "card": card if card in BANDS else None, "devices": [d for d in dict.fromkeys(devices) if d in taste.DEPT],
                  "sounds": sounds, "seats": [x for x in (sy.get("seats") or ctx.dna.get("seats") or []) if isinstance(x, (list, tuple)) and len(x) == 2]}
    ctx.calm = str(ctx.film.get("intensity") or sy.get("intensity") or "") == "calm"


CAP_FLOOR_V, CAP_CEIL_V = 0.04, 0.054      # caption sizes of a vertical film, as shares of its height


def voices(ctx):
    dna = ctx.dna
    pair = dna.get("pair") or {}
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        pair = {"a": pair[0], "b": pair[1]}
    safe = {"a": ("InterDisplay-Bold.ttf", 0.03, -3), "b": ("InstrumentSerif-Italic.ttf", 0.034, 0)}
    got = {}
    for key in ("a", "b"):
        font, size, track = parse_font_spec(pair.get(key)) if isinstance(pair, dict) and pair.get(key) else (None, None, None)
        if font is None:
            if isinstance(pair, dict) and pair.get(key):
                ctx.find(f"font {pair.get(key)!r} is not in the set, safety font used")
            font = safe[key][0]
        got[key] = (font, size if size else safe[key][1], track)
    # A vertical film is read on a phone at arm's length: the plain voice is never under CAP_FLOOR_V of the frame's
    # height, and the second voice keeps its step over it (round six: the rater and the critic both read the captions
    # of the round-five films as tiny, and a customer's own film was kept for its readable ones).
    lift = max(1.0, CAP_FLOOR_V / max(got["a"][1], 1e-6)) if ctx.governed and ctx.H > ctx.W else 1.0
    for key in ("a", "b"):
        font, size, track = got[key]
        if ctx.governed:
            size = clamp(size * lift, 0.022, 0.05 if lift == 1.0 else CAP_CEIL_V)        # P5: a caption is read on a phone: 28 px at 720x1280 or more
        ctx.voices[key] = {"font": font, "px": max(14, int(round(size * ctx.H))), "track": safe[key][2] if track is None else track}
        sh = dna.get("shadow", {"a": 0.35, "blur": 0.012})
        if sh:
            ctx.voices[key]["shadow"] = sh


def parse_look(ctx, v, where):
    if v is None or v == "":
        return None
    from . import grade
    if isinstance(v, str):
        if v in LOOKS:
            return dict(LOOKS[v])
        if v in grade.LOOKS:           # a design the film's look is solved from (film_look): no fixed numbers of its own
            return {}
        ctx.find(f"{where}: look is prose, nothing to grade from. Write a name: {', '.join(grade.LOOKS)}")
        return None
    if isinstance(v, dict):
        return {k: float(x) for k, x in v.items() if k in IDENTITY and isinstance(x, (int, float)) and not isinstance(x, bool)}
    return None


def solved_look(ctx, raw):
    """The film's look solved on its own frames (score.grade): the design the Score named, the tone its style card
    measures, the strength this footage needs to stand off its upload with skin left alone. Kept beside the session,
    since one film is compiled many times. None when there are no frames to read: the older numbers stand."""
    if not ctx.governed or not ctx.work:
        return None
    from . import grade
    card = (ctx.system or {}).get("card")
    try:
        arr = grade.frames(ctx.work)
        if arr is None:
            return None
        key = hashlib.sha1(json.dumps([raw, card, grade.VERSION, list(arr.shape), int(arr[:, ::7, ::7].sum())], sort_keys=True, default=str).encode()).hexdigest()[:16]
        path = os.path.join(ctx.work, f"look_{key}.json")
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
        look = grade.solve(arr, raw, card)
        if look:
            with open(path, "w") as f:
                json.dump(look, f)
        return look
    except Exception as exc:  # noqa: BLE001 - a look that can't be solved is never a film that can't be made
        ctx.find(f"dna: the look could not be solved on these frames ({type(exc).__name__}), the plain grade stands")
        return None


def grade_names():
    from . import grade
    return list(grade.LOOKS)


def film_look(ctx):
    raw = ctx.dna.get("look", ctx.film.get("look"))
    g = parse_look(ctx, raw, "dna")
    ctx.base_grade = {**IDENTITY, **(g or {})}
    src = raw if isinstance(raw, dict) else {}
    ctx.film_out = {
        "grain": float(src.get("grain", ctx.dna.get("grain", 0.0)) or 0.0),
        "vignette": float(src.get("vignette", ctx.dna.get("vignette", 0.0)) or 0.0),
        "over_type": ctx.dna.get("over_type") is True,          # a yes or no: a sentence here turned grain on over the type
        "grade": dict(ctx.base_grade),
    }
    look = solved_look(ctx, raw)
    if look:
        if isinstance(raw, str) and raw not in grade_names():
            ctx.find(f"dna: look {raw!r} is not a design, {look['name']} stands in. The designs: {', '.join(grade_names())}")
        ctx.film_out["look"] = look
    fl = ctx.dna.get("film") if isinstance(ctx.dna.get("film"), dict) else {}
    for k in ("halation", "bloom", "sharpen"):
        v = fl.get(k, src.get(k, ctx.dna.get(k)))
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            ctx.film_out[k] = float(v)
    for k in ("mesh", "raster"):
        v = fl.get(k, ctx.dna.get(k))
        if isinstance(v, dict):
            ctx.film_out[k] = {a: b for a, b in v.items() if isinstance(b, (int, float))}
    if fl.get("mesh_over"):
        ctx.film_out["mesh_over"] = True
    cin = (ctx.dna.get("cap") or {}).get("in") if isinstance(ctx.dna.get("cap"), dict) else None
    if isinstance(cin, str) and cin in ENTRANCES and cin != "cut":
        ctx.film_out["cap_in"] = cin            # the caption's entrance, for the lowering: the second voice arrives with it


def plate_of(ctx, name):
    """A plate name, hex colour or dna key to the plate fields. Never fails."""
    dna_plate = ctx.dna.get("plate")
    c = None
    if isinstance(name, dict):
        if is_hex(name.get("c0")):
            c0 = norm_hex(name["c0"])
            c1 = norm_hex(name["c1"]) if is_hex(name.get("c1")) else None
            return _plate(c0, c1, name.get("grain"))
        name = name.get("plate") or name.get("c")
    if is_hex(name):
        c = norm_hex(name)
    elif isinstance(dna_plate, dict) and name in dna_plate and is_hex(dna_plate[name]):
        c = norm_hex(dna_plate[name])
    elif name in (None, "plate", "dna") and is_hex(dna_plate):
        c = norm_hex(dna_plate)
    elif name in PLATE_NAMES:
        c = PLATE_NAMES[name]
    elif isinstance(dna_plate, dict) and dna_plate:
        c = next((norm_hex(v) for v in dna_plate.values() if is_hex(v)), None)
    if c is None:
        ctx.find(f"plate {name!r} unknown, black used")
        c = PLATE_NAMES["black"]
    return _plate(c, None, None)


def _plate(c0, c1, grain):
    if c1 is None:
        r, g, b = hex_rgb(c0)
        c1 = rgb_hex(r * 0.82, g * 0.82, b * 0.82)
    return {"c0": c0, "c1": c1, "grain": 0.04 if grain is None else float(grain)}


def ink_on(ctx, plate):
    """Polarity is solved per plate: white on dark, near black on light."""
    base = ctx.dna.get("ink") if is_hex(ctx.dna.get("ink")) else "#FFFFFF"
    base = norm_hex(base)
    if plate is None:
        return base
    bg = plate["c0"]
    if abs(luma(base) - luma(bg)) >= 0.4:
        return base
    return "#111111" if luma(bg) > 0.5 else "#FFFFFF"


# ---------------------------------------------------------------- beats and the spine

def read_beats(ctx, lines):
    """Beats in the order written are the programme. A line that can't be read drops itself."""
    beats = []
    for l in lines:
        if l["k"] == "fix":
            read_fix(ctx, l)
        elif l["k"] == "drop":
            read_drop(ctx, l.get("w"))
    for spec in ctx.film.get("drop") or []:
        read_drop(ctx, spec)
    for l in lines:
        if l["k"] != "b":
            continue
        bid = l.get("id")
        if not isinstance(bid, str) or not re.fullmatch(r"[A-Za-z][\w\-]*", bid) or bid in ctx.beat_by:
            ctx.rejects.append((l["_line"], "beat id missing, malformed or repeated", str(bid)[:40]))
            continue
        b = Beat(l, len(beats))
        try:
            if l.get("drop"):
                read_drop(ctx, l.get("w"))
                continue
            if l.get("w") is not None:
                a, z = parse_range(l["w"])
                z = min(z, len(ctx.words) - 1)
                if a < 0 or a > z or not ctx.words:
                    raise ValueError(f"no such words {l['w']}")
                b.ws = list(range(a, z + 1))
            elif l.get("at") is not None:
                at = str(l["at"])
                if ".." not in at:
                    raise ValueError("at needs a span")
                x, y = at.split("..", 1)
                b.src = (src_anchor(ctx, x), src_anchor(ctx, y))
                if b.src[1] <= b.src[0]:
                    raise ValueError("empty span")
            elif isinstance(l.get("f"), (int, float)) and l["f"] > 0:
                if ctx.governed and (bid in ctx.cuts or "*" in ctx.cuts):
                    # a silent beat is time the film added to the upload: the last look, or the floor, takes it back
                    ctx.taste_rows.append({"id": bid, "did": "cut", "why": ctx.cuts.get(bid) or ctx.cuts["*"], "dept": "beat"})
                    continue
                b.silent = int(l["f"])
            else:
                raise ValueError("no span")
        except (ValueError, IndexError, TypeError) as e:
            ctx.rejects.append((l["_line"], f"beat {bid}: {e}", ""))
            continue
        ctx.beat_by[bid] = b
        beats.append(b)
    ctx.beats = fill_coverage(ctx, beats)
    played = {}
    for b in ctx.beats:
        b.ws = [w for w in b.ws if w not in ctx.drop]
        twice = [w for w in b.ws if w in played]
        if twice:
            ctx.find(f"{b.id} plays w{twice[0]}-{twice[-1]} a second time (also {played[twice[0]]}). A callback, or a drop you meant")
        for w in b.ws:
            played.setdefault(w, b.id)
    ctx.beats = [b for b in ctx.beats if b.ws or b.src or b.silent]


def read_fix(ctx, l):
    """{"k":"fix","w":"7-8","t":"Lar Novo"}: the transcript misheard. Spelled by the model."""
    try:
        a, z = parse_range(l.get("w"))
    except ValueError:
        return
    toks = str(l.get("t", "")).split()
    rng = [w for w in range(a, z + 1) if 0 <= w < len(ctx.words)]
    for i, w in enumerate(rng):
        if i == len(rng) - 1:
            ctx.text[w] = " ".join(toks[i:])
        else:
            ctx.text[w] = toks[i] if i < len(toks) else ""
        ctx.fixed.add(w)


def read_drop(ctx, spec):
    try:
        a, z = parse_range(spec)
    except (ValueError, TypeError):
        return
    ctx.drop |= set(range(a, z + 1))


def fill_coverage(ctx, beats):
    """Words no surviving beat covers get hold beats where they fall. Nothing the customer
    said disappears because a line failed. A deliberate cut is a drop."""
    covered = set()
    spans = [b.src for b in beats if b.src]
    for b in beats:
        covered |= set(b.ws)
    for w, x in enumerate(ctx.words):       # a beat written in seconds plays the words inside its span
        mid = (x["t0"] + x["t1"]) / 2
        if any(a <= mid < z for a, z in spans):
            covered.add(w)
    runs, run = [], []
    for w in range(len(ctx.words)):
        if w in covered or w in ctx.drop:
            if run:
                runs.append(run)
                run = []
        else:
            run.append(w)
    if run:
        runs.append(run)
    for run in runs:
        bid = f"hold{run[0]:03d}"
        b = Beat({"id": bid, "type": "cap:2"}, 0)
        b.ws, b.auto = run, True
        ctx.beat_by[bid] = b
        ctx.find(f"w{run[0]}-{run[-1]} are in no beat: {bid} holds them. Give them a beat, or drop them")
        pos = None
        for i, x in enumerate(beats):
            if x.ws and run[0] - 1 in x.ws:
                pos = i + 1
        if pos is None:
            for i, x in enumerate(beats):
                if x.ws and run[-1] + 1 in x.ws:
                    pos = i
                    break
        if pos is None:
            pos = 0 if not beats else len(beats)
        beats.insert(pos, b)
    if not beats:     # no words, no beat: the whole source holds, so a film still comes out
        b = Beat({"id": "hold000"}, 0)
        b.src, b.auto = (0.0, ctx.src_dur), True
        ctx.beat_by["hold000"] = b
        beats.append(b)
    for i, b in enumerate(beats):
        b.idx = i
    return beats


def build_spine(ctx):
    """The programme is the beats in order. Voice runs are split on the film's gap and joined
    where the source is continuous, then quantised to whole frames."""
    runs = []
    for b in ctx.beats:
        if b.ws:
            cur = [b.ws[0]]
            for i, j in zip(b.ws, b.ws[1:]):
                pause = t0(ctx, j) - t1(ctx, i)
                if j != i + 1 or (ctx.gap is not None and pause > ctx.gap):
                    runs.append({"b": b, "ws": cur})
                    cur = [j]
                else:
                    cur.append(j)
            runs.append({"b": b, "ws": cur})
        elif b.src:
            runs.append({"b": b, "s0": b.src[0], "s1": b.src[1], "ws": []})
        else:
            runs.append({"b": b, "silent": b.silent, "ws": []})
    for r in runs:
        if r["ws"]:
            r["s0"] = t0(ctx, r["ws"][0]) - PAD_IN
            r["s1"] = t1(ctx, r["ws"][-1]) + PAD_OUT
    for r, n in zip(runs, runs[1:]):
        if r["ws"] and n["ws"] and n["ws"][0] == r["ws"][-1] + 1:
            pause = t0(ctx, n["ws"][0]) - t1(ctx, r["ws"][-1])
            if ctx.gap is None or pause <= ctx.gap:
                mid = t0(ctx, n["ws"][0]) - min(PAD_IN, max(pause, 0) / 2)
                r["s1"] = n["s0"] = mid
                n["joined"] = True
            else:
                keep = ctx.gap / 2
                r["s1"] = t1(ctx, r["ws"][-1]) + keep
                n["s0"] = t0(ctx, n["ws"][0]) - keep
    if runs and runs[-1]["ws"]:
        runs[-1]["s1"] = min(ctx.src_dur, t1(ctx, runs[-1]["ws"][-1]) + float(ctx.film.get("tail", 0.3) or 0.0))
    prog, prev_end = 0, None
    for r in runs:
        if r.get("silent"):
            r["a"], r["b_"] = prog, prog + int(r["silent"])
            prog = r["b_"]
            prev_end = None
            continue
        s0 = clamp(r["s0"], 0.0, ctx.src_dur)
        if r.get("joined") and prev_end is not None:
            s0 = prev_end
        elif prev_end is not None and 0 < prev_end - s0 <= 0.2:
            # The air before a first word reaches back into the span before it (a beat written in seconds, then a
            # beat of words: p5571, 4.41 after 4.46). That is one continuous take. Lowered as a jump it became an
            # insert of the upload, framed differently from the upload itself.
            s0 = prev_end
        s1 = clamp(r["s1"], s0 + 1.0 / ctx.fps, max(ctx.src_dur, s0 + 1.0 / ctx.fps))
        n = max(1, int(round((s1 - s0) * ctx.fps)))
        s1 = s0 + n / ctx.fps
        r["a"], r["b_"], r["s0"], r["s1"] = prog, prog + n, s0, s1
        last = ctx.spine[-1] if ctx.spine else None
        if last and r.get("joined") and last["f"][1] == prog and abs(last["src"][1] - s0) < 1e-6:
            last["f"][1] = prog + n
            last["src"][1] = s1
        else:
            ctx.spine.append({"f": [prog, prog + n], "src": [s0, s1]})
            ctx.spine_talk.append(bool(r["ws"]))        # a stretch of the voice, or of picture written in seconds
        prog += n
        prev_end = s1
        b = r["b"]
        for w in r["ws"]:
            f = clamp(r["a"] + int(round((t0(ctx, w) - s0) * ctx.fps)), r["a"], r["b_"] - 1)
            e = clamp(r["a"] + int(round((t1(ctx, w) - s0) * ctx.fps)), f + 1, r["b_"])
            b.wf[w], b.wfe[w] = f, e
            ctx.wf_any.setdefault(w, f)
            ctx.wfe_any.setdefault(w, e)
    for b in ctx.beats:
        mine = [r for r in runs if r["b"] is b]
        b.f0, b.f1 = mine[0]["a"], mine[-1]["b_"]
    ctx.dur_f = max(1, prog)
    dead = sum(max(0.0, t0(ctx, r["ws"][j + 1]) - t1(ctx, r["ws"][j]) - 0.3) for r in runs for j in range(len(r["ws"]) - 1))
    ctx.dead_air = dead


def follow_spine(ctx, a, z):
    """Programme frames to linear pieces of source: [(a, z, s0, s1)]. Frames over a silent
    beat freeze on the nearest picture."""
    out, f = [], a
    last_t = None
    for s in ctx.spine:
        if s["f"][1] <= f:
            last_t = s["src"][1] - 1.0 / ctx.fps
            continue
        if s["f"][0] >= z:
            break
        if s["f"][0] > f:
            t = last_t if last_t is not None else s["src"][0]
            out.append((f, s["f"][0], t, t))
            f = s["f"][0]
        b = min(z, s["f"][1])
        s0 = s["src"][0] + (f - s["f"][0]) / ctx.fps
        out.append((f, b, s0, s0 + (b - f) / ctx.fps))
        last_t = s0 + (b - f - 1) / ctx.fps
        f = b
        if f >= z:
            break
    if f < z:
        t = last_t if last_t is not None else 0.0
        out.append((f, z, t, t))
    return out


# ---------------------------------------------------------------- pictures

def fit_crop(rect, src_w, src_h, W, H):
    """Grow a rect to the target aspect around its centre, then keep it inside the source."""
    x0, y0, x1, y1 = rect
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    w, h = max(1e-3, x1 - x0), max(1e-3, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    target = (W / H) * (src_h / src_w)      # width over height in source fractions
    if w / h < target:
        w = h * target
    else:
        h = w / target
    if w > 1.0:
        w, h = 1.0, 1.0 / target
    if h > 1.0:
        h, w = 1.0, target
    cx = clamp(cx, w / 2, 1 - w / 2)
    cy = clamp(cy, h / 2, 1 - h / 2)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def fit_inside(rect, src_w, src_h, W, H):
    """Shrink a rect to the target aspect around its centre. A rect the Director wrote is a limit: it
    was drawn to leave something out, so the picture stays inside it and nothing past its edge shows."""
    x0, y0, x1, y1 = rect
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    w, h = max(1e-3, x1 - x0), max(1e-3, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    target = (W / H) * (src_h / src_w)
    if w / h > target:
        w = h * target
    else:
        h = w / target
    cx = clamp(cx, w / 2, 1 - w / 2)
    cy = clamp(cy, h / 2, 1 - h / 2)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def written_rect(ctx, ref):
    """Did the Score give this crop as a rect, directly or by a name in dna.crops?"""
    c = ref.get("crop")
    if isinstance(c, (list, tuple)):
        return True
    d = ctx.dna.get("crops")
    return isinstance(c, str) and isinstance(d, dict) and c in d


def is_take(pic):
    """The upload itself, or a cut-out of it on the same timeline: perception applies to both."""
    return pic == "main" or str(pic).startswith("own:cut")


def asset_for(ctx, name):
    if name == "main":
        a = ctx.assets.setdefault("main", {"kind": "video", "path": "source.mp4", "w": ctx.src_w, "h": ctx.src_h,
                                           "dur": ctx.src_dur, "fps": ctx.src_fps})
        return a
    if name in ctx.assets:
        return ctx.assets[name]
    if name.startswith("own:"):
        it = ctx.own.get(name[4:])
        if it is None:
            return None
        ctx.assets[name] = {k: v for k, v in it.items() if k != "plate"}
        return ctx.assets[name]
    table = ctx.bin if name.startswith("bin:") else ctx.tray
    key = name.split(":", 1)[1]
    it = table.get(key)
    if it is None:
        return None
    a = {"kind": it.get("kind", "image"), "path": it["path"], "w": int(it.get("w") or 1080), "h": int(it.get("h") or 1920)}
    if a["kind"] == "video":
        a["dur"] = float(it.get("dur") or 3.0)
        a["fps"] = float(it.get("fps") or 30.0)
    ctx.assets[name] = a
    return a


def parse_pic(ctx, p, where):
    """One picture reference to a ref dict, or a list for a named set, or None."""
    if isinstance(p, str):
        p = {"src": p}
    if not isinstance(p, dict):
        return None
    ref = {k: p.get(k) for k in ("crop", "cam", "d", "treat", "look", "step", "zoom", "rate", "for")}
    if "plate" in p:
        ref.update(kind="plate", plate=plate_of(ctx, p["plate"]))
        return ref
    s = str(p.get("src") or p.get("pic") or "main").strip()
    if s == "main" or s.startswith("main@"):
        ref.update(kind="main", asset="main", sync=True)
        if "@" in s:
            tail = s.split("@", 1)[1]
            m = re.fullmatch(r"k(\d+)", tail)
            m2 = re.fullmatch(r"(\d*\.?\d+)(?:-(\d*\.?\d+))?", tail)
            if m:
                ref["k"] = int(m.group(1))
            elif m2:
                ref.update(sync=False, t0=float(m2.group(1)), t1=float(m2.group(2)) if m2.group(2) else None)
            else:
                ctx.find(f"{where}: can't read {s!r}, main follows the voice")
        return ref
    if s.startswith("view:"):
        v = ctx.views.get(s[5:].strip())
        if v is None:
            ctx.find(f"{where}: {s} is not on the board, main stands in")
            ref.update(kind="main", asset="main", sync=True)
            return ref
        # a detail of the upload at another moment: a picture Supply found, not a camera move on the take
        ref.update(kind="main", asset="main", sync=False, t0=v["t0"], t1=v["t1"], crop=None, zoom=None, _view=v["id"])
        return ref
    if s.startswith("cut:") or s == "clean":
        name = "clean" if s == "clean" else "cut_" + s[4:]
        key = "own:" + name
        if asset_for(ctx, key) is None:
            ctx.find(f"{where}: {s} was not made for this take, main stands in")
            ref.update(kind="main", asset="main", sync=True)
            return ref
        ref.update(kind="own", asset=key, sync=s != "clean")
        return ref
    if s.startswith("bin:") or s.startswith("tray:"):
        key, _, inp = s.partition("@")
        a = asset_for(ctx, key)
        if a is None:
            ctx.find(f"{where}: {key} is not in the Bin, main stands in")
            ref.update(kind="main", asset="main", sync=True)
            return ref
        ref.update(kind="asset", asset=key)
        if (ctx.bin.get(key[4:]) or {}).get("timeline") == "main":
            ref.update(kind="own", sync=True)
        if inp:
            try:
                ref["t0"] = float(inp)
            except ValueError:
                pass
        if isinstance(p.get("t"), (int, float)):
            ref["t0"] = float(p["t"])
        return ref
    if s.startswith("%"):
        d = ctx.sets.get(s[1:].strip())
        if d and ("plate" in d or "page" in d):
            ref.update(kind="plate", plate=plate_of(ctx, d.get("plate") or d.get("page")))
            return ref
        ctx.find(f"{where}: {s} has no pics line, the film's plate stands in")
        ref.update(kind="plate", plate=plate_of(ctx, "plate"))
        return ref
    if s.startswith("?"):
        ctx.find(f"{where}: late want {s!r} has no picture yet, main stands in. Pick from the Bin")
        ref.update(kind="main", asset="main", sync=True)
        return ref
    if s in ctx.sets:
        of = ctx.sets[s].get("of") or []
        out = [parse_pic(ctx, x, where) for x in of]
        return [x for x in out if x]
    ctx.find(f"{where}: picture {s!r} unknown, main stands in")
    ref.update(kind="main", asset="main", sync=True)
    return ref


def parse_pics(ctx, v, where):
    if v is None:
        return []
    out = []
    for p in (v if isinstance(v, list) else [v]):
        r = parse_pic(ctx, p, where)
        if isinstance(r, list):
            out += r
        elif r:
            out.append(r)
    return out


def named_crop(ctx, ref, asset, t):
    """A crop as written: a rect, a zoom number, a name from the frame, or nothing."""
    c = ref.get("crop")
    if isinstance(c, (list, tuple)) and len(c) == 4 and all(isinstance(x, (int, float)) for x in c):
        return [float(x) for x in c]
    if isinstance(c, (int, float)) and not isinstance(c, bool) and c > 1:
        w = 1.0 / float(c)
        return [0.5 - w / 2, 0.5 - w / 2, 0.5 + w / 2, 0.5 + w / 2]
    if isinstance(c, str) and c:
        base = {"full": [0, 0, 1, 1], "upper": [0, 0, 1, 0.6], "lower": [0, 0.4, 1, 1], "left": [0, 0, 0.62, 1],
                "right": [0.38, 0, 1, 1], "centre": [0.2, 0.2, 0.8, 0.8], "center": [0.2, 0.2, 0.8, 0.8]}
        if c in base:
            return list(base[c])
        d = ctx.dna.get("crops") if isinstance(ctx.dna.get("crops"), dict) else {}
        s = d.get(c)
        if isinstance(s, (list, tuple)) and len(s) == 4:
            return [float(x) for x in s]
        if c in TRACK:
            f = face_at(ctx, t) if ref.get("kind") == "main" and TRACK[c][0] == "face" else None
            if f:
                k = TRACK[c][1]
                cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
                hw, hh = (f[2] - f[0]) * k / 2, (f[3] - f[1]) * k / 2
                return [cx - hw, cy - hh, cx + hw, cy + hh]
            ctx.find(f"crop {c!r}: no subject found there, the frame is used")
            return [0, 0, 1, 1]
        ctx.find(f"crop {c!r} is not defined: write [x0,y0,x1,y1] or put it in dna.crops")
    return [0, 0, 1, 1]


def face_at(ctx, t):
    """The largest face box nearest in time, in source fractions, or None."""
    if not ctx.faces:
        return None
    s = min(ctx.faces, key=lambda r: abs(r["t"] - t))
    if abs(s["t"] - t) > 2.0 or not s["boxes"]:
        return None
    return max(s["boxes"], key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))


TRACK = {"face": ("face", 3.4), "face.close": ("face", 2.2), "body": ("body", 1.25), "body.wide": ("body", 1.7)}


def subject_path(ctx, key, s0, s1, n):
    """Per-frame centre and height of the subject, from the perception samples, smoothed so
    the framing glides with him and never twitches. None without data."""
    rows = [(r["t"], r[key]) for r in (ctx.perc.get("samples") or []) if r.get(key)]
    rows = [r for r in rows if s0 - 1.0 <= r[0] <= s1 + 1.0]
    if not rows:
        return None
    out = []
    sig = 0.4
    for i in range(n):
        t = s0 + (s1 - s0) * (i + 0.5) / max(1, n)
        wsum = xs = ys = hs = x0s = x1s = 0.0
        for rt, b in rows:
            w = math.exp(-((rt - t) / sig) ** 2 / 2)
            wsum += w
            xs += w * (b[0] + b[2]) / 2
            ys += w * (b[1] + b[3]) / 2
            hs += w * (b[3] - b[1])
        if wsum < 1e-6:
            return None
        out.append((xs / wsum, ys / wsum, hs / wsum))
    return out


def tracked_crop(ctx, ref, asset, src, W, H, n):
    """A named subject crop follows the subject: one rect per frame, in the source's own
    bounds. A framing cut from a phone take has to move with the man in it."""
    name = ref.get("crop")
    if ref.get("kind") not in ("main", "own") or not isinstance(name, str) or name not in TRACK or not src:
        return None
    key, k = TRACK[name]
    path = subject_path(ctx, key, src[0], src[1], n)
    if not path:
        return None
    h = statistics.median(p[2] for p in path) * k
    target = (W / H) * (asset["h"] / asset["w"])
    w = h * target
    if w > 1.0:
        w, h = 1.0, 1.0 / target
    if h > 1.0:
        h, w = 1.0, target
    head = key == "face"
    r0 = limit_up(ctx, [0.5 - w / 2, 0.5 - h / 2, 0.5 + w / 2, 0.5 + h / 2], asset, W, H, ref.get("asset"))
    w, h = r0[2] - r0[0], r0[3] - r0[1]
    out = []
    for cx, cy, _ in path:
        cy2 = cy + (0.12 * h if head else 0.0)     # eyes above centre, as a framed head sits
        x = clamp(cx, w / 2, 1 - w / 2)
        y = clamp(cy2, h / 2, 1 - h / 2)
        out.append([x - w / 2, y - h / 2, x + w / 2, y + h / 2])
    if max(abs(a[0] - out[0][0]) + abs(a[1] - out[0][1]) for a in out) < 0.002:
        return out[0]
    return out


def luma_at(ctx, t):
    if not ctx.lumas:
        return None
    return min(ctx.lumas, key=lambda r: abs(r[0] - t))[1]


def parse_cam(c):
    if not c:
        return None
    if isinstance(c, dict):
        return {"m": str(c.get("m") or c.get("move") or ""), "v": c.get("v"), "dir": c.get("dir")}
    toks = str(c).split()
    nums, dirs = [], []
    for x in toks[1:]:
        try:
            nums.append(float(x))
        except ValueError:
            dirs.append(x)
    return {"m": toks[0], "v": nums[0] if nums else None, "dir": dirs[0] if dirs else None}


def zoom_curve(cam, n):
    """Per-frame zoom on the base crop, always at or above 1, so a move can't leave the source."""
    if not cam or n <= 1:
        return None
    m, v = cam["m"], cam.get("v")
    p = [i / (n - 1) for i in range(n)]
    if m == "push":
        v = 1.04 if v is None else v
        return [1 + (v - 1) * x for x in p]
    if m == "pull":
        v = 1.04 if v is None else v
        return [v - (v - 1) * x for x in p]
    if m == "settle":
        s = SETTLE["s_inf"] if v is None else v
        s = clamp(s, 0.5, 0.99)
        return [(s + (1 - s) * math.exp(-i / SETTLE["tau"])) / s for i in range(n)]
    if m == "ui_zoom":      # velocity peak near 22 %, ATLAS 2.6
        v = 1.25 if v is None else v
        return [1 + (v - 1) * (1 - (1 - x) ** 4.5) for x in p]
    return None


def camera(ctx, base, n, cam):
    """A camera move is a list of crops. A still shot is one crop. A base may already be a
    path (a tracked subject): the move rides on it."""
    cam = parse_cam(cam)
    path = base if isinstance(base[0], list) else None
    if not cam or cam["m"] == "hold":
        return base
    z = zoom_curve(cam, n)
    first = path[0] if path else base
    w0, h0 = first[2] - first[0], first[3] - first[1]
    cx, cy = (first[0] + first[2]) / 2, (first[1] + first[3]) / 2
    if cam["m"] in ("pan", "tilt"):
        v = 0.05 if cam.get("v") is None else cam["v"]
        d = {"l": (-1, 0), "left": (-1, 0), "r": (1, 0), "right": (1, 0), "u": (0, -1), "up": (0, -1), "d": (0, 1), "down": (0, 1)}.get(cam.get("dir") or ("left" if cam["m"] == "pan" else "up"), (0, -1))
        room = 1.0 + abs(v) * 2
        out = []
        for i in range(n):
            p = i / max(1, n - 1)
            w, h = w0 / room, h0 / room
            x = clamp(cx + d[0] * v * (p - 0.5) * w0, w / 2, 1 - w / 2)
            y = clamp(cy + d[1] * v * (p - 0.5) * h0, h / 2, 1 - h / 2)
            out.append([x - w / 2, y - h / 2, x + w / 2, y + h / 2])
        return out
    if z is None:
        if cam["m"]:
            ctx.find(f"camera {cam['m']!r} unknown: push pull settle ui_zoom pan tilt hold")
        return base
    if path:
        out = []
        for r, zz in zip(path, z):
            cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            out.append([cx - w0 / zz / 2, cy - h0 / zz / 2, cx + w0 / zz / 2, cy + h0 / zz / 2])
        return out
    return [[cx - w0 / zz / 2, cy - h0 / zz / 2, cx + w0 / zz / 2, cy + h0 / zz / 2] for zz in z]


def parse_treat(t):
    if not t:
        return None
    if isinstance(t, dict):
        return {k: v for k, v in t.items() if k in ("mode", "c", "th")} or None
    s = str(t)
    mode, _, arg = s.partition(":")
    out = {"mode": mode}
    tints = {"cyan": "#7FE3FF", "red": "#FF6A4D", "violet": "#C58CFF", "yellow": "#FFF27A"}
    if arg:
        if is_hex(arg):
            out["c"] = norm_hex(arg)
        elif arg in tints:
            out["c"] = tints[arg]
    return out


def base_rect(ctx, ref, asset, t, W, H, rng, repeat):
    """Crop for this appearance. A return to the same picture gets a new framing: in the
    references under 6% of shots return exactly, and one photo gives many framings."""
    r = named_crop(ctx, ref, asset, t)
    r = (fit_inside if written_rect(ctx, ref) else fit_crop)(r, asset["w"], asset["h"], W, H)
    z = ref.get("zoom")
    if isinstance(z, (int, float)) and not isinstance(z, bool) and z > 1:
        pass
    elif repeat and asset.get("kind") == "image":
        z = rng.uniform(1.12, 1.38)
    elif repeat and ref.get("kind") == "main" and not ref.get("crop") and not ctx.governed:
        z = (1.0, 1.28, 1.14, 1.42)[repeat % 4]
    else:
        z = None
    if z and z > 1:
        w, h = (r[2] - r[0]) / z, (r[3] - r[1]) / z
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        if repeat and asset.get("kind") == "image":
            cx += rng.uniform(-0.3, 0.3) * ((r[2] - r[0]) - w)
            cy += rng.uniform(-0.3, 0.3) * ((r[3] - r[1]) - h)
        r = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
    return limit_up(ctx, r, asset, W, H, ref.get("asset"))


def limit_up(ctx, r, asset, W, H, name):
    """A crop past 6x of the source is mush, whatever was asked. It backs off and says so."""
    upscale = W / max(1.0, (r[2] - r[0]) * asset["w"])
    if upscale > ctx.max_up:
        ctx.find(f"crop on {name} asked {upscale:.0f}x, held at {ctx.max_up:.0f}x: this source is {asset['w']} wide")
        k = upscale / ctx.max_up
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        w, h = min(1.0, (r[2] - r[0]) * k), min(1.0, (r[3] - r[1]) * k)
        cx, cy = clamp(cx, w / 2, 1 - w / 2), clamp(cy, h / 2, 1 - h / 2)
        r = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
        upscale = ctx.max_up
    if upscale > ctx.soft_at:
        ctx.soft[name] = max(ctx.soft.get(name, 0), round(upscale, 1))
    return r


def grade_of(ctx, b, ref):
    look = {}
    if b is not None:
        g = parse_look(ctx, b.raw.get("look"), b.id)
        look.update(g or {})
    if ref:
        g = parse_look(ctx, ref.get("look"), b.id if b else "pic")
        look.update(g or {})
    return {**ctx.base_grade, **look} if look else None


def take_light(ctx):
    """(mean luma, warmth) of the take, read once from its small frames and its measured palette."""
    if not hasattr(ctx, "_take"):
        arr = thumbs(ctx, "main")
        l = float(arr.mean()) if arr is not None and len(arr) else None
        warm = 0.0
        pal = ctx.perc.get("palette") or []
        if pal:
            tot = sum(p["share"] for p in pal) or 1.0
            warm = sum(p["share"] * (hex_rgb(p["hex"])[0] - hex_rgb(p["hex"])[2]) for p in pal) / tot
        ctx._take = (l, warm)
    return ctx._take


def match_stock(ctx, grade, asset_id, t):
    """One stock across all sources (FT1). A found picture is pulled toward the take's light and warmth by dna.match, 0 to 1:
    a daylight clip goes day-for-night, and the film stays one world. Measured from the pictures, no meaning read."""
    k = ctx.dna.get("match")
    if not isinstance(k, (int, float)) or isinstance(k, bool) or k <= 0 or not str(asset_id).startswith(("bin:", "tray:")):
        return grade
    k = min(float(k), 1.0)
    target, warm = take_light(ctx)
    arr = thumbs(ctx, asset_id)
    if target is None or arr is None or not len(arr):
        return grade
    q = arr[min(max(int(round(t * THUMB_FPS)), 0), len(arr) - 1)]
    l = max(float(q.mean()), 0.01)
    aim = l ** (1 - k) * max(target * 2.2, 0.15) ** k       # above the take: a found picture still has to read as a picture
    g = dict(grade or ctx.base_grade)
    g["gain"] = g.get("gain", 1.0) * clamp(aim / l, 0.2, 1.4)
    g["sat"] = g.get("sat", 1.0) * (1 - 0.38 * k * clamp(l / max(aim, 0.01) / 3, 0, 1))
    g["temp"] = g.get("temp", 0.0) + clamp(warm * 0.35, -0.05, 0.05) * k
    return g


def picture_fields(ctx, b, ref, a, z, W=None, H=None, src=None):
    """The fields a shot or card shares, for one linear stretch of picture."""
    W, H = W or ctx.W, H or ctx.H
    n = z - a
    out = {"pic": ref.get("asset") or "plate"}
    if ref["kind"] == "plate":
        out["pic"] = "plate"
        out["plate"] = ref["plate"]
    else:
        asset = asset_for(ctx, ref["asset"])
        rng = rng_for(ctx.seed, "pic", b.id if b else "", a, ref["asset"])
        key = ref["asset"]
        repeat = ctx.seen.get(key, 0)
        ctx.seen[key] = repeat + 1
        if asset["kind"] == "video":
            if src is None:
                src = video_src(ctx, ref, asset, n, rng)
            out["src"] = [src[0], src[1]]
        t = out["src"][0] if "src" in out else 0.0
        if ref.get("_view") is not None and ref["asset"] == "main":
            vr, cam = view_rect(ctx, ctx.views[ref["_view"]], asset, W, H), calm_cam(ref.get("cam") or "push 1.04")
            if cuts_wide_print(ctx, vr, out.get("src"), asset, W, H):
                # the detail sits under a line the upload prints across its frame: closer would cut that line at both
                # ends (p5572, 13 Nov, the last look took the film back for it). That moment is shown whole instead
                vr, cam = fit_crop([0, 0, 1, 1], asset["w"], asset["h"], W, H), None
            out["crop"] = camera(ctx, vr, n, cam)
            out["view"] = ref["_view"]
        elif ctx.governed and ref["asset"] == "main" and (W, H) == (ctx.W, ctx.H):
            # the upload on the frame: one framing for the film, a step only where a device line earned one
            base = take_crop(ctx, ref, asset, out.get("src"), n, rng, at=a)
            if ref.get("_cam"):            # where it ends is where the take stays
                out["crop"] = keep_marks_whole(ctx, camera(ctx, base, n, calm_cam(ref.get("cam"))), out.get("src"), asset, W, H)
                c = out["crop"]            # what the frame really shows: the framing the take stays in is this one
                c = c[-1] if isinstance(c[0], list) else c
                flip = ref.get("_flip") or 1.0
                cx, cy, hw, hh = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2, (c[2] - c[0]) * flip / 2, (c[3] - c[1]) * flip / 2
                ctx.cam_marks.append((a, [cx - hw, cy - hh, cx + hw, cy + hh], t))
                ctx.cam_marks.sort(key=lambda m: m[0])
            else:
                out["crop"] = settled_take(ctx, ref, asset, out.get("src"), n, rng, a, base)
        elif ctx.governed and asset["kind"] == "image" and ref.get("kind") == "asset" and str(ref["asset"]).startswith("bin:") \
                and (W, H) == (ctx.W, ctx.H) and not ref.get("cam") and not isinstance(ref.get("crop"), str) and _pace_on():
            # a found still on the frame (score.pace): framed on its subject, heads whole, and never standing still
            from . import pace
            wr = None
            if ref.get("crop") is not None or (isinstance(ref.get("zoom"), (int, float)) and not isinstance(ref.get("zoom"), bool) and ref["zoom"] > 1):
                wr = base_rect(ctx, ref, asset, t, W, H, rng, 0)
            out["crop"] = pace.still_path(ctx, ref["asset"], asset, W, H, n, rng, part=int(ref.get("_part") or 0) or min(repeat, 2), written=wr)
        else:
            base = tracked_crop(ctx, ref, asset, out.get("src"), W, H, n) or base_rect(ctx, ref, asset, t, W, H, rng, repeat)
            cam = ref.get("cam") or ctx.dna.get("cam")
            out["crop"] = camera(ctx, base, n, calm_cam(cam) if ctx.governed and asset["kind"] == "image" else cam)
        if ref["asset"] == "main" and (W, H) == (ctx.W, ctx.H) and not ctx.governed and ref.get("_view") is None:        # the frame's own picture, not a card's inset
            out["crop"] = keep_marks_whole(ctx, out["crop"], out.get("src"), asset, W, H)
    tr = parse_treat(ref.get("treat"))
    if tr:
        out["treat"] = tr
    g = match_stock(ctx, grade_of(ctx, b, ref), out.get("pic"), (out.get("src") or [0.0])[0])
    if g:
        out["grade"] = g
    if isinstance(ref.get("step"), int) and ref["step"] > 1:
        out["step"] = ref["step"]
    return out


def calm_cam(cam):
    """A slow move on a take or a still stays slow: 6% in all (TASTE 4.2)."""
    from . import taste
    c = parse_cam(cam)
    if not c:
        return None
    v = c.get("v")
    if c["m"] in ("push", "pull", "ui_zoom"):
        c["v"] = min(1.04 if v is None else float(v), taste.PUSH_MAX)
    elif c["m"] == "settle":
        c["v"] = max(SETTLE["s_inf"] if v is None else float(v), 1 / taste.PUSH_MAX)
    elif c["m"] in ("pan", "tilt"):
        c["v"] = min(0.05 if v is None else abs(float(v)), 0.05)
    return c


def film_framing(ctx, asset, src, n):
    """Where the film's window sits on the upload: dna.take (a rect, or a subject that is followed), else the middle
    of the upload as it was shot. Only its centre is used: how close the frame is belongs to the camera."""
    t = ctx.dna.get("take")
    ref = {"kind": "main", "asset": "main"}
    if t not in (None, "", "full") and not isinstance(t, (int, float)):
        ref["crop"] = t
    r = tracked_crop(ctx, ref, asset, src, ctx.W, ctx.H, n) if ref.get("crop") else None
    return r or base_rect(ctx, ref, asset, (src or [0.0])[0], ctx.W, ctx.H, None, 0)


def take_crop(ctx, ref, asset, src, n, rng, at=0):
    """The upload on the frame, governed (TASTE 4.2, camera). Its scale is the film's one framing; a shot leaves it
    only when its device line earned a step (`_cam`), by 15% at most and 20% once, and by a tenth where the voice
    was cut (`_flip`), aimed on the face. An earned framing then holds: the take does not step back out of it, so
    one device is one step, never an A-B-A. Where the Score wrote a crop without earning it, the window still
    centres where the Score put it: where the frame sits is a reframe, how close it is is a camera move. Nothing
    here zooms past what the source can hold, or until the face fills the frame."""
    from . import taste
    W, H = ctx.W, ctx.H
    fit = fit_crop([0, 0, 1, 1], asset["w"], asset["h"], W, H)
    fw, fh = fit[2] - fit[0], fit[3] - fit[1]
    t = (src or [0.0])[0]
    face = face_at(ctx, t)
    base_up = W / max(1.0, fw * asset["w"])
    cap = max(1.0, min(taste.ZOOM_OVER_UPSCALE, taste.UPSCALE_MAX / base_up))
    if face:
        cap = max(1.0, min(cap, taste.FACE_H_MAX / max((face[3] - face[1]) / max(fh, 1e-6), 1e-6)))
    film = film_framing(ctx, asset, src, n)
    zf = 1.0          # the film's framing is where the window sits on the upload, never how close: it does not zoom
    written = None
    if ref.get("crop") or (isinstance(ref.get("zoom"), (int, float)) and not isinstance(ref.get("zoom"), bool) and ref["zoom"] > 1):
        written = tracked_crop(ctx, ref, asset, src, W, H, n) or base_rect(ctx, ref, asset, t, W, H, rng, 0)
    rects = written if written is not None else film
    path = rects if isinstance(rects[0], list) else [rects]
    held = held_framing(ctx, at, t)       # the last framing a device earned before this shot, while the take ran on
    z, centre = zf, None
    blind = face is None and not (ctx.devices.get(ref.get("_cam")) or {}).get("bound")
    if ref.get("_cam") and written is not None and blind:
        # P9: a closer frame must hold the head whole, and no head was found in this shot to hold. The step is not
        # made blind (p5584: 12% on a man whose face the index missed cut the top of his head).
        ctx.taste_rows.append({"id": ref["_cam"], "did": "repaired", "dept": "camera", "why": "no face found in this shot to hold whole: the frame stays as shot"})
    elif ref.get("_cam") and written is not None:
        zw = fw / max(path[0][2] - path[0][0], 1e-6)
        lim = taste.STEP_MAX
        if not ctx.step_once and (zw / zf > lim or zf / max(zw, 1e-6) > lim):
            lim, ctx.step_once = taste.STEP_ONCE, True
        z = clamp(zf * clamp(zw / zf, 1 / lim, lim), 1.0, cap)
    elif held is not None:
        z = clamp(fw / max(held[2] - held[0], 1e-6), 1.0, cap)
        if written is None:
            centre = ((held[0] + held[2]) / 2, (held[1] + held[3]) / 2)
    flip = ref.get("_flip") if cap >= taste.EDIT_STEP[0] and face is not None else None
    if flip:
        z = min(z * flip, cap * flip)
    w, h = fw / z, fh / z
    out = []
    for r in path:
        cx, cy = centre or ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
        if z > zf and face:         # a closer frame keeps its centre, and moves only as far as holds the head whole (P9)
            hd = head_of(face)
            if hd[2] - hd[0] <= w:
                cx = clamp(cx, hd[2] - w / 2, hd[0] + w / 2)
            if hd[3] - hd[1] <= h:
                cy = clamp(cy, hd[3] - h / 2, hd[1] + h / 2)
        cx, cy = clamp(cx, w / 2, 1 - w / 2), clamp(cy, h / 2, 1 - h / 2)
        out.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
    if len(out) == 1 or max(abs(a[0] - out[0][0]) + abs(a[1] - out[0][1]) for a in out) < 0.002:
        return out[0]
    return out


def take_carries(ctx, f0, s0, f1, s1):
    """Does the take run on from programme frame f0 (source second s0) to frame f1 (source second s1)? One shot of
    the upload, and on every stretch of the spine between the two the source only moves forward. Read from the
    spine alone, like a speech edit: never from the order shots were planned in."""
    from . import taste
    if taste._kshot(ctx, s0) != taste._kshot(ctx, s1) or s1 < s0 - 0.1:
        return False
    sp = ctx.spine
    i0 = next((i for i, s in enumerate(sp) if s["f"][0] <= f0 < s["f"][1]), None)
    i1 = next((i for i, s in enumerate(sp) if s["f"][0] <= f1 < s["f"][1]), None)
    if i0 is None or i1 is None:
        return True
    for i in range(i0 + 1, i1 + 1):
        p, q = sp[i - 1], sp[i]
        if q["src"][0] < p["src"][1] - 0.1 or taste._kshot(ctx, p["src"][1] - 0.02) != taste._kshot(ctx, q["src"][0]):
            return False
    return True


def held_framing(ctx, at, t):
    """The framing an earned step left the take in, for a shot that starts at programme frame `at` and source second
    `t`: the last such step before it, while the take carries on from there. A cut back to an earlier moment, or to
    another shot of the upload, is a new picture and starts from the film's own framing (p5584: a 12% step on the
    cold open stayed on the whole walk that followed and cut the man at the edge of the frame)."""
    held = None
    for f, r, s0 in ctx.cam_marks:
        if f <= at and take_carries(ctx, f, s0, at, t):
            held = r
    return held


def settled_take(ctx, ref, asset, src, n, rng, a, crop):
    """A closer framing of the take (the tenth at a speech edit, or the framing an earned step left it in) is one
    framing for its whole run: solved on the run's first shot and kept. The heads a crop must hold whole are
    measured shot by shot, and nudged the frame at every beat (p5591: 9%, 8%, 6%, 5%, 11% on five shots of one
    run). When holding them whole would widen the closer frame, the run stays as shot: a step that can't be made
    cleanly is not made."""
    W, H = ctx.W, ctx.H
    plain = keep_marks_whole(ctx, crop, src, asset, W, H)
    fit = fit_crop([0, 0, 1, 1], asset["w"], asset["h"], W, H)
    key = (str(held_framing(ctx, a, (src or [0.0])[0])), bisect.bisect_right(ctx.flip_frames or [], a))
    if isinstance(crop[0], list) or crop[2] - crop[0] >= fit[2] - fit[0] - 1e-4:
        return plain
    if ref.get("crop"):            # a window the Score placed on the upload sits where it was put, shot by shot
        if ref.get("_flip"):
            ctx.flip_runs.setdefault(key[1], True)
        return plain
    if key not in ctx.run_crops:
        held = [round(v, 5) for v in plain] == [round(v, 5) for v in crop]
        if not held and ref.get("_flip"):
            crop = take_crop(ctx, {**ref, "_flip": None}, asset, src, n, rng, at=a)
            closer = crop[2] - crop[0] < fit[2] - fit[0] - 1e-4
            ctx.run_crops[key] = keep_marks_whole(ctx, crop, src, asset, W, H) if closer else None
        else:
            ctx.run_crops[key] = plain
        ctx.flip_runs[key[1]] = bool(ref.get("_flip")) and held
    kept = ctx.run_crops[key]
    if kept is None:           # this run is the upload as shot
        return keep_marks_whole(ctx, take_crop(ctx, {**ref, "_flip": None}, asset, src, n, rng, at=a), src, asset, W, H)
    return list(kept)


def head_of(f):
    """A face box grown to the head it belongs to: hair above, ears at the sides, the chin below."""
    w, h = f[2] - f[0], f[3] - f[1]
    return [f[0] - 0.25 * w, f[1] - 0.6 * h, f[2] + 0.25 * w, f[3] + 0.15 * h]


def main_marks(ctx, s0, s1, words=True, aspect=1.0):
    """What a crop of the upload must never cut through between two source seconds: heads, and when
    the upload's shape is not the frame's, the printed words that stay in it (perception's text
    clusters), in source fractions. A same-shape upload's punch-in leaving a sign out is a choice."""
    out = [list(x["box"]) for x in ctx.perc.get("text") or [] if x.get("box") and x["t"][0] - 0.5 <= s1 and x["t"][1] + 0.5 >= s0] if words else []
    # A text box from the index hugs the strokes it found: a script title's first and last letters lie
    # past it (p5363: 'La Parka' read as 'a Park'). Each side gains the line's own height, a little above and below.
    out = [[b[0] - (b[3] - b[1]) * aspect, b[1] - 0.15 * (b[3] - b[1]), b[2] + (b[3] - b[1]) * aspect, b[3] + 0.15 * (b[3] - b[1])] for b in out]
    for r in ctx.faces or []:
        if s0 - 0.75 <= r["t"] <= s1 + 0.75:
            out += [head_of(f) for f in r["boxes"] if (f[2] - f[0]) * (f[3] - f[1]) > 0.0004]
    return out


def _cuts(c, m):
    inside = c[0] - 1e-3 <= m[0] and c[1] - 1e-3 <= m[1] and m[2] <= c[2] + 1e-3 and m[3] <= c[3] + 1e-3
    return rects_touch(c, m) and not inside


def hold_whole(rect, marks, target):
    """The smallest frame-shaped rect round `rect` that cuts through none of `marks`. It may pass the
    source's edges: the lowering then fits the picture whole on a blurred copy of itself."""
    c = list(rect)
    for _ in range(4):
        cut = [m for m in marks if _cuts(c, m)]
        if not cut:
            return c
        u = [min([c[0]] + [m[0] for m in cut]), min([c[1]] + [m[1] for m in cut]), max([c[2]] + [m[2] for m in cut]), max([c[3]] + [m[3] for m in cut])]
        u = [max(u[0], -0.0), max(u[1], 0.0), min(u[2], 1.0), min(u[3], 1.0)]
        w, h = u[2] - u[0], u[3] - u[1]
        if w / max(h, 1e-6) < target:
            w = h * target
        else:
            h = w / target
        cx, cy = (u[0] + u[2]) / 2, (u[1] + u[3]) / 2
        cx = clamp(cx, w / 2, 1 - w / 2) if w <= 1 else 0.5
        cy = clamp(cy, h / 2, 1 - h / 2) if h <= 1 else 0.5
        c = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
    return c


FILL_MIN_H = 0.55       # a landscape upload in a portrait frame covers at least this much of its height


def fill_floor(ctx, c, target):
    """p5565: a 16:9 video call in 9:16 held every printed word whole, so it sat at 46 % of the frame's
    height on a dark blurred band. A frame-shaped crop that passes the source's top and bottom shows the
    upload at 1/h of the frame's height; past the floor it narrows about its centre, so the picture is
    larger and the blurred fill stays above and below. Side columns are what it gives up."""
    if target >= 0.75 or c[3] - c[1] <= 1.0 / FILL_MIN_H + 1e-6:
        return c
    w = target / FILL_MIN_H
    cx = clamp((c[0] + c[2]) / 2, w / 2, 1 - w / 2)
    h = w / target
    ctx.filled = getattr(ctx, "filled", 0) + 1
    return [cx - w / 2, 0.5 - h / 2, cx + w / 2, 0.5 + h / 2]


def keep_marks_whole(ctx, crop, src, asset, W, H):
    """p5363: the 9:16 crop of a 4:3 cartoon cut its printed title to 'a Parka' and the drawn characters
    off at the bottom. A crop of the upload holds every printed word and head it touches whole."""
    if not crop or not src:
        return crop
    sw, sh = float(asset.get("w") or 0), float(asset.get("h") or 0)
    if sw <= 0 or sh <= 0:
        return crop
    target = (W / H) * (sh / sw)
    marks = main_marks(ctx, src[0], src[1], words=abs(target - 1.0) > 0.02, aspect=sh / sw)
    many = isinstance(crop[0], (list, tuple))
    if not marks:
        return [fill_floor(ctx, list(c), target) for c in crop] if many else fill_floor(ctx, list(crop), target)
    if many:
        held = [hold_whole(c, marks, target) for c in crop]
        if held != [list(c) for c in crop]:
            ctx.held_whole = getattr(ctx, "held_whole", 0) + 1
        return [fill_floor(ctx, c, target) for c in held]
    held = hold_whole(crop, marks, target)
    if held != list(crop):
        ctx.held_whole = getattr(ctx, "held_whole", 0) + 1
    return fill_floor(ctx, held, target)


def video_src(ctx, ref, asset, n, rng):
    """A linear stretch of a clip that doesn't follow the voice."""
    need = n / ctx.fps
    dur = max(asset.get("dur") or need, 1e-3)
    if ref.get("t0") is not None:
        key = (ref["asset"], "t", float(ref["t0"]))
        s = ctx.cursor.get(key, float(ref["t0"]))
    elif ref.get("k") is not None and ref["k"] in ctx.kshots:
        key = (ref["asset"], "k", ref["k"])
        s = ctx.cursor.get(key, ctx.kshots[ref["k"]][0])
    else:
        key = ref["asset"]
        s = ctx.cursor.get(key)
        if s is None:
            s = rng.uniform(0, max(0.0, min(dur - need, dur * 0.25)))
    if s + need > dur:
        s = max(0.0, dur - need)
        if ref.get("t0") is None and ref.get("k") is None:
            s = rng.uniform(0, max(0.0, dur - need))
    rate = ref.get("rate")
    rate = float(rate) if isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate >= 0 else 1.0
    e = s + need * rate
    ctx.cursor[key] = e
    if ref.get("t1") is not None and e > ref["t1"]:
        e = float(ref["t1"])
    return [s, min(e, dur)]


def emit_ref(ctx, b, ref, a, z, sid):
    """One planned shot to L1 shots. A shot of the voice crosses a jump in the spine as a
    real jump cut, so the picture stays in lip sync."""
    if z <= a:
        return []
    if ref.get("sync") and ref.get("k") is None:
        parts = follow_spine(ctx, a, z)
        out = []
        for i, (p, q, s0, s1) in enumerate(parts):
            it = {"id": sid if i == 0 else f"{sid}.j{i}", "k": "shot", "f": [p, q]}
            if ctx.governed and ref.get("asset") == "main":
                ref = {**ref, "_flip": speech_edit_flip(ctx, p)}
                if i and ref.get("_cam"):
                    # a step is made once, where its shot starts. Past a jump of the spine the shot is either the
                    # take running on, which stays in the framing the step left (held_framing), or another moment
                    # of the upload, which is a new picture and starts from the film's own framing
                    ref = {**ref, "_cam": None}
            it.update(picture_fields(ctx, b, ref, p, q, src=[s0, s1]))
            if i:
                ctx.seen[ref["asset"]] -= 1      # same framing either side of the jump
            out.append(it)
        return out
    if ref["kind"] == "main" and ref.get("k") is not None:
        parts = follow_spine(ctx, a, z)
        k = ctx.kshots.get(ref["k"])
        if k and parts and all(k[0] - 1e-3 <= p[2] and p[3] <= k[1] + 1e-3 for p in parts):
            ref = {**ref, "k": None}
            return emit_ref(ctx, b, ref, a, z, sid)
    if framed_still(ctx, ref) and z - a >= 9:
        return emit_framed(ctx, b, ref, a, z, sid)
    it = {"id": sid, "k": "shot", "f": [a, z]}
    it.update(picture_fields(ctx, b, ref, a, z))
    return [it]


FRAMED_LOSS = 2.0          # a still this many times wider than the frame's shape shows under half of itself in a cover crop
FRAMED_W, FRAMED_Y = 0.9, 0.42


def _pace_on():
    from . import pace
    return pace.on()


def framed_rect(ctx, f):
    """Where a framed still rests at this frame, settled, in frame fractions, or None."""
    for c in ctx.items:
        if c.get("k") == "card" and c.get("framed") and c["f"][0] <= f < c["f"][1]:
            k = _last(c.get("s", 1)) or 1
            cw, ch = float(c["w"]) * k, float(c["h"]) * k
            return [_last(c["x"]) - cw / 2, _last(c["y"]) - ch / 2, _last(c["x"]) + cw / 2, _last(c["y"]) + ch / 2]
    return None


def framed_still(ctx, ref):
    """A wide photograph cut into a vertical film. A cover crop shows a third of it: Piastri's McLaren came out as a
    wheel under the letters 'einek' of a trackside sign (p5591, 13 Nov). Unless the Score wrote a crop, such a still
    is shown whole: framed on the film's plate, the way the folder sets its archive pictures."""
    if not ctx.governed or ctx.H <= ctx.W or ref.get("kind") != "asset" or ref.get("crop") is not None:
        return False
    if _pace_on() and str(ref.get("asset")).startswith("bin:"):
        return False           # round six: it fills the frame on its subject and the camera travels it (score.pace.still_path)
    z = ref.get("zoom")
    if isinstance(z, (int, float)) and not isinstance(z, bool) and z > 1:
        return False
    asset = asset_for(ctx, ref["asset"])
    if not asset or asset.get("kind") != "image" or not asset.get("w") or not asset.get("h"):
        return False
    return (asset["w"] / asset["h"]) / (ctx.W / ctx.H) >= FRAMED_LOSS


def emit_framed(ctx, b, ref, a, z, sid):
    """The plate takes the frame and the still stands on it whole, settling. The shot keeps its id, so a line that
    answers it still finds it; the picture is `<id>.pic`."""
    asset = asset_for(ctx, ref["asset"])
    dark = ctx.dna.get("plate")
    dark = norm_hex(dark) if is_hex(dark) and luma(norm_hex(dark)) < 0.2 else "#0B0B0D"
    plate = _plate(dark, None, 0)
    bg = {"id": sid, "k": "shot", "f": [a, z], "pic": "plate", "plate": plate, "framed": ref["asset"]}
    w = FRAMED_W
    h = w * (ctx.W / ctx.H) * (asset["h"] / asset["w"])
    make_card(ctx, b, dict(ref, cam=None), a, z, {"w": w, "h": h, "x": 0.5, "y": FRAMED_Y, "in": "settle", "s_inf": 0.93, "border": 0, "shadow": 0, "radius": 0.012, "cap": "free"},
              f"{sid}.pic", z_order=1)
    card = ctx.byid.get(f"{sid}.pic")
    if card is not None:
        card["framed"] = True
    return [bg]


def speech_edit_flip(ctx, p):
    """Where the voice was cut inside the take, the framing changes by a tenth, so the jump reads as a cut
    (TASTE 4.2: one step per speech edit, 8-12%). Never twice inside ten seconds: a row of closed pauses is a
    row of plain jump cuts, not a zoom on every breath. Read from the spine alone, so a shot's framing
    depends on where it starts and never on the order gestures ran in. Returns the zoom at frame p, or None."""
    from . import taste
    if ctx.flip_frames is None:
        out, last = [], -10 ** 6
        gap = int(taste.STEP_GAP_S * ctx.fps)
        earned = [int(v[0]) for v in (ctx.plan.get("cam_at") or {}).values()]      # a step a line earned outranks a tenth
        for f in taste.speech_edits(ctx):
            if f - last >= gap and all(abs(f - e) >= gap for e in earned):
                out.append(f)
                last = f
        ctx.flip_frames = out
    if bisect.bisect_right(ctx.flip_frames, p) % 2 == 0:
        return None
    lo, hi = taste.EDIT_STEP
    return round(rng_for(ctx.seed, "edit_step").uniform(lo, hi), 3)


def sub_item(it, a, z, new_id):
    """A stretch of an item, with its baked lists and source time cut to match."""
    n0, n1 = it["f"]
    n = n1 - n0
    out = dict(it)
    out["id"], out["f"] = new_id, [a, z]
    for key in ("crop", "rect", "op", "s", "x", "y", "rot", "a", "th", "blur"):
        v = out.get(key)
        if isinstance(v, list) and len(v) == n and (key not in ("crop", "rect") or isinstance(v[0], list)):
            out[key] = v[a - n0:z - n0]
    src = out.get("src")
    if src and n:
        rate = (src[1] - src[0]) / n
        out["src"] = [src[0] + (a - n0) * rate, src[0] + (z - n0) * rate]
    return out


def carve(ctx, a, z, new):
    """New shots take frames [a,z) of the base. Whatever stood there keeps the rest."""
    keep = []
    for s in ctx.shots:
        p, q = s["f"]
        if q <= a or p >= z:
            keep.append(s)
            continue
        if p < a:
            keep.append(sub_item(s, p, a, s["id"]))
        if q > z:
            keep.append(sub_item(s, z, q, (s["id"] if p >= a else s["id"] + ".r")))
    ctx.shots = sorted(keep + new, key=lambda s: s["f"][0])


def plan_cut(b, ctx):
    """The cut stave: pulse, lead, or explicit frame lengths."""
    c = b.raw.get("cut")
    out = {"pulse": None, "lead": None, "frames": None}
    if c is None and isinstance(ctx.film.get("pulse"), (int, float)):
        out["pulse"] = float(ctx.film["pulse"])
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        out["pulse"] = float(c)
    elif isinstance(c, list):
        fr = [int(x) for x in c if isinstance(x, (int, float)) and x > 0]
        out["frames"] = fr or None
    elif isinstance(c, str):
        toks = c.split()
        i = 0
        fr = []
        while i < len(toks):
            t = toks[i]
            if t == "pulse":
                if i + 1 < len(toks):
                    try:
                        out["pulse"] = float(toks[i + 1])
                        i += 1
                    except ValueError:
                        out["pulse"] = ctx.band["med"]
                else:
                    out["pulse"] = ctx.band["med"]
            elif t == "lead" and i + 1 < len(toks):
                try:
                    out["lead"] = int(toks[i + 1].rstrip("f"))
                    i += 1
                except ValueError:
                    pass
            elif t.isdigit():
                fr.append(int(t))
            i += 1
        if fr:
            out["frames"] = fr
    return out


def pulse_lengths(ctx, total, med, rng):
    """Shot lengths in frames, drawn from the style's own spread around this beat's median."""
    b = ctx.band
    sigma = max(0.15, (math.log(max(b["p90"], 1e-3)) - math.log(max(b["p10"], 1e-3))) / 2.563)
    sigma = min(sigma, 1.0)
    lo = max(2, int(round(0.7 * min(b["p10"], med) * ctx.fps)))
    hi = max(lo + 1, int(round(1.3 * max(b["p90"], med * 1.6) * ctx.fps)))
    out, left = [], total
    while left > 0:
        d = int(round(math.exp(rng.gauss(math.log(max(med, 0.04)), sigma)) * ctx.fps))
        d = int(clamp(d, lo, hi))
        if left - d < max(3, lo):
            d = left
        out.append(d)
        left -= d
    return out


def jitter_split(total, n, rng):
    if n <= 1:
        return [total]
    w = [rng.uniform(0.75, 1.25) for _ in range(n)]
    s = sum(w)
    cuts = [int(total * sum(w[:i + 1]) / s) for i in range(n - 1)]
    edges = [0] + cuts + [total]
    return [max(1, y - x) for x, y in zip(edges, edges[1:])]


FILL_F = 900        # a written length this long means "what is left": no beat is 30 s of one picture by a number


def explicit_lengths(total, frames):
    """Written lengths, the last picture takes what's left. A length of 900 frames or more is a fill: it takes what
    the written lengths leave, shared when there are several. Read as a plain length, the first 999 swallowed the beat
    and every picture written after it never reached the screen ("cut":[60,999,45,999] showed two of its four
    pictures; "cut":[999,30] never showed its closing page)."""
    frames = [int(f) for f in frames]
    fills = [i for i, f in enumerate(frames) if f >= FILL_F]
    if fills and len(frames) > 1:
        fixed = sum(f for f in frames if f < FILL_F)
        room = total - fixed
        if room >= 6 * len(fills):
            each = room // len(fills)
            out = [each if f >= FILL_F else f for f in frames]
            out[fills[-1]] += total - sum(out)
            return out
        frames = [f for f in frames if f < FILL_F]       # no room for a fill: the written lengths stand
    out, left = [], total
    for f in frames:
        if left <= 0:
            break
        d = min(int(f), left)
        out.append(d)
        left -= d
    if left > 0:
        if out:
            out[-1] += left
        else:
            out = [left]
    return out


def plan_shots(ctx):
    """Picture windows on their own clock, then shot lengths inside each."""
    beats = ctx.beats
    starts = []
    for i, b in enumerate(beats):
        b.cut = plan_cut(b, ctx)
        lead = b.cut["lead"]
        if lead is None:
            r = rng_for(ctx.seed, "lead", b.id)
            lead = r.choice([-4, -3, -2, 2, 3, 4, 5, 6, 7])
        b.lead = lead
        starts.append(0 if i == 0 else b.f0 - lead)
    for i in range(1, len(starts)):
        lo = starts[i - 1] + 2
        hi = beats[i].f1 - 2 if beats[i].f1 - 2 > lo else lo
        starts[i] = int(clamp(starts[i], lo, hi))
    for i, b in enumerate(beats):
        b.pf0 = starts[i]
        b.pf1 = starts[i + 1] if i + 1 < len(beats) else ctx.dur_f
    for b in beats:
        where = b.id
        b.pics = parse_pics(ctx, b.raw.get("pic"), where) or [{"kind": "main", "asset": "main", "sync": True}]
        if ctx.governed:
            camera_gate(ctx, b)
        total = b.pf1 - b.pf0
        if total <= 0:
            continue
        rng = rng_for(ctx.seed, "pulse", b.id)
        fr = [p.get("d") for p in b.pics]
        take_only = ctx.governed and all(p.get("asset") == "main" and p.get("sync") and p.get("k") is None for p in b.pics)
        if take_only and not any(p.get("_cam") for p in b.pics):
            lens = [total]         # a pulse cuts between pictures. One take has none: it holds
        elif take_only:            # each earned framing once, never a pulse back and forth
            want = b.cut["frames"] or [int(x) if isinstance(x, (int, float)) else max(1, total // len(b.pics)) for x in fr]
            lens = explicit_lengths(total, want[:len(b.pics)])
        elif b.cut["frames"]:
            lens = explicit_lengths(total, b.cut["frames"])
        elif any(isinstance(x, (int, float)) for x in fr):
            spent = sum(int(x) for x in fr if isinstance(x, (int, float)))
            fill = sum(1 for x in fr if not isinstance(x, (int, float)))
            each = max(1, (total - spent) // max(1, fill))
            lens = explicit_lengths(total, [int(x) if isinstance(x, (int, float)) else each for x in fr])
        elif b.cut["pulse"]:
            lens = pulse_lengths(ctx, total, b.cut["pulse"], rng)
        else:
            lens = jitter_split(total, len(b.pics), rng)
        if ctx.governed:
            lens = on_the_breath(ctx, b, lens)
        t = b.pf0
        from . import pace
        for j, d0 in enumerate(lens):
            ref0 = b.pics[j % len(b.pics)]
            sid0 = f"{b.id}.s{j}"
            if ctx.governed and ref0.get("asset") != "main" and ref0.get("kind") != "plate":
                # the last look, or the floor: the take stands in. A picture cut into parts goes whole when any part was named
                gone = _gone(ctx, sid0, "picture", None) or next((_gone(ctx, k, "picture", None) for k in list(ctx.cuts) if pace.parent(k) == sid0 and k != sid0), None)
                if gone:
                    ctx.taste_rows.append({"id": sid0, "did": gone[0], "why": gone[1], "dept": "picture"})
                    ref0 = {"kind": "main", "asset": "main", "sync": True}
            # the judge's pace (score.pace): a found picture held about a second or more is cut inside itself
            cut = pace.parts(ctx, b, sid0, ref0, d0, rng_for(ctx.seed, "parts", sid0), at=t) if ctx.governed else [(sid0, ref0, d0)]
            for sid, ref, d in cut:
                its = emit_ref(ctx, b, ref, t, t + d, sid)
                if ref.get("_punch") is not None:           # a framing of the take cut inside a wordless stretch (score.pace, score.move)
                    for it in its:
                        it["punch"] = int(ref["_punch"])
                b.shots += its
                if ref.get("_cam"):
                    ctx.cam_land.setdefault(ref["_cam"], t)
                    ctx.cam_len[ref["_cam"]] = ctx.cam_len.get(ref["_cam"], 0) + d
                t += d
        ctx.shots += b.shots


BREATH_MIN_F, BREATH_REACH_F = 3, 12
CAPTION_CONFIDENCE, CAPTION_WORDS = 0.75, 4        # under this mean confidence, or this many words, the transcript is noise, a song or a shout


def breaths(ctx):
    """Programme frames where the voice is between two words for three frames or more: [(from, to)]."""
    if getattr(ctx, "_breaths", None) is None:
        ws = sorted((f, ctx.wfe_any.get(w, f + 1)) for w, f in ctx.wf_any.items())
        ctx._breaths = [(p[1], q[0]) for p, q in zip(ws, ws[1:]) if q[0] - p[1] >= BREATH_MIN_F]
    return ctx._breaths


def on_the_breath(ctx, b, lens):
    """A cut from the person to a found picture lands in the breath nearest to where the Score put it, when one is
    within 0.4 s: an editor cuts on the breath, and the sound that lands with the picture is then heard in the gap
    instead of under a word (13 Nov: two sounds placed, none measurable against the voice). Other cuts keep their own
    clock."""
    if len(lens) < 2 or not ctx.wf_any:
        return lens
    out = list(lens)
    t = b.pf0
    for j in range(1, len(out)):
        t += out[j - 1]
        prev, ref = b.pics[(j - 1) % len(b.pics)], b.pics[j % len(b.pics)]
        if prev.get("asset") != "main" or ref.get("asset") == "main" or ref.get("kind") == "plate":
            continue
        near = [(abs((a + z) // 2 - t), (a + z) // 2) for a, z in breaths(ctx) if abs((a + z) // 2 - t) <= BREATH_REACH_F]
        if not near:
            continue
        d = min(near)[1] - t
        if out[j - 1] + d >= 9 and out[j] - d >= 9:
            out[j - 1] += d
            out[j] -= d
            t += d
    return out


def _gone(ctx, key, dept, bound):
    """Was this device line already decided against? (did, why) from the last look, the floor or the reading before."""
    if not bound and (key in ctx.cuts or "*" in ctx.cuts):
        return ("cut", ctx.cuts.get(key) or ctx.cuts["*"])
    for lane in ("drop_g", "drop_cam", "drop_snd", "drop_item"):
        if key in ctx.plan.get(lane, {}):
            return tuple(ctx.plan[lane][key])
    return None


def camera_gate(ctx, b):
    """A camera move on the upload is a device line: it stands when it names the event it answers. One that
    doesn't keeps its window where the Score put it, at the film's own scale (take_crop)."""
    from . import taste
    for j, ref in enumerate(b.pics):
        if ref.get("asset") != "main" or ref.get("_view") is not None:
            continue
        z = ref.get("zoom")
        if not (ref.get("crop") or ref.get("cam") or (isinstance(z, (int, float)) and not isinstance(z, bool) and z > 1)):
            continue
        key = f"{b.id}.p{j}"
        ctx.wrote["camera"] = ctx.wrote.get("camera", 0) + 1
        ev, why = taste.read_for(ctx, b, ref.get("for"), "camera")
        bound = taste.bound_by(ctx, key, "camera") or (ev["ask"] if ev and ev["kind"] == "ask" else None)
        gone = _gone(ctx, key, "camera", bound) or (("unearned", why) if ev is None and not bound else None)
        if gone:
            ctx.taste_rows.append({"id": key, "did": gone[0], "why": gone[1], "dept": "camera"})
            continue
        ref["_cam"] = key
        ctx.devices[key] = {"id": key, "dept": "camera", "g": "cam", "b": b.id, "ev": ev, "bound": bound or None, "cam_key": True}


def film_seats(ctx):
    """The film's one caption seat and its display seats, solved once from the planned picture (taste)."""
    if ctx.seats is None:
        from . import taste
        ctx.cap_y = taste.caption_seat(ctx, asked=bool(taste.bound_by(ctx, "cap:", "caption")))
        n = {"T": 2, "M": 3, "P": 3, "L": 2}.get(ctx.plan.get("cls") or "T", 2)
        ctx.seats = taste.display_seats(ctx, ctx.cap_y, n, ctx.system.get("seats"))
    return ctx.seats


SEATED = ("big", "chip", "counter", "type_on", "line")


def device_gate(ctx, b, g):
    """A gesture that is a device stands when its line names the word, the turn or the picture event it answers
    (`for`). One that names none is dropped, with a row the Director reads in the meter. Returns False when dropped."""
    from . import taste
    n, gid = g["g"], g["_gid"]
    dept = taste.DEPT.get(n)
    if dept is None:
        return True
    ctx.wrote[dept] = ctx.wrote.get(dept, 0) + 1
    ev, why = taste.read_for(ctx, b, g.get("for"), dept)
    bound = taste.bound_by(ctx, gid, dept) or (ev["ask"] if ev and ev["kind"] == "ask" else None)
    gone = _gone(ctx, gid, dept, bound)
    if gone:
        ctx.taste_rows.append({"id": gid, "did": gone[0], "why": gone[1], "dept": dept})
        return False
    if ev is None and not bound:
        ctx.rejects.append((b.raw.get("_line", 0), f"{gid} dropped: it {why}", ""))
        ctx.taste_rows.append({"id": gid, "did": "unearned", "why": why, "dept": dept})
        return False
    if n in SEATED and not bound:
        seats = film_seats(ctx)
        k = taste.snap(seats, g.get("seat"))
        if k is None:             # P3: a close face fills this frame. Over the take the film sets no display type; it never sets it on the face
            g["_want"], g["_fit0"], g["_noseat"] = g.get("seat"), g.get("fit"), True
            ctx.devices[gid] = {"id": gid, "dept": dept, "g": n, "b": b.id, "ev": ev, "bound": bound or None}
            return True
        g["_want"], g["_fit0"] = g.get("seat"), g.get("fit")      # what the Score wrote: it stands over a found picture or a page (off_take)
        g["seat"] = list(seats[k])
        room = (getattr(ctx, "seat_rooms", None) or [[0.9, 0.2]] * len(seats))[k]
        g["_room"] = room[1]                       # the type is fitted to the seat's room
        g["fit"] = min(float(g["fit"]) if isinstance(g.get("fit"), (int, float)) else 1 - 2 * SAFE_X - 0.02, room[0])
    ctx.devices[gid] = {"id": gid, "dept": dept, "g": n, "b": b.id, "ev": ev, "bound": bound or None}
    if gid in ctx.plan.get("hold", {}):
        g["d"] = int(ctx.plan["hold"][gid][0])
        ctx.taste_rows.append({"id": gid, "did": "repaired", "why": ctx.plan["hold"][gid][1], "dept": dept})
    if gid in ctx.plan.get("trim", {}):
        g["_max"] = int(ctx.plan["trim"][gid][0])
        ctx.taste_rows.append({"id": gid, "did": "repaired", "why": ctx.plan["trim"][gid][1], "dept": dept})
    if n == "punch" and not bound:
        g["z"] = min(float(g.get("z", 1.28)) if isinstance(g.get("z"), (int, float)) else 1.28, taste.STEP_MAX)
    return True


# ---------------------------------------------------------------- type

def strip_punct(t):
    """Ends only. Machine formatting of a transcript token, no reading of what it says."""
    s, e = 0, len(t)
    keep = "%$€£"
    while s < e and unicodedata.category(t[s])[0] == "P" and t[s] not in keep:
        s += 1
    while e > s and unicodedata.category(t[e - 1])[0] == "P" and t[e - 1] not in keep:
        e -= 1
    return t[s:e]


def word_text(ctx, w):
    """What a caption shows for one word, or '' when it shows nothing."""
    if not 0 <= w < len(ctx.words):
        return ""
    if ctx.words[w]["filler"] and w not in ctx.fixed:
        return ""
    t = ctx.text.get(w, "")
    capcfg = cap_cfg(ctx)
    if capcfg.get("punct") != "keep":
        t = " ".join(strip_punct(x) for x in t.split())
        t = " ".join(t.split())
    case = capcfg.get("case")
    if case == "upper":
        t = t.upper()
    elif case == "lower":
        t = t.lower()
    return t


def cap_cfg(ctx):
    c = ctx.dna.get("cap")
    return c if isinstance(c, dict) else {}


def shot_at(ctx, f):
    for s in ctx.shots:
        if s["f"][0] <= f < s["f"][1]:
            return s
    return ctx.shots[-1] if ctx.shots else None


def cards_at(ctx, f):
    return [c for c in ctx.items if c.get("k") == "card" and c["f"][0] <= f < c["f"][1]]


def face_rects(ctx, shot):
    """Faces in frame fractions, on this shot at its middle."""
    if not shot or not is_take(shot.get("pic")) or "src" not in shot:
        return []
    t = (shot["src"][0] + shot["src"][1]) / 2
    s = None
    if ctx.faces:
        s = min(ctx.faces, key=lambda r: abs(r["t"] - t))
    if not s or abs(s["t"] - t) > 2.0:
        return []
    c = shot["crop"]
    c = c[len(c) // 2] if isinstance(c[0], list) else c
    out = []
    for f in s["boxes"]:
        r = [(f[0] - c[0]) / (c[2] - c[0]), (f[1] - c[1]) / (c[3] - c[1]), (f[2] - c[0]) / (c[2] - c[0]), (f[3] - c[1]) / (c[3] - c[1])]
        if r[2] > 0 and r[0] < 1 and r[3] > 0 and r[1] < 1 and (r[2] - r[0]) * (r[3] - r[1]) > 0.002:
            out.append(r)
    return out


def head_rects(ctx, shot):
    """Heads in frame fractions on this shot at its middle: each face grown to its head, and the head
    of a person seen with no face. Type never sits on one (p5302: ESTÁ CHEGANDO across his hair)."""
    if not shot or not is_take(shot.get("pic")) or "src" not in shot:
        return []
    t = (shot["src"][0] + shot["src"][1]) / 2
    boxes = []
    if ctx.faces:
        s = min(ctx.faces, key=lambda r: abs(r["t"] - t))
        if abs(s["t"] - t) <= 2.0:
            boxes += [head_of(f) for f in s["boxes"]]
    if not boxes:
        rows = [r for r in ctx.perc.get("samples") or [] if r.get("person") and abs(float(r["t"]) - t) <= 2.0]
        if rows:
            b = min(rows, key=lambda r: abs(float(r["t"]) - t))["person"]
            w, h = b[2] - b[0], b[3] - b[1]
            boxes.append([b[0] + 0.28 * w, b[1] + 0.02 * h, b[2] - 0.28 * w, b[1] + 0.2 * h])
    c = shot["crop"]
    c = c[len(c) // 2] if isinstance(c[0], list) else c
    out = []
    for f in boxes:
        r = through_crop(f, c)
        if r[2] > 0 and r[0] < 1 and r[3] > 0 and r[1] < 1:
            out.append([clamp(v, 0, 1) for v in r])
    return out


def _iou(a, b):
    i = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return i / max((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i, 1e-9)


def seen_rows(ctx, pic, t):
    """What Vision read in this picture, nearest its second: (printed text boxes, face boxes), source fractions."""
    if pic == "main":
        boxes = [x["box"] for x in ctx.perc.get("text") or [] if x["t"][0] - 0.5 <= t <= x["t"][1] + 0.5 and x.get("box")]
        # The index's own reading at this second too: a sign or a printed number seen in one part of the
        # take never makes a cluster that stays, and type landed on it (p5302: DIA 22 over a printed 22).
        # A region over a quarter of the frame is a pattern, not words.
        # Nor is a box the next sample does not find again: the detector reads sparks, leaves and gravel as text for
        # one sample and somewhere else in the next, while a sign or a caption stays where it is. p5571: 39 boxes on
        # a firework, 2 of them seen twice; with all 39 counted the frame had no place for its title but a strip on
        # the fountain's base. Print holds its place across two samples (overlap 0.3 of their union).
        samples = (ctx.index.get("spatial") or {}).get("samples") or []
        for i, r in enumerate(samples):
            if abs(float(r.get("t", -9)) - t) <= 0.75:
                beside = [q for j in (i - 1, i + 1) if 0 <= j < len(samples) for q in samples[j].get("text") or [] if len(q) == 4]
                boxes += [list(b) for b in r.get("text") or [] if len(b) == 4 and (b[2] - b[0]) * (b[3] - b[1]) < 0.25 and any(_iou(b, q) >= 0.3 for q in beside)]
        # A text box that lies on a head is the detector reading a moustache or a cap, not words (p5591: three of the
        # four "printed" boxes sat on his face, and with them no seat in the frame was clear). The head itself is
        # kept clear by its own rule, so nothing is lost by not counting it twice as print.
        heads = []
        if ctx.faces:
            near = min(ctx.faces, key=lambda r: abs(r["t"] - t))
            if abs(near["t"] - t) <= 2.0:
                heads = [head_of(f) for f in near["boxes"] if (f[2] - f[0]) * (f[3] - f[1]) > 0.01]

        def on_head(b):
            area = max((b[2] - b[0]) * (b[3] - b[1]), 1e-9)
            return any(max(0.0, min(b[2], h[2]) - max(b[0], h[0])) * max(0.0, min(b[3], h[3]) - max(b[1], h[1])) >= 0.5 * area for h in heads)
        return [b for b in boxes if not on_head(b)], []
    if str(pic).startswith("bin:"):
        rows = (ctx.bin.get(str(pic)[4:]) or {}).get("seen") or []
        if rows:
            r = min(rows, key=lambda q: abs(float(q.get("t", 0)) - t))
            return [x["b"] for x in r.get("text") or []], list(r.get("faces") or [])
    return [], []


def through_crop(b, c):
    return [(b[0] - c[0]) / (c[2] - c[0]), (b[1] - c[1]) / (c[3] - c[1]), (b[2] - c[0]) / (c[2] - c[0]), (b[3] - c[1]) / (c[3] - c[1])]


def marks_of(ctx, it, f):
    """Printed words and faces inside one picture, in frame fractions, through its crop and its card."""
    if it.get("pic") in (None, "plate"):
        return []
    lo, hi = it["f"]
    src = it.get("src")
    t = src[0] + (src[1] - src[0]) * (f - lo) / max(hi - lo, 1) if src else 0.0
    text, faces = seen_rows(ctx, it.get("pic"), t)
    if not text and not faces:
        return []
    c = it.get("crop") or [0, 0, 1, 1]
    if c and isinstance(c[0], (list, tuple)):
        c = c[min(max(f - lo, 0), len(c) - 1)]
    out = []
    for b in list(text) + ([] if it.get("pic") == "main" else list(faces)):
        r = through_crop(b, c)
        if it["k"] == "card":
            s = _last(it.get("s", 1)) or 1
            cw, ch, cx, cy = it["w"] * s, it["h"] * s, _last(it["x"]), _last(it["y"])
            r = [cx + (r[0] - 0.5) * cw, cy + (r[1] - 0.5) * ch, cx + (r[2] - 0.5) * cw, cy + (r[3] - 0.5) * ch]
        r = [clamp(v, 0, 1) for v in r]
        if (r[2] - r[0]) * (r[3] - r[1]) > 0.0006:
            out.append(r)
    return out


def mark_rects(ctx, a, z):
    """What this span's pictures already hold: words printed in them, faces in them. Cards on top count."""
    out = []
    for f in sorted({a, (a + z) // 2, max(a, z - 1)}):
        s = shot_at(ctx, f)
        if s:
            out += marks_of(ctx, s, f)
        for c in cards_at(ctx, f):
            if c["f"][1] - c["f"][0] > 6:
                out += marks_of(ctx, c, f)
            if c.get("framed"):                 # a framed still is the picture: type stands on the plate beside it
                cw, ch = float(c["w"]) * (_last(c.get("s", 1)) or 1), float(c["h"]) * (_last(c.get("s", 1)) or 1)
                out.append([_last(c["x"]) - cw / 2, _last(c["y"]) - ch / 2, _last(c["x"]) + cw / 2, _last(c["y"]) + ch / 2])
    return out


THUMB_W, THUMB_H, THUMB_FPS = 72, 128, 3
STILL_EXT = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")


def thumbs(ctx, aid):
    """One asset as small gray frames, built once beside the session. Image: h,w. Video: n,h,w at
    THUMB_FPS. None when there is nothing to read, as in a compile with no media."""
    if aid in ctx.thumb_cache:
        return ctx.thumb_cache[aid]
    ctx.thumb_cache[aid] = None
    a = ctx.assets.get(aid)
    if not ctx.work or not a or not a.get("path"):
        return None
    src = os.path.join(ctx.work, a["path"])
    if not os.path.exists(src):
        return None
    d = os.path.join(ctx.work, "thumbs")
    p = os.path.join(d, aid.replace(":", "_") + ".npy")
    import numpy as np
    # A found clip reaches the session as its still (supply: bin/N.jpg, the clip itself stays in storage).
    # The fps filter makes no frame from a still, so every found clip read as nothing: no ink flip, no
    # edge and no light match over it (p5374: cream captions on a white clock face and on bokeh).
    moving = a.get("kind") == "video" and os.path.splitext(src)[1].lower() not in STILL_EXT
    try:
        if not os.path.exists(p) or os.path.getmtime(p) < os.path.getmtime(src):
            os.makedirs(d, exist_ok=True)
            vf = f"fps={THUMB_FPS},scale={THUMB_W}:{THUMB_H}:flags=area,format=gray" if moving else f"scale={THUMB_W}:{THUMB_H}:flags=area,format=gray"
            r = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-threads", "1", "-i", src, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, timeout=120)
            arr = np.frombuffer(r.stdout, np.uint8).reshape(-1, THUMB_H, THUMB_W)
            np.save(p, arr if moving else arr[:1])
        arr = np.load(p).astype(np.float32) / 255
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    ctx.thumb_cache[aid] = arr
    return arr


def luma_in(ctx, it, f, rect):
    """The dark and bright ends of the picture under a rect of the frame, or None. Near the ends, because
    type across half a wall and half a black bag reads by its worst stretch, not by its average."""
    import numpy as np
    pic = it.get("pic")
    if pic == "plate":
        l = luma((it.get("plate") or {}).get("c0") or "#000000")
        return l, l
    arr = thumbs(ctx, pic)
    if arr is None or not len(arr):
        return None
    lo, hi = it["f"]
    src = it.get("src")
    i = 0
    if src and len(arr) > 1:
        t = src[0] + (src[1] - src[0]) * (f - lo) / max(hi - lo, 1)
        i = min(max(int(round(t * THUMB_FPS)), 0), len(arr) - 1)
    c = it.get("crop") or [0, 0, 1, 1]
    if c and isinstance(c[0], (list, tuple)):
        c = c[min(max(f - lo, 0), len(c) - 1)]
    x0 = int((c[0] + rect[0] * (c[2] - c[0])) * THUMB_W)
    x1 = int(math.ceil((c[0] + rect[2] * (c[2] - c[0])) * THUMB_W))
    y0 = int((c[1] + rect[1] * (c[3] - c[1])) * THUMB_H)
    y1 = int(math.ceil((c[1] + rect[3] * (c[3] - c[1])) * THUMB_H))
    x0, y0 = clamp(x0, 0, THUMB_W - 1), clamp(y0, 0, THUMB_H - 1)
    v = arr[i][y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)]
    if (it.get("treat") or {}).get("mode") == "neg":
        v = 1 - v
    return float(np.percentile(v, 8)), float(np.percentile(v, 92))


def under_rect(ctx, s, f, rect):
    """What a rect of the frame sits on, the top held card first. (dark end, bright end) or None."""
    x, y = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    for c in sorted(cards_at(ctx, f), key=lambda q: q.get("z", 0), reverse=True):
        hw, hh = c["w"] * _last(c.get("s", 1)) / 2, c["h"] * _last(c.get("s", 1)) / 2
        cx, cy = _last(c["x"]), _last(c["y"])
        if abs(x - cx) < hw and abs(y - cy) < hh and c["f"][1] - c["f"][0] > 6:
            inner = [(rect[0] - cx + hw) / (2 * hw), (rect[1] - cy + hh) / (2 * hh), (rect[2] - cx + hw) / (2 * hw), (rect[3] - cy + hh) / (2 * hh)]
            return luma_in(ctx, c, f, [clamp(q, 0, 1) for q in inner])
    return luma_in(ctx, s, f, rect) if s else None


DARK_INK = "#111111"


def ink_over(ctx, s, f, rect):
    """Polarity from the picture itself. White fails on the bright end under it, dark on the dark end:
    the film's ink stays while it holds, and flips only when the other reads better."""
    base = norm_hex(ctx.dna.get("ink")) if is_hex(ctx.dna.get("ink")) else "#FFFFFF"
    q = under_rect(ctx, s, f, rect)
    if q is None:
        return None
    lo, hi = q
    white, dark = 1.0 - hi, lo - 0.07
    mine = white if luma(base) > 0.5 else dark
    other = dark if luma(base) > 0.5 else white
    if mine >= 0.34 or mine >= other:
        return base
    return DARK_INK if luma(base) > 0.5 else "#FFFFFF"


# Neither polarity reads when type crosses a bright floor and dark trousers at once (p5302 today: white
# AQUI NA, DIA 20 and COM PROMOCOES on white tiles and a white page, low_quality_text). Such a word is
# marked `edge`, and the lowering gives it the dark edge today's renderer can draw.
EDGE_BELOW = 0.34         # the bar ink_over itself reads by


def clip_by_still(ctx, s):
    """A found clip whose frames this session cannot read, only its still (production fetches clips on
    the egress lane and orchestration holds no media bytes). Its moving picture can be far from the
    still (p5374: a dark still, then a white bokeh under cream captions), so a word over it always
    carries the edge: ink chosen from the still, readable whichever way the clip turns."""
    if not s or not str(s.get("pic") or "").startswith("bin:"):
        return False
    a = ctx.assets.get(s["pic"]) or {}
    return a.get("kind") == "video" and os.path.splitext(str(a.get("path") or ""))[1].lower() in STILL_EXT


def ink_margin(ctx, s, f, rect, ink):
    """How well this ink reads on the worst stretch under it: 0 is no contrast, None when unknown."""
    q = under_rect(ctx, s, f, rect)
    if q is None:
        return None
    lo, hi = q
    return 1.0 - hi if luma(ink) > 0.5 else lo - 0.07


def under_plate(ctx, f):
    s = shot_at(ctx, f)
    return s.get("plate") if s and s.get("pic") == "plate" else None


def card_edge_seat(ctx, seat, block_h, a, z):
    """A caption rests on the edge of a card that is on screen, or None when there is none near its seat."""
    x, y = seat
    mid = (a + z) // 2
    cards = [c for c in cards_at(ctx, mid) if c.get("_cap_edge") and c["f"][1] - c["f"][0] > 6]
    if cards:
        c = max(cards, key=lambda q: q.get("z", 0))
        hw = c["w"] * _last(c.get("s", 1)) / 2
        hh = c["h"] * _last(c.get("s", 1)) / 2
        cx, cy = _last(c["x"]), _last(c["y"])
        tilt = abs(math.sin(math.radians(float(_last(c.get("rot", 0) or 0))))) * hw * ctx.W / ctx.H
        if abs(x - cx) < hw + 0.2:
            y2 = cy - hh - tilt - 0.014 - block_h / 2
            if y2 - block_h / 2 > 0.03:
                return [cx, y2]
            return [cx, cy + hh + tilt + 0.014 + block_h / 2]
    return None


def solve_seat(ctx, seat, block_w, block_h, a, z):
    """Seat a block of type: rests on a card's edge, leaves faces and display type, never
    enters the keep-out."""
    x, y = seat
    mid = (a + z) // 2
    on_card = card_edge_seat(ctx, seat, block_h, a, z)
    if on_card:
        return on_card
    margin = 0.012
    obstacles = [b[2] for b in ctx.blocks if b[0] < z and b[1] > a] + mark_rects(ctx, a, z)
    heads = []
    for f in (a, mid, max(a, z - 1)):
        obstacles += face_rects(ctx, shot_at(ctx, f))
        heads += head_rects(ctx, shot_at(ctx, f))
    y0 = y
    y = clear_of(x, y, block_w, block_h, obstacles + heads, margin)
    if heads and clear_of(x, y, block_w, block_h, heads, margin, probe=True):
        # No seat clears everything: a head outranks a printed sign or another line
        y = clear_of(x, y0, block_w, block_h, heads, margin)
    return [x, y]


def clear_of(x, y, block_w, block_h, obstacles, margin, probe=False):
    """The nearest height, down first, where a block touches none of the obstacles. With probe, is
    the block at y touching one?"""
    def hits(yy):
        rect = [x - block_w / 2, yy - block_h / 2 - margin / 2, x + block_w / 2, yy + block_h / 2 + margin / 2]
        return [r for r in obstacles if rects_touch(rect, r)]
    if probe:
        return bool(hits(y))
    for _ in range(6):
        hit = hits(y)
        if not hit:
            break
        low = max(r[3] for r in hit) + margin + block_h / 2
        high = min(r[1] for r in hit) - margin - block_h / 2
        if low + block_h / 2 < 0.93 and not hits(low):
            y = low
        elif high - block_h / 2 > 0.06 and not hits(high):
            y = high
        elif low + block_h / 2 < 0.93:
            y = low
        elif high - block_h / 2 > 0.06:
            y = high
        else:
            break
    return y


def _last(v):
    return v[-1] if isinstance(v, list) else v


def dodge_keepout(x0, y0, x1, y1):
    """How far down a rect must move to clear the watermark, or 0."""
    if rects_touch((x0, y0, x1, y1), KEEPOUT):
        return KEEPOUT[3] + 0.008 - y0
    return 0.0


def layout_lines(ctx, parts, seat_x, max_w):
    """Wrap and centre a run of (text, voice key). Returns lines of [(index, x_px, w_px)]."""
    lines, cur, cw = [], [], 0.0
    gap_em = float(cap_cfg(ctx).get("gap", 0.28))
    W = ctx.W
    for i, (t, v) in enumerate(parts):
        vc = ctx.voices[v]
        w = text_px(t, vc)
        gap = gap_em * vc["px"]
        if cur and (cw + gap + w) / W > max_w:
            lines.append((cur, cw))
            cur, cw = [], 0.0
        cw += (gap if cur else 0) + w
        cur.append((i, w, gap))
    if cur:
        lines.append((cur, cw))
    out = []
    for cur, cw in lines:
        x = seat_x * W - cw / 2
        row = []
        for i, w, gap in cur:
            row.append((i, x, w))
            x += w + gap
        out.append(row)
    return out


def build_captions(ctx):
    """Chunks on the voice. A line is typeset whole, then revealed word by word (TC2), or
    reflowed, or swapped whole, as the DNA says. A caption holds through cuts."""
    cfg = cap_cfg(ctx)
    layout = cfg.get("layout", "prelaid")
    if ctx.governed and layout == "chunk" and os.environ.get("VALMERA_CAP_BUILD", "1") != "0":
        # round six: a chunk is written word by word as the voice says it, each word where it will stay (all eight of
        # the judge's reels build their captions; ours swapped whole chunks, which the rater read as plain). The chunk's
        # words, seat and length are the Score's: only the moment each word appears changes.
        layout = "prelaid"
    chunks = []
    said = " ".join(ctx.text.values())
    if joined_script(said):
        for key in ("a", "b"):
            ctx.voices[key].update(font=script_font(ctx.voices[key]["font"], said), track=0)
    seat0 = ctx.dna.get("seat")
    if seat0 != "free" and not (isinstance(seat0, (list, tuple)) and len(seat0) == 2):
        seat0 = [0.5, 0.62]
    asked = False
    if ctx.governed:
        from . import taste
        film_seats(ctx)
        seat0 = [0.5, ctx.cap_y]           # P1: one caption seat for the film; the caption does not go hunting
        asked = any(str(a.get("probe")) in ("caption_coverage", "caption_language") for a in ctx.asks.values())
        # Words the transcript itself doubts are not set on screen: a firework clip came back captioned "tämä on hän"
        # in Finnish, eight words at 0.64 confidence (p5571). Measured on the index, never on what the words say.
        conf = [float(w.get("confidence", 1.0)) for w in ctx.index.get("words") or []]
        if not asked and ctx.words and (sum(conf) / len(conf) < CAPTION_CONFIDENCE or len(ctx.words) < CAPTION_WORDS):
            ctx.taste_rows.append({"id": "cap", "did": "thinned", "dept": "caption",
                                   "why": f"{len(ctx.words)} words at a transcript confidence of {sum(conf) / len(conf):.2f}: not speech a caption can be trusted with"})
            ctx.n_chunks = 0
            return
        own = taste.upload_captions(ctx)
        ctx.upload_caps = round(own, 2)
        if own >= 0.5 and not asked:       # P7: say it once
            ctx.taste_rows.append({"id": "cap", "did": "thinned", "dept": "caption",
                                   "why": f"the upload prints its own captions on {int(own * 100)}% of its speech, so none are added"})
            ctx.n_chunks = 0
            return
    for b in ctx.beats:
        specs = b.raw.get("type")
        if specs in (None, "", []):
            continue
        for sidx, spec in enumerate(specs if isinstance(specs, list) else [specs]):
            opts = spec if isinstance(spec, dict) else {}
            cap = opts.get("cap") if isinstance(spec, dict) else spec
            if cap in (None, ""):
                continue
            if isinstance(cap, str) and cap.startswith("cap:"):
                try:
                    n = max(1, int(cap[4:]))
                except ValueError:
                    n = 2
                ws = [w for w in b.ws if word_text(ctx, w)]
                groups = [[(w, False) for w in ws[i:i + n]] for i in range(0, len(ws), n)]
            else:
                groups = parse_cap(cap)
            for n, ws in enumerate(groups):
                ws2 = []
                for w, star in ws:
                    if w not in b.wf:
                        if w in ctx.wf_any:
                            b.wf[w], b.wfe[w] = ctx.wf_any[w], ctx.wfe_any[w]
                        else:
                            ctx.find(f"{b.id}: caption word {w} is not in the film")
                            continue
                    if not word_text(ctx, w):
                        continue
                    ws2.append((w, star))
                if not ws2:
                    continue
                r = rng_for(ctx.seed, "cap", b.id, sidx, n)
                lag = cfg.get("lag")
                lag = int(lag) if isinstance(lag, (int, float)) else r.choice([1, 2, 2, 3])
                cid = f"{b.id}.c{n}" if sidx == 0 else f"{b.id}.c{sidx}_{n}"
                chunks.append({"id": cid, "b": b, "ws": ws2, "lag": lag, "seat": seat0 if ctx.governed else opts.get("seat") or seat0,
                               "ink": opts.get("ink"), "on": b.wf[ws2[0][0]] + lag,
                               "end": b.wfe[ws2[-1][0]]})
    chunks.sort(key=lambda c: c["on"])
    linger = int(round(float(cfg.get("hold", 0.3)) * ctx.fps))
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1]["on"] if i + 1 < len(chunks) else None
        off = c["end"] + linger
        if nxt is not None and nxt - c["end"] <= linger + 3:
            off = nxt
        if nxt is not None:
            off = min(off, nxt)
        off = min(max(off, c["on"] + 4), ctx.dur_f)
        c["off"] = off
    auto_pool = cfg.get("pool")
    for c in chunks:
        if c["off"] <= c["on"]:
            continue
        place_chunk(ctx, c, layout, auto_pool)
    ctx.n_chunks = len(chunks)


def place_chunk(ctx, c, layout, auto_pool):
    parts = [(word_text(ctx, w), "b" if star else "a") for w, star in c["ws"]]
    W, H = ctx.W, ctx.H
    px = max(ctx.voices[v]["px"] for _, v in parts)
    max_w = 1 - 2 * SAFE_X
    if ctx.governed and ctx.H > ctx.W:
        from . import taste
        max_w = taste.CAP_MAX_W            # P2: a centred line ends left of the platform's buttons
    if layout == "stack":
        lines = [[(i, 0.0, text_px(t, ctx.voices[v]))] for i, (t, v) in enumerate(parts)]
        lines = [[(i, (W * c["seat"][0] - w / 2), w)] for row in lines for i, _, w in row]
    else:
        lines = layout_lines(ctx, parts, 0.5, max_w)
    lead = 1.16 * px / H
    block_h = (len(lines) - 1) * lead + 0.74 * px / H
    block_w = max((row[-1][1] + row[-1][2] - row[0][1]) for row in lines) / W
    seat0 = c["seat"]
    if seat0 == "free":
        fr = free_seat(ctx, c["on"])
        seat0 = [(fr[0] + fr[2]) / 2, (fr[1] + fr[3]) / 2] if fr else [0.5, 0.62]
    if ctx.governed and seat0 == [0.5, ctx.cap_y]:
        # the film's seat. It changes only onto the edge of a card that is on screen
        seat = card_edge_seat(ctx, [0.5, ctx.cap_y], block_h, c["on"], c["off"]) or [0.5, ctx.cap_y]
    else:
        seat = solve_seat(ctx, [float(seat0[0]), float(seat0[1])], block_w, block_h, c["on"], c["off"])
    seat[0] = clamp(seat[0], SAFE_X + block_w / 2, 1 - SAFE_X - block_w / 2)
    top = seat[1] - block_h / 2
    dy = dodge_keepout(seat[0] - block_w / 2, top, seat[0] + block_w / 2, top + block_h)
    seat[1] += dy
    top += dy
    shift = (seat[0] - 0.5) * W
    ons = {}
    for i, (w, star) in enumerate(c["ws"]):
        ons[i] = c["on"] if layout == "chunk" else clamp(c["b"].wf[w] + c["lag"], c["on"], c["off"] - 1)
    base = top + 0.74 * px / H
    block = [clamp(seat[0] - block_w / 2, 0, 1), clamp(top - 0.002, 0, 1), clamp(seat[0] + block_w / 2, 0, 1), clamp(top + block_h + 0.002, 0, 1)]
    pool = auto_pool
    if pool is None:
        bright = under_bright(ctx, c["on"], c["off"])
        pool = 0.45 if bright else 0
    mid = (c["on"] + c["off"]) // 2
    q = under_rect(ctx, shot_at(ctx, mid), mid, block)
    if not pool and q and 1.0 - q[1] < 0.22 and q[0] - 0.07 < 0.22:      # half on white tile, half on dark jeans: no ink reads there
        pool = 0.55
    if pool:
        hw, hh = block_w * 1.1 + 0.03, block_h * 1.6 + 0.02
        ctx.add({"id": f"{c['id']}.pool", "k": "flash", "mode": "pool", "f": [c["on"], c["off"]], "a": float(pool), "z": -1,
                 "rect": [clamp(seat[0] - hw, 0, 1), clamp(seat[1] - hh, 0, 1), clamp(seat[0] + hw, 0, 1), clamp(seat[1] + hh, 0, 1)]})
    cfg = cap_cfg(ctx)
    for ln, row in enumerate(lines):
        y = base + ln * lead
        for i, x_px, w_px in row:
            w, star = c["ws"][i]
            t, v = parts[i]
            a = ons[i]
            x = (x_px + shift) / W
            iid = f"{c['id']}.w{w}"
            if layout == "reflow":
                x = reflow_x(ctx, c, row, parts, ons, i, a, shift)
            elif layout == "ghost":
                # the line is there before the voice, the ink follows it (TC2)
                g = float(cfg.get("ghost", 0.35))
                emit_word(ctx, iid, t, v, x, y, c["on"], c["off"], c["ink"], "cap", chunk=c["id"], region=block, op=[g] * (a - c["on"]) + [1.0] * (c["off"] - a))
                continue
            elif layout == "trail":
                g = float(cfg.get("ghost", 0.4))
                nxt = min([ons[j] for j in ons if ons[j] > a] + [c["off"]])
                emit_word(ctx, iid, t, v, x, y, a, c["off"], c["ink"], "cap", chunk=c["id"], region=block, op=[1.0] * (nxt - a) + [g] * (c["off"] - nxt))
                continue
            elif cfg.get("in") in ("blur", "rise", "settle", "flicker"):
                ex = entrance(cfg["in"], c["off"] - a, y)
                yy = ex.pop("y", y)
                xx = x
                if "s" in ex and len(set(ons.values())) == 1:     # the line settles as one thing: its words keep their gaps
                    cx, cy = seat[0], seat[1]
                    wf = w_px / W
                    # at rest it holds its exact place, and the engine puts a word at rest on the pixel grid
                    xx = [x if abs(q - 1) < 0.0025 else cx + (x + wf / 2 - cx) * q - wf / 2 for q in ex["s"]]
                    yy = [y if abs(q - 1) < 0.0025 else cy + (y - cy) * q for q in ex["s"]]
                emit_word(ctx, iid, t, v, xx, yy, a, c["off"], c["ink"], "cap", chunk=c["id"], region=block, **ex)
                continue
            elif layout == "redact" and a > c["on"]:
                p_h = ctx.voices[v]["px"] / H
                ctx.add({"id": f"{iid}.bar", "k": "flash", "mode": "fill", "c": norm_hex(cfg["bar"]) if is_hex(cfg.get("bar")) else "#000000",
                         "f": [c["on"], a], "rect": [x, y - 0.8 * p_h, x + w_px / W, y + 0.2 * p_h], "z": 1})
            emit_word(ctx, iid, t, v, x, y, a, c["off"], c["ink"], "cap", chunk=c["id"], region=block)


def spoken_tokens(t):
    return [strip_punct(x).casefold() for x in str(t).split() if strip_punct(x)]


def yield_to_display(ctx):
    """One reading path. Two strings of this film are compared: when display type is showing most of
    a caption's words, the caption leaves as it lands, or doesn't come. A chunk never says twice
    what is on the screen in big type. Coverage still counts the words the display carries."""
    bigs = [w for w in ctx.items if w.get("k") == "word" and w.get("role") == "big"]
    if not bigs:
        return
    chunks = {}
    for w in ctx.items:
        if w.get("k") == "word" and w.get("role") == "cap":
            chunks.setdefault(w.get("chunk"), []).append(w)
    gone = set()
    for cid, words in chunks.items():
        a = min(w["f"][0] for w in words)
        z = max(w["f"][1] for w in words)
        said = [q for w in words for q in spoken_tokens(w["t"])]
        ids = {w["id"].split(".p")[0] for w in words}
        cut = None
        for b in bigs:
            if b["f"][0] >= z or b["f"][1] <= a:
                continue
            shown = set(spoken_tokens(b["t"]))
            if said and sum(1 for q in said if q in shown) * 2 >= len(said):
                cut = b["f"][0] if cut is None else min(cut, b["f"][0])
        if cut is None:
            continue
        keep_to = cut if cut - a >= 9 else a        # a caption that would flash for a few frames doesn't come
        for w in words:
            if keep_to <= w["f"][0]:
                gone.add(w["id"])
            elif keep_to < w["f"][1]:
                n = keep_to - w["f"][0]
                for k in ("x", "y", "op", "s", "blur"):
                    if isinstance(w.get(k), list):
                        w[k] = w[k][:n]
                w["f"] = [w["f"][0], keep_to]
        ctx.yielded = getattr(ctx, "yielded", 0) + 1
        ctx.drop_pools = getattr(ctx, "drop_pools", set()) | {f"{cid}.pool"}
    if gone or getattr(ctx, "drop_pools", None):
        ctx.items = [i for i in ctx.items if i["id"] not in gone and i["id"] not in getattr(ctx, "drop_pools", set())]


def reflow_x(ctx, c, row, parts, ons, i, a, shift):
    """The line re-centres each time a word lands, one x per frame."""
    W = ctx.W
    gap_em = float(cap_cfg(ctx).get("gap", 0.28))
    out = []
    for f in range(a, c["off"]):
        on = [(j, ww) for j, _, ww in row if ons[j] <= f]
        total = sum(ww for _, ww in on) + sum(gap_em * ctx.voices[parts[j][1]]["px"] for j, _ in on[1:])
        x = W / 2 - total / 2 + shift
        for j, ww in on:
            if j == i:
                break
            x += ww + gap_em * ctx.voices[parts[j][1]]["px"]
        out.append(x / W)
    if len(set(out)) == 1:
        return out[0]
    return out


def plate_under(ctx, s):
    """The flat colour a word sits on, when the shot has one."""
    if not s:
        return None
    if s.get("pic") == "plate":
        return s.get("plate")
    return ctx.own.get(str(s.get("pic"))[4:], {}).get("plate") if str(s.get("pic")).startswith("own:") else None


def under_bright(ctx, a, z):
    """Is the picture under this chunk bright, by the index's own luma samples?"""
    s = shot_at(ctx, (a + z) // 2)
    if not s:
        return False
    if plate_under(ctx, s):
        return False
    if s.get("pic") == "main" and "src" in s:
        l = luma_at(ctx, s["src"][0])
        return l is not None and l > 140
    v = ctx.bin.get(str(s.get("pic"))[4:]) if str(s.get("pic")).startswith("bin:") else None
    return bool(v and v.get("luma") is not None and float(v["luma"]) > 0.55)


def word_rect(ctx, t, v, x, y):
    """Where a settled word's ink sits, in frame fractions."""
    vc = ctx.voices.get(v) or {"font": "", "px": 58}
    x, y = _last(x), _last(y)
    w, h = text_px(t, vc) / ctx.W, 0.8 * vc["px"] / ctx.H
    return [clamp(x, 0, 1), clamp(y - h, 0, 1), clamp(x + w, 0, 1), clamp(y, 0, 1)]


def emit_word(ctx, iid, t, v, x, y, a, end, ink, role, chunk=None, region=None, **extra):
    """One word. While gestures run the picture under it isn't final: a salvo replaces the base after
    this line, and a later gesture can too. So words wait for the sealed film, and an anchor on one
    still resolves."""
    if ctx.cur_gid and "_g" not in extra:
        extra["_g"] = ctx.cur_gid
    if ctx.waiting is None:
        place_word(ctx, iid, t, v, x, y, a, end, ink, role, chunk, region, **extra)
    elif t and end > a:
        ctx.waiting.append((iid, t, v, x, y, a, end, ink, role, chunk, region, extra))
        ctx.byid.setdefault(iid, {"f": [a, end]})


def place_waiting_words(ctx):
    rows, ctx.waiting = ctx.waiting or [], None
    for r in rows:
        ctx.byid.pop(r[0], None)
    for iid, t, v, x, y, a, end, ink, role, chunk, region, extra in rows:
        place_word(ctx, iid, t, v, x, y, a, end, ink, role, chunk, region, **extra)


def place_word(ctx, iid, t, v, x, y, a, end, ink, role, chunk=None, region=None, **extra):
    """Split where the picture under it changes polarity, so ink stays readable. A block of words
    passes one region, so a line never changes ink in the middle."""
    end = min(end, ctx.dur_f)             # a word never outlives the film
    if not t or end <= a:
        return
    pieces = []
    f = a
    rect = region or word_rect(ctx, t, v, x, y)
    blk = extra.get("_blk")
    whole = None
    if blk and not is_hex(ink):
        rect = blk                        # the lines of one display block take one ink, read off the whole block
        if ctx.governed and role == "big":
            # and one ink for as long as it stands: over a cut from a dark shirt to pale shorts a title went from white
            # to black in the middle of being read (p5572, 13 Nov). It is dark only where the ground under it is bright
            # in every shot it crosses; anywhere else it is the film's light ink, with the edge where that needs it.
            base = norm_hex(ctx.dna.get("ink")) if is_hex(ctx.dna.get("ink")) else "#FFFFFF"
            light = base if luma(base) > 0.5 else "#FFFFFF"
            dark, lit, seen_any = [], [], False
            for g in sorted(set(list(range(a, end, 6)) + [max(a, end - 1)])):
                s_ = shot_at(ctx, g)
                if s_ is None or plate_under(ctx, s_):
                    continue
                q_ = under_rect(ctx, s_, g, rect)
                if q_ is None:
                    continue
                seen_any = True
                lit.append(1.0 - q_[1])
                dark.append(q_[0] - 0.07)
            if seen_any:
                whole = DARK_INK if (min(dark) >= EDGE_BELOW and min(lit) < EDGE_BELOW) else light
    while f < end:
        s = shot_at(ctx, f)
        q = min(end, s["f"][1]) if s else end
        if q <= f:                        # the nearest shot ended before this frame: the walk must still advance, or the compile
            q = end                       # never returns (p5554, 13 Nov: a counter step on the film's last frame spun for minutes)
        ink2 = ink if is_hex(ink) else (ink_over(ctx, s, (f + q) // 2, rect) or ink_on(ctx, plate_under(ctx, s)))
        if whole is not None and s is not None and not plate_under(ctx, s):
            ink2 = whole
        ink2 = norm_hex(ink2)
        if ctx.governed and role == "cap" and not is_hex(ink) and s is not None and not plate_under(ctx, s):
            # A caption keeps the film's ink over a picture unless the picture swallows it: on a mid-grey hoodie the
            # light ink read a little worse than dark on one chunk and a little better on the next, and the caption
            # changed colour chunk by chunk (p5591, 13 Nov). Under the bar it keeps its ink and takes the edge.
            base = norm_hex(ctx.dna.get("ink")) if is_hex(ctx.dna.get("ink")) else "#FFFFFF"
            if ink2 != base:
                m_base = ink_margin(ctx, s, (f + q) // 2, rect, base)
                if m_base is None or m_base >= 0.15:
                    ink2 = base
        # a chip's word sits on its own label, whatever the frame under the label is: on a dark page the dark word
        # of a white chip was flipped to white and the label stood empty (13 Nov, a name over a framed still)
        pl = {"c0": extra["_label"]} if is_hex(extra.get("_label")) else plate_under(ctx, s)
        if ctx.governed and is_hex(ink) and not pl:        # P4: a named ink the picture swallows flips to the one that reads
            m0 = ink_margin(ctx, s, (f + q) // 2, rect, ink2)
            other = DARK_INK if luma(ink2) > 0.5 else "#FFFFFF"
            m1 = ink_margin(ctx, s, (f + q) // 2, rect, other)
            if m0 is not None and m1 is not None and m0 < EDGE_BELOW and m1 > m0 + 0.1:
                ink2 = other
        if pl and is_hex(ink) and abs(luma(ink2) - luma(pl["c0"])) < 0.16:      # p5302: pink ESPETACULARES on the pink page. Named ink, same plate
            ink2 = "#FFFFFF" if luma(pl["c0"]) < 0.62 else "#111111"
        if pl:
            m = 1.0 - luma(pl["c0"]) if luma(ink2) > 0.5 else luma(pl["c0"]) - 0.07
        else:
            # Every stretch under the word, not one frame of it: a punch-in, a walk past a white wall or
            # a turn of the head changes what a word sits on inside one shot (p5302: white SENSACIONAL
            # on the wall at 7.7 s). A picture this session cannot read under it gets the edge too.
            ms = [ink_margin(ctx, s, g, rect, ink2) for g in sorted({f, f + (q - f) // 4, (f + q) // 2, f + 3 * (q - f) // 4, max(f, q - 1)})]
            ms = [x for x in ms if x is not None]
            m = min(ms) if ms else (None if s is None else -1.0)
        edge = (m is not None and m < EDGE_BELOW) or clip_by_still(ctx, s)
        if ctx.governed and role == "big" and blk and not pl and not is_hex(ink) and edge and luma(ink2) < 0.5:
            # Dark ink reads on a bright ground and nowhere else, and the renderer draws a dark edge only, so dark type
            # that crosses a dark stretch has nothing to hold it (p5584, 13 Nov: ON A CALL in black across a pale step
            # and his black trousers, its second line in white: the last look could read neither). Over a picture
            # that is not bright all the way, display type is light and takes the edge.
            base = norm_hex(ctx.dna.get("ink")) if is_hex(ctx.dna.get("ink")) else "#FFFFFF"
            ink2 = base if luma(base) > 0.5 else "#FFFFFF"
        if pieces and pieces[-1][2] == ink2 and pieces[-1][3] == edge:
            pieces[-1][1] = q
        else:
            pieces.append([f, q, ink2, edge])
        f = q
    for i, (p, q, col, edge) in enumerate(pieces):
        it = {"id": iid if i == 0 else f"{iid}.p{i}", "k": "word", "f": [p, q], "t": t, "v": v, "x": x, "y": y, "ink": col, "role": role, "on": a}
        if edge:
            it["edge"] = True
        if chunk:
            it["chunk"] = chunk
        it.update(extra)
        for key in ("x", "y", "op", "s", "blur"):
            val = it.get(key)
            if isinstance(val, list):
                it[key] = (val + [val[-1]] * (end - a))[p - a:q - a]
        ctx.add(it)


ENTRANCES = ("cut", "flicker", "letters", "blur", "rise", "bounce", "settle")


def entrance(kind, n, y):
    """Baked per-frame fields for a display word."""
    out = {}
    if kind == "flicker":
        out["op"] = (FLICKER + [1.0] * n)[:n]
    elif kind == "blur":
        out["blur"] = ([14, 7, 2] + [0] * n)[:n]
        out["op"] = ([0.6, 0.85, 1.0] + [1.0] * n)[:n]
    elif kind == "bounce":      # blur 10 to 0, rise .03 h, overshoot -.005, ATLAS 2.6
        m = min(9, n)
        ys = [y + 0.03 * (1 - i / max(1, m - 2)) if i < m - 2 else y - 0.005 * (m - 1 - i) / 2 for i in range(m)]
        out["y"] = (ys + [y] * n)[:n]
        out["blur"] = (BOUNCE_BLUR + [0] * n)[:n]
    elif kind == "rise":
        m = min(6, n)
        out["y"] = ([y + 0.02 * (1 - i / m) ** 2 for i in range(m)] + [y] * n)[:n]
    elif kind == "settle":
        s_inf, tau = SETTLE["s_inf"], SETTLE["tau"]
        out["s"] = [(s_inf + (1 - s_inf) * math.exp(-i / tau)) / s_inf for i in range(n)]
    return out


def voice_light(ctx):
    """The film's shadow and glow on display type: a word can give off light on a night plate."""
    out = {}
    for k in ("shadow", "glow"):
        if isinstance(ctx.dna.get(k), dict):
            out[k] = ctx.dna[k]
    return out


def display_lines(ctx, text, opt):
    """Display text to lines, each with its own font, size and ink. A size ladder is a list:
    [{"t":"get","size":.06},{"t":"that you","size":.015}]."""
    H = ctx.H
    if isinstance(text, (list, tuple)):
        rows = [r if isinstance(r, dict) else {"t": str(r)} for r in text]
    else:
        rows = [{"t": l} for l in str(text).replace("|", "\n").split("\n")]
    rows = [r for r in rows if str(r.get("t", "")).strip()]
    base_font, base_size, base_track = None, opt.get("size"), None
    spec = opt.get("font") or ctx.dna.get("big")
    if spec:
        base_font, sz, base_track = parse_font_spec(spec)
        base_size = base_size or sz
        if base_font is None:
            ctx.find(f"font {spec!r} is not in the set, safety font used")
    out = []
    for r in rows:
        font, size, track = base_font, r.get("size", base_size), base_track
        if r.get("font"):
            f2, sz2, tr2 = parse_font_spec(r["font"])
            font = f2 or font
            size = r.get("size") or sz2 or size
            track = base_track if tr2 is None else tr2
        font = font or "InterDisplay-Black.ttf"
        track = -3 if track is None else track
        t = str(r["t"])
        if opt.get("case") == "upper":
            t = t.upper()
        if joined_script(t):
            font, track = script_font(font, t), 0
        px = int(round(float(size) * H)) if isinstance(size, (int, float)) and not isinstance(size, bool) and size > 0 else int(round(0.2 * H))
        out.append({"t": t, "font": font, "track": track, "px": px, "ink": r.get("ink"), "sized": bool(size)})
    return out


def free_seat(ctx, f):
    """The largest free region of the footage at this frame, in frame fractions, or None."""
    s = shot_at(ctx, f)
    if not s or "src" not in s or not (s.get("pic") == "main" or str(s.get("pic")).startswith("own:cut")):   # a cut-out keeps the take's geometry
        return None
    t = s["src"][0]
    row = next((x for x in ctx.perc.get("shots") or [] if x["t"][0] <= t < x["t"][1]), None)
    if not row or not row.get("free"):
        return None
    c = s["crop"]
    c = c[0] if isinstance(c[0], list) else c
    best, area = None, 0.0
    for r in row["free"]:
        q = [(r[0] - c[0]) / (c[2] - c[0]), (r[1] - c[1]) / (c[3] - c[1]), (r[2] - c[0]) / (c[2] - c[0]), (r[3] - c[1]) / (c[3] - c[1])]
        q = [clamp(q[0], 0, 1), clamp(q[1], 0, 1), clamp(q[2], 0, 1), clamp(q[3], 0, 1)]
        if rects_touch(q, KEEPOUT):
            q[1] = max(q[1], KEEPOUT[3] + 0.01)
        ar = max(0.0, q[2] - q[0]) * max(0.0, q[3] - q[1])
        if ar > area and q[2] - q[0] > 0.25:
            best, area = q, ar
    return best


READ_FLOOR = 0.03          # of the frame's height: the reference films' caption median. Display type under it is not display
ON_PRINT = 0.2             # share of a type block lying on words printed in the picture, past which neither reads


def on_print(rect, marks):
    """How much of a type block lies on what the picture already prints, 0-1. A corner on a logo is a touch; a word
    across a caption is on it."""
    area = max((rect[2] - rect[0]) * (rect[3] - rect[1]), 1e-9)
    hit = sum(max(0.0, min(rect[2], r[2]) - max(rect[0], r[0])) * max(0.0, min(rect[3], r[3]) - max(rect[1], r[1])) for r in {tuple(m) for m in marks})
    return min(1.0, hit / area)


def lines_that_read(ctx, iid, lines, fit_w, room, quiet=False):
    """Display lines at a size a phone can read, or none (P5, P3). A line too long for the frame at its size breaks
    in two at the word nearest its middle before it shrinks; the block then shrinks to the room its seat has. A line
    never goes under the floor: it stands at the floor and the others give up the room, then the smaller lines of a
    ladder go and the line that matters stays, and only a block whose main line cannot be read in its seat is left
    out, with a row in the meter (p5574: three one-line headlines written at 0.034 of the frame came out at 0.022,
    which the last look read as tiny three times; 13 Nov: four hook titles in four films were left out whole because
    their second line had been written a hair under the floor, or the seat was 0.01 short)."""
    W, H = ctx.W, ctx.H
    floor = READ_FLOOR * H

    def width_px(l, px):
        return text_px(l["t"], {"font": l["font"], "px": px, "track": l["track"]})

    def fitted(l):
        w = width_px(l, l["px"])
        return l["px"] if w / W <= fit_w else l["px"] * fit_w * W / w

    out = []
    for l in lines:
        words = str(l["t"]).split()
        if fitted(l) < (max(floor, 0.75 * l["px"]) if l.get("sized") else floor) and len(words) >= 2 and len(lines) == 1:
            k = min(range(1, len(words)), key=lambda j: abs(len(" ".join(words[:j])) - len(" ".join(words[j:]))))
            out += [{**l, "t": " ".join(words[:k])}, {**l, "t": " ".join(words[k:])}]
        else:
            out.append(l)

    def solve(rows):
        """Sizes for these lines inside the width and the room, none under the floor, or None."""
        px = [max(fitted(l), floor) for l in rows]
        if any(width_px(l, p) / W > fit_w * 1.03 for l, p in zip(rows, px)):
            return None                         # at the floor it is still wider than its seat
        if not (isinstance(room, (int, float)) and room > 0):
            return px
        for _ in range(8):
            tall = sum(px) * 1.02 / H
            if tall <= room + 1e-6:
                return px
            free = [i for i, p in enumerate(px) if p > floor + 0.5]
            if not free:
                return None
            over = (tall - room) * H / 1.02
            give = sum(px[i] - floor for i in free)
            k = min(1.0, over / max(give, 1e-6))
            px = [p - (p - floor) * k if i in free else p for i, p in enumerate(px)]
        return px if sum(px) * 1.02 / H <= room + 1e-6 else None

    rows, px = out, solve(out)
    if px is None and len(out) > 1:             # a ladder with a line too long for its seat breaks that line in two first
        wide = max(out, key=lambda l: width_px(l, max(fitted(l), floor)))
        words = str(wide["t"]).split()
        if len(words) >= 2 and width_px(wide, floor) / W > fit_w:
            k = min(range(1, len(words)), key=lambda j: abs(len(" ".join(words[:j])) - len(" ".join(words[j:]))))
            at = out.index(wide)
            broken = out[:at] + [{**wide, "t": " ".join(words[:k])}, {**wide, "t": " ".join(words[k:])}] + out[at + 1:]
            got = solve(broken)
            if got is not None:
                rows, px = broken, got
    if px is None and len(out) > 1:             # a ladder too tall for its seat keeps the line that matters
        main = max(out, key=lambda l: l["px"])
        rows, px = [main], solve([main])
        if px is not None and not quiet:
            ctx.taste_rows.append({"id": iid, "did": "repaired", "dept": "display", "why": "its seat holds one line: the large line stands, the small one went"})
    if px is None and quiet:
        return None
    if px is None:
        tall = sum(max(fitted(l), floor) for l in out) * 1.02 / H
        why = (f"its seat has room for {room:.2f} of the frame and it stands {tall:.2f} tall" if isinstance(room, (int, float)) and room > 0 and tall > room
               else "the line is longer than its seat at its size")
        ctx.taste_rows.append({"id": iid, "did": "thinned", "dept": "display", "why": why + ": shrunk that far it would not read. Over a found picture or a page it has the whole frame"})
        return None
    for l, p in zip(rows, px):
        l["px"] = int(p)
    return rows


def off_take(ctx, a, z):
    """Where a display line that lands at frame a stops being over a found picture or a page, or None when it lands
    on the take. Seats are solved for the take (heads, the caption, what the upload prints); a line over a picture
    that takes the whole frame has that picture's own room, and it labels that picture: it leaves with it."""
    s = shot_at(ctx, a)
    if s is None or is_take(s.get("pic")):
        return None
    end = s["f"][1]
    while end < ctx.dur_f:
        q = shot_at(ctx, end)
        if q is None or is_take(q.get("pic")):
            break
        end = q["f"][1]
    if end >= z:
        return z
    return end if end - a >= 12 else None


def seat_for(ctx, iid, opt, a, z):
    """(opt, z) for a seated display line. Over the take: the film's seat, as the gate set it. Over a found picture
    or a page: the seat the Score wrote, to the end of that picture. With no seat on the take and no picture under
    it, the line is left out and the meter says where it could stand."""
    if not ctx.governed or "_want" not in opt:
        return opt, z
    end = off_take(ctx, a, z)
    if end is not None:
        opt = {k: v for k, v in opt.items() if k not in ("_room", "fit", "seat")}
        if opt.get("_fit0") is not None:
            opt["fit"] = opt["_fit0"]
        want = opt.get("_want")
        if getattr(ctx, "hook_page", None) and getattr(ctx, "hook_page", None) == ".".join(iid.split(".")[:2]):
            opt["seat"] = [0.5, 0.44]           # the opening title page: the title is the frame, centred
            opt["_page_title"] = True
        elif isinstance(want, (list, tuple)) and len(want) == 2 and all(isinstance(v, (int, float)) for v in want):
            opt["seat"] = [clamp(float(want[0]), 0.2, 0.8), clamp(float(want[1]), 0.16, 0.74)]
        return opt, end
    if opt.get("_noseat"):
        ctx.taste_rows.append({"id": ".".join(iid.split(".")[:2]), "did": "thinned", "dept": "display",
                               "why": "no place on the take clears the face, the caption and the safe area: this line stands over a found picture or a page, not over the take"})
        return None, z
    return opt, z


def end_unit(ctx, gid, f):
    """A display line or label leaves at frame f: everything its gesture drew, the words still waiting for their
    ink, and the place it held. Used when the next label of a film takes its seat."""
    def cut(it):
        p, q = it["f"]
        if q <= f:
            return it
        n = f - p
        for k in ("x", "y", "op", "s", "blur", "rect"):
            if isinstance(it.get(k), list) and it[k] and isinstance(it[k][0], (int, float, list)) and len(it[k]) > n and k != "rect":
                it[k] = it[k][:max(n, 1)]
        it["f"] = [p, f]
        return it
    mine = lambda i: i == gid or str(i).startswith(gid + ".")
    ctx.items = [cut(it) for it in ctx.items if not (mine(it["id"]) and it["f"][0] >= f)]
    if ctx.waiting is not None:
        rows = []
        for r in ctx.waiting:
            iid, t, v, x, y, a, end, ink, role, chunk, region, extra = r
            if mine(iid) and end > f:
                if a >= f:
                    ctx.byid.pop(iid, None)
                    continue
                extra = dict(extra)
                for k in ("op", "s", "blur"):
                    if isinstance(extra.get(k), list):
                        extra[k] = extra[k][:max(f - a, 1)]
                x = x[:max(f - a, 1)] if isinstance(x, list) else x
                y = y[:max(f - a, 1)] if isinstance(y, list) else y
                r = (iid, t, v, x, y, a, f, ink, role, chunk, region, extra)
                ctx.byid[iid] = {"f": [a, f]}
            rows.append(r)
        ctx.waiting = rows
    ctx.blocks = [(bl[0], min(bl[1], f), *bl[2:]) if len(bl) > 3 and bl[3] == gid else bl for bl in ctx.blocks]
    ctx.blocks = [bl for bl in ctx.blocks if bl[1] > bl[0]]
    for it in ctx.items:
        if mine(it["id"]) and it["id"] in ctx.byid:
            ctx.byid[it["id"]] = it


HOOK_PAGE_F = 30


def title_page(ctx, iid, a, z, b):
    """The hook title has no place on the take: the film opens on a page for a second, the title on it, the voice
    already running, and cuts to the person. One page, at the film's first frame only (TASTE 4.2: a page holds the
    frame 1 s while a person talks). Returns the frame the page ends on, or None when this is not the film's opening
    title. 13 Nov: with no seat the title was left out, and the film's first second was the upload as it was."""
    if not ctx.governed or a > 3 or iid.count(".") != 1 or getattr(ctx, "hook_page", None):
        return None
    s0 = shot_at(ctx, a)
    if s0 is None or not is_take(s0.get("pic")):
        return None
    end = min(z, a + HOOK_PAGE_F, ctx.dur_f)
    if end - a < 12:
        return None
    dark = ctx.dna.get("plate")
    plate = plate_of(ctx, "plate") if (is_hex(dark) or isinstance(dark, dict)) else _plate("#0B0B0D", None, 0)
    carve(ctx, a, end, emit_ref(ctx, b, {"kind": "plate", "plate": plate}, a, end, f"{iid}.page"))
    ctx.hook_page = iid
    ctx.taste_rows.append({"id": iid, "did": "repaired", "dept": "display", "why": "no place on the take holds this title: it opens the film on a page for 1 s and the take follows"})
    return end


def span_seated(ctx, iid, text, a, z, opt):
    """A display line over the take, seated for the frames it stands. The film's seat is kept where it holds the line
    at four fifths of its written size or more: on one take that is the same place for the whole film. Where it does
    not (a clip of several shots, a head that is in frame for part of the film only, a film with no seat at all), the
    line takes the open ground under it while it is on screen, nearest the place the Score wrote, with that room
    (taste.span_seat). A film still uses no more display seats than its footage carries: past the count, the line
    takes the best of the seats already used."""
    from . import taste
    lines = display_lines(ctx, text, opt)
    if not lines or z <= a:
        return opt
    gid = ".".join(iid.split(".")[:2])
    if gid in ctx.span_seats:               # a counter is one gesture: every step of it stands where its first step was seated
        got = ctx.span_seats[gid]
        return opt if got is None else {**{key: v for key, v in opt.items() if key != "_noseat"}, **got}
    ctx.span_seats[gid] = None
    if z - a < ctx.fps // 2:
        z = min(a + 2 * ctx.fps, ctx.dur_f)       # a step of a counter: the place has to hold while the counter runs
    full = 1 - 2 * SAFE_X - 0.02
    peak = 1 / SETTLE["s_inf"] if opt.get("in") == "settle" else 1.0
    need_w = min(full, max(text_px(l["t"], {"font": l["font"], "px": l["px"], "track": l["track"]}) for l in lines) / ctx.W * peak)
    need_h = sum(l["px"] for l in lines) * 1.02 / ctx.H
    noseat = bool(opt.get("_noseat"))
    seat0 = opt.get("seat") if isinstance(opt.get("seat"), (list, tuple)) and len(opt.get("seat")) == 2 else None
    have = 0.0 if noseat else min(1.0, float(opt.get("fit", full)) / max(need_w, 1e-6), float(opt.get("_room") or 0.2) / max(need_h, 1e-6))
    used = ctx.used_seats

    def near(p, q):
        return abs(float(p[0]) - float(q[0])) < 0.25 and abs(float(p[1]) - float(q[1])) < 0.1

    def note(seat):
        if seat is not None and all(isinstance(v, (int, float)) for v in seat) and not any(near(q, seat) for q in used):
            used.append([float(seat[0]), float(seat[1])])
    want = opt.get("_want") if isinstance(opt.get("_want"), (list, tuple)) and len(opt.get("_want")) == 2 and all(isinstance(v, (int, float)) for v in opt.get("_want")) else None
    n = {"T": 2, "M": 3, "P": 3, "L": 2}.get(ctx.plan.get("cls") or "T", 2)
    got = taste.span_seat(ctx, a, z, want or seat0, (need_w, need_h), keep=used, only=len(used) >= n)
    # the place the Score wrote, when it is open while the line stands: the Director looked at the frame and set the
    # title beside what the shot is about; the film's seat knows heads and print, not the subject
    written = got is not None and want is not None and got[2] >= 0.8 and near(got[0], want) and not (seat0 is not None and not noseat and near(seat0, want))
    # a place that holds under three quarters of the written size is not a place for it: a title shrunk that far is
    # what the last look calls tiny (p5584: a two-line title in a strip 0.09 tall beside his head)
    if not written and (have >= 0.8 or got is None or got[2] < 0.75 or got[2] <= have + 0.1):
        if not noseat:
            note(seat0)
        return opt
    seat, room, k = got
    opt = {key: v for key, v in opt.items() if key != "_noseat"}
    fit0 = opt.get("_fit0")
    opt["seat"], opt["_room"] = list(seat), room[1]
    opt["fit"] = min(float(fit0) if isinstance(fit0, (int, float)) and not isinstance(fit0, bool) else full, room[0])
    ctx.span_seats[gid] = {"seat": opt["seat"], "_room": opt["_room"], "fit": opt["fit"]}
    note(seat)
    ctx.taste_rows.append({"id": iid, "did": "repaired", "dept": "display",
                           "why": f"seated for the {(z - a) / ctx.fps:.1f} s it stands, at [{seat[0]:.2f},{seat[1]:.2f}] with room {room[1]:.2f} of the frame tall: "
                                  + ("the film has no seat that holds for the whole of it" if noseat else "the place the Score wrote is open while it stands" if written and have >= 0.8
                                     else f"the film's seat held {have:.0%} of its size")})
    return opt


def display(ctx, iid, text, a, z, opt, b=None):
    """Display type: fits the frame, sits on a seat, lands on its own clock. Returns its rect."""
    W, H = ctx.W, ctx.H
    opt = opt or {}
    if ctx.governed and "_want" in opt and off_take(ctx, a, z) is None:
        opt = span_seated(ctx, iid, text, a, z, opt)
        # over the take: does it have a place there at a size that reads? If not, and it is the film's opening
        # title, the film opens on a page (title_page); anywhere else the meter says where it could stand
        fits = not opt.get("_noseat")
        if fits:
            peak0 = 1 / SETTLE["s_inf"] if opt.get("in") == "settle" else 1.0
            fits = lines_that_read(ctx, iid, [dict(l) for l in display_lines(ctx, text, opt)], float(opt.get("fit", 1 - 2 * SAFE_X - 0.02)) / peak0, opt.get("_room"), quiet=True) is not None
        if not fits:
            end = title_page(ctx, iid, a, z, b)
            if end is not None:
                z = end
    opt, z = seat_for(ctx, iid, opt, a, z)
    if opt is None:
        return None
    lines = display_lines(ctx, text, opt)
    if not lines or z <= a:
        return None
    if opt.get("_page_title"):                  # written for a small seat on the take: on its own page it stands at title size
        top = max(l["px"] for l in lines)
        k = max(1.0, 0.075 * H / max(top, 1))
        for l in lines:
            l["px"] = int(l["px"] * k)
    if ctx.governed:
        from . import taste
        if any(taste.machine_text(l["t"]) for l in lines):       # P10: a stamp off a board is not a word of this film
            ctx.rejects.append((0, f"{iid} dropped: {' '.join(str(l['t']) for l in lines)[:40]!r} is a reference, not a word of this film", ""))
            return None
        for l in lines:
            l["px"] = int(min(l["px"], 0.25 * H))                 # P5: display type at most a quarter of the frame
    seat = opt.get("seat")
    kind = opt.get("in", "cut")
    if kind not in ENTRANCES:
        ctx.find(f"entrance {kind!r} unknown: {' '.join(ENTRANCES)}")
        kind = "cut"
    if kind == "letters" and any(joined_script(l["t"]) for l in lines):
        kind = "cut"       # letters of a joined script don't exist one at a time
    # the size that has to fit is the largest it reaches, or a settle lands past both edges (p5302, 19 s)
    peak = 1 / SETTLE["s_inf"] if kind == "settle" else 1.0
    fit_w = float(opt.get("fit", 1 - 2 * SAFE_X - 0.02)) / peak
    solve = False
    if seat == "free":
        fr = free_seat(ctx, a)
        seat = [(fr[0] + fr[2]) / 2, (fr[1] + fr[3]) / 2] if fr else None
        if fr:
            fit_w = min(fit_w, (fr[2] - fr[0]) * 0.92)
    if not (isinstance(seat, (list, tuple)) and len(seat) == 2):
        seat, solve = [0.5, 0.42], True
    if ctx.governed:
        lines = lines_that_read(ctx, iid, lines, fit_w, opt.get("_room"))
        if not lines:
            return None
    for l in lines:
        v = {"font": l["font"], "px": l["px"], "track": l["track"]}
        w = text_px(l["t"], v)
        if w / W > fit_w:
            l["px"] = max(24, int(l["px"] * fit_w * W / w))
        key = f"big{l['font'][:-4]}_{l['px']}_{int(l['track'])}"
        ctx.voices.setdefault(key, {"font": l["font"], "px": l["px"], "track": l["track"], **voice_light(ctx)})
        l["v"] = key
        l["w"] = text_px(l["t"], ctx.voices[key]) / W
    lead_k = float(opt.get("lead", 1.02))
    ys, y, below = [], 0.0, 0.0
    for i, l in enumerate(lines):
        q = shaped(l["font"], l["t"]) if joined_script(l["t"]) else None
        top = q[1] if q else 0.74
        if i == 0:
            y += top * l["px"] / H
        else:
            y += max(lead_k * l["px"] / H, below + top * l["px"] / H + 0.006 if q else 0.0)
            if ctx.governed and opt.get("lead") is None and min(l["px"], lines[i - 1]["px"]) < 0.8 * max(l["px"], lines[i - 1]["px"]):
                y += 0.22 * min(l["px"], lines[i - 1]["px"]) / H        # a ladder's small line stands clear of the large one: with the edge drawn they touched
        below = q[2] * l["px"] / H if q else 0.0
        ys.append(y)
    block_h = ys[-1] + below
    block_w = max(l["w"] for l in lines)
    x0 = clamp(float(seat[0]), block_w * peak / 2 + 0.02, 1 - block_w * peak / 2 - 0.02)
    y_mid = float(seat[1])
    probe = [x0 - block_w / 2, y_mid - block_h / 2, x0 + block_w / 2, y_mid + block_h / 2]
    on_marks = [r for r in mark_rects(ctx, a, z) if rects_touch(probe, r)]
    # Nor on a head: without a matte to set it behind him, a word written on his seat covers his face
    # (p5302: LAR NOVO MÓVEIS across his hair at 19.0 and 22.25 s).
    on_head = not (opt.get("behind") and ctx.matte) and any(rects_touch(probe, r) for f in sorted({a, (a + z) // 2, max(a, z - 1)})
                                                             for r in head_rects(ctx, shot_at(ctx, f)))
    # Nor on other type: p5565 drew FORENSIC SCIENTIST over PILOT and "next generation" over WELCOME, two
    # bigs of one beat on the same free seat. A seat another type item holds at the same time is taken.
    on_type = [bl for bl in ctx.blocks if bl[0] < z and bl[1] > a and rects_touch(probe, bl[2])]
    if solve or on_marks or on_head or on_type:      # words already printed in the picture are the picture's. Type doesn't land on them
        y_was = y_mid
        x0, y_mid = solve_seat(ctx, [x0, y_mid], block_w, block_h, a, z)
        if on_type:
            ctx.traffic.append({"id": iid, "did": "moved", "dy": round(y_mid - y_was, 3), "at": "seat"})
        probe = [x0 - block_w / 2, y_mid - block_h / 2, x0 + block_w / 2, y_mid + block_h / 2]
        if on_marks and any(rects_touch(probe, r) for r in mark_rects(ctx, a, z)):
            ctx.find(f"{iid}: type sits on words printed in the picture, and no clear seat exists. Another picture, or a card")
            opt = {**opt, "pool": opt.get("pool") or 0.72}
    y_top = y_mid - block_h / 2
    y_top += dodge_keepout(x0 - block_w / 2, y_top, x0 + block_w / 2, y_top + block_h)
    y_top = clamp(y_top, 0.02, max(0.02, 0.98 - block_h))
    n = z - a
    out_rect = [x0 - block_w / 2, y_top, x0 + block_w / 2, y_top + block_h]
    asked = (ctx.devices.get(iid) or ctx.devices.get(iid.rsplit(".", 1)[0]) or {}).get("bound")
    if ctx.governed and not asked and on_print(out_rect, mark_rects(ctx, a, z)) >= ON_PRINT:
        # P6: the upload's own words are the picture's. Type that ends on them makes both unreadable, and where it
        # ends is read after every move: p5572's RELIEF was solved to a clear seat above the creator's caption,
        # then pushed down out of the platform's top bar and onto it. With no clear seat it is left out
        ctx.taste_rows.append({"id": iid, "did": "thinned", "dept": "display", "why": "it lands on words the upload prints, and this frame has no clear seat for it"})
        return None
    # a block is as tall as its ink. A tall condensed face stands well above 0.74 em
    q0 = shaped(lines[0]["font"], lines[0]["t"]) if joined_script(lines[0]["t"]) else None
    ink_top = y_top + ys[0] - (q0[1] if q0 else cap_height(lines[0]["font"])) * lines[0]["px"] / H
    ctx.blocks.append((a, z, [out_rect[0], min(ink_top, y_top), out_rect[2], out_rect[3] + 0.006], ".".join(iid.split(".")[:2])))
    pool = opt.get("pool")
    if pool is None and not opt.get("blend") and not opt.get("behind") and not is_hex(opt.get("hl")) and under_bright(ctx, a, z):
        pool = 0.5
    ink0 = opt.get("ink") if is_hex(opt.get("ink")) else lines[0].get("ink")
    mid = (a + z) // 2
    ink1 = ink0 if is_hex(ink0) else ink_over(ctx, shot_at(ctx, mid), mid, out_rect)
    if pool and opt.get("pool") is None and is_hex(ink1) and luma(norm_hex(ink1)) < 0.5:
        pool = None       # a pool lifts light type. Under dark ink it only muddies the page
    if pool:
        hw, hh = block_w * 0.75 + 0.03, block_h * 1.1 + 0.03
        ctx.add({"id": f"{iid}.pool", "k": "flash", "mode": "pool", "f": [a, z], "a": float(pool), "z": 0,
                 "rect": [clamp(x0 - hw, 0, 1), clamp(y_top + block_h / 2 - hh, 0, 1), clamp(x0 + hw, 0, 1), clamp(y_top + block_h / 2 + hh, 0, 1)]})
    hl = opt.get("hl")
    align = opt.get("align", "centre")
    for i, l in enumerate(lines):
        voice = ctx.voices[l["v"]]
        px, w = l["px"], l["w"]
        x = x0 - block_w / 2 if align == "left" else x0 + block_w / 2 - w if align == "right" else x0 - w / 2
        y = y_top + ys[i]
        ink = l.get("ink") or opt.get("ink")
        if is_hex(hl):
            grow = min(n, 9)
            rects = [[x - 0.008, y - 0.8 * px / H, x - 0.008 + (w + 0.016) * (j + 1) / grow, y + 0.1 * px / H] for j in range(grow)]
            rects += [rects[-1]] * (n - grow)
            ctx.add({"id": f"{iid}.hl{i}", "k": "flash", "mode": "fill", "c": norm_hex(hl), "f": [a, z], "rect": rects, "z": 2})
        if kind == "letters":
            rng = rng_for(ctx.seed, "letters", iid, i)
            xc = x * W
            for j, ch in enumerate(l["t"]):
                cw = text_px(ch, voice)
                if ch.strip():
                    d = rng.randint(0, 4)
                    if a + d < z:
                        emit_word(ctx, f"{iid}.{i}.{j}", ch, l["v"], xc / W, y, a + d, z, ink, "big", z=1, region=[x, y - 0.8 * px / H, x + w, y], **({"_blk": list(out_rect)} if ctx.governed else {}))
                xc += cw + voice["track"] * voice["px"] / 100
            continue
        extra = entrance(kind, n, y)
        yy = extra.pop("y", y)
        if opt.get("blend"):
            extra["blend"] = opt["blend"]
        if opt.get("behind") and ctx.matte and not hidden_line(ctx, [x, y - 0.74 * px / H, x + w, y], a, z):
            extra["behind"] = True
        if opt.get("reflect"):
            extra["reflect"] = float(opt["reflect"])
        if opt.get("fill") is not None:
            refs = parse_pics(ctx, opt["fill"], iid)
            if refs:
                pf = picture_fields(ctx, b, dict(refs[0]), a, z)
                extra["fill"] = {k: pf[k] for k in ("pic", "src", "crop", "plate", "grade", "treat") if k in pf}
                ink = ink if is_hex(ink) else (ink1 if is_hex(ink1) else "#FFFFFF")
        if ctx.governed and block_h >= 1e-6:
            extra["_blk"] = list(out_rect)          # one ink for the block: its lines are read together
        emit_word(ctx, f"{iid}.{i}", l["t"], l["v"], x, yy, a, z, ink, "big", z=1, region=[x, y - 0.8 * px / H, x + w, y], **extra)
    return out_rect


def hidden_line(ctx, r, a, z):
    """Would the person stand in front of most of this line? Then it reads in front of him: a name lost behind a chest says nothing."""
    boxes = subject_rects(ctx, "body", a, min(z, a + 90))
    if not boxes:
        return False
    worst = 0.0
    for q in boxes[::6]:
        if r[1] < q[3] and q[1] < r[3]:
            worst = max(worst, max(0.0, min(r[2], q[2]) - max(r[0], q[0])) / max(r[2] - r[0], 1e-3))
    return worst > 0.62


def display_land(ctx, b, at):
    """The third clock: 0 to 6 frames after the voice, median 3. Long words land near their
    end (ATLAS 1.6, TC4). A word anchor is the only anchor this moves."""
    f = frame_of(ctx, at, b)
    if f is None:
        return None
    m = ANCHOR_RE.match(at.strip()) if isinstance(at, str) else None
    if m and m["w"] is not None and not m["off"] and not m["we"]:
        w = int(m["w"])
        e = frame_of(ctx, f"w{w}.e", b)
        r = rng_for(ctx.seed, "land", b.id if b else "", at)
        lag = r.choice([0, 1, 2, 3, 3, 3, 4, 5, 6])
        if e is not None and e - f >= int(0.5 * ctx.fps):
            return max(f, e - 3)
        return f + lag
    return f


# ---------------------------------------------------------------- gestures

GESTURES = {}


def gesture(name, card, needs=(), degrade=None, lower="hard_cut"):
    """A gesture is a seeded function with a data card. It expands to L1 items."""
    def deco(fn):
        GESTURES[name] = {"fn": fn, "card": card, "needs": needs, "degrade": degrade, "lower": lower}
        return fn
    return deco


def g_snd(ctx, b, g, f):
    """A gesture's own sound, with its peak on the gesture's landing."""
    if g.get("snd") and f is not None:
        ctx.g_snd.append((b, g["snd"], f, f"{g.get('_gid')}"))


def pics_of(ctx, b, g):
    v = g.get("pic", g.get("pics"))
    return parse_pics(ctx, v, b.id) if v is not None else list(b.pics)


def each_frames(ctx, b, at):
    """'each_cut', or one anchor, or a list of anchors, to frames."""
    if at == "each_cut":
        return [s["f"][0] for s in ctx.shots if b.pf0 <= s["f"][0] < b.pf1 and s["f"][0] > 0]
    if isinstance(at, list):
        out = [frame_of(ctx, x, b) for x in at]
        return [f for f in out if f is not None]
    if isinstance(at, str) and at.startswith("every "):
        try:
            step = int(at.split()[1].rstrip("f"))
        except (ValueError, IndexError):
            step = 8
        return list(range(b.pf0, b.pf1, max(1, step)))
    f = frame_of(ctx, at, b)
    if f is None:
        ctx.find(f"{b.id}: anchor {at!r} resolves to nothing")
    return [] if f is None else [f]


def box_at(ctx, key, t):
    """The subject's box at a source second, smoothed over the nearest samples."""
    rows = ctx.boxes.get(key)
    if rows is None:
        rows = ctx.boxes[key] = [(r["t"], r[key]) for r in (ctx.perc.get("samples") or []) if r.get(key)]
    if not rows:
        return None
    i = bisect.bisect_left(rows, (t,))
    near = rows[max(0, i - 4):i + 4]
    tot, acc = 0.0, [0.0, 0.0, 0.0, 0.0]
    for rt, b in near:
        w = math.exp(-((rt - t) / 0.25) ** 2 / 2)
        tot += w
        acc = [x + w * y for x, y in zip(acc, b)]
    return [x / tot for x in acc] if tot > 1e-4 else None


def subject_rects(ctx, key, a, z, pad=0.0):
    """Where the subject is on screen, one rect per frame of [a,z), through whatever crop is
    playing. Frames with no sight of him hold the last one. None if he is never seen."""
    out, last = [], None
    for f in range(a, z):
        s = shot_at(ctx, f)
        r = None
        if s and is_take(s.get("pic")) and "src" in s:
            n, i = s["f"][1] - s["f"][0], f - s["f"][0]
            t = s["src"][0] + (s["src"][1] - s["src"][0]) * i / max(1, n)
            q = box_at(ctx, key, t)
            if q:
                c = s["crop"]
                c = c[i] if isinstance(c[0], list) else c
                r = [(q[0] - c[0]) / (c[2] - c[0]) - pad, (q[1] - c[1]) / (c[3] - c[1]) - pad,
                     (q[2] - c[0]) / (c[2] - c[0]) + pad, (q[3] - c[1]) / (c[3] - c[1]) + pad]
                r = [clamp(v, 0, 1) for v in r]
        out.append(r if r else last)
        last = r or last
    if last is None:
        return None
    first = next(x for x in out if x)
    return [x or first for x in out]


def rect_of(ctx, b, g, f, n=1):
    """A gesture's rect: numbers, or 'face' or 'body' on the frame, one rect per frame when
    it stays long enough to follow him."""
    r = g.get("rect")
    if isinstance(r, (list, tuple)) and len(r) == 4:
        return [float(x) for x in r]
    if r in ("face", "body"):
        p = subject_rects(ctx, r, f, f + max(1, n), pad=0.04 if r == "face" else 0.015)
        if p:
            return p if n > 2 else p[0]
        for fr in face_rects(ctx, shot_at(ctx, f)):
            return [clamp(fr[0] - 0.04, 0, 1), clamp(fr[1] - 0.04, 0, 1), clamp(fr[2] + 0.04, 0, 1), clamp(fr[3] + 0.04, 0, 1)]
        # No face and no body on this frame. A made-up box registered to nothing drew a blank cream
        # rectangle over p5363's cartoon at 0.08 s: the caller falls to its whole-frame form instead.
        return None
    return None


@gesture("hold", 'pic cam at. One picture across the span', lower="overlay")
def g_hold(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: hold has no span")
    refs = pics_of(ctx, b, g) or b.pics
    ref = dict(refs[0])
    if g.get("cam"):
        ref["cam"] = g["cam"]
    carve(ctx, a, z, emit_ref(ctx, b, ref, a, z, g["_gid"]))
    g_snd(ctx, b, g, a)


@gesture("cuts", 'pic[] pulse at. Pictures on a pulse', lower="overlay")
def g_cuts(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: cuts has no span")
    refs = pics_of(ctx, b, g) or b.pics
    rng = rng_for(ctx.seed, g["_gid"])
    med = g.get("pulse")
    lens = pulse_lengths(ctx, z - a, float(med), rng) if med else jitter_split(z - a, len(refs), rng)
    t, out = a, []
    for j, d in enumerate(lens):
        out += emit_ref(ctx, b, refs[j % len(refs)], t, t + d, f"{g['_gid']}.s{j}")
        t += d
    carve(ctx, a, z, out)
    g_snd(ctx, b, g, a)


@gesture("salvo", 'pic[] at f:[2,3] n treat every. Rapid frames under a still word', lower="overlay")
def g_salvo(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: salvo has no span")
    refs = pics_of(ctx, b, g) or b.pics
    rng = rng_for(ctx.seed, g["_gid"])
    fr = g.get("f", [2, 3])
    lo, hi = (fr, fr) if isinstance(fr, (int, float)) else (int(fr[0]), int(fr[-1]))
    n = g.get("n")
    t, j, out = a, 0, []
    every = int(g.get("every", 1) or 1)
    while t < z:
        d = rng.randint(int(lo), int(hi))
        last = isinstance(n, int) and j == n - 1
        q = z if last or z - t - d < lo else t + d
        ref = dict(refs[j % len(refs)])
        ref.pop("d", None)
        if g.get("treat") and j % every == 0:
            ref["treat"] = g["treat"]
        ref["cam"] = None
        if ref["kind"] == "main" and ref.get("sync"):
            # a salvo of one take jumps around it, each frame a new moment and framing
            ref = {**ref, "sync": False, "t0": rng.uniform(0, max(0.0, ctx.src_dur - 1.0))}
        out += emit_ref(ctx, b, ref, t, q, f"{g['_gid']}.s{j}")
        t = q
        j += 1
    carve(ctx, a, z, out)
    g_snd(ctx, b, g, a)


@gesture("page", 'plate t|w at size seat in. A plate takes the frame, words on it', lower="overlay")
def g_page(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: page has no span")
    if g.get("_max"):
        z = min(z, a + int(g["_max"]))
    ref = {"kind": "plate", "plate": plate_of(ctx, g.get("plate", "plate"))}
    carve(ctx, a, z, emit_ref(ctx, b, ref, a, z, g["_gid"]))
    text = g.get("t") or big_words(ctx, g)
    if text:
        opt = dict(g)
        opt.setdefault("seat", [0.5, 0.5])
        display(ctx, f"{g['_gid']}.t", text, a, z, opt, b)
    g_snd(ctx, b, g, a)


def big_words(ctx, g):
    if g.get("w") is None:
        return ""
    try:
        x, y = parse_range(g["w"])
    except ValueError:
        return ""
    return " ".join(t for t in (word_text(ctx, w) for w in range(x, y + 1)) if t)


@gesture("big", 't|w at d size seat font in hl pool blend align lead reflect fill. Display type on the third clock. t may be a size ladder: [{"t":"get","size":.06},{"t":"that you","size":.015}]', lower="text")
def g_big(ctx, b, g, i):
    text = g.get("t") or big_words(ctx, g)
    if not text:
        return ctx.find(f"{b.id}: big has no text")
    at = g.get("at")
    if at is None and g.get("w") is not None:
        at = f"w{parse_range(g['w'])[0]}"
    a0, z0 = span_of(ctx, b, at) if isinstance(at, str) and (".." in at or whole_span(ctx, at)) else (None, None)
    a = a0 if a0 is not None else display_land(ctx, b, at) if at is not None else b.f0
    if a is None:
        return ctx.find(f"{b.id}: big can't place {at!r}")
    z = z0 if z0 is not None else a + int(g["d"]) if isinstance(g.get("d"), (int, float)) else b.pf1
    display(ctx, g["_gid"], text, a, min(max(z, a + 3), ctx.dur_f), g, b)
    g_snd(ctx, b, g, a)


@gesture("line", 't at times[] font size seat ghost in reflect. A sung or spoken line set whole, each word inking on its second, right to left in a joined script', lower="text")
def g_line(ctx, b, g, i):
    text = str(g.get("t") or "").strip()
    if not text:
        return ctx.find(f"{b.id}: line has no text")
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: line has no span")
    words = text.split()
    spec = g.get("font") or ctx.dna.get("big")
    font, sz, track = parse_font_spec(spec) if spec else (None, None, None)
    font = script_font(font or "InterDisplay-Bold.ttf", text)
    rtl = joined_script(text)
    track = 0 if rtl else (-1 if track is None else track)
    size = float(g.get("size") or sz or 0.05)
    px = int(round(size * ctx.H))
    W, H = ctx.W, ctx.H
    fit = float(g.get("fit", 1 - 2 * SAFE_X - (0.05 if rtl else 0.02)))
    while True:         # wrap, right to left when the script runs that way, and shrink only if one word can't fit
        key = f"line{font[:-4]}_{px}_{int(track)}"
        ctx.voices.setdefault(key, {"font": font, "px": px, "track": track, **voice_light(ctx)})
        v = ctx.voices[key]
        gap = text_px(" ", v) if rtl else 0.28 * px
        ws = [text_px(w, v) for w in words]
        if max(ws) <= fit * W or px <= 24:
            break
        px = max(24, int(px * fit * W / max(ws)))
    rows, cur, cw = [], [], 0.0
    for n, w in enumerate(ws):
        if cur and (cw + gap + w) > fit * W:
            rows.append((cur, cw))
            cur, cw = [], 0.0
        cw += (gap if cur else 0) + w
        cur.append(n)
    rows.append((cur, cw))
    q = shaped(font, text) if rtl else None
    top, below = (q[1], q[2]) if q else (0.74, 0.0)
    step = max(1.02 * px / H, (top + below) * px / H + 0.008)
    block_h = (len(rows) - 1) * step + (top + below) * px / H
    block_w = max(c for _, c in rows) / W
    seat = g.get("seat")
    if seat == "free":
        fr = free_seat(ctx, a)
        seat = [(fr[0] + fr[2]) / 2, (fr[1] + fr[3]) / 2] if fr else None
    seat = seat if isinstance(seat, (list, tuple)) and len(seat) == 2 else [0.5, 0.5]
    x0 = clamp(float(seat[0]), block_w / 2 + SAFE_X, 1 - block_w / 2 - SAFE_X)
    y0 = float(seat[1]) - block_h / 2
    y0 += dodge_keepout(x0 - block_w / 2, y0, x0 + block_w / 2, y0 + block_h)
    y0 = clamp(y0, 0.02, max(0.02, 0.98 - block_h))
    rect = [x0 - block_w / 2, y0, x0 + block_w / 2, y0 + block_h]
    ctx.blocks.append((a, z, rect))
    times = g.get("times")
    ons = []
    for n in range(len(words)):
        f = None
        if isinstance(times, list) and n < len(times):
            t = times[n]
            f = frame_of(ctx, t if isinstance(t, str) else f"@{float(t):.3f}", b)
        ons.append(clamp(a + (z - a) * n // len(words) if f is None else f, a, z - 1))
    ghost = float(g.get("ghost", 0.0))
    kind = g.get("in", "blur")
    for r, (row, cw) in enumerate(rows):
        y = y0 + top * px / H + r * step
        x = (x0 * W + cw / 2) if rtl else (x0 * W - cw / 2)
        for n in row:
            if rtl:
                x -= ws[n]
            on = ons[n]
            ex = entrance(kind, z - on, y) if kind in ENTRANCES and kind != "letters" else {}
            yy = ex.pop("y", y)
            if g.get("reflect"):
                ex["reflect"] = float(g["reflect"])
            if ghost and on > a:
                pad = lambda k, rest: [k] * (on - a) + rest
                ex["op"] = pad(ghost, ex.get("op") or [1.0] * (z - on))
                if isinstance(yy, list):
                    yy = pad(yy[0], yy)
                for fld in ("s", "blur"):
                    if fld in ex:
                        ex[fld] = pad(ex[fld][0], ex[fld])
                emit_word(ctx, f"{g['_gid']}.w{n}", words[n], key, x / W, yy, a, z, g.get("ink"), "big", region=rect, **ex)
            else:
                emit_word(ctx, f"{g['_gid']}.w{n}", words[n], key, x / W, yy, on, z, g.get("ink"), "big", region=rect, **ex)
            x = x - gap if rtl else x + ws[n] + gap
    g_snd(ctx, b, g, a)




@gesture("card", 'pic at w h x y rot bg(plate|dim|none|hex) in(settle|cut|flicker) border shadow radius cap(edge|free) behind. A held picture that outlives cuts. The caption rests on its edge', lower="overlay")
def g_card(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: card has no span")
    refs = pics_of(ctx, b, g)
    ref = dict(refs[0]) if refs else {"kind": "plate", "plate": plate_of(ctx, "plate")}
    make_card(ctx, b, ref, a, z, g, g["_gid"], z_order=int(g.get("z", 1)))
    bg = g.get("bg", "none")
    if bg == "plate" or isinstance(bg, str) and (is_hex(bg) or bg in PLATE_NAMES):
        p = {"kind": "plate", "plate": plate_of(ctx, "plate" if bg == "plate" else bg)}
        carve(ctx, a, z, emit_ref(ctx, b, p, a, z, g["_gid"] + ".bg"))
    elif bg == "dim":
        dim = [s for s in ctx.shots if s["f"][0] < z and s["f"][1] > a]
        out = []
        for j, s in enumerate(dim):
            p, q = max(s["f"][0], a), min(s["f"][1], z)
            it = sub_item(s, p, q, f"{g['_gid']}.bg{j}")
            base = it.get("grade") or ctx.base_grade
            it["grade"] = {**base, "gain": base.get("gain", 1) * 0.42, "sat": base.get("sat", 1) * 0.7}
            it["blur"] = 18
            out.append(it)
        carve(ctx, a, z, out)
    g_snd(ctx, b, g, a)


def make_card(ctx, b, ref, a, z, g, gid, z_order=1):
    n = z - a
    kind = g.get("in", "settle")
    s_inf = float(g.get("s_inf", SETTLE["s_inf"]))
    w, h = float(g.get("w", 0.7)), float(g.get("h", 0.5))
    x, y = float(g.get("x", 0.5)), float(g.get("y", 0.42))
    pre = 1.0 / s_inf if kind == "settle" else 1.0
    W0, H0 = w * pre, h * pre           # the card lands big and settles to the size asked for
    # Inside the frame at its largest, tilt included: a card that lands past an edge reads as off-centre
    # (p5302's tilted wardrobe card cut at both edges, p5374's card cut on its landing frames). A card too
    # big to fit tilted is made just small enough to.
    rad = math.radians(abs(float(g.get("rot", 0) or 0)))
    ar = ctx.H / ctx.W
    hw = (W0 * math.cos(rad) + H0 * ar * math.sin(rad)) / 2
    hh = (H0 * math.cos(rad) + W0 / ar * math.sin(rad)) / 2
    # The type's side margin holds for a card too: at 0.49 a card landed 0.01 from both sides, edge to
    # edge on its landing frames (p5302's LAR NOVO MÓVEIS pop at 19.0 and 22.25 s).
    k = min(1.0, (0.5 - SAFE_X) / max(hw, 1e-6), 0.47 / max(hh, 1e-6))
    if k < 1.0:
        w, h, W0, H0, hw, hh = w * k, h * k, W0 * k, H0 * k, hw * k, hh * k
    x = clamp(x, hw + SAFE_X, 1 - hw - SAFE_X)
    y = clamp(y, hh + 0.03, 1 - hh - 0.03)
    y += dodge_keepout(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    it = {"id": gid, "k": "card", "f": [a, z], "z": z_order, "x": x, "y": y, "w": W0, "h": H0, "rot": float(g.get("rot", 0)), "op": 1,
          "_cap_edge": g.get("cap", "edge") == "edge"}
    if g.get("behind") and ctx.matte:
        it["behind"] = True
    it["s"] = [s_inf + (1 - s_inf) * math.exp(-i / SETTLE["tau"]) for i in range(n)] if kind == "settle" else 1
    if kind == "flicker":
        it["op"] = (FLICKER + [1.0] * n)[:n]
    r = g.get("radius", 0.012)
    if r:
        it["radius"] = float(r)
    if g.get("border", 1):
        bw = g["border"] if isinstance(g.get("border"), dict) else {"w": 0.012, "c": "#FFFFFF"}
        it["border"] = bw
    if g.get("shadow", 1):
        it["shadow"] = g["shadow"] if isinstance(g.get("shadow"), dict) else {"dx": 0.006, "dy": 0.01, "blur": 0.02, "a": 0.5}
    src = None
    if ref.get("kind") == "main" and ref.get("sync"):
        p = follow_spine(ctx, a, z)
        src = [p[0][2], p[0][2] + n / ctx.fps]
    it.update(picture_fields(ctx, b, ref, a, z, W=int(W0 * ctx.W), H=int(H0 * ctx.H), src=src))
    ctx.add(it)
    return it


@gesture("band", 'pic at h y. A 2.35 band on black. Hides a soft clip, then full frame lands with force', lower="overlay")
def g_band(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: band has no span")
    refs = pics_of(ctx, b, g) or b.pics
    black = {"kind": "plate", "plate": plate_of(ctx, g.get("plate", "#000000"))}
    black["plate"]["grain"] = 0
    carve(ctx, a, z, emit_ref(ctx, b, black, a, z, g["_gid"] + ".bg"))
    make_card(ctx, b, dict(refs[0]), a, z, {"w": 1.0, "h": float(g.get("h", 0.3)), "x": 0.5, "y": float(g.get("y", 0.5)),
                                            "in": "cut", "border": 0, "shadow": 0, "radius": 0, "cap": "free"}, g["_gid"], z_order=1)
    g_snd(ctx, b, g, a)


@gesture("neg_card", 'at f rect c. A negative registered to the frame, 1-3 frames', degrade="flash:neg")
def g_neg(ctx, b, g, i):
    n = int(g.get("f", 2))
    for j, f in enumerate(each_frames(ctx, b, g.get("at", "each_cut"))):
        g2 = dict(g)
        if "rect" not in g2:
            g2["rect"] = "face"
        it = {"id": f"{g['_gid']}.{j}", "k": "flash", "f": [f, f + n], "mode": "neg"}
        r = rect_of(ctx, b, g2, f, n)
        if r:
            it["rect"] = r
        if is_hex(g.get("c")):
            it["c"] = norm_hex(g["c"])
        ctx.add(it)
        if j == 0:
            g_snd(ctx, b, g, f)


@gesture("flash", 'at f mode(white|tint|neg|threshold|burn|complement|posterise|halftone|mosaic) c rect th. Under the type', degrade=None)
def g_flash(ctx, b, g, i):
    n = int(g.get("f", 2))
    mode = g.get("mode", "white")
    for j, f in enumerate(each_frames(ctx, b, g.get("at", "each_cut"))):
        it = {"id": f"{g['_gid']}.{j}", "k": "flash", "f": [f, f + n]}
        if mode in ("white", "tint", "fill"):
            it.update(mode="fill", c=norm_hex(g["c"]) if is_hex(g.get("c")) else "#FFFFFF")
        else:
            it["mode"] = mode
            if is_hex(g.get("c")):
                it["c"] = norm_hex(g["c"])
            it["th"] = float(g.get("th", 0.5))
        r = rect_of(ctx, b, g, f)
        if r:
            it["rect"] = r
        ctx.add(it)
        if j == 0:
            g_snd(ctx, b, g, f)


@gesture("recolour", 'key c at rect range. What is near the key colour takes the new colour and keeps its light: a red car turns teal and nothing else moves. Read from the frame, so it follows the thing', lower="none")
def g_recolour(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a or not is_hex(g.get("key")) or not is_hex(g.get("c")):
        return ctx.find(f"{b.id}: recolour needs key, c and a span")
    it = {"id": g["_gid"], "k": "flash", "f": [a, z], "mode": "recolour", "key": norm_hex(g["key"]), "c": norm_hex(g["c"]), "th": float(clamp(float(g.get("range", 0.055)), 0.02, 0.07 if hex_sat(g["key"]) > 0.18 else 0.4))}
    r = rect_of(ctx, b, g, a)
    if r:
        it["rect"] = r
    ctx.add(it)
    g_snd(ctx, b, g, a)


@gesture("burn", 'at rect. Burns to a white floor, resolves over 7 frames', degrade="flash:white")
def g_burn(ctx, b, g, i):
    for j, f in enumerate(each_frames(ctx, b, g.get("at", "each_cut"))):
        it = {"id": f"{g['_gid']}.{j}", "k": "flash", "f": [f, f + len(BURN_TH)], "mode": "burn", "th": list(BURN_TH)}
        r = rect_of(ctx, b, g, f)
        if r:
            it["rect"] = r
        ctx.add(it)
        if j == 0:
            g_snd(ctx, b, g, f)


@gesture("tiles", 'at f mode(burn|fill|neg) c n lines. A 3x3 relay, one cell at a time, ending centre', degrade="flash:neg")
def g_tiles(ctx, b, g, i):
    a = frame_of(ctx, g.get("at", f"{b.id}.in"), b)
    if a is None:
        return ctx.find(f"{b.id}: tiles can't place {g.get('at')!r}")
    step = int(g.get("f", 2))
    n = clamp(int(g.get("n", 9)), 1, 9)
    mode = g.get("mode", "burn")
    xs, ys = [0.0], [0.0]
    for v in CELLS_X:
        xs.append(xs[-1] + v)
    for v in CELLS_Y:
        ys.append(ys[-1] + v)
    for j, (r, c) in enumerate(RELAY_WALK[:n]):
        f = a + j * step
        it = {"id": f"{g['_gid']}.{j}", "k": "flash", "f": [f, f + step], "rect": [xs[c], ys[r], xs[c + 1], ys[r + 1]], "mode": "fill" if mode == "fill" else mode}
        if mode == "fill":
            it["c"] = norm_hex(g["c"]) if is_hex(g.get("c")) else "#DFDEDC"
        elif mode == "burn":
            it["th"] = float(g.get("th", 0.42))
        ctx.add(it)
    if g.get("lines", 1):
        z = a + n * step
        for j, x in enumerate(xs[1:3]):
            ctx.add({"id": f"{g['_gid']}.vl{j}", "k": "flash", "f": [a, z], "mode": "fill", "c": "#000000", "a": 0.4, "rect": [x, 0, x + 0.0012, 1]})
        for j, y in enumerate(ys[1:3]):
            ctx.add({"id": f"{g['_gid']}.hl{j}", "k": "flash", "f": [a, z], "mode": "fill", "c": "#000000", "a": 0.4, "rect": [0, y, 1, y + 0.0007]})
    g_snd(ctx, b, g, a)


@gesture("punch", 'at z d. Hard punch-in until the next cut', lower="zoom")
def g_punch(ctx, b, g, i):
    a = frame_of(ctx, g.get("at", f"{b.id}.in"), b)
    s = shot_at(ctx, a) if a is not None else None
    if s is None or s.get("pic") == "plate" or a >= s["f"][1]:
        return ctx.find(f"{b.id}: punch found no picture")
    z = min(s["f"][1], a + int(g["d"])) if isinstance(g.get("d"), (int, float)) else s["f"][1]
    it = sub_item(s, a, z, g["_gid"])
    c = it["crop"]
    c0 = c[0] if isinstance(c[0], list) else c
    k = float(g.get("z", 1.28))
    cx, cy = (c0[0] + c0[2]) / 2, (c0[1] + c0[3]) / 2
    w, h = (c0[2] - c0[0]) / k, (c0[3] - c0[1]) / k
    it["crop"] = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]
    carve(ctx, a, z, [it])
    ctx.cam_land[g["_gid"]], ctx.cam_len[g["_gid"]] = a, z - a
    if ctx.governed and s.get("pic") == "main" and not isinstance(g.get("d"), (int, float)):
        end = z                      # the take stays in the framing it punched to, until another picture cuts in
        for q in sorted(ctx.shots, key=lambda x: x["f"][0]):
            if q["f"][0] < z:
                continue
            if q["f"][0] != end or q.get("pic") != "main" or isinstance(q["crop"][0], list):
                break
            qc = q["crop"]
            qx, qy = (qc[0] + qc[2]) / 2, (qc[1] + qc[3]) / 2
            qw, qh = (qc[2] - qc[0]) / k, (qc[3] - qc[1]) / k
            q["crop"] = [qx - qw / 2, qy - qh / 2, qx + qw / 2, qy + qh / 2]
            end = q["f"][1]
        ctx.cam_len[g["_gid"]] = end - a
    g_snd(ctx, b, g, a)


@gesture("freeze", 'at d treat cam. The picture stops, the voice runs on', lower="hard_cut")
def g_freeze(ctx, b, g, i):
    a = frame_of(ctx, g.get("at", f"{b.id}.in"), b)
    s = shot_at(ctx, a) if a is not None else None
    if s is None or "src" not in s:
        return ctx.find(f"{b.id}: freeze found no moving picture")
    d = int(g.get("d", 18))
    z = min(a + d, ctx.dur_f)
    it = sub_item(s, a, min(z, s["f"][1]), g["_gid"])
    t = it["src"][0]
    it["src"] = [t, t]
    it["f"] = [a, z]
    c = it["crop"]
    base = c[0] if isinstance(c[0], list) else c
    it["crop"] = camera(ctx, base, z - a, g.get("cam"))
    if g.get("treat"):
        it["treat"] = parse_treat(g["treat"])
    carve(ctx, a, z, [it])
    g_snd(ctx, b, g, a)


_FMT_FIELD = re.compile(r"\{n(?::0?(\d+)d?)?\}|%0?(\d*)d")      # Score syntax a model writes: {n}, {n:02d}, %d, %02d


def counter_text(fmt, v, whole, sep=False):
    """A counter's step, through its format. A p5374 replay wrote fmt "11:%02d pm" and the film showed
    the format itself: printf fields are read like {n}, with their zero padding."""
    num = f"{int(round(v)):,}" if whole and sep else str(int(round(v))) if whole else f"{v:.1f}"

    def field(m):
        width = m.group(1) or m.group(2)
        return num.zfill(int(width)) if width and whole and not sep else num
    out, k = _FMT_FIELD.subn(field, fmt)
    if k and "%" in fmt:
        out = out.replace("%%", "%")
    return out if k else f"{fmt} {num}".strip() if fmt else num


@gesture("counter", 'from to at step fmt("DIA {n}", "{n:02d}") size seat font. Digits step hard, slowing at the end', lower="text")
def g_counter(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: counter has no span")
    v0, v1 = float(g.get("from", 0)), float(g.get("to", 100))
    step = max(1, int(g.get("step", 3)))
    n = max(1, (z - a) // step)
    fmt = str(g.get("fmt", "{n}"))
    whole = v0 == int(v0) and v1 == int(v1)
    held = None                     # one counter, one seat: solved on its first tick and kept across cuts
    for j in range(n):
        p = (j + 1) / n
        p = 1 - (1 - p) ** 2.2
        v = v0 + (v1 - v0) * p
        s = counter_text(fmt, v, whole, g.get("sep"))
        f = a + j * step
        q = z if j == n - 1 else f + step
        if g.get("hold") and j == n - 1:
            q = min(ctx.dur_f, z + int(g["hold"]))
        opt = {k: v2 for k, v2 in g.items() if k in ("size", "seat", "font", "ink", "fit", "blend", "case", "_room", "_want", "_fit0", "_noseat")}
        if held:
            opt["seat"] = held
        r = display(ctx, f"{g['_gid']}.{j}", s, f, q, opt, b)
        if r and not held:
            held = [(r[0] + r[2]) / 2, (r[1] + r[3]) / 2]
    g_snd(ctx, b, g, a)


@gesture("pile", 'pic[] at every w h. Prints land on each other, tilted, settling', lower="overlay")
def g_pile(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: pile has no span")
    refs = pics_of(ctx, b, g) or b.pics
    rng = rng_for(ctx.seed, g["_gid"])
    every = max(2, int(g.get("every", 6)))
    n = min(len(refs) if len(refs) > 1 else 6, max(1, (z - a) // every))
    for j in range(n):
        f = a + j * every
        g2 = {"w": g.get("w", 0.55), "h": g.get("h", 0.42), "x": 0.5 + rng.uniform(-0.1, 0.1), "y": float(g.get("y", 0.42)) + rng.uniform(-0.07, 0.07),
              "rot": rng.uniform(-6, 6), "cap": "free"}
        make_card(ctx, b, dict(refs[j % len(refs)]), f, z, g2, f"{g['_gid']}.{j}", z_order=1 + j)
        if j == 0:
            g_snd(ctx, b, g, f)


@gesture("split", 'pic[] at dir(v|h) bg gap. Two or three synced windows', lower="overlay")
def g_split(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: split has no span")
    refs = pics_of(ctx, b, g)
    refs = refs if len(refs) >= 2 else refs + refs
    refs = refs[:3]
    n = len(refs)
    gap = float(g.get("gap", 0.02))
    vertical = g.get("dir", "v") == "v"
    carve(ctx, a, z, emit_ref(ctx, b, {"kind": "plate", "plate": plate_of(ctx, g.get("bg", "plate"))}, a, z, g["_gid"] + ".bg"))
    for j, ref in enumerate(refs):
        if vertical:
            h = (0.86 - gap * (n - 1)) / n
            c = {"w": 0.86, "h": h, "x": 0.5, "y": 0.07 + h / 2 + j * (h + gap)}
        else:
            w = (0.92 - gap * (n - 1)) / n
            c = {"w": w, "h": 0.6, "x": 0.04 + w / 2 + j * (w + gap), "y": 0.5}
        make_card(ctx, b, dict(ref), a, z, {**c, "in": "cut", "cap": "free", "border": 0, "shadow": 0}, f"{g['_gid']}.{j}", z_order=1)
    g_snd(ctx, b, g, a)


@gesture("glitch", 'at rect. A small inverted patch, on 1, off 1, on 2 frames', degrade=None)
def g_glitch(ctx, b, g, i):
    for j, f in enumerate(each_frames(ctx, b, g.get("at", "every 10f"))):
        rng = rng_for(ctx.seed, g["_gid"], j)
        r = rect_of(ctx, b, {**g, "rect": g.get("rect", "face")}, f) or [0.0, 0.0, 1.0, 1.0]
        r = r[0] if isinstance(r[0], list) else r
        w, h = rng.uniform(0.1, 0.14), rng.uniform(0.03, 0.09)
        x = rng.uniform(r[0], max(r[0], r[2] - w))
        y = rng.uniform(r[1], max(r[1], r[3] - h))
        patch = [x, y, x + w, y + h]
        ctx.add({"id": f"{g['_gid']}.{j}a", "k": "flash", "f": [f, f + 1], "mode": "neg", "rect": patch, "c": "#7FE3FF"})
        ctx.add({"id": f"{g['_gid']}.{j}b", "k": "flash", "f": [f + 2, f + 4], "mode": "neg", "rect": patch, "c": "#7FE3FF"})


@gesture("boxes", 'at rect hold. Pale box, the world inverts round it, then a held negative', degrade="neg_card")
def g_boxes(ctx, b, g, i):
    a = frame_of(ctx, g.get("at", f"{b.id}.in"), b)
    if a is None:
        return ctx.find(f"{b.id}: boxes can't place {g.get('at')!r}")
    r = rect_of(ctx, b, {**g, "rect": g.get("rect", "face")}, a)
    if r is None:           # nobody to box: its declared degrade, a negative registered to the frame
        ctx.add({"id": f"{g['_gid']}.hold", "k": "flash", "f": [a, a + 2], "mode": "neg"})
        return g_snd(ctx, b, g, a)
    r = r[0] if isinstance(r[0], list) else r
    x0, y0, x1, y1 = r
    ctx.add({"id": f"{g['_gid']}.box", "k": "flash", "f": [a, a + 1], "mode": "fill", "c": "#FFFFFF", "a": 0.38, "rect": r})
    for j, q in enumerate(([0, 0, 1, y0], [0, y1, 1, 1], [0, y0, x0, y1], [x1, y0, 1, y1])):
        if q[2] > q[0] and q[3] > q[1]:
            ctx.add({"id": f"{g['_gid']}.out{j}", "k": "flash", "f": [a + 1, a + 2], "mode": "neg", "rect": q})
    ctx.add({"id": f"{g['_gid']}.hold", "k": "flash", "f": [a + 2, a + 2 + int(g.get("hold", 40))], "mode": "neg"})
    g_snd(ctx, b, g, a)


@gesture("slat", 'at w steps f dir. A band of negative jumps across in hard steps', degrade="neg_card")
def g_slat(ctx, b, g, i):
    a = frame_of(ctx, g.get("at", f"{b.id}.in"), b)
    if a is None:
        return ctx.find(f"{b.id}: slat can't place {g.get('at')!r}")
    w = float(g.get("w", 0.27))
    steps = int(g.get("steps", 3))
    d = int(g.get("f", 2))
    for j in range(steps):
        p = j / max(1, steps - 1) * (1 - w)
        rect = [p, 0, p + w, 1] if g.get("dir", "x") == "x" else [0, p, 1, p + w]
        ctx.add({"id": f"{g['_gid']}.{j}", "k": "flash", "f": [a + j * d, a + (j + 1) * d], "mode": "neg", "rect": rect})
    g_snd(ctx, b, g, a)


def cut_near(ctx, c):
    """The real cut at or within 3 frames of c, or None. A transition needs a boundary."""
    best = None
    for s in ctx.shots:
        f = s["f"][0]
        if f > 0 and abs(f - c) <= 3 and (best is None or abs(f - c) < abs(best - c)):
            best = f
    return best


def ramp_crop(s, zoom):
    """A stretch of a shot with its crop zoomed by a per-frame list."""
    c = s.get("crop")
    if not c:
        return s
    out = []
    for i, z in enumerate(zoom):
        r = c[i] if isinstance(c[0], list) else c
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        w, h = (r[2] - r[0]) / z, (r[3] - r[1]) / z
        out.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
    s["crop"] = out
    return s


@gesture("whip", 'at f z blur. The cut zooms through: old picture rushes in, new one lands out of it', lower="hard_cut")
def g_whip(ctx, b, g, i):
    n = int(g.get("f", 5))
    zm = float(g.get("z", 1.5))
    bl = float(g.get("blur", 24))
    k = 0
    for c0 in each_frames(ctx, b, g.get("at", "each_cut")):
        c = cut_near(ctx, c0)
        if c is None:
            continue
        A, B = shot_at(ctx, c - 1), shot_at(ctx, c)
        p = max(A["f"][0], c - n)
        q = min(B["f"][1], c + n)
        a1 = sub_item(A, p, c, f"{g['_gid']}.{k}a")
        b1 = sub_item(B, c, q, f"{g['_gid']}.{k}b")
        m1, m2 = c - p, q - c
        ramp_crop(a1, [1 + (zm - 1) * ((j + 1) / m1) ** 2.2 for j in range(m1)])
        a1["blur"] = [bl * ((j + 1) / m1) ** 1.5 for j in range(m1)]
        ramp_crop(b1, [1 + (zm - 1) * (1 - (j + 1) / m2) ** 2.2 for j in range(m2)])
        b1["blur"] = [bl * (1 - (j + 1) / m2) ** 1.5 for j in range(m2)]
        carve(ctx, p, c, [a1])
        carve(ctx, c, q, [b1])
        if k == 0:
            g_snd(ctx, b, g, c)
        k += 1


def as_card(s, a, z, gid, order, xs, ys, blur):
    it = {k: v for k, v in s.items() if k in ("pic", "src", "crop", "plate", "treat", "grade", "step")}
    it.update(id=gid, k="card", f=[a, z], z=order, x=xs, y=ys, w=1, h=1, s=1, rot=0, op=1, _cap_edge=False, blur=blur)
    return it


@gesture("slide", 'at f dir. The new picture pushes the old one out, eased', lower="hard_cut")
def g_slide(ctx, b, g, i):
    n = int(g.get("f", 7))
    d = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}.get(g.get("dir", "up"), (0, -1))
    ease = [1 - (1 - (j + 1) / n) ** 3 for j in range(n)]
    blur = [18 * 4 * e * (1 - e) for e in ease]
    k = 0
    for c0 in each_frames(ctx, b, g.get("at", "each_cut")):
        c = cut_near(ctx, c0)
        if c is None:
            continue
        A, B = shot_at(ctx, c - 1), shot_at(ctx, c)
        p = max(A["f"][0], c - n)
        m = c - p
        e2 = ease[-m:]
        a1 = sub_item(A, p, c, f"{g['_gid']}.{k}a")
        b1 = dict(B)
        if isinstance(b1.get("crop", [0])[0], list):
            b1["crop"] = b1["crop"][0]
        if b1.get("src"):
            s0 = b1["src"][0]
            b1["src"] = [max(0.0, s0 - m / ctx.fps), s0]
        ctx.add(as_card(a1, p, c, f"{g['_gid']}.{k}a", 3, [0.5 + d[0] * e for e in e2], [0.5 + d[1] * e for e in e2], blur[-m:]))
        ctx.add(as_card(b1, p, c, f"{g['_gid']}.{k}b", 4, [0.5 + d[0] * (e - 1) for e in e2], [0.5 + d[1] * (e - 1) for e in e2], blur[-m:]))
        if k == 0:
            g_snd(ctx, b, g, c)
        k += 1


@gesture("shake", 'at amp f. The frame takes the hit and settles', lower="hard_cut")
def g_shake(ctx, b, g, i):
    n = int(g.get("f", 9))
    amp = float(g.get("amp", 0.012))
    rng = rng_for(ctx.seed, g["_gid"])
    for j, a in enumerate(each_frames(ctx, b, g.get("at", f"{b.id}.in"))):
        s = shot_at(ctx, a)
        if s is None or not s.get("crop") or a >= s["f"][1]:
            continue
        z = min(s["f"][1], a + n)
        it = sub_item(s, a, z, f"{g['_gid']}.{j}")
        c = it["crop"]
        out = []
        for k in range(z - a):
            r = c[k] if isinstance(c[0], list) else c
            d = (1 - k / (z - a)) ** 2
            w, h = (r[2] - r[0]) / (1 + 3 * amp), (r[3] - r[1]) / (1 + 3 * amp)
            cx = (r[0] + r[2]) / 2 + rng.uniform(-1, 1) * amp * d
            cy = (r[1] + r[3]) / 2 + rng.uniform(-1, 1) * amp * d
            cx, cy = clamp(cx, w / 2, 1 - w / 2), clamp(cy, h / 2, 1 - h / 2)
            out.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
        it["crop"] = out
        carve(ctx, a, z, [it])
        if j == 0:
            g_snd(ctx, b, g, a)


@gesture("chip", 't at d bg ink size seat in. A word on a small solid label', lower="text")
def g_chip(ctx, b, g, i):
    at = g.get("at", f"{b.id}.in")
    a0, z0 = span_of(ctx, b, at) if at == "beat" or whole_span(ctx, at) or (isinstance(at, str) and ".." in at) else (None, None)
    a = a0 if a0 is not None else display_land(ctx, b, at)
    if a is None:
        return ctx.find(f"{b.id}: chip can't place {g.get('at')!r}")
    z = min(ctx.dur_f, z0 if z0 is not None else a + int(g.get("d", 30)))
    t = str(g.get("t") or big_words(ctx, g))
    if not t or z <= a:
        return
    g, z = seat_for(ctx, g["_gid"], g, a, z)
    if g is None:
        return
    size = float(g.get("size", 0.03))
    if ctx.governed:
        from . import taste
        if taste.machine_text(t):
            ctx.rejects.append((b.raw.get("_line", 0), f"{g['_gid']} dropped: {t[:40]!r} is a reference, not a word of this film", ""))
            return
        size = max(size, 0.02)             # P5: anything meant to be read is 26 px at 720x1280 or more
    font, _, track = parse_font_spec(g.get("font") or "grotesk-bold")
    font = script_font(font or "InterDisplay-Bold.ttf", t)
    track = 0 if joined_script(t) else track
    key = f"chip{font[:-4]}_{int(size * ctx.H)}"
    ctx.voices.setdefault(key, {"font": font, "px": int(round(size * ctx.H)), "track": -2 if track is None else track})
    v = ctx.voices[key]
    tw = text_px(t, v) / ctx.W
    seat = g.get("seat") if isinstance(g.get("seat"), (list, tuple)) else [0.5, 0.3]
    w, h = tw + 2 * float(g.get("pad", 0.022)), float(g.get("h", size * 1.55))
    x = clamp(float(seat[0]), w / 2 + 0.02, 1 - w / 2 - 0.02)
    y = float(seat[1])
    fr = framed_rect(ctx, a)
    if fr and rects_touch([x - w / 2, y - h / 2 - 0.01, x + w / 2, y + h / 2 + 0.01], fr):
        # a label names the framed still from the plate, above it, or under it when the top is the platform's
        y = fr[1] - h / 2 - 0.028 if fr[1] - h - 0.028 >= 0.11 else fr[3] + h / 2 + 0.028
        x = clamp(fr[0] + w / 2, w / 2 + 0.02, 1 - w / 2 - 0.02)        # flush with the picture's left edge
    y += dodge_keepout(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    if ctx.governed:
        # Labels that share a seat while they are on screen are a list: each lands under the one before, flush left
        # with it, and the list grows upward instead when going down would reach a head or leave the safe area
        # (p5574, 13 Nov: four therapies named on four chips at one seat were pushed onto his face and three left out).
        def taken(yy):
            r = [x - w / 2, yy - h / 2, x + w / 2, yy + h / 2]
            return [bl for bl in ctx.blocks if bl[0] < z and bl[1] > a and rects_touch(r, bl[2])]

        def on_head(yy):
            r = [x - w / 2, yy - h / 2, x + w / 2, yy + h / 2]
            return any(rects_touch(r, hd) for f in sorted({a, (a + z) // 2, max(a, z - 1)}) for hd in head_rects(ctx, shot_at(ctx, f)))
        # One seat, one label at a time: a label or a title of an earlier beat that still holds this place leaves as
        # this one lands, once it has been read (0.5 s). Labels of the same beat are a list and stand together.
        # p5575, 13 Nov: 01 to 05 at one seat pushed each other up the frame, the last onto her neck.
        for bl in taken(y):
            owner = bl[3] if len(bl) > 3 else None
            if owner and not str(owner).startswith(b.id + ".") and a - bl[0] >= int(0.5 * ctx.fps):
                end_unit(ctx, owner, a)
        fit = g.get("fit") if isinstance(g.get("fit"), (int, float)) and "_want" in g and not fr else None
        left, right = (float(seat[0]) - fit / 2, float(seat[0]) + fit / 2) if fit else (0.05, 0.95)
        listed = sum(1 for q in (b.raw.get("do") or []) if isinstance(q, dict) and q.get("g") == "chip") >= 2
        hit = taken(y)
        if not hit and listed and fit:
            x = clamp(left + w / 2, w / 2 + 0.02, 1 - w / 2 - 0.02)       # a list starts at its seat's left edge, so it has the row
            hit = taken(y)
        flowed = False
        if hit:
            last = max(hit, key=lambda bl: (round(bl[2][3], 3), bl[2][2]))[2]
            grow = (1 / SETTLE["s_inf"] - 1) / 2 if g.get("in", "settle") == "settle" else 0.0      # labels land a fifth larger and settle
            nx = last[2] + 0.012 + grow * ((last[2] - last[0]) + w) + w / 2
            ny = (last[1] + last[3]) / 2
            r = [nx - w / 2, ny - h / 2, nx + w / 2, ny + h / 2]
            if nx + w / 2 <= right + 1e-6 and not any(bl[0] < z and bl[1] > a and rects_touch(r, bl[2]) for bl in ctx.blocks):
                x, y, flowed = nx, ny, True           # the next label of the row
        if hit and flowed:
            pass
        elif hit:
            x = clamp(min(bl[2][0] for bl in hit) + w / 2, w / 2 + 0.02, 1 - w / 2 - 0.02)
            down = up = y
            row = 0.014 + (1 / SETTLE["s_inf"] - 1) * h          # each label lands a fifth larger: the rows keep clear of each other then too
            for _ in range(8):
                q = taken(down)
                if not q:
                    break
                down = max(bl[2][3] for bl in q) + h / 2 + row
            for _ in range(8):
                q = taken(up)
                if not q:
                    break
                up = min(bl[2][1] for bl in q) - h / 2 - row

            def on_cap(yy):
                return ctx.cap_y is not None and bool(ctx.wf_any) and abs(yy - ctx.cap_y) < 0.0375 + 0.6 * h + 0.016
            down_ok = down + h / 2 <= 0.80 and not taken(down) and not on_head(down) and not on_cap(down)
            up_ok = up - h / 2 >= 0.10 and not taken(up) and not on_head(up) and not on_cap(up)
            y = down if down_ok or not up_ok else up
    n = z - a
    settle = g.get("in", "settle") == "settle"
    scale = [(SETTLE["s_inf"] + (1 - SETTLE["s_inf"]) * math.exp(-j / SETTLE["tau"])) / SETTLE["s_inf"] for j in range(n)] if settle else 1
    plate = plate_of(ctx, g.get("bg", "#FFFFFF"))
    plate["grain"] = 0
    ctx.add({"id": g["_gid"], "k": "card", "f": [a, z], "z": 2, "pic": "plate", "plate": plate, "x": x, "y": y, "w": w, "h": h, "s": scale, "rot": float(g.get("rot", 0)), "op": 1,
             "radius": float(g.get("radius", 0.006)), "_cap_edge": False})
    ctx.blocks.append((a, z, [x - w / 2, y - h / 2, x + w / 2, y + h / 2], g["_gid"]))
    ink = g.get("ink") or ("#111111" if luma(plate["c0"]) > 0.5 else "#FFFFFF")
    ex = {} if not settle else {"s": list(scale)}
    emit_word(ctx, f"{g['_gid']}.t", t, key, x - tw / 2, y + 0.36 * v["px"] / ctx.H, a, z, ink, "big", z=2, _label=plate["c0"], **ex)
    g_snd(ctx, b, g, a)


@gesture("hud", 'of(face|body) at label c. Tracking corners ride the subject', needs=("perception",), degrade=None, lower="none")
def g_hud(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: hud has no span")
    key = g.get("of", "face")
    path = subject_rects(ctx, key, a, z, pad=0.02)
    if not path:
        return ctx.find(f"{b.id}: hud has no subject to follow, dropped")
    c = norm_hex(g["c"]) if is_hex(g.get("c")) else "#FFFFFF"
    th, tv = 0.0026, 0.0026 * ctx.W / ctx.H
    bars = {"tl_h": [], "tl_v": [], "tr_h": [], "tr_v": [], "bl_h": [], "bl_v": [], "br_h": [], "br_v": []}
    for r in path:
        L = 0.22 * min(r[2] - r[0], (r[3] - r[1]) * ctx.H / ctx.W)
        Lv = L * ctx.W / ctx.H
        bars["tl_h"].append([r[0], r[1], r[0] + L, r[1] + tv])
        bars["tl_v"].append([r[0], r[1], r[0] + th, r[1] + Lv])
        bars["tr_h"].append([r[2] - L, r[1], r[2], r[1] + tv])
        bars["tr_v"].append([r[2] - th, r[1], r[2], r[1] + Lv])
        bars["bl_h"].append([r[0], r[3] - tv, r[0] + L, r[3]])
        bars["bl_v"].append([r[0], r[3] - Lv, r[0] + th, r[3]])
        bars["br_h"].append([r[2] - L, r[3] - tv, r[2], r[3]])
        bars["br_v"].append([r[2] - th, r[3] - Lv, r[2], r[3]])
    for name, rects in bars.items():
        ctx.add({"id": f"{g['_gid']}.{name}", "k": "flash", "mode": "fill", "c": c, "f": [a, z], "rect": rects, "z": 3})
    if g.get("label"):
        size = float(g.get("size", 0.016))
        key2 = f"hud_{int(size * ctx.H)}"
        font, _, _ = parse_font_spec(g.get("font") or ctx.dna.get("hud") or "grotesk-bold")
        ctx.voices.setdefault(key2, {"font": font or "InterDisplay-Bold.ttf", "px": int(round(size * ctx.H)), "track": 2})
        emit_word(ctx, f"{g['_gid']}.label", str(g["label"]), key2, [clamp(r[0], 0.02, 0.8) for r in path], [clamp(r[1] - 0.008, 0.03, 0.97) for r in path], a, z, c, "big", z=3)
    g_snd(ctx, b, g, a)


@gesture("spot", 'of at a. The world dims, hard-edged, round the subject', needs=("perception",), degrade=None, lower="none")
def g_spot(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: spot has no span")
    path = subject_rects(ctx, g.get("of", "body"), a, z, pad=0.02)
    if not path:
        return ctx.find(f"{b.id}: spot has no subject to follow, dropped")
    al = float(g.get("a", 0.62))
    for j, fn in enumerate((lambda r: [0, 0, 1, r[1]], lambda r: [0, r[3], 1, 1], lambda r: [0, r[1], r[0], r[3]], lambda r: [r[2], r[1], 1, r[3]])):
        ctx.add({"id": f"{g['_gid']}.{j}", "k": "flash", "mode": "fill", "c": "#000000", "a": al, "f": [a, z], "rect": [fn(r) for r in path], "z": 1})
    g_snd(ctx, b, g, a)


def crop_at(s, i):
    c = s.get("crop")
    if not c:
        return None
    return c[min(i, len(c) - 1)] if isinstance(c[0], list) else c


def last_frame_card(s, c):
    """The fields of shot s frozen on its final frame, for pieces of it to carry away."""
    it = {k: v for k, v in s.items() if k in ("pic", "plate", "treat", "grade")}
    if s.get("src"):
        t = s["src"][1] - (s["src"][1] - s["src"][0]) / max(1, s["f"][1] - s["f"][0])
        it["src"] = [t, t]
    return it, crop_at(s, s["f"][1] - s["f"][0] - 1)


@gesture("shred", 'at n f dir order(wave|seed). The old picture cut into strips that leave one after another', lower="hard_cut")
def g_shred(ctx, b, g, i):
    n = clamp(int(g.get("n", 9)), 2, 24)
    fl = max(4, int(g.get("f", 12)))
    d = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}.get(g.get("dir", "up"), (0, -1))
    vertical = d[0] == 0              # strips stand when they leave up or down
    rng = rng_for(ctx.seed, g["_gid"])
    k = 0
    for c0 in each_frames(ctx, b, g.get("at", "each_cut")):
        c = cut_near(ctx, c0)
        if c is None:
            continue
        A = shot_at(ctx, c - 1)
        fields, crop = last_frame_card(A, c)
        order = list(range(n))
        if g.get("order") == "seed":
            rng.shuffle(order)
        stag = max(1.0, fl * 0.5 / n)
        for j, pos in enumerate(order):
            delay = int(j * stag)
            m = fl
            cw = 1.0 / n
            x, y = (pos + 0.5) * cw, 0.5
            if not vertical:
                x, y = 0.5, (pos + 0.5) * cw
            path = [(e / (m - 1)) ** 2.4 * 1.05 for e in range(m)]
            xs = [x + d[0] * p for p in path]
            ys = [y + d[1] * p for p in path]
            it = dict(fields)
            if crop:
                w, h = crop[2] - crop[0], crop[3] - crop[1]
                it["crop"] = ([crop[0] + w * pos / n, crop[1], crop[0] + w * (pos + 1) / n, crop[3]] if vertical else
                              [crop[0], crop[1] + h * pos / n, crop[2], crop[1] + h * (pos + 1) / n])
            it.update(id=f"{g['_gid']}.{k}.{pos}", k="card", f=[c + delay, c + delay + m], z=6, x=xs, y=ys, w=(cw if vertical else 1), h=(1 if vertical else cw), s=1, rot=0, op=1, _cap_edge=False)
            ctx.add(it)
            if delay:     # until its turn the strip sits still, so the old picture holds whole
                hold = dict(it)
                hold.update(id=f"{it['id']}h", f=[c, c + delay], x=x, y=y)
                ctx.add(hold)
        if k == 0:
            g_snd(ctx, b, g, c)
        k += 1


@gesture("shatter", 'at grid f. The old picture breaks into pieces that fall, each on its own clock', lower="hard_cut")
def g_shatter(ctx, b, g, i):
    try:
        cols, rows = [int(x) for x in str(g.get("grid", "6x10")).split("x")]
    except ValueError:
        cols, rows = 6, 10
    cols, rows = clamp(cols, 2, 14), clamp(rows, 2, 24)
    fl = max(6, int(g.get("f", 16)))
    k = 0
    for c0 in each_frames(ctx, b, g.get("at", "each_cut")):
        c = cut_near(ctx, c0)
        if c is None:
            continue
        rng = rng_for(ctx.seed, g["_gid"], k)
        A = shot_at(ctx, c - 1)
        fields, crop = last_frame_card(A, c)
        cw, ch = 1.0 / cols, 1.0 / rows
        for r in range(rows):
            for q in range(cols):
                delay = int(rng.uniform(0, 0.55) * fl)
                m = fl - delay
                x0, y0 = (q + 0.5) * cw, (r + 0.5) * ch
                vx = rng.uniform(-0.25, 0.25)
                spin = rng.uniform(-80, 80)
                path = [(e / max(1, m - 1)) for e in range(m)]
                it = dict(fields)
                if crop:
                    w, h = crop[2] - crop[0], crop[3] - crop[1]
                    it["crop"] = [crop[0] + w * q * cw, crop[1] + h * r * ch, crop[0] + w * (q + 1) * cw, crop[1] + h * (r + 1) * ch]
                it.update(id=f"{g['_gid']}.{k}.{r}_{q}", k="card", f=[c + delay, c + fl], z=6, x=[x0 + vx * p for p in path], y=[y0 + 1.3 * p * p for p in path],
                          w=cw, h=ch, s=1, rot=[spin * p for p in path], op=1, _cap_edge=False)
                ctx.add(it)
                if delay:
                    hold = dict(it)
                    hold.update(id=f"{it['id']}h", f=[c, c + delay], x=x0, y=y0, rot=0)
                    ctx.add(hold)
        if k == 0:
            g_snd(ctx, b, g, c)
        k += 1


@gesture("echo", 'at n lag s. The picture repeats inside itself, each ring a few frames in the past', lower="overlay")
def g_echo(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    s = shot_at(ctx, a) if a is not None else None
    if s is None or s.get("pic") == "plate":
        return ctx.find(f"{b.id}: echo needs a picture")
    z = min(z, s["f"][1])
    lag = int(g.get("lag", 4))
    k = float(g.get("s", 0.8))
    stretch = sub_item(s, a, z, "x")
    for j in range(1, clamp(int(g.get("n", 4)), 1, 8) + 1):
        it = {q: v for q, v in stretch.items() if q in ("pic", "crop", "treat", "grade")}
        if stretch.get("src"):
            it["src"] = [max(0.0, stretch["src"][0] - j * lag / ctx.fps), max(0.0, stretch["src"][1] - j * lag / ctx.fps)]
        it.update(id=f"{g['_gid']}.{j}", k="card", f=[a, z], z=1 + j, x=0.5, y=0.5, w=1, h=1, s=k ** j, rot=0, op=1, _cap_edge=False,
                  border={"w": 0.003, "c": "#FFFFFF"})
        ctx.add(it)
    g_snd(ctx, b, g, a)


@gesture("stutter", 'at n f z. The picture catches on a word: same frames again, each a step closer', lower="hard_cut")
def g_stutter(ctx, b, g, i):
    a = frame_of(ctx, g.get("at", f"{b.id}.in"), b)
    s = shot_at(ctx, a) if a is not None else None
    if s is None or "src" not in s:
        return ctx.find(f"{b.id}: stutter needs moving picture")
    n, fl = int(g.get("n", 3)), int(g.get("f", 3))
    zm = float(g.get("z", 1.08))
    t = sub_item(s, a, min(a + 1, s["f"][1]), "x")["src"][0]
    r = crop_at(s, a - s["f"][0])
    out = []
    for k in range(n):
        p = a + k * fl
        w, h = (r[2] - r[0]) / zm ** k, (r[3] - r[1]) / zm ** k
        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
        it = {q: v for q, v in s.items() if q in ("pic", "treat", "grade")}
        it.update(id=f"{g['_gid']}.{k}", k="shot", f=[p, p + fl], src=[t, t + fl / ctx.fps], crop=[cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
        out.append(it)
    carve(ctx, a, a + n * fl, out)
    g_snd(ctx, b, g, a)


def point_path(ctx, key, a, z):
    """A tracked point on screen, one (x,y) per frame. Boxes give their centre."""
    pts = []
    rows = ctx.boxes.get(key)
    if rows is None:
        rows = ctx.boxes[key] = [(r["t"], r[key]) for r in (ctx.perc.get("samples") or []) if r.get(key)]
    if not rows:
        return None
    last = None
    for f in range(a, z):
        s = shot_at(ctx, f)
        p = None
        if s and is_take(s.get("pic")) and "src" in s:
            n, i = s["f"][1] - s["f"][0], f - s["f"][0]
            t = s["src"][0] + (s["src"][1] - s["src"][0]) * i / max(1, n)
            j = bisect.bisect_left(rows, (t,))
            near = rows[max(0, j - 3):j + 3]
            tot, xs, ys = 0.0, 0.0, 0.0
            for rt, q in near:
                w = math.exp(-((rt - t) / 0.2) ** 2 / 2)
                qx, qy = (q[0], q[1]) if len(q) == 2 else ((q[0] + q[2]) / 2, (q[1] + q[3]) / 2)
                tot += w
                xs += w * qx
                ys += w * qy
            if tot > 1e-4:
                c = crop_at(s, i)
                p = ((xs / tot - c[0]) / (c[2] - c[0]), (ys / tot - c[1]) / (c[3] - c[1]))
        pts.append(p or last)
        last = p or last
    if last is None:
        return None
    first = next(x for x in pts if x)
    return [x or first for x in pts]


@gesture("pin", 't|pic of(rw|lw|nose|neck|face|body) at dx dy size. Type or a small card rides a tracked point', needs=("perception",), degrade=None, lower="none")
def g_pin(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: pin has no span")
    path = point_path(ctx, g.get("of", "nose"), a, z)
    if not path:
        return ctx.find(f"{b.id}: pin has nothing to follow, dropped")
    dx, dy = float(g.get("dx", 0)), float(g.get("dy", 0))
    refs = pics_of(ctx, b, g) if g.get("pic") is not None else []
    if refs:
        w, h = float(g.get("w", 0.25)), float(g.get("h", 0.15))
        it = make_card(ctx, b, dict(refs[0]), a, z, {"w": w, "h": h, "in": g.get("in", "settle"), "cap": "free"}, g["_gid"], z_order=2)
        it["x"] = [clamp(p[0] + dx, w / 2 + 0.01, 1 - w / 2 - 0.01) for p in path]
        it["y"] = [clamp(max(p[1] + dy, KEEPOUT[3] + h / 2 + 0.01), h / 2 + 0.01, 1 - h / 2 - 0.01) for p in path]
    else:
        t = str(g.get("t") or big_words(ctx, g))
        if not t:
            return ctx.find(f"{b.id}: pin has no text")
        lines = display_lines(ctx, t, g)
        l = lines[0]
        px = int(round(float(g.get("size", 0.03)) * ctx.H))
        key = f"pin{l['font'][:-4]}_{px}"
        ctx.voices.setdefault(key, {"font": l["font"], "px": px, "track": l["track"], "shadow": {"a": 0.5, "blur": 0.01}})
        tw = text_px(l["t"], ctx.voices[key]) / ctx.W
        xs = [clamp(p[0] + dx - tw / 2, 0.02, 0.98 - tw) for p in path]
        ys = [clamp(max(p[1] + dy, KEEPOUT[3] + px / ctx.H + 0.01), 0.04, 0.96) for p in path]
        emit_word(ctx, g["_gid"], l["t"], key, xs, ys, a, z, g.get("ink"), "big", z=2)
    g_snd(ctx, b, g, a)


@gesture("type_on", 't at size seat f caret font. Letters type on, a caret blinks after the last', lower="text")
def g_type_on(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    t = str(g.get("t") or big_words(ctx, g))
    if a is None or z <= a or not t:
        return ctx.find(f"{b.id}: type_on has no span or text")
    size = float(g.get("size", 0.03))
    font, _, track = parse_font_spec(g.get("font") or "grotesk-bold")
    if joined_script(t):       # letters of a joined script can't land one at a time: the line inks word by word
        return g_line(ctx, b, {**g, "t": t, "font": g.get("font") or "arabic-bold", "size": size, "in": "cut"}, i)
    key = f"type{(font or 'x')[:-4]}_{int(size * ctx.H)}"
    ctx.voices.setdefault(key, {"font": font or "InterDisplay-Bold.ttf", "px": int(round(size * ctx.H)), "track": -2 if track is None else track, "shadow": {"a": 0.35, "blur": 0.01}})
    v = ctx.voices[key]
    seat = g.get("seat") if isinstance(g.get("seat"), (list, tuple)) else [0.5, 0.5]
    tw = min(text_px(t, v) / ctx.W, 0.9)
    x = clamp(float(seat[0]) - tw / 2, 0.04, 0.96 - tw)
    y = float(seat[1])
    step = max(1, int(g.get("f", 2)))
    caret = bool(g.get("caret", 1))
    n = len(t)
    for k in range(1, n + 1):
        p = a + (k - 1) * step
        q = z if k == n else a + k * step
        if p >= z:
            break
        s = t[:k] + ("_" if caret and k < n else "")
        emit_word(ctx, f"{g['_gid']}.{k}", s, key, x, y, p, min(q, z), g.get("ink"), "big", z=2)
    if caret:                  # after the last letter the caret blinks 2 on, 2 off
        wx = x + text_px(t, v) / ctx.W
        f = a + n * step
        j = 0
        while f < min(z, a + n * step + 16):
            emit_word(ctx, f"{g['_gid']}.c{j}", "_", key, wx, y, f, min(f + 2, z), g.get("ink"), "big", z=2)
            f += 4
            j += 1
    ctx.blocks.append((a, z, [x, y - 0.74 * size, x + tw, y + 0.2 * size]))
    g_snd(ctx, b, g, a)


@gesture("wall", 'pic[] grid at every gap bg. A grid fills, one picture at a time', lower="overlay")
def g_wall(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    if a is None or z <= a:
        return ctx.find(f"{b.id}: wall has no span")
    refs = pics_of(ctx, b, g) or b.pics
    try:
        cols, rows = [int(x) for x in str(g.get("grid", "3x4")).split("x")]
    except ValueError:
        cols, rows = 3, 4
    gap = float(g.get("gap", 0.006))
    w = (1 - gap * (cols + 1)) / cols
    h = (1 - gap * (rows + 1) * ctx.W / ctx.H) / rows
    every = max(1, int(g.get("every", 3)))
    rng = rng_for(ctx.seed, g["_gid"])
    cells = [(c, r) for r in range(rows) for c in range(cols)]
    rng.shuffle(cells)
    carve(ctx, a, z, emit_ref(ctx, b, {"kind": "plate", "plate": plate_of(ctx, g.get("bg", "#000000"))}, a, z, g["_gid"] + ".bg"))
    for j, (c, r) in enumerate(cells):
        p = a + j * every
        if p >= z:
            break
        x = gap + (c + 0.5) * w + c * gap
        y = gap * ctx.W / ctx.H + (r + 0.5) * h + r * gap * ctx.W / ctx.H
        make_card(ctx, b, dict(refs[j % len(refs)]), p, z, {"w": w, "h": h, "x": x, "y": y, "in": "cut", "border": 0, "shadow": 0, "radius": 0, "cap": "free"}, f"{g['_gid']}.{j}", z_order=1)
    g_snd(ctx, b, g, a)


@gesture("ticker", 't at y h bg ink size speed. A running band of repeated words', lower="text")
def g_ticker(ctx, b, g, i):
    a, z = span_of(ctx, b, g.get("at"))
    z = b.pf1 if z is None else z
    t = str(g.get("t") or big_words(ctx, g))
    if a is None or z <= a or not t:
        return ctx.find(f"{b.id}: ticker has no span or text")
    size = float(g.get("size", 0.03))
    font, _, track = parse_font_spec(g.get("font") or "grotesk-bold")
    font = script_font(font or "InterDisplay-Bold.ttf", t)
    track = 0 if joined_script(t) else track
    key = f"tick{font[:-4]}_{int(size * ctx.H)}"
    ctx.voices.setdefault(key, {"font": font, "px": int(round(size * ctx.H)), "track": -2 if track is None else track})
    v = ctx.voices[key]
    y, h = float(g.get("y", 0.86)), float(g.get("h", 0.05))
    if rects_touch((0, y - h / 2, 1, y + h / 2), KEEPOUT):
        y = KEEPOUT[3] + h / 2 + 0.008
    unit = t + str(g.get("gap", "  /  "))
    uw = text_px(unit, v) / ctx.W
    speed = float(g.get("speed", 0.5)) * (-1 if g.get("rev") else 1)
    n = z - a
    reps = int(math.ceil((1.2 + abs(speed) * n / ctx.fps) / max(uw, 0.02))) + 1
    text = unit * min(reps, 40)
    x0 = -0.02 if speed > 0 else -abs(speed) * n / ctx.fps
    xs = [x0 - speed * k / ctx.fps for k in range(n)]
    if is_hex(g.get("bg")):
        ctx.add({"id": f"{g['_gid']}.bg", "k": "flash", "mode": "fill", "c": norm_hex(g["bg"]), "f": [a, z], "rect": [0, y - h / 2, 1, y + h / 2], "z": 3})
    ctx.blocks.append((a, z, [0, y - h / 2, 1, y + h / 2]))
    emit_word(ctx, g["_gid"], text, key, xs, y + 0.36 * v["px"] / ctx.H, a, z, g.get("ink"), "big", z=3)
    g_snd(ctx, b, g, a)


ALIEN_PASS = ("t", "ink", "font", "width", "seat", "lift", "dim", "room", "behind", "tail", "bars", "develop", "weave", "flicker",
              "n", "r", "grow", "mono", "step", "faces", "hints", "hole", "up", "down", "now")


@gesture("alien", 'dev at ... A device of the engine\'s alien bank (scene.alien.menu()). It rides the scene for the engine; today\'s renderer '
                  'draws the film without it', lower="none")
def g_alien(ctx, b, g, i):
    """The one seam to worker/scene/alien (lane S, 13 Nov 2026). A Score line names a device and what it answers;
    every anchor is resolved to programme frames here and the ask rides the scene under ``alien``. Nothing is drawn
    by the compiler: the engine's side lays the device in (``scene.alien.apply(l1, work)``) before it draws, and a
    renderer that never calls it shows the film as it was. Pictures are named as anywhere in a Score (bin:2, own:x)."""
    def frame(ref):
        return frame_of(ctx, ref, b) if not isinstance(ref, bool) and ref is not None else None

    def pic(name):
        return name if isinstance(name, str) and asset_for(ctx, name) is not None else None
    ask = {"dev": str(g.get("dev") or ""), "id": g["_gid"].replace(".", "_")}
    a, z = span_of(ctx, b, g.get("at"))
    if a is not None:
        ask["at"] = [a, b.pf1 if z is None else z]
    if isinstance(g.get("cuts"), list):
        ask["cuts"] = [f for f in (frame(c) for c in g["cuts"]) if f is not None]
    for key in ("pool", "order"):
        if isinstance(g.get(key), list):
            ask[key] = [p for p in (pic(n) for n in g[key]) if p]
    if isinstance(g.get("picks"), list):
        ask["picks"] = [dict({k: v for k, v in p.items() if k in ("word", "t", "focus")}, f=frame(p.get("at", p.get("w"))), pic=pic(p.get("pic")))
                        for p in g["picks"] if isinstance(p, dict)]
        ask["picks"] = [p for p in ask["picks"] if p["f"] is not None and p["pic"]]
    if isinstance(g.get("marks"), list):
        ask["marks"] = [dict({k: v for k, v in m.items() if k in ("year", "stakes")}, f=frame(m.get("at", m.get("w"))))
                        for m in g["marks"] if isinstance(m, dict)]
        ask["marks"] = [m for m in ask["marks"] if m["f"] is not None]
    for key in ALIEN_PASS:
        if key in g:
            ask[key] = g[key]
    if not ask["dev"]:
        return ctx.find(f"{b.id}: alien names no device")
    ctx.__dict__.setdefault("alien", []).append(ask)


def run_gestures(ctx):
    """Base-replacing gestures first, in the written order, then what sits on top of them."""
    base = ("hold", "cuts", "salvo", "page", "band", "split", "wall", "freeze", "punch", "stutter", "shake", "whip")
    for phase in (0, 1):
        for b in ctx.beats:
            dos = b.raw.get("do")
            if not dos:
                continue
            seen = {}
            for g in (dos if isinstance(dos, list) else [dos]):
                if isinstance(g, str):
                    g = {"g": g}
                if not isinstance(g, dict) or g.get("g") not in GESTURES:
                    ctx.find(f"{b.id}: gesture {g.get('g') if isinstance(g, dict) else g!r} unknown")
                    continue
                n = g["g"]
                k = seen.get(n, 0)
                seen[n] = k + 1
                if (n in base) != (phase == 0):
                    continue
                g = dict(g)
                g["_gid"] = f"{b.id}.{n}" if k == 0 else f"{b.id}.{n}{k}"
                if ctx.governed and not device_gate(ctx, b, g):
                    continue
                ctx.cur_gid = g["_gid"]
                try:
                    GESTURES[n]["fn"](ctx, b, g, k)
                except (ValueError, TypeError, KeyError, IndexError) as e:
                    ctx.find(f"{b.id}: gesture {n} failed: {e}")
                finally:
                    ctx.cur_gid = None


RAW_KINDS = {"shot", "card", "flash", "word"}


def run_raw(ctx, lines):
    """A raw L1 item for a moment no gesture covers. `at` and `d`, or `f`. Same rules apply."""
    for l in lines:
        if l["k"] != "item":
            continue
        raw = dict(l.get("item") or l)
        raw.pop("_line", None)
        kind = raw.get("kind") or raw.get("k")
        if kind == "item":
            kind = raw.get("kind")
        if kind == "shader" and not ctx.owner:
            ctx.find("shader lines are refused on a customer's Score")
            continue
        if kind not in RAW_KINDS or not raw.get("id"):
            ctx.find(f"line {l['_line']}: raw item needs an id and a kind in {sorted(RAW_KINDS)}")
            continue
        a, z = span_of(ctx, None, raw.get("at")) if raw.get("at") is not None else ((raw.get("f") or [None, None])[0], (raw.get("f") or [None, None])[1])
        if a is None:
            ctx.find(f"item {raw['id']}: no time")
            continue
        z = a + int(raw.get("d", 2)) if z is None else z
        it = {k: v for k, v in raw.items() if k not in ("kind", "at", "d", "for")}
        it.update(k=kind, f=[int(a), int(z)])
        if ctx.governed and kind != "shot":
            from . import taste
            dept = {"card": "card", "flash": "flash", "word": "display"}[kind]
            ctx.wrote[dept] = ctx.wrote.get(dept, 0) + 1
            rid = str(raw["id"])
            ev, why = taste.read_for(ctx, None, raw.get("for"), dept)
            bound = taste.bound_by(ctx, rid, dept) or (ev["ask"] if ev and ev["kind"] == "ask" else None)
            gone = _gone(ctx, rid, dept, bound)
            if gone or (ev is None and not bound):
                if not gone:
                    ctx.rejects.append((l["_line"], f"item {rid} dropped: it {why}", ""))
                ctx.taste_rows.append({"id": rid, "did": gone[0] if gone else "unearned", "why": gone[1] if gone else why, "dept": dept})
                continue
            ctx.devices[rid] = {"id": rid, "dept": dept, "g": "item", "b": None, "ev": ev, "bound": bound or None}
            it["_g"] = rid
        if kind == "word":
            for need, dv in (("t", ""), ("x", 0.5), ("y", 0.5), ("v", "a"), ("ink", "#FFFFFF")):
                it.setdefault(need, dv)
            if it["v"] not in ctx.voices:
                it["v"] = "a"
            it.setdefault("role", "big")
            it.setdefault("on", it["f"][0])
        if kind == "shot":
            carve(ctx, it["f"][0], it["f"][1], [it])
            continue
        ctx.add(it)


# ---------------------------------------------------------------- sound

def sound_table():
    if "t" not in _sound_cache:
        try:
            m = json.load(open(os.path.join(SOUNDS, "manifest.json")))
        except OSError:
            m = []
        _sound_cache["t"] = {x["id"]: x for x in m}
        _sound_cache["roles"] = {}
        for x in m:
            _sound_cache["roles"].setdefault(x.get("role"), []).append(x["id"])
    return _sound_cache["t"], _sound_cache["roles"]


QUIET_HIT = -24.0                     # dB: the least level a placed sound lands at on a film with no voice
VOICE_LAYERS = ("echo", "swell")      # made from the film's own recorded audio. Nothing synthesised, nothing from a library


def place_sound(ctx, sid, spec, f, db, n, opt=None):
    table, roles = sound_table()
    name = str(spec)
    if name.startswith("sound:"):
        name = name[6:]
    if name in VOICE_LAYERS:
        opt = opt or {}
        ctx.sound.append({"id": f"{sid}.snd{n}", "s": name, "event_f": int(clamp(f, 0, ctx.dur_f - 1)), "db": float(db) if isinstance(db, (int, float)) and not isinstance(db, bool) else -8.0,
                          "d_s": float(opt.get("d", 0.7)), "n": int(opt.get("n", 3)), "gap_s": float(opt.get("gap", 0.26)), "start_s": max(0.0, f / ctx.fps - (float(opt.get("d", 0.7)) if name == "swell" else 0.0))})
        return
    if name not in table and name in roles:
        r = rng_for(ctx.seed, "snd", sid, n)
        # a role names a kind of sound, never a thing: a recording that means something else (a typewriter key among
        # the clicks: its manifest `family`) answers only the picture that shows it (score/sound_choice.py)
        pool = [i for i in roles[name] if not table[i].get("family") or table[i]["family"] == name] or roles[name]
        name = r.choice(sorted(pool))
    if name not in table:
        ctx.find(f"{sid}: sound {spec!r} is not an approved sound")
        return
    x = table[name]
    gain = float(db) if isinstance(db, (int, float)) and not isinstance(db, bool) else float(x.get("gain_db", -12))
    f = int(clamp(f, 0, ctx.dur_f - 1))
    start = f / ctx.fps - float(x["peak_s"])
    trim = 0.0
    if start < 0:
        trim, start = -start, 0.0
    ctx.sound.append({"id": f"{sid}.snd{n}", "s": name, "file": x["file"], "event_f": f, "start_s": start, "trim_s": trim, "db": gain, "peak_s": x["peak_s"]})


def track_level(ctx, f):
    """The upload's level at a programme frame, dB, or None: the film's own track, read through the spine."""
    a = ctx.perc.get("audio") or {}
    bins, step = a.get("rms_db"), float(a.get("step", 0.5) or 0.5)
    if not bins:
        return None
    for s in ctx.spine:
        if s["f"][0] <= f < s["f"][1]:
            t = s["src"][0] + (f - s["f"][0]) / ctx.fps
            return float(bins[min(int(t / step), len(bins) - 1)])
    return None


def solve_levels(ctx):
    """A sound the track buries was never placed. Measured on the rated films, a sound at -18 sat 20 dB under a song and
    nothing could find it. The Director's number is the floor it asks for; the compiler lifts it to about what plays."""
    table, _ = sound_table()
    for s in ctx.sound:
        x = table.get(s["s"])
        lvl = track_level(ctx, s["event_f"])
        if x is None or lvl is None:
            continue
        need = lvl - 1 - float(x.get("hit_lufs", -15))
        s["db"] = round(clamp(max(float(s["db"]), need), -36, -2), 1)


def sound_gate(ctx, b, sid, spec, host=None):
    """A sound is a device line: it names what it answers, or it is the sound of a gesture that does. Returns
    (stands, frame to move its peak to or None)."""
    from . import taste
    ctx.wrote["sound"] = ctx.wrote.get("sound", 0) + 1
    ev, why = (None, None) if host else taste.read_for(ctx, b, spec.get("for") if isinstance(spec, dict) else None, "sound")
    bound = taste.bound_by(ctx, sid, "sound") or (ev["ask"] if ev and ev["kind"] == "ask" else None)
    if host and ctx.devices.get(host, {}).get("bound"):
        bound = bound or ctx.devices[host]["bound"]
    gone = _gone(ctx, sid, "sound", bound)
    if gone:
        ctx.taste_rows.append({"id": sid, "did": gone[0], "why": gone[1], "dept": "sound"})
        return False, None
    if not host and ev is None and not bound:
        ctx.rejects.append(((b.raw.get("_line", 0) if b is not None else 0), f"{sid} dropped: it {why}", ""))
        ctx.taste_rows.append({"id": sid, "did": "unearned", "why": why, "dept": "sound"})
        return False, None
    ctx.devices[sid] = {"id": sid, "dept": "sound", "g": "snd", "b": b.id if b is not None else None, "ev": ev, "bound": bound or None, "host": host}
    mv = ctx.plan.get("move_snd", {}).get(sid)
    if mv:
        ctx.taste_rows.append({"id": sid, "did": "repaired", "why": mv[1], "dept": "sound"})
    return True, (int(mv[0]) if mv else None)


def build_sound(ctx):
    for b in ctx.beats:
        specs = b.raw.get("snd")
        if not specs:
            continue
        n = 0
        for spec in (specs if isinstance(specs, list) else [specs]):
            if isinstance(spec, str):
                s, _, at = spec.partition("@")
                at, _, db = at.partition(":")
                spec = {"s": s, "at": at or None, "db": float(db) if db else None}
            if not isinstance(spec, dict) or not spec.get("s"):
                ctx.find(f"{b.id}: can't read a sound line")
                continue
            at = spec.get("at")
            if at is None and isinstance(spec.get("for"), str) and spec["for"] not in ("turn",) and spec["for"] not in ctx.asks:
                at = spec["for"] if ANCHOR_RE.match(spec["for"]) else spec["for"] + ".in"      # it lands on what it answers
            if ctx.governed and spec.get("at") is None and at is not None and frame_of(ctx, at, b) is None:
                ctx.wrote["sound"] = ctx.wrote.get("sound", 0) + 1
                ctx.taste_rows.append({"id": f"{b.id}.snd{n}", "did": "thinned", "dept": "sound", "why": f"the sound of {spec['for']}, which is not on screen"})
                n += 1
                continue
            for f in each_frames(ctx, b, at or f"{b.id}.in"):
                if ctx.governed:
                    ok, to = sound_gate(ctx, b, f"{b.id}.snd{n}", spec)
                    if not ok:
                        n += 1
                        continue
                    f = f if to is None else to
                place_sound(ctx, b.id, spec["s"], f, spec.get("db"), n, spec)
                n += 1
    for b, spec, f, gid in ctx.g_snd:
        if isinstance(spec, dict):
            s, db = spec.get("s"), spec.get("db")
        else:
            s, _, d = str(spec).partition(":")
            db = float(d) if d else None
        if ctx.governed:
            ok, to = sound_gate(ctx, b, f"{gid}.snd0", spec, host=gid)
            if not ok:
                continue
            f = f if to is None else to
        place_sound(ctx, gid, s, f, db, 0)
    if ctx.governed:
        placed = {s["id"] for s in ctx.sound}
        for k in [k for k, d in ctx.devices.items() if d["dept"] == "sound" and k not in placed]:
            del ctx.devices[k]             # not an approved sound: place_sound said so
    else:
        solve_levels(ctx)
    ctx.sound.sort(key=lambda s: (s["event_f"], s["id"]))


def voice_levels(ctx, under_db):
    """A placed sound sits under the voice (TASTE 4.2: 9 dB on a take, 6 mixed, 4.5 over pictures). The Director's
    number is what it asks for; this is the most it gets. It replaces the lift that set every sound at the level
    of the voice (p5591: a median of +7.2 dB over the half second before)."""
    from . import taste
    table, _ = sound_table()
    voice = taste.voice_db(ctx)
    for s in ctx.sound:
        x = table.get(s["s"])
        if ctx.devices.get(s["id"], {}).get("bound"):
            continue
        if x is None or voice is None:
            s["db"] = round(clamp(min(float(s["db"]), -6.0 - under_db), -36, -2), 1)
            continue
        if taste.voiceless(ctx):
            # A film with no words in it has no voice to sit under. "Under the voice" was read off the mean level of
            # its track, so a hit under a title landed 4.5 dB below a street or a song and nobody heard it: 0 or 1
            # added sound could be measured on 11 of 11 first films (13 Nov). With nothing spoken to protect, a placed
            # sound sits 3 dB over what the track plays at that moment: heard once, on the thing that lands.
            # "that moment" is the second the sound rings for, not the half second it starts in: an impact under an
            # opening title started in the track's fade-in and was set against that, 8 dB under the song a beat later
            lvls = [track_level(ctx, g) for g in range(s["event_f"], min(s["event_f"] + ctx.fps + 1, ctx.dur_f), max(1, ctx.fps // 4))]
            lvls = [v for v in lvls if v is not None]
            lvl = max(lvls) if lvls else None
            here = float(voice) if lvl is None else max(float(lvl), float(voice) - 12.0)
            # and never under QUIET_HIT: over a near-silent room "3 dB over the track" is still silence (p5582: -31 dB)
            s["db"] = round(clamp(max(here + 3.0, QUIET_HIT) - float(x.get("hit_lufs", -15)), -36, -2), 1)
            continue
        most = float(voice) - under_db - float(x.get("hit_lufs", -15))
        s["db"] = round(clamp(min(float(s["db"]), most), -36, -2), 1)


# ---------------------------------------------------------------- finish

def mono_cap(ctx):
    """B&W stays a device. Past the share, later shots get their colour back."""
    total = 0
    limit = int(MONO_SHARE * ctx.dur_f)
    hit = False
    for s in ctx.shots:
        g = s.get("grade")
        t = s.get("treat") or {}
        if (g and g.get("sat", 1) <= MONO_SAT) or t.get("mode") == "mono":
            n = s["f"][1] - s["f"][0]
            total += n
            if total > limit:
                hit = True
                if g:
                    s["grade"] = {**g, "sat": max(0.5, ctx.base_grade["sat"])}
                if t.get("mode") == "mono":
                    s.pop("treat", None)
    if hit:
        ctx.find("mono: black and white over most of the film is a look, not a device. Colour returned on the later shots")
    if ctx.base_grade.get("sat", 1) <= MONO_SAT:
        ctx.base_grade["sat"] = 0.5
        ctx.film_out["grade"]["sat"] = 0.5
        ctx.find("mono: a film-wide black and white grade was refused")


TRAFFIC_GAP = 0.01            # the least clear space between two type items, in frame fractions
TRAFFIC_MIN_S = 0.8           # a delayed item still holds this long, or it goes


def type_rect(ctx, it):
    """Where a type item draws over its whole life, in frame fractions: every frame's position, at its
    largest scale. A word's box runs from its cap height to under its descenders. None for what isn't type."""
    if it["k"] == "word":
        v = ctx.voices.get(it.get("v"))
        if not v:
            return None
        w = text_px(it["t"], v) / ctx.W
        asc, desc = 0.74 * v["px"] / ctx.H, 0.22 * v["px"] / ctx.H
        xs = it["x"] if isinstance(it["x"], list) else [it["x"]]
        ys = it["y"] if isinstance(it["y"], list) else [it["y"]]
        r = [min(xs), min(ys) - asc, max(xs) + w, max(ys) + desc]
    elif it["k"] == "card" and it.get("pic") == "plate":
        xs = it["x"] if isinstance(it["x"], list) else [it["x"]]
        ys = it["y"] if isinstance(it["y"], list) else [it["y"]]
        r = [min(xs) - it["w"] / 2, min(ys) - it["h"] / 2, max(xs) + it["w"] / 2, max(ys) + it["h"] / 2]
    else:
        return None
    sv = it.get("s", 1)
    sc = max(sv) if isinstance(sv, list) and sv else (float(sv) if isinstance(sv, (int, float)) else 1.0)
    if sc > 1.0:
        cx, cy, hw, hh = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, (r[2] - r[0]) * sc / 2, (r[3] - r[1]) * sc / 2
        r = [cx - hw, cy - hh, cx + hw, cy + hh]
    return [max(r[0], 0.0), r[1], min(r[2], 1.0), r[3]]


def _grow(r, m):
    return [r[0] - m, r[1] - m, r[2] + m, r[3] + m]


def _shift_item(it, dy):
    """The same item, dy lower (negative: higher). Every position it carries moves together."""
    for key in ("y",):
        v = it.get(key)
        if isinstance(v, list):
            it[key] = [q + dy for q in v]
        elif isinstance(v, (int, float)):
            it[key] = v + dy
    if isinstance(it.get("region"), list) and len(it["region"]) == 4:
        r = it["region"]
        it["region"] = [r[0], r[1] + dy, r[2], r[3] + dy]
    rect = it.get("rect")
    if isinstance(rect, list) and rect:
        if isinstance(rect[0], list):
            it["rect"] = [[q[0], q[1] + dy, q[2], q[3] + dy] for q in rect]
        elif len(rect) == 4:
            it["rect"] = [rect[0], rect[1] + dy, rect[2], rect[3] + dy]


def _faces_over(ctx, a, z):
    out = []
    for f in sorted(set(range(a, z, 15)) | {max(a, z - 1)}):
        out += face_rects(ctx, shot_at(ctx, f))
    return out


def type_traffic(ctx):
    """No two type items share a place at the same time. Display words, chips and their labels, counters,
    tickers and type-on lines are each one item: everything one gesture drew moves together, and what one
    gesture lays over itself (a counter's steps, a chip's word on its label, a size ladder) is its own
    design. Across gestures nothing layers type on type by design. Captions are the voice and hold their
    seats. When the Score asks for a taken place, the later item moves to the nearest clear height, then
    waits until the place is free, then goes; ctx.traffic and the meter say which. A running line (a ticker)
    never crosses a face either. p5565: a ticker, two bigs and a chip drawn through each other."""
    units, fixed = {}, []
    for it in ctx.items:
        if it["k"] == "word" and it.get("role") == "cap":
            r = type_rect(ctx, it)
            if r:
                fixed.append((it["f"][0], it["f"][1], r, "caption"))
            continue
        key = it.get("_g") or it["id"]
        units.setdefault(key, []).append(it)
    rows = []
    for key, members in units.items():
        words = [m for m in members if m["k"] == "word"]
        if not words:
            continue
        rects = [r for r in (type_rect(ctx, m) for m in members) if r]
        if not rects:
            continue
        a, z = min(w["f"][0] for w in words), max(w["f"][1] for w in words)
        rect = [min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects)]
        running = any(isinstance(w.get("x"), list) and max(w["x"]) - min(w["x"]) > 0.3 for w in words)
        rows.append({"key": key, "a": a, "z": z, "rect": rect, "items": members, "running": running})
    rows.sort(key=lambda u: (u["a"], u["key"]))
    kept = list(fixed)
    gone, cut = set(), {}
    ends = {}           # unit key -> the frame it leaves at, when the next label took its seat

    def hits(rect, a, z, faces):
        out = [k for k in kept if k[0] < z and a < k[1] and rects_touch(_grow(rect, TRAFFIC_GAP / 2), _grow(k[2], TRAFFIC_GAP / 2))]
        return out + [(a, z, f, "face") for f in faces if rects_touch(rect, f)]

    def seat(u, a, z, faces):
        """The smallest move up or down that clears every type item, face and the watermark."""
        r = u["rect"]
        hard = [k[2] for k in kept if k[0] < z and a < k[1]] + _faces_over(ctx, a, z) + [list(KEEPOUT)]
        # words printed in the picture are the picture's: a seat clear of them first, then any clear seat
        for obst in (hard + mark_rects(ctx, a, z), hard):
            tries = sorted({0.0} | {q[3] + TRAFFIC_GAP - r[1] for q in obst} | {q[1] - TRAFFIC_GAP - r[3] for q in obst}, key=abs)
            top, bottom = (0.10, 0.80) if ctx.governed and ctx.H > ctx.W else (0.03, 0.96)     # P2: the safe area
            for dy in tries:
                m = [r[0], r[1] + dy, r[2], r[3] + dy]
                if m[1] < top or m[3] > bottom:
                    continue
                if not any(rects_touch(m, q) for q in obst):
                    return dy
        return None

    for u in rows:
        faces = _faces_over(ctx, u["a"], u["z"]) if u["running"] else []
        hit = hits(u["rect"], u["a"], u["z"], faces)
        if not hit:
            kept.append((u["a"], u["z"], u["rect"], u["key"]))
            continue
        against = sorted({h[3] for h in hit})
        # One seat, one label at a time. A label that lands where a title or a label of an earlier beat still stands,
        # once that one has been read for half a second, takes the seat and the earlier one leaves: it is not pushed
        # up the frame (p5575, 13 Nov: 01 wake landed above the film's title and 05 on her neck).
        if ".chip" in str(u["key"]) and all(h[3] not in ("caption", "face") for h in hit):
            beat = str(u["key"]).split(".")[0]
            olds = [h for h in hit if str(h[3]).split(".")[0] != beat and u["a"] - h[0] >= int(0.5 * ctx.fps)]
            if olds and len(olds) == len(hit):
                gone_keys = {h[3] for h in olds}
                for kq in gone_keys:
                    ends[kq] = min(ends.get(kq, 10 ** 9), u["a"])
                kept[:] = [(k[0], min(k[1], u["a"]), k[2], k[3]) if k[3] in gone_keys else k for k in kept]
                kept.append((u["a"], u["z"], u["rect"], u["key"]))
                continue
        dy = seat(u, u["a"], u["z"], faces)
        if dy is not None:
            for it in u["items"]:
                _shift_item(it, dy)
            u["rect"] = [u["rect"][0], u["rect"][1] + dy, u["rect"][2], u["rect"][3] + dy]
            kept.append((u["a"], u["z"], u["rect"], u["key"]))
            ctx.traffic.append({"id": u["key"], "did": "moved", "dy": round(dy, 3), "against": against})
            continue
        a2 = max(h[1] for h in hit)
        if u["z"] - a2 >= TRAFFIC_MIN_S * ctx.fps and not hits(u["rect"], a2, u["z"], faces):
            for it in u["items"]:
                p, q = it["f"]
                if q <= a2:
                    gone.add(it["id"])
                elif p < a2:
                    cut[it["id"]] = a2
            kept.append((a2, u["z"], u["rect"], u["key"]))
            ctx.traffic.append({"id": u["key"], "did": "delayed", "frames": a2 - u["a"], "against": against})
            continue
        gone.update(it["id"] for it in u["items"])
        ctx.traffic.append({"id": u["key"], "did": "dropped", "against": against})
    if gone or cut or ends:
        out = []
        for it in ctx.items:
            if it["id"] in gone:
                continue
            if it["id"] in cut:
                it = sub_item(it, cut[it["id"]], it["f"][1], it["id"])
                if "on" in it:
                    it["on"] = max(it["on"], it["f"][0])
            e = ends.get(it.get("_g") or it["id"]) if not (it["k"] == "word" and it.get("role") == "cap") else None
            if e is not None and it["f"][1] > e:
                if it["f"][0] >= e:
                    ctx.byid.pop(it["id"], None)
                    continue
                it = sub_item(it, it["f"][0], e, it["id"])
            out.append(it)
        ctx.items = out
        for i in gone:
            ctx.byid.pop(i, None)
    for t in ctx.traffic:
        if t.get("at") != "seat":
            ctx.find(f"type {t['id']} {t['did']}: its place was taken by {', '.join(t['against'])}")


def _shift_x(it, dx):
    v = it.get("x")
    if isinstance(v, list):
        it["x"] = [q + dx for q in v]
    elif isinstance(v, (int, float)):
        it["x"] = v + dx
    if isinstance(it.get("region"), list) and len(it["region"]) == 4:
        r = it["region"]
        it["region"] = [r[0] + dx, r[1], r[2] + dx, r[3]]
    rect = it.get("rect")
    if isinstance(rect, list) and rect:
        if isinstance(rect[0], list):
            it["rect"] = [[q[0] + dx, q[1], q[2] + dx, q[3]] for q in rect]
        elif len(rect) == 4:
            it["rect"] = [rect[0] + dx, rect[1], rect[2] + dx, rect[3]]


def safe_sweep(ctx):
    """P2: added display type and labels sit inside the safe area of a vertical frame, left of the platform's own
    buttons. Everything one gesture drew moves together. Moved, never dropped. Captions hold the film's seat."""
    from . import taste
    if ctx.H <= ctx.W:
        return
    x0, y0, x1, y1 = taste.SAFE
    bx, by0, _, by1 = taste.BUTTONS
    units = {}
    for it in ctx.items:
        if it["k"] == "word" and it.get("role") == "cap":
            continue
        if it["k"] in ("word", "flash") or (it["k"] == "card" and it.get("pic") == "plate"):
            units.setdefault(it.get("_g") or it["id"].split(".pool")[0], []).append(it)
    for key, members in units.items():
        if ctx.devices.get(key, {}).get("bound"):
            continue
        rects = [r for r in (type_rect(ctx, m) for m in members) if r]
        if not rects:
            continue
        r = [min(q[0] for q in rects), min(q[1] for q in rects), max(q[2] for q in rects), max(q[3] for q in rects)]
        running = any(isinstance(m.get("x"), list) and max(m["x"]) - min(m["x"]) > 0.3 for m in members)
        dy = 0.0
        if r[3] - r[1] <= y1 - y0:
            dy = y0 - r[1] if r[1] < y0 else y1 - r[3] if r[3] > y1 else 0.0
        dx = 0.0
        if not running and r[2] - r[0] <= x1 - x0:
            dx = x0 - r[0] if r[0] < x0 else x1 - r[2] if r[2] > x1 else 0.0
            if r[2] + dx > bx and r[3] + dy > by0 and r[1] + dy < by1 and r[2] - r[0] <= bx - x0:
                dx = bx - r[2]
        if abs(dx) < 1e-4 and abs(dy) < 1e-4:
            continue
        for m in members:
            if dy:
                _shift_item(m, dy)
            if dx:
                _shift_x(m, dx)
        ctx.taste_rows.append({"id": key, "did": "repaired", "dept": "display", "why": f"moved inside the safe area ({dx:+.2f}, {dy:+.2f})"})


def keepout_sweep(ctx):
    """The last word on the watermark: any type that still reaches the zone, on any frame,
    moves down clear of it. Moving type moves only on the frames that touch."""
    for it in ctx.items:
        if it["k"] != "word":
            continue
        v = ctx.voices.get(it["v"])
        if not v:
            continue
        w = text_px(it["t"], v) / ctx.W
        h = 0.74 * v["px"] / ctx.H
        n = it["f"][1] - it["f"][0]
        xs = it["x"] if isinstance(it["x"], list) else [it["x"]] * n
        ys = it["y"] if isinstance(it["y"], list) else [it["y"]] * n
        out = [y + dodge_keepout(x, y - h, x + w, y) for x, y in zip(xs, ys)]
        if out != ys:
            it["y"] = out if len(set(out)) > 1 else out[0]


def flash_gate(ctx):
    """A health rule, on declared flashes: 3 a second on the customer path (DESIGN 5.4). A
    flash takes a quarter of the frame or more. Past that, later ones are thinned. Above the
    gate needs film.flash_ok, set only from the customer's own request."""
    if ctx.film.get("flash_ok"):
        return
    ev = []
    for it in ctx.items:
        if it["k"] != "flash" or it.get("mode") in ("pool", "recolour"):
            continue
        r = it.get("rect") or [0, 0, 1, 1]
        r = r[0] if isinstance(r[0], list) else r
        a = _last(it.get("a", 1))
        if (r[2] - r[0]) * (r[3] - r[1]) >= 0.25 and it["f"][1] - it["f"][0] <= 8 and a > 0.3:
            ev.append((it["f"][0], 0, it))
    for s in ctx.shots:
        if s.get("treat") and s["f"][1] - s["f"][0] <= 4:
            ev.append((s["f"][0], 1, s))
    ev.sort(key=lambda e: (e[0], e[1], e[2]["id"]))
    kept, cut, gone = [], 0, set()
    for f, kind, it in ev:
        if sum(1 for x in kept if x > f - ctx.fps) >= 3:
            cut += 1
            if kind:
                it.pop("treat", None)
            else:
                gone.add(it["id"])
        else:
            kept.append(f)
    if gone:
        ctx.items = [i for i in ctx.items if i["id"] not in gone]
    if cut:
        ctx.find(f"flash gate: {cut} thinned, 3 a second is a health rule here")


def seal(ctx):
    """Close every gap in the base, clip everything to the film, drop what fell off."""
    shots, cur = [], 0
    for s in sorted(ctx.shots, key=lambda x: x["f"][0]):
        p, q = s["f"]
        q = min(q, ctx.dur_f)
        if q <= cur:
            continue
        if p > cur and shots:
            shots[-1] = sub_extend(shots[-1], p)
        elif p > cur:
            shots.append({"id": f"{s['id']}.head", "k": "shot", "f": [cur, p], "pic": "plate", "plate": _plate("#000000", "#000000", 0)})
        if p < cur:
            s = sub_item(s, cur, q, s["id"])
        if s["f"][1] != q:
            s = sub_item(s, s["f"][0], q, s["id"])
        shots.append(s)
        cur = q
    if cur < ctx.dur_f and shots:
        shots[-1] = sub_extend(shots[-1], ctx.dur_f)
    seen = set()
    for s in shots:
        i, n = s["id"], 1
        while s["id"] in seen:
            s["id"] = f"{i}.{n}"
            n += 1
        seen.add(s["id"])
    ctx.shots = shots
    keep = []
    for it in ctx.items:
        a, z = it["f"]
        if a >= ctx.dur_f or z <= 0:
            continue
        if a < 0 or z > ctx.dur_f:
            it = sub_item(it, max(a, 0), min(z, ctx.dur_f), it["id"])
        keep.append(it)
    ctx.items = keep


def sub_extend(s, q):
    """Stretch the last shot over a gap: the picture holds, its lists hold their last value."""
    s = dict(s)
    n0, n1 = s["f"]
    add = q - n1
    for key in ("crop", "op", "s", "blur"):
        v = s.get(key)
        if isinstance(v, list) and len(v) == n1 - n0 and (key != "crop" or isinstance(v[0], list)):
            s[key] = v + [v[-1]] * add
    if s.get("src"):
        rate = (s["src"][1] - s["src"][0]) / max(1, n1 - n0)
        s["src"] = [s["src"][0], s["src"][1] + rate * add]
    s["f"] = [n0, q]
    return s


STAGE = {"shot": 0, "card": 1, "flash": 2, "word": 3}


def to_l1(ctx):
    items = []
    for it in ctx.shots + ctx.items:
        it = {k: v for k, v in it.items() if not k.startswith("_")}
        items.append(rf(it))
    items.sort(key=lambda i: (i["f"][0], STAGE.get(i["k"], 9), i.get("z", 0), i["id"]))
    if ctx.matte and any(i.get("behind") for i in items):
        ctx.assets["matte"] = {"kind": "video", "path": ctx.matte, "w": 1080, "h": 1920, "dur": ctx.src_dur, "fps": 30.0}
    out = {"l1": 1, "sid": ctx.sid, "seed": ctx.seed, "fps": ctx.fps, "frame": [ctx.W, ctx.H], "dur_f": ctx.dur_f,
           "assets": rf(ctx.assets), "spine": rf(ctx.spine), "voices": rf(ctx.voices), "film": rf(ctx.film_out),
           "items": items, "sound": rf(ctx.sound)}
    if getattr(ctx, "alien", None):
        out["alien"] = rf(ctx.alien)          # asks for scene.alien.apply, by the alien gesture; no item of the scene depends on them
    return out


def validate_l1(l1):
    """The contract, checked. Returns a list of problems, empty when it holds."""
    bad = []
    D = l1["dur_f"]
    ids = set()
    shots = []
    for it in l1["items"]:
        i, a, z = it.get("id"), it["f"][0], it["f"][1]
        if not i or i in ids:
            bad.append(f"id {i!r} missing or repeated")
        ids.add(i)
        if not 0 <= a < z <= D:
            bad.append(f"{i}: f {it['f']} outside the film")
        n = z - a
        if it["k"] not in STAGE:
            bad.append(f"{i}: kind {it['k']}")
        for key in ("x", "y", "s", "op", "rot", "a", "th", "blur"):
            v = it.get(key)
            if isinstance(v, list) and len(v) != n:
                bad.append(f"{i}: {key} has {len(v)} values for {n} frames")
        for key in ("crop", "rect"):
            v = it.get(key)
            if not v:
                continue
            rects = v if isinstance(v[0], list) else [v]
            if isinstance(v[0], list) and len(v) != n:
                bad.append(f"{i}: {key} has {len(v)} rects for {n} frames")
            # The upload's own shot may pass its edges when it is held whole in a frame of another shape:
            # the lowering fits it on a blurred copy of itself (keep_marks_whole, lower.base_window)
            past_ok = key == "crop" and it["k"] == "shot" and it.get("pic") == "main"
            for r in rects:
                if key == "crop" and (r[2] <= r[0] or r[3] <= r[1] or not past_ok and (min(r) < -1e-3 or max(r) > 1 + 1e-3)):
                    bad.append(f"{i}: crop {r} leaves the source")
        if it["k"] == "shot":
            shots.append(it)
            if it.get("pic") not in l1["assets"] and it.get("pic") != "plate":
                bad.append(f"{i}: no asset for {it.get('pic')}")
        if it["k"] == "word":
            if not it.get("t") or it.get("v") not in l1["voices"]:
                bad.append(f"{i}: word without text or voice")
    cur = 0
    for s in sorted(shots, key=lambda x: x["f"][0]):
        if s["f"][0] != cur:
            bad.append(f"shot base breaks at {cur}: {s['id']} starts {s['f'][0]}")
        cur = s["f"][1]
    if cur != D:
        bad.append(f"shot base ends at {cur}, film at {D}")
    for s in l1["sound"]:
        if "peak_s" in s and abs(s["start_s"] - max(0.0, s["event_f"] / l1["fps"] - s["peak_s"])) > 1e-3:
            bad.append(f"{s['id']}: start_s is not event minus peak")
    return bad


# ---------------------------------------------------------------- meter

STAVES = (("job", "story"), ("cut", "picture"), ("pic", "sources"), ("type", "type"), ("do", "design"), ("look", "look"), ("snd", "sound"))


def rest_check(ctx):
    """Every beat fills all seven staves, inherited or rested. A blank is visible, never fatal."""
    out = {}
    for b in ctx.beats:
        if b.auto:
            out[b.id] = ["auto"]
            continue
        rest = b.raw.get("rest") if isinstance(b.raw.get("rest"), dict) else {}
        blanks = []
        for f, name in STAVES:
            v = b.raw.get(f)
            if v not in (None, "", []):
                continue
            if ctx.governed and f in ("do", "snd"):
                continue            # empty unless something earns them: restraint needs no reason
            if f in rest or name in rest:
                continue
            if f == "look" and (ctx.dna.get("look") or ctx.film.get("look")):
                continue
            if f == "cut" and (ctx.film.get("pulse") or ctx.band_name):
                continue
            if f == "pic" and ctx.dna.get("pic"):
                continue
            if f == "type" and any(isinstance(g, dict) and g.get("g") in ("line", "big", "page", "chip", "counter", "type_on", "ticker") for g in (b.raw.get("do") or [])):
                continue            # type on the display clock is type
            blanks.append(f)
        if blanks:
            out[b.id] = blanks
    return out


def distinct_cuts(shots):
    """Boundaries where the picture really changes: a source, a framing or a jump."""
    out = []
    for p, q in zip(shots, shots[1:]):
        same = p.get("pic") == q.get("pic") and p.get("plate") == q.get("plate") and not p.get("treat") and not q.get("treat")
        if same and "src" in p and "src" in q:
            same = abs(p["src"][1] - q["src"][0]) < 0.05 and _crop_end(p) == _crop_start(q)
        elif same and "crop" in p and "crop" in q:
            same = _crop_end(p) == _crop_start(q)
        if not same:
            out.append(q["f"][0])
    return out


def _crop_end(s):
    c = s.get("crop")
    return None if c is None else [round(x, 3) for x in (c[-1] if isinstance(c[0], list) else c)]


def _crop_start(s):
    c = s.get("crop")
    return None if c is None else [round(x, 3) for x in (c[0] if isinstance(c[0], list) else c)]


def new_pictures(ctx, shots):
    """Cuts where the picture really changes source. A crop of the same take is a new framing, and on a dark locked-off
    shot neither a stranger nor the judge's cut counter sees it as a cut (p5374: 29 planned cuts read as 10)."""
    n, last = 0, None
    for s in sorted(shots, key=lambda i: i["f"][0]):
        p = str(s.get("framed") or s.get("pic"))          # a still framed on the plate is that still
        q = "take" if p == "main" or p.startswith("own:") else p
        if s.get("view") is not None:
            q = f"view:{s['view']}"                       # a detail of the upload cut in as an insert is its own picture
        if last is not None and q != last:
            n += 1
        last = q
    return n


def word_lock(ctx, cuts):
    """Share of cuts within 60 ms of a word start, against the chance level of this film."""
    frames = sorted(set(ctx.wf_any.values()))
    if not frames or not cuts:
        return None, None
    win = 0.06 * ctx.fps
    near = sum(1 for c in cuts if any(abs(c - f) <= win for f in frames))
    cover = set()
    for f in frames:
        for x in range(int(math.floor(f - win)), int(math.ceil(f + win)) + 1):
            cover.add(x)
    return near / len(cuts), min(1.0, len(cover) / max(1, ctx.dur_f))


def ceiling(ctx, l1, cuts):
    """What rubric v3 arithmetic allows, from the plan's structure. Reads what a film cannot
    reach, never that it is good: overall <= lowest craft dimension + 2.5."""
    items = l1["items"]
    kinds = {}
    for i in items:
        kinds.setdefault(i["k"], []).append(i)
    pics = {i.get("pic") for i in items if i["k"] in ("shot", "card")}
    # found pictures count by the time they hold the frame: 2 s of cards in a 51 s film is still the upload's own frames
    found_f = sum(i["f"][1] - i["f"][0] for i in kinds.get("shot", []) if str(i.get("framed") or i.get("pic")).startswith(("bin:", "tray:")))
    found_f += sum((i["f"][1] - i["f"][0]) * 0.5 for i in kinds.get("card", []) if str(i.get("pic")).startswith(("bin:", "tray:")) and i["f"][1] - i["f"][0] > 6
                   and not i.get("framed"))
    added = found_f >= 0.1 * ctx.dur_f
    cutouts = any(str(p).startswith("own:") for p in pics)
    designed = bool(kinds.get("card")) or any(i.get("pic") == "plate" for i in kinds.get("shot", []))
    dims = []
    if not added:
        reframed = designed or (not ctx.governed and len({str(_crop_start(s)) for s in kinds.get("shot", []) if s.get("pic") == "main"}) > 2)
        reframed = reframed or cutouts
        dims.append(("footage_broll", 5 if reframed else 3, "upload's own frames" + (", re-framed by design" if reframed else "")))
    if not l1["sound"]:
        dims.append(("sound", 2, "nothing added to the source audio"))
    grade_used = ctx.base_grade != IDENTITY or any(i.get("grade") for i in items) or bool(ctx.film_out.get("look"))
    if not grade_used:
        dims.append(("colour_light", 2, "the source's colours untouched"))
    if not (kinds.get("card") or kinds.get("flash") or any(isinstance(i.get("crop", [0])[0], list) for i in kinds.get("shot", []))):
        dims.append(("motion_design", 1, "no card, flash or camera"))
    if not kinds.get("word"):
        dims.append(("type_captions", 1, "no type at all"))
    low = min(dims, key=lambda d: d[1]) if dims else None
    return (low[1] + 2.5, low) if low else (10.0, None)


def compile_facts(ctx, l1, cuts, per_frame):
    """DESIGN 5.3: the plan against the rubric's measured lines. An alarm, never a target."""
    items = l1["items"]
    shots = [i for i in items if i["k"] == "shot"]
    D = max(1, ctx.dur_f)
    cover = [0] * D
    for i in items:
        if i["k"] == "card" or (i["k"] == "word" and i.get("role") == "big") or (i["k"] == "flash" and i.get("mode") != "pool"):
            for f in range(i["f"][0], i["f"][1]):
                cover[f] = 1
    not_take = sum(s["f"][1] - s["f"][0] for s in shots if s.get("pic") != "main")
    moved = sum(s["f"][1] - s["f"][0] for s in shots if s.get("pic") == "main" and _crop_start(s) not in (None, [0.0, 0.0, 1.0, 1.0]))
    overlay = (sum(cover) + not_take) / D
    sources = {s.get("pic") for s in shots if str(s.get("pic")).startswith(("bin:", "tray:", "own:"))}
    sources |= {i.get("pic") for i in items if i["k"] == "card" and str(i.get("pic")).startswith(("bin:", "tray:", "own:"))}
    plates = sum(1 for s in shots if s.get("pic") == "plate")
    first = shots[0] if shots else {}
    src0 = l1["spine"][0]["src"][0] if l1["spine"] else 0.0
    words0 = ctx.words[0]["t0"] if ctx.words else 0.0
    opening = first.get("pic") != "main" or abs(src0 - max(0.0, words0 - PAD_IN)) > 0.3
    designed = sum(1 for i in items if i["k"] == "card" or (i["k"] == "word" and i.get("role") == "big" and not i["id"].split(".")[-1].startswith("p"))
                   or (i["k"] == "flash" and i.get("mode") != "pool"))
    refills = len(sources) + plates
    grade = ctx.base_grade != IDENTITY or any(i.get("grade") for i in items) or bool(ctx.film_out.get("look"))
    if refills == 0 and not cuts and overlay < 0.12 and not grade:
        kind = "same_upload"
    elif refills == 0 and overlay < 0.12 and moved / D < 0.25:
        kind = "light_touch"
    else:
        kind = "edited"
    # the upload again but for its colour: no picture added, no cut moved, type on under an eighth of it (film.bare).
    # A wordless stretch cut inside itself (score.pace) is the compiler's own doing, not a film anyone directed: with
    # nothing else on it the film is still bare, and today's first cut is delivered
    punched = {s["f"][0] for s in shots if s.get("punch")}
    bare = refills == 0 and not [c for c in cuts if c not in punched] and overlay < 0.12
    return {"class": kind, "bare": bare, "opening_changed": opening, "new_sources": len(sources), "plates": plates, "pictures_added": refills,
            "overlay_pct": round(overlay * 100), "reframed_pct": round(moved / D * 100), "grade_applied": grade, "sfx_added": len(l1["sound"]),
            "design_items10": round(designed / max(ctx.dur_f / ctx.fps, 1e-3) * 10, 1)}


def quiet_meter(ctx, l1, cuts):
    """One thing stays quiet: seconds in which picture, type and camera are all busy."""
    fps, D = ctx.fps, ctx.dur_f
    moving = [0] * D
    for i in l1["items"]:
        if i["k"] in ("shot", "card") and isinstance(i.get("crop", [0])[0], list):
            for f in range(i["f"][0], min(i["f"][1], D)):
                moving[f] = 1
    onsets = [i["on"] for i in l1["items"] if i["k"] == "word" and i.get("role") == "big" and not i["id"].split(".")[-1].startswith("p")]
    onsets += [i["on"] for i in l1["items"] if i["k"] == "word" and i.get("role") == "cap"]
    out, f = 0, 0
    while f < D:
        z = min(D, f + fps)
        pic = sum(1 for c in cuts if f <= c < z) >= 3
        typ = sum(1 for o in onsets if f <= o < z) >= 5
        cam = sum(moving[f:z]) > fps * 0.6
        if pic and typ and cam:
            out += 1
        f += fps
    return out


JOINS = ("whip", "slide", "shred", "shatter")


def source_mix(ctx, l1):
    """Seconds of film by where the picture comes from. A crop of the take is still the take."""
    fps = ctx.fps
    take = crops = lifted = found = pages = 0
    seen = set()
    for s in l1["items"]:
        if s["k"] == "card" and str(s.get("pic")).startswith(("bin:", "tray:")) and s["f"][1] - s["f"][0] > 4:
            seen.add(s["pic"])
        if s["k"] != "shot":
            continue
        n = s["f"][1] - s["f"][0]
        p = str(s.get("pic"))
        if p == "main":
            c = _crop_start(s)
            if c in (None, [0.0, 0.0, 1.0, 1.0]):
                take += n
            else:
                crops += n
        elif p.startswith("own:"):
            lifted += n
        elif p == "plate" and s.get("framed"):
            found += n                      # a still shown whole on the plate holds the frame as itself
            seen.add(s["framed"])
        elif p == "plate":
            pages += n
        else:
            found += n
            seen.add(p)
    return {"take": round(take / fps, 1), "crops": round(crops / fps, 1), "lifted": round(lifted / fps, 1), "found": round(found / fps, 1),
            "pages": round(pages / fps, 1), "found_items": len(seen), "bin_items": len(ctx.bin)}


def layer_facts(ctx, l1):
    """Seconds where the frame is thin, read from the plan: a plate with one line on it and nothing moving, a found picture
    held still. And what the first second is, and how much of the film the placed sounds cover."""
    fps, D = ctx.fps, ctx.dur_f
    shots = sorted((i for i in l1["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    cards = [i for i in l1["items"] if i["k"] == "card" and i["f"][1] - i["f"][0] > 6]
    word_on = [i for i in l1["items"] if i["k"] == "word"]
    flat = held = 0
    longest_flat = 0.0
    for s in shots:
        n = s["f"][1] - s["f"][0]
        moving = isinstance(s.get("crop"), list) and s["crop"] and isinstance(s["crop"][0], list) and s["crop"][0] != s["crop"][-1]
        covered = any(c["f"][0] < s["f"][1] and c["f"][1] > s["f"][0] for c in cards)
        if s.get("pic") == "plate" and not covered:
            on = {str(w.get("chunk") or w["id"].split(".")[0] + w["id"].split(".")[1]) for w in word_on if w["f"][0] < s["f"][1] and w["f"][1] > s["f"][0]}
            if n >= fps and len(on) <= 2:
                flat += n
                longest_flat = max(longest_flat, n / fps)
        elif str(s.get("pic")).startswith("bin:") and not moving and n >= 1.2 * fps:
            held += n
    first = next((s for s in shots if s["f"][0] <= int(fps / 2) < s["f"][1]), shots[0] if shots else None)
    found = {}
    for s in shots:
        if str(s.get("pic")).startswith("bin:"):
            arr = thumbs(ctx, s["pic"])
            if arr is not None and len(arr):
                gain = (s.get("grade") or {}).get("gain", 1.0)
                found.setdefault(s["pic"], float(arr.mean()) * gain)
    take = take_light(ctx)[0]
    cover = bytearray(D)
    table, _ = sound_table()
    for q in l1["sound"]:
        dur = float((table.get(q["s"]) or {}).get("duration_s", q.get("n", 1) * q.get("gap_s", 0.3) + q.get("d_s", 0)))
        a = int(q["start_s"] * fps)
        for f in range(max(a, 0), min(a + int(dur * fps), D)):
            cover[f] = 1
    return {"flat_s": round(flat / fps, 1), "flat_longest": round(longest_flat, 1), "found_held_s": round(held / fps, 1),
            "first": str((first or {}).get("pic")), "sound_cover": round(sum(cover) / max(1, D), 2),
            "take_luma": None if take is None else round(take, 3), "found_luma": round(sum(found.values()) / len(found), 3) if found else None}


def gesture_counts(ctx):
    """How many of each gesture the Score reached for, read from the beats."""
    out = {}
    for b in ctx.beats:
        do = b.raw.get("do")
        for g in do if isinstance(do, list) else []:
            if isinstance(g, dict) and g.get("g"):
                out[str(g["g"])] = out.get(str(g["g"]), 0) + 1
    return out


def meter(ctx, l1):
    fps = ctx.fps
    dur = ctx.dur_f / fps
    shots = [i for i in l1["items"] if i["k"] == "shot"]
    words = [i for i in l1["items"] if i["k"] == "word"]
    cards = [i for i in l1["items"] if i["k"] == "card"]
    flashes = [i for i in l1["items"] if i["k"] == "flash"]
    cuts = distinct_cuts(shots)
    lens = []
    last = 0
    for c in cuts + [ctx.dur_f]:
        lens.append((c - last) / fps)
        last = c
    lens.sort()
    caps = [w for w in words if w.get("role") == "cap"]
    per_frame = [0] * ctx.dur_f
    for w in words:
        for f in range(w["f"][0], min(w["f"][1], ctx.dur_f)):
            per_frame[f] += 1
    with_text = sum(1 for x in per_frame if x)
    events = len({(w["on"], w["id"].split(".p")[0]) for w in words if ".counter." not in w["id"]}) + len({w["id"].split(".counter.")[0] for w in words if ".counter." in w["id"]})
    lock, chance = word_lock(ctx, cuts)
    beats = []
    for b in ctx.beats:
        n = sum(1 for c in cuts if b.pf0 <= c < b.pf1) + (1 if b.pf0 in ([0] + cuts) else 0)
        tot = max(1, b.pf1 - b.pf0) / fps
        snd = sum(1 for s in l1["sound"] if b.pf0 <= s["event_f"] < b.pf1)
        wn = len({w["id"].split(".p")[0] for w in words if b.pf0 <= w["on"] < b.pf1})
        des = sum(1 for i in cards + flashes if b.pf0 <= i["f"][0] < b.pf1 and i.get("mode") != "pool")
        beats.append({"id": b.id, "sec": round(tot, 2), "pics10": round(n / tot * 10, 1), "type": wn, "design": des, "snd": snd})
    blanks = rest_check(ctx)
    cap, low = ceiling(ctx, l1, cuts)
    # the first second: what an editor adds has to be there by then (rubric: the upload's opening kept as it is scores 1).
    # 13 Nov: with a seat on the take the hook title was still written on the third word and landed at 2.2 s
    lands = [w.get("on", w["f"][0]) for w in words if w.get("role") == "big"] + [c["f"][0] for c in cards if c.get("pic") != "plate" or c["f"][0] > 0] \
        + [s_["f"][0] for s_ in shots if s_.get("pic") != "main" and not str(s_.get("pic")).startswith("own:")]
    hook_f = min(lands) if lands else None
    if ctx.governed and dur >= 5.0 and (hook_f is None or hook_f > fps):
        ctx.find("HOOK the first second is the upload as it was: " + ("nothing designed lands in this film" if hook_f is None else f"the first title, card or picture lands at {hook_f / fps:.1f} s")
                 + ". The hook title lands at the first beat's `in`: on the take's display seat, beside a found picture that opens the film, or on a page of 1 s")
    first3 = sum(1 for c in cuts if c <= 3 * fps)
    facts = compile_facts(ctx, l1, cuts, per_frame)
    busy = quiet_meter(ctx, l1, cuts)
    m = {"dur_s": round(dur, 2), "beats": len(ctx.beats), "shots": len(shots), "cuts": len(cuts), "cuts10": round(len(cuts) / max(dur, 1e-3) * 10, 1),
         "new_pictures10": round(new_pictures(ctx, shots) / max(dur, 1e-3) * 10, 1), "median_shot": round(statistics.median(lens), 2) if lens else 0, "fast4f": round(sum(1 for x in lens if x <= 4 / fps + 1e-6) / max(1, len(lens)), 2),
         "longest": round(lens[-1], 2) if lens else 0, "cuts_first3s": first3, "type_events10": round(events / max(dur, 1e-3) * 10, 1),
         "words_on_screen": round(sum(per_frame) / max(1, with_text), 2), "text_frames": round(with_text / max(1, ctx.dur_f), 2),
         "captions": len({c.get("chunk") for c in caps}), "cards": len(cards), "flashes": sum(1 for f in flashes if f.get("mode") != "pool"),
         "sounds": len(l1["sound"]), "sounds10": round(len(l1["sound"]) / max(dur, 1e-3) * 10, 1),
         "word_lock": None if lock is None else round(lock, 2), "word_lock_chance": None if chance is None else round(chance, 2),
         "dead_air_s": round(ctx.dead_air, 2), "ceiling": cap, "ceiling_why": list(low) if low else None,
         "sources": source_mix(ctx, l1), "gestures": gesture_counts(ctx), "layers": layer_facts(ctx, l1), "blanks": blanks, "facts": facts, "busy_seconds": busy, "look": ctx.film_out.get("look"), "by_beat": beats, "rejects": [list(r) for r in ctx.rejects], "findings": list(ctx.findings), "soft": dict(ctx.soft), "band": ctx.band_name,
         "type_traffic": [dict(t) for t in ctx.traffic], "framed_larger": getattr(ctx, "filled", 0)}
    return m


def meter_text(ctx, m):
    b = ctx.band
    out = []
    head = f"FILM {m['dur_s']}s, {m['beats']} beats, {m['shots']} shots, band {m['band'] or 'none'}"
    out.append(head)
    want = f" (band {b.get('cuts10')})" if b.get("cuts10") else ""
    out.append(f"PICTURE cuts/10s {m['cuts10']}{want}, of which to a new picture and not a crop of the take {m['new_pictures10']}, median shot {m['median_shot']}s (band {b.get('med')}), shots of 4 frames or less {m['fast4f']}, longest {m['longest']}s, cuts in first 3s {m['cuts_first3s']}")
    want = f" (band {b.get('type10')})" if b.get("type10") else ""
    wos = f" (band {b.get('wos')})" if b.get("wos") is not None else ""
    out.append(f"TYPE events/10s {m['type_events10']}{want}, words on screen {m['words_on_screen']}{wos}, frames with text {m['text_frames']}, chunks {m['captions']}")
    want = f" (band {b.get('sfx10')} transients)" if b.get("sfx10") and not m.get("taste") else ""
    ly = m["layers"]
    out.append(f"SOUND placed {m['sounds']}, {m['sounds10']}/10s{want}, covering {int(ly['sound_cover'] * 100)}% of the film")
    out.append(f"DESIGN cards {m['cards']}, flashes {m['flashes']}")
    sx = m["sources"]
    bin_note = f" from {sx['found_items']} of {sx['bin_items']} Bin items" if sx["bin_items"] else ""
    out.append(f"SOURCES of {m['dur_s']}s: the take as shot {sx['take']}s, crops of it {sx['crops']}s, person lifted {sx['lifted']}s, found pictures {sx['found']}s{bin_note}, pages {sx['pages']}s")
    gs = m["gestures"]
    joins = sum(gs.get(j, 0) for j in JOINS)
    out.append("GESTURES " + (", ".join(f"{k} {v}" for k, v in sorted(gs.items())) or "none") + f"; designed joins {joins}, the other cuts hard")
    out.append(f"THIN first second shows {ly['first']}; flat pages {ly['flat_s']}s, longest {ly['flat_longest']}s; found pictures held still {ly['found_held_s']}s" + (f"; light of the take {ly['take_luma']}, of the found pictures {ly['found_luma']}" if ly["found_luma"] is not None and ly["take_luma"] is not None else ""))
    if m["word_lock"] is not None:
        out.append(f"CLOCKS cuts within 60 ms of a word start {m['word_lock']} (chance {m['word_lock_chance']}, references .25-.53)")
    if m["dead_air_s"] > 0.3:
        out.append(f"DEAD AIR {m['dead_air_s']}s over 0.3 s pauses. film.gap in seconds closes them")
    line = f"CEILING {m['ceiling']}"
    if m["ceiling_why"]:
        d = m["ceiling_why"]
        line += f": {d[0]} at most {d[1]} ({d[2]})"
    out.append(line)
    fx = m["facts"]
    out.append(f"FACTS class {fx['class']}, opening changed {'yes' if fx['opening_changed'] else 'no'}, new sources {fx['new_sources']}, plates {fx['plates']}, "
               f"overlay {fx['overlay_pct']}%, re-framed {fx['reframed_pct']}%, grade {'yes' if fx['grade_applied'] else 'no'}, sounds {fx['sfx_added']}, design items/10s {fx['design_items10']}")
    if m["busy_seconds"]:
        out.append(f"QUIET {m['busy_seconds']}s with picture, type and camera all busy. One thing stays quiet")
    if m.get("look"):
        from . import grade
        out.append(grade.line(m["look"]))
    out += taste_text(m.get("taste"))
    mv = m.get("move") or {}
    if mv.get("runs"):
        why = "; ".join(f"{v} held: {k}" for k, v in (mv.get("why_held") or {}).items())
        out.append(f"CAMERA the take runs {mv['runs']} time(s); {mv.get('moved', 0)} of those runs are eased for you: {mv.get('moves', 0)} moves on the stressed words "
                   f"({mv.get('moves10', 0)} per 10 s) and a slow push between them" + (f". {why}" if why else ""))
    if m.get("hold_s") is not None:
        out.append(f"HOLD the longest the picture goes without changing: {m['hold_s']} s (the judge's films: 0.5 s at the median, 2 s for one shot in forty)")
    out.append("BEATS id sec pics/10s type design snd")
    for r in m["by_beat"]:
        out.append(f"  {r['id']:<9} {r['sec']:>5} {r['pics10']:>5} {r['type']:>3} {r['design']:>3} {r['snd']:>3}")
    if m["blanks"]:
        out.append("BLANKS, filled or rested with a reason:")
        for k, v in m["blanks"].items():
            out.append(f"  {k}: {' '.join(v)}")
    else:
        out.append("BLANKS none")
    if m["soft"]:
        out.append("SOFT " + ", ".join(f"{k} upscaled {v}x" for k, v in m["soft"].items()))
    bare = [str(r[1]).split(" dropped: ")[0] for r in m["rejects"] if NO_FOR in str(r[1])]
    if bare:
        out.append(f"LINES dropped, {len(bare)} with no `for`: {' '.join(bare[:40])}{' ...' if len(bare) > 40 else ''}. "
                   "A device line names the word, the turn or the picture event it answers, or it stays out")
    for r in m["rejects"]:
        if NO_FOR not in str(r[1]):
            out.append(f"LINE {r[0]} dropped: {r[1]} {r[2]}".rstrip())
    for f in m["findings"]:
        out.append(f"FIND {f}")
    return "\n".join(out) + "\n"


NO_FOR = "names no word, turn or picture event it answers"


def taste_text(t):
    """The governor's lines of the meter: what this footage carries, what stood, and each line that went with its reason."""
    if not t:
        return []
    sup, al, kept = t["supply"], t["allow"], t["kept"]
    names = {"T": "T, one take", "M": "M, a take with some pictures", "P": "P, pictures", "L": "L, a long film"}
    out = [f"SUPPLY class {names[sup['cls']]}: {sup['np10']} new pictures/10s ({sup['np']} in {sup['dur_s']}s), {'a bed' if sup['bed'] else 'no bed'}"
           + (", calm: every allowance halved" if t.get("calm") else "")
           + f". It carries: camera moves {al['camera']} (and a tenth at {t['flips']} of {t['speech_edits']} speech edits), sounds {al['sounds']} at {al['under_voice_db']:g} dB under the voice, "
           f"display lines {al['display']} at {al['seats']} seats, cards {al['cards']}, flashes {al['flashes']}"]
    sy = t.get("system") or {}
    out.append(f"SYSTEM {sy.get('card') or 'no card'}{'' if sy.get('named') else ' (the treat line names no system: the most used devices stand in)'}; "
               f"speaks in {', '.join(t.get('speaks') or []) or 'no device'}; sounds {', '.join(sy.get('sounds') or []) or 'any three roles'}")
    seats = t.get("seats") or {}
    if seats.get("caption") is not None:
        out.append(f"SEATS caption y {seats['caption']:.2f}, never moving; display " + " ".join(f"[{x:.2f},{y:.2f}]" for x, y in seats.get("display") or []))
    k = lambda name: "{} of {}".format(*kept.get(name, (0, 0)))        # noqa: E731
    land = ", ".join(f"{n} on a {what}" for what, n in sorted((t.get("partners") or {}).items())) or "none on a picture event"
    out.append(f"EARNED display {k('display')}, cards {k('card')}, pages {k('page')}, flashes {k('flash')}, camera {k('camera')}, "
               f"sounds {t['sounds'][0]} of {t['sounds'][1]} ({land})")
    rows = t.get("rows") or []
    if rows:
        c = t.get("count") or {}
        out.append("TASTE " + ", ".join(f"{v} {n}" for n, v in sorted(c.items())) + ". A line that went is not put back as it was: "
                   "it comes back only with the word, turn or picture event it answers, inside what this footage carries")
        cams = [r for r in rows if r["dept"] == "camera" and r["did"] == "unearned"]
        shown = [r for r in rows if r not in cams and r["why"] != NO_FOR]
        for r in shown[:28]:
            out.append(f"  {r['id']} {r['did']}: {r['why']}")
        if cams:
            out.append(f"  {len(cams)} crops of the take hold the film's framing: no line earned a step ({' '.join(r['id'] for r in cams[:8])})")
        if len(shown) > 28:
            out.append(f"  and {len(shown) - 28} more")
    if t.get("floor"):
        out.append("FLOOR the plain film: its trims, one framing, captions on the film seat, the grade, and what the customer asked for")
    return out


# ---------------------------------------------------------------- entry points

class Compiled:
    def __init__(self, l1, m, text, ms):
        self.l1, self.meter, self.meter_text, self.ms = l1, m, text, ms

    def json(self):
        return json.dumps(self.l1, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_bin(work_dir):
    """bin.json from S2, tolerant of shape. Paths stay relative to the session folder."""
    out = {}
    if not work_dir:
        return out
    p = os.path.join(work_dir, "bin.json")
    if not os.path.exists(p):
        return out
    try:
        d = json.load(open(p))
    except (OSError, ValueError):
        return out
    rows = d.get("items", []) if isinstance(d, dict) else d
    for i, it in enumerate(rows):
        if not isinstance(it, dict):
            continue
        key = str(it.get("id", i)).replace("bin:", "")
        path = it.get("path") or it.get("file") or it.get("local")
        if not path:
            continue
        rel = os.path.relpath(path, work_dir) if os.path.isabs(path) else path
        if "/" not in rel:
            rel = "bin/" + rel
        kind = it.get("kind") or it.get("type") or ("video" if str(path).lower().endswith((".mp4", ".mov", ".webm", ".mkv")) else "image")
        kind = "video" if "video" in str(kind) else "image"
        w, h = it.get("w") or it.get("width"), it.get("h") or it.get("height")
        out[key] = {"kind": kind, "path": rel, "w": w, "h": h, "dur": it.get("dur") or it.get("duration") or it.get("duration_s"), "fps": it.get("fps"), "seen": it.get("seen")}
        if (not w or not h) and kind == "image":
            try:
                from PIL import Image
                with Image.open(os.path.join(work_dir, rel)) as im:
                    out[key]["w"], out[key]["h"] = im.size
            except Exception:
                pass
    return out


def load_views(work_dir):
    """The upload's own details Supply found (bin.json `own`): another moment of the take, re-framed. id -> row."""
    out = {}
    try:
        with open(os.path.join(work_dir or "", "bin.json")) as f:
            rows = (json.load(f) or {}).get("own") or []
    except (OSError, ValueError, AttributeError):
        return out
    for r in rows:
        m = re.fullmatch(r"main@(\d*\.?\d+)-(\d*\.?\d+)", str(r.get("src") or ""))
        box = r.get("box") or r.get("crop")
        if m and isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            out[str(r.get("id"))] = {"id": str(r.get("id")), "t0": float(m.group(1)), "t1": float(m.group(2)), "box": [float(v) for v in box]}
    return out


VIEW_ZOOM = (1.4, 2.0)         # an insert is this much closer than the film's framing: under it is the take again, over it is mush
WIDE_PRINT = 0.4               # printed text this wide in the film's framing is the upload's own caption or title


def wide_print(ctx, s0, s1, fw):
    """The lines the upload prints across its frame while source seconds s0..s1 play: text at least WIDE_PRINT of the
    film's framing wide (fw, in source fractions). Two readings, joined: perception's regions that stay put, and the
    index's own text boxes sample by sample, kept when another sample of the upload prints a line in the same band
    (a caption changes its words and keeps its place; one stray box on one frame is a pattern, not print).
    Perception alone missed a caption for the first four seconds of p5572, and the film's first shot cut it."""
    out = [list(x["box"]) for x in (ctx.perc or {}).get("text") or []
           if x.get("box") and x["t"][0] - 0.5 <= s1 and x["t"][1] + 0.5 >= s0 and x["box"][2] - x["box"][0] >= WIDE_PRINT * fw]
    rows = ((getattr(ctx, "index", None) or {}).get("spatial") or {}).get("samples") or []
    wide = [(float(r.get("t", -9)), [float(v) for v in b]) for r in rows for b in r.get("text") or [] if len(b) == 4 and b[2] - b[0] >= WIDE_PRINT * fw]
    for t, b in wide:
        if not s0 - 0.6 <= t <= s1 + 0.6:
            continue
        again = sum(1 for t2, b2 in wide if t2 != t and min(b[3], b2[3]) - max(b[1], b2[1]) >= 0.5 * min(b[3] - b[1], b2[3] - b2[1]))
        if again >= 1:
            out.append(b)
    return out


def cuts_wide_print(ctx, rect, src, asset, W, H):
    """Does this window cut through a line the upload prints across its frame while it is on screen?"""
    fit = fit_crop([0, 0, 1, 1], asset["w"], asset["h"], W, H)
    s0, s1 = (src or [0.0, ctx.src_dur])[:2]
    return any(_cuts(rect, m) for m in wide_print(ctx, s0, s1, fit[2] - fit[0]))


def view_rect(ctx, v, asset, W, H):
    """The window a view shows: the frame's shape round the detail's box, with air, no wider than the film's own
    framing over VIEW_ZOOM's least and no tighter than its most, inside the source."""
    fit = fit_crop([0, 0, 1, 1], asset["w"], asset["h"], W, H)
    fw, fh = fit[2] - fit[0], fit[3] - fit[1]
    x0, y0, x1, y1 = v["box"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    k = max((x1 - x0) * 1.3 / fw, (y1 - y0) * 1.3 / fh)         # the share of the film's framing the detail needs
    k = clamp(k, 1 / VIEW_ZOOM[1], 1 / VIEW_ZOOM[0])
    k = max(k, W / (ctx.max_up * fw * asset["w"]))                  # never past what the source can hold
    w, h = fw * k, fh * k
    cx, cy = clamp(cx, w / 2, 1 - w / 2), clamp(cy, h / 2, 1 - h / 2)
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def load_own(work_dir):
    """Pictures S2 made from the upload: cut-outs on the take's timeline, the empty room, a matte."""
    out = {}
    d = os.path.join(work_dir or "", "own")
    if not work_dir or not os.path.isdir(d):
        return out
    try:
        plates = json.load(open(os.path.join(d, "plates.json")))
    except (OSError, ValueError):
        plates = {}
    for f in sorted(os.listdir(d)):
        stem, ext = os.path.splitext(f)
        if stem.startswith("cut_") and ext == ".mp4":
            c = plates.get(stem[4:])
            out[stem] = {"kind": "video", "path": f"own/{f}", "w": 1080, "h": 1920, "dur": None, "fps": 30.0}
            if isinstance(c, list) and len(c) == 2:
                out[stem]["plate"] = _plate(norm_hex(c[0]), norm_hex(c[1]), 0)
        elif stem == "clean" and ext in (".jpg", ".png"):
            w, h = 1080, 1920
            try:
                from PIL import Image
                with Image.open(os.path.join(d, f)) as im:
                    w, h = im.size
            except Exception:
                pass
            out["clean"] = {"kind": "image", "path": f"own/{f}", "w": w, "h": h}
    return out


def load_perception(work_dir):
    """perception.json from S1, when it is there. Everything it adds is optional."""
    if not work_dir:
        return {}
    p = os.path.join(work_dir, "perception.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}


def faces_from(perc):
    out = []
    for s in perc.get("samples") or []:
        if s.get("face"):
            out.append({"t": float(s["t"]), "boxes": [s["face"]]})
    return out or None


def load_taste(work_dir):
    """What the governor reads beside the Score: the Scout's ask ledger, the measured stress of each word, and the
    style cards the Brief offered. All optional: a film with none of them is governed on its plan alone."""
    asks, stress, offered = {}, {}, None
    if not work_dir:
        return asks, stress, offered
    try:
        for a in (json.load(open(os.path.join(work_dir, "scout.json"))).get("asks") or []):
            if isinstance(a, dict) and a.get("id"):
                asks[str(a["id"])] = a
    except (OSError, ValueError, AttributeError):
        pass
    try:
        stress = {int(k): float(v) for k, v in json.load(open(os.path.join(work_dir, "stress.json"))).items()}
    except (OSError, ValueError, AttributeError, TypeError):
        stress = {}
    try:
        offered = [str(x) for x in json.load(open(os.path.join(work_dir, "brief", "manifest.json"))).get("styles") or []] or None
    except (OSError, ValueError, AttributeError):
        offered = None
    return asks, stress, offered


def empty_pages(ctx):
    """A flat page with nothing on it says nothing. When the type or the card written for it is gone (the last look
    named it, the count thinned it, it had no place), the page goes with it and the take plays through: the last look
    took 'every morning' off a closing page and the film ended on a second of black (p5575, 13 Nov)."""
    spans = [(it["f"][0], it["f"][1]) for it in ctx.items if it.get("k") in ("word", "card")]
    spans += [(r[5], r[6]) for r in (ctx.waiting or [])]
    out = []
    for s in ctx.shots:
        a, z = s["f"]
        if s.get("pic") != "plate" or s.get("framed") or any(p < z and a < q for p, q in spans):
            out.append(s)
            continue
        parts = [p for p in follow_spine(ctx, a, z) if p[3] > p[2]]
        if not parts or parts[0][0] != a or parts[-1][1] != z:
            out.append(s)                   # a silent beat has no take to show: the page stays
            continue
        b = next((bt for bt in ctx.beats if bt.pf0 <= a < bt.pf1), ctx.beats[0] if ctx.beats else None)
        out += emit_ref(ctx, b, {"kind": "main", "asset": "main", "sync": True}, a, z, s["id"])
        ctx.taste_rows.append({"id": s["id"], "did": "repaired", "dept": "page", "why": "a page with nothing on it: the take plays through"})
    ctx.shots = sorted(out, key=lambda i: i["f"][0])


def compile_once(text, index, work_dir, bin_items, tray, faces, sid, owner, seed, perc, governed, plan, extra):
    """One pass of the compiler. `plan` holds what the reading of the pass before decided (taste.decide)."""
    ctx = Ctx(index, bin_items=bin_items, tray=tray, faces=faces, sid=sid, owner=owner, seed=seed)
    ctx.perc = perc
    ctx.work = work_dir
    ctx.governed, ctx.plan = governed, plan
    ctx.asks, ctx.stress, ctx.offered = extra
    ctx.own = load_own(work_dir)
    ctx.views = load_views(work_dir)
    for v in ctx.own.values():
        if v["kind"] == "video":
            v["dur"] = ctx.src_dur
    if work_dir and os.path.exists(os.path.join(work_dir, "matte.mp4")):
        ctx.matte = "matte.mp4"
    if perc.get("samples"):
        ctx.lumas = [(float(s["t"]), float(s["luma"]) * 255) for s in perc["samples"] if s.get("luma") is not None] or ctx.lumas
    good, bad = parse_score(text)
    ctx.rejects = list(bad)
    read_film(ctx, good)
    read_beats(ctx, good)
    build_spine(ctx)
    asset_for(ctx, "main")
    plan_shots(ctx)
    if governed:
        from . import move, pace
        pace.soft_in(ctx)
        ctx.punch_rep = move.punch_takes(ctx)          # the wordless stretches cut inside themselves get their framings, or are one shot again
    ctx.planned = {x["id"]: list(x["f"]) for x in ctx.shots}
    ctx.waiting = []
    run_gestures(ctx)
    run_raw(ctx, good)
    if governed:
        empty_pages(ctx)
    gone = plan.get("drop_item") or {}
    if governed and gone:                  # one flash of a row the reading thinned
        for it in ctx.items:
            if it["id"] in gone:
                ctx.taste_rows.append({"id": it["id"], "did": gone[it["id"]][0], "why": gone[it["id"]][1], "dept": "flash"})
        ctx.items = [i for i in ctx.items if i["id"] not in gone]
        for i in gone:
            ctx.byid.pop(i, None)
    seal(ctx)
    flash_gate(ctx)            # it can take a negative off a shot, and a word's ink reads that shot
    place_waiting_words(ctx)
    build_captions(ctx)
    yield_to_display(ctx)
    build_sound(ctx)
    mono_cap(ctx)
    seal(ctx)
    if governed:
        safe_sweep(ctx)
    type_traffic(ctx)
    keepout_sweep(ctx)
    flash_gate(ctx)
    return ctx


def compile_score(text, index=None, work_dir=None, bin_items=None, tray=None, faces=None, sid=None, owner=False, seed=None, perception=None, govern=None):
    """Score text to L1. Never raises on a Score: a bad line drops only itself. Governed (taste.py): the film is
    compiled, read against what its footage can carry, and compiled again without what nothing earned, until the
    reading has nothing left to say. Same inputs give the same film."""
    t0_ = time.perf_counter()
    if index is None:
        index = json.load(open(os.path.join(work_dir, "index.json")))
    if bin_items is None:
        bin_items = load_bin(work_dir)
    perc = perception if perception is not None else load_perception(work_dir)
    if faces is None:
        faces = faces_from(perc)
    sid = sid or (os.path.basename(os.path.normpath(work_dir)) if work_dir else None)
    governed = GOVERN if govern is None else bool(govern)
    extra = load_taste(work_dir) if governed else ({}, {}, None)

    def settle(text, plan):
        facts, rounds = None, 0
        while True:
            ctx = compile_once(text, index, work_dir, bin_items, tray, faces, sid, owner, seed, perc, governed, plan, extra)
            rounds += 1
            if not governed:
                break
            from . import taste
            dec, ledger, facts = taste.decide(ctx)
            cls = facts["supply"]["cls"]
            if (taste.empty(dec) and plan.get("cls", "T") == cls) or rounds > GOVERN_ROUNDS:
                ctx.unsettled = ledger if not taste.empty(dec) else []
                voice_levels(ctx, facts["allow"]["under_voice_db"])
                break
            plan = taste.merge(dict(plan), dec)
            plan["cls"] = cls
        return ctx, plan, facts, rounds

    plan = {}
    seen = score_the_look_saw(text) if governed else None
    if seen is not None:
        # The last look judged one film. Taking a device out of it must not let in another that film had no room
        # for and the look never saw (p5575: with 'ready' removed, a counter the reading had thinned came back and
        # sat on her arm for four seconds). What was decided against in the film it saw stays decided.
        plan = settle(seen, {})[1]
    ctx, plan, facts, rounds = settle(text, plan)
    hold_s = None
    if governed:
        hook_finding(ctx)
        hold_s = hold_finding(ctx)
    l1 = to_l1(ctx)
    from . import sound                    # lane J: the film's sound design, peak on each chosen picture event
    turn_s = None
    if governed:
        try:                               # the turn the Score named for its bed is landed like the payoff (sound engine, 13 Nov)
            from . import bed as bedlib
            tf = bedlib.turn_frame(l1, index, ctx.film)
            turn_s = tf / ctx.fps if tf is not None else None
        except Exception:  # noqa: BLE001 - a film is never lost to its music line
            turn_s = None
    l1 = sound.apply(l1, work_dir, index, perc, ctx.asks or None, (facts or {}).get("supply"), turn_s=turn_s)
    m = meter(ctx, l1)
    if governed:
        from . import pace
        m["pace"] = pace.report(ctx)
        m["taste"] = taste_meter(ctx, facts, rounds)
        m["findings"] = list(ctx.findings)
        m["hold_s"] = hold_s
        # The take is never still (score.move, 13 Nov): each run of the upload becomes one eased camera track. Done
        # after the meter, which counts cuts and framings as the Score wrote them, and kept only when the L1 holds.
        try:
            from . import move
            rep = move.ease_takes(ctx)
            pr = getattr(ctx, "punch_rep", None) or {}
            if pr.get("punched") or pr.get("fault") or pr.get("why_held"):
                rep["punched"], rep["punch_held"] = pr.get("punched") or 0, pr.get("why_held") or pr.get("fault")
            if rep.get("moved") or rep.get("stills"):
                l1e = to_l1(ctx)
                l1e["sound"] = l1["sound"]
                for k in ("sound_design",):
                    if k in l1:
                        l1e[k] = l1[k]
                if not validate_l1(l1e):
                    l1 = l1e
                else:
                    rep["fault"] = "the eased film did not validate: the framings stay as written"
            m["move"] = rep
        except Exception as e:  # noqa: BLE001 - the film stands as the Score framed it
            m["move"] = {"fault": f"{type(e).__name__}: {str(e)[:160]}"}
    txt = meter_text(ctx, m)
    return Compiled(l1, m, txt, (time.perf_counter() - t0_) * 1000)


def hook_finding(ctx):
    """A talking film shows its first picture that is not the take inside its first 3 s, when it has one to show
    (a Bin picture or a detail of the upload). Measured on the plan; the repair round reads it."""
    from . import taste
    if len(ctx.words) / max(ctx.src_dur, 1e-3) < 1.0 or not (ctx.bin or ctx.views):
        return
    firsts = [s["f"][0] for s in ctx.shots if (taste._pic_key(ctx, s) or ("take",))[0] != "take"]
    firsts += [c["f"][0] for c in ctx.items if c.get("k") == "card" and c.get("pic") not in (None, "plate", "main")]
    first = min(firsts) / ctx.fps if firsts else None
    if first is None or first > taste.HOOK_S:
        ctx.find("hook: " + ("the film never leaves the take" if first is None else f"the first picture that is not the take lands at {first:.1f} s")
                 + ". A talking film shows one inside its first 3 s: open on the Bin's hook picture with the title, or cut the first picture in earlier")


HOLD_FIND_S = 5.0          # one picture on screen this long is told to the repair round (the judge: one shot in forty passes 2 s)


def hold_finding(ctx):
    """The longest stretch in which the picture never changes: no cut to another source, no cut of the upload's own
    (taste.picture_changes; a crop of the take is not a change). Past HOLD_FIND_S on a film of 8 s or more it is a
    finding, with where it is, so the repair round cuts into it. Returns the seconds, for the meter."""
    from . import taste
    fps = ctx.fps
    marks = [0] + list(taste.picture_changes(ctx)) + [ctx.dur_f]
    gap, at = max(((b - a, a) for a, b in zip(marks, marks[1:])), default=(0, 0))
    hold = gap / fps
    if hold > HOLD_FIND_S and ctx.dur_f / fps >= 8.0:
        ctx.find(f"hold: one picture stays on screen {hold:.1f} s, from {at / fps:.1f} s. A framing held that long reads as nothing happening: cut inside it to a Bin "
                 "picture, a view, another moment of the upload (`main@12.4-13.6`), a page or a counter, every 1 to 3 s")
    return round(hold, 2)


def take_seats(work_dir, index=None):
    """Where type can stand on the upload held as one take, by the same rules a film is compiled with: (caption y,
    display seats, the room each has as [width, height], the open ground shot by shot). For the Brief, before a film exists, so the Director
    knows whether its hook title has a place on the take or must stand over a picture. None when nothing can be told."""
    if index is None:
        index = json.load(open(os.path.join(work_dir, "index.json")))
    dur = float((index.get("video") or {}).get("duration") or 0.0)
    if dur <= 0:
        return None
    stub = ('{"k":"film","v":1,"fps":30,"frame":"9:16","spine":"main"}\n'
            '{"k":"b","id":"b01","at":"@0.0..@%.2f","pic":"main"}\n' % max(0.5, dur - 0.05))
    perc = load_perception(work_dir)
    ctx = compile_once(stub, index, work_dir, {}, None, faces_from(perc), None, False, None, perc, True, {}, ({}, {}, None))
    seats = film_seats(ctx)
    return ctx.cap_y, [list(q) for q in seats], [list(r) for r in (getattr(ctx, "seat_rooms", None) or [])], shot_grounds(ctx)


def shot_grounds(ctx, most=10):
    """Shot by shot, the open ground a display line has while that shot is on screen: [(shot id, [t0, t1], [(seat,
    room height), ..])], the upper place and the lower one where the shot has both. A line over the take is seated
    for the seconds it stands (span_seated), so on a clip of several shots this, not the film-wide seat, is where a
    title goes. Empty for one take: there the film's seat is the answer."""
    from . import taste
    rows = [r for r in ctx.perc.get("shots") or [] if r.get("t") and float(r["t"][1]) - float(r["t"][0]) >= 0.8]
    if len(rows) < 2:
        return []
    out = []
    for r in rows[:most]:
        a = int(float(r["t"][0]) * ctx.fps) + 2
        z = min(int(float(r["t"][1]) * ctx.fps) - 2, a + 2 * ctx.fps, ctx.dur_f)        # a line lands as the shot does and stands about 2 s
        if z - a < 12:
            continue
        places = []
        for want in ([0.5, 0.26], [0.5, 0.7]):
            got = taste.span_seat(ctx, a, z, want, (0.78, 0.12))
            if got and got[2] >= 0.6 and all(abs(got[0][1] - q[0][1]) >= 0.15 for q in places):
                places.append((got[0], got[1][1]))
        out.append((r.get("id"), [round(float(r["t"][0]), 1), round(float(r["t"][1]), 1)], places))
    return out


def score_the_look_saw(text):
    """The Score without the last look's cut lines, or None when it has none or asks for the floor (the floor keeps
    only what was asked for, so nothing can come in). Reads the Score's own syntax, line by line."""
    kept, cuts = [], 0
    for line in text.splitlines():
        s = line.strip()
        row = None
        if s.startswith("{") and '"cut"' in s:
            try:
                row = json.loads(s)
            except ValueError:
                row = None
        if isinstance(row, dict) and row.get("k") == "cut":
            if str(row.get("id")) == "*":
                return None
            cuts += 1
            continue
        kept.append(line)
    return "\n".join(kept) + "\n" if cuts else None


def taste_meter(ctx, facts, rounds):
    """What the governor did, for the meter and the job record."""
    rows, seen = [], set()
    for r in ctx.taste_rows:
        key = (r["id"], r["did"])
        if key not in seen:
            seen.add(key)
            rows.append(dict(r))
    for r in getattr(ctx, "unsettled", None) or []:
        ctx.find(f"taste: {r['id']} still over what this footage carries after {rounds} readings: {r['why']}")
    count = {}
    for r in rows:
        count[r["did"]] = count.get(r["did"], 0) + 1
    kept = {k: [v[0], max(v[1], ctx.wrote.get(k, 0))] for k, v in facts["kept"].items()}
    return {"supply": facts["supply"], "allow": facts["allow"], "kept": kept, "sounds": [facts["sounds"][0], max(facts["sounds"][1], ctx.wrote.get("sound", 0))], "partners": facts["partners"],
            "speaks": facts["speaks"], "speech_edits": facts["speech_edits"],
            "flips": sum(1 for i in range(1, len(ctx.flip_frames or []) + 1) if bool(ctx.flip_runs.get(i - 1)) != bool(ctx.flip_runs.get(i))),
            "seats": {"caption": ctx.cap_y, "display": ctx.seats or []}, "system": dict(ctx.system), "calm": ctx.calm,
            "rows": rows, "count": count, "rounds": rounds, "devices": sorted(ctx.devices), "bound": facts["bound"],
            "silent": [b.id for b in ctx.beats if b.silent],
            "floor": "*" in ctx.cuts, "cuts": len(ctx.cuts), "upload_captions": getattr(ctx, "upload_caps", None)}


def prebuild_thumbs(work_dir):
    """Supply's last step: every picture read small once, so a compile stays in milliseconds."""
    class Assets:
        work, thumb_cache, assets = work_dir, {}, {"main": {"kind": "video", "path": "source.mp4"}}
    for k, v in load_bin(work_dir).items():
        Assets.assets[f"bin:{k}"] = v
    for k, v in load_own(work_dir).items():
        Assets.assets[f"own:{k}"] = v
    for aid in list(Assets.assets):
        thumbs(Assets, aid)


# What today's renderer draws. Measured by compiling each gesture and lowering it (director/first-result):
# a join that moves pixels between cuts (whip, slide, shred, shatter), a freeze or stutter under the
# 0.2 s overlay minimum, a recolour, and the gestures that ride a body track lower to nothing here, so
# they are not offered. They compile still, so a Score written for the engine stays valid.
TODAY_GESTURES = ("hold", "cuts", "salvo", "page", "big", "line", "card", "band", "neg_card", "flash", "burn", "tiles", "punch",
                  "counter", "pile", "split", "glitch", "boxes", "slat", "shake", "chip", "echo", "type_on", "wall", "ticker")


TODAY_HIDDEN = {"big": (" reflect", " fill"), "line": (" reflect",), "card": (" behind",)}     # fields today's renderer drops


def menu_card(k, card):
    head, dot, rest = card.partition(". ")
    for f in TODAY_HIDDEN.get(k, ()):
        head = head.replace(f, "")
    return head + dot + rest


def gesture_menu():
    """One line per gesture this release can draw, for the prefix: its fields, then what it does."""
    return "\n".join(f"{k} {menu_card(k, v['card'])}" for k, v in GESTURES.items() if k in TODAY_GESTURES)
