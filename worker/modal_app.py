"""Modal deployment for Valmera's scale-to-zero executor fleet.

Deploy from the repository root:

    modal deploy worker/modal_app.py --env main

The Render dispatcher invokes these as durable Modal Functions, not Web
Functions. A returned call id is an unambiguous launch boundary and lets the
dispatcher reconnect instead of starting a duplicate paid render.
"""

import hashlib
import os
import sys
from pathlib import Path

import modal


ROOT = Path(__file__).resolve().parent
APP_NAME = "valmera-executor"
SECRET_NAME = "valmera-executor-production"

# The dependency image is intentionally identical to the proven Cloud Run
# executor image. Keep ordinary source out of the Docker build context: the
# Dockerfiles' final ``COPY . .`` then copies only requirements.txt, and the
# add_local_dir layer below supplies the current source. Without this ignore a
# one-line Python change invalidates Chromium, Whisper and Demucs and turns
# every release into a multi-minute full image rebuild.
DEPENDENCY_CONTEXT_IGNORE = ["**", "!requirements.txt"]
image = modal.Image.from_dockerfile(
    ROOT / "Dockerfile",
    context_dir=ROOT,
    build_args={"STEMS": "1", "FFMPEG_STATIC": "1"},
    ignore=DEPENDENCY_CONTEXT_IGNORE,
).add_local_dir(ROOT, "/app", copy=True)

agent_image = modal.Image.from_dockerfile(
    ROOT / "Dockerfile.agent-base",
    context_dir=ROOT,
    ignore=DEPENDENCY_CONTEXT_IGNORE,
).add_local_dir(ROOT, "/app", copy=True)

secret = modal.Secret.from_name(SECRET_NAME)
app = modal.App(APP_NAME)

COMMON = {
    "image": image,
    "secrets": [secret],
    # A live global-placement canary put a customer final in ap-northeast-2.
    # Keep latency/data paths inside the proven US envelope; cost reduction
    # comes from right-sized reservations, never geographic roulette.
    "region": "us",
    "routing_region": "us-east",
    "min_containers": 0,
    "max_containers": 5,
    # Keep a container only long enough to absorb an immediate follow-up.
    # The old two-minute tail was the largest source of paid idle time.
    "scaledown_window": 10,
    "retries": 0,
    "timeout": 3600,
    "startup_timeout": 300,
}

# Modal CPU floats are reservations, not limits: an uncapped ffmpeg process
# may burst into spare host cores and is billed for that actual usage. Pairing
# request=limit preserves the benchmarked Cloud Run-equivalent shape and makes
# the per-second ceiling in compute_cost.py a real ceiling.
PREVIEW_CPU = (2.0, 2.0)   # physical cores = about 4 vCPU
BATCH_CPU = (4.0, 4.0)     # physical cores = about 8 vCPU
HEAVY_CPU = (4.0, 4.0)

# The first number is the guaranteed/billed memory reservation and the second
# is the unchanged hard limit. Modal bills max(request, actual), so moving the
# reservation toward the measured working set saves money without taking away
# the capacity a rare large input may need. CPU request and limit stay
# identical: no render gets fewer cycles or a lower speed ceiling.
PREVIEW_MEMORY = (2048, 4096)
BATCH_MEMORY = (8192, 16384)
LIGHT_MEMORY = (8192, 32768)
HEAVY_MEMORY = (16384, 32768)
AGENT_MEMORY = (1024, 2048)
PROBE_MEMORY = (1024, 4096)
HEALTH_MEMORY = (512, 1024)


def _boot(profile, role="executor", pricing_multiplier=1.5):
    os.environ["WORKER_ROLE"] = role
    os.environ["EXECUTOR_PROVIDER"] = "modal"
    os.environ["MODAL_EXECUTOR_PROFILE"] = profile
    os.environ["MODAL_PRICING_MULTIPLIER"] = str(pricing_multiplier)
    # A Modal agent turn must offload any render tools to the compute
    # functions, exactly as the Render dispatcher does. Set this before
    # importing config/http_server so their module-level routing is correct.
    if role == "agent_executor":
        os.environ["MODAL_EXECUTOR_ENABLED"] = "1"
        os.environ["MODAL_EXECUTOR_PERCENT"] = "100"
    os.environ.setdefault("WORKER_TMP_DIR", "/tmp/valmera")
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")


def adapter_version():
    """Fingerprint the Modal-only transport/configuration adapter."""
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    except OSError:
        return "unknown"


def _run(job, profile, role="executor", pricing_multiplier=1.5):
    _boot(profile, role, pricing_multiplier)
    import config
    import http_server
    import executor_runtime
    if (job or {}).get("type") == "__warm":
        import resource_usage
        import subprocess
        import version
        ffmpeg = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True,
            timeout=10, check=True).stdout.splitlines()[0]
        return {
            "result": {
                "warmed": True,
                "profile": profile,
                "role": role,
                "code_version": version.code_version(),
                "adapter_version": adapter_version(),
                "features": version.version_report().get("features", []),
                "runners": sorted(http_server.RUNNERS),
                "ffmpeg": ffmpeg,
                "compute_region": os.getenv("MODAL_REGION", "unknown"),
                "compute_cloud_provider": os.getenv(
                    "MODAL_CLOUD_PROVIDER", "unknown"),
                "pricing_multiplier": pricing_multiplier,
                "resources": resource_usage.snapshot(),
            },
            "job_completed": False,
        }
    config.require_core()
    executor_runtime.ensure_heartbeat()
    return executor_runtime.execute(job or {}, http_server.RUNNERS)


@app.function(name="preview", cpu=PREVIEW_CPU, memory=PREVIEW_MEMORY,
              **COMMON)
def preview(job):
    return _run(job, "preview")


@app.function(name="batch", cpu=BATCH_CPU, memory=BATCH_MEMORY, **COMMON)
def batch(job):
    return _run(job, "batch")


@app.function(name="light", cpu=HEAVY_CPU, memory=LIGHT_MEMORY, **COMMON)
def light(job):
    """Short browser/frame tools keep full CPU and the old 32-GiB limit."""
    return _run(job, "light")


@app.function(name="heavy", cpu=HEAVY_CPU, memory=HEAVY_MEMORY, **COMMON)
def heavy(job):
    return _run(job, "heavy")


@app.function(name="egress", cpu=HEAVY_CPU, memory=LIGHT_MEMORY,
              **COMMON)
def egress(job):
    """Keep URL acquisition on the proven US egress while right-sizing RAM."""
    return _run(job, "egress", pricing_multiplier=1.5)


@app.function(
    name="probe", image=image, secrets=[secret], region="us",
    routing_region="us-east", min_containers=0, max_containers=1,
    scaledown_window=5, retries=0, timeout=600, startup_timeout=300,
    cpu=(0.25, 0.5), memory=PROBE_MEMORY,
)
def probe(job):
    """Run diagnostics without renting the 32-GiB heavy profile."""
    return _run(job, "probe", pricing_multiplier=1.5)


@app.function(
    name="agent", image=agent_image, secrets=[secret], region="us",
    routing_region="us-east", min_containers=0, max_containers=5,
    scaledown_window=30, retries=0, timeout=3600, startup_timeout=300,
    cpu=(0.125, 1.0), memory=AGENT_MEMORY,
)
# Agent turns spend most of their wall time waiting on the model, database, or
# remote render functions. Share that idle I/O time inside one container before
# scaling another container; no always-on instance is purchased.
@modal.concurrent(max_inputs=6, target_inputs=4)
def agent(job):
    return _run(job, "agent", role="agent_executor", pricing_multiplier=1.5)


@app.function(
    name="health", image=image, min_containers=0, max_containers=1,
    cpu=0.125, memory=HEALTH_MEMORY, timeout=60, startup_timeout=300,
    scaledown_window=5, retries=0, region="us", routing_region="us-east",
)
def health():
    _boot("health", pricing_multiplier=1.5)
    import version
    report = version.version_report()
    report.update({
        "status": "ok",
        "provider": "modal",
        "adapter_version": adapter_version(),
        "pricing_multiplier": 1.5,
        "compute_region": os.getenv("MODAL_REGION", "unknown"),
        "compute_cloud_provider": os.getenv(
            "MODAL_CLOUD_PROVIDER", "unknown"),
    })
    return report
