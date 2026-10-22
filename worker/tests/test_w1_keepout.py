"""Face keep-out measured on frames (Wave 1 integration; the Oct 11 JRE
#2054 run's decisive defects 1, 2 and 5).

* the chin is measured (YuNet on the exact frame, landmark chin), and a
  graphic over the chin band is a collision;
* the corner mark's reserved box is kept clear by moved graphics;
* a width the editor set is never shrunk by the keep-out.
"""
import os
import subprocess

import pytest

import keepout
import motion_templates
import motion_tools
from localfix import fixture, local_only

FACE = (0.43, 0.22, 0.58, 0.56)        # a talking head's face box (9:16)


def _track(face=FACE, n=10):
    return [(0.1 * i, [face]) for i in range(n)]


def test_a_graphic_on_the_chin_is_a_collision():
    """s04: a grid covered the chin for 3.8 s and the check said clean — the
    chin is a small share of the whole face zone, so FACE_HIT never fired.
    The chin band is judged on its own."""
    x0, _y0, x1, y1 = FACE
    w, h = x1 - x0, y1 - FACE[1]
    on_chin = (x0 - 0.1, y1 - 0.08 * h, x1 + 0.1, y1 + 0.2)
    hit = keepout.assess(on_chin, _track())
    assert hit["hit"] and hit["chin"] and not hit["mouth"]
    under = (x0 - 0.1, y1 + 0.05 * h + 0.02, x1 + 0.1, y1 + 0.25)
    assert not keepout.assess(under, _track())["hit"]   # the chest band
    cz = keepout.chin_zone(FACE)
    assert cz[1] < y1 < cz[3]
    assert keepout.on_face(on_chin, [], (), [cz])
    assert not keepout.on_face(under, [], (), [cz])


def test_a_moved_graphic_keeps_off_the_corner_mark():
    """s06: a 'Rounds 2-3' band lockup at y 0.178 drew from y 0.076, over
    the free-tier corner mark, with no warning."""
    W, H = 1080, 1920
    mark = keepout.mark_zone(W, H)
    assert mark and mark[3] < 0.2 and mark[2] > 0.8      # top-right (v6)
    slam = (0.2278, 0.0781, 0.7759, 0.276)
    assert keepout.on_mark(slam, mark)
    assert not keepout.on_mark((0.2278, 0.15, 0.7759, 0.30), mark)
    spec = motion_templates.spec("word_slam")
    params = motion_templates.check_params(
        "word_slam", {"text": "Rounds *2-3*", "width": 0.56, "y": 0.178})
    cands = keepout.candidates("word_slam", spec, params, slam, [], [], W, H,
                               mark=mark)
    assert cands
    assert all(not keepout.on_mark(b, mark) for _c, _p, b in cands)


def test_an_explicit_width_is_never_stepped_down():
    """s01: set_motion_graphic(width=0.544) came back at 0.435 because the
    ESTIMATED box grazed the mouth; the hook rendered at 6.6% of the frame
    and the short was killed. A width the editor set moves or stays."""
    spec = motion_templates.spec("word_slam")
    mine = {"template": "word_slam", "params": motion_templates.check_params(
        "word_slam", {"text": "He gave / *long odds*", "tier": "hook",
                      "width": 0.544})}
    default = {"template": "word_slam", "params": motion_templates.check_params(
        "word_slam", {"text": "He gave / *long odds*", "tier": "hook"})}
    assert motion_tools._pinned_size(spec, mine) == "width"
    assert motion_tools._pinned_size(spec, default) is None
    box = (0.2, 0.52, 0.8, 0.70)
    zones = keepout.zones_of(_track())
    free = keepout.candidates("word_slam", spec, mine["params"], box, [],
                              zones, 1080, 1920)
    pinned = keepout.candidates("word_slam", spec, mine["params"], box, [],
                                zones, 1080, 1920, pin_size=True)
    assert any("width" in p for _c, p, _b in free)
    assert not any("width" in p for _c, p, _b in pinned)


def _frame(src, t, dest):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", src,
                    "-frames:v", "1", "-q:v", "2", dest], check=True)
    return dest


YUNET = os.environ.get("YUNET_MODEL") or os.path.expanduser(
    "~/Documents/Valmera/.valmera/models/face_detection_yunet_2023mar.onnx")


@local_only(fixture("V04", "rogan_gamers.mp4"),
            fixture("V08", "garry_closeup.mp4"), YUNET)
def test_the_chin_is_measured_on_the_frame(tmp_path, monkeypatch):
    """Measured on library frames: the cascade's tightest box stopped 0.05
    (V04 Elon) and 0.07 (V08 close-up) of the frame above the chin; YuNet
    with the landmark chin reaches it (chin read by hand: V04 ~0.61, V08
    ~0.62 at these moments)."""
    import config
    monkeypatch.setattr(config, "YUNET_MODEL", YUNET, raising=False)
    for src, t, chin in ((fixture("V04", "rowing_joke.mp4"), 10, 0.605),
                         (fixture("V08", "garry_closeup.mp4"), 5, 0.615)):
        if not os.path.exists(src):
            pytest.skip("fixture missing")
        p = _frame(src, t, str(tmp_path / f"f{t}.jpg"))
        faces = keepout.detect_faces(p)
        assert faces, src
        top = max(faces, key=lambda b: (b[2] - b[0]))
        assert top[3] >= chin - 0.01, (src, top)
        # the zone (with its pad) covers the chin
        assert keepout.face_zone(top)[3] >= chin
