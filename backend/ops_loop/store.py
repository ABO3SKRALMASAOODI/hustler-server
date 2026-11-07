"""The outcome loop's only production writes: session_reviews, tickets,
ticket_reviews and ops_review_keys (migration 041), and a replay's real
dollars on spend_ledger (migration 043). Nothing else.

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

WRITABLE = ("session_reviews", "tickets", "ticket_reviews", "ops_review_keys",
            "spend_ledger")
_WRITE_RE = re.compile(
    r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_\.\"]+)", re.I)
CAUSE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,79}$")
SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2}
CLOSED = ("fixed", "wont_fix")
# The only wont_fix there is (AGENTS.md: a ticket closes with a root fix plus a
# replay on a copy reaching the bar). FIXER.md section 11.
OWNER_DECISION = "owner decision"
KEY_TTL = timedelta(hours=8)
# Verdict and score must tell the same story (RUBRIC_V3.md, "The verdict
# words"). delivered_good starts at today's podcast-short grade; everything
# below it that reached the user is delivered_poor, down to 1 for "nothing
# really edited".
SCORE_BANDS = {"delivered_good": (7, 10), "delivered_poor": (1, 6),
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


# ── Rubric v3 (owner, 6 Nov 2026) ─────────────────────────────────────────
# "the agent that is rating the videos is actually overrating them ... the
# same upload, nothing changed, and the rater gave the 6." And: "give the
# rater the capability of actually saying incredible edits, not only 10 out
# of 10." A verdict carries what was MEASURED (edit_delta: signal processing,
# no model) and the tier; the ceilings below are checked here, so a score the
# signal disproves cannot reach the database. RUBRIC_V3.md is the scale.
RUBRIC = "v3"
TIERS = ("standard", "alien")
# Only Opus or above rates (owner: Opus 5.5 surpasses Fable 5.1 on benchmarks).
RATER_PREFIXES = ("claude-opus",)
CLASS_CEILING = {"same_upload": 2.0, "trim_only": 3.0, "light_touch": 4.0}
DELTA_CLASSES = tuple(CLASS_CEILING) + ("edited", "standalone", "unknown")
NO_NEW_PICTURES_CEILING = 6.0
DEFECT_CEILING = {"broken_render": 3.0, "ask_missed": 4.0,
                  "caption_over_face": 6.0, "wrong_words": 7.0,
                  "off_centre_card": 8.0, "low_quality_text": 8.0,
                  "slop_visual": 8.0, "typo": 9.5}
CRAFT_DIMENSIONS = ("story_hook", "cuts_pacing", "type_captions",
                    "motion_design", "footage_broll", "colour_light", "sound")
GATE_DIMENSIONS = ("fidelity", "defects")
# What the admin shows and the ceilings read; the raw series stay on the Mac.
DELTA_KEYS = ("class", "proof", "mode", "unchanged_pct", "unmatched_pct",
              "overlay_pct", "shots", "shots_added", "cuts", "cuts_added",
              "cuts_per_min", "type_events", "type_events_added",
              "camera_moves", "camera_moves_added", "audio_events",
              "audio_events_added", "audio_added_pct", "audio_source_pct",
              "duration_change_s", "duration_ratio", "colour_shift_de",
              "source_used_pct", "program_s", "reorders")
ALIEN_WORDS = 30


def delta_ceiling(delta):
    """The measured ceiling for this edit_delta, or None."""
    delta = delta or {}
    caps = []
    if delta.get("class") in CLASS_CEILING:
        caps.append(CLASS_CEILING[delta["class"]])
    if delta.get("mode") == "paired" and delta.get("class") != "unknown" and \
            float(delta.get("shots_added", 99)) <= 1 and \
            float(delta.get("overlay_pct", 100)) < 25:
        caps.append(NO_NEW_PICTURES_CEILING)
    return min(caps) if caps else None


def nothing_edited(delta):
    """The truth of a delta in one sentence, or None for a real edit. The
    journey's one line starts with it."""
    delta = delta or {}
    cls = delta.get("class")
    kept = delta.get("unchanged_pct")
    kept = f"{float(kept):.0f}%" if kept is not None else "almost all"
    if cls == "same_upload":
        return f"Nothing really edited: {kept} of the picture is the upload unchanged."
    if cls == "trim_only":
        return ("Nothing really edited: the upload with parts removed, "
                f"{kept} of the picture unchanged, no new pictures.")
    if cls == "light_touch":
        return ("Barely edited: one thin layer over the upload, "
                f"{kept} of the picture unchanged.")
    return None


def check_rating(fields, verdict, score):
    """The validated rating fields of a v3 verdict, or StoreError naming what
    to fix. fields: rubric, score_exact, tier, dimensions, edit_delta,
    defects, alien_what, alien_nearest. A session with no delivered render
    has no edit_delta (a not_delivered verdict)."""
    fields = dict(fields or {})
    if fields.get("rubric") != RUBRIC:
        raise StoreError(f"a verdict needs rubric \"{RUBRIC}\" (RUBRIC_V3.md, "
                         "written by judge3.py finish)")
    try:
        exact = float(fields.get("score_exact", score))
    except (TypeError, ValueError):
        raise StoreError("score_exact must be a number")
    if exact * 2 != int(exact * 2) or not 1 <= exact <= 10:
        raise StoreError("score_exact goes from 1 to 10 in half points")
    if int(exact) != int(score):
        raise StoreError(f"score {score} is not score_exact {exact} rounded "
                         "down; a score is never rounded up")
    tier = (fields.get("tier") or "standard").strip().lower()
    if tier not in TIERS:
        raise StoreError(f"tier must be one of {', '.join(TIERS)}")
    delta = fields.get("edit_delta")
    if delta is None and verdict != "not_delivered":
        raise StoreError("a delivered result needs its edit_delta numbers "
                         "(edit_delta.py, written into the verdict by "
                         "judge3.py finish)")
    clean_delta = None
    if delta is not None:
        if not isinstance(delta, dict):
            raise StoreError("edit_delta must be an object")
        if delta.get("class") not in DELTA_CLASSES:
            raise StoreError("edit_delta.class must be one of "
                             + ", ".join(DELTA_CLASSES))
        clean_delta = {k: delta[k] for k in DELTA_KEYS if k in delta}
    defects = fields.get("defects") or []
    if not isinstance(defects, list):
        raise StoreError("defects must be a list")
    clean_defects, caps = [], []
    for i, d in enumerate(defects):
        if not isinstance(d, dict) or d.get("id") not in DEFECT_CEILING:
            raise StoreError(f"defects[{i}].id must be one of "
                             + ", ".join(sorted(DEFECT_CEILING)))
        clean_defects.append({"id": d["id"], "at_s": d.get("at_s"),
                              "note": _clean(d.get("note"), 300,
                                             f"defects[{i}].note")})
        caps.append(DEFECT_CEILING[d["id"]])
    measured = delta_ceiling(clean_delta)
    if measured is not None:
        caps.append(measured)
    if caps and exact > min(caps):
        why = ("the signal proves it" if measured == min(caps)
               else "a declared defect sets it")
        raise StoreError(f"score {exact} is above the ceiling {min(caps)}: "
                         f"{why} (RUBRIC_V3.md, Hard ceilings). "
                         f"{(clean_delta or {}).get('proof') or ''}".strip())
    dims = fields.get("dimensions")
    if verdict != "not_delivered" or dims:
        if not isinstance(dims, dict):
            raise StoreError("dimensions is required: the nine scores of "
                             "RUBRIC_V3.md")
        for k in CRAFT_DIMENSIONS + GATE_DIMENSIONS:
            if k not in dims:
                raise StoreError(f"dimensions.{k} is missing (null only when "
                                 "it truly does not apply)")
    what = _clean(fields.get("alien_what"), 400, "alien_what")
    nearest = _clean(fields.get("alien_nearest"), 80, "alien_nearest")
    if tier == "alien":
        if exact < 10:
            raise StoreError("tier alien sits above 10: score 10 first")
        if any(dims.get(k) is not None and float(dims[k]) < 10
               for k in CRAFT_DIMENSIONS):
            raise StoreError("tier alien needs 10 on every craft dimension")
        if not what or _words(what) > ALIEN_WORDS:
            raise StoreError(f"tier alien needs alien_what: at most "
                             f"{ALIEN_WORDS} words naming what the reference "
                             "folder does not contain")
        if not nearest:
            raise StoreError("tier alien needs alien_nearest: the closest "
                             "reference, which you looked at")
    return {"rubric": RUBRIC, "score_exact": exact, "tier": tier,
            "dimensions": dims, "edit_delta": clean_delta,
            "nothing_edited": nothing_edited(clean_delta),
            "defects": clean_defects, "alien_what": what,
            "alien_nearest": nearest}


def _keep_old_score(cur, session_key, new_evidence, signals):
    """A re-rating replaces the old score; the old score stays in the record:
    in signals.previous_scores, and in the evidence, on its own picture."""
    cur.execute("""SELECT verdict, score, reviewer, reviewed_at, evidence,
                          signals FROM session_reviews
                    WHERE session_key = %s""", (session_key,))
    old = cur.fetchone()
    if not old:
        return new_evidence
    old_sig = old.get("signals") or {}
    rubric = old_sig.get("rubric") or "v2"
    at = old["reviewed_at"]
    day = at.strftime("%-d %b %Y") if hasattr(at, "strftime") else str(at)[:10]
    entry = {"score": old["score"], "score_exact": old_sig.get("score_exact"),
             "tier": old_sig.get("tier"), "verdict": old["verdict"],
             "rubric": rubric, "reviewer": old["reviewer"], "at": str(at),
             "why_short": old_sig.get("why_short")}
    signals["previous_scores"] = (old_sig.get("previous_scores") or []) + [entry]
    kept = [dict(e) for e in (old.get("evidence") or [])
            if isinstance(e, dict) and e.get("storage_key")]
    # Rewriting a v3 verdict keeps the line that carries the older score.
    carried = [e for e in kept
               if str(e.get("caption") or "").startswith("Old score")]
    if not kept or rubric == RUBRIC:
        return list(new_evidence) + carried
    first = kept[0]
    first["caption"] = (f"Old score {old['score']}/10 ({old['verdict']}, "
                        f"rubric {rubric}, {day}): "
                        f"{first.get('caption') or ''}")[:400]
    return list(new_evidence) + [first]


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
                  reviewer="claude-opus", journey=None, rating=None):
    """Insert or replace the review of one settled session. `session` is the
    discovered session (key, surface, user, project, times, fingerprint and
    request ids come from the database, not from the agent). `journey` (the
    asks, why_short, next_actions and the two asset ids) is required: it is
    what the admin's Journeys view shows first. `rating` carries rubric v3:
    the half-point score, the tier, the nine dimensions, the defects and the
    edit_delta numbers; `check_rating` refuses a score above a ceiling. A
    replaced score stays in the evidence and in signals.previous_scores."""
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
    if not str(reviewer or "").startswith(RATER_PREFIXES):
        raise StoreError(f"reviewer {reviewer!r}: only Claude Opus or above "
                         "rates (RUBRIC_V3.md)")
    evidence = check_evidence(evidence, session["key"])
    signals = dict(signals or {})
    signals.update(check_journey(journey, required=True))
    signals.update(check_rating(rating, verdict, score))
    evidence = _keep_old_score(cur, session["key"], evidence, signals)
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


def is_owner_decision(ticket):
    """Closed because only the owner can settle it (a price, a plan, the trial,
    a product rule). The one closed state a new failure does not reopen."""
    return ticket.get("status") == "wont_fix" and (
        ticket.get("wont_fix_reason") or "").strip().lower().startswith(
            OWNER_DECISION)


def decide(ticket, happened_at, severity):
    """What one more session or failure showing a cause does to its ticket:
    open | attach | escalate | reopen | report | attach_closed.

    A closed ticket can never hide what comes after it (owner, 6 Nov 2026: "an
    issue must be solved"): #48 sat at wont_fix while account 2837 lost a 7.8 GB
    upload onto it, and only a counter moved. What happened after the closing
    reopens it, however it was closed; an owner decision stays closed and is
    reported. Only what happened BEFORE the closing just counts: it says
    nothing about the fix."""
    if ticket is None:
        return "open"
    if ticket["status"] in CLOSED:
        closed = ticket.get("closed_at")
        if closed and happened_at <= closed:
            return "attach_closed"
        return "report" if is_owner_decision(ticket) else "reopen"
    if _worst(ticket["severity"], severity) != ticket["severity"]:
        return "escalate"
    return "attach"


def open_or_attach(cur, cause_key, title, severity, review_id=None,
                   session_key=None, summary=None):
    """One ticket per cause. A new cause opens a ticket; a known open one
    gains the session (and the worse severity); a closed one that shows up
    again in a session that STARTED after it was closed reopens with
    recurrence + 1 (how it was closed moves to history, so the fixer goes
    deeper). An owner decision stays closed and reports the new occurrence.
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
                           wont_fix_reason = NULL,
                           closed_at = NULL, severity = %s,
                           history = history || %s, updated_at = NOW()
                       WHERE id = %s""",
               (severity,
                _history("recurred", review=review["id"],
                         previous_status=t["status"],
                         previous_wont_fix_reason=t.get("wont_fix_reason"),
                         previous_fix_sha=t["fix_sha"],
                         previous_root_cause=t["root_cause"],
                         previous_regression_check=t["regression_check"],
                         previous_closed_at=t["closed_at"]), t["id"]))
            action = "reopened"
        elif action == "report":
            _w(cur, """UPDATE tickets SET history = history || %s,
                           updated_at = NOW() WHERE id = %s""",
               (_history("reported", review=review["id"]), t["id"]))
            action = "reported"
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
QUALITY_PREFIX = "auto-quality-"    # filed by a rating below the bar
# Measured outcomes of a run that raised no error: a replay with no failure
# proves nothing about them, only the score does.
QUALITY_KEYS = (QUALITY_PREFIX, "auto-gap-first-cut-is-the-upload",
                "auto-gap-request-no-change")
QUALITY_BAR = 10.0                  # AGENTS.md: the floor, on rubric v3


def record_failure(cur, f):
    """File one production failure on the ticket of its cause.

    `f` comes from intake.scan: ref, source, at, user_id, project_id,
    severity, cause_key, title, detail. Idempotent per ref (a ref already in
    the ticket's history is a duplicate). Opens the ticket, attaches to an
    open one (escalating severity), reopens a closed one when the failure
    happened after the closing (recurrence + 1), whatever it was closed as,
    and reports it on an owner decision, which stays closed. A failure from
    before the closing only counts. Returns (ticket, action)."""
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
        _link_review(cur, row["id"], f)
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
            "previous_status": t["status"],
            "previous_wont_fix_reason": t.get("wont_fix_reason"),
            "previous_fix_sha": t["fix_sha"],
            "previous_root_cause": t["root_cause"],
            "previous_regression_check": t["regression_check"],
            "previous_closed_at": t["closed_at"]})
        # A new episode starts from this failure, not from the last one's
        # severity: a customer who then recovers alone pages nobody.
        sev = severity
        status_sql = """, status = 'open', recurrence_count =
                         recurrence_count + 1, fix_sha = NULL,
                         regression_check = NULL, wont_fix_reason = NULL,
                         closed_at = NULL"""
        action = "reopened"
    elif action == "report":
        events.insert(0, {"event": "reported", "at": _now().isoformat(
            timespec="seconds"), "ref": ref})
        action = "reported"
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
    _link_review(cur, t["id"], f)
    cur.execute("SELECT * FROM tickets WHERE id = %s", (t["id"],))
    return dict(cur.fetchone()), action


def _link_review(cur, ticket_id, f):
    """A rating below the bar shows up on its ticket with its pictures."""
    if f.get("review_id"):
        _w(cur, """INSERT INTO ticket_reviews (ticket_id, review_id)
                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
           (ticket_id, f["review_id"]))


def settle_failure(cur, item, blocked, note=None):
    """The second look at a failure (ops_loop.recovery): the customer got past
    it or was left stuck. Left stuck raises its open ticket to P0, whoever the
    customer is, and reopens one that closed meanwhile; an owner decision stays
    closed and says so in its history. Got past it leaves the ticket where it
    is, open, because the cause is still to be fixed. Returns the ticket, or
    None if there is none."""
    cur.execute("SELECT * FROM tickets WHERE cause_key = %s FOR UPDATE",
                (item["cause_key"],))
    row = cur.fetchone()
    if not row:
        return None
    t = dict(row)
    entry = {"at": _now().isoformat(timespec="seconds"), "ref": item.get("ref"),
             "user": item.get("user_id"), "project": item.get("project_id")}
    if note:
        entry["note"] = note[:300]
    if blocked and t["status"] in CLOSED and not is_owner_decision(t):
        # The ticket closed during the grace, or the failure was filed after
        # the closing that followed it. The customer is still stuck, and this
        # look is the only thing that knows: left closed, nobody ever would.
        entry.update(event="recurred", severity="P0", why="left_blocked",
                     previous_status=t["status"],
                     previous_wont_fix_reason=t.get("wont_fix_reason"),
                     previous_fix_sha=t["fix_sha"],
                     previous_root_cause=t["root_cause"],
                     previous_regression_check=t["regression_check"],
                     previous_closed_at=t["closed_at"])
        _w(cur, """UPDATE tickets SET status = 'open', severity = 'P0',
                       recurrence_count = recurrence_count + 1, fix_sha = NULL,
                       regression_check = NULL, wont_fix_reason = NULL,
                       closed_at = NULL, history = history || %s,
                       updated_at = NOW() WHERE id = %s""",
           (json.dumps([entry], default=str), t["id"]))
        t.update(status="open", severity="P0")
        return t
    if blocked and t["status"] in ("open", "fixing") and t["severity"] != "P0":
        entry.update(event="escalated", severity="P0", why="left_blocked")
        _w(cur, """UPDATE tickets SET severity = 'P0', history = history || %s,
                       updated_at = NOW() WHERE id = %s""",
           (json.dumps([entry], default=str), t["id"]))
        t["severity"] = "P0"
        return t
    entry["event"] = "left_blocked" if blocked else "recovered"
    _w(cur, "UPDATE tickets SET history = history || %s WHERE id = %s",
       (json.dumps([entry], default=str), t["id"]))
    return t


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
STATE_KEYS = ("ops_intake_watermark",
              # When each scheduled agent last ran (ops_review.py heartbeat).
              "ops_heartbeat_p0_watch", "ops_heartbeat_fixer",
              "ops_heartbeat_reviewer")


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


# ── The clock of a ticket (owner, 7 Nov 2026: "nor issues neither latency in
# handling them is accepted") ─────────────────────────────────────────────
OPENINGS = ("opened", "recurred", "reopened")
AWAITED = ("escalated", "released", "verify_failed")


def _event_at(entry):
    try:
        at = datetime.fromisoformat(str(entry.get("at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def clock(history, now=None):
    """Where the latest episode of a ticket stands, in seconds; a recurrence
    starts a new one and its history keeps the old.

      seen_s    the failure happening to the ticket opening
      claim_s   waiting to the first claim, from when it became someone's to
                act on: a P1 raised to P0 is timed from the raising, because a
                P1 waits for the daily Fixer by design
      live_s    the opening to the fix verified in production (`ops.sh live`),
                never restarted by an escalation or a hand-back: the customer's
                clock does not stop for those. The latest fix counts: one that
                fell short fixes forward. A ticket closed with no such mark has
                the closing as a bound, flagged live_is_closing
      open_s    how long it has been waiting for that, while it is

    Computed from the history, which is the record; the claim and live events
    also carry their own number, as it stood when they were written."""
    c, due = {}, None
    for h in history or []:
        at = _event_at(h) if isinstance(h, dict) else None
        if at is None:
            continue
        event = h.get("event")
        if event in OPENINGS:
            c, due = {"opened_at": at}, at
        elif not c:
            continue
        elif event == "failure" and "seen_s" not in c:
            c["seen_s"] = max(0, int((c["opened_at"] - at).total_seconds()))
        elif event in AWAITED and "claimed_at" not in c:
            due = at
        elif event == "fixing" and "claimed_at" not in c:
            c["claimed_at"] = at
            c["claim_s"] = int((at - due).total_seconds())
        elif event == "live" or (event == "fixed" and "live_at" not in c):
            c["live_at"] = at
            c["live_s"] = int((at - c["opened_at"]).total_seconds())
            if event == "fixed":
                c["live_is_closing"] = True
            else:
                c.pop("live_is_closing", None)
        elif event == "wont_fix":
            c["closed"] = True
    if c and now is not None and "live_at" not in c and not c.get("closed"):
        c["open_s"] = int((now - c["opened_at"]).total_seconds())
    c.pop("closed", None)
    for key in ("opened_at", "claimed_at", "live_at"):
        if key in c:
            c[key] = c[key].isoformat(timespec="seconds")
    return c


def add_replay_spend(cur, provider, usd):
    """A replay on a copy runs against its own database and bills the same
    provider account: what it spent is reported here, on the replay lane."""
    usd = float(usd)
    if not 0 <= usd < 1000:
        raise StoreError("usd must be a dollar amount of a replay run")
    _w(cur, """INSERT INTO spend_ledger (day, provider, lane, purpose, usd, calls)
               VALUES ((NOW() AT TIME ZONE 'UTC')::date, %s, 'replay',
                       'gauntlet', %s, 1)
               ON CONFLICT (day, provider, lane, purpose) DO UPDATE
               SET usd = spend_ledger.usd + EXCLUDED.usd,
                   calls = spend_ledger.calls + EXCLUDED.calls,
                   updated_at = NOW()""", (str(provider)[:24], usd))


def set_fixing(cur, ticket_id, note=None):
    cur.execute("SELECT status, history FROM tickets WHERE id = %s",
                (int(ticket_id),))
    row = cur.fetchone()
    if not row:
        raise StoreError(f"no ticket {ticket_id}")
    if row["status"] != "open":
        raise StoreError(f"ticket {ticket_id} is {row['status']}, not open")
    claim = _history("fixing", note=_clean(note, 500, "note"))
    entry = json.loads(claim)
    seen = clock((row.get("history") or []) + entry)
    _w(cur, """UPDATE tickets SET status = 'fixing', history = history || %s,
                   updated_at = NOW() WHERE id = %s""",
       (json.dumps([dict(entry[0], claim_s=seen.get("claim_s"))], default=str),
        int(ticket_id)))


def mark_live(cur, ticket_id, sha):
    """The fix is in production, as verified by the caller. It goes live before
    the replay that closes the ticket, so the closing cannot time it."""
    sha = _clean(sha, 80, "sha", required=True)
    cur.execute("SELECT status, history FROM tickets WHERE id = %s FOR UPDATE",
                (int(ticket_id),))
    row = cur.fetchone()
    if not row:
        raise StoreError(f"no ticket {ticket_id}")
    if row["status"] == "wont_fix":
        raise StoreError(f"ticket {ticket_id} is wont_fix: nothing was shipped")
    entry = json.loads(_history("live", sha=sha))
    seen = clock((row.get("history") or []) + entry)
    _w(cur, """UPDATE tickets SET history = history || %s, updated_at = NOW()
               WHERE id = %s""",
       (json.dumps([dict(entry[0], live_s=seen.get("live_s"))], default=str),
        int(ticket_id)))
    return seen


def reopen(cur, ticket_id, note):
    """A closed ticket whose closing turned out to be wrong goes back to open,
    with why it was closed kept in its history. #48 sat at wont_fix ("device
    side, recovery works as designed") while the class hit 7 customers."""
    note = _clean(note, 2000, "note", required=True)
    cur.execute("SELECT * FROM tickets WHERE id = %s FOR UPDATE", (int(ticket_id),))
    t = cur.fetchone()
    if not t:
        raise StoreError(f"no ticket {ticket_id}")
    if t["status"] not in ("fixed", "wont_fix"):
        raise StoreError(f"ticket {ticket_id} is {t['status']}, not closed")
    _w(cur, """UPDATE tickets SET status = 'open', fix_sha = NULL,
                   regression_check = NULL, wont_fix_reason = NULL,
                   closed_at = NULL, history = history || %s,
                   updated_at = NOW()
               WHERE id = %s RETURNING *""",
       (_history("reopened", note=note, previous_status=t["status"],
                 previous_wont_fix_reason=t["wont_fix_reason"],
                 previous_fix_sha=t["fix_sha"],
                 previous_closed_at=t["closed_at"]), int(ticket_id)))
    return dict(cur.fetchone())


def unmute(cur, ticket_id, note):
    """A wont_fix that is not an owner decision goes back to open: it was only
    ever a mute. Nobody is blocked by it this minute, so it waits for the daily
    Fixer instead of paging; the next customer left stuck raises it."""
    t = reopen(cur, ticket_id, note)
    if t["severity"] != "P0":
        return t
    _w(cur, """UPDATE tickets SET severity = 'P1', history = history || %s,
                   updated_at = NOW() WHERE id = %s RETURNING *""",
       (_history("lowered", severity="P1", why="unmuted, nobody blocked now"),
        int(ticket_id)))
    return dict(cur.fetchone())


def release_fixing(cur, ticket_id, expected_updated_at, note, severity=None,
                   waiting_on_customer=None, verify_failed=False):
    """Return an unfinished claim with evidence, without hiding new intake.

    The caller must have reviewed this exact ticket revision. A failure filed
    since that review must be inspected before its urgency can be changed.

    `waiting_on_customer` is the Fixer's explicit statement that our side is
    ready and only the customer's return can move it, saying how the product
    resumes or tells them (FIXER.md). The owner's email reads this field, never
    the note's words: with it the P0 is WAITING ON CUSTOMER; without it, a
    hand-back with no fix live is NOT HANDLED (owner, 7 Nov 2026, #883).

    `verify_failed` is the VERIFY run's verdict that the live fix did not
    prove itself on a copy: the entry is 'verify_failed' instead of
    'released', and the P0 watch takes it again at once (no RELEASE_QUIET).
    """
    note = _clean(note, 2000, "note", required=True)
    if waiting_on_customer is not None:
        waiting_on_customer = _clean(waiting_on_customer, 500,
                                     "waiting_on_customer", required=True)
    if verify_failed and waiting_on_customer:
        raise StoreError("a failed proof is ours to fix, never a wait on the "
                         "customer: pass one of --verify-failed and "
                         "--waiting-on-customer")
    if severity is not None and severity not in SEVERITIES:
        raise StoreError(f"severity must be one of {', '.join(SEVERITIES)}")
    try:
        expected = datetime.fromisoformat(expected_updated_at)
    except (TypeError, ValueError):
        raise StoreError("expected_updated_at must be an ISO timestamp") from None
    if expected.tzinfo is None:
        raise StoreError("expected_updated_at must include a timezone")
    cur.execute("SELECT * FROM tickets WHERE id = %s FOR UPDATE", (int(ticket_id),))
    ticket = cur.fetchone()
    if not ticket:
        raise StoreError(f"no ticket {ticket_id}")
    if ticket["status"] != "fixing":
        raise StoreError(f"ticket {ticket_id} is {ticket['status']}, not fixing")
    if ticket["updated_at"] != expected:
        raise StoreError(f"ticket {ticket_id} changed; inspect its latest failures first")
    severity = severity or ticket["severity"]
    if SEVERITY_RANK[severity] > SEVERITY_RANK[ticket["severity"]]:
        raise StoreError(f"a release never lowers a ticket ({ticket['severity']} "
                         f"to {severity}): a downgrade took #77, #93 and #123 out "
                         "of the watch with their customers unfinished "
                         "(FIXER.md section 11). Hand it back at its severity, "
                         "with the work named in the note")
    _w(cur, """UPDATE tickets SET status = 'open', severity = %s,
                   history = history || %s, updated_at = NOW()
               WHERE id = %s RETURNING *""",
       (severity, _history("verify_failed" if verify_failed else "released",
                           note=note, previous_severity=ticket["severity"],
                           severity=severity,
                           waiting_on_customer=waiting_on_customer),
        int(ticket_id)))
    return dict(cur.fetchone())


def ticket_projects(cur, ticket):
    """The projects a ticket is about: its failures and its linked reviews."""
    found = {h.get("project") for h in (ticket.get("history") or [])
             if isinstance(h, dict) and h.get("event") == "failure"}
    cur.execute("""SELECT r.project_id FROM ticket_reviews tr
                     JOIN session_reviews r ON r.id = tr.review_id
                    WHERE tr.ticket_id = %s""", (ticket["id"],))
    found |= {r["project_id"] for r in cur.fetchall()}
    return {int(p) for p in found if p is not None}


def is_quality(ticket):
    """Filed by a rating or a measured empty result, not by something that
    failed: its bar is the score."""
    key = ticket.get("cause_key") or ""
    return not key.startswith(AUTO_PREFIX) or key.startswith(QUALITY_KEYS)


def check_replay(cur, ticket, replay):
    """The proof a `fixed` needs (AGENTS.md): that exact request, on a copy,
    through the product, reaching the bar. `replay` is what ops_review.py read
    from the run on disk: session, run, state, failures, and for a quality
    ticket score_exact, rubric, rated_by; fixer is who closes. Below the bar
    the ticket stays open."""
    if not isinstance(replay, dict) or not replay.get("session"):
        raise StoreError(
            "a ticket closes only with a root fix plus a replay of that "
            "request on a copy, through the product (AGENTS.md): pass --replay "
            "<gauntlet run>/<session> (FIXER.md section 7)")
    session = str(replay["session"])
    projects = ticket_projects(cur, ticket)
    number = session[1:] if session[:1] == "p" else session
    if projects and not (number.isdigit() and int(number) in projects):
        raise StoreError(f"replay {session} is not a session of this ticket "
                         f"(its projects: "
                         f"{', '.join(str(p) for p in sorted(projects)[:12])})")
    if replay.get("state") != "done":
        raise StoreError(f"replay {session} did not finish "
                         f"(state {replay.get('state')!r}): the ticket stays "
                         "open")
    if replay.get("failures"):
        raise StoreError(f"replay {session} failed "
                         f"({', '.join(map(str, replay['failures']))[:200]}): "
                         "the ticket stays open")
    out = {k: replay.get(k) for k in ("run", "session", "be_sha", "fe_sha",
                                      "score_exact", "tier", "rated_by")
           if replay.get(k) is not None}
    if not is_quality(ticket):
        return out
    try:
        exact = float(replay.get("score_exact"))
    except (TypeError, ValueError):
        raise StoreError(f"replay {session} has no rating: a quality ticket "
                         f"closes at {QUALITY_BAR} on rubric {RUBRIC}, rated by "
                         "a session that did not make the fix") from None
    if replay.get("rubric") != RUBRIC or exact < QUALITY_BAR:
        raise StoreError(f"replay {session} scored {exact} on rubric "
                         f"{replay.get('rubric')}: below the bar of "
                         f"{QUALITY_BAR} the ticket stays open, with the score "
                         "in its note")
    rated_by = str(replay.get("rated_by") or "")
    if not rated_by.startswith(RATER_PREFIXES):
        raise StoreError("--rated-by must name the Reviewer session, Claude "
                         "Opus or above (RUBRIC_V3.md)")
    if rated_by == str(replay.get("fixer") or ""):
        raise StoreError("a fixer never rates its own fix (AGENTS.md)")
    return out


def close(cur, ticket_id, status, root_cause=None, fix_sha=None,
          regression_check=None, reason=None, replay=None):
    """fixed: root_cause + fix_sha + regression_check and a replay on a copy
    that reached the bar (check_replay). wont_fix: only an owner decision,
    with the reason saying which; nothing else is closed without a fix
    (AGENTS.md; owner, 6 Nov 2026: nothing is closed as device-side, noise,
    expected or "the customer recovered alone")."""
    if status not in CLOSED:
        raise StoreError("close with --status fixed or --status wont_fix")
    cur.execute("SELECT * FROM tickets WHERE id = %s FOR UPDATE",
                (int(ticket_id),))
    t = cur.fetchone()
    if not t:
        raise StoreError(f"no ticket {ticket_id}")
    if t["status"] in CLOSED:
        raise StoreError(f"ticket {ticket_id} is already {t['status']}; "
                         "reopen it first")
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
        proof = check_replay(cur, dict(t), replay)
        _w(cur, """UPDATE tickets SET status = 'fixed', root_cause = %s,
                       fix_sha = %s, regression_check = %s,
                       wont_fix_reason = NULL, closed_at = NOW(),
                       history = history || %s, updated_at = NOW()
                   WHERE id = %s""",
           (root_cause, fix_sha, regression_check,
            # Closed through the replay gate is the fix proven: 'verified'
            # (owner, 7 Nov 2026: "we shouldn't just trust a ticket").
            json.dumps(json.loads(_history(
                "fixed", fix_sha=fix_sha, replay=proof,
                regression_check=regression_check))
                + json.loads(_history("verified", fix_sha=fix_sha,
                                      replay=proof))), int(ticket_id)))
    else:
        reason = _clean(reason, 2000, "reason", required=True)
        if not reason.lower().startswith(OWNER_DECISION):
            raise StoreError(
                "wont_fix is only for a decision the owner must make: start "
                "the reason with \"owner decision:\" and name it. A customer "
                "failure is never closed as device-side, noise, expected or "
                "recovered: it stays open until the product change that stops "
                "it hurting the customer ships (FIXER.md section 11)")
        _w(cur, """UPDATE tickets SET status = 'wont_fix',
                       wont_fix_reason = %s, closed_at = NOW(),
                       root_cause = COALESCE(%s, root_cause),
                       history = history || %s, updated_at = NOW()
                   WHERE id = %s""",
           (reason, _clean(root_cause, 3000, "root_cause"),
            _history("wont_fix", reason=reason), int(ticket_id)))
    cur.execute("SELECT * FROM tickets WHERE id = %s", (int(ticket_id),))
    return dict(cur.fetchone())
