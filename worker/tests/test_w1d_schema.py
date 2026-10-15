"""Track 1D: the accent / strobe / junction members, their flash reading and
the apply_edit_batch path (INTERFACES_W1.md §3.4, §5).

Every authoring rule lives in the fx_schemas member, so a department verb
and a stale MCP client's apply_edit_batch are judged identically. These
tests write and remove each 1D device through ``edit_batch.apply_batch``
(the function both paths run), refuse the bad shapes with their reason, and
pin the flash hook flash_safety reads.
"""

import copy
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER = os.path.dirname(HERE)
sys.path.insert(0, WORKER)

import edit_batch                                        # noqa: E402
import flash_safety                                      # noqa: E402
import schemas                                           # noqa: E402
from fx_schemas import accents as A                      # noqa: E402

DUR = 30.0
BASE = {"keep": [[0.0, 10.0], [12.0, 30.0]],
        "motion": [{"id": "m1", "template": "word_slam", "start": 6.0,
                    "end": 9.0, "params": {"text": "FLYING"}}],
        "effects": {"picture_cards": [{"id": "pc1", "start": 14.0,
                                       "end": 20.0}]}}
KEYS = {"media/p1/variant_thermal.png", "media/p1/a.png"}


def _base():
    return schemas.validate_edl(copy.deepcopy(BASE), DUR).model_dump()


def _up(item):
    return {"action": "upsert", "layer": "fx", "id": item["id"],
            "value": item}


OK = [
    {"id": "n1", "type": "accent", "stage": "over_type", "start": 6.0,
     "end": 6.04, "kind": "negative", "device": "accent_negative",
     "bind": {"on": "stress", "ref": 12, "rhythm": "speech"}},
    {"id": "w1", "type": "accent", "stage": "pre_captions", "start": 16.0,
     "end": 16.04, "kind": "white_hit", "decay_f": 3,
     "region": {"shape": "card"}, "device": "accent_white_hit"},
    {"id": "c1", "type": "accent", "stage": "pre_captions", "start": 9.0,
     "end": 9.08, "kind": "colour_hit", "decay_f": 2, "colour": "#FF2020"},
    {"id": "b1", "type": "accent", "stage": "pre_captions", "start": 17.0,
     "end": 17.08, "kind": "box_invert", "bands": 6, "seed": 7,
     "device": "accent_box_invert"},
    {"id": "r1", "type": "accent", "stage": "pre_captions", "start": 3.0,
     "end": 3.08, "kind": "rgb_slice", "bands": 8},
    {"id": "g1", "type": "accent", "stage": "pre_captions", "start": 7.0,
     "end": 7.04, "kind": "negative",
     "region": {"shape": "glyph", "ref": "m1"}},
    {"id": "s1", "type": "strobe", "stage": "pre_captions", "start": 22.0,
     "end": 22.4, "device": "strobe_frames", "fit": "register",
     "pattern": "pingpong",
     "frames": [{"source": {"kind": "asset",
                            "asset_key": "media/p1/variant_thermal.png"},
                 "hold_f": 2},
                {"source": {"kind": "source", "src_t": 25.0}, "hold_f": 2,
                 "negate": True},
                {"source": {"kind": "program"}, "hold_f": 1},
                {"source": {"kind": "colour", "colour": "accent"}}]},
    {"id": "j1", "type": "junction", "stage": "junction", "at": 10.0,
     "style": "box_steps", "duration_s": 0.8, "count": 3,
     "device": "junction_box_steps"},
    {"id": "j2", "type": "junction", "stage": "junction", "at": 10.0,
     "style": "letter_window", "duration_s": 0.8,
     "origin": {"kind": "glyph", "ref": "m1"},
     "device": "junction_letter_window"},
    {"id": "j3", "type": "junction", "stage": "junction", "at": 10.0,
     "style": "zoom_blur", "origin": {"kind": "point", "point": [0.3, 0.4]}},
    {"id": "j4", "type": "junction", "stage": "junction", "at": 10.0,
     "style": "hit_flash", "colour": "accent", "device": "junction_hit_flash"},
]

BAD = [
    ({"id": "x1", "type": "accent", "stage": "pre_captions", "start": 1.0,
      "end": 1.04, "kind": "negative", "decay_f": 3}, "decay_f belongs"),
    ({"id": "x2", "type": "accent", "stage": "world", "start": 1.0,
      "end": 1.04, "kind": "negative"}, "runs at"),
    ({"id": "x3", "type": "accent", "stage": "pre_captions", "start": 1.0,
      "end": 1.04, "kind": "negative", "intensity": 0.0}, "intensity 0"),
    ({"id": "x4", "type": "accent", "stage": "pre_captions", "start": 1.0,
      "end": 1.04, "kind": "mono", "bands": 4}, "bands belong"),
    ({"id": "x5", "type": "junction", "stage": "junction", "at": 10.0,
      "style": "iris", "start": 9.0, "end": 11.0}, "names its cut by at"),
    ({"id": "x6", "type": "junction", "stage": "junction", "at": 10.0,
      "style": "iris", "origin": {"kind": "glyph", "ref": "nope"}},
     "not in this edit"),
    ({"id": "x7", "type": "junction", "stage": "junction", "at": 10.0,
      "style": "iris", "count": 4}, "count belongs"),
    ({"id": "x8", "type": "strobe", "stage": "pre_captions", "start": 1.0,
      "end": 1.4, "frames": [{"source": {"kind": "previous"}}]},
     "a strobe frame is a still"),
    ({"id": "x9", "type": "strobe", "stage": "pre_captions", "start": 1.0,
      "end": 1.4, "frames": [{"source": {"kind": "asset",
                                         "asset_key": "media/other/x.png"}}]},
     "Use media attached"),
    ({"id": "x10", "type": "junction", "stage": "junction", "at": 10.0,
      "style": "datamosh", "device": "junction_datamosh"}, "dark"),
    ({"id": "x11", "type": "accent", "stage": "pre_captions", "start": 1.0,
      "end": 1.04, "kind": "negative", "device": "junction_iris"},
     "writes fx type junction"),
    ({"id": "x12", "type": "strobe", "stage": "pre_captions", "start": 1.0,
      "end": 1.4, "frames": [{"source": {"kind": "source", "src_t": 99}}]},
     "past the end"),
    ({"id": "x13", "type": "accent", "stage": "pre_captions", "start": 1.0,
      "end": 1.04, "kind": "negative", "at": 1.0}, "not at"),
]


def test_1d_registers_its_three_types_live():
    assert A.LIVE is True
    assert {"accent", "strobe", "junction"} <= set(schemas.FX_TYPES)
    assert {"accent", "strobe", "junction"} <= \
        set(schemas.FX_AUTHORABLE_TYPES)


@pytest.mark.parametrize("item", OK, ids=[i["id"] for i in OK])
def test_every_1d_device_is_written_and_removed_through_apply_edit_batch(
        item):
    base = _base()
    sig = schemas.edl_signature(base)
    out = edit_batch.apply_batch(base, [_up(copy.deepcopy(item))], DUR, KEYS)
    got = next(i for i in out["fx"] if i["id"] == item["id"])
    assert got["type"] == item["type"]
    assert f"[{item['id']}]" in schemas.describe_edl(out, DUR)
    back = edit_batch.apply_batch(out, [{"action": "remove", "layer": "fx",
                                         "id": item["id"]}], DUR, KEYS)
    assert schemas.edl_signature(back) == sig


def test_all_1d_devices_in_one_batch_and_the_flash_reading():
    base = _base()
    out = edit_batch.apply_batch(base, [_up(copy.deepcopy(i)) for i in OK],
                                 DUR, KEYS)
    events = flash_safety.flash_events(out)
    by = {}
    for e in events:
        by.setdefault(e["id"], []).append(e["t"])
    assert by["n1"] == [6.0] and by["w1"] == [16.0] and by["c1"] == [9.0]
    assert by["b1"] == [17.0]                 # seeded boxes: full frame
    assert "r1" not in by                     # re-colouring: no flash
    assert by["g1"] == [7.0]     # a glyph's size is unknown here: counted
    assert by["j4"] == [10.0]                 # hit_flash on the cut
    assert len(by["s1"]) >= 6                 # every strobe image change
    assert all(10.0 - 0.4 <= t <= 10.4 for t in by["j1"])


@pytest.mark.parametrize("item, match", BAD, ids=[b[0]["id"] for b in BAD])
def test_bad_1d_shapes_are_refused_with_their_reason(item, match):
    with pytest.raises(Exception) as exc:
        edit_batch.apply_batch(_base(), [_up(item)], DUR, KEYS)
    assert match in str(exc.value), str(exc.value)


def test_a_misspelled_field_is_refused():
    item = dict(OK[0], kind="negative")
    item["colur"] = "white"
    with pytest.raises(ValueError, match="Unknown field|Extra inputs"):
        edit_batch.apply_batch(_base(), [_up(item)], DUR, KEYS)


def test_flash_times_by_kind_region_and_stage():
    mk = lambda **kw: A.AccentFx.model_validate(dict(  # noqa: E731
        {"id": "a", "stage": "pre_captions", "start": 2.0, "end": 2.04,
         "kind": "negative"}, **kw))
    assert mk().flash_times() == [2.0]
    assert mk(kind="mono").flash_times() == []
    assert mk(kind="posterize").flash_times() == []
    small = {"shape": "rect", "box": [0.4, 0.4, 0.6, 0.6]}       # 4%
    big = {"shape": "rect", "box": [0.0, 0.0, 1.0, 0.6]}         # 60%
    assert mk(region=small).flash_times() == []
    assert mk(region=big).flash_times() == [2.0]
    assert mk(region={"shape": "card"}).flash_times() == [2.0]
    assert mk(region=dict(small, invert=True)).flash_times() == [2.0]
    assert mk(kind="box_invert").flash_times() == [2.0]
    assert mk(kind="box_invert", region=small).flash_times() == []
    j = A.JunctionFx.model_validate({"id": "j", "stage": "junction",
                                     "at": 5.0, "style": "dissolve"})
    assert j.flash_times() == []
    # a matte cut's substitute rung is a white flash on the cut: counted
    assert j.model_copy(update={"style": "matte_cut"}).flash_times() == [5.0]


def test_strobe_order_and_schedule_are_deterministic_and_exact():
    assert A.strobe_order(3, "sequence", None, 7) == [0, 1, 2, 0, 1, 2, 0]
    assert A.strobe_order(3, "pingpong", None, 6) == [0, 1, 2, 1, 0, 1]
    r1 = A.strobe_order(5, "random", 7, 40)
    assert r1 == A.strobe_order(5, "random", 7, 40)
    assert all(a != b for a, b in zip(r1, r1[1:]))
    assert r1 != A.strobe_order(5, "random", 8, 40)
    frames = [{"hold_f": 2}, {"hold_f": 1}, {"hold_f": 3}]
    sched = A.strobe_schedule(frames, "sequence", None, 13)
    assert sum(n for _i, _a, n in sched) == 13
    assert [(i, n) for i, _a, n in sched[:3]] == [(0, 2), (1, 1), (2, 3)]
    starts = [a for _i, a, _n in sched]
    assert starts == sorted(starts) and starts[0] == 0


def test_box_steps_land_inside_the_window():
    shares = A.box_step_shares(3)
    assert shares[0] < shares[1] < shares[2] < shares[-1] == 1.0
    lands = A.box_step_frames(4, 24)
    assert lands == sorted(lands) and 0 < lands[0] and lands[-1] < 24


@pytest.mark.parametrize("count", [None, 2, 5])
def test_box_steps_flash_reading_is_the_rendered_white_fills(count):
    """flash_times names every white fill the compiler draws on a box of a
    quarter of the frame or more — the last, full-frame step included — on
    the frame the mask sequence lands it (at the nominal 30 fps)."""
    import wipe_masks
    item = {"id": "j", "stage": "junction", "at": 10.0, "style": "box_steps",
            "duration_s": 0.8}
    if count:
        item["count"] = count
    times = A.JunctionFx.model_validate(item).flash_times()
    n = 24                                       # 0.8 s at 30 fps
    _reveal, flash, lands = wipe_masks.box_steps(60, 108, n, count or 3,
                                                 (30, 54))
    shares = A.box_step_shares(count)
    peaks = [f for f, s in zip(lands, shares) if s >= A.LOCAL_FLASH_SHARE]
    assert all(flash[f].max() == 255 for f in lands)
    assert times == [round(10.0 - 0.4 + f / 30.0, 4) for f in peaks]
    assert shares[-1] == 1.0 and times[-1] == round(9.6 + lands[-1] / 30, 4)


def test_decay_curve_is_ref_02s_measured_shoulder_and_ends_at_zero():
    # ref 02@13.667 inside its card, measured: .87, .66, .375, .16, .05
    vals = A.decay_strengths(1.0, 5)
    assert [round(v, 2) for v in vals] == [0.89, 0.63, 0.36, 0.15, 0.04]
    for d in range(1, 13):
        v = A.decay_strengths(0.8, d)
        assert len(v) == d and all(0 < x < 0.8 for x in v)
        assert all(b < a for a, b in zip(v, v[1:]))       # monotone
        # the frame after the last decay frame is 0: no closing step
        # larger than the step before it
        if d >= 2:
            assert v[-1] <= v[-2] - v[-1] + 1e-9, v
    assert A.decay_strengths(1.0, 0) == []


def test_the_member_module_imports_only_pydantic_and_the_stdlib():
    import ast
    src = open(A.__file__, encoding="utf-8").read()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names.add(node.module.split(".")[0])
    assert names <= {"math", "typing", "pydantic"}, names


def test_backend_style_load_validates_1d_items():
    """The backend loads schemas.py by path with only backend/ on
    sys.path: the 1D member must validate there too."""
    snippet = r'''
import importlib.util, json, sys, os
sys.path = [p for p in sys.path if not p.rstrip("/").endswith("worker")]
spec = importlib.util.spec_from_file_location("worker_schemas", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["worker_schemas"] = m
spec.loader.exec_module(m)
edl = {"keep": [[0, 10], [12, 20]], "fx": [
  {"id": "a", "type": "accent", "stage": "over_type", "start": 1.0,
   "end": 1.04, "kind": "white_hit", "decay_f": 3},
  {"id": "s", "type": "strobe", "stage": "pre_captions", "start": 2.0,
   "end": 2.4, "frames": [{"source": {"kind": "colour", "colour": "white"}}]},
  {"id": "j", "type": "junction", "stage": "junction", "at": 10.0,
   "style": "iris"}]}
out = m.validate_edl(edl, 20.0).model_dump()
cls = [c for c in m.FX_MEMBERS if c.model_fields["type"].default == "strobe"][0]
times = cls.model_validate([i for i in out["fx"] if i["id"] == "s"][0]).flash_times()
print(json.dumps({"types": sorted(m.FX_TYPES), "n": len(out["fx"]), "strobe": len(times)}))
'''
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, "-c", snippet,
                        os.path.join(WORKER, "schemas.py")],
                       cwd=os.path.join(os.path.dirname(WORKER), "backend")
                       if os.path.isdir(os.path.join(os.path.dirname(WORKER),
                                                     "backend")) else "/",
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert {"accent", "strobe", "junction"} <= set(got["types"])
    assert got["n"] == 3 and got["strobe"] >= 1


def test_the_skills_apply_edit_batch_examples_are_valid_operations():
    """skills/punctuation.md teaches stale MCP clients the exact operations
    ("Through apply_edit_batch"): every one of them must apply as written,
    and its remove must restore the edit."""
    import re
    path = os.path.join(WORKER, "skills", "punctuation.md")
    text = open(path, encoding="utf-8").read()
    section = text.split("## Through apply_edit_batch", 1)[1]
    blocks, cur = [], []
    for line in section.splitlines():
        if line.startswith("    "):
            cur.append(line[4:])
        elif cur:
            blocks.append("\n".join(cur))
            cur = []
    ops = []
    for b in blocks:
        for chunk in re.split(r"\n(?=\{\"action\")", b.strip()):
            ops.append(json.loads(chunk))
    assert len(ops) >= 7
    base = _base()
    sig = schemas.edl_signature(base)
    for op in ops:
        out = edit_batch.apply_batch(base, [op], DUR, KEYS)
        back = edit_batch.apply_batch(out, [{"action": "remove", "layer": "fx",
                                             "id": op["id"]}], DUR, KEYS)
        assert schemas.edl_signature(back) == sig, op["id"]
