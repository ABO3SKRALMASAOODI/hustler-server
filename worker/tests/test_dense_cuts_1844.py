"""Ticket #1844 (p6311, 26 Nov 2026): "make all scene transitions soft
dissolves" on a 41 s, 17-shot keyed greeting.

1. The picture map read a snow animation drawn at ~24 pictures a second in a
   60 fps file as 154 cuts: each drawn step changes 13-25 (of 255) with only
   codec noise between, so the median of the neighbours sat on the noise and
   every step towered over it. A cut must now tower over the footage's drawn
   steps too, unless most of the picture changes (a fast montage of
   different shots still cuts on every picture).
2. Those 155 dissolves made a graph the executor's OOM killer took: every
   kept block drew its own picture chain, every dissolve its own mix, and a
   block the windows covered whole drained into a nullsink, which ffmpeg
   serves first and so decoded the film ahead into memory. The blocks now
   share one picture chain, the dissolves one mix, and a covered block
   drains in program order: the same frames, and memory that no longer
   grows with the number of cuts.
"""

import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import picture_map                                         # noqa: E402
import renderer                                            # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="needs ffmpeg")


# ── 1. the detector ──────────────────────────────────────────────────────

def _stepped(n=600, cuts=(150, 330, 480), seed=4):
    """p6311's signal: a picture drawn on a 2-3 frame cadence (24 drawings
    a second on a 60 fps clock) moving 13.5 a step (sd 2: the clip's
    measured median 13.3), 0.1-1.5 of codec noise between steps, and three
    real cuts that change part of the frame (a
    keyed card over the same backdrop: 45-55% of the pixels)."""
    rng = np.random.default_rng(seed)
    d = rng.uniform(0.1, 1.5, n)
    whole = rng.uniform(0.0, 0.02, n)
    f, k = 3, 0
    while f < n:
        d[f] = float(np.clip(rng.normal(13.5, 2.0), 9.0, 20.0))
        whole[f] = rng.uniform(0.08, 0.45)
        f += 2 if k % 2 else 3
        k += 1
    for c in cuts:
        d[c] = rng.uniform(40.0, 56.0)
        whole[c] = rng.uniform(0.45, 0.55)
        for j in (c - 1, c + 1):
            d[j] = min(d[j], 1.0)
    return d, whole


def test_drawn_animation_steps_are_not_cuts():
    d, whole = _stepped()
    # measured frame against frame, the steps read as cuts (the 154)
    assert len(picture_map.find_cuts(d, d)) > 60
    got = picture_map.find_cuts(d, d, step=picture_map.step_frames(60.0),
                                whole=whole)
    assert got == [150, 330, 480]


def test_a_fast_montage_still_cuts_on_every_picture():
    """Every fifth frame a different picture (12 a second) over moving
    footage: each change moves most of the frame, so each is a cut."""
    n = 300
    d = 6.0 + (np.arange(n) % 3).astype(float)
    whole = np.full(n, 0.05)
    cuts = list(range(10, n - 10, 5))
    for c in cuts:
        d[c], whole[c] = 51.0, 0.85
    got = picture_map.find_cuts(d, d, step=picture_map.step_frames(60.0),
                                whole=whole)
    assert got == cuts


def test_a_cut_in_fast_footage_at_its_own_rate_is_kept():
    """Review of #1844: footage moving at its file's rate (here a shake
    every third frame, 12-14 against 4-6 between) is not drawn in steps, so
    a cut that towers over its neighbours is a cut even when it changes
    part of the picture by less than its largest step-window change."""
    rng = np.random.default_rng(7)
    n = 300
    d = rng.uniform(4.0, 6.0, n)
    d[::3] = rng.uniform(12.0, 14.0, len(d[::3]))
    whole = np.full(n, 0.15)
    for c in (61, 151, 241):
        d[c], whole[c] = 26.0, 0.4
    assert picture_map.find_cuts(d, d) == [61, 151, 241]
    assert picture_map.find_cuts(d, d, step=picture_map.step_frames(30.0),
                                 whole=whole) == [61, 151, 241]


def test_step_frames_spans_one_drawing_at_twelve_a_second():
    assert picture_map.step_frames(60.0) == 5
    assert picture_map.step_frames(30.0) == 3
    assert picture_map.step_frames(24.0) == 2
    assert picture_map.step_frames(12.0) == 1
    assert picture_map.step_frames(None) == 1


def _animated_card(path, fps=60, dur=4.0, cuts=(1.5, 2.75)):
    """A static card under a particle layer drawn at 12 pictures a second
    (on fives at 60 fps), and two real changes of card."""
    W = H = 320
    rng = np.random.default_rng(11)
    cards = [rng.integers(30, 220, (H // 8, W // 8), dtype=np.uint8)
             .repeat(8, 0).repeat(8, 1) for _ in range(len(cuts) + 1)]
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE)
    dots = None
    for f in range(int(dur * fps)):
        k = sum(1 for c in cuts if f >= round(c * fps))
        if f % 5 == 0:                           # a new drawing
            dots = rng.random((H // 4, W // 4)) < 0.05
        fr = cards[k].copy()
        fr[np.kron(dots, np.ones((4, 4), bool))] = 255
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    assert p.wait() == 0
    return path


@needs_ffmpeg
def test_the_map_of_an_animated_card_holds_only_its_real_cuts(tmp_path):
    src = _animated_card(str(tmp_path / "card.mp4"))
    got = picture_map.measure(src)
    assert got["v"] == picture_map.VERSION == 3
    assert len(got["cuts"]) == 2, got["cuts"]
    assert got["cuts"][0] == pytest.approx(1.5, abs=0.02)
    assert got["cuts"][1] == pytest.approx(2.75, abs=0.02)


# ── 2. the render ────────────────────────────────────────────────────────

def _montage(path, n_shots=24, shot_s=0.4, fps=30, rate=None):
    """``n_shots`` different moving pictures in one file (``rate``: the
    file's frame rate as ffmpeg reads it, else ``fps``)."""
    W, H = 320, 320
    rng = np.random.default_rng(5)
    pics = [rng.integers(0, 255, (H // 8 + 8, W // 8 + 8, 3), dtype=np.uint8)
            .repeat(8, 0).repeat(8, 1) for _ in range(n_shots)]
    dur = n_shots * shot_s
    p = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(rate or fps), "-i", "-", "-f", "lavfi",
         "-i",
         f"sine=f=330:sample_rate=48000:d={dur}", "-map", "0:v", "-map",
         "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
         "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", str(dur),
         path], stdin=subprocess.PIPE)
    per = int(round(shot_s * fps))
    for f in range(n_shots * per):
        k, j = divmod(f, per)
        p.stdin.write(np.ascontiguousarray(
            pics[k][j % 8:j % 8 + H, j % 8:j % 8 + W]).tobytes())
    p.stdin.close()
    assert p.wait() == 0
    return path, [round(k * shot_s, 2) for k in range(1, n_shots)], dur


def _edl(cuts, dur, dissolves, gap=0.0, mode="pad_blur"):
    """Every shot kept, a dissolve on each join. ``gap``: seconds cut from
    the head of every shot after the first (jump cuts: each side of a join
    has source footage past it, its handle)."""
    edges = [0.0] + cuts + [dur]
    keep = [[a + (gap if k else 0.0), b]
            for k, (a, b) in enumerate(zip(edges, edges[1:]))]
    edl = {"keep": keep,
           "frame": {"ratio": "9:16", "mode": mode},
           "effects": {"grade": "cinematic"},
           "captions": {"enabled": False}}
    if dissolves:
        at, t = [], 0.0
        for a, b in keep[:-1]:
            t += b - a
            at.append(round(t, 3))
        edl["fx"] = [{"id": f"jn{k + 1}", "type": "junction",
                      "stage": "junction", "at": t, "style": "dissolve",
                      "duration_s": 0.2, "hold": not gap,
                      "device": "junction_dissolve"}
                     for k, t in enumerate(at)]
    return edl


def _idx(cuts, dur, fps=30.0):
    edges = [0.0] + cuts + [dur]
    return {"words": [], "silences": [], "sentences": [],
            "video": {"duration": dur, "width": 320, "height": 320,
                      "fps": fps},
            "shots": [{"id": k, "start": a, "end": b}
                      for k, (a, b) in enumerate(zip(edges, edges[1:]))]}


def _graphs(monkeypatch):
    """Record every render graph while the render runs as usual."""
    seen = []
    real = renderer._render_media_run

    def run(cmd, **kw):
        cmd = list(cmd)
        for flag in ("-/filter_complex", "-filter_complex_script"):
            if flag in cmd:
                with open(cmd[cmd.index(flag) + 1], encoding="utf-8") as fh:
                    seen.append(fh.read())
        if "-filter_complex" in cmd:
            seen.append(cmd[cmd.index("-filter_complex") + 1])
        return real(cmd, **kw)
    monkeypatch.setattr(renderer, "_render_media_run", run)
    return seen


def _render_both(tmp_path, monkeypatch, dissolves, gap=0.0, fps=30,
                 rate=None, mode="pad_blur"):
    from w1d_support import frames, lossless, render
    src, cuts, dur = _montage(str(tmp_path / "montage.mp4"), fps=fps,
                              rate=rate)
    lossless(monkeypatch)
    graphs = _graphs(monkeypatch)
    edl = _edl(cuts, dur, dissolves, gap=gap, mode=mode)
    idx = _idx(cuts, dur, fps=float(fps))
    shared = str(tmp_path / "shared.mp4")
    d1, rep1 = render(edl, src, shared, idx=idx, duration=dur)
    g_shared = graphs[-1]
    monkeypatch.setattr(renderer, "SHARED_PICTURE_MIN_BLOCKS", 10 ** 6)
    alone = str(tmp_path / "alone.mp4")
    d2, rep2 = render(edl, src, alone, idx=idx, duration=dur)
    g_alone = graphs[-1]
    a, _ = frames(shared)
    b, _ = frames(alone)
    return (d1, rep1, a, g_shared), (d2, rep2, b, g_alone)


@needs_ffmpeg
def test_many_blocks_share_one_picture_chain_and_the_same_frames(
        tmp_path, monkeypatch):
    (d1, _r1, a, g1), (d2, _r2, b, g2) = _render_both(
        tmp_path, monkeypatch, dissolves=False)
    assert g1.count("boxblur") == 1             # one backdrop for 24 blocks
    assert g2.count("boxblur") == 24
    assert d1 == pytest.approx(d2, abs=1e-3)
    assert a.shape == b.shape
    assert np.array_equal(a, b)


@needs_ffmpeg
def test_many_dissolves_mix_once_with_the_same_frames(tmp_path, monkeypatch):
    (d1, rep1, a, g1), (d2, rep2, b, g2) = _render_both(
        tmp_path, monkeypatch, dissolves=True)
    assert [r["rung"] for r in rep1] == ["full"] * 23, rep1
    assert [r["rung"] for r in rep2] == ["full"] * 23, rep2
    assert g1.count("alphamerge") == 1          # one mix for 23 dissolves
    assert g2.count("alphamerge") == 23
    assert "nullsink" not in g1
    assert d1 == pytest.approx(d2, abs=1e-3)
    assert a.shape == b.shape
    assert np.array_equal(a, b)


def test_a_block_two_windows_cover_whole_drains_in_program_order():
    """No nullsink: a sink is served first and decodes the film ahead."""
    parts = []
    blocks = [("v0", "a0", 1.0), ("v1", "a1", 1.0), ("v2", "a2", 1.0)]
    renderer._weave_windows(parts, blocks, [10, 4, 10],
                            [(10, 2, "w0", 0), (14, 2, "w1", 1)], 30.0)
    graph = ";".join(parts)
    assert "nullsink" not in graph
    assert "[v1]trim=start_frame=2:end_frame=2" in graph


@needs_ffmpeg
def test_peak_memory_does_not_grow_with_the_cuts(tmp_path, monkeypatch):
    """The p6311 shape at test size: 48 blocks, 47 dissolves, a blurred
    backdrop. Per-block chains and mixes held ~10 MB a cut for the whole
    render; the shared ones hold one chain."""
    src, cuts, dur = _montage(str(tmp_path / "long.mp4"), n_shots=48,
                              shot_s=0.3)
    import schemas
    edl = schemas.validate_edl(_edl(cuts, dur, True), dur).model_dump()
    idx = _idx(cuts, dur)
    captured = {}
    real = renderer._render_media_run

    def capture(cmd, **kw):
        if "[adlg]" in cmd:
            return real(cmd, **kw)
        cmd = list(cmd)
        for flag in ("-/filter_complex", "-filter_complex_script"):
            if flag in cmd:
                i = cmd.index(flag)
                with open(cmd[i + 1], encoding="utf-8") as fh:
                    cmd[i:i + 2] = ["-filter_complex", fh.read()]
        captured["cmd"] = [c for c in cmd
                           if c not in ("-progress", "pipe:1", "-nostats")]
        raise StopIteration
    monkeypatch.setattr(renderer, "_render_media_run", capture)
    probe = ("import json,resource,subprocess,sys;"
             "p=subprocess.run(json.loads(sys.argv[1]));"
             "r=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss;"
             "print(p.returncode, r // (1024 * 1024) if sys.platform == "
             "'darwin' else r // 1024)")
    peak = {}
    for name, floor in (("shared", None), ("alone", 10 ** 6)):
        if floor:
            monkeypatch.setattr(renderer, "SHARED_PICTURE_MIN_BLOCKS", floor)
        work = tmp_path / name
        work.mkdir()
        with pytest.raises(StopIteration):
            renderer.render_edl(edl, idx, src, str(tmp_path / "o.mp4"),
                                str(work), preview=True)
        cmd = captured["cmd"]
        i = cmd.index("-filter_complex")
        graph = tmp_path / f"{name}.graph"
        graph.write_text(cmd[i + 1], encoding="utf-8")
        run = cmd[:i] + ["-/filter_complex", str(graph),
                         "-map", "[vout]", "-f", "null", "-",
                         "-map", "[aout]", "-f", "null", "-"]
        res = subprocess.run([sys.executable, "-c", probe, json.dumps(run)],
                             capture_output=True, text=True, check=True,
                             timeout=600)
        rc, peak[name] = (int(x) for x in res.stdout.split())
        assert rc == 0
    assert peak["shared"] < 0.7 * peak["alone"], peak


@needs_ffmpeg
@pytest.mark.parametrize("fps,rate,mode", [(30, None, "pad_blur"),
                                           (29.97, "30000/1001", "crop")])
def test_dissolves_on_jump_cuts_share_their_handles_chains(
        tmp_path, monkeypatch, fps, rate, mode):
    """Review of #1844: a dissolve on a jump cut draws each side's source
    handle; each handle drew its own picture chain (159 backdrops for 80
    dissolves, 1.8 GB). One chain per side now, the same frames."""
    (d1, rep1, a, g1), (d2, rep2, b, g2) = _render_both(
        tmp_path, monkeypatch, dissolves=True, gap=0.15, fps=fps, rate=rate,
        mode=mode)
    assert [r["rung"] for r in rep1] == ["full"] * 23, rep1
    assert [r["rung"] for r in rep2] == ["full"] * 23, rep2
    import re
    taps = int(re.search(r"split=(\d+)\[fxsrc\]", g1).group(1)) - 1
    assert taps >= 10                           # the joins' source handles
    assert re.search(r"split=(\d+)\[fxsrc\]", g2).group(1) == str(taps + 1)
    if mode == "pad_blur":
        # one backdrop for the blocks, one per handle side; alone, one per
        # block and one per handle
        assert g1.count("boxblur") <= 3
        assert g2.count("boxblur") == 24 + taps
    assert d1 == pytest.approx(d2, abs=1e-3)
    assert a.shape == b.shape
    assert np.array_equal(a, b)


@needs_ffmpeg
def test_batched_dissolves_laid_over_the_programme_keep_their_frames(
        tmp_path, monkeypatch):
    """The overlay path (cards, person matte) takes the batched windows as
    the woven path does."""
    monkeypatch.setattr(renderer, "_weaves_windows", lambda *a, **k: False)
    (d1, rep1, a, g1), (d2, rep2, b, g2) = _render_both(
        tmp_path, monkeypatch, dissolves=True)
    assert [r["rung"] for r in rep1] == ["full"] * 23, rep1
    assert g1.count("alphamerge") == 1 and g2.count("alphamerge") == 23
    assert d1 == pytest.approx(d2, abs=1e-3)
    assert np.array_equal(a, b)


@needs_ffmpeg
def test_a_batch_that_cannot_mix_together_mixes_window_by_window(
        tmp_path, monkeypatch):
    """Review of #1844: the batched mix is drawn after every window has
    compiled; when it fails, each window is drawn with its own mix and the
    film still has every dissolve."""
    import junctions_v2

    def boom(*_a, **_k):
        raise ValueError("no batched mix")
    monkeypatch.setattr(junctions_v2._Junction, "_mix_together", boom)
    (d1, rep1, a, g1), (d2, rep2, b, g2) = _render_both(
        tmp_path, monkeypatch, dissolves=True)
    assert [r["rung"] for r in rep1] == ["full"] * 23, rep1
    assert g1.count("alphamerge") == 23
    assert d1 == pytest.approx(d2, abs=1e-3)
    assert np.array_equal(a, b)


@needs_ffmpeg
def test_a_window_that_fails_after_joining_the_batch_leaves_it(
        tmp_path, monkeypatch):
    """Review of #1844: a window rolled back after its row joined the
    batch drained its sides into nullsinks while the batch read them too,
    and ffmpeg refused the whole graph. The row now leaves with it: that
    one join is the plain cut and every other dissolve renders."""
    import junctions_v2
    from w1d_support import lossless, render
    real = junctions_v2._Junction._settle

    def settle(self, ctx, label, N):
        if ctx.junction.get("k") == 5:
            raise ValueError("this window cannot settle")
        return real(self, ctx, label, N)
    monkeypatch.setattr(junctions_v2._Junction, "_settle", settle)
    src, cuts, dur = _montage(str(tmp_path / "montage.mp4"))
    lossless(monkeypatch)
    _d, rep = render(_edl(cuts, dur, True), src, str(tmp_path / "o.mp4"),
                     idx=_idx(cuts, dur), duration=dur)
    rungs = {r["id"]: r["rung"] for r in rep}
    assert rungs.pop("jn6") == "omit"
    assert set(rungs.values()) == {"full"} and len(rungs) == 22, rep
