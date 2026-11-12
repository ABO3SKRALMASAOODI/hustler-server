"""The marketing films, as scene plans (L1) for Valmera's engine.

A style is a function: (script, supply, direction) -> L1. Everything is baked here: seats, curves,
the seam's path, the count. The engine reads numbers and draws. Type is measured with the engine's
own shaper, so a line sits where this file says it sits.

Devices (atlas/ALIEN.md):
  T5 Reading Wipe   the seam of a before/after is the caret of the sentence being read; what the
                    caret has passed is already the edit, and the sentence is spent as it is read
  A1 Pause-Proof    a word that exists only in motion: static inside the letters runs one way,
                    static outside the other; any single frame is noise
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

def probe(ffprobe, path):
    r = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,avg_frame_rate:format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    s = d["streams"][0]
    n, den = (s.get("avg_frame_rate") or "30/1").split("/")
    return dict(w=int(s["width"]), h=int(s["height"]), fps=round(float(n) / float(den or 1), 3),
                dur=round(float(d["format"]["duration"]), 3))


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


# ----------------------------------------------------------------- style: Reading Wipe Before/After

def reading_wipe(script, sup, direction=None):
    """Raw footage and one sentence. The caret that reads the sentence is the seam: behind it the frame
    is already the delivered short. Then the short, others like it, the whole delivery counted, and a
    word that only exists while it moves."""
    d = dict(hold_f=15, word_f=8, reach_f=3, leave_f=11, hero_out_s=7.38, resume_s=10.55, resume_end_s=13.1,
             salvo_f=10, shrink_f=24, fill_lead_f=10, fill_f=45, wall_hold_f=48, cta_f=99, lean=6.0, page="#0A0A0A",
             tick_db=-18.0, whoosh_db=-21.0, riser_db=-15.0)
    d.update(direction or {})
    hero, reg = sup["hero"], sup["hero"]["reg"]
    voices = {
        "s": {"font": "InterDisplay-Bold.ttf", "px": 60, "track": -1.5},      # the sentence
        "t": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 5},         # labels meant to be read (TASTE P5: 0.02 h)
        "c": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 7},         # the source credit, one at a time
        "m": {"font": "InterDisplay-Bold.ttf", "px": 40, "track": 0},         # the row under the count
        "n": {"font": "InterDisplay-Black.ttf", "px": 120, "track": -2},      # the count
        "k": {"font": "InterDisplay-Black.ttf", "px": 196, "track": 0},       # the keyword
    }
    R = Ruler(voices)
    items, sounds = [], []
    lib = sup["sounds"]
    rx0, ry0, rx1, ry1 = reg["rect"]

    # ---- beat 1: the raw page, the sentence, the caret ---------------------------------
    sentence = script["sentence"].strip()
    words = sentence.split()
    x_lo, x_hi = 0.07, RAIL_X                                            # one left edge with the tag; clear of the right rail
    voices["s"]["px"] = R.fit_px("s", sentence, (x_hi - x_lo) * W, 62)
    sx0 = x_lo
    sy = d.get("sentence_y", min(ry1 + 0.058, SAFE[3] - 0.02))          # baseline, where the short sets its headline
    # word places, and where the caret stands after each word
    places, stops = [], []
    for i, wd in enumerate(words):
        pre = " ".join(words[:i]) + (" " if i else "")
        x = sx0 + R.width("s", pre) / W
        places.append(x)
        end = x + R.width("s", wd) / W
        nxt = sx0 + R.width("s", " ".join(words[:i + 1]) + " ") / W if i + 1 < len(words) else end + 0.012
        stops.append((end + nxt) / 2 if i + 1 < len(words) else end + 0.012)
    caret0 = sx0 - 0.014
    lean = float(d["lean"])
    tan = math.tan(math.radians(lean))
    # the engine takes the seam's x at mid height and leans it by tan(lean) per pixel down the frame
    # (a positive lean puts its top to the left); place it so the line passes the caret at the baseline

    def seam_mid(x_caret):
        return x_caret + (0.5 - sy) * H * tan / W

    f_wipe0 = int(d["hold_f"])
    xs = [seam_mid(caret0)] * f_wipe0
    cur = caret0
    marks = []                                                           # frames on which the caret starts a word
    for st in stops:
        marks.append(len(xs))
        e = expo_out(int(d["reach_f"]) + 1)[1:]
        xs += [seam_mid(cur + (st - cur) * v) for v in e]
        xs += [seam_mid(st)] * (int(d["word_f"]) - int(d["reach_f"]))
        cur = st
    far = 1.0 + abs(0.5 * H * tan / W) + 0.03
    xs += [seam_mid(cur) + (far - seam_mid(cur)) * (v ** 2.2) for v in ease_io(int(d["leave_f"]) + 1)[1:]]
    f_wipe1 = len(xs)                                                    # the short is the whole frame from here

    items.append(dict(id="raw.page", k="shot", f=[0, f_wipe1], pic="plate", plate=dict(c0=d["page"], c1=d["page"], grain=0.0)))
    items.append(dict(id="raw.pic", k="card", f=[0, f_wipe1], z=1, pic="raw", src=[0.0, f_wipe1 / FPS], crop=reg["crop"],
                      x=r4((rx0 + rx1) / 2), y=r4((ry0 + ry1) / 2), w=r4(rx1 - rx0), h=r4(ry1 - ry0), s=1, rot=0, op=1))
    tag_x = 0.07
    items.append(dict(id="raw.tag0", k="word", f=[0, f_wipe1], t=script["tag_raw"], v="t", x=tag_x, y=0.1405, ink="#FFFFFF", op=0.9, role="big", on=0))
    items.append(dict(id="raw.tag1", k="word", f=[0, f_wipe1], t=script["tag_source"], v="c", x=tag_x, y=0.1665, ink="#FFFFFF", op=0.5, role="big", on=0))
    for i, wd in enumerate(words):
        items.append(dict(id=f"raw.s{i}", k="word", f=[0, f_wipe1], t=wd, v="s", x=r4(places[i]), y=r4(sy), ink="#FFFFFF", op=0.94, role="big", on=0))
    items.append(dict(id="wipe", k="join", f=[0, f_wipe1], mode="seam", stage="top", pic="main", src=[0.0, f_wipe1 / FPS],
                      x=[r4(v) for v in xs], lean=lean, edge=dict(w=1.6, a=0.9), dim=1.0, a=1.0))
    for i, m in enumerate(marks):
        sounds.append(sound(f"key{i}", "tick_1", lib, m + 1, d["tick_db"]))

    # ---- beat 2: the delivered short, untouched -----------------------------------------
    t_out = quiet_point(sup["ffmpeg"], hero["path"], d["hero_out_s"] - 0.15, d["hero_out_s"] + 0.15)
    f_hero1 = int(round(t_out * FPS))
    items.append(dict(id="hero", k="shot", f=[f_wipe1, f_hero1], pic="main", src=[f_wipe1 / FPS, f_hero1 / FPS]))

    # ---- beat 3: others like it, one after another --------------------------------------
    f = f_hero1
    salvo = sup["salvo"]
    for i, s in enumerate(salvo):
        n = int(d["salvo_f"])
        items.append(dict(id=f"salvo{i}", k="shot", f=[f, f + n], pic=f"e{i}", src=[s["t"], s["t"] + n / FPS]))
        f += n
    f_wall0 = f
    last = salvo[-1]
    t_last = last["t"] + int(d["salvo_f"]) / FPS

    # ---- beat 4: the delivery, counted ---------------------------------------------------
    tiles = sup["tiles"]
    n_t = len(tiles)
    cols, rows_ = sup["grid"]
    gut = 4
    bx0 = 0.07 * W                                                # the film's one left edge: tag, sentence, sheet, count
    tw = d.get("tile_px") or math.floor(((RAIL_X * W - bx0) - (cols - 1) * gut) / cols)     # as wide as fits left of the right rail
    th = tw * 16.0 / 9.0
    bw, bh = cols * tw + (cols - 1) * gut, rows_ * th + (rows_ - 1) * gut
    by0 = d.get("block_y", 0.205) * H
    nf_shrink, nf_fill, nf_hold = int(d["shrink_f"]), int(d["fill_f"]), int(d["wall_hold_f"])
    f_fill0 = f_wall0 + nf_shrink - int(d["fill_lead_f"])        # the first tiles land while the live one settles
    f_lock = f_fill0 + nf_fill
    f_wall1 = f_lock + nf_hold
    items.append(dict(id="wall.page", k="shot", f=[f_wall0, f_wall1], pic="plate", plate=dict(c0="#141414", c1="#070707", grain=0.035)))

    def centre(i):
        c, r = i % cols, i // cols
        return (bx0 + c * (tw + gut) + tw / 2) / W, (by0 + r * (th + gut) + th / 2) / H

    e_idx = next(i for i, t in enumerate(tiles) if t["sid"] == last["sid"])
    ex, ey = centre(e_idx)
    z0 = W / tw                                                   # the tile as the whole frame
    px_, py_ = (0.5 - ex * z0) / (1 - z0), (0.5 - ey * z0) / (1 - z0)   # the fixed point of the pull-back
    zs = [z0 ** (1.0 - u) for u in expo_out(nf_shrink + 1)[1:]] + [1.0] * (f_wall1 - f_wall0 - nf_shrink)
    items.append(dict(id="wall.live", k="card", f=[f_wall0, f_wall1], z=3, pic=f"e{len(salvo) - 1}",
                      src=[t_last, t_last + (f_wall1 - f_wall0) / FPS], w=r4(tw / W), h=r4(th / H),
                      x=[r4(px_ + (ex - px_) * z) for z in zs], y=[r4(py_ + (ey - py_) * z) for z in zs],
                      s=[r4(z) for z in zs], rot=0, op=1))
    # the rest land in reading order; the number is the tiles on screen
    order = [i for i in range(n_t) if i != e_idx]
    land = {}
    for j, i in enumerate(order):
        land[i] = f_fill0 + int(j * nf_fill / len(order))
    pop = 5
    f_slots = f_wall0                                             # the empty sheet is under the live tile as it settles: every slot, waiting
    for i in order:
        a = land[i]
        cx, cy = centre(i)
        ramp = [0.07 * min(1.0, (q + 1) / 6.0) for q in range(a - f_slots)]
        items.append(dict(id=f"wall.s{i:03d}", k="flash", f=[f_slots, a], mode="fill", c="#FFFFFF", a=[r4(v) for v in ramp], z=0,
                          rect=[r4(cx - tw / W / 2), r4(cy - th / H / 2), r4(cx + tw / W / 2), r4(cy + th / H / 2)]))
        s_list = settle(pop) + [1.0] * (f_wall1 - a - pop)
        o_list = [min(1.0, 0.35 + 0.65 * q / (pop - 2)) for q in range(pop)] + [1.0] * (f_wall1 - a - pop)
        items.append(dict(id=f"wall.t{i:03d}", k="card", f=[a, f_wall1], z=2, pic=f"t{i:03d}", w=r4(tw / W), h=r4(th / H),
                          x=r4(cx), y=r4(cy), s=[r4(v) for v in s_list], rot=0, op=[r4(v) for v in o_list]))
    # type: the count as wide as the mosaic, one small row, and the source tag where it was on the raw page
    left = bx0 / W
    count_line = f"{n_t} {script['count_noun']}"
    voices["n"]["px"] = R.fit_px("n", count_line, bw, 168)
    cap_n = R.cap("n")
    ny = (by0 + bh) / H + 0.022 + cap_n / H
    num_w = R.width("n", f"{n_t} ")
    counts = {}
    for fr in range(f_fill0, f_lock + 1):
        n_on = 1 + sum(1 for i in order if land[i] <= fr)
        counts.setdefault(n_on, [fr, fr])[1] = fr
    keys = sorted(counts)
    for q, n_on in enumerate(keys):
        a = counts[n_on][0]
        b = counts[keys[q + 1]][0] if q + 1 < len(keys) else f_wall1
        txt = str(n_on)
        xr = left + (num_w - R.width("n", " ") - R.width("n", txt)) / W      # right-aligned to the noun
        items.append(dict(id=f"wall.n{n_on:03d}", k="word", f=[a, b], t=txt, v="n", x=r4(xr), y=r4(ny), ink="#FFFFFF", role="big", on=a))
    items.append(dict(id="wall.noun", k="word", f=[f_fill0, f_wall1], t=script["count_noun"], v="n", x=r4(left + num_w / W), y=r4(ny),
                      ink="#FFFFFF", role="big", on=f_fill0))
    small_y = ny + 0.04
    n_small = 8
    items.append(dict(id="wall.small", k="word", f=[f_lock, f_wall1], t=script["small_row"], v="m", x=r4(left), y=r4(small_y), ink="#FFFFFF",
                      op=[r4(0.7 * v) for v in ease_io(n_small)] + [0.7] * (f_wall1 - f_lock - n_small), role="big", on=f_lock))
    items.append(dict(id="wall.tag", k="word", f=[f_fill0, f_wall1], t=script["tag_source"], v="c", x=r4(left), y=0.1665, ink="#FFFFFF", op=0.5, role="big", on=f_fill0))
    sounds.append(sound("move", "whoosh_soft_1", lib, f_wall0 + 2, d["whoosh_db"]))
    sounds.append(sound("lock", "riser_2", lib, f_lock, d["riser_db"]))

    # ---- beat 5: the word that only exists while it moves ---------------------------------
    f_cta0, f_cta1 = f_wall1, f_wall1 + int(d["cta_f"])
    cta = script["cta"]
    seed = int(d.get("seed", 1112))
    items.append(dict(id="cta.static", k="shot", f=[f_cta0, f_cta1], pic="plate", plate=dict(kind="static", cell=12, speed=12, seed=seed, c0="#000000", c1="#FFFFFF")))
    cell = 12
    voices["k"]["px"] = R.fit_px("k", cta["word"], 0.84 * W, 232)
    kw = R.width("k", cta["word"])
    kcap = R.cap("k")
    kx = round((0.5 - kw / W / 2) * W / cell) * cell
    ky = round((0.47 * H + kcap / 2) / cell) * cell
    inside = dict(kind="static", cell=cell, speed=-12, seed=seed, c0="#000000", c1="#FFFFFF")
    for j, (cx0, cy0, n_c) in enumerate(cell_runs(cta["word"], R.T.find_font(voices["k"]["font"]), voices["k"]["px"], kx, ky, cell)):
        items.append(dict(id=f"cta.w{j:03d}", k="card", f=[f_cta0, f_cta1], z=1, pic="plate", plate=inside,
                          x=r4((cx0 + n_c * cell / 2) / W), y=r4((cy0 + cell / 2) / H), w=r4(n_c * cell / W), h=r4(cell / H), s=1, rot=0, op=1))
    rows_txt = [r for r in cta["rows"] if r][:2]
    rw = max(R.width("t", r) for r in rows_txt)
    pad_x, row_h = 34, 56
    half = math.ceil((rw / 2 + pad_x) / cell) * cell                 # the ground is whole cells too: no cut cell gives the word's row away
    sx = max((W / 2 - half) / W, SAFE[0])
    y_top = round(0.66 * H / cell) * cell / H
    strip = [r4(sx), r4(y_top), r4(1 - sx), r4(y_top + math.ceil((len(rows_txt) * row_h + 40) / cell) * cell / H)]
    items.append(dict(id="cta.ground", k="flash", f=[f_cta0, f_cta1], mode="fill", c="#000000", a=1, rect=strip, z=1))
    for i, r in enumerate(rows_txt):
        items.append(dict(id=f"cta.row{i}", k="word", f=[f_cta0, f_cta1], t=r, v="t", x=r4(0.5 - R.width("t", r) / W / 2),
                          y=r4(y_top + (52 + i * row_h) / H), ink="#FFFFFF", op=0.94 if i == 0 else 0.7, role="big", on=f_cta0))
    dur = f_cta1

    # ---- sound: the short's own voice, one speech edit, then nothing ----------------------
    t_in = quiet_point(sup["ffmpeg"], hero["path"], d["resume_s"] - 0.2, d["resume_s"] + 0.12)
    t_end = quiet_point(sup["ffmpeg"], hero["path"], d["resume_end_s"] - 0.15, d["resume_end_s"] + 0.3)
    n2 = min(int(round((t_end - t_in) * FPS)), f_cta0 - f_hero1)
    f_v0 = int(round(d.get("voice_from_s", lead_in(sup["ffmpeg"], hero["path"])) * FPS))     # past a cut-off syllable at the head
    spine = [dict(f=[f_v0, f_hero1], src=[r4(f_v0 / FPS), r4(f_hero1 / FPS)]), dict(f=[f_hero1, f_hero1 + n2], src=[r4(t_in), r4(t_in + n2 / FPS)])]

    assets = {"main": dict(kind="video", path=hero["path"], **hero["probe"]),
              "raw": dict(kind="video", path=hero["raw"]["path"], **hero["raw"]["probe"])}
    for i, s in enumerate(salvo):
        assets[f"e{i}"] = dict(kind="video", path=s["path"], **s["probe"])
    for i, t in enumerate(tiles):
        if i != e_idx:
            assets[f"t{i:03d}"] = dict(kind="image", path=t["path"], w=t["w"], h=t["h"])
    order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
    items.sort(key=lambda it: (it["f"][0], order_k[it["k"]], it.get("z", 0), it["id"]))
    l1 = dict(l1=1, sid=sup.get("sid", "wf2"), seed=seed, fps=FPS, frame=[W, H], dur_f=dur, assets=assets, spine=spine, voices=voices,
              film=dict(grain=0.0, vignette=0.0, over_type=False,
                        grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0)),
              items=items, sound=sounds)
    beats = dict(wipe=[0, f_wipe1], hero=[f_wipe1, f_hero1], salvo=[f_hero1, f_wall0], shrink=[f_wall0, f_fill0], fill=[f_fill0, f_lock],
                 wall_hold=[f_lock, f_wall1], cta=[f_cta0, f_cta1], caret_marks=marks, speech_edit=[r4(f_hero1 / FPS), r4(t_in)],
                 voice_starts_f=f_v0, voice_ends_f=f_hero1 + n2, seats=dict(sentence_y=r4(sy), count_y=r4(ny), small_y=r4(small_y), block=[r4(bx0 / W), r4(by0 / H), r4((bx0 + bw) / W), r4((by0 + bh) / H)], cta_strip=strip))
    return l1, beats


STYLES = {"reading-wipe": reading_wipe}


def look_frames(beats):
    """Frames a director should look at before the render: every beat's start, middle and landing."""
    fs = set()
    for k in ("wipe", "hero", "salvo", "shrink", "fill", "wall_hold", "cta"):
        a, b = beats[k]
        fs.update({a, (a + b) // 2, b - 1})
    fs.update(m + 3 for m in beats.get("caret_marks", []))
    return sorted(fs)


# ----------------------------------------------------------------- measured: taste on the plan, truth on the film

def taste_check(l1, beats):
    """TASTE.md on the plan, as numbers. Returns {ok, problems}: every added word inside the safe area and off the
    mark, big enough to read, at most three rows at once, nothing drawn over a real export, every sound on an event."""
    R = Ruler(l1["voices"])
    bad = []
    words = [i for i in l1["items"] if i["k"] == "word"]
    for it in words:
        v = l1["voices"][it["v"]]
        x = it["x"] if not isinstance(it["x"], list) else min(it["x"])
        y = it["y"] if not isinstance(it["y"], list) else max(it["y"])
        x1, y0 = x + R.width(it["v"], it["t"]) / W, y - R.cap(it["v"]) / H
        if x < SAFE[0] - 1e-3 or x1 > SAFE[2] + 1e-3 or y0 < SAFE[1] - 1e-3 or y > SAFE[3] + 1e-3:
            bad.append(f"{it['id']}: outside the safe area ({x:.3f},{y0:.3f})-({x1:.3f},{y:.3f})")
        if y > 0.55 and x1 > RAIL_X + 2e-3:
            bad.append(f"{it['id']}: under the platform's right rail (x1 {x1:.3f})")
        if x < MARK[2] and x1 > MARK[0] and y0 < MARK[3] and y > MARK[1]:
            bad.append(f"{it['id']}: in the watermark's keep-out")
        if float(v["px"]) < (23 if it["v"] == "c" else 38):
            bad.append(f"{it['id']}: {v['px']} px is under the reading floor")
    real = [i for i in l1["items"] if i["k"] == "shot" and i.get("pic") not in (None, "plate")]
    for sh in real:
        over = [i["id"] for i in l1["items"] if i["k"] in ("word", "card", "flash") and i["f"][0] < sh["f"][1] and i["f"][1] > sh["f"][0]]
        if over:
            bad.append(f"{sh['id']}: a real export is drawn over by {over[:3]}")
    for f in range(0, l1["dur_f"], 3):
        rows = {round((i["y"] if not isinstance(i["y"], list) else i["y"][0]), 3) for i in words if i["f"][0] <= f < i["f"][1]}
        if len(rows) > 3:
            bad.append(f"frame {f}: {len(rows)} rows of type")
            break
    events = set(m + 1 for m in beats.get("caret_marks", [])) | {beats["shrink"][0] + 2, beats["fill"][1]}
    for s in l1.get("sound") or []:
        if min(abs(s["event_f"] - e) for e in events) > 3:
            bad.append(f"sound {s['id']} at frame {s['event_f']} has nothing landing with it")
    return dict(ok=not bad, problems=bad, words=len(words), sounds=len(l1.get("sound") or []))


def _gray(ffmpeg, path, t, w=W, h=H):
    import numpy as np
    r = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8)[: w * h].reshape(h, w).astype(np.float32) / 255.0


def film_checks(ffmpeg, film, l1, beats):
    """Measured on the engine's film: the export is shown as it was delivered, and the keyword is not in any one frame."""
    import numpy as np
    out = {}
    a, b = beats["hero"]
    f = (a + b) // 2
    hero = l1["assets"]["main"]["path"]
    x, y = _gray(ffmpeg, film, (f + 0.5) / FPS), _gray(ffmpeg, hero, (f + 0.5) / FPS)
    mse = float(((x - y) ** 2).mean())
    out["export_psnr_db"] = round(10 * math.log10(1.0 / max(mse, 1e-9)), 1)
    out["export_unaltered"] = out["export_psnr_db"] >= 32.0
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
    up = np.abs(cells[1][r0:r1] - cells[0][r0 + 1:r1 + 1])           # the cell below, one frame ago: static moving up
    dn = np.abs(cells[1][r0:r1] - cells[0][r0 - 1:r1 - 1])           # the cell above, one frame ago: static moving down
    out["motion_corr"] = round(corr(cv2.blur(dn - up, (3, 3)), cv2.blur(m, (3, 3))), 3)
    out["pause_proof"] = out["still_corr"] < 0.12 and out["motion_corr"] > 0.45
    out["cta_mean_step"] = round(abs(float(fr[1].mean()) - float(_gray(ffmpeg, film, (beats["cta"][0] - 2 + 0.5) / FPS).mean())), 3)
    return out
