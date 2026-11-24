"""Match a continuing camera feed when an interview switches to a multi-person layout."""
import os

from .motion import fit_panel


def _square(box, width, height):
    # fit_panel measures a square in source pixels, inside the reader's chosen region.
    return fit_panel([0,0,1,1], (1,1), (width,height), box)


def _match(path, frame, region):
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(path)
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame-1)
        detector = cv2.SIFT_create(nfeatures=1000)
        points = []
        for index in range(2):
            ok, rgb = cap.read()
            if not ok:
                return
            h,w = rgb.shape[:2]
            width = min(768,w)
            height = round(width*h/w)
            gray = cv2.resize(cv2.cvtColor(rgb,cv2.COLOR_BGR2GRAY), (width,height))
            mask = None
            if index:
                l,t,r,b = np.rint(np.array(region)*[width,height,width,height]).astype(int)
                mask = np.zeros_like(gray)
                mask[t:b,l:r] = 255
            points.append(detector.detectAndCompute(gray,mask))
    finally:
        cap.release()
    (p,d),(q,e) = points
    if d is None or e is None or len(e)<2:
        return
    pairs = [a for a,b in cv2.BFMatcher().knnMatch(d,e,k=2) if a.distance < .72*b.distance]
    if len(pairs)<8:
        return
    xy = np.float32([p[m.queryIdx].pt for m in pairs])
    uv = np.float32([q[m.trainIdx].pt for m in pairs])
    matrix, inliers = cv2.estimateAffinePartial2D(xy,uv,ransacReprojThreshold=2)
    if matrix is None or inliers.sum()<8 or abs(matrix[1,0])>.02 or matrix[0,0]<=0:
        return
    return float(matrix[0,0]), float(matrix[0,2]/width), float(matrix[1,2]/height)


def closing_crops(path, views, size, fps):
    """Intersect corresponding live pixels across the cut and retain the same seat on both sides."""
    crops = [_square(v["box"], *size) for v in views]
    chain = []
    for i in range(1,len(views)):
        prev, curr = views[i-1:i+1]
        if (prev["first"] != curr["first"] or abs(prev["shot"][1]-curr["shot"][0])>.5/fps
                or not os.path.isfile(path)):
            chain.clear()
            continue
        try:
            import cv2
            transform = _match(path, round(curr["shot"][0]*fps), curr["box"])
        except (ImportError, AttributeError):
            transform = None
        except cv2.error:
            transform = None
        if transform is None:
            chain.clear()
            continue
        s, x, y = transform
        offsets = (x,y,x,y)
        mapped = [s*v+off for v,off in zip(crops[i-1],offsets)]
        common = [op(a,b) for op,a,b in zip((max,max,min,min),mapped,crops[i])]
        if common[2]<=common[0] or common[3]<=common[1]:
            chain.clear()
            continue
        crops[i] = _square(common,*size)
        chain.append((i,s,offsets))
        for j,scale,shift in reversed(chain):
            crops[j-1] = [(v-off)/scale for v,off in zip(crops[j],shift)]
    return crops


def portrait_region(face, size, frame=None):
    """A square around the tracked face, inside the visible feed of a letterboxed composite."""
    import numpy as np
    width, height = size
    x,y,w,h = face[:4]
    cx,cy = x+w/2,y+h/2
    top,bottom = 0.,1.
    if frame is not None:
        ph,pw = frame.shape[:2]
        # Measure the camera column inside the face's horizontal extent. Border
        # credits may have a few bright pixels, but not a sustained textured row.
        column = frame[:,max(0,int(x*pw)):min(pw,int((x+w)*pw)+1)]
        if column.size:
            active = np.mean(column.max(axis=2)>36,axis=1) > .52
            mid = min(ph-1,max(0,round(cy*ph)))
            edges = np.flatnonzero(~active)
            above, below = edges[edges<mid], edges[edges>mid]
            lo = (above[-1]+1)/ph if above.size else 0.
            hi = below[0]/ph if below.size else 1.
            if lo <= y and y+h <= hi:
                top,bottom = lo,hi
    side = min(bottom-top, width/height, max(.58,1.35*h))
    dx = side*height/width
    left = min(1.-dx,max(0.,cx-dx/2))
    top = min(bottom-side,max(top,cy-side/2))
    return [left,top,left+dx,top+side]
