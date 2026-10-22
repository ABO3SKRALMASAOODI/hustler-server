"""Brand integrity with the REAL Wave 4 devices (owner rule 4; the AT-0B-5
pattern, as test_w1_brand does for Wave 1).

One free-tier preview (end card on) carries every Wave 4 compiler that
needs no sidecar: time echo / posterize / reverse (world), a push through a
word and a Ken Burns hold on a main-video still (depth, world), a parallax
still with no depth measured (its Ken Burns substitute, screen) and two
cutaways — a clip and a still (broll, screen). Against the same program
without them: the end card is md5-identical frame for frame and the
watermark robot's opaque pixels are identical, while the picture around it
changed in every device window.
"""
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import w0b_probe                                              # noqa: E402
from test_w0b_render import (_base, _case, _mark_opaque,     # noqa: E402,F401
                             _yuv, work)

CREDIT = {"provider": "owner", "license": "Public domain", "title": "t"}


def _devices():
    # no ``device`` field: the 8 s probe program's +30 % preview budget
    # would omit most of them (the budget is not what this test pins)
    return [
        {"id": "ec", "type": "time", "stage": "world", "start": 0.4,
         "end": 1.4, "kind": "echo"},
        {"id": "po", "type": "time", "stage": "world", "start": 1.6,
         "end": 2.4, "kind": "posterize", "frames": 3},
        {"id": "rv", "type": "time", "stage": "world", "start": 2.6,
         "end": 3.4, "kind": "reverse"},
        {"id": "pt", "type": "depth", "stage": "world", "start": 3.5,
         "end": 4.3, "kind": "push_through", "text": "NOW"},
        {"id": "kb", "type": "depth", "stage": "world", "start": 4.4,
         "end": 5.2, "kind": "ken_burns",
         "source": {"kind": "source", "src_t": 6.0},
         "move": {"path": "drift_left", "amount": 0.4}},
        {"id": "br", "type": "broll", "stage": "screen", "start": 5.3,
         "end": 6.3, "source": {"kind": "asset",
                                "asset_key": "media/7/b.mp4"},
         "grain": 0.3, "credit": CREDIT},
        {"id": "px", "type": "depth", "stage": "screen", "start": 6.4,
         "end": 7.1, "kind": "parallax", "source": {
             "kind": "asset", "asset_key": "media/7/p.png"}},
        {"id": "bs", "type": "broll", "stage": "screen", "start": 7.2,
         "end": 7.9, "media": "still", "fit": "frame", "source": {
             "kind": "asset", "asset_key": "media/7/p.png"},
         "credit": CREDIT},
    ]


@pytest.fixture(scope="module")
def brand(work):
    d = work["dir"]
    clip = os.path.join(d, "b.mp4")
    still = os.path.join(d, "p.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=480x270:r=25:d=3", "-vf", "hue=h=120",
                    "-pix_fmt", "yuv420p", clip], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "mandelbrot=s=640x360", "-frames:v", "1", still],
                   check=True)
    base = _base(music=False)
    locs = {"media/7/b.mp4": clip, "media/7/p.png": still}
    cases = [_case(work, "plain", base, want_wm=True, dump_frames=[30, 140],
                   asset_locals=locs),
             _case(work, "w4", dict(base, fx=_devices()), want_wm=True,
                   dump_frames=[30, 140], asset_locals=locs)]
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        return w0b_probe.run(cases, os.path.join(d, "w4brand"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old


def test_every_w4_compiler_renders_and_the_end_card_is_untouched(brand):
    a, b = brand["plain"], brand["w4"]
    assert a["error"] is None and b["error"] is None, (a["error"], b["error"])
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    assert b["frames"][200:] == a["frames"][200:]          # the card, bit-exact
    rungs = {r["id"]: r["rung"] for r in b["report"]}
    assert set(rungs) >= {"ec", "po", "rv", "pt", "kb", "br", "px", "bs"}
    assert all(v != "omit" for v in rungs.values()), rungs
    for it in _devices():
        f0, f1 = int(round(it["start"] * 25)), int(round(it["end"] * 25))
        changed = [i for i in range(f0, f1)
                   if a["frames"][i] != b["frames"][i]]
        assert len(changed) >= (f1 - f0) // 2, it["id"]


def test_the_watermark_robot_is_drawn_after_every_w4_stage(brand):
    W, H = 270, 480
    m = _mark_opaque(W, H)
    assert m.sum() > 40
    cm = m[::2, ::2] & m[1::2, ::2] & m[::2, 1::2] & m[1::2, 1::2]
    inner = (cm & np.roll(cm, 1, 0) & np.roll(cm, -1, 0)
             & np.roll(cm, 1, 1) & np.roll(cm, -1, 1))
    for k in ("30", "140"):
        a = _yuv(brand["plain"]["dumps"][k], W, H)
        b = _yuv(brand["w4"]["dumps"][k], W, H)
        assert np.abs(a[0] - b[0])[m].max() == 0
        if inner.any():
            assert np.abs(a[1] - b[1])[inner].max() == 0
            assert np.abs(a[2] - b[2])[inner].max() == 0
