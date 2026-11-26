import json
import os
import subprocess
from types import SimpleNamespace

import caption_pixels
import chain


def test_finish_repairs_missing_encoded_ink_and_caches_the_repaired_master(tmp_path):
    ff = os.environ.get("FFMPEG_BIN", "ffmpeg")
    path = str(tmp_path / "programme.mp4")
    subprocess.run([ff,"-v","error","-f","lavfi","-i","color=black:s=320x480:r=24:d=1",
                    "-f","lavfi","-i","sine=d=1","-c:a","aac","-c:v","libx264",path],check=True)
    scene = dict(l1=1, frame=[320,480], dur_f=24, fps=24, assets={}, spine=[], film={}, sound=[],
                 voices={"cap":dict(font="InterDisplay-Bold.ttf",px=30,track=0)},
                 items=[dict(k="shot",id="base",f=[0,24],pic="plate",plate={"c0":"#000000"}),
                        dict(k="word",role="cap",v="cap",id="word0",f=[2,17],on=2,
                             x=.15,y=.73,t="Blue governor",ink="#FFFFFF")])
    composed = tmp_path / "composed.json"
    composed.write_text(json.dumps(scene))
    final = str(tmp_path / "final.mp4")
    ctx = SimpleNamespace(supply=str(tmp_path),tmp=str(tmp_path))
    result,reused = chain.cached_finish(ctx,path,final,caption_scene=str(composed),accent=False)
    assert not reused
    assert result["finish"]["caption_pixels"] == dict(sampled=1,repaired=1,items=["word0"],unreadable=0)
    assert caption_pixels.repair(final,scene,ff)["repaired"] == 0
    assert chain.cached_finish(ctx,path,final,caption_scene=str(composed),accent=False) == (result,True)
