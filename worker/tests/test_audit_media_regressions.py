from types import SimpleNamespace
import shutil
import subprocess
from pathlib import Path
import pytest
import agent_tools
import media


@pytest.mark.parametrize('key', ['main','originals/7/source.mp4','proxy/7/source.mp4'])
def test_source_audio_reference_uses_indexed_source_instead_of_music_rejection(monkeypatch,key):
    class Db:
        def run(self, fn, project_id, asset_key):
            assert project_id==7
            return {'kind':'original' if asset_key.startswith('originals/') else 'proxy'}
    ctx=SimpleNamespace(project_id=7,has_main_video=True,db=Db(),index={'words':[]})
    monkeypatch.setattr(agent_tools,'_get_perception',lambda ctx:{})
    for name in ('_describe_tempo','_describe_beats','_describe_energy'):
        monkeypatch.setattr(agent_tools,name,lambda p:'measured evidence')
    assert 'Audio analysis of the SOURCE' in agent_tools.get_audio_analysis(ctx,key)


def test_main_alias_does_not_invent_audio_for_blank_canvas():
    assert not agent_tools._is_main_audio_reference(SimpleNamespace(has_main_video=False),'main')


def test_frame_jpeg_uses_full_range_and_one_output_thread(monkeypatch,tmp_path):
    def run(cmd,**kwargs):
        # Reproduce the strict MJPEG encoder's rejection of limited-range YUV.
        if '-pix_fmt' not in cmd or cmd[cmd.index('-pix_fmt')+1]!='yuvj420p':
            raise media.MediaError('Non full-range YUV is non-standard')
        assert cmd[cmd.index('-threads:v')+1]=='1'
        Path(cmd[-1]).write_bytes(b'frame')
    monkeypatch.setattr(media,'run',run)
    assert media.frame_at('source.mp4',1,str(tmp_path/'frame.jpg')).endswith('.jpg')


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='requires ffmpeg')
def test_limited_range_video_produces_readable_jpeg(tmp_path):
    from PIL import Image
    src=tmp_path/'source.mp4';dst=tmp_path/'frame.jpg'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=red:size=160x90:duration=1',
        '-c:v','libx264','-pix_fmt','yuv420p',str(src)],check=True)
    media.frame_at(str(src),.5,str(dst))
    with Image.open(dst) as image:
        assert image.size==(160,90)
        r,g,b=image.convert('RGB').getpixel((80,45))
        assert r>200 and g<30 and b<30


def test_unmastered_audio_still_has_a_codec_safe_peak_ceiling(tmp_path):
    import shutil
    import subprocess
    import numpy as np
    import pytest
    import renderer
    from schemas import default_edl
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    source = tmp_path / 'loud.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=red:s=160x90:r=25:d=2',
        '-f','lavfi','-i','sine=frequency=997:duration=2','-af','volume=12',
        '-c:v','libx264','-threads','1','-c:a','aac','-shortest',str(source)],check=True,capture_output=True)
    edl=default_edl(2)
    edl['master']=None
    target=tmp_path/'limited.mp4'
    renderer.render_edl(edl, {'video':{'duration':2,'width':160,'height':90,'fps':25},'words':[]}, str(source), str(target), str(tmp_path), preview=True)
    raw=subprocess.run(['ffmpeg','-v','error','-i',str(target),'-map','0:a:0','-f','f32le','-'],check=True,capture_output=True).stdout
    peak=float(np.abs(np.frombuffer(raw,dtype=np.float32)).max())
    assert .5 < peak < .86
