"""Transcription -> word list + sentence grouping.

Two providers behind one function: Deepgram nova-3 (hosted, better on the noisy
music-over-speech audio this product is full of) and faster-whisper (local CPU,
the default and the automatic fallback). See config.TRANSCRIBER.

Words are the atomic unit ({w, t0, t1}); sentences are speaker-agnostic
groups split on terminal punctuation, pauses, or hard length/duration caps
so the transcript panel can never show a run-on line. The agent is told to
snap every cut to these word boundaries.
"""

import gc
import inspect
import re
import time

import requests

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


def _hotword_terms():
    """The brand vocabulary as discrete terms (Deepgram 'keyterm'); whisper
    takes the same list as one raw 'hotwords' string."""
    return [t for t in (x.strip()
                        for x in re.split(r"[,\n]", config.WHISPER_HOTWORDS))
            if t]


DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_RETRIES = 2
_FALLBACK_PREFIX = "speech recognition fell back to the local model"
_SPARSE_PREFIX = "speech recognition re-ran on the local model"


def _wav_duration_s(wav_path):
    """Duration of the index wav from its header alone (16k mono PCM — a
    few bytes read, never the audio). 0.0 when unreadable, which disables
    the sparse check rather than failing the index."""
    try:
        import wave
        with wave.open(wav_path, "rb") as w:
            rate = w.getframerate() or 16000
            return w.getnframes() / float(rate)
    except Exception:
        return 0.0


def _too_sparse(words, dur):
    """Is this transcript impossibly thin for audio this long?

    Deepgram nova-3 supports a short list of languages, and detect_language
    picks the nearest member of THAT list — so Arabic speech comes back as
    "Portuguese" or "Turkish" with ZERO words, as a 2xx the caller reads as
    success (projects 295/296, Jul 31 2026: a man visibly talking for 130s,
    0 words, and the agent told the user their video had no speech). An
    HTTP success carrying no transcript for real-length audio is a failed
    transcription wearing a success code; whisper (which handles ~100
    languages, Arabic included) must get its turn. Thresholds are deliberately
    conservative — a genuinely silent video passes through whisper's VAD in
    seconds, so a false positive costs little, while a false negative costs a
    user their whole edit."""
    if not words:
        return dur >= 3.0
    if dur < 20.0:
        return False
    # Under ~3 words per minute of audio: nobody talks that slowly; this is
    # a mis-detected language emitting near-nothing (a real case emitted
    # exactly one hallucinated word for 27s of continuous speech).
    return len(words) < max(3.0, dur * 0.05)


def _parse_deepgram(payload):
    """Deepgram response -> ([Word], language). Raises on a shape we don't
    recognise rather than silently returning an empty transcript — an empty
    transcript is indistinguishable from 'this video has no speech', which is
    a claim we then make to the user's face."""
    results = payload.get("results") or {}
    channels = results.get("channels") or []
    if not channels:
        raise ValueError("deepgram response has no channels")
    ch = channels[0]
    alts = ch.get("alternatives") or []
    if not alts:
        raise ValueError("deepgram response has no alternatives")
    words = []
    for w in (alts[0].get("words") or []):
        # punctuated_word carries the terminal punctuation group_sentences
        # splits on; plain 'word' is stripped of it.
        token = str(w.get("punctuated_word") or w.get("word") or "").strip()
        if not token:
            continue
        try:
            t0, t1 = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("deepgram word is missing start/end timestamps")
        words.append(Word(w=token, t0=round(t0, 3), t1=round(t1, 3)))
    lang = ch.get("detected_language") or alts[0].get("language") or "en"
    return words, str(lang)


def _transcribe_deepgram(wav_path):
    params = {
        "model": config.DEEPGRAM_MODEL,
        # punctuation + capitalisation: group_sentences splits on terminal
        # punctuation, so an unpunctuated transcript would be one long run-on.
        "smart_format": "true",
        "punctuate": "true",
        "detect_language": "true",
    }
    terms = _hotword_terms()
    if terms:
        params["keyterm"] = terms      # requests repeats the key per term
    with open(wav_path, "rb") as f:
        audio = f.read()
    last = None
    for attempt in range(DEEPGRAM_RETRIES + 1):
        try:
            r = requests.post(
                DEEPGRAM_URL, params=params, data=audio,
                headers={"Authorization": f"Token {config.DEEPGRAM_API_KEY}",
                         "Content-Type": "audio/wav"},
                timeout=config.DEEPGRAM_TIMEOUT_S)
        except requests.RequestException as e:
            last = e
        else:
            if r.status_code < 300:
                return _parse_deepgram(r.json())
            # 4xx (bad key, bad audio) will fail identically forever — only a
            # 429/5xx is worth waiting on.
            if r.status_code < 500 and r.status_code != 429:
                raise ValueError(
                    f"deepgram {r.status_code}: {r.text[:160]}")
            last = ValueError(f"deepgram {r.status_code}: {r.text[:160]}")
        if attempt < DEEPGRAM_RETRIES:
            time.sleep(1.5 * 2 ** attempt)
    raise last


def _release_model():
    """Drop the cached whisper model and give the memory back.

    `_model` is a process-lifetime cache, which is right when whisper IS the
    transcriber (every index uses it) and actively harmful when it is only the
    fallback: 'medium' holds ~1.5GB for the life of the process to save a reload
    on a path that should almost never run. That resident 1.5GB is what
    OOM-killed the worker in prod — job 204 transcribed a 19-min video, then the
    SAME process ran the next index's proxy encode + a preview render + an agent
    turn, and the box died with no traceback (SIGKILL), taking a customer's
    video down with it.
    """
    global _model
    _model = None
    gc.collect()


def transcribe(wav_path, warnings=None):
    """Returns (words: [Word], language: str)."""
    if config.TRANSCRIBER == "deepgram":
        try:
            words, lang = _transcribe_deepgram(wav_path)
            dur = _wav_duration_s(wav_path)
            if not _too_sparse(words, dur):
                return words, lang
            # A 2xx with (near-)zero words on minutes of audio is not a
            # transcript, it is nova-3 meeting a language it does not
            # support. Whisper covers ~100; run it and keep whichever
            # engine actually heard the speech.
            print(f"[transcribe] deepgram returned {len(words)} word(s) for "
                  f"{dur:.0f}s of audio (detected '{lang}') — likely an "
                  "unsupported language; re-running on local whisper",
                  flush=True)
            try:
                w_words, w_lang = _transcribe_whisper(wav_path)
            except Exception as e:
                print(f"[transcribe] whisper retry failed ({str(e)[:160]}); "
                      "keeping the deepgram result", flush=True)
                return words, lang
            finally:
                _release_model()
            if len(w_words) > len(words):
                if warnings is not None and not any(
                        w.startswith(_SPARSE_PREFIX) for w in warnings):
                    warnings.append(
                        f"{_SPARSE_PREFIX}: the hosted engine heard almost "
                        f"nothing (detected '{lang}', {len(words)} words) but "
                        f"local whisper transcribed {len(w_words)} words "
                        f"(language '{w_lang}') — the local engine's "
                        "transcript is in use")
                return w_words, w_lang
            return words, lang
        except Exception as e:
            # A hosted ASR having a bad day must not fail the whole index — but
            # the user is then reading a transcript from the WEAKER engine, and
            # every cut and caption is snapped to it. Say so instead of passing
            # it off as the good one.
            print(f"[transcribe] deepgram failed ({str(e)[:160]}); "
                  "falling back to local whisper", flush=True)
            # The indexer retries transcribe() once, so guard against saying
            # this twice in one index.
            if warnings is not None and not any(
                    w.startswith(_FALLBACK_PREFIX) for w in warnings):
                warnings.append(
                    f"{_FALLBACK_PREFIX} ({str(e)[:100]}) — the transcript "
                    "may be less accurate than usual on noisy audio")
        # Fallback only: whisper is not this deployment's transcriber, so
        # holding the model resident afterwards buys nothing and can cost the
        # whole worker. See _release_model.
        try:
            return _transcribe_whisper(wav_path)
        finally:
            _release_model()
    return _transcribe_whisper(wav_path)


def _transcribe_whisper(wav_path):
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
        # (garbage) it retries hotter instead of emitting confident nonsense.
        # NOTE: compression_ratio_threshold is DISABLED by default (None). The
        # library's 2.4 reads "the speaker repeated themselves" as a looping
        # hallucination and collapses the repeats — and any replacement number
        # would just be a cap on how often a user may repeat a take, which is
        # unknowable for the raw footage this product exists to cut. See the
        # config note; VAD + no_speech + logprob still guard hallucination.
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        compression_ratio_threshold=config.WHISPER_COMPRESSION_RATIO_THRESHOLD,
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
