"""Child process for tests/test_w1a_program_matte.py's handle-matte case.

Runs with VALMERA_FX_PROBE=1 (the engine-only ``fx_probe`` type) and
replaces the probe's junction compiler with one that asks for the person
matte in prepare (as 1D's matte_cut does) and, at the cut, shows either a
side's PICTURE handle stream or its HANDLE MATTE (fxgraph.handle_matte) as
the 2h-frame window. Prints one JSON line: per side, the per-frame IoU of
the picture's rectangle and the matte inside the window — the frames past
the out-point / before the in-point included.

    python tests/w1a_junction_probe.py <workdir>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)


def main(work):
    import fxgraph
    import media
    import renderer
    import sidecars
    import w1a_support as sup
    from schemas import validate_edl
    src = sup.make_source(os.path.join(work, "src.mp4"))
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [])
    root = os.path.join(work, "store")
    sup.write_chunks(root, ref)
    sidecars.use_store(sidecars.LocalStore(root))
    index = {"words": [], "shots": [],
             "video": {"width": info["width"], "height": info["height"],
                       "fps": info["fps"], "duration": info["duration"],
                       "sha256": sha}}
    edl = {"keep": [[0.4, 3.0], [4.0, 7.0]],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.45},
           "fx": [{"id": "j1", "type": "fx_probe", "stage": "junction",
                   "at": 2.6, "op": "dissolve", "frames": 6}]}
    mode = {}

    def prepare(plan, item):
        fxgraph.request_matte(plan, item)
        return None

    def junction(ctx, items):
        side, what = mode["side"], mode["what"]
        keep, drop = (ctx.a_label, ctx.b_label) if side == "a" else \
            (ctx.b_label, ctx.a_label)
        ctx.parts.append(f"[{drop}]nullsink")
        u = ctx.uid("jt")
        if what == "pic":
            ctx.parts.append(f"[{keep}]null[{u}]")
            return u
        ctx.parts.append(f"[{keep}]nullsink")
        m = fxgraph.handle_matte(ctx, side)
        if m is None:
            raise RuntimeError("no handle matte")
        ctx.parts.append(f"[{m}]format=yuv420p[{u}]")
        return u
    fxgraph.PROBE.prepare = prepare
    fxgraph.PROBE.junction = junction
    out = {}
    for side in ("a", "b"):
        paths = {}
        for what in ("pic", "mat"):
            mode.update(side=side, what=what)
            p = os.path.join(work, f"{side}_{what}.mp4")
            wd = os.path.join(work, f"w_{side}_{what}")
            os.makedirs(wd, exist_ok=True)
            v = validate_edl(edl, info["duration"]).model_dump()
            renderer.render_edl(v, index, src, p, wd, True)
            paths[what] = p
        a = sup.frames(paths["pic"])
        b = sup.frames(paths["mat"])
        cut = int(round(2.6 * 25))
        out[side] = [sup.iou(a[k] > 175, b[k] > 127)
                     for k in range(cut - 6, cut + 6)]
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1])
