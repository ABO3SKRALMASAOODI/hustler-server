"""worker/scene/alien: the five devices on pictures made here (discs, a talking plate, a script's marks).
No GPU: a device gives back scene items, and the engine's own ``eligible`` says whether the scene still holds.
What the devices look like on real films is in .valmera/week-1112/side-by-side/S/."""
import ast
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

import scene.segments as SEG
import version
from scene import alien
from scene.alien import answer, arc, diecut, rhyme, sense
from scene.plan import eligible

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
FPS, W, H = 30, 540, 960
VOICE = {"cap": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 0}}
try:
    import uharfbuzz  # noqa: F401
    HB = True
except Exception:      # noqa: BLE001
    HB = False
needs_hb = pytest.mark.skipif(not HB, reason="uharfbuzz is not installed")
needs_ffmpeg = pytest.mark.skipif(not shutil.which(SEG.FF), reason="no ffmpeg on this machine")


def disc(path, cx, cy, r, size=(360, 640), dark=False):
    """A picture with one round thing on it, and noise so nothing else is an edge worth a vote."""
    import cv2
    w, h = size
    rng = np.random.RandomState(int(cx * 1000 + r))
    im = (rng.rand(h, w, 3) * 18 + (150 if dark else 60)).astype(np.uint8)
    cv2.circle(im, (int(cx * w), int(cy * h)), int(r * w), (40, 40, 40) if dark else (230, 225, 210), -1, cv2.LINE_AA)
    cv2.imwrite(path, im)


def clip(path, n=24, size=(360, 640), move=0.004):
    """A video of a disc drifting to the right: something to follow."""
    import cv2
    w, h = size
    p = subprocess.Popen([SEG.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", "30", "-i", "-",
                          "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", path], stdin=subprocess.PIPE)
    for i in range(n):
        im = np.full((h, w, 3), 70, np.uint8)
        cv2.circle(im, (int((0.45 + move * i) * w), int(0.5 * h)), int(0.2 * w), (235, 235, 225), -1, cv2.LINE_AA)
        p.stdin.write(im.tobytes())
    p.stdin.close()
    p.wait()


def scene_of(items, dur, assets=None, film=None):
    return {"l1": 1, "sid": "t", "seed": 7, "fps": FPS, "frame": [W, H], "dur_f": dur, "assets": assets or {}, "spine": [],
            "voices": dict(VOICE), "film": film if film is not None else {}, "items": items, "sound": []}


def test_the_bank_imports_no_model_code():
    """worker/scene/alien reads no model, like the engine it lives in: the caller hands in every meaning."""
    banned = {"llm", "anthropic", "openai", "agent_loop", "agent_tools", "agent_prompt", "claude_lane", "score", "director", "litellm",
              "onnxruntime", "torch", "mediapipe"}
    root = os.path.join(WORKER, "scene", "alien")
    seen = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(open(os.path.join(root, name), encoding="utf-8").read())):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            seen += [(name, m) for m in mods if m.split(".")[0] in banned]
    assert not seen, seen


def test_the_bank_is_inside_the_code_fingerprint():
    rel = {os.path.relpath(p, WORKER).replace(os.sep, "/") for p in version._source_files()}
    for need in ("scene/alien/__init__.py", "scene/alien/rhyme.py", "scene/alien/diecut.py", "scene/alien/lamp.py", "scene/alien/arc.py",
                 "scene/alien/answer.py", "scene/alien/sense.py", "scene/alien/glyphs.py"):
        assert need in rel, need


def test_menu_and_a_device_that_cannot_stand_leaves_the_film():
    assert set(alien.DEVICES) == {"rhyme", "diecut", "lamp", "arc", "answer"}
    assert len(alien.menu().splitlines()) == 5
    base = scene_of([{"id": "s0", "k": "shot", "f": [0, 60], "pic": "plate", "plate": {"c0": "#101010", "c1": "#101010"}}], 60)
    out, log = alien.apply(base, "/nonexistent", [{"dev": "rhyme", "at": [0, 60], "n": 3}, {"dev": "nothing"}, {"dev": "lamp", "at": [5, 40], "t": "X"},
                                                 {"dev": "answer", "at": [0, 60], "picks": [{"f": 3, "pic": "gone"}]}])
    assert [r["done"] for r in log] == [False] * 4 and all(r["why"] for r in log)
    assert out["items"] == base["items"] and "alien" not in out


# ------------------------------------------------------------------ rhyme

def test_support_tells_a_disc_from_an_arc(tmp_path):
    import cv2
    disc(str(tmp_path / "d.png"), 0.5, 0.5, 0.25)
    g = cv2.imread(str(tmp_path / "d.png"), cv2.IMREAD_GRAYSCALE)
    g = cv2.resize(g, rhyme._size(g.shape[1], g.shape[0]))
    found, G = rhyme.circles(g)
    assert found and found[0][3] > 0.8
    cx, cy, r = found[0][:3]
    assert abs(cx / g.shape[1] - 0.5) < 0.02 and abs(r / g.shape[1] - 0.25) < 0.02
    half = g.copy()
    half[:int(cy)] = half[2, 2]                                # the top half gone: an arc
    assert float(rhyme.support(rhyme._grad(half), cx, cy, r)[0]) < 0.5 * found[0][3]


def test_a_chain_stands_on_one_seat_and_grows_through_every_cut(tmp_path):
    spec = {"a": (0.50, 0.40, 0.20), "b": (0.44, 0.52, 0.24), "c": (0.58, 0.45, 0.19)}
    assets = {}
    for k, (cx, cy, r) in spec.items():
        disc(str(tmp_path / f"{k}.png"), cx, cy, r, dark=(k == "b"))
        assets[k] = {"kind": "image", "path": f"{k}.png"}
    l1 = scene_of([{"id": "s0", "k": "shot", "f": [0, 90], "pic": "a"}], 90, assets)
    out, log = alien.apply(l1, str(tmp_path), [{"dev": "rhyme", "id": "rh", "at": [20, 62], "n": 3}])
    assert log[0]["done"], log
    ok, why = eligible(out)
    assert ok, why
    shots = sorted((i for i in out["items"] if i["id"].startswith("rh.r")), key=lambda i: i["f"][0])
    assert [s["f"] for s in shots] == [[20, 34], [34, 48], [48, 62]] and len({s["pic"] for s in shots}) == 3
    seat, radii = log[0]["seat"], log[0]["seat"]["r"]
    at = 0
    for s in shots:
        cx, cy, r = spec[s["pic"]]
        for j, c in enumerate(s["crop"]):
            cw = c[2] - c[0]
            assert abs((cx - c[0]) / cw - seat["x"]) < 0.012 and abs((cy - c[1]) / (c[3] - c[1]) - seat["y"]) < 0.012     # the centre, on the seat
            assert abs(r / cw - radii[at + j]) < 0.012                                                                      # the radius, to the pixel
            assert -0.005 <= c[0] and c[2] <= 1.005 and -0.005 <= c[1] and c[3] <= 1.005
        at += len(s["crop"])
    assert all(b >= a for a, b in zip(radii, radii[1:])) and radii[-1] > radii[0] * 1.03      # one push through all three
    assert all(s["treat"] == {"mode": "mono"} for s in shots)                                 # three pictures: one silver


@needs_ffmpeg
def test_a_shape_is_followed_inside_a_moving_clip(tmp_path):
    clip(str(tmp_path / "v.mp4"))
    disc(str(tmp_path / "a.png"), 0.5, 0.45, 0.22)
    assets = {"v": {"kind": "video", "path": "v.mp4"}, "a": {"kind": "image", "path": "a.png"}}
    l1 = scene_of([{"id": "s0", "k": "shot", "f": [0, 60], "pic": "a"}], 60, assets)
    out, log = alien.apply(l1, str(tmp_path), [{"dev": "rhyme", "id": "rh", "at": [10, 38], "n": 2, "order": ["a", "v"], "mono": False}])
    assert log[0]["done"], log
    row = next(c for c in log[0]["log"]["chain"] if c["pic"] == "v")
    assert row["tracked"] >= row["of"] - 2 and not row["held"]
    v = next(i for i in out["items"] if i.get("pic") == "v")
    xs = [c[0] for c in v["crop"]]
    assert xs[-1] - xs[0] > 0.02            # the crop travels with the disc, so the disc does not travel on the frame
    assert "treat" not in v


# ------------------------------------------------------------------ diecut

@needs_hb
def test_diecut_letters_carry_the_pixels_that_were_under_them(tmp_path):
    disc(str(tmp_path / "p.png"), 0.5, 0.3, 0.2)
    crops = [[0.0, 0.0 + 0.001 * i, 1.0, 0.9 + 0.001 * i] for i in range(60)]          # a camera move under the word
    l1 = scene_of([{"id": "s0", "k": "shot", "f": [0, 60], "pic": "p", "crop": crops}], 60, {"p": {"kind": "image", "path": "p.png"}})
    out, log = alien.apply(l1, str(tmp_path), [{"dev": "diecut", "id": "dc", "at": [10, 50], "t": "GONE", "seat": [0.5, 0.7]}])
    assert log[0]["done"], log
    ok, why = eligible(out)
    assert ok, why
    joins = sorted((i for i in out["items"] if i["k"] == "join"), key=lambda i: i["f"][0])
    assert joins and all(j["mode"] == "glyph" and j["stage"] == "top" and j["pic"] == "p" and j["t"] == "GONE" for j in joins)
    assert [j["f"][0] for j in joins][0] == 10 and joins[-1]["f"][1] == 50 and all(a["f"][1] == b["f"][0] for a, b in zip(joins, joins[1:]))
    first, last = joins[0], joins[-1]
    assert first["a"] == 0.0 and last["a"] == 0.0                                 # seated on its first and last frame: the picture is whole
    c0 = first["crop"] if not isinstance(first["crop"][0], list) else first["crop"][0]
    assert np.allclose(c0, crops[10], atol=1e-5)                                  # at rest the letters show exactly the shot's own crop
    held = max(joins, key=lambda j: j["f"][1] - j["f"][0])
    assert isinstance(held["crop"][0], list) and len(held["crop"]) == held["f"][1] - held["f"][0]
    assert held["crop"][1][1] - held["crop"][0][1] == pytest.approx(0.001 / held["s"], abs=2e-4)   # and follow the camera on every frame
    assert held["x0"] < first["x0"] and held["y0"] < first["y0"] and held["dim"] < 1.0 and held["grade"]["gain"] > 1.0
    hole = next(i for i in out["items"] if i["id"] == "dc.hole")
    assert hole["k"] == "word" and hole["x"] == first["x0"] and hole["y"] == first["y0"]
    assert out["voices"]["alien.dc"]["track"] == 0


@needs_hb
def test_diecut_seat_stays_off_a_face(tmp_path):
    import cv2
    im = np.full((640, 360, 3), 40, np.uint8)
    im[:330] = 200                                                               # the bright half is where the face is
    cv2.imwrite(str(tmp_path / "p.png"), im)
    l1 = scene_of([{"id": "s0", "k": "shot", "f": [0, 40], "pic": "p"}], 40, {"p": {"kind": "image", "path": "p.png"}})
    free = diecut.seat_of(l1, str(tmp_path), 0, 40, (0.8, 0.12), None)
    kept = diecut.seat_of(l1, str(tmp_path), 0, 40, (0.8, 0.12), [[0.3, 0.1, 0.4, 0.36]])
    assert free[1] - 0.12 < 0.46 <= kept[1] - 0.12          # free, it takes the bright band; with a face there, the first band clear of it


# ------------------------------------------------------------------ arc

def arc_scene():
    shots = [{"id": f"s{i}", "k": "shot", "f": [i * 30, i * 30 + 30], "pic": "p"} for i in range(8)]
    return scene_of(shots, 240, {"p": {"kind": "image", "path": "p.png"}}, {"look": "story_low", "grain": 0.014})


def test_arc_follows_the_year_and_the_stakes(tmp_path):
    disc(str(tmp_path / "p.png"), 0.5, 0.5, 0.2)
    marks = [{"f": 0, "year": 2026, "stakes": 0.0}, {"f": 44, "stakes": 0.0}, {"f": 45, "year": 1927, "stakes": -0.2}, {"f": 150, "stakes": -1.0},
             {"f": 152, "stakes": 0.6}, {"f": 200, "year": 2006, "stakes": 0.6}]
    out, log = alien.apply(arc_scene(), str(tmp_path), [{"dev": "arc", "id": "arc", "marks": marks}])
    assert log[0]["done"], log
    ok, why = eligible(out)
    assert ok, why
    shots = sorted((i for i in out["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    at = lambda f: next(s for s in shots if s["f"][0] <= f < s["f"][1])      # noqa: E731
    assert "look" not in at(10)                                               # the film's own time, nothing at stake: untouched
    assert {s["f"][0] for s in shots} >= {45, 47, 49, 51, 200, 202}           # the year changes on its frame and comes up in steps
    ramp = [at(f)["look"]["sat"] for f in (45, 47, 49, 51, 70)]
    assert ramp == sorted(ramp, reverse=True) and ramp[-1] == 0.0 and ramp[0] > 0.3       # colour drains over six frames to silver
    assert at(70)["look"]["skin"] == 0.0 and at(70)["look"]["black"] >= 0.035
    assert isinstance(at(100)["crop"], list)                                  # 1927: the gate weaves
    assert at(140)["look"]["drop"] < at(70)["look"]["drop"] <= 1.0            # as things sink the room comes down round the face
    assert at(215)["look"]["sat"] > 0.8 and "crop" not in at(215)             # home: colour, and a steady gate
    bars = [i for i in out["items"] if i["id"].startswith("arc.bar")]
    assert len(bars) == 2
    top = bars[0]
    h = [r[3] for r in top["rect"]]
    lo = top["f"][0]
    assert max(h) == pytest.approx(0.085, abs=0.01) and h[149 - lo] > 0.06 and (153 >= top["f"][1] or h[153 - lo] < 0.01)   # open in two frames on the turn
    assert all(b - a <= 0.0017 for a, b in zip(h, h[1:]))                     # and never close faster than the eye forgives
    fl = next(i for i in out["items"] if i["id"] == "arc.lampf")
    assert fl["f"][0] == 45 and max(fl["a"]) <= 0.11


def test_arc_begins_in_its_first_year_without_a_ramp(tmp_path):
    disc(str(tmp_path / "p.png"), 0.5, 0.5, 0.2)
    out, log = alien.apply(arc_scene(), str(tmp_path), [{"dev": "arc", "id": "arc", "marks": [{"f": 0, "year": 1973, "stakes": -0.5}]}])
    shots = [i for i in out["items"] if i["k"] == "shot"]
    assert len(shots) == 8 and log[0]["log"]["changes"] == []
    assert all(0.4 < s["look"]["sat"] < 0.7 for s in shots)
    assert arc.era(2015) is None and arc.era(1927)["sat"] == 0.0 and 0.4 < arc.era(1968)["sat"] < 0.72


# ------------------------------------------------------------------ answer

@needs_hb
def test_answer_pictures_are_born_in_their_words_and_tile_the_frame(tmp_path):
    assets = {}
    for k in "abc":
        disc(str(tmp_path / f"{k}.png"), 0.5, 0.5, 0.2, dark=(k == "b"))
        assets[k] = {"kind": "image", "path": f"{k}.png"}
    l1 = scene_of([{"id": "s0", "k": "shot", "f": [0, 120], "pic": "a"}], 120, assets)
    picks = [{"f": 20, "pic": "b", "word": "FUEL"}, {"f": 50, "pic": "c", "word": "VEGAS"}, {"f": 52, "pic": "a", "word": "NO"}]
    out, log = alien.apply(l1, str(tmp_path), [{"dev": "answer", "id": "an", "at": [0, 100], "picks": picks}])
    assert log[0]["done"], log
    ok, why = eligible(out)
    assert ok, why
    rows = log[0]["log"]["picks"]
    assert [r["word"] for r in rows] == ["FUEL", "VEGAS"]                    # a word two frames after another keeps that one's picture
    for r in rows:
        assert r["lands"] - r["onset"] >= answer.OPEN                        # long enough to be read as a window
    j = next(i for i in out["items"] if i["id"] == "an.w0")
    sh = next(i for i in out["items"] if i["id"] == "an.p0")
    assert j["k"] == "join" and j["mode"] == "glyph" and j["t"] == "FUEL" and j["f"][1] == sh["f"][0] and j["pic"] == sh["pic"] == "b"
    assert j["crop"][-1][2] - j["crop"][-1][0] > sh["crop"][0][2] - sh["crop"][0][0]      # one push, carried from the letters into the frame
    assert j["dim"][-1] < 0.5 and j["grade"]["gain"] >= 1.0
    assert next(i for i in out["items"] if i["id"] == "an.p1")["f"][1] == 100
    assert any(i["id"] == "s0~z" and i["f"] == [100, 120] for i in out["items"])          # the film's own picture comes back after the run


def test_the_voice_decides_the_frame(tmp_path):
    lev = np.zeros(60, np.float32)
    lev[20:24] = [0.1, 0.2, 0.9, 1.0]
    assert sense.peak_near(lev, 19, back=0, ahead=6) == 22                    # the frame the level rises most: the attack
    env = sense.envelope(np.array([0, 0, 1, 1, 0, 0, 0, 0, 0, 0], np.float32))
    assert env[2] > 0.5 and env[5] > env[8] > 0.0 and env[9] < env[4]         # up in a frame, down over several


# ------------------------------------------------------------------ lamp

@needs_hb
@needs_ffmpeg
def test_lamp_relights_the_take_and_leaves_the_rest(tmp_path):
    import cv2
    from scene.alien import lamp
    clip(str(tmp_path / "take.mp4"), n=45, size=(270, 480), move=0.0)
    l1 = scene_of([{"id": "s0", "k": "shot", "f": [0, 45], "pic": "main", "src": [0.0, 1.5]}], 45, {"main": {"kind": "video", "path": "take.mp4"}})
    l1["frame"] = [270, 480]
    ask = {"dev": "lamp", "id": "lp", "at": [10, 30], "t": "ON", "ink": "#FF2010", "seat": [0.5, 0.2], "tail": 6}
    out, log = alien.apply(l1, str(tmp_path), [ask])
    assert log[0]["done"], log
    ok, why = eligible(out)
    assert ok, why
    lit = next(i for i in out["items"] if i["id"] == "lp.lit")
    assert lit["f"] == [9, 36] and out["assets"][lit["pic"]]["path"] == os.path.join("alien", "lp.mp4")
    assert os.path.getsize(tmp_path / "alien" / "lp.mp4") > 1000
    word = next(i for i in out["items"] if i["id"] == "lp.word")
    assert word["ink"] == "#FF2010" and "behind" not in word and word["op"][-1] < 0.2      # no matte: the word stands in front, and dies with its light
    before = sense.still(str(tmp_path / "take.mp4"), 0.5).astype(np.float32)
    after = sense.still(str(tmp_path / "alien" / "lp.mp4"), 0.4).astype(np.float32)        # frame 12 of the lit clip: the lamp is on
    wall = (slice(60, 110), slice(100, 170))                                               # the wall just under the word
    assert after[wall][..., 0].mean() - after[wall][..., 2].mean() > 12                    # it took the lamp's colour
    far = (slice(430, 470), slice(10, 60))
    assert after[far].mean() < before[far].mean()                                          # and the room stepped down away from it
    # with a matte the person is lit on the side that faces the word and throws a shadow away from it
    fr = np.full((120, 68, 3), 90, np.uint8)
    m = np.zeros((120, 68), np.float32)
    cv2.circle(m, (34, 80), 16, 1.0, -1)
    E = np.zeros((120, 68), np.float32)
    E[10:16, 20:48] = 1.0
    o = lamp.relight(fr, m, lamp.light_field(E), np.array([1.0, 0.1, 0.05], np.float32), 1.0, 0.4, (34.0, 13.0)).astype(np.float32)
    assert o[66, 34, 0] > o[94, 34, 0] + 4                                                 # the top of him, nearer the lamp, is the lit side
    assert o[40, 34, 0] > o[108, 34, 0] + 4                                                # the wall between is lit; behind him it is his shadow


# ------------------------------------------------------------------ the Score's seam

def test_the_alien_gesture_rides_the_scene_with_its_frames(monkeypatch):
    import score.compiler as S
    monkeypatch.setattr(S, "GOVERN", False)
    words = [{"w": f"w{i}", "t0": round(0.4 + i * 0.3, 3), "t1": round(0.4 + i * 0.3 + 0.24, 3), "filler": False} for i in range(40)]
    idx = {"video": {"fps": 30.0, "width": 1080, "height": 1920, "duration": 14.0, "has_audio": True}, "words": words,
           "sentences": [{"id": "s1", "t0": 0.4, "t1": 12.4, "wi0": 0, "wi1": 39, "text": ""}], "shots": [{"id": 1, "start": 0.0, "end": 14.0}]}
    bin_items = {str(i): {"kind": "image", "path": f"bin/{i}.jpg", "w": 1600, "h": 1067} for i in range(3)}
    do = [{"g": "alien", "dev": "answer", "for": "w3", "at": "w0..w30", "seat": [0.5, 0.47],
           "picks": [{"w": "w3", "pic": "bin:1", "word": "ONE"}, {"at": "w9", "pic": "bin:2", "word": "TWO"}, {"w": "w12", "pic": "bin:9", "word": "NO"}]},
          {"g": "alien", "dev": "arc", "for": "w0", "marks": [{"at": "w0", "year": 1927, "stakes": -0.5}, {"w": "w20", "stakes": 0.6}], "bars": 0.06},
          {"g": "alien", "for": "w0"}]
    text = "\n".join([json.dumps({"k": "film", "v": 1, "fps": 30, "frame": "9:16", "band": "S1", "seed": 7}),
                      json.dumps({"k": "dna", "plate": "#101010", "ink": "#FFFFFF", "pair": {"a": "grotesk-bold@.03", "b": "serif-italic@.034"}, "seat": [0.5, 0.5]}),
                      json.dumps({"k": "b", "id": "b00", "w": "0-39", "job": "a beat", "cut": "pulse 0.8", "pic": [{"src": "main"}], "type": "0-2|3-5", "do": do})]) + "\n"
    r = S.compile_score(text, index=idx, sid="fixture", bin_items=bin_items, faces=None)
    assert not S.validate_l1(r.l1)
    asks = r.l1["alien"]
    assert [a["dev"] for a in asks] == ["answer", "arc"]                      # a line that names no device is not an ask
    an, ar = asks
    assert an["at"][0] < an["picks"][0]["f"] < an["picks"][1]["f"] < an["at"][1] and [p["pic"] for p in an["picks"]] == ["bin:1", "bin:2"]
    assert an["seat"] == [0.5, 0.47] and {"bin:1", "bin:2"} <= set(r.l1["assets"])        # the pictures it names are assets of the scene
    assert ar["marks"][0]["year"] == 1927 and ar["marks"][1]["f"] > ar["marks"][0]["f"] and ar["bars"] == 0.06
    assert all(isinstance(a["id"], str) and "." not in a["id"] for a in asks)
    ok, why = eligible(r.l1)                                                  # an engine that never calls the bank draws the film as it was
    assert ok or all("uharfbuzz" in w for w in why), why
