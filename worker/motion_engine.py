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
import contextvars
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
# Shared page libraries (Wave 0 seam, track 0B): a motion/lib/<name>.js is
# appended to the runtime (name order) of every document that uses it
# (libs_used: the page names MG.<name> or carries the _<name> param), and
# every library enters every clip cache key. Device tracks add a file here
# (1C: paint.js); nobody edits runtime.js for a library.
LIB_DIR = os.path.join(MOTION_DIR, "lib")
FONTS_DIR = os.path.join(HERE, "fonts")

# Bump when runtime.js, the document wrapper or capture semantics change:
# it is part of every cache key.
ENGINE_VERSION = "mg-4"
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
MOTION_FONTS_DIR = os.path.join(MOTION_DIR, "fonts")
# Motion-only families (worker/motion/fonts, never on libass's fontsdir so
# caption rendering cannot change). Variable fonts declare weight ranges.
# (family, file, weight or "lo hi", style, stretch or None)
MOTION_FONT_FACES = [
    ("Inter", "Inter-Variable.ttf", "100 900", "normal", None),
    ("Inter", "Inter-Italic-Variable.ttf", "100 900", "italic", None),
    ("Inter Tight", "InterTight-Variable.ttf", "100 900", "normal", None),
    ("Inter Tight", "InterTight-Italic-Variable.ttf", "100 900", "italic", None),
    ("JetBrains Mono", "JetBrainsMono-Variable.ttf", "100 800", "normal", None),
    ("Caveat", "Caveat-Variable.ttf", "400 700", "normal", None),
    ("Space Grotesk", "SpaceGrotesk-Variable.ttf", "300 700", "normal", None),
    ("Manrope", "Manrope-Variable.ttf", "200 800", "normal", None),
    ("Pinyon Script", "PinyonScript-Regular.ttf", 400, "normal", None),
    ("Great Vibes", "GreatVibes-Regular.ttf", 400, "normal", None),
    ("Yellowtail", "Yellowtail-Regular.ttf", 400, "normal", None),
    ("Bodoni Moda", "BodoniModa-Variable.ttf", "400 900", "normal", None),
    ("Bodoni Moda", "BodoniModa-Italic-Variable.ttf", "400 900", "italic", None),
    ("Playfair Display", "PlayfairDisplay-Variable.ttf", "400 900", "normal", None),
    ("Playfair Display", "PlayfairDisplay-Italic-Variable.ttf", "400 900", "italic", None),
    ("Unbounded", "Unbounded-Variable.ttf", "200 900", "normal", None),
    ("Montserrat", "Montserrat-Variable.ttf", "100 900", "normal", None),
    ("Poppins", "Poppins-Light.ttf", 300, "normal", None),
    ("Poppins", "Poppins-ExtraBold.ttf", 800, "normal", None),
    ("Archivo", "Archivo-Variable.ttf", "100 900", "normal", "62% 125%"),
]


def _manifest_faces():
    """Wave 2 (track 2B, INTERFACES_W2.md §3.3): the faces listed in
    motion/fonts/manifest.json join MOTION_FONT_FACES (the typesetter
    measures the same files). A missing or unreadable manifest adds none."""
    try:
        with open(os.path.join(MOTION_FONTS_DIR, "manifest.json"),
                  encoding="utf-8") as f:
            faces = json.load(f).get("faces") or []
        return [(str(x["family"]), str(x["file"]), x["weight"],
                 str(x.get("style") or "normal"), x.get("stretch"))
                for x in faces if isinstance(x, dict) and x.get("file")]
    except (OSError, ValueError, KeyError, TypeError):
        return []


_MANIFEST_FACES = _manifest_faces()
MOTION_FONT_FACES = MOTION_FONT_FACES + _MANIFEST_FACES
# Wave 2's faces are declared on a page only when the page names them (a
# typeset page carries its LayoutDoc's families): every other page keeps its
# @font-face block as it was, so its document size does not grow.
_MANIFEST_FAMILIES = {f[0] for f in _MANIFEST_FACES}
OPTIONAL_FONT_FACES = []


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


def package_installed():
    """The Playwright package imports. Every worker image installs it (it is
    in requirements.txt), including lanes that ship no browser, so this
    answers "can a motion item be authored here", not "can it render here"."""
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    return True


def authorable():
    """Motion items and motion captions may be written into an EDL for this
    deployment. The agent lanes that author edits ship no browser; the
    interactive and batch render lanes that draw them do, and a lane without
    one falls back (libass captions; graphics skipped with a warning). So the
    authoring question is "does this deployment ship the engine", answered by
    the package, never by this process's own Chromium. MOTION_ENGINE=off is
    the operator kill switch."""
    if os.getenv("MOTION_ENGINE", "").strip().lower() in ("0", "off", "false", "no"):
        return False
    return package_installed()


_AVAILABILITY = {"ok": None, "reason": ""}
_AVAILABILITY_LOCK = threading.Lock()
_BROWSER_PROBE_TIMEOUT_S = 30.0


def _chromium_executable_path():
    """Ask Playwright's driver where it would launch Chromium from.

    The driver is the authority (it resolves PLAYWRIGHT_BROWSERS_PATH and the
    pinned browser revision). It runs in a helper thread because the sync API
    refuses to start inside a running asyncio loop."""
    box = {}

    def target():
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                box["path"] = p.chromium.executable_path
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    th = threading.Thread(target=target, daemon=True,
                          name="motion-browser-probe")
    th.start()
    th.join(_BROWSER_PROBE_TIMEOUT_S)
    if th.is_alive():
        raise TimeoutError("Playwright driver did not answer")
    if "error" in box:
        raise box["error"]
    return box.get("path") or ""


def _is_executable(path):
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def _browser_installed(executable_path):
    """Full Chromium, or the headless shell Playwright >=1.49 launches for
    headless mode from the same browsers directory."""
    if _is_executable(executable_path):
        return True
    marker = os.sep + "chromium-"
    if marker not in executable_path:
        return False
    root = executable_path.split(marker, 1)[0]
    try:
        names = os.listdir(root)
    except OSError:
        return False
    for name in names:
        if not name.startswith("chromium_headless_shell-"):
            continue
        for sub in ("chrome-linux", "chrome-mac", "chrome-win"):
            for exe in ("headless_shell", "headless_shell.exe"):
                if _is_executable(os.path.join(root, name, sub, exe)):
                    return True
    return False


def available():
    """True when THIS process can render: Playwright imports AND its Chromium
    executable is installed. Checked once per process.

    Importing playwright alone was the old test, and every image passes it —
    including Cloudflare lanes built without a browser
    (PLAYWRIGHT_BROWSERS_PATH=/tmp/no-local-browser, FULL_COMPUTE=0). Those
    lanes then failed late inside the launch with a multi-line driver error.
    Now they report unavailability up front; ``unavailable_reason()`` says
    why."""
    cached = _AVAILABILITY["ok"]
    if cached is not None:
        return cached
    with _AVAILABILITY_LOCK:
        if _AVAILABILITY["ok"] is not None:
            return _AVAILABILITY["ok"]
        if not package_installed():
            _AVAILABILITY.update(ok=False, reason="the Playwright package is "
                                 "not installed on this render lane")
            return False
        try:
            path = _chromium_executable_path()
        except TimeoutError as e:
            # Unknown is not "no": report unavailable now but ask again on
            # the next call instead of caching a driver hiccup forever.
            _AVAILABILITY["reason"] = f"browser probe failed ({e})"
            return False
        except Exception as e:  # noqa: BLE001
            _AVAILABILITY.update(
                ok=False, reason="the Playwright driver could not start "
                f"({str(e).splitlines()[0][:160] if str(e) else type(e).__name__})")
            return False
        if _browser_installed(path):
            _AVAILABILITY.update(ok=True, reason="")
        else:
            _AVAILABILITY.update(
                ok=False, reason="headless Chromium is not installed on this "
                "render lane")
        return _AVAILABILITY["ok"]


def unavailable_reason():
    """Why available() is False ('' when it is True). Never probes twice: a
    probe that timed out leaves its reason without caching the answer."""
    if _AVAILABILITY["ok"] is None and not _AVAILABILITY["reason"]:
        available()
    if _AVAILABILITY["ok"] is True:
        return ""
    return (_AVAILABILITY["reason"]
            or "the motion graphics renderer is not available here")


def _reset_availability_cache():
    """Tests and long-lived processes after a browser install."""
    with _AVAILABILITY_LOCK:
        _AVAILABILITY.update(ok=None, reason="")


def font_faces():
    """[(family, url_path, weight, style, stretch)] for every installed face."""
    out = []
    for fam, fn, weight, style in FONT_FACES:
        if os.path.exists(os.path.join(FONTS_DIR, fn)):
            out.append((fam, f"fonts/{fn}", weight, style, None))
    for fam, fn, weight, style, stretch in MOTION_FONT_FACES:
        if os.path.exists(os.path.join(MOTION_FONTS_DIR, fn)):
            out.append((fam, f"mfonts/{fn}", weight, style, stretch))
    return out


def font_families():
    seen = []
    for fam, *_ in font_faces():
        if fam not in seen:
            seen.append(fam)
    return seen


def _font_css(page_text=None):
    rules = []
    for fam, path, weight, style, stretch in font_faces():
        if page_text is not None and fam in _MANIFEST_FAMILIES and \
                fam not in page_text:
            continue
        rules.append(
            "@font-face{font-family:'%s';src:url('%s/%s') format('truetype');"
            "font-weight:%s;font-style:%s;%sfont-display:block}" % (
                fam, ORIGIN, path, weight, style,
                f"font-stretch:{stretch};" if stretch else ""))
    return "\n".join(rules)


_RUNTIME_CACHE = {}


def strip_line_comments(src):
    """JavaScript without its whole-line comments: ``//`` lines and ``/* */``
    blocks that start a line (code with a trailing comment is kept). The
    runtime is documented for its readers, not for every page: its comments
    are a third of it, and every composition (a caption segment with its
    plate grids above all) carries the runtime under MAX_HTML_BYTES. Only for
    sources without multi-line template literals (runtime.js)."""
    out, block = [], False
    for line in src.split("\n"):
        t = line.strip()
        if block:
            if "*/" in t:
                block = False
                rest = t.split("*/", 1)[1].strip()
                if rest:
                    out.append(rest)
            continue
        if t.startswith("//"):
            continue
        if t.startswith("/*") and "*/" not in t[2:]:
            block = True
            continue
        if t.startswith("/*") and t.endswith("*/"):
            continue
        out.append(line)
    return "\n".join(out)


def _lib_files():
    try:
        names = sorted(n for n in os.listdir(LIB_DIR) if n.endswith(".js"))
    except OSError:
        return []
    return [os.path.join(LIB_DIR, n) for n in names]


def _lib_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def _runtime_js(libs=None):
    """runtime.js, then motion/lib/*.js in name order, comments stripped.

    ``libs``: the library names (file stems) a document uses; None means
    every library (what a clip key hashes, so a change to any library
    re-keys every clip). With no library this is runtime.js exactly as
    before Wave 0."""
    path = os.path.join(MOTION_DIR, "runtime.js")
    lib_files = _lib_files()
    if libs is not None:
        want = set(libs)
        lib_files = [p for p in lib_files if _lib_name(p) in want]
    files = [path] + lib_files
    key = tuple((p, st.st_mtime_ns, st.st_size)
                for p, st in ((p, os.stat(p)) for p in files))
    if key not in _RUNTIME_CACHE:
        chunks = []
        for p in files:
            with open(p, "r", encoding="utf-8") as f:
                chunks.append(strip_line_comments(f.read()))
        if len(_RUNTIME_CACHE) >= 16:
            _RUNTIME_CACHE.clear()
        _RUNTIME_CACHE[key] = "\n".join(chunks)
    return _RUNTIME_CACHE[key]


def libs_used(body, params=None):
    """The motion/lib libraries a document uses: ``<name>.js`` joins it when
    the page names ``MG.<name>`` or its params carry ``_<name>`` (beats.js:
    ``MG.beats`` / ``_beats``). Every other document carries runtime.js
    alone, byte for byte as before Wave 0: each caption segment holds the
    runtime under the 200 KB document cap (Wave 0 final review: joining
    every library to every page pushed caption pages past their headroom)."""
    text = str(body or "")
    keys = set((params or {}).keys()) if isinstance(params, dict) else set()
    return [n for n in (_lib_name(p) for p in _lib_files())
            if f"MG.{n}" in text or f"_{n}" in keys]


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


# params._beats (Wave 0 seam, track 0B): a rhythm the page can move on,
# in COMPOSITION seconds — {"t": beats, "down": downbeats, "on": [[t, cls,
# strength], ...], "src": "music" | "speech"}. Bounded so a dense map never
# pushes a composition over MAX_HTML_BYTES.
BEATS_MAX = 512


def _beat_list(values, lo, hi):
    out = []
    for v in values or []:
        try:
            t = float(v)
        except (TypeError, ValueError):
            continue
        if lo - 1e-6 <= t <= hi + 1e-6:
            out.append(round(t, 3))
    return sorted(set(out))[:BEATS_MAX]


def normalize_beats(beats, duration):
    """The ``_beats`` param from a loose description: sorted, deduplicated,
    inside [0, duration], at most BEATS_MAX per list. None or empty: None."""
    if not beats:
        return None
    hi = max(0.0, float(duration or 0.0))
    if isinstance(beats, (list, tuple)):
        beats = {"t": beats}
    out = {"t": _beat_list(beats.get("t"), 0.0, hi),
           "down": _beat_list(beats.get("down"), 0.0, hi)}
    ons = []
    for o in beats.get("on") or []:
        try:
            t, cls, st = float(o[0]), str(o[1])[:12], float(o[2])
        except (TypeError, ValueError, IndexError):
            continue
        if -1e-6 <= t <= hi + 1e-6:
            ons.append([round(t, 3), cls, round(max(0.0, min(1.0, st)), 2)])
    out["on"] = sorted(ons)[:BEATS_MAX]
    if beats.get("src") in ("music", "speech"):
        out["src"] = beats["src"]
    if not (out["t"] or out["down"] or out["on"]):
        return None
    return out


def beats_param(rhythm, *, start, end, phase=0.0, full_duration=None):
    """``_beats`` for a motion item at program [start, end) from a
    PROGRAM-clock RhythmMap (rhythm_types.to_program): every time moved to the
    item's composition clock (program t -> t - start + phase)."""
    if not rhythm:
        return None
    shift = float(phase or 0.0) - float(start)
    dur = float(full_duration or (float(end) - float(start) + float(phase or 0.0)))

    def mv(ts):
        return [float(t) + shift for t in ts or []
                if float(start) - 1e-6 <= float(t) < float(end) + 1e-6]
    return normalize_beats({
        "t": mv(rhythm.get("beats")), "down": mv(rhythm.get("downbeats")),
        "on": [[float(o["t"]) + shift, o.get("cls", ""), o.get("strength", 0)]
               for o in rhythm.get("onsets") or []
               if float(start) - 1e-6 <= float(o["t"]) < float(end) + 1e-6],
        "src": rhythm.get("source")}, dur)


def build_document(body, *, params=None, theme=None, duration=1.0, fps=30.0,
                   design_w=DESIGN_W, design_h=1920, plate=None, yields=None,
                   beats=None):
    """Wrap template/authored HTML with fonts, the runtime and its inputs.

    plate: the measured program picture under the item (worker/plate.py),
    {c, r, s: [{t, g}]} with g the c x r luma grid (base64 bytes, or a
    list), exposed as MG.plate. None leaves the document exactly as it was
    before plates existed.

    yields: a persistent graphic's hand-over windows (worker/motion_layer.
    yield_windows), {w: [[a, b], ...], out, in} in composition seconds,
    exposed as MG.yields — the runtime fades the whole page out before each
    window and back after it. None (every other item) adds nothing.

    beats: the item's rhythm (``beats_param`` / ``normalize_beats``), exposed
    as MG.params._beats and read through MG.beats (motion/lib/beats.js).
    None leaves the document exactly as it was."""
    beats = normalize_beats(beats, duration)
    if beats:
        params = dict(params or {}, _beats=beats)
    init = {"params": params or {}, "theme": theme or {}, "duration": float(duration),
            "fps": float(fps), "W": int(design_w), "H": int(design_h)}
    if plate:
        init["plate"] = plate
    if yields and yields.get("w"):
        init["yields"] = yields
    init_json = _json_for_script(init)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{_font_css(body + init_json)}\n:root{{--W:{int(design_w)}px;--H:{int(design_h)}px}}{BASE_CSS}</style>"
        f"<script>{_runtime_js(libs_used(body, params))}</script>"
        f"<script>Object.assign(window.MG,{init_json});</script>"
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
        h.update(_env_digest().encode())
        h.update(_runtime_js().encode())
        h.update(self.html.encode("utf-8"))
        h.update(json.dumps([self.out_w, self.out_h, round(self.fps, 4),
                             round(self.duration, 4), round(self.t0, 4), self.box],
                            sort_keys=True).encode())
        for k in sorted(self.assets or {}):
            try:
                h.update(f"{k}:{_asset_digest(self.assets[k])}".encode())
            except OSError:
                h.update(f"{k}:missing".encode())
        return h.hexdigest()[:40]


# Asset files enter the clip key by CONTENT (Wave 0 integration). The key
# addresses the shared clip store (clip_store, R2) across containers and
# projects: a size+mtime stamp misses on every cold container (a download's
# mtime is its download time) and lets two different same-size files written
# in the same second share a key — one project's picture in another's clip.
# Hashing is memoised per (path, size, mtime_ns), so a file is read once.
_ASSET_DIGESTS = {}
_ASSET_DIGESTS_MAX = 1024


def _asset_digest(path):
    st = os.stat(path)
    memo = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
    hit = _ASSET_DIGESTS.get(memo)
    if hit is None:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        hit = f"{st.st_size}:{h.hexdigest()}"
        if len(_ASSET_DIGESTS) >= _ASSET_DIGESTS_MAX:
            _ASSET_DIGESTS.clear()
        _ASSET_DIGESTS[memo] = hit
    return hit


# What draws a page besides its document (Wave 0 final review). The shared
# clip store outlives deploys — before it, every deploy started with empty
# container caches — so a clip key must also name what a deploy can change
# under an unchanged document: the bundled fonts (served by FILE NAME, so a
# replaced font file keeps every document byte), this module's capture code
# and the Chromium build. Hashed once per process (~11 MB of fonts, ~20 ms).
_ENV_DIGEST = None


def _env_digest():
    global _ENV_DIGEST
    if _ENV_DIGEST is not None:
        return _ENV_DIGEST
    h = hashlib.sha256()
    for d in (FONTS_DIR, MOTION_FONTS_DIR):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            names = []
        for n in names:
            p = os.path.join(d, n)
            if n.startswith(".") or not os.path.isfile(p):
                continue
            try:
                h.update(f"{os.path.basename(d)}/{n}:{_asset_digest(p)}"
                         .encode())
            except OSError:
                h.update(f"{n}:missing".encode())
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            h.update(f.read())
    except OSError:
        h.update(b"engine:unreadable")
    try:
        from importlib import metadata
        h.update(("playwright=" + metadata.version("playwright")).encode())
    except Exception:  # noqa: BLE001 — a lane without playwright never renders
        h.update(b"playwright=none")
    _ENV_DIGEST = h.hexdigest()[:16]
    return _ENV_DIGEST


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


# Two alpha thresholds over the same probe screenshot, because the two
# consumers ask different questions about a graphic (worker/keepout.py,
# worker/caption_carry.py):
#
# INK_ALPHA (200 of 255): what HIDES the speaker — glyphs, strokes, opaque
# plates and solid shapes. Soft scrims, washes and blooms stay below it (the
# counter's scrim peaks at .68 = 173). The face keep-out compares this box:
# a scrim darkens a face without hiding it, and it spreads across most of
# the frame, so counting it would put every scrimmed graphic "on the face"
# with no move able to clear it.
#
# COVER_ALPHA (102 of 255, 40%): what a CAPTION cannot share space with —
# the ink plus translucent plates and the dense core of a scrim or glow,
# where caption letters would read as part of the graphic. Only the falloff
# tails (shadows, glows, a scrim's edge) stay below it; a caption may sit on
# those. This is the box stored on the item (MotionItem.footprint) and the
# one the caption plan keeps every caption clear of.
#
# On the library's examples the two boxes agree within 0.01 of the frame
# except where a graphic has translucent furniture: the checklist's glass
# card (+0.05 each side), versus_split's split shading (full width), a
# scrimmed counter (+0.035 above and below the figure). Comparing the face
# with COVER would push a scrimmed counter a scrim's core further from the
# chin (into the caption band); placing captions against INK would set them
# on the checklist's card.
INK_ALPHA = 200
COVER_ALPHA = 102
VISIBLE_ALPHA = 4


def _alpha_bbox(png_bytes, threshold=VISIBLE_ALPHA):
    """Bbox of the pixels at or above ``threshold`` alpha (an int), or a
    tuple of bboxes for a tuple of thresholds, from one decode."""
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(png_bytes))
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    a = im.getchannel("A")
    # Threshold through a LUT (C speed) rather than a per-pixel lambda.
    def box(t):
        return a.point([0] * t + [255] * (256 - t)).getbbox()
    if isinstance(threshold, (tuple, list)):
        return tuple(box(int(t)) for t in threshold)
    return box(int(threshold))


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


# The worker's own helpers, imported on use. The backend loads THIS file by
# path under its plain name (validate_edl reaches it through
# motion_templates) without the worker directory on sys.path, so a top-level
# import of another worker module would take the backend down at import.
# Outside the worker both answer "not here".
def _clip_store():
    try:
        import clip_store
        return clip_store
    except ImportError:
        return None


def _fault(name):
    try:
        import faultinject
    except ImportError:
        return False
    return faultinject.fault(name)


def _cache_get(key):
    """(local .mov, meta) from the container-local LRU, else from the shared
    content-addressed store (clip_store, R2) — a cold container renders a
    warm project's graphics without starting Chromium (AT-0A-2) — else None.
    A key this render already asked the shared store for (_prefetch_shared)
    is not asked again. Never raises."""
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
    if key in _SHARED_ASKED.get():
        return None
    return _shared_get(key)


def _write_json_atomic(path, value):
    tmp = path + f".{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump(value, f)
    os.replace(tmp, path)


def _shared_get(key):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        store = _clip_store()
        if store is None:
            return None
        got = store.get(key, CACHE_DIR)
        if not got:
            return None
        mov, m = got
        if not mov or not os.path.exists(mov) or not isinstance(m, dict) \
                or not all(k in m for k in ("x", "y", "w", "h", "frames")):
            return None
        dst = os.path.join(CACHE_DIR, key + ".mov")
        if os.path.abspath(mov) != os.path.abspath(dst):
            tmp = dst + f".{os.getpid()}.{threading.get_ident()}.tmp"
            shutil.copyfile(mov, tmp)
            os.replace(tmp, dst)
        _write_json_atomic(os.path.join(CACHE_DIR, key + ".json"), m)
        return dst, m
    except Exception:
        return None


# Shared-store reads on a render's path (Wave 0 final review). A preview with
# 40 new caption segments misses the store 40 times; asked one by one, each
# miss is a network round trip on the render's critical path, and a slow R2
# (2 s connect + 10 s read per call) could stall a preview for minutes. So a
# render asks for every key its local LRU lacks AT ONCE, on a few threads,
# within one wall-clock budget; whatever has not answered by then renders in
# Chromium as it always did.
SHARED_GET_THREADS = 8
SHARED_GET_BUDGET_S = float(os.getenv("MOTION_SHARED_GET_BUDGET_S", "6") or 6)
_SHARED_ASKED = contextvars.ContextVar("motion_shared_asked",
                                       default=frozenset())


def _local_present(key):
    return (os.path.exists(os.path.join(CACHE_DIR, key + ".mov"))
            and os.path.exists(os.path.join(CACHE_DIR, key + ".json")))


def _prefetch_shared(keys, budget_s=None):
    """Copy every listed clip the local LRU lacks from the shared store into
    it, in parallel, waiting at most ``budget_s``. Returns the keys it asked
    for (hits are now local; misses and late answers render in Chromium).
    Never raises."""
    try:
        want = [k for k in dict.fromkeys(keys or ()) if not _local_present(k)]
        store = _clip_store()
        if not want or store is None or not store.available():
            return frozenset()
    except Exception:  # noqa: BLE001
        return frozenset()
    budget = SHARED_GET_BUDGET_S if budget_s is None else float(budget_s)
    lock = threading.Lock()
    queue = list(want)
    left = [len(want)]
    done = threading.Event()

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                key = queue.pop(0)
            try:
                _shared_get(key)
            finally:
                with lock:
                    left[0] -= 1
                    if left[0] <= 0:
                        done.set()
    try:
        for _ in range(min(SHARED_GET_THREADS, len(want))):
            threading.Thread(target=worker, daemon=True,
                             name="clip-store-get").start()
        done.wait(max(0.0, budget))
    except Exception:  # noqa: BLE001
        pass
    with lock:
        queue.clear()      # a late worker starts no new lookup
    return frozenset(want)


# At most this many clip uploads run at once per process: they share the
# container's CPU and network with the render that made them.
_SHARED_PUT_SLOTS = threading.BoundedSemaphore(2)


def _shared_put(key, path, meta):
    """Hand a fresh clip to the shared store on a daemon thread: the upload
    never holds up the render that made the clip (best effort, never raises).
    The file is the LRU's own copy, which outlives the render's workdir."""
    def run():
        try:
            store = _clip_store()
            if store is None:
                return
            with _SHARED_PUT_SLOTS:
                store.put(key, path, dict(meta))
        except Exception:
            pass
    try:
        threading.Thread(target=run, daemon=True,
                         name=f"clip-store-{key[:8]}").start()
    except Exception:
        pass


def _cache_put(key, src, meta):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        dst = os.path.join(CACHE_DIR, key + ".mov")
        tmp = dst + f".{os.getpid()}.{threading.get_ident()}.tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        _write_json_atomic(os.path.join(CACHE_DIR, key + ".json"), meta)
        _cache_prune()
    except Exception:
        return
    _shared_put(key, dst, meta)


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
            for prefix, base in (("/fonts/", font_dir), ("/mfonts/", MOTION_FONTS_DIR)):
                if url.startswith(ORIGIN + prefix):
                    fn = os.path.basename(url.split("?", 1)[0])
                    p = os.path.join(base, fn)
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
    # Fault injection (INTERFACES.md §6.2): this item's page dies, exactly
    # as a Chromium crash surfaces here — an error out of the page call.
    if _fault(f"chromium_crash:{job.label}"):
        raise RuntimeError(f"{job.label}: Target crashed (injected "
                           "chromium_crash)")
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
        # Wave 3 (3E, devices/INTERFACES_W3.md §6): a page that declares
        # video slots (MG.slot) has its rects recorded every frame; no
        # other page runs this
        slot_rows = [] if "MG.slot(" in job.html else None
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
                if slot_rows is not None:
                    slot_rows.append(await page.evaluate(
                        "window.MG && MG._slots ? MG._slots.current() : []"))
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
        if slot_rows is not None:
            _write_json_atomic(out_path + ".slots.json",
                               {"scale": scale, "frames": slot_rows})
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
    keys = []
    for job in jobs:
        if job.duration <= 0 or job.duration > MAX_DURATION_S:
            raise MotionRenderError(f"{job.label}: duration must be 0-{MAX_DURATION_S:.0f}s")
        if len(job.html.encode("utf-8")) > MAX_HTML_BYTES:
            raise MotionRenderError(f"{job.label}: HTML exceeds {MAX_HTML_BYTES // 1000} KB")
        keys.append(job.key())
    # one bounded, parallel read of the shared store for every local miss
    asked = _prefetch_shared(keys)
    token = _SHARED_ASKED.set(_SHARED_ASKED.get() | asked)
    try:
        lookups = [_cache_get(key) for key in keys]
    finally:
        _SHARED_ASKED.reset(token)
    for i, (job, key, hit) in enumerate(zip(jobs, keys, lookups)):
        if hit:
            path, m = hit
            local = os.path.join(out_dir, f"mg_{key}.mov")
            if not os.path.exists(local):
                try:
                    os.link(path, local)
                except OSError:
                    shutil.copyfile(path, local)
            if m.get("slots") is not None:
                # Wave 3: the slot rects travel with the cached clip
                _write_json_atomic(local + ".slots.json", m["slots"])
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
                        meta = {"x": clip.x, "y": clip.y, "w": clip.w,
                                "h": clip.h, "frames": clip.frames}
                        if os.path.exists(out + ".slots.json"):
                            with open(out + ".slots.json") as f:
                                meta["slots"] = json.load(f)
                        _cache_put(key, out, meta)
            await asyncio.gather(*(run(i, j, k) for i, j, k in todo))
        finally:
            await browser.close()
    return results


_PW_FAST = {"done": False}


def _fast_playwright_stacks():
    """Playwright's Python client captures ``inspect.stack()`` on every API
    call (it names the call and its user frames in the protocol metadata,
    for traces and error messages). On Python 3.11 that reads every
    frame's source context and code positions: about 4,200 captures per
    preview, the largest Python cost of the motion layer after the
    browser itself (E3 profile, 30 Oct 2026). A plain frame walk yields the
    same (frame, filename, lineno) the parser reads, so the metadata, and
    every pixel, are unchanged. Applied once per process; MOTION_PW_FAST_STACK=0
    turns it off. Never raises."""
    if _PW_FAST["done"]:
        return
    _PW_FAST["done"] = True
    if os.getenv("MOTION_PW_FAST_STACK", "1") == "0":
        return
    try:
        import collections
        import inspect as _inspect
        import sys
        from playwright._impl import _connection
        if getattr(_connection, "inspect", None) is not _inspect:
            return                      # another layout of the client
        frame_info = collections.namedtuple("FrameInfo",
                                            "frame filename lineno")

        class _Inspect:
            """``inspect`` for playwright's connection module: stack() is
            a frame walk, everything else the real module."""

            def __getattr__(self, name):
                return getattr(_inspect, name)

            @staticmethod
            def stack(context=1):
                f = sys._getframe(1)
                out = []
                while f is not None:
                    out.append(frame_info(f, f.f_code.co_filename,
                                          f.f_lineno))
                    f = f.f_back
                return out
        _connection.inspect = _Inspect()
    except Exception:  # noqa: BLE001 — the slow path still works
        pass


def render_jobs(jobs, out_dir, pages=None, budget_s=900.0):
    """Render RenderJobs to transparent clips in out_dir. Returns RenderedClip
    list aligned with jobs. Raises MotionRenderError with a relayable reason."""
    if not jobs:
        return []
    if not available():
        raise MotionRenderError("motion graphics renderer unavailable: "
                                + unavailable_reason())
    os.makedirs(out_dir, exist_ok=True)
    pages = pages or DEFAULT_PAGES
    _fast_playwright_stacks()
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


async def _probe_all(jobs, times_list, budget_s, measure=None):
    from playwright.async_api import async_playwright
    out = []
    deadline = time.monotonic() + float(budget_s or 60.0)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=CHROME_ARGS)
        try:
            for job, times in zip(jobs, times_list):
                if measure is not None and time.monotonic() > deadline:
                    # a measuring caller asked for a bound: the rest unread
                    out.append({"errors": ["probe budget spent"],
                                "visible_frames": 0, "samples": len(times),
                                "bboxes": [], "ink": [], "cover": [],
                                "measures": []})
                    continue
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
                    visible, bboxes, inks, covers, measures = 0, [], [], [], []

                    def frac(bb):
                        return [round(v * 4.0 / dw, 3) if i % 2 == 0 else round(v * 4.0 / dh, 3)
                                for i, v in enumerate(bb)]
                    for t in times:
                        await page.evaluate("t => window.__mgSeek(t)", float(t))
                        r = await cdp.send("Page.captureScreenshot", {
                            "format": "png", "optimizeForSpeed": True,
                            "clip": {"x": 0, "y": 0, "width": dw, "height": dh, "scale": 0.25}})
                        bb, cb, ib = _alpha_bbox(base64.b64decode(r["data"]),
                                                 (VISIBLE_ALPHA, COVER_ALPHA, INK_ALPHA))
                        if bb:
                            visible += 1
                            bboxes.append(frac(bb))
                        # one entry per requested time (None = nothing that dense)
                        inks.append(frac(ib) if ib else None)
                        covers.append(frac(cb) if cb else None)
                        if measure is not None:
                            # the full design canvas, for a caller that
                            # measures the type itself (render_qc)
                            full = await cdp.send("Page.captureScreenshot", {
                                "format": "png", "optimizeForSpeed": True,
                                "clip": {"x": 0, "y": 0, "width": dw, "height": dh,
                                         "scale": 1.0}})
                            try:
                                measures.append(measure(base64.b64decode(full["data"])))
                            except Exception as e:  # noqa: BLE001
                                measures.append({"error": str(e)[:120]})
                    errs = await page.evaluate("window.__mgErrors || []")
                    out.append({"errors": [str(e)[:200] for e in errs], "visible_frames": visible,
                                "samples": len(times), "bboxes": bboxes, "ink": inks,
                                "cover": covers,
                                **({"measures": measures} if measure is not None else {})})
                  except Exception as e:  # noqa: BLE001 — one bad composition
                    out.append({"errors": [str(e).splitlines()[0][:200]], "visible_frames": 0,
                                "samples": len(times), "bboxes": [], "ink": [], "cover": []})
                finally:
                    await ctx.close()
        finally:
            await browser.close()
    return out


def probe(jobs, times_list, budget_s=60.0, measure=None):
    """Cheap write-time check: load each composition, seek the given item-local
    times at quarter scale, and report script errors and visible coverage
    (bboxes as frame fractions, one per visible moment). ``ink`` and
    ``cover`` hold one entry per requested time: the box of the pixels at
    INK_ALPHA (what hides a face) and at COVER_ALPHA (what a caption must
    clear), or None. ``measure`` (optional): called with the full-size PNG
    of the design canvas at every requested time; its answers come back in
    ``measures`` (one per time), and ``budget_s`` then bounds the whole
    probe (compositions past it come back unread). Raises MotionRenderError
    when the browser itself cannot run.

    E3 (30 Oct 2026): with render_memo on, a composition's report is
    remembered by its clip key, the times and the measuring code, so the
    next render of an unchanged graphic (the next version, the final, the
    picture check's type pass after the caption plan's) starts no browser
    for it. Only clean reports (no errors) are remembered."""
    if not available():
        raise MotionRenderError("motion graphics renderer unavailable: "
                                + unavailable_reason())
    jobs, times_list = list(jobs), list(times_list)
    keys = _probe_memo_keys(jobs, times_list, measure)
    if keys is None:
        return _probe_run(jobs, times_list, budget_s, measure)
    import render_memo
    out = [None] * len(jobs)
    todo = []
    for i, k in enumerate(keys):
        got = render_memo.get("probe", k) if k else render_memo.MISS
        if not render_memo.is_miss(got):
            try:
                out[i] = render_memo.unpack(got)
                continue
            except Exception:  # noqa: BLE001 — a damaged answer is a miss
                pass
        todo.append(i)
    if todo:
        fresh = _probe_run([jobs[i] for i in todo],
                           [times_list[i] for i in todo], budget_s, measure)
        for i, rep in zip(todo, fresh):
            out[i] = rep
            if keys[i] and isinstance(rep, dict) and not rep.get("errors"):
                try:
                    render_memo.put("probe", keys[i], render_memo.pack(rep))
                except Exception:  # noqa: BLE001
                    pass
    return out


def _probe_memo_keys(jobs, times_list, measure):
    """One render_memo key per job (None for a job whose key cannot be
    formed), or None when render_memo is off. The key holds the job's clip
    key (document, runtime, sizes, assets by content), the requested times,
    this module's source and the measuring function's module source."""
    try:
        import render_memo
    except Exception:  # noqa: BLE001
        return None
    if not render_memo.enabled():
        return None
    try:
        tag = None
        if measure is not None:
            import inspect
            mod = inspect.getsourcefile(measure) or ""
            tag = [getattr(measure, "__module__", ""),
                   getattr(measure, "__qualname__", ""),
                   render_memo.file_digest(mod) if mod else "unknown"]
            if tag[2] == "unknown" or tag[2] == "missing":
                return None
        code = render_memo.code_digest(__file__)
    except Exception:  # noqa: BLE001
        return None
    keys = []
    for job, times in zip(jobs, times_list):
        try:
            keys.append(render_memo.key(
                "motion_engine.probe", code, tag, job.key(),
                [float(t) for t in times]))
        except Exception:  # noqa: BLE001
            keys.append(None)
    return keys


def _probe_run(jobs, times_list, budget_s, measure):
    _fast_playwright_stacks()
    coro = _probe_all(list(jobs), list(times_list), budget_s, measure)
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
