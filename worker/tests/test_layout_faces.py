"""Face safety in every card and panel, and the layout's geometry for
captions (judges, Oct 2026).

* A stack panel's speaker rect is SOLVED from the face track
  (picture_cards.panel_framing): chin and hair margins, lead room where the
  speaker looks, burned-in screen boxes kept out where any framing can — the
  face wins where none can (the judged Elon/Rogan panels cut a chin and
  pressed a nose to the edge to stay clear of the browser box).
* Stacked panels keep a gutter (>= STACK_CAPTION_GAP) that is no caption
  band: picture_cards.layout_rects / free_bands name the windows and the
  bands around them exactly as the caption solver (caption_place) does.
* A small archival face may be enlarged past 2x (face_cap), with grain.
* render_qc watches layout changes (luma dips/flashes), faces cut by a
  panel edge and an empty headline band.

Run:  python -m pytest tests/test_layout_faces.py -q     (from worker/)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_tools  # noqa: E402
import caption_place  # noqa: E402
import picture_cards as pc  # noqa: E402
import render_qc  # noqa: E402
import test_picture_layouts as T  # noqa: E402
from schemas import default_edl, validate_edl  # noqa: E402

SW, SH, W, H = 1920, 1080, 1080, 1920
PANEL = [0.04, 0.287, 0.96, 0.57]
INSET = [0.531, 0.522, 0.984, 0.978]


def _inside(rect, keep, tol=1e-3):
    return (rect[0] <= keep[0] + tol and rect[1] <= keep[1] + tol
            and rect[2] >= keep[2] - tol and rect[3] >= keep[3] - tol)


def _aspect(rect, box):
    rw, rh = (rect[2] - rect[0]) * SW, (rect[3] - rect[1]) * SH
    bw, bh = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    return rw / rh, bw / bh


# ── the solver ────────────────────────────────────────────────────────────

def test_a_panel_holds_the_whole_head_with_lead_room_where_they_look():
    face = [0.40, 0.30, 0.52, 0.52]
    for look in (-1, 1):
        rect, info = pc.panel_framing(SW, SH, W, H, PANEL, [face] * 5, [look] * 5)
        keep = pc.panel_keep([face])
        assert _inside(rect, keep) and pc.holds(rect, keep, pc.PANEL_MARGIN)
        a, b = _aspect(rect, PANEL)
        assert a == pytest.approx(b, rel=.01)
        # more room on the side the speaker looks to
        left, right = keep[0] - rect[0], rect[2] - keep[2]
        assert (left > right) if look < 0 else (right > left)
        assert info["lead"] == look
        # the crown sits near the top: no empty curtain above the head
        assert (keep[1] - rect[1]) / (rect[3] - rect[1]) < .2


def test_a_burned_in_box_is_kept_out_when_a_framing_can():
    face = [0.30, 0.25, 0.42, 0.47]                    # well clear of the box
    rect, info = pc.panel_framing(SW, SH, W, H, PANEL, [face], [0], [INSET])
    assert pc._overlap(rect, INSET) == 0 and info["inset"] == 0
    assert _inside(rect, pc.panel_keep([face]))


def test_where_the_box_touches_the_face_the_face_wins_and_says_how_much():
    """The Rogan layout: the browser's corner sits at the speaker's chin."""
    face = [0.324, 0.205, 0.554, 0.613]
    rect, info = pc.panel_framing(SW, SH, W, H, PANEL, [face] * 4, [1] * 4, [INSET])
    assert _inside(rect, pc.panel_keep([face]))
    assert info["inset"] > 1.0                         # 1 + the share still shown
    # ... and it is the least of it any holding framing shows
    alt, _ = pc.panel_framing(SW, SH, W, H, PANEL, [face] * 4, [1] * 4)
    assert pc._overlap(rect, INSET) <= pc._overlap(alt, INSET) + 1e-9


def test_an_editors_rect_that_cuts_the_chin_moves_only_as_far_as_it_must():
    """The judged Elon panel: a rect ending above the browser box ran
    through the mouth and chin with empty curtain above."""
    face = [0.37, 0.18, 0.64, 0.66]
    given = [0.22, 0.0, 0.75, 0.515]
    rect, info = pc.panel_framing(SW, SH, W, H, PANEL, [face], [-1],
                                  prefer=given)
    assert _inside(rect, pc.panel_keep([face])) and info["moved"]
    assert not _inside(given, pc.panel_keep([face]))
    assert pc._overlap(rect, given) > .5 * (given[2] - given[0]) * (given[3] - given[1])


def test_an_editors_rect_of_another_shape_that_holds_the_face_is_not_cut():
    """A look_at rect taller than the box is the editor's framing: it is
    trimmed to the box as fit='crop' shows it (from the bottom up) and the
    face it holds is never reported as cut."""
    face = [0.40, 0.30, 0.52, 0.52]
    given = [0.25, 0.10, 0.70, 0.90]
    rect, info = pc.panel_framing(SW, SH, W, H, PANEL, [face], [0], prefer=given)
    assert not info["cut"] and _inside(rect, pc.panel_keep([face]))
    a, b = _aspect(rect, PANEL)
    assert a == pytest.approx(b, rel=.01)
    # its width and top are the editor's (a nudge for the margins at most)
    assert rect[0] == pytest.approx(given[0], abs=.01)
    assert rect[2] == pytest.approx(given[2], abs=.01)
    assert rect[1] == pytest.approx(given[1], abs=.02)
    note = agent_tools._speaker_note(info, 0, moved_from=rect)
    assert "cut the speaker's face" not in note and "held the face" in note
    # a rect that does cut the face says so
    _r, info = pc.panel_framing(SW, SH, W, H, PANEL, [face], [0],
                                prefer=[0.35, 0.0, 0.75, 0.4])
    assert info["cut"] and info["moved"]
    assert "cut the speaker's face" in agent_tools._speaker_note(info, 0, moved_from=_r)


def test_no_framing_of_this_shape_holds_a_head_taller_than_the_source_allows():
    face = [0.40, 0.02, 0.60, 0.95]                    # a head filling the frame
    rect, info = pc.panel_framing(SW, SH, W, H, [0.04, 0.4, 0.96, 0.5], [face], [0])
    assert rect is None and info["keep"] is not None


# ── small archival faces ─────────────────────────────────────────────────

def test_a_small_archival_face_may_be_enlarged_past_the_cap_to_be_read():
    card_h = (0.6758 - 0.3042) * H                     # the judged Jobs card
    face_px = 0.213 * 480
    k = pc.face_cap(face_px, card_h, H)
    assert pc.SOURCE_UPSCALE_CAP < k <= pc.FACE_UPSCALE_CAP
    # the face reaches FACE_MIN_FRAME of the frame, or fills its card's
    # FACE_SHARE_MAX (a medium close-up: the card is the limit)
    assert face_px * k / H >= min(pc.FACE_MIN_FRAME,
                                  pc.FACE_SHARE_MAX * card_h / H) - 1e-6
    # an HD speaker never needs it
    assert pc.face_cap(0.3 * 1080, card_h, H) == pc.SOURCE_UPSCALE_CAP
    rect, _h = pc.speaker_rect(646, 480, W, H, [0.06, 0.3042, 0.94, 0.6758],
                               [0.48, 0.276, 0.636, 0.489])
    k_rect = (0.88 * W) / ((rect[2] - rect[0]) * 646)
    assert pc.SOURCE_UPSCALE_CAP < k_rect <= pc.FACE_UPSCALE_CAP + 1e-6


def test_enlarged_footage_wears_the_cards_grain():
    rect, box = [0.3, 0.1, 0.62, 0.55], [0.06, 0.3042, 0.94, 0.6758]
    chain = pc._panel(W, H, box, rect, (646, 480), grain=.25)
    assert "noise=c0s=4:c0f=t" in chain                # 16 * .25
    assert "noise" not in pc._panel(W, H, box, rect, (646, 480))
    wide = [0.05, 0.05, 0.95, 0.95]                    # under 2x: never re-noised
    assert "noise" not in pc._panel(W, H, box, wide, (646, 480), grain=.25)


# ── captions get a band ──────────────────────────────────────────────────

def test_stacked_panels_open_a_caption_band_and_name_their_edges():
    boxes, note = agent_tools._open_caption_gaps(
        [[0.04, 0.287, 0.96, 0.57], [0.04, 0.60, 0.96, 0.885]])
    assert boxes[1][1] - boxes[0][3] == pytest.approx(agent_tools.STACK_CAPTION_GAP)
    assert boxes[0][1] == 0.287 and boxes[1][3] == 0.885 and "band" in note
    side, note = agent_tools._open_caption_gaps(
        [[0.02, 0.3, 0.49, 0.7], [0.51, 0.3, 0.98, 0.7]])
    assert side == [[0.02, 0.3, 0.49, 0.7], [0.51, 0.3, 0.98, 0.7]] and not note
    assert agent_tools.STACK_SCREEN_BOX[1] - agent_tools.STACK_SPEAKER_BOX[3] >= \
        agent_tools.STACK_CAPTION_GAP - 1e-6
    e = T._edl([dict(T.CARD, start=1.0, end=3.0, panels=[
        {"box": boxes[0], "source": [0, 0, .5, 1]},
        {"box": boxes[1], "source": [.5, 0, 1, 1]}])])
    assert pc.layout_rects(e, 2.0) == [boxes[0], boxes[1]]
    assert pc.layout_rects(e, 3.5) == []
    assert pc.layout_edges(e) == [1.0, 3.0]
    # one design with the caption solver: the gutter is no caption band (a
    # page never sits on a seam); the band above the speaker panel is
    bands = pc.free_bands(pc.layout_rects(e, 2.0), top=.13, bottom=.8)
    assert not any(a < boxes[0][3] + .02 and b > boxes[1][1] - .02 for a, b in bands)
    assert bands and bands[0][0] == pytest.approx(.13) and \
        bands[0][1] == pytest.approx(boxes[0][1] - caption_place.EDGE_PAD)
    assert all(b - a >= pc.CAPTION_BAND_MIN for a, b in bands)
    rects = caption_place.card_rects(caption_place.live_cards(e, 1.5, 2.5))
    solver = caption_place.free_bands(caption_place.edge_zones(rects, (0.15, 0.85)),
                                      (0.13, 0.8), (0.15, 0.85))
    seam = [(a, b) for a, b in solver
            if a >= boxes[0][3] - 1e-6 and b <= boxes[1][1] + 1e-6]
    assert seam and all(b - a < caption_place.min_band(e, 1080, 1920) for a, b in seam)


# ── the tool ─────────────────────────────────────────────────────────────

def _faces_ctx(face, look=0, inset=None, monkeypatch=None):
    samples = [{"t": 10.0 + i * .25, "faces": [list(face)]} for i in range(40)]
    ctx = T._Ctx(SW, SH, samples)
    if monkeypatch is not None:
        monkeypatch.setattr(agent_tools, "_panel_insets",
                            lambda c, spans: [list(inset)] if inset else [])
        monkeypatch.setattr(agent_tools, "_inset_rect",
                            lambda c, spans: list(inset) if inset else None)
    return ctx


def test_a_stack_frames_its_speaker_from_the_face_and_opens_the_band(monkeypatch):
    face = [0.40, 0.30, 0.52, 0.52]
    ctx = _faces_ctx(face, inset=INSET, monkeypatch=monkeypatch)
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": "auto"},
        {"box": [0.04, 0.60, 0.96, 0.885], "source": "inset"}])
    assert res.startswith("EDL v"), res
    card = ctx.card()
    speaker, screen = card["panels"]
    assert _inside(speaker["source"], pc.panel_keep([face]))
    assert pc._overlap(speaker["source"], INSET) < 1e-4
    assert screen["box"][1] - speaker["box"][3] >= agent_tools.STACK_CAPTION_GAP - 1e-4
    assert "face held whole" in res and "kept out of the panel" in res
    assert "the stacked panels get a gutter" in res and "never sit on the seam" in res


def test_a_given_rect_that_cuts_the_face_is_repaired(monkeypatch):
    face = [0.37, 0.18, 0.64, 0.66]
    ctx = _faces_ctx(face, inset=INSET, monkeypatch=monkeypatch)
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": [0.22, 0.0, 0.75, 0.515],
         "fit": "crop"},
        {"box": [0.04, 0.60, 0.96, 0.885], "source": "inset"}])
    assert res.startswith("EDL v"), res
    speaker = ctx.card()["panels"][0]
    assert _inside(speaker["source"], pc.panel_keep([face]))
    assert "the given rect cut the speaker's face" in res
    assert "touches the speaker's face" in res          # the corner it costs


def test_a_screen_rect_is_left_alone(monkeypatch):
    face = [0.30, 0.25, 0.42, 0.47]
    ctx = _faces_ctx(face, monkeypatch=monkeypatch)
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": "auto"},
        {"box": [0.04, 0.65, 0.96, 0.885], "source": INSET, "fit": "crop"}])
    assert res.startswith("EDL v"), res
    # the screen panel is resolved as before (cropped to its box's shape
    # inside the given rect), never re-solved around the speaker's face
    r = ctx.card()["panels"][1]["source"]
    assert INSET[0] - 1e-3 <= r[0] and r[2] <= INSET[2] + 1e-3
    assert INSET[1] - 1e-3 <= r[1] and r[3] <= INSET[3] + 1e-3
    assert "panel 2: 1920x1080 source rect" in res


def test_a_panel_whose_speaker_moves_follows_them(monkeypatch):
    def at(t):
        x = .25 if t < 14.5 else .55                   # steps across the room
        return [x, .3, x + .12, .52]
    samples = [{"t": 10.0 + i * .25, "faces": [at(10.0 + i * .25)]} for i in range(40)]
    ctx = T._Ctx(SW, SH, samples)
    monkeypatch.setattr(agent_tools, "_panel_insets", lambda c, spans: [])
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": "auto"},
        {"box": [0.04, 0.65, 0.96, 0.885], "source": [0.6, 0.6, 1.0, 1.0]}])
    assert res.startswith("EDL v"), res
    speaker = ctx.card()["panels"][0]
    assert speaker.get("follow"), res
    assert "FOLLOWS the speaker" in res
    for t in (11.0, 18.0):
        r = pc.panel_source_at(speaker, t)
        assert _inside(r, pc.panel_keep([at(t)]), tol=.02)


def test_a_following_panel_is_drawn_by_the_window_chain(tmp_path):
    stack = dict(T.CARD, start=1.0, end=4.0, panels=[
        {"box": [.05, .3, .95, .55], "source": [0.1, 0.1, 0.5, 0.9],
         "follow": [{"t0": 0.0, "t1": 60.0, "k": [[1.5, .3, .5], [3.5, .6, .5]]}]},
        {"box": [.05, .62, .95, .9], "source": [.68, 0, 1, 1]}])
    graph, _a, _t = T._graph(T._edl([stack]), tmp_path)
    assert "perspective=" in graph


# ── picture QC ────────────────────────────────────────────────────────────

def test_qc_flags_a_dip_inside_a_dissolve_and_a_blink_at_a_cut():
    plan = {"layout": [{"t": 1.0, "lo": 30, "hi": 38, "kind": "dissolve",
                        "id": "a", "opening": True},
                       {"t": 4.0, "lo": 120, "hi": 120, "kind": "cut",
                        "id": "b", "opening": True}], "still": []}
    smooth = [66.0] * 30 + [66 - 4 * k for k in range(9)] + [34.0] * 200
    assert render_qc.layout_glitches(smooth, plan) == []
    dipped = [66.0] * 30 + [27.0] + [30 + k for k in range(8)] + [38.0] * 200
    (hit,) = render_qc.layout_glitches(dipped, plan)
    assert hit[0] == "dip" and hit[1] == 30 and hit[4] == "a"
    flash = [66.0] * 30 + [62 - 4 * k for k in range(9)] + [30.0] * 200
    flash[37] = 15.0
    flash[38] = 135.0
    assert render_qc.layout_glitches(flash, plan)[0][0] in ("dip", "flash")
    # a cut may change level on its frame; a blink comes back
    level = [120.0] * 120 + [40.0] * 100
    assert render_qc.layout_glitches(level, plan) == []
    blink = [120.0] * 120 + [30.0, 31.0, 118.0] + [118.0] * 100
    assert render_qc.layout_glitches(blink, plan)[0][0] == "dip"
    lines = render_qc.findings({"layout": [list(hit)]}, {"fps": 30.0})
    assert lines and lines[0].startswith("LAYOUT DIP at 1.00s")
    # a short (4-frame) dissolve from a bright shot to a dark card halves
    # the level on its last frame by design: its steps are even, no glitch
    short = {"layout": [{"t": 1.0, "lo": 30, "hi": 33, "kind": "dissolve",
                         "id": "s", "opening": True}], "still": []}
    fast = [140.0] * 30 + [110.0, 80.0, 50.0, 20.0] + [20.0] * 30
    assert render_qc.layout_glitches(fast, short) == []
    # ...while a frame of bare canvas inside it still is one
    fast[31] = 12.0
    assert render_qc.layout_glitches(fast, short)[0][0] == "dip"


def test_qc_flags_a_face_cut_by_its_panel_edge():
    plan = {"cards": [(5.0, 9.0, [[.04, .287, .96, .57], [.04, .65, .96, .885]])]}
    chin_cut = ([.40, .42, .55, .58], -1)                # the chin past the edge
    hits = render_qc.cut_faces([(t, [chin_cut]) for t in (6.0, 6.5, 7.0)], plan)
    assert hits and hits[0][2] == "bottom" and hits[0][4] == "panel"
    held = ([.40, .33, .55, .48], -1)
    assert render_qc.cut_faces([(t, [held]) for t in (6.0, 6.5, 7.0)], plan) == []
    # full frame: a close-up may lose the top of its hair, never the chin
    top = ([.3, .0, .7, .3], 0)
    assert render_qc.cut_faces([(t, [top]) for t in (1.0, 1.5)], plan) == []
    lines = render_qc.findings({"cut": [list(hits[0])]}, dict(plan, fps=30.0))
    assert lines[0].startswith("FACE CUT BY THE PANEL EDGE") and "chin" in lines[0]


def test_qc_flags_an_empty_headline_band_longer_than_150ms():
    fps = 30.0
    ink = [(round(i / fps, 3), .2) for i in range(60)]
    ink += [(round((60 + i) / fps, 3), 0.0) for i in range(6)]      # 0.2 s hole
    ink += [(round((66 + i) / fps, 3), .2) for i in range(30)]
    ink += [(round((96 + i) / fps, 3), 0.0) for i in range(3)]      # 0.1 s: fine
    ink += [(round((99 + i) / fps, 3), .2) for i in range(30)]
    gaps = render_qc.band_gaps(ink, fps)
    assert gaps == [(2.0, 2.2)]
    # an entrance from nothing at the very start is not a dropped layer,
    # nor the headline's own yield at either edge (motion_layer.YIELD_EDGE_S)
    import motion_layer
    assert render_qc.BAND_EDGE_S >= motion_layer.YIELD_EDGE_S
    opening = [(round(i / fps, 3), 0.0) for i in range(5)] + ink[5:]
    assert render_qc.band_gaps(opening, fps) == gaps
    edge = int(motion_layer.YIELD_EDGE_S * fps) - 1
    held = [(t, 0.0 if i < edge else v) for i, (t, v) in enumerate(ink)]
    held += [(round((129 + i) / fps, 3), 0.0) for i in range(edge)]
    assert render_qc.band_gaps(held, fps) == gaps
    # ...but a hole that long mid-band is one
    mid = ink[:40] + [(round((40 + i) / fps, 3), 0.0) for i in range(edge)] + \
        [(round((40 + edge + i) / fps, 3), .2) for i in range(10)]
    assert len(render_qc.band_gaps(mid, fps)) == 1


def test_the_qc_plan_knows_layout_changes_and_the_band(tmp_path):
    e = default_edl(60.0)
    e["keep"] = [[0.0, 20.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    e["effects"] = {"picture_cards": [dict(T.CARD, start=2.0, end=8.0,
                                           source=[0, 0, 1, 1], entrance="fade",
                                           exit="none", duration_s=.3)]}
    e["motion"] = [{"id": "hl", "template": "headline", "start": 0.0, "end": 20.0,
                    "params": {"text": "A *claim*", "y": .2, "height": .14}}]
    e = validate_edl(e, 60.0).model_dump()
    p = render_qc.plan(e, {}, W=1080, H=1920, fps=30.0)
    kinds = {(x["t"], x["kind"]) for x in p["layout"]}
    assert kinds == {(2.0, "dissolve"), (8.0, "cut")}
    (band,) = p["bands"]
    assert band["box"][1] >= 0.128 - 1e-3 and band["box"][3] == pytest.approx(.27)


def test_the_qc_plan_judges_edges_the_renderer_cuts_as_cuts(tmp_path):
    """Two cards meeting mid-segment cut (the renderer never dissolves into
    another card's layout), and a timed flash at a layout change is
    deliberate: neither may read as a dip/flash glitch (a blocking QC line)."""
    e = default_edl(60.0)
    e["keep"] = [[0.0, 20.0]]
    e["frame"] = {"ratio": "9:16", "mode": "crop"}
    a = dict(T.CARD, id="a", start=2.0, end=6.0, source=[0, 0, 1, 1],
             entrance="fade", exit="fade", duration_s=.3)
    b = dict(T.CARD, id="b", start=6.0, end=9.0, source=[0, 0, 1, 1],
             entrance="fade", exit="fade", duration_s=.3)
    e["effects"] = {"picture_cards": [a, b],
                    "stylize": [{"id": "fl", "kind": "flash", "start": 8.9,
                                 "end": 9.2}]}
    e = validate_edl(e, 60.0).model_dump()
    p = render_qc.plan(e, {}, W=1080, H=1920, fps=30.0)
    kinds = {(x["id"], x["t"]): x["kind"] for x in p["layout"]}
    assert kinds[("a", 2.0)] == "dissolve"
    assert kinds[("a", 6.0)] == "cut" and kinds[("b", 6.0)] == "cut"
    # the flash at b's exit: a level change there is not a layout glitch
    n = 270
    lumas = [60.0] * n + [180.0, 60.0] + [60.0] * 30
    assert render_qc.layout_glitches(lumas, p) == []


def test_a_following_stack_panel_counts_as_a_follow_everywhere():
    import follow
    import timeline
    path = [{"t0": 0.0, "t1": 10.0, "k": [[4.0, .3, .5], [6.0, .5, .5]]}]
    card = dict(T.CARD, start=1.0, end=6.0, panels=[
        {"box": [.05, .3, .95, .55], "source": [.1, .1, .5, .9], "follow": path},
        {"box": [.05, .62, .95, .9], "source": [.6, 0, 1, 1]}])
    assert follow.card_follows(card) and follow.card_spans(card) == path
    e = {"effects": {"picture_cards": [card]}}
    assert follow.moves_during(e, [(4.5, 5.0)])
    # a re-aim hidden on a jump cut slows to a glide when the cut is undone
    s = [(t / 4.0, [.25 if t < 20 else .43, .3, .37 if t < 20 else .55, .5], 0)
         for t in range(0, 41)]
    keys, info = follow.plan(s, 0, 10, 607.5 / 1920, 1.0, kept=[(0.0, 4.6), (5.4, 10.0)])
    assert info["hidden"] == 1
    card["panels"][0]["follow"] = [{"t0": 0.0, "t1": 10.0, "k": keys}]
    edl = {"effects": {"picture_cards": [card]}}
    notes = timeline._reveal_follow(edl, [(0.0, 10.0)], [(0.0, 4.6), (5.4, 10.0)])
    assert notes and "slowed" in notes[0]
    moving = follow.moving_windows(card["panels"][0]["follow"][0])
    assert len(moving) == 1 and moving[0][1] - moving[0][0] >= follow.MOVE_MIN_S - 1e-3
    # a re-cut stitched preview re-renders a following panel's blocks
    import stitch
    from timeline import Timeline
    short = dict(card, end=5.0)
    prev = T._edl([short])
    new = T._edl([short], keep=((0.5, 2.37), (3.13, 6.0), (6.9, 8.0)))
    tl0 = Timeline(prev["keep"], [], [])
    tl1 = Timeline(new["keep"], [], [])
    windows, _runs, why = stitch.plan_timeline(prev, new, tl0, tl1, tl1.out_duration)
    assert windows is None and "face-following" in why


# ── the corner a face forces in ──────────────────────────────────────────

def test_a_box_touching_the_face_is_concealed_in_the_panel(monkeypatch):
    face = [0.324, 0.205, 0.554, 0.613]                # the Rogan layout
    ctx = _faces_ctx(face, look=1, inset=INSET, monkeypatch=monkeypatch)
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": "auto"},
        {"box": [0.04, 0.60, 0.96, 0.885], "source": "inset"}])
    assert res.startswith("EDL v"), res
    speaker = ctx.card()["panels"][0]
    assert speaker["conceal"] == [pytest.approx(INSET)]
    assert "softened" in res
    boxes = pc.conceal_boxes(speaker["source"], speaker["conceal"], 994, 544)
    (x0, y0, x1, y1, inner) = boxes[0]
    assert inner[0] and inner[1] and x1 == 994 and y1 == 544   # the corner


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg")
def test_a_concealed_corner_is_darkened_and_feathered(tmp_path):
    """The panel's bottom-right corner shows the box: it is drawn dark and
    soft, the rest of the panel untouched."""
    stack = dict(T.CARD, start=1.0, end=4.0, panels=[
        {"box": [.05, .3, .95, .55], "source": [0, 0, .6, 1],
         "conceal": [[.4, .5, 1, 1]]},
        {"box": [.05, .62, .95, .9], "source": [.68, 0, 1, 1]}])
    frames = T._render(tmp_path, T._edl([stack]))
    f = frames[60]
    x, y, w, h = pc.pixels(320, 568, [.05, .3, .95, .55])
    clear = int(f[0][y + 4, x + 4])                   # top-left: the footage
    hidden = int(f[0][y + h - 4, x + w - 4])          # bottom-right: concealed
    assert hidden < 16 + (clear - 16) * .6
    assert T._is_card(f)


def test_a_face_seen_over_little_of_the_window_gets_extra_room(monkeypatch):
    """The judged Rogan window: the profile was measured over ~35% of it."""
    face = [0.40, 0.30, 0.52, 0.52]
    samples = [{"t": 16.0 + i * .25, "faces": [list(face)]} for i in range(12)]
    ctx = T._Ctx(SW, SH, samples)
    monkeypatch.setattr(agent_tools, "_panel_insets", lambda c, spans: [])
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": "auto"},
        {"box": [0.04, 0.65, 0.96, 0.885], "source": [0.6, 0.6, 1.0, 1.0]}])
    assert res.startswith("EDL v") and "measured over only" in res, res
    rect = ctx.card()["panels"][0]["source"]
    assert _inside(rect, pc.panel_keep([face], pc.PANEL_UNSEEN_PAD))


def test_a_head_at_the_sources_edge_is_framed_not_refused():
    """A close-up whose hair meets the top of the source: the source edge
    is the limit there (as speaker_rect's headroom), never a refusal."""
    face = [0.40, 0.03, 0.56, 0.40]
    rect, info = pc.panel_framing(SW, SH, W, H, PANEL, [face] * 3, [0] * 3)
    assert rect is not None and rect[1] == 0.0
    assert _inside(rect, pc.panel_keep([face]))
    tall = [0.38, 0.02, 0.62, 0.92]                    # nearly the source's height
    rect, info = pc.panel_framing(SW, SH, W, H, [0.1, 0.2, 0.9, 0.7], [tall], [0])
    assert rect is not None and _inside(rect, pc.panel_keep([tall]))
