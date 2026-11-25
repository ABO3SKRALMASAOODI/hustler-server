"""The normal chapter supplier must serve a located passage before asking for video."""
from pathlib import Path
from types import SimpleNamespace

import cinema as C
import idea_exhibits as E
import pytest


@pytest.mark.parametrize('resume', [False, True])
def test_document_and_motion_chapter(tmp_path, monkeypatch, resume):
    url = 'https://example.edu/check.log'
    quote = 'Proof checker accepted all five statements.'
    exhibit = dict(url=url,quote=quote,kind='text',page=None,subject='Verifier',title='Verifier log',
                   author='Archive',date='2026-11-20',parent=None)
    doc = dict(chapter='c1',say=quote,pictures=['Verifier'],
               design=dict(kind='document',fact='f1',mark='all five statements'))
    witness = dict(chapter='c1',say='Here is the reviewer speaking.',pictures=['Witness'])
    video = dict(url='https://video.example/witness',title='Original testimony')
    ch = dict(id='c1',title='Review')
    script = dict(script=dict(title='The verification',chapters=[ch],segments=[doc,witness]),
                  dossier=dict(named=[],facts=[dict(id='f1',quote=quote,url=url)]),
                  idea_full=dict(supply=dict(subjects=[dict(sources=[video])],exhibits=[exhibit])))
    root = tmp_path/'idea-originals'
    root.mkdir()
    (root/E.key(url)).write_text(quote)
    ctx = SimpleNamespace(run_dir=tmp_path,supply=tmp_path/'supply',log=lambda s:None)
    dest = ctx.supply/'footage/chapter-1'
    dest.mkdir(parents=True)
    cut = dict(file='clips/witness.mp4',source=video,time_range=[12,18],seconds=6)
    record = dict(lines=[dict(i=1,clips=[cut])],counts=dict(frames_checked=216,sources_opened=5,
                  frames_passed=18),credits=[dict(line=1,**video)],voices=[],spend=dict(usd=.01))
    if resume:
        record['lines'].insert(0,dict(i=0,clips=[]))
        C.save(dest/'footage.json', record)
    def fetch(payload, folder, log):
        assert not resume
        assert [l['i'] for l in payload['lines']] == [1]
        assert payload['known_sources'] == [video]
        return record
    monkeypatch.setattr(C.footage_box,'fetch',fetch)
    monkeypatch.setattr(C.wf4.story.writer,'ask',lambda *a,**kw: ('{"lines":[]}',{}))
    result = C.footage(ctx,script)
    first,second = result['lines']
    clip, = first['clips']
    assert clip['kind']=='image' and clip['document'] and clip['mark']==doc['design']['mark']
    assert clip['source']['primary_context'][0]['quote']==quote and Path(clip['path']).is_file()
    assert second['clips']==[dict(cut,path=str(dest/cut['file']),w=C.W,h=C.H)]
    assert result['credits'][0]==dict(line=1,**video)
    if resume:
        assert C.read(dest/'footage.json')==record
    assert C.read(dest/'document-supply.json')['motion_lines']==[1]
    assert C.footage(ctx,script)==result


def test_pdf_focus_is_inside_cited_passage(tmp_path):
    # Two occurrences on one page. Only the second belongs to the cited sentence.
    import pypdfium2 as pdfium
    import cited_page
    content = b'BT /F1 16 Tf 40 220 Td (Anchor in a heading) Tj 0 -150 Td (Here is the Anchor in its context.) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 360 270] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n'%len(content)+content+b'\nendstream']
    data = bytearray(b'%PDF-1.4\n')
    offsets = []
    for n,obj in enumerate(objects,1):
        offsets.append(len(data))
        data.extend(f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n')
    xref = len(data)
    data.extend(b'xref\n0 6\n0000000000 65535 f \n')
    for offset in offsets:
        data.extend(f'{offset:010} 00000 n \n'.encode())
    data.extend(f'trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    with pdfium.PdfDocument(bytes(data)) as pdf:
        page=pdf[0]
        text=page.get_textpage()
        image=cited_page.marked_crop(page,text,'Here is the Anchor in its context.','Anchor')
        assert image.height < 350
        assert (170,67,42) in {color for _,color in image.getcolors(image.width*image.height)}
        with pytest.raises(ValueError):
            cited_page.marked_crop(page,text,'Here is the Anchor in its context.','heading')
