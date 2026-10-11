"""The one job kind behind every sidecar (worker/devices/INTERFACES.md §2.5).

Synchronous like ``matte`` (no video_jobs row): sidecars.ensure() launches it
in the background through remote.run_sidecar_remote, and the runner
(analysis_jobs.run_sidecar_job) publishes each chunk's manifest itself. Home
lane batch; the vision lane is dark (config.VISION_LANE_ENABLED). A future
sidecar kind is a new analysis runner, never a new job type.

Routing is listed once each, by track 0A: config.CLOUDFLARE_EXECUTOR_TYPES /
CLOUDFLARE_SYNCHRONOUS_TYPES defaults, remote._cloudflare_lane (vision
branch) and the type sets and env lists in cloudflare/src/index.ts.
"""

from . import JobKind

JOB_KINDS = [
    JobKind(name="sidecar", runner="analysis_jobs:run_sidecar_job",
            lane="batch", synchronous=True, timeout_s=900,
            features=("onnx.rvm", "onnx.yunet")),
]
