"""The plan's own arithmetic: no engine, no media. Run: $PY -m pytest -q test_film.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import film as F  # noqa: E402
import supply as S  # noqa: E402
import wf2  # noqa: E402


def test_grid_holds_exactly_the_delivery():
    assert S.grid_for(135) == (15, 9)
    c, r = S.grid_for(104)
    assert c * r == 104
    assert S.grid_for(7) is None            # no sheet shape: a film may not pad or drop a short to make one


def test_curves_land_on_their_value():
    assert abs(F.expo_out(12)[-1] - 1.0) < 1e-9 and F.expo_out(12)[0] == 0.0
    assert F.settle(6)[-1] == 1.0
    assert F.ease_io(9)[0] == 0.0 and abs(F.ease_io(9)[-1] - 1.0) < 1e-9


def test_keyword_is_whole_cells():
    font = os.path.join(F.scene_worker(), "fonts", "InterDisplay-Black.ttf")
    runs = F.cell_runs("SHORTS", font, 216, 144, 960, 12)
    assert runs and all(x % 12 == 0 and y % 12 == 0 and n >= 1 for x, y, n in runs)
    rows = {y for _, y, _ in runs}
    assert 8 <= len(rows) <= 20             # tall enough to read in motion


def test_numbers_on_screen_come_from_the_evidence():
    facts = [135, "2 h 13 min", "Lex Fridman Podcast #422"]
    assert wf2.numbers_ok(["135 shorts", "LEX FRIDMAN #422 · 2 H 13 MIN"], facts) == []
    assert wf2.numbers_ok(["140 shorts"], facts) == ["140"]


def test_sound_lands_its_peak_on_the_event():
    lib = {"tick_1": dict(file="tick_1.flac", peak_s=0.02, max_db=-3.0)}
    s = F.sound("k", "tick_1", lib, 30, -27.0)
    assert abs(s["start_s"] + s["peak_s"] - 1.0) < 1e-6 and s["db"] == -24.0
    assert F.sound("k", "tick_1", lib, 0, -27.0)["trim_s"] == 0.02


def test_lead_in_skips_a_cut_off_syllable(tmp_path):
    import subprocess
    import numpy as np
    sr = 16000
    t = np.arange(int(1.6 * sr)) / sr
    x = np.zeros_like(t, dtype=np.float32)
    x[: int(0.28 * sr)] = 0.08 * np.sin(2 * np.pi * 220 * t[: int(0.28 * sr)])       # the tail of a word
    x[int(1.05 * sr):] = 0.3 * np.sin(2 * np.pi * 180 * t[int(1.05 * sr):])           # the line
    ff = os.path.join("/Users/masaoodi/Documents/Valmera/audit/subscriber-reliability-20260908/bin-arm", "ffmpeg")
    for name, sig in (("tail.wav", x), ("clean.wav", np.where(t < 0.5, 0, 0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32))):
        p = str(tmp_path / name)
        subprocess.run([ff, "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "-", p], input=sig.tobytes(), check=True)
    assert 0.3 < F.lead_in(ff, str(tmp_path / "tail.wav")) < 1.0
    assert F.lead_in(ff, str(tmp_path / "clean.wav")) == 0.0
