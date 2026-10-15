"""Track 4B compiler: EDL.fx ``ui_media`` — footage in a UI scene's media
slot (fx_schemas/ui.py; worker/devices/INTERFACES_W4.md §3.4).

The slot's rectangle comes from the SAME solved graph the ``ui_scene``
template draws (``ui_scene.state_at``), so the footage follows every morph
of the slot exactly. ``prepare`` writes the window as one RGBA intermediate
(the footage cover/contain-fitted into the slot, its rounded corners
anti-aliased, transparent elsewhere); the stage overlays it on the program
inside the window (``finish.window_composite``: bit-exact outside). The
scene's own chrome, drawn later on the below-captions motion layer, leaves
the slot open; the captions stay on top of both.
"""

import os

import numpy as np

import fxprims

FILTERS = ("split", "trim", "setpts", "settb", "format", "scale", "overlay",
           "nullsink")
KEEPS_FRAME_TAGS = True
MS_PER_MPX = 7.0


def _state(plan):
    return plan.__dict__.setdefault("_w4_ui_media", {})


def _motion_item(plan, mid):
    import subject_fx
    for m in (subject_fx.plan_edl(plan) or {}).get("motion") or []:
        if isinstance(m, dict) and m.get("id") == mid:
            return m
    return None


def _round_mask(w, h, r):
    """An anti-aliased rounded-rect alpha (0..1) of w x h."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32) + 0.5
    dx = np.maximum(0.0, np.maximum(r - xx, xx - (w - r)))
    dy = np.maximum(0.0, np.maximum(r - yy, yy - (h - r)))
    d = np.sqrt(dx * dx + dy * dy)
    return np.clip(r + 0.5 - d, 0.0, 1.0) if r > 0 else np.ones((h, w),
                                                                 np.float32)


class _Frames:
    """Footage frames on the program clock: the asset (video or still) or
    the main video from ``src_t``, decoded at the window's rate and
    STREAMED — one frame in memory at a time (a 12 s portrait recording
    held whole is ~2 GB; the lanes are small). ``at(i)`` is called with
    i = 0, 1, 2, ...; past the clip's end it holds the last frame."""

    def __init__(self, path, start_s, n, fps, still=False, w=960):
        import subprocess
        self.frames = []
        self._proc = None
        self._last = None
        self._i = -1
        if still:
            import depth_fx
            img = depth_fx._decode_frame(path, 0.0)
            self.frames = [img] if img is not None else []
            return
        import qa_metrics
        info = qa_metrics.probe(path)
        sw, sh = int(info.get("width") or 0), int(info.get("height") or 0)
        if not sw or not sh:
            return
        self._w = int(w)
        self._h = int(round(w * sh / float(sw) / 2.0)) * 2
        cmd = ["ffmpeg", "-v", "error", "-nostdin"]
        if start_s > 0:
            cmd += ["-ss", f"{start_s:.3f}"]
        cmd += ["-i", path, "-frames:v", str(n), "-vf",
                f"fps={fps:.4f},scale={self._w}:{self._h}", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "-"]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)
        if self._read() is not None:
            self.frames = [self._last]       # non-empty: the clip is readable

    def _read(self):
        if self._proc is None:
            return None
        fs = self._w * self._h * 3
        raw = self._proc.stdout.read(fs)
        if len(raw) < fs:
            self.close()
            return None
        self._last = np.frombuffer(raw, np.uint8).reshape(self._h, self._w,
                                                          3)
        self._i += 1
        return self._last

    def at(self, i):
        if self._proc is None and self._last is None:
            return self.frames[0] if self.frames else None
        while self._i < i and self._proc is not None:
            if self._read() is None:
                break
        return self._last

    def close(self):
        p, self._proc = self._proc, None
        if p is not None:
            try:
                p.stdout.close()
                p.kill()
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass


class _UiMedia:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        return 250.0 + (f1 - f0) * mpx * MS_PER_MPX

    def landing(self, plan, item):
        return [fxprims.frame_at(float(item["start"]), plan.fps)]

    def prepare(self, plan, item):
        import cv2
        import depth_fx
        import faultinject
        import subject_fx
        import ui_scene
        if not plan.W or not plan.H or not plan.workdir:
            return ("omit", "no geometry or workdir for the slot")
        if faultinject.fault("oom:fx_precompute"):
            raise MemoryError("the slot precompute was OOM-killed")
        mo = _motion_item(plan, item.get("motion_id"))
        graph = ((mo or {}).get("params") or {}).get("graph") or {}
        obj = next((o for o in graph.get("objects") or []
                    if o.get("id") == item.get("slot")), None)
        if mo is None or obj is None:
            return ("omit", "the UI scene or its slot is gone")
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        n = f1 - f0
        if n < 1:
            return ("omit", "an empty window")
        if depth_fx.audio_only(plan):
            _state(plan)[item["id"]] = {"audio_only": True}
            return None
        W, H = int(plan.W), int(plan.H)
        k = W / float(graph.get("W") or W)
        src = item.get("source") or {}
        try:
            if src.get("kind") == "asset":
                path = subject_fx.asset_path(plan, src.get("asset_key"))
                still = os.path.splitext(str(src.get("asset_key")))[1] \
                    .lower() in (".png", ".jpg", ".jpeg", ".webp")
                frames = _Frames(path, float(src.get("offset_s") or 0.0), n,
                                 plan.fps3, still=still)
            else:
                path = subject_fx.render_env(plan).get("src_path")
                if not path:
                    return ("omit", "this render names no main video")
                frames = _Frames(path, float(src.get("src_t") or 0.0), n,
                                 plan.fps3)
        except Exception as e:  # noqa: BLE001
            return ("omit", f"the slot footage could not be read "
                            f"({str(e)[:100]})")
        if not frames.frames:
            return ("omit", "the slot footage could not be read")
        os.makedirs(plan.workdir, exist_ok=True)
        out = os.path.join(plan.workdir, f"w4u_{item['id']}_{n}.mkv")
        w = depth_fx.ClipWriter(out, W, H, plan.fps3, "rgba")
        u = float(graph.get("u") or 3.0) * k
        t_item0 = float(item["start"]) - float(mo["start"])
        for i in range(n):
            t = t_item0 + i / plan.fps3
            rect, op = ui_scene.state_at(obj, t)
            canvas = np.zeros((H, W, 4), np.uint8)
            if rect is not None and op > 0.001:
                x, y = rect[0] * k, rect[1] * k
                rw, rh = max(2, int(round(rect[2] * k))), \
                    max(2, int(round(rect[3] * k)))
                fr = frames.at(i)
                if item.get("fit") == "contain":
                    pic = np.zeros((rh, rw, 3), np.uint8)
                    fh, fw = fr.shape[:2]
                    s = min(rw / float(fw), rh / float(fh))
                    nw, nh = max(1, int(fw * s)), max(1, int(fh * s))
                    pic[(rh - nh) // 2:(rh - nh) // 2 + nh,
                        (rw - nw) // 2:(rw - nw) // 2 + nw] = cv2.resize(
                        fr, (nw, nh), interpolation=cv2.INTER_AREA)
                else:
                    pic = depth_fx.cover_crop(fr, rw, rh)
                s_ = min(rect[2] / max(1e-6, obj["design"][2]),
                         rect[3] / max(1e-6, obj["design"][3]))
                a = _round_mask(rw, rh, 18.0 * u * min(1.0, s_)) * op
                xi, yi = int(round(x)), int(round(y))
                x0, y0 = max(0, xi), max(0, yi)
                x1, y1 = min(W, xi + rw), min(H, yi + rh)
                if x1 > x0 and y1 > y0:
                    sub = (slice(y0 - yi, y1 - yi), slice(x0 - xi, x1 - xi))
                    canvas[y0:y1, x0:x1, :3] = pic[sub]
                    canvas[y0:y1, x0:x1, 3] = np.clip(a[sub] * 255 + 0.5,
                                                      0, 255)
            w.write(canvas)
        w.close()
        frames.close()
        _state(plan)[item["id"]] = {"clip": out}
        plan.add_input(f"w4u:{item['id']}", ["-i", out])
        return None

    def stage(self, ctx, items):
        import finish
        return finish.emit_each(ctx, items, self._emit)

    def _emit(self, ctx, label, it):
        import finish
        st = _state(ctx.plan).get(it.get("id")) or {}
        if st.get("audio_only"):
            return label
        clip = ctx.input_label(f"w4u:{it['id']}")
        if not st.get("clip") or clip is None:
            raise ValueError("the slot intermediate was never bound")
        f0, f1 = ctx.frames(it)
        n = f1 - f0
        clock = finish.window_clock(ctx.fps)

        def build(parts, src, uid):
            a, o = ctx.uid("uma"), ctx.uid("umo")
            parts.append(f"[{clip}]trim=end_frame={n},{clock},format=yuva420p"
                         f"[{a}]")
            parts.append(f"[{src}][{a}]overlay=0:0:format=auto,"
                         f"format=yuv420p[{o}]")
            return o
        return finish.window_composite(ctx, label, [(f0, f1)], build,
                                       uid=ctx.uid("um"))


UI_MEDIA = _UiMedia()
