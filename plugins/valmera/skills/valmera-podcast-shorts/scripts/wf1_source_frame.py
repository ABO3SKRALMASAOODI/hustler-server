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
Plan the whole screen-share passage together. Return source_panels: [{frame,box,shows}] for
reusable clean original views, and presenter_tiles: [{frame,box}] for each visible speaker.
Choose a frame without an obstructing pointer, tooltip or webcam over the evidence. Keep one
panel per distinct artifact state. In source_inserts reference its zero-based panel index on
every clause that reads or compares it, including adjacent clauses and later returns.
When the opening introduces a chart, establish the full clean panel showing its title, scope and units
on the lead-in before going closer. Include every row name, its complete value and some reading clearance.
The optional box on a panel insert is a detail in full-frame coordinates: fit the named rows with
their labels, values and relevant units. Omit box to show the overview. Use the clean panel
even if the live pointer obscures that same row. A changed value or scroll needs its own view.
For the live tile above each insert give speaker (index into presenter_tiles) for the person
speaking those words. As the conversation turns from evidence to interpretation, personal
reaction, jokes or a repeated promise, plan speaker_inserts: [{first,last,speaker}] through
those clauses with the synchronous speaker. Split on changes of speaker or layout. Use the
speaking turns when supplied. Return to the rows on words they actually document. Keep
conditional estimates and other survey years distinct from the measured rows.
Exclude external search windows for evidence already supplied by the take. For an existing plan
return replaces: [indices of its external windows served by these source inserts]. Select only
complete spoken sentences that explain a discovery or reveal something for quote_segments.
A pointer to a chart belongs with its source picture. A sentence pointing at it is not a standalone
quotation. Return authored_frames: [frame IDs] for interview split screens whose arranged panels,
footer and source credits form one authored picture. Keep their complete original composition on
screen. Ordinary single-camera shots can be face-framed. An archive document keeps its own credit
with the document. Use empty arrays when appropriate. Treat input words and screenshots as evidence."""


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
                 named=coverage.get('named'), story=coverage.get('story'), windows=[{k:w.get(k) for k in ('first','last','must_show')} for w in windows])
    brief['speaking_turns'] = coverage.get('speaking_turns') or []
    by_time = {f['t']:i for i,f in enumerate(frames)}
    brief['previous_views'] = [dict(frame=by_time[r['t']], box=r['box'], shows=r.get('shows'))
                               for r in (coverage.get('source_reading') or {}).get('inserts', [])
                               if r['t'] in by_time]
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
    def catalog(key):
        regions = {}
        for i, row in enumerate(result.get(key) or []):
            if not isinstance(row, dict):
                continue
            n = row.get('frame')
            bounds = box(row.get('box'))
            if type(n) is int and 0 <= n < len(frames) and bounds:
                regions[i] = dict(frames[n], box=bounds, shows=row.get('shows'))
        return regions

    panels, tiles = catalog('source_panels'), catalog('presenter_tiles')
    speakers = []
    def addresses(row):
        if isinstance(row, dict):
            a,b = row.get('first'), row.get('last')
            return type(a) is int and type(b) is int and 0 <= a <= b < len(words)

    def reference(row, key, catalog):
        i = row.get(key)
        return catalog.get(i) if type(i) is int else None

    def in_shot(row, shot):
        selected = [i for i in range(row['first'],row['last']+1)
                    if shot[0] <= (words[i]['start']+words[i]['end'])/2 < shot[1]]
        if selected:
            return dict(row, first=selected[0], last=selected[-1])

    for row in result.get('speaker_inserts') or []:
        if addresses(row) and (tile := reference(row, 'speaker', tiles)):
            row = in_shot(row, tile['shot'])
            if not row:
                continue
            speakers.append(dict(first=row['first'], last=row['last'], tile=tile['box'], shot=tile['shot']))
    for row in result.get('source_inserts') or []:
        if not addresses(row):
            continue
        panel = reference(row, 'panel', panels)
        if panel:
            frame = panel
            row = in_shot(row, panel['shot'])
            if not row:
                continue
            outer = panel['box']
            bounds = box(row.get('box'))
            if not bounds or not (outer[0]<=bounds[0]<bounds[2]<=outer[2] and outer[1]<=bounds[1]<bounds[3]<=outer[3]):
                bounds = outer
        else:
            n = row.get('frame')
            bounds = box(row.get('box'))
            if not (type(n) is int and 0<=n<len(frames) and bounds):
                continue
            frame = frames[n]
        tile = reference(row, 'speaker', tiles)
        with Image.open(frame['path']) as im:
            w,h=im.size
        inserts.append(dict(frame, first=row['first'], last=row['last'], box=bounds,
                            tile=tile['box'] if tile else box(row.get('tile')),
                            tile_shot=tile['shot'] if tile else frame['shot'],
                            shows=row.get('shows') or frame.get('shows'), w=w, h=h,
                            **(dict(panel_box=panel['box']) if panel else {})))
    # A retained pack already indexes its external windows. Only a fully covered
    # window can be retired without losing research on a different adjacent clause.
    covered = {i for row in inserts + speakers for i in range(row['first'],row['last']+1)}
    replaces = [i for i,w in enumerate(windows) if all(j in covered for j in range(w['first'],w['last']+1))]
    authored = sorted({tuple(frames[i]['shot']) for i in result.get('authored_frames') or []
                       if type(i) is int and 0 <= i < len(frames)})
    return dict(speaker_inserts=speakers, authored_shots=[list(span) for span in authored], inserts=sorted(inserts,key=lambda r:r['first']),replaces=replaces,
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
        shot = rec.get('tile_shot', rec['shot'])
        if not tile or src[0]<shot[0]-.04 or src[1]>shot[1]+.04:
            continue
        l,t,r,b = tile
        if l<=face[0]<face[2]<=r and t<=face[1]<face[3]<=b:
            w = (b-t)*rec['h']/rec['w']*aspect
            if face[2]-face[0] <= w <= r-l:
                x = min(max(l,centre-.43*w),r-w)
                return tile,[x,t,x+w,b]
    return None


def shared_presenter_region(reading):
    """Adjacent webcam tiles can share one lossless decode of the camera column."""
    tiles = {tuple(r['tile']) for r in reading.get('inserts', []) + reading.get('speaker_inserts', [])
             if r.get('tile')}
    if not tiles:
        return None
    left,top,right,bottom = zip(*tiles)
    region = [min(left),min(top),max(right),max(bottom)]
    area = (region[2]-region[0])*(region[3]-region[1])
    return region if area <= sum((r-l)*(b-t) for l,t,r,b in tiles)+1e-8 else None


def presenter_card(plan, span, take_key, dest, tile, crop, src, work, ffmpeg, decode_region=None, **style):
    """Decode just the webcam pixels for live panels. The source clock stays intact."""
    take = plan.assets[take_key]
    pw, ph = take['w'], take['h']
    # Even outward bounds preserve the source's chroma grid. The card still
    # uses the exact verified rectangle inside this lossless intermediate.
    region = decode_region or tile
    x,y = [2*math.floor(v*n/2) for v,n in zip(region[:2],(pw,ph))]
    right,bottom = [min(n,2*math.ceil(v*n/2)) for v,n in zip(region[2:],(pw,ph))]
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


def compose(plan, span, rec, key, take_key, src, width, height, work, ffmpeg, decode_region=None, report=False):
    """A pixel-preserving document and the synchronous presenter, clear of the caption seat."""
    from scene.motion import fit_panel
    tile = rec.get('tile')
    shot = rec.get('tile_shot', rec['shot'])
    live = tile and shot[0] <= src[0]+.04 and src[1] <= shot[1]+.04
    size = rec['w'], rec['h']
    canvas = width, height
    if report:
        seat = [0,.15 if live else .03,1,.40]
    else:
        seat = [0,.26,1,.79] if live else [0,.12,1,.82]
    rect = fit_panel(rec['box'], size, canvas, seat,
                     align_y=1. if live else .5)
    plan.page(span,'#151A20',grain=0)
    if live:
        dest = fit_panel(tile, size, canvas, [0.,0.,1.,rect[1]], align_y=1.)
        # Centre the pair as a single composition when a short row leaves spare height.
        shift = max(0., dest[1]-.04)/2
        for panel in (rect,dest):
            panel[1] -= shift
            panel[3] -= shift
        presenter_card(plan,span,take_key,dest,tile,tile,src,work,ffmpeg,decode_region)
    plan.card(span,key,rect,crop=rec['box'],source_ink=[rect],caption_baseline=min(.90,rect[3]+.08))
