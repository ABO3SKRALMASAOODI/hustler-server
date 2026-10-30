"""Q6 (30 Oct 2026): silent clips, montages, screen recordings and explainer
endings at the references' craft.

- A short silent clip is cut into its beats (jump cuts on its clean, strong
  moments), each framed on the subject, with an eased push-in under the
  title and an end beat: a snap in to the hero framing with one flash.
- A highlight montage runs 0.3-0.48 s shots, may take two to four moments of
  a long shot, opens on a strong moment and ENDS on its payoff's motion peak
  with one white flash; its crop sits on the action of each shot.
- A screen recording's camera snaps to each region (no drift) and pushes in
  on the last one; its captions sit on a dark plate in one low band.
- A narrated cut never ends on an empty frame or mid-sentence (u4899 ended
  on "a fitting piece" over the source's grey fade).

Engine accounts only (trial and new subscribers); a grandfathered payer's
First cut is unchanged.
"""
import json
import os
import shutil
import subprocess
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

import agent_tools
import ending_guard
import first_cut
import first_cut_visual as fcv
import montage_cut
import short_clip
import silent_cut


# --------------------------------------------------------------------------
#  Silent clips
# --------------------------------------------------------------------------

def _walk(duration=17.3, n=14):
    """A man walking toward a tripod from a speck, leaving the frame on the
    right at the very end (u4914's shape)."""
    out = []
    for k in range(n):
        t = duration * (k + 0.5) / n
        f = t / duration
        h = 0.08 + 0.6 * f * f
        cx = 0.45 + 0.05 * f
        box = (cx - 0.27 * h, 0.42 - 0.3 * h, cx + 0.27 * h, 0.42 + 0.7 * h)
        out.append((t, tuple(min(1.0, max(0.0, v)) for v in box), True,
                    k in (9, 11, 12)))
    out.append((duration - 0.3, (0.8, 0.3, 1.0, 1.0), False, False))
    return out


def _gym(duration=15.1, n=14):
    """A mirror routine: poses, with the phone arm in front of the lens at
    about 3.3 s and 8-10 s (u4924's shape)."""
    out = []
    for k in range(n):
        t = duration * (k + 0.5) / n
        clean = not (3.0 < t < 3.6 or 7.9 < t < 10.1)
        out.append((t, (0.2, 0.12, 0.7, 0.62), clean, clean and k % 2 == 0))
    out.append((duration - 0.3, (0.2, 0.12, 0.7, 0.62), True, True))
    return out


@pytest.mark.parametrize("looks,duration", [(_walk(), 17.3), (_gym(), 15.1)])
def test_a_silent_clip_is_cut_into_four_to_six_clean_beats_in_order(looks, duration):
    segs = silent_cut.plan(looks, duration)
    assert silent_cut.BEATS_MIN - 1 <= len(segs) <= silent_cut.BEATS_MAX
    flat = [t for s in segs for t in s]
    assert flat == sorted(flat) and segs[0][0] >= 0 and segs[-1][1] <= duration
    for (a0, b0), (a1, b1) in zip(segs, segs[1:]):
        assert a1 - b0 >= silent_cut.GAP_MIN_S - 1e-6     # every jump reads
    total = sum(b - a for a, b in segs)
    assert 5.0 <= total <= silent_cut.PROG_MAX_S + 1.0
    assert total < duration - 1.0                          # it IS cut
    dirty = [lk[0] for lk in looks if not lk[2]]
    for a, b in segs:                                      # nothing blocks a beat
        assert not any(a - 1e-6 <= d <= b + 1e-6 for d in dirty)
    assert segs[-1][1] - segs[-1][0] >= silent_cut.END_MIN_S - 0.25


def test_the_end_beat_is_the_latest_strong_moment_and_he_is_still_in_frame():
    looks = _walk()
    segs = silent_cut.plan(looks, 17.3)
    last_peak = max(lk[0] for lk in looks if lk[3])
    assert segs[-1][0] <= last_peak <= segs[-1][1]
    assert segs[-1][1] <= 17.3 - 0.3 - silent_cut.DIRTY_PAD_S + 1e-6


def test_a_short_clip_or_one_without_clean_frames_stays_whole():
    assert silent_cut.plan(_walk(6.0), 6.0) == []
    blocked = [(t, b, False, False) for t, b, _c, _p in _walk()]
    assert silent_cut.plan(blocked, 17.3) == []
    assert silent_cut.plan([], 17.3) == []


def test_each_beat_is_framed_on_the_subject_and_the_last_snaps_in():
    looks = _walk()
    segs = silent_cut.plan(looks, 17.3)
    prog = sum(b - a for a, b in segs)
    keys, snap, first = silent_cut.keyframes(looks, segs, 2.6)
    ts = [k["t"] for k in keys]
    assert ts == sorted(ts) and ts[0] == 0.0 and abs(ts[-1] - prog) < 0.01
    for k in keys:
        x0, y0, x1, y1 = k["rect"]
        assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0
        assert silent_cut.Z_FLOOR - 1.0 - 1e-6 <= k["strength"] <= 2.6 + 0.1 - 1.0 + 1e-6
    # the first beat eases in (one of the one or two eased push-ins)
    assert keys[1]["strength"] > keys[0]["strength"]
    # the end beat: a snap of at least 0.25x inside 0.1 s, END_HIT_S before the end
    assert abs(snap - (prog - silent_cut.END_HIT_S)) < 0.6
    before = [k for k in keys if k["t"] <= snap][-1]
    after = [k for k in keys if k["t"] > snap][0]
    assert after["t"] - before["t"] <= 0.1
    assert after["strength"] - before["strength"] >= 0.25
    # ... and the picture keeps moving after it (live to the last frame)
    assert keys[-1]["strength"] > after["strength"]
    assert first == keys[0]["rect"]


def test_medium_and_close_alternate_so_each_cut_reads_as_a_new_camera():
    looks = [(float(t), (0.3, 0.3, 0.6, 0.7), True, True)
             for t in np.arange(0.5, 16, 1.1)]
    segs = silent_cut.plan(looks, 16.5)
    assert len(segs) >= 4
    keys, _snap, _f = silent_cut.keyframes(looks, segs, 2.0)
    firsts = [keys[2 + 2 * i]["strength"] for i in range(len(segs) - 2)]
    for a, b in zip(firsts, firsts[1:]):
        assert abs(a - b) >= 0.3


def test_the_models_answer_becomes_looks_and_nonsense_goes():
    sizes = {1: (384, 683), 2: (384, 683), 3: (384, 683)}
    times = {1: 0.6, 2: 1.7, 3: 2.8}
    text = ('{"frames": [{"n": 1, "box": [100, 200, 200, 600], "clean": true, '
            '"peak": true}, {"n": 2, "box": [0, 0, 384, 683], "clean": true}, '
            '{"n": 3, "box": [10, 20, 30, 40], "clean": false, "peak": true}, '
            '{"n": 7, "box": [1, 2, 3, 4]}]}')
    got = silent_cut.parse_looks(text, sizes, times)
    assert [g[0] for g in got] == [0.6, 1.7, 2.8]
    assert got[0][2] and got[0][3] and abs(got[0][1][0] - 100 / 384) < 1e-6
    assert got[1][1] is None and not got[1][2]           # the whole frame: no subject
    assert not got[2][2] and not got[2][3]               # blocked is never a peak
    assert silent_cut.parse_looks("no json", sizes, times) == []


def test_the_camera_writes_one_path_and_one_flash_on_the_snap():
    looks = _gym()
    segs = silent_cut.plan(looks, 15.1)
    calls = []

    def run(ctx, steps, name, args, started):
        calls.append((name, args))
        return "EDL v3 -> v4: ok"
    ctx = NS(index={"video": {"width": 720, "height": 1280}})
    assert silent_cut.camera(ctx, [], 0.0, run, looks, segs)
    assert [c[0] for c in calls] == ["add_zoom_path", "add_stylize"]
    _keys, snap, _f = silent_cut.keyframes(looks, segs, silent_cut.z_max_for(720))
    flash = calls[1][1]
    assert flash["kind"] == "flash" and flash["start"] <= snap <= flash["end"]
    assert flash["end"] - flash["start"] <= 0.12
    # a 720-wide clip is never enlarged past about 3x on a 1080 output
    assert max(k["strength"] for k in calls[0][1]["keyframes"]) <= 1.0 + 0.1 + 1e-6


def test_no_lane_no_look():
    ctx = NS(first_cut_model=None)
    assert silent_cut.look(ctx, 15.0, {"usd": 0.0}) == []


class _ChainCtx:
    def __init__(self, q1, width=1080, height=1920, duration=15.1):
        self.index = {"video": {"duration": duration, "width": width,
                                "height": height},
                      "words": [], "sentences": []}
        self.duration = duration
        self.first_cut_q1 = q1
        self.versions_written = []
        self.web_recordings = []
        self.last_preview = None


def _chain(monkeypatch, ctx):
    names = []

    def execute(c, name, args):
        names.append((name, args))
        return f"EDL v{len(names) + 1} ok"
    monkeypatch.setattr(agent_tools, "execute", execute)
    monkeypatch.setattr(first_cut, "montage_scout",
                        lambda c, w, s, title_only=False, boxes=False: (w, "Mirror *flex*"))
    monkeypatch.setattr(silent_cut, "look", lambda c, d, s: _gym())
    monkeypatch.setattr(short_clip, "whole_looks", lambda c, s: [])
    _s, applied, _p = first_cut.video_chain(ctx, None, time.monotonic(),
                                            {"usd": 0.0})
    return names, applied


def test_an_engine_account_gets_the_beats_title_camera_and_end_beat(monkeypatch):
    names, applied = _chain(monkeypatch, _ChainCtx(q1=True))
    order = [n for n, _a in names]
    assert order.index("keep_segments") < order.index("add_motion_graphic") \
        < order.index("add_zoom_path") < order.index("add_stylize")
    segs = [a for n, a in names if n == "keep_segments"][0]["segments"]
    assert len(segs) >= 3
    assert "cut into its strongest moments" in applied
    assert any("punch-in" in p for p in applied)


def test_a_grandfathered_payer_keeps_the_whole_take(monkeypatch):
    names, applied = _chain(monkeypatch, _ChainCtx(q1=False))
    assert "keep_segments" not in [n for n, _a in names]
    assert "add_stylize" not in [n for n, _a in names]
    assert "cut into its strongest moments" not in applied


# --------------------------------------------------------------------------
#  Montage
# --------------------------------------------------------------------------

def _curve(duration=60.0, fps=4.0, seed=3):
    rng = np.random.default_rng(seed)
    times = np.arange(int(duration * fps), dtype=np.float32) / fps
    diff = (1.0 + rng.random(len(times)) * 3.0).astype(np.float32)
    luma = np.full(len(times), 120.0, np.float32)
    return times, diff, luma


SHOTS = [{"start": float(a), "end": float(b)} for a, b in
         [(0, 2), (2, 5), (5, 7), (7, 11), (11, 14), (14, 16), (16, 30),
          (30, 33), (33, 47), (47, 60)]]


def test_montage_shots_run_at_the_references_rhythm():
    wins = montage_cut.windows(_curve(), SHOTS, 60.0)
    assert wins and len(wins) >= 8
    lens = [b - a for a, b in wins]
    assert all(l <= montage_cut.SHOT_SLOW + 1e-6 for l in lens[1:])
    assert lens[0] <= montage_cut.OPEN_S + 1e-6
    flat = [t for w in wins for t in w]
    assert flat == sorted(flat)
    # a long shot may give more than one moment, never adjacent ones
    by = {}
    for a, b in wins:
        sh = next(k for k, s in enumerate(SHOTS) if s["start"] <= a < s["end"])
        by.setdefault(sh, []).append(a)
    assert max(len(v) for v in by.values()) >= 2
    for k, starts in by.items():
        assert len(starts) <= montage_cut.per_shot_cap(SHOTS[k]["end"] - SHOTS[k]["start"])
        for x, y in zip(starts, starts[1:]):
            assert y - x >= montage_cut.SECOND_GAP_S - 0.5


def test_the_most_moving_moments_get_the_shortest_shots():
    times, diff, luma = _curve()
    diff[(times >= 20) & (times < 23)] = 30.0            # one huge moment
    wins = montage_cut.windows((times, diff, luma), SHOTS, 60.0)
    hit = [w for w in wins if 20 <= (w[0] + w[1]) / 2 < 23.2]
    assert hit and abs((hit[0][1] - hit[0][0]) - montage_cut.SHOT_FAST) < 0.02


def test_the_reel_never_opens_on_a_weak_moment():
    times, diff, luma = _curve()
    diff[times < 2.0] = 0.7                              # a near-static tower
    wins = montage_cut.windows((times, diff, luma), SHOTS, 60.0)
    assert wins[0][0] >= 2.0


def test_black_frozen_and_cut_frames_are_never_moments():
    times, diff, luma = _curve()
    luma[(times >= 47)] = 5.0                            # black tail
    diff[(times >= 33) & (times < 47)] = 0.2             # frozen
    wins = montage_cut.windows((times, diff, luma), SHOTS, 60.0)
    assert not any(a >= 33 for a, _b in wins)


def test_the_payoff_ends_on_its_motion_peak_not_a_hold():
    times, diff, luma = _curve()
    wins = montage_cut.windows((times, diff, luma), SHOTS, 60.0)
    wins = [w for w in wins if w[0] < 36.5] + [[37.6, 38.2]]
    diff[(times > 38.4) & (times < 38.6)] = 50.0         # the splashdown
    diff[(times > 42.4) & (times < 42.6)] = 80.0         # smoke later: out of reach
    out = montage_cut.hit_end((times, diff, luma), wins, SHOTS, 60.0)
    a, b = out[-1]
    assert a <= 38.5 <= b and b - 38.5 <= montage_cut.HIT_AFTER_S + 0.01
    assert b - a <= montage_cut.HIT_BEFORE_S + montage_cut.HIT_AFTER_S + 0.01
    kept = [list(w) for w in wins[:-1]]
    assert all(w in kept for w in out[:-1])             # at most one moment gives way
    assert len(out) >= len(wins) - 1 and out[-2][1] < a


def test_the_montage_ends_on_one_white_flash():
    calls = []

    def run(ctx, steps, name, args, started):
        calls.append((name, args))
        return "ok"
    ctx = NS(latest_edl=lambda: {"json": {"keep": [[0.0, 4.0], [10.0, 13.0]]}})
    montage_cut.end_flash(ctx, [], 0.0, run)
    name, args = calls[0]
    assert name == "add_stylize" and args["kind"] == "flash"
    assert abs(args["end"] - 7.0) < 1e-6 and args["end"] - args["start"] <= 0.11


def test_the_crop_sits_on_the_subject_the_scout_boxed():
    sizes = {1: (256, 144), 2: (256, 144), 3: (256, 144)}
    got = montage_cut.parse_boxes(
        [{"n": 1, "box": [40, 20, 70, 120]}, {"n": 2, "box": [0, 0, 256, 144]},
         {"n": 3, "box": "x"}, {"n": 9, "box": [1, 2, 3, 4]}], sizes)
    assert list(got) == [1] and abs(got[1][0] - 40 / 256) < 1e-6
    ww = 0.316
    assert montage_cut.crop_x(got[1], ww) == 0.2148                # on the rocket
    assert montage_cut.crop_x((0.0, 0.2, 0.05, 0.4), ww) == 0.158  # inside
    assert montage_cut.crop_x(None, ww) == 0.5
    pairs = [([10.0, 10.4], (0.1, 0.1, 0.2, 0.2)), ([20.0, 20.4], (0.7, 0.1, 0.8, 0.2))]
    assert montage_cut._box_for(pairs, 19.9, 20.5) == (0.7, 0.1, 0.8, 0.2)
    assert montage_cut._box_for(pairs, 30.0, 30.4) is None


def test_the_scout_boxes_each_kept_shot_for_the_crop(monkeypatch):
    import claude_lane
    windows = [[float(k), k + 0.4] for k in range(0, 40, 4)]
    monkeypatch.setattr(claude_lane, "available", lambda: True)

    class R:
        stdout = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x90\x01\x00\x03" + b"\x00" * 40
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: R())
    answer = json.dumps({"title": "Launch *day*", "end": 9, "drop": [2],
                         "boxes": [{"n": n, "box": [10 * n, 10, 10 * n + 20, 60]}
                                   for n in range(1, 11)]})
    seen = {}

    def create(model, messages, **k):
        seen["system"] = messages[0]["content"]
        return NS(choices=[NS(message=NS(content=answer))],
                  usage=NS(provider_cost_usd=0.001))
    monkeypatch.setattr(claude_lane, "create", create)
    monkeypatch.setattr(first_cut.llm, "record", lambda *a, **k: None)
    ctx = NS(first_cut_model="m", proxy_path=lambda: "x.mp4")
    out, title = first_cut.montage_scout(ctx, windows, {"usd": 0.0}, boxes=True)
    assert "BOXES" in seen["system"] and "aftermath" in seen["system"]
    assert out == [w for n, w in enumerate(windows, 1) if n <= 9 and n != 2]
    assert out[-1] == windows[8]                       # no 1 s hold added
    pairs = ctx.first_cut_montage_boxes
    assert [p[0] for p in pairs] == out and all(p[1] for p in pairs)
    assert title == "Launch *day*"


def test_a_grandfathered_montage_is_the_c2_montage(monkeypatch):
    seen = []
    monkeypatch.setattr(montage_cut, "windows", lambda *a, **k: seen.append(1))
    ctx = _ChainCtx(q1=False, width=1920, height=1080, duration=60.0)
    monkeypatch.setattr(first_cut, "_motion_curve", lambda c: _curve())
    ctx.index["shots"] = SHOTS
    names = []
    monkeypatch.setattr(agent_tools, "execute",
                        lambda c, n, a: names.append(n) or "EDL v2 ok")
    monkeypatch.setattr(first_cut, "montage_scout",
                        lambda c, w, s, title_only=False, boxes=False: (w, None))
    first_cut.video_chain(ctx, None, time.monotonic(), {"usd": 0.0})
    assert not seen and "add_stylize" not in names
    assert "keep_segments" in names


# --------------------------------------------------------------------------
#  Screen recordings
# --------------------------------------------------------------------------

def test_the_screen_camera_snaps_to_each_region_and_pushes_in_at_the_end():
    t1, _ = fcv.window_for([0.3, 0.1, 0.5, 0.2], 1.6)
    t2, _ = fcv.window_for([0.5, 0.6, 0.7, 0.7], 1.6)
    t3, _ = fcv.window_for([0.1, 0.3, 0.3, 0.4], 1.6)
    keys = fcv.screen_keyframes([(0.5, t1, 2.2), (4.0, t2, 2.2),
                                 (8.0, t3, 2.2)], 14.0, 3.0)
    moves = [(a, b) for a, b in zip(keys, keys[1:]) if a["rect"] != b["rect"]
             and a["strength"] > 0 and b["t"] < 12.0]
    assert moves and all(b["t"] - a["t"] <= fcv.SCREEN_MOVE_S + 1e-6
                         for a, b in moves)
    assert keys[-1]["t"] == 14.0
    # the last region (8 s) is held over 2.6 s: a reading step down its column
    assert keys[-1]["rect"][0] == t3[0] and keys[-1]["rect"][1] > t3[1]
    assert keys[-1]["strength"] - keys[-2]["strength"] >= fcv.SCREEN_END_PUSH - 1e-6
    assert keys[-2]["t"] == round(14.0 - fcv.SCREEN_END_PUSH_S, 2)
    # the push never ends deeper than the region's own framing (the source
    # caps the zoom): it starts wider, from the region's arrival
    assert max(k["strength"] for k in keys) <= 2.2 - 1.0 + 1e-6


def test_the_screen_camera_never_aims_at_the_page_bar():
    idx = {"spatial": {"samples": [{"t": 5.0, "text": [
        [0.27, 0.013, 0.45, 0.044], [0.27, 0.08, 0.9, 0.11],
        [0.3, 0.2, 0.6, 0.23], [0.3, 0.25, 0.6, 0.28]]}]}}
    box = fcv.column_top(idx, 5.0, [0.25, 0.9])
    assert box[1] >= fcv.SCREEN_HEADER_Y - 0.03


def test_screen_captions_sit_on_a_plate_in_one_low_band():
    style = {"preset": "reels", "motion_look": "editorial", "size_scale": 1.45}
    got, n = fcv.caption_style(style, talking=False, screen=True, vertical=False)
    assert got["motion_look"] == "box" and got["anchor_y"] >= 0.85
    assert got["size_scale"] >= 1.45 * 1.25 and n == 3


# --------------------------------------------------------------------------
#  Explainer endings
# --------------------------------------------------------------------------

def _rows_words():
    """u4899's closing sentences (whisper small, source seconds)."""
    text = [("s12", 64.0, 70.92, "The installed rehab is checked by means of a pressure test."),
            ("s13", 71.32, 74.92, "It's connected to the existing pipe network with a fitting piece."),
            ("s14", 75.56, 81.52, "The rehabilitated pipe thus has a new service life of at least 50 years."),
            ("s15", 82.84, 85.44, "Safe, reliable, sustainable.")]
    rows, words = [], []
    for sid, a, b, t in text:
        rows.append({"id": sid, "t0": a, "t1": b, "text": t})
        toks = t.split()
        step = (b - a) / len(toks)
        for k, w in enumerate(toks):
            words.append({"w": w, "t0": round(a + k * step, 3),
                          "t1": round(a + (k + 1) * step - 0.02, 3)})
    return rows, words


def _grey(a, b):
    """The source's empty grey fade runs 73.8-75.5 s."""
    return not (a < 75.5 and b > 73.8)


def test_an_ending_over_the_empty_fade_moves_to_the_next_live_complete_line():
    rows, words = _rows_words()
    keep = [[50.0, 70.98], [71.32, 75.4]]
    new, why = ending_guard.choose(rows, words, keep, 87.9, 40.0, _grey)
    assert why.startswith("forward to s14")
    assert new[-1][1] <= 81.52 + ending_guard.TAIL_S + 1e-6 and new[-1][1] > 81.52
    # the pause after "piece." is cut like the rest
    assert any(abs(b - (74.9 + ending_guard.PAD_S)) < 0.1 for _a, b in new)
    # the new ending's picture is live
    assert _grey(81.52 - ending_guard.PROBE_BEFORE_S, new[-1][1])


def test_a_complete_live_ending_is_left_alone():
    rows, words = _rows_words()
    keep = [[50.0, 70.98]]
    assert ending_guard.choose(rows, words, keep, 87.9, 30.0, _grey)[0] is None


def test_an_ending_cut_mid_sentence_is_completed():
    rows, words = _rows_words()
    keep = [[50.0, 70.98], [75.56, 79.0]]                 # stops inside s14
    new, why = ending_guard.choose(rows, words, keep, 87.9, 30.0, _grey)
    assert why.startswith("forward to s14") and new[-1][1] > 81.52


def test_when_the_next_line_is_too_far_the_ending_walks_back_to_a_live_one():
    rows, words = _rows_words()
    keep = [[10.0, 70.98], [71.32, 75.4]]
    new, why = ending_guard.choose(rows, words, keep, 87.9, 61.5, _grey)
    assert why.startswith("back to s12")
    assert new[-1][1] <= 70.98 + 0.4 and new[-1][1] > 70.92


def test_nothing_reachable_means_no_change_not_a_worse_cut():
    rows, words = _rows_words()
    keep = [[71.32, 75.4]]
    assert ending_guard.choose(rows, words, keep, 87.9, 61.5,
                               lambda a, b: False)[0] is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg")
def test_an_empty_grey_frame_reads_blank_and_a_real_picture_does_not(tmp_path):
    grey = tmp_path / "grey.mp4"
    live = tmp_path / "live.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=0xB4B8BE:s=320x180:d=1.5,vignette", "-pix_fmt",
                    "yuv420p", str(grey)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=320x180:d=1.5", "-pix_fmt", "yuv420p",
                    str(live)], check=True)
    assert ending_guard.live(str(grey), 0.2, 1.2) is False
    assert ending_guard.live(str(live), 0.2, 1.2) is True


def test_the_guard_runs_only_for_engine_accounts_on_narration_without_a_face(monkeypatch):
    seen = []
    monkeypatch.setattr(ending_guard, "guard",
                        lambda *a, **k: seen.append(1))
    for q1 in (True, False):
        ctx = _ChainCtx(q1=q1, width=1920, height=1080, duration=40.0)
        ctx.index["words"] = [{"w": "word.", "t0": 1.0 + k, "t1": 1.5 + k}
                              for k in range(30)]
        ctx.index["sentences"] = [{"id": f"s{k}", "t0": 1.0 + k, "t1": 1.5 + k,
                                   "text": "a whole line here."} for k in range(30)]
        monkeypatch.setattr(agent_tools, "execute", lambda c, n, a: "EDL v2 ok")
        first_cut.video_chain(ctx, None, time.monotonic(), {"usd": 0.0})
        assert bool(seen) == q1
        seen.clear()


def test_a_sentence_the_index_split_in_two_rows_is_one_ending():
    """u4899's index split "...a new service life of at least" / "50 years."
    (a row length cap): the guard read neither as a whole thought and walked
    the ending back 4 s (candidate run q6c1)."""
    rows, words = _rows_words()
    s14 = rows[2]
    rows[2:3] = [{"id": "s14a", "t0": s14["t0"], "t1": 80.5,
                  "text": "The rehabilitated pipe thus has a new service life of at least"},
                 {"id": "s14b", "t0": 80.5, "t1": s14["t1"], "text": "50 years."}]
    rows[-1] = dict(rows[-1], text="Safe, reliable, sustainable PrimusLine")
    keep = [[50.0, 70.98], [71.32, 75.4]]
    new, why = ending_guard.choose(rows, words, keep, 87.9, 40.0, _grey)
    assert why.startswith("forward to s14b") and new[-1][1] > 81.52
    whole = ending_guard.whole_sentences(rows)
    assert [w["id"] for w in whole] == ["s12", "s13", "s14b", "s15"]
    assert whole[2]["t0"] == s14["t0"] and whole[-1]["t1"] == rows[-1]["t1"]


def test_a_short_reel_is_lengthened_inside_its_shots():
    wins = [[float(k * 3) + 0.5, k * 3 + 0.82] for k in range(12)] + [[40.0, 40.6]]
    shots = [{"start": float(k * 3), "end": k * 3 + 2.0} for k in range(14)]
    out = montage_cut.fit_length(wins, shots, 60.0)
    total = sum(b - a for a, b in out)
    assert total > sum(b - a for a, b in wins)
    assert all(b - a <= montage_cut.MAX_SPAN_S + 1e-6 for a, b in out[:-1])
    assert out[-1] == wins[-1]                          # the hit stays
    for (a, b), sh in zip(out[:-1], shots):
        assert sh["start"] < a < b < sh["end"]           # never across a cut
    flat = [t for w in out for t in w]
    assert flat == sorted(flat)
    long = [[0.0, 3.0], [4.0, 7.0], [8.0, 9.0]]
    assert montage_cut.fit_length(long, shots, 60.0) == long


def test_a_long_hold_reads_down_the_column_in_discrete_steps():
    t1, _ = fcv.window_for([0.3, 0.15, 0.5, 0.25], 1.6)
    held = fcv.reading_steps([(1.0, t1, 2.2)], 12.0)
    assert len(held) >= 3
    ts = [h[0] for h in held]
    assert all(b - a >= fcv.SCREEN_STEP_S - 1e-6 for a, b in zip(ts, ts[1:]))
    ys = [h[1][1] for h in held]
    assert ys[1] > ys[0]                                  # down the page
    assert all(0.0 <= h[1][1] < h[1][3] <= 1.0 for h in held)
    assert all(h[2] == 2.2 for h in held)                 # same zoom
    # a region that moves on soon is never stepped
    assert fcv.reading_steps([(1.0, t1, 2.2), (3.0, t1, 2.2)], 5.0)[:2] == \
        [(1.0, t1, 2.2), (3.0, t1, 2.2)]


def test_a_beat_never_shows_much_below_the_subject():
    """The gym clip's judges, every run: "the lower third is sink". A
    window keeps at most GROUND of itself below the subject's base."""
    box = (0.05, 0.47, 0.35, 0.72)
    for z in (1.3, 1.6, 2.1):
        x0, y0, x1, y1 = silent_cut.frame_at(box, z)
        assert y1 - box[3] <= silent_cut.GROUND * (y1 - y0) + 1e-3   # 4-decimal windows
        assert y0 <= box[1]                                 # the head stays in
    # a medium beat is clearly wider than a close one, even when capped
    close = silent_cut.beat_z(box, 1, 2.1)
    medium = silent_cut.beat_z(box, 2, 2.1)
    assert medium <= close * silent_cut.MEDIUM_RATIO + 1e-6


def test_never_more_than_two_moments_of_one_shot_in_a_row():
    times, diff, luma = _curve(90.0)
    shots = [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 60.0},
             {"start": 60.0, "end": 64.0}, {"start": 64.0, "end": 90.0}]
    wins = montage_cut.windows((times, diff, luma), shots, 90.0, target=30.0)
    which = [next(k for k, sh in enumerate(shots) if sh["start"] <= (a + b) / 2 < sh["end"])
             for a, b in wins]
    for k in range(2, len(which)):
        assert not (which[k] == which[k - 1] == which[k - 2])


def test_a_subject_wider_than_the_vertical_crop_is_dropped_but_not_the_payoff():
    wins = [[float(k), k + 0.4] for k in range(0, 12, 2)]
    wide = (0.1, 0.4, 0.9, 0.6)
    pairs = [([2.0, 2.4], wide), ([10.0, 10.4], wide)] + \
        [([float(k), k + 0.4], (0.45, 0.4, 0.55, 0.6)) for k in (0, 4, 6, 8)]
    out = montage_cut.framable(wins, pairs, 0.316)
    assert [2.0, 2.4] not in out and out[-1] == [10.0, 10.4] and len(out) == 5
    # never below four shots
    assert montage_cut.framable(wins[:4], [([float(k), k + 0.4], wide) for k in range(0, 8, 2)],
                                0.316) == wins[:4]
