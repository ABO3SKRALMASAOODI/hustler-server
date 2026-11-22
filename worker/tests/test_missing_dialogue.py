from pathlib import Path
import sys
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcribe
from schemas import Word


def audio_with_missing_line(tmp_path):
    path = tmp_path / "speech.wav"
    audio = np.zeros(24 * 16000, dtype="<i2")
    audio[12 * 16000:14 * 16000] = 16000
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(audio.tobytes())
    return str(path)


def test_recover_voiced_gap_inside_an_accepted_hosted_index(tmp_path, monkeypatch):
    from faster_whisper import vad
    wav = audio_with_missing_line(tmp_path)
    known = [Word(w="First", t0=0, t1=1, speaker=2), Word(w="line", t0=1, t1=2), Word(w="Last", t0=20, t1=21, speaker=3)]
    assert not transcribe._too_sparse(known, 24)
    monkeypatch.setattr(transcribe.config, "TRANSCRIBER", "deepgram")
    monkeypatch.setattr(transcribe, "_transcribe_deepgram", lambda path: (known, "en"))

    def activity(samples, **kwargs):
        voiced = np.flatnonzero(samples)
        return [{"start": voiced[0], "end": voiced[-1]+1}] if len(voiced) else []

    monkeypatch.setattr(vad, "get_speech_timestamps", activity)
    calls = []

    def recognize(samples, **kwargs):
        calls.append(len(samples))
        i = int(np.flatnonzero(samples)[0])
        return [Word(w="Dialogue", t0=i / 16000, t1=i / 16000 + 1)], "en"

    monkeypatch.setattr(transcribe, "_transcribe_whisper", recognize)
    words, language = transcribe.transcribe(wav)
    assert [w.w for w in words] == ["First", "line", "Dialogue", "Last"]
    assert words[0] == known[0] and words[1] == known[1] and words[3] == known[2]
    assert (words[2].t0, words[2].t1, language) == (12, 13, "en")
    assert len(calls) == 1 and calls[0] < 4 * 16000


def test_secondary_failure_preserves_the_primary_words(tmp_path, monkeypatch):
    from faster_whisper import vad
    wav = audio_with_missing_line(tmp_path)
    primary = [Word(w="A", t0=0, t1=1), Word(w="a", t0=1, t1=2), Word(w="B", t0=20, t1=21)]
    monkeypatch.setattr(transcribe.config, "TRANSCRIBER", "deepgram")
    monkeypatch.setattr(transcribe, "_transcribe_deepgram", lambda _: (primary, "en"))
    monkeypatch.setattr(vad, "get_speech_timestamps", lambda x, **kw: [{"start": 0, "end": len(x)}])
    def unavailable(*args, **kwargs):
        raise RuntimeError("model unavailable")
    monkeypatch.setattr(transcribe, "_transcribe_whisper", unavailable)
    words, language = transcribe.transcribe(wav)
    assert words == primary and language == "en"


def test_local_transcriber_also_recovers_missing_speech(tmp_path, monkeypatch):
    from faster_whisper import vad
    wav = audio_with_missing_line(tmp_path)
    monkeypatch.setattr(transcribe.config, "TRANSCRIBER", "whisper")
    monkeypatch.setattr(vad, "get_speech_timestamps", lambda a, **kw: [{"start": 0, "end": len(a)}])
    known = Word(w="Original", t0=0, t1=10, speaker=0)

    def recognize(source, *, presegmented=False):
        if presegmented:
            return [Word(w="Recovered", t0=2, t1=3)], "en"
        return [known], "en"

    monkeypatch.setattr(transcribe, "_transcribe_whisper", recognize)
    words, _ = transcribe.transcribe(wav)
    assert words == [known, Word(w="Recovered", t0=12, t1=13)]
