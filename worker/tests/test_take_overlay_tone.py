"""Final review of 27 Nov 2026 (#1830 follow-up): the collage spine's photo
fills are overlays of the take itself, and the Director's lowering turned
the film's keyed look off whenever the take appeared as an overlay, so every
collage film whose spine window moved lost its keyed tone.

The render now grades a take shown full-frame as a picture (the main upload,
fit cover or picture) with the very keyed grade windows the footage under it
gets, so the film stays one colour and the lowering keeps the keyed look.
"""
import shutil
import subprocess

import numpy as np
import pytest

import renderer
import schemas

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="ffmpeg required")

WARM = {"id": "look.key", "type": "grade_window", "stage": "screen",
        "start": 0.0, "end": 4.0, "temperature": 0.9, "tint": 0.4}
TAKE = {"id": "pn.s1", "asset_key": "main", "kind": "video", "start": 1.0,
        "duration_s": 2.0, "fit": "cover", "source_start_s": 1.0}


def test_a_take_overlay_takes_the_keyed_windows_and_a_cutaway_does_not():
    assert renderer.takes_tone(TAKE)
    assert not renderer.takes_tone(dict(TAKE, asset_key="u/1/b.mp4"))
    assert not renderer.takes_tone(dict(TAKE, fit="contain"))
    edl = {"fx": [WARM, dict(WARM, id="later", start=2.5, end=4.0),
                  {"id": "plain", "type": "grade_window", "start": 0.0,
                   "end": 4.0, "saturation": 0.8}]}
    assert [w["id"] for w in renderer.take_tone_windows(edl, 1.0, 2.0)] == \
        ["look.key"]


def _frame(path, t, size=(160, 90)):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={size[0]}:{size[1]},format=rgb24", "-f", "rawvideo",
         "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(size[1], size[0], 3) \
        .astype(np.int16)


@needs_ffmpeg
def test_the_take_shown_as_a_picture_is_one_colour_with_the_take(tmp_path):
    src = str(tmp_path / "take.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=320x180:r=25:d=4,trim=end_frame=1,loop=loop=-1:size=1,"
         "trim=duration=4,setpts=N/25/TB", "-f", "lavfi", "-i",
         "sine=frequency=330:sample_rate=48000:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True, capture_output=True)
    index = {"words": [], "sentences": [], "silences": [],
             "video": {"duration": 4.0, "width": 320, "height": 180,
                       "fps": 25.0}}
    outs = {}
    for name, overlays in (("base", []), ("take", [TAKE])):
        edl = schemas.validate_edl({
            "keep": [[0.0, 4.0]], "frame": {"ratio": "source",
                                            "mode": "crop"},
            "fx": [WARM], "overlays": overlays}, 4.0).model_dump()
        out = str(tmp_path / f"out_{name}.mp4")
        work = tmp_path / name
        work.mkdir()
        renderer.render_edl(edl, index, src, out, str(work), preview=True,
                            asset_locals={"main": src})
        outs[name] = out
    plain = _frame(src, 1.6)
    base, take = _frame(outs["base"], 1.6), _frame(outs["take"], 1.6)
    graded = np.abs(base - plain).mean()
    assert graded > 3, "the keyed window grades the take"
    # the overlay of the take shows the same moment in the same colour
    assert np.abs(take - base).mean() < graded / 2, \
        (np.abs(take - base).mean(), graded)


def test_streamed_cutaways_stop_before_a_take_that_takes_the_tone():
    import overlay_stream
    items = [dict(TAKE, id=f"pn.{i}", start=float(i), duration_s=1.0)
             for i in range(10)]
    assert overlay_stream.plan(items, 25.0)              # streamed as before
    assert overlay_stream.plan(items, 25.0,
                               graph_only=renderer.takes_tone) == []
