"""worker/scene/palette: the registry every workflow's devices go through. No GPU: a device gives back scene items
and the engine's own ``eligible`` says whether the scene still holds. Frames of the devices on real films are in
.valmera/week-1120/creativity/A/."""
import ast
import json
import os

import numpy as np
import pytest

from scene import palette
from scene.palette import finish as FIN
from scene.palette import sense as SENSE
from scene.plan import eligible

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
FPS, W, H = 30, 540, 960
VOICES = {"cap": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 0}, "dsp": {"font": "InterDisplay-Black.ttf", "px": 80, "track": 0}}


def picture(path, col):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (360, 640), col)
    ImageDraw.Draw(im).ellipse([90, 210, 270, 390], fill=(240, 230, 210))
    im.save(path)
    return path


@pytest.fixture
def film(tmp_path, monkeypatch):
    monkeypatch.setattr(SENSE, "RVM", str(tmp_path / "no-model.onnx"))      # no matte: neg_world must fall back
    assets = {k: {"kind": "image", "path": picture(str(tmp_path / f"{k}.png"), c)}
              for k, c in (("p1", (40, 60, 90)), ("p2", (90, 50, 30)), ("p3", (20, 90, 40)), ("p4", (70, 70, 70)))}
    items = [{"id": "s1", "k": "shot", "f": [0, 40], "pic": "p4", "crop": [0, 0, 1, 1]},
             {"id": "s2", "k": "shot", "f": [40, 70], "pic": "p1", "crop": [[0, 0, 1, 1]] * 30},
             {"id": "s3", "k": "shot", "f": [70, 100], "pic": "p2", "crop": [0, 0, 1, 1]},
             {"id": "s4", "k": "shot", "f": [100, 150], "pic": "p4", "crop": [0, 0, 1, 1], "blur": [0.0] * 50},
             {"id": "s5", "k": "shot", "f": [150, 180], "pic": "p3", "crop": [0, 0, 1, 1]},
             {"id": "c1", "k": "card", "f": [100, 140], "pic": "p1", "x": 0.5, "y": 0.35, "w": 0.6, "h": 0.3, "s": 1, "rot": 0, "op": 1, "z": 2},
             {"id": "w1", "k": "word", "f": [45, 90], "t": "BILLION", "v": "dsp", "x": 0.2, "y": 0.5, "ink": "#F2C14E", "op": 1, "role": "big", "z": 8,
              "stroke": {"w": 3, "c": "#000000"}},
             {"id": "w2", "k": "word", "f": [0, 180], "t": "words", "v": "cap", "x": 0.4, "y": 0.62, "ink": "#FFFFFF", "op": 1, "role": "cap", "z": 5}]
    l1 = {"l1": 1, "sid": "t", "seed": 7, "fps": FPS, "frame": [W, H], "dur_f": 180, "assets": assets, "spine": [],
          "voices": dict(VOICES), "film": {}, "items": items, "sound": []}
    assert eligible(l1)[0] or eligible(l1)[1] == ["uharfbuzz is not installed"]
    return l1, str(tmp_path)


def ok(l1):
    good, why = eligible(l1)
    return good or why == ["uharfbuzz is not installed"]


def test_every_entry_keeps_the_contract():
    palette._discover()
    assert palette.CONTRACT == 1
    for name, e in palette.DEVICES.items():
        assert callable(e["plan"]), name
        assert e["layer"] in palette.LAYERS, name
        assert e["card"].split()[0] == name, name
        assert isinstance(e["fallback"], tuple), name
    for name in ("neg", "neg_hold", "neg_split", "neg_world", "white", "black", "tint", "variant", "spotlight", "blur_in",
                 "word_blend", "finish", "rhyme", "diecut", "lamp", "arc", "answer", "arc_stakes"):
        assert name in palette.DEVICES, name
    m = palette.menu()
    assert all(f"{n} " in m for n in palette.DEVICES)


def test_the_palette_reads_no_model_of_meaning():
    banned = {"llm", "anthropic", "openai", "agent_loop", "agent_tools", "claude_lane", "score", "director", "litellm"}
    root = os.path.join(WORKER, "scene", "palette")
    seen = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".py"):
            for node in ast.walk(ast.parse(open(os.path.join(root, name), encoding="utf-8").read())):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                seen += [(name, m) for m in mods if m.split(".")[0] in banned]
    assert not seen, seen


def test_every_device_lands_and_the_scene_holds(film):
    l1, work = film
    asks = [{"dev": "neg", "f": 40}, {"dev": "white", "f": 70}, {"dev": "black", "f": 99}, {"dev": "tint", "f": 100, "c": "magenta"},
            {"dev": "finish", "f": [100, 150], "surface": "gate"}, {"dev": "variant", "f": [150, 160], "mode": "thermal"},
            {"dev": "variant", "f": [160, 170], "mode": "kaleidoscope"}, {"dev": "spotlight", "f": [0, 40]}, {"dev": "blur_in", "f": 150},
            {"dev": "word_blend", "f": [45, 90]}, {"dev": "neg_split", "f": [120, 126], "part": "left"},
            {"dev": "neg_hold", "f": [10, 30]}, {"dev": "finish", "f": [70, 100], "surface": "bloom"},
            {"dev": "finish", "f": [0, 30], "surface": "crt"}, {"dev": "neg_world", "f": [110, 130]}]
    out, log = palette.apply(dict(l1, devices=asks), work)
    assert ok(out)
    assert len(log) == len(asks) and all(r["done"] for r in log), [r for r in log if not r["done"]]
    by = {r["id"]: r for r in log}
    assert by["d14"]["landed"] == "neg_split" and by["d14"]["fallback"]           # no matte: the world's negative becomes a split
    kinds = {it["k"] for it in out["items"]}
    assert {"flash", "card", "shot", "word"} <= kinds
    assert out["device_events"] and all("kind" in e for e in out["device_events"])
    assert "devices" not in out and "alien" not in out
    w = next(i for i in out["items"] if i["id"] == "w1")
    assert w["blend"] == "difference" and "stroke" not in w


def test_apply_is_deterministic_and_leaves_the_input(film):
    l1, work = film
    asks = [{"dev": "neg", "f": [10, 60], "run": 4}, {"dev": "finish", "f": [40, 120], "surface": "mono"}, {"dev": "tint", "f": 70}]
    before = json.dumps(l1, sort_keys=True)
    a, _ = palette.apply(dict(l1, devices=asks), work)
    b, _ = palette.apply(dict(l1, devices=asks), work)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(l1, sort_keys=True) == before


def test_a_finish_switches_on_its_edges_only(film):
    l1, work = film
    out, log = palette.apply(dict(l1, devices=[{"dev": "finish", "f": [55, 120], "surface": "mono"}]), work)
    shots = sorted((i for i in out["items"] if i["k"] == "shot"), key=lambda i: i["f"][0])
    cover = np.zeros(180, int)
    for s in shots:
        cover[s["f"][0]:s["f"][1]] += 1
        inside = s["f"][0] >= 55 and s["f"][1] <= 120
        assert (s.get("look") == "mono_silver") == inside, s
    assert (cover == 1).all()
    s2 = next(s for s in shots if s["f"][0] == 55)
    assert len(s2["crop"]) == s2["f"][1] - s2["f"][0]                     # a per-frame curve is cut with its shot


def test_an_unknown_name_maps_and_an_alien_skip_falls_back(film):
    l1, work = film
    asks = [{"dev": "polarity", "f": 50}, {"dev": "made-up thing", "f": 20}, {"dev": "gate", "f": [100, 140]},
            {"dev": "diecut", "at": 60}, {"dev": "rhyme", "n": 4}]
    out, log = palette.apply(dict(l1, devices=asks), work)
    assert ok(out)
    by = {r["asked"]: r for r in log}
    assert by["polarity"]["landed"] == "neg"
    assert by["made-up thing"]["landed"] == "white"
    assert by["gate"]["landed"] == "finish" and any(i["k"] == "card" and i["pic"] == "palette_gate" for i in out["items"])
    assert by["diecut"]["done"] and by["diecut"]["landed"] != "diecut" and by["diecut"]["fallback"]
    assert by["rhyme"]["done"] and by["rhyme"]["landed"] != "rhyme"
    assert all(r["done"] for r in log)


def test_strobes_stay_under_three_full_frame_changes_a_second(film):
    l1, work = film
    asks = [{"dev": "neg", "f": f} for f in (30, 33, 36, 40, 44, 48)] + [{"dev": "white", "f": 41}, {"dev": "black", "f": 45},
                                                                         {"dev": "neg", "f": [50, 150], "run": 8}]
    out, log = palette.apply(dict(l1, devices=asks), work)
    assert all(r["done"] for r in log)
    full = sorted(i["f"][0] for i in out["items"] if i["k"] == "flash" and not i.get("rect") and i["mode"] in ("neg", "fill"))
    for f in full:
        assert sum(1 for g in full if f <= g < f + FPS) <= 3, full


def test_the_alien_bank_keeps_its_own_order_under_the_palette(film):
    l1, work = film
    out, log = palette.apply(dict(l1, alien=[{"dev": "arc", "marks": [{"f": 0, "year": 1975, "stakes": -0.5}, {"f": 90, "year": 2015, "stakes": 0.6}]}],
                                  devices=[{"dev": "white", "f": 70}]), work)
    assert [r["dev"] for r in log] == ["white", "arc"]        # look layer: the arc lays last of all
    assert all(r["done"] for r in log)
    assert ok(out)


def test_overlays_are_drawn_once(tmp_path):
    p = FIN.overlay(str(tmp_path), "gate", 108, 192)
    m = os.path.getmtime(p)
    assert FIN.overlay(str(tmp_path), "gate", 108, 192) == p and os.path.getmtime(p) == m


def test_the_measure_reads_flips_whites_and_cuts_from_pixels():
    from scene.palette import measure as M
    rng = np.random.RandomState(3)
    a = (rng.rand(128, 72, 3) * 120 + 40).astype(np.uint8)
    b = (rng.rand(128, 72, 3) * 120 + 60).astype(np.uint8)
    seq = [a] * 20 + [255 - a] + [a] * 9 + [np.full_like(a, 250)] + [b] * 29 + [np.zeros_like(a)] + [b] * 30
    s = M.signal(np.stack(seq), 30.0)
    assert s["flips_f"] == [20] and s["white_f"] == [30] and s["black_f"] == [60]
    assert s["cuts_10s"] > 0 and s["contour_3s"][2] >= s["contour_3s"][0]
    assert M.edit_distance(["neg", "white"], ["neg", "tint", "white"]) == 1
    side = M.log_side([dict(done=True, landed="neg", f=[3, 4]), dict(done=True, landed="white", f=[0, 1], fallback=True), dict(done=False)])
    assert side["sequence"] == ["white", "neg"] and side["fell_back"] == 1 and side["first_device_f"] == 0
