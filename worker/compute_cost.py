"""Gross Cloud Run compute estimates attached to every terminal job.

Billing export remains the invoice source of truth. These deliberately ignore
free tier/credits so they are a conservative per-job ceiling that can be summed
from ``video_jobs.result`` immediately, without waiting for billing ingestion.
Unit prices are the us-central1 request/Jobs defaults used by this fleet.
"""

import os


REQUEST_CPU_S = 0.000024
REQUEST_GIB_S = 0.0000025
JOB_CPU_S = 0.000018
JOB_GIB_S = 0.000002
MODAL_CORE_S = 0.0000131
MODAL_GIB_S = 0.00000222
MODAL_US_MULTIPLIER = 1.5


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
        resources = {
            # Modal requests physical cores; two vCPUs are approximately one
            # physical core, preserving the live Cloud Run compute shape.
            "preview": (2.0, 4),
            "batch": (4.0, 16),
            "heavy": (4.0, 32),
            # Agent containers share up to four I/O-heavy turns. The 0.125
            # core reservation may burst to one physical core when needed.
            "agent": (0.125, 1),
            "probe": (0.25, 1),
        }
        cores, memory = resources.get(profile, resources["heavy"])
        tail_s = {"preview": 10, "batch": 10, "heavy": 10,
                  "agent": 30, "probe": 5}.get(profile, 10)
        unit = (cores * MODAL_CORE_S + memory * MODAL_GIB_S) \
            * MODAL_US_MULTIPLIER
        timings.update({
            "compute_provider": "modal",
            "compute_profile": f"modal-{profile}-{cores:g}core-{memory}g-us",
            "compute_unit_usd_s": round(unit, 9),
            "gross_compute_usd_ceiling": round(float(seconds) * unit, 6),
            # Conservative: consecutive jobs may reuse one tail, so this is a
            # visibility ceiling rather than a billable per-job charge.
            "configured_idle_tail_s": tail_s,
            "gross_compute_usd_with_tail_ceiling": round(
                (float(seconds) + tail_s) * unit, 6),
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
