"""The p5716 many-cutaway export must stream, retaining shot clocks and looks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import overlay_stream
import renderer


def covers(count=24):
    return [dict(id=f'ov{i}', asset_key='source', kind='video', fit='cover',
                 start=i*.5, duration_s=.5, source_start_s=(i % 12)*.7)
            for i in range(count)]


def test_frame_ownership_gaps_overlaps_and_top_layers():
    items = covers(8)
    items[1]['start'] = .7
    items[2]['start'] = .9
    top = dict(kind='image', fit=None, start=1., duration_s=2.)
    prefix, spans = overlay_stream.plan(items+[top], 30)
    assert prefix == items
    assert (15, 21, None) in spans
    assert (21, 27, 1) in spans
    assert (27, 42, 2) in spans
    assert not overlay_stream.plan([top]+items, 30)
    items[3]['entrance'] = 'fade'
    assert not overlay_stream.plan(items, 30)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
def test_stream_preserves_visible_frames_and_cleans_staging(tmp_path, monkeypatch):
    src = str(tmp_path/'source.mp4')
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',
                    'testsrc2=s=160x288:r=30:d=8','-c:v','libx264',
                    '-threads','2','-preset','ultrafast','-qp','0',src],
                   check=True, capture_output=True, timeout=60)
    items = covers(8)
    items[2]['start'] = 1.1  # an actual gap must reveal the source
    items[4]['source_crop'] = [.1,.1,.9,.9]
    edl = dict(keep=[[0,4]], frame={'ratio':'9:16','mode':'crop'}, overlays=items,
               fx=[dict(id='look',type='grade_window',stage='screen',start=0,end=4,
                        ev=-.25,contrast=1.24,saturation=.9)])
    index = {'words':[], 'sentences':[], 'silences':[], 'video':{'duration':8}}
    real_plan = overlay_stream.plan
    for stream in [False, True]:
        monkeypatch.setattr(overlay_stream, 'plan', real_plan if stream else lambda *a: [])
        work = tmp_path/str(stream);work.mkdir()
        renderer.render_edl(edl,index,src,str(work/'out.mp4'),str(work),True,
                            asset_locals={'source':src},suppress_outro=True)
        assert not list(work.glob('cutaway_stream_*'))
    def frames(path):
        data = subprocess.check_output(['ffmpeg','-v','error','-i',str(path),
                    '-vf','scale=160:288','-pix_fmt','rgb24','-f','rawvideo','-'])
        return np.frombuffer(data,np.uint8).reshape(-1,288,160,3).astype(float)
    old,new = frames(tmp_path/'False/out.mp4'),frames(tmp_path/'True/out.mp4')
    assert old.shape == new.shape
    # Encoded twice but staged losslessly: reject a wrong crop, missing look,
    # source-clock shift, black gap, or extra/absent boundary picture.
    per_frame = abs(old-new).mean(axis=(1,2,3))
    assert per_frame.max() < 6, per_frame.tolist()


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
def test_delivery_peak_rss_is_bounded_for_many_cutaways(tmp_path):
    """24 RGBA-scaled readers exceeded 2 GiB even with a 360p source.

    Measure the REAL renderer, including every preparation ffmpeg child.
    48 shots must also stay under the same ceiling; dropping effects or
    merely lowering output resolution cannot make this test pass.
    """
    script = '''
import json,pathlib,resource,subprocess,sys,time
sys.path.insert(0,sys.argv[1])
import renderer
w=pathlib.Path(sys.argv[2]);n=int(sys.argv[3]);src=w/'source.mp4'
subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=s=360x640:r=30:d=14','-c:v','libx264','-threads','2','-preset','ultrafast',str(src)],check=True)
edl={'keep':[[0,12]],'frame':{'ratio':'9:16','mode':'crop'},'overlays':[{'id':f'ov{i}','asset_key':'source','kind':'video','fit':'cover','start':i*12/n,'duration_s':12/n,'source_start_s':(i%12)*.7} for i in range(n)],'fx':[{'id':'look','type':'grade_window','stage':'screen','start':0,'end':12,'ev':-.25,'contrast':1.24,'saturation':.9}]}
renderer.render_edl(edl,{'words':[],'sentences':[],'silences':[]},str(src),str(w/'out.mp4'),str(w),False,asset_locals={'source':str(src)},suppress_outro=True)
r=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
print('RSS_RESULT',json.dumps({'peak_bytes':r if sys.platform=='darwin' else r*1024,'info':renderer.media.probe(str(w/'out.mp4'))}))
'''
    for count in [24,48]:
        work=tmp_path/str(count);work.mkdir()
        p=subprocess.run([sys.executable,'-c',script,str(Path(renderer.__file__).parent),
                          str(work),str(count)],capture_output=True,text=True,timeout=240)
        assert p.returncode == 0, p.stdout+p.stderr
        assert 're-rendering without fx' not in p.stdout+p.stderr
        result=json.loads(p.stdout.split('RSS_RESULT ')[-1])
        assert result['info']['width']==1080 and result['info']['height']==1920
        assert abs(result['info']['duration']-12) < .1
        assert result['peak_bytes'] < 1024**3, result
