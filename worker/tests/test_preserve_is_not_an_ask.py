"""Ticket #77 (journey j15, request 134968, 3 Nov 2026).

"Preserve all original subtitles and branding" asked us NOT to touch the
subtitles. The ask ledger knew "keep" and "leave" but no other word for that,
so it counted a captions ask, marked the request partial, and the next turn
laid new captions over the burned-in subtitles the customer wanted preserved.
"""
import pytest

import ask_ledger

ACADEMY_BRIEF = """Transform my existing video into a high-end promotional video.

EDITING:

* Add beautiful B-roll cutaways every few seconds where appropriate.
* Keep the original speaker, voice, timing and Azerbaijani language.
* Preserve all original subtitles and branding.
* Keep the original vertical 9:16 format.

Do not simply add subtitles or filters."""


def test_the_ticket_request_has_no_captions_ask():
    asked = ask_ledger.asks(ACADEMY_BRIEF)
    assert "captions" not in asked
    assert "broll" in asked


@pytest.mark.parametrize("text", [
    "Preserve all original subtitles and branding.",
    "Preserve her original voice, Azerbaijani speech, subtitles and academy logo.",
    "Preserving the captions is important.",
    "Retain the existing subtitles.",
    "Please maintain the captions.",
    "Behalte die B-Roll.",
    "Conserva el b-roll original.",
    "Mantén el b-roll original.",
    "Mantenha o b-roll original.",
    "Mantieni il b-roll originale.",
    "Garde le b-roll original.",
    "Behoud de b-roll.",
])
def test_preserving_a_layer_is_not_asking_for_it(text):
    assert ask_ledger.asks(text) == []


@pytest.mark.parametrize("text, topic", [
    ("Add subtitles.", "captions"),
    ("Preserve the voice and add captions.", "captions"),
    ("Preserve the logo, add subtitles.", "captions"),
    ("Keep the speaker visible. Add b-roll.", "broll"),
    ("Retain the opening. Add captions throughout.", "captions"),
])
def test_a_real_ask_next_to_a_preserve_stays_an_ask(text, topic):
    assert topic in ask_ledger.asks(text)
