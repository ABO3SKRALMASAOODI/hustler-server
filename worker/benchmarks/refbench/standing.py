"""Standing checks (ROADMAP §4.5, INTERFACES §8.2): on for every case.

    render_ok        the file decodes and has a program
    brand_integrity  end-card frames md5-identical to the control render,
                     watermark ROI unchanged outside the devices' windows
    no_collisions    no caption/graphic over a face or the watermark zone
                     (OCR boxes vs faces; None when OCR is not installed)
    loudness         integrated loudness -14 LUFS ±0.5
    owner_rules      no music, sfx or zoom unless the case allows it; flash
                     rate <= 3/s unless flash_optin
    ledger           taste.ledger(edl).over_budget() is empty (None until
                     0C's ledger lands)

Each value is True / False / None (not measurable here); ``detail`` says
why. A case cannot switch a standing check off.
"""

import numpy as np

import qa_metrics

LUFS_TARGET, LUFS_TOL = -14.0, 0.5
# A graphic collides with a face when its ink covers >= 5% of the ellipse
# inscribed in the face's Haar core. Haar boxes sit up to ~15% off the real
# face (measured on AT-0D-1a: type that stops at the cheek reached 3.2% of
# the ellipse), while one caption line across a face covers ~8-10% and a
# title over it far more. YuNet landmarks (0A) are the precise successor.
FACE_INK_COVER = 0.05


def watermark_roi(W, H):
    """(x0, y0, x1, y1) around the free-tier mark: the robot box plus room
    for the wordmark, from renderer.watermark_geometry."""
    try:
        import renderer
        g = renderer.watermark_geometry(W, H)
        x0 = max(0, g["margin_x"] - 6)
        y0 = max(0, g["margin_y"] - 6)
        x1 = min(W, g["x_out"] + int(0.45 * W))
        y1 = min(H, g["margin_y"] + g["rh"] + 6)
        return x0, y0, x1, y1
    except Exception:
        return 0, 0, int(0.6 * W), int(0.14 * H)


def edl_summary(edl):
    edl = edl or {}
    eff = edl.get("effects") or {}
    return {"music": len(edl.get("music") or []),
            "sfx": len(edl.get("sfx") or []),
            "zooms": len(eff.get("zooms") or []),
            "zoom_paths": len(eff.get("zoom_paths") or []),
            "voiceover": len(edl.get("voiceover") or []),
            "motion": [m.get("template") for m in edl.get("motion") or []],
            "texts": len(edl.get("texts") or []),
            "fx": [f.get("type") for f in edl.get("fx") or []],
            "keep": len(edl.get("keep") or [])}


def owner_rules(edl, allow, *, render=None, fps=None, program_s=None):
    allow = set(allow or [])
    s = edl_summary(edl)
    bad = []
    if s["music"] and "music" not in allow:
        bad.append(f"{s['music']} music item(s) not asked for")
    if s["sfx"] and "sfx" not in allow:
        bad.append(f"{s['sfx']} sound effect(s) not asked for")
    if (s["zooms"] or s["zoom_paths"]) and "zoom" not in allow:
        bad.append(f"{s['zooms'] + s['zoom_paths']} zoom(s) not asked for")
    flash = None
    if render is not None:
        info = qa_metrics.probe(render)
        n = int(round((program_s or info["duration"]) * (fps or info["fps"])))
        _, frames = qa_metrics.read_frames(render, size=(160, 90), count=n)
        flash = qa_metrics.flash_risk(frames=frames, fps=fps or info["fps"],
                                      opted_in="flash_optin" in allow)
        if flash["pass"] is False:
            bad.append(f"{flash['value']:.0f} flashes in one second "
                       "without an opt-in")
    return (not bad), {"violations": bad, "edl": s,
                       "flash_risk": flash and {"value": flash["value"],
                                                "detail": flash["detail"]}}


def brand_integrity(render, control, *, outro_s, device_windows=(),
                    tol=0):
    """End card md5-identical to the control; watermark ROI identical on
    every frame outside the device windows (program seconds)."""
    if control is None:
        return None, {"why": "no control render"}
    info = qa_metrics.probe(render)
    fps = info["fps"]
    a = qa_metrics.frame_md5s(render)
    b = qa_metrics.frame_md5s(control)
    n_out = int(round(outro_s * fps))
    end_ok = None
    if n_out:
        end_ok = len(a) >= n_out and len(b) >= n_out and \
            a[-n_out:] == b[-n_out:]
    W, H = info["width"], info["height"]
    x0, y0, x1, y1 = watermark_roi(W, H)
    prog = len(a) - n_out
    skip = set()
    for s, e in device_windows:
        skip.update(range(max(0, int(s * fps) - 1), int(e * fps) + 2))
    pick = [f for f in range(0, prog, 3) if f not in skip]
    rect = (x0, y0, x1 - x0, y1 - y0)
    ia, fa = qa_metrics.read_frames(render, rect=rect, frames=set(pick))
    ib, fb = qa_metrics.read_frames(control, rect=rect, frames=set(pick))
    worst = 0
    for x, y in zip(fa, fb):
        worst = max(worst, int(np.abs(x.astype(np.int16)
                                      - y.astype(np.int16)).max()))
    wm_ok = worst <= tol
    ok = wm_ok and (end_ok is not False)
    return ok, {"endcard_frames": n_out, "endcard_md5_identical": end_ok,
                "watermark_roi": [x0, y0, x1, y1],
                "watermark_frames_compared": len(fa),
                "watermark_max_abs_diff": worst, "tol": tol}


def loudness(render, program_s=None):
    """The PROGRAM's integrated loudness (the end card is not program)."""
    lufs = qa_metrics.loudness_lufs(render, program_s)
    if lufs is None:
        return None, {"why": "no audio"}
    return abs(lufs - LUFS_TARGET) <= LUFS_TOL, {
        "lufs": lufs, "target": LUFS_TARGET, "tol": LUFS_TOL,
        "measured_s": program_s}


def no_collisions(render, *, program_s=None, every_s=0.5, control=None):
    """OCR text boxes vs Haar faces (and the watermark zone) on sampled
    program frames. With ``control`` (the same edit without graphics) only
    boxes Valmera drew count -- >= 25% of the box changed vs the control --
    so text burned into the footage (a T-shirt, a set sign) is not ours,
    and faces are detected on the control frame.
    A face is its Haar box's central 80%; a collision is a box covering
    >= 5% of that core and, with a control, graphic ink (pixels changed
    vs the control) covering >= FACE_INK_COVER of the ellipse inscribed in
    it: type BEHIND a head leaves the face pixels unchanged, however its
    OCR box spans it."""
    import refsig
    ok, why = refsig.ocr_status()
    if not ok:
        return None, {"why": f"OCR unavailable: {why}"}
    import cv2
    info = qa_metrics.probe(render)
    fps = info["fps"]
    end = program_s or info["duration"]
    frames = list(range(0, int(end * fps), max(1, int(every_s * fps))))
    boxes = refsig.ocr_frames(render, frames)
    casc = cv2.CascadeClassifier(cv2.data.haarcascades +
                                 "haarcascade_frontalface_default.xml")
    idx, imgs = qa_metrics.read_frames(render, frames=set(frames))
    ctrl = {}
    if control is not None:
        ctrl = dict(zip(*qa_metrics.read_frames(control, frames=set(frames))))
    W, H = info["width"], info["height"]
    wx0, wy0, wx1, wy1 = watermark_roi(W, H)
    hits = []
    for f, im in zip(idx, imgs):
        # faces are found on the CONTROL frame when there is one: type can
        # hide a real face from the detector, and big type can itself fire
        # it (measured: Haar found a "face" in a '1960s' headline)
        g = cv2.cvtColor(ctrl.get(f, im), cv2.COLOR_RGB2GRAY)
        faces = casc.detectMultiScale(g, 1.1, 6, minSize=(int(0.06 * W),) * 2)
        diff = None
        if f in ctrl:
            diff = np.abs(im.astype(np.int16) - ctrl[f].astype(np.int16)
                          ).max(axis=2) > 40
        for b in boxes.get(f, []):
            x0, y0, x1, y1 = b["box"]
            if x0 >= wx0 and y0 >= wy0 and x1 <= wx1 and y1 <= wy1:
                continue                       # the watermark itself
            if diff is not None and diff[max(0, y0):y1, max(0, x0):x1] \
                    .mean() < 0.25:
                continue                       # burned into the footage
            for (fx, fy, fw, fh) in faces:
                fx, fy = fx + 0.1 * fw, fy + 0.1 * fh
                fw, fh = 0.8 * fw, 0.8 * fh
                ix = max(0, min(x1, fx + fw) - max(x0, fx))
                iy = max(0, min(y1, fy + fh) - max(y0, fy))
                if ix * iy <= 0.05 * fw * fh:
                    continue
                cover = None
                if diff is not None:
                    # with a control the INK decides, not the OCR box: a
                    # box spans the letters a subject hides (type behind a
                    # head), so only pixels the graphic really changed
                    # inside the face itself count -- the ellipse inscribed
                    # in the core (a square box's corners are background)
                    a0, a1 = int(max(fy, y0)), int(min(fy + fh, y1))
                    b0, b1 = int(max(fx, x0)), int(min(fx + fw, x1))
                    yy, xx = np.mgrid[a0:a1, b0:b1]
                    cx, cy = fx + fw / 2.0, fy + fh / 2.0
                    ell = ((xx - cx) / (fw / 2.0)) ** 2 + \
                        ((yy - cy) / (fh / 2.0)) ** 2 <= 1.0
                    cover = float((diff[a0:a1, b0:b1] & ell).sum()) / \
                        max(1.0, np.pi * fw * fh / 4.0)
                    if cover < FACE_INK_COVER:
                        continue
                hits.append({"frame": f, "text": b["text"], "box": b["box"],
                             "face": [int(fx), int(fy), int(fw), int(fh)],
                             "ink_cover": None if cover is None
                             else round(cover, 4)})
            wi = max(0, min(x1, wx1) - max(x0, wx0)) * \
                max(0, min(y1, wy1) - max(y0, wy0))
            if wi > 0.2 * (x1 - x0) * (y1 - y0):
                hits.append({"frame": f, "text": b["text"], "box": b["box"],
                             "zone": "watermark"})
    return (not hits), {"frames_checked": len(idx), "collisions": hits[:20]}


def ledger(edl):
    try:
        import taste
        fn = getattr(taste, "ledger", None)
        if fn is None:
            return None, {"why": "taste.ledger not available (0C)"}
        led = fn(edl)
        over = led.over_budget()
        return (not over), {"line": led.line(), "over": over}
    except Exception as e:
        return None, {"why": f"ledger unavailable ({e.__class__.__name__})"}


def run_all(case, render, *, control=None, edl=None, outro_s=0.0,
            device_windows=(), program_s=None):
    out, detail = {}, {}
    try:
        info = qa_metrics.probe(render)
        _, fr = qa_metrics.read_frames(render, size=(64, 36), count=3)
        out["render_ok"] = bool(info["duration"] > 0 and len(fr) == 3)
        detail["render_ok"] = {"duration": info["duration"]}
    except Exception as e:
        out["render_ok"] = False
        detail["render_ok"] = {"error": str(e)[:200]}
        return out, detail
    checks = (
        ("brand_integrity", lambda: brand_integrity(
            render, control, outro_s=outro_s,
            device_windows=device_windows)),
        ("no_collisions", lambda: no_collisions(render,
                                                program_s=program_s,
                                                control=control)),
        ("loudness", lambda: loudness(render, program_s)),
        ("owner_rules", lambda: owner_rules(edl, case.get("allow"),
                                            render=render,
                                            program_s=program_s)),
        ("ledger", lambda: ledger(edl)),
    )
    for name, fn in checks:
        try:
            out[name], detail[name] = fn()
        except Exception as e:
            out[name], detail[name] = None, {"error": f"{e.__class__.__name__}: {e}"[:240]}
    return out, detail
