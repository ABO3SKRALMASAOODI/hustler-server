"""Record a live web page as real video — a scrolling pan, or a driven demo.

TWO SHAPES, ONE BROWSER.

  record()      — the classic product-demo pan. Load, hold the top, smooth-
                  scroll to the bottom, hold. What "show my landing page in
                  the edit" means.

  record_demo() — round 45. The browser USES the site: it moves a visible
                  cursor to a button, clicks it, waits for the page to react,
                  types into a field, scrolls to a section, and so on, from a
                  script the agent writes. What "record yourself using my
                  product" means, and the thing a founder actually needs for
                  a launch video.

WHY A DRAWN CURSOR. Headless Chromium does not composite the OS pointer into
its capture: drive the real mouse and the recording shows a page reacting to
an invisible hand — buttons highlighting for no reason. So an injected overlay
draws one. It is not a fake, and this matters: the REAL Playwright mouse is
what moves, in eased steps, and the drawn arrow is positioned by listening to
the real mousemove events. Hover states, :active, tooltips and JS handlers all
fire exactly as they would for a person, and what you see on screen is where
the pointer genuinely is.

The script is injected with add_init_script and re-attaches itself on every
document, so it survives navigation and SPA route changes; it re-appends
itself if a framework wipes the DOM under it.

THE CLICK FLASH IS BEST-EFFORT AND, ON THIS DEPLOYMENT, DOES NOT SHOW. The
cursor also draws an expanding ring and a halo on mousedown. Page-side it
provably runs — sampled from the page's own rAF loop DURING a real capture it
animates across ~23 frames — and it has never appeared in a single captured
frame on a software-composited headless surface, through four different
implementations (CSS keyframes, JS-driven ring, a halo on the arrow itself,
and after fixing the screencast starvation that swallowed the window). It is
left in because it costs nothing and will render wherever compositing allows,
but NOTHING may promise it to a user: the tool description and the agent
prompt describe the cursor and the synced click SOUND, never a visible flash.
If you pick this up again, start by dumping the raw screencast JPEGs around a
click — the video is not the thing to debug.

WHAT COMES BACK IS AS IMPORTANT AS THE VIDEO. Every click, scroll, keystroke
run and navigation is timestamped against the delivered mp4 and reported with
its position in the frame as 0-1 fractions. That event track is what lets the
editor zoom exactly onto the button at the moment it is pressed and land a
click sound on the frame it happens, instead of an agent eyeballing a contact
sheet and guessing. Verified end to end by recording a page that burns its own
stopwatch on screen: video time and real time advance 1:1, and a reported
click lands within ~0.2s of the frame it happens on.

SECURITY. The page URL passes net_fetch.check_url before the browser exists
(kills file://, literal internal addresses, hosts that resolve internally).
That alone is NOT enough: a page fetches subresources, and any of them could
point back inside our network — so every request the browser makes is routed
through a per-request interceptor that applies the same resolve-then-refuse
policy, with a per-host verdict cache so DNS happens once per host, not once
per request. Driving the page adds a second exposure the pan never had: a
click can NAVIGATE. Navigation goes through the same interceptor (it is just
another request), and pop-ups/new tabs are closed on sight rather than
followed, so the capture cannot wander into a window nothing is guarding.
That still leaves DNS-rebinding style gaps (verdict cached, connection made
later); egress policy on the worker remains the real fix, same caveat as
url_media's extractor path.

RESOURCES. Chromium on this ~1 vCPU box is heavy but bounded: one page, no
GPU, --disable-dev-shm-usage (Render's /dev/shm is tiny), a hard wall-clock
deadline enforced around every step, and the browser is closed in a finally.
Frames come off the CDP screencast as JPEGs on disk (capped by
_Screencast.MAX_FRAMES) and ffmpeg encodes them into the same H.264 yuv420p
faststart profile every other asset uses, so downstream never learns where
the file came from.
"""

import base64
import json
import os
import subprocess
import time
from urllib.parse import urlparse

import config
import net_fetch


class WebRecordError(Exception):
    pass


def available():
    """Recording exists only where the playwright package AND its baked
    Chromium are installed (the worker Docker image; not a dev Mac unless
    set up). Import is the honest probe — config can force it off."""
    if not config.WEB_RECORD_ENABLED:
        return False
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except Exception:
        return False


# Viewports: full-pixel frames matching the timelines they land in. Text is
# the whole point of a website capture, so recording is at the composite
# resolution — never upscaled later.
_VIEWPORTS = {
    "landscape": (1920, 1080),
    "portrait": (1080, 1920),
    "square": (1080, 1080),
}


def _guard_router(cache):
    """Playwright route handler enforcing the address policy per request."""
    def route_all(route):
        host = ""
        try:
            host = (urlparse(route.request.url).hostname or "").lower()
            scheme = urlparse(route.request.url).scheme
            if scheme not in ("http", "https", "data", "blob", "about"):
                route.abort()
                return
            if scheme in ("data", "blob", "about"):
                route.continue_()
                return
            verdict = cache.get(host)
            if verdict is None:
                try:
                    net_fetch.check_url(route.request.url)
                    verdict = True
                except Exception:
                    verdict = False
                cache[host] = verdict
            if verdict:
                route.continue_()
            else:
                route.abort()
        except Exception:
            try:
                route.abort()
            except Exception:
                pass
    return route_all


# ---------------------------------------------------------------- #
#  The drawn cursor                                                  #
# ---------------------------------------------------------------- #

# Injected into every document. Deliberately dependency-free and defensive:
# it runs on sites we have never seen, and a thrown exception here would
# break THEIR page, not just our overlay.
#
# The arrow is the macOS pointer silhouette, drawn as an inline SVG with a
# white fill and a dark stroke so it stays legible on any background — a
# single flat colour disappears against half the pages on the web.
#
# EVERYTHING IS DRAWN BY ONE requestAnimationFrame LOOP, and nothing is
# animated by CSS. The first version created a ring element per click and let
# an @keyframes animation expand it. The element provably entered the DOM —
# a MutationObserver saw every one — and not a single ripple appeared in any
# captured frame. Rather than keep guessing at why (insertion-time animation
# start, compositing on a headless surface, both), the effect now owns its
# own clock: two permanent nodes whose transform and opacity are written from
# elapsed time on every frame. That also makes it immune to the arbitrary
# page it is injected into — a site with `* { animation: none !important }`
# in its reset, or a heavy CSS-containment root, cannot switch it off.
_CURSOR_JS = r"""
(() => {
  if (window.__valmeraCursorInstalled) return;
  window.__valmeraCursorInstalled = true;

  const ID = '__valmera_cursor_layer__';
  const RING_MS = 520;
  let layer = null, arrow = null, ring = null;
  let x = -100, y = -100, down = false;
  let ringT0 = -1e9, ringX = 0, ringY = 0;

  const ARROW_SVG =
    '<svg width="30" height="30" viewBox="0 0 26 26" fill="none" ' +
    'xmlns="http://www.w3.org/2000/svg">' +
    '<path d="M3 1.6 L3 20.4 L8.1 15.6 L11.4 22.9 L14.9 21.3 L11.6 14.2 ' +
    'L18.6 13.9 Z" fill="#fff" stroke="#111" stroke-width="1.4" ' +
    'stroke-linejoin="round"/></svg>';

  function px(el, styles) { for (const k in styles) el.style[k] = styles[k]; }

  function build() {
    if (layer && layer.isConnected) return;
    layer = document.createElement('div');
    layer.id = ID;
    px(layer, {position: 'fixed', left: '0', top: '0', width: '0',
               height: '0', zIndex: '2147483647', pointerEvents: 'none'});

    ring = document.createElement('div');
    px(ring, {position: 'fixed', left: '0', top: '0', width: '18px',
              height: '18px', marginLeft: '-9px', marginTop: '-9px',
              borderRadius: '50%', border: '2.5px solid #fff',
              boxShadow: '0 0 0 1.5px rgba(0,0,0,.45), 0 0 14px rgba(255,255,255,.7)',
              opacity: '0', pointerEvents: 'none'});
    layer.appendChild(ring);

    arrow = document.createElement('div');
    px(arrow, {position: 'fixed', left: '0', top: '0', width: '30px',
               height: '30px', pointerEvents: 'none',
               filter: 'drop-shadow(0 2px 5px rgba(0,0,0,.45))'});
    arrow.innerHTML = ARROW_SVG;
    layer.appendChild(arrow);

    (document.body || document.documentElement).appendChild(layer);
    draw();
  }

  function draw() {
    if (!arrow) return;
    const e = (performance.now() - ringT0) / RING_MS;
    const live = (e >= 0 && e <= 1);
    // THE CLICK FEEDBACK LIVES ON THE ARROW, not only on the ring. The ring
    // below is the nicer effect and it is kept — but on a headless software
    // compositor the screencast demonstrably captured the arrow on every
    // frame while never once catching the separately-animated ring. The
    // press therefore also dips and haloes the pointer itself: whatever the
    // capture manages to sample, the click is visible on it.
    const s = live ? 0.80 + 0.20 * e : 1;
    arrow.style.transform = 'translate(' + x + 'px,' + y + 'px) scale(' + s.toFixed(3) + ')';
    if (live) {
      const g = (1 - e) * 26;
      arrow.style.filter =
        'drop-shadow(0 0 ' + g.toFixed(1) + 'px rgba(255,255,255,.95)) ' +
        'drop-shadow(0 0 ' + (g * 2).toFixed(1) + 'px rgba(255,255,255,.6)) ' +
        'drop-shadow(0 2px 5px rgba(0,0,0,.45))';
    } else if (arrow.style.filter.indexOf('rgba(255,255,255') !== -1) {
      arrow.style.filter = 'drop-shadow(0 2px 5px rgba(0,0,0,.45))';
    }
    if (live) {
      // ease-out on the radius, and fade over the back half only, so the
      // ring is unmistakably visible on the frames right after the press.
      const k = 1 - Math.pow(1 - e, 3);
      ring.style.left = ringX + 'px';
      ring.style.top = ringY + 'px';
      ring.style.transform = 'scale(' + (0.4 + k * 3.0).toFixed(3) + ')';
      ring.style.opacity = (e < 0.45 ? 1 : (1 - (e - 0.45) / 0.55)).toFixed(3);
    } else if (ring.style.opacity !== '0') {
      ring.style.opacity = '0';
    }
  }

  function loop() {
    try { build(); draw(); } catch (err) {}
    requestAnimationFrame(loop);
  }

  addEventListener('mousemove', (e) => { x = e.clientX; y = e.clientY; }, true);
  addEventListener('mousedown', (e) => {
    down = true;
    ringX = (e && e.clientX !== undefined) ? e.clientX : x;
    ringY = (e && e.clientY !== undefined) ? e.clientY : y;
    ringT0 = performance.now();
  }, true);
  addEventListener('mouseup', () => { down = false; }, true);

  // The loop also re-attaches the layer, so a framework that re-renders the
  // whole tree gets the cursor back on the next frame rather than after a
  // timer — no MutationObserver on document, no second mechanism to keep.
  requestAnimationFrame(loop);
  if (document.readyState === 'loading') {
    addEventListener('DOMContentLoaded', build);
  } else {
    build();
  }
})();
"""


# ---------------------------------------------------------------- #
#  The capture itself                                                #
# ---------------------------------------------------------------- #

class _Screencast:
    """Frames straight off Chromium's screencast, each with its REAL time.

    WHY NOT PLAYWRIGHT'S BUILT-IN RECORDER. It writes a WebM whose frames are
    stamped at a fixed 25fps in the order they arrived — so the file's clock
    is not a clock at all, it is a frame counter. Measured here on a real
    capture: 22.4 seconds of browsing produced a 9.1-second file, and inside
    that file the page's own on-screen stopwatch advanced 0.25s across one
    1.7-second stretch and 3.2s across the next 2.0-second stretch. Nothing
    computed from wall-clock time can be placed on a timeline like that: the
    click sounds and zooms this feature exists to sync would land seconds
    from the clicks, differently on every run and every machine.

    `Page.screencastFrame` hands us each frame WITH `metadata.timestamp` — a
    real epoch time. Keeping those and encoding each frame for exactly as
    long as it was actually on screen makes the delivered video a true
    recording of real time, which is the property every timestamp we report
    depends on. It also fixes the pan: `duration_s=12` now means 12 seconds.

    Chromium sends frames on DAMAGE, so a still page produces none — that is
    correct and desirable here: the gap becomes one long frame duration, not
    a hole.
    """

    # 80s at 30fps. A backstop on disk, not a policy: the wall-clock deadline
    # and WEB_DEMO_MAX_DURATION_S both bind long before this.
    MAX_FRAMES = 2600

    def __init__(self, page, context, workdir, width, height):
        self.dir = os.path.join(workdir, "frames")
        os.makedirs(self.dir, exist_ok=True)
        self.frames = []            # (path, epoch_seconds)
        self.dropped = 0
        self.width, self.height = width, height
        self._n = 0
        self._stopped = False
        self.cdp = context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._on_frame)

    def start(self):
        self.cdp.send("Page.startScreencast", {
            "format": "jpeg", "quality": 80,
            "maxWidth": self.width, "maxHeight": self.height,
            "everyNthFrame": 1})

    def _on_frame(self, params):
        # The ack MUST happen, and first: Chromium stops sending after a
        # couple of unacknowledged frames, and a capture that silently stops
        # producing frames looks like a frozen page in the final video.
        try:
            self.cdp.send("Page.screencastFrameAck",
                          {"sessionId": params.get("sessionId")})
        except Exception:
            pass
        if self._stopped:
            return
        ts = ((params.get("metadata") or {}).get("timestamp")
              or time.time())
        if len(self.frames) >= self.MAX_FRAMES:
            self.dropped += 1
            return
        try:
            raw = base64.b64decode(params.get("data") or "")
            if not raw:
                return
            path = os.path.join(self.dir, f"f{self._n:06d}.jpg")
            with open(path, "wb") as fh:
                fh.write(raw)
        except Exception:
            return
        self._n += 1
        self.frames.append((path, float(ts)))

    def stop(self):
        self._stopped = True
        try:
            self.cdp.send("Page.stopScreencast")
        except Exception:
            pass

    def write_concat(self, t_zero, t_end):
        """A concat-demuxer list holding each frame for exactly as long as it
        was on screen, starting EXACTLY at t_zero.

        Frames before `t_zero` (the blank page-load head) are dropped here
        rather than trimmed later, so the delivered file starts at the same
        instant our event timestamps are measured from — one definition of
        zero, not two that can drift.

        The subtlety that cost an hour: Chromium sends frames on DAMAGE, so a
        page that is merely SETTLING sends none, and the first frame at-or-
        after t_zero can arrive a second later. Starting the file at that
        frame silently shifts the whole timeline by the length of that quiet
        gap — every reported timestamp then lands late by an amount that
        varies per run. So the settled image (the last frame BEFORE t_zero)
        opens the file and is held across the gap: t_zero really is frame
        zero.
        """
        after = [(p, ts) for p, ts in self.frames if ts >= t_zero - 0.001]
        before = [(p, ts) for p, ts in self.frames if ts < t_zero - 0.001]
        kept = []
        if before:
            # Re-stamped to t_zero: this frame IS what the screen showed at
            # zero, whenever it happened to be captured.
            kept.append((before[-1][0], t_zero))
        elif after:
            kept.append((after[0][0], t_zero))
        kept.extend((p, ts) for p, ts in after
                    if not kept or ts > kept[0][1] + 0.001)
        if not kept:
            return None, 0.0
        lines, total = [], 0.0
        for i, (path, ts) in enumerate(kept):
            nxt = kept[i + 1][1] if i + 1 < len(kept) else t_end
            dur = max(1.0 / 60.0, float(nxt) - float(ts))
            lines.append(f"file '{path}'")
            lines.append(f"duration {dur:.4f}")
            total += dur
        # The concat demuxer ignores the duration after the LAST file unless
        # the file is repeated; without this the final frame is one tick long
        # and the video ends early.
        lines.append(f"file '{kept[-1][0]}'")
        list_path = os.path.join(self.dir, "frames.txt")
        with open(list_path, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        return list_path, total


# ---------------------------------------------------------------- #
#  Step vocabulary                                                   #
# ---------------------------------------------------------------- #

STEP_KINDS = ("click", "type", "scroll", "hover", "wait", "press", "goto")

# Words that must never be typed into a page we are filming. Recording a
# real credential into a video the customer then publishes is a footgun no
# convenience justifies, and a demo never needs it — placeholder values show
# the same flow.
_SECRETISH = ("password", "passwd", "secret", "token", "api_key", "apikey",
              "credit card", "cvv")


def _norm_steps(steps):
    """Validate the agent's script into a list of dicts, or raise
    WebRecordError with a sentence the agent can repeat to the user."""
    if not isinstance(steps, (list, tuple)) or not steps:
        raise WebRecordError("the demo script was empty")
    if len(steps) > config.WEB_DEMO_MAX_STEPS:
        raise WebRecordError(
            f"a demo can be at most {config.WEB_DEMO_MAX_STEPS} steps; "
            f"{len(steps)} were given")
    out = []
    for i, raw in enumerate(steps, 1):
        if not isinstance(raw, dict):
            raise WebRecordError(f"step {i} is not an object")
        do = str(raw.get("do") or "").strip().lower()
        if do not in STEP_KINDS:
            raise WebRecordError(
                f"step {i}: '{do or '(missing)'}' is not a step — use one of "
                + ", ".join(STEP_KINDS))
        step = {"do": do}
        for key in ("text", "selector", "url", "to", "key", "label"):
            val = raw.get(key)
            if val is not None:
                step[key] = str(val)[:400]
        for key in ("seconds", "by"):
            val = raw.get(key)
            if val is not None:
                try:
                    step[key] = float(val)
                except (TypeError, ValueError):
                    raise WebRecordError(
                        f"step {i}: {key} must be a number")
        if do in ("click", "hover") and not (step.get("selector")
                                             or step.get("text")):
            raise WebRecordError(
                f"step {i}: a {do} needs `text` (the visible label) or "
                "`selector` (a CSS selector)")
        if do == "type":
            if not step.get("text"):
                raise WebRecordError(f"step {i}: type needs `text`")
            if not step.get("selector"):
                raise WebRecordError(
                    f"step {i}: type needs `selector` — the field to type "
                    "into")
            low = (step.get("selector", "") + " "
                   + step.get("label", "")).lower()
            if any(s in low for s in _SECRETISH) or "password" in low:
                raise WebRecordError(
                    f"step {i}: this records to a video file, so it will not "
                    "type into a password or payment field. Demo the flow up "
                    "to that point instead")
        if do == "press" and not step.get("key"):
            raise WebRecordError(
                f"step {i}: press needs `key` (Enter, Tab, Escape…)")
        if do == "goto" and not step.get("url"):
            raise WebRecordError(f"step {i}: goto needs `url`")
        out.append(step)
    return out


class _Session:
    """One browser, one page, one growing event track.

    Exists so the pan and the driven demo share the setup, the guard router,
    the deadline arithmetic and the event bookkeeping — the two shapes differ
    only in what they DO once the page is open.
    """

    def __init__(self, page, deadline, size):
        self.page = page
        self.deadline = deadline
        self.W, self.H = size
        # EPOCH seconds, because that is the clock Chromium stamps screencast
        # frames with. Set once the page has settled: it is the instant the
        # delivered file starts, so `now()` is already a position in that
        # file with nothing to subtract afterwards.
        self.t_zero = time.time()
        self.events = []
        self.problems = []
        self.mx, self.my = self.W * 0.5, self.H * 0.42

    # ---- time ----

    def left(self):
        r = self.deadline - time.monotonic()
        if r <= 0:
            raise WebRecordError("the page took too long to load and record")
        return r

    def now(self):
        """Seconds into the DELIVERED file."""
        return max(0.0, time.time() - self.t_zero)

    def sleep(self, seconds):
        """Hold, in SLICES.

        One long wait_for_timeout looks identical from here and is not: the
        screencast requires an ack per frame, and Chromium stops sending
        after a couple of unacknowledged ones. Our ack runs on the same
        greenlet as this code, so a single multi-second Playwright call
        starves it and the capture goes blank for the whole hold — measured
        as 1.6-second gaps with no frames, and it is why the click ripple
        (which plays during exactly these dwells) never appeared in a single
        captured frame. Slicing the wait pumps the event loop ~16 times a
        second, so holds record like the rest of the demo.
        """
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            budget = max(0, int(self.left() * 1000))
            if budget <= 0:
                return
            slice_ms = int(min(60.0, remaining * 1000, budget))
            if slice_ms <= 0:
                return
            self.page.wait_for_timeout(slice_ms)
            remaining -= slice_ms / 1000.0

    def note(self, kind, x=None, y=None, label=""):
        ev = {"t": round(self.now(), 2), "kind": kind}
        if x is not None and y is not None:
            ev["x"] = round(min(max(x / max(1, self.W), 0.0), 1.0), 4)
            ev["y"] = round(min(max(y / max(1, self.H), 0.0), 1.0), 4)
        if label:
            ev["label"] = label[:80]
        self.events.append(ev)

    # ---- pointer ----

    def move_to(self, x, y, seconds=0.55):
        """Glide the REAL mouse there. The drawn arrow follows because it
        listens to the same events; a person's hand does not teleport, and a
        cut from one instant jump to another is the tell that gives away a
        scripted capture.

        DRIVEN BY THE CLOCK, NOT BY A STEP COUNT. Each mouse.move is a CDP
        round-trip to the browser, and on a loaded box that round-trip costs
        far more than the frame interval it is standing in for. A fixed
        "40 steps of 13ms" therefore does not take 0.55s, it takes as long as
        40 round-trips take — measured at ~4s on the first real capture,
        which turned a five-step demo into forty seconds of slow motion.
        Sampling the position from ELAPSED time instead makes the gesture
        take the time it says it does on any machine; a slow browser gets a
        coarser path, not a longer one.
        """
        x = min(max(float(x), 1.0), self.W - 2.0)
        y = min(max(float(y), 1.0), self.H - 2.0)
        x0, y0 = self.mx, self.my
        if ((x - x0) ** 2 + (y - y0) ** 2) ** 0.5 < 2:
            self.mx, self.my = x, y
            return
        seconds = max(0.05, float(seconds))
        t0 = time.monotonic()
        while True:
            f = min(1.0, (time.monotonic() - t0) / seconds)
            eased = f * f * (3 - 2 * f)          # smoothstep
            self.page.mouse.move(x0 + (x - x0) * eased,
                                 y0 + (y - y0) * eased)
            if f >= 1.0 or self.deadline - time.monotonic() <= 2:
                break
            # ~30 positions a second is the capture rate; anything finer is
            # invisible and only costs round-trips.
            self.page.wait_for_timeout(33)
        self.mx, self.my = x, y

    # ---- targets ----

    def locate(self, step):
        """Resolve a step's target to a Playwright locator.

        `text` is tried as a real user would name it — the visible label —
        before falling back to a substring match, because "Sign up" should
        find the button that says Sign up, not the paragraph that mentions
        signing up.
        """
        page = self.page
        if step.get("selector"):
            loc = page.locator(step["selector"])
        else:
            label = step["text"]
            loc = page.get_by_role(
                "button", name=label, exact=False).or_(
                page.get_by_role("link", name=label, exact=False)).or_(
                page.get_by_text(label, exact=False))
        return loc.first

    def target_box(self, step, what):
        loc = self.locate(step)
        try:
            loc.scroll_into_view_if_needed(
                timeout=min(4000, int(self.left() * 1000)))
        except Exception:
            pass
        try:
            box = loc.bounding_box(timeout=min(4000,
                                               int(self.left() * 1000)))
        except Exception:
            box = None
        if not box or box.get("width", 0) < 1 or box.get("height", 0) < 1:
            name = step.get("selector") or step.get("text") or "?"
            self.problems.append(
                f"could not find \"{name}\" on the page, so the {what} was "
                "skipped")
            return None
        return box

    # ---- steps ----

    def do_click(self, step):
        box = self.target_box(step, "click")
        if box is None:
            return
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        self.move_to(cx, cy)
        # A beat on the target before pressing. Without it the click lands the
        # instant the pointer arrives and the viewer never sees WHAT was
        # clicked — the single biggest difference between a screen recording
        # that teaches and one that just moves.
        self.sleep(0.35)
        label = step.get("label") or step.get("text") or step.get("selector")
        self.note("click", cx, cy, label or "")
        try:
            self.page.mouse.click(cx, cy)
        except Exception as e:
            self.problems.append(
                f"the click on \"{label}\" did not go through "
                f"({str(e).splitlines()[0][:80]})")
            return
        # THE PRESS ITSELF IS RECORDED FIRST. The load-state wait below is a
        # single blocking Playwright call, and a single blocking call starves
        # the screencast ack (see sleep) — parked immediately after the click
        # it swallowed exactly the window the click feedback plays in, so no
        # capture ever contained a visible press. This sliced hold comes
        # first; the page is still loading underneath it either way.
        self.sleep(0.6)
        # Let whatever it triggered actually happen and settle on screen.
        try:
            # A SHORT budget on purpose. This is "give the page a moment to
            # react", not "wait until the network is quiet" — and networkidle
            # routinely burns its whole timeout on pages that keep a socket
            # open, which at 3.5s a click turned a five-click demo into
            # seventeen seconds of dead air. Genuinely slow navigations are
            # still covered: the next step's locator auto-waits for its
            # element with its own timeout.
            self.page.wait_for_load_state(
                "networkidle", timeout=min(1200, int(self.left() * 1000)))
        except Exception:
            pass
        self.sleep(max(0.0, float(step.get("seconds") or 0.9) - 0.6))

    def do_hover(self, step):
        box = self.target_box(step, "hover")
        if box is None:
            return
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        self.move_to(cx, cy)
        self.note("hover", cx, cy,
                  step.get("label") or step.get("text") or "")
        self.sleep(float(step.get("seconds") or 1.0))

    def do_type(self, step):
        box = self.target_box(step, "typing")
        if box is None:
            return
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        self.move_to(cx, cy)
        self.sleep(0.25)
        self.note("click", cx, cy, "focus the field")
        try:
            self.page.mouse.click(cx, cy)
            self.note("type", cx, cy, step["text"][:40])
            self.page.keyboard.type(step["text"][:120],
                                    delay=config.WEB_DEMO_TYPE_DELAY_MS)
        except Exception as e:
            self.problems.append(
                f"could not type into that field "
                f"({str(e).splitlines()[0][:80]})")
            return
        self.sleep(float(step.get("seconds") or 0.5))

    def do_press(self, step):
        key = step["key"][:24]
        self.note("key", label=key)
        try:
            self.page.keyboard.press(key)
        except Exception as e:
            self.problems.append(
                f"could not press {key} ({str(e).splitlines()[0][:80]})")
            return
        try:
            # A SHORT budget on purpose. This is "give the page a moment to
            # react", not "wait until the network is quiet" — and networkidle
            # routinely burns its whole timeout on pages that keep a socket
            # open, which at 3.5s a click turned a five-click demo into
            # seventeen seconds of dead air. Genuinely slow navigations are
            # still covered: the next step's locator auto-waits for its
            # element with its own timeout.
            self.page.wait_for_load_state(
                "networkidle", timeout=min(1200, int(self.left() * 1000)))
        except Exception:
            pass
        self.sleep(float(step.get("seconds") or 0.8))

    def do_scroll(self, step):
        """A section-to-section move, eased. `to` is a target to bring into
        view; `by` is a pixel distance; neither means one screenful."""
        target_y = None
        if step.get("to"):
            probe = {"selector": step["to"]} if (
                step["to"].startswith((".", "#", "[")) or "=" in step["to"]
            ) else {"text": step["to"]}
            box = self.target_box(probe, "scroll")
            if box is not None:
                # Land the section a little below the top edge; flush against
                # it reads as a jump cut rather than a move.
                target_y = self.page.evaluate("() => window.scrollY") \
                    + box["y"] - self.H * 0.12
        if target_y is None:
            by = step.get("by")
            delta = float(by) if by is not None else self.H * 0.9
            target_y = self.page.evaluate("() => window.scrollY") + delta
        self.note("scroll")
        self._glide_scroll(target_y, float(step.get("seconds") or 1.1))
        self.sleep(0.35)

    def _glide_scroll(self, to_y, seconds):
        """Clock-driven for the same reason as move_to: every scrollTo is a
        round-trip, so a fixed step count makes the pan take as long as the
        round-trips do rather than as long as it was asked to."""
        try:
            from_y = float(self.page.evaluate("() => window.scrollY") or 0)
        except Exception:
            from_y = 0.0
        to_y = max(0.0, float(to_y))
        if abs(to_y - from_y) < 4:
            return
        seconds = max(0.05, float(seconds))
        t0 = time.monotonic()
        while True:
            f = min(1.0, (time.monotonic() - t0) / seconds)
            eased = f * f * (3 - 2 * f)
            self.page.evaluate(
                f"window.scrollTo(0, {int(from_y + (to_y - from_y) * eased)})")
            if f >= 1.0 or self.deadline - time.monotonic() <= 2:
                break
            self.page.wait_for_timeout(33)

    def do_goto(self, step):
        url = step["url"]
        try:
            net_fetch.check_url(url)
        except Exception as e:
            self.problems.append(f"refused to open {url[:60]} ({e})")
            return
        self.note("nav", label=urlparse(url).path or "/")
        try:
            self.page.goto(url, wait_until="load",
                           timeout=min(20.0, self.left()) * 1000)
        except Exception:
            self.problems.append(f"{url[:60]} did not finish loading")
        self.sleep(float(step.get("seconds") or 1.2))

    def run(self, steps):
        handlers = {"click": self.do_click, "hover": self.do_hover,
                    "type": self.do_type, "press": self.do_press,
                    "scroll": self.do_scroll, "goto": self.do_goto}
        for step in steps:
            if self.deadline - time.monotonic() <= 3:
                self.problems.append(
                    "ran out of recording time before the last steps")
                break
            if self.now() >= config.WEB_DEMO_MAX_DURATION_S:
                self.problems.append(
                    f"stopped at the {config.WEB_DEMO_MAX_DURATION_S:.0f}s "
                    "length limit before the last steps")
                break
            if step["do"] == "wait":
                self.sleep(min(float(step.get("seconds") or 1.0), 5.0))
                continue
            try:
                handlers[step["do"]](step)
            except WebRecordError:
                raise
            except Exception as e:                      # pragma: no cover
                self.problems.append(
                    f"the {step['do']} step failed "
                    f"({str(e).splitlines()[0][:90]})")


def _transcode(list_path, workdir):
    """Encode the timestamped frames into the same delivery profile every
    other asset uses: H.264 high, yuv420p, faststart, CFR 30.

    The concat list already carries each frame's REAL on-screen duration, so
    `-r 30` here is a resample of a correct timeline rather than an attempt
    to invent one — a still second becomes 30 identical frames, and a second
    of fast motion keeps every frame Chromium sent.
    """
    if not list_path:
        raise WebRecordError("the page loaded but no video was captured")
    out = os.path.join(workdir, "webrecord.mp4")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
           "-an", "-vsync", "cfr", "-r", "30",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", "replace")[-200:]
        raise WebRecordError(f"could not encode the capture ({tail})")
    except subprocess.TimeoutExpired:
        raise WebRecordError("encoding the capture took too long")
    return out


def _probe_out(out, extra):
    import media
    try:
        info = media.probe(out)
    except Exception as e:
        raise WebRecordError(f"the capture is unreadable ({e})")
    got = {"path": out, "width": info["width"], "height": info["height"],
           "duration_s": info["duration"]}
    got.update(extra)
    return got


def _record(url, workdir, orientation, wall_s, cursor, body):
    """The one place a browser is opened, driven and closed.

    `body(session)` does the shape-specific work. Everything else — the
    address guard, the deadline, the injected cursor, the screencast and its
    real-time frame clock, the pop-up policy, the transcode — is identical
    for a pan and a demo and lives here so the two cannot drift apart.
    """
    try:
        net_fetch.check_url(url)
    except net_fetch.FetchError as e:
        raise WebRecordError(str(e))
    if not available():
        raise WebRecordError("website recording is not installed on this "
                             "deployment")
    from playwright.sync_api import sync_playwright, Error as PWError

    W, H = _VIEWPORTS.get(orientation or "landscape",
                          _VIEWPORTS["landscape"])
    deadline = time.monotonic() + wall_s

    session = None
    title, final_url = "", url
    browser = None
    list_path, cast_len = None, 0.0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--mute-audio", "--hide-scrollbars",
            ])
            context = browser.new_context(
                viewport={"width": W, "height": H},
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36 ValmeraCapture"),
            )
            cache = {}
            context.route("**/*", _guard_router(cache))
            if cursor:
                context.add_init_script(_CURSOR_JS)
            page = context.new_page()
            # A click can open a tab. Following it would take the capture
            # somewhere the recording was never scoped to, and Playwright
            # would keep recording the ORIGINAL page anyway — so close it and
            # say so, rather than film a window nobody can see.
            #
            # Attached AFTER new_page(), and it still checks identity: the
            # 'page' event fires for the context's FIRST page too, so a
            # handler registered above this line closes the very page we are
            # about to drive — every capture then dies on goto with "target
            # closed". Both guards, because either alone is one refactor away
            # from that.
            popped = []

            def _on_page(pg):
                if pg is page:
                    return
                popped.append(pg.url or "a new tab")
                try:
                    pg.close()
                except Exception:
                    pass
            context.on("page", _on_page)
            session = _Session(page, deadline, (W, H))
            cast = _Screencast(page, context, workdir, W, H)
            cast.start()
            try:
                page.goto(url, wait_until="load",
                          timeout=min(30.0, session.left()) * 1000)
            except PWError as e:
                msg = str(e).splitlines()[0][:160]
                if "Timeout" not in msg:
                    raise WebRecordError(f"could not open that page ({msg})")
                # Slow pages: record whatever has painted rather than fail.
            page.wait_for_timeout(min(1500, int(session.left() * 1000)))
            # Everything captured so far is white/half-painted loading noise,
            # which would open every capture with a blank flash. Zero is set
            # HERE, and write_concat drops the earlier frames — so the file
            # starts at the same instant every reported timestamp is measured
            # from, rather than being trimmed to it afterwards.
            session.t_zero = time.time()
            title = (page.title() or "")[:120]
            final_url = page.url or url
            if cursor:
                # Put the pointer on screen before anything moves, or the
                # first gesture appears to fly in from a corner.
                try:
                    page.mouse.move(session.mx, session.my)
                except Exception:
                    pass

            body(session)

            for u in popped[:3]:
                session.problems.append(
                    f"a new tab opened ({str(u)[:60]}) and was closed — the "
                    "recording stayed on the page being demoed")
            t_end = time.time()
            cast.stop()
            # One last beat of frames can still be in flight; draining them
            # costs nothing and stops the file ending a frame short.
            try:
                page.wait_for_timeout(120)
            except Exception:
                pass
            list_path, cast_len = cast.write_concat(session.t_zero, t_end)
            if cast.dropped:
                session.problems.append(
                    f"the capture hit its frame limit and {cast.dropped} "
                    "frames near the end were not recorded")
            context.close()
            browser.close()
            browser = None
    except WebRecordError:
        raise
    except Exception as e:
        raise WebRecordError(
            f"the capture failed ({str(e).splitlines()[0][:160]})")
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass

    out = _transcode(list_path, workdir)
    return _probe_out(out, {
        "final_url": final_url, "page_title": title,
        "events": session.events if session else [],
        "problems": session.problems if session else [],
    })


def record(url, workdir, duration_s=12.0, orientation="landscape",
           scroll=True, cursor=False):
    """Record `url` for ~duration_s seconds as a scrolling pan.

    Returns dict(path, width, height, duration_s, final_url, page_title,
    events, problems). Raises WebRecordError with a user-fit sentence.
    """
    try:
        dur = min(max(float(duration_s or 12.0), 4.0),
                  config.WEB_RECORD_MAX_DURATION_S)
    except (TypeError, ValueError):
        dur = 12.0

    def body(s):
        if not scroll:
            s.sleep(min(dur, max(0.0, s.left() - 2.0)))
            return
        total_h = s.page.evaluate(
            "() => Math.max(document.body ? document.body.scrollHeight"
            " : 0, document.documentElement ?"
            " document.documentElement.scrollHeight : 0)")
        travel = max(0, int(total_h or 0) - s.H)
        hold_s = 1.2
        scroll_s = max(0.0, dur - 2 * hold_s)
        s.sleep(hold_s)
        if travel > 0 and scroll_s > 0.5:
            s.note("scroll")
            s._glide_scroll(travel, scroll_s)
        else:
            s.sleep(min(scroll_s, max(0.0, s.left() - 2.0)))
        s.sleep(hold_s)

    return _record(url, workdir, orientation, config.WEB_RECORD_WALL_S,
                   cursor, body)


def record_demo(url, workdir, steps, orientation="landscape"):
    """Drive `url` through `steps` with a visible cursor and record it.

    Returns the same dict as record(), where `events` is the timestamped
    track of every click / scroll / keystroke run / navigation, positioned as
    0-1 fractions of the frame, and `problems` lists anything the script
    asked for that the page could not deliver.
    """
    plan = _norm_steps(steps)

    def body(s):
        # A beat on the opening screen before anything moves — the viewer has
        # to see WHERE they are before they can follow what is being done.
        s.sleep(0.9)
        s.run(plan)
        # And a beat at the end, so the last state is readable and the editor
        # has frames to cut on rather than ending mid-gesture.
        s.sleep(1.0)

    return _record(url, workdir, orientation, config.WEB_DEMO_WALL_S,
                   True, body)


def summarize_events(events):
    """One compact line per event, as the agent reads them."""
    bits = []
    for e in events:
        where = (f" at ({e['x']:.2f},{e['y']:.2f})"
                 if "x" in e and "y" in e else "")
        label = f" \"{e['label']}\"" if e.get("label") else ""
        bits.append(f"{e['t']:.2f}s {e['kind']}{label}{where}")
    return "; ".join(bits)


def events_json(events):
    return json.dumps(events, separators=(",", ":"))
