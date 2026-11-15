"""Face following must not classify a chair or collar as another person."""
import numpy as np
import pytest
import cv2
import follow
import keepout


def test_learned_faces_override_false_group_cascade_boxes(monkeypatch):
    real = [.39, .26, .56, .67]
    monkeypatch.setattr(keepout, 'yunet_detect', lambda im: [(real, .93)])
    rows = follow.detect(np.zeros((252, 448), np.uint8), cv2, [])
    assert rows == [(real, 0)]
    counts = follow.face_counts([(t, rows) for t in range(8)])
    assert not follow.is_group(counts, 0, 7)


def test_two_measured_people_remain_a_group(monkeypatch):
    faces = [([.1, .2, .3, .6], .94), ([.6, .2, .8, .6], .95)]
    monkeypatch.setattr(keepout, 'yunet_detect', lambda im: faces)
    rows = follow.detect(np.zeros((252, 448), np.uint8), cv2, [])
    assert follow.is_group(follow.face_counts([(t, rows) for t in range(8)]), 0, 7)


def test_roi_faces_are_mapped_back_to_the_source_canvas(monkeypatch):
    monkeypatch.setattr(keepout, 'yunet_detect', lambda im: [([.2, .1, .8, .9], .94)])
    rows = follow.detect(np.zeros((200, 400), np.uint8), cv2, [], roi=[.25,.2,.75,.8])
    assert rows[0][0] == pytest.approx([.35,.26,.65,.74])


@pytest.mark.parametrize('learned', [None, []])
def test_unavailable_or_empty_model_retains_cascade_fallback(monkeypatch, learned):
    import subject
    monkeypatch.setattr(keepout, 'yunet_detect', lambda im: learned)
    monkeypatch.setattr(subject, '_faces_in', lambda *a: [(40,30,80,80)])
    rows = follow.detect(np.zeros((200,400), np.uint8), cv2, [object()])
    assert rows == [([.1,.15,.3,.55], 0)]
