"""faster-whisper transcription -> word list + sentence grouping.

Words are the atomic unit ({w, t0, t1}); sentences are speaker-agnostic
groups split on terminal punctuation, long pauses, or length. The agent is
told to snap every cut to these word boundaries.
"""

import re

import config
from schemas import Word, Sentence

_model = None

SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")
MAX_SENTENCE_WORDS = 40
SENTENCE_GAP_S = 1.0


def get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.WHISPER_MODEL,
                              device=config.WHISPER_DEVICE,
                              compute_type=config.WHISPER_COMPUTE)
    return _model


def transcribe(wav_path):
    """Returns (words: [Word], language: str)."""
    model = get_model()
    segments, info = model.transcribe(
        wav_path, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            token = (w.word or "").strip()
            if not token:
                continue
            words.append(Word(w=token,
                              t0=round(float(w.start), 3),
                              t1=round(float(w.end), 3)))
    return words, getattr(info, "language", "en")


def group_sentences(words):
    """[Word] -> [Sentence]. Breaks on ., !, ?, …, a >1s pause, or 40 words."""
    sentences = []
    start_i = 0
    for i, w in enumerate(words):
        is_last = i == len(words) - 1
        gap_break = (not is_last and
                     words[i + 1].t0 - w.t1 > SENTENCE_GAP_S)
        punct_break = bool(SENTENCE_END.search(w.w))
        too_long = (i - start_i + 1) >= MAX_SENTENCE_WORDS
        if punct_break or gap_break or too_long or is_last:
            chunk = words[start_i:i + 1]
            sentences.append(Sentence(
                id=f"s{len(sentences) + 1}",
                text=" ".join(x.w for x in chunk),
                t0=chunk[0].t0,
                t1=chunk[-1].t1,
                wi0=start_i,
                wi1=i,
            ))
            start_i = i + 1
    return sentences
