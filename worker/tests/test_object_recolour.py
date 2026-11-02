import copy
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(__file__))
import fxgraph
import object_recolour as rc
import schemas
import world_defs
from tools_ext import world_tools as wt
from test_w3f_agent import Ctx


OBJECT = {'id': 'ob1', 'type': 'object', 'stage': 'world', 'start': 0.,
          'end': 6., 'prompt': {'src_t': 1., 'box': [.1, .2, .4, .6]}}
EFFECT = {'id': 'rc1', 'type': 'recolour', 'stage': 'world', 'start': 0.,
          'end': 6., 'object_id': 'ob1', 'from_colour': '#b41919', 'colour': '#2050be'}


def edl(*items):
    return {'keep': [[0., 12.]], 'fx': copy.deepcopy(list(items))}


def test_schema_and_executor_compiler_register_the_capability():
    result = schemas.validate_edl(edl(OBJECT, EFFECT), 12.)
    assert result.fx[1].type == 'recolour'
    assert fxgraph.compiler_for('recolour') is rc.COMPILER
    # References are checked against all items, independent of list order.
    ordered = schemas.validate_edl(edl(EFFECT, OBJECT), 12.).fx
    assert next(x for x in ordered if x.type == 'recolour').object_id == 'ob1'


@pytest.mark.parametrize('changes', [
    {'end': 12.}, {'start': -.1}, {'object_id': 'missing'}, {'stage': 'screen'},
    {'colour': 'blue;movie=/etc/passwd'},
    {'tolerance': float('nan')},
])
def test_invalid_region_colour_or_span_cannot_reach_renderer(changes):
    with pytest.raises(ValueError):
        schemas.validate_edl(edl(OBJECT, dict(EFFECT, **changes)), 12.)


def test_lut_preserves_shading_neutrals_and_other_hues():
    red = rc.recolour_rgb((.7, .1, .1), '#b41919', '#2050be')
    dark = rc.recolour_rgb((.35, .05, .05), '#b41919', '#2050be')
    assert red[2] > red[0] * 3
    assert np.allclose(np.array(dark) * 2, red)
    for rgb in ((.2, .7, .2), (.5, .5, .5), (0., 0., 0.), (1., 1., 1.)):
        assert rc.recolour_rgb(rgb, '#b41919', '#2050be') == rgb


@pytest.mark.parametrize('source,level', [('#808080', .5), ('#ffffff', .8), ('#000000', .12)])
def test_neutral_surfaces_gain_colour_without_flattening_detail(source, level):
    bright = rc.recolour_rgb((level,) * 3, source, '#2050be')
    dark = rc.recolour_rgb((level * .65,) * 3, source, '#2050be')
    assert bright[2] > bright[0] * 2 and max(bright) > max(dark) > 0
    assert rc.recolour_rgb((1., 0., 0.), source, '#2050be') == (1., 0., 0.)
    assert rc.recolour_rgb((level,) * 3, source, source) == (level,) * 3
    assert schemas.validate_edl(edl(OBJECT, dict(EFFECT, from_colour=source)), 12.)


def test_tool_authors_bounded_effect_and_removal_cleans_references(monkeypatch):
    import remote
    monkeypatch.setattr(remote, 'executor_supports', lambda cap: True)
    monkeypatch.setattr(wt, '_launch_source', lambda ctx: None)
    ctx = Ctx(edl(OBJECT))
    out = wt.recolour_object(ctx, 'object:ob1', '#b41919', '#2050be')
    assert not out.startswith('REJECTED')
    assert [x['type'] for x in ctx.edl['fx']] == ['object', 'recolour']
    assert ctx.edl['fx'][1]['end'] == 6.
    previous = copy.deepcopy(ctx.edl)
    assert wt.recolour_object(ctx, 'ob1', '#b41919', '#2050be', end=12).startswith('REJECTED')
    assert ctx.edl == previous
    assert not wt.remove_world(ctx, ['ob1']).startswith('REJECTED')
    assert not ctx.edl.get('fx')


def test_older_executor_gets_clear_refusal_without_writing(monkeypatch):
    import remote
    monkeypatch.setattr(remote, 'executor_supports', lambda cap: False)
    ctx = Ctx(edl(OBJECT))
    assert wt.recolour_object(ctx, 'ob1', '#b41919', '#2050be').startswith('REJECTED')
    assert not ctx.written


def test_tool_requires_new_selection_after_a_source_shot_change(monkeypatch):
    import remote
    monkeypatch.setattr(remote, 'executor_supports', lambda cap: True)
    ctx = Ctx(edl(OBJECT))
    ctx.index['shots'] = [{'start': 0., 'end': 5.1}, {'start': 5.1, 'end': 7.8}]
    result = wt.recolour_object(ctx, 'ob1', '#b41919', '#2050be')
    assert result.startswith('REJECTED') and '5.10s' in result
    assert not ctx.written


def test_missing_tracking_has_no_scene_grade_substitute(tmp_path, monkeypatch):
    plan = fxgraph.FxPlan(W=128, H=80, fps=5., preview=True,
                          workdir=str(tmp_path), program_s=6.)
    plan.edl = edl(OBJECT, EFFECT)
    monkeypatch.setattr(world_defs, 'status', lambda *args: 'absent')
    rung, reason = rc.COMPILER.prepare(plan, EFFECT)
    assert rung == 'omit' and 'has not been applied' in reason
    assert not getattr(plan, '_recolour_luts', {})


def _encode(path, frames, gray=False):
    height, width = frames.shape[1:3]
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-threads', '1',
                    '-f', 'rawvideo', '-pix_fmt', 'gray' if gray else 'rgb24',
                    '-s', f'{width}x{height}', '-r', '5', '-i', 'pipe:0',
                    '-c:v', 'ffv1', '-pix_fmt', 'gray' if gray else 'yuv420p',
                    str(path)], input=frames.tobytes(), check=True, capture_output=True)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='requires ffmpeg')
@pytest.mark.parametrize('paint', [(180, 25, 25), (180, 180, 180)])
def test_real_filtergraph_follows_mask_preserves_background_and_exact_window(tmp_path, paint):
    frames = np.full((5, 80, 128, 3), 85, np.uint8)
    masks = np.zeros((5, 80, 128), np.uint8)
    frames[:, 4:24, 94:118] = paint  # same colour, a different object
    for i in range(5):
        x = 10 + i * 10
        frames[i, 36:56, x:x + 20] = paint
        masks[i, 36:56, x:x + 20] = 255
    source, mask_file = tmp_path / 'source.mkv', tmp_path / 'mask.mkv'
    _encode(source, frames)
    _encode(mask_file, masks, gray=True)
    target = dict(OBJECT, start=0., end=1.)
    effect = dict(EFFECT, start=.2, end=.8, from_colour='#%02x%02x%02x' % paint)
    plan = fxgraph.FxPlan(W=128, H=80, fps=5., preview=True,
                          workdir=str(tmp_path), program_s=1.)
    plan.edl = edl(target, effect)
    plan._w3_objects = {'ob1': target}
    plan.add_input('objmask:ob1', ['-i', str(mask_file)])
    assert rc.COMPILER.prepare(plan, effect) is None
    extra = []
    plan.bind_inputs(extra, 1)
    parts = []
    ctx = fxgraph.StageCtx(plan, 'world', parts, '0:v')
    label = rc.COMPILER.stage(ctx, [effect])
    assert label != '0:v', plan.report()
    script = tmp_path / 'graph.txt'
    script.write_text(';'.join(parts))
    command = ['ffmpeg', '-v', 'error', '-threads', '1', '-filter_complex_threads', '1',
               '-i', str(source), *extra, '-filter_complex', script.read_text(),
               '-map', f'[{label}]', '-frames:v', '5', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1']
    after = subprocess.run(command, capture_output=True, check=True).stdout
    before = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(source),
                             '-frames:v', '5', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'],
                            capture_output=True, check=True).stdout
    a, b = (np.frombuffer(raw, np.uint8).reshape(5, 80, 128, 3) for raw in (after, before))
    assert np.array_equal(a[[0, 4]], b[[0, 4]])
    assert np.array_equal(a[masks == 0], b[masks == 0])
    for i in (1, 2, 3):
        pixel = a[i, 44, 20 + i * 10]
        assert pixel[2] > 150 and pixel[0] < 60
