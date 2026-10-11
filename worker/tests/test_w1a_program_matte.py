"""Track 1A: the program matte lands on the picture frame it was measured on.

A synthetic subject (a white rectangle moving over a gray plate) with its
matte written straight into a local sidecar store (tests/w1a_support.py) is
rendered through the real renderer with the composite replaced by "show the
matte" (w1a_support.show_matte). Every frame of the program matte must be
the picture's own rectangle: through a static crop, a crop that FOLLOWS,
cuts, a speed ramp, an insert, a payoff hold, pad and picture-rect framings,
a source-fed picture card and a camera push (screen space) — the cases
Wave 0's per-item mask gave up on (motion_layer.behind_why).
"""

import copy
import json
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import w1a_support as sup  # noqa: E402

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """(source path, SourceRef, store root) — 8 s at 25 fps, 640x360, with
    its matte chunks in a local store."""
    import media
    import sidecars
    d = tmp_path_factory.mktemp("w1a_synth")
    src = sup.make_source(str(d / "src.mp4"))
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [])
    store = str(d / "store")
    sup.write_chunks(store, ref, params=None)
    return src, ref, store, info


@pytest.fixture(scope="module")
def synth_cut(tmp_path_factory):
    """The same, with a camera cut at 4.00 s (the rectangle jumps) and the
    index shot there: chunks reset at the cut."""
    import media
    import sidecars
    d = tmp_path_factory.mktemp("w1a_synth_cut")
    src = sup.make_source(str(d / "src.mp4"), cut_at=4.0)
    info = media.probe(src)
    sha = media.sha256_file(src)
    ref = sidecars.source_from(sha, src, info, 0, [4.0])
    store = str(d / "store")
    sup.write_chunks(store, ref, cut_at=4.0)
    return src, ref, store, info


@pytest.fixture
def store(monkeypatch):
    import sidecars
    prev = []

    def use(root):
        prev.append(sidecars.use_store(sidecars.LocalStore(root)))
    yield use
    for p in prev:
        sidecars.use_store(None)


def _index(ref, info, shots=()):
    return {"words": [], "shots": [{"start": s} for s in shots],
            "video": {"width": info["width"], "height": info["height"],
                      "fps": info["fps"], "duration": info["duration"],
                      "sha256": ref.sha256}}


def _render(tmp_path, src, index, edl, name, preview=True, show=False,
            monkeypatch=None, asset_locals=None):
    import renderer
    from schemas import validate_edl
    if show:
        sup.show_matte(monkeypatch)
    out = str(tmp_path / f"{name}.mp4")
    work = str(tmp_path / f"work_{name}")
    os.makedirs(work, exist_ok=True)
    e = validate_edl(edl, float(index["video"]["duration"])).model_dump()
    renderer.render_edl(e, index, src, out, work, preview,
                        asset_locals=asset_locals or {})
    return out


def _behind_text(tl, a, b, tid="tx1"):
    import matte_track
    return {"id": tid, "text": "W", "start": a, "end": b,
            "template": "title", "size_scale": 1.0,
            "behind": matte_track.program_behind(tl, a, b)}


def _compare(picture_path, matte_path, f0, f1, min_iou=0.9, box=None):
    """Per frame inside [f0, f1): IoU of the picture's rectangle (luma
    > 200) and the matte (> 127), inside ``box`` (x, y, w, h) when given.
    Returns the per-frame IoUs."""
    pic = sup.frames(picture_path)
    mat = sup.frames(matte_path)
    if box:
        x, y, w, h = box
        pic = pic[:, y:y + h, x:x + w]
        mat = mat[:, y:y + h, x:x + w]
    n = min(len(pic), len(mat))
    out, seen = [], 0
    for k in range(f0, min(f1, n)):
        a = pic[k] > 175            # the plate-to-white midpoint
        b = mat[k] > 127
        seen += int(a.sum() > 0)
        if a.sum() == 0 and b.sum() == 0:
            out.append(1.0)
            continue
        ys, xs = np.nonzero(a | b)
        H, W = a.shape
        if xs.min() == 0 or ys.min() == 0 or xs.max() == W - 1 \
                or ys.max() == H - 1:
            # a sliver entering or leaving the frame: a pixel of edge is
            # most of its IoU; judged by its edges instead
            ea = np.nonzero(a.any(axis=0))[0]
            eb = np.nonzero(b.any(axis=0))[0]
            ok = (len(ea) and len(eb) and abs(int(ea.min()) - int(eb.min())) <= 2
                  and abs(int(ea.max()) - int(eb.max())) <= 2)
            out.append(1.0 if ok else sup.iou(a, b))
            continue
        out.append(sup.iou(a, b))
    # the subject is on screen in most of the window (the test means it)
    assert seen >= 0.6 * len(out), (seen, len(out))
    return out


def _case(tmp_path, monkeypatch, store, synth, edl, window, name,
          shots=(), asset_locals=None, picture_frame=None, box=None):
    from timeline import Timeline
    src, ref, root, info = synth
    store(root)
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    edl = copy.deepcopy(edl)
    edl["texts"] = [_behind_text(tl, *window)]
    index = _index(ref, info, shots)
    plain = copy.deepcopy(edl)
    plain["texts"] = []
    if picture_frame:
        # judge the matte against the picture of another framing with the
        # same geometry (pad_blur's backdrop is footage, not the subject)
        plain["frame"] = picture_frame
    pic = _render(tmp_path, src, index, plain, name + "_pic",
                  asset_locals=asset_locals)
    mat = _render(tmp_path, src, index, edl, name + "_mat", show=True,
                  monkeypatch=monkeypatch, asset_locals=asset_locals)
    fps = 25.0
    f0, f1 = int(round(window[0] * fps)), int(round(window[1] * fps))
    return _compare(pic, mat, f0, f1, box=box)


def test_static_crop_and_follow_through_cuts(tmp_path, monkeypatch, store,
                                             synth):
    """A 9:16 crop that FOLLOWS the subject across three kept spans: the
    Wave 0 mask demoted here ("the crop follows the speaker")."""
    edl = {"keep": [[0.4, 2.4], [2.8, 4.8], [5.2, 7.6]],
           "frame": {"ratio": "9:16", "mode": "crop",
                     "follow": [{"t0": 0.0, "t1": 8.0,
                                 "k": [[0.0, 0.2, 0.5], [3.0, 0.5, 0.5],
                                       [7.6, 0.8, 0.5]]}]}}
    ious = _case(tmp_path, monkeypatch, store, synth, edl, (0.2, 5.8),
                 "follow")
    assert len(ious) > 100
    assert min(ious) >= 0.98, min(ious)
    assert float(np.mean(ious)) >= 0.995, float(np.mean(ious))


def test_speed_ramp_insert_and_payoff_hold(tmp_path, monkeypatch, store,
                                          synth):
    """A ramp inside the window, a spliced still and a payoff hold: the
    matte is timed like the picture (Wave 0: "speed ramp ... now covers its
    footage"), black over the insert, held over the hold."""
    from PIL import Image
    still = str(tmp_path / "still.png")
    Image.new("RGB", (64, 36), (20, 40, 60)).save(still)
    edl = {"keep": [[0.4, 3.6], [4.0, 7.2]],
           "speed": [{"id": "sp1", "start": 1.0, "end": 2.6,
                      "factor": 2.0}],
           "inserts": [{"id": "in1", "kind": "image", "asset_key": still,
                        "at_output_s": 2.4, "duration_s": 0.8}],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.45}}
    import renderer
    monkeypatch.setattr(renderer, "_fetch_into",
                        lambda workdir, key, tag: key, raising=False)
    ious = _case(tmp_path, monkeypatch, store, synth, edl, (0.2, 5.4),
                 "ramp", asset_locals={still: still})
    assert min(ious) >= 0.98, min(ious)
    assert float(np.mean(ious)) >= 0.995, float(np.mean(ious))


@pytest.mark.parametrize("frame", [
    {"ratio": "9:16", "mode": "pad"},
    {"ratio": "9:16", "mode": "pad_blur"},
    {"ratio": "1:1", "mode": "crop", "focus_x": 0.3},
    {"ratio": "4:5", "mode": "crop", "picture": [0.06, 0.1, 0.94, 0.8]},
], ids=["pad", "pad_blur", "square_focus", "picture_rect"])
def test_every_framing(tmp_path, monkeypatch, store, synth, frame):
    edl = {"keep": [[0.4, 6.4]], "frame": frame}
    ious = _case(tmp_path, monkeypatch, store, synth, edl, (0.4, 5.6),
                 "frame", picture_frame=dict(frame, mode="pad")
                 if frame["mode"] == "pad_blur" else None)
    assert min(ious) >= 0.96, min(ious)
    assert float(np.mean(ious)) >= 0.995, float(np.mean(ious))


def test_mask_switches_on_the_exact_cut_frame(tmp_path, monkeypatch, store,
                                              synth_cut):
    """AT-1A-2's contract on synthetic footage: a camera cut inside a kept
    span (and the chunk grid resetting there): the matte switches on the
    very frame the picture does, no frame of either side leaks."""
    edl = {"keep": [[1.0, 7.0]], "frame": {"ratio": "9:16", "mode": "crop",
                                           "focus_x": 0.5}}
    ious = _case(tmp_path, monkeypatch, store, synth_cut, edl, (0.4, 5.6),
                 "cut", shots=(4.0,))
    # program 3.0 s = source 4.0 s (the cut): frames 75 (0.4 s in) ...
    cut_k = int(round(3.0 * 25)) - int(round(0.4 * 25))
    near = ious[cut_k - 3:cut_k + 3]
    assert min(near) >= 0.98, near
    assert min(ious) >= 0.98, min(ious)


def test_source_fed_picture_card(tmp_path, monkeypatch, store, synth):
    """AT-1A-5's contract on synthetic footage: inside a source-fed picture
    card the matte maps through the card's source rect onto its box (Wave
    0: "a source-fed picture card re-frames its footage")."""
    box = [0.06, 0.27, 0.94, 0.7]
    edl = {"keep": [[0.4, 6.4]],
           "frame": {"ratio": "9:16", "mode": "crop",
                     "picture": [0.0, 0.0, 1.0, 1.0]},
           "effects": {"picture_cards": [{
               "id": "card", "start": 0.0, "end": 6.0, "box": box,
               "fit": "crop", "radius": 0.03, "background": "#363B48",
               "source": [0.05, 0.1, 0.75, 0.9]}]}}
    import renderer
    W, H = renderer.preview_geometry(*renderer.frame_dims(640, 360, "9:16"),
                                     25.0)[:2]
    x, y = int(W * box[0]) + 4, int(H * box[1]) + 4
    w, h = int(W * (box[2] - box[0])) - 8, int(H * (box[3] - box[1])) - 8
    ious = _case(tmp_path, monkeypatch, store, synth, edl, (0.4, 5.6),
                 "card", box=(x, y, w, h))
    assert min(ious) >= 0.98, min(ious)
    assert float(np.mean(ious)) >= 0.995, float(np.mean(ious))


def test_screen_space_matte_under_a_camera_push(tmp_path):
    """A behind-subject graphic with composite.stage 'screen' composites
    after the camera through the SCREEN-space matte (the world matte through
    the picture's own camera chain); a world-stage one before it. Both land
    on the zoomed picture (AT-1A-3's mechanism). 1C's composite slot is
    registered in a child process (VALMERA_FX_PREVIEW=1c)."""
    import subprocess
    import motion_engine
    if not motion_engine.available():
        pytest.skip("no Chromium here for the motion graphic")
    env = dict(os.environ, VALMERA_FX_PREVIEW="1c", SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    r = subprocess.run([sys.executable, os.path.join(HERE,
                                                     "w1a_screen_probe.py"),
                        str(tmp_path)], cwd=os.path.dirname(HERE), env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for stage in ("screen", "world"):
        ious = out[stage]
        assert len(ious) > 100
        assert min(ious) >= 0.97, (stage, min(ious))
        assert float(np.mean(ious)) >= 0.995, (stage, np.mean(ious))


@pytest.mark.parametrize("effects", [
    {"transition": {"style": "zoom_punch", "duration_s": 0.3,
                    "scope": "every_cut"}},
    {"transition": {"style": "whip_left", "duration_s": 0.3,
                    "scope": "every_cut"}},
    {"stylize": [{"id": "sk1", "kind": "shake", "intensity": 0.8,
                  "start": 1.0,
                  "end": 2.6}]},
], ids=["zoom_punch", "whip", "shake"])
def test_post_concat_geometry_rides_the_matte(tmp_path, monkeypatch, store,
                                              synth, effects):
    """The picture's own post-concat geometry (a zoom-punch or whip at
    every cut, a windowed shake) moves the matte with it."""
    edl = {"keep": [[0.4, 2.4], [2.8, 4.8], [5.2, 7.6]],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.4},
           "effects": effects}
    ious = _case(tmp_path, monkeypatch, store, synth, edl, (0.2, 5.6),
                 "post")
    assert min(ious) >= 0.9, min(ious)
    assert float(np.mean(ious)) >= 0.99, float(np.mean(ious))


def test_handle_matte_carries_each_side_past_the_cut(tmp_path):
    """fxgraph.handle_matte (1D's matte_cut reads it): each side's 2h-frame
    handle stream has its own matte — the block's last (first) frames and
    the frames measured past its out-point (before its in-point), which
    the program matte never holds. Child process: VALMERA_FX_PROBE=1."""
    import subprocess
    env = dict(os.environ, VALMERA_FX_PROBE="1", SKIP_DB_INIT="1",
               DATABASE_URL="postgresql://stub/stub")
    r = subprocess.run([sys.executable, os.path.join(HERE,
                                                     "w1a_junction_probe.py"),
                        str(tmp_path)], cwd=os.path.dirname(HERE), env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for side in ("a", "b"):
        assert len(out[side]) == 12
        assert min(out[side]) >= 0.98, (side, out[side])
