"""Lane D (20 Nov): the renderer additions, measured on drawn frames. An RGB split moves red and blue by the asked
pixels, a kept hue leaves no chroma elsewhere, a tilted card's corners land where a lens puts them, the person drawn
again through his matte keeps his pixels while the world behind him becomes the plate. Every existing item still
draws byte for byte as before (the golden frames in test_scene_engine.py)."""
import math

import numpy as np
import pytest

from scene import palette
from test_scene_palette import scene_of

# ------------------------------------------------------------------ drawn: the renderer additions

GL = pytest.importorskip("moderngl")
pytest.importorskip("uharfbuzz")
from test_scene_engine import Engine, encode, film, one, plate, scenery, stills  # noqa: E402

SMALL = (360, 640)


@pytest.fixture(scope="module")
def pics(tmp_path_factory):
    d = tmp_path_factory.mktemp("pal")
    return {"a": scenery(str(d / "a.png"), seed=5), "b": scenery(str(d / "b.png"), seed=11, warm=False), "dir": str(d)}


def small(items, dur, pics, film_=None, voices=None):
    assets = {k: {"kind": "image", "path": pics[k]} for k in ("a", "b")}
    return film(items, dur=dur, assets=assets, voices=voices, film_=film_ if film_ is not None else {"dither": 0.0}, frame=SMALL)


def shot(pic="a", dur=4, **kw):
    return dict({"id": "s", "k": "shot", "f": [0, dur], "pic": pic, "crop": [0, 0, 1, 1]}, **kw)


def shift_of(a, b, axis=1):
    """The shift of b against a along one axis, by phase correlation of the two planes."""
    import cv2
    (dx, dy), _ = cv2.phaseCorrelate(a.astype(np.float64), b.astype(np.float64))
    return dx if axis == 1 else dy


def test_rgb_split_moves_red_and_blue_by_the_asked_pixels(pics):
    plain = one(small([shot()], 4, pics)).astype(np.float64)
    for item in ({"id": "f", "k": "flash", "f": [0, 4], "mode": "rgb_split", "px": 12 * 1080 / 360, "angle": 0},):
        got = one(small([shot(), item], 4, pics)).astype(np.float64)
        r = shift_of(plain[..., 0], got[..., 0])
        b = shift_of(plain[..., 2], got[..., 2])
        assert abs(r - (-12)) < 0.6 and abs(b - 12) < 0.6, (r, b)
        assert np.abs(got[..., 1] - plain[..., 1]).max() <= 1, "green stays where it was"
    # inside a picture (its own treat): the same shift, read in the picture's own space
    got = one(small([shot(treat={"mode": "rgb_split", "px": 9 * 1080 / 360, "angle": 90})], 4, pics)).astype(np.float64)
    assert abs(shift_of(plain[..., 0], got[..., 0], 0) - (-9)) < 0.7 and abs(shift_of(plain[..., 2], got[..., 2], 0) - 9) < 0.7


def test_an_amount_curve_scales_the_split_frame_by_frame(pics):
    l1 = small([shot(), {"id": "f", "k": "flash", "f": [0, 4], "mode": "rgb_split", "px": 30.0, "amount": [1.0, 0.5, 0.0, 0.0]}], 4, pics)
    fr = stills(l1, [0, 1, 2])
    plain = one(small([shot()], 4, pics))
    d = [float(np.abs(fr[f][..., :3].astype(int) - plain).mean()) for f in (0, 1, 2)]
    assert d[0] > d[1] > d[2] and d[2] < 0.2


def test_slice_tears_some_bands_and_leaves_the_rest(pics):
    plain = one(small([shot()], 4, pics))
    got = one(small([shot(), {"id": "f", "k": "flash", "f": [0, 4], "mode": "slice", "shift": 60, "band": 40, "density": 0.4}], 4, pics))
    band = 40 * 360 / 1080
    rows = [np.abs(got[int(y):int(y + band) - 1].astype(int) - plain[int(y):int(y + band) - 1]).mean() for y in np.arange(0, 640 - band, band)]
    moved = sum(r > 3 for r in rows)
    still = sum(r < 0.5 for r in rows)
    assert moved >= 3 and still >= 3, rows
    a, b = one(small([shot(), {"id": "f", "k": "flash", "f": [0, 4], "mode": "slice"}], 4, pics)), \
        one(small([shot(), {"id": "f", "k": "flash", "f": [0, 4], "mode": "slice"}], 4, pics))
    assert np.array_equal(a, b), "the same plan tears the same bands"


def test_keep_hue_leaves_no_chroma_outside_the_kept_hue(tmp_path):
    a = np.zeros((640, 360, 3), np.uint8)
    a[:, :120] = (220, 30, 30)
    a[:, 120:240] = (30, 200, 40)
    a[:, 240:] = (40, 60, 220)
    from PIL import Image
    p = str(tmp_path / "rgb.png")
    Image.fromarray(a).save(p)
    assets = {"t": {"kind": "image", "path": p}}
    for item in ([{"id": "s", "k": "shot", "f": [0, 2], "pic": "t", "crop": [0, 0, 1, 1], "treat": {"mode": "keep_hue", "c": "#E02020"}}],
                 [{"id": "s", "k": "shot", "f": [0, 2], "pic": "t", "crop": [0, 0, 1, 1]},
                  {"id": "f", "k": "flash", "f": [0, 2], "mode": "keep_hue", "c": "#E02020"}]):
        g = one(film(item, dur=2, assets=assets, frame=SMALL)).astype(int)
        chroma = g.max(axis=2) - g.min(axis=2)
        assert chroma[320, 60] > 150, "red is kept"
        assert chroma[320, 180] <= 2 and chroma[320, 300] <= 2, "green and blue are grey"


def plane_corners(cx, cy, hw, hh, s, rot, rx, ry, W, H, persp=1.2):
    """The four corners of a tilted card, projected through a pinhole at the frame's centre (an independent twin
    of runtime.plane_hom, written from the geometry)."""
    f = persp * H
    out = []
    for qx, qy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        x, y, z = qx * s, qy * s, 0.0
        a = math.radians(-rx)                                 # about x: the top goes away for positive rx
        y, z = y * math.cos(a) - z * math.sin(a), y * math.sin(a) + z * math.cos(a)
        b = math.radians(-ry)                                 # about y: the right side goes away for positive ry
        x, z = x * math.cos(b) + z * math.sin(b), -x * math.sin(b) + z * math.cos(b)
        r = math.radians(rot)
        x, y = x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r)
        X, Y, Z = cx + x, cy + y, z
        k = f / (Z + f)
        out.append((W / 2 + (X - W / 2) * k, H / 2 + (Y - H / 2) * k))
    return np.array(out)


def test_a_tilted_card_lands_on_the_quad_a_lens_would_draw():
    import cv2
    W, H = 1080, 1920
    card = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": "#F0F0F0", "c1": "#F0F0F0"}, "x": 0.45, "y": 0.52,
            "w": 0.6, "h": 0.3, "s": 1.0, "rx": 38, "ry": -20, "rot": 6, "op": 1}
    a = one(film([plate(2, "#000000"), card], dur=2))[..., 0]
    m = (a > 128).astype(np.uint8)
    want = plane_corners(0.45 * W, 0.52 * H, 0.3 * W, 0.15 * H, 1.0, 6, 38, -20, W, H)
    ref = np.zeros_like(m)
    cv2.fillPoly(ref, [np.round(want * 16).astype(np.int32)], 1, lineType=cv2.LINE_8, shift=4)
    iou = float((m & ref).sum()) / float((m | ref).sum())
    assert iou > 0.985, iou
    flat = dict(card, rx=40, ry=0, rot=0)
    b = one(film([plate(2, "#000000"), flat], dur=2))[..., 0] > 128
    ys = np.where(b.any(axis=1))[0]
    top, bot = np.ptp(np.where(b[ys.min() + 2])[0]), np.ptp(np.where(b[ys.max() - 2])[0])
    assert top < bot - 10, ("the far (top) edge is the shorter", top, bot)


def test_depth_of_field_softens_the_card_away_from_its_focus(pics):
    import cv2
    card = {"id": "c", "k": "card", "f": [0, 2], "pic": "b", "crop": [0, 0, 1, 1], "x": 0.5, "y": 0.5, "w": 0.9, "h": 0.6, "rx": 50,
            "dof": {"blur": 6.0, "focus": [0.5, 0.85]}}
    g = one(small([shot("a", 2), card], 2, pics))[..., 1].astype(np.float32)
    lap = np.abs(cv2.Laplacian(g, cv2.CV_32F))
    near = float(lap[450:500, 120:240].mean())
    far = float(lap[200:250, 120:240].mean())
    assert near > 1.5 * far, (near, far)


def test_a_tilted_word_narrows_to_its_far_edge():
    W, H = 1080, 1920
    w = {"id": "w", "k": "word", "f": [0, 2], "t": "HHHH", "v": "a", "x": 0.1, "y": 0.55, "ink": "#FFFFFF", "rx": 50}
    big = {"a": {"font": "InterDisplay-Bold.ttf", "px": 300, "track": 0}}
    a = one(film([plate(2, "#000000"), w], dur=2, voices=big))[..., 0]
    ys, xs = np.where(a > 128)
    top, bot = ys.min() + 3, ys.max() - 3
    wt, wb = np.ptp(np.where(a[top] > 128)[0]), np.ptp(np.where(a[bot] > 128)[0])
    assert wt < wb - 4, (wt, wb)
    flat = one(film([plate(2, "#000000"), dict(w, rx=0)], dur=2, voices=big))[..., 0]
    assert (ys.max() - ys.min()) < np.ptp(np.where(flat > 128)[0]), "and its height is foreshortened"


def test_card_blends_add_screen_and_multiply():
    def px(blend, under="#808080", over="#404040"):
        c = {"id": "c", "k": "card", "f": [0, 2], "pic": "plate", "plate": {"c0": over, "c1": over}, "x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5,
             "blend": blend}
        return one(film([plate(2, under), c], dur=2))[960, 540, 0]
    u, o = 0x80 / 255, 0x40 / 255
    assert abs(px("add") - 255 * min(u + o, 1)) <= 2
    assert abs(px("screen") - 255 * (1 - (1 - u) * (1 - o))) <= 2
    assert abs(px("multiply") - 255 * u * o) <= 2
    assert abs(px("normal") - 0x40) <= 1


def matte_case(tmp_path, n=12):
    """A take of grey with a white square person at the centre of his matte, on main's clock."""
    w, h = 270, 480
    pic = np.full((h, w, 3), 90, np.uint8)
    pic[180:300, 90:180] = (200, 60, 50)                  # he wears red
    m = np.zeros((h, w, 3), np.uint8)
    m[180:300, 90:180] = 255
    encode(str(tmp_path / "s.mp4"), [pic] * 30, w, h)
    encode(str(tmp_path / "m.mp4"), [m] * 30, w, h, crf=4)
    assets = {"main": {"kind": "video", "path": "s.mp4", "w": w, "h": h, "dur": 1.0, "fps": 30},
              "matte": {"kind": "video", "path": "m.mp4", "w": w, "h": h, "dur": 1.0, "fps": 30}}
    sh = {"id": "s", "k": "shot", "f": [0, n], "pic": "main", "src": [0.0, n / 30], "crop": [0, 0, 1, 1]}
    return assets, sh


def test_the_person_stays_his_while_the_world_behind_him_becomes_the_plate(tmp_path):
    assets, sh = matte_case(tmp_path)
    take = one(film([sh], dur=12, assets=assets), work=str(tmp_path))
    l1 = scene_of([sh], 12, assets, frame=(1080, 1920))
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "bg_swap", "id": "x", "f": [0, 12], "c": "#00C040"}])
    assert log[0]["done"] and log[0]["dev"] == "bg_swap", log
    got = stills(out, [5], work=str(tmp_path))[5][..., :3].astype(int)
    H, W = got.shape[:2]
    inside = (slice(int(H * 0.42), int(H * 0.58)), slice(int(W * 0.38), int(W * 0.62)))
    outside = (slice(int(H * 0.05), int(H * 0.25)), slice(int(W * 0.05), int(W * 0.95)))
    assert np.abs(got[inside] - take[inside]).max() <= 5, "inside the matte: the take"
    assert np.abs(got[outside] - np.array([0x00, 0xC0, 0x40])).max() <= 12, "outside: the plate"


def test_keep_passes_fill_offset_glow_and_outline(tmp_path):
    assets, sh = matte_case(tmp_path)
    W, H = 1080, 1920
    green = {"id": "g", "k": "card", "f": [0, 12], "pic": "plate", "plate": {"c0": "#00FF00", "c1": "#00FF00"}, "x": 0.5, "y": 0.5, "w": 1, "h": 1,
             "behind": True}
    c = (int(H * 0.5), int(W * 0.5))
    fill = one(film([dict(sh, keep=[{"mode": "fill", "c": "#FF00FF"}]), green], dur=12, assets=assets), 5, work=str(tmp_path))
    assert np.abs(fill[c] - (255, 0, 255)).max() <= 3 and fill[int(H * 0.1), int(W * 0.5), 1] > 240
    clone = one(film([dict(sh, keep=[{"mode": "person", "dx": 0.3, "a": 1.0}, {"mode": "person"}]), green], dur=12, assets=assets), 5,
                work=str(tmp_path))
    assert clone[c[0], int(W * 0.8), 0] > 150 and clone[c[0], int(W * 0.8), 1] < 120, "a copy of him a third of the frame to the right"
    glow = one(film([dict(sh, keep=[{"mode": "glow", "c": "#FFFF00", "w": 40}, {"mode": "person"}])], dur=12, assets=assets), 5, work=str(tmp_path))
    edge = (c[0], int(W * (180 / 270)) + 20)
    take = one(film([sh], dur=12, assets=assets), 5, work=str(tmp_path))
    assert glow[edge][0] > take[edge][0] + 40 and glow[edge][2] <= take[edge][2] + 3, "a yellow halo just outside him"
    assert np.abs(glow[c] - take[c]).max() <= 3
    out = one(film([dict(sh, keep=[{"mode": "outline", "c": "#FFFFFF", "w": 16}, {"mode": "person"}]), green], dur=12, assets=assets), 5,
              work=str(tmp_path))
    ring = (c[0], int(W * (180 / 270)) + 8)
    assert out[ring].min() > 230 and out[int(H * 0.1), int(W * 0.5), 1] > 240 and out[int(H * 0.1), int(W * 0.5), 0] < 20


def test_keep_colour_greys_the_room_and_keeps_him(tmp_path):
    assets, sh = matte_case(tmp_path)
    room = np.full((480, 270, 3), 0, np.uint8)
    room[:, :] = (40, 70, 160)
    room[180:300, 90:180] = (200, 60, 50)
    encode(str(tmp_path / "s.mp4"), [room] * 30, 270, 480)
    l1 = scene_of([sh], 12, assets, frame=(1080, 1920))
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "keep_colour", "id": "k", "f": [0, 12]}])
    g = stills(out, [5], work=str(tmp_path))[5][..., :3].astype(int)
    ch = g.max(axis=2) - g.min(axis=2)
    assert ch[960, 540] > 100 and ch[200, 540] <= 4, (ch[960, 540], ch[200, 540])


@pytest.mark.parametrize("mode", ["leak", "filmburn", "prism", "vhs", "led", "xerox", "projector", "paper", "mark"])
def test_every_surface_draws_the_same_twice_and_changes_the_frame(pics, mode):
    extra = {"rect": [0.1, 0.4, 0.9, 0.5]} if mode == "mark" else {"amount": 0.6} if mode == "filmburn" else {}
    l1 = small([shot(dur=6), dict({"id": "f", "k": "flash", "f": [0, 6], "mode": mode}, **extra)], 6, pics)
    a, b = stills(l1, [3])[3], stills(l1, [3])[3]
    plain = one(small([shot(dur=6)], 6, pics), 3)
    assert np.array_equal(a, b)
    region = (slice(int(0.4 * 640), int(0.5 * 640)), slice(None)) if mode == "mark" else (slice(None), slice(None))
    assert np.abs(a[..., :3].astype(int)[region] - plain[region]).mean() > 2.0, mode


def test_the_projector_breathes_and_never_strobes(pics):
    l1 = small([shot(dur=30), {"id": "f", "k": "flash", "f": [0, 30], "mode": "projector"}], 30, pics)
    fr = stills(l1, list(range(30)))
    lum = np.array([fr[f][..., :3].astype(float).mean() for f in range(30)])
    assert np.abs(np.diff(lum)).max() < 0.1 * lum.mean()


def test_the_mark_multiplies_inside_its_rect_only(pics):
    plain = one(small([shot()], 4, pics))
    got = one(small([shot(), {"id": "f", "k": "flash", "f": [0, 4], "mode": "mark", "c": "#FFFF00", "rect": [0.2, 0.4, 0.8, 0.5]}], 4, pics))
    assert np.abs(got[100] - plain[100]).max() == 0 and np.abs(got[600] - plain[600]).max() == 0
    mid = got[288, 100:260].astype(int)
    assert mid[:, 2].mean() < plain[288, 100:260, 2].mean() * 0.2 + 2 and np.abs(mid[:, 0] - plain[288, 100:260, 0]).max() <= 2


def test_folded_shaders_draw_the_new_paths_as_the_full_ones(pics, tmp_path, monkeypatch):
    """A software rasteriser folds the gating uniforms (uTilt, uKMode) to constants: the frames must not change."""
    assets, sh = matte_case(tmp_path)
    scenes = [small([shot(), {"id": "c", "k": "card", "f": [0, 4], "pic": "b", "crop": [0, 0, 1, 1], "x": 0.5, "y": 0.5, "w": 0.7, "h": 0.4,
                              "rx": 30, "ry": 10, "dof": {"blur": 3.0}}], 4, pics),
              film([dict(sh, keep=[{"mode": "glow", "c": "#FFFF00", "w": 30}, {"mode": "duo", "c": "#101010", "c2": "#F0F0F0"}])], dur=12,
                   assets=assets, frame=SMALL)]
    full = [one(s, 2, work=str(tmp_path)) for s in scenes]
    monkeypatch.setenv("VALMERA_SCENE_FOLD", "1")
    folded = [one(s, 2, work=str(tmp_path)) for s in scenes]
    for a, b in zip(full, folded):
        assert np.abs(a - b).max() <= 1
