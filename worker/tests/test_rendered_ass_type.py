import copy
import shutil
import subprocess
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

import graphics
import motion_critic
import render_qc
import rendered_ass_type as title


def test_local_bright_picture_under_one_word_is_not_hidden_by_dark_average():
    mask = Image.new('L', (640, 360))
    draw = ImageDraw.Draw(mask)
    font = ImageFont.load_default(size=36)
    draw.text((50, 100), 'THE WORLD', font=font, fill=255)
    draw.text((240, 160), 'DOWN', font=font, fill=255)
    frame = Image.new('RGB', mask.size, '#151515')
    frame.paste('white', mask=mask)
    assert title.contrast(frame, mask)['pass']
    ImageDraw.Draw(frame).rectangle((230, 150, 370, 210), fill='#eeeeee')
    frame.paste('white', mask=mask)
    assert np.median(np.asarray(frame)) == 21
    assert title.contrast(frame, mask)['pass'] is False


def test_measurement_preserves_edl_and_reports_words_to_editor():
    texts = [dict(id='title', text='Keep all these words', start=0, end=4,
                  font='DM Serif Display', font_size=.06, box=True,
                  color='#FFEEAA', shadow=3, motion={'opacity':[{'t':0,'v':0}]})]
    original = copy.deepcopy(texts)
    exposed = title.exposed(texts, 0)
    assert texts == original and exposed[0]['text'] == texts[0]['text']
    assert exposed[0]['font'] == texts[0]['font']
    row = dict(id='title', t=2, lc=10, text=[texts[0]['text']])
    report = {'ordinary_text_contrast':[row]}
    assert texts[0]['text'] in render_qc.measured_findings(report)[0]
    findings, _ = motion_critic.lens_type({'picture_qc':report})
    assert findings[0]['code'] == 'title_low_contrast'
    assert 'Preserve the words' in str(findings[0])


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg unavailable')
@pytest.mark.parametrize('background, fails', [('black', False), ('white', True)])
def test_libass_geometry_is_measured_against_encoded_picture(tmp_path, monkeypatch, background, fails):
    texts = [dict(id='title', text='Keep every word', start=0, end=2,
                  font_size=.12, x=.5, y=.5, color='#FFFFFF',
                  outline_width=0, shadow=0, entrance='none', exit='none')]
    ass = tmp_path/'actual.ass'
    graphics.build_gfx_ass({'texts':texts}, 2, str(ass), (360, 640))
    film = tmp_path/'actual.mp4'
    vf = f"ass='{title._filter_path(ass)}':fontsdir='{title._filter_path(graphics.FONTS_DIR)}'"
    subprocess.run(['ffmpeg','-v','error','-nostdin','-y','-threads','1',
        '-filter_threads','1','-f','lavfi','-i',f'color={background}:s=360x640:r=10:d=2',
        '-vf',vf,'-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(film)],check=True)
    monkeypatch.setattr(title.tempfile, 'tempdir', str(tmp_path))
    original = copy.deepcopy(texts)
    rows, skipped = title.measure(film, texts, 360, 640, 2, time.monotonic()+20)
    assert not skipped
    assert bool(rows) is fails
    assert texts == original
    assert not list(tmp_path.glob('title-ink-*'))
