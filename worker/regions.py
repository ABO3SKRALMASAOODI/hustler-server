"""FxRegion -> program mask: one resolver for every Wave 1 device.

Wave 1 interface pass (worker/devices/INTERFACES_W1.md §1.4). A region
(``fx_schemas.FxRegion``) is written once and means the same thing in an
accent (1D), a subject effect (1B), a region effect (1C), a grade window or
a finish (1E). Compilers never rasterise regions themselves; they call:

  prepare(plan, item, region, key="region") -> None | (rung, reason)
      from their own ``prepare``: precomputes what the region needs (a gray
      still for a static shape) and registers it as an ffmpeg input
  pad(ctx, item, region, key="region") -> pad | None
      at emit: a FRESH gray program mask (W x H, program rate, full length;
      255 inside, black inert) with ``invert`` and ``feather_px`` applied.
      One pad per (item, key); split it yourself if you need two.
  bbox(region, W, H) -> (x, y, w, h) | None
      the region's even pixel box when it is static (bbox-local cost), else
      None (full frame)

Shapes and their owners:

  rect, ellipse, polygon (static)  here (round lead): a PIL still, feather
                                   and invert baked in
  rect, ellipse, polygon (keys)    1C: keyed_prepare / keyed_pad
  glyph                            1C: the ``glyph:<motion id>`` mask
  face                             1A: the ``face`` mask (faces.yunet)
  card                             1D: card_prepare / card_pad (the picture
                                   card's inner rect, from picture_cards)

Never raises out of prepare/pad: a region that cannot be built degrades its
device (``omit`` with the reason, or None -> the compiler's substitute).
"""

import os

import fxprims

# Feather is authored in pixels at a 1080-px-high frame.
FEATHER_REF_H = 1080.0


def _get(region, key, default=None):
    if region is None:
        return default
    if isinstance(region, dict):
        return region.get(key, default)
    return getattr(region, key, default)


def _input_key(item, key):
    return f"region:{(item or {}).get('id')}:{key}"


def is_static(region):
    return _get(region, "shape") in ("rect", "ellipse", "polygon") and \
        not _get(region, "keys")


def bbox(region, W, H):
    """Even pixel (x, y, w, h) of a static rect/ellipse/polygon (feather
    included), clamped to the frame; None for every other region or an
    inverted one (it covers the frame)."""
    if not is_static(region) or _get(region, "invert"):
        return None
    shape = _get(region, "shape")
    if shape == "polygon":
        pts = _get(region, "points") or []
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    else:
        x0, y0, x1, y1 = (float(v) for v in _get(region, "box"))
    pad = float(_get(region, "feather_px") or 0.0) * 2.0 * H / FEATHER_REF_H
    return fxprims.even_box((x0 * W - pad, y0 * H - pad,
                             (x1 - x0) * W + 2 * pad,
                             (y1 - y0) * H + 2 * pad), W, H)


def still(region, W, H):
    """A PIL ``L`` image of a static region at W x H (invert and feather
    applied), or None."""
    from PIL import Image, ImageDraw, ImageFilter
    if not is_static(region):
        return None
    W, H = int(W), int(H)
    im = Image.new("L", (W, H), 0)
    draw = ImageDraw.Draw(im)
    shape = _get(region, "shape")
    if shape == "polygon":
        pts = [(float(x) * W, float(y) * H)
               for x, y in (_get(region, "points") or [])]
        if len(pts) < 3:
            return None
        draw.polygon(pts, fill=255)
    else:
        x0, y0, x1, y1 = (float(v) for v in _get(region, "box"))
        box = [x0 * W, y0 * H, x1 * W - 1, y1 * H - 1]
        if box[2] < box[0] or box[3] < box[1]:
            return None
        (draw.ellipse if shape == "ellipse" else draw.rectangle)(
            box, fill=255)
    feather = float(_get(region, "feather_px") or 0.0) * H / FEATHER_REF_H
    if feather > 0:
        im = im.filter(ImageFilter.GaussianBlur(radius=feather))
    if _get(region, "invert"):
        from PIL import ImageOps
        im = ImageOps.invert(im)
    return im


def prepare(plan, item, region, key="region"):
    """Precompute the region for one item. None when ready (or when nothing
    needs precomputing); (rung, reason) to degrade the item."""
    try:
        shape = _get(region, "shape")
        if shape is None:
            return ("omit", "no region")
        if is_static(region):
            return _prepare_static(plan, item, region, key)
        if shape in ("rect", "ellipse", "polygon"):
            return keyed_prepare(plan, item, region, key)
        if shape == "card":
            return card_prepare(plan, item, region, key)
        return None                # glyph / face: masks resolved at emit
    except Exception as e:  # noqa: BLE001 — degrade, never fail
        return ("omit", f"region could not be prepared "
                f"({type(e).__name__}: {str(e)[:120]})")


def _prepare_static(plan, item, region, key):
    if not plan.workdir or not plan.W or not plan.H:
        return ("omit", "no workdir/geometry for the region mask")
    im = still(region, plan.W, plan.H)
    if im is None:
        return ("omit", "the region is empty at this frame size")
    path = os.path.join(plan.workdir,
                        f"fxregion_{(item or {}).get('id')}_{key}.png")
    im.save(path)
    plan.add_input(_input_key(item, key), [
        "-loop", "1", "-framerate", f"{plan.fps3:.3f}",
        "-t", f"{plan.program_s + 1.0:.3f}", "-i", path])
    return None


_SPENT = "_regions_spent"


def pad(ctx, item, region, key="region"):
    """A fresh gray program mask for ``region`` (see the module doc), or
    None (absent: the compiler takes its substitute rung)."""
    try:
        shape = _get(region, "shape")
        if is_static(region):
            spent = ctx.plan.__dict__.setdefault(_SPENT, set())
            k = _input_key(item, key)
            label = ctx.input_label(k)
            if label is None or k in spent:
                return None
            spent.add(k)
            out = ctx.uid("rg")
            ctx.parts.append(f"[{label}]format=gray[{out}]")
            return out
        if shape in ("rect", "ellipse", "polygon"):
            return keyed_pad(ctx, item, region, key)
        if shape == "card":
            return card_pad(ctx, item, region, key)
        if shape == "glyph":
            raw = ctx.mask(f"glyph:{_get(region, 'ref')}")
        elif shape == "face":
            ref = _get(region, "ref")
            raw = ctx.mask("face" if ref is None else f"face:{ref}")
        else:
            return None
        if raw is None:
            return None
        feather = float(_get(region, "feather_px") or 0.0) * \
            float(ctx.H or FEATHER_REF_H) / FEATHER_REF_H
        if not feather and not _get(region, "invert"):
            return raw
        out = ctx.uid("rg")
        return fxprims.mask_ops(ctx.parts, raw, out, uid=out,
                                invert=bool(_get(region, "invert")),
                                feather_px=feather)
    except Exception:  # noqa: BLE001
        return None


def region_mask(plan, name, space, ctx):
    """Mask provider for ``region:<fx id>`` (fxgraph.MASK_PEERS): the region
    of that fx item on this render, or None."""
    if ctx is None or ":" not in name:
        return None
    fx_id = name.split(":", 1)[1]
    for items in plan.by_stage.values():
        for it in items:
            if it.get("id") == fx_id and it.get("region"):
                return pad(ctx, it, it["region"])
    return None


# ── keyframed shapes (track 1C) ─────────────────────────────────────────

def keyed_prepare(plan, item, region, key):
    """1C: precompute a keyframed rect/ellipse/polygon (a gray mask clip
    over the item's window, eased between keys). STUB until 1C."""
    return ("omit", "keyframed regions arrive with track 1C")


def keyed_pad(ctx, item, region, key):
    """1C: the keyframed region's fresh gray program mask. STUB."""
    return None


# ── the picture card's region (track 1D) ────────────────────────────────

def _card_key(item, key):
    return f"region:{(item or {}).get('id')}:{key}:card"


def card_prepare(plan, item, region, key):
    """1D: the picture card's rounded window as a gray still, drawn exactly
    as the card's own plate draws its hole (wipe_masks.card_mask), for the
    card on screen over the item's window. Registered as a one-frame input
    (decoded once, held by ``loop`` in card_pad). Degrades to ``omit`` when
    no card is on screen then, or the render's EDL is out of reach."""
    import wipe_masks
    if not plan.workdir or not plan.W or not plan.H:
        return ("omit", "no workdir/geometry for the card mask")
    edl = wipe_masks.render_env(plan).get("edl")
    if edl is None:
        return ("omit", "the render's picture cards are out of reach here")
    t0 = (item or {}).get("start")
    t1 = (item or {}).get("end")
    if t0 is None:
        t0 = (item or {}).get("at")
    if t0 is None:
        return ("omit", "a card region needs the item's window")
    spec = wipe_masks.card_spec_at(edl, t0, t1)
    if spec is None:
        return ("omit", f"no picture card is on screen at {float(t0):g}s")
    path = wipe_masks.card_mask_path(plan.workdir, plan.W, plan.H, spec)
    plan.add_input(_card_key(item, key), ["-i", path])
    return None


def card_pad(ctx, item, region, key):
    """1D: the card region's fresh gray program mask (255 inside the
    card's window, the whole program long), or None."""
    import wipe_masks
    k = _card_key(item, key)
    spent = ctx.plan.__dict__.setdefault(_SPENT, set())
    label = ctx.input_label(k)
    if label is None or k in spent:
        return None
    spent.add(k)
    out = ctx.uid("rgc")
    n = int(ctx.plan.n_frames) + 2
    ctx.parts.append(f"[{label}]format=gray,loop=loop=-1:size=1,"
                     f"{wipe_masks.pts(ctx.fps3)},trim=end_frame={n}"
                     f"[{out}]")
    if _get(region, "invert") or float(_get(region, "feather_px") or 0.0):
        feather = float(_get(region, "feather_px") or 0.0) * \
            float(ctx.H or FEATHER_REF_H) / FEATHER_REF_H
        o2 = ctx.uid("rgc")
        return fxprims.mask_ops(ctx.parts, out, o2, uid=o2,
                                invert=bool(_get(region, "invert")),
                                feather_px=feather)
    return out
