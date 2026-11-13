"""The marketing films, as scene plans (L1) for Valmera's engine.

A style is a function: (script, supply, direction) -> (L1, beats). Everything is baked here: seats,
curves, the seam's path, every block of a timeline. The engine reads numbers and draws. Type is
measured with the engine's own shaper, so a line sits where this file says it sits.

Every picture of an edit is an export the product made in a recorded session (studio.py), played
as it was delivered. Where two of them, or an export and its upload, are on screen at once they
show the same instant: the place of every frame comes from the project's own EDL, through the
worker's own timeline (source seconds to programme seconds).

Devices (atlas/ALIEN.md):
  T5 Reading Wipe   the seam of a before/after is the caret of the sentence being read; what the
                    caret has passed is already the edit, and the sentence is spent as it is read
  A1 Pause-Proof    a word that exists only in motion: static inside the letters runs one way,
                    static outside the other; any single frame is noise
  N7 Kuleshov, live the same seconds, three edits from three typed sentences, in lip-sync
  N8 Live Timeline  the film's own EDL as blocks at their true seconds, the playhead at true time,
                    and one sentence's change shown as the blocks it removed, moved and added
  A3 Edit Receipt   the edit, itemised from the session's own record
Taste (week-1112/TASTE.md): one seat per kind of type, nothing over a face, nothing outside the
safe area, a sound only where something lands, and the real export is never drawn over.
"""
import json
import math
import os
import subprocess
import sys

FPS, W, H = 30, 1080, 1920
SAFE = (0.05, 0.10, 0.95, 0.80)          # x0, y0, x1, y1 (TASTE P2)
RAIL_X = 0.86                            # right of this between y .55 and .80 sits the platform's buttons
MARK = (0.5594, 0.115, 0.9146, 0.1923)   # the watermark's keep-out


# ----------------------------------------------------------------- engine access

def scene_worker():
    """The worker that holds the engine package: beside this plugin once it is on main, else lane C's tree."""
    if os.environ.get("VALMERA_SCENE_WORKER"):
        return os.environ["VALMERA_SCENE_WORKER"]
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.exists(os.path.join(d, "worker", "scene", "run.py")):
            return os.path.join(d, "worker")
    return "/Users/masaoodi/Documents/Valmera/worktrees/lane-c-scene-1112/worker"


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
        """The largest size, at most px_max, at which text is no wider than max_px_wide."""
        vo = dict(self.voices[v])
        vo["px"] = 100.0
        w100 = self.T.Face.of(vo).measure(text, 100.0, float(vo.get("track", 0)))
        return float(min(px_max, math.floor(100.0 * max_px_wide / max(w100, 1e-6))))


# ----------------------------------------------------------------- curves (baked lists)

def expo_out(n):
    if n <= 1:
        return [1.0]
    k = 1.0 - 2.0 ** -10.0
    return [(1.0 - 2.0 ** (-10.0 * i / (n - 1))) / k for i in range(n)]


def ease_io(n):
    if n <= 1:
        return [1.0]
    return [0.5 - 0.5 * math.cos(math.pi * i / (n - 1)) for i in range(n)]


def settle(n, s0=0.72):
    """A small landing: from s0 to 1 with one soft overshoot."""
    out = []
    for i in range(n):
        t = i / max(n - 1, 1)
        out.append(1.0 + (s0 - 1.0) * math.exp(-5.5 * t) * math.cos(4.2 * t))
    out[-1] = 1.0
    return out


def r4(x):
    return round(float(x), 5)


# ----------------------------------------------------------------- media facts

def quiet_point(ffmpeg, path, lo, hi, win=0.06):
    """The quietest moment of the voice between lo and hi seconds: where a speech edit is not heard."""
    import numpy as np
    sr = 16000
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{lo:.3f}", "-t", f"{hi - lo:.3f}", "-i", path, "-vn", "-ac", "1",
                        "-ar", str(sr), "-f", "f32le", "-"], capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.float32)
    n = int(win * sr)
    if len(x) < 2 * n:
        return (lo + hi) / 2
    e = np.convolve(x * x, np.ones(n, np.float32) / n, mode="valid")
    return lo + (int(e.argmin()) + n / 2) / sr


def lead_in(ffmpeg, path, look_s=1.6):
    """Seconds to skip at the head of a short's audio: a tail of the word before its first line, when the short was cut
    a breath early. Found from the signal: sound, then 0.3 s or more of quiet, then the line. 0 when the line starts clean."""
    import numpy as np
    sr = 16000
    r = subprocess.run([ffmpeg, "-v", "error", "-t", f"{look_s:.2f}", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    x = np.frombuffer(r.stdout, np.float32)
    w = 0.05
    n = int(w * sr)
    if len(x) < 12 * n:
        return 0.0
    e = 10 * np.log10(np.maximum((x[: len(x) // n * n].reshape(-1, n) ** 2).mean(1), 1e-12))
    loud, quiet = e > e.max() - 21, e < e.max() - 23
    if not loud[: int(0.25 / w)].any():        # nothing before the line: it starts clean
        return 0.0
    k = int(np.argmax(loud))
    while k < len(e) and not quiet[k]:
        k += 1
    q0 = k
    while k < len(e) and quiet[k]:
        k += 1
    if (k - q0) * w < 0.3 or k >= len(e):
        return 0.0
    return round((q0 + min(k - q0, 6) / 2) * w, 3)


def sound(sid, name, lib, event_f, target_peak_db):
    """One library sound with its peak on event_f, at a peak level in dBFS before the film's loudness pass."""
    m = lib[name]
    start = event_f / FPS - m["peak_s"]
    trim = 0.0
    if start < 0:
        trim, start = -start, 0.0
    return dict(id=sid, s=name, file=m["file"], event_f=int(event_f), start_s=round(start, 4), trim_s=round(trim, 4),
                db=round(target_peak_db - m["max_db"], 2), peak_s=m["peak_s"])


def cell_runs(text, font_path, px, x, baseline, cell):
    """A word as whole cells of the static's grid: (x px, y px, cells) for each horizontal run.

    A letter cut out of static with its true outline shows that outline in a still: the cells it
    crosses break into curved pieces. Whole cells leave nothing to see in any one frame; only the
    direction the static moves says where the word is."""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(font_path, int(round(px)))
    im = Image.new("L", (W, H), 0)
    ImageDraw.Draw(im).text((x, baseline), text, font=font, fill=255, anchor="ls")
    import numpy as np
    a = np.asarray(im, np.float32) / 255.0
    g = a.reshape(H // cell, cell, W // cell, cell).mean(axis=(1, 3)) >= 0.5
    runs = []
    for r in range(g.shape[0]):
        c = 0
        while c < g.shape[1]:
            if g[r, c]:
                c0 = c
                while c < g.shape[1] and g[r, c]:
                    c += 1
                runs.append((c0 * cell, r * cell, c - c0))
            else:
                c += 1
    return runs


# ----------------------------------------------------------------- the product's own timeline

def worker_dir():
    """The worker that holds the product's timeline arithmetic: beside this plugin in a checkout, else lane C's tree."""
    if os.environ.get("VALMERA_WORKER"):
        return os.environ["VALMERA_WORKER"]
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.exists(os.path.join(d, "worker", "timeline.py")):
            return os.path.join(d, "worker")
    return scene_worker()


def program(edl):
    """(timeline, programme seconds) of a project's EDL, by the worker's own source-to-programme mapping."""
    w = worker_dir()
    if w not in sys.path:
        sys.path.append(w)
    import timeline as TL
    keep = [(float(k[0]), float(k[1])) for k in (edl.get("keep") or [])]
    tl = TL.Timeline(keep, sorted(list(edl.get("inserts") or []), key=TL._ins_sort_key), edl.get("speed"))
    return tl, float(tl.out_duration)


def runs(tl, a, b):
    """Stretches of programme time inside [a, b] that play one run of the source at its own speed:
    (programme start, programme end, source start). A cut ends a run; a speed change inside the window is refused,
    because the film would then show two pictures of different instants as one."""
    out = []
    for i, (s, e) in enumerate(tl.segs):
        o0 = tl.offsets[i]
        o1 = o0 + tl.seg_out_len[i]
        lo, hi = max(a, o0), min(b, o1)
        if hi - lo > 1e-3:
            if len(tl.pieces[i]) != 1 or abs(tl.pieces[i][0][2] - 1.0) > 1e-6:
                raise ValueError("a speed change inside the window: it cannot be played in sync")
            out.append((lo, hi, s + (lo - o0)))
    return out


def clock(sec):
    sec = int(round(float(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def ratio(w, h):
    g = math.gcd(int(w), int(h))
    a, b = int(w) // g, int(h) // g
    return f"{a}:{b}" if max(a, b) <= 21 else f"{w / h:.2f}:1"


# ----------------------------------------------------------------- a plan being written

class Plan:
    """Items, sounds and assets of one film, with the few shapes every style here is made of."""

    def __init__(self, voices, lib):
        self.voices, self.lib = voices, lib
        self.items, self.sounds, self.assets, self.spine = [], [], {}, []
        self.events, self.exports, self.shown = [], [], []
        self.R = Ruler(voices)

    def video(self, key, m):
        self.assets[key] = dict(kind="video", path=m["path"], w=m["w"], h=m["h"], fps=m["fps"], dur=m["dur"])
        return key

    def image(self, key, path, w, h):
        self.assets[key] = dict(kind="image", path=path, w=w, h=h)
        return key

    def page(self, iid, f, c0, c1=None, grain=0.0, **kw):
        self.items.append(dict(id=iid, k="shot", f=list(f), pic="plate", plate=dict(c0=c0, c1=c1 or c0, grain=grain), **kw))

    def shot(self, iid, f, pic, t0):
        """A real export, full frame, at its own speed, with nothing over it."""
        self.items.append(dict(id=iid, k="shot", f=list(f), pic=pic, src=[r4(t0), r4(t0 + (f[1] - f[0]) / FPS)]))
        self.exports.append([int(f[0]), int(f[1])])

    @staticmethod
    def seat(box):
        x0, y0, x1, y1 = box
        return dict(x=r4((x0 + x1) / 2), y=r4((y0 + y1) / 2), w=r4(x1 - x0), h=r4(y1 - y0))

    def block(self, iid, f, box, c, z=0, **kw):
        """A flat shape: a plate card."""
        it = dict(id=iid, k="card", f=list(f), z=z, pic="plate", plate=dict(c0=c, c1=c, grain=0.0), s=1, rot=0, op=1, **self.seat(box))
        it.update(kw)
        self.items.append(it)
        return it

    def card(self, iid, f, pic, box, t0=None, z=1, **kw):
        it = dict(id=iid, k="card", f=list(f), z=z, pic=pic, s=1, rot=0, op=1, **self.seat(box))
        if t0 is not None:
            it["src"] = [r4(t0), r4(t0 + (f[1] - f[0]) / FPS)]
        it.update(kw)
        self.items.append(it)
        return it

    def word(self, iid, f, t, v, x, y, ink="#FFFFFF", op=1.0, align="l", **kw):
        if kw.pop("note", True):
            self.shown.append(t)                   # every string the film shows, for the check against the evidence
        fade = int(kw.pop("fade", 0) or 0)
        if fade:                                   # it leaves over its last frames
            n = f[1] - f[0]
            ops = list(op) if isinstance(op, list) else [op] * n
            op = [ops[min(i, len(ops) - 1)] * min(1.0, (n - 1 - i) / fade) for i in range(n)]
        if align != "l":
            wd = self.R.width(v, t) / W
            x = x - wd if align == "r" else x - wd / 2
        it = dict(id=iid, k="word", f=list(f), t=t, v=v, x=r4(x) if not isinstance(x, list) else [r4(q) for q in x],
                  y=r4(y) if not isinstance(y, list) else [r4(q) for q in y], ink=ink,
                  op=r4(op) if not isinstance(op, list) else [r4(q) for q in op], role="big", on=int(f[0]))
        it.update(kw)
        self.items.append(it)
        return it

    def rise(self, iid, f, t, v, x, y, ink="#FFFFFF", op=1.0, n=7, dy=0.008, align="l", **kw):
        """A line that arrives: a short rise, eased, while it comes up to its level."""
        m = f[1] - f[0]
        e = expo_out(n)
        ys = [y + dy * (1 - q) for q in e] + [y] * max(m - n, 0)
        ops = [op * min(1.0, (i + 1) / 4.0) for i in range(n)] + [op] * max(m - n, 0)
        return self.word(iid, f, t, v, x, ys[:m], ink, ops[:m], align=align, **kw)

    def typed(self, iid, f0, f1, lines, v, x, y0, pitch, ink="#FFFFFF", per=1, op=1.0, fade=0):
        """Lines typed a character a frame. Returns (frame after the last character, [(frame, x, y)] of the caret)."""
        f, caret = f0, []
        self.shown.append(" ".join(lines))
        for li, text in enumerate(lines):
            y = y0 + li * pitch
            for i, ch in enumerate(text):
                if ch != " ":
                    xi = x + (self.R.width(v, text[:i + 1]) - self.R.width(v, ch)) / W
                    self.word(f"{iid}.{li}.{i:03d}", [f, f1], ch, v, xi, y, ink, op, note=False, fade=fade)
                f += per
                caret.append((f, x + self.R.width(v, text[:i + 1]) / W, y))
        return f, caret

    def wrap(self, text, v, max_px):
        """Break a sentence into lines no wider than max_px, at spaces."""
        out, cur = [], ""
        for wd in text.split():
            t = (cur + " " + wd).strip()
            if cur and self.R.width(v, t) > max_px:
                out.append(cur)
                cur = wd
            else:
                cur = t
        if cur:
            out.append(cur)
        return out

    def hit(self, sid, name, f, db):
        """A library sound with its peak on frame f. The frame is an event of the film: something lands there."""
        self.sounds.append(sound(sid, name, self.lib, f, db))
        self.events.append(int(f))

    def voice(self, f, key, t0):
        self.spine.append(dict(f=[int(f[0]), int(f[1])], src=[r4(t0), r4(t0 + (f[1] - f[0]) / FPS)], a=key))

    def synced(self, iid, f, tl, t_at_f0, pic, box, z=1, **kw):
        """The source, on the frames f, showing the instant the programme shows: one card per uncut run."""
        a = t_at_f0
        made, prev = [], f[0]
        for o0, o1, s0 in runs(tl, a, a + (f[1] - f[0]) / FPS):
            fa = max(prev, f[0] + int(round((o0 - a) * FPS)))
            fb = min(f[1], f[0] + int(round((o1 - a) * FPS)))
            if fb > fa:
                made.append(self.card(f"{iid}.{fa}", [fa, fb], pic, box, t0=s0 + ((fa - f[0]) / FPS - (o0 - a)), z=z, **kw))
                prev = fb
        return made

    def done(self, sid, dur, seed, film=None):
        order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
        self.items.sort(key=lambda it: (it["f"][0], order_k[it["k"]], it.get("z", 0), it["id"]))
        return dict(l1=1, sid=sid, seed=seed, fps=FPS, frame=[W, H], dur_f=int(dur), assets=self.assets, spine=self.spine, voices=self.voices,
                    film=film or dict(grain=0.0, vignette=0.0, over_type=False,
                                      grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0)),
                    items=self.items, sound=self.sounds)


def pull(seat_box, n, out=True):
    """A real export between the whole frame and a seat of its own shape: (x, y, s) per frame about one fixed
    point, so the picture never slides. out: the frame becomes the card (fast, then settling); else the card
    becomes the frame (eased both ways)."""
    x0, y0, x1, y1 = seat_box
    ex, ey, sw = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0
    z0 = 1.0 / sw
    px_, py_ = (0.5 - ex * z0) / (1 - z0), (0.5 - ey * z0) / (1 - z0)
    us = expo_out(n + 1)[1:] if out else [1.0 - u for u in ease_io(n + 1)[1:]]
    zs = [z0 ** (1.0 - u) for u in us]
    return [r4(px_ + (ex - px_) * z) for z in zs], [r4(py_ + (ey - py_) * z) for z in zs], [r4(z) for z in zs]


def keyword_page(P, f0, f1, cta, seed, cell=12):
    """A1: static over the whole frame and a word drawn as whole cells that run the other way. Under the product's
    mark the ground is plain, in whole cells, so the mark is read on every frame of it."""
    P.items.append(dict(id="cta.static", k="shot", f=[f0, f1], pic="plate", plate=dict(kind="static", cell=cell, speed=12, seed=seed, c0="#000000", c1="#FFFFFF")))
    P.voices["k"]["px"] = P.R.fit_px("k", cta["word"], 0.84 * W, 232)
    kw, kcap = P.R.width("k", cta["word"]), P.R.cap("k")
    kx = round((0.5 - kw / W / 2) * W / cell) * cell
    ky = round((0.47 * H + kcap / 2) / cell) * cell
    inside = dict(kind="static", cell=cell, speed=-12, seed=seed, c0="#000000", c1="#FFFFFF")
    for j, (cx0, cy0, n_c) in enumerate(cell_runs(cta["word"], P.R.T.find_font(P.voices["k"]["font"]), P.voices["k"]["px"], kx, ky, cell)):
        P.items.append(dict(id=f"cta.w{j:03d}", k="card", f=[f0, f1], z=1, pic="plate", plate=inside,
                            x=r4((cx0 + n_c * cell / 2) / W), y=r4((cy0 + cell / 2) / H), w=r4(n_c * cell / W), h=r4(cell / H), s=1, rot=0, op=1))
    rows_txt = [r for r in cta["rows"] if r][:2]
    rw = max(P.R.width("t", r) for r in rows_txt)
    pad_x, row_h = 34, 56
    half = math.ceil((rw / 2 + pad_x) / cell) * cell
    sx = max((W / 2 - half) / W, SAFE[0])
    y_top = round(0.66 * H / cell) * cell / H
    strip = [r4(sx), r4(y_top), r4(1 - sx), r4(y_top + math.ceil((len(rows_txt) * row_h + 40) / cell) * cell / H)]
    P.items.append(dict(id="cta.ground", k="flash", f=[f0, f1], mode="fill", c="#000000", a=1, rect=strip, z=1))
    mark = [r4(math.floor(MARK[0] * W / cell - 1) * cell / W), r4(math.floor(MARK[1] * H / cell - 1) * cell / H),
            r4(math.ceil(MARK[2] * W / cell + 1) * cell / W), r4(math.ceil(MARK[3] * H / cell + 1) * cell / H)]
    P.items.append(dict(id="cta.mark", k="flash", f=[f0, f1], mode="fill", c="#000000", a=1, rect=mark, z=1))
    for i, r in enumerate(rows_txt):
        P.word(f"cta.row{i}", [f0, f1], r, "t", 0.5, y_top + (52 + i * row_h) / H, "#FFFFFF", 0.94 if i == 0 else 0.7, align="c")
    return dict(cta_strip=strip, mark_ground=mark)


# ----------------------------------------------------------------- style: Reading Wipe Before/After

def reading_wipe(script, sup, direction=None):
    """The upload, whole, on a page, and the one sentence that was typed. The caret that reads the sentence is the
    seam: behind it the frame is already the film the product made of it. Then that film, untouched; then the two
    side by side at the same instant; then a word that only exists while it moves."""
    d = dict(hold_f=8, word_f=5, reach_f=3, leave_f=10, hero_in_s=0.0, hero_f=150, shrink_f=20, pair_f=100, cta_f=99, lean=6.0,
             paper0="#2B4BD8", paper1="#1A33A6", ink="#F7F3EA", tick_db=-20.0, whoosh_db=-21.0)   # a page the product's white mark reads on
    d.update(direction or {})
    rec = sup["records"][0]
    exp, up = rec["export"], rec["upload"]
    tl, body = program(json.load(open(rec["edl"])))
    t0 = float(d["hero_in_s"])
    voices = {
        "s": {"font": "InstrumentSerif-Regular.ttf", "px": 80, "track": -1.0},     # the sentence, as it was typed
        "t": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},              # labels meant to be read (TASTE P5)
        "b": {"font": "InstrumentSerif-Regular.ttf", "px": 132, "track": -1.5},    # the one line of the pair
        "k": {"font": "InterDisplay-Black.ttf", "px": 196, "track": 0},            # the keyword
    }
    P = Plan(voices, sup["sounds"])
    R = P.R
    P.video("main", exp)
    P.video("raw", up)
    seed = int(d.get("seed", 1113))
    ink, paper = d["ink"], (d["paper0"], d["paper1"])

    # ---- beat 1: the page ---------------------------------------------------------------
    sentence = rec["sentence"].strip()
    words = sentence.split()
    x_lo, x_hi = 0.07, 0.93
    raw_h = (x_hi - x_lo) * W * up["h"] / up["w"] / H
    raw_y0 = d.get("raw_y", 0.236)
    raw_box = [x_lo, raw_y0, x_hi, raw_y0 + raw_h]
    sy = d.get("sentence_y", raw_box[3] + 0.04)            # above y 0.55 the line may run the page's width (TASTE P2, the platform's rail)
    voices["s"]["px"] = R.fit_px("s", sentence, ((x_hi if sy <= 0.55 else RAIL_X) - x_lo) * W, 84)
    if voices["s"]["px"] < 40:
        raise ValueError(f"the sentence is {len(sentence)} characters: too long to read on one line (40 px floor). Record a shorter one")
    places, stops = [], []
    for i, wd in enumerate(words):
        pre = " ".join(words[:i]) + (" " if i else "")
        x = x_lo + R.width("s", pre) / W
        places.append(x)
        end = x + R.width("s", wd) / W
        nxt = x_lo + R.width("s", " ".join(words[:i + 1]) + " ") / W if i + 1 < len(words) else end + 0.012
        stops.append((end + nxt) / 2 if i + 1 < len(words) else end + 0.012)
    caret0 = x_lo - 0.014
    lean = float(d["lean"])
    tan = math.tan(math.radians(lean))

    def seam_mid(x_caret):
        return x_caret + (0.5 - sy) * H * tan / W

    f_wipe0 = int(d["hold_f"])
    xs = [seam_mid(caret0)] * f_wipe0
    cur, marks = caret0, []
    for st in stops:
        marks.append(len(xs))
        xs += [seam_mid(cur + (st - cur) * v) for v in expo_out(int(d["reach_f"]) + 1)[1:]]
        xs += [seam_mid(st)] * (int(d["word_f"]) - int(d["reach_f"]))
        cur = st
    far = 1.0 + abs(0.5 * H * tan / W) + 0.03
    xs += [seam_mid(cur) + (far - seam_mid(cur)) * (v ** 2.2) for v in ease_io(int(d["leave_f"]) + 1)[1:]]
    f_wipe1 = len(xs)
    P.page("raw.page", [0, f_wipe1], paper[0], paper[1], grain=0.03)
    P.synced("raw.pic", [0, f_wipe1], tl, t0, "raw", raw_box, z=1, radius=0.008, shadow=dict(a=0.34, blur=0.03, dx=0, dy=0.012))
    P.word("raw.tag", [0, f_wipe1], script["tag_raw"], "t", x_lo, raw_box[1] - 0.016, ink, 0.7)
    P.shown.append(sentence)
    for i, wd in enumerate(words):
        P.word(f"raw.s{i}", [0, f_wipe1], wd, "s", places[i], sy, ink, 0.96, note=False)
    P.items.append(dict(id="wipe", k="join", f=[0, f_wipe1], mode="seam", stage="top", pic="main", src=[r4(t0), r4(t0 + f_wipe1 / FPS)],
                        x=[r4(v) for v in xs], lean=lean, edge=dict(w=1.6, a=0.9), dim=1.0, a=1.0))
    for i, m in enumerate(marks):
        P.hit(f"key{i}", "tick_1", m + 1, d["tick_db"])

    # ---- beat 2: the film the product made, untouched -----------------------------------
    f_hero1 = f_wipe1 + int(d["hero_f"])
    P.shot("hero", [f_wipe1, f_hero1], "main", t0 + f_wipe1 / FPS)

    # ---- beat 3: the two, at the same instant -------------------------------------------
    f_pair1 = f_hero1 + int(d["pair_f"])
    n_s = int(d["shrink_f"])
    P.page("pair.page", [f_hero1, f_pair1], paper[0], paper[1], grain=0.03)
    ebox = d.get("export_box", [0.44, 0.205, 0.86, 0.625])
    t_pair = t0 + f_hero1 / FPS
    xs_, ys_, ss_ = pull(ebox, n_s)
    P.card("pair.live", [f_hero1, f_hero1 + n_s], "main", ebox, t0=t_pair, z=3, x=xs_, y=ys_, s=ss_)
    P.card("pair.held", [f_hero1 + n_s, f_pair1], "main", ebox, t0=t_pair + n_s / FPS, z=3, radius=0.008, shadow=dict(a=0.3, blur=0.03, dx=0, dy=0.012))
    rw_ = d.get("pair_raw_w", 0.34)
    rbox = [x_lo, ebox[1], x_lo + rw_, ebox[1] + rw_ * W * up["h"] / up["w"] / H]
    f_r0 = f_hero1 + 8
    made = P.synced("pair.raw", [f_r0, f_pair1], tl, t0 + f_r0 / FPS, "raw", rbox, z=2, radius=0.008, shadow=dict(a=0.26, blur=0.025, dx=0, dy=0.01))
    if made:
        n0 = made[0]["f"][1] - made[0]["f"][0]
        made[0]["op"] = [r4(min(1.0, (i + 1) / 6.0)) for i in range(n0)]
    P.rise("pair.tag", [f_r0 + 2, f_pair1], script["tag_raw"], "t", x_lo, rbox[3] + 0.034, ink, 0.7)
    f_line = f_hero1 + n_s
    voices["b"]["px"] = R.fit_px("b", script["line"], (0.86 - x_lo) * W, 136)
    ly = d.get("line_y", 0.722)
    P.rise("pair.line", [f_line, f_pair1], script["line"], "b", x_lo, ly, ink, 0.96, n=8, dy=0.012)
    P.rise("pair.facts", [f_line + 8, f_pair1], script["facts_row"], "t", x_lo, ly + 0.045, ink, 0.7)
    P.hit("move", "whoosh_soft_1", f_hero1 + 2, d["whoosh_db"])
    P.events.append(f_line)

    # ---- beat 4: the word that only exists while it moves -------------------------------
    f_cta0, f_cta1 = f_pair1, f_pair1 + int(d["cta_f"])
    seats = keyword_page(P, f_cta0, f_cta1, script["cta"], seed)

    # ---- sound: the film's own voice, one run, then nothing ------------------------------
    v_from = d.get("voice_from_s", lead_in(sup["ffmpeg"], exp["path"]) if t0 == 0 else 0.0)
    if v_from > 0.2:
        v_from = quiet_point(sup["ffmpeg"], exp["path"], t0 + v_from - 0.2, t0 + v_from + 0.2) - t0
    f_v0 = int(round(v_from * FPS))
    want = min(t0 + (f_pair1 - 12) / FPS, body - 0.05)
    t_end = quiet_point(sup["ffmpeg"], exp["path"], max(want - 0.9, t0 + 1.0), want) if d.get("voice_end_s") is None else float(d["voice_end_s"])
    f_v1 = min(int(round((t_end - t0) * FPS)), f_pair1)
    P.voice([f_v0, f_v1], "main", t0 + f_v0 / FPS)
    l1 = P.done(sup.get("sid", "wf2"), f_cta1, seed)
    mid = (f_wipe1 + f_hero1) // 2
    beats = dict(wipe=[0, f_wipe1], hero=[f_wipe1, f_hero1], pair=[f_hero1, f_pair1], cta=[f_cta0, f_cta1], caret_marks=marks,
                 events=sorted(set(P.events)), exports=P.exports, voice=[f_v0, f_v1], cta_kind="static",
                 export_check=dict(f=mid, asset="main", t=r4(t0 + (mid + 0.5) / FPS)),
                 own_pages=[[0, f_wipe1], [f_hero1, f_pair1]], on_screen=P.shown,
                 look=sorted({0, f_wipe0 + 2, *(m + 3 for m in marks), f_wipe1 - 2, f_wipe1 + 30, f_hero1 - 1, f_hero1 + 6, f_hero1 + n_s + 2,
                              f_pair1 - 2, f_cta0 + 30}),
                 seats=dict(sentence_y=r4(sy), raw=[r4(v) for v in raw_box], export=ebox, pair_raw=[r4(v) for v in rbox], line_y=r4(ly), **seats))
    return l1, beats


# ----------------------------------------------------------------- style: One Clip, Three Films

def one_clip_three_films(script, sup, direction=None):
    """The same seconds of one upload, three times, as three films the product made from three typed sentences.
    First the three at once, at the same instant; then each sentence typed and its film whole; then the three again."""
    d = dict(hook_f=42, take_s=4.4, type_hold_f=10, reprise_f=84, page="#060606", ink="#F4F1EB", dim="#8C8A86",
             click_db=-20.0, typing_db=-24.0, whoosh_db=-22.0)
    d.update(direction or {})
    recs = sup["records"][:3]
    tls = [program(json.load(open(r["edl"])))[0] for r in recs]
    ws = float(d["window_s"][0])
    L = int(round((float(d["window_s"][1]) - ws if len(d["window_s"]) > 1 else float(d["take_s"])) * FPS))
    starts = []
    for r, tl in zip(recs, tls):
        a = tl.src_to_out(ws)
        if len(runs(tl, a, a + L / FPS)) != 1:
            raise ValueError(f"{r['sid']} cuts inside the window {ws:.2f}-{ws + L / FPS:.2f} s: the three would not show the same seconds")
        starts.append(a)
    voices = {
        "s": {"font": "InstrumentSerif-Regular.ttf", "px": 100, "track": -1.0},    # a sentence, as it was typed
        "b": {"font": "InstrumentSerif-Regular.ttf", "px": 112, "track": -1.5},    # the film's two lines
        "q": {"font": "InstrumentSerif-Regular.ttf", "px": 96, "track": -1.5},     # the question of the close
        "t": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},              # its row, and the take numbers
    }
    P = Plan(voices, sup["sounds"])
    R = P.R
    for i, r in enumerate(recs):
        P.video(f"e{i}", r["export"])
    seed = int(d.get("seed", 1113))
    ink, dim, page = d["ink"], d["dim"], d["page"]
    x_lo = 0.055
    cw = 0.285
    gap = (0.945 - x_lo - 3 * cw) / 2
    cy0 = d.get("row_y", 0.262)
    boxes = [[x_lo + i * (cw + gap), cy0, x_lo + i * (cw + gap) + cw, cy0 + cw] for i in range(3)]

    def triptych(tag, f, t_off, voice_of=None):
        P.page(f"{tag}.page", f, page, page, grain=0.0)
        for i in range(3):
            P.card(f"{tag}.c{i}", f, f"e{i}", boxes[i], t0=starts[i] + t_off, z=1, radius=0.008, border=dict(w=0.0012, c="#2A2A2A"))
            P.word(f"{tag}.n{i}", f, f"0{i + 1}", "t", boxes[i][0], cy0 - 0.014, dim, 0.9)
        if voice_of is not None:
            P.voice(f, f"e{voice_of}", starts[voice_of] + t_off)

    # ---- hook: three films of the same instant -------------------------------------------
    n_h = int(d["hook_f"])
    triptych("hook", [0, n_h], 0.0, voice_of=0)
    voices["b"]["px"] = R.fit_px("b", max(script["line1"], script["line2"], key=len), (0.86 - x_lo) * W, 136)
    voices["q"]["px"] = R.fit_px("q", script["cta"]["rows"][0], (0.86 - x_lo) * W, 110)
    pitch_b = voices["b"]["px"] * 1.04 / H
    ty = boxes[0][3] + 0.03 + R.cap("b") / H
    P.word("hook.l1", [0, n_h], script["line1"], "b", x_lo, ty, ink, 0.96)
    P.rise("hook.l2", [12, n_h], script["line2"], "b", x_lo, ty + pitch_b, ink, 0.96, n=8, dy=0.012)
    P.events.append(12)
    f = n_h

    # ---- the three takes: a sentence typed, then its film, whole --------------------------
    takes = []
    for i, r in enumerate(recs):
        lines = P.wrap(r["sentence"].strip(), "s", (0.86 - 0.07) * W)
        if len(lines) > 3:
            raise ValueError(f"{r['sid']}: the sentence needs {len(lines)} lines at {voices['s']['px']} px")
        n_chars = sum(len(x) for x in lines)
        f_t1 = f + 6 + n_chars + int(d["type_hold_f"])
        P.page(f"type{i}.page", [f, f_t1], page, page, grain=0.0)
        pitch = voices["s"]["px"] * 1.1 / H
        ty0 = 0.47 - (len(lines) - 1) * pitch / 2            # the block of lines stands about the middle of the safe area
        P.word(f"type{i}.n", [f, f_t1], f"0{i + 1}", "t", 0.07, ty0 - 0.07, dim, 0.9)
        end, caret = P.typed(f"type{i}.s", f + 6, f_t1, lines, "s", 0.07, ty0, pitch, ink)
        cap = R.cap("s") / H
        n_c = f_t1 - f
        cxs = ([0.07] * 6 + [c[1] + 0.004 for c in caret] + [caret[-1][1] + 0.004] * n_c)[:n_c]
        cys = ([ty0] * 6 + [c[2] for c in caret] + [caret[-1][2]] * n_c)[:n_c]
        blink = [1.0 if (q < 6 + n_chars or ((q - 6 - n_chars) // 4) % 2 == 0) else 0.0 for q in range(n_c)]
        P.items.append(dict(id=f"type{i}.caret", k="card", f=[f, f_t1], z=2, pic="plate", plate=dict(c0=ink, c1=ink, grain=0.0),
                            x=[r4(v + 0.0015) for v in cxs], y=[r4(v - cap / 2) for v in cys], w=r4(3.0 / W), h=r4(cap * 1.25), s=1, rot=0,
                            op=[r4(v) for v in blink]))
        P.hit(f"type{i}", "typing_2", f + 7, d["typing_db"])
        f = f_t1
        P.shot(f"take{i}", [f, f + L], f"e{i}", starts[i])
        P.voice([f, f + L], f"e{i}", starts[i])
        P.hit(f"cut{i}", "click_1", f, d["click_db"])
        takes.append([f, f + L])
        f += L

    # ---- the three again, and the question ------------------------------------------------
    n_r = int(d["reprise_f"])
    t_rep = max(L / FPS - n_r / FPS, 0.0) if d.get("reprise_from_s") is None else float(d["reprise_from_s"])
    triptych("rep", [f, f + n_r], t_rep, voice_of=int(d.get("reprise_voice", 2)))
    cta = script["cta"]
    qy = boxes[0][3] + 0.03 + R.cap("q") / H
    P.rise("rep.q", [f + 6, f + n_r], cta["rows"][0], "q", x_lo, qy, ink, 0.96, n=8, dy=0.012)
    P.rise("rep.a", [f + 18, f + n_r], cta["rows"][1], "t", x_lo, qy + 0.048, ink, 0.6)
    P.hit("rep", "whoosh_soft_1", f + 2, d["whoosh_db"])
    P.events += [f + 6, f + 18]
    rep = [f, f + n_r]
    f += n_r
    l1 = P.done(sup.get("sid", "wf2"), f, seed)
    mid = (takes[1][0] + takes[1][1]) // 2
    beats = dict(hook=[0, n_h], takes=takes, reprise=rep, events=sorted(set(P.events)), exports=P.exports, cta_kind="rows",
                 window_s=[r4(ws), r4(ws + L / FPS)], starts=[r4(a) for a in starts],
                 export_check=dict(f=mid, asset="e1", t=r4(starts[1] + (mid - takes[1][0] + 0.5) / FPS)),
                 sync_check=dict(f=n_h // 2, boxes=[[r4(v) for v in b] for b in boxes], t=[r4(a + (n_h // 2 + 0.5) / FPS) for a in starts]),
                 own_pages=[[0, n_h], rep], on_screen=P.shown,
                 look=sorted({0, 14, n_h - 1, *(t[0] - 2 for t in takes), *(t[0] + 20 for t in takes), *(t[1] - 2 for t in takes), rep[0] + 4,
                              rep[0] + 30, rep[1] - 2}),
                 seats=dict(cards=[[r4(v) for v in b] for b in boxes], title_y=r4(ty)))
    return l1, beats


# ----------------------------------------------------------------- style: It Edits Itself

def edl_pieces(ka, kb):
    """Two cuts of one source as pieces no cut of either divides: (s, e, in the first, in the second)."""
    bounds = sorted({round(float(v), 3) for k in (ka, kb) for seg in k for v in seg})
    out = []
    for s, e in zip(bounds, bounds[1:]):
        if e - s < 0.02:
            continue
        m = (s + e) / 2
        ia = any(a <= m <= b for a, b in ka)
        ib = any(a <= m <= b for a, b in kb)
        if ia or ib:
            out.append((s, e, ia, ib))
    return out


def pictures_of(edl):
    """The pictures an EDL lays over its programme, on programme time: [{id, start, duration_s}]. The product keeps
    them in two places: `overlays` (a start and a length) and `fx` of type broll (a start and an end)."""
    out = []
    for o in edl.get("overlays") or []:
        if o.get("kind") in ("image", "video") and o.get("start") is not None and o.get("duration_s"):
            out.append(dict(id=str(o.get("id")), start=float(o["start"]), duration_s=float(o["duration_s"])))
    for o in edl.get("fx") or []:
        if o.get("type") == "broll" and o.get("start") is not None and o.get("end") is not None:
            out.append(dict(id=str(o.get("id")), start=float(o["start"]), duration_s=float(o["end"]) - float(o["start"])))
    return sorted(out, key=lambda o: o["start"])


def titles_of(edl):
    """The designed titles of an EDL (motion graphics), on programme time: [{id, start, duration_s}]."""
    return sorted((dict(id=str(o.get("id")), start=float(o["start"]), duration_s=float(o["end"]) - float(o["start"]))
                   for o in (edl.get("motion") or []) if o.get("start") is not None and o.get("end") is not None), key=lambda o: o["start"])


def edits_itself(script, sup, direction=None):
    """The film is one project of the product, before and after one typed sentence. It opens on the first cut,
    pulls back onto its own timeline (every block a kept range of the EDL at its true second, the playhead at the
    true time), the sentence is typed, the blocks it removed leave, the rest close up, the pictures it added land,
    and the frame goes back into the new cut. It closes on the edit's receipt, read from the session's record."""
    d = dict(open_f=34, pull_f=16, type_lead_f=8, send_f=10, change_f=34, watch_f=44, push_f=16, after_f=150, receipt_f=112,
             before_in_s=0.0, after_in_s=0.0, page="#0B0B0D", ink="#F1EEE8", dim="#8E8C88", accent="#FF5B35", track="#17181B",
             chip="#34363D", paper="#F4F1EA", paper_ink="#151515", click_db=-19.0, typing_db=-24.0, whoosh_db=-22.0, tick_db=-20.0)
    d.update(direction or {})
    rec = sup["records"][0]
    A, B = rec["before"], rec["export"]
    ea, eb = json.load(open(A["edl"])), json.load(open(rec["edl"]))
    tla, dur_a = program(ea)
    tlb, dur_b = program(eb)
    voices = {
        "s": {"font": "InstrumentSerif-Regular.ttf", "px": 56, "track": -0.5},     # the sentence, typed
        "t": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 6},              # labels
        "f": {"font": "InterDisplay-ExtraBold.ttf", "px": 46, "track": 0},         # what changed
        "r": {"font": "InterDisplay-Bold.ttf", "px": 38, "track": 1},              # receipt rows
        "h": {"font": "InterDisplay-Black.ttf", "px": 42, "track": 14},            # receipt head
        "q": {"font": "InstrumentSerif-Regular.ttf", "px": 64, "track": -0.5},     # receipt total
    }
    P = Plan(voices, sup["sounds"])
    R = P.R
    P.video("a", A)
    P.video("b", B)
    seed = int(d.get("seed", 1113))
    ink, dim, acc, page = d["ink"], d["dim"], d["accent"], d["page"]

    # frames of the beats
    f_open1 = int(d["open_f"])
    f_pull1 = f_open1 + int(d["pull_f"])
    lines = P.wrap(rec["sentence"].strip(), "s", d.get("sentence_w", 0.40) * W)
    n_chars = sum(len(x) for x in lines)
    f_type0 = f_pull1 + int(d["type_lead_f"])
    f_send = f_type0 + n_chars + int(d["send_f"])
    f_cut = f_send + int(d["change_f"])                   # the viewer shows the new cut from here
    f_push0 = f_cut + int(d["watch_f"])
    f_push1 = f_push0 + int(d["push_f"])
    f_after1 = f_push1 + int(d["after_f"])
    f_end = f_after1 + int(d["receipt_f"])
    ta0, tb0 = float(d["before_in_s"]), float(d["after_in_s"])

    # ---- the first cut, whole, then the frame becomes a viewer ----------------------------
    vbox = d.get("viewer", [0.05, 0.215, 0.40, 0.565])
    P.shot("open", [0, f_open1], "a", ta0)
    P.page("desk", [f_open1, f_push1], page, page, grain=0.012)
    xs_, ys_, ss_ = pull(vbox, f_pull1 - f_open1)
    P.card("view.out", [f_open1, f_pull1], "a", vbox, t0=ta0 + f_open1 / FPS, z=6, x=xs_, y=ys_, s=ss_)
    P.card("view.a", [f_pull1, f_cut], "a", vbox, t0=ta0 + f_pull1 / FPS, z=6, radius=0.006, border=dict(w=0.001, c="#2B2C31"))
    P.card("view.b", [f_cut, f_push0], "b", vbox, t0=tb0, z=6, radius=0.006, border=dict(w=0.001, c="#2B2C31"))
    xs_, ys_, ss_ = pull(vbox, f_push1 - f_push0, out=False)
    P.card("view.in", [f_push0, f_push1], "b", vbox, t0=tb0 + (f_push0 - f_cut) / FPS, z=9, x=xs_, y=ys_, s=ss_)
    t_after = tb0 + (f_push1 - f_cut) / FPS
    P.shot("after", [f_push1, f_after1], "b", t_after)
    P.hit("pull", "whoosh_soft_1", f_open1 + 2, d["whoosh_db"])

    # ---- the timeline: the EDL, as blocks -------------------------------------------------
    X0, X1 = 0.05, 0.86
    pps = (X1 - X0) / max(dur_a, dur_b)                   # frame widths per programme second
    yV, hV = d.get("track_y", 0.600), 0.100
    yP, hP = yV + hV + 0.008, 0.044
    yT, hT = yP + hP + 0.008, 0.022
    f_tl0 = f_open1 + 4                                   # blocks rise as the frame pulls back
    rise_n = 9
    f_drop = f_send + 12                                  # removed blocks leave; the rest close up from here
    slide_n = 16

    def life(k, n_life, drops=False, to=0.0):
        """Per-frame (x offset, y offset, opacity) of a block of the first cut: it rises in, then leaves or closes up."""
        dx, dy, op = [], [], []
        for q in range(n_life):
            fr = f_tl0 + k + q
            r_ = expo_out(rise_n)[min(q, rise_n - 1)]
            y_, o_, x_ = 0.022 * (1 - r_), min(1.0, (q + 1) / 5.0), 0.0
            if fr >= f_drop:
                if drops:
                    u = min((fr - f_drop) / 9.0, 1.0)
                    y_ += 0.03 * u * u
                    o_ *= max(0.0, 1.0 - u)
                else:
                    x_ = to * ease_io(slide_n)[min(fr - f_drop, slide_n - 1)]
            dx.append(x_)
            dy.append(y_)
            op.append(o_)
        return dx, dy, op

    ka, kb = [tuple(s) for s in tla.segs], [tuple(s) for s in tlb.segs]
    pieces = edl_pieces(ka, kb)
    thumbs_a, thumbs_b = sup["thumbs"]["a"], sup["thumbs"]["b"]
    th_w = hV * H * 9 / 16 / W                            # a 9:16 thumbnail as tall as the track
    bed_op = [r4(min(1.0, (q + 1) / 6.0)) for q in range(f_push1 - f_tl0)]
    has_titles = bool(titles_of(ea) or titles_of(eb))     # a track is drawn only when the project has something on it
    for nm, y_, h_ in (("v", yV, hV), ("p", yP, hP)) + ((("t", yT, hT),) if has_titles else ()):
        P.block(f"tl.{nm}.bed", [f_tl0, f_push1], [X0, y_, X1, y_ + h_], d["track"], z=0, radius=0.004, op=bed_op)
    inset = 1.5 / W
    order = [0]

    def bar(iid, y_, h_, xa, xb, wd, colour, thumbs=None, t_prog=0.0, new_colour=None, picture=None):
        """One block of a track. xa: its left edge in the first cut (None: the first cut does not have it); xb: in the
        new cut (None: the sentence removed it). It rises with the timeline, then leaves marked, closes up, or lands."""
        k = order[0]
        order[0] += 1
        if xa is not None:
            f0_, f1_ = f_tl0 + min(k, 14), (f_drop + 10) if xb is None else f_push1
            dx, dy, op = life(min(k, 14), f1_ - f0_, drops=xb is None, to=(xb - xa) if xb is not None else 0.0)
            x_at = xa
        else:
            f0_, f1_ = f_land + 4 * landed[0], f_push1
            landed[0] += 1
            n_ = f1_ - f0_
            dx, dy, op = [0.0] * n_, [-0.03 * (1 - expo_out(9)[min(q, 8)]) for q in range(n_)], [min(1.0, (q + 1) / 4.0) for q in range(n_)]
            x_at = xb
            P.hit(f"{iid}.land", "tick_1", f0_ + 1, d["tick_db"])
        box = [x_at + inset, y_ + 0.004, x_at + wd - inset, y_ + h_ - 0.004]
        if box[2] - box[0] < 3 / W:
            return
        seat = P.seat(box)
        xs_b, ys_b, ops_b = [r4(seat["x"] + v) for v in dx], [r4(seat["y"] + v) for v in dy], [r4(v) for v in op]
        if picture:
            P.card(iid, [f0_, f1_], picture, box, z=2, radius=0.003, border=dict(w=0.0022, c=new_colour if xa is None else "#4A4C55"), x=xs_b, y=ys_b, op=ops_b)
        else:
            P.block(iid, [f0_, f1_], box, new_colour if (xa is None and new_colour) else colour, z=1, radius=0.003, x=xs_b, y=ys_b, op=ops_b)
        for j in range(max(int(math.ceil((box[2] - box[0]) / th_w - 1e-6)), 1) if thumbs else 0):
            tx0 = box[0] + j * th_w
            tw_ = min(th_w, box[2] - tx0)
            if tw_ < 6 / W:
                continue
            tm = min(thumbs[1], key=lambda q: abs(q["t"] - (t_prog + (j + 0.5) * th_w / pps)))
            key = f"th{thumbs[0]}{int(round(tm['t'] * 10)):04d}"
            if key not in P.assets:
                P.image(key, tm["path"], tm["w"], tm["h"])
            st_ = P.seat([tx0, box[1], tx0 + tw_, box[3]])
            P.card(f"{iid}.t{j:02d}", [f0_, f1_], key, [tx0, box[1], tx0 + tw_, box[3]], z=2, crop=[0.0, 0.0, r4(tw_ / th_w), 1.0],
                   x=[r4(st_["x"] + v) for v in dx], y=[r4(st_["y"] + v) for v in dy], op=[r4(0.9 * v) for v in op])
        if xa is not None and xb is None:                 # what the sentence removes is marked, then it leaves
            n_m = f1_ - f_send
            P.block(f"{iid}.x", [f_send, f1_], box, acc, z=3, radius=0.003, x=xs_b[-n_m:], y=ys_b[-n_m:],
                    op=[r4(0.6 * min(1.0, (q + 1) / 4.0) * op[-n_m:][q]) for q in range(n_m)])

    landed = [0]
    f_land = f_drop + slide_n - 4
    removed_s = 0.0
    for pi, (s, e, ia, ib) in enumerate(pieces):
        removed_s += (e - s) if (ia and not ib) else 0.0
        bar(f"tl.v{pi:02d}", yV, hV, X0 + tla.src_to_out(s) * pps if ia else None, X0 + tlb.src_to_out(s) * pps if ib else None, (e - s) * pps, d["chip"],
            thumbs=("a", thumbs_a) if ia else ("b", thumbs_b), t_prog=(tla if ia else tlb).src_to_out(s))
    # pictures and titles stand on programme time; the same id in both cuts is the same thing, moved
    pic_thumbs = sup["thumbs"].get("pictures") or {}
    added = 0
    for nm, y_, h_, of in (("p", yP, hP, pictures_of), ("t", yT, hT, titles_of)):
        was, now = {o["id"]: o for o in of(ea)}, {o["id"]: o for o in of(eb)}
        for j, oid in enumerate(sorted(set(was) | set(now), key=lambda q: (now.get(q) or was.get(q))["start"])):
            oa, ob = was.get(oid), now.get(oid)
            th = pic_thumbs.get(oid) if nm == "p" else None
            key = P.image(f"pic.{oid}", th["path"], th["w"], th["h"]) if th else None
            added += 1 if (nm == "p" and oa is None) else 0
            bar(f"tl.{nm}{j:02d}", y_, h_, X0 + oa["start"] * pps if oa else None, X0 + ob["start"] * pps if ob else None,
                (ob or oa)["duration_s"] * pps, d["chip"] if nm == "p" else "#5A5D68", new_colour=acc, picture=key)
    # the playhead, at the true second of whichever cut the viewer shows
    ph = []
    for fr in range(f_tl0, f_push1):
        t = ta0 + fr / FPS if fr < f_cut else tb0 + (fr - f_cut) / FPS
        x = X0 + min(t, dur_a if fr < f_cut else dur_b) * pps
        ph.append([r4(x - 1.5 / W), r4(yV - 0.012), r4(x + 1.5 / W), r4((yT + hT) if has_titles else (yP + hP))])
    f_off = f_push0 + 5                                   # the page's type and the playhead leave as the frame goes back into the cut
    n_ph = f_push0 + 2 - f_tl0
    P.items.append(dict(id="tl.head", k="flash", f=[f_tl0, f_push0 + 2], mode="fill", c=acc, a=bed_op[:n_ph], rect=ph[:n_ph], z=5))
    # labels: the versions and lengths of the EDL, as the product numbered them
    ly = yV - 0.022
    f_flip = f_drop + 8
    P.rise("tl.va", [f_tl0 + 4, f_flip], f"EDL v{A['edl_version']}", "t", X0, ly, dim, 0.9)
    P.rise("tl.vb", [f_flip, f_off], f"EDL v{B['edl_version']}", "t", X0, ly, ink, 0.95, fade=5)
    P.rise("tl.da", [f_tl0 + 4, f_flip], clock(dur_a), "t", X1, ly, dim, 0.9, align="r")
    P.rise("tl.db", [f_flip, f_off], clock(dur_b), "t", X1, ly, ink, 0.95, align="r", fade=5)
    P.events.append(f_flip)

    # ---- the sentence, typed beside the viewer --------------------------------------------
    sx = d.get("sentence_x", 0.45)
    sy = d.get("sentence_y", 0.285)
    pitch = voices["s"]["px"] * 1.16 / H
    P.rise("say.tag", [f_pull1 - 4, f_off], script["tag_ask"], "t", sx, sy - 0.042, dim, 0.9, fade=5)
    end, caret = P.typed("say.s", f_type0, f_off, lines, "s", sx, sy, pitch, ink, fade=5)
    cap = R.cap("s") / H
    n_c = f_send - f_type0
    cxs = ([c[1] + 0.004 for c in caret] + [caret[-1][1] + 0.004] * n_c)[:n_c]
    cys = ([c[2] for c in caret] + [caret[-1][2]] * n_c)[:n_c]
    P.items.append(dict(id="say.caret", k="card", f=[f_type0, f_send], z=2, pic="plate", plate=dict(c0=ink, c1=ink, grain=0.0),
                        x=[r4(v + 0.0015) for v in cxs], y=[r4(v - cap / 2) for v in cys], w=r4(3.0 / W), h=r4(cap * 1.25), s=1, rot=0,
                        op=[1.0 if (q < len(caret) or ((q - len(caret)) // 4) % 2 == 0) else 0.0 for q in range(n_c)]))
    P.hit("typing", "typing_2", f_type0 + 1, d["typing_db"])
    P.hit("send", "click_1", f_send, d["click_db"])
    fy = sy + (len(lines) - 1) * pitch + 0.052
    for j, t in enumerate([r for r in script["changes"] if r][:2]):
        fa = f_drop + 2 + 14 * j
        P.rise(f"say.c{j}", [fa, f_off], t, "f", sx, fy + j * 0.036, acc, 1.0, n=7, dy=0.01, fade=5)
        P.events.append(fa)

    # ---- the receipt ---------------------------------------------------------------------
    f_r0 = f_after1
    P.page("bill.page", [f_r0, f_end], page, page, grain=0.012)
    bill = script["receipt"]
    row_h = 0.0315
    px0, px1 = 0.17, 0.83
    slot = d.get("slot_y", 0.795)
    top_pad, n_rows = 0.05, len(bill["rows"])
    voices["q"]["px"] = R.fit_px("q", bill["total"], (px1 - px0 - 0.08) * W, 70)
    cta = script["cta"]
    offs, off = {}, top_pad
    offs["head"] = off
    off += 0.055
    for j in range(n_rows):
        offs[f"r{j}"] = off + j * row_h
    off += (n_rows - 1) * row_h + 0.06
    offs["total"] = off
    off += 0.062
    for j in range(len(cta["rows"][:2])):
        offs[f"c{j}"] = off + j * 0.032
    body_h = off + (len(cta["rows"][:2]) - 1) * 0.032 + 0.04
    feed_n = int(d.get("feed_f", 64))
    steps = n_rows + 5
    feed = []
    for q in range(f_end - f_r0):
        u = min(q / feed_n, 1.0) * steps
        feed.append(body_h * min((math.floor(u) + ease_io(6)[min(int((u % 1.0) * 6), 5)]) / steps, 1.0))
    P.items.append(dict(id="bill.paper", k="flash", f=[f_r0, f_end], mode="fill", c=d["paper"], a=1, rect=[[px0, r4(slot - v), px1, slot] for v in feed], z=1))
    P.items.append(dict(id="bill.slot", k="flash", f=[f_r0, f_end], mode="fill", c="#000000", a=1, rect=[px0 - 0.03, slot, px1 + 0.03, slot + 0.007], z=2))
    pin = d["paper_ink"]

    def printed(iid, t, v, x, o, align="l", op=1.0):
        """A line of the receipt: it exists from the frame its baseline clears the slot, and rides the paper up."""
        ys = [slot - fv + o for fv in feed]
        q0 = next((q for q, y in enumerate(ys) if y <= slot - 0.003), None)
        if q0 is not None:
            P.word(iid, [f_r0 + q0, f_end], t, v, x, ys[q0:], pin, op, align=align)
        return q0

    printed("bill.head", bill["head"], "h", (px0 + px1) / 2, offs["head"], align="c")
    for j, (lab, val) in enumerate(bill["rows"]):
        printed(f"bill.l{j}", lab, "r", px0 + 0.04, offs[f"r{j}"], op=0.6)
        q0 = printed(f"bill.v{j}", val, "r", px1 - 0.04, offs[f"r{j}"], align="r")
        if q0 is not None:                                # the printer's tick as each row clears the slot
            P.hit(f"bill.t{j}", "tick_1", f_r0 + q0, d["tick_db"] - 2.0)
    printed("bill.total", bill["total"], "q", px0 + 0.04, offs["total"])
    for j, t in enumerate(cta["rows"][:2]):
        printed(f"bill.cta{j}", t, "r", (px0 + px1) / 2, offs[f"c{j}"], align="c", op=1.0 if j == 0 else 0.6)
    P.hit("bill", "whoosh_soft_1", f_r0 + 2, d["whoosh_db"])
    P.events.append(f_r0 + feed_n)

    # ---- sound: the first cut's voice to the send, the new cut's voice from the cut --------
    f_v0 = int(round(float(d.get("before_voice_from_s", lead_in(sup["ffmpeg"], A["path"]) if ta0 == 0 else 0.0)) * FPS))
    t_stop = (quiet_point(sup["ffmpeg"], A["path"], ta0 + (f_send - 14) / FPS, ta0 + (f_send + 4) / FPS) if d.get("before_voice_end_s") is None
              else float(d["before_voice_end_s"]))
    f_va1 = int(round((t_stop - ta0) * FPS))
    P.voice([f_v0, f_va1], "a", ta0 + f_v0 / FPS)
    want = min(tb0 + (f_after1 - f_cut - 4) / FPS, dur_b - 0.05)
    t_endb = quiet_point(sup["ffmpeg"], B["path"], want - 0.9, want) if d.get("after_voice_end_s") is None else float(d["after_voice_end_s"])
    f_vb1 = f_cut + int(round((t_endb - tb0) * FPS))
    P.voice([f_cut, f_vb1], "b", tb0)
    l1 = P.done(sup.get("sid", "wf2"), f_end, seed)
    mid = (f_push1 + f_after1) // 2
    beats = dict(open=[0, f_open1], pull=[f_open1, f_pull1], type=[f_type0, f_send], change=[f_send, f_cut], watch=[f_cut, f_push0],
                 push=[f_push0, f_push1], after=[f_push1, f_after1], receipt=[f_r0, f_end], events=sorted(set(P.events)), exports=P.exports,
                 cta_kind="receipt", removed_s=r4(removed_s), added_pictures=added, pieces=[[r4(s), r4(e), ia, ib] for s, e, ia, ib in pieces],
                 timeline=dict(x0=X0, x1=X1, pps=r4(pps), track_v=[r4(yV), r4(yV + hV)], cut_f=f_cut, before_in_s=ta0, after_in_s=tb0,
                               dur=[r4(dur_a), r4(dur_b)]),
                 export_check=dict(f=mid, asset="b", t=r4(t_after + (mid - f_push1 + 0.5) / FPS)),
                 own_pages=[[f_pull1, f_push0], [f_r0, f_end]], on_screen=P.shown,
                 look=sorted({0, f_open1 + 6, f_pull1 + 2, f_type0 + n_chars // 2, f_send - 1, f_send + 8, f_drop + 6, f_drop + 14, f_cut - 1,
                              f_cut + 20, f_push0 + 8, f_push1 + 40, f_after1 - 2, f_r0 + 20, f_r0 + feed_n // 2 + 10, f_r0 + feed_n + 6, f_end - 2}),
                 seats=dict(viewer=vbox, sentence=[sx, r4(sy)], slot=slot))
    return l1, beats


STYLES = {"reading-wipe": reading_wipe, "one-clip-three-films": one_clip_three_films, "edits-itself": edits_itself}


def look_frames(beats):
    """Frames a director should look at before the render."""
    return sorted(set(int(f) for f in beats["look"]))


# ----------------------------------------------------------------- the film's sound

def audio_master(ffmpeg, l1, out, tmp):
    """The film's sound, as one file: the voice runs of the plan's spine, each from the export it names, and each
    library sound with its peak on its event, levelled to -14 LUFS. No music, no bed: silence is silence."""
    import numpy as np
    sr = 48000
    n = int(round(l1["dur_f"] / FPS * sr))
    mix = np.zeros((n, 2), np.float32)

    def pcm(path):
        r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0?", "-vn", "-ac", "2", "-ar", str(sr),
                            "-f", "f32le", "pipe:1"], capture_output=True, timeout=600)
        return np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()

    cache = {}
    fade = int(0.006 * sr)
    for e in l1.get("spine") or []:
        p = l1["assets"][e.get("a", "main")]["path"]
        if p not in cache:
            cache[p] = pcm(p)
        at, to = int(round(e["f"][0] / FPS * sr)), min(int(round(e["f"][1] / FPS * sr)), n)
        s0 = int(round(e["src"][0] * sr))
        seg = cache[p][s0:s0 + (to - at)].copy()
        if len(seg) > 2 * fade:
            seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
            seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
        mix[at:at + len(seg)] += seg
    lib_dir = os.path.join(scene_worker(), "sound_library")
    for s in l1.get("sound") or []:
        p = os.path.join(lib_dir, os.path.basename(s["file"]))
        if p not in cache:
            cache[p] = pcm(p)
        smp = cache[p][int(round(float(s.get("trim_s", 0)) * sr)):]
        at = int(round(float(s["start_s"]) * sr))
        if at >= n or not len(smp):
            continue
        smp = smp[:n - at] * (10 ** (float(s.get("db", 0)) / 20))
        mix[at:at + len(smp)] += smp
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
    return dict(spine=len(l1.get("spine") or []), sounds=len(l1.get("sound") or []), lufs_in=float(j["input_i"]))


# ----------------------------------------------------------------- measured: taste on the plan, truth on the film

def _rest(v):
    return v[-1] if isinstance(v, list) else v


def taste_check(l1, beats):
    """TASTE.md on the plan, as numbers. Returns {ok, problems}: every added word inside the safe area at rest and off
    the mark, big enough to read, at most three kinds of type at once, nothing drawn over a real export played whole,
    every sound on an event of the film."""
    R = Ruler(l1["voices"])
    bad = []
    words = [i for i in l1["items"] if i["k"] == "word"]
    for it in words:
        v = l1["voices"][it["v"]]
        x, y = _rest(it["x"]), _rest(it["y"])
        x1, y0 = x + R.width(it["v"], it["t"]) / W, y - R.cap(it["v"]) / H
        if x < SAFE[0] - 1e-3 or x1 > SAFE[2] + 1e-3 or y0 < SAFE[1] - 1e-3 or y > SAFE[3] + 1e-3:
            bad.append(f"{it['id']}: outside the safe area ({x:.3f},{y0:.3f})-({x1:.3f},{y:.3f})")
        if y > 0.55 and x1 > RAIL_X + 2e-3:
            bad.append(f"{it['id']}: under the platform's right rail (x1 {x1:.3f})")
        if x < MARK[2] and x1 > MARK[0] and y0 < MARK[3] and y > MARK[1]:
            bad.append(f"{it['id']}: in the watermark's keep-out")
        if float(v["px"]) < 38:
            bad.append(f"{it['id']}: {v['px']} px is under the reading floor")
    for a, b in beats.get("exports") or []:
        over = [i["id"] for i in l1["items"] if i["k"] in ("word", "card", "flash") and i["f"][0] < b and i["f"][1] > a]
        if over:
            bad.append(f"frames {a}-{b}: a real export is drawn over by {over[:3]}")
    for f in range(0, l1["dur_f"], 3):
        kinds = {i["v"] for i in words if i["f"][0] <= f < i["f"][1]}
        if len(kinds) > 3:
            bad.append(f"frame {f}: {len(kinds)} kinds of type at once")
            break
    events = set(beats.get("events") or [])
    for s in l1.get("sound") or []:
        if not events or min(abs(s["event_f"] - e) for e in events) > 3:
            bad.append(f"sound {s['id']} at frame {s['event_f']} has nothing landing with it")
    n_s = len(l1.get("sound") or [])
    return dict(ok=not bad, problems=bad, words=len(words), sounds=n_s, sounds_per_10s=round(n_s / (l1["dur_f"] / FPS) * 10, 2))


def _gray(ffmpeg, path, t, w=W, h=H):
    import numpy as np
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8)[: w * h].reshape(h, w).astype(np.float32) / 255.0


def _psnr(x, y):
    mse = float(((x - y) ** 2).mean())
    return round(10 * math.log10(1.0 / max(mse, 1e-9)), 1)


def film_checks(ffmpeg, film, l1, beats):
    """Measured on the engine's film: the export is shown as it was delivered; where the film says pictures are the
    same instant, each is the frame of its own export at that instant; a timeline's playhead stands on the second
    the viewer shows; and a keyword in static is in no one frame."""
    import numpy as np
    out = {}
    ec = beats["export_check"]
    x = _gray(ffmpeg, film, (ec["f"] + 0.5) / FPS)
    y = _gray(ffmpeg, l1["assets"][ec["asset"]]["path"], ec["t"])
    out["export_psnr_db"] = _psnr(x, y)
    out["export_unaltered"] = out["export_psnr_db"] >= 32.0
    ok = out["export_unaltered"]
    sc = beats.get("sync_check")
    if sc:
        fr = _gray(ffmpeg, film, (sc["f"] + 0.5) / FPS)
        rows = []
        for i, (b, t) in enumerate(zip(sc["boxes"], sc["t"])):
            x0, y0, x1, y1 = int(round(b[0] * W)), int(round(b[1] * H)), int(round(b[2] * W)), int(round(b[3] * H))
            ref = _gray(ffmpeg, l1["assets"][f"e{i}"]["path"], t, x1 - x0, y1 - y0)
            m = 10
            rows.append(_psnr(fr[y0 + m:y1 - m, x0 + m:x1 - m], ref[m:-m, m:-m]))
        out["sync_psnr_db"] = rows
        out["same_instant"] = min(rows) >= 24.0
        ok = ok and out["same_instant"]
    if beats.get("cta_kind") == "static":
        cell = 12
        mask = np.zeros((H // cell, W // cell), np.float32)
        for it in l1["items"]:
            if it["id"].startswith("cta.w"):
                c0 = int(round((it["x"] - it["w"] / 2) * W / cell))
                r0 = int(round((it["y"] - it["h"] / 2) * H / cell))
                mask[r0, c0:c0 + int(round(it["w"] * W / cell))] = 1.0
        f0 = beats["cta"][0] + 30
        fr = [_gray(ffmpeg, film, (f0 + k + 0.5) / FPS) for k in range(3)]
        cells = [g.reshape(H // cell, cell, W // cell, cell).mean(axis=(1, 3)) for g in fr]
        box = np.where(mask.any(1))[0]
        r0, r1 = int(box.min()) - 4, int(box.max()) + 5
        m = mask[r0:r1]

        def corr(p, q):
            p, q = p - p.mean(), q - q.mean()
            return float((p * q).sum() / (math.sqrt(float((p * p).sum()) * float((q * q).sum())) + 1e-9))

        import cv2
        out["still_corr"] = round(abs(corr(cv2.blur(cells[1][r0:r1], (3, 3)), cv2.blur(m, (3, 3)))), 3)
        up = np.abs(cells[1][r0:r1] - cells[0][r0 + 1:r1 + 1])
        dn = np.abs(cells[1][r0:r1] - cells[0][r0 - 1:r1 - 1])
        out["motion_corr"] = round(corr(cv2.blur(dn - up, (3, 3)), cv2.blur(m, (3, 3))), 3)
        out["pause_proof"] = out["still_corr"] < 0.12 and out["motion_corr"] > 0.45
        ok = ok and out["pause_proof"]
    tlm = beats.get("timeline")
    if tlm:
        rows = []
        for fr_ in (beats["type"][0] + 4, beats["watch"][0] + 12):
            g = _gray(ffmpeg, film, (fr_ + 0.5) / FPS)
            t = tlm["before_in_s"] + fr_ / FPS if fr_ < tlm["cut_f"] else tlm["after_in_s"] + (fr_ - tlm["cut_f"]) / FPS
            want = (tlm["x0"] + t * tlm["pps"]) * W
            y0 = int((tlm["track_v"][0] - 0.010) * H)
            xa = int(tlm["x0"] * W) - 4
            band = g[y0:y0 + 14, xa:int(tlm["x1"] * W) + 4].mean(0)
            rows.append(dict(f=fr_, want_px=round(want, 1), got_px=int(band.argmax()) + xa))
        out["playhead"] = rows
        out["playhead_true"] = all(abs(r["want_px"] - r["got_px"]) <= 4 for r in rows)
        ok = ok and out["playhead_true"]
    out["ok"] = bool(ok)
    return out
