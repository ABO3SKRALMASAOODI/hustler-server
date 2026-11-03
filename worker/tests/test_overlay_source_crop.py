"""A cutaway can preserve actual uploaded subtitle/logo pixels and their clock."""
import json
import shutil
import subprocess

import numpy as np
import pytest

from schemas import OverlayItem


def overlay(**kw):
    value = dict(id="protected", asset_key="original", kind="video", start=0,
                 duration_s=2, x=.5, y=.8, scale=1, source_start_s=1,
                 source_crop=[0, .7, 1, .9])
    return {**value, **kw}


@pytest.mark.parametrize("rect", [[0, 0, 1, 2], [0, 0, .001, 1], [0, float("nan"), 1, 1]])
def test_invalid_source_regions_are_rejected(rect):
    with pytest.raises(ValueError):
        OverlayItem(**overlay(source_crop=rect))


def test_original_upload_can_supply_a_protected_overlay():
    import agent_tools
    from schemas import default_edl
    from test_insert_fit import _Ctx
    ctx = _Ctx(default_edl(3), duration=3)
    ctx.db.assets["original"] = {"id": 3, "kind": "original", "storage_key": "original",
                                  "duration_s": 3, "meta": {"filename": "uploaded.mp4"}}
    result = agent_tools.add_overlay(ctx, "original", 0, duration_s=2,
        source_start_s=1, source_crop=[0, .7, 1, .9], x=.5, y=.8, scale=1)
    assert result.startswith("EDL v"), result
    assert ctx.latest_edl()["json"]["overlays"][0]["source_crop"] == [0, .7, 1, .9]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="requires FFmpeg")
def test_render_preserves_source_regions_above_broll_at_the_right_time(tmp_path):
    import renderer
    src, cover, out = [tmp_path / name for name in ("source.mp4", "cover.mp4", "result.mp4")]
    for path, colour, duration, filters in [
        (src, "red", 3, "drawbox=x=10:y=10:w=50:h=20:color=lime:t=fill,"
                         "drawbox=x=80:y=135:w=160:h=20:color=white:t=fill:enable='between(t,1.5,2.5)'"),
        (cover, "blue", 2, "null"),
    ]:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color={colour}:s=320x180:r=24:d={duration}", "-vf", filters,
                        "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(path)], check=True)
    edl = {"keep": [[1, 3]], "frame": {"ratio": "source"}, "overlays": [
        dict(id="z_cover", asset_key="cover", kind="video", start=.1, duration_s=1.9, fit="cover"),
        overlay(id="a_logo", x=.15, y=.15, scale=.3, source_crop=[0, 0, .3, .3], z_index=10),
        overlay(z_index=20),
    ]}
    index = {"video": {"duration": 3, "width": 320, "height": 180, "fps": 24, "has_audio": False},
             "words": [], "sentences": [], "silences": [], "shots": []}
    renderer.render_edl(edl, index, str(src), str(out), str(tmp_path), True,
                        suppress_outro=True, asset_locals={"original": str(src), "cover": str(cover)})
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(out)]))
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    width, height = stream["width"], stream["height"]
    assert abs(float(probe["format"]["duration"]) - 2) < .15
    for at, caption_visible in ((.2, False), (.7, True)):
        raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(at), "-i", str(out),
            "-frames:v", "1", "-pix_fmt", "rgb24", "-threads", "1", "-f", "rawvideo", "-"])
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
        def pixel(x, y):
            return frame[int(height*y), int(width*x)].astype(int)
        assert pixel(.5, .4)[2] > 180  # The blue cutaway remains visible.
        r, g, b = pixel(.1, .1)
        assert g > r + 80 and g > b + 80  # Original green branding survives.
        r, g, b = pixel(.5, .8)
        if caption_visible:
            assert min(r, g, b) > 200
        else:
            assert r > 180 and g < 80 and b < 80


def test_original_excerpt_can_precede_the_full_episode_without_losing_it():
    import agent_tools
    from schemas import default_edl
    from timeline import program_blocks
    from test_insert_fit import _Ctx
    ctx = _Ctx(default_edl(3700), duration=3700)
    ctx.db.assets['original'] = {'id': 3, 'kind': 'original', 'storage_key': 'original',
        'duration_s': 3700, 'meta': {'filename': 'episode.mp4'}}
    result = agent_tools.insert_media(ctx, 'original', 0, duration_s=11, clip_start_s=16, allow_repeat=True)
    assert result.startswith('EDL v'), result
    edl = ctx.latest_edl()['json']
    assert edl['keep'] == [[0.0, 3700.0]]
    blocks = program_blocks(edl)
    assert blocks[0]['kind'] == 'insert' and blocks[0]['clip_start_s'] == 16
    assert blocks[1]['kind'] == 'footage' and blocks[1]['src_start'] == 0
