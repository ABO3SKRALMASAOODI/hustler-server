"""Moving subjects must be measured where the caption actually appears."""
from types import SimpleNamespace
import re

import pytest

import graphics
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


def test_display_seat_cannot_trade_a_brief_face_collision_for_average_clearance():
    ctx = context()
    ctx.faces = [{"t": i / 2, "boxes": [[.4, .16, .6, .3]] if i == 0 else []}
                 for i in range(20)]
    seats = taste.display_seats(ctx, .7, 1, named=[[.5, .24]])
    assert seats and [.5, .24] not in seats
    rows = taste._take_heads(ctx, marks=False)
    for x, y in seats:
        assert taste._clear(rows, [x-.21, y-.03, x+.21, y+.03]) == 1


@pytest.mark.parametrize("moving", [False, True])
def test_fill_enlargement_cannot_reclip_protected_source_text(moving):
    ctx = context()
    ctx.faces = []
    text = [.05, .42, .95, .47]
    ctx.perc = {"text": [{"t": [0, 10], "box": text}]}
    crop = [0, -1.08, 1, 2.08]
    if moving:
        crop = [crop, crop]
    result = compiler.keep_marks_whole(
        ctx, crop, [0, 10], {"w": 1920, "h": 1080}, 1080, 1920)
    for rect in result if moving else [result]:
        assert not compiler._cuts(rect, text)
        assert rect[0] <= text[0] and rect[2] >= text[2]


def test_same_aspect_punch_in_keeps_burned_caption_whole():
    ctx = context()
    ctx.faces = []
    text = [.12, .82, .88, .86]
    ctx.perc = {"text": [{"t": [0, 10], "box": text}]}
    result = compiler.keep_marks_whole(
        ctx, [.15, .15, .85, .85], [0, 10],
        {"w": 1080, "h": 1920}, 1080, 1920)
    assert not compiler._cuts(result, text)
    assert result[3] >= text[3]


@pytest.mark.parametrize("template", ["title", "subtitle", "lower_third"])
@pytest.mark.parametrize("overrides", [{}, {"shadow": 4, "outline_width": 1,
                                          "tracking": 2},
                                      {"shadow": 0, "outline_width": 0}])
def test_explicit_type_keeps_its_edges_and_spacing_when_canvas_turns(template, overrides):
    """Small serif titles must not acquire a portrait-sized ghost silhouette."""
    tx = {"text": "Morgan | Editor", "start": 0, "end": 3,
          "template": template, "font_size": .046, **overrides}
    tags = []
    for size in [(1280, 720), (720, 1280), (720, 720)]:
        event = graphics._compile_item(tx, 3, size)
        tags.append(re.findall(r"\\(fs|fsp|bord|shad)([\d.]+)", event["text"]))
    assert tags[0] == tags[1] == tags[2]


def test_explicit_font_scales_decorations_with_the_type_size():
    tx = {"text": "Morning", "start": 0, "end": 3, "template": "subtitle",
          "font_size": .04, "shadow": 4}
    def shadow(scale):
        event = graphics._compile_item({**tx, "size_scale": scale}, 3, (720, 1280))
        return float(re.search(r"\\shad([\d.]+)", event["text"])[1])
    assert shadow(2) == pytest.approx(2 * shadow(1), abs=.1)
