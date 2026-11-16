import hashlib
import io
import json
from types import SimpleNamespace

from PIL import Image
import caption_context as captions
import picture_check as pictures


def test_flickr_preserves_published_caption_and_image_association():
    raw=b'''<meta property="og:title" content="Archive 123">
    <meta property="og:description" content="A ground rehearsal &amp; test.">
    <meta property="og:image" content="https://live.staticflickr.com/example.jpg">
    <meta name="description" content="Generic site description">'''
    url='https://www.flickr.com/photos/archive/123/'
    record=captions.published_record(url,raw)
    assert record['verbatim_archive_description']=='A ground rehearsal & test.'
    assert record['published_title']=='Archive 123'
    assert record['published_image']=='https://live.staticflickr.com/example.jpg'
    assert record['html_sha256']==hashlib.sha256(raw).hexdigest() and record['url']==url


def test_commons_caption_still_comes_from_description_row():
    raw=b'<table><tr><td id="fileinfotpl_desc">Description</td><td>A folded shield.</td></tr><tr><td>Other</td></tr></table>'
    record=captions.published_record('https://commons.wikimedia.org/wiki/File:shield.jpg',raw)
    assert record['verbatim_archive_description']=='Description A folded shield.'


def test_encoded_review_detail_uses_final_film_not_original_asset(tmp_path,monkeypatch):
    import cinema as C
    C.worker()
    from footage import ask
    calls=[];requests=[]
    def decode(cmd,**kwargs):
        calls.append(cmd)
        assert cmd[cmd.index('-i')+1]=='encoded.mp4'
        im=Image.new('RGB',(480,270) if '-vf' in cmd else (1920,1080),'red')
        if cmd[-1]=='-':
            b=io.BytesIO();im.save(b,format='JPEG');return SimpleNamespace(stdout=b.getvalue())
        im.save(cmd[-1]);return SimpleNamespace(stdout=b'')
    def check(prompt,content,*args,**kwargs):
        requests.append(content)
        return json.dumps(dict(shots=[dict(i=0,matches=True,real_moving_footage=False)])),0
    monkeypatch.setattr(pictures.subprocess,'run',decode)
    monkeypatch.setattr(ask,'ask',check)
    monkeypatch.setattr(ask,'image_block',lambda p:dict(image=p))
    row=dict(f=[30,60],src=[0,1],say='The shield.',clip=dict(kind='image',path='unencoded-original.jpg',source=dict(url='https://archive.test/123')))
    result=pictures.check('encoded.mp4',[row],tmp_path,'ffmpeg')
    assert result['ok'] and len(calls)==4
    with Image.open(tmp_path/'detail-0000.jpg') as im:assert im.size==(1920,1080)
    assert [c['image'] for c in requests[0] if 'image' in c]==[str(tmp_path/'shot-0000.jpg'),str(tmp_path/'detail-0000.jpg')]


def test_archive_caption_never_replaces_requested_title(tmp_path,monkeypatch):
    import evidence_web
    monkeypatch.setattr(evidence_web,'download',lambda url:b'<meta property="og:description" content="Ground test, before launch.">')
    clip=dict(source=dict(title='In-flight deployment',caption_page='https://www.flickr.com/photos/archive/123/'))
    captions.enrich(SimpleNamespace(supply=tmp_path),[clip])
    assert clip['source']['title']=='In-flight deployment'
    assert clip['source']['published_caption']['verbatim_archive_description']=='Ground test, before launch.'
