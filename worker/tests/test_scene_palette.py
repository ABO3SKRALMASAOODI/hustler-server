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


def test_a_models_field_shapes_are_read_not_refused(film):
    from scene.palette import clean
    got, notes = clean({"dev": "lamp", "seat": "center", "room": "warm", "width": "0.7", "lift": True, "ink": "gold", "n": "3"})
    assert got["seat"] == [0.5, 0.45] and got["room"] == 0.55 and got["width"] == 0.7 and got["n"] == 3
    assert "lift" not in got and "ink" not in got and notes
    l1, work = film
    out, log = palette.apply(dict(l1, devices=[{"dev": "diecut", "at": [40, 70], "t": "BILLION", "seat": "center", "width": "wide"}]), work)
    assert log[0]["done"] and ok(out)


def full_negative(items, f):
    """Is frame f printed negative over the whole frame (an odd number of full-frame negative flashes on it)?"""
    n = sum(1 for i in items if i["k"] == "flash" and i["mode"] == "neg" and not i.get("rect") and i["f"][0] <= f < i["f"][1])
    return n % 2 == 1


def band_negative(items, f):
    return any(i["k"] == "flash" and i["mode"] == "neg" and i.get("rect") and i["f"][0] <= f < i["f"][1] for i in items)


def test_a_neg_hold_inside_a_neg_finish_leaves_its_span_visibly_changed(film):
    l1, work = film
    # asked as the Director's proofs did: the hold first, the section's finish after it, over the same frames
    asks = [{"dev": "neg_hold", "id": "h", "f": [100, 130]}, {"dev": "finish", "id": "fin", "f": [100, 170], "surface": "neg"}]
    out, log = palette.apply(dict(l1, devices=asks), work)
    assert all(r["done"] for r in log)
    items = out["items"]
    for f in range(100, 170):
        assert full_negative(items, f), f                          # the section stays negative: nothing cancels it
    for f in range(100, 130):
        assert band_negative(items, f), f                          # and the hold shows over it: a positive band through it
    assert not any(band_negative(items, f) for f in range(130, 170))
    hold = next(r for r in log if r["id"] == "h")
    assert hold["landed"] == "neg_hold" and hold["log"]["counterpart"]


def test_a_neg_finish_over_a_negative_frame_keeps_it_negative(film):
    l1, work = film
    l1 = dict(l1, items=l1["items"] + [{"id": "x", "k": "flash", "f": [110, 120], "mode": "neg", "a": 1.0}])
    out, log = palette.apply(dict(l1, devices=[{"dev": "finish", "f": [100, 150], "surface": "neg"}]), work)
    assert all(full_negative(out["items"], f) for f in range(100, 150))
    assert log[0]["log"]["kept_negative"] == 10


def test_out_of_range_numbers_and_seat_words_are_mapped_and_logged():
    from scene.palette import clean
    got, notes = clean({"dev": "diecut", "lift": 0.6, "seat": "low", "width": "big"})
    assert got["lift"] == 0.05 and any("outside 0-0.05" in n for n in notes)
    assert got["seat"][1] == 0.64 and 0.3 <= got["width"] <= 0.92
    got, _ = clean({"dev": "lamp", "room": True, "seat": "high"})
    assert got["room"] == 0.42 and got["seat"][1] == 0.3
    got, notes = clean({"dev": "neg", "run": 30, "n": 0})
    assert got["run"] == 8 and got["n"] == 1 and notes
    got, _ = clean({"dev": "diecut", "seat": [0.05, 0.02], "width": 0.84})
    assert got["seat"][0] - 0.42 >= 0 and got["seat"][1] >= 0.14       # a seat that would push the word off the frame moves in
    assert "frame widths" in palette.card("diecut") and "Units:" in palette.menu()


def test_diecut_lifted_letters_stay_inside_the_frame(film):
    from scene.alien import glyphs
    l1, work = film
    out, log = palette.apply(dict(l1, devices=[{"dev": "diecut", "id": "dc", "at": [40, 75], "t": "PREDICTIVE", "lift": 0.6,
                                                "width": 0.92, "seat": "top"}]), work)
    assert log[0]["landed"] == "diecut", log
    joins = [i for i in out["items"] if i["k"] == "join" and i["id"].startswith("dc.")]
    assert joins
    voice = out["voices"]["alien.dc"]
    _, b = glyphs.box(voice, "PREDICTIVE", voice["px"])
    for j in joins:
        s = float(j["s"])
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        x0 = j["x0"] * W + cx + (b[0] - cx) * s
        x1 = j["x0"] * W + cx + (b[2] - cx) * s
        y0 = j["y0"] * H + cy + (b[1] - cy) * s
        assert x0 >= 0 and x1 <= W and y0 >= 0, (j["id"], x0, x1, y0)


def test_a_word_device_replaces_the_builders_copy_and_word_devices_take_distinct_seats(film):
    l1, work = film
    asks = [{"dev": "diecut", "id": "a", "at": [40, 80], "t": "BILLION", "seat": "center"},
            {"dev": "diecut", "id": "b", "at": [50, 90], "t": "DOLLARS", "seat": "center"}]
    out, log = palette.apply(dict(l1, devices=asks), work)
    assert all(r["landed"] == "diecut" for r in log), log
    assert "w1" not in {i["id"] for i in out["items"]}               # the builder's display BILLION left while the device sets it
    assert log[0]["replaced"] == ["w1"]
    type_items = [i for i in out["items"] if i["k"] in ("word", "join") and i.get("t") and i.get("role") != "cap"]
    for x in type_items:
        for y in type_items:
            if x is y or x["id"].split(".")[0] == y["id"].split(".")[0]:
                continue
            same = palette.same_text(x["t"], y["t"])
            overlap = x["f"][0] < y["f"][1] and y["f"][0] < x["f"][1]
            assert not (same and overlap), (x["id"], y["id"])
    sa, sb = log[0]["seat"], log[1]["seat"]
    assert sb["y"] != sa["y"] and (sb["y"] - sb["h"] >= sa["y"] or sa["y"] - sa["h"] >= sb["y"])


def test_every_mode_named_on_a_card_resolves_to_the_device_that_draws_it(film):
    import re
    l1, work = film
    palette._discover()
    modes = re.search(r"mode\(([^)]*)\)", palette.DEVICES["variant"]["card"]).group(1).split("|")
    surfaces = re.search(r"surface\(([^)]*)\)", palette.DEVICES["finish"]["card"]).group(1).split("|")
    for m in modes:
        assert palette.resolve(m) == "variant", m
        out, log = palette.apply(dict(l1, devices=[{"dev": m, "f": [100, 130]}]), work)
        drawn = {i.get("mode") for i in out["items"] if i["k"] == "flash"} | {(i.get("treat") or {}).get("mode") for i in out["items"]}
        assert log[0]["landed"] == "variant" and m in drawn, (m, log)
    for sname in surfaces:
        if sname in palette.DEVICES:
            continue                                                    # "neg" is a device of its own
        dev = palette.resolve(sname)
        assert dev in ("finish", "variant"), sname
        out, log = palette.apply(dict(l1, devices=[{"dev": sname, "f": [100, 140]}]), work)
        got = (log[0]["log"] or {}).get("surface") or (log[0]["log"] or {}).get("mode")
        assert got == FIN.surface_of(sname), (sname, log[0])
    for name in ("posterize", "solarize", "rew", "collage", "zoom", "punch_in"):
        assert palette.resolve(name) != "white", name
    assert palette.resolve("b") == palette.resolve("zz") == "white"     # one or two letters: no prefix guess
    assert palette.resolve("bl") != "black"


def test_white_on_a_card_never_clips_to_an_overlay_and_a_frame_sized_card_is_a_full_white(film):
    l1, work = film
    asks = [{"dev": "finish", "f": [0, 40], "surface": "gate"}, {"dev": "white", "id": "w", "f": 20, "card": True}]
    out, log = palette.apply(dict(l1, devices=asks), work)
    w = next(i for i in out["items"] if i["id"].startswith("w") and i["k"] == "flash")
    assert not w.get("rect") and log[1]["log"].get("f") is not None   # full frame, spaced, its light recorded
    big = dict(l1, items=l1["items"] + [{"id": "c9", "k": "card", "f": [140, 170], "pic": "p2", "x": 0.5, "y": 0.5, "w": 0.95,
                                          "h": 0.95, "s": 1, "rot": 0, "op": 1, "z": 2}])
    out, log = palette.apply(dict(big, devices=[{"dev": "white", "f": 150, "card": True}]), work)
    assert not next(i for i in out["items"] if i["k"] == "flash").get("rect")
    out, log = palette.apply(dict(l1, devices=[{"dev": "white", "f": 110, "card": True}]), work)
    assert next(i for i in out["items"] if i["k"] == "flash").get("rect")   # a real card under it: clipped to the card


def test_devices_that_need_the_speaker_report_their_frames_and_fall_back_when_too_few(film):
    l1, work = film
    l1 = dict(l1, assets=dict(l1["assets"], main={"kind": "video", "path": l1["assets"]["p1"]["path"]}),
              items=[i for i in l1["items"] if i["id"] != "s3"] + [{"id": "t1", "k": "shot", "f": [70, 75], "pic": "main", "src": [0.0, 0.1667]},
                                                              {"id": "t2", "k": "shot", "f": [75, 100], "pic": "p2", "crop": [0, 0, 1, 1]}])
    patch = palette.plan(l1, work, {"dev": "neg_world", "id": "nw", "f": [70, 100]})
    assert "on screen 5 of the span's 30 frames" in patch["skip"]
    patch = palette.plan(l1, work, {"dev": "lamp", "id": "lp", "at": [60, 66], "t": "AI", "tail": 0})
    assert "would light 7 frames" in patch["skip"]
    out, log = palette.apply(dict(l1, devices=[{"dev": "neg_world", "id": "nw", "f": [70, 100]}]), work)
    assert log[0]["done"] and log[0]["landed"] != "neg_world" and log[0]["fallback"]


def test_a_fallback_never_reads_the_asked_devices_field_in_its_own_units(film):
    l1, work = film
    out, log = palette.apply(dict(l1, devices=[{"dev": "rhyme", "id": "r", "at": [40, 120], "n": 8}]), work)
    r = log[0]
    assert r["done"] and r["landed"] in ("match_pos", "neg") and r["fallback"]
    if r["landed"] == "neg":
        flips = [i for i in out["items"] if i["id"].startswith("rn")]
        assert flips and all(i["f"][1] - i["f"][0] == 1 for i in flips)     # 8 pictures is not an 8-frame (or 2-frame) flip
    else:                       # with lane B's match devices registered, the rhyme's chain lands on a position match
        chain = [i for i in out["items"] if i["id"].startswith("r.m") and "~" not in i["id"]]
        assert 2 <= len(chain) <= 8                                              # n read as pictures, never as frames


def test_a_card_behind_the_speaker_stands_only_on_his_take():
    l1 = {"dur_f": 60, "assets": {}, "items": [
        {"id": "t", "k": "shot", "f": [0, 20], "pic": "main", "src": [0.0, 0.6667]},
        {"id": "lit", "k": "shot", "f": [20, 40], "pic": "alien:x", "src": [0.0, 0.6667]},
        {"id": "t2", "k": "shot", "f": [40, 60], "pic": "own:cut1", "src": [1.0, 1.6667]},
        {"id": "nw", "k": "card", "f": [10, 50], "pic": "main", "src": [0.3333, 1.6667], "behind": True, "treat": {"mode": "neg"},
         "crop": [[0, 0, 1, 1]] * 40}]}
    out, trimmed = palette.behind_on_take(l1)
    cards = sorted((i for i in out["items"] if i["k"] == "card"), key=lambda i: i["f"][0])
    assert [c["f"] for c in cards] == [[10, 20], [40, 50]] and trimmed == {"nw": 20}
    assert len(cards[1]["crop"]) == 10 and cards[1]["src"][0] == round(0.3333 + (1.6667 - 0.3333) / 40 * 30, 5)
