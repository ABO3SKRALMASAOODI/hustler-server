from types import SimpleNamespace
import cv2
import numpy as np

import agent_tools
import inpaint
from schemas import RegionItem


def test_source_region_survives_insert_and_maps_to_every_kept_showing():
    edl = {"keep": [[75, 80], [85, 96]], "inserts": [
        {"id":"i", "at_output_s":5, "duration_s":10, "kind":"image"}]}
    ctx = SimpleNamespace(latest_edl=lambda: {"json": edl},
                          write_edl=lambda e, note: edl.update(e) or note)
    result = agent_tools.blur_region(ctx, .1, .6, .4, .3, start=77, end=95)
    item = RegionItem(**edl["effects"]["regions"][0])
    assert item.source_span == (77, 95)
    assert "2.00-5.00s, 15.00-25.00s" in result
    assert item.start is None
    agent_tools.blur_region(ctx, .1, .6, .4, .3, start=2, end=5, time_space="program")
    legacy = RegionItem(**edl["effects"]["regions"][1])
    assert legacy.start == 2 and legacy.source_span is None


def test_coloured_letters_on_an_equal_luma_field_are_measured():
    field = np.full((160, 360, 3), (0, 100, 0), np.uint8)
    cv2.putText(field, "sample caption", (15, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.1,
                (0, 0, 196), 2, cv2.LINE_AA)
    assert not inpaint.ink_mask(field).any()
    mask = inpaint.ink_mask(field, colour=True)
    assert mask[70:120, 20:240].any()
    assert inpaint._line_mask(field)[90:105].any()
