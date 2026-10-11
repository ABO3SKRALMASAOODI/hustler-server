"""Engine-mode cases: a VFL window's EDL + ops, rendered locally.

Everything runs on the FIXTURE's clock (file seconds): the window's keep is
``[t0 - file_t0, t1 - file_t0]``, the ASR words are shifted the same way,
so a render needs nothing but the fixture store.

    base EDL   ``vfl_default``: one keep over the window, framed to the case
               aspect (crop, focus from ``edl.focus`` or centre), captions
               default; or ``baseline:<file>`` under <fixtures>/baselines/.
    ops        ``apply_edit_batch`` operations (edit_batch.apply_batch),
               plus harness ops the agent tools would perform at write time:
                 {"harness": "behind_matte", "layer": "texts"|"motion",
                  "id": "<item id>"}   measure the subject matte for that
                  item (matte.measure_and_build, person model when the RVM
                  file is present) and attach ``behind``, exactly as
                  add_text_behind / add_motion_graphic(behind_subject) do.
    render     renderer.render_edl on the fixture; a CONTROL render of the
               base EDL is made too (brand integrity, duration, locality).

Needs the worker runtime (renderer, Chromium for motion items, ffmpeg).
"""

import copy
import hashlib
import json
import os
import time

from . import fixtures_dir, vfl as vflmod


def asr_words(win):
    """ASR words of the window on the FILE clock ([] when none stored)."""
    p = os.path.join(fixtures_dir(), "asr", win["vfl"], f"{win['name']}.json")
    if not os.path.exists(p):
        return []
    with open(p) as fh:
        data = json.load(fh)
    off = float(win["file_t0"]) if data.get("clock", "source") == "source" \
        else 0.0
    out = []
    for w in data.get("words", []):
        out.append({"w": w["w"], "t0": round(float(w["t0"]) - off, 3),
                    "t1": round(float(w["t1"]) - off, 3),
                    "p": w.get("p", 1.0)})
    return out


def index_for(win):
    return {"words": asr_words(win),
            "video": {"width": win["width"], "height": win["height"],
                      "duration": round(win["file_span"][1]
                                        - win["file_span"][0], 3),
                      "fps": win["fps"]}}


def window_edl(win, aspect="9:16", focus=None, keep=None):
    """The window as one keep (file clock), framed to ``aspect``."""
    a = vflmod.file_time(win, win["t"][0])
    b = vflmod.file_time(win, win["t"][1])
    edl = {"keep": keep or [[a, b]]}
    if aspect and aspect != "source":
        fr = {"ratio": aspect, "mode": "crop"}
        if focus:
            fr["focus_x"], fr["focus_y"] = focus
        edl["frame"] = fr
    return edl


def base_edl(case, win):
    spec = case.get("edl") or {}
    base = spec.get("base", "vfl_default")
    if base.startswith("baseline:"):
        p = os.path.join(fixtures_dir(), "baselines", base.split(":", 1)[1])
        with open(p) as fh:
            data = json.load(fh)
        return data.get("edl", data)
    keep = spec.get("keep")
    if keep:
        keep = [[vflmod.file_time(win, a), vflmod.file_time(win, b)]
                for a, b in keep]
    return window_edl(win, case.get("aspect", "9:16"), spec.get("focus"),
                      keep)


def _validated(edl, duration):
    from schemas import validate_edl
    return validate_edl(edl, duration).model_dump()


def _matte_geometry(edl, win):
    """(fit filter, w, h) for a mask that composites onto the render — the
    same computation as agent_tools._matte_geometry."""
    import config
    import renderer
    fr = edl.get("frame") or {}
    W, H = renderer.frame_dims(float(win["width"]), float(win["height"]),
                               fr.get("ratio"))
    mode = (fr.get("mode") or "crop") if fr.get("ratio") not in (
        None, "source") else "crop"
    focus = None
    if fr.get("focus_x") is not None or fr.get("focus_y") is not None:
        focus = (fr.get("focus_x"), fr.get("focus_y"))
    target_h = min(int(H), int(config.PROXY_HEIGHT))
    k = target_h / float(H)
    w = max(16, int(round(W * k / 2)) * 2)
    h = max(16, int(round(H * k / 2)) * 2)
    return renderer.frame_fit_filter(mode, w, h, focus, pad_color="black",
                                     picture=fr.get("picture")), w, h


def behind_matte(edl, layer, item_id, win, workdir, locals_):
    """Attach a measured subject matte to texts/motion item ``item_id``."""
    import matte
    from schemas import subject_matte_geom
    from timeline import Timeline
    items = edl.get(layer) or []
    item = next(i for i in items if i.get("id") == item_id)
    tl = Timeline(edl["keep"], edl.get("inserts") or [],
                  edl.get("speed") or [])
    s, e = float(item["start"]), float(item["end"])
    a_src = tl.out_to_src(s)
    b_src = tl.out_to_src(max(s, e - 0.02))
    src_start, src_end = round(min(a_src, b_src), 2), \
        round(max(a_src, b_src), 2)
    fit, mw, mh = _matte_geometry(edl, win)
    fp = hashlib.sha256(json.dumps(
        [win["sha256"], src_start, src_end, fit, mw, mh, matte.VERSION],
        sort_keys=True).encode()).hexdigest()
    out = os.path.join(workdir, f"matte_{fp[:10]}.mp4")
    stats = matte.measure_and_build(win["path"], out, src_start,
                                    src_end - src_start, extra_vf=fit,
                                    width=mw, height=mh, allow_model=True)
    if not stats.get("ok"):
        raise RuntimeError(f"matte refused: {stats.get('why')}")
    key = f"local/matte/{fp[:16]}.mp4"
    locals_[key] = out
    item["behind"] = {"asset_key": key, "src_start": src_start,
                      "src_end": src_end, "fp": fp,
                      "coverage": stats.get("coverage"),
                      "fps": stats.get("fps"), "method": stats.get("method"),
                      "geom": subject_matte_geom(edl.get("frame"))}
    if layer == "motion":
        item["layer"] = "behind_subject"
    return {"item": item_id, "mask": out, "stats": {
        k: v for k, v in stats.items() if isinstance(v, (int, float, str))}}


def build(case, win, workdir):
    """(control_edl, case_edl, locals, notes): the validated EDLs."""
    import edit_batch
    os.makedirs(workdir, exist_ok=True)
    duration = float(win["file_span"][1] - win["file_span"][0])
    base = _validated(base_edl(case, win), duration)
    edl = copy.deepcopy(base)
    locals_, notes = {}, []
    ops = (case.get("edl") or {}).get("ops") or []
    batch = [op for op in ops if "harness" not in op]
    if batch:
        edl = edit_batch.apply_batch(edl, batch, duration, set())
        if isinstance(edl, tuple):
            edl = edl[0]
    for op in ops:
        if op.get("harness") == "behind_matte":
            notes.append(behind_matte(edl, op.get("layer", "texts"),
                                      op["id"], win, workdir, locals_))
    edl = _validated(edl, duration)
    return base, edl, locals_, notes


def render(edl, win, out, workdir, *, preview=True, want_wm=None,
           locals_=None, suppress_outro=False, placements=None):
    """renderer.render_edl on the fixture; returns (seconds, wall_s). With
    ``placements`` (a dict), the motion clips the renderer overlays are
    recorded into it (``ink.capture_overlays``). The free-tier mark is drawn
    as production draws it (``renderer.wants_watermark``: finals only)
    unless ``want_wm`` says otherwise."""
    import renderer
    from . import ink
    if want_wm is None:
        want_wm = not preview
    os.makedirs(workdir, exist_ok=True)
    t = time.time()
    with ink.capture_overlays() as placed:
        d = renderer.render_edl(edl, index_for(win), win["path"], out,
                                workdir, preview, want_wm=want_wm,
                                asset_locals=locals_ or {},
                                suppress_outro=suppress_outro)
    if placements is not None:
        placements.update(placed)
    return d, round(time.time() - t, 2)


PROBE_COLOUR = "#FF00FF"


def colour_probe(edl, item_id, colour=PROBE_COLOUR):
    """The same EDL with one TEXT item's colour swapped: the pixels that
    differ between a render of each are exactly that item's visible ink
    (caption muting, layout and every other layer are identical)."""
    e = copy.deepcopy(edl)
    for it in e.get("texts") or []:
        if it.get("id") == item_id:
            it["color"] = colour
            if it.get("accent_color"):
                it["accent_color"] = colour
            return e
    raise KeyError(f"no text item {item_id}")


def front_of(edl):
    """The same EDL with every behind-subject item drawn in front: what the
    glyphs look like un-occluded (texts lose ``behind``; motion items move
    to ``above_captions``)."""
    e = copy.deepcopy(edl)
    for layer in ("motion", "texts"):
        for it in e.get(layer) or []:
            if it.get("behind"):
                it.pop("behind", None)
                if layer == "motion":
                    it["layer"] = "above_captions"
    return e


def window_for_case(case):
    foot = case["footage"][int((case.get("edl") or {}).get("window", 0))]
    if "window" in foot:
        return vflmod.window(foot["vfl"], foot["window"])
    name = vflmod.find(foot["vfl"], *foot["t"])
    if not name:
        raise KeyError(f"no VFL window of {foot['vfl']} holds {foot['t']}")
    return vflmod.window(foot["vfl"], name)
