"""Measured display seating on final camera crops; no interpretation of text."""
import collections
import math


def value(item, key, frame, default):
    v = item.get(key, default)
    return v[min(max(frame-item['f'][0], 0), len(v)-1)] if isinstance(v, list) else v


def face_boxes(item, meta, frame):
    """Source face boxes transformed through the actual crop/card at this frame."""
    crop = item.get('crop') or [0, 0, 1, 1]
    if isinstance(crop[0], list): crop = crop[min(frame-item['f'][0], len(crop)-1)]
    x0,y0,x1,y1 = crop
    cw,ch = x1-x0,y1-y0
    if cw <= 0 or ch <= 0: return []
    if item['k'] == 'card':
        scale = value(item,'s',frame,1)
        w,h = value(item,'w',frame,1)*scale,value(item,'h',frame,1)*scale
        cx,cy = value(item,'x',frame,.5),value(item,'y',frame,.5)
        rot = math.radians(value(item,'rot',frame,0))
    else:
        w,h,cx,cy,rot = 1,1,.5,.5,0
    out=[]
    for fx,fy,fw,fh,*_ in meta.get('faces',[]):
        pts=[]
        for x in (fx,fx+fw):
            for y in (fy,fy+fh):
                dx,dy=((x-x0)/cw-.5)*w,((y-y0)/ch-.5)*h
                pts.append((cx+dx*math.cos(rot)-dy*math.sin(rot)*1920/1080,cy+dx*math.sin(rot)*1080/1920+dy*math.cos(rot)))
        out.append([min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts)])
    return out


def intersects(a,b,pad=.018):
    return a[0] < b[2]+pad and a[2] > b[0]-pad and a[1] < b[3]+pad and a[3] > b[1]-pad


def caption_restarts(l1):
    caps=[i for i in l1['items'] if i['k']=='word' and i.get('role')=='cap']
    groups=collections.defaultdict(list)
    for i in caps: groups[i['f'][0]].append(i['t'])
    bad=[]
    for f, words in sorted(groups.items()):
        before=collections.Counter(i['t'] for i in caps if i['f'][0] <= f-1 < i['f'][1])
        new=list((collections.Counter(words)-before).elements())
        if len(new)>1: bad.append(dict(frame=f,words=new))
    return bad


def caption_pages(caps):
    """Caption words grouped by page: words whose time on screen overlaps share a page. [(first frame, last frame, words)]."""
    pages=[]
    for i in sorted(caps,key=lambda i:i['f'][0]):
        if pages and i['f'][0] < pages[-1][1]:
            pages[-1][2].append(i); pages[-1][1]=max(pages[-1][1],i['f'][1])
        else:
            pages.append([i['f'][0],i['f'][1],[i]])
    return [tuple(p) for p in pages]


SAFE_TOP, SAFE_BASE = .14, .80     # the brand seat above; the taste rule's safe area below (a baseline under .80 is outside)


def _bounds(l1, items, ruler):
    """(frame, box, baseline) for every frame of every word: the box for faces, the baseline for the safe area."""
    W,H=l1['frame']
    out=[]
    for i in items:
        voice=l1['voices'][i['v']]
        quads=ruler.T.Face.of(voice).quads(i['t'],voice['px'],voice.get('track',0))
        if not quads: continue
        x0=min(q[1] for q in quads)/W; x1=max(q[3] for q in quads)/W
        y0=min(q[2] for q in quads)/H; y1=max(q[4] for q in quads)/H
        for f in range(*i['f']):
            x,y=value(i,'x',f,0),value(i,'y',f,0)
            # Stroke and the three sharp landing frames' remaining blur.
            margin=.006
            out.append((f,[x+x0-margin,y+y0-margin,x+x1+margin,y+y1+margin],y))
    return out


def _covered(bounds, faces, offset):
    """Frames on which the block, moved by offset, covers a face."""
    return {f for f,b,_ in bounds if any(intersects([b[0],b[1]+offset,b[2],b[3]+offset],face) for face in faces[f])}


def _clear(bounds, faces, offset):
    return not any(b[1]+offset<SAFE_TOP or base+offset>SAFE_BASE for _,b,base in bounds) and not _covered(bounds,faces,offset)


def _least_cover(bounds, faces):
    """No seat inside the safe band clears every face: the seat in the band that covers a face on the fewest
    frames, nearest the designed seat."""
    if not bounds: return 0.0
    lo=SAFE_TOP-min(b[1] for _,b,_ in bounds); hi=SAFE_BASE-max(base for _,_,base in bounds)
    if hi < lo: return 0.0
    steps=max(1,int(round((hi-lo)/.005)))
    cands=sorted({round(lo+k*(hi-lo)/steps,5) for k in range(steps+1)} | ({0.0} if lo <= 0 <= hi else set()))
    return min(cands,key=lambda o:(len(_covered(bounds,faces,o)),abs(o)))


def seat_display(l1, meta, ruler, _retry=0):
    """Translate each display block as a unit, clearing every face in its lifetime.

    Text bounds come from the renderer's shaped glyphs. Motion, both title rows,
    and the final reframed faces are included. The safe lower margin is fixed.
    Captions share one seat; when no single seat clears the faces of the whole
    film, each caption page takes its own. A block that no seat in the safe band
    can clear takes the seat covering a face on the fewest frames, and says so.
    """
    W,H=l1['frame']
    visuals=[i for i in l1['items'] if i['k'] in ('shot','card') and i.get('pic') in meta
             and not (isinstance(i.get('blur'),(int,float)) and i['blur'] >= 20)]
    groups=collections.defaultdict(list)
    # a display word's bar (a contrast on the accent) is part of its block: it is seated, moved and shrunk with the
    # block's lines, found by the block's `on` (bar_on), never by a voice name a later pass may change
    bars=collections.defaultdict(list)
    for i in l1['items']:
        if i['k']=='card' and i.get('bar_on') is not None:
            bars[('display',int(i['bar_on']))].append(i)
    for i in l1['items']:
        if i['k']=='word' and i.get('role')=='cap':
            groups['captions'].append(i)
        elif i['k']=='word' and i.get('role')=='big':
            # a display block is every line landed together (a two-size stack shares its `on`): seated as one unit
            groups['title' if i['v'].startswith('dsp_t') else ('display',i.get('on',i['f'][0]))].append(i)
    logs=[]
    work=list(groups.items())
    while work:
        key, items = work.pop(0)
        frames=range(min(i['f'][0] for i in items+bars.get(key,[])),max(i['f'][1] for i in items+bars.get(key,[])))
        faces={f:[b for v in visuals if v['f'][0]<=f<v['f'][1] for b in face_boxes(v,meta[v['pic']],f)] for f in frames}
        offset=0.0
        bounds=_bounds(l1,items,ruler)+[(f,[b['x']-b['w']*b.get('s',1)/2,b['y']-b['h']*b.get('s',1)/2,b['x']+b['w']*b.get('s',1)/2,
                                            b['y']+b['h']*b.get('s',1)/2],b['y']+b['h']*b.get('s',1)/2) for b in bars.get(key,[]) for f in range(*b['f'])]
        for _ in range(16):
            needed=offset
            for f,b,_ in bounds:
                shifted=[b[0],b[1]+offset,b[2],b[3]+offset]
                for face in faces[f]:
                    if intersects(shifted,face): needed=max(needed,face[3]+.019-b[1])
            if needed <= offset+1e-6: break
            offset=needed
        if any(base+offset>SAFE_BASE for _,_,base in bounds):
            # Try the clear band above all faces, keeping below the brand seat.
            offset=min((face[1]-.019-b[3] for f,b,_ in bounds for face in faces[f]),default=0)
        covered=0
        if not _clear(bounds,faces,offset):
            pages=caption_pages(items) if key=='captions' else []
            if len(pages) > 1:
                # Faces fill every height of the film somewhere: one shared caption seat cannot clear them all,
                # so each page is seated in its own lifetime.
                work[:0]=[(('captions',a),words) for a,_,words in pages]
                continue
            if key=='captions' or (isinstance(key,tuple) and key[0]=='captions') or \
                    _retry >= 8 or min(l1['voices'][i['v']]['px'] for i in items) < 64:
                offset=_least_cover(bounds,faces)
                covered=len(_covered(bounds,faces,offset))
            else:
                # A tall two-line headline needs less type, not a face hidden by it.
                # Keep the block's center and relative row spacing as it shrinks.
                center=sum(value(i,'y',i['f'][1]-1,0) for i in items)/len(items)
                names={}
                for i in items:
                    old=i['v']
                    if old not in names:
                        new=f'{old}_seat{_retry}'
                        l1['voices'][new]=dict(l1['voices'][old],px=l1['voices'][old]['px']*.9)
                        names[old]=new
                    vo=l1['voices'][old]; nv=l1['voices'][names[old]]
                    delta=(ruler.T.Face.of(vo).measure(i['t'],vo['px'],vo.get('track',0))-
                           ruler.T.Face.of(nv).measure(i['t'],nv['px'],nv.get('track',0)))/W/2
                    i['x']=[x+delta for x in i['x']] if isinstance(i['x'],list) else i['x']+delta
                    i['y']=[center+(y-center)*.9 for y in i['y']] if isinstance(i['y'],list) else center+(i['y']-center)*.9
                    i['v']=names[old]
                for b in bars.get(key,[]):            # the bar shrinks with its lines, about the same centre
                    b['y']=round(center+(b['y']-center)*.9,5); b['w']=round(b['w']*.9,5); b['h']=round(b['h']*.9,5)
                out=seat_display(l1,meta,ruler,_retry+1)
                if not _retry: _bar_keys_off(l1)
                return out
        for i in items+bars.get(key,[]):
            i['y']=[round(y+offset,5) for y in i['y']] if isinstance(i['y'],list) else round(i['y']+offset,5)
        logs.append(dict(group=str(key),shift_y=round(offset,5),face_boxes_checked=sum(map(len,faces.values())),overlaps=covered))
    if not _retry: _bar_keys_off(l1)
    return dict(groups=logs,overlaps=sum(g['overlaps'] for g in logs))


def _bar_keys_off(l1):
    """The seat's helper key leaves the scene once the blocks are seated."""
    for i in l1['items']:
        i.pop('bar_on',None)
