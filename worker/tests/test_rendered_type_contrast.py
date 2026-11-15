"""Custom HTML's shadow/opaque panel is backing, never a glyph mask."""
import base64
from io import BytesIO
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

import motion_critic
import motion_engine
import motion_templates
import render_qc
import rendered_type


def _exposure():
    im = Image.new('RGBA', (360, 200))
    ImageDraw.Draw(im).text((35, 70), 'Keep every word', font=ImageFont.load_default(size=28),
                           fill='white')
    buf = BytesIO(); im.save(buf, 'PNG')
    return im, base64.b64encode(buf.getvalue()).decode()


@pytest.mark.parametrize('background,passes', [(235, False), (15, True)])
def test_contrast_reads_finished_picture_instead_of_transparent_graphic(background, passes):
    mask, encoded = _exposure()
    frame = Image.new('RGB', mask.size, (background,) * 3)
    frame.paste(mask, mask=mask.getchannel('A'))
    assert rendered_type.contrast(frame, encoded)['pass'] is passes


def test_real_backing_makes_light_type_readable_on_a_bright_picture():
    mask, encoded = _exposure()
    frame = Image.new('RGB', mask.size, 'white')
    ImageDraw.Draw(frame).rectangle((25, 60, 290, 115), fill='#121212')
    frame.paste(mask, mask=mask.getchannel('A'))
    assert rendered_type.contrast(frame, encoded)['pass']


def test_measured_failure_reaches_editor_with_words_preserved():
    row = dict(id='closing', t=16.3, lc=22.1, text=['NOT ALL THOSE WHO WANDER ARE LOST.'])
    report = dict(type={'low_contrast_html': [row]})
    assert 'finished picture' in render_qc.measured_findings(report)[0]
    found, _ = motion_critic.lens_type({'picture_qc': report})
    assert found[0]['code'] == 'html_type_low_contrast'
    assert 'Preserve the words' in str(found[0])


@pytest.mark.skipif(not motion_engine.available(), reason='headless Chromium unavailable')
def test_glyph_exposure_excludes_cards_and_restores_the_next_animation_frame():
    html = '''<style>html,body{margin:0;background:white}</style>
    <div style="position:absolute;inset:0;background:#345">
    <div style="font:80px Arial;color:white;text-shadow:0 3px 12px black">Visible words</div>
    <div style="font:70px Arial;color:transparent">Invisible words</div>
    <div style="opacity:0;font:70px Arial">Hidden animation</div>
    <div style="background:red;width:300px;height:100px"></div></div>'''
    item = dict(id='card', template='html', start=0, end=3, params={}, html=html)
    job = motion_templates.build_job(item, 1080, 1920, 30)
    job.glyph_exposure = True
    report = motion_engine.probe([job], [[1., 2.]], budget_s=30, measure=render_qc.ink_lines)[0]
    assert not report['errors']
    a, b = report['glyph_masks']
    aa = np.asarray(Image.open(BytesIO(base64.b64decode(a))).convert('RGBA'))[:, :, 3]
    assert .001 < (aa >= 204).mean() < .04
    assert aa[120:].max() == 0, 'Cards and hidden text must not become glyphs'
    assert a == b, 'Measuring cannot leave mutated styles on the next frame'
    assert report['measures'][0]['box'] == report['measures'][1]['box'] == [0., 0., 1., 1.]
