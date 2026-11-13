"""The sound engine lane (13 Nov): the signal ruler (score/sound_signal.py), the choice (score/sound_choice.py
on score/sound_profiles.py), the music engine (score/music.py) and the owner's batch approval.

Signal only: synthetic sounds and a synthetic song, the owner's own library, no model, no network."""
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from score import music as MU  # noqa: E402
from score import sound as SD  # noqa: E402
from score import sound_choice as CH  # noqa: E402
from score import sound_profiles as SP  # noqa: E402
from score import sound_signal as SS  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
needs_ffmpeg = pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")
SR = SS.SR


def _noise(n, seed=3):
    return np.random.default_rng(seed).standard_normal(n).astype(np.float32)


def _shape(kind):
    """One second of silence with one synthetic sound of a known shape in it, at 48 kHz."""
    x = np.zeros(int(2.4 * SR), np.float32)
    at = int(1.2 * SR)
    if kind == "click":
        n = int(0.03 * SR)
        x[at:at + n] = _noise(n) * np.exp(-np.arange(n) / (0.006 * SR)) * 0.5
    elif kind == "whoosh":
        n = int(0.4 * SR)
        env = np.concatenate([np.linspace(0, 1, n // 2) ** 2, np.linspace(1, 0, n - n // 2) ** 2])
        sp = np.fft.rfft(_noise(n))
        hz = np.fft.rfftfreq(n, 1 / SR)
        sp[(hz < 500) | (hz > 9000)] = 0
        x[at - n // 2:at - n // 2 + n] = np.fft.irfft(sp, n).astype(np.float32) * env * 0.3
    elif kind == "impact":
        n = int(0.9 * SR)
        t = np.arange(n) / SR
        x[at:at + n] = (np.sin(2 * np.pi * 55 * t) * np.exp(-t * 5) * 0.8).astype(np.float32)
    elif kind == "riser":
        n = int(1.1 * SR)
        sp = np.fft.rfft(_noise(n))
        hz = np.fft.rfftfreq(n, 1 / SR)
        sp[(hz < 800) | (hz > 10000)] = 0
        x[at - n:at] = np.fft.irfft(sp, n).astype(np.float32) * (np.linspace(0, 1, n) ** 2) * 0.3
    return x


def test_the_ruler_reads_a_shape_for_what_it_is():
    got = {k: SS.describe(_shape(k)) for k in ("click", "whoosh", "impact", "riser")}
    assert {k: d["kind"] for k, d in got.items()} == {"click": "click", "whoosh": "whoosh", "impact": "impact", "riser": "riser"}
    assert got["click"]["dur_s"] < 0.09 < got["whoosh"]["dur_s"]
    assert got["impact"]["low"] > 0.8 and got["riser"]["attack_s"] > 0.7
    assert abs(got["impact"]["peak_s"] - 1.2) < 0.06                       # the peak is where the sound was put
    scale = SP.SCALE
    for k in ("click", "whoosh", "impact"):                               # each shape sits nearest its own reference class
        near = min(SP.CLASSES, key=lambda c: SS.distance(SS.vector(got[k]), SP.CLASSES[c]["vec"], scale))
        assert near == k, (k, near)          # (the study holds few risers; a riser is told by its climb, kind_of)
    assert SS.distance(got["click"], got["click"]) == 0.0


def test_the_bed_under_a_sound_is_taken_off():
    """A click on a steady bed reads as the click, not as the bed."""
    x = _shape("click")
    t = np.arange(len(x)) / SR
    bed = (np.sin(2 * np.pi * 80 * t) * 0.2).astype(np.float32)
    d = SS.describe(x + bed, bg=bed[:int(0.3 * SR)], at=1.2)
    assert d["kind"] == "click" and d["low"] < 0.2


def test_the_profiles_are_the_study():
    assert SP.FILMS >= 71 and set(SP.CLASSES) >= {"click", "hit", "impact", "whoosh", "riser"}
    for m in ("cut", "moving_cut", "flash", "jump", "hook", "payoff", "landing", "type"):
        assert abs(sum(SP.MOMENTS[m].values()) - 1.0) < 0.06, m
    lib = SD.library()
    known = [sid for sid, d in SP.LIBRARY.items() if d.get("sha") == lib.get(sid, {}).get("sha")]
    assert len(known) >= 20                                               # the approved library is on the ruler


@needs_ffmpeg
def test_a_moment_gets_the_class_the_references_use_there():
    desc = CH.describe_library(SD.library(), SD.SOUNDS, SD.pcm)
    lib = SD.library()
    # a cash register or a ding is never a guess: only general roles of the first library and recordings the references vouch for
    assert desc and all(lib[s]["role"] in CH.GENERAL or lib[s].get("reference") for s in desc)
    assert not {lib[s]["role"] for s in desc} & {"cash", "ding", "heartbeat", "typing", "drone"}
    free = lambda sid: True
    # a jump cut: a click, the references' own answer there
    cls, lead = CH.class_for("jump", 0.4, 0.5, {})
    assert cls == "click" and lead is None
    # a light moment never carries an impact; the hook with room gets a hit with a climb into it
    for m in ("cut", "landing", "type"):
        assert CH.class_for(m, 0.3, 2.0, {})[0] != "impact"
    cls, lead = CH.class_for("payoff", 1.0, 2.0, {})
    assert cls == "impact" and lead == "riser"
    assert CH.class_for("payoff", 1.0, 0.2, {})[1] is None                # no room, no climb
    # the shares are spent: a film that has heard three whooshes on its cuts hears something else next
    first = CH.class_for("cut", 0.7, 1.0, {})[0]
    assert CH.class_for("cut", 0.7, 1.0, {first: 3})[0] != first
    # a riser is never the hit
    for m in SP.MOMENTS:
        assert CH.class_for(m, 1.0, 3.0, {})[0] != "riser"
    # the recording is an approved one of that shape, and a heard one gives way
    a = CH.nearest("whoosh", desc, False, free)
    assert a in lib and desc[a]["kind"] == "whoosh"
    b = CH.nearest("whoosh", desc, False, free, used={a: 3})
    assert b in lib and b != a
    assert desc[CH.nearest("riser", desc, False, free)]["kind"] == "riser"
    layers, cls = CH.layers({"f": 300, "kind": "display", "room_s": 2.5}, "payoff", {"desc": desc, "tally": {}, "free": free, "spoken": False})
    assert cls == "impact" and [w for _s, w in layers] == ["lead", "hit"]
    assert desc[layers[0][0]]["kind"] == "riser" and desc[layers[1][0]]["kind"] == "impact"


@needs_ffmpeg
def test_a_recording_that_means_something_answers_only_the_picture_that_shows_it():
    """SND2: paper for a page or a document, a shutter for a still photograph, a glitch for a glitched flash, a braam for
    the film's structural landings, and none of them for anything else. The moment's weight picks the length."""
    desc = CH.describe_library(SD.library(), SD.SOUNDS, SD.pcm)
    fam = lambda sid: desc[sid].get("family")
    free = lambda sid: True
    st = lambda: {"desc": desc, "tally": {}, "free": free, "spoken": False, "used": {}}
    assert {fam(s) for s in desc} >= {"paper", "shutter", "glitch", "braam", None}
    # a plain moving cut never hears paper, a camera, a glitch or a braam
    for k in range(6):
        got, _ = CH.layers({"f": 300 + 40 * k, "kind": "picture", "size": 0.3 + 0.1 * k, "room_s": 2.0, "moving": bool(k % 2)}, "plain", st())
        assert got and all(fam(s) is None for s, _w in got), got
    assert CH.fits_of({"kind": "page"}, "plain") == {"paper"}
    assert CH.fits_of({"kind": "picture", "still": True}, "plain") == {"shutter", "paper"}
    assert CH.fits_of({"kind": "flash", "glitch": True}, "plain") == {"glitch"}
    assert CH.fits_of({"kind": "flash"}, "plain") == set() and "braam" in CH.fits_of({"kind": "display"}, "payoff")
    for cls in ("click", "hit", "whoosh"):
        assert fam(CH.nearest(cls, desc, False, free, fits={"paper"})) in ("paper", None)
        assert fam(CH.nearest(cls, desc, False, free, fits=set())) is None
    assert fam(CH.nearest("click", desc, False, free, fits={"shutter"}, weight=0.5)) == "shutter"
    assert fam(CH.nearest("whoosh", desc, False, free, fits={"glitch"}, weight=0.55)) == "glitch"
    # a heavy moment hears a longer recording of the class than a light one
    light, heavy = CH.nearest("whoosh", desc, False, free, weight=0.3), CH.nearest("whoosh", desc, False, free, weight=0.9)
    assert desc[light]["dur_s"] < desc[heavy]["dur_s"]
    # a recording the references vouch for is set at its class's reference level against the voice
    sid = CH.nearest("impact", desc, False, free, weight=1.0)
    assert desc[sid]["matched"] and SD.class_of(sid) == "impact"
    hit = SD.library()[sid]["hit_lufs"]
    # its class's reading on the references' stems plus what a stem loses of a sound under a voice
    want = SD.VOICE_LUFS + SP.CLASSES["impact"]["over_voice_spoken_db"] + SD.STEM_LOSS_DB
    assert SD.STEM_LOSS_DB == 3.0 and abs(hit + SD.gain_for(sid, SD.VOICE_LUFS) - want) <= 0.06
    assert SD.gain_for(sid, SD.VOICE_LUFS, under=4.0) <= SD.VOICE_LUFS - 4.0 - hit + 0.06


def test_a_film_of_new_pictures_carries_the_judges_amount_and_no_more():
    """SND2: 3.6 clearly audible sounds per 10 s on a film of new pictures over a bed (the judge's median), what the
    governor allows on some pictures and on a take, 0.8 on a long film; never more in any 10 s than the rate and a
    half; two gestures never inside 0.6 s."""
    total, win, _al = SD.allowance("P", 12.0, 60.0, True)
    assert total == 21 and win == 6
    assert SD.allowance("P", 3.0, 60.0, True)[0] <= 16                    # the picture supply still earns it
    assert SD.allowance("M", 3.0, 60.0, True)[0] == 18 and SD.allowance("T", 0.5, 60.0, True)[0] == 12
    assert SD.allowance("T", 0.5, 30.0, False)[0] <= 3 and SD.allowance("L", 6.0, 600.0, True)[0] == 48
    fps, dur = 30, 60.0
    D = int(dur * fps)
    ev = [{"f": 45, "kind": "display"}] + [{"f": f, "kind": "picture", "size": 0.4 + 0.5 * ((f // 21) % 2)} for f in range(90, D - 60, 21)]
    ctx = {"fps": fps, "dur_f": D, "existing": [], "speech": None, "voice": None, "words": [], "bed": True, "np10": 14.0, "turn_f": 1200}
    sounds, rep = SD.plan_sounds(ev, ctx)
    t = sorted(p["t"] for p in rep["placed"])
    assert 18 <= len(t) <= 21, len(t)                                     # 3.0 to 3.6 per 10 s
    assert min(b - a for a, b in zip(t, t[1:])) >= SD.SPACE_S - 1e-6
    for w in np.arange(0, dur, 0.5):
        assert sum(1 for x in t if w <= x < w + 10) <= rep["allow_10s"]
    for a in sorted({s["s"] for s in sounds}):                             # one recording: twice in 30 s, never inside 3 s
        at = sorted(s["event_f"] / fps for s in sounds if s["s"] == a and s.get("layer") != "lead")
        assert all(b - c >= SD.SAME_GAP_S for c, b in zip(at, at[1:])), a
    assert all(s["event_f"] in {e["f"] for e in ev} for s in sounds)      # every sound has a picture event under it
    # the allowance reaches the end of the film: every 10 s of it carries sound, and no silence runs past 8 s
    assert all(any(w <= x < w + 10 for x in t) for w in np.arange(0, dur - 10, 2.0))
    assert max(b - a for a, b in zip([0.0] + t, t + [dur])) <= 8.0, t
    halves = [sum(1 for x in t if x < dur / 2), sum(1 for x in t if x >= dur / 2)]
    assert abs(halves[0] - halves[1]) <= 4, halves


@needs_ffmpeg
def test_a_burst_of_quick_cuts_is_one_train_of_clicks():
    """The judge's list bursts (TEARDOWN B31): one click on each cut of a run of quick cuts, one gesture to the count,
    never the same sample twice in a row, nothing else inside it, and two trains never back to back."""
    fps, dur = 30, 30.0
    D = int(dur * fps)
    run = [240 + 6 * k for k in range(8)]                                 # eight stills 0.2 s apart from 8.0 s
    again = [330 + 6 * k for k in range(6)]                               # another run 3 s later: too soon for a second train
    ev = [{"f": 45, "kind": "display"}, {"f": 150, "kind": "picture", "size": 0.8}] + \
         [{"f": f, "kind": "picture", "still": True} for f in run + again] + [{"f": 246, "kind": "display"}, {"f": 700, "kind": "picture", "size": 0.9}]
    ctx = {"fps": fps, "dur_f": D, "existing": [], "speech": None, "voice": None, "words": [], "bed": True, "np10": 9.0}
    assert [[e["f"] for e in r] for r in SD.bursts(ev, fps)] == [run, again]
    sounds, rep = SD.plan_sounds([dict(e) for e in ev], ctx)
    lib = SD.library()
    trains = [p for p in rep["placed"] if p["kind"] == "burst"]
    assert len(trains) == 1 and trains[0]["t"] == 8.0 and trains[0]["class"] == "click" and trains[0]["train"] == 8
    clicks = sorted((s["event_f"], s["s"]) for s in sounds if s["for"] == "burst")
    assert [f for f, _ in clicks] == run                                   # one on each cut, its peak on the cut
    ids = [sid for _, sid in clicks]
    assert len(set(ids)) >= 2 and all(a != b for a, b in zip(ids, ids[1:]))
    # each a single short click of one kind: nothing with a second clack in it, nothing that rings into the next cut
    assert all(not lib[sid].get("hit_s") and (lib[sid].get("max_s") or lib[sid]["duration_s"]) <= SD.TRAIN_CLICK_S for sid in ids)
    assert len({lib[sid].get("family") for sid in ids}) == 1
    others = [p["t"] for p in rep["placed"] if p["kind"] != "burst"]
    assert not [t for t in others if 8.0 - SD.SPACE_S < t < run[-1] / fps + SD.SPACE_S]                 # the display word inside it stays silent
    assert len(rep["placed"]) <= rep["allow"]                               # a train is one gesture to the allowance
    # three quick cuts are not a burst
    assert SD.bursts([{"f": f, "kind": "picture"} for f in (30, 36, 42)], fps) == []


@needs_ffmpeg
def test_a_recording_that_joins_the_library_is_read_from_its_samples(tmp_path):
    """No table names a file: an approved recording the profiles have never seen is described when it is first met."""
    x = np.stack([_shape("click")[int(1.15 * SR):int(1.35 * SR)]] * 2, axis=1)
    raw = tmp_path / "n.f32"
    x.astype(np.float32).tofile(raw)
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw), "-c:a", "flac", str(tmp_path / "new_1.flac")], check=True)
    lib = {"new_1": {"id": "new_1", "role": "click", "file": "new_1.flac", "sha": "not-in-the-table"}}
    desc = CH.describe_library(lib, str(tmp_path), SD.pcm)
    assert desc["new_1"]["kind"] == "click"


@needs_ffmpeg
def test_the_plan_names_the_class_of_every_gesture_and_keeps_the_library(tmp_path):
    fps, dur = 30, 30.0
    D = int(dur * fps)
    ev = [{"f": 60, "kind": "display"}] + [{"f": f, "kind": "picture", "size": 0.9} for f in range(150, D - 60, 75)]
    ctx = {"fps": fps, "dur_f": D, "existing": [], "speech": None, "voice": None, "words": [], "bed": True, "np10": 8.0, "turn_f": 600}
    sounds, rep = SD.plan_sounds(ev, ctx)
    lib = SD.library()
    assert sounds and all(s["s"] in lib for s in sounds)
    kinds = {p["class"] for p in rep["placed"]}
    assert None not in kinds and len(kinds) >= 3, rep["placed"]           # a film of cuts does not hear one sound
    turn = [p for p in rep["placed"] if abs(p["t"] - 20.0) < 0.05]
    assert turn and turn[0]["part"] == "payoff" and turn[0]["class"] == "impact"
    for w in np.arange(0, dur, 0.5):                                      # the footage's allowance still holds
        assert sum(1 for p in rep["placed"] if w <= p["t"] < w + 10) <= rep["allow_10s"]


# ----------------------------------------------------------------- the music engine

def _song(tmp_path, bars=28, intro_bars=8):
    from test_w2c_rhythm import synth
    y, truth = synth(bpm=120.0, bars=bars, intro_bars=intro_bars)
    from rhythm import dsp
    raw = tmp_path / "song.f32"
    np.asarray(y, np.float32).tofile(raw)
    out = str(tmp_path / "song.wav")
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(dsp.SR), "-ac", "1", "-i", str(raw), "-ac", "2", "-ar", "48000", out], check=True)
    return out, truth


@pytest.fixture(scope="module")
def song(tmp_path_factory):
    if shutil.which(FF) is None:
        pytest.skip("needs ffmpeg")
    p, truth = _song(tmp_path_factory.mktemp("song"))
    return p, MU.analyse(p), truth


def test_the_song_is_read_on_one_bar_grid(song):
    _p, s, truth = song
    assert s["steady"] and abs(s["bar_s"] - 2.0) < 0.03 and len(s["bars"]) >= 24
    drops = [b for b in s["bars"] if b["drop"]]
    assert drops and abs(drops[0]["t0"] - truth["drop"]) < 0.25 * s["bar_s"]
    before, after = [b["e"] for b in s["bars"] if b["t1"] <= truth["drop"]], [b["e"] for b in s["bars"] if b["t0"] >= truth["drop"]]
    assert np.mean(after) > np.mean(before) + 0.3                         # the drop is a step up in the song's own level


def _played(plan):
    return [(x["bar"], x["role"], x["e"]) for x in plan["bars_played"]]


def test_the_film_opens_full_builds_into_the_drop_and_never_plays_the_intro(song):
    """MUSIC2 (owner, 13 Nov: "a very boring and weird part"): the song's intro is not what the film waits in. The film
    opens on a full part, the song pulls back for a build of two to four bars, and its drop lands on the turn."""
    _p, s, _truth = song
    words = [(0.4 + k * 0.5, 0.8 + k * 0.5) for k in range(60) if not 13.0 < 0.4 + k * 0.5 < 14.0]      # speech to 30.3 s, a breath at 13-14
    marks = {"dur_s": 32.0, "words": words, "turn_s": 14.1, "end_hit_s": None, "quiet": []}
    plan = MU.arrange(s, marks)
    assert plan["ok"] and abs(plan["drop_at"] - 14.1) < 0.02 and plan["turn_miss_s"] == 0.0 and plan["drop_is"] == "entrance"
    assert all(abs(x["tempo"] - 1.0) <= MU.STRETCH_MAX + 1e-3 for x in plan["spans"])
    assert max(w1 for _w0, w1 in words) <= plan["end_hit_s"] and plan["end_hit_s"] + plan["end_tail_s"] <= 32.0 - MU.PROFILE["end_gap_s"] + 1e-6
    seg = plan["segments"]
    assert abs(seg[-1]["film"][1] - plan["end_hit_s"]) < 1e-3 and seg[0]["film"][0] < MU.PROFILE["hole_max_s"]
    for a, b in zip(seg, seg[1:]):
        assert abs(a["film"][1] - b["film"][0]) < 1e-3                    # the film is covered, bar after bar
    rows = _played(plan)
    bars = s["bars"]
    opening = [e for _b, role, e in rows if role in ("open", "groove")]
    assert opening and min(opening) >= 0.6 and plan["intro_bars"] == 0 and plan["low_bars"] == 0      # a full part from the first bar
    build = [(b, e) for b, role, e in rows if role == "build"]
    assert MU.BUILD_BARS[0] <= len(build) <= MU.BUILD_BARS[1] and max(e for _b, e in build) < 0.5       # the pull-back before the drop
    drop = next(b for b, role, _e in rows if role == "drop")
    assert bars[drop]["entry"] and bars[drop]["drop"] and build[-1][0] == drop - 1                    # the song's own bars lead into its own drop
    assert min(e for _b, role, e in rows if role in ("drop", "post")) >= 0.6
    assert [g for g in plan["grid"] if g["down"]] and plan["build_at"] < plan["drop_at"]


def test_the_build_starts_where_the_story_turns_and_on_a_picture_cut_when_one_is_near(song):
    _p, s, _truth = song
    words = [(0.4 + k * 0.5, 0.8 + k * 0.5) for k in range(70)]
    marks = {"dur_s": 40.0, "words": words, "turn_s": 15.0, "payoff_s": 22.0, "end_hit_s": None, "quiet": [], "cuts": [3.0, 9.1, 15.9, 22.03, 30.0]}
    plan = MU.arrange(s, marks)
    assert plan["ok"] and abs(plan["drop_at"] - 22.03) < 1e-6           # the drop takes the cut the payoff sits on: the visual turn
    assert abs(plan["build_at"] - 15.9) < 1e-6 and plan["build_bars"] == 3      # three bars from the cut nearest the turn, inside the tempo's limit
    assert all(abs(x["tempo"] - 1.0) <= MU.STRETCH_MAX + 1e-3 for x in plan["spans"])
    assert MU.arrange(s, dict(marks, snap=False))["drop_at"] == 22.0     # a landing the film itself made is kept to the frame


def test_the_song_sits_under_the_voice_rises_in_a_pause_and_ends_on_its_hit(song):
    p, s, _truth = song
    words = [(0.4 + k * 0.5, 0.85 + k * 0.5) for k in range(60) if not 5.0 < 0.4 + k * 0.5 < 6.6]      # a pause of 1.55 s at 5.35-6.9
    marks = {"dur_s": 32.0, "words": words, "turn_s": 14.1, "end_hit_s": None, "quiet": []}
    plan = MU.arrange(s, marks)
    music, rep = MU.track(p, s, plan, marks, voice=None)
    assert music.shape == (int(32.0 * MU.SR), 2)
    end = int(rep["silent_from_s"] * MU.SR)
    assert float(np.abs(music[end:]).max()) == 0.0 and rep["silent_from_s"] <= 32.0 - MU.PROFILE["end_gap_s"] + 1e-6
    hit = int(plan["end_hit_s"] * MU.SR)
    assert float(np.abs(music[hit:hit + int(0.1 * MU.SR)]).max()) > 0.0   # the last hit is there

    def lv(a, b):
        seg = music[int(a * MU.SR):int(b * MU.SR)]
        return 10 * np.log10(float((seg ** 2).mean()) + 1e-12)
    assert abs(rep["under_voice_db"] - MU.PROFILE["under_voice_db"]) <= 0.6     # on the judge's ruler, whatever the song's crest
    assert len(rep["lifts"]) == 1 and abs(rep["lifts"][0]["t"] - 5.35) < 0.01 and rep["lifts"][0]["db"] == MU.PROFILE["lift_max_db"]
    assert lv(5.9, 6.5) > lv(2.0, 4.5) + 2.5                              # up in the pause, down again before the next word
    d = plan["drop_at"]
    assert rep["holes"][0]["before"] == "drop" and lv(d - 0.3, d - 0.05) < lv(d - 2.0, d - 1.0) - 10.0     # out for a beat before the drop
    assert lv(d, d + 0.4) > lv(d - 2.0, d - 1.0)                          # and the drop over the bed
    assert rep["ride_wobble_db"] < 1.0                                    # the ride does not move at syllable rate: no pumping


def test_a_breath_gets_no_lift_and_the_ride_swells_into_the_films_own_sounds():
    P = MU.PROFILE
    nf, hop = 3000, 0.01
    words = [(0.2, 4.0), (4.4, 9.0), (10.2, 20.0), (20.3, 27.0)]            # breaths of 0.4 and 0.3 s, a pause of 1.2 s
    plan = {"drop_at": None, "end_hit_s": 27.2, "beat_s": 0.5, "spans": [{"t0": 0.0, "t1": 27.2, "beats": 54, "beat_s": 0.5}]}
    slow, fast, notes = MU.flow(nf, hop, {"words": words, "accents": [15.0]}, plan, P, -10.0, -6.0)
    assert [x["t"] for x in notes["lifts"]] == [9.0]                     # only the pause
    assert slow[int(4.2 / hop)] == -10.0 and slow[int(20.15 / hop)] == -10.0
    assert -10.0 + P["lift_db"] < slow[int(9.7 / hop)] < -10.0 + P["lift_max_db"] + 1e-6 and slow[int(10.2 / hop)] <= -10.0 + 1e-6      # down before the next word
    assert abs(slow[int(15.0 / hop)] - (-10.0 + P["accent_db"])) < 0.1 and slow[int(13.0 / hop)] == -10.0 and slow[int(16.5 / hop)] == -10.0
    assert fast[int(27.0 / hop)] == -P["end_hole_db"] and notes["holes"][-1]["before"] == "last hit"


def test_a_voice_with_no_word_times_says_where_it_speaks():
    sr = MU.SR
    x = np.zeros(int(6.0 * sr), np.float32)
    rng = np.random.default_rng(4)
    for a, b in ((0.5, 1.9), (2.6, 4.0), (4.05, 5.2)):                    # 4.0-4.05 is inside a sentence
        x[int(a * sr):int(b * sr)] = rng.standard_normal(int(b * sr) - int(a * sr)).astype(np.float32) * 0.1
    spans = MU.voice_spans(x)
    assert len(spans) == 2 and abs(spans[0][0] - 0.5) < 0.05 and abs(spans[0][1] - 1.9) < 0.05 and abs(spans[1][0] - 2.6) < 0.05 and abs(spans[1][1] - 5.2) < 0.05
    assert MU.voice_spans(np.zeros(sr, np.float32)) == []


def test_a_cut_moves_to_the_beat_only_where_nobody_speaks():
    plan = {"grid": [{"t": 0.5 * k, "down": k % 4 == 0, "bar": k // 4} for k in range(40)]}
    marks = {"words": [(4.0, 6.0)]}
    cuts, moved = MU.fit_cuts([31, 58, 150, 400], plan, marks, 30.0)       # 1.03 s, 1.93 s, 5.0 s (under a word), 13.33 s (no beat near)
    assert cuts[0] == 30 and cuts[1] == 60 and cuts[2] == 150 and cuts[3] == 400
    assert [m["f"] for m in moved] == [31, 58]


def test_marks_come_from_the_compiled_film():
    l1 = {"fps": 30, "dur_f": 900, "spine": [{"f": [0, 900], "src": [0.0, 30.0]}], "items": [], "sound": [],
          "sound_design": {"placed": [{"t": 12.0, "part": "payoff"}]}}
    m = MU.marks_from_l1(l1, {"words": [{"t0": 1.0, "t1": 1.4}, {"t0": 27.0, "t1": 27.5}]}, prog_end_f=840)
    assert m["dur_s"] == 28.0 and m["turn_s"] == 12.0 and m["words"][-1] == (27.0, 27.5) and m["accents"] == [12.0]
    own = MU.marks_from_l1(dict(l1, music_marks={"turn_s": 9.0, "payoff_s": 14.0}), {"words": []}, prog_end_f=840)
    assert own["turn_s"] == 9.0 and own["payoff_s"] == 14.0             # the scene's own marks come before the sound design's payoff
    assert MU.marks_from_l1(l1, {"words": []}, prog_end_f=840, payoff_s=20.0)["turn_s"] is None


# ----------------------------------------------------------------- the owner's desk

@needs_ffmpeg
def test_one_typed_line_approves_a_list_and_nothing_else_does(tmp_path, monkeypatch):
    import sound_approval as AP
    monkeypatch.setenv("PATH", os.path.dirname(shutil.which(FF)) + os.pathsep + os.environ.get("PATH", ""))
    aud, lib = str(tmp_path / "aud"), str(tmp_path / "lib")
    os.makedirs(lib)
    json.dump([], open(os.path.join(lib, "manifest.json"), "w"))
    x = np.stack([_shape("click")[int(1.15 * SR):int(1.4 * SR)]] * 2, axis=1).astype(np.float32)
    raw = tmp_path / "c.f32"
    x.tofile(raw)
    wav = str(tmp_path / "c.wav")
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw), wav], check=True)
    cids = [AP.stage("rq", wav, source_url="https://example.org/a", licence="CC0", author="someone", audition_dir=aud) for _ in range(2)]
    picks = [{"cid": c, "role": "click", "use": "a jump cut"} for c in cids] + [{"cid": "c99", "role": "click", "use": "x"}]
    with pytest.raises(AP.ApprovalError):
        AP.approve_many("rq", picks, attest="yes", audition_dir=aud, lib_dir=lib)
    assert json.load(open(os.path.join(lib, "manifest.json"))) == []
    got = AP.approve_many("rq", picks, attest=AP.attestation("rq"), audition_dir=aud, lib_dir=lib)
    rows = json.load(open(os.path.join(lib, "manifest.json")))
    assert [g[1] for g in got[:2]] == ["click_1", "click_2"] and got[2][1].startswith("not approved")
    assert [r["id"] for r in rows] == ["click_1", "click_2"] and all(r["license"] == "CC0 1.0" for r in rows)


# ----------------------------------------------------------------- a placed sound is there

@needs_ffmpeg
def test_a_sound_the_voice_would_hide_comes_up_or_gives_its_place(tmp_path):
    """Under an unbroken voice every plain gesture that stays can be picked out of it (PRESENT_MIN over the voice in its
    own band at its hit), a quiet one is raised by PRESENT_LIFT at most, and a hidden one is dropped with the reason."""
    sr, dur, fps = SD.AR, 20.0, 30
    rng = np.random.default_rng(5)
    n = rng.standard_normal(int(dur * sr)).astype(np.float32)
    sp = np.fft.rfft(n)
    hz = np.fft.rfftfreq(len(n), 1 / sr)
    sp[(hz < 120) | (hz > 6000)] = 0                                      # a wide, loud voice with no breath in it
    voice = (np.fft.irfft(sp, len(n)) * 0.2).astype(np.float32)
    speech, _ = SD.speech_frames(voice, [(0.0, dur)])
    D = int(dur * fps)
    ev = [{"f": f, "kind": "picture", "size": 0.5} for f in range(90, D - 60, 60)]
    ctx = {"fps": fps, "dur_f": D, "existing": [], "speech": speech, "voice": voice, "voice_shift_db": SD.voice_shift(voice, speech),
           "words": [(0.0, dur)], "bed": True, "np10": 8.0}
    sounds, rep = SD.plan_sounds(ev, ctx)
    for p in rep["placed"]:
        assert p["part"] != "plain" or p["over_voice_db"] >= SD.PRESENT_MIN, p
    for s in sounds:
        assert s.get("lifted_db", 0.0) <= SD.PRESENT_LIFT + 1e-6
    hidden = [d for d in rep["dropped"] if "hide" in d["why"]]
    assert rep["placed"] or hidden                                        # every event was either heard or named as hidden


@needs_ffmpeg
def test_with_the_bed_known_a_buried_hit_is_raised_and_a_clear_one_is_left():
    fps, dur = 30, 6.0
    n = int(dur * SD.AR)
    voice = np.zeros(n, np.float32)
    voice[:SD.AR] = np.random.default_rng(1).standard_normal(SD.AR).astype(np.float32) * 0.05       # a word, then silence
    quiet_bed = np.random.default_rng(2).standard_normal(n).astype(np.float32) * 1e-4
    loud_bed = np.random.default_rng(3).standard_normal(n).astype(np.float32) * 0.2
    it = SD._item("x", "click_2", 120, fps, -14.0, {"kind": "picture"}, "plain", "hit")
    same = SD.present_over([it], voice, quiet_bed, fps)[0]
    assert same["db"] == it["db"] and "lifted_for_bed_db" not in same
    up = SD.present_over([it], voice, loud_bed, fps)[0]
    assert 0 < up["lifted_for_bed_db"] <= SD.PRESENT_LIFT and up["db"] <= SD.GAIN_MAX
    lead = dict(it, layer="lead")
    assert SD.present_over([lead], voice, loud_bed, fps)[0]["db"] == it["db"]


def test_the_named_turn_takes_the_heaviest_landing_whatever_the_film_has_used():
    """A workflow that names its story's turn gets the hit with its climb there, even when the hook already took the
    impact (a film whose hook spent it left the turn a sweep nobody heard, 13 Nov)."""
    desc = CH.describe_library(SD.library(), SD.SOUNDS, SD.pcm)
    lib = SD.library()
    free = lambda sid: True
    spent = {"impact": 3}
    plain, cls0 = CH.layers({"f": 990, "kind": "display", "room_s": 6.0}, "payoff", {"desc": desc, "tally": dict(spent), "free": free, "spoken": True})
    assert cls0 != "impact"                                               # the shares alone give it something lighter
    layers, cls = CH.layers({"f": 990, "kind": "display", "room_s": 6.0, "turn": True}, "payoff",
                            {"desc": desc, "tally": dict(spent), "free": free, "spoken": True})
    assert cls == "impact" and [w for _s, w in layers] == ["lead", "hit"]
    assert desc[layers[0][0]]["kind"] == "riser" and desc[layers[1][0]]["kind"] == "impact"
    tight, _ = CH.layers({"f": 990, "kind": "display", "room_s": 0.3, "turn": True}, "payoff", {"desc": desc, "tally": {}, "free": free, "spoken": True})
    assert [w for _s, w in tight] == ["hit"]                              # no room for the climb: the hit alone


@needs_ffmpeg
def test_a_pause_inside_a_words_span_has_nothing_to_mask():
    """A transcript can hold a word's span over a breath (a figure heard as digits shares one span between its words).
    A sound through that silence masks nothing: the check reads only frames that carry a voice."""
    sr, fps = SD.AR, 30.0
    rng = np.random.default_rng(7)
    n = rng.standard_normal(int(9.0 * sr)).astype(np.float32)
    sp = np.fft.rfft(n)
    hz = np.fft.rfftfreq(len(n), 1 / sr)
    sp[(hz < 200) | (hz > 4000)] = 0
    full = (np.fft.irfft(sp, len(n)) * 0.2).astype(np.float32)
    voice = full.copy()
    voice[int(1.2 * sr):int(4.8 * sr)] = 0.0                              # a long breath the transcript calls spoken
    speech, _ = SD.speech_frames(voice, [(0.0, 9.0)])
    sid = next(s for s, x in SD.library().items() if x.get("role") == "riser")
    it = SD._item("t.0", sid, int(3.6 * fps), fps, 0.0, {"kind": "display"}, "payoff", "lead")
    a, b = it["start_s"], it["start_s"] + float(it.get("dur_s") or 1.0)
    assert 1.2 <= a and b <= 4.8, (a, b)                                  # the whole sound lies in the breath
    assert SD._masking(it, SD.frames_db(voice)[0], speech, fps) == 0.0
    quiet = full.copy()
    quiet[int(1.2 * sr):int(4.8 * sr)] *= 0.1                             # the same seconds spoken softly: a voice is there, and is kept clear
    assert SD._masking(it, SD.frames_db(quiet)[0], speech, fps) > 0.0
