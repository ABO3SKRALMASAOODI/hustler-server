"""Preserve the archive's published description, separate from requested labels."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import hashlib


class CommonsDescription(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.parts=[];self.selected=False;self.description=''
    def handle_starttag(self,tag,attrs):
        if tag=='tr':
            if self.depth==0:self.parts=[];self.selected=False
            self.depth+=1
        if dict(attrs).get('id')=='fileinfotpl_desc':self.selected=True
    def handle_data(self,data):
        if self.depth:self.parts.append(data)
    def handle_endtag(self,tag):
        if tag=='tr' and self.depth:
            self.depth-=1
            if not self.depth and self.selected:self.description=' '.join(' '.join(self.parts).split())


class PhotoMetadata(HTMLParser):
    """Read the publisher's structured metadata, never a generated search label."""
    def __init__(self):
        super().__init__();self.values={}
    def handle_starttag(self,tag,attrs):
        if tag!='meta':return
        attrs=dict(attrs);key=attrs.get('property',attrs.get('name',''))
        if key in ('og:description','og:title','og:image','description'):
            self.values[key]=attrs.get('content','')


def published_record(url,raw):
    host=urlsplit(url).hostname
    text=raw.decode('utf-8')
    record=dict(url=url,html_sha256=hashlib.sha256(raw).hexdigest())
    if host=='commons.wikimedia.org':
        parser=CommonsDescription();parser.feed(text)
        record['verbatim_archive_description']=parser.description
    elif host in ('flickr.com','www.flickr.com'):
        parser=PhotoMetadata();parser.feed(text)
        record.update(verbatim_archive_description=parser.values.get('og:description',parser.values.get('description','')),
                      published_title=parser.values.get('og:title',''),published_image=parser.values.get('og:image',''))
    return record


def enrich(ctx,clips):
    import cinema as C
    from evidence_web import download
    root=Path(ctx.supply)/'archive-captions';root.mkdir(exist_ok=True)
    for c in clips:
        url=c['source'].get('caption_page')
        if not url or urlsplit(url).hostname not in ('commons.wikimedia.org','flickr.com','www.flickr.com'):continue
        path=root/(hashlib.sha256(url.encode()).hexdigest()[:16]+'.json')
        if path.exists():record=C.read(path)
        else:
            try:
                record=published_record(url,download(url))
            except Exception as e:record=dict(url=url,error=str(e)[:400])
            C.save(path,record)
        if record.get('verbatim_archive_description'):c['source']['published_caption']=record
