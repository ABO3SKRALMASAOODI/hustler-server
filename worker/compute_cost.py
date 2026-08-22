"""Gross provider compute estimates attached to every terminal job.

Billing export remains the invoice source of truth. These deliberately ignore
free tier/credits so they are a conservative per-job ceiling that can be summed
from ``video_jobs.result`` immediately, without waiting for billing ingestion.
Cloud Run unit prices are the us-central1 request/Jobs defaults retained by
the fallback fleet; Modal uses its current base rates plus the explicit-region
multiplier recorded by ``modal_app.py``; Cloudflare uses measured active CPU
plus provisioned memory/disk and conservative outbound-byte cost.
"""

import os


REQUEST_CPU_S = 0.000024
REQUEST_GIB_S = 0.0000025
JOB_CPU_S = 0.000018
JOB_GIB_S = 0.000002
MODAL_CORE_S = 0.0000131
MODAL_GIB_S = 0.00000222
MODAL_US_MULTIPLIER = 1.5

# Cloudflare Containers public list rates (2026-04-21). CPU is billed from
# measured active CPU time; memory and disk are billed from the provisioned
# instance shape for every running wall-clock second. Included Workers Paid
# usage is deliberately ignored so this remains a conservative gross estimate
# comparable to the Modal estimate above.
CLOUDFLARE_VCPU_S = 0.000020
CLOUDFLARE_GIB_S = 0.0000025
CLOUDFLARE_GB_DISK_S = 0.00000007
CLOUDFLARE_EGRESS_GB = 0.025
CLOUDFLARE_PROFILES = {
    "standard-3": (2.0, 8.0, 16.0),
    "standard-4": (4.0, 12.0, 20.0),
}

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
    provider = os.getenv("EXECUTOR_PROVIDER", "")
    if provider == "cloudflare":
        profile = os.getenv(
            "CLOUDFLARE_CONTAINER_PROFILE", "standard-4")
        cores, memory_gib, disk_gb = CLOUDFLARE_PROFILES.get(
            profile, CLOUDFLARE_PROFILES["standard-4"])
        wall_s = max(0.0, float(seconds))
        try:
            measured_cpu_s = max(
                0.0, float(timings.get("container_cpu_s")))
        except (TypeError, ValueError):
            # Missing cgroup CPU evidence is not allowed to turn Cloudflare
            # into an artificially cheap provider. Provisioned-vCPU wall time
            # is the conservative ceiling until measurement is available.
            measured_cpu_s = wall_s * cores
        active_cost = measured_cpu_s * CLOUDFLARE_VCPU_S
        memory_cost = wall_s * memory_gib * CLOUDFLARE_GIB_S
        disk_cost = wall_s * disk_gb * CLOUDFLARE_GB_DISK_S
        try:
            egress_bytes = max(0, int(timings.get("uploaded_bytes") or 0))
        except (TypeError, ValueError):
            egress_bytes = 0
        egress_cost = (egress_bytes / (1024 ** 3)) * CLOUDFLARE_EGRESS_GB
        tail_s = 60
        idle_unit = (memory_gib * CLOUDFLARE_GIB_S
                     + disk_gb * CLOUDFLARE_GB_DISK_S)
        gross = active_cost + memory_cost + disk_cost + egress_cost
        timings.update({
            "compute_provider": "cloudflare",
            "compute_profile": (
                f"cloudflare-{profile}-{cores:g}vcpu-"
                f"{memory_gib:g}g-{disk_gb:g}gb"),
            "compute_cpu_measured_s": round(measured_cpu_s, 3),
            "compute_cpu_usd": round(active_cost, 6),
            "compute_memory_usd": round(memory_cost, 6),
            "compute_disk_usd": round(disk_cost, 6),
            "compute_egress_usd_ceiling": round(egress_cost, 6),
            "gross_compute_usd_ceiling": round(gross, 6),
            "configured_idle_tail_s": tail_s,
            "gross_compute_usd_with_tail_ceiling": round(
                gross + tail_s * idle_unit, 6),
        })
        return timings
    if provider == "modal":
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
