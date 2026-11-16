"""Ticket #77 recurrence (2 Nov 2026), request 134289, user 2728.

"10. Do not add unnecessary text, subtitles, logos or watermarks." was read
as a captions ask (the negation never reached past the comma), so the turn
was marked partial and the reply said "I didn't change the captions this
time". The same request asked for "the uploaded Tamil song", which never
reached the project; the editor's "Attach the MP3" line was dropped by the
trial reply's length cap and the library offer never said the song was
missing. (The turn-end music offer is gone since 16 Nov 2026: Valmera chooses
the music and the editor reads the project's assets itself.)
"""
import ask_ledger

ASK_2728 = (
    "Create a beautiful, professional Tamil movie-style travel cinematic reel "
    "using my uploaded 90-second video and the uploaded Tamil song \"Thuli "
    "Thuliyaai Mazhaiyaai Vandhaale\" from Paiyaa.\n\n"
    "6. Use smooth, elegant transitions where appropriate.\n"
    "8. Apply a consistent warm cinematic colour grade.\n"
    "10. Do not add unnecessary text, subtitles, logos or watermarks.\n")


def test_a_negation_reaches_along_its_list():
    assert "captions" not in ask_ledger.asks(ASK_2728)
    assert {"transitions", "music", "grade"} <= set(ask_ledger.asks(ASK_2728))
    for text in ("Do not add text, logos or subtitles",
                 "No logos, watermarks, captions or text",
                 "Ohne Text, Untertitel oder Logos",
                 "- No voiceover, speech, music, or sound effects."):
        assert "captions" not in ask_ledger.asks(text) \
            and "music" not in ask_ledger.asks(text), text


def test_a_new_instruction_after_the_comma_is_still_an_ask():
    for text in ("No music, captions please", "no music, add captions",
                 "Keep the grade, add subtitles",
                 "Remove the logo, add captions, music"):
        assert "captions" in ask_ledger.asks(text), text
    assert "music" in ask_ledger.asks("Remove the logo, add captions, music")
