"""The free-space map a typeset item is solved in (ROADMAP 2A).

Everything is in design pixels on the solved canvas (W x H, y down). The map
combines what type must never touch and what it may only pass behind:

  face      the face zone (hair to below the chin): HARD for front type.
            The eyes-to-mouth core stays hard even for type set behind the
            subject — if the program matte is unusable at render, a behind
            item draws in front, and it must still never cover the face.
  subject   the person (head and torso prior from the face): type set
            behind passes here and is partly hidden (glyph_visible).
  watermark the free-tier corner mark's zone (HARD, every render).
  safe      the platform safe area (9:16: 6% sides, 6% top, below 0.80
            and the right button rail are out).
  captions  a band reserved for captions that keep running (optional).
  graphic   other graphics up during the item (HARD).

``regions()`` returns maximal empty rectangles (a free-space map, not a
fixed grid of slots) and a few named ones (above / below / left / right of
the face) the layout families start from.
"""

import math

GRID = 48                          # cells across the short edge
FACE_GROW = (0.12, 0.25, 0.12, 0.12)   # left, top (hair), right, bottom
                                        # (chin and neck) x face size
CORE_INSET = (0.12, 0.30, 0.12, 0.05)  # the eyes-to-mouth core
CLEAR = 0.015                      # clearance kept from a hard zone (x W)
# Talking-head prior when no face was measured near the moment: the
# caption_carry FACE_PRIOR (assuming a face only ever blocks a placement).
FACE_PRIOR_PORTRAIT = (0.30, 0.20, 0.70, 0.46)
FACE_PRIOR_LAND = (0.38, 0.14, 0.62, 0.56)


def portrait(W, H):
    return H / max(1.0, W) >= 1.6


def safe_box(W, H):
    if portrait(W, H):
        return (0.06 * W, 0.06 * H, 0.94 * W, 0.80 * H)
    return (0.045 * W, 0.07 * H, 0.955 * W, 0.93 * H)


def rail_box(W, H):
    if portrait(W, H):
        return (0.88 * W, 0.50 * H, W, 0.85 * H)
    return None


def _px(box, W, H):
    return (box[0] * W, box[1] * H, box[2] * W, box[3] * H)


def _grow(b, l, t, r, bt):
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[0] - l * w, b[1] - t * h, b[2] + r * w, b[3] + bt * h)


def inter(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


class Space:
    """The free-space map for one item on one canvas."""

    def __init__(self, W, H, faces=(), watermark=None, captions=None,
                 graphics=(), prior=True):
        self.W, self.H = float(W), float(H)
        self.safe = safe_box(self.W, self.H)
        self.hard = []            # (box, kind)
        self.faces_px = [_px(f, W, H) for f in faces or () if f]
        self.measured_face = bool(self.faces_px)
        if not self.faces_px and prior:
            self.faces_px = [_px(FACE_PRIOR_PORTRAIT if portrait(W, H)
                                 else FACE_PRIOR_LAND, W, H)]
        self.face_zones = [_grow(f, *FACE_GROW) for f in self.faces_px]
        self.cores = [_grow(f, *(-v for v in CORE_INSET))
                      for f in self.faces_px]
        self.subject = [self._subject(f) for f in self.faces_px]
        if watermark:
            self.hard.append((_px(watermark, W, H), "watermark"))
        rail = rail_box(self.W, self.H)
        if rail:
            self.hard.append((rail, "safe"))
        if captions:
            self.hard.append(((0.0, captions[0] * H, self.W,
                               captions[1] * H), "caption_band"))
        for g in graphics or ():
            self.hard.append((_px(g, W, H), "graphic"))

    def _subject(self, f):
        """Head + torso prior from a face box (px): the head ellipse's box
        and a widening torso down to the frame's bottom."""
        fw, fh = f[2] - f[0], f[3] - f[1]
        cx = (f[0] + f[2]) / 2
        head = _grow(f, 0.18, 0.45, 0.18, 0.2)
        neck_y = f[3] + 0.35 * fh
        torso = (cx - 1.25 * fw, neck_y + 0.25 * fh, cx + 1.25 * fw, self.H)
        neck = (cx - 0.42 * fw, f[3], cx + 0.42 * fw, neck_y + 0.3 * fh)
        return (head, neck, torso)

    # ── tests a layout runs ──────────────────────────────────────────────

    def in_safe(self, box, tol=1.0):
        s = self.safe
        return (box[0] >= s[0] - tol and box[1] >= s[1] - tol and
                box[2] <= s[2] + tol and box[3] <= s[3] + tol)

    def hard_hits(self, box, depth="front"):
        """Kinds of hard zones ``box`` touches (with the clearance)."""
        c = CLEAR * self.W
        g = (box[0] - c, box[1] - c, box[2] + c, box[3] + c)
        hits = [k for b, k in self.hard if inter(g, b) > 0]
        zones = self.cores if depth == "behind" else self.face_zones
        if any(inter(g, z) > 0 for z in zones):
            hits.append("face")
        return hits

    def head_hit(self, box, depth="front"):
        """Does ``box`` touch the head as an ELLIPSE (the zone's inscribed
        ellipse grown by the clearance)? Path text round the head is held to
        the head's shape, not its bounding rectangle's corners."""
        c = CLEAR * self.W
        zones = self.cores if depth == "behind" else self.face_zones
        for z in zones:
            ell = (z[0] - c, z[1] - c, z[2] + c, z[3] + c)
            if _ellipse_cover(box, ell) > 0:
                return True
        return False

    def subject_share(self, box):
        """Share of ``box`` the subject prior covers (0..1): how much of a
        word set behind the subject is hidden."""
        a = area(box)
        if a <= 0:
            return 0.0
        cov = 0.0
        for head, neck, torso in self.subject:
            cov += _ellipse_cover(box, head) + inter(box, neck) + \
                inter(box, torso)
        return min(1.0, cov / a)

    def clearance(self, box):
        """Smallest gap (px) from ``box`` to a face zone or hard zone."""
        best = math.inf
        for z in self.face_zones + [b for b, _k in self.hard]:
            dx = max(z[0] - box[2], box[0] - z[2], 0.0)
            dy = max(z[1] - box[3], box[1] - z[3], 0.0)
            best = min(best, math.hypot(dx, dy) if dx and dy else dx + dy)
        return best

    # ── regions ─────────────────────────────────────────────────────────

    def _grid(self, depth):
        cell = min(self.W, self.H) / GRID
        nx, ny = int(math.ceil(self.W / cell)), int(math.ceil(self.H / cell))
        s = self.safe
        free = [[True] * nx for _ in range(ny)]
        zones = [b for b, _k in self.hard]
        zones += self.cores if depth == "behind" else self.face_zones
        c = CLEAR * self.W
        for j in range(ny):
            y0, y1 = j * cell, (j + 1) * cell
            for i in range(nx):
                x0, x1 = i * cell, (i + 1) * cell
                if x0 < s[0] - 1 or x1 > s[2] + 1 or y0 < s[1] - 1 or \
                        y1 > s[3] + 1:
                    free[j][i] = False
                    continue
                for z in zones:
                    if x1 > z[0] - c and x0 < z[2] + c and y1 > z[1] - c \
                            and y0 < z[3] + c:
                        free[j][i] = False
                        break
        return free, cell

    def regions(self, depth="front", min_frac=0.04, top=8):
        """Maximal empty rectangles (px boxes), largest first, each at
        least ``min_frac`` of the safe area."""
        free, cell = self._grid(depth)
        ny, nx = len(free), len(free[0]) if free else 0
        heights = [0] * nx
        found = []
        for j in range(ny):
            for i in range(nx):
                heights[i] = heights[i] + 1 if free[j][i] else 0
            stack = []
            for i in range(nx + 1):
                h = heights[i] if i < nx else 0
                start = i
                while stack and stack[-1][1] >= h:
                    s_i, s_h = stack.pop()
                    if s_h > 0:
                        found.append((s_i, j - s_h + 1, i, j + 1))
                    start = s_i
                stack.append((start, h))
        sa = area(self.safe)
        out = []
        for i0, j0, i1, j1 in found:
            b = (i0 * cell, j0 * cell, min(self.W, i1 * cell),
                 min(self.H, j1 * cell))
            if area(b) >= min_frac * sa:
                out.append(b)
        out.sort(key=area, reverse=True)
        kept = []
        for b in out:
            if any(inter(b, k) >= 0.85 * area(b) for k in kept):
                continue
            kept.append(b)
            if len(kept) >= top:
                break
        return kept

    def named(self, depth="front"):
        """{name: px box} of the four regions around the (first) face,
        clipped to the safe area; empty names are left out."""
        if not self.face_zones:
            return {"full": self.safe}
        s = self.safe
        z = (self.cores if depth == "behind" else self.face_zones)[0]
        c = CLEAR * self.W
        out = {
            "above": (s[0], s[1], s[2], z[1] - c),
            "below": (s[0], z[3] + c, s[2], s[3]),
            "left": (s[0], s[1], z[0] - c, s[3]),
            "right": (z[2] + c, s[1], s[2], s[3]),
        }
        return {k: b for k, b in out.items()
                if b[2] - b[0] > 0.12 * self.W and b[3] - b[1] > 0.04 * self.H}

    def keepouts_used(self):
        out = [{"kind": "face", "box": [round(v, 1) for v in z]}
               for z in self.face_zones]
        out += [{"kind": k, "box": [round(v, 1) for v in b]}
                for b, k in self.hard]
        out.append({"kind": "safe", "box": [round(v, 1) for v in self.safe]})
        return out


def _ellipse_cover(box, ell):
    """Area of ``box`` inside the ellipse inscribed in ``ell`` (sampled)."""
    x0, y0 = max(box[0], ell[0]), max(box[1], ell[1])
    x1, y1 = min(box[2], ell[2]), min(box[3], ell[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    cx, cy = (ell[0] + ell[2]) / 2, (ell[1] + ell[3]) / 2
    rx, ry = (ell[2] - ell[0]) / 2, (ell[3] - ell[1]) / 2
    n, inside = 8, 0
    for a in range(n):
        for b in range(n):
            px = x0 + (a + 0.5) * (x1 - x0) / n
            py = y0 + (b + 0.5) * (y1 - y0) / n
            if ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2 <= 1.0:
                inside += 1
    return (x1 - x0) * (y1 - y0) * inside / (n * n)
