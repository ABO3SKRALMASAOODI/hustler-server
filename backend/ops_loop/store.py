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


def write_verdict(cur, session, verdict, score, reasoning, evidence,
                  root_cause=None, request_summary=None, signals=None,
                  reviewer="claude-opus"):
    """Insert or replace the review of one settled session. `session` is the
    discovered session (key, surface, user, project, times, fingerprint and
    request ids come from the database, not from the agent)."""
    if verdict not in VERDICTS:
        raise StoreError(f"verdict must be one of {', '.join(VERDICTS)}")
    try:
        score = int(score)
    except (TypeError, ValueError):
        raise StoreError("score must be a whole number from 1 to 10")
    if not 1 <= score <= 10:
        raise StoreError("score must be from 1 to 10")
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

