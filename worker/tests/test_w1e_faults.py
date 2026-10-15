"""AT-0C-3 for track 1E: every fault mode x every 1E device, ZERO render
failures (a device may degrade, it never fails a render).

One spec carries every 1E device kind at once — section grades (mono with
ramps, a hue key, a split tone with protection, a region grade), a LUT grade
whose asset is gone, and all sixteen finishes including a matte-weighted
thermal — through a real local preview render under each pinned fault. The
render must keep the program's exact length; every device row must be a
valid rung; faults that remove what a device needs must degrade it."""

import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import faultinject  # noqa: E402
import fxgraph  # noqa: E402
import renderer  # noqa: E402
from schemas import default_edl, program_duration, validate_edl  # noqa

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="ffmpeg required")

FAULTS = ("none",) + faultinject.FAULTS
SRC_S = 12.0
KINDS = ("lcd", "crt", "led", "halftone", "xerox", "band_tear", "film",
         "thermal", "xray", "blueprint", "line_art", "solarise", "posterise",
         "kaleidoscope", "pixel_stretch", "night_vision")


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1e_faults")
    src = str(d / "src.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=640x360:r=30:d={SRC_S}", "-f", "lavfi",
                    "-i", f"sine=frequency=330:sample_rate=48000:"
                          f"duration={SRC_S}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", src], check=True, capture_output=True)
    return {"src": src}


def _spec():
    edl = default_edl(SRC_S)
    edl["keep"] = [[0.0, 5.0], [6.0, 11.0]]
    edl["frame"] = {"ratio": "9:16"}
    fx = [
        {"id": "g_mono", "type": "grade_window", "stage": "world",
         "start": 0.0, "end": 2.0, "saturation": 0.0, "ramp_out_f": 3,
         "device": "grade_section"},
        {"id": "g_iso", "type": "grade_window", "stage": "world",
         "start": 2.0, "end": 3.0, "isolate": {"hue": "#c8202a"},
         "device": "grade_isolate_hue"},
        {"id": "g_tone", "type": "grade_window", "stage": "screen",
         "start": 3.0, "end": 4.0, "split_tone": {
             "shadows": "#1f4e79", "highlights": "#e9b44c"},
         "protect": ["skin"], "device": "grade_split_tone"},
        {"id": "g_reg", "type": "grade_window", "stage": "world",
         "start": 4.0, "end": 5.0, "ev": 0.7, "region": {
             "shape": "ellipse", "box": [0.2, 0.2, 0.8, 0.8]}},
        {"id": "g_lut", "type": "grade_window", "stage": "world",
         "start": 5.0, "end": 6.0, "contrast": 1.2, "lut": {
             "asset_key": "media/w1e/gone.cube"},
         "device": "grade_lut_import"},
    ]
    for i, k in enumerate(KINDS):
        s = round(0.5 + 0.55 * i, 2)
        it = {"id": f"f_{k}", "type": "finish", "stage": "pre_captions",
              "start": s, "end": round(s + 0.4, 2), "kind": k,
              "device": f"finish_{k}"}
        if k == "thermal":
            it.update(matte_weighted=True, stage="world")
        if k == "crt":
            it["stage"] = "over_type"
        fx.append(it)
    edl["fx"] = fx
    return edl


def _index():
    return {"words": [], "silences": [], "sentences": [],
            "video": {"duration": SRC_S}}


@pytest.fixture
def no_budget(monkeypatch):
    """This suite is about faults: the preview's device budget (+30 %,
    which a 10 s preview carrying 21 devices exceeds by design) is lifted
    here and pinned in its own test below."""
    monkeypatch.setattr(fxgraph, "_device_budget", lambda plan, edl: None)


@pytest.mark.parametrize("fault", FAULTS)
def test_every_fault_times_every_1e_device_renders(fault, media, tmp_path,
                                                   no_budget):
    norm = validate_edl(_spec(), SRC_S).model_dump()
    want = float(program_duration(norm))
    out = str(tmp_path / "out.mp4")
    rows = []
    names = () if fault == "none" else (fault,)
    tok = fxgraph.collect(rows)
    try:
        with faultinject.inject(*names):
            dur = renderer.render_edl(norm, _index(), media["src"], out,
                                      str(tmp_path), True,
                                      suppress_outro=True)
    finally:
        fxgraph.stop_collecting(tok)
    assert dur == pytest.approx(want, abs=0.07), (fault, dur, want)
    probe = renderer.media.probe(out)
    assert probe.get("width") and probe.get("height"), probe
    report = {r["id"]: r for r in fxgraph.merge_report(rows)}
    assert set(report) == {i["id"] for i in norm["fx"]}
    assert all(r["rung"] in fxgraph.RUNGS for r in report.values())
    # the LUT asset is gone in every cell: graded without it, never failed
    assert report["g_lut"]["rung"] in ("reduced", "omit")
    if fault == "none":
        assert report["f_thermal"]["rung"] == "reduced"   # no matte (1A)
        full = {k for k, r in report.items() if r["rung"] == "full"}
        assert full >= {"g_mono", "g_iso", "g_tone", "g_reg"} | {
            f"f_{k}" for k in KINDS if k != "thermal"}
    if fault == "ffmpeg_filter_missing":
        assert all(r["rung"] == "omit" for r in report.values())
    if fault == "oom":
        # map precompute is OOM-killed: map-using finishes omit, the rest
        # draw
        for k in ("lcd", "crt", "led", "halftone", "blueprint",
                  "kaleidoscope"):
            assert report[f"f_{k}"]["rung"] == "omit", k
        assert report["g_mono"]["rung"] == "full"


def test_a_crowded_preview_spends_its_budget_and_says_so(media, tmp_path):
    """Twenty-one devices in a 10 s preview: the resolver spends the +30 %
    preview budget in program order and omits the rest WITH the reason;
    the render still ships at its exact length."""
    norm = validate_edl(_spec(), SRC_S).model_dump()
    rows = []
    tok = fxgraph.collect(rows)
    try:
        dur = renderer.render_edl(norm, _index(), media["src"],
                                  str(tmp_path / "o.mp4"), str(tmp_path),
                                  True, suppress_outro=True)
    finally:
        fxgraph.stop_collecting(tok)
    assert dur == pytest.approx(float(program_duration(norm)), abs=0.07)
    report = fxgraph.merge_report(rows)
    full = [r for r in report if r["rung"] == "full"]
    over = [r for r in report if "budget" in (r["reason"] or "")]
    assert full and over, report
    assert all(r["rung"] != "full" for r in over)
