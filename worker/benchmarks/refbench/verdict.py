"""Verdict JSON (INTERFACES §8.2), written to $VALMERA_REFBENCH_OUT.

    {"case", "commit", "ts", "render": {path, sha256, variant, wall_s, lane},
     "metrics": {name: {value, bar, pass}}, "standing": {...}, "devices": [],
     "judges": [{lens, verdict, major_defect, notes}], "pass", "board": {...}}

``pass`` is True only when every metric with a bar passes, every standing
check that could be measured passes and the judges pass. A standing check that could
not be measured here (None) is listed in ``unmeasured``; pending judges make
``pass`` None (never a silent True).
"""

import datetime
import json
import os
import subprocess

from . import WORKER
from .judges import combine


def commit():
    try:
        return subprocess.run(["git", "-C", WORKER, "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return None


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def decide(case, metrics, standing, judges):
    """The case's pass: False when any metric with a bar fails, a measured
    standing check fails or the judges fail; None while judges are pending;
    True otherwise. A metric without a bar (pass None, e.g. cost_ratio) is
    reported, not judged."""
    metrics = metrics or {}
    standing = standing or {}
    m_ok = bool(metrics) and all(v.get("pass") is not False
                                 for v in metrics.values())
    s_fail = [k for k, v in standing.items() if v is False]
    lenses = case.get("judges", {}).get("lenses", [])
    j_pass = combine(judges or [], lenses,
                     case.get("judges", {}).get("pass", "2of3_no_major"))
    if not m_ok or s_fail or j_pass is False:
        return False
    if j_pass is None:
        return None
    return True


def build(case, *, render_path, variant, wall_s=None, lane="local",
          metrics=None, standing=None, standing_detail=None, devices=None,
          judges=None, board=None, extra=None):
    metrics = metrics or {}
    standing = standing or {}
    s_fail = [k for k, v in standing.items() if v is False]
    unmeasured = [k for k, v in standing.items() if v is None]
    passed = decide(case, metrics, standing, judges)
    out = {"case": case["id"], "commit": commit(),
           "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(
               timespec="seconds"),
           "render": {"path": render_path,
                      "sha256": sha256(render_path)
                      if render_path and os.path.exists(render_path) else None,
                      "variant": variant, "wall_s": wall_s, "lane": lane},
           "metrics": {k: {"value": v.get("value"), "bar": v.get("bar"),
                           "pass": v.get("pass")}
                       for k, v in metrics.items()},
           "standing": standing, "devices": devices or [],
           "judges": judges or [], "pass": passed,
           "board": board or {}}
    if unmeasured:
        out["unmeasured"] = unmeasured
    if s_fail:
        out["standing_failed"] = s_fail
    if standing_detail:
        out["standing_detail"] = standing_detail
    if extra:
        out["detail"] = extra
    return out


def write(v, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, f"{v['case']}.verdict.json")
    with open(p, "w") as fh:
        json.dump(v, fh, indent=1, default=str)
    return p


def rejudge(case, case_dir):
    """Re-read the case's verdict after judge replies arrived: judges and
    pass recomputed, everything measured left as it was."""
    from .judges import collect
    p = os.path.join(case_dir, f"{case['id']}.verdict.json")
    with open(p) as fh:
        v = json.load(fh)
    v["judges"] = collect(case, case_dir)
    v["pass"] = decide(case, v.get("metrics"), v.get("standing"),
                       v["judges"])
    with open(p, "w") as fh:
        json.dump(v, fh, indent=1, default=str)
    return v
