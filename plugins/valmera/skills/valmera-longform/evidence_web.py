"""Find exact primary archive photographs after broad catalogue searches fail."""
from pathlib import Path
import hashlib,json


def download(url):
    import requests,shlex
    try:
        response=requests.get(url,timeout=60);response.raise_for_status()
        return response.content
    except requests.RequestException:
        # The Mac's established archive wall also affects original image URLs.
        # Fetch the same public bytes on the already-authorized box route.
        from footage_box import ssh
        return ssh('curl --fail --location --silent --show-error --max-time 60 '+shlex.quote(url),capture_output=True).stdout


def supply(ctx,script,ft,gaps,reuse_only=False):
    from PIL import Image
    import cinema as C
    from wf4 import story
    from footage import ask
    root=Path(ctx.supply)/'exact-evidence';root.mkdir(exist_ok=True)
    signature=hashlib.sha256(json.dumps(gaps,sort_keys=True).encode()).hexdigest()[:16]
    plan=root/(signature+'.json')
    known={r['url']:r for p in root.glob('*.json') for r in C.read(p).get('images',[])}
    if reuse_only:data=dict(images=list(known.values()))
    elif plan.exists():data=C.read(plan)
    else:
        text,usage=story.writer.ask_web('Find exact original archive images for these documentary gaps. Broad catalogue searches have failed. '
            'Research primary archives and their original captions. Return JSON {images:[{url,page,title,credit,licence,licence_url,caption,lines:[segment indexes]}]}. '
            'url must be a directly downloadable original photograph or original technical illustration, page its source caption page. '
            'Use only public-domain or explicitly openly licensed material. Find several genuinely different pictures when a line needs more than eight seconds. '
            'No museum substitutes for a historical event, wrong crews, modern reconstructions, generated illustrations or duplicate scans. '
            'Do not substitute a picture of a spacecraft for a named person sewing, or a later EVA for an earlier hatch repair. '
            'Return only evidence you actually find. Original mission diagrams explaining the precise operation are useful; distinguish them from filmed events.',
            json.dumps(dict(title=script['title'],gaps=gaps,facts=script['dossier']['facts'],known=list(known.values()))),searches=12,fetches=12,max_tool_calls=12,
            effort='high',max_tokens=5000,what='WF4 exact archive evidence')
        data=story.writer.json_block(text);data['usage']=usage;C.save(plan,data)
    by={r['i']:r for r in ft['lines']};wanted={}
    for gap in gaps:wanted[gap['seg']]=(wanted.get(gap['seg'],'')+' '+(gap.get('heard') or gap['words'])).strip()
    for row in data.get('images',[]):
        key=hashlib.sha256(row['url'].encode()).hexdigest()[:20];path=root/(key+'.jpg');vp=root/(key+'-'+signature+'-check.json')
        if not path.exists():
            try:content=download(row['url'])
            except Exception as error:
                C.save(root/(key+'-download-error.json'),dict(url=row['url'],error=str(error)[:500]));continue
            if len(content)>30_000_000:raise ValueError('archive image exceeds 30 MB')
            raw=root/(key+'.download');raw.write_bytes(content)
            try:
                with Image.open(raw) as im:
                    if max(im.size)<1000:continue
                    im.convert('RGB').save(path,quality=95)
            finally:raw.unlink(missing_ok=True)
        lines={i:wanted[i] for i in row['lines'] if i in wanted}
        if not lines:continue
        if vp.exists():verdict=C.read(vp)
        else:
            text,usd=ask.ask('Verify this original archive image against the supplied narration. Source captions are evidence of identity but not proof of visual fit. '
                'Reject wrong people, event, crew, repair state, museum substitutes, illegible scans, unrelated collages and misleading illustrations. '
                'Return JSON {lines:[only matching segment indexes],document:bool,shows:string,why:string}. document means an original technical illustration '
                'which must be presented as an archive record, never implied to be flight photography.',
                [dict(type='text',text=json.dumps(dict(source=row,words=lines))),ask.image_block(str(path))],
                'wf4_exact_archive_check',max_tokens=1500)
            verdict=ask.json_obj(text);verdict['usd']=usd;C.save(vp,verdict)
        with Image.open(path) as im:w,h=im.size
        source={k:row[k] for k in ('url','title','credit','licence','licence_url')};source['caption_page']=row['page']
        rec=dict(path=str(path),kind='image',document=verdict.get('document',False),w=w,h=h,content_hash=C.digest(path),
                 seconds=7.8,time_range=[0,7.8],source=source,shows=verdict.get('shows',row['caption']))
        for i in verdict['lines']:
            if i not in lines:continue
            line=by.setdefault(i,dict(i=i,clips=[]))
            if not any(c['path']==str(path) for c in line['clips']):line['clips'].append(rec)
            ft['credits'].append(dict(line=i,**source))
    ft['lines']=list(by.values())
