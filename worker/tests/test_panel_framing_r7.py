"""Panels and cards that frame the speaker WELL, not just safely (judges,
Oct 2026, round 7).

* panel_framing: every face box keeps PANEL_FACE_MARGIN of the panel inside
  each edge first; a burned-in box no framing leaves out never pulls the
  head more than PANEL_SHIFT_MAX off its composition (the judged Rogan
  panel sat him in its lower-right corner) and never shows as a thin strip.
* conceal_clear: a softened corner (feather included) never lies on the
  head (the judged smear over Rogan's mouth and jaw).
* crop_clear and the LAYOUT line: where the box touches the face, the
  stack, a speaker card alone and the speaker full-bleed are measured with
  the same rules and the cleanest is named, with the call.
* sliver_shift / _clear_slivers / _step_sliver: a thin band of a pillar or
  frame edge along a card's edge (the Jobs stage pillar) is slid off per
  HELD framing — never a glide — and a cut step that would open one stays
  bare.
* An archival 4:3 speaker card defaults to a larger, squarer window between
  a headline band clear of the free-tier mark and a whole caption band.
* render_qc: a face under a softened corner is a finding.

Run:  python -m pytest tests/test_panel_framing_r7.py -q     (from worker/)
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_tools  # noqa: E402
import caption_carry  # noqa: E402
import caption_place  # noqa: E402
import keepout  # noqa: E402
import motion_tools  # noqa: E402
import picture_cards as pc  # noqa: E402
import render_qc  # noqa: E402
import test_picture_layouts as T  # noqa: E402

SW, SH, W, H = 1920, 1080, 1080, 1920
STACK = [0.04, 0.287, 0.96, 0.5525]          # the judged Elon speaker panel
BROWSER = [0.5312, 0.5167, 0.9844, 0.9778]   # the burned-in browser
# Rogan's face where the detector saw it (the frontal turn at the end of a
# window it lost in profile for 90% of the time)
ROGAN = [[0.372, 0.208, 0.581, 0.579], [0.383, 0.218, 0.586, 0.579]]


def _centre_share(rect, face):
    """Where the face's centre sits across the rect (0 left .. 1 right)."""
    return ((face[0] + face[2]) / 2.0 - rect[0]) / (rect[2] - rect[0])


# ── the solver frames the head well ──────────────────────────────────────

def test_the_rogan_panel_is_framed_on_him_not_pushed_into_a_corner():
    rect, info = pc.panel_framing(SW, SH, W, H, STACK, ROGAN, [0, 0], [BROWSER],
                                  pad=pc.PANEL_UNSEEN_PAD)
    face = pc.face_extent(ROGAN)
    assert info["tier"] == 0
    assert info["face_margin"] >= pc.PANEL_FACE_MARGIN - 1e-3
    assert info["inset"] > 1.0                  # the box touches his face
    # the judged rect [0, .032, .669, .642] was pushed to the source's edge
    # to show less of the box: him at 72% across, under the corner
    assert _centre_share([0.0, 0.032, 0.6692, 0.6423], face) > .7
    assert rect[0] > .02 and .35 <= _centre_share(rect, face) <= .65
    # the box pulls the framing at most PANEL_SHIFT_MAX off the composition
    plain, _ = pc.panel_framing(SW, SH, W, H, STACK, ROGAN, [0, 0],
                                pad=pc.PANEL_UNSEEN_PAD)
    assert abs(_centre_share(rect, face) - _centre_share(plain, face)) <= \
        pc.PANEL_SHIFT_MAX + .02


@pytest.mark.parametrize("face", [[0.40, 0.30, 0.52, 0.52],
                                  [0.08, 0.22, 0.22, 0.50],
                                  [0.70, 0.35, 0.82, 0.60]])
def test_every_face_box_keeps_its_margin_inside_the_panel(face):
    rect, info = pc.panel_framing(SW, SH, W, H, STACK, [face] * 3, [0] * 3)
    assert info["tier"] == 0
    assert pc.face_margin(rect, face) >= pc.PANEL_FACE_MARGIN - 1e-3
    assert info["face_frame"] == pytest.approx(
        (face[3] - face[1]) * SH * info["k"] / H, abs=2e-3)


def test_what_shows_of_a_box_is_a_corner_never_a_thin_strip():
    face = [0.40, 0.30, 0.52, 0.52]             # the box diagonal to the chin
    inset = [0.531, 0.522, 0.984, 0.978]
    rect, info = pc.panel_framing(SW, SH, W, H, STACK, [face], [0], [inset])
    assert info["inset"] >= 1.0 and info["tier"] == 0
    assert pc._thin_strip(rect, [inset]) == 0
    assert pc._thin_strip([0.28, 0.22, 0.64, 0.535], [inset]) == 1


# ── a softened corner never lies on the head ─────────────────────────────

def _canvas_keep(rect, keep, box=STACK):
    x, y, w, h = pc.pixels(W, H, box)
    return [(x + (keep[0] - rect[0]) / (rect[2] - rect[0]) * w) / W,
            (y + (keep[1] - rect[1]) / (rect[3] - rect[1]) * h) / H,
            (x + (keep[2] - rect[0]) / (rect[2] - rect[0]) * w) / W,
            (y + (keep[3] - rect[1]) / (rect[3] - rect[1]) * h) / H]


def test_a_softened_corner_is_trimmed_off_the_head():
    rect = [0.0731, 0.0495, 0.7575, 0.6737]
    keep = pc.panel_keep(ROGAN, pc.PANEL_UNSEEN_PAD)
    out, trimmed = pc.conceal_clear(rect, [BROWSER], keep, STACK, SW, SH, W, H)
    assert trimmed and len(out) == 1
    c = out[0]
    assert c[0] > BROWSER[0] and c[2:] == pytest.approx(BROWSER[2:])
    head = _canvas_keep(rect, keep)
    for z in pc.conceal_canvas(STACK, rect, out, W, H):     # feather included
        assert min(z[2], head[2]) - max(z[0], head[0]) <= 2.0 / W or \
            min(z[3], head[3]) - max(z[1], head[1]) <= 2.0 / H
    # untrimmed, the feather reached the head (the judged smear)
    head_hit = [z for z in pc.conceal_canvas(STACK, rect, [BROWSER], W, H)
                if min(z[2], head[2]) > max(z[0], head[0])
                and min(z[3], head[3]) > max(z[1], head[1])]
    assert head_hit


def test_a_box_clear_of_the_head_is_softened_whole_and_one_under_it_never():
    rect, box = [0.0, 0.0, 0.6, 0.6], [0.5, 0.5, 1.0, 1.0]
    out, trimmed = pc.conceal_clear(rect, [box], [0.1, 0.1, 0.3, 0.3],
                                    STACK, SW, SH, W, H)
    assert out == [box] and not trimmed
    # the head itself under the box: nothing softened (no smear on it)
    out, trimmed = pc.conceal_clear(rect, [box], [0.45, 0.45, 0.59, 0.59],
                                    STACK, SW, SH, W, H)
    assert out == [] and trimmed


# ── the speaker + screen fallback: measured, named ──────────────────────

def test_the_full_bleed_alternative_is_measured():
    win, shown, strip = pc.crop_clear(SW, SH, W, H, [0.20, 0.2, 0.32, 0.6], [BROWSER])
    assert win and shown == 0 and win[2] <= BROWSER[0] + 1e-6
    face = pc.face_extent(ROGAN, pc.PANEL_UNSEEN_PAD)
    win, shown, strip = pc.crop_clear(SW, SH, W, H, face, [BROWSER])
    assert win and shown > 0 and strip > .2
    # a head wider than a 9:16 window with its margins: no full-bleed
    assert pc.crop_clear(SW, SH, W, H, [0.1, 0.1, 0.6, 0.9], [BROWSER])[0] is None


def _layout(monkeypatch, card_info, face, card_rect=(0.2, 0.1, 0.5, 0.65)):
    monkeypatch.setattr(agent_tools, "_panel_speaker",
                        lambda *a, **k: (list(card_rect), card_info, None, None))
    info = {"inset": 1.08, "face": face, "avoid": [BROWSER]}
    return agent_tools._stack_layout_note(
        None, {}, [(0.0, 9.0)], (W, H), {"width": SW, "height": SH}, info,
        "screen_2", 7.78, 17.53)


def test_the_stack_names_a_cleaner_layout_when_one_is_measured(monkeypatch):
    note = _layout(monkeypatch, {"inset": 0.0, "tier": 0}, [0.20, 0.2, 0.32, 0.6])
    assert note.startswith("BETTER LAYOUT (measured): a speaker card ALONE")
    assert "set_picture_card(id='screen_2', start=7.78, end=17.53" in note
    assert "fit='crop'" in note and "8% corner" in note
    note = _layout(monkeypatch, {"inset": 1.07, "tier": 0}, [0.20, 0.2, 0.32, 0.6])
    assert note.startswith("BETTER LAYOUT (measured): the speaker FULL-BLEED")
    assert "remove_picture_card('screen_2')" in note
    note = _layout(monkeypatch, {"inset": 1.07, "tier": 0},
                   pc.face_extent(ROGAN, pc.PANEL_UNSEEN_PAD))
    assert note.startswith("LAYOUT (measured): this stack is the cleanest")
    assert "would show 7% of it" in note and "strip of it down the frame" in note


# ── thin slivers at a card's edge ───────────────────────────────────────

def test_a_thin_band_along_an_edge_is_slid_off():
    rect = [0.24, 0.1, 0.84, 0.9]
    new, found = pc.sliver_shift(rect, [0.25], [])
    assert new[0] == pytest.approx(0.25 + pc.SLIVER_PAD)
    assert new[2] - new[0] == pytest.approx(0.6)
    assert found[0][:2] == ("x", "left") and found[0][3] > 0
    # the edge's soft falloff just outside the card counts too
    new, _found = pc.sliver_shift([0.255, 0.1, 0.855, 0.9], [0.25], [])
    assert new[0] == pytest.approx(0.25 + pc.SLIVER_PAD)
    # a deep band is the picture, not a sliver
    assert pc.sliver_shift([0.10, 0.1, 0.70, 0.9], [0.25], [])[1] == []
    # a band on both sides, or a slide that would cut the head: reported,
    # the framing kept
    new, found = pc.sliver_shift(rect, [0.25, 0.83], [])
    assert new == rect and found[0][1] == "both" and found[0][3] == 0
    new, found = pc.sliver_shift(rect, [0.25], [], keep=[0.245, 0.3, 0.5, 0.8])
    assert new == rect and found and found[0][3] == 0
    # a pattern of lines (panelling, blinds) is never a sliver
    assert pc.sliver_shift(rect, [0.25, 0.4, 0.5, 0.6], [])[1] == []
    # horizontal edges too
    new, found = pc.sliver_shift([0.2, 0.30, 0.8, 0.9], [], [0.88])
    assert found[0][:2] == ("y", "bottom")
    assert new[3] == pytest.approx(0.88 - pc.SLIVER_PAD)


def test_held_positions_slide_together_and_never_start_to_glide(monkeypatch):
    monkeypatch.setattr(agent_tools, "_edge_lines",
                        lambda ctx, windows, tag: ([0.25], []))
    samples = [{"t": 10.0 + i * .5, "faces": [[0.45, 0.2, 0.6, 0.45]]}
               for i in range(20)]
    ctx = T._Ctx(646, 480, samples)
    rect = [0.24, 0.05, 0.84, 0.75]                  # centred on x .54
    follows = [{"t0": 10.0, "t1": 20.0,
                "k": [[11.0, .54, .4], [15.0, .54, .4], [16.0, .70, .4]]}]
    r, f, track, note = agent_tools._clear_slivers(ctx, ctx._edl, [(10.0, 20.0)],
                                                   rect, follows)
    keys = f[0]["k"]
    # one held position: one slide for both of its keys (no 4 s creep)
    assert keys[0][1] == keys[1][1] == pytest.approx(.54 + 0.01 + pc.SLIVER_PAD)
    assert keys[2][1] == pytest.approx(.70)          # clear already: untouched
    assert [k[2] for k in keys] == [.4, .4, .4] and track is None
    assert "EDGE SLIVER CLEARED" in note and "left edge" in note
    # the still rect of a card is slid the same way
    r, f, _t, note = agent_tools._clear_slivers(ctx, ctx._edl, [(10.0, 20.0)], rect)
    assert r[0] == pytest.approx(0.25 + pc.SLIVER_PAD) and f is None


def test_a_panel_holds_still_while_its_speaker_sways(monkeypatch):
    """Stable framing: a speaker who only sways gets one still panel (no
    follow path, no hunting); one who moves gets a path."""
    sway = [{"t": 10.0 + i * .25,
             "faces": [[0.40 + .006 * (i % 3 - 1), 0.30, 0.52 + .006 * (i % 3 - 1), 0.52]]}
            for i in range(40)]
    ctx = T._Ctx(SW, SH, sway)
    monkeypatch.setattr(agent_tools, "_panel_insets", lambda c, spans: [])
    res = agent_tools.set_picture_card(ctx, "s", 0.0, 9.0, panels=[
        {"box": [0.04, 0.287, 0.96, 0.57], "source": "auto"},
        {"box": [0.04, 0.65, 0.96, 0.885], "source": [0.6, 0.6, 1.0, 1.0]}])
    assert res.startswith("EDL v"), res
    assert not ctx.card()["panels"][0].get("follow")
    assert "FOLLOWS" not in res


def test_a_cut_step_that_opens_a_sliver_stays_bare(monkeypatch):
    monkeypatch.setattr(agent_tools, "_edge_lines",
                        lambda ctx, windows, tag: ([0.25], []))
    card = dict(T.CARD, box=[0.06, 0.27, 0.94, 0.70], fit="crop",
                source=[0.28, 0.08, 0.848, 0.75])
    why = agent_tools._step_sliver(None, card, 0.8929, 10.0, 12.0, 646, 480, W, H)
    assert "wide step" in why and "left edge" in why and "stays bare" in why
    assert agent_tools._step_sliver(None, card, 1.12, 10.0, 12.0, 646, 480, W, H) == ""


# ── archival 4:3 speakers get a larger window ────────────────────────────

def test_an_archival_speaker_card_defaults_to_the_larger_window():
    samples = [{"t": 10.0 + i * .25, "faces": [[0.42, 0.25, 0.58, 0.48]]}
               for i in range(40)]
    ctx = T._Ctx(646, 480, samples)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9)
    assert res.startswith("EDL v"), res
    card = ctx.card()
    assert card["box"] == pytest.approx(pc.ARCHIVAL_CARD_BOX)
    assert card["fit"] == "crop" and "archival 4:3 speaker" in res
    k = (card["box"][2] - card["box"][0]) * W / ((card["source"][2] - card["source"][0]) * 646)
    assert k <= pc.FACE_UPSCALE_CAP + 1e-6
    # taller than the judged full-width 4:3 card (37% of the frame)
    assert card["box"][3] - card["box"][1] > (0.6758 - 0.3042) + .05
    # an explicit postage-stamp box is kept, with the larger one named
    ctx = T._Ctx(646, 480, samples)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9, box=[0.06, 0.3042, 0.94, 0.6758],
                                       fit="crop")
    assert ctx.card()["box"][1] == pytest.approx(0.3042) and "omit box" in res
    # no face measured: the whole stage as before; an HD source: as before
    ctx = T._Ctx(646, 480)
    agent_tools.set_picture_card(ctx, "c", 0, 9)
    assert ctx.card()["fit"] == "pad"
    ctx = T._Ctx(1920, 1080, samples)
    res = agent_tools.set_picture_card(ctx, "c", 0, 9)
    assert "archival" not in res


def test_the_archival_window_keeps_both_bands():
    box = pc.ARCHIVAL_CARD_BOX
    top = motion_tools.band_top(W, H)
    assert top >= keepout.watermark_zone(W, H)[3] - 1e-6       # below the mark
    # the band above holds the headline and hero lockups ...
    assert box[1] - motion_tools.HEADLINE_GAP - top >= .13 - 1e-3
    # ... and below it a whole caption band above the feed's safe bottom
    bottom = caption_carry.safe_range(W, H)[1]
    assert bottom - (box[3] + caption_place.EDGE_PAD) >= caption_carry.MIN_BAND_H - 1e-6
    # full width, squarer than 4:3
    w, h = (box[2] - box[0]) * W, (box[3] - box[1]) * H
    assert box[0] <= .06 + 1e-6 and box[2] >= .94 - 1e-6 and .95 <= w / h <= 1.25


# ── picture QC: no face under a softened corner ──────────────────────────

def test_qc_flags_a_face_under_a_softened_corner():
    judged = [0.0, 0.032, 0.6692, 0.6423]
    zones = pc.conceal_canvas(STACK, judged, [BROWSER], W, H)
    assert zones
    z = zones[0]
    plan = {"softened": [(7.78, 17.53, zones)], "fps": 30.0}
    under = ([z[0] - .02, z[1] - .02, z[0] + .08, z[1] + .06], 1)
    hits = render_qc.softened_faces([(t, [under]) for t in (9.0, 9.5, 10.0)], plan)
    assert hits and hits[0][0] == 9.0 and hits[0][2] > render_qc.SOFT_HIT
    clear = ([.2, .32, .3, .4], 1)
    assert render_qc.softened_faces([(t, [clear]) for t in (9.0, 9.5)], plan) == []
    lines = render_qc.findings({"softened": [list(hits[0])]}, plan)
    assert lines[0].startswith("FACE UNDER A SOFTENED CORNER 9.0-10.0s")
    # the plan reads the zones off the EDL's still panels
    e = T._edl([dict(T.CARD, start=1.0, end=4.0, panels=[
        {"box": [.05, .3, .95, .55], "source": [0, 0, .6, 1],
         "conceal": [[.4, .5, 1, 1]]},
        {"box": [.05, .62, .95, .9], "source": [.68, 0, 1, 1]}])])
    p = render_qc.plan(e, {}, W=W, H=H, fps=30.0)
    assert p["softened"] and p["softened"][0][:2] == (1.0, 4.0)
