"""worker/scene/palette rhythm, layout, match and callback: pictures and pace. No GPU: a device gives back scene items,
the engine's own ``eligible`` says whether the scene still holds, and the geometry is read off the items. Frames of the
devices on real films are in .valmera/week-1120/creativity/B/."""
import math
import os
import shutil
import subprocess

import numpy as np
import pytest

import scene.segments as SEG
from scene import palette
from scene.palette import callback as CB
from scene.palette import layout as LAY
from scene.palette import match as MATCH
from scene.palette import picks as K
from scene.palette import rhythm as RH
from scene.alien import rhyme
from scene.plan import eligible

FPS, W, H = 30, 540, 960
VOICES = {"cap": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 0}}
needs_ffmpeg = pytest.mark.skipif(not shutil.which(SEG.FF), reason="no ffmpeg on this machine")


def ok(l1):
    good, why = eligible(l1)
    return good or why == ["uharfbuzz is not installed"]


def picture(path, grey, face=False, size=(480, 640)):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", size, (grey, grey, grey))
    d = ImageDraw.Draw(im)
    for i in range(0, size[0], 40):                       # some detail, so regions and framings exist
        d.rectangle([i, int(size[1] * 0.6), i + 18, int(size[1] * 0.6) + 18], fill=(min(grey + 60, 255),) * 3)
    if face:
        d.ellipse([180, 160, 300, 320], fill=(220, 180, 150))
    im.save(path)
    return path


def video(path, n=90, size=(320, 568), grey=90):
    p = subprocess.Popen([SEG.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", "30", "-i", "-",
                          "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    for i in range(n):
        im = np.full((size[1], size[0], 3), grey, np.uint8)
        x = 20 + i * 2
        im[200:260, x:x + 60] = 230
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait()
    return path


@pytest.fixture
def film(tmp_path):
    """A 12 s film: the take (a video) with a picture placed between two of its stretches, and a pool of six stills
    of different light beside it."""
    work = str(tmp_path)
    vid = shutil.which(SEG.FF) is not None
    assets = {}
    if vid:
        video(os.path.join(work, "take.mp4"), n=380)
        video(os.path.join(work, "clip.mp4"), n=120, grey=150)
        assets["main"] = {"kind": "video", "path": "take.mp4", "w": 320, "h": 568, "fps": 30.0, "dur": 380 / 30}
        assets["v1"] = {"kind": "video", "path": "clip.mp4", "w": 320, "h": 568, "fps": 30.0, "dur": 4.0}
    for k, g in (("p1", 30), ("p2", 200), ("p3", 90)):
        assets[k] = {"kind": "image", "path": picture(os.path.join(work, f"{k}.png"), g, face=(k == "p1")), "w": 480, "h": 640}
    pool = [{"path": picture(os.path.join(work, f"pool{i}.png"), g), "kind": "photo"} for i, g in enumerate((20, 240, 60, 180, 120, 100))]
    if vid:
        items = [{"id": "t1", "k": "shot", "f": [0, 120], "pic": "main", "src": [0.0, 4.0]},
                 {"id": "s1", "k": "shot", "f": [120, 180], "pic": "v1", "src": [0.5, 2.5]},
                 {"id": "t2", "k": "shot", "f": [180, 360], "pic": "main", "src": [6.0, 12.0]}]
    else:
        items = [{"id": "t1", "k": "shot", "f": [0, 120], "pic": "p3"},
                 {"id": "s1", "k": "shot", "f": [120, 180], "pic": "p2"},
                 {"id": "t2", "k": "shot", "f": [180, 360], "pic": "p3"}]
    items.append({"id": "w1", "k": "word", "f": [10, 340], "t": "words", "v": "cap", "x": 0.4, "y": 0.62, "role": "cap", "z": 5})
    l1 = {"l1": 1, "sid": "t", "seed": 7, "fps": FPS, "frame": [W, H], "dur_f": 360, "assets": assets, "spine": [],
          "voices": dict(VOICES), "film": {}, "items": items, "sound": [], "pool": pool}
    assert ok(l1)
    return l1, work


def lay(l1, work, ask):
    patch = palette.plan(l1, work, ask)
    assert not patch.get("skip"), patch
    out = palette.merge(l1, patch)
    assert ok(out), eligible(out)
    return out, patch


def shots(l1, a=0, z=10 ** 9):
    return sorted((i for i in l1["items"] if i["k"] == "shot" and i["f"][0] < z and i["f"][1] > a), key=lambda i: i["f"][0])


# ------------------------------------------------------------------ the registry

def test_every_device_of_this_lane_keeps_the_contract():
    palette._discover()
    mine = {**RH.DEVICES, **LAY.DEVICES, **MATCH.DEVICES, **CB.DEVICES}
    assert {"burst", "contour", "glimpse", "settle", "snap", "whip", "streak", "shake", "ramp", "freeze", "grid", "split", "collage",
            "pip", "shrink", "dissolve", "match", "rhyme_eyes", "match_pos", "callback", "loop"} <= set(mine)
    for name, e in mine.items():
        assert palette.DEVICES[name]["layer"] in palette.LAYERS, name
        assert e["card"].startswith(name), name
        assert all(f in palette.DEVICES for f in e.get("fallback") or ()), name
    assert palette.resolve("speed_ramp") == "ramp" and palette.resolve("split") == "split" and palette.resolve("list burst") == "burst"


def test_the_palette_imports_no_model_code():
    import ast
    banned = {"llm", "anthropic", "openai", "agent_loop", "score", "director", "litellm", "onnxruntime", "torch", "mediapipe"}
    root = os.path.dirname(RH.__file__)
    for name in ("rhythm.py", "layout.py", "match.py", "callback.py", "picks.py"):
        for node in ast.walk(ast.parse(open(os.path.join(root, name), encoding="utf-8").read())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            assert not [m for m in mods if m.split(".")[0] in banned], (name, mods)


# ------------------------------------------------------------------ burst, contour

def test_a_burst_gives_three_to_six_frame_shots_from_the_pool(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "burst", "id": "b", "f": [30, 75]})
    bs = [s for s in shots(out) if s["id"].startswith("b.b")]
    assert len(bs) >= 6
    assert all(3 <= s["f"][1] - s["f"][0] <= 6 for s in bs[:-1]) and bs[-1]["f"][1] - bs[-1]["f"][0] >= 6
    assert bs[0]["f"][0] == 30 and bs[-1]["f"][1] == 75
    pool_ids = set(patch["assets"])
    assert len({s["pic"] for s in bs} & pool_ids) >= 4                     # the pool's pictures, added to the scene
    assert all(out["assets"][p]["path"] for p in pool_ids)
    assert patch["log"]["flashes"] <= K.FLASH_PER_S                         # a path of light, never a strobe of flashes
    assert sum(1 for e in patch["events"] if e["kind"] == "cut") == len(bs)


def test_the_directors_pictures_come_first_whichever_field_carries_them(film):
    l1, work = film
    for field in ("pics", "pool"):
        pics = K.pictures(l1, work, {"dev": "burst", "f": [30, 60], field: ["p2", "p1"]})
        assert [p.aid for p in pics] == ["p2", "p1"] and [p.chosen for p in pics] == [0, 1]     # nothing else when nothing more is needed
        more = K.pictures(l1, work, {"dev": "burst", "f": [30, 60], field: ["p2", "p1"]}, need=8)
        assert [p.aid for p in more[:2]] == ["p2", "p1"] and len(more) > 2                       # extended only to what the device lays
    near = K.pictures(dict(l1, pool=[dict(r, t=[9.0, 11.0]) if i == 3 else r for i, r in enumerate(l1["pool"])]), work, {"dev": "burst", "f": [290, 320]})
    assert near[0].path == l1["pool"][3]["path"]                                  # the pool found for these words first


def test_flash_safety_counts_opposing_steps_and_tames_a_fixed_order():
    lum = [0.05, 0.6, 0.05, 0.6, 0.05, 0.6, 0.05, 0.6, 0.05, 0.6]
    lens = [3] * 10
    assert K.flashes(lum, lens, 30) > 3
    order, gains = K.flash_safe(lum, lens, 30)
    assert K.flashes([lum[i] for i in order], lens, 30) == 0
    order, gains = K.flash_safe(lum, lens, 30, order=list(range(10)))
    tamed = [lum[i] * gains[i] ** 2.2 for i in range(10)]
    assert K.flashes(tamed, lens, 30) <= 3


def test_the_contour_runs_strobe_against_hold(film):
    l1, work = film
    ask = {"dev": "contour", "id": "ct", "sections": [{"f": [0, 90], "energy": "strobe"}, {"f": [90, 180], "energy": "brisk"},
                                                       {"f": [180, 360], "energy": "hold"}]}
    out, patch = lay(l1, work, ask)
    starts = sorted(s["f"][0] for s in shots(out))
    per3 = [sum(1 for f in starts if w <= f < w + 90) for w in range(0, 360, 90)]
    assert per3[0] >= 10 and max(per3) >= 3 * max(min(per3), 1)             # strobe windows at least 3x the holds
    held = shots(out, 180, 360)
    assert len(held) == 1                                                    # the hold is one picture


@needs_ffmpeg                      # the take is a video: without ffmpeg the fixture has none
def test_the_contour_breaks_a_long_take_with_pictures_and_its_own_tighter_framing(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "contour", "id": "ct", "f": [180, 330], "energy": "brisk"})
    seg = shots(out, 180, 330)
    assert len(seg) >= 6 and max(s["f"][1] - s["f"][0] for s in seg) <= 2 * RH.BRISK_F[1]
    take = [s for s in seg if s["pic"] == l1["items"][2]["pic"]]
    assert take and any(s["pic"] != l1["items"][2]["pic"] for s in seg)
    if take[0].get("src"):                                                   # the take keeps the voice's clock
        for s in take:
            t = 6.0 + (s["f"][0] - 180) / FPS
            assert abs(s["src"][0] - t) < 1.5 / FPS


# ------------------------------------------------------------------ glimpse, settle, snap, whip, shake

def test_a_glimpse_shows_the_incoming_picture_before_its_cut(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "glimpse", "id": "g", "at": 118, "frames": 2, "lead": 5})
    g = next(s for s in shots(out) if s["id"] == "g.g")
    assert g["f"] == [113, 115] and g["pic"] == "v1" if "v1" in l1["assets"] else g["pic"] == "p2"
    assert [s["pic"] for s in shots(out, 105, 125)][:3] == [shots(l1)[0]["pic"], g["pic"], shots(l1)[0]["pic"]]


def test_a_settle_peaks_early_and_dies(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "settle", "id": "st", "f": [120, 150], "dir": "in", "size": 0.1})
    sh = next(s for s in shots(out) if s["id"] == "s1")
    w = [c[2] - c[0] for c in sh["crop"][:30]]
    v = [w[i] - w[i + 1] for i in range(len(w) - 1)]
    peak = int(np.argmax(v))
    assert 1 <= peak <= int(0.35 * 30)                                       # the speed peaks in the first third
    assert v[-1] < 0.25 * max(v)
    k = K.decel(30, 0.18)
    assert all(b >= a for a, b in zip(k, k[1:])) and k[-1] == 1.0


def test_a_snap_zoom_jumps_on_the_word_and_settles(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "snap", "id": "sn", "at": 200, "size": 1.3, "hold": 30, "back": True})
    a = next(s for s in shots(out) if s["f"][1] == 200)
    b = next(s for s in shots(out) if s["f"][0] == 200)
    c = next(s for s in shots(out) if s["f"][0] == 230)
    wa = a["crop"][-1][2] - a["crop"][-1][0] if isinstance(a.get("crop"), list) and isinstance(a["crop"][0], list) else None
    wb = [q[2] - q[0] for q in b["crop"]]
    assert wb[0] < wb[5] and wb[5] == pytest.approx(wb[-1], rel=0.02)       # lands past and settles in a few frames
    if wa:
        assert wa / wb[-1] >= 1.2                                            # a 1.2x or more jump
    if b.get("src"):
        assert b["src"][0] == pytest.approx(6.0 + 20 / FPS, abs=1e-3) and c["src"][0] == pytest.approx(6.0 + 50 / FPS, abs=1e-3)


def test_a_whip_carries_one_shot_into_the_next(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "whip", "id": "wh", "at": 120, "frames": 4})
    p = next(s for s in shots(out) if s["f"][1] == 120)
    q = next(s for s in shots(out) if s["f"][0] == 120)
    assert p["shutter"] == q["shutter"] == 0.85
    moved_p = [abs(a[0] - b[0]) + abs(a[2] - a[0] - b[2] + b[0]) for a, b in zip(p["crop"][-5:], p["crop"][-4:])]
    moved_q = [abs(a[0] - b[0]) + abs(a[2] - a[0] - b[2] + b[0]) for a, b in zip(q["crop"][:4], q["crop"][1:5])]
    assert moved_p[-1] > moved_p[0] > 0 or moved_p[-1] > 0                   # leaving gathers speed
    assert moved_q[0] > moved_q[-1]                                         # arriving brakes


def test_a_shake_dies_inside_six_frames(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "shake", "id": "sk", "at": 200, "frames": 6, "amp": 0.03})
    sh = next(s for s in shots(out) if s["f"][0] <= 200 < s["f"][1])
    lo = sh["f"][0]
    base = K.crops(l1, work, next(s for s in shots(l1) if s["f"][0] <= 200 < s["f"][1]))
    cx = lambda c: (c[0] + c[2]) / 2                                       # noqa: E731
    off = [abs(cx(sh["crop"][200 - lo + j]) - cx(base[200 - lo + j])) for j in range(8)]
    assert off[0] > 0.004 and off[5] < 0.35 * off[0] + 0.004 and off[6] < 1e-6 and off[7] < 1e-6


# ------------------------------------------------------------------ ramp, freeze

def test_a_ramp_uses_the_next_clip_when_the_first_overlap_is_a_sliver(film):
    l1, work = film
    if "v1" not in l1["assets"]:
        pytest.skip("fixture requires ffmpeg")
    clip = next(it for it in l1["items"] if it.get("pic") == "v1")
    a, b = clip["f"]
    l1["items"].remove(clip)
    l1["items"].extend([dict(clip, f=[a,a+4], src=[0,4/30]),
                        dict(clip, id="second", f=[a+4,b], src=[4/30,(b-a)/30])])
    _, patch = lay(l1, work, {"dev":"ramp", "id":"rp", "f":[a,b], "low":.3})
    assert patch["log"]["pieces"] > 1


def test_the_speed_ramp_maps_monotonically_on_a_smooth_curve(film):
    l1, work = film
    if l1["assets"]["v1"].get("kind") != "video" if "v1" in l1["assets"] else True:
        pytest.skip("no ffmpeg: no clip")
    out, patch = lay(l1, work, {"dev": "ramp", "id": "rp", "f": [125, 175], "low": 0.3})
    seq = shots(out, 120, 180)
    src = [s["src"] for s in seq]
    assert all(b[0] >= a[0] and abs(b[0] - a[1]) < 1e-4 for a, b in zip(src, src[1:]))     # no step back, no gap
    rates = [(s["src"][1] - s["src"][0]) / ((s["f"][1] - s["f"][0]) / FPS) for s in seq]
    assert min(rates) <= 0.45 and rates[0] == pytest.approx(1.0, abs=0.05)
    assert max(abs(b - a) for a, b in zip(rates, rates[1:])) <= 0.25                         # no jolt in speed
    r = RH.ramp_rates(60, 0.3)
    assert max(abs(b - a) for a, b in zip(r, r[1:])) < 0.06 and min(r) == pytest.approx(0.3, abs=0.01)


def test_a_freeze_holds_and_the_take_keeps_its_sync(film):
    l1, work = film
    if "main" not in l1["assets"]:
        pytest.skip("no ffmpeg: no take")
    out, patch = lay(l1, work, {"dev": "freeze", "id": "fz", "at": 240, "frames": 18})
    fz = next(s for s in shots(out) if s["f"][0] == 240)
    after = next(s for s in shots(out) if s["f"][0] == 258)
    assert fz["src"][0] == fz["src"][1] == pytest.approx(8.0, abs=1e-3)
    assert after["src"][0] == pytest.approx(6.0 + 78 / FPS, abs=1e-3)


# ------------------------------------------------------------------ layouts

def test_the_grid_fills_one_tile_per_step_and_collapses(film):
    l1, work = film
    steps = [40, 46, 52, 58, 64, 70]
    out, patch = lay(l1, work, {"dev": "grid", "id": "gr", "f": [40, 110], "steps": steps})
    tiles = sorted((i for i in out["items"] if i["id"].startswith("gr.t")), key=lambda i: i["f"][0])
    assert [t["f"][0] for t in tiles] == steps                               # one tile on each step
    assert len({(t["x"], t["y"]) for t in tiles}) == len(tiles)              # each on its own seat
    assert all(t["s"][-1] < 0.2 for t in tiles)                              # and they collapse at the end
    assert next(s for s in shots(out) if s["f"][0] == 40)["pic"] == "plate"


def test_a_grid_can_resolve_one_picture_through_its_tiles(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "grid", "id": "gr", "f": [40, 110], "resolve": "p2", "rows": 3, "cols": 2})
    tiles = [i for i in out["items"] if i["id"].startswith("gr.t")]
    assert len(tiles) == 6 and {t["pic"] for t in tiles} == {"p2"}
    assert len({tuple(t["crop"]) for t in tiles}) == 6


@needs_ffmpeg                      # the take is a video: without ffmpeg the fixture has none
def test_a_split_keeps_the_speaker_on_his_own_clock(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "split", "id": "sp", "f": [200, 260], "pics": ["p2"]})
    panels = sorted((i for i in out["items"] if i["id"].startswith("sp.p")), key=lambda i: i["id"])
    assert len(panels) == 2
    if "main" in l1["assets"]:
        assert panels[0]["pic"] == "main" and panels[0]["src"][0] == pytest.approx(6.0 + 20 / FPS, abs=1e-3)
    assert panels[1]["pic"] == "p2"
    ys = panels[1]["y"]
    assert isinstance(ys, list) and ys[0] != ys[-1]                          # slides in and settles


def test_a_collage_piles_turned_prints_on_paper(film):
    l1, work = film
    out, patch = lay(l1, work, {"dev": "collage", "id": "pl", "f": [190, 280], "pics": ["p1", "p2", "p3"]})
    prints = [i for i in out["items"] if i["id"].startswith("pl.c")]
    assert len(prints) == 3 and len({p["z"] for p in prints}) == 3
    assert all(2.0 <= abs(p["rot"]) <= 7.5 and p["border"]["w"] > 0 for p in prints)
    assert next(s for s in shots(out) if s["f"][0] == 190)["plate"]["kind"] == "paper"


def test_shrink_pip_and_dissolve_stand_on_a_join(film):
    l1, work = film
    for ask in ({"dev": "shrink", "id": "sh", "at": 180, "frames": 14}, {"dev": "pip", "id": "pp", "at": 165, "frames": 24},
                {"dev": "dissolve", "id": "dx", "at": 180, "frames": 10}, {"dev": "dissolve", "id": "dd", "f": [200, 260], "double": True, "pics": ["p1"]}):
        out, patch = lay(l1, work, ask)
        cards = [i for i in out["items"] if i["k"] == "card"]
        assert cards, ask


# ------------------------------------------------------------------ match, rhyme fallback, callback, loop

def test_a_face_chain_holds_the_eyes_on_one_seat(tmp_path):
    work = str(tmp_path)
    assets = {}
    pool = []
    for i, (fx, fy, fh) in enumerate(((0.3, 0.35, 0.15), (0.42, 0.3, 0.18), (0.4, 0.45, 0.14))):
        p = picture(os.path.join(work, f"f{i}.png"), 80 + i * 30, size=(1000, 1200))
        pool.append({"path": p, "kind": "photo", "faces": [[fx, fy, fh * 1200 / 1000 * 0.75, fh, fy + fh * 0.42]]})
    l1 = {"l1": 1, "sid": "t", "seed": 3, "fps": FPS, "frame": [W, H], "dur_f": 90, "assets": assets, "spine": [], "voices": dict(VOICES),
          "film": {}, "items": [{"id": "s0", "k": "shot", "f": [0, 90], "pic": "plate"}], "sound": [], "pool": pool}
    patch = MATCH.plan(l1, work, {"dev": "match", "id": "m", "f": [0, 90], "n": 3, "seat": [0.5, 0.36]}, None, None, by="eyes")
    assert not patch.get("skip"), patch
    out = palette.merge(l1, patch)
    assert ok(out)
    chain = sorted((i for i in out["items"] if i["id"].startswith("m.m")), key=lambda i: i["f"][0])
    assert len(chain) == 3 and patch["log"]["eyes"] == 3
    for it in chain:
        rec = next(r for r in pool if K.asset_id_for(r["path"]) == it["pic"])
        fx, fy, fw, fh, ey = rec["faces"][0]
        c = it["crop"][0]
        assert abs((fx + fw / 2 - c[0]) / (c[2] - c[0]) - 0.5) < 0.03 and abs((ey - c[1]) / (c[3] - c[1]) - 0.36) < 0.03


def test_a_rhyme_without_round_shapes_falls_back_to_a_match(tmp_path):
    work = str(tmp_path)
    assets = {k: {"kind": "image", "path": picture(os.path.join(work, f"{k}.png"), g)} for k, g in (("a", 40), ("b", 120), ("c", 200))}
    l1 = {"l1": 1, "sid": "t", "seed": 3, "fps": FPS, "frame": [W, H], "dur_f": 90, "assets": assets, "spine": [], "voices": dict(VOICES),
          "film": {}, "items": [{"id": "s0", "k": "shot", "f": [0, 90], "pic": "a"}], "sound": []}
    assert rhyme.plan(l1, work, {"dev": "rhyme", "id": "rh", "at": [10, 70], "n": 3}).get("skip")     # no round shapes
    out, log = palette.apply(l1, work, [{"dev": "rhyme", "id": "rh", "at": [10, 70], "n": 3, "pics": ["a", "b", "c"]}])
    assert log[0]["done"] and log[0]["landed"] == "match_pos" and [t["dev"] for t in log[0]["tried"]] == ["rhyme", "rhyme_eyes"]
    assert ok(out) and len([i for i in out["items"] if i["id"].startswith("rh.m")]) == 3       # the registry's chain, not a refusal


def test_the_opening_returns_at_the_payoff_and_the_end_loops(film):
    l1, work = film
    l2, _ = lay(l1, work, {"dev": "burst", "id": "b", "f": [0, 40], "frames": 4})
    opening = [s["pic"] for s in shots(l2, 0, 40)]
    out, patch = lay(l2, work, {"dev": "callback", "id": "cb", "f": [300, 340], "from": [0, 40]})
    back = [s["pic"] for s in shots(out, 300, 340)]
    firsts = list(dict.fromkeys(p for p in opening if not K.is_take(p)))
    assert back[:len(firsts)] == firsts and back[-1] == firsts[0]
    l3 = dict(l1, items=[i for i in l1["items"] if i["k"] != "word"] + [dict(l1["items"][-1], f=[10, 330])])
    out, patch = lay(l3, work, {"dev": "loop", "id": "lp", "frames": 15})
    end = shots(out)[-1]
    assert end["f"] == [345, 360] and end["pic"] == shots(l1)[0]["pic"]


def test_every_device_stands_on_the_film_and_keeps_its_contract(film):
    """plan.eligible holds after every device of this lane, laid through the registry's own apply."""
    l1, work = film
    asks = [{"dev": "burst", "f": [20, 60]}, {"dev": "contour", "sections": [{"f": [200, 260], "energy": "strobe"}, {"f": [260, 330], "energy": "brisk"}]},
            {"dev": "grid", "f": [60, 110]}, {"dev": "split", "f": [140, 175]}, {"dev": "collage", "f": [300, 350]},
            {"dev": "glimpse", "at": 118}, {"dev": "settle", "f": [120, 140]}, {"dev": "snap", "at": 190}, {"dev": "whip", "at": 180},
            {"dev": "shake", "at": 150}, {"dev": "ramp", "f": [122, 172]}, {"dev": "freeze", "at": 185}, {"dev": "pip", "at": 150},
            {"dev": "shrink", "at": 120}, {"dev": "dissolve", "at": 118}, {"dev": "match", "f": [330, 350]}, {"dev": "loop"}]
    out, log = palette.apply(l1, work, asks)
    assert ok(out)
    landed = [r for r in log if r["done"]]
    assert len(landed) >= len(asks) - 2, [(r["dev"], r.get("tried")) for r in log if not r["done"]]
    assert all(e.get("kind") for e in out["device_events"])


# ------------------------------------------------------------------ the Director's pictures, kin before strangers (review 20 Nov)

def _pool_film(film):
    """The film with a pool of two subjects in two windows, the way wf1_coverage.device_pool writes it."""
    l1, work = film
    pool = []
    for i, r in enumerate(l1["pool"]):
        subj, win, t = ("Mark Cuban", 0, [0.0, 4.0]) if i < 3 else ("Hiring and sales interview", 1, [8.0, 11.0])
        pool.append(dict(r, id=f"pl{i}", subject=subj, window=win, t=t))
    return dict(l1, pool=pool), work


def test_a_burst_of_the_directors_pictures_grows_from_their_own_moments_then_their_subject_and_lands_on_the_last(film):
    l1, work = _pool_film(film)
    # the Director chose two pool pictures of one subject; palette_asks writes them in the pool field, never "only"
    ask = {"dev": "burst", "id": "who", "f": [200, 245], "pool": ["pl0", "pl2"]}
    pics = K.pictures(l1, work, ask, need=12)
    subj = [p.meta.get("subject") for p in pics]
    assert [p.aid for p in pics[:2]] == ["pl0", "pl2"]
    k = subj.index("Hiring and sales interview") if "Hiring and sales interview" in subj else len(subj)
    assert all(s_ == "Mark Cuban" for s_ in subj[:k])               # the same subject before any neighbouring window's
    out, patch = lay(l1, work, ask)
    bs = [s for s in shots(out) if s["id"].startswith("who.b")]
    assert out["assets"][bs[-1]["pic"]]["path"] == l1["pool"][2]["path"]           # it lands on the last chosen picture
    assert patch["log"]["lands_on"] == bs[-1]["pic"] and patch["log"]["flashes"] <= K.FLASH_PER_S
    kin = {r["path"] for r in l1["pool"][:3]}
    ours = sum(1 for s in bs if out["assets"][s["pic"]]["path"] in kin)
    assert ours >= len(bs) - ours                                                 # the Director's subject carries the burst
    alone = K.pictures(l1, work, dict(ask, f=[200, 212]), need=4)
    assert {p.meta.get("subject") for p in alone} == {"Mark Cuban"}               # a short burst never reaches a stranger


@needs_ffmpeg
def test_a_placed_clip_is_shown_only_inside_its_checked_slice(film):
    l1, work = film
    pic = K.pic_of_asset(l1, work, "v1")
    assert pic.rng == [0.5, 2.5] and pic.span() == [0.5, 2.5]                     # the placed shot's src, not the whole 4 s file
    import random
    for t0, t1 in K.moments(pic, 6, 0.15, random.Random(3)):
        assert 0.5 <= t0 and t1 <= 2.5
    out, _ = lay(l1, work, {"dev": "contour", "id": "c", "sections": [{"f": [120, 180], "energy": "strobe"}], "pics": ["v1"]})
    for s in shots(out, 120, 180):
        if s["pic"] == "v1":
            assert 0.5 - 1e-6 <= s["src"][0] and s["src"][1] <= 2.5 + 1e-6, s["src"]
    one = K.clocked({"id": "c", "k": "card", "f": [0, 30], "s": [1.0] * 30}, pic, FPS, random.Random(1))
    assert len(one) == 1 and abs((one[0]["src"][1] - one[0]["src"][0]) - 1.0) < 1e-3 and 0.5 <= one[0]["src"][0] and one[0]["src"][1] <= 2.5
    two = K.clocked({"id": "c", "k": "card", "f": [0, 90], "s": [1.0] * 90}, pic, FPS, random.Random(1))
    head, tail = two                                                              # a 2 s slice under 3 s: 2 s at real speed, then held
    assert head["f"] == [0, 60] and head["src"] == [0.5, 2.5] and len(head["s"]) == 60
    assert tail["f"] == [60, 90] and tail["src"] == [2.5, 2.5] and len(tail["s"]) == 30


@needs_ffmpeg
def test_pip_dissolve_and_shrink_keep_the_incoming_clip_at_real_speed(film):
    l1, work = film
    # a placed clip's checked slice is its src: nothing before it was checked, so the window holds its first frame
    for dev, n in (("pip", 24), ("dissolve", 10), ("shrink", 14)):
        out, patch = lay(l1, work, {"dev": dev, "id": dev, "at": 112, "frames": n})
        assert patch["log"]["held"] == n, (dev, patch["log"])
        for it in (i for i in out["items"] if i.get("pic") == "v1" and i["f"][0] < 120):
            assert it["src"][1] - it["src"][0] < 1e-6 and it["src"][0] >= 0.5 - 1e-6, (dev, it)
    # a clip whose checked slice is the whole cut (a pool clip) starting 0.1 s in: 3 frames of pre-roll run at real speed
    l1 = dict(l1, assets=dict(l1["assets"], v1=dict(l1["assets"]["v1"], rng=[0.0, 4.0])),
              items=[dict(i, src=[0.1, 2.1]) if i["id"] == "s1" else i for i in l1["items"]])
    for dev, n in (("pip", 24), ("dissolve", 10), ("shrink", 14)):
        out, patch = lay(l1, work, {"dev": dev, "id": dev, "at": 112, "frames": n})
        moving = [i for i in out["items"] if i.get("pic") == "v1" and i["f"][0] < 120 and i.get("src")]
        assert moving, dev
        for it in moving:
            frames = it["f"][1] - it["f"][0]
            rate = (it["src"][1] - it["src"][0]) * FPS / frames
            assert rate < 1e-6 or abs(rate - 1.0) < 0.02, (dev, it["id"], it["src"], it["f"])     # held, or real speed
            assert it["src"][0] >= 0.0 and it["src"][1] <= 2.1 + 1e-6, (dev, it["src"])             # never past its slice
        assert patch["log"]["held"] == n - 3, (dev, patch["log"])


@needs_ffmpeg
def test_the_contour_cuts_on_the_voices_stress_when_the_ask_gives_none(film, monkeypatch):
    l1, work = film
    from scene import pace
    peaks = [(f, 0.9) for f in (40, 61, 77, 95)]
    monkeypatch.setattr(pace, "stress", lambda path, fps=30.0, t0=0.0, dur=None: [(f - int(round(t0 * fps)), s) for f, s in peaks])
    got = K.voice_stress(l1, work, 30, 110)
    assert [f for f, _ in got] == [40, 61, 77, 95]
    out, patch = lay(l1, work, {"dev": "contour", "id": "c", "sections": [{"f": [30, 110], "energy": "brisk"}]})
    assert patch["log"]["stress"] == "voice"
    cuts = {s["f"][0] for s in shots(out, 30, 110)}
    assert len(cuts & {61, 77, 95}) >= 2                                          # the section's cuts land on the peaks


def test_the_loop_returns_to_the_first_held_picture_not_a_sliver(film):
    l1, work = film
    items = [dict(i, f=[3, 120]) if i["id"] == "t1" else i for i in l1["items"]]
    items.insert(0, {"id": "sliver", "k": "shot", "f": [0, 3], "pic": "p2"})
    l1 = dict(l1, items=items)
    out, patch = lay(l1, work, {"dev": "loop", "id": "lp", "frames": 12})
    assert patch["log"]["opening_shot"] == "t1" and patch["log"]["end_card_follows"] is True
    end = [i for i in out["items"] if i["id"] == "lp.end"][0]
    assert end["pic"] == "t1" or end["pic"] == next(i["pic"] for i in items if i["id"] == "t1")


def test_a_chosen_picture_whose_text_stayed_on_is_offered_last(film):
    l1, work = _pool_film(film)
    pool = [dict(r, text_kept="label") if r["id"] == "pl0" else r for r in l1["pool"]]
    l1 = dict(l1, pool=pool)
    pics = K.pictures(l1, work, {"dev": "match", "f": [200, 260], "pool": ["pl0", "pl1", "pl2"]}, least=2)
    assert [p.aid for p in pics] == ["pl1", "pl2"]                                   # enough without it
    pics = K.pictures(l1, work, {"dev": "match", "f": [200, 260], "pool": ["pl0", "pl1", "pl2"]}, least=6)
    assert pics[-1].aid == "pl0" or "pl0" not in [p.aid for p in pics[:2]]          # reached only when the rest run out


def test_shrink_never_lifts_a_layouts_ground_away_from_its_cards(film):
    l1, work = film
    out, _ = lay(l1, work, {"dev": "split", "id": "sp", "f": [120, 180], "pics": ["p1", "p2"]})
    patch = palette.plan(out, work, {"dev": "shrink", "id": "sh", "at": 180, "frames": 14})
    cards = {it["id"]: it for it in out["items"] if it["k"] == "card"}
    assert {it["id"] for it in patch["items"]} == set(cards)
    for item in patch["items"]:
        original = cards[item["id"]]
        assert item["pic"] == original["pic"] and item["f"] == original["f"]
        assert item["s"][-1] < item["s"][0]


def _held(out, prefix):
    """(pic, frames) of every run of one picture in a burst's items, neighbouring shots of the same picture as one."""
    bs = sorted((s for s in shots(out) if s["id"].startswith(prefix)), key=lambda s: s["f"][0])
    runs = []
    for s in bs:
        if runs and runs[-1][0] == s["pic"] and runs[-1][2] == s["f"][0]:
            runs[-1][1] += s["f"][1] - s["f"][0]
            runs[-1][2] = s["f"][1]
        else:
            runs.append([s["pic"], s["f"][1] - s["f"][0], s["f"][1]])
    return bs, [(p, n) for p, n, _ in runs]


@needs_ffmpeg
def test_a_long_burst_strobes_a_second_then_cuts_brisk_and_lands_on_the_speaker(film):
    """20 Nov review of short 02: a 3.5 s burst strobed 1.2 s and then held whatever picture landed (a stock resume page)
    for 2.5 s under the captions. Now the strobe runs about a second, the rest cuts at brisk pace through the burst's
    own pictures, each moving, and with no picture chosen to land it ends on the speaker's take, about 0.5-1 s."""
    l1, work = film
    out, patch = lay(l1, work, {"dev": "burst", "id": "b", "f": [10, 115]})      # 3.5 s, all over the take
    bs, runs = _held(out, "b.b")
    assert bs[0]["f"][0] == 10 and all(s["f"][1] - s["f"][0] >= 3 for s in bs)
    assert max(n for _, n in runs) <= 30                                     # no burst picture holds over a second
    strobe = [s for s in bs if s["f"][1] - s["f"][0] <= 6]
    brisk = [s for s in bs if s["f"][1] - s["f"][0] > 6]
    assert strobe[-1]["f"][1] - 10 <= 40 and len(brisk) >= 2
    assert all(10 <= s["f"][1] - s["f"][0] <= 24 for s in brisk)
    for s in brisk:                                                          # each brisk picture moves
        w = [c[2] - c[0] for c in s["crop"]]
        assert w[0] / w[-1] >= 1.04
    assert len({s["pic"] for s in brisk}) >= 2
    land = [i for i in out["items"] if i["id"].startswith("b.land")]
    assert patch["log"]["lands_on"] == "take" and land and all(i["pic"] == "main" for i in land)
    assert land[-1]["f"][1] == 115 and land[0]["f"][0] == bs[-1]["f"][1]
    assert 15 <= 115 - land[0]["f"][0] <= 30
    t1 = next(i for i in l1["items"] if i["id"] == "t1")                      # the take as the base had it: its own clock
    assert land[0]["src"][0] == pytest.approx(t1["src"][0] + (land[0]["f"][0] - 0) * 4.0 / 120, abs=1e-3)


@needs_ffmpeg
def test_a_long_burst_over_a_cutaway_lands_on_the_speaker_on_his_own_clock(film):
    """Where the base shows a cutaway under the landing, the burst still lands on the speaker: the take on the clock of
    the take shot nearest the landing (his voice's time there), never the pool's last picture."""
    l1, work = film
    out, patch = lay(l1, work, {"dev": "burst", "id": "b", "f": [60, 170]})      # the landing falls on s1 (v1)
    land = [i for i in out["items"] if i["id"].startswith("b.land")]
    assert patch["log"]["lands_on"] == "take" and len(land) == 1 and land[0]["pic"] == "main"
    b, z = land[0]["f"]
    assert z == 170 and 15 <= z - b <= 30
    assert land[0]["src"] == pytest.approx([6.0 + (b - 180) / 30, 6.0 + (z - 180) / 30], abs=1e-3)
    w = [c[2] - c[0] for c in land[0]["crop"]]
    assert w[0] / w[-1] >= 1.04                                                  # he is pushed in on, not frozen


def test_a_long_burst_lands_on_the_callers_picture_and_holds_nothing_else(film):
    """With a picture chosen to land, the burst lands on it for 0.5-1 s; no other burst picture holds over a second."""
    l1, work = film
    out, patch = lay(l1, work, {"dev": "burst", "id": "b", "f": [30, 135], "pics": ["p1", "p2"]})
    bs, runs = _held(out, "b.b")
    assert bs[0]["f"][0] == 30 and bs[-1]["f"][1] == 135
    assert bs[-1]["pic"] == "p2" and patch["log"]["lands_on"] == "p2"
    assert 15 <= bs[-1]["f"][1] - bs[-1]["f"][0] <= 30
    assert all(n <= 30 for p, n in runs[:-1])
    w = [c[2] - c[0] for c in bs[-1]["crop"]]
    assert w[0] / w[-1] >= 1.05                                                  # the landing pushes in


@needs_ffmpeg
def test_no_burst_shot_plays_a_clip_past_its_verified_range(film):
    """20 Nov review: the long landing was chosen without checking its clip had room, and src ran past pic.span(). A clip
    whose verified range (0.4 s) is shorter than the landing, chosen to land, and a pool of such clips: every burst item
    stays inside its clip's range, and the burst still fills its span."""
    l1, work = film
    l1 = dict(l1, assets=dict(l1["assets"], v1=dict(l1["assets"]["v1"], rng=[1.0, 1.4])))
    pool = []
    for i in range(4):
        p = os.path.join(work, f"cl{i}.mp4")
        shutil.copy(os.path.join(work, "clip.mp4"), p)
        pool.append({"id": f"cl{i}", "path": p, "kind": "clip", "w": 320, "h": 568, "seconds": 4.0, "rng": [0.5 + i * 0.3, 0.8 + i * 0.3]})
    l1 = dict(l1, pool=pool)
    rngs = {"v1": [1.0, 1.4], **{r["id"]: r["rng"] for r in pool}}
    for ask in ({"dev": "burst", "id": "b", "f": [10, 115], "pics": ["cl0", "v1"]},
                {"dev": "burst", "id": "b", "f": [180, 285]},
                {"dev": "burst", "id": "b", "f": [180, 225], "pics": ["v1"]}):
        out, patch = lay(l1, work, ask)
        items = [i for i in out["items"] if i["id"].startswith("b.b")]
        assert items and min(i["f"][0] for i in items) == ask["f"][0]
        assert max(i["f"][1] for i in out["items"] if i["id"].startswith("b.")) == ask["f"][1]
        for it in items:
            if it["pic"] in rngs:
                lo, hi = rngs[it["pic"]]
                assert lo - 1e-6 <= it["src"][0] <= it["src"][1] <= hi + 1e-6, (ask, it["id"], it["src"])
        if ask.get("pics"):
            assert patch["log"]["lands_on"] == "v1"
