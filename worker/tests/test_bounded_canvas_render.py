import copy
import shutil
import subprocess

import numpy as np
import pytest

import db
import media
import renderer
import render_plan
from schemas import canvas_edl, validate_edl


def sequence(n=12):
    edl = canvas_edl(fps=24)
    edl["canvas"].update(width=320, height=180)
    edl["inserts"] = [dict(id=f"ins{i}", asset_key=f"clip{i % 3}.mp4",
        kind="video", at_output_s=0, source_start_s=0.25,
        duration_s=0.75 if i % 2 else 1.25,
        mute=(i == 5), rate=1.5 if i == 7 else None)
        for i in range(n)]
    return validate_edl(edl).model_dump()


def test_batches_preserve_complete_order_and_transition_context():
    edl = sequence(62)
    edl["effects"] = {"transition": dict(style="dip_black", duration_s=0.2,
                                         junctions=[0, 3, 30, 60])}
    original = copy.deepcopy(edl)
    batches = list(render_plan.canvas_batches(edl))
    assert len(batches) == 16
    assert max(len(b["edl"]["inserts"]) for b in batches) <= 6
    assert batches[0]["start"] == 0
    assert all(a["end"] == b["start"] for a, b in zip(batches, batches[1:]))
    assert batches[-1]["end"] == sum(i["duration_s"] for i in edl["inserts"])
    # Cut3 crosses a batch boundary. Both sides retain its transition.
    assert 3 in batches[0]["edl"]["effects"]["transition"]["junctions"]
    assert 0 in batches[1]["edl"]["effects"]["transition"]["junctions"]
    assert edl == original


def test_cancelled_batch_never_starts_an_encoder(tmp_path):
    with pytest.raises(db.JobLeaseLost):
        renderer._bounded_canvas_program(sequence(), str(tmp_path), True,
                                          None, lambda: True, None)


def ff(args):
    return subprocess.run(["ffmpeg", "-v", "error", *args],
                          check=True, capture_output=True, timeout=90).stdout


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_phone_frame_rate_batches_do_not_repeat_the_context_clip(tmp_path):
    # Customer 2463: seeking/copying the second batch at 21.43 fps started
    # from the preceding keyframe and emitted 33.411s instead of 26.800s.
    source = tmp_path / "phone.mp4"
    ff(["-f", "lavfi", "-i", "testsrc2=s=160x90:r=21.43:d=11",
        "-f", "lavfi", "-i", "sine=frequency=400:duration=11",
        "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(source)])
    edl = canvas_edl(fps=21.43)
    edl["canvas"].update(width=160, height=90)
    edl["inserts"] = [dict(id=f"ins{i}", asset_key="phone.mp4", kind="video",
        at_output_s=0, duration_s=d) for i,d in enumerate(
            [5.7,5.5,10.1,6.6,7.5,4.8,8.4,6.1,8.4])]
    original = copy.deepcopy(edl)
    composed, local = renderer._bounded_canvas_program(
        edl, str(tmp_path), True, {"phone.mp4": str(source)}, None, None)
    program = local[composed['inserts'][0]['asset_key']]
    assert edl == original
    assert abs(media.duration_of(program) - 63.1) < .15
    samples = ff(["-i", program, "-map", "0:a:0", "-ar", "48000",
                  "-ac", "1", "-f", "s16le", "-"])
    assert abs(len(samples) // 2 - round(63.1 * 48000)) <= 1


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
@pytest.mark.parametrize("style,fractional", [
    ("dip_black", False), ("whip_left", False), ("dip_black", True)])
def test_real_batched_video_keeps_frames_audio_effects_and_global_master(
        tmp_path, monkeypatch, style, fractional):
    assets = {}
    for i, color in enumerate(["red", "green", "blue"]):
        source = tmp_path / f"clip{i}.mp4"
        ff(["-f", "lavfi", "-i", f"color={color}:s=640x360:r=24:d=3",
            "-f", "lavfi", "-i", f"sine=frequency={400+i*300}:duration=3",
            "-c:v", "libx264", "-threads", "2", "-c:a", "aac", str(source)])
        assets[source.name] = str(source)
    edl = sequence()
    if fractional:
        edl["inserts"][3]["duration_s"] = .81
    edl["effects"] = {"grade": "cinematic", "fade_in_s": 0.2,
        "fade_out_s": 0.3, "transition": {"style": style,
        "duration_s": 0.2, "scope": "every_cut"}}
    edl["master"] = {"loudness": "social"}
    original = copy.deepcopy(edl)
    ref_dir, actual_dir = tmp_path / "ref", tmp_path / "actual"
    ref_dir.mkdir(); actual_dir.mkdir()
    ref, actual = ref_dir / "out.mp4", actual_dir / "out.mp4"
    monkeypatch.setattr(renderer, "CANVAS_MAX_DIRECT_INPUTS", 1000)
    renderer.render_edl(edl, {}, None, str(ref), str(ref_dir), True,
                        asset_locals=assets)
    monkeypatch.setattr(renderer, "CANVAS_MAX_DIRECT_INPUTS", 8)
    commands, progress = [], []
    real_run = media.run
    def record(cmd, **kwargs):
        commands.append(cmd)
        kwargs["timeout"] = min(kwargs.get("timeout") or 20, 20)
        return real_run(cmd, **kwargs)
    monkeypatch.setattr(media, "run", record)
    renderer.render_edl(edl, {}, None, str(actual), str(actual_dir), True,
                        asset_locals=assets, progress_cb=progress.append)
    assert edl == original
    assert abs(media.duration_of(ref) - media.duration_of(actual)) < 0.06
    assert progress == sorted(progress)
    graphs = [c[c.index("-filter_complex")+1] for c in commands if "-filter_complex" in c]
    assert len(graphs) == len(edl["inserts"]) + 4
    assert sum("loudnorm=" in g for g in graphs) == 1
    # Each original is prepared once and never shares a graph with another
    # remote source. The batch and final graphs consume only local media.
    source_commands = [c for c in commands if "ffv1" in c]
    assert len(source_commands) == len(edl["inserts"])
    assert all(c.count("-i") == 2 for c in source_commands)  # source + silence
    # Parallelize decoding only after isolating the single original source;
    # local multi-input composition retains its smaller per-input budget.
    assert all(c[c.index("-threads:v") + 1] == "4" for c in source_commands)
    originals = set(assets.values())
    assert all(sum(arg in originals for arg in c) <= 1 for c in commands)
    # The final compositor opens one local program; each local batch has <=6 clips.
    assert max(c.count("-i") for c in commands if "-filter_complex" in c) <= 7
    def video(path):
        return np.frombuffer(ff(["-i", str(path), "-map", "0:v:0", "-pix_fmt",
            "rgb24", "-f", "rawvideo", "-"]), np.uint8).reshape(-1,180,320,3)
    a, b = video(ref), video(actual)
    assert a.shape == b.shape
    # Per-frame comparison includes both sides of the two batch seams, fades
    # and every transition. Allow the extra high-quality intermediate encode.
    aa, bb = a.astype(float), b.astype(float)
    error = np.abs(aa-bb).mean(axis=(1,2,3))
    if fractional:
        # Authored hundredths can fall between delivery frames. A copied
        # batch starts on the next encoded frame; permit one frame of sampling
        # at that seam, but no accumulated timing drift or changed scene.
        error = np.minimum.reduce([error,
            np.abs(np.concatenate([aa[:1], aa[:-1]])-bb).mean(axis=(1,2,3)),
            np.abs(np.concatenate([aa[1:], aa[-1:]])-bb).mean(axis=(1,2,3))])
    assert float(error.max()) < 8, (error.max(), np.argmax(error))
    def audio(path):
        return np.frombuffer(ff(["-i", str(path), "-map", "0:a:0", "-ar", "48000",
            "-ac", "1", "-f", "f32le", "-"]), np.float32)
    a, b = audio(ref), audio(actual)
    duration = sum(i["duration_s"] for i in edl["inserts"])
    base_program = next(actual_dir.glob("canvas_base_*/program.nut"))
    assert abs(len(audio(base_program)) - round(duration*48000)) <= 1
    # Delivery AAC/loudnorm can finish within one video frame plus one AAC
    # packet. The lossless assembly itself must retain every audio sample.
    assert abs(len(b)/48000 - duration) < 1/24 + 1024/48000
    # AAC and atempo can end a source within a small audio packet of its
    # requested boundary. Preserve each audible window and the full program
    # duration instead of accumulating that shortfall at every batch seam.
    cursor = 0.0
    for item in edl["inserts"]:
        t = cursor + item["duration_s"] / 2
        cursor += item["duration_s"]
        first, last = int(t*48000), int((t+.1)*48000)
        if item.get("mute"):
            assert np.max(np.abs(b[first:last])) < .002
            continue
        x = a[first:last]
        correlations = [np.corrcoef(x, b[first+d:last+d])[0,1]
                        for d in range(-120, 121)]
        assert max(correlations) > .99
