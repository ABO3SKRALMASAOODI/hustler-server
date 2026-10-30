#!/usr/bin/env python3
"""YouTube through the box's proxy: the daily canary (C1, 30 Oct 2026).

The box reaches YouTube only through the owner's IPRoyal ISP proxy
(``YTDLP_PROXY`` in /etc/valmera/executor.env, delivered by box-provision;
one static US IP, whitelisted for this box, 100 GB of traffic per 30 days).
YouTube can wall a single static IP at any time, and a walled route is
silent: customers' YouTube imports would fail one by one. This canary looks:

* it runs the production probe (worker ``ytaccess.probe``: the exact yt-dlp
  flag set with cookies, PO tokens and the proxy; one extraction plus a small
  real byte range from the media CDN, deleted at once) for videos the box's
  own IP was walled on (E1, 29 Oct), inside an idle live slot with that
  slot's real environment, at nice 19. A few MB of proxy traffic a run;
* it writes app_kv ``ytdlp_probe_box`` (the verdict; stage_gate.py's YouTube
  gate wants it passing and under 24 h old) and app_kv ``youtube_canary``
  (ok, consecutive failures, the last runs, and the YouTube bytes the product
  fetched per day: an estimate from ``assets``; the vendor's dashboard is the
  authoritative meter).

The P0 watch on the owner's Mac (ops-loop/yt_watch.py) starts the Fixer,
which emails the owner, once ``consecutive_failures`` reaches 2.

The timer looks every hour; a probe runs only when the last pass is
``every_h`` (23) hours old, or the last run failed and ``retry_min`` (50)
minutes passed. So a healthy route costs one probe a day, and two failures
in a row means about an hour of a broken route, not two days. Never during a
planned window (host/maint.sh).

    yt_canary.py            probe when due (valmera-ytcanary.timer)
    yt_canary.py --force    probe now
    yt_canary.py --print    print the last state; write nothing

Config (box.json, optional): ``"yt_canary": {"urls": [...], "every_h": 23,
"retry_min": 50}``. Never prints the proxy's credentials (the box's proxy is
IP-whitelisted and has none; a URL with credentials is shown host:port).
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import health_report as hr  # noqa: E402  (admission state, DB writer)

STATE_PATH = os.path.join(hr.DATA_DIR, "yt-canary.json")
KEY_CANARY = "youtube_canary"
KEY_PROBE = "ytdlp_probe_box"      # worker ytaccess.provider_youtube_ok("box")
# Walled from the box's own IP on every client and IP family (E1, 29 Oct):
# a YouTube Short and a 2-hour podcast. The probe reads ~10 KB of media.
DEFAULT_URLS = ("https://www.youtube.com/shorts/myZ9kn9MIWQ",
                "https://www.youtube.com/watch?v=iKx3gAODybU")
LANES = ("mcp", "batch", "interactive")   # mcp first: fetch_url runs there
KEEP_RUNS = 30
KEEP_DAYS = 14

# Runs inside the slot: the production probe, one per URL.
SLOT_CODE = r"""
import json, sys, time
sys.path.insert(0, "/app")
import ytaccess
try:
    import version
    code = version.code_version()
except Exception:
    code = None
out = []
for url in sys.argv[1:]:
    t0 = time.time()
    try:
        v = ytaccess.probe(url=url, timeout_s=90)
    except Exception as e:
        v = {"url": url, "ok": False, "download_ok": False,
             "why": "probe crashed: " + str(e)[:160]}
    v["secs"] = round(time.time() - t0, 1)
    out.append(v)
print("C1CANARY " + json.dumps({"code": code, "probes": out}))
"""

BYTES_SQL = """
SELECT a.created_at::date AS day,
       COALESCE(a.meta->>'fetch_provider', 'worker') AS provider,
       COUNT(*) AS n, COALESCE(SUM(a.bytes), 0) AS bytes
  FROM assets a
 WHERE a.created_at >= NOW() - INTERVAL '%s days'
   AND a.meta->>'source_url' ~* '(youtube\\.com|youtu\\.be)'
 GROUP BY 1, 2
"""


def now_utc():
    return datetime.now(timezone.utc)


def proxy_host(env_path=None):
    """host:port of YTDLP_PROXY in the executor env, or None (never creds)."""
    try:
        with open(env_path or hr.ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("YTDLP_PROXY="):
                    value = line.split("=", 1)[1].strip()
                    if not value:
                        return None
                    parts = urlsplit(value if "://" in value
                                     else "http://" + value)
                    return f"{parts.hostname}:{parts.port or ''}".rstrip(":")
    except OSError:
        pass
    return None


def due(state, now, cfg, force=False):
    """(bool, reason). Pure."""
    if force:
        return True, "forced"
    every = timedelta(hours=float(cfg.get("every_h", 23)))
    retry = timedelta(minutes=float(cfg.get("retry_min", 50)))
    runs = (state or {}).get("runs") or []
    if not runs:
        return True, "first run"
    last = runs[-1]
    last_at = datetime.fromisoformat(last["at"])
    if last.get("skipped"):
        return now - last_at >= retry, "retry after a skipped run"
    if not last.get("ok"):
        return now - last_at >= retry, "retry after a failure"
    last_ok = datetime.fromisoformat(state.get("last_ok_at") or last["at"])
    return now - last_ok >= every, "daily"


def pick_slot(state, has_proxy=lambda name: True):
    """An idle, ready slot (container name) to probe from, or None. Pure
    apart from has_proxy (does that container's env carry YTDLP_PROXY: a
    slot not yet rolled onto a new env would test the old route)."""
    slots = (state or {}).get("slot_detail") or []
    for lane in LANES:
        cands = [s for s in slots
                 if s.get("lane") == lane and s.get("ready")
                 and not s.get("busy") and not s.get("retiring")
                 and not s.get("quick")
                 and (s.get("status") in (None, "ok"))]
        # The highest-numbered slot of the lane: the last one admission
        # fills, so the probe almost never shares a CPU with a real call.
        cands.sort(key=lambda s: (len(s["name"]), s["name"]), reverse=True)
        for s in cands:
            name = "vbox-" + s["name"]
            if has_proxy(name):
                return name
    return None


def container_has_proxy(name):
    proc = subprocess.run(
        ["docker", "inspect", "-f",
         "{{range .Config.Env}}{{println .}}{{end}}", name],
        capture_output=True, text=True, timeout=30)
    return any(line.split("=", 1)[0] == "YTDLP_PROXY" and
               line.split("=", 1)[1].strip()
               for line in proc.stdout.splitlines() if "=" in line)


def slots_missing_proxy(names=None, has_proxy=None):
    """Live slot containers whose env lacks YTDLP_PROXY. A slot created
    before box-provision delivered the proxy keeps the old env until the
    roll replaces it, and a YouTube fetch landing there is walled (30 Oct:
    an R1 fetch child on the not-yet-rolled bquick1). Expected only while a
    roll is in progress; the cutover gate wants it empty."""
    if names is None:
        proc = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                              capture_output=True, text=True, timeout=30)
        names = [n for n in proc.stdout.split() if n.startswith("vbox-")]
    has_proxy = has_proxy or container_has_proxy
    return sorted(n for n in names if not has_proxy(n))


def run_probes(container, urls, timeout_s=900):
    """The slot's verdicts: {"code", "probes": [...]}; raises on no answer."""
    proc = subprocess.run(
        ["docker", "exec", container, "nice", "-n", "19", "python", "-c",
         SLOT_CODE, *urls],
        capture_output=True, text=True, timeout=timeout_s)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("C1CANARY "):
            return json.loads(line[len("C1CANARY "):])
    raise RuntimeError("no probe answer from %s (exit %s): %s" % (
        container, proc.returncode, (proc.stderr or "")[-200:]))


def judge(answer):
    """(ok, why, best verdict). The route passes when any probe got real
    media bytes; a single removed video must not page the owner."""
    probes = (answer or {}).get("probes") or []
    for v in probes:
        if v.get("ok") and v.get("download_ok"):
            return True, "", v
    whys = ["%s: %s" % (str(v.get("url", "?")).rsplit("/", 1)[-1],
                        str(v.get("download_why") or v.get("why") or "?")[:140])
            for v in probes]
    return False, "; ".join(whys) or "no probe ran", (probes[0] if probes
                                                      else {})


def record(state, run, now):
    """The next state document (pure): runs, streaks, alert."""
    state = dict(state or {})
    runs = list(state.get("runs") or [])
    runs.append(run)
    state["runs"] = runs[-KEEP_RUNS:]
    state["at"] = now.isoformat()
    if run.get("skipped"):
        return state
    if run["ok"]:
        state["consecutive_failures"] = 0
        state["last_ok_at"] = run["at"]
    else:
        state["consecutive_failures"] = int(
            state.get("consecutive_failures") or 0) + 1
        state["last_fail_at"] = run["at"]
        state["last_why"] = run.get("why")
    state["ok"] = bool(run["ok"])
    state["alert"] = state.get("consecutive_failures", 0) >= 2
    return state


def bytes_by_day(rows, days=KEEP_DAYS):
    """{day: {"assets", "bytes", "proxy_bytes_est", "by_provider"}}. Pure.

    proxy_bytes_est counts what the box and in-process fetches pulled
    (fetch_provider box / worker): customers' fetches leave through the
    box's proxy once stage mcp is on; Cloudflare-fetched ones never did."""
    out = {}
    for r in rows:
        day = str(r["day"])
        d = out.setdefault(day, {"assets": 0, "bytes": 0,
                                 "proxy_bytes_est": 0, "by_provider": {}})
        n, b = int(r["n"]), int(r["bytes"])
        d["assets"] += n
        d["bytes"] += b
        d["by_provider"][r["provider"]] = b
        if r["provider"] in ("box", "worker"):
            d["proxy_bytes_est"] += b
    keep = sorted(out)[-days:]
    return {k: out[k] for k in keep}


def read_bytes(days=KEEP_DAYS):
    import psycopg2
    import psycopg2.extras
    url = hr.database_url()
    if not url:
        return {}
    conn = psycopg2.connect(url, connect_timeout=10)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SET TRANSACTION READ ONLY")
            c.execute("SET LOCAL statement_timeout = 20000")
            c.execute(BYTES_SQL % int(days))
            rows = c.fetchall()
        conn.rollback()
    finally:
        conn.close()
    return bytes_by_day(rows, days)


def keys_for(cfg):
    """(canary key, probe key): the bare names on the first box, suffixed
    ':<name>' on any other (the plan's "name" or E2's "box_id")."""
    name = str(cfg.get("name") or cfg.get("box_id") or "box1")
    if name == "box1":
        return KEY_CANARY, KEY_PROBE
    return f"{KEY_CANARY}:{name}", f"{KEY_PROBE}:{name}"


def load_state():
    return hr.load_json(STATE_PATH, {})


def save_state(state):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def probe_doc(verdict, answer, proxy, now):
    """app_kv ytdlp_probe_box, in run_probe_job's shape (provider, code)."""
    doc = dict(verdict or {})
    doc.update(provider="box", code=(answer or {}).get("code"),
               source="yt_canary", proxy=proxy, checked_at=now.isoformat())
    doc.setdefault("ok", False)
    doc.setdefault("download_ok", False)
    return doc


def main(argv):
    cfg_all = hr.load_json(hr.CONFIG_PATH, {})
    cfg = cfg_all.get("yt_canary") or {}
    state = load_state()
    if "--print" in argv:
        print(json.dumps(state, indent=1, sort_keys=True))
        return 0
    now = now_utc()
    go, reason = due(state, now, cfg, force="--force" in argv)
    if not go:
        print(f"yt-canary: not due ({reason}); last ok "
              f"{state.get('last_ok_at')}")
        return 0
    if hr.maintenance_until():
        print("yt-canary: planned maintenance window; not probing")
        return 0
    proxy = proxy_host()
    urls = list(cfg.get("urls") or DEFAULT_URLS)
    run = {"at": now.isoformat(), "reason": reason, "proxy": proxy}
    answer, verdict = None, {}
    if not proxy:
        run.update(ok=False, why="YTDLP_PROXY is not provisioned on the box "
                   "(GitHub secret + box-provision)")
    else:
        slot = pick_slot(hr.admission_state(cfg_all), container_has_proxy)
        if not slot:
            run.update(skipped=True, ok=None,
                       why="no idle slot with YTDLP_PROXY (admission down, "
                           "busy, or a roll in progress)")
        else:
            run["slot"] = slot
            t0 = time.time()
            try:
                answer = run_probes(slot, urls)
                ok, why, verdict = judge(answer)
                run.update(ok=ok, why=why, strategy=verdict.get("strategy"))
            except Exception as exc:  # noqa: BLE001 - recorded as a failure
                run.update(ok=False, why=f"probe did not run: "
                           f"{type(exc).__name__}: {str(exc)[:160]}")
            run["secs"] = round(time.time() - t0, 1)
    state = record(state, run, now)
    state["proxy"] = proxy
    if proxy:
        try:
            state["slots_missing_proxy"] = slots_missing_proxy()
        except Exception as exc:  # noqa: BLE001 - informational
            state["slots_missing_proxy"] = [f"check failed: {exc}"[:120]]
    state["urls"] = urls
    try:
        state["bytes_by_day"] = read_bytes()
    except Exception as exc:  # noqa: BLE001 - the probe result matters more
        state["bytes_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    save_state(state)
    status = 0
    key_canary, key_probe = keys_for(cfg_all)
    try:
        hr.write(key_canary, state)
        if not run.get("skipped"):
            hr.write(key_probe, probe_doc(verdict, answer, proxy, now))
    except Exception as exc:  # noqa: BLE001 - next hourly run retries
        print(f"yt-canary: app_kv not written ({type(exc).__name__}: "
              f"{str(exc)[:160]})", file=sys.stderr)
        status = 1
    label = ("skipped" if run.get("skipped") else
             "ok" if run["ok"] else "FAILED")
    print(f"yt-canary: {label} via {proxy} on {run.get('slot')} "
          f"({run.get('secs')} s); consecutive failures "
          f"{state.get('consecutive_failures', 0)}"
          + (f"; {run.get('why')}" if run.get("why") else ""))
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
