"""Restore the camera windows and approved insets of previously scored shorts."""
import hashlib
import json


def key_of(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def local_coverage(coverage, plan):
    """Keep stable window IDs and their exact passing insets, with camera through the gaps."""
    plan = plan if isinstance(plan, dict) else {}
    indices = plan.get("camera_windows") or []
    # The scored issuer example already supplies the picture on its exact clause.
    if isinstance(indices,list):
        spans = {(r["first"],r["last"]) for r in plan.get("readings") or [] if r.get("evidence")}
        indices = indices + [i for i,w in enumerate(plan.get("windows") or [])
                             if (w.get("first"),w.get("last")) in spans]
    snapshots = plan.get("windows") or []
    if not isinstance(indices, list) or not isinstance(snapshots, list) or not indices or not snapshots:
        return coverage
    windows = list(coverage.get("windows") or [])
    for i in indices:
        if (type(i) is int and 0 <= i < min(len(windows), len(snapshots))
                and {k:windows[i].get(k) for k in ("say","first","last")} == snapshots[i]):
            windows[i] = dict(windows[i], publisher_camera=True)
    return dict(coverage, windows=windows)
