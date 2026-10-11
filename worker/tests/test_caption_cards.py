"""Captions inside picture cards (Diamandis podcast run, Oct 2026).

Reviewers killed or held 6 of 9 shorts over captions in a card:
  - s08: 'clean' captions with anchor_y 0.735 under Elon's chin landed on
    his mouth (y ~0.56) at 7.2 s and 23.2 s, elsewhere over his hair: the
    plan priced a caption as a block CAP_HALF_H either side of its anchor,
    which brushed the card's bottom edge, then took the largest free band
    above a talking-head PRIOR face (nothing measured near the moment);
  - s02: 'scenario' was muted twice under swap slams in the top band that
    never touched the captions, and every graphic write claimed the
    captions sat on its band ('false collisions');
  - s06: caption lines ran past the card's sides.

What is pinned here (worker/caption_carry.plan, worker/caption_place.py,
worker/motion_captions.py, worker/motion/templates/caption_motion.html):
  1. An anchor_y inside a card stays on it: its block, measured as the
     caption track lays it out, moves at most NUDGE_LINES lines to clear the
     card's edge or a measured chin — in pages of one line where the card's
     foot holds one — and the talking-head prior never moves an anchor the
     editor set.
  2. A face measured in the same source shot speaks for it (within
     FACE_SHOT_S): the captions keep below its chin.
  3. A measured face alone never sends an explicit anchor to a band over
     the head: it stays, and the plan names it (unplaced, why 'face').
  4. Words are muted only where a graphic that replaces speech covers the
     caption block; a graphic elsewhere is not named as touching them.
  5. Lines keep to the card's inner column (cue ``x``), usual place or
     moved; hero ladders (lockup/stack) keep the template's column.
  6. The render agrees with the plan: the browser lays every cue inside its
     zone and column, and one line where the plan said one line.
"""

import asyncio
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import caption_carry  # noqa: E402
import caption_place  # noqa: E402
import captions  # noqa: E402
import motion_captions  # noqa: E402
import motion_engine  # noqa: E402
import motion_templates  # noqa: E402
import motion_tools  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

W, H = 1080, 1920
AR = caption_carry.frame_ar(W, H)
DUR = 12.0
# s08: a program card on a 9:16 crop of a 16:9 stage feed (the phone call)
S08_CARD = [0.095, 0.13, 0.905, 0.795]
# s02/s06: source-fed cards on the call window, the face filling them
S02_CARD, S02_SRC = [0.19, 0.245, 0.81, 0.80], [0.447, 0.215, 0.614, 0.69]
S06_CARD, S06_SRC = [0.14, 0.235, 0.86, 0.794], [0.385, 0.215, 0.615, 0.779]
WORDS = [("at", 0.30, 0.42), ("the", 0.42, 0.52), ("point", 0.52, 0.80), ("at", 0.80, 0.90),
         ("which", 0.90, 1.20), ("you", 1.30, 1.42), ("have", 1.42, 1.60), ("say", 1.60, 1.90),
         ("advanced", 2.00, 2.50), ("robotics.", 2.50, 3.10),
         ("in", 3.60, 3.70), ("being", 3.70, 3.95), ("actually", 3.95, 4.40),
         ("remarkably", 4.40, 5.00), ("accurate.", 5.00, 5.60),
         ("the", 6.00, 6.10), ("positive", 6.10, 6.60), ("scenario", 6.60, 7.20),
         ("outweighs", 7.30, 7.90), ("the", 8.00, 8.10), ("negative", 8.10, 8.60),
         ("scenario.", 8.60, 9.30), ("that", 9.80, 9.95), ("is", 9.95, 10.05),
         ("my", 10.05, 10.20), ("view.", 10.20, 10.80)]
# a swap slam in the headline band: shows 'positive', far from the captions
SWAP = {"id": "swap", "template": "word_slam", "start": 5.9, "end": 7.25,
        "params": {"text": "POSITIVE", "y": 0.18},
        "footprint": caption_carry.make_footprint([0.24, 0.13, 0.76, 0.23], W, H)}


def _w(rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


def _edl(card, source=None, anchor=0.735, look="clean", motion=(SWAP,), track=None,
         frame_faces=None):
    edl = default_edl(DUR)
    edl["frame"] = {"ratio": "9:16", "mode": "crop", "focus_x": 0.5, "focus_y": 0.46}  # s08's
    style = {"motion_look": look} if look else {"preset": "stacked"}
    if anchor is not None:
        style["anchor_y"] = anchor
    edl["captions"] = {"mode": "from_transcript", "design_version": 2, "style": style}
    if track:
        edl["captions"]["placement_track"] = track
    c = {"id": "card", "start": 0.0, "end": DUR, "box": list(card),
         "entrance": "none", "exit": "none"}
    if source:
        c["source"] = list(source)
    edl["effects"] = dict(edl.get("effects") or {}, picture_cards=[c])
    edl["motion"] = [dict(m) for m in motion]
    edl = validate_edl(edl, DUR).model_dump()
    if frame_faces:
        # the write-time keep-out's zones, measured on the picture as it is
        geo = caption_carry.face_geometry(edl)
        for m in edl["motion"]:
            m["footprint"] = dict(m["footprint"], faces=[list(f) for f in frame_faces],
                                  geo=geo)
    return edl


def _index(faces=(), shots=None, at=(0.5, 3.0, 6.0, 9.0, 11.5)):
    samples = [{"t": t, "faces": [list(f) for f in faces], "text": []} for t in at]
    ix = {"video": {"width": 1920, "height": 1080, "fps": 30.0}, "words": _w(WORDS),
          "spatial": {"samples": samples}}
    if shots is not None:
        ix["shots"] = shots
    return ix


def _plan(edl, ix):
    return captions.caption_plan(edl, ix, Timeline(edl["keep"]), canvas=(W, H))


def _cues(edl, ix):
    return motion_captions.cues(edl, ix, Timeline(edl["keep"]), canvas=(W, H))


def _shown(p):
    return [w["w"] for w in p.caption_words()]


# ── 1. an anchor in a card stays on it ───────────────────────────────────

# s08's equation slam over the card's foot, on the anchor (it shows its word)
EQ = {"id": "eq", "template": "word_slam", "start": 3.5, "end": 5.7,
      "params": {"text": "ACCURATE", "y": 0.7},
      "footprint": caption_carry.make_footprint([0.1352, 0.633, 0.8674, 0.7688], W, H)}


def test_a_locked_anchor_under_the_chin_stays_there_not_on_the_mouth():
    """s08: nothing measured near the moment (a 30-minute feed sampled every
    ~22 s) — the talking-head prior put the chin at y 0.49 of the card, so
    while a slam covered the anchor the captions took the band between that
    chin and the slam, Elon's mouth (y 0.56), and stayed there after it. The
    anchor is the editor's: it holds, its two-line block raised a hair to
    clear the card's foot, and the slam sends the captions above the head."""
    edl, ix = _edl(S08_CARD, motion=(SWAP, EQ)), _index(faces=())
    cues = _cues(edl, ix)
    assert cues and not [c for c in cues if 0.42 < c["y"] < 0.7], cues   # the mouth
    under = [c for c in cues if 3.5 <= c["s"] < 5.7]
    assert under and all(c["z"][1] <= 0.633 for c in under), under
    cues = [c for c in cues if c not in under]
    for c in cues:
        assert c["y"] == pytest.approx(0.735), c
        z0, z1 = c["z"]
        assert 0.69 <= z0 <= 0.735 and z1 <= S08_CARD[3] - caption_place.EDGE_PAD + 1e-4, c
        assert c.get("h") == 1
        assert c["x"] == [pytest.approx(S08_CARD[0] + caption_place.CARD_SIDE_PAD),
                          pytest.approx(S08_CARD[2] - caption_place.CARD_SIDE_PAD)]
    assert not _plan(edl, ix).unplaced


def test_the_block_is_measured_as_the_caption_track_lays_it_out():
    one, tall, motion = caption_place.block_metrics(_edl(S08_CARD), W, H)
    assert motion and one == pytest.approx(70 * 1.16 / 1920, rel=1e-3)
    assert 1.9 * one < tall < 2.2 * one              # two lines and the ink slack
    # the first line's row sits ON the anchor; the block grows down
    top, bot = caption_place.natural_block(0.735, one, tall)
    assert top == pytest.approx(0.735 - one / 2) and bot == pytest.approx(top + tall)
    # the card's foot (its edge less EDGE_PAD) stops it: raised as far as it needs
    spot = caption_place.anchor_spot([(0.15, 0.783)], 0.735, one, tall)
    assert spot[1] == pytest.approx(0.783) and spot[1] - spot[0] == pytest.approx(tall, abs=1e-3)
    # an obstacle above the row (a chin at 0.729) lowers it; the foot then
    # holds one line: the zone is that line's, never a squeezed two-line block
    spot = caption_place.anchor_spot([(0.729, 0.783)], 0.735, one, tall)
    assert spot == (0.729, 0.783)
    # an explicit anchor rises at most LOCKED_LIFT_LINES for its tallest
    # page: past that, pages of one line, their row risen only as far as one
    # line needs (not the 0.041 the two-line block would)
    lift = caption_place.LOCKED_LIFT_LINES * one
    spot = caption_place.anchor_spot([(0.15, 0.788)], 0.765, one, tall, lift=lift)
    assert spot == (pytest.approx(0.788 - caption_place.ONE_LINE_SLACK * one, abs=1e-4), 0.788)
    assert 0.765 - one / 2 - spot[0] < 0.005 and spot[1] - spot[0] < tall
    # never more than NUDGE_LINES lines off its row
    assert caption_place.anchor_spot([(0.2, 0.62)], 0.735, one, tall) is None
    assert caption_place.anchor_spot([(0.76, 0.78)], 0.735, one, tall) is None


# ── 2. a face measured in the same shot speaks for it ───────────────────

# Elon's face as the index measured it on the stage feed (source fractions),
# as low in this unzoomed fixture as s08's 1.22x framing zoom set it: its
# chin at y ~0.725 of the frame, under the anchor's row
S08_FACE = [0.4278, 0.43, 0.6194, 0.78]


def test_a_face_measured_in_the_same_shot_keeps_the_captions_below_its_chin():
    shots = [{"id": 1, "start": 0.0, "end": 60.0}]
    edl = _edl(S08_CARD)
    ix = _index(faces=[S08_FACE], shots=shots, at=(40.0,))     # 30-40 s from every word
    tl = Timeline(edl["keep"])
    zones = caption_carry._faces_in_shot(edl, ix, tl, 0.3, 1.2, W, H)
    assert zones                      # beyond FACE_FAR_S, inside the shot
    chin = max(caption_place.chin(z)[3] for z in zones)
    assert 0.7 < chin < 0.74           # s08: 0.729 — the prior's was 0.49
    cues = _cues(edl, ix)
    for c in cues:
        assert c["z"][0] >= chin - 1e-3 and c["y"] == pytest.approx(0.735), c
        assert c.get("n") == 1        # the foot under the chin holds one line
    # pages of one line are cut to what one line of the card's column holds
    budget = caption_place.line_chars(edl, W, H, c["x"])
    assert all(len(" ".join(w["t"] for w in c["w"])) <= budget + 6 for c in cues)
    # another shot, or a sample further than FACE_SHOT_S, speaks for nothing
    far = _index(faces=[S08_FACE], shots=[{"id": 1, "start": 0.0, "end": 200.0}],
                 at=(0.3 + caption_carry.FACE_SHOT_S + 5,))
    assert not caption_carry._faces_in_shot(edl, far, tl, 0.3, 1.2, W, H)
    other = _index(faces=[S08_FACE], shots=[{"id": 1, "start": 0.0, "end": 20.0},
                                            {"id": 2, "start": 20.0, "end": 60.0}], at=(40.0,))
    assert not caption_carry._faces_in_shot(edl, other, tl, 0.3, 1.2, W, H)


# ── 3. a measured face never sends an explicit anchor over the head ──────

# the write-time keep-out's zones on s02: the face fills the card to its foot
S02_FACES = [[0.19, 0.361, 0.81, 0.80]]


def test_a_face_filling_the_card_keeps_the_anchor_and_says_so():
    edl = _edl(S02_CARD, S02_SRC, anchor=0.765, frame_faces=S02_FACES)
    p = _plan(edl, _index(faces=()))
    cues = _cues(edl, _index(faces=()))
    assert cues and all(c["y"] == pytest.approx(0.765) and c["z"][0] > 0.73 for c in cues), cues
    assert all(c.get("n") == 1 for c in cues)          # one line, its row on the anchor
    assert p.unplaced and {u["why"] for u in p.unplaced} == {"face"}
    assert all(u["face_to"] >= 0.79 for u in p.unplaced)
    line = " ".join(caption_carry.unplaced_lines(p))
    assert "CAPTIONS ON THE MEASURED FACE" in line and "anchor_y 0.77" in line
    # ...in the render report too (an advisory: the captions are on screen)
    import render_qc
    tplan = render_qc._type_plan(edl, _index(faces=()), Timeline(edl["keep"]), W, H, 30.0, DUR)
    assert any("CAPTIONS ON THE MEASURED FACE" in u for u in tplan["unplaced"])
    assert any("CAPTIONS ON THE MEASURED FACE" in a
               for a in render_qc.advice({"unplaced": tplan["unplaced"]}))
    # the product's own placement (no anchor set) may still move them
    free = _edl(S02_CARD, S02_SRC, anchor=None, frame_faces=S02_FACES)
    assert not [u for u in _plan(free, _index(faces=())).unplaced if u["why"] == "face"]


# Review (Oct 2026), on the production 'composed' cards (94 EDLs with an
# anchor_y inside a card, read-only): kept on the measured face, twice as many
# words sat on a face as before. A measured face reaching the anchor sends
# the captions past it on the anchor's own side inside the card, else to
# the canvas around the card — never across the face (mouth) or over the
# head inside the card; only with neither left do they stay, named.
WIDE_CARD = [0.04, 0.3, 0.96, 0.72]          # a landscape picture in a 9:16 frame
SHOT = [{"id": 1, "start": 0.0, "end": 60.0}]
HEADLINE = {"id": "headline", "template": "headline", "start": 0.0, "end": DUR,
            "params": {"text": "A HEADLINE"},
            "footprint": caption_carry.make_footprint([0.06, 0.10, 0.94, 0.29], W, H)}


def _composed(edl, anchor):
    edl["captions"]["style"] = {"preset": "composed", "anchor_y": anchor}
    return edl


@pytest.mark.parametrize("look", ["clean", None])
def test_a_face_filling_the_card_sends_the_captions_to_the_canvas_not_onto_it(look):
    edl = _edl(WIDE_CARD, anchor=0.655, look=look, motion=())
    if look is None:
        _composed(edl, 0.655)
    ix = _index(faces=[[0.40, 0.30, 0.60, 0.80]], shots=SHOT)
    p = _plan(edl, ix)
    places = {str(pl) for pl in p.placed.values()}
    assert len(places) == 1 and not p.unplaced
    pl = next(iter(p.placed.values()))
    # on the canvas above the card, clear of its edge: never inside it
    assert pl["z"][1] <= WIDE_CARD[1] - caption_place.EDGE_PAD + 1e-4 and "x" not in pl, pl
    assert len(p.placed) == len(p.words)                 # every heard word shown
    # with the canvas taken (a standing headline) they keep the anchor, named
    edl = _edl(WIDE_CARD, anchor=0.655, look=look, motion=(HEADLINE,))
    if look is None:
        _composed(edl, 0.655)
    p = _plan(edl, ix)
    assert p.unplaced and {u["why"] for u in p.unplaced} == {"face"}
    assert all(WIDE_CARD[1] < pl["z"][0] and pl["z"][1] <= WIDE_CARD[3] for pl in p.placed.values())


@pytest.mark.parametrize("look", ["clean", None])
def test_a_measured_face_over_the_anchor_moves_it_under_the_chin_never_across(look):
    """An anchor set on the face (0.5 of a tall card): the captions go under
    the measured chin, however far — never above the brow (the hair) or
    left on the face."""
    card = [0.0, 0.1, 1.0, 0.9]
    edl = _edl(card, anchor=0.5, look=look, motion=())
    if look is None:
        _composed(edl, 0.5)
    ix = _index(faces=[[0.42, 0.15, 0.58, 0.55]], shots=SHOT)
    tl = Timeline(edl["keep"])
    chin = max(caption_place.chin(z)[3]
               for z in caption_carry.faces_over(edl, ix, tl, 0.3, 1.2, W, H))
    p = _plan(edl, ix)
    assert not p.unplaced and p.placed
    for pl in p.placed.values():
        assert pl["z"][0] >= chin - 1e-4 and pl["z"][1] <= card[3], (pl, chin)
        assert pl["z"][0] - 1e-4 <= pl["y"] <= pl["z"][1] + 1e-4
    # an anchor set above the face's middle rises above its brow, whole
    edl = _edl(card, anchor=0.45, look=look, motion=())
    if look is None:
        _composed(edl, 0.45)
    ix = _index(faces=[[0.44, 0.45, 0.56, 0.8]], shots=SHOT)
    brow = min(z[1] for z in caption_carry.faces_over(edl, ix, tl, 0.3, 1.2, W, H))
    p = _plan(edl, ix)
    one, tall, _m = caption_place.block_metrics(edl, W, H)
    assert not p.unplaced and p.placed
    for pl in p.placed.values():
        assert pl["z"][1] <= brow + 1e-4 and pl["z"][1] - pl["z"][0] >= tall - 1e-3, (pl, brow)
        assert not pl.get("l")


def test_libass_captions_on_their_card_anchor_keep_the_opening_lead_in_and_their_row():
    """The libass 'composed' cards: their first caption still starts on the
    opening frame (FIRST_CAPTION_LEAD_IN_S — the card's own place is no
    window a page may not hold into), and an anchor near the frame's foot is
    not lifted to the motion template's safe area toward the face."""
    ix = _index(faces=())
    for card, anchor in ((WIDE_CARD, 0.655), ([0.0, 0.25, 1.0, 0.875], 0.8)):
        edl = _composed(_edl(card, anchor=anchor, look=None, motion=()), anchor)
        tl = Timeline(edl["keep"])
        p = _plan(edl, ix)
        assert not p.clamp_spans
        events, _style = captions.compiled_events(edl, ix, tl, play_res=(W, H))
        assert events and events[0]["start"] == 0.0
        one, tall, _m = caption_place.block_metrics(edl, W, H)
        for pl in p.placed.values():
            # its row: the anchor, raised only as far as the card's foot needs
            assert pl["y"] == pytest.approx(min(anchor, card[3] - caption_place.EDGE_PAD
                                                - tall / 2), abs=1e-3), pl
    # a graphic over the card's foot still keeps a page from holding into it
    low = dict(SWAP, id="low", start=0.0, footprint=caption_carry.make_footprint(
        [0.1, 0.55, 0.9, 0.71], W, H))
    edl = _composed(_edl(WIDE_CARD, anchor=0.655, look=None, motion=(low,)), 0.655)
    assert _plan(edl, ix).clamp_spans


def test_a_page_of_one_line_holds_what_one_line_of_its_face_holds(monkeypatch):
    """s08 (Inter Display Black, capitals, a 0.74 column): the flat 0.7 em
    price cut 'TESLA'S DEVELOPING' into two one-word pages, the second up
    for 0.21 s. The look's face is measured (worker/band_type.py)."""
    edl = _edl(S08_CARD)
    edl["captions"]["style"].update(font="Inter Display Black", uppercase=True)
    col = (0.13, 0.87)
    measured = caption_place.line_chars(edl, W, H, col)
    # (Chromium lays 'TESLA'S DEVELOPING' 0.674 of the frame wide: 18 fit)
    assert measured >= len("TESLA'S DEVELOPING") - 1
    # sentence case holds more than capitals
    plain = _edl(S08_CARD)
    assert caption_place.line_chars(plain, W, H, col) > measured
    # no measurable face (a lane without the font or Pillow): the flat price
    import band_type
    monkeypatch.setattr(band_type, "font_path", lambda *a, **k: None)
    flat = caption_place.line_chars(edl, W, H, col)
    one = caption_place.line_height(edl, W, H)
    fs = one / caption_place.LOOK_LINE["clean"][1]
    assert flat == int(0.74 * W / (caption_place.CAPS_EM * fs * H)) < measured


# ── 4. muted only where a graphic truly covers them ──────────────────────

def test_a_slam_in_the_headline_band_mutes_nothing_and_claims_no_collision():
    """s02: 'scenario' heard but never shown, twice; the graphic's write said
    the captions sat on its band. The slam sits at y 0.13-0.23, the captions
    at 0.765 under the chin."""
    edl = _edl(S02_CARD, S02_SRC, anchor=0.765, frame_faces=S02_FACES)
    ix = _index(faces=())
    p = _plan(edl, ix)
    assert "scenario" in _shown(p) and not [h for h in p.hidden.values() if h[1] != "carried"]
    rep = p.report["swap"]
    assert rep["muted"] == [] and rep["kept"] == []
    tl = Timeline(edl["keep"])
    notes = " ".join(motion_tools._word_level_notes(edl, ix, tl, edl["motion"][0],
                                                    canvas=(W, H)))
    assert "muted" not in notes and "touch it" not in notes and "move to y" not in notes
    assert not caption_carry.heard_unshown(edl, ix, tl)


def test_a_graphic_covering_the_captions_still_moves_them_or_mutes_and_names_them():
    # a slam over the card's foot: the captions move clear of it
    low = dict(SWAP, id="low", footprint=caption_carry.make_footprint(
        [0.15, 0.66, 0.85, 0.79], W, H))
    edl, ix = _edl(S08_CARD, motion=(low,)), _index(faces=())
    p = _plan(edl, ix)
    moved = [p.placed[i] for i, w in enumerate(p.words) if 6.0 <= w["t0"] < 7.25
             and i not in p.hidden]
    assert moved and all(pl["z"][1] <= 0.66 for pl in moved)
    # one over the whole frame, replacing speech: muted, and named
    wall = dict(SWAP, id="wall", footprint=caption_carry.make_footprint(
        [0.0, 0.0, 1.0, 1.0], W, H))
    p = _plan(_edl(S08_CARD, motion=(wall,)), ix)
    muted = [p.words[i]["w"] for i, h in p.hidden.items() if h == ("wall", "room")]
    assert "scenario" in muted and p.report["wall"]["muted"]


# ── 5. lines keep to the card's column ───────────────────────────────────

def test_lines_keep_to_the_cards_column_in_their_usual_place_too():
    """s06: 'in being actually remarkably' spanned x 0.09-0.905 over a card
    0.14-0.86 wide."""
    edl = _edl(S06_CARD, S06_SRC, anchor=0.762, motion=())
    cues = _cues(edl, _index(faces=()))
    col = [S06_CARD[0] + caption_place.CARD_SIDE_PAD, S06_CARD[2] - caption_place.CARD_SIDE_PAD]
    assert cues and all(c["x"] == pytest.approx(col) for c in cues)
    # captions on the canvas below a short card keep the frame's column
    short = _edl([0.1, 0.2, 0.9, 0.6], anchor=0.75, motion=())
    assert all("x" not in c for c in _cues(short, _index(faces=())))
    # a hero ladder is a poster across the card by design
    stack = _edl(S06_CARD, S06_SRC, anchor=0.762, look="stack", motion=())
    assert all("x" not in c for c in _cues(stack, _index(faces=())))


def test_home_rect_is_the_card_holding_the_anchor():
    col = caption_place.column(W, H)
    card = tuple(S08_CARD)
    assert caption_place.home_rect([card], 0.735, col) == card
    assert caption_place.home_rect([card], 0.79, col) is None        # on its edge
    assert caption_place.home_rect([(0.05, 0.2, 0.3, 0.8)], 0.5, col) is None  # beside
    assert caption_place.card_column(card, W, H) == (0.13, 0.87)
    # never wider than the template's own column
    assert caption_place.card_column((0.0, 0.1, 1.0, 0.9), W, H) == \
        pytest.approx(caption_place.template_column(W, H), abs=1e-4)


# ── 6. the render agrees with the plan ───────────────────────────────────

def _chromium_ok():
    if not motion_engine.available():
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch(args=motion_engine.CHROME_ARGS).close()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_browser = pytest.mark.skipif(not (shutil.which("ffmpeg") and _chromium_ok()),
                                   reason="headless Chromium + ffmpeg required")

_INK = """() => Array.from(document.querySelectorAll('.cue.on .mg-w')).map(e => {
    const r = e.getBoundingClientRect();
    return [r.left, r.top, r.right, r.bottom, parseFloat(getComputedStyle(e).fontSize)]; })"""


async def _dom(job, times, script):
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=motion_engine.CHROME_ARGS)
        dw, dh = motion_engine.design_size(job.out_w, job.out_h)
        ctx = await browser.new_context(viewport={"width": dw, "height": dh})
        await ctx.route("**/*", await motion_engine._route_factory(job))
        page = await ctx.new_page()
        await page.goto(motion_engine.ORIGIN + "/")
        await page.evaluate("async () => { await document.fonts.ready; await MG.ready; }")
        for t in times:
            await page.evaluate("t => window.__mgSeek(t)", t)
            out.append(await page.evaluate(script))
        await browser.close()
    return out


@needs_browser
@pytest.mark.parametrize("case", ["s08_prior", "s08_eq", "s08_chin", "s06_face",
                                  "s07_editorial"])
def test_the_browser_lays_every_cue_inside_the_plans_zone_and_column(case, tmp_path,
                                                                     monkeypatch):
    monkeypatch.setattr(motion_engine, "CACHE_DIR", str(tmp_path / "cache"))
    shots = [{"id": 1, "start": 0.0, "end": 60.0}]
    edl, ix = {
        "s08_prior": lambda: (_edl(S08_CARD), _index(faces=())),
        # the slam sends a page to a band one line tall above the head: the
        # template lays it there, not in the whole frame
        "s08_eq": lambda: (_edl(S08_CARD, motion=(SWAP, EQ)), _index(faces=())),
        "s08_chin": lambda: (_edl(S08_CARD), _index(faces=[S08_FACE], shots=shots, at=(40.0,))),
        "s06_face": lambda: (_edl(S06_CARD, S06_SRC, anchor=0.762, motion=(),
                                  frame_faces=[[0.162, 0.235, 0.86, 0.763]]),
                             _index(faces=())),
        "s07_editorial": lambda: (_edl([0.236, 0.215, 0.764, 0.795], [0.447, 0.24, 0.617, 0.83],
                                       anchor=0.745, look="editorial", motion=()),
                                  _index(faces=[S08_FACE], shots=shots, at=(40.0,))),
    }[case]()
    tl = Timeline(edl["keep"])
    items = motion_captions.items(edl, ix, tl, canvas=(W, H))
    one = caption_place.line_height(edl, W, H)
    checked = 0
    for it in items:
        job = motion_templates.build_job(it, W, H, 30)
        cues = it["params"]["cues"]
        ts = [round(c["w"][-1]["s"] + 0.15, 3) for c in cues]
        for c, boxes in zip(cues, asyncio.run(_dom(job, ts, _INK))):
            assert boxes, (case, c)
            x0 = min(b[0] for b in boxes) / W
            x1 = max(b[2] for b in boxes) / W
            y0 = min(b[1] for b in boxes) / H
            y1 = max(b[3] for b in boxes) / H
            z0, z1 = c["z"]
            # the line boxes inside the plan's zone (a word box carries its
            # line's leading: a hair of slack) and the card's column
            assert z0 - 0.004 <= y0 and y1 <= z1 + 0.004, (case, c, (y0, y1))
            assert c["x"][0] - 0.003 <= x0 and x1 <= c["x"][1] + 0.003, (case, c, (x0, x1))
            assert c.get("h") == 1, c
            if c.get("n"):
                assert y1 - y0 <= 1.25 * one, (case, c, (y0, y1))   # one line
                # ...at the look's size: a page of one line is cut to what
                # one line holds, never shrunk to fit (its sans words)
                base = caption_place.LOOK_LINE[motion_captions.look_of(edl)][0]
                assert min(b[4] for b in boxes) >= 0.9 * base, (case, c, boxes)
            checked += 1
    assert checked >= 6
