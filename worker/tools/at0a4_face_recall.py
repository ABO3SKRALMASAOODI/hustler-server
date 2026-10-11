"""AT-0A-4: YuNet face recall against today's Haar passes, and its speed.

ROADMAP Wave 0 track 0A: "YuNet face recall >= Haar's on 200 labelled frames
across V01-V06; <= 10 ms per frame at 320 px on standard-1."

Inputs (local only, never in git):
  * frames:  $VALMERA_FIXTURES_DIR/w0a/faces_frames/V0x/*.jpg (540p stills
    sampled evenly across each VFL source, ROADMAP §4.2)
  * labels:  $VALMERA_FIXTURES_DIR/labels/V0x/w0a_faces/faces/labels.json
    (hand-verified; INTERFACES.md §8.4 layout)
  * model:   YUNET_MODEL (the pinned file; worker/models/yunet.json)

Detectors compared, each exactly as production runs it:
  yunet          cv2.FaceDetectorYN at 320 px wide, score 0.6 (faces.yunet)
  haar_keepout   keepout.detect_faces: three cascades + mirrored profile at
                 480 px, clustered and filtered (today's keep-out pass)
  haar_index     one frontal cascade at 360 px (the index's spatial sidecar)

A labelled face counts as found when a detection overlaps it (IoU >= 0.3, or
each box's centre lies inside the other). Recall is scored on faces >= 7% of
the frame height ("all") and on the frame's prominent faces (width >= 40% of
its largest face, the ones keep-out exists for). Speed: one thread, frames
already decoded, the model call alone; standard-1 is half a vCPU, so the
report states the measured time and the 2x quota bound beside it.

    PYTHONPATH=.. python tools/at0a4_face_recall.py [--json out.json]
"""

import argparse
import glob
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MIN_H = 0.07
PROMINENT = 0.40


def fixtures_dir():
    return os.environ.get("VALMERA_FIXTURES_DIR") or os.path.expanduser(
        "~/Documents/Valmera/.valmera/fixtures")


def load_labels(root=None):
    root = root or fixtures_dir()
    frames = []
    for path in sorted(glob.glob(os.path.join(
            root, "labels", "V0*", "w0a_faces", "faces", "labels.json"))):
        vid = path.split(os.sep)[-4]
        for f in json.load(open(path))["frames"]:
            f = dict(f, vid=vid, path=os.path.join(root, f["file"]))
            frames.append(f)
    return frames


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    i = ix * iy
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u > 0 else 0.0


def _inside(p, b):
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def matches(label, det):
    c = lambda b: ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)  # noqa: E731
    return _iou(label, det) >= 0.3 or (_inside(c(det), label)
                                       and _inside(c(label), det))


class YuNet:
    def __init__(self, model, width=320, score=0.6):
        import cv2
        self.cv2, self.width, self.score = cv2, width, score
        self.model, self.det, self.size = model, None, None
        self.times = []

    def __call__(self, img):
        cv2 = self.cv2
        h, w = img.shape[:2]
        W = self.width
        H = int(round(h * W / float(w)))
        H -= H % 2
        small = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA)
        if self.det is None:
            self.det = cv2.FaceDetectorYN.create(self.model, "", (W, H),
                                                 self.score, 0.3, 50)
        if self.size != (W, H):
            self.det.setInputSize((W, H))
            self.size = (W, H)
        t = time.perf_counter()
        _ok, faces = self.det.detect(small)
        self.times.append(time.perf_counter() - t)
        return [[float(f[0]) / W, float(f[1]) / H, float(f[0] + f[2]) / W,
                 float(f[1] + f[3]) / H]
                for f in (faces if faces is not None else [])]


def haar_index(img, cache={}):
    import cv2
    if "c" not in cache:
        cache["c"] = cv2.CascadeClassifier(os.path.join(
            cv2.data.haarcascades, "haarcascade_frontalface_default.xml"))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    W = 360
    H = int(round(h * W / float(w)))
    g = cv2.equalizeHist(cv2.resize(g, (W, H)))
    found = cache["c"].detectMultiScale(g, scaleFactor=1.1, minNeighbors=4,
                                        minSize=(max(24, W // 20),) * 2)
    return [[x / W, y / H, (x + bw) / W, (y + bh) / H]
            for x, y, bw, bh in found]


def evaluate(frames, model):
    import cv2
    import keepout
    cv2.setNumThreads(1)
    yunet = YuNet(model)
    tally = {d: {"all": [0, 0], "prominent": [0, 0], "dets": 0, "fp": 0}
             for d in ("yunet", "haar_keepout", "haar_index")}
    per_vid = {}
    haar_times = []
    for f in frames:
        img = cv2.imread(f["path"])
        labels = [b for b in f["faces"] if b[3] - b[1] >= MIN_H]
        if labels:
            wmax = max(b[2] - b[0] for b in labels)
        t = time.perf_counter()
        hk = keepout.detect_faces(f["path"]) or []
        haar_times.append(time.perf_counter() - t)
        dets = {"yunet": yunet(img), "haar_keepout": hk,
                "haar_index": haar_index(img)}
        for name, boxes in dets.items():
            row = tally[name]
            row["dets"] += len(boxes)
            row["fp"] += sum(1 for d in boxes
                             if not any(matches(b, d) for b in f["faces"]))
            for b in labels:
                hit = any(matches(b, d) for d in boxes)
                row["all"][0] += hit
                row["all"][1] += 1
                if b[2] - b[0] >= PROMINENT * wmax:
                    row["prominent"][0] += hit
                    row["prominent"][1] += 1
                pv = per_vid.setdefault(f["vid"], {}).setdefault(name, [0, 0])
                pv[0] += hit
                pv[1] += 1
    report = {"frames": len(frames),
              "faces": tally["yunet"]["all"][1],
              "prominent_faces": tally["yunet"]["prominent"][1]}
    for name, row in tally.items():
        report[name] = {
            "recall_all": round(row["all"][0] / max(1, row["all"][1]), 4),
            "recall_prominent": round(row["prominent"][0]
                                      / max(1, row["prominent"][1]), 4),
            "found_all": row["all"][0], "found_prominent": row["prominent"][0],
            "detections": row["dets"], "false_positives": row["fp"]}
    report["per_source"] = {
        vid: {n: round(v[0] / max(1, v[1]), 3) for n, v in rows.items()}
        for vid, rows in sorted(per_vid.items())}
    ms = sorted(t * 1000.0 for t in yunet.times)
    report["yunet_ms_per_frame_1thread"] = {
        "mean": round(statistics.mean(ms), 3),
        "p50": round(ms[len(ms) // 2], 3),
        "p95": round(ms[int(len(ms) * 0.95) - 1], 3),
        "standard1_bound_mean": round(2 * statistics.mean(ms), 3)}
    hm = sorted(t * 1000.0 for t in haar_times)
    report["haar_keepout_ms_per_frame"] = {
        "mean": round(statistics.mean(hm), 2), "p50": round(hm[len(hm) // 2], 2)}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json")
    ap.add_argument("--model", default=os.environ.get("YUNET_MODEL"))
    args = ap.parse_args(argv)
    if not args.model or not os.path.exists(args.model):
        print("set YUNET_MODEL to the pinned YuNet file", file=sys.stderr)
        return 2
    frames = load_labels()
    if not frames:
        print("no labels under", fixtures_dir(), file=sys.stderr)
        return 2
    report = evaluate(frames, args.model)
    text = json.dumps(report, indent=1)
    print(text)
    if args.json:
        with open(args.json, "w") as f:
            f.write(text)
    ok = (report["yunet"]["recall_all"] >= report["haar_keepout"]["recall_all"]
          and report["yunet"]["recall_all"] >= report["haar_index"]["recall_all"]
          and report["yunet_ms_per_frame_1thread"]["standard1_bound_mean"]
          <= 10.0)
    print("AT-0A-4", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
