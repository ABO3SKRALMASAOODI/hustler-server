"""Child process for tests/test_w1a_program_matte.py's screen-space case.

Runs with VALMERA_FX_PREVIEW=1c (track 1C's MotionItem.composite slot
registered, so a behind-subject graphic can carry composite.stage 'screen')
and prints one JSON line: the per-frame IoU of the picture's rectangle and
the SCREEN-space program matte under a camera push, and the same for the
world-space item.

    python tests/w1a_screen_probe.py <workdir>
"""

import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)


def main(work):
    import media
    import motion_layer
    import renderer
    import sidecars
    import w1a_support as sup
    from schemas import validate_edl
    from timeline import Timeline
    import matte_track
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
    base = {"keep": [[0.4, 6.4]],
            "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.4},
            "effects": {"zooms": [{"id": "z1", "start": 1.0, "end": 5.0,
                                   "mode": "push_in", "strength": 0.3}]}}
    tl = Timeline(base["keep"])
    out = {}
    for stage in ("screen", "world"):
        pic = copy.deepcopy(base)
        e = copy.deepcopy(base)
        e["motion"] = [{
            "id": "m1", "template": "word_slam", "start": 0.6, "end": 5.6,
            "layer": "behind_subject",
            "params": {"text": "W"},
            "behind": matte_track.program_behind(tl, 0.6, 5.6),
            "composite": {"stage": stage}}]
        paths = {}
        for name, edl, show in (("pic", pic, False), ("mat", e, True)):
            saved = motion_layer.composite_behind
            if show:
                def _show(parts, vlabel, mask_label, j, **kw):
                    parts.append(f"[{mask_label}]format=yuv420p[sh{j}m]")
                    parts.append(f"[{vlabel}][sh{j}m]overlay=0:0:"
                                 f"eof_action=pass[sh{j}]")
                    return f"sh{j}"
                motion_layer.composite_behind = _show
            p = os.path.join(work, f"{stage}_{name}.mp4")
            wd = os.path.join(work, f"w_{stage}_{name}")
            os.makedirs(wd, exist_ok=True)
            v = validate_edl(edl, info["duration"]).model_dump()
            renderer.render_edl(v, index, src, p, wd, True)
            motion_layer.composite_behind = saved
            paths[name] = p
        a = sup.frames(paths["pic"])
        b = sup.frames(paths["mat"])
        ious = []
        for k in range(15, 140):
            ious.append(sup.iou(a[k] > 175, b[k] > 127))
        out[stage] = ious
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1])
