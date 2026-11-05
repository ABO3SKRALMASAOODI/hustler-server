"""Measured authored visual coverage; editorial relevance still needs review."""
from collections import Counter
import math

from schemas import program_duration
from timeline import Timeline, insert_windows


def measure(edl):
    duration = float(program_duration(edl))
    rows, unmeasured = [], []

    def add(kind, item, start, end=None, length=None):
        try:
            start = float(start)
            end = float(end) if end is not None else start + float(length)
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                raise ValueError("invalid window")
            a, b = max(0.0, start), min(duration, end)
        except (TypeError, ValueError):
            unmeasured.append({"kind": kind, "id": item.get("id")})
            return
        if b > a:
            row = {"kind": kind, "id": item.get("id"), "start": a, "end": b}
            if kind == "zoom":
                row["strength"] = item.get("strength")
            rows.append(row)

    for item in edl.get("overlays") or []:
        add("overlay", item, item.get("start"), length=item.get("duration_s"))
    inserts = edl.get("inserts") or []
    windows = insert_windows(inserts, Timeline(edl.get("keep") or [], inserts,
                                              edl.get("speed")))
    for item in inserts:
        start, end = windows.get(item.get("id"), (None, None))
        add("insert", item, start, end)
    for kind, items in (("zoom", (edl.get("effects") or {}).get("zooms")),
                        ("motion", edl.get("motion")), ("text", edl.get("texts")),
                        ("vector", edl.get("vectors"))):
        for item in items or []:
            add(kind, item, item.get("start"), item.get("end"))
    rows.sort(key=lambda r: (r["start"], r["end"], r["kind"], str(r["id"])))
    spans = []
    for row in rows:
        a, b = row["start"], row["end"]
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    gaps, previous = [], 0.0
    for a, b in spans + [[duration, duration]]:
        if a > previous:
            gaps.append({"start": previous, "end": a, "duration_s": round(a - previous, 3)})
        previous = b
    return {"duration_s": duration, "counts_by_kind": dict(Counter(r["kind"] for r in rows)),
            "placements": rows, "gaps_without_authored_treatment": gaps,
            "unmeasured_items": unmeasured,
            "interpretation": "Kinds are separate: a source credit is not another B-roll insert. "
            "Gaps may contain natural source motion/cuts or intentional presenter commentary. "
            "These measurements establish placement and timing, not relevance or visual quality."}
