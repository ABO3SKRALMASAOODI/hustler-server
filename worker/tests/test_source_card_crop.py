"""Source cards keep their own picture shape through final clearance."""
import copy

import pytest

from score import source_guard


@pytest.mark.parametrize("width,height,crop", [
    (.9, .284765625, [0., 0., 1., 1.]),  # a 16:9 inset on a portrait canvas
    (.8, .45, [.25, 0., .8125, 1.]),    # a square inset
])
def test_safe_source_card_is_not_recropped_to_the_films_shape(width, height, crop, tmp_path):
    item = dict(id="source-card", k="card", pic="main", src=[0., 2.],
                f=[0, 60], w=width, h=height, crop=crop, x=.5, y=.4,
                s=[1. - n / 600 for n in range(60)])
    film = dict(frame=[1080, 1920], items=[item])
    index = dict(_first_film_source_guard=True,
                 video=dict(width=1920, height=1080, duration=2.),
                 spatial=dict(samples=[dict(t=t, faces=[[.3, .2, .7, .7]]) for t in (0., 1.)]))
    before = copy.deepcopy(film)
    report = source_guard.apply(film, index, {})
    assert film == before
    assert report[0]["reason"] == "subject_boxes_contained"
    # Lowering infers a video card's height from its retained source crop.
    # A portrait crop here therefore also deforms the authored layout.
    from types import SimpleNamespace
    from score import lower
    film.update(fps=30, assets={"main": dict(kind="video", path="source.mp4",
                                           w=1920, h=1080)})
    overlay, = lower.lower_cards(film, str(tmp_path), SimpleNamespace(locals={}), {})
    c = overlay.get("source_crop", [0., 0., 1., 1.])
    picture_aspect = 1920 * (c[2] - c[0]) / (1080 * (c[3] - c[1]))
    assert picture_aspect == pytest.approx(1080 * width / (1920 * height), abs=.001)


def test_unsafe_card_reframes_in_its_own_shape_and_keeps_the_subject():
    item = dict(id="source-card", k="card", pic="main", src=[0., 2.],
                f=[0, 60], w=.8, h=.45, crop=[0., 0., .2, 1.])
    film = dict(frame=[1080, 1920], items=[item])
    face = [.7, .2, .9, .7]
    index = dict(_first_film_source_guard=True,
                 video=dict(width=1920, height=1080, duration=2.),
                 spatial=dict(samples=[dict(t=t, faces=[face]) for t in (0., 1.)]))
    source_guard.apply(film, index, {})
    for c in item["crop"]:
        assert (c[2] - c[0]) / (c[3] - c[1]) == pytest.approx(.5625)
        assert c[0] <= face[0] < face[2] <= c[2]
        assert c[1] <= face[1] < face[3] <= c[3]
