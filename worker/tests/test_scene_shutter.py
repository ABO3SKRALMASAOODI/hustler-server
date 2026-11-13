"""The shutter and the look, drawn (worker/scene/runtime.py, glsl/pic.frag.glsl, glsl/picture.glsl).
Needs a GL context, like test_scene_engine.py; test_scene_motion.py holds everything that does not."""
import os
import shutil

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("moderngl")
pytest.importorskip("uharfbuzz")

import scene.compile as SC          # noqa: E402
import scene.reader as SR_          # noqa: E402
import scene.run as RUN             # noqa: E402
from scene import looks, motion     # noqa: E402
from scene.faults import SceneFault  # noqa: E402

if not shutil.which(SR_.FF):
    pytest.skip("no ffmpeg on this machine", allow_module_level=True)
try:
    from scene.runtime import make_context
    make_context().release()
except SceneFault as _e:
    pytest.skip(f"no GL context here: {_e}", allow_module_level=True)

W, H = 540, 960
SKIN = (224, 172, 150)


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    d = tmp_path_factory.mktemp("shutter")
    # the top half changes only across (bars standing up), the bottom half only down (bars lying): a pan across
    # must wipe the first and leave the second
    rng = np.random.RandomState(7)
    img = np.zeros((1200, 1800, 3), np.uint8)
    img[:600] = np.repeat((rng.rand(1, 90, 3) * 255).astype(np.uint8), 20, axis=1)
    img[600:] = np.repeat((rng.rand(30, 1, 3) * 255).astype(np.uint8), 20, axis=0)
    Image.fromarray(img).save(d / "p.png")
    chart = np.zeros((960, 540, 3), np.uint8)
    cols = [SKIN, (198, 140, 110), (141, 85, 60), (40, 90, 200), (30, 160, 60), (120, 120, 120), (200, 200, 40), (20, 30, 40)]
    for i, c in enumerate(cols):
        chart[i * 120:(i + 1) * 120] = c
    Image.fromarray(chart).save(d / "chart.png")
    card = np.zeros((200, 200, 3), np.uint8) + 255
    Image.fromarray(card).save(d / "white.png")
    return d


def _l1(film, items, dur=12):
    return {"l1": 1, "sid": "t", "seed": 1, "fps": 30, "frame": [W, H], "dur_f": dur, "voices": {}, "film": dict({"dither": 0.0}, **film),
            "assets": {"p": {"kind": "image", "path": "p.png"}, "chart": {"kind": "image", "path": "chart.png"}, "white": {"kind": "image", "path": "white.png"}},
            "items": items}


def _stills(l1, work, frames):
    e = RUN.Engine(SC.compile(l1, str(work), segment=False))
    try:
        return {f: a[..., :3].astype(np.float32) for f, a in e.stills(frames).items()}, dict(e.rt.n)
    finally:
        e.close()


def _energy(a):
    g = a.mean(-1)
    return float((np.diff(g, axis=1) ** 2).mean()), float((np.diff(g, axis=0) ** 2).mean())


def _pan(n=12):
    cam = motion.Cam(1800, 1200, W / H, out_w=W)
    return motion.track(cam, n, cam.rect(0.35, 0.5, 0.25), 30, whip_out=dict(frames=5, travel=0.5, ang=0.0))


def test_a_fast_pan_is_blurred_along_its_travel_and_only_there(work):
    crops = _pan()
    sharp, _ = _stills(_l1({}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops}]), work, [2, 10])
    blur, n = _stills(_l1({"shutter": 0.6}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops}]), work, [2, 10])
    assert np.array_equal(sharp[2], blur[2])                       # nothing moves on frame 2: the same frame, bit for bit
    top, low = slice(H // 4, H // 2 - 20), slice(H // 2 + 20, 3 * H // 4)
    ex0, ex1 = _energy(sharp[10][top])[0], _energy(blur[10][top])[0]
    ey0, ey1 = _energy(sharp[10][low])[1], _energy(blur[10][low])[1]
    assert ex1 < 0.2 * ex0                                          # the detail across the travel is smeared
    assert ey1 > 0.75 * ey0                                         # the detail along it stays (the samples are bilinear, so a little softer)
    assert n.get("shutter", 0) == 1


def test_a_longer_shutter_blurs_more(work):
    crops = _pan()
    e = []
    for sh in (0.3, 0.6, 1.0):
        st, _ = _stills(_l1({"shutter": sh}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops}]), work, [10])
        e.append(_energy(st[10][H // 4:H // 2 - 20])[0])
    assert e[0] > e[1] > e[2]


def test_an_items_own_shutter_wins_over_the_films(work):
    crops = _pan()
    off, n = _stills(_l1({"shutter": 0.6}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops, "shutter": 0}]), work, [10])
    plain, _ = _stills(_l1({}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops}]), work, [10])
    assert np.array_equal(off[10], plain[10]) and not n.get("shutter")


def test_a_card_in_flight_smears_and_so_does_its_edge(work):
    xs = [0.2 + 0.05 * i for i in range(12)]
    card = {"id": "c", "k": "card", "f": [0, 12], "pic": "white", "w": 0.2, "h": 0.1, "x": xs, "y": 0.5}
    base = {"id": "s", "k": "shot", "f": [0, 12], "pic": "plate", "plate": {"c0": "#000000", "c1": "#000000"}}
    sharp, _ = _stills(_l1({}, [base, card]), work, [6])
    blur, _ = _stills(_l1({"shutter": 1.0}, [base, card]), work, [6])
    row_s, row_b = sharp[6][H // 2, :, 0], blur[6][H // 2, :, 0]
    soft_s = int(((row_s > 12) & (row_s < 243)).sum())
    soft_b = int(((row_b > 12) & (row_b < 243)).sum())
    assert soft_s <= 4 and soft_b >= 30                             # the edge is a ramp as wide as the card travelled (27 px a frame)
    assert abs(float(row_b.sum()) - float(row_s.sum())) < 0.06 * float(row_s.sum())      # and the light is the same light


def test_the_drawn_look_is_the_numpy_twin(work):
    lk = dict(looks.LOOKS["story_low"])
    st, _ = _stills(_l1({"look": lk}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "chart"}]), work, [0])
    plain, _ = _stills(_l1({}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "chart"}]), work, [0])
    for i in range(8):
        got = st[0][i * 120 + 60, 270] / 255.0
        want = looks.apply(plain[0][i * 120 + 60, 270][None] / 255.0, lk, win=0.0)[0]
        assert np.abs(got - np.clip(want, 0, 1)).max() < 2.5 / 255.0, i


def test_skin_is_kept_while_the_rest_takes_the_look(work):
    st, _ = _stills(_l1({"look": "story_cold"}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "chart"}]), work, [0])
    plain, _ = _stills(_l1({}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "chart"}]), work, [0])

    def de(i):
        return float(np.sqrt(((looks._lab(st[0][i * 120 + 60, 270][None] / 255.0) - looks._lab(plain[0][i * 120 + 60, 270][None] / 255.0)) ** 2).sum()))
    skin, rest = [de(i) for i in (0, 1, 2)], [de(i) for i in (3, 4, 6)]
    assert max(skin) < min(rest)


def test_the_window_keeps_the_face_at_its_level_and_takes_the_room_down_softly(work):
    lk = dict(looks.IDENTITY, drop=0.7)
    shot = {"id": "s", "k": "shot", "f": [0, 12], "pic": "white", "crop": [0.0, 0.0, 1.0, 1.0], "window": [0.5, 0.5, 0.12, 0.12]}
    st, _ = _stills(_l1({"look": lk}, [shot]), work, [0])
    col = st[0][:, W // 2, 0]
    assert col[H // 2] >= 254 and abs(col[4] - 0.7 * 255) < 2            # the middle holds, the far edge is down by the drop
    d = np.diff(col[: H // 2])
    assert d.min() >= -0.5 and d.max() < 6                                # one soft slope: no step, no ring


def test_a_plate_takes_no_look(work):
    base = {"id": "s", "k": "shot", "f": [0, 12], "pic": "plate", "plate": {"c0": "#3060C0", "c1": "#3060C0"}}
    a, _ = _stills(_l1({"look": "story_low"}, [base]), work, [0])
    b, _ = _stills(_l1({}, [base]), work, [0])
    assert np.array_equal(a[0], b[0])


def test_a_film_without_the_new_keys_draws_as_it_always_did(work):
    """The frame of a plan that names no shutter and no look goes through the old path: one sample, no look."""
    crops = _pan()
    a, n = _stills(_l1({"grain": 0.03, "vignette": 0.2}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops}]), work, [10])
    b, _ = _stills(_l1({"grain": 0.03, "vignette": 0.2, "shutter": 0, "look": None}, [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crops}]), work, [10])
    assert not n.get("shutter") and np.array_equal(a[10], b[10])
