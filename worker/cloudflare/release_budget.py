"""Executor release budget for deploy-cloudflare-executor.yml (R3, 25 Oct 2026).

Every executor release rebuilds both images, probes all five lanes and makes
Cloudflare replace every warm container (rollout_active_grace_period, then a
drain): warm MCP sessions and preview media are lost, and each lane's next
calls start cold. October had 76 releases, 16 on one day. The rule is AT MOST
TWO EXECUTOR RELEASES A DAY: batch worker changes into as few pushes as
possible (CLAUDE.md, "Executor releases").

This step enforces the part of that rule that is always safe, and reports
the rest:

* COALESCE. When a newer run of this workflow is already waiting in the
  concurrency group, this run's commit is contained in the newer one (main
  only moves forward), so this run skips its publish and the waiting run
  releases both changes at once. Tests still run on every push.
* COUNT. The publishes that succeeded in the trailing 24 hours are counted;
  a third or later release is announced as a warning and in the job summary.
  It is not blocked: Render deploys the dispatcher, and the dispatcher
  publishes the MCP tool catalog, on every push, so an executor held back for
  hours would leave callers offered tools the executors cannot run yet (the
  skew guard would refuse them as "deploy in progress").

* DRAIN. Before a release goes ahead, it waits (at most
  CUSTOMER_DRAIN_MAX_S, 15 min) for a moment when no real customer has a job
  running, so a customer's index, export or Studio turn is not the one the
  rollout recycles. The owner's own production (internal accounts) is not
  waited for: it runs around the clock and is drained by SIGTERM as before.

Writes ``release=true|false`` to $GITHUB_OUTPUT. Any API or database problem
fails open (release=true, no wait): this step may save a release or delay it,
never lose one.
"""

import datetime as dt
import json
import os
import sys
import urllib.request

PUBLISH_STEP = "publish Worker and Container images"
# PLAN.md: the owner's and internal accounts (60 is the owner's agents).
INTERNAL_USER_IDS = (22, 28, 29, 45, 46, 49, 60, 61, 62, 68, 108, 398, 1058)
CUSTOMER_JOBS_SQL = """
    SELECT id, type, user_id FROM video_jobs
     WHERE state = 'running'
       AND heartbeat_at >= NOW() - INTERVAL '3 minutes'
       AND NOT (user_id = ANY(%s))
     ORDER BY id"""
DAILY_RULE = 2
WAITING = ("queued", "pending", "waiting", "requested")


def _get(path):
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(
        f"{api}{path}",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _ts(value):
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def newer_waiting_run(runs, me):
    """The newest run of this workflow waiting behind this one, or None."""
    mine = next((r for r in runs if r.get("id") == me), None)
    if mine is None:
        return None
    later = [r for r in runs
             if r.get("id") != me and r.get("status") in WAITING
             and r.get("head_branch") == mine.get("head_branch")
             and (r.get("run_number") or 0) > (mine.get("run_number") or 0)]
    return max(later, key=lambda r: r.get("run_number") or 0, default=None)


def published(jobs):
    """Did this run's publish step succeed?"""
    for job in jobs.get("jobs") or []:
        for step in job.get("steps") or []:
            if step.get("name") == PUBLISH_STEP \
                    and step.get("conclusion") == "success":
                return True
    return False


def decide(runs, me, publishes_24h, event):
    """(release, note) for this run."""
    if event == "workflow_dispatch":
        return True, "operator dispatch: release"
    newer = newer_waiting_run(runs, me)
    if newer is not None:
        return False, (
            f"run #{newer.get('run_number')} ({str(newer.get('head_sha'))[:8]}) "
            "is waiting with a newer main that contains this commit: it "
            "releases both, so this run skips its publish")
    note = f"release {publishes_24h + 1} in the trailing 24 h"
    if publishes_24h >= DAILY_RULE:
        note += (f" (rule: at most {DAILY_RULE} a day; batch worker changes "
                 "into fewer pushes, CLAUDE.md 'Executor releases')")
    return True, note


def running_customer_jobs(database_url):
    """Real customers' jobs running now (read-only), or None if unknown."""
    if not database_url:
        return None
    import psycopg2
    conn = psycopg2.connect(
        database_url, connect_timeout=10,
        options="-c default_transaction_read_only=on "
                "-c statement_timeout=15000")
    try:
        with conn.cursor() as cur:
            cur.execute(CUSTOMER_JOBS_SQL, (list(INTERNAL_USER_IDS),))
            return cur.fetchall()
    finally:
        conn.close()


def drain_customers(database_url, max_s, poll_s=20.0, sleep=None,
                    clock=None, probe=None):
    """Wait until no customer job is running, at most max_s. Returns
    (waited_s, still_running_rows_or_None)."""
    import time
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    probe = probe or running_customer_jobs
    started = clock()
    while True:
        try:
            rows = probe(database_url)
        except Exception as exc:  # noqa: BLE001 — fail open
            print(f"customer drain check unavailable ({type(exc).__name__})")
            return clock() - started, None
        if not rows:
            return clock() - started, rows
        if clock() - started >= max_s:
            return clock() - started, rows
        print("waiting for customer job(s) to finish before the release: "
              + ", ".join(f"{r[1]} {r[0]} (user {r[2]})" for r in rows[:6]))
        sleep(poll_s)


def main():
    repo = os.environ["GITHUB_REPOSITORY"]
    me = int(os.environ["GITHUB_RUN_ID"])
    event = os.environ.get("GITHUB_EVENT_NAME", "push")
    release, note, count = True, "budget check unavailable: release", None
    workflow = os.environ.get("WORKFLOW_FILE", "deploy-cloudflare-executor.yml")
    try:
        runs = _get(f"/repos/{repo}/actions/workflows/{workflow}/runs"
                    "?per_page=60").get("workflow_runs") or []
        since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
        count = 0
        for run in runs:
            if run.get("id") == me or _ts(run["created_at"]) < since:
                continue
            if run.get("status") != "completed":
                continue
            if published(_get(f"/repos/{repo}/actions/runs/{run['id']}/jobs")):
                count += 1
        release, note = decide(runs, me, count, event)
    except Exception as exc:  # noqa: BLE001 — fail open, loudly
        note = f"budget check unavailable ({type(exc).__name__}): release"
    if release:
        max_s = float(os.environ.get("CUSTOMER_DRAIN_MAX_S", "900"))
        waited, rows = drain_customers(os.environ.get("DATABASE_URL"), max_s)
        if rows:
            note += (f"; {len(rows)} customer job(s) still running after "
                     f"{waited:.0f} s: released anyway (SIGTERM drains them)")
        elif waited >= 1:
            note += f"; waited {waited:.0f} s for customer jobs to finish"
        if waited >= 1 and event != "workflow_dispatch":
            # a newer push may have queued while this run waited
            try:
                runs = _get(f"/repos/{repo}/actions/workflows/{workflow}"
                            "/runs?per_page=60").get("workflow_runs") or []
                if newer_waiting_run(runs, me) is not None:
                    release, note = decide(runs, me, count or 0, event)
            except Exception:  # noqa: BLE001 — keep the decision made
                pass
    level = "notice"
    if release and count is not None and count >= DAILY_RULE:
        level = "warning"
    print(f"::{level}::executor release budget: {note}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Executor release budget\n\n{note}.\n\n"
                         + ("Published.\n" if release else
                            "**Not published by this run.**\n"))
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"release={'true' if release else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
