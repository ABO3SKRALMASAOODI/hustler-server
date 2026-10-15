"""Wave 1 integration: the seams where the six tracks meet.

Owned by the Wave 1 integrator (claude/w1-engine). Each test pins one
cross-track defect found when 1A..1F were merged, measured on rendered
frames where it can be.
"""
import os
import shutil
import subprocess

import pytest

import fxprims

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _frame_md5s(graph, fps, n, concat):
    src = f"testsrc2=s=160x90:r={fps},trim=end_frame={n},format=yuv420p"
    if concat:
        # two blocks joined like the program after its concat (AVTB stamps)
        h = n // 2
        src += (f",split[c0][c1];[c0]trim=end_frame={h},setpts=PTS-STARTPTS"
                f"[d0];[c1]trim=start_frame={h},setpts=PTS-STARTPTS[d1];"
                "[d0][d1]concat=n=2:v=1:a=0")
    out = subprocess.run([FFMPEG, "-v", "error", "-filter_complex",
                          f"{src}[v];{graph}", "-map", "[o]", "-f",
                          "framemd5", "-"], capture_output=True, text=True,
                         timeout=120, check=True).stdout
    return [ln.split(",")[-1].strip() for ln in out.splitlines()
            if ln and not ln.startswith("#")]


@needs_ffmpeg
@pytest.mark.parametrize("fps", ["25", "30", "29.970", "23.976", "59.940",
                                 "50"])
@pytest.mark.parametrize("concat", [False, True])
def test_a_window_branch_lands_on_its_own_frame(fps, concat):
    """fxprims.repad re-stamps a window branch on the program grid. The Wave
    0 stamp truncated (N/fps/TB), so on a 1/fps link a null window showed
    the previous frame at 57-58, 113-116 (25 and 50 fps) and 245-246 (30
    fps): every track had worked around it on its own (1A, 1B, 1D, 1E).
    One rounded, microsecond, 2 us-early stamp is exact everywhere."""
    n = 300
    ref = _frame_md5s("[v]null[o]", fps, n, concat)
    parts = []
    fxprims.window(parts, "v", "o", [(3, n - 2)], "null", uid="u",
                   timeline=False, fps=float(fps))
    got = _frame_md5s(";".join(parts), fps, n, concat)
    assert len(got) == len(ref) == n
    bad = [k for k in range(n) if got[k] != ref[k]]
    assert bad == []


def test_track_repads_are_the_shared_primitive():
    import wipe_masks
    for fps in (25, 29.97, 59.94):
        for f0 in (0, 7):
            for alpha in (False, True):
                assert wipe_masks.repad(f0, fps, alpha) == \
                    fxprims.repad(f0, fps, alpha)
    assert "settb" in fxprims.PRIMITIVE_FILTERS


class _FakePlan:
    pass


class _FakeCtx:
    def __init__(self, plan, parts):
        self.plan, self.parts, self._n = plan, parts, 0
        self.H = 1920

    def input_label(self, key, stream="v"):
        return "7:v"

    def uid(self, prefix="fx"):
        self._n += 1
        return f"{prefix}{self._n}"


def test_static_and_card_regions_are_handed_out_on_every_graph_build():
    """The renderer builds the graph twice (the dialogue-gain pass, then the
    real one) from one plan; regions.pad kept its one-pad-per-input record
    on the plan, so the real build drew no static or card region (1B, 1C
    and 1E each worked around it). The record is per build now."""
    import regions
    plan = _FakePlan()
    plan.n_frames = 100
    item = {"id": "rg1", "start": 1.0, "end": 2.0}
    rect = {"shape": "rect", "box": [0.1, 0.1, 0.5, 0.5]}
    card = {"shape": "card"}
    first = _FakeCtx(plan, [])
    first.fps3 = 25.0
    assert regions.pad(first, item, rect) is not None
    assert regions.pad(first, item, rect) is None      # one pad per build
    assert regions.card_pad(first, item, card, "region") is not None
    second = _FakeCtx(plan, [])
    second.fps3 = 25.0
    assert regions.pad(second, item, rect) is not None
    assert regions.card_pad(second, item, card, "region") is not None


def test_a_failed_graph_retries_with_composites_drawn_plain():
    """1C review: a render whose only devices are motion composites never
    retried when its graph failed (the fx plan was empty), so a composite
    could fail a whole render. The retry plan now draws them plain and
    reports each one."""
    import fxgraph
    import motion_layer
    from types import SimpleNamespace
    edl = {"motion": [{"id": "m1", "start": 1.0, "end": 2.0,
                       "composite": {"blend": "difference"}},
                      {"id": "m2", "start": 1.0, "end": 2.0}]}
    plan = fxgraph.prepare(edl, tl=SimpleNamespace(out_duration=5.0),
                           W=1080, H=1920, fps=25.0, preview=True,
                           workdir=None, media={"src_path": "/x.mp4"})
    assert plan.empty and plan.has_composites and not plan.no_composites
    assert plan.edl is edl and plan.media["src_path"] == "/x.mp4"
    fresh = fxgraph.without_fx(plan, RuntimeError("boom"))
    assert fresh is not plan and fresh.no_composites
    assert [r["id"] for r in fresh.notes] == ["m1"]
    clip = SimpleNamespace(x=10, y=20)
    plain, comp = [], []
    motion_layer.overlay_clip(plain, "v", 3, dict(edl["motion"][1]), clip,
                              25.0, "t")
    motion_layer.overlay_clip(comp, "v", 3, dict(edl["motion"][0]), clip,
                              25.0, "t", plan=fresh)
    assert comp == plain
    # nothing to strip: the plan passes through untouched, as before
    bare = fxgraph.prepare({"motion": [edl["motion"][1]]},
                           tl=SimpleNamespace(out_duration=5.0), W=1080,
                           H=1920, fps=25.0, preview=True, workdir=None)
    assert fxgraph.without_fx(bare, RuntimeError("x")) is bare


def test_a_cut_that_drops_a_graphic_takes_its_letter_devices_with_it():
    """1C review: cutting away a motion graphic left a glyph-region effect
    naming it and validation refused the whole cut. The built-in keep-change
    re-solver drops fx items anchored to letters that are gone."""
    import hooks
    old = {"keep": [[0.0, 10.0]], "motion": [{"id": "tw1"}, {"id": "m2"}],
           "fx": [{"id": "rg", "type": "region", "region": {
                   "shape": "glyph", "ref": "tw1"}},
                  {"id": "jn", "type": "junction", "at": 5.0,
                   "origin": {"kind": "glyph", "ref": "tw1"}},
                  {"id": "ok", "type": "region", "region": {
                   "shape": "glyph", "ref": "m2"}},
                  {"id": "ac", "type": "accent"}]}
    new = {"keep": [[0.0, 10.0]], "motion": [{"id": "m2"}],
           "fx": [dict(i) for i in old["fx"]]}
    edl, notes = hooks.on_keep_change(old, new)
    ids = [i["id"] for i in edl["fx"]]
    assert "rg" not in ids and "ok" in ids and "ac" in ids
    assert any("rg" in n for n in notes)


def test_a_render_drawn_while_the_matte_measured_is_not_served_once_ready(
        monkeypatch):
    """1F review (high): a device drawn as its substitute while the person
    matte was measuring stayed a substitute — the cache served the same
    version again and stitched previews copied its windows. A render now
    stores the matte status it drew with and is current only while the
    matte has not improved."""
    import renderer
    now = {"v": "partial"}
    monkeypatch.setattr(renderer, "matte_status_now",
                        lambda edl, index: now["v"])
    reads = []

    def index_of():
        reads.append(1)
        return {}
    assert renderer.analysis_current({}, {}, index_of)          # old render
    assert renderer.analysis_current({"matte_status": "ready"}, {},
                                     index_of)
    assert reads == []                     # nothing to check: no index read
    assert renderer.analysis_current({"matte_status": "partial"}, {},
                                     index_of)
    now["v"] = "ready"
    assert not renderer.analysis_current({"matte_status": "partial"}, {},
                                         index_of)
    assert not renderer.analysis_current({"matte_status": "absent"}, {},
                                         index_of)
    now["v"] = "partial"
    assert not renderer.analysis_current({"matte_status": "absent"}, {},
                                         index_of)
    now["v"] = "absent"
    assert renderer.analysis_current({"matte_status": "absent"}, {},
                                     index_of)


def test_stitching_refuses_letters_filled_from_another_moment():
    """1C review: a stitched piece clipped to the graphic's window holds no
    frames of the shot after the cut, so a `next` fill showed plain letters
    in the preview while the final was right. Such edits render in full;
    a hit's decay is inside the piece."""
    import stitch
    comp = {"id": "m1", "start": 2.0, "end": 4.0,
            "composite": {"fill": {"kind": "next"}}}
    assert stitch._footage_composite({"motion": [comp]})
    asset = dict(comp, composite={"fill": {"kind": "asset",
                                            "asset_key": "media/p/a.mp4"}})
    assert not stitch._footage_composite({"motion": [asset]})
    a, b = stitch._fx_window({"type": "accent", "start": 3.0, "end": 3.04,
                              "decay_f": 12})
    assert a == 3.0 and b >= 3.04 + 12 / 24.0


needs_ffprobe = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="needs ffmpeg and ffprobe")


def _chroma_spread(yuv_path, W, H, box):
    """Mean |U-128| + |V-128| inside a fractional box of a yuv420p dump."""
    import numpy as np
    a = np.fromfile(yuv_path, np.uint8)
    cw, ch = W // 2, H // 2
    u = a[W * H:W * H + cw * ch].reshape(ch, cw).astype(int)
    v = a[W * H + cw * ch:].reshape(ch, cw).astype(int)
    x0, y0, x1, y1 = (int(box[0] * cw), int(box[1] * ch),
                      int(box[2] * cw), int(box[3] * ch))
    return float(abs(u[y0:y1, x0:x1] - 128).mean()
                 + abs(v[y0:y1, x0:x1] - 128).mean())


@needs_ffprobe
def test_a_full_frame_cutaway_takes_the_looks_grade_and_sections(tmp_path):
    """Oct 11 run, s05: under a black-and-white grade with grain and a
    vignette, both stock cutaways rendered in full colour (the grade and
    the stylize chain run on the main footage before overlays are laid).
    A full-frame cutaway now takes the Look's grade, its grain and
    vignette, and the B&W section of a grade window over the overlap; a
    small picture-in-picture keeps its own colour."""
    sys_path = os.path.dirname(os.path.abspath(__file__))
    import sys
    sys.path.insert(0, sys_path)
    import w0b_probe
    src = w0b_probe.make_source(str(tmp_path / "src.mp4"), dur=8.0)
    red = str(tmp_path / "red.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:r=25:d=4", "-c:v", "libx264",
                    "-preset", "ultrafast", "-crf", "12", "-pix_fmt",
                    "yuv420p", red], check=True)
    idx = {"words": [], "silences": [], "sentences": [],
           "video": {"duration": 8.0}}
    key = "media/w1/broll.mp4"

    def edl(grade=None, fx=None, pip=False):
        e = {"keep": [[0.0, 6.0]], "captions": {"enabled": False},
             "frame": {"ratio": "16:9"},
             "overlays": [{"id": "ov1", "kind": "video", "asset_key": key,
                           "start": 1.0, "duration_s": 2.0,
                           **({"scale": 0.3, "x": 0.25, "y": 0.25}
                              if pip else {"fit": "cover"})}],
             "effects": {}}
        if grade:
            e["effects"]["grade"] = grade
        if fx:
            e["fx"] = fx
        return e
    sec = [{"id": "gw1", "type": "grade_window", "stage": "world",
            "start": 0.0, "end": 2.0, "saturation": 0.0}]
    cases = [
        {"name": "plain", "src": src, "idx": idx, "edl": edl(),
         "asset_locals": {key: red}, "dump_frames": [40]},
        {"name": "bw", "src": src, "idx": idx, "edl": edl("bw"),
         "asset_locals": {key: red}, "dump_frames": [40]},
        {"name": "section", "src": src, "idx": idx, "edl": edl(fx=sec),
         "asset_locals": {key: red}, "dump_frames": [30, 60]},
        {"name": "pip", "src": src, "idx": idx, "edl": edl("bw", pip=True),
         "asset_locals": {key: red}, "dump_frames": [40]},
    ]
    env_preview = os.environ.get("VALMERA_FX_PREVIEW")
    os.environ["VALMERA_FX_PREVIEW"] = "1"
    try:
        res = w0b_probe.run(cases, str(tmp_path / "work"))
    finally:
        if env_preview is None:
            os.environ.pop("VALMERA_FX_PREVIEW", None)
        else:
            os.environ["VALMERA_FX_PREVIEW"] = env_preview
    for name in ("plain", "bw", "section", "pip"):
        assert res[name]["error"] is None, res[name]["error"]
    W, H = res["plain"]["probe"]["width"], res["plain"]["probe"]["height"]
    whole = (0.05, 0.05, 0.95, 0.95)
    plain = _chroma_spread(res["plain"]["dumps"]["40"], W, H, whole)
    assert plain > 10                          # the cutaway is in colour
    assert _chroma_spread(res["bw"]["dumps"]["40"], W, H, whole) < 1.5
    # the B&W section ends at 2.0 s: mono inside it, colour after it
    assert _chroma_spread(res["section"]["dumps"]["30"], W, H, whole) < 1.5
    assert _chroma_spread(res["section"]["dumps"]["60"], W, H, whole) > 10
    # a picture-in-picture is not footage the Look replaces: its colour
    # stays while the main picture around it is graded
    assert _chroma_spread(res["pip"]["dumps"]["40"], W, H,
                          (0.17, 0.2, 0.32, 0.3)) > 10
