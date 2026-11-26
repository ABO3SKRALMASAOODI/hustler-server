"""The Studio editor authors workflow devices in program time. Draw them
against the current cut at render time so a later trim, grade or crop is visible."""
import os
import tempfile
import sys

from timeline import Timeline


def windows(moments, duration):
    """Group overlapping devices so the palette can compose and seat them together."""
    groups = []
    for moment in sorted(moments, key=lambda m: m["start"]):
        start, end = max(0., moment["start"]), min(duration, moment["end"])
        if end <= start:
            continue
        # A native video overlay needs 0.2 s. Pad its plate, not the device.
        end_plate = min(duration, max(end, start + 0.21))
        start_plate = max(0., min(start, end_plate - 0.21))
        if groups and start_plate <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], end_plate)
            groups[-1][2].append(moment)
        else:
            groups.append([start_plate, end_plate, [moment]])
    return groups


def draw_plate(path, out, moments, start, work):
    import media
    from scene import SceneFault, palette, render
    from customer_craft import CONTROLS
    info = media.probe(path)
    fps = info["fps"]
    n = max(1, round(info["duration"] * fps))
    w, h = info["width"], info["height"]
    shot = {"id": "take", "k": "shot", "f": [0, n], "pic": "main",
            "src": [0., n / fps], "crop": [0, 0, 1, 1]}
    scene = {"l1": 1, "sid": "customer", "seed": 7, "fps": fps, "frame": [w, h],
             "dur_f": n, "assets": {"main": {"kind": "video", "path": path, "w": w,
             "h": h, "dur": info["duration"], "fps": fps}}, "spine": [], "voices": {},
             "items": [shot], "sound": [], "film": {}, "workflow_text_layout": True}
    asks = []
    for i, m in enumerate(moments):
        ask = {"id": f"beat{i}", "dev": m["device"],
               "f": [round((m[k] - start) * fps) for k in ("start", "end")]}
        ask.update({k: v for k, v in (m.get("controls") or {}).items()
                    if k in CONTROLS and isinstance(v, (str, int, float))})
        if m.get("text"):
            ask["text"] = m["text"]
        if m.get("color"):
            ask["c"] = m["color"]
        asks.append(ask)
    directed, receipt = palette.apply(scene, work, asks=asks)
    if any(r["done"] for r in receipt):
        try:
            render(directed, work, out, tier="inter", audio=False, use_cache=False)
        except SceneFault as exc:
            if exc.code != "gl" or not sys.platform.startswith("linux"):
                raise
            render(directed, work, out, tier="inter", audio=False, use_cache=False,
                   env={"LIBGL_ALWAYS_SOFTWARE": "1"})
    return receipt


def materialize(edl, index, source, work, preview, locals_, patches, cancelled):
    """Produce temporary local plates below captions and above the picture.
    The parent renderer owns these files and the original audio/master."""
    import renderer
    import stitch
    from scene import SceneFault

    clean = dict(edl)
    moments = clean.pop("direction")
    tl = Timeline(clean.get("keep") or [], clean.get("inserts") or [], clean.get("speed") or [])
    overlays = list(clean.get("overlays") or [])
    local = dict(locals_ or {})
    for a, b, group in windows(moments, tl.out_duration):
        folder = tempfile.mkdtemp(prefix="direction_", dir=work)
        plate = os.path.join(folder, "plate.mp4")
        output = os.path.join(folder, "directed.mp4")
        window = stitch.window_edl(clean, tl, a, b, render_fragment=True)
        window.update(texts=[], vectors=[], motion=[], captions=None)
        renderer.render_edl(window, index, source, plate, folder, preview,
                            patch_locals=patches, asset_locals=locals_, cancelled_cb=cancelled,
                            cap_ass_override="", suppress_outro=True, render_fragment=True,
                            discard_audio=True)
        try:
            receipt = draw_plate(plate, output, group, a, folder)
        except SceneFault as exc:
            # Headless workers without GL still draw an actual accent at its beat.
            # Use FFmpeg on the same plate and keep the time outside each beat intact.
            import media
            filters = [f"eq=contrast=1.18:saturation=0.72:enable='between(t,{max(0,m['start']-a):.5f},{m['end']-a:.5f})'"
                       for m in group]
            media.run(["ffmpeg", "-y", "-i", plate, "-vf", ",".join(filters),
                       "-an", "-c:v", "libx264", "-crf", "16", output], timeout=600)
            receipt = [{"device": m["device"], "fallback": "contrast accent",
                        "reason": str(exc)[:160]} for m in group]
        print(f"[direction] {a:.2f}-{b:.2f}s: {receipt}", flush=True)
        if os.path.isfile(output):
            key = f"direction/{os.path.basename(folder)}.mp4"
            local[key] = output
            overlays.append({"id": os.path.basename(folder), "asset_key": key,
                             "kind": "video", "start": a, "duration_s": max(0.2, b-a),
                             "x": 0.5, "y": 0.5, "scale": 1., "z_index": 1000})
    return dict(clean, overlays=overlays), local
