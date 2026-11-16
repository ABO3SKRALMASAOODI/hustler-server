"""EDIT-CORE A1-A4 (16 Nov 2026): picture and sound measured right.

Golden replays of real requests: a 93 s music video became a 6.3 s teaser
and an 11 s walk was "tightened" to 2 s because stretches with no words were
read as dead air (A1); the First cut raised the walk's room tone by ~28 dB
(A2); "mute the background noise" had no capability to reach (A3); a library
track the editor offered could not be placed (A4). Each test proves the
outcome: footage kept, a dead pause still cut, the floor not lifted, the
noise lowered in the render, the track placed.
"""
import os
import shutil
import subprocess
import sys
from copy import deepcopy

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import agent_tools  # noqa: E402
import config  # noqa: E402
import dialogue_level  # noqa: E402
import edit_batch  # noqa: E402
import first_cut  # noqa: E402
import liveness  # noqa: E402
import music_library  # noqa: E402
import noise_clean  # noqa: E402
import renderer  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg"))


# ---- A1: a stretch is dead only when picture AND sound show nothing --------

def _track(seconds, moving=(), loud=(), fps=4.0, floor=-55.0):
    """A measured activity track: motion 0.08 (a talking head) except the
    moving spans (0.55, a walking camera); level at the floor except the
    loud spans (-22 dBFS, a song) and the speech the tests mark loud."""
    n = int(seconds * fps)
    t = np.arange(n) / fps
    motion = np.full(n, 0.08)
    level = np.full(n, floor)
    for a, b in moving:
        motion[(t >= a) & (t < b)] = 0.55
    for a, b in loud:
        level[(t >= a) & (t < b)] = -22.0
    return {"version": 2, "fps": fps, "motion": motion.tolist(),
            "audio": None, "level": level.tolist(), "duration": seconds,
            "t0": 0.0}


class _Ctx:
    def __init__(self, words, duration, track):
        self.duration = duration
        self.index = {"video": {"duration": duration, "fps": 25},
                      "words": words, "silences": [], "shots": []}
        self.edl = {"keep": [[0.0, duration]]}
        self.workdir = "."
        self._track = track

    def latest_edl(self):
        return {"version": 1, "json": deepcopy(self.edl)}

    def write_edl(self, edl, description):
        self.edl = deepcopy(edl)
        return "EDL v2 " + description


@pytest.fixture
def measured(monkeypatch):
    def fake(ctx, lo, hi, measure=True):
        return ctx._track, None
    monkeypatch.setattr(agent_tools, "_liveness_track", fake)


def _w(word, t0, t1):
    return {"w": word, "t0": t0, "t1": t1}


def test_a_walk_with_one_word_is_footage_not_dead_air(measured):
    # p5744: one "Yeah" at 1.33-1.97 s, the camera walking the whole 11 s
    ctx = _Ctx([_w("Yeah", 1.33, 1.97)], 11.26,
               _track(11.26, moving=[(0.0, 11.26)]))
    seen = agent_tools.find_silences(ctx, min_seconds=0.7)
    assert "0 are DEAD AIR" in seen and "ACTION, NOT DEAD AIR" in seen
    out = agent_tools.cut_silences(ctx, min_silence_s=0.7, padding_s=0.12)
    assert ctx.edl["keep"] == [[0.0, 11.26]]          # the walk is all kept
    assert "footage, not dead air" in out


def test_a_music_video_keeps_its_song_stretches(measured):
    # p5717: a few lyric words; the song plays under a still shot
    words = [_w("AJ", 8.8, 9.3), _w("yeah", 12.8, 13.1)]
    ctx = _Ctx(words, 40.0, _track(40.0, loud=[(0.0, 40.0)]))
    agent_tools.cut_silences(ctx, min_silence_s=0.7, padding_s=0.12)
    assert ctx.edl["keep"] == [[0.0, 40.0]]


def test_a_real_dead_pause_in_a_talking_head_is_still_cut(measured):
    # p5711-like: speech, a 1.4 s pause on a still picture at the room's
    # floor, speech again; the speech itself sounds
    words = [_w("um", 0.2, 0.5), _w("isso", 0.6, 3.0),
             _w("Esse", 4.4, 4.8), _w("aqui", 4.9, 8.0)]
    tr = _track(8.0, loud=[(0.2, 3.0), (4.4, 8.0)])
    ctx = _Ctx(words, 8.0, tr)
    out = agent_tools.cut_silences(ctx, min_silence_s=0.7, padding_s=0.12)
    assert out.startswith("EDL v")
    kept = sum(b - a for a, b in ctx.edl["keep"])
    assert kept < 8.0 - 1.0                            # the pause went
    assert not any(a < 3.7 < b for a, b in ctx.edl["keep"])


def test_word_adjacent_pauses_on_a_still_talking_head_are_cut(measured):
    # p5574: 0.5-0.6 s pauses between words on a still talking head. A
    # level sample spans 0.25 s, so the samples at a pause's edges also hold
    # the next word's onset; those must not make the pause read as sound.
    fps, dur = 4.0, 12.0
    words, t, pauses = [], 0.3, []
    for i in range(6):
        words.append(_w(f"w{i}", t, t + 1.37))
        t += 1.37
        if i < 5:
            pauses.append((t, t + 0.6))
            t += 0.6
    tr = _track(dur, fps=fps, floor=-80.0)
    for i in range(len(tr["level"])):
        a, b = i / fps, (i + 1) / fps          # the sample's span
        if any(a < w["t1"] and b > w["t0"] for w in words):
            tr["level"][i] = -24.0             # holds some of a word
    ctx = _Ctx(words, dur, tr)
    out = agent_tools.cut_silences(ctx, min_silence_s=0.4, padding_s=0.12)
    assert out.startswith("EDL v"), out
    for a, b in pauses:                        # every dead pause went
        mid = (a + b) / 2
        assert not any(x < mid < y for x, y in ctx.edl["keep"]), (a, b)
    assert "footage, not dead air" not in out


def test_a_pause_that_really_sounds_is_still_kept(measured):
    # p5574 46.5-47.7 s: a -28 dBFS sound between words on a still picture
    words = [_w("a", 0.2, 3.0), _w("b", 4.4, 8.0)]
    tr = _track(8.0, loud=[(0.2, 3.0), (3.0, 4.4), (4.4, 8.0)], floor=-80.0)
    ctx = _Ctx(words, 8.0, tr)
    out = agent_tools.cut_silences(ctx, min_silence_s=0.7, padding_s=0.12)
    assert ctx.edl["keep"] == [[0.0, 8.0]]
    assert "sound plays" in out


def test_liveness_reads_moving_picture_song_and_still_room_tone():
    tr = _track(12.0, moving=[(0.0, 4.0)], loud=[(4.0, 8.0)])
    assert liveness.read_span(tr, 0.0, 4.0)["live"]
    assert liveness.read_span(tr, 4.0, 8.0)["live"]
    dead = liveness.read_span(tr, 8.0, 12.0)
    assert dead["live"] is False and dead["why"] == ""
    # a shot change every second is an edited piece even on a still frame
    assert liveness.read_span(tr, 8.0, 12.0, shots=[9.0, 10.0, 11.0])["live"]


def test_the_first_cut_keeps_an_edited_piece_over_its_song(monkeypatch):
    # p5717: 93 s, 66 shot changes, the song throughout: kept whole, never a
    # montage of 0.35 s clips
    tr = _track(93.0, loud=[(0.0, 93.0)])
    monkeypatch.setattr(agent_tools, "_liveness_track",
                        lambda ctx, lo, hi, measure=True: (tr, None))

    class Ctx:
        index = {"shots": [{"start": round(i * 1.4, 2)} for i in range(67)]}
    assert first_cut._edited_song_piece(Ctx(), 93.0)
    # a raw take (a concert filmed on a phone: few shots) is not one
    Ctx.index = {"shots": [{"start": 0.0}, {"start": 40.0}]}
    assert not first_cut._edited_song_piece(Ctx(), 93.0)


# ---- A2: the master never lifts a noise floor into foreground --------------

def _blocks(levels, step=0.1):
    return [(round((i + 1) * step - 0.2, 3), v) for i, v in enumerate(levels)]


def test_room_tone_is_not_leveled_or_called_content():
    rng = np.random.default_rng(5)
    walk = list(-43.0 + rng.normal(0, 0.8, 110))      # p5744: -42.9 LUFS
    assert dialogue_level.read_bed(_blocks(walk), speech=[(1.3, 2.0)])[
        "content"] is False
    assert dialogue_level.gain_knots(_blocks(walk), speech=[(1.3, 2.0)]) \
        is None
    # without a transcript, a steady quiet bed is room tone too
    assert dialogue_level.read_bed(_blocks(walk))["content"] is False


def test_speech_and_music_are_still_content():
    talk = [-30.0] * 50 + [-62.0] * 20 + [-30.0] * 50
    assert dialogue_level.read_bed(_blocks(talk), speech=[(0, 5), (7, 12)])[
        "content"]
    assert dialogue_level.gain_knots(_blocks(talk), speech=[(0, 5), (7, 12)])
    song = [-21.0] * 120
    assert dialogue_level.read_bed(_blocks(song), speech=[])["content"]


def _gain_at(knots, t):
    for (t0, g0), (t1, g1) in zip(knots, knots[1:]):
        if t0 <= t <= t1:
            return g0 + (g1 - g0) * (t - t0) / max(t1 - t0, 1e-9)
    return knots[-1][1] if t > knots[-1][0] else knots[0][1]


@pytest.mark.parametrize("down", [16.0, 10.0])
def test_a_quiet_speaker_in_a_tight_program_is_still_lifted(down):
    # After the first cut's cut_silences there are no pauses left: the
    # quieter speaker IS the program's floor, and must still be leveled up
    # to the louder one, with or without a transcript.
    loud, quiet = -22.0, -22.0 - down
    blocks = _blocks([loud] * 100 + [quiet] * 100 + [loud] * 100)
    for speech in (None, [(0.0, 30.0)]):
        bed = dialogue_level.read_bed(blocks, speech=speech)
        assert bed["content"], (speech, bed)
        knots = dialogue_level.gain_knots(blocks, speech=speech)
        lifted = _gain_at(knots, 15.0) - _gain_at(knots, 5.0)
        assert lifted >= 0.6 * down, (down, speech, lifted)
        assert _gain_at(knots, 15.0) > 5.0, knots


def test_a_quiet_take_with_short_pauses_is_speech_not_room_tone():
    # a quiet recording (-40 LUFS speech) with pauses under 10% of it: the
    # room floor is read between the words, not from the quiet speech
    take = ([-40.0] * 45 + [-62.0] * 4) * 6
    speech = [(i * 4.9, i * 4.9 + 4.5) for i in range(6)]
    assert dialogue_level.read_bed(_blocks(take), speech=speech)["content"]
    knots = dialogue_level.gain_knots(_blocks(take), speech=speech)
    assert knots and _gain_at(knots, 10.0) > 15.0, knots


def _graph(edl, **kw):
    tl = Timeline(edl["keep"], [])
    return renderer.build_filtergraph(
        edl, 20.0, True, tl, None, kw.pop("music_inputs", []),
        {"words": []}, False, W=1080, H=1920, fps=30.0, src_w=1080,
        src_h=1920, **kw)


def test_an_ambient_program_is_not_mastered_up_but_music_still_is():
    tall = {"keep": [[0.0, 20.0]], "frame": {"ratio": "9:16"}}
    assert "loudnorm" in _graph(tall)
    assert "loudnorm" not in _graph(tall, ambient_bed=True)
    music = [(1, {"id": "m", "storage_key": "music/1/x.mp3", "start": 0.0,
                  "end": 20.0, "gain_db": -18}, 60.0)]
    assert "loudnorm" in _graph(tall, ambient_bed=True, music_inputs=music)


def _ff(args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True,
                   capture_output=True)


def _room(path, seconds=8.0, tone=False):
    """A portrait clip of steady pink noise at about -45 dBFS (a room), with
    a voiced tone from 2 to 5 s when tone."""
    a = f"anoisesrc=c=pink:a=0.006:r=48000:d={seconds}:seed=1"
    graph = "[1:a]anull[a]"
    if tone:
        graph = ("aevalsrc='0.25*sin(2*PI*220*t)*between(t,2,5)':s=48000:"
                 f"d={seconds}[v];[1:a][v]amix=inputs=2:normalize=0[a]")
    _ff(["-f", "lavfi", "-i", f"color=c=gray:s=180x320:r=25:d={seconds}",
         "-f", "lavfi", "-i", a, "-filter_complex", graph,
         "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-threads", "1",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(path)])


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg required")
def test_a_room_tone_first_cut_keeps_its_recorded_level(tmp_path,
                                                         monkeypatch):
    monkeypatch.setattr(config, "OUTRO_ON_PREVIEW", False, raising=False)
    src = tmp_path / "walk.mp4"
    _room(src)
    index = {"video": {"duration": 8.0, "width": 180, "height": 320,
                       "fps": 25}, "words": [_w("Yeah", 1.3, 1.9)]}
    edl = default_edl(8.0)
    out = tmp_path / "first_cut.m4a"
    renderer.render_edl(edl, index, str(src), str(out), str(tmp_path),
                        preview=True, audio_only=True)
    before, after = noise_clean.level_db(str(src)), noise_clean.level_db(
        str(out))
    assert abs(after - before) < 1.5, (before, after)   # was +28 dB
    assert renderer.ambient_program(edl, str(out))


# ---- A3: noise cleaning is a real capability, and the render carries it ----

@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg required")
def test_clean_lowers_the_noise_and_keeps_the_voice(tmp_path):
    src = tmp_path / "room_voice.mp4"
    _room(src, tone=True)
    out = tmp_path / "clean.m4a"
    rep = noise_clean.clean(str(src), str(out), [[2.0, 5.0]], 0.8)
    assert rep["profile"] == "pauses" and rep["reduction_db"] >= 30
    noise_before = noise_clean.level_db(str(src), 6.0, 7.8)
    noise_after = noise_clean.level_db(str(out), 6.0, 7.8)
    voice_before = noise_clean.level_db(str(src), 2.5, 4.5)
    voice_after = noise_clean.level_db(str(out), 2.5, 4.5)
    assert noise_before - noise_after >= 15, (noise_before, noise_after)
    assert abs(voice_before - voice_after) <= 2.0, (voice_before, voice_after)


def test_any_writer_s_noise_clean_node_is_repaired_not_refused():
    # a direct ctx.write_edl (not through edit_batch) with a strength out of
    # range and a foreign key: the EDL validates, the strength is clamped and
    # the key dropped, so the render cleans the footage's own sound
    edl = default_edl(10.0)
    edl["noise_clean"] = {"strength": 1.7, "audio_key": "music/1/x.mp3",
                          "applies_to": "speech", "floor_db": "n/a"}
    nc = validate_edl(edl, 10.0).model_dump()["noise_clean"]
    assert nc["strength"] == 1.0 and nc["audio_key"] is None
    assert nc["applies_to"] == "source" and nc["floor_db"] is None
    edl["noise_clean"] = {"strength": -0.4,
                          "audio_key": "analysis/noise_clean/ab/0.80.m4a"}
    nc = validate_edl(edl, 10.0).model_dump()["noise_clean"]
    assert nc["strength"] == 0.0
    assert nc["audio_key"] == "analysis/noise_clean/ab/0.80.m4a"


def test_the_batch_layer_and_the_edl_carry_the_node():
    base = validate_edl(default_edl(10.0), 10.0).model_dump()
    notes = []
    out = edit_batch.apply_batch(base, [{"action": "set", "layer":
                                         "noise_clean", "value": 0.7}],
                                 10.0, set(), notes=notes)
    assert out["noise_clean"]["strength"] == 0.7
    assert any("background noise" in n for n in notes)
    keyed = dict(out, noise_clean={"strength": 0.7, "audio_key":
                                   "analysis/noise_clean/v1/x/s070.m4a"})
    again = edit_batch.apply_batch(keyed, [{"action": "upsert", "layer":
                                            "noise_clean",
                                            "value": {"strength": 1.0}}],
                                   10.0, set())
    assert again["noise_clean"]["strength"] == 1.0
    assert again["noise_clean"]["audio_key"] is None   # made for 0.7
    off = edit_batch.apply_batch(out, [{"action": "set", "layer":
                                        "noise_clean", "value": None}],
                                 10.0, set())
    assert off.get("noise_clean") is None
    schema = agent_tools.TOOLS["apply_edit_batch"][2]
    layers = schema["operations"]["items"]["properties"]["layer"]["enum"]
    assert "noise_clean" in layers and "clean_noise" in agent_tools.TOOLS


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg required")
def test_the_render_plays_the_cleaned_sound(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTRO_ON_PREVIEW", False, raising=False)
    monkeypatch.setattr(config, "TMP_DIR", str(tmp_path / "tmp"))
    src = tmp_path / "walk.mp4"
    _room(src, tone=True)
    index = {"video": {"duration": 8.0, "width": 180, "height": 320,
                       "fps": 25}, "words": [_w("ah", 2.0, 5.0)]}
    plain = default_edl(8.0)
    cleaned = edit_batch.apply_batch(
        validate_edl(plain, 8.0).model_dump(),
        [{"action": "set", "layer": "noise_clean", "value": {"strength":
                                                             0.8}}],
        8.0, set())
    a, b = tmp_path / "a.m4a", tmp_path / "b.m4a"
    for edl, out in ((plain, a), (cleaned, b)):
        renderer.render_edl(edl, index, str(src), str(out), str(tmp_path),
                            preview=True, audio_only=True)
    assert noise_clean.level_db(str(a), 6.0, 7.8) - \
        noise_clean.level_db(str(b), 6.0, 7.8) >= 12


def test_harness2_audio_op_reaches_the_cleaner(monkeypatch):
    from harness2 import ops
    calls = []
    monkeypatch.setattr(ops, "_run_verb",
                        lambda stage, name, args, env, index:
                        calls.append((name, args)) or True)
    assert ops.op_audio(None, {"clean": 0.9}, None, 0)
    assert ops.op_audio(None, {"clean": False}, None, 1)
    assert calls == [("clean_noise", {"strength": 0.9}),
                     ("clean_noise", {"strength": 0})]


# ---- A4: every track the editor can see can be placed -----------------------

class _Missing(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


def test_a_transient_copy_error_is_retried(monkeypatch):
    monkeypatch.setattr(music_library, "COPY_BACKOFF_S", (0.0, 0.0))
    seen = []

    def copy(src, dst):
        seen.append(src)
        if len(seen) == 1:
            raise RuntimeError("SlowDown: please reduce your request rate")
    monkeypatch.setattr(music_library.storage, "copy_object", copy)
    track = music_library.find("chill-calm-currents-lofi-relax-calm")[0]
    assert music_library._copy_track(None, track, "music/9/x.mp3") is None
    assert len(seen) == 2


def test_a_missing_library_object_is_placed_from_an_earlier_copy(
        monkeypatch):
    monkeypatch.setattr(music_library, "COPY_BACKOFF_S", (0.0, 0.0))
    track = music_library.find("chill-calm-currents-lofi-relax-calm")[0]
    other = "music/5744/library-chill-calm-currents-lofi-relax-calm.mp3"

    class DB:
        def run(self, fn, *a):
            return [other]

    class Ctx:
        db = DB()
    placed = []

    def copy(src, dst):
        if src == track["storage_key"]:
            raise _Missing("Not Found")
        placed.append(src)
    monkeypatch.setattr(music_library.storage, "copy_object", copy)
    assert music_library._copy_track(Ctx(), track, "music/9/x.mp3") is None
    assert placed == [other]
