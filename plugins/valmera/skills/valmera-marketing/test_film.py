"""The plan's own arithmetic: no engine, no media, no recording. Run: $PY -m pytest -q test_film.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import film as F  # noqa: E402
import wf2  # noqa: E402


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
    facts = ['{"export_ratio": "9:16", "pictures": 3, "upload_length": "0:28"}', "Cut 2 pauses."]
    assert wf2.numbers_ok(["9:16 · 3 pictures", "RAW · 0:28", "Cut 2 pauses."], facts) == []
    assert wf2.numbers_ok(["4 pictures"], facts) == ["4"]


def test_sound_lands_its_peak_on_the_event():
    lib = {"tick_1": dict(file="tick_1.flac", peak_s=0.02, max_db=-3.0)}
    s = F.sound("k", "tick_1", lib, 30, -27.0)
    assert abs(s["start_s"] + s["peak_s"] - 1.0) < 1e-6 and s["db"] == -24.0
    assert F.sound("k", "tick_1", lib, 0, -27.0)["trim_s"] == 0.02


def test_two_pictures_of_one_instant_follow_the_edl():
    """The upload beside its export shows the frame the export shows: across a trim the source jumps with it."""
    tl, dur = F.program(dict(keep=[[0.0, 4.0], [5.0, 9.0]]))
    assert abs(dur - 8.0) < 1e-6
    got = F.runs(tl, 3.0, 6.0)
    assert [(round(a, 3), round(b, 3), round(s, 3)) for a, b, s in got] == [(3.0, 4.0, 3.0), (4.0, 6.0, 5.0)]
    assert F.runs(tl, 0.5, 2.0) == [(0.5, 2.0, 0.5)]


def test_pieces_of_two_cuts_say_what_a_sentence_removed():
    before, after = [(0.5, 15.8), (16.1, 24.2)], [(9.5, 15.8), (16.1, 24.2)]
    p = F.edl_pieces(before, after)
    assert p[0] == (0.5, 9.5, True, False) and all(ia and ib for _, _, ia, ib in p[1:])
    assert abs(sum(e - s for s, e, ia, ib in p if ia and not ib) - 9.0) < 1e-6


def test_pictures_are_read_from_both_places_the_product_keeps_them():
    edl = dict(overlays=[dict(id="ov1", kind="image", start=2.2, duration_s=2.6)], fx=[dict(id="br1", type="broll", start=1.49, end=3.69)],
               motion=[dict(id="mg1", start=0.0, end=3.0)])
    assert [o["id"] for o in F.pictures_of(edl)] == ["br1", "ov1"]
    assert abs(F.pictures_of(edl)[0]["duration_s"] - 2.2) < 1e-6
    assert F.titles_of(edl) == [dict(id="mg1", start=0.0, duration_s=3.0)]


def test_a_pull_back_keeps_one_point_still_and_ends_on_the_seat():
    box = [0.44, 0.205, 0.86, 0.625]
    xs, ys, ss = F.pull(box, 20)
    assert abs(ss[-1] - 1.0) < 1e-4 and abs(xs[-1] - 0.65) < 1e-4 and abs(ys[-1] - 0.415) < 1e-4
    assert ss[0] > 1.5 and all(a >= b for a, b in zip(ss, ss[1:]))       # it only ever shrinks
    xi, yi, si = F.pull(box, 16, out=False)
    assert abs(si[-1] - 1 / 0.42) < 1e-3 and abs(xi[-1] - 0.5) < 1e-3 and abs(yi[-1] - 0.5) < 1e-3   # and grows back to the whole frame


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
