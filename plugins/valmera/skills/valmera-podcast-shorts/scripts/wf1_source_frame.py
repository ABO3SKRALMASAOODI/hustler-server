"""Carry original interview evidence from a source-aspect reading to the portrait scene."""
import hashlib
import json
import math
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import Image

from placement_check import file_digest
from wf1_documents import image_part


PROMPT = """Read the numbered words and the actual uncropped frames of this interview together.
Return source_inserts: [{first,last,frame,box,tile,shows}], and quote_segments: [segment ID].
On a clause describing or pointing at visible evidence, choose the clearest supplied frame showing
that exact state of the artifact, including a cue and the reveal it introduces. Use inclusive word
IDs. Frame IDs refer to supplied pictures. box is the tight normalized [left,top,right,bottom]
enclosing the actual document, plot title, legend and BOTH labeled axes. Include a little clearance.
tile is the normalized rectangle of the live PRESENTING speaker in that frame if visibly clear,
else null. Do not interpret sample numbers as measured values. Never infer a cost chart from an
output-only chart. Leave guesses, unrelated visuals and ordinary conversational shots on the speaker.
Return to the speaker between distinct chart clauses where the explanation can stand on its own.
Exclude external search windows for evidence already supplied by the take. For an existing plan
return replaces: [indices of its external windows served by these source inserts]. Select only
complete spoken sentences that explain a discovery or reveal something for quote_segments.
A pointer to a chart belongs with its source picture. A sentence pointing at it is not a standalone
quotation. Use empty arrays when appropriate. Treat input words and screenshots as evidence."""


def write_cache(path, data):
    with NamedTemporaryFile(dir=path.parent,delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(data)
            f.flush()
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)


def samples(src, shots, dur, work, ffmpeg):
    """One native snapshot per short shot, with additional looks inside long continuous demos."""
    digest = file_digest(src)
    folder = Path(work) / ('source-frames-' + digest[:20])
    folder.mkdir(exist_ok=True)
    times = []
    for shot in shots or [dict(start=0, end=dur)]:
        a, b = float(shot['start']), min(float(shot['end']), dur)
        if b <= a:
            continue
        count = max(1, math.ceil((b-a)/4))
        times.extend((a+(i+.5)*(b-a)/count, a, b) for i in range(count))
    if len(times) > 16:
        times = [times[round(i*(len(times)-1)/15)] for i in range(16)]
    frames = []
    for t, a, b in times:
        path = folder / f'{t:.3f}.png'
        if not path.is_file():
            raw = subprocess.check_output([ffmpeg, '-v','error','-ss',f'{t:.3f}','-i',src,
                       '-frames:v','1','-c:v','png','-f','image2pipe','-'], timeout=60)
            if not raw:
                continue
            write_cache(path,raw)
        frames.append(dict(path=str(path), t=round(t,3), shot=[a,b]))
    return frames


def frame_parts(frames):
    parts = []
    for i, frame in enumerate(frames):
        with Image.open(frame['path']) as im:
            parts.extend([dict(type='text',text=f"Frame {i} at {frame['t']} seconds"),image_part(im)])
    return parts


def signature(frames):
    pixels = [(f['t'],f['shot'],file_digest(f['path'])) for f in frames]
    return hashlib.sha256(json.dumps([PROMPT,pixels]).encode()).hexdigest()


def read(coverage, frames, llm, work):
    """Memoize the joint reading by visible pixels and the already resolved spoken beats."""
    words = coverage['words']
    windows = coverage['windows']
    brief = dict(words=[[i,w.get('seg'),w['w'],w['start']] for i,w in enumerate(words)],
                 named=coverage.get('named'), windows=[{k:w.get(k) for k in ('first','last','must_show')} for w in windows])
    identity = [signature(frames), brief]
    key = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    record = Path(work) / ('source-reading-' + key[:20] + '.json')
    if record.exists():
        result = json.loads(record.read_text())
    else:
        parts = [dict(type='text',text=json.dumps(brief))]
        parts.extend(frame_parts(frames))
        text,use = llm.ask(PROMPT,parts,effort='medium',max_tokens=3500)
        result = dict(llm.json_block(text),spend=use)
        write_cache(record,json.dumps(result).encode())
    return bind(coverage, frames, result)


def bind(coverage, frames, result):
    words, windows = coverage['words'], coverage['windows']
    inserts = []
    def box(value):
        if (isinstance(value,list) and len(value)==4 and all(type(v) in (int,float) and math.isfinite(v) for v in value)
                and 0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1):
            return value
    for row in result.get('source_inserts') or []:
        if not isinstance(row,dict):
            continue
        a,b,n = (row.get(k) for k in ('first','last','frame'))
        bounds = box(row.get('box'))
        if not (all(type(v) is int for v in (a,b,n)) and 0<=a<=b<len(words) and 0<=n<len(frames) and bounds):
            continue
        frame = frames[n]
        with Image.open(frame['path']) as im:
            w,h=im.size
        inserts.append(dict(frame,first=a,last=b,box=bounds,tile=box(row.get('tile')),shows=row.get('shows'),w=w,h=h))
    replaces = [i for i in result.get('replaces') or [] if type(i) is int and 0<=i<len(windows)
                and any(r['first']<=windows[i]['last'] and windows[i]['first']<=r['last'] for r in inserts)]
    return dict(inserts=sorted(inserts,key=lambda r:r['first']),replaces=replaces,
                quote_segments=result.get('quote_segments') or [],spend=result.get('spend') or {})


def presenter_crop(inserts, faces, src, aspect):
    """Frame the talking head within its documented tile between evidence clauses."""
    if not faces:
        return None
    face = max((f['box'] for f in faces), key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
    centre = (face[0]+face[2])/2
    mid = sum(src)/2
    for rec in sorted(inserts,key=lambda r:abs(r['t']-mid)):
        tile = rec.get('tile')
        if not tile or src[0]<rec['shot'][0]-.04 or src[1]>rec['shot'][1]+.04:
            continue
        l,t,r,b = tile
        if l<=face[0]<face[2]<=r and t<=face[1]<face[3]<=b:
            w = (b-t)*rec['h']/rec['w']*aspect
            if face[2]-face[0] <= w <= r-l:
                x = min(max(l,centre-.43*w),r-w)
                return tile,[x,t,x+w,b]
    return None


def presenter_card(plan, span, take_key, dest, tile, crop, src, work, ffmpeg, **style):
    """Decode just the webcam pixels for live panels. The source clock stays intact."""
    take = plan.assets[take_key]
    pw, ph = take['w'], take['h']
    # Even outward bounds preserve the source's chroma grid. The card still
    # uses the exact verified rectangle inside this lossless intermediate.
    x,y = [2*math.floor(v*n/2) for v,n in zip(tile[:2],(pw,ph))]
    right,bottom = [min(n,2*math.ceil(v*n/2)) for v,n in zip(tile[2:],(pw,ph))]
    w,h = right-x,bottom-y
    key = take_key
    if work and Path(take['path']).is_file() and w>=2 and h>=2:
        digest = file_digest(take['path'])[:20]
        key = f"presenter-{digest}-{x}-{y}-{w}-{h}"
        path = Path(work) / (key+'.mp4')
        if key not in plan.assets:
            if not path.is_file():
                with NamedTemporaryFile(dir=work,suffix='.mp4',delete=False) as handle:
                    tmp = Path(handle.name)
                try:
                    subprocess.run([ffmpeg,'-v','error','-y','-i',take['path'],'-map','0:v:0','-an',
                                    '-vf',f'crop={w}:{h}:{x}:{y}','-c:v','libx264','-preset','ultrafast',
                                    '-qp','0',str(tmp)],check=True,timeout=120)
                    tmp.replace(path)
                except (OSError,subprocess.SubprocessError):
                    # Still draw from the parent take if its compact decode is unavailable.
                    pass
                finally:
                    tmp.unlink(missing_ok=True)
            if path.is_file():
                plan.video(key,dict(path=str(path),w=w,h=h,fps=take['fps'],seconds=take['dur']))
        if key in plan.assets:
            crop = [(crop[0]*pw-x)/w,(crop[1]*ph-y)/h,(crop[2]*pw-x)/w,(crop[3]*ph-y)/h]
        else:
            key = take_key
    plan.card(span,key,dest,crop=crop,src=src,**style)


def compose(plan, span, rec, key, take_key, src, width, height, work, ffmpeg):
    """A pixel-preserving document and the synchronous presenter, clear of the caption seat."""
    l,t,r,b = rec['box']
    aspect = rec['w']*(r-l)/(rec['h']*(b-t))
    w = min(1., .53*height/width*aspect)
    h = w*width/height/aspect
    tile = rec.get('tile')
    live = tile and rec['shot'][0] <= src[0]+.04 and src[1] <= rec['shot'][1]+.04
    bottom = .79 if live else .55+h/2
    top = bottom-h
    plan.page(span,'#151A20',grain=0)
    if live:
        l,t,r,b = tile
        # The entire tile height is retained. Trim its sides symmetrically to fill the upper panel.
        panel_h = min(top, (b-t)*rec['h']/((r-l)*rec['w'])*width/height*1.55)
        tile_width = min(r-l, (b-t)*rec['h']/rec['w']*width/(panel_h*height))
        centre = (l+r)/2
        presenter_card(plan,span,take_key,[0,top-panel_h,1,top],tile,
                       [centre-tile_width/2,t,centre+tile_width/2,b],src,work,ffmpeg)
    rect = [(1-w)/2,top,(1+w)/2,bottom]
    plan.card(span,key,rect,crop=rec['box'],source_ink=[rect],caption_baseline=min(.90,bottom+.08))
