"""Junctions v2: duration-preserving cut devices (EDL.fx type ``junction``).

Wave 1, track 1D (ROADMAP "1D — Punctuation, strobes and junctions v2";
contract worker/devices/INTERFACES_W1.md §3.4). Compiler ``JUNCTION``,
named by ``fx_schemas.accents.JunctionFx.COMPILER``.

At each cut the renderer (fxgraph.apply_junction, Wave 0 seam) hands the
compiler two 2h-frame streams: the outgoing shot's last h frames and h
frames of its footage past the out-point (its source handle), and h frames
of the incoming footage before its in-point plus its first h frames. The
compiler returns exactly 2h frames that replace program frames [cut - h,
cut + h): the program keeps its length and its sound (``duration_exact``),
whatever the style. A side with no source handle arrives with its edge
frame held (reported in the device row, not a degrade).

Styles

  xfade built-ins   fadegrays, pixelize, radial, slices (hl/hr/vu/vd by
                    direction): ffmpeg's own transitions
  eased masks       dissolve, wipe_*, iris, columns, staircase, box_steps,
                    bands — gray mask clips computed before ffmpeg runs
                    (wipe_masks: analytic anti-aliasing, eased progress),
                    composited with alphamerge + overlay (ROADMAP §2's
                    precomputed-mask path; never a per-pixel expression)
  letter_window     the incoming shot through a motion item's letters
                    (1C's ``glyph:<id>`` mask), which then grow to the frame
  matte_cut         the incoming shot through the outgoing subject's
                    silhouette (1A's handle matte), growing to the frame;
                    ``direction: out`` swaps the background around the held
                    subject instead (ref 20@7.40)
  double_exposure   a screen-blended double exposure dissolve
  pixel_stretch     a line of pixels stretched across the frame on the cut
  hit_flash         a white (or ``colour``) flash peaking on the cut
  whip_up/down      a vertical whip pan with motion blur
  zoom_blur         a radial (zoom) blur peaking on the cut; radial_blur a
                    rotational one — polar remap + directional blur
  datamosh          the incoming shot's motion smeared over the outgoing
                    picture (in-graph; the real codec path, ``real_mosh``,
                    needs a renderer prepass — see DATAMOSH below)

Origins (iris, box_steps, letter_window, zoom/radial blur, pixel_stretch):
the frame centre, a point, a face (1A's ``matte_track:face_box``) or a
motion item's letters (1C's ``compositor:glyph_box``). A missing face or
glyph box falls back to the centre and says so (rung ``reduced``).
"""

import hashlib
import math
import os

import fxgraph
import fxprims
import wipe_masks
from fx_schemas.accents import DEFAULT_BOX_STEPS

XFADE_NAMES = {"fadegrays": "fadegrays", "pixelize": "pixelize",
               "radial": "radial"}
SLICE_NAMES = {"left": "hlslice", "right": "hrslice", "up": "vuslice",
               "down": "vdslice", None: "hrslice"}
MASK_SEQ_STYLES = ("wipe_left", "wipe_right", "wipe_up", "wipe_down",
                   "iris", "columns", "staircase", "bands", "box_steps",
                   "pixel_stretch", "letter_window")
# A prime time-base denominator for a compiled window (see _settle).
WINDOW_TB_DEN = 999983
# The box_steps fill: paper white, as ref 14@15.30's steps land.
BOX_FLASH_RGB = (242, 239, 230)
# The real datamosh re-encodes the junction window (MPEG-4 Part 2, fixed
# GOP, the incoming keyframe dropped by ``-bsf:v noise=drop=...``); the
# renderer has no prepass that hands a compiler the window's frames before
# the main graph runs, so in-render datamosh is the in-graph smear and is
# reported ``reduced``. ``real_mosh`` is the codec path itself (tested
# standalone; used as soon as the plan carries ``junction_prepass``).
DATAMOSH_REDUCED = ("the real datamosh needs the renderer's junction "
                    "prepass (requested of 1A); rendered as the in-graph "
                    "smear")


def _safe(s):
    return hashlib.sha256(str(s).encode()).hexdigest()[:16]


def _seq_dir(plan, item, name):
    return os.path.join(plan.workdir or ".",
                        f"w1d_jn_{_safe(item.get('id'))}_{name}")


def _seq_key(item, name):
    return f"w1d_jn:{item.get('id')}:{name}"


def junction_h(plan, item):
    """Frames each side of the cut the style wants (d/2)."""
    return max(1, int(round(float(item.get("duration_s") or 0.4)
                            * plan.fps3 / 2.0)))


class _Junction:
    FILTERS = ("split", "trim", "setpts", "concat", "format", "overlay",
               "alphamerge", "xfade", "color", "geq", "scale", "blend",
               "crop", "vstack", "gblur", "remap", "loop", "zoompan",
               "negate", "dilation", "nullsink", "tpad", "null", "settb")

    # -- prepare ---------------------------------------------------------
    def prepare(self, plan, item):
        wipe_masks.precompute_guard()
        wipe_masks.render_env(plan)            # cache the render context
        style = item.get("style")
        if not plan.workdir or not plan.W or not plan.H:
            return ("omit", "no workdir/geometry for the junction")
        st = _state(plan)
        names = []
        if style in MASK_SEQ_STYLES:
            names.append("m")
        if style == "box_steps":
            names.append("f")
        if style == "letter_window":
            names.append("i")
        for name in names:
            d = _seq_dir(plan, item, name)
            if not os.path.exists(os.path.join(d, "f_00000.png")):
                import numpy as np
                wipe_masks.write_sequence_at(
                    d, [np.zeros((int(plan.H), int(plan.W)), np.uint8)])
            plan.add_input(_seq_key(item, name), wipe_masks.sequence_input(
                os.path.join(d, "f_%05d.png"), plan.fps3))
        if style in ("zoom_blur", "radial_blur"):
            centre, why = self._origin(plan, item)
            st["origin"][item["id"]] = (centre, why)
            for name, arr in _polar_maps(int(plan.W), int(plan.H),
                                         centre).items():
                path = os.path.join(plan.workdir, f"w1d_polar_"
                                    f"{_safe([plan.W, plan.H, centre, POLAR_ANGLE_PX, POLAR_WRAP_PX])}"
                                    f"_{name}.png")
                if not os.path.exists(path):
                    wipe_masks.write_still(path, arr)
                plan.add_input(_seq_key(item, name),
                               wipe_masks.still_input(path))
            if why:
                return ("reduced", why)
        if style == "matte_cut":
            info = fxgraph.request_matte(plan, item, "person", "world")
            if (info or {}).get("status") == "absent":
                return ("substitute", f"{info.get('reason') or 'no matte'}"
                        " -> flash_junction")
        if style == "datamosh" and not getattr(plan, "junction_prepass",
                                               None):
            return ("reduced", DATAMOSH_REDUCED)
        return None

    def junction_frames(self, plan, item):
        return junction_h(plan, item)

    def landing(self, plan, item):
        return [fxprims.frame_at(item["at"], plan.fps)]

    def cost_ms(self, plan, item):
        n = 2 * junction_h(plan, item)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        per = {"zoom_blur": 40.0, "radial_blur": 40.0, "datamosh": 18.0,
               "double_exposure": 14.0, "whip_up": 12.0,
               "whip_down": 12.0}.get(item.get("style"), 8.0)
        return 120.0 + n * mpx * per

    # -- origin ------------------------------------------------------------
    def _origin(self, plan, item, at=None):
        """(centre px, degrade reason or None)."""
        W, H = float(plan.W), float(plan.H)
        o = item.get("origin") or {"kind": "centre"}
        kind = o.get("kind") or "centre"
        t = float(item.get("at") if at is None else at)
        if kind == "point":
            x, y = o.get("point")
            return (float(x) * W, float(y) * H), None
        if kind == "face":
            ref = o.get("ref")
            box = fxgraph.peer("matte_track:face_box", plan, t,
                               None if ref is None else int(ref),
                               default=None)
            if box:
                return (((box[0] + box[2]) / 2.0) * W,
                        ((box[1] + box[3]) / 2.0) * H), None
            return (W / 2, H / 2), ("no face box on this render; the "
                                    "transition opens from the centre")
        if kind == "glyph":
            box = fxgraph.peer("compositor:glyph_box", plan, o.get("ref"), t,
                               default=None)
            if box:
                return (((box[0] + box[2]) / 2.0) * W,
                        ((box[1] + box[3]) / 2.0) * H), None
            return (W / 2, H / 2), (f"no glyph box for {o.get('ref')} on "
                                    "this render; the transition opens from "
                                    "the centre")
        return (W / 2, H / 2), None

    # -- emit --------------------------------------------------------------
    def junction(self, ctx, items):
        item = items[0]
        for other in items[1:]:
            ctx.note(other, "omit", f"one junction per cut: "
                     f"{item.get('id')} ({item.get('style')}) is kept")
        h = int(ctx.h)
        N = 2 * h
        style = item.get("style")
        j = ctx.junction or {}
        held = [s for s, k in (("outgoing", "a_handle"),
                               ("incoming", "b_handle"))
                if j.get(k) == "freeze"]
        why = []
        if held:
            why.append(f"no source handle on the {' and '.join(held)} side: "
                       "its edge frame is held")
        if h < junction_h(ctx.plan, item):
            why.append(f"window shortened to {N} frames (short shots)")
        rung = ctx.rung(item)
        fn = getattr(self, "_" + style, None)
        if style in XFADE_NAMES or style == "slices":
            fn = self._xfade
        elif style.startswith("wipe_") or style in ("columns", "staircase",
                                                   "bands", "iris"):
            fn = self._masked
        out, extra = fn(ctx, item, h, N)
        if extra:
            rung, reason = extra
            if reason and reason != "ok":
                why.insert(0, reason)
        if why or extra:
            ctx.note(item, rung if rung in ("full", "reduced", "substitute")
                     else "full", "; ".join(why) or "ok")
        return out

    # -- shared pieces -------------------------------------------------------
    def _settle(self, ctx, label, N):
        """Exactly N frames, yuv420p, on a time base no frame rate divides.

        fxgraph.junction_overlay re-pads the window with fxprims.repad,
        whose ``setpts=N/fps/TB`` TRUNCATES: on the junction streams' own
        time base (1/fps) frame N's stamp lands on N - 1 for N = 57, 58, 59,
        113 ... at 25 fps, two frames share a stamp and the overlay showed
        the black pad frame on the window's last frame (measured on AT-1D-6,
        program frame 117). On 1/WINDOW_TB_DEN every stamp is a hair under
        its exact time and never collides, so the overlay picks every frame
        in order."""
        out = ctx.uid("jo")
        ctx.parts.append(f"[{label}]trim=end_frame={N},"
                         f"{wipe_masks.pts(ctx.fps3)},settb=1/{WINDOW_TB_DEN},"
                         f"{wipe_masks.pts(ctx.fps3)},format=yuv420p[{out}]")
        return out

    def _concat(self, ctx, a, b, h, N):
        """A's first h frames then B's last h: the plain cut, rebuilt."""
        u = ctx.uid("jc")
        ctx.parts.append(f"[{a}]trim=end_frame={h}[{u}a]")
        ctx.parts.append(f"[{b}]trim=start_frame={h}:end_frame={N},"
                         f"setpts=PTS-STARTPTS[{u}b]")
        ctx.parts.append(f"[{u}a][{u}b]concat=n=2:v=1:a=0,"
                         f"{wipe_masks.pts(ctx.fps3)}[{u}]")
        return u

    def _over(self, ctx, base, top, mask):
        """``top`` laid over ``base`` through the gray ``mask`` (255 = top):
        exact A where the mask is 0, exact B where it is 255."""
        u = ctx.uid("jm")
        ctx.parts.append(f"[{top}]format=yuva420p[{u}t]")
        ctx.parts.append(f"[{mask}]format=gray[{u}m]")
        ctx.parts.append(f"[{u}t][{u}m]alphamerge[{u}tm]")
        ctx.parts.append(f"[{base}][{u}tm]overlay=eof_action=pass:"
                         f"format=yuv420[{u}]")
        return u

    def _strength(self, ctx, values, N):
        u = ctx.uid("js")
        ctx.parts.append(wipe_masks.strength_chain(values, ctx.W, ctx.H,
                                                   ctx.fps3, N) + f"[{u}]")
        return u

    def _seq(self, ctx, item, name, frames, N):
        """Write the mask sequence registered in prepare and read it."""
        lab = ctx.input_label(_seq_key(item, name))
        if lab is None:
            raise RuntimeError(f"mask input {name} was never bound")
        wipe_masks.write_sequence_at(_seq_dir(ctx.plan, item, name), frames)
        u = ctx.uid("jq")
        ctx.parts.append(wipe_masks.seq_chain(lab, ctx.fps3, N) + f"[{u}]")
        return u

    def _colour_layer(self, ctx, rgb, N):
        u = ctx.uid("jk")
        hexc = "0x{:02X}{:02X}{:02X}".format(*rgb)
        ctx.parts.append(f"color=c={hexc}:s={int(ctx.W)}x{int(ctx.H)}:"
                         f"r={ctx.fps3:.3f},trim=end_frame={N},"
                         f"{wipe_masks.pts(ctx.fps3)}[{u}]")
        return u

    # -- styles --------------------------------------------------------------
    def _xfade(self, ctx, item, h, N):
        style = item["style"]
        name = (SLICE_NAMES.get(item.get("direction")) if style == "slices"
                else XFADE_NAMES[style])
        u = ctx.uid("jx")
        d = N / ctx.fps3
        ctx.parts.append(f"[{ctx.a_label}][{ctx.b_label}]xfade=transition="
                         f"{name}:duration={d:.6f}:offset=0[{u}]")
        return self._settle(ctx, u, N), None

    def _dissolve(self, ctx, item, h, N):
        vals = {j: wipe_masks.ease_in_out_sine(wipe_masks.progress(j, N))
                for j in range(N)}
        m = self._strength(ctx, vals, N)
        return self._settle(ctx, self._over(ctx, ctx.a_label, ctx.b_label,
                                            m), N), None

    def _masked(self, ctx, item, h, N):
        style = item["style"]
        W, H = int(ctx.W), int(ctx.H)
        extra = None
        if style.startswith("wipe_"):
            frames = wipe_masks.wipe(W, H, N, style.split("_", 1)[1])
        elif style == "columns":
            frames = wipe_masks.columns(W, H, N, item.get("count") or 6,
                                        item.get("direction"))
        elif style == "bands":
            frames = wipe_masks.bands(W, H, N, item.get("count") or 8,
                                      item.get("direction"))
        elif style == "staircase":
            frames = wipe_masks.staircase(W, H, N, item.get("direction"))
        else:                                   # iris
            centre, why = self._origin(ctx.plan, item)
            frames = wipe_masks.iris(W, H, N, centre)
            if why:
                extra = ("reduced", why)
        m = self._seq(ctx, item, "m", frames, N)
        return self._settle(ctx, self._over(ctx, ctx.a_label, ctx.b_label,
                                            m), N), extra

    def _box_steps(self, ctx, item, h, N):
        W, H = int(ctx.W), int(ctx.H)
        centre, why = self._origin(ctx.plan, item)
        reveal, flash, _lands = wipe_masks.box_steps(
            W, H, N, item.get("count") or DEFAULT_BOX_STEPS, centre)
        m = self._seq(ctx, item, "m", reveal, N)
        f = self._seq(ctx, item, "f", flash, N)
        x = self._over(ctx, ctx.a_label, ctx.b_label, m)
        c = self._colour_layer(ctx, BOX_FLASH_RGB, N)
        out = self._over(ctx, x, c, f)
        return self._settle(ctx, out, N), (("reduced", why) if why else None)

    def _hit_flash(self, ctx, item, h, N):
        p = self._concat(ctx, ctx.a_label, ctx.b_label, h, N)
        rise = {h - 2: 0.22, h - 1: 0.58}
        vals = {k: v for k, v in rise.items() if k >= 0}
        vals[h] = 1.0
        for k in range(1, N - h):
            vals[h + k] = math.exp(-k / 2.0)
        accent = wipe_masks.look_accent(
            wipe_masks.render_env(ctx.plan).get("edl"))
        rgb = wipe_masks.hex_rgb(item.get("colour") or "white", accent)
        m = self._strength(ctx, vals, N)
        c = self._colour_layer(ctx, rgb, N)
        return self._settle(ctx, self._over(ctx, p, c, m), N), None

    def _flash_junction(self, ctx, item, h, N, reason):
        """The built-in substitute (matte_cut, subject_hold): a white flash
        on the cut."""
        out, _ = self._hit_flash(ctx, dict(item, colour=None), h, N)
        return out, ("substitute", f"{reason} -> flash_junction")

    def _double_exposure(self, ctx, item, h, N):
        u = ctx.uid("jd")
        ctx.parts.append(f"[{ctx.a_label}]split[{u}a1][{u}a2]")
        ctx.parts.append(f"[{ctx.b_label}]split[{u}b1][{u}b2]")
        ctx.parts.append(f"[{u}a2]format=gbrp[{u}ag]")
        ctx.parts.append(f"[{u}b2]format=gbrp[{u}bg]")
        ctx.parts.append(f"[{u}ag][{u}bg]blend=all_mode=screen,"
                         f"format=yuv420p[{u}s]")
        m1 = {j: (wipe_masks.ease_in_out_sine((j + 0.5) / h) if j < h
                  else 1.0) for j in range(N)}
        m2 = {j: wipe_masks.ease_in_out_sine((j - h + 0.5) / max(1, N - h))
              for j in range(h, N)}
        x = self._over(ctx, f"{u}a1", f"{u}s", self._strength(ctx, m1, N))
        out = self._over(ctx, x, f"{u}b1", self._strength(ctx, m2, N))
        return self._settle(ctx, out, N), None

    def _pixel_stretch(self, ctx, item, h, N):
        W, H = int(ctx.W), int(ctx.H)
        centre, why = self._origin(ctx.plan, item)
        vertical = item.get("direction") not in ("left", "right")
        line = int(min(max(2, centre[1] if vertical else centre[0]),
                       (H if vertical else W) - 3)) // 2 * 2
        band = wipe_masks.stretch_band(W, H, N, h, line, vertical)
        m = self._seq(ctx, item, "m", band, N)
        u = ctx.uid("jp")
        ctx.parts.append(f"[{ctx.a_label}]split[{u}a1][{u}a2]")
        ctx.parts.append(f"[{ctx.b_label}]split[{u}b1][{u}b2]")
        crop = (f"crop={W}:2:0:{line},scale={W}:{H}:flags=bilinear"
                if vertical else
                f"crop=2:{H}:{line}:0,scale={W}:{H}:flags=bilinear")
        ctx.parts.append(f"[{u}a2]{crop}[{u}as]")
        ctx.parts.append(f"[{u}b2]{crop}[{u}bs]")
        pic = self._concat(ctx, f"{u}a1", f"{u}b1", h, N)
        streak = self._concat(ctx, f"{u}as", f"{u}bs", h, N)
        out = self._over(ctx, pic, streak, m)
        return self._settle(ctx, out, N), (("reduced", why) if why else None)

    def _whip(self, ctx, item, h, N, up):
        H = int(ctx.H)
        w = max(2, min(h, int(round(0.12 * ctx.fps3))))
        ys = []
        for j in range(N):
            e = wipe_masks.smoothstep(h - w, h + w, j + 0.5)
            e = 0.5 - 0.5 * math.cos(math.pi * e)        # extra ease
            ys.append(int(round(H * e)) // 2 * 2)
        if not up:
            ys = [H - y for y in ys]
        vals = {j: y for j, y in enumerate(ys) if y > 0}
        yexpr = wipe_masks.piecewise_expr(vals, var="n", scale=1.0,
                                          clamp=False)
        u = ctx.uid("jw")
        top, bot = ((ctx.a_label, ctx.b_label) if up
                    else (ctx.b_label, ctx.a_label))
        ctx.parts.append(f"[{top}][{bot}]vstack=inputs=2,"
                         f"crop={int(ctx.W)}:{H}:0:'{yexpr}'[{u}c]")
        speed = [abs(ys[min(N - 1, j + 1)] - ys[max(0, j - 1)]) / 2.0 / H
                 for j in range(N)]
        lab = f"{u}c"
        for lim, sig in ((0.015, 0.012), (0.06, 0.035), (0.16, 0.07)):
            spans = [(j, j + 1) for j in range(N) if speed[j] > lim]
            if not spans:
                continue
            nxt = ctx.uid("jw")
            ctx.parts.append(
                f"[{lab}]gblur=sigma=0.6:sigmaV={sig * H:.1f}:"
                f"enable='{fxprims.enable_expr(spans)}'[{nxt}]")
            lab = nxt
        return self._settle(ctx, lab, N), None

    def _whip_up(self, ctx, item, h, N):
        return self._whip(ctx, item, h, N, True)

    def _whip_down(self, ctx, item, h, N):
        return self._whip(ctx, item, h, N, False)

    def _polar_blur(self, ctx, item, h, N, radial):
        W, H = int(ctx.W), int(ctx.H)
        st = _state(ctx.plan)
        _centre, why = st["origin"].get(item["id"], (None, None))
        labs = {k: ctx.input_label(_seq_key(item, k))
                for k in ("fx", "fy", "ix", "iy")}
        if any(v is None for v in labs.values()):
            raise RuntimeError("polar maps were never bound")
        p = self._concat(ctx, ctx.a_label, ctx.b_label, h, N)
        # strength: a bump peaking on the cut; frames under 4% stay exact
        s = [max(0.0, 1.0 - abs(j + 0.5 - h) / float(h)) ** 1.6
             for j in range(N)]
        on = [j for j in range(N) if s[j] > 0.04]
        if not on:
            return self._settle(ctx, p, N), None
        j0, j1 = on[0], on[-1] + 1
        K = j1 - j0
        Wp, Hp = _polar_size(W, H)
        u = ctx.uid("jz")
        ctx.parts.append(f"[{p}]split[{u}m][{u}b]")
        maps = {}
        for k, lab in labs.items():
            ww, hh = (Wp, Hp) if k in ("fx", "fy") else (W, H)
            maps[k] = f"{u}{k}"
            ctx.parts.append(f"[{lab}]format=gray16le,loop=loop=-1:size=1,"
                             f"{wipe_masks.pts(ctx.fps3)},trim=end_frame={K},"
                             f"scale={ww}:{hh}:flags=neighbor[{maps[k]}]")
        ctx.parts.append(f"[{u}b]trim=start_frame={j0}:end_frame={j1},"
                         f"setpts=PTS-STARTPTS,format=gbrp[{u}g]")
        ctx.parts.append(f"[{u}g][{maps['fx']}][{maps['fy']}]remap="
                         f"fill=black[{u}pol]")
        lab = f"{u}pol"
        # cumulative passes: a frame at strength s gets every pass under it
        # (effective sigma = the root of the summed squares), five steps so
        # the blur swells and settles instead of switching on
        for lim, sig in ((0.04, 0.012), (0.15, 0.03), (0.35, 0.045),
                         (0.6, 0.06), (0.85, 0.07)):
            spans = [(j - j0, j - j0 + 1) for j in range(j0, j1)
                     if s[j] > lim]
            if not spans:
                continue
            nxt = ctx.uid("jz")
            if radial:   # spin: blur along the angle axis
                blur = (f"gblur=sigma={sig * POLAR_ANGLE_PX:.1f}:"
                        "sigmaV=0.01")
            else:        # zoom: blur along the radius axis
                blur = f"gblur=sigma=0.01:sigmaV={sig * Hp * 1.6:.1f}"
            ctx.parts.append(f"[{lab}]{blur}:enable="
                             f"'{fxprims.enable_expr(spans)}'[{nxt}]")
            lab = nxt
        ctx.parts.append(f"[{lab}][{maps['ix']}][{maps['iy']}]remap="
                         f"fill=black,format=yuv420p"
                         f"{wipe_masks.repad(j0, ctx.fps)}[{u}w]")
        out = ctx.uid("jz")
        ctx.parts.append(f"[{u}m][{u}w]overlay=eof_action=pass:"
                         f"enable='between(n,{j0},{j1 - 1})'[{out}]")
        return self._settle(ctx, out, N), (("reduced", why) if why
                                           else None)

    def _zoom_blur(self, ctx, item, h, N):
        return self._polar_blur(ctx, item, h, N, False)

    def _radial_blur(self, ctx, item, h, N):
        return self._polar_blur(ctx, item, h, N, True)

    def _letter_window(self, ctx, item, h, N):
        import numpy as np
        W, H = int(ctx.W), int(ctx.H)
        ref = (item.get("origin") or {}).get("ref")
        F0 = int((ctx.junction or {}).get("frame", 0)) - h
        centre, why = self._origin(ctx.plan, item)
        g = ctx.mask(f"glyph:{ref}")
        if g is None:
            # no glyph mask on this render: an iris from the letters' box
            # (or the centre) carries the reveal
            m = self._seq(ctx, item, "m", wipe_masks.iris(W, H, N, centre),
                          N)
            out = self._over(ctx, ctx.a_label, ctx.b_label, m)
            reason = f"no glyph mask for {ref} on this render" + \
                (f"; {why}" if why else "")
            return self._settle(ctx, out, N), ("substitute",
                                               reason + " -> an iris")
        # the letters show the incoming shot until the cut, then grow
        # about their own centre (zoompan on the mask) while an iris from
        # the same centre makes sure the frame is filled by the last frame
        cx, cy = centre[0] / W, centre[1] / H
        zmax = 9.0
        dz = {j: (zmax - 1.0) * wipe_masks.ease_in_cubic(
            (j - h + 1) / float(max(1, N - h))) for j in range(h, N)}
        zexpr = "1+(" + wipe_masks.piecewise_expr(
            dz, var="in", scale=1000.0, clamp=False) + ")/1000"
        u = ctx.uid("jl")
        ctx.parts.append(
            f"[{g}]trim=start_frame={max(0, F0)}:end_frame={max(0, F0) + N},"
            f"setpts=PTS-STARTPTS,format=gray,"
            f"zoompan=z='{zexpr}':x='{cx:.5f}*iw-iw/zoom*{cx:.5f}':"
            f"y='{cy:.5f}*ih-ih/zoom*{cy:.5f}':d=1:s={W}x{H}:"
            f"fps={ctx.fps3:.3f},{wipe_masks.pts(ctx.fps3)},"
            f"format=gray[{u}g]")
        fill = [np.zeros((H, W), np.uint8)] * h + \
            wipe_masks.iris(W, H, N - h, centre, halo=0.0)
        i = self._seq(ctx, item, "i", fill, N)
        ctx.parts.append(f"[{u}g][{i}]blend=all_mode=lighten[{u}m]")
        out = self._over(ctx, ctx.a_label, ctx.b_label, f"{u}m")
        return self._settle(ctx, out, N), (("reduced", why) if why else None)

    def _matte_cut(self, ctx, item, h, N):
        m = fxgraph.handle_matte(ctx, "a")
        if m is None:
            return self._flash_junction(ctx, item, h, N,
                                        "no handle matte on this render")
        k = max(1, min(h, int(round(0.08 * ctx.fps3))))      # ~2 frames
        u = ctx.uid("jt")
        if item.get("direction") == "out":
            # ref 20@7.40: the outgoing subject holds while the incoming
            # shot replaces the world around it, then the cut completes
            ctx.parts.append(f"[{m}]format=gray,trim=end_frame={N},"
                             f"negate[{u}inv]")
            gate = self._strength(ctx, {j: 1.0 for j in range(h - k, N)}, N)
            full = self._strength(ctx, {j: 1.0 for j in range(h + 1, N)}, N)
            ctx.parts.append(f"[{u}inv][{gate}]blend=all_mode=multiply"
                             f"[{u}g]")
            ctx.parts.append(f"[{u}g][{full}]blend=all_mode=lighten[{u}m]")
        else:
            # the incoming shot through the outgoing silhouette, which
            # grows (dilation at 1/8 scale) while the rest dissolves in
            W, H = int(ctx.W), int(ctx.H)
            sw, sh = max(8, W // 8 // 2 * 2), max(8, H // 8 // 2 * 2)
            chain = [f"[{m}]format=gray,trim=end_frame={N},"
                     f"scale={sw}:{sh}:flags=area"]
            for i in range(1, 7):
                chain.append(f"dilation=enable='gte(n,{h + i // 2})'")
            chain.append(f"gblur=sigma=1.2,scale={W}:{H}:flags=bicubic")
            ctx.parts.append(",".join(chain) + f"[{u}sil]")
            gate = self._strength(ctx, {j: 1.0 for j in range(h - k, N)}, N)
            ramp = self._strength(ctx, {
                j: wipe_masks.ease_in_cubic((j - h + 0.5) / max(1, N - h))
                for j in range(h, N)}, N)
            ctx.parts.append(f"[{u}sil][{gate}]blend=all_mode=multiply"
                             f"[{u}g]")
            ctx.parts.append(f"[{u}g][{ramp}]blend=all_mode=lighten[{u}m]")
        out = self._over(ctx, ctx.a_label, ctx.b_label, f"{u}m")
        # the handle matte is here: full, whatever prepare foresaw
        return self._settle(ctx, out, N), ("full", "ok")

    def _datamosh(self, ctx, item, h, N):
        """In-graph datamosh: the outgoing shot's last frame stays on
        screen and the incoming shot's frame-to-frame changes are added to
        it in macroblocks (grainextract / grainmerge at 1/16 scale), so B's
        motion drags A's colours until B resolves over the window's last
        frames. The codec version is ``real_mosh``."""
        W, H = int(ctx.W), int(ctx.H)
        bw, bh = max(4, W // 16), max(4, H // 16)
        u = ctx.uid("jdm")
        ctx.parts.append(f"[{ctx.a_label}]split[{u}a1][{u}a2]")
        ctx.parts.append(f"[{ctx.b_label}]split=3[{u}b1][{u}b2][{u}b3]")
        # A's last real frame (h - 1), held for the mosh frames
        ctx.parts.append(f"[{u}a2]trim=start_frame={h - 1}:end_frame={h},"
                         f"setpts=PTS-STARTPTS,format=gbrp,"
                         f"loop=loop=-1:size=1,{wipe_masks.pts(ctx.fps3)},"
                         f"trim=end_frame={N - h}[{u}af]")
        # B's change since the cut frame, per macroblock
        ctx.parts.append(f"[{u}b2]trim=start_frame={h},setpts=PTS-STARTPTS,"
                         f"format=gbrp,split[{u}bx][{u}by]")
        ctx.parts.append(f"[{u}by]trim=end_frame=1,loop=loop=-1:size=1,"
                         f"{wipe_masks.pts(ctx.fps3)},trim=end_frame={N - h}"
                         f"[{u}b0]")
        ctx.parts.append(f"[{u}bx][{u}b0]blend=all_mode=grainextract,"
                         f"scale={bw}:{bh}:flags=area,"
                         f"scale={W}:{H}:flags=neighbor[{u}d]")
        ctx.parts.append(f"[{u}af][{u}d]blend=all_mode=grainmerge,"
                         f"format=yuv420p[{u}mo]")
        # B resolves over the last third of the after-cut frames
        tail = max(2, (N - h) // 3)
        vals = {j: wipe_masks.ease_in_cubic((j - (N - h - tail) + 1)
                                            / float(tail))
                for j in range(N - h - tail, N - h)}
        ctx.parts.append(f"[{u}b3]trim=start_frame={h},setpts=PTS-STARTPTS"
                         f"[{u}bt]")
        m = self._strength(ctx, vals, N - h)
        after = self._over(ctx, f"{u}mo", f"{u}bt", m)
        ctx.parts.append(f"[{u}a1]trim=end_frame={h}[{u}ah]")
        ctx.parts.append(f"[{u}b1]nullsink")
        ctx.parts.append(f"[{u}ah][{after}]concat=n=2:v=1:a=0[{u}]")
        extra = None
        if not getattr(ctx.plan, "junction_prepass", None):
            extra = ("reduced", DATAMOSH_REDUCED)
        return self._settle(ctx, u, N), extra


# Polar maps: the angle runs on x over POLAR_ANGLE_PX columns plus a wrap
# margin each side (so a blur along the angle crosses the ±pi seam instead
# of stopping at it), the radius on y.
POLAR_ANGLE_PX = 2048
POLAR_WRAP_PX = 576


def _polar_size(W, H):
    R = math.hypot(W, H) / 2.0
    return POLAR_ANGLE_PX + 2 * POLAR_WRAP_PX, int(math.ceil(R / 2) * 2)


def _polar_maps(W, H, centre):
    """uint16 remap maps: forward (frame -> polar: angle on x, radius on
    y) at the polar size, inverse (polar -> frame) at the frame size. The
    polar radius covers the farthest corner from ``centre``."""
    import numpy as np
    cx, cy = float(centre[0]), float(centre[1])
    Wp, Hp = _polar_size(W, H)
    core, pad = POLAR_ANGLE_PX, POLAR_WRAP_PX
    R = max(math.hypot(cx - x, cy - y) for x in (0, W) for y in (0, H))
    us, vs = np.meshgrid(np.arange(Wp, dtype=np.float64) + 0.5,
                         np.arange(Hp, dtype=np.float64) + 0.5)
    th = (us - pad) / core * 2 * math.pi - math.pi
    r = vs / Hp * R
    fx = np.clip(np.round(cx + r * np.cos(th) - 0.5), 0, W - 1)
    fy = np.clip(np.round(cy + r * np.sin(th) - 0.5), 0, H - 1)
    xs, ys = np.meshgrid(np.arange(W, dtype=np.float64) + 0.5,
                         np.arange(H, dtype=np.float64) + 0.5)
    dx, dy = xs - cx, ys - cy
    ix = np.clip(np.round((np.arctan2(dy, dx) + math.pi) / (2 * math.pi)
                          * core + pad - 0.5), 0, Wp - 1)
    iy = np.clip(np.round(np.hypot(dx, dy) / R * Hp - 0.5), 0, Hp - 1)
    return {"fx": fx.astype(np.uint16), "fy": fy.astype(np.uint16),
            "ix": ix.astype(np.uint16), "iy": iy.astype(np.uint16)}


def _state(plan):
    st = plan.__dict__.get("_w1d_jn")
    if st is None:
        st = {"origin": {}}
        plan.__dict__["_w1d_jn"] = st
    return st


# ── the real datamosh (codec path) ──────────────────────────────────────

def real_mosh(a_path, b_path, out_path, *, fps, size=None, ffmpeg=None):
    """The codec datamosh: A's frames then B's, encoded to MPEG-4 Part 2
    with exactly two keyframes (A's first frame and B's) and no B-frames;
    the ``noise`` bitstream filter drops B's keyframe; decoding then lays
    B's motion vectors and residuals onto A's last picture. Deterministic
    (single thread, fixed quantizer). ``a_path``/``b_path`` are the two
    sides as short clips; returns the moshed clip (FFV1, one frame shorter
    than the two together: the dropped keyframe), or None."""
    import shutil
    import subprocess
    import tempfile
    exe = ffmpeg or wipe_masks.ffmpeg_bin()
    d = tempfile.mkdtemp(prefix="w1d_mosh_")
    try:
        sc = f",scale={int(size[0])}:{int(size[1])}" if size else ""
        na = _frame_count(exe, a_path, fps)
        keyed = os.path.join(d, "keyed.avi")
        r = subprocess.run(
            [exe, "-v", "error", "-y", "-i", str(a_path), "-i", str(b_path),
             "-filter_complex",
             f"[0:v]fps={float(fps):.3f}{sc},setsar=1,format=yuv420p[a];"
             f"[1:v]fps={float(fps):.3f}{sc},setsar=1,format=yuv420p[b];"
             "[a][b]concat=n=2:v=1:a=0[v]", "-map", "[v]",
             "-c:v", "mpeg4", "-q:v", "3", "-g", "100000", "-bf", "0",
             "-sc_threshold", "1000000000", "-threads", "1",
             "-force_key_frames", f"expr:eq(n,0)+eq(n,{na})", keyed],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return None
        dropped = os.path.join(d, "dropped.avi")
        r = subprocess.run(
            [exe, "-v", "error", "-y", "-i", keyed, "-c", "copy",
             "-bsf:v", "noise=drop='key*gt(n,0)'", dropped],
            capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return None
        r = subprocess.run(
            [exe, "-v", "error", "-y", "-i", dropped, "-threads", "1",
             "-c:v", "ffv1", "-pix_fmt", "yuv420p", str(out_path)],
            capture_output=True, text=True, timeout=300)
        return str(out_path) if r.returncode == 0 else None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _frame_count(exe, path, fps):
    import subprocess
    out = subprocess.run(
        [exe, "-v", "error", "-i", str(path), "-vf",
         f"fps={float(fps):.3f}", "-f", "framemd5", "-"],
        capture_output=True, text=True).stdout
    return sum(1 for ln in out.splitlines() if ln and not ln.startswith("#"))


JUNCTION = _Junction()
