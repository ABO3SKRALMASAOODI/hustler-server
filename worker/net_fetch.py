"""Safe outbound fetching of third-party media.

Everything the worker downloads from a URL it did not construct itself goes
through here. That is a short list today (the music catalogs) but the failure
modes are the same for any future one, and they are not obvious:

SSRF. renderer._fetch() and llm._download_image() take a URL and GET it with
no validation of any kind — no scheme check, no host check, no redirect
handling. The worker sits inside Render's network with a DATABASE_URL and S3
credentials in its environment, and can reach RFC1918, loopback and
link-local equally (there is no egress policy in the Dockerfile). A URL that
reaches this module comes from a third-party API response, which is one
compromised or simply odd upstream away from being attacker-chosen, so the
host is checked against an allowlist BEFORE connect and again after every
redirect. `allow_redirects=False` with a manual hop loop is the only way to
check the intermediate hops — requests' automatic following would have
already fetched the internal address by the time we saw the final URL.

Unbounded transfer. requests' `timeout=` is a per-socket-read timeout, not a
transfer deadline: it resets on every byte, so a server dribbling data holds
the connection open indefinitely. The agent loop only checks its wall clock
BETWEEN tool calls, so a slow read inside one is invisible to it. Both a byte
cap and a total-elapsed cap are therefore enforced in the read loop itself.
(This exact pair of bugs was found in music_gen.py by review, which is why
the logic lives in one place now rather than being written a third time.)

DNS rebinding is NOT defended against here: the host is resolved once by
requests after we have checked the name. The allowlist is a policy control
over which upstreams we talk to, not a hard sandbox. If arbitrary user- or
model-supplied URLs ever need fetching, resolve to an IP first and reject
private ranges — do not just widen this allowlist.
"""

import ipaddress
import os
import socket
import time
from urllib.parse import urlparse

import requests

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


def _looks_internal(host):
    """True when the host is a literal private/loopback/link-local address.

    A cheap belt to the allowlist's braces: it costs nothing and catches the
    case where an allowlist entry is ever widened carelessly. Hostnames are
    not resolved here — see the DNS-rebinding note in the module docstring."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def check_url(url, allowed_hosts):
    """Validate a URL against scheme + host policy. Returns the parsed host."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        raise FetchError(f"refusing a non-HTTP URL ({p.scheme or 'no scheme'})")
    host = p.hostname or ""
    if _looks_internal(host):
        raise FetchError("refusing an internal address")
    if not _host_ok(host, allowed_hosts):
        raise FetchError(f"refusing a URL on an unexpected host ({host})")
    return host


def download(url, out_path, *, allowed_hosts, max_bytes, timeout_s,
             user_agent="valmera/1.0 (+https://valmera.io)"):
    """GET url to out_path with host, size and wall-clock limits.

    Redirects are followed MANUALLY so every hop is policy-checked; an
    automatic follow would already have made the request before we could
    inspect where it went."""
    seen = 0
    current = url
    t0 = time.monotonic()
    resp = None
    for _ in range(MAX_REDIRECTS + 1):
        check_url(current, allowed_hosts)
        resp = requests.get(current, stream=True,
                            timeout=(CONNECT_TIMEOUT_S, timeout_s),
                            allow_redirects=False,
                            headers={"User-Agent": user_agent})
        if resp.status_code in (301, 302, 303, 307, 308):
            nxt = resp.headers.get("location")
            resp.close()
            if not nxt:
                raise FetchError("redirect without a destination")
            # Relative redirects are legal and common; resolve against the
            # hop we actually made, then re-check on the next pass.
            current = requests.compat.urljoin(current, nxt)
            continue
        break
    else:
        raise FetchError("too many redirects")

    if resp is None or resp.status_code != 200:
        code = resp.status_code if resp is not None else "?"
        if resp is not None:
            resp.close()
        raise FetchError(f"HTTP {code}")

    # Trust Content-Length only to FAIL EARLY. It is advisory — a server can
    # understate it or omit it — so the read loop below is the real bound.
    try:
        declared = int(resp.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared and declared > max_bytes:
        resp.close()
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
        resp.close()
    return seen


def get_json(url, *, allowed_hosts, timeout_s, params=None,
             user_agent="valmera/1.0 (+https://valmera.io)"):
    """GET a JSON API response under the same host policy."""
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
