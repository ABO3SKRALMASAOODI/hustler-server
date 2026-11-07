"""A request a gate refused runs once the gate's reason is gone (P0 #861).

Account 2869 (7 Nov 2026) typed a request while its 2 h 39 min upload was
analysed. When the index finished, the trial gate refused it: analysis had
wrongly used up the account's trial allowance. 2c288ef9 made the allowance
whole again, but nothing re-ran the refused request, so the customer stayed
on the plan cards until they happened to type it again. Every refusal of a
saved request says it waits ("send it again once the plan is active", "your
request picks up right here"); this is what makes that true for everyone.

A refusal is an assistant reply that shows the wall (credits_exhausted,
subscribe_required or subscribe_gate) and names the request it refused
(``request_message_id``: the trial's stop before or during a turn, and the
index's auto-resume gate). An index gate written before that key existed
(2869's) names it by the request it marked ``subscribe_gated``.

The reaper calls ``sweep`` about once a minute. A refusal runs when, in one
transaction under the account's row lock and the reply's row lock:
  * it is from the last WINDOW_H hours and was never lifted;
  * its request is still the conversation's newest message from the
    customer (anything typed after it means they moved on) and no turn ran
    that request after the refusal (the Studio's own replay, a resend);
  * no turn is queued or running in the project (it is retried next sweep);
  * ``db.saved_request_admission`` admits it now: the same answer the
    index's auto-resume gives (a subscriber with credits, or the account's
    trial project with trial requests on and trial budget left). The
    dispatcher holds no Claude key, so the turn judges the lane, as on the
    backend's send path; a request is lifted at most once per lane, so a
    closed lane costs one refused turn, never a loop.
Then the request is enqueued as an ordinary agent turn and the reply is
stamped ``gate_lifted_at`` in the same transaction, so it runs exactly once
however many workers sweep, and its plan cards are retired: the flags the
Studio draws them from are kept under ``superseded_card`` and turned off,
and the resumed turn's own reply follows in the chat.

Nothing reads the customer's words; no credits are granted and no budget is
changed. A gate that still holds leaves the refusal exactly as it was.
"""
import os
import random
import time

from psycopg2.extras import Json

import db as dbx

WINDOW_H = max(1, int(os.getenv("GATE_LIFT_WINDOW_H", "24")))
SWEEP_EVERY_S = max(30, int(os.getenv("GATE_LIFT_SWEEP_EVERY_S", "60")))
MAX_PER_SWEEP = 20
# One sweeper at a time across every worker (pg_try_advisory_xact_lock).
LOCK_KEY = (861, 4401)
CARD_FLAGS = ("credits_exhausted", "subscribe_required", "subscribe_gate")

# Refusals still waiting, with the request each one refused and the cheap
# reasons it cannot run (moved on, already ran, project busy) already ruled
# out. The admission and the locks come per candidate, in resume_one.
CANDIDATES_SQL = """
WITH refusals AS (
    SELECT r.id AS reply_id, r.session_id, r.created_at,
           COALESCE(
             CASE WHEN r.meta->>'request_message_id' ~ '^[0-9]+$'
                  THEN (r.meta->>'request_message_id')::bigint END,
             CASE WHEN r.meta->>'kind' IN ('index_ready', 'direct_short')
                  THEN (SELECT max(m.id) FROM chat_messages m
                         WHERE m.session_id = r.session_id
                           AND m.role = 'user' AND m.id < r.id
                           AND m.meta->>'subscribe_gated' = 'true') END
           ) AS message_id
      FROM chat_messages r
     WHERE r.created_at > NOW() - make_interval(hours => %s)
       AND r.role = 'assistant'
       AND NOT (COALESCE(r.meta, '{}'::jsonb) ? 'gate_lifted_at')
       AND (r.meta->>'credits_exhausted' = 'true'
            OR r.meta->>'subscribe_required' = 'true'
            OR r.meta->>'subscribe_gate' = 'true')
       AND (r.meta ? 'request_message_id'
            OR r.meta->>'kind' IN ('index_ready', 'direct_short'))
)
SELECT f.reply_id, f.session_id, f.message_id, p.id AS project_id,
       p.user_id
  FROM refusals f
  JOIN projects p ON p.chat_session_id = f.session_id
 WHERE f.message_id IS NOT NULL
   AND p.user_id IS NOT NULL
   AND EXISTS (SELECT 1 FROM chat_messages m
                WHERE m.id = f.message_id AND m.session_id = f.session_id
                  AND m.role = 'user')
   AND NOT EXISTS (SELECT 1 FROM chat_messages n
                    WHERE n.session_id = f.session_id AND n.role = 'user'
                      AND n.id > f.message_id)
   AND NOT EXISTS (SELECT 1 FROM video_jobs j
                    WHERE j.project_id = p.id AND j.type = 'agent_turn'
                      AND j.created_at > f.created_at
                      AND j.payload->>'message_id' ~ '^[0-9]+$'
                      AND (j.payload->>'message_id')::bigint >= f.message_id)
   AND NOT EXISTS (SELECT 1 FROM video_jobs j
                    WHERE j.project_id = p.id
                      AND j.type IN ('agent_turn', 'mcp_tool')
                      AND j.state IN ('queued', 'running'))
 ORDER BY f.reply_id
 LIMIT %s
"""

_next_at = [0.0]


def candidates(conn, window_h=WINDOW_H, limit=MAX_PER_SWEEP):
    with conn.cursor() as cur:
        cur.execute(CANDIDATES_SQL, (int(window_h), int(limit)))
        return [dict(r) for r in cur.fetchall()]


def resume_one(conn, cand):
    """Run one refused request if its gate no longer holds. Returns
    {"state": "enqueued", "job_id"} or {"state": <why not>}. Must run inside
    the caller's transaction (the locks and the stamp commit with the job)."""
    user_id, project_id = int(cand["user_id"]), int(cand["project_id"])
    reply_id, message_id = int(cand["reply_id"]), int(cand["message_id"])
    # Lock order is account, then message (db.resolve_pending_auto_resume).
    gated = dbx.subscribe_gate_applies(conn, user_id)
    with conn.cursor() as cur:
        cur.execute("""SELECT id FROM chat_messages
                        WHERE id = %s
                          AND NOT (COALESCE(meta, '{}'::jsonb)
                                   ? 'gate_lifted_at')
                        FOR UPDATE""", (reply_id,))
        if not cur.fetchone():
            return {"state": "already_lifted"}
        # Re-read under the lock: a turn may have been enqueued (a resend,
        # the Studio's replay) between the candidate scan and this lock.
        cur.execute("""SELECT 1 FROM video_jobs
                        WHERE project_id = %s AND type = 'agent_turn'
                          AND (state IN ('queued', 'running')
                               OR (payload->>'message_id' ~ '^[0-9]+$'
                                   AND (payload->>'message_id')::bigint
                                       >= %s
                                   AND created_at > (SELECT created_at
                                                       FROM chat_messages
                                                      WHERE id = %s)))
                        LIMIT 1""", (project_id, message_id, reply_id))
        if cur.fetchone():
            return {"state": "busy_or_ran"}
    state, lane = dbx.saved_request_admission(conn, project_id, user_id,
                                              gated, lane_here=False)
    if state != "enqueue":
        return {"state": state}
    # One lift per request and lane: a turn that meets a closed lane answers
    # with a new refusal naming the same request, which must not start the
    # same turn again every minute. A later payment (another lane) may.
    with conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM video_jobs
                        WHERE project_id = %s AND type = 'agent_turn'
                          AND payload->>'gate_lifted' = 'true'
                          AND payload->>'message_id' = %s
                          AND COALESCE(payload->>'trial_lane', 'false') = %s
                        LIMIT 1""",
                    (project_id, str(message_id),
                     "true" if lane.get("trial_lane") else "false"))
        if cur.fetchone():
            return {"state": "lifted_before"}
    payload = {"message_id": message_id, "auto_resumed": True,
               "gate_lifted": True, "gate_reply_id": reply_id}
    payload.update(lane)
    job_id = dbx.enqueue_job(conn, project_id, user_id, "agent_turn", payload)
    with conn.cursor() as cur:
        cur.execute("""UPDATE chat_messages
                          SET meta = COALESCE(meta, '{}'::jsonb) || %s
                                     || jsonb_build_object(
                                          'superseded_card',
                                          jsonb_build_object(
                                            'credits_exhausted',
                                            meta->'credits_exhausted',
                                            'subscribe_required',
                                            meta->'subscribe_required',
                                            'subscribe_gate',
                                            meta->'subscribe_gate'))
                        WHERE id = %s""",
                    (Json({"gate_lifted_at": time.strftime(
                              "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "gate_lifted_job_id": int(job_id),
                           "credits_exhausted": False,
                           "subscribe_required": False,
                           "subscribe_gate": False}), reply_id))
    return {"state": "enqueued", "job_id": job_id,
            "trial": bool(lane.get("trial_lane"))}


def sweep_once(conn, window_h=WINDOW_H):
    """One sweep in one transaction: [(candidate, outcome), ...]. Each
    candidate runs under its own savepoint, so one failure never blocks the
    others. Another worker sweeping right now means this one skips."""
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s, %s) AS ok",
                    LOCK_KEY)
        row = cur.fetchone()
        if not (row and (row.get("ok") if isinstance(row, dict) else row[0])):
            return []
    out = []
    for cand in candidates(conn, window_h):
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT gate_lift")
        try:
            result = resume_one(conn, cand)
            with conn.cursor() as cur:
                cur.execute("RELEASE SAVEPOINT gate_lift")
        except Exception as e:
            with conn.cursor() as cur:
                cur.execute("ROLLBACK TO SAVEPOINT gate_lift")
            result = {"state": "error", "error": f"{type(e).__name__}: {e}"}
        if result.get("state") == "enqueued":
            try:
                with conn.cursor() as cur:
                    cur.execute("SAVEPOINT gate_lift_event")
                dbx.record_client_event(
                    conn, cand["user_id"], cand["project_id"],
                    "blocked_prompt_replayed",
                    {"surface": "gate_lift", "via": "gate_lift",
                     "job_id": result["job_id"],
                     "reply_id": int(cand["reply_id"]),
                     "message_id": int(cand["message_id"]),
                     "trial_lane": result.get("trial", False)})
                with conn.cursor() as cur:
                    cur.execute("RELEASE SAVEPOINT gate_lift_event")
            except Exception:
                with conn.cursor() as cur:
                    cur.execute("ROLLBACK TO SAVEPOINT gate_lift_event")
        out.append((cand, result))
    return out


def sweep(worker_db, now=None):
    """The reaper's entry point: at most one sweep per SWEEP_EVERY_S in this
    process (jittered so the fleet spreads out). Never raises."""
    now = time.monotonic() if now is None else now
    if now < _next_at[0]:
        return None
    _next_at[0] = now + SWEEP_EVERY_S * random.uniform(0.8, 1.2)
    try:
        out = worker_db.run(sweep_once)
    except Exception as e:
        print(f"[gate_lift] sweep failed: {type(e).__name__}: {e}",
              flush=True)
        return None
    for cand, result in out or []:
        if result.get("state") in ("enqueued", "error"):
            print(f"[gate_lift] project {cand['project_id']} request "
                  f"{cand['message_id']} (refused in {cand['reply_id']}): "
                  f"{result}", flush=True)
    return out
