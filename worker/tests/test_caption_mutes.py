"""Round 37 — caption mutes and standalone title cards.

Both exist because of one real session (project 99, thevalmera@gmail.com,
2026-07-23/24): the user asked for a term shown on a blank screen and then for
"the captions off at the effect time only". Neither was expressible. The agent
improvised a full-frame black `blur_region`, which is drawn into the SOURCE
segment before captions burn — so the captions landed on top of the black card,
exactly the overlap the user reported — and then spent 450s building and tearing
the arrangement down twice until the turn timed out.

What is pinned here:
  1. caption_mutes never perturbs a pre-round-37 EDL's signature (no phantom
     re-render of anyone's cached edit).
  2. A muted window really removes the caption events on screen during it, in
     EVERY emission mode (plain / premium / karaoke), and only those.
  3. The graze rule: a caption clipping a window edge by a few frames survives.
  4. add_title_card lands its text on the card's OWN program window, including
     after earlier inserts have shifted the timeline.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

import captions  # noqa: E402
from schemas import (EDLValidationError, describe_edl,  # noqa: E402
                     edl_signature, validate_edl)
from timeline import Timeline, remap_program_items  # noqa: E402


WORDS = [{"w": f"word{i}", "t0": i * 1.0, "t1": i * 1.0 + 0.9}
         for i in range(12)]
INDEX = {"words": WORDS}


def _dump(edl, dur=12.0):
    return validate_edl(edl, dur).model_dump()


def _dialogue(edl, tl=None):
    tl = tl or Timeline([[0.0, 12.0]], edl.get("inserts") or [], [])
    path = os.path.join(tempfile.mkdtemp(), "c.ass")
    if not captions.build_ass(edl, INDEX, tl, path):
        return []
    return [ln for ln in open(path).read().splitlines()
            if ln.startswith("Dialogue")]


def _start_s(line):
    h, m, s = line.split(",")[1].split(":")
    return float(h) * 3600 + float(m) * 60 + float(s)


def _end_s(line):
    h, m, s = line.split(",")[2].split(":")
    return float(h) * 3600 + float(m) * 60 + float(s)


# ── 1. signature stability ──────────────────────────────────────────────── #

def test_caption_mutes_absent_keeps_legacy_signature():
    legacy = {"keep": [[0.0, 10.0]],
              "captions": {"mode": "from_transcript"}}
    assert _dump(legacy, 10.0)["caption_mutes"] == []
    assert edl_signature(_dump(legacy, 10.0)) == edl_signature(legacy)


# ── 2. validation ───────────────────────────────────────────────────────── #

def test_mutes_are_sorted_and_overlaps_merged():
    e = _dump({"keep": [[0.0, 12.0]],
               "captions": {"mode": "from_transcript"},
               "caption_mutes": [[5.0, 6.0], [1.0, 2.0], [1.9, 3.0]]})
    assert e["caption_mutes"] == [[1.0, 3.0], [5.0, 6.0]]


def test_mute_past_program_end_is_rejected():
    try:
        _dump({"keep": [[0.0, 12.0]], "caption_mutes": [[9.0, 50.0]]})
    except EDLValidationError as err:
        assert "50" in str(err)
    else:
        raise AssertionError("a mute past the program end must not validate")


def test_mutes_are_described_to_the_agent():
    e = validate_edl({"keep": [[0.0, 12.0]],
                      "captions": {"mode": "from_transcript"},
                      "caption_mutes": [[4.0, 8.0]]}, 12.0)
    assert "captions muted (4-8s)" in describe_edl(e)


# ── 3. the filter itself ────────────────────────────────────────────────── #

def test_muted_window_drops_only_the_captions_inside_it():
    base = {"keep": [[0.0, 12.0]],
            "captions": {"mode": "from_transcript",
                         "max_words_per_caption": 2}}
    before = _dialogue(_dump(base))
    after = _dialogue(_dump({**base, "caption_mutes": [[4.0, 8.0]]}))
    assert len(before) == 6 and len(after) == 4
    # nothing left is on screen during the window
    for ln in after:
        assert _end_s(ln) <= 4.15 or _start_s(ln) >= 7.85
    # and the captions outside it are untouched
    assert [ln.split(",,")[-1] for ln in after] == \
        [ln.split(",,")[-1] for ln in before
         if _end_s(ln) <= 4.15 or _start_s(ln) >= 7.85]


def test_mute_applies_in_every_emission_mode():
    for style in ({"preset": "luxe"}, {"preset": "karaoke"},
                  {"dynamic": True}, None):
        e = _dump({"keep": [[0.0, 12.0]],
                   "captions": {"mode": "from_transcript", "style": style,
                                "max_words_per_caption": 2},
                   "caption_mutes": [[4.0, 8.0]]})
        lines = _dialogue(e)
        assert lines, f"{style} produced no captions at all"
        assert not [ln for ln in lines if 4.2 < _start_s(ln) < 7.8], \
            f"{style} still burns a caption inside the muted window"


def test_a_straddling_line_is_hidden_whole():
    """Documented cost of drop-don't-trim: premium and karaoke events carry
    inline \\k word timings measured from the event start, so a line running
    into a window cannot be cut short without desyncing every word after it.
    It disappears instead — which is why set_caption_mutes tells the model to
    tighten the window rather than widen it."""
    e = _dump({"keep": [[0.0, 12.0]],
               "captions": {"mode": "from_transcript"},   # one long group
               "caption_mutes": [[4.0, 8.0]]})
    assert _dialogue(e) == []


def test_manual_caption_items_are_muted_too():
    e = _dump({"keep": [[0.0, 12.0]],
               "captions": [{"text": "keep me", "start": 1.0, "end": 2.0},
                            {"text": "hide me", "start": 5.0, "end": 6.0}],
               "caption_mutes": [[4.0, 8.0]]})
    body = " ".join(_dialogue(e))
    assert "keep me" in body and "hide me" not in body


def test_graze_at_the_edge_keeps_the_caption():
    ev = [{"start": 3.0, "end": 5.1, "text": "grazes"},
          {"start": 6.0, "end": 8.2, "text": "overlaps"},
          {"start": 9.0, "end": 10.0, "text": "outside"}]
    kept = [e["text"] for e in captions.apply_mutes(ev, [[5.0, 6.0],
                                                         [8.0, 8.9]])]
    assert kept == ["grazes", "outside"]


def test_no_mutes_is_an_exact_passthrough():
    ev = [{"start": 1.0, "end": 2.0, "text": "a"}]
    assert captions.apply_mutes(ev, None) is ev
    assert captions.apply_mutes(ev, []) is ev


def test_muting_everything_yields_no_ass_file():
    e = _dump({"keep": [[0.0, 12.0]],
               "captions": {"mode": "from_transcript"},
               "caption_mutes": [[0.0, 12.0]]})
    tl = Timeline([[0.0, 12.0]], [], [])
    path = os.path.join(tempfile.mkdtemp(), "c.ass")
    assert captions.build_ass(e, INDEX, tl, path) is None


# ── 3b. no caption may hold across a spliced screen ─────────────────────── #

def _windows(edl):
    tl = Timeline([list(k) for k in edl["keep"]], edl.get("inserts") or [],
                  edl.get("speed") or [])
    return [(round(s, 2), round(s + d, 2)) for s, d in tl.insert_positions()]


def _overlap_count(edl):
    tl = Timeline([list(k) for k in edl["keep"]], edl.get("inserts") or [],
                  edl.get("speed") or [])
    path = os.path.join(tempfile.mkdtemp(), "c.ass")
    if not captions.build_ass(edl, INDEX, tl, path):
        return 0
    wins = _windows(edl)
    n = 0
    for ln in open(path):
        if not ln.startswith("Dialogue"):
            continue
        s0, s1 = _start_s(ln), _end_s(ln)
        for w0, w1 in wins:
            if min(s1, w1) - max(s0, w0) > 0.15:
                n += 1
    return n


def test_no_caption_holds_across_a_short_insert_in_any_mode():
    """A 0.3s screen makes only a 0.3s output gap — under the 1.2s flush — so
    grouping used to pack words from both sides into one caption that spanned
    it, and premium/karaoke held the prior caption across it. Both are fixed:
    grouping breaks at the insert AND the display end is clamped to it."""
    base = {"keep": [[0.0, 6.0], [6.0, 30.0]],
            "inserts": [{"id": "ins1", "asset_key": "generated/1/card.png",
                         "kind": "image", "at_output_s": 6.0,
                         "duration_s": 0.3}]}
    for style in (None, {"preset": "luxe"}, {"preset": "karaoke"},
                  {"dynamic": True}):
        e = _dump({**base,
                   "captions": {"mode": "from_transcript",
                                "max_words_per_caption": 3, "style": style}},
                  30.0)
        assert _overlap_count(e) == 0, f"{style} caption spans the screen"


def test_insert_break_passes_are_noops_without_inserts():
    """Legacy render identity: an EDL with no inserts must build byte-identical
    captions — the break/clamp passes touch nothing."""
    ev = [{"start": 1.0, "end": 2.0, "text": "a"}]
    words = [{"w": "x", "t0": 1.0, "t1": 2.0}]

    class NoInsertTL:
        def insert_positions(self):
            return []

    assert captions._clamp_events_to_inserts(ev, NoInsertTL()) is ev
    assert captions._mark_insert_breaks(words, NoInsertTL()) is words
    assert "brk" not in words[0]


# ── 4. title cards ──────────────────────────────────────────────────────── #

def test_inserted_card_time_carries_no_captions():
    """The reason add_title_card needs no mute: inserted media is not
    transcribed, so the timeline maps captions around it."""
    # inserts splice at a keep-segment boundary — the split insert_media makes
    keep = [[0.0, 6.0], [6.0, 12.0]]
    e = _dump({"keep": keep,
               "captions": {"mode": "from_transcript",
                            "max_words_per_caption": 2},
               "inserts": [{"id": "ins1", "asset_key": "generated/1/card.png",
                            "kind": "image", "at_output_s": 6.0,
                            "duration_s": 2.0}]})
    tl = Timeline(keep, e["inserts"], [])
    for ln in _dialogue(e, tl):
        assert _end_s(ln) <= 6.05 or _start_s(ln) >= 7.95, \
            "a caption burns over the inserted card"


def test_mutes_follow_their_footage_through_a_cut():
    """A mute is content-anchored: it shadows the effect it was paired with.
    Program-anchored, a cut upstream would slide it onto innocent speech and
    silently delete those captions instead."""
    old = Timeline([[0.0, 30.0]], [], [])
    new = Timeline([[5.0, 30.0]], [], [])          # first 5s cut away
    edl = {"caption_mutes": [[12.0, 14.0]]}
    remap_program_items(edl, old, new)
    assert edl["caption_mutes"] == [[7.0, 9.0]]


def test_mute_on_removed_footage_is_dropped_and_disclosed():
    old = Timeline([[0.0, 30.0]], [], [])
    new = Timeline([[5.0, 30.0]], [], [])
    edl = {"caption_mutes": [[1.0, 3.0]]}
    notes = remap_program_items(edl, old, new)
    assert edl["caption_mutes"] == []
    assert any("caption mute" in n for n in notes), \
        "dropping a mute must be disclosed, not silent"


def test_word_on_the_split_boundary_does_not_span_the_insert():
    """The actual mechanism behind "the captions overlap the title card".

    insert_media splits a take at a WORD EDGE so no word is clipped, which
    puts a word's t0 exactly on the new keep boundary. Word edges map
    independently and a boundary time is ambiguous, so that word's start
    resolved to the segment BEFORE the spliced card and its end to the one
    AFTER — one caption stretched across the whole card and burned over it.
    """
    keep = [[0.0, 6.0], [6.0, 12.0]]
    card = [{"id": "ins1", "at_output_s": 6.0, "duration_s": 2.0}]
    tl = Timeline(keep, card, [])
    (card_start, card_len), = tl.insert_positions()
    card_end = card_start + card_len
    for w in tl.kept_words(WORDS):
        assert w["t1"] <= card_start + 1e-6 or w["t0"] >= card_end - 1e-6, \
            f"{w['w']} ({w['t0']}-{w['t1']}) runs across the card"


def test_card_program_window_math_survives_earlier_inserts():
    """add_title_card resolves its card's window by the same sort the
    Timeline uses, then centres the text there. Pin that mapping: an insert
    added at a LATER point must not be credited with an earlier card's
    program position once preceding inserts have shifted the clock."""
    inserts = [{"id": "ins1", "at_output_s": 4.0, "duration_s": 2.0},
               {"id": "ins2", "at_output_s": 8.0, "duration_s": 3.0}]
    positions = Timeline([[0.0, 12.0]], inserts, []).insert_positions()
    tuples = sorted((float(i["at_output_s"]), float(i["duration_s"]))
                    for i in inserts)

    def window(item):
        mine = (float(item["at_output_s"]), float(item["duration_s"]))
        idx = len(tuples) - 1 - tuples[::-1].index(mine)
        start, length = positions[idx]
        return round(start, 2), round(start + length, 2)

    # ins1 plays at its own pre-insert time; ins2 is pushed by ins1's 2s
    assert window(inserts[0]) == (4.0, 6.0)
    assert window(inserts[1]) == (10.0, 13.0)


def test_identical_cards_resolve_to_distinct_windows():
    """Two cards of the same colour and length at the same boundary: the one
    appended LAST must map to the LAST of the tied windows, or its text would
    be centred on its twin."""
    inserts = [{"id": "ins1", "at_output_s": 5.0, "duration_s": 2.0},
               {"id": "ins2", "at_output_s": 5.0, "duration_s": 2.0}]
    positions = Timeline([[0.0, 12.0]], inserts, []).insert_positions()
    tuples = sorted((float(i["at_output_s"]), float(i["duration_s"]))
                    for i in inserts)
    mine = (5.0, 2.0)
    idx = len(tuples) - 1 - tuples[::-1].index(mine)
    assert (round(positions[idx][0], 2),
            round(positions[idx][0] + positions[idx][1], 2)) == (7.0, 9.0)
