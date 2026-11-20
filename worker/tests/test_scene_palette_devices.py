"""Lane D (20 Nov): what the scene engine could not draw before, and the palette devices that lay it.

Each renderer addition is measured on a drawn frame: an RGB split moves red and blue by the asked pixels, a kept hue
leaves no chroma elsewhere, a tilted card's corners land where a lens puts them, the person drawn again through his
matte keeps his pixels while the world behind him becomes the plate. Every existing item still draws byte for byte as
before (the golden frames in test_scene_engine.py, and the before/after hashes kept with the proof in
.valmera/week-1120/creativity/D/). The palette tests run the devices on films made here; every device lands, and a
device a film cannot carry lands its fallback."""
import ast
import math
import os

import numpy as np
import pytest

from scene import palette
from scene.plan import eligible

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)


# ------------------------------------------------------------------ no GL: the plan and the palette


def scene_of(items, dur, assets=None, frame=(540, 960), voices=None):
    return {"l1": 1, "sid": "t", "seed": 7, "fps": 30, "frame": list(frame), "dur_f": dur, "assets": assets or {}, "spine": [],
            "voices": voices or {"cap": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 0}}, "film": {}, "items": items, "sound": []}


def test_the_palette_imports_no_model_code_and_no_meaning():
    """The palette reads no language model; its signal models (RVM, depth) run in a child or lazily inside sense."""
    banned = {"llm", "anthropic", "openai", "agent_loop", "agent_tools", "agent_prompt", "claude_lane", "score", "director", "litellm", "torch"}
    root = os.path.join(WORKER, "scene", "palette")
    seen = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py") or name == "_depth_child.py":
            continue
        tree = ast.parse(open(os.path.join(root, name), encoding="utf-8").read())
        for node in tree.body:                       # module level only: a signal model is imported where it is used
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            seen += [(name, m) for m in mods if m.split(".")[0] in banned | {"onnxruntime", "cv2"}]
    assert not seen, seen


def test_the_palette_is_inside_the_code_fingerprint():
    import version
    rel = {os.path.relpath(p, WORKER).replace(os.sep, "/") for p in version._source_files()}
    for need in ("scene/palette/__init__.py", "scene/palette/matte.py", "scene/palette/depth.py", "scene/palette/proof.py",
                 "scene/palette/surface.py", "scene/palette/glitch.py", "scene/palette/sense.py", "scene/glsl/keep.frag.glsl"):
        assert need in rel, need


def test_every_device_has_a_card_a_plan_and_a_chain_that_ends_on_any_film():
    palette._discover()
    assert len(palette.DEVICES) >= 45
    for name, d in palette.DEVICES.items():
        assert callable(d["plan"]) and d["card"] and d["layer"] in palette.LAYERS, name
        for fb in d.get("fallback") or ():
            assert palette.resolve(fb), (name, fb)
    assert len(palette.menu().splitlines()) == len(palette.DEVICES)
    for alien_dev in ("rhyme", "diecut", "lamp", "arc", "answer"):
        assert alien_dev in palette.DEVICES
    # a family name or a near name is repaired to a device, never refused
    assert palette.resolve("glitch") == "glitch" and palette.resolve("surface") and palette.resolve("matte")
    assert palette.resolve("rgb") == "rgb_split"


def test_new_fields_are_known_to_the_plan():
    base = [{"id": "s", "k": "shot", "f": [0, 4], "pic": "plate", "plate": {"c0": "#101010", "c1": "#101010"}}]
    ok, _ = eligible(scene_of(base + [{"id": "f", "k": "flash", "f": [0, 4], "mode": m} for m in ("leak",)], 4))
    assert ok
    for mode in ("leak", "filmburn", "prism", "vhs", "led", "xerox", "projector", "paper", "mark", "rgb_split", "slice", "keep_hue"):
        ok, why = eligible(scene_of(base + [{"id": "f", "k": "flash", "f": [0, 4], "mode": mode}], 4))
        assert ok, (mode, why)
    ok, why = eligible(scene_of(base + [{"id": "c", "k": "card", "f": [0, 4], "pic": "plate", "blend": "screen", "rx": 30, "ry": [0, 1, 2, 3]}], 4))
    assert ok, why
    ok, why = eligible(scene_of(base + [{"id": "c", "k": "card", "f": [0, 4], "pic": "plate", "blend": "overlay"}], 4))
    assert not ok and "card blend" in why[0]
    ok, why = eligible(scene_of([dict(base[0], keep=[{"mode": "ghost"}])], 4))
    assert not ok and "keep" in why[0]


def test_a_device_on_a_film_without_a_take_lands_its_fallback(tmp_path):
    l1 = scene_of([{"id": "s", "k": "shot", "f": [0, 60], "pic": "plate", "plate": {"c0": "#202020", "c1": "#202020"}}], 60)
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "silhouette", "id": "a", "f": [0, 30]}, {"dev": "glitchy", "id": "b", "at": 10},
                                                 {"dev": "bg_swap", "id": "c", "f": [30, 60]}])
    assert all(r["done"] for r in log), log
    assert log[0]["landed"] == "keep_hue" and log[0]["tried"][0]["dev"] == "silhouette"
    assert log[1]["landed"] in ("glitch", "rgb_split")
    assert log[2]["landed"] in ("keep_hue",) and [t["dev"] for t in log[2]["tried"]] == ["bg_swap", "sticker"]
    ok, why = eligible({k: v for k, v in out.items() if k not in ("device_events", "device_log")})
    assert ok, why
    assert out["device_events"], "every landing is listed for the sound workflow"


def test_glitch_follows_the_moment_and_never_flashes_light():
    l1 = scene_of([{"id": "s", "k": "shot", "f": [0, 90], "pic": "plate", "plate": {"c0": "#202020", "c1": "#202020"}}], 90)
    out, log = palette.apply(l1, "/nonexistent", [{"dev": "glitch", "id": "g", "f": [10, 20], "follow": "hit"},
                                                  {"dev": "tear", "id": "t", "f": [30, 90]}])
    modes = sorted({i["mode"] for i in out["items"] if i["k"] == "flash"})
    assert modes == ["rgb_split", "slice"]
    tears = [i for i in out["items"] if i["id"].startswith("pd_tr")]
    assert len(tears) >= 3 and all(i["f"][1] - i["f"][0] <= 4 for i in tears)


def test_cutting_a_shot_keeps_its_clock_and_curves():
    from scene.palette._util import cut, make_room
    sh = {"id": "s", "k": "shot", "f": [10, 40], "pic": "main", "src": [1.0, 2.0], "crop": [[0, 0, 1, 1]] * 30, "blur": list(range(30))}
    c = cut(sh, 20, 30)
    assert c["f"] == [20, 30] and abs(c["src"][0] - (1.0 + 10 / 30)) < 1e-9 and len(c["crop"]) == 10 and c["blur"][0] == 10
    drop, add = make_room(scene_of([sh], 60), 20, 30, "x")
    assert drop == ["s"] and [a["f"] for a in add] == [[10, 20], [30, 40]] and len({a["id"] for a in add}) == 2


def test_parallax_layers_cover_the_frame_and_the_near_one_is_the_near_part():
    from scene.palette.depth import layers
    h, w = 240, 180
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (40, 90, 160)
    img[120:, 40:140] = (200, 120, 40)                    # a near block
    d = np.zeros((h, w), np.float32)
    d[:] = np.linspace(0.0, 0.4, h)[:, None]
    d[120:, 40:140] = 1.0
    ls = layers(img, d, 3)
    assert ls[0][1] is None, "the far layer is opaque: no hole anywhere"
    near = ls[-1][1]
    assert near[180, 90] > 0.95 and near[40, 90] < 0.05
    far = ls[0][0].astype(int)
    assert np.abs(far[180, 90] - (200, 120, 40)).sum() > 120, "the far layer is filled where the near block stood"


# ------------------------------------------------------------------ review round (20 Nov)


def _photo(path, text=False):
    from PIL import Image, ImageDraw, ImageFont
    im = Image.new("RGB", (900, 1200), (60, 64, 70))
    d = ImageDraw.Draw(im)
    if text:
        im.paste((236, 232, 222), (0, 0, 900, 1200))
        font = ImageFont.truetype(os.path.join(WORKER, "fonts", "InterDisplay-Bold.ttf"), 46)
        for k, line in enumerate(("THE COMPANY FILED", "FOR BANKRUPTCY ON", "MONDAY, 14 MARCH")):
            d.text((80, 420 + k * 90), line, fill=(20, 20, 20), font=font)
    else:
        d.ellipse([560, 160, 760, 360], fill=(250, 246, 230))          # a lamp, high right: the light
    im.save(path)
    return path


def test_proof_object_marks_a_measured_line_and_never_a_photograph(tmp_path):
    for text in (False, True):
        p = _photo(str(tmp_path / f"p{int(text)}.png"), text)
        l1 = scene_of([{"id": "s", "k": "shot", "f": [0, 90], "pic": "p", "crop": [0, 0, 1, 1]}], 90,
                      {"p": {"kind": "image", "path": p, "w": 900, "h": 1200}})
        out, log = palette.apply(l1, str(tmp_path / f"w{int(text)}"), [{"dev": "proof_object", "id": "q", "f": [0, 90]}])
        assert log[0]["done"] and log[0]["landed"] == "proof_object", log
        marks = [i for i in out["items"] if i["k"] == "flash" and i.get("mode") == "mark"]
        how = log[0]["log"]["box_from"]
        if text:
            assert how == "measured" and len(marks) == 1
            box = log[0]["log"]["box"]
            assert 0.3 < box[1] < 0.6, box                                # one of the printed lines
        else:
            assert how in ("face", "light") and not marks, (how, marks)
            box = log[0]["log"]["box"]
            assert box[0] < 0.73 < box[2] and box[1] < 0.22 < box[3], box    # pushed towards the lamp


def test_the_alien_bank_lays_in_its_own_order_inside_a_layer_and_rows_keep_marks_and_seat(tmp_path):
    l1 = scene_of([{"id": "s", "k": "shot", "f": [0, 90], "pic": "plate", "plate": {"c0": "#202020", "c1": "#202020"}}], 90)
    asks = [{"dev": "diecut", "id": "d", "at": [10, 40], "t": "GO"}, {"dev": "lamp", "id": "l", "at": [50, 80], "t": "ME"}]
    out, log = palette.apply(l1, str(tmp_path), asks)
    assert [r["id"] for r in log][:2] == ["l", "d"], log                  # lamp before diecut (alien.ORDER), whatever the asking order
    assert all("marks" in r and "seat" in r for r in log if r.get("done"))


def test_no_machine_path_is_written_in_the_palette():
    root = os.path.join(WORKER, "scene", "palette")
    for name in os.listdir(root):
        if name.endswith(".py"):
            assert "/Users/" not in open(os.path.join(root, name), encoding="utf-8").read(), name


def test_model_files_are_found_through_the_environment_only(tmp_path, monkeypatch):
    from scene.palette import sense as S
    (tmp_path / "depth").mkdir()
    (tmp_path / "depth" / "depth_anything_v2_vits.pth").write_bytes(b"x")
    (tmp_path / "rvm_mobilenetv3_fp32.onnx").write_bytes(b"x")
    monkeypatch.setenv("VALMERA_MODELS", str(tmp_path))
    monkeypatch.setenv("VALMERA_TORCH_PY", "/opt/torch/bin/python")
    assert S.rvm_path() == str(tmp_path / "rvm_mobilenetv3_fp32.onnx")
    assert S.model("depth_anything_v2_vits.pth") == str(tmp_path / "depth" / "depth_anything_v2_vits.pth")
    assert S.torch_pythons()[0] == "/opt/torch/bin/python"
    monkeypatch.delenv("VALMERA_MODELS")
    monkeypatch.delenv("VALMERA_MODELS_DIR", raising=False)
    assert S.model_dirs() == [os.path.join(WORKER, "models")]


def test_a_director_ask_on_one_word_lands_on_that_word(tmp_path):
    """The Director's asks arrive as one frame (``f``: the word's frame) and its pictures as ``pool``."""
    from scene.palette._util import frames, pictures
    l1 = scene_of([{"id": "s", "k": "shot", "f": [0, 300], "pic": "plate"}], 300,
                  {"p1": {"kind": "image", "path": "a.png"}, "p2": {"kind": "image", "path": "b.png"}})
    assert frames(l1, {"f": 120}, default_len=30) == (120, 150)
    assert frames(l1, {"at": [40, 70]}) == (40, 70)
    assert frames(l1, {"f": 12, "t": "word", "until": {"w": 3}}, default_len=10) == (12, 22)
    assert pictures(l1, {"pool": ["p2"]})[0] == "p2"
