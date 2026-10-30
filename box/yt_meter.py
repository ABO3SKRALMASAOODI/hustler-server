#!/usr/bin/env python3
"""The YouTube fallback route's traffic meter and prepaid budget (30 Oct 2026).

``YTDLP_PROXY_FALLBACK`` (executor.env, from the GitHub secret of that name)
is the route worker url_media takes only after the primary ISP proxy
(``YTDLP_PROXY``) ended on YouTube's bot wall. The owner's fallback is
IPRoyal residential traffic, which is PREPAID (1.35 GB on 30 Oct, auto
top-up off): a wall during a busy hour moves customers' full downloads
through it (the measured peak, 16 downloads an hour, is about 1 GB), and once
it is spent the fallback refuses the box and every walled stretch fails
customers' YouTube imports again. The worker does not record which route
served a fetch, so the box counts the bytes on the wire instead:

* a COUNTING-ONLY nftables table ``inet valmera_ytmeter``: three base chains
  (forward, for every slot container; output and input, for the host) whose
  policy is accept and whose rules carry nothing but a named counter
  (``fb_tx`` to the fallback, ``fb_rx`` from it). No verdict, no drop, no
  NAT, no logging: every packet's fate is decided by the box's real
  firewall exactly as before;
* yt_canary.py reads it at every look (``take``), adds the bytes to a per-UTC
  -day record, and spends them from the BUDGET: the remaining traffic the
  vendor's dashboard showed at a baseline, minus everything counted since
  (``remaining_est_gb``). Under ``floor_gb`` (0.3) the canary row says
  ``low`` and the Mac's P0 watch (ops-loop/yt_watch.py) starts the Fixer in
  YOUTUBE mode, which emails the owner once a day until it is topped up and
  re-baselined.

The count is conservative: it includes TCP/IP headers and TLS, which the
vendor may not bill, and it treats a vendor "GB" as 10^9 bytes. The vendor's
dashboard stays the authority; re-baseline from it after a top-up:

    yt_canary.py --fallback-baseline <GB shown> [--floor 0.3]
                 [--endpoint host:port ...]   (arms the meter before the
                                               secret reaches executor.env)

Never prints or stores credentials: a proxy URL with user:pass is reduced
to host:port before anything else sees it.
"""

import hashlib
import ipaddress
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit

TABLE = "valmera_ytmeter"
FAMILY = "inet"
TX, RX = "fb_tx", "fb_rx"
DEFAULT_FLOOR_GB = 0.3
KEEP_DAYS = 30
# Fallback traffic that real fetches moved since the last probe run, above
# which the canary probes (at most once per retry_min) to learn whether the
# primary is walled; its own probe bytes never count toward this.
PROBE_TRIGGER_BYTES = 1_000_000


# ── endpoints (host:port only, never credentials) ───────────────────────
def host_port(value):
    """(host, port) of one proxy URL or host:port, without credentials;
    None when there is no host or port."""
    value = (value or "").strip()
    if not value:
        return None
    parts = urlsplit(value if "://" in value else "http://" + value)
    try:
        port = parts.port
    except ValueError:
        return None
    if not parts.hostname or not port:
        return None
    return parts.hostname.lower(), int(port)


def parse_list(value):
    """[(host, port)] of a comma-separated proxy list (YTDLP_PROXY_FALLBACK's
    shape), order kept, duplicates dropped."""
    out = []
    for item in (value or "").split(","):
        hp = host_port(item)
        if hp and hp not in out:
            out.append(hp)
    return out


def env_value(name, env_path):
    try:
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def endpoints(env_path, budget=None):
    """The fallback endpoints to meter: executor.env's YTDLP_PROXY_FALLBACK
    plus any the budget file arms by hand (before the secret lands)."""
    out = parse_list(env_value("YTDLP_PROXY_FALLBACK", env_path))
    for item in (budget or {}).get("endpoints") or []:
        hp = host_port(item)
        if hp and hp not in out:
            out.append(hp)
    return out


def label(hp):
    return "%s:%s" % hp


def _ip_version(host):
    try:
        return ipaddress.ip_address(host).version
    except ValueError:
        return None


def resolve_v4(host):
    """IPv4 addresses of a host (a literal resolves to itself)."""
    if _ip_version(host) == 4:
        return [host]
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET,
                                   socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos})


# ── the counting-only ruleset ───────────────────────────────────────────
def rule_matches(fallbacks, primary=None, primary_ips=()):
    """([tx match], [rx match]) nft match expressions. Pure.

    An IP literal is matched by address and port. A hostname (a gateway such
    as geo.iproyal.com, whose address DNS may rotate) is matched by its port
    alone, minus the primary proxy's addresses when the primary uses that
    same port, so the primary's traffic is never counted as the fallback's.
    A port matched as a whole is not matched again per address (no double
    count)."""
    tx, rx = [], []
    wide = {p for h, p in fallbacks if _ip_version(h) is None}

    def add(t, r):
        if t not in tx:
            tx.append(t)
            rx.append(r)

    for host, port in fallbacks:
        ver = _ip_version(host)
        if ver is not None:
            if port in wide:
                continue
            fam = "ip" if ver == 4 else "ip6"
            add(f"{fam} daddr {host} tcp dport {port}",
                f"{fam} saddr {host} tcp sport {port}")
            continue
        excl = sorted(set(primary_ips)) if (
            primary and primary[1] == port) else []
        if excl:
            s = "{ " + ", ".join(excl) + " }"
            add(f"ip daddr != {s} tcp dport {port}",
                f"ip saddr != {s} tcp sport {port}")
            add(f"meta nfproto ipv6 tcp dport {port}",
                f"meta nfproto ipv6 tcp sport {port}")
        else:
            add(f"tcp dport {port}", f"tcp sport {port}")
    return tx, rx


def ruleset(fallbacks, primary=None, primary_ips=()):
    """(nft script, sha). The script replaces the table atomically (one
    transaction: declare, delete, recreate). Pure."""
    tx, rx = rule_matches(fallbacks, primary, primary_ips)
    lines = [f'        {m} counter name "{TX}"' for m in tx]
    rlines = [f'        {m} counter name "{RX}"' for m in rx]
    body = [
        f"table {FAMILY} {TABLE} {{",
        '    comment "valmera: YouTube fallback traffic meter; counters '
        'only, no verdicts"',
        f"    counter {TX} {{ }}",
        f"    counter {RX} {{ }}",
        "    chain meter_forward {",
        "        type filter hook forward priority -150; policy accept;",
        *lines, *rlines,
        "    }",
        "    chain meter_output {",
        "        type filter hook output priority -150; policy accept;",
        *lines,
        "    }",
        "    chain meter_input {",
        "        type filter hook input priority -150; policy accept;",
        *rlines,
        "    }",
        "}",
    ]
    table = "\n".join(body) + "\n"
    sha = hashlib.sha256(table.encode()).hexdigest()[:12]
    script = (f"table {FAMILY} {TABLE}\n"
              f"delete table {FAMILY} {TABLE}\n" + table)
    return script, sha


def _nft(args, stdin=None, timeout=20):
    return subprocess.run(["nft", *args], input=stdin, capture_output=True,
                          text=True, timeout=timeout)


def read_counters(run=None):
    """{"tx", "rx"} cumulative bytes, or None when the table is absent."""
    proc = (run or _nft)(["-j", "list", "table", FAMILY, TABLE])
    if proc.returncode != 0:
        return None
    got = {}
    for item in (json.loads(proc.stdout or "{}").get("nftables") or []):
        c = item.get("counter")
        if c and c.get("table") == TABLE and c.get("name") in (TX, RX):
            got["tx" if c["name"] == TX else "rx"] = int(c.get("bytes") or 0)
    return {"tx": got.get("tx", 0), "rx": got.get("rx", 0)}


def apply(script, run=None):
    proc = (run or _nft)(["-f", "-"], stdin=script)
    if proc.returncode != 0:
        raise RuntimeError("nft refused the meter table: "
                           + (proc.stderr or "").strip()[-200:])


def remove(run=None):
    (run or _nft)(["delete", "table", FAMILY, TABLE])


# ── accounting (pure) ───────────────────────────────────────────────────
def take(fstate, reading, now):
    """Fold one counter reading into the meter state: (state, new bytes).

    Counters are cumulative since the table was made; a reading below the
    last one means the table was rebuilt or the box rebooted, and counts
    from zero again. None (no table) resets the baseline to zero."""
    fs = dict(fstate or {})
    last = fs.get("last") or {}
    if reading is None:
        fs["last"] = {"tx": 0, "rx": 0}
        return fs, 0
    delta = 0
    for k in ("tx", "rx"):
        cur, prev = int(reading.get(k) or 0), int(last.get(k) or 0)
        delta += cur - prev if cur >= prev else cur
    fs["last"] = {"tx": int(reading.get("tx") or 0),
                  "rx": int(reading.get("rx") or 0)}
    if delta > 0:
        day = now.astimezone(timezone.utc).date().isoformat()
        byday = dict(fs.get("bytes_by_day") or {})
        byday[day] = int(byday.get(day) or 0) + delta
        fs["bytes_by_day"] = {d: byday[d] for d in sorted(byday)[-KEEP_DAYS:]}
        fs["total_bytes"] = int(fs.get("total_bytes") or 0) + delta
    return fs, max(delta, 0)


def spend(budget, delta):
    """The budget with delta bytes spent (a copy; None stays None). Pure."""
    if not budget or budget.get("baseline_gb") is None:
        return budget
    out = dict(budget)
    out["used_bytes"] = int(out.get("used_bytes") or 0) + int(delta or 0)
    return out


def budget_view(budget):
    """What the canary row and the watch read. Pure."""
    if not budget or budget.get("baseline_gb") is None:
        return {"set": False, "low": False}
    floor = float(budget.get("floor_gb", DEFAULT_FLOOR_GB))
    used = int(budget.get("used_bytes") or 0)
    left = float(budget["baseline_gb"]) - used / 1e9
    return {"set": True, "baseline_gb": float(budget["baseline_gb"]),
            "baseline_at": budget.get("baseline_at"),
            "used_gb": round(used / 1e9, 3),
            "remaining_est_gb": round(left, 3), "floor_gb": floor,
            "low": left < floor}


def new_baseline(gb, now, floor_gb=None, endpoints_=None, old=None):
    """A fresh budget after reading the vendor's dashboard. Pure."""
    out = {"baseline_gb": float(gb), "baseline_at": now.isoformat(),
           "used_bytes": 0,
           "floor_gb": float(floor_gb if floor_gb is not None else
                             (old or {}).get("floor_gb", DEFAULT_FLOOR_GB))}
    eps = endpoints_ if endpoints_ is not None else (old or {}).get(
        "endpoints")
    if eps:
        out["endpoints"] = [label(hp) if isinstance(hp, tuple) else str(hp)
                            for hp in eps]
    return out


# ── one look (the canary calls this; side effects are injected) ─────────
def look(fstate, budget, eps, primary, now, run=None, resolve=None):
    """Read, fold, spend, and (re)build the table so it counts ``eps``.

    Returns (meter state, budget, new bytes since the last look). A meter
    failure is recorded in the state (``error``), never raised: the probe
    and the route verdict matter more."""
    fs = dict(fstate or {})
    fs["endpoints"] = [label(hp) for hp in eps]
    delta = 0
    try:
        reading = read_counters(run)
        fs, delta = take(fs, reading, now)
        budget = spend(budget, delta)
        if not eps:
            if reading is not None:
                remove(run)
            fs.pop("rules", None)
            fs["last"] = {"tx": 0, "rx": 0}
        else:
            prim_ips = ((resolve or resolve_v4)(primary[0])
                        if primary else [])
            script, sha = ruleset(eps, primary, prim_ips)
            if reading is None or fs.get("rules") != sha:
                apply(script, run)
                fs["rules"] = sha
                fs["last"] = {"tx": 0, "rx": 0}
        fs.pop("error", None)
    except Exception as exc:  # noqa: BLE001 - reported, the canary goes on
        fs["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    fs["at"] = now.isoformat()
    return fs, budget, delta


def load_budget(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def save_budget(path, budget):
    if budget is None:
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(budget, fh, sort_keys=True)
    os.replace(tmp, path)


def now_utc():
    return datetime.now(timezone.utc)
