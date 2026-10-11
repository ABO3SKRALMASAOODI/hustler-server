"""Strobes: frame-exact image trains over the program (EDL.fx ``strobe``).

Wave 1, track 1D (ROADMAP "1D — Punctuation, strobes and junctions v2";
contract worker/devices/INTERFACES_W1.md §3.4). Compiler ``STROBE``, named
by ``fx_schemas.accents.StrobeFx.COMPILER``.

A strobe is a TIMELINE-NEUTRAL full-frame cover: over its window the
picture is replaced, frame for frame, by a list of images held 1–8 frames
each (ref 01@0.04–0.96's 2-frame product strobe; 1E variant sets), while
the sound and the timeline run on underneath — no insert, so the 0.2 s
insert floor never applies and nothing re-times.

Images (``frames[].source``, an ``FxSource``):

  asset    a project image, or a frame of a project video at ``offset_s``
           (a 1E variant is an asset)
  source   the main video's frame at source second ``src_t`` (a clip frame)
  colour   a flat colour (``accent`` = the Look's accent)
  program  the live program itself (``offset_s`` away, at most ±2 s), so a
           strobe can alternate stills with the moving picture

Every still is decoded and fitted ONCE in ``prepare`` (``fit``: cover,
contain, or register — every image scaled exactly as the first, so a
variant set lands on the same pixels), negated there when asked, and the
window is written as a PNG sequence whose repeated frames are hard links
of one file: the graph reads one input and lays it over the program with
the window's ``enable`` — two composites whatever the image count.
"""

import os

import fxprims
import wipe_masks
from fx_schemas.accents import strobe_schedule

# program-kind frames further than this from now are refused (buffering)
MAX_PROGRAM_OFFSET_S = 2.0


class _Strobe:
    FILTERS = ("split", "trim", "setpts", "tpad", "overlay", "format",
               "negate", "scale", "nullsink")
    KEEPS_FRAME_TAGS = True

    def prepare(self, plan, item):
        wipe_masks.precompute_guard()
        env = wipe_masks.render_env(plan)
        if not plan.workdir or not plan.W or not plan.H:
            return ("omit", "no workdir/geometry for the strobe")
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        f1 = min(f1, int(plan.n_frames))
        n = f1 - f0
        if n <= 0:
            return ("omit", "its window is outside this program")
        frames = item.get("frames") or []
        sched = strobe_schedule(frames, item.get("pattern") or "sequence",
                                item.get("seed"), n)
        W, H = int(plan.W), int(plan.H)
        used = sorted({i for i, _a, _n in sched})
        stills, missing, live, cache = {}, [], {}, {}
        ref_size = None
        accent = wipe_masks.look_accent(env.get("edl"))
        for i in used:
            fr = frames[i]
            src = fr.get("source") or {}
            kind = src.get("kind")
            if kind == "program":
                off = float(src.get("offset_s") or 0.0)
                if abs(off) > MAX_PROGRAM_OFFSET_S:
                    missing.append(f"image {i}: a program frame more than "
                                   f"{MAX_PROGRAM_OFFSET_S:g}s away")
                    continue
                live[i] = (int(round(off * plan.fps3)),
                           bool(fr.get("negate")))
                continue
            ck = (kind, src.get("asset_key"), src.get("src_t"),
                  src.get("offset_s"), src.get("colour"))
            if ck not in cache:
                cache[ck] = self._image(plan, env, src, accent)
            im = cache[ck]
            if im is None:
                missing.append(f"image {i} ({kind}) is not available here")
                continue
            if ref_size is None and kind != "colour":
                ref_size = im.size        # register on the first picture
            stills[i] = (im, bool(fr.get("negate")))
        if not stills and not live:
            return ("omit", "; ".join(missing) or "no image to strobe")
        fit = item.get("fit") or "cover"
        d = os.path.join(plan.workdir,
                         f"w1d_strobe_{wipe_masks._safe_id(item['id'])}")
        os.makedirs(d, exist_ok=True)
        from PIL import Image, ImageOps
        paths = {}
        for i, (im, neg) in stills.items():
            out = wipe_masks.fit_image(im, W, H, fit, ref_size)
            if neg:
                out = ImageOps.invert(out)
            p = os.path.join(d, f"img_{i:03d}.png")
            out.convert("RGBA").save(p, compress_level=1)
            paths[i] = p
        clear = os.path.join(d, "clear.png")
        Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(clear,
                                                     compress_level=1)
        k = 0
        live_frames = {}
        for i, a, cnt in sched:
            for _ in range(cnt):
                dst = os.path.join(d, f"f_{k:05d}.png")
                src = paths.get(i, clear)
                if i in live:
                    live_frames.setdefault(live[i], []).append(f0 + k)
                wipe_masks._link(src, dst)
                k += 1
        j = k
        while os.path.exists(os.path.join(d, f"f_{j:05d}.png")):
            os.remove(os.path.join(d, f"f_{j:05d}.png"))
            j += 1
        plan.add_input(f"w1d_strobe:{item['id']}", wipe_masks.sequence_input(
            os.path.join(d, "f_%05d.png"), plan.fps3))
        _state(plan)[item["id"]] = {"f0": f0, "n": n, "live": live_frames}
        if missing:
            return ("reduced", "; ".join(missing)[:200])
        return None

    @staticmethod
    def _image(plan, env, src, accent):
        from PIL import Image
        kind = src.get("kind")
        if kind == "colour":
            rgb = wipe_masks.hex_rgb(src.get("colour"), accent)
            return Image.new("RGB", (int(plan.W), int(plan.H)), rgb)
        if kind == "asset":
            path = wipe_masks.asset_local(plan, src.get("asset_key"))
            if not path:
                return None
            return wipe_masks.grab_frame(path, float(src.get("offset_s")
                                                     or 0.0),
                                         max_side=_max_side(plan))
        if kind == "source":
            path = env.get("src_path")
            if not path:
                return None
            return wipe_masks.grab_frame(path, float(src.get("src_t") or 0.0),
                                         max_side=_max_side(plan))
        return None

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        return 300.0 + (f1 - f0) * mpx * 14.0

    def landing(self, plan, item):
        return [fxprims.frame_at(item["start"], plan.fps)]

    def stage(self, ctx, items):
        label = ctx.label
        for it in items:
            mark = len(ctx.parts)
            try:
                label = self._one(ctx, label, it)
            except Exception as e:  # noqa: BLE001 — degrade this strobe
                del ctx.parts[mark:]
                ctx.note(it, "omit", f"compile failed ({type(e).__name__}: "
                         f"{str(e)[:120]})")
        return label

    def _one(self, ctx, label, item):
        st = _state(ctx.plan).get(item["id"])
        lab = ctx.input_label(f"w1d_strobe:{item['id']}")
        if st is None or lab is None:
            ctx.note(item, "omit", "its images were never prepared")
            return label
        f0, n = st["f0"], st["n"]
        # live program frames: time-shifted copies, negated where asked
        for (off, neg), frames in sorted(st["live"].items()):
            spans = fxprims.merge_frame_spans((f, f + 1) for f in frames)
            if off == 0 and not neg:
                continue
            if off == 0:
                out = ctx.uid("stn")
                ctx.parts.append(f"[{label}]negate=enable="
                                 f"'{fxprims.enable_expr(spans)}'[{out}]")
                label = out
                continue
            label = self._shifted(ctx, label, spans, off, neg)
        u = ctx.uid("st")
        ov = fxprims.composite_overlay(ctx.parts, label)
        fmt = "yuva420p"
        # no explicit matrix or range: an explicit one joins the colour
        # negotiation and re-tags (and converts) the whole program
        ctx.parts.append(f"[{lab}]format={fmt},"
                         f"{wipe_masks.pts(ctx.fps3)},trim=end_frame={n}"
                         f"{wipe_masks.repad(f0, ctx.fps, alpha=True)}[{u}w]")
        ctx.parts.append(f"[{label}][{u}w]{ov}:enable="
                         f"'{fxprims.enable_expr([(f0, f0 + n)])}'[{u}]")
        return u

    def _shifted(self, ctx, label, spans, off, neg):
        """The program ``off`` frames away over ``spans`` (a strobe image
        that is the moving picture itself)."""
        u = ctx.uid("sts")
        a0, a1 = spans[0][0], spans[-1][1]
        s0 = max(0, a0 + off)
        lead = s0 - (a0 + off)
        ctx.parts.append(f"[{label}]split[{u}a][{u}b]")
        flat = (",format=yuv420p" if fxprims.composite_overlay(
            ctx.parts, f"{u}a") == fxprims.OVERLAY else "")
        ctx.parts.append(
            f"[{u}b]trim=start_frame={s0}:end_frame={a1 + off},"
            f"setpts=PTS-STARTPTS{',negate' if neg else ''}{flat}"
            f"{wipe_masks.repad(a0 + lead, ctx.fps)}[{u}w]")
        ov = fxprims.composite_overlay(ctx.parts, f"{u}a")
        out = ctx.uid("sts")
        ctx.parts.append(f"[{u}a][{u}w]{ov}:enable="
                         f"'{fxprims.enable_expr(spans)}'[{out}]")
        return out


def _max_side(plan):
    """Decode stills at most twice the program's long side (a 4K clip frame
    for a 480p preview is decoded at 960)."""
    return 2 * max(int(plan.W or 1080), int(plan.H or 1920))


def _state(plan):
    st = plan.__dict__.get("_w1d_strobe")
    if st is None:
        st = {}
        plan.__dict__["_w1d_strobe"] = st
    return st


STROBE = _Strobe()
