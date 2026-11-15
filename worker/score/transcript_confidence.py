"""Read measured ASR confidence without inventing a score for corrected words.

transcript_verify clears the old token's confidence when it changes a word.
None therefore means unknown, not zero and not a failed transcript.
"""


def low(word, threshold=.75):
    value = word.get("confidence")
    return value is not None and float(value) < threshold


def mean(words):
    values = [float(w["confidence"]) for w in words if w.get("confidence") is not None]
    return sum(values) / len(values) if values else None


def low_mean(words, threshold=.75):
    value = mean(words)
    return value is not None and value < threshold
