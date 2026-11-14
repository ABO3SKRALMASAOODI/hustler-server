"""Primary archive photographs through the worker's existing NASA provider.

Commons is an index, not the whole archive. A missing Commons result must not
be mistaken for a missing historical picture. Every candidate still faces the
same named-subject vision check, and the final film's word/picture check.
"""
from pathlib import Path
import hashlib
import json
import pictures as PX
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit


class ArticleImages(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.stack=[]
    def handle_starttag(self,tag,attrs):
        row=dict(attrs)
        if tag=='img' and any(content for _,content in self.stack) and row.get('src') and row.get('alt'):
            self.rows.append(row)
        if tag not in ('img','br','hr','input','meta','link','source','wbr','area','base','embed','param','track'):
            self.stack.append((tag,'entry-content' in row.get('class','').split()))
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:self.stack=self.stack[:i];break
    def handle_data(self,text):
        if self.rows and any(tag=='figcaption' for tag,_ in self.stack):
            self.rows[-1]['caption']=self.rows[-1].get('caption','')+' '+text.strip()


def source_images(urls,out):
    """NASA's cited history articles contain scans absent from its search API."""
    import requests
    B=PX._worker();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    cache=out/'article-caption-candidates.json'
    if cache.exists():return json.loads(cache.read_text())
    rows={}
    for url in urls:
        parsed=urlsplit(url)
        if parsed.hostname!='www.nasa.gov':continue
        try:
            response=requests.get(url,timeout=30);response.raise_for_status()
            parser=ArticleImages();parser.feed(response.text)
            for image in parser.rows:
                p=urlsplit(image['src'])
                if p.hostname not in ('www.nasa.gov','images-assets.nasa.gov'):continue
                full=urlunsplit((p.scheme,p.netloc,p.path,'',''))
                caption=(image.get('caption') or image['alt']).strip()
                rows[full]=dict(provider='nasa',kind=B.KIND_PHOTO,id='nasa-article:'+hashlib.sha256(full.encode()).hexdigest()[:20],
                    title=caption,description=caption,credit='NASA',license=B.NASA_LICENCE[0],license_url=B.NASA_LICENCE[1],
                    page_url=url,_thumb=image['src'],_url=full,width=None,height=None)
        except Exception:continue
    cache.write_text(json.dumps(list(rows.values()),indent=1))
    return list(rows.values())


def find(subjects, out, sources=()):
    B=PX._worker();out=Path(out);out.mkdir(parents=True,exist_ok=True)
    results={};reports=[]
    article=source_images(sources,out) if sources else []
    assignments={}
    if article:
        from wf4 import story
        signature=hashlib.sha256(json.dumps(dict(subjects=subjects,candidates=article),sort_keys=True).encode()).hexdigest()[:16]
        selection=out/('selection-'+signature+'.json')
        if selection.exists():assignments=json.loads(selection.read_text())
        else:
            text,usage=story.writer.ask('Select candidate archive photographs by their source captions for subsequent visual verification. '
                'For each subject return up to 12 image indexes whose captions actually describe its required subject and era. '
                'No generic substitutes. Keep ground rehearsals distinct from flight. Return JSON {subjects:[{name,images:[index]}]}. '
                'This is only a shortlist: the vision check will independently inspect every selected image.',
                json.dumps(dict(subjects=subjects,images=[dict(i=i,caption=c['description'][:800],page=c['page_url']) for i,c in enumerate(article)])),
                effort='medium',max_tokens=3500,what='WF4 primary archive shortlist')
            rows=story.writer.json_block(text)['subjects']
            assignments={r['name']:r['images'] for r in rows};selection.write_text(json.dumps(assignments,indent=1))
    for subject in subjects:
        selected=[article[i] for i in assignments.get(subject['name'],[]) if isinstance(i,int) and 0<=i<len(article)]
        key=hashlib.sha256(json.dumps(dict(subject=subject,article=selected),sort_keys=True).encode()).hexdigest()[:16]
        cache=out/(key+'.json')
        if cache.exists():
            row=json.loads(cache.read_text())
            results[subject['name']]=row['pictures'];reports.append(row['report']);continue
        candidates={c['id']:c for c in selected};errors=[]
        for query in subject.get('searches',[subject['name']])[:3]:
            try:
                for c in B._nasa(query,B.KIND_PHOTO,None,6):candidates[c['id']]=c
            except Exception as e:errors.append(str(e)[:200])
        records=[];checks=[];usd=0.
        values=list(candidates.values())
        for start in range(0,len(values),B.NAMED_CHECK_MAX):
            kept,checked,cost=B.check_named(subject,values[start:start+B.NAMED_CHECK_MAX],str(out))
            checks.extend(checked);usd+=cost
            for c in kept:
                try:
                    B.nasa_renditions(c)
                    got=B.fetch_local(c,str(out/'pics'),1920,1080)
                    if max(got['width'],got['height'])<1200:continue
                    records.append(PX._record(c,got,subject['name']))
                except Exception as e:errors.append(c['id']+': '+str(e)[:200])
        report=dict(subject=subject['name'],checked=checks,errors=errors,usd=usd,candidates=len(values),downloaded=len(records))
        row=dict(pictures=records,report=report)
        cache.write_text(json.dumps(row,indent=1))
        results[subject['name']]=records;reports.append(report)
    return results,reports
