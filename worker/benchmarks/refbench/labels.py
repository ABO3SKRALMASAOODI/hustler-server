"""Labels for VFL windows: store layout, reader, bootstrap, CVAT round trip.

Layout (INTERFACES §8.4), all under the fixture store, never in git:

    <fixtures>/labels/<vfl>/<window>/<label>/
        manifest.json         frames, clocks, method, review notes
        000123.png            person / subject masks: 8-bit, 255 = inside
        points.json           fingertips / plane corners: {frame: {name: [x, y]}}
        onsets.json           syllable onsets: [{word, t0, t1, syllables: [t]}]

Frame numbers are the FIXTURE stream's decode order (0 = first frame,
``qa_metrics.read_frames`` / ffprobe / framemd5 numbering): frame k has PTS
``file_start + k/fps`` and source time ``file_start + k/fps + file_t0``.
Reference labels (AT-0D-2) live in ``labels/refs/<ref>/<label>.json``.

CVAT (cvat.ai, "CVAT for images 1.1" XML) is the hand-correction tool:
``export_cvat`` writes the frames as JPEGs plus an annotations.xml whose
polygons trace the current masks; after correcting in CVAT, ``import_cvat``
rasterises the polygons (and CVAT RLE masks) back into the PNG store and
records the import in the manifest.
"""

import json
import os
import xml.etree.ElementTree as ET

import numpy as np

from . import fixtures_dir

MASK_KINDS = ("person", "subject", "object")


def label_dir(vfl, window, label):
    return os.path.join(fixtures_dir(), "labels", vfl, window, label)


def read_manifest(vfl, window, label):
    p = os.path.join(label_dir(vfl, window, label), "manifest.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def write_manifest(vfl, window, label, man):
    d = label_dir(vfl, window, label)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1)


def mask_frames(vfl, window, label="person"):
    d = label_dir(vfl, window, label)
    try:
        return sorted(int(n[:-4]) for n in os.listdir(d)
                      if n.endswith(".png") and n[:-4].isdigit())
    except OSError:
        return []


def read_mask(vfl, window, frame, label="person"):
    """bool (h, w) mask, or None."""
    from PIL import Image
    p = os.path.join(label_dir(vfl, window, label), f"{frame:06d}.png")
    if not os.path.exists(p):
        return None
    return np.asarray(Image.open(p).convert("L")) >= 128


def write_mask(vfl, window, frame, mask, label="person"):
    from PIL import Image
    d = label_dir(vfl, window, label)
    os.makedirs(d, exist_ok=True)
    a = (np.asarray(mask) > 0).astype(np.uint8) * 255 if \
        np.asarray(mask).dtype == bool else \
        np.where(np.asarray(mask) >= 128, 255, 0).astype(np.uint8)
    Image.fromarray(a).save(os.path.join(d, f"{frame:06d}.png"),
                            optimize=True)


def read_masks(vfl, window, label="person"):
    return {f: read_mask(vfl, window, f, label)
            for f in mask_frames(vfl, window, label)}


def read_void(vfl, window, label="person"):
    """{frame: bool mask} of the label's don't-care pixels
    (``<label>_void/``), for ``qa_metrics.matte_iou(void=...)``: IoU against
    these labels is defined WITHOUT them. Frames without a void file are
    absent (every pixel counts)."""
    return {f: read_mask(vfl, window, f, label + "_void")
            for f in mask_frames(vfl, window, label + "_void")}


def read_points(vfl, window, label="fingertips"):
    p = os.path.join(label_dir(vfl, window, label), "points.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def read_onsets(vfl, window, label="syllables"):
    p = os.path.join(label_dir(vfl, window, label), "onsets.json")
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def sample_frames(win, n=30, margin_s=0.4):
    """``n`` fixture frame indices spread evenly over the window's source
    span [t0, t1] (inset by ``margin_s``)."""
    fps = float(win["fps"])
    a = win["t"][0] + margin_s - win["file_t0"]
    b = win["t"][1] - margin_s - win["file_t0"]
    ts = np.linspace(a, b, n)
    return sorted(set(int(round(t * fps)) for t in ts))


def frame_time(win, frame):
    """(file_time, source_time) of a fixture frame index."""
    ft = frame / float(win["fps"]) + float(win.get("file_start", 0.0))
    return ft, ft + float(win["file_t0"])


def coverage(win, label, n=30):
    """AT-0D-3 check for one window: how many label frames exist and
    whether every one is reviewed."""
    man = read_manifest(win["vfl"], win["name"], label) or {}
    if label in MASK_KINDS:
        have = mask_frames(win["vfl"], win["name"], label)
        reviewed = [f for f in have
                    if (man.get("frames", {}).get(str(f), {})
                        .get("reviewed"))]
        return {"label": label, "frames": len(have),
                "reviewed": len(reviewed), "need": n,
                "ok": len(have) >= n and len(reviewed) == len(have)}
    if label in ("fingertips", "corners"):
        pts = read_points(win["vfl"], win["name"], label) or {}
        status = pts.get("status", "reviewed" if pts else None)
        return {"label": label, "frames": len(pts.get("frames", {})),
                "status": status, "need": n,
                "ok": len(pts.get("frames", {})) >= n and
                status == "reviewed"}
    if label == "syllables":
        on = read_onsets(win["vfl"], win["name"], label) or {}
        words = on.get("words", [])
        filled = [w for w in words if w.get("syllables")]
        return {"label": label, "words": len(words), "filled": len(filled),
                "syllables": sum(len(w["syllables"]) for w in filled),
                "status": on.get("status"),
                "ok": bool(words) and len(filled) == len(words) and
                on.get("status") == "reviewed"}
    return {"label": label, "ok": False, "why": "unknown label kind"}


# ── bootstrap (dev machines: RVM) ─────────────────────────────────────────

def bootstrap_person(win, frames, *, warmup=15, width=None, log=print):
    """RVM soft alphas for ``frames`` of the window's fixture, each measured
    after ``warmup`` preceding frames (the recurrent state settles in a few
    frames). Full resolution unless ``width``. Returns {frame: alpha}."""
    import refsig
    out = {}
    for f in frames:
        lo = max(0, f - warmup)
        m = refsig.person_masks(win["path"], list(range(lo, f + 1)),
                                width=width or int(win["width"]))
        out[f] = m[f]
        if log:
            log(f"  {win['vfl']}/{win['name']} f{f}: "
                f"coverage {float((m[f] >= 0.5).mean()):.3f}")
    return out


# ── CVAT round trip ───────────────────────────────────────────────────────

def _polygons(mask, min_area=24, eps=1.2):
    import cv2
    m = (np.asarray(mask) > 0).astype(np.uint8)
    cs, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    for i, c in enumerate(cs):
        if cv2.contourArea(c) < min_area:
            continue
        a = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
        if len(a) < 3:
            continue
        hole = hier is not None and hier[0][i][3] >= 0
        polys.append((a.tolist(), hole))
    return polys


def export_cvat(win, label, out_dir, frames=None):
    """Frames as JPEGs + CVAT-for-images-1.1 annotations.xml tracing the
    current masks (holes as label '<label>_hole'). Returns the xml path."""
    import qa_metrics
    from PIL import Image
    frames = frames or mask_frames(win["vfl"], win["name"], label)
    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
    idx, imgs = qa_metrics.read_frames(win["path"], frames=set(frames))
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"
    meta = ET.SubElement(root, "meta")
    task = ET.SubElement(meta, "task")
    labels = ET.SubElement(task, "labels")
    for name in (label, f"{label}_hole"):
        lab = ET.SubElement(labels, "label")
        ET.SubElement(lab, "name").text = name
    for i, (f, im) in enumerate(zip(idx, imgs)):
        name = f"{f:06d}.jpg"
        Image.fromarray(im).save(os.path.join(out_dir, "images", name),
                                 quality=92)
        h, w = im.shape[:2]
        el = ET.SubElement(root, "image", id=str(i), name=name,
                           width=str(w), height=str(h))
        m = read_mask(win["vfl"], win["name"], f, label)
        if m is None:
            continue
        for pts, hole in _polygons(m):
            ET.SubElement(el, "polygon",
                          label=f"{label}_hole" if hole else label,
                          occluded="0", source="manual",
                          points=";".join(f"{x:.1f},{y:.1f}"
                                          for x, y in pts), z_order="0")
    path = os.path.join(out_dir, "annotations.xml")
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _rle_decode(rle, left, top, width, height, W, H):
    """CVAT mask RLE (alternating 0/1 run lengths, row-major inside the box)
    -> full-frame bool mask."""
    runs = [int(v) for v in str(rle).replace(" ", "").split(",") if v]
    flat = np.zeros(width * height, bool)
    pos, val = 0, False
    for r in runs:
        if val:
            flat[pos:pos + r] = True
        pos += r
        val = not val
    out = np.zeros((H, W), bool)
    out[top:top + height, left:left + width] = flat.reshape(height, width)
    return out


def import_cvat(win, label, xml_path, *, note="cvat import"):
    """Rasterise a CVAT-for-images-1.1 export into the PNG store. Polygons
    labelled ``<label>`` add, ``<label>_hole`` subtract; CVAT ``mask``
    elements (RLE) add. Image names must be '<frame:06d>.jpg|png'.
    Returns the frames written."""
    from PIL import Image, ImageDraw
    tree = ET.parse(xml_path)
    written = []
    man = read_manifest(win["vfl"], win["name"], label) or {"frames": {}}
    for el in tree.getroot().iter("image"):
        name = el.get("name", "")
        stem = os.path.splitext(os.path.basename(name))[0]
        if not stem.isdigit():
            continue
        f = int(stem)
        W, H = int(el.get("width")), int(el.get("height"))
        canvas = Image.new("L", (W, H), 0)
        dr = ImageDraw.Draw(canvas)
        holes = Image.new("L", (W, H), 0)
        dh = ImageDraw.Draw(holes)
        any_shape = False
        for p in el.iter("polygon"):
            pts = [tuple(float(v) for v in xy.split(","))
                   for xy in p.get("points", "").split(";") if xy]
            if len(pts) < 3:
                continue
            any_shape = True
            if p.get("label") == f"{label}_hole":
                dh.polygon(pts, fill=255)
            elif p.get("label") == label:
                dr.polygon(pts, fill=255)
        m = np.asarray(canvas) > 0
        for mk in el.iter("mask"):
            if mk.get("label") != label:
                continue
            any_shape = True
            m |= _rle_decode(mk.get("rle"), int(mk.get("left")),
                             int(mk.get("top")), int(mk.get("width")),
                             int(mk.get("height")), W, H)
        m &= ~(np.asarray(holes) > 0)
        if not any_shape:
            m = np.zeros((H, W), bool)
        write_mask(win["vfl"], win["name"], f, m, label)
        rec = man["frames"].setdefault(str(f), {})
        rec.setdefault("history", []).append(note)
        rec["reviewed"] = True
        written.append(f)
    write_manifest(win["vfl"], win["name"], label, man)
    return written


def bootstrap_window(vfl_id, name, label="person", n=30, warmup=10,
                     log=print):
    """Sample ``n`` frames, write RVM-bootstrapped masks (threshold 0.5)
    and a manifest marking every frame unreviewed."""
    from . import vfl as vflmod
    win = vflmod.window(vfl_id, name)
    frames = sample_frames(win, n)
    alphas = bootstrap_person(win, frames, warmup=warmup, log=log)
    man = read_manifest(vfl_id, name, label) or {}
    man.update({"vfl": vfl_id, "window": name, "label": label,
                "fixture_sha256": win["sha256"], "fps": win["fps"],
                "file_t0": win["file_t0"],
                "file_start": win.get("file_start", 0.0),
                "size": [win["width"], win["height"]],
                "bootstrap": {"model": "rvm_mobilenetv3_fp32 (PeterL1n "
                              "RobustVideoMatting v1.0.0, sha256 "
                              "88d45312...2828)",
                              "input": "full resolution",
                              "warmup_frames": warmup, "threshold": 0.5}})
    frames_meta = man.setdefault("frames", {})
    for f, a in alphas.items():
        write_mask(vfl_id, name, f, a >= 0.5, label)
        ft, st = frame_time(win, f)
        frames_meta[str(f)] = {"file_time": round(ft, 4),
                               "source_time": round(st, 4),
                               "method": "rvm_bootstrap",
                               "reviewed": False,
                               "coverage": round(float((a >= 0.5).mean()), 4)}
    write_manifest(vfl_id, name, label, man)
    return frames


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="refbench labels")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refine")
    r.add_argument("targets", nargs="+")
    r.add_argument("--label", default="person")
    b = sub.add_parser("bootstrap")
    b.add_argument("targets", nargs="+", help="V02/thiel_flying_cars ...")
    b.add_argument("--label", default="person")
    b.add_argument("--n", type=int, default=30)
    c = sub.add_parser("coverage")
    e = sub.add_parser("export-cvat")
    e.add_argument("target")
    e.add_argument("out")
    e.add_argument("--label", default="person")
    i = sub.add_parser("import-cvat")
    i.add_argument("target")
    i.add_argument("xml")
    i.add_argument("--label", default="person")
    st = sub.add_parser("syllable-template")
    st.add_argument("targets", nargs="+")
    sh = sub.add_parser("syllable-sheet")
    sh.add_argument("target")
    sh.add_argument("out")
    a = ap.parse_args(argv)
    if a.cmd == "syllable-template":
        for t in a.targets:
            print(syllable_template(*t.split("/")))
        return 0
    if a.cmd == "syllable-sheet":
        for p in syllable_sheet(*a.target.split("/"), a.out):
            print(p)
        return 0
    from . import vfl as vflmod
    if a.cmd == "bootstrap":
        for t in a.targets:
            v, w = t.split("/")
            bootstrap_window(v, w, a.label, a.n)
        return 0
    if a.cmd == "refine":
        for t in a.targets:
            v, w = t.split("/")
            refine_window(v, w, a.label)
        return 0
    if a.cmd == "coverage":
        ok = True
        for vid, src in vflmod.sources().items():
            for wname, w in src["windows"].items():
                for lab in w.get("labels", []):
                    res = coverage(vflmod.window(vid, wname), lab)
                    ok &= res["ok"]
                    print(f"{vid}/{wname:<24} {json.dumps(res)}")
        return 0 if ok else 1
    v, w = (a.target.split("/") + [""])[:2]
    win = vflmod.window(v, w)
    if a.cmd == "export-cvat":
        print(export_cvat(win, a.label, a.out))
        return 0
    print(import_cvat(win, a.label, a.xml))
    return 0


# ── refinement: cut-aware RVM, a u2net second opinion, cleanup, void band ─

def _u2net_session(path=None):
    import onnxruntime as ort
    p = path or os.environ.get("VALMERA_U2NET_MODEL") or os.path.expanduser(
        "~/Documents/Valmera/.valmera/models/u2net_human_seg.onnx")
    so = ort.SessionOptions()
    so.log_severity_level = 3
    return ort.InferenceSession(p, so, providers=["CPUExecutionProvider"])


def u2net_prob(sess, rgb):
    """u2net_human_seg person probability at the frame's size (the
    production fallback model; personseg.segment's preprocessing)."""
    import cv2
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)
    x = cv2.resize(rgb, (320, 320), interpolation=cv2.INTER_AREA)
    x = (x.astype(np.float32) / 255.0 - mean) / std
    x = np.ascontiguousarray(x.transpose(2, 0, 1))[None]
    out = sess.run(None, {sess.get_inputs()[0].name: x})[0][0, 0]
    out = (out - out.min()) / max(1e-6, out.max() - out.min())
    return cv2.resize(out.astype(np.float32), (rgb.shape[1], rgb.shape[0]),
                      interpolation=cv2.INTER_LINEAR)


def _last_cut(win, f, lo):
    """The latest frame in (lo, f] that starts a new take (big structural
    change), or lo."""
    import qa_metrics
    import refsig
    if f - lo < 1:
        return lo
    idx, fr = qa_metrics.read_frames(win["path"], start_frame=lo,
                                     count=f - lo + 1, seek=True,
                                     size=(64, 36), gray=True)
    g = [refsig._grad(x.astype(np.float32)) for x in fr]
    cut = lo
    for k in range(1, len(fr)):
        if refsig._corr(g[k - 1], g[k]) < 0.35 and np.abs(
                fr[k].astype(float) - fr[k - 1].astype(float)).mean() > 6:
            cut = idx[k]
    return cut


def clean_mask(m, min_frac=0.0005):
    """Drop islands and fill holes smaller than ``min_frac`` of the frame."""
    import cv2
    m = m.astype(np.uint8)
    area = m.size * min_frac
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = np.zeros_like(m)
    for k in range(1, n):
        if st[k, cv2.CC_STAT_AREA] >= area:
            keep[lab == k] = 1
    inv = (1 - keep).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(inv, connectivity=4)
    H, W = m.shape
    for k in range(1, n):
        x, y, w, h, a = st[k]
        touches = x == 0 or y == 0 or x + w >= W or y + h >= H
        if not touches and a < area:
            keep[lab == k] = 1
    return keep.astype(bool)


def refine_window(vfl_id, name, label="person", frames=None, warmup=12,
                  cut_search=45, log=print):
    """Re-measure every label frame with a cut-aware RVM warm-up, take the
    u2net second opinion, clean islands/holes, write the mask, a void band
    (soft alpha 0.15..0.85: hair, motion blur) and the disagreement area
    to the manifest. Frames with disagreement >= 1% of the frame are
    flagged ``needs_review``."""
    import cv2
    import qa_metrics
    import refsig
    from . import vfl as vflmod
    win = vflmod.window(vfl_id, name)
    frames = frames or mask_frames(vfl_id, name, label)
    sess = _u2net_session()
    man = read_manifest(vfl_id, name, label) or {"frames": {}}
    for f in frames:
        # the recurrent state must not carry a silhouette across a cut: it
        # starts fresh at the take's first frame when the take began within
        # cut_search frames, else ``warmup`` frames back
        cut = _last_cut(win, f, max(0, f - cut_search))
        lo = cut if cut > max(0, f - cut_search) else max(0, f - warmup)
        lo = max(lo, f - cut_search)
        a = refsig.person_masks(win["path"], list(range(lo, f + 1)),
                                width=int(win["width"]))[f]
        _, img = qa_metrics.read_frames(win["path"], start_frame=f, count=1,
                                        seek=True)
        u = u2net_prob(sess, img[0])
        rv = a >= 0.5
        m = clean_mask(rv)
        k = max(3, int(round(0.012 * win["height"])))
        ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k + 1,) * 2)
        u_bin = u >= 0.5
        u_near = cv2.dilate(u_bin.astype(np.uint8), ker).astype(bool)
        rv_near = cv2.dilate(m.astype(np.uint8), ker).astype(bool)
        extra = m & ~u_near          # RVM says person, u2net nowhere near
        missing = u_bin & ~rv_near   # u2net says person, RVM nowhere near
        void = (a > 0.15) & (a < 0.85)
        write_mask(vfl_id, name, f, m, label)
        write_mask(vfl_id, name, f, void, label + "_void")
        ft, st = frame_time(win, f)
        rec = man["frames"].setdefault(str(f), {})
        rec.update({"file_time": round(ft, 4), "source_time": round(st, 4),
                    "method": "rvm(cut-aware warm-up)+clean",
                    "warmup_from": lo,
                    "coverage": round(float(m.mean()), 4),
                    "void_share": round(float(void.mean()), 4),
                    "u2net_extra": round(float(extra.mean()), 4),
                    "u2net_missing": round(float(missing.mean()), 4)})
        rec["needs_review"] = bool(extra.mean() >= 0.01
                                   or missing.mean() >= 0.01)
        rec.setdefault("reviewed", False)
        if log:
            log(f"  {vfl_id}/{name} f{f}: cov {m.mean():.3f} extra "
                f"{extra.mean():.3f} missing {missing.mean():.3f} "
                f"{'REVIEW' if rec['needs_review'] else ''}")
    man["refine"] = {"u2net": "u2net_human_seg (rembg release v0.0.0, "
                     "sha256 01eb6a29...c73), second opinion only",
                     "void": "RVM alpha in (0.15, 0.85): excluded from IoU",
                     "clean": "islands and holes < 0.05% of the frame"}
    write_manifest(vfl_id, name, label, man)
    return frames


# ── syllable onsets (AT-2C-3): a listener's labels, prepared here ─────────

# 25 multi-syllable words per window, chosen for clear acoustic boundaries
# (stops, fricatives) across 2-5 syllables; indices into the window's ASR.
SYLLABLE_WORDS = {
    ("V01", "thiel_ai_bigger"): [9, 12, 14, 21, 23, 25, 28, 33, 37, 42, 45,
                                 48, 54, 56, 75, 77, 78, 86, 87, 88, 95, 97,
                                 98, 103, 105],
    ("V02", "thiel_flying_cars"): [18, 20, 29, 38, 41, 43, 44, 48, 49, 51,
                                   52, 55, 68, 72, 85, 88, 89, 98, 101, 104,
                                   122, 125, 126, 133, 141],
}


def syllable_template(vfl_id, name, label="syllables", words=None,
                      overwrite=False):
    """Write onsets.json with the chosen words (ASR spans, a syllable count
    to confirm) and EMPTY onset lists for a listener to fill. Status stays
    ``todo`` until every word has its onsets and ``reviewed`` is set: an
    ASR span is not a word boundary (small.en drifts by 0.1-0.4 s on V01)
    and syllable boundaries need ears, so nothing here is machine-filled."""
    from . import fixtures_dir as _fx
    from . import vfl as vflmod
    win = vflmod.window(vfl_id, name)
    p = os.path.join(label_dir(vfl_id, name, label), "onsets.json")
    if os.path.exists(p) and not overwrite:
        return p
    with open(os.path.join(_fx(), "asr", vfl_id, f"{name}.json")) as fh:
        asr = json.load(fh)["words"]
    idx = words or SYLLABLE_WORDS[(vfl_id, name)]
    out = {"vfl": vfl_id, "window": name, "label": label,
           "fixture_sha256": win["sha256"], "clock": "source",
           "status": "todo",
           "definition": ("a syllable's onset is the acoustic start of its "
                          "first segment: the release burst of a stop, the "
                          "start of frication or nasal murmur, or the vowel "
                          "onset; source seconds (fixture audio decode "
                          "time + file_t0)"),
           "tool": "python -m benchmarks.refbench labels syllable-sheet "
                   f"{vfl_id}/{name} OUT_DIR",
           "words": [{"i": i, "w": asr[i]["w"].strip(".,!?;:"),
                      "asr": [asr[i]["t0"], asr[i]["t1"]],
                      "t0": None, "t1": None, "syllables": [],
                      "n_syllables": None, "confidence": None}
                     for i in idx]}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(out, fh, indent=1)
    return p


def syllable_sheet(vfl_id, name, out_dir, words=None, per_image=3, pad=0.3):
    """Wideband spectrogram panels (1 px = 1 ms, 10 ms ticks, source-second
    labels, energy curve, ASR words) for the template's words, plus each
    word's audio as a WAV to listen to: what a labeller fills onsets.json
    from. Returns the files written."""
    import subprocess
    from PIL import Image, ImageDraw
    from . import fixtures_dir as _fx
    from . import vfl as vflmod
    from .boards import _font
    win = vflmod.window(vfl_id, name)
    with open(os.path.join(_fx(), "asr", vfl_id, f"{name}.json")) as fh:
        asr = json.load(fh)["words"]
    idx = words or SYLLABLE_WORDS[(vfl_id, name)]
    SR = 16000
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", win["path"], "-map",
         "0:a:0", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    t0f = float(win["file_t0"])
    os.makedirs(out_dir, exist_ok=True)
    font = _font(14)
    written, panels = [], []
    for i in idx:
        wd = asr[i]
        a, b = wd["t0"] - pad, wd["t1"] + pad
        s0, s1 = max(0, int(round((a - t0f) * SR))), int(round((b - t0f) * SR))
        seg = x[s0:s1]
        wav = os.path.join(out_dir, f"{vfl_id}_{name}_{i:03d}.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f",
                        "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", wav],
                       input=seg.tobytes(), check=True)
        written.append(wav)
        n, hop = 128, 16
        win_ = np.hanning(n)
        fr = [seg[k:k + n] * win_ for k in range(0, max(1, len(seg) - n), hop)]
        S = np.abs(np.fft.rfft(np.array(fr), axis=1)).T
        f = np.fft.rfftfreq(n, 1 / SR)
        S = 20 * np.log10(S + 1e-7)
        S = S[f <= 7000][::-1]
        S = np.clip((S - S.max() + 65) / 65, 0, 1)
        E = np.array([10 * np.log10(np.mean(q ** 2) + 1e-10) for q in fr])
        E = np.clip((E - E.max() + 50) / 50, 0, 1)
        W, Hs, L = int((b - a) * 1000), 260, 60
        img = Image.new("RGB", (W + 2 * L, Hs + 170), (255, 255, 255))
        img.paste(Image.fromarray((255 * (1 - S)).astype(np.uint8)).resize(
            (W, Hs)).convert("RGB"), (L, 40))
        d = ImageDraw.Draw(img)
        ys = 40 + Hs + 70
        d.line([(L + int(((k * hop + n / 2) / SR) * 1000), ys - int(60 * e))
                for k, e in enumerate(E)], fill=(200, 0, 0), width=2)
        t = np.ceil(a * 100) / 100
        while t < b:
            xx = L + int(round((t - a) * 1000))
            ms = int(round(t * 1000))
            tick = 16 if ms % 100 == 0 else 10 if ms % 50 == 0 else 5
            d.line([(xx, 40 + Hs), (xx, 40 + Hs + tick)], fill=(0, 0, 0))
            if ms % 50 == 0:
                d.line([(xx, 40), (xx, 40 + Hs)], fill=(255, 190, 190))
                d.text((xx - 22, 40 + Hs + 75), f"{t:.2f}", fill=(0, 0, 0),
                       font=font)
            t = round(t + 0.01, 3)
        for j in range(max(0, i - 2), min(len(asr), i + 3)):
            ww = asr[j]
            if ww["t1"] < a or ww["t0"] > b:
                continue
            xx = L + int((max(a, ww["t0"]) - a) * 1000)
            d.text((xx + 2, 18), ww["w"], fill=(0, 110, 0) if j == i
                   else (120, 120, 120), font=font)
        d.text((4, 2), f"#{i} {wd['w']}  asr {wd['t0']:.2f}-{wd['t1']:.2f}  "
               f"({os.path.basename(wav)}; 1 px = 1 ms; source s)",
               fill=(0, 0, 0), font=font)
        panels.append(img)
    for k in range(0, len(panels), per_image):
        grp = panels[k:k + per_image]
        sheet = Image.new("RGB", (max(p.width for p in grp),
                                  sum(p.height for p in grp)), (255,) * 3)
        y = 0
        for p in grp:
            sheet.paste(p, (0, y))
            y += p.height
        path = os.path.join(out_dir,
                            f"{vfl_id}_{name}_sheet{k // per_image:02d}.png")
        sheet.save(path)
        written.append(path)
    return written
