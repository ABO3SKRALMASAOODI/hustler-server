"""The story films, as scene plans (L1) for Valmera's engine (worker/scene).

A style is a function (script, supply, direction) -> (l1, beats). Everything is baked here: every picture's crop
and push, every word's place on every frame, every stat's value. The engine reads numbers and draws. Type is
measured with the engine's own shaper, so a line sits where this file says it sits.

The clock is the narration: words.json from the narrate tool gives every spoken word its start and end, and every
picture, stat, mark and word on screen lands on the word that names it.

Styles (atlas/ALIEN.md 8.3):
  character-sheet  G4 the founder's run as a stat block that ticks on the word that causes it, red and blinking at
                   the low point, the last stat overflowing its box; T4 a sentence's full stop swells into the cut
  whole-page       N6 the script set as one page with photographs as windows; the camera rides the spoken line and
                   pulls back to the whole page as a poster; C1 each word's size and lift measured from the voice
  one-photograph   T3 one archive photograph assembled by the voice, a face per beat; the camera travels inside it,
                   each person told keeps a mark; X2 once, a face whose tones are the spoken sentence in tiny type
Taste (week-1112/TASTE.md): one caption seat, type inside the safe area and off the mark, nothing under the reading
floor, a sound only where something lands, pushes on stills at most 6%, no upscale past 3x.
"""
import json
import math
import os
import subprocess
import sys

FPS, W, H = 30, 1080, 1920
SAFE = (0.05, 0.10, 0.95, 0.80)          # x0, y0, x1, y1 (TASTE P2)
RAIL_X = 0.86                            # right of this between y .55 and .80 sit the platform's buttons
MARK = (0.5594, 0.115, 0.9146, 0.1923)   # the watermark's keep-out (renderer.watermark_geometry at 1080x1920)
LEAD_F = 3                               # the voice starts on frame 3: frame 0 is already the hook's picture
MAX_UP = 3.0                             # TASTE 4.2: total upscale never past 3x


# ----------------------------------------------------------------- engine access

def scene_worker():
    if os.environ.get("VALMERA_SCENE_WORKER"):
        return os.environ["VALMERA_SCENE_WORKER"]
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.exists(os.path.join(d, "worker", "scene", "run.py")):
            return os.path.join(d, "worker")
    return "/Users/masaoodi/Documents/Valmera/worktrees/skill-main/worker"


def _type():
    w = scene_worker()
    if w not in sys.path:
        sys.path.insert(0, w)
    from scene import type as T
    return T


class Ruler:
    """Advance widths and cap heights from the engine's shaper."""

    def __init__(self, voices):
        self.T = _type()
        self.voices = voices

    def width(self, v, text):
        if not text:
            return 0.0
        vo = self.voices[v]
        return self.T.Face.of(vo).measure(text, float(vo["px"]), float(vo.get("track", 0)))

    def cap(self, v):
        vo = self.voices[v]
        return self.T.Face.of(vo).metrics(float(vo["px"]))[2]

    def fit_px(self, v, text, max_px_wide, px_max):
        vo = dict(self.voices[v])
        vo["px"] = 100.0
        w100 = self.T.Face.of(vo).measure(text, 100.0, float(vo.get("track", 0)))
        return float(min(px_max, math.floor(100.0 * max_px_wide / max(w100, 1e-6))))


# ----------------------------------------------------------------- curves

def expo_out(n):
    if n <= 1:
        return [1.0]
    k = 1.0 - 2.0 ** -10.0
    return [(1.0 - 2.0 ** (-10.0 * i / (n - 1))) / k for i in range(n)]


def ease_io(n):
    if n <= 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(math.pi * i / (n - 1)) for i in range(n)]


def smooth(t):
    t = min(max(t, 0.0), 1.0)
    return t * t * (3 - 2 * t)


def r4(x):
    return round(float(x), 5)


def r4l(v):
    return [r4(q) for q in v] if isinstance(v, list) else r4(v)


# ----------------------------------------------------------------- the clock: the narration

def norm_word(s):
    return "".join(c for c in str(s).lower() if c.isalnum())


class Clock:
    """Spoken words in programme frames. The voice starts at LEAD_F."""

    def __init__(self, words_json, script):
        d = json.load(open(words_json)) if isinstance(words_json, str) else words_json
        self.words = [dict(w=w["w"], t0=float(w["start"]) + LEAD_F / FPS, t1=float(w["end"]) + LEAD_F / FPS, seg=int(w.get("seg", 0)))
                      for w in d["words"]]
        self.dur = float(d["duration"]) + LEAD_F / FPS
        self._spread()
        self.nseg = len(script["segments"])
        self.segs = []
        for i in range(self.nseg):
            ws = [w for w in self.words if w["seg"] == i]
            if ws:
                self.segs.append((ws[0]["t0"], ws[-1]["t1"]))
            else:
                prev = self.segs[-1][1] if self.segs else LEAD_F / FPS
                self.segs.append((prev, prev + 0.4))

    def _spread(self):
        """Words the aligner gave one span between them (a figure heard as digits: "one hundred and eighty-six" all at
        15.06-16.04) share that span by their length, in order, so each word has its own moment."""
        ws, i = self.words, 0
        while i < len(ws):
            j, hi = i, max(ws[i]["t0"], ws[i]["t1"])
            while j + 1 < len(ws) and ws[j + 1]["seg"] == ws[i]["seg"] and (ws[j + 1]["t0"] < hi - 0.02 or ws[j + 1]["t1"] <= ws[j + 1]["t0"]):
                j += 1
                hi = max(hi, ws[j]["t0"], ws[j]["t1"])
            if j > i:
                a = min(w["t0"] for w in ws[i:j + 1])
                b = max(max(w["t0"], w["t1"]) for w in ws[i:j + 1])
                if j + 1 < len(ws) and ws[j + 1]["seg"] == ws[i]["seg"]:
                    b = min(b, ws[j + 1]["t0"])
                b = max(b, a + 0.08 * (j - i + 1))
                units = [len(norm_word(w["w"])) + 1 for w in ws[i:j + 1]]
                t = a
                for w, u in zip(ws[i:j + 1], units):
                    w["t0"], w["t1"] = t, t + (b - a) * u / sum(units)
                    t = w["t1"]
            i = j + 1

    @staticmethod
    def f(t):
        return int(round(t * FPS))

    def seg_words(self, i):
        return [w for w in self.words if w["seg"] == i]

    def word_t(self, seg, word, default=None):
        """Start of the first word of segment `seg` that is `word` (letters and digits compared)."""
        k = norm_word(word)
        for w in self.seg_words(seg):
            if k and norm_word(w["w"]) == k:
                return w["t0"]
        for w in self.seg_words(seg):
            if k and k in norm_word(w["w"]):
                return w["t0"]
        return self.segs[seg][0] if default is None else default

    def phrases(self, max_chars=18, max_words=3, gap=0.28):
        """Caption chunks: a few words, broken at pauses and segment ends. [(t0, t1, text, [words])]"""
        out, cur = [], []
        for i, w in enumerate(self.words):
            nxt = self.words[i + 1] if i + 1 < len(self.words) else None
            cur.append(w)
            text = " ".join(x["w"] for x in cur)
            brk = (nxt is None or nxt["seg"] != w["seg"] or nxt["t0"] - w["t1"] >= gap or len(cur) >= max_words
                   or len(text + " " + (nxt["w"] if nxt else "")) > max_chars or w["w"].endswith((",", ".", "?", "!", ";", ":")))
            if brk:
                out.append([cur[0]["t0"], cur[-1]["t1"], text, cur])
                cur = []
        for i in range(len(out)):          # a chunk stays up until the next one comes, unless a real pause opens
            nxt = out[i + 1][0] if i + 1 < len(out) else out[i][1] + 0.5
            out[i][1] = min(nxt, out[i][1] + 0.45) if nxt - out[i][1] > 0.6 else nxt
        return [tuple(x) for x in out]


# ----------------------------------------------------------------- the plan

class Plan:
    def __init__(self, voices, lib):
        self.voices, self.lib = voices, lib
        self.items, self.sounds, self.assets = [], [], {}
        self.events, self.shown, self.pictures, self.cuts = [], [], [], []
        self.R = Ruler(voices)
        self.n = 0

    def nid(self, p):
        self.n += 1
        return f"{p}{self.n:04d}"

    def image(self, key, rec):
        self.assets[key] = dict(kind="image", path=rec["path"], w=int(rec["w"]), h=int(rec["h"]))
        return key

    def video(self, key, rec):
        self.assets[key] = dict(kind="video", path=rec["path"], w=int(rec.get("w") or W), h=int(rec.get("h") or H), fps=float(rec.get("fps") or FPS),
                                dur=float(rec.get("seconds") or 0))
        return key

    def page(self, f, c0, c1=None, grain=0.03, **kw):
        self.items.append(dict(id=self.nid("pg"), k="shot", f=[int(f[0]), int(f[1])], pic="plate", plate=dict(c0=c0, c1=c1 or c0, grain=grain), **kw))

    def shot(self, f, pic, crop, **kw):
        it = dict(id=self.nid("sh"), k="shot", f=[int(f[0]), int(f[1])], pic=pic, crop=crop)
        it.update(kw)
        self.items.append(it)
        return it

    def card(self, f, pic, box, z=1, **kw):
        x0, y0, x1, y1 = box
        it = dict(id=self.nid("cd"), k="card", f=[int(f[0]), int(f[1])], z=z, pic=pic, s=1, rot=0, op=1,
                  x=r4((x0 + x1) / 2), y=r4((y0 + y1) / 2), w=r4(x1 - x0), h=r4(y1 - y0))
        it.update(kw)
        self.items.append(it)
        return it

    def block(self, f, box, c, z=1, op=1.0, **kw):
        return self.card(f, "plate", box, z=z, plate=dict(c0=c, c1=c, grain=0.0), op=op, **kw)

    def word(self, f, t, v, x, y, ink="#FFFFFF", op=1.0, align="l", note=True, role="big", **kw):
        if note:
            self.shown.append(t)
        if align != "l" and not isinstance(x, list):
            wd = self.R.width(v, t) / W
            x = x - wd if align == "r" else x - wd / 2
        it = dict(id=self.nid("w"), k="word", f=[int(f[0]), int(f[1])], t=t, v=v, x=r4l(x), y=r4l(y), ink=ink, op=r4l(op),
                  role=role, on=int(f[0]))
        it.update(kw)
        self.items.append(it)
        return it

    def flash(self, f, mode="fill", c="#FFFFFF", a=1.0, rect=None, **kw):
        it = dict(id=self.nid("fl"), k="flash", f=[int(f[0]), int(f[1])], mode=mode, c=c, a=r4l(a), **kw)
        if rect:
            it["rect"] = [r4(v) for v in rect]
        self.items.append(it)
        return it

    def hit(self, name, f, db):
        """A library sound with its peak on frame f; f is an event of the film (something lands there)."""
        if name not in self.lib:
            return
        m = self.lib[name]
        start = f / FPS - m["peak_s"]
        trim = 0.0
        if start < 0:
            trim, start = -start, 0.0
        self.sounds.append(dict(id=self.nid("s"), s=name, file=m["file"], event_f=int(f), start_s=r4(start), trim_s=r4(trim),
                                db=round(db - m["max_db"], 2), peak_s=m["peak_s"]))
        self.events.append(int(f))

    def done(self, sid, dur, seed, film):
        order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
        self.items.sort(key=lambda it: (it["f"][0], order_k[it["k"]], it.get("z", 0), it["id"]))
        return dict(l1=1, sid=sid, seed=seed, fps=FPS, frame=[W, H], dur_f=int(dur), assets=self.assets, spine=[], voices=self.voices,
                    film=film, items=self.items, sound=self.sounds)


# ----------------------------------------------------------------- framing a still

def cover_crop(pw, ph, aspect, focus=(0.5, 0.42), at=(0.5, 0.42)):
    """The largest crop of a picture with this aspect (w/h of the frame or card) that puts `focus` (picture
    fractions) as near `at` (frame fractions) as the picture allows."""
    pa = pw / ph
    if pa > aspect:
        cw, ch = aspect / pa, 1.0
    else:
        cw, ch = 1.0, pa / aspect
    x0 = min(max(focus[0] - at[0] * cw, 0.0), 1.0 - cw)
    y0 = min(max(focus[1] - at[1] * ch, 0.0), 1.0 - ch)
    return [x0, y0, x0 + cw, y0 + ch]


def push(crop, n, amount, anchor):
    """A slow push on a still: the crop shrinks by `amount` about `anchor` over n frames (TASTE K: at most 6%)."""
    x0, y0, x1, y1 = crop
    out = []
    for i in range(n):
        k = 1.0 - amount * smooth(i / max(n - 1, 1)) * 0.5 - amount * 0.5 * i / max(n - 1, 1)
        cw, ch = (x1 - x0) * k, (y1 - y0) * k
        ax, ay = anchor
        nx0 = x0 + (ax - x0) * (1 - k)
        ny0 = y0 + (ay - y0) * (1 - k)
        nx0, ny0 = min(max(nx0, 0.0), 1 - cw), min(max(ny0, 0.0), 1 - ch)
        out.append([r4(nx0), r4(ny0), r4(nx0 + cw), r4(ny0 + ch)])
    return out


def focus_of(rec):
    """Where the picture's people are: the centre of its faces (eyes height), else the upper middle."""
    fs = rec.get("faces") or []
    if not fs:
        return (0.5, 0.45), 0.0
    big = max(fs, key=lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1]))
    keep = [f for f in fs if (f["box"][2] - f["box"][0]) >= 0.45 * (big["box"][2] - big["box"][0])]
    x0 = min(f["box"][0] for f in keep)
    x1 = max(f["box"][2] for f in keep)
    ey = sum((f["eyes"][0][1] + f["eyes"][1][1]) / 2 for f in keep) / len(keep)
    return ((x0 + x1) / 2, ey), (big["box"][3] - big["box"][1])


def bleed_ok(rec):
    """Can this photograph fill the frame (no upscale past 3x, its people not cut)?"""
    (fx, fy), _ = focus_of(rec)
    crop = cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.40))
    up = W / max((crop[2] - crop[0]) * rec["w"], 1)
    cut = False
    if rec.get("faces"):
        xs = [x for fc in rec["faces"] for x in (fc["box"][0], fc["box"][2])]
        cut = (max(xs) - min(xs)) > (crop[2] - crop[0]) * 1.02 and len(rec["faces"]) <= 4
    return up <= MAX_UP and not cut


def still(P, f, key, rec, grade=None, amount=0.045, z=0):
    """One photograph on frames f. Full bleed when it can fill the frame without passing 3x and without cutting its
    people; else whole on a blurred, darkened bed of itself. Returns the framing used."""
    a, b = f
    n = b - a
    pw, ph = rec["w"], rec["h"]
    (fx, fy), face_h = focus_of(rec)
    crop = cover_crop(pw, ph, W / H, (fx, fy), (0.5, 0.40))
    up = W / max((crop[2] - crop[0]) * pw, 1)
    gk = dict(grade=grade) if grade else {}
    if bleed_ok(rec):
        P.shot(f, key, push(crop, n, amount, (fx, fy)), **gk)
        return dict(mode="bleed", upscale=round(up, 2))
    # the bed: the same picture, cover, blurred and dark; the picture whole on it
    P.shot(f, key, cover_crop(pw, ph, W / H), blur=26, grade=dict(lift=0, gamma=1.0, gain=0.42, sat=0.7, contrast=1, temp=0, tint=0))
    cw = 0.90
    chh = cw * W * ph / pw / H
    if chh > 0.62:
        chh = 0.62
        cw = chh * H * pw / ph / W
    box = [0.5 - cw / 2, 0.42 - chh / 2, 0.5 + cw / 2, 0.42 + chh / 2]
    s = [r4(1.0 + 0.03 * smooth(i / max(n - 1, 1))) for i in range(n)]
    P.card(f, key, box, z=z, s=s, radius=0.006, shadow=dict(a=0.45, blur=0.03, dx=0, dy=0.012), **gk)
    return dict(mode="bed", upscale=round(cw * W / pw, 2), box=box)


def caption_track(P, clock, v, y, ink="#FFFFFF", f_end=None, skip=(), z=0, ink_for=None, yield_to=()):
    """Captions at one seat for the whole film (TASTE P1), centred, a few words at a time. ink_for(a, b, box) may
    return (voice, ink) for a chunk from what is measured under it (TASTE P4: flip the ink before anything else)."""
    ph = clock.phrases()
    for i, (t0, t1, text, ws) in enumerate(ph):
        a, b = Clock.f(t0) - 1, Clock.f(t1)
        if i + 1 < len(ph):
            b = min(b, Clock.f(ph[i + 1][0]) - 1)       # one chunk at a time, never two on the seat
        if f_end is not None:
            b = min(b, f_end)
        if b - a < 3 or any(s0 <= a < s1 for s0, s1 in skip):
            continue
        words_here = {norm_word(x) for x in text.split() if len(norm_word(x)) >= 3}
        if any(s0 < b and a < s1 and words_here & {norm_word(x) for x in t_.split()} for s0, s1, t_ in yield_to):
            continue                                    # TASTE P7: the display says it; the caption yields
        v_, ink_ = v, ink
        if ink_for:
            wd = P.R.width(v, text) / W
            v_, ink_ = ink_for(max(a, 0), b, [0.5 - wd / 2 - 0.02, y - P.R.cap(v) / H - 0.012, 0.5 + wd / 2 + 0.02, y + 0.012])
        P.word([max(a, 0), b], text, v_, 0.5, y, ink_, 1.0, align="c", note=False, z=z)


def designed_page(P, f, text, sub, palette, v_big, v_sub):
    """A page of type: a figure or a date on its own, where no photograph shows it (a designed picture)."""
    a, b = f
    P.page(f, palette["page0"], palette["page1"], grain=0.05)
    v_own = f"{v_big}{a}"                   # each page's figure is sized for itself: a voice of its own
    P.voices[v_own] = dict(P.voices[v_big])
    P.voices[v_own]["px"] = P.R.fit_px(v_own, text, 0.80 * W, 300)
    v_big = v_own
    n = b - a
    ys = [0.47 + 0.012 * (1 - q) for q in expo_out(min(7, n))] + [0.47] * max(n - 7, 0)
    ops = [min(1.0, (i + 1) / 3) for i in range(n)]
    P.word(f, text, v_big, 0.5, ys[:n], palette["ink"], ops[:n], align="c")
    if sub:
        P.word([a + 3, b], sub, v_sub, 0.5, 0.47 + 0.05, palette["ink_soft"], 0.85, align="c")


# Picture density follows the pace the writer gave each segment (or the script): the shortest and longest framing in
# seconds. A slow, reflective stretch holds a picture longer; a list or a money passage changes faster (atlas 1.2: the
# fastest 10 s of a story sits on a list or on money). Shared with workflow 4 (valmera-longform), whose chapters set it.
PACE_S = {"slow": (1.6, 3.4), "steady": (1.15, 2.6), "brisk": (0.85, 1.9), "fast": (0.65, 1.5)}


def assign_pictures(script, clock, pics, f_end, min_s=1.15, max_s=2.6):
    """Shots in programme frames from the segments' named pictures: [(f0, f1, name, rec)]. A subject's photographs
    rotate; a segment that names nothing keeps the last subject with its next photograph. Each segment's framings
    last between its pace's shortest and longest (PACE_S); without a pace, min_s and max_s."""
    used = {}
    out = []
    last = None
    names_all = [n for s in script["segments"] for n in s.get("pictures") or [] if pics.get(n)]
    for i, s in enumerate(script["segments"]):
        a = Clock.f(clock.segs[i][0]) if i else 0
        b = Clock.f(clock.segs[i + 1][0]) if i + 1 < len(script["segments"]) else f_end
        names = [n for n in s.get("pictures") or [] if pics.get(n)] or ([last] if last else names_all[:1])
        if not names:
            continue
        span = (b - a) / FPS
        lo_s, hi_s = PACE_S.get(str(s.get("pace") or script.get("pace") or ""), (min_s, max_s))
        k = max(1, min(len(names) * 2, int(span / lo_s)))
        k = max(k, int(math.ceil(span / hi_s)))
        k = max(k, len(names))
        cuts = [a + int(round((b - a) * j / k)) for j in range(k + 1)]
        for j in range(k):
            name = names[min(j * len(names) // k, len(names) - 1)]
            lst = pics[name]
            rec = lst[used.get(name, 0) % len(lst)]
            used[name] = used.get(name, 0) + 1
            out.append([cuts[j], cuts[j + 1], name, rec])
            last = name
    # never the same photograph twice in a row: merge such neighbours into one hold
    merged = []
    for s in out:
        if merged and merged[-1][3]["path"] == s[3]["path"]:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    return merged


def shown_items(s):
    """A segment's display strings, as (text, word) whatever form the writer gave them."""
    out = []
    for x in s.get("show") or []:
        if isinstance(x, dict):
            out.append((str(x.get("text") or ""), x.get("word")))
        else:
            out.append((str(x), None))
    return [o for o in out if o[0]]


# ----------------------------------------------------------------- the engines beside this plugin (worker/scene, worker/score)

def _engines():
    w = scene_worker()
    if w not in sys.path:
        sys.path.insert(0, w)
    from scene import bake, looks, pace
    return pace, looks, bake


def voice_stress(sup):
    """The narration's stress peaks as (programme frame, strength 0..1): the speech band's syllable peaks, by signal."""
    pace, _, _ = _engines()
    if sup.get("ffmpeg"):
        pace.FF = sup["ffmpeg"]
    try:
        return [(int(f) + LEAD_F, float(s)) for f, s in pace.stress(sup["narration"], FPS)]
    except Exception:  # noqa: BLE001 - no signal: cuts fall on the even division
        return []


def face_meta(rec):
    """A picture's faces as the pacing driver reads them: [x, y, w, h, eyes y] in picture fractions."""
    out = []
    for f in rec.get("faces") or []:
        x0, y0, x1, y1 = f["box"]
        ey = (f["eyes"][0][1] + f["eyes"][1][1]) / 2 if f.get("eyes") else y0 + (y1 - y0) * 0.42
        out.append([x0, y0, x1 - x0, y1 - y0, ey])
    return out


# ----------------------------------------------------------------- a display string against the words that say it

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
         "eighteen nineteen").split()
_TENS = "- - twenty thirty forty fifty sixty seventy eighty ninety".split()


def _spell(n):
    """A whole number as spoken words, tens and ones joined the way a script writes them (twenty-seven)."""
    n = int(n)
    if n < 20:
        return [_ONES[n]]
    if n < 100:
        return [_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else "")]
    if n < 1000:
        return [_ONES[n // 100], "hundred"] + (_spell(n % 100) if n % 100 else [])
    for size, name in ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")):
        if n >= size:
            return _spell(n // size) + [name] + (_spell(n % size) if n % size else [])
    return []


def spoken_forms(text):
    """The ways a printed string can be said, each a list of normalised words: its words with every figure spelled out,
    and a four-digit figure also as a year (nineteen seventy-three). Arithmetic on the film's own printed strings."""
    toks, alts = [], [[]]
    cur = ""
    for ch in str(text) + " ":
        if ch.isalnum() or (ch in ",." and cur[-1:].isdigit()):
            cur += ch
        else:
            if cur:
                toks.append(cur.rstrip(",."))
            cur = ""
    for t in toks:
        body = t.replace(",", "")
        if body.replace(".", "", 1).isdigit():
            whole = body.split(".")[0]
            forms = [[norm_word(w) for w in _spell(int(whole))]]
            if len(whole) == 4 and 1100 <= int(whole) <= 2099 and "." not in body:
                a, b = int(whole[:2]), int(whole[2:])
                forms.append([norm_word(w) for w in _spell(a) + (_spell(b) if b >= 10 else (["hundred"] if b == 0 else ["oh"] + _spell(b)))])
            alts = [x + f for x in alts for f in forms]
        else:
            alts = [x + [norm_word(t)] for x in alts]
    return [a for a in alts if a]


def said_at(clock, seg, text):
    """The spoken word of segment `seg` on which a printed string is said: (its start in seconds, words matched), or
    None. The first word of the best run: the string's words in order, a filler word allowed between two of them."""
    ws = clock.seg_words(seg)
    keys = [norm_word(w["w"]) for w in ws]
    best = None
    for form in spoken_forms(text):
        for j, k in enumerate(keys):
            if k != form[0]:
                continue
            got, at = 1, j
            for tok in form[1:]:
                nxt = next((q for q in range(at + 1, min(at + 3, len(keys))) if keys[q] == tok), None)
                if nxt is None:
                    break
                got, at = got + 1, nxt
            score = got / len(form)
            if best is None or score > best[1] + 1e-9:
                best = (ws[j]["t0"], score, got)
    return (best[0], best[2]) if best and best[1] >= 0.5 else None


# ----------------------------------------------------------------- the pictures: real footage first, then photographs

def clause_starts(clock, seg, gap=0.22):
    """Frames inside a segment where a new clause starts: after a word that ends in punctuation, or after a pause."""
    ws = clock.seg_words(seg)
    out = []
    for j in range(1, len(ws)):
        if ws[j - 1]["w"].endswith((",", ".", "?", "!", ";", ":")) or ws[j]["t0"] - ws[j - 1]["t1"] >= gap:
            out.append(Clock.f(ws[j]["t0"]) - 1)
    return out


def too_close(c):
    """A clip cut to the phone whose largest face is wider than 0.45 of the frame: a broadcast close-up, closer than
    any reference holds a head in a vertical film."""
    if int(c.get("w") or W) > int(c.get("h") or H):
        return False                                   # a clip kept wide plays in a window: its head is a share of the window
    return any((f["box"][2] - f["box"][0]) > 0.45 for f in c.get("faces") or [])


def supply_shots(script, clock, pics, footage, f_end, stress, aim_s=1.0, lo_s=0.55, hi_s=1.5, slow=0.75, never_s=2.6, opener=None):
    """The film's base pictures, a new one about every second (the reference stories change every 0.5 to 1.5 s):
    [[f0, f1, kind, name, rec]], kind "clip" (real footage found for that line, worker/footage) or "photo".

    A line's time is shared between the things it names in the script's order: a thing's pictures begin with the clause
    that says its name (the writer's own name against the writer's own words), else at the clause boundary nearest an
    even share. Inside a share its clips play first, then its photographs; a cut lands on the voice's strongest stress
    in its window; a clip is never held past its own length (it may run a quarter slow); a photograph is never shown
    twice in a row."""
    lo, hi, aim, never = int(round(lo_s * FPS)), int(round(hi_s * FPS)), aim_s * FPS, int(round(never_s * FPS))
    used, out, last_names = {}, [], []
    spare, played = {}, set()                # clips earlier lines did not play, by subject: a later line in the same place may
    names_all = [n for s in script["segments"] for n in s.get("pictures") or [] if pics.get(n)]
    nseg = len(script["segments"])

    def photo(name):
        lst = pics.get(name) or []
        if not lst:
            return None
        for _ in range(len(lst)):
            rec = lst[used.get(name, 0) % len(lst)]
            used[name] = used.get(name, 0) + 1
            if not out or out[-1][4].get("path") != rec["path"]:
                return rec
        return lst[0]

    def cap(src):
        return int(src[2]["seconds"] * FPS / slow) if src[0] == "clip" else never

    def fill(a, b, name, clips, before=()):
        n_ph = len(pics.get(name) or [])
        want = max(1, int(round((b - a) / aim)))
        srcs = [("clip", name, c) for c in clips]
        k = 0
        while len(srcs) < want and k < max(n_ph, 1) and n_ph:
            srcs.append(("photo", name, None))
            k += 1
        # still short of pictures: clips no line has played, of this thing first (after its own), then of the place
        # the line before was in (ahead of them: the film is still arriving there)
        own = [c for c in spare.get(name) or [] if c["path"] not in played]
        while len(srcs) < want and own:
            srcs.append(("clip", name, own.pop(0)))
        lead = [(n, c) for n in before if n != name for c in spare.get(n) or [] if c["path"] not in played]
        ins = 0
        while len(srcs) < want and lead:
            n, c = lead.pop(0)
            srcs.insert(ins, ("clip", n, c))
            ins += 1
        srcs = srcs[:max(want, 1)] if sum(cap(s) for s in srcs[:max(want, 1)]) >= b - a else srcs
        while sum(cap(s) for s in srcs) < b - a and n_ph and len(srcs) < 12:
            srcs.append(("photo", name, None))
        if not srcs:
            return
        pos = a
        for j, src in enumerate(srcs):
            left = len(srcs) - j
            if pos >= b - 1:
                break
            if left == 1:
                end = b
            else:
                rest = sum(cap(s) for s in srcs[j + 1:])
                tgt = pos + (b - pos) / left
                w0 = max(pos + lo, b - rest)
                w1 = min(pos + min(cap(src), hi), b - lo * (left - 1))
                if w1 < w0:
                    end = int(min(max(tgt, pos + 3), b))
                else:
                    cands = [(s - abs(f - tgt) / FPS * 0.7, -f) for f, s in stress if w0 <= f <= w1]
                    end = -max(cands)[1] if cands else int(round(min(max(tgt, w0), w1)))
            kind, nm, rec = src
            if kind == "photo":
                rec = photo(nm)
                if rec is None:
                    continue
            else:
                n = end - pos
                dur = float(rec["seconds"])
                t0 = max((dur - n / FPS) / 2, 0.0) if n / FPS <= dur else 0.0
                rec = dict(rec, src=[round(t0, 3), round(min(t0 + n / FPS, dur), 3)])
                played.add(rec["path"])
            out.append([pos, end, kind, nm, rec])
            pos = end
        if out and out[-1][1] < b:
            out[-1][1] = b

    footage = {k: list(v) for k, v in (footage or {}).items()}
    first = next((c for v in footage.values() for c in v if opener and os.path.basename(c["path"]) == opener), None)
    if first is None and footage.get(0):
        # frame 0 is the hook's subject in his world, whole: when the hook's own clips are all too close, the widest
        # clip of that subject that still shows a person (found for a later line that has clips to spare) opens the film
        subj = next((n for n in script["segments"][0].get("pictures") or []), None)
        mine = [c for c in footage[0] if c.get("subject") == subj]
        if mine and all(too_close(c) for c in mine):
            def face_w(c):
                return max((f["box"][2] - f["box"][0] for f in c.get("faces") or []), default=0.0)
            pool = [c for k, v in footage.items() if k and len(v) >= 3 for c in v if c.get("subject") == subj and 0.08 <= face_w(c) <= 0.36]
            first = min(pool, key=face_w) if pool else None
    if first is not None:
        for k in footage:
            footage[k] = [c for c in footage[k] if c is not first]
        footage[0] = [first] + footage.get(0, [])
    for i, s in enumerate(script["segments"]):
        a = Clock.f(clock.segs[i][0]) if i else 0
        b = min(Clock.f(clock.segs[i + 1][0]) if i + 1 < nseg else f_end, f_end)
        if b - a < 3:
            continue
        clips = sorted((c for c in (footage or {}).get(i) or [] if c["path"] not in played), key=lambda c: (c is not first, too_close(c)))     # a head that fills the phone waits for last
        names = [n for n in s.get("pictures") or [] if pics.get(n) or any(c.get("subject") == n for c in clips)]
        names += [c["subject"] for c in clips if c.get("subject") and c["subject"] not in names]
        names = list(dict.fromkeys(names))
        if not names:
            names = [n for n in last_names if pics.get(n)] or names_all[:1]
        if not names:
            continue
        # the line's time, shared between the things it names
        bounds = clause_starts(clock, i)
        marks = [a]
        for j, n in enumerate(names[1:], 1):
            even = a + (b - a) * j / len(names)
            keys = {norm_word(w) for w in str(n).split() if len(norm_word(w)) >= 4}
            said = next((Clock.f(w["t0"]) - 1 for w in clock.seg_words(i) if norm_word(w["w"]) in keys), None)
            floor = marks[-1] + lo
            ceil = b - int(0.9 * FPS) * (len(names) - j)
            if said is not None:
                cl = [c for c in bounds if floor <= c <= min(said, ceil)]
                at = max(cl) if cl else min(max(said, floor), ceil)
            else:
                cl = [c for c in bounds if floor <= c <= ceil]
                at = min(cl, key=lambda c: abs(c - even)) if cl else int(min(max(even, floor), ceil))
            marks.append(int(max(at, marks[-1] + 2)))
        marks.append(b)
        for j, n in enumerate(names):
            if marks[j + 1] - marks[j] >= 3:
                fill(marks[j], marks[j + 1], n, [c for c in clips if c.get("subject") == n], before=last_names if j == 0 else ())
        for c in clips:
            if c["path"] not in played:
                spare.setdefault(c.get("subject"), []).append(c)
        last_names = names
    # close any gap, and never a sliver
    out.sort(key=lambda r: r[0])
    for j in range(len(out) - 1):
        out[j][1] = out[j + 1][0]
    if out:
        out[0][0] = 0
        out[-1][1] = f_end
    return [r for r in out if r[1] - r[0] >= 2]


def pin_voices(timeline, voices):
    """The seconds a real voice speaks (sup["voices"]: narration start/end, its clip) show that voice's own picture,
    whole, from its first frame, so the lips meet the words; the pictures around it are trimmed to make room."""
    for v in voices:
        c = v.get("clip") or {}
        if not c.get("path") or not os.path.exists(c["path"]):
            continue
        f0, f1 = Clock.f(float(v["start"]) + LEAD_F / FPS), Clock.f(float(v["end"]) + LEAD_F / FPS)
        f1 = min(f1, f0 + int(float(c.get("seconds") or (f1 - f0) / FPS) * FPS))
        if f1 - f0 < 6:
            continue
        out = []
        for r in timeline:
            a, b = r[0], r[1]
            if b <= f0 or a >= f1:
                out.append(r)
                continue
            if a < f0:
                out.append([a, f0] + r[2:])
            if b > f1:
                rec = r[4]
                if r[2] == "clip" and rec.get("src"):
                    k = (f1 - a) / FPS
                    rec = dict(rec, src=[round(min(rec["src"][0] + k, float(rec["seconds"])), 3), rec["src"][1]])
                out.append([f1, b, r[2], r[3], rec])
        out.append([f0, f1, "clip", v.get("person"), dict(c, voice=v.get("person"), src=[0.0, round((f1 - f0) / FPS, 3)])])
        timeline = sorted((r for r in out if r[1] - r[0] >= 2), key=lambda r: r[0])
    return timeline


# ----------------------------------------------------------------- captions that ink word by word; display words

def caption_build(P, clock, v, y, ink="#FFFFFF", f_end=None, skip=(), hold=(), z=0, max_chars=22, max_words=4, late=2):
    """Captions at one seat for the whole film (TASTE P1), the way the reference stories set them: a line is laid out
    centred as it will stand, and each word is inked on its own spoken word, where it will stay; the line leaves at
    once when the next begins. `hold`: [(f0, f1)] when a display word has the seat and the caption yields to it."""
    ph = clock.phrases(max_chars=max_chars, max_words=max_words)
    n_lines = 0
    for i, (t0, t1, text, ws) in enumerate(ph):
        a, b = Clock.f(t0) - 1, Clock.f(t1)
        if i + 1 < len(ph):
            b = min(b, Clock.f(ph[i + 1][0]) - 1)       # one line at a time, never two on the seat
        if f_end is not None:
            b = min(b, f_end)
        if any(s0 <= a < s1 for s0, s1 in skip):
            continue
        first = a
        for h0, h1 in hold:                              # the display has the seat: the line ends when it comes, or waits for it to go
            if h0 <= a < h1:
                first = max(first, h1)
            elif a < h0 < b:
                b = h0
        if b - first < 5:
            continue
        x_left = 0.5 - P.R.width(v, text) / W / 2
        toks = text.split()
        for j, w in enumerate(ws):
            on = max(Clock.f(w["t0"]) + late, first, 0) if j else max(first, 0)
            if on >= b - 1:
                continue
            x = x_left + (P.R.width(v, " ".join(toks[:j]) + " x") - P.R.width(v, "x")) / W if j else x_left
            P.word([on, b], toks[j] if j < len(toks) else w["w"], v, x, y, ink, 1.0, note=False, z=z, role="cap", stroke=dict(w=1.6, c="#0B0A09"))
        n_lines += 1
    return n_lines


def display_words(P, clock, script, v, y, f_end, skip_text=(), ink="#FFFFFF", accent="#F2C14E", gap_s=2.2, hold_s=1.15, max_w=0.70, px_max=190):
    """The writer's display strings as large type on the caption seat, each landing on the word that says it (blur
    10 to 0, a rise of 0.03 h, a hair of overshoot, 9 frames: bake.BOUNCE): figures in the accent, names in ink.
    A string the film already prints elsewhere is left out (TASTE P7: say it once); two never come closer than gap_s.
    Returns [(f0, f1, text)]."""
    _, _, bake = _engines()
    out, last = [], -10 ** 6
    for i, s in enumerate(script["segments"]):
        for text, word in shown_items(s):
            if any(x in text or text in x for x in skip_text):
                continue
            hit = said_at(clock, i, text)
            if hit is None and word:
                hit = (clock.word_t(i, word), 1)
            if hit is None:
                continue
            a = max(Clock.f(hit[0]) - 1, 1)
            if a - last < gap_s * FPS or a >= f_end - 12:
                continue
            b = int(min(a + hold_s * FPS, f_end, Clock.f(clock.segs[i][1]) + 10))
            if b - a < 14:
                continue
            vk = f"{v}{a}"
            P.voices[vk] = dict(P.voices[v])
            P.voices[vk]["px"] = P.R.fit_px(vk, text, max_w * W, px_max)
            n = b - a
            k = min(int(bake.BOUNCE["n"]), n)
            rise = bake.bounce(k, rest=0.0)
            ys = [r4(y + float(q)) for q in rise] + [r4(y)] * (n - k)
            bl = [round(float(q), 3) for q in bake.bounce_blur(k)] + [0.0] * (n - k)
            ops = [min(1.0, (q + 1) / 2) for q in range(n)]
            num = any(ch.isdigit() for ch in text)
            P.word([a, b], text, vk, 0.5, ys, accent if num else ink, ops, align="c", z=3, blur=bl, role="big", stroke=dict(w=3.0, c="#0B0A09"))
            P.events.append(a)
            out.append((a, b, text))
            last = a
    return out


# ----------------------------------------------------------------- the engines on a baked scene

def _thumb(path, side=360):
    import numpy as np
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        k = side / max(im.size)
        return np.asarray(im.resize((max(int(im.size[0] * k), 2), max(int(im.size[1] * k), 2))))


def narration_spine(l1, sup):
    """The narration as the scene's spine: the voice from frame LEAD_F, on its own clock."""
    d = json.load(open(sup["words"])) if isinstance(sup["words"], str) else sup["words"]
    dur = float(d["duration"])
    end = min(int(l1["dur_f"]), LEAD_F + int(math.ceil(dur * FPS)))
    return [dict(f=[LEAD_F, end], src=[0.0, round((end - LEAD_F) / FPS, 4)])], d, dur


def clear_of(items, assets_meta, rect, ids):
    """Keep the subject's head out of a seat the film's own type holds (TASTE: nothing on a face). For each driven
    shot whose largest face, with its hair, would pass under `rect` (frame fractions): the whole move slides so the
    head sits below the seat, or beside it when that is the shorter way, as far as the picture has room. The move
    itself is untouched: every frame's crop shifts by the same amount. Returns how many shots moved."""
    moved = 0
    for it in items:
        if it.get("k") != "shot" or it["id"].split(".")[0] not in ids or not it.get("crop"):
            continue
        fs = (assets_meta.get(it.get("pic")) or {}).get("faces") or []
        if not fs:
            continue
        fx, fy, fw, fh = max(fs, key=lambda f: f[2] * f[3])[:4]
        crops = it["crop"] if isinstance(it["crop"][0], list) else [it["crop"]]
        need_dy = need_dx = 0.0
        for c in crops:
            cw, ch = c[2] - c[0], c[3] - c[1]
            sx0, sx1 = (fx - c[0]) / cw, (fx + fw - c[0]) / cw
            sy0, sy1 = (fy - 0.28 * fh - c[1]) / ch, (fy + fh - c[1]) / ch
            if sx0 < rect[2] and sx1 > rect[0] and sy0 < rect[3] and sy1 > rect[1] and 0 < (sx0 + sx1) / 2 < 1:
                need_dy = max(need_dy, (rect[3] - sy0) * ch)           # slide the picture down: the crop moves up
                need_dx = max(need_dx, (rect[2] - sx0) * cw)           # or the head right: the crop moves left
        if need_dy <= 0:
            continue
        room_y = min(c[1] for c in crops)
        room_x = min(c[0] for c in crops)
        cw0 = crops[0][2] - crops[0][0]
        right_edge = max((fx + fw - c[0]) / (c[2] - c[0]) for c in crops)
        dx_ok = need_dx <= room_x and right_edge + need_dx / cw0 <= 0.96
        dy = min(need_dy, room_y)
        if dx_ok and need_dx / cw0 < need_dy / (crops[0][3] - crops[0][1]) and dy < need_dy:
            sh = (-need_dx, 0.0)
        else:
            sh = (0.0, -dy)
        if abs(sh[0]) + abs(sh[1]) < 1e-5:
            continue
        new = [[r4(c[0] + sh[0]), r4(c[1] + sh[1]), r4(c[2] + sh[0]), r4(c[3] + sh[1])] for c in crops]
        it["crop"] = new if isinstance(it["crop"][0], list) else new[0]
        moved += 1
    return moved


def engines_finish(l1, sup, stress, drive_ids, meta, d, turn_f=None, mono=(), clear=None, spoken=None, np10=None):
    """What the engines do to a baked scene (worker/scene, worker/score), in the order a film is finished:
    pace   every driven picture is cut into framings on the voice's stress and set moving: a slow push, a settle on a
           stressed cut-in, an eased move in on the strongest word, a whip through about one cut in eleven, a face cut
           to a face with the eyes kept (pace.drive_l1, the references' measured curves);
    look   one look solved on the film's own pictures, skin kept (looks.film); `mono`: [(f0, f1)] spans drawn in
           black and white (the low point), colour back after them;
    soft   one new picture in three opens soft and is sharp by its fourth frame (the references: 28% of shots);
    sound  each chosen event gets the recording the references would use, its peak on the event, set against this
           narration (score.sound.design). `np10`: the film's new pictures a 10 s when the caller counts them (prints
           and windows are cards the sound engine would not count as pictures); None lets the engine count."""
    import numpy as np
    pace, looks, bake = _engines()
    base = {it["id"]: it for it in l1["items"] if it.get("k") == "shot"}
    for a, b in mono:
        for it in base.values():
            if it["id"] in drive_ids and a <= it["f"][0] < b:
                it["look"] = "mono_silver"
    out = pace.drive_l1(l1, sup.get("work") or "/", stress, meta=meta, family=d.get("family") or "story", only=set(drive_ids), band=d.get("band"))
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    out["items"].sort(key=lambda it: (it["f"][0], order_k[it["k"]], it.get("z", 0), it["id"]))
    if clear:
        out.setdefault("pace", {})["cleared"] = clear_of(out["items"], meta, clear, drive_ids)
    k = 0
    for it in out["items"]:
        if it.get("k") != "shot" or it["id"] not in drive_ids or it["f"][0] == 0:       # a new picture, not a framing of one
            continue
        k += 1
        n = it["f"][1] - it["f"][0]
        if k % 3 == 0 and n >= 12 and float(it.get("shutter") or 0) < pace.WHIP_SHUTTER:
            it["blur"] = [11.0, 6.0, 2.5] + [0.0] * (n - 3)
    try:
        pics = [_thumb(p) for p in (d.get("look_pictures") or [])[:40]]
        out["film"] = looks.film(d.get("look") or "story_low", pictures=pics or None, base=out.get("film"))
    except Exception as e:  # noqa: BLE001 - a picture that cannot be read leaves the look as designed
        out["film"] = looks.film(d.get("look") or "story_low", base=out.get("film"))
        out["film"]["look_fault"] = repr(e)[:160]
    out["film"]["shutter"] = out["film"].get("shutter") or (out.get("pace") or {}).get("shutter") or 0.6
    # the sound design, against this film's own voice
    spine, wd, dur = narration_spine(out, sup)
    try:
        from score import sound as SD
        # each word's own moment on the narration's clock (the film's clock repairs spans the aligner shared out)
        words = [dict(t0=float(a), t1=float(b)) for a, b in spoken] if spoken else \
            [dict(t0=float(w["start"]), t1=float(max(w["end"], w["start"] + 0.02))) for w in wd["words"]]
        sounds, rep = SD.design(dict(out, spine=spine, sound=[]), index=dict(words=words, video=dict(duration=dur)), src=sup["narration"],
                                turn_s=(turn_f / FPS if turn_f is not None else None), bed=bool(d.get("bed")),
                                supply=dict(np10=float(np10), bed=bool(d.get("bed"))) if np10 else None)
        out["sound"], out["sound_design"] = sounds, rep
    except Exception as e:  # noqa: BLE001 - a film is never lost to its sound design
        out["sound"], out["sound_design"] = [], {"fault": repr(e)[:300]}
    return out


def audio_scene(l1, sup):
    """The scene as the sound and music tools read it: the same plan with the narration as its spine."""
    spine, _, _ = narration_spine(l1, sup)
    assets = dict(l1.get("assets") or {})
    assets["main"] = dict(kind="voice", path=sup["narration"], abs=sup["narration"])
    return dict(l1, spine=spine, assets=assets)


# ----------------------------------------------------------------- style: Character Sheet

def _core(text):
    """The printed number inside a stat's text: (start, end, value, decimals), or None."""
    import re
    m = re.search(r"\d[\d,]*(?:\.\d+)?", str(text))
    if not m:
        return None
    body = m.group(0)
    return m.start(), m.end(), float(body.replace(",", "")), (len(body.split(".")[1]) if "." in body else 0), "," in body


def tick_text(a_text, b_text, q):
    """The value between two printed stats, q of the way, printed the way the new one is (its prefix, unit, commas)."""
    ca, cb = _core(a_text), _core(b_text)
    if not cb:
        return str(b_text)
    v = (ca[2] if ca else 0.0) + (cb[2] - (ca[2] if ca else 0.0)) * q
    dec, comma = cb[3], cb[4] or abs(v) >= 10000
    num = f"{v:,.{dec}f}" if comma else f"{v:.{dec}f}"
    t = str(b_text)
    return t[:cb[0]] + num + t[cb[1]:]


def character_sheet(script, sup, direction=None):
    d = dict(low_blink=12, pal="night", aim_s=0.85, look="story_low", display=True, mono_low=True, bed=False)
    d.update(direction or {})
    clock = Clock(sup["words"], script)
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "shadow": {"a": 0.85, "blur": 0.006}},
        "lab": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},
        "val": {"font": "InterDisplay-Black.ttf", "px": 44, "track": 0},
        "dsp": {"font": "InterDisplay-Black.ttf", "px": 170, "track": -2, "shadow": {"a": 0.85, "blur": 0.010}},
        "sub": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 8},
        "ovf": {"font": "InterDisplay-Black.ttf", "px": 220, "track": -2},
    }
    P = Plan(voices, sup.get("sounds") or {})
    pal = dict(page0="#14110F", page1="#0B0A09", ink="#F4EFE6", ink_soft="#BDB4A6", accent="#F2C14E", red="#E5322D", plate="#0A0A0A")
    drop = set(d.get("drop") or [])            # the director's hand: photographs (by title or file) and clips (by file) the film must not use
    pics = {k: [r for r in v if r.get("title") not in drop and os.path.basename(str(r.get("path"))) not in drop] for k, v in sup["pictures"].items()}
    pics = {k: v for k, v in pics.items() if v}
    footage = {int(ln["i"]): [c for c in ln.get("clips") or [] if os.path.basename(str(c.get("file") or c.get("path"))) not in drop
                              and os.path.exists(str(c.get("path")))] for ln in sup.get("footage") or []}
    keys, meta = {}, {}
    for name, lst in pics.items():
        for rec in lst:
            if rec["path"] not in keys:
                keys[rec["path"]] = P.image(f"p{len(keys):02d}", rec)
    for lst in footage.values():
        for c in lst:
            if c["path"] not in keys:
                keys[c["path"]] = P.video(f"v{len(keys):02d}", c)
    f_voice_end = Clock.f(clock.dur)
    ovf = script.get("overflow") or {}
    f_ovf = Clock.f(clock.word_t(len(script["segments"]) - 1, ovf.get("word"))) if ovf else f_voice_end
    f_ovf = min(max(f_ovf, f_voice_end - 42), f_voice_end - 20)      # the number leaves its box on the film's last words
    f_end = max(f_voice_end + 36, f_ovf + 66)
    stress = voice_stress(sup)

    # ---- the pictures: the line's real footage, then photographs, a new one about every second
    timeline = supply_shots(script, clock, pics, footage, f_ovf, stress, aim_s=float(d["aim_s"]), opener=d.get("open"))
    for v in sup.get("voices") or []:            # a real voice cut in (narrate realvoice.py): its own picture, lips with words
        if (v.get("clip") or {}).get("path") and os.path.exists(v["clip"]["path"]):
            if v["clip"]["path"] not in keys:
                keys[v["clip"]["path"]] = P.video(f"v{len(keys):02d}", v["clip"])
    timeline = pin_voices(timeline, sup.get("voices") or [])
    low_seg = next((i for i, s in enumerate(script["segments"]) if s.get("beat") == "low"), None)
    cap_y = 0.615
    # ---- display words on the caption seat: what the sheet prints from its first frame, and its last number, are left to it
    stats = script.get("stats") or []
    printed = [x for x in [str(st.get("start")) for st in stats] + [str(ovf.get("to"))] if x and x != "None"]
    shown = display_words(P, clock, script, "dsp", cap_y + 0.012, f_ovf, skip_text=printed, ink=pal["ink"], accent=pal["accent"]) if d.get("display") else []
    # ---- T4: chosen sentence endings are cuts through their own full stop (at most one in five, never on a display word)
    phrases = clock.phrases(max_chars=22, max_words=4)
    t4 = []
    n_grow = 9
    for i in range(len(script["segments"]) - 1):
        if len(t4) >= max(1, len(script["segments"]) // 5) or i % 3 != 1 or not d.get("t4", True):
            continue
        end_f = Clock.f(clock.segs[i][1])
        j = next((j for j, r in enumerate(timeline) if end_f - 2 <= r[0] <= end_f + 30 and r[2] == "photo" and bleed_ok(r[4])), None)
        last_w = (clock.seg_words(i) or [None])[-1]
        ph = next((p for p in phrases if last_w is not None and p[3][-1] is last_w), None)
        if j is None or not ph or j == 0 or timeline[j][1] - (end_f + n_grow) < 12:
            continue
        if any(a_ < end_f + n_grow + 2 and Clock.f(ph[0]) - 2 < b_ for a_, b_, _ in shown):
            continue
        timeline[j - 1][1] = end_f + n_grow          # the old picture holds while the stop grows
        timeline[j][0] = end_f + n_grow
        if timeline[j - 1][2] == "clip":             # a clip held a little longer runs on, never freezes
            c = timeline[j - 1][4]
            n = timeline[j - 1][1] - timeline[j - 1][0]
            c["src"] = [c["src"][0], round(min(c["src"][0] + n / FPS, float(c["seconds"])), 3)]
        t4.append((i, end_f, timeline[j], ph))
    cut_pts, drive_ids, look_pics = [], set(), []
    for f0, f1, kind, name, rec in timeline:
        if f1 - f0 < 2:
            continue
        key = keys[rec["path"]]
        if kind == "clip" and (too_close(rec) or rec.get("voice")):
            # a broadcast close-up cut to the phone already holds its head as large as a talking-head short does (the
            # references' 0.34 of the height): it plays as it was cut, whole, with no push into the face
            P.shot([f0, f1], key, [0.0, 0.0, 1.0, 1.0], src=list(rec["src"]))
            if rec.get("still"):
                look_pics.append(rec["still"])
            P.pictures.append(dict(f=[f0, f1], kind="clip", name=name, path=rec["path"], shows=rec.get("shows"),
                                   source=(rec.get("source") or {}).get("url"), time_range=rec.get("time_range"), mode="held"))
        elif kind == "clip":
            it = P.shot([f0, f1], key, None, src=list(rec["src"]))
            it.pop("crop")
            drive_ids.add(it["id"])
            fm = face_meta(rec)
            # a clip is cut to the frame already: a face at the references' size (0.24 of the height) or over is not
            # pushed into; a wider clip may come in a little
            meta[key] = dict(pw=int(rec.get("w") or W), ph=int(rec.get("h") or H), faces=fm, max_up=1.0 if any(f[3] >= 0.24 for f in fm) else 1.12)
            if rec.get("still"):
                look_pics.append(rec["still"])
            P.pictures.append(dict(f=[f0, f1], kind="clip", name=name, path=rec["path"], shows=rec.get("shows"),
                                   source=(rec.get("source") or {}).get("url"), time_range=rec.get("time_range")))
        elif bleed_ok(rec) or rec.get("faces"):
            (fx, fy), _ = focus_of(rec)
            it = P.shot([f0, f1], key, cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.40)))
            drive_ids.add(it["id"])
            if key not in meta:
                m = dict(pw=int(rec["w"]), ph=int(rec["h"]), faces=[f for f in face_meta(rec) if f[3] * rec["h"] >= 60], max_up=MAX_UP)
                if not m["faces"]:
                    try:
                        import numpy as np
                        from PIL import Image
                        pace, _, _ = _engines()
                        with Image.open(rec["path"]) as im_:
                            m["regions"] = pace.regions_of(np.asarray(im_.convert("L")), n=3, size=0.5)
                    except Exception:  # noqa: BLE001 - no regions: the picture's own thirds
                        pass
                meta[key] = m
            look_pics.append(rec["path"])
            P.pictures.append(dict(f=[f0, f1], kind="photo", name=name, path=rec["path"], mode="bleed"))
        else:
            fr = still(P, [f0, f1], key, rec)
            look_pics.append(rec["path"])
            P.pictures.append(dict(f=[f0, f1], kind="photo", name=name, path=rec["path"], **fr))
        cut_pts.append(f0)
    # the close: the last picture held, darkened, under the overflowing number
    last_rec = next((r for _, _, k_, _, r in reversed(timeline) if k_ == "photo"), None) or \
        next((r for v in pics.values() for r in v), None)
    if last_rec:
        # under the number the picture is out of focus and dim, and still moving: a slow push to the last frame
        c0 = cover_crop(last_rec["w"], last_rec["h"], W / H, focus_of(last_rec)[0])
        P.shot([f_ovf, f_end], keys[last_rec["path"]], push(c0, f_end - f_ovf, 0.09, focus_of(last_rec)[0]),
               blur=[r4(4 + 10 * smooth(k / 10.0)) for k in range(f_end - f_ovf)], grade=dict(lift=0, gamma=1.0, gain=0.42, sat=0.6, contrast=1, temp=0, tint=0))
    else:
        P.page([f_ovf, f_end], pal["page0"], pal["page1"])
    skip_caps = []
    for i, end_f, nxt, ph in t4:
        text = ph[2].rstrip(".,;:!?")
        x_left = 0.5 - P.R.width("cap", text + ".") / W / 2
        a = Clock.f(ph[0]) - 1
        dot_x = x_left + P.R.width("cap", text) / W
        b = end_f + n_grow
        P.word([a, b], text, "cap", x_left, cap_y, "#FFFFFF", [1.0] * (end_f + 2 - a) + [0.0] * (b - end_f - 2), note=False, z=2, role="cap")
        rec = nxt[4]
        crop0 = cover_crop(rec["w"], rec["h"], W / H, focus_of(rec)[0], (0.5, 0.40))
        P.word([a, end_f], ".", "cap", dot_x, cap_y, "#FFFFFF", 1.0, note=False, z=2, role="cap")
        P.word([end_f, b], ".", "cap", dot_x, cap_y, "#FFFFFF", 1.0, note=False, z=3, s=[r4(1.9 ** k) for k in range(b - end_f)],
               fill=dict(pic=keys[rec["path"]], crop=crop0, frame=True), role="big")
        skip_caps.append((a, b))
        P.events.append(end_f)

    # ---- G4: the stat block
    bx0, by0 = 0.05, 0.105
    row_h, pad = 0.034, 0.011
    bw = 0.40
    bh = pad * 2 + row_h * len(stats)
    box = [bx0, by0, bx0 + bw, by0 + bh]
    P.block([0, f_ovf + 10], box, pal["plate"], z=4, op=[r4(0.78 * min(1.0, (f_ovf + 10 - k) / 10.0)) if k > f_ovf else 0.78 for k in range(f_ovf + 10)],
            radius=0.010, border=dict(w=0.0012, c="#3A3530"))
    ticks = []
    for i, s in enumerate(script["segments"]):
        for t in s.get("ticks") or []:
            ticks.append(dict(t, f=Clock.f(clock.word_t(i, t.get("word"))), seg=i, beat=s.get("beat")))
    ticks.sort(key=lambda t: t["f"])
    low_stat = script.get("low_stat") or d.get("low_stat")
    if low_seg is not None and stats and not low_stat:
        lf = Clock.f(clock.segs[low_seg][0])

        def at(st):
            v = float(st.get("number") or 0)
            for t in ticks:
                if t.get("stat") == st["id"] and t["f"] <= lf:
                    v = float(t.get("number") or 0)
            return abs(v) / max(abs(float(st.get("max") or 0)), abs(v), 1e-9)
        low_stat = min(stats, key=at)["id"]
    stat_rows = []
    for r, st in enumerate(stats):
        y_base = by0 + pad + row_h * r + row_h * 0.62
        lab_x = bx0 + 0.022
        P.word([0, f_ovf + 4], str(st["label"]).upper(), "lab", lab_x, y_base, pal["ink_soft"], 0.9, z=5, role="stat")
        mine = [t for t in ticks if t.get("stat") == st["id"] and t["f"] < f_ovf]
        vals = [(0, str(st["start"]), float(st.get("number") or 0))] + [(t["f"], str(t["to"]), float(t.get("number") or 0)) for t in mine]
        mx = max([abs(float(st.get("max") or 0))] + [abs(v[2]) for v in vals] + [1e-9])
        right = bx0 + bw - 0.022
        bar_y0 = by0 + pad + row_h * r + row_h * 0.80
        bar_x0, bar_x1 = lab_x, right
        P.block([0, f_ovf + 4], [bar_x0, bar_y0, bar_x1, bar_y0 + 0.0042], "#2A2724", z=5)
        for j, (f0, text, num) in enumerate(vals):
            f1 = vals[j + 1][0] if j + 1 < len(vals) else f_ovf + 4
            if f1 - f0 < 1:
                continue
            prev = vals[j - 1] if j else None
            n_tick = 7 if prev else 0
            # the number ticks from the old value to the new over 7 frames, in accent, then settles back to ink
            for k in range(min(n_tick, f1 - f0)):
                tx = tick_text(prev[1], text, expo_out(n_tick)[k])
                P.word([f0 + k, f0 + k + 1], tx, "val", right, y_base + 0.002, pal["accent"], 1.0, align="r", note=False, z=6,
                       role="big" if k == 0 else "stat")
            hold_a = f0 + min(n_tick, f1 - f0)
            if f1 > hold_a:
                inks = pal["accent"] if prev else pal["ink"]
                P.word([hold_a, f1], text, "val", right, y_base + 0.002, inks, 1.0, align="r", z=6, role="stat")
            # the bar: its length is the value against the stat's most
            frac = max(min(abs(num) / mx, 1.0), 0.012)
            bar = [bar_x0, bar_y0, bar_x0 + (bar_x1 - bar_x0) * frac, bar_y0 + 0.0042]
            # the low point: from the low segment's first word, the stat at its worst against its most goes red and
            # blinks, until its next change
            red_from = None
            if low_seg is not None and st["id"] == low_stat:
                lf = Clock.f(clock.segs[low_seg][0])
                if f0 <= lf < f1:
                    red_from = lf
            if red_from is not None:
                if red_from > f0:
                    P.block([f0, red_from], bar, pal["accent"], z=6)
                blink = int(d["low_blink"])
                ops = [(1.0 if (k // 2) % 2 == 0 else 0.0) if k < blink else 1.0 for k in range(f1 - red_from)]
                P.block([red_from, f1], bar, pal["red"], z=6, op=ops)
                P.events.append(red_from)
            else:
                P.block([f0, f1], bar, pal["accent"], z=6)
            if prev:
                P.events.append(f0)
        stat_rows.append(dict(id=st["id"], y=r4(y_base), right=r4(right), values=[v[1] for v in vals]))
    # ---- the captions, yielding the seat to a display word
    lines = caption_build(P, clock, "cap", cap_y, f_end=f_voice_end + 8, skip=skip_caps, hold=[(a - 1, b) for a, b, _ in shown])
    # a soft pool of shade under the one seat, so white ink reads on a bright sky as on a dark room (TASTE P4)
    P.flash([0, f_voice_end + 8], mode="pool", a=float(d.get("pool", 0.42)), rect=[0.06, cap_y - 0.105, 0.94, cap_y + 0.075], z=-1)
    # ---- the turn: the first change of the sheet on the payoff, else the payoff's first word. A white frame lands on it
    pay = next((i for i, s in enumerate(script["segments"]) if s.get("beat") == "payoff"), None)
    turn_f = next((t["f"] for t in ticks if t["seg"] == pay), None) if pay is not None else None
    if turn_f is None and pay is not None:
        turn_f = Clock.f(clock.segs[pay][0])
    if turn_f is not None:
        P.flash([turn_f, turn_f + 3], mode="fill", c="#FFFFFF", a=[0.85, 0.45, 0.15], z=8)
        P.events.append(turn_f)
    mono = []
    if low_seg is not None and d.get("mono_low"):
        lf = Clock.f(clock.segs[low_seg][0])
        cut = min((c for c in cut_pts if c >= lf - 6), default=lf)
        mono = [(cut, turn_f if turn_f is not None and turn_f > cut else Clock.f(clock.segs[low_seg][1]))]
        P.flash([cut, cut + 2], mode="neg", a=1.0, z=8)                # the low point opens on a negative frame
        P.events.append(cut)
    # ---- the overflow: the last stat grows past its box and fills the frame
    if ovf:
        r = next((k for k, st in enumerate(stats) if st["id"] == ovf.get("stat")), 0)
        y_base = by0 + pad + row_h * r + row_h * 0.62
        text = str(ovf.get("to"))
        voices["ovf"]["px"] = P.R.fit_px("ovf", text, 0.84 * W, 230)
        w_big = P.R.width("ovf", text) / W
        k0 = voices["val"]["px"] / voices["ovf"]["px"]
        right = bx0 + bw - 0.022
        n = 22
        e = ease_io(n)
        x_small = right - w_big * k0
        xs, ys, ss = [], [], []
        for q in e:
            s_ = k0 + (1 - k0) * q
            cx = (x_small + w_big * k0 / 2) * (1 - q) + 0.5 * q
            xs.append(r4(cx - w_big * s_ / 2 + (w_big * s_ / 2 - w_big / 2)))
            ys.append(r4(y_base * (1 - q) + 0.50 * q))
            ss.append(r4(s_))
        a = f_ovf
        P.word([a, a + n], text, "ovf", xs, ys, pal["accent"], 1.0, s=ss, z=7, role="stat")
        P.word([a + n, f_end], text, "ovf", 0.5 - w_big / 2, 0.50, pal["accent"], 1.0, z=7, role="big")
        lab = next((st["label"] for st in stats if st["id"] == ovf.get("stat")), "")
        P.word([a + n + 4, f_end], str(lab).upper(), "sub", 0.5, 0.50 + 0.055, pal["ink_soft"], 0.9, align="c", z=7, role="stat")
        P.events.append(a + n)
    film = dict(grain=0.014, vignette=0.4, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf3"), f_end, int(d.get("seed", 1113)), film)
    l1 = engines_finish(l1, sup, stress, drive_ids, meta, dict(d, look_pictures=look_pics), turn_f=turn_f, mono=mono,
                        clear=[bx0, by0, bx0 + bw + 0.01, by0 + bh + 0.008] if stats else None,
                        spoken=[(w["t0"] - LEAD_F / FPS, w["t1"] - LEAD_F / FPS) for w in clock.words])
    changes = sorted({it["f"][0] for it in l1["items"] if it["k"] == "shot"})
    for s_ in l1.get("sound") or []:
        P.events.append(int(s_["event_f"]))
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], overflow_f=f_ovf, cuts=sorted(set(cut_pts)), changes=changes, t4=[x[1] for x in t4],
                 events=sorted(set(P.events) | set(cut_pts) | set(changes)), pictures=P.pictures, stats=stat_rows, on_screen=P.shown, caption_y=cap_y,
                 caption_lines=lines, display=[dict(f=[a, b], text=t) for a, b, t in shown], turn_f=turn_f, pace=l1.get("pace"),
                 sound_design={k: v for k, v in (l1.get("sound_design") or {}).items() if k != "dropped"},
                 look=sorted({0, 12, *[c + 5 for c in cut_pts[::2]], *[a + 10 for a, _, _ in shown], *[x[1] + 4 for x in t4], f_ovf + 25, f_end - 2,
                              *([turn_f + 8] if turn_f is not None else []),
                              *([Clock.f(clock.segs[low_seg][0]) + 6] if low_seg is not None else [])}))
    return l1, beats


# ----------------------------------------------------------------- style: Whole Page

def whole_page(script, sup, direction=None):
    # the page is seen below `top`: above it is the desk, where the product's mark sits on dark ground and no word
    # passes under it (TASTE P2, the mark's keep-out). The poster at the close sits in the same window.
    d = dict(read_y=0.55, top=0.215, bottom=0.975, paper0="#F3EEE4", paper1="#E9E2D4", ink="#191714", wait=0.22, ground="#171513",
             poster_fill=0.98)
    d.update(direction or {})
    clock = Clock(sup["words"], script)
    words = clock.words
    # C1, from the voice: a word the narrator leaned on is set a step larger. Leaned on = loud against its own
    # segment (more than 1.4 standard deviations of the segment's words, measured on the narration), or a word the
    # writer directed the actor to stress. The baseline stays level: a lifted word read as broken type.
    loud = sup.get("loudness") or {}
    lv = [float(loud.get(str(i), -99.0)) for i in range(len(words))]
    pitch = [0.0] * len(words)
    lean = set()
    for si, seg in enumerate(script["segments"]):
        ks = [k for k, w in enumerate(words) if w["seg"] == si and lv[k] > -98]
        if len(ks) >= 3:
            m = sum(lv[k] for k in ks) / len(ks)
            sd = (sum((lv[k] - m) ** 2 for k in ks) / len(ks)) ** 0.5 or 1.0
            lean.update(k for k in ks if (lv[k] - m) / sd > 1.4)
        emph = {norm_word(e) for e in seg.get("emphasis") or [] for e in str(e).split()}
        lean.update(k for k, w in enumerate(words) if w["seg"] == si and norm_word(w["w"]) in emph)
    steps = (1.0, 1.13)
    voices = {"title": {"font": "DMSerifDisplay-Italic.ttf", "px": 118, "track": -1},
              "kick": {"font": "InterDisplay-Bold.ttf", "px": 34, "track": 10}}
    P = Plan(voices, sup["sounds"])
    R = P.R
    pics = sup["pictures"]
    keys = {}
    for name, lst in pics.items():
        if lst:
            keys[name] = P.image(f"p{len(keys):02d}", lst[0])

    def step_of(i):
        return 1 if i in lean else 0

    win_after = {}
    for i, s in enumerate(script["segments"]):
        wdw = s.get("window") or {}
        if wdw.get("subject") in keys:
            seg_ws = [k for k, w in enumerate(words) if w["seg"] == i]
            anchor = str(wdw.get("line") or "").split()
            k_at = seg_ws[-1] if seg_ws else None
            if anchor and seg_ws:
                last = norm_word(anchor[-1])
                k_at = next((k for k in seg_ws if norm_word(words[k]["w"]) == last), k_at)
            if k_at is not None and k_at not in win_after:
                win_after[k_at] = wdw["subject"]
    title = str(script.get("headline") or script.get("title") or "")
    first = {norm_word(w) for w in str(script["segments"][0].get("say", "")).split()}
    tw = [norm_word(w) for w in title.split()]
    if tw and sum(w in first for w in tw) / len(tw) >= 0.6:      # TASTE P7: a headline that repeats the first line is said twice
        title = ""
    kick = next((t for t, _ in shown_items(script["segments"][0])), "")
    col_w = W * 0.80
    margin_top, margin_bot = 0.16 * H, 0.08 * H

    def lay(px, ncols, PH):
        """Words and windows into columns of the page at this size; None when it does not fit."""
        for k, sc in enumerate(steps):
            voices[f"b{k}"] = {"font": "InstrumentSerif-Regular.ttf", "px": round(px * sc, 1), "track": -0.5}
        voices["title"]["px"] = round(px * 1.8, 1)
        lead = px * 1.42
        cols = [(W - col_w) / 2 + c * W for c in range(ncols)]
        tlines, tl = [], ""
        for wd in title.split():
            t = (tl + " " + wd).strip()
            if tl and R.width("title", t) > col_w:
                tlines.append(tl)
                tl = wd
            else:
                tl = t
        if tl:
            tlines.append(tl)
        th = voices["title"]["px"] * 1.05
        y = margin_top + th * len(tlines) + px * 1.6
        bottom = PH - margin_bot
        col, x, out, wins = 0, 0.0, [], []
        space = R.width("b0", " ")
        for k, w in enumerate(words):
            v = f"b{step_of(k)}"
            wd = R.width(v, w["w"])
            if x > 0 and x + wd > col_w:
                x, y = 0.0, y + lead
            if y > bottom:
                col += 1
                if col >= ncols:
                    return None
                x, y = 0.0, margin_top + px
            lift = max(min(pitch[k], 6.0), -6.0) / 6.0 * 0.12 * px
            out.append(dict(k=k, v=v, px=cols[col] + x, py=y - lift, line_y=y, col=col, w=wd))
            x += wd + space
            if k in win_after:
                rec = pics[win_after[k]][0]
                wh = min(col_w * rec["h"] / rec["w"], 0.36 * H, max(0.22 * H, col_w * 0.62))
                ww = min(col_w, wh * rec["w"] / rec["h"]) if rec["w"] / rec["h"] < col_w / wh else col_w
                y_top = y + lead * 0.55
                if y_top + wh > bottom:
                    col += 1
                    if col >= ncols:
                        return None
                    y_top = margin_top + px * 0.2
                wins.append(dict(name=win_after[k], k=k, px0=cols[col] + (col_w - ww) / 2, py0=y_top, w=ww, h=wh, col=col, rec=rec))
                x, y = 0.0, y_top + wh + lead * 1.2
        used = y + lead
        return dict(layout=out, windows=wins, cols=cols, lead=lead, tlines=tlines, th=th, used=used, ncols=ncols, col_last=col)

    best = None
    for ncols in (1, 2):
        PH = H * (1.9 if ncols == 1 else 2.0)
        for px in range(84, 50, -2):
            g = lay(px, ncols, PH)
            if g and (ncols == 2 or True):
                # a page should be full: in two columns the second must be at least half used
                if ncols == 2 and (g["col_last"] < 1 or g["used"] < 0.5 * PH):
                    continue
                best = (g, px, ncols, PH)
                break
        if best:
            break
    if not best:
        g, px, ncols, PH = lay(52, 2, 2.4 * H), 52, 2, 2.4 * H
    else:
        g, px, ncols, PH = best
    g = lay(px, ncols, PH)
    if ncols == 1:
        PH = max(min(g["used"] + margin_bot, 1.9 * H), 1.15 * H)
    PW = W * ncols
    layout, windows, cols, lead = g["layout"], g["windows"], g["cols"], g["lead"]
    top, bot = d["top"], d["bottom"]
    z_end = d["poster_fill"] * min(0.90 * W / PW, (bot - top) * H / PH)
    poster_y = (top + bot) / 2

    # ---- the camera: a stop per word (its line), a window holds the frame while it is talked about
    n_end_voice = Clock.f(clock.dur)
    f_pull0 = n_end_voice + 10
    n_pull = 90
    f_end = f_pull0 + n_pull + 54
    stops = []
    for l in layout:
        w = words[l["k"]]
        stops.append((Clock.f(w["t0"]), cols[l["col"]] + col_w / 2, l["line_y"] - lead * 0.25))
        if l["k"] in win_after:
            wn = next(x for x in windows if x["k"] == l["k"])
            stops.append((Clock.f(w["t1"]) + 2, cols[wn["col"]] + col_w / 2, wn["py0"] + wn["h"] * 0.5 - 0.02 * H))
    stops.sort(key=lambda s: s[0])
    read_y = d["read_y"]
    cy_min = (read_y - top) * H + 0.012 * H              # the page's head never scrolls under the desk's edge
    cx, cy = stops[0][1], max(stops[0][2], cy_min)
    cam = []
    ti = 0
    k = 0.17
    for f in range(f_pull0):
        while ti + 1 < len(stops) and stops[ti + 1][0] <= f:
            ti += 1
        tx, ty = stops[ti][1], max(stops[ti][2], cy_min)
        cx, cy = cx + (tx - cx) * k, cy + (ty - cy) * k        # an exponential follow: most of a line in 8 frames
        cam.append((cx, cy, 1.0, read_y))
    x0, y0, _, _ = cam[-1]
    for j in range(f_end - f_pull0):
        q = ease_io(n_pull)[min(j, n_pull - 1)]
        z = 1.0 / ((1 - q) + q / z_end)
        cam.append((x0 + (PW / 2 - x0) * q, y0 + (PH / 2 - y0) * q, z, read_y + (poster_y - read_y) * q))

    def to_screen(px_, py_, f):
        cx_, cy_, z, yc = cam[f]
        return ((px_ - cx_) * z + W * 0.5) / W, ((py_ - cy_) * z + H * yc) / H, z

    def seen(v, ys, ss):
        """1 where a word stands below the desk's edge, fading to 0 as it passes under it."""
        cap = R.cap(v) / H
        return [min(1.0, max(0.0, (y - cap * s - top) / 0.018)) for y, s in zip(ys, ss)]

    def track_card(px_, py_, a, b):
        xs, ys, ss = [], [], []
        for f in range(a, b):
            x_, y_, z = to_screen(px_, py_, f)
            xs.append(r4(x_))
            ys.append(r4(y_))
            ss.append(r4(z))
        return xs, ys, ss

    def track_word(v, text, px_, py_, a, b):
        """A word set in page space: the engine scales a word about its own centre, so its origin is moved by what
        that scaling would shift, and the word stays where the page puts it."""
        wpx, cap = R.width(v, text), R.cap(v)
        xs, ys, ss = [], [], []
        for f in range(a, b):
            x_, y_, z = to_screen(px_, py_, f)
            xs.append(r4(x_ - (wpx / 2) * (1 - z) / W))
            ys.append(r4(y_ + (cap / 2) * (1 - z) / H))
            ss.append(r4(z))
        return xs, ys, ss

    P.page([0, f_end], d["ground"], "#0E0D0C", grain=0.02)
    xs, ys, ss = track_card(PW / 2, PH / 2, 0, f_end)
    P.card([0, f_end], "plate", [0.5 - PW / W / 2, 0.5 - PH / H / 2, 0.5 + PW / W / 2, 0.5 + PH / H / 2],
           z=0, x=xs, y=ys, s=ss, plate=dict(c0=d["paper0"], c1=d["paper1"], grain=0.05), shadow=dict(a=0.5, blur=0.03, dx=0, dy=0.01))
    for li, t in enumerate(g["tlines"]):
        py_ = margin_top + g["th"] * (li + 1) - 0.2 * g["th"]
        xs, ys, ss = track_word("title", t, cols[0], py_, 0, f_end)
        P.word([0, f_end], t, "title", xs, ys, d["ink"], [r4(o) for o in seen("title", ys, ss)], s=ss, z=2)
    if kick:
        xs, ys, ss = track_word("kick", kick.upper(), cols[0], margin_top - 0.25 * g["th"], 0, f_end)
        P.word([0, f_end], kick.upper(), "kick", xs, ys, "#6E655A", [r4(o) for o in seen("kick", ys, ss)], s=ss, z=2)
    rule_y = margin_top + g["th"] * len(g["tlines"]) + px * 0.35
    xs, ys, ss = track_card(cols[0] + col_w / 2, rule_y, 0, f_end)
    P.card([0, f_end], "plate", [0.5 - col_w / W / 2, 0.5 - 0.0008, 0.5 + col_w / W / 2, 0.5 + 0.0008], z=2, x=xs, y=ys, s=ss,
           plate=dict(c0="#2A2622", c1="#2A2622", grain=0))
    for l in layout:
        w = words[l["k"]]
        on = Clock.f(w["t0"])
        xs, ys, ss = track_word(l["v"], w["w"], l["px"], l["py"], 0, f_end)
        vis = seen(l["v"], ys, ss)
        ops = [r4(vis[f] * (d["wait"] + (1 - d["wait"]) * min(1.0, max(0.0, (f - on + 1) / 3.0)))) for f in range(f_end)]
        P.word([0, f_end], w["w"], l["v"], xs, ys, d["ink"], ops, s=ss, z=3, note=False)
    for wn in windows:
        rec = wn["rec"]
        k_on = Clock.f(words[wn["k"]]["t1"])
        xs, ys, ss = track_card(wn["px0"] + wn["w"] / 2, wn["py0"] + wn["h"] / 2, 0, f_end)
        crop = cover_crop(rec["w"], rec["h"], wn["w"] / wn["h"], focus_of(rec)[0], (0.5, 0.45))
        op = [r4(0.16 + 0.84 * min(1.0, max(0.0, (f - k_on + 4) / 8.0))) for f in range(f_end)]
        P.card([0, f_end], keys[wn["name"]], [0.5 - wn["w"] / W / 2, 0.5 - wn["h"] / H / 2, 0.5 + wn["w"] / W / 2, 0.5 + wn["h"] / H / 2],
               z=2, x=xs, y=ys, s=ss, op=op, crop=push(crop, f_end, 0.04, (0.5, 0.45)),
               grade=dict(lift=0.02, gamma=1.0, gain=0.97, sat=0.9, contrast=1.04, temp=0.03, tint=0))
        P.pictures.append(dict(f=[k_on, f_end], kind="window", name=wn["name"], path=rec["path"]))
        P.events.append(k_on)
    # the desk above the page: windows and the page itself pass under it (words fade out on their own)
    P.block([0, f_end], [0.0, 0.0, 1.0, top], d["ground"], z=9)
    P.block([0, f_end], [0.0, top - 0.0012, 1.0, top], "#2C2925", z=9)
    film = dict(grain=0.03, vignette=0.22, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf3"), f_end, int(d.get("seed", 1113)), film)
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, n_end_voice], pull=[f_pull0, f_pull0 + n_pull], windows=[dict(name=w["name"], k=w["k"]) for w in windows],
                 body_px=px, columns=ncols, page=[r4(PW / W), r4(PH / H)], z_end=r4(z_end), events=sorted(set(P.events)), pictures=P.pictures,
                 on_screen=[title, kick] + [w["w"] for w in words], poster=True,
                 look=sorted({0, 20, *[Clock.f(words[w["k"]]["t1"]) + 12 for w in windows], f_pull0 - 30, f_pull0 + 45, f_end - 2}))
    return l1, beats


# ----------------------------------------------------------------- style: One Photograph

def microtext_image(photo_path, region, sentence, out_path, size=3000, pitch=26, font="InterDisplay-Black.ttf"):
    """X2: the region of the photograph redrawn as lines of the spoken sentence in tiny type, each letter's ink set by
    the photograph's tone under it, over a faint print of the same tones. From afar it is the face; close up it is the
    sentence. Returns (path, rows)."""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    T = _type()
    im = Image.open(photo_path).convert("L")
    pw, ph = im.size
    x0, y0, x1, y1 = [int(round(v)) for v in (region[0] * pw, region[1] * ph, region[2] * pw, region[3] * ph)]
    tone = np.asarray(im.crop((x0, y0, x1, y1)).resize((size, size), Image.LANCZOS), np.float32) / 255.0
    import cv2
    t8 = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(6, 6)).apply((tone * 255).astype(np.uint8))   # local contrast, so a pale face reads
    tone = cv2.GaussianBlur(t8, (0, 0), size / 400).astype(np.float32) / 255.0
    lo, hi = np.percentile(tone, 2), np.percentile(tone, 98)
    tone = np.clip((tone - lo) / max(hi - lo, 1e-3), 0, 1) ** 1.25
    ground = (255 - (1.0 - tone) * 255 * 0.42).astype(np.uint8)      # the faint print the letters sit on
    canvas = Image.fromarray(ground, "L")
    fnt = ImageFont.truetype(T.find_font(font), int(pitch * 0.9))
    dr = ImageDraw.Draw(canvas)
    unit = " ".join(sentence.split()) + "  \u00b7  "
    rows, pos = [], 0
    for y in range(pitch - 4, size, pitch):
        x = 4
        while x < size - 4:
            ch = unit[pos % len(unit)]
            cw = fnt.getlength(ch)
            if x + cw > size - 4:
                break
            if ch != " ":
                v = float(tone[min(max(y - pitch // 3, 0), size - 1), min(int(x + cw / 2), size - 1)])
                dr.text((x, y), ch, font=fnt, fill=int(round(8 + 235 * v ** 1.15)), anchor="ls")
            x += cw
            pos += 1
        rows.append(y)
    Image.merge("RGB", (canvas, canvas, canvas)).save(out_path, quality=92)
    return out_path, rows


def one_photograph(script, sup, direction=None):
    d = dict(face_zoom=0.32, whole_zoom=None, tick_db=-24.0, ring_c="#F2C14E", ground="#0C0B0A")
    d.update(direction or {})
    clock = Clock(sup["words"], script)
    photo = sup["photo"]
    pw, ph = photo["w"], photo["h"]
    aspect = pw / ph
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "shadow": {"a": 0.7}},
        "capd": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "glow": {"a": 0.35}},
        "name": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 8},
        "line": {"font": "DMSerifDisplay-Regular.ttf", "px": 96, "track": -1},
    }
    P = Plan(voices, sup["sounds"])
    key = P.image("photo", photo)
    faces = photo.get("faces") or []
    targets = dict(photo.get("targets") or {})          # place -> {"face": n} | {"box": [x0, y0, x1, y1]}
    for n, fi in (photo.get("who") or {}).items():       # an older pack: names to face indexes
        targets.setdefault(n, {"face": fi} if fi is not None else None)
    people = {p_.get("name") for p_ in (script.get("photo") or {}).get("people") or []}
    f_voice_end = Clock.f(clock.dur)
    n_close = 70
    f_close0 = f_voice_end - 30
    f_end = f_voice_end + n_close
    Hc = W / aspect                          # the photograph's height in frame pixels at zoom 1 (full width)
    s_fill = H / Hc                          # the least zoom at which the photograph fills the frame

    def face_center(i):
        b = faces[i]["box"]
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2 + (b[3] - b[1]) * 0.15)

    def face_zoom(i):
        """Zoom at which this face is about a third of the frame wide, held to 3x upscale."""
        b = faces[i]["box"]
        fw = (b[2] - b[0]) * W                  # face width at zoom 1, frame pixels
        s = d["face_zoom"] * W / max(fw, 1)
        s_max = MAX_UP * pw / W                 # zoom at which one photo pixel is 3 frame pixels
        return min(max(s, s_fill * 1.2), s_max)

    # "whole": the people of the photograph, all of them in frame. Their span decides the zoom: the photograph fills
    # the frame when they fit inside it, else it pulls back (letterboxed) until they do
    spans = [t["box"] for n_, t in targets.items() if t and "box" in t and n_ in people]
    spans += [f["box"] for f in faces]
    if spans:
        ux0, ux1 = min(b[0] for b in spans), max(b[2] for b in spans)
        uy0, uy1 = min(b[1] for b in spans), max(b[3] for b in spans)
        group = ((ux0 + ux1) / 2, (uy0 + uy1) / 2)
        s_hook = min(max(0.95 / max(ux1 - ux0, 1e-3), 1.0), s_fill * 1.02)
    else:
        group = (0.5, 0.5)
        s_hook = max(s_fill * 1.02, 1.0)

    def view(name):
        """(centre, zoom) for a place in the photograph, or None."""
        t = targets.get(name)
        if not t:
            return None
        if "face" in t and t["face"] < len(faces):
            return face_center(t["face"]), face_zoom(t["face"])
        if "box" in t:
            x0, y0, x1, y1 = t["box"]
            z = min(0.86 / max(x1 - x0, 1e-3), 0.55 * H / max((y1 - y0) * Hc, 1e-3))
            return ((x0 + x1) / 2, (y0 + y1) / 2), min(max(z, s_fill * 1.02), MAX_UP * pw / W)
        return None

    def anchor(name):
        """Where a person's mark sits: under the face, or at the foot of the region."""
        t = targets.get(name) or {}
        if "face" in t and t["face"] < len(faces):
            b = faces[t["face"]]["box"]
            return (b[0] + b[2]) / 2, b[3] + (b[3] - b[1]) * 0.28
        if "box" in t:
            b = t["box"]
            return (b[0] + b[2]) / 2, b[3] - (b[3] - b[1]) * 0.08
        return None

    # ---- the camera: a target per segment, eased moves of 18 frames, a slow drift while it holds
    cam_t = []
    for i, s in enumerate(script["segments"]):
        a = Clock.f(clock.segs[i][0]) if i else 0
        if i == 0:
            cam_t.append((0, group, s_hook))
            continue
        vw = view(s.get("look"))
        cam_t.append((a - 4, vw[0], vw[1]) if vw else (a - 4, group, s_hook * 1.08))
    cam = []
    j = 0
    prev = (group, s_hook)
    move_n = 18
    for f in range(f_close0):
        while j + 1 < len(cam_t) and cam_t[j + 1][0] <= f:
            j += 1
            prev = (cam[-1][0], cam[-1][1]) if cam else prev
        f0, (u, v), s = cam_t[j]
        q = ease_io(move_n)[min(max(f - f0, 0), move_n - 1)] if j else 1.0
        (pu, pv), ps = prev if j else ((u, v), s)
        # zoom moves in log space, so a dive feels even
        z = math.exp(math.log(ps) * (1 - q) + math.log(s) * q)
        drift = 1.0 + 0.012 * max(0, f - f0 - move_n) / FPS
        cam.append(((pu + (u - pu) * q, pv + (v - pv) * q), min(z * drift, MAX_UP * pw / W)))
    # the close: back to the whole photograph, full width, centred a little high
    c_last = cam[-1]
    for k in range(f_end - f_close0):
        q = ease_io(46)[min(k, 45)]
        (pu, pv), ps = c_last
        z = math.exp(math.log(ps) * (1 - q) + math.log(1.0) * q)
        cam.append(((pu + (0.5 - pu) * q, pv + (0.5 - pv) * q), z))

    def clamp(u, v, z):
        """Keep the frame inside the photograph while it covers it; centre it when it does not."""
        half_u = 0.5 / z
        half_v = 0.5 * H / (Hc * z)
        u = 0.5 if half_u >= 0.5 else min(max(u, half_u), 1 - half_u)
        v = 0.5 if half_v >= 0.5 else min(max(v, half_v), 1 - half_v)
        return u, v

    cams = []
    for (u, v), z in cam:
        u, v = clamp(u, v, z)
        cams.append((u, v, z))
    y_mid = 0.46                            # the frame's centre of interest

    def to_screen(u, v, f):
        cu, cv, z = cams[f]
        return 0.5 + (u - cu) * z, y_mid + (v - cv) * z * Hc / H

    def photo_track(a, b):
        xs, ys, ss = [], [], []
        for f in range(a, b):
            cu, cv, z = cams[f]
            xs.append(r4(0.5 + (0.5 - cu) * z))
            ys.append(r4(y_mid + (0.5 - cv) * z * Hc / H))
            ss.append(r4(z))
        return xs, ys, ss

    # the ink of each caption from the photograph's tone under it, measured through the camera (TASTE P4)
    import numpy as np
    from PIL import Image
    with Image.open(photo["path"]) as im_:
        g_ = np.asarray(im_.convert("L").resize((800, max(2, int(800 * ph / pw)))), np.float32) / 255.0

    def under(a, b, box):
        vals = []
        for f in range(a, b, max(1, (b - a) // 4)):
            cu, cv, z = cams[min(f, len(cams) - 1)]
            u0, u1 = cu + (box[0] - 0.5) / z, cu + (box[2] - 0.5) / z
            v0, v1 = cv + (box[1] - y_mid) * H / (Hc * z), cv + (box[3] - y_mid) * H / (Hc * z)
            x0_, x1_ = int(max(min(u0, 1), 0) * g_.shape[1]), int(max(min(u1, 1), 0) * g_.shape[1])
            y0_, y1_ = int(max(min(v0, 1), 0) * g_.shape[0]), int(max(min(v1, 1), 0) * g_.shape[0])
            if x1_ > x0_ and y1_ > y0_:
                vals.append(float(g_[y0_:y1_, x0_:x1_].mean()))
            else:
                vals.append(0.05)                       # off the photograph: the dark ground
        lum = sum(vals) / max(len(vals), 1)
        return ("capd", "#17120D") if lum > 0.52 else ("cap", "#FFFFFF")

    photo_box = [0.0, 0.5 - Hc / H / 2, 1.0, 0.5 + Hc / H / 2]
    P.page([0, f_end], d["ground"], "#060606", grain=0.02)
    # ---- T3: the photograph assembled by the voice, a few faces per spoken word, out of a blurred copy
    hook_ws = clock.seg_words(0)
    f_hook_end = Clock.f(clock.segs[0][1]) + 6
    xs, ys, ss = photo_track(0, f_end)
    blur_op = [1.0] * f_end
    P.card([0, f_end], key, photo_box, z=1, x=xs, y=ys, s=ss, blur=22, op=blur_op,
           grade=dict(lift=0, gamma=1.0, gain=0.62, sat=0.6, contrast=1, temp=0, tint=0))
    sharp_op = [r4(min(1.0, max(0.0, (f - f_hook_end) / 8.0))) for f in range(f_end)]
    P.card([0, f_end], key, photo_box, z=2, x=xs, y=ys, s=ss, op=sharp_op)
    # the pieces: its faces (with head and shoulders), nearest the middle first; a photograph whose people are in
    # profile has none, and then its own named regions land last, out of a grid of the rest
    if len(faces) >= 4:
        boxes = []
        for i in sorted(range(len(faces)), key=lambda i: (faces[i]["box"][0] - group[0]) ** 2 + (faces[i]["box"][1] - group[1]) ** 2):
            b = faces[i]["box"]
            boxes.append([b[0] - (b[2] - b[0]) * 0.35, b[1] - (b[3] - b[1]) * 0.45, b[2] + (b[2] - b[0]) * 0.35, b[3] + (b[3] - b[1]) * 0.55])
    else:
        named = []
        for t in targets.values():
            if t and "box" in t and (t["box"][2] - t["box"][0]) * (t["box"][3] - t["box"][1]) < 0.4 and t["box"] not in named:
                named.append(t["box"])
        grid = [[c / 4, r / 3, (c + 1) / 4, (r + 1) / 3] for r in range(3) for c in range(4)]
        boxes = grid + named
    order = list(range(len(boxes)))
    onsets = [Clock.f(w["t0"]) for w in hook_ws] or [LEAD_F]
    per = max(1, math.ceil(len(order) / max(len(onsets), 1)))
    pieces = 0
    for oi, fi in enumerate(order):
        on = onsets[min(oi // per, len(onsets) - 1)] + (oi % per)
        gx0, gy0, gx1, gy1 = boxes[fi]
        gx0, gy0, gx1, gy1 = max(gx0, 0), max(gy0, 0), min(gx1, 1), min(gy1, 1)
        end = f_hook_end + 8
        if on >= end:
            continue
        cxs, cys, css = [], [], []
        for f in range(on, end):
            sx, sy = to_screen((gx0 + gx1) / 2, (gy0 + gy1) / 2, f)
            cxs.append(r4(sx))
            cys.append(r4(sy))
            css.append(r4(cams[f][2]))
        wbox = [0.5 - (gx1 - gx0) / 2, 0.5 - (gy1 - gy0) * Hc / H / 2, 0.5 + (gx1 - gx0) / 2, 0.5 + (gy1 - gy0) * Hc / H / 2]
        P.card([on, end], key, wbox, z=3, x=cxs, y=cys, s=css, crop=[r4(gx0), r4(gy0), r4(gx1), r4(gy1)],
               grade=[None][0] or dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
        P.card([on, on + 1], key, wbox, z=4, x=cxs[:1], y=cys[:1], s=css[:1], crop=[r4(gx0), r4(gy0), r4(gx1), r4(gy1)],
               grade=dict(lift=0.04, gamma=0.85, gain=1.7, sat=0.4, contrast=1, temp=0, tint=0))
        pieces += 1
        if oi % per == 0:
            P.events.append(on)
    # sound: one photograph is a take (TASTE 4.2, class T): one sound on the first face that lands, then one per
    # mark while the allowance lasts (0.6 a 10 s, no bed)
    allow = max(1, int(0.6 * (f_end / FPS) / 10))
    if onsets:
        P.hit("tick_1", onsets[0], d["tick_db"])

    # ---- marks: a person whose part is told keeps a small ring from then on; brackets while the camera is on them
    marks = []
    for i, s in enumerate(script["segments"]):
        m = s.get("mark")
        if m and anchor(m) and m not in [x[0] for x in marks]:
            marks.append((m, anchor(m), Clock.f(clock.segs[i][0]) + 10))
    for name, (u, v), on in marks:
        xs_, ys_, ss_ = [], [], []
        for f in range(on, f_end):
            sx, sy = to_screen(u, v, f)
            xs_.append(r4(sx))
            ys_.append(r4(sy))
            z = cams[f][2]
            ss_.append(r4(min(max(z / 3.0, 0.55), 1.6) * (expo_out(7)[f - on] if f - on < 7 else 1.0)))
        P.block([on, f_end], [0.5 - 0.009, 0.5 - 0.009 * W / H, 0.5 + 0.009, 0.5 + 0.009 * W / H], d["ring_c"], z=5, x=xs_, y=ys_, s=ss_,
                radius=0.009, border=dict(w=0.0018, c="#1A1406"))
        P.events.append(on)
        if len(P.sounds) < allow:
            P.hit("click_2", on, d["tick_db"] - 2)
    # ---- names: the first time the camera arrives on a person, their name at the lower seat
    named_once = set()
    shown_names = []
    for i, s in enumerate(script["segments"]):
        look = s.get("look")
        if look and look in people and view(look) and look not in named_once:
            named_once.add(look)
            a = Clock.f(clock.segs[i][0]) + 12
            b = min(a + 54, Clock.f(clock.segs[i][1]) + 8)
            if b - a > 12:
                nw = P.R.width("name", str(look).upper()) / W
                _, nink = under(a, b, [0.5 - nw / 2 - 0.02, 0.715 - P.R.cap("name") / H - 0.01, 0.5 + nw / 2 + 0.02, 0.715 + 0.01])
                P.word([a, b], str(look).upper(), "name", 0.5, 0.715, "#F2E8D6" if nink == "#FFFFFF" else nink,
                       [r4(min(1, (k + 1) / 5)) for k in range(b - a)], align="c", z=6)
                shown_names.append((a, b, str(look)))
    # ---- X2: one face, whose tones are the sentence being spoken
    por = script.get("portrait") or {}
    x2 = None
    if por.get("name") in targets and sup.get("microtext"):
        mt = sup["microtext"]
        si = int(por.get("segment", 0))
        a, b = Clock.f(clock.segs[si][0]), Clock.f(clock.segs[si][1]) + 6
        if b - a > 40:
            P.assets["micro"] = dict(kind="image", path=mt["path"], w=mt["size"], h=mt["size"])
            reg = mt["region"]
            # the portrait fills the frame's width; the camera pushes from the whole face to the line by the eyes
            n = b - a
            row_y = mt["row_y"]
            ws = clock.seg_words(si)
            crops = []
            for k in range(n):
                q = ease_io(min(42, n))[min(k, min(42, n) - 1)]
                zoom = 1.0 + 3.2 * q
                cw = 1.0 / zoom
                chh = cw * H / W
                if chh > 1.0:
                    chh = 1.0
                    cw = chh * W / H
                # after the push, slide along the line at the rate of the words
                t = (a + k) / FPS
                done = sum(1 for w in ws if w["t0"] <= t) / max(len(ws), 1)
                slide = (max(0, k - 40) / max(n - 40, 1)) * 0.5 if k > 40 else 0.0
                slide = min(slide, done * 0.6)
                cx_ = min(max(0.5 * (1 - q) + (cw / 2 + 0.02 + slide) * q, cw / 2), 1 - cw / 2)
                cy_ = min(max(0.5 * (1 - q) + row_y * q, chh / 2), 1 - chh / 2)
                crops.append([r4(cx_ - cw / 2), r4(cy_ - chh / 2), r4(cx_ + cw / 2), r4(cy_ + chh / 2)])
            op = [r4(min(1.0, (k + 1) / 10.0) if k < n - 8 else (n - k) / 8.0) for k in range(n)]
            P.card([a, b], "micro", [0, 0, 1, 1], z=7, crop=crops, op=op)
            x2 = dict(f=[a, b], name=por["name"], segment=si)
            P.events.append(a)
    # ---- captions, one seat, low; off during the portrait
    skip = [tuple(x2["f"])] if x2 else []
    caption_track(P, clock, "cap", 0.775, f_end=f_close0 + 20, skip=skip, z=8, ink_for=under, yield_to=shown_names)
    # ---- the close: the whole photograph with every mark, and one line
    last = script["segments"][-1]
    line = next((t for t, _ in shown_items(last)), None)
    if line:
        voices["line"]["px"] = P.R.fit_px("line", line, 0.80 * W, 110)
        a = f_close0 + 40
        P.word([a, f_end], line, "line", 0.5, 0.5 + Hc / H / 2 + 0.085, "#F2E8D6", [r4(min(1, (k + 1) / 8)) for k in range(f_end - a)], align="c", z=8)
        P.events.append(a)
    film = dict(grain=0.045, vignette=0.3, over_type=False, grade=dict(lift=0.01, gamma=1, gain=1, sat=0.9, contrast=1.05, temp=0.02, tint=0))
    l1 = P.done(sup.get("sid", "wf3"), f_end, int(d.get("seed", 1113)), film)
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], close=[f_close0, f_end], pieces=pieces, marks=[m[0] for m in marks], x2=x2,
                 events=sorted(set(P.events)), pictures=[dict(kind="photo", name=photo.get("title"), path=photo["path"])], on_screen=P.shown,
                 look=sorted({0, 8, 30, f_hook_end - 4, f_hook_end + 20, *[t[0] + 30 for t in cam_t[1:]][:12],
                              *([x2["f"][0] + 30, x2["f"][1] - 12] if x2 else []), f_end - 2}))
    return l1, beats


# ----------------------------------------------------------------- style: Footage Cut (a spoken take, workflow 1)

def cut_ins(script, clock, pics, footage, f_first, f_last, stress, aim_s=1.0, lo_s=0.55, hi_s=1.5, share=0.6, gap_s=0.8):
    """Cutaways over a spoken take: [[f0, f1, kind, name, rec]] in film frames, each a clip of real footage or a
    photograph of something the sentence names, landing with the clause that says its name (else the sentence's own
    second clause), a new picture about every second, never more than `share` of a sentence, never before f_first
    (the hook is the speaker) or after f_last (the payoff is his), and never leaving the take on screen for less
    than gap_s between two of them."""
    lo, hi, aim = int(round(lo_s * FPS)), int(round(hi_s * FPS)), aim_s * FPS
    out, used, played = [], {}, set()
    nseg = len(script["segments"])
    for i, s in enumerate(script["segments"]):
        a = Clock.f(clock.segs[i][0]) if i else 0
        b = Clock.f(clock.segs[i + 1][0]) if i + 1 < nseg else Clock.f(clock.segs[i][1])
        a, b = max(a, f_first), min(b, f_last)
        if b - a < lo * 2:
            continue
        clips = sorted((c for c in (footage or {}).get(i) or [] if c["path"] not in played), key=too_close)
        names = [n for n in s.get("pictures") or [] if pics.get(n) or any(c.get("subject") == n for c in clips)]
        names += [c["subject"] for c in clips if c.get("subject") and c["subject"] not in names]
        names = list(dict.fromkeys(names))
        names.sort(key=lambda n: not any(c.get("subject") == n for c in clips))      # what was filmed comes before what was photographed
        if not names:
            continue
        bounds = [c for c in clause_starts(clock, i) if a + 6 <= c <= b - lo]
        budget = int(share * (b - a))
        at = a
        for j, n in enumerate(names):
            keys = {norm_word(w) for w in str(n).split() if len(norm_word(w)) >= 4}
            said = next((Clock.f(w["t0"]) - 1 for w in clock.seg_words(i) if norm_word(w["w"]) in keys and Clock.f(w["t0"]) - 1 >= at), None)
            if said is not None:
                cl = [c for c in bounds if at <= c <= said]
                start = max(cl) if cl and said - max(cl) <= FPS else max(said - 3, at)
            else:
                after = at + (int(gap_s * FPS) if out and out[-1][1] > at - 2 else 0)
                cl = [c for c in bounds if c >= after]
                # no clause to land on: after the sentence's first words, on the voice's strongest stress there
                near = [(st, -f) for f, st in stress if max(after, a + int(0.35 * FPS)) <= f <= a + int(1.1 * FPS)]
                start = cl[0] if cl else (-max(near)[1] if near else max(after, a + int(0.5 * FPS)))
            if start is None or budget < lo or b - start < lo:
                continue
            if out and 0 < start - out[-1][1] < gap_s * FPS:           # the take would flash by: the picture before holds on
                start = out[-1][1]
            mine = [c for c in clips if c.get("subject") == n]
            ph = [r for r in pics.get(n) or []]
            srcs = [("clip", c) for c in mine]
            k0 = used.get(n, 0)
            srcs += [("photo", ph[(k0 + q) % len(ph)]) for q in range(min(len(ph), 2))] if ph else []
            room = min(budget, b - start)
            want = max(1, min(len(srcs), int(round(room / aim))))
            srcs = srcs[:want]
            pos = start
            for q, (kind, rec) in enumerate(srcs):
                left = len(srcs) - q
                mx = int(float(rec["seconds"]) * FPS) if kind == "clip" else hi
                tgt = pos + min(room - (pos - start), aim * left) / left
                w0, w1 = pos + lo, min(pos + min(mx, hi), start + room)
                if w1 < w0:
                    break
                cands = [(st - abs(f - tgt) / FPS * 0.7, -f) for f, st in stress if w0 <= f <= w1]
                end = -max(cands)[1] if cands else int(round(min(max(tgt, w0), w1)))
                if kind == "clip":
                    nfr, dur = end - pos, float(rec["seconds"])
                    t0 = max((dur - nfr / FPS) / 2, 0.0)
                    rec = dict(rec, src=[round(t0, 3), round(min(t0 + nfr / FPS, dur), 3)])
                    played.add(rec["path"])
                else:
                    used[n] = used.get(n, 0) + 1
                out.append([pos, end, kind, n, rec])
                pos = end
            budget -= pos - start
            at = pos
    return out


def podcast_cut(script, sup, direction=None):
    """A podcast moment as a film of what is said: the speaker's take is the spine and its sound, and over the
    sentences that name real people, companies and places the picture cuts to real footage and photographs of them,
    a new one about every second, then back to him. Captions ink word by word at one seat; the title holds that
    seat over his first breath; each named thing is labelled once, small, when it first appears. The engines pace
    it (the take is eased, never punched), grade it and give its events their sounds; the payoff is his face."""
    d = dict(aim_s=1.0, look="podcast_low", family="podcast", share=0.6, bed=False, title=True)
    d.update(direction or {})
    clock = Clock(sup["words"], script)
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "shadow": {"a": 0.85, "blur": 0.006}},
        "dsp": {"font": "InterDisplay-Black.ttf", "px": 120, "track": -2, "shadow": {"a": 0.85, "blur": 0.010}},
        "lab": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},
    }
    P = Plan(voices, sup.get("sounds") or {})
    pal = dict(ink="#F4EFE6", accent=d.get("accent") or "#F2C14E", plate="#0A0A0A")
    drop = set(d.get("drop") or [])
    pics = {k: [r for r in v if r.get("title") not in drop and os.path.basename(str(r.get("path"))) not in drop] for k, v in (sup.get("pictures") or {}).items()}
    pics = {k: v for k, v in pics.items() if v}
    footage = {int(ln["i"]): [c for c in ln.get("clips") or [] if os.path.basename(str(c.get("file") or c.get("path"))) not in drop
                              and os.path.exists(str(c.get("path")))] for ln in sup.get("footage") or []}
    take = sup["take"]
    tk = P.video("main", dict(path=take["path"], w=take["w"], h=take["h"], fps=take.get("fps") or FPS, seconds=take["dur"]))
    keys, meta = {}, {}
    for lst in pics.values():
        for rec in lst:
            if rec["path"] not in keys:
                keys[rec["path"]] = P.image(f"p{len(keys):02d}", rec)
    for lst in footage.values():
        for c in lst:
            if c["path"] not in keys:
                keys[c["path"]] = P.video(f"v{len(keys):02d}", c)
    f_voice_end = Clock.f(clock.dur)
    f_end = min(f_voice_end + 8, int((float(take["dur"]) + LEAD_F / FPS) * FPS))      # out on his last word: the end card follows
    stress = voice_stress(sup)
    nseg = len(script["segments"])
    cap_y = 0.615
    # ---- the payoff is the last sentence: his face, and the film's turn lands on its first word
    turn_f = max(Clock.f(clock.segs[nseg - 1][0]) - 1, 0) if nseg > 1 else None
    f_last = turn_f - 6 if turn_f is not None else f_voice_end - 45
    ins = cut_ins(script, clock, pics, footage, int(1.2 * FPS), f_last, stress, aim_s=float(d["aim_s"]), share=float(d["share"]))
    cut_pts, drive_ids, look_pics = [], set(), []
    faces_take = face_meta(dict(faces=take.get("faces") or []))
    if faces_take:                                       # a speaker's head, not a face-shaped thing on the shelf behind him
        tall = max(f[3] for f in faces_take)
        faces_take = [f for f in faces_take if f[3] >= 0.5 * tall]
    meta[tk] = dict(pw=int(take["w"]), ph=int(take["h"]), faces=faces_take, max_up=float(d.get("take_up", 2.4)))
    if take.get("still"):
        look_pics.append(take["still"])

    def take_shot(a, b):
        if b - a < 2:
            return
        t0 = max(a - LEAD_F, 0) / FPS
        t1 = min(max(b - LEAD_F, 1) / FPS, float(take["dur"]))
        it = P.shot([a, b], tk, None, src=[round(t0, 4), round(max(t1, t0 + 1 / FPS), 4)])
        it.pop("crop")
        drive_ids.add(it["id"])
        cut_pts.append(a)
    pos, labelled, labels = 0, set(), []
    for f0, f1, kind, name, rec in ins:
        take_shot(pos, f0)
        key = keys[rec["path"]]
        if kind == "clip" and int(rec.get("w") or W) > int(rec.get("h") or H):
            # footage cut as it was shot, wide: it plays in a window the width of the phone, between the mark and the
            # caption seat, three by two round its subject, over itself out of focus and dim (an archive's small copy
            # is never blown up to fill the phone)
            n = f1 - f0
            pw_, ph_ = float(rec["w"]), float(rec["h"])
            cw, y0, y1 = 0.92, 0.205, 0.565
            asp = cw * W / ((y1 - y0) * H)                        # the window's shape
            rw = min(asp * ph_ / pw_, 1.0)                        # the share of the clip's width it shows
            fs_ = rec.get("faces") or []
            cx = 0.5
            if fs_:
                big = max(fs_, key=lambda q: (q["box"][2] - q["box"][0]) * (q["box"][3] - q["box"][1]))
                cx = (big["box"][0] + big["box"][2]) / 2
            x0 = min(max(cx - rw / 2, 0.0), 1.0 - rw)
            P.shot([f0, f1], key, cover_crop(pw_, ph_, W / H, (cx, 0.45), (0.5, 0.40)), src=list(rec["src"]), blur=26,
                   grade=dict(lift=0, gamma=1.0, gain=0.46, sat=0.8, contrast=1, temp=0, tint=0))
            sc_ = [r4(1.035 - 0.035 * smooth(q / 7.0)) if q < 8 else r4(1.0 + 0.04 * (q - 8) / max(n - 9, 1)) for q in range(n)]
            P.card([f0, f1], key, [0.5 - cw / 2, y0, 0.5 + cw / 2, y1], z=1, s=sc_, src=list(rec["src"]), crop=[r4(x0), 0.0, r4(x0 + rw), 1.0],
                   radius=0.010, op=[min(1.0, 0.5 + 0.25 * q) for q in range(n)], shadow=dict(a=0.55, blur=0.03, dx=0, dy=0.012))
            if rec.get("still"):
                look_pics.append(rec["still"])
            P.pictures.append(dict(f=[f0, f1], kind="clip", name=name, path=rec["path"], shows=rec.get("shows"), mode="window",
                                   source=(rec.get("source") or {}).get("url"), time_range=rec.get("time_range")))
        elif kind == "clip":
            it = P.shot([f0, f1], key, None, src=list(rec["src"]))
            it.pop("crop")
            drive_ids.add(it["id"])
            fm = face_meta(rec)
            meta[key] = dict(pw=int(rec.get("w") or W), ph=int(rec.get("h") or H), faces=fm, max_up=1.0 if any(f[3] >= 0.24 for f in fm) else 1.12)
            if rec.get("still"):
                look_pics.append(rec["still"])
            P.pictures.append(dict(f=[f0, f1], kind="clip", name=name, path=rec["path"], shows=rec.get("shows"),
                                   source=(rec.get("source") or {}).get("url"), time_range=rec.get("time_range")))
        else:
            (fx, fy), _ = focus_of(rec)
            it = P.shot([f0, f1], key, cover_crop(rec["w"], rec["h"], W / H, (fx, fy), (0.5, 0.40)))
            drive_ids.add(it["id"])
            if key not in meta:
                m = dict(pw=int(rec["w"]), ph=int(rec["h"]), faces=[f for f in face_meta(rec) if f[3] * rec["h"] >= 60], max_up=MAX_UP)
                if not m["faces"]:
                    try:
                        import numpy as np
                        from PIL import Image
                        pace, _, _ = _engines()
                        with Image.open(rec["path"]) as im_:
                            m["regions"] = pace.regions_of(np.asarray(im_.convert("L")), n=3, size=0.5)
                    except Exception:  # noqa: BLE001 - no regions: the picture's own thirds
                        pass
                meta[key] = m
            look_pics.append(rec["path"])
            P.pictures.append(dict(f=[f0, f1], kind="photo", name=name, path=rec["path"], mode="bleed"))
        cut_pts.append(f0)
        P.events.append(f0)
        if name not in labelled:                         # a named thing is labelled once, small, when it first appears
            labelled.add(name)
            run_end = next((x[0] for x in ins if x[0] >= f1 and x[3] != name), None)
            b_lab = max([x[1] for x in ins if x[3] == name and x[0] >= f0 and (run_end is None or x[1] <= run_end)] or [f1])
            labels.append((f0 + 2, min(b_lab, f0 + int(2.4 * FPS)), str(name)))
        pos = f1
    take_shot(pos, f_end)
    # ---- the title holds the caption seat over his first breath; the captions yield to it
    shown = []
    title = str(script.get("title") or "")
    if d.get("title") and title:
        ws = title.split()
        cut = min(range(1, len(ws)), key=lambda k: abs(len(" ".join(ws[:k])) - len(" ".join(ws[k:])))) if len(title) > 16 and len(ws) > 1 else len(ws)
        rows = [" ".join(ws[:cut])] + ([" ".join(ws[cut:])] if cut < len(ws) else [])
        _, _, bake = _engines()
        a, b = 2, int(2.3 * FPS)
        px = min([P.R.fit_px("dsp", r, 0.70 * W, 132) for r in rows])      # inside the platform's right rail, as every display word
        for k, row in enumerate(rows):
            vk = f"dsp_t{k}"
            P.voices[vk] = dict(P.voices["dsp"], px=px)
            y = cap_y + 0.012 - (len(rows) - 1 - k) * px * 1.04 / H
            n = b - a
            kk = min(int(bake.BOUNCE["n"]), n)
            ys = [r4(y + float(q)) for q in bake.bounce(kk, rest=0.0)] + [r4(y)] * (n - kk)
            bl = [round(float(q), 3) for q in bake.bounce_blur(kk)] + [0.0] * (n - kk)
            P.word([a + 2 * k, b], row, vk, 0.5, ys[2 * k:] if k else ys, pal["accent"] if k == len(rows) - 1 else pal["ink"],
                   [min(1.0, (q + 1) / 2) for q in range(n - 2 * k)], align="c", z=3, blur=bl[2 * k:] if k else bl, role="big", stroke=dict(w=3.0, c="#0B0A09"))
        shown.append((a, b, title))
        P.events.append(a)
    for a, b, text in labels:
        if b - a < 14:
            continue
        t = text.upper()
        wd = P.R.width("lab", t) / W
        if wd > 0.8:
            continue
        y = 0.752
        P.block([a, b], [0.5 - wd / 2 - 0.022, y - 0.030, 0.5 + wd / 2 + 0.022, y + 0.013], pal["plate"], z=4, op=0.78, radius=0.008)
        P.word([a, b], t, "lab", 0.5, y, pal["ink"], 0.95, align="c", z=5, role="stat")
    lines = caption_build(P, clock, "cap", cap_y, f_end=f_voice_end + 8, hold=[(a - 1, b) for a, b, _ in shown])
    P.flash([0, f_end], mode="pool", a=float(d.get("pool", 0.42)), rect=[0.06, cap_y - 0.105, 0.94, cap_y + 0.075], z=-1)
    film = dict(grain=0.012, vignette=0.34, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf1"), f_end, int(d.get("seed", 1113)), film)
    l1 = engines_finish(l1, sup, stress, drive_ids, meta, dict(d, look_pictures=look_pics), turn_f=turn_f,
                        spoken=[(w["t0"] - LEAD_F / FPS, w["t1"] - LEAD_F / FPS) for w in clock.words])
    changes = sorted({it["f"][0] for it in l1["items"] if it["k"] == "shot"})
    for s_ in l1.get("sound") or []:
        P.events.append(int(s_["event_f"]))
    cover = sum(x[1] - x[0] for x in ins) / max(f_voice_end, 1)
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], cuts=sorted(set(cut_pts)), changes=changes, events=sorted(set(P.events) | set(cut_pts) | set(changes)),
                 pictures=P.pictures, on_screen=P.shown, caption_y=cap_y, caption_lines=lines, display=[dict(f=[a, b], text=t) for a, b, t in shown],
                 labels=[dict(f=[a, b], text=t) for a, b, t in labels], turn_f=turn_f, pace=l1.get("pace"), cutaway_share=round(cover, 3),
                 sound_design={k: v for k, v in (l1.get("sound_design") or {}).items() if k != "dropped"},
                 look=sorted({0, 12, 40, *[x[0] + 5 for x in ins], *[x[1] + 8 for x in ins[::2]], *([turn_f + 10] if turn_f is not None else []), f_end - 3}))
    return l1, beats


STYLES = {"character-sheet": character_sheet, "whole-page": whole_page, "one-photograph": one_photograph, "footage-cut": podcast_cut}


# ----------------------------------------------------------------- the film's sound

def audio_master(ffmpeg, l1, narration_wav, out, tmp, lead_f=LEAD_F):
    """The film's sound as one file: the narration from frame lead_f and each placed sound with its peak on its event,
    mixed and mastered by the engine's own mixer (scene.audio: the voice levelled, the sounds on it at the gains the
    sound design set against that voice, -14 LUFS, true peak under -1 dBTP). No music here: a song is laid by
    score.music on a copy, and this file stays the clean export."""
    w = scene_worker()
    if w not in sys.path:
        sys.path.insert(0, w)
    from scene import audio as AU
    AU.FF = ffmpeg
    dur = l1["dur_f"] / FPS
    n_voice = int(math.ceil(float(subprocess.run([os.path.join(os.path.dirname(ffmpeg), "ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                                  "-of", "csv=p=0", narration_wav], capture_output=True, text=True).stdout.strip() or 0) * FPS))
    end = min(int(l1["dur_f"]), lead_f + n_voice)

    class _P:
        doc = dict(spine=[dict(f=[lead_f, end], src=[0.0, (end - lead_f) / FPS])], sound=l1.get("sound") or [])
        fps, assets = float(FPS), {"main": {"abs": narration_wav}}
    _P.dur = int(l1["dur_f"])
    voice, fx, rep = AU.split_audio(_P)
    # the shell's brand finish encodes this sound once more when it joins the end card, and a second AAC pass overshoots
    # by up to a decibel (a film of hits measured 0.0 dBTP delivered): the master here stops at -2 dBTP so the film ends under -1
    ceil, limit = AU.CEIL_DBTP, AU.LIMIT_DBFS
    AU.CEIL_DBTP, AU.LIMIT_DBFS = min(ceil, -2.0), min(limit, -2.6)
    try:
        m = AU.master(voice, fx, out, tmp, dur)
    finally:
        AU.CEIL_DBTP, AU.LIMIT_DBFS = ceil, limit
    return dict(sounds=rep.get("sounds"), skipped=rep.get("skipped"), lufs_in=m.get("lufs_in"), lufs=m.get("lufs"), tp=m.get("tp"))


def voice_measures(wav, words, ffmpeg):
    """C1's signals, per spoken word: loudness (dB RMS) and pitch (semitones from the read's median), from the
    narration itself. Signal processing only."""
    import numpy as np
    sr = 16000
    r = subprocess.run([ffmpeg, "-v", "error", "-i", wav, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.float32)
    loud, f0 = {}, {}
    pitches = []
    for i, w in enumerate(words):
        a, b = int(w["start"] * sr), int(w["end"] * sr)
        seg = x[a:b]
        if len(seg) < 400:
            continue
        loud[str(i)] = float(10 * np.log10(np.mean(seg ** 2) + 1e-10))
        # autocorrelation pitch on the word's loudest 60 ms
        n = int(0.06 * sr)
        if len(seg) > n:
            e = np.convolve(seg ** 2, np.ones(n), "valid")
            k = int(e.argmax())
            fr = seg[k:k + n] - seg[k:k + n].mean()
            ac = np.correlate(fr, fr, "full")[n - 1:]
            lo, hi = int(sr / 400), int(sr / 70)
            if hi < len(ac) and ac[0] > 0:
                lag = lo + int(ac[lo:hi].argmax())
                if ac[lag] / ac[0] > 0.3:
                    f0[str(i)] = sr / lag
                    pitches.append(sr / lag)
    if pitches:
        med = float(np.median(pitches))
        f0 = {k: float(12 * np.log2(v / med)) for k, v in f0.items()}
    return loud, f0


# ----------------------------------------------------------------- measured: taste on the plan

def _rest(v):
    return v[-1] if isinstance(v, list) else v


def taste_check(l1, beats):
    """TASTE.md on the plan, as numbers: added type inside the safe area at rest and off the mark, at or above the
    reading floor, at most three type systems at once, every sound on an event, sounds within the supply's allowance."""
    R = Ruler(l1["voices"])
    bad = []
    poster = bool(beats.get("poster"))
    words = [i for i in l1["items"] if i["k"] == "word"]
    for it in words:
        v = l1["voices"][it["v"]]
        if poster:                          # the page moves under the camera: its words are set in page space, checked by the poster rule
            continue
        if it["t"].strip() in (".",):
            continue
        x, y = _rest(it["x"]), _rest(it["y"])
        s = _rest(it.get("s", 1)) or 1
        x1, y0 = x + R.width(it["v"], it["t"]) * s / W, y - R.cap(it["v"]) * s / H
        if it["f"][1] - it["f"][0] <= 1:
            continue
        if x < SAFE[0] - 2e-3 or x1 > SAFE[2] + 2e-3 or y0 < SAFE[1] - 2e-3 or y > SAFE[3] + 2e-3:
            bad.append(f"{it['id']} '{it['t'][:20]}': outside the safe area ({x:.3f},{y0:.3f})-({x1:.3f},{y:.3f})")
        if y > 0.55 and x1 > RAIL_X + 2e-3:
            bad.append(f"{it['id']} '{it['t'][:20]}': under the platform's right rail (x1 {x1:.3f})")
        if x < MARK[2] and x1 > MARK[0] and y0 < MARK[3] and y > MARK[1]:
            bad.append(f"{it['id']} '{it['t'][:20]}': in the watermark's keep-out")
        if float(v["px"]) * s < 38 - 1e-6:
            bad.append(f"{it['id']} '{it['t'][:20]}': {float(v['px']) * s:.0f} px is under the reading floor")
    fams = {}
    for f in range(0, l1["dur_f"], 5):
        fs = {l1["voices"][i["v"]]["font"].split("-")[0] for i in words if i["f"][0] <= f < i["f"][1]}
        if len(fs) > 3:
            bad.append(f"frame {f}: {len(fs)} type families at once")
            break
        fams[f] = len(fs)
    events = set(beats.get("events") or [])
    for s in l1.get("sound") or []:
        if s.get("layer") in ("lead", "riser"):          # the climb into a hit belongs to the hit
            continue
        if not events or min(abs(s["event_f"] - e) for e in events) > 3:
            bad.append(f"sound {s['id']} at frame {s['event_f']} has nothing landing with it")
    secs = l1["dur_f"] / FPS
    hits = sorted({int(s["event_f"]) for s in l1.get("sound") or []})
    gest = [f for i, f in enumerate(hits) if i == 0 or f - hits[i - 1] > FPS]      # sounds within a second are one gesture
    n_s = len(gest)
    np10 = len([p for p in beats.get("pictures") or [] if p.get("kind") in ("photo", "designed", "window", "clip")]) / secs * 10
    # the references' placed layer: 3.6 audible sounds per 10 s on 16 pictures per 10 s (judge teardown); the engine holds 3
    allow = max(1, int(min(0.9 * np10, 3.6) * secs / 10 + 1)) if np10 >= 5 else max(1, int((1.5 if np10 >= 1 else 0.6) * secs / 10))
    eng = (l1.get("sound_design") or {}).get("allow")       # the sound engine's own governor counted the film's picture changes
    if eng:                                                 # (a take cut to a picture and back is two): its allowance stands
        allow = max(allow, int(eng))
    if n_s > allow:
        bad.append(f"{n_s} sounds where the supply ({np10:.1f} new pictures a 10 s) allows {allow}")
    return dict(ok=not bad, problems=bad[:20], words=len(words), sounds=n_s, sounds_allowed=allow, new_pictures_10s=round(np10, 2),
                sounds_per_10s=round(n_s / secs * 10, 2))
