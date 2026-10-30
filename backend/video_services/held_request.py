"""A request the subscribe wall held runs once the payment is confirmed.

Ticket #11 (30 Oct 2026): a new user typed "a 20 s 9:16 website demo" into
the Studio twice, got the plans reply both times (the wall, by design), paid,
and nothing followed. The refused request was kept on the plans reply
(``meta.blocked_prompt``) but only ONE thing could ever run it: the Studio tab
that came back through /purchase-success with a sessionStorage marker and a
plan that includes the Studio. Every other way back (the webhook landing
while the user was elsewhere, another tab or device, a fresh /studio, the
grant arriving a few seconds after the Studio asked) and every MCP-only plan
(4 of 4 payers after a held request in 30 days bought Plus) dropped it.

The paid state is the webhook's, never the browser's: ``kick_after_payment``
runs from the Paddle webhook once a real-money transaction is recorded.
``resume`` is also what /replay-blocked's "already started?" answer reads, so
whichever arrives first (the webhook or the Studio) runs it, and the other
finds it running.

Exactly once:
  * a request that is already a chat message (a blank-canvas / website
    request) is enqueued under the project's advisory lock in the same
    transaction that stamps ``replayed_at`` on its plans reply, only while
    that reply has no ``replayed_at``;
  * a typed request the wall returned to the composer goes through the
    ordinary send path (routes.video.send_as_user: every gate, the index
    resume, the stacking rules) with client_msg_id ``replay-<plans reply
    id>``, the same id the Studio's own replay sends, and the unique index on
    (session_id, client_msg_id) lets exactly one of them insert.

Which lane runs it is ``routes.video._studio_ai_gate``'s answer, unchanged:
a plan without the Studio AI editor (MCP Connect, Plus while owner decision
D1 is held on main) never gets a Studio turn from here. Its buyer's own
assistant is shown the request instead (``for_assistant``, read by the MCP
project tools) until that assistant starts working. D1 (Studio in Plus, on
the trial release) widens _studio_ai_gate for new Plus subscribers while the
owner's switch and the Claude credit line are on, so such a lane can open
AFTER the hand-off: the assistant keeps the note until the Studio runs the
request, and once the assistant has started on it the Studio never runs it
(``assistant_took_over``, D1V). A held kind this module does not know (the trial branch's
``export_version``) is left to the Studio's /replay-blocked.

Nothing here changes when or where the wall shows, its plans or its copy.
"""
import os
import threading
from datetime import datetime, timezone

from psycopg2.extras import Json

# How long after the wall a confirmed payment still runs the request without
# the user asking again. A request from the day before is still what a new
# subscriber paid for; anything older would surprise them.
WINDOW_H = max(1, int(os.getenv("HELD_REQUEST_WINDOW_H", "24")))

# The plans reply's meta keys this module writes (beside the Studio's own
# ``replayed_at`` / ``replay_job_id`` written by /replay-blocked).
REPLAY_KEYS = ("replayed_at", "replay_job_id", "replay_message_id",
               "replay_origin")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _kind(meta):
    """'message' (already a chat message), 'text' (returned to the composer)
    or None (nothing this module runs)."""
    blocked = (meta or {}).get("blocked_prompt") or {}
    if blocked.get("message_id") and (meta or {}).get("surface") == "blank_canvas":
        return "message"
    if str(blocked.get("text") or "").strip():
        return "text"
    return None


def _mcp_only_plan(cur, user_id):
    """The account is on a plan that is MCP-only today (routes.video
    .MCP_ONLY_PLANS: MCP Connect, Plus): a held request of such a plan is
    shown to the customer's own assistant.

    D1 (Studio in Plus, 30 Oct 2026): a NEW Plus subscriber's Studio lane
    opens and closes with the owner's switch and the Claude credit line, so
    it can open AFTER the request was handed to the assistant (the payment
    landed while it was off). The hand-off therefore follows the plan, not
    the lane at this instant (D1V)."""
    from routes import video as v
    cur.execute("SELECT plan FROM users WHERE id = %s", (int(user_id),))
    row = cur.fetchone() or {}
    return row.get("plan") in v.MCP_ONLY_PLANS


def _assistant_worked_since(cur, user_id, since):
    """Any MCP call other than a state read since ``since``: the customer's
    own assistant started working (for_assistant's rule)."""
    cur.execute("""SELECT 1 FROM video_jobs
                    WHERE user_id = %s AND type = 'mcp_tool'
                      AND created_at > %s
                      AND COALESCE(payload->>'tool', '') <> '__state__'
                    LIMIT 1""", (int(user_id), since))
    return cur.fetchone() is not None


# Stamped on the plans reply when open_project / project_state print the
# waiting request to the customer's assistant (routes.mcp._held_for_assistant).
SHOWN_KEY = "shown_to_assistant_at"


def mark_shown(cur, plans_id):
    """The customer's assistant was just shown the waiting request (its text,
    printed with the project's state): from now on it is the assistant's
    (assistant_took_over). Once only; a pure bookkeeping key nothing else
    reads, so ticket #11's behaviour is otherwise unchanged."""
    cur.execute("""UPDATE chat_messages
                      SET meta = COALESCE(meta, '{}'::jsonb) || %s
                    WHERE id = %s AND NOT (COALESCE(meta, '{}'::jsonb)
                                           ? 'shown_to_assistant_at')""",
                (Json({SHOWN_KEY: _now()}), int(plans_id)))


def assistant_took_over(cur, user_id, since, meta=None):
    """The held request (walled at ``since``; ``meta`` is its plans reply's)
    is the customer's assistant's: the assistant was shown it (mark_shown)
    or started working (any MCP call other than a state read since the
    wall). The Studio must then never run it as well.

    Only an MCP-only plan's request is ever handed to the assistant, so every
    other plan answers False (ticket #11's behaviour, unchanged). D1V: with
    Studio in Plus switched on (or the credit line back) after a payment
    that landed while it was off, the Studio (/replay-blocked on the next
    project open) and a webhook retry otherwise ran a request the assistant
    had already made or been asked to make: twice, and charged twice."""
    if not _mcp_only_plan(cur, user_id):
        return False
    if (meta or {}).get(SHOWN_KEY):
        return True
    return _assistant_worked_since(cur, user_id, since)


def studio_lane_open(cur, user_id):
    """Whether this account's plan includes the Studio AI editor: exactly
    routes.video._studio_ai_gate's answer, through its unrecorded form (this
    is a lookup, not a refusal the customer saw, so it must never add an
    mcp_only_refused event). A refusal is a jsonify response, so an app
    context is pushed when the caller has none (the intake's unit tests)."""
    from flask import Flask, has_app_context
    from routes import video as v
    gate = getattr(v, "_studio_ai_gate_unrecorded", v._studio_ai_gate)
    if has_app_context():
        return not gate(cur, user_id)
    with Flask(__name__).app_context():
        return not gate(cur, user_id)


def pending(cur, user_id, window_h=WINDOW_H, project_id=None):
    """The account's newest held request that is still waiting, or None.

    A plans reply that carries the refused request, was written within the
    window, has not been resumed, and has no user message after it (anything
    typed after it means the user moved on, and a resumed request's own
    message is such a message). Automatic assistant messages after it, such
    as the analysis greeting of the walled upload that the subscription
    unlocked, do not count: users 2544, 2555 and 2589 (25-27 Oct) each had
    one, and their held requests were dropped too."""
    cur.execute("""
        SELECT m.id, m.session_id, m.meta, m.created_at, p.id AS project_id
          FROM projects p
          JOIN chat_messages m ON m.session_id = p.chat_session_id
         WHERE p.user_id = %s
           AND (%s::bigint IS NULL OR p.id = %s)
           AND m.role = 'assistant'
           AND m.meta->>'kind' = 'subscription_plans'
           AND m.meta ? 'blocked_prompt'
           AND NOT (m.meta ? 'replayed_at')
           AND m.created_at > NOW() - make_interval(hours => %s)
           AND NOT EXISTS (
                 SELECT 1 FROM chat_messages n
                  WHERE n.session_id = m.session_id AND n.id > m.id
                    AND n.role = 'user')
         ORDER BY m.id DESC LIMIT 1""",
                (int(user_id), project_id, project_id, int(window_h)))
    row = cur.fetchone()
    if not row or not _kind(row.get("meta")):
        return None
    return dict(row)


def started(cur, session_id, window_h=WINDOW_H):
    """The resumed request of this conversation while its turn is still
    queued or running: {job_id, message_id, origin, state}, else None.

    Read by /replay-blocked so a Studio that comes back after the webhook ran
    the request shows it starting instead of asking again."""
    cur.execute("""
        SELECT id, meta FROM chat_messages
         WHERE session_id = %s AND role = 'assistant'
           AND meta->>'kind' = 'subscription_plans'
           AND created_at > NOW() - make_interval(hours => %s)
         ORDER BY id DESC LIMIT 1""", (session_id, int(window_h)))
    wall = cur.fetchone()
    if not wall:
        return None
    meta = wall.get("meta") or {}
    message_id = meta.get("replay_message_id")
    if message_id is None:
        if meta.get("replayed_at"):
            message_id = (meta.get("blocked_prompt") or {}).get("message_id")
        else:
            # The Studio's own replay sent it (client_msg_id replay-<id>).
            cur.execute("""SELECT id FROM chat_messages
                            WHERE session_id = %s AND role = 'user'
                              AND meta->>'client_msg_id' = %s""",
                        (session_id, f"replay-{wall['id']}"))
            sent = cur.fetchone()
            message_id = sent["id"] if sent else None
    if message_id is None:
        return None
    cur.execute("""SELECT id, state FROM video_jobs
                    WHERE type = 'agent_turn'
                      AND project_id = (SELECT id FROM projects
                                         WHERE chat_session_id = %s LIMIT 1)
                      AND (payload->>'message_id')::bigint = %s
                    ORDER BY id DESC LIMIT 1""",
                (session_id, int(message_id)))
    job = cur.fetchone()
    waiting_for_index = False
    if not job:
        # An upload the wall locked analyses first; the message runs when
        # the analysis lands (the index job's durable auto-resume).
        cur.execute("""SELECT id, state FROM video_jobs
                        WHERE type = 'index'
                          AND project_id = (SELECT id FROM projects
                                             WHERE chat_session_id = %s
                                             LIMIT 1)
                          AND state IN ('queued', 'running')
                        ORDER BY id DESC LIMIT 1""", (session_id,))
        job = cur.fetchone()
        waiting_for_index = bool(job)
    if not job or job.get("state") not in ("queued", "running"):
        return None
    return {"job_id": job["id"], "message_id": int(message_id),
            "origin": meta.get("replay_origin") or "studio",
            "state": job["state"], "waiting_for_index": waiting_for_index}


def _mark(cur, plans_id, origin, job_id=None, message_id=None):
    """Stamp the plans reply resumed, once. True when this call stamped it."""
    cur.execute("""UPDATE chat_messages
                      SET meta = COALESCE(meta, '{}'::jsonb) || %s
                    WHERE id = %s AND NOT (COALESCE(meta, '{}'::jsonb)
                                           ? 'replayed_at')
                RETURNING id""",
                (Json({"replayed_at": _now(), "replay_job_id": job_id,
                       "replay_message_id": message_id,
                       "replay_origin": origin}), int(plans_id)))
    return bool(cur.fetchone())


def _body(out):
    """(json body, status) of a Flask view's return value."""
    if isinstance(out, tuple):
        response, status = out[0], out[1]
    else:
        response, status = out, getattr(out, "status_code", 200)
    try:
        body = response.get_json(silent=True) or {}
    except Exception:                                   # pragma: no cover
        body = {}
    return body, int(status)


def resume(user_id, origin, project_id=None, window_h=WINDOW_H):
    """Run the account's newest held request once, as a normal agent turn.

    Needs a Flask app context (the send path answers with jsonify and reads
    the app's DATABASE_URL). Never raises. Returns a dict with ``resumed``
    (this call started it), ``reason`` and, when it ran, ``project_id``,
    ``plans_id``, ``message_id`` and ``job_id``."""
    from routes import video as v           # routes.video imports this package
    try:
        with v.vdb() as conn:
            cur = conn.cursor()
            if v._subscribe_gate_applies(cur, user_id):
                return {"resumed": False, "reason": "subscription_required"}
            held = pending(cur, user_id, window_h, project_id)
            if not held:
                return {"resumed": False, "reason": "nothing_held"}
            base = {"project_id": held["project_id"], "plans_id": held["id"]}
            if not studio_lane_open(cur, user_id):
                # The plan edits through the customer's own assistant; it is
                # shown the request (for_assistant) instead of a Studio turn.
                return dict(base, resumed=False, reason="mcp_only")
            if assistant_took_over(cur, user_id, held["created_at"],
                                   held["meta"]):
                # The lane opened after the request was handed to the
                # customer's assistant, which already took it (D1V).
                return dict(base, resumed=False, reason="assistant_took_over")
            kind = _kind(held["meta"])
            blocked = held["meta"]["blocked_prompt"]
            pid = held["project_id"]
            if kind == "message":
                message_id = int(blocked["message_id"])
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (pid,))
                # Under the lock: the Studio's /replay-blocked may have taken
                # it while this call was looking.
                cur.execute("""SELECT 1 FROM chat_messages
                                WHERE id = %s AND NOT (COALESCE(meta, '{}')
                                                       ? 'replayed_at')""",
                            (held["id"],))
                if not cur.fetchone():
                    return dict(base, resumed=False, reason="already_resumed")
                cur.execute("""SELECT 1 FROM video_jobs
                                WHERE project_id = %s AND type = 'agent_turn'
                                  AND state IN ('queued','running') LIMIT 1""",
                            (pid,))
                if cur.fetchone():
                    return dict(base, resumed=False, reason="busy")
                if (v._running_jobs_count(cur, user_id)
                        >= v.MAX_CONCURRENT_JOBS_PER_USER):
                    return dict(base, resumed=False, reason="busy_capacity")
                if not v.check_and_reserve(conn, user_id, min_credits=1.0):
                    return dict(base, resumed=False,
                                reason="insufficient_credits")
                job_id = v._enqueue(cur, pid, user_id, "agent_turn",
                                    {"message_id": message_id,
                                     "replayed_blocked": True,
                                     "replay_origin": origin})
                _mark(cur, held["id"], origin, job_id, message_id)
                result = dict(base, resumed=True, reason="enqueued",
                              job_id=job_id, message_id=message_id)
        if kind == "message":
            v.record_client_event(user_id, pid, "blocked_prompt_replayed",
                                  detail={"surface": "blank_canvas",
                                          "job_id": result["job_id"],
                                          "via": origin}, origin="server")
            return result
        # A typed request: the ordinary send path, deduped with the Studio's
        # own replay by the same client_msg_id.
        out = v.send_as_user(user_id, pid, {
            "text": str(blocked.get("text") or "")[:4000],
            "attachments": blocked.get("attachments") or [],
            "client_msg_id": f"replay-{held['id']}"})
        body, status = _body(out)
        if status != 200 or not body.get("message_id"):
            return dict(base, resumed=False,
                        reason=str(body.get("code") or f"http_{status}"))
        with v.vdb() as conn:
            stamped = _mark(conn.cursor(), held["id"], origin,
                            body.get("job_id"), body.get("message_id"))
        if body.get("duplicate") or not stamped:
            return dict(base, resumed=False, reason="already_resumed",
                        message_id=body.get("message_id"))
        v.record_client_event(user_id, pid, "blocked_prompt_replayed",
                              detail={"surface": held["meta"].get("surface")
                                      or "chat",
                                      "job_id": body.get("job_id"),
                                      "prompt_chars": len(blocked["text"]),
                                      "via": origin}, origin="server")
        return dict(base, resumed=True,
                    reason=("waiting_for_index" if body.get("waiting_for_index")
                            else "sent"),
                    job_id=body.get("job_id"),
                    message_id=body.get("message_id"))
    except Exception as e:
        print(f"[held_request] resume failed for user {user_id} "
              f"({origin}): {type(e).__name__}: {e}", flush=True)
        return {"resumed": False, "reason": "error"}


def kick_after_payment(app, user_id, origin):
    """The webhook's entry point: resume off the request thread, so Paddle's
    acknowledgement never waits on it and the webhook's own transaction (its
    connection is per app context) has ended before this one reads. Never
    raises. Returns the thread (tests join it)."""
    def run():
        try:
            with app.app_context():
                out = resume(user_id, origin)
            if out.get("resumed") or out.get("reason") not in (
                    "nothing_held", "subscription_required"):
                print(f"[held_request] user {user_id} after {origin}: {out}",
                      flush=True)
        except Exception as e:                          # pragma: no cover
            print(f"[held_request] kick failed for user {user_id}: {e}",
                  flush=True)
    try:
        t = threading.Thread(target=run, daemon=True, name="held-request")
        t.start()
        return t
    except Exception as e:                              # pragma: no cover
        print(f"[held_request] could not start for user {user_id}: {e}",
              flush=True)
        return None


def for_assistant(cur, user_id, project_id=None, window_h=WINDOW_H):
    """The held request an MCP-only subscriber's own assistant should see:
    {project_id, text} (``project_id`` None: the account's newest), or None.

    Only for a plan that is MCP-only today (MCP Connect, Plus), while the
    request is still waiting (pending: not run by the Studio, nothing typed
    after it), the account has paid past the wall, and the assistant has not
    started working since the wall (any MCP call other than a state read, in
    any project, means it took over). Exactly once: a Studio run stamps the
    wall (pending then answers None), and once the assistant took over the
    Studio never runs it (assistant_took_over, read by resume and by
    /replay-blocked). D1V: a NEW Plus subscriber's Studio lane may open after
    the payment handed the request here; the note stays until one of the two
    takes it, instead of vanishing with nobody running it."""
    from routes import video as v
    held = pending(cur, user_id, window_h, project_id)
    if not held:
        return None
    text = str((held["meta"].get("blocked_prompt") or {}).get("text")
               or "").strip()
    if not text:
        return None
    if v._subscribe_gate_applies(cur, user_id) \
            or not _mcp_only_plan(cur, user_id):
        return None
    if _assistant_worked_since(cur, user_id, held["created_at"]):
        return None
    return {"project_id": held["project_id"], "plans_id": held["id"], "text": (
        "WAITING REQUEST FROM THE STUDIO: before subscribing, the user typed "
        f"this in project {held['project_id']}; it has not run yet and their "
        "plan edits through you: \"" + text[:1500] + "\". Unless the user "
        "asks for something else, this is what they want made there.")}
