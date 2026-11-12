"""E2 and E4: the GLSL runtime. One GL context and the draw stages of DESIGN 4.4 that a plan reaches.

The shaders are written in the subset GLSL ES 3.00 and GLSL 3.30 share, and nothing here asks for
a platform: a Metal-backed context on the Mac and EGL on llvmpipe on the box take the same calls.
``VALMERA_SCENE_GL`` picks the context: ``auto`` (EGL on Linux, the default context elsewhere),
``egl`` or ``default``. ``LP_NUM_THREADS`` is left alone.

Ops, as DESIGN 4.3 names them: src and fit (a shot's picture through its crop), grade, treat, plate,
blit (cards, and type from the atlas), flash, join, finish (lens, raster, vignette, grain, mesh), out.
"""
import collections
import hashlib
import math
import os
import sys
import time

import numpy as np

from .faults import SceneFault
from .plan import A, AR, BLENDS, IDENTITY, JOINS, MODES, PLATES, TREATS, held, hex3

GLSL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glsl")
HEADER = {"core": "#version 330 core\n",
          "es": "#version 300 es\nprecision highp float;\nprecision highp int;\nprecision highp sampler2D;\n"}
PROGRAMS = {"pic": ("common", "easu", "picture", "pic.frag"), "join": ("common", "easu", "picture", "join.frag"),
            "shadow": ("shadow.frag",), "flash": ("common", "flash.frag"), "film": ("common", "film.frag"),
            "word": ("common", "word.frag"), "out": ("out.frag",), "bright": ("bright.frag",)}
STRETCH = {"left": 0, "right": 1, "up": 2, "down": 3}


def make_context():
    """A standalone GL context for this machine, or SceneFault('gl')."""
    try:
        import moderngl
    except Exception as e:      # noqa: BLE001
        raise SceneFault("gl", f"moderngl is not installed: {e}")
    mode = os.environ.get("VALMERA_SCENE_GL", "auto")
    if mode == "auto":
        mode = "egl" if sys.platform.startswith("linux") else "default"
    try:
        if mode == "egl":
            return moderngl.create_standalone_context(require=330, backend="egl")
        return moderngl.create_standalone_context(require=330)
    except Exception as e:      # noqa: BLE001
        raise SceneFault("gl", f"no GL context ({mode}): {e}")


def _src(name):
    return open(os.path.join(GLSL, name + ".glsl"), encoding="utf-8").read()


class Runtime:
    def __init__(self, plan, ctx=None):
        import moderngl
        self.mgl = moderngl
        self.plan = plan
        self.W, self.H = plan.W, plan.H
        self.ctx = ctx or make_context()
        ctx = self.ctx
        self.gl = ctx.info.get("GL_RENDERER")
        self.gl_version = ctx.info.get("GL_VERSION")
        head = HEADER["es" if "OpenGL ES" in str(self.gl_version) else "core"]
        vs = head + _src("quad.vert")
        self.vbo = ctx.buffer(np.array([0, 0, 1, 0, 0, 1, 1, 1], "f4").tobytes())
        self.P = {}
        for name, parts in PROGRAMS.items():          # every shader compiles before the first frame
            try:
                p = ctx.program(vertex_shader=vs, fragment_shader=head + "".join(_src(x) for x in parts))
            except Exception as e:      # noqa: BLE001
                raise SceneFault("gl", f"shader {name} does not compile on {self.gl}: {str(e)[:600]}")
            self.P[name] = (p, ctx.simple_vertex_array(p, self.vbo, "p"))
        size = (self.W, self.H)

        def target(dtype, mips=False):
            t = ctx.texture(size, 4, dtype=dtype)
            t.repeat_x = t.repeat_y = False
            t.filter = (moderngl.LINEAR, moderngl.LINEAR)
            if mips:
                t.build_mipmaps()
            return t, ctx.framebuffer([t])
        try:
            self.comp, self.comp_fb = target("f2")
            self.bg, self.bg_fb = target("f2", mips=True)
            self.final, self.final_fb = target("f1")
            self.hi = ctx.texture((max(self.W // 4, 1), max(self.H // 4, 1)), 4, dtype="f2")
            self.hi.repeat_x = self.hi.repeat_y = False
            self.hi.build_mipmaps()
            self.hi_fb = ctx.framebuffer([self.hi])
        except Exception as e:      # noqa: BLE001
            raise SceneFault("gl", f"no float render target on {self.gl}: {e}")
        rng = np.random.RandomState(plan.seed ^ 0x5EED)
        self.noise_tex = ctx.texture((1024, 1024), 1, self.noise_tile(rng).tobytes(), dtype="f2")
        self.noise_tex.filter = (moderngl.NEAREST, moderngl.NEAREST)
        self.atlas = []
        for pg in plan.pages:
            t = ctx.texture((pg.shape[1], pg.shape[0]), 4, pg.tobytes(), alignment=1)
            t.repeat_x = t.repeat_y = False
            t.build_mipmaps()
            self.atlas.append(t)
        self.mask_tex = None
        if plan.masks is not None:
            m = (plan.masks.astype(np.float32) / 65535.0).astype("f2")
            self.mask_tex = ctx.texture((m.shape[1], m.shape[0]), 1, m.tobytes(), dtype="f2", alignment=1)
            self.mask_tex.repeat_x = self.mask_tex.repeat_y = False
            self.mask_tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.pics = {}
        self.reader = None
        self.t = collections.Counter()
        self.n = collections.Counter()
        self.drop = set()                  # kinds left out, so a check can draw the film without its flashes
        self.src_sum, self.src_n = 0.0, 0

    @staticmethod
    def noise_tile(rng):
        """Periodic gaussian noise, gently band-limited so grain has a size. Baked and seeded, so
        every tier draws the same grain."""
        n = rng.standard_normal((1024, 1024))
        f = np.fft.fft2(n)
        fy = np.fft.fftfreq(1024)[:, None]
        fx = np.fft.fftfreq(1024)[None, :]
        s = 0.62
        f *= np.exp(-2 * (math.pi * s) ** 2 * (fx * fx + fy * fy))
        n = np.fft.ifft2(f).real
        n /= n.std()
        return n.astype("f2")

    def U(self, prog, **kw):
        for k, v in kw.items():
            if k in prog:
                prog[k].value = v

    def rect_uniforms(self, prog, x=0, y=0, w=None, h=None):
        self.U(prog, uRect=(x, y, self.W if w is None else w, self.H if h is None else h), uFrame=(self.W, self.H))

    def note(self, msg):
        if msg not in self.plan.warn:
            self.plan.warn.append(msg)

    def tick(self, stage, t0):
        t1 = time.perf_counter()
        self.t[stage] += t1 - t0
        self.n[stage] += 1
        return t1

    # src ---------------------------------------------------------

    def picture(self, it, f):
        """The texture of this item's picture on this frame, uploaded once."""
        from PIL import Image, ImageOps
        aid = it.get("pic")
        a = self.plan.assets.get(aid)
        if a is None:
            return None
        if a.get("kind") == "video":
            n = self.plan.src_frame(it, f)
            slots = self.pics.setdefault(aid, [])
            for s in slots:
                if s["n"] == n:
                    s["used"] = f
                    return s
            if len(slots) < 3:
                t = self.ctx.texture((a["w"], a["h"]), 4)
                t.repeat_x = t.repeat_y = False
                t.build_mipmaps()
                s = dict(tex=t, w=a["w"], h=a["h"], alpha=False, n=-1, used=f, mips=False)
                slots.append(s)
            else:
                s = min(slots, key=lambda q: q["used"])
            b = self.reader.get((aid, n))
            s["tex"].write(b)
            self.src_sum += float(np.frombuffer(b, np.uint8)[::4099].mean())
            self.src_n += 1
            s.update(n=n, used=f, mips=False)
            return s
        if aid not in self.pics:
            from .reader import MAX_PIC
            im = ImageOps.exif_transpose(Image.open(a["abs"]))
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA")
            if max(im.size) > MAX_PIC:
                k = MAX_PIC / max(im.size)
                im = im.resize((max(2, int(im.width * k)), max(2, int(im.height * k))), Image.LANCZOS)
            arr = np.asarray(im, np.uint8).copy()
            if has_alpha:
                al = arr[..., 3:4].astype(np.float32) / 255.0
                arr[..., :3] = (arr[..., :3] * al + 0.5).astype(np.uint8)
            t = self.ctx.texture((arr.shape[1], arr.shape[0]), 4, arr.tobytes(), alignment=1)
            t.repeat_x = t.repeat_y = False
            t.build_mipmaps()
            self.pics[aid] = dict(tex=t, w=arr.shape[1], h=arr.shape[0], alpha=has_alpha, n=0, used=f, mips=True)
            self.src_sum += float(arr[..., :3].reshape(-1)[::4099].mean())
            self.src_n += 1
        return self.pics[aid]

    def matte_tex(self, shot, f):
        """The person's matte for this shot and frame, uploaded once per frame of the matte."""
        n = self.plan.matte_frame(shot, f)
        a = self.plan.assets["matte"]
        s = self.pics.get("matte")
        if s is None:
            t = self.ctx.texture((a["w"], a["h"]), 4)
            t.repeat_x = t.repeat_y = False
            t.filter = (self.mgl.LINEAR, self.mgl.LINEAR)
            s = self.pics["matte"] = dict(tex=t, n=-1)
        if s["n"] != n:
            s["tex"].write(self.reader.get(("matte", n)))
            s["n"] = n
        return s["tex"]

    @staticmethod
    def cover(tw, th, w, h):
        """Default crop when none is given: centred cover."""
        ta, ra = tw / th, w / h
        if ta > ra:
            e = (1 - ra / ta) / 2
            return (e, 0.0, 1 - e, 1.0)
        e = (1 - ta / ra) / 2
        return (0.0, e, 1.0, 1 - e)

    # fit, grade, treat: the uniforms every picture shares ---------

    def picture_uniforms(self, p, it, f, pic, crop, out_w, blur):
        """Crop, sampling, treat and grade for one picture drawn ``out_w`` frame pixels wide."""
        treat = it.get("treat") or {}
        grade = {**IDENTITY, **(it.get("grade") or self.plan.film.get("grade") or {})}
        mode = TREATS.get(treat.get("mode"), 0)
        tp = (float(treat.get("zoom", 1.0)), 0.0, 0.0, 0.0)
        if mode == 26:
            tp = (float(treat.get("origin", 0.5)), float(STRETCH.get(treat.get("direction", "left"), 0)), 0.0, 0.0)
        self.U(p, uG0=(grade["lift"], grade["gamma"], grade["gain"]), uG1=(grade["sat"], grade["contrast"], grade["temp"]),
               uTint=grade["tint"], uBlur=blur, uTreat=mode, uTreatC=hex3(treat.get("c")), uTreatHasC=1 if treat.get("c") else 0,
               uTreatTh=float(treat.get("th", 0.5)), uTreatP=tp, uTex=0, uNoise=1)
        if pic is None:
            self.U(p, uTexSize=(1, 1), uCrop=(0, 0, 1, 1), uSharp=0.0, uAlpha=0, uEdge=0)
            return
        pix_in = (crop[2] - crop[0]) * pic["w"]
        up = out_w / max(pix_in, 1.0)
        if (up < 0.95 or blur > 0.01 or mode >= 20) and not pic.get("mips"):
            pic["tex"].build_mipmaps()
            pic["mips"] = True
        edge = up >= 1.25 and not self.plan.film.get("no_easu")
        sharp = float(self.plan.film.get("sharpen", min(max((up - 1.15) / 1.4, 0.0), 1.0) * (0.35 if edge else 1.0)))
        self.U(p, uTexSize=(pic["w"], pic["h"]), uCrop=tuple(crop), uSharp=sharp, uAlpha=1 if pic.get("alpha") else 0,
               uEdge=1 if edge else 0)

    # blit: shot and card -----------------------------------------

    def draw_pic(self, it, f, geom, matte=None):
        p, vao = self.P["pic"]
        lo = it["f"][0]
        cx, cy, hw, hh, s, rot, op, radius = geom
        blur = float(A(it.get("blur", 0) or 0, f, lo)) * self.W / 1080.0
        self.U(p, uCentre=(cx, cy), uHalf=(hw, hh), uScale=s, uRot=(math.cos(rot), math.sin(rot)), uRadius=radius, uOp=op,
               uNoiseAt=self.plan.noise_at(f))
        b = it.get("border") or {}
        self.U(p, uBorder=float(b.get("w", 0)) * self.W, uBorderC=hex3(b.get("c")))
        if it.get("pic") == "plate" or self.plan.assets.get(it.get("pic")) is None:
            if it.get("pic") != "plate":
                self.note(f"no picture for {it['id']}: drew a plain plate")
            pl = it.get("plate") or {"c0": "#101010", "c1": "#0A0A0A"}
            self.picture_uniforms(p, it, f, None, None, 0, blur)
            self.plate_uniforms(p, it["id"], pl, f, lo)
            self.U(p, uUseMatte=0)
            self.noise_tex.use(0)                  # a plate samples no picture, but unit 0 must hold a texture
            self.noise_tex.use(1)
            self.noise_tex.use(2)
            self.quad_scissored("pic", cx, cy, hw, hh, s, rot)
            return
        pic = self.picture(it, f)
        crop = AR(it.get("crop"), f, lo) or self.cover(pic["w"], pic["h"], hw, hh)
        self.picture_uniforms(p, it, f, pic, crop, 2 * hw * s, blur)
        self.U(p, uPlate=0, uUseMatte=1 if matte is not None else 0, uMatte=2)
        pic["tex"].use(0)
        self.noise_tex.use(1)
        (matte if matte is not None else self.noise_tex).use(2)     # a sampler needs a texture on its unit even when unread
        self.quad_scissored("pic", cx, cy, hw, hh, s, rot)

    def plate_uniforms(self, p, item_id, pl, f, lo):
        kind = PLATES.get(pl.get("kind", "paper"), 1)
        sd = int(hashlib.md5(str(item_id).encode()).hexdigest()[:6], 16)
        if kind == 2:
            # static: blocks of cell px at 1080 wide that move `speed` px a frame, down when positive
            k = self.W / 1080.0
            cell = max(round(float(pl.get("cell", 12)) * k), 1.0)
            self.U(p, uPlate=2, uC0=hex3(pl.get("c0"), (0.0, 0.0, 0.0)), uC1=hex3(pl.get("c1"), (1.0, 1.0, 1.0)),
                   uStatic=(cell, round(float(pl.get("speed", 12)) * k) * (f - lo), float(int(pl.get("seed", self.plan.seed)) % 65521)))
            return
        # a page is lit, never flat: the light sits off centre and drifts a little, seeded per plate
        lx = 0.28 + (sd % 97) / 97 * 0.44 + (f - lo) * 0.0011 * (1 if sd & 1 else -1)
        ly = 0.24 + ((sd >> 7) % 89) / 89 * 0.3
        self.U(p, uLight=(lx, ly), uPlate=1, uC0=hex3(pl.get("c0")), uC1=hex3(pl.get("c1") or pl.get("c0")),
               uPlateGrain=float(pl.get("grain", 0)), uNoiseAt=(sd & 1023, (sd >> 10) & 1023))

    def quad_scissored(self, name, cx, cy, hw, hh, s=1.0, rot=0.0, grow=0.0):
        """Full-frame quad, scissored to the item's bounds, so a small card costs a small fill."""
        p, vao = self.P[name]
        c, sn = abs(math.cos(rot)), abs(math.sin(rot))
        ex = (hw * c + hh * sn) * s + grow + 2
        ey = (hw * sn + hh * c) * s + grow + 2
        x0, y0 = max(int(cx - ex), 0), max(int(cy - ey), 0)
        x1, y1 = min(int(math.ceil(cx + ex)), self.W), min(int(math.ceil(cy + ey)), self.H)
        if x1 <= x0 or y1 <= y0:
            return
        self.rect_uniforms(p)
        self.ctx.scissor = (x0, y0, x1 - x0, y1 - y0)
        vao.render(self.mgl.TRIANGLE_STRIP)
        self.ctx.scissor = None

    def draw_shot(self, it, f, matte=None):
        self.draw_pic(it, f, (self.W / 2, self.H / 2, self.W / 2, self.H / 2, 1.0, 0.0, 1.0, 0.0), matte=matte)

    def draw_card(self, it, f):
        lo = it["f"][0]
        W, H = self.W, self.H
        cx, cy = float(A(it.get("x", 0.5), f, lo)) * W, float(A(it.get("y", 0.5), f, lo)) * H
        hw, hh = float(it.get("w", 0.5)) * W / 2, float(it.get("h", 0.5)) * H / 2
        s = float(A(it.get("s", 1), f, lo))
        rot = math.radians(float(A(it.get("rot", 0) or 0, f, lo)))
        op = float(A(it.get("op", 1), f, lo))
        radius = min(float(it.get("radius", 0) or 0) * W, min(hw, hh))
        if op <= 0 or s <= 0:
            return
        sh = it.get("shadow")
        if sh and float(sh.get("a", 0)) > 0:
            p, vao = self.P["shadow"]
            sigma = max(float(sh.get("blur", 0.01)) * W, 0.5)
            sx, sy = cx + float(sh.get("dx", 0)) * W, cy + float(sh.get("dy", 0)) * W
            self.U(p, uCentre=(sx, sy), uHalf=(hw * s, hh * s), uRadius=max(radius * s, 0.01), uSigma=sigma,
                   uA=float(sh["a"]) * op, uRot=(math.cos(rot), math.sin(rot)))
            self.quad_scissored("shadow", sx, sy, hw, hh, s, rot, grow=sigma * 3.2)
        self.draw_pic(it, f, (cx, cy, hw, hh, s, rot, op, radius))

    # flash and join: they read the frame ---------------------------

    def snapshot(self, mips=False):
        self.ctx.copy_framebuffer(self.bg_fb, self.comp_fb)
        if mips:
            self.bg.build_mipmaps()

    def draw_flash(self, it, f):
        lo = it["f"][0]
        W, H = self.W, self.H
        p, vao = self.P["flash"]
        r = AR(it.get("rect"), f, lo) or (0, 0, 1, 1)
        x0, y0 = int(round(r[0] * W)), int(round(r[1] * H))
        x1, y1 = int(round(r[2] * W)), int(round(r[3] * H))
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
        if x1 <= x0 or y1 <= y0:
            return
        mode = MODES.get(it.get("mode"), 0)
        if mode == 0:
            self.note(f"unknown flash mode {it.get('mode')}")
            return
        a = float(A(it.get("a", 1), f, lo))
        self.snapshot(mips=mode in (20, 22))
        self.bg.use(0)
        self.noise_tex.use(1)
        self.U(p, uBg=0, uNoise=1, uMode=mode, uC=hex3(it.get("c"), (0.0, 0.0, 0.0) if mode == 11 else (1.0, 1.0, 1.0)),
               uHasC=1 if it.get("c") else 0, uA=a, uTh=float(A(it.get("th", 0.5), f, lo)),
               uCell=max(float(it.get("cell", 16)) * W / 1080.0, 1.0), uBox=(x0, y0, x1, y1), uKey=hex3(it.get("key")),
               uNoiseAt=self.plan.noise_at(f))
        self.rect_uniforms(p)
        self.ctx.scissor = (x0, y0, x1 - x0, y1 - y0)
        vao.render(self.mgl.TRIANGLE_STRIP)
        self.ctx.scissor = None

    def draw_join(self, it, f):
        """Two pictures into one: this item's picture over the frame as drawn, through a mask."""
        lo = it["f"][0]
        W, H = self.W, self.H
        p, vao = self.P["join"]
        pic = self.picture(it, f)
        if pic is None:
            self.note(f"no picture for {it['id']}: junction not drawn")
            return
        mode = JOINS[it["mode"]]
        crop = AR(it.get("crop"), f, lo) or self.cover(pic["w"], pic["h"], W, H)
        self.picture_uniforms(p, it, f, pic, crop, W, float(A(it.get("blur", 0) or 0, f, lo)) * W / 1080.0)
        k = W / 1080.0
        J = (0.0, 0.0, 0.0, 0.0)
        if mode == 1:
            e = it.get("edge") or {}
            J = (float(A(it.get("x", 0.0), f, lo)) * W, math.tan(math.radians(float(it.get("lean", 6.0)))),
                 float(e.get("w", 1.3)) * k, float(e.get("a", 0.85)))
        elif mode == 2:
            J = (max(float(it.get("pitch", 0.1)) * W, 2.0), min(max(float(A(it.get("t", 0.5), f, lo)), 0.0), 1.0),
                 float(it.get("falloff", 0.55)), float(it.get("shadow", 0.3)))
        elif mode == 3:
            m = self.plan.mask_meta[it["mask"]]
            v = self.plan.voices.get(it.get("v")) or {}
            s0 = float(v.get("px", 58)) / m["px"]                 # mask texels to frame pixels at scale 1
            s = s0 * float(A(it.get("s", 1.0), f, lo))
            X, Y = float(it.get("x0", 0.5)) * W, float(it.get("y0", 0.5)) * H    # the glyph's pen origin on the frame
            gx, gy = X + m["cx"] * s0, Y + m["cy"] * s0           # its centroid: the point it grows from
            rx, ry = gx + (m["x0"] - m["cx"]) * s, gy + (m["y0"] - m["cy"]) * s
            sw, sh_ = self.plan.masks.shape[1], self.plan.masks.shape[0]
            J = (float(it.get("noise", 0.0)) * k, 0.0, 0.0, 0.0)
            self.U(p, uMR=(rx, ry, m["w"] * s, m["h"] * s), uMUV=(m["ax"] / sw, 0.0, m["w"] / sw, m["h"] / sh_),
                   uMSpread=m["spread"] * s)
        self.snapshot()
        self.bg.use(3)
        pic["tex"].use(0)
        self.noise_tex.use(1)
        (self.mask_tex if self.mask_tex is not None else self.noise_tex).use(2)
        self.U(p, uBg=3, uMask=2, uJoin=mode, uJ=J, uDim=float(A(it.get("dim", 1.0), f, lo)), uA=float(A(it.get("a", 1.0), f, lo)),
               uNoiseAt=self.plan.noise_at(f))
        self.rect_uniforms(p)
        vao.render(self.mgl.TRIANGLE_STRIP)

    # blit: type from the atlas -----------------------------------

    def draw_word(self, it, f):
        lo = it["f"][0]
        W, H = self.W, self.H
        p, vao = self.P["word"]
        op = float(A(it.get("op", 1), f, lo))
        if op <= 0:
            return
        spr = self.plan.sprites[it["spr"]]
        s_now = float(A(it.get("s", 1), f, lo))
        br = it.get("blur", 0) or 0
        b_now = float(A(br, f, lo)) * W / 1080.0
        X, Y = float(A(it.get("x", 0), f, lo)) * W + spr["dx"], float(A(it.get("y", 0), f, lo)) * H
        if abs(s_now - 1.0) < 0.0025:            # a word at rest sits on the pixel grid, so it stays crisp
            s_now = 1.0
            if held(it.get("x"), f, lo):
                X = float(round(X))
            if held(it.get("y"), f, lo):
                Y = float(round(Y))
        rx, ry, rw, rh = X + spr["x0"], Y + spr["y0"], spr["w"], spr["h"]
        if s_now != 1.0:
            cx, cy = X + spr["cx"], Y + spr["cy"]
            rx, ry = cx + (rx - cx) * s_now, cy + (ry - cy) * s_now
            rw, rh = rw * s_now, rh * s_now
        v = self.plan.voices.get(it.get("v")) or {}
        blend = BLENDS.get(it.get("blend"), 0)
        ss = spr["ss"]
        lod = max(math.log2(max(ss / max(s_now, 1e-3), 1.0)), 0.0)
        bsig = b_now * ss / max(s_now, 1e-3) if b_now > 0.25 else 0.0
        if blend:
            self.snapshot()
            self.bg.use(1)
            self.ctx.disable(self.mgl.BLEND)
        fill = self.plan.fill_of.get(it["id"])
        st = (it.get("fill") or {}).get("plate") if isinstance(it.get("fill"), dict) else None
        self.U(p, uFill=0, uFillTex=2, uHard=1 if it.get("hard") else 0)
        if fill:
            pf = self.picture(fill, f)
            c = AR(fill.get("crop"), f, lo) or self.cover(pf["w"], pf["h"], W, H)
            # the picture fills the word, not the frame: a crop of the same shape as the letters' block
            cw, ch = (c[2] - c[0]) * pf["w"], (c[3] - c[1]) * pf["h"]
            k = (rw / max(abs(rh), 1)) / max(cw / max(ch, 1), 1e-3)
            mx, my = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
            hx, hy = (c[2] - c[0]) / 2, (c[3] - c[1]) / 2
            hx, hy = (hx, hy / k) if k > 1 else (hx * k, hy)
            pf["tex"].use(2)
            self.U(p, uFill=1, uFillCrop=(mx - hx, my - hy, mx + hx, my + hy))
        elif st and PLATES.get(st.get("kind")) == 2:
            kk = W / 1080.0
            self.noise_tex.use(2)
            self.U(p, uFill=2, uFillC0=hex3(st.get("c0"), (0.0, 0.0, 0.0)), uFillC1=hex3(st.get("c1"), (1.0, 1.0, 1.0)),
                   uStatic=(max(round(float(st.get("cell", 12)) * kk), 1.0), round(float(st.get("speed", -12)) * kk) * (f - lo),
                            float(int(st.get("seed", self.plan.seed)) % 65521)))
        else:
            self.noise_tex.use(2)
        self.atlas[spr["page"]].use(0)
        pg = self.plan.pages[spr["page"]]
        stroke = it.get("stroke") or {}
        from .type_atlas import SPREAD
        self.U(p, uW=0, uBg=1, uAtlas=(pg.shape[1], pg.shape[0]), uSpr=(spr["ax"], spr["ay"], spr["aw"], spr["ah"]), uLod=lod,
               uInk=hex3(it.get("ink")), uOp=op, uShadow=float((v.get("shadow") or {}).get("a", 0)),
               uGlow=float((v.get("glow") or {}).get("a", 0)), uBlur=bsig, uBlend=blend,
               uFx=(float(stroke.get("w", 0)) * W / 1080.0, float(A(it.get("grow", 0) or 0, f, lo)) * W / 1080.0, SPREAD * ss, ss / max(s_now, 1e-3)),
               uStrokeC=hex3(stroke.get("c"), (0.0, 0.0, 0.0)))
        self.rect_uniforms(p, rx, ry, rw, rh)
        self.U(p, uFade=0)
        vao.render(self.mgl.TRIANGLE_STRIP)
        rf = float(it.get("reflect", 0) or 0)
        if rf > 0 and not blend:         # the word stands on a wet floor: mirrored below its own baseline, softer and fading away
            floor = Y + 0.05 * float(v.get("px", 58))
            self.U(p, uOp=op * rf, uFade=1, uBlur=max(bsig, 2.2 * ss), uShadow=0.0, uGlow=0.0)
            self.rect_uniforms(p, rx, 2 * floor - ry, rw, -rh)
            vao.render(self.mgl.TRIANGLE_STRIP)
        if blend:
            self.ctx.enable(self.mgl.BLEND)

    # finish ------------------------------------------------------

    def draw_film(self, f, grain, vig, over=False):
        """One film layer. Under type: lens, raster, vignette. Over type, on request: grain, mesh."""
        fl = self.plan.film
        mesh, raster = fl.get("mesh") or {}, fl.get("raster") or {}
        hal, bloom = (0.0, 0.0) if over else (float(fl.get("halation", 0) or 0), float(fl.get("bloom", 0) or 0))
        m_a = float(mesh.get("a", 0)) if bool(fl.get("mesh_over", False)) == over else 0.0
        r_a = 0.0 if over else float(raster.get("a", 0))
        if not (grain or vig or hal or bloom or m_a or r_a):
            return
        self.snapshot(mips=bool(hal or bloom))
        if hal or bloom:
            pb, vb = self.P["bright"]
            self.hi_fb.use()
            self.ctx.disable(self.mgl.BLEND)
            self.bg.use(0)
            self.U(pb, uBg=0, uRect=(0, 0, self.W // 4, self.H // 4), uFrame=(self.W // 4, self.H // 4))
            vb.render(self.mgl.TRIANGLE_STRIP)
            self.hi.build_mipmaps()
            self.comp_fb.use()
        self.hi.use(2)
        p, vao = self.P["film"]
        self.bg.use(0)
        self.noise_tex.use(1)
        self.U(p, uBg=0, uNoise=1, uHi=2, uAt=self.plan.noise_at(f), uGrain=grain, uVig=vig, uHal=hal, uBloom=bloom,
               uMesh=(float(mesh.get("pitch", 7)) * self.W / 1080.0, m_a),
               uRaster=(float(raster.get("pitch", 4.4)) * self.W / 1080.0, r_a))
        self.rect_uniforms(p)
        self.ctx.disable(self.mgl.BLEND)
        vao.render(self.mgl.TRIANGLE_STRIP)

    # one frame ---------------------------------------------------

    def frame(self, f, active):
        """Draw frame f into the final target, in the stage order of DESIGN 4.4."""
        ctx, mgl = self.ctx, self.mgl
        t = time.perf_counter()
        self.comp_fb.use()
        ctx.clear(0, 0, 0, 1)
        ctx.disable(mgl.BLEND)
        by = collections.defaultdict(list)
        for it in active:
            if it["k"] not in self.drop:
                by[it["k"]].append(it)
        zid = lambda i: (i.get("z", 0), str(i["id"]))      # noqa: E731
        shots = by["shot"]
        if shots:                                           # stage 0: base
            self.draw_shot(shots[-1], f)
        t = self.tick("base", t)
        top = [i for i in by["join"] if i.get("stage") == "top"]
        if by["join"]:                                      # stage 3: junction, reads both shots
            for it in sorted((i for i in by["join"] if i.get("stage") != "top"), key=zid):
                self.draw_join(it, f)
            self.comp_fb.use()
            t = self.tick("join", t)
        if shots and self.plan.take_shot(shots[-1]):
            back_c = [i for i in by["card"] if i.get("behind")]
            back_w = [i for i in by["word"] if i.get("behind")]
            if back_c or back_w:
                # Stages 4 and 5, the depth sandwich: what stands behind him is drawn, then he is drawn again through his matte.
                ctx.enable(mgl.BLEND)
                ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
                for it in sorted(back_c, key=zid):
                    self.draw_card(it, f)
                for it in sorted(back_w, key=zid):
                    self.draw_word(it, f)
                self.draw_shot(shots[-1], f, matte=self.matte_tex(shots[-1], f))
                ctx.disable(mgl.BLEND)
                by["card"] = [i for i in by["card"] if not i.get("behind")]
                by["word"] = [i for i in by["word"] if not i.get("behind")]
                t = self.tick("behind", t)
        if by["card"]:                                      # stage 6: the world in front
            ctx.enable(mgl.BLEND)
            ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
            for it in sorted(by["card"], key=zid):
                self.draw_card(it, f)
            ctx.disable(mgl.BLEND)
            t = self.tick("card", t)
        fl = self.plan.film
        over = bool(fl.get("over_type"))
        plate = bool(shots) and shots[-1].get("pic") == "plate"
        grain = float(fl.get("grain", 0) or 0)
        self.draw_film(f, 0.0 if over else grain, 0.0 if plate else float(fl.get("vignette", 0) or 0))   # stages 8, 9
        t = self.tick("film", t)
        if by["flash"]:                                     # stage 10
            for it in sorted(by["flash"], key=zid):
                self.draw_flash(it, f)
            t = self.tick("flash", t)
        if by["word"]:                                      # stage 11: the screen plane
            ctx.enable(mgl.BLEND)
            ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
            for it in sorted(by["word"], key=zid):
                self.draw_word(it, f)
            ctx.disable(mgl.BLEND)
            t = self.tick("type", t)
        if top:                                             # a junction that eats the type as it passes
            for it in sorted(top, key=zid):
                self.draw_join(it, f)
            self.comp_fb.use()
            t = self.tick("join", t)
        if over or fl.get("mesh_over"):                     # stage 12
            self.draw_film(f, grain if over else 0.0, 0.0, over=True)
            t = self.tick("film_over", t)
        p, vao = self.P["out"]
        self.final_fb.use()
        self.comp.use(0)
        self.noise_tex.use(1)
        self.U(p, uBg=0, uNoise=1, uAt=self.plan.noise_at(f, 1), uDither=float(fl.get("dither", 1.0)))
        self.rect_uniforms(p)
        vao.render(mgl.TRIANGLE_STRIP)
        self.tick("out", t)

    def read(self):
        return self.final_fb.read(components=4, alignment=1)

    def frame_array(self, f, active):
        self.frame(f, active)
        return np.frombuffer(self.read(), np.uint8).reshape(self.H, self.W, 4)

    def release(self):
        try:
            self.ctx.release()
        except Exception:      # noqa: BLE001
            pass
