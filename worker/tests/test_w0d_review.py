"""Track 0D review fixes: a standing check or metric that measured nothing
never passes, the brand check sees the mark inside device windows, every
zoom source counts, label void masks reach matte_iou, the reference flash
rate is the flash_risk counter, and the RVM file is pinned.
CI-safe: synthetic inputs only, no footage, no references, no models.
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402

import qa_metrics as q                                        # noqa: E402
import refsig                                                 # noqa: E402
from benchmarks.refbench import labels, standing, verdict     # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None and \
    shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg")

LENSES = ("motion", "craft", "owner_rules")


def _case():
    return {"id": "AT-0D-1a", "judges": {"lenses": list(LENSES),
                                         "pass": "2of3_no_major"}}


def _judges():
    return [{"lens": lens, "verdict": "matches", "major_defect": False,
             "notes": ""} for lens in LENSES]


# ── an unmeasured bar is a failure ────────────────────────────────────────

def test_a_bar_nobody_measured_fails_the_case():
    m = {"glyph_visible": {"value": None, "bar": ">=0.65", "pass": None},
         "land_err_f": {"value": 0.0, "bar": "<=1", "pass": True}}
    assert verdict.unmeasured_metrics(m) == ["glyph_visible"]
    assert verdict.decide(_case(), m, {"render_ok": True}, _judges()) \
        is False
    v = verdict.build(_case(), render_path=None, variant="final", metrics=m,
                      standing={"render_ok": True}, judges=_judges())
    assert v["pass"] is False
    assert v["metrics_unmeasured"] == ["glyph_visible"]


def test_reported_metrics_and_flagged_opt_ins_still_pass():
    m = {"cost_ratio": {"value": 1.3, "bar": None, "pass": None},
         "flash_risk": {"value": 9.0, "bar": "<=3", "pass": None},
         "land_err_f": {"value": 1.0, "bar": "<=1", "pass": True}}
    assert verdict.unmeasured_metrics(m) == []
    assert verdict.decide(_case(), m, {"render_ok": True}, _judges()) is True


def test_metrics_that_find_nothing_report_none_not_a_pass():
    r = q.glyph_visible(glyphs=[np.zeros((4, 4))], subject=np.zeros((4, 4)))
    assert r["value"] is None and r["bar"] == ">=0.65"
    assert verdict.unmeasured_metrics({"glyph_visible": r}) == \
        ["glyph_visible"]


def test_a_cut_target_is_its_program_time_through_speed_spans():
    from benchmarks.refbench import runner
    edl = {"keep": [[10.0, 14.4], [21.2, 25.2]]}
    assert runner._target_time(edl, [], {"start": 4.3}, {"cut": 1}) == \
        pytest.approx(4.4)
    edl["speed"] = [{"start": 11.0, "end": 13.0, "factor": 2.0}]
    assert runner._target_time(edl, [], {"start": 3.3}, {"cut": 1}) == \
        pytest.approx(3.4)


# ── matte_iou honours the label's void pixels ────────────────────────────

def test_matte_iou_excludes_void_pixels_and_reports_the_strict_number():
    truth = np.zeros((20, 20), bool)
    truth[5:15, 5:15] = True
    pred = truth.copy()
    pred[5:15, 15:17] = True                      # a 2 px halo on one side
    void = np.zeros((20, 20), bool)
    void[:, 14:18] = True                         # the label's soft edge
    plain = q.matte_iou(pred={3: pred}, truth={3: truth})
    with_void = q.matte_iou(pred={3: pred}, truth={3: truth},
                            void={3: void})
    assert plain["value"] == pytest.approx(100 / 120)
    assert with_void["value"] == pytest.approx(1.0)
    assert with_void["detail"]["mean_without_void_exclusion"] == \
        pytest.approx(round(100 / 120, 4))
    # list form, and a frame without a void file counts every pixel
    r = q.matte_iou(pred=[pred, pred], truth=[truth, truth], void=[void])
    assert r["detail"]["per_frame"] == {"0": 1.0, "1": round(100 / 120, 4)}
    with pytest.raises(ValueError):
        q.matte_iou(pred={0: pred}, truth={0: truth},
                    void={0: np.zeros((5, 5), bool)})


def test_object_bar_applies_even_when_nothing_was_scored():
    r = q.matte_iou(pred={}, truth={1: np.ones((2, 2))}, kind="object")
    assert r["value"] is None and r["bar"] == ">=0.85"


def test_read_void_pairs_with_read_masks(tmp_path, monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setenv("VALMERA_FIXTURES_DIR", str(tmp_path))
    m = np.zeros((8, 8), bool)
    m[2:6, 2:6] = True
    v = np.zeros((8, 8), bool)
    v[2:6, 5] = True
    labels.write_mask("V99", "w", 4, m)
    labels.write_mask("V99", "w", 4, v, "person_void")
    got = labels.read_void("V99", "w")
    assert list(got) == [4] and (got[4] == v).all()
    assert labels.read_void("V99", "nothing") == {}


# ── owner rules: every zoom source counts ────────────────────────────────

def test_every_zoom_source_counts_against_the_allow_list():
    edl = {"effects": {
        "zooms": [{"id": "cs1", "cut_step": True}, {"id": "z5"}],
        "transition": {"style": "zoom_punch"},
        "frame_shifts": [{"id": "f1", "zoom": True},
                         {"id": "f2", "zoom": False}]}}
    s = standing.edl_summary(edl)
    assert s["zoom_ids"] == ["cs1 (cut_step)", "z5"]
    assert (s["zooms"], s["zoom_transitions"], s["frame_shift_zooms"]) == \
        (2, 1, 1)
    ok, det = standing.owner_rules(edl, [])
    assert ok is False and det["violations"] == [
        "4 zoom(s) not asked for (allow lacks zoom)"]
    assert standing.owner_rules(edl, ["zoom"])[0] is True
    assert standing.owner_rules({"effects": {"transition": {
        "style": "flash"}}}, [])[0] is True


# ── brand integrity: the mark is checked inside device windows ───────────

W, H = 96, 64
ROI = (8, 6, 56, 30)                       # x0, y0, x1, y1
CORE_X, CORE_Y = 14, 12                    # the robot's corner


def _robot():
    m = np.zeros((12, 10), bool)
    m[1:11, 1:9] = True
    rgb = np.full((12, 10, 3), 240, np.uint8)
    return CORE_X, CORE_Y, m, rgb


def _clip(path, frames):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-s", f"{W}x{H}", "-r", "10", "-i", "-",
           "-c:v", "ffv1", "-pix_fmt", "bgr0", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f, np.uint8).tobytes())
    p.stdin.close()
    assert p.wait() == 0


def _frames(n=30, robot=True, cover=(), shade=()):
    """A moving plate with the robot pasted LAST (like the mark); frames in
    ``cover`` get a graphic drawn OVER the robot, frames in ``shade`` a
    graphic UNDER it (the whole ROI darkened before the robot)."""
    x, y, m, rgb = _robot()
    out = []
    for k in range(n):
        f = np.full((H, W, 3), 40 + 3 * k, np.uint8)
        if k in shade:
            f[ROI[1]:ROI[3], ROI[0]:ROI[2]] = 5
        if robot:
            sub = f[y:y + m.shape[0], x:x + m.shape[1]]
            sub[m] = rgb[m]
        if k in cover:
            f[y:y + 6, x:x + 10] = (255, 0, 0)
        out.append(f)
    return out


@pytest.fixture
def brand(monkeypatch, tmp_path):
    monkeypatch.setattr(standing, "watermark_roi", lambda w, h: ROI)
    monkeypatch.setattr(standing, "robot_core", lambda w, h: _robot())

    def run(render_frames, control_frames, windows=()):
        r, c = str(tmp_path / "r.mkv"), str(tmp_path / "c.mkv")
        _clip(r, render_frames)
        _clip(c, control_frames)
        return standing.brand_integrity(r, c, outro_s=0.0,
                                        device_windows=windows)
    return run


@needs_ffmpeg
def test_brand_checks_the_robot_inside_device_windows(brand):
    # a device UNDER the mark for the whole programme: the ROI changes,
    # the robot does not -> measured (robot frames), and a pass
    ok, det = brand(_frames(shade=range(30)), _frames(), windows=[(0, 3)])
    assert ok is True
    assert det["watermark_frames_compared"] == 0
    assert det["robot_frames_compared"] == 10
    assert det["robot_present_in_control"] is True
    # a device OVER the mark inside its window: caught
    ok, det = brand(_frames(cover={12}), _frames(), windows=[(1.0, 1.5)])
    assert ok is False and det["robot_max_abs_diff"] > 0


@needs_ffmpeg
def test_brand_outside_windows_still_compares_the_whole_roi(brand):
    ok, det = brand(_frames(shade={9}), _frames(), windows=[])
    assert ok is False and det["watermark_max_abs_diff"] > 0
    ok, det = brand(_frames(), _frames(), windows=[(2.0, 2.4)])
    assert ok is True and det["watermark_frames_compared"] > 0


@needs_ffmpeg
def test_brand_never_passes_on_nothing(brand, monkeypatch):
    # no robot in the control: comparing its pixels proves nothing
    ok, det = brand(_frames(robot=False), _frames(robot=False),
                    windows=[(0, 3)])
    assert ok is None and "no robot" in det["why"]
    # no robot asset and every frame inside a device window
    monkeypatch.setattr(standing, "robot_core", lambda w, h: None)
    ok, det = brand(_frames(), _frames(), windows=[(0, 3)])
    assert ok is None and det["why"] == "no watermark pixel was compared"


@needs_ffmpeg
def test_previews_and_lossy_finals_leave_brand_unmeasured(brand, tmp_path,
                                                          monkeypatch):
    brand(_frames(), _frames())                    # writes r.mkv / c.mkv
    r, c = str(tmp_path / "r.mkv"), str(tmp_path / "c.mkv")
    case = {"id": "AT-1B-1", "allow": [], "render": {"variant": "preview"}}
    st, det = standing.run_all(case, r, control=c, edl={})
    assert st["render_ok"] is True
    assert st["brand_integrity"] is None
    assert "preview" in det["brand_integrity"]["why"]
    import config
    case["render"]["variant"] = "final"
    monkeypatch.setattr(config, "FINAL_CRF", 18)
    st, det = standing.run_all(case, r, control=c, edl={})
    assert st["brand_integrity"] is None and \
        "FINAL_CRF=0" in det["brand_integrity"]["why"]
    monkeypatch.setattr(config, "FINAL_CRF", 0)
    st, det = standing.run_all(case, r, control=c, edl={})
    assert st["brand_integrity"] is True


def test_robot_core_matches_the_watermark_geometry():
    pytest.importorskip("PIL")
    import renderer
    if not renderer.robot_path():
        pytest.skip("brand asset missing")
    core = standing.robot_core(1080, 1920)
    g = renderer.watermark_geometry(1080, 1920)
    x, y, m, rgb = core
    assert (x, y) == (g["rx"], g["ry"])
    assert m.shape == (g["rh"], g["rw"]) == rgb.shape[:2]
    assert 0.1 < m.mean() < 0.6                    # the eroded silhouette
    x0, y0, x1, y1 = standing.watermark_roi(1080, 1920)
    assert x0 <= x and y0 <= y and x + m.shape[1] <= x1 and \
        y + m.shape[0] <= y1


# ── read_frames: an odd rect on a 4:2:0 stream is exact ──────────────────

@needs_ffmpeg
def test_odd_rect_on_a_subsampled_stream_is_the_exact_slice(tmp_path):
    rng = np.random.default_rng(5)
    frames = [rng.integers(0, 255, (48, 64, 3)).astype(np.uint8)
              for _ in range(6)]
    p = str(tmp_path / "y420.mkv")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-s", "64x48", "-r", "10", "-i", "-", "-c:v", "ffv1",
           "-pix_fmt", "yuv420p", p]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        pr.stdin.write(f.tobytes())
    pr.stdin.close()
    assert pr.wait() == 0
    _, full = q.read_frames(p)
    for rect in ((3, 5, 21, 13), (2, 4, 31, 20), (1, 0, 64 - 1, 48)):
        x, y, w, h = rect
        idx, got = q.read_frames(p, rect=rect)
        assert idx == list(range(6))
        for a, b in zip(got, full):
            assert a.shape == (h, w, 3)
            assert (a == b[y:y + h, x:x + w]).all()
        _, g = q.read_frames(p, rect=rect, gray=True, frames={4})
        assert g[0].shape == (h, w)


# ── refsig: the flash rate is the flash_risk counter; RVM is pinned ──────

@needs_ffmpeg
def test_reference_flash_rate_is_the_flash_risk_counter(tmp_path):
    # 30 opposing transitions in frames 20-49: one second, but straddling
    # the half-second grid a sampled count used (its best window, 15-44,
    # holds 12 flashes); every 1 s window is 15
    frames = []
    for k in range(60):
        on = 20 <= k < 49 and k % 2 == 0
        frames.append(np.full((36, 64, 3), 235 if on else 20, np.uint8))
    p = str(tmp_path / "f.mkv")
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
           "rgb24", "-s", "64x36", "-r", "30", "-i", "-", "-c:v", "ffv1",
           "-pix_fmt", "bgr0", p]
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        pr.stdin.write(f.tobytes())
    pr.stdin.close()
    assert pr.wait() == 0
    sig = refsig.analyze(p)
    want = q.flash_risk(frames=frames, fps=30.0)["value"]
    assert sig["flash_rate_max_per_s"] == int(want) == 15
    assert sig["contains_flashing"] is True


def test_rvm_file_must_match_its_pin(tmp_path, monkeypatch):
    bad = tmp_path / "rvm.onnx"
    bad.write_bytes(b"not the model")
    monkeypatch.setenv("VALMERA_RVM_MODEL", str(bad))
    ok, why = refsig.rvm_status()
    assert ok is False and "sha256" in why
    with pytest.raises(RuntimeError, match="sha256"):
        refsig.person_masks(str(tmp_path / "x.mp4"), [0])
    monkeypatch.setenv("VALMERA_RVM_MODEL", str(tmp_path / "missing.onnx"))
    assert refsig.rvm_status()[0] is False
