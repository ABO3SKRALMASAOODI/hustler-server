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
from . import looks
from .plan import A, AR, BLENDS, IDENTITY, JOINS, MODES, PLATES, TREATS, held, hex3

GLSL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "glsl")
HEADER = {"core": "#version 330 core\n",
          "es": "#version 300 es\nprecision highp float;\nprecision highp int;\nprecision highp sampler2D;\n"}
PROGRAMS = {"pic": ("common", "easu", "picture", "pic.frag"), "join": ("common", "easu", "picture", "join.frag"),
            "shadow": ("shadow.frag",), "flash": ("common", "flash.frag"), "film": ("common", "film.frag"),
            "word": ("common", "word.frag"), "out": ("out.frag",), "bright": ("bright.frag",), "hiblur": ("common", "hiblur.frag"),
            "keep": ("keep.frag",), "wblur": ("common", "wblur.frag")}
# Uniforms that gate heavy branches. A software rasteriser runs both sides of every branch on a
# uniform, so on llvmpipe they are folded to constants: one compiled variant per distinct set of
# gates, cached (lane D measured the film pass at 75 ms a frame before and 9 ms after).
#   int      always folded          float0  folded when 0.0
#   vec2y0   folded when .y is 0    zero    folded when every component is 0
FOLD = {"pic": {"uPlate": "int", "uEdge": "int", "uAlpha": "int", "uSamp": "int", "uTreat": "int", "uTreatHasC": "int",
                "uBlur": "float0", "uSharp": "float0", "uBorder": "float0", "uPlateGrain": "float0", "uLook": "int", "uTaps": "int"},
        "join": {"uJoin": "int", "uEdge": "int", "uAlpha": "int", "uSamp": "int", "uTreat": "int", "uTreatHasC": "int", "uBlur": "float0",
                 "uSharp": "float0", "uLook": "int"},
        "hiblur": {"uStep": "int"}, "bright": {"uStep": "int"},
        "flash": {"uMode": "int", "uHasC": "int"},
        "film": {"uGrain": "float0", "uVig": "float0", "uHal": "float0", "uBloom": "float0", "uMesh": "vec2y0", "uRaster": "vec2y0",
                 "uOut": "int", "uDither": "float0", "uTypeOn": "int"},
        "word": {"uBlur": "float0", "uPre": "int", "uBlend": "int", "uFade": "int", "uFill": "int", "uHard": "int", "uShadow": "float0",
                 "uGlow": "float0", "uFx": "zero"}}
SOFTWARE = ("llvmpipe", "softpipe", "swiftshader", "swrast")
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


def fold_wanted(renderer):
    """VALMERA_SCENE_FOLD: 1, 0, or unset for auto (on when the GL renderer is a software rasteriser)."""
    v = os.environ.get("VALMERA_SCENE_FOLD", "auto")
    if v in ("0", "1"):
        return v == "1"
    return any(k in str(renderer).lower() for k in SOFTWARE)


class Folded:
    """A program whose gating uniforms become constants at draw time. Uniform writes are recorded;
    the draw picks (or compiles once) the variant for the recorded gates and replays the rest on it.
    It stands where a (program, vertex array) pair stands, so the draw code does not know."""

    class _Slot:
        def __init__(self, owner, k):
            self.owner, self.k = owner, k

        @property
        def value(self):
            return self.owner.vals.get(self.k)

        @value.setter
        def value(self, v):
            self.owner.vals[self.k] = v

    class _Vao:
        def __init__(self, owner):
            self.owner = owner

        def render(self, mode):
            self.owner.resolve().render(mode)

    def __init__(self, name, base, build):
        self.name, self.build = name, build
        self.names = set(base[0])
        self.vals, self.cache = {}, {}

    def pair(self):
        return self, Folded._Vao(self)

    def __contains__(self, k):
        return k in self.names

    def __getitem__(self, k):
        return Folded._Slot(self, k)

    def resolve(self):
        consts = {}
        for u, rule in FOLD[self.name].items():
            if u not in self.vals:
                continue
            v = self.vals[u]
            if rule == "int":
                consts[u] = ("int", str(int(v)))
            elif rule == "float0" and float(v) == 0.0:
                consts[u] = ("float", "0.0")
            elif rule == "vec2y0" and float(v[1]) == 0.0:
                consts[u] = ("vec2", f"vec2({float(v[0])!r}, 0.0)")
            elif rule == "zero" and not any(float(c) for c in v):
                consts[u] = (f"vec{len(v)}", f"vec{len(v)}(0.0)")
        key = tuple(sorted((k, c[1]) for k, c in consts.items()))
        pv = self.cache.get(key)
        if pv is None:
            pv = self.cache[key] = self.build(self.name, consts)
        p, vao = pv
        for k, v in self.vals.items():
            if k in p:
                p[k].value = v
        return vao


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
        self.folded = fold_wanted(self.gl)
        if self.folded:
            import re

            def build(name, consts):
                src = "".join(_src(x) for x in PROGRAMS[name])
                for u, (ty, val) in consts.items():
                    src, n = re.subn(r"uniform\s+" + ty + r"\s+" + u + r"\s*;", f"const {ty} {u} = {val};", src)
                    if n != 1:
                        raise SceneFault("gl", f"shader {name}: cannot fold {u}")
                try:
                    p = ctx.program(vertex_shader=vs, fragment_shader=head + src)
                except Exception as e:      # noqa: BLE001
                    raise SceneFault("gl", f"shader {name} with {sorted(consts)} folded does not compile on {self.gl}: {str(e)[:600]}")
                return p, ctx.simple_vertex_array(p, self.vbo, "p")
            for name in FOLD:
                self.P[name] = Folded(name, self.P[name], build).pair()
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
            self.type, self.type_fb = target("f2")                 # the frame's type, as a layer (see ``frame``)

            def small(k):
                t = ctx.texture((max(self.W // k, 1), max(self.H // k, 1)), 4, dtype="f2")
                t.repeat_x = t.repeat_y = False
                t.filter = (moderngl.LINEAR, moderngl.LINEAR)
                return t, ctx.framebuffer([t])
            # the lens field: highlights at an eighth size, the same at a sixteenth, its wide blur
            # there, and the finished field (tight and wide glow) back at an eighth
            self.hi, self.hi_fb = small(8)
            self.hq, self.hq_fb = small(16)
            self.hw, self.hw_fb = small(16)
            self.hi2, self.hi2_fb = small(8)
            self.cur_fb = self.comp_fb          # where type is being drawn: the composite, or the type layer
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
        self.soft = {}                     # blurred sprites, kept while a word's blur holds (``soft_sprite``)
        self.base = self.base_fb = None    # the base picture, kept for the frames where the person is drawn again over what stands behind him
        self.reader = None
        self.t = collections.Counter()
        self.n = collections.Counter()
        self.drop = set()                  # kinds left out, so a check can draw the film without its flashes
        self.src_sum, self.src_n = 0.0, 0
        # A software rasteriser defers every draw to the next flush, so a stage timer reads whichever
        # stage happened to flush. VALMERA_SCENE_SYNC=1 finishes the GL queue at each mark: true
        # per-stage times for a profile, at a cost, so never on a customer render.
        self.sync = os.environ.get("VALMERA_SCENE_SYNC") == "1"
        self._m = time.perf_counter()
        # A software rasteriser draws inside whichever GL call next needs the pixels, and moderngl
        # keeps the interpreter lock through its calls: the reader's and the encoder's threads stood
        # still while a frame was drawn, and the draw then stood still for them (measured on the box:
        # 21 to 33 ms a frame waiting for a decoded picture). glFinish asked through ctypes waits with
        # the lock released, so pictures are read and frames are handed on while the rasteriser works.
        # VALMERA_SCENE_YIELD: 1, 0, or unset for auto (on when the renderer is a software rasteriser).
        self._finish = None
        y = os.environ.get("VALMERA_SCENE_YIELD", "auto")
        if y == "1" or (y != "0" and any(k in str(self.gl).lower() for k in SOFTWARE)):
            try:
                import ctypes
                self._finish = ctypes.CFUNCTYPE(None)(ctx.mglo._context.load_opengl_function("glFinish"))
            except Exception:      # noqa: BLE001 - without it the film is drawn the same, only slower
                self._finish = None

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

    def settle(self):
        """Let the rasteriser finish what is queued, with the interpreter lock released."""
        if self._finish is not None:
            self._finish()

    def mark(self, what):
        """After a pass: the rasteriser finishes it (``settle``). In a profile the time since the last
        mark goes to ``what`` (see ``sync``). ``py`` and ``word`` only keep time: the type stage
        settles once, at its end."""
        if what not in ("py", "word"):
            self.settle()
        if not self.sync:
            return
        self.ctx.finish()
        now = time.perf_counter()
        self.t["p:" + what] += now - self._m
        self.n["p:" + what] += 1
        self._m = now

    def tick(self, stage, t0):
        self.settle()
        if self.sync:
            self.ctx.finish()
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
                s = dict(tex=t, w=a["w"], h=a["h"], alpha=False, n=-1, used=f, mips=0)
                slots.append(s)
            else:
                s = min(slots, key=lambda q: q["used"])
            self.mark("py")
            tw = time.perf_counter()
            b = self.reader.get((aid, n))
            tw = time.perf_counter() - tw
            self.t["decode_wait"] += tw                # inside whichever stage asked: the draw stood still for the reader
            self.n["decode_wait"] += 1
            if tw > 0.05:
                self.t["decode_wait_long"] += tw
                self.n["decode_wait_long"] += 1
            self.mark("wait_decode")
            s["tex"].write(b)
            self.mark("upload")
            self.src_sum += float(np.frombuffer(b, np.uint8)[::4099].mean())
            self.src_n += 1
            s.update(n=n, used=f, mips=0)
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
            self.pics[aid] = dict(tex=t, w=arr.shape[1], h=arr.shape[0], alpha=has_alpha, n=0, used=f, mips=1000)
            self.src_sum += float(arr[..., :3].reshape(-1)[::4099].mean())
            self.src_n += 1
        return self.pics[aid]

    def matte_tex(self, shot, f):
        """The person's matte for this shot and frame, uploaded once per frame of the matte."""
        n = self.plan.matte_frame(shot, f)
        a = self.plan.assets["matte"]
        s = self.pics.get("matte")
        if s is None:
            from .reader import frame_bytes
            t = self.ctx.texture((a["w"], a["h"]), 1 if frame_bytes(a) == a["w"] * a["h"] else 4, alignment=1)
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

    @staticmethod
    def on_grid(pic, crop, geom):
        """True when every pixel centre of this draw lands on a texel centre, one to one: then the
        picture is its texels, and nine taps of Catmull-Rom would only give them back."""
        cx, cy, hw, hh, s, rot = geom
        if rot != 0.0 or s <= 0 or hw <= 0 or hh <= 0:
            return False
        for c0, c1, n, c, h in ((crop[0], crop[2], pic["w"], cx, hw), (crop[1], crop[3], pic["h"], cy, hh)):
            k = (c1 - c0) * n / (2 * h * s)                          # texels per pixel
            t0 = (c0 + ((0.5 - c) / (2 * h * s) + 0.5) * (c1 - c0)) * n      # the texel coordinate under the first pixel centre
            if abs(k - 1.0) > 1e-5 or abs(t0 - math.floor(t0) - 0.5) > 2e-3:
                return False
        return True

    def look_uniforms(self, p, it, pic):
        """The look for one picture: the item's own, else the film's. A plate is a designed colour and takes none."""
        lk = it.get("look") if "look" in it else self.plan.film.get("look")
        if pic is None or not isinstance(lk, dict):
            self.U(p, uLook=0)
            return
        u = looks.uniforms(lk)
        win = it.get("window")
        win = tuple(float(v) for v in win[:4]) if (isinstance(win, (list, tuple)) and len(win) >= 4) else (0.5, 0.5, 0.0, 0.0)
        self.U(p, uLook=1, uLk0=u["lk0"], uLk1=u["lk1"], uLkSh=u["sh"], uLkHi=u["hi"], uSk=u["sk"], uLkDrop=u["drop"], uLkWin=win)

    def picture_uniforms(self, p, it, f, pic, crop, out_w, blur, geom=None, taps=0):
        """Crop, sampling, treat and grade for one picture drawn ``out_w`` frame pixels wide."""
        self.look_uniforms(p, it, pic)
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
            self.U(p, uTexSize=(1, 1), uCrop=(0, 0, 1, 1), uSharp=0.0, uAlpha=0, uEdge=0, uSamp=0)
            return
        pix_in = (crop[2] - crop[0]) * pic["w"]
        up = out_w / max(pix_in, 1.0)
        tpp = pix_in / max(out_w, 1e-6)                       # texels per pixel, as the shader works it out
        samp = 2 if (blur > 0.01 and not taps) else 1 if tpp > 1.05 else 0
        # Mips are built per decoded frame, so only as deep as this draw reads: a push-in that
        # minifies by a sixth needs one level, not eleven (a level is a pass of its own on llvmpipe).
        deep = 1000 if (samp == 2 or mode >= 20) else max(int(math.ceil(math.log2(tpp))), 1) if samp == 1 else 0
        if deep > int(pic.get("mips") or 0):
            self.mark("py")
            pic["tex"].build_mipmaps(0, deep)
            pic["mips"], pic["filter"] = deep, "mip"
            self.mark("src_mips")
        # the sampler says what the draw reads: a picture read at its own level asks for no mip maths
        want = "mip" if deep else "flat"
        if pic.get("filter") != want:
            pic["tex"].filter = (self.mgl.LINEAR_MIPMAP_LINEAR, self.mgl.LINEAR) if deep else (self.mgl.LINEAR, self.mgl.LINEAR)
            pic["filter"] = want
        edge = up >= 1.25 and not self.plan.film.get("no_easu")
        sharp = float(self.plan.film.get("sharpen", min(max((up - 1.15) / 1.4, 0.0), 1.0) * (0.35 if edge else 1.0)))
        if samp == 0 and sharp == 0.0 and mode < 20 and not taps and not pic.get("alpha") and geom and self.on_grid(pic, crop, geom):
            samp = 3
        self.U(p, uTexSize=(pic["w"], pic["h"]), uCrop=tuple(crop), uSharp=sharp if samp == 0 else 0.0, uAlpha=1 if pic.get("alpha") else 0,
               uEdge=1 if (edge and samp == 0) else 0, uSamp=samp)

    # blit: shot and card -----------------------------------------

    def shutter_of(self, it):
        """How long the shutter is open on this item, as a share of a frame: its own, else the film's. 0 is none."""
        v = it.get("shutter") if "shutter" in it else self.plan.film.get("shutter")
        return min(max(float(v or 0.0), 0.0), 2.0)

    @staticmethod
    def _mid(a, b, t):
        if a is None or b is None:
            return a
        if isinstance(a, (list, tuple)):
            return [x + (y - x) * t for x, y in zip(a, b)]
        return a + (b - a) * t

    def shutter_poses(self, it, f, sh, pose):
        """Pose A and pose B of an exposure centred on frame f: (centre x, centre y, scale, crop) at f - sh/2 and
        f + sh/2, read off the item's own baked curves. ``pose(g)`` gives the pose on frame g; frames outside the
        item hold its ends, so a move that starts on a cut blurs forward only. None when nothing moves."""
        lo, hi = it["f"]
        now, before, after = pose(f), pose(max(f - 1, lo)), pose(min(f + 1, hi - 1))
        if now == before and now == after:
            return None
        h = sh / 2
        return tuple(self._mid(n, b, h) for n, b in zip(now, before)), tuple(self._mid(n, a, h) for n, a in zip(now, after))

    def draw_pic(self, it, f, geom, pose_b=None):
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
            self.U(p, uTaps=0)
            self.plate_uniforms(p, it["id"], pl, f, lo)
            self.noise_tex.use(0)                  # a plate samples no picture, but unit 0 must hold a texture
            self.noise_tex.use(1)
            self.quad_scissored("pic", cx, cy, hw, hh, s, rot)
            return
        pic = self.picture(it, f)
        crop = AR(it.get("crop"), f, lo) or self.cover(pic["w"], pic["h"], hw, hh)
        taps, grow = 0, 0.0
        if pose_b is not None:
            # the exposure runs from pose A to pose B (``shutter_poses``): how far the picture travels decides the samples
            (cxa, cya, sa, cropa), (cxb, cyb, sb, cropb) = pose_b
            ca = cropa or crop
            cb = cropb or crop
            kx, ky = 2 * hw * s / max(ca[2] - ca[0], 1e-6), 2 * hh * s / max(ca[3] - ca[1], 1e-6)
            ext = max(abs(cxb - cxa) + abs(sb - sa) * hw, abs(cyb - cya) + abs(sb - sa) * hh,
                      max(abs(cb[0] - ca[0]), abs(cb[2] - ca[2])) * kx, max(abs(cb[1] - ca[1]), abs(cb[3] - ca[3])) * ky)
            # under three pixels of travel (at 1080 wide) nobody sees a smear, and the single sample is the sharper picture
            if ext >= 3.0 * self.W / 1080.0:
                taps = 8 if ext < 16 else 16 if ext < 64 else 24
                grow = abs(cxb - cxa) + abs(cyb - cya) + abs(sb - sa) * max(hw, hh)
                cx, cy, s, crop = cxa, cya, sa, ca
                self.U(p, uCentre=(cxa, cya), uScale=sa, uCentreB=(cxb, cyb), uScaleB=sb, uCropB=tuple(cb))
                cx, cy = (cxa + cxb) / 2, (cya + cyb) / 2                 # the scissor's centre: between the two poses
                self.n["shutter"] += 1
        self.picture_uniforms(p, it, f, pic, crop, 2 * hw * s, blur, (cx, cy, hw, hh, s, rot), taps=taps)
        self.U(p, uPlate=0, uTaps=taps)
        pic["tex"].use(0)
        self.noise_tex.use(1)
        self.quad_scissored("pic", cx, cy, hw, hh, max(s, pose_b[1][2]) if taps else s, rot, grow=grow)

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
        self.mark("py")
        vao.render(self.mgl.TRIANGLE_STRIP)
        tag = ""
        if self.sync and name == "pic":
            vals = getattr(p, "vals", {})
            tag = f"[samp{vals.get('uSamp')}" + "".join(f" {k[1:].lower()}" for k in ("uEdge", "uSharp", "uTreat", "uPlate", "uAlpha") if vals.get(k)) + "]"
        self.mark(name + ("_full" if (x1 - x0) * (y1 - y0) > 0.7 * self.W * self.H else "_part") + tag)
        self.ctx.scissor = None

    def draw_shot(self, it, f):
        pose_b = None
        sh = self.shutter_of(it)
        if sh > 0 and isinstance(it.get("crop"), list) and it["crop"] and isinstance(it["crop"][0], (list, tuple)):
            lo = it["f"][0]
            pose_b = self.shutter_poses(it, f, sh, lambda g: (self.W / 2, self.H / 2, 1.0, list(AR(it["crop"], g, lo))))
        self.draw_pic(it, f, (self.W / 2, self.H / 2, self.W / 2, self.H / 2, 1.0, 0.0, 1.0, 0.0), pose_b)

    def keep_base(self):
        """Keep the base picture as drawn, for ``draw_kept``."""
        if self.base is None:
            self.base = self.ctx.texture((self.W, self.H), 4, dtype="f2")
            self.base.repeat_x = self.base.repeat_y = False
            self.base.filter = (self.mgl.NEAREST, self.mgl.NEAREST)
            self.base_fb = self.ctx.framebuffer([self.base])
        self.ctx.copy_framebuffer(self.base_fb, self.comp_fb)
        self.mark("keep_base")

    def draw_kept(self, it, f, matte):
        """The person again, through his matte, over what was drawn behind him. The base picture is
        read back as it was drawn (``keep_base``), not sampled, treated and graded a second time."""
        p, vao = self.P["keep"]
        pic = self.picture(it, f)
        crop = AR(it.get("crop"), f, it["f"][0]) or self.cover(pic["w"], pic["h"], self.W / 2, self.H / 2)
        self.base.use(0)
        matte.use(1)
        self.U(p, uBase=0, uMatte=1, uCrop=tuple(crop))
        self.rect_uniforms(p)
        vao.render(self.mgl.TRIANGLE_STRIP)
        self.mark("keep")

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
        pose_b = None
        shut = self.shutter_of(it)
        if shut > 0 and any(isinstance(it.get(k), list) for k in ("x", "y", "s", "crop")):
            def pose(g):
                c = AR(it.get("crop"), g, lo)
                return (float(A(it.get("x", 0.5), g, lo)) * W, float(A(it.get("y", 0.5), g, lo)) * H, float(A(it.get("s", 1), g, lo)),
                        list(c) if c is not None else None)
            pose_b = self.shutter_poses(it, f, shut, pose)
        self.draw_pic(it, f, (cx, cy, hw, hh, s, rot, op, radius), pose_b)

    # flash and join: they read the frame ---------------------------

    def snapshot(self, mips=False):
        """Copy the frame as it stands, for a pass that reads it and writes over it."""
        self.mark("py")
        self.ctx.copy_framebuffer(self.bg_fb, self.comp_fb)
        self.mark("snap")
        if mips:
            self.bg.build_mipmaps()
            self.mark("snap_mips")

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
        self.mark("flash")
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
        self.picture_uniforms(p, it, f, pic, crop, W, float(A(it.get("blur", 0) or 0, f, lo)) * W / 1080.0, (W / 2, H / 2, W / 2, H / 2, 1.0, 0.0))
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
        self.mark("join")

    # blit: type from the atlas -----------------------------------

    SOFT_KEPT = 48

    def soft_sprite(self, spr, sig, f):
        """The sprite blurred by ``sig``, as a texture the size the sprite stands at rest, or None
        the first time this blur is met. A word held out of focus (and every reflection) is the same
        blurred sprite frame after frame: it is blurred on the second frame that asks and read back
        after that, through one bilinear tap, at whatever scale the word is settling through (a blur
        is smooth: it is fully described on the grid it is drawn on). A blur that is moving asks once
        per value and is drawn as it always was. Blurs within 2% of each other share one copy."""
        size = (max(int(round(spr["w"])), 1), max(int(round(spr["h"])), 1))
        key = (spr["page"], spr["ax"], spr["ay"], spr["aw"], spr["ah"], int(round(32 * math.log2(max(sig, 1e-3)))))
        e = self.soft.get(key)
        if e is None:
            if len(self.soft) > 4096:
                self.soft = {k: v for k, v in self.soft.items() if v["tex"] is not None}
            e = self.soft[key] = {"n": 0, "tex": None, "fb": None, "used": f}
        e["n"] += 1
        e["used"] = f
        if e["tex"] is None:
            if e["n"] < 2 or size[0] * size[1] > self.W * self.H:
                self.n["soft_drawn"] += 1
                return None
            kept = [v for v in self.soft.values() if v["tex"] is not None]
            if len(kept) >= self.SOFT_KEPT:
                old = min(kept, key=lambda v: v["used"])
                old["fb"].release()
                old["tex"].release()
                old.update(tex=None, fb=None, n=0)
            t = self.ctx.texture(size, 4, dtype="f2")
            t.repeat_x = t.repeat_y = False
            t.filter = (self.mgl.LINEAR, self.mgl.LINEAR)
            e["tex"], e["fb"] = t, self.ctx.framebuffer([t])
            p, vao = self.P["wblur"]
            pg = self.plan.pages[spr["page"]]
            e["fb"].use()
            self.ctx.disable(self.mgl.BLEND)
            self.atlas[spr["page"]].use(0)
            self.U(p, uW=0, uAtlas=(pg.shape[1], pg.shape[0]), uSpr=(spr["ax"], spr["ay"], spr["aw"], spr["ah"]), uBlur=sig,
                   uRect=(0, 0, size[0], size[1]), uFrame=size)
            vao.render(self.mgl.TRIANGLE_STRIP)
            self.cur_fb.use()                               # back to the frame: type is drawn on it, blended
            self.ctx.enable(self.mgl.BLEND)
            self.mark("word_soft")
        self.n["soft_kept"] += 1
        return e["tex"]

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
        # A word at rest sits on the pixel grid, so it stays crisp. The margin keeps a value that sits
        # exactly on the line (1.0025, as a Score rounds it) at rest after its trip through Float32.
        if abs(s_now - 1.0) < 0.0025 + 1e-6:
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
            if fill.get("frame"):
                # a window onto the picture as a shot would show it: same crop, same sampling, same grade
                g = {**IDENTITY, **(fill.get("grade") or self.plan.film.get("grade") or {})}
                if (c[2] - c[0]) * pf["w"] > W * 1.05 and int(pf.get("mips") or 0) < 1000:
                    pf["tex"].build_mipmaps()
                    pf["mips"], pf["filter"] = 1000, "mip"
                self.U(p, uFill=3, uFillCrop=tuple(c), uFillSize=(pf["w"], pf["h"]), uFG0=(g["lift"], g["gamma"], g["gain"]),
                       uFG1=(g["sat"], g["contrast"], g["temp"]), uFTint=g["tint"])
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
        fx_w, fx_g = float(stroke.get("w", 0)) * W / 1080.0, float(A(it.get("grow", 0) or 0, f, lo)) * W / 1080.0
        self.U(p, uW=0, uBg=1, uAtlas=(pg.shape[1], pg.shape[0]), uSpr=(spr["ax"], spr["ay"], spr["aw"], spr["ah"]), uLod=lod,
               uInk=hex3(it.get("ink")), uOp=op, uShadow=float((v.get("shadow") or {}).get("a", 0)),
               uGlow=float((v.get("glow") or {}).get("a", 0)), uBlur=bsig, uBlend=blend,
               uFx=(fx_w, fx_g, SPREAD * ss, ss / max(s_now, 1e-3)) if (fx_w or fx_g) else (0.0, 0.0, 0.0, 0.0),
               uStrokeC=hex3(stroke.get("c"), (0.0, 0.0, 0.0)))
        self.rect_uniforms(p, rx, ry, rw, rh)
        self.U(p, uFade=0, uPre=0, uPreTex=3)
        self.word_quad(p, vao, spr, bsig, f, blend)
        rf = float(it.get("reflect", 0) or 0)
        if rf > 0 and not blend:         # the word stands on a wet floor: mirrored below its own baseline, softer and fading away
            floor = Y + 0.05 * float(v.get("px", 58))
            self.U(p, uOp=op * rf, uFade=1, uBlur=max(bsig, 2.2 * ss), uShadow=0.0, uGlow=0.0)
            self.rect_uniforms(p, rx, 2 * floor - ry, rw, -rh)
            self.word_quad(p, vao, spr, max(bsig, 2.2 * ss), f, blend)
        self.mark("word")
        if blend:
            self.ctx.enable(self.mgl.BLEND)

    def word_quad(self, p, vao, spr, sig, f, blend):
        """One sprite, its uniforms set: through the kept blurred copy when there is one."""
        pre = self.soft_sprite(spr, sig, f) if sig > 0.0 else None
        if pre is not None:
            if blend:
                self.ctx.disable(self.mgl.BLEND)            # soft_sprite leaves blending on, as the type stage has it
            pre.use(3)
            self.atlas[spr["page"]].use(0)
            self.U(p, uPre=1)
        else:
            self.atlas[spr["page"]].use(3)                  # unread, but a sampler's unit must hold a texture
            self.U(p, uPre=0)
        vao.render(self.mgl.TRIANGLE_STRIP)

    # finish ------------------------------------------------------

    def draw_film(self, f, grain, vig, over=False, out=False, typed=False):
        """One film layer. Under type: lens, raster, vignette. Over type, on request: grain, mesh.

        ``out``: the layer is also the way out of the float composite. It is drawn into the 8-bit
        frame, with the dither, so the frame needs no pass of its own for that, and no copy of the
        composite either (it no longer writes where it reads). ``typed``: the type layer goes over
        it in the same pass. With no layer on, it is the way out alone."""
        fl = self.plan.film
        mesh, raster = fl.get("mesh") or {}, fl.get("raster") or {}
        hal, bloom = (0.0, 0.0) if over else (float(fl.get("halation", 0) or 0), float(fl.get("bloom", 0) or 0))
        m_a = float(mesh.get("a", 0)) if bool(fl.get("mesh_over", False)) == over else 0.0
        r_a = 0.0 if over else float(raster.get("a", 0))
        if not out:
            if not (grain or vig or hal or bloom or m_a or r_a):
                return
            self.snapshot()
        self.ctx.disable(self.mgl.BLEND)
        if hal or bloom:
            def small(name, fb, src, **kw):
                p_, v_ = self.P[name]
                fb.use()
                src.use(0)
                self.U(p_, uRect=(0, 0, fb.width, fb.height), uFrame=(fb.width, fb.height), **kw)
                v_.render(self.mgl.TRIANGLE_STRIP)
                self.mark(name + str(kw.get("uStep", "")))
            small("bright", self.hi_fb, self.comp, uBg=0, uCut=0.62, uStep=0)   # the highlights, an eighth size
            small("bright", self.hq_fb, self.hi, uBg=0, uCut=0.0, uStep=1)      # the same field at a sixteenth
            self.noise_tex.use(1)                                               # unread in step 0, but never the target it draws into
            small("hiblur", self.hw_fb, self.hq, uHi=0, uWide=1, uStep=0)       # the wide glow, where it is cheap
            self.hw.use(1)
            small("hiblur", self.hi2_fb, self.hi, uHi=0, uWide=1, uStep=1)      # the tight glow, and both together
        (self.final_fb if out else self.comp_fb).use()
        self.hi2.use(2)
        p, vao = self.P["film"]
        (self.comp if out else self.bg).use(0)
        self.noise_tex.use(1)
        (self.type if typed else self.noise_tex).use(3)                         # a sampler's unit must hold a texture even when unread
        self.U(p, uBg=0, uNoise=1, uHi=2, uAt=self.plan.noise_at(f), uGrain=grain, uVig=vig, uHal=hal, uBloom=bloom,
               uMesh=(float(mesh.get("pitch", 7)) * self.W / 1080.0, m_a),
               uRaster=(float(raster.get("pitch", 4.4)) * self.W / 1080.0, r_a),
               uOut=1 if out else 0, uDither=float(fl.get("dither", 1.0)) if out else 0.0, uAt2=self.plan.noise_at(f, 1),
               uTypeOn=1 if typed else 0, uType=3)
        self.rect_uniforms(p)
        vao.render(self.mgl.TRIANGLE_STRIP)
        self.mark("film")

    # one frame ---------------------------------------------------

    def frame(self, f, active):
        """Draw frame f into the final target, in the stage order of DESIGN 4.4.

        On most frames nothing but type follows the film layer. Those frames take two full-frame
        passes fewer: the type is drawn first, into a layer of its own, and the film layer is drawn
        straight into the 8-bit frame with the type laid over it and the dither added, all in one
        pass. It is the same sum (type over film over the composite, then the way out), so the frame
        is the same. A frame with a flash, a blend-mode word, a junction over the type, or a film
        that lays grain or mesh over its type is drawn in the order of the stages, as it always was."""
        ctx, mgl = self.ctx, self.mgl
        self.mark("between")
        t = time.perf_counter()
        self.cur_fb = self.comp_fb
        self.comp_fb.use()
        ctx.clear(0, 0, 0, 1)
        ctx.disable(mgl.BLEND)
        by = collections.defaultdict(list)
        for it in active:
            if it["k"] not in self.drop:
                by[it["k"]].append(it)
        zid = lambda i: (i.get("z", 0), str(i["id"]))      # noqa: E731
        shots = by["shot"]
        back_c = back_w = ()
        if shots and self.plan.take_shot(shots[-1]):
            back_c = [i for i in by["card"] if i.get("behind")]
            back_w = [i for i in by["word"] if i.get("behind")]
        if shots:                                           # stage 0: base
            self.draw_shot(shots[-1], f)
            if back_c or back_w:
                self.keep_base()
        t = self.tick("base", t)
        top = [i for i in by["join"] if i.get("stage") == "top"]
        if by["join"]:                                      # stage 3: junction, reads both shots
            for it in sorted((i for i in by["join"] if i.get("stage") != "top"), key=zid):
                self.draw_join(it, f)
            self.comp_fb.use()
            t = self.tick("join", t)
        if back_c or back_w:
            # Stages 4 and 5, the depth sandwich: what stands behind him is drawn, then he is drawn again through his matte.
            ctx.enable(mgl.BLEND)
            ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
            for it in sorted(back_c, key=zid):
                self.draw_card(it, f)
            for it in sorted(back_w, key=zid):
                self.draw_word(it, f)
            self.draw_kept(shots[-1], f, self.matte_tex(shots[-1], f))
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
        vig = 0.0 if plate else float(fl.get("vignette", 0) or 0)
        staged = (over or bool(fl.get("mesh_over")) or bool(by["flash"]) or bool(top)
                  or any(BLENDS.get(i.get("blend"), 0) for i in by["word"]))
        if not staged:
            if by["word"]:                                  # stage 11, drawn first: the screen plane, as a layer
                self.cur_fb = self.type_fb
                self.type_fb.use()
                ctx.clear(0, 0, 0, 0)
                ctx.enable(mgl.BLEND)
                ctx.blend_func = mgl.ONE, mgl.ONE_MINUS_SRC_ALPHA
                for it in sorted(by["word"], key=zid):
                    self.draw_word(it, f)
                ctx.disable(mgl.BLEND)
                self.cur_fb = self.comp_fb
                t = self.tick("type", t)
            self.draw_film(f, grain, vig, out=True, typed=bool(by["word"]))     # stages 8, 9, the type over them, 13
            self.tick("film", t)
            return
        self.draw_film(f, 0.0 if over else grain, vig)      # stages 8, 9
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
        self.mark("py")
        vao.render(mgl.TRIANGLE_STRIP)
        self.mark("out")
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
