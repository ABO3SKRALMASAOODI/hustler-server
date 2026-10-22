"""Track 1A: brand integrity and the fault gate for the program matte.

  * AT-0B-5's pattern (owner rule 4): a free-tier render with words behind
    the subject through the program matte vs the same render without them —
    the 5 s Valmera end card is md5-identical and the watermark robot's
    opaque pixels are untouched, while the words did change the program.
    Lossless encodes in a probe-lane child (tests/w0b_probe.py).
  * AT-0C-3's gate (a device degrades, it never fails a render): every
    pinned fault mode around a render that asks for the program matte, and
    a matte chain ffmpeg rejects: every render succeeds at the program's
    exact length, the words drawn in front where the matte is lost.
"""

import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import w1a_support as sup  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="ffmpeg required")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    import media
    import sidecars
    d = tmp_path_factory.mktemp("w1a_brand")
    src = sup.make_source(str(d / "src.mp4"))
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [])
    store = str(d / "store")
    sup.write_chunks(store, ref)
    idx = {"words": [], "shots": [],
           "video": {"width": info["width"], "height": info["height"],
                     "fps": info["fps"], "duration": info["duration"],
                     "sha256": sha}}
    return {"src": src, "store": store, "idx": idx, "dir": str(d),
            "info": info}


def _edl(with_text):
    import matte_track
    from timeline import Timeline
    keep = [[0.4, 3.4], [3.8, 7.8]]
    edl = {"keep": keep,
           "frame": {"ratio": "9:16", "mode": "crop",
                     "follow": [{"t0": 0.0, "t1": 8.0,
                                 "k": [[0.0, 0.25, 0.5],
                                       [7.8, 0.75, 0.5]]}]}}
    if with_text:
        tl = Timeline(keep)
        edl["texts"] = [{"id": "tx1", "text": "WWWW", "start": 1.0,
                         "end": 5.0, "template": "title",
                         "size_scale": 2.0, "color": "#00FF00",
                         "behind": matte_track.program_behind(tl, 1.0, 5.0)}]
    return edl


def _yuv(path, W, H):
    raw = np.fromfile(path, np.uint8)
    y = raw[:W * H].reshape(H, W).astype(np.int16)
    u = raw[W * H:W * H + (W // 2) * (H // 2)].reshape(H // 2, W // 2)
    return y, u.astype(np.int16)


@pytest.fixture(scope="module")
def brand(synth, tmp_path_factory):
    import w0b_probe
    from schemas import validate_edl
    cases = []
    for k, with_text in ((0, False), (1, True)):
        e = validate_edl(_edl(with_text),
                         float(synth["info"]["duration"])).model_dump()
        cases.append({"name": f"b{k}", "src": synth["src"],
                      "idx": synth["idx"], "edl": e, "preview": True,
                      "want_wm": True, "dump_frames": [60, 100]})
    old = {k: os.environ.get(k) for k in ("OUTRO_ON_PREVIEW",
                                          "VALMERA_SIDECAR_DIR")}
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    os.environ["VALMERA_SIDECAR_DIR"] = synth["store"]
    try:
        return w0b_probe.run(cases, str(tmp_path_factory.mktemp("w1a_b")))
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_end_card_is_md5_identical_with_the_program_matte(brand):
    a, b = brand["b0"], brand["b1"]
    assert a["error"] is None and b["error"] is None, (a["error"],
                                                       b["error"])
    n_prog = int(round(7.0 * 25))
    assert len(a["frames"]) == len(b["frames"]) == n_prog + 125
    assert a["frames"][n_prog:] == b["frames"][n_prog:]   # the 5 s card
    # ...while the words behind the subject changed the program
    changed = [i for i in range(n_prog) if a["frames"][i] != b["frames"][i]]
    assert changed and min(changed) >= 25 - 1 and max(changed) <= 125 + 1
    assert a["audio"] == b["audio"]


def test_watermark_robot_is_untouched_by_the_program_matte(brand):
    import renderer
    from test_w0b_render import _mark_opaque
    W, H = 270, 480
    m = _mark_opaque(W, H)
    cm = m[::2, ::2] & m[1::2, ::2] & m[::2, 1::2] & m[1::2, 1::2]
    for k in ("60", "100"):
        ya, ua = _yuv(brand["b0"]["dumps"][k], W, H)
        yb, ub = _yuv(brand["b1"]["dumps"][k], W, H)
        assert np.abs(ya - yb)[m].max() == 0
        assert np.abs(ua - ub)[cm].max() == 0
        assert renderer.robot_path()


# ── the fault gate ───────────────────────────────────────────────────────

@pytest.fixture
def store(synth):
    import sidecars
    sidecars.use_store(sidecars.LocalStore(synth["store"]))
    yield
    sidecars.use_store(None)


def _render(synth, edl, out, work):
    import renderer
    from schemas import validate_edl
    os.makedirs(work, exist_ok=True)
    e = validate_edl(edl, float(synth["info"]["duration"])).model_dump()
    return renderer.render_edl(e, synth["idx"], synth["src"], out, work,
                               True)


@pytest.mark.parametrize("fault", ("none",) + __import__(
    "faultinject").FAULTS)
def test_every_fault_mode_renders(synth, store, tmp_path, fault):
    import faultinject
    out = str(tmp_path / "f.mp4")
    names = () if fault == "none" else (fault,)
    with faultinject.inject(*names):
        d = _render(synth, _edl(True), out, str(tmp_path / "w"))
    assert abs(d - 7.0) < 0.1, d
    assert len(sup.frames(out)) == 175


def test_a_matte_chain_ffmpeg_rejects_still_renders(synth, store, tmp_path,
                                                    monkeypatch):
    """The render's fx fallback (renderer._fx_run) also drops the program
    matte: the words are drawn in front, the render succeeds."""
    import matte_track
    monkeypatch.setattr(matte_track, "edge_lut",
                        lambda: "no_such_function(val)")
    out = str(tmp_path / "broken.mp4")
    d = _render(synth, _edl(True), out, str(tmp_path / "w"))
    assert abs(d - 7.0) < 0.1
    assert len(sup.frames(out)) == 175


def test_a_lane_without_the_chains_filters_draws_the_words_in_front(
        synth, store, tmp_path):
    import faultinject
    out = str(tmp_path / "nofilter.mp4")
    with faultinject.inject("ffmpeg_filter_missing:interleave"):
        d = _render(synth, _edl(True), out, str(tmp_path / "w"))
    assert abs(d - 7.0) < 0.1


def test_the_words_really_sit_behind_the_subject(brand):
    """The program-matte render drew the words BEHIND the white subject:
    where the subject is, the frame is the subject (unchanged); beside it
    the green words are drawn."""
    W, H = 270, 480
    for k in ("60", "100"):
        ya, _ua = _yuv(brand["b0"]["dumps"][k], W, H)
        yb, _ub = _yuv(brand["b1"]["dumps"][k], W, H)
        subject = ya > 200
        core = subject.copy()
        for _ in range(3):           # away from the composite's soft edge
            core = (core & np.roll(core, 1, 0) & np.roll(core, -1, 0)
                    & np.roll(core, 1, 1) & np.roll(core, -1, 1))
        drawn = np.abs(ya - yb) > 30
        assert core.sum() > 500
        assert drawn[core].mean() < 0.01, drawn[core].mean()
        assert drawn[~subject].sum() > 500


def test_a_render_with_an_unused_program_matte_fails_as_before(monkeypatch):
    """Every render of an indexed source holds a ProgramMatte; one nothing
    asked for emits nothing, so a failure of that graph is the legacy
    graph's own: raised at once, not re-rendered 'without fx' (review)."""
    import fxgraph
    import media
    import renderer
    calls = []

    def boom(cmd, **kw):
        calls.append(cmd)
        raise media.MediaError("ffmpeg failed: Invalid data found")
    monkeypatch.setattr(renderer, "_render_media_run", boom)
    plan = fxgraph.prepare({"keep": [[0.0, 2.0]]}, tl=None, W=270, H=480,
                           fps=25.0, preview=True, workdir=None)
    assert plan.empty

    class _Unused:
        active, wants = False, []
    plan.pm = _Unused()
    with pytest.raises(media.MediaError):
        renderer._fx_run(["ffmpeg"], [plan], lambda: ["ffmpeg", "again"],
                         None)
    assert len(calls) == 1

    class _Used:
        active, wants = True, [(0.0, 1.0, "tx1", "person", "world")]
    calls.clear()
    plan.pm = _Used()
    with pytest.raises(media.MediaError):
        renderer._fx_run(["ffmpeg"], [plan], lambda: ["ffmpeg", "again"],
                         None)
    assert calls == [["ffmpeg"], ["ffmpeg", "again"]]


def test_the_matte_chain_filters_exist_on_this_ffmpeg():
    """Every filter the program matte's chain emits (matte_track.FILTERS,
    the list the render checks before building it) is in this ffmpeg — on
    CI the production 8.1 build, so a lane that would silently draw every
    behind item in front fails here first."""
    import fxgraph
    import matte_track
    assert fxgraph.missing_filters(matte_track.FILTERS) == []
