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
    assert [w["w"] for w in display.words] == ["Try","it","at","valmera.io."]
    assert display.words[-1]["t1"] == clock.words[-1]["t1"]
    assert len(clock.words) == 7


def test_a_written_address_keeps_the_writers_phrase_lines():
    """26 Nov: merging the address first broke the phrase spans' word count, phrases_of gave None and every caption
    of the reel was re-chunked by the clock. The authored lines stay, the address on one word inside its phrase."""
    import film
    said = "Try it today. Go to valmera dot I O. It edits for you".split()
    script = {"segments":[{"phrases":[[0,3],[3,9],[9,13]],
                           "show":[{"text":"VALMERA.IO","word_i":5}]}]}
    clock = film.Clock(dict(duration=14,words=[dict(w=w,seg=0,start=i,end=i+.9) for i,w in enumerate(said)]),script)
    assert clock.phrases_of(script)
    display, lines = film.written_captions(clock, script)
    assert [text for _,_,text,_ in lines] == ["Try it today.", "Go to valmera.io.", "It edits for you"]
    assert [w["w"] for w in lines[1][3]] == ["Go","to","valmera.io."]
    assert lines[1][3][-1] is display.words[5] and lines[1][1] >= display.words[5]["t1"]
    assert lines[2][0] == display.words[6]["t0"]
    plain = {"segments":[{"phrases":[[0,3],[3,9],[9,13]]}]}
    assert film.written_captions(clock, plain) == (clock, clock.phrases_of(plain))
