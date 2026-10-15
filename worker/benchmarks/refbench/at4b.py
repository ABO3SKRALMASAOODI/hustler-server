"""Track 4B acceptance (ROADMAP 4B): the UI scene graph on real renders.

    python -m benchmarks.refbench.at4b AT-4B-1 AT-4B-2 AT-4B-3

AT-4B-1  HC-I explainer VO (cycle industry, 30 s) told as a UI scene: a
         three-state pill and cards that persist and morph across >= 3
         scenes, irises opening out of the element that caused them.
         Pass: an object present in >= 3 scenes whose rendered position
         agrees with the graph in every sampled frame (no jump: per-frame
         travel <= 5% of the height), and every iris origin recovered from
         the rendered pixels (circle fit to the opening arc) within 1 px.
AT-4B-2  a dashboard from a user CSV (FX-UI phone frame): every figure
         drawn equals the CSV (the graph's strings and the counters' final
         values), nothing unbound.
AT-4B-3  UI cards around a real recording (FX-UI valmera.io scroll) in a
         media slot that moves to the focus: owner board.

Internal only: fixtures stay in the scratch dir, nothing is uploaded.
"""

import copy
import csv
import io
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

from . import out_dir

FIX = os.path.expanduser("~/Documents/Valmera/.valmera/fixtures")
SCRATCH = os.path.expanduser("~/Documents/Valmera/.valmera/engines/w4/at4b")
BOARDS = os.path.expanduser(
    "~/Documents/Valmera/.valmera/refbench/boards/wave4/4B")
VO = os.path.join(FIX, "hc-i-voiceover/windows/explainer-30s/"
                  "explainer-30s-01-cycle-industry-benson")
FXUI = os.path.join(FIX, "FX-UI/valmera_io.mp4")
# the user's data for AT-4B-2 (a weekly parts sheet, as a user would paste)
USER_CSV = ("part,units,last_week\nFront forks,1000,940\nHandlebars,0,980\n"
            "Frames,1000,1000\nWheels,2000,1960\n")


def _plate(path, dur, audio=None):
    if os.path.exists(path):
        return path
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"color=c=0x101014:s=1080x1920:r=30", "-t", f"{dur}"]
    if audio:
        cmd += ["-i", audio, "-map", "0:v", "-map", "1:a", "-c:a", "aac",
                "-shortest"]
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
                "-map", "0:v", "-map", "1:a", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)
    return path


class _DB:
    def __init__(self, keys):
        self.keys = {k: {"storage_key": k, "duration_s": 4.0} for k in keys}

    def run(self, fn, *a, **kw):
        import edit_batch
        if fn is edit_batch.project_media_keys:
            return dict(self.keys)
        raise AssertionError(f"unexpected db call {fn}")


class _Ctx:
    def __init__(self, edl, idx, keys=(), msg=None):
        self.db = _DB(keys)
        self.project_id = 7
        self.duration = float(idx["video"]["duration"])
        self._edl = edl
        self.version = 1
        self.writes = []
        self.index = idx
        self.user_message = msg
        self.pending_images = []

    def latest_edl(self):
        return {"version": self.version, "json": copy.deepcopy(self._edl)}

    def write_edl(self, edl, desc):
        self.writes.append((edl, desc))
        self._edl = edl
        self.version += 1
        return f"EDL v{self.version - 1} -> v{self.version}: {desc}"


def _project(src, words):
    import qa_metrics
    from schemas import validate_edl
    info = qa_metrics.probe(src)
    idx = {"words": words, "silences": [], "sentences": [],
           "video": {"width": info["width"], "height": info["height"],
                     "fps": info["fps"], "duration": info["duration"]}}
    edl = validate_edl({"keep": [[0.0, float(info["duration"])]],
                        "frame": {"ratio": "9:16", "mode": "crop"}},
                       float(info["duration"])).model_dump()
    return edl, idx, info


def _render(edl, src, idx, out, locals_=None):
    import fxgraph
    import gradewin
    import renderer
    import qa_metrics
    from schemas import validate_edl
    dur = float(qa_metrics.probe(src)["duration"])
    e = validate_edl(copy.deepcopy(edl), dur).model_dump()
    work = out + ".work"
    os.makedirs(work, exist_ok=True)
    gradewin.ASSET_LOCALS.update(locals_ or {})
    os.environ["VALMERA_FX_ASSET_LOCALS"] = json.dumps(locals_ or {})
    rows = []
    tok = fxgraph.collect(rows)
    t = time.time()
    try:
        renderer.render_edl(e, idx, src, out, work, False,
                            asset_locals=dict(locals_ or {}))
    finally:
        fxgraph.stop_collecting(tok)
    return {"wall_s": round(time.time() - t, 2),
            "report": fxgraph.merge_report(rows), "edl": e}


def _motion_clip(item, W=1080, H=1920, fps=30.0):
    """The scene alone, as the motion engine draws it (RGBA): the
    measurement surface for positions and irises."""
    import motion_engine
    import motion_templates
    job = motion_templates.build_job(item, W, H, fps)
    clip = motion_engine.render_jobs([job], os.path.join(SCRATCH, "mg"))[0]
    return clip


def _alpha(clip, k, W=1080, H=1920):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", clip.path, "-vf",
                          f"select=eq(n\\,{k}),format=rgba,alphaextract",
                          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "gray", "-"], capture_output=True).stdout
    full = np.zeros((H, W), np.uint8)
    if len(raw) >= clip.w * clip.h:
        full[clip.y:clip.y + clip.h, clip.x:clip.x + clip.w] = \
            np.frombuffer(raw[:clip.w * clip.h], np.uint8).reshape(
                clip.h, clip.w)
    return full


def _rgb_frames(path, idx):
    import qa_metrics
    got, fr = qa_metrics.read_frames(path, frames=set(idx))
    return {k: np.asarray(f) for k, f in zip(got, fr)}


def _fit_circle(xs, ys):
    """Kasa least-squares circle: (cx, cy, r)."""
    A = np.c_[2 * xs, 2 * ys, np.ones(len(xs))]
    b = xs ** 2 + ys ** 2
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = sol
    return float(cx), float(cy), float(math.sqrt(max(0.0, c + cx * cx
                                                       + cy * cy)))


def _thumb(img, h=480):
    from PIL import Image
    im = Image.fromarray(np.asarray(img, np.uint8))
    return im.resize((max(1, int(im.width * h / im.height)), h))


def _board(path, rows, title):
    from PIL import Image, ImageDraw
    pad = 10
    h = max((im.height for _l, t in rows for _c, im in t), default=480)
    w = max((sum(im.width + pad for _c, im in t) + pad for _l, t in rows),
            default=600)
    sheet = Image.new("RGB", (max(900, w), 50 + len(rows) * (h + 64)),
                      (16, 16, 18))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), title, fill=(240, 240, 240))
    y = 50
    for label, tiles in rows:
        d.text((pad, y), label[:160], fill=(233, 180, 76))
        x = pad
        for cap, im in tiles:
            sheet.paste(im, (x, y + 18))
            d.text((x, y + 22 + im.height), cap[:40], fill=(220, 220, 220))
            x += im.width + pad
        y += h + 64
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sheet.save(path, quality=88)
    return path


def _verdict(case, metrics, extra=None):
    od = os.path.join(out_dir(), case)
    os.makedirs(od, exist_ok=True)
    v = {"case": case, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
         "metrics": metrics, **(extra or {})}
    v["pass"] = all(m.get("pass") is True for m in metrics.values())
    with open(os.path.join(od, "verdict.json"), "w") as f:
        json.dump(v, f, indent=1, default=str)
    print(json.dumps({k: v[k] for k in ("case", "metrics", "pass")},
                     indent=1, default=str))
    return v


def _m(value, op, bar, detail=None):
    ok = None
    if value is not None:
        ok = value <= bar if op == "<=" else value >= bar
    return {"value": None if value is None else round(float(value), 4),
            "bar": f"{op}{bar}", "pass": ok, "detail": detail or {}}


def _words():
    raw = json.load(open(os.path.join(VO, "words.json")))["words"]
    return [{"w": x["w"], "t0": float(x["s"]), "t1": float(x["e"]),
             "p": x.get("p")} for x in raw]


SCENES_1 = [
    {"word": "progress", "layout": "stack", "objects": [
        {"id": "pill", "kind": "pill", "label": "Tracking the work",
         "state": "live"},
        {"id": "staff", "kind": "card", "title": "The work staff",
         "body": "follows every part through the shops"}]},
    {"word": "numbers", "layout": "grid", "objects": [
        {"id": "pill", "kind": "pill", "label": "Tracking the work",
         "state": "live"},
        {"id": "staff", "kind": "card", "title": "The work staff",
         "body": "follows every part through the shops"},
        {"id": "forks", "kind": "metric", "label": "Front forks",
         "bind": {"row": "Front forks", "col": "units"}, "from": "staff"},
        {"id": "hb", "kind": "metric", "label": "Handlebars",
         "bind": {"row": "Handlebars", "col": "units"}}]},
    {"word": "chaos", "layout": "focus", "focus": "warn", "objects": [
        {"id": "pill", "kind": "pill", "label": "Tracking the work",
         "state": "idle"},
        {"id": "forks", "kind": "metric", "label": "Front forks",
         "bind": {"row": "Front forks", "col": "units"}},
        {"id": "hb", "kind": "metric", "label": "Handlebars",
         "bind": {"row": "Handlebars", "col": "units"}},
        {"id": "warn", "kind": "card", "title": "Without them, chaos",
         "body": "parts pile up while others never arrive", "from": "hb"}]},
    {"word": "might", "layout": "stack", "objects": [
        {"id": "pill", "kind": "pill", "label": "Tracking the work",
         "state": "idle"},
        {"id": "bikes", "kind": "metric", "label": "Bicycles ready",
         "value": "1,000", "from": "pill"},
        {"id": "nohb", "kind": "card", "title": "...and no handlebars",
         "body": "forthcoming at the last moment"}]},
]


def at_4b_1():
    import ui_scene
    from tools_ext import ui_tools
    case = "AT-4B-1"
    os.makedirs(SCRATCH, exist_ok=True)
    wav = os.path.join(VO, "explainer-30s-01-cycle-industry-benson.wav")
    src = _plate(os.path.join(SCRATCH, "vo_plate.mp4"), 30.1, wav)
    edl, idx, info = _project(src, _words())
    ctx = _Ctx(edl, idx)
    reply = ui_tools.add_ui_scene(ctx, scenes=SCENES_1, end=26.0,
                                  data=USER_CSV)
    print(reply)
    mo = next(m for m in ctx._edl["motion"] if m["template"] == "ui_scene")
    g = mo["params"]["graph"]
    out = os.path.join(SCRATCH, f"{case}.mp4")
    r = _render(ctx._edl, src, idx, out)
    # measurement surface: the scene alone, RGBA at 30 fps
    # (no backdrop: the objects' own alpha is what is measured)
    clip = _motion_clip(dict(mo, start=0.0, end=g["dur"], params=dict(
        mo["params"], backdrop="none")))
    fps = 30.0
    # 1. continuity: the pill through all its scenes
    pill = next(o for o in g["objects"] if o["id"] == "pill")
    scenes_in = sum(1 for k, t in enumerate(g["scenes"])
                    if ui_scene.state_at(pill, t + 0.6)[0] is not None)
    jump = ui_scene.continuity(g, "pill", fps)
    agree, worst = [], 0.0
    ks = list(range(0, int(g["dur"] * fps), 7))
    for k in ks:
        rect, op = ui_scene.state_at(pill, k / fps)
        if rect is None or op < 0.99:
            continue
        a = _alpha(clip, k)
        # the pill's ends are half discs: measure its straight middle
        sx = rect[3] / 2.0 + 4 if pill["kind"] == "pill" else 6
        x0, y0 = int(rect[0] + sx), int(rect[1] + 6)
        x1, y1 = int(rect[0] + rect[2] - sx), int(rect[1] + rect[3] - 6)
        inside = float((a[y0:y1, x0:x1] > 200).mean())
        agree.append(inside)
        worst = max(worst, 1.0 - inside)
    # 2. irises: the opening arc in the rendered pixels; its centre fitted
    # (least squares, the drawn radius schedule known) over every frame of
    # the opening where the arc crosses the tile
    import cv2
    iris_err, iris_fit = {}, {}
    R = int(math.ceil(18.0 * float(g.get("u") or 3.0))) + 3
    for ir in g["irises"]:
        o = next(x for x in g["objects"] if x["id"] == ir["id"])
        k0 = int(math.floor(ir["t"] * fps))
        base = _alpha(clip, k0)
        pts = []
        for dk in range(1, 18):
            k = k0 + dk
            rad = ui_scene.iris_radius(o, k / fps)
            rect, _op = ui_scene.state_at(o, k / fps)
            if rect is None or not rad:
                continue
            a = _alpha(clip, k)
            x0, y0 = int(math.ceil(rect[0])) + 3, int(math.ceil(rect[1])) + 3
            x1 = int(rect[0] + rect[2]) - 3
            y1 = int(rect[1] + rect[3]) - 3
            m = np.zeros_like(a, bool)
            m[y0:y1, x0:x1] = (a[y0:y1, x0:x1].astype(int)
                               - base[y0:y1, x0:x1].astype(int)) > 60
            if m.sum() < 40:
                continue
            edge = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_GRADIENT,
                                    np.ones((3, 3), np.uint8)) > 0
            inner = np.zeros_like(edge)
            inner[y0 + 2:y1 - 2, x0 + 2:x1 - 2] = True
            for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                inner[max(0, cy - R):cy + R, max(0, cx - R):cx + R] = False
            ys, xs = np.nonzero(edge & inner)
            if len(xs) < 12:
                continue
            # gradient pixels straddle the drawn boundary: their mean is the
            # boundary itself
            pts += [(float(x) + 0.5, float(y) + 0.5, rad)
                    for x, y in zip(xs, ys)]
        if len(pts) < 30:
            continue
        P = np.asarray(pts)
        c = np.array([ir["ox"] + 7.0, ir["oy"] - 7.0])   # off on purpose
        for _ in range(40):
            d = P[:, :2] - c
            n = np.hypot(d[:, 0], d[:, 1]) + 1e-9
            res = n - P[:, 2]
            J = -d / n[:, None]
            step, *_ = np.linalg.lstsq(J, -res, rcond=None)
            c = c + step
            if np.hypot(*step) < 1e-4:
                break
        d = P[:, :2] - c
        rms = float(np.sqrt(np.mean((np.hypot(d[:, 0], d[:, 1])
                                     - P[:, 2]) ** 2)))
        iris_err[ir["id"]] = round(float(math.hypot(c[0] - ir["ox"],
                                                     c[1] - ir["oy"])), 3)
        iris_fit[ir["id"]] = {"centre": [round(float(c[0]), 2),
                                         round(float(c[1]), 2)],
                              "origin": [ir["ox"], ir["oy"]],
                              "arc_points": len(pts),
                              "radius_rms_px": round(rms, 2)}
    fr = _rgb_frames(out, [int((t + 1.2 + mo["start"]) * fps)
                           for t in g["scenes"]] + [
        int((ir["t"] + 0.15 + mo["start"]) * fps) for ir in g["irises"]])
    rows = [("AT-4B-1 UI explainer (HC-I cycle-industry VO) — every scene "
             "and every iris, rendered", [(f"{k / fps:.2f}s", _thumb(f, 400))
                                          for k, f in sorted(fr.items())])]
    b = _board(os.path.join(BOARDS, case, "board.jpg"), rows,
               "AT-4B-1: persistent objects, morphs and irises")
    return _verdict(case, {
        "scenes_with_persistent_object": _m(scenes_in, ">=", 3),
        "max_frame_jump_px": _m(jump, "<=", 0.05 * 1920),
        "render_agrees_with_graph": _m(1.0 - worst, ">=", 0.95,
                                       {"frames": len(agree)}),
        "iris_origin_err_px": _m(max(iris_err.values()) if iris_err
                                 else None, "<=", 1.0, iris_fit),
    }, {"reply": reply, "board": b, "render": out,
        "report": [x for x in r["report"] if str(x.get("id", ""))
                   .startswith(("ui", "um"))]})


def at_4b_2():
    import ui_scene
    from tools_ext import ui_tools
    case = "AT-4B-2"
    src = _plate(os.path.join(SCRATCH, "plate8.mp4"), 8.0)
    edl, idx, info = _project(src, [])
    ctx = _Ctx(edl, idx, keys=("media/7/valmera_io.mp4",))
    scenes = [
        {"at": 0.0, "layout": "grid", "objects": [
            {"id": "t", "kind": "text", "text": "This week"},
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}},
            {"id": "hb", "kind": "metric", "label": "Handlebars",
             "bind": {"row": "Handlebars", "col": "units"}},
            {"id": "fr", "kind": "metric", "label": "Frames",
             "bind": {"row": "Frames", "col": "units"}},
            {"id": "wh", "kind": "metric", "label": "Wheels",
             "bind": {"row": "Wheels", "col": "units"}}]},
        {"at": 3.5, "layout": "focus", "focus": "chart", "objects": [
            {"id": "forks", "kind": "metric", "label": "Front forks",
             "bind": {"row": "Front forks", "col": "units"}},
            {"id": "hb", "kind": "metric", "label": "Handlebars",
             "bind": {"row": "Handlebars", "col": "units"}},
            {"id": "chart", "kind": "bars", "title": "Units last week",
             "rows_from": {"key": "part", "col": "last_week"},
             "from": "hb"}]}]
    reply = ui_tools.add_ui_scene(ctx, scenes=scenes, start=0.5, end=8.0,
                                  data=USER_CSV)
    print(reply)
    mo = next(m for m in ctx._edl["motion"] if m["template"] == "ui_scene")
    g = mo["params"]["graph"]
    data = list(csv.DictReader(io.StringIO(USER_CSV)))
    truth = {(r["part"], "units"): r["units"] for r in data}
    truth.update({(r["part"], "last_week"): r["last_week"] for r in data})
    shown, bad = [], []
    for o in g["objects"]:
        if o["kind"] == "metric":
            want = truth[(o["label"], "units")]
            shown.append((o["label"], o["value"], want))
            if o["value"].replace(",", "") != want or \
                    (o.get("count") and abs(o["count"]["to"]
                                            - float(want)) > 1e-9):
                bad.append(o["id"])
        if o["kind"] == "bars":
            for row in o["rows"]:
                want = truth[(row["label"], "last_week")]
                shown.append((row["label"], row["value"], want))
                if row["value"].replace(",", "") != want:
                    bad.append(f"{o['id']}:{row['label']}")
    unbound = ui_scene.check_numbers(
        ui_scene.bind_objects(copy.deepcopy(scenes), USER_CSV), [], None)
    out = os.path.join(SCRATCH, f"{case}.mp4")
    _render(ctx._edl, src, idx, out)
    fr = _rgb_frames(out, [int(2.5 * 30), int(7.0 * 30)])
    b = _board(os.path.join(BOARDS, case, "board.jpg"),
               [("AT-4B-2 dashboard from the user's CSV (rendered)",
                 [(f"{k / 30:.2f}s", _thumb(f, 480))
                  for k, f in sorted(fr.items())])],
               "AT-4B-2: every figure is the user's data")
    return _verdict(case, {
        "figures_not_equal_to_data": _m(len(bad), "<=", 0,
                                        {"shown": shown, "bad": bad}),
        "unbound_figures": _m(0 if unbound is None else 1, "<=", 0,
                              {"msg": unbound}),
    }, {"reply": reply, "board": b, "render": out})


def at_4b_3():
    from tools_ext import ui_tools
    case = "AT-4B-3"
    src = _plate(os.path.join(SCRATCH, "plate8.mp4"), 8.0)
    edl, idx, info = _project(src, [])
    key = "media/7/valmera_io.mp4"
    ctx = _Ctx(edl, idx, keys=(key,))
    scenes = [
        {"at": 0.0, "layout": "stack", "objects": [
            {"id": "pill", "kind": "pill", "label": "Upload a long talk",
             "state": "live"},
            {"id": "screen", "kind": "media", "aspect": 9 / 16 * 1.25,
             "title": "valmera.io"}]},
        {"at": 2.6, "layout": "focus", "focus": "screen", "objects": [
            {"id": "pill", "kind": "pill", "label": "Upload a long talk",
             "state": "done"},
            {"id": "screen", "kind": "media", "aspect": 9 / 16 * 1.25,
             "title": "valmera.io"},
            {"id": "c1", "kind": "card", "title": "Finds the story",
             "from": "screen"}]},
        {"at": 5.2, "layout": "stack", "objects": [
            {"id": "c1", "kind": "card", "title": "Finds the story"},
            {"id": "c2", "kind": "card", "title": "Shorts out, every word "
             "in motion", "from": "c1"},
            {"id": "screen", "kind": "media", "aspect": 9 / 16 * 1.25,
             "title": "valmera.io"}]}]
    reply = ui_tools.add_ui_scene(ctx, scenes=scenes, start=0.3, end=7.8,
                                  media={"screen": {"asset_key": key,
                                                    "fit": "cover"}})
    print(reply)
    out = os.path.join(SCRATCH, f"{case}.mp4")
    r = _render(ctx._edl, src, idx, out, {key: FXUI})
    fr = _rgb_frames(out, [int(t * 30) for t in (1.4, 2.9, 4.4, 6.0, 7.4)])
    b = _board(os.path.join(BOARDS, case, "board.jpg"),
               [("AT-4B-3 UI cards around the real valmera.io recording "
                 "(media slot follows every morph)",
                 [(f"{k / 30:.2f}s", _thumb(f, 480))
                  for k, f in sorted(fr.items())])],
               "AT-4B-3: owner board")
    rows = [x for x in r["report"] if str(x.get("id", "")).startswith("um")]
    return _verdict(case, {
        "slot_rendered": _m(float(bool(rows) and all(
            x.get("rung") == "full" for x in rows)), ">=", 1.0,
            {"report": rows}),
    }, {"reply": reply, "board": b, "render": out, "judge": "owner board"})


CASES = {"AT-4B-1": at_4b_1, "AT-4B-2": at_4b_2, "AT-4B-3": at_4b_3}


def main(argv):
    for case in argv or list(CASES):
        CASES[case]()


if __name__ == "__main__":
    main(sys.argv[1:])
