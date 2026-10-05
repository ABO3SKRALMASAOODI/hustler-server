"""Raster regressions for the reported spoken-word flash, not just ASS tags."""
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import captions


@pytest.mark.parametrize('layout',['flow','stack'])
def test_karaoke_fade_never_erases_already_visible_words(tmp_path,layout):
    style={'preset':'composed','animation':'fade','layout':layout,
           'color':'#FFFFFF','highlight_color':'#EBC497','anchor_y':.5,
           'size_scale':.7,'outline_width':0,'shadow':0}
    words=[{'w':w,'t0':i*.4,'t1':i*.4+.35}
           for i,w in enumerate(['Keep','every','word','visible'])]
    path=tmp_path/'words.ass'
    captions.write_ass(captions.events_premium(words,style,max_words=4,
        play_res=(360,640),design_version=2),str(path),style,play_res=(360,640))
    raw=subprocess.check_output(['ffmpeg','-v','error','-f','lavfi','-i',
        'color=black:s=360x640:r=50:d=1.6','-vf',
        f"ass='{path}':fontsdir='{captions.FONTS_DIR}'",'-f','rawvideo','-pix_fmt','rgb24','-'],timeout=30)
    frames=np.frombuffer(raw,np.uint8).reshape(-1,640,360,3)
    # Compare glyph coverage, not exact color: the requested tint should
    # change but neither the active word nor its following words may vanish.
    masks=frames.max(axis=3)>65
    # Ignore antialiased edge pixels whose intensity legitimately crosses a
    # threshold as ivory changes to sand. Solid glyph interiors cannot do so.
    reference=frames[15].max(axis=2)>180
    counts=[]
    for i in range(3,len(masks)-2):
        counts.append(float((masks[i]&reference).sum()/max(1,reference.sum())))
    assert min(counts)>.985, f'Visible glyphs blinked at a cue: {min(counts):.3f}'
    assert np.any(frames[5]!=frames[25]), 'The spoken-word tint must still change'


def test_reveal_fade_does_not_leak_alpha_to_previously_visible_words():
    words=[{'w':w,'t0':i*.4,'t1':i*.4+.3} for i,w in enumerate(['One','clear','idea'])]
    ev=captions.events_premium(words,{'preset':'composed','animation':'fade','layout':'flow'},design_version=2)
    # Color easing is deliberately different from an entrance fade.
    assert all(r'\alpha&HFF&' not in e['text'] for e in ev)
    assert any(r'\t(' in e['text'] for e in ev)
