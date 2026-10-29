#!/usr/bin/env python3
"""Valmera render box: host control (images, slots, updates, disk hygiene).

    boxctl.py update     pull main; when the worker fingerprint changed, build
                         the two executor images and roll every slot onto them,
                         draining each before it is replaced (systemd timer,
                         every 2 min; one run at a time)
    boxctl.py apply      make every configured slot container exist and run
                         the current release (boot, config changes)
    boxctl.py clean      disk hygiene (systemd timer, every 15 min)
    boxctl.py status     one JSON line: release, slots, disk

Pull-based: the box reads the public repository over HTTPS and needs no
credential to update itself. The images are built from the SAME Dockerfiles
and build arguments as the Cloudflare lanes (worker/cloudflare/wrangler.jsonc:
the batch image, FULL_COMPUTE=1 + WHISPER_MODEL=medium, serves the media
lanes; Dockerfile.agent-base serves agent, mcp and shorts), tagged with the
worker's source fingerprint (worker/version.py), so a slot's /health reports
exactly the code the dispatcher compares against.

Rolling update with drain: half of each lane's slots are retired (admission
stops giving them calls), each is replaced once idle, then the other half.
Admission's strict release matching sends every call to a slot running its
dispatcher's release, so old and new dispatchers are both served during the
switch and no job ever runs on other code.
"""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

CONFIG_PATH = os.getenv("BOX_CONFIG", "/etc/valmera/box.json")
RELEASE_PATH = os.getenv("BOX_RELEASE", "/var/lib/valmera/release.json")
DATA_DIR = os.getenv("BOX_DATA_DIR", "/var/lib/valmera")
REPO = os.getenv("BOX_REPO", "/opt/valmera/repo")
BOX_HOME = os.getenv("BOX_HOME", "/opt/valmera/box")
ENV_PATH = os.getenv("BOX_ENV_FILE", "/etc/valmera/executor.env")
SLOT_ENV_PATH = os.getenv("BOX_SLOT_ENV_FILE", "/etc/valmera/slot.env")
ADMIN_TOKEN_PATH = os.getenv("BOX_ADMIN_TOKEN_FILE",
                             "/etc/valmera/admin.token")
LOCK_PATH = "/run/valmera-boxctl.lock"
CHANGED_FLAG = os.path.join(DATA_DIR, "env-changed")
LOG_PATH = "/var/log/valmera/boxctl.log"
NETWORK = "valmera"
SUBNET = "172.30.0.0/24"
GATEWAY = "172.30.0.1"
ROLES = {"interactive": "executor", "batch": "executor",
         "agent": "agent_executor", "mcp": "mcp_executor",
         "shorts": "shorts_executor"}
EXEC_LANES = {"interactive", "batch"}


def log(msg):
    line = f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def sh(args, check=True, timeout=None, capture=True, cwd=None):
    out = subprocess.run(args, capture_output=capture, text=True,
                         timeout=timeout, cwd=cwd)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:4])} failed ({out.returncode}): "
                           f"{(out.stderr or out.stdout or '')[-600:]}")
    return out


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def config():
    return load_json(CONFIG_PATH, {})


def release():
    return load_json(RELEASE_PATH, {})


# ── admission's admin API (localhost + token) ──────────────────────────────
def admin(method, path, body=None):
    cfg = config()
    try:
        with open(ADMIN_TOKEN_PATH, encoding="ascii") as fh:
            token = fh.read().strip()
    except OSError:
        token = ""
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(cfg.get('listen_port', 8090))}{path}",
        method=method, data=(json.dumps(body).encode() if body is not None
                             else None),
        headers={"X-Admin-Token": token,
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def admission_state():
    try:
        return admin("GET", "/admin/state")
    except Exception as exc:  # noqa: BLE001
        log(f"admission state unavailable: {exc}")
        return None


# ── source and images ──────────────────────────────────────────────────────
def fetch_main():
    sh(["git", "-C", REPO, "fetch", "-q", "origin", "main"], timeout=300)
    commit = sh(["git", "-C", REPO, "rev-parse", "FETCH_HEAD"]).stdout.strip()
    sh(["git", "-C", REPO, "checkout", "-q", "--detach", "--force", commit])
    return commit


def source_version():
    out = sh([sys.executable, "-c",
              "import version; print(version.code_version())"],
             cwd=os.path.join(REPO, "worker"))
    return out.stdout.strip()


def image_names(source):
    return f"valmera-exec:{source}", f"valmera-agent:{source}"


def image_exists(name):
    return sh(["docker", "image", "inspect", name], check=False).returncode == 0


def build_images(source):
    exec_image, agent_image = image_names(source)
    worker = os.path.join(REPO, "worker")
    for name, args in (
            (exec_image, ["-f", "Dockerfile.cloudflare",
                          "--build-arg", "FULL_COMPUTE=1",
                          "--build-arg", "WHISPER_MODEL=medium"]),
            (agent_image, ["-f", "Dockerfile.agent-base"])):
        if image_exists(name):
            continue
        log(f"building {name}")
        started = time.monotonic()
        # Low priority: a build must not take CPU from customers' renders.
        sh(["nice", "-n", "19", "ionice", "-c", "3", "docker", "build", "-q",
            *args, "--build-arg", f"SOURCE_VERSION={source}", "-t", name,
            "."], cwd=worker, timeout=7200)
        log(f"built {name} in {time.monotonic() - started:.0f}s")
    return exec_image, agent_image


def install_box_tooling():
    """Copy box/ from the fetched commit to BOX_HOME; names of changed files."""
    src = os.path.join(REPO, "box")
    changed = []
    for root, _dirs, files in os.walk(src):
        for name in files:
            path = os.path.join(root, name)
            rel = os.path.relpath(path, src)
            dest = os.path.join(BOX_HOME, rel)
            with open(path, "rb") as fh:
                new = fh.read()
            try:
                with open(dest, "rb") as fh:
                    if fh.read() == new:
                        continue
            except OSError:
                pass
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest + ".tmp", "wb") as fh:
                fh.write(new)
            os.chmod(dest + ".tmp", 0o755 if name.endswith((".py", ".sh"))
                     else 0o644)
            os.replace(dest + ".tmp", dest)
            changed.append(rel)
    return changed


# ── slot containers ────────────────────────────────────────────────────────
def container_name(slot):
    return slot.get("container") or f"vbox-{slot['name']}"


def container_info(name):
    out = sh(["docker", "inspect", "-f",
              "{{.Config.Image}}|{{.State.Running}}|{{.Created}}|"
              "{{range $p, $b := .HostConfig.PortBindings}}"
              "{{range $b}}{{.HostPort}}{{end}}{{end}}", name],
             check=False)
    if out.returncode != 0:
        return None
    image, running, created, port = (out.stdout.strip().split("|", 3)
                                     + [""])[:4]
    return {"image": image, "running": running == "true", "created": created,
            "port": port.strip()}


def _slot_drifted(slot, info, source):
    """Missing, stopped, on another image, or published on another port."""
    return (info is None or not info["running"]
            or info["image"] != slot_image(slot, source)
            or (info.get("port") and info["port"] != str(slot["port"])))


def _bytes(size):
    size = str(size).strip().lower()
    mult = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}.get(size[-1:], 1)
    return int(float(size.rstrip("kmg")) * mult)


def sync_limits():
    """Apply box.json's per-slot CPU and memory limits to running slots in
    place (docker update): a limit change needs no drain or restart."""
    for slot in config().get("slots", []):
        name = container_name(slot)
        out = sh(["docker", "inspect", "-f",
                  "{{.HostConfig.NanoCpus}}|{{.HostConfig.Memory}}|"
                  "{{.HostConfig.CpusetCpus}}", name], check=False)
        if out.returncode != 0:
            continue
        nano, mem, cpuset = (out.stdout.strip().split("|") + ["", "", ""])[:3]
        nano, mem = int(nano or 0), int(mem or 0)
        want_nano = int(float(slot.get("cpus", 1)) * 1e9)
        want_mem = _bytes(slot.get("memory", "2g"))
        want_set = str(slot.get("cpuset") or "")
        if nano == want_nano and mem == want_mem and cpuset == want_set:
            continue
        args = ["docker", "update", "--cpus", str(slot.get("cpus", 1)),
                "--memory", str(slot.get("memory", "2g")),
                "--memory-swap", str(slot.get("memory", "2g"))]
        if want_set or cpuset:
            args += ["--cpuset-cpus", want_set or f"0-{os.cpu_count() - 1}"]
        res = sh(args + [name], check=False)
        log(f"limits {name}: cpus {nano / 1e9:g}->{want_nano / 1e9:g}, "
            f"memory {mem >> 20}->{want_mem >> 20} MiB, cpuset "
            f"{cpuset or 'all'}->{want_set or 'all'} "
            f"({'ok' if res.returncode == 0 else res.stderr.strip()[:160]})")


def ensure_network():
    if sh(["docker", "network", "inspect", NETWORK],
          check=False).returncode == 0:
        return
    sh(["docker", "network", "create", "--driver", "bridge",
        "--subnet", SUBNET, "--gateway", GATEWAY,
        "-o", "com.docker.network.bridge.name=vbox0", NETWORK])


def slot_image(slot, source):
    exec_image, agent_image = image_names(source)
    return exec_image if slot["lane"] in EXEC_LANES else agent_image


def free_port(port, name):
    """Remove another IDLE slot container still publishing ``port`` (a slot
    plan re-numbered the ports); a busy one keeps it until a later run."""
    out = sh(["docker", "ps", "-a", "--filter", f"publish={int(port)}",
              "--format", "{{.Names}}|{{.Label \"valmera.slot\"}}"],
             check=False)
    state = None
    for line in out.stdout.split():
        other, _, other_slot = line.partition("|")
        if other == name or not other_slot:
            continue
        state = state or admission_state() or {}
        if slot_busy(state, other_slot):
            raise RuntimeError(f"port {port} is held by busy slot {other}")
        sh(["docker", "rm", "-f", other], check=False)
        log(f"removed {other}: it held port {port} of the new slot plan")


def run_slot(slot, source):
    """(Re)create one slot container on ``source``'s image."""
    name = container_name(slot)
    sh(["docker", "rm", "-f", name], check=False)
    free_port(slot["port"], name)
    scratch = os.path.join(DATA_DIR, "slots", slot["name"])
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch, exist_ok=True)
    cfg = config()
    port = int(cfg.get("listen_port", 8090))
    mem = str(slot.get("memory", "2g"))
    args = [
        "docker", "run", "-d", "--name", name,
        "--restart", "unless-stopped",
        "--network", NETWORK,
        "-p", f"127.0.0.1:{int(slot['port'])}:8080",
        "--cpus", str(slot.get("cpus", 1)),
        *(["--cpuset-cpus", str(slot["cpuset"])] if slot.get("cpuset")
          else []),
        "--memory", mem, "--memory-swap", mem,
        "--pids-limit", str(slot.get("pids", 4096)),
        "--cpu-shares", "1024",
        "--shm-size", str(slot.get("shm", "512m")),
        "--stop-timeout", "900",
        "-v", f"{scratch}:/tmp/valmera",
        "--label", f"valmera.slot={slot['name']}",
        "--label", f"valmera.lane={slot['lane']}",
        "--label", f"valmera.source={source}",
        "--env-file", SLOT_ENV_PATH,
        "--env-file", ENV_PATH,
        "-e", f"WORKER_ROLE={ROLES[slot['lane']]}",
        "-e", "EXECUTOR_PROVIDER=box",
        "-e", f"BOX_SLOT={slot['name']}",
        "-e", f"BOX_SLOT_CPUS={slot.get('cpus', 1)}",
        "-e", f"BOX_EXECUTOR_URL=http://{GATEWAY}:{port}",
        slot_image(slot, source),
    ]
    sh(args, timeout=300)


def wait_ready(slot, source, timeout_s=240):
    deadline = time.monotonic() + timeout_s
    url = f"http://127.0.0.1:{int(slot['port'])}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                body = json.load(resp)
            if body.get("status") == "ok" and \
                    body.get("role") == ROLES[slot["lane"]] and \
                    body.get("code_version") == source:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3)
    return False


def slot_busy(state, name):
    for s in (state or {}).get("slot_detail", []):
        if s.get("name") == name:
            return bool(s.get("busy"))
    return False


def roll_slots(slots, source, drain_timeout_s):
    """Retire, drain, replace and readmit ``slots`` together."""
    for slot in slots:
        try:
            admin("POST", "/admin/slot", {"name": slot["name"],
                                          "retiring": True})
        except Exception as exc:  # noqa: BLE001
            log(f"could not retire {slot['name']}: {exc}")
    pending = list(slots)
    deadline = time.monotonic() + drain_timeout_s
    while pending:
        state = admission_state()
        for slot in list(pending):
            if state is not None and slot_busy(state, slot["name"]) and \
                    time.monotonic() < deadline:
                continue
            if state is not None and slot_busy(state, slot["name"]):
                # Past the drain bound the slot keeps its old release and
                # stays retired until a later run: never kill a call.
                log(f"{slot['name']} still busy after drain window; "
                    "leaving it on its release for the next run")
                try:
                    admin("POST", "/admin/slot", {"name": slot["name"],
                                                  "retiring": False})
                except Exception:  # noqa: BLE001
                    pass
                pending.remove(slot)
                continue
            log(f"replacing {slot['name']} with {source}")
            try:
                run_slot(slot, source)
                ready = wait_ready(slot, source)
            except Exception as exc:  # noqa: BLE001
                log(f"slot {slot['name']} failed to start: {exc}")
                ready = False
            if not ready:
                log(f"slot {slot['name']} not ready on {source}")
            try:
                admin("POST", "/admin/slot", {"name": slot["name"],
                                              "retiring": False})
                admin("POST", "/admin/refresh", {})
            except Exception as exc:  # noqa: BLE001
                log(f"could not readmit {slot['name']}: {exc}")
            pending.remove(slot)
        if pending:
            time.sleep(5)


def stale_slots(source):
    """Configured slots whose container is missing, stopped or on another
    image, plus all of them when the environment changed."""
    env_changed = os.path.exists(CHANGED_FLAG)
    out = []
    for slot in config().get("slots", []):
        info = container_info(container_name(slot))
        if env_changed or _slot_drifted(slot, info, source):
            out.append(slot)
    return out


def provisioned():
    """Whether provision.py has delivered the secrets every slot needs."""
    names = set()
    try:
        with open(ENV_PATH, encoding="utf-8") as fh:
            for line in fh:
                key, sep, value = line.strip().partition("=")
                if sep and value:
                    names.add(key)
    except OSError:
        return False
    return {"REMOTE_EXECUTOR_SECRET", "DATABASE_URL",
            "S3_ACCESS_KEY_ID"}.issubset(names)


def converge(source, commit, drain_timeout_s):
    if not provisioned():
        log("executor secrets not provisioned yet (box-provision workflow); "
            "slots wait")
        return False
    ensure_network()
    todo = stale_slots(source)
    if not todo:
        return False
    log(f"rolling {len(todo)} slot(s) onto {source}")
    # Wave 1: missing or stopped containers (nothing to drain) and about
    # half of each lane's live slots, never the whole lane while it serves;
    # wave 2: the rest, once wave 1 serves the new release.
    lane_size = {}
    for slot in config().get("slots", []):
        lane_size[slot["lane"]] = lane_size.get(slot["lane"], 0) + 1
    by_lane = {}
    for slot in todo:
        by_lane.setdefault(slot["lane"], []).append(slot)
    first, second = [], []
    for lane, items in by_lane.items():
        dead = [s for s in items
                if not (container_info(container_name(s)) or {}).get(
                    "running")]
        live = [s for s in items if s not in dead]
        first.extend(dead)
        half = (len(live) + 1) // 2
        if lane_size[lane] > 1 and len(live) == lane_size[lane]:
            half = min(half, lane_size[lane] - 1)
        first.extend(live[:half])
        second.extend(live[half:])
    roll_slots(first, source, drain_timeout_s)
    rel = release()
    if rel.get("source") and rel.get("source") != source:
        rel["previous_source"] = rel["source"]
    rel.update({"source": source, "commit": commit,
                "deployed_at": datetime.now(timezone.utc).isoformat()})
    write_json(RELEASE_PATH, rel)
    if second:
        # Parents (mcp, agent, shorts) before the media lanes their children
        # use: an old parent still draining keeps old media slots to call.
        parents = [s for s in second if s["lane"] not in EXEC_LANES]
        media = [s for s in second if s["lane"] in EXEC_LANES]
        if parents:
            roll_slots(parents, source, drain_timeout_s)
        if media:
            roll_slots(media, source, drain_timeout_s)
    if os.path.exists(CHANGED_FLAG) and not stale_slots_without_env(source):
        os.remove(CHANGED_FLAG)
    return True


def stale_slots_without_env(source):
    out = []
    for slot in config().get("slots", []):
        info = container_info(container_name(slot))
        if _slot_drifted(slot, info, source):
            out.append(slot)
    return out


def restart_services(changed):
    """Pick up new box tooling. Admission restarts are safe (its state is
    durable and every in-flight call is recovered as `unknown` until its
    executor finishes) but are done only when no call is running."""
    units = []
    if "provision.py" in changed:
        units.append("valmera-provision")
    if "admission.py" in changed:
        state = admission_state() or {}
        busy = (state.get("slots") or {}).get("busy", 0)
        depth = (state.get("queue") or {}).get("depth", 0)
        if busy or depth:
            log("admission.py changed; restart deferred (calls in flight)")
            flag = os.path.join(DATA_DIR, "admission-restart-pending")
            open(flag, "w").close()
        else:
            units.append("valmera-admission")
    try:
        with open(os.path.join(BOX_HOME, "host", "box.json"), "rb") as fh:
            repo_plan = fh.read()
        with open(CONFIG_PATH, "rb") as fh:
            live_plan = fh.read()
    except OSError:
        repo_plan = live_plan = b""
    if repo_plan and repo_plan != live_plan:
        # The repository's slot plan is the source of truth. Scalar settings
        # reload live; a new slot list needs admission restarted (deferred
        # while calls are in flight, like a code change).
        before = [(s.get("name"), s.get("lane")) for s in
                  config().get("slots", [])]
        shutil.copyfile(os.path.join(BOX_HOME, "host", "box.json"),
                        CONFIG_PATH + ".tmp")
        os.replace(CONFIG_PATH + ".tmp", CONFIG_PATH)
        after = [(s.get("name"), s.get("lane")) for s in
                 config().get("slots", [])]
        log("installed box.json from the repository")
        if before != after and "valmera-admission" not in units:
            state = admission_state() or {}
            if (state.get("slots") or {}).get("busy", 0) or \
                    (state.get("queue") or {}).get("depth", 0):
                open(os.path.join(DATA_DIR, "admission-restart-pending"),
                     "w").close()
                log("slot list changed; admission restart deferred")
            else:
                units.append("valmera-admission")
    for unit in units:
        sh(["systemctl", "restart", unit], check=False)
        log(f"restarted {unit}")


def pending_admission_restart():
    flag = os.path.join(DATA_DIR, "admission-restart-pending")
    if not os.path.exists(flag):
        return
    state = admission_state() or {}
    if (state.get("slots") or {}).get("busy", 0) or \
            (state.get("queue") or {}).get("depth", 0):
        return
    sh(["systemctl", "restart", "valmera-admission"], check=False)
    os.remove(flag)
    log("restarted valmera-admission (deferred)")


def cmd_update():
    cfg = config()
    commit = fetch_main()
    source = source_version()
    changed = install_box_tooling()
    if changed:
        log(f"box tooling changed: {', '.join(sorted(changed))}")
    restart_services(changed)
    pending_admission_restart()
    build_images(source)
    converge(source, commit, float(cfg.get("drain_timeout_s", 7200)))
    sync_limits()
    rel = release()
    if rel.get("source") != source or rel.get("commit") != commit:
        rel.update({"source": source, "commit": commit})
        rel.setdefault("deployed_at",
                       datetime.now(timezone.utc).isoformat())
        write_json(RELEASE_PATH, rel)


def cmd_apply():
    rel = release()
    source = rel.get("source")
    if not source:
        commit = fetch_main()
        source = source_version()
        build_images(source)
        rel.update({"source": source, "commit": commit})
    converge(source, rel.get("commit"), float(
        config().get("drain_timeout_s", 7200)))
    sync_limits()


# ── disk hygiene ───────────────────────────────────────────────────────────
def free_gb(path=DATA_DIR):
    return shutil.disk_usage(path).free / 1024 ** 3


def cmd_clean():
    """Bounded disk use, never touching a busy slot's working files.

    * every slot's scratch: job workdirs older than 12 h, and the source
      cache (srccache) beyond 24 h, or beyond 2 h when the disk is under
      its low-water mark;
    * images: every valmera-* tag other than the current and previous
      release; dangling layers; build cache above 20 GB;
    * stopped containers that no slot owns.
    """
    cfg = config()
    low_water = float(cfg.get("disk_low_water_gib", 40))
    state = admission_state()
    busy = {s.get("name") for s in (state or {}).get("slot_detail", [])
            if s.get("busy")}
    tight = free_gb() < low_water
    now = time.time()
    removed = 0
    for slot in cfg.get("slots", []):
        if slot["name"] in busy:
            continue
        root = os.path.join(DATA_DIR, "slots", slot["name"])
        if not os.path.isdir(root):
            continue
        for entry in os.scandir(root):
            try:
                age = now - entry.stat(follow_symlinks=False).st_mtime
            except OSError:
                continue
            if entry.name == "srccache" and entry.is_dir():
                limit = 2 * 3600 if tight else 24 * 3600
                for cached in os.scandir(entry.path):
                    try:
                        if now - cached.stat().st_atime > limit:
                            os.remove(cached.path)
                            removed += 1
                    except OSError:
                        pass
                continue
            if age > 12 * 3600 or (tight and age > 3600):
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    try:
                        os.remove(entry.path)
                    except OSError:
                        pass
                removed += 1
    rel = release()
    keep = {rel.get("source"), rel.get("previous_source")}
    images = sh(["docker", "images", "--format",
                 "{{.Repository}}:{{.Tag}}"]).stdout.split()
    in_use = {(container_info(container_name(s)) or {}).get("image")
              for s in cfg.get("slots", [])}
    for image in images:
        repo, _, tag = image.partition(":")
        if repo in ("valmera-exec", "valmera-agent") and tag not in keep \
                and image not in in_use:
            sh(["docker", "rmi", image], check=False)
            log(f"removed image {image}")
    sh(["docker", "image", "prune", "-f"], check=False)
    sh(["docker", "builder", "prune", "-f", "--keep-storage", "20GB"],
       check=False, timeout=600)
    log(f"clean: removed {removed} scratch entries; "
        f"{free_gb():.1f} GB free{' (was tight)' if tight else ''}")


def cmd_status():
    rel = release()
    slots = []
    for slot in config().get("slots", []):
        info = container_info(container_name(slot)) or {}
        slots.append({"name": slot["name"], "lane": slot["lane"],
                      "image": info.get("image"),
                      "running": info.get("running")})
    print(json.dumps({"release": rel, "slots": slots,
                      "free_gb": round(free_gb(), 1)}))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    fn = {"update": cmd_update, "apply": cmd_apply, "clean": cmd_clean,
          "status": cmd_status}.get(cmd)
    if fn is None:
        print(__doc__)
        return 2
    if cmd == "status":
        fn()
        return 0
    with open(LOCK_PATH, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"another boxctl run holds {LOCK_PATH}")
            return 0
        fn()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
