"""faster-whisper transcription -> word list + sentence grouping.

Words are the atomic unit ({w, t0, t1}); sentences are speaker-agnostic
groups split on terminal punctuation, pauses, or hard length/duration caps
so the transcript panel can never show a run-on line. The agent is told to
snap every cut to these word boundaries.
"""

import inspect
import re

import config
from schemas import Word, Sentence

_model = None
_supports_hotwords = None

SENTENCE_END = re.compile(r"[.!?…]['\")\]]*$")
MAX_SENTENCE_WORDS = 12
MAX_SENTENCE_S = 6.0
SENTENCE_GAP_S = 0.6


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
    global _supports_hotwords
    model = get_model()
    # speech_pad_ms keeps VAD from shaving word tails (especially the very
    # last words of the clip); 500ms min-silence keeps short gaps inside
    # sentences from being dropped as non-speech.
    kwargs = dict(
        word_timestamps=True, vad_filter=True,
        beam_size=config.WHISPER_BEAM_SIZE,
        # A temperature-fallback ladder + these thresholds are whisper's own
        # anti-hallucination guard: if a window decodes with a bad avg-logprob
        # or a runaway compression ratio (the classic looping/garbage output),
        # it retries hotter instead of emitting confident nonsense.
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=2.4,
        log_prob_threshold=-1.0,
        no_speech_threshold=0.6,
        # Each window decoded independently — one mis-heard proper noun can't
        # snowball into the next window's context.
        condition_on_previous_text=False,
        vad_parameters={"min_silence_duration_ms": 500,
                        "speech_pad_ms": 400})
    if config.WHISPER_INITIAL_PROMPT.strip():
        kwargs["initial_prompt"] = config.WHISPER_INITIAL_PROMPT.strip()
    # 'hotwords' biases every window toward domain vocab (brand names). Guarded
    # by a signature check so an older faster-whisper can't crash the index.
    if _supports_hotwords is None:
        _supports_hotwords = "hotwords" in inspect.signature(
            model.transcribe).parameters
    hot = config.WHISPER_HOTWORDS.strip()
    if hot and _supports_hotwords:
        kwargs["hotwords"] = hot
    segments, info = model.transcribe(wav_path, **kwargs)
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
    """[Word] -> [Sentence].

    Breaks on terminal punctuation, a pause > SENTENCE_GAP_S, or — hard caps
    that make run-ons impossible — when adding the next word would exceed
    MAX_SENTENCE_WORDS words or MAX_SENTENCE_S seconds.
    """
    sentences = []
    start_i = 0
    for i, w in enumerate(words):
        is_last = i == len(words) - 1
        punct_break = bool(SENTENCE_END.search(w.w))
        gap_break = (not is_last and
                     words[i + 1].t0 - w.t1 > SENTENCE_GAP_S)
        cap_break = (not is_last and (
            (i - start_i + 2) > MAX_SENTENCE_WORDS or
            (words[i + 1].t1 - words[start_i].t0) > MAX_SENTENCE_S))
        if punct_break or gap_break or cap_break or is_last:
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
