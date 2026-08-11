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


def request_profile(role=None, service=None):
    role = role or os.getenv("WORKER_ROLE", "executor")
    service = service or os.getenv("K_SERVICE", "")
    if role == "agent_executor" or service == "valmera-agent":
        return "agent-1cpu-2g-concurrency2", 1, 2
    if "preview" in service:
        return "preview-8cpu-8g", 8, 8
    return "request-heavy-8cpu-32g", 8, 32


def annotate_request(timings, seconds, role=None, service=None):
    name, cpu, memory = request_profile(role, service)
    unit = cpu * REQUEST_CPU_S + memory * REQUEST_GIB_S
    timings.update({
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
