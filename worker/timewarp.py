"""Time devices: the ``time`` fx compiler (track 4A; INTERFACES_W4.md §3.3).

Picture-only and duration-preserving: a window's frames are re-timed
inside ffmpeg (``finish.window_composite``: the program outside the window
is bit-exact) and the window still holds exactly its own number of frames,
so the program clock, every cut and the audio stay where they were.

  reverse      the window plays backwards (``reverse`` buffers the window:
               at most 3 s, fx_schemas/depth.py REVERSE_MAX_S)
  echo         trails: each frame mixed with ``count`` earlier frames
               ``spacing_f`` apart, weights decaying by ``decay`` (tmix)
  posterize    step-printing: every picture held ``frames`` frames
  stutter      the window's first ``frames`` frames looped ``repeats``
               times, then the window catches up on a cut
  interpolate  a slowed window without stuttering slow motion: the frames a
               speed span repeats are known exactly from the timeline (each
               program frame's source frame), so the window keeps one frame
               per source frame, stamped at the program time it SHOULD play
               (its source second through the timeline), and minterpolate
               synthesises the in-betweens on that clock (motion-
               compensated; flagged ``interp: minterpolate``). A source shot
               faster than the program (60p under a 30p program at 0.5x)
               already has real frames: the item reports "native" and draws
               nothing
  remap        an eased time remap inside the window that ends on the same
               frame: input frame g(i) shown at output frame i (g from
               ``curve``), built with setpts + fps on frame indices

Deterministic: every filter here is a function of the window's frames.
"""

import math

import fxprims

KINDS = ("reverse", "echo", "posterize", "stutter", "interpolate", "remap")
FILTERS = ("split", "trim", "setpts", "settb", "format", "reverse", "tmix",
           "fps", "loop", "minterpolate", "select", "nullsink", "null",
           "tpad", "overlay", "scale")
KEEPS_FRAME_TAGS = True
# Remap curves: g(x), x and g in 0..1 (input progress at output progress x).
_CURVES = {
    "ease_in": lambda x: x * x,
    "ease_out": lambda x: 1 - (1 - x) ** 2,
    "ease_in_out": lambda x: x * x * (3 - 2 * x),
    # a held frame for the first 40 %, then a rush to catch up
    "hold_then_catch": lambda x: 0.0 if x < 0.4 else ((x - 0.4) / 0.6) ** 1.6,
}
MS_PER_FRAME_MPX = {"reverse": 3.0, "echo": 6.0, "posterize": 1.0,
                    "stutter": 1.0, "interpolate": 45.0, "remap": 1.5}


def _state(plan):
    return plan.__dict__.setdefault("_w4_time", {})


def remap_frames(n, curve):
    """[input frame for each output frame] of an n-frame window."""
    g = _CURVES.get(curve or "ease_in_out", _CURVES["ease_in_out"])
    out = []
    for i in range(n):
        x = i / max(1, n - 1)
        out.append(min(n - 1, max(0, int(round(g(x) * (n - 1))))))
    return out


def stutter_frames(n, k, repeats):
    """Input frame per output frame: the first k frames ``repeats`` times,
    then the window from where the loop would have played (a catch-up
    cut)."""
    k = max(1, min(int(k), n))
    out = []
    for r in range(int(repeats)):
        out += list(range(k))
    tail = list(range(len(out), n))
    return (out + tail)[:n]


def posterize_frames(n, k):
    return [min(n - 1, (i // int(k)) * int(k)) for i in range(n)]


def unique_frames(src_frames):
    """[(window frame k, source frame)] of the FIRST program frame showing
    each source frame (``src_frames``: the source frame index of every
    window frame, in order)."""
    out = []
    prev = None
    for k, s in enumerate(src_frames):
        if s != prev:
            out.append((k, s))
            prev = s
    return out


def pts_segments(times, tol=1e-4):
    """Piecewise-linear runs of ``times`` (program seconds from the window
    start, one per kept frame): [(n0, t0, dt)] with time(N) = t0 + (N - n0)
    * dt on each run — a speed ramp has a few, a constant span one."""
    segs = []
    for n, t in enumerate(times):
        if segs:
            n0, t0, dt = segs[-1]
            if dt is None:
                segs[-1] = (n0, t0, t - t0)
                continue
            if abs(t0 + (n - n0) * dt - t) <= tol:
                continue
        segs.append((n, t, None))
    return [(n0, t0, dt if dt is not None else 0.0) for n0, t0, dt in segs]


def interp_chain(unique, times, rate, n, clock):
    """The filter chain (after a window's frames) that keeps ``unique``
    window frames, stamps kept frame N at ``times[N]`` and synthesises the
    program's frames at ``rate``: exactly n frames out."""
    # inside single quotes a filtergraph passes commas to the expression
    sel = "+".join(f"eq(n,{k})" for k in unique)
    segs = pts_segments(times)
    # the earliest run is the fallback; each later run takes over from its
    # own first frame
    n0, t0, dt = segs[0]
    expr = f"({t0:.6f}+(N-{n0})*{dt:.9f})"
    for n0, t0, dt in segs[1:]:
        piece = f"({t0:.6f}+(N-{n0})*{dt:.9f})"
        expr = f"if(gte(N,{n0}),{piece},{expr})"
    return (f"select='{sel}',setpts='{expr}/TB',"
            f"minterpolate=fps={rate:.6f}:mi_mode=mci:mc_mode=aobmc:"
            f"me_mode=bidir:vsbmc=1,tpad=stop=-1:stop_mode=clone,"
            f"trim=end_frame={n},{clock}")


def select_expr(index):
    """An ffmpeg expression mapping output frame ``n`` to the input frame
    ``index[n]`` as piecewise-constant runs (eq/between chains), kept
    small: runs of consecutive identical or +1 steps are merged."""
    runs = []                           # (out0, out1, in0, step)
    for i, v in enumerate(index):
        if runs:
            o0, o1, i0, st = runs[-1]
            exp = i0 + st * (i - o0)
            if i == o1 + 1 and v == exp:
                runs[-1] = (o0, i, i0, st)
                continue
            if i == o1 + 1 and o1 == o0 and v - i0 in (0, 1):
                runs[-1] = (o0, i, i0, v - i0)
                continue
        runs.append((i, i, v, 0))
    return runs


class _Time:
    FILTERS = FILTERS
    KEEPS_FRAME_TAGS = KEEPS_FRAME_TAGS

    def cost_ms(self, plan, item):
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        mpx = float(plan.W or 1080) * float(plan.H or 1920) / 1e6
        return 100.0 + (f1 - f0) * mpx * MS_PER_FRAME_MPX.get(
            item.get("kind"), 2.0)

    def prepare(self, plan, item):
        kind = item.get("kind")
        if kind not in KINDS:
            return ("omit", f"unknown time kind {kind!r}")
        f0, f1 = fxprims.frame_span(item["start"], item["end"], plan.fps)
        st = {"n": f1 - f0}
        if kind == "interpolate":
            factor, native = self._speed_under(plan, item)
            st["factor"] = factor
            if factor is None or factor >= 0.95:
                st["skip"] = "not a slowed window: nothing to interpolate"
            elif native:
                st["skip"] = ("the source has real frames for this speed "
                              "(native high frame rate)")
            if not st.get("skip"):
                pat = self._pattern(plan, item, f0, f1)
                if pat is None or len(pat[0]) < 2:
                    st["skip"] = "the window shows no repeated frames"
                else:
                    st["unique"], st["times"] = pat
            _state(plan)[item.get("id")] = st
            if st.get("skip"):
                # nothing to synthesise (real frames, or no repeats): the
                # picture is already the best it can be — not a degrade
                # the final-export gate should hold
                return ("full", st["skip"])
            return ("reduced", "interp: minterpolate (motion-compensated "
                    "in-betweens; RIFE is not on this lane)")
        _state(plan)[item.get("id")] = st
        return None

    def _pattern(self, plan, item, f0, f1):
        """([window frame of each source frame's first showing], [program
        seconds from the window start each SHOULD play at]) from the
        timeline, or None."""
        tl = plan.tl
        src_fps = float(getattr(plan.source, "fps", 0) or 0)
        if tl is None or not src_fps:
            return None
        r = plan.fps3
        sf = []
        for k in range(f1 - f0):
            st = tl.out_to_src((f0 + k) / r)
            if st is None:
                return None
            sf.append(int(math.floor(st * src_fps + 1e-6)))
        uniq = unique_frames(sf)
        start = f0 / r
        times = []
        for _k, s in uniq:
            o = tl.src_to_out(s / src_fps + 1e-6)
            if o is None:
                return None
            times.append(max(0.0, o - start))
        return [k for k, _s in uniq], times

    def _speed_under(self, plan, item):
        """(the slowest speed factor under the window, the source has real
        frames for it) — from the timeline's speed pieces."""
        tl = plan.tl
        if tl is None:
            return None, False
        try:
            a, b = float(item["start"]), float(item["end"])
            slow = None
            acc = 0.0
            for (s, e), pcs, off in zip(tl.segs, tl.pieces, tl.offsets):
                acc = off
                for ps, pe, f in pcs:
                    L = (pe - ps) / f
                    if acc < b and acc + L > a:
                        slow = f if slow is None else min(slow, f)
                    acc += L
            src_fps = float(getattr(plan.source, "fps", 0) or 0)
            native = bool(slow and src_fps and src_fps * slow >=
                          plan.fps3 * 0.98)
            return slow, native
        except Exception:  # noqa: BLE001
            return None, False

    def stage(self, ctx, items):
        import finish
        return finish.emit_each(ctx, items, self._emit)

    def _emit(self, ctx, label, it):
        import finish
        st = _state(ctx.plan).get(it.get("id")) or {}
        if st.get("skip"):
            return label
        f0, f1 = ctx.frames(it)
        n = f1 - f0
        if n < 2:
            return label
        kind = it["kind"]
        clock = finish.window_clock(ctx.fps)
        r = ctx.fps3

        def by_index(parts, src, index):
            """Output frame i = input frame index[i]: hold the window as a
            looped buffer and pick by index with select + setpts. The
            window is short (a device), so the frames fit in memory."""
            runs = select_expr(index)
            branches = []
            sp = ctx.uid("tws")
            parts.append(f"[{src}]split={len(runs)}"
                         + "".join(f"[{sp}{k}]" for k in range(len(runs))))
            for k, (o0, o1, i0, step) in enumerate(runs):
                cnt = o1 - o0 + 1
                b = ctx.uid("twb")
                if step == 1:
                    parts.append(f"[{sp}{k}]trim=start_frame={i0}:end_frame="
                                 f"{i0 + cnt},setpts=PTS-STARTPTS[{b}]")
                else:
                    parts.append(f"[{sp}{k}]trim=start_frame={i0}:end_frame="
                                 f"{i0 + 1},setpts=PTS-STARTPTS,"
                                 f"tpad=stop={cnt - 1}:stop_mode=clone[{b}]")
                branches.append(b)
            out = ctx.uid("two")
            parts.append("".join(f"[{b}]" for b in branches)
                         + f"concat=n={len(branches)}:v=1:a=0,{clock}[{out}]")
            return out

        if kind == "reverse":
            def build(parts, src, uid):
                out = ctx.uid("two")
                parts.append(f"[{src}]reverse,{clock}[{out}]")
                return out
        elif kind == "echo":
            e = it.get("echo") or {}
            cnt, sp_f = int(e.get("count") or 4), int(e.get("spacing_f") or 2)
            dec = float(e.get("decay") or 0.55)
            span = (cnt - 1) * sp_f + 1
            w = []
            for j in range(span):
                # newest frame last in tmix's window
                age = span - 1 - j
                w.append(f"{dec ** (age // sp_f):.4f}" if age % sp_f == 0
                         else "0")

            def build(parts, src, uid):
                out = ctx.uid("two")
                parts.append(f"[{src}]tmix=frames={span}:weights='"
                             f"{' '.join(w)}':scale=0,{clock}[{out}]")
                return out
        elif kind == "posterize":
            idx = posterize_frames(n, it.get("frames") or 3)

            def build(parts, src, uid):
                return by_index(parts, src, idx)
        elif kind == "stutter":
            idx = stutter_frames(n, it.get("frames") or 4,
                                 it.get("repeats") or 3)

            def build(parts, src, uid):
                return by_index(parts, src, idx)
        elif kind == "remap":
            idx = remap_frames(n, it.get("curve"))

            def build(parts, src, uid):
                return by_index(parts, src, idx)
        elif kind == "interpolate":
            def build(parts, src, uid):
                out = ctx.uid("two")
                parts.append(f"[{src}]" + interp_chain(
                    st["unique"], st["times"], r, n, clock) + f"[{out}]")
                return out
        else:
            raise ValueError(f"no emitter for time kind {kind}")
        return finish.window_composite(ctx, label, [(f0, f1)], build,
                                       uid=ctx.uid("tw"))


TIME = _Time()


def native_rate_ok(src_fps, factor, program_fps):
    """Does a source at ``src_fps`` give real frames at ``factor`` speed in
    a program at ``program_fps``?"""
    return bool(src_fps and factor and src_fps * factor >=
                program_fps * 0.98)


def slowmo_quality(src_fps, factor, program_fps):
    """'native' | 'interpolate' | 'repeat' — what a slowed window shows."""
    if native_rate_ok(src_fps, factor, program_fps):
        return "native"
    return "interpolate" if factor < 0.95 else "native"

