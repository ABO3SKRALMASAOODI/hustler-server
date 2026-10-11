"""Subject FX and background operations: the compiler for EDL.fx ``subject``
and ``background`` items (track 1B; worker/devices/INTERFACES_W1.md §3.2).

Everything here is bounded by the person matte M (``fxgraph`` label
``person``, built by track 1A in program space) and the picture P at the
``world`` stage (rides zooms) or the ``screen`` stage (screen-locked):

  fill       the subject a flat colour for 1-N frames, optional decay
  poster     the subject (footage or a flat silhouette) on a flat field
  glow       light around and over the subject: screen(P, tint x glow(M))
  outline    a sticker stroke: dilate(M) - M in a colour
  negate     the picture negated inside (scope subject) or outside M
  exposure   ev / saturation inside or outside M
  isolate    colour kept on one side of M, mono on the other; optionally
             joined (union / intersect) with track 1E's hue key
  echo       copies of the subject from k frames earlier (echo, clone,
             twin, trail), drawn under the subject
  background solid, gradient, blur, radial blur, footage (a shot, an asset,
             a screen recording) or a strobe of plates behind the subject,
             optionally with the subject cut-out frozen on one frame

Contract (INTERFACES_W1.md §1, §3; ROADMAP A3):

* **A device can degrade, never fail a render.** ``prepare`` takes every
  decision: the resolver's rung for a device item, the matte's status
  (``fxgraph.matte_info``), the matte's QA per window, the region and the
  footage inputs. With no matte a kind draws its substitute
  (``full_frame_flash``, ``colour_field_card``, ``halation``,
  ``hue_key_only``, ``flash_junction``) or is omitted, with the reason in the
  render report. A crisp kind (fill, outline, poster) whose matte window
  fails QA demotes to the soft glow there (rung ``reduced``).
* **Exact outside.** Every effect is a window-local layer laid over the
  untouched program through an alpha (the matte, its inverse, a ring, a
  region) and gated on program frames, so pixels outside the effect and
  frames outside the window are the picture's own, bit for bit.
* **No per-pixel expressions.** Masks are reshaped with ``lut`` (a 256-entry
  table), ``gblur``, ``dilation`` and ``blend``; gradients and card masks are
  stills precomputed once in ``prepare``.
* **Frame tags kept.** Every composite is an ``overlay`` onto the program
  stream (the fxprims pattern), so picture-card layout tags survive
  (``KEEPS_FRAME_TAGS``).

Owner rules: nothing here adds a sound or a zoom. Fills, negates, short
posters and plate strobes are flashes for ``flash_safety`` (the schema's
``flash_times``).
"""

import contextvars
import math
import os

import faultinject
import fxgraph
import fxprims

# ffmpeg filters this compiler may emit (fxgraph checks the lane has them;
# a missing one omits the item with the reason, never fails the render).
FILTERS = ("split", "trim", "setpts", "tpad", "overlay", "format",
           "alphamerge", "lut", "lutyuv", "color", "gblur", "dilation",
           "erosion", "blend", "scale", "negate", "hue", "loop", "nullsink",
           "null", "crop", "mix", "fps", "setsar")
# Composites are overlays onto the program stream: frames keep their tags.
KEEPS_FRAME_TAGS = True

TYPES = ("subject", "background")

# The matte is firmed into a crisp alpha with a 256-entry ramp: below LO is
# background, above HI is subject. Soft kinds (negate, exposure, isolate)
# keep a wider ramp so a grade change never shows a hard seam.
FIRM_CRISP = (0.30, 0.70)
FIRM_SOFT = (0.12, 0.88)
# The poster field encroaches this much (px at a 1080-high frame) into the
# subject's soft edge: the footage's own background never fringes the field.
POSTER_CHOKE_PX = 1.5
# Defaults (share of frame height, unless px at a 1080-high frame).
GLOW_RADIUS = 0.04
OUTLINE_WIDTH_PX = 4.0
BLUR_RADIUS = 0.025
RADIAL_RADIUS = 0.08
# Glow shape: the light outside the subject is a sum of gaussian tails
# (sigma as a share of the glow radius, weight); the heaviest tail reaches
# ~0.3 % of full strength at the radius (none beyond it). Inside, a light
# wrap (WRAP x 1 - the tightest tail) on a faint CORE wash.
GLOW_TAIL = ((0.10, 0.30), (0.25, 0.35), (0.38, 0.35))
GLOW_WRAP = 0.9
GLOW_CORE = 0.18
# Glow and blurs are soft by nature: they are computed at a working height
# of about SOFT_LINES (a 1920-line final works at 1/4 resolution, a 480-line
# preview at full), so a preview and its final show the same light and the
# same defocus, and a final pays for 480 lines, not 1920.
SOFT_LINES = 480
RADIAL_COPIES = 8
# A coloured light composites its screened picture through min(1, GAIN x
# light): the RGB round trip only shows where the light itself does.
LIGHT_ALPHA_GAIN = 8
# Echo tint (duotone): tint x clip(LIFT + GAIN x luma).
DUO_LIFT = 70
DUO_GAIN = 1.05
# Exponential fall-off of a fill's last ``decay_f`` frames: weight of the
# k-th decay frame (k = 1..decay_f) is exp(-k / tau), tau in frames.
DECAY_TAU_F = 1.4
# Substitute draws (devices.BUILTIN_SUBSTITUTES).
FLASH_SUB_INTENSITY = 0.6          # fill -> full-frame flash at 0.6
CARD_SCALE = 0.84                  # poster -> colour-field card
CARD_RADIUS = 0.045                # card corner radius, share of width
HALATION_KNEE = (0.62, 0.95)       # glow -> halation: luma knee
# The "accent" colour when neither the captions nor their preset name one
# (captions.DEFAULT_HIGHLIGHT, read lazily).
ACCENT_FALLBACK = "#ffe14d"

# Kinds whose matte edge must read crisp: their window demotes to the glow
# when the matte's QA says the edge chatters there.
CRISP_KINDS = ("fill", "outline", "poster")
# What each subject kind draws when the matte is absent (§3.6).
SUBSTITUTES = {"fill": "full_frame_flash", "poster": "colour_field_card",
               "glow": "halation", "isolate": "hue_key_only"}

# Optional resolver for media keys -> local paths (refbench and tests bind
# it to the asset_locals they render with; production fetches through the
# renderer's own source cache).
ASSET_LOCALS = contextvars.ContextVar("subject_fx_asset_locals",
                                      default=None)

_STATE = "_w1b_state"
# RGB round trips (glow, halation, blurs) convert with accurate rounding: the
# default swscale path drifts flat luma by 2 levels (126 -> 124, measured).
# Colour layers are born RGB: libavfilter's colour-space negotiation then
# converts them with the PROGRAM's own matrix (BT.709 on HD, BT.601 on an
# untagged SD source), so an accent field is the accent on every source.
TO_RGB = "scale=flags=accurate_rnd+full_chroma_int,format=gbrp"
TO_YUV = "scale=flags=accurate_rnd+full_chroma_int,format=yuv420p"
# Frame n at n / fps on a 1 / fps timebase: setpts TRUNCATES its result, and
# N / 30.000 / TB evaluates to 244.99999 for N = 245 (38 of the first 2000
# frames at 30 fps), which stacked two frames on one timestamp and made the
# overlay drop a window frame (measured: a fill's decay frame vanished).
# Every restamp here rounds. (fxprims.repad has the same expression; see
# the 1B report.)


def restamp_expr(fps):
    return f"setpts=round(N/{fxprims.fps3(fps):.3f}/TB)"


def repad(f0, fps, alpha=False):
    """fxprims.repad with a rounded restamp (see restamp_expr)."""
    color = "black@0" if alpha else "black"
    lead = f"start={int(f0)}:" if f0 > 0 else ""
    return (f",tpad={lead}stop=1:start_mode=add:stop_mode=add:color={color},"
            f"{restamp_expr(fps)}")


# ------------------------------------------------------------------ colours
def _hex_rgb(value):
    v = str(value).lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def accent_hex(edl):
    """The Look's accent: the caption style's highlight colour, else its
    preset's, else the house default. Lower-case #rrggbb."""
    caps = (edl or {}).get("captions")
    style = caps.get("style") if isinstance(caps, dict) else None
    style = style if isinstance(style, dict) else {}
    hl = style.get("highlight_color")
    if not hl and style.get("preset"):
        try:
            import captions
            hl = captions._highlight_of(
                captions.PRESETS.get(style["preset"]) or {}, style)
        except Exception:  # noqa: BLE001 — a colour never fails a render
            hl = None
    if not hl:
        try:
            import captions
            hl = captions.DEFAULT_HIGHLIGHT
        except Exception:  # noqa: BLE001
            hl = ACCENT_FALLBACK
    hl = str(hl).strip().lower()
    if not hl.startswith("#"):
        hl = "#" + hl
    try:
        _hex_rgb(hl)
        return hl if len(hl) == 7 else ACCENT_FALLBACK
    except ValueError:
        return ACCENT_FALLBACK


def resolve_colour(value, edl, default="white"):
    """An FxColour (white | black | accent | #rrggbb) as #rrggbb."""
    v = str(value or default).strip().lower()
    if v == "white":
        return "#ffffff"
    if v == "black":
        return "#000000"
    if v == "accent":
        return accent_hex(edl)
    return v


def rgb_to_yuv(rgb, height=1080):
    """Limited-range 8-bit Y'CbCr of an sRGB triple, BT.709 (``height`` >=
    600) or BT.601: the values libavfilter's colour-space negotiation gives
    a colour layer on a BT.709-tagged program or an untagged one (the
    compiler itself lets ffmpeg convert; tests compare against this)."""
    r, g, b = (float(c) / 255.0 for c in rgb)
    if height >= 600:
        kr, kb = 0.2126, 0.0722
    else:
        kr, kb = 0.299, 0.114
    kg = 1.0 - kr - kb
    y = kr * r + kg * g + kb * b
    cb = (b - y) / (2.0 * (1.0 - kb))
    cr = (r - y) / (2.0 * (1.0 - kr))
    Y = int(round(16 + 219 * y))
    U = int(round(128 + 224 * cb))
    V = int(round(128 + 224 * cr))
    clamp = lambda x, lo, hi: max(lo, min(hi, x))  # noqa: E731
    return clamp(Y, 16, 235), clamp(U, 16, 240), clamp(V, 16, 240)


# ------------------------------------------------------------------ state
def _state(plan):
    return plan.__dict__.setdefault(_STATE, {})


def soft_scale(H, reduced=False):
    """The downscale a soft effect works at for a frame H lines high."""
    s = max(1, int(round(float(H or SOFT_LINES) / SOFT_LINES)))
    return s * 2 if reduced else s


def plan_edl(plan):
    """The EDL this plan renders (colour roles such as ``accent`` resolve
    from its captions). ``plan.edl`` when the plan carries it; else read
    once from fxgraph.prepare's own frame (the plan does not keep the EDL
    in Wave 1); {} when neither is there."""
    d = plan.__dict__
    for key in ("edl", "_w1b_edl"):
        if isinstance(d.get(key), dict):
            return d[key]
    found = None
    try:
        import sys
        f = sys._getframe(1)
        code = fxgraph.prepare.__code__
        while f is not None:
            if f.f_code is code:
                found = f.f_locals.get("edl")
                break
            f = f.f_back
    except Exception:  # noqa: BLE001 — a colour never fails a render
        found = None
    d["_w1b_edl"] = found if isinstance(found, dict) else {}
    return d["_w1b_edl"]


def _get(item, key, default=None):
    v = item.get(key)
    return default if v is None else v


def _span_frames(plan, item):
    return fxprims.frame_span(item["start"], item["end"], plan.fps)


def _overlapping(windows, t0, t1):
    out = []
    for w in windows or []:
        try:
            if float(w.get("t1", 0)) > t0 and float(w.get("t0", 0)) < t1:
                out.append(w)
        except (TypeError, ValueError):
            continue
    return out


def _cuts(plan):
    """Program frames where a new block starts (keep spans and inserts)."""
    tl = plan.tl
    out = set()
    try:
        for o in list(getattr(tl, "offsets", []) or [])[1:]:
            out.add(fxprims.frame_at(o, plan.fps))
        for at, d in (tl.insert_positions() or []):
            out.add(fxprims.frame_at(at, plan.fps))
            out.add(fxprims.frame_at(at + d, plan.fps))
    except Exception:  # noqa: BLE001
        pass
    return sorted(f for f in out if 0 < f < plan.n_frames)


# ------------------------------------------------------------------ media
def asset_path(plan, key):
    """A local path (or ranged URL) ffmpeg can read for media ``key``."""
    locs = ASSET_LOCALS.get()
    if locs and key in locs and os.path.exists(locs[key]):
        return locs[key]
    import renderer
    return renderer._render_asset_source(key, "fx", 0, plan.workdir)


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def _source_plan(plan, item, src, n, tag):
    """How one FxSource becomes a window-local stream: a dict the emitter
    reads, or (None, reason) when this render cannot draw it."""
    kind = src.get("kind")
    f0, f1 = _span_frames(plan, item)
    if kind == "colour":
        return {"kind": "colour", "colour": src.get("colour")}, None
    if kind == "program":
        off = int(round(float(src.get("offset_s") or 0.0) * plan.fps3))
        return {"kind": "program", "from": f0 + off}, None
    if kind == "source":
        t = None
        try:
            t = plan.tl.src_to_out(float(src["src_t"]))
        except Exception:  # noqa: BLE001
            t = None
        if t is None:
            return None, (f"source second {float(src['src_t']):g} is not in "
                          "this program (fx compilers read the program, not "
                          "the main video's cut-away footage)")
        return {"kind": "program",
                "from": fxprims.frame_at(t, plan.fps)}, None
    if kind in ("next", "previous"):
        cuts = _cuts(plan)
        if kind == "next":
            after = [c for c in cuts if c >= f0]
            if not after:
                return None, "no cut after this window for a next-shot fill"
            return {"kind": "freeze", "frame": after[0]}, None
        before = [c for c in cuts if c <= f1]
        if not before:
            return None, "no cut before this window for a previous-shot fill"
        return {"kind": "freeze", "frame": before[-1] - 1}, None
    if kind == "asset":
        key = src.get("asset_key")
        try:
            path = asset_path(plan, key)
        except Exception as e:  # noqa: BLE001 — degrade, never fail
            return None, f"media {key} could not be read ({str(e)[:80]})"
        if not path:
            return None, f"media {key} could not be read"
        dur = n / plan.fps3 + 1.0
        off = max(0.0, float(src.get("offset_s") or 0.0))
        ikey = f"w1b:{item['id']}:{tag}"
        if os.path.splitext(str(key))[1].lower() in _IMAGE_EXTS:
            args = ["-loop", "1", "-framerate", f"{plan.fps3:.3f}", "-t",
                    f"{dur:.3f}", "-i", path]
        else:
            args = (["-stream_loop", "-1"] if src.get("loop") else []) + [
                "-ss", f"{off:.3f}", "-t", f"{dur:.3f}", "-i", path]
        plan.add_input(ikey, args)
        return {"kind": "input", "key": ikey}, None
    return None, f"unknown source kind {kind!r}"


# ------------------------------------------------------------------ prepare
def _precompute_guard():
    """The ``oom`` fault takes the path a real OOM-killed precompute takes
    (INTERFACES.md §6.2): the item is omitted, the render goes on."""
    if faultinject.fault("oom:fx_precompute"):
        raise MemoryError("fx precompute was OOM-killed")


def _demote_crisp(info, item):
    t0, t1 = float(item["start"]), float(item["end"])
    bad = [w for w in _overlapping(info.get("windows"), t0, t1)
           if w.get("ok") is False]
    return bad


def _partial(info, item):
    t0, t1 = float(item["start"]), float(item["end"])
    return [w for w in _overlapping(info.get("windows"), t0, t1)
            if w.get("status") not in (None, "ready")]


class _Compiler:
    """fxgraph compiler for the ``subject`` and ``background`` types."""

    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    # -- prepare ---------------------------------------------------------
    def prepare(self, plan, item):
        if not plan.W or not plan.H or not plan.workdir:
            return ("omit", "no workdir/geometry for a subject effect")
        st = {"mode": "full", "kind": item.get("kind"),
              "space": "screen" if item.get("stage") == "screen"
              else "world"}
        _state(plan)[item["id"]] = st
        plan_edl(plan)
        d = plan.devices.get(item.get("id")) or {}
        st["reduced"] = d.get("rung") == "reduced"
        sub = d.get("substitute") if d.get("rung") == "substitute" else None
        if sub:
            return self._prepare_substitute(plan, item, st, sub, None)
        info = fxgraph.matte_info(plan, "person")
        status = (info or {}).get("status")
        if status not in ("ready", "partial"):
            reason = (info or {}).get("reason") or "no person matte"
            sub = self._substitute_for(item)
            if sub:
                return self._prepare_substitute(plan, item, st, sub, reason)
            return ("omit", reason)
        got = self._prepare_region(plan, item, st)
        if got:
            return got
        try:
            got = (self._prepare_background(plan, item, st)
                   if item.get("type") == "background"
                   else self._prepare_subject(plan, item, st))
        except Exception as e:  # noqa: BLE001 — degrade, never fail
            return ("omit", f"could not be prepared ({type(e).__name__}: "
                    f"{str(e)[:120]})")
        if got and got[0] == "omit":
            return got
        fxgraph.request_matte(plan, item, "person", st["space"])
        rung = got
        missing = _partial(info, item)
        if missing and not rung:
            rung = ("reduced", "the person matte is missing for part of "
                    "this window (that part shows the plain picture)")
        if item.get("type") == "subject" and item.get("kind") in CRISP_KINDS:
            bad = _demote_crisp(info, item)
            if bad:
                st["mode"] = "soft"
                rung = ("reduced", "the matte edge is unsteady here (QA "
                        "over the bar): the crisp effect is drawn as a glow")
        return rung

    @staticmethod
    def _substitute_for(item):
        if item.get("type") == "background":
            return "flash_junction" if item.get("hold_subject") else None
        kind = item.get("kind")
        sub = SUBSTITUTES.get(kind)
        if sub == "hue_key_only":
            hue = str(item.get("hue") or "")
            if not hue.startswith("#") or fxgraph.peer_fn(
                    fxgraph.MASK_PEERS.get("hue")) is None:
                return None
        return sub

    def _prepare_substitute(self, plan, item, st, sub, reason):
        st["mode"] = "substitute"
        st["substitute"] = sub
        if sub == "colour_field_card":
            self._card_mask(plan, item)
        elif sub == "hue_key_only":
            hue = str(item.get("hue") or "")
            if not hue.startswith("#") or fxgraph.peer_fn(
                    fxgraph.MASK_PEERS.get("hue")) is None:
                return ("omit", "no matte and no hue key for the isolate")
        elif sub not in ("full_frame_flash", "halation", "flash_junction"):
            return ("omit", f"substitute {sub} is not drawn by this "
                    "compiler")
        if reason is None:
            return None              # the resolver already chose and said why
        return ("substitute", f"{reason} -> {sub}")

    @staticmethod
    def _card_mask(plan, item):
        _precompute_guard()
        from PIL import Image, ImageDraw
        W, H = int(plan.W), int(plan.H)
        cw, ch = int(round(W * CARD_SCALE / 2)) * 2, \
            int(round(H * CARD_SCALE / 2)) * 2
        im = Image.new("L", (cw, ch), 0)
        r = int(round(CARD_RADIUS * W))
        ImageDraw.Draw(im).rounded_rectangle([0, 0, cw - 1, ch - 1],
                                             radius=r, fill=255)
        path = os.path.join(plan.workdir, f"w1b_card_{item['id']}.png")
        im.save(path)
        plan.add_input(f"w1b:{item['id']}:card", [
            "-loop", "1", "-framerate", f"{plan.fps3:.3f}", "-t",
            f"{plan.program_s + 1.0:.3f}", "-i", path])

    @staticmethod
    def _prepare_region(plan, item, st):
        region = item.get("region")
        if not region:
            return None
        import regions
        got = regions.prepare(plan, item, region)
        if got:
            rung, reason = got
            # a region that cannot be drawn would let the effect spill
            return ("omit", f"its region could not be drawn ({reason})")
        st["region"] = True
        return None

    def _prepare_subject(self, plan, item, st):
        kind = item.get("kind")
        if kind == "isolate":
            hue = str(item.get("hue") or "")
            if hue == "auto":
                st["hue"] = None
                return ("reduced", "hue 'auto' was never solved for this "
                        "item: the subject alone keeps its colour")
            if hue.startswith("#"):
                if fxgraph.peer_fn(fxgraph.MASK_PEERS.get("hue")) is None:
                    st["hue"] = None
                    return ("reduced", "the hue key (track 1E) is not on "
                            "this lane: the subject alone keeps its colour")
                st["hue"] = hue
        if kind == "echo":
            echo = item.get("echo") or {}
            offs = sorted({int(k) for k in echo.get("offsets_f") or []})
            if not offs:
                return ("omit", "an echo needs offsets")
            st["offsets"] = offs
        return None

    def _prepare_background(self, plan, item, st):
        kind = item.get("kind")
        f0, f1 = _span_frames(plan, item)
        n = f1 - f0
        if kind == "gradient":
            st["gradient"] = self._gradient(plan, item)
        if kind == "source":
            got, why = _source_plan(plan, item, item.get("source") or {}, n,
                                    "src0")
            if got is None:
                return ("omit", why)
            st["sources"] = [got]
        if kind == "strobe":
            plans, dropped = [], []
            for i, src in enumerate(item.get("sources") or []):
                got, why = _source_plan(plan, item, src, n, f"src{i}")
                if got is None:
                    dropped.append(why)
                else:
                    plans.append(got)
            if len(plans) < 1:
                return ("omit", "; ".join(dropped)[:200] or "no plates")
            st["sources"] = plans
            if dropped:
                return ("reduced", f"{len(dropped)} plate(s) dropped: "
                        + dropped[0][:120])
        if item.get("hold_subject"):
            ht = item.get("hold_t")
            h = fxprims.frame_at(float(item["start"] if ht is None else ht),
                                 plan.fps)
            st["hold"] = max(0, min(plan.n_frames - 1, h))
        return None

    @staticmethod
    def _gradient(plan, item):
        """A W x H gradient still (colour -> colour2 along ``angle``, CSS
        convention: 180 = top to bottom), dithered so 8-bit bands never
        show, written once and looped as an input."""
        _precompute_guard()
        import numpy as np
        from PIL import Image
        W, H = int(plan.W), int(plan.H)
        edl = plan_edl(plan)
        c1 = np.array(_hex_rgb(resolve_colour(item.get("colour"), edl)),
                      np.float32)
        c2 = np.array(_hex_rgb(resolve_colour(item.get("colour2"), edl)),
                      np.float32)
        ang = math.radians(float(item.get("angle")
                                 if item.get("angle") is not None else 180))
        dx, dy = math.sin(ang), -math.cos(ang)
        ys, xs = np.mgrid[0:H, 0:W].astype(np.float32)
        p = (xs - W / 2.0) * dx + (ys - H / 2.0) * dy
        half = abs(W / 2.0 * dx) + abs(H / 2.0 * dy)
        t = np.clip((p / max(half, 1.0) + 1.0) / 2.0, 0.0, 1.0)[..., None]
        rng = np.random.default_rng(int(item.get("seed") or 0))
        img = c1 * (1.0 - t) + c2 * t
        img += rng.uniform(-0.5, 0.5, img.shape).astype(np.float32)
        img = np.clip(np.round(img), 0, 255).astype(np.uint8)
        path = os.path.join(plan.workdir, f"w1b_grad_{item['id']}.png")
        Image.fromarray(img, "RGB").save(path)
        key = f"w1b:{item['id']}:grad"
        plan.add_input(key, ["-loop", "1", "-framerate", f"{plan.fps3:.3f}",
                             "-t", f"{plan.program_s + 1.0:.3f}", "-i",
                             path])
        return key

    # -- report hooks ----------------------------------------------------
    def cost_ms(self, plan, item):
        f0, f1 = _span_frames(plan, item)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        if item.get("type") == "background":
            a, b = BACKGROUND_COST.get(item.get("kind"), (3.0, 12.0))
        else:
            a, b = SUBJECT_COST.get(item.get("kind"), (2.0, 10.0))
            if item.get("kind") == "echo":
                n = len((item.get("echo") or {}).get("offsets_f") or [1])
                a, b = a * (n + 1) / 2.0, b * (n + 1) / 2.0
        return FIXED_MS + (f1 - f0) * (a + b * mpx)

    def landing(self, plan, item):
        f0, _f1 = _span_frames(plan, item)
        return [f0]

    # -- emit ------------------------------------------------------------
    def stage(self, ctx, items):
        label = ctx.label
        edl_cache = plan_edl(ctx.plan)
        for it in sorted(items, key=lambda i: (float(i["start"]), i["id"])):
            st = _state(ctx.plan).get(it["id"]) or {"mode": "full"}
            ctx.label = label
            em = _Emit(ctx, it, st, edl_cache)
            label = em.run(label)
        ctx.label = label
        return label


# Measured costs (tests/w1b cost bench, Apple M5 Pro under load): per
# device frame, ms = A + B x megapixels of the program frame, plus a fixed
# FIXED_MS per device. The cards carry the per-lane figures the resolver
# spends; these feed the render report's est_ms.
FIXED_MS = 60.0
SUBJECT_COST = {"fill": (2.7, 2.6), "poster": (0.7, 17.5),
                "glow": (6.9, 31.4), "outline": (2.5, 13.3),
                "negate": (1.6, 7.4), "exposure": (1.6, 7.4),
                "isolate": (1.6, 7.4), "echo": (2.2, 12.2)}
BACKGROUND_COST = {"solid": (2.8, 10.0), "gradient": (2.8, 10.0),
                   "source": (2.8, 10.0), "strobe": (2.8, 12.0),
                   "blur": (9.2, 44.8), "radial_blur": (14.7, 60.0)}


class _Emit:
    """Emits one item: builds window-local layers and lays them over the
    running program label."""

    def __init__(self, ctx, item, st, edl):
        self.ctx, self.it, self.st = ctx, item, st
        self.p = ctx.parts
        self.W, self.H = int(ctx.W), int(ctx.H)
        self.F = ctx.fps3
        self.f0, self.f1 = ctx.frames(item)
        self.n = self.f1 - self.f0
        self.u = ctx.uid("sx")
        self.edl = edl or {}
        self._k = 0

    # -- naming ----------------------------------------------------------
    def lab(self, tag):
        self._k += 1
        return f"{self.u}{tag}{self._k}"

    @property
    def restamp(self):
        return restamp_expr(self.F)

    def scale_px(self, px):
        return float(px) * self.H / 1080.0

    # -- dispatch --------------------------------------------------------
    def run(self, label):
        st, it = self.st, self.it
        if st.get("mode") == "substitute":
            return getattr(self, "sub_" + st["substitute"])(label)
        if it.get("type") == "background":
            return self.background(label)
        kind = "glow" if st.get("mode") == "soft" else it.get("kind")
        return getattr(self, "k_" + kind)(label)

    # -- building blocks -------------------------------------------------
    def colour_layer(self, hexcol, n=None):
        """A flat window-local colour stream, born RGB (the program's own
        matrix converts it: see TO_RGB)."""
        r, g, b = _hex_rgb(hexcol)
        out = self.lab("c")
        self.p.append(f"color=c=0x{r:02x}{g:02x}{b:02x}:s={self.W}x{self.H}"
                      f":r={self.F:.3f},trim=end_frame={n or self.n},"
                      f"{self.restamp}[{out}]")
        return out

    def const_alpha(self, value, n=None):
        out = self.lab("k")
        self.p.append(f"color=c=black:s={self.W}x{self.H}:r={self.F:.3f},"
                      f"trim=end_frame={n or self.n},format=gray,"
                      f"lut=c0={int(round(max(0, min(255, value))))},"
                      f"{self.restamp}[{out}]")
        return out

    def window_pic(self, label, f0=None, f1=None):
        """(main, picture of [f0, f1) as a window-local stream)."""
        f0 = self.f0 if f0 is None else f0
        f1 = self.f1 if f1 is None else f1
        a, b, w = self.lab("a"), self.lab("b"), self.lab("p")
        self.p.append(f"[{label}]split[{a}][{b}]")
        self.p.append(f"[{b}]trim=start_frame={f0}:end_frame={f1},"
                      f"{self.restamp}[{w}]")
        return a, w

    def split(self, label, n):
        if n == 1:
            return [label]
        outs = [self.lab("s") for _ in range(n)]
        self.p.append(f"[{label}]split={n}" + "".join(f"[{o}]" for o in outs))
        return outs

    def raw_mask(self):
        m = self.ctx.mask("person")
        if m is None:
            raise RuntimeError("the person matte vanished at emit")
        return m

    def firm_expr(self, firm, invert=False, scale=1.0):
        lo, hi = (int(round(v * 255)) for v in firm)
        k = 255.0 / max(1, hi - lo)
        base = f"clip((val-{lo})*{k:.5f},0,255)"
        if invert:
            base = f"(255-{base})"
        if abs(scale - 1.0) > 1e-6:
            base = f"{base}*{scale:.5f}"
        return f"lut=c0='{base}'"

    def mask_window(self, raw, f0=None, f1=None, *, firm=FIRM_CRISP,
                    invert=False, grow_px=0.0, intensity=1.0, decay=None,
                    region=True):
        """The trimmed, firmed, reshaped window-local alpha of ``raw``."""
        f0 = self.f0 if f0 is None else f0
        f1 = self.f1 if f1 is None else f1
        chain = [f"trim=start_frame={max(0, f0)}:end_frame={f1}",
                 self.restamp, "format=gray"]
        if f0 < 0:
            chain.append(f"tpad=start={-f0}:start_mode=clone")
            chain.append(self.restamp)
        grow = self.scale_px(grow_px)
        if grow:
            passes = int(round(abs(grow)))
            if passes:
                chain += (["dilation"] if grow > 0 else ["erosion"]) * \
                    min(passes, 32)
        chain.append(self.firm_expr(firm, invert,
                                    max(0.0, min(1.0, float(intensity)))))
        for k, w in (decay or []):
            chain.append(f"lut=c0='val*{w:.5f}':enable='eq(n,{k})'")
        out = self.lab("m")
        self.p.append(f"[{raw}]{','.join(chain)}[{out}]")
        if region and self.st.get("region"):
            out = self.intersect_region(out)
        return out

    def intersect_region(self, alpha):
        import regions
        # regions.pad hands out one pad per (item, key) per PLAN, but the
        # renderer builds the graph twice (a dialogue-gain measuring pass,
        # then the real one): release this item's key for this build.
        spent = self.ctx.plan.__dict__.get("_regions_spent")
        if spent:
            spent.discard(regions._input_key(self.it, "region"))
        rp = regions.pad(self.ctx, self.it, self.it.get("region"))
        if rp is None:
            raise RuntimeError("its region could not be drawn")
        rt, out = self.lab("r"), self.lab("ri")
        self.p.append(f"[{rp}]trim=start_frame={self.f0}:end_frame={self.f1},"
                      f"{self.restamp},format=gray[{rt}]")
        self.p.append(f"[{alpha}][{rt}]blend=all_mode=multiply[{out}]")
        return out

    def region_alpha(self):
        """The item's region alone as a window-local alpha (or None)."""
        if not self.st.get("region"):
            return None
        return self.intersect_region(self.const_alpha(255))

    def lay(self, base, layer, alpha=None):
        """``layer`` (window-local) over ``base`` inside the window, through
        ``alpha`` (window-local gray) or opaque. Outside the alpha and the
        window the program is untouched."""
        ov = fxprims.composite_overlay(self.p, base)
        plain = ov == fxprims.OVERLAY
        w, out = self.lab("w"), self.lab("o")
        if alpha is not None:
            lf = self.lab("lf")
            self.p.append(f"[{layer}]{'format=yuva420p' if plain else 'null'}"
                          f"[{lf}]")
            self.p.append(f"[{lf}][{alpha}]alphamerge"
                          f"{repad(self.f0, self.F, alpha=True)}[{w}]")
        else:
            self.p.append(f"[{layer}]{'format=yuv420p' if plain else 'null'}"
                          f"{repad(self.f0, self.F)}[{w}]")
        expr = fxprims.enable_expr([(self.f0, self.f1)])
        self.p.append(f"[{base}][{w}]{ov}:enable='{expr}'[{out}]")
        return out

    def decay_weights(self):
        """[(window frame, weight)] for a fill's decay frames."""
        k = int(_get(self.it, "decay_f", 0))
        if k <= 0 or self.n < 2:
            return []
        k = min(k, self.n - 1)
        out = []
        for j in range(1, k + 1):
            out.append((self.n - k - 1 + j, math.exp(-j / DECAY_TAU_F)))
        return out

    def colour(self, key="colour", default="white"):
        return resolve_colour(self.it.get(key), self.edl, default)

    # -- subject kinds ---------------------------------------------------
    def k_fill(self, label):
        a = self.mask_window(self.raw_mask(),
                             intensity=_get(self.it, "intensity", 1.0),
                             decay=self.decay_weights())
        return self.lay(label, self.colour_layer(self.colour()), a)

    def k_poster(self, label):
        it = self.it
        field = self.colour_layer(resolve_colour(it.get("field"), self.edl,
                                                 "accent"))
        inten = float(_get(it, "intensity", 1.0))
        if it.get("keep_footage", True):
            a = self.mask_window(self.raw_mask(), invert=True,
                                 grow_px=-POSTER_CHOKE_PX, intensity=inten)
            return self.lay(label, field, a)
        sil = self.mask_window(self.raw_mask(), region=False)
        col = self.colour_layer(self.colour("colour", "black"))
        cf, layer = self.lab("cf"), self.lab("pl")
        self.p.append(f"[{col}]format=yuva420p[{cf}a]")
        self.p.append(f"[{cf}a][{sil}]alphamerge[{cf}]")
        self.p.append(f"[{field}][{cf}]overlay=format=auto,"
                      f"format=yuv420p[{layer}]")
        alpha = None
        if self.st.get("region"):
            alpha = self.region_alpha()
            if inten < 1.0:
                s = self.lab("ia")
                self.p.append(f"[{alpha}]lut=c0='val*{inten:.5f}'[{s}]")
                alpha = s
        elif inten < 1.0:
            alpha = self.const_alpha(255 * inten)
        return self.lay(label, layer, alpha)

    def glow_alpha(self, raw, radius, intensity, region=True):
        """The glow's window-local light (gray) from the matte, shaped like
        real bloom: outside the subject it starts at full strength on the
        edge and falls off monotonically as a sum of three gaussians (a
        heavy tail, never a band), dying out within ``radius`` (share of
        frame height) of the edge; inside, the light wraps the edge and
        washes the subject faintly (``GLOW_CORE``). Computed at
        the soft working height (soft_scale): bloom is soft by nature."""
        s = soft_scale(self.H, self.st.get("reduced"))
        w, h = max(2, self.W // s // 2 * 2), max(2, self.H // s // 2 * 2)
        r = radius * self.H / s                   # radius in low-res px
        L = self.lab
        m, out = L("gm"), L("go")
        sig = [max(0.5, k * r) for k, _w in GLOW_TAIL]
        self.p.append(f"[{raw}]trim=start_frame={self.f0}:end_frame="
                      f"{self.f1},{self.restamp},format=gray,"
                      f"{self.firm_expr(FIRM_CRISP)},"
                      f"scale={w}:{h}:flags=area,split=5"
                      + "".join(f"[{m}{i}]" for i in range(5)))
        tails = []
        for i, ((_k, wt), sg) in enumerate(zip(GLOW_TAIL, sig)):
            t = L("gt")
            # 2 x weight: a blurred step is 0.5 on the edge itself
            self.p.append(f"[{m}{i}]gblur=sigma={sg:.3f},"
                          f"lut=c0='clip(val*{2 * wt:.4f},0,255)'"
                          + (f",split[{t}][{t}w]" if i == 0 else "")
                          + (f"[{t}]" if i else ""))
            tails.append(t)
        tail = tails[0]
        for t in tails[1:]:
            nx = L("ga")
            self.p.append(f"[{tail}][{t}]blend=all_mode=addition[{nx}]")
            tail = nx
        outside, inside, wrap = L("gx"), L("gy"), L("gw")
        self.p.append(f"[{m}3]negate[{m}n]")
        self.p.append(f"[{tail}][{m}n]blend=all_mode=multiply[{outside}]")
        # light wrap: just inside the edge the subject catches the light
        self.p.append(f"[{tails[0]}w]negate,lut=c0='clip(val*{GLOW_WRAP:.4f}"
                      f"+{GLOW_CORE * 255:.1f},0,255)'[{wrap}]")
        self.p.append(f"[{wrap}][{m}4]blend=all_mode=multiply[{inside}]")
        inten = max(0.0, min(1.0, float(intensity)))
        self.p.append(f"[{outside}][{inside}]blend=all_mode=addition,"
                      f"scale={self.W}:{self.H}:flags=bicubic,"
                      f"lut=c0='val*{inten:.5f}'[{out}]")
        if region and self.st.get("region"):
            out = self.intersect_region(out)
        return out

    def screen_light(self, label, light_alpha, tint):
        """screen(P, tint x light) inside the window.

        A white light is exactly a lerp to white (screen(P, L) = P + L x
        (1 - P)), which is linear in Y'CbCr too: it is laid as white through
        the light as alpha, with no colour round trip at all. A coloured
        light is screened in RGB and laid back through a soft alpha
        (min(1, 8 L)), so where the light is faint the RGB round trip's
        chroma-edge rounding cannot show; where there is no light the
        program is untouched."""
        if str(tint).lower() == "#ffffff":
            return self.lay(label, self.colour_layer("#ffffff"), light_alpha)
        main, pw = self.window_pic(label)
        la, lb = self.lab("la"), self.lab("lb")
        self.p.append(f"[{light_alpha}]split[{la}][{lb}]")
        tl, tg, pg, sc = (self.lab("tl"), self.lab("tg"), self.lab("pg"),
                          self.lab("sc"))
        r, g, b = _hex_rgb(tint)
        self.p.append(f"color=c=0x{r:02x}{g:02x}{b:02x}:s={self.W}x{self.H}"
                      f":r={self.F:.3f},trim=end_frame={self.n},format=gbrp,"
                      f"{self.restamp}[{tg}]")
        self.p.append(f"[{la}]format=gbrp[{tl}g]")
        self.p.append(f"[{tg}][{tl}g]blend=all_mode=multiply[{tl}]")
        self.p.append(f"[{pw}]{TO_RGB}[{pg}]")
        self.p.append(f"[{pg}][{tl}]blend=all_mode=screen,{TO_YUV}[{sc}]")
        sup = self.lab("su")
        self.p.append(f"[{lb}]lut=c0='min(255,val*{LIGHT_ALPHA_GAIN})'"
                      f"[{sup}]")
        return self.lay(main, sc, sup)

    def k_glow(self, label):
        it = self.it
        if self.st.get("mode") == "soft" and it.get("kind") != "glow":
            # a crisp kind demoted: its own colour, a tight radius
            tint = self.colour("colour", "white")
            radius = 0.012
        else:
            tint = self.colour("colour", "white")
            radius = float(_get(it, "radius", GLOW_RADIUS))
        light = self.glow_alpha(self.raw_mask(), radius,
                                _get(it, "intensity", 1.0))
        return self.screen_light(label, light, tint)

    def k_outline(self, label):
        it = self.it
        width = self.scale_px(float(_get(it, "width_px", OUTLINE_WIDTH_PX)))
        raw = self.raw_mask()
        ra, rb = self.lab("oa"), self.lab("ob")
        self.p.append(f"[{raw}]split[{ra}][{rb}]")
        inner = self.mask_window(ra, region=False)
        # rounded dilation: blur the matte, then a threshold ramp at the
        # 5 % level sits ~1.65 sigma outside the edge
        sig = max(0.5, width / 1.645)
        outer = self.lab("od")
        self.p.append(f"[{rb}]trim=start_frame={self.f0}:end_frame={self.f1},"
                      f"{self.restamp},format=gray,"
                      f"{self.firm_expr(FIRM_CRISP)},gblur=sigma={sig:.3f},"
                      f"lut=c0='clip((val-8)*12.75,0,255)'[{outer}]")
        ring = self.lab("or")
        inten = max(0.0, min(1.0, float(_get(it, "intensity", 1.0))))
        self.p.append(f"[{outer}][{inner}]blend=all_mode=subtract,"
                      f"lut=c0='val*{inten:.5f}'[{ring}]")
        if self.st.get("region"):
            ring = self.intersect_region(ring)
        return self.lay(label, self.colour_layer(self.colour()), ring)

    def side_alpha(self, firm=FIRM_SOFT):
        """Alpha of the side ``scope`` names (subject or background)."""
        return self.mask_window(
            self.raw_mask(), firm=firm,
            invert=self.it.get("scope") == "background",
            intensity=_get(self.it, "intensity", 1.0))

    def k_negate(self, label):
        a = self.side_alpha(FIRM_CRISP)
        main, pw = self.window_pic(label)
        neg = self.lab("ng")
        self.p.append(f"[{pw}]negate[{neg}]")
        return self.lay(main, neg, a)

    def k_exposure(self, label):
        it = self.it
        ev = float(_get(it, "ev", 0.0))
        sat = float(_get(it, "saturation", 1.0))
        gain = 2.0 ** (ev / 2.2)
        cg = sat * min(1.0, gain) if ev < 0 else sat
        a = self.side_alpha()
        main, pw = self.window_pic(label)
        ex = self.lab("ex")
        self.p.append(f"[{pw}]lutyuv=y='clip(16+(val-16)*{gain:.5f},16,235)'"
                      f":u='clip(128+(val-128)*{cg:.5f},16,240)'"
                      f":v='clip(128+(val-128)*{cg:.5f},16,240)'[{ex}]")
        return self.lay(main, ex, a)

    def k_isolate(self, label):
        it = self.it
        # colour is kept on ``scope``; the other side goes mono
        mono_side_bg = it.get("scope", "subject") == "subject"
        a = self.mask_window(self.raw_mask(), firm=FIRM_SOFT,
                             invert=mono_side_bg,
                             intensity=_get(it, "intensity", 1.0),
                             region=False)
        hue = self.st.get("hue")
        if hue:
            self.ctx.label = label
            hm = self.ctx.mask(f"hue:{hue.lstrip('#')}")
            label = self.ctx.label        # the provider may rebind it
            if hm is not None:
                ht = self.lab("ht")
                self.p.append(f"[{hm}]trim=start_frame={self.f0}:end_frame="
                              f"{self.f1},{self.restamp},format=gray,"
                              f"negate[{ht}]")
                op = it.get("hue_op") or "union"
                # mono where NOT kept. union: kept = side | hue -> mono =
                # min(not side, not hue); intersect: kept = side & hue ->
                # mono = max(not side, not hue)
                mode = "darken" if op == "union" else "lighten"
                j = self.lab("hj")
                self.p.append(f"[{a}][{ht}]blend=all_mode={mode}[{j}]")
                a = j
        if self.st.get("region"):
            a = self.intersect_region(a)
        main, pw = self.window_pic(label)
        mono = self.lab("mn")
        self.p.append(f"[{pw}]hue=s=0[{mono}]")
        return self.lay(main, mono, a)

    def k_echo(self, label):
        """Copies of the subject from k frames earlier, under the subject.

        One picture branch and one matte branch cover program frames
        [f0 - lead, f1) (the first frame repeated before frame 0), so every
        copy k is a trim of the same two streams: frames [lead - k,
        lead - k + n). Oldest copy first, nearest last, the live subject on
        top."""
        it = self.it
        echo = it.get("echo") or {}
        mode = echo.get("mode") or "echo"
        offs = self.st.get("offsets") or sorted(
            {int(k) for k in echo.get("offsets_f") or []})
        if self.st.get("reduced"):
            offs = offs[:2]
        op = float(echo.get("opacity") if echo.get("opacity") is not None
                   else 0.6)
        if mode == "twin" and echo.get("opacity") is None:
            op = 1.0
        decay = float(echo.get("decay") or 0.0)
        tint = echo.get("tint")
        inten = max(0.0, min(1.0, float(_get(it, "intensity", 1.0))))
        k_n = len(offs)
        lead = max(offs)
        a0 = self.f0 - lead
        pre = max(0, -a0)
        main, pic = self.window_pic(label, max(0, a0), self.f1)
        mraw = self.lab("em")
        self.p.append(f"[{self.raw_mask()}]trim=start_frame={max(0, a0)}:"
                      f"end_frame={self.f1},{self.restamp},format=gray,"
                      f"{self.firm_expr(FIRM_CRISP)}[{mraw}]")
        if pre:
            q, mq = self.lab("pq"), self.lab("mq")
            self.p.append(f"[{pic}]tpad=start={pre}:start_mode=clone,"
                          f"{self.restamp}[{q}]")
            self.p.append(f"[{mraw}]tpad=start={pre}:start_mode=clone,"
                          f"{self.restamp}[{mq}]")
            pic, mraw = q, mq
        pics = self.split(pic, k_n + 2)
        masks = self.split(mraw, k_n + 1)

        def window_of(src, start, extra=""):
            out = self.lab("et")
            self.p.append(f"[{src}]trim=start_frame={start}:end_frame="
                          f"{start + self.n},{self.restamp}{extra}[{out}]")
            return out

        canvas = window_of(pics[0], lead)
        ranked = sorted(offs)
        for i, k in enumerate(sorted(offs, reverse=True)):
            w = op * ((1.0 - decay) ** ranked.index(k)) * inten
            cp = window_of(pics[i + 1], lead - k)
            if tint:
                cp = self.duotone(cp, resolve_colour(tint, self.edl))
            am = window_of(masks[i + 1], lead - k, f",lut=c0='val*{w:.5f}'")
            ca, nxt = self.lab("ea"), self.lab("ec")
            self.p.append(f"[{cp}]format=yuva420p[{ca}x]")
            self.p.append(f"[{ca}x][{am}]alphamerge[{ca}]")
            self.p.append(f"[{canvas}][{ca}]overlay=format=auto,"
                          f"format=yuv420p[{nxt}]")
            canvas = nxt
        sp = window_of(pics[k_n + 1], lead, ",format=yuva420p")
        sm = window_of(masks[0], lead)
        cur, res = self.lab("es"), self.lab("er")
        self.p.append(f"[{sp}][{sm}]alphamerge[{cur}]")
        self.p.append(f"[{canvas}][{cur}]overlay=format=auto,"
                      f"format=yuv420p[{res}]")
        return self.lay(main, res, self.region_alpha())

    def duotone(self, label, hexcol):
        """The copy as a luminance-keyed tint: tint x (lift + gain x luma)
        in RGB, so a ghost reads as the subject in the tint's colour, never
        a flat stamp."""
        r, g, b = _hex_rgb(hexcol)
        t, k, out = self.lab("dt"), self.lab("dk"), self.lab("do")
        self.p.append(f"color=c=0x{r:02x}{g:02x}{b:02x}:s={self.W}x{self.H}"
                      f":r={self.F:.3f},trim=end_frame={self.n},format=gbrp,"
                      f"{self.restamp}[{t}]")
        self.p.append(f"[{label}]format=gray,lut=c0='clip({DUO_LIFT}+val*"
                      f"{DUO_GAIN},0,255)',format=gbrp[{k}]")
        self.p.append(f"[{t}][{k}]blend=all_mode=multiply,{TO_YUV}[{out}]")
        return out

    # -- backgrounds -----------------------------------------------------
    def background(self, label):
        it, st = self.it, self.st
        kind = it.get("kind")
        main = label
        hold = st.get("hold")
        raw = self.raw_mask()
        need_pic = kind in ("blur", "radial_blur") or hold is None
        pic_srcs = [s for s in st.get("sources") or []
                    if s.get("kind") in ("program", "freeze")]
        n_pic = (1 if need_pic else 0) + (1 if hold is not None else 0) + \
            len(pic_srcs)
        pics = []
        if n_pic:
            outs = [self.lab("bp") for _ in range(n_pic + 1)]
            self.p.append(f"[{label}]split={n_pic + 1}"
                          + "".join(f"[{o}]" for o in outs))
            main, pics = outs[0], outs[1:]
        masks = self.split(raw, 2 if kind in ("blur", "radial_blur") else 1)
        # subject layer S (with its alpha)
        if hold is not None:
            sp = self.lab("hs")
            self.p.append(f"[{pics.pop()}]trim=start_frame={hold}:end_frame="
                          f"{hold + 1},loop=loop={self.n - 1}:size=1:start=0,"
                          f"{self.restamp}[{sp}]")
            sa = self.lab("ha")
            self.p.append(f"[{masks[0]}]trim=start_frame={hold}:end_frame="
                          f"{hold + 1},format=gray,"
                          f"{self.firm_expr(FIRM_CRISP)},"
                          f"loop=loop={self.n - 1}:size=1:start=0,"
                          f"{self.restamp}[{sa}]")
            pw = None
            if need_pic:
                pw = self.lab("hp")
                self.p.append(f"[{pics.pop()}]trim=start_frame={self.f0}:"
                              f"end_frame={self.f1},{self.restamp}[{pw}]")
        else:
            pw = self.lab("bw")
            self.p.append(f"[{pics.pop()}]trim=start_frame={self.f0}:"
                          f"end_frame={self.f1},{self.restamp}[{pw}]")
            sp_split = self.split(pw, 2) if kind in ("blur", "radial_blur") \
                else [pw]
            sp = sp_split[0]
            pw = sp_split[1] if len(sp_split) > 1 else None
            sa = self.mask_window(masks[0], firm=FIRM_CRISP, region=False)
        bg = self.background_layer(kind, pw, masks[1] if len(masks) > 1
                                   else None, pics)
        # The subject picture is the overlay's MAIN input and the new world
        # goes on top through the inverse matte: the result keeps the
        # program's colour tags, and footage plates (whatever their own
        # matrix) are converted into them, never the program into theirs.
        bl, inv, res = self.lab("bl"), self.lab("bi"), self.lab("br")
        self.p.append(f"[{sa}]negate[{inv}]")
        self.p.append(f"[{bg}]format=yuva420p[{bl}x]")
        self.p.append(f"[{bl}x][{inv}]alphamerge[{bl}]")
        self.p.append(f"[{sp}][{bl}]overlay=eof_action=pass[{res}]")
        return self.lay(main, res, self.region_alpha())

    def background_layer(self, kind, pw, mask2, pics):
        it, st = self.it, self.st
        if kind == "solid":
            return self.colour_layer(self.colour("colour", "black"))
        if kind == "gradient":
            g = self.ctx.input_label(st["gradient"])
            out = self.lab("gr")
            self.p.append(f"[{g}]trim=end_frame={self.n},format=yuv420p,"
                          f"{self.restamp}[{out}]")
            return out
        if kind == "blur":
            return self.clean_blur(pw, mask2,
                                   float(_get(it, "radius", BLUR_RADIUS)))
        if kind == "radial_blur":
            # streak a CLEAN plate: the subject is filled from its
            # surroundings first, so it never smears into its own halo
            a, b = self.split(pw, 2)
            plate = self.clean_blur(a, mask2, 0.03, keep=b)
            return self.zoom_blur(plate, float(_get(it, "radius",
                                                    RADIAL_RADIUS)))
        if kind == "source":
            return self.source_stream(st["sources"][0], pics)
        if kind == "strobe":
            return self.strobe(st["sources"], pics)
        raise ValueError(f"unknown background kind {kind!r}")

    def clean_blur(self, pw, mask, radius, keep=None):
        """The background defocused WITHOUT the subject bleeding into it:
        a normalised convolution, blur(P x w) / blur(w) with w = 1 - M, at
        half resolution. Where the blurred weight vanishes (deep inside the
        subject) the plain blur shows instead, never a divide-by-zero.

        ``keep``: a second copy of the window picture; the result is then a
        CLEAN PLATE: that sharp picture outside the subject, the subject's
        area filled from its blurred surroundings."""
        s = soft_scale(self.H, self.st.get("reduced"))
        w, h = max(2, self.W // s // 2 * 2), max(2, self.H // s // 2 * 2)
        sig = max(0.5, radius * self.H / s)
        L = self.lab
        wt, pm, num, den, pb, nc, out = (L("bw"), L("bm"), L("bn"), L("bd"),
                                         L("bp"), L("bc"), L("bb"))
        self.p.append(f"[{mask}]trim=start_frame={self.f0}:end_frame="
                      f"{self.f1},{self.restamp},format=gray,"
                      f"{self.firm_expr(FIRM_CRISP, invert=True)},"
                      + ("erosion,erosion,erosion," if keep else "dilation,")
                      + f"split[{wt}f][{wt}s]")
        self.p.append(f"[{wt}s]scale={w}:{h}:flags=area,"
                      f"split[{wt}a][{wt}b]")
        self.p.append(f"[{wt}b]gblur=sigma={sig:.3f},split[{den}g][{den}a]")
        self.p.append(f"[{den}g]format=gbrp[{den}]")
        self.p.append(f"[{wt}a]format=gbrp[{wt}c]")
        self.p.append(f"[{pw}]scale={w}:{h}:flags=area,format=gbrp,"
                      f"split[{pm}p][{pb}p]")
        self.p.append(f"[{pm}p][{wt}c]blend=all_mode=multiply[{pm}]")
        self.p.append(f"[{pm}]gblur=sigma={sig:.3f}[{num}]")
        self.p.append(f"[{num}][{den}]blend=all_mode=divide[{nc}]")
        self.p.append(f"[{pb}p]gblur=sigma={sig * 2:.3f}[{pb}]")
        self.p.append(f"[{den}a]lut=c0='clip(val*16,0,255)'[{nc}a]")
        self.p.append(f"[{nc}]format=gbrap[{nc}x]")
        self.p.append(f"[{nc}x][{nc}a]alphamerge[{nc}m]")
        if keep is None:
            self.p.append(f"[{wt}f]nullsink")
            self.p.append(f"[{pb}][{nc}m]overlay=format=auto,"
                          f"scale={self.W}:{self.H}:flags=bicubic,"
                          f"format=yuv420p[{out}]")
            return out
        fill = L("bf")
        self.p.append(f"[{pb}][{nc}m]overlay=format=auto,"
                      f"scale={self.W}:{self.H}:flags=bicubic,"
                      f"format=yuv420p[{fill}]")
        kp = L("bk")
        self.p.append(f"[{keep}]format=yuva420p[{kp}x]")
        self.p.append(f"[{kp}x][{wt}f]alphamerge[{kp}]")
        self.p.append(f"[{fill}][{kp}]overlay=format=auto,format=yuv420p"
                      f"[{out}]")
        return out

    def zoom_blur(self, pw, radius):
        """A radial (zoom) blur from the frame centre: the mean of
        RADIAL_COPIES copies scaled 1 .. 1 + radius, at half resolution."""
        s = soft_scale(self.H, self.st.get("reduced"))
        w, h = max(2, self.W // s // 2 * 2), max(2, self.H // s // 2 * 2)
        k = RADIAL_COPIES // (2 if self.st.get("reduced") else 1)
        base = self.lab("zb")
        self.p.append(f"[{pw}]scale={w}:{h}:flags=area[{base}]")
        copies = self.split(base, k)
        outs = []
        for i, c in enumerate(copies):
            z = 1.0 + radius * i / max(1, k - 1)
            zw = max(w, int(round(w * z / 2)) * 2)
            zh = max(h, int(round(h * z / 2)) * 2)
            o = self.lab("zc")
            self.p.append(f"[{c}]scale={zw}:{zh}:flags=bilinear,"
                          f"crop={w}:{h}:{(zw - w) // 2}:{(zh - h) // 2}"
                          f"[{o}]")
            outs.append(o)
        out = self.lab("zm")
        self.p.append("".join(f"[{o}]" for o in outs)
                      + f"mix=inputs={k}:duration=first,"
                      f"scale={self.W}:{self.H}:flags=bicubic,"
                      f"format=yuv420p[{out}]")
        return out

    def source_stream(self, src, pics):
        """One FxSource plan as a window-local W x H stream."""
        kind = src["kind"]
        out = self.lab("ss")
        if kind == "colour":
            return self.colour_layer(resolve_colour(src.get("colour"),
                                                    self.edl))
        if kind == "program":
            a = int(src["from"])
            last = max(0, self.ctx.n_frames - 1)
            lo, hi = max(0, min(a, last)), max(0, min(a + self.n, last + 1))
            pre = max(0, -a) if a < 0 else 0
            chain = [f"trim=start_frame={lo}:end_frame={max(lo + 1, hi)}",
                     self.restamp]
            if pre:
                chain += [f"tpad=start={pre}:start_mode=clone"]
            chain += [f"tpad=stop={self.n}:stop_mode=clone",
                      f"trim=end_frame={self.n}", self.restamp]
            self.p.append(f"[{pics.pop()}]{','.join(chain)}[{out}]")
            return out
        if kind == "freeze":
            fr = max(0, min(int(src["frame"]), self.ctx.n_frames - 1))
            self.p.append(f"[{pics.pop()}]trim=start_frame={fr}:end_frame="
                          f"{fr + 1},loop=loop={self.n - 1}:size=1:start=0,"
                          f"{self.restamp}[{out}]")
            return out
        if kind == "input":
            lab = self.ctx.input_label(src["key"])
            if lab is None:
                raise RuntimeError("a background input was never bound")
            self.p.append(
                f"[{lab}]fps={self.F:.3f},scale={self.W}:{self.H}:"
                f"force_original_aspect_ratio=increase:flags=bicubic,"
                f"crop={self.W}:{self.H},setsar=1,format=yuv420p,"
                f"{self.restamp},tpad=stop={self.n}:stop_mode=clone,"
                f"trim=end_frame={self.n},{self.restamp}[{out}]")
            return out
        raise ValueError(f"unknown source plan {kind!r}")

    def strobe(self, sources, pics):
        """Plates swapped every ``hold_f`` frames, in order, looping."""
        hold = max(1, int(_get(self.it, "hold_f", 2)))
        streams = [self.source_stream(s, pics) for s in sources]
        if len(streams) == 1:
            return streams[0]
        spans = {i: [] for i in range(len(streams))}
        for j, a in enumerate(range(0, self.n, hold)):
            spans[j % len(streams)].append((a, min(self.n, a + hold)))
        canvas = streams[0]
        for i in range(1, len(streams)):
            if not spans[i]:
                self.p.append(f"[{streams[i]}]nullsink")
                continue
            nxt = self.lab("so")
            self.p.append(f"[{canvas}][{streams[i]}]overlay="
                          f"enable='{fxprims.enable_expr(spans[i])}'[{nxt}]")
            canvas = nxt
        return canvas

    # -- substitutes (no matte) -----------------------------------------
    def sub_full_frame_flash(self, label):
        it = self.it
        params = (self.ctx.plan.devices.get(it.get("id")) or {}).get(
            "params") or {}
        inten = float(params.get("intensity", FLASH_SUB_INTENSITY)) * \
            float(_get(it, "intensity", 1.0))
        a = self.const_alpha(255 * inten)
        dec = self.decay_weights()
        if dec:
            d = self.lab("fd")
            self.p.append(f"[{a}]" + ",".join(
                f"lut=c0='val*{w:.5f}':enable='eq(n,{k})'" for k, w in dec)
                + f"[{d}]")
            a = d
        if self.st.get("region"):
            a = self.intersect_region(a)
        return self.lay(label, self.colour_layer(self.colour()), a)

    def sub_colour_field_card(self, label):
        it = self.it
        field = self.colour_layer(resolve_colour(it.get("field"), self.edl,
                                                 "accent"))
        main, pw = self.window_pic(label)
        cw = int(round(self.W * CARD_SCALE / 2)) * 2
        ch = int(round(self.H * CARD_SCALE / 2)) * 2
        card = self.ctx.input_label(f"w1b:{it['id']}:card")
        if card is None:
            raise RuntimeError("the card mask was never bound")
        cm, cp, cc, res = (self.lab("cm"), self.lab("cp"), self.lab("cc"),
                           self.lab("cr"))
        self.p.append(f"[{card}]trim=end_frame={self.n},format=gray,"
                      f"{self.restamp}[{cm}]")
        self.p.append(f"[{pw}]scale={cw}:{ch}:flags=bicubic,format=yuva420p"
                      f"[{cp}]")
        self.p.append(f"[{cp}][{cm}]alphamerge[{cc}]")
        self.p.append(f"[{field}][{cc}]overlay=x={(self.W - cw) // 2}:"
                      f"y={(self.H - ch) // 2}:format=auto,format=yuv420p"
                      f"[{res}]")
        return self.lay(main, res, self.region_alpha())

    def sub_halation(self, label):
        """Glow without a matte: the picture's own highlights bloom."""
        it = self.it
        lo, hi = HALATION_KNEE
        main, pw = self.window_pic(label)
        a, b = self.lab("ha"), self.lab("hb")
        self.p.append(f"[{pw}]split[{a}][{b}]")
        s = soft_scale(self.H)
        w, h = max(2, self.W // s // 2 * 2), max(2, self.H // s // 2 * 2)
        sig = max(0.6, float(_get(it, "radius", GLOW_RADIUS)) * self.H / s
                  * 0.35)
        inten = max(0.0, min(1.0, float(_get(it, "intensity", 1.0)))) * 0.8
        lk = self.lab("hk")
        self.p.append(f"[{a}]format=gray,scale={w}:{h}:flags=area,"
                      f"lut=c0='clip((val-{int(lo * 255)})*"
                      f"{255.0 / max(1, int(hi * 255) - int(lo * 255)):.4f}"
                      f",0,255)',gblur=sigma={sig:.3f},"
                      f"scale={self.W}:{self.H}:flags=bicubic,"
                      f"lut=c0='val*{inten:.4f}'[{lk}]")
        # re-join: screen_light expects the program label; work on the
        # window picture directly instead
        tg, tl, pg, sc, sup = (self.lab("tg"), self.lab("tl"),
                               self.lab("pg"), self.lab("sc"), self.lab("su"))
        r, g, bb = _hex_rgb(self.colour("colour", "white"))
        self.p.append(f"[{lk}]split[{lk}a][{lk}b]")
        self.p.append(f"color=c=0x{r:02x}{g:02x}{bb:02x}:s={self.W}x"
                      f"{self.H}:r={self.F:.3f},trim=end_frame={self.n},"
                      f"format=gbrp,{self.restamp}[{tg}]")
        self.p.append(f"[{lk}a]format=gbrp[{tl}g]")
        self.p.append(f"[{tg}][{tl}g]blend=all_mode=multiply[{tl}]")
        self.p.append(f"[{b}]{TO_RGB}[{pg}]")
        self.p.append(f"[{pg}][{tl}]blend=all_mode=screen,{TO_YUV}[{sc}]")
        self.p.append(f"[{lk}b]lut=c0='min(255,val*{LIGHT_ALPHA_GAIN})'"
                      f"[{sup}]")
        if self.st.get("region"):
            sup = self.intersect_region(sup)
        return self.lay(main, sc, sup)

    def sub_hue_key_only(self, label):
        hue = str(self.it.get("hue") or "")
        self.ctx.label = label
        hm = self.ctx.mask(f"hue:{hue.lstrip('#')}")
        label = self.ctx.label
        if hm is None:
            raise RuntimeError("the hue key vanished at emit")
        a = self.lab("hk")
        self.p.append(f"[{hm}]trim=start_frame={self.f0}:end_frame={self.f1},"
                      f"{self.restamp},format=gray,negate[{a}]")
        if self.st.get("region"):
            a = self.intersect_region(a)
        main, pw = self.window_pic(label)
        mono = self.lab("mn")
        self.p.append(f"[{pw}]hue=s=0[{mono}]")
        return self.lay(main, mono, a)

    def sub_flash_junction(self, label):
        """No matte for a subject hold: a white flash on the swap."""
        a = self.const_alpha(255)
        d = self.lab("fj")
        self.p.append(f"[{a}]lut=c0=0:enable='gte(n,2)',"
                      f"lut=c0='val*0.42':enable='eq(n,1)'[{d}]")
        return self.lay(label, self.colour_layer("#ffffff"), d)


COMPILER = _Compiler()

