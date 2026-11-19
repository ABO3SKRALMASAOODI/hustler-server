"""picks: the pictures a rhythm, layout, match or callback device draws from, and the plain geometry they share.

Signal only, like every palette module: sizes and clocks from the files, luminance from pixels, busy regions from
gradients. Which pictures belong to a moment is the caller's (the Director's ``pics``, else the film's pool, else the
pictures the scene already holds); nothing here reads a meaning.

A pool entry (what the workflows hand in beside the scene, ``apply(..., pool=...)`` or ``l1["pool"]``) is a dict:
    {"id"?: str, "path": str, "kind": "clip"|"video"|"photo"|"image", "w"?, "h"?, "seconds"?: float,
     "faces"?: [[x, y, w, h(, eyes_y)]] or [{"box": [x0, y0, x1, y1], "eyes": [[x, y], [x, y]]}],
     "regions"?: [[x0, y0, x1, y1]], "seg"?: int, "subject"?: str}
A device never needs more than "path"; the rest is read from the file when it is missing.
"""
import hashlib
import math
import os
import random

import numpy as np

from .. import bake, motion
from ..alien import sense

TAKE_PREFIX = ("main", "own:")
FLASH_STEP = 0.10          # WCAG 2.x general flash: an opposing pair of relative-luminance changes of 10% or more...
FLASH_DARK = 0.80          # ...where the darker side sits under 0.80
FLASH_PER_S = 3            # ...more than three in any second is a flash hazard; a strobe of pictures stays under it
GAIN = (0.45, 2.4)         # the most a strobe's grade brings a picture toward its neighbours' light


# ----------------------------------------------------------------- the scene

def geometry(l1):
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    return W, H, float(l1.get("fps") or 30)


def is_take(aid):
    a = str(aid or "")
    return a == "main" or a.startswith("own:")


def shots(l1):
    return sorted((i for i in l1.get("items") or [] if i.get("k") == "shot"), key=lambda i: i["f"][0])


def shot_at(l1, f):
    return sense.shot_at(l1, int(f))


def cuts(l1):
    """Frames where the base picture changes to another item (every shot start but the first)."""
    return [s["f"][0] for s in shots(l1)][1:]


def seed_of(l1, ask, salt=""):
    h = hashlib.sha256(f"{l1.get('seed') or 0}|{ask.get('id')}|{ask.get('dev')}|{salt}".encode()).hexdigest()
    return int(h[:8], 16)


def args_of(ask):
    """The Director's contract keeps a device's own parameters under ``args``; a hand-written ask puts them on the ask."""
    out = dict(ask.get("args") or {})
    out.update({k: v for k, v in ask.items() if k != "args"})
    return out


def _frame(x, fps):
    if isinstance(x, dict):
        if x.get("f") is not None:
            return x["f"]
        if x.get("t") is not None:
            return float(x["t"]) * fps
        return None
    return x


def span(l1, ask, default_s=1.0, least=2):
    """(a, z) programme frames of an ask: ``at`` (a frame, [a, z], {"f": ...} or {"t": seconds}) and ``until``
    (a frame or {"f"}/{"t"}), else ``at`` plus ``frames`` or ``default_s`` seconds, inside the film."""
    W, H, fps = geometry(l1)
    dur = int(l1["dur_f"])
    at = _frame(ask.get("f") if ask.get("f") is not None else ask.get("at"), fps)
    if isinstance(at, (list, tuple)) and len(at) == 2:
        a, z = float(at[0]), float(at[1])
    else:
        a = float(at if at is not None else 0)
        until = _frame(ask.get("until"), fps)
        if isinstance(until, (list, tuple)):
            until = until[-1]
        z = float(until) if until is not None else a + float(ask.get("frames") or round(default_s * fps))
    a = int(round(min(max(a, 0), dur - least)))
    z = int(round(min(max(z, a + least), dur)))
    return a, z


# ----------------------------------------------------------------- pictures

def _box_face(f):
    """[x, y, w, h, eyes_y?] from either face shape the workflows carry."""
    if isinstance(f, dict):
        b = f.get("box") or []
        if len(b) != 4:
            return None
        out = [b[0], b[1], b[2] - b[0], b[3] - b[1]]
        eyes = f.get("eyes") or []
        if eyes and all(len(e) == 2 for e in eyes):
            out.append(sum(e[1] for e in eyes) / len(eyes))
        return [float(v) for v in out]
    if isinstance(f, (list, tuple)) and len(f) >= 4:
        return [float(v) for v in f[:5]]
    return None


def faces_of(rec):
    return [q for q in (_box_face(f) for f in (rec or {}).get("faces") or []) if q]


class Pic:
    """One picture a device may cut to: an asset of the scene, or a pool entry that becomes one."""

    def __init__(self, aid, path, kind, w, h, dur, faces=(), regions=(), rng=None, new=None, meta=None):
        self.aid, self.path, self.kind = aid, path, kind
        self.w, self.h, self.dur = int(w), int(h), float(dur or 0.0)
        self.faces, self.regions = list(faces or []), list(regions or [])
        self.rng = rng                    # [t0, t1] of the source a viewer may see (a clip's verified slice); None: all
        self.new = new                    # the asset to add to the scene, when it is not there yet
        self.meta = meta or {}

    @property
    def video(self):
        return self.kind == "video"

    def asset(self):
        a = dict(kind=self.kind, path=self.path, w=self.w, h=self.h)
        if self.video:
            a.update(fps=float(self.meta.get("fps") or 30.0), dur=self.dur)
        return a

    def __repr__(self):
        return f"Pic({self.aid}, {self.kind}, {self.w}x{self.h}, {self.dur:.2f}s)"


def _abs(work, p):
    return p if os.path.isabs(p) else os.path.join(work or "", p)


def _probe(path):
    try:
        i = sense.probe(path)
        return int(i["w"]), int(i["h"]), float(i.get("dur") or 0.0), float(i.get("fps") or 0.0)
    except Exception:      # noqa: BLE001 - a picture that cannot be read is no candidate
        return 0, 0, 0.0, 0.0


def pic_of_asset(l1, work, aid, meta=None):
    a = (l1.get("assets") or {}).get(aid)
    if not a or a.get("kind") not in ("image", "video") or not a.get("path"):
        return None
    p = _abs(work, a["path"])
    w, h, dur, fps = int(a.get("w") or 0), int(a.get("h") or 0), float(a.get("dur") or 0.0), 0.0
    if not (w and h) or (a["kind"] == "video" and not dur):
        if not os.path.exists(p):
            return None
        w, h, dur, fps = _probe(p)
    if not (w and h):
        return None
    m = meta or {}
    return Pic(aid, p, a["kind"], w, h, dur if a["kind"] == "video" else 0.0, faces_of(m), m.get("regions") or [], meta=dict(a))


def asset_id_for(path):
    return "pb" + hashlib.sha256(os.path.abspath(path).encode()).hexdigest()[:8]


def pic_of_pool(l1, work, rec):
    p = rec.get("path")
    if not p:
        return None
    p = _abs(work, p)
    assets = l1.get("assets") or {}
    same = next((k for k, a in assets.items() if a.get("path") and os.path.abspath(_abs(work, a["path"])) == os.path.abspath(p)), None)
    kind = "video" if str(rec.get("kind") or "").lower() in ("clip", "video") else "image"
    if same:
        pic = pic_of_asset(l1, work, same, rec)
        if pic:
            return pic
    w, h, dur = int(rec.get("w") or 0), int(rec.get("h") or 0), float(rec.get("seconds") or rec.get("dur") or 0.0)
    if not (w and h) or (kind == "video" and not dur):
        if not os.path.exists(p):
            return None
        w, h, dur, fps = _probe(p)
        if fps == 0.0:
            kind = "image"
    if not (w and h):
        return None
    aid = str(rec.get("id") or asset_id_for(p))
    if aid in assets:
        aid = asset_id_for(p)
    pic = Pic(aid, p, kind, w, h, dur if kind == "video" else 0.0, faces_of(rec), rec.get("regions") or [], meta=dict(rec))
    pic.new = pic.asset()
    return pic


def pool_of(l1, ask, pool=None):
    """Pool records (dicts with a path) handed beside the scene: the caller's, the ask's, or the scene's ``pool``."""
    got = pool if isinstance(pool, (list, tuple)) and all(isinstance(r, dict) for r in pool) else None
    return list(got if got is not None else (ask.get("pool_recs") or l1.get("pool") or []))


def scene_pool(l1, sense_):
    """Asset ids the palette's sense offers as the film's pool (palette.Sense.pictures), in its order; [] without one."""
    fn = getattr(sense_, "pictures", None) if sense_ is not None else None
    if not callable(fn):
        return []
    try:
        return [a for a in fn() or [] if a in (l1.get("assets") or {})]
    except Exception:      # noqa: BLE001 - the scene's own pictures stand in
        return []


def sense_faces(sense_, aid, t=0.0):
    """Faces the palette's sense finds on a picture ([[x0, y0, x1, y1, eye_y]]) as [x, y, w, h, eye_y]."""
    fn = getattr(sense_, "faces", None) if sense_ is not None else None
    if not callable(fn):
        return []
    try:
        rows = fn(aid, t) or []
    except Exception:      # noqa: BLE001
        return []
    out = []
    for r in rows:
        if isinstance(r, (list, tuple)) and len(r) >= 4 and r[2] > r[0] and r[3] > r[1]:
            out.append([float(r[0]), float(r[1]), float(r[2] - r[0]), float(r[3] - r[1])] + ([float(r[4])] if len(r) > 4 else []))
    return out


def pictures(l1, work, ask, pool=None, take=False, least=0, sense_=None):
    """The pictures an ask may draw from, in the caller's order: its ``pics`` (asset ids, pool ids or paths), then the
    pool (records beside the scene, and the asset ids the palette's sense names), then the scene's own pictures. The
    take is left out unless ``take``. No picture twice."""
    recs = pool_of(l1, ask, pool)
    by_id = {str(r.get("id")): r for r in recs if r.get("id") is not None}
    by_path = {os.path.abspath(_abs(work, r["path"])): r for r in recs if r.get("path")}
    meta = ask.get("meta") or {}
    out, seen = [], set()

    def add(pic):
        if pic and (take or not is_take(pic.aid)) and os.path.abspath(pic.path) not in seen:
            seen.add(os.path.abspath(pic.path))
            out.append(pic)
    for ref in ask.get("pics") or []:
        ref = str(ref)
        if ref in (l1.get("assets") or {}):
            add(pic_of_asset(l1, work, ref, meta.get(ref) or by_path.get(os.path.abspath(_abs(work, l1["assets"][ref].get("path") or "")))
                             or by_path.get(os.path.abspath(_abs(work, (l1["assets"][ref].get("from") or ""))))))
        elif ref in by_id:
            add(pic_of_pool(l1, work, by_id[ref]))
        elif os.path.abspath(_abs(work, ref)) in by_path:
            add(pic_of_pool(l1, work, by_path[os.path.abspath(_abs(work, ref))]))
        elif os.path.exists(_abs(work, ref)):
            add(pic_of_pool(l1, work, {"path": ref}))
    if not ask.get("only"):
        # the pool nearest the moment first: the pictures found for the words being said, then their neighbours'
        W, H, fps = geometry(l1)
        a, z = span(l1, ask)
        mid = (a + z) / 2 / fps

        def far(r):
            t = r.get("t")
            if isinstance(t, (list, tuple)) and len(t) == 2:
                return 0.0 if float(t[0]) <= mid <= float(t[1]) else min(abs(mid - float(t[0])), abs(mid - float(t[1])))
            return 1e9
        for r in sorted(recs, key=far):
            add(pic_of_pool(l1, work, r))
        ids = scene_pool(l1, sense_) or sorted(l1.get("assets") or {})
        for aid in ids:
            if aid == "matte":
                continue
            p_ = os.path.abspath(_abs(work, (l1.get("assets") or {}).get(aid, {}).get("path") or ""))
            add(pic_of_asset(l1, work, aid, meta.get(aid) or by_path.get(p_)))
    return out


def cover_on(pic, W, H):
    """The cover crop of a picture centred on its subject: the supply's measured subject place (``x``, the picture
    check's subject_x), else its biggest face, else the middle."""
    x = pic.meta.get("x") if isinstance(pic.meta.get("x"), (int, float)) else None
    y = 0.42
    if pic.faces:
        f = max(pic.faces, key=lambda q: q[2] * q[3])
        x = f[0] + f[2] / 2 if x is None else x
        y = f[4] if len(f) > 4 else f[1] + f[3] * 0.42
    cam = cam_of(pic, W, H)
    if x is None:
        return sense.cover(pic.w, pic.h, W, H)
    return cam.cover(focus=(float(x), float(y)), at=(0.5, 0.40 if pic.faces else 0.42))


def new_assets(pics):
    return {p.aid: p.new for p in pics if p.new}


# ----------------------------------------------------------------- moments and framings

def moments(pic, n, length_s, rng):
    """n source times [t0, t1] of a clip, each ``length_s`` long, spread over its whole length (a list burst may show
    one clip several times, never the same seconds twice). A still gives n zero-length moments."""
    if not pic.video:
        return [[0.0, 0.0]] * n
    lo, hi = (pic.rng or [0.0, pic.dur])
    lo, hi = max(float(lo), 0.0), min(float(hi), pic.dur) if pic.dur else float(hi)
    room = max(hi - lo - length_s - 1.0 / 30, 0.0)
    out = []
    for k in range(n):
        u = (k + 0.5) / n if n > 1 else 0.5
        u = min(max(u + (rng.random() - 0.5) * 0.5 / max(n, 1), 0.0), 1.0)
        t0 = lo + room * u
        out.append([round(t0, 4), round(t0 + length_s, 4)])
    return out


def cam_of(pic, W, H, max_up=None):
    return motion.Cam(pic.w, pic.h, W / H, out_w=W, max_up=float(max_up or motion.MAX_UP))


def framings(pic, W, H, still=None):
    """The distinct framings a picture offers the frame: [(name, crop)], the widest first (pace.menu)."""
    from .. import pace
    cam = cam_of(pic, W, H)
    regions = pic.regions
    if not regions and not pic.faces and still is not None:
        try:
            regions = pace.regions_of(still, n=3, size=0.5)
        except Exception:      # noqa: BLE001
            regions = []
    sh = {"faces": pic.faces, "regions": regions}
    return pace.menu(cam, sh, W)


def still_of(pic, t=None, size=(96, 96), gray=True):
    try:
        tt = 0.0 if not pic.video else float(t if t is not None else (pic.rng or [0.0, pic.dur])[0] + 0.05)
        return sense.still(pic.path, tt, size=size, gray=gray)
    except Exception:      # noqa: BLE001
        return None


def luminance(pic, t=None, crop=None):
    """Relative luminance (0..1, linear light) of what a framing shows: its pixels, not its name."""
    try:
        tt = 0.0 if not pic.video else float(t if t is not None else 0.05)
        arr = sense.still(pic.path, tt, size=(72, 128) if pic.w < pic.h else (128, 72), gray=False)
    except Exception:      # noqa: BLE001
        return 0.3
    if arr is None:
        return 0.3
    if crop:
        h, w = arr.shape[:2]
        x0, y0, x1, y1 = crop
        arr = arr[int(max(y0, 0) * h):max(int(min(y1, 1) * h), int(max(y0, 0) * h) + 1),
                  int(max(x0, 0) * w):max(int(min(x1, 1) * w), int(max(x0, 0) * w) + 1)]
    lin = (arr.astype(np.float32) / 255.0) ** 2.2
    return float((lin @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean())


# ----------------------------------------------------------------- strobes that stay under the flash line

def flashes(lumas, lens, fps):
    """The most flashes (opposing pairs of luminance steps of FLASH_STEP or more with the darker side under
    FLASH_DARK) in any one second of a run of pictures ``lumas`` lasting ``lens`` frames each."""
    t, ev = 0, []
    for i in range(1, len(lumas)):
        t += lens[i - 1]
        d = lumas[i] - lumas[i - 1]
        if abs(d) >= FLASH_STEP and min(lumas[i], lumas[i - 1]) < FLASH_DARK:
            ev.append((t, 1 if d > 0 else -1))
    pairs = [ev[i][0] for i in range(1, len(ev)) if ev[i][1] != ev[i - 1][1]]
    best = 0
    for i, f in enumerate(pairs):
        best = max(best, sum(1 for g in pairs[i:] if g - f < fps))
    return best


def flash_safe(lumas, lens, fps, order=None):
    """(order, gains): a strobe of pictures laid so that the frame's luminance never flashes more than FLASH_PER_S
    times a second. Without an order the pictures run as a ramp of light (no opposing steps); with one, the pictures
    that step furthest from their neighbours are brought toward them by their grade's gain, as little as it takes."""
    n = len(lumas)
    gains = [1.0] * n
    if order is None:
        order = sorted(range(n), key=lambda i: lumas[i])
        return order, gains
    lens_o = [lens[k] for k in range(len(order))]
    cur = [lumas[i] for i in order]
    for _ in range(16):
        got = [lumas[i] * g ** 2.2 for i, g in zip(order, [gains[i] for i in order])]
        if flashes(got, lens_o, fps) <= FLASH_PER_S:
            return order, gains
        m = float(np.median(cur))
        cur = [m + (c - m) * 0.75 for c in cur]
        for k, i in enumerate(order):
            if lumas[i] > 1e-4:
                gains[i] = float(min(max((cur[k] / lumas[i]) ** (1 / 2.2), GAIN[0]), GAIN[1]))
    # pictures too far apart in light for any grade to bring together: they run as a path of light instead
    return sorted(range(n), key=lambda i: lumas[i]), [1.0] * n


# ----------------------------------------------------------------- curves

def decel(n, peak=0.18, k=4.0):
    """0..1 over n frames: a rigid move whose speed peaks early (``peak`` of the way in, the references' 9-35%) and
    dies away, as the fast pushes on archive pictures do."""
    if n <= 1:
        return [1.0] * max(n, 0)
    out = []
    p = max(min(peak, 0.6), 0.02)
    for i in range(n):
        u = i / (n - 1)
        if u < p:
            v = 0.5 * (u / p) ** 2 * p * k / (1 + p * k)          # a short rise to full speed
        else:
            q = (u - p) / (1 - p)
            head = 0.5 * p * k / (1 + p * k)
            v = head + (1 - head) * (1 - math.exp(-k * q)) / (1 - math.exp(-k))
        out.append(v)
    out[-1] = 1.0
    return out


def scale_crop(c, k, cx=None, cy=None):
    """A crop scaled by k about (cx, cy) (its centre by default): k < 1 is tighter. Kept inside the picture."""
    x0, y0, x1, y1 = c
    cx = (x0 + x1) / 2 if cx is None else cx
    cy = (y0 + y1) / 2 if cy is None else cy
    w, h = (x1 - x0) * k, (y1 - y0) * k
    w, h = min(w, 1.0), min(h, 1.0)
    nx = min(max(cx - (cx - x0) * k, 0.0), 1.0 - w)
    ny = min(max(cy - (cy - y0) * k, 0.0), 1.0 - h)
    return [nx, ny, nx + w, ny + h]


def shift_crop(c, dx, dy):
    """A crop moved by (dx, dy) of its own size, kept inside the picture."""
    x0, y0, x1, y1 = c
    w, h = x1 - x0, y1 - y0
    nx = min(max(x0 + dx * w, 0.0), 1.0 - w)
    ny = min(max(y0 + dy * h, 0.0), 1.0 - h)
    return [nx, ny, nx + w, ny + h]


def r5(c):
    return [round(float(v), 5) for v in c]


def crops(l1, work, sh):
    """A base shot's crop on each of its frames, as a list (the default cover crop spelt out)."""
    W, H, fps = geometry(l1)
    n = sh["f"][1] - sh["f"][0]
    c = sh.get("crop")
    if isinstance(c, dict):
        c = bake.resolve(c, n)
    if isinstance(c, list) and c and isinstance(c[0], (list, tuple)):
        out = [list(map(float, x)) for x in c[:n]]
        return out + [out[-1]] * (n - len(out))
    if isinstance(c, (list, tuple)) and len(c) == 4:
        return [list(map(float, c))] * n
    pic = pic_of_asset(l1, work, sh.get("pic"))
    base = sense.cover(pic.w, pic.h, W, H) if pic else [0.0, 0.0, 1.0, 1.0]
    return [list(base)] * n


def rng_of(l1, ask, salt=""):
    return random.Random(seed_of(l1, ask, salt))


def carve(l1, a, z):
    return sense.carve(l1, a, z)


def inherit(was):
    """What a replaced base shot hands the pictures that stand in for it: the film's look on that stretch."""
    return {k: v for k, v in (was[0] if was else {}).items() if k in ("look",)}
