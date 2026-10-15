"""Wave 2 track 2E: recall over the footage and story shapes
(INTERFACES_W2.md §6). Synthetic frames and descriptors; the fixture pool
measurements live in WAVE2_RELEASE.md (AT 2E-1..2E-5)."""

import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import w2_support as S                                    # noqa: E402
import agent_tools                                        # noqa: E402,F401
import arc_planner                                        # noqa: E402
import callbacks                                          # noqa: E402
import library                                            # noqa: E402
import loops                                              # noqa: E402
import stills                                             # noqa: E402
import story_match                                        # noqa: E402
import tools_ext                                          # noqa: E402
import virtual_shots                                      # noqa: E402
from tools_ext import structure_tools as ST               # noqa: E402


def _img(seed, palette=(0, 0, 0)):
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, 192)[None, :, None]
    y = np.linspace(0, 1, 108)[:, None, None]
    base = (np.array(palette)[None, None, :] + 60 * np.sin(6 * x + seed)
            + 40 * np.cos(4 * y))
    return np.clip(base + rng.normal(0, 4, (108, 192, 3)), 0,
                   255).astype(np.uint8)


def test_same_framing_finds_the_set_by_its_palette_and_lines():
    a = library.describe(_img(1, (150, 60, 40)))
    b = library.describe(_img(1, (156, 66, 44)))   # same set, other light
    c = library.describe(_img(7, (30, 90, 160)))
    ents = [{"key": "x", "mid": 1, "desc": c}, {"key": "y", "mid": 1,
                                                "desc": b}]
    top = story_match.same_framing(a, ents, k=2)
    assert top[0][1]["key"] == "y"


def test_conform_crop_maps_the_point_and_the_size():
    crop = story_match.conform_crop(1920, 1080, 9 / 16, (900, 400), 120,
                                    (0.5, 0.42), 0.25)
    x0, y0, x1, y1 = crop
    cw, ch = (x1 - x0) * 1920, (y1 - y0) * 1080
    assert cw / ch == pytest.approx(9 / 16, rel=0.01)
    assert (900 - x0 * 1920) / cw == pytest.approx(0.5, abs=0.002)
    assert (400 - y0 * 1080) / ch == pytest.approx(0.42, abs=0.002)
    assert 120 / cw == pytest.approx(0.25, abs=0.002)
    # a point too near the edge for that window cannot align
    assert story_match.conform_crop(1920, 1080, 9 / 16, (20, 400), 120,
                                    (0.5, 0.42), 0.25) is None


def _face_entry(key, eyes, mid=1.0):
    return {"key": key, "mid": mid, "t0": 0, "t1": 2,
            "desc": {"eyes": [eyes], "faces": []}}


def test_faces_conform_onto_one_eye_line_and_one_size():
    ents = [_face_entry(f"k{i}", [[0.45 + 0.01 * i, 0.30 + 0.02 * i],
                                  [0.53 + 0.012 * i, 0.30 + 0.02 * i]])
            for i in range(6)]
    ents.append(_face_entry("tilted", [[0.40, 0.30], [0.50, 0.40]]))
    sizes = {e["key"]: (1920, 1080) for e in ents}
    seq = story_match.face_aligned(ents, sizes, 9 / 16, n=10)
    assert len(seq) == 6 and "tilted" not in {e["key"] for e, _c in seq}
    dists = []
    for e, (x0, y0, x1, y1) in seq:
        (lx, ly), (rx, ry) = e["desc"]["eyes"][0]
        cw, ch = (x1 - x0), (y1 - y0)
        my = (((ly + ry) / 2) - y0) / ch
        assert my == pytest.approx(story_match.EYE_LINE_Y, abs=0.003)
        dists.append((rx - lx) / cw)
    assert max(dists) - min(dists) < 0.003


def test_a_shape_chain_needs_three_true_circles():
    def ent(k, c):
        return {"key": k, "mid": 1.0, "cluster": k,
                "desc": {"circles": [c]}}
    sizes = {k: (1920, 1080) for k in "abcd"}
    two = [ent("a", [0.5, 0.5, 0.2]), ent("b", [0.4, 0.45, 0.25])]
    assert story_match.shape_chain(two, sizes, 9 / 16) == []
    three = two + [ent("c", [0.6, 0.5, 0.22])]
    seq = story_match.shape_chain(three, sizes, 9 / 16)
    assert len(seq) == 3
    for e, (x0, y0, x1, y1) in seq:
        cx, cy, r = e["desc"]["circles"][0]
        assert (cx - x0) / (x1 - x0) == pytest.approx(0.5, abs=0.003)
        assert (cy - y0) / (y1 - y0) == pytest.approx(0.5, abs=0.003)


def test_circles_are_verified_not_texture():
    import cv2
    disc = np.full((216, 384), 40, np.uint8)
    cv2.circle(disc, (190, 100), 60, 220, -1)
    found = library.find_circles(disc)
    assert found and found[0][0] == pytest.approx(190 / 384, abs=0.02)
    noise = (np.random.default_rng(0).random((216, 384)) * 255).astype(
        np.uint8)
    assert library.find_circles(noise) == []


def test_callbacks_find_the_repeated_phrase():
    text = ("AI is bigger than the internet and I mean it is really "
            "bigger than the internet").split()
    words = [{"w": w, "t0": i * 0.5} for i, w in enumerate(text)]
    cb = callbacks.find(words)
    assert cb and cb[0]["text"].lower().startswith("bigger than the internet")
    assert cb[0]["t_again"] and cb[0]["t_again"][0] > cb[0]["t_first"]
    edl = {"motion": [{"id": "ts1", "template": "typeset", "params": {
        "text": "bigger than the *internet*"}}]}
    assert callbacks.for_typeset(cb, edl)[0][1] == "ts1"


def test_a_loop_closes_before_the_end_card_on_a_rhyme():
    a = library.describe(_img(3, (90, 90, 90)))
    ents = [{"key": "main", "t0": 0, "t1": 4, "mid": 2, "desc": a},
            {"key": "main", "t0": 20, "t1": 24, "mid": 22, "desc": a},
            {"key": "c", "t0": 0, "t1": 4, "mid": 2,
             "desc": library.describe(_img(9, (10, 120, 30)))}]
    hits = loops.rhyme(a, ents, exclude=("main", 0, 4))
    assert hits and hits[0][1]["t0"] == 20
    ins = loops.reprise_insert(dict(hits[0][1], key="orig.mp4"), 30.0, 30.0)
    assert ins["at_output_s"] == 30.0 and 0.6 <= ins["duration_s"] <= 1.4
    assert 20 <= ins["source_start_s"] <= 24 - ins["duration_s"]


def test_still_scores_prefer_sharp_frames_and_clear_boxes():
    flat = np.full((90, 160), 120.0)
    sharp = flat.copy()
    sharp[::4, :] = 200.0
    assert stills.sharpness(sharp) > stills.sharpness(flat)
    assert stills.clutter(sharp, [0, 0, 1, 1]) > stills.clutter(
        flat, [0, 0, 1, 1])


def test_density_arc_never_steps_back():
    slots = [{"start": float(k)} for k in range(9)]
    secs = [{"t0": 0, "t1": 3, "energy": 0.2},
            {"t0": 3, "t1": 6, "energy": 0.9},
            {"t0": 6, "t1": 9, "energy": 0.5}]
    out = arc_planner.density(slots, secs)
    order = [arc_planner.STATES.index(s) for s in out]
    assert order == sorted(order) and out[0] == "type_black"


def test_macro_crops_keep_the_canvas_shape():
    e = {"key": "k", "t0": 0, "t1": 3,
         "desc": {"faces": [[0.4, 0.2, 0.5, 0.4]], "circles": []}}
    out = virtual_shots.macros(e, 3840, 2160, 9 / 16)
    x0, y0, x1, y1 = out[0]["crop"]
    assert (x1 - x0) * 3840 / ((y1 - y0) * 2160) == pytest.approx(9 / 16,
                                                                 rel=0.01)
    assert out[0]["derived_from"]["key"] == "k"


def test_direction_department_holds_the_recall_verbs():
    assert set(ST.VERBS) <= tools_ext.EXT_DEPARTMENTS.get("direction", set())


def test_recall_without_footage_says_so(monkeypatch):
    ctx = S.Ctx(S.edl())
    monkeypatch.setattr(ST, "entries", lambda c, keys=None, main=True:
                        ([], {}, None))
    assert "No footage" in ST.find_shots(ctx, like=1.0)
    assert "No footage" in ST.plan_loop(ctx)


def test_plan_loop_writes_one_insert_before_the_end_card(monkeypatch):
    a = library.describe(_img(3, (90, 90, 90)))
    ents = [{"key": "main", "t0": 0, "t1": 4, "mid": 2, "desc": a},
            {"key": "main", "t0": 12, "t1": 16, "mid": 14, "desc": a}]
    ctx = S.Ctx(S.edl(), keys={"orig.mp4": {"storage_key": "orig.mp4",
                                            "duration_s": 60}})
    monkeypatch.setattr(ST, "entries", lambda c, keys=None, main=True:
                        (ents, {"main": (1920, 1080)}, "orig.mp4"))
    out = ST.plan_loop(ctx, write=True)
    assert out.startswith("EDL v3 -> v4"), out
    ins = ctx.edl["inserts"]
    assert len(ins) == 1 and ins[0]["at_output_s"] == 20.0 and ins[0]["mute"]
