"""A held source frame still has a shot and must use that shot's evidence."""
import pytest

from score import source_guard


@pytest.mark.parametrize("at,face", [(0., [.1, .2, .25, .5]),
                                    (1., [.75, .2, .9, .5])])
def test_held_frame_uses_the_shot_containing_its_source_time(at, face):
    item = dict(id="held", k="shot", pic="main", src=[at, at],
                f=[0, 7], crop=[0., -1., 1., 2.])
    film = dict(frame=[720, 1280], items=[item])
    index = dict(_first_film_source_guard=True,
                 video=dict(width=1280, height=720, duration=2.),
                 shots=[dict(start=0., end=1.), dict(start=1., end=2.)],
                 spatial=dict(samples=[
                     dict(t=.25, faces=[[.1, .2, .25, .5]]),
                     dict(t=1.25, faces=[[.75, .2, .9, .5]])]))
    report = source_guard.apply(film, index, {})
    assert report[0]["reason"] == "local_subject_crop"
    assert item["src"] == [at, at] and item["f"] == [0, 7]
    assert len(item["crop"]) == 7
    for crop in item["crop"]:
        assert crop[0] <= face[0] < face[2] <= crop[2]
        assert crop[1] <= face[1] < face[3] <= crop[3]


def test_held_first_frame_without_local_evidence_still_fails_closed():
    film = dict(frame=[720, 1280], items=[dict(
        id="held", k="shot", pic="main", src=[0., 0.], f=[0, 7])])
    index = dict(_first_film_source_guard=True,
                 video=dict(width=1280, height=720, duration=2.),
                 shots=[dict(start=0., end=1.), dict(start=1., end=2.)],
                 spatial=dict(samples=[dict(t=1.25, faces=[[.7, .2, .9, .5]])]))
    with pytest.raises(ValueError, match="awaits local subject"):
        source_guard.apply(film, index, {})


def test_held_static_first_frame_keeps_its_wide_reading_track():
    film = dict(frame=[720, 1280], items=[dict(
        id="slide", k="shot", pic="main", src=[0., 0.], f=[0, 7])])
    index = dict(_first_film_source_guard=True,
                 video=dict(width=1280, height=720, duration=2.))
    per = dict(shots=[dict(t=[0., 2.], change=0., faces_seen=0)],
               text=[dict(t=[0., 2.], seen=4, box=[.1, .2, .9, .3])])
    report = source_guard.apply(film, index, per)
    assert report[0]["reason"] == "static_source_text_fit"
    crop = film["items"][0]["crop"]
    assert crop[0] <= .1 < .9 <= crop[2]
    assert crop[1] <= .2 < .3 <= crop[3]
