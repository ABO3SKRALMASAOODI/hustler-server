"""Reflow the source camera when an interview carries its own account."""
from functools import lru_cache

import cv2
import numpy as np
from scene.motion import fit_panel


def camera_panels(frame):
    """Two broad camera feeds bounded by a common matte, including a real middle gutter."""
    bright = frame.max(axis=2) > 35
    rows = np.flatnonzero(bright.mean(axis=1) > .58)
    if not len(rows):
        return []
    top, end = rows[0], rows[-1]+1
    h, w = bright.shape
    if top < .04*h or end > .96*h or end-top < .5*h:
        return []
    columns = bright[top:end].mean(axis=0) > .28
    gaps = np.flatnonzero(np.diff(np.r_[False, columns, False]))
    runs = [(l,r) for l,r in zip(gaps[::2],gaps[1::2]) if r-l > .30*w]
    if len(runs) != 2:
        return []
    (a,b),(c,d) = runs
    if not (.4*w < b < c < .6*w and (c-b) < .07*w):
        return []
    return [[0,0,1,top/h], [a/w,top/h,b/w,end/h], [c/w,top/h,d/w,end/h], [0,end/h,1,1]]


def camera_frame(path, seconds):
    capture = cv2.VideoCapture(path)
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, 1000*seconds)
        ok, frame = capture.read()
        return frame if ok else None
    finally:
        capture.release()


def lettering(gray):
    # Remove changing low-frequency light under a publisher's overprinted masthead.
    k = max(3, round(gray.shape[1]/220))
    return cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, np.ones((k,k),np.uint8))


def source_masthead(frame, panels):
    gray = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
    header = int(panels[0][3]*len(gray))-2
    y,x = np.where(gray[:header] > 65)
    if not len(x):
        return None
    l,t = max(0,x.min()-4),max(0,y.min()-4)
    r,b = min(gray.shape[1],x.max()+5),min(header,y.max()+5)
    template = lettering(gray)[t:b,l:r].copy()
    if template.std() < 3:
        return None
    return template, [l/gray.shape[1],t/len(gray),r/gray.shape[1],b/len(gray)]


def interview_cameras(path, authored):
    """Use the show's own two-camera matte and match its actual masthead on a later single."""
    @lru_cache(maxsize=8)
    def two_shot(shot):
        seconds = sum(shot)/2
        frame = camera_frame(path,seconds)
        panels = camera_panels(frame) if frame is not None else []
        return panels, source_masthead(frame,panels) if panels else None

    @lru_cache(maxsize=32)
    def read(shot):
        if shot in authored:
            panels,_ = two_shot(shot)
            if panels:
                return panels,None
        frame = camera_frame(path,sum(shot)/2)
        if frame is None:
            return [],None
        gray = cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        strip = lettering(gray[:max(1,round(.2*len(gray)))])
        for reference in sorted(authored,key=lambda other: abs(shot[0]-other[0])):
            _,header = two_shot(reference)
            if header is None:
                continue
            template,original = header
            h,w = template.shape
            if h > len(strip) or w > strip.shape[1]:
                continue
            _,score,_,(x,y) = cv2.minMaxLoc(cv2.matchTemplate(strip,template,cv2.TM_CCOEFF_NORMED))
            if score >= .75:
                box = [x/gray.shape[1],y/len(gray),(x+w)/gray.shape[1],(y+h)/len(gray)]
                return [],(box,original,sum(reference)/2)
        return [],None
    return read


def place(plan, span, key, src, info, camera, size):
    panels, masthead = camera
    source = (info["pw"], info["ph"])
    if panels:
        # Preserve both participants and the source's original header and footer.
        # Timed speech owns the gap between the cameras.
        seats = [[0,.025,1,.115], [0,.202,1,.519], [0,.596,1,.913], [0,.925,1,.993]]
        boxes = [fit_panel(crop, source, size, seat) for crop,seat in zip(panels,seats)]
        plan.page(span, "#08090B", grain=0, source_ink=boxes, caption_baseline=.567)
        for box,crop in zip(boxes,panels):
            plan.card(span,key,box,crop=crop,src=src)
        return True
    if not masthead or not info["faces"]:
        return False
    # A credit outside the portrait stays in its original lettering above the picture.
    # Keep the full camera height and enough width for the whole tracked head.
    matched, original, reference_time = masthead
    l,t,r,b = matched
    credit = [max(0,l-.015), max(0,t-.014), min(1,r+.015), min(1,b+.014)]
    face = max(info["faces"], key=lambda f:f[2]*f[3])
    from scene.continuity import tracked_head_span
    head = tracked_head_span(info.get("track", []), *src)
    left,right = head or (face[0]-.18*face[2], face[0]+1.18*face[2])
    # Use the available headroom up to the label when the preferred wider margin crosses it.
    if credit[2] <= face[0]-.06*face[2]:
        a,b = credit[2],1.
        left = max(a,left)
    else:
        a,b = 0.,credit[0]
        right = min(b,right)
        if b < face[0]+1.06*face[2]:
            return False
    if not a <= left < right <= b:
        return False
    seat = [0,.202,1,.99]
    width = min(b-a,max(right-left, size[0]/((seat[3]-seat[1])*size[1])*source[1]/source[0]))
    x = max(a,min(b-width,(left+right-width)/2))
    crop = [x,0,x+width,1]
    box = fit_panel(crop,source,size,seat)
    label = fit_panel(original,source,size,[.028,.115,.485,.188])
    plan.page(span,"#101215",grain=0,source_ink=[label],caption_baseline=.89)
    plan.card(span,key,box,crop=crop,src=src,speaker_panel=True)
    plan.card(span,key,label,crop=original,src=[reference_time, reference_time])
    return True
