"""Framing through movement, screen insets, cut pops and the rendered-output
QC gate (judges round 3, Oct 2026).

* a one-frame framing pop one frame before a cut (Elon 21.888 s): a focus
  edge on a keep join, with no indexed shot, split the block half a frame
  early and released the zoom held to the join a frame early;
* Elon turned to profile and the still crop left his nose 3-4% from the
  edge (follow held still on a tight close-up, and Haar lost the profile);
* a burned-in browser inset was cropped through, and a no-face crop of it
  ran 7 s while Rogan spoke;
* nothing checked the rendered output for clipped faces, single-frame
  jumps off a cut, or a missing end card or watermark.

Run:  python -m pytest tests/test_reframe_qc.py -q     (from worker/)
"""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import renderer                                              # noqa: E402
from schemas import default_edl, validate_edl                # noqa: E402
from timeline import Timeline                                # noqa: E402


# ── the one-frame pop at a cut (renderer.composition_join) ─────────────────

def _focus_edl(keep, edge, dur=200.0, zooms=None):
    edl = default_edl(dur)
    edl["keep"] = [list(k) for k in keep]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 0.0, "t1": edge, "x": 0.3, "y": 0.5},
        {"t0": edge, "t1": dur, "x": 0.7, "y": 0.5}]}
    if zooms:
        edl["effects"] = dict(edl.get("effects") or {}, zooms=zooms)
    return validate_edl(edl, dur).model_dump()


def test_composition_join_names_the_keep_edge_an_unmeasured_edge_means():
    keep = [(149.6, 152.63), (152.63, 153.25)]
    fps = 29.97
    assert renderer.composition_join(152.63, keep, {}, fps) == 152.63
    # a hundredth off still means the join (within 1.5 frames)
    assert renderer.composition_join(152.64, keep, {}, fps) == 152.63
    # a measured camera cut near it keeps the measured handoff
    shots = {"shots": [{"start": 0.0}, {"start": 152.65}]}
    assert renderer.composition_join(152.63, keep, shots, fps) is None
    # an edge mid-span is a crop switch inside the footage
    assert renderer.composition_join(151.0, keep, {}, fps) is None
    # an edge that names a frame (a measured cut written in hundredths: the
    # first Rogan frame at 138.0379, 2 ms before the 138.04 join) keeps the
    # measured handoff, so that frame takes the new shot's crop
    keep2 = [(130.7, 138.04), (138.04, 138.58)]
    assert renderer.composition_join(138.04, keep2, {}, fps) is None
    h = renderer.composition_handoff(138.04, keep2, {}, fps, 0.0, fps)
    assert 4136 / fps < h < 4137 / fps


def test_a_focus_edge_on_an_unindexed_join_switches_on_it():
    """The Elon pop: keep join and focus edge at 152.63, no shots in the
    index. The block before the join keeps its own aim to its last frame:
    no sliver block of the next aim half a frame before the join."""
    edl = _focus_edl([(149.6, 152.63), (152.63, 153.25)], 152.63)
    graph = renderer.build_filtergraph(
        edl, 200.0, True, Timeline(edl["keep"]), None, [], {"words": []},
        preview=True, W=270, H=480, fps=29.97, frame_mode="crop",
        src_w=1920, src_h=1080, src_fps=29.97, focus_origin=0.0)
    assert "trim=start=152.603" not in graph
    assert "trim=start=149.600:end=152.630" in graph
    blocks = {p.split("]")[0]: p for p in graph.split(";")
              if p.startswith("[segv")}
    assert len(blocks) == 2
    assert "min(270,iw-ow)" in blocks["[segv0"]       # x 0.3 to the join
    assert "min(1026,iw-ow)" in blocks["[segv1"]      # x 0.7 after it


def test_the_camera_cut_of_an_unindexed_crop_switch_is_the_join():
    edl = _focus_edl([(149.6, 152.63), (152.63, 153.25)], 152.63)
    tl = Timeline(edl["keep"])
    for origin in (None, 0.0, 1 / 29.97):
        cuts = renderer.camera_cuts(edl, {}, tl, 29.97, 29.97, origin)
        assert cuts == [pytest.approx(3.03)]
    # with a measured cut a frame later the measured handoff stands
    index = {"shots": [{"start": 0.0}, {"start": 152.65}]}
    cuts = renderer.camera_cuts(edl, index, tl, 29.97, 29.97, 0.0)
    assert all(c > 3.03 for c in cuts)


def test_a_zoom_held_to_the_join_holds_through_its_last_frame():
    """z5: an ease ending on the join released on the frame before it."""
    zoom = {"id": "z5", "start": 0.0, "end": 3.03, "strength": 0.08,
            "mode": "ease", "cx": 0.6, "cy": 0.4}
    edl = _focus_edl([(149.6, 152.63), (152.63, 153.25)], 152.63,
                     zooms=[zoom])
    tl = Timeline(edl["keep"])
    (held,) = renderer.camera_zooms(edl, {"video": {"fps": 29.97}}, tl,
                                    29.97, 29.97, 0.0)
    assert held["end"] == pytest.approx(3.03)
    last = renderer.first_frame_at(3.03, 29.97) - 1
    z, _cx, _cy = renderer.zoom_state_at([held], last / 29.97, 3.65)
    assert z > 1.05


def test_only_renders_the_rule_changes_are_stale():
    edl = _focus_edl([(149.6, 152.63), (152.63, 153.25)], 152.63)
    assert renderer.handoff_may_matter(edl)
    assert renderer.handoff_affected(edl, {"video": {"fps": 29.97}})
    assert not renderer.handoff_current({}, edl, {"video": {"fps": 29.97}})
    assert renderer.handoff_current(
        {"handoff_v": renderer.config.HANDOFF_VERSION}, edl,
        {"video": {"fps": 29.97}})
    # an indexed cut at the edge: the render is unchanged, the cache stands
    shots = {"video": {"fps": 29.97},
             "shots": [{"start": 0.0}, {"start": 152.63}]}
    assert renderer.handoff_current({}, edl, shots)
    # an edge inside a span (no join near) never asks
    mid = _focus_edl([(149.6, 153.25)], 151.0)
    assert not renderer.handoff_may_matter(mid)
    assert renderer.handoff_current({}, mid, {})


# ── burned-in screen insets (insets.py) ────────────────────────────────────

def _scene(w=640, h=360, seed=3):
    """A soft, textured studio-like grey frame (no straight edges)."""
    import numpy as np
    rng = np.random.default_rng(seed)
    small = rng.integers(40, 200, size=(h // 24 + 2, w // 24 + 2)).astype(np.float32)
    import cv2
    img = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    img += rng.normal(0, 3, size=(h, w))
    return np.clip(img, 0, 255).astype(np.uint8)


def _with_box(img, rect, fill=28, lines=True):
    out = img.copy()
    h, w = out.shape
    x0, y0, x1, y1 = (int(rect[0] * w), int(rect[1] * h), int(rect[2] * w),
                      int(rect[3] * h))
    out[y0:y1, x0:x1] = fill
    if lines:                             # a screenshot's text lines
        for k, y in enumerate(range(y0 + 12, y1 - 8, 14)):
            out[y:y + 4, x0 + 20:x1 - 20 - (k % 3) * 40] = 200
    return out


INSET = [0.53, 0.52, 0.98, 0.98]


def test_a_burned_in_box_is_found_and_scenery_is_not():
    import insets
    scene = _scene()
    assert insets.rects_in(scene) == []
    (r,) = insets.rects_in(_with_box(scene, INSET))
    assert all(abs(a - b) < .015 for a, b in zip(r, INSET))
    # a box flush to a corner is scenery the frame cuts, never an inset
    assert insets.rects_in(_with_box(scene, [0.0, 0.0, 0.3, 0.4])) == []
    # a panel of the screen's own layout is part of it: one box
    nested = _with_box(_with_box(scene, INSET), [0.6, 0.7, 0.9, 0.9], fill=90,
                       lines=False)
    assert len(insets.rects_in(nested)) == 1
    # a box too small to be a screen is not one
    assert insets.rects_in(_with_box(scene, [0.4, 0.4, 0.5, 0.5])) == []


def test_boxes_are_persistent_spans_refined_to_where_they_appear():
    import insets
    rows = [(t, [list(INSET)] if 3.0 <= t <= 8.0 else [])
            for t in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)]
    (box,) = insets.boxes(rows, step=1.0)
    assert box["seen"] == 5 and box["spans"] == [(3.0, 8.0)]
    # a probe that knows the truth (on screen 3.2-7.9) moves the edges
    probe = lambda t, r: 3.2 <= t <= 7.9                 # noqa: E731
    (box,) = insets.boxes(rows, step=1.0, probe=probe)
    (a, b), = box["spans"]
    assert abs(a - 3.2) <= .07 and abs(b - 7.9) <= .07
    assert insets.present(box, 0, 20) == pytest.approx(b - a)
    # one sighting among many is a blip, not a box
    assert insets.boxes([(0.5, [list(INSET)]), (1.5, []), (2.5, [])]) == []


def test_crop_windows_against_a_box():
    import insets
    crop = [0.24, 0.0, 0.56, 1.0]          # a 9:16 window of 16:9 footage
    assert insets.cuts_through(INSET, crop)
    assert insets.overlap_share(INSET, crop) == pytest.approx(.03 / .32)
    assert not insets.cuts_through(INSET, [0.1, 0.0, 0.42, 1.0])
    # the nearest window clear of the box that keeps the face in it
    x = insets.clear_aim(INSET, crop, (0.30, 0.45))
    assert x is not None and x + .16 <= INSET[0]
    # a face reaching into the box's columns cannot be kept clear of it
    assert insets.clear_aim(INSET, crop, (0.30, 0.54)) is None
    # a fitted card shows the whole box at its own aspect
    box = insets.fit_box(INSET, 1920, 1080, 1080, 1920)
    w_px = (box[2] - box[0]) * 1080
    h_px = (box[3] - box[1]) * 1920
    assert w_px / h_px == pytest.approx((0.45 * 1920) / (0.46 * 1080), rel=.02)
    assert insets.upscale(INSET, 1920, 1080, box, 1080) < 2.0


# ── follow through a turn on a tight close-up (follow.py) ──────────────────

WW = 607.5 / 1920          # a 9:16 window of 16:9 footage


def _tight(cx, look, h=.48):
    w = h / (16 / 9)
    return [cx - w / 2, .42 - h / 2, cx + w / 2, .42 + h / 2]


def test_a_turned_tight_face_keeps_its_leading_edge_clear():
    """Elon: a close-up whose face is wider than the window's room turns
    toward Rogan and leans in; a crop that kept only the face's centre
    line left his nose 3% from the edge. The leading edge (the side he
    looks to) now stays TIGHT_LEAD_MARGIN inside, the trailing side gives."""
    import follow
    f = _tight(.50, -1)
    kb = follow.keep_box(f, WW, 1.0, look=-1.0)
    assert kb[0] < f[0]                        # the nose side, with margin
    assert kb[2] < f[2]                        # the ear side gives way
    frontal = follow.keep_box(f, WW, 1.0, look=0.0)
    assert frontal[0] > f[0] and frontal[2] < f[2]       # centred share
    assert (frontal[0] - f[0]) == pytest.approx(f[2] - frontal[2])
    # the turn and the lean: a still crop leaves the nose at the edge
    s = []
    for t in np.arange(0, 8, .25):
        cx = .56 if t < 4 else .56 - min(.07, (t - 4) * .07)
        look = 0 if t < 3.5 else -1
        s.append((round(float(t), 2), _tight(cx, look), look))
    keys, info = follow.plan(s, 0, 8, WW, 1.0)
    assert keys is not None and info["moves"] >= 1 and info["overhang"] == 0.0
    ts = np.arange(0, 8, 1 / 30.0)
    for t in ts:
        cx = follow.centre_at({"t0": 0, "t1": 8, "k": keys}, t)[0]
        face = min(s, key=lambda r: abs(r[0] - t))[1]
        if t > 6.5:                           # turned and settled
            gap = (face[0] - (cx - WW / 2)) / WW
            assert gap >= follow.TIGHT_LEAD_MARGIN - .01
    v, _a = _speed({"t0": 0, "t1": 8, "k": keys}, 0, 8)
    assert np.max(np.abs(v)) <= follow.VMAX_HARD * WW + 1e-6


def test_a_frontal_tight_face_still_does_not_hunt():
    import follow
    rng = np.random.default_rng(11)
    s = [(round(float(t), 2), _tight(.55 + rng.normal(0, .004), 0), 0)
         for t in np.arange(0, 9, .25)]
    keys, info = follow.plan(s, 0, 9, WW, 1.0)
    assert keys is None and info["holds"] == 1


def _speed(span, t0, t1, axis=0):
    import follow
    ts = np.arange(t0, t1, 1 / 30.0)
    v = [follow.centre_at(span, t)[axis] for t in ts]
    return np.diff(v) * 30.0, np.diff(v, 2) * 900.0


def test_a_face_the_detector_loses_is_carried_through_the_turn():
    """Haar loses a full profile; the head is carried by the optical flow
    of the features inside its last box, at most CARRY_MAX_S, and a false
    positive elsewhere in the frame does not stop it."""
    import follow
    rng = np.random.default_rng(5)
    tex = rng.integers(0, 255, size=(60, 50)).astype(np.uint8)
    import cv2
    tex = cv2.GaussianBlur(tex, (3, 3), 0)
    frames, times = [], []
    for i in range(14):
        g = np.full((252, 448), 90, np.uint8)
        x = 200 - 6 * i                          # the head drifts left
        g[80:140, x:x + 50] = tex
        frames.append(g)
        times.append(round(i * .25, 3))
    box0 = [200 / 448, 80 / 252, 250 / 448, 140 / 252]
    fp = [[.85, .05, .95, .2], 0]
    dets = [[(list(box0) if i == 0 else [box0[0] - 6 * i / 448, box0[1],
                                         box0[2] - 6 * i / 448, box0[3]], -1)]
            if i < 3 else ([tuple(fp)] if i == 5 else []) for i in range(14)]
    out = follow.carry(times, frames, dets)
    for i in range(3, 9):                        # within CARRY_MAX_S of 0.5 s
        boxes = [b for b, _l in out[i] if b[0] < .8]
        assert boxes, i
        assert boxes[0][0] == pytest.approx((200 - 6 * i) / 448, abs=.01)
        assert all(lk == -1 for b, lk in out[i] if b[0] < .8)
    assert not [b for b, _l in out[13] if b[0] < .8]   # past 2 s: let go


# ── the rendered-output picture check (render_qc.py) ───────────────────────

def _changes(n=500, spikes=()):
    rng = np.random.default_rng(2)
    ch = [(float(abs(rng.normal(1.0, .4))), .02) for _ in range(n)]
    for k, level, share in spikes:
        ch[k] = (level, share)
    return ch


def test_a_global_jump_off_a_cut_is_a_pop_or_a_jump():
    import render_qc
    # changes[k] is the jump INTO frame k+1
    plan = {"cut_frames": [100, 200], "events": [(400, 403)], "end_frame": 480}
    ch = _changes(spikes=[(99, 60, .8),                # the cut at 100
                          (198, 55, .8), (199, 80, .9),  # pop on 199, cut 200
                          (299, 40, .7),               # a jump off any cut
                          (349, 15, .2),               # a graphic: a region
                          (400, 70, .9),               # a full-frame layer
                          (479, 90, .9)])              # into the end card
    got = render_qc.jumps(ch, plan)
    assert ("pop", 199, 200) in got
    assert any(k == "jump" and fr == 300 for k, fr, _c in got)
    assert not any(fr in (100, 200, 350, 401, 480) for _k, fr, _c in got)
    lines = render_qc.findings({"jumps": got}, dict(plan, fps=30.0))
    assert any(l.startswith("ONE-FRAME POP at 6.63s") and "cut at 6.67s" in l
               for l in lines)


def test_a_face_at_the_edge_is_clipped_only_when_it_could_clear_it():
    import render_qc
    plan = {"cards": [(10.0, 20.0, [0.06, 0.3, 0.94, 0.7])]}
    left = [0.02, 0.3, 0.40, 0.6]                      # 2% from the left
    wide = [0.03, 0.2, 0.97, 0.6]                      # too wide to clear
    mid = [0.30, 0.3, 0.70, 0.6]
    samples = [(0.25, [(left, -1)]), (0.75, [(left, -1)]), (1.25, [(mid, 0)]),
               (2.25, [(wide, 0)]), (2.75, [(wide, 0)]),
               # inside the card, its edge is the one that counts
               (10.25, [([0.07, 0.4, 0.4, 0.6], 0)]),
               (10.75, [([0.07, 0.4, 0.4, 0.6], 0)])]
    runs = render_qc.clipped_faces(samples, plan)
    assert [(r[0], r[1], r[2]) for r in runs] == [(0.25, 0.75, "left"),
                                                   (10.25, 10.75, "left")]
    # a turned face counts on the side it looks to: the back of the head
    # may meet the edge (and the profile box runs past it)
    back = [(t, [([0.04, 0.2, 0.80, 0.6], 1)]) for t in (0.25, 0.75)]
    assert render_qc.clipped_faces(back, {}) == []
    nose = [(t, [([0.04, 0.2, 0.80, 0.6], -1)]) for t in (0.25, 0.75)]
    assert render_qc.clipped_faces(nose, {})[0][2] == "left"
    # a smaller face beside the speaker is not the speaker
    small = [(0.25, [(mid, 0), ([0.0, 0.1, 0.05, 0.15], 0)]),
             (0.75, [(mid, 0), ([0.0, 0.1, 0.05, 0.15], 0)])]
    assert render_qc.clipped_faces(small, {}) == []


FFMPEG = pytest.mark.skipif(not __import__("shutil").which("ffmpeg"),
                            reason="ffmpeg required")


def _clip(path, vf, d=2.0, size="270x480"):
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=30:duration={d}", "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15",
                    path], check=True)


@FFMPEG
def test_the_check_finds_a_one_frame_pop_in_a_rendered_file(tmp_path):
    import render_qc
    out = str(tmp_path / "pop.mp4")
    # frame 30 alone is pushed in (a crop switch a frame off the cut at 31)
    _clip(out, "crop=iw-60:ih-108:'if(eq(n,30),60,30)':'if(eq(n,30),108,54)',"
          "scale=270:480")
    plan = {"program_s": 2.0, "fps": 30.0, "W": 270, "H": 480,
            "end_frame": 60, "cut_frames": [31], "events": [], "cards": [],
            "watermark": None, "endcard": None}
    res = render_qc.check(out, plan, budget_s=30)
    assert ["pop", 30, 31] in res["jumps"]
    assert any("ONE-FRAME POP" in f for f in res["findings"])
    # the same file with the switch ON the cut is clean
    plan["cut_frames"] = [30, 31]
    assert render_qc.check(out, plan, budget_s=30)["jumps"] == []


@FFMPEG
def test_the_check_sees_the_end_card_and_the_watermark(tmp_path):
    import subprocess
    import render_qc
    card = renderer.endcard_path()
    robot = renderer.robot_path()
    if not card or not robot:
        pytest.skip("brand assets missing")
    # finals carry the mark, and finals compose on an HD canvas: at half
    # that the robot is still ~56 px tall
    W, H = 540, 960
    g = renderer.watermark_geometry(W, H)
    body = str(tmp_path / "body.mp4")
    _clip(body, "scale=540:960", size="540x960")
    marked = str(tmp_path / "marked.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", body, "-i", robot,
                    "-filter_complex",
                    f"[1:v]scale={g['rw']}:{g['rh']}[r];"
                    f"[0:v][r]overlay={g['margin_x']}:{g['margin_y']}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", marked], check=True)
    cw, ch = int(W * .98) // 2 * 2, int(H * .98) // 2 * 2
    full = str(tmp_path / "full.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", marked,
                    "-stream_loop", "-1", "-t", "3", "-i", card,
                    "-filter_complex",
                    f"[1:v]scale={cw}:{ch}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,fps=30,setsar=1[c];"
                    "[0:v]setsar=1[p];[p][c]concat=n=2:v=1:a=0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", full], check=True)
    base = {"program_s": 2.0, "fps": 30.0, "W": W, "H": H, "end_frame": 60,
            "cut_frames": [], "events": [], "cards": []}
    wm = {"x": g["margin_x"], "y": g["margin_y"], "w": g["rw"], "h": g["rh"],
          "robot": robot}
    good = render_qc.check(full, dict(base, watermark=wm,
                                      endcard={"path": card, "s": 3.0}))
    assert good["endcard"] >= render_qc.ENDCARD_MIN_NCC
    assert good["watermark"] >= render_qc.ROBOT_MIN_NCC
    assert not any("MISSING" in f for f in good["findings"])
    # the body alone: no card after it, no robot in the corner
    bare = str(tmp_path / "bare.mp4")
    _clip(bare, "scale=540:960", d=5.0, size="540x960")
    bad = render_qc.check(bare, dict(base, watermark=wm,
                                     endcard={"path": card, "s": 3.0}))
    assert any(f.startswith("END CARD MISSING") for f in bad["findings"])
    assert any(f.startswith("WATERMARK MISSING") for f in bad["findings"])


def test_the_plan_reads_the_edits_cuts_zooms_and_branding():
    import render_qc
    edl = _focus_edl([(149.6, 152.63), (152.63, 153.25)], 152.63, zooms=[
        {"id": "z5", "start": 0.0, "end": 3.03, "strength": 0.08,
         "mode": "ease", "cx": 0.6, "cy": 0.4}])
    plan = render_qc.plan(edl, {"video": {"fps": 29.97}}, W=1080, H=1920,
                          fps=29.97, outro_s=5.0, want_wm=True, src_fps=29.97)
    cut = renderer.first_frame_at(3.03, 29.97)
    assert cut in plan["cut_frames"] and cut - 1 not in plan["cut_frames"]
    assert plan["end_frame"] == round(plan["program_s"] * 29.97)
    assert plan["watermark"]["w"] > 0 and plan["endcard"]["s"] == 5.0
    preview = render_qc.plan(edl, {}, W=270, H=480, fps=30.0)
    assert preview["watermark"] is None and preview["endcard"] is None
    # a finishing effect's whole window is deliberate
    fx = dict(edl, effects=dict(edl.get("effects") or {}, stylize=[
        {"id": "f", "kind": "flash", "start": 1.0, "end": 1.5}]))
    p2 = render_qc.plan(fx, {}, W=270, H=480, fps=30.0)
    assert any(a <= 30 and b >= 45 for a, b in p2["events"])


def test_scenery_boxes_are_not_screens():
    """A light panel is flat and empty, a wall or a shirt pattern is all
    texture: neither is screen content, however straight its edges."""
    import insets
    scene = _scene()
    assert insets.rects_in(_with_box(scene, INSET, fill=235, lines=False)) == []
    rng = np.random.default_rng(9)
    tex = _with_box(scene, INSET, fill=60, lines=False)
    h, w = tex.shape
    y0, y1, x0, x1 = int(.52 * h), int(.98 * h), int(.53 * w), int(.98 * w)
    tex[y0:y1, x0:x1] = np.clip(60 + rng.normal(0, 25, (y1 - y0, x1 - x0)),
                                0, 255).astype(np.uint8)
    assert insets.rects_in(tex) == []


# ── what the reframe tools say about the crop (agent_tools) ────────────────

class _Ctx:
    def __init__(self, edl, words=(), shots=()):
        self.project_id = 1
        self.duration = 200.0
        self.has_main_video = True
        self.workdir = None
        self.index = {"video": {"duration": 200.0, "width": 1920,
                                "height": 1080, "fps": 30.0},
                      "shots": list(shots), "words": list(words),
                      "sentences": [], "silences": []}
        self.written = []
        self._edl = validate_edl(edl, 200.0).model_dump()

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), 200.0).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written) + 1}: {desc}"

    def proxy_path(self):
        return "/nonexistent.mp4"


def _crop_edl(x, keep=((10.0, 20.0),)):
    edl = default_edl(200.0)
    edl["keep"] = [list(k) for k in keep]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_x": x, "focus_y": .5}
    return edl


def _faces(cx, t0=10.0, t1=20.0, w=.08):
    return [(round(t, 2), [cx - w / 2, .25, cx + w / 2, .45], 0)
            for t in np.arange(t0, t1, .25)]


def _words(t0=10.0, t1=20.0):
    return [{"w": "word", "t0": round(t, 2), "t1": round(t + .3, 2)}
            for t in np.arange(t0, t1, .35)]


def test_a_crop_on_a_screen_box_with_no_face_is_named_with_the_fix(monkeypatch):
    """The Elon evidence section: a crop aimed at the screenshot inset and
    the neon wall, no face for 7 s while Rogan reads."""
    import agent_tools
    ctx = _Ctx(_crop_edl(.76), words=_words())
    box = {"rect": list(INSET), "spans": [(10.0, 20.0)], "seen": 10}
    monkeypatch.setattr(agent_tools, "_inset_boxes", lambda c, w: [box])
    monkeypatch.setattr(agent_tools, "_follow_samples",
                        lambda c, w, cuts=(): (_faces(.42), "measured", [], None))
    notes = agent_tools._framing_checks(ctx)
    text = "\n".join(notes)
    assert "NO FACE IN THE CROP 0-10s" in text and "x=0.42" in text
    assert "SCREEN INSET" in text and "source='inset'" in text
    assert "'source': 'auto'" in text or '"source": "auto"' in text
    assert ctx.written == []                 # advice only: the aim is the editor's


def test_auto_reframe_slides_a_still_crop_off_a_box_it_slices(monkeypatch):
    import agent_tools
    ctx = _Ctx(_crop_edl(.40), words=_words())
    box = {"rect": list(INSET), "spans": [(10.0, 20.0)], "seen": 10}
    monkeypatch.setattr(agent_tools, "_inset_boxes", lambda c, w: [box])
    monkeypatch.setattr(agent_tools, "_follow_samples",
                        lambda c, w, cuts=(): (_faces(.36), "measured", [], None))
    # an authored crop is told, not moved
    notes = agent_tools._framing_checks(ctx)
    assert any(n.startswith("SCREEN INSET (look") for n in notes)
    assert ctx.written == []
    notes = agent_tools._framing_checks(ctx, apply=True)
    assert any(n.startswith("SCREEN INSET CLEARED") for n in notes)
    (span,) = ctx._edl["frame"]["follow"]
    x = span["k"][0][1]
    assert x + (607.5 / 1920) / 2 <= INSET[0] + 1e-3      # the box is out
    assert x - (607.5 / 1920) / 2 <= .32                   # the face is in
    # a face reaching into the box's columns cannot be cleared: advice
    ctx = _Ctx(_crop_edl(.48), words=_words())
    monkeypatch.setattr(agent_tools, "_follow_samples",
                        lambda c, w, cuts=(): (_faces(.50), "measured", [], None))
    notes = agent_tools._framing_checks(ctx, apply=True)
    assert ctx.written == [] and any("cannot leave it out" in n for n in notes)


def test_a_picture_card_can_show_the_measured_screen_box(monkeypatch):
    import agent_tools
    ctx = _Ctx(_crop_edl(.42))
    monkeypatch.setattr(agent_tools, "_inset_rect", lambda c, spans: list(INSET))
    res = agent_tools.set_picture_card(ctx, "screen", 0.0, 6.0, source="inset")
    assert res.startswith("EDL v"), res
    card = ctx._edl["effects"]["picture_cards"][0]
    assert card["source"] == pytest.approx(INSET) and card["fit"] == "pad"
    bw = (card["box"][2] - card["box"][0]) * 1080
    bh = (card["box"][3] - card["box"][1]) * 1920
    assert bw / bh == pytest.approx((.45 * 1920) / (.46 * 1080), rel=.03)
    res = agent_tools.set_picture_card(ctx, "stack", 6.5, 9.5, panels=[
        {"box": [.04, .07, .96, .47], "source": "auto"},
        {"box": [.04, .5, .96, .93], "source": "inset"}])
    assert res.startswith("EDL v"), res
    stack = [c for c in ctx._edl["effects"]["picture_cards"] if c["id"] == "stack"][0]
    assert stack["panels"][1]["source"] == pytest.approx(INSET)
    monkeypatch.setattr(agent_tools, "_inset_rect", lambda c, spans: None)
    assert agent_tools.set_picture_card(ctx, "x", 0.0, 3.0, source="inset") \
        .startswith("REJECTED: no burned-in screen box")


def test_the_watermark_robot_is_never_the_speaker():
    import render_qc
    plan = {"W": 1080, "H": 1920, "cards": [],
            "watermark": {"x": 108, "y": 115, "w": 76, "h": 112}}
    robot = [0.017, 0.019, 0.389, 0.228]          # Haar on the robot
    face = [0.3, 0.2, 0.75, 0.45]
    samples = [(t, [(robot, 0), (face, 1)]) for t in (0.25, 0.75, 1.25)]
    assert render_qc.clipped_faces(samples, plan) == []
    assert render_qc.clipped_faces(samples, dict(plan, watermark=None))


@FFMPEG
def test_a_long_programme_reads_its_faces_on_keyframes(tmp_path, monkeypatch):
    import render_qc
    out = str(tmp_path / "long.mp4")
    _clip(out, "scale=270:480", d=4.0)
    monkeypatch.setattr(render_qc, "MAX_FULL_PASS_S", 2.0)
    plan = {"program_s": 4.0, "fps": 30.0, "W": 270, "H": 480,
            "end_frame": 120, "cut_frames": [], "events": [], "cards": [],
            "watermark": None, "endcard": None}
    res = render_qc.check(out, plan, budget_s=30)
    assert any(s.startswith("jumps: a 4s programme") for s in res["skipped"])
    assert not any(s.startswith("faces") for s in res["skipped"])
    assert render_qc._keyframe_times(out, 4.0, 10, __import__("time").monotonic() + 20)


@FFMPEG
def test_long_footage_is_sampled_by_seeks(tmp_path, monkeypatch):
    import insets
    out = str(tmp_path / "long.mp4")
    _clip(out, "scale=320:180", d=6.0, size="320x180")
    monkeypatch.setattr(insets, "SEEK_PAST_S", 3.0)
    rows = insets.measure(out, [(0.0, 2.5), (3.0, 6.0)], fps=1.0)
    assert [r[0] for r in rows] == [0.5, 1.5, 3.0, 4.0, 5.0]


# ── review fixes ───────────────────────────────────────────────────────────

def _blocks(edl, index, fps=30.0):
    import re
    graph = renderer.build_filtergraph(
        edl, 200.0, True, Timeline(edl["keep"]), None, [], index,
        preview=True, W=270, H=480, fps=fps, frame_mode="crop",
        src_w=1920, src_h=1080, src_fps=fps, focus_origin=0.0)
    return re.findall(r"trim=start=([0-9.]+):end=([0-9.]+)\[", graph) or \
        re.findall(r"trim=start=([0-9.]+):end=([0-9.]+)", graph)


@pytest.mark.parametrize("shots", [[], [{"id": 1, "start": 0.0, "end": 200.0}]])
def test_a_frame_named_edge_on_a_skip_join_still_switches_on_it(shots):
    """10.07 at 30 fps sits 3 ms after the frame at 10.0667: read as a
    measured cut it split [10.05, 10.07] and that last frame before the
    jump cut took the next span's crop — the judged pop, at an edge whose
    digits happen to name a frame. A join that skips source time is the
    programme's cut whatever the digits."""
    edl = _focus_edl([(5.0, 10.07), (20.0, 25.0)], 10.07)
    index = {"words": [], "shots": shots}
    assert renderer.composition_join(10.07, edl["keep"], index, 30.0) == 10.07
    trims = _blocks(edl, index)
    assert ("10.050", "10.070") not in trims
    assert ("5.000", "10.070") in trims


def test_with_a_shot_list_an_unindexed_edge_on_a_contiguous_join_is_the_join():
    """A shot list means detection ran: an edge with no indexed cut near it
    is editorial even where its digits name a frame. Only an index without
    one keeps the measured reading (the 138.04 case)."""
    keep = [(130.7, 138.04), (138.04, 138.58)]
    assert renderer.composition_join(138.04, keep, {}, 29.97) is None
    single = {"shots": [{"id": 1, "start": 0.0, "end": 200.0}]}
    assert renderer.composition_join(138.04, keep, single, 29.97) == 138.04


def test_a_carried_face_never_crosses_a_picture_cut():
    """Optical flow can 'track' a face's features into the next shot; the
    carry stops at a sample pair whose picture changes wholesale."""
    import follow
    rng = np.random.default_rng(5)
    import cv2
    tex = cv2.GaussianBlur(rng.integers(0, 255, size=(60, 50)).astype(np.uint8),
                           (3, 3), 0)
    frames, times = [], []
    for i in range(8):
        # shot A for 4 samples, then shot B: another background, the same
        # textured patch where the face was (flow would follow it)
        g = np.full((252, 448), 90 if i < 4 else 200, np.uint8)
        g[80:140, 200:250] = tex
        frames.append(g)
        times.append(round(i * .25, 3))
    box = [200 / 448, 80 / 252, 250 / 448, 140 / 252]
    dets = [[(list(box), 0)] if i == 0 else [] for i in range(8)]
    out = follow.carry(times, frames, dets)
    assert all(out[i] for i in range(1, 4))          # carried inside shot A
    assert not any(out[i] for i in range(4, 8))      # never into shot B
    assert follow.cut_between(frames[3], frames[4])
    assert not follow.cut_between(frames[2], frames[3])


def test_the_plan_knows_overlays_shifts_inserts_and_stacked_panels():
    import render_qc
    edl = default_edl(30.0)
    edl["overlays"] = [{"id": "b1", "asset_key": "k", "kind": "video",
                        "start": 2.0, "duration_s": 1.5, "fit": "cover"}]
    edl["effects"] = {"frame_shifts": [{"id": "s1", "at": 5.0, "ratio": "1:1",
                                        "duration_s": 0.8}],
                      "picture_cards": [{"id": "c", "start": 8.0, "end": 10.0,
                                         "panels": [
                                             {"box": [.04, .07, .96, .47],
                                              "source": [.0, .0, .5, 1.0]},
                                             {"box": [.04, .5, .96, .93],
                                              "source": [.5, .5, 1.0, 1.0]}]}]}
    edl = validate_edl(edl, 30.0).model_dump()
    p = render_qc.plan(edl, {}, W=270, H=480, fps=30.0)
    end = renderer.first_frame_at(3.5, 30.0)          # the cutaway's return
    assert any(a <= end <= b for a, b in p["events"])
    mid = renderer.first_frame_at(5.4, 30.0)          # inside the morph
    assert any(a <= mid <= b for a, b in p["events"])
    assert (2.0, 3.5) in p["others"]
    (card,) = p["cards"]
    assert card[2] == [[.04, .07, .96, .47], [.04, .5, .96, .93]]
    # a face inside the B-roll window is nobody the editor framed
    edge_face = ([0.0, .3, .2, .45], 0)
    samples = [(t, [edge_face]) for t in (2.25, 2.75, 3.25)]
    assert render_qc.clipped_faces(samples, p) == []
    assert render_qc.clipped_faces([(t, [edge_face]) for t in (12.0, 12.5)], p)
    # inside the stack the speaker panel's edge is the edge
    in_panel = ([.05, .2, .3, .4], 0)                  # 1% from the panel's left
    hits = render_qc.clipped_faces([(t, [in_panel]) for t in (8.5, 9.0)], p)
    assert hits and hits[0][2] == "left"


def test_the_watermark_is_read_by_seeks_not_a_whole_decode(monkeypatch, tmp_path):
    """Six frames of a long final decoded the whole programme (minutes), in
    a pipe the budget could not interrupt between frames."""
    import render_qc
    calls = []

    def fake(src, w, h, fps, ss=None, t=None, extra_vf="", deadline=None):
        calls.append((fps, ss, t, extra_vf))
        return [np.zeros((h, w), np.uint8)]
    monkeypatch.setattr(render_qc, "_gray_frames", fake)
    robot = renderer.robot_path()
    if not robot or not os.path.exists(robot):
        pytest.skip("no watermark robot bundled")
    f = tmp_path / "x.mp4"
    f.write_bytes(b"")
    plan = {"program_s": 1800.0, "W": 1080, "H": 1920,
            "watermark": {"x": 30, "y": 40, "w": 90, "h": 120, "robot": robot}}
    render_qc.robot_match(str(f), plan, __import__("time").monotonic() + 30)
    assert len(calls) == render_qc.ROBOT_SAMPLES
    assert all(c[0] is None and c[1] is not None and c[2] is None
               and "eq(n,0)" in c[3] for c in calls)
    assert max(c[1] for c in calls) < 1800.0


def test_a_fitted_screen_card_over_speech_says_nobody_is_seen(monkeypatch):
    import agent_tools
    ctx = _Ctx(_crop_edl(.42), words=_words())
    monkeypatch.setattr(agent_tools, "_inset_rect", lambda c, spans: list(INSET))
    res = agent_tools.set_picture_card(ctx, "screen", 0.0, 6.0, source="inset")
    assert res.startswith("EDL v") and "NO FACE ON SCREEN" in res
    assert "panels=" in res and "'inset'" in res.replace('"', "'")
    # a beat with no speech under it is the screen's to carry
    ctx = _Ctx(_crop_edl(.42))
    res = agent_tools.set_picture_card(ctx, "screen", 0.0, 6.0, source="inset")
    assert "NO FACE ON SCREEN" not in res


def test_the_screen_advice_splits_stacks_at_camera_cuts_only(monkeypatch):
    """A focus edge on a contiguous join (a re-aim inside one shot) must
    not split the speaker + screen stack: two framings of one shot jump."""
    import agent_tools
    edl = _crop_edl(.76, keep=((10.0, 14.0), (14.0, 20.0)))
    edl["frame"]["focus_track"] = [{"t0": 0.0, "t1": 14.0, "x": .76, "y": .5},
                                   {"t0": 14.0, "t1": 200.0, "x": .74, "y": .5}]
    ctx = _Ctx(edl, words=_words(), shots=[{"id": 1, "start": 0.0, "end": 200.0}])
    box = {"rect": list(INSET), "spans": [(10.0, 20.0)], "seen": 10}
    monkeypatch.setattr(agent_tools, "_inset_boxes", lambda c, w: [box])
    monkeypatch.setattr(agent_tools, "_follow_samples",
                        lambda c, w, cuts=(): (_faces(.42), "measured", [], None))
    text = "\n".join(agent_tools._framing_checks(ctx))
    assert "screen_1" not in text and "panels=" in text
    # the stack leads; the faceless fitted card is the fallback for a beat
    assert text.index("panels=") < text.index("source='inset') fits")


def test_the_cache_precheck_reaches_as_far_as_a_low_rate_join():
    """At 15 fps 1.5 frames is 0.1 s: an edge 0.08 s before its join is
    the join (the old handoff split [9.9, 10.0], a frame of the next crop),
    so the cheap pre-check must ask the index."""
    edl = _focus_edl([(5.0, 10.0), (20.0, 25.0)], 9.92)
    index = {"video": {"fps": 15.0}}
    assert renderer.composition_join(9.92, edl["keep"], index, 15.0) == 10.0
    assert renderer.handoff_affected(edl, index)
    assert renderer.handoff_may_matter(edl)
