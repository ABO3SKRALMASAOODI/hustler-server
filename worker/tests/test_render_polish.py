"""Render polish (judges, round 4, Oct 2026): export-level finishing.

* a click on frame 0 — Thiel's first kept span starts mid-word, so the file
  opened on a step from silence to -0.64: a few-ms fade from zero at the
  programme's TRUE start (and at an end with no card), never on a stitched
  piece, a proof window that does not hold the edge, or an internal join;
* no rate ceiling on finals — animated grain over a 1.9x-upscaled 480p plate
  encoded the Jobs short at 21.6 Mb/s (116 MB for 43 s): CRF under a VBV
  ceiling scaled with the pixel rate;
* a held frame right before a jump cut (Thiel 7.21, 24.10) — a block on the
  block clock one frame longer than its span cloned its last frame: it reads
  the next frame of the SAME shot instead, never across a camera cut.

Run:  python -m pytest tests/test_render_polish.py -q     (from worker/)
"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config                                                 # noqa: E402
import renderer                                               # noqa: E402
from schemas import validate_edl                              # noqa: E402
from timeline import Timeline                                 # noqa: E402

FPS = 30.0
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="ffmpeg not present")
ONE_SHOT = [{"id": 1, "start": 0.0, "end": 60.0}]


def _graph(edl, index=None, **kw):
    edl = validate_edl(edl, 60.0).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return renderer.build_filtergraph(
        edl, 60.0, True, tl, None, [],
        index if index is not None else {"words": [], "video": {}},
        preview=False, W=320, H=180, fps=FPS, src_fps=FPS,
        frame_mode=(edl.get("frame") or {}).get("mode"), **kw)


# ── the programme's edges ────────────────────────────────────────────────

def test_edge_fades_take_only_the_true_edges():
    d = config.PROGRAM_EDGE_FADE_S
    assert 0.002 <= d <= 0.01                    # a de-click, not a fade
    assert renderer.edge_fades(30.0) == (d, d)
    assert renderer.edge_fades(30.0, head=False, tail=False) == (0.0, 0.0)
    assert renderer.edge_fades(30.0, head=True, tail=False) == (d, 0.0)
    # the EDL's own fades already land the edge in silence
    assert renderer.edge_fades(30.0, fade_in=0.5, fade_out=1.0) == (0.0, 0.0)
    # a carried score plays on through the card and fades at its far edge
    assert renderer.edge_fades(30.0, carry_music=True) == (d, 0.0)
    assert renderer.edge_fades(0.01) == (0.0, 0.0)


def test_only_a_graph_that_holds_an_edge_fades_it():
    edl = {"keep": [[1.0, 4.0], [6.0, 9.0]],
           "frame": {"ratio": "9:16", "mode": "crop"}}
    bare = _graph(edl)
    assert "afade=t=in:st=0:d=0.005" not in bare
    whole = _graph(edl, program_edges=(True, True))
    assert "afade=t=in:st=0:d=0.005" in whole
    # on the block clock the sound ends with the blocks: 90 + 90 frames
    assert "afade=t=out:st=5.995000:d=0.005" in whole
    head = _graph(edl, program_edges=(True, False))
    assert "afade=t=in:st=0:d=0.005" in head and "st=5.995000" not in head
    # a programme with an end card already fades its last 0.25 s into it
    carded = _graph(edl, program_edges=(True, True), outro_s=1.0, card_idx=1)
    assert "afade=t=in:st=0:d=0.005" in carded
    assert "d=0.005" not in carded.split("afade=t=in:st=0:d=0.005", 1)[1]
    assert f"d={config.OUTRO_AUDIO_TAIL_FADE_S:.2f}" in carded


def test_render_entry_points_decide_which_edges_a_file_holds():
    import inspect
    src = inspect.getsource(renderer.render_edl)
    assert "program_edges = (not render_fragment, not render_fragment)" in src
    # a proof window restores only the edges it really contains
    proof = inspect.getsource(renderer._render_changed_sections)
    assert "program_edges=(a <= 0.001, b >= duration - 0.001)" in proof
    canvas = inspect.getsource(renderer._render_canvas_edl)
    assert "program_edges=program_edges" in canvas


# ── the export's rate ceiling ────────────────────────────────────────────

def test_finals_are_capped_and_the_cap_scales_with_the_pixel_rate():
    base = config.FINAL_MAXRATE_KBPS
    assert 8000 <= base <= 16000
    assert renderer.final_rate_cap_kbps(1080, 1920, 30) == base
    assert renderer.final_rate_cap_kbps(1080, 1920, 29.97) == pytest.approx(
        base * 0.999, rel=0.002)
    assert renderer.final_rate_cap_kbps(3840, 2160, 60) == 5 * base  # 4K60
    assert renderer.final_rate_cap_kbps(320, 180, 30) == base // 4
    enc = renderer.final_video_encode(1080, 1920, 30)
    i = enc.index("-maxrate")
    assert enc[i + 1] == f"{base}k"
    assert enc[enc.index("-bufsize") + 1] == \
        f"{int(round(base * config.FINAL_BUFSIZE_S))}k"
    assert enc[enc.index("-crf") + 1] == str(config.FINAL_CRF)


def test_the_cap_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(config, "FINAL_MAXRATE_KBPS", 0)
    assert renderer.final_rate_cap_kbps(1080, 1920, 30) is None
    assert "-maxrate" not in renderer.final_video_encode(1080, 1920, 30)


def test_only_finals_over_the_ceiling_are_re_encoded():
    big = {"meta": {}, "width": 1080, "height": 1920, "fps": 29.97,
           "bytes": 116_610_801, "duration_s": 42.876}       # 21.8 Mb/s
    small = dict(big, bytes=20_795_000, duration_s=37.871)  # 4.4 Mb/s
    assert not renderer.finish_current(big, "final")
    assert renderer.finish_current(small, "final")
    assert renderer.finish_current(big, "preview")
    stamped = dict(big, meta={"finish_v": config.FINISH_VERSION})
    assert renderer.finish_current(stamped, "final")
    assert renderer.finish_current({"meta": {}}, "final")    # no size known
    import inspect
    src = inspect.getsource(renderer)
    assert "and finish_current(cached, variant)" in src
    assert "and finish_current(prev_asset, variant)" in src
    assert '"finish_v": (reused_visual_meta.get("finish_v")' in src


# ── a block's last slot ──────────────────────────────────────────────────

def test_same_shot_tail_reads_one_more_frame_only_inside_one_shot():
    idx = {"shots": ONE_SHOT}
    assert renderer.same_shot_tail(4.27, idx, FPS, FPS, 60.0) == \
        pytest.approx(1 / FPS, abs=1e-6)
    # no shot list: a measured cut cannot be told from an edit
    assert renderer.same_shot_tail(4.27, {}, FPS, FPS, 60.0) == 0.0
    # a camera cut just after the span's end (or rounded just before it)
    for c in (4.28, 4.30, 4.25):
        cut = {"shots": [{"id": 1, "start": 0.0, "end": c},
                         {"id": 2, "start": c, "end": 60.0}]}
        assert renderer.same_shot_tail(4.27, cut, FPS, FPS, 60.0) == 0.0, c
    far = {"shots": [{"id": 1, "start": 0.0, "end": 4.5},
                     {"id": 2, "start": 4.5, "end": 60.0}]}
    assert renderer.same_shot_tail(4.27, far, FPS, FPS, 60.0) > 0.0
    # a focus/follow edge there is a composition change: no
    assert renderer.same_shot_tail(4.27, idx, FPS, FPS, 60.0,
                                   edges=(4.29,)) == 0.0
    # nothing past the source's end
    assert renderer.same_shot_tail(59.99, idx, FPS, FPS, 60.0) == 0.0


def test_a_cut_block_reads_the_next_frame_and_a_split_does_not():
    edl = {"keep": [[1.03, 4.27], [6.11, 8.94]],
           "frame": {"ratio": "9:16", "mode": "crop"}}
    plain = _graph(edl)
    assert "trim=start=1.030:end=4.270," in plain
    shot = _graph(edl, {"words": [], "video": {}, "shots": ONE_SHOT})
    assert "trim=start=1.030:end=4.303," in shot      # the cut
    assert "trim=start=6.110:end=8.973," in shot      # the programme's end
    # still bounded by the clock: the block keeps its frame count
    assert "trim=end_frame=98," in shot and "trim=end_frame=85," in shot
    # a source-contiguous split (a focus handoff) owns that frame next door
    split = {"keep": [[1.0, 9.0]], "frame": {
        "ratio": "9:16", "mode": "crop", "focus_track": [
            {"t0": 0.0, "t1": 4.5, "x": 0.3},
            {"t0": 4.5, "t1": 60.0, "x": 0.7}]}}
    g = _graph(split, {"words": [], "video": {}, "shots": ONE_SHOT},
               focus_origin=0.0)
    assert "end=4.483," in g and "end=4.516," not in g
    # censor regions stop at the span's end: no extra frame under them
    regs = dict(edl, effects={"regions": [
        {"id": "r1", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2,
         "mode": "blur"}]})
    assert "end=4.303" not in _graph(regs, {"words": [], "video": {},
                                            "shots": ONE_SHOT})


# ── rendered ─────────────────────────────────────────────────────────────

RW, RH = 320, 180


def _source(path, dur):
    """A frame-coded clip (a patch whose grey encodes the frame index) and a
    loud 440 Hz tone from the first sample."""
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{RW}x{RH}", "-r", str(int(FPS)), "-i", "pipe:0",
         "-f", "lavfi", "-i", f"sine=f=440:sample_rate=48000:d={dur}",
         "-filter_complex", "[1:a]volume=4.0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "ffv1", "-c:a", "pcm_s16le",
         "-t", str(dur), path], stdin=subprocess.PIPE,
        stderr=subprocess.PIPE)
    for k in range(int(dur * FPS)):
        f = np.full((RH, RW), 60, np.uint8)
        f[60:120, 120:200] = 40 + (k % 40) * 5
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0, p.stderr.read().decode()


def _run(src, d, keep, index, **kw):
    edl = validate_edl({"keep": keep, "frame": {"ratio": "16:9",
                                                 "mode": "crop"}},
                       9.0).model_dump()
    tl = Timeline(edl["keep"])
    g = renderer.build_filtergraph(
        edl, 9.0, True, tl, None, [], dict(index, video={"fps": FPS}),
        preview=False, W=RW, H=RH, fps=FPS, frame_mode="crop", src_fps=FPS,
        focus_origin=0.0, **kw)
    vid, aud = os.path.join(d, "v.raw"), os.path.join(d, "a.raw")
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_complex", g,
         "-map", "[vout]", "-fps_mode", "cfr", "-r", f"{FPS:.3f}",
         "-f", "rawvideo", "-pix_fmt", "gray", vid,
         "-map", "[aout]", "-f", "f32le", "-ac", "1", "-ar", "48000", aud],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    frames = np.fromfile(vid, np.uint8).reshape(-1, RH, RW)
    return frames, np.fromfile(aud, np.float32), tl


@pytest.fixture(scope="module")
def src():
    d = tempfile.mkdtemp(prefix="rpolish_")
    path = os.path.join(d, "src.mkv")
    _source(path, 9.0)
    yield d, path
    shutil.rmtree(d, ignore_errors=True)


# 0.31 -> 2.09 holds 53 source frames (10..62) but its block 54 (1.78 s):
# the clock gives it one slot more than the span holds
KEEP = [[0.31, 2.09], [3.11, 4.54]]


@needs_ffmpeg
def test_rendered_start_opens_from_silence_without_a_click(src):
    d, path = src
    _f, raw, _tl = _run(path, d, KEEP, {"words": []})
    _f, faded, _tl = _run(path, d, KEEP, {"words": []},
                          program_edges=(True, True))
    assert abs(raw[0]) > 0.2                          # the step it opened on
    assert abs(faded[0]) < 0.01
    n = int(config.PROGRAM_EDGE_FADE_S * 48000)
    ramp = np.abs(faded[:n]).max()
    assert ramp < np.abs(faded[n:4 * n]).max()        # rising, then the tone
    assert np.abs(faded[-24:]).max() < 0.06           # and ends in silence
    assert np.abs(raw[-240:]).max() > 0.2
    # nothing else moved: past the fade the two are the same samples
    assert np.allclose(raw[4 * n:-4 * n], faded[4 * n:-4 * n], atol=1e-4)


@needs_ffmpeg
def test_rendered_cut_shows_the_next_frame_not_a_held_one(src):
    d, path = src

    def code(frames):
        return frames[:, 85:95, 155:165].mean(axis=(1, 2))
    held, _a, tl = _run(path, d, KEEP, {"words": []})
    k1 = renderer.first_frame_at(tl.offsets[1], FPS)
    c = code(held)
    assert abs(c[k1 - 1] - c[k1 - 2]) < 1.0           # the clone it showed
    fixed, _a, _tl = _run(path, d, KEEP, {"words": [], "shots": ONE_SHOT})
    c = code(fixed)
    assert len(fixed) == len(held)                    # same clock
    assert abs(c[k1 - 1] - c[k1 - 2]) > 2.0           # a real next frame
    assert np.array_equal(fixed[:k1 - 1], held[:k1 - 1])
    # the second block (the programme's end) is the same up to its own
    # last slot
    assert np.array_equal(fixed[k1:-1], held[k1:-1])
