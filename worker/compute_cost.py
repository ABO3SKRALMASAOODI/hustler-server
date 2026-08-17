"""Gross provider compute estimates attached to every terminal job.

Billing export remains the invoice source of truth. These deliberately ignore
free tier/credits so they are a conservative per-job ceiling that can be summed
from ``video_jobs.result`` immediately, without waiting for billing ingestion.
Cloud Run unit prices are the us-central1 request/Jobs defaults retained by
the fallback fleet; Modal uses its current base rates plus the explicit-region
multiplier recorded by ``modal_app.py``.
"""

import os


REQUEST_CPU_S = 0.000024
REQUEST_GIB_S = 0.0000025
JOB_CPU_S = 0.000018
JOB_GIB_S = 0.000002
MODAL_CORE_S = 0.0000131
MODAL_GIB_S = 0.00000222
MODAL_US_MULTIPLIER = 1.5

# cores, requested GiB, hard-limit GiB, idle tail seconds. The hard limits are
# unchanged from the proven fleet; request/limit separation lets Modal bill
# ordinary inputs at their real shape without rejecting an outlier.
MODAL_PROFILES = {
    "preview": (2.0, 2, 4, 10),
    "batch": (4.0, 4, 16, 10),
    "final": (4.0, 4, 16, 10),
    "index": (4.0, 4, 16, 10),
    "light": (1.0, 1, 4, 10),
    "heavy": (4.0, 16, 32, 10),
    "egress": (1.0, 1, 4, 10),
    "agent": (0.125, 1, 2, 30),
    "mcp": (0.125, 1, 2, 20),
    "shorts": (0.125, 1, 2, 20),
    "probe": (0.25, 1, 4, 5),
    "health": (0.125, 0.5, 1, 5),
}


def request_profile(role=None, service=None):
    role = role or os.getenv("WORKER_ROLE", "executor")
    service = service or os.getenv("K_SERVICE", "")
    if role == "agent_executor" or service == "valmera-agent":
        return "agent-1cpu-2g-concurrency4", 1, 2
    if "preview" in service:
        return "preview-4cpu-8g", 4, 8
    if "executor-batch" in service:
        return "request-batch-8cpu-16g", 8, 16
    return "request-heavy-8cpu-32g", 8, 32


def annotate_request(timings, seconds, role=None, service=None):
    if os.getenv("EXECUTOR_PROVIDER", "") == "modal":
        profile = os.getenv("MODAL_EXECUTOR_PROFILE", "heavy")
        priced_profile = (profile[:-3]
                          if profile.endswith("-eu") else profile)
        cores, memory_request, memory_limit, tail_s = MODAL_PROFILES.get(
            priced_profile, MODAL_PROFILES["heavy"])
        try:
            multiplier = float(os.getenv(
                "MODAL_PRICING_MULTIPLIER", str(MODAL_US_MULTIPLIER)))
        except ValueError:
            multiplier = MODAL_US_MULTIPLIER
        multiplier = max(1.0, multiplier)
        reserved_unit = (cores * MODAL_CORE_S
                         + memory_request * MODAL_GIB_S) * multiplier
        limit_unit = (cores * MODAL_CORE_S
                      + memory_limit * MODAL_GIB_S) * multiplier
        region_class = "global" if multiplier == 1.0 else (
            "pinned-eu" if profile.endswith("-eu") else "pinned-us")
        timings.update({
            "compute_provider": "modal",
            "compute_profile": (
                f"modal-{profile}-{cores:g}core-"
                f"{memory_request}-{memory_limit}g-{region_class}"),
            "compute_region_class": region_class,
            "compute_pricing_multiplier": multiplier,
            # Compatibility key: the amount guaranteed on every second.
            "compute_unit_usd_s": round(reserved_unit, 9),
            "compute_unit_usd_s_hard_limit": round(limit_unit, 9),
            "gross_compute_usd_reserved": round(
                float(seconds) * reserved_unit, 6),
            # Actual billing lies between reserved and this unchanged hard
            # limit according to observed use on each second.
            "gross_compute_usd_ceiling": round(
                float(seconds) * limit_unit, 6),
            # Conservative: consecutive jobs may reuse one tail, so this is a
            # visibility ceiling rather than a billable per-job charge.
            "configured_idle_tail_s": tail_s,
            "gross_compute_usd_with_tail_ceiling": round(
                (float(seconds) + tail_s) * limit_unit, 6),
        })
        if os.getenv("MODAL_REGION"):
            timings["compute_region"] = os.environ["MODAL_REGION"]
        if os.getenv("MODAL_CLOUD_PROVIDER"):
            timings["compute_cloud_provider"] = \
                os.environ["MODAL_CLOUD_PROVIDER"]
        return timings
    name, cpu, memory = request_profile(role, service)
    unit = cpu * REQUEST_CPU_S + memory * REQUEST_GIB_S
    timings.update({
        "compute_provider": "cloud_run",
        "compute_profile": name,
        "compute_unit_usd_s": round(unit, 9),
        # With concurrency >1 this is a ceiling per request; overlapping agent
        # turns share one instance and the invoice is lower.
        "gross_compute_usd_ceiling": round(float(seconds) * unit, 6),
    })
    return timings


def annotate_batch(timings, seconds, cpu=8, memory=16):
    unit = cpu * JOB_CPU_S + memory * JOB_GIB_S
    timings.update({
        "compute_profile": f"job-{cpu}cpu-{memory}g",
        "compute_unit_usd_s": round(unit, 9),
        "gross_compute_usd_ceiling": round(float(seconds) * unit, 6),
    })
    return timings
