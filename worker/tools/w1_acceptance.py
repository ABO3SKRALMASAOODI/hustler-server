"""Wave 1 acceptance on the merged engine: every track's AT harness run on
the footage library, the numbers collected into one JSON (LOCAL, INTERNAL:
fixtures and references never leave this machine; outputs go to --out).

    cd worker && PYTHONPATH=.. python tools/w1_acceptance.py --out <dir> \
        [--track 1B 1C 1D 1E]

1A runs separately (tools/at1a_matte.py, it measures its own sidecars);
1F's numbers come from its tests (tests/test_w1f_*).
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(HERE, "tests")
for p in (HERE, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        if isinstance(v, dict):
            return {str(k): _jsonable(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_jsonable(x) for x in v]
        return repr(v)


def run_1b(out):
    """The 1B ATs (pytest: their bars are the assertions) with verdicts
    written under ``out``; returns {verdict name: metrics}."""
    vdir = os.path.join(out, "1B")
    os.makedirs(vdir, exist_ok=True)
    env = dict(os.environ, VALMERA_REFBENCH_OUT=vdir)
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p",
                        "no:cacheprovider", "tests/test_w1b_acceptance.py"],
                       cwd=HERE, env=env, capture_output=True, text=True)
    res = {"pytest": r.stdout.strip().splitlines()[-1:]}
    for p in sorted(glob.glob(os.path.join(vdir, "**", "*.json"),
                              recursive=True)):
        try:
            v = json.load(open(p))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, dict) and v.get("metrics"):
            res[os.path.basename(p)] = v["metrics"]
    return res


def run_1c(out):
    import w1c_at
    d = os.path.join(out, "1C")
    os.makedirs(d, exist_ok=True)
    os.environ.setdefault("W1C_AT_DIR", os.path.join(d, "cases"))
    return w1c_at.run_child(os.path.join(d, "results.json"))


def run_1d(out):
    import pytest
    import test_w1d_local as T
    d = os.path.join(out, "1D")
    os.makedirs(d, exist_ok=True)
    res = {}
    jobs = [
        ("AT-1D-1", lambda mp: T.measure_at1(d, mp)),
        ("AT-1D-1-no-ask", lambda mp: T.measure_at1(d, mp, opted_in=False)),
        ("AT-1D-2", lambda mp: T.measure_at2(d, mp)),
        ("AT-1D-3", lambda mp: T.measure_at3(d, mp)),
        ("AT-1D-4", lambda mp: T.measure_junctions(d, mp, [
            ("columns", {"duration_s": 0.5, "count": 6,
                         "direction": "left"}),
            ("box_steps", {"duration_s": 0.8}),
            ("iris", {"duration_s": 0.4, "origin": {
                "kind": "point", "point": [0.62, 0.38]}})], "at4")),
        ("AT-1D-5", lambda mp: T.measure_junctions(d, mp, [
            ("datamosh", {"duration_s": 1.0})], "at5")),
        ("AT-1D-6", lambda mp: T.measure_junctions(d, mp, [
            ("dissolve", {}), ("dissolve", {}),
            ("matte_cut", {"duration_s": 0.5})], "at6")),
        ("AT-1D-7", lambda mp: T.measure_at7(d, mp)),
        ("AT-1D-perf", lambda mp: T.measure_perf(d, mp)),
    ]
    for name, fn in jobs:
        t0 = time.time()
        with pytest.MonkeyPatch.context() as mp:
            try:
                res[name] = _jsonable(fn(mp))
            except Exception as e:  # noqa: BLE001
                res[name] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        res[name + ".wall_s"] = round(time.time() - t0, 1)
    return res


def run_1e(out):
    d = os.path.join(out, "1E")
    os.makedirs(d, exist_ok=True)
    env = dict(os.environ, VALMERA_REFBENCH_OUT=d)
    r = subprocess.run([sys.executable, "-m", "benchmarks.refbench.at1e"],
                       cwd=HERE, env=env, capture_output=True, text=True)
    res = {"exit": r.returncode, "tail": r.stdout.strip().splitlines()[-3:]}
    for p in sorted(glob.glob(os.path.join(d, "AT-1E-*", "*.verdict.json"))):
        try:
            v = json.load(open(p))
        except Exception:  # noqa: BLE001
            continue
        res[os.path.basename(p)] = {k: v.get(k) for k in
                                    ("pass", "metrics", "standing")
                                    if k in v}
    return res


RUNNERS = {"1B": run_1b, "1C": run_1c, "1D": run_1d, "1E": run_1e}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--track", nargs="*")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, "w1_acceptance.json")
    allres = json.load(open(path)) if os.path.exists(path) else {}
    for t in a.track or sorted(RUNNERS):
        t0 = time.time()
        try:
            allres[t] = _jsonable(RUNNERS[t](a.out))
        except Exception as e:  # noqa: BLE001
            allres[t] = {"error": f"{type(e).__name__}: {str(e)[:400]}"}
        allres[t + ".wall_s"] = round(time.time() - t0, 1)
        with open(path, "w") as f:
            json.dump(allres, f, indent=1)
        print(f"[w1 acceptance] {t} done in {time.time() - t0:.0f}s",
              flush=True)


if __name__ == "__main__":
    main()
