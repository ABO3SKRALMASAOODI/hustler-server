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


def enrich(ctx,clips):
    import cinema as C
    from evidence_web import download
    root=Path(ctx.supply)/'archive-captions';root.mkdir(exist_ok=True)
    for c in clips:
        url=c['source'].get('caption_page')
        if not url or urlsplit(url).hostname!='commons.wikimedia.org':continue
        path=root/(hashlib.sha256(url.encode()).hexdigest()[:16]+'.json')
        if path.exists():record=C.read(path)
        else:
            try:
                parser=CommonsDescription();parser.feed(download(url).decode('utf-8'))
                record=dict(url=url,verbatim_archive_description=parser.description)
            except Exception as e:record=dict(url=url,error=str(e)[:400])
            C.save(path,record)
        if record.get('verbatim_archive_description'):c['source']['published_caption']=record
