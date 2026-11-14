"""The brand finish's end accent, measured on files (owner, 14 Nov: make people look at the mark at the close).

    /Users/masaoodi/Documents/Valmera/.valmera/venv-worker/bin/python -m pytest -q -o tmp_path_retention_policy=failed test_brand.py

1. The beat grid reads a click track's tempo and phase, and finds no beat in noise bursts or silence.
2. The sting lands on the beat nearest the reveal inside the reveal, else on the reveal.
3. On a finished film: the card is not digital silence, the sting lands within one frame of its beat (or of the reveal
   when the music has no clear beat), the card's loudness stays inside the programme's, its true peak is under -1 dBTP,
   and the watermark, the end card and the length checks still pass.
4. The marketing film keeps the plain finish; the others pass the film's own library sounds so the sting is fresh.
"""
import json
import os
import re
import subprocess
import sys
import wave
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import brand  # noqa: E402
import chain  # noqa: E402

SR = brand.SR
FPS = 30
PROG_F = 180                       # a 6 s programme


def clicks(dur, period, phase, sr=SR, level=0.5, seed=0):
    """A click track (a decaying 1.5 kHz blip on each beat) over a quiet bed of noise."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(dur * sr)) * 0.01
    n = int(0.04 * sr)
    blip = np.sin(2 * np.pi * 1500 * np.arange(n) / sr) * np.exp(-np.arange(n) / (0.008 * sr)) * level
    t = phase
    while t < dur:
        i = int(round(t * sr))
        x[i:i + n] += blip[: len(x) - i]
        t += period
    return x.astype(np.float32)


def bursts(dur, sr=SR, seed=1):
    """Irregular noise bursts, like syllables: onsets with no period."""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(dur * sr)) * 0.005
    t = 0.1
    while t < dur - 0.3:
        i, n = int(t * sr), int(rng.uniform(0.08, 0.25) * sr)
        x[i:i + n] += rng.standard_normal(n) * rng.uniform(0.05, 0.3) * np.hanning(n)
        t += rng.uniform(0.12, 0.7)
    return x.astype(np.float32)


def test_beat_grid_reads_a_click_track():
    g = brand.beat_grid(clicks(8.0, 0.5, 0.2))
    assert g, "a clear 120 BPM click track must have a grid"
    assert abs(g["period_s"] - 0.5) <= 0.005
    d = (g["phase_s"] - 0.2) % g["period_s"]
    assert min(d, g["period_s"] - d) <= 0.012
    g = brand.beat_grid(clicks(8.0, 60 / 97.0, 0.33))
    assert g and abs(g["period_s"] - 60 / 97.0) <= 0.006


def test_no_beat_in_bursts_or_silence():
    # irregular syllable-like bursts: a grid fits a few by chance (a miss costs nothing: the sting then lands on the
    # reveal, and a false beat still lies inside the reveal), but never most
    false = sum(brand.beat_grid(bursts(d, seed=s)) is not None for s in range(20) for d in (6.0, 8.0))
    assert false <= 4, false
    assert brand.beat_grid(bursts(6.0, seed=0)) is None
    assert brand.beat_grid(np.zeros(8 * SR, np.float32)) is None


def test_sting_time_takes_the_beat_inside_the_reveal_else_the_reveal():
    prog_s, j, rk, sk = PROG_F / FPS, 9, 3, 11
    t_rev = (PROG_F - j + rk) / FPS
    on_beat = brand.sting_time(prog_s, FPS, j, rk, sk, dict(period_s=0.5, abs_phase_s=t_rev + 0.1 - 5.0))
    assert on_beat[1] == "beat" and abs(on_beat[0] - (t_rev + 0.1)) < 1e-6
    # the nearest beat falls before the reveal by more than a frame and the next after the settled card: the reveal
    off = brand.sting_time(prog_s, FPS, j, rk, sk, dict(period_s=0.5, abs_phase_s=t_rev - 0.2))
    assert off[1] == "reveal" and abs(off[0] - t_rev) < 1e-6
    assert brand.sting_time(prog_s, FPS, j, rk, sk, None)[1:] == ("reveal", t_rev)


def make_programme(path, audio):
    """A 540x960 30 fps programme of moving test pattern with the given mono audio."""
    wav = str(path) + ".wav"
    with wave.open(wav, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    subprocess.run([brand.FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=540x960:r={FPS}", "-i", wav,
                    "-frames:v", str(PROG_F), "-af", f"atrim=end_sample={PROG_F * SR // FPS}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", str(path)], check=True)
    return str(path)


def loudness(path):
    """ebur128 per 100 ms: (t, momentary, short-term) rows."""
    r = subprocess.run([brand.FF, "-v", "verbose", "-i", path, "-af", "ebur128=framelog=verbose", "-f", "null", "-"],
                       capture_output=True, text=True)
    rows = []
    for line in r.stderr.splitlines():
        m = re.search(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(\S+)\s+S:\s*(\S+)", line)
        if m:
            rows.append(tuple(float(v) if v not in ("-inf", "nan") else -120.0 for v in m.groups()))
    return np.array(rows)


def true_peak(path, start):
    r = subprocess.run([brand.FF, "-v", "info", "-ss", f"{start:.3f}", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float([ln for ln in r.stderr.splitlines() if "Peak:" in ln][-1].split()[1])


def sting_hit(final, sting_file, hit_in_file, near):
    """Where the sting's hit sits in the final: the recording found by cross-correlating its first 200 ms with the
    final's audio, plus where its hit lies in the recording."""
    y = brand._pcm(sting_file).mean(axis=1)
    k = y[: int(0.2 * SR)]
    s0 = int((near - 0.4) * SR)
    z = brand._pcm(final, s0, s0 + int(0.8 * SR) + len(k)).mean(axis=1)
    c = np.correlate(z, k, mode="valid")
    lag = int(np.argmax(np.abs(c)))
    return (s0 + lag) / SR + hit_in_file


def finish_and_measure(tmp_path, audio):
    prog = make_programme(tmp_path / "prog.mp4", audio)
    final = str(tmp_path / "final.mp4")
    info = brand.finish(prog, final, str(tmp_path))
    acc = info["accent"]
    chk = brand.check(prog, final, str(tmp_path))
    assert chk["length_ok"] and chk["mark_ok"] and chk["endcard_ok"], chk
    prog_s = PROG_F / FPS
    # the card is not digital silence: every 100 ms of it sounds, up to its own far-edge fade
    card = brand._pcm(final, int(prog_s * SR), int((prog_s + 5.0 - 0.4) * SR))
    rms = [float(np.sqrt((card[i:i + SR // 10] ** 2).mean())) for i in range(0, len(card) - SR // 10, SR // 10)]
    assert min(rms) > 10 ** (-70 / 20), f"a silent stretch on the card: {20 * np.log10(min(rms) + 1e-12):.1f} dBFS"
    # the sting lands within one frame of its target
    st = os.path.join(brand.worker_dir(), "sound_library", acc["sting"]["file"])
    got = sting_hit(final, st, acc["sting"]["hit_in_file_s"], acc["sting_s"])
    assert abs(got - acc["sting_s"]) <= 1.0 / FPS, (got, acc["sting_s"])
    # loudness: the card's short-term loudness inside the programme's range, true peak under -1 dBTP
    L = loudness(final)
    t, S = L[:, 0], L[:, 2]
    ps = S[(t >= 3.0) & (t <= prog_s) & (S > -70)]
    cs = S[(t > prog_s + 0.1) & (t <= prog_s + 3.0)]
    assert cs.max() <= ps.max() + 1.0 and cs.max() >= np.percentile(ps, 10) - 1.0, (cs.max(), ps.min(), ps.max())
    assert true_peak(final, prog_s - 1.0) <= -1.0
    return acc, got


def test_finish_lands_the_sting_on_the_beat(tmp_path):
    # 120 BPM; the card's type appears at frame 174 (5.8 s); a beat at 5.9333 s sits inside the reveal
    acc, got = finish_and_measure(tmp_path, clicks(PROG_F / FPS, 0.5, 5.9333 - 11 * 0.5))
    assert acc["on"] == "beat", acc
    assert abs(acc["sting_s"] - 5.9333) <= 1.0 / FPS
    assert abs(got - 5.9333) <= 1.0 / FPS


def test_finish_lands_the_sting_on_the_reveal_without_a_beat(tmp_path):
    acc, got = finish_and_measure(tmp_path, bursts(PROG_F / FPS, seed=0))
    assert acc["on"] == "reveal", acc
    assert abs(got - acc["reveal_s"]) <= 1.0 / FPS


def test_the_accent_is_deterministic(tmp_path):
    prog = make_programme(tmp_path / "prog.mp4", bursts(PROG_F / FPS))
    a = brand.accent_tail(prog, str(tmp_path), PROG_F / FPS, FPS, 9, 5.0)
    one = open(a[0], "rb").read()
    b = brand.accent_tail(prog, str(tmp_path), PROG_F / FPS, FPS, 9, 5.0)
    assert open(b[0], "rb").read() == one and a[1] == b[1]


def test_marketing_keeps_the_plain_finish_and_others_pass_their_sounds(tmp_path):
    l1 = tmp_path / "l1.json"
    l1.write_text(json.dumps({"sound": [{"file": "impact_10.flac"}, {"file": "click_3.flac"}, {"kind": "x"}]}))
    assert chain.end_accent(SimpleNamespace(workflow="wf2"), {"l1": str(l1)}) == {"accent": False}
    got = chain.end_accent(SimpleNamespace(workflow="wf1"), {"l1": str(l1)})
    assert got == {"accent": True, "used": ["impact_10.flac", "click_3.flac"]}
    assert chain.end_accent(SimpleNamespace(workflow="wf3"), {}) == {"accent": True, "used": []}
    first = brand.pick_sting()
    assert first and brand.pick_sting([first["path"]])["id"] != first["id"]
