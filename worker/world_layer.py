"""The world compositor: motion graphics attached to the footage (Wave 3).

ROADMAP Wave 3 (3A anchors, 3B occlusion, 3C planes); contract in
devices/INTERFACES_W3.md §4.

``MotionItem.anchor`` says where a graphic lives in the world: on a face
part, round the head, on a fingertip, between two hands, on a tracked
point, on an object, on a plane — and what passes in front of it. The
graphic is rendered by the motion engine exactly as before (flat, its own
design); THIS module then turns its clip into the program-space clip the
renderer overlays, before the filter graph is built:

  per program frame
    program second -> source second (the Timeline)
    the anchor's pose on the source clock (anchors/query.py over READY
      sidecars; One-Euro smoothed in source space, so a reframe never
      changes the motion)
    source fractions -> program pixels (keepout.Geometry: crop, follow,
      picture rect, zoom, cards — the render's own geometry)
    the clip warped onto it (affine: position, scale, rotation; or the
      plane / finger-frame homography), premultiplied so edges never fringe
    occluders cut out of its alpha (the person matte, object masks, the
      hands), in program space
    shadow transfer on planes (the surface's real shadow darkens the type)
  -> one RGBA clip (qtrle) at the union box of everything it covered.

The normal overlay then draws it (1C's ``composite`` — blend, opacity,
fill — still applies). An item without an anchor is never touched: byte
for byte the clip it always was.

Degrade, never fail (A3; ROADMAP §9): anchor not measured -> the graphic
where it was authored, with "pinned static: tracking not measured yet";
tracking lost for a while -> ``lost`` (hold the last pose / fade out /
authored place); occluder not measured -> drawn in front, said so; any
exception -> the original clip. A canvas program (no footage) keeps every
graphic where it was authored.
"""

import math
import os
import subprocess
import tempfile
import time

import numpy as np

LIVE = True
# Plane decals fill this share of the place rect (a margin inside it).
PLANE_FILL = 0.92
# A plane decal never covers more than this share of the frame (a quad
# misread as the whole floor would paint the frame).
PLANE_MAX_AREA = 0.7
# Fade (frames) when tracking is lost with lost="hide".
HIDE_FADE_F = 4
# Shadow transfer: blur sigma as a share of the frame height, and limits.
SHADOW_SIGMA = 0.012
SHADOW_LO, SHADOW_HI = 0.3, 1.05
# Person-matte firming (matte_track.FIRM_LO / FIRM_HI: a doubted limb is
# body, not translucency).
FIRM_LO, FIRM_HI = 0.15, 0.55
_MISSING = "pinned static: tracking not measured yet"


class Ctx:
    """What the renderer lends the world compositor (motion_layer passes it
    through ``prepare_inputs(world=...)``)."""

    def __init__(self, edl, W, H, fps, *, tl=None, source=None, index=None,
                 preview=True, plan=None, clock_offset=0.0, workdir=None,
                 src_path=None, out_duration=None):
        self.edl = edl
        self.W, self.H, self.fps = int(W), int(H), float(fps or 30.0)
        self.tl = tl
        self.source = source
        self.index = index
        self.preview = bool(preview)
        self.plan = plan
        self.clock_offset = float(clock_offset or 0.0)
        self.workdir = workdir
        self.src_path = src_path
        self.out_duration = float(out_duration or getattr(tl, "out_duration",
                                                          0.0) or 0.0)
        self._geo = {}
        self._masks = {}
        self._ink = {}             # item id -> dark ink (printed type)
        self._blend = {}           # item id -> blend override

    # source frame size (fractions <-> pixels of the measured proxy)
    @property
    def src_size(self):
        v = (self.index or {}).get("video") or {}
        w = float(getattr(self.source, "width", 0) or v.get("width") or 0)
        h = float(getattr(self.source, "height", 0) or v.get("height") or 0)
        return (w or 1920.0, h or 1080.0)

    def geometry(self, lock="world"):
        g = self._geo.get(lock)
        if g is None:
            import keepout
            w, h = self.src_size
            g = keepout.Geometry(self.edl, {"width": w, "height": h},
                                 self.W, self.H, self.out_duration,
                                 zooms=[] if lock == "screen" else None)
            self._geo[lock] = g
        return g


def anchored(item):
    return isinstance(item, dict) and isinstance(item.get("anchor"), dict)


# Wave 3 templates whose pages read engine data (augment) or declare
# video slots (slots.fill): 3D elements and 3E layouts.
W3_TEMPLATES = frozenset((
    "el_particles", "el_lightning", "el_doodle", "el_pictogram", "el_shapes",
    "el_sprites", "el_orbit", "mosaic", "scroll_wall", "flatlay", "stack",
    "device_stage", "glass_card", "hud", "breakdown", "spec_sheet"))


def wants(edl):
    """Does this EDL anchor any graphic?"""
    return any(anchored(m) for m in (edl or {}).get("motion") or [])


def needed(edl):
    """Does this render need the world context at all (an anchored graphic
    or a Wave 3 template)? False: the renderer passes none and nothing
    here runs."""
    for m in (edl or {}).get("motion") or []:
        if not isinstance(m, dict):
            continue
        if anchored(m) or m.get("template") in W3_TEMPLATES:
            return True
    return False


# ── program geometry ────────────────────────────────────────────────────

def program_affine(geo, prog_t, trim_t):
    """2x3: SOURCE fractions -> OUTPUT fractions at this moment (crop,
    follow, picture rect, zoom, cards are all axis-aligned scale + offset),
    or None where the footage is not on screen."""
    try:
        outs = geo.to_outputs(prog_t, trim_t, (0.0, 0.0, 1.0, 1.0))
    except Exception:  # noqa: BLE001
        return None
    if not outs:
        return None
    b = outs[0]
    sx, sy = b[2] - b[0], b[3] - b[1]
    if sx <= 0 or sy <= 0:
        return None
    return np.array([[sx, 0.0, b[0]], [0.0, sy, b[1]]], np.float64)


def program_box(ctx, box, t):
    """A SOURCE-fraction box [x0, y0, x1, y1] as PROGRAM fractions at
    program second ``t`` (clipped to the frame), or None where the
    footage is not on screen."""
    if ctx is None or ctx.tl is None:
        return None
    tr = ctx.tl.out_to_src(float(t))
    if tr is None:
        return None
    A = program_affine(ctx.geometry("world"), float(t), tr)
    if A is None:
        return None
    x0, y0 = _apply(A, (float(box[0]), float(box[1])))
    x1, y1 = _apply(A, (float(box[2]), float(box[3])))
    x0, x1 = max(0.0, min(x0, x1)), min(1.0, max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(1.0, max(y0, y1))
    if x1 - x0 <= 1e-3 or y1 - y0 <= 1e-3:
        return None
    return [x0, y0, x1, y1]


def _apply(A, xy):
    return np.array([A[0, 0] * xy[0] + A[0, 2], A[1, 1] * xy[1] + A[1, 2]])


# ── anchor targets ──────────────────────────────────────────────────────

def _target(anchor):
    to = anchor["to"]
    if ":" in to:
        kind, ref = to.split(":", 1)
        return kind, ref
    if "." in to:
        kind, part = to.split(".", 1)
        return kind, part
    return to, None


def series_for(anchor, ctx, lo, hi):
    """(Series | None, reason) for the anchor over sidecar seconds
    [lo, hi]."""
    from anchors import query
    import world_defs
    kind, ref = _target(anchor)
    src = ctx.source
    which = anchor.get("which", "any")
    rng = [(max(0.0, lo - 0.5), hi + 0.5)]
    if kind == "screen":
        return None, "screen anchor"
    if src is None:
        return None, "no source footage on this render"
    if kind in ("face", "head"):
        samples, share, meta = query.load("landmarks.face", src, rng)
        why = None
        if not samples:
            # the ladder's next rung (ROADMAP 3A): the face detector's box
            # and five points, prefetched for every upload at index time
            samples, meta = _yunet_faces(src, rng)
            if not samples:
                return None, "face landmarks not measured yet"
            why = YUNET_NOTE
        part = "head" if kind == "head" else (ref or "face")
        series = query.face_series(samples, part, which, meta)
        if kind == "head":
            series = _refine_head(series, ctx)
        return series, why
    if kind == "hand":
        samples, share, meta = query.load("landmarks.hand", src, rng)
        if not samples:
            return None, "hand landmarks not measured yet"
        return query.hand_series(samples, ref, which), None
    if kind == "pose":
        samples, share, meta = query.load("landmarks.pose", src, rng)
        if not samples:
            return None, "pose not measured yet"
        joint = ref
        if which in ("left", "right") or joint.endswith(("_l", "_r")):
            pass
        return query.pose_series(samples, joint, which), None
    defs = world_defs.definitions(ctx.edl)
    d = defs.get(ref)
    if d is None:
        return None, f"{kind} {ref} is not in this edit"
    if kind == "track":
        req = world_defs.request(d, ctx.tl, ctx.clock_offset)
        if req is None:
            return None, "track has no source window here"
        k, ranges, params = req
        import sidecars
        rows = []
        for _c, m in sidecars.ready_chunks(k, src.sha256, src.shots,
                                           src.duration_s, ranges, params):
            rows.append(sidecars.read_json_chunk(k, src.sha256, m) or {})
        if not rows:
            return None, "point track not measured yet"
        return query.track_series(rows, d["id"]), None
    if kind == "plane":
        st = world_defs.status(d, ctx.tl, src, ctx.clock_offset)
        quad = d["quad"]
        if st == "ready":
            k, ranges, params = world_defs.request(d, ctx.tl,
                                                   ctx.clock_offset)
            samples, _sh, meta = query.load(k, src, ranges, params)
            if samples:
                # the batch lane snapped the authored corners to the
                # surface's edges on the key frame (planes.refine_quad)
                return query.plane_series(
                    samples, meta.get("quad_refined") or quad), None
        # static (written so, or not measured yet): the seed quad holds —
        # exact on a locked-off camera, the honest fallback otherwise
        why = None if st == "static" else (
            "plane tracking not measured yet: pinned to its first frame")
        return query.static_plane(quad, lo - 0.5, hi + 0.5), why
    if kind == "object":
        return _object_series(d, ctx, lo, hi)
    return None, f"unknown anchor {anchor['to']}"


def _refine_head(series, ctx):
    """The head as the person matte draws it: the face mesh knows the face,
    not the skull (in a three-quarter view its ellipse sits forward of the
    head). Where the matte is measured, the head's centre x is the middle
    of the matte's run through the face on the rows between the crown and
    the eyes, and its height runs from the crown to the chin."""
    reader = _mask_reader(ctx, "person")
    if reader is None:
        return series
    xy, size = series.xy.copy(), series.size.copy()
    for k, t in enumerate(series.t):
        if not np.all(np.isfinite(xy[k])) or not np.isfinite(size[k]):
            continue
        m = reader.at(float(t))
        if m is None:
            continue
        mh, mw = m.shape
        cx, cy, hh = xy[k][0] * mw, xy[k][1] * mh, size[k] * mh
        col = int(min(mw - 1, max(0, round(cx))))
        top_lim = int(max(0, cy - hh * 0.75))
        colv = m[top_lim:int(min(mh, cy)), col] > 127
        if not colv.any():
            continue
        top = top_lim + int(np.argmax(colv))
        chin = cy + hh * 0.5
        rows = range(top + int(0.15 * (chin - top)),
                     int(top + 0.45 * (chin - top)))
        mids = []
        for y in rows:
            line = m[int(y)] > 127
            if not line[col]:
                continue
            a = col
            while a > 0 and line[a - 1]:
                a -= 1
            b = col
            while b < mw - 1 and line[b + 1]:
                b += 1
            if b - a < hh * 1.6:          # a head, not the shoulders
                mids.append((a + b) / 2.0)
        if len(mids) >= 3:
            xy[k][0] = float(np.median(mids)) / mw
        xy[k][1] = ((top + chin) / 2.0) / mh
        size[k] = (chin - top) / mh
    return query_series(series, xy, size)


YUNET_NOTE = ("face from the detector's box and five points (the face mesh "
              "is still measuring): position and size, no mesh shape")


def _yunet_faces(src, rng):
    """(samples, meta) in the landmarks.face sample shape, built from the
    ``faces.yunet`` sidecar (box + eyes, nose, mouth corners), or ([], {}):
    the degrade rung between the face mesh and a static placement. Parts
    the detector does not see (ears, the mesh oval) read as not seen."""
    from anchors import query
    rows, _share, _meta = query.load("faces.yunet", src, rng)
    if not rows:
        return [], {}
    names = query.FACE_NAMES
    nan = float("nan")
    out = []
    for s in rows:
        faces = []
        for r in s.get("faces") or []:
            if len(r) < 15:
                continue
            x0, y0, x1, y1 = (float(v) for v in r[:4])
            w, h = x1 - x0, y1 - y0
            if w <= 0 or h <= 0:
                continue
            e1, e2 = (float(r[5]), float(r[6])), (float(r[7]), float(r[8]))
            nose = (float(r[9]), float(r[10]))
            m1, m2 = (float(r[11]), float(r[12])), (float(r[13]), float(r[14]))
            mouth = ((m1[0] + m2[0]) / 2.0, (m1[1] + m2[1]) / 2.0)
            emid = ((e1[0] + e2[0]) / 2.0, (e1[1] + e2[1]) / 2.0)
            lift = 0.12 * h
            pts = {"iris_r": e1, "iris_l": e2, "eye_r": e1, "eye_l": e2,
                   "brow_r": (e1[0], e1[1] - lift),
                   "brow_l": (e2[0], e2[1] - lift),
                   "nose": nose, "nose_bridge": emid, "mouth": mouth,
                   "mouth_r": m1, "mouth_l": m2,
                   "chin": (mouth[0], y1),
                   "forehead": (emid[0], max(0.0, y0 - 0.05 * h))}
            flat = []
            for n in names:
                p = pts.get(n, (nan, nan))
                flat += [float(p[0]), float(p[1])]
            roll = math.degrees(math.atan2(e2[1] - e1[1], e2[0] - e1[0]))
            if e2[0] < e1[0]:
                roll = math.degrees(math.atan2(e1[1] - e2[1],
                                               e1[0] - e2[0]))
            # the head as faces.head_ellipse grows the face oval (the box
            # stands in for the oval): 12% wider, 30% taller, centre raised
            ry = 0.55 * h * 1.30
            rx = 0.5 * w * 1.12
            cy = y0 + 0.45 * h - 0.14 * 2.0 * 0.55 * h
            faces.append({"b": [x0, y0, x1, y1], "p": flat,
                          "hp": [0.0, 0.0, roll],
                          "he": [(x0 + x1) / 2.0, cy, rx, ry, roll]})
        out.append({"t": float(s["t"]), "faces": faces})
    return out, {"names": list(names)}


def query_series(series, xy, size):
    from anchors import query
    return query.Series(series.t, xy, size, series.angle, series.quad,
                        None, series.kind)


def _object_series(d, ctx, lo, hi):
    """An object as a target: its mask's centroid, size = its box height."""
    from anchors import query
    reader = _mask_reader(ctx, "object", d)
    if reader is None:
        return None, "object mask not measured yet"
    ts = np.arange(lo, hi + 1e-6, 1.0 / 15.0)
    xy, size = [], []
    for t in ts:
        m = reader.at(t)
        if m is None or m.max() < 128:
            xy.append([np.nan, np.nan])
            size.append(np.nan)
            continue
        ys, xs = np.where(m > 127)
        h, w = m.shape
        xy.append([xs.mean() / w, ys.mean() / h])
        size.append((ys.max() - ys.min() + 1) / float(h))
    return query.Series(ts, xy, size, kind="object"), None


# ── mask streams (person matte, object masks) on the sidecar clock ──────

class MaskReader:
    """Gray mask frames of a mask-video sidecar kind (matte.person,
    objects.sam2) at sidecar seconds, decoded per chunk on demand and
    cached (the item's few seconds, never a whole source)."""

    def __init__(self, files, firm=False):
        self.files = sorted(files, key=lambda f: f["t0"])
        self.firm = firm
        self._cache = {}

    def _chunk(self, f):
        key = f["path"]
        hit = self._cache.get(key)
        if hit is None:
            m = f.get("meta") or {}
            w, h = int(m.get("width") or 0), int(m.get("height") or 0)
            fps = float(m.get("fps") or 30.0)
            n = int(m.get("frames") or 0)
            if not w or not h or not n:
                hit = (fps, [])
            else:
                hit = (fps, _decode_gray(f["path"], w, h, n))
            if len(self._cache) > 6:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = hit
        return hit

    def at(self, t):
        for f in self.files:
            if f["t0"] - 1e-3 <= t < f["t1"] + 1e-3:
                fps, frames = self._chunk(f)
                if not frames:
                    return None
                k = int(round((t - f["t0"]) * fps))
                k = min(len(frames) - 1, max(0, k))
                m = frames[k]
                if self.firm:
                    m = _firm(m)
                return m
        return None


def _firm(m):
    x = np.clip((m.astype(np.float32) / 255.0 - FIRM_LO)
                / (FIRM_HI - FIRM_LO), 0.0, 1.0)
    return (255.0 * x * x * (3.0 - 2.0 * x)).astype(np.uint8)


def _decode_gray(path, w, h, n):
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-frames:v",
         str(n), "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
        capture_output=True, check=False)
    raw = proc.stdout
    size = w * h
    k = len(raw) // size
    return [np.frombuffer(raw[i * size:(i + 1) * size], np.uint8)
            .reshape(h, w) for i in range(k)]


def _mask_reader(ctx, what, d=None, lo=None, hi=None, windows=None):
    """A MaskReader for the person matte or one object definition, over the
    READY chunks (fetched into the workdir), or None. The person matte is
    read over every anchored item's window, plus ``windows`` (program
    seconds) when a caller names its own."""
    key = (what, (d or {}).get("id"), tuple(windows or ()))
    if key in ctx._masks:
        return ctx._masks[key]
    reader = None
    try:
        import sidecars
        src = ctx.source
        if src is None:
            raise LookupError("no source")
        dest = os.path.join(ctx.workdir or tempfile.gettempdir(), "w3masks")
        if what == "person":
            import matte_track
            params = matte_track.sidecar_params()
            spans = []
            for m in (ctx.edl or {}).get("motion") or []:
                if anchored(m):
                    s, e = float(m["start"]), float(m["end"])
                    spans += matte_track.program_to_source(ctx.tl, s, e)
            for s, e in windows or ():
                spans += matte_track.program_to_source(ctx.tl, float(s),
                                                       float(e))
            ranges = [(max(0.0, a - ctx.clock_offset - 0.5),
                       b - ctx.clock_offset + 0.5) for a, b in spans]
            if not ranges:
                raise LookupError("no window")
            h = matte_track.read_handle(
                "matte.person", src,
                sidecars.chunks_for(src.shots, src.duration_s, ranges),
                params)
            files = sidecars.fetch(h, dest)
            reader = MaskReader(files, firm=True) if files else None
        else:
            import world_defs
            req = world_defs.request(d, ctx.tl, ctx.clock_offset)
            if req is None:
                raise LookupError("no request")
            kind, ranges, params = req
            import matte_track
            h = matte_track.read_handle(
                kind, src, sidecars.chunks_for(src.shots, src.duration_s,
                                               ranges), params)
            files = sidecars.fetch(h, dest)
            reader = MaskReader(files) if files else None
    except Exception as e:  # noqa: BLE001 — a mask question never fails
        if not isinstance(e, LookupError):
            print(f"[world] {what} mask unreadable ({str(e)[:140]})",
                  flush=True)
        reader = None
    ctx._masks[key] = reader
    return reader


# ── clip i/o ────────────────────────────────────────────────────────────

def _decode_rgba(path, w, h):
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-f", "rawvideo",
         "-pix_fmt", "rgba", "pipe:1"], stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL)
    size = w * h * 4
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 4)
    finally:
        proc.stdout.close()
        proc.wait()


class _Encoder:
    def __init__(self, path, w, h, fps):
        self.path = path
        self.proc = subprocess.Popen(
            ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt",
             "rgba", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "pipe:0",
             "-c:v", "qtrle", "-pix_fmt", "argb", "-r", f"{fps:.6f}", path],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)

    def write(self, rgba):
        self.proc.stdin.write(np.ascontiguousarray(rgba, np.uint8).tobytes())

    def close(self):
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise RuntimeError("world clip encoder failed")


# ── the composite ───────────────────────────────────────────────────────

class _Plan:
    """Per-frame transforms of one anchored item."""

    def __init__(self, n):
        self.n = n
        self.M = [None] * n          # 3x3 clip px -> output px, or None
        self.alpha = np.ones(n)      # lost="hide" fades
        self.A = [None] * n          # source fractions -> output fractions
        self.trim = [None] * n       # trim seconds
        self.quads = [None] * n      # output px quad (planes / frames)


def object_depth(ctx, d):
    """(``front`` | ``behind``, how) for an object definition: its authored
    ``depth``, or (auto) the order measured from its mask and the person
    matte at three moments of its window (objseg.depthorder). ``front``
    when it cannot be seen (no overlap, nothing measured)."""
    dep = (d or {}).get("depth") or "auto"
    if dep in ("front", "behind"):
        return dep, "authored"
    key = ("depth", (d or {}).get("id"))
    if key in ctx._masks:
        return ctx._masks[key]
    res = ("front", "not measurable")
    try:
        import cv2
        from objseg import depthorder
        a, b = float(d["start"]), float(d["end"])
        orx = _mask_reader(ctx, "object", d)
        pr = _mask_reader(ctx, "person", windows=[(a, b)]) if orx else None
        pairs = []
        for f in (0.2, 0.5, 0.8):
            tr = ctx.tl.out_to_src(a + f * (b - a)) if pr else None
            if tr is None:
                continue
            om = orx.at(tr - ctx.clock_offset)
            pm = pr.at(tr - ctx.clock_offset)
            if om is None or pm is None:
                continue
            if pm.shape != om.shape:
                pm = cv2.resize(pm, (om.shape[1], om.shape[0]),
                                interpolation=cv2.INTER_LINEAR)
            pairs.append((om, pm))
        o, _det = depthorder.vote(pairs)
        if o:
            res = (o, "measured")
    except Exception as e:  # noqa: BLE001 — an order question never fails
        print(f"[world] depth of {(d or {}).get('id')} not measured "
              f"({str(e)[:120]})", flush=True)
    ctx._masks[key] = res
    return res


def _default_occluders(anchor, item, defs):
    occ = anchor.get("occluders")
    if occ is not None:
        out = list(occ)
    else:
        out = []
        kind, ref = _target(anchor)
        if kind == "plane":
            # a surface of the room: the person stands on it (floor), sits
            # at it (desk, table: hands and arms cross it) or in front of
            # it (wall, screen, projection) — type there never covers them
            out.append("person")
    if item.get("layer") == "behind_subject" and "person" not in out:
        out.append("person")
    return out


def compose(item, clip, ctx, plan_only=False):
    """(new clip | None, notes). None: keep the original clip (static).
    ``plan_only``: (the per-frame plan | None, notes) without rendering
    (acceptance measurements read the exact transforms)."""
    import world_defs
    import motion_engine
    anchor = item["anchor"]
    notes = []
    n = int(getattr(clip, "frames", 0) or 0)
    if n <= 0:
        n = max(1, int(round((float(item["end"]) - float(item["start"]))
                             * ctx.fps)))
    tl = ctx.tl
    if tl is None or ctx.source is None:
        return None, [_MISSING + " (no source footage on this render)"]
    s = float(item["start"])
    plan = _Plan(n)
    for k in range(n):
        t = s + (k + 0.5) / ctx.fps
        tr = tl.out_to_src(min(t, ctx.out_duration - 1e-3))
        plan.trim[k] = tr
    valid = [v for v in plan.trim if v is not None]
    if not valid:
        return None, [_MISSING + " (its window shows no main footage)"]
    off = ctx.clock_offset
    lo, hi = min(valid) - off, max(valid) - off
    kind, ref = _target(anchor)
    if kind == "screen":
        # no tracking: the graphic stays where it was authored, and what
        # passes in front of it (occluders) still does
        geo = ctx.geometry(anchor.get("lock") or "world")
        ident = np.array([[1.0, 0.0, clip.x], [0.0, 1.0, clip.y],
                          [0.0, 0.0, 1.0]])
        for k in range(n):
            tr = plan.trim[k]
            plan.A[k] = (program_affine(geo, s + (k + 0.5) / ctx.fps, tr)
                         if tr is not None else None)
        objs = [str(o).split(":", 1)[1] for o in anchor.get("occluders")
                or [] if str(o).startswith("object:")]
        if anchor.get("pin") is not None and objs:
            # seated: the graphic's own ink point ``pin`` set on the
            # object that hides it (a wide object's top edge, a tall one's
            # middle), measured on the landing frame — the word sits where
            # the object really cuts it, whatever size the page set it at
            d = world_defs.definitions(ctx.edl).get(objs[0])
            off_xy = _seat(item, clip, ctx, anchor, plan, d, notes) \
                if d else None
            if off_xy is not None:
                ident = np.array([[1.0, 0.0, clip.x + off_xy[0]],
                                  [0.0, 1.0, clip.y + off_xy[1]],
                                  [0.0, 0.0, 1.0]])
        for k in range(n):
            plan.M[k] = ident
        if plan_only:
            return plan, notes
        return _render(item, clip, ctx, plan, n, notes, kind, ref, lo, hi)
    series, why = series_for(anchor, ctx, lo, hi)
    defs = world_defs.definitions(ctx.edl)
    if series is None:
        return None, [f"pinned static: {why}"]
    if why:
        notes.append(why)
    sw, sh = ctx.src_size
    series = series.smoothed(float(anchor.get("smooth", 0.5)),
                             aspect=sw / sh)
    lock = anchor.get("lock") or "world"
    geo = ctx.geometry(lock)
    W, H = ctx.W, ctx.H
    cw, ch = float(clip.w), float(clip.h)
    pin = anchor.get("pin") or [0.5, 0.5]
    follow = set(anchor.get("follow") or ["position"])
    offset = anchor.get("offset") or [0.0, 0.0]
    ts = np.array([np.nan if v is None else v - off for v in plan.trim])
    good_t = np.where(np.isfinite(ts), ts, lo)
    xy, size, ang, quad, ok = series.at(good_t)
    ok = ok & np.isfinite(ts)
    if not ok.any():
        return None, [f"pinned static: {kind} not seen in this window"]
    if ok.mean() < 0.999:
        notes.append(f"tracking lost on {int((~ok).sum())} of {n} frames "
                     f"({anchor.get('lost', 'hold')})")
    first = int(np.argmax(ok))
    ref_size = size[first] if np.isfinite(size[first]) else None
    ref_ang = ang[first] if np.isfinite(ang[first]) else 0.0
    plane_like = kind == "plane" or (kind == "hand" and ref == "frame")
    fit = _anchor_fit(item)
    if plane_like and quad is None:
        return None, ["pinned static: no quad for this anchor"]
    lost = anchor.get("lost") or "hold"
    if kind == "plane" and not anchor.get("place"):
        # default place: the largest part of the plane on screen when the
        # graphic lands (a slide half outside a 9:16 crop keeps its type
        # inside the frame)
        occ0 = anchor.get("occluders")
        if occ0 is None:
            occ0 = _default_occluders(anchor, item, defs)
        person = (_mask_reader(ctx, "person") if "person" in occ0
                  else None)
        anchor = dict(anchor, place=_visible_place(
            geo, s, plan.trim, first, quad, W, H, person=person, off=off))
        item = dict(item, anchor=anchor)
    last_good = None
    for k in range(n):
        tr = plan.trim[k]
        A = program_affine(geo, s + (k + 0.5) / ctx.fps, tr) \
            if tr is not None else None
        plan.A[k] = A
        if A is None:
            plan.M[k] = None
            continue
        if ok[k]:
            last_good = k
            idx = k
        elif lost == "hold":
            # nearest good frame (back, then forward)
            idx = last_good if last_good is not None else first
        elif lost == "hide":
            idx = last_good if last_good is not None else first
            plan.alpha[k] = 0.0
        else:   # static: the authored placement
            plan.M[k] = np.array([[1, 0, clip.x], [0, 1, clip.y],
                                  [0, 0, 1]], np.float64)
            continue
        Akx = np.array([[A[0, 0] * W, 0, A[0, 2] * W],
                        [0, A[1, 1] * H, A[1, 2] * H]])
        if plane_like:
            q = quad[idx]
            qo = np.array([[Akx[0, 0] * x + Akx[0, 2],
                            Akx[1, 1] * y + Akx[1, 2]] for x, y in q])
            M = _plane_transform(anchor, defs.get(ref) if kind == "plane"
                                 else None, qo, cw, ch, sw, sh, W, H, A)
            plan.quads[k] = qo
            plan.M[k] = M
            continue
        p = xy[idx]
        P = np.array([Akx[0, 0] * p[0] + Akx[0, 2],
                      Akx[1, 1] * p[1] + Akx[1, 2]])
        scl = 1.0
        if "scale" in follow and fit and np.isfinite(size[idx]):
            # the template asks its box to be ``fit`` x the anchor's size
            # (el_orbit: a ring just outside the head)
            scl = float(size[idx]) * A[1, 1] * H * fit / max(1.0, ch)
        elif "scale" in follow and ref_size and np.isfinite(size[idx]):
            scl = float(size[idx]) / float(ref_size)
        rot = 0.0
        if "rotation" in follow and np.isfinite(ang[idx]):
            rot = float(ang[idx] - ref_ang)
        if "scale" in follow and np.isfinite(size[idx]):
            unit = float(size[idx]) * A[1, 1] * H
            d = np.array([offset[0] * unit, offset[1] * unit])
        else:
            d = np.array([offset[0] * W, offset[1] * H])
        c, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        R = np.array([[c, -sn], [sn, c]]) * scl
        pin_px = np.array([pin[0] * cw, pin[1] * ch])
        t2 = P + d - R @ pin_px
        plan.M[k] = np.array([[R[0, 0], R[0, 1], t2[0]],
                              [R[1, 0], R[1, 1], t2[1]], [0, 0, 1]])
    if "shape" in follow and kind in ("face", "head"):
        _fit_face_shape(plan, ctx, anchor, good_t, ok, first, geo, s, lo, hi,
                        notes)
    # hide fades
    if lost == "hide" and (plan.alpha < 1).any():
        a = plan.alpha.copy()
        for k in range(n):
            if a[k] == 0.0:
                continue
            dist = min([abs(k - j) for j in range(n) if plan.alpha[j] == 0]
                       or [HIDE_FADE_F])
            a[k] = min(1.0, dist / float(HIDE_FADE_F))
        plan.alpha = a
    if kind == "plane" and quad is not None:
        _print_polarity(item, clip, ctx, plan, quad, ok, good_t, notes)
    if plan_only:
        return plan, notes
    return _render(item, clip, ctx, plan, n, notes, kind, ref, lo, hi)


# Type multiplied into a surface ("printed") must be darker than it: a
# light page on a bright wall prints in dark ink; on a dark surface a
# multiply shows nothing, so it is laid as light (screen) instead.
PRINT_BRIGHT, PRINT_DARK = 0.5, 0.33
INK = (22, 22, 26)


def _print_polarity(item, clip, ctx, plan, quad, ok, good_t, notes):
    """For a plane graphic composited ``multiply``: measure the surface
    (the source frame inside the quad, on the landing frame) and the
    graphic's ink; set ``ctx._ink[id]`` (recolour to dark ink, the light
    page's own shadow dropped) or ``ctx._blend[id] = 'screen'``."""
    comp = item.get("composite") or {}
    if comp.get("blend") != "multiply" or ctx.src_path is None:
        return
    n = len(plan.M)
    k0 = int(min(n - 1, max(0, round(SEAT_AT_S * ctx.fps))))
    idx = k0 if ok[k0] else int(np.argmax(ok))
    q = quad[idx]
    if q is None or not np.all(np.isfinite(q)):
        return
    lum = _source_luma(ctx, float(good_t[idx]))
    ink = _ink_luma(clip, k0)
    if lum is None or ink is None:
        return
    import cv2
    h, w = lum.shape
    m = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(m, (np.asarray(q) * [w, h]).astype(np.int32), 255)
    if m.sum() == 0:
        return
    surf = float(lum[m > 0].mean())
    iid = item.get("id")
    if surf >= PRINT_BRIGHT and ink >= 0.6:
        ctx._ink[iid] = INK
        notes.append(f"printed in dark ink (the surface is bright, "
                     f"{surf:.2f})")
    elif surf < PRINT_DARK:
        ctx._blend[iid] = "screen"
        notes.append(f"laid as light (the surface is dark, {surf:.2f}: a "
                     "multiply would vanish)")


def _source_luma(ctx, t):
    """The source frame's luma (0..1, 320 px wide) at sidecar second t."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, t):.3f}",
         "-i", ctx.src_path, "-frames:v", "1", "-vf",
         "scale=320:-2:flags=area,format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=False)
    sw, sh = ctx.src_size
    w = 320
    h = int(round(320 * sh / max(1.0, sw) / 2.0)) * 2
    if len(proc.stdout) < w * h:
        return None
    return np.frombuffer(proc.stdout[:w * h], np.uint8).reshape(
        h, w).astype(np.float32) / 255.0


def _ink_luma(clip, k):
    """Mean luma (0..1) of the graphic's opaque ink on frame k, or None."""
    last = None
    for j, rgba in enumerate(_decode_rgba(clip.path, int(clip.w),
                                          int(clip.h))):
        last = rgba
        if j >= k:
            break
    if last is None:
        return None
    a = last[..., 3] > 200
    if a.sum() < 20:
        return None
    rgb = last[..., :3][a].astype(np.float32) / 255.0
    return float((0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1]
                  + 0.0722 * rgb[:, 2]).mean())


def _to_ink(rgba, ink):
    """A light page as dark ink: the glyphs keep their coverage (alpha x
    their own lightness, so a dark drop shadow falls away) in ``ink``."""
    out = np.empty_like(rgba)
    rgb = rgba[..., :3].astype(np.float32) / 255.0
    light = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    out[..., 0], out[..., 1], out[..., 2] = ink
    out[..., 3] = np.clip(rgba[..., 3].astype(np.float32) * light, 0,
                          255).astype(np.uint8)
    return out


SEAT_AT_S = 0.45          # the landing frame: after a rise / pop entrance


def _ink_box(clip, k):
    """(x0, y0, x1, y1) clip px of frame ``k``'s visible ink, or None."""
    last = None
    for j, rgba in enumerate(_decode_rgba(clip.path, int(clip.w),
                                          int(clip.h))):
        last = rgba
        if j >= k:
            break
    if last is None:
        return None
    ys, xs = np.where(last[..., 3] > 160)
    if xs.size < 20:
        return None
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1),
            float(ys.max() + 1))


def _last_line(clip, k, box):
    """(y0, y1) clip px of the LAST line of ink inside ``box`` (rows of
    ink separated from the line above by an empty band), or the box."""
    last = None
    for j, rgba in enumerate(_decode_rgba(clip.path, int(clip.w),
                                          int(clip.h))):
        last = rgba
        if j >= k:
            break
    y0, y1 = int(box[1]), int(box[3])
    if last is None or y1 - y0 < 4:
        return box[1], box[3]
    rows = (last[y0:y1, ..., 3] > 160).sum(axis=1)
    on = rows > max(1, 0.01 * (box[2] - box[0]))
    # walk up from the bottom: the last run of inked rows
    i = len(on) - 1
    while i >= 0 and not on[i]:
        i -= 1
    bot = i
    gap = max(2, int(0.04 * (y1 - y0)))
    while i >= 0:
        if not on[i]:
            j = i
            while j >= 0 and not on[j]:
                j -= 1
            if i - j >= gap:
                break
            i = j
            continue
        i -= 1
    top = i + 1
    return float(y0 + top), float(y0 + bot + 1)


def _seat(item, clip, ctx, anchor, plan, d, notes):
    """(dx, dy) program px that set the graphic's ink point ``pin`` on the
    object ``d`` that hides it, or None (not measured / nothing to do).
    A wide object (laptop, desk edge, car) takes the point on its top edge
    over its own centre; a tall one (mic, bottle, door jamb) on its middle.
    Kept inside the frame and off the faces the detector sees there."""
    n = len(plan.M)
    k0 = int(min(n - 1, max(0, round(SEAT_AT_S * ctx.fps))))
    tr, A = plan.trim[k0], plan.A[k0]
    if tr is None or A is None:
        return None
    reader = _mask_reader(ctx, "object", d)
    m = reader.at(tr - ctx.clock_offset) if reader is not None else None
    if m is None or m.max() < 128:
        notes.append(f"not seated on {d.get('id')}: its mask is not "
                     "measured yet (kept where authored)")
        return None
    ink = _ink_box(clip, k0)
    if ink is None:
        return None
    W, H = ctx.W, ctx.H
    mh, mw = m.shape
    ys, xs = np.where(m > 127)
    px = (A[0, 0] * (xs + 0.5) / mw + A[0, 2]) * W
    py = (A[1, 1] * (ys + 0.5) / mh + A[1, 2]) * H
    keep = (px >= 0) & (px < W) & (py >= 0) & (py < H)
    if keep.sum() < 20:
        return None
    px, py = px[keep], py[keep]
    bx0, bx1 = np.percentile(px, 2), np.percentile(px, 98)
    by0, by1 = np.percentile(py, 2), np.percentile(py, 98)
    pin = anchor.get("pin") or [0.5, 0.5]
    ix0, iy0 = ink[0] + clip.x, ink[1] + clip.y
    iw, ih = ink[2] - ink[0], ink[3] - ink[1]
    sx = (bx0 + bx1) / 2.0
    wide = bx1 - bx0 >= 1.2 * (by1 - by0)
    # the pin's height is read on the LAST line of type: a wide object bites
    # the baseline of a stacked headline, never swallows its last line
    ly0, ly1 = _last_line(clip, k0, ink) if wide else (ink[1], ink[3])
    py0 = ly0 + clip.y
    lh = ly1 - ly0
    if wide:
        # the top edge over the object's middle half (a lid, a desk edge)
        cols = (px >= bx0 + 0.25 * (bx1 - bx0)) & \
            (px <= bx1 - 0.25 * (bx1 - bx0))
        sy = float(np.percentile(py[cols], 3)) if cols.sum() > 20 \
            else float(by0)
    else:
        sy = float(np.median(py))
        sx = float(np.median(px))
    dx = sx - (ix0 + float(pin[0]) * iw)
    dy = sy - (py0 + float(pin[1]) * lh)
    # inside the frame's margins
    dx = min(max(dx, 0.03 * W - ix0), 0.97 * W - (ix0 + iw))
    dy = min(max(dy, 0.06 * H - iy0), 0.94 * H - (iy0 + ih))
    # never onto a face: below the chin when there is room, else as written
    for fb in _faces_at(ctx, tr, A):
        x0, y0, x1, y1 = fb
        if ix0 + dx < x1 and ix0 + iw + dx > x0 and iy0 + dy < y1 and \
                iy0 + ih + dy > y0:
            dy2 = y1 + 0.01 * H - iy0
            if iy0 + ih + dy2 <= 0.94 * H:
                dy = dy2
            else:
                notes.append(f"not seated on {d.get('id')}: it would cover "
                             "the face")
                return None
    notes.append(f"seated on {d.get('id')} (moved {dx:+.0f}, {dy:+.0f} px)")
    return dx, dy


def _faces_at(ctx, tr, A):
    """Face boxes (program px) the detector saw near trim second ``tr``."""
    try:
        from anchors import query
        t = tr - ctx.clock_offset
        rows, _s, _m = query.load("faces.yunet", ctx.source,
                                  [(max(0.0, t - 0.2), t + 0.2)])
    except Exception:  # noqa: BLE001
        return []
    best = None
    for r in rows:
        dt = abs(float(r["t"]) - t)
        if dt <= 0.2 and (best is None or dt < best[0]):
            best = (dt, r)
    out = []
    for f in (best[1].get("faces") if best else None) or []:
        x0, y0, x1, y1 = (float(v) for v in f[:4])
        out.append(((A[0, 0] * x0 + A[0, 2]) * ctx.W,
                    (A[1, 1] * y0 + A[1, 2]) * ctx.H,
                    (A[0, 0] * x1 + A[0, 2]) * ctx.W,
                    (A[1, 1] * y1 + A[1, 2]) * ctx.H))
    return out


def _fit_face_shape(plan, ctx, anchor, good_t, ok, first, geo, s, lo, hi,
                    notes):
    """``follow: shape``: each frame's transform becomes F_k . M_first,
    F_k the least-squares affine carrying the face mesh (named + oval
    points, output px) from the first good frame to frame k — the plate
    foreshortens, shears and turns with the face (zero-phase smoothed)."""
    from anchors import query, smooth as smoothmod
    samples, _sh, _m = query.load("landmarks.face", ctx.source,
                                  [(max(0.0, lo - 0.5), hi + 0.5)])
    t, P = query.face_points(samples, anchor.get("which", "any"))
    if len(t) < 2 or plan.M[first] is None:
        notes.append("shape: the face mesh is not measured; position/scale "
                     "only")
        return
    N, K = P.shape[:2]
    flat = P.reshape(N, 2 * K)
    sw, sh = ctx.src_size
    flat[:, 0::2] *= sw / sh
    flat = smoothmod.one_euro(t, smoothmod.bridge(t, flat),
                              float(anchor.get("smooth", 0.5)), scale=0.1)
    flat[:, 0::2] /= sw / sh
    S = smoothmod.sample(t, flat, good_t).reshape(len(good_t), K, 2)
    W, H = ctx.W, ctx.H

    def out_px(k):
        A = plan.A[k]
        if A is None or not np.all(np.isfinite(S[k])):
            return None
        return np.c_[A[0, 0] * W * S[k][:, 0] + A[0, 2] * W,
                     A[1, 1] * H * S[k][:, 1] + A[1, 2] * H]
    base = out_px(first)
    if base is None:
        return
    M0 = plan.M[first]
    X = np.c_[base, np.ones(K)]
    fitted = 0
    for k in range(len(plan.M)):
        if plan.M[k] is None or not ok[k]:
            continue
        cur = out_px(k)
        if cur is None:
            continue
        F, *_ = np.linalg.lstsq(X, cur, rcond=None)      # (3, 2)
        F3 = np.array([[F[0, 0], F[1, 0], F[2, 0]],
                       [F[0, 1], F[1, 1], F[2, 1]], [0.0, 0.0, 1.0]])
        plan.M[k] = F3 @ M0
        fitted += 1
    if fitted:
        notes.append(f"shape: fitted to the face mesh on {fitted} frames")


def _render(item, clip, ctx, plan, n, notes, kind, ref, lo, hi):
    """Warp, occlude and encode one anchored item's clip by ``plan``."""
    import world_defs
    import motion_engine
    anchor = item["anchor"]
    defs = world_defs.definitions(ctx.edl)
    off = ctx.clock_offset
    sw, sh = ctx.src_size
    W, H = ctx.W, ctx.H
    cw, ch = float(clip.w), float(clip.h)
    # the union box of every frame
    corners = np.array([[0, 0, 1], [cw, 0, 1], [cw, ch, 1], [0, ch, 1]])
    xs, ys = [], []
    for M in plan.M:
        if M is None:
            continue
        p = corners @ M.T
        p = p[:, :2] / p[:, 2:3]
        xs += list(p[:, 0])
        ys += list(p[:, 1])
    if not xs:
        return _empty(clip, n), notes + ["the anchor is off screen for the "
                                         "whole window"]
    bx0 = int(max(0, math.floor(min(xs)) - 2))
    by0 = int(max(0, math.floor(min(ys)) - 2))
    bx1 = int(min(W, math.ceil(max(xs)) + 2))
    by1 = int(min(H, math.ceil(max(ys)) + 2))
    bx1 = max(bx1, bx0 + 2)
    by1 = max(by1, by0 + 2)
    bw, bh = bx1 - bx0, by1 - by0
    bw -= bw % 2
    bh -= bh % 2
    if bw < 2 or bh < 2:
        return _empty(clip, n), notes + ["the anchor is off screen"]
    occ = _default_occluders(anchor, item, defs)
    # an object BEHIND the person puts the graphic behind the person too
    # (objseg.depthorder; ``depth`` auto is measured from the masks)
    if "person" not in occ:
        for o in list(occ):
            if str(o).startswith("object:"):
                d = defs.get(str(o).split(":", 1)[1])
                dep, how = object_depth(ctx, d) if d else (None, None)
                if dep == "behind":
                    occ.append("person")
                    notes.append(f"{o} is behind the person ({how}): the "
                                 "person passes in front of the graphic too")
                    break
    occ_readers = []
    for o in occ:
        if o == "person":
            r = _mask_reader(ctx, "person")
            if r is None:
                notes.append("the person matte is not measured yet: drawn "
                             "in front of the person")
            else:
                occ_readers.append(("mask", r))
        elif o == "hands":
            occ_readers.append(("hands", None))
        elif str(o).startswith("object:"):
            d = defs.get(str(o).split(":", 1)[1])
            r = _mask_reader(ctx, "object", d) if d else None
            if r is None:
                notes.append(f"{o} is not measured yet: drawn in front of "
                             "it")
            else:
                occ_readers.append(("mask", r))
    shadow = None
    if kind == "plane" and anchor.get("shadow"):
        shadow = _ShadowSource(ctx, defs.get(ref), notes)
        if not shadow.ok:
            shadow = None
    hands = None
    if any(k_ == "hands" for k_, _ in occ_readers):
        from anchors import query
        samples, _sh, _m = query.load("landmarks.hand", ctx.source,
                                      [(lo - 0.5, hi + 0.5)])
        hands = samples
        if not samples:
            notes.append("hands not measured yet: drawn in front of them")
    out_dir = os.path.join(ctx.workdir or tempfile.gettempdir(), "world")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"w3_{item.get('id')}_{int(time.time()*1e3)}"
                       ".mov")
    import cv2
    enc = _Encoder(out, bw, bh, ctx.fps)
    T = np.array([[1, 0, -bx0], [0, 1, -by0], [0, 0, 1]], np.float64)
    last = None
    k = 0
    ink = ctx._ink.get(item.get("id"))
    k_check = min(n - 1, max(0, n // 2))
    try:
        for rgba in _decode_rgba(clip.path, int(clip.w), int(clip.h)):
            if k >= n:
                break
            if ink is not None:
                rgba = _to_ink(rgba, ink)
            if k == k_check and plan.M[k] is not None and \
                    abs(plan.M[k][2, 0]) + abs(plan.M[k][2, 1]) < 1e-12:
                share = _inside_share(cv2, rgba, plan.M[k], W, H)
                if share is not None and share < 0.85:
                    notes.append(
                        f"leaves the frame: {100 * (1 - share):.0f}% of it "
                        "is outside at mid-window (no room round the "
                        "anchor; a wider shot or another device)")
            last = rgba
            enc.write(_frame(cv2, rgba, plan, k, T, bw, bh, ctx,
                             occ_readers, hands, shadow, off, sw, sh))
            k += 1
        while k < n:              # a clip a frame short: hold its last
            enc.write(_frame(cv2, last if last is not None else
                             np.zeros((int(clip.h), int(clip.w), 4),
                                      np.uint8),
                             plan, k, T, bw, bh, ctx, occ_readers, hands,
                             shadow, off, sw, sh))
            k += 1
    finally:
        enc.close()
    newclip = motion_engine.RenderedClip(
        path=out, x=bx0, y=by0, w=bw, h=bh, frames=n, captured=n,
        cached=False, seconds=0.0, empty=False)
    return newclip, notes




def _anchor_fit(item):
    """The template's ``anchor_fit`` (its MG-SPEC): the box height it wants
    as a multiple of the anchor's own size when it follows scale."""
    try:
        import motion_templates
        v = motion_templates.spec(item.get("template")).get("anchor_fit")
        return float(v) if v else None
    except Exception:  # noqa: BLE001
        return None


def _empty(clip, n):
    import motion_engine
    return motion_engine.RenderedClip(path=clip.path, x=clip.x, y=clip.y,
                                      w=clip.w, h=clip.h, frames=n,
                                      captured=0, cached=False, seconds=0.0,
                                      empty=True)


def _largest_rect(grid):
    """(r0, c0, r1, c1) of the largest all-True rectangle of a 2D bool
    grid (histogram method), or None."""
    rows, cols = grid.shape
    heights = np.zeros(cols, int)
    best, out = 0, None
    for r in range(rows):
        heights = np.where(grid[r], heights + 1, 0)
        stack = []
        for c in range(cols + 1):
            h = heights[c] if c < cols else 0
            start = c
            while stack and stack[-1][1] >= h:
                start, hh = stack.pop()
                area = hh * (c - start)
                if area > best:
                    best, out = area, (r - hh + 1, start, r + 1, c)
            stack.append((start, h))
    return out


def _visible_place(geo, s, trims, k0, quad_series, W, H, margin=0.05,
                   n=32, person=None, off=0.0):
    """[u0, v0, u1, v1]: the largest plane-space rect visible at frame
    ``k0`` — inside the frame's margins and, when the person passes in
    front, clear of where the person stands then (the type lands where it
    can be read); the whole plane when it all is."""
    import cv2
    q = quad_series[k0] if quad_series is not None else None
    tr = trims[k0]
    if q is None or tr is None or not np.all(np.isfinite(q)):
        return [0.0, 0.0, 1.0, 1.0]
    A = program_affine(geo, s, tr)
    if A is None:
        return [0.0, 0.0, 1.0, 1.0]
    qo = np.array([[(A[0, 0] * x + A[0, 2]) * W, (A[1, 1] * y + A[1, 2]) * H]
                   for x, y in q], np.float32)
    Hq = cv2.getPerspectiveTransform(
        np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32), qo)
    us = (np.arange(n) + 0.5) / n
    uu, vv = np.meshgrid(us, us)
    pts = np.stack([uu.ravel(), vv.ravel()], 1).astype(np.float32)
    out = cv2.perspectiveTransform(pts[None], Hq)[0]
    mx, my = margin * W, margin * H
    vis = ((out[:, 0] >= mx) & (out[:, 0] <= W - mx) & (out[:, 1] >= my)
           & (out[:, 1] <= H - my)).reshape(n, n)
    if person is not None:
        m = person.at(tr - off)
        if m is not None:
            mh, mw = m.shape
            Hs = cv2.getPerspectiveTransform(
                np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32),
                np.asarray(q, np.float32))
            sp = cv2.perspectiveTransform(pts[None], Hs)[0]
            xi = np.clip((sp[:, 0] * mw).astype(int), 0, mw - 1)
            yi = np.clip((sp[:, 1] * mh).astype(int), 0, mh - 1)
            grown = cv2.dilate(m, np.ones((15, 15), np.uint8))
            vis &= (grown[yi, xi] < 100).reshape(n, n)
    if vis.all():
        return [0.0, 0.0, 1.0, 1.0]
    r = _largest_rect(vis)
    if r is None:
        return [0.0, 0.0, 1.0, 1.0]
    r0, c0, r1, c1 = r
    return [c0 / n, r0 / n, c1 / n, r1 / n]


def _plane_transform(anchor, d, qo, cw, ch, sw, sh, W, H, A):
    """3x3: clip px -> output px onto the plane quad ``qo`` (output px):
    the clip's box fitted (aspect kept, as the surface would be seen face
    on) into ``place`` (default: the whole quad), PLANE_FILL of it."""
    import cv2
    import planes as planesmod
    place = anchor.get("place") or [0.0, 0.0, 1.0, 1.0]
    # the plane's own aspect, measured on the SEED quad in source pixels
    quad_src = (d or {}).get("quad")
    if quad_src:
        asp = planesmod.plane_aspect(np.array(quad_src) * [sw, sh])
    else:
        asp = planesmod.plane_aspect(qo)
    u0, v0, u1, v1 = place
    pw, ph = (u1 - u0) * asp, (v1 - v0)
    ca = cw / max(1e-6, ch)
    if ca >= pw / ph:
        fw, fh = pw * PLANE_FILL, pw * PLANE_FILL / ca
    else:
        fh, fw = ph * PLANE_FILL, ph * PLANE_FILL * ca
    cu = (u0 + u1) / 2.0 * asp
    cv = (v0 + v1) / 2.0
    # plane space (u in [0, asp], v in [0, 1]) -> quad
    src_q = np.array([[0, 0], [asp, 0], [asp, 1], [0, 1]], np.float32)
    Hq = cv2.getPerspectiveTransform(src_q, qo.astype(np.float32))
    rect = np.array([[cu - fw / 2, cv - fh / 2], [cu + fw / 2, cv - fh / 2],
                     [cu + fw / 2, cv + fh / 2], [cu - fw / 2, cv + fh / 2]],
                    np.float32)
    dst = cv2.perspectiveTransform(rect[None], Hq)[0]
    box = np.array([[0, 0], [cw, 0], [cw, ch], [0, ch]], np.float32)
    return cv2.getPerspectiveTransform(box, dst.astype(np.float32)) \
        .astype(np.float64)


def _frame(cv2, rgba, plan, k, T, bw, bh, ctx, occ_readers, hands, shadow,
           off, sw, sh):
    M = plan.M[k]
    out = np.zeros((bh, bw, 4), np.uint8)
    if M is None:
        return out
    MT = T @ M
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    pm = np.concatenate([rgba[..., :3].astype(np.float32) * a,
                         a * 255.0], axis=2)
    persp = abs(MT[2, 0]) > 1e-12 or abs(MT[2, 1]) > 1e-12
    if persp:
        w = cv2.warpPerspective(pm, MT, (bw, bh), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_CONSTANT,
                                borderValue=0)
    else:
        w = cv2.warpAffine(pm, MT[:2], (bw, bh), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    alpha = w[..., 3] / 255.0
    if plan.alpha[k] < 1.0:
        alpha = alpha * plan.alpha[k]
    if not alpha.any():
        return out
    A = plan.A[k]
    tr = plan.trim[k]
    if A is not None and tr is not None and occ_readers:
        occ = np.zeros((bh, bw), np.float32)
        for kind, r in occ_readers:
            if kind == "mask":
                m = r.at(tr - off)
                if m is None:
                    continue
                mh, mw = m.shape
                # mask px -> source fraction -> output px -> box px
                S = np.array([[A[0, 0] * ctx.W / mw, 0,
                               A[0, 2] * ctx.W + T[0, 2]],
                              [0, A[1, 1] * ctx.H / mh,
                               A[1, 2] * ctx.H + T[1, 2]]])
                mm = cv2.warpAffine(m, S, (bw, bh), flags=cv2.INTER_LINEAR,
                                    borderMode=cv2.BORDER_CONSTANT,
                                    borderValue=0)
                occ = np.maximum(occ, mm.astype(np.float32) / 255.0)
            elif kind == "hands" and hands:
                occ = np.maximum(occ, _hands_mask(cv2, hands, tr - off, A,
                                                  T, bw, bh, ctx))
        alpha = alpha * (1.0 - occ)
    rgb = w[..., :3] / np.maximum(w[..., 3:4] / 255.0, 1e-6)
    if shadow is not None and A is not None and tr is not None:
        ratio = shadow.ratio(tr - off, A, T, bw, bh)
        if ratio is not None:
            rgb = rgb * ratio[..., None]
    out[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[..., 3] = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out


def _inside_share(cv2, rgba, M, W, H):
    """The share of the graphic's ink an affine ``M`` (clip px -> output
    px) keeps inside the W x H frame, or None (no ink)."""
    a = rgba[..., 3].astype(np.float32)
    tot = float(a.sum())
    det = abs(M[0, 0] * M[1, 1] - M[0, 1] * M[1, 0])
    if tot <= 0 or det <= 1e-9:
        return None
    w = cv2.warpAffine(a, M[:2].astype(np.float64), (int(W), int(H)),
                       flags=cv2.INTER_LINEAR,
                       borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    return min(1.0, float(w.sum()) / (tot * det))


def _hands_mask(cv2, samples, t, A, T, bw, bh, ctx):
    """The hands' convex hulls (dilated by a finger's width) at sidecar
    second ``t``, in box pixels."""
    m = np.zeros((bh, bw), np.uint8)
    best = None
    for s in samples:
        dt = abs(float(s["t"]) - t)
        if dt <= 0.1 and (best is None or dt < best[0]):
            best = (dt, s)
    if best is None:
        return m.astype(np.float32)
    for hd in best[1].get("hands") or []:
        P = np.array(hd["p"], np.float64).reshape(-1, 2)
        ox = P[:, 0] * A[0, 0] * ctx.W + A[0, 2] * ctx.W + T[0, 2]
        oy = P[:, 1] * A[1, 1] * ctx.H + A[1, 2] * ctx.H + T[1, 2]
        pts = np.stack([ox, oy], 1).astype(np.int32)
        hull = cv2.convexHull(pts)
        cv2.fillConvexPoly(m, hull, 255)
        span = float(np.hypot(*(np.array([ox[9], oy[9]])
                                - np.array([ox[0], oy[0]]))))
        r = max(2, int(span * 0.12))
        m = cv2.dilate(m, np.ones((r, r), np.uint8))
    m = cv2.GaussianBlur(m, (0, 0), 1.5)
    return m.astype(np.float32) / 255.0


class _ShadowSource:
    """Shadow transfer for a plane: the program frame's luma against the
    shot's clean plate (plate.clean), both read on the source clock."""

    def __init__(self, ctx, d, notes):
        self.ok = False
        self.ctx = ctx
        self.plate = None
        self.frames = {}
        if d is None or ctx.src_path is None:
            notes.append("shadow transfer off: no plane or footage")
            return
        try:
            import sidecars
            src = ctx.source
            st = float(d["src_t"]) - ctx.clock_offset
            rows = sidecars.ready_chunks("plate.clean", src.sha256,
                                         src.shots, src.duration_s,
                                         [(st, st + 0.05)])
            if not rows:
                notes.append("shadow transfer off: clean plate not "
                             "measured yet")
                return
            h = sidecars.Handle(kind="plate.clean", source_sha=src.sha256,
                                chunks=(rows[0][0],), params=sidecars
                                .canonical_params(sidecars.resolve_params(
                                    "plate.clean", None)))
            dest = os.path.join(ctx.workdir or tempfile.gettempdir(),
                                "w3plates")
            files = sidecars.fetch(h, dest)
            if not files:
                notes.append("shadow transfer off: clean plate unreadable")
                return
            import cv2
            p = cv2.imread(files[0]["path"], cv2.IMREAD_GRAYSCALE)
            self.plate = p.astype(np.float32) / 255.0
            self.ok = True
        except Exception as e:  # noqa: BLE001
            notes.append(f"shadow transfer off ({str(e)[:80]})")

    def _luma(self, t):
        """The source frame's luma at sidecar second ``t`` at plate size."""
        key = round(t, 3)
        hit = self.frames.get(key)
        if hit is not None:
            return hit
        ph, pw = self.plate.shape
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{t:.3f}", "-i",
             self.ctx.src_path, "-frames:v", "1", "-vf",
             f"scale={pw}:{ph}:flags=area", "-f", "rawvideo", "-pix_fmt",
             "gray", "pipe:1"], capture_output=True, check=False)
        if len(proc.stdout) < pw * ph:
            return None
        lum = np.frombuffer(proc.stdout[:pw * ph], np.uint8).reshape(
            ph, pw).astype(np.float32) / 255.0
        if len(self.frames) > 4:
            self.frames.pop(next(iter(self.frames)))
        self.frames[key] = lum
        return lum

    def ratio(self, t, A, T, bw, bh):
        import cv2
        import cleanplate
        lum = self._luma(t)
        if lum is None:
            return None
        ph, pw = self.plate.shape
        r = cleanplate.shadow_ratio(lum, self.plate, SHADOW_SIGMA * ph,
                                    SHADOW_LO, SHADOW_HI)
        S = np.array([[A[0, 0] * self.ctx.W / pw, 0,
                       A[0, 2] * self.ctx.W + T[0, 2]],
                      [0, A[1, 1] * self.ctx.H / ph,
                       A[1, 2] * self.ctx.H + T[1, 2]]])
        return cv2.warpAffine(r.astype(np.float32), S, (bw, bh),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT,
                              borderValue=1.0)


# ── render-time measurement (the stale-client path) ─────────────────────

_LANDMARKS = {"face": "landmarks.face", "head": "landmarks.face",
              "hand": "landmarks.hand", "pose": "landmarks.pose"}


def _src_span(ctx, s, e, step=0.1):
    """(lo, hi) SIDECAR seconds the program window [s, e] plays, or
    None (no main footage there)."""
    tl = ctx.tl
    if tl is None:
        return None
    n = max(2, int(math.ceil((e - s) / step)) + 1)
    vals = []
    for k in range(n):
        t = min(e - 1e-3, s + (e - s) * k / (n - 1))
        v = tl.out_to_src(min(t, ctx.out_duration - 1e-3))
        if v is not None:
            vals.append(float(v) - ctx.clock_offset)
    if not vals:
        return None
    return min(vals), max(vals)


def requests(ctx):
    """[(kind, ranges, params)]: every sidecar this render's world items
    read — the anchors' landmarks, the definitions they name (and every
    definition in the edit), the person matte their occluders, head
    refinement, edge emitters and behind-subject layers read, the clean
    plate a shadow transfer reads. Exactly the requests the readers here
    make, so a launch lands on the keys a render reads."""
    import world_defs
    edl = ctx.edl or {}
    out, person = [], []
    defs = world_defs.definitions(edl)
    named = set()

    def add_def(fid):
        d = defs.get(fid)
        if d is None or fid in named:
            return
        named.add(fid)
        req = world_defs.request(d, ctx.tl, ctx.clock_offset)
        if req is not None:
            out.append(req)

    for m in edl.get("motion") or []:
        if not isinstance(m, dict) or m.get("start") is None:
            continue
        s, e = float(m["start"]), float(m["end"])
        tpl = m.get("template")
        p = m.get("params") or {}
        if tpl == "el_particles" and p.get("emit") == "edge":
            person.append((s, e))
        span = _src_span(ctx, s, e)
        if tpl == "el_pictogram" and p.get("mode", "pose") == "pose" \
                and span:
            out.append(("landmarks.pose", [(max(0.0, span[0] - 0.2),
                                            span[1] + 0.2)], None))
        if not anchored(m):
            continue
        a = m["anchor"]
        kind, ref = _target(a)
        if span:
            rng = [(max(0.0, span[0] - 0.5), span[1] + 0.5)]
            if kind in _LANDMARKS:
                out.append((_LANDMARKS[kind], rng, None))
            if kind in ("face", "head"):
                out.append(("faces.yunet", rng, None))
            if "hands" in (a.get("occluders") or []):
                out.append(("landmarks.hand", [(span[0] - 0.5,
                                                span[1] + 0.5)], None))
        if kind in ("track", "object", "plane"):
            add_def(ref)
            d = defs.get(ref) or {}
            if kind == "plane" and a.get("shadow") and d:
                st = float(d["src_t"]) - ctx.clock_offset
                out.append(("plate.clean", [(st, st + 0.05)], None))
        occ = _default_occluders(a, m, defs)
        for o in occ:
            if str(o).startswith("object:"):
                add_def(str(o).split(":", 1)[1])
        if kind == "screen" and a.get("pin") is not None and span and any(
                str(o).startswith("object:") for o in occ):
            # seating reads the faces to stay off them
            out.append(("faces.yunet", [(max(0.0, span[0] - 0.5),
                                         span[1] + 0.5)], None))
        if "person" in occ or kind == "head" or any(
                str(o).startswith("object:") for o in occ):
            # an object's depth order reads the person matte too
            person.append((s, e))
    for fid in defs:
        add_def(fid)
    if person and ctx.tl is not None:
        import matte_track
        spans = []
        for s, e in person:
            spans += matte_track.program_to_source(ctx.tl, s, e)
        rng = [(max(0.0, a - ctx.clock_offset - 0.5),
                b - ctx.clock_offset + 0.5) for a, b in spans]
        if rng:
            out.append(("matte.person", rng, matte_track.sidecar_params()))
    return out


def warm(ctx, launch=None, wait_s=0.0):
    """Ask the batch lane for every sidecar the world items of this render
    read (``requests``), as the program matte asks for its windows: a
    stale client that wrote its world items through apply_edit_batch, with
    no verb to launch the measurements, gets them from its first render.
    ``launch`` is the render's launching SourceRef (None: a read-only lane,
    nothing launched). Previews never wait (``wait_s`` 0); a final waits up
    to ``wait_s`` (the render's sidecar budget), then degrades. Never
    raises. Returns {kind: status}."""
    res = {}
    if ctx is None or ctx.source is None or launch is None:
        return res
    try:
        import sidecars
    except Exception:  # noqa: BLE001
        return res
    handles = []
    t0 = time.monotonic()
    try:
        reqs = requests(ctx)
    except Exception as e:  # noqa: BLE001
        print(f"[world] measurement requests not built ({str(e)[:140]})",
              flush=True)
        return res
    for kind, ranges, params in reqs:
        try:
            h = sidecars.ensure(kind, launch, ranges, priority="interactive",
                                params=params)
            handles.append(h)
        except Exception as e:  # noqa: BLE001 — never fails a render
            print(f"[world] {kind} not launched ({str(e)[:140]})",
                  flush=True)
    left = float(wait_s or 0.0) - (time.monotonic() - t0)
    if handles and left > 0:
        try:
            sidecars.wait(handles, left)
        except Exception:  # noqa: BLE001
            pass
    for h in handles:
        try:
            res[h.kind] = sidecars.status(h)
        except Exception:  # noqa: BLE001
            res[h.kind] = "absent"
    if handles:
        print(f"[world] measurements: " + ", ".join(
            f"{k} {v}" for k, v in sorted(res.items())), flush=True)
    return res


# ── device rows (the render's ``devices`` report; INTERFACES.md §6.3) ────

def device_card(item):
    """The card an anchored motion item is (its own ``device`` first)."""
    if item.get("device"):
        return item["device"]
    a = item.get("anchor") or {}
    to = str(a.get("to") or "")
    if item.get("template") == "el_orbit":
        return "element_orbit"
    if to.startswith("plane:"):
        return "plane_type"
    if any(str(o).startswith("object:") for o in a.get("occluders") or []):
        return "object_occluder"
    if to.startswith("object:"):
        return "object_occluder"
    fam = to.split(".")[0].split(":")[0]
    return {"hand": "anchor_hand", "face": "anchor_face",
            "head": "anchor_face", "pose": "anchor_pose",
            "track": "anchor_track"}.get(fam)


_SUBSTITUTE = ("pinned static", "drawn in front", "not measured yet: drawn",
               "no quad")


def rung_of(new, notes):
    """(rung, reason) of one anchored item from its compose result: the
    clip composed with nothing missing is ``full``; the detector-box face
    is ``reduced``; a static placement or a missing occluder (drawn in
    front of what should hide it) is ``substitute``."""
    text = "; ".join(n for n in notes or [] if n)
    if new is None or any(k in text for k in _SUBSTITUTE):
        return "substitute", text or "pinned static"
    if YUNET_NOTE in text or "plane tracking not measured yet" in text \
            or "leaves the frame" in text:
        return "reduced", text
    return "full", text or None


def _report(item, rung, reason):
    try:
        import fxgraph
        card = device_card(item)
        fam = None
        try:
            fam = (fxgraph._card(card) or {}).get("family") if card else None
        except Exception:  # noqa: BLE001
            fam = None
        fxgraph._emit([{"id": item.get("id"), "device": card,
                        "family": fam or "world", "stage": "world",
                        "rung": rung, "reason": reason, "est_ms": None}])
    except Exception:  # noqa: BLE001 — a report never fails a render
        pass


# ── the motion_layer hooks ──────────────────────────────────────────────

def augment(item, ctx):
    """Before the motion engine renders ``item``: engine data its template
    reads (3D/3E: subject-edge emitters, pose, slot spill, the truthful EDL
    breakdown). Returns the item (a new dict when it changed). Any problem
    leaves it unchanged."""
    try:
        import elements
        return elements.augment(item, ctx)
    except ImportError:
        return item
    except Exception as e:  # noqa: BLE001
        print(f"[world] motion '{item.get('id')}' data not prepared "
              f"({str(e)[:160]})", flush=True)
        return item


def finish(items, clips, ctx, warn=None):
    """After the motion engine: every anchored item's clip replaced by its
    world-composited clip (and video slots filled). Returns (items, clips),
    aligned; items without an anchor or slots come back untouched. Each
    anchored item reports one device row (its worst piece's rung)."""
    out_items, out_clips = [], []
    rows = {}
    for item, clip in zip(items, clips):
        if clip is None or getattr(clip, "empty", False):
            out_items.append(item)
            out_clips.append(clip)
            continue
        try:
            import slots
            if slots.wants(item, clip):
                clip = slots.fill(item, clip, ctx, warn=warn) or clip
        except ImportError:
            pass
        except Exception as e:  # noqa: BLE001
            if warn:
                warn(f"motion '{item.get('id')}': video slots not filled "
                     f"({str(e)[:160]})")
        if anchored(item) and ctx is not None:
            try:
                t0 = time.time()
                new, notes = compose(item, clip, ctx)
                rung, why = rung_of(new, notes)
                _keep_row(rows, item, rung, why)
                if new is not None:
                    layer = item.get("layer") or "above_captions"
                    if layer == "behind_subject":
                        item = dict(item, layer="below_captions",
                                    behind=None)
                    blend = ctx._blend.get(item.get("id"))
                    if blend:
                        item = dict(item, composite=dict(
                            item.get("composite") or {}, blend=blend))
                    clip = new
                for note in notes:
                    if warn:
                        warn(f"motion '{item.get('id')}' (world): {note}")
                print(f"[world] motion '{item.get('id')}' anchored to "
                      f"{item['anchor'].get('to')} in "
                      f"{time.time() - t0:.1f}s ({rung})", flush=True)
            except Exception as e:  # noqa: BLE001 — degrade, never fail
                why = (f"pinned static ({type(e).__name__}: "
                       f"{str(e)[:160]})")
                _keep_row(rows, item, "substitute", why)
                if warn:
                    warn(f"motion '{item.get('id')}' (world): {why}")
                if item.get("layer") == "behind_subject":
                    item = dict(item, layer="above_captions", behind=None)
        out_items.append(item)
        out_clips.append(clip)
    for item, rung, why in rows.values():
        _report(item, rung, why)
    return out_items, out_clips


_RANK = {"full": 0, "reduced": 1, "substitute": 2, "omit": 3}


def _keep_row(rows, item, rung, why):
    key = item.get("id")
    have = rows.get(key)
    if have is None or _RANK.get(rung, 3) > _RANK.get(have[1], 3):
        rows[key] = (item, rung, why)


# ── object masks for the fx graph (world_defs.object_mask) ──────────────

def object_mask_clip(plan, item, clock_offset=0.0):
    """A gray clip of the object's PROGRAM-space mask over its window
    (plan.W x plan.H, plan.fps), from objects.sam2 chunks through the
    render's geometry; None when nothing is measured."""
    edl = getattr(plan, "edl", None)
    if edl is None or plan.tl is None:
        return None
    ctx = Ctx(edl, plan.W, plan.H, plan.fps, tl=plan.tl, source=plan.source,
              preview=plan.preview, plan=plan, clock_offset=clock_offset,
              workdir=plan.workdir)
    r = _mask_reader(ctx, "object", item)
    if r is None:
        return None
    import cv2
    import fxprims
    f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
    path = os.path.join(plan.workdir, f"objmask_{item['id']}.mp4")
    W, H = int(plan.W), int(plan.H)
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{W}x{H}", "-r", f"{plan.fps:.6f}", "-i", "pipe:0",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
         "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # fxgraph's WORLD space is the picture before the camera: the geometry
    # without the zooms (this module's lock "screen")
    geo = ctx.geometry("screen")
    blank = np.zeros((H, W), np.uint8)
    try:
        for f in range(f0, f1):
            t = (f + 0.5) / plan.fps
            tr = plan.tl.out_to_src(t)
            A = program_affine(geo, t, tr) if tr is not None else None
            m = r.at(tr - clock_offset) if A is not None else None
            if m is None:
                proc.stdin.write(blank.tobytes())
                continue
            mh, mw = m.shape
            S = np.array([[A[0, 0] * W / mw, 0, A[0, 2] * W],
                          [0, A[1, 1] * H / mh, A[1, 2] * H]])
            proc.stdin.write(cv2.warpAffine(m, S, (W, H)).tobytes())
    finally:
        proc.stdin.close()
        proc.wait()
    return path
