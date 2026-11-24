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
        # A boundary sample can still be on the old camera. A tiny intersection
        # would magnify a sliver of the selected portrait on both sides.
        common_width = common[2]-common[0]
        common_height = common[3]-common[1]
        if (common_width < .75*min(mapped[2]-mapped[0], crops[i][2]-crops[i][0])
                or common_height < .75*min(mapped[3]-mapped[1], crops[i][3]-crops[i][1])):
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


def tracked_head_span(samples, start, end):
    """Horizontal envelope of the moving head, with room beyond the detected face."""
    boxes = [s for s in samples if start <= s[0] < end and s[3] > s[1]]
    if boxes:
        return (max(0., min(s[1] - .34*(s[3]-s[1]) for s in boxes)),
                min(1., max(s[3] + .34*(s[3]-s[1]) for s in boxes)))


def enclosing_window(path, seconds, action):
    """Find a visible rectangular UI boundary containing the reader's verified action."""
    try:
        import cv2
    except ImportError:
        return None
    capture = cv2.VideoCapture(path)
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds*1000)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        return None
    h,w = frame.shape[:2]
    width = min(720,w)
    height = round(h*width/w)
    gray = cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),(width,height))
    edges = cv2.morphologyEx(cv2.Canny(gray,25,75), cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT,(3,3)))
    contours,_ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    regions = []
    for contour in contours:
        x,y,dx,dy = cv2.boundingRect(contour)
        if dx*dy >= .85*width*height or cv2.contourArea(contour) < .90*dx*dy:
            continue
        box = [x/width,y/height,(x+dx)/width,(y+dy)/height]
        if box[0] <= action[0] < action[2] <= box[2] and box[1] <= action[1] < action[3] <= box[3]:
            regions.append((dx*dy,box))
    if regions:
        _, inner = min(regions)
        # Adjacent borders of the same width can surround the title bar as well.
        l,t,r,b = max((row for row in regions if row[1][0] <= inner[0] and row[1][2] >= inner[2]
                       and row[1][2]-row[1][0] <= 1.15*(inner[2]-inner[0])
                       and row[1][3]-row[1][1] <= 1.55*(inner[3]-inner[1])),default=(0,inner))[1]
        return [max(0,l-.02),max(0,t-.025),min(1,r+.02),min(1,b+.025)]


def repeated_projection(gray):
    """Frame one of two matching lit projections on a dark stage, preserving its ink."""
    import cv2
    import numpy as np

    h, w = gray.shape
    if w < h or np.median(gray) > 45:
        return None
    half = w//2
    left, right = gray[:,:half], gray[:,half:]
    sift = cv2.SIFT_create(contrastThreshold=.015)
    src, a = sift.detectAndCompute(left,None)
    dst, b = sift.detectAndCompute(right,None)
    if a is None or b is None or len(b) < 2:
        return None
    pairs = [m for m,n in cv2.BFMatcher().knnMatch(a,b,k=2) if m.distance < .7*n.distance]
    if len(pairs) < 8:
        return None
    x = np.float32([src[m.queryIdx].pt for m in pairs])
    y = np.float32([dst[m.trainIdx].pt for m in pairs])
    matrix, mask = cv2.estimateAffinePartial2D(x,y,ransacReprojThreshold=2)
    if matrix is None or mask.sum() < 8 or abs(matrix[1,0]) > .06 or not .9 < matrix[0,0] < 1.1:
        return None
    aligned = cv2.warpAffine(right,matrix,(half,h),flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
    # Bound the matching bright ink, including titles beyond the keypoints.
    light = np.minimum(left,aligned)
    ink = light > max(30,np.percentile(light,96))
    yy, xx = np.nonzero(ink)
    if len(xx) < 24:
        return None
    # Intersect with the matched panel's vertical extent to exclude matching stage lights below it.
    hits = x[mask.ravel().astype(bool)]
    top, bottom = np.min(hits[:,1]), np.max(hits[:,1])
    keep = (yy >= top-.10*h) & (yy <= bottom+.10*h)
    if keep.sum() < 24:
        return None
    yy,xx = yy[keep],xx[keep]
    l,r = np.percentile(xx,[.1,99.9])/w
    t,b = np.percentile(yy,[.1,99.9])/h
    if r-l < .14 or b-t < .06:
        return None
    crop = [max(0.,l-.025),max(0.,t-.08),min(.5,r+.025),min(1.,b+.08)]
    highlight = max(.1, np.percentile(left[yy,xx],55)/255)
    gamma = min(1.8,max(1., np.log(highlight)/np.log(.82)))
    return [round(float(v),5) for v in crop], round(float(gamma),3)


def projection_at(path, seconds):
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(path)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC,seconds*1000)
        ok, frame = cap.read()
        if ok:
            h,w = frame.shape[:2]
            width = min(w,768)
            return repeated_projection(cv2.resize(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),
                                                  (width,round(h*width/w))))
    finally:
        cap.release()
