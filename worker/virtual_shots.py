"""Virtual shots (Wave 2, track 2E): new shots made from the footage the
user already has, each tagged ``derived_from``.

* ``macros``: tight crops of a high-resolution source on what matters in
  it (faces, hands, the circle of a product, the sharpest region), each a
  window of the canvas aspect that drifts a little (InsertItem crop +
  motion): a 4K wide becomes three close-ups;
* ``slow_windows``: windows of a >= 50 fps source with motion in them (the
  clean slow-motion candidates for set_speed_ramp, ramps.floor);
* ``posters``: freeze-poster candidates (stills.pick).

Nothing here renders; a virtual shot is a description an insert can play.
"""

MACRO_MIN_PX = 540          # a crop narrower than this is too soft to use


def macros(entry, src_w, src_h, canvas_ar, k=3, scale=0.5):
    """[{derived_from, crop, why}] up to ``k`` macro crops of a shot."""
    d = entry.get("desc") or {}
    targets = []
    for f in d.get("faces") or []:
        targets.append(((f[0] + f[2]) / 2, (f[1] + f[3]) / 2, "face"))
    for cx, cy, _r in d.get("circles") or []:
        targets.append((cx, cy, "circle"))
    if not targets:
        targets.append((0.5, 0.5, "centre"))
    out = []
    ch = scale
    cw = ch * src_h * canvas_ar / src_w
    if cw * src_w < MACRO_MIN_PX:
        return []
    for cx, cy, why in targets[:k]:
        x0 = min(max(0.0, cx - cw / 2), 1.0 - cw)
        y0 = min(max(0.0, cy - ch / 2), 1.0 - ch)
        out.append({"derived_from": {"key": entry["key"],
                                     "t0": entry.get("t0"),
                                     "t1": entry.get("t1")},
                    "crop": [round(x0, 4), round(y0, 4),
                             round(x0 + cw, 4), round(y0 + ch, 4)],
                    "motion": "zoom_in", "why": why})
    return out


def slow_windows(entries, fps_by_key, min_fps=50.0, min_motion=3.0):
    """[{derived_from, t0, t1, floor}] windows of high-rate sources with
    movement worth slowing."""
    import ramps
    out = []
    for e in entries:
        fps = float(fps_by_key.get(e["key"]) or 0.0)
        if fps < min_fps or float(e.get("motion", 0.0)) < min_motion:
            continue
        out.append({"derived_from": {"key": e["key"]},
                    "t0": e.get("t0"), "t1": e.get("t1"),
                    "floor": round(ramps.floor(fps, 30.0), 3)})
    return out
