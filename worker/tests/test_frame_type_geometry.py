"""Moving subjects must be measured where the caption actually appears."""
from types import SimpleNamespace

import pytest

from score import compiler, taste


def context():
    shot = {"pic": "main", "src": [0, 10], "f": [0, 300],
            "crop": [0, 0, 1, 1]}
    return SimpleNamespace(
        W=1080, H=1920, fps=30, dur_f=300, shots=[shot],
        dna={"seat": [.5, .62]}, wf_any={}, wfe_any={},
        perc={}, index={}, faces=[
            {"t": i / 2, "boxes": [[.4, .52, .6, .66] if i == 0
                                    else [.4, .16, .6, .3]]}
            for i in range(20)])


def test_caption_clears_a_face_that_moves_out_before_the_shot_midpoint():
    ctx = context()
    seat = taste.caption_seat(ctx)
    rect = [.14, seat - taste.CAP_BLOCK_H / 2,
            .86, seat + taste.CAP_BLOCK_H / 2]
    rows = taste._take_heads(ctx)
    assert len(rows) == 20
    assert all(not compiler.rects_touch(rect, head)
               for row in rows for head in row)
    assert seat != .62


def test_perception_does_not_discard_other_indexed_faces():
    ctx = context()
    ctx.perc = {"samples": [{"t": 0, "face": [.1, .1, .2, .2]}]}
    boxes = taste._heads_near(ctx, ctx.shots[0], 0)
    assert [.4, .52, .6, .66] in boxes
    assert [.1, .1, .2, .2] in boxes


def test_face_box_uses_current_crop_even_without_perception():
    ctx = context()
    shot = ctx.shots[0]
    shot["crop"] = [[0, 0, 1, 1]] * 150 + [[.2, .1, .8, .9]] * 150
    early = taste._heads_near(ctx, shot, 0)
    late = taste._heads_near(ctx, shot, 270)
    assert [.4, .52, .6, .66] in early
    expected = compiler.through_crop([.4, .16, .6, .3], [.2, .1, .8, .9])
    assert any(box == pytest.approx(expected) for box in late)


def test_sparse_face_track_is_not_treated_as_an_empty_frame():
    ctx = context()
    ctx.faces = [{"t": 1, "boxes": [[.4, .52, .6, .66]]}]
    assert [.4, .52, .6, .66] in taste._heads_near(ctx, ctx.shots[0], 0)
