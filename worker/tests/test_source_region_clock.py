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


def test_caption_avoidance_and_region_share_the_camera_after_a_trim():
    from pytest import approx
    edl = {"keep": [[10, 30]], "frame": {"ratio": "9:16", "mode": "crop", "focus_x": .38},
           "effects": {"zooms": [{"id": "z", "start": 5, "end": 8, "mode": "punch",
                                  "strength": .1, "ramp_s": 0, "cy": .3, "cx": .5}]}}
    ctx = SimpleNamespace(index={"video": {"width": 1920, "height": 1080}})
    box = (.32, .2, .48, .5)
    before = agent_tools._source_box_to_output(ctx, edl, 14., box)
    during = agent_tools._source_box_to_output(ctx, edl, 16., box)
    assert (during[3] - during[1]) == approx(1.1 * (before[3] - before[1]), abs=.002)
    note = agent_tools._source_region_report(ctx, edl, 15., 17., dict(x=.32,y=.2,w=.16,h=.3))
    assert str(tuple(round(v, 3) for v in during)) in note


def test_caption_avoidance_reads_each_kept_segment_across_a_cut():
    import caption_place
    from timeline import Timeline
    edl = {"keep": [[2, 3], [14, 15]]}
    left, right = (.1, .2, .3, .4), (.6, .7, .8, .9)
    index = {"video": {"width": 640, "height": 640}, "spatial": {"samples": [
        {"t": 2.5, "props": [left]}, {"t": 14.5, "props": [right]}]}}
    assert caption_place._mapped(edl, index, Timeline(edl["keep"]), 640, 640, 0., 2., "props") == [left, right]
