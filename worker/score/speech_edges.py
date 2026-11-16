"""Expose the write guard's acoustic boundaries before a first film is authored.

These are facts for the Director, never an edit or an exemption from either
speech guard. A transcript edge can fall after an audible onset; the model
must choose a complete phrase that survives both checks.
"""
from functools import lru_cache
import os

import cut_audio


def _inputs(index, samples):
    words = index.get("words") or []
    if not words or samples is None or not len(samples):
        return None
    duration = len(samples) / cut_audio.SR
    level = lru_cache(maxsize=8192)(cut_audio.level_reader({(0., duration): samples}))
    return words, cut_audio._Words(words), duration, level


def at_words(index, samples):
    inputs = _inputs(index, samples)
    if inputs is None:
        return []
    words, table, duration, level = inputs
    facts = []
    for number, word in enumerate(words):
        for side, found in (
            ("start", cut_audio.clipped_onsets([[float(word["t0"]), duration]], table, duration, level)),
            ("end", cut_audio.clipped_releases([[0., float(word["t1"])]], table, duration, level)),
        ):
            facts.extend(dict(f, side=side, word_id=number) for f in found)
    return facts


def brief_lines(facts):
    if not facts:
        return []
    return [
        "## Measured speech boundaries",
        "The write guard measured audible speech crossing these transcript edges. "
        "Keep continuous speech across them or choose a different whole-word phrase. "
        "Do not cut at the listed edge, pad into an omitted word, or rewrite the transcript times. "
        "Each arrow gives the nearby measured quiet point, not permission to cut inside another word.",
        "; ".join(f"w{f['word_id']} {f['side']} {f['b']:.3f}s -> quiet {f['t']:.3f}s"
                  for f in facts),
    ]


def selected(index, samples, spine):
    inputs = _inputs(index, samples)
    if inputs is None:
        return []
    _words, table, duration, level = inputs
    facts = []
    # Each spine entry is a continuous source run. Checking independently
    # preserves reordered speech; source-order limits from the preceding
    # programme run would otherwise hide a bad cut after a reorder.
    for span in spine:
        keep = [span["src"]]
        for side, found in (
            ("start", cut_audio.clipped_onsets(keep, table, duration, level)),
            ("end", cut_audio.clipped_releases(keep, table, duration, level)),
        ):
            facts.extend(dict(f, side=side, source=list(span["src"])) for f in found)
    return facts


def in_film(work, index, spine):
    if not work or not index.get("first_film") or not index.get("words"):
        return []
    source = os.path.join(work, "source.mp4")
    if not os.path.isfile(source):
        return []
    from . import source_audio
    return selected(index, source_audio.decode(source, work), spine)


def findings(facts):
    out = []
    for fact in facts:
        describe = cut_audio.onset_lines if fact["side"] == "start" else cut_audio.release_lines
        out.append("speech boundary: " + describe([fact])[0] +
                   ". Keep continuous speech across this edge or choose another whole-word phrase; "
                   "do not pad into an omitted word. The write guard still applies.")
    return out
