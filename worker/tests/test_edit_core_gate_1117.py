"""EDIT-CORE gate of 17 Nov 2026: the root fixes for the two requests the branch made worse.

p5717 "Cut the silences and filler words" (a 93 s music video): the picture check called the
video's own flash edits "framing pops" (only the first 12 of 23 rows were measured against the
source), its black-tail reading started a frame late (1.38 s of black from 91.42 on a programme
black from 90.67), and the audio-safe snap moved the editor's end into the black.

p5873 "warm, sun-touched ... creamy neutrals": the grade's colour was never measured for the
editor, and temperature=1.2 was read as 1.2% (+0.012).
"""
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_tools  # noqa: E402
import cut_audio  # noqa: E402
import look_guard  # noqa: E402
import render_qc  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not on PATH")
FPS = 30


def _flash_source(path, cuts):
    """A source whose own edit cuts hard between two pictures ``cuts`` times, 0.4 s apart."""
    inputs, labels = [], []
    for i in range(cuts + 1):
        colour = "0xd02030" if i % 2 else "0x20a0d0"
        inputs += ["-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r={FPS}:d=0.4"]
        labels.append(f"[{i}:v]")
    graph = "".join(labels) + f"concat=n={len(labels)}:v=1:a=0,format=yuv420p[v]"
    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph,
                    "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-g", "6", path],
                   check=True, capture_output=True)


@needs_ffmpeg
def test_every_jump_the_upload_itself_makes_is_the_uploads_own(tmp_path):
    src = str(tmp_path / "src.mp4")
    _flash_source(src, 23)
    plan = {"fps": FPS, "src_wh": [160, 90], "src_of": lambda t: t, "others": []}
    # the render is the upload untouched: every global change sits where the source changes
    rows = [["jump", int(round(0.4 * k * FPS)), None] for k in range(1, 24)]
    owned = render_qc.SourceChanges(plan, src, time.monotonic() + 60)
    mine, theirs = owned.split(rows, lambda r: [r[1]])
    assert mine == [] and len(theirs) == 23
    # a jump where the source holds still is the edit's
    mine, _theirs = render_qc.SourceChanges(plan, src, time.monotonic() + 60).split(
        rows + [["jump", 6, None]], lambda r: [r[1]])
    assert mine == [["jump", 6, None]]


def test_a_black_run_that_opens_a_frame_into_the_window_is_traced_back(monkeypatch):
    prog = 92.88
    window = np.ceil((prog - render_qc.STALL_TAIL_S) * FPS - 1e-6) / FPS
    rows = [["black", round(window + 1.0 / FPS, 3), prog, "picture", "x"]]
    black = np.zeros((4, 4), "uint8")
    lit = np.full((4, 4), 200, "uint8")

    def frames(path, start, end, w, h, deadline, keyframes=False):
        times = np.arange(max(0.0, start), end, 1.0 / FPS)
        return [(float(t), lit if t < 90.67 else black) for t in times]
    monkeypatch.setattr(render_qc, "_frames_with_times", frames)
    t0, t1, exact = render_qc.black_tail("x.mp4", {"fps": FPS, "W": 4, "H": 4, "program_s": prog},
                                         1e12, stall_rows=rows)
    assert exact and t0 == pytest.approx(90.67, abs=1.5 / FPS) and t1 == prog


def test_an_end_never_snaps_past_the_uploads_last_picture():
    # loud music until 90.9, quiet after: the quiet point lies in the black after the picture ends
    def level(t):
        return -12.0 if t < 90.9 else -60.0
    keep = [[0.0, 90.70]]
    free, _m, _c = cut_audio.refine_keep(keep, [], 92.88, [[0.0, 92.88]], level)
    held, _m, _c = cut_audio.refine_keep(keep, [], 92.88, [[0.0, 92.88]], level, content_end=90.76)
    assert free[-1][1] > 90.76 and held[-1][1] <= 90.76
    ctx = NS(duration=92.88, index={"source_screen": {"safe_end_s": 90.757424}})
    assert agent_tools._content_end(ctx) == pytest.approx(90.757424)
    assert agent_tools._content_end(NS(duration=92.88, index={})) is None


@needs_ffmpeg
def test_the_look_write_reads_back_the_colour_it_made(tmp_path):
    raw = str(tmp_path / "grey.png")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "gradients=s=192x108:c0=0x202020:c1=0xf0f0f0:x0=0:y0=0:x1=191:y1=0,format=rgb24",
                    "-frames:v", "1", raw], check=True, capture_output=True)
    pink = "colorbalance=rm=0.15:bm=0.12:rh=0.15:bh=0.12"
    c = look_guard.colour([look_guard.frame_lab(raw)], [look_guard.frame_lab(raw, pink)])
    (_sL, sa, sb), (_gL, ga, gb) = c["whites"]
    assert abs(sa) < 1.5 and abs(sb) < 1.5 and ga > sa + 3
    line = look_guard.colour_line(c)
    assert line.startswith("\nLOOK COLOUR MEASURED") and "magenta/red" in line
    # a custom filter is part of the measured look, and its write is a look write
    assert "add_custom_filter" in look_guard.LOOK_TOOLS
    edl = {"keep": [[0, 10]], "effects": {"custom": [{"id": "cf1", "chain": pink, "start": 0, "end": 5}]}}
    assert pink in look_guard._chain_at(edl, 2.0) and pink not in look_guard._chain_at(edl, 7.0)


def test_a_value_just_past_the_axis_is_its_end_not_a_hundredth():
    saved = {}
    ctx = NS(latest_edl=lambda: {"version": 3, "json": {"keep": [[0, 5]], "effects": {}}},
             write_edl=lambda edl, what: saved.update(edl=edl, what=what) or "EDL v3 -> v4: " + what,
             _call_notes=[])
    agent_tools.set_grade_custom(ctx, temperature=1.2, tint=-6)
    gc = saved["edl"]["effects"]["grade_custom"]
    assert gc["temperature"] == 1.0 and gc["tint"] == -0.06
    assert any("axis end" in n for n in ctx._call_notes)


# ---- follow-up of the 17 Nov p5717 replay (egf-fixes-1117) -------------------------------------

def test_the_pushback_carries_what_the_tools_measured_when_they_changed_nothing():
    import agent_loop
    ctx = NS(turn_tool_outcomes=[], versions_written=[], last_tool_result=None, write_attempts=0)
    agent_loop._record_outer_tool_outcome(ctx, "load_tools", "Capabilities loaded")
    agent_loop._record_outer_tool_outcome(
        ctx, "cut_silences", "NO CHANGE — measured picture and sound: nothing here is dead air. KEPT 5 stretch(es)")
    agent_loop._record_outer_tool_outcome(
        ctx, "remove_filler_words", "No filler words ['um'] were found in the transcript, so nothing was removed.")
    note = agent_loop._edit_without_write_note(ctx)
    assert "nothing here is dead air" in note and "No filler words" in note
    assert "changing nothing is the result" in note and "Do the work now" not in note
    # nothing measured: the note is as it was
    assert agent_loop._edit_without_write_note(NS(turn_tool_outcomes=[])) == agent_loop._EDIT_WITHOUT_WRITE_NUDGE


def test_cutting_footage_on_request_says_what_the_cut_carried(monkeypatch):
    sys.path.insert(0, os.path.dirname(__file__))
    from test_edit_core_signal import _Ctx, _track, _w
    monkeypatch.setattr(agent_tools, "_liveness_track", lambda ctx, lo, hi, measure=True: (ctx._track, None))
    ctx = _Ctx([_w("AJ", 8.8, 9.3), _w("yeah", 24.5, 24.9)], 40.0, _track(40.0, loud=[(0.0, 40.0)]))
    out = agent_tools.cut_silences(ctx, min_silence_s=0.5, padding_s=0.12, allow_nonquiet=True)
    assert out.startswith("EDL v") and "MEASURED: 3 of the 3 cut gap(s)" in out
    assert "were footage, not dead air" in out and "sound plays over the room's floor" in out
    assert "dead-air gap" not in out


def test_restoring_what_the_same_request_cut_is_not_a_regression():
    import audit
    turn = [[0.0, 90.75]]
    after_cut = [[8.72, 9.36], [12.6, 13.2], [24.37, 25.51]]
    assert audit.regression_warnings(after_cut, [[0.0, 90.45]], {}, turn_keep=turn) == []
    # bringing back what the film had before the request is still named
    warned = audit.regression_warnings(after_cut, [[0.0, 92.0]], {}, turn_keep=turn)
    assert warned and "re-includes 90.75-92.00" in warned[0]


def test_a_song_after_the_last_word_is_not_a_dead_tail():
    import edit_review
    from timeline import Timeline
    prog = NS(duration=90.45, tl=Timeline([[0.0, 90.45]], [], None))
    song = {"silences": [], "shots": [{"start": float(t)} for t in range(0, 90, 2)]}
    assert edit_review._tail_plays(song, prog, 25.3)
    still = {"silences": [[25.0, 90.45]], "shots": [{"start": 0.0}]}
    assert not edit_review._tail_plays(still, prog, 25.3)


def test_the_ask_check_reads_the_pauses_cut_silences_measured(monkeypatch):
    # EDIT-CORE gate, 18 Nov 2026 (p5717 R1): cut_silences measured the music video's wordless
    # stretches as footage (NO CHANGE) and the editor said so, but the ask check's cheap read skipped
    # the turn's measured window: "88.6 s of pauses were not measured", the ask went back as not done
    # and the repair pass cut 2.9 s out of the song. The check now reads the same measurement.
    sys.path.insert(0, os.path.dirname(__file__))
    import activity
    from score import asks
    from test_edit_core_signal import _Ctx, _track, _w
    track = _track(93.0, moving=[(0.0, 93.0)], loud=[(0.0, 93.0)])
    calls = []

    def measure(proxy, budget_s=None, start=0.0, length=None, progress=None):
        calls.append((start, length))
        return dict(track)
    monkeypatch.setattr(activity, "measure", measure)
    monkeypatch.setattr(agent_tools, "_activity_track", lambda ctx, cached_only=False: (None, "not measured yet"))
    ctx = _Ctx([_w("AJ", 8.8, 9.3), _w("yeah", 10.2, 10.7), _w("yeah", 24.5, 24.9)], 92.88, track)
    ctx.proxy_path = lambda: "proxy.mp4"
    out = agent_tools.cut_silences(ctx, min_silence_s=0.35, padding_s=0.1)
    assert "NO CHANGE" in out and len(calls) == 1
    text = asks.pauses(ctx, ctx.edl)
    assert "dead air 0.0 s" in text and "not measured" not in text
    assert len(calls) == 1                      # the turn's window, not a second measurement
    # nothing measured yet this turn: the check measures the short take itself
    fresh = _Ctx([_w("AJ", 8.8, 9.3), _w("yeah", 24.5, 24.9)], 92.88, track)
    fresh.proxy_path = lambda: "proxy.mp4"
    text = asks.pauses(fresh, fresh.edl)
    assert "dead air 0.0 s" in text and "not measured" not in text and len(calls) == 2
