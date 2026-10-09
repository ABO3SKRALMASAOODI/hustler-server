"""Cut and zoom hygiene (judges, Oct 2026).

Findings from the showcase shorts, each fixed in the engine:

* an 'ease' zoom whose window ended on a cut released over its last frames,
  so the wide flashed a frame before the new shot (Elon 21.888), and an
  ease starting on a cut began at 1.0x and left the jump bare —
  camera.hold_through_cuts moves a zoom edge within 4 frames of a cut onto
  it and holds the strength through the cut;
* the cut itself landed a frame before or after the zoom edge timed to it,
  because each concat block took however many frames its trim produced —
  renderer.block_clock gives every block an exact frame count starting on
  the frame its Timeline start names, with sound exactly as long;
* keep joins butted two waveforms (and up to a frame of silence) together —
  each cut between kept spans is an equal-power micro-crossfade centred on
  the cut (renderer.join_fades), without changing any block's length;
* jump cuts with no framing change — taste.uncovered_jump_cuts names each
  one with a one-line fix.

Run:  python -m pytest tests/test_cut_hygiene.py -q     (from worker/)
"""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camera                                                 # noqa: E402
import renderer                                               # noqa: E402
import stitch                                                 # noqa: E402
import taste                                                  # noqa: E402
from schemas import validate_edl                              # noqa: E402
from timeline import Timeline                                 # noqa: E402

FPS = 30.0
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None,
                                  reason="ffmpeg not present")


def _z(**kw):
    z = {"id": "z", "start": 1.0, "end": 3.0, "strength": 0.2,
         "mode": "ease"}
    z.update(kw)
    return z


def _strength(z, t):
    return float(camera.zoom_terms(z, t, float(z["start"]),
                                   float(z["end"])).z)


# --------------------------------------------------------------- the camera

def test_an_ease_ending_on_a_cut_holds_to_the_cut():
    z = _z(end=5.0)
    (held,) = camera.hold_through_cuts([z], [5.0], FPS)
    assert held["_hold_out"] and held["end"] == 5.0
    last = (int(np.ceil(5.0 * FPS)) - 1) / FPS     # last frame before it
    assert _strength(z, last) < 0.01                # it used to release
    assert _strength(held, last) == pytest.approx(0.2)
    assert _strength(held, 5.0) == 0.0              # the cut changes it


def test_an_edge_a_few_frames_off_a_cut_moves_onto_it():
    # Ends 2 frames BEFORE the cut: those frames were the wide.
    (a,) = camera.hold_through_cuts([_z(end=4.94)], [5.0], FPS)
    assert a["end"] == 5.0 and _strength(a, 4.97) == pytest.approx(0.2)
    # Ends 2 frames AFTER it: the new shot's first frames wore the zoom.
    (b,) = camera.hold_through_cuts([_z(mode="punch", ramp_s=0, end=5.06)],
                                    [5.0], FPS)
    assert b["end"] == 5.0 and _strength(b, 5.0) == 0.0
    # Past CUT_HOLD_FRAMES it is a deliberate release and stays.
    far = _z(end=4.8)
    assert camera.hold_through_cuts([far], [5.0], FPS)[0] is far


def test_a_zoom_starting_on_a_cut_is_already_in_on_its_first_frame():
    z = _z(start=2.0, end=4.0)
    (held,) = camera.hold_through_cuts([z], [2.0], FPS)
    assert held["_hold_in"]
    assert _strength(z, 2.0) == 0.0                 # 1.0x on the cut
    assert _strength(held, 2.0) == pytest.approx(0.2)
    assert _strength(held, 2.0 - 1 / FPS) == 0.0
    punch = _z(mode="punch", start=2.0, end=4.0)     # expo attack
    (hp,) = camera.hold_through_cuts([punch], [2.0], FPS)
    assert _strength(hp, 2.0) == pytest.approx(0.2)


def test_untouched_zooms_keep_their_dict_and_their_text():
    zs = [_z(start=0.5, end=1.5),                    # nowhere near a cut
          _z(mode="punch", ramp_s=0, start=2.0, end=4.0),   # already exact
          _z(mode="punch", overshoot=0.2, start=2.0, end=4.0),  # a slam
          _z(mode="pulse", ramp_s=0.3, start=3.98, end=4.3),
          _z(mode="shake", start=3.97, end=4.4)]
    out = camera.hold_through_cuts(zs, [2.0, 4.0], FPS)
    assert all(a is b for a, b in zip(zs, out))
    assert camera.changed(zs, out) == []


def test_the_programme_end_is_a_cut_for_zoom_ends():
    (held,) = camera.hold_through_cuts([_z(start=8.0, end=9.97)], [], FPS,
                                       out_duration=10.0)
    assert held["end"] == 10.0 and held["_hold_out"]
    over = _z(start=8.0, end=12.0)                   # overhanging ease
    (h2,) = camera.hold_through_cuts([over], [], FPS, out_duration=10.0)
    assert h2["end"] == 10.0 and h2["_hold_out"]
    punch = _z(mode="punch", start=8.0, end=12.0)    # clamped anyway
    assert camera.hold_through_cuts([punch], [], FPS, 10.0)[0] is punch


def test_a_snap_never_collapses_a_short_zoom():
    z = _z(start=4.95, end=5.1)
    assert camera.hold_through_cuts([z], [5.0], FPS)[0]["end"] == 5.1


def test_camera_cuts_are_the_frames_the_picture_changes():
    edl = {"keep": [[0, 4], [6, 9], [9, 12]],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5,
                     "focus_track": [{"t0": 0, "t1": 10.0, "x": 0.3},
                                     {"t0": 10.0, "t1": 12, "x": 0.7}]}}
    tl = Timeline(edl["keep"])
    index = {"video": {"fps": FPS},
             "shots": [{"id": 0, "start": 0, "end": 2.5},
                       {"id": 1, "start": 2.5, "end": 20}]}
    cuts = renderer.camera_cuts(edl, index, tl, FPS, FPS, 0.0)
    assert 4.0 in cuts                    # the jump at the first join
    assert not any(abs(c - 7.0) < 0.05 for c in cuts)  # 9|9 is one run
    # The indexed camera cut at source 2.5 and the crop's re-aim at 10.0,
    # both half a frame before the new composition's first frame.
    assert any(abs(c - (2.5 - 0.5 / FPS)) < 1e-3 for c in cuts)
    assert any(abs(c - (8.0 - 0.5 / FPS)) < 1e-3 for c in cuts)


def test_an_old_render_stays_spliceable_unless_a_zoom_sits_on_a_cut():
    far = {"keep": [[0, 4], [6, 9]], "effects": {"zooms": [
        _z(start=0.5, end=2.0)]}}
    near = {"keep": [[0, 4], [6, 9]], "effects": {"zooms": [
        _z(start=0.5, end=4.0)]}}
    assert renderer.camera_current({"cam_v": 1}, far)
    assert not renderer.camera_current({"cam_v": 1}, near)
    assert renderer.camera_current(
        {"cam_v": renderer.config.CAMERA_VERSION}, near)
    assert not renderer.camera_current({"cam_v": 0}, far)


def test_a_stitched_piece_contains_every_zoom_with_cut_room():
    edl = {"keep": [[0, 10]], "effects": {"zooms": [_z(start=2, end=4)]}}
    (a, b, kind), = stitch._item_windows(edl, Timeline(edl["keep"]), 10)
    assert kind == "zoom"
    assert a <= 2 - 4 / 23.976 and b >= 4 + 4 / 23.976


def test_look_at_reads_the_held_zooms():
    edl = {"keep": [[0, 4], [6, 9]], "effects": {"zooms": [
        _z(start=1.0, end=4.0)]}}
    zs = renderer.camera_zooms(edl, {"video": {"fps": FPS}})
    z, _cx, _cy = renderer.zoom_state_at(zs, 4.0 - 1 / FPS, 7.0)
    assert z == pytest.approx(1.2)


def test_a_held_start_never_shrinks_the_step_across_its_cut():
    # The Elon 18.88 shape: a 0.12 punch ends on the jump cut and a 0.08
    # ease starts on it. In on its first frame the ease made the cut a 1.12x
    # -> 1.08x step (4%, a bare jump); ramping up from the wide steps 12% on
    # the cut, so the start ramps — and keeps its dict, since nothing moved.
    punch = _z(id="p", mode="punch", ramp_s=0, start=1.0, end=2.0,
               strength=0.12)
    ease = _z(id="e", start=2.0, end=5.0, strength=0.08)
    out = camera.hold_through_cuts([punch, ease], [2.0], FPS)
    assert out[1] is ease and "_hold_in" not in out[1]
    before = renderer.zoom_state_at(out, 2.0 - 0.5 / FPS, 9.0)[0]
    after = renderer.zoom_state_at(out, 2.0 + 0.5 / FPS, 9.0)[0]
    assert before / after - 1.0 >= taste.JUMP_CUT_MIN_SCALE
    # Off the wide, or into a stronger zoom, being in on the cut is the
    # bigger change and the hold stays.
    strong = _z(id="s", start=2.0, end=5.0, strength=0.3)
    assert camera.hold_through_cuts([punch, strong], [2.0],
                                    FPS)[1]["_hold_in"]
    alone = camera.hold_through_cuts([ease], [2.0], FPS)[0]
    assert alone["_hold_in"]
    # The taste note agrees: the cut is covered.
    edl = {"keep": [[0, 2], [3, 9]], "effects": {"zooms": [punch, ease]}}
    assert _bare(edl) == []


def test_preview_final_and_mirrors_snap_the_same_zooms():
    # A preview renders a 60 fps source at 30 fps; its final at 60. Counted
    # in output frames, an edge 3 preview frames off its cut snapped in the
    # preview and released before the cut in the final.
    assert camera.cut_reach_s(60.0) == camera.cut_reach_s(30.0)
    assert camera.cut_reach_s(24.0) > camera.cut_reach_s(30.0)
    z = _z(end=4.9)
    for fps in (30.0, 59.94, 60.0):
        (held,) = camera.hold_through_cuts([z], [5.0], fps)
        assert held["end"] == 5.0 and held["_hold_out"], fps


def test_an_old_render_is_checked_on_every_focus_origin():
    # A crop re-aim at source 10.0 is placed where the source's first frame
    # sits (focus_handoff): 9.983 on a zero-origin download, 10.033 on the
    # legacy rule. A zoom ending 0.12 s before the first is held onto it by
    # the render, so a v1 render of it is never spliced into a v2 one.
    edl = {"keep": [[0, 20]],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5,
                     "focus_track": [{"t0": 0, "t1": 10.0, "x": 0.3},
                                     {"t0": 10.0, "t1": 20, "x": 0.7}]},
           "effects": {"zooms": [_z(start=8.0, end=9.86)]}}
    index = {"video": {"fps": FPS}}
    legacy = renderer.camera_zooms(edl, index)
    assert not camera.changed(edl["effects"]["zooms"], legacy)
    zero = renderer.camera_zooms(edl, index, origin=0.0)
    assert camera.changed(edl["effects"]["zooms"], zero)
    assert not renderer.camera_current({"cam_v": 1}, edl, index)


# ----------------------------------------------------------- the block clock

def test_block_clock_starts_every_block_on_the_frame_its_start_names():
    lengths = [3.24, 2.83, 0.01, 2.87, 1.5]
    frames = renderer.block_clock(lengths, FPS)
    assert all(n >= 1 for n in frames)
    acc = 0.0
    for k, L in enumerate(lengths[:2]):
        acc += L
        assert sum(frames[:k + 1]) == renderer.first_frame_at(acc, FPS)
    assert renderer.first_frame_at(3.24, FPS) == 98       # 97.2 -> 98
    assert renderer.first_frame_at(3.0, FPS) == 90        # exact frame
    assert renderer.first_frame_at(1.74, 29.97) == 53     # 52.15 -> 53


def test_the_output_length_covers_the_clock_so_the_end_card_is_whole():
    tl = Timeline([[1.03, 4.27], [6.11, 8.94], [10.5, 13.37]])
    assert tl.out_duration == pytest.approx(8.94)
    assert renderer.program_render_s(tl, FPS) == pytest.approx(269 / FPS)
    one = Timeline([[1.0, 9.0]])
    assert renderer.program_render_s(one, FPS) == one.out_duration
    # One block's `trim=end` after `fps` keeps the frames up to the one its
    # end names as well — a focus-track split of it runs on the clock — so
    # -t never trims the card after it either.
    odd = Timeline([[1.03, 9.94]])
    assert renderer.program_render_s(odd, 29.97) == pytest.approx(
        268 / 29.97)


def test_a_focus_track_makes_one_kept_span_multi_block():
    split = {"keep": [[0, 9]], "frame": {"ratio": "9:16", "focus_track": [
        {"t0": 0, "t1": 4, "x": 0.3}, {"t0": 4, "t1": 9, "x": 0.7}]}}
    assert not renderer.block_clock_current({}, split)
    assert renderer.block_clock_current(
        {"clock_v": renderer.config.BLOCK_CLOCK_VERSION}, split)


def test_old_multi_block_pictures_are_never_spliced_onto_the_new_clock():
    multi = {"keep": [[0, 4], [6, 9]]}
    assert not renderer.block_clock_current({}, multi)
    assert renderer.block_clock_current(
        {"clock_v": renderer.config.BLOCK_CLOCK_VERSION}, multi)
    assert renderer.block_clock_current({}, {"keep": [[0, 9]]})
    import inspect
    src = inspect.getsource(renderer)
    assert "block_clock_current(pm, prev_row[\"json\"])" in src
    assert '"clock_v": (reused_visual_meta.get("clock_v")' in src


def _graph(edl, **kw):
    edl = validate_edl(edl, 60.0).model_dump()
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return renderer.build_filtergraph(
        edl, 60.0, True, tl, None, [], {"words": [], "video": {}},
        preview=False, W=320, H=180, fps=FPS,
        frame_mode=(edl.get("frame") or {}).get("mode"), **kw)


def test_a_multi_block_normalized_programme_runs_on_the_clock():
    g = _graph({"keep": [[1.03, 4.27], [6.11, 8.94]],
                "frame": {"ratio": "9:16", "mode": "crop"}})
    # 3.24 s -> frames 0..97; 2.83 s -> the block up to frame 183 (6.07 s).
    assert "trim=end_frame=98," in g and "trim=end_frame=85," in g
    assert "tpad=stop_mode=clone:stop=2,fps=30.000" in g
    # The sound of the first block is exactly its 98 frames long.
    assert "end=3.266667" in g


def test_one_block_and_the_cheap_path_keep_their_legacy_graph():
    one = _graph({"keep": [[1.0, 9.0]],
                  "frame": {"ratio": "9:16", "mode": "crop"}})
    assert "end_frame" not in one and "afade" not in one
    assert "trim=end=8.000" in one
    cheap = _graph({"keep": [[1.0, 4.0], [6.0, 9.0]]})
    assert "end_frame" not in cheap and "trim=end=" not in cheap


def test_a_cut_between_kept_spans_is_a_centred_crossfade():
    g = _graph({"keep": [[1.0, 4.0], [6.0, 9.0], [9.0, 12.0]],
                "frame": {"ratio": "9:16", "mode": "crop"}})
    # Block 0 reads 6 ms past its end, block 1 6 ms before its start; each
    # hands that handle to the other's mix.
    assert "[ain0]atrim=start=1.000000:end=4.006000" in g
    assert "afade=t=out:st=2.994000:d=0.012:curve=qsin" in g
    assert "[ain1]atrim=start=5.994000:end=9.000000" in g
    assert "afade=t=in:st=0:d=0.012:curve=qsin" in g
    assert "[axk0][axpre1]amix=inputs=2:duration=first" in g
    assert "[axk1][axpost0]amix=inputs=2:duration=first" in g
    # 9|9 is one continuous waveform: block 2 gets no join treatment.
    assert "[ain2]atrim=start=9.000000:end=12.000000,asetpts=PTS-STARTPTS,"\
        in g


def test_join_fades_shapes():
    spans = [(0, 4), (6, 9), (9, 12), (20, 22)]
    xl, xr, fl, fr = renderer.join_fades(
        spans, [4, 3, 3, 2], 60.0, [True, False, True, False],
        insert_after=[False, False, True, False],
        insert_before=[False, False, False, True])
    assert xr[0] == xl[1] == 0.006            # the jump: a crossfade
    assert xr[1] == xl[2] == 0.0              # one continuous run
    assert fr[2] == fl[3] == 0.006 and xr[2] == 0.0   # an insert between
    _xl, _xr, sl, sr = renderer.join_fades(
        spans[:2], [4, 3], 60.0, [True, False], crossfade=False)
    assert sr[0] == sl[1] == 0.006 and _xr[0] == 0.0  # speed/cheap: fades
    # No source before the start: no handle, no crossfade.
    xl2, xr2, _a, _b = renderer.join_fades([(0, 4), (0.001, 3)], [4, 3],
                                           60.0, [True, False])
    assert xr2[0] == 0.0


# ------------------------------------------------------- jump-cut coverage

def _bare(edl, index=None):
    edl = validate_edl(edl, 60.0).model_dump(exclude_none=True)
    tl = Timeline(edl["keep"], edl.get("inserts") or [], edl.get("speed"))
    return taste.uncovered_jump_cuts(edl, index or {"video": {"fps": FPS}},
                                     tl)


def test_bare_jump_cuts_are_named_with_an_alternating_fix():
    rows = _bare({"keep": [[0, 4], [5, 8], [9, 12], [13, 16]]})
    assert [r["t"] for r in rows] == [4.0, 7.0, 10.0]
    assert "add_zoom start=4.00 end=7.00 strength=0.12" in rows[0]["fix"]
    assert "covered by the punch above" in rows[1]["fix"]
    assert "add_zoom start=10.00" in rows[2]["fix"]
    line = taste.jump_cut_line(rows)
    assert "3 jump cuts" in line and "4s" in line


def test_a_real_framing_step_covers_a_jump_and_a_5pct_punch_does_not():
    keep = [[0, 4], [5, 8]]
    ok = _bare({"keep": keep, "effects": {"zooms": [
        {"id": "p", "start": 4.0, "end": 7.0, "strength": 0.12,
         "mode": "punch", "ramp_s": 0}]}})
    assert ok == []
    weak = _bare({"keep": keep, "effects": {"zooms": [
        {"id": "p", "start": 4.0, "end": 7.0, "strength": 0.05,
         "mode": "punch", "ramp_s": 0}]}})
    assert len(weak) == 1 and weak[0]["fix"] == (
        "zoom p steps the framing only 5% — raise its strength to 0.12")
    # An ease starting on the cut used to begin at 1.0x — with the hold it
    # is in on the cut's first frame, so the cut is covered.
    eased = _bare({"keep": keep, "effects": {"zooms": [
        {"id": "e", "start": 4.0, "end": 7.0, "strength": 0.12,
         "mode": "ease"}]}})
    assert eased == []


def test_camera_changes_crop_moves_and_transitions_are_not_bare():
    keep = [[0, 4], [5, 8]]
    shots = {"video": {"fps": FPS},
             "shots": [{"id": 0, "start": 0, "end": 4.5},
                       {"id": 1, "start": 4.5, "end": 60}]}
    assert _bare({"keep": keep}, shots) == []
    moved = {"keep": keep, "frame": {
        "ratio": "9:16", "mode": "crop", "focus_track": [
            {"t0": 0, "t1": 4.5, "x": 0.3}, {"t0": 4.5, "t1": 8, "x": 0.6}]}}
    assert _bare(moved) == []
    tr = {"keep": keep, "effects": {"transition": {
        "style": "dip_black", "scope": "every_cut"}}}
    assert _bare(tr) == []


def test_a_weak_punch_is_named_once_and_zoomed_framings_keep_the_step_fix():
    rows = _bare({"keep": [[0, 2], [3, 5], [6, 9]], "effects": {"zooms": [
        {"id": "w", "start": 2.0, "end": 4.0, "strength": 0.05,
         "mode": "punch", "ramp_s": 0}]}})
    assert [r["t"] for r in rows] == [2.0, 4.0]
    assert ("2s, 4s — zoom w steps the framing only 5% — raise its "
            "strength to 0.12") in taste.jump_cut_line(rows)
    # A 1.08x push into a 1.14x punch: a stronger push would SHRINK the
    # step, so the fix is the step itself.
    both = _bare({"keep": [[0, 4], [5, 9]], "effects": {"zooms": [
        {"id": "a", "start": 0.5, "end": 4.0, "strength": 0.08,
         "mode": "push_in"},
        {"id": "b", "start": 4.0, "end": 7.0, "strength": 0.14,
         "mode": "punch", "ramp_s": 0}]}})
    assert len(both) == 1 and "make that step ≥8%" in both[0]["fix"]


def test_a_zoom_running_through_the_cut_is_named():
    rows = _bare({"keep": [[0, 4], [5, 8]], "effects": {"zooms": [
        {"id": "w", "start": 1.0, "end": 6.0, "strength": 0.1,
         "mode": "punch", "ramp_s": 0}]}})
    assert len(rows) == 1 and "zoom w (punch) runs through it" in \
        rows[0]["fix"] and "end=4.00" in rows[0]["fix"]


def test_the_critic_reports_bare_cuts_unless_the_user_said_no_zooms():
    edl = validate_edl({"keep": [[0, 4], [5, 8], [9, 12]]},
                       60.0).model_dump(exclude_none=True)
    tl = Timeline(edl["keep"])
    found = taste.critique(edl, {"video": {"fps": FPS}, "words": []}, tl)
    assert any("jump cuts inside one take" in f for f in found)
    quiet = taste.critique(edl, {"video": {"fps": FPS}, "words": []}, tl,
                           user_asked="cut it tight, no zooms please")
    assert not any("jump cut" in f for f in quiet)


# ------------------------------------------------------------- rendered

RW, RH = 320, 180
BORDER = 14                    # black frame the 1.3x zoom crops away


def _source(path, dur):
    """Frame-coded clip: a centre patch whose grey encodes the frame index
    (k % 40), vertical lines for scale, a black border; a 440 Hz tone with
    a 2 kHz burst at 3.6 s and a white flash on the frame at 3.6 s."""
    base = np.full((RH, RW), 60, np.uint8)
    for x in range(20, RW, 24):
        base[:, x:x + 2] = 235
    base[:BORDER] = base[-BORDER:] = 0
    base[:, :BORDER] = base[:, -BORDER:] = 0
    p = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray",
         "-s", f"{RW}x{RH}", "-r", str(int(FPS)), "-i", "pipe:0",
         "-f", "lavfi", "-i", f"sine=f=440:sample_rate=48000:d={dur}",
         "-f", "lavfi", "-i", f"sine=f=2000:sample_rate=48000:d={dur}",
         "-filter_complex",
         "[1:a]volume=0.3[t];[2:a]volume='0.3*between(t,3.6,3.62)'"
         ":eval=frame[b];[t][b]amix=inputs=2:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "ffv1", "-c:a", "pcm_s16le",
         "-t", str(dur), path], stdin=subprocess.PIPE,
        stderr=subprocess.PIPE)
    for k in range(int(dur * FPS)):
        f = base.copy()
        f[80:100, 150:170] = 40 + (k % 40) * 5
        if k == int(round(3.6 * FPS)):
            f[BORDER:-BORDER, BORDER:-BORDER] = 255
        p.stdin.write(f.tobytes())
    p.stdin.close()
    assert p.wait() == 0, p.stderr.read().decode()


@pytest.fixture(scope="module")
def rendered():
    d = tempfile.mkdtemp(prefix="cuthyg_")
    src = os.path.join(d, "src.mkv")
    _source(src, 9.0)
    keep = [[0.33, 2.07], [3.11, 4.54], [5.2, 7.0]]
    tl0 = Timeline(keep)
    c1, c2 = (round(o, 2) for o in tl0.offsets[1:])          # 1.74, 3.17
    edl = validate_edl({"keep": keep, "effects": {"zooms": [
        # ends ON the first cut (it used to release over its last frames)
        {"id": "a", "start": 0.6, "end": c1, "strength": 0.3,
         "mode": "ease"},
        # starts ON the second cut (it used to begin at 1.0x)
        {"id": "b", "start": c2, "end": 4.3, "strength": 0.3,
         "mode": "ease"}]}}, 9.0).model_dump()
    tl = Timeline(edl["keep"])
    g = renderer.build_filtergraph(
        edl, 9.0, True, tl, None, [], {"video": {"fps": FPS}, "words": []},
        preview=False, W=RW, H=RH, fps=FPS, src_fps=FPS, focus_origin=0.0)
    vid = os.path.join(d, "v.raw")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src, "-filter_complex", g,
         "-map", "[vout]", "-fps_mode", "cfr", "-r", f"{FPS:.3f}",
         "-f", "rawvideo", "-pix_fmt", "gray", vid,
         "-map", "[aout]", "-f", "f32le", "-ac", "1", "-ar", "48000",
         os.path.join(d, "a.raw")], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    frames = np.fromfile(vid, np.uint8).reshape(-1, RH, RW)
    audio = np.fromfile(os.path.join(d, "a.raw"), np.float32)
    yield {"frames": frames, "audio": audio, "tl": tl, "cuts": (c1, c2)}
    shutil.rmtree(d, ignore_errors=True)


def _content_cuts(frames):
    code = frames[:, RH // 2 - 2:RH // 2 + 2, RW // 2 - 2:RW // 2 + 2]\
        .mean(axis=(1, 2))
    return [n for n in range(1, len(code))
            if abs(code[n] - code[n - 1]) > 9 and not (
                code[n - 1] > 200 and code[n] < 70)]


def _zoomed(frame):
    return frame[2:BORDER - 4, 2:BORDER - 4].mean() > 30


@needs_ffmpeg
def test_rendered_cuts_land_on_the_clock_and_the_zoom_holds_to_them(
        rendered):
    frames = rendered["frames"]
    c1, c2 = rendered["cuts"]
    k1 = renderer.first_frame_at(c1, FPS)
    k2 = renderer.first_frame_at(c2, FPS)
    cuts = _content_cuts(frames)
    assert k1 in cuts and k2 in cuts, (k1, k2, cuts)
    # The frame before the first cut keeps the zoomed framing — at FULL
    # strength: its line profile matches the hold's, not the wide's.
    row = slice(RH - BORDER - 30, RH - BORDER - 20)

    def prof(n):
        return frames[n, row].mean(axis=0).astype(np.float64)

    def corr(a, b):
        a, b = a - a.mean(), b - b.mean()
        return float((a * b).sum() / np.sqrt((a * a).sum() * (b * b).sum()))
    hold, wide = prof(int(1.2 * FPS)), prof(2)
    assert _zoomed(frames[k1 - 1]) and corr(prof(k1 - 1), hold) > 0.97
    assert not _zoomed(frames[k1]) and corr(prof(k1), wide) > 0.97
    # The zoom starting on the second cut is in on the cut's first frame.
    assert not _zoomed(frames[k2 - 1])
    assert _zoomed(frames[k2]) and corr(prof(k2), hold) > 0.97


@needs_ffmpeg
def test_rendered_joins_neither_click_nor_drift(rendered):
    audio, tl = rendered["audio"], rendered["tl"]
    frames = rendered["frames"]
    total = renderer.first_frame_at(tl.out_duration, FPS)
    assert len(frames) == total
    assert abs(len(audio) / 48000.0 - total / FPS) < 0.002
    d2 = np.abs(np.diff(audio.astype(np.float64), 2))
    steady = 0.3 * (2 * np.pi * 440 / 48000) ** 2
    for c in rendered["cuts"]:
        j = int(renderer.first_frame_at(c, FPS) / FPS * 48000)
        # A butt join of two 440 Hz phases spikes ~50x the tone's own
        # curvature; the centred crossfade stays within a few times it.
        assert d2[j - 480:j + 480].max() < 4 * steady
    # A/V: the burst at source 3.6 s plays within a frame of its flash.
    lum = frames[:, BORDER:-BORDER, BORDER:-BORDER].mean(axis=(1, 2))
    flash = int(np.argmax(lum > 200)) / FPS
    burst = int(np.argmax(d2 > 0.01)) / 48000.0
    assert abs(burst - flash) < 1.0 / FPS + 0.002, (burst, flash)


@pytest.fixture(scope="module")
def rendered_card():
    """A mastered final with its end card: the programme's sound through
    the loudnorm chain, then the bundled card, at the -t the renderer uses."""
    card = renderer.endcard_path()
    if not card:
        pytest.skip("no end card asset in this build")
    d = tempfile.mkdtemp(prefix="cuthyg_card_")
    src = os.path.join(d, "src.mkv")
    _source(src, 9.0)
    # 5.01 s: the programme ends on frame 151, off loudnorm's 100 ms grid.
    edl = validate_edl({"keep": [[0.33, 2.07], [3.11, 4.54], [5.2, 7.04]]},
                       9.0).model_dump()
    tl = Timeline(edl["keep"])
    outro = 1.0
    g = renderer.build_filtergraph(
        edl, 9.0, True, tl, None, [], {"video": {"fps": FPS}, "words": []},
        preview=False, W=RW, H=RH, fps=FPS, frame_mode="crop", src_fps=FPS,
        focus_origin=0.0, outro_s=outro, card_idx=1, loudness="social")
    t = f"{renderer.program_render_s(tl, FPS) + outro:.3f}"
    vid = os.path.join(d, "v.raw")
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", src,
         *renderer._endcard_input_args(card, outro, FPS),
         "-filter_complex", g,
         "-map", "[vout]", "-fps_mode", "cfr", "-r", f"{FPS:.3f}", "-t", t,
         "-f", "rawvideo", "-pix_fmt", "gray", vid,
         "-map", "[aout]", "-t", t, "-f", "f32le", "-ac", "1", "-ar",
         "48000", os.path.join(d, "a.raw")], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()[-2000:]
    frames = np.fromfile(vid, np.uint8).reshape(-1, RH, RW)
    audio = np.fromfile(os.path.join(d, "a.raw"), np.float32)
    yield {"frames": frames, "audio": audio, "tl": tl, "outro": outro}
    shutil.rmtree(d, ignore_errors=True)


@needs_ffmpeg
def test_the_end_card_follows_the_last_frame_whole(rendered_card):
    """The mastering chain stamped the programme's sound up to 0.1 s past
    its last sample, so the card started late: the programme's last frame
    repeated into the gap and -t cut the card's own last frames."""
    frames, tl = rendered_card["frames"], rendered_card["tl"]
    k = renderer.first_frame_at(tl.out_duration, FPS)          # 151
    n_card = int(round(rendered_card["outro"] * FPS))
    assert len(frames) == k + n_card
    code = frames[:, 85:95, 155:165].mean(axis=(1, 2))
    lines = frames[:, RH // 2, BORDER:-BORDER].astype(np.float64).std(axis=1)
    assert lines[k - 1] > 40 and lines[k - 2] > 40     # programme picture
    assert abs(code[k - 1] - code[k - 2]) > 2          # no repeated frame
    assert frames[k].mean() < 8                        # the card's first
    assert len(rendered_card["audio"]) / 48000.0 == pytest.approx(
        len(frames) / FPS, abs=0.002)
