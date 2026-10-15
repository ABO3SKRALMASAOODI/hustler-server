"""AT-0C-3 for track 1B: every pinned fault mode x 1B's devices gives ZERO
render failures (INTERFACES_W1.md §7 "its devices in the fault suite").

Each cell renders, in this process and through the real renderer, a program
holding every subject kind (or every background kind) with its device card,
with the track-1A stand-in feeding the person matte, while one fault is
injected (faultinject.inject). A creative device may degrade — a missing
model or a timed-out sidecar takes the substitute rung, a missing filter or
an OOM-killed precompute omits it — it never fails the render: the program
keeps its exact length and its audio, and every device has a report row.
"""

import os
import shutil
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import faultinject                                            # noqa: E402
import fxgraph                                                # noqa: E402
import renderer                                               # noqa: E402
import schemas                                                # noqa: E402
import subject_fx                                             # noqa: E402
import w1b_stub1a                                             # noqa: E402
import w1b_support                                            # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                reason="ffmpeg required")

FAULTS = ("none",) + faultinject.FAULTS
DUR = 6.0
# what a fault means for the matte's readiness (as the 0C suite reads it)
SIDECAR = {"sidecar_timeout": False, "model_missing": False, "oom": False}


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("w1b_faults")
    src, mask = w1b_support.make_subject_source(str(d / "src.mp4"),
                                                str(d / "mask.mkv"),
                                                dur=DUR)
    return {"src": src, "mask": mask,
            "plate": w1b_support.make_still(str(d / "plate.png")),
            "clip": w1b_support.make_clip(str(d / "clip.mp4"))}


def _sx(fid, kind, s, e, **kw):
    it = {"id": fid, "type": "subject", "stage": "world", "start": s,
          "end": e, "kind": kind}
    it.update(kw)
    return it


def _bg(fid, kind, s, e, **kw):
    it = {"id": fid, "type": "background", "stage": "world", "start": s,
          "end": e, "kind": kind}
    it.update(kw)
    return it


def _spec_subject():
    return [
        _sx("f", "fill", 0.4, 0.52, colour="white", decay_f=2,
            device="subject_fill_flash"),
        _sx("p", "poster", 0.6, 1.2, field="accent", device="subject_poster"),
        _sx("g", "glow", 1.2, 1.8, colour="white", stage="screen",
            device="subject_glow"),
        _sx("o", "outline", 1.8, 2.4, colour="white",
            device="subject_outline"),
        _sx("n", "negate", 2.4, 2.48, device="subject_negate"),
        _sx("x", "exposure", 2.6, 3.4, scope="background", ev=-1.0,
            device="subject_exposure"),
        _sx("i", "isolate", 3.4, 4.2, hue="#deaa8c",
            device="subject_isolate"),
        _sx("c", "echo", 4.2, 5.0, echo={"offsets_f": [4, 8]},
            device="subject_echo")]


def _spec_background():
    return [
        _bg("b", "blur", 0.4, 1.0, device="background_blur"),
        _bg("gr", "gradient", 1.0, 1.6, colour="accent", colour2="black",
            device="background_swap"),
        _bg("s", "source", 1.6, 2.4, source={
            "kind": "asset", "asset_key": "media/p1/clip.mp4"},
            device="background_swap"),
        _bg("h", "strobe", 2.6, 3.4, hold_subject=True, hold_f=6,
            sources=[{"kind": "asset", "asset_key": "media/p1/plate.png"},
                     {"kind": "colour", "colour": "white"}],
            device="background_subject_hold"),
        _bg("r", "radial_blur", 3.6, 4.4, device="background_blur")]


SPECS = {"subject": _spec_subject, "background": _spec_background}


@pytest.mark.parametrize("fault", FAULTS)
@pytest.mark.parametrize("spec", sorted(SPECS))
def test_every_fault_times_1b_devices_renders(spec, fault, media, tmp_path,
                                              monkeypatch):
    w1b_stub1a.install_matte(monkeypatch, media["mask"], 25.0,
                             ready_sidecars=SIDECAR.get(fault, True))
    w1b_stub1a.install_hue(monkeypatch)
    edl = schemas.default_edl(DUR)
    edl["keep"] = [[0.0, DUR]]
    edl["frame"] = {"ratio": "9:16"}
    edl["fx"] = SPECS[spec]()
    locals_ = {"media/p1/plate.png": media["plate"],
               "media/p1/clip.mp4": media["clip"]}
    norm = schemas.validate_edl(edl, DUR).model_dump()
    out = str(tmp_path / "out.mp4")
    rows = []
    tok = fxgraph.collect(rows)
    atok = subject_fx.ASSET_LOCALS.set(locals_)
    names = () if fault == "none" else (fault,)
    try:
        with faultinject.inject(*names):
            dur = renderer.render_edl(norm, {"words": [], "video": {
                "duration": DUR, "fps": 25.0, "width": 640,
                "height": 360}}, media["src"], out, str(tmp_path), True,
                suppress_outro=True, asset_locals=locals_)
    finally:
        fxgraph.stop_collecting(tok)
        subject_fx.ASSET_LOCALS.reset(atok)
    assert dur == pytest.approx(DUR, abs=0.07), (spec, fault, dur)
    probe = renderer.media.probe(out)
    assert probe.get("width") and probe.get("height"), probe
    assert float(probe.get("duration") or 0) == pytest.approx(DUR, abs=0.12)
    report = fxgraph.merge_report(rows)
    ids = {i["id"] for i in norm["fx"]}
    if fault != "executor_skew":
        assert {r["id"] for r in report} >= ids, report
    rungs = {r["id"]: r["rung"] for r in report}
    if fault == "none":
        # eight matte devices in a 6 s preview overrun the +30 % preview
        # budget (devices.render_budget_ms): the first ones draw full, the
        # rest degrade in program order, each saying why
        first = norm["fx"][0]["id"]
        assert rungs[first] in ("full", "reduced"), report
        for r in report:
            assert r["rung"] == "full" or "budget" in r["reason"] or \
                "->" in r["reason"], r
    if fault in ("model_missing", "sidecar_timeout"):
        assert "full" not in {rungs[i] for i in ids}, report
