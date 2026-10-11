"""Stand-ins for the Wave 1 seams track 1B consumes before they merge.

Track 1B codes against the pinned interfaces (INTERFACES_W1.md §1): until
track 1A merges, ``fxgraph.matte_info`` reads "absent" and
``program_matte`` returns None, so every subject effect takes its
substitute rung. These stand-ins let 1B's tests and acceptance renders
exercise the FULL rung through the real renderer, exactly as 1A will feed
it:

* ``install_matte(mask_path)``: a SOURCE-space person matte (a gray video
  with the source's frame rate and size, 255 inside) mapped through the
  same keep spans and frame fit as the picture — the program matte 1A
  builds from ``matte.person`` sidecars. ``matte_info`` reads ready,
  ``request_matte`` records the request, the ``person`` mask provider
  returns a fresh gray pad per call, and the render's sidecar readiness
  (``fxgraph._sidecar_status``) reads ready for ``matte.person``.
* ``install_hue()``: 1E's ``hue:<rrggbb>`` provider, keyed from the
  picture itself (a colorkey stand-in for ``gradewin.hue_mask``).

Test-only: production code never imports this module.
"""

import fxgraph
import fxprims


def _geometry(plan):
    """(keep spans, fit chain) the picture is built with on this render."""
    import renderer
    import subject_fx
    edl = subject_fx.plan_edl(plan) or {}
    fr = edl.get("frame") or {}
    ratio = fr.get("ratio")
    mode = (fr.get("mode") or "crop") if ratio not in (None, "source") \
        else "crop"
    focus = None
    if fr.get("focus_x") is not None or fr.get("focus_y") is not None:
        focus = (fr.get("focus_x"), fr.get("focus_y"))
    fit = renderer.frame_fit_filter(mode, int(plan.W), int(plan.H), focus,
                                    pad_color="black",
                                    picture=fr.get("picture"))
    segs = list(getattr(plan.tl, "segs", None) or [])
    return segs, fit


def _program_pad(ctx, plan, key, src_fps):
    """A fresh program-space gray pad from the SOURCE-space video bound as
    input ``key``: every keep span trimmed on the source clock, joined,
    brought to the render rate and fitted like the picture."""
    lab = plan.input_label(key)
    if lab is None:
        return None
    segs, fit = _geometry(plan)
    u = ctx.uid("st1a")
    parts = ctx.parts
    pieces = []
    for i, (a, b) in enumerate(segs or [(0.0, plan.program_s)]):
        fa = int(round(float(a) * src_fps))
        fb = int(round(float(b) * src_fps))
        p = f"{u}k{i}"
        parts.append(f"[{lab}]trim=start_frame={fa}:end_frame={fb},"
                     f"setpts=PTS-STARTPTS[{p}]")
        pieces.append(p)
    joined = f"{u}j"
    if len(pieces) == 1:
        parts.append(f"[{pieces[0]}]null[{joined}]")
    else:
        parts.append("".join(f"[{p}]" for p in pieces)
                     + f"concat=n={len(pieces)}:v=1:a=0[{joined}]")
    out = f"{u}o"
    F = fxprims.fps3(plan.fps)
    parts.append(f"[{joined}]fps={F:.3f},format=yuv420p,{fit},"
                 f"scale={int(plan.W)}:{int(plan.H)},format=gray,"
                 f"setpts=N/{F:.3f}/TB,"
                 f"tpad=stop={int(plan.n_frames) + 2}:stop_mode=clone,"
                 f"trim=end_frame={int(plan.n_frames)}[{out}]")
    return out


def install_matte(monkeypatch, mask_path, src_fps, *, windows=None,
                  status="ready", ready_sidecars=True):
    """Patch fxgraph so the program matte comes from ``mask_path``."""
    key = "stub1a:person"

    def matte_info(plan, label="person"):
        return {"label": label, "status": status, "source": "rvm",
                "reason": "test stand-in for track 1A",
                "windows": list(windows or []), "qa": {}}

    def request_matte(plan, item, label="person", space="world"):
        plan.matte_requests.setdefault(
            (str(label), "screen" if space == "screen" else "world"),
            []).append((item or {}).get("id"))
        plan.add_input(key, ["-i", mask_path])
        return matte_info(plan, label)

    def person(plan, name, space, ctx):
        if ctx is None:
            return None
        return _program_pad(ctx, plan, key, src_fps)

    real_status = fxgraph._sidecar_status

    def sidecar_status(item, source, plan):
        got = real_status(item, source, plan)
        if ready_sidecars and "matte.person" in got:
            got = dict(got, **{"matte.person": "ready"})
        return got

    monkeypatch.setattr(fxgraph, "matte_info", matte_info)
    monkeypatch.setattr(fxgraph, "request_matte", request_matte)
    monkeypatch.setitem(fxgraph._MASK_PROVIDERS, "person", person)
    monkeypatch.setattr(fxgraph, "_sidecar_status", sidecar_status)


def install_hue(monkeypatch, similarity=0.32, blend=0.12):
    """1E's ``hue:<rrggbb>`` provider, the way INTERFACES_W1 §1.3 pins it:
    derived from the picture (``ctx.label`` is split and rebound to the
    pass-through branch). The stand-in keys with ffmpeg ``colorkey``."""
    import sys
    import types
    mod = types.ModuleType("w1b_stub_gradewin")

    def hue_mask(plan, name, space, ctx):
        if ctx is None or ":" not in name:
            return None
        hexcol = name.split(":", 1)[1].lstrip("#")
        u = ctx.uid("st1e")
        ctx.parts.append(f"[{ctx.label}]split[{u}p][{u}k]")
        ctx.parts.append(f"[{u}k]format=rgba,colorkey=color=0x{hexcol}:"
                         f"similarity={similarity}:blend={blend},"
                         f"alphaextract,negate,format=gray[{u}m]")
        ctx.label = f"{u}p"
        return f"{u}m"
    mod.hue_mask = hue_mask
    monkeypatch.setitem(sys.modules, "w1b_stub_gradewin", mod)
    monkeypatch.setitem(fxgraph.MASK_PEERS, "hue",
                        "w1b_stub_gradewin:hue_mask")


class Patch:
    """A minimal monkeypatch for child processes (no pytest there)."""

    def __init__(self):
        self._undo = []

    def setattr(self, obj, name, value):
        self._undo.append((setattr, obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def setitem(self, d, key, value):
        had = key in d
        old = d.get(key)
        self._undo.append(("item", d, key, (had, old)))
        d[key] = value

    def undo(self):
        for entry in reversed(self._undo):
            if entry[0] == "item":
                _, d, key, (had, old) = entry
                if had:
                    d[key] = old
                else:
                    d.pop(key, None)
            else:
                fn, obj, name, old = entry
                fn(obj, name, old)
        self._undo = []
