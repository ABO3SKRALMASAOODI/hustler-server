"""Every heard word reaches the screen once (showcase judging, Oct 2026, round 4).

The captions side of the contract between graphics and captions:
  1. A graphic takes the words it shows and the one or two connectors
     between them. Every other heard word is captioned — beside it, in a
     band clear of it — whatever the graphic: a lockup's left-out words
     (no micro bridge rows inside it), a slam's tail ('to take our
     civilization to'), the words between two stat slams ('and scored'),
     the speech under a split ('technology … it just means').
  2. mute_captions=true never mutes a whole window any more; it hides what
     the graphic shows, like unset.
  3. Only where no band is clear of a graphic that says the line, the face
     and the layout are its other words muted — and heard_unshown names
     every one of them, however short.
  4. A lockup's reading times its rows and carries no bridges; an old
     stored reading with bridges still validates.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import caption_carry  # noqa: E402
import captions  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

AR = round(1080 / 1920, 4)


def _w(rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


def _edl(motion, words, look="editorial", dur=None):
    dur = dur or max(b for _w, _a, b in words) + 2.0
    edl = default_edl(dur)
    edl["captions"] = {"mode": "from_transcript", "design_version": 2,
                       "style": {"motion_look": look} if look else {"preset": "stacked"}}
    edl["motion"] = [dict(m) for m in motion]
    return validate_edl(edl, dur).model_dump()


def _index(words, faces=((0.3, 0.12, 0.7, 0.3),)):
    samples = [{"t": t, "faces": [list(f) for f in faces]} for t in (0.5, 2.0, 4.0, 6.0)]
    return {"video": {"width": 1080, "height": 1920}, "words": _w(words),
            "spatial": {"samples": samples}}


def _fp(box):
    return {"box": list(box), "ar": AR, "faces": []}


def _on_screen(edl, ix):
    """The spoken words a viewer can read: captions plus graphic-carried."""
    tl = Timeline(edl["keep"])
    p = captions.caption_plan(edl, ix, tl)
    shown = {caption_carry.said_key(w) for w in p.caption_words()}
    carried = {caption_carry.said_key(p.words[i]) for i, (_o, why) in p.hidden.items()
               if why == "carried"}
    return p, shown, carried


def _assert_every_word_once(edl, ix):
    p, shown, carried = _on_screen(edl, ix)
    for w in p.words:
        k = caption_carry.said_key(w)
        assert (k in shown) != (k in carried), (w["w"], k in shown, k in carried)
    assert caption_carry.heard_unshown(edl, ix, Timeline(edl["keep"])) == []
    return p


# ── 1. what a graphic does not show is captioned ─────────────────────────

PAPER = [("no", 0.7, 0.92), ("college", 0.92, 1.28), ("student", 1.28, 1.6),
         ("three", 1.6, 1.8), ("or", 1.8, 1.9), ("four", 1.9, 2.02), ("years", 2.02, 2.2),
         ("from", 2.2, 2.36), ("now", 2.36, 2.48), ("that's", 2.48, 2.62), ("ever", 2.62, 2.76),
         ("going", 2.76, 2.86), ("to", 2.86, 3.0), ("think", 3.0, 3.14), ("of", 3.14, 3.28),
         ("writing", 3.28, 3.5), ("a", 3.5, 3.66), ("paper", 3.66, 3.9), ("without", 3.9, 4.2),
         ("one", 4.2, 4.36), ("of", 4.36, 4.46), ("these", 4.46, 4.58), ("things.", 4.58, 5.04)]
PAPER_LOCKUP = {"id": "paper", "template": "phrase_build", "start": 0.6, "end": 5.3,
                "params": {"rows": [{"text": "no college student"},
                                    {"text": "writing a paper"},
                                    {"text": "WITHOUT *ONE*"}]},
                "footprint": _fp((0.1, 0.06, 0.9, 0.26))}


def test_a_lockup_shows_its_rows_and_the_captions_carry_the_rest():
    edl = _edl([PAPER_LOCKUP], PAPER)
    ix = _index(PAPER)
    p = _assert_every_word_once(edl, ix)
    said = caption_carry._said
    assert [said(r) for r in p.report["paper"]["captioned"]] == [
        "three or four years from now that's ever going to think of", "of these things"]
    # no bridge rows: the lockup's reading is its own rows only
    rd = caption_carry.readings(edl, ix, Timeline(edl["keep"]))["paper"]
    assert rd["bridges"] == [] and len(rd["rows"]) == 3
    # the captions for those words run beside it, never on its box
    for i, w in enumerate(p.words):
        if i not in p.hidden and 0.6 <= w["t0"] < 5.3 and i in p.placed:
            z = p.placed[i]["z"]
            assert z[0] >= 0.26 + caption_carry.GRAPHIC_PAD - 1e-6


def test_a_slams_tail_and_the_words_between_stat_slams_are_captioned():
    enough = [("it's", 0.0, 0.2), ("not", 0.62, 0.75), ("quite", 0.75, 0.9),
              ("been", 0.9, 1.05), ("enough", 1.3, 1.8), ("to", 1.9, 2.0), ("take", 2.0, 2.3),
              ("our", 2.3, 2.45), ("civilization", 2.45, 3.0), ("to", 3.0, 3.1)]
    slam = {"id": "enough", "template": "word_slam", "start": 1.1, "end": 3.3,
            "params": {"text": "*enough*", "kicker": "it's not quite been"},
            "footprint": _fp((0.1, 0.62, 0.9, 0.78))}
    edl = _edl([slam], enough)
    p = _assert_every_word_once(edl, _index(enough))
    assert [p.words[i]["w"] for i in sorted(p.placed)] == ["to", "take", "our", "civilization", "to"]
    # Elon: "were 24% faster, and scored 26% better overall"
    stats = [("were", 0.2, 0.4), ("24%", 0.4, 0.9), ("faster,", 0.9, 1.3), ("and", 1.4, 1.48),
             ("scored", 1.48, 1.76), ("26%", 1.8, 2.4), ("better", 2.4, 2.9),
             ("overall", 2.9, 3.4)]
    st2 = {"id": "st2", "template": "word_slam", "start": 0.4, "end": 1.8,
           "params": {"text": "*24%* / faster"}, "footprint": _fp((0.3, 0.09, 0.7, 0.27))}
    st3 = {"id": "st3", "template": "word_slam", "start": 1.8, "end": 3.6,
           "params": {"text": "*26%* / better overall"}, "footprint": _fp((0.3, 0.11, 0.7, 0.25))}
    edl = _edl([st2, st3], stats)
    p = _assert_every_word_once(edl, _index(stats))
    assert [w["w"] for w in p.caption_words()] == ["were", "and", "scored"]


def test_a_split_that_shows_other_words_keeps_the_speech_captioned():
    words = [("and", 0.0, 0.1), ("when", 0.1, 0.3), ("we", 0.3, 0.4), ("use", 0.4, 0.6),
             ("technology", 0.6, 1.2), ("today,", 1.3, 1.7), ("it", 1.8, 1.9),
             ("just", 1.9, 2.1), ("means", 2.1, 2.5), ("information", 2.6, 3.2),
             ("technology.", 3.2, 3.9)]
    vs = {"id": "vs", "template": "versus_split", "start": 0.5, "end": 4.0,
          "params": {"left": "1960s", "right": "TODAY", "left_sub": "rockets, cures",
                     "right_sub": "information tech"},
          "mute_captions": True, "footprint": _fp((0.0, 0.665, 1.0, 0.796))}
    edl = _edl([vs], words)
    assert captions.effective_caption_mutes(edl) == []
    p = _assert_every_word_once(edl, _index(words))
    shown = [w["w"] for w in p.caption_words()]
    assert "technology" in shown and "means" in shown


# ── 2/3. muted only without room, and always named ───────────────────────

def test_words_muted_for_want_of_a_band_are_named_however_short():
    words = [("were", 0.2, 0.4), ("24%", 0.4, 0.9), ("faster,", 0.9, 1.3), ("and", 1.4, 1.48),
             ("scored", 1.48, 1.76), ("26%", 1.8, 2.4)]
    # a slam filling the safe area under an unmeasured (assumed) face
    st2 = {"id": "st2", "template": "word_slam", "start": 0.3, "end": 1.8,
           "params": {"text": "*24%* / faster"}, "footprint": _fp((0.1, 0.45, 0.9, 0.85))}
    edl = _edl([st2], words)
    ix = {"video": {"width": 1080, "height": 1920}, "words": _w(words)}
    tl = Timeline(edl["keep"])
    p = captions.caption_plan(edl, ix, tl)
    room = [p.words[i]["w"] for i, (_o, why) in p.hidden.items() if why == "room"]
    assert "scored" in room
    # heard and never shown: named, however short
    gone = caption_carry.heard_unshown(edl, ix, tl)
    assert gone and any("scored" in g["said"] for g in gone)
    assert all(g["owner"] == "st2" for g in gone)


# ── 4. readings ──────────────────────────────────────────────────────────

def test_an_old_stored_reading_with_bridges_still_validates():
    old = dict(PAPER_LOCKUP, reading={"v": 1, "rows": [[0.1, 0.32, 0.68]],
                                      "bridges": [{"after": 0, "words": [
                                          {"t": "three", "s": 1.0, "g": 1}]}]})
    edl = _edl([old], PAPER)
    assert edl["motion"][0]["reading"]["bridges"][0]["words"][0]["t"] == "three"
    # ...and the render recomputes it without them
    caption_carry.attach_readings(edl, _index(PAPER), Timeline(edl["keep"]))
    assert edl["motion"][0]["reading"]["bridges"] == []


def test_a_printed_word_cut_short_is_read_inside_a_run_only():
    # the split prints 'information tech' for 'information technology': the
    # captions do not say it a second time beside it
    words = [("it", 0.1, 0.2), ("just", 0.2, 0.4), ("means", 0.4, 0.8),
             ("information", 0.9, 1.5), ("technology.", 1.5, 2.2)]
    vs = {"id": "vs", "template": "versus_split", "start": 0.0, "end": 2.5,
          "params": {"left": "1960s", "right": "TODAY", "left_sub": "rockets, cures",
                     "right_sub": "information tech"},
          "footprint": _fp((0.0, 0.665, 1.0, 0.796))}
    p = _assert_every_word_once(_edl([vs], words), _index(words))
    assert [w["w"] for w in p.caption_words()] == ["it", "just", "means"]
    # a clipped word never makes a run on its own ('the tech' over 'the
    # technology'), and a one-word line never reads a longer spoken word
    for printed in ("the tech", "tech"):
        sp = [("the", 0.1, 0.3), ("technology", 0.3, 1.0), ("works", 1.0, 1.4)]
        slam = {"id": "s", "template": "word_slam", "start": 0.0, "end": 1.6,
                "params": {"text": printed}, "footprint": _fp((0.1, 0.1, 0.9, 0.25))}
        edl = _edl([slam], sp)
        p = captions.caption_plan(edl, _index(sp), Timeline(edl["keep"]))
        assert "technology" in [w["w"] for w in p.caption_words()], printed
