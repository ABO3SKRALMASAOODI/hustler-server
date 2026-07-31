"""Find the filmed screen by matching THE CONTENT THAT IS ON IT (round 65).

The takeover's whole effect lives in four corners, and both existing sources
of them have failed on real footage in ways the checks cannot fully catch:
screendet measured a portrait shelf at 0.66 confidence (round 62b), and the
vision fallback reads a plausible-but-sloppy, near-axis-aligned quad — which
is exactly why a real user's recording appeared "not tuned to the rotation of
the laptop": the pin renders whatever quad it is given, and a flat quad is a
flat pin.

But in the shot this tool exists for, we hold information neither source
uses: WE KNOW WHAT THE SCREEN IS SHOWING. "Film my laptop, then continue in
the screen recording" almost always means the laptop was filmed while
displaying that very recording (or the same app the recording captures). So
match the recording's own pixels against the filmed frame — ORB features,
ratio-tested matches, RANSAC homography — and the projected corners of the
content rectangle are the screen's corners EXACTLY, rotation and keystone
included, with sub-pixel fidelity no detector or vision read approaches. This
is the match-moving a compositor would do by hand.

A bonus that matters: if the laptop showed the content in a window (a browser,
a player) rather than fullscreen, the homography lands on THAT window — the
region actually displaying the content — so the pinned recording grows out of
exactly its own pixels, which is the strongest possible continuity into the
full-frame cut.

Honest failure: content the glass never showed produces few inliers and this
returns None — the caller falls through to screendet, then the vision read,
exactly as before. Never a guess.
"""

import numpy as np

# Ratio-test threshold for descriptor matches (Lowe's ratio).
RATIO = 0.78
# A homography needs 4 points; trusting one needs far more. Below MIN_INLIERS
# the "match" is texture coincidence, not the content on the glass.
MIN_MATCHES = 16
MIN_INLIERS = 12
# ...and the inliers must agree as a SHARE of the candidate matches, or a
# repeating UI texture (icons, list rows) can fake a consensus.
MIN_INLIER_RATIO = 0.30
# ...and the inliers must SPAN the content: a cluster in one corner means the
# other three projected corners are extrapolation, and a homography's error
# explodes outside its support (verified on real frames: 37 inliers clustered
# in one panel projected a quad twice the screen's height).
MIN_SPREAD = 0.18
SIFT_FEATURES = 3000
# The filmed side gets more budget: the glass is a small, dim, slightly
# defocused region of the frame, and its weak features are exactly the ones
# a tight cap discards first — while the room's strong corners survive.
SIFT_FEATURES_FILMED = 6000
ORB_FEATURES = 5000
RANSAC_PX = 4.0


def _load_gray(path, cv2):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    # Equalize: the filmed glass is a bright emitter in a dark room and the
    # recording is full-range — CLAHE narrows the exposure gap without
    # inventing gradients the way global equalization does.
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return clahe.apply(img)


def _features(img, cv2, nfeat=SIFT_FEATURES):
    """(keypoints, descriptors, norm). SIFT first — a FILMED screen is
    slightly defocused, glared and exposure-shifted, and SIFT's gradient
    descriptors survive that where ORB's binary tests flip; UI content is
    also self-similar (rows, icons), which the ratio test only untangles
    with descriptors that separate well. ORB is the fallback for a cv2
    build without SIFT."""
    try:
        det = cv2.SIFT_create(nfeatures=nfeat)
        kp, des = det.detectAndCompute(img, None)
        if des is not None and len(kp) >= MIN_MATCHES:
            return kp, des, cv2.NORM_L2
    except Exception:
        pass
    det = cv2.ORB_create(nfeatures=ORB_FEATURES, scaleFactor=1.2, nlevels=10)
    kp, des = det.detectAndCompute(img, None)
    return kp, des, cv2.NORM_HAMMING


# A projected quad covering more than this of the filmed frame in either
# dimension is not a filmed screen: it is the match locking onto SHARED
# SCENERY (a recording of this very editor contains a video panel showing
# the filmed room itself — room-to-room matched with 77 confident inliers on
# real footage). Rejected candidates have their inliers STRIPPED and RANSAC
# re-fit on the remainder (sequential multi-homography), because the correct
# chrome-to-glass consensus is usually still in the match set underneath.
MAX_QUAD_FRAC = 0.92
QUAD_BOUNDS = (-0.25, 1.25)
SEQ_ROUNDS = 3


def _quad_from_h(H, cw, ch, fw, fh, cv2):
    # Corner order is the perspective filter's: TL, TR, BL, BR.
    rect = np.float32([[0, 0], [cw, 0], [0, ch], [cw, ch]]).reshape(-1, 1, 2)
    proj = cv2.perspectiveTransform(rect, H).reshape(4, 2)
    quad = []
    for x, y in proj:
        quad.extend([float(x) / fw, float(y) / fh])
    return quad


def _quad_candidate_ok(quad):
    xs, ys = quad[0::2], quad[1::2]
    if min(xs) < QUAD_BOUNDS[0] or max(xs) > QUAD_BOUNDS[1] or \
            min(ys) < QUAD_BOUNDS[0] or max(ys) > QUAD_BOUNDS[1]:
        return False
    if (max(xs) - min(xs)) > MAX_QUAD_FRAC or \
            (max(ys) - min(ys)) > MAX_QUAD_FRAC:
        return False
    return True


def _match_pair(content_path, filmed_path, cv2):
    """One (content frame, filmed frame) attempt. Returns (quad_fracs,
    inliers) in the FILMED frame's fractions, or (None, 0)."""
    content = _load_gray(content_path, cv2)
    filmed = _load_gray(filmed_path, cv2)
    if content is None or filmed is None:
        return None, 0
    kc, dc, norm_c = _features(content, cv2)
    kf, df, norm_f = _features(filmed, cv2, nfeat=SIFT_FEATURES_FILMED)
    if dc is None or df is None or norm_c != norm_f or \
            len(kc) < MIN_MATCHES or len(kf) < MIN_MATCHES:
        return None, 0
    matcher = cv2.BFMatcher(norm_c)
    knn = matcher.knnMatch(dc, df, k=2)
    good = [m for m, n in (p for p in knn if len(p) == 2)
            if m.distance < RATIO * n.distance]
    ch, cw = content.shape[:2]
    fh, fw = filmed.shape[:2]
    for _ in range(SEQ_ROUNDS):
        if len(good) < MIN_MATCHES:
            return None, 0
        src = np.float32([kc[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kf[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, RANSAC_PX)
        if H is None or mask is None:
            return None, 0
        keep = mask.ravel() == 1
        inliers = int(keep.sum())
        if inliers < MIN_INLIERS:
            return None, 0
        quad = _quad_from_h(H, cw, ch, fw, fh, cv2)
        inl = src[keep].reshape(-1, 2)
        span = ((inl[:, 0].max() - inl[:, 0].min()) / cw
                * (inl[:, 1].max() - inl[:, 1].min()) / ch)
        if _quad_candidate_ok(quad) and span >= MIN_SPREAD and \
                inliers >= MIN_INLIER_RATIO * len(good):
            return quad, inliers
        # This consensus is scenery or a degenerate cluster — strip its
        # inliers and let the next-strongest structure have its RANSAC.
        good = [m for m, k in zip(good, keep) if not k]
    return None, 0


def refine_with_read(filmed_paths, content_paths, quad):
    """The guided lock (round 65c/d): a vision read LOCATES the glass, the
    content's pixels then NAIL it. Crops each filmed frame to the read's
    neighbourhood, upscales 2x, matches the content against the crop, and
    maps a lock back into frame fractions. The refined quad must stay NEAR
    the read — sized 0.45-1.7x and centred inside it — or it is the
    shared-scenery steal wearing a crop. Returns the same dict shape as
    match_screen plus refined_from_read, or None (the read stands).
    """
    try:
        import cv2
    except Exception:
        return None
    if not filmed_paths or not content_paths:
        return None
    xs, ys = quad[0::2], quad[1::2]
    rw, rh = max(xs) - min(xs), max(ys) - min(ys)
    if rw <= 0.01 or rh <= 0.01:
        return None
    best = None
    for i, fp in enumerate(filmed_paths):
        img = cv2.imread(fp)
        if img is None:
            continue
        h, w = img.shape[:2]
        x0 = max(0, int((min(xs) - 0.35 * rw) * w))
        x1 = min(w, int((max(xs) + 0.35 * rw) * w))
        y0 = max(0, int((min(ys) - 0.35 * rh) * h))
        y1 = min(h, int((max(ys) + 0.35 * rh) * h))
        if x1 - x0 < 60 or y1 - y0 < 60:
            continue
        crop = img[y0:y1, x0:x1]
        crop = cv2.resize(crop, ((x1 - x0) * 2, (y1 - y0) * 2),
                          interpolation=cv2.INTER_CUBIC)
        cp = fp + f".smref{i}.jpg"
        if not cv2.imwrite(cp, crop):
            continue
        got = match_screen([cp], content_paths)
        if not got:
            continue
        q = got["corners"]
        mapped = []
        for j in range(4):
            mapped.extend([(x0 + q[2 * j] * (x1 - x0)) / w,
                           (y0 + q[2 * j + 1] * (y1 - y0)) / h])
        try:
            from schemas import quad_is_sane, quad_bbox
            ok, _why = quad_is_sane(mapped)
            if not ok:
                continue
            bx, by, bw, bh = quad_bbox(mapped)
        except Exception:
            continue
        if not (0.45 * rw <= bw <= 1.7 * rw and 0.45 * rh <= bh <= 1.7 * rh):
            continue
        cxm, cym = bx + bw / 2.0, by + bh / 2.0
        if not (min(xs) - 0.15 <= cxm <= max(xs) + 0.15
                and min(ys) - 0.15 <= cym <= max(ys) + 0.15):
            continue
        if best is None or got["inliers"] > best["inliers"]:
            best = {"corners": [round(v, 4) for v in mapped],
                    "inliers": got["inliers"],
                    "agreement": got["agreement"],
                    "n_pairs": got["n_pairs"],
                    "refined_from_read": True}
    return best


def run_smatch_job(worker_db, job):
    """Executor-side runner (capture/frames/track-shaped: synchronous, no
    row). SIFT on high-resolution frames is exactly the compute class that
    OOM-killed the dispatcher when round 65d tried it there (job 1513,
    'Worker died and retries are exhausted' 79s into a takeover call) — the
    round-61 rule, learned again: heavy per-frame compute runs HERE.

    payload: {filmed_key, filmed_times[], content_key, content_times[],
              content_kind ('video'|'image'), read_quad[8], width?}
    -> {"match": refine dict | None}. `worker_db` is unused.
    """
    import os as _os
    import shutil as _shutil
    import uuid as _uuid
    import config as _config
    import media as _media
    import storage as _storage
    payload = job.get("payload") or {}
    fk = payload.get("filmed_key")
    ck = payload.get("content_key")
    quad = payload.get("read_quad")
    if not fk or not ck or not quad or len(quad) != 8:
        raise ValueError("smatch needs filmed_key, content_key, read_quad[8]")
    width = int(payload.get("width") or 2048)
    workdir = _os.path.join(_config.TMP_DIR, f"smx_{_uuid.uuid4().hex[:8]}")
    _os.makedirs(workdir, exist_ok=True)
    try:
        flocal = _os.path.join(workdir, "filmed" + _os.path.splitext(fk)[1])
        _storage.download_to(fk, flocal)
        filmed = []
        for i, t in enumerate(payload.get("filmed_times") or [0.0]):
            fp = _os.path.join(workdir, f"f_{i}.jpg")
            try:
                _media.frame_at(flocal, float(t), fp, width=width)
                filmed.append(fp)
            except Exception:
                continue
        clocal = _os.path.join(workdir, "content" + _os.path.splitext(ck)[1])
        _storage.download_to(ck, clocal)
        content = []
        if payload.get("content_kind") == "image":
            content = [clocal]
        else:
            for i, t in enumerate(payload.get("content_times") or [0.0]):
                cp = _os.path.join(workdir, f"c_{i}.jpg")
                try:
                    _media.frame_at(clocal, float(t), cp, width=1280)
                    content.append(cp)
                except Exception:
                    continue
        got = refine_with_read(filmed, content,
                               [float(v) for v in quad])
        return {"match": got}
    finally:
        _shutil.rmtree(workdir, ignore_errors=True)


def match_screen(filmed_paths, content_paths):
    """Best content->filmed corner match across all frame pairs.

    Returns {"corners": [8 fractions of the filmed frame, TL TR BL BR],
    "inliers": int, "agreement": how many pairs found nearly the same quad,
    "n_pairs": pairs tried} or None. The caller still runs quad_is_sane and
    its size/plausibility checks — this reports what it saw, it does not
    vouch for geometry it cannot judge.
    """
    try:
        import cv2
    except Exception:
        return None
    best = None
    quads = []
    n_pairs = 0
    for cp in content_paths:
        for fp in filmed_paths:
            n_pairs += 1
            quad, inl = _match_pair(cp, fp, cv2)
            if quad is None:
                continue
            quads.append(quad)
            if best is None or inl > best["inliers"]:
                best = {"corners": [round(v, 4) for v in quad],
                        "inliers": inl}
    if best is None:
        return None
    # Agreement across independent pairs is the same honesty signal
    # screendet's frame voting provides: quads within 1.5% of the best one.
    agree = 0
    for q in quads:
        if max(abs(a - b) for a, b in zip(q, best["corners"])) < 0.015:
            agree += 1
    best["agreement"] = agree
    best["n_pairs"] = n_pairs
    return best
