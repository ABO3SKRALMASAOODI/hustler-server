"""Depth in source space: the ``depth.small`` sidecar and its readers
(track 4A; worker/devices/INTERFACES_W4.md §2).

Depth Anything V2 Small (Apache-2.0, official weights pinned in
models.lock) measures relative depth on KEYFRAMES of the source — 8 per
second by default, 518 px on the long side — once per source window, in
the ``sidecar`` job on the batch lane (the only lane with torch). Like the
person matte, depth is analysed once and every device reuses it: parallax
posters, depth of field and rack focus, fog and light by depth, the
LiDAR-style point cloud.

Storage (``depth.npz.v1``): one compressed .npz per chunk — ``depth``
uint16 [n, h, w], relative inverse depth normalised per chunk (65535 = the
nearest 1st percentile... 0 = the farthest), and ``t`` float32 [n], the
SOURCE seconds of each keyframe. Images (a project still) use the same kind
over [0, 0.04] of the image's own sha.

Readers (every lane: numpy only):

  read_chunks(files)              -> DepthClip   (t, depth float32 0..1)
  DepthClip.at(src_t)             -> [h, w] depth at a source second
                                     (linear between keyframes of one shot)
  to_program(clip, edl, tl, t, W, H, src_w, src_h)
                                  -> [H', W'] depth in PROGRAM space (the
                                     picture's own crop/follow/card geometry,
                                     card_geom.windows), None outside footage

The child (``child_depth``) is what analysis_jobs runs in its child
process; it imports torch and the vendored network lazily.
"""

import json
import os
import time

import numpy as np

KIND = "depth.small"
FORMAT = "depth.npz.v1"
MODEL = "depth_anything_v2_small"
# Normalisation percentiles per chunk: the near and far ends of a shot's
# depth (robust to a stray pixel), so a chunk's 0..1 spans its own scene.
P_LO, P_HI = 1.0, 99.0
_NET = {}


# ── the child (batch lane) ───────────────────────────────────────────────

def _net(path, threads=None):
    """The vendored DA2-Small network with the pinned weights (cached)."""
    key = (path, threads)
    if key in _NET:
        return _NET[key]
    import torch
    from depth_anything.dpt import DepthAnythingV2
    if threads:
        torch.set_num_threads(int(threads))
    net = DepthAnythingV2(encoder="vits", features=64,
                          out_channels=[48, 96, 192, 384])
    sd = torch.load(path, map_location="cpu", weights_only=True)
    net.load_state_dict(sd)
    net.eval()
    _NET[key] = net
    return net


def infer(net, bgr, input_size=518):
    """Relative inverse depth (float32, high = near) of one BGR frame, at
    the frame's own size. CPU only (production parity)."""
    import torch
    import torch.nn.functional as F
    img, (h, w) = net.image2tensor(bgr, input_size)
    img = img.cpu()
    with torch.no_grad():
        d = net.forward(img)
        d = F.interpolate(d[:, None], (h, w), mode="bilinear",
                          align_corners=True)[0, 0]
    return d.numpy().astype(np.float32)


def normalise(stack):
    """[n, h, w] float -> uint16 0..65535 over the chunk's own P_LO..P_HI."""
    lo = float(np.percentile(stack, P_LO))
    hi = float(np.percentile(stack, P_HI))
    if hi - lo < 1e-6:
        hi = lo + 1e-6
    out = np.clip((stack - lo) / (hi - lo), 0.0, 1.0)
    return (out * 65535.0 + 0.5).astype(np.uint16), (lo, hi)


def child_depth(spec, ch):
    """One chunk: keyframes at ``fps``, the network at ``input_size``, the
    maps stored at the decode size (long side ``max_side``). Returns (path,
    meta) for analysis_jobs._publish."""
    import analysis_jobs as aj
    params, srcinfo, engine = spec["params"], spec["source"], spec["engine"]
    sw, sh = int(srcinfo["width"]), int(srcinfo["height"])
    side = int(params.get("max_side") or 518)
    k = side / float(max(sw, sh))
    w, h = aj._even(sw * k), aj._even(sh * k)
    fps = float(params.get("fps") or 8.0)
    span = float(ch["t1"]) - float(ch["t0"])
    n = max(1, int(round(span * fps)))
    net = _net(engine["path"], engine.get("threads") or None)
    maps, ts, t_model = [], [], 0.0
    i = 0
    for frame in aj._decode(spec["src"], ch["t0"], max(span, 0.04), w, h,
                            fps, n):
        t = time.perf_counter()
        maps.append(infer(net, np.ascontiguousarray(frame),
                          int(params.get("input_size") or 518)))
        t_model += time.perf_counter() - t
        ts.append(float(ch["t0"]) + i / fps)
        i += 1
    if not maps:
        raise aj.DecodeError("no frames decoded in the chunk window")
    stack = np.stack(maps)
    q, (lo, hi) = normalise(stack)
    out = os.path.join(spec["outdir"], f"{ch['out_key']}.npz")
    np.savez_compressed(out, depth=q, t=np.asarray(ts, np.float32),
                        near_is_high=np.asarray(1, np.uint8))
    flicker = float(np.mean(np.abs(np.diff(q.astype(np.float32) / 65535.0,
                                           axis=0)))) if len(q) > 1 else 0.0
    return out, {"fps": fps, "frames": len(maps), "width": w, "height": h,
                 "ms_per_frame": round(1000.0 * t_model / max(1, i), 1),
                 "qa": {"flicker": round(flicker, 5), "edge_chatter": None,
                        "coverage": None, "range": [round(lo, 4),
                                                    round(hi, 4)]}}


# ── readers (every lane) ─────────────────────────────────────────────────

class DepthClip:
    """Depth keyframes on the source clock: ``t`` [n] seconds, ``d`` [n, h,
    w] float32 0..1 (1 = near). ``cuts`` (source seconds) keep
    interpolation inside one shot."""

    def __init__(self, t, d, cuts=()):
        order = np.argsort(t)
        self.t = np.asarray(t, np.float64)[order]
        self.d = np.asarray(d, np.float32)[order]
        self.cuts = sorted(float(c) for c in cuts or ())
        self.h, self.w = self.d.shape[1:3] if len(self.d) else (0, 0)

    def __len__(self):
        return len(self.t)

    def _shot(self, t):
        k = 0
        for c in self.cuts:
            if t >= c:
                k += 1
        return k

    def at(self, src_t):
        """[h, w] depth at ``src_t``: linear between the bracketing
        keyframes of the same shot, the nearest one otherwise; None when
        the clip is empty or ``src_t`` is more than 0.5 s from any key."""
        if not len(self.t):
            return None
        t = float(src_t)
        j = int(np.searchsorted(self.t, t))
        if j <= 0:
            return self.d[0] if self.t[0] - t <= 0.5 else None
        if j >= len(self.t):
            return self.d[-1] if t - self.t[-1] <= 0.5 else None
        a, b = j - 1, j
        if self._shot(self.t[a]) != self._shot(self.t[b]):
            # never blend across a cut: the side of t's own shot
            return self.d[a] if self._shot(t) == self._shot(self.t[a]) \
                else self.d[b]
        span = self.t[b] - self.t[a]
        if span <= 1e-6:
            return self.d[a]
        x = (t - self.t[a]) / span
        return (1.0 - x) * self.d[a] + x * self.d[b]


def read_chunks(files, cuts=()):
    """A DepthClip from fetched chunk files ([{path, ...}] as
    sidecars.fetch returns them); None when nothing is readable."""
    ts, ds = [], []
    for f in files or []:
        path = f["path"] if isinstance(f, dict) else f
        try:
            with np.load(path) as z:
                d = z["depth"].astype(np.float32) / 65535.0
                t = z["t"].astype(np.float64)
        except Exception:  # noqa: BLE001 — a bad chunk is inert
            continue
        ts += list(t)
        ds += list(d)
    if not ts:
        return None
    shapes = {x.shape for x in ds}
    if len(shapes) > 1:
        # one geometry per clip (a chunk from a mid-deploy lane is left out)
        common = max(shapes, key=lambda s: sum(1 for x in ds
                                               if x.shape == s))
        keep = [(t, x) for t, x in zip(ts, ds) if x.shape == common]
        ts, ds = [t for t, _x in keep], [x for _t, x in keep]
    return DepthClip(np.asarray(ts), np.stack(ds), cuts)


def fetch(source, ranges, dest, *, params=None, wait_s=0.0,
          priority="interactive", launch=None):
    """(DepthClip | None, status) for SOURCE ``ranges``: ensures the
    sidecar (through ``launch``, a launchable SourceRef of the same sha,
    when the render may launch: matte_track.RENDER_LAUNCH), waits at most
    ``wait_s`` (previews 0, finals <= 90) and reads what is ready. Never
    raises."""
    try:
        import sidecars
        if launch is None:
            try:
                import matte_track
                cand = matte_track.RENDER_LAUNCH.get()
                if cand is not None and getattr(cand, "sha256", None) == \
                        getattr(source, "sha256", None):
                    launch = cand
            except Exception:  # noqa: BLE001
                launch = None
        if launch is not None:
            sidecars.ensure(KIND, launch, ranges, priority=priority,
                            params=params)
        h = sidecars.ensure(KIND, source, ranges, priority=priority,
                            params=params)
        if wait_s:
            sidecars.wait([h], wait_s)
        st = sidecars.status(h)
        files = sidecars.fetch(h, dest)
        clip = read_chunks(files, cuts=source.shots)
        return clip, ("ready" if clip is not None and st == "ready" else
                      "partial" if clip is not None else st)
    except Exception as e:  # noqa: BLE001
        return None, f"failed ({str(e)[:80]})"


def local_clip(path, t0, t1, *, fps=8.0, max_side=518, input_size=518,
               model=None, threads=None, cuts=()):
    """Development/refbench path: measure depth in-process (needs torch and
    the weights; never on a production render). Returns a DepthClip."""
    import analysis_jobs as aj
    import qa_metrics
    info = qa_metrics.probe(path)
    sw, sh = int(info["width"]), int(info["height"])
    k = max_side / float(max(sw, sh))
    w, h = aj._even(sw * k), aj._even(sh * k)
    model = model or os.environ.get("DEPTH_MODEL") or _default_model()
    net = _net(model, threads)
    span = max(0.04, float(t1) - float(t0))
    n = max(1, int(round(span * fps)))
    maps, ts = [], []
    for i, frame in enumerate(aj._decode(path, t0, span, w, h, fps, n)):
        maps.append(infer(net, np.ascontiguousarray(frame), input_size))
        ts.append(float(t0) + i / fps)
    if not maps:
        return None
    q, _r = normalise(np.stack(maps))
    return DepthClip(np.asarray(ts), q.astype(np.float32) / 65535.0, cuts)


def _default_model():
    try:
        import config
        return getattr(config, "DEPTH_MODEL", None)
    except Exception:  # noqa: BLE001
        return None


def save_clip(clip, path):
    np.savez_compressed(path, t=clip.t.astype(np.float32),
                        depth=(np.clip(clip.d, 0, 1) * 65535 + .5)
                        .astype(np.uint16), cuts=np.asarray(clip.cuts))


def load_clip(path):
    with np.load(path) as z:
        cuts = list(z["cuts"]) if "cuts" in z else []
        return DepthClip(z["t"].astype(np.float64),
                         z["depth"].astype(np.float32) / 65535.0, cuts)


# ── into program space ───────────────────────────────────────────────────

def warp(src_map, windows, W, H, fill=0.0):
    """A source-space map (fractions of the SOURCE frame, any resolution)
    laid into a W x H program-space map through ``windows`` ([(box, src)]
    from card_geom.windows: box = program fractions, src = source
    fractions). Pixels no window covers read ``fill``."""
    import cv2
    out = np.full((int(H), int(W)), fill, np.float32)
    if src_map is None:
        return out
    mh, mw = src_map.shape[:2]
    for box, src in windows or []:
        x0, y0 = int(round(box[0] * W)), int(round(box[1] * H))
        x1, y1 = int(round(box[2] * W)), int(round(box[3] * H))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        sx0, sy0, sx1, sy1 = (src[0] * mw, src[1] * mh, src[2] * mw,
                              src[3] * mh)
        # an affine map of the source rect onto the box (sub-pixel exact)
        a = (sx1 - sx0) / float(x1 - x0)
        b = (sy1 - sy0) / float(y1 - y0)
        m = np.float32([[a, 0, sx0 + 0.5 * a - 0.5],
                        [0, b, sy0 + 0.5 * b - 0.5]])
        patch = cv2.warpAffine(src_map.astype(np.float32), m,
                               (x1 - x0, y1 - y0),
                               flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                               borderMode=cv2.BORDER_REPLICATE)
        out[y0:y1, x0:x1] = patch
    return out


def to_program(clip, edl, tl, t, W, H, src_w, src_h, *, fill=0.0):
    """The depth under program second ``t`` in program space (world: the
    picture's geometry before the camera), or None when the program shows
    no main footage there (an insert) or no keyframe is near."""
    import card_geom
    src_t = tl.out_to_src(float(t)) if tl is not None else float(t)
    if src_t is None or clip is None:
        return None
    m = clip.at(src_t)
    if m is None:
        return None
    wins = card_geom.windows(edl, src_t, W, H, src_w, src_h, None, t)
    if not wins:
        return None
    return warp(m, wins, W, H, fill)


def smooth(maps, alpha=0.6):
    """Temporal EMA over consecutive program-space maps (None breaks the
    run): keyframe interpolation steps and network jitter do not flicker
    the devices that read depth."""
    out, prev = [], None
    for m in maps:
        if m is None:
            out.append(None)
            prev = None
            continue
        prev = m if prev is None else alpha * prev + (1 - alpha) * m
        out.append(prev.astype(np.float32))
    return out


def stability(maps, mask=None):
    """Mean |Δdepth| frame to frame (0..1) over ``mask`` (or everything):
    AT-4A-1's temporal depth stability."""
    vals = []
    for a, b in zip(maps, maps[1:]):
        if a is None or b is None:
            continue
        d = np.abs(b - a)
        if mask is not None:
            d = d[mask]
        if d.size:
            vals.append(float(d.mean()))
    return float(np.mean(vals)) if vals else None


def manifest_meta(meta):
    return json.dumps(meta, separators=(",", ":"))
