#!/usr/bin/env python3
"""Box health for the owner: the app_kv row ``box_health``, every minute.

The owner's daily digest and the admin's Today panel read app_kv
``box_health`` (or ``box_health:<name>`` once there are several boxes) and
call it stale after 10 minutes (backend/ops_loop/views.box_health). This
writer runs on the box itself (valmera-health.timer): the box already holds
DATABASE_URL in /etc/valmera/executor.env (box/provision.py), so no
dispatcher hop is needed. One JSON document::

    {"name", "version", "commit", "status", "load1", "cpus", "steal_pct",
     "steal_pct_15m", "busy_pct", "mem_used_pct", "mem_available_gib",
     "disk_used_pct", "disk_free_gb", "running_jobs", "queued",
     "oldest_wait_s", "slots_ready", "slots_total", "reboot_required",
     "maintenance_until", "updated_at"}

``status`` is admission's own (ok / degraded / draining) or
``admission_down`` when its local state cannot be read: the host is up and
reporting, but nothing can be admitted, which the P0 watch treats like a
down box.

Disk guard: when free space falls under box.json ``disk_low_water_gib`` the
writer also starts the disk-hygiene service at once (at most every 5
minutes), so the guard reacts within a minute instead of the next 15-minute
clean. Admission itself refuses new calls under ``disk_min_free_gib``
(nothing runs, the dispatcher waits); this keeps the box away from that
line.

    health_report.py           write the row (the timer)
    health_report.py --print   print the document, write nothing
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

CONFIG_PATH = os.getenv("BOX_CONFIG", "/etc/valmera/box.json")
ENV_PATH = os.getenv("BOX_ENV_FILE", "/etc/valmera/executor.env")
ADMIN_TOKEN_PATH = os.getenv("BOX_ADMIN_TOKEN_FILE",
                             "/etc/valmera/admin.token")
DATA_DIR = os.getenv("BOX_DATA_DIR", "/var/lib/valmera")
CLEAN_STAMP = os.path.join(DATA_DIR, "health-clean-started")
MAINT_UNTIL = os.path.join(DATA_DIR, "maint-until")      # host/maint.sh
REBOOT_FLAG = "/var/run/reboot-required"


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def admission_state(cfg, timeout=8):
    port = int(cfg.get("listen_port", 8090))
    try:
        with open(ADMIN_TOKEN_PATH, encoding="utf-8") as fh:
            token = fh.read().strip()
    except OSError:
        return None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/admin/state",
        headers={"X-Admin-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - any failure means "admission down"
        return None


def meminfo(path="/proc/meminfo"):
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                key, _, rest = line.partition(":")
                out[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def maintenance_until(now_s=None):
    """Epoch seconds a planned maintenance window ends, or None."""
    try:
        with open(MAINT_UNTIL, encoding="utf-8") as fh:
            until = int(fh.read().strip())
    except (OSError, ValueError):
        return None
    return until if until > (time.time() if now_s is None else now_s) \
        else None


def build(cfg, state, mem=None, disk=None, load=None, cpus=None,
          reboot=None, now=None, maint=None):
    """The document (pure; every input injectable for tests)."""
    mem = meminfo() if mem is None else mem
    disk = shutil.disk_usage(DATA_DIR) if disk is None else disk
    load = os.getloadavg() if load is None else load
    cpus = os.cpu_count() if cpus is None else cpus
    reboot = os.path.exists(REBOOT_FLAG) if reboot is None else reboot
    now = now or datetime.now(timezone.utc)
    total, avail = mem.get("MemTotal"), mem.get("MemAvailable")
    gb = 1024 ** 3
    doc = {
        "name": str(cfg.get("name") or "box1"),
        "load1": round(float(load[0]), 2),
        "cpus": int(cpus or 0),
        "mem_used_pct": (round(100.0 * (total - avail) / total, 1)
                         if total and avail is not None else None),
        "mem_available_gib": (round(avail / gb, 2)
                              if avail is not None else None),
        "disk_used_pct": round(100.0 * disk.used / disk.total, 1)
        if disk.total else None,
        "disk_free_gb": round(disk.free / gb, 1),
        "reboot_required": bool(reboot),
        "maintenance_until": (datetime.fromtimestamp(
            maint, timezone.utc).isoformat() if maint else None),
        "updated_at": now.isoformat(),
    }
    if not state:
        doc.update(status="admission_down", version=None, commit=None,
                   running_jobs=None, queued=None, oldest_wait_s=None,
                   slots_ready=None, slots_total=None, steal_pct=None,
                   steal_pct_15m=None, busy_pct=None)
        return doc
    slots = state.get("slots") or {}
    lanes = (slots.get("by_lane") or {}).values()
    queue = state.get("queue") or {}
    cpu = state.get("cpu") or {}
    doc.update(
        status=str(state.get("status") or "unknown"),
        version=state.get("code_version"),
        commit=(state.get("commit") or "")[:12] or None,
        running_jobs=int(slots.get("busy") or 0),
        queued=int(queue.get("depth") or 0),
        oldest_wait_s=queue.get("oldest_wait_s"),
        slots_ready=sum(int(v.get("ready") or 0) for v in lanes),
        slots_total=int(slots.get("total") or 0),
        steal_pct=(cpu.get("last_60s") or {}).get("steal_pct"),
        steal_pct_15m=(cpu.get("last_15m") or {}).get("steal_pct"),
        busy_pct=(cpu.get("last_60s") or {}).get("busy_pct"))
    return doc


def key_for(cfg):
    """'box_health' for the first box; 'box_health:<name>' for any other
    (box.json "health_key" overrides)."""
    explicit = cfg.get("health_key")
    if explicit:
        return str(explicit)
    name = str(cfg.get("name") or "box1")
    return "box_health" if name == "box1" else f"box_health:{name}"


def database_url():
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def write(key, doc):
    import psycopg2  # python3-psycopg2 (box/host/install.sh)
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not provisioned yet")
    conn = psycopg2.connect(url, connect_timeout=10,
                            options="-c statement_timeout=10000")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO app_kv (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()""",
                (key, json.dumps(doc, sort_keys=True)))
        conn.commit()
    finally:
        conn.close()


def disk_guard(cfg, doc, now_s=None, start=None):
    """Start the hygiene service now when free space is under low water."""
    low = float(cfg.get("disk_low_water_gib", 40))
    if doc.get("disk_free_gb") is None or doc["disk_free_gb"] >= low:
        return False
    now_s = time.time() if now_s is None else now_s
    try:
        if now_s - os.path.getmtime(CLEAN_STAMP) < 300:
            return False
    except OSError:
        pass
    (start or (lambda: subprocess.run(
        ["systemctl", "start", "--no-block", "valmera-clean.service"],
        check=False, capture_output=True)))()
    try:
        with open(CLEAN_STAMP, "w", encoding="utf-8") as fh:
            fh.write(str(now_s))
    except OSError:
        pass
    return True


def main(argv):
    cfg = load_json(CONFIG_PATH, {})
    doc = build(cfg, admission_state(cfg), maint=maintenance_until())
    if "--print" in argv:
        print(json.dumps(doc, indent=1, sort_keys=True))
        return 0
    guarded = disk_guard(cfg, doc)
    try:
        write(key_for(cfg), doc)
    except Exception as exc:  # noqa: BLE001 - logged, retried next minute
        print(f"health: not written ({type(exc).__name__}: "
              f"{str(exc)[:160]})", file=sys.stderr)
        return 1
    print(f"health: {doc['status']} load {doc['load1']}/{doc['cpus']} "
          f"mem {doc['mem_used_pct']}% disk {doc['disk_used_pct']}% "
          f"running {doc['running_jobs']} queued {doc['queued']}"
          + (" (disk guard started a clean)" if guarded else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
