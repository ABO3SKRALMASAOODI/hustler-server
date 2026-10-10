"""Framing and cut hygiene (judges, Oct 2026).

Three findings from the showcase shorts, each fixed where the edit is written:

* a 646x480 archival source cropped to fill 9:16 was enlarged 4x (4.5x under
  punch-ins) — auto mode now shows such footage as a fitted window with a
  free headline band, and zoom writes cap their strength by the enlargement;
* a keep span ended 2 frames past a source camera cut and a focus_track
  switch sat 2 frames after another — keep and focus edges now snap onto
  indexed shot cuts;
* a burned-in browser inset left an 8% sliver down the crop's edge for 9 s —
  auto_reframe now slides the crop off persistent hard-edged bands.

Run:  python -m pytest tests/test_framing_hygiene.py -q     (from worker/)
"""

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import subject                                                # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402

SRC = 200.0
FPS = 30.0
STEP = 1.0 / FPS


class _Ctx:
    def __init__(self, width=1920, height=1080, shots=None, words=None,
                 edl=None, workdir=None):
        self.project_id = 1
        self.duration = SRC
        self.has_main_video = True
        self.workdir = workdir
        self.index = {"video": {"duration": SRC, "width": width,
                                "height": height, "fps": FPS},
                      "shots": shots or [], "words": words or [],
                      "sentences": [], "silences": []}
        self.written = []
        self._edl = validate_edl(edl or default_edl(SRC), SRC).model_dump()

    def clamp(self, t):
        return round(min(max(float(t), 0.0), self.duration), 2)

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"

    def proxy_path(self):
        raise RuntimeError("no proxy in this test")


def _shots(*cuts):
    edges = [0.0, *cuts, SRC]
    return [{"id": i + 1, "start": a, "end": b}
            for i, (a, b) in enumerate(zip(edges, edges[1:]))]


# ── shot-cut hygiene: keep edges ───────────────────────────────────────────

def test_keep_end_a_few_frames_past_a_cut_snaps_back_before_it():
    """The Elon showcase: the last span ended at 152.70, the camera cut at
    152.65 — two frames of an unrelated wide before the end card. The end
    lands half a frame BEFORE the indexed cut: that time is rounded to
    0.01 s and can sit just past the new shot's first frame."""
    keep, notes = agent_tools._snap_keep_to_shots(
        [[149.6, 152.7]], {"shots": _shots(152.65),
                           "video": {"fps": FPS}})
    assert keep == [[149.6, 152.63]]
    assert notes and "SHOT-CUT HYGIENE" in notes[0]
    assert "end 152.7->152.63 (" in notes[0] and "of the next shot" in notes[0]


def test_keep_end_on_a_rounded_cut_still_moves_off_the_new_shot():
    """The real Elon index puts the Rogan cut at 138.04 for a first frame
    at 138.0379: an end AT 138.04 still ends on one frame of the new shot."""
    fps = 30000 / 1001
    index = {"shots": _shots(138.04), "video": {"fps": fps}}
    keep, notes = agent_tools._snap_keep_to_shots([[130.7, 138.04]], index)
    assert keep == [[130.7, 138.02]]
    assert "(on the cut, cut at 138.04)" in notes[0]
    # the frame at 138.0045 (old shot) stays, the one at 138.0379 is gone
    assert 4136 / fps < keep[0][1] < 4137 / fps
    # idempotent: a second write moves nothing
    assert agent_tools._snap_keep_to_shots(keep, index) == (keep, [])


def test_keep_start_just_before_a_cut_moves_to_the_first_new_frame():
    keep, notes = agent_tools._snap_keep_to_shots(
        [[39.9, 45.0]], {"shots": _shots(40.0), "video": {"fps": FPS}})
    # one frame past the indexed cut: the cut can sit a frame early on the
    # source clock (the renderer shifts focus edges by the same frame)
    assert keep == [[40.03, 45.0]]
    assert "previous shot" in notes[0]
    # a start already ON the cut gets the same one-frame guard
    keep, _ = agent_tools._snap_keep_to_shots(
        [[40.0, 45.0]], {"shots": _shots(40.0), "video": {"fps": FPS}})
    assert keep[0][0] == 40.03


def test_keep_snap_leaves_clean_continuous_and_tiny_spans_alone():
    index = {"shots": _shots(40.0, 60.0), "video": {"fps": FPS}}
    # far from any cut: untouched, and the input list comes back as is
    clean = [[10.0, 20.0], [30.0, 39.5]]
    keep, notes = agent_tools._snap_keep_to_shots(clean, index)
    assert keep is clean and notes == []
    # two spans that touch AT the cut are one continuous source run
    # (set_transitions exposes baked cuts exactly like this)
    touching = [[30.0, 40.0], [40.0, 50.0]]
    assert agent_tools._snap_keep_to_shots(touching, index)[0] == touching
    # a snap that would leave less than SHOT_SNAP_MIN_SPAN_S is not made
    tiny = [[59.95, 60.1]]
    assert agent_tools._snap_keep_to_shots(tiny, index)[0] == tiny
    # 7 frames past the cut is a short shot, not a flash
    late = [[50.0, 60.0 + 7 * STEP]]
    assert agent_tools._snap_keep_to_shots(late, index)[1] == []


def test_keep_segments_reports_the_snap_and_the_words_it_trims():
    words = [{"w": "characters", "t0": 152.2, "t1": 152.68}]
    ctx = _Ctx(shots=_shots(152.65), words=words)
    res = agent_tools.keep_segments(ctx, [[149.6, 152.7]])
    assert res.startswith("EDL v1"), res
    assert ctx.latest_edl()["json"]["keep"] == [[149.6, 152.63]]
    assert "SHOT-CUT HYGIENE" in res and "trims 'characters'" in res


def test_cut_range_and_cut_output_range_snap_new_edges():
    ctx = _Ctx(shots=_shots(100.0))
    agent_tools.keep_segments(ctx, [[90.0, 110.0]])
    # cutting 98-99.95 leaves a span that starts 2 frames before the cut
    res = agent_tools.cut_range(ctx, 92.0, 99.95)
    assert "SHOT-CUT HYGIENE" in res
    assert ctx.latest_edl()["json"]["keep"] == [
        [90.0, 92.0], [100.03, 110.0]]

    ctx = _Ctx(shots=_shots(100.0))
    agent_tools.keep_segments(ctx, [[90.0, 110.0]])
    # output 0-10 is source 90-100; cutting output 10.05-15 leaves the first
    # span ending 1.5 frames past the camera cut at 100.0
    res = agent_tools.cut_output_range(ctx, 10.05, 15.0)
    assert res.startswith("EDL v"), res
    assert "SHOT-CUT HYGIENE" in res
    assert ctx.latest_edl()["json"]["keep"][0] == [90.0, 99.98]


def test_no_shots_in_the_index_means_no_snapping():
    ctx = _Ctx(shots=[])
    res = agent_tools.keep_segments(ctx, [[149.6, 152.7]])
    assert "SHOT-CUT" not in res
    assert ctx.latest_edl()["json"]["keep"] == [[149.6, 152.7]]


# ── shot-cut hygiene: focus_track edges ────────────────────────────────────

def test_focus_track_switch_two_frames_late_moves_onto_the_cut():
    """The Elon showcase: the crop re-aimed at 138.04, the cut was 137.98."""
    ctx = _Ctx(shots=_shots(137.98))
    res = agent_tools.set_frame(ctx, "9:16", "crop", focus_track=[
        {"t0": 120.0, "t1": 138.04, "x": 0.56, "y": 0.5},
        {"t0": 138.04, "t1": 160.0, "x": 0.40, "y": 0.5}])
    assert res.startswith("EDL v"), res
    track = ctx.latest_edl()["json"]["frame"]["focus_track"]
    assert [(sp["t0"], sp["t1"]) for sp in track] == [
        (120.0, 137.98), (137.98, 160.0)]
    assert "138.04->137.98" in res


def test_focus_track_outer_bounds_and_far_edges_stay_put():
    # outer bounds near a cut are not moved (that would uncover footage);
    # an internal edge 0.4 s from the nearest cut is a deliberate re-aim
    ctx = _Ctx(shots=_shots(120.1, 140.0, 160.1))
    res = agent_tools.set_frame(ctx, "9:16", "crop", focus_track=[
        {"t0": 120.0, "t1": 139.6, "x": 0.56, "y": 0.5},
        {"t0": 139.6, "t1": 160.0, "x": 0.40, "y": 0.5}])
    track = ctx.latest_edl()["json"]["frame"]["focus_track"]
    assert [(sp["t0"], sp["t1"]) for sp in track] == [
        (120.0, 139.6), (139.6, 160.0)]
    assert "SHOT-CUT" not in res


# ── focus handoffs land on the real first frame (renderer) ─────────────────

def test_focus_handoff_splits_half_a_frame_before_the_cut_frame():
    """The judged Elon flash: the index put the Rogan cut at 138.04 (first
    frame 4137 at 138.0379 on a zero-origin YouTube file) and the renderer
    split one frame LATER, so frame 4137 kept Elon's crop. Rendered and
    looked at: the split now lands between frames 4136 and 4137 — and on a
    one-frame-origin x264 encode between the same two frames shifted."""
    import renderer
    fps = 29.97
    zero = renderer.focus_handoff(138.04, fps, 0.0)
    assert 4136 / fps < zero < 4137 / fps
    one = renderer.focus_handoff(138.04, fps, 1 / fps)
    assert 4137 / fps < one < 4138 / fps
    # an edge rounded either way names the same frame
    assert renderer.focus_handoff(138.035, fps, 0.0) == pytest.approx(zero)
    # unknown origin (VFR): the legacy one-frame rule, byte-identical
    assert renderer.focus_handoff(3.0, 30.0, None, 30.0) == \
        pytest.approx(3.0 + 1 / 30)


def test_build_filtergraph_uses_the_origin_for_focus_blocks():
    import renderer
    from timeline import Timeline
    edl = default_edl(6.0)
    edl["keep"] = [[0.0, 6.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 0.0, "t1": 3.0, "x": 0.3, "y": 0.5},
        {"t0": 3.0, "t1": 6.0, "x": 0.7, "y": 0.5}]}
    edl = validate_edl(edl, 6.0).model_dump()
    graph = renderer.build_filtergraph(
        edl, 6.0, True, Timeline(edl["keep"]), None, [], {"words": []},
        preview=True, W=270, H=480, fps=30.0, frame_mode="crop",
        src_w=1920, src_h=1080, src_fps=30.0, focus_origin=0.0)
    # frame 90 (3.000) opens the new block; frame 89 (2.967) closes the old
    assert "trim=start=0.000:end=2.983" in graph
    assert "trim=start=2.983:end=6.000" in graph
    # each block still takes its own span's aim (x 0.3 then x 0.7)
    blocks = {p.split("]")[0]: p for p in graph.split(";")
              if p.startswith(("[segv0]", "[segv1]"))}
    assert "min(270,iw-ow)" in blocks["[segv0"]
    assert "min(1026,iw-ow)" in blocks["[segv1"]


def test_a_keep_junction_on_the_cut_gives_the_new_frame_the_new_aim():
    """set_transitions exposes a baked camera cut as touching keep spans at
    the indexed cut; the half-frame before that junction holds the new
    shot's first frame and must take the new span's aim. (Without an
    indexed cut there the join itself is the cut: see
    test_reframe_qc.test_a_focus_edge_on_an_unindexed_join_switches_on_it.)"""
    import renderer
    from timeline import Timeline
    edl = default_edl(6.0)
    edl["keep"] = [[0.0, 3.0], [3.0, 6.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 0.0, "t1": 3.0, "x": 0.3, "y": 0.5},
        {"t0": 3.0, "t1": 6.0, "x": 0.7, "y": 0.5}]}
    edl = validate_edl(edl, 6.0).model_dump()
    index = {"words": [], "shots": [{"id": 1, "start": 0.0, "end": 3.0},
                                    {"id": 2, "start": 3.0, "end": 6.0}]}
    graph = renderer.build_filtergraph(
        edl, 6.0, True, Timeline(edl["keep"]), None, [], index,
        preview=True, W=270, H=480, fps=30.0, frame_mode="crop",
        src_w=1920, src_h=1080, src_fps=30.0, focus_origin=0.0)
    blocks = {p.split("]")[0]: p for p in graph.split(";")
              if p.startswith("[segv")}
    assert "trim=start=2.983:end=3.000" in graph
    assert "min(270,iw-ow)" in blocks["[segv0"]       # 0 - 2.983: old aim
    assert "min(1026,iw-ow)" in blocks["[segv1"]      # 2.983 - 3.0: new
    assert "min(1026,iw-ow)" in blocks["[segv2"]


def test_probe_reports_where_the_picture_starts(tmp_path):
    import subprocess
    import media
    out = str(tmp_path / "clip.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=160x90:rate=30:duration=1", "-c:v",
                    "libx264", "-pix_fmt", "yuv420p", out], check=True)
    info = media.probe(out)
    assert info["video_start"] is not None and info["video_start"] >= 0.0
    assert info["format_start"] is not None


# ── resolution-aware framing ───────────────────────────────────────────────

@pytest.mark.parametrize("size,ratio,mode,expected", [
    ((646, 480), "9:16", "crop", 4.0),       # the archival Jobs talk
    ((646, 480), "9:16", "pad_blur", 1.672),
    ((1920, 1080), "9:16", "crop", 1.778),
    ((1280, 720), "9:16", "crop", 2.667),
    # a 4K original keeps its own 2160x3840 canvas: same 1.78x as 1080p
    ((3840, 2160), "9:16", "crop", 1.778),
])
def test_frame_upscale_measures_the_final_enlargement(size, ratio, mode,
                                                       expected):
    index = {"video": {"width": size[0], "height": size[1]}}
    assert agent_tools._frame_upscale(index, ratio, mode) == \
        pytest.approx(expected, abs=0.01)


def test_auto_reframe_windows_a_low_resolution_source_with_headline_band():
    ctx = _Ctx(width=646, height=480)
    # no frames are needed (proxy_path raises): resolution decides first
    res = agent_tools.auto_reframe(ctx, "9:16", "auto")
    frame = ctx.latest_edl()["json"]["frame"]
    # below 720p the bars are near-black, never a blurred self-copy
    assert frame["mode"] == "pad" and frame["ratio"] == "9:16"
    assert "RESOLUTION-AWARE WINDOW" in res
    assert "4.0x" in res and "TOP band (y 0-0.29)" in res
    assert "kind='headline'" in res
    # the old "they wanted it to FILL the phone, call crop" push is gone
    assert "LETTERBOXES" not in res


def test_auto_reframe_keeps_filling_hd_sources():
    ctx = _Ctx(width=1920, height=1080)
    res = agent_tools.auto_reframe(ctx, "9:16", "auto")
    assert "RESOLUTION-AWARE" not in res


def test_explicit_crop_on_low_resolution_source_still_crops_with_a_note():
    ctx = _Ctx(width=646, height=480)
    res = agent_tools.set_frame(ctx, "9:16", "crop", 0.44, 0.5)
    assert ctx.latest_edl()["json"]["frame"]["mode"] == "crop"
    assert "enlarges the source 4.0x" in res
    ctx = _Ctx(width=1920, height=1080)
    assert "enlarges the source" not in agent_tools.set_frame(
        ctx, "9:16", "crop", 0.44, 0.5)


def test_pad_blur_on_hd_keeps_the_fill_reminder():
    ctx = _Ctx(width=1920, height=1080)
    res = agent_tools.set_frame(ctx, "9:16", "pad_blur")
    assert "LETTERBOXES" in res and "RESOLUTION-AWARE" not in res


# ── zoom strength capped by the enlargement ────────────────────────────────

def _framed(width, height, frame, zooms=None):
    edl = default_edl(SRC)
    edl["keep"] = [[0.0, 40.0]]
    edl["frame"] = frame
    if zooms:
        edl["effects"] = {"zooms": zooms}
    return _Ctx(width=width, height=height, edl=edl)


def test_zoom_on_an_archival_crop_is_capped_with_a_note():
    ctx = _framed(646, 480, {"ratio": "9:16", "mode": "crop",
                             "focus_x": 0.44})
    res = agent_tools.add_zoom(ctx, 2.0, 4.0, strength=0.12, mode="punch",
                               cx=0.5, cy=0.4)
    assert res.startswith("EDL v"), res
    zoom = ctx.latest_edl()["json"]["effects"]["zooms"][-1]
    # already 4x: only the minimum move remains
    assert zoom["strength"] == agent_tools.ZOOM_STRENGTH_MIN
    assert "NOTE: zoom strength capped 0.12 -> 0.05" in res
    assert "646x480" in res and "4.0x" in res


def test_zoom_on_hd_crop_keeps_ordinary_strengths_and_caps_extremes():
    ctx = _framed(1920, 1080, {"ratio": "9:16", "mode": "crop"})
    res = agent_tools.add_zoom(ctx, 2.0, 4.0, strength=0.3, cx=0.5, cy=0.4)
    assert "capped" not in res
    assert ctx.latest_edl()["json"]["effects"]["zooms"][-1]["strength"] == 0.3
    res = agent_tools.add_zoom(ctx, 6.0, 8.0, strength=1.5, cx=0.5, cy=0.4)
    # 3.0 / 1.778 - 1 = 0.6875 -> 0.68
    assert ctx.latest_edl()["json"]["effects"]["zooms"][-1]["strength"] == 0.68
    assert "capped 1.5 -> 0.68" in res


def test_overlapping_zooms_share_the_cap():
    ctx = _framed(1920, 1080, {"ratio": "9:16", "mode": "crop"}, zooms=[
        {"id": "zm1", "start": 0.0, "end": 10.0, "strength": 0.3,
         "mode": "push_in"}])
    res = agent_tools.add_zoom(ctx, 2.0, 4.0, strength=0.6, cx=0.5, cy=0.4)
    assert ctx.latest_edl()["json"]["effects"]["zooms"][-1]["strength"] == 0.38
    assert "overlapping zooms add 0.3" in res


def test_window_layout_leaves_room_for_a_punch():
    ctx = _framed(646, 480, {"ratio": "9:16", "mode": "pad_blur"})
    res = agent_tools.add_zoom(ctx, 2.0, 4.0, strength=0.12, cx=0.5, cy=0.5)
    assert "capped" not in res


def test_zoom_path_keyframes_are_capped():
    ctx = _framed(1920, 1080, {"ratio": "9:16", "mode": "crop"})
    res = agent_tools.add_zoom_path(ctx, [
        {"t": 1.0, "cx": 0.5, "cy": 0.5, "strength": 0},
        {"t": 2.0, "cx": 0.5, "cy": 0.4, "strength": 1.2},
        {"t": 3.0, "cx": 0.5, "cy": 0.5, "strength": 0}])
    assert res.startswith("EDL v"), res
    zoom = ctx.latest_edl()["json"]["effects"]["zooms"][-1]
    assert max(p["s"] for p in zoom["path"]) <= 0.68 + 1e-9
    assert "capped 1.2 -> 0.68" in res


def test_batch_advisories_name_what_the_tools_would_fix():
    ctx = _Ctx(shots=_shots(152.65))
    before = ctx.latest_edl()["json"]
    after = dict(before, keep=[[149.6, 152.7]],
                 frame={"ratio": "9:16", "mode": "crop"},
                 effects={"zooms": [{"id": "zm1", "start": 1.0, "end": 2.0,
                                     "strength": 1.5}]})
    notes = agent_tools._batch_framing_advisories(ctx, before, after)
    assert any("SHOT-CUT HYGIENE" in n for n in notes)
    assert any("zoom zm1" in n and "capped 1.5" in n for n in notes)
    assert all(n.startswith("ADVISORY (not applied") for n in notes)


# ── burned-in bands at the crop edge ───────────────────────────────────────

def _scene(path, inset_x0=None, seed=0, inset_x1=0.98):
    """A synthetic 640x360 talking-head frame: soft gradient wall, a head,
    and optionally a dark burned-in browser inset from inset_x0 to the right
    edge over the lower half (the Rogan/Elon showcase geometry)."""
    import cv2
    import numpy as np
    rng = np.random.default_rng(seed)
    h, w = 360, 640
    ramp = np.linspace(70, 150, w, dtype=np.float32)[None, :]
    img = np.repeat(ramp, h, axis=0)
    img += rng.normal(0, 4, (h, w)).astype(np.float32)
    cv2.ellipse(img, (int(0.41 * w), int(0.45 * h)), (60, 80), 0, 0, 360,
                200, -1)
    if inset_x0 is not None:
        x0 = int(inset_x0 * w)
        img[int(0.52 * h):int(0.98 * h), x0:int(inset_x1 * w)] = 18
        for k in range(6):          # text-ish rows inside the inset
            y = int(0.6 * h) + 14 * k
            img[y:y + 3, x0 + 20:x0 + 200] = 120
    cv2.imwrite(path, np.clip(img, 0, 255).astype(np.uint8))
    return path


def test_hard_edge_lines_find_a_persistent_inset_and_ignore_clean_frames(
        tmp_path):
    inset = [_scene(str(tmp_path / f"i{k}.jpg"), 0.533, k) for k in range(3)]
    lines = subject.hard_edge_lines(inset, "x")
    assert any(abs(p - 0.533) < 0.01 for p, _run in lines), lines
    clean = [_scene(str(tmp_path / f"c{k}.jpg"), None, k) for k in range(3)]
    assert subject.hard_edge_lines(clean, "x") == []
    # an edge that moves between frames is not a burned-in band
    moving = [_scene(str(tmp_path / f"m{k}.jpg"), 0.45 + 0.1 * k, k,
                     0.75 + 0.1 * k) for k in range(3)]
    assert subject.hard_edge_lines(moving, "x") == []
    assert subject.hard_edge_lines([str(tmp_path / "missing.jpg")]) == []


def test_crop_slides_off_an_inset_sliver(tmp_path):
    frames = [_scene(str(tmp_path / f"i{k}.jpg"), 0.533, k) for k in range(3)]
    ctx = _Ctx(width=1920, height=1080)
    focus, note = agent_tools._clear_crop_edges(ctx, "9:16", (0.40, 0.45),
                                                frames)
    assert "EDGE BAND CLEARED" in note and "right edge" in note
    cw = (9 / 16) / (16 / 9)
    assert focus[0] + cw / 2 < 0.533          # the inset is out of the crop
    assert focus[0] - cw / 2 >= 0.0
    assert focus[1] == 0.45


def test_crop_edge_review_when_the_slide_would_cut_the_face(tmp_path):
    frames = [_scene(str(tmp_path / f"i{k}.jpg"), 0.533, k) for k in range(3)]
    ctx = _Ctx(width=1920, height=1080)
    # the measured face runs right up to the inset: excluding the band
    # would cut it, so the aim stays and the editor is asked to LOOK — the
    # detector cannot tell an inset from a door frame, so it never orders a
    # fit on its own
    focus, note = agent_tools._clear_crop_edges(
        ctx, "9:16", (0.40, 0.45), frames, [[0.33, 0.25, 0.53, 0.6]])
    assert focus == (0.40, 0.45)
    assert note.startswith("EDGE BAND (look before acting)")
    assert "REVIEW REQUIRED" not in note and "look_at" in note
    # a clean picture changes nothing and says nothing
    clean = [_scene(str(tmp_path / f"c{k}.jpg"), None, k) for k in range(3)]
    assert agent_tools._clear_crop_edges(ctx, "9:16", (0.40, 0.45),
                                         clean) == ((0.40, 0.45), "")


def test_an_edge_deeper_than_a_sliver_is_scenery_and_left_alone(tmp_path):
    """Real podcast sets are full of straight static edges — a still
    speaker's hair line against a curtain, door and window frames. On the
    Elon showcase source (Rogan studio, t=793) a 35% reach slid the crop
    off Elon's own head contour 27% inside it and cut into his face. Only a
    SLIVER (up to CROP_SLIVER_MAX of the crop) is slid off."""
    frames = [_scene(str(tmp_path / f"d{k}.jpg"), 0.473, k)
              for k in range(3)]
    ctx = _Ctx(width=1920, height=1080)
    # crop x 0.242-0.558: the edge at 0.473 sits 27% inside its right edge
    assert subject.hard_edge_lines(frames, "x")
    assert agent_tools._clear_crop_edges(ctx, "9:16", (0.40, 0.45),
                                         frames) == ((0.40, 0.45), "")


def _panels(path, seed=0):
    """Wood panelling: a straight seam every 5% of the width."""
    import cv2
    import numpy as np
    rng = np.random.default_rng(seed)
    h, w = 360, 640
    img = np.full((h, w), 90.0, np.float32)
    for k in range(20):
        x0 = int(k * 0.05 * w)
        img[:, x0:x0 + int(0.025 * w)] = 150.0
    img += rng.normal(0, 3, (h, w)).astype(np.float32)
    cv2.ellipse(img, (int(0.41 * w), int(0.45 * h)), (60, 80), 0, 0, 360,
                210, -1)
    cv2.imwrite(path, np.clip(img, 0, 255).astype(np.uint8))
    return path


def test_a_pattern_of_lines_is_not_an_inset(tmp_path):
    frames = [_panels(str(tmp_path / f"p{k}.jpg"), k) for k in range(3)]
    ctx = _Ctx(width=1920, height=1080)
    assert len(subject.hard_edge_lines(frames, "x")) >= \
        agent_tools.CROP_SLIVER_PATTERN_LINES
    assert agent_tools._clear_crop_edges(ctx, "9:16", (0.40, 0.45),
                                         frames) == ((0.40, 0.45), "")


def test_set_frame_edge_checks_are_bounded_per_camera_position(monkeypatch,
                                                               tmp_path):
    """A 24-cut two-camera track is two camera positions: two checks of
    three frames, not 72 ffmpeg seeks on a framing write."""
    src = _scene(str(tmp_path / "src.jpg"), None)
    ctx = _Ctx(width=1920, height=1080, workdir=str(tmp_path))
    ctx.proxy_path = lambda: "proxy.mp4"
    grabs = []

    def grab(_proxy, t, out):
        grabs.append(t)
        shutil.copyfile(src, out)

    monkeypatch.setattr(agent_tools.media, "frame_at", grab)
    agent_tools.keep_segments(ctx, [[0.0, 120.0]])
    track = [{"t0": 5.0 * i, "t1": 5.0 * (i + 1),
              "x": 0.3 if i % 2 else 0.7, "y": 0.5} for i in range(24)]
    res = agent_tools.set_frame(ctx, "9:16", "crop", focus_track=track)
    assert res.startswith("EDL v"), res
    assert 0 < len(grabs) <= 6
    # a ratio that crops nothing has no crop edge: no frames at all
    grabs.clear()
    agent_tools.set_frame(ctx, "16:9", "crop", 0.4, 0.5)
    assert grabs == []


def test_zoom_over_a_spliced_insert_is_not_capped_by_the_main_source():
    """Zooms render over the assembled program; a window that shows only an
    insert (its own resolution) is not limited by the 480p main source."""
    edl = default_edl(SRC)
    edl["keep"] = [[0.0, 10.0], [10.0, 20.0]]
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_x": 0.44}
    edl["inserts"] = [{"id": "ins1", "at_output_s": 10.0, "duration_s": 4.0,
                       "asset_key": "broll.mp4", "kind": "video"}]
    ctx = _Ctx(width=646, height=480, edl=edl)
    room = agent_tools._zoom_room(ctx, ctx.latest_edl()["json"], 10.5, 13.5)
    assert room[0] is None
    # ...while footage around it still is
    assert agent_tools._zoom_room(ctx, ctx.latest_edl()["json"],
                                  2.0, 4.0)[0] is not None


def test_auto_reframe_aims_the_crop_off_the_inset(monkeypatch, tmp_path):
    src = _scene(str(tmp_path / "src.jpg"), 0.533)
    faces = [[0.36, 0.25, 0.46, 0.55]]
    sidecar = {"samples": [{"t": t, "faces": faces} for t in
                           (2.0, 6.0, 10.0, 14.0, 18.0)]}
    ctx = _Ctx(width=1920, height=1080, workdir=str(tmp_path))
    ctx._spatial = sidecar
    ctx.proxy_path = lambda: "proxy.mp4"
    edl = dict(ctx.latest_edl()["json"], keep=[[0.0, 20.0]])
    ctx._edl = validate_edl(edl, SRC).model_dump()
    monkeypatch.setattr(agent_tools.media, "frame_at",
                        lambda _p, _t, out: shutil.copyfile(src, out))
    monkeypatch.setattr(agent_tools.subject, "crop_detail_kept",
                        lambda *_a, **_k: 0.9)
    res = agent_tools.auto_reframe(ctx, "9:16", "auto")
    assert res.startswith("EDL v"), res
    frame = ctx.latest_edl()["json"]["frame"]
    cw = (9 / 16) / (16 / 9)
    assert frame["mode"] == "crop"
    assert frame["focus_x"] + cw / 2 < 0.533
    assert "EDGE BAND CLEARED" in res


def test_shorts_story_seed_accepts_the_shot_snapped_keep(monkeypatch,
                                                         tmp_path):
    """The shorts scout seeds each child through keep_segments and verifies
    the deterministic result; that check must expect the cut-snapped edge
    instead of failing the whole materialization."""
    import db as dbx
    import shorts

    ctx = _Ctx(shots=_shots(152.65))

    class FakeDb:
        def run(self, fn, *args):
            if fn is dbx.get_project:
                return {"id": args[0]}
            raise AssertionError(fn)

    monkeypatch.setattr(agent_tools, "ToolContext",
                        lambda *_args, **_kwargs: ctx)
    monkeypatch.setattr(agent_tools, "execute",
                        lambda c, name, args: agent_tools.keep_segments(
                            c, **args))
    version, note = shorts._seed_story_child(
        FakeDb(), {"id": 9}, 71, ctx.index, {"start": 140.0, "end": 152.7},
        str(tmp_path))
    assert version == 2 and note.startswith("EDL v1")
    assert ctx.latest_edl()["json"]["keep"] == [[140.0, 152.63]]


def test_set_frame_keeps_an_authored_aim_but_names_the_sliver(monkeypatch,
                                                              tmp_path):
    """The Elon showcase authored x=0.40 by hand; set_frame keeps that aim
    and says which x excludes the inset."""
    src = _scene(str(tmp_path / "src.jpg"), 0.533)
    ctx = _Ctx(width=1920, height=1080, workdir=str(tmp_path))
    ctx.proxy_path = lambda: "proxy.mp4"
    seen = []

    def grab(_proxy, t, out):
        seen.append(t)
        shutil.copyfile(src, out)

    monkeypatch.setattr(agent_tools.media, "frame_at", grab)
    agent_tools.keep_segments(ctx, [[0.0, 4.0], [10.0, 12.0]])
    res = agent_tools.set_frame(ctx, "9:16", "crop", 0.40, 0.45)
    assert ctx.latest_edl()["json"]["frame"]["focus_x"] == 0.40
    assert "EDGE BAND:" in res and "would exclude it" in res
    # sampled only footage the viewer sees
    assert seen and all(0.0 <= t <= 4.0 or 10.0 <= t <= 12.0 for t in seen)
    # auto_reframe's own writes (_measured) do not re-sample
    seen.clear()
    agent_tools.set_frame(ctx, "9:16", "crop", 0.40, 0.45, _measured=True)
    assert seen == []
