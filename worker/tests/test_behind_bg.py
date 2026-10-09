"""Behind-subject motion graphics and designed picture-card backdrops.

Two premium signatures the owner's reference reels use constantly and
Valmera could not produce:

1. A motion graphic (giant hero word, number, shape) composited INTO the
   shot with the speaker in front of it — the add_text_behind matte applied
   to browser-rendered motion design (MotionItem.layer='behind_subject').
2. A designed canvas around a picture card instead of a flat #101012 void:
   vertical/radial gradients, a blurred darkened copy of the footage, film
   grain and a vignette (PictureCard.background_style/grain/vignette).

Pixel claims are checked on rendered files, not on filtergraph strings.

Run:  python -m pytest tests/test_behind_bg.py -q        (from worker/)
"""
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import numpy as np                                            # noqa: E402
import pytest                                                 # noqa: E402
from PIL import Image                                         # noqa: E402

import agent_tools                                            # noqa: E402
import matte                                                  # noqa: E402
import motion_engine                                          # noqa: E402
import motion_layer                                           # noqa: E402
import motion_tools                                           # noqa: E402
import picture_cards                                          # noqa: E402
import renderer                                               # noqa: E402
import stitch                                                 # noqa: E402
import timeline as tl_mod                                     # noqa: E402
from schemas import (EDLValidationError, PictureCard, default_edl,  # noqa: E402
                     describe_edl, edl_signature, subject_matte_geom,
                     validate_edl)
from timeline import Timeline                                 # noqa: E402

HAVE_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not present")

W, H, FPS = 640, 360, 30
SHOT_S = 6.0
BAR_W = 90
WIN = (1.0, 4.0)
GFX_BAND = (int(H * 0.40), int(H * 0.60))     # rows the stand-in graphic fills


# ── fixtures: a subject walking across a still, textured room ─────────────

def _bg():
    rng = np.random.default_rng(11)
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = (90, 70, 55)
    for k in range(0, W, 40):
        img[:, k:k + 18] = (110, 88, 66)
    return np.clip(img.astype(np.int16) + rng.integers(-6, 7, (H, W, 3)),
                   0, 255).astype(np.uint8)


def _frame(i, n):
    img = _bg().copy()
    x = int((i / max(1, n - 1)) * (W - BAR_W))
    img[H // 3:2 * H // 3, x:x + BAR_W] = (250, 250, 250)
    return img


def _encode(path, frames):
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={len(frames) / FPS:.3f}",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    assert p.wait() == 0, p.stderr.read().decode("utf-8", "replace")


@pytest.fixture(scope="module")
def workdir():
    d = tempfile.mkdtemp(prefix="behind_bg_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="module")
def shot(workdir):
    path = os.path.join(workdir, "shot.mp4")
    n = int(SHOT_S * FPS)
    _encode(path, [_frame(i, n) for i in range(n)])
    return path


@pytest.fixture(scope="module")
def panning(workdir):
    path = os.path.join(workdir, "pan.mp4")
    n = int(3.0 * FPS)
    rng = np.random.default_rng(3)
    base = rng.integers(20, 230, (H, W + n * 4, 3), dtype=np.uint8)
    _encode(path, [np.ascontiguousarray(base[:, i * 4:i * 4 + W]) for i in range(n)])
    return path


@pytest.fixture(scope="module")
def mask(shot, workdir):
    path = os.path.join(workdir, "mask.mp4")
    res = matte.measure_and_build(shot, path, WIN[0], WIN[1] - WIN[0],
                                  width=W, height=H)
    assert res["ok"], res
    return path


@pytest.fixture(scope="module")
def gfx_clip(workdir):
    """A stand-in for a rendered motion graphic: an opaque magenta band
    across the rows the subject walks through, as the engine's transparent
    qtrle clip, placed at (0, GFX_BAND[0])."""
    path = os.path.join(workdir, "gfx.mov")
    h = GFX_BAND[1] - GFX_BAND[0]
    dur = WIN[1] - WIN[0]
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=magenta:s={W}x{h}:r={FPS}:d={dur}",
         "-vf", "format=argb", "-c:v", "qtrle", "-pix_fmt", "argb", path],
        capture_output=True)
    assert r.returncode == 0, r.stderr.decode()
    return motion_engine.RenderedClip(path, 0, GFX_BAND[0], W, h,
                                      int(dur * FPS), 1, False, 0.0)


def _frame_at(path, t, w=W, h=H):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.4f}", "-i", path,
         "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        capture_output=True)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    return np.frombuffer(p.stdout[:w * h * 3], np.uint8).reshape(h, w, 3)


def _subject_cols(src):
    band = slice(H // 3 + 6, 2 * H // 3 - 6)
    cols = (src[band].astype(np.int16).min(axis=2) > 200).any(axis=0)
    x0, x1 = int(np.argmax(cols)), int(W - np.argmax(cols[::-1]))
    return x0, x1


def _magenta(frame):
    f = frame.astype(np.int16)
    return (f[..., 0] > 180) & (f[..., 2] > 180) & (f[..., 1] < 90)


def _mo(layer="behind_subject", behind=True, start=WIN[0], end=WIN[1]):
    item = {"id": "mg1", "template": "hook_title", "start": start, "end": end,
            "params": {"text": "BEHIND"}, "layer": layer}
    if behind:
        item["behind"] = {"asset_key": "matte/1/x.mp4", "src_start": start,
                          "src_end": end, "fp": "testfp"}
    return item


# ── 1. schema ─────────────────────────────────────────────────────────────

def test_behind_layer_requires_a_measured_mask():
    edl = default_edl(SHOT_S)
    edl["motion"] = [_mo(behind=False)]
    with pytest.raises(EDLValidationError, match="measured subject mask"):
        validate_edl(edl, SHOT_S)


def test_behind_item_validates_and_a_stale_mask_on_other_layers_is_dropped():
    edl = default_edl(SHOT_S)
    edl["motion"] = [_mo()]
    out = validate_edl(edl, SHOT_S).model_dump()
    assert out["motion"][0]["behind"]["asset_key"] == "matte/1/x.mp4"
    assert "behind subject" in describe_edl(out, SHOT_S)
    edl["motion"] = [_mo(layer="above_captions")]
    out = validate_edl(edl, SHOT_S).model_dump()
    assert out["motion"][0]["behind"] is None
    # an ordinary motion item hashes exactly as before the field existed
    edl["motion"] = [_mo(layer="above_captions", behind=False)]
    assert "behind" not in edl_signature(validate_edl(edl, SHOT_S).model_dump())


def test_behind_mask_source_span_is_bounded_by_the_video():
    edl = default_edl(SHOT_S)
    item = _mo()
    item["behind"]["src_end"] = SHOT_S + 5
    edl["motion"] = [item]
    with pytest.raises(EDLValidationError, match="behind"):
        validate_edl(edl, SHOT_S)


# ── 2. content anchoring and stitching ───────────────────────────────────

def _remap(edl_in, new_keep):
    edl = validate_edl(edl_in, SHOT_S).model_dump()
    old = Timeline(edl["keep"], [], [])
    edl["keep"] = new_keep
    notes = tl_mod.remap_program_items(edl, old, Timeline(new_keep, [], []))
    return edl, notes


def test_a_behind_graphic_follows_its_footage_through_a_cut():
    edl, notes = _remap({"keep": [[0.0, SHOT_S]], "motion": [_mo(start=3.0, end=5.0)]},
                        [[2.0, SHOT_S]])
    mo = edl["motion"][0]
    assert (mo["start"], mo["end"]) == (1.0, 3.0)
    assert mo["behind"]["src_start"] == 3.0           # the mask is not rewritten
    assert any("behind the same subject" in n for n in notes), notes


def test_a_behind_graphic_dies_with_its_footage():
    edl, notes = _remap({"keep": [[0.0, SHOT_S]], "motion": [_mo(start=1.0, end=3.0)]},
                        [[4.0, SHOT_S]])
    assert edl["motion"] == []
    assert any("no longer in the edit" in n for n in notes), notes


def test_a_cut_inside_the_window_is_disclosed():
    edl, notes = _remap({"keep": [[0.0, SHOT_S]], "motion": [_mo(start=1.0, end=4.0)]},
                        [[0.0, 2.0], [2.5, SHOT_S]])
    assert edl["motion"], "the graphic should survive a partial cut"
    assert any("cut" in n and "ABOVE" in n for n in notes), notes
    # an unrelated later edit does not repeat the disclosure
    old = Timeline(edl["keep"], [], [])
    edl["keep"] = [[0.0, 2.0], [2.5, SHOT_S - 0.5]]
    again = tl_mod.remap_program_items(edl, old, Timeline(edl["keep"], [], []))
    assert not any("ABOVE" in n for n in again), again


def test_stitching_refuses_behind_graphics():
    a = validate_edl({"keep": [[0.0, SHOT_S]], "motion": [_mo()]}, SHOT_S).model_dump()
    b = copy.deepcopy(a)
    b["motion"][0]["params"]["text"] = "CHANGED"
    tl = Timeline(a["keep"], [], [])
    windows, reason = stitch.plan(a, b, tl, tl, SHOT_S, SHOT_S)
    assert windows is None and "behind-subject motion" in reason
    windows, runs, reason = stitch.plan_timeline(a, b, tl, tl, SHOT_S)
    assert windows is None and "behind-subject motion" in reason


# ── 2b. a speed ramp added later (review: the mask drifted silently) ─────

RAMP = [{"id": "sp1", "start": 1.0, "end": 4.0, "factor": 1.5}]


def _btext(start=1.0, end=4.0):
    return {"id": "tx1", "text": "BEHIND", "start": start, "end": end,
            "template": "title",
            "behind": {"asset_key": "matte/1/t.mp4", "src_start": start,
                       "src_end": end, "fp": "testfp"}}


def _remap_speed(edl_in, new_speed):
    edl = validate_edl(edl_in, SHOT_S).model_dump()
    old = Timeline(edl["keep"], [], edl.get("speed") or [])
    edl["speed"] = new_speed
    notes = tl_mod.remap_program_items(edl, old,
                                       Timeline(edl["keep"], [], new_speed))
    return edl, notes


def test_ramp_over_finds_only_spans_that_retime_the_footage():
    tl = Timeline([[0.0, SHOT_S]], [], [
        {"id": "a", "start": 0.0, "end": 1.0, "factor": 2.0},
        {"id": "b", "start": 2.0, "end": 3.0, "factor": 1.0},
        {"id": "c", "start": 4.5, "end": 5.5, "factor": 0.5}])
    assert tl.ramp_over(1.0, 4.4) is None          # touching / factor 1
    assert tl.ramp_over(0.5, 2.0) == ("a", 2.0)
    assert tl.ramp_over(4.0, 4.6) == ("c", 0.5)
    assert Timeline([[0.0, SHOT_S]], [], []).ramp_over(0, SHOT_S) is None


def test_a_ramp_over_a_behind_graphic_is_disclosed_not_claimed_behind():
    edl, notes = _remap_speed(
        {"keep": [[0.0, SHOT_S]], "motion": [_mo(start=1.0, end=4.0)]}, RAMP)
    mo = edl["motion"][0]
    assert (mo["start"], mo["end"]) == (1.0, 3.0)
    assert not any("staying behind" in n for n in notes), notes
    assert any("speed ramp sp1" in n and "ABOVE" in n for n in notes), notes
    # once disclosed, an unrelated later edit does not repeat it
    old = Timeline(edl["keep"], [], edl["speed"])
    edl["keep"] = [[0.0, SHOT_S - 0.5]]
    again = tl_mod.remap_program_items(
        edl, old, Timeline(edl["keep"], [], edl["speed"]))
    assert not any("speed ramp" in n for n in again), again
    # a ramp elsewhere in the edit changes nothing about the claim
    edl, notes = _remap_speed(
        {"keep": [[0.0, SHOT_S]], "motion": [_mo(start=2.0, end=4.0)]},
        [{"id": "sp2", "start": 0.0, "end": 1.0, "factor": 2.0}])
    assert any("staying behind the same subject" in n for n in notes), notes
    assert not any("speed ramp" in n for n in notes), notes


def test_a_ramp_over_a_behind_text_is_disclosed_too():
    edl, notes = _remap_speed({"keep": [[0.0, SHOT_S]], "texts": [_btext()]},
                              RAMP)
    assert edl["texts"] and (edl["texts"][0]["start"],
                             edl["texts"][0]["end"]) == (1.0, 3.0)
    assert not any("staying behind" in n for n in notes), notes
    assert any("speed ramp sp1" in n and "plain title" in n for n in notes), notes


# ── 3. the composite, in rendered pixels ─────────────────────────────────

def _graph(edl, mask_idx, clip_idx, clip, demoted=False):
    edl = validate_edl(edl, SHOT_S).model_dump()
    tl = Timeline(edl["keep"], [], [])
    item = edl["motion"][0]
    motion_inputs = [(clip_idx, item, clip)]
    behind = []
    if demoted:
        motion_inputs = [(clip_idx, motion_layer.demote(item, "test"), clip)]
    else:
        behind = [(mask_idx, {"motion": (clip_idx, item, clip)}, WIN)]
    return renderer.build_filtergraph(
        edl, SHOT_S, True, tl, None, [], {"video": {"duration": SHOT_S}},
        preview=False, W=W, H=H, fps=float(FPS), behind_inputs=behind,
        motion_inputs=motion_inputs)


def _run(graph, inputs, out):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    for i in inputs:
        cmd += ["-i", i]
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
            "-pix_fmt", "yuv420p", "-c:a", "aac", out]
    r = subprocess.run(cmd, capture_output=True)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-3000:]
    return out


def test_the_behind_stage_draws_the_clip_under_the_subject_matte(gfx_clip):
    graph = _graph({"keep": [[0.0, SHOT_S]], "motion": [_mo()]}, 2, 1, gfx_clip)
    assert "alphamerge" in graph
    assert "[bhb0][mgs0c]overlay" in graph            # clip on the scene copy
    assert "[bht0]" not in graph                       # no libass burn for it
    assert "mga0" not in graph and "mgb0" not in graph  # not ALSO composited on top
    # the ordinary layer composites it when it is demoted
    demoted = _graph({"keep": [[0.0, SHOT_S]], "motion": [_mo()]}, 2, 1, gfx_clip,
                     demoted=True)
    assert "alphamerge" not in demoted and "[mga0c]" in demoted


@needs_ffmpeg
def test_the_subject_walks_in_front_of_the_graphic(shot, mask, gfx_clip, workdir):
    out = _run(_graph({"keep": [[0.0, SHOT_S]], "motion": [_mo()]}, 2, 1, gfx_clip),
               [shot, gfx_clip.path, mask], os.path.join(workdir, "behind.mp4"))
    front = _run(_graph({"keep": [[0.0, SHOT_S]], "motion": [_mo()]}, 2, 1, gfx_clip,
                        demoted=True),
                 [shot, gfx_clip.path], os.path.join(workdir, "front.mp4"))
    for frac in (0.15, 0.5, 0.85):
        t = WIN[0] + (WIN[1] - WIN[0]) * frac
        src = _frame_at(shot, t)
        x0, x1 = _subject_cols(src)
        band = slice(GFX_BAND[0] + 4, GFX_BAND[1] - 4)
        behind, above = _magenta(_frame_at(out, t)), _magenta(_frame_at(front, t))
        # the graphic is on screen beside the subject...
        beside = behind[band].copy()
        beside[:, max(0, x0 - 10):min(W, x1 + 10)] = False
        assert beside.mean() > 0.5, "the graphic did not reach the frame"
        # ...the subject's own pixels are NOT overprinted...
        assert behind[band, x0 + 8:x1 - 8].mean() < 0.03, (
            f"at {frac:.0%} the graphic printed over the subject")
        # ...and the control (same clip on the ordinary layer) does print there.
        assert above[band, x0 + 8:x1 - 8].mean() > 0.9
    # outside its window the picture is the shot
    a, b = _frame_at(out, 0.4).astype(int), _frame_at(shot, 0.4).astype(int)
    assert np.abs(a - b).mean() < 3.0


@pytest.fixture
def fake_engine(monkeypatch, gfx_clip):
    """render_edl with the stand-in clip as the 'rendered' composition."""
    monkeypatch.setattr(motion_engine, "render_jobs",
                        lambda jobs, out_dir, **k: [gfx_clip for _ in jobs])
    seen = {}
    real = renderer.build_filtergraph

    def _capture(*a, **kw):
        g = real(*a, **kw)
        seen["graph"] = g
        return g
    monkeypatch.setattr(renderer, "build_filtergraph", _capture)
    return seen


@needs_ffmpeg
def test_render_edl_puts_the_mask_and_the_clip_on_the_right_inputs(
        shot, mask, workdir, fake_engine, monkeypatch):
    monkeypatch.setattr(renderer.storage, "download_to",
                        lambda key, local: shutil.copyfile(mask, local))
    edl = {"keep": [[0.0, SHOT_S]], "motion": [_mo()]}
    out = os.path.join(workdir, "e2e.mp4")
    dur = renderer.render_edl(edl, {"video": {"duration": SHOT_S}, "words": [],
                                    "sentences": []}, shot, out, workdir, preview=True)
    assert dur and abs(dur - SHOT_S) < 1.5
    assert "alphamerge" in fake_engine["graph"]
    assert not motion_layer.LAST_WARNINGS
    t = 2.5
    src, frame = _frame_at(shot, t), _frame_at(out, t)
    x0, x1 = _subject_cols(src)
    band = slice(GFX_BAND[0] + 4, GFX_BAND[1] - 4)
    mag = _magenta(frame)
    assert mag[band].mean() > 0.4
    assert mag[band, x0 + 10:x1 - 10].mean() < 0.05


@needs_ffmpeg
def test_a_proof_fragment_starting_mid_window_skips_into_the_mask(
        shot, mask, workdir, fake_engine, monkeypatch):
    monkeypatch.setattr(renderer.storage, "download_to",
                        lambda key, local: shutil.copyfile(mask, local))
    e = validate_edl({"keep": [[0.0, SHOT_S]], "motion": [_mo()]}, SHOT_S).model_dump()
    frag = stitch.window_edl(e, Timeline(e["keep"], [], []), 2.0, 5.0)
    assert frag["motion"][0]["phase_s"] == pytest.approx(1.0)
    out = os.path.join(workdir, "frag.mp4")
    renderer.render_edl(frag, {"video": {"duration": SHOT_S}, "words": [],
                               "sentences": []}, shot, out, workdir, preview=True)
    assert "trim=start=1.000:end=3.000" in fake_engine["graph"]
    src, frame = _frame_at(shot, 2.6), _frame_at(out, 0.6)
    x0, x1 = _subject_cols(src)
    band = slice(GFX_BAND[0] + 4, GFX_BAND[1] - 4)
    assert _magenta(frame)[band].mean() > 0.4
    assert _magenta(frame)[band, x0 + 10:x1 - 10].mean() < 0.05, \
        "the fragment paired the wrong mask frames with the picture"


@needs_ffmpeg
def test_render_edl_degrades_to_an_ordinary_graphic_when_the_mask_is_missing(
        shot, workdir, fake_engine, monkeypatch):
    monkeypatch.setattr(
        renderer.storage, "download_to",
        lambda key, local: (_ for _ in ()).throw(RuntimeError("no such object")))
    out = os.path.join(workdir, "degraded.mp4")
    dur = renderer.render_edl({"keep": [[0.0, SHOT_S]], "motion": [_mo()]},
                              {"video": {"duration": SHOT_S}, "words": [],
                               "sentences": []}, shot, out, workdir, preview=True)
    assert dur and abs(dur - SHOT_S) < 1.5
    assert "alphamerge" not in fake_engine["graph"]
    assert any("behind the subject" in w and "mask unavailable" in w
               for w in motion_layer.LAST_WARNINGS), motion_layer.LAST_WARNINGS
    # the graphic is still delivered, now on top of the subject
    src, frame = _frame_at(shot, 2.5), _frame_at(out, 2.5)
    x0, x1 = _subject_cols(src)
    assert _magenta(frame)[GFX_BAND[0] + 4:GFX_BAND[1] - 4, x0 + 10:x1 - 10].mean() > 0.9


def test_a_canvas_render_path_demotes_behind_items(gfx_clip):
    item = validate_edl({"keep": [[0.0, SHOT_S]], "motion": [_mo()]},
                        SHOT_S).model_dump()["motion"][0]
    out = motion_layer.demote_behind([(3, item, gfx_clip)], "no footage")
    assert out[0][1]["layer"] == "above_captions" and out[0][1]["behind"] is None


@needs_ffmpeg
def test_the_mask_holds_to_the_graphics_last_frame(shot, mask, gfx_clip, workdir):
    """The tool measures the mask to out_to_src(end - 0.02), rounded, so its
    span ends about a frame before the graphic does. That last frame must
    not draw the graphic over the subject."""
    item = _mo()
    item["behind"]["src_end"] = 3.96
    edl = validate_edl({"keep": [[0.0, SHOT_S]], "motion": [item]},
                       SHOT_S).model_dump()
    it = edl["motion"][0]
    graph = renderer.build_filtergraph(
        edl, SHOT_S, True, Timeline(edl["keep"], [], []), None, [],
        {"video": {"duration": SHOT_S}}, preview=False, W=W, H=H,
        fps=float(FPS), behind_inputs=[(2, {"motion": (1, it, gfx_clip)},
                                        (1.0, 3.96))],
        motion_inputs=[(1, it, gfx_clip)])
    assert "tpad=stop_mode=clone" in graph
    out = _run(graph, [shot, gfx_clip.path, mask],
               os.path.join(workdir, "tail.mp4"))
    t = 3.966                                   # the graphic's last frame
    frame = _frame_at(out, t)
    x0, x1 = _subject_cols(_frame_at(shot, t))
    band = slice(GFX_BAND[0] + 4, GFX_BAND[1] - 4)
    assert _magenta(frame)[band].mean() > 0.4, "the graphic ended early"
    assert _magenta(frame)[band, x0 + 8:x1 - 8].mean() < 0.05, \
        "the last frame printed the graphic over the subject"


class _Stop(Exception):
    pass


@pytest.fixture
def graph_only(monkeypatch, gfx_clip):
    """render_edl up to the filtergraph: records what reached the behind
    stage and which masks were fetched, then stops (no encode)."""
    seen = {"fetched": []}
    monkeypatch.setattr(motion_engine, "render_jobs",
                        lambda jobs, out_dir, **k: [gfx_clip for _ in jobs])
    monkeypatch.setattr(renderer.storage, "download_to",
                        lambda key, local: seen["fetched"].append(key))

    def _capture(*a, **kw):
        seen.update(kw)
        raise _Stop()
    monkeypatch.setattr(renderer, "build_filtergraph", _capture)

    def run(edl):
        with pytest.raises(_Stop):
            renderer.render_edl(edl, {"video": {"duration": SHOT_S},
                                      "words": [], "sentences": []},
                                seen["shot"], os.path.join(seen["wd"], "x.mp4"),
                                seen["wd"], preview=True)
        return seen
    seen["run"] = run
    return seen


@needs_ffmpeg
def test_render_edl_demotes_a_behind_graphic_once_a_ramp_covers_it(
        shot, workdir, graph_only):
    graph_only.update(shot=shot, wd=workdir)
    mo = _mo(start=1.0, end=3.0)
    mo["behind"].update(src_start=1.0, src_end=4.0)
    seen = graph_only["run"]({"keep": [[0.0, SHOT_S]], "motion": [mo],
                              "speed": RAMP})
    assert seen["behind_inputs"] == [] and seen["fetched"] == []
    (_i, item, _c), = [m for m in seen["motion_inputs"]
                       if m[1]["id"] == "mg1"]
    assert item["layer"] == "above_captions" and item["behind"] is None
    assert any("speed ramp sp1" in w for w in motion_layer.LAST_WARNINGS), \
        motion_layer.LAST_WARNINGS
    # the same edit without the ramp keeps the composite
    seen = graph_only["run"]({"keep": [[0.0, SHOT_S]], "motion": [_mo()]})
    assert len(seen["behind_inputs"]) == 1


@needs_ffmpeg
def test_render_edl_burns_a_behind_text_plain_once_a_ramp_covers_it(
        shot, workdir, graph_only, capsys):
    graph_only.update(shot=shot, wd=workdir)
    tx = _btext(1.0, 3.0)
    tx["behind"].update(src_start=1.0, src_end=4.0)
    seen = graph_only["run"]({"keep": [[0.0, SHOT_S]], "texts": [tx],
                              "speed": RAMP})
    assert seen["behind_inputs"] == [] and seen["fetched"] == []
    assert "speed ramp sp1" in capsys.readouterr().out
    seen = graph_only["run"]({"keep": [[0.0, SHOT_S]], "texts": [_btext()]})
    assert len(seen["behind_inputs"]) == 1


def test_the_framing_stamp_tracks_only_what_moves_the_subject():
    g = subject_matte_geom
    assert g(None) == g({"ratio": "source", "mode": "crop"})
    assert g({"ratio": "9:16", "mode": "pad"}) == \
        g({"ratio": "9:16", "mode": "pad_blur", "focus_x": .2})
    assert g({"ratio": "9:16"}) == g({"ratio": "9:16", "mode": "crop",
                                       "focus_x": .5, "focus_y": None})
    base = g({"ratio": "9:16", "mode": "crop"})
    for other in ({"ratio": "1:1", "mode": "crop"},
                  {"ratio": "9:16", "mode": "crop", "focus_x": .3},
                  {"ratio": "9:16", "mode": "pad"},
                  {"ratio": "9:16", "mode": "crop",
                   "picture": [0, .2, 1, .8]}):
        assert g(other) != base, other


@needs_ffmpeg
def test_render_edl_drops_the_depth_when_the_framing_changed(
        shot, workdir, graph_only, capsys):
    graph_only.update(shot=shot, wd=workdir)
    stale = subject_matte_geom({"ratio": "9:16", "mode": "crop"})
    mo, tx = _mo(), _btext(1.0, 4.0)
    mo["behind"]["geom"] = tx["behind"]["geom"] = stale
    seen = graph_only["run"]({"keep": [[0.0, SHOT_S]], "motion": [mo],
                              "texts": [tx]})
    assert seen["behind_inputs"] == []
    assert any("framing changed" in w for w in motion_layer.LAST_WARNINGS)
    assert "framing changed" in capsys.readouterr().out
    # a mask stamped in the CURRENT framing (and an unstamped legacy one)
    # keeps its composite
    mo["behind"]["geom"] = subject_matte_geom(None)
    tx["behind"].pop("geom")
    seen = graph_only["run"]({"keep": [[0.0, SHOT_S]], "motion": [mo],
                              "texts": [tx]})
    assert len(seen["behind_inputs"]) == 2


# ── 4. the tool ──────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, proxy, workdir, duration=SHOT_S):
        self.project_id = 1
        self.workdir = workdir
        self.duration = duration
        self.index = {"video": {"duration": duration, "width": W, "height": H,
                                "fps": FPS}, "words": [], "sentences": []}
        self.has_main_video = True
        self._orig_sha = "sha-for-test"
        self._proxy = proxy
        self.written = []
        self._edl = validate_edl(default_edl(duration), duration).model_dump()

    def proxy_path(self):
        return self._proxy

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


@pytest.fixture
def tool_env(monkeypatch):
    put = {}
    monkeypatch.setattr(agent_tools.storage, "exists", lambda k: k in put)
    monkeypatch.setattr(agent_tools.storage, "upload_file",
                        lambda path, key, ct: put.setdefault(key, path))
    monkeypatch.setattr(agent_tools.storage, "download_to",
                        lambda key, local: shutil.copyfile(put[key], local))
    # the composition draws a band across the subject's path
    monkeypatch.setattr(motion_tools, "_probe_item", lambda item, W_, H_, fps=30.0: {
        "errors": [], "visible_frames": 4, "samples": 4,
        "bboxes": [[0.0, 0.4, 1.0, 0.6]]})
    monkeypatch.setattr(motion_tools, "ensure_library_asset",
                        lambda ctx, sid: f"sfx/1/lib-{sid}.flac")
    return put


@needs_ffmpeg
def test_the_tool_measures_the_subject_and_reports_coverage(shot, workdir, tool_env):
    ctx = _Ctx(shot, workdir)
    res = motion_tools.add_motion_graphic(ctx, "hook_title", WIN[0], WIN[1],
                                          params={"text": "BEHIND"},
                                          layer="behind_subject", sfx=True)
    assert res.startswith("EDL v1"), res
    mo = ctx._edl["motion"][0]
    assert mo["layer"] == "behind_subject"
    assert mo["behind"]["asset_key"].startswith("matte/1/")
    assert mo["behind"]["src_start"] == WIN[0]
    assert list(tool_env) == [mo["behind"]["asset_key"]]
    assert "MEASURED on the footage" in res and "BEHIND the subject" in ctx.written[0]
    assert "graphic's width" in res or "WARNING" in res
    # owned sound cues (opted in) still follow the graphic
    assert any(s["id"].startswith("mg_mg1_sfx") for s in ctx._edl["sfx"])


@needs_ffmpeg
def test_moving_the_window_remeasures_and_a_layer_change_drops_the_mask(
        shot, workdir, tool_env):
    ctx = _Ctx(shot, workdir)
    assert motion_tools.add_motion_graphic(
        ctx, "hook_title", 1.0, 3.0, params={"text": "X"}, sfx=False,
        layer="behind_subject").startswith("EDL v")
    first = ctx._edl["motion"][0]["behind"]["fp"]
    res = motion_tools.set_motion_graphic(ctx, "mg1", start=2.0, end=4.0)
    assert res.startswith("EDL v"), res
    second = ctx._edl["motion"][0]["behind"]
    assert second["fp"] != first and second["src_start"] == 2.0
    # a design tweak over the same window is a cache hit, not a re-measure
    res = motion_tools.set_motion_graphic(ctx, "mg1", params={"text": "Y"})
    assert "already measured" in res and len(tool_env) == 2
    assert motion_tools.set_motion_graphic(
        ctx, "mg1", layer="above_captions").startswith("EDL v")
    assert ctx._edl["motion"][0]["behind"] is None


@needs_ffmpeg
def test_the_tool_refuses_a_window_that_crosses_a_cut(shot, workdir, tool_env):
    ctx = _Ctx(shot, workdir)
    ctx._edl = validate_edl({"keep": [[0.0, 2.0], [4.0, SHOT_S]]}, SHOT_S).model_dump()
    res = motion_tools.add_motion_graphic(ctx, "hook_title", 1.0, 3.0,
                                          params={"text": "X"}, layer="behind_subject")
    assert res.startswith("REJECTED") and "CUT inside" in res and "the graphic" in res
    assert ctx.written == [] and not tool_env


@needs_ffmpeg
def test_the_tool_refuses_a_moving_camera_and_names_the_alternative(
        panning, workdir, tool_env):
    ctx = _Ctx(panning, workdir, duration=3.0)
    res = motion_tools.add_motion_graphic(ctx, "hook_title", 0.2, 2.2,
                                          params={"text": "X"}, layer="behind_subject")
    assert res.startswith("REJECTED") and "camera is moving" in res
    assert "above_captions" in res
    assert ctx.written == [] and not tool_env


def test_the_tool_refuses_without_a_main_video_or_a_valid_layer(workdir, tool_env):
    ctx = _Ctx("unused.mp4", workdir)
    ctx.has_main_video = False
    res = motion_tools.add_motion_graphic(ctx, "hook_title", 1.0, 3.0,
                                          params={"text": "X"}, layer="behind_subject")
    assert res.startswith("REJECTED") and "no main video" in res
    res = motion_tools.add_motion_graphic(ctx, "hook_title", 1.0, 3.0,
                                          params={"text": "X"}, layer="sideways")
    assert res.startswith("REJECTED") and "layer must be" in res
    assert ctx.written == []


def test_the_tool_schema_offers_the_behind_layer():
    spec = agent_tools.TOOLS["add_motion_graphic"]
    assert "behind_subject" in spec[2]["layer"]["enum"]
    assert "behind_subject" in spec[1]


@needs_ffmpeg
def test_the_mask_is_stamped_and_a_reframe_is_disclosed_and_remeasured(
        shot, workdir, tool_env):
    ctx = _Ctx(shot, workdir)
    assert motion_tools.add_motion_graphic(
        ctx, "hook_title", 1.0, 3.0, params={"text": "X"}, sfx=False,
        layer="behind_subject").startswith("EDL v")
    assert ctx._edl["motion"][0]["behind"]["geom"] == subject_matte_geom(None)
    # Same aspect as the 16:9 shot, but a focus point: the stamp moves.
    res = agent_tools.set_frame(ctx, "16:9", "crop", focus_x=.3)
    assert res.startswith("EDL v"), res
    assert "NOTE: the subject mask behind motion graphic mg1" in res
    assert "set_motion_graphic" in res
    # an untouched set_motion_graphic re-measures in the new framing
    res = motion_tools.set_motion_graphic(ctx, "mg1")
    assert res.startswith("EDL v"), res
    assert ctx._edl["motion"][0]["behind"]["geom"] == \
        subject_matte_geom(ctx._edl["frame"])
    assert len(tool_env) == 2
    # a set_frame that keeps the framing says nothing
    res = agent_tools.set_frame(ctx, "16:9", "crop", focus_x=.3)
    assert "subject mask" not in res


# ── 5. designed picture-card backdrops ───────────────────────────────────

CARD = {"id": "c", "start": 1.0, "end": 3.0, "box": [.1, .3, .9, .7],
        "radius": .12, "entrance": "none", "exit": "none", "border": .002}


def test_a_legacy_card_keeps_its_signature_and_knobs_normalize():
    e = default_edl(4)
    e["effects"] = {"picture_cards": [dict(CARD)]}
    out = validate_edl(e, 4).model_dump()
    sig = edl_signature(out)
    for k in ("background_style", "grain", "vignette", "background_color2",
              "background_dim"):
        assert k not in sig
    row = PictureCard.model_validate(dict(
        CARD, background_style="solid", grain=0, vignette=0,
        background_color2="#ff0000", background_dim=.3)).model_dump()
    assert (row["background_style"], row["grain"], row["vignette"],
            row["background_color2"], row["background_dim"]) == (None,) * 5
    assert not picture_cards.designed(row)
    row = PictureCard.model_validate(dict(
        CARD, background_style="blur", background_color2="#ff0000",
        background_dim=.3)).model_dump()
    assert row["background_color2"] is None and row["background_dim"] == .3
    row = PictureCard.model_validate(dict(
        CARD, background_style="vertical_gradient", background_color2="#ff00aa")).model_dump()
    assert row["background_color2"] == "#FF00AA"
    for bad in ({"background_color2": "red", "background_style": "radial_gradient"},
                {"grain": 1.5}, {"vignette": -.1}, {"background_style": "plaid"}):
        with pytest.raises(ValueError):
            PictureCard.model_validate(dict(CARD, **bad))


def test_the_tool_offers_designed_backdrops_with_tasteful_defaults():
    class Ctx:
        index = {"video": {"width": 1920, "height": 1080}}
        edit_plan = None

        def __init__(self):
            self.edl = validate_edl(default_edl(4), 4).model_dump()

        def latest_edl(self):
            return {"json": self.edl}

        def write_edl(self, edl, desc):
            self.edl = validate_edl(edl, 4).model_dump()
            return "EDL v2: " + desc

    ctx = Ctx()
    res = agent_tools.set_picture_card(ctx, "p", 1, 3)
    card = ctx.edl["effects"]["picture_cards"][0]
    assert card["background"] == "#101012" and not picture_cards.designed(card)
    assert "background_style='blur'" in res          # the nudge
    res = agent_tools.set_picture_card(ctx, "p", 1, 3, background_style="radial_gradient",
                                       grain=.25, vignette=.4)
    card = ctx.edl["effects"]["picture_cards"][0]
    assert (card["background"], card["background_color2"]) == \
        agent_tools.PICTURE_CARD_STYLE_COLORS["radial_gradient"]
    assert "radial_gradient + grain + vignette" in res and "NOTE" not in res
    agent_tools.set_picture_card(ctx, "p", 1, 3, background_style="vertical_gradient",
                                 background="#3A2A1E")
    card = ctx.edl["effects"]["picture_cards"][0]
    assert card["background"] == "#3A2A1E" and card["background_color2"] is None
    assert agent_tools.set_picture_card(ctx, "p", 1, 3, grain=3).startswith("REJECTED")
    schema = agent_tools.TOOLS["set_picture_card"][2]
    assert set(schema["background_style"]["enum"]) == {
        "solid", "vertical_gradient", "radial_gradient", "blur"}
    assert "blur" in agent_tools.TOOLS["set_picture_card"][1]


def test_designed_plates_draw_the_backdrop_and_keep_a_clean_hole(tmp_path):
    Wp, Hp = 320, 568
    spec = PictureCard.model_validate(dict(
        CARD, background_style="vertical_gradient", background="#606878",
        background_color2="#08080A", vignette=.5, shadow=.5)).model_dump()
    a = np.asarray(Image.open(picture_cards.build_designed_plate(
        str(tmp_path / "p.png"), Wp, Hp, spec)))
    assert a[284, 160, 3] == 0 and a[20, 160, 3] == 255         # hole / plate
    assert a[20, 160, :3].mean() > a[540, 160, :3].mean() + 30   # top -> bottom
    assert a[20, 4, :3].mean() < a[20, 160, :3].mean()           # vignette
    # the hole carries the clean fill (no shadow) for the opening layer
    hole_row = a[250:320, 100:220, :3].astype(int)
    assert hole_row.std() < 12
    radial = PictureCard.model_validate(dict(
        CARD, background_style="radial_gradient", background="#707888")).model_dump()
    r = np.asarray(Image.open(picture_cards.build_designed_plate(
        str(tmp_path / "r.png"), Wp, Hp, radial)))
    assert r[150, 160, :3].mean() > r[4, 4, :3].mean() + 20      # glow behind card


def test_the_blur_decor_is_transparent_inside_and_the_mask_white_outside(tmp_path):
    spec = PictureCard.model_validate(dict(CARD, background_style="blur",
                                           vignette=.4)).model_dump()
    d, m = picture_cards.build_decor(str(tmp_path / "d.png"), str(tmp_path / "m.png"),
                                     320, 568, spec)
    d, m = np.asarray(Image.open(d)), np.asarray(Image.open(m))
    assert d[284, 160, 3] == 0 and m[284, 160] == 0
    assert m[10, 10] == 255 and d[2, 2, 3] > 40                  # vignette corner


def test_a_legacy_card_still_renders_through_the_historical_graph():
    e = default_edl(4)
    e["effects"] = {"picture_cards": [dict(CARD)]}
    spec = validate_edl(e, 4).model_dump()["effects"]["picture_cards"][0]
    parts = []
    picture_cards.append_graph(parts, "v", [(2, spec)], 320, 568, 30)
    g = ";".join(parts)
    assert "color=c=0x101012" in g
    for new in ("loop=", "noise", "alphamerge", "gblur", "yuva444p"):
        assert new not in g


SRC_COLOR = (0xCC, 0x55, 0x22)


def _card_render(tmp_path, card, name, frame=None, dur=4):
    """Render a card over flat orange footage at 320x568 (9:16)."""
    import graphics
    Wc, Hc = 320, 568
    e = default_edl(dur)
    e["frame"] = frame or {"ratio": "9:16", "mode": "crop"}
    e["effects"] = {"picture_cards": [card]}
    e = validate_edl(e, dur).model_dump()
    source = tmp_path / "src.mp4"
    if not source.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c=0x{''.join(f'{c:02X}' for c in SRC_COLOR)}:s=320x180:r=30:d={dur}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)], check=True)
    tl = Timeline(e["keep"], [], [])
    args = ["-i", str(source), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    inputs, _ = picture_cards.prepare_inputs(e, str(tmp_path), Wc, Hc, 30, args, 2)
    graph = renderer.build_filtergraph(
        e, dur, False, tl, None, [], {}, False, W=Wc, H=Hc, fps=30,
        frame_mode=e["frame"].get("mode", "crop"), src_w=320, src_h=180,
        silence_idx=1, picture_card_inputs=inputs)
    out = tmp_path / f"{name}.mp4"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", graph,
                        "-map", "[vout]", "-map", "[aout]", "-t", str(tl.out_duration),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "6",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-4000:]
    return out


def _f(path, t):
    return _frame_at(str(path), t, 320, 568).astype(int)


@needs_ffmpeg
@pytest.mark.parametrize("style", ["vertical_gradient", "radial_gradient", "blur"])
def test_designed_backdrops_render_around_a_sharp_card(tmp_path, style):
    card = dict(CARD, background_style=style, vignette=.4,
                background="#5A6070" if style != "blur" else "#101012")
    out = _card_render(tmp_path, card, style)
    before, inside = _f(out, .5), _f(out, 2)
    # the footage stays the footage, inside the card
    assert abs(inside[284, 160] - np.array(SRC_COLOR)).max() < 20
    # outside it the canvas is the backdrop, not footage and not a flat void
    assert abs(before[40, 160] - np.array(SRC_COLOR)).max() < 20   # pre-card: footage
    top, bottom, corner = inside[40, 160], inside[530, 160], inside[6, 6]
    assert corner.mean() < top.mean() or corner.mean() < bottom.mean()  # vignette
    if style == "vertical_gradient":
        assert top.mean() > bottom.mean() + 15
    if style == "radial_gradient":
        assert inside[150, 160].mean() > corner.mean() + 15
    if style == "blur":
        # a darkened copy of THIS footage: orange hue, well below its level
        assert top[0] > top[1] > top[2]
        assert top.mean() < np.mean(SRC_COLOR) * .8 and top.mean() > 25
    # the rounded corner shows backdrop, not square footage
    assert abs(inside[172, 34] - np.array(SRC_COLOR)).max() > 40


@needs_ffmpeg
def test_blur_lift_never_shows_footage_outside_the_window(tmp_path):
    card = dict(CARD, background_style="blur", entrance="lift", exit="none",
                duration_s=.6)
    out = _card_render(tmp_path, card, "lift")
    # 0.1s into the lift the tile hangs below its window; the backdrop has to
    # cover that overhang (the window's bottom edge is y=.7 -> row 398)
    f = _f(out, 1.1)
    below = f[402:416, 60:260]
    assert abs(below - np.array(SRC_COLOR)).max(axis=2).min() > 30


@needs_ffmpeg
def test_grain_moves_on_the_backdrop_but_never_on_the_footage(tmp_path):
    card = dict(CARD, background_style="radial_gradient", background="#5A6070",
                grain=.6)
    out = _card_render(tmp_path, card, "grain")
    a, b = _f(out, 1.5), _f(out, 1.6)
    back = np.abs(a[20:120, 20:300] - b[20:120, 20:300]).mean()
    foot = np.abs(a[250:320, 100:220] - b[250:320, 100:220]).mean()
    assert back > 1.0, "grain is not animated on the backdrop"
    assert foot < 0.6, "grain leaked onto the footage"


@needs_ffmpeg
def test_a_stitched_fragment_matches_the_full_render(tmp_path):
    card = dict(CARD, background_style="blur", vignette=.3, entrance="fade",
                exit="fade", duration_s=.6)
    e = validate_edl(dict(default_edl(4), frame={"ratio": "9:16", "mode": "crop"},
                          effects={"picture_cards": [card]}), 4).model_dump()
    tl = Timeline(e["keep"], [], [])
    proof = stitch.window_edl(e, tl, 1.2, 2.7)
    c = proof["effects"]["picture_cards"][0]
    assert c["background_style"] == "blur" and c["phase_s"] == pytest.approx(.2)
    full = _card_render(tmp_path, card, "full")
    part = _card_render(tmp_path, c, "part", dur=1.5)
    assert np.abs(_f(full, 1.3) - _f(part, .1)).mean() < 4


def test_the_tool_never_silently_drops_a_backdrop_knob():
    class Ctx:
        index = {"video": {"width": 1920, "height": 1080}}
        edit_plan = None

        def __init__(self):
            self.edl = validate_edl(default_edl(4), 4).model_dump()

        def latest_edl(self):
            return {"json": self.edl}

        def write_edl(self, edl, desc):
            self.edl = validate_edl(edl, 4).model_dump()
            return "EDL v2: " + desc

    ctx = Ctx()
    # a second colour on its own is a gradient, not a flat card
    res = agent_tools.set_picture_card(ctx, "p", 1, 3, background="#2C303B",
                                       background_color2="#0A0A0C")
    card = ctx.edl["effects"]["picture_cards"][0]
    assert card["background_style"] == "vertical_gradient"
    assert card["background_color2"] == "#0A0A0C"
    assert "vertical_gradient" in res and "NOTE" not in res
    for kw in ({"background_style": "blur", "background_color2": "#000000"},
               {"background_style": "solid", "background_color2": "#000000"},
               {"background_style": "radial_gradient", "background_dim": .3},
               {"background_dim": .3}):
        before = copy.deepcopy(ctx.edl)
        res = agent_tools.set_picture_card(ctx, "p", 1, 3, **kw)
        assert res.startswith("REJECTED"), (kw, res)
        assert ctx.edl == before
    assert agent_tools.set_picture_card(
        ctx, "p", 1, 3, background_style="blur",
        background_dim=.25).startswith("EDL v")
    assert ctx.edl["effects"]["picture_cards"][0]["background_dim"] == .25


def test_designed_plates_build_without_full_canvas_index_grids(tmp_path,
                                                                monkeypatch):
    """A 4K-class plate peaked at ~1 GB through int64 mgrid temporaries; the
    radial maths now broadcasts two float32 axes."""
    monkeypatch.setattr(np, "mgrid", None)          # any use would raise
    spec = PictureCard.model_validate(dict(
        CARD, background_style="radial_gradient", background="#707888",
        vignette=.4)).model_dump()
    a = np.asarray(Image.open(picture_cards.build_designed_plate(
        str(tmp_path / "r.png"), 320, 568, spec)))
    assert a[150, 160, :3].mean() > a[4, 4, :3].mean() + 20
    blur = PictureCard.model_validate(dict(CARD, background_style="blur",
                                           vignette=.4)).model_dump()
    picture_cards.build_decor(str(tmp_path / "d.png"), str(tmp_path / "m.png"),
                              320, 568, blur)
