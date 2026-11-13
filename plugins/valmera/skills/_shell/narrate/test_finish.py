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
