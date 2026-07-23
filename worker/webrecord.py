"""Record a scrolling screen capture of a live web page as real video.

The user asks "show valmera.io in my edit" / "record my landing page and cut
it in". A headless Chromium loads the page at the project's aspect, holds the
top, smooth-scrolls to the bottom and holds again — the classic product-demo
pan a human editor would screen-record by hand — and the capture becomes an
ordinary project video asset the agent can insert or overlay like any upload.

SECURITY. The page URL passes net_fetch.check_url before the browser exists
(kills file://, literal internal addresses, hosts that resolve internally).
That alone is NOT enough: a page fetches subresources, and any of them could
point back inside our network — so every request the browser makes is routed
through a per-request interceptor that applies the same resolve-then-refuse
policy, with a per-host verdict cache so DNS happens once per host, not once
per request. That still leaves DNS-rebinding style gaps (verdict cached,
connection made later); egress policy on the worker remains the real fix,
same caveat as url_media's extractor path.

RESOURCES. Chromium on this ~1 vCPU box is heavy but bounded: one page, no
GPU, --disable-dev-shm-usage (Render's /dev/shm is tiny), a hard wall-clock
deadline enforced around every step, and the browser is closed in a finally.
Playwright records WebM/VP8; ffmpeg transcodes to the same H.264 yuv420p
faststart profile every other asset uses so downstream never learns where
the file came from.
"""

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


def record(url, workdir, duration_s=12.0, orientation="landscape",
           scroll=True):
    """Record `url` for ~duration_s seconds. Returns dict(path, width,
    height, duration_s, final_url, page_title). Raises WebRecordError with a
    user-fit sentence."""
    try:
        net_fetch.check_url(url)
    except net_fetch.FetchError as e:
        raise WebRecordError(str(e))
    if not available():
        raise WebRecordError("website recording is not installed on this "
                             "deployment")
    from playwright.sync_api import sync_playwright, Error as PWError

    dur = min(max(float(duration_s or 12.0), 4.0),
              config.WEB_RECORD_MAX_DURATION_S)
    W, H = _VIEWPORTS.get(orientation or "landscape", _VIEWPORTS["landscape"])
    deadline = time.monotonic() + config.WEB_RECORD_WALL_S

    def left():
        r = deadline - time.monotonic()
        if r <= 0:
            raise WebRecordError("the page took too long to load and record")
        return r

    raw_dir = os.path.join(workdir, "pwvideo")
    os.makedirs(raw_dir, exist_ok=True)
    title, final_url = "", url
    lead_s = 0.0
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[
                "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                "--mute-audio", "--hide-scrollbars",
            ])
            context = browser.new_context(
                viewport={"width": W, "height": H},
                record_video_dir=raw_dir,
                record_video_size={"width": W, "height": H},
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0 Safari/537.36 ValmeraCapture"),
            )
            cache = {}
            context.route("**/*", _guard_router(cache))
            page = context.new_page()
            # Recording starts the moment the page exists — everything until
            # the post-load settle is white/half-painted loading noise, which
            # would open every capture with a blank flash. Measure it and cut
            # it off in the transcode below.
            rec_t0 = time.monotonic()
            try:
                page.goto(url, wait_until="load",
                          timeout=min(30.0, left()) * 1000)
            except PWError as e:
                msg = str(e).splitlines()[0][:160]
                if "Timeout" not in msg:
                    raise WebRecordError(
                        f"could not open that page ({msg})")
                # Slow pages: record whatever has painted rather than fail.
            # Let fonts/first paint settle, then the capture choreography:
            # hold the top, smooth-scroll the page, hold the end.
            page.wait_for_timeout(min(1500, int(left() * 1000)))
            lead_s = max(0.0, time.monotonic() - rec_t0 - 0.2)
            title = (page.title() or "")[:120]
            final_url = page.url or url
            if scroll:
                total_h = page.evaluate(
                    "() => Math.max(document.body ? document.body.scrollHeight"
                    " : 0, document.documentElement ?"
                    " document.documentElement.scrollHeight : 0)")
                travel = max(0, int(total_h or 0) - H)
                hold_ms = 1200
                scroll_ms = max(0, int(dur * 1000) - 2 * hold_ms)
                page.wait_for_timeout(min(hold_ms, int(left() * 1000)))
                if travel > 0 and scroll_ms > 500:
                    steps = max(1, scroll_ms // 40)   # ~25 moves/second
                    for i in range(1, int(steps) + 1):
                        # ease-in-out so the pan starts and lands softly
                        f = i / steps
                        eased = f * f * (3 - 2 * f)
                        page.evaluate(
                            f"window.scrollTo(0, {int(travel * eased)})")
                        page.wait_for_timeout(40)
                        if deadline - time.monotonic() <= 2:
                            break
                else:
                    page.wait_for_timeout(
                        min(scroll_ms, max(0, int(left() * 1000) - 2000)))
                page.wait_for_timeout(min(hold_ms, int(left() * 1000)))
            else:
                page.wait_for_timeout(
                    min(int(dur * 1000), max(0, int(left() * 1000) - 2000)))
            # close() flushes the recording to disk — must happen in-scope
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

    webms = [os.path.join(raw_dir, f) for f in os.listdir(raw_dir)
             if f.endswith(".webm")]
    if not webms:
        raise WebRecordError("the page loaded but no video was captured")
    raw = max(webms, key=os.path.getsize)

    out = os.path.join(workdir, "webrecord.mp4")
    # Same delivery profile as every render: H.264 high, yuv420p, faststart.
    # -r 30 re-times Chromium's variable capture rate to CFR so the composite
    # concat never fights a VFR insert. -ss AFTER -i (frame-exact) drops the
    # measured page-load lead — without it every capture opens on a white
    # half-painted flash.
    cmd = ["ffmpeg", "-y", "-i", raw, "-ss", f"{lead_s:.2f}", "-an",
           "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode("utf-8", "replace")[-200:]
        raise WebRecordError(f"could not encode the capture ({tail})")
    except subprocess.TimeoutExpired:
        raise WebRecordError("encoding the capture took too long")
    try:
        os.remove(raw)
    except OSError:
        pass

    import media
    try:
        info = media.probe(out)
    except Exception as e:
        raise WebRecordError(f"the capture is unreadable ({e})")
    return {"path": out, "width": info["width"], "height": info["height"],
            "duration_s": info["duration"], "final_url": final_url,
            "page_title": title}
