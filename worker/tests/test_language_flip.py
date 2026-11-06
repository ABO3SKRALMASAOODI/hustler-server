"""The reply-language guard, held against one real day of escapes.

2026-08-09, production: English-writing users received replies in Russian
(via ask_user), Spanish, Turkish, Albanian, Portuguese and Romanized Hindi
within twelve hours. The script count catches the cross-script ones and these
fixtures pin it; same-script flips belong to the model
(tests/test_reply_language.py).
"""

import agent_loop as al


EN_HIST = ("re edit this video the captions and also the dimension deal "
           "with this add b rolls related to the topic and keep the music "
           "I like I need better captions than this")
EN_LAST = "theres three overlapping b roll keep the first and the last one"


def _flip(reply, hist=EN_HIST, last=EN_LAST):
    return al._language_flip(hist + " " + last, last, reply)


def test_cyrillic_reply_to_english_user_flips():
    assert _flip("У текущем монтаже вижу только два b-roll-изображения: "
                 "первый — стартовый кадр SpaceX с огнём") is not None


def test_english_advisory_must_not_run_before_the_language_check():
    """Project 755: a Bulgarian draft plus the English quality advisory
    dropped below the 60% Cyrillic threshold and shipped. Check the draft
    alone; the advisory is appended after."""
    bg = ("Промоът е финализиран като съществуваща 16:9 Dope Sports "
          "редакция с продължителност 56,9 секунди: започва и завършва "
          "с 3-панелните action монтажи.")
    assert al._language_flip(EN_HIST, EN_LAST, bg) is not None
    diluted = bg + "\n\nQuality advisory (export remains available): " + (
        "independent visual review at 12.2s: two text cards compete.")
    # Documents the failure mode the call-site order must avoid.
    assert al._dominant_script(diluted) in (None, "Latin")


def test_english_reply_with_accented_quote_does_not_flip():
    assert _flip("I added “Café de Paris — Élan” as the music bed with "
                 "smooth ducking; the edit is 23 seconds and the mix sits "
                 "at -14 LUFS.") is None


def test_plain_english_reply_does_not_flip():
    assert _flip("I downloaded the track and added it as a ducked music "
                 "bed across all 8 shorts. Each short is re-rendering "
                 "now — watch the board.") is None


def test_arabic_user_arabic_reply_does_not_flip():
    hist = "كيف احمل الان المقاطع كلها"
    assert al._language_flip(
        hist, hist,
        "إذا كنت تقصد رفع المقاطع إلى المشروع: اضغط رمز الإرفاق، حدد "
        "جميع الفيديوهات دفعة واحدة ثم أرسلها.") is None


def test_bilingual_user_keeps_their_second_language():
    # A user who has themselves written Portuguese in the conversation may
    # be answered in Portuguese — their scripts/words appear in history.
    hist = ("nao, usa a letra da musica e faz sem ser em formato tiktok "
            "finish the edit at the 40 seconds")
    assert al._language_flip(
        hist, "finish the edit at the 40 seconds",
        "O corte final ficou com 40 segundos, com a progressão emocional "
        "e a tipografia seletiva preservadas.") is None
