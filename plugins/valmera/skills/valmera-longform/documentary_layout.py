"""Frame original sources on the branded canvas; keep the spoken text on its own rail."""


def fit(plan, span, key, clip, box, **options):
    from scene.motion import fit_panel
    crop = options.get("crop") or [0,0,1,1]
    initial = crop[0] if isinstance(crop[0],list) else crop
    seat = fit_panel(initial, (clip["w"],clip["h"]), (1920,1080), box)
    return plan.card(span, key, seat, **options)


def reading_box(clip):
    """Keep whole text lines around an original underline, including the wrap below it."""
    if clip.get("meaning_box"):
        return clip["meaning_box"]
    from PIL import Image
    import numpy as np
    try:
        with Image.open(clip["path"]) as image:
            rgb = np.asarray(image.convert("RGB").resize((min(1200,image.width),
                max(1,round(image.height*min(1200,image.width)/image.width)))),dtype=np.int16)
    except (KeyError,ValueError,OSError):
        return [0,0,1,1]
    h,w = rgb.shape[:2]
    ink = rgb.max(axis=2)<155
    color = (rgb.max(axis=2)-rgb.min(axis=2)>70)
    rows = np.where(ink.sum(axis=1) >= max(3,w*.009))[0]
    marked = np.where(color.sum(axis=1)>w*.025)[0]
    if not len(rows) or not len(marked):
        return [0,0,1,1]
    groups = np.split(rows, np.where(np.diff(rows)>max(2,h*.005))[0]+1)
    center = marked.mean()
    i = min(range(len(groups)),key=lambda q:abs(groups[q].mean()-center))
    line_h = max(3,groups[i][-1]-groups[i][0]+1)
    top = max(0,int(min(groups[i][0],marked.min())-line_h*.4))
    bottom = min(h,int(max(groups[min(i+1,len(groups)-1)][-1],marked.max())+line_h*.4+1))
    xs = np.where(ink[top:bottom].any(axis=0))[0]
    if not len(xs):
        return [0,0,1,1]
    return [max(0,(xs[0]-line_h)/w),top/h,min(1,(xs[-1]+line_h+1)/w),bottom/h]


def document(plan, span, key, clip, label, companion=None):
    plan.page(span, "#182028", grain=.002)
    crop = reading_box(clip)
    seat = [.035,.18,.965,.79]
    detail = span[0]
    if crop != [0,0,1,1] and span[1]-span[0] >= 75:
        detail = span[0]+24
        fit(plan,[span[0],detail],key,clip,seat,crop=[0,0,1,1])
    fit(plan,[detail,span[1]],key,clip,seat,crop=crop)
    plan.word(span,label,"source",.05,.855,note=False,z=3)


def archive(plan, row, key, crop):
    clip = row['clip']
    plan.page(row['f'], '#14191e', grain=0)
    box = (.025,.195,.975,.85) if clip.get('kind')=='diagram' else (.025,.14,.975,.795)
    fit(plan, row['f'], key, clip, box,
        src=row['src'], crop=crop)
