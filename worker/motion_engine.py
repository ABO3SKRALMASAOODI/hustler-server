"""HTML/CSS motion-graphics engine: deterministic transparent overlay clips.

libass is excellent at subtitles but it cannot draw what premium short-form
motion design is made of: gaussian glows, gradient fills on type, real 3D
perspective, masks, SVG icon draws, blur-to-sharp springs, UI cards, counters
or particles. Headless Chromium can, and LLM editors author HTML/CSS fluently.
This module turns one motion item (a library template + params, or authored
HTML) into a transparent clip that the ffmpeg graph overlays like any other
input.

Determinism: the page never sees real time. For each output frame the engine
calls ``window.__mgSeek(t)`` (worker/motion/runtime.js), which seeks every
CSS/Web animation and runs registered JS choreography for that instant, then
captures a transparent PNG. Identical inputs therefore produce identical
frames, which is what makes the content-addressed cache safe.

Speed: (1) frames where nothing can change re-use the previous capture
(``__mgSeek`` reports activity exactly for CSS animations and for declared
JS windows); (2) capture is clipped to the item's drawing box; (3) items
render concurrently on separate pages of one browser; (4) clips are cached
by content hash, so a repair elsewhere in the edit never re-renders them.

Safety: every network request except the document, bundled fonts and the
explicit local asset map is aborted, scripts get a per-frame deadline, and
the page runs in a disposable context.
"""

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
MOTION_DIR = os.path.join(HERE, "motion")
TEMPLATES_DIR = os.path.join(MOTION_DIR, "templates")
FONTS_DIR = os.path.join(HERE, "fonts")

# Bump when runtime.js, the document wrapper or capture semantics change:
# it is part of every cache key.
ENGINE_VERSION = "mg-1"
DESIGN_W = 1080           # templates are authored in a 1080-wide CSS space
ORIGIN = "https://mg.valmera.invalid"
MAX_DURATION_S = 120.0
MAX_HTML_BYTES = 200_000
FRAME_EVAL_TIMEOUT_MS = 4000
PAGE_LOAD_TIMEOUT_MS = 20000
DEFAULT_PAGES = int(os.getenv("MOTION_PAGES", "3") or 3)
CACHE_DIR = os.getenv("MOTION_CACHE_DIR") or os.path.join(
    tempfile.gettempdir(), "valmera-motion-cache")
CACHE_MAX_BYTES = int(os.getenv("MOTION_CACHE_MAX_BYTES", str(3 * 1024 ** 3)))

# family name used in CSS -> (file, weight, style)
FONT_FACES = [
    ("Inter Display", "InterDisplay-Bold.ttf", 700, "normal"),
    ("Inter Display", "InterDisplay-BoldItalic.ttf", 700, "italic"),
    ("Inter Display", "InterDisplay-ExtraBold.ttf", 800, "normal"),
    ("Inter Display", "InterDisplay-Black.ttf", 900, "normal"),
    ("Instrument Serif", "InstrumentSerif-Regular.ttf", 400, "normal"),
    ("Instrument Serif", "InstrumentSerif-Italic.ttf", 400, "italic"),
    ("DM Serif Display", "DMSerifDisplay-Regular.ttf", 400, "normal"),
    ("DM Serif Display", "DMSerifDisplay-Italic.ttf", 400, "italic"),
    ("Playfair Display", "PlayfairDisplay-Black.ttf", 900, "normal"),
    ("Playfair Display", "PlayfairDisplay-BlackItalic.ttf", 900, "italic"),
    ("Anton", "Anton-Regular.ttf", 400, "normal"),
    ("Bebas Neue", "BebasNeue-Regular.ttf", 400, "normal"),
    ("Archivo Black", "ArchivoBlack-Regular.ttf", 400, "normal"),
    ("Montserrat", "Montserrat-Bold.ttf", 700, "normal"),
    ("Poppins", "Poppins-Black.ttf", 900, "normal"),
    ("Plus Jakarta Sans", "PlusJakartaSans-ExtraBold.ttf", 800, "normal"),
    ("Syne", "Syne-ExtraBold.ttf", 800, "normal"),
]
OPTIONAL_FONT_FACES = [
    # Added with the motion engine; absent files are skipped so an older
    # image never references a font it does not ship.
    ("Inter", "Inter-Regular.ttf", 400, "normal"),
    ("Inter", "Inter-Medium.ttf", 500, "normal"),
    ("Inter", "Inter-SemiBold.ttf", 600, "normal"),
    ("Inter", "Inter-Bold.ttf", 700, "normal"),
    ("Inter", "Inter-ExtraBold.ttf", 800, "normal"),
    ("Inter", "Inter-Black.ttf", 900, "normal"),
    ("JetBrains Mono", "JetBrainsMono-Bold.ttf", 700, "normal"),
    ("JetBrains Mono", "JetBrainsMono-Regular.ttf", 400, "normal"),
    ("Caveat", "Caveat-Bold.ttf", 700, "normal"),
    ("Space Grotesk", "SpaceGrotesk-Bold.ttf", 700, "normal"),
    ("Manrope", "Manrope-ExtraBold.ttf", 800, "normal"),
]


# Headless Chrome otherwise paces screenshots to its frame clock (~50 ms per
# capture, measured). Every frame here is an explicit seek + capture, so the
# vsync/frame-rate limiter only adds latency: disabling it measured 8.6x
# faster with identical pixels. (Do NOT add
# --run-all-compositor-stages-before-draw without begin-frame control: it
# deadlocks capture.)
CHROME_ARGS = [
    "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
    "--mute-audio", "--hide-scrollbars", "--font-render-hinting=none",
    "--disable-background-networking", "--disable-extensions",
    "--disable-gpu-vsync", "--disable-frame-rate-limit",
    "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
]


class MotionRenderError(RuntimeError):
    """A motion item could not be rendered; the message is user-relayable."""


def available():
    """True when Playwright and its Chromium are importable/installed."""
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


def font_faces():
    out = []
    for fam, fn, weight, style in FONT_FACES + OPTIONAL_FONT_FACES:
        if os.path.exists(os.path.join(FONTS_DIR, fn)):
            out.append((fam, fn, weight, style))
    return out


def font_families():
    seen = []
    for fam, *_ in font_faces():
        if fam not in seen:
            seen.append(fam)
    return seen


def _font_css():
    rules = []
    for fam, fn, weight, style in font_faces():
        rules.append(
            "@font-face{font-family:'%s';src:url('%s/fonts/%s') format('truetype');"
            "font-weight:%d;font-style:%s;font-display:block}" % (fam, ORIGIN, fn, weight, style))
    return "\n".join(rules)


_RUNTIME_CACHE = {}


def _runtime_js():
    path = os.path.join(MOTION_DIR, "runtime.js")
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _RUNTIME_CACHE:
        with open(path, "r", encoding="utf-8") as f:
            _RUNTIME_CACHE.clear()
            _RUNTIME_CACHE[key] = f.read()
    return _RUNTIME_CACHE[key]


BASE_CSS = """
*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;background:transparent!important;overflow:hidden;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision}
body{width:var(--W);height:var(--H);position:relative;font-family:'Inter Display','Inter',sans-serif;color:#fff}
.mg-root{position:absolute;inset:0}
.mg-w,.mg-c{display:inline-block;white-space:pre}
"""


def _json_for_script(obj):
    # JSON inside <script>: neutralise "</script" and HTML comment openers.
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def design_size(width, height):
    """CSS design space for an output size: 1080 wide, same aspect."""
    w = DESIGN_W
    h = int(round(DESIGN_W * float(height) / float(width)))
    return w, h


def build_document(body, *, params=None, theme=None, duration=1.0, fps=30.0,
                   design_w=DESIGN_W, design_h=1920):
    """Wrap template/authored HTML with fonts, the runtime and its inputs."""
    init = {"params": params or {}, "theme": theme or {}, "duration": float(duration),
            "fps": float(fps), "W": int(design_w), "H": int(design_h)}
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_font_css()}\n:root{{--W:{int(design_w)}px;--H:{int(design_h)}px}}{BASE_CSS}</style>"
        f"<script>{_runtime_js()}</script>"
        f"<script>Object.assign(window.MG,{_json_for_script(init)});</script>"
        "</head><body>"
        f"{body}"
        "</body></html>")


_TEMPLATE_CACHE = {}


def template_body(name):
    """Return the HTML body of a library template (file in motion/templates)."""
    safe = "".join(ch for ch in str(name) if ch.isalnum() or ch in "-_")
    path = os.path.join(TEMPLATES_DIR, f"{safe}.html")
    if not os.path.exists(path):
        raise MotionRenderError(f"unknown motion template '{name}'")
    st = os.stat(path)
    key = (path, st.st_mtime_ns)
    if key not in _TEMPLATE_CACHE:
        with open(path, "r", encoding="utf-8") as f:
            _TEMPLATE_CACHE[key] = f.read()
    return _TEMPLATE_CACHE[key]


@dataclass
class RenderJob:
    """One clip to render. Times are item-local seconds."""
    html: str                       # complete document from build_document
    out_w: int                      # output canvas size in pixels
    out_h: int
    fps: float
    duration: float
    box: list = None                # [x0,y0,x1,y1] design px, or None = auto/template
    assets: dict = field(default_factory=dict)   # url path -> local file
    label: str = "motion"
    t0: float = 0.0                 # composition time of the first frame

    def key(self):
        h = hashlib.sha256()
        h.update(ENGINE_VERSION.encode())
        h.update(_runtime_js().encode())
        h.update(self.html.encode("utf-8"))
        h.update(json.dumps([self.out_w, self.out_h, round(self.fps, 4),
                             round(self.duration, 4), round(self.t0, 4), self.box],
                            sort_keys=True).encode())
        for k in sorted(self.assets or {}):
            p = self.assets[k]
            try:
                st = os.stat(p)
                h.update(f"{k}:{st.st_size}:{int(st.st_mtime)}".encode())
            except OSError:
                h.update(f"{k}:missing".encode())
        return h.hexdigest()[:40]


@dataclass
class RenderedClip:
    path: str          # transparent .mov (qtrle, argb)
    x: int             # placement on the output canvas (pixels)
    y: int
    w: int
    h: int
    frames: int
    captured: int      # frames actually screenshotted (rest re-used)
    cached: bool
    seconds: float
    empty: bool = False   # nothing visible at all — caller may skip it


def _ffmpeg():
    return shutil.which("ffmpeg") or "ffmpeg"


def _alpha_bbox(png_bytes):
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(png_bytes))
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    a = im.getchannel("A")
    # Threshold through a LUT (C speed) rather than a per-pixel lambda.
    return a.point([0] * 4 + [255] * 252).getbbox()


def _even_box(x0, y0, x1, y1, W, H):
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(W, int(round(x1))), min(H, int(round(y1)))
    if (x1 - x0) % 2:
        x1 = min(W, x1 + 1) if x1 < W else x1
        if (x1 - x0) % 2:
            x0 = max(0, x0 - 1)
    if (y1 - y0) % 2:
        y1 = min(H, y1 + 1) if y1 < H else y1
        if (y1 - y0) % 2:
            y0 = max(0, y0 - 1)
    return x0, y0, x1, y1


def _cache_get(key):
    path = os.path.join(CACHE_DIR, key + ".mov")
    meta = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path) and os.path.exists(meta):
        try:
            with open(meta) as f:
                m = json.load(f)
            os.utime(path, None)
            return path, m
        except Exception:
            return None
    return None


def _cache_put(key, src, meta):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        dst = os.path.join(CACHE_DIR, key + ".mov")
        tmp = dst + f".{os.getpid()}.{threading.get_ident()}.tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        with open(os.path.join(CACHE_DIR, key + ".json"), "w") as f:
            json.dump(meta, f)
        _cache_prune()
    except Exception:
        pass


def _cache_prune():
    try:
        files = [os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".mov")]
        files.sort(key=lambda p: os.stat(p).st_mtime)
        total = sum(os.path.getsize(p) for p in files)
        while files and total > CACHE_MAX_BYTES:
            p = files.pop(0)
            total -= os.path.getsize(p)
            for q in (p, p[:-4] + ".json"):
                try:
                    os.remove(q)
                except OSError:
                    pass
    except Exception:
        pass


async def _route_factory(job):
    font_dir = FONTS_DIR
    doc = job.html.encode("utf-8")

    async def handler(route):
        url = route.request.url
        try:
            if url == ORIGIN + "/" or url == ORIGIN:
                return await route.fulfill(body=doc, content_type="text/html; charset=utf-8")
            if url.startswith(ORIGIN + "/fonts/"):
                fn = os.path.basename(url.split("?", 1)[0])
                p = os.path.join(font_dir, fn)
                if os.path.isfile(p):
                    return await route.fulfill(path=p, content_type="font/ttf")
            if url.startswith(ORIGIN + "/assets/"):
                rel = url[len(ORIGIN) + len("/assets/"):].split("?", 1)[0]
                p = (job.assets or {}).get(rel)
                if p and os.path.isfile(p):
                    return await route.fulfill(path=p)
            return await route.abort()
        except Exception:
            try:
                await route.abort()
            except Exception:
                pass
    return handler


async def _render_one(browser, job, out_path, deadline):
    t_start = time.monotonic()
    dw, dh = design_size(job.out_w, job.out_h)
    scale = float(job.out_w) / float(dw)
    W, H = int(job.out_w), int(job.out_h)
    n_frames = max(1, int(round(job.duration * job.fps)))
    # Output size comes from the capture clip's scale: headless Chrome
    # ignores device_scale_factor < 1 for screenshots (measured), while
    # clip.scale re-rasterizes at the requested size (crisp at any scale).
    ctx = await browser.new_context(viewport={"width": dw, "height": dh},
                                    device_scale_factor=1)
    try:
        await ctx.route("**/*", await _route_factory(job))
        page = await ctx.new_page()
        page.set_default_timeout(FRAME_EVAL_TIMEOUT_MS)
        await page.goto(ORIGIN + "/", wait_until="load", timeout=PAGE_LOAD_TIMEOUT_MS)
        await page.evaluate("""async () => {
            await document.fonts.ready;
            await Promise.all(Array.from(document.images).map(i => i.complete ? 0 :
              new Promise(r => { i.onload = i.onerror = r; })));
            if (window.MG && MG.ready) await MG.ready;
            return true; }""")
        errs = await page.evaluate("window.__mgErrors || []")
        if errs:
            raise MotionRenderError(f"{job.label}: script error: {str(errs[0])[:200]}")
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("Emulation.setDefaultBackgroundColorOverride",
                       {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})

        async def capture(clip=None, shrink=1.0):
            # clip is in OUTPUT pixels; CDP wants CSS px plus the output scale.
            c = clip or (0, 0, W, H)
            params = {"format": "png", "optimizeForSpeed": True, "captureBeyondViewport": False,
                      "clip": {"x": c[0] / scale, "y": c[1] / scale,
                               "width": (c[2] - c[0]) / scale,
                               "height": (c[3] - c[1]) / scale,
                               "scale": scale * shrink}}
            r = await cdp.send("Page.captureScreenshot", params)
            return base64.b64decode(r["data"])

        # ---- drawing box (output pixels) --------------------------------
        box = job.box
        if box is None:
            box = await page.evaluate("window.MG && MG.box ? MG.box : null")
        if box:
            x0, y0, x1, y1 = [float(v) * scale for v in box]
            pad = 24 * scale
            bx = _even_box(x0 - pad, y0 - pad, x1 + pad, y1 + pad, W, H)
        else:
            # Sample the clip and take the union of visible alpha. Dense
            # enough to catch entrances/exits; padded for glows.
            samples = sorted(set([0, n_frames - 1] + [
                int(i * (n_frames - 1) / 23) for i in range(24)]))
            union = None
            shrink = 0.25
            for fi in samples:
                await page.evaluate("t => window.__mgSeek(t)", job.t0 + fi / job.fps)
                bb = _alpha_bbox(await capture(shrink=shrink))
                if bb:
                    # sampled at a quarter of CSS size; map back to output px
                    k = 1.0 / shrink
                    bb = tuple(v * k for v in bb)
                    union = bb if union is None else (min(union[0], bb[0]), min(union[1], bb[1]),
                                                      max(union[2], bb[2]), max(union[3], bb[3]))
            if union is None:
                return RenderedClip(out_path, 0, 0, 0, 0, n_frames, len(samples), False,
                                    time.monotonic() - t_start, empty=True)
            pad = 40 * scale
            bx = _even_box(union[0] - pad, union[1] - pad, union[2] + pad, union[3] + pad, W, H)
        if bx[2] - bx[0] < 2 or bx[3] - bx[1] < 2:
            return RenderedClip(out_path, 0, 0, 0, 0, n_frames, 0, False,
                                time.monotonic() - t_start, empty=True)
        clip = None if bx == (0, 0, W, H) else bx

        cmd = [_ffmpeg(), "-v", "error", "-y", "-f", "image2pipe", "-framerate",
               f"{job.fps:.6f}", "-c:v", "png", "-i", "-", "-c:v", "qtrle",
               "-pix_fmt", "argb", "-r", f"{job.fps:.6f}", out_path]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        captured, prev = 0, None
        try:
            for i in range(n_frames):
                if time.monotonic() > deadline:
                    raise MotionRenderError(f"{job.label}: motion render exceeded its time budget")
                active = await page.evaluate("t => window.__mgSeek(t)", job.t0 + i / job.fps)
                if active == 2:
                    errs = await page.evaluate("window.__mgErrors || []")
                    raise MotionRenderError(f"{job.label}: script error at "
                                            f"{job.t0 + i / job.fps:.2f}s: {str(errs[:1])[:200]}")
                if active or prev is None:
                    prev = await capture(clip)
                    captured += 1
                proc.stdin.write(prev)
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait(timeout=120) != 0:
                raise MotionRenderError(f"{job.label}: encoder failed: {err[-300:]}")
        except BaseException:
            try:
                proc.kill()
            except Exception:
                pass
            raise
        errs = await page.evaluate("window.__mgErrors || []")
        if errs:
            raise MotionRenderError(f"{job.label}: script error: {str(errs[0])[:200]}")
        return RenderedClip(out_path, bx[0], bx[1], bx[2] - bx[0], bx[3] - bx[1],
                            n_frames, captured, False, time.monotonic() - t_start)
    finally:
        try:
            await ctx.close()
        except Exception:
            pass


async def _render_all(jobs, out_dir, pages, budget_s):
    from playwright.async_api import async_playwright
    deadline = time.monotonic() + budget_s
    results = [None] * len(jobs)
    todo = []
    for i, job in enumerate(jobs):
        if job.duration <= 0 or job.duration > MAX_DURATION_S:
            raise MotionRenderError(f"{job.label}: duration must be 0-{MAX_DURATION_S:.0f}s")
        if len(job.html.encode("utf-8")) > MAX_HTML_BYTES:
            raise MotionRenderError(f"{job.label}: HTML exceeds {MAX_HTML_BYTES // 1000} KB")
        key = job.key()
        hit = _cache_get(key)
        if hit:
            path, m = hit
            local = os.path.join(out_dir, f"mg_{key}.mov")
            if not os.path.exists(local):
                try:
                    os.link(path, local)
                except OSError:
                    shutil.copyfile(path, local)
            results[i] = RenderedClip(local, m["x"], m["y"], m["w"], m["h"], m["frames"],
                                      0, True, 0.0, bool(m.get("empty")))
        else:
            todo.append((i, job, key))
    if not todo:
        return results
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=CHROME_ARGS)
        try:
            sem = asyncio.Semaphore(max(1, pages))

            async def run(i, job, key):
                async with sem:
                    out = os.path.join(out_dir, f"mg_{key}.mov")
                    clip = await _render_one(browser, job, out, deadline)
                    results[i] = clip
                    if not clip.empty and os.path.exists(out):
                        _cache_put(key, out, {"x": clip.x, "y": clip.y, "w": clip.w,
                                              "h": clip.h, "frames": clip.frames})
            await asyncio.gather(*(run(i, j, k) for i, j, k in todo))
        finally:
            await browser.close()
    return results


def render_jobs(jobs, out_dir, pages=None, budget_s=900.0):
    """Render RenderJobs to transparent clips in out_dir. Returns RenderedClip
    list aligned with jobs. Raises MotionRenderError with a relayable reason."""
    if not jobs:
        return []
    if not available():
        raise MotionRenderError("motion graphics are not installed on this deployment")
    os.makedirs(out_dir, exist_ok=True)
    pages = pages or DEFAULT_PAGES
    coro = _render_all(list(jobs), out_dir, pages, budget_s)
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False
    if not running:
        try:
            return asyncio.run(coro)
        except MotionRenderError:
            raise
        except Exception as e:  # playwright/browser failures
            raise MotionRenderError(f"motion renderer failed: {str(e).splitlines()[0][:240]}")
    # Called from inside an event loop (tests/servers): run in a helper thread.
    box = {}

    def target():
        try:
            box["r"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join()
    if "e" in box:
        e = box["e"]
        if isinstance(e, MotionRenderError):
            raise e
        raise MotionRenderError(f"motion renderer failed: {str(e).splitlines()[0][:240]}")
    return box["r"]


async def _probe_all(jobs, times_list, budget_s):
    from playwright.async_api import async_playwright
    out = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=CHROME_ARGS)
        try:
            for job, times in zip(jobs, times_list):
                dw, dh = design_size(job.out_w, job.out_h)
                ctx = await browser.new_context(viewport={"width": dw, "height": dh},
                                                device_scale_factor=1)
                try:
                  try:
                    await ctx.route("**/*", await _route_factory(job))
                    page = await ctx.new_page()
                    page.set_default_timeout(FRAME_EVAL_TIMEOUT_MS)
                    await page.goto(ORIGIN + "/", wait_until="load", timeout=PAGE_LOAD_TIMEOUT_MS)
                    await page.evaluate("async () => { await document.fonts.ready; return true; }")
                    cdp = await ctx.new_cdp_session(page)
                    await cdp.send("Emulation.setDefaultBackgroundColorOverride",
                                   {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
                    visible, bboxes = 0, []
                    for t in times:
                        await page.evaluate("t => window.__mgSeek(t)", float(t))
                        r = await cdp.send("Page.captureScreenshot", {
                            "format": "png", "optimizeForSpeed": True,
                            "clip": {"x": 0, "y": 0, "width": dw, "height": dh, "scale": 0.25}})
                        bb = _alpha_bbox(base64.b64decode(r["data"]))
                        if bb:
                            visible += 1
                            bboxes.append([round(v * 4.0 / dw, 3) if i % 2 == 0 else round(v * 4.0 / dh, 3)
                                           for i, v in enumerate(bb)])
                    errs = await page.evaluate("window.__mgErrors || []")
                    out.append({"errors": [str(e)[:200] for e in errs], "visible_frames": visible,
                                "samples": len(times), "bboxes": bboxes})
                  except Exception as e:  # noqa: BLE001 — one bad composition
                    out.append({"errors": [str(e).splitlines()[0][:200]], "visible_frames": 0,
                                "samples": len(times), "bboxes": []})
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


def probe(jobs, times_list, budget_s=60.0):
    """Cheap write-time check: load each composition, seek the given item-local
    times at quarter scale, and report script errors and visible coverage
    (bboxes as frame fractions). Raises MotionRenderError when the browser
    itself cannot run."""
    if not available():
        raise MotionRenderError("motion graphics are not installed on this deployment")
    coro = _probe_all(list(jobs), list(times_list), budget_s)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        try:
            return asyncio.run(coro)
        except Exception as e:  # noqa: BLE001
            raise MotionRenderError(f"motion check failed: {str(e).splitlines()[0][:200]}")
    box = {}

    def target():
        try:
            box["r"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["e"] = e
    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join()
    if "e" in box:
        raise MotionRenderError(f"motion check failed: {str(box['e']).splitlines()[0][:200]}")
    return box["r"]
