"""Layout changes never show the bare canvas (judges, Oct 2026).

The Elon stack opened on a frame of empty dark canvas (mean luma 66.6 ->
27.4 in one frame, mid-sentence) and closed by fading to it before a hard
cut to a bright shot. A card's animated entrance/exit now DISSOLVES the
whole card with the full-frame shot under it (picture_cards module
docstring); a source-fed card on a cut hard-cuts with its panels populated.

Run:  python -m pytest tests/test_layout_transitions.py -q     (from worker/)
"""
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import picture_cards  # noqa: E402
import test_picture_layouts as T  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"),
                                  reason="ffmpeg required")
H = 568
BOX_Y = int(.42 * H)          # a row inside the card's box ([.05, .3, .95, .55])


def _between(seq, a, b, tol=3):
    lo, hi = min(a, b) - tol, max(a, b) + tol
    return all(lo <= v <= hi for v in seq)


def _monotone(seq, tol=2):
    up = all(b >= a - tol for a, b in zip(seq, seq[1:]))
    down = all(b <= a + tol for a, b in zip(seq, seq[1:]))
    return up or down


# ── the plan ──────────────────────────────────────────────────────────────

def test_animation_windows_follow_the_cards_own_clock():
    card = dict(T.CARD, start=2.0, end=6.0, entrance="fade", exit="lift",
                duration_s=.4)
    assert picture_cards.animation_windows(card) == ((2.0, 2.4), (5.6, 6.0))
    # a stitched piece opened 1 s into the card has its entrance behind it
    piece = dict(card, start=3.0, phase_s=1.0, full_duration_s=4.0)
    assert picture_cards.animation_windows(piece) == (None, (5.6, 6.0))
    assert picture_cards.animation_windows(
        dict(card, entrance="none", exit="none")) == (None, None)


def test_the_dissolves_inner_edges_split_the_blocks(tmp_path):
    card = dict(T.CARD, start=1.0, end=5.3, source=[0, 0, 1, 1],
                entrance="fade", exit="fade", duration_s=.3)
    graph, _a, _t = T._graph(T._edl([card]), tmp_path)
    # 3 kept segments; the card's edges and its dissolves' inner edges
    # (1.3 and 5.0) split the first two: 7 blocks
    assert "concat=n=7:v=1:a=1" in graph
    # the dissolving blocks are the shot with the box fading in / out (on
    # the block's clock), the whole card fading at the same moments (on
    # the program's)
    assert graph.count("fade=t=in:st=0.000000:d=0.300000:alpha=1") == 1
    assert graph.count("fade=t=out:st=0.000000:d=0.300000:alpha=1") == 1
    assert "fade=t=in:st=1.000000:d=0.300000:alpha=1" in graph
    assert "fade=t=out:st=5.000000:d=0.300000:alpha=1" in graph


# ── pixels ────────────────────────────────────────────────────────────────

@needs_ffmpeg
@pytest.mark.parametrize("stacked", [False, True])
def test_a_source_card_dissolves_from_and_to_the_shot(tmp_path, stacked):
    """Mid-segment entrance and exit: the backdrop above the box fades
    between the shot and the card, the footage inside the box between the
    shot's crop and the card's source rect — every frame on the straight
    line between them, never the bare canvas."""
    if stacked:
        card = dict(T.CARD, start=1.0, end=5.3, entrance="fade", exit="lift",
                    duration_s=.3, panels=[
                        {"box": [.05, .3, .95, .55], "source": [0, 0, .32, 1]},
                        {"box": [.05, .62, .95, .9], "source": [.68, 0, 1, 1]}])
    else:
        card = dict(T.CARD, start=1.0, end=5.3, source=[0, 0, 1, 1],
                    entrance="fade", exit="fade", duration_s=.3)
    frames = T._render(tmp_path, T._edl([card]))
    for lo, hi in ((29, 40), (149, 160)):           # entrance, exit
        cr = [int(frames[i][2][BOX_Y, 40]) for i in range(lo, hi + 1)]
        cb_above = [int(frames[i][1][30, 160]) for i in range(lo, hi + 1)]
        # the box: the 9:16 crop's neutral middle <-> the source's red left
        assert _monotone(cr) and _between(cr, 128, 200)
        assert max(cr) > 175 and min(cr) < 135
        # above the box: the shot <-> the card's backdrop (#406080), in a
        # straight line (a bare-canvas frame would jump to the backdrop)
        assert _monotone(cb_above) and _between(cb_above, 128, 147)
        steps = [abs(b - a) for a, b in zip(cb_above, cb_above[1:])]
        assert max(steps) <= 4


@needs_ffmpeg
def test_a_card_that_opens_on_a_cut_cuts_in_populated(tmp_path):
    """Starting on the 1.87 s jump cut, a 'fade' entrance hard-cuts: the
    first card frame is the whole card, footage and backdrop at once."""
    card = dict(T.CARD, start=1.87, end=4.0, source=[0, 0, 1, 1],
                entrance="fade", exit="none", duration_s=.3)
    frames = T._render(tmp_path, T._edl([card]))
    kinds = [T._is_card(f) for f in frames]
    first = kinds.index(True)
    assert abs(first - round(1.87 * 30)) <= 1
    f = frames[first]
    assert f[2][BOX_Y, 40] > 190                     # the source's red third
    assert not T._is_card(frames[first - 1])


@needs_ffmpeg
def test_a_program_card_dissolves_as_a_whole(tmp_path):
    """A program card's footage is the program: the finished card fades in
    over the shot (its backdrop never appears on its own)."""
    card = dict(T.CARD, start=1.0, end=4.0, entrance="fade", exit="none",
                duration_s=.3, fit="crop")
    frames = T._render(tmp_path, T._edl([card]))
    cb = [int(frames[i][1][30, 160]) for i in range(29, 41)]
    assert _monotone(cb) and _between(cb, 128, 147)
    assert max(abs(b - a) for a, b in zip(cb, cb[1:])) <= 4
