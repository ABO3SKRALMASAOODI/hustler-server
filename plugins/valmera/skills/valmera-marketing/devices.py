"""The proof reel's editing devices: Valmera's real edit happening on screen, as beats of the narrated film.

Owner, 16 Nov 2026, on the marketing films: "There were some editing tricks, which is good. That's a good direction that
needs to be followed." The tricks were the 12-13 Nov built styles' (film.py): the frame pulling back onto its own
timeline, the typed sentence, the receipt, three exports side by side, the leaning wipe and the count-up grid. They are
rebuilt here as devices laid over the proof reel's own beats, with the faults those films had (MARKETING-LANE.md) fixed
where they were made:

  timeline  on the line that says the typed sentence. The frame pulls back from the upload onto the project's own
            timeline (the upload as one block at its true length with its frames on it, the playhead at the true
            second), the sentence is typed in the prompt as the narrator says it, word by word; on send the blocks the
            edit removed are marked and leave, the rest close up to where the final edit list puts them, the pictures
            it added land, the captions track fills, the version and the length flip and the prompt prints the
            receipt's rows (read from the edit list, never written); the viewer turns into the export as the export's
            own voice starts and pushes back into it, full frame. The receipt prints inside the change: no held page.
  wipe      on the same line, the other way: the upload plays whole on its blurred copy while the sentence appears a
            word as it is said; on send a leaning seam crosses the frame and everything it has passed is the export at
            the same instant, its voice starting under it.
  grid      after the export: the films the product made from this one clip (each a real export of the same upload)
            land in a grid while a counter counts them up. Four or more, or it is not laid.
  triptych  the hook: three exports of the same upload, made from three typed sentences, side by side at the same
            source instant (each export's own edit list maps the second), the first one on frame 0.

Fixed against the old films: every device sits on the real picture (the upload's own blurred copy, the viewer, the
export), never on plain black; no type under 44 px (typed sentence 52-64 px, receipt rows 54 px, labels 44-48 px);
no TV static; nothing in the watermark's place (y < 0.20) or right of the platform's rail below y 0.55; the device's own
sounds are a few, each on something that visibly lands (a key run under the typing, the send, the blocks moving, a
picture landing, the counter), and the narration and the film's bed run under all of it.

Every function lays items into a proof-reel L1 (the stories reel's scene plan) over a frame span: the reel's own
pictures in the span give way (`clear_span`), the reel's sounds on events that no longer show leave with them
(`drop_sounds`), and the device returns a record of what it laid (frames, seats, type sizes, facts and their sources).
"""
import importlib.util
import json
import math
import os
import subprocess

HERE = os.path.dirname(os.path.realpath(__file__))
_spec = importlib.util.spec_from_file_location("_wf2_built_styles", os.path.join(HERE, "film.py"))
MF = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MF)

FPS, W, H = MF.FPS, MF.W, MF.H
MARK, RAIL_X = MF.MARK, MF.RAIL_X
TOP_Y = 0.205                  # under the watermark's place (MARK y1 0.1923)
BOTTOM_Y = 0.795               # over the platform's caption and button band
r4 = MF.r4
DEVICES = ("timeline", "wipe", "triptych")
FORMATS = ("showcase", "tutorial")
# The call to action (owner, 17 Nov: "there is a CTA ... when you tell the user the CTA, he will immediately convert").
# Every line is true of the product; never "free" (the trial is off), never a price or a plan.
# The website itself is the call's largest line (owner, 17 Nov: "Something that could show the website directly"); the
# brand's end card carries VALMERA.IO only small, so the film carries it large.
CTA_LINES = (("The AI video editor", "dv_l", "#C9C6BF"), ("Type what you want.", "dv_s", "#FFFFFF"),
             ("It edits your video.", "dv_s", "#FFFFFF"), ("valmera.io", "dv_w", "#FF5B35"))
TYPED_LABEL = "TYPED AT VALMERA.IO"     # the site, beside the moment that makes a viewer ask how
CTA_S = 3.4                    # 14 words at a phone reader's pace, with the landing
ACCENT = "#FF5B35"
CYAN = "#7FE3FF"
INK = "#F4F1EB"
DIM = "#9A9893"
DESK_GRADE = dict(lift=0, gamma=1.0, gain=0.40, sat=0.7, contrast=1, temp=0, tint=0)
VOICES = {
    "dv_s": {"font": "InterDisplay-Bold.ttf", "px": 64, "track": 0},          # the typed sentence
    "dv_l": {"font": "InterDisplay-Bold.ttf", "px": 44, "track": 4},          # labels: TYPED AT VALMERA.IO, the version, the length
    "dv_r": {"font": "InterDisplay-ExtraBold.ttf", "px": 54, "track": 0},     # the receipt's rows
    "dv_n": {"font": "InterDisplay-Black.ttf", "px": 56, "track": 2},         # the triptych's numbers
    "dv_c": {"font": "InterDisplay-Black.ttf", "px": 150, "track": 0},        # the grid's counter
    "dv_w": {"font": "InterDisplay-Black.ttf", "px": 168, "track": 6},        # the product's name in the call to action
    "dv_u": {"font": "InterDisplay-ExtraBold.ttf", "px": 68, "track": 0},     # what to do next
}
PER_FRAME = {"x", "y", "s", "rot", "op", "blur", "a", "th", "t", "dim", "grow"}
RECTS = {"crop", "rect"}
SOUND_DB = dict(typing=-27.0, click=-20.0, whoosh=-23.0, tick=-24.0, impact=-19.0, riser=-26.0)  # peaks before the film's loudness pass (film.py's own)
CTA_RISER = "riser_2"          # the climb into the call to action: a one-second riser whose top is the website's landing


# ----------------------------------------------------------------- the reel gives way

def _frames(v, n):
    """A per-frame list as long as its item (the engine holds the last value past a short one)."""
    v = list(v)
    return v + [v[-1]] * (n - len(v)) if v and len(v) < n else v


def slice_item(it, a, b):
    """The item restricted to frames [a, b): per-frame values cut, the source window moved with it. None when empty."""
    f0, f1 = int(it["f"][0]), int(it["f"][1])
    lo, hi = max(a, f0), min(b, f1)
    if hi <= lo:
        return None
    n = f1 - f0
    out = dict(it, f=[lo, hi])
    for k, v in it.items():
        if not isinstance(v, list) or not v:
            continue
        if k in PER_FRAME or (k in RECTS and isinstance(v[0], (list, tuple))):
            out[k] = _frames(v, n)[lo - f0:hi - f0]
    src = it.get("src")
    if isinstance(src, list) and len(src) == 2 and n:
        rate = (float(src[1]) - float(src[0])) / n
        out["src"] = [r4(float(src[0]) + rate * (lo - f0)), r4(float(src[0]) + rate * (hi - f0))]
    if "on" in it:
        out["on"] = max(int(it["on"]), lo)
    return out


def clear_span(l1, a, b, keep=None):
    """The reel's items inside frames [a, b) give way to a device; an item that runs across an edge keeps its parts
    outside. keep(item) -> True leaves an item whole (the narration's captions over the triptych)."""
    out = []
    for it in l1["items"]:
        f0, f1 = it["f"]
        if f1 <= a or f0 >= b or (keep and keep(it)):
            out.append(it)
            continue
        for lo, hi, tag in ((f0, a, "a"), (b, f1, "b")):
            piece = slice_item(it, lo, hi)
            if piece:
                piece["id"] = f"{it['id']}.{tag}"
                out.append(piece)
    l1["items"] = out


def drop_sounds(l1, a, b):
    """The reel's sounds whose event is inside [a, b): what they answered no longer shows. Returns how many left."""
    keep = [s for s in l1.get("sound") or [] if not (a <= int(s.get("event_f", -1)) < b) or s.get("device")]
    n = len(l1.get("sound") or []) - len(keep)
    l1["sound"] = keep
    return n


def is_caption(it):
    return it.get("k") == "word" and it.get("v") in ("cap", "capd")


# ----------------------------------------------------------------- media

def media(ff, path):
    """w, h, fps, dur of a video file (ffprobe beside the film's ffmpeg)."""
    probe = os.path.join(os.path.dirname(ff), "ffprobe")
    r = subprocess.run([probe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate:format=duration",
                        "-of", "json", path], capture_output=True, text=True, timeout=60)
    j = json.loads(r.stdout or "{}")
    st = (j.get("streams") or [{}])[0]
    a, b = (st.get("r_frame_rate") or "30/1").split("/")
    return dict(path=path, w=int(st.get("width") or 0), h=int(st.get("height") or 0), fps=round(float(a) / max(float(b), 1.0), 3),
                dur=float((j.get("format") or {}).get("duration") or 0.0))


def stills(ff, path, times, out_dir, h=320):
    """JPEG frames of a video at the given seconds: [{t, path, w, h}]."""
    os.makedirs(out_dir, exist_ok=True)
    out = []
    stem = os.path.splitext(os.path.basename(path))[0] + "_" + str(abs(hash(path)) % 10 ** 6)
    for t in times:
        p = os.path.join(out_dir, f"{stem}_{int(round(max(t, 0.0) * 100)):06d}.jpg")
        if not os.path.exists(p):
            subprocess.run([ff, "-v", "error", "-y", "-ss", f"{max(t, 0.0):.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale=-2:{h}",
                            "-q:v", "3", p], capture_output=True, timeout=60)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            from PIL import Image
            with Image.open(p) as im:
                out.append(dict(t=round(float(t), 3), path=p, w=im.width, h=im.height))
    return out


def cover(src_aspect, box_aspect):
    """The centred crop [x0, y0, x1, y1] of a picture of src_aspect (w/h) that fills a box of box_aspect."""
    if src_aspect > box_aspect:
        cw = box_aspect / src_aspect
        return [r4(0.5 - cw / 2), 0.0, r4(0.5 + cw / 2), 1.0]
    ch = src_aspect / box_aspect
    return [0.0, r4(0.5 - ch / 2), 1.0, r4(0.5 + ch / 2)]


def program(edl):
    return MF.program(edl)


def src_at(tl, prog_s):
    """The source second the programme shows at prog_s (the worker's own mapping)."""
    return float(tl.out_to_src(float(prog_s)))


# ----------------------------------------------------------------- the edit, as facts

def receipt_rows(edl_a, edl_b, dur_a, dur_b):
    """What the sentence changed, read from the two edit lists (never written): at most three rows."""
    rows = []
    kept = sum(max(float(k[1]) - float(k[0]), 0.0) for k in edl_b.get("keep") or [])
    whole = sum(max(float(k[1]) - float(k[0]), 0.0) for k in edl_a.get("keep") or [])
    cut = whole - kept
    if cut >= 0.3:
        rows.append(dict(text=f"−{cut:.1f} s cut", fact="removed_s", value=round(cut, 2)))
    pics = len(MF.pictures_of(edl_b)) - len(MF.pictures_of(edl_a))
    if pics > 0:
        rows.append(dict(text=f"+{pics} picture{'s' if pics != 1 else ''}", fact="pictures_added", value=pics))
    if edl_b.get("captions") and not edl_a.get("captions"):
        rows.append(dict(text="+ captions", fact="captions", value=True))
    titles = len(MF.titles_of(edl_b)) - len(MF.titles_of(edl_a))
    if titles > 0:
        rows.append(dict(text=f"+{titles} title{'s' if titles != 1 else ''}", fact="titles_added", value=titles))
    fr = edl_b.get("frame") or {}
    if fr.get("mode") == "crop" and not (edl_a.get("frame") or {}).get("mode"):
        rows.append(dict(text=f"{fr.get('ratio') or '9:16'} reframe", fact="frame", value=fr.get("ratio") or "9:16"))
    return rows[:3]


def word_frames(clock_words, typed, norm):
    """The frames each typed word is said on: [(f0, f1)] for every word of `typed`, aligned to the spoken words of its
    line (the longest run of the typed words in order, a filler word allowed between two). A word the aligner did not
    find takes the time between its neighbours; with nothing found the words share the line evenly."""
    tw = typed.split()
    keys = [norm(w["w"]) for w in clock_words]
    want = [norm(w) for w in tw]
    best, at_best = [], 0
    for j in range(len(keys)):
        got, q = [], j
        for i, k in enumerate(want):
            hit = next((p for p in range(q, min(q + 3, len(keys))) if keys[p] == k), None)
            if hit is None:
                continue
            got.append((i, hit))
            q = hit + 1
        if len(got) > len(best):
            best, at_best = got, j
    del at_best
    f = lambda t: int(round(float(t) * FPS))  # noqa: E731
    if not clock_words:
        return []
    a, b = f(clock_words[0]["t0"]), f(clock_words[-1]["t1"])
    if not best:
        step = (b - a) / max(len(tw), 1)
        return [(int(a + i * step), int(a + (i + 1) * step)) for i in range(len(tw))]
    at = dict(best)
    out = []
    for i in range(len(tw)):
        if i in at:
            w = clock_words[at[i]]
            out.append((f(w["t0"]), max(f(w["t1"]), f(w["t0"]) + 2)))
        else:
            out.append(None)
    i = 0
    while i < len(out):
        if out[i] is not None:
            i += 1
            continue
        j = next((k for k in range(i+1, len(out)) if out[k] is not None), len(out))
        left = out[i-1][1] if i else a
        right = out[j][0] if j < len(out) else b
        left = min(left, right)
        edges = [round(left+(right-left)*k/(j-i)) for k in range(j-i+1)]
        out[i:j] = [(lo, max(lo+1, hi)) for lo, hi in zip(edges, edges[1:])]
        i = j
    return out


# ----------------------------------------------------------------- shared pieces

class Layer:
    """A device's items, sounds and assets, laid into the reel's L1 with its own id prefix."""

    def __init__(self, l1, lib, prefix):
        self.l1, self.prefix = l1, prefix
        voices = l1.setdefault("voices", {})
        self.P = MF.Plan(voices, lib or {})
        self.R = self.P.R
        self.texts = []

    def k(self, name):
        """This device's own copy of a type voice: a size one device sets never resizes another's words (the L1's
        voices are shared by every item that names them)."""
        key = f"{name}.{self.prefix}"
        self.l1["voices"].setdefault(key, dict(VOICES[name]))
        return key

    def text(self, iid, f, t, v, x, y, ink=INK, op=1.0, rise=False, **kw):
        it = (self.P.rise if rise else self.P.word)(iid, f, t, v, x, y, ink, op, **kw)
        it["role"] = "device"                    # the sound engine's display landings are the reel's; these sound by name
        vo = self.l1["voices"][v]
        self.texts.append(dict(t=t, v=v, f=[int(f[0]), int(f[1])], px=float(vo["px"]), cap_px=round(self.R.cap(v), 1), x=r4(x if not isinstance(x, list) else x[-1]),
                               y=r4(y if not isinstance(y, list) else y[-1]), w=r4(self.R.width(v, t) / W)))
        return it

    def hit(self, name, f, kind):
        if name in self.P.lib:
            self.P.hit(f"{self.prefix}.snd{len(self.P.sounds)}", name, int(f), SOUND_DB[kind])
            self.P.sounds[-1]["device"] = self.prefix

    def lay(self, span, keep=None):
        """The reel gives way over span (its pictures and the sounds that answered them), then the device goes in."""
        clear_span(self.l1, span[0], span[1], keep=keep)
        dropped = drop_sounds(self.l1, span[0], span[1])
        for it in self.P.items:
            it["id"] = f"{self.prefix}.{it['id']}"
            it["keep_over_proof"] = True
        self.l1["items"].extend(self.P.items)
        self.l1.setdefault("assets", {}).update(self.P.assets)
        self.l1.setdefault("sound", []).extend(self.P.sounds)
        order_k = {"shot": 0, "join": 1, "card": 2, "flash": 3, "word": 4}
        self.l1["items"].sort(key=lambda it: (it["f"][0], order_k.get(it["k"], 5), it.get("z", 0), str(it["id"])))
        return dict(sounds=[dict(s=s["s"], f=s["event_f"]) for s in self.P.sounds], reel_sounds_dropped=dropped, texts=self.texts,
                    min_text_px=min((t["px"] for t in self.texts), default=None),
                    min_cap_px=min((t["cap_px"] for t in self.texts), default=None))


def desk(L, f, key, m, focus=(0.5, 0.42), src0=0.0):
    """The ground of a device: the clip's own picture, blurred and darkened, filling the frame (never a black page)."""
    crop = cover(m["w"] / m["h"], W / H)
    dx = (focus[0] - 0.5) * (1 - (crop[2] - crop[0]))
    crop = [r4(crop[0] + dx), crop[1], r4(crop[2] + dx), crop[3]]
    n_s = (f[1] - f[0]) / FPS
    src0 = min(max(src0, 0.0), max(float(m.get("dur") or 0.0) - n_s - 0.05, 0.0))
    L.P.items.append(dict(id="desk", k="shot", f=list(f), pic=key, crop=crop, src=[r4(src0), r4(src0 + n_s)], blur=26,
                          grade=DESK_GRADE))


def pull_from_cover(box, n, focus):
    """(x, y, s) per frame of a card seated in `box` that starts covering the whole frame around `focus` (the clip's
    own crop centre: the speaker) and settles into its seat, the focus travelling in a straight line."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    cx1, cy1 = (x0 + x1) / 2, (y0 + y1) / 2
    s0 = max(1.0 / bh, 1.0 / bw)
    u, v = focus[0] - 0.5, focus[1] - 0.5
    c0x = min(max(0.5 - u * bw * s0, 1 - bw * s0 / 2), bw * s0 / 2)
    c0y = min(max(0.5 - v * bh * s0, 1 - bh * s0 / 2), bh * s0 / 2)
    p0 = (c0x + u * bw * s0, c0y + v * bh * s0)
    p1 = (cx1 + u * bw, cy1 + v * bh)
    xs, ys, ss = [], [], []
    for q in MF.expo_out(n):
        s = s0 ** (1 - q)
        px, py = p0[0] + (p1[0] - p0[0]) * q, p0[1] + (p1[1] - p0[1]) * q
        xs.append(r4(px - u * bw * s))
        ys.append(r4(py - v * bh * s))
        ss.append(r4(s))
    return xs, ys, ss


def fit_lines(R, v, text, max_w_px, px_hi, px_lo, max_lines=2):
    """The largest size from px_hi down to px_lo at which text wraps into at most max_lines lines no wider than max_w_px
    (the shaper's own widths); else px_lo and as many lines as it takes."""
    vo = R.voices[v]
    keep = vo["px"]
    px = px_hi
    while px >= px_lo:
        vo["px"] = px
        lines = MF.Plan.wrap(_Wrap(R), text, v, max_w_px)
        if len(lines) <= max_lines:
            return px, lines
        px -= 2
    vo["px"] = px_lo
    lines = MF.Plan.wrap(_Wrap(R), text, v, max_w_px)
    if not lines:
        vo["px"] = keep
    return px_lo, lines


class _Wrap:
    def __init__(self, R):
        self.R = R


def typed_text(L, iid, f_end, lines, v, x, y0, pitch, word_f, fade_from=None):
    """Reveal shaped whole words on their spoken frames in their final line positions."""
    caret = []
    wi = 0
    for li, line in enumerate(lines):
        prefix = ""
        for word in line.split():
            start, _ = word_f[min(wi, len(word_f) - 1)]
            wi += 1
            if start >= f_end:
                continue
            wx = x + L.R.width(v, prefix) / W
            y = y0 + li * pitch
            opacity = 1. if fade_from is None else [
                min(1., max(0., (fade_from + 5 - f) / 6)) for f in range(start, f_end)]
            L.P.word(f"{iid}.{wi-1:02d}", [start, f_end], word, v, wx, y, INK, opacity, note=False)["role"] = "device"
            caret.append((start, x + L.R.width(v, prefix + word) / W, y))
            prefix += word + " "
    L.P.shown.append(" ".join(lines))
    vo = L.l1["voices"][v]
    L.texts.append(dict(t=" ".join(lines), v=v, f=[int(word_f[0][0]), int(f_end)], px=float(vo["px"]), cap_px=round(L.R.cap(v), 1), x=r4(x), y=r4(y0),
                        w=r4(max(L.R.width(v, ln) for ln in lines) / W), lines=len(lines)))
    return caret


def caret_card(L, iid, f0, f1, caret, v, x, y):
    """A thin caret that follows the typing and blinks while it waits."""
    if f1 <= f0:
        return
    cap = L.R.cap(v) / H
    xs, ys, ops = [], [], []
    k = 0
    cx, cy = x, y
    for f in range(f0, f1):
        while k < len(caret) and caret[k][0] <= f:
            cx, cy = caret[k][1] + 0.004, caret[k][2]
            k += 1
        xs.append(r4(cx + 0.0015))
        ys.append(r4(cy - cap / 2))
        typing = k < len(caret)
        ops.append(1.0 if typing or ((f - f0) // 8) % 2 == 0 else 0.0)
    L.P.items.append(dict(id=iid, k="card", f=[f0, f1], z=6, pic="plate", plate=dict(c0=INK, c1=INK, grain=0.0), x=xs, y=ys,
                          w=r4(4.0 / W), h=r4(cap * 1.25), s=1, rot=0, op=ops))


# ----------------------------------------------------------------- timeline: the frame pulls back onto its own timeline

def recorded_field(ui, typed, work, seat_width):
    """Crop the photographed input using its browser bounds, pinning its first baseline as it grows."""
    from PIL import Image
    data = ui.get("field") or {}
    rows = data.get("words") or []
    files = ui.get("typing") or []
    if not rows or data.get("text") != typed or len(rows) != len(typed.split()) or len(files) != len(rows)+1:
        return None
    pictures = []
    for row, name in zip(rows, files[1:]):
        if (not isinstance(row, dict) or row.get("scroll") or len(row.get("box", [])) != 4
                or not all(0 <= v <= 1 for v in row["box"]) or not os.path.isfile(os.path.join(ui["dir"], name))):
            return None
        with Image.open(os.path.join(ui["dir"], name)) as image:
            l, t, r, b = row["box"]
            box = (round(l*image.width), round(t*image.height), round(r*image.width), round(b*image.height))
            if box[2] <= box[0] or box[3] <= box[1]:
                return None
            pictures.append(image.crop(box).convert("RGB"))
    width, height = max(p.width for p in pictures), max(p.height for p in pictures)
    scale = seat_width * W / width
    paths = []
    os.makedirs(work, exist_ok=True)
    for j, pic in enumerate(pictures):
        padded = Image.new("RGB", (width, height), pic.getpixel((0, 0)))
        padded.paste(pic)
        path = os.path.join(work, f"recorded-field-{j:02d}.png")
        padded.save(path)
        paths.append(path)
    return dict(width=width, height=height, h=height*scale/H, px=round(rows[-1]["font"]*ui["w"]*scale, 1),
                pictures=paths)


def timeline(l1, *, f0, f_send, f_after, upload, export, edl_a, edl_b, typed, word_f, export_at, lib, ff, work, focus=(0.5, 0.42),
             caption_spans=(), versions=(1, None), ui=None):
    """The typed line as Valmera's own project. upload/export: media facts of the upload and of the export's body (no
    end card); edl_a: the upload as it came in (one block); edl_b: the export's final edit list; export_at: the export
    second the reel's after starts on, at frame f_after. Returns (record, the frame the reel's after takes over)."""
    L = Layer(l1, lib, "tl")
    P, R = L.P, L.R
    up_key, ex_key = P.video("dv_up", upload), P.video("dv_ex", export)
    tla, dur_a = program(edl_a)
    tlb, dur_b = program(edl_b)
    s_after = src_at(tlb, export_at)                  # the upload second the after opens on
    pull_n = 10
    f_pull1 = f0 + pull_n
    f_push0 = max(f_after + 16, f_send + 24)
    f_push1 = f_push0 + 12
    f_land = f_send + 10

    # ---- seats, bottom up: the tracks, the labels, the prompt, then the viewer as large as what is left
    X0, X1 = 0.05, 0.86
    pictures_b, titles_b = MF.pictures_of(edl_b), MF.titles_of(edl_b)
    tracks = [("v", 0.060)]
    if pictures_b or MF.pictures_of(edl_a):
        tracks.append(("p", 0.032))
    if titles_b:
        tracks.append(("t", 0.022))
    if edl_b.get("captions"):
        tracks.append(("c", 0.022))
    gap = 0.008
    y = BOTTOM_Y - sum(h for _, h in tracks) - gap * (len(tracks) - 1)
    seats = {}
    for name, h in tracks:
        seats[name] = (y, h)
        y += h + gap
    tracks_top = seats["v"][0]
    label_base = tracks_top - 0.014
    L.l1["voices"][L.k("dv_l")]["px"] = 46
    px, lines = fit_lines(R, L.k("dv_s"), typed, (RAIL_X - 0.09) * W, 64, 52, max_lines=2)
    pitch = px * 1.2 / H
    lab_cap = R.cap(L.k("dv_l")) / H
    s_cap = R.cap(L.k("dv_s")) / H
    rows = receipt_rows(edl_a, edl_b, dur_a, dur_b)
    R.voices[L.k("dv_r")]["px"] = 54
    r_cap = R.cap(L.k("dv_r")) / H
    row_h = max(r_cap * 1.75, 0.034)
    field = recorded_field(ui, typed, work, (RAIL_X - .09)) if ui else None
    if field and field["px"] < px:
        field = None
    body_h = max(field["h"] if field else 0., s_cap + (len(lines) - 1) * pitch, r_cap + (len(rows) - 1) * row_h)   # the typed lines, then the receipt in their place
    prompt_bot = label_base - lab_cap - 0.022
    prompt_h = 0.024 + lab_cap + 0.022 + body_h + 0.026
    prompt_top = prompt_bot - prompt_h
    avail = prompt_top - 0.016 - TOP_Y
    vw = min(0.92, avail * H * upload["w"] / upload["h"] / W)
    vh = vw * W * upload["h"] / upload["w"] / H
    vbox = [r4(0.5 - vw / 2), r4(TOP_Y), r4(0.5 + vw / 2), r4(TOP_Y + vh)]

    # ---- the ground and the viewer: the upload pulls back from full frame into its seat, then plays there
    desk(L, [f0, f_push1], up_key, upload, focus=focus, src0=max(s_after - (f_after - f0) / FPS, 0.0))
    up_t = lambda f: max(s_after + (f - f_after) / FPS, 0.0)  # noqa: E731
    xs, ys, ss = pull_from_cover(vbox, pull_n, focus)
    P.card("view.pull", [f0, f_pull1], up_key, vbox, t0=up_t(f0), z=4, x=xs, y=ys, s=ss)
    n_v = f_after + 6 - f_pull1
    P.card("view.up", [f_pull1, f_after + 6], up_key, vbox, t0=up_t(f_pull1), z=4, radius=0.01, border=dict(w=0.0012, c="#2E3038"),
           op=[r4(1.0 if f_pull1 + q < f_after else 1.0 - (f_pull1 + q - f_after + 1) / 6.0) for q in range(n_v)])
    eh = vh
    ew = eh * H * export["w"] / export["h"] / W
    ebox = [r4(0.5 - ew / 2), vbox[1], r4(0.5 + ew / 2), vbox[3]]
    land = MF.settle(8, 0.86)
    n_e = f_push0 - f_after
    P.card("view.ex", [f_after, f_push0], ex_key, ebox, t0=export_at, z=5, radius=0.01, s=[r4(v) for v in land] + [1.0] * max(n_e - 8, 0),
           op=[r4(min(1.0, (q + 1) / 3.0)) for q in range(n_e)], border=dict(w=0.0015, c=ACCENT))
    pxs, pys, pss = MF.pull(ebox, f_push1 - f_push0, out=False)
    P.card("view.in", [f_push0, f_push1], ex_key, ebox, t0=export_at + (f_push0 - f_after) / FPS, z=9, x=pxs, y=pys, s=pss)

    # ---- the timeline: the upload as one block at its true length, then the final edit list
    pps = (X1 - X0) / max(dur_a, dur_b)
    yv, hv = seats["v"]
    ka, kb = [tuple(s) for s in tla.segs], [tuple(s) for s in tlb.segs]
    pieces = MF.edl_pieces(ka, kb)
    tile_w = hv * H * upload["w"] / upload["h"] / W
    n_tiles = int(math.ceil((X1 - X0) / tile_w)) + 1
    thumbs = stills(ff, upload["path"], [min((j + 0.5) * tile_w / pps, dur_a - 0.05) for j in range(n_tiles)], os.path.join(work, "device_thumbs"),
                    h=200)
    f_tl0 = f0 + 4
    rise = MF.expo_out(9)
    slide = MF.ease_io(14)
    f_slide = f_send + 6
    removed = 0.0
    for nm, (yy, hh) in seats.items():
        P.block(f"bed.{nm}", [f_tl0, f_push1], [X0, yy, X1, yy + hh], "#16181D", z=1, radius=0.004,
                op=[r4(min(1.0, (q + 1) / 6.0) * 0.92) for q in range(f_push1 - f_tl0)])
    for pi, (s, e, ia, ib) in enumerate(pieces):
        if not ia:
            continue                                   # an insert the upload did not have: the pictures track carries additions
        xa = X0 + s * pps
        xb = X0 + tlb.src_to_out(s) * pps if ib else None
        wd = (e - s) * pps
        if not ib:
            removed += e - s
        f_a = f_tl0 + min(pi, 8)
        f_b = (f_send + 18) if xb is None else f_push1
        n = f_b - f_a
        dx, dy, op = [], [], []
        for q in range(n):
            fr = f_a + q
            r_ = rise[min(q, 8)]
            x_, y_, o_ = 0.0, 0.02 * (1 - r_), min(1.0, (q + 1) / 5.0)
            if xb is None and fr >= f_send + 4:
                u = min((fr - f_send - 4) / 10.0, 1.0)
                y_ += 0.03 * u * u
                o_ *= 1.0 - u
            elif xb is not None and fr >= f_slide:
                x_ = (xb - xa) * slide[min(fr - f_slide, len(slide) - 1)]
            dx.append(x_)
            dy.append(y_)
            op.append(o_)
        box = [xa + 1.5 / W, yv + 0.003, xa + wd - 1.5 / W, yv + hv - 0.003]
        if box[2] - box[0] < 3 / W:
            continue
        seat = P.seat(box)
        P.block(f"v{pi:02d}", [f_a, f_b], box, "#2C2F36", z=2, radius=0.003, x=[r4(seat["x"] + v) for v in dx],
                y=[r4(seat["y"] + v) for v in dy], op=[r4(v) for v in op])
        for j in range(int(math.ceil((box[2] - box[0]) / tile_w - 1e-6))):
            tx0 = box[0] + j * tile_w
            tw = min(tile_w, box[2] - tx0)
            if tw < 6 / W or not thumbs:
                continue
            tm = min(thumbs, key=lambda q: abs(q["t"] - (s + (j + 0.5) * tile_w / pps)))
            key = f"dv_th{int(round(tm['t'] * 100)):05d}"
            if key not in P.assets:
                P.image(key, tm["path"], tm["w"], tm["h"])
            st = P.seat([tx0, box[1], tx0 + tw, box[3]])
            P.card(f"v{pi:02d}.t{j:02d}", [f_a, f_b], key, [tx0, box[1], tx0 + tw, box[3]], z=3, crop=[0.0, 0.0, r4(tw / tile_w), 1.0],
                   x=[r4(st["x"] + v) for v in dx], y=[r4(st["y"] + v) for v in dy], op=[r4(0.92 * v) for v in op])
        if xb is None:                                 # what the sentence removed is marked as it is sent, then it leaves
            n_m = f_b - f_send
            if n_m > 0:
                P.block(f"v{pi:02d}.x", [f_send, f_b], box, ACCENT, z=4, radius=0.003, x=[r4(seat["x"] + v) for v in dx[-n_m:]],
                        y=[r4(seat["y"] + v) for v in dy[-n_m:]], op=[r4(0.65 * min(1.0, (q + 1) / 3.0) * op[-n_m:][q]) for q in range(n_m)])
    # the pictures the edit added land on their track at their programme seconds, each a frame of the export showing it
    landed = []
    if "p" in seats:
        yp, hp = seats["p"]
        for j, o in enumerate(pictures_b[:6]):
            fa = f_land + 4 * j
            box = [X0 + o["start"] * pps, yp + 0.003, X0 + (o["start"] + o["duration_s"]) * pps, yp + hp - 0.003]
            if box[2] - box[0] < 4 / W or fa >= f_push1:
                continue
            th = stills(ff, export["path"], [min(o["start"] + o["duration_s"] / 2, export["dur"] - 0.05)], os.path.join(work, "device_thumbs"), h=240)
            key = P.image(f"dv_pic{j}", th[0]["path"], th[0]["w"], th[0]["h"]) if th else None
            n = f_push1 - fa
            st = P.seat(box)
            ys_ = [r4(st["y"] - 0.03 * (1 - MF.expo_out(8)[min(q, 7)])) for q in range(n)]
            ops = [r4(min(1.0, (q + 1) / 3.0)) for q in range(n)]
            if key:
                P.card(f"p{j}", [fa, f_push1], key, box, z=3, radius=0.003, crop=cover(th[0]["w"] / th[0]["h"], (box[2] - box[0]) * W / ((box[3] - box[1]) * H)),
                       border=dict(w=0.002, c=ACCENT), y=ys_, op=ops)
            else:
                P.block(f"p{j}", [fa, f_push1], box, ACCENT, z=3, radius=0.003, y=ys_, op=ops)
            landed.append(dict(start=o["start"], duration_s=o["duration_s"], f=fa))
            if j < 2:
                L.hit("tick_1", fa + 1, "tick")
    if "t" in seats:
        yt, ht = seats["t"]
        for j, o in enumerate(titles_b[:6]):
            fa = f_land + 2 + 4 * j
            box = [X0 + o["start"] * pps, yt + 0.002, X0 + (o["start"] + o["duration_s"]) * pps, yt + ht - 0.002]
            if box[2] - box[0] >= 4 / W and fa < f_push1:
                P.block(f"ti{j}", [fa, f_push1], box, "#8C7CFF", z=3, radius=0.003, op=[r4(min(1.0, (q + 1) / 3.0)) for q in range(f_push1 - fa)])
    if "c" in seats:                                   # the captions track fills left to right
        yc, hc = seats["c"]
        spans = [tuple(s) for s in caption_spans] or [(0.0, dur_b)]
        for j, (a, b) in enumerate(spans[:60]):
            fa = f_land + 2 + int(round(10 * a / max(dur_b, 1e-3)))
            box = [X0 + a * pps, yc + 0.003, X0 + min(b, dur_b) * pps - 1.5 / W, yc + hc - 0.003]
            if box[2] - box[0] >= 3 / W and fa < f_push1:
                P.block(f"c{j:02d}", [fa, f_push1], box, "#F2C14E", z=3, radius=0.002, op=[r4(min(1.0, (q + 1) / 3.0)) for q in range(f_push1 - fa)])
    # the playhead at the true second of whatever the viewer shows
    rects = []
    y_ph1 = BOTTOM_Y
    for fr in range(f_tl0, f_push0 + 2):
        t = up_t(fr) if fr < f_after else export_at + (fr - f_after) / FPS
        if f_send <= fr < f_after:
            t = up_t(f_send)
        xp = X0 + min(t, dur_a if fr < f_after else dur_b) * pps
        rects.append([r4(xp - 2.0 / W), r4(tracks_top - 0.010), r4(xp + 2.0 / W), r4(y_ph1)])
    P.items.append(dict(id="head", k="flash", f=[f_tl0, f_push0 + 2], mode="fill", c=ACCENT, a=[r4(min(1.0, (q + 1) / 6.0)) for q in range(len(rects))],
                        rect=rects, z=6))
    # the version and the length, as the product numbered them; they flip as the edit lands
    f_flip = f_send + 8
    va, vb = versions
    L.text("lab.va", [f_tl0 + 4, f_flip], f"v{va}", L.k("dv_l"), X0, label_base, DIM, 0.95, rise=True)
    L.text("lab.da", [f_tl0 + 4, f_flip], MF.clock(dur_a), L.k("dv_l"), X1, label_base, DIM, 0.95, rise=True, align="r")
    if vb is not None:
        L.text("lab.vb", [f_flip, f_push1], f"v{vb}", L.k("dv_l"), X0, label_base, INK, 1.0, rise=True)
    L.text("lab.db", [f_flip, f_push1], MF.clock(dur_b), L.k("dv_l"), X1, label_base, INK, 1.0, rise=True, align="r")

    # ---- the prompt: the sentence typed as it is said; on send it prints the receipt's rows
    P.block("prompt", [f_pull1 - 4, f_push1], [0.05, prompt_top, 0.95, prompt_bot], "#121419", z=4, radius=0.014,
            border=dict(w=0.0012, c="#2E3038"), op=[r4(min(1.0, (q + 1) / 5.0) * 0.96) for q in range(f_push1 - f_pull1 + 4)])
    lab_y = prompt_top + 0.024 + lab_cap
    L.text("lab.typed", [f_pull1 - 2, f_push1], TYPED_LABEL, L.k("dv_l"), 0.09, lab_y, CYAN, 1.0, rise=True)
    y_s = lab_y + 0.022 + s_cap
    wf = list(word_f) or [(f_pull1 + 2 + 3 * i, f_pull1 + 5 + 3 * i) for i in range(len(typed.split()))]
    wf = [(max(a, f_pull1 + 1), max(b, max(a, f_pull1 + 1) + 1)) for a, b in wf]
    if field:
        for j, photo in enumerate(field["pictures"]):
            start, end = wf[j][0], wf[j+1][0] if j+1 < len(wf) else f_send + 4
            if end <= start:
                continue
            pk = P.image(f"dv_field{j}", photo, field["width"], field["height"])
            top = y_s - s_cap
            P.card(f"field{j}", [start, end], pk, [.09, top, RAIL_X, top + field["h"]], z=6)
        L.texts.append(dict(t=typed, f=[wf[0][0], f_send+4], px=field["px"], cap_px=field["px"]*.73,
                            x=.09, y=y_s, w=RAIL_X-.09, v="recorded composer"))
    else:
        caret = typed_text(L, "say", f_send + 10, lines, L.k("dv_s"), 0.09, y_s, pitch, wf, fade_from=f_send + 4)
        caret_card(L, "caret", wf[0][0], f_send + 2, caret, L.k("dv_s"), 0.09, y_s)
    P.items.append(dict(id="sent", k="flash", f=[f_send, f_send + 8], mode="fill", c=ACCENT, a=[r4(0.9 * (1 - q / 8.0)) for q in range(8)],
                        rect=[0.05, r4(prompt_bot - 0.005), 0.95, r4(prompt_bot)], z=7))
    first = y_s - s_cap + r_cap
    for j, row in enumerate(rows):
        fa = f_send + 10 + 6 * j
        if fa < f_push1 and first + j * row_h <= prompt_bot - 0.01:
            L.text(f"row{j}", [fa, f_push1], row["text"], L.k("dv_r"), 0.09, first + j * row_h, ACCENT if j == 0 else INK, 1.0, rise=True)
            row["f"] = fa

    # ---- sound: a few, each on what lands
    # A run of keys starts at the first word. Its loudest key can be more
    # than a second into the sample and does not time the first keystroke.
    if "typing_2" in lib:
        L.hit("typing_2", wf[0][0], "typing")
        keys = L.P.sounds[-1]
        keys.update(start_s=wf[0][0] / FPS, trim_s=0., dur_s=max(1, f_send-wf[0][0]) / FPS)
    L.hit("click_1", f_send, "click")
    if removed > 0.2 or landed or rows:
        L.hit("whoosh_soft_1", f_slide + 2, "whoosh")
    rec = L.lay([f0, f_push1])
    rec.update(device="timeline", frames=[f0, f_push1], pull=[f0, f_pull1], typed=[wf[0][0], f_send], send_f=f_send, after_f=f_after,
               push=[f_push0, f_push1], viewer=vbox, export_in_viewer=ebox, prompt=[0.05, r4(prompt_top), 0.95, r4(prompt_bot)],
               tracks={k: [r4(v[0]), r4(v[0] + v[1])] for k, v in seats.items()}, typed_px=field["px"] if field else px, recorded_composer=bool(field), typed_lines=lines, receipt=rows,
               removed_s=round(removed, 3), pictures_landed=landed, durations=[round(dur_a, 3), round(dur_b, 3)], versions=list(versions),
               export_at=round(export_at, 3), upload_at_after=round(s_after, 3))
    return rec, f_push1


# ----------------------------------------------------------------- wipe: a leaning seam reveals the edit

def wipe(l1, *, f0, f_send, f_after, upload, export, edl_b, typed, word_f, export_at, lib, focus=(0.5, 0.42), lean=7.0, typing=False):
    """The typed line as the upload, whole on its blurred copy, with the sentence appearing as it is said; then a
    leaning seam sweeps across and everything it has passed is the export at the same instant. Returns (record, the
    frame the reel's after takes over)."""
    L = Layer(l1, lib, "wp")
    P, R = L.P, L.R
    up_key, ex_key = P.video("dv_up", upload), P.video("dv_ex", export)
    tlb, _ = program(edl_b)
    s_after = src_at(tlb, export_at)
    sweep_n = 20
    f_s0 = max(f_send - 2, f_after - 12)
    f_s1 = f_s0 + sweep_n
    up_t = lambda f: max(s_after + (f - f_after) / FPS, 0.0)  # noqa: E731
    desk(L, [f0, f_s1], up_key, upload, focus=focus, src0=up_t(f0))
    vw = 0.92
    vh = vw * W * upload["h"] / upload["w"] / H
    px, lines = fit_lines(R, L.k("dv_s"), typed, (0.95 - 0.08) * W, 72, 56, max_lines=2)
    pitch = px * 1.2 / H
    L.l1["voices"][L.k("dv_l")]["px"] = 44
    lab_cap, s_cap = R.cap(L.k("dv_l")) / H, R.cap(L.k("dv_s")) / H
    block_h = lab_cap + 0.024 + s_cap + (len(lines) - 1) * pitch
    gap = 0.045
    top = max(TOP_Y + 0.01, 0.5 - (block_h + gap + vh) / 2)
    lab_y = top + lab_cap
    y_s = lab_y + 0.024 + s_cap
    card_y0 = y_s + (len(lines) - 1) * pitch + gap
    vbox = [r4(0.5 - vw / 2), r4(card_y0), r4(0.5 + vw / 2), r4(card_y0 + vh)]
    xs, ys, ss = pull_from_cover(vbox, 10, focus)
    P.card("up.pull", [f0, f0 + 10], up_key, vbox, t0=up_t(f0), z=3, x=xs, y=ys, s=ss)
    P.card("up", [f0 + 10, f_s1], up_key, vbox, t0=up_t(f0 + 10), z=3, radius=0.01, shadow=dict(a=0.34, blur=0.03, dx=0, dy=0.012))
    L.text("lab", [f0 + 8, f_s1], TYPED_LABEL, L.k("dv_l"), 0.08, lab_y, CYAN, 1.0, rise=True)
    wf = list(word_f) or [(f0 + 12 + 3 * i, f0 + 15 + 3 * i) for i in range(len(typed.split()))]
    k = 0
    for li, line in enumerate(lines):
        for wi, wd in enumerate(line.split()):
            pre = " ".join(line.split()[:wi]) + (" " if wi else "")
            a = max(wf[min(k, len(wf) - 1)][0], f0 + 10)
            if a < f_s1:
                it = P.rise(f"w{k:02d}", [a, f_s1], wd, L.k("dv_s"), 0.08 + R.width(L.k("dv_s"), pre) / W, y_s + li * pitch, INK, 1.0, n=6, dy=0.01, note=False)
                it["role"] = "device"
            k += 1
    P.shown.append(typed)
    L.texts.append(dict(t=typed, v=L.k("dv_s"), f=[int(wf[-1][0]), int(f_s1)], px=float(px), cap_px=round(R.cap(L.k("dv_s")), 1), x=0.08, y=r4(y_s),
                        w=r4(max(R.width(L.k("dv_s"), ln) for ln in lines) / W), lines=len(lines)))
    # the seam: off the left edge, then across in one decisive sweep; behind it the export at the true instant
    tan = math.tan(math.radians(lean))
    reach = abs(0.5 * H * tan / W) + 0.03
    ex_t0 = export_at + (f_s0 - f_after) / FPS
    if ex_t0 < 0:
        ex_t0 = 0.0
    curve = MF.ease_io(sweep_n)                       # it gathers, crosses the face fast, and settles off the edge
    xs_seam = [r4(-reach + (1.0 + 2 * reach) * c) for c in curve]
    P.items.append(dict(id="seam", k="join", f=[f_s0, f_s1], mode="seam", stage="top", pic=ex_key, src=[r4(ex_t0), r4(ex_t0 + sweep_n / FPS)],
                        x=xs_seam, lean=lean, edge=dict(w=2.0, a=0.95), dim=1.0, a=1.0))
    if typing:                                        # no narrator: the keys are heard as the sentence lands
        L.hit("typing_2", max(wf[0][0], f0 + 10) + 1, "typing")
    L.hit("whoosh_soft_1", f_s0 + sweep_n // 2 - 2, "whoosh")
    rec = L.lay([f0, f_s1])
    rec.update(device="wipe", frames=[f0, f_s1], sweep=[f_s0, f_s1], send_f=f_send, after_f=f_after, upload_box=vbox, typed_px=px,
               typed_lines=lines, lean=lean, export_at=round(export_at, 3), export_in_sweep=round(ex_t0, 3), upload_at_after=round(s_after, 3))
    return rec, f_s1


# ----------------------------------------------------------------- grid: the films the product made from this clip

def grid(l1, *, f0, f1, tiles, lib, label="exports of one clip", upload=None):
    """tiles: [{path, w, h, sid}] a frame of each real export of this upload, in the order they land. A counter counts
    them up as they land; it ends on their number. Returns the record."""
    n = len(tiles)
    L = Layer(l1, lib, "gr")
    P, R = L.P, L.R
    cols = 4
    rows_n = int(math.ceil(min(n, 12) / cols))
    tw = 0.16 if rows_n == 3 else (0.92 - 3 * 0.012) / cols
    th = tw * W * 16 / 9 / H
    gx, gy = 0.012, 0.010
    gw = cols * tw + (cols - 1) * gx
    x0 = 0.5 - gw / 2
    y0 = TOP_Y
    if upload:
        desk(L, [f0, f1], P.video("dv_up", upload), upload, src0=0.0)
    else:
        P.page("ground", [f0, f1], "#0B0C10", "#121419", grain=0.02)
    step = max(2, min(4, (f1 - f0 - 12) // max(min(n, 12), 1)))
    lands = []
    for j, t in enumerate(tiles[:12]):
        r_, c_ = divmod(j, cols)
        box = [x0 + c_ * (tw + gx), y0 + r_ * (th + gy), x0 + c_ * (tw + gx) + tw, y0 + r_ * (th + gy) + th]
        fa = f0 + 2 + j * step
        if fa >= f1:
            break
        key = P.image(f"dv_g{j}", t["path"], t["w"], t["h"])
        m = f1 - fa
        P.card(f"t{j:02d}", [fa, f1], key, box, z=2, radius=0.006, crop=cover(t["w"] / t["h"], 9 / 16),
               s=[r4(v) for v in MF.settle(8, 0.8)] + [1.0] * max(m - 8, 0), op=[r4(min(1.0, (q + 1) / 3.0)) for q in range(m)])
        lands.append(fa)
    cy = y0 + rows_n * (th + gy) + 0.012
    R.voices[L.k("dv_c")]["px"] = 150
    c_cap = R.cap(L.k("dv_c")) / H
    base = min(cy + c_cap, BOTTOM_Y - 0.01)
    for j, fa in enumerate(lands):                 # the counter steps as each lands; ids end .counterNN for the sound engine
        fb = lands[j + 1] if j + 1 < len(lands) else f1
        it = P.word(f"n.counter{j:02d}", [fa, fb], str(j + 1 if j + 1 < len(lands) else n), L.k("dv_c"), 0.08, base, INK, 1.0, note=False)
        it["role"] = "device"
    L.texts.append(dict(t=str(n), v=L.k("dv_c"), f=[int(lands[-1]) if lands else f0, int(f1)], px=150.0, cap_px=round(R.cap(L.k("dv_c")), 1), x=0.08, y=r4(base),
                        w=r4(R.width(L.k("dv_c"), str(n)) / W)))
    R.voices[L.k("dv_l")]["px"] = 48
    L.text("lab", [lands[-1] if lands else f0, f1], label, L.k("dv_l"), 0.08 + R.width(L.k("dv_c"), str(n)) / W + 0.03, base, INK, 0.92, rise=True)
    for j, fa in enumerate(lands[::max(1, len(lands) // 4)][:4]):
        L.hit("tick_1", fa + 1, "tick")
    rec = L.lay([f0, f1])
    rec.update(device="grid", frames=[f0, f1], tiles=[t.get("sid") for t in tiles[:12]], count=n, lands=lands, tile_w=r4(tw))
    return rec


# ----------------------------------------------------------------- triptych: three exports of one instant

def triptych(l1, *, f0, f1, exports, upload, lib, src_s):
    """exports: three {key_path(media facts), edl}: the same upload's films from three sentences. src_s: the upload
    second all three show at frame f0 (each export enters at the programme second its own edit list maps it to)."""
    L = Layer(l1, lib, "tr")
    P = L.P
    up_key = P.video("dv_up", upload)
    desk(L, [f0, f1], up_key, upload, src0=max(src_s, 0.0))
    x_lo, x_hi, gap = 0.04, 0.96, 0.02
    cw = (x_hi - x_lo - 2 * gap) / 3
    ch = cw * W * 16 / 9 / H
    y0 = TOP_Y + 0.05
    boxes = [[x_lo + i * (cw + gap), y0, x_lo + i * (cw + gap) + cw, y0 + ch] for i in range(3)]
    starts = []
    for i, e in enumerate(exports[:3]):
        key = P.video(f"dv_e{i}", e["media"])
        tl, _ = program(e["edl"])
        t_in = float(tl.src_to_out(src_s))
        starts.append(round(t_in, 3))
        fa = f0 + 3 * i if i else f0
        n = f1 - fa
        land = [r4(v) for v in MF.settle(9, 0.9 if i == 0 else 0.82)] + [1.0] * max(n - 9, 0)
        P.card(f"c{i}", [fa, f1], key, [r4(v) for v in boxes[i]], t0=t_in + (fa - f0) / FPS, z=2, radius=0.008, border=dict(w=0.0012, c="#2A2A2A"),
               s=land[:n], op=[1.0] * n if i == 0 else [r4(min(1.0, (q + 1) / 3.0)) for q in range(n)],
               crop=cover(e["media"]["w"] / e["media"]["h"], 9 / 16))
        L.text(f"n{i}", [fa, f1], f"0{i + 1}", L.k("dv_n"), boxes[i][0], y0 - 0.014, DIM if i else INK, 1.0)
    L.hit("whoosh_soft_1", f0 + 4, "whoosh")
    rec = L.lay([f0, f1], keep=is_caption)
    rec.update(device="triptych", frames=[f0, f1], boxes=[[r4(v) for v in b] for b in boxes], src_s=round(src_s, 3), starts=starts)
    return rec


def common_second(edls, span_s, prefer=None, dur=None):
    """An upload second from which all the edit lists play `span_s` uncut at their own speed (lips together on every
    card), nearest `prefer`. None when they share no such stretch."""
    tls = [program(e)[0] for e in edls]
    dur = dur or min(sum(float(k[1]) - float(k[0]) for k in (e.get("keep") or [[0, 0]])) for e in edls)
    best = None
    t = 0.3
    while t + span_s < (dur or 0) + 1e-6:
        ok = True
        for tl in tls:
            try:
                a = float(tl.src_to_out(t))
                rr = MF.runs(tl, a, a + span_s)
                ok = ok and len(rr) == 1 and abs(rr[0][2] - t) < 0.05
            except Exception:  # noqa: BLE001 - a stretch the timeline cannot play in sync is not a candidate
                ok = False
            if not ok:
                break
        if ok and (best is None or abs(t - (prefer or 0)) < abs(best - (prefer or 0))):
            best = t
        t += 0.25
    return best


# ----------------------------------------------------------------- the call to action

def cta(l1, *, f0, f1, export, lib, src0=0.0):
    """The payoff: over the export's own picture (blurred, darkened), the product's name, what it is, what it does and
    what to do next, each line landing in turn; held to the end of the programme (the brand's end card follows).
    Readable at phone size (44 px and up, the website fitted to the frame, 150 px and up) and on screen at least CTA_S
    when the film gives it room."""
    L = Layer(l1, lib, "ct")
    P, R = L.P, L.R
    key = P.video("dv_cx", export)
    desk(L, [f0, f1], key, export, src0=src0)
    P.block("veil", [f0, f1], [0.0, 0.0, 1.0, 1.0], "#07080B", z=1, op=[r4(min(0.55, 0.55 * (q + 1) / 6.0)) for q in range(f1 - f0)])
    R.voices[L.k("dv_l")]["px"], R.voices[L.k("dv_s")]["px"] = 52, 70
    url = next((t for t, v, _ in CTA_LINES if v == "dv_w"), "valmera.io")
    R.voices[L.k("dv_w")]["px"] = R.fit_px(L.k("dv_w"), url, 0.82 * W, 190)              # the website as large as the frame allows
    gaps = {"dv_w": 0.060, "dv_l": 0.030, "dv_s": 0.030, "dv_u": 0.052}
    y, rows = 0.0, []
    for k, (t, v, ink) in enumerate(CTA_LINES):
        y += (gaps[v] if k else 0.0) + R.cap(L.k(v)) / H
        rows.append((t, L.k(v), ink, y))
    top = max(min(0.47 - y / 2, 0.55 - y), TOP_Y + 0.04)                     # mid-frame, the wide website above the platform's rail
    rows = [(t, v, ink, yy + top) for t, v, ink, yy in rows]
    lands = []
    for k, (t, v, ink, yy) in enumerate(rows):
        fa = f0 + [0, 5, 9, 12, 17][min(k, 4)]
        if fa < f1:
            L.text(f"l{k}", [fa, f1], t, v, 0.09, yy, ink, 1.0, rise=True)
            lands.append(fa)
    L.hit(CTA_RISER, f0 + 1, "riser")             # the film climbs into its call to action, then it lands
    L.hit("whoosh_soft_1", f0 + 1, "whoosh")
    if len(lands) == len(rows):
        L.hit("click_1", lands[-1] + 1, "click")
    rec = L.lay([f0, f1])
    rec.update(device="cta", frames=[f0, f1], seconds=round((f1 - f0) / FPS, 2), lines=[t for t, _, _, _ in rows], lands=lands)
    return rec


# ----------------------------------------------------------------- the showcase: Valmera's edit is the film
#
# Owner, 17 Nov 2026, on the Mark Cuban proof film: "Mark Cuban is talking, then it shows weirdly Valmera. How is this
# marketing the product? How do the viewers understand? Where's the editing capability? Look at the references."
# The references (marketing-rebuild/SPEC.md): the before/after reels put both versions of the same instant side by side
# from frame 0 with a two-word label each (DJrjetsKPHW, DUQzZ_kE3O7); the AI-edit promos show the striking result, then
# the tool's own screen being typed into, then the result again (DZsnwX0yXf8); the brand is the real product (its
# screen, its mark, its site), never a link floating over unrelated footage. So the showcase is four beats:
#   split   the raw clip as uploaded (small, left) and Valmera's export (big, right), the same instant, labelled
#   studio  the real Valmera Studio on a phone, photographed by the recording at every typed word: the sentence lands
#           in the composer, Send, and the result grows out of the screen to full frame
#   export  Valmera's export as delivered, full frame, on its own sound: one continuous thought
#   site    the real valmera.io front page (the recording's own photograph) under the website as the largest line

SPLIT_LABELS = ("RAW CLIP", "1 SENTENCE IN VALMERA")
SPLIT_S = 2.8                  # the hook: the split reads in two seconds, its picture lands in the third
STUDIO_IN_F = 16               # the Studio arriving: 6 frames whole (the clip in its player, the composer), then onto the composer
WORD_F = 5                     # a typed word every 5 frames: a fast typist, every word still seen
READ_F = 12                    # the whole sentence held before Send
SENT_F = 14                    # Send, the sentence in its bubble
PUSH_F = 12                    # the result growing out of the Studio to full frame
SITE_S = 3.4                   # the website: its name and a page to recognise, with the landing
SITE_OVER_S = 2.0              # the website lands under the speaker's last line (his payoff is said over valmera.io), and the
                               # programme ends 1.4 s after his last word: no dead air before the brand's card
LATER_LABEL = "{} LATER"       # the recording's own time to the result, as a label over the push


def showcase_marks(hook, window, n_words, lead_f=3, site_s=SITE_S, over_s=SITE_OVER_S, board_f=0):
    """The showcase's frames, shared by the sound (proof.showcase_supply cuts the voice to them) and the picture
    (showcase). hook / window: export seconds of the split and of the export's own stretch. The export's voice runs to
    the window's end; its picture gives way to the website `over_s` before, so the speaker's last line is said over it."""
    f_h = int(round((float(hook[1]) - float(hook[0])) * FPS))
    f_t = f_h + STUDIO_IN_F
    f_send = f_t + WORD_F * max(int(n_words), 1) + READ_F
    f_b = f_send + (8 if board_f else SENT_F)   # a Shorts board: the sent screen gives way to the board
    f_p = f_b + int(board_f)                    # the export starts here, as a card, on its own voice
    f_x = f_p + PUSH_F                          # full frame
    f_v = f_p + int(round((float(window[1]) - float(window[0])) * FPS))     # the export's voice ends
    f_s = max(f_v - int(round(over_s * FPS)), f_x + int(FPS))
    f_end = f_s + int(round(site_s * FPS))
    return dict(split=[0, f_h], studio=[f_h, f_p], typing=[f_t, f_send], send=f_send, board=[f_b, f_p] if board_f else None,
                push=[f_p, f_x], export=[f_p, f_s],
                export_voice=[f_p, f_v], site=[f_s, f_end], dur_f=max(f_end, f_v), lead_f=lead_f)


def split(l1, *, f0, f1, upload, export, edl_b, export_at, lib, focus=(0.5, 0.42), land_f=None):
    """The hook: the clip as uploaded and Valmera's export of it side by side at the same instant (the export's own
    edit list maps every frame), each under its label; the export is the larger, in front. `land_f`: the frame the
    export's picture lands on, given a hit there (the tutorial's opening beat, proof.lay_hook)."""
    L = Layer(l1, lib, "sp")
    P, R = L.P, L.R
    up_key, ex_key = P.video("dv_up", upload), P.video("dv_ex", export)
    tlb, _ = program(edl_b)
    desk(L, [f0, f1], up_key, upload, focus=focus, src0=src_at(tlb, export_at))
    P.block("veil", [f0, f1], [0.0, 0.0, 1.0, 1.0], "#07080B", z=1, op=0.35)
    ew = 0.55
    eh = ew * W * export["h"] / export["w"] / H
    ex_box = [r4(0.955 - ew), r4(0.255), r4(0.955), r4(0.255 + eh)]
    rw = 0.50
    rh = rw * W * upload["h"] / upload["w"] / H
    ry0 = 0.50 - rh / 2
    up_box = [r4(0.035), r4(ry0), r4(0.035 + rw), r4(ry0 + rh)]
    n = f1 - f0
    land = [r4(v) for v in MF.settle(8, 0.94)] + [1.0] * max(n - 8, 0)     # frame 0 already reads: both cards whole, both labels set
    P.synced("up", [f0, f1], tlb, export_at, up_key, up_box, z=2, radius=0.008, shadow=dict(a=0.40, blur=0.03, dx=0, dy=0.012))
    P.card("ex", [f0, f1], ex_key, ex_box, t0=export_at, z=3, radius=0.012, shadow=dict(a=0.5, blur=0.035, dx=0, dy=0.014), s=land)
    R.voices[L.k("dv_l")]["px"] = 44
    lab_cap = R.cap(L.k("dv_l")) / H
    L.text("lab_up", [f0, f1], SPLIT_LABELS[0], L.k("dv_l"), up_box[0] + 0.01, up_box[1] - 0.014, DIM, 1.0)
    R.voices[L.k("dv_u")]["px"] = R.fit_px(L.k("dv_u"), SPLIT_LABELS[1], (ex_box[2] - ex_box[0]) * W, 46)
    L.text("lab_ex", [f0, f1], SPLIT_LABELS[1], L.k("dv_u"), ex_box[0], ex_box[1] - 0.014, INK, 1.0)
    L.hit("whoosh_soft_1", f0 + 2, "whoosh")
    if land_f is not None and f0 + 4 <= land_f < f1 - 2:
        L.hit("impact_1", land_f, "impact")
    rec = L.lay([f0, f1])
    rec.update(device="split", land_f=land_f, frames=[f0, f1], upload_box=up_box, export_box=ex_box, export_at=r4(export_at),
               upload_at=r4(src_at(tlb, export_at)), labels=list(SPLIT_LABELS), label_cap=r4(lab_cap))
    return rec


def studio_screen(l1, *, marks, ui, typed, export, lib, later=None, upload=None, upload_t0=0.0, board=None):
    """The real Valmera Studio on a phone (the recording's own photographs, `ui`: {dir, w, h, typing: [png per word,
    the empty composer first], sent: png}): the screen arrives, the sentence lands in the composer a word at a time
    while the frame pushes in on it, Send puts it in its bubble, and the result grows out of the screen to full frame.
    The Studio's player plays the upload itself (`upload` from `upload_t0`, muted: the film's sound is the device's),
    seated on the player's rectangle measured on the photograph (`ui["player"]`), so the clip in the Studio is the clip
    of the split, not its first frame (the upload opens on the host's camera). Nothing else is drawn over the screen
    but the time label of the push."""
    L = Layer(l1, lib, "st")
    P, R = L.P, L.R
    f0, f_p = marks["studio"]
    f_t, f_send = marks["typing"]
    iw, ih = float(ui["w"]), float(ui["h"])
    ch = W / iw * ih / H                            # the screen at the frame's width: taller than the frame
    comp = float(ui.get("composer_y") or 0.942)     # the typed line's height on the screen (fraction of its height)
    seat = lambda s, at: r4(at - (comp - 0.5) * ch * s)  # noqa: E731   the card's centre that keeps the composer at `at`
    box = [0.0, r4(-ch / 2 + 0.5), 1.0, r4(ch / 2 + 0.5)]
    P.page("ground", [f0, f_p + PUSH_F], "#0A0A0C", "#0A0A0C")     # the base picture under the screen (the screen covers it)
    shots = list(ui["typing"])
    words = typed.split()
    # where each photograph shows: the empty composer, then one per word, then the sent screen
    spans = [(f0, f_t)] + [(f_t + WORD_F * k, f_t + WORD_F * (k + 1)) for k in range(len(words))]
    spans[-1] = (spans[-1][0], f_send)
    ys, ss = [], []
    for f in range(f0, f_p):
        if f < f_t:                                 # arriving: the whole screen (fitted to the frame's height), then onto the composer
            s_fit = 1.0 / ch
            n_in = max(f_t - f0 - 6, 1)
            q = MF.ease_io(n_in)[min(max(f - f0 - 6, 0), n_in - 1)] if f - f0 >= 6 else 0.0
            s = s_fit + (1.0 - s_fit) * q
            ys.append(r4(0.5 + (seat(1.0, 0.74) - 0.5) * q))
        elif f < f_send:                            # pushing in on the composer while the sentence lands
            q = (f - f_t) / max(f_send - f_t, 1)
            s = 1.0 + 0.15 * (0.5 - 0.5 * math.cos(math.pi * q))     # to 1.15: the sentence at ~51 px, its lines still whole
            ys.append(seat(s, 0.74))
        else:                                       # sent: the frame rises to the bubble
            q = MF.ease_io(8)[min(f - f_send, 7)]
            s = 1.15
            ys.append(r4(seat(s, 0.74) + q * 0.30))
        ss.append(r4(s))
    keys = []
    for j, (a, b) in enumerate(spans[:len(shots)]):
        if b <= a:
            continue
        k = P.image(f"dv_ui{j:02d}", os.path.join(ui["dir"], shots[j]), int(iw), int(ih))
        keys.append(k)
        P.card(f"ui{j:02d}", [a, b], k, box, z=2, x=0.5, y=ys[a - f0:b - f0], s=ss[a - f0:b - f0])
    # while the sentence lands, the screen above the composer steps back (a dark veil, the product's screen untouched
    # under it): the eye goes to the typed line, not to the chat above it (17 Nov: a first cut's reply filled the frame)
    v0, v1 = f_t + 4, f_send + 6
    P.block("focus", [v0, v1], [0.0, 0.0, 1.0, 0.655], "#050608", z=4,
            op=[r4(0.72 * min(1.0, (f - v0 + 1) / 8.0, (v1 - f) / 6.0)) for f in range(v0, v1)])
    k = P.image("dv_uis", os.path.join(ui["dir"], ui["sent"]), int(iw), int(ih))
    y_all, s_all = ys + [ys[-1]] * PUSH_F, ss + [ss[-1]] * PUSH_F
    f_sent_end = marks["board"][0] if board and marks.get("board") else f_p + PUSH_F
    P.card("ui_sent", [f_send, f_sent_end], k, box, z=2, x=0.5, y=y_all[f_send - f0:f_sent_end - f0], s=s_all[f_send - f0:f_sent_end - f0])
    if board and marks.get("board"):            # the Shorts board the sentence made: the whole screen, a slow push in
        bw, bh = float(board["w"]), float(board["h"])
        bch = W / bw * bh / H
        n_b = f_p + PUSH_F - f_sent_end
        bk = P.image("dv_board", board["path"], int(bw), int(bh))
        P.card("board", [f_sent_end, f_p + PUSH_F], bk, [0.0, r4(0.5 - bch / 2), 1.0, r4(0.5 + bch / 2)], z=2, x=0.5, y=0.5,
               s=[r4(min(1.0, 1.0 / bch) * (1.0 + 0.06 * q)) for q in MF.ease_io(n_b)])
        L.hit("whoosh_soft_1", f_sent_end + 1, "whoosh")      # the screen turns to the board
    if upload and ui.get("player"):
        p0, p1 = [float(v) for v in ui["player"]]
        pc = (p0 + p1) / 2
        pbox = [0.0, r4(0.5 - (p1 - p0) * ch / 2), 1.0, r4(0.5 + (p1 - p0) * ch / 2)]
        up_key = P.video("dv_up", upload)
        P.card("player", [f0, f_p + PUSH_F], up_key, pbox, t0=max(float(upload_t0), 0.0), z=3, x=0.5,
               y=[r4(y + (pc - 0.5) * ch * s) for y, s in zip(y_all, s_all)], s=s_all)
    # the result grows out of the Studio: the export, a card on its own voice, to full frame
    ex_key = P.video("dv_ex", export)
    sb = [0.35, 0.35, 0.65, 0.65]
    xs, ys2, zs = MF.pull(sb, PUSH_F, out=False)
    P.card("result", [f_p, f_p + PUSH_F], ex_key, sb, t0=float(marks.get("export_t0") or 0.0), z=4, radius=0.01, x=xs, y=ys2, s=zs)
    if later:                                   # on its own dark pill: the screen behind it is busy with the product's own text
        R.voices[L.k("dv_l")]["px"] = 44
        lab = LATER_LABEL.format(later)
        cap_l, w_l = R.cap(L.k("dv_l")) / H, R.width(L.k("dv_l"), lab) / W
        y_l = TOP_Y + 0.06
        P.block("later.pill", [f_p - 6, f_p + PUSH_F - 2], [0.06, r4(y_l - cap_l - 0.016), r4(0.10 + w_l), r4(y_l + 0.016)], "#07080B", z=5,
                op=0.88, radius=0.012)
        it = L.text("later", [f_p - 6, f_p + PUSH_F - 2], lab, L.k("dv_l"), 0.08, y_l, CYAN, 1.0, rise=True)
        it["z"] = 6
    L.hit("whoosh_soft_1", f0 + 1, "whoosh")
    # the key run starts with the first word and ends as the last lands (its peak placed so its head is on f_t)
    L.hit("typing_2", f_t + int(round(float((P.lib.get("typing_2") or {}).get("peak_s") or 0.0) * FPS)), "typing")
    L.hit("click_1", f_send + 1, "click")
    L.hit("whoosh_soft_1", f_p + PUSH_F // 2, "whoosh")
    rec = L.lay([f0, f_p + PUSH_F])
    rec.update(device="studio", frames=[f0, f_p + PUSH_F], board=bool(board), typing=[f_t, f_send], send_f=f_send, push=[f_p, f_p + PUSH_F], shots=len(keys) + 1,
               typed=typed, typed_px=round(32.0 * W / iw * 1.15, 1) if iw <= 800 else round(16.0 * 3 * W / iw * 1.15, 1),
               screen=dict(w=int(iw), h=int(ih), view=ui.get("view")), later=later)
    return rec


def site(l1, *, f0, f1, page, export, lib, src0=0.0, url="valmera.io", action_media=None):
    """The call to action is the website itself: the real valmera.io front page as a phone shows it (`page`: {path, w,
    h}, the recording's own photograph), under the website's name as the film's largest line. The brand's end card
    follows (the shell's, unchanged). True by construction: everything on it is the site's own words."""
    L = Layer(l1, lib, "ct")
    P, R = L.P, L.R
    key = P.video("dv_cx", export)
    desk(L, [f0, f1], key, export, src0=src0)
    P.block("veil", [f0, f1], [0.0, 0.0, 1.0, 1.0], "#07080B", z=1, op=[r4(min(0.62, 0.62 * (q + 1) / 6.0)) for q in range(f1 - f0)])
    R.voices[L.k("dv_w")]["px"] = R.fit_px(L.k("dv_w"), url, 0.80 * W, 190)
    u_cap = R.cap(L.k("dv_w")) / H
    base = TOP_Y + 0.02 + u_cap
    pw = 0.64
    rows = min(1.0, 0.585 * H / (pw * W) * float(page["w"]) / float(page["h"]))   # the page's top: its name, its promise, its composer
    ph = pw * W * float(page["h"]) * rows / float(page["w"]) / H
    top = base + 0.035
    box = [r4(0.5 - pw / 2), r4(top), r4(0.5 + pw / 2), r4(top + ph)]
    pk = P.image("dv_site", page["path"], int(page["w"]), int(page["h"]))
    n = f1 - f0
    # Travel down the photographed site to its upload control, then show the
    # recorded Studio with the upload in its player. Both use the same seat.
    action = f0 + n // 2 if action_media and n >= 12 else f1
    travel = MF.ease_io(max(2, action - f0))[:action - f0]
    crops = ([[0., r4((1 - rows) * u), 1., r4(rows + (1 - rows) * u)] for u in travel]
             if action_media else [0., 0., 1., r4(rows)])
    scale = ([r4(.96 + .04*u) for u in travel] if action_media else
             (MF.settle(10, .9) + [1.] * n)[:n])
    P.card("page", [f0, action], pk, box, z=3, radius=0.02, crop=crops,
           s=scale, shadow=dict(a=.5, blur=.04, dx=0, dy=.016), op=[r4(min(1., (j+1)/4)) for j in range(action-f0)])
    if action < f1:
        if action_media.get("fps"):
            key = P.video("dv_action", action_media)
            # The completed portrait edit fits as a whole inside the page seat.
            w = min(pw, ph * H / W * action_media["w"] / action_media["h"])
            h = w * W / H * action_media["h"] / action_media["w"]
            center = top + ph / 2
            P.card("action", [action, f1], key, [.5-w/2, center-h/2, .5+w/2, center+h/2],
                   t0=action_media["at"], z=3, radius=.02)
        else:
            photo = P.image("dv_action", action_media["path"], action_media["w"], action_media["h"])
            visible = min(1., ph * H / (pw * W) * action_media["w"] / action_media["h"])
            P.card("action", [action, f1], photo, box, z=3, radius=0.02,
                   crop=[0., 0., 1., visible], s=[r4(1. + .04*u) for u in MF.ease_io(f1-action)])
    w_url = R.width(L.k("dv_w"), url) / W
    L.text("url", [f0 + 4, f1], url, L.k("dv_w"), 0.5 - w_url / 2, base, "#FFFFFF", 1.0, rise=True)
    if action_media is None:
        L.hit(CTA_RISER, f0 + 1, "riser")
        L.hit("whoosh_soft_1", f0 + 1, "whoosh")
        L.hit("click_1", f0 + 5, "click")
    elif action < f1:
        L.hit("click_1", action, "click")
    rec = L.lay([f0, f1])
    rec.update(device="cta", frames=[f0, f1], seconds=round((f1 - f0) / FPS, 2), lines=[url], page_box=box, page_rows=r4(rows),
               url_px=float(R.voices[L.k("dv_w")]["px"]), url_cap_px=round(u_cap * H, 1), lands=[f0, f0 + 4] + ([action] if action < f1 else []))
    return rec


def showcase(*, upload, export, edl_b, typed, window, hook, ui, page, lib, sid="wf2", focus=(0.5, 0.42), lead_f=3, later=None,
             site_s=SITE_S):
    """The whole programme of a film with no narrator (owner, 17 Nov: "an extremely powerful edit ... then the CTA";
    "show the website directly"): split, studio, export, site (see above). `hook` / `window`: export seconds of the
    split and of the export's own stretch; `ui`: the recording's Studio photographs; `page`: its photograph of the
    site. Returns (l1, beats)."""
    mk = showcase_marks(hook, window, len(typed.split()), lead_f=lead_f, site_s=site_s)
    e0, e1 = float(window[0]), float(window[1])
    l1 = dict(l1=1, sid=sid, seed=1117, fps=FPS, frame=[W, H], dur_f=mk["dur_f"], spine=[], voices={}, assets={}, items=[], sound=[],
              film=dict(grain=0.0, vignette=0.0, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0)))
    l1["assets"]["dv_ex"] = dict(kind="video", path=export["path"], w=export["w"], h=export["h"], fps=export["fps"], dur=export["dur"])
    f_p, f_x = mk["push"]
    f_e = mk["export"][1]                       # the picture gives way to the website here; the voice runs on to e1
    l1["items"].append(dict(id="export", k="shot", f=[f_x, f_e], pic="dv_ex", src=[r4(e0 + (f_x - f_p) / FPS), r4(e0 + (f_e - f_p) / FPS)]))
    rec_s = split(l1, f0=0, f1=mk["split"][1], upload=upload, export=export, edl_b=edl_b, export_at=float(hook[0]), lib=lib, focus=focus)
    rec_u = studio_screen(l1, marks=dict(mk, export_t0=e0), ui=ui, typed=typed, export=export, lib=lib, later=later, upload=upload,
                          upload_t0=rec_s["upload_at"] + mk["split"][1] / FPS)
    rec_c = site(l1, f0=f_e, f1=mk["dur_f"], page=page, export=export, lib=lib, src0=e0 + (f_e - f_p) / FPS)
    pictures = [dict(path=export["path"], f=[f_x, f_e], src=[r4(e0 + (f_x - f_p) / FPS), r4(e0 + (f_e - f_p) / FPS)], name="THE EXPORT")]
    beats = dict(format="showcase", marks=mk, split=mk["split"], studio=mk["studio"], reveal=mk["push"], export=[f_p, f_e], cta=mk["site"],
                 pictures=pictures, events=sorted({s["event_f"] for s in l1["sound"]}),
                 look=[0, mk["split"][1] - 10, mk["typing"][0] + 20, mk["send"] + 6, f_x + 30, (f_x + f_e) // 2, f_e + 20, mk["dur_f"] - 2],
                 pace=dict(split_s=round(mk["split"][1] / FPS, 2), studio_s=round((f_p - mk["split"][1]) / FPS, 2),
                           export_s=round((f_e - f_p) / FPS, 2), cta_s=round((mk["dur_f"] - f_e) / FPS, 2)),
                 style="showcase", devices=[rec_s, rec_u, rec_c], export_window=[r4(e0), r4(e1)], hook_window=[r4(hook[0]), r4(hook[1])])
    return l1, beats


# ----------------------------------------------------------------- scale: one sentence, a board of finished shorts
#
# Coordinator, 17 Nov: "a capability that reads as 'how did they do that' in one glance: scale". The Studio's Shorts
# board turns one typed sentence on a long podcast into a board of stories; Edit on each card has the product's own
# editor finish the reel; each is exported. The film opens on every finished short playing at once, counted up, then
# the Studio (the sentence, the board), then one of them full frame on its own sound, then the website.

SCALE_HOOK_S = 3.0             # the grid lands and counts up in two seconds, then holds whole for one
BOARD_F = 36                   # the Studio's board, read for a little over a second


def grid_live(l1, *, f0, f1, tiles, lib, upload=None, top="1 SENTENCE IN VALMERA", unit="SHORTS", sub=None, hero=0):
    """Every short the sentence made, playing at once in a grid (tiles: [{path, w, h, fps, dur, t0}], each a real
    export), landing one after another while a counter counts them; `hero` is the one the film opens up later,
    outlined. Returns the record."""
    n = len(tiles)
    L = Layer(l1, lib, "gl")
    P, R = L.P, L.R
    cols = 5 if n > 8 else 4 if n > 4 else max(n, 1)
    rows_n = int(math.ceil(n / cols))
    gx = 0.012
    tw = (0.92 - (cols - 1) * gx) / cols
    th = tw * W * 16 / 9 / H
    gy = gx * W / H
    R.voices[L.k("dv_u")]["px"] = R.fit_px(L.k("dv_u"), top, 0.84 * W, 50)
    top_cap = R.cap(L.k("dv_u")) / H
    y_top = TOP_Y + 0.03 + top_cap
    y0 = y_top + 0.03
    if upload:
        desk(L, [f0, f1], P.video("dv_up", upload), upload, src0=0.0)
    else:
        P.page("ground", [f0, f1], "#0B0C10", "#121419", grain=0.02)
    P.block("veil", [f0, f1], [0.0, 0.0, 1.0, 1.0], "#07080B", z=1, op=0.45)
    step = 2                                    # frame 0 already holds a film; all of them land inside the first 0.7 s
    lands = []
    for j, t in enumerate(tiles):
        r_, c_ = divmod(j, cols)
        x0 = 0.5 - (cols * tw + (cols - 1) * gx) / 2
        box = [r4(x0 + c_ * (tw + gx)), r4(y0 + r_ * (th + gy)), r4(x0 + c_ * (tw + gx) + tw), r4(y0 + r_ * (th + gy) + th)]
        fa = f0 + j * step
        if fa >= f1:
            break
        key = P.video(f"dv_s{j}", t)
        m = f1 - fa
        P.card(f"t{j:02d}", [fa, f1], key, box, t0=float(t.get("t0") or 0.0), z=3 if j == hero else 2, radius=0.008,
               crop=cover(t["w"] / t["h"], (box[2] - box[0]) * W / ((box[3] - box[1]) * H)),
               s=[r4(v) for v in MF.settle(8, 0.8)] + [1.0] * max(m - 8, 0), op=[r4(min(1.0, (q + 1) / 3.0)) for q in range(m)])
        if j == hero:
            P.block("hero.ring", [fa, f1], [box[0] - 0.006, box[1] - 0.006 * W / H, box[2] + 0.006, box[3] + 0.006 * W / H], ACCENT, z=2,
                    radius=0.011, op=[r4(min(1.0, (q + 1) / 3.0)) for q in range(m)])
        lands.append(fa)
    grid_bottom = y0 + rows_n * (th + gy) - gy
    R.voices[L.k("dv_c")]["px"] = 150
    c_cap = R.cap(L.k("dv_c")) / H
    base = min(grid_bottom + 0.035 + c_cap, BOTTOM_Y - 0.01)
    for j, fa in enumerate(lands):               # the counter steps as each lands
        fb = lands[j + 1] if j + 1 < len(lands) else f1
        it = P.word(f"n.counter{j:02d}", [fa, fb], str(j + 1), L.k("dv_c"), 0.08, base, INK, 1.0, note=False)
        it["role"] = "device"
    L.texts.append(dict(t=str(len(lands)), v=L.k("dv_c"), f=[int(lands[-1]) if lands else f0, int(f1)], px=150.0, cap_px=round(R.cap(L.k("dv_c")), 1),
                        x=0.08, y=r4(base), w=r4(R.width(L.k("dv_c"), str(len(lands))) / W)))
    x_unit = 0.08 + R.width(L.k("dv_c"), str(len(lands))) / W + 0.03
    R.voices[L.k("dv_r")]["px"] = 58
    L.text("unit", [lands[1] if len(lands) > 1 else f0, f1], unit, L.k("dv_r"), x_unit, base - (c_cap - R.cap(L.k("dv_r")) / H) if sub else base, INK, 1.0)
    if sub:
        R.voices[L.k("dv_l")]["px"] = 44
        L.text("sub", [lands[1] if len(lands) > 1 else f0, f1], sub, L.k("dv_l"), x_unit, base, DIM, 1.0)
    L.text("top", [f0, f1], top, L.k("dv_u"), 0.5 - R.width(L.k("dv_u"), top) / W / 2, y_top, INK, 1.0)
    L.hit("whoosh_soft_1", f0 + 1, "whoosh")
    for fa in lands[::max(1, len(lands) // 4)][:4]:
        L.hit("tick_1", fa + 1, "tick")
    rec = L.lay([f0, f1])
    rec.update(device="grid", frames=[f0, f1], count=len(lands), lands=lands, cols=cols, tile_w=r4(tw), hero=hero,
               tiles=[os.path.basename(t["path"]) for t in tiles])
    return rec


def scale_showcase(*, upload, hero, tiles, typed, window, ui, page, lib, sid="wf2", lead_f=3, later=None, site_s=SITE_S, sub=None):
    """The scale film: the grid of every finished short (counted), the Studio (the sentence typed, Send, the board),
    the hero short growing out of the board and playing full frame on its own sound (`window`, its seconds), then the
    website under its last line. `hero`: {path, w, h, fps, dur} of the hero's export, which is tiles[0]."""
    mk = showcase_marks([0.0, SCALE_HOOK_S], window, len(typed.split()), lead_f=lead_f, site_s=site_s, board_f=BOARD_F if ui.get("board") else 0)
    e0, e1 = float(window[0]), float(window[1])
    l1 = dict(l1=1, sid=sid, seed=1118, fps=FPS, frame=[W, H], dur_f=mk["dur_f"], spine=[], voices={}, assets={}, items=[], sound=[],
              film=dict(grain=0.0, vignette=0.0, over_type=False, grade=dict(lift=0, gamma=1, gain=1, sat=1, contrast=1, temp=0, tint=0)))
    l1["assets"]["dv_ex"] = dict(kind="video", path=hero["path"], w=hero["w"], h=hero["h"], fps=hero["fps"], dur=hero["dur"])
    f_p, f_x = mk["push"]
    f_e = mk["export"][1]
    l1["items"].append(dict(id="export", k="shot", f=[f_x, f_e], pic="dv_ex", src=[r4(e0 + (f_x - f_p) / FPS), r4(e0 + (f_e - f_p) / FPS)]))
    rec_g = grid_live(l1, f0=0, f1=mk["split"][1], tiles=tiles, lib=lib, upload=upload, sub=sub, hero=0)
    rec_u = studio_screen(l1, marks=dict(mk, export_t0=e0), ui=ui, typed=typed, export=hero, lib=lib, later=later, board=ui.get("board"))
    rec_c = site(l1, f0=f_e, f1=mk["dur_f"], page=page, export=hero, lib=lib, src0=e0 + (f_e - f_p) / FPS)
    beats = dict(format="showcase", variant="scale", marks=mk, grid=mk["split"], studio=mk["studio"], reveal=mk["push"], export=[f_p, f_e],
                 cta=mk["site"], pictures=[dict(path=hero["path"], f=[f_x, f_e], name="THE EXPORT")],
                 events=sorted({s["event_f"] for s in l1["sound"]}), look=[0, mk["split"][1] - 6, mk["typing"][0] + 30, (mk["board"] or [f_p])[0] + 10,
                                                                            f_x + 20, (f_x + f_e) // 2, f_e + 20, mk["dur_f"] - 2],
                 pace=dict(grid_s=round(mk["split"][1] / FPS, 2), studio_s=round((f_p - mk["split"][1]) / FPS, 2),
                           export_s=round((f_e - f_p) / FPS, 2), cta_s=round((mk["dur_f"] - f_e) / FPS, 2)),
                 style="showcase", devices=[rec_g, rec_u, rec_c], export_window=[r4(e0), r4(e1)], shorts=len(tiles))
    return l1, beats
