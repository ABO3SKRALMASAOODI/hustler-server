"""Two holes in segment 41 previously both requested the first clause and received its clip."""
from pathlib import Path

import cinema as C
from timeline_repair import anchor


def test_two_missing_intervals_in_one_segment_keep_distinct_source_jobs(tmp_path):
    sc=dict(segments=[dict(say='Read the declaration. All the points.')])
    words=dict(duration=8,words=[dict(w='Read',start=0,end=1,seg=0),dict(w='points',start=4,end=8,seg=0)])
    clock=C.F.Clock(words,sc)
    gaps=[dict(seg=0,start=a,end=b,seconds=b-a,words=sc['segments'][0]['say'],heard=heard)
          for a,b,heard in [(0,4,'Read the declaration'),(4,clock.dur,'All the points')]]
    lines,targets=C.interval_requests(sc,gaps)
    assert len({r['i'] for r in lines})==2
    # The engine returns its matches by line ID, including when results arrive out of order.
    ft=dict(lines=[dict(i=0,clips=[])],voices=[],spend=[],credits=[])
    matches=[dict(i=r['i'],clips=[dict(file=f'{r["i"]}.mp4',seconds=5,time_range=[0,5],
                                       source=dict(url=r['say']))]) for r in reversed(lines)]
    C.attach_intervals(ft,dict(lines=matches),tmp_path,targets,clock)
    rows,_,end=C.schedule(sc,words,ft)
    assert [r['clip']['source']['url'] for r in rows]==[g['heard'] for g in gaps]
    assert [r['f'] for r in rows]==[[0,120],[120,end]]


def test_selected_corrupt_cut_is_reopened_at_original_range(tmp_path,monkeypatch):
    import source_media as media
    from types import SimpleNamespace
    from footage import cut
    clip=dict(file='old.mp4',path=str(tmp_path/'old.mp4'),w=1920,h=1080,
              source=dict(url='https://archive.example/original'),time_range=[21.917,26.05])
    Path(clip['path']).write_bytes(b'damaged')
    requests=[]
    def reopen(c,root,out):
        requests.append((c['source']['url'],c['time_range']))
        out.write_bytes(b'complete')
    monkeypatch.setattr(media,'decoded',lambda path,ffmpeg:path.read_bytes()==b'complete')
    monkeypatch.setattr(media,'original_cut',reopen)
    monkeypatch.setattr(cut,'finish',lambda src,out,*a,**k:Path(out).write_bytes(Path(src).read_bytes()))
    rows=[dict(clip=clip)]
    for _ in range(2):assert not media.restore(SimpleNamespace(supply=str(tmp_path)),rows)
    assert requests==[('https://archive.example/original',[21.917,26.05])]


def test_published_declaration_glyphs_survive_the_evidence_panel():
    from design import face_for,glyphs
    text='∀ (s : ℂ), riemannZeta s = 0 → (¬∃ n, s = -2 * (↑n + 1)) → s ≠ 1'
    assert {ord(c) for c in text if not c.isspace()} <= glyphs(face_for(text))


def test_long_marked_page_cuts_to_the_printed_detail(tmp_path):
    from PIL import Image,ImageDraw
    image=Image.new('RGB',(600,800),'white')
    ImageDraw.Draw(image).line((70,545,420,545),fill=(200,55,45),width=5)
    path=tmp_path/'source.png';image.save(path)
    crop=C.marked_detail(dict(path=path))
    assert 0<crop[1]<545/800<crop[3]<1
