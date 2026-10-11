"""AT-0D-2: refsig detectors against hand labels.

ROADMAP 0D: precision and recall >= 0.9 on
  - the ~30 negatives in 20                     labels/refs/20/negatives.json
  - the 22 white flashes in 19@4.30-17.27       labels/refs/19/white_flashes.json
  - the 45 shots of 15                          labels/refs/15/shots.json
  - the B&W section in 24@~10.7-12              labels/refs/24/bw.json
  - the occluded text in 22@~0.67-2.67          labels/refs/22/occluded_text.json

The labels were made by viewing every frame on numbered contact sheets
BEFORE any detector existed; each file states its method, scope and
don't-care spans. Scoring:
  - frame tasks: P/R over the scope's frames minus don't-care spans;
  - shots: a hard cut matches a detection within ±1 frame (one-to-one); a
    labelled transition [a, b] matches any detection in [a-1, b+1] and
    absorbs further detections inside it; detections in don't-care spans
    (±1) are neither hits nor false alarms.

    python -m benchmarks.refbench at0d2 [--only 20,19,15,24,22] [--out F]
"""

import json
import os
import time

from . import fixtures_dir, reference_path

LABELS = {
    "20": "negatives.json",
    "19": "white_flashes.json",
    "15": "shots.json",
    "24": "bw.json",
    "22": "occluded_text.json",
}
BAR = 0.9


def _in_spans(f, spans, pad=0):
    return any(a - pad <= f <= b + pad for a, b in spans)


def frame_pr(pred, truth, scope, dont_care=()):
    """Precision/recall of frame sets over [scope0, scope1) minus don't-care
    spans ([a, b] inclusive)."""
    s0, s1 = scope
    keep = [f for f in range(s0, s1) if not _in_spans(f, dont_care)]
    ks = set(keep)
    p = set(f for f in pred if f in ks)
    t = set(f for f in truth if f in ks)
    tp, fp, fn = len(p & t), len(p - t), len(t - p)
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": (tp / (tp + fp)) if (tp + fp) else 1.0,
            "recall": (tp / (tp + fn)) if (tp + fn) else 1.0,
            "false_alarms": sorted(p - t), "misses": sorted(t - p),
            "frames_scored": len(keep)}


def runs(frames):
    out = []
    for f in sorted(set(frames)):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def run_pr(pred, truth, scope, dont_care=()):
    """Event-level: a labelled run is found when any detected frame overlaps
    it; a detected run is false when it overlaps no labelled run."""
    s0, s1 = scope
    pr = [r for r in runs(f for f in pred if s0 <= f < s1
                          and not _in_spans(f, dont_care))]
    tr = runs(f for f in truth if s0 <= f < s1)
    found = [r for r in tr if any(a <= r[1] and b >= r[0] for a, b in pr)]
    real = [r for r in pr if any(a <= r[1] and b >= r[0] for a, b in tr)]
    return {"labelled_runs": len(tr), "detected_runs": len(pr),
            "precision": (len(real) / len(pr)) if pr else 1.0,
            "recall": (len(found) / len(tr)) if tr else 1.0}


def boundary_pr(pred, cuts, transitions=(), dont_care=(), scope=None, tol=1):
    """Shot-boundary precision/recall with transitions and don't-care."""
    if scope:
        pred = [p for p in pred if scope[0] <= p < scope[1]]
    pred = sorted(pred)
    used = set()
    exact = 0
    hits = []
    for c in sorted(cuts):
        best = None
        for i, p in enumerate(pred):
            if i in used or abs(p - c) > tol:
                continue
            if best is None or abs(p - c) < abs(pred[best] - c):
                best = i
        if best is not None:
            used.add(best)
            hits.append(("cut", c, pred[best]))
            exact += int(pred[best] == c)
    t_found = 0
    for a, b in transitions:
        inside = [i for i, p in enumerate(pred)
                  if a - tol <= p <= b + tol and i not in used]
        if inside:
            t_found += 1
            used.update(inside)
            hits.append(("transition", [a, b], [pred[i] for i in inside]))
    ignored = [i for i, p in enumerate(pred)
               if i not in used and _in_spans(p, dont_care, pad=tol)]
    fp = [pred[i] for i in range(len(pred))
          if i not in used and i not in ignored]
    labelled = len(cuts) + len(transitions)
    found = len([h for h in hits if h[0] == "cut"]) + t_found
    matched_dets = len(pred) - len(fp) - len(ignored)
    # precision counts each labelled boundary once (a transition absorbs
    # its extra detections), so it is boundaries found / (found + false)
    return {"labelled": labelled, "found": found, "false_alarms": fp,
            "ignored_dont_care": [pred[i] for i in ignored],
            "misses": [c for c in cuts
                       if not any(h[0] == "cut" and h[1] == c for h in hits)]
            + [t for t in transitions
               if not any(h[0] == "transition" and h[1] == list(t)
                          for h in hits)],
            "exact_cut_frames": exact,
            "detections": len(pred), "detections_matched": matched_dets,
            "precision": (found / (found + len(fp))) if (found + len(fp))
            else 1.0,
            "recall": (found / labelled) if labelled else 1.0}


def load_label(ref):
    p = os.path.join(fixtures_dir(), "labels", "refs", ref, LABELS[ref])
    with open(p) as fh:
        return json.load(fh)


def evaluate(ref, *, verbose=False):
    """Run the detector for one labelled reference -> result dict."""
    import refsig
    lab = load_label(ref)
    path = reference_path(ref)
    if not path:
        raise FileNotFoundError(f"reference {ref} not found")
    t0 = time.time()
    res = {"ref": ref, "label": lab["label"], "scope": lab["scope"]}
    dc = lab.get("dont_care", [])
    if ref == "20":
        F = refsig.Feats(path)
        pred = refsig.negatives(F)
        res["frames"] = frame_pr(pred, lab["frames"], lab["scope"], dc)
        res["runs"] = run_pr(pred, lab["frames"], lab["scope"], dc)
    elif ref == "19":
        F = refsig.Feats(path, rect=lab.get("picture_rect"))
        neg = refsig.negatives(F)
        pred = refsig.white_flashes(F, neg=neg)
        res["frames"] = frame_pr(pred, lab["frames"], lab["scope"], dc)
        res["picture_rect"] = lab.get("picture_rect")
        try:
            res["layout_detected"] = refsig.layout(path)
        except Exception as e:                                 # pragma: no cover
            res["layout_detected"] = {"error": str(e)[:120]}
    elif ref == "15":
        F = refsig.Feats(path)
        neg = refsig.negatives(F)
        wf = refsig.white_flashes(F, neg=neg)
        cf = refsig.colour_flashes(F, neg=neg)
        pred = refsig.cuts(F, punct=set(neg) | set(wf) | set(cf))
        res["boundaries"] = boundary_pr(pred, lab["cuts"],
                                        lab.get("transitions", []), dc,
                                        lab["scope"])
        res["shots_detected"] = len(pred) + 1
        res["shots_labelled"] = lab.get("shots")
    elif ref == "24":
        F = refsig.Feats(path)
        frames, undecided, sections = refsig.bw(F)
        res["frames"] = frame_pr(frames, lab["frames"], lab["scope"], dc)
        s0, s1 = lab["scope"]
        res["sections_detected"] = [s for s in sections
                                    if s[1] >= s0 and s[0] < s1]
        res["sections_labelled"] = lab.get("sections")
        res["section_iou"] = _sections_iou(res["sections_detected"],
                                           lab.get("sections", []))
    elif ref == "22":
        s0, s1 = lab["scope"]
        ok, why = refsig.ocr_status()
        if not ok:
            raise RuntimeError(f"OCR unavailable: {why}")
        frames = list(range(s0, s1))
        boxes = refsig.ocr_frames(path, frames)
        occ = refsig.occluded_text(path, frames, ocr=boxes)
        res["frames"] = frame_pr(occ["frames"], lab["frames"],
                                 lab["scope"], dc)
        res["ocr_frames_with_text"] = len([f for f in frames if boxes.get(f)])
    res["wall_s"] = round(time.time() - t0, 2)
    pr = res.get("frames") or res.get("boundaries")
    res["precision"] = round(pr["precision"], 4)
    res["recall"] = round(pr["recall"], 4)
    res["pass"] = pr["precision"] >= BAR and pr["recall"] >= BAR
    return res


def _sections_iou(det, lab):
    def frames(spans):
        s = set()
        for a, b in spans:
            s.update(range(a, b + 1))
        return s
    d, l_ = frames(det), frames(lab)
    return round(len(d & l_) / len(d | l_), 4) if (d | l_) else 1.0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="refbench at0d2")
    ap.add_argument("--only", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    refs = a.only.split(",") if a.only else list(LABELS)
    results = []
    for r in refs:
        try:
            res = evaluate(r)
        except Exception as e:
            res = {"ref": r, "error": f"{e.__class__.__name__}: {e}",
                   "pass": False}
        results.append(res)
        if "error" in res:
            print(f"ref {r}: ERROR {res['error']}")
        else:
            print(f"ref {r} {res['label']:<14} P={res['precision']:.3f} "
                  f"R={res['recall']:.3f} pass={res['pass']} "
                  f"({res['wall_s']} s)")
    out = {"case": "AT-0D-2", "bar": BAR, "results": results,
           "pass": all(r.get("pass") for r in results)}
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(out, fh, indent=1)
    return 0 if out["pass"] else 1
