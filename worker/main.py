"""Valmera media+agent worker.

Deployed as a Render Background Worker from worker/Dockerfile. Polls the
video_jobs table in Postgres (FOR UPDATE SKIP LOCKED) with three lanes:

  media lane  — preview | final   (interactive ffmpeg encodes)
  index lane  — index             (multi-minute whisper/scene analysis)
  agent lane  — agent_turn        (IO-bound LLM loops)

Indexing gets its OWN lane so a long analysis can never wedge interactive
previews behind it (that starvation was the #1 "I chatted and nothing
happened" churn cause). Separate agent/media lanes mean an agent turn that
enqueues a preview and waits for it can never deadlock the worker. Heartbeats
keep long jobs claimable-safe; stale jobs are retried up to the attempt limit,
then failed by the reaper.
"""

import os
import shutil
import signal
import threading
import time
import traceback
from datetime import datetime, timezone

import agent_loop
import config
import db as dbx
import filmstrip
import failure_policy
import indexer
import job_completion
import mcp_exec
import remote
import renderer
import shorts
import version
import ytaccess

# A filmstrip is a few seconds of ffmpeg on an already-small proxy, and the
# studio asks for one every time a project is opened. On the single-box
# deployment it rides the MEDIA lane: it is the same shape of work as a
# render, and giving it its own concurrency on a box whose CPU is the ceiling
# would let a hundred project-opens starve the previews people are waiting on.
# With a REMOTE executor (round 91) the media lane is pure HTTP waiting and
# runs several slots. Filmstrips originally remained local, but one project
# with two concurrent 4K asset decoders crossed the dispatcher's 512-MiB
# ceiling three times. Modal now owns filmstrip compute too; the dedicated
# lane remains useful because it bounds queued timeline-art requests to one
# durable launch at a time.
# A failed one is deliberately absent from FAIL_NOTES/REAPER_NOTES — a missing
# strip is a cosmetic degradation, not something to put in a user's chat.
if (config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED
        or (config.CLOUDFLARE_EXECUTOR_ENABLED
            and config.CLOUDFLARE_EXECUTOR_URL)):
    MEDIA_TYPES = ("preview", "preview_check", "final")
    FILMSTRIP_TYPES = ("filmstrip",)
else:
    MEDIA_TYPES = ("preview", "preview_check", "final", "filmstrip")
    FILMSTRIP_TYPES = ()
INDEX_TYPES = ("index",)
# A normal agent turn is intentionally single-attempt because replaying its
# EDL writes can apply the same edit twice. A shorts plan is resumable and has
# its own per-child checkpoints, so it gets the media retry budget. Keeping the
# two types in one lane used the agent attempt limit at claim time but the
# media limit in the reaper: after one worker death a shorts job was neither
# claimable nor exhaustible and sat "running" forever. Its own light lane
# closes that gap and also stops a long podcast plan occupying a chat slot.
AGENT_TYPES = ("agent_turn",)
SHORTS_TYPES = ("shorts_plan",)
MCP_TYPES = ("mcp_tool",)

# Set before a planned shutdown releases or drains anything. Every lane checks
# it before its next claim, closing the race where Render sent SIGTERM, the
# active set became empty, and a polling thread claimed fresh work during the
# final milliseconds before os._exit().
DRAINING = threading.Event()


def _supervise(name, target, args=(), restart_delay_s=1.0):
    """Keep one critical worker loop alive until a planned drain.

    Queue lanes already catch ordinary Exceptions, but a library can still
    terminate a thread with a BaseException or an accidental return. The
    process used to stay healthy enough for Render while that queue family
    silently stopped being served. Restarting the *thread* is safe: a job
    that escaped process_one was untracked in its finally and remains owned by
    its database execution lease (or the durable remote-execution ledger), so
    this supervisor cannot immediately double-claim it.

    The delay prevents a broken boot invariant from becoming a CPU/log storm.
    DRAINING.wait makes the delay interruptible during deployment.
    """
    while not DRAINING.is_set():
        failed = None
        try:
            target(*args)
        except BaseException as exc:  # thread boundary; never reaches main
            failed = exc
            traceback.print_exc()
        if DRAINING.is_set():
            return
        suffix = (f" after {type(failed).__name__}: {failed}"
                  if failed is not None else " after an unexpected return")
        print(f"[supervisor] {name} stopped{suffix}; restarting in "
              f"{restart_delay_s:.1f}s", flush=True)
        try:
            dbx.Db().run(dbx.bump_metric, "worker_thread_restarted")
        except Exception:
            pass
        DRAINING.wait(max(0.0, restart_delay_s))


def _build_runners(policy="redesign"):
    """Choose local runners or their request-based execution owners.

    Media/index use the heavy executor. Agent turns use their smaller sibling
    when discovered; that service exists for memory isolation and scale-out,
    not extra CPU. Explicit empty URLs restore the historical local paths.
    """
    if (config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED
            or (config.CLOUDFLARE_EXECUTOR_ENABLED
                and config.CLOUDFLARE_EXECUTOR_URL)):
        return {
            "index": remote.run_index_remote,
            "preview": remote.run_render_remote,
            "preview_check": remote.run_render_remote,
            "final": remote.run_render_remote,
            "agent_turn": (remote.run_agent_remote
                           if (config.REMOTE_AGENT_EXECUTOR_URL
                               or (config.MODAL_EXECUTOR_ENABLED
                                   and "agent_turn" in
                                   config.MODAL_EXECUTOR_TYPES))
                           else agent_loop.run_agent_job),
            "shorts_plan": (remote.run_shorts_remote
                            if (policy == "redesign"
                                and config.MODAL_EXECUTOR_ENABLED)
                            else shorts.run_shorts_plan),
            "mcp_tool": (remote.run_mcp_remote
                         if (policy == "redesign"
                             and config.MODAL_EXECUTOR_ENABLED)
                         else mcp_exec.run_mcp_job),
            # A main strip is cheap, but inserted clips may be full-resolution
            # phone footage and are sampled concurrently. Keep those decoders
            # off the 512-MiB dispatcher whenever Modal is configured.
            "filmstrip": (remote.run_filmstrip_remote
                          if (config.MODAL_EXECUTOR_ENABLED
                              or (config.CLOUDFLARE_EXECUTOR_ENABLED
                                  and config.CLOUDFLARE_EXECUTOR_URL))
                          else filmstrip.run_filmstrip_job),
        }
    return {
        "index": indexer.run_index_job,
        "preview": renderer.run_render_job,
        "preview_check": renderer.run_render_job,
        "final": renderer.run_render_job,
        "agent_turn": agent_loop.run_agent_job,
        "shorts_plan": shorts.run_shorts_plan,
        # An MCP tool call runs where an agent turn runs — same process, same
        # ToolContext, same tools. That is the whole point: the outside model
        # gets the in-house editor, not a copy of it.
        "mcp_tool": mcp_exec.run_mcp_job,
        "filmstrip": filmstrip.run_filmstrip_job,
    }


RUNNERS_BY_POLICY = {
    "legacy": _build_runners("legacy"),
    "redesign": _build_runners("redesign"),
}
# Kept as a compatibility view for operational scripts/tests that inspect the
# active deployment. Per-job dispatch below never consults it.
RUNNERS = RUNNERS_BY_POLICY[config.EXECUTION_POLICY_MODE]


def _runner_for_job(job):
    policy = config.execution_policy_for(job)
    return RUNNERS_BY_POLICY[policy][job["type"]], policy


def process_one(worker_db, job):
    job_id = job["id"]
    dbx.track_job(job_id)
    lease_claim = job.get("total_claims")
    t0 = time.monotonic()
    queue_wait = None
    if job.get("created_at"):
        queue_wait = round(max(0.0, (
            datetime.now(timezone.utc) - job["created_at"]).total_seconds()), 2)
    # Every provider receives the same queue observation. Previously only the
    # dedicated agent wrapper recomputed it; media utilization therefore had
    # execution time but no queue time, hiding whether a speed problem was
    # cold compute or dispatcher contention.
    job["_queue_wait_s"] = queue_wait
    try:
        provider = remote.stamp_execution_provider(worker_db, job)
        runner, execution_policy = _runner_for_job(job)
        print(f"[job {job_id}] start {job['type']} project={job['project_id']} "
              f"attempt={job['attempts']} queue_wait={queue_wait}s "
              f"policy={execution_policy} provider={provider}", flush=True)
        result = runner(worker_db, job)
        total = round(time.monotonic() - t0, 2)
        # A request-based executor owns its terminal write so a Render redeploy
        # cannot lose the result after Cloud Run completed it.
        # The marker exists only in the HTTP envelope; it is not persisted in
        # video_jobs.result.
        if isinstance(result, dict) and result.pop(
                "_remote_job_completed", False):
            terminal = result.pop("_remote_job_terminal_state", "done")
            print(f"[job {job_id}] {terminal} by remote executor in {total}s "
                  f"(queue {queue_wait}s)", flush=True)
            return
        if isinstance(result, dict):
            timings = result.setdefault("timings", {})
            timings["queue_wait_s"] = queue_wait
            timings["total_s"] = total
            result.setdefault("execution", {}).update({
                "policy": execution_policy,
                "class": config.execution_class_for(job["type"]),
            })
        finished = job_completion.finalize_success(
            worker_db, job, result, lease_claim)
        if finished is False:
            print(f"[job {job_id}] result discarded — execution lease "
                  f"{lease_claim} was superseded", flush=True)
            return
        print(f"[job {job_id}] done in {total}s "
              f"(queue {queue_wait}s) timings="
              f"{(result or {}).get('timings') if isinstance(result, dict) else None}",
              flush=True)
    except Exception as e:
        if isinstance(e, remote.RemoteBatchDetached):
            # The Cloud Run Job owns heartbeats + terminal state. Requeueing
            # an ambiguous launch here is exactly how one execution becomes
            # two paid 8-vCPU executions. The stale reaper is the bounded
            # recovery path if no Job actually started.
            print(f"[job {job_id}] detached from batch execution: {e}",
                  flush=True)
            return
        traceback.print_exc()
        decision = failure_policy.decision_for(e, job["type"])
        if decision.retryable and job["attempts"] < decision.max_attempts:
            requeued = worker_db.run(dbx.requeue_job, job_id, e, lease_claim)
            what = ("requeued" if requeued else
                    "NOT requeued (already terminal — reaped or superseded)")
            print(f"[job {job_id}] {what} after error: {e}", flush=True)
        else:
            failure_result = {
                "failure": decision.payload(e),
                "timings": dict(getattr(e, "executor_timings", {}) or {}),
            }
            failure_result["timings"].setdefault("queue_wait_s", queue_wait)
            failure_result["timings"].setdefault(
                "total_s", round(time.monotonic() - t0, 2))
            finished = worker_db.run(dbx.finish_job, job_id, "failed", e,
                                     failure_result, lease_claim)
            if finished is not False:
                # Own transaction, after the terminal write — see bump_metric.
                worker_db.run(dbx.bump_metric, "job_failed")
                print(f"[job {job_id}] FAILED: {e}", flush=True)
                _notify_failure(worker_db, job, e)
            else:
                print(f"[job {job_id}] stale failure discarded — execution "
                      f"lease {lease_claim} was superseded", flush=True)
    finally:
        dbx.untrack_job(job_id)


FAIL_NOTES = {
    # agent_turn posts its own apology inside run_agent_job — not repeated.
    "final": ("The final export failed ({err}). Your edit is saved. "
              "Try Download once more; if it repeats, ask me to repair the "
              "current timeline."),
    "index": ("I couldn't analyze that video ({err}). Try uploading it "
              "again, or a different format like mp4."),
    "shorts_plan": ("I couldn't cut shorts from this video ({err}). "
                    "Press Make shorts to try again."),
}

# A preview enqueued by a USER edit (not by the agent — the agent reacts to a
# failed preview inline via the render_preview tool result) has nowhere else
# to surface a failure, so tell the user their edit is safe and offer a retry.
USER_PREVIEW_FAIL_NOTE = (
    "I couldn't render the preview for that edit ({err}). Your change is "
    "saved — hit retry, or make another edit.")
# A FORCED re-render is a playback recovery, not a fresh edit: that version
# already rendered successfully once and the user's browser simply would not
# play the file. Reusing the note above would misdirect them completely —
# "your change is saved, make another edit" points at the edit, when the edit
# was never the problem. Same rule as the index note: never hand the user
# advice that could not have helped.
FORCED_PREVIEW_FAIL_NOTE = (
    "I couldn't rebuild that preview for playback ({err}). Your edit itself is "
    "intact — you can still download it, or try opening the project in another "
    "browser.")


def _notify_failure(worker_db, job, err):
    if job["type"] == "shorts_plan":
        try:
            # The later chat turn is authoritative and runs immediately after
            # this serialized shorts job. Posting the automatic planner's
            # failure beside that editor's answer produced two contradictory
            # assistants in one conversation.
            if worker_db.run(dbx.has_newer_agent_turn, job["project_id"],
                             job["id"]):
                print(f"[notify] suppressing stale shorts failure for job "
                      f"{job['id']} — a newer agent turn owns the reply",
                      flush=True)
                return
        except Exception:
            pass
    note = FAIL_NOTES.get(job["type"])
    if job["type"] == "final" and \
            not failure_policy.decision_for(err, "final").retryable:
        note = ("This edit did not pass the export safety check ({err}). "
                "The timeline needs to be repaired before exporting; pressing "
                "Download again on this same version will not fix it.")
    if "scratch space" in str(err):
        # The full text is operator advice (Cloud Run flags, a deploy doc) —
        # it reached user 387's chat verbatim on Aug 9. Users get the honest
        # shape of the problem and a path; the operator detail stays in
        # video_jobs.error where it belongs.
        note = ("This video is so large it exceeded the render fleet's "
                "staging room. Press Download to try again — it now streams "
                "oversized sources. If it still fails, tell me in chat and "
                "I'll export a lighter version.")
    if job["type"] == "shorts_plan":
        reason = str(err)
        if "shorts need a longer source" in reason:
            note = ("This video is already short-form ({err}). Edit it "
                    "directly here instead of pressing Make shorts again.")
        elif "no transcribed speech" in reason:
            # Retrying cannot grow a transcript — never tell them to press
            # the button again (user 455 did, twice, and got the same wall).
            note = ("This video has no speech, and shorts are cut around "
                    "spoken moments. Edit it directly here instead — ask me "
                    "for a beat-synced montage of its best shots.")
        elif "clip-worthy moments" in reason:
            note = ("I couldn't find self-contained short moments in this "
                    "transcript ({err}). Ask me in chat to build one short "
                    "around the specific idea you want instead.")
    payload = job.get("payload") or {}
    if not note and job["type"] == "preview":
        if payload.get("force"):
            note = FORCED_PREVIEW_FAIL_NOTE
        elif payload.get("source") == "user_edit":
            note = USER_PREVIEW_FAIL_NOTE
    if not note:
        return
    try:
        project = worker_db.run(dbx.get_project, job["project_id"])
        if project and project.get("chat_session_id"):
            worker_db.run(dbx.add_message, project["chat_session_id"],
                          "assistant", note.format(err=str(err)[:160]),
                          {"error": "job_failed", "job": job["id"]})
    except Exception as e2:
        print(f"[notify] {e2}", flush=True)


def lane(name, types, max_attempts, poll_interval=None):
    # NOTHING may escape this loop. worker_db.reset() used to run bare inside
    # the except — if reset itself threw (a torn connection mid-restart), the
    # exception escaped and the THREAD died silently. Every other lane has
    # siblings to cover for it; shorts had exactly one thread, and on Aug 9 a
    # shorts_plan sat claimable for 108 minutes (job 3776: 50s of work,
    # 8090s of queue wait) while the rest of the process worked normally —
    # exactly the shape of a dead lone thread. The obituary print is so a
    # dead lane can never again be invisible in the logs.
    worker_db = dbx.Db()
    try:
        while not DRAINING.is_set():
            try:
                job = worker_db.run(dbx.claim_job, types, max_attempts)
                if job:
                    process_one(worker_db, job)
                    continue
            except Exception as e:
                print(f"[{name}] poll error: {e}", flush=True)
                try:
                    worker_db.reset()
                except Exception as e2:
                    print(f"[{name}] reset failed too ({e2}) — keeping the "
                          "lane alive; next poll reconnects", flush=True)
            # Event.wait wakes immediately when a deploy begins; time.sleep
            # made every polling lane blind to the drain until its timer ended.
            DRAINING.wait(poll_interval or config.POLL_INTERVAL_S)
    finally:
        planned = DRAINING.is_set()
        why = "drained for shutdown" if planned else "UNEXPECTED EXIT"
        print(f"[{name}] lane {why} — jobs of types {types} will not be "
              "claimed by this thread again", flush=True)
        # A dead lane IS a worker failure (the Aug 9 shorts lane sat dead
        # for 108 minutes). Lanes are daemon threads and shutdown is
        # os._exit, so this finally only runs for a genuine in-thread death,
        # never on deploys. Fresh Db — the lane's own may be the casualty.
        if not planned:
            try:
                dbx.Db().run(dbx.bump_metric, "worker_died")
            except Exception:
                pass


REAPER_NOTES = {
    # Never claims "nothing was changed" — the reaper cannot know that, and
    # on Aug 1 it said exactly that over a turn whose edit HAD landed (v266).
    # The graceful drain above it usually gets there first now; this is the
    # hard-death fallback (OOM, kill -9).
    "agent_turn": ("I was interrupted mid-request on our side. Any editing "
                   "steps you can see above are saved — check the preview, "
                   "and tell me to continue or send the request again."),
    "final": ("The final export was interrupted before it finished. "
              "Press Download again to restart it."),
    # An index dying to a dead worker used to say NOTHING — 'index' was in
    # neither this table nor the reaper's "turn and render" framing, and it is
    # the ONE job that runs before the user has any other feedback. A real
    # customer uploaded a 24-min video, waited 88 minutes on a spinner, was
    # never told her analysis had failed, and left. Note this deliberately does
    # NOT reuse FAIL_NOTES["index"] ("try a different format like mp4"): the
    # worker died, her file was never the problem, and sending her off to
    # re-encode it would be a lie about whose fault this was.
    "index": ("Analyzing your video was interrupted on our side and didn't "
              "finish — this wasn't a problem with your file. Please re-open "
              "the project to try again."),
    # Same gap for previews: only the in-process path told a user their edit's
    # preview died. A reaper-failed one left them on 'Rendering…' forever.
    "preview": ("I couldn't finish rendering the preview for that edit — your "
                "change is saved. Hit retry, or make another edit."),
    "shorts_plan": ("Cutting your shorts was interrupted on our side. Any "
                    "clips already on the Shorts board are safe — press "
                    "Make shorts to finish the rest."),
}


_catalog_published = False


def publish_mcp_catalog(worker_db):
    """Publish the MCP tool catalog, and keep trying until it lands.

    Boot alone is not enough. The FIRST deploy of this feature booted before
    its migration had been applied: the table did not exist, the publish failed
    honestly, and the MCP surface then reported itself unavailable for as long
    as the worker happened to stay up — with the schema already fixed and
    nothing left to do but restart a healthy process. Order-of-operations
    between a deploy and a migration is not something to get right by hand, so
    the retry lives here (in the reaper, the janitor thread) and costs one
    UPSERT, once, the first time it can succeed."""
    global _catalog_published
    if _catalog_published:
        return
    try:
        worker_db.run(dbx.publish_mcp_catalog, mcp_exec.catalog())
        _catalog_published = True
        print("[mcp] published tool catalog", flush=True)
    except Exception as e:
        print(f"[mcp] could not publish tool catalog ({e}) — the MCP surface "
              "reports itself unavailable until this succeeds; retrying",
              flush=True)
        worker_db.reset()


def reaper():
    """Every job must terminate VISIBLY: when a stale job's retries are
    exhausted, tell the user in chat instead of leaving the UI on 'Editing…'
    (or 'Analyzing…') forever."""
    worker_db = dbx.Db()
    while True:
        time.sleep(60)
        publish_mcp_catalog(worker_db)
        try:
            # Two ways a job runs out of road, and BOTH have to end visibly.
            # fail_exhausted_jobs is the refundable budget (attempts); the
            # second is the non-refundable one (total_claims), which exists
            # precisely because deploys keep refunding the first. A job the
            # queue has stopped selecting is invisible, not finished — left
            # alone it sits `queued` forever under a spinner.
            exhausted = worker_db.run(dbx.fail_exhausted_jobs) or []
            ceilinged = worker_db.run(dbx.fail_ceilinged_jobs) or []
            rows = exhausted + ceilinged
            # Reliability counters (migration 017). Every exhausted row is a
            # job a dead worker was holding when its heartbeat went stale —
            # the closest observable thing to "a worker died" from the DB
            # side. Both lists are terminal failures.
            if exhausted:
                worker_db.run(dbx.bump_metric, "worker_died", len(exhausted))
            if rows:
                worker_db.run(dbx.bump_metric, "job_failed", len(rows))
            for row in rows:
                print(f"[reaper] failed exhausted job {row['id']} "
                      f"({row['type']})", flush=True)
                # Round 97 (#1): an agent turn killed by a worker death gets
                # ONE fresh pass over the same message instead of "I was
                # interrupted — send the request again". The EDL writes it
                # landed are already saved, so the resume behaves exactly
                # like the user resending — which is what they were being
                # asked to do by hand (job 2932: the one trial customer's
                # flagship request died at 20:15 and he left). The
                # death_resume marker bounds it: a request that kills the
                # worker twice is a poison pill and stops with the honest
                # note.
                if row["type"] == "agent_turn" \
                        and int((row.get("payload") or {}).get(
                            "death_resume_count") or 0) < 3 \
                        and row.get("user_id") is not None:
                    try:
                        prior_payload = dict(row.get("payload") or {})
                        count = int(prior_payload.get(
                            "death_resume_count") or 0) + 1
                        root_id = int(prior_payload.get(
                            "root_agent_job_id") or row["id"])
                        sequence = int(prior_payload.get(
                            "continuation_sequence") or 0) + 1
                        resume_payload = dict(
                            prior_payload, death_resume=count,
                            death_resume_count=count,
                            root_agent_job_id=root_id,
                            logical_turn_continuation=True,
                            continuation_sequence=sequence)
                        nid = worker_db.run(
                            dbx.enqueue_agent_continuation,
                            row["project_id"], row["user_id"], root_id,
                            sequence, resume_payload)
                        print(f"[reaper] agent turn {row['id']} died "
                              f"mid-request — durable resume {count}/3 as "
                              f"job {nid}",
                              flush=True)
                        continue
                    except Exception as e:
                        print(f"[reaper] could not resume agent turn "
                              f"{row['id']}: {e}", flush=True)
                note = REAPER_NOTES.get(row["type"])
                # Same distinction as _notify_failure: a died forced re-render
                # is a playback recovery, and the generic preview note would
                # send the user off to re-edit something that was never wrong.
                if row["type"] == "preview" and (row.get("payload") or {}).get("force"):
                    note = FORCED_PREVIEW_FAIL_NOTE.format(
                        err="the render was interrupted on our side")
                if not note:
                    continue
                try:
                    project = worker_db.run(dbx.get_project, row["project_id"])
                    if project and project.get("chat_session_id"):
                        worker_db.run(dbx.add_message,
                                      project["chat_session_id"],
                                      "assistant", note,
                                      {"error": "job_died", "job": row["id"]})
                except Exception as e:
                    print(f"[reaper] notify failed: {e}", flush=True)
        except Exception as e:
            print(f"[reaper] {e}", flush=True)
            worker_db.reset()
        # A tray nobody submitted is a dead studio, not a finished job — and
        # it is invisible to everything above, because no job was ever
        # created to reap. Same contract as the reaper's: a session that
        # cannot end well must not be left to sit forever. Its own try so a
        # sweep failure can never cost the queue its reaper.
        try:
            for pid, _uid, aid, jid in (
                    worker_db.run(dbx.rescue_abandoned_trays) or []):
                print(f"[reaper] tray on project {pid} was never submitted — "
                      f"promoted asset {aid} to main footage and queued "
                      f"index job {jid}", flush=True)
        except Exception as e:
            print(f"[reaper] tray rescue: {e}", flush=True)
            worker_db.reset()

        # Terminal charging/qualification uses savepoints so a transient SQL
        # failure never erases a completed edit. The failure is durable in the
        # job result; repair the accounting itself here, without rerunning a
        # model or touching the EDL.
        try:
            for repair in (
                    worker_db.run(dbx.reconcile_pending_accounting) or []):
                print(f"[reaper] repaired terminal accounting for job "
                      f"{repair['job_id']}: "
                      f"{','.join(repair['fixed'])}", flush=True)
        except Exception as e:
            print(f"[reaper] terminal accounting repair: {e}", flush=True)
            worker_db.reset()


def remote_guardian():
    """Reconnect durable calls whose original dispatcher may be gone."""
    worker_db = dbx.Db()
    while not DRAINING.is_set():
        try:
            rows = worker_db.run(dbx.active_remote_executions) or []
            for row in rows:
                event = remote.reconcile_remote_execution(worker_db, row)
                status = event.get("status")
                if status in {"failed", "completion_pending"}:
                    print(f"[remote-guardian] job {row['job_id']} "
                          f"provider={row['provider']} status={status}",
                          flush=True)
                if status == "failed":
                    try:
                        worker_db.run(dbx.bump_metric, "job_failed")
                        _notify_failure(worker_db, event["job"],
                                        event["error"])
                    except Exception as notify_exc:
                        print(f"[remote-guardian] failure notification "
                              f"failed: {notify_exc}", flush=True)
        except Exception as exc:
            print(f"[remote-guardian] {str(exc)[:240]}", flush=True)
            try:
                worker_db.reset()
            except Exception:
                pass
        DRAINING.wait(config.REMOTE_GUARDIAN_INTERVAL_S)


def _sweep_tmp():
    """Delete work directories left by a previous process.

    Every job cleans its own workdir in a finally — but a finally does not run
    when the kernel SIGKILLs the process (OOM) or Render replaces the container
    mid-job. Those workdirs hold the downloaded ORIGINAL: gigabytes each, for
    jobs that are already dead, that nothing else ever deletes. Safe to do here
    and only here — this process has just booted, so it owns none of them.
    """
    freed = 0
    try:
        entries = os.listdir(config.TMP_DIR)
    except OSError:
        return
    for name in entries:
        path = os.path.join(config.TMP_DIR, name)
        try:
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        freed += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass
    if freed:
        print(f"[startup] swept {freed / 1e9:.2f}GB of work dirs orphaned by a "
              "previous process", flush=True)


def _on_shutdown(signum, _frame):
    """Render SIGTERMs us before every deploy/restart. Give back whatever we
    were holding so the next container picks it up instead of the job rotting
    through its retry budget for a death it did not cause. See db.release_jobs.

    Agent turns can't be given back (a replay re-applies EDL writes), so
    before round 71f they simply DIED here — every deploy shot whatever turn
    was running and the user read "I lost my connection", once over an edit
    that had actually landed. Now the loop drains: SHUTDOWN tells every
    running turn to finalize honestly between steps, and exit waits for them
    (bounded well inside Render's grace period).
    """
    DRAINING.set()
    agent_loop.SHUTDOWN.set()
    # This snapshot and the provider's pre-spawn reservation share one lock.
    # A job is therefore either released for the replacement worker or kept
    # remote-owned while this process waits for its accepted call id — never
    # released and launched at the same time.
    ids, remote_ids = dbx.begin_remote_shutdown()
    try:
        n = dbx.Db().run(dbx.release_jobs, ids)
        print(f"[shutdown] signal {signum}: handed {n} of {len(ids)} in-flight "
              f"locally-owned job(s) back to the queue; preserving "
              f"{len(remote_ids)} durable remote job(s)", flush=True)
    except Exception as e:
        # Best effort — if we can't reach the DB the reaper still cleans up,
        # just the slower, attempt-charging way.
        print(f"[shutdown] could not release jobs: {e}", flush=True)
    deadline = time.time() + config.SHUTDOWN_GRACE_S
    while time.time() < deadline and (
            dbx.locally_owned_job_ids()
            or dbx.remote_launching_job_ids()):
        time.sleep(0.5)
    left = len(dbx.locally_owned_job_ids())
    launching = len(dbx.remote_launching_job_ids())
    if left:
        print(f"[shutdown] {left} local job(s) still running at the wire — the "
              "reaper will surface them", flush=True)
    if launching:
        print(f"[shutdown] {launching} provider submission(s) remained "
              "ambiguous at the wire — their durable ownership deadline "
              "will bound recovery", flush=True)
    os._exit(0)


def main():
    config.require_core()
    os.makedirs(config.TMP_DIR, exist_ok=True)
    _sweep_tmp()
    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)
    slots = config.worker_lane_slots()
    exec_mode = ((f"modal {config.MODAL_EXECUTOR_PERCENT}%" +
                  (" + retired-executor rollback"
                   if (config.MODAL_CLOUD_RUN_FALLBACK and
                       config.REMOTE_EXECUTOR_URL) else ""))
                 if config.MODAL_EXECUTOR_ENABLED else
                 ("remote executor " + config.REMOTE_EXECUTOR_URL
                  if config.REMOTE_EXECUTOR_URL else "local"))
    print(f"valmera-worker ({config.WORKER_ROLE}) starting: "
          f"code={version.code_version()} media_slots={slots['media']} "
          f"filmstrip_slots={slots['filmstrip']} "
          f"index_slots={slots['index']} agent_slots={slots['agent']} "
          f"shorts_slots={slots['shorts']} mcp_slots={slots['mcp']} "
          f"media/index={exec_mode} whisper={config.WHISPER_MODEL}/"
          f"{config.WHISPER_DEVICE} agent_model={config.AGENT_MODEL} "
          f"vision={config.VISION_MODEL or 'off'}"
          f"@{config.VISION_BASE_URL if config.VISION_API_KEY else 'NO KEY'}",
          flush=True)
    # Publish the MCP tool catalog for the backend to serve on tools/list.
    # Best effort by design — a missing table (migration not applied yet) must
    # never stop the worker from booting — and retried by the reaper until it
    # lands, so applying the migration after the deploy still ends up correct.
    publish_mcp_catalog(dbx.Db())

    # The dispatcher launches the egress probe but never performs it. Render's
    # IP and 512-MiB process are control-plane resources; the verdict must
    # describe the same Modal egress users actually receive.
    def _egress_probe():
        if config.YTDLP_BOOT_PROBE != "1":
            return
        try:
            verdict = (remote.run_probe_remote()
                       if config.MODAL_EXECUTOR_ENABLED
                       else ytaccess.boot_probe())
            if isinstance(verdict, dict):
                print(f"[ytaccess] remote probe ok={verdict.get('ok')} "
                      f"download_ok={verdict.get('download_ok')}", flush=True)
        except Exception as exc:
            print(f"[ytaccess] remote probe failed: {str(exc)[:200]}",
                  flush=True)

    threading.Thread(target=_egress_probe, daemon=True,
                     name="ytdlp-probe-launcher").start()

    if config.REMOTE_EXECUTOR_URL and not config.REMOTE_EXECUTOR_SECRET:
        print("[dispatcher] WARNING: REMOTE_EXECUTOR_URL set but "
              "REMOTE_EXECUTOR_SECRET is empty — calls will be unauthenticated.",
              flush=True)

    if config.REMOTE_EXECUTOR_URL or config.MODAL_EXECUTOR_ENABLED:
        # Say, on every boot, whether the service that actually makes the
        # pixels is running this code. A push deploys the dispatcher
        # automatically and the executor not at all, so the moment a deploy is
        # most likely to be half-done is exactly here. Threaded because it is a
        # cold-start HTTP call to a scale-to-zero service and a diagnostic must
        # not delay the lanes; best effort because it must never stop a boot.
        def _probe():
            try:
                remote.check_executor_version()
            except Exception as e:
                print(f"[dispatcher] version probe error: {str(e)[:200]}",
                      flush=True)

        threading.Thread(target=_probe, daemon=True,
                         name="version-probe").start()

    if config.REMOTE_AGENT_EXECUTOR_URL or (
            config.MODAL_EXECUTOR_ENABLED
            and "agent_turn" in config.MODAL_EXECUTOR_TYPES):
        def _agent_probe():
            try:
                remote.check_agent_executor_version()
            except Exception as e:
                print(f"[dispatcher] agent executor version probe error: "
                      f"{str(e)[:200]}", flush=True)

        threading.Thread(target=_agent_probe, daemon=True,
                         name="agent-version-probe").start()

    threads = [
        threading.Thread(
            target=_supervise,
            args=("remote-guardian", remote_guardian), daemon=True,
            name="remote-guardian"),
        threading.Thread(
            target=_supervise,
            args=("heartbeat", dbx.heartbeat_forever), daemon=True,
            name="heartbeat"),
        threading.Thread(
            target=_supervise, args=("reaper", reaper), daemon=True,
            name="reaper"),
    ]
    for i in range(slots["media"]):
        lane_name = f"media{i}"
        threads.append(threading.Thread(
            target=_supervise,
            args=(lane_name, lane, (lane_name, MEDIA_TYPES,
                                    config.MAX_ATTEMPTS_MEDIA,
                                    config.MEDIA_POLL_INTERVAL_S)),
            daemon=True, name=f"media{i}"))
    if slots["filmstrip"] and FILMSTRIP_TYPES:
        threads.append(threading.Thread(
            target=_supervise,
            args=("filmstrip", lane, ("filmstrip", FILMSTRIP_TYPES,
                                       config.MAX_ATTEMPTS_MEDIA)),
            daemon=True, name="filmstrip"))
    for i in range(slots["index"]):
        lane_name = f"index{i}"
        threads.append(threading.Thread(
            target=_supervise,
            args=(lane_name, lane, (lane_name, INDEX_TYPES,
                                    config.MAX_ATTEMPTS_MEDIA,
                                    config.MEDIA_POLL_INTERVAL_S)),
            daemon=True, name=f"index{i}"))
    for i in range(slots["agent"]):
        lane_name = f"agent{i}"
        threads.append(threading.Thread(
            target=_supervise,
            args=(lane_name, lane, (lane_name, AGENT_TYPES,
                                    config.MAX_ATTEMPTS_AGENT,
                                    config.AGENT_POLL_INTERVAL_S)),
            daemon=True, name=f"agent{i}"))
    # TWO shorts threads, deliberately: this was the only lane with a single
    # thread, so one silent thread death (Aug 9) left shorts_plan jobs
    # unclaimable for 108 minutes while every other lane worked. Per-project
    # claim serialization already prevents the pair double-running one plan.
    for i in range(slots["shorts"]):
        lane_name = f"shorts{i}"
        threads.append(threading.Thread(
            target=_supervise,
            args=(lane_name, lane, (lane_name, SHORTS_TYPES,
                                    config.MAX_ATTEMPTS_MEDIA,
                                    config.AGENT_POLL_INTERVAL_S)),
            daemon=True, name=f"shorts{i}"))
    for i in range(slots["mcp"]):
        lane_name = f"mcp{i}"
        threads.append(threading.Thread(
            target=_supervise,
            args=(lane_name, lane, (lane_name, MCP_TYPES,
                                    config.MAX_ATTEMPTS_MCP,
                                    config.MCP_POLL_INTERVAL_S)),
            daemon=True, name=f"mcp{i}"))
    for t in threads:
        t.start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    # One image, two roles. The executor is the stateless compute endpoint
    # (Cloud Run); the default "worker" is the always-on dispatcher.
    if config.WORKER_ROLE in ("executor", "agent_executor", "mcp_executor",
                              "shorts_executor"):
        import http_server
        http_server.serve()
    else:
        main()
