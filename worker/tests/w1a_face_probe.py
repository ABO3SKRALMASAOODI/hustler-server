"""Child process for tests/test_w1a_program_matte.py's face-mask case.

VALMERA_FX_PROBE=1; the probe's picture stage is replaced by one that
asks for the ``face`` mask in prepare (fxgraph.request_matte(..., "face"))
and shows it (fxgraph.mask(plan, "face")) as the picture. Prints one JSON
line: per frame of the window, the face mask's centroid and the centroid of
the top of the picture's rectangle (the synthetic face), in px; and
face_box at the window's middle against the rectangle's box.

    python tests/w1a_face_probe.py <workdir>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)


def main(work):
    import numpy as np
    import fxgraph
    import matte_track
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
    sup.write_faces(root, ref)
    sidecars.use_store(sidecars.LocalStore(root))
    index = {"words": [], "shots": [],
             "video": {"width": info["width"], "height": info["height"],
                       "fps": info["fps"], "duration": info["duration"],
                       "sha256": sha}}
    edl = {"keep": [[0.4, 6.4]],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.4},
           "fx": [{"id": "f1", "type": "fx_probe", "stage": "world",
                   "start": 0.5, "end": 5.5, "op": "noop"}]}
    seen = {}

    def prepare(plan, item):
        seen["info"] = fxgraph.request_matte(plan, item, "face", "world")
        return None

    def stage(ctx, items):
        m = ctx.mask("face")
        if m is None:
            raise RuntimeError("no face mask")
        mid = (float(items[0]["start"]) + float(items[0]["end"])) / 2
        seen["box"] = matte_track.face_box(ctx.plan, mid)
        u = ctx.uid("fm")
        ctx.parts.append(f"[{ctx.label}]nullsink")
        ctx.parts.append(f"[{m}]format=yuv420p[{u}]")
        return u
    fxgraph.PROBE.prepare = prepare
    fxgraph.PROBE.stage = stage
    outs = {}
    for name, e in (("pic", dict(edl, fx=[])), ("face", edl)):
        p = os.path.join(work, f"{name}.mp4")
        wd = os.path.join(work, f"w_{name}")
        os.makedirs(wd, exist_ok=True)
        v = validate_edl(e, info["duration"]).model_dump()
        renderer.render_edl(v, index, src, p, wd, True)
        outs[name] = sup.frames(p)
    rows = []
    for k in range(20, 130, 10):
        pic = outs["pic"][k] > 175
        ys, xs = np.nonzero(pic)
        if not len(xs):
            continue
        top = pic & (np.arange(pic.shape[0])[:, None]
                     < ys.min() + 0.375 * (ys.max() - ys.min()))
        fy, fx_ = np.nonzero(outs["face"][k] > 127)
        if not len(fx_):
            rows.append([k, None])
            continue
        ty, tx = np.nonzero(top)
        rows.append([k, round(float(fx_.mean() - tx.mean()), 2),
                     round(float(fy.mean() - ty.mean()), 2)])
    print(json.dumps({"rows": rows, "info": seen.get("info"),
                      "box": seen.get("box"),
                      "W": int(outs["pic"].shape[2]),
                      "H": int(outs["pic"].shape[1])}))


if __name__ == "__main__":
    main(sys.argv[1])
