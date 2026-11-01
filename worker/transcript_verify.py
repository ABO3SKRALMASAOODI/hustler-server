"""Independent speech recognition for uncertain words, before caption styling.

The second recognizer hears audio without the first transcript or suggested
answers. Only isolated lower-case substitutions with two matching words on
both sides can change the primary transcript. Timings, speakers, numbers,
proper names, fillers and sentence punctuation stay with the primary ASR.
This is transcription, separate from the disabled subjective audio-review lane.
"""
import collections
import concurrent.futures
import difflib
import os
import re
import tempfile
import threading
import wave
from urllib.parse import urlparse

import config
import llm
from caption_truth import _NUM, faithful_fix

MODEL = 'gpt-transcribe'
# https://developers.openai.com/api/docs/models/gpt-transcribe (2 Nov 2026)
USD_PER_MINUTE = .0045
WINDOW_S = 120
CONTEXT_S = 2
TOKEN = re.compile(r"\w+(?:['’]\w+)*", re.UNICODE)


def _get(word, key, default=None):
    return word.get(key, default) if isinstance(word, dict) else getattr(word, key, default)


def _tokens(text):
    return TOKEN.findall(str(text or ''))


def _candidate(word):
    raw = str(_get(word, 'w', ''))
    tokens = _tokens(raw)
    confidence = _get(word, 'confidence')
    return (len(tokens) == 1 and tokens[0].isalpha()
            and tokens[0].islower() and 3 <= len(tokens[0]) <= 24
            and tokens[0] not in _NUM
            and isinstance(confidence, (int, float)) and confidence < .98
            and not _get(word, 'filler', False))


def aligned_votes(words, transcript):
    """Candidate index -> independently heard token, including agreements.

    Missing/added words, rewritten phrases and unanchored edges never supply a
    replacement. Agreements matter when overlapping windows disagree.
    """
    primary = [_tokens(_get(w, 'w')) for w in words]
    owners = [i for i, tokens in enumerate(primary) for _ in tokens]
    original = [token.casefold() for tokens in primary for token in tokens]
    heard = _tokens(transcript)
    normalized = [token.casefold() for token in heard]
    ops = difflib.SequenceMatcher(None, original, normalized, autojunk=False).get_opcodes()
    votes = {}
    for n, (tag, a, b, c, d) in enumerate(ops):
        if tag == 'equal':
            for i in range(a, b):
                owner = owners[i]
                if _candidate(words[owner]):
                    votes[owner] = primary[owner][0]
            continue
        if tag != 'replace' or b - a != 1 or d - c != 1:
            continue
        owner = owners[a]
        if not _candidate(words[owner]):
            continue
        if not (0 < n < len(ops) - 1):
            continue
        left, right = ops[n - 1], ops[n + 1]
        replacement = heard[c]
        if (left[0] != 'equal' or left[2] - left[1] < 2
                or right[0] != 'equal' or right[2] - right[1] < 2
                or len(set(owners[left[1]:left[2]])) < 2
                or len(set(owners[right[1]:right[2]])) < 2
                or not replacement.isalpha() or not replacement.islower()
                or not 3 <= len(replacement) <= 24 or replacement in _NUM
                or not faithful_fix(primary[owner][0], replacement)[0]):
            continue
        votes[owner] = replacement
    return votes


def apply_votes(words, votes):
    result = list(words)
    changes = []
    for index, heard in votes.items():
        alternatives = set(heard)
        if len(alternatives) != 1 or not _candidate(words[index]):
            continue
        replacement = next(iter(alternatives))
        original = str(_get(words[index], 'w'))
        if (not replacement.isalpha() or not replacement.islower()
                or replacement in _NUM or not faithful_fix(original, replacement)[0]):
            continue
        match = TOKEN.search(original)
        if match is None or match.group().casefold() == replacement.casefold():
            continue
        updated = original[:match.start()] + replacement + original[match.end():]
        # The primary confidence belongs to its old token, not this replacement.
        fields = {'w': updated, 'confidence': None}
        if isinstance(words[index], dict):
            result[index] = dict(words[index], **fields)
        else:
            result[index] = words[index].model_copy(update=fields)
        changes.append({'t0': _get(words[index], 't0'), 'before': original, 'after': updated})
    return result, changes


def verify(words, wav_path, language, warnings=None):
    if (not config.TRANSCRIPT_VERIFY_ENABLED or not config.OPENAI_API_KEY
            or urlparse(config.OPENAI_BASE_URL).hostname != 'api.openai.com'
            # The phonetic/number safeguards are English. Expand only with
            # equivalent safeguards and source-audio replays for each language.
            or str(language or '').lower().split('-')[0] != 'en'
            or not any(_candidate(word) for word in words)):
        return words
    try:
        return _verify(words, wav_path, language, warnings)
    except Exception as exc:
        # A second recognizer must never fail an otherwise usable upload.
        print(f'[transcript-verify] unavailable: {type(exc).__name__}', flush=True)
        return words


def _verify(words, wav_path, language, warnings):
    with wave.open(wav_path, 'rb') as audio:
        params = audio.getparams()
        duration = audio.getnframes() / audio.getframerate()
    # Keep every upload below the API's 25 MB limit, even for nonstandard WAVs.
    bytes_per_second = params.framerate * params.nchannels * params.sampwidth
    width = min(WINDOW_S, int(20 * 1024 ** 2 / bytes_per_second) - 2 * CONTEXT_S)
    if width < 5:
        return words
    windows = []
    for core in range(0, int(duration) + 1, width):
        if not any(_candidate(w) and core <= float(_get(w, 't0')) < core + width
                   for w in words):
            continue
        start, end = max(0, core - CONTEXT_S), min(duration, core + width + CONTEXT_S)
        positions = [i for i, w in enumerate(words)
                     if start <= float(_get(w, 't0')) and float(_get(w, 't1')) <= end]
        if len(positions) >= 5:
            windows.append((start, end, positions))
    if not windows:
        return words
    client = llm.client().with_options(timeout=25, max_retries=0)
    recorder = llm.get_recorder()
    unavailable = threading.Event()

    def recognize(item):
        if unavailable.is_set():
            return {}
        start, end, positions = item
        if recorder is not None:
            llm.set_recorder(recorder)
        try:
            with tempfile.TemporaryDirectory(prefix='transcript-verify-') as work:
                path = os.path.join(work, 'speech.wav')
                with wave.open(wav_path, 'rb') as source:
                    source.setpos(int(start * params.framerate))
                    frames = source.readframes(int((end - start) * params.framerate))
                with wave.open(path, 'wb') as target:
                    target.setparams(params)
                    target.writeframes(frames)
                extra = {'languages': [str(language).lower()]} if language else {}
                with open(path, 'rb') as audio:
                    response = client.audio.transcriptions.create(
                        model=MODEL, file=audio, extra_body=extra)
                heard = str(getattr(response, 'text', '') or '')
                local = aligned_votes([words[i] for i in positions], heard)
                llm.record('transcript_verification',
                    {'model': MODEL, 'source_start_s': start, 'audio_seconds': end - start},
                    {'text': heard, 'provider_cost_usd': round((end - start) / 60 * USD_PER_MINUTE, 8),
                     'cost_basis': 'audio_duration_official_rate'})
                return {positions[i]: token for i, token in local.items()}
        except Exception as exc:
            # Stop queued calls after an outage or rate limit; two in flight at
            # most. Do not turn a provider incident into minutes of retries.
            unavailable.set()
            print(f'[transcript-verify] retained primary: {type(exc).__name__}', flush=True)
            return {}
        finally:
            if recorder is not None:
                llm.set_recorder(None)

    votes = collections.defaultdict(list)
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(2, len(windows))) as pool:
        for answer in pool.map(recognize, windows):
            for index, token in answer.items():
                votes[index].append(token)
    result, changes = apply_votes(words, votes)
    if changes:
        print(f'[transcript-verify] corrected {len(changes)} isolated word(s); timing preserved', flush=True)
        llm.record('transcript_corrections', {'model': MODEL}, {'corrections': changes})
    if unavailable.is_set() and warnings is not None:
        warnings.append('Independent transcript verification was unavailable for some speech; '
                        'the primary recognizer\'s words were retained there.')
    return result
