"""A source scene must not change underneath a crop when its output run is longer."""
from types import SimpleNamespace
import shutil
import subprocess

import numpy as np
import pytest

from score import lower
from schemas import OverlayItem
import overlay_stream
import renderer
import stitch
from timeline import Timeline


def test_lower_preserves_source_bounds_without_changing_run_duration():
    l1 = dict(fps=30, frame=[1080,1920], spine=[], assets={
        'main': dict(kind='video',path='source.mp4',w=1280,h=720)}, items=[
        dict(k='shot',id='b04.s0',f=[100,122],pic='main',src=[4.7,5.05],crop=[.68,0,1,1])])
    rep = {}
    overlays = lower.lower_pictures(l1, '/tmp', SimpleNamespace(locals={}), rep)[0]
    item = OverlayItem(**overlays[0]).model_dump()
    assert item['duration_s'] == .73
    assert item['source_start_s'] + item['duration_s'] * item['rate'] == pytest.approx(5.05)
    assert item['source_crop'] == [.68,0,1,1]


@pytest.mark.parametrize('offset', [None, 4.7])
def test_preview_window_retains_source_clock(offset):
    item = dict(id='cutaway',asset_key='source',kind='video',fit='cover',
                start=0,duration_s=4,rate=.5,source_start_s=offset)
    edl = dict(keep=[[0,4]],overlays=[item])
    piece = stitch.window_edl(edl, Timeline(edl['keep']), 1, 3)
    actual = piece['overlays'][0]
    assert actual['source_start_s'] == pytest.approx((offset or 0) + .5)
    assert actual['rate'] == .5 and actual['duration_s'] == 2
    assert item['source_start_s'] == offset


@pytest.mark.parametrize('rate', [0, -1, float('nan'), float('inf')])
def test_invalid_source_clocks_are_rejected(rate):
    with pytest.raises(ValueError):
        OverlayItem(id='x',asset_key='source',kind='video',start=0,duration_s=1,rate=rate)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('interval', [(0.2, 0.5), (0.21, 0.04)])
def test_render_does_not_cross_the_authored_source_scene(tmp_path, monkeypatch, stream, interval):
    src = str(tmp_path/'source.mp4')
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'color=red:s=160x288:r=30:d=1.2','-vf',
                    "drawbox=color=blue:t=fill:enable='gte(t,0.4)'",
                    '-c:v','libx264','-threads','1','-preset','ultrafast',src],
                   check=True, capture_output=True)
    # The second interval is shorter than one source frame, as in p5719's
    # 7.1333-7.15s shot. Its preceding decoded frame must still be visible.
    offset, rate = interval
    items = [dict(id=f'ov{i}',asset_key='source',kind='video',fit='cover',
                  start=i*.4,duration_s=.4,source_start_s=offset,rate=rate,
                  source_crop=[.05,.05,.95,.95]) for i in range(8)]
    if not stream:
        monkeypatch.setattr(overlay_stream,'plan',lambda *args: [])
    edl = dict(keep=[[0,3.2]],canvas=dict(width=160,height=288,fps=30),
               frame=dict(ratio='9:16',mode='crop'),overlays=items)
    # A long black base makes accidental underlying frames visible too.
    base = str(tmp_path/'base.mp4')
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'color=black:s=160x288:r=30:d=3.2','-c:v','libx264',
                    '-threads','1','-preset','ultrafast',base],check=True,capture_output=True)
    out = str(tmp_path/'out.mp4')
    renderer.render_edl(edl,dict(words=[],sentences=[],silences=[],video=dict(duration=3.2)),
                        base,out,str(tmp_path),False,suppress_outro=True,asset_locals={'source':src})
    raw = subprocess.check_output(['ffmpeg','-v','error','-i',out,'-vf','scale=32:58',
                                    '-pix_fmt','rgb24','-threads','1','-f','rawvideo','-'])
    frames = np.frombuffer(raw,np.uint8).reshape(-1,58,32,3)
    mean = frames.mean(axis=(1,2))
    assert len(frames) == 96
    assert np.all(mean[:,0] > 180), mean.tolist()
    assert np.all(mean[:,2] < 80), mean.tolist()
    assert not list(tmp_path.glob('cutaway_stream_*'))
