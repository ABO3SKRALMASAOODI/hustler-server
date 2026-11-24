"""A split with only the underlying interview available keeps that camera on its own clock."""
import subprocess

import numpy as np

from scene import palette, reader, stills


def portrait_frames(path, work):
    asset = dict(kind="video", path=str(path))
    scene = dict(l1=1, sid="portrait", fps=30, dur_f=500, frame=[216,384], seed=2,
                 assets={"main": asset, "own:0": asset}, documentary_cuts=[], documentary_lead_f=3,
                 voices={}, sound=[], spine=[], film={},
                 items=[dict(id="host", k="shot", pic="own:0", f=[0,500], src=[0,500/30],
                             crop=[.335,0,.6514,1])])
    frames = [465, 474, 487]
    edited, log = palette.apply(scene, str(work), [dict(dev="split", id="dr0", f=[464,489], dir="v")])
    return stills(scene, str(work), frames), stills(edited, str(work), frames), log[0]


def test_single_camera_split_preserves_drawn_portrait(tmp_path):
    take = tmp_path / "camera.mp4"
    subprocess.run([reader.FF, "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                    "-t", "17", "-c:v", "libx264", "-preset", "ultrafast", str(take)], check=True)
    original, split, event = portrait_frames(take, tmp_path)
    for frame in original:
        np.testing.assert_array_equal(split[frame], original[frame])
    assert event["landed"] == "portrait" and event["log"]["panels"] == 1


def test_composite_portrait_stays_inside_the_speakers_camera():
    from scene.continuity import portrait_region

    image = np.zeros((100, 180, 3), dtype=np.uint8)
    image[25:75, :90] = 125
    image[80:83, 5:9] = 255  # Show credit below the live feed.
    face = [.15, .30, .15, .27]
    left, top, right, bottom = portrait_region(face, (180, 100), image)
    assert .25 <= top <= face[1] and face[1] + face[3] <= bottom <= .75
    assert left <= face[0] and right >= face[0] + face[2]
    assert np.isclose((right - left) * 180, (bottom - top) * 100)
