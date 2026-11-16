"""A dissolve on a cut baked into the upload (p5873, ticket 77, 16 Nov 2026).

"edit the transitions to make it look more elevated" on a 35 s, 11-shot
teaser got a 1 s dissolve on every join, and every join ghosted: with no
footage past a baked cut, each side's last/first half second was played at
HALF SPEED across the whole window and laid over the other, so two slowed
pictures double-exposed for a second and every frame slid off its beat.

The outcome proved here: each side keeps its own frames on their own times
and holds its edge frame only while it is the fainter picture, so the
picture that dominates is always the one the plain cut shows at that frame;
and set_junction measures what the windows do to the film."""

import copy
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import renderer                                            # noqa: E402
import wipe_masks                                          # noqa: E402
from tools_ext import punctuation_tools as P               # noqa: E402
from test_w1d_tools import Ctx                             # noqa: E402


def _moving_montage(path):
    """Two moving shots in ONE file (a cut baked in at 3.0 s)."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=3",
         "-f", "lavfi", "-i",
         "testsrc=s=320x180:r=25:d=3,hue=h=120",
         "-f", "lavfi", "-i", "sine=f=330:sample_rate=48000:d=6",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
         "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-qp", "0",
         "-pix_fmt", "yuv420p", "-t", "6", path], check=True)
    return path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_dissolve_on_a_baked_cut_keeps_every_picture_on_its_own_time(
        tmp_path, monkeypatch):
    from w1d_support import IDX, frames, lossless, render
    src = _moving_montage(str(tmp_path / "teaser.mp4"))
    lossless(monkeypatch)
    idx = dict(IDX, shots=[{"id": 0, "start": 0.0, "end": 3.0},
                           {"id": 1, "start": 3.0, "end": 6.0}])
    base = {"keep": [[0.0, 3.0], [3.0, 6.0]], "captions": {"enabled": False}}
    hard_out = str(tmp_path / "hard.mp4")
    render(base, src, hard_out, idx=idx, duration=6.0)
    edl = dict(copy.deepcopy(base), fx=[{
        "id": "j1", "type": "junction", "stage": "junction", "at": 3.0,
        "style": "dissolve", "duration_s": 0.8, "hold": True,
        "device": "junction_dissolve"}])
    out = str(tmp_path / "dissolve.mp4")
    dur, rep = render(edl, src, out, idx=idx, duration=6.0)
    assert dur == pytest.approx(6.0, abs=0.08)
    assert rep and rep[0]["rung"] in ("full", "reduced"), rep
    hard, _ = frames(hard_out)
    got, _ = frames(out)
    hard, got = hard.astype(float), got.astype(float)
    k, h = 75, 10                       # the cut frame; 0.8 s at 25 fps
    N = 2 * h
    err = []
    for j in range(N):
        i = k - h + j
        m = wipe_masks.ease_in_out_sine(wipe_masks.progress(j, N))
        if i < k:   # outgoing on time, incoming held on its first frame
            want = (1 - m) * hard[i] + m * hard[k]
        else:       # incoming on time, outgoing held on its last frame
            want = (1 - m) * hard[k - 1] + m * hard[i]
        err.append(np.abs(got[i] - want).mean())
    assert max(err) < 3.0, [round(e, 2) for e in err]
    # outside the window the film is the plain cut, frame for frame
    assert np.abs(got[: k - h] - hard[: k - h]).mean() < 0.5
    assert np.abs(got[k + h:] - hard[k + h:]).mean() < 0.5


def test_set_junction_measures_what_the_dissolves_cover():
    ctx = Ctx({"keep": [[0.0, 20.0]]})
    ctx.index["shots"] = [{"id": i, "start": i * 4.0, "end": i * 4.0 + 4.0}
                          for i in range(5)]
    out = P.set_junction(ctx, "dissolve", duration=1.0)
    assert out.startswith("EDL"), out
    line = next(x for x in out.splitlines() if x.startswith("MEASURED"))
    # 4 joins x 1 s of two pictures at once on a 20 s film
    assert "4.0 s of the 20.0 s film (20%)" in line
    assert "shortest shot beside one is 4.0 s" in line
    assert "baked into the upload" in line
    # the edit itself is the model's: written as asked
    assert {j["duration_s"] for j in ctx.edl["fx"]} == {1.0}


def test_moving_styles_still_spread_and_blends_hold():
    assert "dissolve" in renderer.BLEND_JUNCTION_STYLES
    assert "slide" not in renderer.BLEND_JUNCTION_STYLES
    assert set(renderer.BLEND_JUNCTION_STYLES) == set(P._BLEND_STYLES)
