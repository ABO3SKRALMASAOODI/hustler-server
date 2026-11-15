"""A fitted source strip must not report its empty canvas as picture area."""
import pytest

import render_qc
from timeline import Timeline


@pytest.mark.parametrize("mode,area", [("pad", .316), ("pad_blur", .316), ("crop", 1.)])
def test_main_picture_area_excludes_padding(mode, area):
    edl = dict(keep=[[0, 10]], frame=dict(ratio="9:16", mode=mode))
    report = render_qc.picture_measures(
        edl, dict(video=dict(width=960, height=540)),
        Timeline(edl["keep"], [], None), 720, 1280, 10.)
    assert report["area"] == pytest.approx(area, abs=.001)
    assert bool(render_qc.advice({"picture": report})) == (mode != "crop")


def test_mixed_crop_and_fit_reports_each_visible_shot_after_trim():
    edl = dict(keep=[[10, 20]], frame=dict(ratio="9:16", mode="crop", focus_track=[
        dict(t0=10, t1=15, x=.5, y=.5, mode="crop"),
        dict(t0=15, t1=20, x=.5, y=.5, mode="pad_blur")]))
    report = render_qc.picture_measures(
        edl, dict(video=dict(width=1920, height=1080)),
        Timeline(edl["keep"], [], None), 720, 1280, 10.)
    assert [(r["t0"], r["t1"], r["area"]) for r in report["windows"]] == [
        (0., 5., 1.), (5., 10., .316)]
    assert "5.0-10.0s" in render_qc.advice({"picture": report})[0]
