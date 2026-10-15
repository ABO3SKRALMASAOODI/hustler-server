"""Track 1C's page pieces on the motion engine (needs Chromium).

  * ``type_window``: words reveal one word per hard cut at one type size;
    hold/open split a multi-word line for the largest type; ``open`` grows
    one letter until the whole frame is window (alpha everywhere at the
    end), eased and monotone;
  * the stroke-only paint mode (motion/lib/paint.js, the ghost pass) draws
    each glyph's outline in its own colour and none of its fill, shadow or
    glow, on exactly the letters of the normal render.
"""

import os
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import motion_engine                                       # noqa: E402
import motion_templates                                    # noqa: E402

pytestmark = pytest.mark.skipif(not motion_engine.available(),
                                reason="needs the motion engine")
W, H, FPS = 270, 480, 25.0


def _clip(tmp, params, dur=2.0, template="type_window", extra=None):
    item = {"id": "t", "template": template, "start": 0.0, "end": dur,
            "params": dict(motion_templates.check_params(template, params),
                           **(extra or {}))}
    job = motion_templates.build_job(item, W, H, FPS)
    return motion_engine.render_jobs([job], str(tmp))[0]


def _alpha(clip, k):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", clip.path, "-vf",
         f"select=eq(n\\,{k}),format=rgba,crop=min(iw\\,{clip.w}):"
         f"min(ih\\,{clip.h}):0:0,pad={clip.w}:{clip.h}:0:0:color=black@0",
         "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(clip.h, clip.w, 4)
    full = np.zeros((H, W, 4), np.uint8)
    full[clip.y:clip.y + clip.h, clip.x:clip.x + clip.w] = a
    return full


def test_words_reveal_one_word_per_hard_cut(tmp_path):
    clip = _clip(tmp_path, {"text": "YOU SHOULD KNOW", "reveal": "words"},
                 dur=1.5)
    boxes = []
    for k in (5, 17, 30):           # one frame inside each word's third
        a = _alpha(clip, k)[..., 3] > 128
        ys, xs = np.nonzero(a)
        boxes.append((xs.min(), xs.max(), ys.min(), ys.max()))
    widths = [b[1] - b[0] for b in boxes]
    heights = [b[3] - b[2] for b in boxes]
    assert widths[1] > widths[0] and widths[1] > widths[2]   # SHOULD widest
    assert max(heights) - min(heights) <= 3                  # one type size
    assert widths[1] >= 0.8 * W                              # fills width


def test_open_grows_one_letter_into_the_whole_frame(tmp_path):
    clip = _clip(tmp_path, {"text": "LOOKS LIKE", "reveal": "open",
                            "open_at": 0.4}, dur=2.0)
    area = [(_alpha(clip, k)[..., 3] > 127).mean() for k in range(0, 50, 2)]
    start = area[0]
    assert 0.03 < start < 0.5                     # letters, not a field
    assert area[-1] > 0.995                       # all window at the end
    k_open = int(0.4 * 25 / 2)
    growth = area[k_open + 1:]
    assert all(b >= a - 0.01 for a, b in zip(growth, growth[1:]))
    # eased: the first steps of the opening grow slowly
    assert growth[1] - growth[0] < 0.05
    # two lines: "LOOKS / LIKE" set larger than one line would be
    a0 = _alpha(clip, 0)[..., 3] > 127
    ys, _xs = np.nonzero(a0)
    assert ys.max() - ys.min() > 0.2 * H


def test_stroke_paint_mode_draws_outlines_only(tmp_path):
    p = {"text": "HELLO", "entrance": "pop", "exit": "cut",
         "color": "#FF2020", "y": 0.5}
    full = _clip(tmp_path / "f", p, dur=0.6, template="word_slam")
    # the compositor adds _paint to the RENDER job only (never stored)
    stroke = _clip(tmp_path / "s", p, dur=0.6, template="word_slam",
                   extra={"_paint": {"mode": "stroke", "width": 2.0}})
    a = _alpha(full, 8)
    s = _alpha(stroke, 8)
    glyph = a[..., 3] > 250
    ring = s[..., 3] > 128
    assert glyph.sum() > 500 and ring.sum() > 100
    # the outline is far thinner than the letters and hugs their edges
    assert ring.sum() < 0.6 * glyph.sum()
    core = glyph.copy()
    for _ in range(3):
        core = core & np.roll(core, 1, 0) & np.roll(core, -1, 0) & \
            np.roll(core, 1, 1) & np.roll(core, -1, 1)
    assert (ring & core).sum() < 0.05 * ring.sum()          # hollow
    # in the glyph's own colour (red)
    rgb = s[..., :3][ring].astype(float)
    assert rgb[:, 0].mean() > 150 and rgb[:, 1].mean() < 90
