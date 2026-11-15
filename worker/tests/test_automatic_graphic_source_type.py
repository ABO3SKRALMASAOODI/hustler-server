"""Automatic display words must leave the upload's reading track visible."""
from copy import deepcopy
from types import SimpleNamespace

import keepout
import motion_templates
import motion_tools
from schemas import default_edl


def scene():
    edl = default_edl(4)
    edl['frame'] = {'ratio': '9:16', 'mode': 'pad_blur'}
    edl['effects'] = {'zooms': [dict(start=0, end=4, mode='punch',
                                    strength=.15, ramp_s=0, cx=.5, cy=.5)]}
    index = {'video': {'width': 1920, 'height': 1080}, 'spatial': {'samples': [
        dict(t=i/2, text=[[.1, .52, .9, .70]], dense_ui=False, faces=[])
        for i in range(9)]}}
    item = dict(id='display', template='word_slam', start=1., end=2.5,
                params=motion_templates.check_params('word_slam', dict(
                    text='Build confidence', width=.82, y=.58,
                    entrance='pop', exit='cut')))
    return edl, SimpleNamespace(index=index, first_cut_auto_graphic=True,
                                has_main_video=True), item


def test_automatic_words_trigger_estimated_relocation_without_a_face(monkeypatch):
    edl, ctx, item = scene()
    monkeypatch.setattr(motion_tools, '_canvas_size', lambda *a: (720, 1280))
    monkeypatch.setattr(motion_tools, '_face_measure', lambda *a: None)
    source = motion_tools._automatic_source_words(ctx, edl, item, 720, 1280)
    original = deepcopy(item)
    box = keepout.nominal_ink(item['template'], motion_templates.spec(item['template']),
                              item['params'], frame=(720, 1280))
    assert any(keepout.inter(box, z) > 1e-6 for z in source)
    note, _ = motion_tools._keep_out_estimated(ctx, edl, item)
    assert "upload's printed words" in note
    assert item['params']['text'] == original['params']['text']
    assert all(keepout.inter(item['footprint']['box'], z) <= 1e-6 for z in source)


def test_source_words_are_not_visible_under_a_full_frame_card():
    edl, ctx, item = scene()
    edl['vectors'] = [dict(kind='rectangle', start=0, end=4, x=.5, y=.5,
                           width=1., height=1., opacity=1.)]
    assert motion_tools._automatic_source_words(ctx, edl, item, 720, 1280) == []
    edl['vectors'][0]['width'] = .3
    assert motion_tools._automatic_source_words(ctx, edl, item, 720, 1280)


def test_explicit_editor_graphic_keeps_the_existing_placement_policy():
    edl, ctx, item = scene()
    ctx.first_cut_auto_graphic = False
    assert motion_tools._automatic_source_words(ctx, edl, item, 720, 1280) == []


def test_real_probe_cannot_accept_predicted_clearance_when_ink_still_hits(monkeypatch):
    edl, ctx, item = scene()
    source = motion_tools._automatic_source_words(ctx, edl, item, 720, 1280)
    spec = motion_templates.spec(item['template'])
    box = (.1, .54, .8, .64)
    assert any(keepout.inter(box, z) > 1e-6 for z in source)
    monkeypatch.setattr(motion_tools, '_probe_items', lambda items, *a: [
        {'visible_frames': 1} for _ in items])
    monkeypatch.setattr(keepout, 'settled_ink', lambda *a: box)
    # Simulate an inaccurate prediction: real ink still covers the source.
    assert motion_tools._relocate(item, spec, box, [], [], 720, 1280,
                                   ([], []), pin='width', obstacles=source) is None


def test_automatic_display_actual_ink_clears_the_upload_reading_track():
    import motion_engine
    import pytest
    if not motion_engine.available():
        pytest.skip('no headless Chromium')
    edl, ctx, item = scene()
    source = motion_tools._automatic_source_words(ctx, edl, item, 720, 1280)
    spec = motion_templates.spec(item['template'])
    rep = motion_tools._probe_item(item, 720, 1280)
    assert rep and not rep.get('errors')
    box = keepout.settled_ink(rep, motion_tools._probe_times(item))
    assert any(keepout.inter(box, z) > 1e-6 for z in source)
    result = motion_tools._relocate(item, spec, box, [], [], 720, 1280,
                                    ([], []), pin='width', obstacles=source)
    assert result, 'A readable unchanged display phrase must find a clear band'
    _, actual, params, _ = result
    assert all(keepout.inter(actual, z) <= 1e-6 for z in source)
    assert params['text'] == item['params']['text']
    assert params['width'] == item['params']['width']
    # This test proves relocation preserves the original glyph size. The
    # independent frame audit still decides whether that design is readable.
    assert actual[3] - actual[1] >= box[3] - box[1] - .005
