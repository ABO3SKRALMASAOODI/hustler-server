"""Render-performance changes must not change the picture.

* Cover crops cut the window out of the SOURCE before scaling (only when the
  source size is known) and keep the scale-then-crop framing.
* The global grade runs inside each normalized block, on the fewest pixels,
  with bars padded in graded black, and falls back to the post-concat grade
  wherever the order would matter (constant-colour junction transitions).
* Glow / halation / dream_blur blur at reduced resolution.
"""
import itertools
import math
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import gradelut  # noqa: E402
import renderer  # noqa: E402
from schemas import validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="ffmpeg required")
INDEX = {"words": [], "silences": [], "sentences": []}


def _legacy_fit(W, H, focus=None):
    fx = focus[0] if focus else None
    fy = focus[1] if focus else None
    if (fx is not None and abs(fx - .5) > 1e-6) or \
            (fy is not None and abs(fy - .5) > 1e-6):
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:x='clip(iw*{(fx if fx is not None else .5):.4f}"
                f"-ow/2,0,iw-ow)':y='clip(ih*{(fy if fy is not None else .5):.4f}"
                f"-oh/2,0,ih-oh)'")
    return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}")


# ------------------------------------------------------------ geometry ----

def test_unknown_source_size_keeps_the_exact_legacy_fit_string():
    """The matte measurement and inserts (no probed size) keep the old chain
    byte for byte, so cached masks and pinned graphs stay valid."""
    for focus in (None, (0.3, None), (None, 0.7)):
        assert renderer.frame_fit_filter("crop", 1080, 1920, focus) == \
            _legacy_fit(1080, 1920, focus)
    assert renderer.frame_fit_filter("pad", 720, 1280) == (
        "scale=720:1280:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black")


def test_cover_window_reproduces_the_scale_then_crop_mapping():
    def rnd(v):
        return math.floor(v + .5)
    worst = 0.0
    for (sw, sh), (W, H), fx, fy in itertools.product(
            [(1920, 1080), (1280, 720), (3840, 2160), (1918, 1080),
             (960, 540), (1080, 1920), (1440, 1080), (854, 480)],
            [(1080, 1920), (720, 1280), (270, 480), (1080, 1350),
             (1080, 1080), (1920, 1080)],
            [None, .42, .02, .97], [None, .3]):
        tw = max(rnd(H * sw / sh), W)
        th = max(rnd(W * sh / sw), H)

        def off(t, size, f):
            v = (t - size) / 2 if f is None else t * f - size / 2
            return int(round(min(max(v, 0), t - size))) & ~1
        X, Y = off(tw, W, fx), off(th, H, fy)
        xa, ya, wa, ha, twa, tha, xo, yo = renderer._cover_geometry(
            sw, sh, W, H, (fx, fy))
        for v in (xa, ya, wa, ha, xo, yo):
            assert v % 2 == 0, "every crop value stays chroma-aligned"
        assert xa + wa <= sw and ya + ha <= sh
        assert xo + W <= twa and yo + H <= tha
        for i in (0, W - 1):
            legacy = (X + i + .5) * sw / tw
            new = xa + (xo + i + .5) * wa / twa
            worst = max(worst, abs(new - legacy) * tw / sw)
        for j in (0, H - 1):
            legacy = (Y + j + .5) * sh / th
            new = ya + (yo + j + .5) * ha / tha
            worst = max(worst, abs(new - legacy) * th / sh)
    assert worst < 1.0, worst


def test_known_source_size_crops_first_and_sharpens_only_enlargements():
    up = renderer.frame_fit_filter("crop", 1080, 1920, (0.42, None),
                                   src_size=(1920, 1080))
    assert up.startswith("crop='min(iw,")
    assert ":flags=lanczos" in up and "unsharp=3:3:" in up
    assert up.index("unsharp") < up.index("scale="), "sharpen the small window"
    approval = renderer.frame_fit_filter("crop", 720, 1280,
                                         src_size=(1920, 1080))
    assert ":flags=lanczos" in approval and "unsharp" not in approval
    down = renderer.frame_fit_filter("crop", 270, 480, src_size=(960, 540))
    assert "lanczos" not in down and "unsharp" not in down


def _gray_frame(chain, sw, sh, W, H, t=1.0):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=s={sw}x{sh}:r=30:d=2", "-ss", str(t), "-frames:v", "1",
         "-vf", f"{chain},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(out, np.uint8).reshape(H, W).astype(float)


@needs_ffmpeg
@pytest.mark.parametrize("src,out,focus", [
    ((640, 360), (360, 640), None),
    ((640, 360), (360, 640), (0.3, None)),
    ((360, 640), (640, 360), (None, 0.65)),
])
def test_crop_first_frames_match_the_legacy_framing(src, out, focus,
                                                    monkeypatch):
    monkeypatch.setattr(renderer, "UPSCALE_SHARPEN", 0.0)
    monkeypatch.setattr(renderer, "UPSCALE_SCALER", "bicubic")
    W, H = out
    a = _gray_frame(_legacy_fit(W, H, focus), *src, W, H)
    b = _gray_frame(renderer.frame_fit_filter("crop", W, H, focus,
                                              src_size=src), *src, W, H)
    errs = {}
    for dx, dy in itertools.product((-1, 0, 1), repeat=2):
        A = a[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)]
        B = b[max(0, -dy):H + min(0, -dy), max(0, -dx):W + min(0, -dx)]
        errs[(dx, dy)] = float(np.mean((A - B) ** 2))
    best = min(errs, key=errs.get)
    assert max(abs(best[0]), abs(best[1])) <= 1
    assert errs[best] < 40, errs


# ---------------------------------------------------------------- grade ----

def _graph(edl, W=1080, H=1920, src=(1920, 1080), preview=False,
           has_audio=True):
    e = validate_edl(edl, 30).model_dump()
    tl = Timeline(e["keep"], e.get("inserts") or [], e.get("speed") or [])
    return renderer.build_filtergraph(
        e, 30.0, has_audio, tl, None, [], INDEX, preview, W=W, H=H, fps=30.0,
        frame_mode=(e.get("frame") or {}).get("mode"),
        src_w=src[0], src_h=src[1], silence_idx=1)


KEEP = [[1.0, 2.5], [3.0, 4.5]]


def test_grade_runs_on_the_source_window_before_the_enlargement():
    g = _graph({"keep": KEEP, "frame": {"ratio": "9:16", "mode": "crop"},
                "effects": {"grade": "cinematic"}})
    fast = gradelut.fast_chain(renderer.GRADE_FILTERS["cinematic"])
    assert "[vgrade]" not in g, "no post-concat grade"
    for i in range(2):
        block = re.search(rf"\[segv{i}\](.*?)\[v_seg{i}\]", g).group(1)
        assert fast in block
        assert block.index(fast) < block.index("scale="), block[:200]


def test_dip_transitions_keep_the_post_concat_grade():
    g = _graph({"keep": KEEP, "frame": {"ratio": "9:16", "mode": "crop"},
                "effects": {"grade": "warm",
                            "transition": {"style": "dip_black",
                                           "duration_s": .3,
                                           "scope": "every_cut"}}})
    assert "[vgrade]" in g


def test_plain_cut_without_normalization_keeps_the_post_concat_grade():
    e = validate_edl({"keep": KEEP, "effects": {"grade": "vibrant"}},
                     30).model_dump()
    g = renderer.build_filtergraph(
        e, 30.0, True, Timeline(e["keep"]), None, [], INDEX, False,
        W=1920, H=1080, fps=30.0, src_w=1920, src_h=1080)
    assert "[vgrade]" in g


@needs_ffmpeg
def test_letterbox_bars_are_padded_in_graded_black():
    g = _graph({"keep": KEEP, "frame": {"ratio": "9:16", "mode": "pad"},
                "effects": {"grade": "vintage"}}, W=360, H=640)
    color = renderer._graded_black(
        gradelut.fast_chain(renderer.GRADE_FILTERS["vintage"]))
    assert color and color != "0x000000"
    assert f"color={color}" in g and "[vgrade]" not in g


def test_a_failed_graded_black_measurement_is_not_remembered(monkeypatch):
    """A timeout under load used to pin the chain to the slow post-concat
    grade (functools.lru_cache kept the None) for the life of the process."""
    answers = iter([None, "0x0C0908", "0xFFFFFF"])
    calls = []

    def measure(chain):
        calls.append(chain)
        return next(answers)

    monkeypatch.setattr(renderer, "_measure_graded_black", measure)
    monkeypatch.setattr(renderer, "_GRADED_BLACK", {})
    assert renderer._graded_black("eq=gamma=1.1") is None
    assert renderer._graded_black("eq=gamma=1.1") == "0x0C0908"
    assert renderer._graded_black("eq=gamma=1.1") == "0x0C0908"
    assert len(calls) == 2


def _render_rgb(graph, W, H, tmp_path, name, t=0.5):
    out = str(tmp_path / f"{name}.mkv")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i",
         "anullsrc=r=48000:cl=stereo", "-filter_complex", graph,
         "-map", "[vout]", "-map", "[aout]", "-t", "1", "-c:v", "ffv1",
         "-c:a", "pcm_s16le", out], capture_output=True, check=True,
        timeout=60)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", out, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=60).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(float)


@needs_ffmpeg
@pytest.mark.parametrize("frame,grade", [
    ({"ratio": "9:16", "mode": "crop", "focus_x": .4}, "cinematic"),
    ({"ratio": "9:16", "mode": "pad"}, "vintage"),
    ({"ratio": "9:16", "mode": "crop", "picture": [0, .29, 1, .71]}, "warm"),
])
def test_block_grade_is_the_same_picture_as_the_post_concat_grade(
        frame, grade, monkeypatch, tmp_path):
    W, H = 360, 640
    edl = {"keep": KEEP, "frame": frame, "effects": {"grade": grade}}
    monkeypatch.setenv("BLOCK_GRADE_DISABLE", "1")
    legacy = _graph(edl, W=W, H=H, src=(640, 360), has_audio=False)
    monkeypatch.setenv("BLOCK_GRADE_DISABLE", "0")
    block = _graph(edl, W=W, H=H, src=(640, 360), has_audio=False)
    assert "[vgrade]" in legacy and "[vgrade]" not in block
    a = _render_rgb(legacy, W, H, tmp_path, "legacy")
    b = _render_rgb(block, W, H, tmp_path, "block")
    # testsrc2's hard, saturated colour edges are the worst case for grading
    # before vs after a resample or a chroma subsampling step (real 1080p
    # footage measured 43-45 dB luma PSNR, chroma ~50 dB): the difference
    # lives on edges only and is about a level on average.
    diff = np.abs(a - b)
    assert float(diff.mean()) < 1.5, float(diff.mean())
    assert float(np.percentile(diff, 99)) <= 16
    # bars (rows above the picture / letterbox) keep their graded colour
    if frame.get("mode") == "pad" or frame.get("picture"):
        assert np.abs(a[3, 3] - b[3, 3]).max() <= 4, (a[3, 3], b[3, 3])


# ------------------------------------------------------------- stylize ----

def test_wide_blurs_run_at_reduced_resolution():
    e = validate_edl({"keep": [[0, 4]], "effects": {"stylize": [
        {"id": "g", "kind": "glow", "intensity": .4},
        {"id": "h", "kind": "halation", "intensity": .5},
        {"id": "d", "kind": "dream_blur", "intensity": .5}]}},
        4).model_dump()
    g = renderer.build_filtergraph(
        e, 4.0, True, Timeline(e["keep"]), None, [], INDEX, False,
        W=1080, H=1920, fps=30.0)
    assert g.count("scale=270:480:flags=area") == 3
    assert g.count("scale=1080:1920:flags=bicubic") == 3
    # the halation threshold still selects highlights at full resolution
    assert re.search(r"lutyuv=y='if\(gte\(val,\d+\),val,16\)':u=128:v=128,"
                     r"scale=270:480", g)
    windowed = validate_edl({"keep": [[0, 4]], "effects": {"stylize": [
        {"id": "d", "kind": "dream_blur", "intensity": .5,
         "start": 1, "end": 2}]}}, 4).model_dump()
    gw = renderer.build_filtergraph(
        windowed, 4.0, True, Timeline(windowed["keep"]), None, [], INDEX,
        False, W=1080, H=1920, fps=30.0)
    assert "gblur=sigma=6.0:enable='between(t,1.000,2.000)'" in gw


@needs_ffmpeg
def test_reduced_resolution_glow_matches_the_full_resolution_glow():
    W, H = 540, 960
    down, blur, up = renderer._reduced_gblur(W, H, 20.0)
    assert down

    def glow(chain):
        return (f"[0:v]format=yuv420p,split[a][b];[b]{chain}[g];"
                f"[a][g]blend=all_mode=screen:all_opacity=0.41")
    vals = []
    for chain in ("gblur=sigma=20.0", f"{down}{blur}{up}"):
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
             f"testsrc2=s={W}x{H}:r=30:d=1", "-frames:v", "1",
             "-filter_complex", glow(chain), "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
        vals.append(np.frombuffer(out, np.uint8).astype(float))
    mse = float(np.mean((vals[0] - vals[1]) ** 2))
    assert 10 * math.log10(255 ** 2 / max(mse, 1e-9)) > 40


# ------------------------------------------------- distant keep clusters ----

def test_keep_clusters_split_on_long_gaps_and_cap_the_input_count():
    keep = [[600, 606], [610, 614.5], [1800, 1806], [2400, 2405.5]]
    assert renderer._keep_clusters(keep, 20, 8) == [
        (600.0, 614.5), (1800.0, 1806.0), (2400.0, 2405.5)]
    # the closest clusters merge first when there are too many
    assert renderer._keep_clusters(keep, 20, 2) == [
        (600.0, 614.5), (1800.0, 2405.5)]
    assert renderer._keep_clusters(keep, 0, 8) == [(600.0, 2405.5)]


def test_cluster_inputs_feed_each_segment_its_own_bounded_read():
    e = validate_edl({"keep": [[600, 606], [610, 614], [1800, 1806]],
                      "frame": {"ratio": "9:16", "mode": "crop"}},
                     4000).model_dump()
    tl = Timeline(e["keep"])
    kw = dict(W=360, H=640, fps=30.0, frame_mode="crop", src_w=640,
              src_h=360)
    g = renderer.build_filtergraph(
        e, 4000.0, True, tl, None, [], INDEX, False,
        main_video_inputs=[(1, 600.0, 614.0), (2, 1800.0, 1806.0)], **kw)
    assert "[0:v]" not in g, "the gap between clusters is never decoded"
    assert "[1:v]split=2[vin0][vin1]" in g and "[2:v]null[vin2]" in g
    assert "[0:a]" in g, "audio still comes from the main input"
    # a segment no cluster covers keeps the single-input graph
    g2 = renderer.build_filtergraph(
        e, 4000.0, True, tl, None, [], INDEX, False,
        main_video_inputs=[(1, 600.0, 606.0)], **kw)
    assert "[0:v]split=3" in g2


@needs_ffmpeg
def test_cluster_reads_render_the_identical_programme(tmp_path, monkeypatch):
    src = str(tmp_path / "long.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=320x180:r=30:d=95", "-f", "lavfi", "-i",
         "sine=frequency=330:sample_rate=48000:duration=95",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
         "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True, capture_output=True, timeout=120)
    edl = {"keep": [[8.0, 10.0], [11.0, 12.5], [45.0, 47.0], [88.0, 90.0]],
           "frame": {"ratio": "9:16", "mode": "crop"},
           "effects": {"grade": "warm"}}
    index = {"words": [], "sentences": [], "silences": [], "video": {}}
    commands = {}

    def capture(cmd, **kw):
        commands["cmd"] = [c for c in cmd
                           if c not in ("-progress", "pipe:1", "-nostats")]
        raise StopIteration

    monkeypatch.setattr(renderer, "_render_media_run", capture)
    digests = {}
    for gap in (0.0, 20.0):
        monkeypatch.setattr(renderer.config, "KEEP_CLUSTER_GAP_S", gap)
        out = str(tmp_path / f"out_{int(gap)}.mp4")
        work = tmp_path / f"work_{int(gap)}"
        work.mkdir()
        with pytest.raises(StopIteration):
            renderer.render_edl(edl, index, src, out, str(work), preview=True)
        n_inputs = commands["cmd"].count("-i")
        subprocess.run(commands["cmd"], check=True, capture_output=True,
                       timeout=180)
        frames = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", out, "-map", "0:v", "-f",
             "framemd5", "-"], capture_output=True, text=True,
            check=True).stdout
        audio = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", out, "-map", "0:a", "-f", "md5",
             "-"], capture_output=True, text=True, check=True).stdout
        digests[gap] = (n_inputs,
                        [ln for ln in frames.splitlines()
                         if not ln.startswith("#")], audio)
    assert digests[20.0][0] == digests[0.0][0] + 3, "one read per cluster"
    assert digests[20.0][1] == digests[0.0][1], "every frame identical"
    assert digests[20.0][2] == digests[0.0][2], "audio identical"


# ------------------------------------------------------- end-card colour ----

def test_outro_join_is_pinned_to_the_source_matrix():
    e = validate_edl({"keep": KEEP, "frame": {"ratio": "9:16", "mode": "crop"},
                      "effects": {"grade": "cinematic"}}, 30).model_dump()
    tl = Timeline(e["keep"])
    kw = dict(W=360, H=640, fps=30.0, frame_mode="crop", src_w=640,
              src_h=360, outro_s=5.0, card_idx=1)
    g = renderer.build_filtergraph(e, 30.0, True, tl, None, [], INDEX, False,
                                   src_color_space="bt709", **kw)
    prog = re.search(r"\]([^;]*)\[vprog\]", g).group(1)
    card = re.search(r"\[ocomp\]([^;]*)\[ovid\]", g).group(1)
    assert "out_color_matrix=bt709:out_range=tv" in prog
    assert "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p" in card
    assert renderer._outro_matrix("bt2020nc") == "bt2020"
    assert renderer._outro_matrix("bt470bg") == "bt470"
    # an untagged source keeps the old free negotiation, byte for byte
    g0 = renderer.build_filtergraph(e, 30.0, True, tl, None, [], INDEX, False,
                                    **kw)
    assert "out_color_matrix" not in g0
    assert "format=yuv420p,setsar=1[ovid]" in g0


def _programme_y(cmd, W, H, frames=12):
    """The first frames of [vout] (the programme, before any end card) as Y
    planes, straight off the graph — no encode."""
    i = cmd.index("-filter_complex")
    raw = subprocess.run(
        cmd[:i + 2] + ["-map", "[vout]", "-frames:v", str(frames), "-f",
                       "rawvideo", "-pix_fmt", "yuv420p", "-",
                       "-map", "[aout]", "-f", "null", "-"],
        capture_output=True, check=True, timeout=180).stdout
    fs = W * H * 3 // 2
    d = np.frombuffer(raw, np.uint8)[:frames * fs].reshape(frames, fs)
    return d[:, :W * H].reshape(frames, H, W).astype(float)


@needs_ffmpeg
@pytest.mark.parametrize("frame", [
    {"ratio": "9:16", "mode": "crop"},
    {"ratio": "9:16", "mode": "pad"},
    {"ratio": "9:16", "mode": "crop", "picture": [0, .29, 1, .71]},
])
def test_end_card_join_keeps_the_programme_colour(frame, tmp_path,
                                                  monkeypatch):
    """A final carries the end card; the approval preview does not. Joining
    the card must not change a single programme pixel's tone: an unanchored
    join once converted a BT.709 programme into BT.601 and turned graded pad
    bars from Y 25 to 21 in finals only."""
    src = str(tmp_path / "src709.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i",
         "sine=frequency=330:sample_rate=48000:duration=6",
         "-vf", "format=yuv420p,setparams=colorspace=bt709:"
         "color_primaries=bt709:color_trc=bt709:range=tv",
         "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0",
         "-c:a", "aac", src], check=True, capture_output=True, timeout=120)
    assert renderer.media.probe(src)["color_space"] == "bt709"
    edl = {"keep": KEEP, "frame": frame,
           "effects": {"grade": "cinematic",
                       "grade_custom": {"temperature": .3, "contrast": 1.05,
                                        "shadows": .3}}}
    index = {"words": [], "sentences": [], "silences": [], "video": {}}
    commands = {}

    def capture(cmd, **kw):
        commands["cmd"] = [c for c in cmd
                           if c not in ("-progress", "pipe:1", "-nostats")]
        raise StopIteration

    monkeypatch.setattr(renderer, "_render_media_run", capture)
    planes = {}
    for grade_path in ("block", "post"):
        monkeypatch.setenv("BLOCK_GRADE_DISABLE",
                           "1" if grade_path == "post" else "0")
        for outro in (True, False):
            work = tmp_path / f"w_{grade_path}_{outro}"
            work.mkdir()
            with pytest.raises(StopIteration):
                renderer.render_edl(edl, index, src,
                                    str(tmp_path / "o.mp4"), str(work),
                                    preview=False, suppress_outro=not outro)
            assert ("[ovid]" in " ".join(commands["cmd"])) is outro
            planes[grade_path, outro] = _programme_y(commands["cmd"],
                                                     1080, 1920)
    for grade_path in ("block", "post"):
        with_card = planes[grade_path, True]
        without = planes[grade_path, False]
        assert abs(with_card.mean() - without.mean()) < 0.25, grade_path
        assert np.abs(with_card - without).mean() < 0.5, grade_path
    # block grade vs post-concat grade: bars and overall tone agree too
    a, b = planes["post", True], planes["block", True]
    assert abs(a.mean() - b.mean()) < 1.0, (a.mean(), b.mean())
    if frame.get("mode") == "pad" or frame.get("picture"):
        assert abs(a[0, 40, 540] - b[0, 40, 540]) <= 1, (a[0, 40, 540],
                                                         b[0, 40, 540])


def test_cluster_read_count_is_held_to_a_memory_budget(monkeypatch):
    monkeypatch.setattr(renderer.config, "KEEP_CLUSTER_MAX_INPUTS", 8)
    monkeypatch.setattr(renderer.config, "KEEP_CLUSTER_MEM_MB", 512.0)
    assert renderer._cluster_input_cap(1920, 1080) == 8
    assert renderer._cluster_input_cap(3840, 2160) == 3
    assert renderer._cluster_input_cap(7680, 4320) == 1     # no extra reads
    monkeypatch.setattr(renderer.config, "KEEP_CLUSTER_MAX_INPUTS", 4)
    assert renderer._cluster_input_cap(1280, 720) == 4


@needs_ffmpeg
def test_cluster_reads_keep_peak_memory_bounded(tmp_path, monkeypatch):
    """Every cluster read is its own decoder, started at launch. With ffmpeg's
    auto thread count each one held a full set of frame-thread buffers, so a
    4K final with 8 clusters went from 4.0 to 6.8 GB. Each read now decodes on
    two threads, and nothing may queue a cluster's frames while an earlier
    cluster plays (4 s of 960x540 is ~93 MB per read)."""
    import json
    src = str(tmp_path / "long540.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=960x540:r=30:d=100", "-f", "lavfi", "-i",
         "sine=frequency=330:sample_rate=48000:duration=100",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "60",
         "-pix_fmt", "yuv420p", "-c:a", "aac", src],
        check=True, capture_output=True, timeout=180)
    edl = {"keep": [[8.0, 12.0], [34.0, 38.0], [60.0, 64.0], [90.0, 94.0]],
           "frame": {"ratio": "9:16", "mode": "crop"}}
    index = {"words": [], "sentences": [], "silences": [], "video": {}}
    commands = {}

    def capture(cmd, **kw):
        commands["cmd"] = [c for c in cmd
                           if c not in ("-progress", "pipe:1", "-nostats")]
        raise StopIteration

    monkeypatch.setattr(renderer, "_render_media_run", capture)
    monkeypatch.setattr(renderer.config, "KEEP_CLUSTER_THREADS", 2)
    probe = ("import json,resource,subprocess,sys;"
             "p=subprocess.run(json.loads(sys.argv[1]));"
             "r=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss;"
             "print(p.returncode, r // (1024 * 1024) if sys.platform == "
             "'darwin' else r // 1024)")
    peak, cmds = {}, {}
    for gap in (0.0, 20.0):
        monkeypatch.setattr(renderer.config, "KEEP_CLUSTER_GAP_S", gap)
        work = tmp_path / f"w{int(gap)}"
        work.mkdir()
        with pytest.raises(StopIteration):
            renderer.render_edl(edl, index, src, str(tmp_path / "o.mp4"),
                                str(work), preview=True)
        cmd = cmds[gap] = commands["cmd"]
        i = cmd.index("-filter_complex")
        run = cmd[:i + 2] + ["-map", "[vout]", "-f", "null", "-",
                             "-map", "[aout]", "-f", "null", "-"]
        res = subprocess.run([sys.executable, "-c", probe, json.dumps(run)],
                             capture_output=True, text=True, check=True,
                             timeout=300)
        rc, peak[gap] = (int(x) for x in res.stdout.split())
        assert rc == 0
    reads = [j for j, a in enumerate(cmds[20.0])
             if a == "-i" and cmds[20.0][j + 1] == src]
    assert len(reads) == 5, "the main input plus one read per cluster"
    for j in reads[1:]:
        assert cmds[20.0][j - 8:j - 6] == ["-threads:v", "2"]
    assert "-threads:v" not in cmds[0.0]
    per_read = (peak[20.0] - peak[0.0]) / (len(reads) - 2)
    assert per_read < 40, (peak, per_read)
