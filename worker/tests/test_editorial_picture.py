"""A native inset must preserve geometry, speech clocks and crisp text layers."""
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent_tools
import captions
import renderer
import graphics
import stitch
from schemas import Frame, default_edl, edl_signature, validate_edl
from timeline import Timeline

PICTURE = [0, .25, 1, .75]


def test_picture_schema_and_legacy_signature():
    old = default_edl(3)
    old['frame'] = {'ratio': '9:16', 'mode': 'pad'}
    assert edl_signature(old) == edl_signature(validate_edl(old, 3).model_dump())
    for rectangle in ([0, .4, 1, .3], [0, 0, 1.1, 1], [0, 0, 1], [0, 0, float('nan'), 1]):
        with pytest.raises(ValueError):
            Frame(picture=rectangle)


@pytest.mark.parametrize('mode', ['crop', 'pad', 'pad_blur'])
def test_native_picture_pixels_match_inspection(tmp_path, mode):
    source = tmp_path / 'source.png'
    image = Image.new('RGB', (640, 360), '#d72735')
    # A blue centre lets the test detect an incorrectly placed source crop.
    image.paste(Image.new('RGB', (160, 360), '#204ded'), (240, 0))
    image.save(source)
    edl = default_edl(2)
    edl['frame'] = {'ratio': '9:16', 'mode': mode, 'picture': PICTURE}
    tl = Timeline(edl['keep'])
    graph = renderer.build_filtergraph(edl, 2, False, tl, None, [],
        {'words': [], 'sentences': [], 'silences': [], 'shots': []}, False,
        W=360, H=640, fps=25, frame_mode=mode, src_w=640, src_h=360, silence_idx=1)
    output = tmp_path / 'out.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-loop', '1', '-i', str(source),
        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo:d=2',
        '-filter_complex', graph, '-map', '[vout]', '-map', '[aout]',
        '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(output)],
        check=True, timeout=30)
    frame = tmp_path / 'render.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-ss', '1', '-i', str(output),
        '-frames:v', '1', str(frame)], check=True, timeout=15)
    rendered = Image.open(frame).convert('RGB')
    inspect_path, _ = agent_tools._fit_and_zoom_frame(str(tmp_path), 'proof',
        str(source), 1, (360, 640), mode, None, [], 2, True,
        output_size=(360,640), picture=PICTURE)
    inspected = Image.open(inspect_path).convert('RGB')
    for point in [(180, 60), (180, 320), (180, 590)]:
        assert max(abs(a-b) for a,b in zip(rendered.getpixel(point),inspected.getpixel(point))) < 15
    assert max(rendered.getpixel((180,60))) < 5
    assert rendered.getpixel((180,320))[2] > 190
    assert max(rendered.getpixel((180,590))) < 5


def test_inset_spatial_checks_share_renderer_geometry():
    ctx = SimpleNamespace(index={'video': {'width':1920,'height':1080}})
    edl = {'frame': {'ratio':'9:16','mode':'crop','picture':PICTURE}}
    assert agent_tools._caption_picture_bounds(ctx, edl, 0) == (.25,.75)
    assert agent_tools._source_point_to_output(ctx, edl, 0, (.5,.5)) == (.5,.5)
    assert agent_tools._source_point_to_output(ctx, edl, 0, (0,.5)) is None
    assert agent_tools._source_box_to_output(ctx, edl, 0, [0,0,1,1]) == [0,.25,1,.75]


def test_persistent_headline_keeps_all_dialogue_captions():
    class Ctx:
        duration = 3
        def __init__(self):
            self.edl=default_edl(3)
            self.edl['captions']={'mode':'from_transcript'}
        def latest_edl(self): return {'json': self.edl}
        def write_edl(self, edl, description):
            self.edl=validate_edl(edl,3).model_dump()
            return 'EDL v2'
    ctx=Ctx()
    agent_tools.add_text(ctx, 'A topic headline', 0, 3, y=.2, mute_captions=False)
    assert captions.effective_caption_mutes(ctx.edl) == []
    agent_tools.add_text(ctx, 'Speech emphasis', 1, 2)
    assert captions.effective_caption_mutes(ctx.edl) == [[1,2]]


@pytest.mark.parametrize('fit', ['cover', 'picture'])
def test_cutaway_preserves_speech_and_reaches_its_last_frame(tmp_path, fit):
    edl = default_edl(2)
    edl['frame'] = {'ratio':'9:16','mode':'crop','picture':PICTURE}
    overlay = {'id':'broll','asset_key':'clip','kind':'video','start':.4,
               'duration_s':1.2,'fit':fit}
    edl['overlays']=[overlay]
    graph = renderer.build_filtergraph(edl,2,False,Timeline(edl['keep']),None,[],
        {'words':[],'sentences':[],'silences':[],'shots':[]},False,
        W=180,H=320,fps=25,src_w=320,src_h=180,
        silence_idx=1,overlay_inputs=[(2,overlay)])
    # The output audio is the main program; overlay audio never replaces it.
    output=tmp_path/'cutaway.mp4'
    subprocess.run(['ffmpeg','-v','error',
        '-f','lavfi','-i','color=c=blue:s=320x180:d=2:r=25',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=2',
        '-f','lavfi','-i','color=c=red:s=300x200:d=1.2:r=29.97',
        '-filter_complex',graph,'-map','[vout]','-map','[aout]',
        '-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(output)],
        check=True,timeout=30)
    import io
    for time in (.45,1.56):
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(time),'-i',str(output),
            '-frames:v','1','-f','image2pipe','-c:v','png','-threads','1','-'],timeout=15)
        frame=Image.open(io.BytesIO(raw)).convert('RGB')
        assert frame.getpixel((90,160))[0]>220
        top=frame.getpixel((90,30))
        assert (top[0]>220) if fit=='cover' else (max(top)<10)
    pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-ss','0.5',
        '-t','0.8','-vn','-ac','1','-ar','48000','-f','s16le','-'],timeout=15)
    import numpy as np
    values=np.frombuffer(pcm,dtype='<i2').astype(float)
    hz=np.argmax(abs(np.fft.rfft(values)))*48000/len(values)
    assert abs(hz-880)<3


@pytest.mark.parametrize('layout', ['flow','stack'])
@pytest.mark.parametrize('animation', ['none','rise'])
def test_reveal_spoken_word_color_survives_size_and_motion(layout, animation):
    words=[{'w':'Make','t0':0,'t1':.4}, {'w':'progress','t0':.5,'t1':.9}]
    style={'preset':'podcast','dynamic':True,'highlight_color':'#BDF76A',
           'emphasis':'big','animation':animation,'layout':layout}
    events=captions.events_premium(words,style,emphasis_words=['progress'],design_version=2)
    first=next(x for x in events if x['start']==0 and x['layer']==5)
    later=next(x for x in events if x['start']==.5 and x['layer']==5)
    assert r'\1c&H6AF7BD&' in first['text']
    assert r'\1c&H6AF7BD&' in later['text']
    # Once the next word starts, the ordinary first word returns to white.
    assert r'\1c&H6AF7BD&' not in later['text'].split('Make')[0]
    historical=captions.events_premium(words,{**style,'dynamic':False},
        emphasis_words=['progress'],design_version=2)
    assert all(r'\1c&H6AF7BD&' not in x['text'] for x in historical)


def test_flat_graphic_edges_and_historical_defaults(tmp_path):
    edl=default_edl(3)
    item={'id':'flat','text':'A clear idea','template':'title','start':0.0,'end':3.0,
          'outline_width':0,'shadow':0,'uppercase':False}
    edl['texts']=[item]
    validated=validate_edl(edl,3).model_dump()
    path=tmp_path/'flat.ass'
    graphics.build_gfx_ass(validated,3,str(path),play_res=(1080,1920))
    assert r'\bord0.0\shad0.0' in path.read_text()
    for field in ['outline_width','shadow']:
        bad={**edl,'texts':[{**item,field:float('nan')}]}
        with pytest.raises(ValueError):validate_edl(bad,3)
    old={**edl,'texts':[{k:v for k,v in item.items() if k not in ('outline_width','shadow')}]}
    assert edl_signature(old)==edl_signature(validate_edl(old,3).model_dump())


def test_keyframe_lookup_accepts_both_ffprobe_timestamp_fields(monkeypatch):
    import json
    report={'frames':[{'best_effort_timestamp_time':'0.0','side_data_list':[{}]},
        {'pts_time':'1.0','best_effort_timestamp_time':'1.0'},
        {'best_effort_timestamp_time':'2.0'}, {'pts_time':'N/A'}, {'pts_time':'nan'}]}
    monkeypatch.setattr(stitch.subprocess,'run',lambda *a,**k:
        SimpleNamespace(returncode=0,stdout=json.dumps(report)))
    assert stitch.keyframe_times('render.mp4')==[0.,1.,2.]
