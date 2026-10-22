"""Brand integrity with the REAL Wave 1 devices at every stage (owner rule 4;
the AT-0B-5 pattern on the merged engine, not the probe type).

One free-tier preview (end card on) carries a device at every creative
stage — a mono grade window, a subject fill (its full-frame substitute: no
matte here), a thermal finish (world); a region negate (screen); a negative
and a colour strobe (pre_captions); a whole-frame negative and a CRT finish
(over_type); a column wipe (junction). Against the same program without
them: the end card is md5-identical frame for frame and the watermark
robot's opaque pixels are identical, while the picture around it changed.
"""
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import w0b_probe                                              # noqa: E402
from test_w0b_render import (_base, _case, _mark_opaque,     # noqa: E402,F401
                             _yuv, work)


def _devices(n_s):
    return [
        {"id": "gw", "type": "grade_window", "stage": "world", "start": 0.0,
         "end": 3.0, "saturation": 0.0, "ramp_out_f": 3},
        {"id": "sx", "type": "subject", "stage": "world", "start": 2.4,
         "end": 2.48, "kind": "fill", "colour": "white", "decay_f": 2,
         "device": "subject_fill_flash"},
        {"id": "th", "type": "finish", "stage": "world", "start": 5.0,
         "end": 5.6, "kind": "thermal"},
        {"id": "rg", "type": "region", "stage": "screen", "start": 0.0,
         "end": n_s, "effect": "negate",
         "region": {"shape": "rect", "box": [0.0, 0.0, 0.4, 0.3]}},
        {"id": "ac", "type": "accent", "stage": "pre_captions", "start": 1.0,
         "end": 1.04, "kind": "negative"},
        {"id": "st", "type": "strobe", "stage": "pre_captions", "start": 6.0,
         "end": 6.32, "frames": [
             {"source": {"kind": "colour", "colour": "#e8202a"},
              "hold_f": 2},
             {"source": {"kind": "colour", "colour": "white"},
              "hold_f": 2}]},
        {"id": "ot", "type": "accent", "stage": "over_type", "start": 0.0,
         "end": n_s, "kind": "negative"},
        {"id": "crt", "type": "finish", "stage": "over_type", "start": 6.5,
         "end": 7.5, "kind": "crt"},
        {"id": "jn", "type": "junction", "stage": "junction", "at": 4.0,
         "style": "columns", "count": 6, "duration_s": 0.4},
    ]


@pytest.fixture(scope="module")
def brand(work):
    base = _base(music=False)
    cases = [_case(work, "plain", base, want_wm=True, dump_frames=[60, 150]),
             _case(work, "every", dict(base, fx=_devices(8.0)), want_wm=True,
                   dump_frames=[60, 150])]
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        return w0b_probe.run(cases, os.path.join(work["dir"], "w1brand"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old


def test_every_w1_stage_renders_and_the_end_card_is_untouched(brand):
    a, b = brand["plain"], brand["every"]
    assert a["error"] is None and b["error"] is None, (a["error"], b["error"])
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    assert b["frames"][200:] == a["frames"][200:]          # the card, bit-exact
    changed = [i for i in range(200) if a["frames"][i] != b["frames"][i]]
    assert len(changed) == 200                             # every frame had fx
    rungs = {r["id"]: r["rung"] for r in b["report"]}
    assert set(rungs) >= {"gw", "th", "rg", "ac", "st", "ot", "crt", "jn"}
    assert all(v != "omit" for k, v in rungs.items() if k != "sx"), rungs


def test_the_watermark_robot_is_drawn_after_every_w1_stage(brand):
    W, H = 270, 480
    m = _mark_opaque(W, H)
    assert m.sum() > 40
    cm = m[::2, ::2] & m[1::2, ::2] & m[::2, 1::2] & m[1::2, 1::2]
    # chroma is subsampled with neighbours: a chroma sample on the mark's
    # rim reads the picture beside it (a red strobe frame there moves it by
    # up to 3 levels, as any footage under the mark does). The mark's
    # interior chroma — one more sample in — and its luma are exact.
    inner = (cm & np.roll(cm, 1, 0) & np.roll(cm, -1, 0)
             & np.roll(cm, 1, 1) & np.roll(cm, -1, 1))
    for k in ("60", "150"):
        a = _yuv(brand["plain"]["dumps"][k], W, H)
        b = _yuv(brand["every"]["dumps"][k], W, H)
        assert np.abs(a[0] - b[0])[m].max() == 0
        if inner.any():
            assert np.abs(a[1] - b[1])[inner].max() == 0
            assert np.abs(a[2] - b[2])[inner].max() == 0
        assert np.abs(a[1] - b[1])[cm].max() <= 6
        outside = ~m
        outside[:60, :] = False
        assert np.abs(a[0] - b[0])[outside].mean() > 20
