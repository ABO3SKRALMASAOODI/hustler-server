"""Pixel/audio checks of canvas, approval and final reuse against a full encode."""
import copy
import shutil
import subprocess
import numpy as np
import pytest
import renderer
import stitch
from schemas import validate_edl
from timeline import Timeline


def test_replaced_insert_does_not_reuse_same_id():
    a = dict(keep=[], canvas=dict(width=320,height=180), inserts=[dict(id='same',kind='video',asset_key='a',at_output_s=0,duration_s=5)])
    b=copy.deepcopy(a);b['inserts'][0]['asset_key']='b'
    tl=Timeline([],a['inserts'])
    assert not stitch.match_runs(stitch.timeline_atoms(a,tl),stitch.timeline_atoms(b,tl))


def test_caption_style_is_part_of_compiled_picture_evidence(tmp_path):
    path=tmp_path/'captions.ass'
    path.write_text('Style: Default,Arial,20\nDialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,hello\n')
    first=stitch.ass_events(path,with_payload=True)
    path.write_text(path.read_text().replace('Arial,20','Arial,40'))
    assert stitch.ass_events(path,with_payload=True) != first


def ff(*args):
    return subprocess.run(['ffmpeg','-v','error','-y',*args],check=True,capture_output=True).stdout


def frame(path,t):
    return np.frombuffer(ff('-ss',str(t),'-i',str(path),'-frames:v','1','-vf','format=gray','-f','rawvideo','-'),np.uint8).astype(float)


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'),reason='FFmpeg and FFprobe required')
@pytest.mark.parametrize('canvas,preview,quality,outro',[(True,True,'draft',False),(True,True,'approval',False),(True,False,'draft',False),(False,False,'draft',False),(True,False,'draft',True)])
def test_sequence_reuse_matches_fresh_pixels_and_audio(tmp_path,monkeypatch,canvas,preview,quality,outro):
    source=tmp_path/'source.mp4'
    ff('-f','lavfi','-i','testsrc2=s=320x180:r=30:d=24','-f','lavfi','-i','sine=frequency=731:sample_rate=48000:duration=24','-c:v','libx264','-preset','ultrafast','-g','60','-c:a','aac',str(source))
    # No external services: immutable assets resolve to this owned fixture.
    monkeypatch.setattr(renderer,'_render_asset_source',lambda *a,**k:str(source))
    if not outro:monkeypatch.setattr(renderer,'outro_seconds',lambda _preview:0.)
    token=renderer._PREVIEW_QUALITY.set(quality)
    try:
        old=dict(keep=[[0.,24.]],texts=[])
        if canvas:
            old.update(keep=[],canvas=dict(width=320,height=180,fps=30),inserts=[dict(id=f'c{i}',kind='video',asset_key='src',at_output_s=i*8,duration_s=8,source_start_s=i*8) for i in range(3)])
        old=validate_edl(old,24).model_dump()
        new=copy.deepcopy(old)
        if canvas:
            new['inserts']=[new['inserts'][1],new['inserts'][0],new['inserts'][2]]
            for i,item in enumerate(new['inserts']):item['at_output_s']=i*8
        else:
            new['keep']=[[0,7],[8,24]]
        new=validate_edl(new,24).model_dump()
        index={'video':{'duration':24},'sentences':[],'words':[]}
        prev,full,patched=[str(tmp_path/name) for name in ('prev.mp4','full.mp4','patched.mp4')]
        renderer.render_edl(old,index,str(source),prev,str(tmp_path),preview)
        renderer.render_edl(new,index,str(source),full,str(tmp_path),preview)
        monkeypatch.setattr(renderer,'_job_cached_source',lambda *_:prev)
        result=renderer._stitched_preview(0,{'version':2,'json':new},{'version':1,'json':old},{'storage_key':'previous'},index,str(source),str(tmp_path),{},patched,preview=preview)
        assert result is not None, 'Expected incremental path, not a fallback'
        assert abs(renderer.media.duration_of(full)-result)<.15
        for at in (1.,6.,9.,15.,20.,*([result-1] if outro else [])):
            a,b=frame(full,at),frame(patched,at)
            assert a.size==b.size and a.size
            mse=np.mean((a-b)**2)
            assert mse<65, f'Picture mismatch at {at}s: MSE={mse}'
        pcm=lambda p:np.frombuffer(ff('-i',p,'-map','0:a:0','-ac','1','-ar','8000','-f','f32le','-'),np.float32)
        a,b=pcm(full),pcm(patched)
        for second in range(1,int(result)-1):
            rms=lambda x:np.sqrt(np.mean(x[second*8000:(second+1)*8000]**2))
            assert abs(20*np.log10((rms(a)+1e-8)/(rms(b)+1e-8)))<1.5
    finally:
        renderer._PREVIEW_QUALITY.reset(token)
