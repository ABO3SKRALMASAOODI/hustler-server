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


def seat_display(l1, meta, ruler, _retry=0):
    """Translate each display block as a unit, clearing every face in its lifetime.

    Text bounds come from the renderer's shaped glyphs. Motion, both title rows,
    and the final reframed faces are included. The safe lower margin is fixed.
    """
    W,H=l1['frame']
    visuals=[i for i in l1['items'] if i['k'] in ('shot','card') and i.get('pic') in meta
             and not (isinstance(i.get('blur'),(int,float)) and i['blur'] >= 20)]
    groups=collections.defaultdict(list)
    for i in l1['items']:
        if i['k']=='word' and i.get('role')=='cap':
            groups['captions'].append(i)
        elif i['k']=='word' and i.get('role')=='big':
            groups['title' if i['v'].startswith('dsp_t') else ('display',i.get('on',i['f'][0]),i['t'])].append(i)
    logs=[]
    for key, items in groups.items():
        frames=range(min(i['f'][0] for i in items),max(i['f'][1] for i in items))
        faces={f:[b for v in visuals if v['f'][0]<=f<v['f'][1] for b in face_boxes(v,meta[v['pic']],f)] for f in frames}
        offset=0.0
        bounds=[]
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
                bounds.append((f,[x+x0-margin,y+y0-margin,x+x1+margin,y+y1+margin]))
        for _ in range(16):
            needed=offset
            for f,b in bounds:
                shifted=[b[0],b[1]+offset,b[2],b[3]+offset]
                for face in faces[f]:
                    if intersects(shifted,face): needed=max(needed,face[3]+.019-b[1])
            if needed <= offset+1e-6: break
            offset=needed
        if any(b[3]+offset>.86 for _,b in bounds):
            # Try the clear band above all faces, keeping below the brand seat.
            offset=min((face[1]-.019-b[3] for f,b in bounds for face in faces[f]),default=0)
        if any(b[1]+offset<.14 or b[3]+offset>.86 or
               any(intersects([b[0],b[1]+offset,b[2],b[3]+offset],face) for face in faces[f]) for f,b in bounds):
            if _retry >= 8 or min(l1['voices'][i['v']]['px'] for i in items) < 64:
                raise RuntimeError(f'display block {key!r} has no face-safe seat within the frame')
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
            return seat_display(l1,meta,ruler,_retry+1)
        for i in items:
            i['y']=[round(y+offset,5) for y in i['y']] if isinstance(i['y'],list) else round(i['y']+offset,5)
        logs.append(dict(group=str(key),shift_y=round(offset,5),face_boxes_checked=sum(map(len,faces.values())),overlaps=0))
    return dict(groups=logs,overlaps=0)
