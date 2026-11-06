"""Ticket #77 recurrence (2 Nov 2026), request 134289, user 2728.

"10. Do not add unnecessary text, subtitles, logos or watermarks." was read
as a captions ask (the negation never reached past the comma), so the turn
was marked partial and the reply said "I didn't change the captions this
time". The same request asked for "the uploaded Tamil song", which never
reached the project; the editor's "Attach the MP3" line was dropped by the
trial reply's length cap and the library offer never said the song was
missing.
"""
import ask_ledger
import music_offer

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


class _DB:
    def __init__(self, rows):
        self.rows = rows

    def run(self, fn, *args):
        return self.rows


class _Ctx:
    project_id = 5330

    def __init__(self, music_rows):
        self.db = _DB(music_rows)

    def latest_edl(self):
        return {"json": {"music": []}}


CAT = [{"slug": f"{m}-{i}", "title": f"{m.title()} Song {i}", "mood": m}
       for m in ("chill", "ambient", "upbeat", "cinematic") for i in (1, 2)]


def _offer(monkeypatch, ctx, text):
    import music_gate
    import music_library
    monkeypatch.setattr(music_library, "catalog", lambda: CAT)
    monkeypatch.setattr(music_library, "used_by_user", lambda c: set())
    monkeypatch.setattr(music_gate, "open_offer", lambda c, q: {"slugs": ["x"]})
    return music_offer.turn_end_offer(ctx, text)


def test_a_named_upload_that_never_arrived_is_said_first(monkeypatch):
    text = _offer(monkeypatch, _Ctx([]), ASK_2728)
    assert text.startswith(music_offer.MISSING_SONG)
    assert "Cinematic Song 1" in text


def test_the_plain_offer_when_the_song_is_there_or_never_named(monkeypatch):
    assert _offer(monkeypatch, _Ctx([{"id": 1}]), ASK_2728).startswith(
        "For the music, pick one")
    assert _offer(monkeypatch, _Ctx([]), "add some chill music").startswith(
        "For the music, pick one")
    assert not music_offer.own_song_missing(object(), ASK_2728)  # no db: unsure


def test_only_music_left_is_a_choice_not_a_partial():
    unmet = [t for t in ask_ledger.asks(ASK_2728) if t in ("music", "captions")]
    assert unmet == ["music"]
    assert music_offer.choice_outcome("fulfilled", "offer") == "awaiting_user"
