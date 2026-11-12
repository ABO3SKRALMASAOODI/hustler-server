"""E1: the plan. A scene (the Score's L1) read, checked and baked: what the runtime draws from.

frame(f) is a pure function of the plan and the sources. Nothing here reads a model or a meaning.
On disk a plan is three files, named by what they hold: ``plan.json``, ``curves.bin`` (Float32, every
per-frame number) and ``atlas.png`` (type), plus ``masks.png`` when a junction grows from a glyph.
"""
import collections
import hashlib
import json
import os
import zlib

import numpy as np

from . import bake

PLAN_VERSION = 1
KINDS = ("shot", "card", "flash", "word", "join")
# point treatments, shared by shot.treat, card.treat and flash; the rest of a flash's modes act on the frame
TREATS = {"neg": 1, "threshold": 2, "burn": 3, "mono": 4, "tint": 5, "complement": 6, "posterise": 7, "halftone": 8,
          "solarise": 9, "thermal": 14, "night_vision": 15,
          # re-renders that read the picture's neighbourhood or move its pixels (ports of variants.py)
          "line": 20, "blueprint": 21, "xray": 22, "kaleidoscope": 25, "pixel_stretch": 26}
MODES = dict(TREATS, fill=10, pool=11, mosaic=12, recolour=13)
PICTURE_ONLY = ("kaleidoscope", "pixel_stretch")       # they move pixels, so they need a picture, not a frame
BLENDS = {"normal": 0, "difference": 1, "multiply": 2}
JOINS = {"seam": 1, "weave": 2, "glyph": 3}
PLATES = {"paper": 1, "static": 2}
IDENTITY = {"lift": 0.0, "gamma": 1.0, "gain": 1.0, "sat": 1.0, "contrast": 1.0, "temp": 0.0, "tint": 0.0}
# per-frame fields by kind: numbers, and rects
ANIM = {"shot": ("blur",), "card": ("x", "y", "s", "rot", "op", "blur"), "flash": ("a", "th"),
        "word": ("x", "y", "op", "s", "blur", "grow"), "join": ("t", "x", "s", "a", "dim")}
ANIM_RECT = {"shot": ("crop",), "card": ("crop",), "flash": ("rect",), "word": (), "join": ("crop",)}


def A(x, f, a):
    """Animatable: a number, or one number per frame of the item."""
    if isinstance(x, (list, tuple)):
        return x[min(max(f - a, 0), len(x) - 1)]
    return x


def AR(r, f, a):
    """Animatable rect: one rect, or one rect per frame of the item."""
    if r is None:
        return None
    if isinstance(r[0], (list, tuple)):
        return r[min(max(f - a, 0), len(r) - 1)]
    return r


def hex3(c, default=(1.0, 1.0, 1.0)):
    if not c:
        return default
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def held(x, f, a):
    """True when an animatable value is not moving on this frame. A reflowing word jumps, then holds."""
    if not isinstance(x, (list, tuple)):
        return True
    i = min(max(f - a, 0), len(x) - 1)
    return (i > 0 and x[i] == x[i - 1]) or (i + 1 < len(x) and x[i] == x[i + 1]) or len(x) == 1


def scene_of(doc):
    """The scene layer of whatever was handed in: an L1 itself, or an EDL carrying one under ``scene``."""
    if isinstance(doc, dict) and "items" in doc and "dur_f" in doc:
        return doc
    if isinstance(doc, dict) and isinstance(doc.get("scene"), dict):
        return doc["scene"]
    raise ValueError("no scene layer: expected an L1, or an EDL with one under 'scene'")


def sized(l1, size):
    """The same scene at another frame size of the same shape. Places are frame fractions and most
    lengths are written at 1080 wide, so only two things are in output pixels: the frame and each
    voice's ``px``. Raises ValueError when the shape differs: that is a different film."""
    W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
    w, h = int(size[0]), int(size[1])
    if (w, h) == (W, H):
        return l1
    if w % 2 or h % 2 or abs(w / h - W / H) > 0.01 * W / H:
        raise ValueError(f"size {w}x{h} is not the shape of {W}x{H}")
    k = w / W
    return dict(l1, frame=[w, h], voices={n: dict(v, px=float(v.get("px", 58)) * k) for n, v in (l1.get("voices") or {}).items()})


def _curve_names(x, out):
    if isinstance(x, dict):
        if "curve" in x:
            out.add(x["curve"])
        for v in x.values():
            _curve_names(v, out)
    elif isinstance(x, list) and x and isinstance(x[0], (dict, list)):
        for v in x:
            _curve_names(v, out)


def eligible(doc):
    """(ok, reasons). False for anything this engine does not know how to draw, decided from the
    plan alone, before any work. The caller renders an ineligible film on today's renderer."""
    why = []
    try:
        l1 = scene_of(doc)
    except ValueError as e:
        return False, [str(e)]
    if l1.get("l1") not in (None, 1):
        why.append(f"scene version {l1.get('l1')!r}")
    try:
        dur = int(l1["dur_f"])
        W, H = [int(v) for v in (l1.get("frame") or [1080, 1920])]
        if dur <= 0 or W < 16 or H < 16 or W % 2 or H % 2:
            why.append(f"frame {W}x{H}, {dur} frames")
    except (KeyError, TypeError, ValueError):
        return False, why + ["no usable dur_f or frame"]
    ids, cov, names = set(), np.zeros(dur + 1, np.int32), set()
    for it in l1.get("items") or []:
        k = it.get("k")
        if k not in KINDS:
            why.append(f"unknown kind {k!r}")
            continue
        i = it.get("id")
        if not i or i in ids:
            why.append(f"missing or repeated id {i!r}")
        ids.add(i)
        f = it.get("f") or []
        if len(f) != 2 or not (0 <= int(f[0]) < int(f[1]) <= dur):
            why.append(f"{i}: f {f} outside 0..{dur}")
            continue
        _curve_names(it, names)
        if k == "shot":
            cov[int(f[0]):int(f[1])] += 1
        if k == "flash" and it.get("mode") not in MODES:
            why.append(f"{i}: unknown flash mode {it.get('mode')!r}")
        if k == "flash" and it.get("mode") in PICTURE_ONLY:
            why.append(f"{i}: {it.get('mode')} needs a picture, not a flash")
        if k in ("shot", "card", "join") and it.get("treat") and (it["treat"].get("mode") not in TREATS):
            why.append(f"{i}: unknown treat {it['treat'].get('mode')!r}")
        if k in ("shot", "card") and it.get("pic") == "plate" and (it.get("plate") or {}).get("kind", "paper") not in PLATES:
            why.append(f"{i}: unknown plate {(it.get('plate') or {}).get('kind')!r}")
        if k == "word" and it.get("blend") not in (None, *BLENDS):
            why.append(f"{i}: unknown blend {it.get('blend')!r}")
        if k == "word" and (not isinstance(it.get("t"), str) or it.get("v") not in (l1.get("voices") or {})):
            why.append(f"{i}: word without text or voice")
        if k == "join" and it.get("mode") not in JOINS:
            why.append(f"{i}: unknown join {it.get('mode')!r}")
    bad = int((cov[:dur] != 1).sum())
    if bad:
        why.append(f"{bad} frames without exactly one base picture")
    for n in sorted(names - set(bake.CURVES)):
        why.append(f"unknown curve {n!r}")
    from . import type as T
    if T.hb is None:
        why.append("uharfbuzz is not installed")
    return not why, why[:40]


def fingerprint(path):
    """A cheap identity for a source: size, head and tail. Enough to notice a replaced file."""
    h = hashlib.sha256()
    try:
        n = os.path.getsize(path)
        h.update(str(n).encode())
        with open(path, "rb") as f:
            h.update(f.read(262144))
            if n > 524288:
                f.seek(n - 262144)
                h.update(f.read(262144))
    except OSError:
        h.update(b"missing")
    return h.hexdigest()[:16]


def canon(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class Plan:
    """What the runtime reads. Built by ``compile.compile`` or ``Plan.load``; never by hand."""

    def __init__(self, doc, work, pages=(), masks=None):
        self.doc, self.work = doc, work
        self.fps = float(doc.get("fps") or 30)
        self.W, self.H = [int(x) for x in doc["frame"]]
        self.dur = int(doc["dur_f"])
        self.seed = int(doc.get("seed") or 0)
        self.film = doc.get("film") or {}
        self.voices = doc.get("voices") or {}
        self.items = doc["items"]
        self.assets = doc["assets"]
        self.sprites = doc.get("sprites") or {}
        self.mask_meta = doc.get("masks") or {}
        self.pages, self.masks = list(pages), masks
        self.segments = doc.get("segments") or []
        self.warn = list(doc.get("warnings") or [])
        self.sha = doc.get("sha")
        for a in self.assets.values():
            a["abs"] = a["path"] if os.path.isabs(a["path"]) else os.path.join(work, a["path"])
        self.starts = collections.defaultdict(list)
        for it in self.items:
            self.starts[it["f"][0]].append(it)
        # a word that is a window onto a picture: the picture runs on the word's own clock
        self.fills = [dict(it["fill"], f=it["f"], k="fill", id=it["id"]) for it in self.items
                      if it["k"] == "word" and it.get("fill") and it["fill"].get("pic") in self.assets]
        self.fill_of = {x["id"]: x for x in self.fills}
        self._seg_at = None

    # clocks ------------------------------------------------------

    def src_frame(self, it, f):
        """Which decoded frame of its asset this item shows on programme frame f."""
        a = self.assets[it["pic"]]
        lo, hi = it["f"]
        src = it.get("src") or [0, 0]
        step = max(int(it.get("step") or 1), 1)
        g = lo + (f - lo) // step * step
        t = src[0] + (src[1] - src[0]) * (g - lo) / max(hi - lo, 1)
        return max(int(round(t * a["rate"])), 0)

    def take_shot(self, shot):
        """Does this shot run on the take's clock and geometry, so the person's matte registers to it?"""
        pic = str((shot or {}).get("pic"))
        return "matte" in self.assets and bool((shot or {}).get("src")) and (pic == "main" or pic.startswith("own:cut"))

    def matte_frame(self, shot, f):
        m = self.assets["matte"]
        lo, hi = shot["f"]
        src = shot["src"]
        step = max(int(shot.get("step") or 1), 1)
        g = lo + (f - lo) // step * step
        t = src[0] + (src[1] - src[0]) * (g - lo) / max(hi - lo, 1)
        return max(int(round(t * m["rate"])), 0)

    def pictured(self):
        """Items that pull frames of a video, in start order."""
        need = [it for it in self.items if it["k"] in ("shot", "card", "join") and self.assets.get(it.get("pic"), {}).get("kind") == "video"]
        need += [x for x in self.fills if self.assets[x["pic"]].get("kind") == "video"]
        need.sort(key=lambda i: i["f"][0])
        return need

    def demand(self, frames):
        """{programme frame: [(asset, source frame), ...]} for the frames asked."""
        out = {}
        need = self.pictured()
        behind = [it for it in self.items if it.get("behind")]
        shots = sorted((it for it in self.items if it["k"] == "shot"), key=lambda i: i["f"][0])
        for f in sorted(frames):
            ks = []
            for it in need:
                if it["f"][0] > f:
                    break
                if f < it["f"][1]:
                    k = (it["pic"], self.src_frame(it, f))
                    if k not in ks:
                        ks.append(k)
            if behind and any(b["f"][0] <= f < b["f"][1] for b in behind):
                sh = next((q for q in shots if q["f"][0] <= f < q["f"][1]), None)
                if self.take_shot(sh):
                    ks.append(("matte", self.matte_frame(sh, f)))
            if ks:
                out[f] = ks
        return out

    def active(self, f):
        return [i for i in self.items if i["f"][0] <= f < i["f"][1]]

    # grain clock -------------------------------------------------

    def _noise_tables(self):
        """Per-frame offsets into the noise tile. With segments, each segment owns a table seeded by
        the id of the shot it starts on, so a ripple edit upstream leaves a later segment's grain,
        and so its hash, alone. Without, one table for the film, as the slice drew it."""
        if self._seg_at is not None:
            return
        self._seg_at = np.zeros(self.dur + 2, np.int32)
        self._ofs = []
        if not self.segments:
            rng = np.random.RandomState(self.seed ^ 0x5EED)
            rng.standard_normal((1024, 1024))        # the noise tile is drawn from this stream first: keep the slice's clock
            self._ofs.append(rng.randint(0, 1024, size=(self.dur + 2, 2)))
            self._start = [0]
            return
        self._start = []
        for i, s in enumerate(self.segments):
            a, b = s["f"]
            self._seg_at[a:b] = i
            rng = np.random.RandomState((self.seed ^ 0x5EED ^ zlib.crc32(s["key"].encode())) & 0x7FFFFFFF)
            self._ofs.append(rng.randint(0, 1024, size=(b - a + 2, 2)))
            self._start.append(a)
        self._seg_at[self.dur:] = len(self.segments) - 1

    def noise_at(self, f, k=0):
        """(x, y) offset into the noise tile for frame f. k=1 is the second, independent draw the
        dither takes."""
        self._noise_tables()
        i = int(self._seg_at[min(max(f, 0), self.dur + 1)])
        t = self._ofs[i]
        j = min(max(f - self._start[i] + k, 0), len(t) - 1)
        return (int(t[j][0]), int(t[j][1])) if k == 0 else (int(t[j][1]), int(t[j][0]))

    # disk --------------------------------------------------------

    def save(self, out_dir):
        """plan.json, curves.bin, atlas.png (and masks.png). Same plan, same bytes."""
        from PIL import Image
        os.makedirs(out_dir, exist_ok=True)
        curves, at = [], 0

        def ref(x):
            nonlocal at
            a = np.asarray(x, np.float32)
            curves.append(a.ravel())
            r = [at, a.shape[0]] + ([a.shape[1]] if a.ndim == 2 else [])
            at += a.size
            return {"$c": r}

        items = []
        for it in self.items:
            o = dict(it)
            for k in ANIM.get(it["k"], ()):
                if isinstance(o.get(k), list):
                    o[k] = ref(o[k])
            for k in ANIM_RECT.get(it["k"], ()):
                if isinstance(o.get(k), list) and o[k] and isinstance(o[k][0], list):
                    o[k] = ref(o[k])
            if isinstance((o.get("fill") or {}).get("crop"), list) and o["fill"]["crop"] and isinstance(o["fill"]["crop"][0], list):
                o["fill"] = dict(o["fill"], crop=ref(o["fill"]["crop"]))
            items.append(o)
        doc = dict(self.doc, items=items, assets={k: {x: y for x, y in a.items() if x != "abs"} for k, a in self.assets.items()},
                   warnings=self.warn, pages=len(self.pages))
        blob = np.concatenate(curves).astype("<f4").tobytes() if curves else b""
        with open(os.path.join(out_dir, "curves.bin"), "wb") as f:
            f.write(blob)
        for i, pg in enumerate(self.pages):
            Image.fromarray(pg, "RGBA").save(os.path.join(out_dir, "atlas.png" if i == 0 else f"atlas.{i}.png"), compress_level=3)
        if self.masks is not None:
            import cv2
            cv2.imwrite(os.path.join(out_dir, "masks.png"), self.masks)
        with open(os.path.join(out_dir, "plan.json"), "w", encoding="utf-8") as f:
            f.write(canon(doc))
        return out_dir

    @classmethod
    def load(cls, plan_dir, work=None):
        from PIL import Image
        doc = json.load(open(os.path.join(plan_dir, "plan.json"), encoding="utf-8"))
        cur = np.fromfile(os.path.join(plan_dir, "curves.bin"), "<f4")

        def un(x):
            if isinstance(x, dict) and "$c" in x:
                r = x["$c"]
                a = cur[r[0]:r[0] + r[1] * (r[2] if len(r) > 2 else 1)]
                return (a.reshape(r[1], r[2]) if len(r) > 2 else a).astype(np.float64).tolist()
            return x

        for it in doc["items"]:
            for k in ANIM.get(it["k"], ()) + ANIM_RECT.get(it["k"], ()):
                if k in it:
                    it[k] = un(it[k])
            if isinstance(it.get("fill"), dict) and "crop" in it["fill"]:
                it["fill"]["crop"] = un(it["fill"]["crop"])
        pages = []
        for i in range(int(doc.get("pages") or 0)):
            p = os.path.join(plan_dir, "atlas.png" if i == 0 else f"atlas.{i}.png")
            pages.append(np.asarray(Image.open(p).convert("RGBA"), np.uint8).copy())
        masks = None
        mp = os.path.join(plan_dir, "masks.png")
        if os.path.exists(mp):
            import cv2
            masks = cv2.imread(mp, cv2.IMREAD_UNCHANGED)
        return cls(doc, work if work is not None else doc.get("work") or plan_dir, pages, masks)
