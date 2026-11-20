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


def _product_row(l1, f0=40, f1=90):
    """A display row as the product sets it: one word item for the whole line, punched in on its landing."""
    l1["voices"]["row"] = {"font": "InterDisplay-Black.ttf", "px": 64, "track": -1}
    n = f1 - f0
    l1["items"].append({"id": "row", "k": "word", "f": [f0, f1], "t": "wouldn't be here", "v": "row", "x": 0.18, "y": 0.5,
                        "ink": "#F6F1E7", "role": "big", "on": f0, "z": 7, "stroke": "#000000",
                        "s": [round(1.0 + 0.16 * np.exp(-q / 3.0), 5) for q in range(n)]})
    return l1


@pytest.mark.parametrize("style", ["marker", "box", "under"])
def test_highlight_covers_the_whole_phrase_as_drawn_and_darkens_only_the_phrase(tmp_path, style):
    """m06b frame 262: the marker covered 'her' of 'here' and the whole row went dark over a dark suit."""
    l1 = _product_row(film(tmp_path))
    l1["said"].append(["here", 44, 50, 0])
    l1["said"].sort(key=lambda r: r[1])
    out, log = laid(l1, tmp_path, [{"dev": "highlight", "id": "h", "at": {"word": "here"}, "args": {"t": "here", "style": style, "c": "#F2C14E"}}])
    assert log[0]["dev"] == "highlight", log
    sweep = next(i for i in out["items"] if i["id"] == "h.hl")
    row = next(i for i in l1["items"] if i["id"] == "row")
    v = l1["voices"]["row"]
    full = row["t"]
    g0, g1 = len(K.metrics(v, "wouldn't be ")["glyphs"]), len(K.metrics(v, full)["glyphs"])
    lo = sweep["f"][0]
    widths = [r[2] - r[0] for r in sweep["rect"]]
    assert widths[0] < widths[4]                                              # it sweeps in left to right
    for q in range(5, len(sweep["rect"])):                                    # then covers the phrase as drawn, frame by frame
        f = lo + q
        s = row["s"][f - row["f"][0]]
        ink = K.drawn(v, full, row["x"], row["y"], W, H, s=s, part=(g0, g1))
        r = sweep["rect"][q]
        assert r[0] <= ink[0] + 1e-4 and r[2] >= ink[2] - 1e-4, (f, r, ink)
        if style != "under":
            assert r[1] < ink[3] and r[3] >= ink[3] - 1e-4
    pieces = [i for i in out["items"] if str(i["id"]).startswith("row~hl")]
    if style == "under":
        assert not pieces                                                     # an underline leaves the ink as it is
        return
    dark = [i for i in pieces if i["ink"] == K.DARK]
    assert [i["t"] for i in dark] == ["here"]                                 # only the phrase turns
    q = dark[0]["f"][0] - lo                                                  # once the marker is under all of it
    ink = K.drawn(v, full, row["x"], row["y"], W, H, s=row["s"][dark[0]["f"][0] - row["f"][0]], part=(g0, g1))
    assert sweep["rect"][q][2] >= ink[2] - 0.002, (q, sweep["rect"][q], ink)
    keep = [i for i in pieces if i["ink"] != K.DARK]
    assert [i["t"] for i in keep] == ["wouldn't be"] and keep[0]["ink"] == row["ink"] and keep[0].get("stroke") == "#000000"
    assert not any(i["id"] == "row" and i["f"][1] > dark[0]["f"][0] for i in out["items"])
    # the pieces stand exactly where the line drew those glyphs, through its punch (scale about the line's centre)
    for pc, part in ((keep[0], (0, g0)), (dark[0], (g0, g1))):
        for f in (pc["f"][0], pc["f"][0] + 3, pc["f"][1] - 1):
            line = K.drawn(v, full, row["x"], row["y"], W, H, s=row["s"][f - row["f"][0]], part=part)
            got = K.item_box(pc, out["voices"], W, H, f)
            assert max(abs(p - q) for p, q in zip(line[::2], got[::2])) * W < 1.0, (pc["t"], f, line, got)
            assert max(abs(p - q) for p, q in zip(line[1::2], got[1::2])) * H < 1.0, (pc["t"], f, line, got)


def test_flip_turns_only_the_phrase(tmp_path):
    l1 = _product_row(film(tmp_path))
    l1["said"].append(["here", 44, 50, 0])
    l1["said"].sort(key=lambda r: r[1])
    out, log = laid(l1, tmp_path, [{"dev": "flip", "id": "f", "at": {"word": "here"}, "args": {"t": "here", "c": "#E8452C"}}])
    assert log[0]["dev"] == "flip", log
    turned = [i for i in out["items"] if str(i["id"]).startswith("row~fl")]
    assert {i["t"]: i["ink"] for i in turned} == {"wouldn't be": "#F6F1E7", "here": "#E8452C"}
    neg = next(i for i in out["items"] if i["id"] == "f.neg")
    row = next(i for i in l1["items"] if i["id"] == "row")
    v = l1["voices"]["row"]
    g0, g1 = len(K.metrics(v, "wouldn't be ")["glyphs"]), len(K.metrics(v, row["t"])["glyphs"])
    f = neg["f"][0]
    ink = K.drawn(v, row["t"], row["x"], row["y"], W, H, s=row["s"][f - row["f"][0]], part=(g0, g1))
    assert neg["rect"][0] <= ink[0] and neg["rect"][2] >= ink[2]


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


TOP_RIGHT = [0.62, 0.20, 0.86, 0.40]          # a podcast framing: the speaker's face high on the right, under the watermark


def _ask_for(dev):
    args = {"t": "Mark Cuban", "name": "Mark Cuban", "title": "brother", "from": 0, "to": 9, "prefer": 0.12, "label": "Mark Cuban",
            "note": "Dallas Mavericks", "lines": ["Pittsburgh 1982", "a long line of fine print"], "value": 0.4, "corner": "tl",
            "kicker": "part two", "y": 0.05, "words": ["Mark", "Cuban"], "bars": [{"label": "us", "value": 0.8}], "small": "brother"}
    ask = {"dev": dev, "id": dev[:5], "at": {"word": "Mark"}, "args": args}
    if dev in ("highlight", "flip"):
        ask.update(at={"word": "lowest"}, args=dict(args, t="lowest"))
    if dev == "readalong":
        ask.update(at=0, until=80)
    if dev in ("answer", "rhyme", "arc"):
        ask.update(at=[60, 90], picks=[{"f": 64, "pic": "p1", "word": "VEGAS"}], t="MARK")
    if dev in ("lamp", "diecut"):
        ask.update(at=[66, 80], t="MARK")
    return ask


def _in_mark(out, own):
    """Every frame on which a word of the device's own type (or a line it re-laid) meets the watermark's keep-out."""
    bad = []
    for it in out["items"]:
        if it["k"] != "word" or it.get("role") == "cap" or not (str(it["id"]).startswith(own + ".") or "~" in str(it["id"])):
            continue
        for f in range(it["f"][0], it["f"][1]):
            b = K.item_box(it, out["voices"], W, H, f)
            if b and K.overlap(b, K.MARK) > 1e-6:
                bad.append((it["id"], f, [round(q, 3) for q in b]))
                break
    return bad


@pytest.mark.parametrize("dev", sorted(d for d in palette.DEVICES if d != "behind"))
def test_no_device_lands_in_the_watermark_with_a_face_top_right(tmp_path, monkeypatch, dev):
    """Every device in the palette, on every frame it stands, with the speaker's face high on the right (where the HUD's
    box is pushed to the watermark's edge)."""
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [list(TOP_RIGHT)])
    l1 = film(tmp_path)
    ask = _ask_for(dev)
    out, log = laid(l1, tmp_path, [ask])
    assert log[0]["done"] or not palette.DEVICES[dev]["fallback"], log      # rhyme and arc need their own pictures here
    assert not _in_mark(out, ask["id"]), (log[0]["dev"], _in_mark(out, ask["id"]))


def test_the_hud_label_is_seated_off_the_watermark_where_the_face_is_top_right(tmp_path, monkeypatch):
    """The reproduction: face box [0.62, 0.20, 0.86, 0.40] put the label block at y 0.179-0.192, inside the mark."""
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [list(TOP_RIGHT)])
    l1 = film(tmp_path)
    out, log = laid(l1, tmp_path, [{"dev": "track", "id": "t", "at": 10, "until": 60, "args": {"label": "Mark Cuban", "note": "brother", "style": "corners"}}])
    assert log[0]["dev"] == "track" and not _in_mark(out, "t")
    labels = [i for i in mine(out, "t") if i["k"] == "word"]
    assert any(i["t"] == "MARK CUBAN" for i in labels) and any(i["t"] == "BROTHER" for i in labels)


def test_a_marquee_and_the_type_pages_stay_off_the_watermark(tmp_path, monkeypatch):
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [list(TOP_RIGHT)])
    l1 = film(tmp_path)
    asks = [{"dev": "marquee", "id": "mq", "at": 10, "until": 50, "args": {"t": "TOGETHER", "y": 0.02, "behind": False}},
            {"dev": "chapter", "id": "ch", "at": 60, "args": {"t": "a very long chapter title that wraps", "kicker": "part two"}},
            {"dev": "type_run", "id": "tr", "at": 90, "until": 120, "args": {"words": ["WITHIN", "WALKING", "DISTANCE"]}}]
    out, log = laid(l1, tmp_path, asks)
    assert [r["dev"] for r in log] == ["chapter", "type_run", "marquee"] or {r["dev"] for r in log} == {"marquee", "chapter", "type_run"}
    for a in asks:
        assert not _in_mark(out, a["id"]), a["dev"]


# ------------------------------------------------------------------ the row's window, and the type laid before

def test_a_device_ends_by_the_rows_window_and_a_counter_still_lands_on_its_word(tmp_path):
    l1 = film(tmp_path)
    land = next(r[1] for r in l1["said"] if r[0] == "4.3")
    asks = [{"dev": "verb", "id": "v", "at": {"word": "lowest"}, "bound": 30, "args": {"t": "lowest", "hold": 60}},
            {"dev": "counter", "id": "c", "at": {"word": "sold"}, "lands": {"word": "4.3"}, "bound": land + 3,
             "args": {"from": 285e6, "to": 4.3e9, "unit": "short", "fmt": "${n}", "hold": 40}}]
    out, log = laid(l1, tmp_path, asks)
    assert all(r["done"] for r in log)
    assert max(i["f"][1] for i in mine(out, "v")) <= 30
    ticks = sorted((i for i in mine(out, "c") if i["k"] == "word" and i["id"].startswith("c.c")), key=lambda i: i["f"][0])
    assert ticks[-1]["f"][0] == land and land + 8 >= ticks[-1]["f"][1] > land   # it lands on its word and holds a beat, no more


def test_hud_type_seats_itself_off_the_type_laid_before_it(tmp_path, monkeypatch):
    """m03: the track's label and note ran through the counter's label and digits; m06: the note met the name super."""
    l1 = film(tmp_path)
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [[0.25, 0.30, 0.75, 0.52]])
    asks = [{"dev": "counter", "id": "c", "at": 10, "lands": 40, "until": 100,
             "args": {"from": 200e6, "to": 285e6, "unit": "short", "fmt": "${n}", "label": "bought for", "seat": [0.5, 0.27]}},
            {"dev": "track", "id": "t", "at": 12, "until": 90, "args": {"label": "Mark Cuban", "note": "Dallas Mavericks", "style": "box"}},
            {"dev": "fineprint", "id": "fp", "at": 12, "until": 90, "args": {"lines": ["Pittsburgh 1982"], "corner": "tl"}}]
    out, log = laid(l1, tmp_path, asks)
    assert [r["dev"] for r in log] == ["counter", "track", "fineprint"], log
    counter = [i for i in mine(out, "c") if i["k"] == "word"]
    for dev in ("t", "fp"):
        for it in (i for i in mine(out, dev) if i["k"] == "word"):
            for f in range(it["f"][0], it["f"][1], 2):
                b = K.item_box(it, out["voices"], W, H, f)
                for c in counter:
                    if c["f"][0] <= f < c["f"][1]:
                        cb = K.item_box(c, out["voices"], W, H, f)
                        assert K.overlap(b, cb) < 1e-5, (it["id"], it["t"], c["t"], f, b, cb)


# ------------------------------------------------------------------ the word behind the head, seated on the matte

def _head(w=135, h=240, cx=0.42, top=0.22, r=0.17):
    """A person's matte: a round head (``r`` of the frame's width across) whose crown is at ``top``, a neck, and
    shoulders and a body under it."""
    import cv2
    m = np.zeros((h, w), np.float32)
    rx, ry = int(r * w), int(r * w * 1.3)
    cv2.ellipse(m, (int(cx * w), int(top * h) + ry), (rx, ry), 0, 0, 360, 1.0, -1)
    cv2.rectangle(m, (int((cx - 0.08) * w), int(top * h) + ry), (int((cx + 0.08) * w), int(top * h) + 2 * ry + 8), 1.0, -1)
    cv2.rectangle(m, (int((cx - 0.36) * w), int(top * h) + 2 * ry + 6), (int((cx + 0.36) * w), h), 1.0, -1)
    return m


def test_the_word_behind_the_head_keeps_every_letter_in_view(tmp_path):
    """m06 frames 697/712: LOVE read 'L VE', the O hidden behind the head. The seat is measured on the matte: every
    letter mostly in view, the head over the lower part of the word."""
    l1 = film(tmp_path)
    K.scaled(l1)
    v = K.fit(K.voice("InterDisplay-Black.ttf", 100, track=-2, shadow=0.0), "LOVE", 0.94 * W, cap_px=0.24 * H)
    mattes = [_head(top=0.22 + 0.005 * k) for k in range(4)]
    face = [0.30, 0.30, 0.54, 0.50]
    old_base = face[1] + 0.12 * (face[3] - face[1])                          # the seat this device used to take
    vv, x, base, meas = PB.seat_behind(v, "LOVE", mattes, W, H, K.MARK[3] + 0.015, 0.75, old_base)
    sats = [PB._sat(m) for m in mattes]
    glyphs = K.metrics(vv, "LOVE")["glyphs"]
    seen = [min(1 - PB._cover(sa, [x + g[0] / W, base + g[1] / H, x + g[2] / W, base + g[3] / H]) for sa in sats) for g in glyphs]
    assert len(glyphs) == 4 and min(seen) >= PB.SEEN, seen                  # no letter lost behind the head
    assert meas["hidden"] > 0.02                                             # and the head still passes in front of the word
    x0, _ = K.place(v, "LOVE", 0.5, old_base, W, H)
    old = [min(1 - PB._cover(sa, [x0 + g[0] / W, old_base + g[1] / H, x0 + g[2] / W, old_base + g[3] / H]) for sa in sats)
           for g in K.metrics(v, "LOVE")["glyphs"]]
    assert min(old) < PB.SEEN                                                # the old seat lost a letter on this matte
    blk = K.drawn(vv, "LOVE", x, base, W, H)
    assert K.overlap(blk, K.MARK) == 0
    low = [_head(top=0.40 + 0.005 * k) for k in range(4)]                   # a head lower in the frame: the word keeps its size,
    _, _, base2, meas2 = PB.seat_behind(v, "LOVE", low, W, H, K.MARK[3] + 0.015, 0.75, 0.42)   # the crown over its feet
    assert meas2["scale"] == 1.0 and meas2["seen_min"] >= PB.SEEN and meas2["hidden"] > 0.02 and base2 > 0.40


def _person_take(tmp_path, top=0.22):
    """A take (a dark person on a light wall) and its matte, 9:16, through the scene's own clock."""
    import cv2
    n, size = 24, (180, 320)
    m = _head(size[0], size[1], top=top)
    for name, person, wall in (("take.mp4", (40, 40, 40), (210, 205, 200)), ("matte_src.mp4", (255, 255, 255), (0, 0, 0))):
        p = subprocess.Popen([SEG.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", "30", "-i", "-",
                              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p", str(tmp_path / name)], stdin=subprocess.PIPE)
        for _ in range(n):
            im = np.full((size[1], size[0], 3), wall, np.uint8)
            im[m > 0.5] = person
            p.stdin.write(im.tobytes())
        p.stdin.close()
        p.wait()
    return cv2


@needs_ffmpeg
def test_behind_reads_the_takes_matte_and_seats_the_word_by_it(tmp_path, monkeypatch):
    _person_take(tmp_path)
    l1 = {"l1": 1, "sid": "t", "seed": 1, "fps": 30, "frame": [W, H], "dur_f": 24, "spine": [], "film": {}, "sound": [],
          "assets": {"main": {"kind": "video", "path": "take.mp4"}}, "voices": {}, "said": [["I", 1, 3, 0], ["LOVE", 2, 10, 0], ["you", 10, 14, 0]],
          "items": [{"id": "s0", "k": "shot", "f": [0, 24], "pic": "main", "src": [0.0, 0.8]}]}
    monkeypatch.setattr(PB, "ensure_matte", lambda l1, work, a, z: ({"matte": {"kind": "video", "path": "matte_src.mp4"}}, "fixture"))
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [[0.30, 0.30, 0.54, 0.50]])
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "behind", "id": "b", "at": 2, "until": 22, "args": {"t": "LOVE", "small": "I"}}])
    assert log[0]["dev"] == "behind", log
    meas = log[0]["log"]["measured"]
    assert meas and meas["seen_min"] >= PB.SEEN and meas["hidden"] > 0.0, meas
    assert not _in_mark(out, "b")


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
        from scene.faults import SceneFault  # noqa: F401
        from scene.runtime import make_context
        make_context().release()
    except Exception as e:      # noqa: BLE001
        pytest.skip(f"no GL context here: {e}")
    import scene.compile as SC
    import scene.run as RUN
    import cv2
    _person_take(tmp_path, top=0.40)
    W2, H2 = 180, 320
    l1 = {"l1": 1, "sid": "t", "seed": 1, "fps": 30, "frame": [W2, H2], "dur_f": 24, "spine": [], "film": {}, "sound": [],
          "assets": {"main": {"kind": "video", "path": "take.mp4"}},
          "voices": {}, "said": [["LOVE", 2, 10, 0]],
          "items": [{"id": "s0", "k": "shot", "f": [0, 24], "pic": "main", "src": [0.0, 0.8]}]}
    monkeypatch.setattr(PB, "ensure_matte", lambda l1, work, a, z: ({"matte": {"kind": "video", "path": "matte_src.mp4"}}, "fixture"))
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [[0.30, 0.48, 0.54, 0.62]])
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "behind", "id": "b", "at": 2, "until": 22, "args": {"t": "LOVE", "ink": "#FF0000", "hold": 20}}])
    assert log[0]["dev"] == "behind" and log[0]["done"], log
    word = next(i for i in out["items"] if i["id"] == "b.bh")
    assert word["behind"] and out["assets"]["matte"]["path"] == "matte_src.mp4"
    e = RUN.Engine(SC.compile(out, str(tmp_path), segment=False))
    try:
        fr = e.stills([18])[18][..., :3].astype(np.int16)
    finally:
        e.close()
    red = (fr[..., 0] > 150) & (fr[..., 1] < 90) & (fr[..., 2] < 90)
    assert red.sum() > 50                                                      # the word is drawn on the wall
    person = cv2.erode((_head(W2, H2, top=0.40) > 0.5).astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    assert red[person].sum() == 0                                              # and never over the person
    assert red[person].sum() == 0 and log[0]["log"]["measured"]["hidden"] > 0  # though the head passes in front of it


@needs_ffmpeg
def test_a_close_up_with_no_room_behind_the_head_lands_the_words_in_front(tmp_path, monkeypatch):
    """m06b frame 700: the head fills the width and the body the rest; no seat behind keeps a letter. The payoff
    still lands, in front, off the face, on its word."""
    import cv2
    n, size = 24, (180, 320)
    m = np.zeros((size[1], size[0]), np.uint8)
    cv2.ellipse(m, (90, 80), (70, 80), 0, 0, 360, 255, -1)
    m[130:] = 255
    for name, img in (("take.mp4", np.where(m[..., None] > 0, 60, 200).astype(np.uint8).repeat(3, 2)), ("matte_src.mp4", m[..., None].repeat(3, 2))):
        p = subprocess.Popen([SEG.FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", "30", "-i", "-",
                              "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8", "-pix_fmt", "yuv420p", str(tmp_path / name)], stdin=subprocess.PIPE)
        for _ in range(n):
            p.stdin.write(np.ascontiguousarray(img).tobytes())
        p.stdin.close()
        p.wait()
    l1 = {"l1": 1, "sid": "t", "seed": 1, "fps": 30, "frame": [W, H], "dur_f": 24, "spine": [], "film": {}, "sound": [],
          "assets": {"main": {"kind": "video", "path": "take.mp4"}}, "voices": {}, "said": [["I", 1, 3, 0], ["LOVE", 3, 10, 0]],
          "items": [{"id": "s0", "k": "shot", "f": [0, 24], "pic": "main", "src": [0.0, 0.8]}]}
    monkeypatch.setattr(PB, "ensure_matte", lambda l1, work, a, z: ({"matte": {"kind": "video", "path": "matte_src.mp4"}}, "fixture"))
    monkeypatch.setattr(K, "detect_faces", lambda rgb: [[0.2, 0.15, 0.8, 0.45]])
    out, log = palette.apply(l1, str(tmp_path), [{"dev": "behind", "id": "b", "at": 1, "until": 22, "args": {"t": "LOVE", "small": "I"}}])
    assert log[0]["done"] and log[0]["log"].get("front"), log
    words = [i for i in mine(out, "b") if i["k"] == "word"]
    assert words and not any(i.get("behind") for i in words) and " ".join(i["t"] for i in words) == "I LOVE"
    assert words[0]["f"][0] == 1                                               # on the word that says it
