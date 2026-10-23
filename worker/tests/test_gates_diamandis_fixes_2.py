"""Gates / Diamandis runs (22-23 Oct), second pass: the one-frame orphans no
EDL edit could move (repaint patches a frame late) and one sound-based edge
for every mid-word check."""
import math

import pytest

import audit
import config
import cut_audio
import motion_critic
import renderer


# ── repaint patches on their own frames ────────────────────────────────

def _overlay_pick(shift, patch_fps, main_t):
    """The patch frame ffmpeg's overlay shows on a main frame at main_t: the
    latest secondary frame at or before it."""
    return math.floor((main_t - shift) * patch_fps + 1e-9)


@pytest.mark.parametrize("start,end,first,origin", [
    (345.14, 349.47, 8275, 0.0),      # Gates s23 (4356): cut 348.723
    (1171.05, 1175.63, 28077, 0.0),   # Gates s15 (4368): cut 1174.882
    (2.01, 5.0, 48, 0.0),
])
def test_patch_frame_k_replaces_the_source_frame_it_was_painted_from(
        start, end, first, origin):
    fps = 24000 / 1001
    patch_fps = float(f"{fps:.5f}")             # inpaint's encoder -r
    snapped = math.floor(start * fps) / fps     # inpaint.build_patch
    k0 = int(round(snapped * fps))
    assert k0 == first
    shift, on, off = renderer.patch_clock(start, end, fps, origin)
    n = int(round((math.ceil(end * fps) / fps - snapped) * fps))
    for j in range(n):
        main_t = (k0 + j) / fps
        assert on <= main_t <= off
        assert _overlay_pick(shift, patch_fps, main_t) == j
    # the frames either side of the window are the untouched source
    assert (k0 - 1) / fps < on and (k0 + n) / fps > off


def test_the_old_placement_ran_the_window_a_frame_late():
    fps = 24000 / 1001
    patch_fps = float(f"{fps:.5f}")
    # the real cut of Gates s23: source frame 8361 (348.723) is patch frame 86
    assert _overlay_pick(345.14, patch_fps, 8361 / fps) == 85     # old: Ezra
    shift, _on, _off = renderer.patch_clock(345.14, 349.47, fps, 0.0)
    assert _overlay_pick(shift, patch_fps, 8361 / fps) == 86      # Gates


def test_patch_clock_without_a_frame_rate_keeps_the_legacy_window():
    assert renderer.patch_clock(2.0, 5.0, None) == (2.0, 2.0, 5.0)


def test_patch_renders_are_stamped_and_older_ones_re_render():
    edl = {"patches": [{"id": "pa1", "src_start": 1.0, "src_end": 2.0}]}
    assert not renderer.patch_clock_current({}, edl)
    assert renderer.patch_clock_current(
        {"patch_v": config.PATCH_CLOCK_VERSION}, edl)
    assert renderer.patch_clock_current({}, {"patches": []})


# ── one sound-based edge for every mid-word check ──────────────────────

WORDS = [{"w": "alpha", "t0": 9.70, "t1": 10.20},
         {"w": "beta", "t0": 10.50, "t1": 10.90}]
KEEP = [[5.0, 10.0], [20.0, 25.0]]


def _sounding_until(t_end):
    return lambda t: -20.0 if t < t_end else -60.0


def _hit():
    return [h for h in audit.midword_boundaries(KEEP, WORDS, 60.0)
            if h["boundary"] == 10.0]


def test_a_cut_in_a_sounding_word_gets_one_sound_safe_edge_everywhere():
    level = _sounding_until(10.08)
    v = audit.edge_verdicts(_hit(), level, 1 / 24, KEEP, WORDS)[0]
    assert v["loud"] and v["safe"] is not None and v["safe"] > 10.08
    # the keep write's warning names it (not 10.20, the transcript's end)
    lines = audit.boundary_warning_lines(KEEP, WORDS, [], 60.0,
                                         safe={10.0: v["safe"]})
    assert any(f"move it to {v['safe']:.2f}" in ln for ln in lines)
    assert not any("(word end)" in ln for ln in lines)
    # critique_short's op names the same edge
    snap, measured = motion_critic._sound_safe_edge(
        WORDS[0], 10.0, "end", KEEP, WORDS, level,
        {"video": {"fps": 24000 / 1001}})
    assert measured and snap == pytest.approx(v["safe"])


def test_a_transcript_word_over_quiet_sound_is_clean_for_every_check():
    level = _sounding_until(9.90)          # the ASR end runs late
    v = audit.edge_verdicts(_hit(), level, 1 / 24, KEEP, WORDS)[0]
    assert not v["loud"]
    snap, measured = motion_critic._sound_safe_edge(
        WORDS[0], 10.0, "end", KEEP, WORDS, level, {})
    assert snap is False and measured


def test_without_the_sound_the_critic_falls_back_like_the_keep_tools():
    snap, measured = motion_critic._sound_safe_edge(
        WORDS[0], 10.0, "end", KEEP, WORDS, None, {})
    assert not measured
    assert snap == pytest.approx(
        cut_audio.place_edge("end", 10.0, WORDS, None)["t"])


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"),
                    reason="ffmpeg required")
def test_check_speech_names_the_cut_inside_a_sounding_word(tmp_path):
    import subprocess
    from tools_ext import rhythm_tools
    wav = str(tmp_path / "src.wav")
    # 'one' sounds 1.0-1.6 s (the transcript says to 1.8); the keep ends
    # at 1.5 s, inside it
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "aevalsrc='between(t,1.0,1.6)*sin(2*PI*220*t)*0.5"
                    "+between(t,5.5,6.0)*sin(2*PI*220*t)*0.5':s=16000:d=8",
                    wav], check=True)

    class Ctx:
        duration = 8.0
        index = {"words": [{"w": "one", "t0": 1.0, "t1": 1.8},
                           {"w": "two", "t0": 5.5, "t1": 6.0}]}

        def latest_edl(self):
            return {"version": 3, "json": {"keep": [[0.0, 1.5], [5.0, 7.0]]}}

        def proxy_path(self):
            return wav

    out = rhythm_tools.check_speech(Ctx(), 0.0, 3.0)
    assert "CUT INSIDE A SOUNDING WORD: end 1.5 in 'one'" in out, out
    assert "the sound-safe edge" in out, out


# ── a word set_volume silences is not heard ────────────────────────────

def test_a_ducked_tail_word_is_not_the_last_word_heard():
    # Gates s15 (4368 v67): keep ends 1214.67, the next speaker's 'The' at
    # 1214.48-1214.66 ducked -60 dB (1214.46-1214.7)
    edl = {"keep": [[1200.0, 1214.67]],
           "volume": [{"start": 1214.46, "end": 1214.7, "gain_db": -60.0}]}
    plan = {"words": [
        {"w": "danger.", "t0": 13.0, "t1": 13.5, "s0": 1213.0, "s1": 1213.5},
        {"w": "The", "t0": 14.48, "t1": 14.66, "s0": 1214.48, "s1": 1214.66}]}
    got = motion_critic.lens_words(edl, {}, plan, 14.67)
    assert not [f for f in got if f["code"] == "payoff_hold_short"]
    # without the duck the same tail is too short
    edl.pop("volume")
    got = motion_critic.lens_words(edl, {}, plan, 14.67)
    assert [f for f in got if f["code"] == "payoff_hold_short"]


def test_silenced_needs_most_of_the_word_under_a_deep_duck():
    spans = audit.ducked_spans({"volume": [
        {"start": 10.0, "end": 10.3, "gain_db": -60.0},
        {"start": 20.0, "end": 21.0, "gain_db": -12.0}]})
    assert spans == [(10.0, 10.3)]
    assert audit.silenced(10.05, 10.25, spans)
    assert not audit.silenced(9.8, 10.1, spans)       # a third of it
    assert not audit.silenced(20.1, 20.4, spans)      # a mix duck, heard
    assert not audit.silenced(None, None, spans)


def test_a_cut_into_a_ducked_word_is_no_mid_word_cut():
    # Gates s15 (4368 v69): keep end 1214.67 inside 'That' (1214.50-1214.82)
    # under a -60 dB duck: the MID-WORD AUDIT read 'sound -8 dB' on the
    # source although nothing of it is heard
    keep = [[1200.56, 1214.67]]
    words = [{"w": "That", "t0": 1214.50, "t1": 1214.82}]
    edl = {"keep": keep,
           "volume": [{"start": 1214.46, "end": 1214.7, "gain_db": -60.0}]}
    assert audit.midword_boundaries(keep, words, 4429.5)
    ducks = audit.ducked_spans(edl)
    assert not audit.midword_boundaries(keep, words, 4429.5, ducks)
    assert not audit.midword_audit(keep, words, 4429.5, ducks=ducks)
    assert not audit.boundary_warning_lines(keep, words, [], 4429.5,
                                            ducks=ducks)
