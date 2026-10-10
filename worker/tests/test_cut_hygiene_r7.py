"""Audio-safe cuts, the payoff hold, a clean first frame and no orphan
frames (judges, round 7).

* keep boundaries cut into words — Elon's first second clipped 'ever' and
  'was', a hard chop at Jobs 9.25 (the 's' of 'fonts,' at -13 dBFS), and
  Thiel's edit log carried eleven in-word warnings nobody acted on: every
  NEW keep edge a tool writes now lands audio-safe (cut_audio) and the
  result reports what moved;
* the punchline got ~0.5 s and the laugh ~0.55 s before the end card: the
  verifier asks for 0.8 s and add_freeze_frame(audio_mode='hold') holds
  the composed frame over the source's own room tone;
* Elon opened on a blink: the picture QC reads the opening frames' eyes
  and offers the nearest clean start (never moving the cut itself), and a
  programme that opens mid-sound is named;
* a crop switch one frame before the source's camera cut flashed Rogan
  re-framed for a frame (21.867): the QC finds 1-2 frame orphan shots
  whatever the plan calls them, and reframes off a cut; card windows a
  frame or two off a cut snap onto it.

Run:  python -m pytest tests/test_cut_hygiene_r7.py -q     (from worker/)
"""

import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_tools                                            # noqa: E402
import cut_audio                                              # noqa: E402
import render_qc                                              # noqa: E402
import renderer                                               # noqa: E402
from schemas import default_edl, validate_edl                 # noqa: E402

FFMPEG = pytest.mark.skipif(not shutil.which("ffmpeg"),
                            reason="ffmpeg required")


def _level(spans, quiet=-62.0):
    """level(t): dBFS from [(a, b, dB)] spans (first match), else quiet."""
    def level(t):
        for a, b, db in spans:
            if a <= t < b:
                return db
        return quiet
    return level


WORDS = [{"w": "somebody", "t0": 0.60, "t1": 1.00},
         {"w": "was", "t0": 1.00, "t1": 1.30},
         {"w": "like,", "t0": 1.60, "t1": 1.90},
         {"w": "ever", "t0": 2.40, "t1": 2.70}]


# ── 1. which edges are cuts ──────────────────────────────────────────────

def test_only_the_new_real_cut_edges_are_placed():
    keep = [[0.0, 1.25], [1.25, 1.9], [2.4, 5.0]]
    edges = cut_audio.cut_edges(keep, duration=5.0)
    # the source's own start and end, and the touching split, are no cuts
    assert edges == [(1, "end", 1.9), (2, "start", 2.4)]
    # an edge the previous keep already had is left as it was written
    assert cut_audio.cut_edges(keep, 5.0, prev_keep=[[0.0, 1.9],
                                                      [2.4, 5.0]]) == []


# ── 2. placement on the sound ───────────────────────────────────────────

def test_a_mid_word_end_moves_out_to_the_quiet_after_the_word():
    # 'was' is loud to its release at 1.33; quiet after
    lv = _level([(0.6, 1.33, -15.0), (1.6, 1.9, -14.0)])
    got = cut_audio.place_edge("end", 1.25, WORDS, lv)
    assert got["why"] == "word" and got["word"] == "was"
    assert 1.33 <= got["t"] <= 1.38 and got["db"] < -50
    # a start inside 'ever' moves before its onset, never into 'like,'
    lv = _level([(1.6, 1.9, -14.0), (2.36, 2.7, -15.0)])
    got = cut_audio.place_edge("start", 2.45, WORDS, lv)
    assert got["why"] == "word" and 2.28 <= got["t"] < 2.36


def test_a_quiet_in_word_edge_is_the_transcript_being_off():
    # the transcript says 'ever' starts at 2.40, the sound starts at 2.48
    lv = _level([(2.48, 2.7, -15.0)])
    got = cut_audio.place_edge("start", 2.43, WORDS, lv)
    assert got["why"] is None and got.get("quiet_already")


def test_an_edge_in_a_words_release_reaches_past_whispers_end():
    # Jobs 'fonts,': Whisper ends it at 1.30 but the 's' sounds to 1.45
    lv = _level([(0.6, 1.45, -16.0), (1.6, 1.9, -14.0)])
    got = cut_audio.place_edge("end", 1.32, WORDS, lv)
    assert got["why"] == "quiet" and 1.45 <= got["t"] <= 1.5
    assert not got["loud"]


def test_a_cut_never_reaches_into_the_word_on_the_removed_side():
    # 'was' runs straight into 'like,' (no pause): nothing quiet within
    # reach short of the far word — the edge stays and is reported loud
    words = [{"w": "was", "t0": 1.0, "t1": 1.30},
             {"w": "like,", "t0": 1.31, "t1": 1.60}]
    lv = _level([(0.9, 1.6, -18.0)])
    got = cut_audio.place_edge("end", 1.28, words, lv)
    assert got["t"] == 1.28 and got["loud"]
    new, moves, checked = cut_audio.refine_keep([[0.0, 1.28], [2.0, 4.0]],
                                                words, 5.0, None, lv)
    lines = cut_audio.report(moves, checked)
    assert any("joins running speech" in l and "end 1.28" in l for l in lines)
    # (constant sound with no floor to compare is judged by its level)


def test_an_end_is_quiet_a_frame_either_side_of_where_it_renders():
    # On the block clock a span's sound runs a whole number of frames, so
    # its end really cuts up to a frame either side of the keep's end: the
    # placement reads the level there too.
    lv = _level([(0.6, 1.45, -16.0), (1.6, 1.9, -14.0)])
    new, _m, _c = cut_audio.refine_keep([[0.0, 1.47], [2.4, 5.0]], WORDS,
                                        5.0, None, lv)
    assert new[0][1] == 1.47                       # quiet right there...
    new, moves, _c = cut_audio.refine_keep([[0.0, 1.47], [2.4, 5.0]], WORDS,
                                           5.0, None, lv, end_slack=1 / 30)
    assert new[0][1] >= 1.49                       # ...but not a frame early
    assert moves[0]["side"] == "end"
    # a start is exact on the block clock: no slack
    lv2 = _level([(2.36, 2.7, -15.0), (0.6, 1.3, -15.0)])
    new, _m, _c = cut_audio.refine_keep([[0.0, 1.3], [2.34, 5.0]], WORDS,
                                        5.0, None, lv2, end_slack=1 / 30)
    assert new[1][0] == 2.34


def test_without_the_sound_an_in_word_edge_moves_to_the_words_edge():
    new, moves, checked = cut_audio.refine_keep(
        [[0.0, 1.25], [2.45, 5.0]], WORDS, 5.0)
    assert checked == 0
    # end: past 'was' (+ a release pad, short of 'like,'); start: before
    # 'ever' (a lead-in pad)
    assert new == [[0.0, 1.38], [2.36, 5.0]]
    lines = cut_audio.report(moves, checked)
    assert lines and "no source sound to measure" in lines[0]
    # an edge between words stays where it was written
    assert cut_audio.refine_keep([[0.0, 1.45]], WORDS, 5.0)[0] == [[0.0, 1.45]]


# ── 3. measured on a real file ───────────────────────────────────────────

def _speech_wav(path, dur=5.0):
    """A tone where WORDS are spoken (and a fricative-like tail on 'was'
    to 1.45), near-silence between."""
    expr = ("if(between(t,0.6,1.45)+between(t,1.6,1.9)+between(t,2.4,2.7)"
            "+between(t,3.0,4.6),0.9,0.001)")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"sine=f=220:d={dur}:sample_rate=48000",
                    "-af", f"volume='{expr}':eval=frame", "-ar", "48000",
                    path], check=True)


@FFMPEG
def test_the_sound_is_read_by_range_and_the_quiet_point_found(tmp_path):
    wav = str(tmp_path / "s.wav")
    _speech_wav(wav)
    level = cut_audio.source_levels(wav, [1.25, 2.45], 5.0)
    assert level is not None
    assert level(1.1) > -25 and level(1.52) < -45
    new, moves, checked = cut_audio.refine_keep(
        [[0.0, 1.25], [2.45, 5.0]], WORDS, 5.0, None, level)
    assert checked == 2
    assert 1.45 <= new[0][1] <= 1.52            # past the release
    assert 2.3 <= new[1][0] <= 2.4              # before the onset


class _Ctx:
    def __init__(self, sound=None, words=WORDS, dur=5.0, keep=None):
        self.project_id = 1
        self.duration = dur
        self.has_main_video = True
        self.workdir = None
        self.index = {"video": {"duration": dur, "width": 1920,
                                "height": 1080, "fps": 30.0},
                      "shots": [], "words": list(words), "sentences": [],
                      "silences": []}
        self.written = []
        edl = default_edl(dur)
        if keep:
            edl["keep"] = keep
        self._edl = validate_edl(edl, dur).model_dump()
        self._sound = sound

    def cut_sound_source(self):
        return self._sound

    def clamp(self, t):
        return round(min(max(float(t), 0.0), self.duration), 2)

    def latest_edl(self):
        return {"version": len(self.written) + 1, "json": self._edl}

    def write_edl(self, edl, desc):
        self._edl = validate_edl(dict(edl), self.duration).model_dump()
        self.written.append(desc)
        return f"EDL v{len(self.written)}: {desc}"


@FFMPEG
def test_every_keep_tool_writes_audio_safe_edges_and_says_so(tmp_path):
    wav = str(tmp_path / "s.wav")
    _speech_wav(wav)
    ctx = _Ctx(sound=wav)
    res = agent_tools.keep_segments(ctx, [[0.0, 1.25], [2.45, 5.0]])
    keep = ctx._edl["keep"]
    assert 1.45 <= keep[0][1] <= 1.52 and 2.3 <= keep[1][0] <= 2.4
    assert "AUDIO-SAFE CUTS: moved 2 keep edges" in res
    assert "lands inside the word" not in res
    # cut_range places only the edges it makes: the old ones stay put
    before = [list(k) for k in keep]
    res = agent_tools.cut_range(ctx, 3.5, 3.7)          # inside the tone
    keep = ctx._edl["keep"]
    assert keep[0] == before[0] and keep[1][0] == before[1][0]
    assert "AUDIO-SAFE CUTS" in res and "joins running speech" in res
    # snap_to_words=false writes the exact times (a stutter cut)
    ctx2 = _Ctx(sound=wav)
    res = agent_tools.keep_segments(ctx2, [[0.0, 1.25], [2.45, 5.0]],
                                    snap_to_words=False)
    assert ctx2._edl["keep"] == [[0.0, 1.25], [2.45, 5.0]]
    assert "AUDIO-SAFE" not in res and "lands inside the word" in res


def test_a_context_without_sound_still_never_cuts_a_word():
    ctx = _Ctx(sound=None)
    res = agent_tools.keep_segments(ctx, [[0.0, 1.25], [2.45, 5.0]])
    assert ctx._edl["keep"] == [[0.0, 1.38], [2.36, 5.0]]
    assert "no source sound to measure" in res
    # the batch path writes what it was given and only advises
    advice = agent_tools._batch_framing_advisories(
        ctx, {"keep": [[0.0, 5.0]]}, {"keep": [[0.0, 1.25], [2.45, 5.0]]})
    assert any("keep_segments would move" in a for a in advice)


def test_a_story_seed_is_the_same_cut_within_the_audio_safe_reach():
    import shorts
    assert shorts._same_story_cut([[10.02, 29.4]], ([[10.0, 29.33]],), 30.0)
    # the placement's tail reach and the camera-cut snap after it compound
    # (0.25 s into a release, then pulled 6 frames off a cut): still the
    # seed — refusing it failed the whole short
    assert shorts._same_story_cut([[10.0, 29.78]], ([[10.0, 29.33]],), 30.0)
    assert not shorts._same_story_cut([[10.0, 30.5]], ([[10.0, 29.33]],),
                                      30.0)
    assert not shorts._same_story_cut([[10.0, 20.0], [21.0, 29.3]],
                                      ([[10.0, 29.33]],), 30.0)


def test_the_sound_read_keeps_to_its_budget_in_total(monkeypatch):
    # a keep write never stalls on a slow remote read: the budget bounds
    # the whole read (not each ffmpeg call), and what it could not read
    # falls back to the transcript's word edges
    import types
    clock, calls = [0.0], []

    def stalled(cmd, capture_output=True, timeout=None, check=False):
        calls.append(timeout)
        clock[0] += timeout                     # a read that hangs to it
    monkeypatch.setattr(cut_audio, "time",
                        types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(cut_audio.subprocess, "run", stalled)
    windows = [(float(k), k + 0.5) for k in range(cut_audio.MAX_INPUTS + 5)]
    assert cut_audio.fetch("https://example.invalid/a.wav", windows,
                           timeout=10.0) == {}
    assert calls == [10.0]                      # the second chunk never ran


# ── 4. the payoff hold ───────────────────────────────────────────────────

def _hold_ctx(monkeypatch, tmp_path):
    words = [{"w": f"w{i}", "t0": 10.0 + i * 0.3, "t1": 10.28 + i * 0.3}
             for i in range(30)]                        # to 18.98
    words += [{"w": "Next", "t0": 21.0, "t1": 21.3}]    # a 2 s pause first
    ctx = _Ctx(words=words, dur=60.0, keep=[[10.0, 19.2]])
    ctx.workdir = str(tmp_path)
    monkeypatch.setattr(agent_tools, "_freeze_frame_asset",
                        lambda c, t, b=0.0, d=0.0: (f"local:{t:.2f}.png", None))
    edl = dict(ctx._edl)
    edl["motion"] = [{"id": "payoff", "template": "word_slam",
                      "start": 7.0, "end": 9.2,
                      "params": {"text": "140 / characters"}}]
    ctx._edl = validate_edl(edl, 60.0).model_dump()
    return ctx


def test_a_hold_holds_the_composed_last_frame_over_room_tone(monkeypatch,
                                                            tmp_path):
    ctx = _hold_ctx(monkeypatch, tmp_path)
    res = agent_tools.add_freeze_frame(ctx, 9.2, audio_mode="hold",
                                       darken=0.3, motion="pan_left")
    assert res.startswith("EDL v"), res
    ins = ctx._edl["inserts"]
    assert len(ins) == 1 and ins[0]["kind"] == "image"
    assert ins[0]["at_output_s"] == 9.2 and ins[0]["duration_s"] == 1.0
    assert ins[0]["motion"] is None
    hold = ins[0]["hold"]
    a, b = hold["room_tone"]
    # lifted from the pause before 'Next', clear of both words
    assert 19.0 < a < b <= 20.92 and b - a == pytest.approx(1.0, abs=1e-6)
    assert hold["dim"] == 0.3
    # the payoff graphic that ended with the programme holds over it
    assert ctx._edl["motion"][0]["end"] == pytest.approx(10.2)
    assert "HOLD:" in res and "room tone" in res and "motion" in res
    # the freeze is the frame the viewer sees last (half a frame before
    # the end), not a frame from earlier in the shot
    assert "source 19.18s" in res


def test_a_second_hold_retimes_the_first(monkeypatch, tmp_path):
    ctx = _hold_ctx(monkeypatch, tmp_path)
    before = [dict(m) for m in ctx._edl["motion"]]
    agent_tools.add_freeze_frame(ctx, 9.2, audio_mode="hold")
    res = agent_tools.add_freeze_frame(ctx, 10.2, audio_mode="hold",
                                       duration_s=1.4)
    assert res.startswith("EDL v"), res
    ins = ctx._edl["inserts"]
    assert len(ins) == 1 and ins[0]["duration_s"] == 1.4
    # the payoff graphic follows the hold to the new end
    assert ctx._edl["motion"][0]["end"] == pytest.approx(10.6)
    assert before[0]["end"] == 9.2


def test_a_hold_keeps_every_finish_that_ran_to_the_end():
    edl = {"motion": [{"id": "m", "start": 7.0, "end": 9.2}],
           "texts": [{"id": "t", "start": 8.0, "end": 9.2},
                     {"id": "tb", "start": 8.0, "end": 9.2,
                      "anchor_insert": "ins9"}],
           "vectors": [{"id": "v", "start": 8.5, "end": 9.2}],
           "effects": {"zooms": [{"id": "z", "start": 8.0, "end": 9.18}],
                       "stylize": [{"id": "g", "kind": "grain",
                                    "start": 2.0, "end": 9.2},
                                   {"id": "f", "kind": "flash",
                                    "start": 9.0, "end": 9.2},
                                   {"id": "w", "kind": "vignette",
                                    "start": None, "end": None}],
                       "custom": [{"id": "c", "start": 5.0, "end": 9.21}]}}
    moved = agent_tools._extend_to_end(edl, 9.2, 10.2)
    assert sorted(moved) == ["c", "g", "m", "t", "v", "z"]
    assert edl["vectors"][0]["end"] == 10.2
    assert edl["effects"]["stylize"][0]["end"] == 10.2
    # a flash is a moment, not a finish; a whole-programme look needs none
    assert edl["effects"]["stylize"][1]["end"] == 9.2
    assert edl["effects"]["stylize"][2]["end"] is None
    assert edl["texts"][1]["end"] == 9.2


def test_no_transition_lands_on_the_join_into_a_hold():
    from timeline import transition_junctions
    hold = {"id": "ins1", "asset_key": "k.png", "kind": "image",
            "at_output_s": 9.0, "duration_s": 1.0, "hold": {}}
    edl = {"keep": [[0.0, 5.0], [8.0, 12.0]], "inserts": [hold],
           "effects": {"transition": {"style": "dip_black",
                                      "duration_s": 0.3,
                                      "scope": "every_cut"}}}
    # blocks: seg, seg, hold — the join into the hold is the held frame
    # itself, never a cut
    assert transition_junctions(edl, {}) == {0}
    still = dict(hold)
    still.pop("hold")
    assert transition_junctions(dict(edl, inserts=[still]), {}) == {0, 1}
    edl["effects"]["transition"]["scope"] = "scene"
    assert 1 not in transition_junctions(
        edl, {"shots": [{"id": 0, "start": 0.0, "end": 100.0}]})


def test_a_stitched_preview_never_reuses_a_hold_of_other_footage():
    import stitch
    from timeline import Timeline

    def atoms(end):
        edl = {"keep": [[0.0, end]], "inserts": [
            {"id": "ins1", "asset_key": "k.png", "kind": "image",
             "at_output_s": end, "duration_s": 1.0,
             "hold": {"room_tone": [20.0, 21.0]}}]}
        tl = Timeline(edl["keep"], edl["inserts"], [])
        return [a for a in stitch.timeline_atoms(edl, tl) if a[2][0] == "ins"]
    # restore_range on the last keep: the hold now stops a later frame, so
    # its pixels cannot be copied from the previous preview
    assert atoms(5.0)[0][2] != atoms(5.3)[0][2]
    assert atoms(5.0)[0][2] == atoms(5.0)[0][2]


def test_a_hold_after_a_short_reaction_gives_it_its_second():
    import edit_review
    words = [{"w": f"w{i}", "t0": 0.2 + 0.3 * i, "t1": 0.48 + 0.3 * i}
             for i in range(100)]
    edl = {"keep": [[0.0, 30.5], [33.0, 33.6]], "inserts": [], "speed": [],
           "texts": [], "motion": [], "sfx": [], "music": [],
           "frame": {"ratio": "9:16", "mode": "crop", "focus_x": 0.5},
           "effects": {"grade": "warm", "zooms": []}}
    codes = [n["code"] for n in edit_review.review(edl, {"words": words})]
    assert "reaction_button_short" in codes
    # the note's own fix: hold the reaction's last frame
    edl["inserts"] = [{"id": "ins1", "asset_key": "k.png", "kind": "image",
                       "at_output_s": 31.1, "duration_s": 0.6,
                       "hold": {"room_tone": [31.0, 32.0]}}]
    codes = [n["code"] for n in edit_review.review(edl, {"words": words})]
    assert "reaction_button_short" not in codes


def test_a_hold_mid_programme_sits_on_a_cut(monkeypatch, tmp_path):
    ctx = _hold_ctx(monkeypatch, tmp_path)
    res = agent_tools.add_freeze_frame(ctx, 4.0, audio_mode="hold")
    assert res.startswith("REJECTED") and "sits on a cut" in res
    assert agent_tools.add_freeze_frame(ctx, 9.2, audio_mode="bogus") \
        .startswith("REJECTED")


def test_hold_filter_chains():
    assert renderer.hold_room_tone({"kind": "image", "hold": {
        "room_tone": [3.0, 3.4]}}) == (3.0, 3.4)
    assert renderer.hold_room_tone({"kind": "video", "hold": {
        "room_tone": [3.0, 3.4]}}) is None
    assert renderer.hold_room_tone({"kind": "image"}) is None
    ch = renderer.hold_audio_chain((3.0, 3.4), 1.0)
    assert "atrim=start=3.000:end=3.400" in ch and "aloop=loop=-1" in ch
    assert "aloop" not in renderer.hold_audio_chain((3.0, 4.2), 1.0)
    parts = renderer.hold_video_parts("in", "out", 40, 30, 0.0)
    assert parts == ["[in]trim=start_frame=39,setpts=PTS-STARTPTS,"
                     "tpad=stop_mode=clone:stop=30,trim=end_frame=30,"
                     "setpts=PTS-STARTPTS,setsar=1,format=yuv420p[out]"]
    dimmed = renderer.hold_video_parts("in", "out", 40, 30, 0.4, tag="h1")
    assert len(dimmed) == 3 and "lutyuv" in dimmed[1] and "blend" in dimmed[2]


def test_a_hold_insert_validates_and_old_inserts_are_untouched():
    edl = default_edl(30.0)
    edl["keep"] = [[0.0, 10.0]]
    edl["inserts"] = [{"id": "ins1", "asset_key": "k.png", "kind": "image",
                       "at_output_s": 10.0, "duration_s": 1.0,
                       "hold": {"room_tone": [12, 13.5], "dim": 2}}]
    got = validate_edl(edl, 30.0).model_dump()["inserts"][0]["hold"]
    assert got == {"room_tone": [12.0, 13.5], "dim": 0.85}
    edl["inserts"][0]["hold"] = {"tone": [1, 2]}
    with pytest.raises(Exception):
        validate_edl(edl, 30.0)
    edl["inserts"][0].pop("hold")
    assert validate_edl(edl, 30.0).model_dump()["inserts"][0]["hold"] is None


@FFMPEG
def test_the_render_holds_the_last_frame_with_room_tone(tmp_path):
    src = str(tmp_path / "src.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "testsrc2=s=320x180:r=30:d=6", "-f", "lavfi", "-i",
         "sine=f=300:d=6:sample_rate=48000", "-f", "lavfi", "-i",
         "anoisesrc=a=0.004:d=6:r=48000", "-filter_complex",
         "[1:a]volume='if(lt(mod(t,1),0.6),0.5,0)':eval=frame[s];"
         "[s][2:a]amix=inputs=2:normalize=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-pix_fmt",
         "yuv420p", "-c:a", "aac", src], check=True)
    png = str(tmp_path / "still.png")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=320x180", "-frames:v", "1", png],
                   check=True)
    edl = default_edl(6.0)
    edl["keep"] = [[0.0, 1.5], [2.0, 2.55]]
    edl["inserts"] = [{"id": "ins1", "asset_key": "k/still.png",
                       "kind": "image", "at_output_s": 2.05,
                       "duration_s": 1.0, "fit": "crop",
                       "hold": {"room_tone": [2.65, 2.95]}}]
    idx = {"video": {"duration": 6.0, "width": 320, "height": 180,
                     "fps": 30.0}, "words": [], "sentences": [],
           "silences": []}
    out = str(tmp_path / "out.mp4")
    dur = renderer.render_edl(edl, idx, src, out, str(tmp_path), preview=True,
                              suppress_outro=True,
                              asset_locals={"k/still.png": png})
    assert dur == pytest.approx(3.05, abs=0.05)

    def gray(t):
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i",
                            out, "-frames:v", "1", "-vf",
                            "scale=64:36,format=gray", "-f", "rawvideo", "-"],
                           capture_output=True)
        return np.frombuffer(r.stdout, np.uint8).astype(np.float32)
    last, held, later = gray(2.02), gray(2.4), gray(2.95)
    # the held frame is the programme's last frame (testsrc moves every
    # frame, and the red fallback still is nowhere)
    assert np.abs(last - held).mean() < 3.0
    assert np.abs(held - later).mean() < 1.0
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", out, "-ac", "1",
                        "-ar", "16000", "-f", "f32le", "-"],
                       capture_output=True)
    x = np.frombuffer(r.stdout, np.float32)

    def db(a, b):
        seg = x[int(a * 16000):int(b * 16000)]
        return 20 * math.log10(float(np.sqrt(np.mean(seg ** 2))) + 1e-9)
    # room tone under the hold: the source's own noise floor, not silence
    assert -70 < db(2.3, 2.9) < -40
    # the kept tone fades into it (0.15 s), it is not chopped
    assert db(1.86, 1.9) > db(2.0, 2.04) + 6


# ── 5. the first frame of the hook ───────────────────────────────────────

def test_closed_eyes_in_the_opening_are_a_blink_on_a_measured_face():
    box = [.2, .2, .7, .6]
    # Elon: open 3 frames, closed 8 (0.10-0.37 s), open after
    counts = [(2, box)] * 3 + [(1, box)] * 5 + [(0, box)] * 3 + \
        [(2, box)] * 34
    assert render_qc.closed_eyes(counts, 29.97) == (3, 8, 11)
    # frame 0 itself closed for two frames is enough
    assert render_qc.closed_eyes([(0, box)] * 2 + [(2, box)] * 40,
                                 30.0)[:2] == (0, 2)
    # a one-frame detector miss is not a blink
    assert render_qc.closed_eyes([(2, box)] * 5 + [(1, box)] + [(2, box)] * 40,
                                 30.0) is None
    # a blink after the opening the feed shows first is not the hook's
    assert render_qc.closed_eyes([(2, box)] * 20 + [(0, box)] * 6
                                 + [(2, box)] * 20, 30.0) is None
    # a face the strict cascade cannot read (a profile, glasses) is never
    # judged: too few frames show two eyes
    assert render_qc.closed_eyes([(1, box)] * 45, 30.0) is None
    assert render_qc.closed_eyes([(None, None)] * 45, 30.0) is None


def test_the_clean_start_is_offered_on_a_word_onset_never_applied():
    plan = {"hook": {"src0": 129.27, "words": [
        {"w": "if", "t0": 129.28, "t1": 129.5},
        {"w": "somebody", "t0": 129.5, "t1": 129.68},
        {"w": "was", "t0": 129.68, "t1": 129.84}]}, "fps": 29.97}
    adv = render_qc.hook_advice(plan, (3, 8, 11), 29.97)
    assert adv["word"] == "was" and adv["start"] == 129.65
    assert adv["drops"] == ["if", "somebody"]
    lines = render_qc.findings({"hook_eyes": [3, 8, 11], "hook_advice": adv},
                               plan)
    assert lines[0].startswith("HOOK OPENS ON CLOSED EYES 0.10-0.37s")
    assert "129.65" in lines[0] and "nothing was moved" in lines[0]
    # a later start that drops the hook's first words says what it costs
    assert "cuts the hook's opening words" in lines[0]
    assert "frame 0 is open" in lines[0]
    lines = render_qc.findings({"hook_sound": [-18.0, -55.0]}, plan)
    assert lines[0].startswith("HOOK OPENS MID-SOUND")


# ── 6. orphan frames ─────────────────────────────────────────────────────

def _changes(n=120, spikes=()):
    ch = [(1.0, 0.01)] * n
    for k, level, share in spikes:
        ch[k] = (level, share)
    return ch


def test_a_one_frame_shot_is_an_orphan_even_between_two_planned_cuts():
    # Elon 21.855: the crop switched on frame 655, the source cut on 656 —
    # both 'cuts' of the plan, so the pop check passed it
    plan = {"cut_frames": [55, 56], "events": [], "end_frame": 120,
            "reframes": [(55, 152.63, "focus")]}
    ch = _changes(spikes=[(54, 61.5, .76), (55, 95.2, .91)])
    assert render_qc.jumps(ch, plan) == []
    assert render_qc.orphans(ch, plan) == [(55, 1, 152.63)]
    lines = render_qc.findings({"orphans": [[55, 1, 152.63]]},
                               dict(plan, fps=30.0))
    assert lines[0].startswith("ORPHAN FRAME at 1.83s (frame 55)")
    assert "152.63" in lines[0]
    # two frames is an orphan too; three is a (short) shot
    two = _changes(spikes=[(29, 60, .8), (31, 70, .9)])
    assert render_qc.orphans(two, {"end_frame": 120}) == [(30, 2, None)]
    three = _changes(spikes=[(29, 60, .8), (32, 70, .9)])
    assert render_qc.orphans(three, {"end_frame": 120}) == []


def test_orphans_at_the_programme_edges_and_deliberate_flashes():
    # the last frame before the end card is another shot (a keep edge a
    # frame past a source cut)
    end = _changes(spikes=[(118, 70, .9)])
    assert render_qc.orphans(end, {"end_frame": 120}) == [(119, 1, None)]
    # frame 0 alone, then the real opening
    first = _changes(spikes=[(0, 70, .9)])
    assert render_qc.orphans(first, {"end_frame": 120}) == [(0, 1, None)]
    # a deliberate flash or a junction transition is left alone
    flash = _changes(spikes=[(59, 70, .9), (60, 70, .9)])
    assert render_qc.orphans(flash, {"end_frame": 120,
                                     "still": [(58, 63)]}) == []
    assert render_qc.orphans(flash, {"end_frame": 120,
                                     "trans_windows": [(55, 65)]}) == []


def test_a_reframe_off_the_cut_and_mid_shot_from_the_plan():
    keep = [[0.0, 60.0]]
    edl = default_edl(100.0)
    edl["keep"] = keep
    # the source cuts at 40.0; the crop re-aims 3 frames late and again
    # mid-shot at 50.0
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_track": [
        {"t0": 0.0, "t1": 40.1, "x": 0.3, "y": 0.5},
        {"t0": 40.1, "t1": 50.0, "x": 0.7, "y": 0.5},
        {"t0": 50.0, "t1": 100.0, "x": 0.4, "y": 0.5}]}
    edl = validate_edl(edl, 100.0).model_dump()
    index = {"video": {"fps": 30.0}, "shots": [
        {"id": 1, "start": 0.0, "end": 40.0},
        {"id": 2, "start": 40.0, "end": 100.0}]}
    p = render_qc.plan(edl, index, W=1080, H=1920, fps=30.0, src_fps=30.0,
                       origin=0.0)
    assert p["has_shots"]
    got = render_qc.reframes_off_cut(p)
    kinds = sorted((k, round(src, 2)) for k, _f, _c, src, _w in got)
    assert kinds == [("mid", 50.0), ("off", 40.1)]
    lines = render_qc.findings({"reframes": [list(r) for r in got]}, p)
    assert any(l.startswith("REFRAME OFF THE CUT") and "3 frames" in l
               for l in lines)
    assert any(l.startswith("REFRAME MID-SHOT") for l in lines)
    # a switch within 1.5 frames of the indexed cut renders ON it
    edl["frame"]["focus_track"][0]["t1"] = 40.03
    edl["frame"]["focus_track"][1]["t0"] = 40.03
    p = render_qc.plan(edl, index, W=1080, H=1920, fps=30.0, src_fps=30.0,
                       origin=0.0)
    assert [r for r in render_qc.reframes_off_cut(p) if r[0] == "off"] == []


def _crop_clip(path, vf, d=2.0):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=270x480:rate=30:duration={d}", "-vf", vf,
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "15",
                    path], check=True)


@FFMPEG
def test_the_check_finds_the_orphan_frame_in_a_rendered_file(tmp_path):
    out = str(tmp_path / "orphan.mp4")
    # frame 30 alone is pushed in: a crop switch a frame before the cut
    _crop_clip(out, "crop=iw-60:ih-108:'if(eq(n,30),60,30)':"
                    "'if(eq(n,30),108,54)',scale=270:480")
    plan = {"program_s": 2.0, "fps": 30.0, "W": 270, "H": 480,
            "end_frame": 60, "cut_frames": [30, 31], "events": [],
            "cards": [], "watermark": None, "endcard": None,
            "reframes": [(30, 152.63, "focus")]}
    res = render_qc.check(out, plan, budget_s=60)
    assert res["jumps"] == []                     # both frames are 'cuts'
    assert res["orphans"] == [[30, 1, 152.63]]
    assert any(f.startswith("ORPHAN FRAME") for f in res["findings"])


# ── 7. card windows snap onto the cut ────────────────────────────────────

def test_a_card_window_a_frame_off_a_cut_moves_onto_it():
    ctx = _Ctx(dur=60.0, keep=[[0.0, 10.0], [20.0, 30.0]])
    edl = ctx._edl
    # the keep join sits at 10.0 program seconds
    s, e, notes = agent_tools._snap_window_to_cuts(ctx, edl, 10.05, 15.0)
    assert s == 10.0 and e == 15.0
    assert notes and "card window start 10.05->10s" in notes[0]
    # on the cut, or well off it: untouched
    assert agent_tools._snap_window_to_cuts(ctx, edl, 10.0, 15.0)[2] == []
    assert agent_tools._snap_window_to_cuts(ctx, edl, 10.2, 15.0)[2] == []
    # never collapses a window
    assert agent_tools._snap_window_to_cuts(ctx, edl, 9.95, 10.05)[2] == []
