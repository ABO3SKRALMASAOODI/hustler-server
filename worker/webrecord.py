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
import re
import time
from urllib.parse import urlparse

import brief_tour
import config
import net_fetch


class WebRecordError(Exception):
    pass


def available():
    """Recording exists where the playwright package AND its baked Chromium
    are installed (the worker Docker image; not a dev Mac unless set up), OR
    where an executor is configured to run it for us.

    Import is the honest probe for the local case — config can force it off.
    The remote clause matters because round 61 moved the browser OFF this
    process: on a dispatcher that somehow lacks Chromium the capability is
    still real, because the box that actually runs it has one. Both roles ship
    the same image today, so in practice both clauses agree; the point is that
    the answer follows where the work happens, not where the question is asked.
    """
    if not config.WEB_RECORD_ENABLED:
        return False
    return local_available() or bool(config.REMOTE_EXECUTOR_URL)


def local_available():
    """Can THIS process drive a browser? The executor's runner asks this one:
    `available()` would answer for the fleet, and an executor that also had
    REMOTE_EXECUTOR_URL set would then claim a capability it does not have and
    crash on the import instead of refusing in words."""
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
# Portrait renders the site's PHONE layout (C1, 29 Oct 2026): a 432x768 CSS
# viewport drawn at 2.5x is still 1080x1920 pixels, but every site's own
# responsive design makes its text and buttons phone-sized and readable in a
# 9:16 frame. At 1080 CSS px the desktop layout's body text was ~1% of the
# frame height (the judge: "unreadable at phone size").
_CSS_VIEWPORTS = {"portrait": (432, 768, 2.5)}
# The automatic tour (the First cut's website demo and the trial recipe) films
# a narrower phone, 360x640 CSS at 3x (Q2, 30 Oct 2026): the same 1080x1920
# frames with every line of text 1.2x the size it is at 432 (the judge, five
# runs: "the body copy is too small to read"), and the 360-wide layout every
# responsive site is built for. Landscape tours film the desktop layout at
# 1280x720 CSS, 1.5x: its text is 1.5x the 1920-wide layout's. Captures
# scripted step by step keep _CSS_VIEWPORTS, so nothing changes for them.
_TOUR_VIEWPORTS = {"portrait": (360, 640, 3.0), "landscape": (1280, 720, 1.5)}
# D1 (QI, 30 Oct): both layouts above are the trial branch's engine. A
# capture for any other account (a grandfathered subscriber, MCP) films
# main's viewport exactly: the frame size in CSS pixels, density 1. The
# caller says which with phone_layout (agent_tools.engine_on).
_DESKTOP_VIEWPORTS = {}


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

STEP_KINDS = ("click", "type", "scroll", "hover", "wait", "press", "goto",
              "tour")

# The automatic tour ({do: 'tour', by: <sections>}): what a person shows first
# on an unknown site (C1, 29 Oct 2026: the owner's references and the
# bake-off's passive scrolls). Section links are opened in this order of
# preference; the call to action is pointed at, never submitted.
TOUR_SECTIONS = ("pricing", "features", "product", "how it works", "solutions",
                 "use cases", "customers", "showcase", "examples", "templates",
                 "integrations", "platform", "tools", "explore")
TOUR_CTA = ("get started", "start free", "try it", "try for free", "try free",
            "add to cart", "add to bag", "buy now", "shop now",
            "start for free", "sign up", "book a demo", "get a demo",
            "request a demo", "start now", "join", "create", "download",
            "contact sales", "try")
# The page's CLOSING call to action may also be a shop's or a person's: "Shop
# now", "Get in touch" (Q2: the portfolio and shop fixtures).
CLOSING_CTA = tuple(w for w in TOUR_CTA if w not in ("add to cart", "add to bag")) + ("get in touch", "contact us", "contact", "shop now",
                          "shop all", "explore", "book", "schedule",
                          "subscribe", "say hello", "hire me", "let's talk",
                          "talk to", "order now", "discover")
TOUR_AVOID = ("log in", "login", "sign in", "cookie", "accept", "reject",
              "privacy", "terms", "careers", "jobs", "blog", "docs",
              "documentation", "support", "status", "legal", "skip to")
_TOUR_JS = r"""() => {
  const H = window.innerHeight, W = window.innerWidth, out = [];
  for (const el of document.querySelectorAll('a,button,[role=button],[role=tab],[role=switch],summary')) {
    const r = el.getBoundingClientRect();
    if (r.width < 12 || r.height < 10 || r.bottom < 0 || r.top > H || r.right < 0 || r.left > W) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity || '1') < 0.2) continue;
    let t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    // an icon-only button in the top bar is the phone menu (C2: linear.app)
    if (!t && r.top < H * 0.12 && r.width < 80 && el.tagName === 'BUTTON') t = '\u2630';
    if (!t) continue;
    // a whole card is one link with its card's text (a project, a product):
    // kept as a thing to point at, by its first words
    const card = t.length > 32;
    if (card && (r.width * r.height) < W * H * 0.015) continue;
    if (card) t = t.slice(0, 40);
    const href = el.getAttribute('href') || '';
    let ext = false;
    try { ext = !!href && !href.startsWith('#') && new URL(href, location.href).hostname !== location.hostname; } catch (e) {}
    // pinned in place (a sticky header's own button): never a stop's
    // feature, it is on screen at every stop
    let fixed = false;
    for (let n = el, k = 0; n && k < 12; n = n.parentElement, k++) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed' || p === 'sticky') { fixed = true; break; }
    }
    let abs = '';
    try { abs = href && !href.startsWith('#') && !href.startsWith('javascript') ? new URL(href, location.href).href : ''; } catch (e) {}
    out.push({t, x: (r.left + r.width / 2) / W, y: (r.top + r.height / 2) / H,
              area: (r.width * r.height) / (W * H), ext, fixed, href: abs, card,
              // clicked in place, nothing submitted: a tab, a switch, a
              // billing toggle, an accordion (the judge: "click the Yearly
              // toggle so the prices visibly change")
              toggle: (['tab', 'switch'].includes(el.getAttribute('role') || '') ||
                       el.tagName === 'SUMMARY' || el.hasAttribute('aria-pressed') ||
                       (el.tagName === 'BUTTON' && /^(monthly|yearly|annual|annually|month|year|billed yearly|billed monthly)$/i.test(t))) &&
                      !(el.tagName === 'A' && abs && new URL(abs).pathname !== location.pathname),
              box: [r.left / W, r.top / H, r.right / W, r.bottom / H],
              aria: (el.getAttribute('aria-label') || '').toLowerCase(),
              button: el.tagName === 'BUTTON' || /btn|button|cta/i.test(String(el.className || ''))});
  }
  // the site's own section links, visible or not (a phone layout hides its
  // navigation behind a menu): same origin, short text, with their href
  const links = [];
  for (const a of document.querySelectorAll('header a[href], nav a[href], [role=navigation] a[href]')) {
    const t = (a.textContent || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    const href = a.getAttribute('href') || '';
    if (!t || t.length > 32 || !href || href.startsWith('#') || href.startsWith('mailto:')) continue;
    let u = null;
    try { u = new URL(href, location.href); } catch (e) { continue; }
    if (u.hostname !== location.hostname || u.href === location.href) continue;
    links.push({t, href: u.href});
    if (links.length >= 60) break;
  }
  const heads = [];
  for (const h of document.querySelectorAll('h2')) {
    const r = h.getBoundingClientRect();
    const t = (h.innerText || '').trim();
    if (t && r.height > 8) heads.push({t: t.slice(0, 60), top: r.top + window.scrollY});
  }
  return {items: out, heads, links};
}"""

# The cards of a page (QB, 30 Oct 2026): every same-origin link outside the
# header, nav and footer, with the heading of the card it belongs to (the
# smallest ancestor that holds one heading and only this link's address). A
# brief that names a thing a section page lists ("a ferramenta de gráficos")
# opens that card.
_CARDS_JS = r"""() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.getAttribute('href') || '';
    if (!href || href.startsWith('#') || /^(mailto|tel|javascript):/.test(href)) continue;
    let u = null;
    try { u = new URL(href, location.href); } catch (e) { continue; }
    if (u.hostname !== location.hostname || u.href === location.href) continue;
    if (a.closest('header,nav,footer,[role=navigation]')) continue;
    let head = '';
    const own = a.querySelector('h1,h2,h3,h4');
    if (own) head = own.innerText;
    else {
      let n = a.parentElement;
      for (let k = 0; n && k < 5 && !head; k++, n = n.parentElement) {
        const hrefs = new Set(Array.from(n.querySelectorAll('a[href]')).map(x => x.href));
        if (hrefs.size > 1) break;
        const h = n.querySelector('h1,h2,h3,h4');
        if (h) head = h.innerText;
      }
    }
    head = (head || '').trim().replace(/\s+/g, ' ');
    const t = (a.innerText || a.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    out.push({head: head.slice(0, 60), t: t.slice(0, 60), href: u.href});
    if (out.length >= 80) break;
  }
  return out;
}"""

# A tool page's own form (QB, 30 Oct 2026): a brief that asks to see its tools
# working ("gravações reais das funcionalidades") needs one real use of each:
# the page's text or number fields that are not about a person (never an
# e-mail, a password, a phone, a card, a search or a sign-up) and the button
# whose label says what it does (generate, calculate, convert, solve...).
# Elements are tagged for the recorder's own selectors.
_FORM_JS = r"""() => {
  const bad = /e-?mail|mail|passw|senha|phone|\btel\b|fone|card|cart[aã]o|cvv|cpf|cnpj|search|busca|pesquis|newsletter|subscri|assin|coupon|cupom|login|entrar|user|usu[aá]rio|name|nome|address|endere|zip|cep/i;
  const act = /gerar|calcular|plot|desenhar|resolver|converter|planificar|simular|visualizar|executar|decompor|analisar|abrir|animar|girar|generate|calculate|convert|solve|draw|compute|run|plan|show|mostrar|criar|animate|unfold|rotate|play|\u25b6/i;
  const noact = /subscri|assin|sign|cadastr|entrar|login|enviar|send|join|buy|compr|pagar|pay|checkout/i;
  const H = window.innerHeight;
  const vis = (el) => {
    const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 20 && r.height > 14 &&
           r.top + window.scrollY < 6 * H &&
           st.visibility !== 'hidden' && st.display !== 'none';
  };
  const topOf = (el) => el.getBoundingClientRect().top + window.scrollY;
  const inputs = [];
  let k = 0;
  const sel = 'input:not([type=hidden]):not([type=password]):not([type=email]):not([type=tel]):not([type=file]):not([type=checkbox]):not([type=radio]):not([type=submit]):not([type=button]):not([type=range]):not([type=color]), textarea';
  for (const el of document.querySelectorAll(sel)) {
    if (el.readOnly || el.disabled || !vis(el)) continue;
    const tag = [el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.autocomplete, el.type].join(' ');
    if (bad.test(tag)) continue;
    el.setAttribute('data-qb-i', String(k));
    inputs.push({sel: '[data-qb-i="' + k + '"]', value: el.value || '', ph: el.placeholder || '', type: el.type || 'text', top: topOf(el)});
    k++;
    if (inputs.length >= 2) break;
  }
  let button = null;
  const form = inputs.length ? document.querySelector('[data-qb-i="0"]').closest('form') : null;
  const scope = form || document;
  for (const el of scope.querySelectorAll('button, input[type=submit], input[type=button], [role=button]')) {
    if (!vis(el)) continue;
    const t = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    if (!t || t.length > 40 || noact.test(t) || !act.test(t)) continue;
    el.setAttribute('data-qb-b', '1');
    button = {sel: '[data-qb-b="1"]', t: t, top: topOf(el)};
    break;
  }
  return {inputs: inputs, button: button};
}"""

# The page's geometry for the tour (Q2, 30 Oct 2026): where its footer starts,
# its calls to action anywhere on the page and its headings, in page pixels.
# The tour keeps the footer and the page's closing call to action out of the
# middle of the demo and ends on that call to action, once (the judge, every
# website run: "footer link columns dominate the last 5 s", "'Built for the
# future' appears three times").
_PAGE_JS = r"""(words) => {
  const sy = window.scrollY, H = window.innerHeight, W = window.innerWidth;
  const de = document.documentElement;
  const total = Math.max(de.scrollHeight, document.body ? document.body.scrollHeight : 0);
  const pinned = (el) => {
    for (let n = el, k = 0; n && k < 12; n = n.parentElement, k++) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed' || p === 'sticky') return true;
    }
    return false;
  };
  let foot = null;
  for (const f of document.querySelectorAll('footer, [role=contentinfo]')) {
    const r = f.getBoundingClientRect();
    if (r.height < 60 || r.width < W * 0.6 || pinned(f)) continue;
    if (r.bottom + sy < total - H * 0.6) continue;
    const top = r.top + sy;
    if (foot === null || top < foot) foot = top;
  }
  const ctas = [];
  for (const el of document.querySelectorAll('a,button,[role=button]')) {
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 18) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity || '1') < 0.2) continue;
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ');
    const low = t.toLowerCase();
    if (!t || t.length > 32 || !words.some(c => low === c || low.startsWith(c))) continue;
    if (pinned(el)) continue;
    // a BUTTON, not a text link in a list of links (a footer's "Get a
    // demo" link is not the page's closing call to action; Q2, cal.com)
    const solid = (n) => { const c = getComputedStyle(n).backgroundColor || '';
      return c && c !== 'transparent' && !/rgba\([^)]*,\s*0\)$/.test(c); };
    const filled = solid(el) || (el.firstElementChild && solid(el.firstElementChild));
    // a button by its look: filled, outlined, named so, or simply button-
    // sized (cal.com's are links whose fill is on an inner span)
    const btn = el.tagName === 'BUTTON' || el.getAttribute('role') === 'button' || filled ||
                parseFloat(st.borderTopWidth || '0') >= 1 || /btn|button|cta/i.test(String(el.className || '')) ||
                (r.height >= 32 && r.width * r.height >= W * H * 0.012);
    ctas.push({t, top: r.top + sy, h: r.height, x: (r.left + r.width / 2) / W, btn,
               x0: r.left / W, x1: r.right / W, area: (r.width * r.height) / (W * H)});
    if (ctas.length >= 80) break;
  }
  // a heading inside a fixed header is chrome; a STICKY section heading (a
  // one-page portfolio's "About", "Experience") is a section, at the top of
  // the block it sticks in (Q2: brittanychiang.com had no other headings)
  const stuck = (el) => {
    for (let n = el, k = 0; n && k < 12; n = n.parentElement, k++) {
      const p = getComputedStyle(n).position;
      if (p === 'fixed') return 'fixed';
      if (p === 'sticky') return n;
    }
    return null;
  };
  const heads = [];
  for (const h of document.querySelectorAll('h1,h2,h3')) {
    const r = h.getBoundingClientRect();
    const t = (h.innerText || '').trim().replace(/\s+/g, ' ');
    if (!t || r.height < 8 || r.width < 20) continue;
    const st = getComputedStyle(h);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity || '1') < 0.05) continue;
    const s = stuck(h);
    if (s === 'fixed') continue;
    let top = r.top + sy;
    if (s && s.parentElement) top = s.parentElement.getBoundingClientRect().top + sy
                                    + (r.top - s.getBoundingClientRect().top);
    heads.push({t: t.slice(0, 60), top, vtop: r.top, h: r.height, lvl: +h.tagName[1],
                x0: r.left / W, x1: r.right / W, sticky: !!s});
    if (heads.length >= 120) break;
  }
  return {sy, H, total, bottom: Math.max(0, total - H), foot, ctas, heads};
}"""

# Words that must never be typed into a page we are filming. Recording a
# real credential into a video the customer then publishes is a footgun no
# convenience justifies, and a demo never needs it — placeholder values show
# the same flow.
_SECRETISH = ("password", "passwd", "secret", "token", "api_key", "apikey",
              "credit card", "cvv")


_STEP_ALIASES = ("action", "step", "kind", "op", "command", "type")


def _canonical_step(raw):
    """Accept the unambiguous spellings models write for a step.

    Tolerant tool-call handling: {"action": "scroll", "amount": 400},
    {"type": "goto", "url": ...} and the one-key shorthand {"wait": 2},
    {"goto": "https://…"}, {"click": "Pricing"} all mean exactly one step.
    Anything still ambiguous falls through to the validator's sentence."""
    if raw.get("do"):
        step = dict(raw)
    else:
        step = dict(raw)
        for alias in _STEP_ALIASES:
            value = str(raw.get(alias) or "").strip().lower()
            if value in STEP_KINDS and not (
                    alias == "type" and value == "type" and "do" in raw):
                step["do"] = value
                step.pop(alias, None)
                break
        else:
            named = [k for k in raw if str(k).lower() in STEP_KINDS]
            if len(named) == 1:
                kind = str(named[0]).lower()
                value = raw[named[0]]
                step = {k: v for k, v in raw.items() if k != named[0]}
                step["do"] = kind
                if kind == "goto":
                    step.setdefault("url", value)
                elif kind == "wait":
                    step.setdefault("seconds", value)
                elif kind == "scroll":
                    if isinstance(value, (int, float)) or str(value).lstrip(
                            "-").replace(".", "", 1).isdigit():
                        step.setdefault("by", value)
                    else:
                        step.setdefault("to", value)
                elif kind in ("click", "hover", "type"):
                    step.setdefault("text", value)
                elif kind == "press":
                    step.setdefault("key", value)
    if step.get("do") == "scroll" and "by" not in step and "to" not in step:
        for key in ("amount", "pixels", "distance", "y"):
            if step.get(key) is not None:
                step["by"] = step[key]
                break
    if step.get("do") == "wait" and "seconds" not in step:
        for key in ("duration", "duration_s", "time", "s"):
            if step.get(key) is not None:
                step["seconds"] = step[key]
                break
    return step


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
        raw = _canonical_step(raw)
        do = str(raw.get("do") or "").strip().lower()
        if do not in STEP_KINDS:
            raise WebRecordError(
                f"step {i}: '{do or '(missing)'}' is not a step. Each step is "
                "an object whose `do` names it, e.g. {\"do\": \"scroll\", "
                "\"by\": 800}, {\"do\": \"click\", \"text\": \"Pricing\"}, "
                "{\"do\": \"wait\", \"seconds\": 1.5} — `do` is one of "
                + ", ".join(STEP_KINDS))
        step = {"do": do}
        for key in ("text", "selector", "url", "to", "key", "label"):
            val = raw.get(key)
            if val is not None:
                step[key] = str(val)[:400]
        if do == "tour":
            # mode='scroll': travel the page top to bottom by its own
            # sections instead of clicking into pages; prefer: the request's
            # words, which rank the site's links that name them first
            if str(raw.get("mode") or "").strip().lower() == "scroll":
                step["mode"] = "scroll"
            if raw.get("brief") is True:
                # QB: the request is a brief with its own words (brief_tour):
                # its named pages and cards are matched by word stem and
                # share the time; a tile or a bare URL keeps the old tour
                step["brief"] = True
            if raw.get("prefer"):
                step["prefer"] = str(raw["prefer"])[:1600 if step.get("brief") else 600]
            try:
                secs = float(raw.get("seconds") or 0)
            except (TypeError, ValueError):
                secs = 0.0
            if secs:
                step["seconds"] = min(max(secs, 8.0), config.WEB_DEMO_MAX_DURATION_S - 5)
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
        # capture seconds spent loading a page the tour opened (cut from the
        # edit as blank frames): the tour adds them back to the length asked
        self.lost_s = 0.0
        self._toggled = set()
        self._chapters = []

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

    def note(self, kind, x=None, y=None, label="", box=None):
        ev = {"t": round(self.now(), 2), "kind": kind}
        if x is not None and y is not None:
            ev["x"] = round(min(max(x / max(1, self.W), 0.0), 1.0), 4)
            ev["y"] = round(min(max(y / max(1, self.H), 0.0), 1.0), 4)
        if label:
            ev["label"] = label[:80]
        if box:
            # Wave 3 (3F): the DOM element's own box in frame fractions, so
            # a push, an iris or a breakout lands on the element itself
            ev["box"] = element_box(box, self.W, self.H)
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
        # The first VISIBLE match: a responsive site keeps its hidden
        # desktop nav ahead of the open phone menu in the DOM, and `.first`
        # was that hidden link ("could not find PRICING", C1 29 Oct).
        # And one that is on TOP where it sits: an open phone menu covers the
        # page's own links, which stay "visible" underneath it, and a click
        # at their spot lands on whatever the menu shows there.
        visible = []
        try:
            for k in range(min(loc.count(), 8)):
                cand = loc.nth(k)
                if cand.is_visible():
                    visible.append(cand)
                    if cand.evaluate(_ON_TOP_JS):
                        return cand
        except Exception:
            pass
        return visible[0] if visible else loc.first

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
        # The target may have moved while the pointer travelled (an opening
        # menu still sliding in: the stripe.com phone menu's "Pricing" click
        # landed on "Sign in", C1 29 Oct). Measure again and follow it.
        try:
            again = self.locate(step).bounding_box(timeout=800)
        except Exception:
            again = None
        if again and again.get("width", 0) >= 1:
            nx = again["x"] + again["width"] / 2
            ny = again["y"] + again["height"] / 2
            if abs(nx - cx) > 3 or abs(ny - cy) > 3:
                box, cx, cy = again, nx, ny
                self.move_to(cx, cy, seconds=0.25)
                self.sleep(0.15)
        label = step.get("label") or step.get("text") or step.get("selector")
        self.note("click", cx, cy, label or "", box=box)
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
                  step.get("label") or step.get("text") or "", box=box)
        self.sleep(float(step.get("seconds") or 1.0))

    def do_type(self, step):
        box = self.target_box(step, "typing")
        if box is None:
            return
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        self.move_to(cx, cy)
        self.sleep(0.25)
        self.note("click", cx, cy, "focus the field", box=box)
        try:
            self.page.mouse.click(cx, cy)
            self.note("type", cx, cy, step["text"][:40], box=box)
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

    # ---- the automatic tour ----

    def _tour_links(self):
        """[(rank, {t, href})] of the site's own section links (hidden ones
        too), in TOUR_SECTIONS order."""
        try:
            info = self.page.evaluate(_TOUR_JS) or {}
        except Exception:
            return []
        out, seen = [], set()
        for link in info.get("links") or []:
            rank = self._section_rank(link.get("t"))
            key = str(link.get("href") or "")
            if rank is None or key in seen or \
                    any(a in str(link.get("t", "")).lower() for a in TOUR_AVOID):
                continue
            seen.add(key)
            out.append((rank, link))
        return [link for _r, link in sorted(out, key=lambda x: x[0])]

    def _tour_items(self):
        try:
            info = self.page.evaluate(_TOUR_JS) or {}
        except Exception:
            info = {}
        items = [i for i in info.get("items") or []
                 if not i.get("ext") and not any(
                     a in str(i.get("t", "")).lower() for a in TOUR_AVOID)]
        return items, info.get("heads") or []

    @staticmethod
    def _section_rank(text):
        low = str(text or "").strip().lower()
        for k, word in enumerate(TOUR_SECTIONS):
            if low == word or (word in low and len(low) <= len(word) + 12):
                return k
        return None

    def _sections(self, items, top_only=True):
        found, seen = [], set()
        for i in items:
            rank = self._section_rank(i.get("t"))
            if rank is None or (top_only and i.get("y", 1) > 0.16):
                continue
            key = str(i["t"]).lower()
            if key not in seen:
                seen.add(key)
                found.append((rank, i))
        return [i for _r, i in sorted(found, key=lambda x: x[0])]

    def _cta(self, items):
        best, score = None, 0.0
        for i in items:
            low = str(i.get("t", "")).lower()
            if not any(low == c or low.startswith(c) for c in TOUR_CTA):
                continue
            sc = float(i.get("area") or 0) * (2.0 if i.get("button") else 1.0)
            if 0.03 < float(i.get("y") or 0) < 0.92 and sc > score:
                best, score = i, sc
        return best

    def drift(self, seconds=1.2):
        """Keep the last frames LIVE: the pointer glides a short way, so the
        capture never ends on a held picture (a still page before the end
        card reads as a freeze)."""
        x = min(max(self.mx + self.W * 0.08, self.W * 0.2), self.W * 0.8)
        y = min(max(self.my - self.H * 0.04, self.H * 0.2), self.H * 0.8)
        self.move_to(x, y, seconds=max(0.3, float(seconds)))

    def _settle(self, most_s=3.0):
        """Until the page has painted its content (a navigation shows a blank
        page while its scripts load; scroll-triggered sections stay at
        opacity 0 until something scrolls), at most ``most_s``."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < most_s and self.deadline - time.monotonic() > 4:
            try:
                ready = self.page.evaluate(
                    "() => document.readyState === 'complete' && "
                    "(document.body ? document.body.innerText.trim().length : 0) > 120")
            except Exception:
                ready = False
            if ready:
                break
            self.sleep(0.25)
        try:
            self.page.evaluate("() => window.scrollBy(0, 1)")
        except Exception:
            pass
        self.sleep(0.3)

    def _painted(self):
        """Whether the viewport shows content: text, an image, a video or a
        canvas under at least 3 of 15 sampled points."""
        try:
            return bool(self.page.evaluate(_PAINTED_JS))
        except Exception:
            return True

    # ---- the tour's beats (Q2, 30 Oct 2026) ----
    #
    # A tour is a run of BEATS the edit can cut on: the hero (the title plays
    # over it), each section's own heading ("look"), stops down a page that
    # point at one thing each ("hover"), and the page's closing call to action
    # ("finale"), once, at the end. Every beat is pointed at by its position on
    # screen, never re-found by its text: a shop shows "Add to cart" forty
    # times, and the old hover-by-text scrolled back up to the FIRST one on
    # every stop (the judge: "jumps back to NEW ARRIVALS three times").
    # The footer and the closing block stay out of the middle of the tour.

    FINALE_S = 3.4          # what the closing beat needs at the end

    def _page(self):
        try:
            return self.page.evaluate(_PAGE_JS, list(CLOSING_CTA)) or {}
        except Exception:
            return {}

    def _closing(self, info):
        """The page's closing call to action: the last one above the footer,
        in the lower half of the page and below its first screen, or None."""
        H = float(info.get("H") or self.H)
        total = float(info.get("total") or 0)
        foot = info.get("foot")
        limit = float(foot) if foot is not None else total
        cands = [c for c in info.get("ctas") or []
                 if c.get("btn", True) and H * 1.2 < float(c["top"]) < limit - 8
                 and float(c["top"]) > total * 0.45]
        if not cands:
            return None
        # a real call to action ("Start now", "Get started") over a softer
        # one ("Explore payments" cards below stripe's closing block)
        strong = [c for c in cands
                  if any(str(c["t"]).lower() == w or str(c["t"]).lower().startswith(w)
                         for w in TOUR_CTA if w not in ("add to cart", "add to bag"))]
        cands = strong or cands
        last = max(float(c["top"]) for c in cands)
        row = [c for c in cands if float(c["top"]) >= last - H * 0.3]

        def rank(c):
            low = str(c["t"]).lower()
            k = next((i for i, w in enumerate(CLOSING_CTA)
                      if low == w or low.startswith(w)), len(CLOSING_CTA))
            return (k, -float(c.get("area") or 0))
        return sorted(row, key=rank)[0]

    def _limit(self, info, closing):
        """The deepest scroll position the MIDDLE of a tour may use: the
        footer fills at most a fifth of the screen, and the closing block
        stays below the screen until the finale shows it."""
        H = self.H
        lim = float(info.get("bottom") or 0)
        if info.get("foot") is not None:
            lim = min(lim, float(info["foot"]) - H * 0.8)
        if closing:
            lim = min(lim, float(closing["top"]) - H * 1.2)
        return max(0.0, lim)

    def _positions(self, info, y0, limit, step=0.8):
        """Scroll positions down the page from ``y0`` to ``limit``: each
        heading 15% below the top edge, ``step``-screen steps between them."""
        H = self.H
        heads = sorted(float(h["top"]) - H * 0.15 for h in info.get("heads") or []
                       if float(h["top"]) - H * 0.15 > y0 + H * 0.3)
        stops = []
        for pos in heads:
            if pos > limit + 1:
                break
            if not stops or pos - stops[-1] > H * 0.5:
                stops.append(pos)
        steps, k = [], 1
        while y0 + H * step * k <= limit + 1 and k <= 60:
            steps.append(y0 + H * step * k)
            k += 1
        return sorted(stops + [p for p in steps
                               if all(abs(p - q) > H * min(0.45, step * 0.6)
                                      for q in stops)])

    def _head_stops(self, info, y0, limit):
        """The scroll positions that put a section heading 15% below the top
        edge, between ``y0`` and ``limit``."""
        H = self.H
        return sorted(float(h["top"]) - H * 0.15 for h in info.get("heads") or []
                      if y0 + H * 0.3 < float(h["top"]) - H * 0.15 <= limit + 1
                      and int(h.get("lvl") or 3) <= 2)

    def _glide_s(self, dist):
        """An eased scroll's length: a screen in about a second, a long jump
        a little longer, never a whip."""
        return min(max(0.85 + 0.18 * abs(float(dist)) / max(1.0, self.H), 0.9), 1.7)

    def _time_up(self, until, reserve):
        return (self.deadline - time.monotonic() <= 8
                or self.now() >= config.WEB_DEMO_MAX_DURATION_S - 6
                or self.now() >= until + self.lost_s - reserve)

    def _point(self, item, dwell, kind="hover"):
        """Glide the pointer onto ``item`` (a _TOUR_JS item: viewport
        fractions) where it is NOW, and note it as a beat."""
        x, y = float(item["x"]) * self.W, float(item["y"]) * self.H
        self.move_to(x, y, seconds=0.55)
        b = item.get("box")
        box = ({"x": b[0] * self.W, "y": b[1] * self.H,
                "width": (b[2] - b[0]) * self.W, "height": (b[3] - b[1]) * self.H}
               if b else None)
        self.note(kind, x, y, str(item.get("t") or ""), box=box)
        self.sleep(dwell)

    def _feature(self, items, skip=()):
        """The thing to point at in view: a call to action, else the most
        prominent card, tile or button in the middle of the screen. Never a
        pinned header's own button."""
        mid = [i for i in items if not i.get("fixed")
               and 0.22 < float(i.get("y") or 0) < 0.82]
        cta = self._cta(mid)
        if cta and cta["t"] not in skip:
            return cta
        # a card or a button, never a plain row of text: the pointer on a
        # feature row opened its tooltip over the prices (the judge, cal.com)
        best, area = None, 0.0
        for i in mid:
            if i["t"] in skip:
                continue
            a = float(i.get("area") or 0)
            if not (i.get("card") or a >= 0.015 or (i.get("button") and a > 0.004)):
                continue
            if a > area:
                best, area = i, a
        return best

    def _toggle(self, items, done):
        """A tab, switch or billing toggle in the middle of the screen, not
        clicked yet on this page, or None."""
        for i in items:
            if i.get("toggle") and not i.get("fixed") and \
                    0.15 < float(i.get("y") or 0) < 0.85 and \
                    (self.page.url, i["t"]) not in done:
                return i
        return None

    def _click_here(self, item, dwell):
        """Click a control where it is, in place (a tab or a toggle: the page
        changes, it does not navigate), and note it as a beat."""
        x, y = float(item["x"]) * self.W, float(item["y"]) * self.H
        self.move_to(x, y, seconds=0.55)
        self.sleep(0.25)
        b = item.get("box")
        box = ({"x": b[0] * self.W, "y": b[1] * self.H,
                "width": (b[2] - b[0]) * self.W, "height": (b[3] - b[1]) * self.H}
               if b else None)
        was = self.page.url
        self.note("toggle", x, y, str(item.get("t") or ""), box=box)
        try:
            self.page.mouse.click(x, y)
        except Exception:
            return
        self.sleep(dwell)
        if self.page.url != was:
            self.problems.append(f"\"{item['t']}\" opened another page")

    def _chapter(self):
        """A short section heading (one to three words) just below the top
        edge after a scroll is a chapter: noted, with a beat for the edit to
        set its name (at most three, five seconds apart)."""
        if getattr(self, "_paged", False) or \
                len(getattr(self, "_chapters", ())) >= 3 or \
                self.now() - (self._chapters[-1] if self._chapters else -9) < 5.0:
            return
        info = self._page()
        sy = float(info.get("sy") or 0)
        done = getattr(self, "_chapter_names", set())

        def where(h):
            return float(h["vtop"]) if h.get("vtop") is not None \
                else float(h["top"]) - sy
        # the heading the stop landed on (15% below the top edge) first; one
        # stuck at the top edge (the section being left) only when nothing
        # else is there
        heads = sorted(info.get("heads") or [],
                       key=lambda h: abs(where(h) - 0.15 * self.H))
        for h in heads:
            # where it is on screen NOW: a sticky section heading (a
            # portfolio's "EXPERIENCE") sits at the top edge inside its section
            top = where(h)
            words = str(h.get("t") or "").split()
            name = str(h.get("t") or "").strip().lower()
            if -0.01 * self.H <= top <= 0.32 * self.H and int(h.get("lvl") or 3) <= 2 \
                    and 1 <= len(words) <= 3 and len(str(h["t"])) <= 22 \
                    and name not in done:
                self.note("section", label=str(h["t"]))
                self._chapters.append(self.now())
                done.add(name)
                self._chapter_names = done
                self.sleep(0.9)
                return

    def _stop(self, pos, dwell, pointed):
        here_y = float(self.page.evaluate("() => window.scrollY") or 0)
        self.note("scroll")
        self._glide_scroll(pos, self._glide_s(pos - here_y))
        self.sleep(0.3)
        self._chapter()
        items, _h = self._tour_items()
        toggle = self._toggle(items, self._toggled)
        here = None if toggle else self._feature(items, skip=pointed)
        if toggle:
            self._toggled.add((self.page.url, toggle["t"]))
            self._click_here(toggle, dwell + 0.3)
        elif here:
            pointed.add(here["t"])
            self._point(here, dwell)
        elif self._look() is not None:
            # nothing to point at: the section's own heading is the beat
            self.sleep(dwell + 0.55)
        else:
            self.sleep(dwell + 0.55)

    def _look(self, info=None):
        """The beat on a page's own words: its main heading in view."""
        info = info or self._page()
        sy = float(info.get("sy") or 0)
        inview = [h for h in info.get("heads") or []
                  if sy - 2 <= float(h["top"]) <= sy + self.H * 0.6]
        if not inview:
            return None
        best = sorted(inview, key=lambda h: (int(h.get("lvl") or 3),
                                             -float(h.get("h") or 0)))[0]
        y = float(best["top"]) - sy
        box = {"x": float(best["x0"]) * self.W, "y": y,
               "width": (float(best["x1"]) - float(best["x0"])) * self.W,
               "height": float(best["h"])}
        self.note("look", box["x"] + box["width"] / 2, y + box["height"] / 2,
                  str(best["t"]), box=box)
        return best

    def _travel(self, until, reserve, most=None, scroll_mode=False):
        """Travel DOWN the page we are on by its own sections, never back up,
        one stop at a time, until the time left is the finale's (or, asked
        for the page top to bottom, to the bottom of the page)."""
        info = self._page()
        y0 = float(info.get("sy") or 0)
        closing = self._closing(info)
        if scroll_mode and not closing:
            limit = max(0.0, float(info.get("bottom") or 0) - self.H * 0.6)
        else:
            # top to bottom still ends on the page's closing call to action
            # when it has one, just above its foot (the judge, three tile
            # runs: "end live on a real CTA, not the footer")
            limit = self._limit(info, closing)
        stops = self._positions(info, y0, limit)
        left = max(0.0, until + self.lost_s - reserve - self.now())
        fit = max(1, int(left / 2.5))
        if most is None and len(stops) < fit * 0.7 and limit - y0 > self.H:
            # a short page and a long piece asked for (a 60 s brief on a
            # one-page site; Q2: 20 s delivered): closer stops, held longer
            stops = self._positions(info, y0, limit,
                                    step=max(0.45, (limit - y0) / self.H / fit))
        if most is None and len(stops) > fit:
            # a long page is still travelled to its end in the time asked
            # for (the judge: "never reaches the bottom of the page", "drifts
            # to an arbitrary end mid-Experience"): stops sampled evenly,
            # each moved to the section heading nearest it so a stop lands on
            # a section (a chapter, a heading beat) rather than mid-text
            heads = self._head_stops(info, y0, limit)
            step = len(stops) / float(fit)
            picked = []
            for k in range(fit):
                pos = stops[min(len(stops) - 1, int(round((k + 1) * step)) - 1)]
                near = [h for h in heads if abs(h - pos) <= self.H * 0.9
                        and (not picked or h > picked[-1] + self.H * 0.4)]
                if near:
                    pos = min(near, key=lambda h: abs(h - pos))
                if not picked or pos > picked[-1] + self.H * 0.4:
                    picked.append(pos)
            stops = picked
        if most is not None:
            stops = stops[:most]
        # a short page still fills the time: a steadier pace, longer stops
        dwell = 0.8
        if stops:
            dwell = min(1.6 if until <= 30 else 3.0,
                        max(0.8, left / len(stops) - 1.75))
        pointed = set()
        for pos in stops:
            if self._time_up(until, reserve):
                break
            if not (scroll_mode and not closing):
                # a page that renders its lower sections as they scroll in
                # shows its closing block (and footer) only now: never travel
                # past it (Q2: cal.com's tour ran on into its footer)
                fresh = self._page()
                if pos > self._limit(fresh, self._closing(fresh)) + 1:
                    break
            self._stop(pos, dwell, pointed)

    def _finale(self, until, scroll_mode=False, here_only=False):
        """The closing beat: the page's closing call to action framed in the
        middle of the screen, the pointer arriving on it, live to the end.
        Asked for the page top to bottom, the page's bottom instead.
        ``here_only`` (QB: a brief that asked for a product page ends ON it):
        the closing block of a product page is the footer's newsletter form,
        so the beat is what is in view, never a scroll down to the footer."""
        info = self._page()
        H = self.H
        sy = float(info.get("sy") or 0)
        bottom = float(info.get("bottom") or 0)
        closing = None if here_only else self._closing(info)
        if closing and float(closing["top"]) > sy - H * 3.0:
            # the closing call to action, even when the page only rendered it
            # after the tour passed it: the last beat is the call to action
            target = min(bottom, max(0.0, float(closing["top"])
                                     + float(closing["h"]) / 2 - H * 0.52))
            self.note("scroll")
            self._glide_scroll(target, self._glide_s(target - sy))
            self.sleep(0.35)
            sy = float(self.page.evaluate("() => window.scrollY") or 0)
            cy = float(closing["top"]) - sy + float(closing["h"]) / 2
            if 0 < cy < H:
                cx = float(closing["x"]) * self.W
                self.move_to(cx, cy, seconds=0.7)
                box = {"x": float(closing["x0"]) * self.W,
                       "y": float(closing["top"]) - sy,
                       "width": (float(closing["x1"]) - float(closing["x0"])) * self.W,
                       "height": float(closing["h"])}
                self.note("finale", cx, cy, str(closing["t"]), box=box)
                self.events[-1]["cta"] = True
                # the closing block's own heading just above the button: the
                # push keeps it whole too (the judge: "Built for the future"
                # lost its edges to a push framed on the buttons alone)
                over = [h for h in info.get("heads") or []
                        if float(closing["top"]) - H * 0.6 <= float(h["top"])
                        < float(closing["top"])]
                if over:
                    h = max(over, key=lambda x: float(x["top"]))
                    self.events[-1]["head_box"] = element_box(
                        {"x": float(h["x0"]) * self.W, "y": float(h["top"]) - sy,
                         "width": (float(h["x1"]) - float(h["x0"])) * self.W,
                         "height": float(h["h"])}, self.W, self.H)
                self._live_hold(until, cx, cy, press=True)
                return
        if scroll_mode and bottom - sy > 4:
            self.note("scroll")
            self._glide_scroll(bottom, self._glide_s(bottom - sy))
            self.sleep(0.3)
        items, _h = self._tour_items()
        here = self._feature(items)
        if here:
            x, y = float(here["x"]) * self.W, float(here["y"]) * self.H
            self.move_to(x, y, seconds=0.7)
            b = here.get("box")
            box = ({"x": b[0] * self.W, "y": b[1] * self.H,
                    "width": (b[2] - b[0]) * self.W,
                    "height": (b[3] - b[1]) * self.H} if b else None)
            self.note("finale", x, y, str(here.get("t") or ""), box=box)
            self._live_hold(until, x, y)
        else:
            # no box: the edit keeps the whole width in frame
            self.note("finale", self.W * 0.5, self.H * 0.5)
            self._live_hold(until, self.mx, self.my)

    def _live_hold(self, until, cx, cy, press=False):
        """The last beat stays LIVE and short: on a call to action the
        pointer presses it (the cursor's press ring only: nothing is clicked,
        the page never leaves the site), then eases a little off it; about
        1.6 s (the judge: "land on Get started 0.3-0.5 s after the click
        instead of a 3 s slow drift")."""
        left = until + self.lost_s - self.now()
        # about half a second of live picture after the press (the judge:
        # "hold 0.3-0.5 s ... rather than 1.2 s where only the cursor moves")
        hold = min(max(left, 1.1), 1.5)
        if press:
            self.sleep(0.25)
            try:
                self.page.evaluate(_PRESS_JS, [cx, cy])
            except Exception:
                pass
            self.sleep(0.35)
            hold -= 0.6
        self.move_to(min(cx + self.W * 0.05, self.W * 0.9),
                     min(cy + self.H * 0.02, self.H * 0.92),
                     seconds=max(0.6, hold))

    def _norm_url(self, u):
        u = str(u or "").split("#")[0]
        for tail in ("index.html", "index.htm", "index.php"):
            if u.endswith("/" + tail):
                u = u[: -len(tail)]
        return u.rstrip("/").lower()

    def _named(self, text, prefer):
        """Whether the request names this link's words ("open the designer
        brands section")."""
        t = str(text or "").strip().lower()
        low = str(prefer or "").lower()
        return bool(low) and 3 <= len(t) <= 28 and bool(
            re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", low))

    def _candidates(self, items, prefer, start, brief=False, want=0):
        """The sections to open, in order: the links the request names (in
        the order it names them), then the usual sections. Each is
        {t, href, item?}: ``item`` when it sits visibly in the top bar (it is
        clicked), else it is opened by its own address (a phone layout's
        hidden navigation, no menu fumble on camera).

        ``brief`` (QB): the request is a brief in its own words. A link is
        named when the brief uses its words by stem ("guides" -> "Halal
        Guide", "ferramentas" -> "Ferramentas"), in the brief's order; when
        the brief names fewer than three of the site's links, the first
        ``want`` navigation links fill the tour (a one-page scroll of 44 s was
        the judge's "passive scroll")."""
        try:
            info = self.page.evaluate(_TOUR_JS) or {}
        except Exception:
            info = {}
        low = str(prefer or "").lower()
        here = self._norm_url(start)
        top = {str(i.get("t", "")).strip().lower(): i for i in items
               if float(i.get("y") or 1) <= 0.16 and not i.get("ext")
               and i.get("href")}
        found, seen, spare = [], set(), []
        for link in info.get("links") or []:
            t = str(link.get("t") or "").strip()
            lt = t.lower()
            href = str(link.get("href") or "")
            key = self._norm_url(href)
            if not href or key == here or key in seen or \
                    any(a in lt for a in TOUR_AVOID) or \
                    any(lt.startswith(c) for c in TOUR_CTA) or \
                    _off_tour(start, href):
                continue
            named = self._named(t, low)
            rank = self._section_rank(t)
            stem_pos = brief_tour.label_pos(t, prefer) if brief else None
            if brief:
                spare.append({"t": t, "href": href, "item": top.get(lt)})
            if not named and rank is None and stem_pos is None:
                continue
            seen.add(key)
            if stem_pos is not None:
                order = (0, stem_pos)
            else:
                pos = low.find(lt) if named else -1
                order = (0, pos) if named else (1, rank)
            found.append((order, {"t": t, "href": href, "item": top.get(lt)}))
        out = [c for _o, c in sorted(found, key=lambda x: x[0])]
        if brief and want >= 2 and sum(
                1 for o, _c in found if o[0] == 0) < 3:
            have = {self._norm_url(c["href"]) for c in out}
            fill = brief_tour.nav_fallback(
                [c for c in spare if self._norm_url(c["href"]) not in have],
                max(0, want - len(out)), TOUR_AVOID, TOUR_CTA)
            out += fill
        return out

    def _open(self, cand, start):
        """Open one section: a visible top-bar link is clicked where it is, a
        hidden one is opened by its address. Returns whether a showable page
        of this site is now on screen."""
        before = len(self.problems)
        item = cand.get("item")
        t0 = self.now()
        t_act = t0
        if item:
            x, y = float(item["x"]) * self.W, float(item["y"]) * self.H
            self.move_to(x, y)
            self.sleep(0.3)
            b = item.get("box")
            box = ({"x": b[0] * self.W, "y": b[1] * self.H,
                    "width": (b[2] - b[0]) * self.W,
                    "height": (b[3] - b[1]) * self.H} if b else None)
            self.note("click", x, y, cand["t"], box=box)
            t_act = self.now()
            try:
                self.page.mouse.click(x, y)
            except Exception as e:
                self.problems.append(f"the click on \"{cand['t']}\" did not go "
                                     f"through ({str(e).splitlines()[0][:80]})")
                return False
            # a site that routes in the page changes its address a moment
            # after the click: wait for it (sliced, so the capture keeps
            # its frames), then for the new page's load
            was = self.page.url
            for _k in range(10):
                self.sleep(0.25)
                if self.page.url != was:
                    break
            try:
                self.page.wait_for_load_state(
                    "load", timeout=min(8000, int(self.left() * 1000)))
            except Exception:
                pass
        else:
            self.do_goto({"url": cand["href"], "seconds": 0.2})
            if len(self.problems) > before:
                return False
        self._settle()
        _hide_consent(self.page)
        # half of it: the old page often stays on screen while the new one
        # loads, and only blank frames are cut (Q2: 20 s asks came out 23-24 s)
        self.lost_s += 0.5 * max(0.0, self.now() - t0 - 0.9)
        # QB: the old page held on screen while a slow site loads the new one
        # (klosmo: 13 s on the hero, "pacing 3" from both judges) is noted so
        # the edit can cut it: from a second after the click (the click must
        # still read) to just before the new page paints
        t_done = self.now()
        if t_done - t_act > 2.6:
            self.events.append({"t": round(t_act + 1.0, 2), "kind": "stale",
                                "end": round(t_done - 0.35, 2)})
        if _off_tour(start, self.page.url):
            self.problems.append(f"\"{cand['t']}\" led to a sign-in or another "
                                 "site, so the tour went on")
            return False
        if not self._painted():
            self.problems.append(f"\"{cand['t']}\" opened a blank page, so the "
                                 "tour went on")
            return False
        return True

    def _named_cards(self, prefer, start, skip_stems, visited, most=3):
        """The cards of this page the brief names (by the stems of their
        heading, never the stems of the section's own name), in the brief's
        order: [{t, href}]."""
        try:
            cards = self.page.evaluate(_CARDS_JS) or []
        except Exception:
            return []
        found, seen = [], set()
        for card in cards:
            label = str(card.get("head") or "").strip()
            href = str(card.get("href") or "")
            key = self._norm_url(href)
            if not label or not href or key in seen or key in visited or \
                    _off_tour(start, href):
                continue
            pos = brief_tour.label_pos(label, prefer, skip=skip_stems)
            # a card is named when the brief is about it: two of its words,
            # or one the brief says twice (never a domain word said once)
            if pos is None or brief_tour.label_score(
                    label, prefer, skip=skip_stems) < 2:
                continue
            seen.add(key)
            found.append((pos, {"t": label[:40], "href": href}))
        return [c for _p, c in sorted(found, key=lambda x: x[0])][:most]

    _EXAMPLE = re.compile(r"[\d=+^*/()]|\bx\b", re.I)

    def _demo_form(self, section_end):
        """Use the tool this page is, once: type an example the field itself
        shows (its placeholder, when that looks like a value) into at most two
        empty fields and press the button that says what it does, then give
        the result a moment. Nothing that asks for a person's details is ever
        touched (_FORM_JS). Returns whether a button was pressed."""
        try:
            info = self.page.evaluate(_FORM_JS) or {}
        except Exception:
            return False
        button = info.get("button")
        if not button or self.now() >= section_end - 2.5:
            return False
        for field in info.get("inputs") or []:
            if str(field.get("value") or "").strip():
                continue                # a default is already there: keep it
            ph = str(field.get("ph") or "").strip()
            if self._EXAMPLE.search(ph) and len(ph) <= 30:
                text = ph
            elif field.get("type") == "number":
                text = "12"
            else:
                continue
            if self.now() >= section_end - 2.5:
                return False
            try:
                here = float(self.page.evaluate("() => window.scrollY") or 0)
                want = max(0.0, float(field.get("top") or 0) - 0.4 * self.H)
                if abs(want - here) > 0.2 * self.H:
                    self.note("scroll")
                    self._glide_scroll(want, self._glide_s(want - here))
                    self.sleep(0.2)
            except Exception:
                pass
            self.do_type({"selector": field["sel"], "text": text,
                          "seconds": 0.4})
        if self.now() >= section_end - 1.8:
            return False
        try:
            # the tool's button, brought to the middle of the screen the way
            # the tour moves everywhere (eased, never a jump)
            here = float(self.page.evaluate("() => window.scrollY") or 0)
            want = max(0.0, float(button.get("top") or 0) - 0.45 * self.H)
            if abs(want - here) > 0.15 * self.H:
                self.note("scroll")
                self._glide_scroll(want, self._glide_s(want - here))
                self.sleep(0.2)
        except Exception:
            pass
        before = len(self.problems)
        self.do_click({"selector": button["sel"], "text": button["t"]})
        if len(self.problems) > before:
            return False
        self.sleep(1.3)
        return True

    def _open_cards(self, cards, start, visited, section_end):
        """Open each named card by its own address and read it: its name is
        the chapter, its heading the beat, one thing on it pointed at.
        Returns how many opened."""
        opened = 0
        for card in cards:
            if self.now() >= section_end - 3.5 or self._time_up(section_end, 0.0):
                break
            key = self._norm_url(card["href"])
            if key in visited:
                continue
            visited.add(key)
            if not self._open({"t": card["t"], "href": card["href"],
                               "item": None}, start):
                visited.add(self._norm_url(self.page.url))
                continue
            visited.add(self._norm_url(self.page.url))
            opened += 1
            self.note("section", label=card["t"])
            self._chapters.append(self.now())
            self.sleep(0.9)
            info = self._page()
            self._look(info)
            self.sleep(1.2)
            if self._demo_form(section_end):
                continue                # the tool was used: that is the beat
            items, _h = self._tour_items()
            here = self._feature(items)
            if here and not self._time_up(section_end, 0.0):
                self._point(here, 1.0)
        return opened


    def do_tour(self, step):
        """Show the site the way a person demos it, as beats an edit cuts on:
        the hero (the pointer finds its call to action), then up to `by`
        section pages, each opened from the navigation and read from its own
        heading down through one or more stops, then the closing call to
        action, framed, live to the end. A page the request points at (its
        pricing page), or a one-page site, is travelled down by its own
        sections instead. Never back up, never a revisit, never the footer
        in the middle. A section that leads to a sign-in, another site or a
        blank page is skipped. Nothing is ever submitted."""
        want = max(0, min(int(step.get("by") or 2), 6))
        start = self.page.url
        _hide_consent(self.page)
        until = float(step.get("seconds") or 0) or 18.0
        scroll_mode = step.get("mode") == "scroll"
        items, _heads = self._tour_items()
        cta = self._cta([i for i in items if not i.get("fixed")])
        # the hero: the opening title plays over it, then the pointer finds
        # the call to action (no push-in there: the title owns the opening),
        # or drifts into the hero when it has none in its body
        self.sleep(0.1)
        if cta:
            # the pointer reaches it as the title leaves and presses it (the
            # press ring only, nothing is clicked): the first beat after the
            # title, with a push on it (the judge: "click Upload video with a
            # push-in right after the reveal")
            cx, cy = float(cta["x"]) * self.W, float(cta["y"]) * self.H
            self.move_to(cx, cy, seconds=0.8)
            b = cta.get("box")
            self.note("hover", cx, cy, str(cta.get("t") or ""),
                      box=({"x": b[0] * self.W, "y": b[1] * self.H,
                            "width": (b[2] - b[0]) * self.W,
                            "height": (b[3] - b[1]) * self.H} if b else None))
            self.sleep(0.15)
            try:
                self.page.evaluate(_PRESS_JS, [cx, cy])
            except Exception:
                pass
            self.sleep(1.0)
        else:
            self.move_to(self.W * 0.56, self.H * 0.55, seconds=1.1)
            self.sleep(0.3)
        reserve = self.FINALE_S
        if scroll_mode:
            self._travel(until, reserve, scroll_mode=True)
            self._finale(until, scroll_mode=True)
            return
        deep = _deep_page(getattr(self, "requested", None) or start)
        brief = bool(step.get("brief"))
        cands = [] if deep or not want else \
            self._candidates(items, step.get("prefer"), start, brief=brief,
                             want=want)
        if not cands:
            # a one-page site, or the page the request points at: its own
            # sections, top to bottom, then its closing call to action
            self._travel(until, reserve)
            self._finale(until)
            return
        n = min(want, len(cands))
        # the chapters are the section pages, not their sub-headings
        self._paged = True
        item_ask = bool(_ITEM_ASK.search(str(step.get("prefer") or "")))
        if brief and item_ask:
            # the product page the brief asks for is paid for up front, not
            # from what the sections leave (klosmo: the brands page ate it);
            # the sections that fit in what is left are the ones that lead
            # to products (a collection), before an A-Z list of brands
            reserve += 4.5
            shop = [c for c in cands if brief_tour.leads_to_products(c["t"])]
            cands = shop + [c for c in cands if c not in shop]
            n = max(1, min(n, int((until - self.now() - reserve - 3.0) / 5.0)))
        left = until - self.now() - reserve
        # a brief's hero is not held for a stop of its own unless there is time to
        # spare after every page (klosmo, judge: "5.6 s on the hero, only the
        # cursor drifts")
        home_stop = left >= (n * 9.0 + 8.0 if brief else n * 5.0 + 2.6)
        per = max(1, min(3, int((left - (2.6 if home_stop else 0.0)
                                 - n * 2.4) / (2.6 * n))))
        if home_stop:
            self._travel(until, reserve, most=1)
        opened, visited = 0, {self._norm_url(start)}
        item_done = False
        for cand in cands:
            if opened >= n or self._time_up(until, reserve + 3.0):
                break
            key = self._norm_url(cand["href"])
            if key in visited:
                continue
            slot = section_end = None
            if brief:
                # the time left is shared by the sections still to open, the
                # earlier ones a little short so the last page (where a brief
                # names its items) is not starved
                rem = n - opened
                slot = brief_tour.section_slot(
                    until + self.lost_s - reserve - self.now(), rem) \
                    * (0.55 if rem > 1 else 1.0)
                if opened and slot < 4.2:
                    break
                section_end = self.now() + slot
            visited.add(key)
            if not self._open(cand, start):
                visited.add(self._norm_url(self.page.url))
                continue
            visited.add(self._norm_url(self.page.url))
            opened += 1
            # the section opens on its name (the edit sets it over the
            # page, softened), then its own heading is the beat: long enough
            # for a push to ease in, hold and let go before the page moves on
            self.note("section", label=str(cand["t"])[:40])
            self._chapters.append(self.now())
            self.sleep(0.9)
            info = self._page()
            self._look(info)
            self.sleep(1.3 if brief else 1.7)
            if brief and item_ask and opened >= n and not item_done:
                # the last section of a brief that asks for a product page:
                # its grid is scrolled into view and one product opens now,
                # while the grid is still near the top (the lazy grid is
                # scrolled past by the usual stops)
                item_done = bool(self._open_item(start))
                if item_done:
                    continue
            if brief:
                cards = self._named_cards(
                    step.get("prefer"), start,
                    brief_tour.label_stems(cand["t"]), visited)
                # a card page costs about CARD_S seconds: its load, its name,
                # its heading and one use of the tool (or the one thing pointed at)
                room = max(0, int((slot - 5.0) / CARD_S))
                cards = cards[:room]
                card_s = CARD_S * len(cards)
                self._travel(section_end - card_s - self.lost_s, 0.0,
                             most=brief_tour.stops_for(
                                 slot - card_s, open_s=5.0, per_stop_s=3.0,
                                 least=0))
                if cards:
                    self._open_cards(cards, start, visited, section_end)
            else:
                self._travel(until, reserve, most=per)
        if item_ask and not brief:
            # "open a product page to highlight product imagery and details"
            # (a real brief): the last page opens one of its own items
            reserve += 4.5
        if not opened:
            self._travel(until, reserve)
        elif not (brief and item_done) and \
                not self._time_up(until, reserve + 2.6):
            # time left over: further down the last page, before its finale
            self._travel(until, reserve)
        if item_ask and not item_done:
            item_done = bool(self._open_item(start))
        self._finale(until, here_only=bool(brief and item_done))

    def _open_item(self, start):
        """Open one item of the page (a product, a project) by its own card,
        and read it from its heading."""
        for _attempt in range(2):
            items, _h = self._tour_items()
            pick = None
            for i in items:
                href = str(i.get("href") or "")
                if i.get("fixed") or i.get("ext") or not href or \
                        _off_tour(start, href) or \
                        not _ITEM_PATH.search(urlparse(href).path or ""):
                    continue
                if not 0.2 < float(i.get("y") or 0) < 0.8:
                    continue
                if pick is None or float(i.get("area") or 0) > float(pick.get("area") or 0):
                    pick = i
            if pick:
                break
            pos = float(self.page.evaluate("() => window.scrollY") or 0) + self.H * 0.7
            self.note("scroll")
            self._glide_scroll(pos, 1.0)
            self.sleep(0.3)
        if not pick:
            return False
        if not self._open({"t": pick["t"], "href": pick["href"], "item": pick},
                          start):
            return False
        self._look()
        self.sleep(1.7)
        return True

    def _live_end(self):
        """The last ~2 s keep moving: the pointer glides, then the page
        eases a little further down (a frames-on-change capture of a still
        page is a held frame, which the judge marks as a freeze before the
        end card; C2: linear.app ended on 2.3 s of one frame)."""
        try:
            y = float(self.page.evaluate("() => window.scrollY") or 0)
            bottom = float(self.page.evaluate(
                "() => document.documentElement.scrollHeight - window.innerHeight") or 0)
        except Exception:
            y, bottom = 0.0, 0.0
        self.drift(0.8)
        if bottom - y > self.H * 0.06:
            self._glide_scroll(min(bottom, y + self.H * 0.12), 1.4)
        else:
            x = min(max(self.mx - self.W * 0.12, self.W * 0.2), self.W * 0.8)
            self.move_to(x, min(self.my + self.H * 0.05, self.H * 0.8),
                         seconds=1.2)

    def run(self, steps):
        handlers = {"click": self.do_click, "hover": self.do_hover,
                    "type": self.do_type, "press": self.do_press,
                    "scroll": self.do_scroll, "goto": self.do_goto,
                    "tour": self.do_tour}
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
           # -fps_mode (FFmpeg >= 5.1) — the old -vsync spelling is gone
           # from FFmpeg 9 (and the capture failed to encode there)
           "-an", "-fps_mode", "cfr", "-r", "30",
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


_OFF_TOUR = re.compile(r"(^|[/.])(log-?in|sign-?in|sign-?up|register|auth|"
                       r"account|dashboard|checkout|cart)([/.?#-]|$)", re.I)


# The cursor overlay's press ring, played without a click: the event goes to
# the window (where the injected cursor listens), never to an element.
_PRESS_JS = r"""([x, y]) => {
  window.dispatchEvent(new MouseEvent('mousedown', {clientX: x, clientY: y}));
  setTimeout(() => window.dispatchEvent(new MouseEvent('mouseup', {clientX: x, clientY: y})), 160);
}"""

_LOCALE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2,4})?$", re.I)


_ITEM_ASK = re.compile(r"product (?:page|detail)|product's page|item page|"
                       r"p[aá]gina d[eo] produto|p[aá]gina del producto|"
                       r"page produit|open a product", re.I)
_ITEM_PATH = re.compile(r"/(?:products?|p|item|items|shop/[^/]+|collections/[^/]+/products)/[^/]+",
                        re.I)


def _deep_page(url):
    """Whether ``url`` points at one page of a site (its pricing page)
    rather than its home: a path beyond an index file or a locale ("/ae",
    "/en-us": stripe.com redirects to the visitor's locale)."""
    parts = [p for p in (urlparse(str(url or "")).path or "").lower().split("/")
             if p]
    parts = [p for p in parts if not _LOCALE.match(p)
             and p not in ("index.html", "index.htm", "index.php", "home")]
    return bool(parts)


CARD_S = 6.5   # a tool page in a brief tour: load, name, heading, one use


def _off_tour(start, url):
    """A page a demo tour must not show: a sign-in or checkout flow, or
    another site (a subdomain like dashboard.example.com counts)."""
    try:
        a = (urlparse(start).hostname or "").lower().removeprefix("www.")
        b = (urlparse(url).hostname or "").lower().removeprefix("www.")
        path = (urlparse(url).hostname or "") + (urlparse(url).path or "")
    except Exception:
        return False
    return (a and b and a != b) or bool(_OFF_TOUR.search(path))


_ON_TOP_JS = r"""(el) => {
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) return true;
  const top = document.elementFromPoint(x, y);
  return !!top && (el === top || el.contains(top) || top.contains(el));
}"""


_PAINTED_JS = r"""() => {
  const W = window.innerWidth, H = window.innerHeight; let hits = 0;
  for (let i = 1; i <= 3; i++) for (let j = 1; j <= 5; j++) {
    const el = document.elementFromPoint(W * i / 4, H * j / 6);
    if (!el) continue;
    const tag = el.tagName;
    if (['IMG', 'VIDEO', 'CANVAS', 'SVG', 'svg', 'PICTURE'].includes(tag) ||
        ((el.innerText || '').trim().length > 0 && el !== document.body &&
         el !== document.documentElement)) hits++;
  }
  return hits >= 3;
}"""


_DECLINE_JS = r"""() => {
  const want = /^(reject all|reject|decline|decline all|deny|only necessary|necessary only|use necessary cookies only|reject non-essential)$/i;
  for (const el of document.querySelectorAll('button,[role=button],a')) {
    const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
    const r = el.getBoundingClientRect();
    if (want.test(t) && r.width > 0 && r.height > 0) { el.click(); return t; }
  }
  return '';
}"""


# A consent banner that offers no privacy-preserving choice (only "Accept
# all" and "Manage") covered the lower third of a whole tour (Q2: a real
# user's site). The tour never accepts it: it hides the banner from the
# picture, which consents to nothing.
_HIDE_CONSENT_JS = r"""() => {
  const W = innerWidth, H = innerHeight; let hidden = 0;
  const words = /cookie|consent|gdpr|we use cookies|privacy preferences/i;
  for (const el of document.querySelectorAll('body *')) {
    const st = getComputedStyle(el);
    if (st.position !== 'fixed' && st.position !== 'sticky') continue;
    if (st.display === 'none' || st.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width * r.height < W * H * 0.04) continue;
    const t = (el.innerText || '').slice(0, 1600);
    if (!words.test(t) || t.length > 1500) continue;
    const named = /cookie|consent|gdpr|cmp|onetrust|privacy/i.test((el.id || '') + ' ' + String(el.className || ''));
    if (!named && el.querySelector('nav, header')) continue;
    el.style.setProperty('display', 'none', 'important');
    hidden++;
  }
  if (hidden && document.body) document.body.style.setProperty('overflow', 'auto', 'important');
  return hidden;
}"""


def _hide_consent(page):
    try:
        return int(page.evaluate(_HIDE_CONSENT_JS) or 0)
    except Exception:
        return 0


def _decline_consent(page):
    """A cookie banner covers the page a demo is about. Before frame zero,
    take its privacy-preserving choice (reject / necessary only) when it
    offers one; a banner with only an accept button is left alone."""
    try:
        if page.evaluate(_DECLINE_JS):
            page.wait_for_timeout(500)
    except Exception:
        pass


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


def _record(url, workdir, orientation, wall_s, cursor, body, css=None):
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
    # CSS size and pixel density: the frames are W x H pixels either way
    CW, CH, DPR = (_CSS_VIEWPORTS if css is None else css).get(
        orientation or "landscape", (W, H, 1.0))
    deadline = time.monotonic() + wall_s

    session = None
    title, final_url, h1, site_name = "", url, "", ""
    browser = None
    list_path, cast_len = None, 0.0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--mute-audio", "--hide-scrollbars",
            ] + ([f"--force-device-scale-factor={DPR:g}"] if DPR != 1.0
                 else []))       # the screencast is CSS-sized without it
            context = browser.new_context(
                viewport={"width": CW, "height": CH},
                device_scale_factor=DPR,
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
            # the session works in CSS pixels: pointer moves and event
            # positions are fractions of the viewport either way
            session = _Session(page, deadline, (CW, CH))
            session.requested = url
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
            _decline_consent(page)
            session.t_zero = time.time()
            title = (page.title() or "")[:120]
            final_url = page.url or url
            try:
                h1 = page.evaluate(
                    "() => { const h = document.querySelector('h1');"
                    " return h ? h.innerText.trim().replace(/\\s+/g, ' ')"
                    ".slice(0, 160) : ''; }") or ""
            except Exception:
                h1 = ""
            try:
                site_name = (page.evaluate(
                    "() => { const m = document.querySelector("
                    "'meta[property=\"og:site_name\"]');"
                    " return m ? (m.content || '').trim().slice(0, 60) : ''; }")
                    or "")
            except Exception:
                site_name = ""
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
        "final_url": final_url, "page_title": title, "h1": h1,
        "site_name": site_name,
        "events": session.events if session else [],
        "problems": session.problems if session else [],
    })


def record(url, workdir, duration_s=12.0, orientation="landscape",
           scroll=True, cursor=False, phone_layout=False):
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
                   cursor, body,
                   css=None if phone_layout else _DESKTOP_VIEWPORTS)


def record_demo(url, workdir, steps, orientation="landscape",
                phone_layout=False):
    """Drive `url` through `steps` with a visible cursor and record it.

    Returns the same dict as record(), where `events` is the timestamped
    track of every click / scroll / keystroke run / navigation, positioned as
    0-1 fractions of the frame, and `problems` lists anything the script
    asked for that the page could not deliver.
    """
    plan = _norm_steps(steps)
    toured = any(st.get("do") == "tour" for st in plan)

    def body(s):
        # A beat on the opening screen before anything moves — the viewer has
        # to see WHERE they are before they can follow what is being done.
        s.sleep(0.9)
        s.run(plan)
        if toured and plan[-1].get("do") == "tour":
            return          # the tour ends on its own live finale
        # And a beat at the end, so the last state is readable and the editor
        # has frames to cut on rather than ending mid-gesture. The pointer
        # keeps moving through it: a still page here is a freeze before the
        # end card (C1, 29 Oct 2026).
        s.sleep(0.3)
        try:
            s.drift(0.9)
        except Exception:
            s.sleep(0.7)

    return _record(url, workdir, orientation, config.WEB_DEMO_WALL_S,
                   True, body, css=(_TOUR_VIEWPORTS if toured else
                                    None if phone_layout
                                    else _DESKTOP_VIEWPORTS))


def element_box(box, W, H):
    """A Playwright bounding box (page px) as [x0, y0, x1, y1] fractions of
    the W x H capture, clipped to the frame."""
    def c(v):
        return round(min(max(v, 0.0), 1.0), 4)
    x, y = float(box.get("x", 0.0)), float(box.get("y", 0.0))
    w, h = float(box.get("width", 0.0)), float(box.get("height", 0.0))
    return [c(x / max(1, W)), c(y / max(1, H)), c((x + w) / max(1, W)),
            c((y + h) / max(1, H))]


def element_at(events, t, kinds=("click", "type", "hover"), tol=0.6):
    """The DOM element box of the event nearest program-free capture second
    ``t`` (within ``tol``), or None — the origin for an iris, the target of
    a push, the crop of a UI breakout (3F-4)."""
    best = None
    for e in events or []:
        if e.get("kind") not in kinds or not e.get("box"):
            continue
        d = abs(float(e.get("t", 0.0)) - float(t))
        if d <= tol and (best is None or d < best[0]):
            best = (d, e["box"])
    return best[1] if best else None


def summarize_events(events):
    """One compact line per event, as the agent reads them."""
    bits = []
    for e in events:
        where = (f" at ({e['x']:.2f},{e['y']:.2f})"
                 if "x" in e and "y" in e else "")
        label = f" \"{e['label']}\"" if e.get("label") else ""
        box = (" box [" + ",".join(f"{v:.2f}" for v in e["box"]) + "]"
               if e.get("box") else "")
        bits.append(f"{e['t']:.2f}s {e['kind']}{label}{where}{box}")
    return "; ".join(bits)


def events_json(events):
    return json.dumps(events, separators=(",", ":"))


# ── Running the browser somewhere it fits (round 61) ─────────────────────────
#
# On 2026-07-30 a customer asked for website b-roll and the FIRST real
# record_website call this product has ever served killed the worker:
#
#   13:15:28  "record a website (like a Google search for 'plumber near me'
#              or a business listing) For b-rolls"
#   13:15:57  the model asks for get_kept_transcript + three record_website
#   13:16:15  last heartbeat, then nothing
#   13:18:27  the reaper: "Worker died and retries are exhausted"
#
# A tool call runs inside the agent turn, and agent turns run LOCALLY on the
# dispatcher — deliberately, because they are network-bound waiting on an LLM
# (worker/main._build_runners). That is exactly the wrong box for a full
# headless Chromium at 1080x1920 rendering Google Search while a CDP screencast
# drains frames: the same small instance whose memory ceiling had already been
# killing filmstrip jobs that morning.
#
# The careful error handling in agent_tools.record_website — "Could not record
# that page, offer the alternative" — could not fire, because an OOM kill is a
# disappearance, not an exception (round 57). MAX_ATTEMPTS_AGENT is 1, so there
# was no retry either. The customer's turn simply ended.
#
# The executor already has Chromium (one image, two roles — the Dockerfile
# bakes `playwright install --with-deps chromium` for both), 8 vCPU and 32Gi.
# So the capture runs THERE and only its result comes back. The recorded file
# never crosses the wire: the executor uploads it to storage and returns the
# key, which is the same key _store_capture would have written.

def run_capture_job(worker_db, job):
    """Executor-side runner for a web capture. Returns the `got` dict that
    record()/record_demo() produce, with `path` replaced by `storage_key`.

    `worker_db` is unused — a capture writes no rows. The asset is registered
    by the DISPATCHER, which owns the ToolContext and the project's DB writes;
    this side only produces bytes. Keeping it that way means a capture that
    succeeds but whose caller has gone away leaves an orphaned object, not a
    half-registered asset pointing at nothing.
    """
    import shutil as _shutil
    import uuid as _uuid

    import storage as _storage
    import url_media as _url_media

    payload = job.get("payload") or {}
    project_id = job.get("project_id")
    mode = payload.get("mode") or "record"
    url = payload.get("url")
    if not url:
        raise WebRecordError("no url given")
    if not config.WEB_RECORD_ENABLED or not local_available():
        # local_available, not available(): see its docstring. An executor
        # without a browser is a deployment fault, and the agent must hear it
        # as "cannot", never as a crash.
        raise WebRecordError("web capture is not available on this worker")

    workdir = os.path.join(config.TMP_DIR, f"cap_{_uuid.uuid4().hex[:8]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        if mode == "demo":
            got = record_demo(url, workdir, payload.get("steps") or [],
                              orientation=payload.get("orientation"),
                              phone_layout=bool(payload.get("phone_layout")))
        else:
            got = record(url, workdir,
                         duration_s=payload.get("duration_s"),
                         orientation=payload.get("orientation"),
                         scroll=bool(payload.get("scroll", True)),
                         phone_layout=bool(payload.get("phone_layout")))
        path = got.pop("path")
        key = _url_media.storage_key(project_id, _url_media.KIND_VIDEO, path)
        _storage.upload_file(path, key, _url_media.content_type(path))
        got["storage_key"] = key
        return got
    finally:
        _shutil.rmtree(workdir, ignore_errors=True)
