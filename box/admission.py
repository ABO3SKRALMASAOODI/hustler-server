#!/usr/bin/env python3
"""Valmera render box: admission layer (the Durable Object contract, one host).

The dispatcher (worker/remote.py, ``box`` routing) speaks to this service
exactly as it speaks to the Cloudflare Worker (worker/cloudflare/src/index.ts):

    POST /calls/<lane>/<call_id>            launch one named call
    GET  /calls/<lane>/<call_id>            its durable state
    POST /calls/<lane>/<call_id>/complete   dispatcher-proven success
    POST /calls/<lane>/<call_id>/abandon    dispatcher-proven death
    POST /calls/<lane>/<call_id>/fence      tombstone a launch never seen
    GET  /health                            provider, code_version, capacity
    GET  /probes/<lane>                     one slot container's own /health

Behind it run executor containers built from the SAME Dockerfiles as the
Cloudflare lanes, one call per container slot (Cloudflare's one call per
Durable Object shard). What this layer adds for the box:

* a priority queue: customers first, trial next, the owner factory last and
  capped per lane (``owner_max_by_lane``) and in CPUs (``owner_max_cpus``);
  synchronous children (id-less frames, captures, ...) of an admitted parent
  rank above top-level work of their class and are exempt from the owner cap,
  so a parent can never deadlock on its own child;
* CPU priority under contention: each call sets its slot's cgroup CPU weight
  from its class (customers 4x the owner factory), on top of the slot's hard
  ``--cpus``/``--memory`` limits;
* memory-budgeted admission: the measured p95 of every running call's type
  plus the newcomer's must fit ``mem_budget_gib`` and MemAvailable must keep
  ``mem_reserve_gib``;
* strict release matching: a call runs only on a slot whose code fingerprint
  equals the dispatcher's (``dispatcher_source``), so a rolling update never
  runs a job on other code;
* when no slot frees up within the call's bounded wait, a 429 that proves
  nothing ran (``box_wait``). The dispatcher NEVER falls back to another
  provider: it holds the job in its claim and the durable Postgres queue and
  asks again;
* /health with free slots, queue depth, disk, memory and CPU steal over the
  last 60 s and 15 min (from /proc/stat);
* per-IP rate limiting and the shared EXECUTOR_SECRET bearer on every call.

State is durable (SQLite) so a restart of this process never forgets an
accepted call: a call whose /run handler was lost becomes ``unknown`` and
keeps its slot fenced until completion, a container exit, the dispatcher's
abandonment or its lease, exactly like a Durable Object after a Worker
deploy. Python standard library only.
"""

import collections
import hashlib
import hmac
import http.client
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

CONFIG_PATH = os.getenv("BOX_CONFIG", "/etc/valmera/box.json")
STATE_PATH = os.getenv("BOX_STATE_DB", "/var/lib/valmera/admission.sqlite")
RELEASE_PATH = os.getenv("BOX_RELEASE", "/var/lib/valmera/release.json")
STEAL_LOG = os.getenv("BOX_STEAL_LOG", "/var/log/valmera/steal.log")
DATA_DIR = os.getenv("BOX_DATA_DIR", "/var/lib/valmera")
ENV_PATH = os.getenv("BOX_ENV_FILE", "/etc/valmera/executor.env")
ADMIN_TOKEN_PATH = os.getenv("BOX_ADMIN_TOKEN_FILE", "/etc/valmera/admin.token")

# ── The Worker's contract, mirrored ─────────────────────────────────────────
INTERACTIVE_TYPES = {"preview", "preview_check", "filmstrip", "frames",
                     "mcp_media"}
BATCH_TYPES = {"index", "final", "capture", "track", "matte", "smatch",
               "clean", "stems", "fetch", "search", "stock_acquire",
               "ytprobe", "faces", "sidecar", "frames", "mcp_media",
               "preview", "preview_check", "filmstrip"}
LANE_TYPES = {"interactive": INTERACTIVE_TYPES, "batch": BATCH_TYPES,
              "agent": {"agent_turn"}, "mcp": {"mcp_tool"},
              "shorts": {"shorts_plan"}, "vision": {"sidecar"}}
SYNCHRONOUS_TYPES = {"capture", "frames", "track", "matte", "smatch",
                     "clean", "stems", "fetch", "search", "stock_acquire",
                     "ytprobe", "mcp_media", "faces", "sidecar"}
LANE_ROLES = {"interactive": "executor", "batch": "executor",
              "agent": "agent_executor", "mcp": "mcp_executor",
              "shorts": "shorts_executor", "vision": "executor"}
MUTATING_JOB_TYPES = {"mcp_tool", "agent_turn"}
CALL_ID = re.compile(r"^[a-zA-Z0-9_-]{8,96}$")
LAUNCH_ID = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
FEATURES = ["custom_filter", "stems", "skew_guard", "fence", "alt_slots_32",
            "box_admission"]
TERMINAL_RETENTION_S = 7 * 24 * 3600
SUBMITTED_STALE_S = 60.0
STARTING_STALE_S = 180.0
ABANDON_MIN_DISCONNECTED_S = 120.0
LIVE = ("submitted", "starting", "running", "unknown", "stopping")
OCCUPYING = ("starting", "running", "unknown", "stopping")
CLASS_RANK = {"customer": 0, "trial": 1, "owner": 2}


def now():
    return time.time()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def log(msg):
    print(f"[admission] {msg}", flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


# ── Configuration ───────────────────────────────────────────────────────────
# Measured p95 peak memory per job type (GiB), production 22-29 Oct 2026
# (video_jobs.result.timings.vm_memory_sampled_peak_mib on Cloudflare):
# preview 2.8, final 6.2, preview_check 1.1, index 1.6, mcp_tool 0.74,
# filmstrip 0.5, shorts_plan 0.5, agent_turn 0.55. Rounded up; types never
# measured get a conservative 2 GiB. box.json may override any of them.
MEM_ESTIMATE_GIB = {
    "preview": 3.0, "preview_check": 1.2, "final": 6.5, "index": 2.0,
    "filmstrip": 0.6, "mcp_tool": 0.8, "agent_turn": 0.6,
    "shorts_plan": 0.6, "frames": 0.8, "mcp_media": 1.0, "capture": 1.5,
    "ytprobe": 0.3, "search": 0.5, "fetch": 1.0, "stock_acquire": 1.0,
}
DEFAULT_MEM_ESTIMATE_GIB = 2.0
# cgroup CPU weight per class (docker --cpu-shares): under contention a
# customer's render gets 4x the owner factory's CPU time.
CPU_SHARES = {"customer": 4096, "trial": 2048, "owner": 1024}


class Config:
    def __init__(self, raw):
        self.raw = raw
        self.host = raw.get("listen_host", "127.0.0.1")
        self.port = int(raw.get("listen_port", 8090))
        # Extra listen addresses (the containers' bridge gateway), so slot
        # containers reach admission for their own synchronous children.
        self.extra_hosts = [str(h) for h in raw.get("extra_listen_hosts", [])]
        self.owner_user_ids = {int(u) for u in raw.get("owner_user_ids", [60])}
        self.trial_user_ids = {int(u) for u in raw.get("trial_user_ids", [])}
        self.owner_max_slots = int(raw.get("owner_max_slots", 12))
        # Off by default: slot quotas oversubscribe the box, so summing them
        # blocked light MCP calls behind renders. Per-lane caps and the
        # per-call CPU weight bound the owner factory instead.
        self.owner_max_cpus = float(raw.get("owner_max_cpus", 99))
        self.owner_max_by_lane = {str(k): int(v) for k, v in (
            raw.get("owner_max_by_lane") or {}).items()}
        self.queue_max = int(raw.get("queue_max", 256))
        # Below the dispatcher's 180-s launch observation so a refusal comes
        # back on the launch request itself; the dispatcher asks again at
        # once, so a waiting call keeps its place within a second.
        self.wait_s = {"customer": 150.0, "trial": 150.0, "owner": 150.0}
        self.wait_s.update({k: float(v) for k, v in
                            (raw.get("wait_s") or {}).items()})
        self.max_wait_s = float(raw.get("max_wait_s", 170))
        # Sums of per-type p95s overstate concurrent use (the 29 Oct load
        # test: 23 GiB "committed" while 14 GiB stayed available), so the
        # budget oversubscribes and MemAvailable keeps the real reserve.
        self.mem_reserve_gib = float(raw.get("mem_reserve_gib", 3.0))
        self.mem_budget_gib = float(raw.get("mem_budget_gib", 28.0))
        self.mem_estimate = dict(MEM_ESTIMATE_GIB)
        self.mem_estimate.update({str(k): float(v) for k, v in (
            raw.get("mem_estimate_gib") or {}).items()})
        self.cpu_shares = dict(CPU_SHARES)
        self.cpu_shares.update({str(k): int(v) for k, v in (
            raw.get("cpu_shares") or {}).items()})
        self.strict_source = bool(raw.get("strict_source", True))
        self.disk_min_free_gib = float(raw.get("disk_min_free_gib", 20))
        self.rate_per_s = float(raw.get("rate_per_s", 40))
        self.rate_burst = float(raw.get("rate_burst", 120))
        self.slots = [dict(s) for s in raw.get("slots", [])]

    def estimate(self, job_type):
        return self.mem_estimate.get(job_type, DEFAULT_MEM_ESTIMATE_GIB)


# ── CPU steal (/proc/stat) ─────────────────────────────────────────────────
class CpuSampler:
    """Aggregate steal and busy percentages over sliding windows."""

    def __init__(self, path="/proc/stat"):
        self.path = path
        self.samples = collections.deque(maxlen=400)   # 5-s samples, ~33 min
        self.lock = threading.Lock()

    def read(self):
        with open(self.path, encoding="ascii") as fh:
            parts = fh.readline().split()
        vals = [int(v) for v in parts[1:]]
        while len(vals) < 8:
            vals.append(0)
        user, nice, system, idle, iowait, irq, softirq, steal = vals[:8]
        total = user + nice + system + idle + iowait + irq + softirq + steal
        busy = total - idle - iowait
        return total, busy, steal

    def sample(self):
        try:
            total, busy, steal = self.read()
        except (OSError, ValueError):
            return
        with self.lock:
            self.samples.append((time.monotonic(), total, busy, steal))

    def window(self, seconds):
        with self.lock:
            if len(self.samples) < 2:
                return None
            last = self.samples[-1]
            first = None
            for item in self.samples:
                if last[0] - item[0] <= seconds + 2.5:
                    first = item
                    break
            if first is None or first is last:
                first = self.samples[-2]
        d_total = last[1] - first[1]
        if d_total <= 0:
            return None
        return {"steal_pct": round(100.0 * (last[3] - first[3]) / d_total, 2),
                "busy_pct": round(100.0 * (last[2] - first[2]) / d_total, 2),
                "span_s": round(last[0] - first[0], 1)}


def mem_available_gib():
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / (1024 ** 2)
    except (OSError, ValueError):
        pass
    return None


def disk_report(path=None):
    try:
        usage = shutil.disk_usage(path or DATA_DIR)
    except OSError:
        return {"free_gb": None, "total_gb": None}
    gb = 1024 ** 3
    return {"free_gb": round(usage.free / gb, 1),
            "total_gb": round(usage.total / gb, 1)}


# ── Call identity (worker/cloudflare/src/index.ts matchesCompletedJob) ──────
def matches_job(call_id, job):
    if not isinstance(job, dict):
        return False
    jid, claims, project = (job.get("id"), job.get("total_claims"),
                            job.get("project_id"))
    jtype = str(job.get("type") or "")
    if not all(isinstance(v, int) and not isinstance(v, bool)
               for v in (jid, claims, project)):
        return False
    digest = hashlib.sha256(f"{jtype}:{jid}:{claims}".encode()).hexdigest()[:20]
    if jtype == "mcp_tool":
        prefix = f"mcp-p{project}"
    elif jtype in {"preview", "preview_check"}:
        prefix = f"preview-p{project}"
    else:
        prefix = jtype[:18]
    if call_id in {f"bx-{prefix}-{digest}", f"bx-{jtype[:18]}-{digest}"}:
        return True
    if jtype in {"final", "preview", "preview_check"} and re.fullmatch(
            rf"bx-render-g[0-9]+-[01]-{digest}", call_id):
        return True
    return any(call_id == f"bx-alt{slot}-{jtype}-p{project}-{digest}"
               for slot in range(1, 33))


def affinity_key(call_id):
    for pattern in (r"^bx-mcp-p([0-9]+)-", r"^bx-preview-p([0-9]+)-"):
        m = re.match(pattern, call_id)
        if m:
            return f"project:{m.group(1)}"
    m = re.match(r"^bx-render-g([0-9]+-[01])-", call_id)
    return f"group:{m.group(1)}" if m else None


def retry_attempts(job_type):
    return 1 if job_type in {"mcp_tool", "agent_turn"} else 2


def abandoned_envelope(job_type, read_only, reason):
    if job_type in MUTATING_JOB_TYPES and not read_only:
        error = (f"Box lost the executor during this {job_type} ({reason}). "
                 "Its outcome is unknown: it may or may not have changed the "
                 "project. Re-read the current project state before retrying.")
        return {"error": error, "retryable": False, "failure": {
            "kind": "outcome_unknown", "retryable": False, "max_attempts": 0,
            "agent_repairable": False}}
    error = (f"Box lost the executor during this {job_type} ({reason}). "
             "No result was recorded; it is safe to retry.")
    return {"error": error, "retryable": True, "failure": {
        "kind": "transient_infrastructure", "retryable": True,
        "max_attempts": retry_attempts(job_type), "agent_repairable": False}}


def refusal_envelope(job_type, message, capacity=True, rollout=False):
    """A proof that nothing ran. ``box_wait`` tells the dispatcher to hold
    the job and ask again; ``safe_to_fallback`` keeps the shared pre-/run
    classification of the Cloudflare contract (the box path never falls
    back to another provider)."""
    return {"error": message, "safe_to_fallback": True, "retryable": True,
            "box_wait": True,
            **({"rollout_pending": True} if rollout else {}),
            **({"capacity_unavailable": True} if capacity else {}),
            "failure": {"kind": ("provider_capacity_unavailable" if capacity
                                 else "provider_start_abandoned"),
                        "retryable": True,
                        "max_attempts": retry_attempts(job_type),
                        "agent_repairable": False}}


# ── Slots ───────────────────────────────────────────────────────────────────
class Slot:
    def __init__(self, spec):
        self.name = spec["name"]
        self.lane = spec["lane"]
        self.port = int(spec["port"])
        self.cpus = float(spec.get("cpus", 1))
        # A quick slot serves only synchronous children (a frame look, a
        # capture): an agent's look_at never queues behind 3-minute renders.
        self.quick = bool(spec.get("quick", False))
        self.shares = None        # the cgroup CPU weight last applied
        self.container = spec.get("container") or f"vbox-{self.name}"
        self.role = LANE_ROLES.get(self.lane, "executor")
        self.ready = False
        self.release = "unknown"
        self.health_status = "unknown"
        self.started_at = ""      # docker State.StartedAt of the container
        self.running = False
        self.retiring = False     # set by the updater while it swaps
        self.last_affinity = None
        self.last_used = 0.0
        self.last_error = ""
        self.checked_at = 0.0

    def report(self, active):
        return {"name": self.name, "lane": self.lane, "ready": self.ready,
                "release": self.release, "status": self.health_status,
                "retiring": self.retiring, "busy": active is not None,
                "call": active, "cpus": self.cpus, "quick": self.quick,
                "container_started_at": self.started_at,
                "error": self.last_error[:200] or None}


def docker_state(container):
    try:
        out = subprocess.run(
            ["docker", "inspect", "-f",
             "{{.State.Running}}|{{.State.StartedAt}}", container],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None, ""
    if out.returncode != 0:
        return False, ""
    running, _, started = out.stdout.strip().partition("|")
    return running == "true", started


def docker_restart(container):
    """Physically fence a slot: whatever ran inside is gone afterwards."""
    try:
        out = subprocess.run(["docker", "restart", "-t", "5", container],
                             capture_output=True, text=True, timeout=120)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def docker_cpu_shares(container, shares):
    """Set a running slot's cgroup CPU weight; False when docker refused."""
    try:
        out = subprocess.run(["docker", "update", "--cpu-shares",
                              str(int(shares)), container],
                             capture_output=True, text=True, timeout=15)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def container_health(port, timeout=3.0):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = json.loads(resp.read() or b"{}")
        return resp.status, body
    finally:
        conn.close()


# ── The service ─────────────────────────────────────────────────────────────
class Box:
    def __init__(self, cfg, secret, admin_token):
        self.cfg = cfg
        self.secret = secret
        self.admin_token = admin_token
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.slots = [Slot(s) for s in cfg.slots]
        self.by_name = {s.name: s for s in self.slots}
        self.lanes = sorted({s.lane for s in self.slots})
        self.waiting = {}          # call_id -> waiter dict
        self.live_handlers = set()  # call ids whose /run is in this process
        self.draining = False
        self.cpu = CpuSampler()
        self.db = sqlite3.connect(STATE_PATH, check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS calls (
            call_id TEXT PRIMARY KEY, lane TEXT NOT NULL,
            job_type TEXT NOT NULL, status TEXT NOT NULL, slot TEXT,
            job TEXT, launch_id TEXT, read_only INTEGER DEFAULT 0,
            priority TEXT, user_id INTEGER, envelope TEXT, error TEXT,
            created_at REAL, updated_at REAL, active_until REAL,
            started_at REAL, container_started TEXT, disconnected_at REAL,
            terminal_at REAL, abandon_reason TEXT)""")
        self.db.execute("CREATE INDEX IF NOT EXISTS calls_status "
                        "ON calls (status)")
        self.db.execute("CREATE INDEX IF NOT EXISTS calls_terminal "
                        "ON calls (terminal_at)")
        self.db.execute("""CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY, value TEXT)""")
        row = self.db.execute(
            "SELECT value FROM kv WHERE key='draining'").fetchone()
        self.draining = bool(row and row["value"] == "1")
        self._config_mtime = None
        self._recover_after_restart()

    # ── persistence helpers (call under self.lock) ──
    def get(self, call_id):
        row = self.db.execute("SELECT * FROM calls WHERE call_id=?",
                              (call_id,)).fetchone()
        return dict(row) if row else None

    def put(self, rec):
        rec["updated_at"] = now()
        cols = list(rec.keys())
        self.db.execute(
            f"INSERT OR REPLACE INTO calls ({','.join(cols)}) VALUES "
            f"({','.join('?' for _ in cols)})", [rec[c] for c in cols])

    def terminalize(self, rec, status, envelope, error=None):
        rec = dict(rec)
        rec.update({"status": status,
                    "envelope": json.dumps(envelope, default=str),
                    "error": error or (envelope or {}).get("error"),
                    "terminal_at": now(), "active_until": now()})
        self.put(rec)
        self.cond.notify_all()
        return rec

    def active_call(self, slot_name):
        row = self.db.execute(
            "SELECT call_id FROM calls WHERE slot=? AND status IN "
            "('starting','running','unknown','stopping') "
            "ORDER BY updated_at DESC LIMIT 1", (slot_name,)).fetchone()
        return row["call_id"] if row else None

    def _recover_after_restart(self):
        """Every handler is gone after a restart; fence what they owned."""
        t = now()
        with self.lock:
            for rec in [dict(r) for r in self.db.execute(
                    "SELECT * FROM calls WHERE status IN "
                    "('submitted','starting','running')")]:
                if rec["status"] in ("submitted", "starting"):
                    # No /run was sent by a handler that no longer exists.
                    self.terminalize(rec, "failed", refusal_envelope(
                        rec["job_type"], "Box admission restarted before this "
                        "call reached an executor; nothing ran",
                        capacity=False))
                else:
                    rec.update({"status": "unknown",
                                "disconnected_at": rec.get("disconnected_at")
                                or t,
                                "error": "the admission handler awaiting /run "
                                         "was lost (admission restart)"})
                    self.put(rec)
                    log(f"call {rec['call_id']} on {rec['slot']} is unknown "
                        "after restart; its slot stays fenced")

    # ── views ──
    def state_json(self, rec):
        if not rec:
            return {"status": "missing"}
        out = {"status": rec["status"], "jobType": rec["job_type"],
               "updatedAt": iso(rec["updated_at"] or now()),
               "activeUntil": int((rec["active_until"] or 0) * 1000)}
        if rec.get("envelope"):
            out["envelope"] = json.loads(rec["envelope"])
        if rec.get("error"):
            out["error"] = rec["error"]
        if rec.get("launch_id"):
            out["launchId"] = rec["launch_id"]
        if rec.get("job"):
            out["job"] = json.loads(rec["job"])
        if rec.get("disconnected_at"):
            out["disconnectedAt"] = int(rec["disconnected_at"] * 1000)
        if rec.get("read_only"):
            out["readOnly"] = True
        if rec.get("slot"):
            out["slot"] = rec["slot"]
        return out

    def release_info(self):
        return load_json(RELEASE_PATH, {})

    def health(self):
        rel = self.release_info()
        with self.lock:
            slots = []
            for s in self.slots:
                slots.append(s.report(self.active_call(s.name)))
            queue = collections.Counter(w["priority"]
                                        for w in self.waiting.values())
            queue_lanes = collections.Counter(w["lane"]
                                              for w in self.waiting.values())
            oldest = min((w["enqueued"] for w in self.waiting.values()),
                         default=None)
            occupying = self._occupying()
            owner_busy = self._owner_busy(occupying)
            committed = self._committed_gib(occupying)
        free = [s for s in slots if s["ready"] and not s["busy"]
                and not s["retiring"]]
        by_lane = {}
        for lane in self.lanes:
            lane_slots = [s for s in slots if s["lane"] == lane]
            by_lane[lane] = {
                "total": len(lane_slots),
                "ready": sum(1 for s in lane_slots if s["ready"]),
                "free": sum(1 for s in lane_slots if s in free)}
        ready_lanes = [lane for lane, v in by_lane.items() if v["ready"]]
        source = str(rel.get("source") or "unknown")
        mem = mem_available_gib()
        disk = disk_report()
        healthy = bool(ready_lanes) and not self.draining and (
            disk["free_gb"] is None
            or disk["free_gb"] >= self.cfg.disk_min_free_gib)
        status = "draining" if self.draining else (
            "ok" if healthy else "degraded")
        # Every release a ready slot runs. During a rolling update two
        # fingerprints coexist; strict matching sends each call to its own.
        releases = sorted({s["release"] for s in slots if s["ready"]
                           and s["release"] not in ("", "unknown")})
        return {
            "status": status, "provider": "box",
            "code_version": source, "source_version": source,
            "code_versions": releases,
            "strict_source": self.cfg.strict_source,
            "commit": rel.get("commit"), "release_at": rel.get("deployed_at"),
            "features": FEATURES, "lanes": ready_lanes,
            "draining": self.draining,
            "slots": {"total": len(slots), "free": len(free),
                      "busy": sum(1 for s in slots if s["busy"]),
                      "by_lane": by_lane},
            "owner": {"busy_slots": owner_busy[0],
                      "busy_cpus": owner_busy[1],
                      "by_lane": owner_busy[2],
                      "max_slots": self.cfg.owner_max_slots,
                      "max_cpus": self.cfg.owner_max_cpus,
                      "max_by_lane": self.cfg.owner_max_by_lane},
            "queue": {"depth": sum(queue.values()), "by_class": dict(queue),
                      "by_lane": dict(queue_lanes),
                      "oldest_wait_s": (round(now() - oldest, 1)
                                        if oldest else 0.0),
                      "max": self.cfg.queue_max},
            "disk": disk,
            "memory": {"available_gib": round(mem, 2) if mem else None,
                       "reserve_gib": self.cfg.mem_reserve_gib,
                       "committed_gib": round(committed, 2),
                       "budget_gib": self.cfg.mem_budget_gib},
            "cpu": {"last_60s": self.cpu.window(60),
                    "last_15m": self.cpu.window(900),
                    "load": os.getloadavg()},
            "reboot_required": os.path.exists("/var/run/reboot-required"),
            "slot_detail": slots,
        }

    def _occupying(self):
        """(slot, priority, job_type, child) of every call holding a slot."""
        rows = self.db.execute(
            "SELECT slot, priority, job_type, job FROM calls WHERE status IN "
            "('starting','running','unknown','stopping')").fetchall()
        out = []
        for r in rows:
            if r["slot"] not in self.by_name:
                continue
            try:
                child = (json.loads(r["job"] or "{}") or {}).get("id") is None
            except ValueError:
                child = False
            out.append((self.by_name[r["slot"]], r["priority"],
                        r["job_type"], child))
        return out

    def _owner_busy(self, occupying=None):
        """Owner factory TOP-LEVEL calls: (count, cpus, {lane: count}).

        Synchronous children are part of a parent that was already admitted
        under the cap; counting them could deadlock a parent on its child."""
        occupying = self._occupying() if occupying is None else occupying
        owner = [s for s, prio, _t, child in occupying
                 if prio == "owner" and not child]
        by_lane = collections.Counter(s.lane for s in owner)
        return len(owner), sum(s.cpus for s in owner), dict(by_lane)

    def _committed_gib(self, occupying=None):
        occupying = self._occupying() if occupying is None else occupying
        return sum(self.cfg.estimate(jtype) for _s, _p, jtype, _c in occupying)

    def _free_slots(self, lane):
        return [s for s in self.slots if s.lane == lane and s.ready
                and not s.retiring and self.active_call(s.name) is None]

    def lane_releases(self, lane):
        return {s.release for s in self.slots if s.lane == lane and s.ready}

    def _assign(self):
        """Give free slots to the best waiting calls; True if any moved.

        Order: class (customer, trial, owner), then synchronous children
        before top-level work of the same class, then arrival. A waiter that
        a slot could serve but the memory budget cannot is a reservation:
        nobody of a LOWER class starts while it waits, so the owner factory
        can never starve a customer by always fitting first."""
        moved = False
        if self.draining:
            return False
        order = sorted(self.waiting.values(),
                       key=lambda w: (CLASS_RANK.get(w["priority"], 0),
                                      0 if w.get("child") else 1,
                                      w["enqueued"]))
        occupying = self._occupying()
        committed = self._committed_gib(occupying)
        blocked_rank = None
        for waiter in order:
            if waiter.get("slot"):
                continue
            rank = CLASS_RANK.get(waiter["priority"], 0)
            if blocked_rank is not None and rank > blocked_rank:
                break
            free = self._free_slots(waiter["lane"])
            # Quick slots take only synchronous children; children prefer
            # them but may use any slot of their lane.
            free = [s for s in free if waiter.get("child") or not s.quick]
            want = waiter.get("source")
            if self.cfg.strict_source and want and want != "unknown":
                free = [s for s in free if s.release == want]
            if not free:
                continue
            if waiter["priority"] == "owner" and not waiter.get("child"):
                busy_n, busy_cpu, by_lane = self._owner_busy(occupying)
                lane_cap = self.cfg.owner_max_by_lane.get(waiter["lane"])
                if busy_n + 1 > self.cfg.owner_max_slots or (
                        lane_cap is not None
                        and by_lane.get(waiter["lane"], 0) + 1 > lane_cap):
                    continue
                free = [s for s in free if busy_cpu + s.cpus
                        <= self.cfg.owner_max_cpus + 1e-9]
                if not free:
                    continue
            need = self.cfg.estimate(waiter.get("job_type") or "")
            mem = mem_available_gib()
            if committed + need > self.cfg.mem_budget_gib + 1e-9 or (
                    mem is not None
                    and mem - need * 0.5 < self.cfg.mem_reserve_gib):
                # Fits a slot but not memory: hold every lower class back.
                if committed > 0 or (mem is not None and mem <
                                     self.cfg.mem_reserve_gib):
                    blocked_rank = rank if blocked_rank is None \
                        else min(blocked_rank, rank)
                    continue
                # Nothing runs at all: a lone oversized call still starts.
            # Prefer a quick slot for a child, this project's warm slot,
            # then the least recently used.
            free.sort(key=lambda s: (
                0 if waiter.get("child") and s.quick else 1,
                0 if waiter.get("affinity") and
                s.last_affinity == waiter["affinity"] else 1,
                s.last_used))
            slot = free[0]
            rec = self.get(waiter["call_id"])
            if not rec or rec["status"] != "submitted":
                continue
            waiter["slot"] = slot.name
            rec.update({"status": "starting", "slot": slot.name})
            self.put(rec)
            slot.last_used = now()
            occupying.append((slot, waiter["priority"],
                              waiter.get("job_type") or "",
                              bool(waiter.get("child"))))
            committed += need
            moved = True
        if moved:
            self.cond.notify_all()
        return moved

    # ── the launch ──
    def execute(self, lane, call_id, body):
        job = body.get("job")
        if not isinstance(job, dict):
            return 400, {"error": "invalid executor job",
                         "safe_to_fallback": True}
        jtype = str(job.get("type") or "")
        queue_backed = isinstance(job.get("id"), int) and isinstance(
            job.get("total_claims"), int)
        synchronous = job.get("id") is None and job.get(
            "total_claims") is None and jtype in SYNCHRONOUS_TYPES
        if not queue_backed and not synchronous:
            return 400, {"error": "invalid executor job",
                         "safe_to_fallback": True}
        launch_id = body.get("launch_id")
        launch_id = launch_id if isinstance(launch_id, str) and \
            LAUNCH_ID.match(launch_id) else None
        try:
            timeout_s = float(body.get("timeout_s", 3600))
        except (TypeError, ValueError):
            timeout_s = 3600.0
        timeout_s = max(60.0, min(21660.0, timeout_s))
        user_id = job.get("user_id")
        user_id = user_id if isinstance(user_id, int) else None
        priority = str(body.get("priority_class") or "")
        if priority not in CLASS_RANK:
            priority = ("owner" if user_id in self.cfg.owner_user_ids else
                        "trial" if user_id in self.cfg.trial_user_ids else
                        "customer")
        try:
            wait_s = float(body.get("max_wait_s",
                                    self.cfg.wait_s.get(priority, 10.0)))
        except (TypeError, ValueError):
            wait_s = self.cfg.wait_s.get(priority, 10.0)
        wait_s = max(0.0, min(self.cfg.max_wait_s, wait_s))
        payload = job.get("payload") if isinstance(job.get("payload"),
                                                   dict) else {}
        read_only = jtype == "mcp_tool" and payload.get("mutation") is False
        identity = {k: job.get(k) for k in
                    ("id", "type", "project_id", "total_claims")}
        t = now()
        with self.lock:
            existing = self.get(call_id)
            if existing and existing["status"] in ("done", "failed"):
                env = json.loads(existing["envelope"] or "{}") or {
                    "error": existing["error"] or "executor failed"}
                # A failure that proves nothing ran (an admission restart
                # before /run, a fenced lost launch) may be launched again
                # by a NEW launch of the same claim: the dispatcher holds
                # box jobs instead of failing them. The fenced launch itself
                # (its own launch id) stays refused forever.
                relaunch = (existing["status"] == "failed"
                            and env.get("safe_to_fallback") is True
                            and launch_id is not None
                            and launch_id != existing.get("launch_id"))
                if not relaunch:
                    return 200, env
            live = existing if existing and existing["status"] in LIVE \
                else None
            if live and live["job_type"] != jtype:
                return 409, {"error": "call id already belongs to another "
                             "job type"}
            if live:
                return 409, {"error": "call already accepted",
                             "call_status": live["status"]}
            if self.draining:
                return 429, refusal_envelope(
                    jtype, "Box is draining for maintenance; nothing ran")
            if lane not in self.lanes:
                return 404, {"error": f"box does not serve the {lane} lane",
                             "safe_to_fallback": True}
            disk = disk_report()
            if disk["free_gb"] is not None and \
                    disk["free_gb"] < self.cfg.disk_min_free_gib:
                return 429, refusal_envelope(
                    jtype, f"Box scratch disk is low ({disk['free_gb']} GB "
                    "free); nothing ran")
            if len(self.waiting) >= self.cfg.queue_max:
                return 429, refusal_envelope(
                    jtype, "Box admission queue is full; nothing ran")
            rec = {"call_id": call_id, "lane": lane, "job_type": jtype,
                   "status": "submitted", "slot": None,
                   "job": json.dumps(identity), "launch_id": launch_id,
                   "read_only": 1 if read_only else 0, "priority": priority,
                   "user_id": user_id, "envelope": None, "error": None,
                   # The execution lease starts when a slot takes the
                   # call (_run_on_slot); a queued call only needs to
                   # outlive its own bounded wait.
                   "created_at": t, "active_until": t + wait_s + 120,
                   "started_at": None, "container_started": None,
                   "disconnected_at": None, "terminal_at": None,
                   "abandon_reason": None}
            self.put(rec)
            waiter = {"call_id": call_id, "lane": lane, "priority": priority,
                      "enqueued": t, "slot": None, "job_type": jtype,
                      "child": bool(synchronous),
                      "source": str(job.get("dispatcher_source") or ""),
                      "affinity": affinity_key(call_id)}
            self.waiting[call_id] = waiter
            self.live_handlers.add(call_id)
            try:
                deadline = time.monotonic() + wait_s
                self._assign()
                while not waiter["slot"]:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or self.draining:
                        break
                    self.cond.wait(timeout=min(remaining, 1.0))
                    self._assign()
                if not waiter["slot"]:
                    self.waiting.pop(call_id, None)
                    want = waiter["source"]
                    rollout = bool(
                        self.cfg.strict_source and want
                        and want != "unknown"
                        and want not in self.lane_releases(lane))
                    msg = ("Box is draining for maintenance; nothing ran"
                           if self.draining else
                           f"Box is switching releases: no {lane} slot runs "
                           f"{want} yet; nothing ran" if rollout else
                           f"Box is at capacity: no free {lane} slot within "
                           f"{wait_s:.0f}s; nothing ran")
                    refusal = refusal_envelope(jtype, msg, rollout=rollout)
                    cur = self.get(call_id) or rec
                    cur.update({"status": "refused",
                                "envelope": json.dumps(refusal),
                                "error": msg, "terminal_at": now(),
                                "active_until": now()})
                    self.put(cur)
                    return 429, refusal
                self.waiting.pop(call_id, None)
                slot = self.by_name[waiter["slot"]]
            except BaseException:
                self.waiting.pop(call_id, None)
                self.live_handlers.discard(call_id)
                raise
        try:
            return self._run_on_slot(slot, call_id, job, timeout_s)
        finally:
            with self.lock:
                self.live_handlers.discard(call_id)
                self._assign()

    def _refuse_started(self, call_id, jtype, message, status=503,
                        capacity=False):
        with self.lock:
            rec = self.get(call_id)
            refusal = refusal_envelope(jtype, message, capacity=capacity)
            if rec and rec["status"] == "starting":
                rec.update({"status": "refused",
                            "envelope": json.dumps(refusal), "error": message,
                            "terminal_at": now(), "active_until": now()})
                self.put(rec)
                self.cond.notify_all()
            elif rec and rec["status"] in ("done", "failed"):
                return 200, json.loads(rec["envelope"] or "{}")
        return status, refusal

    def _run_on_slot(self, slot, call_id, job, timeout_s):
        jtype = str(job.get("type") or "")
        # Readiness before /run: the right role, serving, not draining.
        try:
            code, body = container_health(slot.port, timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            with self.lock:
                slot.ready = False
                slot.last_error = f"readiness: {exc}"
            return self._refuse_started(
                call_id, jtype, f"container readiness failed: {exc}")
        if code != 200 or body.get("status") != "ok" or \
                body.get("role") != slot.role:
            with self.lock:
                slot.ready = False
                slot.last_error = (f"readiness role={body.get('role')} "
                                   f"status={body.get('status')}")
            return self._refuse_started(
                call_id, jtype,
                f"container readiness mismatch role={body.get('role')} "
                f"source={body.get('code_version')} "
                f"expected_role={slot.role} status={body.get('status')}")
        running, started = docker_state(slot.container)
        with self.lock:
            rec = self.get(call_id)
            if not rec or rec["status"] != "starting" or \
                    rec.get("slot") != slot.name:
                env = json.loads((rec or {}).get("envelope") or "{}") or {
                    "error": "Box startup no longer owns this call",
                    "retryable": True,
                    "failure": {"kind": "provider_start_abandoned",
                                "retryable": True}}
                return 200, env
            rec.update({"status": "running", "started_at": now(),
                        "active_until": now() + timeout_s,
                        "container_started": started})
            self.put(rec)
            slot.last_affinity = affinity_key(call_id) or slot.last_affinity
            shares = self.cfg.cpu_shares.get(rec.get("priority") or "",
                                             self.cfg.cpu_shares["customer"])
        # CPU priority under contention: this call's class decides the
        # slot's cgroup weight. Best effort: a refusal never fails a call.
        if slot.shares != shares:
            if docker_cpu_shares(slot.container, shares):
                slot.shares = shares
            else:
                log(f"could not set cpu shares {shares} on {slot.container}")
        rel = self.release_info()
        forwarded = dict(job)
        forwarded["provider_call_id"] = call_id
        forwarded["provider_adapter_version"] = \
            f"box-{rel.get('commit') or 'unknown'}"
        data = json.dumps({"job": forwarded}).encode()
        conn = http.client.HTTPConnection(
            "127.0.0.1", slot.port, timeout=timeout_s + 120)
        sent = False
        try:
            conn.connect()
            conn.request("POST", "/run", body=data, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {str(self.secret)}"})
            sent = True
            resp = conn.getresponse()
            raw = resp.read()
            try:
                envelope = json.loads(raw)
            except ValueError:
                raise RuntimeError(
                    f"container /run HTTP {resp.status} returned non-JSON: "
                    f"{raw[:240]!r}")
            if not isinstance(envelope, dict):
                raise RuntimeError(
                    f"container /run HTTP {resp.status} returned an invalid "
                    "envelope")
        except Exception as exc:  # noqa: BLE001
            if not sent:
                # The connection never opened: no /run reached Python.
                with self.lock:
                    rec = self.get(call_id)
                    if rec and rec["status"] == "running":
                        rec["status"] = "starting"
                        self.put(rec)
                return self._refuse_started(
                    call_id, jtype, "Box container is not running; nothing "
                    f"ran ({type(exc).__name__}: {exc})")
            with self.lock:
                rec = self.get(call_id)
                if rec and rec["status"] in ("running",):
                    rec.update({"status": "unknown",
                                "disconnected_at": rec.get(
                                    "disconnected_at") or now(),
                                "error": f"{type(exc).__name__}: {exc}"})
                    self.put(rec)
            return 502, {"error": f"{type(exc).__name__}: {exc}",
                         "call_status": "unknown"}
        finally:
            conn.close()
        with self.lock:
            rec = self.get(call_id)
            if rec and rec["status"] == "done":
                return 200, json.loads(rec["envelope"] or "{}")
            status = "failed" if envelope.get("error") else "done"
            if rec:
                self.terminalize(rec, status, envelope)
            self._prune()
        return 200, envelope

    # ── status, lease and liveness ──
    def status(self, call_id):
        with self.lock:
            rec = self.get(call_id)
            if not rec:
                return None
            t = now()
            live = call_id in self.live_handlers
            if rec["status"] == "submitted" and not live and \
                    t - (rec["updated_at"] or t) >= SUBMITTED_STALE_S:
                rec = self.terminalize(rec, "failed", refusal_envelope(
                    rec["job_type"], "Box startup was abandoned before /run",
                    capacity=False))
            elif rec["status"] == "starting" and not live and \
                    t - (rec["updated_at"] or t) >= STARTING_STALE_S:
                rec = self.terminalize(rec, "failed", refusal_envelope(
                    rec["job_type"], "Box startup was abandoned before /run",
                    capacity=False))
            elif rec["status"] == "running" and not live:
                rec.update({"status": "unknown",
                            "disconnected_at": rec.get("disconnected_at") or t,
                            "error": "the admission handler awaiting /run "
                                     "was lost"})
                self.put(rec)
        if rec["status"] in ("running", "unknown", "stopping"):
            rec = self._check_exit_or_lease(rec)
        return rec

    def _check_exit_or_lease(self, rec):
        slot = self.by_name.get(rec.get("slot") or "")
        t = now()
        if rec["status"] == "unknown" and slot is not None:
            running, started = docker_state(slot.container)
            if running is False or (started and rec.get("container_started")
                                    and started != rec["container_started"]):
                with self.lock:
                    cur = self.get(rec["call_id"])
                    if cur and cur["status"] == "unknown":
                        reason = f"Box container exited during {cur['job_type']}"
                        return self.terminalize(cur, "failed",
                                                abandoned_envelope(
                                                    cur["job_type"],
                                                    bool(cur["read_only"]),
                                                    reason))
                return self.get(rec["call_id"])
        if (rec.get("active_until") or t) > t:
            return rec
        # Lease expired: fence the container, then terminalize.
        return self._fence_and_fail(rec, lease=True)

    def _fence_and_fail(self, rec, lease=False, reason=None):
        slot = self.by_name.get(rec.get("slot") or "")
        with self.lock:
            cur = self.get(rec["call_id"])
            if not cur or cur["status"] in ("done", "failed", "refused"):
                return cur
            if slot is not None and self.active_call(slot.name) == \
                    cur["call_id"]:
                cur.update({"status": "stopping"})
                self.put(cur)
            else:
                slot = None
        if slot is not None:
            log(f"fencing slot {slot.name} for call {rec['call_id']}")
            if not docker_restart(slot.container):
                with self.lock:
                    cur = self.get(rec["call_id"])
                    cur["error"] = f"{cur.get('error') or ''}; fence restart " \
                        "failed (retried on the next status read)"
                    self.put(cur)
                    return cur
            with self.lock:
                slot.ready = False   # the poller re-proves readiness
        with self.lock:
            cur = self.get(rec["call_id"])
            jtype = cur["job_type"]
            if reason:
                envelope = abandoned_envelope(jtype, bool(cur["read_only"]),
                                              reason)
            elif lease:
                render = jtype in {"preview", "preview_check", "final"}
                retryable = not render and (jtype != "mcp_tool"
                                            or bool(cur["read_only"]))
                error = (f"Box {jtype} call exceeded its executor lease while "
                         f"{cur['status']}")
                envelope = {"error": error, "retryable": retryable,
                            "failure": {
                                "kind": ("render_budget_exceeded" if render
                                         else "transient_infrastructure"),
                                "retryable": retryable,
                                "max_attempts": (retry_attempts(jtype)
                                                 if retryable else 0),
                                "agent_repairable": False}}
            else:
                envelope = abandoned_envelope(jtype, bool(cur["read_only"]),
                                              "executor liveness lost")
            return self.terminalize(cur, "failed", envelope)

    def complete(self, call_id, body):
        job, envelope = body.get("job"), body.get("envelope")
        if not matches_job(call_id, job) or not isinstance(envelope, dict) \
                or envelope.get("job_completed") is not True \
                or envelope.get("error"):
            return False
        with self.lock:
            rec = self.get(call_id)
            if not rec or rec["job_type"] != job.get("type"):
                return False
            if rec["status"] == "done":
                return True
            if rec["status"] not in ("running", "unknown"):
                return False
            self.terminalize(rec, "done", envelope)
            self._assign()
            return True

    def abandon(self, call_id, body):
        job = body.get("job")
        reason = re.sub(r"[^\x20-\x7e]", " ", str(body.get("reason") or ""))
        reason = reason[:160] or "executor liveness lost"
        if not matches_job(call_id, job):
            return False, None
        rec = self.status(call_id)
        if not rec or rec["job_type"] != job.get("type"):
            return False, rec
        if rec["status"] in ("done", "failed"):
            return True, rec
        if rec["status"] != "unknown" or now() - (
                rec.get("disconnected_at") or now()) < \
                ABANDON_MIN_DISCONNECTED_S:
            return False, rec
        rec = self._fence_and_fail(rec, reason=f"abandoned: {reason}")
        return bool(rec and rec["status"] == "failed"), rec

    def fence(self, call_id, body):
        job = body.get("job") if isinstance(body.get("job"), dict) else {}
        launch_id = body.get("launch_id")
        launch_id = launch_id if isinstance(launch_id, str) and \
            LAUNCH_ID.match(launch_id) else None
        rec = self.status(call_id)
        with self.lock:
            rec = self.get(call_id)
            older_refusal = rec and rec["status"] == "refused" and (
                not launch_id or rec.get("launch_id") != launch_id)
            if rec and not older_refusal:
                return rec
            message = ("Box never received this launch; it was fenced so it "
                       "can never start, and nothing ran")
            t = now()
            fenced = {"call_id": call_id, "lane": (rec or {}).get("lane")
                      or "unknown",
                      "job_type": str(job.get("type") or "unknown"),
                      "status": "failed", "slot": None,
                      "job": json.dumps({k: job.get(k) for k in
                                         ("id", "type", "project_id",
                                          "total_claims")}),
                      # The fenced launch: only a launch with ANOTHER id
                      # may reuse this call id (execute()).
                      "launch_id": launch_id, "read_only": 0,
                      "priority": None,
                      "user_id": None,
                      "envelope": json.dumps({
                          "error": message, "retryable": True, "fenced": True,
                          "safe_to_fallback": True, "box_wait": True,
                          "failure": {"kind": "provider_start_abandoned",
                                      "retryable": True}}),
                      "error": message, "created_at": t, "active_until": t,
                      "started_at": None, "container_started": None,
                      "disconnected_at": None, "terminal_at": t,
                      "abandon_reason": None}
            self.put(fenced)
            return fenced

    def _prune(self):
        cutoff = now() - TERMINAL_RETENTION_S
        self.db.execute(
            "DELETE FROM calls WHERE call_id IN (SELECT call_id FROM calls "
            "WHERE status IN ('done','failed','refused') AND terminal_at < ? "
            "LIMIT 256)", (cutoff,))

    # ── background: container readiness, exits, leases, steal log ──
    def poll_slots(self):
        for slot in self.slots:
            running, started = docker_state(slot.container)
            try:
                code, body = container_health(slot.port, timeout=3.0)
                ok = (code == 200 and body.get("status") == "ok"
                      and body.get("role") == slot.role)
                release = str(body.get("code_version") or "unknown")
                status = str(body.get("status") or "unknown")
                err = "" if ok else f"health {code} {status} role=" \
                    f"{body.get('role')}"
            except Exception as exc:  # noqa: BLE001
                ok, release, status, err = False, slot.release, "down", str(exc)
            with self.lock:
                slot.running = bool(running)
                slot.started_at = started
                slot.ready = bool(ok and running)
                slot.release = release
                slot.health_status = status
                slot.last_error = err
                slot.checked_at = now()
        # Unknown/running calls: container exits and lease expiry.
        with self.lock:
            recs = [dict(r) for r in self.db.execute(
                "SELECT * FROM calls WHERE status IN "
                "('running','unknown','stopping')")]
        for rec in recs:
            try:
                if rec["status"] == "stopping":
                    self._fence_and_fail(rec, reason="fence retried")
                elif rec["call_id"] not in self.live_handlers or \
                        (rec.get("active_until") or now()) <= now():
                    self.status(rec["call_id"])
            except Exception as exc:  # noqa: BLE001
                log(f"poll of {rec['call_id']} failed: {exc}")
        with self.lock:
            self._assign()
            self.cond.notify_all()

    def reload_config(self):
        """Apply box.json's scalar settings without a restart (a restart
        orphans every in-flight /run handler). Slot changes need boxctl and
        a restart; they are reported, not applied."""
        try:
            mtime = os.stat(CONFIG_PATH).st_mtime
        except OSError:
            return False
        if self._config_mtime is None:
            self._config_mtime = mtime
            return False
        if mtime == self._config_mtime:
            return False
        self._config_mtime = mtime
        raw = load_json(CONFIG_PATH, None)
        if not isinstance(raw, dict):
            log("box.json unreadable; keeping the running configuration")
            return False
        cfg = Config(raw)
        with self.lock:
            if [(x.get("name"), x.get("lane")) for x in cfg.slots] != \
                    [(x.name, x.lane) for x in self.slots]:
                log("box.json changed its slots: restart admission to apply "
                    "them (scalar settings applied now)")
            cfg.slots = self.cfg.slots
            self.cfg = cfg
            self.cond.notify_all()
        log("box.json reloaded")
        return True

    def background(self):
        last_log = 0.0
        while True:
            self.cpu.sample()
            try:
                self.reload_config()
            except Exception as exc:  # noqa: BLE001
                log(f"config reload failed: {exc}")
            try:
                self.poll_slots()
            except Exception as exc:  # noqa: BLE001
                log(f"slot poll failed: {exc}")
            if time.monotonic() - last_log >= 60:
                last_log = time.monotonic()
                self._write_steal_line()
            time.sleep(5)

    def _write_steal_line(self):
        w = self.cpu.window(60) or {}
        with self.lock:
            busy = sum(1 for s in self.slots if self.active_call(s.name))
            depth = len(self.waiting)
        mem = mem_available_gib()
        line = ",".join(str(v) for v in (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            w.get("steal_pct", ""), w.get("busy_pct", ""),
            round(mem, 2) if mem else "", busy, depth))
        try:
            with open(STEAL_LOG, "a", encoding="ascii") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    # ── admin (localhost + admin token only; Caddy never proxies /admin) ──
    def set_draining(self, on):
        with self.lock:
            self.draining = bool(on)
            self.db.execute("INSERT OR REPLACE INTO kv VALUES ('draining', ?)",
                            ("1" if on else "0",))
            self.cond.notify_all()

    def set_retiring(self, name, on):
        with self.lock:
            slot = self.by_name.get(name)
            if not slot:
                return None
            slot.retiring = bool(on)
            self.cond.notify_all()
            return slot.report(self.active_call(name))


# ── Rate limiting ───────────────────────────────────────────────────────────
class RateLimiter:
    def __init__(self, rate, burst):
        self.rate, self.burst = rate, burst
        self.buckets = {}
        self.failures = collections.defaultdict(collections.deque)
        self.blocked = {}
        self.lock = threading.Lock()

    def allow(self, ip):
        t = time.monotonic()
        with self.lock:
            until = self.blocked.get(ip)
            if until and until > t:
                return False
            tokens, last = self.buckets.get(ip, (self.burst, t))
            tokens = min(self.burst, tokens + (t - last) * self.rate)
            if tokens < 1:
                self.buckets[ip] = (tokens, t)
                return False
            self.buckets[ip] = (tokens - 1, t)
            if len(self.buckets) > 4096:
                self.buckets.clear()
            return True

    def auth_failed(self, ip):
        t = time.monotonic()
        with self.lock:
            q = self.failures[ip]
            q.append(t)
            while q and t - q[0] > 600:
                q.popleft()
            if len(q) >= 20:
                self.blocked[ip] = t + 600
                log(f"blocking {ip} for 10 min after repeated auth failures")


ROUTE = re.compile(
    r"^/calls/(interactive|batch|agent|mcp|shorts|vision)/([^/]+)"
    r"(/complete|/abandon|/fence)?$")


def make_handler(box, limiter):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "valmera-box"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def client_ip(self):
            peer = self.client_address[0]
            fwd = self.headers.get("X-Forwarded-For", "")
            if peer in ("127.0.0.1", "::1") and fwd:
                return fwd.split(",")[0].strip()
            return peer

        def send_json(self, code, obj):
            data = json.dumps(obj, default=str).encode()
            try:
                self.send_response(code)
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            got = self.headers.get("Authorization", "")
            supplied = got[7:] if got.startswith("Bearer ") else ""
            secret = str(box.secret)
            return bool(secret) and hmac.compare_digest(
                hashlib.sha256(supplied.encode()).digest(),
                hashlib.sha256(secret.encode()).digest())

        def admin_ok(self):
            if self.client_address[0] not in ("127.0.0.1", "::1") or \
                    self.headers.get("X-Forwarded-For"):
                return False
            got = self.headers.get("X-Admin-Token", "")
            return bool(box.admin_token) and hmac.compare_digest(
                got.encode(), box.admin_token.encode())

        def read_body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 16 * 1024 * 1024:
                raise ValueError("request body too large")
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            return body

        def gate(self):
            ip = self.client_ip()
            if not limiter.allow(ip):
                self.send_json(429, {"error": "rate limited",
                                     "safe_to_fallback": True})
                return False
            if not self.authorized():
                limiter.auth_failed(ip)
                self.send_json(401, {"error": "unauthorized",
                                     "safe_to_fallback": True})
                return False
            return True

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/ping":
                return self.send_json(200, {"ok": True})
            if path.startswith("/admin/"):
                if not self.admin_ok():
                    return self.send_json(404, {"error": "not found"})
                if path == "/admin/state":
                    return self.send_json(200, box.health())
                return self.send_json(404, {"error": "not found"})
            if not self.gate():
                return
            if path == "/health":
                return self.send_json(200, box.health())
            m = re.match(r"^/probes/(interactive|batch|agent|mcp|shorts)$",
                         path)
            if m:
                lane = m.group(1)
                for slot in box.slots:
                    if slot.lane == lane and slot.ready:
                        try:
                            code, body = container_health(slot.port)
                            body["slot"] = slot.name
                            return self.send_json(code, body)
                        except Exception as exc:  # noqa: BLE001
                            return self.send_json(503, {"error": str(exc)})
                return self.send_json(503, {"error": f"no ready {lane} slot"})
            m = ROUTE.match(path)
            if not m or not CALL_ID.match(m.group(2)) or m.group(3):
                return self.send_json(404, {"error": "not found",
                                            "safe_to_fallback": True})
            rec = box.status(m.group(2))
            if not rec:
                return self.send_json(404, {"status": "missing"})
            return self.send_json(200, box.state_json(rec))

        def do_POST(self):
            path = urlparse(self.path).path
            if path.startswith("/admin/"):
                if not self.admin_ok():
                    return self.send_json(404, {"error": "not found"})
                try:
                    body = self.read_body()
                except ValueError as exc:
                    return self.send_json(400, {"error": str(exc)})
                if path == "/admin/drain":
                    box.set_draining(bool(body.get("on")))
                    return self.send_json(200, {"draining": box.draining})
                if path == "/admin/slot":
                    out = box.set_retiring(str(body.get("name")),
                                           bool(body.get("retiring")))
                    return self.send_json(200 if out else 404,
                                          out or {"error": "no such slot"})
                if path == "/admin/refresh":
                    box.poll_slots()
                    return self.send_json(200, box.health())
                return self.send_json(404, {"error": "not found"})
            if not self.gate():
                return
            m = ROUTE.match(path)
            if not m or not CALL_ID.match(m.group(2)):
                return self.send_json(404, {"error": "not found",
                                            "safe_to_fallback": True})
            lane, call_id, action = m.group(1), m.group(2), \
                (m.group(3) or "")[1:]
            if not call_id.startswith("bx-"):
                return self.send_json(400, {
                    "error": "box call ids start with bx-",
                    "safe_to_fallback": True})
            try:
                body = self.read_body()
            except (ValueError, TypeError) as exc:
                return self.send_json(400, {"error": f"bad request: {exc}",
                                            "safe_to_fallback": True})
            jtype = str(((body.get("job") or {}) if isinstance(
                body.get("job"), dict) else {}).get("type") or "")
            media_tool = jtype == "mcp_tool" and lane in (
                "interactive", "batch") and (
                action != "" or ((body.get("job") or {}).get(
                    "payload") or {}).get("tool") == "__media__")
            if jtype not in LANE_TYPES.get(lane, set()) and not media_tool:
                return self.send_json(400, {
                    "error": f"job type {jtype} is not allowed on {lane}",
                    "safe_to_fallback": True})
            if action == "complete":
                ok = box.complete(call_id, body)
                return self.send_json(200 if ok else 409,
                                      {"acknowledged": bool(ok)})
            if action == "abandon":
                ok, rec = box.abandon(call_id, body)
                out = box.state_json(rec) if rec else {"status": "missing"}
                out["abandoned"] = bool(ok)
                return self.send_json(200 if ok else 409, out)
            if action == "fence":
                rec = box.fence(call_id, body)
                out = box.state_json(rec)
                out["fenced"] = bool((out.get("envelope") or {}).get(
                    "fenced"))
                return self.send_json(200, out)
            code, out = box.execute(lane, call_id, body)
            return self.send_json(code, out)

    return Handler


class EnvSecret:
    """REMOTE_EXECUTOR_SECRET, re-read when the provisioned env file changes.

    box/provision.py rewrites ENV_PATH from GitHub's secrets; a rotation then
    reaches admission within a few seconds without a restart (a restart would
    orphan every in-flight /run handler). The environment wins when set."""

    def __init__(self, path):
        self.path = path
        self.mtime = None
        self.value = os.getenv("REMOTE_EXECUTOR_SECRET", "").strip()
        self.lock = threading.Lock()

    def __str__(self):
        return self.get()

    def __bool__(self):
        return bool(self.get())

    def encode(self, *args):
        return self.get().encode(*args)

    def get(self):
        if os.getenv("REMOTE_EXECUTOR_SECRET", "").strip():
            return os.getenv("REMOTE_EXECUTOR_SECRET").strip()
        with self.lock:
            try:
                mtime = os.stat(self.path).st_mtime
            except OSError:
                return self.value
            if mtime != self.mtime:
                self.mtime = mtime
                try:
                    with open(self.path, encoding="utf-8") as fh:
                        for line in fh:
                            key, sep, val = line.strip().partition("=")
                            if sep and key == "REMOTE_EXECUTOR_SECRET":
                                self.value = val.strip().strip("'\"")
                except OSError:
                    pass
            return self.value


def main():
    cfg = Config(load_json(CONFIG_PATH, {}))
    secret = EnvSecret(ENV_PATH)
    admin_token = os.getenv("BOX_ADMIN_TOKEN", "").strip()
    if not admin_token:
        try:
            with open(ADMIN_TOKEN_PATH, encoding="ascii") as fh:
                admin_token = fh.read().strip()
        except OSError:
            pass
    if not secret.get():
        log("REMOTE_EXECUTOR_SECRET is not provisioned yet: every call is "
            "refused 401 until box/provision.py writes it")
    if not cfg.slots:
        log("no slots configured")
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    box = Box(cfg, secret, admin_token)
    limiter = RateLimiter(cfg.rate_per_s, cfg.rate_burst)
    box.cpu.sample()
    threading.Thread(target=box.background, daemon=True,
                     name="box-background").start()
    handler = make_handler(box, limiter)
    server = ThreadingHTTPServer((cfg.host, cfg.port), handler)
    server.daemon_threads = True

    def bind_extra(host):
        # The bridge address exists only once docker made the network (at
        # boot admission can start first): keep trying, never give up.
        while True:
            try:
                extra = ThreadingHTTPServer((host, cfg.port), handler)
            except OSError as exc:
                log(f"cannot listen on {host}:{cfg.port} yet ({exc})")
                time.sleep(10)
                continue
            extra.daemon_threads = True
            log(f"listening on {host}:{cfg.port}")
            extra.serve_forever()

    for host in cfg.extra_hosts:
        threading.Thread(target=bind_extra, args=(host,), daemon=True,
                         name=f"listen-{host}").start()
    log(f"listening on {cfg.host}:{cfg.port} with {len(cfg.slots)} slots "
        f"({', '.join(s['name'] + ':' + s['lane'] for s in cfg.slots)}); "
        f"draining={box.draining}")
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
