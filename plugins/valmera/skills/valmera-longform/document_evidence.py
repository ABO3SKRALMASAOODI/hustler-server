"""Research and verify original technical figures when an archive view is scarce.
No generated reconstruction: every displayed line comes from the cited PDF.
"""
from pathlib import Path
import hashlib,json
from contextlib import closing


def supply(ctx,script,ft,gaps):
    import requests,pypdfium2 as pdfium
    import cinema as C
    from wf4 import story
    from footage import ask
    root=Path(ctx.supply)/'evidence-documents';root.mkdir(exist_ok=True)
    discovery=root/'discovery.json'
    if discovery.exists():docs=C.read(discovery)['documents']
    else:
        text,usage=story.writer.ask_web('Research original public-domain or openly licensed technical reports with diagrams or archival photographs '
            'that can literally illustrate these documentary narration gaps. Find at most three downloadable PDF URLs from primary archives. '
            'No modern invented reconstructions or unrelated technical manuals. Verify the title and licence. Return JSON '
            '{documents:[{url,title,credit,licence,licence_url}]}.',json.dumps(dict(title=script['title'],gaps=gaps,facts=script['dossier']['facts'])),
            searches=6,effort='high',max_tokens=3000,what='WF4 original technical evidence')
        data=story.writer.json_block(text);data['usage']=usage;C.save(discovery,data);docs=data['documents']
    pages=[];files={}
    for di,doc in enumerate(docs):
        key=hashlib.sha256(doc['url'].encode()).hexdigest()[:16];path=root/(key+'.pdf')
        if not path.exists():
            response=requests.get(doc['url'],timeout=90);response.raise_for_status()
            if len(response.content)>50_000_000:raise ValueError('technical report exceeds 50 MB budget')
            path.write_bytes(response.content)
        files[di]=path
        with pdfium.PdfDocument(path) as pdf:
            for pi in range(min(len(pdf),300)):
                with closing(pdf[pi]) as page:
                    with closing(page.get_textpage()) as text:
                        pages.append(dict(document=di,page=pi,text=text.get_text_range()[:650]))
    signature=hashlib.sha256(json.dumps(dict(gaps=gaps,pages=pages),sort_keys=True).encode()).hexdigest()[:16]
    selection=root/('selection-'+signature+'.json')
    if selection.exists():selected=C.read(selection)
    else:
        text,usage=story.writer.ask('Select original figures or photographs that show the machinery and operations actually named in the gaps. '
            'Choose up to 18 PDF pages, by zero-based document and page indexes. No text-only filler, title pages or tables. '
            'Original explanatory diagrams are valid when they accurately explain the spoken mechanism; do not imply they are camera footage. '
            'Return JSON {pages:[{document,page,lines:[segment indexes],why}]}. Pages will be visually verified and cropped to one useful figure.',
            json.dumps(dict(gaps=gaps,documents=docs,pages=pages)),effort='high',max_tokens=4000,what='WF4 technical figure selection')
        selected=story.writer.json_block(text);selected['usage']=usage;C.save(selection,selected)
    by={r['i']:r for r in ft['lines']};valid_lines={g['seg']:g['words'] for g in gaps}
    for line in by.values():
        line['clips']=[c for c in line['clips'] if not (c.get('document') and Path(c['path']).parent==root)]
    for row in selected['pages']:
        di,pi=int(row['document']),int(row['page']);doc=docs[di]
        base=root/f'd{di}-p{pi}';preview=base.with_suffix('.jpg');verdict=root/(base.name+'-v2.json')
        with pdfium.PdfDocument(files[di]) as pdf:
            with closing(pdf[pi]) as page:
                if not preview.exists():page.render(scale=1.5).to_pil().convert('RGB').save(preview,quality=90)
                if verdict.exists():check=C.read(verdict)
                else:
                    wanted={i:valid_lines[i] for i in row['lines'] if i in valid_lines}
                    text,cost=ask.ask('Inspect this original technical-report page. Select ONE genuine diagram or photograph that accurately illustrates '
                        'one or more supplied narration lines. Return JSON {ok:bool,usable:bool,lines:[indexes],crop:[left,top,right,bottom],rotation_cw:0|90|180|270,caption:string,why:string}. '
                        'Crop coordinates are fractions of this entire page. Keep all parts necessary to understand the figure and its labels. '
                        'Specify rotation to make the cropped labels upright. usable requires a clear, legible reproduction; reject blown-out or unreadable photocopies. '
                        'Reject wrong repair states, dates, people or unrelated machinery, and text-only filler. A ground rehearsal must never pose as flight. '
                        'Use only the supplied line indexes. The crop may be the full page if appropriate.',
                        [dict(type='text',text=json.dumps(dict(source=doc,words=wanted,selection=row['why']))),ask.image_block(str(preview))],
                        'wf4_original_document',max_tokens=1500)
                    check=ask.json_obj(text);check['usd']=cost;C.save(verdict,check)
                if check.get('ok') is not True or check.get('usable') is not True:continue
                crop=check.get('crop',[])
                if len(crop)!=4 or not 0<=crop[0]<crop[2]<=1 or not 0<=crop[1]<crop[3]<=1:raise ValueError('invalid original-document crop')
                w,h=page.get_size();image=base.with_suffix('.png')
                rotation=check.get('rotation_cw',0)
                if rotation not in (0,90,180,270):raise ValueError('invalid original-document orientation')
                page.render(scale=3,crop=(crop[0]*w,(1-crop[3])*h,(1-crop[2])*w,crop[1]*h)).to_pil().rotate(-rotation,expand=True).save(image)
        from PIL import Image
        with Image.open(image) as im:w,h=im.size
        source=dict(url=doc['url']+'#page='+str(pi+1),title=doc['title'],credit=doc['credit'],licence=doc['licence'],licence_url=doc['licence_url'])
        rec=dict(path=str(image),kind='image',document=True,w=w,h=h,content_hash=C.digest(image),seconds=7.8,time_range=[0,7.8],source=source,shows=check['caption'])
        for i in check['lines']:
            if i not in valid_lines:continue
            by.setdefault(i,dict(i=i,clips=[]))['clips'].append(rec)
            ft['credits'].append(dict(line=i,**source))
    ft['lines']=list(by.values())
