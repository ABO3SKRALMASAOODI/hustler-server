"""The render's memory (lane NARRATE-LEAK, 14 Nov): one listener in memory at a time, MLX's cache emptied after every gate
call, and the take pool holding signals, not audio. The listeners are stand-ins here; the real numbers are in
.valmera/week-1112/lanes/NARRATE-LEAK/fix.json.

  $PY -c "import test_memory as T; [getattr(T, n)() for n in dir(T) if n.startswith('test_')]"
"""
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lines as LN  # noqa: E402
import narrate as N  # noqa: E402

SR = 24000


class _Fakes:
    """mlx_whisper (with its ModelHolder), mlx.core and faster_whisper stand-ins, put back on exit"""
    NAMES = ("mlx", "mlx.core", "mlx_whisper", "mlx_whisper.transcribe", "faster_whisper")

    def __enter__(self):
        self.saved = {k: sys.modules.get(k) for k in self.NAMES}
        self.saved_cache = dict(N._asr_cache)
        N._asr_cache.clear()
        self.calls = []
        calls = self.calls

        class ModelHolder:
            model = None
            model_path = None

        def transcribe(audio, path_or_hf_repo=None, **kw):
            ModelHolder.model, ModelHolder.model_path = object(), path_or_hf_repo
            calls.append("gate")
            return {"text": " a b", "segments": [{"words": [{"word": " a", "start": 0.0, "end": 0.1},
                                                            {"word": " b", "start": 0.2, "end": 0.3}]}]}

        tr = types.ModuleType("mlx_whisper.transcribe")
        tr.ModelHolder = ModelHolder
        mw = types.ModuleType("mlx_whisper")
        mw.transcribe = transcribe
        core = types.ModuleType("mlx.core")
        core.set_cache_limit = lambda n: calls.append(("limit", n))
        core.clear_cache = lambda: calls.append("clear")
        mlx = types.ModuleType("mlx")
        mlx.core = core

        class W:
            def __init__(self, word, start, end):
                self.word, self.start, self.end, self.probability = word, start, end, 1.0

        class WhisperModel:
            def __init__(self, *a, **kw):
                calls.append("take_load")

            def transcribe(self, y, **kw):
                calls.append("take")
                return [types.SimpleNamespace(words=[W(" a", 0.0, 0.1)])], None

        fw = types.ModuleType("faster_whisper")
        fw.WhisperModel = WhisperModel
        sys.modules.update({"mlx": mlx, "mlx.core": core, "mlx_whisper": mw, "mlx_whisper.transcribe": tr,
                            "faster_whisper": fw})
        self.holder = ModelHolder
        return self

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        N._asr_cache.clear()
        N._asr_cache.update(self.saved_cache)


def test_the_two_listeners_are_never_in_memory_together():
    x = np.zeros(SR, np.float32)
    with _Fakes() as f:
        N.listen_fast(x, SR)
        assert N.listeners_loaded() == {"take": True, "gate": False}
        N.listen(x, SR)
        assert N.listeners_loaded() == {"take": False, "gate": True}
        N.listen(x, SR)                       # the gate's weights stay between gate calls
        assert f.calls.count("gate") == 2
        N.listen_fast(x, SR)                  # a take after the gate (gate_repair's new takes): the gate goes first
        assert N.listeners_loaded() == {"take": True, "gate": False}
        assert f.holder.model_path is None
        assert f.calls.count("take_load") == 2


def test_mlx_cache_is_capped_and_emptied_after_every_gate_call():
    x = np.zeros(SR, np.float32)
    with _Fakes() as f:
        for _ in range(3):
            N.listen(x, SR)
        gate_at = [i for i, c in enumerate(f.calls) if c == "gate"]
        assert len(gate_at) == 3
        for i in gate_at:                     # capped before the call, emptied right after it
            assert f.calls[i - 1] == ("limit", N.MLX_CACHE_MB << 20)
            assert f.calls[i + 1] == "clear"


def _tone(dur, f0, seed):
    rng = np.random.default_rng(seed)
    t = np.arange(int(dur * SR)) / SR
    x = 0.2 * np.sin(2 * np.pi * f0 * t) + 0.01 * rng.standard_normal(len(t))
    pad = np.zeros(int(0.3 * SR))
    return np.concatenate([pad, x, pad]).astype(np.float32)


def test_the_pool_keeps_signals_and_the_gate_reloads_a_takes_audio_from_disk():
    import soundfile as sf
    old_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for k, f0 in enumerate((150.0, 190.0)):
                sf.write(d / f"s00_t{k}.wav", _tone(1.2, f0, k), SR, subtype="FLOAT")
            judged = [LN.take_audio(d, {"file": f"s00_t{k}.wav"})[0] for k in range(2)]
            text = "alpha beta gamma"
            heard_by_gate = []

            def listen(x, sr):
                heard_by_gate.append(x)
                return (text if len(x) == len(judged[1]) and np.array_equal(x, judged[1]) else "alpha beta delta"), []

            rounds = []

            def listen_long(x, sr):
                rounds.append(len(x))
                return ("alpha beta delta" if len(rounds) == 1 else text), []

            fakeN = types.SimpleNamespace(listen=listen, listen_long=listen_long, log=lambda *a: None)
            pool = [LN.drop_audio([
                {"file": "s00_t0.wav", "errs": 0, "score": 60.0, "voice": (None, None), "_x": judged[0], "_sr": SR},
                {"file": "s00_t1.wav", "errs": 0, "score": 50.0, "voice": (None, None), "_x": judged[1], "_sr": SR}])]
            assert all("_x" not in t for t in pool[0])
            kept, kept_v, chosen, report = [judged[0]], [(None, None)], [dict(pool[0][0])], [{}]
            script = {"segments": [{"text": text}]}
            hist = LN.gate_repair(fakeN, "phonon", {"lines": [{"id": "s00", "text": text}]}, d, script, {}, [], SR,
                                  kept, kept_v, chosen, pool, report)
            assert [h["errors"] for h in hist] == [1, 0]
            assert report[0]["kept"] == "s00_t1.wav"
            assert np.array_equal(kept[0], judged[1])     # the audio heard and kept is the take as it was judged
            assert all("_x" not in t for t in pool[0])
    finally:
        if old_key is not None:
            os.environ["OPENAI_API_KEY"] = old_key
