"""Describe the actual archive before committing the final spoken script.

The catalogue is editorial input, not a substitute for the final word/picture
check. A retrieved filename or a requested subject never establishes a picture.
"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,io,json,subprocess


def catalogue(ctx,ft):
    import cinema as C
    from footage import ask
    from PIL import Image,ImageOps,ImageDraw
    C.worker();C.encoded_durations(ft);C.unify_image_copies(ft)
    root=Path(ctx.supply)/'archive-catalogue';root.mkdir(exist_ok=True)
    unique={}
    for line in ft['lines']+[dict(clips=ft.get('editorial',{}).get('bank',[]))]:
        for clip in line['clips']:
            key=('image:'+clip['content_hash']) if clip.get('kind')=='image' else clip['source']['url']+str(clip['time_range'])
            if key not in unique:unique[key]=clip
    items=[dict(id=hashlib.sha256(k.encode()).hexdigest()[:16],clip=c) for k,c in unique.items()]
    from document_context import enrich
    enrich(ctx,[r['clip'] for r in items])
    from caption_context import enrich as captions
    captions(ctx,[r['clip'] for r in items])
    def one(item):
        c=item['clip'];key=item['id'];out=root/(key+'.json');board=root/(key+'.jpg')
        version=3 if c.get('kind')=='image' else 2
        if out.exists():
            prior=C.read(out)
            if prior.get('version')==version and prior['clip']['source']==c['source']:return prior
        if not board.exists():
            cells=[]
            if c.get('kind')=='image':
                with Image.open(c['path']) as im:cells=[ImageOps.contain(im.convert('RGB'),(480,270))]*3
            else:
                for fraction in (.1,.5,.9):
                    at=max(0,min(c['seconds']-1/30,c['seconds']*fraction))
                    result=subprocess.run([C.PX.FF,'-v','error','-ss',str(at),'-i',c['path'],'-frames:v','1',
                        '-vf','scale=480:270','-f','image2pipe','-vcodec','mjpeg','-'],capture_output=True,check=True)
                    cells.append(Image.open(io.BytesIO(result.stdout)).convert('RGB'))
            canvas=Image.new('RGB',(1440,300),'#14161A')
            for i,im in enumerate(cells):canvas.paste(im,(i*480+(480-im.width)//2,30+(270-im.height)//2))
            ImageDraw.Draw(canvas).text((10,8),'Archive '+key,fill='white');canvas.save(board,quality=88)
        disputes=[dict(words=r['words'],observation=r['why']) for r in ft.get('editorial',{}).get('rejected',[]) if r['clip']==c['path']+':'+str(c['time_range'])]
        content=[dict(type='text',text=json.dumps(dict(source=c['source'],claimed_view=c.get('shows'),seconds=c['seconds'],prior_observations=disputes))),ask.image_block(str(board))]
        if c.get('kind')=='image':
            detail=root/(key+'-detail.jpg')
            with Image.open(c['path']) as im:ImageOps.contain(im.convert('RGB'),(1600,1200)).save(detail,quality=92)
            content.append(dict(type='text',text='Additional full-picture detail of this same still, to resolve small subjects and technical parts.'))
            content.append(ask.image_block(str(detail)))
        text,usd=ask.ask('Catalogue the actual archive shown in these three frames for a documentary writer. This is a CONTACT SHEET: '
            'left, middle and right are successive frames sampled at ten, fifty and ninety percent of ONE source clip. '
            'The contact-sheet panels and Archive heading are review aids, not part of the source video. Still photographs are repeated three times for review. '
            'Do not mistake these review aids for intrusive source graphics or repeated shots. Do not try to make the source fit an existing narration. '
            'For a third-party presenter describe who the source itself credits and what role is evidenced. A topic title does not establish a witness. Transcribe legible dates and results on build or verification screens, including what was checked. Describe only visible subjects, action, hardware and setting. Distinguish flight footage, ground rehearsal, museum display, original illustration, '
            'still photography and another editor\'s graphics. Source captions may establish provenance; filename labels and requested subjects are not proof. '
            'Do not invent an unseen action, date, identity or repair state. Keep disputed identity or events explicitly uncertain. '
            'Return JSON {visible:string,form:string,real_moving_footage:bool,usable:bool,limitations:[string],verified_context:string}. '
            'usable requires a readable, relevant archive picture with no misleading or intrusive overlays. This inventory is not a quality rating.',
            content,
            'wf4_archive_catalogue',max_tokens=1200)
        report=dict(item,**ask.json_obj(text),usd=usd,board=str(board),version=version);C.save(out,report);return report
    with ThreadPoolExecutor(max_workers=3) as pool:rows=list(pool.map(one,items))
    C.save(root/'catalogue.json',rows)
    return rows
