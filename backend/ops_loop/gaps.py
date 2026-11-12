"""What hurts a customer and writes no failure row (audit-invisible, 6 Nov 2026).

intake.scan reads rows that say something failed. Most pain never writes one: a
job that sits, an export nobody receives, a checkout that never loads, a request
answered with nothing changed. Each check here expects the next row by a
measured deadline, or reads a measured fact the intake skips, and files the
miss as a failure on a cause of its own. Uploads that stop without landing are
intake._stopped_uploads, which this builds on: recovery.py gives them the same
second look.

Every check is read-only, bounded by time, reads no customer words, and sees a
deadline once: it fires when the deadline falls inside (since, now], so a replay
of any past window answers what it would have filed (ops_review.py gaps).
Deadlines are the audit's, back-tested on 14 days of production.
"""
import json
import re
from datetime import timedelta

from admin_metrics import defs
from ops_loop import store

ANALYSIS_FLOOR_S = 600          # 10 min, or 1.5 x the source
ANALYSIS_RATIO = 1.5
# 20 min, or 1.5 x the source: back-tested on 14 days it fires on 1 of 8 finals
# and 6 of 400 previews (3 accounts), what a customer really sat through.
RENDER_FLOOR_S = 1200
RENDER_JOBS = ("final", "preview", "shorts_plan")
EXPORT_WAIT = timedelta(minutes=10)
CHECKOUT_WAIT = timedelta(seconds=60)
TURN_SILENCE = timedelta(seconds=120)
REVIEW_SETTLE = timedelta(minutes=10)   # the Reviewer files its own cause first
MODEL_STREAK = 3
CANCEL_WITHIN = timedelta(hours=24)
BAR = 10
LOOKBACK = timedelta(days=3)
CHECKOUT_FAILED = ("setup_failed", "error", "load_timeout", "payment_failed")
# An export refusal that is not a failure: the paywall, a second click, and the
# two holds whose cause has its own check (the turn, the upload). Any other
# code, today's or tomorrow's, stopped a customer pressing Export.
EXPORT_HOLDS = ("subscription_required", "already_running", "edit_in_progress",
                "original_uploading")
# Sentences the trial's reply filter removed for saying what failed. "length"
# and most of "internal" are tidying, so they are not read as hiding anything.
HIDING_REASONS = ("deny", "limit", "review_note")
KNOWN_OUTCOMES = ("fulfilled", "partial", "blocked", "awaiting_user",
                  "upgrade_required")
QUALITY_CLASSES = ("same_upload", "trim_only", "light_touch")
TERMINAL = "('done', 'failed', 'canceled', 'cancelled')"


def _i():
    from ops_loop import intake
    return intake


def _customers():
    return _i()._customers()


def _item(ref, source, at, r, severity, parts, title, detail, **extra):
    i = _i()
    return dict({"ref": ref, "source": source, "at": i._aware(at),
                 "user_id": r.get("user_id"), "project_id": r.get("project_id"),
                 "severity": severity, "cause_key": i.cause_key(*parts),
                 "title": i._short(title), "detail": i._short(detail, 600)},
                **extra)


def _analysis_overdue(cur, since, now, p):
    """49 analysis jobs took over 10 min and no row said so; a 5 s clip took
    3,569 s for 36 s of work. A customer sat looking at a spinner."""
    q = dict(p, floor=ANALYSIS_FLOOR_S, ratio=ANALYSIS_RATIO,
             lookback=now - LOOKBACK)
    cur.execute(f"""
        SELECT o.* FROM (
            SELECT j.id, j.user_id, j.project_id, j.state, j.attempts,
                   j.updated_at, a.duration_s, a.bytes,
                   j.created_at + make_interval(secs => GREATEST(
                       %(floor)s, %(ratio)s * COALESCE(a.duration_s, 0))) AS due
              FROM video_jobs j JOIN users u ON u.id = j.user_id
              LEFT JOIN assets a ON a.id = CASE
                   WHEN j.payload->>'asset_id' ~ '^[0-9]+$'
                   THEN (j.payload->>'asset_id')::int END
             WHERE j.type = 'index' AND j.created_at > %(lookback)s
               AND j.created_at <= %(now)s AND {_customers()}) o
         WHERE o.due > %(since)s AND o.due <= %(now)s
           AND (o.state NOT IN {TERMINAL} OR o.updated_at > o.due)
         ORDER BY o.due LIMIT %(limit)s""", q)
    out = []
    for r in cur.fetchall():
        trait = "queued" if r["state"] == "queued" else "running"
        out.append(_item(
            f"gap:analysis:{r['id']}", "analysis", r["due"], r, "P0",
            ("gap", "analysis-overdue", trait),
            f"Analysis overran its deadline ({trait})",
            f"job {r['id']} · {r['state']} · attempts {r['attempts']} · source "
            f"{r['duration_s']} s, {r['bytes']} bytes", job_id=r["id"]))
    return out


def _render_overdue(cur, since, now, p):
    """Account 2820's final ran 4,397 s and the ticket came only at its death.
    A render that sits writes nothing, so it is judged like an analysis: by a
    deadline, while it is open."""
    q = dict(p, floor=RENDER_FLOOR_S, ratio=ANALYSIS_RATIO,
             lookback=now - LOOKBACK, kinds=list(RENDER_JOBS))
    cur.execute(f"""
        SELECT o.* FROM (
            SELECT j.id, j.type, j.user_id, j.project_id, j.state, j.attempts,
                   j.updated_at, s.duration_s,
                   j.created_at + make_interval(secs => GREATEST(
                       %(floor)s, %(ratio)s * COALESCE(s.duration_s, 0))) AS due
              FROM video_jobs j JOIN users u ON u.id = j.user_id
              LEFT JOIN LATERAL (
                   SELECT max(a.duration_s) AS duration_s FROM assets a
                    WHERE a.project_id = j.project_id
                      AND a.kind IN ('original', 'video_clip')) s ON TRUE
             WHERE j.type = ANY(%(kinds)s) AND j.created_at > %(lookback)s
               AND j.created_at <= %(now)s AND {_customers()}) o
         WHERE o.due > %(since)s AND o.due <= %(now)s
           AND (o.state NOT IN {TERMINAL} OR o.updated_at > o.due)
         ORDER BY o.due LIMIT %(limit)s""", q)
    return [_item(f"gap:render:{r['id']}", "analysis", r["due"], r, "P0",
                  ("gap", "render-overdue", r["type"]),
                  f"A {r['type']} render overran its deadline",
                  f"job {r['id']} · {r['state']} · attempts {r['attempts']} · "
                  f"source {r['duration_s']} s", job_id=r["id"])
            for r in cur.fetchall()]


def _export_never_arrived(cur, since, now, p):
    """Paying account 2818's export finished and no download followed."""
    q = dict(p, lo=since - EXPORT_WAIT, hi=now - EXPORT_WAIT)
    cur.execute(f"""
        SELECT ce.id, ce.user_id, ce.project_id, ce.created_at AS at, ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind = 'export_render_done' AND ce.created_at > %(lo)s
           AND ce.created_at <= %(hi)s AND {_customers()}
           AND NOT EXISTS (
                 SELECT 1 FROM client_events d
                  WHERE d.project_id = ce.project_id
                    AND d.kind IN ('download_url_ready', 'download_triggered',
                                   'mcp_export_delivered')
                    AND d.created_at >= ce.created_at
                    AND d.created_at <= %(now)s)
         ORDER BY ce.created_at LIMIT %(limit)s""", q)
    out = []
    for r in cur.fetchall():
        d = _i()._detail(r)
        out.append(_item(
            f"gap:export:{r['id']}", "export", r["at"] + EXPORT_WAIT, r, "P0",
            ("gap", "export-no-download"),
            "An export finished and never reached the customer",
            f"job {d.get('job_id')} · v{d.get('version')} · no download URL in "
            f"{int(EXPORT_WAIT.total_seconds() / 60)} min"))
    return out


def _checkout(cur, since, now, p):
    """Five never-paid accounts, all Pro or Max, lost at checkout: 2821 and 2824
    at setup_failed, three with a checkout that never loaded. 0 tickets."""
    q = dict(p, stages=list(CHECKOUT_FAILED), lo=since - CHECKOUT_WAIT,
             hi=now - CHECKOUT_WAIT)
    cur.execute(f"""
        SELECT ce.id, ce.user_id, ce.project_id, ce.created_at AS at, ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind = 'checkout_stage' AND ce.created_at > %(since)s
           AND ce.created_at <= %(now)s
           AND ce.detail->>'stage' = ANY(%(stages)s) AND {_customers()}
         ORDER BY ce.created_at LIMIT %(limit)s""", q)
    out = []
    for r in cur.fetchall():
        d = _i()._detail(r)
        stage = d.get("stage")
        # A declined card is the customer's to retry; everything else stopped
        # someone who was trying to pay.
        out.append(_item(
            f"gap:checkout:{r['id']}", "checkout", r["at"], r,
            "P1" if stage == "payment_failed" else "P0",
            ("gap", "checkout", stage), f"Checkout failed: {stage}",
            f"{d.get('plan')} · {d.get('billing')} · {stage}"))
    cur.execute(f"""
        SELECT ce.id, ce.user_id, ce.project_id, ce.created_at AS at, ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind = 'checkout_stage' AND ce.created_at > %(lo)s
           AND ce.created_at <= %(hi)s AND ce.detail->>'stage' = 'opened'
           AND {_customers()}
           AND NOT EXISTS (
                 SELECT 1 FROM client_events n
                  WHERE n.user_id = ce.user_id AND n.kind = 'checkout_stage'
                    AND n.id > ce.id AND n.created_at <= %(now)s
                    AND n.detail->>'stage' <> 'opened')
         ORDER BY ce.created_at LIMIT %(limit)s""", q)
    for r in cur.fetchall():
        d = _i()._detail(r)
        out.append(_item(
            f"gap:checkout:{r['id']}", "checkout", r["at"] + CHECKOUT_WAIT, r,
            "P0", ("gap", "checkout", "not-loaded"),
            "Checkout opened and never loaded",
            f"{d.get('plan')} · {d.get('billing')} · nothing in "
            f"{int(CHECKOUT_WAIT.total_seconds())} s"))
    return out


def _request_changed_nothing(cur, since, now, p):
    """21 of 88 typed requests rendered nothing new and 13 were never filed: 3
    ended `fulfilled` on a plan pitch or "your cut stays as it is". A real
    question also lands here, so this is filed, not paged, until the contract
    model stores whether the request expected a change."""
    cur.execute(f"""
        SELECT j.id, j.user_id, j.project_id, j.updated_at AS at,
               j.result->>'outcome' AS outcome,
               COALESCE(j.result->>'status', '') AS status
          FROM video_jobs j JOIN users u ON u.id = j.user_id
          LEFT JOIN video_jobs root ON root.id = CASE
               WHEN j.payload->>'root_agent_job_id' ~ '^[0-9]+$'
               THEN (j.payload->>'root_agent_job_id')::int END
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.type = 'agent_turn' AND j.state = 'done'
           AND j.result->>'outcome' IN ('fulfilled', 'awaiting_user')
           AND COALESCE(j.result->>'continued_job_id', '') = ''
           AND COALESCE(j.result->>'status', '') <> 'budget'
           AND j.result->>'edl_changed' IS DISTINCT FROM 'true'
           AND NOT COALESCE(j.payload, '{{}}'::jsonb) ? 'first_cut'
           AND COALESCE(j.payload, '{{}}'::jsonb) ? 'message_id'
           AND COALESCE(j.payload->>'operator_repair', '') = ''
           AND NOT EXISTS (
                 SELECT 1 FROM assets a
                  WHERE a.project_id = j.project_id AND a.kind = 'render'
                    AND a.created_at >= COALESCE(root.created_at, j.created_at)
                    AND a.created_at <= j.updated_at + INTERVAL '1 minute'
                    AND COALESCE(a.meta->>'variant', '') <> 'preview_check')
           AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    return [_item(f"gap:nochange:{r['id']}", "result", r["at"], r, "P1",
                  ("gap", "request-no-change", r["outcome"]),
                  f"A request ended {r['outcome']} with no new picture",
                  f"job {r['id']} · {r['outcome']} {r['status']} · the edit did "
                  "not change and nothing rendered", job_id=r["id"])
            for r in cur.fetchall()]


def _first_cut_is_the_upload(cur, since, now, p):
    """7 of 40 delivered First cuts are EDL v1 rendered back, outcome
    `fulfilled`. Only the Reviewer could see it, and it has been off."""
    cur.execute(f"""
        SELECT j.id, j.user_id, j.project_id, j.updated_at AS at
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.type = 'agent_turn' AND j.state = 'done'
           AND j.result->>'outcome' = 'fulfilled'
           AND COALESCE(j.payload, '{{}}'::jsonb) ? 'first_cut'
           AND COALESCE(j.result->>'continued_job_id', '') = ''
           AND COALESCE((j.result->>'edl_version'), '') IN ('', '0', '1')
           AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    return [_item(f"gap:firstcut:{r['id']}", "result", r["at"], r, "P1",
                  ("gap", "first-cut-is-the-upload"),
                  "The First cut delivered the upload unchanged",
                  f"job {r['id']} · EDL still v1 at delivery", job_id=r["id"])
            for r in cur.fetchall()]


def _reply_hid_a_failure(cur, since, now, p):
    """The trial's reply filter removes sentences; on a `fulfilled` turn the
    customer is then told nothing failed. The reason code is already stored."""
    cur.execute(f"""
        SELECT j.id, j.user_id, j.project_id, j.updated_at AS at,
               j.result->'honesty'->'trial_reply_dropped' AS dropped
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.type = 'agent_turn' AND j.state = 'done'
           AND j.result->>'outcome' = 'fulfilled'
           AND jsonb_typeof(j.result->'honesty'->'trial_reply_dropped') = 'array'
           AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    out = []
    for r in cur.fetchall():
        dropped = r["dropped"] if isinstance(r["dropped"], list) else []
        why = sorted({str(x.get("why")) for x in dropped
                      if isinstance(x, dict)} & set(HIDING_REASONS))
        if why:
            out.append(_item(
                f"gap:hidden:{r['id']}", "result", r["at"], r, "P1",
                ("gap", "reply-hid-failure", why[0]),
                f"A reply was stripped of what failed ({', '.join(why)})",
                f"job {r['id']} · {len(dropped)} sentence(s) removed · reasons "
                f"{', '.join(why)}", job_id=r["id"]))
    return out


def _brief_unread(cur, since, now, p):
    """The call that reads a request failed and the editor went on from the
    customer's own words with nothing protected. That keeps the turn alive, so
    it can end fulfilled and no failure row says it happened (#136, #138 failed
    the whole turn instead). Reads the stored reason, no customer words."""
    cur.execute(f"""
        SELECT j.id, j.user_id, j.project_id, j.updated_at AS at,
               j.result->'honesty'->>'brief_unavailable' AS why
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.type = 'agent_turn' AND j.state = 'done'
           AND j.result->'honesty' ? 'brief_unavailable'
           AND COALESCE(j.result->>'continued_job_id', '') = ''
           AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    i = _i()
    return [_item(f"gap:brief:{r['id']}", "result", r["at"], r, "P1",
                  ("gap", "brief-unread", i.signature(r["why"])),
                  "A request ran with nothing protected: its brief could not be read",
                  f"job {r['id']} · {r['why']}", job_id=r["id"])
            for r in cur.fetchall()]


def _model_failing(cur, since, now, p):
    """24 byte-identical canned replies went to 8 accounts over 39 hours while
    one provider was dry. Three failures in a row would have fired on the
    first day. One ticket per purpose; ref per hour, so it cannot flood."""
    q = dict(p, streak=MODEL_STREAK, lookback=now - timedelta(days=1))
    cur.execute("""
        SELECT c.purpose, max(c.id) AS id, max(c.created_at) AS at
          FROM (SELECT l.purpose, l.id, l.created_at,
                       l.response->>'error' IS NOT NULL AS bad,
                       row_number() OVER (PARTITION BY l.purpose
                                          ORDER BY l.id DESC) AS n
                  FROM llm_calls l
                 WHERE l.created_at > %(lookback)s
                   AND l.created_at <= %(now)s
                   -- One account's own trial budget refusing a call is a
                   -- budget stop (P2 by the intake's rule), not a dead model:
                   -- ticket 861 paged P0 on 18 of them in one index.
                   AND COALESCE(l.response->>'stop', '') <> 'budget') c
         WHERE c.n <= %(streak)s
         GROUP BY c.purpose
        HAVING count(*) = %(streak)s AND bool_and(c.bad)
           AND max(c.created_at) > %(since)s""", q)
    out = []
    for r in cur.fetchall():
        hour = _i()._aware(r["at"]).strftime("%Y%m%d%H")
        out.append(_item(
            f"gap:model:{r['purpose']}:{hour}", "model", r["at"], {}, "P0",
            ("gap", "model-failing", r["purpose"]),
            f"The last {MODEL_STREAK} {r['purpose']} model calls all failed",
            f"purpose {r['purpose']} · call {r['id']}"))
    return out


def _cancelled_within_a_day(cur, since, now, p):
    """8 of 15 new payers scheduled a cancel within 24 h, 3 of them within 5 min
    of a result, and no ticket type said so. Read only: this reports what
    happened and suggests nothing about the subscription."""
    q = dict(p, within=CANCEL_WITHIN)
    cur.execute(f"""
        SELECT DISTINCT ON (m.user_id) m.user_id, h.provider_updated_at AS at,
               f.paid_at, last.project_id
          FROM admin_subscription_history h
          JOIN (SELECT DISTINCT subscription_id, user_id FROM payments
                 WHERE subscription_id IS NOT NULL) m
            ON m.subscription_id = h.subscription_id
          JOIN users u ON u.id = m.user_id
         CROSS JOIN LATERAL (
               SELECT min(COALESCE(pay.occurred_at, pay.created_at)) AS paid_at
                 FROM payments pay
                WHERE pay.user_id = m.user_id AND {defs.success('pay')}) f
         CROSS JOIN LATERAL (
               SELECT max(pr.id) AS project_id FROM projects pr
                WHERE pr.user_id = m.user_id) last
         WHERE h.provider_updated_at > %(since)s
           AND h.provider_updated_at <= %(now)s
           AND h.scheduled_action = 'cancel'
           AND h.provider_updated_at <= f.paid_at AT TIME ZONE 'UTC' + %(within)s
           AND {_customers()}
         ORDER BY m.user_id, h.provider_updated_at""", q)
    return [_item(f"gap:cancel:{r['user_id']}", "billing", r["at"], r, "P1",
                  ("gap", "cancelled-within-a-day"),
                  "A new payer scheduled a cancel within a day of paying",
                  f"first paid {r['paid_at']} · last project "
                  f"{r['project_id']}")
            for r in cur.fetchall()]


def _quality_trait(review, signals):
    """The one measured thing that names the problem, so ratings fold into
    causes a fixer can take, not one umbrella: nothing edited, not delivered, a
    declared defect, else the weakest craft dimension of rubric v3."""
    delta = (signals.get("edit_delta") or {}).get("class")
    if delta in QUALITY_CLASSES:
        return delta
    if review["verdict"] == "not_delivered":
        return "not-delivered"
    defects = [d.get("id") for d in signals.get("defects") or []
               if isinstance(d, dict) and d.get("id")]
    if defects:
        return sorted(defects)[0]
    dims = signals.get("dimensions") or {}
    scored = [(float(dims[k]), k) for k in store.CRAFT_DIMENSIONS
              if isinstance(dims.get(k), (int, float))]
    return min(scored)[1] if scored else "below-the-bar"


def _below_the_bar(cur, since, now, p):
    """AGENTS.md: an edit that ran without an error and delivered 2/10 is an
    open ticket like any crash. A rating used to touch no ticket, so 42 honest
    ratings at a mean of 2.4 left 0 quality tickets open. A review with no
    active ticket of its own is filed by what was measured; `at` is when the
    session began, so an old session re-rated today cannot reopen a later fix."""
    q = dict(p, bar=BAR, lo=since - REVIEW_SETTLE, hi=now - REVIEW_SETTLE)
    cur.execute(f"""
        SELECT r.id, r.review_count, r.user_id, r.project_id, r.session_key,
               r.verdict, r.score, r.started_at AS at, r.signals
          FROM session_reviews r JOIN users u ON u.id = r.user_id
         WHERE r.reviewed_at > %(lo)s AND r.reviewed_at <= %(hi)s
           AND r.score < %(bar)s AND {_customers()}
           AND NOT EXISTS (
                 SELECT 1 FROM ticket_reviews tr
                   JOIN tickets t ON t.id = tr.ticket_id
                  WHERE tr.review_id = r.id
                    AND t.status IN ('open', 'fixing'))
         ORDER BY r.reviewed_at LIMIT %(limit)s""", q)
    out = []
    for r in cur.fetchall():
        s = r["signals"] if isinstance(r["signals"], dict) else {}
        trait = _quality_trait(r, s)
        out.append(_item(
            f"review:{r['id']}:{r['review_count']}", "review", r["at"], r, "P1",
            ("quality", trait),
            f"Rated {s.get('score_exact') or r['score']}/10: {trait}",
            f"{r['session_key']} · {r['verdict']} · "
            f"{s.get('why_short') or ''}", review_id=r["id"]))
    return out


def _turn_went_silent(cur, since, now, p):
    """27 of 88 typed turns had a silence over 120 s, while the customer watched
    nothing happen. Only the product speaking resets the clock: a customer
    asking "hello?" is still waiting, and so is one whose request moved on to
    its next slice. The product speaks in its replies, in the step rows the
    Studio shows as they land ('activity'), and in the progress of the turn's
    own preview render, which the working bubble shows live (ticket #842: 17
    of 20 "silences" were steps the check did not read, or a 1-3 min render). Judged only while the turn is open, so a past window is not
    replayed by this one."""
    q = dict(p, silence=TURN_SILENCE, lookback=now - timedelta(days=1))
    cur.execute(f"""
        SELECT s.* FROM (
            SELECT j.id, j.user_id, j.project_id, j.state,
                   GREATEST(COALESCE(root.created_at, j.created_at), (
                       SELECT max(m.created_at) FROM chat_messages m
                        WHERE m.session_id = pr.chat_session_id
                          AND m.role IN ('assistant', 'activity')
                       ) AT TIME ZONE 'UTC', (
                       SELECT max(pj.heartbeat_at) FROM video_jobs pj
                        WHERE pj.project_id = j.project_id
                          AND pj.type = 'preview'
                          AND pj.created_at >= COALESCE(root.created_at,
                                                        j.created_at)
                       )) AS heard
              FROM video_jobs j JOIN users u ON u.id = j.user_id
              JOIN projects pr ON pr.id = j.project_id
              LEFT JOIN video_jobs root ON root.id = CASE
                   WHEN j.payload->>'root_agent_job_id' ~ '^[0-9]+$'
                   THEN (j.payload->>'root_agent_job_id')::int END
             WHERE j.type = 'agent_turn' AND j.state IN ('queued', 'running')
               AND j.created_at > %(lookback)s
               AND COALESCE(j.payload->>'operator_repair', '') = ''
               AND {_customers()}) s
         WHERE s.heard + %(silence)s > %(since)s
           AND s.heard + %(silence)s <= %(now)s
         ORDER BY s.heard LIMIT %(limit)s""", q)
    return [_item(f"gap:silent:{r['id']}", "turn", r["heard"] + TURN_SILENCE, r,
                  "P0", ("gap", "turn-silent", r["state"]),
                  "A request went silent while the customer waited",
                  f"job {r['id']} · {r['state']} · nothing shown for "
                  f"{int(TURN_SILENCE.total_seconds())} s", job_id=r["id"])
            for r in cur.fetchall()]


def _unread_endings(cur, since, now, p):
    """A request can end in an outcome the intake does not list (job 135056:
    internal_error), the player's fallback writes a row nothing reads, and so
    does Export refusing a customer: a render that failed, a verification repair
    still open, a network error, a download link that would not come."""
    q = dict(p, known=list(KNOWN_OUTCOMES), holds=list(EXPORT_HOLDS))
    cur.execute(f"""
        SELECT j.id, j.user_id, j.project_id, j.updated_at AS at,
               j.result->>'outcome' AS outcome
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.type = 'agent_turn' AND j.state = 'done'
           AND j.result->>'outcome' IS NOT NULL
           AND NOT (j.result->>'outcome' = ANY(%(known)s))
           AND COALESCE(j.result->>'continued_job_id', '') = ''
           AND COALESCE(j.payload->>'operator_repair', '') = ''
           AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", q)
    out = [_item(f"request:{r['id']}", "request", r["at"], r, "P0",
                 ("request", r["outcome"]),
                 f"A Studio request ended {r['outcome']}",
                 f"job {r['id']} · outcome {r['outcome']}", job_id=r["id"])
           for r in cur.fetchall()]
    cur.execute(f"""
        SELECT ce.id, ce.user_id, ce.project_id, ce.created_at AS at, ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind = 'player_composition_fallback'
           AND ce.created_at > %(since)s AND ce.created_at <= %(now)s
           AND {_customers()}
         ORDER BY ce.created_at LIMIT %(limit)s""", q)
    for r in cur.fetchall():
        d = _i()._detail(r)
        out.append(_item(f"event:{r['id']}", "player", r["at"], r, "P1",
                         ("gap", "player-fallback", d.get("reason") or
                          _i().signature(json.dumps(d, default=str))),
                         "The preview fell back from the live composition",
                         json.dumps(d, default=str)))
    cur.execute(f"""
        SELECT ce.id, ce.kind, ce.user_id, ce.project_id, ce.created_at AS at,
               ce.detail
          FROM client_events ce JOIN users u ON u.id = ce.user_id
         WHERE ce.kind IN ('export_blocked', 'download_failed')
           AND ce.created_at > %(since)s AND ce.created_at <= %(now)s
           AND NOT COALESCE(ce.detail->>'code', '') = ANY(%(holds)s)
           AND {_customers()}
         ORDER BY ce.created_at LIMIT %(limit)s""", q)
    for r in cur.fetchall():
        d = _i()._detail(r)
        code = d.get("code") or "unknown"
        said = "refused" if r["kind"] == "export_blocked" else "could not download"
        out.append(_item(f"event:{r['id']}", "export", r["at"], r, "P0",
                         ("gap", r["kind"].replace("_", "-"), code),
                         f"Export {said}: {code}",
                         f"v{d.get('version')} · {code} · {d.get('reason') or ''}"))
    return out


_TOOL_KEY_PART = re.compile(r"[^a-z0-9-]+")


def _tool_defects(cur, since, now, p):
    """Owner, 9 Nov 2026: "when the edit produce a corrupt thing that may be
    because of the tools ... the reviewer must raise a ticket". A render
    measures its own pixels after its asset row exists (worker/caption_qc.py:
    a caption plate or shadow off its text, text cut by the frame, two items
    overlapping) and carries each defect in result.picture_qc.tool_defects.
    Each files on `tool:<layer>:<defect>` with the frame time and the asset
    id, whatever the model asked for. P0 when it shows in the first seconds
    of the film, else P1."""
    cur.execute(f"""
        SELECT j.id, j.type, j.user_id, j.project_id, j.updated_at AS at,
               j.result->'picture_qc'->'tool_defects' AS defects,
               j.result->>'render_asset_id' AS asset_id,
               j.result->>'edl_version' AS edl_version
          FROM video_jobs j JOIN users u ON u.id = j.user_id
         WHERE j.updated_at > %(since)s AND j.updated_at <= %(now)s
           AND j.state = 'done' AND j.type IN ('preview', 'final')
           AND jsonb_typeof(j.result->'picture_qc'->'tool_defects') = 'array'
           AND {_customers()}
         ORDER BY j.updated_at LIMIT %(limit)s""", p)
    out = []
    for r in cur.fetchall():
        for d in (r["defects"] if isinstance(r["defects"], list) else [])[:8]:
            if not isinstance(d, dict):
                continue
            layer = _TOOL_KEY_PART.sub("-", str(d.get("layer") or "render")
                                       .lower()).strip("-")[:30] or "render"
            kind = _TOOL_KEY_PART.sub("-", str(d.get("defect") or "defect")
                                      .lower()).strip("-")[:40] or "defect"
            try:
                t = float(d.get("t"))
            except (TypeError, ValueError):
                t = None
            where = (f"asset {r['asset_id']} at {t:.2f}s" if t is not None
                     else f"asset {r['asset_id']}")
            out.append({
                "ref": f"tool:{r['id']}:{layer}:{kind}", "source": "tool",
                "at": r["at"], "user_id": r["user_id"],
                "project_id": r["project_id"],
                "severity": "P0" if t is not None and t < 3.0 else "P1",
                "cause_key": f"tool:{layer}:{kind}",
                "title": f"Render drew a broken {layer}: {kind} "
                         f"(project {r['project_id']}, {where})",
                "detail": (f"{r['type']} job {r['id']}, EDL v"
                           f"{r['edl_version']}, {where}: "
                           f"{str(d.get('detail') or '')[:300]}"),
                "job_id": r["id"], "job_type": r["type"],
                "asset_id": r["asset_id"], "frame_t": t})
    return out


def _store_health():
    """This web process's measured store health (storage_health.py), or {}
    where no probe runs (a replay on the Mac, ops_review.py gaps: it must
    not probe production stores from there). create_app starts the probe in
    every web worker. Kept separate so a test can stand in."""
    import storage
    if not storage.store_prober().running():
        return {}
    return storage.store_health()


def _kv(cur, key):
    cur.execute("SELECT value FROM app_kv WHERE key = %s", (key,))
    row = cur.fetchone()
    raw = row["value"] if row else None
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _storage_write_failing(cur, since, now, p):
    """12 Nov 2026: R2 answered every call with NotEntitled from 16:36 UTC;
    every upload failed, and /healthz said storage was fine because nothing
    asked a store. The store the live mode writes to has now failed
    FAILS_FOR_ALARM background probes in a row: every upload fails until a
    store takes them. One ticket per store, a ref per hour. An automatic
    failover (the dispatcher's, storage_health.Failover) is filed on the same
    ticket: it says what moved and that returning is a person's decision."""
    import storage_health
    out = []
    st = _store_health() or {}
    store = st.get("write_store")
    res = (st.get("stores") or {}).get(store) or {}
    if res.get("ok") is False and \
            int(res.get("fails") or 0) >= storage_health.FAILS_FOR_ALARM:
        out.append(_item(
            f"gap:storage:{store}:{now.strftime('%Y%m%d%H')}", "storage", now,
            {}, "P0", ("gap", "storage-write-failing", store),
            f"Uploads fail: the {store} store fails its probe "
            f"({res.get('code') or 'no answer'})",
            f"{store} ({res.get('host')}) failed {res.get('fails')} probes in "
            f"a row since {res.get('since')}: {res.get('err')}"))
    row = _kv(cur, "storage_routing")
    at = row.get("set_at")
    if row.get("by") == storage_health.routing.AUTO_BY and at:
        moment = _i()._aware(at)
        if since < moment <= now:
            failed = row.get("failed_store") or "legacy"
            out.append(_item(
                f"gap:storage-failover:{at}", "storage", moment, {}, "P0",
                ("gap", "storage-write-failing", failed),
                f"Storage failed over automatically: {row.get('previous')} -> "
                f"{row.get('mode')}",
                f"{row.get('note') or ''} Returning is a human or Fixer "
                "decision (storage_migrate.py mode)."))
    return out


LANE_OFF_AFTER = timedelta(minutes=15)


def _claude_lane_off(cur, since, now, p):
    """12 Nov 2026: app_kv.claude_credit_state went off on a billing refusal
    on 9 Nov and stayed off for 3 days after the provider took calls again:
    0 trial grants, about 105 paywalls, and nothing in the ticket system
    said so. Off for anything but the owner's quota (``budget``) for over
    LANE_OFF_AFTER: every new trial meets the paywall. A ref per hour."""
    state = _kv(cur, "claude_credit_state")
    if str(state.get("state") or "").lower() != "off":
        return []
    reason = str(state.get("reason") or "")
    if reason == "budget":
        return []
    started = state.get("since")
    started = _i()._aware(started) if started else None
    if started is None or now - started < LANE_OFF_AFTER:
        return []
    return [_item(
        f"gap:claude-lane-off:{now.strftime('%Y%m%d%H')}", "lane", now, {},
        "P0", ("gap", "claude-lane-off", reason or "unknown"),
        "Claude lane off: every new trial meets the paywall",
        f"claude_credit_state off since {state.get('since')} ({reason}); "
        f"last probe {state.get('last_probe_at')} ok="
        f"{state.get('last_probe_ok')}: {state.get('last_probe_error') or ''}")]


CHECKS = (_analysis_overdue, _render_overdue, _export_never_arrived, _checkout,
          _request_changed_nothing, _first_cut_is_the_upload,
          _reply_hid_a_failure, _brief_unread, _model_failing,
          _cancelled_within_a_day,
          _below_the_bar, _turn_went_silent, _unread_endings,
          _tool_defects, _storage_write_failing, _claude_lane_off)


def scan(cur, since, now, checks=CHECKS):
    """What should have happened by now and did not, in (since, now]. One check
    that breaks never costs the intake its tick or the other checks, and files
    itself: a check that dies in a log line sees nothing, for as long as nobody
    reads the log."""
    i = _i()
    now = i._aware(now)
    p = {"since": i._aware(since), "now": now,
         "internal": list(i.EXCLUDED_USER_IDS), "limit": i.MAX_PER_TICK}
    out = []
    for check in checks:
        try:
            out.extend(check(cur, i._aware(since), now, p))
        except Exception as e:
            print(f"⚠️ [ops_gaps] {check.__name__}: {type(e).__name__}: {e}",
                  flush=True)
            name = check.__name__.strip("_")
            out.append(_item(
                f"gap:broken:{name}:{now.strftime('%Y%m%d%H')}", "gap", now, {},
                "P1", ("gap", "check-broken", name),
                f"A failure check is broken: {name}",
                f"{type(e).__name__}: {e}"))
    return out
