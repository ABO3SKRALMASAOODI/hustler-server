"""Lane J (13 Nov): the film's sound design (score/sound.py) and its master (scene/audio.py).

Signal only: synthetic uploads made with ffmpeg, the owner's own library, no model."""
import json
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from score import sound as SD  # noqa: E402

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")
pytestmark = pytest.mark.skipif(shutil.which(FF) is None, reason="needs ffmpeg")
FPS = 30


def _upload(path, dur, talk=None):
    """A stereo upload: a 'voice' (band-limited noise, gated on and off) where talk says, room tone elsewhere."""
    sr = 48000
    t = np.arange(int(dur * sr)) / sr
    rng = np.random.default_rng(7)
    x = rng.standard_normal(len(t)).astype(np.float32) * 0.002
    for a, b in talk or []:
        i0, i1 = int(a * sr), int(b * sr)
        n = rng.standard_normal(i1 - i0).astype(np.float32)
        sp = np.fft.rfft(n)
        hz = np.fft.rfftfreq(len(n), 1 / sr)
        sp[(hz < 200) | (hz > 3500)] = 0
        v = np.fft.irfft(sp, len(n)).astype(np.float32)
        v *= (0.6 + 0.4 * np.sin(2 * np.pi * 4 * t[i0:i1]))      # syllables
        x[i0:i1] += 0.25 * v / (np.abs(v).max() + 1e-9)
    raw = path + ".f32"
    np.stack([x, x], 1).astype(np.float32).tofile(raw)
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "2", "-i", raw, "-c:a", "pcm_s16le", path], check=True)
    os.remove(raw)


def _film(dur_s, items, sound=(), spine_src=None):
    D = int(dur_s * FPS)
    return {"l1": 1, "sid": "t", "seed": 1, "fps": FPS, "frame": [1080, 1920], "dur_f": D,
            "assets": {"main": {"kind": "video", "path": "source.wav"}, "bin:0": {"kind": "image", "path": "a.jpg"},
                       "bin:1": {"kind": "video", "path": "b.mp4"}},
            "spine": [{"f": [0, D], "src": [0.0, dur_s]}] if spine_src is None else spine_src,
            "voices": {}, "film": {}, "items": items, "sound": list(sound)}


def _shot(i, a, z, pic="main", src=None):
    it = {"id": i, "k": "shot", "f": [a, z], "pic": pic}
    if pic == "main":
        it["src"] = list(src or [a / FPS, z / FPS])
    return it


def _work(tmp_path, dur, talk, words=None):
    w = tmp_path / "work"
    w.mkdir(exist_ok=True)
    _upload(str(w / "source.wav"), dur, talk)
    idx = {"video": {"duration": dur}, "shots": [], "words": words if words is not None else
           [{"w": "x", "t0": a + k * 0.3, "t1": a + k * 0.3 + 0.25} for a, b in talk for k in range(int((b - a) / 0.3))]}
    json.dump(idx, open(w / "index.json", "w"))
    return str(w)


def test_every_peak_lands_13ms_before_its_event_and_validates(tmp_path):
    work = _work(tmp_path, 20.0, [(0.5, 4.0), (6.0, 9.5), (12.0, 18.0)])
    items = [_shot("s0", 0, 150), _shot("s1", 150, 210, "bin:1"), _shot("s2", 210, 400, src=[7.0, 13.3]),
             _shot("s3", 400, 460, "bin:0"), _shot("s4", 460, 600, src=[15.3, 20.0]),
             {"id": "b05.big.0", "k": "word", "f": [520, 580], "t": "SO", "v": "a", "role": "big", "on": 520}]
    l1 = SD.apply(_film(20.0, items), work=work)
    rep = l1["sound_design"]
    assert "fault" not in rep, rep
    lib = SD.library()
    assert l1["sound"], rep
    for s in l1["sound"]:
        assert s["s"] in lib and s["file"] == lib[s["s"]]["file"]               # only the owner's library
        assert abs(s["peak_s"] - float(lib[s["s"]].get("hit_s") or lib[s["s"]]["peak_s"]) - SD.LEAD_S) < 1e-6
        if lib[s["s"]].get("max_s"):
            assert abs(s["dur_s"] - (lib[s["s"]]["max_s"] - s["trim_s"])) < 1e-3
        assert abs(s["start_s"] - max(0.0, s["event_f"] / FPS - s["peak_s"])) < 1e-3
        assert s["event_f"] in (150, 210, 400, 460, 520)                         # every sound answers a picture event
        assert SD.GAIN_MIN <= s["db"] <= SD.GAIN_MAX
    from score import compiler
    assert not [b for b in compiler.validate_l1(l1) if "start_s" in b]


def test_density_follows_the_supply_and_the_10s_window(tmp_path):
    """A talking take with display words everywhere: reference density at meaningful events, not one per word."""
    work = _work(tmp_path, 20.0, [(0.3, 19.5)])
    items = [_shot("s0", 0, 600)] + [{"id": f"b{k:02d}.big.0", "k": "word", "f": [k * 60 + 5, k * 60 + 40], "t": "W",
                                      "v": "a", "role": "big", "on": k * 60 + 5} for k in range(10)]
    sounds, rep = SD.design(_film(20.0, items), work=work)
    assert rep["class"] == "T"
    times = sorted(s["event_f"] / FPS for s in sounds)
    gestures = SD._gestures(times)
    assert 1 <= len(gestures) <= rep["allow"] <= 8, rep
    for w in np.arange(0, 20, 0.5):
        assert sum(1 for g in gestures if w <= g < w + 10) <= rep["allow_10s"]


def test_no_sound_on_frame_zero_without_a_designed_event_and_none_in_the_last_breath(tmp_path):
    work = _work(tmp_path, 10.0, [])
    items = [_shot("s0", 0, 100), _shot("s1", 100, 200, "bin:1"), _shot("s2", 200, 295, "bin:0"), _shot("s3", 295, 300, "bin:1")]
    sounds, rep = SD.design(_film(10.0, items), work=work)
    assert all(s["event_f"] > 1 for s in sounds)
    assert all(s["event_f"] < 300 - SD.END_GUARD_S * FPS for s in sounds)


def test_one_recording_twice_in_30s_never_inside_3s():
    uses = [(1.0, "whoosh_soft_2"), (5.0, "whoosh_soft_2")]
    assert SD._free("whoosh_soft_2", 9.0, uses) == "whoosh_soft_1"
    assert SD._free("whoosh_soft_2", 40.0, uses) == "whoosh_soft_2"
    assert SD._free("impact_1", 2.0, [(0.5, "impact_1")]) is None


def test_a_sound_that_masks_the_voice_comes_down_or_goes(tmp_path):
    """A ding laid over the voice on spoken frames (its tone sits in the speech band) is lowered to its floor or dropped."""
    work = _work(tmp_path, 6.0, [(0.0, 6.0)])
    l1 = _film(6.0, [_shot("s0", 0, 90), _shot("s1", 90, 180, "bin:1")])
    voice = SD.spine_audio(l1["spine"], FPS, os.path.join(work, "source.wav"), int(6.0 * SD.AR))[:, 0]
    speech, _ = SD.speech_frames(voice, [(0.0, 6.0)])
    it = SD._item("x", "ding_1", 90, FPS, -2.0, {"kind": "picture"}, "plain", "hit")
    ctx = {"fps": FPS, "voice": voice, "speech": speech, "voice_shift_db": 0.0}
    out, rep = SD._signal_check([{"t": 3.0, "ev": {"kind": "picture"}, "part": "plain", "items": [it], "ref": SD.VOICE_LUFS}], ctx,
                                {"placed": [], "dropped": []})
    assert (out and out[0]["db"] < -2.0) or rep["dropped"], (out, rep)


def test_an_ask_on_the_sound_leaves_the_directors_lines_alone(tmp_path):
    work = _work(tmp_path, 8.0, [(0.5, 7.5)])
    l1 = _film(8.0, [_shot("s0", 0, 120), _shot("s1", 120, 240, "bin:1")])
    sounds, rep = SD.design(l1, work=work, asks={"a1": {"id": "a1", "probe": "sfx_count", "min": 0}})
    assert rep.get("asked") and sounds == []


def test_the_seam_never_raises_and_can_be_turned_off(tmp_path, monkeypatch):
    bad = {"fps": 30, "dur_f": 10, "items": None, "sound": [{"id": "a"}], "spine": "x", "assets": {"main": {"path": "/nope"}}}
    out = SD.apply(dict(bad), work=str(tmp_path))
    assert out["sound"] == [{"id": "a"}]
    monkeypatch.setenv("VALMERA_SOUND_DESIGN", "0")
    assert "sound_design" not in SD.apply(dict(bad), work=str(tmp_path))


def test_no_upload_to_listen_to_adds_nothing():
    l1 = _film(5.0, [_shot("s0", 0, 75), _shot("s1", 75, 150, "bin:1")])
    sounds, rep = SD.design(l1, work=None)
    assert sounds == [] and rep["no_signal"]


def test_master_is_minus_14_lufs_under_minus_1_dbtp(tmp_path):
    sr, dur = SD.SR, 8.0
    p = str(tmp_path / "v.wav")
    _upload(p, dur, [(0.2, 3.5), (4.0, 7.8)])
    voice = SD.pcm(p, sr, 2)
    fx = np.zeros_like(voice)
    hit = SD.pcm(os.path.join(SD.SOUNDS, SD.library()["impact_1"]["file"]), sr, 2)
    at = int(3.7 * sr)
    fx[at:at + len(hit)] += hit[:len(fx) - at] * 10 ** (-6 / 20)
    out = str(tmp_path / "m.m4a")
    from scene import audio as AU
    rep = AU.master(voice, fx, out, str(tmp_path), dur)
    assert abs(rep["lufs"] - AU.TARGET_LUFS) <= 0.3, rep
    assert rep["tp"] <= AU.CEIL_DBTP + 0.05, rep
    assert rep["curve"] is True
