"""Automatic caption anchors must preserve the upload's reading track."""
from copy import deepcopy

import caption_place
import first_cut_type
import keepout
from schemas import default_edl
from timeline import Timeline


def scene():
    edl = default_edl(4)
    edl['frame'] = {'ratio': '9:16', 'mode': 'pad_blur'}
    edl['effects'] = {'zooms': [dict(start=0, end=4, mode='punch',
                                    strength=.15, ramp_s=0, cx=.5, cy=.5)]}
    index = {'video': {'width': 1920, 'height': 1080}, 'spatial': {'samples': [
        dict(t=i / 2, text=[[.1, .52, .9, .70]], dense_ui=False,
             faces=[[.20, .16, .28, .35], [.45, .16, .53, .35]])
        for i in range(9)]}}
    style = dict(preset='reels', motion_look='serif', single_line=True,
                 anchor_y=.58, size_scale=1.6)
    return edl, index, style


def test_slide_question_clears_the_unchanged_caption_block_under_a_zoom():
    edl, index, style = scene()
    before = deepcopy((edl, index, style))
    fixed, report = first_cut_type.place(edl, index, style, 720, 1280)
    assert report['moved'] and report['clear']
    assert fixed['size_scale'] == style['size_scale']
    assert {k: v for k, v in fixed.items() if k != 'anchor_y'} == {
        k: v for k, v in style.items() if k != 'anchor_y'}
    metrics = caption_place.block_metrics(
        dict(edl, captions={'mode': 'from_transcript', 'style': fixed}), 720, 1280)
    lo, hi = caption_place.natural_block(fixed['anchor_y'], *metrics)
    tl = Timeline(edl['keep'])
    faces = keepout.zones_of(keepout.face_track(edl, index, 720, 1280, 0, 4))
    for i in range(8):
        boxes = caption_place.text_boxes(edl, index, tl, 720, 1280, i/2, i/2)
        assert not caption_place.hits(lo, hi, boxes + faces, (.15, .85))
    assert (edl, index, style) == before


def test_clear_automatic_anchor_is_not_moved():
    edl, index, style = scene()
    style['anchor_y'] = .72
    fixed, report = first_cut_type.place(edl, index, style, 720, 1280)
    assert fixed == style and report['reason'] == 'source text clear'


def test_no_clear_band_preserves_caption_words_and_size():
    edl, index, style = scene()
    edl['frame'] = {'ratio': '9:16', 'mode': 'crop'}
    edl['effects']['zooms'] = []
    for sample in index['spatial']['samples']:
        sample.update(text=[[0, 0, 1, 1]], dense_ui=True)
    fixed, report = first_cut_type.place(edl, index, style, 720, 1280)
    assert fixed == style and report['clear'] is False
    assert 'captions' not in edl or edl['captions'] is None


def test_unanchored_style_stays_for_the_existing_caption_director():
    edl, index, style = scene()
    del style['anchor_y']
    fixed, report = first_cut_type.place(edl, index, style, 720, 1280)
    assert fixed == style and not report['moved']


def test_whole_upload_checks_the_late_program_with_bounded_geometry(monkeypatch):
    edl, index, style = scene()
    edl['keep'] = [[0, 3600]]
    times = []

    def boxes(edl, index, tl, W, H, a, b):
        times.append(a)
        return [[.1, .54, .9, .60]] if a > 3500 else []

    monkeypatch.setattr(caption_place, 'text_boxes', boxes)
    fixed, report = first_cut_type.place(edl, index, style, 720, 1280)
    assert len(times) <= keepout.MAX_TRACK
    assert times[0] == 0 and times[-1] > 3599
    assert report['moved'] and fixed['anchor_y'] != style['anchor_y']


def test_first_film_passes_the_measured_anchor_to_the_caption_tool(monkeypatch):
    import first_cut
    from test_first_cut_visual import _talking_chain
    calls = []
    original = first_cut._run

    def placed(edl, index, style, W, H, measure=None):
        return dict(style, anchor_y=.72), {'moved': True, 'clear': True}

    def run(ctx, steps, name, args, started):
        if name == 'add_captions':
            calls.append(args)
        return original(ctx, steps, name, args, started)

    monkeypatch.setattr(first_cut_type, 'place', placed)
    monkeypatch.setattr(first_cut, '_run', run)
    _talking_chain(monkeypatch, True)
    assert len(calls) == 1 and calls[0]['style']['anchor_y'] == .72
    assert calls[0]['mode'] == 'from_transcript'


def test_text_free_excerpt_measures_faces_before_accepting_anchor():
    edl = default_edl(60)
    edl['keep'] = [[180, 225]]
    edl['frame'] = {'ratio': '9:16', 'mode': 'crop'}
    index = {'video': {'width': 720, 'height': 1280}}
    style = dict(preset='reels', anchor_y=.58, single_line=True)
    sampled = []

    def measure(t):
        sampled.append(t)
        return [[.25, .30, .70, .65]]

    fixed, report = first_cut_type.place(edl, index, style, 720, 1280, measure)
    assert report['moved'] and report['clear']
    assert 2 <= len(sampled) <= 60
    assert min(sampled) < 181 and max(sampled) > 224
    assert {k: v for k, v in fixed.items() if k != 'anchor_y'} == {
        k: v for k, v in style.items() if k != 'anchor_y'}
    one, tall, motion = caption_place.block_metrics(
        dict(edl, captions={'mode': 'from_transcript', 'style': fixed}), 720, 1280)
    lo, hi = caption_place.natural_block(fixed['anchor_y'], one, tall, motion)
    assert not caption_place.hits(lo, hi, [[.25, .30, .70, .65]], (.15, .85))


def test_failed_automatic_seat_leaves_shot_placement_open(monkeypatch):
    import first_cut
    from test_first_cut_visual import _talking_chain
    calls, treatments = [], []
    original = first_cut._run

    def crowded(edl, index, style, W, H, measure=None):
        treatments.append(dict(style))
        return dict(style, anchor_y=.7), {
            'moved': False, 'clear': False,
            'reason': 'no band fits unchanged captions'}

    def run(ctx, steps, name, args, started):
        if name == 'add_captions':
            calls.append(args)
        return original(ctx, steps, name, args, started)

    monkeypatch.setattr(first_cut_type, 'place', crowded)
    monkeypatch.setattr(first_cut, '_run', run)
    _talking_chain(monkeypatch, True)
    assert len(calls) == 1
    assert calls[0]['mode'] == 'from_transcript'
    assert calls[0]['style'] == {
        k: v for k, v in treatments[0].items()
        if k not in ('anchor_y', 'position')}
