"""The caption placement solver (showcase judging, Oct 2026, round 4).

What is pinned here (worker/caption_place.py through caption_carry.plan):
  1. Card and panel edges and the seams between stacked panels are no-go:
     a band shorter than ~1.4 caption lines is no band, and a placement span
     pinned between two panels (Elon: y 0.585 in a 55 px seam) moves to the
     largest free band — the empty band above the speaker panel — clear of
     the free-tier watermark's corner.
  2. A stack's content panel (no face in its source rect while the other
     panel has one: the screen share) never takes a caption.
  3. The face blocks with its chin; write-time zones and the index's face
     evidence both count (Haar misses profiles).
  4. Placement is re-solved at every layout change: a page never carries
     its place across a card's start or end onto the new layout, a word said
     within two frames before the change appears ON it (never later), and
     one or two words stranded by the change join the rest of their line.
  5. The 9:16 caption column keeps out of the ~9% side crop.
  6. Connector pages of one word ('and', 'that', 'on') join their line.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import caption_carry  # noqa: E402
import caption_place  # noqa: E402
import captions  # noqa: E402
import motion_captions  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402
from timeline import Timeline  # noqa: E402

AR = round(1080 / 1920, 4)
# The Elon stack: the speaker (Rogan) on top, the screen share below, with a
# 0.03 seam between them; both from one 16:9 camera frame with the browser
# burned in at its lower right.
SPEAKER = [0.04, 0.287, 0.96, 0.57]
SCREEN = [0.0573, 0.6, 0.9427, 0.885]
SPEAKER_SRC = [0.155, 0.168, 0.595, 0.5958]
SCREEN_SRC = [0.5312, 0.5167, 0.9844, 0.9778]
ROGAN_FACE = [0.3833, 0.2178, 0.5861, 0.5792]          # source fractions (16:9)

WORDS = [("any", 0.5, 0.6), ("kind", 0.6, 0.74), ("of", 0.74, 0.86), ("fast", 0.86, 1.12),
         ("reaction", 1.95, 2.35), ("video", 2.35, 2.55), ("games.", 2.55, 2.9),
         ("than", 4.0, 4.38), ("their", 4.38, 4.6), ("non-player", 4.6, 5.16),
         ("colleagues.", 5.16, 5.62), ("Oh,", 5.75, 5.89), ("I", 5.93, 5.99),
         ("believe", 5.99, 6.17), ("that", 6.17, 6.35), ("for", 6.36, 6.56),
         ("sure.", 6.56, 6.72), ("That's", 7.0, 7.08), ("incredible.", 7.08, 7.56)]
CARD = (2.0, 6.4)        # the stack's program window


def _w(rows):
    return [{"w": w, "t0": a, "t1": b} for w, a, b in rows]


def _stack(start=CARD[0], end=CARD[1]):
    return {"id": "stack", "start": start, "end": end, "box": [0.04, 0.287, 0.96, 0.885],
            "entrance": "none", "exit": "none",
            "panels": [{"box": SPEAKER, "source": SPEAKER_SRC},
                       {"box": SCREEN, "source": SCREEN_SRC}]}


def _edl(cards=(), motion=(), track=None, look="editorial", words=WORDS, dur=9.0):
    edl = default_edl(dur)
    edl["frame"] = {"ratio": "9:16", "mode": "crop"}
    edl["captions"] = {"mode": "from_transcript", "design_version": 2,
                       "style": {"motion_look": look} if look else {"preset": "stacked"}}
    if track:
        edl["captions"]["placement_track"] = track
    edl["effects"] = dict(edl.get("effects") or {}, picture_cards=[dict(c) for c in cards])
    edl["motion"] = [dict(m) for m in motion]
    return validate_edl(edl, dur).model_dump()


def _index(faces=True, words=WORDS):
    samples = [{"t": t, "faces": [ROGAN_FACE] if faces else [], "text": []}
               for t in (0.5, 2.5, 4.5, 6.5, 8.0)]
    return {"video": {"width": 1920, "height": 1080, "fps": 30.0}, "words": _w(words),
            "spatial": {"samples": samples}}


def _plan(edl, ix):
    return captions.caption_plan(edl, ix, Timeline(edl["keep"]), canvas=(1080, 1920))


# the agent's explicit placement span: 'between the speaker and the screen'
BETWEEN = [{"t0": 1.9, "t1": 6.4, "position": "middle", "anchor_y": 0.585,
            "reason": "between the speaker and the screen"}]


# ── 1. edges, seams and the largest free band ────────────────────────────

def test_a_seam_is_no_band_and_edges_are_hard():
    col = caption_place.column(1080, 1920)
    edges = caption_place.edge_zones([tuple(SPEAKER), tuple(SCREEN)], col)
    bands = caption_place.free_bands(edges, (0.08, 0.80), col)
    seam = [b for b in bands if 0.57 <= b[0] < b[1] <= 0.60]
    assert seam and all(b - a < caption_place.min_band(_edl(), 1080, 1920) for a, b in seam)
    # the editorial look's line is ~4% of the frame: 1.4 lines is the floor
    lh = caption_place.line_height(_edl(), 1080, 1920)
    assert 0.035 < lh < 0.05
    assert caption_place.min_band(_edl(), 1080, 1920) == pytest.approx(1.4 * lh)
    # a block touching an edge band is blocked; one inside a panel is not
    assert caption_place.hits(0.53, 0.64, edges, col)
    assert not caption_place.hits(0.35, 0.45, edges, col)
    # side-by-side panels: a vertical edge through the column blocks the window
    side = caption_place.edge_zones([(0.04, 0.3, 0.5, 0.7), (0.5, 0.3, 0.96, 0.7)], col)
    assert caption_place.hits(0.45, 0.55, side, col)


def test_the_largest_free_band_wins_and_a_free_previous_band_holds():
    bands = [(0.13, 0.275), (0.30, 0.34), (0.612, 0.80)]
    y, a, b, _s = caption_place.choose(bands, 0.585, 0.055, 0.057)
    assert (a, b) == (0.612, 0.80)                       # largest, and near
    # source text under it (a screen) costs: the canvas band wins then
    text = [(0.1, 0.62, 0.9, 0.79)]
    y, a, b, _s = caption_place.choose(bands, 0.585, 0.055, 0.057, soft=text)
    assert (a, b) == (0.13, 0.275)
    # the previous page's band, still free, is kept (no needless jump)
    y2, a2, b2, s2 = caption_place.choose(bands, 0.585, 0.055, 0.057, prev=(0.22, 0.13, 0.275))
    assert y2 == 0.22 and s2 == float("inf")
    # nothing tall enough: no band
    assert caption_place.choose([(0.57, 0.6)], 0.585, 0.055, 0.057) is None


def test_captions_pinned_between_stacked_panels_move_to_the_band_above():
    edl = _edl([_stack()], track=BETWEEN)
    ix = _index()
    p = _plan(edl, ix)
    wm = caption_place.watermark_box(1080, 1920)
    lh = caption_place.line_height(edl, 1080, 1920)
    inside = [i for i, w in enumerate(p.words) if CARD[0] <= w["t0"] < CARD[1] - 0.1]
    assert inside and all(i in p.placed for i in inside)
    for i in inside:
        pl = p.placed[i]
        z0, z1 = pl["z"]
        # above the speaker panel's top edge, below the watermark
        assert z1 <= SPEAKER[1] - caption_place.EDGE_PAD + 1e-6
        assert z0 >= wm[3] - 1e-6
        assert z1 - z0 >= 1.4 * lh - 1e-6
        assert pl["b"] == "t"
    tl = Timeline(edl["keep"])
    for c in motion_captions.cues(edl, ix, tl, canvas=(1080, 1920)):
        if CARD[0] <= c["s"] < CARD[1]:
            assert c["y"] < SPEAKER[1] and c.get("z"), c
            assert not (0.57 - 0.06 < c["y"] < 0.60 + 0.06)       # never the seam


# ── 2. content panels ─────────────────────────────────────────────────────

def test_a_stacks_screen_panel_is_content_and_never_takes_a_caption():
    card = _stack()
    ix = _index()
    zones = caption_place.content_zones([card], ix, Timeline([[0.0, 9.0]]), 2.0, 6.4)
    assert len(zones) == 1 and zones[0][1] < SCREEN[1] < SCREEN[3] < zones[0][3]
    # no face anywhere near: the panels cannot be told apart, nothing is hard
    assert caption_place.content_zones([card], _index(faces=False),
                                       Timeline([[0.0, 9.0]]), 2.0, 6.4) == []
    edl = _edl([card], track=BETWEEN)
    p = _plan(edl, ix)
    assert all(not (SCREEN[1] <= pl["y"] <= SCREEN[3]) for pl in p.placed.values())


# ── 3. faces ─────────────────────────────────────────────────────────────

def test_the_chin_and_both_face_sources_count():
    z = (0.3, 0.2, 0.7, 0.5)
    assert caption_place.chin(z)[3] == pytest.approx(0.5 + caption_place.CHIN_PAD * 0.3)
    # a write-time zone that missed the turned head does not hide the index's
    edl = _edl([_stack()])
    stale = {"id": "s", "template": "word_slam", "start": 2.0, "end": 6.4,
             "params": {"text": "*32%*"}, "footprint": {
                 "box": [0.29, 0.10, 0.71, 0.25], "ar": AR,
                 "faces": [[0.04, 0.49, 0.13, 0.57]],
                 "geo": caption_carry.face_geometry(edl)}}
    edl = _edl([_stack()], motion=[stale])
    tl = Timeline(edl["keep"])
    faces = caption_carry.faces_over(edl, _index(), tl, 2.0, 6.3, 1080, 1920, edl["motion"])
    assert (0.04, 0.49, 0.13, 0.57) in faces
    assert any(f[1] < 0.45 and f[2] > 0.5 for f in faces)       # Rogan's head, from the index


# ── 4. layout changes ────────────────────────────────────────────────────

def test_a_page_never_carries_its_place_across_a_layout_change():
    edl = _edl([_stack()], track=BETWEEN)
    ix = _index()
    tl = Timeline(edl["keep"])
    p = _plan(edl, ix)
    cues = motion_captions.cues(edl, ix, tl, canvas=(1080, 1920))
    for c in cues:
        words_on = [w for w in c["w"]]
        start, end = c["s"], c["e"]
        in_card = CARD[0] - 1e-6 <= start < CARD[1]
        # a page placed for the stack ends by the stack's end; one in the
        # usual place before it ends by the stack's start
        if in_card:
            assert end <= CARD[1] + 1e-6 or words_on[-1]["s"] >= CARD[1] - 1e-6, c
        elif start < CARD[0]:
            assert end <= CARD[0] + 1e-6, c
    # the stretch after the stack keeps the usual place (not the pinned span)
    after = [c for c in cues if c["s"] >= CARD[1]]
    assert after and all(c["y"] > 0.7 and "z" not in c for c in after)
    assert p.hold_limit(1.0, None) == pytest.approx(CARD[0])


def test_a_word_said_just_before_a_layout_change_appears_on_it(monkeypatch):
    words = [("any", 0.5, 0.6), ("kind", 0.6, 0.74), ("of", 0.74, 0.86), ("fast", 0.86, 1.95),
             ("reaction", 1.95, 2.35), ("video", 2.35, 2.55), ("games.", 2.55, 2.9)]
    edl = _edl([_stack()], track=BETWEEN, words=words)
    ix = _index(words=words)
    tl = Timeline(edl["keep"])
    shown = {w["w"]: w for w in captions.caption_plan(edl, ix, tl, canvas=(1080, 1920)).caption_words()}
    # 'reaction' starts 0.05 s (under two frames) before the stack: it shows
    # with the stack, in the stack's place, and is never later than 2 frames
    r = shown["reaction"]
    assert r["t0"] == pytest.approx(CARD[0]) and r["t0_said"] == pytest.approx(1.95)
    assert r["t0"] - r["t0_said"] <= 2 / 30 + 0.01
    assert r.get("place") and r.get("brk")
    # the sound-off audit still knows the word by its spoken onset
    assert caption_carry.heard_unshown(edl, ix, tl) == []
    cues = motion_captions.cues(edl, ix, tl, canvas=(1080, 1920))
    first = next(c for c in cues if any(w["t"].lower() == "reaction" for w in c["w"]))
    assert first["s"] == pytest.approx(CARD[0], abs=1e-3) and first.get("f") == 1
    # ...and is drawn with the stack, not a frame early over the full shot
    import motion_engine
    monkeypatch.setattr(motion_engine, "available", lambda: True)
    items = motion_captions.items(edl, ix, tl, canvas=(1080, 1920))
    starts = [it["start"] + c["s"] for it in items for c in it["params"]["cues"]
              if any(w["t"].lower() == "reaction" for w in c["w"])]
    assert starts and starts[0] == pytest.approx(CARD[0], abs=1e-3)


def test_one_or_two_words_stranded_by_a_change_join_their_line():
    # a lower third arrives mid-line: "I'd" alone before it would flash as an
    # orphan page in the usual place; it takes the moved place with its line
    words = [("I'd", 2.28, 2.42), ("say", 2.42, 2.82), ("like", 2.82, 2.98),
             ("the", 2.98, 3.3), ("surgical", 3.3, 3.8)]
    lt = {"id": "name", "template": "lower_third", "start": 2.4, "end": 5.6,
          "params": {"name": "Elon Musk", "role": "on The Joe Rogan Experience"},
          "footprint": {"box": [0.067, 0.565, 0.681, 0.688], "ar": AR, "faces": []}}
    edl = _edl(motion=[lt], words=words)
    ix = _index(faces=False, words=words)
    ix["video"] = {"width": 1080, "height": 1920, "fps": 30.0}
    tl = Timeline(edl["keep"])
    p = captions.caption_plan(edl, ix, tl, canvas=(1080, 1920))
    places = [p.placed.get(i) for i in range(len(words))]
    assert places[0] is not None and len({str(x) for x in places}) == 1
    cues = motion_captions.cues(edl, ix, tl, canvas=(1080, 1920))
    assert all(len(c["w"]) > 1 for c in cues), [[w["t"] for w in c["w"]] for c in cues]


# ── 5. side crop ─────────────────────────────────────────────────────────

def test_the_reel_caption_column_keeps_out_of_the_side_crop():
    assert caption_place.column(1080, 1920) == (0.15, 0.85)
    assert caption_place.column(1080, 1920, base=(0.0, 1.0)) == pytest.approx(
        (caption_place.SIDE_CROP_PORTRAIT, 1 - caption_place.SIDE_CROP_PORTRAIT))
    assert caption_place.column(1920, 1080, base=(0.0, 1.0)) == (0.0, 1.0)
    html = open(os.path.join(os.path.dirname(__file__), "..", "motion", "templates",
                             "caption_motion.html")).read()
    assert "x0: Math.round(0.09 * W)" in html


# ── 6. orphan connector pages ────────────────────────────────────────────

def test_a_one_word_connector_page_joins_its_line():
    # "and" alone after a breath would be its own page
    ws = [{"w": "great", "t0": 0.0, "t1": 0.3}, {"w": "companies", "t0": 0.3, "t1": 0.8},
          {"w": "and", "t0": 1.5, "t1": 1.6}, {"w": "the", "t0": 2.3, "t1": 2.4},
          {"w": "next", "t0": 2.4, "t1": 2.6}, {"w": "level.", "t0": 2.6, "t1": 3.0}]
    p = {"mode": "reveal", "max_words": 5, "target_words": 3, "max_chunk_s": 2.2}
    chunks = captions._premium_chunks_v2(ws, 5, 26, p)
    assert all(len(c) > 1 or c[0]["w"] not in ("and", "that", "on") for c in chunks), \
        [[w["w"] for w in c] for c in chunks]
    assert sum(len(c) for c in chunks) == len(ws)
    # never across a cut, a placement change or past the word cap
    cut = [dict(w) for w in ws]
    for w in cut[:3]:
        w["cut"] = 1.62
    chunks = captions._premium_chunks_v2(cut, 5, 26, p)
    assert any([w["w"] for w in c] == ["great", "companies", "and"] or
               [w["w"] for w in c] == ["and"] for c in chunks)
    assert max(len(c) for c in captions._premium_chunks_v2(ws, 2, 26, dict(p, max_words=2))) <= 2
