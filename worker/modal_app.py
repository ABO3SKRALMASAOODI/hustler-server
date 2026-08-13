"""Modal deployment for Valmera's scale-to-zero executor fleet.

Deploy from the repository root:

    modal deploy worker/modal_app.py --env main

The Render dispatcher invokes these as durable Modal Functions, not Web
Functions. A returned call id is an unambiguous launch boundary and lets the
dispatcher reconnect instead of starting a duplicate paid render.
"""

import os
import sys
from pathlib import Path

import modal


ROOT = Path(__file__).resolve().parent
APP_NAME = "valmera-executor"
SECRET_NAME = "valmera-executor-production"

# The dependency image is intentionally identical to the proven Cloud Run
# executor image. Modal caches Dockerfile layers, and add_local_dir adds only
# source on ordinary deploys. This avoids provider-specific ffmpeg/model drift.
image = modal.Image.from_dockerfile(
    ROOT / "Dockerfile",
    context_dir=ROOT,
    build_args={"STEMS": "1", "FFMPEG_STATIC": "1"},
).add_local_dir(ROOT, "/app", copy=True)

agent_image = modal.Image.from_dockerfile(
    ROOT / "Dockerfile.agent-base",
    context_dir=ROOT,
).add_local_dir(ROOT, "/app", copy=True)

secret = modal.Secret.from_name(SECRET_NAME)
app = modal.App(APP_NAME)

COMMON = {
    "image": image,
    "secrets": [secret],
    "region": "us",
    "routing_region": "us-east",
    "min_containers": 0,
    "max_containers": 5,
    "scaledown_window": 120,
    "retries": 0,
    "timeout": 3600,
    "startup_timeout": 300,
}


def _boot(profile, role="executor"):
    os.environ["WORKER_ROLE"] = role
    os.environ["EXECUTOR_PROVIDER"] = "modal"
    os.environ["MODAL_EXECUTOR_PROFILE"] = profile
    os.environ.setdefault("WORKER_TMP_DIR", "/tmp/valmera")
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")


def _run(job, profile, role="executor"):
    if (job or {}).get("type") == "__warm":
        return {"result": {"warmed": True}, "job_completed": False}
    _boot(profile, role)
    import config
    import http_server
    import executor_runtime
    config.require_core()
    executor_runtime.ensure_heartbeat()
    return executor_runtime.execute(job or {}, http_server.RUNNERS)


@app.function(name="preview", cpu=2.0, memory=8192, **COMMON)
def preview(job):
    return _run(job, "preview")


@app.function(name="batch", cpu=4.0, memory=16384, **COMMON)
def batch(job):
    return _run(job, "batch")


@app.function(name="heavy", cpu=4.0, memory=32768, **COMMON)
def heavy(job):
    return _run(job, "heavy")


@app.function(
    name="agent", image=agent_image, secrets=[secret], region="us",
    routing_region="us-east", min_containers=0, max_containers=5,
    scaledown_window=120, retries=0, timeout=3600, startup_timeout=300,
    cpu=(0.125, 1.0), memory=2048,
)
@modal.concurrent(max_inputs=4, target_inputs=3)
def agent(job):
    return _run(job, "agent", role="agent_executor")


@app.function(
    name="health", image=image, min_containers=0, max_containers=1,
    cpu=0.125, memory=512, timeout=60, startup_timeout=300,
    scaledown_window=60, retries=0, region="us", routing_region="us-east",
)
def health():
    _boot("health")
    import version
    report = version.version_report()
    report.update({"status": "ok", "provider": "modal"})
    return report
