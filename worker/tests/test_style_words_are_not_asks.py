"""Ticket #77 recurrence (2 Nov 2026), request 134582, user 2737.

A kinetic typography intro was read as five edit asks it never made: "bold
modern music-video typography" and "a strong musical beat if audio is
available" (music), "slight rotations, overlapping text compositions"
(angle), "crisp typography" (clarity), letters that "move, slide, scale and
transition" (transitions) and "Avoid ... generic subtitles" (captions). The
request was marked partial and the reply told the user "I didn't change the
angle this time. I didn't sharpen the picture this time."
"""
import ask_ledger

ASK_2737 = (
    "Create a professional, high-energy kinetic typography intro using my "
    "uploaded video.\n\n"
    "**VISUAL STYLE:**\nCreate bold, oversized, modern music-video typography "
    "inspired by premium Instagram Reels motion graphics.\n\n"
    "**TEXT ANIMATION:**\nMake the text extremely dynamic. Use fast "
    "horizontal movements, scale animations, slight rotations, overlapping "
    "text compositions and smooth motion blur.\n\n"
    "Do not simply place static text behind the person. The letters "
    "themselves must continuously move, slide, scale and transition.\n\n"
    "**TIMING:**\nSynchronise the biggest \"SYRUP SIPPIN\" reveal with a "
    "strong musical beat if audio is available. Avoid slow fades and generic "
    "subtitles.\n\n"
    "**FINISH:**\nKeep the original video, person, face, clothing and "
    "background unchanged. Use crisp typography, strong contrast, "
    "professional motion blur, smooth easing and carefully composed "
    "transitions. Avoid unnecessary effects, particles, generic captions and "
    "decorative graphics.")


def test_style_words_of_a_typography_request_are_not_asks():
    assert ask_ledger.asks(ASK_2737) == []
    assert ask_ledger.asks(
        "Add dynamic kinetic typography to my video. Make the words slide in "
        "from different directions, grow and shrink, rotate slightly and move "
        "behind my body. Use bold modern typography, with a professional "
        "music-video aesthetic.") == []


def test_avoid_reaches_along_and_or_lists():
    for text in ("Avoid slow fades and generic subtitles",
                 "Do not add music or captions", "avoid music"):
        assert not {"captions", "music"} & set(ask_ledger.asks(text)), text


def test_the_same_topics_asked_for_the_frame_are_still_asks():
    cases = {
        "Add transitions between the clips and put a title on top": "transitions",
        "Use short English titles, and clean transitions.": "transitions",
        "Add crossfade transitions": "transitions",
        "Add some music and rotate the video, it's tilted": "angle",
        "straighten the horizon and add text": "angle",
        "Make the video sharper": "clarity",
        "Add a beat under it": "music",
        "Add some music": "music",
        "no music and add captions": "captions",
        "Cut out the audio and play something else relating to nature": "music",
        "Keep the grade and add music": "music",
    }
    for text, topic in cases.items():
        assert topic in ask_ledger.asks(text), text
