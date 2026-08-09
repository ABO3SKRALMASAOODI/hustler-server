"""The reply-language guard, held against one real day of escapes.

2026-08-09, production: English-writing users received replies in Russian
(via ask_user), Spanish, Turkish, Albanian, Portuguese and Romanized Hindi
within twelve hours. The script check caught only the Russian; the marker
vote missed the rest because a two-sentence reply rarely carries three
distinctive function words. These are those exact replies (trimmed), pinned
as fixtures: every one must flip, and the legitimate replies beside them
must not.
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


def test_spanish_reply_to_english_user_flips():
    assert _flip("Descargué y añadí “Hans Zimmer - Interstellar Main "
                 "Theme” (versión no oficial de SoundCloud) como pista "
                 "musical con ducking suave durante todo el programa. La "
                 "mezcla quedó aplicada al video actual; el proyecto no "
                 "contiene shorts separados en este momento.") is not None


def test_turkish_reply_to_english_user_flips():
    assert _flip("Edit teslim edildi: 19,83 saniyelik dikey klasik anime "
                 "tarzı kamera tanıtımı; sinematik siyah-beyaz görünüm, "
                 "kırmızı vurgu, ritmik altyazılar ve temiz vignette "
                 "kullanıldı.") is not None


def test_albanian_reply_to_english_user_flips():
    assert _flip("Editimi tani është një Reel 7,6-sekondësh me fillim të "
                 "drejtpërdrejtë, dy tekstet vetëm gjatë 2,8 sekondave të "
                 "para dhe një payoff të pastër të outfit-it pa "
                 "mbishkrime shtesë; audioja është normalizuar.") is not None


def test_portuguese_reply_to_english_user_flips():
    assert _flip("O vídeo ficou como um desafio completo de 3:18, com "
                 "cortes de dead time, hook visual, ritmo acelerado, "
                 "captions em hindi, zooms de reação, grade quente, "
                 "música funk com ducking e mix normalizado. A prévia "
                 "confirmou o corte.") is not None


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
