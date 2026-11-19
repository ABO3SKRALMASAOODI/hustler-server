"""worker/scene/palette (lane C): type that performs, type in depth, the HUD, and the alien devices' fallbacks.

Every device gives back plain scene items; the engine's own ``eligible`` says the scene still holds. Pictures here are
made here (noise and discs); the type is the engine's own shaper. The pixel test at the end draws a word behind a
person through a matte made here, on the GPU, and is skipped where there is no GL context."""
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

import scene.segments as SEG
from scene import palette
from scene.palette import behind as PB
from scene.palette import hud as PH
from scene.palette import kit as K
from scene.palette import type as PT
from scene.plan import eligible

FPS, W, H = 30, 540, 960
try:
    import uharfbuzz  # noqa: F401
    HB = True
except Exception:      # noqa: BLE001
    HB = False
needs_hb = pytest.mark.skipif(not HB, reason="uharfbuzz is not installed")
needs_ffmpeg = pytest.mark.skipif(not shutil.which(SEG.FF), reason="no ffmpeg on this machine")
pytestmark = needs_hb

SAID = "July 2005 is the lowest moment of my life and my brother Mark Cuban sold it for 4.3 billion dollars".split()


def picture(path, shade, size=(360, 640)):
    import cv2
    rng = np.random.RandomState(shade)
    im = (rng.rand(size[1], size[0], 3) * 30 + shade).astype(np.uint8)
    cv2.circle(im, (size[0] // 2, size[1] // 3), size[0] // 6, (200, 200, 190), -1)
    cv2.imwrite(path, im)


def film(tmp_path, n_shots=4, dur=150):
    """A film of ``n_shots`` stills, a word clock, captions inked on their words, a display voice."""
    assets, items = {}, []
    edges = [round(dur * i / n_shots) for i in range(n_shots + 1)]
    for i in range(n_shots):
        picture(str(tmp_path / f"p{i}.png"), 40 + 25 * i)
        assets[f"p{i}"] = {"kind": "image", "path": f"p{i}.png"}
        items.append({"id": f"s{i}", "k": "shot", "f": [edges[i], edges[i + 1]], "pic": f"p{i}"})
    said = [[w, 4 + 6 * i, 4 + 6 * i + 5, 0 if i < 11 else 1] for i, w in enumerate(SAID)]
    for i, (w, on, off, seg) in enumerate(said):
        if on >= dur - 2:
            continue
        items.append({"id": f"c{i:02d}", "k": "word", "f": [on, min(on + 14, dur)], "t": w, "v": "cap", "x": 0.3 + 0.05 * (i % 4), "y": 0.78,
                      "ink": "#FFFFFF", "role": "cap", "on": on, "z": 5})
    items.append({"id": "big0", "k": "word", "f": [20, min(70, dur)], "t": "lowest moment", "v": "dsp", "x": 0.2, "y": 0.4, "ink": "#F2EDE4", "role": "big", "on": 40, "z": 7})
    return {"l1": 1, "sid": "t", "seed": 7, "fps": FPS, "frame": [W, H], "dur_f": dur, "assets": assets, "spine": [], "said": said,
            "voices": {"cap": {"font": "InterDisplay-Bold.ttf", "px": 30, "track": 0}, "dsp": {"font": "InterDisplay-Black.ttf", "px": 70, "track": 0}},
            "film": {}, "items": items, "sound": []}


def laid(l1, tmp_path, asks):
    out, log = palette.apply(l1, str(tmp_path), asks)
    ok, why = eligible(out)
    assert ok, why
    return out, log


def mine(out, ask_id):
    return [i for i in out["items"] if str(i["id"]).startswith(ask_id + ".")]


# ------------------------------------------------------------------ the registry

def test_every_family_registers_its_devices_with_cards_layers_and_fallbacks():
    for name in ("counter", "staircase", "name", "ladder", "brackets", "highlight", "flip", "typewriter", "redact", "chapter",
                 "type_run", "marquee", "verb", "readalong", "behind", "track", "fineprint", "donut", "bar",
                 "answer", "rhyme", "lamp", "diecut", "arc"):
        d = palette.DEVICES[name]
        assert callable(d["plan"]) and d["card"].startswith(name) and d["layer"] in range(1, 7)
        assert all(f in palette.DEVICES for f in d["fallback"]), (name, d["fallback"])
    assert len(palette.menu().splitlines()) == len(palette.DEVICES)
    assert "verb" in palette.chain("counter") and palette.chain("counter")[0] == "counter"


def test_the_palette_reads_no_meaning():
    """No language model and no keyword rule: the palette reads numbers of the scene and pixels of its pictures."""
    import ast
    root = os.path.dirname(palette.__file__)
    banned = {"llm", "anthropic", "openai", "agent_loop", "agent_tools", "score", "director", "re"}
    for name in os.listdir(root):
        if name.endswith(".py"):
            for node in ast.walk(ast.parse(open(os.path.join(root, name), encoding="utf-8").read())):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
                assert not [m for m in mods if m.split(".")[0] in banned], (name, mods)


def test_words_are_found_on_the_clock_by_sentence():
    l1 = {"said": [["As", 10, 14, 1], ["we", 14, 18, 1], ["as", 40, 44, 1], ["our", 44, 48, 1], ["As", 90, 94, 2]], "items": []}
    assert K.frame_of(l1, {"seg": 1, "word": "As"}) == 10
    assert K.frame_of(l1, {"seg": 1, "word": "as our"}) == 40           # a phrase names the one occurrence
    assert K.frame_of(l1, {"seg": 2, "word": "As"}) == 90
    assert K.frame_of(l1, {"word": "as", "near": 85}) == 90


# ------------------------------------------------------------------ figures

def test_the_counter_holds_one_seat_across_every_cut_and_lands_on_the_spoken_figure(tmp_path):
    l1 = film(tmp_path)
    ask = {"dev": "counter", "id": "c", "at": {"word": "sold"}, "lands": {"word": "4.3"},
           "args": {"from": 285e6, "to": 4.3e9, "unit": "short", "fmt": "${n}", "hold": 30, "run": 40}}
    out, log = laid(l1, tmp_path, [ask])
    assert log[0]["done"] and log[0]["dev"] == "counter", log
    ticks = sorted((i for i in mine(out, "c") if i["k"] == "word" and i["id"].startswith("c.c")), key=lambda i: i["f"][0])
    assert ticks[-1]["t"] == "$4.3B" and ticks[0]["t"] == "$285M"
    land = next(r[1] for r in l1["said"] if r[0] == "4.3")
    assert ticks[-1]["f"][0] == land                                           # the last value on the frame the figure is said
    cuts = {s["f"][0] for s in out["items"] if s["k"] == "shot"}
    assert len([c for c in cuts if ticks[0]["f"][0] < c < ticks[-1]["f"][1]]) >= 1
    v = out["voices"][ticks[0]["v"]]
    centres = set()
    for it in ticks:
        _, b = K.ink_box(v, it["t"])
        centres.add(round(it["x"] + (b[0] + b[2]) / 2 / W, 4))
    assert len(centres) == 1 and len({it["y"] for it in ticks}) == 1          # one seat: centre and baseline never move
    lens = [i["f"][1] - i["f"][0] for i in ticks[:-1]]
    assert lens[-1] >= lens[0]                                                 # the steps slow into the landing
    assert isinstance(ticks[-1]["s"], list) and ticks[-1]["s"][0] > 1.05       # and the figure hits


def test_a_counter_crossing_three_cuts_keeps_its_seat(tmp_path):
    l1 = film(tmp_path, n_shots=6, dur=150)
    out, log = laid(l1, tmp_path, [{"dev": "counter", "id": "k", "at": 10, "lands": 100, "args": {"from": 0, "to": 1200, "sep": True, "fmt": "{n} MILES"}}])
    ticks = [i for i in mine(out, "k") if i["k"] == "word" and i["id"].startswith("k.c")]
    cuts = sorted({s["f"][0] for s in out["items"] if s["k"] == "shot"})
    assert len([c for c in cuts if 10 < c < 100]) >= 3
    assert len({i["y"] for i in ticks}) == 1 and ticks[-1]["t"] == "1,200 MILES"


def test_formats_follow_the_callers_figure():
    assert PT.fmt_value(4.3e9, {"unit": "short", "fmt": "${n}"}, final=True) == "$4.3B"
    assert PT.fmt_value(285e6, {"unit": "short", "fmt": "${n}"}, final=True) == "$285M"
    assert PT.fmt_value(1200, {"from": 0, "to": 1200, "sep": True}, final=True) == "1,200"
    assert PT.fmt_value(2005, {"from": 1987, "to": 2005, "fmt": "JULY {n}"}, final=True) == "JULY 2005"
    assert PT.fmt_value(4.3e9, {"unit": "words"}, final=True) == "4.3 BILLION"


# ------------------------------------------------------------------ names and words

def test_the_name_super_has_two_sizes_and_the_big_one_is_the_name(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "name", "id": "n", "at": {"word": "Mark"}, "args": {"name": "Mark Cuban", "title": "brother", "layout": "stack"}}])
    words = [i for i in mine(out, "n") if i["k"] == "word"]
    big = [i for i in words if i["t"] == "MARK CUBAN"]
    small = [i for i in words if i["t"].startswith("B")]
    assert big and small
    assert out["voices"][big[0]["v"]]["px"] >= 2.0 * out["voices"][small[0]["v"]]["px"]
    assert big[0]["f"][0] == next(r[1] for r in l1["said"] if r[0] == "Mark")
    assert min(i["f"][0] for i in small) < big[0]["f"][0]                     # the small line is there first: the name lands second


def test_the_ladder_whispers_then_shouts_on_the_word(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "ladder", "id": "L", "at": {"word": "the lowest"},
                                    "args": {"words": [{"t": "the", "w": "whisper"}, {"t": "lowest", "w": "shout"}], "brackets": True}}])
    shout = next(i for i in mine(out, "L") if i["t"] == "LOWEST")
    assert shout["f"][0] == next(r[1] for r in l1["said"] if r[0] == "lowest")
    assert any(i["t"] == "[" for i in mine(out, "L")) and any(i["t"] == "]" for i in mine(out, "L"))


def test_typewriter_letters_only_ever_arrive_and_keep_up_with_the_voice(tmp_path):
    l1 = film(tmp_path)
    text = "the lowest moment of my life"
    out, log = laid(l1, tmp_path, [{"dev": "typewriter", "id": "tw", "at": {"word": "the lowest"}, "args": {"t": text}}])
    lines = sorted((i for i in mine(out, "tw") if i["k"] == "word"), key=lambda i: i["f"][0])
    for y in {i["y"] for i in lines}:                                          # on every line the letters only ever arrive
        lens = [len(i["t"]) for i in lines if i["y"] == y]
        assert lens == sorted(lens)
    assert " ".join(max((i for i in lines if i["y"] == y), key=lambda i: len(i["t"]))["t"] for y in sorted({i["y"] for i in lines})) == text
    for w in ("lowest", "moment", "life"):
        said_f = next(r[1] for r in l1["said"] if r[0] == w)
        nxt = next(r[1] for r in l1["said"] if r[1] > said_f)
        first = min(i["f"][0] for i in lines if i["t"].endswith(w))
        assert said_f <= first + 1 and first <= nxt, (w, said_f, first, nxt)   # each word is typed while it is said


def test_highlight_sweeps_behind_a_word_on_screen_and_turns_its_ink(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "highlight", "id": "h", "at": {"word": "lowest"}, "args": {"t": "lowest", "style": "box", "c": "#F2C14E"}}])
    sweep = next(i for i in out["items"] if i["id"] == "h.hl")
    assert sweep["k"] == "flash" and sweep["mode"] == "fill"
    widths = [r[2] - r[0] for r in sweep["rect"]]
    assert widths[0] < widths[4] and widths[-1] == widths[5]                 # it grows left to right, then holds
    assert any(i["id"] == "big0~hl" and i["ink"] == K.DARK for i in out["items"])


def test_a_type_page_takes_the_frame_with_one_base_picture_and_no_caption_over_it(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "type_run", "id": "tr", "at": {"word": "the lowest moment"}, "until": {"word": "moment"},
                                    "args": {"t": "the lowest moment"}}, {"dev": "chapter", "id": "ch", "at": 100, "args": {"t": "Mark", "kicker": "part two"}}])
    pages = [i for i in out["items"] if i["k"] == "shot" and i.get("pic") == "plate"]
    assert len(pages) >= 3
    for p in pages:
        assert not [c for c in out["items"] if c.get("role") == "cap" and c["f"][0] < p["f"][1] and c["f"][1] > p["f"][0]]


def test_redact_reveals_each_word_as_it_is_said(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "redact", "id": "r", "at": {"word": "the lowest"}, "args": {"t": "the lowest moment", "mode": "reveal"}}])
    for w in ("LOWEST", "MOMENT"):
        it = next(i for i in mine(out, "r") if i.get("t") == w)
        bar = next(i for i in mine(out, "r") if i["id"] == it["id"].replace(".w", ".bar"))
        assert bar["f"][0] < it["f"][0] < bar["f"][1]                         # the bar stands until the word is said, then slides off


def test_readalong_stands_the_line_dim_and_lights_each_word_as_it_is_said(tmp_path):
    l1 = film(tmp_path)
    for i, it in enumerate(x for x in l1["items"] if x.get("role") == "cap"):
        it["f"][1] = 60 if it["on"] < 60 else it["f"][1]
    out, log = laid(l1, tmp_path, [{"dev": "readalong", "id": "ra", "at": 0, "until": 60}])
    late = next(i for i in out["items"] if i["id"] == "c03")
    assert late["f"][0] < late["on"] and late["op"][0] < 0.5 and late["op"][late["on"] - late["f"][0]] == 1.0


# ------------------------------------------------------------------ the HUD

def test_hud_boxes_follow_the_face(tmp_path, monkeypatch):
    l1 = film(tmp_path, n_shots=1, dur=40)
    calls = {"n": 0}

    def faces(rgb):
        calls["n"] += 1
        x = 0.2 + 0.01 * calls["n"]
        return [[x, 0.3, x + 0.3, 0.55]]
    monkeypatch.setattr(K, "detect_faces", faces)
    out, log = laid(l1, tmp_path, [{"dev": "track", "id": "t", "at": 2, "until": 30, "args": {"label": "Mark", "style": "box"}}])
    left = next(i for i in out["items"] if i["id"] == "t.ln2")                 # the box's left edge
    xs = [r[0] for r in left["rect"]]
    assert xs[-1] > xs[6] + 0.03                                               # it moves with the face
    assert any(i["k"] == "word" and i["t"] == "MARK" for i in mine(out, "t"))


def test_donut_and_bars_fill_to_their_values(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [[0.1, 0.3, 0.45, 0.6]])
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "donut", "id": "d", "at": 20, "until": 80, "args": {"value": 73, "label": "share"}},
                                   {"dev": "bar", "id": "b", "at": 90, "until": 140, "args": {"bars": [{"label": "us", "value": 0.8}, {"label": "them", "value": 0.2}]}}])
    lit = [i for i in mine(out, "d") if i["id"].startswith("d.D")]
    assert len(lit) == round(0.73 * 40)
    assert any(i["t"] == "73%" for i in out["items"] if i["k"] == "word")
    b0 = next(i for i in out["items"] if i["id"] == "b.b0")
    assert abs((b0["rect"][-1][2] - b0["rect"][-1][0]) - 0.8 * 0.62) < 1e-3


# ------------------------------------------------------------------ fallbacks: no moment is left bare

def test_a_device_that_cannot_stand_runs_its_fallback_chain(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "counter", "id": "x", "at": {"word": "lowest"}, "args": {"t": "lowest"}},
                                   {"dev": "highlight", "id": "y", "at": {"word": "brother"}, "args": {"t": "Vegas"}}])
    assert log[0]["done"] and log[0]["dev"] in ("staircase", "verb") and log[0]["tried"][0]["dev"] == "counter"
    assert log[1]["done"] and log[1]["dev"] in ("flip", "verb")


def test_behind_with_no_take_falls_back_to_type_in_front(tmp_path):
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "behind", "id": "b", "at": {"word": "Mark"}, "args": {"t": "Mark"}}])
    assert log[0]["done"] and log[0]["dev"] in ("marquee", "verb") and "no take" in log[0]["tried"][0]["why"]
    assert not any(i.get("behind") for i in out["items"])


def test_alien_answer_lamp_and_diecut_fall_back_instead_of_leaving(tmp_path):
    l1 = film(tmp_path)
    l1["items"] = [i for i in l1["items"] if i["k"] != "shot"] + [
        {"id": "pl", "k": "shot", "f": [0, 60], "pic": "plate", "plate": {"c0": "#101010", "c1": "#101010"}},
        {"id": "s1", "k": "shot", "f": [60, 150], "pic": "p1"}]
    asks = [{"dev": "answer", "id": "an", "at": [0, 60], "picks": [{"f": 10, "pic": "gone", "word": "VEGAS"}, {"f": 30, "pic": "gone2", "word": "MOON"}]},
            {"dev": "lamp", "id": "lp", "at": [12, 30], "t": "LOVE"},
            {"dev": "diecut", "id": "dc", "at": [36, 40], "t": "GONE"}]
    out, log = laid(l1, tmp_path, asks)
    by = {r["id"]: r for r in log}
    assert by["an"]["done"] and by["an"]["dev"] == "answer" and [w["word"] for w in by["an"]["log"]["words"]] == ["VEGAS", "MOON"]
    assert by["lp"]["done"] and by["lp"]["log"]["glow_only"]
    spill = next(i for i in out["items"] if i["id"] == "lp.spill")
    assert max(spill["a"]) <= 0.12                                             # a breath of colour, never a flash
    assert by["dc"]["done"] and by["dc"]["log"]["borrowed_frames"] > 0
    j = [i for i in out["items"] if i["id"].startswith("dc.l")]
    assert j and all(i["pic"] == "p1" for i in j) and all(i["f"][1] - i["f"][0] >= 1 for i in j)
    assert max(i["f"][1] for i in j) - min(i["f"][0] for i in j) >= 8           # held the eight frames a lift needs


def test_no_type_device_lands_in_the_watermark(tmp_path):
    l1 = film(tmp_path)
    asks = [{"dev": d, "id": d[:3], "at": {"word": "Mark"}, "args": {"t": "Mark Cuban", "name": "Mark Cuban", "from": 0, "to": 9, "prefer": 0.12}}
            for d in ("counter", "name", "verb", "staircase", "typewriter", "redact")]
    out, log = laid(l1, tmp_path, asks)
    assert all(r["done"] for r in log), log
    for it in out["items"]:
        if it["k"] != "word" or not str(it["id"])[:3] in {a["id"] for a in asks}:
            continue
        v = out["voices"][it["v"]]
        x = it["x"][-1] if isinstance(it["x"], list) else it["x"]
        y = it["y"][-1] if isinstance(it["y"], list) else it["y"]
        assert K.overlap(K.block_of(v, it["t"], x, y, W, H), K.MARK) < 1e-4, it["id"]


# ------------------------------------------------------------------ the depth sandwich, on the GPU

def _matte_film(tmp_path):
    """A take: a bright wall with a dark 'person' disc in the middle, and its matte (the disc)."""
    import cv2
    n, size = 30, (320, 180)
    for name, person, wall in (("take.mp4", (40, 40, 40), (210, 205, 200)), ("matte_src.mp4", (255, 255, 255), (0, 0, 0))):
        p = subprocess.Popen([SEG.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", "30", "-i", "-",
                              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p", str(tmp_path / name)], stdin=subprocess.PIPE)
        for _ in range(n):
            im = np.full((size[1], size[0], 3), wall, np.uint8)
            cv2.rectangle(im, (130, 0), (190, 180), person, -1)
            p.stdin.write(im.tobytes())
        p.stdin.close()
        p.wait()


@needs_ffmpeg
def test_a_word_behind_the_speaker_is_drawn_only_where_the_matte_is_empty(tmp_path, monkeypatch):
    try:
        from scene.faults import SceneFault
        from scene.runtime import make_context
        make_context().release()
    except Exception as e:      # noqa: BLE001
        pytest.skip(f"no GL context here: {e}")
    import scene.compile as SC
    import scene.run as RUN
    _matte_film(tmp_path)
    W2, H2 = 180, 320
    l1 = {"l1": 1, "sid": "t", "seed": 1, "fps": 30, "frame": [W2, H2], "dur_f": 30, "spine": [], "film": {}, "sound": [],
          "assets": {"main": {"kind": "video", "path": "take.mp4"}},
          "voices": {}, "said": [["LOVE", 2, 10, 0]],
          "items": [{"id": "s0", "k": "shot", "f": [0, 30], "pic": "main", "src": [0.0, 1.0]}]}
    monkeypatch.setattr(PB, "ensure_matte", lambda l1, work, a, z: ({"matte": {"kind": "video", "path": "matte_src.mp4"}}, "fixture"))
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [[0.4, 0.3, 0.6, 0.5]])
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "behind", "id": "b", "at": 2, "until": 28, "args": {"t": "LOVE", "ink": "#FF0000", "hold": 26}}])
    assert log[0]["dev"] == "behind" and log[0]["done"], log
    word = next(i for i in out["items"] if i["id"] == "b.bh")
    assert word["behind"] and out["assets"]["matte"]["path"] == "matte_src.mp4"
    e = RUN.Engine(SC.compile(out, str(tmp_path), segment=False))
    try:
        fr = e.stills([20])[20][..., :3].astype(np.int16)
    finally:
        e.close()
    red = (fr[..., 0] > 150) & (fr[..., 1] < 90) & (fr[..., 2] < 90)
    assert red.sum() > 50                                                      # the word is drawn on the wall
    person = np.zeros_like(red)
    person[:, int(W2 * 0.21) + 2:int(W2 * 0.79) - 2] = True                   # the person, through the take's cover crop
    assert red[person].sum() == 0                                              # and never over the person
