"""Social mastering by default, the natural opt-out, and dialogue leveling.

Three judges measured the showcase shorts at -29.3/-16.8/-18.7 LUFS against
28 references at -12.6..-14.9: nothing set master.loudness for shorts, the
library sfx played over a quiet lecture, and a two-speaker clip jumped 4.7 LU
at the speaker change. These pin the resolution rule, the leveler's curve and
a real render through it.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools  # noqa: E402
import audio_qc  # noqa: E402
import config  # noqa: E402
import dialogue_level  # noqa: E402
import renderer  # noqa: E402
from schemas import (default_edl, describe_edl, edl_signature,  # noqa: E402
                     master_loudness, validate_edl)
from timeline import Timeline  # noqa: E402


# ---- which renders are mastered --------------------------------------------

@pytest.mark.parametrize("frame,dims,want", [
    ({"ratio": "9:16"}, (1920, 1080), "social"),
    ({"ratio": "4:5"}, (1920, 1080), "social"),
    ({"ratio": "1:1"}, (1920, 1080), "social"),
    ({"ratio": "16:9"}, (1080, 1920), None),
    ({"ratio": "source"}, (1080, 1920), "social"),     # a phone recording
    ({"ratio": "source"}, (1080, 1080), "social"),
    ({"ratio": "source"}, (1920, 1080), None),
    (None, (1920, 1080), None),
    (None, (720, 1280), "social"),
    (None, (None, None), None),                       # unknown shape: off
])
def test_unset_master_resolves_by_output_frame(frame, dims, want):
    edl = {"keep": [[0, 5]], "frame": frame}
    assert master_loudness(edl, *dims) == want


def test_explicit_values_win_over_the_format():
    wide = {"keep": [[0, 5]], "frame": {"ratio": "16:9"},
            "master": {"loudness": "social"}}
    tall = {"keep": [[0, 5]], "frame": {"ratio": "9:16"},
            "master": {"loudness": "natural"}}
    assert master_loudness(wide, 1920, 1080) == "social"
    assert master_loudness(tall, 1920, 1080) is None


def test_canvas_programs_resolve_from_the_canvas():
    tall = {"keep": [], "canvas": {"width": 1080, "height": 1920}}
    wide = {"keep": [], "canvas": {"width": 1920, "height": 1080}}
    assert master_loudness(tall) == "social"
    assert master_loudness(wide, 1080, 1920) is None


def test_natural_validates_and_old_edls_keep_their_signature():
    edl = default_edl(10.0)
    sig = edl_signature(validate_edl(edl, 10.0).model_dump())
    assert '"master"' not in sig            # unset stays absent
    edl["master"] = {"loudness": "natural"}
    out = validate_edl(edl, 10.0).model_dump()
    assert out["master"] == {"loudness": "natural"}
    edl["master"] = {"loudness": None}
    assert validate_edl(edl, 10.0).model_dump()["master"] is None


def test_describe_says_what_will_actually_ship():
    base = default_edl(10.0)
    tall = dict(base, frame={"ratio": "9:16", "mode": "crop"})
    assert "mastered (social loudness, format default)" in describe_edl(tall)
    assert "mastered" not in describe_edl(
        dict(base, frame={"ratio": "16:9", "mode": "crop"}))
    natural = dict(tall, master={"loudness": "natural"})
    assert "unmastered (natural loudness)" in describe_edl(natural)
    assert "mastered (social loudness)" in describe_edl(
        dict(base, master={"loudness": "social"}))


# ---- the graph ---------------------------------------------------------------

def _graph(edl, **kw):
    tl = Timeline(edl["keep"], [])
    return renderer.build_filtergraph(
        edl, 20.0, True, tl, None, [], {"words": []}, False,
        W=1080, H=1920, fps=30.0, src_w=1920, src_h=1080, **kw)


def test_vertical_frames_master_by_default_and_natural_opts_out():
    tall = {"keep": [[0.0, 20.0]], "frame": {"ratio": "9:16"}}
    assert "loudnorm=I=-14:TP=-2.0:LRA=11" in _graph(tall)
    assert "loudnorm" not in _graph(dict(tall, master={"loudness": "natural"}))
    assert "loudnorm" not in _graph({"keep": [[0.0, 20.0]],
                                     "frame": {"ratio": "16:9"}})
    # The canvas base stages force it off: mastering happens exactly once.
    assert "loudnorm" not in _graph(tall, loudness=None)
    # Natural still carries the codec-safe peak ceiling.
    assert "alimiter=limit=0.75" in _graph(
        dict(tall, master={"loudness": "natural"}))


def test_leveling_sits_on_the_dialogue_before_anything_is_mixed():
    edl = {"keep": [[0.0, 20.0]], "frame": {"ratio": "9:16"},
           "volume": [{"start": 4.0, "end": 6.0, "gain_db": -12.0}]}
    sfx = [(1, {"id": "s", "storage_key": "sfx/hit.wav", "at": 3.0,
                "gain_db": -9.0}, None)]
    knots = [(0.2, 6.0), (10.0, 2.0)]
    g = _graph(edl, sfx_inputs=sfx, dialogue_gain=knots)
    lev = g.index("[ac]aformat=sample_fmts=fltp,volume=volume='pow(10,(")
    assert lev < g.index("amix=") and "[alev]" in g
    assert ":eval=frame[alev]" in g
    # The user's volume automation is in the render...
    assert "volume=-12.0dB:enable='between(t,4.00,6.00)'" in g
    # ...but not in what the leveler measures, so it is never undone.
    probe = _graph(edl, sfx_inputs=sfx, dialogue_probe=True)
    assert "enable='between(t,4.00,6.00)'" not in probe
    pruned = renderer._prune_graph_to_audio(probe, target="ac")
    assert "amix" not in pruned and "loudnorm" not in pruned
    # Leveling only ever rides on a mastered mix.
    natural = _graph(dict(edl, master={"loudness": "natural"}),
                     dialogue_gain=knots)
    assert "[alev]" not in natural


# ---- the curve ---------------------------------------------------------------

def _blocks(levels, step=0.1):
    return [(round((i + 1) * step - 0.2, 3), v) for i, v in enumerate(levels)]


def _eval_gain(expr, t):
    py = expr.replace("if(", "_if(")
    return eval(py, {"_if": lambda c, a, b: a if c else b,
                     "lt": lambda a, b: a < b, "gte": lambda a, b: a >= b,
                     "t": t})


def _leveled(levels, knots, a, b, step=0.1):
    """Energy-mean loudness of speech blocks in [a, b) after the gain."""
    expr = dialogue_level.gain_expr(knots)
    out = [v + _eval_gain(expr, t) for t, v in _blocks(levels, step)
           if a <= t < b and v > -50]
    return 10 * np.log10(np.mean([10 ** (x / 10) for x in out]))


def test_two_speakers_meet_in_the_middle_at_the_target():
    # Speaker A 8 s at -22, speaker B 14 s at -17.4: the Elon/Rogan case.
    rng = np.random.default_rng(3)
    levels = list(-22 + rng.normal(0, 1.5, 80)) + \
        list(-17.4 + rng.normal(0, 1.5, 140))
    knots = dialogue_level.gain_knots(_blocks(levels))
    a = _leveled(levels, knots, 0, 8)
    b = _leveled(levels, knots, 8, 22)
    assert abs(a - b) < 1.6, (a, b)
    overall = _leveled(levels, knots, 0, 22)
    assert abs(overall - dialogue_level.TARGET_LUFS) < 1.0


def test_a_quiet_recording_is_brought_up_and_pauses_hold_the_gain():
    # A -30 LUFS lecture with a 2 s pause of room tone in the middle.
    levels = [-30.0] * 50 + [-62.0] * 20 + [-30.0] * 50
    knots = dialogue_level.gain_knots(_blocks(levels))
    expr = dialogue_level.gain_expr(knots)
    gains = [_eval_gain(expr, t) for t, _v in _blocks(levels)]
    assert all(abs(g - 12.0) < 0.3 for g in gains)    # never pumps the pause


def test_the_curve_is_slew_limited_and_clamped():
    levels = [-45.0] * 60 + [-10.0] * 60
    knots = dialogue_level.gain_knots(_blocks(levels))
    for (t0, v0), (t1, v1) in zip(knots, knots[1:]):
        assert abs(v1 - v0) <= dialogue_level.SLEW_DB_S * (t1 - t0) + 0.02
    assert all(dialogue_level.MIN_GAIN_DB <= v <= dialogue_level.MAX_GAIN_DB
               for _t, v in knots)


def test_silence_and_a_wisp_of_sound_are_left_alone():
    assert dialogue_level.gain_knots([]) is None
    assert dialogue_level.gain_knots(_blocks([-120.0] * 100)) is None
    assert dialogue_level.gain_knots(_blocks([-120.0] * 100 + [-20.0] * 5)) \
        is None


def test_long_programs_stay_a_shallow_bounded_expression():
    rng = np.random.default_rng(1)
    levels = list(-20 + rng.normal(0, 4, 36000))      # one hour
    knots = dialogue_level.gain_knots(_blocks(levels))
    assert len(knots) <= dialogue_level.MAX_KNOTS
    expr = dialogue_level.gain_expr(knots)
    depth = max_depth = 0
    for ch in expr:
        depth += ch == "("
        depth -= ch == ")"
        max_depth = max(max_depth, depth)
    assert max_depth < 40
    for t in (0.0, 100.05, 1800.0, 3599.0, 4000.0):
        assert dialogue_level.MIN_GAIN_DB <= _eval_gain(expr, t) \
            <= dialogue_level.MAX_GAIN_DB


def test_probe_log_parses_into_window_centres():
    text = ("frame:0    pts:0       pts_time:0\nlavfi.r128.M=-120.691\n"
            "frame:1    pts:4800    pts_time:0.1\nlavfi.r128.M=-21.5\n"
            "frame:2    pts:9600    pts_time:0.2\nlavfi.r128.M=nan\n")
    assert dialogue_level.parse_probe(text) == [
        (-0.1, -120.0), (0.0, -21.5), (0.1, -120.0)]


# ---- cache freshness ---------------------------------------------------------

def test_stale_unmastered_shorts_are_not_served_as_current():
    tall = {"keep": [[0, 5]], "frame": {"ratio": "9:16"}}
    wide = {"keep": [[0, 5]], "frame": {"ratio": "16:9"}}
    natural = dict(tall, master={"loudness": "natural"})
    assert not renderer.master_current({}, tall)
    assert not renderer.master_current({"master_v": 0}, tall)
    assert renderer.master_current({"master_v": config.MASTER_VERSION}, tall)
    # Explicit social predates leveling too.
    assert not renderer.master_current({}, dict(wide, master={"loudness":
                                                              "social"}))
    assert renderer.master_current({}, wide)
    assert renderer.master_current({}, natural)
    portrait_source = {"keep": [[0, 5]]}
    assert not renderer.master_current({}, portrait_source, 1080, 1920)
    assert renderer.master_current({}, portrait_source, 1920, 1080)


# ---- the tool, the state, the advisories -------------------------------------

class _Ctx:
    def __init__(self, edl, index=None):
        self._edl = edl
        self.index = index or {}
        self.written = None

    def latest_edl(self):
        return {"version": 4, "json": self._edl}

    def write_edl(self, edl, summary):
        self.written = edl
        return f"EDL v5 — {summary}"


def test_turning_mastering_off_is_an_explicit_natural_opt_out():
    tall = {"keep": [[0, 5]], "frame": {"ratio": "9:16"}}
    ctx = _Ctx(dict(tall))
    res = agent_tools.set_master_loudness(ctx, False)
    assert ctx.written["master"] == {"loudness": "natural"}
    assert "natural" in res
    assert master_loudness(ctx.written, 1920, 1080) is None
    res = agent_tools.set_master_loudness(ctx, True)
    assert ctx.written["master"] == {"loudness": "social"}
    assert "-18 LUFS" in res


def test_project_state_reports_the_effective_mastering():
    ctx = _Ctx({}, index={"video": {"width": 720, "height": 1280}})
    assert agent_tools._master_state(ctx, {"keep": [[0, 5]]}) == {
        "loudness": "social", "set_by": "format default"}
    assert agent_tools._master_state(
        ctx, {"keep": [[0, 5]], "master": {"loudness": "natural"}}) == {
        "loudness": "natural", "set_by": "edl"}


def test_audio_qc_never_prescribes_mastering_to_a_mastered_mix(monkeypatch):
    monkeypatch.setattr(
        audio_qc, "_run_ffmpeg",
        lambda _p: '{"input_i":"-24.0","input_tp":"-9.0","input_lra":"3"}')
    plain = audio_qc.measure("x.mp4", 10)["findings"][0]
    assert "set_master_loudness fixes it" in plain
    mastered = audio_qc.measure("x.mp4", 10, master="social")["findings"][0]
    assert "set_master_loudness" not in mastered and "muted" in mastered
    natural = audio_qc.measure("x.mp4", 10, master="natural")["findings"][0]
    assert "by request" in natural


# ---- a real render -----------------------------------------------------------

def _ff(args):
    return subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                          check=True, capture_output=True).stdout


def _speechlike(path, quiet_db, loud_db, seconds=6.0):
    """Portrait clip: a voiced tone in syllable-rate bursts, a quiet speaker
    for the first half and a louder one for the second."""
    half = seconds / 2
    voice = ("aevalsrc='0.5*(0.6*sin(2*PI*180*t)+0.4*sin(2*PI*370*t))"
             f"*(0.55-0.45*cos(2*PI*2.5*t))':s=48000:d={seconds}")
    _ff(["-f", "lavfi", "-i", f"color=c=gray:s=180x320:r=25:d={seconds}",
         "-f", "lavfi", "-i", voice,
         "-filter_complex",
         "[1:a]asplit[q][l];"
         f"[q]atrim=0:{half},volume={quiet_db}dB[a0];"
         f"[l]atrim={half}:{seconds},asetpts=PTS-STARTPTS,"
         f"volume={loud_db}dB[a1];[a0][a1]concat=n=2:v=0:a=1[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-threads", "1",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)])


def _loudness(path, a=None, b=None):
    pre = ["-ss", str(a), "-t", str(b - a)] if a is not None else []
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *pre, "-i",
                          str(path), "-map", "0:a:0", "-af",
                          "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    s = err[err.rfind("Summary:"):]
    i = float(re.search(r"I:\s+(-?[\d.]+)", s).group(1))
    tp = re.search(r"Peak:\s+(-?[\d.]+|-inf)", s)
    return i, (float(tp.group(1)) if tp else None)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_a_portrait_render_is_leveled_and_mastered(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTRO_ON_PREVIEW", False, raising=False)
    src = tmp_path / "two_mics.mp4"
    _speechlike(src, -16.0, -8.0)             # about -33 and -25 LUFS
    index = {"video": {"duration": 6.0, "width": 180, "height": 320,
                      "fps": 25}, "words": []}
    edl = default_edl(6.0)
    edl["volume"] = [{"start": 4.0, "end": 5.0, "gain_db": -12.0}]
    out = tmp_path / "mastered.m4a"
    renderer.render_edl(edl, index, str(src), str(out), str(tmp_path),
                        preview=True, audio_only=True)
    i, tp = _loudness(out)
    assert abs(i - (-14.0)) <= 1.0, i
    assert tp is not None and tp <= -1.5, tp
    first, _ = _loudness(out, 0.5, 2.8)
    second, _ = _loudness(out, 3.2, 3.9)
    assert abs(first - second) < 3.0, (first, second)     # was 8 LU apart
    # The user's -12 dB second stays down: the leveler measured the bed
    # without it, so it does not ride it back up.
    ducked, _ = _loudness(out, 4.2, 4.9)
    assert second - ducked > 9.0, (second, ducked)

    natural = dict(edl, master={"loudness": "natural"})
    raw = tmp_path / "natural.m4a"
    renderer.render_edl(natural, index, str(src), str(raw), str(tmp_path),
                        preview=True, audio_only=True)
    q, _ = _loudness(raw, 0.5, 2.8)
    loud, _ = _loudness(raw, 3.2, 3.9)
    assert loud - q > 6.0                     # untouched: the mics still differ
    assert _loudness(raw)[0] < -16.0
