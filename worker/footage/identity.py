"""The same person on every clip of a named person: measured, never guessed.

The picture check judges a frame by its source's title and what is said there; it does not recognise
faces, so an interview's host or a colleague can pass as the founder (FedEx run 1, 13 Nov: three of the
"Fred Smith" clips showed other men). Here each passed frame's faces are embedded (OpenCV YuNet to find
them, SFace to embed them) and the person is the face that recurs across the most sources: a host
appears in one programme, the founder in all of them. With anchor photographs (the WF3 pictures the
check already passed for that name) their faces define the person instead. A frame where another face
fills the picture and the person's does not appear is dropped; a frame with no readable face stays,
one grade down (a wide shot, a back, a car).

The SFace model (OpenCV Zoo, Apache-2.0, 37 MB) is not baked into the image yet: without it this step
says so and changes nothing.
"""
import os
import subprocess

from . import fetch

V_MODELS = "/Users/masaoodi/Documents/Valmera/.valmera/models"
MATCH = 0.363            # SFace's published cosine threshold for one identity
MIN_FACE_PX = 36


def _model(env, name):
    p = os.environ.get(env)
    if p and os.path.exists(p):
        return p
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", name)
    for c in (here, os.path.join(V_MODELS, name)):
        if os.path.exists(c):
            return c
    return None


def models():
    try:
        import cv2
        if not hasattr(cv2, "FaceRecognizerSF"):
            return None
    except ImportError:
        return None
    y = _model("YUNET_MODEL", "face_detection_yunet_2023mar.onnx")
    s = _model("FOOTAGE_SFACE", "face_recognition_sface_2021dec.onnx")
    return (y, s) if y and s else None


def faces(path, det, rec):
    """[(feature, box_px, area_fraction, cx)] for every face in an image."""
    import cv2
    im = cv2.imread(path)
    if im is None:
        return []
    h, w = im.shape[:2]
    det.setInputSize((w, h))
    _, found = det.detect(im)
    out = []
    for f in (found if found is not None else []):
        if f[3] < MIN_FACE_PX:
            continue
        crop = rec.alignCrop(im, f)
        out.append((rec.feature(crop).copy(), f[:4].tolist(), float(f[2] * f[3]) / (w * h), float(f[0] + f[2] / 2) / w))
    return out


def full_frame(proxy, t, out):
    if not os.path.exists(out):
        subprocess.run([fetch.ffmpeg(), "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", proxy, "-frames:v", "1",
                        "-q:v", "2", "-y", out], capture_output=True)
    return out if os.path.exists(out) else None


def keep_person(checked, proxies, work, anchors=()):
    """checked: the look's rows for one person (src index, t, ok, grade, x). proxies: src index -> scanning copy.
    Returns (rows, report). Rows that show someone else are dropped; x moves to the person's face."""
    m = models()
    if not m:
        return checked, dict(identity="skipped: no SFace model")
    import cv2
    import numpy as np
    det = cv2.FaceDetectorYN.create(m[0], "", (320, 320), 0.7, 0.3, 50)
    rec = cv2.FaceRecognizerSF.create(m[1], "")
    os.makedirs(os.path.join(work, "id"), exist_ok=True)
    ok = [r for r in checked if r.get("ok")]
    per = {}
    for k, r in enumerate(ok):
        p = full_frame(proxies[r["src"]], r["t"], os.path.join(work, "id", f"{r['source']}_{r['t']:09.3f}.jpg"))
        per[k] = faces(p, det, rec) if p else []
    def unit(v):
        v = np.asarray(v, dtype=np.float32).ravel()
        return v / (np.linalg.norm(v) + 1e-9)
    anchor_feats = []
    for a in anchors or ():
        anchor_feats += [unit(f[0]) for f in sorted(faces(a, det, rec), key=lambda f: -f[2])[:1]]
    pool = [(k, unit(f[0]), ok[k]["src"]) for k, fs in per.items() for f in fs]
    centre, how = None, None
    if anchor_feats:
        a = unit(np.mean(anchor_feats, axis=0))
        hits = [v for _, v, _ in pool if float(a @ v) >= MATCH]
        if hits:
            centre = unit(np.mean([a] + hits, axis=0))       # the photographs, refined by the frames that match them
            how = f"anchored on {len(anchor_feats)} photographs, {len(hits)} frames match"
    if centre is None:
        if len(pool) < 3:
            return checked, dict(identity=f"skipped: {len(pool)} faces")
        best, best_score = None, None
        for k, v, s in pool:
            near = [(k2, s2) for k2, v2, s2 in pool if float(v @ v2) >= MATCH + 0.04]
            score = (len({s2 for _, s2 in near}), len({k2 for k2, _ in near}))
            if best_score is None or score > best_score:
                best, best_score = v, score
        if best_score[0] < 2:
            # One programme's most frequent face may be its host: with no photograph to anchor on and no
            # second source agreeing, the look's verdict stands rather than a guess.
            return checked, dict(identity=f"skipped: no face recurs across sources ({len(pool)} faces)")
        mates = [v2 for _, v2, _ in pool if float(best @ v2) >= MATCH]
        centre = unit(np.mean(mates, axis=0))
        how = f"the face in {best_score[0]} sources, {best_score[1]} frames"
    keep_ids, dropped, faceless = set(), 0, 0
    for k, r in enumerate(ok):
        fs = per.get(k) or []
        if not fs:
            faceless += 1
            r["grade"] = max(0, int(r.get("grade") or 0) - 1)
            keep_ids.add(id(r))
            continue
        sims = [(float(unit(f[0]) @ centre), f) for f in fs]
        s, f = max(sims, key=lambda x: x[0])
        if s >= MATCH:
            r["x"] = round(min(max(f[3], 0.0), 1.0), 3)
            r["face_match"] = round(s, 3)
            keep_ids.add(id(r))
        else:
            dropped += 1
            r["face_match"] = round(s, 3)
    rows = [r for r in checked if not r.get("ok") or id(r) in keep_ids]
    for r in checked:
        if r.get("ok") and id(r) not in keep_ids:
            r["ok"] = False
            r["why_dropped"] = "another person's face"
    return rows, dict(identity=how, dropped=dropped, faceless=faceless, kept=len(keep_ids))
