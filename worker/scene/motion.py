"""Camera ops: a picture's framing and how it moves, baked to one crop a frame.

A framing is a crop of the picture in picture fractions, [x0, y0, x1, y1], with the frame's shape. A track is
a framing plus the moves laid on it, and comes out as the list ``shot.crop`` carries. The runtime's shutter
(``film.shutter``) then blurs whatever moves, by exactly how far it moved, so no op here asks for blur.

Every number is the references' own (scene/bake.py MOVE, DRIFT_RATE, CAM_SETTLE, PUNCH_SIZE, WHIP; notes in
week-1112/notes/VIS.md). Nothing reads meaning: the pacing driver (scene/pace.py) says where and when.

    cam = Cam(pw, ph, W / H)
    wide = cam.cover(focus=(0.5, 0.4))
    face = cam.on_face(face_box)                       # eyes at 0.37 of the frame, the head whole
    crops = track(cam, n, wide, fps, drift=dict(rate=0.06, dir=1), settle=dict(size=0.05, dir=-1))
    crops = track(cam, n, wide, fps, to=dict(crop=face, at=14, frames=12, kind="out", over=0.11))
    crops = track(cam, n, wide, fps, whip_out=dict(frames=4, travel=0.28, ang=0.0))
"""
import math

from . import bake

EYES_Y = 0.345            # where the eyes sit in the frame, top to bottom: the references' median (quartiles 0.31 to 0.40)
FACE_H = (0.24, 0.31, 0.40)   # a face's height in the frame: the references' median (0.24), a closer framing, and the talking heads' (WF1 0.34, WF4 0.41)
FACE_MARGIN = 0.14        # a head is kept whole with this much of its own height around the detector's box (hair, chin)
MAX_UP = 2.2              # the tightest a picture is drawn: this many output pixels a source pixel, no more


def r5(x):
    return round(float(x), 5)


class Cam:
    """One picture's camera: what crops it can give a frame of this shape."""

    def __init__(self, pw, ph, aspect, out_w=1080, max_up=MAX_UP):
        self.pw, self.ph, self.aspect, self.max_up = float(pw), float(ph), float(aspect), float(max_up)
        pa = self.pw / self.ph
        # the widest crop of the frame's shape: the cover crop
        self.w_max = min(1.0, self.aspect / pa)
        # the tightest: the upscale limit, and never under a tenth of the cover
        self.w_min = min(self.w_max, max(out_w / (self.pw * max_up), self.w_max * 0.1))

    def h_of(self, w):
        return w * self.pw / (self.aspect * self.ph)

    def rect(self, cx, cy, w):
        """The crop of width w (picture fraction) centred as near (cx, cy) as the picture allows."""
        w = min(max(w, self.w_min), self.w_max)
        h = self.h_of(w)
        cx = min(max(cx, w / 2), 1 - w / 2)
        cy = min(max(cy, h / 2), 1 - h / 2)
        return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

    def cover(self, focus=(0.5, 0.42), at=(0.5, 0.42), zoom=1.0):
        """The widest crop (or 1/zoom of it) that puts picture point ``focus`` at frame point ``at``."""
        w = self.w_max / max(zoom, 1.0)
        h = self.h_of(w)
        return self.rect(focus[0] + (0.5 - at[0]) * w, focus[1] + (0.5 - at[1]) * h, w)

    def on_face(self, face, height=FACE_H[1], eyes_y=EYES_Y, eyes=None, x_at=0.5):
        """A framing on a face: the face ``height`` of the frame tall, its eyes at ``eyes_y``, the head whole.
        face: [x, y, w, h] in picture fractions; eyes: their y in the picture when the detector gave it."""
        fx, fy, fw, fh = face
        h = min(max(fh / max(height, 0.05), self.h_of(self.w_min)), self.h_of(self.w_max))
        w = h * self.aspect * self.ph / self.pw
        ey = eyes if eyes is not None else fy + fh * 0.42
        c = self.rect(fx + fw / 2 + (0.5 - x_at) * w, ey + (0.5 - eyes_y) * h, w)
        return self.keep_whole(c, [face])

    def on_region(self, region, margin=0.12):
        """The tightest framing that holds a region [x0, y0, x1, y1] with a margin round it."""
        x0, y0, x1, y1 = region
        w = max((x1 - x0) * (1 + 2 * margin), (y1 - y0) * (1 + 2 * margin) * self.aspect * self.ph / self.pw)
        return self.rect((x0 + x1) / 2, (y0 + y1) / 2, w)

    def keep_whole(self, crop, faces, margin=FACE_MARGIN):
        """Move, and if it must widen, a crop so that every face it shows is whole: no head cut by the frame's edge.
        A face whose centre lies outside the crop is not this framing's subject and is left out."""
        x0, y0, x1, y1 = crop
        for _ in range(3):
            moved = False
            for fx, fy, fw, fh in faces or ():
                cx, cy = fx + fw / 2, fy + fh / 2
                if not (x0 < cx < x1 and y0 < cy < y1):
                    continue
                bx0, by0, bx1, by1 = fx - fw * margin, fy - fh * (margin + 0.18), fx + fw * (1 + margin), fy + fh * (1 + margin)
                bx0, by0, bx1, by1 = max(bx0, 0.0), max(by0, 0.0), min(bx1, 1.0), min(by1, 1.0)
                w, h = x1 - x0, y1 - y0
                if bx1 - bx0 > w or by1 - by0 > h:                     # the head does not fit: widen about it
                    w = max(w, bx1 - bx0, (by1 - by0) * self.aspect * self.ph / self.pw)
                    x0, y0, x1, y1 = self.rect((x0 + x1) / 2, (y0 + y1) / 2, w)
                    w, h = x1 - x0, y1 - y0
                    moved = True
                dx = (bx0 - x0) if bx0 < x0 else (bx1 - x1) if bx1 > x1 else 0.0
                dy = (by0 - y0) if by0 < y0 else (by1 - y1) if by1 > y1 else 0.0
                if dx or dy:
                    x0, y0, x1, y1 = self.rect((x0 + x1) / 2 + dx, (y0 + y1) / 2 + dy, w)
                    moved = True
            if not moved:
                break
        return [x0, y0, x1, y1]

    def whole(self, crop, faces, margin=0.0):
        """Is every face this crop shows inside it? (The check a film is held to.)"""
        x0, y0, x1, y1 = crop
        for fx, fy, fw, fh in faces or ():
            cx, cy = fx + fw / 2, fy + fh / 2
            if x0 < cx < x1 and y0 < cy < y1:
                if fx - fw * margin < x0 - 1e-6 or fy - fh * margin < y0 - 1e-6 or fx + fw * (1 + margin) > x1 + 1e-6 or fy + fh * (1 + margin) > y1 + 1e-6:
                    return False
        return True

    def up(self, crop, out_w=1080):
        """Output pixels per source pixel at this crop."""
        return out_w / max((crop[2] - crop[0]) * self.pw, 1e-6)


def _cwz(crop):
    return (crop[0] + crop[2]) / 2, (crop[1] + crop[3]) / 2, crop[2] - crop[0]


def room(cam, crop):
    """How far this crop can travel inside its picture, in frame widths: (left, right, up, down)."""
    w, h = crop[2] - crop[0], crop[3] - crop[1]
    return crop[0] / w, (1 - crop[2]) / w, crop[1] / h / cam.aspect, (1 - crop[3]) / h / cam.aspect


def track(cam, n, crop, fps=30.0, drift=None, settle=None, to=None, whip_in=None, whip_out=None, faces=None):
    """n crops: the framing ``crop`` with its moves. All optional, and they add:

    drift     {"rate": scale per second, "dir": 1 in / -1 out, "pan": [x, y] crop widths per second}: one speed
              for the whole shot, as the references' slow pushes hold (their middle sits at 53% of the travel)
    settle    {"size", "tau", "dir"}: lands that far off its size and settles, exponentially, from frame 0
    to        {"crop", "at", "frames", "kind", "over", "back"}: an eased move to another framing, on a measured
              curve, starting at frame ``at``; held there after. A list of them is a camera that keeps re-framing
    whip_out  {"frames", "travel", "ang"}: the last frames run off along ``ang`` (radians, 0 is right), gathering speed
    whip_in   the same, arriving: the first frames come in along ``ang`` and brake
    faces     picture-fraction boxes: every frame keeps the faces it shows whole (a drift never slides a head out)
    """
    n = int(n)
    cx, cy, w = _cwz(crop)
    lz = [0.0] * n                       # log of the zoom on top of the framing
    ox, oy = [0.0] * n, [0.0] * n        # centre offset on screen, in frame widths (one crop width is one frame width)
    ky = cam.pw / cam.ph                 # a frame width, in picture heights, per crop width
    if drift:
        rate = math.log1p(float(drift.get("rate", bake.DRIFT_RATE[1]))) * (1 if drift.get("dir", 1) >= 0 else -1)
        pan = drift.get("pan") or (0.0, 0.0)
        # the push runs about the shot's middle when it goes out, so a pull never opens past the picture
        for i in range(n):
            lz[i] += rate * i / fps
            ox[i] += pan[0] * i / fps
            oy[i] += pan[1] * i / fps
    if settle:
        sgn = 1.0 if settle.get("dir", -1) >= 0 else -1.0
        tau = float(settle.get("tau", bake.CAM_SETTLE["tau"])) * fps / 30.0
        for i, v in enumerate(bake.cam_settle(n, settle.get("size"), tau, sgn)):
            lz[i] += v
    # one move or several in a row: each starts from where the last one ended
    fx_, fy_, fw_ = cx, cy, w
    for mv in ([to] if isinstance(to, dict) else list(to or ())):
        tx, ty, tw = _cwz(mv["crop"])
        fr = int(mv.get("frames") or round(bake.MOVE_FRAMES * fps / 30.0))
        over = float(mv.get("over", 0.0))
        k = bake.move(n, 0.0, 1.0, mv.get("kind", "out"), frames=fr, over=over, back=mv.get("back"), delay=int(mv.get("at", 0)))
        for i in range(n):
            lz[i] += math.log(fw_ / tw) * k[i]
            ox[i] += (tx - fx_) / w * k[i]
            oy[i] += (ty - fy_) / (w * ky) * k[i]
        fx_, fy_, fw_ = tx, ty, tw
    for wh, arriving in ((whip_in, True), (whip_out, False)):
        if not wh:
            continue
        fr = min(int(wh.get("frames") or bake.WHIP["frames"]), n)
        tr = float(wh.get("travel", bake.WHIP["travel_w"]))
        ang = float(wh.get("ang", 0.0))
        dx, dy = math.cos(ang) * tr, math.sin(ang) * tr
        for j in range(fr):
            if arriving:                 # comes in from behind its rest along the pan, braking
                k = 1.0 - bake._on(bake.MOVE["out"], (j + 1) / fr)
                i = j
                ox[i] -= dx * k
                oy[i] -= dy * k
            else:                        # leaves along the pan, gathering speed
                k = bake._on(bake.MOVE["in"], (j + 1) / fr)
                i = n - fr + j
                ox[i] += dx * k
                oy[i] += dy * k
    out = []
    for i in range(n):
        # a framing obeys the upscale limit; a move on it may pass the limit by 8%, so that no picture has to stand still
        wi = min(max(w / math.exp(lz[i]), cam.w_min * 0.92), cam.w_max)
        h = cam.h_of(wi)
        px_, py_ = cx + ox[i] * w, cy + oy[i] * w * ky
        px_, py_ = min(max(px_, wi / 2), 1 - wi / 2), min(max(py_, h / 2), 1 - h / 2)
        c = [px_ - wi / 2, py_ - h / 2, px_ + wi / 2, py_ + h / 2]
        if faces:
            c = cam.keep_whole(c, faces, margin=0.04)
        out.append([r5(v) for v in c])
    return out


def speed(crops):
    """Per frame, how far the picture travels on screen between this crop and the next: (pan as a share of the
    frame, scale change as a ratio - 1). What the shutter will blur, and what a test reads."""
    out = []
    for a, b in zip(crops[:-1], crops[1:]):
        ax, ay, aw = _cwz(a)
        bx, by, bw = _cwz(b)
        out.append((math.hypot((bx - ax) / aw, (by - ay) / max(a[3] - a[1], 1e-9)), aw / bw - 1.0))
    return out
