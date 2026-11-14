"""The finish (pauses, stress ride, mastering chain) and the OpenAI engine's pure parts, on synthetic reads.

  pytest -q test_finish.py        (venv-narrate has no pytest:
  $PY -c "import test_finish as T; [getattr(T, n)() for n in dir(T) if n.startswith('test_')]")
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import master as M  # noqa: E402
import narrate as N  # noqa: E402
import openai_voice as OV  # noqa: E402
import pauses as PZ  # noqa: E402

SR = 24000


def voice(dur, f0=120.0, seed=0):
    """A voiced, syllable-modulated harmonic signal standing in for speech."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    f = f0 * 2 ** (2 * np.sin(2 * np.pi * 0.7 * t) / 12)
    ph = 2 * np.pi * np.cumsum(f) / SR
    x = sum(np.sin(k * ph) / k for k in range(1, 30))
    x += 0.05 * rng.standard_normal(len(t))
    env = 0.6 + 0.4 * np.sin(2 * np.pi * 4.5 * t) ** 2
    return (0.2 * x * env).astype(np.float32)


def read(parts):
    """parts: [(speech seconds, silence seconds after)] -> signal and the word spans."""
    x, words, t = [], [], 0.0
    for i, (d, gap) in enumerate(parts):
        x.append(voice(d, seed=i))
        words.append({"w": f"w{i}", "start": t, "end": t + d})
        t += d
        if gap:
            x.append(np.zeros(int(gap * SR), np.float32))
            t += gap
    return np.concatenate(x), words


def test_master_hits_loudness_and_peak():
    x, _ = read([(3.0, 0.3), (3.0, 0.2), (3.0, 0)])
    y, rep = M.master(x, SR, band="03")
    assert abs(M.lufs(y, M.SR) - M.LUFS) < 0.6
    from scipy.signal import resample_poly
    assert np.abs(resample_poly(y, 4, 1)).max() <= 10 ** (-1.0 / 20) * 1.02
    assert set(rep) >= {"compressor", "leveler", "presence", "deess", "out", "tonal_eq_db"}


def test_ride_gain_is_solved_and_bounded():
    x, words = read([(0.4, 0.0)] * 6 + [(0.4, 0.4)] + [(0.4, 0.0)] * 6)
    words = [dict(w, w=str(i)) for i, w in enumerate(words)]
    y, rep = M.master(x, SR, ride=[(words[2]["start"], words[2]["end"], 0)], words=words)
    assert 0 <= rep["ride"]["gain_db"] <= M.RIDE_DB


def test_pauses_shorten_inside_a_segment_and_set_segment_ends():
    # words: "a b." | "c d" with a 0.9 s gap after "b." (a sentence end inside segment 1) and a
    # 0.8 s gap after "d" that ends segment 1; segment 2 "e f"
    x, words = read([(0.5, 0.0), (0.5, 0.9), (0.5, 0.0), (0.5, 0.8), (0.5, 0.0), (0.5, 0)])
    names = ["a", "b.", "c", "d.", "e", "f."]
    words = [dict(w, w=n) for w, n in zip(words, names)]
    segs = [{"text": "a b. c d.", "pause_after": 0.6}, {"text": "e f.", "pause_after": 0.0}]
    y, edits = PZ.shape(x, SR, segs, words, N, "03")
    kinds = {e["kind"]: e for e in edits if "skipped" not in e}
    assert kinds["sentence"]["want"] <= PZ.CAP["03"]["sentence"]
    assert len(y) < len(x) - int(0.5 * SR)


def test_openai_chunks_and_capitals():
    s = {"segments": [{"text": " ".join(["w"] * 25)}, {"text": " ".join(["w"] * 30)}, {"text": " ".join(["w"] * 20)}]}
    assert OV.chunks(s, 60) == [[0, 1], [2]]
    assert OV.styled({"text": "And he won twenty-seven thousand.", "emphasis": ["twenty-seven"]}) == "And he won TWENTY-SEVEN thousand."
    assert OV.styled({"text": "They told us so.", "emphasis": ["us"]}) == "They told us so."      # read as letters
    ins = OV.instructions("Voice: x.", [{"text": "a b c", "melody": "falling", "emphasis": ["b"], "direction": "dry"}], {"pace": "brisk"})
    assert "falling cadence" in ins and '"b"' in ins and "dry" in ins and "200 words" in ins


def test_pause_targets_the_heard_gap():
    """A join with soft edges (a decaying tail above the floor) is shortened so the gap between the words, as a
    listener times them, is the band's sentence gap, not the silence alone (14 Nov, lane VO2)."""
    tail = (voice(0.05, seed=9) * 0.03).astype(np.float32)          # a breath / decay above the floor, each side
    a, b = voice(1.2, seed=1), voice(1.2, seed=2)
    x = np.concatenate([a, tail, np.zeros(int(0.40 * SR), np.float32), tail, b])
    words = [{"w": "one.", "start": 0.0, "end": 1.2}, {"w": "two", "start": 1.2 + 0.05 + 0.40 + 0.05, "end": 3.0}]
    segs = [{"text": "one. two", "pause_after": 0.0}]
    y, edits = PZ.shape(x, SR, segs, words, N, "03")
    assert edits, "the long join was not edited"
    e = edits[0]
    heard_gap = e["want"] + (words[1]["start"] - words[0]["end"] - e["had"])
    assert abs(heard_gap - PZ.CAP["03"]["sentence"]) <= 0.03, (e, heard_gap)


def test_splice_puts_a_real_voice_after_its_line(tmp_path=None):
    import json
    import tempfile

    try:
        import soundfile as sf
    except ImportError:   # the narrate venv has it; a test venv without it has nothing to splice
        return

    import realvoice as RV
    d = Path(tmp_path or tempfile.mkdtemp())
    nd, fd = d / "narration", d / "footage"
    nd.mkdir(parents=True, exist_ok=True)
    (fd / "voices").mkdir(parents=True, exist_ok=True)
    x, words = read([(1.0, 0.5), (1.0, 0.5), (1.0, 0.0)])
    for k, w in enumerate(words):
        w["seg"] = k
    sf.write(nd / "narration.wav", x, SR)
    json.dump({"voice": "atlas", "duration": len(x) / SR, "words": words, "phrases": [], "cut_points": []}, open(nd / "words.json", "w"))
    sf.write(fd / "voices" / "v.wav", voice(2.0, f0=160, seed=5) * 3.0, SR)
    v = dict(after=0, person="Fred Smith", wav="voices/v.wav", file=None, said="I was willing",
             words=[{"w": "I", "start": 0.1, "end": 0.3}, {"w": "was", "start": 0.4, "end": 0.7}, {"w": "willing", "start": 0.8, "end": 1.9}])
    placed = RV.splice(nd, [v, dict(v, after=2)], fd)          # after the last line: nothing to cut into
    assert len(placed) == 1 and placed[0]["after"] == 0
    wj = json.load(open(nd / "words.json"))
    y, _ = sf.read(nd / "narration.wav")
    assert abs(len(y) / SR - (len(x) / SR + 2.0 + max(RV.GAP_BEFORE - 0.25, 0) + max(RV.GAP_AFTER - 0.25, 0))) < 0.02
    heard = [w for w in wj["words"] if w.get("voice")]
    assert [w["w"] for w in heard] == ["I", "was", "willing"] and all(w["seg"] == 0 for w in heard)
    w1 = next(w for w in wj["words"] if w["w"] == "w1")
    assert w1["start"] > heard[-1]["end"] + 0.3                 # the narrator comes back after a breath
    assert (nd / "narration_narrator.wav").exists() and wj["voices"][0]["person"] == "Fred Smith"
    # the cut-in sits at the narrator's speech level (the loud half of its frames), not at the clip's own
    a = int(placed[0]["start"] * SR)
    assert abs(20 * np.log10(RV._speech_rms(y[a:a + int(1.9 * SR)].astype(np.float32), SR) / RV._speech_rms(x, SR)) + 1.0) < 1.5


def test_phonon_easton_is_the_default_voice():
    """the owner's choice of 14 Nov: Phonon Easton reads unless a caller names another engine; a VoxCPM2 voice id
    (atlas, sage...) never reaches Phonon, and the pinned revisions are there when the folder on disk is not"""
    import json
    import types

    import lines as LN
    assert N.DEFAULT_ENGINE == "phonon" and "phonon" in LN.ENGINES
    assert LN.phonon_voice(types.SimpleNamespace(voice="atlas"), {"voice": "sage"}) == "Easton"
    pin = json.loads((Path(__file__).resolve().parent / "phonon.json").read_text())
    assert pin["model"].startswith("hf://gradium/phonon-31m-en@") and pin["voices"] == ["Easton"]
    assert all(len(f["sha256"]) == 64 for f in pin["files"].values())


def test_gate_errors_land_on_their_lines():
    """a word the gate listener drops, or one it hears that was never said, is charged to its own line"""
    import lines as LN
    segs = [{"text": "Smith got on a plane to Las Vegas instead."}, {"text": "And he won twenty-seven thousand dollars."},
            {"text": "Enough to pay the fuel bill."}]
    full = " ".join(s["text"] for s in segs)
    assert LN.seg_errors(segs, full) == {"total": 0, "words": 22, "by_line": {}}
    r = LN.seg_errors(segs, full.replace(" thousand dollars.", " thousand."))
    assert r["total"] == 1 and set(r["by_line"]) == {1}
    r = LN.seg_errors(segs, full.replace("Enough", "uh Enough"))
    assert r["total"] == 1 and set(r["by_line"]) <= {1, 2}


def test_spoken_form_takes_a_currency_word_said_after_its_scale():
    """'$30 million' is said 'thirty million dollars': the sign's word lands after the scale word and is taken; a word
    the listener wrote in letters still never changes, and the model still cannot add a word of its own"""
    import spoken
    t, g = spoken.merge("the debt grew to nearly $30 million.", "the debt grew to nearly thirty million dollars.")
    assert t == "the debt grew to nearly thirty million dollars." and g["model_changes_rejected"] == 0
    t, _ = spoken.merge("he won 27 thousand.", "he won twenty-seven thousand dollars.")
    assert t == "he won twenty-seven thousand."          # no sign was heard: no dollars
    t, _ = spoken.merge("the debt grew to nearly $30 million.", "the debt grew to nearly thirty billion dollars.")
    assert "million" in t and "billion" not in t
    t, _ = spoken.merge("and he won.", "and he won big.")
    assert t == "and he won."


def test_a_take_loses_its_leading_tick_and_a_join_keeps_the_floor():
    """Phonon's 10 ms tick and the 100 ms of digital silence after it are cut before the first word; a line join is
    filled with the takes' own floor, never with digital silence; retakes slow down batch by batch"""
    import lines as LN
    v = voice(1.0)
    tick = np.full(int(0.01 * SR), 0.004, np.float32)
    x = np.concatenate([tick, np.zeros(int(0.1 * SR), np.float32), v, np.zeros(int(0.1 * SR), np.float32)])
    y = LN.trim_take(x, SR)
    assert len(y) < len(x) - int(0.07 * SR) and np.abs(y[: int(0.02 * SR)]).max() == 0.0   # no tick: only the pre-roll
    rng = np.random.default_rng(1)
    a = np.concatenate([v, 1e-3 * rng.standard_normal(int(0.2 * SR)).astype(np.float32)])
    b = np.concatenate([1e-3 * rng.standard_normal(int(0.2 * SR)).astype(np.float32), v])
    j, bounds, _ = LN.join_lines([a, b], [{"text": "one.", "pause_after": 0.4}, {"text": "two."}], SR)
    g = j[len(a): len(a) + int(0.4 * SR)]
    assert 0.5e-3 < float(np.sqrt((g ** 2).mean())) < 2e-3 and len(bounds) == 1
    assert LN.retake_dd(0) == 0.0 and LN.retake_dd(LN.TAKES + LN.INPROC_MORE) > 0 and LN.retake_dd(99) == LN.PHONON_RETAKE_DD[-1]


def test_a_compound_spelt_apart_is_the_same_word():
    import measure
    assert measure.wer("retrorockets struck a partially opened panel", "Retro rockets struck a partially open panel")["wer"] == round(1 / 6, 4)
    assert measure.wer("the retro rockets fired", "the retrorockets fired")["wer"] == 0.0
    assert measure.wer("a lot of mail", "alot of mail")["wer"] == 0.0
    assert measure.wer("a lot of mail", "a lot of mail")["wer"] == 0.0
    assert measure.wer("the base ball", "the baseball")["wer"] == 0.0
    assert measure.wer("night time", "night time")["wer"] == 0.0
    assert measure.wer("nighttime", "night time")["wer"] == 0.0
    assert measure.wer("night owl", "nightowl")["wer"] == 0.0
    assert measure.wer("night", "night owl")["wer"] == 1.0
