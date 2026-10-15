"""Track 4C compiler: EDL.fx ``broll`` — the cinematic cutaway
(fx_schemas/broll.py; worker/devices/INTERFACES_W4.md §3.5).

A sourced clip or photo replaces the program picture over [start, end) at
the ``screen`` stage (the speech and the captions carry on above it), with
the de-stocking finish that makes it sit in the camera's world:

  clip    the asset from ``offset_s`` (looped when the window outlasts it),
          resampled to the program rate, fitted (``cover`` aimed at
          ``focus_x``/``focus_y``, ``contain`` on black, or ``frame``: the
          whole picture on a soft, darkened copy of itself — archival)
  still   never dead on screen: a 2.5D move through its measured depth
          (depth_fx layers; the ``depth.small`` sidecar of the asset) or a
          Ken Burns move when no depth is measured (rung ``substitute``)
  finish  the hero-matched .cube (``match``; gradewin's LUT path), a soften
          for over-sharpened stock, temporal luma grain

Composited with ``finish.window_composite``: outside the window the program
is bit-exact; everything compiles before the bars, the screen-frame plate,
the watermark and the end card (owner rule 4). Renders never run models and
never fetch from providers: the asset is already in the project.
"""

import os

import numpy as np

import fxprims

FILTERS = ("split", "trim", "setpts", "settb", "format", "scale", "crop",
           "pad", "overlay", "fps", "lut3d", "gblur", "noise", "eq",
           "nullsink", "setsar", "null")
KEEPS_FRAME_TAGS = True
# Local CPU-ms per program megapixel-frame (decode + scale + finish), and a
# still's 2.5D precompute.
MS_PER_MPX = {"video": 3.0, "still": 6.0}
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")


def _state(plan):
    return plan.__dict__.setdefault("_w4_broll", {})


def _key(item, plan):
    import hashlib
    raw = repr((item.get("id"), item.get("source"), item.get("move"),
                item.get("focus_x"), item.get("focus_y"), item.get("fit"),
                item.get("start"), item.get("end"), plan.W, plan.H,
                plan.fps3, item.get("depth")))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def is_still(item):
    if item.get("media") == "still":
        return True
    key = str(((item.get("source") or {}).get("asset_key")) or "")
    return os.path.splitext(key)[1].lower() in _IMAGE_EXTS


def crop_aspect(img, aspect, focus=(0.5, 0.5)):
    """The largest ``aspect`` (w/h) window of ``img`` centred as near
    ``focus`` (fractions) as the picture allows."""
    h, w = img.shape[:2]
    if w / float(h) > aspect:
        cw, ch = max(2, int(round(h * aspect))), h
    else:
        cw, ch = w, max(2, int(round(w / aspect)))
    x = int(round(focus[0] * w - cw / 2.0))
    y = int(round(focus[1] * h - ch / 2.0))
    x = min(max(0, x), w - cw)
    y = min(max(0, y), h - ch)
    return img[y:y + ch, x:x + cw], (x, y, cw, ch)


def framed(img, W, H):
    """The archival ``frame`` fit as a still: the whole picture contained
    (with a small margin) on a soft, darkened cover copy of itself."""
    import cv2
    import depth_fx
    bg = depth_fx.cover_crop(img, W, H)
    sig = max(6.0, 0.03 * max(W, H))
    bg = cv2.GaussianBlur(bg, (0, 0), sig).astype(np.float32) * 0.55
    h, w = img.shape[:2]
    k = min(W * 0.94 / w, H * 0.94 / h)
    nw, nh = max(2, int(w * k)), max(2, int(h * k))
    fg = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA
                    if k < 1 else cv2.INTER_CUBIC)
    x, y = (W - nw) // 2, (H - nh) // 2
    bg[y:y + nh, x:x + nw] = fg
    return np.clip(bg + 0.5, 0, 255).astype(np.uint8)


class _Broll:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        kind = "still" if is_still(item) else "video"
        return 200.0 + (f1 - f0) * mpx * MS_PER_MPX[kind]

    def landing(self, plan, item):
        return [fxprims.frame_at(float(item["start"]), plan.fps)]

    def prepare(self, plan, item):
        import faultinject
        import subject_fx
        if not plan.W or not plan.H or not plan.workdir:
            return ("omit", "no geometry or workdir for the cutaway")
        if faultinject.fault("oom:fx_precompute"):
            raise MemoryError("the cutaway precompute was OOM-killed")
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        n = f1 - f0
        if n < 1:
            return ("omit", "an empty window")
        import depth_fx
        if depth_fx.audio_only(plan):
            _state(plan)[item.get("id")] = {"audio_only": True}
            return None
        os.makedirs(plan.workdir, exist_ok=True)
        src = item.get("source") or {}
        st = {"n": n, "rung": "full", "reason": "ok", "lut_path": None}
        try:
            path = subject_fx.asset_path(plan, src.get("asset_key"))
        except Exception as e:  # noqa: BLE001
            path, why = None, str(e)[:120]
        else:
            why = "the asset could not be fetched"
        if not path:
            return ("omit", f"cutaway asset unavailable ({why})")
        lut = item.get("match") or {}
        if lut.get("asset_key"):
            st.update(self._lut(plan, item, lut))
        try:
            if is_still(item):
                st.update(self._prep_still(plan, item, n, path, st))
            else:
                dur = n / plan.fps3 + 0.5
                args = []
                if src.get("loop"):
                    args += ["-stream_loop", "-1"]
                off = max(0.0, float(src.get("offset_s") or 0.0))
                if off > 0:
                    args += ["-ss", f"{off:.3f}"]
                args += ["-t", f"{dur:.3f}", "-i", path]
                st.update({"video": True, "args": args})
        except MemoryError:
            raise
        except Exception as e:  # noqa: BLE001 — degrade, never fail
            return ("omit", f"cutaway precompute failed "
                            f"({type(e).__name__}: {str(e)[:120]})")
        if st.get("rung") == "omit":
            return ("omit", st.get("reason") or "omitted")
        _state(plan)[item.get("id")] = st
        if st.get("clip"):
            plan.add_input(f"w4b:{item['id']}", ["-i", st["clip"]])
        elif st.get("args"):
            plan.add_input(f"w4b:{item['id']}", st["args"])
        if st["rung"] != "full":
            return (st["rung"], st["reason"])
        return None

    def _lut(self, plan, item, lut):
        import fxgraph
        import gradewin
        import lutlib
        why = None
        if fxgraph.filter_missing("lut3d"):
            why = "this ffmpeg lacks lut3d"
        else:
            path = gradewin._asset_local(plan, lut.get("asset_key"))
            if not path:
                why = "the match LUT could not be fetched"
            else:
                try:
                    cube = gradewin.load_lut(path, lut.get("strength", 1.0))
                    return {"lut_path": gradewin._write_cube(plan, item,
                                                             cube)}
                except (OSError, lutlib.LutError) as e:
                    why = f"the match LUT is unusable ({str(e)[:80]})"
        return {"rung": "reduced", "reason": f"cut in without its match "
                                             f"grade: {why}"}

    def _prep_still(self, plan, item, n, path, prev):
        import depth_fx
        W, H = int(plan.W), int(plan.H)
        img = depth_fx._decode_frame(path, 0.0)
        if img is None:
            return {"rung": "omit", "reason": "the still could not be read"}
        focus = (float(item.get("focus_x", 0.5)),
                 float(item.get("focus_y", 0.5)))
        move = item.get("move") or {"path": "push_in", "amount": 0.3,
                                    "ease": "in_out"}
        out = os.path.join(plan.workdir, f"w4b_{_key(item, plan)}.mkv")
        fit = item.get("fit") or "cover"
        rung = prev.get("rung", "full")
        reason = prev.get("reason", "ok")
        if fit != "cover":
            pic = framed(img, W, H) if fit == "frame" else \
                _contained(img, W, H)
            w = depth_fx.ClipWriter(out, W, H, plan.fps3, "rgb24")
            depth_fx.render_ken_burns(pic, W, H, n, dict(
                move, amount=min(0.2, float(move.get("amount") or 0.3))), w)
            w.close()
            return {"clip": out, "rung": rung, "reason": reason}
        crop, box = crop_aspect(img, W / float(H), focus)
        depth = None
        why = "depth off for this cutaway"
        if item.get("depth", True):
            wait_s = 0.0 if plan.preview else 90.0
            fake = {"id": item.get("id"), "source": item.get("source")}
            d, drung, why = depth_fx.still_depth(plan, fake, img,
                                                 ("asset", path), wait_s)
            if d is not None and drung == "full":
                x, y, cw, ch = box
                depth = np.clip(d[y:y + ch, x:x + cw], 0, 1)
        w = depth_fx.ClipWriter(out, W, H, plan.fps3, "rgb24")
        if depth is None:
            depth_fx.render_ken_burns(crop, W, H, n, move, w)
            w.close()
            return {"clip": out, "rung": "substitute" if rung == "full"
                    else rung, "reason": f"{why} -> Ken Burns"
                    if rung == "full" else reason}
        pic = depth_fx.cover_crop(crop, W, H)
        dep = depth_fx.cover_crop(np.dstack([depth] * 3).astype(np.float32),
                                  W, H)[..., 0]
        planes = depth_fx.two_planes(pic, np.clip(dep, 0, 1))
        depth_fx.render_parallax_planes(pic, planes, W, H, n, move, w)
        w.close()
        vis = depth_fx.visible_inpaint_planes(planes, W, H, move, n)
        return {"clip": out, "rung": rung, "reason": reason,
                "visible_inpaint": round(vis, 4)}

    def stage(self, ctx, items):
        import finish
        return finish.emit_each(ctx, items, self._emit)

    def _chain(self, it, st, W, H, fps):
        """The fit + finish filters for one cutaway's frames."""
        fit = it.get("fit") or "cover"
        fx_, fy = float(it.get("focus_x", 0.5)), float(it.get("focus_y", 0.5))
        out = []
        if st.get("video"):
            out.append(f"fps={fxprims.fps3(fps):.3f}")
            if fit == "cover":
                out.append(f"scale={W}:{H}:force_original_aspect_ratio="
                           f"increase:flags=bicubic,"
                           f"crop={W}:{H}:(in_w-{W})*{fx_:.4f}:"
                           f"(in_h-{H})*{fy:.4f}")
            elif fit == "contain":
                out.append(f"scale={W}:{H}:force_original_aspect_ratio="
                           f"decrease:flags=bicubic,"
                           f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
            out.append("setsar=1")
        if st.get("lut_path"):
            import gradewin
            out.append(f"format=gbrp,lut3d=file='"
                       f"{gradewin._escape(st['lut_path'])}'"
                       f":interp=tetrahedral")
        soft = it.get("soften")
        if soft:
            out.append(f"gblur=sigma={0.25 + 0.9 * float(soft):.3f}")
        grain = it.get("grain")
        if grain:
            out.append(f"format=yuv420p,noise=c0s="
                       f"{int(round(3 + 15 * float(grain)))}:c0f=t+u")
        return out

    def _emit(self, ctx, label, it):
        import finish
        st = _state(ctx.plan).get(it.get("id")) or {}
        if st.get("audio_only"):
            return label
        f0, f1 = ctx.frames(it)
        n = f1 - f0
        u = ctx.uid("br")
        clip = ctx.input_label(f"w4b:{it['id']}")
        if clip is None:
            raise ValueError("the cutaway input was never bound")
        W, H = int(ctx.W), int(ctx.H)
        clock = finish.window_clock(ctx.fps)
        fit = it.get("fit") or "cover"

        def build(parts, src, uid):
            parts.append(f"[{src}]nullsink")
            chain = self._chain(it, st, W, H, ctx.fps)
            head = f"[{clip}]setpts=PTS-STARTPTS"
            if st.get("video") and fit == "frame":
                a, b, c = ctx.uid("brb"), ctx.uid("brf"), ctx.uid("brc")
                sig = max(6.0, 0.03 * max(W, H))
                parts.append(f"{head},fps={fxprims.fps3(ctx.fps):.3f},"
                             f"setsar=1,split[{a}][{b}]")
                parts.append(f"[{a}]scale={W}:{H}:force_original_aspect_"
                             f"ratio=increase,crop={W}:{H},gblur=sigma="
                             f"{sig:.1f},eq=brightness=-0.16:saturation=0.8"
                             f"[{a}x]")
                fw, fh = int(W * 0.94) // 2 * 2, int(H * 0.94) // 2 * 2
                parts.append(f"[{b}]scale={fw}:{fh}:force_original_aspect_"
                             f"ratio=decrease[{b}x]")
                parts.append(f"[{a}x][{b}x]overlay=(W-w)/2:(H-h)/2[{c}]")
                head = f"[{c}]null"
                rest = [x for x in chain if not x.startswith(("fps=",
                                                              "setsar"))]
            else:
                rest = chain
            o = ctx.uid("bro")
            body = ",".join([head] + rest + [f"format=yuv420p,trim="
                                              f"end_frame={n},{clock}"])
            parts.append(f"{body}[{o}]")
            return o
        return finish.window_composite(ctx, label, [(f0, f1)], build, uid=u)


def _contained(img, W, H):
    import cv2
    h, w = img.shape[:2]
    k = min(W / float(w), H / float(h))
    nw, nh = max(2, int(w * k)), max(2, int(h * k))
    out = np.zeros((H, W, 3), np.uint8)
    x, y = (W - nw) // 2, (H - nh) // 2
    out[y:y + nh, x:x + nw] = cv2.resize(img, (nw, nh),
                                         interpolation=cv2.INTER_AREA)
    return out


def ledger(edl):
    """The licence ledger of an EDL: one row per cutaway, in program
    order (what the export's credits and the reply read)."""
    rows = []
    for it in sorted((x for x in (edl or {}).get("fx") or []
                      if isinstance(x, dict) and x.get("type") == "broll"),
                     key=lambda x: float(x.get("start") or 0)):
        c = it.get("credit") or {}
        rows.append({"id": it.get("id"), "start": it.get("start"),
                     "end": it.get("end"), "provider": c.get("provider"),
                     "title": c.get("title"), "author": c.get("author"),
                     "license": c.get("license"),
                     "license_url": c.get("license_url"),
                     "page_url": c.get("page_url"),
                     "attribution_required": bool(
                         c.get("attribution_required")),
                     "line": c.get("line") or credit_line(c)})
    return rows


def credit_line(c):
    """'"Title" by Author, Licence (Provider)' — the ready-to-paste credit."""
    c = c or {}
    bits = []
    if c.get("title"):
        bits.append(f'"{str(c["title"])[:80]}"')
    if c.get("author"):
        bits.append(f"by {str(c['author'])[:60]}")
    lic = str(c.get("license") or "licence unknown")
    prov = str(c.get("provider") or "")
    head = " ".join(bits) if bits else "Footage"
    return f"{head}, {lic}" + (f" ({prov})" if prov else "")


BROLL = _Broll()
