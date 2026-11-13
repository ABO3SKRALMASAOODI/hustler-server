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
        self.nseg = len(script["segments"])
        self.segs = []
        for i in range(self.nseg):
            ws = [w for w in self.words if w["seg"] == i]
            if ws:
                self.segs.append((ws[0]["t0"], ws[-1]["t1"]))
            else:
                prev = self.segs[-1][1] if self.segs else LEAD_F / FPS
                self.segs.append((prev, prev + 0.4))

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

    def word(self, f, t, v, x, y, ink="#FFFFFF", op=1.0, align="l", note=True, **kw):
        if note:
            self.shown.append(t)
        if align != "l" and not isinstance(x, list):
            wd = self.R.width(v, t) / W
            x = x - wd if align == "r" else x - wd / 2
        it = dict(id=self.nid("w"), k="word", f=[int(f[0]), int(f[1])], t=t, v=v, x=r4l(x), y=r4l(y), ink=ink, op=r4l(op),
                  role="big", on=int(f[0]))
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
    if bleed_ok(rec):
        P.shot(f, key, push(crop, n, amount, (fx, fy)), grade=grade)
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
    P.card(f, key, box, z=z, s=s, radius=0.006, shadow=dict(a=0.45, blur=0.03, dx=0, dy=0.012), grade=grade)
    return dict(mode="bed", upscale=round(cw * W / pw, 2), box=box)


def caption_track(P, clock, v, y, ink="#FFFFFF", f_end=None, skip=(), z=0, ink_for=None):
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


def assign_pictures(script, clock, pics, f_end, min_s=1.15, max_s=2.6):
    """Shots in programme frames from the segments' named pictures: [(f0, f1, name, rec)]. A subject's photographs
    rotate; a segment that names nothing keeps the last subject with its next photograph."""
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
        k = max(1, min(len(names) * 2, int(span / min_s)))
        k = max(k, int(math.ceil(span / max_s)))
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
    d = dict(stat_db=-26.0, cut_db=-17.0, low_blink=12, pal="night")
    d.update(direction or {})
    clock = Clock(sup["words"], script)
    voices = {
        "cap": {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "shadow": {"a": 0.65}},
        "lab": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},
        "val": {"font": "InterDisplay-Black.ttf", "px": 44, "track": 0},
        "big": {"font": "DMSerifDisplay-Regular.ttf", "px": 260, "track": -2},
        "sub": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 8},
        "ovf": {"font": "InterDisplay-Black.ttf", "px": 220, "track": -2},
    }
    P = Plan(voices, sup["sounds"])
    pal = dict(page0="#14110F", page1="#0B0A09", ink="#F4EFE6", ink_soft="#BDB4A6", accent="#F2C14E", red="#E5322D", plate="#0A0A0A")
    drop = set(d.get("drop") or [])            # the director's hand: photographs (by title) the film must not use
    pics = {k: [r for r in v if r.get("title") not in drop] for k, v in sup["pictures"].items()}
    pics = {k: v for k, v in pics.items() if v}
    keys = {}
    for name, lst in pics.items():
        for j, rec in enumerate(lst):
            keys[rec["path"]] = P.image(f"p{len(keys):02d}", rec)
    f_voice_end = Clock.f(clock.dur)
    ovf = script.get("overflow") or {}
    f_ovf = Clock.f(clock.word_t(len(script["segments"]) - 1, ovf.get("word"))) if ovf else f_voice_end
    f_ovf = min(max(f_ovf, f_voice_end - 75), f_voice_end - 20)
    f_end = max(f_voice_end + 36, f_ovf + 66)
    grade = dict(lift=0.0, gamma=1.0, gain=0.92, sat=0.86, contrast=1.06, temp=-0.02, tint=0)

    # ---- pictures, with designed pages on the figures the writer printed
    shots = assign_pictures(script, clock, pics, f_ovf)
    # a designed page only for a figure or a date the block does not already print (TASTE P7: say it once), and at
    # most one every other segment, so the photographs carry the film
    printed = [str(st.get("start")) for st in script.get("stats") or []] + [str(ovf.get("to"))] + \
              [str(t.get("to")) for s_ in script["segments"] for t in s_.get("ticks") or []]
    printed = [x for x in printed if x and x != "None"]
    pages, last_i = [], -2
    for i, s in enumerate(script["segments"]):
        if i == 0 or i - last_i < 2:
            continue
        for text, word in shown_items(s):
            if not any(ch.isdigit() for ch in text) or any(x in text or text in x for x in printed):
                continue
            t = clock.word_t(i, word) if word else clock.segs[i][0] + 0.15
            a = max(Clock.f(t) - 2, 1)
            pages.append([a, min(a + 34, Clock.f(clock.segs[i][1]) + 6), text])
            last_i = i
            break
    cut_pts = []
    timeline = []
    for f0, f1, name, rec in shots:
        segs = [(f0, f1)]
        for a, b, _ in pages:
            nxt = []
            for x0, x1 in segs:
                if b <= x0 or a >= x1:
                    nxt.append((x0, x1))
                    continue
                if a - x0 >= 12:
                    nxt.append((x0, a))
                if x1 - b >= 12:
                    nxt.append((b, x1))
            segs = nxt
        for x0, x1 in segs:
            timeline.append([x0, x1, name, rec])
    for a, b, text in pages:
        timeline.append([a, b, None, text])
    timeline.sort(key=lambda r: r[0])
    # close any gap: a picture holds until the next thing starts
    for j in range(len(timeline) - 1):
        timeline[j][1] = timeline[j + 1][0]
    if timeline:
        timeline[0][0] = 0
        timeline[-1][1] = f_ovf
    # ---- T4: chosen sentence endings are cuts through their own full stop (at most one in three)
    phrases = clock.phrases()
    cap_y = 0.615
    t4 = []
    n_grow = 9
    for i in range(len(script["segments"]) - 1):
        if len(t4) >= max(1, len(script["segments"]) // 3) or i % 3 != 1:
            continue
        end_f = Clock.f(clock.segs[i][1])
        j = next((j for j, r in enumerate(timeline) if end_f - 2 <= r[0] <= end_f + 30 and r[2] and bleed_ok(r[3])), None)
        ph = next((p for p in phrases if abs(Clock.f(p[1]) - end_f) <= 18 and p[3][-1]["seg"] == i), None)
        if j is None or not ph or j == 0:
            continue
        timeline[j - 1][1] = end_f + n_grow          # the old picture holds while the stop grows
        timeline[j][0] = end_f + n_grow
        t4.append((i, end_f, timeline[j], ph))
    framing = []
    for f0, f1, name, rec in timeline:
        if f1 - f0 < 2:
            continue
        if name is None:
            designed_page(P, [f0, f1], rec, None, pal, "big", "sub")
            P.pictures.append(dict(f=[f0, f1], kind="designed", text=rec))
            P.events.append(f0)
        else:
            fr = still(P, [f0, f1], keys[rec["path"]], rec, grade=grade)
            P.pictures.append(dict(f=[f0, f1], kind="photo", name=name, path=rec["path"], **fr))
            framing.append(fr)
        cut_pts.append(f0)
    # the close: the last picture held, darkened, under the overflowing number
    last_rec = next((r for _, _, n, r in reversed(timeline) if n), None)
    if last_rec:
        P.shot([f_ovf, f_end], keys[last_rec["path"]], cover_crop(last_rec["w"], last_rec["h"], W / H, focus_of(last_rec)[0]),
               blur=18, grade=dict(lift=0, gamma=1.0, gain=0.30, sat=0.5, contrast=1, temp=0, tint=0))
    else:
        P.page([f_ovf, f_end], pal["page0"], pal["page1"])
    skip_caps = []
    for i, end_f, nxt, ph in t4:
        text = ph[2].rstrip(".,;:!?")
        x_left = 0.5 - P.R.width("cap", text + ".") / W / 2
        a = Clock.f(ph[0]) - 1
        dot_x = x_left + P.R.width("cap", text) / W
        b = end_f + n_grow
        P.word([a, b], text, "cap", x_left, cap_y, "#FFFFFF", [1.0] * (end_f + 2 - a) + [0.0] * (b - end_f - 2), note=False, z=2)
        rec = nxt[3]
        crop0 = cover_crop(rec["w"], rec["h"], W / H, focus_of(rec)[0], (0.5, 0.40))
        P.word([a, end_f], ".", "cap", dot_x, cap_y, "#FFFFFF", 1.0, note=False, z=2)
        P.word([end_f, b], ".", "cap", dot_x, cap_y, "#FFFFFF", 1.0, note=False, z=3, s=[r4(1.9 ** k) for k in range(b - end_f)],
               fill=dict(pic=keys[rec["path"]], crop=crop0, frame=True))
        skip_caps.append((a, b))
        P.hit("pop_1", end_f, d["cut_db"])
        P.events.append(end_f)
    # the ink of each caption from the photograph's tone under it (TASTE P4): a full-bleed photograph is read through
    # its crop; a page and a blurred bed are dark, and keep white
    import numpy as np
    from PIL import Image
    grays = {}

    def gray(path):
        if path not in grays:
            with Image.open(path) as im_:
                grays[path] = np.asarray(im_.convert("L").resize((480, max(2, int(480 * im_.size[1] / im_.size[0])))), np.float32) / 255.0
        return grays[path]

    bleeds = [(p_["f"][0], p_["f"][1], p_["path"]) for p_ in P.pictures if p_.get("kind") == "photo" and p_.get("mode") == "bleed"]
    recs = {r["path"]: r for v in pics.values() for r in v}

    def under(a, b, box):
        m = (a + b) // 2
        hit = next((x for x in bleeds if x[0] <= m < x[1]), None)
        if not hit:
            return "cap", "#FFFFFF"
        rec = recs[hit[2]]
        cr = cover_crop(rec["w"], rec["h"], W / H, focus_of(rec)[0], (0.5, 0.40))
        g_ = gray(hit[2])
        x0_ = int((cr[0] + box[0] * (cr[2] - cr[0])) * g_.shape[1])
        x1_ = int((cr[0] + box[2] * (cr[2] - cr[0])) * g_.shape[1])
        y0_ = int((cr[1] + box[1] * (cr[3] - cr[1])) * g_.shape[0])
        y1_ = int((cr[1] + box[3] * (cr[3] - cr[1])) * g_.shape[0])
        lum = float(g_[y0_:max(y1_, y0_ + 1), x0_:max(x1_, x0_ + 1)].mean()) * grade["gain"]
        return ("capd", "#17120D") if lum > 0.55 else ("cap", "#FFFFFF")

    voices["capd"] = {"font": "InterDisplay-Bold.ttf", "px": 56, "track": 0, "glow": {"a": 0.35}}
    caption_track(P, clock, "cap", cap_y, f_end=f_ovf, skip=skip_caps, ink_for=under)

    # ---- G4: the stat block
    stats = script.get("stats") or []
    bx0, by0 = 0.05, 0.105
    row_h, pad = 0.040, 0.016
    bw = 0.42
    bh = pad * 2 + row_h * len(stats)
    box = [bx0, by0, bx0 + bw, by0 + bh]
    P.block([0, f_ovf + 10], box, pal["plate"], z=4, op=[r4(0.72 * min(1.0, (f_ovf + 10 - k) / 10.0)) if k > f_ovf else 0.72 for k in range(f_ovf + 10)],
            radius=0.010, border=dict(w=0.0012, c="#3A3530"))
    ticks = []
    for i, s in enumerate(script["segments"]):
        for t in s.get("ticks") or []:
            ticks.append(dict(t, f=Clock.f(clock.word_t(i, t.get("word"))), seg=i, beat=s.get("beat")))
    ticks.sort(key=lambda t: t["f"])
    low_seg = next((i for i, s in enumerate(script["segments"]) if s.get("beat") == "low"), None)
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
        P.word([0, f_ovf + 4], str(st["label"]).upper(), "lab", lab_x, y_base, pal["ink_soft"], 0.9, z=5)
        mine = [t for t in ticks if t.get("stat") == st["id"] and t["f"] < f_ovf]
        vals = [(0, str(st["start"]), float(st.get("number") or 0))] + [(t["f"], str(t["to"]), float(t.get("number") or 0)) for t in mine]
        mx = max([abs(float(st.get("max") or 0))] + [abs(v[2]) for v in vals] + [1e-9])
        right = bx0 + bw - 0.022
        bar_y0 = by0 + pad + row_h * r + row_h * 0.80
        bar_x0, bar_x1 = lab_x, right
        P.block([0, f_ovf + 4], [bar_x0, bar_y0, bar_x1, bar_y0 + 0.0042], "#2A2724", z=5)
        for j, (f0, text, num) in enumerate(vals):
            f1 = vals[j + 1][0] if j + 1 < len(vals) else f_ovf + 4
            is_ovf = ovf and ovf.get("stat") == st["id"] and j + 1 == len(vals)
            if f1 - f0 < 1:
                continue
            prev = vals[j - 1] if j else None
            n_tick = 7 if prev else 0
            # the number ticks from the old value to the new over 7 frames, in accent, then settles back to ink
            for k in range(min(n_tick, f1 - f0)):
                tx = tick_text(prev[1], text, expo_out(n_tick)[k])
                P.word([f0 + k, f0 + k + 1], tx, "val", right, y_base + 0.002, pal["accent"], 1.0, align="r", note=False, z=6)
            hold_a = f0 + min(n_tick, f1 - f0)
            if f1 > hold_a:
                n = f1 - hold_a
                inks = pal["accent"] if prev else pal["ink"]
                P.word([hold_a, f1], text, "val", right, y_base + 0.002, inks, 1.0, align="r", z=6)
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
                P.hit("tick_1", f0, d["stat_db"])
        stat_rows.append(dict(id=st["id"], y=r4(y_base), right=r4(right), values=[v[1] for v in vals]))
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
        P.word([a, a + n], text, "ovf", xs, ys, pal["accent"], 1.0, s=ss, z=7)
        P.word([a + n, f_end], text, "ovf", 0.5 - w_big / 2, 0.50, pal["accent"], 1.0, z=7)
        lab = next((st["label"] for st in stats if st["id"] == ovf.get("stat")), "")
        P.word([a + n + 4, f_end], str(lab).upper(), "sub", 0.5, 0.50 + 0.055, pal["ink_soft"], 0.9, align="c", z=7)
        P.hit("impact_1", a + n - 1, -14.0)
        P.events.append(a + n)
    film = dict(grain=0.035, vignette=0.28, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0))
    l1 = P.done(sup.get("sid", "wf3"), f_end, int(d.get("seed", 1113)), film)
    beats = dict(dur_f=f_end, voice_f=[LEAD_F, f_voice_end], overflow_f=f_ovf, cuts=sorted(set(cut_pts)), t4=[x[1] for x in t4],
                 events=sorted(set(P.events) | set(cut_pts)), pictures=P.pictures, stats=stat_rows, on_screen=P.shown, caption_y=cap_y,
                 look=sorted({0, 12, *[c + 4 for c in cut_pts[::3]], *[x[1] + 4 for x in t4], f_ovf + 25, f_end - 2,
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

    group = None
    if faces:
        xs_ = [f["box"][0] for f in faces] + [f["box"][2] for f in faces]
        ys_ = [f["box"][1] for f in faces] + [f["box"][3] for f in faces]
        group = ((min(xs_) + max(xs_)) / 2, (min(ys_) + max(ys_)) / 2)
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
    order = sorted(range(len(faces)), key=lambda i: (faces[i]["box"][0] - group[0]) ** 2 + (faces[i]["box"][1] - group[1]) ** 2)
    onsets = [Clock.f(w["t0"]) for w in hook_ws] or [LEAD_F]
    per = max(1, math.ceil(len(order) / max(len(onsets), 1)))
    pieces = 0
    for oi, fi in enumerate(order):
        on = onsets[min(oi // per, len(onsets) - 1)] + (oi % per)
        b = faces[fi]["box"]
        gx0, gy0 = b[0] - (b[2] - b[0]) * 0.35, b[1] - (b[3] - b[1]) * 0.45
        gx1, gy1 = b[2] + (b[2] - b[0]) * 0.35, b[3] + (b[3] - b[1]) * 0.55
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
    caption_track(P, clock, "cap", 0.775, f_end=f_close0 + 20, skip=skip, z=8, ink_for=under)
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


STYLES = {"character-sheet": character_sheet, "whole-page": whole_page, "one-photograph": one_photograph}


# ----------------------------------------------------------------- the film's sound

def audio_master(ffmpeg, l1, narration_wav, out, tmp, lead_f=LEAD_F):
    """The film's sound as one file: the narration from frame lead_f, each library sound with its peak on its event,
    levelled to -14 LUFS. No music, no bed (Valmera never picks music)."""
    import numpy as np
    sr = 48000
    n = int(round(l1["dur_f"] / FPS * sr))
    mix = np.zeros((n, 2), np.float32)

    def pcm(path):
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0?", "-vn", "-ac", "2", "-ar",
                            str(sr), "-f", "f32le", "pipe:1"], capture_output=True, timeout=600)
        return np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()

    v = pcm(narration_wav)
    at = int(round(lead_f / FPS * sr))
    v = v[:max(n - at, 0)]
    mix[at:at + len(v)] += v
    lib_dir = os.path.join(scene_worker(), "sound_library")
    for s in l1.get("sound") or []:
        smp = pcm(os.path.join(lib_dir, os.path.basename(s["file"])))[int(round(float(s.get("trim_s", 0)) * sr)):]
        a = int(round(float(s["start_s"]) * sr))
        if a >= n or not len(smp):
            continue
        smp = smp[:n - a] * (10 ** (float(s.get("db", 0)) / 20))
        mix[a:a + len(smp)] += smp
    raw = os.path.join(tmp, "mix.f32")
    mix.tofile(raw)
    dur = l1["dur_f"] / FPS
    src = ["-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw]
    target = "I=-14:TP=-1.5:LRA=11"
    m = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y", *src, "-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=600)
    t = m.stderr
    j = json.loads(t[t.rindex("{"):t.rindex("}") + 1])
    af = (f"loudnorm={target}:measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}:"
          f"measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true:print_format=json,"
          f"aresample={sr},apad=whole_dur={dur:.6f},atrim=0:{dur:.6f}")
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y", *src, "-af", af, "-c:a", "aac", "-b:a", "256k", out], capture_output=True, text=True, timeout=600)
    if r.returncode:
        raise RuntimeError("audio master failed: " + r.stderr[-300:])
    os.remove(raw)
    return dict(sounds=len(l1.get("sound") or []), lufs_in=float(j["input_i"]))


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
        if not events or min(abs(s["event_f"] - e) for e in events) > 3:
            bad.append(f"sound {s['id']} at frame {s['event_f']} has nothing landing with it")
    secs = l1["dur_f"] / FPS
    n_s = len(l1.get("sound") or [])
    np10 = len([p for p in beats.get("pictures") or [] if p.get("kind") in ("photo", "designed", "window")]) / secs * 10
    allow = max(1, int(min(0.9 * np10, 6) * secs / 10)) if np10 >= 5 else max(1, int((1.5 if np10 >= 1 else 0.6) * secs / 10))
    if n_s > allow:
        bad.append(f"{n_s} sounds where the supply ({np10:.1f} new pictures a 10 s) allows {allow}")
    return dict(ok=not bad, problems=bad[:20], words=len(words), sounds=n_s, sounds_allowed=allow, new_pictures_10s=round(np10, 2),
                sounds_per_10s=round(n_s / secs * 10, 2))
