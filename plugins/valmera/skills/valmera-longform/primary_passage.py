"""Readable original source passages for genuinely unfilmed story beats.

These are original document pixels, never generated historical pictures or
retyped quotations. They remain stills in the moving-footage measurement.
"""
from pathlib import Path
from contextlib import closing
import hashlib,json
from urllib.parse import urlsplit


def locate(pages,locators):
    """Literal source lookup, not a judgement about narration or visual fit."""
    needles=[' '.join(s.casefold().split()) for s in locators if len(s.split())>=8]
    return [i for i,text in enumerate(pages) if any(n in ' '.join(text.casefold().split()) for n in needles)]


def retained_passages(ft):
    """Retain source attribution with excerpts already read from original PDFs."""
    clips=[c for row in ft.get('lines',[]) for c in row.get('clips',[])]
    editorial=ft.get('editorial',{})
    clips+=editorial.get('bank',[])+[p['clip'] for p in editorial.get('pins',[])]
    docs={}
    for clip in clips:
        source=clip.get('source',{});url=source.get('url','').partition('#')[0]
        if not urlsplit(url).path.lower().endswith('.pdf'):continue
        fields=('title','credit','licence','licence_url')
        if not all(source.get(k) for k in fields):continue
        for passage in source.get('primary_context',[]):
            # A nearby citation to a different report cannot inherit this
            # report's title or licence merely because it is on the same clip.
            if passage.get('source_url','').partition('#')[0]!=url:continue
            quote=passage.get('quote','')
            if len(quote.split())<8:continue
            doc=docs.setdefault(url,dict(url=url,**{k:source[k] for k in fields},passages=[]))
            if quote not in doc['passages']:doc['passages'].append(quote)
    return list(docs.values())


def select_retained(candidates,selections,wanted):
    """Model selects meaning; literal lookup verifies its locators and source."""
    docs=[]
    for row in selections:
        index=row.get('document')
        if type(index) is not int or not 0<=index<len(candidates):raise ValueError('unknown retained primary source')
        source=candidates[index]
        locators=[s for s in row.get('locators',[]) if locate(source['passages'],[s])]
        lines=[i for i in row.get('lines',[]) if i in wanted]
        if locators and lines:
            docs.append(dict({k:v for k,v in source.items() if k!='passages'},locators=locators,lines=lines))
    return docs[:2]


def local_plan(ctx,ft,gaps,root):
    import cinema as C
    from wf4 import story
    from document_evidence import spoken_intervals
    candidates=retained_passages(ft)
    if not candidates:return None
    payload=dict(words=spoken_intervals(gaps),documents=candidates)
    key=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:16]
    path=root/('retained-'+key+'.json')
    if path.exists():return C.read(path)
    text,usage=story.writer.ask(
        'Select original primary-source passages already read from these licensed reports for the exact heard words. '
        'Return JSON {documents:[{document:zero-based index,lines:[segment indexes],locators:[exact 8-20 word substrings]}]}. '
        'At most two documents. Require direct evidence of the stated action, not generic background. '
        'The original PDF page will be independently inspected and cropped as attributed documentary evidence; '
        'do not invent a quotation or claim that it is flight photography. Return an empty list if none fits.',
        json.dumps(payload),effort='high',max_tokens=1800,what='WF4 retained primary testimony')
    selected=story.writer.json_block(text)
    data=dict(documents=select_retained(candidates,selected.get('documents',[]),payload['words']),usage=usage)
    C.save(path,data);return data


def existing_pdf(supply,url):
    """Reuse original reports; the literal locator is checked in the PDF again."""
    supply=Path(supply);key=hashlib.sha256(url.encode()).hexdigest()[:16]
    paths=[supply/'primary-passages'/(key+'.pdf'),supply/'evidence-documents'/(key+'.pdf')]
    # Early runs used the archive filename. Require its retained discovery
    # record before considering that legacy path.
    known=[d.get('url') for p in (supply/'evidence-documents').glob('discovery*.json')
           for d in json.loads(p.read_text()).get('documents',[])]
    if url in known:paths.append(supply/'evidence-documents'/Path(urlsplit(url).path).name)
    return next((p for p in paths if p.is_file()),None)


def supply(ctx,script,ft,gaps):
    import cinema as C
    import pypdfium2 as pdfium
    from PIL import Image
    from wf4 import story
    from footage import ask
    from evidence_web import download
    from document_evidence import discovery_key,spoken_intervals
    root=Path(ctx.supply)/'primary-passages';root.mkdir(exist_ok=True)
    signature=discovery_key(gaps);plan=root/(signature+'.json')
    data=local_plan(ctx,ft,gaps,root)
    if not data or not data.get('documents'):
        data=C.read(plan) if plan.exists() else None
    if data is None:
        text,usage=story.writer.ask_web(
            'Find original first-person testimony or official mission/event reports documenting these exact unfilmed actions. '
            'Footage, photographs and technical-figure research have failed. Find at most two openly licensed or public-domain '
            'downloadable PDFs, ideally different sources or pages that support the different spoken intervals. '
            'A relevant witness transcript is useful. No generic background, title pages, tables, modern invented pictures or rewritten quotes. '
            'Return JSON {documents:[{url,title,credit,licence,licence_url,lines:[segment indexes],locators:[exact 8-20 word substrings from the relevant PDF passages]}]}. '
            'Verify the source and licence. The program will locate the literal substrings in the actual PDF, then visually verify the original page. '
            'Return an empty list if no precise source is found.',
            json.dumps(dict(title=script['title'],gaps=gaps,facts=script['dossier']['facts'])),
            searches=4,fetches=4,max_tool_calls=8,effort='high',max_tokens=2500,what='WF4 primary testimony passages')
        data=story.writer.json_block(text);data['usage']=usage;C.save(plan,data)
    wanted=spoken_intervals(gaps);by={r['i']:r for r in ft['lines']}
    for doc in data.get('documents',[])[:2]:
        key=hashlib.sha256(doc['url'].encode()).hexdigest()[:16]
        path=existing_pdf(ctx.supply,doc['url']) or root/(key+'.pdf')
        if not path.exists():
            content=download(doc['url'])
            if len(content)>50_000_000:raise ValueError('primary passage report exceeds 50 MB')
            path.write_bytes(content)
        with pdfium.PdfDocument(path) as pdf:
            texts=[]
            for pi in range(len(pdf)):
                with closing(pdf[pi]) as page,closing(page.get_textpage()) as tp:texts.append(tp.get_text_range())
            found=locate(texts,doc.get('locators',[]))
            C.save(root/(key+'-located.json'),dict(pages=found,locators=doc.get('locators',[])))
            for pi in found[:3]:
                base=root/(key+'-p'+str(pi));preview=base.with_suffix('.jpg')
                verdict=root/(base.name+'-'+signature+'-check.json')
                lines={i:wanted[i] for i in doc['lines'] if i in wanted}
                if not lines:continue
                with closing(pdf[pi]) as page:
                    if not preview.exists():page.render(scale=1.5).to_pil().convert('RGB').save(preview,quality=92)
                    if verdict.exists():check=C.read(verdict)
                    else:
                        text,usd=ask.ask(
                            'Inspect this original primary-source page. Select ONE short, readable continuous passage that directly documents '
                            'the supplied heard words. Surrounding narration is context, not a licence to substitute a different action. '
                            'This is visibly attributed documentary evidence, not a claim to be a camera recording of the event. '
                            'Reject generic background, unrelated text, wrong people/events/states, illegible scans, or passages too dense to read. '
                            'Crop the actual page; do not invent or retype anything. Keep complete sentences and their speaker attribution where present. '
                            'Return JSON {ok:bool,usable:bool,lines:[matching segment indexes],crop:[left,top,right,bottom],caption:string,label:string,why:string}. '
                            'Crop coordinates are fractions of the page. label is a concise truthful on-screen source attribution.',
                            [dict(type='text',text=json.dumps(dict(source=doc,words=lines,page_text=texts[pi]))),ask.image_block(str(preview))],
                            'wf4_primary_passage',max_tokens=1500)
                        check=ask.json_obj(text);check['usd']=usd;C.save(verdict,check)
                    if check.get('ok') is not True or check.get('usable') is not True:continue
                    crop=check.get('crop',[])
                    if len(crop)!=4 or not 0<=crop[0]<crop[2]<=1 or not 0<=crop[1]<crop[3]<=1:raise ValueError('invalid primary-passage crop')
                    w,h=page.get_size();image=root/(base.name+'-'+signature+'.png')
                    page.render(scale=3,crop=(crop[0]*w,(1-crop[3])*h,(1-crop[2])*w,crop[1]*h)).to_pil().save(image)
                with Image.open(image) as im:w,h=im.size
                source={k:doc[k] for k in ('url','title','credit','licence','licence_url')}
                source['url']+='#page='+str(pi+1)
                source.update(presentation='Original primary-source passage, visibly attributed; documentary testimony, not flight photography',
                              primary_context=[dict(page=pi,quote=texts[pi],source_url=source['url'])])
                rec=dict(path=str(image),kind='image',primary_passage=True,w=w,h=h,content_hash=C.digest(image),
                         seconds=7.8,time_range=[0,7.8],source=source,shows=check['caption'],editorial_label=check['label'])
                for i in check['lines']:
                    if i not in lines:continue
                    row=by.setdefault(i,dict(i=i,clips=[]))
                    if not any(c['path']==str(image) for c in row['clips']):row['clips'].append(rec)
                    credit=dict(line=i,**source)
                    if credit not in ft['credits']:ft['credits'].append(credit)
    ft['lines']=list(by.values())
