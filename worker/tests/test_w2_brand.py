"""Brand integrity with the REAL Wave 2 devices (owner rule 4; the AT-0B-5
pattern, as test_w1_brand does for Wave 1). INTERFACES_W2.md §0.1 names
this test: every Wave 2 compiler emits before the end card and the
free-tier watermark.

One free-tier preview (end card on) carries a typeset lockup, a moving
still (full-frame b-roll) and the user's track shaped on its bus (a
low-pass intro, a stutter, the held breath before a drop). Against the same
program without them: the end card is md5-identical frame for frame, the
music under it is never cut or filtered by the arrangement, and the watermark robot's opaque pixels are identical,
while the picture and the music around them changed.
"""
import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import motion_engine                                          # noqa: E402
import w0b_probe                                              # noqa: E402
from test_w0b_render import (_base, _case, _mark_opaque,     # noqa: E402,F401
                             _yuv, work)

needs_engine = pytest.mark.skipif(not motion_engine.available(),
                                  reason="needs the motion engine")
IMG_KEY = "w2brand/still.png"


def _still(path):
    from PIL import Image
    y, x = np.mgrid[0:540, 0:960]
    rgb = np.stack([(x * 255 // 960), (y * 255 // 540),
                    np.full_like(x, 180)], -1).astype(np.uint8)
    Image.fromarray(rgb).save(path)
    return path


def _w2(edl):
    music = [dict(m, provenance="user_upload") for m in edl["music"]]
    motion = [
        {"id": "ts1", "template": "typeset", "start": 0.4, "end": 3.4,
         "layer": "above_captions",
         "params": {"text": "the voice made *visible*",
                    "voice": "condensed_poster", "layout": "auto",
                    "depth": "front", "verb": "auto", "seed": 3}},
        {"id": "sm1", "template": "still_motion", "start": 5.0, "end": 7.4,
         "layer": "below_captions",
         "params": {"image": IMG_KEY, "path": "push_in", "amount": 0.2}},
    ]
    fx = [
        {"id": "af", "type": "arrange", "stage": "music_bus", "kind":
         "filter", "music_id": "m1", "start": 0.0, "end": 2.0,
         "filter": "lowpass", "from_hz": 300.0, "to_hz": 16000.0,
         "open_s": 0.1},
        {"id": "as", "type": "arrange", "stage": "music_bus", "kind":
         "stutter", "music_id": "m1", "start": 3.0, "end": 3.5,
         "slice": "1/16"},
        {"id": "ah", "type": "arrange", "stage": "music_bus", "kind":
         "hold", "music_id": "m1", "start": 7.4, "end": 7.9},
    ]
    return dict(edl, music=music, motion=motion, fx=fx)


@pytest.fixture(scope="module")
def brand(work):
    base = _base(music=True)
    base["music"] = [dict(m, provenance="user_upload")
                     for m in base["music"]]
    img = _still(os.path.join(work["dir"], "still.png"))
    bw = _w2(base)
    bw["fx"] = [{"id": "gw", "type": "grade_window", "stage": "world",
                 "start": 4.5, "end": 7.0, "saturation": 0.0,
                 "ramp_out_f": 2}]
    cases = [_case(work, "plain", base, want_wm=True, dump_frames=[30, 150]),
             _case(work, "w2", _w2(base), want_wm=True,
                   dump_frames=[30, 150]),
             _case(work, "bw_still", bw, want_wm=True, dump_frames=[150])]
    for c in cases:
        c["asset_locals"] = dict(c["asset_locals"], **{IMG_KEY: img})
    old = os.environ.get("OUTRO_ON_PREVIEW")
    os.environ["OUTRO_ON_PREVIEW"] = "1"
    try:
        res = w0b_probe.run(cases, os.path.join(work["dir"], "w2brand"))
    finally:
        if old is None:
            os.environ.pop("OUTRO_ON_PREVIEW", None)
        else:
            os.environ["OUTRO_ON_PREVIEW"] = old
    res["_dir"] = os.path.join(work["dir"], "w2brand")
    return res


def _pcm(path, ss):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{ss:.3f}", "-i",
                          path, "-map", "0:a:0", "-ac", "1", "-ar", "48000",
                          "-f", "s16le", "-"], capture_output=True,
                         check=True).stdout
    return np.frombuffer(raw, "<i2").astype(np.int32)


@needs_engine
def test_every_w2_device_renders_and_the_end_card_is_untouched(brand):
    a, b = brand["plain"], brand["w2"]
    assert a["error"] is None and b["error"] is None, (a["error"], b["error"])
    assert len(a["frames"]) == len(b["frames"]) == 200 + 125
    assert b["frames"][200:] == a["frames"][200:]          # the card, bit-exact
    changed = [i for i in range(200) if a["frames"][i] != b["frames"][i]]
    assert len(changed) >= 100                             # devices drew
    assert a["audio"] != b["audio"]                        # the bus shaped
    # the fixture's track is MONO: aeval on it crashed ffmpeg (8.0, 9.0)
    # and the whole fx graph fell back without the arrangement
    rungs = {r["id"]: r["rung"] for r in b["report"]}
    assert rungs.get("af") == rungs.get("as") == rungs.get("ah") == "full", \
        b["report"]
    # under the card plays the user's music tail (the bed carries into
    # the card, and the whole mix is loudness-normalised, so its samples
    # move with any change to the program's sound — not a Wave 2 effect).
    # What must hold: no arrangement reaches into the card — it plays at
    # the same level, never a hole or a filtered stretch.
    pa = _pcm(os.path.join(brand["_dir"], "plain.mp4"), 8.1)
    pb = _pcm(os.path.join(brand["_dir"], "w2.mp4"), 8.1)
    n = min(len(pa), len(pb))
    assert n > 48000 * 4
    for k in range(0, n - 4800, 4800):
        ra = np.sqrt(np.mean(pa[k:k + 4800].astype(float) ** 2)) + 1.0
        rb = np.sqrt(np.mean(pb[k:k + 4800].astype(float) ** 2)) + 1.0
        assert abs(20 * np.log10(rb / ra)) <= 1.0, (k / 48000.0, ra, rb)


@needs_engine
def test_the_watermark_robot_is_drawn_after_every_w2_device(brand):
    W, H = 270, 480
    m = _mark_opaque(W, H)
    assert m.sum() > 40
    for k in ("30", "150"):
        a = _yuv(brand["plain"]["dumps"][k], W, H)
        b = _yuv(brand["w2"]["dumps"][k], W, H)
        assert np.abs(a[0] - b[0])[m].max() == 0


@needs_engine
def test_a_moving_still_inside_a_bw_section_is_black_and_white(brand):
    """B&W is a device on a moment: a B&W section over an archive still
    (a moving still is full-frame b-roll) greys the still too — W1 shares
    the Look with cutaway overlays; a still left in colour inside the B&W
    stretch was the run report's defect 4 on a Wave 2 device."""
    W, H = 270, 480
    a = _yuv(brand["w2"]["dumps"]["150"], W, H)
    b = _yuv(brand["bw_still"]["dumps"]["150"], W, H)
    rows = slice(H // 4 // 2, H // 2)            # chroma rows under the mark
    assert np.abs(a[1][rows] - 128).mean() > 8   # the still has colour
    assert np.abs(b[1][rows] - 128).mean() < 2   # and is grey in the section
    assert np.abs(b[2][rows] - 128).mean() < 2
