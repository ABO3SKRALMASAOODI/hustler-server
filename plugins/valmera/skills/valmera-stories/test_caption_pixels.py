import os
import subprocess

import caption_pixels


def test_missing_caption_is_drawn_and_the_encoded_glyphs_are_measured(tmp_path):
    ff = os.environ.get("FFMPEG_BIN", "ffmpeg")
    path = str(tmp_path / "programme.mp4")
    subprocess.run([ff,"-v","error","-f","lavfi","-i","color=black:s=320x480:r=24:d=1",
                    "-c:v","libx264",path],check=True)
    scene = dict(frame=[320,480], dur_f=24, fps=24, voices={
        "cap":dict(font="InterDisplay-Bold.ttf",px=30,track=0)}, items=[dict(
            k="word",role="cap",v="cap",id="word0",f=[2,22],on=2,x=.15,y=.73,t="Blue governor",ink="#FFFFFF")])
    first = caption_pixels.repair(path,scene,ff)
    assert first == dict(sampled=1,repaired=1,items=["word0"],unreadable=0)
    assert caption_pixels.repair(path,scene,ff) == dict(sampled=1,repaired=0,items=[],unreadable=0)


def test_spoken_address_uses_the_authored_display_spelling_without_changing_source_clock():
    import film
    words = "Try it at valmera dot I O.".split()
    script = {"segments":[{"show":[{"text":"VALMERA.IO","word_i":3}]}]}
    clock = film.Clock(dict(duration=7,words=[dict(w=w,seg=0,start=i,end=i+1)
                                              for i,w in enumerate(words)]),script)
    display = film.written_addresses(clock,script)
    assert [w["w"] for w in display.words] == ["Try","it","at","valmera.io"]
    assert display.words[-1]["t1"] == clock.words[-1]["t1"]
    assert len(clock.words) == 7
