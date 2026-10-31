"""The outcome loop's only production writes: session_reviews, tickets,
ticket_reviews and ops_review_keys (migration 041). Nothing else.

`guard()` refuses any write statement that names another table, so a bug in
a caller cannot touch customer data, billing or credits through this module.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from ops_loop import (EVIDENCE_KINDS, EVIDENCE_PREFIX, SEVERITIES, SURFACES,
                      VERDICTS)
from ops_loop.sessions import evidence_slug

WRITABLE = ("session_reviews", "tickets", "ticket_reviews", "ops_review_keys")
_WRITE_RE = re.compile(
    r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_\.\"]+)", re.I)
CAUSE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
KEY_TTL = timedelta(hours=8)
# Verdict and score must tell the same story (REVIEWER.md, step 4).
SCORE_BANDS = {"delivered_good": (6, 10), "delivered_poor": (3, 6),
               "not_delivered": (1, 3)}


class StoreError(ValueError):
    """A refused write, with a sentence the agent can act on."""


def guard(sql):
    m = _WRITE_RE.match(sql)
    if not m:
        raise StoreError("only INSERT/UPDATE/DELETE statements are allowed")
    table = m.group(2).replace('"', "").split(".")[-1].lower()
    if table not in WRITABLE:
        raise StoreError(f"writes to {table} are not allowed; the outcome "
                         f"loop writes only {', '.join(WRITABLE)}")
    return sql


def _w(cur, sql, params=None):
    cur.execute(guard(sql), params)


def _now():
    return datetime.now(timezone.utc)


def _clean(text, limit, name, required=False):
    text = (text or "").strip()
    if required and not text:
        raise StoreError(f"{name} is required")
    if len(text) > limit:
        raise StoreError(f"{name} is {len(text)} characters; keep it under "
                         f"{limit}")
    return text or None


# ── Keys the backend accepts for presigning and evidence uploads ─────────

def mint_key(cur, label="ops_review", ttl=KEY_TTL):
    """A fresh random key; only its SHA-256 is stored. Returns (key, exp)."""
    key = secrets.token_urlsafe(32)
    exp = _now() + ttl
    _w(cur, "DELETE FROM ops_review_keys WHERE expires_at < NOW() - "
            "INTERVAL '1 day'")
    _w(cur, """INSERT INTO ops_review_keys (key_sha256, label, expires_at)
               VALUES (%s, %s, %s)""",
       (hashlib.sha256(key.encode()).hexdigest(), label[:80], exp))
    return key, exp


# ── Verdicts ─────────────────────────────────────────────────────────────

def check_evidence(items, session_key):
    if not isinstance(items, list) or not items:
        raise StoreError("a verdict needs at least one evidence picture "
                         "(upload it with `ops_review.py evidence`)")
    prefix = f"{EVIDENCE_PREFIX}{evidence_slug(session_key)}/"
    out = []
    for i, e in enumerate(items):
        if not isinstance(e, dict):
            raise StoreError(f"evidence[{i}] must be an object")
        kind = (e.get("kind") or "").strip()
        if kind not in EVIDENCE_KINDS:
            raise StoreError(f"evidence[{i}].kind {kind!r} is not one of "
                             f"{', '.join(EVIDENCE_KINDS)}")
        key = (e.get("storage_key") or "").strip()
        if not key.startswith(prefix) or ".." in key:
            raise StoreError(f"evidence[{i}].storage_key must be under "
                             f"{prefix} (got {key!r})")
        caption = _clean(e.get("caption"), 400, f"evidence[{i}].caption",
                         required=True)
        item = {"kind": kind, "caption": caption, "storage_key": key}
        for opt in ("at_s", "asset_id", "job_id"):
            if e.get(opt) is not None:
                item[opt] = e[opt]
        out.append(item)
    return out


# ── The journey fields (owner, 31 Oct 2026) ───────────────────────────────
# "I need to understand what happened with the user, what the product did,
# what the user wanted and whether we made that or not." Every verdict
# carries, inside session_reviews.signals (no migration): one row per thing
# the user asked for with done / partly / not_done and a short note, one
# short line of why the score, what the user did next, and which source and
# delivered video the verdict is about. The admin's Journeys view shows them.
ASK_STATUSES = ("done", "partly", "not_done")
JOURNEY_FORMAT = 2
ASKS_MAX = 12
ASK_TEXT_MAX = 300
ASK_NOTE_WORDS = 20
WHY_WORDS = 25
NEXT_MAX = 8
NEXT_WORDS = 12


def _words(text):
    return len(re.findall(r"\S+", text or ""))


def check_journey(fields, required=True):
    """The validated journey fields, or StoreError naming what to fix.

    fields: {"asks": [{"text", "status", "note"}], "why_short",
             "next_actions": [str], "source_asset_id", "delivered_asset_id"}
    """
    fields = dict(fields or {})
    asks = fields.get("asks")
    if asks in (None, "", []):
        if required:
            raise StoreError(
                "a verdict needs `asks`: one row per thing the user asked for, "
                "[{\"text\": \"cinematic colour grade\", \"status\": "
                "\"done|partly|not_done\", \"note\": \"at most 20 words\"}] "
                "(REVIEWER.md step 5). For the automatic First cut with no "
                "typed request, the ask is \"a usable first cut of my video\"")
        asks = []
    if not isinstance(asks, list):
        raise StoreError("asks must be a list of {text, status, note}")
    if len(asks) > ASKS_MAX:
        raise StoreError(f"{len(asks)} asks; group them into at most "
                         f"{ASKS_MAX}")
    clean_asks = []
    for i, a in enumerate(asks):
        if not isinstance(a, dict):
            raise StoreError(f"asks[{i}] must be an object")
        text = _clean(a.get("text"), ASK_TEXT_MAX, f"asks[{i}].text",
                      required=True)
        status = (a.get("status") or "").strip().lower().replace(" ", "_")
        status = {"partial": "partly", "not done": "not_done",
                  "missing": "not_done", "no": "not_done",
                  "yes": "done"}.get(status, status)
        if status not in ASK_STATUSES:
            raise StoreError(f"asks[{i}].status must be one of "
                             f"{', '.join(ASK_STATUSES)}")
        note = _clean(a.get("note"), 400, f"asks[{i}].note")
        if note and _words(note) > ASK_NOTE_WORDS:
            raise StoreError(f"asks[{i}].note is {_words(note)} words; keep "
                             f"it to {ASK_NOTE_WORDS} or fewer")
        clean_asks.append({"text": text, "status": status, "note": note})
    why = _clean(fields.get("why_short"), 400, "why_short",
                 required=required)
    if why and _words(why) > WHY_WORDS:
        raise StoreError(f"why_short is {_words(why)} words; keep it to "
                         f"{WHY_WORDS} or fewer (one plain line of why this "
                         "score)")
    nxt = fields.get("next_actions") or []
    if isinstance(nxt, str):
        nxt = [nxt]
    if not isinstance(nxt, list) or len(nxt) > NEXT_MAX:
        raise StoreError(f"next_actions is a list of at most {NEXT_MAX} "
                         "short phrases")
    clean_next = []
    for i, n in enumerate(nxt):
        n = _clean(str(n), 200, f"next_actions[{i}]")
        if n and _words(n) > NEXT_WORDS:
            raise StoreError(f"next_actions[{i}] is {_words(n)} words; keep "
                             f"each to {NEXT_WORDS} or fewer")
        if n:
            clean_next.append(n)
    out = {"asks": clean_asks, "why_short": why, "next_actions": clean_next,
           "journey_format": JOURNEY_FORMAT}
    for k in ("source_asset_id", "delivered_asset_id"):
        v = fields.get(k)
        if v in (None, ""):
            out[k] = None
            continue
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            raise StoreError(f"{k} must be an asset id (a number)")
    return out


def annotate(cur, session_key, fields, backfilled_from=None):
    """Add or replace the journey fields of an EXISTING review without
    touching its verdict, score, reasoning or review count (the backfill of
    reviews written before the fields existed)."""
    journey = check_journey(fields, required=True)
    if backfilled_from:
        journey["backfilled_from"] = _clean(backfilled_from, 60,
                                            "backfilled_from")
    _review(cur, session_key=session_key)
    _w(cur, """UPDATE session_reviews
                  SET signals = COALESCE(signals, '{}'::jsonb) || %(j)s::jsonb,
                      updated_at = NOW()
                WHERE session_key = %(key)s
            RETURNING id, session_key""",
       {"j": json.dumps(journey), "key": session_key})
    return dict(cur.fetchone())


def write_verdict(cur, session, verdict, score, reasoning, evidence,
                  root_cause=None, request_summary=None, signals=None,
                  reviewer="claude-opus", journey=None):
    """Insert or replace the review of one settled session. `session` is the
    discovered session (key, surface, user, project, times, fingerprint and
    request ids come from the database, not from the agent). `journey` (the
    asks, why_short, next_actions and the two asset ids) is required: it is
    what the admin's Journeys view shows first."""
    if verdict not in VERDICTS:
        raise StoreError(f"verdict must be one of {', '.join(VERDICTS)}")
    try:
        score = int(score)
    except (TypeError, ValueError):
        raise StoreError("score must be a whole number from 1 to 10")
    if not 1 <= score <= 10:
        raise StoreError("score must be from 1 to 10")
    lo, hi = SCORE_BANDS[verdict]
    if not lo <= score <= hi:
        raise StoreError(f"a {verdict} verdict scores {lo}-{hi} (got {score}); "
                         "pick the verdict that matches the score")
    if session["surface"] not in SURFACES:
        raise StoreError(f"unknown surface {session['surface']!r}")
    if not session.get("settled"):
        raise StoreError("this session is not settled yet; review it later")
    reasoning = _clean(reasoning, 6000, "reasoning", required=True)
    root_cause = _clean(root_cause, 2000, "root_cause")
    if verdict != "delivered_good" and not root_cause:
        raise StoreError("a delivered_poor or not_delivered verdict needs a "
                         "root-cause hypothesis (--root-cause)")
    request_summary = _clean(request_summary, 1000, "request_summary")
    evidence = check_evidence(evidence, session["key"])
    signals = dict(signals or {})
    signals.update(check_journey(journey, required=True))
    from ops_loop.sessions import request_ids
    _w(cur, """
        INSERT INTO session_reviews
            (session_key, surface, user_id, project_id, request_ids,
             started_at, settled_at, fingerprint, verdict, score, reasoning,
             root_cause, request_summary, evidence, signals, reviewer)
        VALUES (%(key)s, %(surface)s, %(user)s, %(project)s, %(ids)s,
                %(start)s, %(end)s, %(fp)s, %(verdict)s, %(score)s,
                %(reasoning)s, %(root)s, %(summary)s, %(evidence)s,
                %(signals)s, %(reviewer)s)
        ON CONFLICT (session_key) DO UPDATE SET
            surface = EXCLUDED.surface, request_ids = EXCLUDED.request_ids,
            settled_at = EXCLUDED.settled_at,
            fingerprint = EXCLUDED.fingerprint, verdict = EXCLUDED.verdict,
            score = EXCLUDED.score, reasoning = EXCLUDED.reasoning,
            root_cause = EXCLUDED.root_cause,
            request_summary = EXCLUDED.request_summary,
            evidence = EXCLUDED.evidence, signals = EXCLUDED.signals,
            reviewer = EXCLUDED.reviewer,
            review_count = session_reviews.review_count + 1,
            reviewed_at = NOW(), updated_at = NOW()
        RETURNING id, review_count""",
       {"key": session["key"], "surface": session["surface"],
        "user": session["user_id"], "project": session["project_id"],
        "ids": json.dumps(request_ids(session)),
        "start": session["started_at"], "end": session["ended_at"],
        "fp": session["fingerprint"], "verdict": verdict, "score": score,
        "reasoning": reasoning, "root": root_cause,
        "summary": request_summary, "evidence": json.dumps(evidence),
        "signals": json.dumps(signals or {}, default=str),
        "reviewer": reviewer[:60]})
    return dict(cur.fetchone())


# ── Tickets ──────────────────────────────────────────────────────────────

def _history(event, **kw):
    return json.dumps([dict(event=event, at=_now().isoformat(
        timespec="seconds"), **{k: v for k, v in kw.items()
                                if v is not None})], default=str)


def _review(cur, review_id=None, session_key=None):
    if review_id is not None:
        cur.execute("SELECT * FROM session_reviews WHERE id = %s",
                    (int(review_id),))
    else:
        cur.execute("SELECT * FROM session_reviews WHERE session_key = %s",
                    (session_key,))
    row = cur.fetchone()
    if not row:
        raise StoreError("no review for that session yet: write the verdict "
                         "first, then open or attach the ticket")
    return dict(row)


def decide(ticket, review_started_at, severity):
    """What one more session showing a cause does to its ticket:
    open | attach | escalate | reopen | attach_closed."""
    if ticket is None:
        return "open"
    if ticket["status"] == "fixed" and ticket.get("closed_at") \
            and review_started_at > ticket["closed_at"]:
        return "reopen"
    if ticket["status"] in ("fixed", "wont_fix"):
        return "attach_closed"
    if _worst(ticket["severity"], severity) != ticket["severity"]:
        return "escalate"
    return "attach"


def open_or_attach(cur, cause_key, title, severity, review_id=None,
                   session_key=None, summary=None):
    """One ticket per cause. A new cause opens a ticket; a known open one
    gains the session (and the worse severity); a FIXED one that shows up
    again in a session that STARTED after it was closed reopens with
    recurrence + 1 (its previous fix moves to history, so the fixer goes
    deeper). A won't-fix cause only counts the new occurrence.
    Returns (ticket, action)."""
    cause_key = (cause_key or "").strip().lower()
    if not CAUSE_RE.match(cause_key):
        raise StoreError("cause must be a lowercase slug of 3-80 letters, "
                         "digits and dashes, naming the CAUSE (e.g. "
                         "index-scratch-space-too-small), not the symptom")
    if severity not in SEVERITIES:
        raise StoreError(f"severity must be one of {', '.join(SEVERITIES)}")
    title = _clean(title, 200, "title", required=True)
    summary = _clean(summary, 3000, "summary")
    review = _review(cur, review_id, session_key)
    _w(cur, """INSERT INTO tickets (cause_key, title, severity, status,
                   summary, first_seen, last_seen, occurrences, history)
               VALUES (%s, %s, %s, 'open', %s, %s, %s, 0, %s)
               ON CONFLICT (cause_key) DO NOTHING RETURNING *""",
       (cause_key, title, severity, summary, review["started_at"],
        review["settled_at"],
        _history("opened", review=review["id"], severity=severity)))
    row = cur.fetchone()
    if row:
        t, action = dict(row), "opened"
    else:
        cur.execute("SELECT * FROM tickets WHERE cause_key = %s FOR UPDATE",
                    (cause_key,))
        t = dict(cur.fetchone())
        action = decide(t, review["started_at"], severity)
        if action == "reopen":
            _w(cur, """UPDATE tickets SET status = 'open',
                           recurrence_count = recurrence_count + 1,
                           fix_sha = NULL, regression_check = NULL,
                           closed_at = NULL, severity = %s,
                           history = history || %s, updated_at = NOW()
                       WHERE id = %s""",
               (_worst(t["severity"], severity),
                _history("recurred", review=review["id"],
                         previous_fix_sha=t["fix_sha"],
                         previous_root_cause=t["root_cause"],
                         previous_regression_check=t["regression_check"],
                         previous_closed_at=t["closed_at"]), t["id"]))
            action = "reopened"
        elif action == "escalate":
            _w(cur, """UPDATE tickets SET severity = %s,
                           history = history || %s, updated_at = NOW()
                       WHERE id = %s""",
               (severity, _history("escalated", review=review["id"],
                                   severity=severity), t["id"]))
            action = "attached"
        elif action == "attach_closed":
            action = "attached_to_closed"
        else:
            action = "attached"
    _w(cur, """INSERT INTO ticket_reviews (ticket_id, review_id)
               VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING ticket_id""",
       (t["id"], review["id"]))
    if cur.fetchone():
        _w(cur, """UPDATE tickets SET occurrences = occurrences + 1,
                       last_seen = GREATEST(last_seen, %s),
                       first_seen = LEAST(first_seen, %s),
                       updated_at = NOW()
                   WHERE id = %s""",
           (review["settled_at"], review["started_at"], t["id"]))
    cur.execute("SELECT * FROM tickets WHERE id = %s", (t["id"],))
    return dict(cur.fetchone()), action


def _worst(a, b):
    return a if SEVERITY_RANK[a] <= SEVERITY_RANK[b] else b


# ── Automatic intake (ops_loop.intake): a failure without a review yet ───

FAILURE_REFS_KEPT = 60       # history keeps this many failure refs a ticket
AUTO_PREFIX = "auto-"


def record_failure(cur, f):
    """File one production failure on the ticket of its cause.

    `f` comes from intake.scan: ref, source, at, user_id, project_id,
    severity, cause_key, title, detail. Idempotent per ref (a ref already in
    the ticket's history is a duplicate). Opens the ticket, attaches to an
    open one (escalating severity), reopens a FIXED one when the failure
    happened after the fix was closed (recurrence + 1), and only counts it on
    a won't-fix one. Returns (ticket, action)."""
    key = (f.get("cause_key") or "").strip().lower()
    if not CAUSE_RE.match(key):
        raise StoreError(f"bad cause key {key!r}")
    severity = f.get("severity") or "P0"
    if severity not in SEVERITIES:
        raise StoreError(f"severity must be one of {', '.join(SEVERITIES)}")
    at = f["at"] if f["at"].tzinfo else f["at"].replace(tzinfo=timezone.utc)
    ref = str(f["ref"])[:80]
    entry = {"event": "failure", "ref": ref, "source": f.get("source"),
             "user": f.get("user_id"), "project": f.get("project_id"),
             "at": at.isoformat(timespec="seconds"),
             "detail": (f.get("detail") or "")[:400] or None}
    if f.get("job_id"):
        entry["job"] = f["job_id"]
    title = _clean((f.get("title") or key)[:200], 200, "title",
                   required=True)
    summary = _auto_summary(f, 1, 1)
    _w(cur, """INSERT INTO tickets (cause_key, title, severity, status,
                   summary, first_seen, last_seen, occurrences, history)
               VALUES (%s, %s, %s, 'open', %s, %s, %s, 1, %s)
               ON CONFLICT (cause_key) DO NOTHING RETURNING *""",
       (key, title, severity, summary, at, at,
        json.dumps([{"event": "opened", "at": _now().isoformat(
            timespec="seconds"), "severity": severity, "auto": True,
            "ref": ref}, entry], default=str)))
    row = cur.fetchone()
    if row:
        return dict(row), "opened"
    cur.execute("SELECT * FROM tickets WHERE cause_key = %s FOR UPDATE",
                (key,))
    t = dict(cur.fetchone())
    cur.execute("""SELECT EXISTS (SELECT 1 FROM jsonb_array_elements(history)
                                   h WHERE h->>'ref' = %s) AS seen
                     FROM tickets WHERE id = %s""", (ref, t["id"]))
    if (cur.fetchone() or {}).get("seen"):
        return t, "duplicate"
    action = decide(t, at, severity)
    refs = sum(1 for h in (t.get("history") or [])
               if isinstance(h, dict) and h.get("event") == "failure")
    users = {h.get("user") for h in (t.get("history") or [])
             if isinstance(h, dict) and h.get("event") == "failure"}
    users.add(f.get("user_id"))
    events = [entry] if refs < FAILURE_REFS_KEPT else []
    sev = t["severity"]
    status_sql = ""
    if action == "reopen":
        events.insert(0, {"event": "recurred", "at": _now().isoformat(
            timespec="seconds"), "ref": ref, "auto": True,
            "previous_fix_sha": t["fix_sha"],
            "previous_root_cause": t["root_cause"],
            "previous_regression_check": t["regression_check"],
            "previous_closed_at": t["closed_at"]})
        sev = _worst(t["severity"], severity)
        status_sql = """, status = 'open', recurrence_count =
                         recurrence_count + 1, fix_sha = NULL,
                         regression_check = NULL, closed_at = NULL"""
        action = "reopened"
    elif action == "escalate":
        events.insert(0, {"event": "escalated", "at": _now().isoformat(
            timespec="seconds"), "severity": severity, "ref": ref})
        sev = severity
        action = "attached"
    elif action == "attach_closed":
        action = "attached_to_closed"
    else:
        action = "attached"
    _w(cur, f"""UPDATE tickets SET occurrences = occurrences + 1,
                    last_seen = GREATEST(last_seen, %s),
                    first_seen = LEAST(first_seen, %s), severity = %s,
                    summary = CASE WHEN cause_key LIKE 'auto-%%' THEN %s
                                   ELSE summary END,
                    history = history || %s, updated_at = NOW()
                    {status_sql}
                WHERE id = %s""",
       (at, at, sev, _auto_summary(f, int(t["occurrences"] or 0) + 1,
                                   len(users - {None})),
        json.dumps(events, default=str), t["id"]))
    cur.execute("SELECT * FROM tickets WHERE id = %s", (t["id"],))
    return dict(cur.fetchone()), action


def _auto_summary(f, occurrences, users):
    at = f["at"]
    when = at.strftime("%Y-%m-%d %H:%M UTC") if hasattr(at, "strftime") \
        else str(at)
    return (f"Auto-filed from production ({f.get('source')}). Latest: "
            f"{f.get('ref')} for user #{f.get('user_id')}"
            + (f", project {f.get('project_id')}" if f.get("project_id")
               else "")
            + f" at {when}: {(f.get('detail') or '')[:600]} · "
            f"{occurrences} failure{'s' if occurrences != 1 else ''} from "
            f"{users} customer{'s' if users != 1 else ''} so far.")[:3000]


# The intake's own bookkeeping row in app_kv (its watermark). Nothing else
# in app_kv is ever written from here.
STATE_KEYS = ("ops_intake_watermark",)


def guard_state(sql, key):
    """The one app_kv write: an upsert of one of STATE_KEYS."""
    m = _WRITE_RE.match(sql)
    if not m or m.group(1).upper() != "INSERT INTO" or \
            m.group(2).lower() != "app_kv":
        raise StoreError("the outcome loop's state is an app_kv upsert")
    if key not in STATE_KEYS:
        raise StoreError(f"app_kv key {key!r} is not the outcome loop's")
    return sql


def put_state(cur, key, value):
    cur.execute(guard_state("""INSERT INTO app_kv (key, value, updated_at)
                   VALUES (%s, %s, NOW()) ON CONFLICT (key) DO UPDATE
                   SET value = EXCLUDED.value, updated_at = NOW()""", key),
                (key, json.dumps(value, default=str)))


def set_fixing(cur, ticket_id, note=None):
    cur.execute("SELECT status FROM tickets WHERE id = %s", (int(ticket_id),))
    row = cur.fetchone()
    if not row:
        raise StoreError(f"no ticket {ticket_id}")
    if row["status"] != "open":
        raise StoreError(f"ticket {ticket_id} is {row['status']}, not open")
    _w(cur, """UPDATE tickets SET status = 'fixing', history = history || %s,
                   updated_at = NOW() WHERE id = %s""",
       (_history("fixing", note=_clean(note, 500, "note")), int(ticket_id)))


def close(cur, ticket_id, status, root_cause=None, fix_sha=None,
          regression_check=None, reason=None):
    """fixed: root_cause + fix_sha + regression_check are all required.
    wont_fix: a reason is required."""
    if status not in ("fixed", "wont_fix"):
        raise StoreError("close with --status fixed or --status wont_fix")
    cur.execute("SELECT * FROM tickets WHERE id = %s FOR UPDATE",
                (int(ticket_id),))
    t = cur.fetchone()
    if not t:
        raise StoreError(f"no ticket {ticket_id}")
    if status == "fixed":
        root_cause = _clean(root_cause, 3000, "root_cause", required=True)
        fix_sha = _clean(fix_sha, 80, "fix_sha", required=True)
        one = r"((be|fe):)?[0-9a-f]{7,40}"
        if not re.fullmatch(f"{one}(,{one})*", fix_sha):
            raise StoreError("fix_sha must be the commit sha(s) on main, "
                             "comma-separated; prefix a frontend commit "
                             "with fe: (e.g. abc1234,fe:def5678)")
        regression_check = _clean(regression_check, 500, "regression_check",
                                  required=True)
        _w(cur, """UPDATE tickets SET status = 'fixed', root_cause = %s,
                       fix_sha = %s, regression_check = %s,
                       wont_fix_reason = NULL, closed_at = NOW(),
                       history = history || %s, updated_at = NOW()
                   WHERE id = %s""",
           (root_cause, fix_sha, regression_check,
            _history("fixed", fix_sha=fix_sha,
                     regression_check=regression_check), int(ticket_id)))
    else:
        reason = _clean(reason, 2000, "reason", required=True)
        _w(cur, """UPDATE tickets SET status = 'wont_fix',
                       wont_fix_reason = %s, closed_at = NOW(),
                       root_cause = COALESCE(%s, root_cause),
                       history = history || %s, updated_at = NOW()
                   WHERE id = %s""",
           (reason, _clean(root_cause, 3000, "root_cause"),
            _history("wont_fix", reason=reason), int(ticket_id)))
    cur.execute("SELECT * FROM tickets WHERE id = %s", (int(ticket_id),))
    return dict(cur.fetchone())

