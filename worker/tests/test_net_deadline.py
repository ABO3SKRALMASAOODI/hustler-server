"""Regression test for the slow-header wedge. Needs loopback sockets.

Run from the worker/ directory:  python tests/test_net_deadline.py

Kept out of test_units.py because that suite promises no network of any kind.
This one is worth its own file anyway: it guards the single worst bug found in
the link-fetching review, and the bug is invisible to any test that does not
actually make a socket behave badly.

WHAT IT GUARDS. requests' `timeout=(connect, read)` is a PER-READ timeout that
resets on every byte, so a server dribbling response headers keeps a download
blocked forever. urllib3's `Timeout(total=...)` does not fix it either — that
was tried and measured: an 8-second budget was still blocked after 40 seconds.
Only closing the socket from another thread interrupts a blocked recv.

WHY IT MATTERS. The elapsed check inside download()'s read loop cannot run
until requests.get() returns; the agent loop only tests its wall clock BETWEEN
tool calls; and the job heartbeat runs on its own thread and keeps marking the
job healthy, so the reaper never reclaims it. One such link pins one of two
agent slots until a redeploy. Two pin the service.
"""

import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import net_fetch                                             # noqa: E402

PASS = 0
STOP = threading.Event()


def check(name, cond):
    global PASS
    assert cond, f"FAIL: {name}"
    PASS += 1
    print(f"  ok  {name}")


def _drip(conn):
    """One byte of response headers every 3s — each read succeeds well inside
    any per-read timeout, which is precisely why per-read timeouts cannot
    bound this."""
    try:
        conn.recv(4096)
        for ch in "HTTP/1.1 200 OK\r\nContent-Type: video/mp4\r\n":
            if STOP.is_set():
                break
            conn.sendall(ch.encode())
            time.sleep(3)
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _serve(sock):
    while not STOP.is_set():
        try:
            sock.settimeout(0.5)
            conn, _ = sock.accept()
        except (socket.timeout, OSError):
            continue
        threading.Thread(target=_drip, args=(conn,), daemon=True).start()


srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0))
srv.listen(5)
threading.Thread(target=_serve, args=(srv,), daemon=True).start()
url = f"http://127.0.0.1:{srv.getsockname()[1]}/movie.mp4"

# Loopback is (correctly) refused by the address policy, and that policy is
# not what is under test here — the TIMEOUT layer is.
_real_check = net_fetch.check_url
net_fetch.check_url = lambda u, allowed_hosts=None: "127.0.0.1"

BUDGET = 8.0
TOLERANCE = BUDGET * 2.5          # generous: the point is "bounded at all"
out = os.path.join(os.getenv("TMPDIR", "/tmp"), "valmera-deadline-test.bin")
try:
    t0 = time.monotonic()
    raised = None
    try:
        net_fetch.download(url, out, max_bytes=1 << 20, timeout_s=BUDGET)
    except net_fetch.FetchError as e:
        raised = str(e)
    elapsed = time.monotonic() - t0
    check(f"download() gives up on a slow-header server ({elapsed:.1f}s)",
          elapsed < TOLERANCE)
    check(f"...and says so honestly ({raised!r})",
          raised is not None and "exceeded" in raised)
    check("no half-written file is left behind", not os.path.exists(out))

    t0 = time.monotonic()
    try:
        net_fetch.head_kind(url, timeout_s=BUDGET)
    except Exception:
        pass
    elapsed = time.monotonic() - t0
    check(f"head_kind() is bounded by the same deadline ({elapsed:.1f}s)",
          elapsed < TOLERANCE)
finally:
    net_fetch.check_url = _real_check
    STOP.set()
    srv.close()
    if os.path.exists(out):
        os.remove(out)

print(f"\nALL {PASS} CHECKS PASSED")
