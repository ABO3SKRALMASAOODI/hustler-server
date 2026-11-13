"""The picture and motion engine's numbers (worker/scene: bake.move, motion, pace, looks). No GL here:
curves, framings, the pacing driver and the look's numpy twin. test_scene_shutter.py draws them."""
import math

import numpy as np
import pytest

pytest.importorskip("uharfbuzz")

import scene.compile as SC          # noqa: E402
from scene import bake, looks, motion, pace  # noqa: E402

FPS, W, H = 30.0, 1080, 1920


# ----------------------------------------------------------------- curves

def test_a_move_ends_where_it_was_sent_and_never_turns_back():
    for kind in ("out", "io", "in"):
        c = bake.move(20, 1.0, 1.3, kind, frames=12)
        assert c[0] == 1.0 and abs(c[-1] - 1.3) < 1e-9 and c[12] == 1.3
        assert all(b >= a - 1e-9 for a, b in zip(c, c[1:]))


def test_the_three_measured_shapes_peak_where_the_references_do():
    def peak(kind):
        c = bake.move(41, 0.0, 1.0, kind, frames=40)
        v = [b - a for a, b in zip(c, c[1:])]
        return v.index(max(v)) / 40.0
    assert peak("out") < 0.35 < 0.65 < peak("in")
    assert 0.3 <= peak("io") <= 0.7


def test_an_overshoot_passes_its_end_once_and_is_back_in_six_frames():
    c = bake.move(30, 0.0, 1.0, "out", frames=12, over=bake.MOVE_OVER)
    assert max(c) == pytest.approx(1.0 + bake.MOVE_OVER, abs=1e-6)
    assert c[12 + bake.MOVE_BACK] == 1.0 and c[-1] == 1.0
    assert all(v >= 1.0 for v in c[12:])


def test_a_move_can_wait_for_its_word():
    c = bake.move(20, 0.0, 1.0, "out", frames=8, delay=5)
    assert c[:6] == [0.0] * 6 and c[13] == 1.0


def test_curve_specs_resolve_the_new_curves():
    v = bake.resolve({"curve": "move", "a": 1.0, "b": 1.1, "kind": "in", "frames": 6}, 10)
    assert len(v) == 10 and v[-1] == pytest.approx(1.1)
    assert len(bake.resolve({"curve": "cam_settle", "size": 0.05}, 12)) == 12


# ----------------------------------------------------------------- framings

def _aspect(cam, c):
    return (c[2] - c[0]) * cam.pw / ((c[3] - c[1]) * cam.ph)


def test_every_framing_has_the_frames_shape_and_stays_in_the_picture():
    for pw, ph in ((4000, 3000), (1252, 1545), (1920, 1080), (900, 2000)):
        cam = motion.Cam(pw, ph, W / H, out_w=W)
        for c in (cam.cover(), cam.cover(focus=(0.1, 0.9)), cam.rect(0.95, 0.02, 0.2), cam.on_region([0.6, 0.6, 0.9, 0.8])):
            assert _aspect(cam, c) == pytest.approx(W / H, rel=1e-6)
            assert min(c) >= -1e-9 and max(c) <= 1 + 1e-9


def test_a_face_framing_puts_the_eyes_where_the_references_do_and_keeps_the_head_whole():
    cam = motion.Cam(4000, 3000, W / H, out_w=W)
    face = [0.40, 0.30, 0.10, 0.18]
    c = cam.on_face(face, height=0.30, eyes=0.37)
    h = c[3] - c[1]
    assert (0.37 - c[1]) / h == pytest.approx(motion.EYES_Y, abs=0.01)
    assert face[3] / h == pytest.approx(0.30, abs=0.01)
    assert cam.whole(c, [face])


def test_a_framing_never_asks_for_more_than_the_upscale_limit():
    cam = motion.Cam(2400, 1600, W / H, out_w=W)
    c = cam.on_face([0.45, 0.4, 0.03, 0.05], height=0.42)
    assert cam.up(c, W) <= motion.MAX_UP * 1.001
    # a picture too small for the frame is drawn at its cover and no tighter
    small = motion.Cam(1200, 800, W / H, out_w=W)
    assert small.on_face([0.45, 0.4, 0.03, 0.05], height=0.42) == pytest.approx(small.cover(focus=(0.465, 0.42)), abs=0.05)
    assert small.w_min == small.w_max


def test_keep_whole_moves_a_crop_off_a_head_it_was_cutting():
    cam = motion.Cam(4000, 3000, W / H, out_w=W)
    face = [0.30, 0.30, 0.10, 0.20]
    cut = cam.rect(0.42, 0.42, 0.2)                 # its left edge runs through the face
    assert not cam.whole(cut, [face])
    assert cam.whole(cam.keep_whole(cut, [face]), [face])


def test_a_drift_holds_one_speed():
    cam = motion.Cam(4000, 3000, W / H, out_w=W)
    crops = motion.track(cam, 31, cam.rect(0.5, 0.5, 0.3), FPS, drift=dict(rate=0.06, dir=1))
    z = [math.log(crops[0][2] - crops[0][0]) - math.log(c[2] - c[0]) for c in crops]
    assert z[-1] == pytest.approx(math.log(1.06), rel=0.02)
    assert z[15] == pytest.approx(z[-1] / 2, rel=0.03)            # linear: half the travel at half the time


def test_a_whip_out_gathers_speed_and_a_whip_in_brakes():
    cam = motion.Cam(4000, 3000, W / H, out_w=W)
    base = cam.rect(0.5, 0.5, 0.2)
    out = motion.speed(motion.track(cam, 20, base, FPS, whip_out=dict(frames=5, travel=0.28, ang=0.0)))
    assert out[10][0] == 0 and out[-1][0] > out[-3][0] > 0
    assert sum(p for p, _ in out) == pytest.approx(0.28, rel=0.05)
    inn = motion.speed(motion.track(cam, 20, base, FPS, whip_in=dict(frames=5, travel=0.28, ang=0.0)))
    assert inn[0][0] > inn[2][0] > 0 and inn[10][0] == 0


def test_an_eased_move_reaches_its_framing():
    cam = motion.Cam(4000, 3000, W / H, out_w=W)
    a, b = cam.rect(0.5, 0.5, 0.3), cam.rect(0.55, 0.45, 0.24)
    crops = motion.track(cam, 30, a, FPS, to=dict(crop=b, at=6, frames=12, kind="out"))
    assert crops[5] == [motion.r5(v) for v in a]
    assert crops[-1] == pytest.approx(b, abs=2e-4)


# ----------------------------------------------------------------- the voice

def test_stress_peaks_are_found_where_the_voice_hits():
    sr = 16000
    x = np.random.RandomState(1).randn(sr * 4).astype(np.float32) * 0.002
    hits = (0.8, 1.7, 2.9)
    for t in hits:
        i = int(t * sr)
        x[i:i + 1600] += np.sin(np.arange(1600) * 0.5).astype(np.float32) * 0.4 * np.hanning(1600).astype(np.float32)
    pk = pace.stress_env(x, sr, FPS)
    got = [f / FPS for f, _ in pk]
    assert len(got) == 3
    for t, g in zip(hits, got):
        assert abs(g - (t + 0.05)) < 0.06


def test_word_timings_alone_give_a_stress_too():
    st = pace.stress_of_words([(0.0, 0.2), (0.2, 0.4), (0.4, 1.0), (1.4, 1.6)], FPS)
    assert max(st, key=lambda r: r[1])[0] == int(round(0.4 * FPS))       # the long word before the pause


# ----------------------------------------------------------------- the driver

def _film(n_shots=6, length=75, faces=True):
    shots, f = [], 0
    for i in range(n_shots):
        sh = {"id": f"s{i}", "f": [f, f + length], "pic": f"p{i % 4}", "pw": 3840, "ph": 2560}
        if faces and i % 2 == 0:
            sh["faces"] = [[0.42, 0.22, 0.12, 0.22, 0.31]]
        else:
            sh["regions"] = [[0.1, 0.5, 0.4, 0.8], [0.6, 0.2, 0.9, 0.5]]
        shots.append(sh)
        f += length
    stress = [(k, 0.3 + 0.7 * ((k * 7919) % 13) / 12.0) for k in range(4, f, 9)]
    return shots, stress


def test_no_framing_is_held_past_the_band_and_none_is_shorter_than_its_floor():
    shots, stress = _film()
    items, log = pace.drive(shots, stress, FPS, (W, H), "story", seed=3)
    lo, hi = pace.BANDS["story"]["hold"]
    for it in items:
        n = (it["f"][1] - it["f"][0]) / FPS
        assert lo - 1e-6 <= n <= hi + 1e-6
    assert log["hold_max"] <= pace.BANDS["story"]["never"]
    assert items[0]["f"][0] == 0 and items[-1]["f"][1] == shots[-1]["f"][1]
    assert all(a["f"][1] == b["f"][0] for a, b in zip(items, items[1:]))          # one base picture on every frame


def test_a_change_lands_on_a_stress_peak():
    shots, stress = _film()
    items, _ = pace.drive(shots, stress, FPS, (W, H), "story", seed=3)
    peaks = {f for f, _ in stress}
    cuts_in = [it["f"][0] for it in items if "." in it["id"]]
    assert cuts_in and all(f in peaks for f in cuts_in)


def test_the_driver_never_returns_to_the_framing_it_just_left():
    shots, stress = _film(n_shots=2, length=300)
    _, log = pace.drive(shots, stress, FPS, (W, H), "story", seed=5)
    by = {}
    for m in log["moves"]:
        by.setdefault(m["pic"], []).append(m["framing"])
    for seq in by.values():
        assert len(seq) >= 4
        assert all(a != b for a, b in zip(seq, seq[1:]))
        assert all(a != c for a, c in zip(seq, seq[2:]))


def test_no_head_is_cut_on_any_frame_and_every_frame_is_inside_its_picture():
    shots, stress = _film()
    items, _ = pace.drive(shots, stress, FPS, (W, H), "story", seed=9)
    faces = {s["id"]: [f[:4] for f in s.get("faces") or []] for s in shots}
    for it in items:
        cam = motion.Cam(3840, 2560, W / H, out_w=W)
        fs = faces[it["id"].split(".")[0]]
        assert len(it["crop"]) == it["f"][1] - it["f"][0]
        for c in it["crop"]:
            assert min(c) >= -1e-6 and max(c) <= 1 + 1e-6
            assert cam.whole(c, fs)
            assert cam.up(c, W) <= motion.MAX_UP / 0.92 * 1.001          # a move may pass the framing's limit by 8%


def test_a_framing_is_never_still():
    shots, stress = _film()
    items, _ = pace.drive(shots, stress, FPS, (W, H), "story", seed=9)
    for it in items:
        sp = motion.speed(it["crop"])
        assert sum(1 for p, z in sp if p > 0 or abs(z) > 0) >= 0.8 * len(sp)


def test_the_same_film_and_seed_give_the_same_cut():
    shots, stress = _film()
    a, _ = pace.drive(shots, stress, FPS, (W, H), "story", seed=4)
    b, _ = pace.drive(shots, stress, FPS, (W, H), "story", seed=4)
    assert a == b


def test_a_take_cut_into_framings_keeps_its_clock():
    shots = [{"id": "take", "f": [0, 300], "pic": "main", "pw": 3840, "ph": 2160, "src": [10.0, 20.0], "faces": [[0.42, 0.2, 0.14, 0.28]]}]
    stress = [(k, 0.9) for k in range(10, 300, 17)]
    items, log = pace.drive(shots, stress, FPS, (W, H), "story", seed=1)
    assert len(items) >= 3
    assert items[0]["src"][0] == 10.0 and items[-1]["src"][1] == 20.0
    for a, b in zip(items, items[1:]):
        assert a["src"][1] == b["src"][0]
    for it in items:
        assert it["src"][0] == pytest.approx(10.0 + it["f"][0] / 30.0, abs=1e-3)


def test_a_podcast_take_is_eased_and_never_cut():
    """The references never punch a take with a cut: they ease it, a few per cent at a time, and it keeps moving."""
    face = [0.42, 0.2, 0.14, 0.28]
    shots = [{"id": "take", "f": [0, 600], "pic": "main", "pw": 1920, "ph": 1080, "src": [0.0, 20.0], "faces": [face], "max_up": 2.7}]
    stress = [(k, 0.4 + 0.6 * ((k * 31) % 7) / 6.0) for k in range(10, 600, 13)]
    items, log = pace.drive(shots, stress, FPS, (W, H), "podcast", seed=1)
    assert len(items) == 1 and items[0]["src"] == [0.0, 20.0] and len(items[0]["crop"]) == 600
    assert log["eased"] >= 6 and 3.0 <= log["per10"] <= 8.0
    crops = items[0]["crop"]
    sp = motion.speed(crops)
    assert max(abs(z) for _, z in sp) < 0.02                       # no step of a cut-in anywhere: under 2% a frame
    assert sum(1 for p, z in sp if p > 0 or abs(z) > 0) >= 0.75 * len(sp)
    cam = motion.Cam(1920, 1080, W / H, out_w=W, max_up=2.7)
    assert all(cam.whole(c, [face]) for c in crops)
    widths = [c[2] - c[0] for c in crops]
    assert max(widths) / min(widths) >= 1.08                       # it does go somewhere


def test_a_locked_shot_passes_through_untouched():
    plate = {"id": "pg", "k": "shot", "f": [0, 40], "pic": "plate", "plate": {"c0": "#101010"}}
    shots = [{"id": "pg", "f": [0, 40], "pic": "plate", "lock": True, "item": plate},
             {"id": "s", "f": [40, 100], "pic": "p", "pw": 2000, "ph": 1500}]
    items, _ = pace.drive(shots, [], FPS, (W, H))
    assert items[0] is plate and items[1]["f"][0] == 40


def test_a_whip_runs_one_way_through_its_cut():
    shots, stress = _film(n_shots=12, length=40)
    items, log = pace.drive(shots, stress, FPS, (W, H), "story", seed=2, band={"whip": 0.5})
    assert log["whips"] >= 1
    hit = 0
    for a, b in zip(items, items[1:]):
        va, vb = motion.speed(a["crop"])[-1], motion.speed(b["crop"])[0]
        if va[0] > 0.03 and vb[0] > 0.03:
            da = a["crop"][-1][0] - a["crop"][-2][0]
            db = b["crop"][1][0] - b["crop"][0][0]
            dya = a["crop"][-1][1] - a["crop"][-2][1]
            dyb = b["crop"][1][1] - b["crop"][0][1]
            assert da * db + dya * dyb > 0
            hit += 1
    assert hit == log["whips"]


def test_regions_are_where_the_detail_is():
    a = np.full((300, 400), 128.0)
    a[40:120, 260:360] += np.random.RandomState(0).randn(80, 100) * 40
    r = pace.regions_of(a, n=1, size=0.4)[0]
    cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
    assert 0.6 < cx < 0.95 and 0.1 < cy < 0.5


# ----------------------------------------------------------------- the look

def _lab_de(a, b):
    return float(np.sqrt(((looks._lab(a) - looks._lab(b)) ** 2).sum(-1)).mean())


SKIN = np.array([[224, 172, 150], [198, 140, 110], [141, 85, 60], [255, 219, 190], [110, 70, 50]]) / 255.0
OTHER = np.array([[40, 90, 200], [30, 160, 60], [120, 120, 120], [200, 200, 40], [150, 40, 160], [20, 30, 40]]) / 255.0


def test_the_skin_key_finds_skin_of_every_depth_and_nothing_else_here():
    assert looks.skin_key(SKIN).min() > 0.8
    assert looks.skin_key(OTHER).max() < 0.2


def test_an_identity_look_changes_nothing():
    px = np.random.RandomState(2).rand(500, 3)
    assert np.abs(looks.apply(px, looks.IDENTITY) - px).max() < 1e-9


def test_skin_moves_less_than_everything_else_under_every_look():
    for name, lk in looks.LOOKS.items():
        if lk["sat"] == 0:
            continue
        moved_skin, moved_rest = _lab_de(looks.apply(SKIN, lk), SKIN), _lab_de(looks.apply(OTHER, lk), OTHER)
        assert moved_skin < moved_rest, name
        # skin keeps its hue: the angle of its chroma turns by less than six degrees
        a0, a1 = looks._lab(SKIN), looks._lab(looks.apply(SKIN, lk))
        turn = np.degrees(np.abs(np.arctan2(a1[:, 2], a1[:, 1]) - np.arctan2(a0[:, 2], a0[:, 1])))
        assert turn.max() < 6.0, name


def test_a_face_framing_carries_its_window_for_the_look():
    shots, stress = _film(n_shots=2, length=90)
    items, _ = pace.drive(shots, stress, FPS, (W, H), "story", seed=3)
    with_face = [it for it in items if it["id"].startswith("s0")]
    assert with_face and all(len(it["window"]) == 4 for it in with_face)
    assert all("window" not in it for it in items if it["id"].startswith("s1"))       # no face in that picture: the room is the picture
    cx, cy, rx, ry = with_face[0]["window"]
    assert 0.42 < cx < 0.54 and 0.22 < cy < 0.44 and rx > 0 and ry > 0


def test_the_solver_takes_a_bright_flat_picture_toward_the_references_and_keeps_a_face_lit():
    rng = np.random.RandomState(4)
    px = np.clip(0.55 + 0.12 * rng.randn(4000, 3), 0, 1)
    px[:800] = np.clip(SKIN[0] + 0.03 * rng.randn(800, 3), 0, 1)
    lk, gr, rep = looks.solve("story_low", px)
    b, a, t = rep["before"], rep["after"], rep["target"]
    assert abs(a["L50"] - t["L50"]) < abs(b["L50"] - t["L50"])
    flat = looks.numbers(np.clip(px * gr["gain"], 0, 1))                  # the exposure alone, no look
    assert (a["L95"] - a["L5"]) > (flat["L95"] - flat["L5"])
    assert a["skin_L"] >= looks.SKIN_L_MIN - 0.5
    assert looks.CON[0] <= lk["con"] <= looks.CON[1] and 2 ** -looks.EV_MAX - 1e-3 <= gr["gain"] <= 2 ** looks.EV_MAX + 1e-3


def test_the_same_pixels_give_the_same_look():
    px = np.random.RandomState(5).rand(3000, 3)
    assert looks.solve("podcast_low", px) == looks.solve("podcast_low", px)


def test_a_film_dict_carries_numbers_only_for_the_runtime():
    f = looks.film("story_low")
    assert set(f["look"]) == set(looks.LOOK_KEYS)
    u = looks.uniforms(f["look"])
    assert len(u["lk0"]) == 4 and len(u["sk"]) == 4
    assert abs(sum(c * w for c, w in zip(u["sh"], looks.LUMA))) < 1e-9          # a hue adds no luma


# ----------------------------------------------------------------- compile

def _l1(tmp, film, crop):
    from PIL import Image
    Image.fromarray((np.random.RandomState(0).rand(200, 300, 3) * 255).astype(np.uint8)).save(tmp / "p.png")
    return {"l1": 1, "sid": "t", "seed": 1, "fps": 30, "frame": [540, 960], "dur_f": 12, "assets": {"p": {"kind": "image", "path": "p.png"}},
            "voices": {}, "film": film, "items": [{"id": "s", "k": "shot", "f": [0, 12], "pic": "p", "crop": crop}]}


def test_a_named_look_becomes_numbers_in_the_plan_and_an_unknown_one_is_said(tmp_path):
    plan = SC.compile(_l1(tmp_path, {"look": "story_low"}, [0.2, 0.1, 0.5, 0.9]), str(tmp_path), segment=False)
    assert plan.film["look"]["con"] == looks.LOOKS["story_low"]["con"] and not plan.warn
    plan = SC.compile(_l1(tmp_path, {"look": "no_such"}, [0.2, 0.1, 0.5, 0.9]), str(tmp_path), segment=False)
    assert "look" not in plan.film and any("unknown look" in w for w in plan.warn)


def test_the_cost_program_counts_the_frames_the_shutter_blurs(tmp_path):
    cam = motion.Cam(300, 200, 540 / 960, out_w=540)
    crops = motion.track(cam, 12, cam.rect(0.4, 0.5, 0.3), 30, whip_out=dict(frames=4, travel=0.3))
    still = SC.compile(_l1(tmp_path, {"shutter": 0.6}, [0.2, 0.1, 0.5, 0.9]), str(tmp_path), segment=False)
    moving = SC.compile(_l1(tmp_path, {"shutter": 0.6}, crops), str(tmp_path), segment=False)
    off = SC.compile(_l1(tmp_path, {}, crops), str(tmp_path), segment=False)
    assert "shutter" not in still.doc["program"][0]["ops"] and "shutter" not in off.doc["program"][0]["ops"]
    assert 3 <= moving.doc["program"][0]["ops"]["shutter"] <= 5
    assert SC.estimate(moving, "llvmpipe") > SC.estimate(off, "llvmpipe")
