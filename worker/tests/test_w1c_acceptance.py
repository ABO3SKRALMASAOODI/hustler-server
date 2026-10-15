"""Track 1C acceptance on the footage library (local only; ROADMAP Wave 1
§1C). Every case is rendered from its refbench spec
(benchmarks/refbench/cases/1C/AT-1C-*.yaml: apply_edit_batch operations) by
tests/w1c_at.py in a lossless child and measured there; the bars below are
the roadmap's, or the physical limit of a 4:2:0 delivery where the roadmap
states a blend-domain identity (the blend-domain identity itself is pinned
in CI by tests/test_w1c_compositor.py).

  AT-1C-1  footage in the letters: PSNR >= 40 dB against a direct crop of
           the fill; outside the glyphs <= 2/255; the I window opens onto
           the program exactly, eased (slow in, slow out) and monotone
  AT-1C-2  difference word over the bookshelf plate: |Lc| >= 45 over >= 85%
           of the glyph on every sampled frame and >= 90% on most; luma of
           |plate - ink| within the delivery's colour-matrix round trip
  AT-1C-3  ghost: stroke = hidden glyph ∧ matte within 1 px (precision and
           recall >= 0.98), 2-3 px wide, 35-60% opacity
  AT-1C-4  glass: region_locality 0 outside the panel and outside the
           letters (<= 1 for the type's own antialiased fringe);
           deterministic across two renders
  AT-1C-5  cross-cut reveal: the next shot inside the letters on its own
           clock (PSNR >= 40 dB), the window grows to the whole frame by the
           cut, no jump at the cut; duration and audio exact
  AT-1C-6  solarise inside the keyframed quad matches the transform (mean
           error <= 6), outside ΔE2000 <= 1
  AT-1C-7  screen / add light: no pixel darker than the picture (<= 2
           levels of 4:2:0 rounding, none darker by more)
  perf     four composites + a region effect on a 20 s 1080x1920 final:
           CPU-time ratio against the plain graphics (bar 1.3)
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import w1c_at                                                  # noqa: E402
from localfix import fixture, local_only                       # noqa: E402

FOOTAGE = (fixture("V01", "thiel_ai_bigger.mp4"),
           fixture("V13", "starship_launch_4k60.mp4"),
           fixture("V10", "twoshot.mp4"), fixture("V05", "welt_cut.mp4"),
           fixture("V13", "dragon_unveil.mp4"))
RVM = os.environ.get("MATTE_RVM_MODEL") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/models/rvm_mobilenetv3_fp32.onnx")

pytestmark = local_only(*FOOTAGE)


@pytest.fixture(scope="module")
def at(tmp_path_factory):
    old = {k: os.environ.get(k) for k in ("MATTE_RVM_MODEL", "W1C_AT_DIR")}
    os.environ.setdefault("MATTE_RVM_MODEL", RVM)
    d = tmp_path_factory.mktemp("w1c_at")
    os.environ.setdefault("W1C_AT_DIR", str(d / "cases"))
    try:
        return w1c_at.run_child(str(d / "results.json"))
    finally:
        # never leak the pinned model into later tests of the run
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _ok(at, cid):
    r = at[cid]
    assert "error" not in r, r.get("trace", r.get("error"))
    return r


def test_at_1c_1_footage_inside_the_letters_then_the_window(at):
    r = _ok(at, "AT-1C-1")
    assert min(r["inside_psnr_db"]) >= 40.0, r["inside_psnr_db"]
    assert max(r["outside_max"]) <= 2
    for w in r["window"]:
        assert w["inside_mean_vs_program"] <= 1.0 and w["outside_max"] <= 2
    e = r["eased"]
    assert e["monotone"] and e["p15"] <= 0.1 and e["p85"] >= 0.8, e


def test_at_1c_2_difference_word_reads_over_the_busy_plate(at):
    r = _ok(at, "AT-1C-2")
    shares = [f["lc45_share"] for f in r["frames"]]
    assert min(shares) >= 0.85 and sorted(shares)[len(shares) // 2] >= 0.90
    assert max(f["luma_mae"] for f in r["frames"]) <= 6.0


@pytest.mark.skipif(not os.path.exists(RVM), reason="needs the RVM model")
def test_at_1c_3_ghost_outlines_where_the_subject_hides_letters(at):
    r = _ok(at, "AT-1C-3")
    assert r["ghosted_items"] == [0]
    for f in r["frames"]:
        assert f["precision_1px"] >= 0.98 and f["recall_1px"] >= 0.98, f
        assert 2.0 <= f["width_px"] <= 3.0, f
        assert 0.35 <= f["opacity"] <= 0.60, f


def test_at_1c_4_glass_stays_inside_its_regions(at):
    r = _ok(at, "AT-1C-4")
    assert max(r["outside_panel_max"]) == 0
    assert max(r["outside_glyph_max"]) <= 1
    assert min(r["inside_panel_mean"]) > 2.0          # the glass is there
    assert r["determinism_frames_differing"] == 0


def test_at_1c_5_the_next_shot_through_the_letters(at):
    r = _ok(at, "AT-1C-5")
    assert r["frames"][0] == r["frames"][1] and r["audio_identical"]
    assert min(r["inside_psnr_db"]) >= 40.0, r["inside_psnr_db"]
    first, last = r["window_area_first_last"]
    assert first < 0.4 and last > 0.995 and r["window_monotone"]
    c = r["cut_continuity"]
    assert c["jump_at_cut"] <= max(2.0, 1.5 * c["next_step"]), c


def test_at_1c_6_solarise_inside_the_quad_only(at):
    r = _ok(at, "AT-1C-6")
    assert max(r["inside_mae"]) <= 6.0, r["inside_mae"]
    assert max(r["outside_de2000_max"]) <= 1.0, r["outside_de2000_max"]


def test_at_1c_7_light_never_darkens(at):
    r = _ok(at, "AT-1C-7")
    for name, it in r["items"].items():
        assert it["max_darker"] <= 2 and it["px_darker_gt2"] == 0, (name, it)
        assert it["max_brighter"] > 100, name
    # normal paint of the same leak and burn does darken: screen is the fix
    assert r["items"]["leak"]["normal_paint_max_darker"] > 10


@pytest.mark.skipif(os.environ.get("W1C_PERF") != "1",
                    reason="perf runs on request (W1C_PERF=1): wall time on "
                           "a shared machine is not a CI number")
def test_at_1c_perf(tmp_path):
    os.environ.setdefault("W1C_AT_DIR", str(tmp_path / "cases"))
    r = w1c_at.run_child(str(tmp_path / "perf.json"), ("AT-1C-perf",),
                         crf="18", timeout=5400)["AT-1C-perf"]
    assert "error" not in r, r
    assert r["ratio_cpu"] <= 1.3, r
