"""Crops and picture cards that FOLLOW the speaker's face inside a shot
(owner, Oct 10 2026: "the card doesn't adjust with the face — occasionally
part of the face moves out").

* The plan (follow.plan): a sway inside the dead zone holds still; a step is
  ONE eased move inside the velocity/acceleration limits, centred on the
  moment the speaker moved and never letting the head out; a walk is a
  steady pan, not stop-and-go; a move that can happen inside removed footage
  happens on the jump cut; plans are deterministic.
* The data: one span per shot, canonical and bounded; the render splits its
  blocks on a span edge inside a kept segment.
* The render: the window lands where the python mirror says on every frame
  (sub-pixel, monotone, holds exact), for a full-bleed crop, a picture rect,
  a sped segment, a following card and a proof fragment; drafts and finals
  draw the same path.
* The mirrors: keep-out and caption placement see the face where the follow
  put it; behind-subject depth yields where the crop moves; renders carry a
  follow_v stamp.
* The tools: set_frame/auto_reframe and set_picture_card write follow spans
  from a dense face track, and leave a still speaker still.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_tools
import config
import follow
import keepout
import picture_cards
import renderer
import stitch
from schemas import default_edl, edl_signature, validate_edl
from timeline import Timeline

SRC = 60.0
FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def _face(cx, cy=.35, h=.12, aspect=16 / 9):
    """A detector box of height h (frame fractions) centred on (cx, cy)."""
    w = h / aspect
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


def _track(fn, t0, t1, step=.25, look=0):
    out, t = [], t0
    while t <= t1 + 1e-9:
        out.append((round(t, 3), _face(fn(t)), look))
        t += step
    return out


def _speed(span, t0, t1, axis=0):
    ts = np.arange(t0, t1, 1 / 30.0)
    v = [follow.centre_at(span, t)[axis] for t in ts]
    return np.diff(v) * 30.0, np.diff(v, 2) * 900.0


# ── the plan ──────────────────────────────────────────────────────────────

def test_the_path_holds_outside_its_keys_and_never_overshoots():
    ts, vs = [1.0, 2.0, 2.5, 4.0], [10.0, 30.0, 30.0, 20.0]
    assert follow.path(ts, vs, 0.0) == 10.0 and follow.path(ts, vs, 9.0) == 20.0
    for t, v in zip(ts, vs):
        assert follow.path(ts, vs, t) == pytest.approx(v)
    seg = [follow.path(ts, vs, 1.0 + i / 100.0) for i in range(101)]
    assert all(b >= a - 1e-9 for a, b in zip(seg, seg[1:]))   # monotone
    assert max(seg) <= 30.0 + 1e-9 and min(seg) >= 10.0 - 1e-9
    # whole-pixel keys stay whole pixels once a move is over
    assert follow.path([0.0, 1.0], [8.0, 264.0], 3.0) == 264.0


def test_a_sway_inside_the_dead_zone_holds_still():
    """A speaker swaying a few percent (with detector jitter) needs no
    camera: no span, and the still aim is reported."""
    rng = np.random.default_rng(3)
    s = _track(lambda t: .40 + .02 * np.sin(t * 1.7) + rng.normal(0, .006), 0, 12)
    keys, info = follow.plan(s, 0, 12, 607.5 / 1920, 1.0)
    assert keys is None and info["static"] is not None
    assert abs(info["static"][0] - .40) < .03


def test_a_step_is_one_eased_move_within_the_limits():
    ww = 607.5 / 1920
    s = _track(lambda t: .30 if t < 6 else .48, 0, 12)
    keys, info = follow.plan(s, 0, 12, ww, 1.0)
    assert keys and info["moves"] == 1 and info["overhang"] == 0
    span = {"t0": 0, "t1": 12, "k": keys}
    v, a = _speed(span, 0, 12)
    assert np.abs(v).max() <= follow.VMAX * ww * 1.02            # no whip
    assert np.abs(a).max() <= follow.AMAX * ww * 1.1
    moving = np.flatnonzero(np.abs(v) > 1e-6) / 30.0
    assert 4.5 < moving[0] < 6.0 < moving[-1] < 7.5            # around the step
    assert follow.centre_at(span, 0)[0] < .33 and follow.centre_at(span, 12)[0] > .45


def test_a_walk_is_a_steady_pan_not_stop_and_go():
    s = _track(lambda t: .25 + .08 * min(6, max(0, t - 2)), 0, 10.75)
    keys, info = follow.plan(s, 0, 10.75, 607.5 / 1920, 1.0)
    span = {"t0": 0, "t1": 10.75, "k": keys}
    v, _a = _speed(span, 0, 10.75)
    walk = v[int(4.5 * 30):int(7.5 * 30)]
    assert walk.min() > .03 and info["overhang"] == 0           # never stops


def test_a_move_happens_on_the_jump_cut_when_it_can():
    """The speaker steps while the edit removed 4.6-5.4: the re-aim lands
    inside the removed footage, so no kept frame shows a glide."""
    s = _track(lambda t: .30 if t < 5 else .48, 0, 10)
    kept = [(0.0, 4.6), (5.4, 10.0)]
    keys, info = follow.plan(s, 0, 10, 607.5 / 1920, 1.0, kept=kept)
    assert info["hidden"] == 1
    span = {"t0": 0, "t1": 10, "k": keys}
    for a, b in kept:
        xs = {round(follow.centre_at(span, t)[0], 6) for t in np.arange(a, b, .05)}
        assert len(xs) == 1


def test_lead_room_follows_the_look_and_headroom_the_crown():
    ww, wh = .4, .5
    s = _track(lambda t: .50 if t < 5 else .30, 0, 10, look=1)
    keys, _info = follow.plan(s, 0, 10, ww, wh)
    cx, cy = follow.centre_at({"t0": 0, "t1": 10, "k": keys}, 9.5)
    assert cx > .30                       # room on the side he looks to
    crown = .35 - .06 - follow.HAIR_ABOVE_FACE * .12
    top = cy - wh / 2
    assert crown - top == pytest.approx(follow.HEADROOM_TARGET * wh, abs=.03)


def test_plans_are_deterministic_and_detector_blips_are_dropped():
    s = _track(lambda t: .30 if t < 6 else .48, 0, 12)
    assert follow.plan(s, 0, 12, .3164, 1.0) == follow.plan(list(s), 0, 12, .3164, 1.0)
    frames = [(t, [(_face(.4), 0)] + ([(_face(.9), 0)] if i == 7 else []))
              for i, t in enumerate(np.arange(0, 4, .25))]
    frames[8] = (frames[8][0], [(_face(.85), 0)])                 # a one-frame jump
    track = follow.speaker_track(frames)
    assert all(abs((b[0] + b[2]) / 2 - .4) < .01 for _t, b, _l in track)
    assert len(track) == len(frames) - 1


# ── the data ──────────────────────────────────────────────────────────────

def _frame_edl(follow_spans, keep=((1.0, 6.0),), **frame):
    e = default_edl(SRC)
    e["keep"] = [list(k) for k in keep]
    e["frame"] = {"ratio": "9:16", "mode": "crop", "follow": follow_spans, **frame}
    return validate_edl(e, SRC).model_dump()


MOVE = [{"t0": 1.0, "t1": 6.0, "k": [[2.0, .3, .5], [3.5, .7, .5], [4.0, .7, .5],
                                      [5.0, .45, .5]]}]


def test_follow_spans_are_canonical_and_bounded():
    e = _frame_edl(MOVE)
    assert e["frame"]["follow"][0]["k"][1] == [3.5, .7, .5]
    plain = default_edl(SRC)
    plain["keep"] = [[1.0, 6.0]]
    plain["frame"] = {"ratio": "9:16", "mode": "crop"}
    plain = validate_edl(plain, SRC).model_dump()
    assert plain["frame"]["follow"] is None and '"follow"' not in edl_signature(plain)
    for bad in ([{"t0": 1, "t1": 6, "k": [[3, .5, .5], [2, .5, .5]]}],
                [{"t0": 1, "t1": 6, "k": []}],
                [{"t0": 1, "t1": 6, "k": [[3, .5]]}],
                [{"t0": 1, "t1": 4, "k": [[2, .5, .5]]},
                 {"t0": 3, "t1": 6, "k": [[4, .5, .5]]}]):
        with pytest.raises(Exception):
            _frame_edl(bad)
    # nothing to move: a whole-frame or fitted frame drops it
    assert _frame_edl(MOVE, mode="pad_blur")["frame"]["follow"] is None
    e = default_edl(SRC)
    e["keep"] = [[1.0, 6.0]]
    e["frame"] = {"ratio": "source", "follow": MOVE}
    assert not (validate_edl(e, SRC).model_dump()["frame"] or {}).get("follow")


def _graph(e, W=202, H=360, src=(640, 360), preview=False, inputs=None):
    tl = Timeline(e["keep"], e.get("inserts") or [], e.get("speed") or [])
    return renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, preview, W=W, H=H, fps=30,
        frame_mode="crop", src_w=src[0], src_h=src[1], silence_idx=1,
        picture_card_inputs=inputs), tl


def test_a_span_edge_inside_a_kept_segment_splits_the_blocks():
    spans = [{"t0": 1.0, "t1": 3.4, "k": [[1.5, .3, .5], [2.5, .6, .5]]},
             {"t0": 3.4, "t1": 6.0, "k": [[4.0, .7, .5], [5.0, .4, .5]]}]
    g, _tl = _graph(_frame_edl(spans))
    trims = [p for p in g.split(";") if "trim=start=" in p and "[segv" in p]
    assert len(trims) == 2
    cut = float(trims[0].split("end=")[1].split(",")[0])
    assert 3.38 < cut < 3.45            # the handoff rule puts it a frame on
    assert g.count("perspective=") == 2


def test_nothing_changes_without_a_follow():
    """No follow: the exact legacy graph (no perspective, no follow crop)."""
    e = _frame_edl(None, focus_x=.3)
    g, _tl = _graph(e)
    assert "perspective" not in g and "ld(9)" not in g


# ── the render ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def grid(tmp_path_factory):
    """A static 640x360 texture: thin vertical lines every 40 px, a luma
    ramp across — any horizontal shift of a window is measurable."""
    path = tmp_path_factory.mktemp("follow") / "grid.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=gray:s=640x360:r=30:d=10,format=yuv444p,"
         "geq=lum='if(lt(mod(X,40),2),235,if(lt(mod(Y,40),2),200,60+X/8))'"
         ":cb='128+Y/8':cr=128",
         "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", "-g", "1",
         str(path)], check=True)
    return path


def _render(path, graph, tl, W, H, out):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-f", "lavfi", "-i",
         "anullsrc=r=48000:cl=stereo", "-filter_complex", graph, "-map", "[vout]",
         "-map", "[aout]", "-t", str(tl.out_duration), "-c:v", "libx264", "-qp", "0",
         "-pix_fmt", "yuv444p", "-an", str(out)], capture_output=True, text=True,
        timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(out), "-f",
                                   "rawvideo", "-pix_fmt", "gray", "-"])
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(float)


def _ref(grid):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(grid), "-frames:v",
                                   "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    return np.frombuffer(raw, np.uint8).reshape(360, 640).astype(float)


def _offset(ref_row, row, lo=0.0, hi=440.0, step=.125, x0=4):
    """Sub-pixel shift of ``row`` (an output row) inside ``ref_row``."""
    xs = np.arange(len(ref_row), dtype=float)
    idx = np.arange(x0, x0 + len(row))
    best = None
    for s in np.arange(lo, hi, step):
        e = ((np.interp(idx + s, xs, ref_row) - row) ** 2).mean()
        if best is None or e < best[0]:
            best = (e, s)
    return best[1]


@FFMPEG
def test_a_following_crop_lands_where_the_mirror_says_on_every_frame(grid, tmp_path):
    e = _frame_edl(MOVE)
    g, tl = _graph(e)
    frames = _render(grid, g, tl, 202, 360, tmp_path / "o.mp4")
    ref = _ref(grid)[37]
    meas, want = [], []
    for n, f in enumerate(frames):
        meas.append(_offset(ref, f[37][4:-4]))
        cx, _cy = follow.centre_at(MOVE[0], 1.0 + n / 30.0)
        want.append(min(max(cx * 640 - 101, 0), 640 - 202))
    err = np.abs(np.array(meas) - np.array(want))
    assert err.max() <= 1.4                       # holds snap to even pixels
    # sub-pixel smooth: the rise 2.0-3.5 s never steps back or stalls
    rise = np.diff(meas[30:76])
    assert rise.min() > -.13 and (rise[3:-3] > .05).all()
    # a hold is an exact, still crop
    assert np.ptp(meas[80:90]) == 0 and np.ptp(meas[130:]) == 0


@FFMPEG
def test_draft_previews_and_finals_draw_the_same_path(grid, tmp_path):
    e = _frame_edl(MOVE)
    g_final, tl = _graph(e)
    tok = renderer._PREVIEW_QUALITY.set("draft")
    try:
        g_draft, _ = _graph(e, preview=True)
    finally:
        renderer._PREVIEW_QUALITY.reset(tok)
    assert "interpolation=linear" in g_draft and "interpolation=cubic" in g_final
    a = _render(grid, g_final, tl, 202, 360, tmp_path / "a.mp4")
    b = _render(grid, g_draft, tl, 202, 360, tmp_path / "b.mp4")
    assert np.abs(a[85] - b[85]).max() == 0          # holds: identical
    assert np.abs(a - b).mean() < 1.5                # moves: same path


@FFMPEG
def test_a_picture_rect_and_a_sped_block_follow_too(grid, tmp_path):
    e = _frame_edl(MOVE, picture=[0, .2, 1, .8])
    g, tl = _graph(e)
    frames = _render(grid, g, tl, 202, 360, tmp_path / "p.mp4")
    assert frames[0][10].max() < 30 and frames[0][180].max() > 200   # bars, picture
    sped = default_edl(SRC)
    sped["keep"] = [[1.0, 6.0]]
    sped["speed"] = [{"id": "s1", "start": 1.0, "end": 6.0, "factor": 2.0}]
    sped["frame"] = {"ratio": "9:16", "mode": "crop", "follow": MOVE}
    sped = validate_edl(sped, SRC).model_dump()
    g, tl = _graph(sped)
    assert "if(lt(" in g or "*2.000000" in g
    frames = _render(grid, g, tl, 202, 360, tmp_path / "s.mp4")
    ref = _ref(grid)[37]
    n = 40                                          # 1.333 program s = 2.667 source s
    cx, _ = follow.centre_at(MOVE[0], 1.0 + 2 * n / 30.0)
    assert abs(_offset(ref, frames[n][37][4:-4]) - (cx * 640 - 101)) <= 1.5


@FFMPEG
def test_a_following_card_moves_its_rect(grid, tmp_path):
    card = {"id": "c", "start": 0.0, "end": 5.0, "box": [.05, .3, .95, .55],
            "fit": "crop", "background": "#406080", "entrance": "none",
            "exit": "none", "shadow": 0, "border": 0, "source": [.3, .2, .6, .8],
            "follow": [{"t0": 1.0, "t1": 6.0, "k": [[2.0, .3, .5], [3.0, .6, .5]]}]}
    e = default_edl(SRC)
    e["keep"] = [[1.0, 6.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["effects"] = {"picture_cards": [card]}
    e = validate_edl(e, SRC).model_dump()
    card = e["effects"]["picture_cards"][0]
    W, H = 202, 360
    args = ["-i", str(grid)]
    inputs, _ = picture_cards.prepare_inputs(e, str(tmp_path), W, H, 30, args, 2)
    tl = Timeline(e["keep"], [], [])
    g = renderer.build_filtergraph(
        e, SRC, False, tl, None, [], {}, False, W=W, H=H, fps=30, frame_mode="crop",
        src_w=640, src_h=360, silence_idx=1, picture_card_inputs=inputs)
    assert "perspective=" in g and "valmera_card" in g
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(grid), "-f", "lavfi",
                        "-i", "anullsrc=r=48000:cl=stereo", *args[2:], "-filter_complex",
                        g, "-map", "[vout]", "-map", "[aout]", "-an", "-t", "5",
                        "-c:v", "libx264", "-qp", "0",
                        "-pix_fmt", "yuv444p", str(tmp_path / "c.mp4")],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-3000:]
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(tmp_path / "c.mp4"),
                                   "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    frames = np.frombuffer(raw, np.uint8).reshape(-1, H, W).astype(float)
    x, y, w, h = picture_cards.pixels(W, H, card["box"])
    ref = _ref(grid)[15]                           # a row between grid lines
    for n in (15, 45, 75, 120):
        rect = picture_cards.source_at(card, 1.0 + n / 30.0)
        rect = picture_cards.match_rect(rect, card["box"], 640, 360, W, H)
        k = w / ((rect[2] - rect[0]) * 640)
        # a canvas row whose source row sits between horizontal grid lines
        row_y = next(yc for yc in range(y + 2, y + h - 2)
                     if 10 <= (rect[1] * 360 + (yc - y) / k) % 40 <= 30)
        row = frames[n][row_y][x + 4:x + w - 4]
        ref_scaled = np.interp(np.arange(0, 640, 1 / k), np.arange(640), ref)
        got = _offset(ref_scaled, row, 0, len(ref_scaled) - len(row) - 8, .25) / k
        assert abs(got - rect[0] * 640) <= 1.5, (n, got, rect[0] * 640)


@FFMPEG
def test_a_proof_fragment_draws_the_same_follow(grid, tmp_path):
    e = _frame_edl(MOVE)
    g, tl = _graph(e)
    full = _render(grid, g, tl, 202, 360, tmp_path / "f.mp4")
    part = validate_edl(stitch.window_edl(e, tl, 1.4, 3.2), SRC).model_dump()
    assert part["frame"]["follow"] == e["frame"]["follow"]
    g2, tl2 = _graph(part)
    frag = _render(grid, g2, tl2, 202, 360, tmp_path / "g.mp4")
    ref = _ref(grid)[37]
    for t in (.1, .8, 1.5):
        a = _offset(ref, full[round((1.4 + t) * 30)][37][4:-4])
        b = _offset(ref, frag[round(t * 30)][37][4:-4])
        assert abs(a - b) <= 1.0


def test_stitching_refuses_a_recut_follow():
    a = _frame_edl(MOVE)
    b = _frame_edl(MOVE, keep=((1.0, 3.0), (3.5, 6.0)))
    tl_a, tl_b = Timeline(a["keep"], [], []), Timeline(b["keep"], [], [])
    windows, _runs, why = stitch.plan_timeline(a, b, tl_a, tl_b, tl_b.out_duration)
    assert windows is None and "face-following" in why


# ── the mirrors ───────────────────────────────────────────────────────────

def test_keep_out_sees_the_face_where_the_follow_put_it():
    """The face walks from x .30 to .55; a follow that tracks it keeps it
    put on the canvas — the keep-out (and the caption plan reading it)
    must see that, not the static aim's drifting face."""
    spans = [{"t0": 1.0, "t1": 6.0, "k": [[2.0, .30, .5], [5.0, .55, .5]]}]
    e = _frame_edl(spans, focus_x=.3)
    geo = keepout.Geometry(e, {"width": 1920, "height": 1080}, 1080, 1920, 5.0)
    xs = []
    for src_t, fx in ((2.0, .30), (3.5, follow.centre_at(spans[0], 3.5)[0]), (5.0, .55)):
        out = geo.to_output(src_t - 1.0, src_t, _face(fx, .35, .2))
        xs.append((out[0] + out[2]) / 2)
    assert max(xs) - min(xs) < .01 and abs(xs[0] - .5) < .01
    import caption_carry
    assert caption_carry.face_geometry(e) != caption_carry.face_geometry(_frame_edl(None, focus_x=.3))


def test_a_cards_face_is_mapped_through_its_follow():
    card = {"id": "c", "start": 0.0, "end": 5.0, "box": [.05, .3, .95, .55],
            "fit": "crop", "source": [.2, .1, .5, .7],
            "follow": [{"t0": 1.0, "t1": 6.0, "k": [[2.0, .35, .4], [4.0, .65, .4]]}]}
    e = default_edl(SRC)
    e["keep"] = [[1.0, 6.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["effects"] = {"picture_cards": [card]}
    e = validate_edl(e, SRC).model_dump()
    geo = keepout.Geometry(e, {"width": 1920, "height": 1080}, 1080, 1920, 5.0)
    a = geo.to_output(1.0, 2.0, _face(.35, .4, .2))
    b = geo.to_output(3.0, 4.0, _face(.65, .4, .2))
    assert abs((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2) < .01


def test_behind_depth_yields_and_renders_are_stamped():
    e = _frame_edl(MOVE)
    assert follow.moves_during(e, [(3.0, 3.2)]) and not follow.moves_during(e, [(3.6, 3.9)])
    assert not renderer.follow_current({}, e)
    assert renderer.follow_current({"follow_v": config.FOLLOW_VERSION}, e)
    assert renderer.follow_current({}, _frame_edl(None))


# ── the tools ─────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self, width, height, samples, keep=((10.0, 20.0),), shots=None):
        self.has_main_video = True
        self.workdir = None
        self.duration = SRC
        self.index = {"video": {"width": width, "height": height, "fps": 30,
                                "duration": SRC},
                      "spatial": {"v": 1, "samples": samples}}
        if shots:
            self.index["shots"] = shots
        e = default_edl(SRC)
        e["keep"] = [list(k) for k in keep]
        e["frame"] = {"ratio": "9:16", "mode": "crop"}
        self._edl = validate_edl(e, SRC).model_dump()
        self.written = []

    def latest_edl(self):
        return {"json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), SRC).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written) + 1}: {desc}"

    def proxy_path(self):
        raise RuntimeError("no proxy in this test")


def _dense(fn, t0, t1, h=.2):
    return [{"t": round(t, 2), "faces": [_face(fn(t), .35, h)]}
            for t in np.arange(t0, t1 + 1e-9, .25)]


def test_a_reframe_follows_a_moving_speaker_and_leaves_a_still_one():
    walk = _dense(lambda t: .30 if t < 15 else .55, 10, 20)
    ctx = _Ctx(1920, 1080, walk)
    res = agent_tools.set_frame(ctx, "9:16", "crop", .3, .5, _measured=True,
                                _follow=True)
    spans = ctx._edl["frame"]["follow"]
    assert spans and len(spans) == 1 and "FOLLOWS the speaker inside 1 shot" in res
    assert follow.centre_at(spans[0], 11)[0] < .4 < follow.centre_at(spans[0], 19)[0]
    still = _Ctx(1920, 1080, _dense(lambda t: .40 + .01 * np.sin(t), 10, 20))
    agent_tools.set_frame(still, "9:16", "crop", .4, .5, _measured=True, _follow=True)
    assert still._edl["frame"]["follow"] is None


def test_follow_spans_stop_on_camera_cuts():
    samples = _dense(lambda t: .30 if t < 13 else (.55 if t < 15 else .70), 10, 20)
    ctx = _Ctx(1920, 1080, samples, shots=[{"id": 0, "start": 0.0, "end": 15.0},
                                            {"id": 1, "start": 15.0, "end": 60.0}])
    agent_tools.set_frame(ctx, "9:16", "crop", .3, .5, _measured=True, _follow=True)
    spans = ctx._edl["frame"]["follow"]
    assert spans and all(sp["t1"] <= 15.0 or sp["t0"] >= 15.0 for sp in spans)
    g, _tl = _graph(ctx._edl, src=(1920, 1080))
    trims = [p for p in g.split(";") if "trim=start=" in p and "[segv" in p]
    assert len(trims) == 2 and "end=15.0" in trims[0]


def test_a_source_card_follows_from_a_dense_track():
    # the speaker steps across the stage in about a second
    samples = _dense(lambda t: .35 + .25 * min(1.0, max(0.0, t - 14.5)), 10, 20, h=.18)
    ctx = _Ctx(1920, 1080, samples)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9, box=[.04, .2, .96, .55])
    card = ctx._edl["effects"]["picture_cards"][0]
    assert card["follow"] and "FOLLOWS the speaker inside 1 of 1 shot" in res
    early = picture_cards.source_at(card, 11.0)
    late = picture_cards.source_at(card, 19.0)
    assert (early[0] + early[2]) / 2 < .45 < (late[0] + late[2]) / 2
    # every face sample stays inside the card's rect along the path
    for row in samples:
        r = picture_cards.source_at(card, row["t"])
        f = row["faces"][0]
        assert r[0] <= f[0] and f[2] <= r[2] and r[1] <= f[1] and f[3] <= r[3]


def test_a_low_resolution_crop_card_frames_the_speaker():
    samples = _dense(lambda t: .45, 10, 20, h=.2)
    ctx = _Ctx(646, 480, samples)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9, box=[.04, .265, .96, .685],
                                       fit="crop")
    card = ctx._edl["effects"]["picture_cards"][0]
    assert card["source"] != picture_cards.archival_rect() and card["fit"] == "crop"
    assert "framed on the speaker at most 2x" in res and "enlarged 2.00x" in res
    # the default stays the whole frame
    ctx = _Ctx(646, 480, samples)
    agent_tools.set_picture_card(ctx, "c", 0, 9, box=[.04, .265, .96, .685])
    assert ctx._edl["effects"]["picture_cards"][0]["source"] == picture_cards.archival_rect()


def test_follow_can_be_switched_off_and_added_to_an_authored_track():
    samples = _dense(lambda t: .35 + .25 * min(1.0, max(0.0, t - 14.5)), 10, 20, h=.18)
    ctx = _Ctx(1920, 1080, samples)
    agent_tools.set_picture_card(ctx, "c", 0, 9, box=[.04, .2, .96, .55], follow=False)
    assert ctx._edl["effects"]["picture_cards"][0]["follow"] is None
    # an authored per-shot track that already covers the cut keeps its aims;
    # auto_reframe adds the follow inside the shot instead of "NO CHANGE"
    ctx = _Ctx(1920, 1080, samples)
    agent_tools.set_frame(ctx, "9:16", "crop", .35, .5, focus_track=[
        {"t0": 10.0, "t1": 20.0, "x": .35, "y": .5, "mode": "crop"}])
    assert ctx._edl["frame"]["follow"] is None          # set_frame is still
    res = agent_tools.auto_reframe(ctx, "9:16")
    assert ctx._edl["frame"]["follow"] and "follows the speaker" in res
    assert ctx._edl["frame"]["focus_track"][0]["x"] == .35
    off = _Ctx(1920, 1080, samples)
    agent_tools.set_frame(off, "9:16", "crop", .35, .5, focus_track=[
        {"t0": 10.0, "t1": 20.0, "x": .35, "y": .5, "mode": "crop"}])
    assert agent_tools.auto_reframe(off, "9:16", follow=False).startswith("NO CHANGE")
