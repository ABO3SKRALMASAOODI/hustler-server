"""AT-0C-2 calibration bench (track 0C): measure real local renders, fit the
baseline render model in worker/cost_model.json, and test it on 20 held-out
EDLs.

    cd worker && PYTHONPATH=.. python tests/w0c_costbench.py [--write]
    cd worker && PYTHONPATH=.. python tests/w0c_costbench.py --rerun [--write]

A synthetic 1280x720 30p source is rendered through the real renderer:
40 TRAINING EDLs (seeded random mixes) fit the ``baseline`` coefficients per
variant (preview, final) by relative-error least squares; the 20 TEST EDLs
below (12 previews, 8 finals: captions, motion graphics, inserts, four
aspects, 6-60 s programs) are then predicted by the fitted model and must
land within ±25%. Every sample is the faster of two renders (a busy shared
machine adds noise, never speed); motion graphics render cold (no clip
cache), the worst case a preview pays. ``--write`` stores the samples in
tests/fixtures/w0c_cost_samples.json (what tests/test_w0c_cost_model.py
re-predicts in CI) and the fit in cost_model.json's ``baseline``. Local
only; nothing leaves the machine.
"""

import json
import os
import platform
import random
import shutil
import subprocess
import sys
import resource
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)
os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")

import numpy as np  # noqa: E402

import devices  # noqa: E402
import motion_engine  # noqa: E402
import renderer  # noqa: E402
from schemas import default_edl, program_duration, validate_edl  # noqa: E402

SAMPLES = os.path.join(HERE, "fixtures", "w0c_cost_samples.json")
COST_MODEL = os.path.join(WORKER, "cost_model.json")
SRC_S = 70.0
RATIOS = ("9:16", "16:9", "1:1", "4:5")

# (variant, program seconds, ratio, captions, motion items, inserts)
TEST = [
    ("preview", 6, "9:16", False, 0, 0), ("preview", 12, "9:16", True, 0, 0),
    ("preview", 20, "9:16", True, 1, 0), ("preview", 30, "9:16", True, 2, 1),
    ("preview", 45, "9:16", False, 0, 2), ("preview", 60, "9:16", True, 3, 0),
    ("preview", 10, "16:9", False, 0, 0), ("preview", 25, "16:9", True, 1, 1),
    ("preview", 40, "1:1", True, 0, 0), ("preview", 15, "1:1", False, 2, 0),
    ("preview", 35, "4:5", True, 1, 2), ("preview", 8, "9:16", True, 3, 1),
    ("final", 6, "9:16", False, 0, 0), ("final", 15, "9:16", True, 1, 0),
    ("final", 30, "9:16", True, 0, 1), ("final", 45, "9:16", True, 2, 0),
    ("final", 12, "16:9", False, 1, 0), ("final", 25, "1:1", True, 0, 2),
    ("final", 20, "4:5", False, 2, 1), ("final", 40, "9:16", False, 0, 0),
]


def _train_specs(seed=20261011, previews=24, finals=16):
    rng = random.Random(seed)
    out = []
    for variant, n in (("preview", previews), ("final", finals)):
        for _ in range(n):
            prog = rng.choice((5, 8, 10, 14, 18, 22, 28, 34, 42, 50, 58))
            out.append((variant, prog, rng.choice(RATIOS),
                        rng.random() < 0.6,
                        rng.choice((0, 0, 1, 2, 3)) if prog >= 8 else 0,
                        rng.choice((0, 0, 1, 2))))
    return out


def _words(n=150):
    vocab = ("this changed everything about how we build companies and the "
             "number was four hundred million dollars in revenue").split()
    return [{"w": vocab[i % len(vocab)], "t0": round(0.4 + i * 0.42, 3),
             "t1": round(0.4 + i * 0.42 + 0.34, 3), "speaker": "S0"}
            for i in range(n)]


def _make_media(d):
    src, still = os.path.join(d, "src.mp4"), os.path.join(d, "still.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=1280x720:r=30:d={SRC_S}", "-f", "lavfi",
                    "-i", f"sine=frequency=330:sample_rate=48000:"
                          f"duration={SRC_S}",
                    "-c:v", "libx264", "-preset", "veryfast", "-g", "60",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", src], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0x2a6fdb:s=1280x720:d=1", "-frames:v", "1",
                    still], check=True)
    return src, still


def _edl(spec):
    variant, prog, ratio, captions, motion, inserts = spec
    edl = default_edl(SRC_S)
    half = (prog - inserts) / 2.0
    gap_end = round(1.0 + half + 2.0, 2)            # a 2 s jump cut
    edl["keep"] = [[1.0, round(1.0 + half, 2)],
                   [gap_end, round(gap_end + half, 2)]]
    if ratio != "16:9":
        edl["frame"] = {"ratio": ratio, "mode": "crop"}
    edl["effects"] = {"grade": "warm"}
    if captions:
        edl["captions"] = {"mode": "from_transcript",
                           "style": {"preset": "clean"}}
    edl["motion"] = [{"id": f"mg{i}", "template": "hook_title",
                      "start": 0.5 + i * 2.5, "end": 2.5 + i * 2.5,
                      "params": {"text": f"Beat *{i}*"}}
                     for i in range(motion)]
    # inserts splice on keep boundaries: the cut first, then the head
    at = [round(half, 2), 0.0][:inserts]
    edl["inserts"] = [{"id": f"ins{i}", "asset_key": "img/7/still.png",
                       "kind": "image", "at_output_s": a,
                       "duration_s": 1.0} for i, a in enumerate(at)]
    return validate_edl(edl, SRC_S).model_dump()


def _cpu_s():
    """CPU seconds of this process and every child it has reaped (ffmpeg,
    the Playwright driver and its Chromium): unlike wall time, it does not
    grow when other work shares the machine."""
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime


def _render_ms(edl, index, src, still, d, preview, n):
    out, wd = os.path.join(d, f"o{n}.mp4"), os.path.join(d, f"w{n}")
    os.makedirs(wd, exist_ok=True)
    t0, c0 = time.monotonic(), _cpu_s()
    renderer.render_edl(edl, index, src, out, wd, preview,
                        suppress_outro=True,
                        asset_locals={"img/7/still.png": still})
    ms = (time.monotonic() - t0) * 1000.0
    cpu = (_cpu_s() - c0) * 1000.0
    p = renderer.media.probe(out)
    os.remove(out)
    shutil.rmtree(wd, ignore_errors=True)
    return ms, cpu, p


def measure(specs, label):
    motion_engine._cache_get = lambda key: None     # cold clips
    edls = [_edl(spec) for spec in specs]          # fail fast on a bad spec
    rows = []
    with tempfile.TemporaryDirectory() as d:
        src, still = _make_media(d)
        index = {"words": _words(), "silences": [],
                 "sentences": [{"t0": 0.4, "t1": 63.0, "text": "x"}],
                 "video": {"duration": SRC_S, "width": 1280, "height": 720,
                           "fps": 30.0}}
        _render_ms(edls[0], index, src, still, d, True, "warm")  # warm-up
        for n, (spec, edl) in enumerate(zip(specs, edls)):
            times, cpus = [], []
            for attempt in range(3):            # best of two good renders
                try:
                    ms, cpu, p = _render_ms(edl, index, src, still, d,
                                            spec[0] == "preview", n)
                    times.append(ms)
                    cpus.append(cpu)
                except Exception as e:  # noqa: BLE001 — transient I/O
                    print(f"{label} {n} attempt {attempt} failed: "
                          f"{str(e)[-160:]}", flush=True)
                if len(times) == 2:
                    break
            if not times:
                raise RuntimeError(f"{label} {n} never rendered")
            rows.append({"set": label, "variant": spec[0],
                         "program_s": round(float(program_duration(edl)), 2),
                         "ratio": spec[2], "crop": spec[2] != "16:9",
                         "width": p["width"], "height": p["height"],
                         "captions": spec[3], "motion_items": spec[4],
                         "inserts": spec[5],
                         "wall_ms": round(min(times), 1),
                         "cpu_ms": round(min(cpus), 1)})
            print(json.dumps(rows[-1]), flush=True)
    return rows


# What the model predicts: render CPU time. Wall time on a shared machine is
# CPU time divided by whatever share of the cores the render got (this
# bench ran beside other tracks' suites at load averages of 35-45); CPU time
# is what transfers to a lane, whose wall time is CPU / its parallelism
# (cost_model.json lanes.*.parallel).
TARGET = "cpu_ms"
FIELDS = ("fixed_ms", "ms_per_s", "ms_per_mpix_s", "crop_ms_per_s",
          "captions_ms_per_s", "motion_launch_ms", "ms_per_motion_item",
          "ms_per_insert")


def _features(r):
    mpix_s = r["width"] * r["height"] / 1e6 * r["program_s"]
    return [1.0, r["program_s"], mpix_s,
            r["program_s"] if r["crop"] else 0.0,
            r["program_s"] if r["captions"] else 0.0,
            1.0 if r["motion_items"] else 0.0, r["motion_items"],
            r["inserts"]]


def fit(rows):
    """{variant: {field: coefficient}} by relative-error least squares,
    non-negative (a cost is never a saving)."""
    out = {}
    for variant in ("preview", "final"):
        sub = [r for r in rows if r["variant"] == variant]
        X = np.array([_features(r) for r in sub])
        y = np.array([r[TARGET] for r in sub])
        w = 1.0 / y
        Xw, yw = X * w[:, None], y * w
        active = list(range(X.shape[1]))
        beta = np.zeros(X.shape[1])
        for _ in range(X.shape[1]):                 # drop negatives, refit
            b, *_ = np.linalg.lstsq(Xw[:, active], yw, rcond=None)
            beta = np.zeros(X.shape[1])
            beta[active] = b
            neg = [i for i in active if beta[i] < 0]
            if not neg:
                break
            active = [i for i in active if i not in neg]
        out[variant] = {f: round(float(v), 2) for f, v in zip(FIELDS, beta)}
    return out


def predict(baseline, r):
    b = baseline[r["variant"]]
    return float(np.dot([b[f] for f in FIELDS], _features(r)))


def report(baseline, rows, name):
    errs = [predict(baseline, r) / r[TARGET] - 1.0 for r in rows]
    within = sum(abs(e) <= 0.25 for e in errs)
    print(f"{name}: {within}/{len(errs)} within ±25%, worst "
          f"{max(abs(e) for e in errs) * 100:.1f}%, mean |err| "
          f"{np.mean(np.abs(errs)) * 100:.1f}%", flush=True)
    return errs


def rerun(argv):
    """Re-measure the 20 held-out EDLs and predict them with the SHIPPED
    model (no refit): an independent check that the fit reproduces on
    another run. ``--write`` stores them as the fixture's ``rerun``."""
    test = measure(TEST, "rerun")
    shipped = devices.cost_model()["baseline"]
    report(shipped, test, "rerun (shipped model)")
    if "--write" in argv:
        with open(SAMPLES, encoding="utf-8") as f:
            data = json.load(f)
        data["rerun"] = {
            "about": "The 20 held-out EDLs re-measured on another run and "
                     "predicted by the unchanged model.",
            "load_avg": os.getloadavg(),
            "ffmpeg": subprocess.run(["ffmpeg", "-version"],
                                     capture_output=True, text=True
                                     ).stdout.splitlines()[0],
            "samples": test}
        with open(SAMPLES, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
            f.write("\n")
        print("wrote", SAMPLES)


def main(argv):
    if "--rerun" in argv:
        return rerun(argv)
    train = measure(_train_specs(), "train")
    test = measure(TEST, "test")
    baseline = fit(train)
    report(baseline, train, "train (in-sample)")
    report(baseline, test, "test (held out)")
    ffv = subprocess.run(["ffmpeg", "-version"], capture_output=True,
                         text=True).stdout.splitlines()[0]
    if "--write" in argv:
        try:            # a rerun stays: its EDLs are held out of any fit
            with open(SAMPLES, encoding="utf-8") as f:
                kept = {k: v for k, v in json.load(f).items()
                        if k == "rerun"}
        except (OSError, ValueError):
            kept = {}
        with open(SAMPLES, "w", encoding="utf-8") as f:
            json.dump({"machine": platform.platform(),
                       "ffmpeg": ffv, "source": "testsrc2 1280x720 30p",
                       "timing": "best of two renders (CPU ms = this "
                                 "process + reaped children), cold motion "
                                 "clips",
                       "load_avg": os.getloadavg(),
                       "train": train, "samples": test, **kept}, f,
                      indent=1)
            f.write("\n")
        with open(COST_MODEL, encoding="utf-8") as f:
            model = json.load(f)
        model["baseline"] = baseline
        with open(COST_MODEL, "w", encoding="utf-8") as f:
            json.dump(model, f, indent=2, ensure_ascii=False)
            f.write("\n")
        devices.cost_model.cache_clear()
        print("wrote", SAMPLES, "and", COST_MODEL)


if __name__ == "__main__":
    main(sys.argv[1:])
