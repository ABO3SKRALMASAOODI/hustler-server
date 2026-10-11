"""Accents: 1–N-frame punctuation on transients (EDL.fx type ``accent``).

Wave 1, track 1D (ROADMAP "1D — Punctuation, strobes and junctions v2";
contract worker/devices/INTERFACES_W1.md §3.4). Compiler ``ACCENT``, named
by ``fx_schemas.accents.AccentFx.COMPILER``.

Kinds (each a YUV-native chain; nothing converts the program to RGB):

  negative    ``negate`` — limited-range exact (Y -> 251-Y), the RGB
              negative of ref 04@1.93–12.6
  white_hit   the picture mixed to white: a 1-frame peak and ``decay_f``
              frames of exponential fall-off (tau ≈ 2 frames, ref
              02@13.667 inside its rounded card)
  colour_hit  a colour flood (screen-like on luma, chroma pushed to the
              colour; ref 01@3.833): ``colour`` or, when None, the
              outgoing shot's dominant hue measured at render
  threshold   two-level luma, no chroma
  posterize   four luma levels, quantized chroma
  mono        chroma removed
  mirror      the frame flipped
  hue_step    the hue rotated by a golden-angle step per hit
  rgb_slice   ``bands`` horizontal slices swapped and offset (``swaprect``)
              with a chroma split
  box_invert  ``bands`` seeded boxes negated (ref 15@2.33, 24@≈13); inside
              a region the region itself is the box

Graph size is flat in the number of accents (ROADMAP 1D "graph size"):
items of one stage are grouped by (kind, colour, region) and every group is
ONE chain whatever its count — a single timeline filter gated by the union
``enable`` when every hit is a full-strength full-frame op, else one
processed copy over the group's window hull composited through one alpha
stream: the per-frame strength (an expression evaluated on a 2x2 frame and
scaled up: no per-pixel expression, no file), times the region's mask,
times (box_invert) a per-hit box sprite cropped per frame. Outside the
accents' frames and outside the region the picture is bit-exact.

Scope is the stage: ``pre_captions`` changes the picture only (after the
picture cards, below the captions), ``over_type`` inverts the type too
(ref 04). Both compile before the aspect bars, the screen-frame plate, the
watermark and the end card (owner rule 4; tests/test_w1d_render.py pins it
bit-exact).
"""

import json
import math
import random

import fxprims
import regions
import wipe_masks
from fx_schemas.accents import DECAY_TAU_F, DEFAULT_BOXES, DEFAULT_SLICES

# Ops that are timeline filters at full strength over the full frame: one
# filter with the union ``enable``.
_LUMA_WHITE = 235
_LUMA_BLACK = 16


def op_for(kind, *, colour_rgb=None):
    """The processing chain of a kind (frame-independent kinds only)."""
    if kind == "negative":
        return "negate"
    if kind == "white_hit":
        return f"lutyuv=y={_LUMA_WHITE}:u=128:v=128"
    if kind == "colour_hit":
        # light, not paint: the colour SCREENED onto the picture per RGB
        # channel (darks take the colour, highlights stay light — ref
        # 01@3.833's red flood). A per-channel LUT on the branch's RGB copy.
        r, g, b = colour_rgb or (232, 54, 43)
        return (f"format=gbrp,lutrgb=r='255-(255-val)*{255 - r}/255':"
                f"g='255-(255-val)*{255 - g}/255':"
                f"b='255-(255-val)*{255 - b}/255'")
    if kind == "threshold":
        return (f"lutyuv=y='if(gte(val,126),{_LUMA_WHITE},{_LUMA_BLACK})'"
                ":u=128:v=128")
    if kind == "posterize":
        return ("lutyuv=y='16+73*min(3,floor((clip(val,16,235)-16)/54.75))'"
                ":u='clip(128+56*floor((val-128)/56+0.5),16,240)'"
                ":v='clip(128+56*floor((val-128)/56+0.5),16,240)'")
    if kind == "mono":
        return "lutyuv=u=128:v=128"
    if kind == "mirror":
        return "hflip"
    if kind == "box_invert":
        return "negate"
    return None


# Kinds whose full-strength op takes ``enable`` (timeline): one filter.
TIMELINE_KINDS = ("negative", "white_hit", "threshold", "posterize",
                  "mono")


def _even(v):
    return int(v) // 2 * 2


class _Accent:
    FILTERS = ("split", "trim", "setpts", "tpad", "overlay", "format",
               "negate", "lutyuv", "lutrgb", "hue", "hflip", "swaprect",
               "chromashift", "alphamerge", "color", "geq", "scale", "blend",
               "loop", "crop", "nullsink")
    # composites lay a processed copy over the untouched stream (overlay
    # keeps the main frame and its metadata) or gate timeline filters
    KEEPS_FRAME_TAGS = True

    # -- prepare ---------------------------------------------------------
    def prepare(self, plan, item):
        if item.get("region") or item.get("kind") == "box_invert":
            wipe_masks.precompute_guard()      # masks are precomputed
        env = wipe_masks.render_env(plan)   # read while the render's
        kind = item.get("kind")             # call stack is here
        state = _state(plan)
        if kind == "colour_hit":
            state["colour"][item["id"]] = self._colour(plan, item, env)
        got = self._prepare_region(plan, item, env, state)
        if got is None and kind == "box_invert":
            self._register_boxes(plan, item, state)
        return got

    def _prepare_region(self, plan, item, env, state):
        region = item.get("region")
        if not region:
            return None
        shape = region.get("shape")
        gkey = _group_key(item, state)
        if shape == "card":
            if env.get("edl") is None:
                return ("omit", "the render's picture cards are out of "
                        "reach here")
            spec = wipe_masks.card_spec_at(env["edl"], item["start"],
                                           item["end"])
            if spec is None:
                return ("omit", f"no picture card is on screen at "
                        f"{float(item['start']):g}s")
            path = wipe_masks.card_mask_path(plan.workdir, plan.W, plan.H,
                                             spec)
            state["card"][item["id"]] = wipe_masks.card_digest(
                plan.W, plan.H, spec)
            gkey = _group_key(item, state)
            if region.get("invert") or region.get("feather_px"):
                state["mask_ops"][gkey] = (bool(region.get("invert")),
                                           float(region.get("feather_px")
                                                 or 0.0))
            plan.add_input(f"w1d_acc:{gkey}", ["-i", path])
            return None
        if regions.is_static(region):
            if not plan.workdir or not plan.W or not plan.H:
                return ("omit", "no workdir/geometry for the region mask")
            im = regions.still(region, plan.W, plan.H)
            if im is None:
                return ("omit", "the region is empty at this frame size")
            import hashlib
            import os
            path = os.path.join(plan.workdir, "w1d_region_" + hashlib.sha256(
                gkey.encode()).hexdigest()[:16] + ".png")
            im.save(path, compress_level=wipe_masks.PNG_LEVEL)
            plan.add_input(f"w1d_acc:{gkey}", ["-i", path])
            return None
        if shape in ("rect", "ellipse", "polygon"):
            return regions.prepare(plan, item, region)   # keyed: 1C
        return None                    # glyph / face: masks at emit

    @staticmethod
    def _boxes_path(plan, gkey):
        import hashlib
        import os
        return os.path.join(plan.workdir, "w1d_boxes_" + hashlib.sha256(
            gkey.encode()).hexdigest()[:16] + ".png")

    def _register_boxes(self, plan, item, state):
        """The group's box sprite is an input (written here as a
        placeholder, rewritten at emit once the group's hits are known)."""
        import numpy as np
        gkey = _group_key(item, state)
        path = self._boxes_path(plan, gkey)
        TW, TH = (int(plan.W) + 3) // 4, (int(plan.H) + 3) // 4
        if not __import__("os").path.exists(path):
            wipe_masks.write_still(path, np.zeros((TH, TW), np.uint8))
        plan.add_input(f"w1d_boxes:{gkey}", wipe_masks.still_input(path))

    @staticmethod
    def _colour(plan, item, env):
        """'#rrggbb' for a colour_hit: its colour (the Look accent for
        'accent'), else the dominant hue of the outgoing shot's frame
        before the hit, else the Look accent, else a warm red."""
        accent = wipe_masks.look_accent(env.get("edl"))
        col = item.get("colour")
        if col and col != "accent":
            return col
        if col == "accent" and accent:
            return accent.lower()
        hue = None
        src, tl = env.get("src_path"), getattr(plan, "tl", None)
        if col is None and src and tl is not None:
            try:
                t = max(0.0, float(item["start"]) - 1.0 / plan.fps3)
                st = tl.out_to_src(t)
                if st is not None:
                    im = wipe_masks.grab_frame(src, st, max_side=480)
                    hue = wipe_masks.dominant_hue(im) if im else None
            except Exception:  # noqa: BLE001
                hue = None
        return (hue or (accent or "").lower() or "#e8362b")

    # -- report hooks ------------------------------------------------------
    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        n = f1 - f0 + int(item.get("decay_f") or 0)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        per = 1.6 if item.get("kind") in TIMELINE_KINDS and \
            not item.get("region") else 6.0
        return 40.0 + n * mpx * per

    def landing(self, plan, item):
        return [fxprims.frame_at(item["start"], plan.fps)]

    # -- emit --------------------------------------------------------------
    def stage(self, ctx, items):
        state = _state(ctx.plan)
        groups = {}
        for it in items:
            groups.setdefault(_group_key(it, state), []).append(it)
        label = ctx.label
        for gkey, group in groups.items():
            mark = len(ctx.parts)
            try:
                label = self._group(ctx, label, gkey, group, state)
            except Exception as e:  # noqa: BLE001 — degrade the group only
                del ctx.parts[mark:]
                for it in group:
                    ctx.note(it, "omit", f"compile failed "
                             f"({type(e).__name__}: {str(e)[:120]})")
        return label

    def _group(self, ctx, label, gkey, group, state):
        kind = group[0]["kind"]
        n_prog = max(1, int(ctx.n_frames))
        strength = {}
        hits = []
        for it in group:
            f0, f1 = fxprims.frame_span(it["start"], it["end"], ctx.fps)
            inten = float(it.get("intensity", 1.0) or 0.0)
            frames = {f: inten for f in range(f0, f1)}
            for k in range(1, int(it.get("decay_f") or 0) + 1):
                frames[f1 - 1 + k] = inten * math.exp(-k / DECAY_TAU_F)
            frames = {f: v for f, v in frames.items() if 0 <= f < n_prog}
            if not frames:
                ctx.note(it, "omit", "its frames are outside this program")
                continue
            for f, v in frames.items():
                strength[f] = max(strength.get(f, 0.0), v)
            hits.append((it, min(frames), max(frames) + 1))
        if not hits:
            return label
        region = group[0].get("region")
        F0 = min(a for _i, a, _b in hits)
        F1 = max(b for _i, _a, b in hits)
        spans = fxprims.merge_frame_spans((a, b) for _i, a, b in hits)
        full = all(abs(v - 1.0) < 1e-9 for v in strength.values())
        colour = state["colour"].get(group[0]["id"])
        rgb = wipe_masks.hex_rgb(colour) if kind == "colour_hit" else None
        # the simplest graph: one timeline filter, union enable
        if full and not region and kind in TIMELINE_KINDS:
            out = ctx.uid("ac")
            ctx.parts.append(f"[{label}]"
                             f"{fxprims.with_enable(op_for(kind, colour_rgb=rgb), fxprims.enable_expr(spans))}"
                             f"[{out}]")
            return out
        K = F1 - F0
        op = self._op(ctx, kind, hits, F0, rgb, group)
        alpha = self._alpha(ctx, strength, F0, K)
        if region:
            m = self._region_mask(ctx, group, gkey, region, state, F0, K)
            if m is None:
                for it, _a, _b in hits:
                    ctx.note(it, "omit", f"its {region.get('shape')} "
                             "region is not available on this render")
                return label
            alpha = _multiply(ctx, alpha, m)
        if kind == "box_invert":
            boxes = self._box_sprite(ctx, hits, F0, K, region, gkey)
            if boxes is None:
                for it, _a, _b in hits:
                    ctx.note(it, "omit", "its box mask was never bound")
                return label
            alpha = _multiply(ctx, alpha, boxes)
        return _composite(ctx, label, op, alpha, spans, F0, F1)

    # -- pieces ------------------------------------------------------------
    def _op(self, ctx, kind, hits, F0, rgb, group):
        if kind == "hue_step":
            vals = {}
            for k, (it, a, b) in enumerate(hits):
                ang = ((k + 1 + int(it.get("seed") or 0)) * 137.508) % 360
                ang = ang * float(it.get("intensity", 1.0) or 0.0)
                for f in range(a, b):
                    vals[f - F0] = ang / 360.0
            expr = wipe_masks.piecewise_expr(vals, var="n", scale=360.0)
            return f"hue=h='{expr}':s=1.25"
        if kind == "rgb_slice":
            return self._slices(ctx, hits, F0, group)
        return op_for(kind, colour_rgb=rgb)

    def _slices(self, ctx, hits, F0, group):
        W, H = int(ctx.W), int(ctx.H)
        n_b = int(group[0].get("bands") or DEFAULT_SLICES)
        n_b = max(2, n_b - n_b % 2)
        bh = _even(max(2, H // (n_b * 2)))
        pairs = n_b // 2
        rows = max(2, H // bh)
        chains = []
        per_pair = [dict(y1={}, y2={}, x1={}, x2={}, w={})
                    for _ in range(pairs)]
        for k, (it, a, b) in enumerate(hits):
            rnd = random.Random((int(it.get("seed") or 0) + 1) * 7919 + k)
            for p in range(pairs):
                ya = rnd.randrange(0, rows - 1) * bh
                yb = rnd.randrange(0, rows - 1) * bh
                if ya == yb:
                    yb = (yb + 2 * bh) % max(bh, (rows - 1) * bh)
                dx = _even(int(W * rnd.uniform(0.02, 0.09))) * \
                    (1 if rnd.random() < 0.5 else -1)
                for f in range(a, b):
                    lf = f - F0
                    per_pair[p]["y1"][lf] = ya / float(H)
                    per_pair[p]["y2"][lf] = yb / float(H)
                    per_pair[p]["x1"][lf] = max(0, -dx) / float(W)
                    per_pair[p]["x2"][lf] = max(0, dx) / float(W)
                    per_pair[p]["w"][lf] = (W - abs(dx)) / float(W)
        for p in range(pairs):
            e = {k: wipe_masks.piecewise_expr(v, var="n", scale=1.0e6)
                 for k, v in per_pair[p].items()}
            # piecewise_expr quantizes to integers: carry 1e6 fractions
            chains.append(
                f"swaprect=w='max(2,({e['w']})*w/1e6)':h={bh}:"
                f"x1='({e['x1']})*w/1e6':y1='({e['y1']})*h/1e6':"
                f"x2='({e['x2']})*w/1e6':y2='({e['y2']})*h/1e6'")
        shift = max(2, _even(int(W * 0.006)))
        chains.append(f"chromashift=cbh={shift}:crh=-{shift}")
        return ",".join(chains)

    def _alpha(self, ctx, strength, F0, K):
        vals = {f - F0: v for f, v in strength.items() if F0 <= f < F0 + K}
        out = ctx.uid("aca")
        ctx.parts.append(wipe_masks.strength_chain(vals, ctx.W, ctx.H,
                                                   ctx.fps3, K) + f"[{out}]")
        return out

    def _region_mask(self, ctx, group, gkey, region, state, F0, K):
        shape = region.get("shape")
        trim = (f"trim=start_frame={F0}:end_frame={F0 + K},"
                f"setpts=PTS-STARTPTS")
        if shape == "card" or regions.is_static(region):
            lab = ctx.input_label(f"w1d_acc:{gkey}")
            if lab is None:
                return None
            out = ctx.uid("acm")
            ctx.parts.append(f"[{lab}]format=gray,loop=loop=-1:size=1,"
                             f"{wipe_masks.pts(ctx.fps3)},trim=end_frame={K}"
                             f"[{out}]")
            ops = state["mask_ops"].get(gkey)
            if ops:
                inv, feather = ops
                o2 = ctx.uid("acm")
                out = fxprims.mask_ops(
                    ctx.parts, out, o2, uid=o2, invert=inv,
                    feather_px=feather * float(ctx.H) / 1080.0)
            return out
        if shape in ("rect", "ellipse", "polygon"):     # keyed (1C)
            pad = regions.pad(ctx, group[0], region)
        else:
            pad = regions.pad(ctx, group[0], region)    # glyph / face
        if pad is None:
            return None
        out = ctx.uid("acm")
        ctx.parts.append(f"[{pad}]format=gray,{trim}[{out}]")
        return out

    def _box_sprite(self, ctx, hits, F0, K, region, gkey):
        """A per-hit box mask: tiles of seeded boxes (or the region's own
        box) at quarter resolution on a 4-px grid, laid out in a sprite and
        cropped per frame, scaled up 4x nearest (exact box edges)."""
        import numpy as np
        W, H = int(ctx.W), int(ctx.H)
        TW, TH = (W + 3) // 4, (H + 3) // 4
        cols = 8
        tiles = [np.zeros((TH, TW), np.uint8)]
        index = {}
        for k, (it, a, b) in enumerate(hits):
            t = np.zeros((TH, TW), np.uint8)
            if region:
                t[:] = 255                     # the region IS the box
            else:
                for x0, y0, x1, y1 in seeded_boxes(
                        int(it.get("seed") or 0) + k,
                        int(it.get("bands") or DEFAULT_BOXES)):
                    t[int(y0 * TH):max(int(y0 * TH) + 1, int(y1 * TH)),
                      int(x0 * TW):max(int(x0 * TW) + 1, int(x1 * TW))] = 255
            tiles.append(t)
            for f in range(a, b):
                index[f - F0] = len(tiles) - 1
        rows = (len(tiles) + cols - 1) // cols
        sheet = np.zeros((rows * TH, cols * TW), np.uint8)
        for i, t in enumerate(tiles):
            r, c = divmod(i, cols)
            sheet[r * TH:(r + 1) * TH, c * TW:(c + 1) * TW] = t
        lab = ctx.input_label(f"w1d_boxes:{gkey}")
        if lab is None:
            return None
        wipe_masks.write_still(self._boxes_path(ctx.plan, gkey), sheet)
        ix = wipe_masks.piecewise_expr(
            {f: (v % cols) for f, v in index.items()}, var="n", scale=1.0,
            clamp=False)
        iy = wipe_masks.piecewise_expr(
            {f: (v // cols) for f, v in index.items()}, var="n", scale=1.0,
            clamp=False)
        out = ctx.uid("acb")
        ctx.parts.append(
            f"{wipe_masks.still_chain(lab, ctx.fps3, K)},"
            f"crop={TW}:{TH}:x='({ix})*{TW}':y='({iy})*{TH}',"
            f"scale={TW * 4}:{TH * 4}:flags=neighbor,crop={W}:{H}:0:0,"
            f"{wipe_masks.pts(ctx.fps3)}[{out}]")
        return out


def seeded_boxes(seed, n):
    """``n`` boxes (frame fractions) from a seed: sizes 9–34% wide and
    5–20% tall, centres pulled toward the middle of the frame where the
    subject usually is, on a 1/64 grid. Deterministic."""
    rnd = random.Random(int(seed) * 104729 + 31)
    out = []
    for _ in range(max(1, int(n))):
        w = rnd.uniform(0.09, 0.34)
        h = rnd.uniform(0.05, 0.20)
        cx = 0.5 + (rnd.random() - 0.5) * 0.7
        cy = 0.5 + (rnd.random() - 0.5) * 0.7
        x0 = round(max(0.0, min(1.0 - w, cx - w / 2)) * 64) / 64
        y0 = round(max(0.0, min(1.0 - h, cy - h / 2)) * 64) / 64
        out.append((x0, y0, min(1.0, x0 + round(w * 64) / 64),
                    min(1.0, y0 + round(h * 64) / 64)))
    return out


def _state(plan):
    st = plan.__dict__.get("_w1d_acc")
    if st is None:
        st = {"colour": {}, "card": {}, "mask_ops": {}}
        plan.__dict__["_w1d_acc"] = st
    return st


def _group_key(item, state):
    region = item.get("region")
    rsig = None
    if region:
        if region.get("shape") == "card":
            rsig = ["card", state["card"].get(item["id"]),
                    bool(region.get("invert")), region.get("feather_px")]
        elif region.get("keys"):
            rsig = ["keyed", item["id"]]
        else:
            rsig = json.dumps(region, sort_keys=True, default=str)
    kind = item.get("kind")
    extra = None
    if kind == "colour_hit":
        extra = state["colour"].get(item["id"])
    elif kind == "rgb_slice":
        extra = item.get("bands")
    return json.dumps([kind, extra, rsig], default=str)


def _multiply(ctx, a, b):
    out = ctx.uid("acx")
    ctx.parts.append(f"[{a}]format=gray[{out}a]")
    ctx.parts.append(f"[{b}]format=gray[{out}b]")
    ctx.parts.append(f"[{out}a][{out}b]blend=all_mode=multiply[{out}]")
    return out


def _composite(ctx, label, op, alpha, spans, F0, F1):
    """``op`` on the window hull [F0, F1), laid over the untouched picture
    through ``alpha`` (a hull-local gray stream), gated to ``spans``. The
    branch joins the program's pixel-format group (fxprims.region_fx's
    rule): outside the alpha and the spans the picture is bit-exact."""
    u = ctx.uid("acc")
    ov = fxprims.composite_overlay(ctx.parts, label)
    alpha_fmt = ",format=yuva420p" if ov == fxprims.OVERLAY else ""
    ctx.parts.append(f"[{label}]split[{u}a][{u}b]")
    ctx.parts.append(f"[{u}b]trim=start_frame={F0}:end_frame={F1},"
                     f"setpts=PTS-STARTPTS,{op}{alpha_fmt}[{u}p]")
    ctx.parts.append(f"[{u}p][{alpha}]alphamerge"
                     f"{wipe_masks.repad(F0, ctx.fps, alpha=True)}[{u}w]")
    out = ctx.uid("ac")
    ctx.parts.append(f"[{u}a][{u}w]{ov}:"
                     f"enable='{fxprims.enable_expr(spans)}'[{out}]")
    return out


ACCENT = _Accent()
