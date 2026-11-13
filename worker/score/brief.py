"""The Brief: what the Director reads. Built under its cap by construction (Director DESIGN 3.3).

    build(work, session, scout)          boards, film part, manifest, into <work>/brief/
    load(work) -> (system, content)      what the Direct call sends

The cached prefix is one fixed file, byte-identical across films, so the cache can hit. Over-long
parts are dropped by priority and recorded, never sent. Nothing here reads a word's meaning: stress
and pauses are printed from the signal.
"""
import base64
import concurrent.futures as cf
import json
import math
import os
import subprocess
import time

import numpy as np
from PIL import Image, ImageDraw

from . import boards, taste

HERE = os.path.dirname(os.path.abspath(__file__))
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
PREFIX = os.path.join(HERE, "prompts", "prefix.md")
STYLES = os.path.join(HERE, "assets", "styles.json")
STYLE_DIR = os.path.join(HERE, "assets", "styles")
SR = 16000
FINAL_W, FINAL_H = 1080, 1920
BOARD_W, BOARD_H = 1568, 880
PREFIX_CAP, FILM_CAP = 13600, 18000

GREEN, CYAN, YEL = (70, 240, 120), (90, 210, 255), (255, 225, 60)

def jload(path, default=None):
    if not os.path.exists(path):
        return default
    return json.load(open(path))


# ----------------------------------------------------------------- words


def decode(path):
    p = subprocess.run([FF, "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True, timeout=120)
    return np.frombuffer(p.stdout, dtype=np.float32)


def word_rms(x, t0, t1):
    a, b = int(t0 * SR), max(int(t0 * SR) + 400, int(t1 * SR))
    seg = x[a:b]
    if len(seg) < 8:
        return -80.0
    win = int(0.05 * SR)
    env = np.sqrt(np.convolve(seg ** 2, np.ones(win) / win, mode="same"))
    return 20 * math.log10(float(env.max()) + 1e-6)


def stress_row(ws, x):
    """Stress of each word of one sentence, from the signal only: louder and longer than its neighbours.
    Returns (z, level in dB, seconds)."""
    db = np.array([word_rms(x, w["t0"], w["t1"]) for w in ws]) if len(x) else np.zeros(len(ws))
    secs = np.array([w["t1"] - w["t0"] for w in ws])
    z = (db - db.mean()) / (db.std() + 1e-6) + 0.8 * (secs - secs.mean()) / (secs.std() + 1e-6)
    z[secs < 0.16] = -9                  # too short to carry stress: a signal gate, not a word list
    return z, db, secs


def stress_map(idx, x):
    """Word id -> measured stress, for the compiler: past what a film can carry, the devices on the words the
    voice stresses most are the ones that stay (taste.decide). The same number that prints the * marks."""
    words = idx.get("words") or []
    out = {}
    for s in idx.get("sentences") or []:
        ids = list(range(int(s["wi0"]), min(int(s["wi1"]), len(words) - 1) + 1))
        if not ids:
            continue
        z, _, _ = stress_row([words[i] for i in ids], x)
        for i, v in zip(ids, z):
            out[i] = round(float(max(v, -9.0)), 3)
    return out


def sentence_line(n, s, words, x, shown):
    """One sentence with its signal facts written into the text. Signal only: loudness and length."""
    ids = list(range(s["wi0"], s["wi1"] + 1))
    ws = [words[i] for i in ids]
    z, db, secs = stress_row(ws, x)
    letters = np.array([max(2, len(w["w"])) for w in ws])
    marks = set(int(i) for i in np.argsort(-z)[:max(1, round(len(ws) / 5))])
    ld = np.log(np.maximum(secs, 0.04) / letters)
    held = set(i for i in range(len(ws)) if ld[i] - ld.mean() > 1.1 * (ld.std() + 1e-6))
    out = []
    for i, wid in enumerate(ids):
        t = ws[i]["w"]
        doubt = float(ws[i].get("confidence", 1.0)) < 0.75 and wid not in shown
        if i in marks:
            t = f"*{t}*"
        elif i in held:
            t = f"~{t}~"
        if doubt:
            t += "?"
        if ws[i].get("filler"):
            t += "°"
        if wid in shown:
            t += "=>" + shown[wid].replace(" ", "_")
        out.append(f"{wid}:{t}")
        if i + 1 < len(ws):
            gap = ws[i + 1]["t0"] - ws[i]["t1"]
            if gap >= 0.25:
                out.append(f"|{gap:.2f}|")
            elif gap >= 0.08:
                out.append("·")
    dt = max(ws[-1]["t1"] - ws[0]["t0"], 0.1)
    return f"S{n} {ws[0]['t0']:.2f} {len(ws) / dt:.1f}w/s {db.mean():+.0f}dB  " + " ".join(out)


def words_part(work, idx, per, x, scout):
    words = idx.get("words") or []
    shown = {int(f["w"]): str(f["t"]) for f in (scout or {}).get("fixes", []) if str(f.get("w", "")).isdigit()}
    for k in list(shown):                  # a proposal that repeats the transcript is no proposal
        if 0 <= k < len(words) and shown[k] == words[k]["w"].strip(".,;:!?"):
            del shown[k]
    out = []
    if words:
        out.append("## Words")
        out.append("Each line: sentence, start second, pace, level, then id:word. *stressed*  ~held~  · short pause  |0.40| pause in seconds  ? the transcript doubts this word  ° a filler word  =>the Scout's hearing of a misheard word")
        sents = idx.get("sentences") or []
        for n, s in enumerate(sents, 1):
            out.append(sentence_line(int(str(s["id"]).lstrip("s")) if str(s.get("id", "")).lstrip("s").isdigit() else n, s, words, x, shown))
        w0, w1 = words[0]["t0"], words[-1]["t1"]
        dur = per["dur"]
        out.append(f"Speech runs {w0:.2f} to {w1:.2f} of {dur:.2f} s. Language {idx.get('language')}, {idx.get('speakers', 1)} speaker(s).")
        conf = float(np.mean([float(w.get("confidence", 1.0)) for w in words]))
        if conf < 0.75 or len(words) < 4:
            out.append(f"{len(words)} words at a mean transcript confidence of {conf:.2f}: this is likely a song, a crowd or noise rather than speech, and the language is a guess. "
                       "These words are not captioned (the compiler leaves them out) and no title is taken from them. Build the film on the picture and the sound.")
    else:
        out.append("## Words")
        out.append("No word ids in this upload. Anchor on `k` and `@`. Beats take `\"at\":\"@12.0..@15.5\"`.")
    return out


def audio_part(idx, per):
    """What the source sounds like, in numbers. Wordless films are built on this line."""
    a = per.get("audio") or {}
    p = idx.get("perception") or {}
    out = []
    if not a.get("rms_db"):
        return [f"Audio: {a.get('note', 'none')}."]
    line = f"Audio: {a.get('note')}."
    if p.get("bpm"):
        line += f" Tempo estimate {p['bpm']:.0f} bpm, confidence {p.get('bpm_conf', 0):.2f}, bass ratio {p.get('bass_ratio', 0):.2f}."
    out.append(line)
    beats = p.get("beats") or []
    if beats:
        out.append(f"Beat grid, {len(beats)} beats, anchors d0.. at seconds: " + " ".join(f"d{i}={float(t):.2f}" for i, t in enumerate(beats[:48])))
    if not idx.get("words") or len(idx["words"]) / max(per["dur"], 1) < 1:
        bins = a["rms_db"]
        out.append("Level in dB every 2 s: " + " ".join(f"{i * 0.5:.0f}s {bins[i]:.0f}" for i in range(0, len(bins), 4)))
        out.append("Sharp onsets at seconds: " + " ".join(str(t) for t in a.get("onsets", [])))
    return out


# ----------------------------------------------------------------- footage


def word_at(idx, t):
    """Id of the word being spoken at t, or the nearest within a beat, else None."""
    words = idx.get("words") or []
    best, gap = None, 0.6
    for i, w in enumerate(words):
        if w["t0"] <= t <= w["t1"]:
            return i
        g = min(abs(w["t0"] - t), abs(w["t1"] - t))
        if g < gap:
            best, gap = i, g
    return best


def box_s(b):
    return "[" + ",".join(f"{v:.2f}".lstrip("0") if v < 1 else "1" for v in b) + "]"


def framing_of(sh):
    fh = sh.get("face_h") or 0
    if fh >= 0.22:
        return "close on a face"
    if fh >= 0.1:
        return "head and shoulders"
    if fh:
        return "a small face in the frame"
    return "no face found (read the boards for a person)"


def footage_part(per, idx):
    W, H = per["W"], per["H"]
    up = FINAL_W / W
    out = ["## Footage",
           f"Source {W}x{H}, {per.get('fps') or 0:.0f} fps, {per['dur']:.2f} s, {len(per['shots'])} shot(s). Final frame {FINAL_W}x{FINAL_H}: "
           f"the full width upscales {up:.1f}x, a crop to half width {up * 2:.1f}x. Past 3.4x it reads soft."
           + (f" Kept landscape (`\"frame\":\"16:9\"`, {FINAL_H}x{FINAL_W}) the whole upload shows at {FINAL_H / W:.1f}x." if W > H else "")]
    samples = per["samples"]
    step = per["step"]
    for sh in per["shots"]:
        a, b = sh["t"]
        cam = "locked off" if sh["drift_per_s"] < 0.006 else "handheld or moving"
        line = f"k{sh['id']} {a:.2f}-{b:.2f} ({b - a:.1f} s) {framing_of(sh)}, {cam}."
        if sh.get("face_c"):
            line += f" Face seen in {sh['faces_seen'] * 100:.0f}% of samples, height {sh['face_h']:.3f}, centre {sh['face_c'][0]:.2f},{sh['face_c'][1]:.2f}."
        if sh.get("body_h"):
            line += f" Body height {sh['body_h']:.2f}."
        line += f" Luma {sh['luma']:.2f}, clipped white {sh['hi'] * 100:.0f}%, crushed black {sh['lo'] * 100:.0f}%. Sharpness {sh['sharp']:.0f}, noise {sh['noise']:.2f}, frame change {sh['change']:.1f}."
        if sh.get("free"):
            line += " Free space: " + " ".join(box_s(f) for f in sh["free"]) + "."
        out.append(line)
        ss = [s for s in samples if a <= s["t"] < b and s["face"]]
        if len(ss) >= 3:
            every = max(1, int(round(max(2.0, (b - a) / 14) / step)))
            rows = [f"{s['t']:.1f} {(s['face'][0] + s['face'][2]) / 2:.2f},{(s['face'][1] + s['face'][3]) / 2:.2f}" for s in ss[::every]]
            out.append("  head (second, centre): " + "; ".join(rows))
    evs = []
    names = {"turned_away": "turned from camera", "arms_wide": "arms wide", "hands_fast": "hands fast"}
    for e in per.get("events", []):
        t = e["t"] if isinstance(e["t"], (int, float)) else e["t"][0]
        ts = f"{e['t']:.1f}" if isinstance(e["t"], (int, float)) else f"{e['t'][0]:.1f}-{e['t'][1]:.1f}"
        wid = word_at(idx, t)
        label = names.get(e["k"], f"walks {e.get('dir', '')}".strip())
        evs.append(f"{ts} {label}" + (f" w{wid}" if wid is not None else ""))
    if evs:
        out.append("Moments in the picture: " + "; ".join(evs))
    if per.get("text"):
        rows = []
        for t in per["text"][:6]:
            rows.append(f"{box_s(t['box'])}, seen {t['t'][0]:.1f}-{t['t'][1]:.1f}")
        out.append("Printed or burned-in text stays at these places, read it on the boards: " + "; ".join(rows))
    pal = " ".join(f"{p['hex']} {p['share'] * 100:.0f}%" for p in per.get("palette", [])[:6])
    acc = " ".join(f"{p['hex']} {p['share'] * 100:.2f}%" for p in per.get("accents", []))
    out.append(f"Colours: {pal}. Saturated colours really in the frame: {acc or 'none'}.")
    return out


# ----------------------------------------------------------------- boards


def layout(aspect):
    """Rows and columns for one 1568x880 board. A cell stays large enough to read a hand."""
    rows, cols = (2, 6) if aspect < 0.75 else (2, 4) if aspect < 1.1 else (3, 3)
    ch = (BOARD_H - (rows - 1) * 4) // rows
    cw = int(ch * aspect)
    if cols * (cw + 4) - 4 > BOARD_W:
        cw = (BOARD_W + 4) // cols - 4
        ch = int(cw / aspect)
    return rows, cols, cw, ch


def pick_times(per, n):
    """n seconds over the film, each pulled onto a nearby moment, every shot represented."""
    dur = per["dur"]
    shots = [sh["t"] for sh in per["shots"]]
    tot = sum(b - a for a, b in shots) or dur
    alloc = [max(2, int(round(n * (b - a) / tot))) for a, b in shots]
    while sum(alloc) > n:
        alloc[alloc.index(max(alloc))] -= 1
    while sum(alloc) < n:
        alloc[alloc.index(max(alloc))] += 1
    evs = []
    for e in per.get("events", []):
        if isinstance(e["t"], (int, float)):
            evs.append(float(e["t"]))
        else:
            evs += [float(e["t"][0]), float(sum(e["t"]) / 2)]
    out = []
    for (a, b), k in zip(shots, alloc):
        if k <= 0:              # a shot the allowance passed over (p5554, 45 s with no words: the Director fell here)
            continue
        base = np.linspace(a + min(0.15, (b - a) / 4), b - min(0.15, (b - a) / 4), k)
        tol = (b - a) / k * 0.45
        used = set()
        for t in base:
            near = sorted((abs(e - t), e) for e in evs if a <= e < b and e not in used)
            if near and near[0][0] <= tol:
                used.add(near[0][1])
                t = near[0][1]
            out.append(round(float(t), 2))
    return sorted(out)


def sample_at(per, t):
    return min(per["samples"], key=lambda s: abs(s["t"] - t))


def overlay(img, per, idx, t, draw_free):
    """Stamp, boxes and frame ticks on one cell. Ticks mark every .1 of the frame, long ones at .5."""
    d = ImageDraw.Draw(img)
    w, h = img.size
    s = sample_at(per, t)
    if s.get("body"):
        b = s["body"]
        d.rectangle((b[0] * w, b[1] * h, b[2] * w, b[3] * h), outline=CYAN, width=1)
    if s.get("face"):
        b = s["face"]
        d.rectangle((b[0] * w - 1, b[1] * h - 1, b[2] * w + 1, b[3] * h + 1), outline=GREEN, width=2)
    if draw_free:
        for sh in per["shots"]:
            if sh["t"][0] <= t < sh["t"][1]:
                for f in sh.get("free", [])[:2]:
                    d.rectangle((f[0] * w, f[1] * h, f[2] * w, f[3] * h), outline=YEL, width=1)
    for i in range(1, 10):
        ln = 9 if i == 5 else 4
        d.line((i * w / 10, 0, i * w / 10, ln), fill=(255, 255, 255), width=1)
        d.line((0, i * h / 10, ln, i * h / 10), fill=(255, 255, 255), width=1)
    wid = word_at(idx, t)
    label = f"@{t:.1f}" + (f" w{wid}" if wid is not None else "")
    f = boards.font(15)
    tw = d.textlength(label, font=f)
    d.rectangle((0, h - 19, tw + 8, h), fill=(0, 0, 0))
    d.text((4, h - 18), label, fill=YEL, font=f)
    return img


def frame_board(src, per, idx, times, rows, cols, cw, ch):
    im = Image.new("RGB", (BOARD_W, BOARD_H), boards.BG)
    gw = (BOARD_W - cols * cw) // max(1, cols - 1) if cols > 1 else 0
    gh = (BOARD_H - rows * ch) // max(1, rows - 1) if rows > 1 else 0
    with cf.ThreadPoolExecutor(max_workers=3) as pool:
        grabbed = list(pool.map(lambda q: boards.grab(src, q, width=cw * 2), times))
    for i, (t, fr) in enumerate(zip(times, grabbed)):
        if fr is None:
            continue
        cell = boards.fit(fr, cw, ch)
        overlay(cell, per, idx, t, draw_free=(i == 0))
        r, c = i // cols, i % cols
        im.paste(cell, (c * (cw + gw), r * (ch + gh)))
    return im


def detail_board(src, per, idx):
    """Board 4: small things in the world, magnified. How soft a crop really is, at 1:1 with the final frame. The palette."""
    im = Image.new("RGB", (BOARD_W, BOARD_H), boards.BG)
    d = ImageDraw.Draw(im)
    W, H = per["W"], per["H"]
    d.text((6, 4), "Board 4. Left: text found in the picture, magnified. Right: patches at the size they would have in the final 1080x1920 frame, centred on the subject.", fill=boards.FG, font=boards.font(15))
    x, y = 6, 30
    mid = per["dur"] / 2
    full = boards.grab(src, mid, width=W)
    # text in the world, grouped, largest reading first
    shown = 0
    for t in per.get("text", [])[:3]:
        b = t["box"]
        at = sum(t["t"]) / 2
        fr = boards.grab(src, at, width=W) or full
        bw, bh = b[2] - b[0], b[3] - b[1]
        crop = (int(max(0, b[0] - bw * 0.7) * W), int(max(0, b[1] - bh * 1.2) * H),
                int(min(1, b[2] + bw * 0.7) * W) + 1, int(min(1, b[3] + bh * 1.2) * H) + 1)
        c = fr.crop(crop)
        sc = min(600 / c.width, 240 / c.height)
        c = c.resize((max(1, int(c.width * sc)), max(1, int(c.height * sc))), Image.BICUBIC)
        im.paste(c, (x, y + 20))
        d.text((x, y + 2), f"{box_s(b)} @{at:.1f}", fill=YEL, font=boards.font(14))
        y += c.height + 28
        shown += 1
    if not shown:
        d.text((x, y), "No text found in the picture.", fill=boards.DIM, font=boards.font(15))
        y += 26
    # palette
    swatches = [p["hex"] for p in per.get("palette", [])[:6]] + [p["hex"] for p in per.get("accents", [])[:4]]
    sx = 6
    sy = max(y + 10, 760)
    for hx in swatches:
        rgb = tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))
        d.rectangle((sx, sy, sx + 58, sy + 58), fill=rgb)
        d.text((sx, sy + 62), hx, fill=boards.DIM, font=boards.font(11))
        sx += 64
    # softness at 1:1 with the final frame
    faced = [q for q in per["samples"] if q.get("face")]
    s = min(faced, key=lambda q: abs(q["t"] - mid)) if faced else sample_at(per, mid)
    full = boards.grab(src, s["t"], width=W) or full
    b = s.get("face") or s.get("body")
    cx, cy = ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else (0.5, 0.5)
    if full is not None:
        px = 280
        ups = [FINAL_W / W, 2 * FINAL_W / W, 3 * FINAL_W / W]
        for j, u in enumerate(ups):
            n = px / u
            cxp, cyp = cx * W, cy * H
            c = full.crop((int(cxp - n / 2), int(cyp - n / 2), int(cxp + n / 2), int(cyp + n / 2))).resize((px, px), Image.LANCZOS)
            ox, oy = 640 + j * (px + 8), 40
            im.paste(c, (ox, oy))
            d.text((ox, oy + px + 4), f"{u:.1f}x upscale (crop {1 / (u / (FINAL_W / W)):.2f} of width)", fill=boards.DIM, font=boards.font(13))
        # a coordinate key on one frame, full size in frame terms
        key = full.copy()
        kw, kh = (int(px * 1.0 * W / H), px) if H >= W else (px, int(px * H / W))
        key = key.resize((kw, kh), Image.LANCZOS)
        kd = ImageDraw.Draw(key)
        for i in range(1, 10):
            kd.line((i * kw / 10, 0, i * kw / 10, kh), fill=(255, 255, 255) if i == 5 else (200, 200, 200), width=1)
            kd.line((0, i * kh / 10, kw, i * kh / 10), fill=(255, 255, 255) if i == 5 else (200, 200, 200), width=1)
        im.paste(key, (640, 40 + px + 30))
        d.text((6, 660), "Grid every .1 of the frame. Crops are [x0,y0,x1,y1] of the source.\nGreen face. Cyan body. Yellow free space. The ticks on every cell are this .1 grid.", fill=boards.DIM, font=boards.font(14))
    # the film's biggest moments, large
    return im


TALK_CARDS, WORDLESS_CARDS = ("P1", "P6", "M8", "P2"), ("S9", "S11", "M5")


def pick_styles(idx, per, bin_items=None):
    """Style cards this footage can supply (taste.fit_styles): films of the folder whose picture changes about as
    often as this upload's own cuts and its Bin can reach. One take with an empty Bin is offered the take's card,
    not three films that cut fifteen times in 10 s."""
    from . import compiler
    words = idx.get("words") or []
    talk = len(words) / max(per["dur"], 1) >= 1.0
    have = [s for s in (TALK_CARDS if talk else WORDLESS_CARDS) if os.path.exists(os.path.join(STYLE_DIR, f"{s}.jpg"))]
    return taste.fit_styles(compiler.BANDS, taste.reach(per, bin_items, per["dur"]), have)


def seats_part(work, idx, per):
    """Where type can stand on the take, solved by the compiler's own rules before a film exists. The Director
    wrote hook titles the compiler then left out for want of a place (p5591: three of three). Told where the
    place is, or that the take has none, it sets the title there, or over a picture."""
    if per["W"] >= per["H"]:
        return []
    try:
        from . import compiler
        got = compiler.take_seats(work, idx)
    except Exception:  # noqa: BLE001 - a Brief without this line is still a Brief
        got = None
    if not got:
        return []
    cap_y, seats, rooms = got
    out = ["## Seats", f"Solved on the take as shot. Caption seat y {cap_y:.2f}."]
    if seats:
        rows = [f"[{q[0]:.2f},{q[1]:.2f}] with room for type {r[1]:.2f} of the frame tall and {r[0]:.2f} wide" for q, r in zip(seats, rooms)]
        out.append("Display seats on the take: " + "; ".join(rows) + ". Display type over the take stands there, fitted to that room: a two-line title needs about 0.12 tall."
                   + (" The room is small: set the hook title over a found picture or a page instead, and keep the seat for one short word." if max(r[1] for r in rooms) < 0.1 else ""))
    else:
        out.append("Display seats on the take: none, the head fills the frame. Display type stands over a found picture or a page, never over the take; the hook title opens the film on one.")
    return out


def supply_part(idx, per, bin_items):
    """What this footage can carry, in numbers, before a film exists."""
    dur = float(per["dur"])
    own = max(0, len(per.get("shots") or []) - 1)
    n = len(bin_items or [])
    words = idx.get("words") or []
    a = per.get("audio") or {}
    bins = a.get("rms_db") or []
    bed = False
    if bins and len(words) / max(dur, 1e-3) < 1.0:
        top = sorted(bins)[int(len(bins) * 0.9)]
        bed = sum(1 for b in bins if b >= top - 20.0) >= 0.5 * len(bins) and top > -45.0
    out = ["## Supply"]
    rows = []
    for label, np10 in (("held as one take, no found picture", own / max(dur, 1e-3) * 10), (f"with all {n} Bin pictures cut in", taste.reach(per, bin_items, dur))):
        cls = taste.klass(np10, dur)
        al = taste.allow({"cls": cls, "np10": np10, "dur_s": dur, "bed": bed})
        rows.append(f"{label}: {np10:.1f} new pictures per 10 s, class {cls}: camera moves {al['camera']}, sounds {al['sounds']}, display lines {al['display']} at {al['seats']} seats, cards {al['cards']}, flashes {al['flashes']}")
        if not n:
            break
    out.append(f"The upload has {own} cut(s) of its own, {dur:.1f} s, {'a sound bed' if bed else 'no bed'}. What it carries, " + "; ".join(rows) + ". The compiler counts the pictures you really cut to and holds the film to that.")
    return out


# ----------------------------------------------------------------- prefix


def prefix_text():
    with open(PREFIX, encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------- film part


def doubtful_words(idx):
    """Is the transcript noise rather than speech: under four words, or a mean confidence under 0.75?"""
    words = idx.get("words") or []
    if not words:
        return False
    return len(words) < 4 or float(np.mean([float(w.get("confidence", 1.0)) for w in words])) < 0.75


def scout_part(scout, idx=None):
    if not scout:
        return ["## Asks", "The Scout has not run."]
    lang = scout.get("lang")
    if idx is not None and doubtful_words(idx) and not scout.get("asks"):
        # the language was guessed from noise: a firework clip got its title in Finnish (p5571, 13 Nov)
        lang = "en"
    out = ["## Asks", f"The Scout read the request. Scope `{scout.get('scope')}`. Customer's language `{lang}`: write every `job` and every word of type in it. {scout.get('about', '')}".strip()]
    if scout.get("scope") == "surgical":
        out.append("The product makes the literal change on its own. This Score is the director's cut built alongside it, offered once: write the whole film.")
    if scout.get("asks"):
        for a in scout["asks"]:
            line = f"{a['id']}: \"{a.get('said')}\" probe {a.get('probe')}"
            if a.get("min") is not None:
                line += f" min {a['min']}"
            if a.get("note"):
                line += f". {a['note']}"
            out.append(line)
        out.append("Each ask gets a bind line.")
    else:
        out.append("No asks: the customer typed nothing. The film is the whole brief.")
    if scout.get("fixes"):
        out.append("=> marks are a cheap model's hearing. You read the sentence and decide what was said. Each correction you keep is a fix line, spelled as the caption should read.")
    return out


def bin_part(work):
    b = jload(os.path.join(work, "bin.json"))
    if not b:
        return ["## The Bin", "Empty so far. Plates and the upload only."], None
    out = ["## The Bin", "On the last board. Everything here exists: it is real files."]
    mine = [w.get("want") for w in b.get("wishes", []) if w.get("mine")]
    if mine:
        out.append("The customer's own, so only the upload shows these: " + "; ".join(str(x) for x in mine[:4]) + ".")
    out.append("Video runs from the in-point you give, `bin:N@2.5`.")
    for it in b.get("items", []):
        line = f"bin:{it['id']} {it['kind']}"
        if it.get("dur"):
            line += f" {it['dur']:.1f}s"
        line += f" {it.get('w')}x{it.get('h')} {it.get('what', '')}".rstrip()
        if it.get("for"):
            line += f" (for {it['for']})"
        if it.get("design"):
            line += ". Designed from the words said there: it is type, so no type goes on it"
        elif any(r.get("text") for r in it.get("seen") or []):
            line += ". Holds printed words"
        if any(r.get("faces") for r in it.get("seen") or []):
            line += f". Shows {it['named']}, as the words name" if it.get("named") else ". Holds a stranger's face"
        out.append(line)
    if b.get("own_lines"):
        out.append("The upload's own moments, re-framed for what only it shows; on the board after the found pictures, used as written:")
        out += b["own_lines"]
    if b.get("plates"):
        out.append("Plates ready for any beat: " + " ".join(f"{p['id']} {p['c0']}>{p['c1']} ({p.get('why', '')})" for p in b["plates"]))
    return out, os.path.join(work, "bin_board.jpg")


def film_text(work, idx, sess, per, x, scout, styles, cards, bin_items=None):
    prompts = sess.get("prompts") or []
    out = ["# This film", "## The ask"]
    names = [u.get("filename") for u in sess.get("uploads") or [] if u.get("filename")]
    if names:
        out.append("File name: " + ", ".join(names))
    if prompts:
        for p in prompts:
            out.append("Customer, verbatim: " + json.dumps(p["text"], ensure_ascii=False))
    else:
        out.append("The customer uploaded and typed nothing.")
    out += words_part(work, idx, per, x, scout)
    out += footage_part(per, idx)
    out += seats_part(work, idx, per)
    out += audio_part(idx, per)
    out += ["## Frames",
            "Boards 1-3: frames in time order. Each stamp reads @source-second, then the word being spoken. Green box face, yellow box free space on the first cell. White ticks mark every .1 of the frame. Board 4: details, softness, palette."]
    out += scout_part(scout, idx)
    out += supply_part(idx, per, bin_items)
    out.append("## Style cards")
    out.append("Real films from the folder that this footage can supply. The frames follow, one board per card, after the frame boards. Numbers are that film's, measured. `system.card` is one of these: " + " ".join(styles) + ".")
    for s in styles:
        out.append(cards[s])
    out += bin_part(work)[0]
    return "\n".join(out) + "\n"


def boards_for(work, idx, per):
    """The four frame boards. The Scout reads the first, so these come before anything else."""
    out = os.path.join(work, "brief")
    os.makedirs(out, exist_ok=True)
    src = os.path.join(work, "source.mp4")
    rows, cols, cw, ch = layout(per["W"] / per["H"])
    per_board = rows * cols
    times = pick_times(per, per_board * 3)
    for i in range(3):
        boards.save(frame_board(src, per, idx, times[i * per_board:(i + 1) * per_board], rows, cols, cw, ch), f"{out}/board_{i + 1}.jpg", quality=86)
    boards.save(detail_board(src, per, idx), f"{out}/board_4.jpg", quality=86)
    return dict(rows=rows, cols=cols, cell=[cw, ch], per_board=per_board), times


def build(work, idx, sess, per, scout, grid=None):
    out = os.path.join(work, "brief")
    os.makedirs(out, exist_ok=True)
    src = os.path.join(work, "source.mp4")
    x = decode(src) if idx.get("words") else np.zeros(0)
    with open(STYLES, encoding="utf-8") as f:
        cards = json.load(f)
    bin_items = (jload(os.path.join(work, "bin.json")) or {}).get("items") or []
    styles = pick_styles(idx, per, bin_items)
    with open(os.path.join(work, "stress.json"), "w") as f:      # the compiler ranks devices by the voice's own stress
        json.dump(stress_map(idx, x) if len(x) else {}, f)
    # text, under the cap: a third card goes first, then a second, each recorded
    dropped = []
    while True:
        txt = film_text(work, idx, sess, per, x, scout, styles, cards, bin_items)
        est = estimate(work, txt, styles)
        if est <= FILM_CAP or len(styles) <= 1:
            break
        dropped.append(dict(part=f"style card {styles[-1]}", why=f"estimate {est} over {FILM_CAP}"))
        styles = styles[:-1]
    with open(f"{out}/film.txt", "w", encoding="utf-8") as f:
        f.write(txt)
    man = dict(built=time.strftime("%Y-%m-%d %H:%M:%S"), styles=styles, grid=grid, dropped=dropped, estimate=est)
    with open(f"{out}/manifest.json", "w") as f:
        json.dump(man, f, indent=1)
    return man


def image_tokens(path):
    with Image.open(path) as im:
        return im.width * im.height // 750


def estimate(work, txt, styles):
    t = int(len(txt) / 2.1)            # measured on the slice's Briefs: digits and ids run about 2.2 chars a token
    t += 4 * (BOARD_W * BOARD_H // 750)
    for s in styles:
        p = os.path.join(STYLE_DIR, f"{s}.jpg")
        if os.path.exists(p):
            t += image_tokens(p)
    if os.path.exists(os.path.join(work, "bin_board.jpg")):
        t += BOARD_W * BOARD_H // 750
    return t


def image_block(path):
    with open(path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}


def load(work):
    """(system, content) for the Director call. Stable bytes in system so the cache hits."""
    out = os.path.join(work, "brief")
    with open(f"{out}/manifest.json") as f:
        man = json.load(f)
    system = [{"type": "text", "text": prefix_text(), "cache_control": {"type": "ephemeral"}}]
    with open(f"{out}/film.txt", encoding="utf-8") as f:
        content = [{"type": "text", "text": f.read()}]
    for i in range(1, 5):
        content.append({"type": "text", "text": f"Board {i}"})
        content.append(image_block(f"{out}/board_{i}.jpg"))
    for s in man["styles"]:
        p = os.path.join(STYLE_DIR, f"{s}.jpg")
        if os.path.exists(p):
            content.append({"type": "text", "text": f"Style card {s}"})
            content.append(image_block(p))
    bb = os.path.join(work, "bin_board.jpg")
    if os.path.exists(bb):
        content.append({"type": "text", "text": "The Bin"})
        content.append(image_block(bb))
    content.append({"type": "text", "text": "Now the Score: JSONL only, every line complete."})
    return system, content
