"""Ticket #123 (2 Nov 2026), request 134693, user 2742.

The user rejected a photo slideshow and wrote "Do NOT continue the previous
music-only instruction ... If not, explain the limitation and stop". The
reply said it cannot generate moving video and then ended on a list of three
library tracks: wants_music() saw the word "music" and ignored the "NOT".
"""
import music_offer

ASK_2742 = (
    "The user rejects the result: it is a slideshow with no actual action. "
    "Do NOT continue the previous music-only instruction. We need "
    "image-to-video generation with real subject motion. If not, explain the "
    "limitation and stop rather than producing another slideshow.")


def test_a_negated_music_mention_is_not_a_music_ask():
    assert not music_offer.wants_music(ASK_2742)
    for text in ("- No voiceover, speech, music, or sound effects.",
                 "Never add background music.",
                 "Stop with the music suggestions."):
        assert not music_offer.wants_music(text), text


def test_a_fresh_ask_after_a_negation_still_counts():
    for text in ("I don't like this song, so add some upbeat music",
                 "Add tasteful instrumental fashion background music if "
                 "available; no voiceover.",
                 "No captions please. Add some chill music.",
                 "Not the sad track but play something upbeat"):
        assert music_offer.wants_music(text), text
