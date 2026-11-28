import numpy as np
from picture_map import _fill_rows
from tools_ext.photo_tools import _needs_fill


def test_fill_follows_asymmetric_and_changing_bands_in_one_indexed_shot():
    rng = np.random.default_rng(3)
    dark = rng.integers(0, 75, (100, 60), dtype=np.uint8)
    banded = dark.copy()
    banded[:14] = 1
    banded[-6:] = 0
    frames = list(enumerate([dark]*3 + [banded]*5))
    rows = _fill_rows({"start": 0, "end": 8}, frames, 1)
    assert [(r["start"], r["end"]) for r in rows] == [(0, 3), (3, 8)]
    assert rows[1]["rect"] == [0, .14, 1, .94]
    assert _needs_fill(rows[1], .75, {"width": 60, "height": 100})
