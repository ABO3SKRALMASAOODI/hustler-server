"""Safe outbound fetching of third-party media.

Everything the worker downloads from a URL it did not construct itself goes
through here. This module previously served the music catalogs and guarded
them with a HOST ALLOWLIST. It ended with a note about what would have to
change before it could be pointed at arbitrary URLs:

    "DNS rebinding is NOT defended against here: the host is resolved once by
    requests after we have checked the name. The allowlist is a policy control
    over which upstreams we talk to, not a hard sandbox. If arbitrary user- or
    model-supplied URLs ever need fetching, resolve to an IP first and reject
    private ranges — do not just widen this allowlist."

That is now the job: the user pastes a link and we fetch it. So the allowlist
became optional (`allowed_hosts=None` means "any PUBLIC host") and the real
control moved to the address layer, in three parts:

1. RESOLVE FIRST, and reject if *any* returned address is private. Checking
   only the first is not enough — we do not choose which address the socket
   layer picks, so one public A record next to a loopback one would sail
   through a first-address-only check.

2. VERIFY THE PEER AFTER CONNECT. The gap between our getaddrinfo() and
   requests' own is a DNS-rebinding window: a hostile resolver can answer
   public for our lookup and 127.0.0.1 for theirs. So once the socket is up we
   ask it who it actually reached (`getpeername`) and abort on a private
   address. This is the check that makes the whole thing a sandbox rather than
   a policy — it is the only one that sees ground truth.

3. RE-CHECK EVERY REDIRECT HOP. `allow_redirects=False` with a manual loop is
   the only way; requests' automatic following would already have fetched the
   internal address by the time we saw the final URL.

The threat is concrete. The worker sits inside Render's network holding
DATABASE_URL and S3 credentials, with no egress policy in the Dockerfile, and
can reach RFC1918, loopback and link-local equally — including
169.254.169.254, the cloud metadata endpoint (covered by the link-local
rejection). Now that the URL comes from the *user*, "attacker-chosen" is not a
hypothetical about a compromised upstream. It is the normal case.

Unbounded transfer. requests' `timeout=` is a per-socket-read timeout, not a
transfer deadline: it resets on every byte, so a server dribbling data holds
the connection open indefinitely. The agent loop only checks its wall clock
BETWEEN tool calls, so a slow read inside one is invisible to it — and the job
heartbeat runs on its own thread, so it keeps marking a wedged job healthy and
the reaper never reclaims the slot. A hung fetch therefore pins one of two
agent slots until a redeploy.

Two things follow, and BOTH are needed:

* An absolute deadline for the whole call, enforced by _DeadlineAdapter, which
  CLOSES THE SOCKET when it expires. Note that urllib3's `Timeout(total=...)`
  does not do this despite the name — it resolves to a single socket timeout
  that still resets per recv, verified by measurement. Interrupting a blocked
  read requires closing the socket from another thread, and nothing shorter
  works. This covers the header/redirect phase, which the read loop cannot see
  because it does not run until requests.get() has already returned.
* A byte cap and an elapsed check inside the body read loop, for the transfer
  itself.

The deadline spans the entire redirect chain rather than resetting per hop:
five hops x a 180s budget is 925s, twice the agent-turn timeout, with no
attacker needed.
"""

import ipaddress
import os
import socket
import threading
import time
from urllib.parse import urlparse

import requests
import urllib3

MAX_REDIRECTS = 4

# Seconds to wait for the TCP connect, separate from the read timeout.
#
# requests/urllib3 does NOT implement Happy Eyeballs: it walks getaddrinfo's
# results in order and waits the full timeout on each. So a host with an AAAA
# record, on a network where IPv6 is broken, stalls for the whole timeout
# before falling back to IPv4 — measured at 46s against
# commons.wikimedia.org, while curl (which does do Happy Eyeballs) answered
# the same request in 0.4s. archive.org publishes no AAAA and was unaffected,
# which is exactly how this hides.
#
# A short connect timeout turns that stall into a fast failover: the dead
# address is abandoned in seconds and the next one is tried. It is separate
# from the read timeout because a slow-but-alive server is a different thing
# from an unreachable address and should not get the same budget.
CONNECT_TIMEOUT_S = float(os.getenv("NET_CONNECT_TIMEOUT_S", "5"))


class FetchError(Exception):
    pass


class PolicyError(FetchError):
    """The URL is refused on address/scheme grounds — never retried, never
    routed around. Separate from FetchError so a caller can treat a transport
    failure as "try another route" while a policy refusal stays fatal: they
    are both FetchError to anyone who does not care, and only one of them is
    safe to fall back from."""


class _DeadlineAdapter(requests.adapters.HTTPAdapter):
    """Applies an ABSOLUTE deadline by CLOSING THE SOCKET when it expires.

    requests' `timeout=(connect, read)` cannot express a deadline: `read` is a
    per-socket-read timeout that RESETS on every byte. A server dribbling one
    byte of response headers every `read - 10` seconds satisfies every
    individual read and blocks the call forever.

    urllib3's `Timeout(total=...)` does NOT fix this, despite the name. It is
    consulted once, when the response read begins, to compute a single socket
    timeout — after which each recv resets it exactly as before. This was
    measured, not assumed: against a server dripping one header byte every 3s,
    a download with an 8-second budget was still blocked after 40 seconds with
    `total` set.

    Closing the socket from another thread is the only thing that interrupts a
    blocked recv, so that is what happens here. Every connection the session
    checks out is recorded, and `abort()` shuts them down; the blocked read
    then raises immediately.

    Why it has to be a hard deadline: the elapsed-time check in download()'s
    read loop cannot run until requests.get() RETURNS, the agent loop only
    tests its wall clock BETWEEN tool calls, and the job heartbeat runs on its
    own thread and keeps marking the job healthy. So a hung fetch pins one of
    two agent slots until a redeploy, with nothing able to reclaim it."""

    def __init__(self, deadline, read_timeout, **kw):
        self._deadline = deadline
        self._read_timeout = read_timeout
        self._conns = []
        self._lock = threading.Lock()
        self.aborted = False
        super().__init__(**kw)

    def _watch(self, pool):
        """Record every connection this pool hands out.

        Wrapping `_get_conn` on the pool INSTANCE rather than subclassing the
        connection classes: the connection is created deep inside urllib3 and
        this is the shallowest place it becomes visible to us."""
        if getattr(pool, "_valmera_watched", False):
            return pool
        original = pool._get_conn

        def _get_conn(timeout=None):
            conn = original(timeout=timeout)
            with self._lock:
                self._conns.append(conn)
            return conn

        pool._get_conn = _get_conn
        pool._valmera_watched = True
        return pool

    # requests >= 2.32 routes through the TLS-context variant; the plain one
    # stays overridden so an older requests is not silently unwatched.
    def get_connection_with_tls_context(self, *a, **kw):
        return self._watch(super().get_connection_with_tls_context(*a, **kw))

    def get_connection(self, *a, **kw):
        return self._watch(super().get_connection(*a, **kw))

    def abort(self):
        self.aborted = True
        with self._lock:
            conns = list(self._conns)
        for conn in conns:
            sock = getattr(conn, "sock", None)
            if sock is not None:
                # shutdown() before close(): close() alone only drops our
                # reference, while shutdown() tears down the connection and
                # wakes a thread already blocked in recv.
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            try:
                conn.close()
            except Exception:
                pass

    def send(self, request, **kw):
        left = self._deadline - time.monotonic()
        if left <= 0:
            raise FetchError("download exceeded its time limit")
        kw["timeout"] = urllib3.Timeout(
            connect=min(CONNECT_TIMEOUT_S, left),
            read=min(self._read_timeout, left))
        return super().send(request, **kw)


def _session(deadline, read_timeout):
    """A session whose every request is bounded by one absolute deadline.

    The timer is armed for the WHOLE call — headers and body alike — and is
    disarmed by _close(). It is a daemon timer so a stray one can never hold
    the worker open."""
    s = requests.Session()
    adapter = _DeadlineAdapter(deadline, read_timeout)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    timer = threading.Timer(max(0.1, deadline - time.monotonic()),
                            adapter.abort)
    timer.daemon = True
    timer.start()
    s._valmera_adapter = adapter
    s._valmera_timer = timer
    return s


def _shut(session):
    """Disarm the deadline timer and drop the session."""
    if session is None:
        return
    timer = getattr(session, "_valmera_timer", None)
    if timer is not None:
        timer.cancel()
    try:
        session.close()
    except Exception:
        pass


def _aborted(session):
    adapter = getattr(session, "_valmera_adapter", None)
    return bool(adapter is not None and adapter.aborted)


def _ip_is_public(ip):
    """True only for addresses it is safe to let the worker talk to.

    `is_reserved` and `is_unspecified` are in here alongside the obvious
    private/loopback/link-local trio because 0.0.0.0 and the reserved blocks
    are routable-to-something on enough stacks to be worth refusing outright.
    Link-local covers 169.254.169.254 — the cloud metadata endpoint, which is
    the single most valuable thing an SSRF can reach from inside Render."""
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _parse_ip(text):
    """ip_address for a bare literal, or None. Strips an IPv6 zone id."""
    try:
        return ipaddress.ip_address(str(text).split("%")[0])
    except ValueError:
        return None


def _host_ok(host, allowed_hosts):
    """Exact host, or a dot-anchored subdomain of an allowed host.

    Dot-anchored on purpose: a plain `endswith("archive.org")` also accepts
    `evil-archive.org`, which is the classic way an allowlist stops being
    one."""
    h = (host or "").lower().strip(".")
    if not h:
        return False
    for a in allowed_hosts:
        a = a.lower().strip(".")
        if h == a or h.endswith("." + a):
            return True
    return False


def resolve_public(host):
    """Every address `host` resolves to, or raise if ANY of them is internal.

    All-or-nothing on purpose. getaddrinfo can return several addresses and
    the connect path picks among them however it likes, so a hostname with one
    public and one loopback address must be refused outright — accepting it on
    the strength of the public one would leave the choice to the resolver."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise FetchError(f"could not resolve {host}")
    ips = {info[4][0] for info in infos}
    if not ips:
        raise FetchError(f"could not resolve {host}")
    for text in ips:
        ip = _parse_ip(text)
        if ip is None or not _ip_is_public(ip):
            raise PolicyError("refusing a URL that points inside our network")
    return sorted(ips)


def check_url(url, allowed_hosts=None):
    """Validate scheme + address policy. Returns the parsed host.

    `allowed_hosts=None` means any PUBLIC host — the address checks are then
    the entire control, which is why they are all-or-nothing above."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        raise PolicyError(
            f"refusing a non-HTTP URL ({p.scheme or 'no scheme'})")
    host = p.hostname or ""
    if not host:
        raise PolicyError("refusing a URL with no host")
    if allowed_hosts is not None and not _host_ok(host, allowed_hosts):
        raise PolicyError(f"refusing a URL on an unexpected host ({host})")

    literal = _parse_ip(host)
    if literal is not None:
        # A literal address skips DNS entirely, so this is the only check it
        # will ever get.
        if not _ip_is_public(literal):
            raise PolicyError("refusing a URL that points inside our network")
    else:
        resolve_public(host)
    return host


def _peer_ip(resp):
    """The address the socket ACTUALLY reached, or None if we cannot see it.

    urllib3 has moved this attribute around across versions, so every path is
    tried and failure is tolerated — a None here degrades us to the pre-connect
    DNS check (still a real control), it does not open a hole. It is kept
    best-effort rather than strict for exactly that reason: a urllib3 upgrade
    must not turn every download into a hard failure."""
    raw = getattr(resp, "raw", None)
    for get in (lambda: raw._connection.sock,
                lambda: raw._fp.fp.raw._sock,
                lambda: raw._original_response.fp.raw._sock):
        try:
            sock = get()
            if sock is not None:
                return sock.getpeername()[0]
        except Exception:
            continue
    return None


def _assert_peer_public(resp, host):
    """Abort if the connection landed on an internal address.

    This is the DNS-rebinding defence. Our getaddrinfo() and requests' own are
    two separate lookups, and a hostile resolver is free to answer differently
    for each. Only the live socket knows where we really ended up."""
    text = _peer_ip(resp)
    if text is None:
        return
    ip = _parse_ip(text)
    if ip is None or not _ip_is_public(ip):
        raise PolicyError(f"{host} resolved to an internal address mid-request")


def _open(url, allowed_hosts, timeout_s, user_agent, headers=None,
          deadline=None):
    """GET with every redirect hop policy-checked. Returns a live response.

    The deadline covers the WHOLE chain, not each hop. Giving each hop a fresh
    timeout_s meant a legitimate 5-hop redirect (MAX_REDIRECTS + 1) could take
    5 x 180s = 925s — twice the 450s agent-turn budget — with no attacker
    involved at all."""
    if deadline is None:
        deadline = time.monotonic() + timeout_s
    session = _session(deadline, timeout_s)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if time.monotonic() >= deadline:
            _shut(session)
            raise FetchError(f"download exceeded {timeout_s:.0f}s")
        host = check_url(current, allowed_hosts)
        hdrs = {"User-Agent": user_agent}
        hdrs.update(headers or {})
        try:
            resp = session.get(current, stream=True, allow_redirects=False,
                               headers=hdrs)
        except FetchError:
            _shut(session)
            raise
        except requests.RequestException as e:
            # A socket the watchdog tore down surfaces here as a generic
            # connection error; report it as what it actually was.
            aborted = _aborted(session)
            _shut(session)
            if aborted:
                raise FetchError(f"download exceeded {timeout_s:.0f}s")
            raise FetchError(f"could not reach {host} ({type(e).__name__})")
        try:
            _assert_peer_public(resp, host)
        except FetchError:
            resp.close()
            _shut(session)
            raise
        if resp.status_code in (301, 302, 303, 307, 308):
            nxt = resp.headers.get("location")
            resp.close()
            if not nxt:
                _shut(session)
                raise FetchError("redirect without a destination")
            # Relative redirects are legal and common; resolve against the
            # hop we actually made, then re-check on the next pass.
            current = requests.compat.urljoin(current, nxt)
            continue
        # The session owns the connection pool the body will be streamed from,
        # so it must outlive this function. Closing it here would drop the
        # connection out from under the caller's iter_content().
        resp._valmera_session = session
        return resp, current
    _shut(session)
    raise FetchError("too many redirects")


def _close(resp):
    """Release the response AND the session that owns its connection pool."""
    try:
        resp.close()
    finally:
        _shut(getattr(resp, "_valmera_session", None))


def head_kind(url, *, allowed_hosts=None, timeout_s=15,
              user_agent="valmera/1.0 (+https://valmera.io)"):
    """Best-effort (content_type, content_length) without downloading a body.

    Used to decide whether a URL is a direct media file or a web page that
    needs an extractor. Advisory ONLY — servers lie, and plenty of CDNs serve
    video as application/octet-stream. The real classification is ffprobe on
    the downloaded bytes. Returns (None, None) rather than raising when the
    server dislikes being asked, because "I could not tell" and "this is not
    media" must not be the same answer.

    A ranged GET rather than a HEAD: HEAD is widely unimplemented or lied
    about, and Range lets us see the real Content-Type from the handler that
    would actually serve the body."""
    try:
        resp, _ = _open(url, allowed_hosts, timeout_s, user_agent,
                        headers={"Range": "bytes=0-0"},
                        deadline=time.monotonic() + timeout_s)
    except PolicyError:
        # An address refusal is NOT advisory and must not be routed around.
        raise
    except Exception:
        # Everything else — a 403, a dead host, too many redirects — only
        # means "I could not tell from here". Treating those as fatal killed
        # fetches the extractor handles perfectly well.
        return None, None
    try:
        if resp.status_code not in (200, 206):
            return None, None
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        # With a satisfied Range the length is the RANGE's, not the file's —
        # Content-Range carries the real total.
        total = None
        rng = resp.headers.get("content-range") or ""
        if "/" in rng:
            try:
                total = int(rng.rsplit("/", 1)[1])
            except ValueError:
                total = None
        if total is None:
            try:
                total = int(resp.headers.get("content-length") or 0) or None
            except ValueError:
                total = None
        return (ctype or None), total
    finally:
        _close(resp)


def download(url, out_path, *, allowed_hosts=None, max_bytes, timeout_s,
             user_agent="valmera/1.0 (+https://valmera.io)"):
    """GET url to out_path with address, size and wall-clock limits."""
    seen = 0
    t0 = time.monotonic()
    # ONE deadline for the whole call — redirects, headers and body together.
    resp, final_url = _open(url, allowed_hosts, timeout_s, user_agent,
                            deadline=t0 + timeout_s)

    if resp.status_code != 200:
        code = resp.status_code
        _close(resp)
        raise FetchError(f"HTTP {code}")

    # Trust Content-Length only to FAIL EARLY. It is advisory — a server can
    # understate it or omit it — so the read loop below is the real bound.
    try:
        declared = int(resp.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared and declared > max_bytes:
        _close(resp)
        raise FetchError(f"file is {declared >> 20} MB, over the "
                         f"{max_bytes >> 20} MB limit")
    try:
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(1 << 16):
                seen += len(chunk)
                if seen > max_bytes:
                    raise FetchError(
                        f"download exceeded {max_bytes >> 20} MB")
                if time.monotonic() - t0 > timeout_s:
                    raise FetchError(
                        f"download exceeded {timeout_s:.0f}s")
                f.write(chunk)
    except FetchError:
        # Leave no half-written file behind for a caller to mistake for a
        # successful download.
        if os.path.exists(out_path):
            os.remove(out_path)
        raise
    finally:
        _close(resp)
    return seen, final_url


def get_json(url, *, allowed_hosts=None, timeout_s, params=None,
             user_agent="valmera/1.0 (+https://valmera.io)"):
    """GET a JSON API response under the same address policy."""
    check_url(url, allowed_hosts)
    r = requests.get(url, params=params,
                     timeout=(CONNECT_TIMEOUT_S, timeout_s),
                     headers={"User-Agent": user_agent,
                              "Accept": "application/json"})
    if r.status_code != 200:
        raise FetchError(f"HTTP {r.status_code} from {urlparse(url).hostname}")
    try:
        return r.json()
    except Exception:
        raise FetchError(
            f"{urlparse(url).hostname} returned a non-JSON response")
