import copy
import array
import shutil
import subprocess

import pytest
import renderer
import render_plan
from schemas import canvas_edl, default_edl


def test_organizational_split_keeps_transitions_and_motion_identical():
    edl = default_edl(30)
    edl["effects"] = {"transition": {"style": "dip_black", "scope": "every_cut"}}
    split = copy.deepcopy(edl)
    split["keep"] = [[0, 10], [10, 30]]
    split["split_keep_boundaries"] = [10]
    assert render_plan.canonical_program(split) == render_plan.canonical_program(edl)
    edl = canvas_edl()
    edl["inserts"] = [dict(id="ins1", asset_key="clip.mp4", kind="video",
                          at_output_s=0, duration_s=10, source_start_s=40,
                          rate=2, motion="zoom_in")]
    split = copy.deepcopy(edl)
    first = split["inserts"][0]
    first.update(duration_s=4, split_parent="ins1")
    split["inserts"].append(dict(first, id="ins2", duration_s=6, source_start_s=48))
    assert render_plan.canonical_program(split) == render_plan.canonical_program(edl)


def test_a_real_gap_or_intervening_insert_keeps_the_cut():
    edl = dict(keep=[[0, 10], [10, 30]], split_keep_boundaries=[10],
               inserts=[dict(at_output_s=10, id="ins1", duration_s=2)])
    assert len(render_plan.canonical_program(edl)["keep"]) == 2
    edl["inserts"] = []
    edl["keep"][1][0] = 12
    assert len(render_plan.canonical_program(edl)["keep"]) == 2


def test_saved_fractional_split_keeps_the_original_picture_dependencies():
    from schemas import validate_edl
    original = default_edl(30)
    split = copy.deepcopy(original)
    split['keep'] = [[0, 8.318], [8.318, 30]]
    split['split_keep_boundaries'] = [8.318]
    saved = validate_edl(split, 30).model_dump()
    assert saved['keep'][1][0] == 8.32
    assert render_plan.can_reuse_picture(original, saved)


def test_picture_dependencies_preserve_every_visual_change():
    old = default_edl(30)
    changed = copy.deepcopy(old)
    changed["master"] = {"gain_db": -4}
    assert render_plan.can_reuse_picture(old, changed)
    changed["frame"] = {"ratio": "9:16"}
    assert not render_plan.can_reuse_picture(old, changed)


@pytest.mark.parametrize("canvas,source_start", [(True, 0), (False, 0), (False, 8)])
def test_bounded_insert_inputs_on_both_render_paths(monkeypatch, tmp_path, canvas, source_start):
    edl = canvas_edl() if canvas else default_edl(20)
    if not canvas:
        edl["keep"] = [[source_start, source_start + 2]]
    edl["inserts"] = [dict(id="ins1", asset_key="clip.mp4", kind="video",
                          at_output_s=0, duration_s=2, source_start_s=100, rate=2)]
    monkeypatch.setattr(renderer, "_render_asset_source", lambda *a, **k: "clip.mp4")
    monkeypatch.setattr(renderer.media, "probe", lambda p: dict(
        duration=120, video_duration=120, width=320, height=180, fps=30, has_audio=True, sar=1))
    monkeypatch.setattr(renderer.media, "duration_of", lambda p: 4)
    commands = []
    monkeypatch.setattr(renderer.media, "run", lambda c, **k: commands.append(c))
    renderer.render_edl(edl, {}, "main.mp4", str(tmp_path / "out.mp4"), str(tmp_path), True)
    cmd = commands[-1]
    i = cmd.index("clip.mp4")
    options = cmd[max(0, i-9):i]
    assert options[options.index("-ss") + 1] == "99.000"
    assert options[options.index("-t") + 1] == "6.000"
    graph = cmd[cmd.index("-filter_complex") + 1]
    offset = "100.000" if source_start == 8 else "1.000"
    assert f"trim=start={offset}:" in graph


def test_canvas_audio_only_never_encodes_video(monkeypatch, tmp_path):
    edl = canvas_edl()
    edl["inserts"] = [dict(id="ins1", asset_key="clip.mp4", kind="video",
                          at_output_s=0, duration_s=2, source_start_s=100)]
    monkeypatch.setattr(renderer, "_render_asset_source", lambda *a, **k: "clip.mp4")
    monkeypatch.setattr(renderer.media, "probe", lambda p: {"has_audio": True})
    monkeypatch.setattr(renderer.media, "probe_audio_duration", lambda p: 2)
    commands = []
    monkeypatch.setattr(renderer.media, "run", lambda c, **k: commands.append(c))
    renderer.render_edl(edl, {}, None, str(tmp_path / "audio.m4a"), str(tmp_path), True, audio_only=True)
    cmd = commands[-1]
    assert "[vout]" not in cmd and "-c:v" not in cmd
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "[insv" not in graph and "atrim=start=1.000:end=3.000" in graph


def test_canvas_audio_does_not_fetch_muted_visuals(monkeypatch, tmp_path):
    edl = canvas_edl()
    edl["inserts"] = [dict(id="ins1", asset_key="large-clip.mp4", kind="video",
                          at_output_s=0, duration_s=2, mute=True)]
    def unexpected(*args, **kwargs):
        raise AssertionError("an audio-only change must not fetch muted footage")
    monkeypatch.setattr(renderer, "_render_asset_source", unexpected)
    monkeypatch.setattr(renderer.media, "probe_audio_duration", lambda p: 2)
    monkeypatch.setattr(renderer.media, "run", lambda c, **k: None)
    assert renderer.render_edl(edl, {}, None, str(tmp_path / "audio.m4a"),
                               str(tmp_path), True, audio_only=True) == 2


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("copyts", [False, True])
def test_real_seek_preserves_selected_frames_and_audio(tmp_path, copyts):
    source = str(tmp_path / "source.mkv")
    run = lambda args: subprocess.run(["ffmpeg", "-v", "error", "-threads", "2", *args], check=True, capture_output=True).stdout
    run(["-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=15",
         "-f", "lavfi", "-i", "sine=frequency=731:duration=15:sample_rate=48000",
         "-c:v", "libx264", "-g", "60", "-c:a", "pcm_s16le", source])
    item = dict(kind="video", duration_s=2, source_start_s=11.4, rate=1.5)
    bounded, rebased = render_plan.insert_input(item, source, 30, copy_timestamps=copyts)
    for media_type in ("v", "a"):
        def decoded(options, spec):
            start = spec["source_start_s"]
            trim = "trim" if media_type == "v" else "atrim"
            clock = "setpts" if media_type == "v" else "asetpts"
            graph = f"[{0}:{media_type}]{trim}=start={start}:end={start+3},{clock}=PTS-STARTPTS[out]"
            return run([*( ["-copyts"] if copyts else []), *options,
                        "-filter_complex", graph, "-map", "[out]",
                        "-f", "rawvideo" if media_type == "v" else "s16le", "-"])
        fast, original = decoded(bounded, rebased), decoded(["-i", source], item)
        if media_type == "v":
            assert fast == original
        else:
            # Matroska rounds packet timestamps to milliseconds. Accurate
            # seeking may choose a sample origin within that 1ms tick; it
            # must preserve the waveform, length and sub-millisecond sync.
            a, b = array.array("h", original), array.array("h", fast)
            assert len(a) == len(b)
            assert any(a[1000:10000] == b[1000+d:10000+d]
                       for d in range(-47, 48))


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg tools required")
def test_audio_change_reuses_identical_encoded_video(monkeypatch, tmp_path):
    source, before, after = [str(tmp_path / name) for name in ("source.mp4", "before.mp4", "after.mp4")]
    def run(args):
        return subprocess.run(["ffmpeg", "-v", "error", *args], check=True, capture_output=True).stdout
    run(["-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=4",
         "-f", "lavfi", "-i", "sine=frequency=731:duration=4:sample_rate=48000",
         "-c:v", "libx264", "-c:a", "aac", source])
    monkeypatch.setattr(renderer, "_render_asset_source", lambda key, *a, **k: before if key == "previous.mp4" else source)
    edl = canvas_edl()
    edl["canvas"].update(width=320, height=180)
    edl["inserts"] = [dict(id="ins1", asset_key="source.mp4", kind="video", at_output_s=0, duration_s=2, source_start_s=1)]
    duration = renderer.render_edl(edl, {}, None, before, str(tmp_path), True)
    changed = copy.deepcopy(edl)
    changed["inserts"][0]["mute"] = True
    result = renderer._reuse_picture_with_new_audio(edl, changed,
        {"storage_key": "previous.mp4", "duration_s": duration}, {}, None,
        str(tmp_path), after, preview=True)
    assert result is not None and abs(result - duration) < .04
    def video_hash(path):
        return run(["-i", path, "-map", "0:v:0", "-c", "copy", "-f", "hash", "-hash", "sha256", "-"])
    assert video_hash(before) == video_hash(after)
    def audio_samples(path):
        return array.array("h", run(["-i", path, "-map", "0:a:0", "-f", "s16le", "-"]))
    assert max(audio_samples(before)) > 100
    assert max(audio_samples(after)) < 2
