from unittest.mock import Mock
from renderer import _compose_join


def test_only_same_shot_dissolve_resolves_to_cut():
    order = [("seg", 0, 4), ("seg", 1, 2)]
    idx = {"shots": [{"start": 0, "end": 10}, {"start": 10, "end": 13}]}
    dissolve = {"id": "d", "style": "dissolve"}
    wipe = {"id": "w", "style": "slide"}
    def join(at, items, index=idx):
        return _compose_join(dict(k=0, items=items), order, [(0, 4), (at, at+2)],
                             index, 30, Mock())
    assert join(5, [dissolve]) is None
    assert join(5, [dissolve, wipe])["items"] == [wipe]
    assert join(10, [dissolve])["items"] == [dissolve]
    assert join(5, [dissolve], {})["items"] == [dissolve]
    assert join(5, [dict(dissolve, hold=True)]) is not None


def test_handle_hold_does_not_make_matching_source_pictures_dissolve():
    import numpy as np
    background = np.random.default_rng(4).integers(0, 255, (64, 96, 3), dtype=np.uint8)
    incoming = background.copy()
    incoming[20:45, 40:55] = 128
    cap = Mock()
    cap.read.side_effect = [(True, background), (True, incoming)]
    join = dict(k=0, items=[dict(style="dissolve", hold=True)])
    order = [("seg", 0, 3), ("seg", 1, 4)]
    assert _compose_join(join, order, [(0,3),(5,9)], {}, 30, Mock(), cap) is None
    cap.read.side_effect = [(True, background), (True, 255-background)]
    assert _compose_join(join, order, [(0,3),(5,9)], {}, 30, Mock(), cap) == join
