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
# The control's robot pixels vs the robot PNG (mean |d| per channel, after
# the yuv420p round trip, best of the 3x3 one-pixel shifts): measured 3.6
# on the AT-0D-1 finals, 25 one row off, 97 on a skewed read. The robot is
# white, red and black, so plain footage in its place misses by far more.
ROBOT_MATCH_TOL = 24.0


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
    """What the edit carries that the owner rules govern, plus a device
    list for the judges. A zoom is any picture push-in the EDL asks the
    renderer for: ``effects.zooms`` (``cut_step`` ones are listed as such:
    optional cut hygiene is still a zoom), a ``zoom_punch`` junction
    transition, and a frame shift that zooms as it narrows."""
    edl = edl or {}
    eff = edl.get("effects") or {}
    zooms = eff.get("zooms") or []
    tr = eff.get("transition") or {}
    zoom_tr = 1 if (tr or {}).get("style") == "zoom_punch" else 0
    shifts = [f for f in eff.get("frame_shifts") or []
              if f.get("zoom", True)]
    return {"music": len(edl.get("music") or []),
            "sfx": len(edl.get("sfx") or []),
            "zooms": len(zooms),
            "zoom_ids": [(z.get("id") or "?") + (" (cut_step)"
                                                 if z.get("cut_step") else "")
                         for z in zooms],
            "zoom_transitions": zoom_tr,
            "frame_shift_zooms": len(shifts),
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
        bad.append(f"{s['music']} music item(s) not asked for (allow lacks "
                   "music)")
    if s["sfx"] and "sfx" not in allow:
        bad.append(f"{s['sfx']} sound effect(s) not asked for (allow lacks "
                   "sfx)")
    n_zoom = s["zooms"] + s["zoom_transitions"] + s["frame_shift_zooms"]
    if n_zoom and "zoom" not in allow:
        bad.append(f"{n_zoom} zoom(s) not asked for (allow lacks zoom)")
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


def robot_core(W, H, *, erode=2):
    """(x, y, mask, rgb): the free-tier robot's OPAQUE pixels in a W x H
    output frame -- the robot PNG (renderer.robot_path) at the size and
    corner renderer.watermark_geometry gives the overlay, alpha 255 (alpha
    250-254 lets a white flash through by a level or two: measured on
    AT-0D-1c), eroded by ``erode`` px so resampling and 4:2:0 chroma at its
    edge never count -- and its colour there. None without the asset."""
    try:
        import renderer
        from PIL import Image
        p = renderer.robot_path()
        if not p:
            return None
        g = renderer.watermark_geometry(W, H)
        im = Image.open(p).convert("RGBA").resize((g["rw"], g["rh"]),
                                                  Image.BICUBIC)
        a = np.asarray(im)
        m = np.pad(a[..., 3] == 255, erode, constant_values=False)
        m = qa_metrics._erode(m, erode)[erode:-erode, erode:-erode]
        if not m.any():
            return None
        return g["margin_x"], g["margin_y"], m, a[..., :3]
    except Exception:
        return None


def brand_integrity(render, control, *, outro_s, device_windows=(),
                    tol=0):
    """The free-tier mark and the end card are untouched by the devices.

    - End card: its frames are md5-identical to the control's.
    - Robot: on EVERY sampled program frame (device windows included) the
      robot's opaque pixels are identical to the control's. The mark is
      composited after every creative stage, so whatever a device draws
      under it, those pixels cannot change; a device drawn over the mark
      (or a mark that went missing) changes them. The control must show
      the robot there (``robot_present``), or the comparison proves nothing.
    - Watermark ROI (robot + sliding wordmark + margin): identical on every
      sampled frame outside the device windows (inside one, a device may
      legitimately change the picture under the mark).

    True / False, or None when no watermark pixel could be compared (a
    check that compared nothing is never a pass)."""
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
    prog = min(len(a), len(b)) - n_out
    skip = set()
    for s, e in device_windows:
        skip.update(range(max(0, int(s * fps) - 1), int(e * fps) + 2))
    every = list(range(0, max(0, prog), 3))
    rect = (x0, y0, x1 - x0, y1 - y0)
    ia, fa = qa_metrics.read_frames(render, rect=rect, frames=set(every))
    ib, fb = qa_metrics.read_frames(control, rect=rect, frames=set(every))
    fa, fb = dict(zip(ia, fa)), dict(zip(ib, fb))
    core = robot_core(W, H)
    cm = None
    if core is not None:
        cx, cy, m, rgb = core
        ry, rx = cy - y0, cx - x0
        if ry >= 0 and rx >= 0 and ry + m.shape[0] <= y1 - y0 and \
                rx + m.shape[1] <= x1 - x0:
            cm = (ry, rx, m, rgb)
    worst, compared = 0, 0
    core_worst, core_frames, present = 0, 0, []
    for f in every:
        if f not in fa or f not in fb:
            continue
        d = np.abs(fa[f].astype(np.int16) - fb[f].astype(np.int16)).max(
            axis=2)
        if f not in skip:
            worst = max(worst, int(d.max()))
            compared += 1
        if cm is not None:
            ry, rx, m, rgb = cm
            win = (slice(ry, ry + m.shape[0]), slice(rx, rx + m.shape[1]))
            core_worst = max(core_worst, int(d[win][m].max()))
            core_frames += 1
            if len(present) < 12:
                # is the robot really there? its colours within a pixel
                # of where the geometry puts it (a rounding shift is not
                # an absent mark)
                present.append(min(
                    float(np.abs(fb[f][ry + dy:ry + dy + m.shape[0],
                                       rx + dx:rx + dx + m.shape[1]][m]
                                 .astype(np.int16)
                                 - rgb[m].astype(np.int16)).mean())
                    for dy in (-1, 0, 1) for dx in (-1, 0, 1)))
    robot_present = None
    if present:
        robot_present = float(np.median(present)) <= ROBOT_MATCH_TOL
    detail = {"endcard_frames": n_out, "endcard_md5_identical": end_ok,
              "watermark_roi": [x0, y0, x1, y1],
              "watermark_frames_compared": compared,
              "watermark_frames_in_device_windows": len(
                  [f for f in every if f in skip]),
              "watermark_max_abs_diff": worst,
              "robot_core_px": None if cm is None else int(cm[2].sum()),
              "robot_frames_compared": core_frames,
              "robot_max_abs_diff": core_worst if core_frames else None,
              "robot_present_in_control": robot_present,
              "robot_control_mean_abs_err": round(float(np.median(present)),
                                                  2) if present else None,
              "tol": tol}
    if end_ok is False:
        return False, detail
    checks = []
    if compared:
        checks.append(worst <= tol)
    if core_frames:
        if robot_present is False:
            detail["why"] = ("the control shows no robot at the mark's "
                             "corner: the comparison proves nothing")
            return None, detail
        checks.append(core_worst <= tol)
    if not checks:
        detail["why"] = "no watermark pixel was compared"
        return None, detail
    return all(checks), detail


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
    variant = (case.get("render") or {}).get("variant")

    def brand():
        if variant != "final":
            return None, {"why": "a preview carries neither the free-tier "
                                 "mark nor the end card "
                                 "(renderer.wants_watermark, outro_seconds):"
                                 " brand integrity is a final-render check"}
        import config
        if int(config.FINAL_CRF) != 0:
            return None, {"why": "lossy final (--lossy): the bit-exact "
                                 "brand comparison needs FINAL_CRF=0"}
        return brand_integrity(render, control, outro_s=outro_s,
                               device_windows=device_windows)
    checks = (
        ("brand_integrity", brand),
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
