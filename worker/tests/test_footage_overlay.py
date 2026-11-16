"""footage.overlay: other editors' text measured on a cut, and the band of the picture clear of it (16 Nov 2026)."""
import cv2
import numpy as np

from footage import overlay


def _clip(path, n=24, w=1280, h=720, words=True, label=False):
    """A moving scene (a drifting gradient with shapes) with subtitles that change every few frames at the bottom."""
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24, (w, h))
    lines = ["the first line of words here", "another sentence follows now", "and a third one at the end"]
    for k in range(n):
        f = np.zeros((h, w, 3), np.uint8)
        f[:] = (40 + k * 4, 80, 120)
        cv2.circle(f, (200 + k * 25, 300), 120, (200, 160, 60), -1)
        cv2.rectangle(f, (900 - k * 20, 150), (1100 - k * 20, 400), (60, 200, 90), -1)
        if words:
            t = lines[(k // 8) % len(lines)]
            cv2.putText(f, t, (220, int(h * 0.9)), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 4, cv2.LINE_AA)
        if label:
            cv2.putText(f, "VOLUME FACTORY", (60, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (255, 255, 255), 5, cv2.LINE_AA)
        vw.write(f)
    vw.release()
    return str(path)


def test_subtitles_are_measured_as_a_seated_line_where_they_stand(tmp_path):
    rec = overlay.measure(_clip(tmp_path / "s.mp4"))
    low = [s for s in rec["seated"] if s["box"][1] > 0.75]
    assert len(low) == 1 and low[0]["box"][0] < 0.2 and low[0]["box"][2] > 0.6 and low[0]["share"] >= 0.5


def test_a_clean_moving_scene_has_no_seated_text(tmp_path):
    rec = overlay.measure(_clip(tmp_path / "c.mp4", words=False))
    assert not rec["overlay"] and not rec["seated"]


def test_model_box_grows_to_the_measured_lines_it_touches():
    seated = [dict(box=[0.10, 0.84, 0.90, 0.95]), dict(box=[0.40, 0.10, 0.60, 0.14])]
    got = overlay.text_boxes([0.30, 0.86, 0.70, 0.93], seated)
    assert got == [[0.1, 0.84, 0.9, 0.95]]           # the far label is not touched, so not added
    assert overlay.text_boxes(None, seated) == []


def test_clear_band_drops_bottom_subtitles_and_still_fills_the_phone():
    r = overlay.clear_rect(1920, 1080, [[0.1, 0.84, 0.9, 0.95]], x=0.5)
    assert r[1] == 0.0 and r[3] <= 0.84 - overlay.PAD + 1e-6 and r[0] == 0.0 and r[2] == 1.0
    assert max(1080 / (1920 * (r[2] - r[0])), 1920 / (1080 * (r[3] - r[1]))) <= 3.0


def test_clear_band_takes_the_side_away_from_a_label_and_keeps_the_subject():
    r = overlay.clear_rect(1920, 1080, [[0.0, 0.05, 0.3, 0.95]], x=0.7)
    assert r[0] >= 0.3 and r[2] == 1.0 and r[1] == 0.0 and r[3] == 1.0


def test_no_band_when_text_covers_the_middle_of_a_small_picture():
    assert overlay.clear_rect(640, 360, [[0.05, 0.3, 0.95, 0.7]], x=0.5) is None


def test_remap_moves_faces_and_x_into_the_band():
    rect = [0.0, 0.0, 1.0, 0.8]
    face = dict(box=[0.4, 0.2, 0.5, 0.4], eyes=[[0.43, 0.28], [0.47, 0.28]])
    got = overlay.remap(rect, face)
    assert got["box"] == [0.4, 0.25, 0.5, 0.5] and got["eyes"][0] == [0.43, 0.35]
    assert overlay.remap(rect, dict(box=[0.4, 0.75, 0.5, 0.99])) is None
    assert overlay.remap([0.25, 0, 1, 1], 0.5) == 0.3333


def test_crop_clip_writes_the_band(tmp_path):
    import shutil
    import subprocess
    ff = shutil.which("ffmpeg")
    if not ff:
        import pytest
        pytest.skip("no ffmpeg")
    src = _clip(tmp_path / "s.mp4")
    dst = overlay.crop_clip(src, str(tmp_path / "o.mp4"), [0.0, 0.0, 1.0, 0.8], ff)
    cap = cv2.VideoCapture(dst)
    assert (int(cap.get(3)), int(cap.get(4))) == (1280, 576)
    cap.release()
    assert not overlay.measure(dst)["seated"]


def test_native_pixels_keep_a_wide_source_whole_for_full_bleed(tmp_path, monkeypatch):
    """FOOTAGE_NATIVE_PIXELS (the podcast shorts): a 1280x720 source stays 1280x720 instead of 1080x608, a 4K one comes
    down to the frame's longer side, and the default fit is unchanged for every other caller."""
    import shutil
    import pytest
    from footage import cut
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("no ffmpeg")
    monkeypatch.setenv("FOOTAGE_FFMPEG", ff)
    src = _clip(tmp_path / "w.mp4", n=6, words=False)
    for native, want in ((True, (1280, 720)), (False, (1080, 608))):
        out = str(tmp_path / f"o{native}.mp4")
        cut.finish(src, out, 1080, 1920, preserve_source=True, native=native)
        cap = cv2.VideoCapture(out)
        assert (int(cap.get(3)), int(cap.get(4))) == want
        cap.release()
