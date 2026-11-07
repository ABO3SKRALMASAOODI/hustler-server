"""Joined-script type and film-made sounds for the lowering, with no GL.

A title in Arabic or Persian has no libass font today, so it goes in as a still shaped by
HarfBuzz and filled by exact area coverage. Echo and swell are made from the film's own
recorded audio. Both are the Mac slice's engine code, without its window or its shaders.
"""
import math
import os
import subprocess

import numpy as np

import score.compiler as compiler

SR = 48000
FF = os.environ.get("FFMPEG_BIN", "ffmpeg")


def coverage(lines, w, h):
    """Exact area coverage of closed outlines, as a signed accumulation buffer. lines: Nx4 float,
    pixels, y down. Each edge is split at every row and column it crosses, so every piece lies in
    one cell and adds its true area. Overlapping contours of a variable font stay solid."""
    A_ = np.zeros((h + 2, w + 3), np.float64)
    x0, y0, x1, y1 = lines.T
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    sign = np.where(y1 > y0, 1.0, -1.0)
    flip = y1 < y0
    x0, x1 = np.where(flip, x1, x0), np.where(flip, x0, x1)
    y0, y1 = np.where(flip, y1, y0), np.where(flip, y0, y1)
    r0 = np.floor(y0).astype(np.int64)
    n = np.ceil(y1).astype(np.int64) - r0
    idx = np.repeat(np.arange(len(x0)), n)
    row = r0[idx] + (np.arange(len(idx)) - np.repeat(np.cumsum(n) - n, n))
    dxdy = (x1 - x0) / (y1 - y0)
    ya, yb = np.maximum(row, y0[idx]), np.minimum(row + 1, y1[idx])
    xa, xb = x0[idx] + (ya - y0[idx]) * dxdy[idx], x0[idx] + (yb - y0[idx]) * dxdy[idx]
    dy = (yb - ya) * sign[idx]
    xl, xr = np.minimum(xa, xb), np.maximum(xa, xb)
    c0 = np.floor(xl).astype(np.int64)
    m = np.maximum(np.ceil(xr).astype(np.int64) - c0, 1)
    j = np.repeat(np.arange(len(xl)), m)
    col = c0[j] + (np.arange(len(j)) - np.repeat(np.cumsum(m) - m, m))
    left, right = np.maximum(col, xl[j]), np.minimum(col + 1, xr[j])
    span = (xr - xl)[j]
    frac = np.where(span > 1e-12, (right - left) / np.maximum(span, 1e-12), 1.0)
    d = dy[j] * frac
    xm = np.clip(0.5 * (left + right) - col, 0, 1)
    rr = row[j]
    np.add.at(A_, (rr, col), d * (1 - xm))
    np.add.at(A_, (rr, col + 1), d * xm)
    return np.clip(np.abs(np.cumsum(A_, axis=1)), 0, 1)[:h, :w].astype(np.float32)


class Pen:
    """Collects an outline from HarfBuzz, flattened to lines at a given pixel scale."""

    def __init__(self, scale):
        self.scale, self.out, self.start, self.p = scale, [], None, None

    def moveTo(self, p):
        self.closePath()
        self.start = self.p = p

    def lineTo(self, p):
        self.out.append((*self.p, *p))
        self.p = p

    def qCurveTo(self, c, p):
        a = self.p
        dev = math.hypot(a[0] - 2 * c[0] + p[0], a[1] - 2 * c[1] + p[1]) * self.scale
        n = max(2, min(40, int(math.ceil(math.sqrt(dev) * 1.6))))
        q = a
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            r = (u * u * a[0] + 2 * u * t * c[0] + t * t * p[0], u * u * a[1] + 2 * u * t * c[1] + t * t * p[1])
            self.out.append((*q, *r))
            q = r
        self.p = p

    def curveTo(self, c1, c2, p):
        a = self.p
        dev = max(math.hypot(a[0] - 2 * c1[0] + c2[0], a[1] - 2 * c1[1] + c2[1]),
                  math.hypot(c1[0] - 2 * c2[0] + p[0], c1[1] - 2 * c2[1] + p[1])) * self.scale
        n = max(3, min(48, int(math.ceil(math.sqrt(dev) * 2.2))))
        q = a
        for i in range(1, n + 1):
            t = i / n
            u = 1 - t
            r = tuple(u * u * u * a[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t * t * t * p[k] for k in (0, 1))
            self.out.append((*q, *r))
            q = r
        self.p = p

    def closePath(self):
        if self.p is not None and self.start is not None and self.p != self.start:
            self.out.append((*self.p, *self.start))
        self.p = self.start = None

    def endPath(self):
        self.closePath()



class Face:
    """One font file: shaping and outlines. Pure, cached."""
    _all = {}

    @classmethod
    def get(cls, font):
        if font not in cls._all:
            cls._all[font] = cls(compiler.font_path(font))
        return cls._all[font]

    def __init__(self, path):
        import uharfbuzz as hb
        self.hb = hb
        self.face = hb.Face(hb.Blob.from_file_path(path))
        self.font = hb.Font(self.face)
        self.upem = float(self.face.upem)
        self.glyphs = {}

    def shape(self, text):
        hb = self.hb
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {"kern": True, "liga": True, "clig": True})
        out, x = [], 0.0
        for g, p in zip(buf.glyph_infos, buf.glyph_positions):
            out.append((g.codepoint, x + p.x_offset, p.y_offset))
            x += p.x_advance
        return out, x

    def lines(self, gid, px):
        k = (gid, round(px, 2))
        if k not in self.glyphs:
            s = px / self.upem
            pen = Pen(s)
            self.font.draw_glyph_with_pen(gid, pen)
            pen.closePath()
            self.glyphs[k] = np.array(pen.out, np.float64).reshape(-1, 4) * s
        return self.glyphs[k]

    def outline(self, text, px):
        glyphs, adv = self.shape(text)
        s = px / self.upem
        parts = []
        for gid, x, y in glyphs:
            ln = self.lines(gid, px)
            if len(ln):
                ln = ln.copy()
                ln[:, [0, 2]] += x * s
                ln[:, [1, 3]] = -(ln[:, [1, 3]] + y * s)
                parts.append(ln)
        return (np.vstack(parts) if parts else np.zeros((0, 4))), adv * s


def pcm(path):
    """Any file to stereo float at the mix rate. Empty when it has no audio."""
    cmd = [FF, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", path, "-map", "0:a:0?", "-vn",
           "-ac", "2", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    return np.frombuffer(r.stdout, np.float32).reshape(-1, 2).copy()


def dry_spine(l1, src):
    """The film's own audio in programme order, for a sound made out of it."""
    fps = float(l1["fps"])
    n = int(round(l1["dur_f"] / fps * SR))
    mix = np.zeros((n, 2), np.float32)
    pc = pcm(src)
    for e in l1.get("spine") or []:
        at = int(round(e["f"][0] / fps * SR))
        to = min(int(round(e["f"][1] / fps * SR)), n)
        s0 = int(round(e["src"][0] * SR))
        seg = pc[s0:s0 + (to - at)]
        mix[at:at + len(seg)] += seg
    return mix


def voice_layer(dry, s, fps):
    """A sound made from the film's own recorded audio. echo: the last moment before the event throws back into the gap after it,
    side to side, darker each time. swell: the moment after it, reversed, rising into the event. Returns (first sample, samples)."""
    at = int(round(s["event_f"] / fps * SR))
    d = max(int(float(s.get("d_s", 0.7)) * SR), 64)
    gain = 10 ** (float(s.get("db", -8)) / 20)
    if s["s"] == "swell":
        seg = dry[at:at + d][::-1].copy()
        if not len(seg):
            return at, seg
        seg *= (np.linspace(0, 1, len(seg), dtype=np.float32) ** 2)[:, None]
        return at - len(seg), seg * gain
    seg = dry[max(0, at - d):at].copy()
    if not len(seg):
        return at, seg
    fade = min(int(0.02 * SR), len(seg) // 2)
    seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)[:, None]
    seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
    n, gap = max(int(s.get("n", 3)), 1), int(float(s.get("gap_s", 0.26)) * SR)
    out = np.zeros((len(seg) + gap * n, 2), np.float32)
    tap = seg
    for k in range(1, n + 1):
        kern = np.ones(1 + 6 * k, np.float32) / (1 + 6 * k)     # darker each throw, as a tape echo
        wet = np.stack([np.convolve(tap[:, c], kern, mode="same") for c in (0, 1)], axis=1) * (0.62 ** (k - 1))
        wet[:, 0] *= 1.0 if k % 2 else 0.45
        wet[:, 1] *= 0.45 if k % 2 else 1.0
        o = gap * (k - 1)
        out[o:o + len(wet)] += wet
    return at, out * gain
