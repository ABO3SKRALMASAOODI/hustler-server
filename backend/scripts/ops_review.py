#!/usr/bin/env python3
"""ops_review: the outcome loop's CLI for the Reviewer and Fixer sessions
(Claude Code, Opus, on the owner's Mac). Runbooks:
/Users/masaoodi/Documents/Valmera/.valmera/ops-loop/REVIEWER.md and FIXER.md.

THE ONLY PRODUCTION WRITE PATH FOR THE AGENTS. It writes session_reviews,
tickets, ticket_reviews and ops_review_keys and nothing else
(ops_loop/store.guard refuses any other table); every read runs on a
read-only connection. It never sends email, never messages a customer and
never touches billing, credits, plans or Paddle.

    ops_review.py pending [--days 3] [--out DIR] [--count]
    ops_review.py context  --session p4746-1792941458
    ops_review.py evidence --session KEY --file sheet.jpg [--name sheet.jpg]
    ops_review.py verdict  --session KEY --verdict delivered_poor --score 3 \
        --reasoning "..." --root-cause "..." --evidence-json evidence.json
    ops_review.py ticket   --session KEY --cause index-scratch-too-small \
        --title "..." --severity P0 [--summary "..."]
    ops_review.py tickets  [--status active] [--urls]
    ops_review.py fixing   --ticket 7 [--note "..."]
    ops_review.py close    --ticket 7 --status fixed --root-cause "..." \
        --fix-sha abc1234 --regression-check "tests/test_x.py::test_y"
    ops_review.py close    --ticket 7 --status wont_fix --reason "..."
    ops_review.py fetch    --key ops/evidence/<slug>/x.jpg --out /tmp/x.jpg
    ops_review.py reviews  [--days 7]
    ops_review.py p0       [--count] [--sync-marker]     # the P0 watch
    ops_review.py failures --ticket 7                    # auto-filed failures
    ops_review.py whoami

DATABASE_URL comes from the environment, else from
/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env; it is never
printed. Downloads and evidence uploads go through the production backend
(/ops/review/*) with a short-lived key this CLI mints (only its SHA-256 is
stored, in ops_review_keys); the key is cached in ~/.valmera-ops/key.json.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)
os.environ.setdefault("SKIP_DB_INIT", "1")

ENV_FILE = "/Users/masaoodi/Documents/Valmera/hustler-server/backend/.env"
OPS_DIR = "/Users/masaoodi/Documents/Valmera/.valmera/ops-loop"
P0_MARKER = os.path.join(OPS_DIR, "P0_PENDING")
BACKEND_URL = os.getenv("OPS_BACKEND_URL",
                        "https://entrepreneur-bot-backend.onrender.com")
KEY_CACHE = os.path.expanduser("~/.valmera-ops/key.json")
READ_ONLY = "-c default_transaction_read_only=on"
KEY_RE = re.compile(r"^p(\d+)-(\d+)$")


class CliError(Exception):
    pass


def out(obj):
    print(json.dumps(obj, indent=1, default=str, ensure_ascii=False))


# ── Database ─────────────────────────────────────────────────────────────

def database_url():
    url = os.getenv("DATABASE_URL", "")
    if url and "stub" not in url:
        return url
    try:
        with open(ENV_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    raise CliError(f"no DATABASE_URL in the environment or {ENV_FILE}")


def connect(read_only=True):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    opts = "-c statement_timeout=60000 -c lock_timeout=5000"
    if read_only:
        opts += " " + READ_ONLY
    conn = psycopg2.connect(database_url(), cursor_factory=RealDictCursor,
                            connect_timeout=15, options=opts)
    conn.autocommit = read_only
    return conn


class Writer:
    """One transaction on a writable connection; commits on success."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run

    def __enter__(self):
        self.conn = connect(read_only=False)
        return self.conn.cursor()

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None and not self.dry_run:
                self.conn.commit()
            else:
                self.conn.rollback()
        finally:
            self.conn.close()
        return False


# ── The backend's storage helpers ────────────────────────────────────────

def ops_key():
    try:
        with open(KEY_CACHE, encoding="utf-8") as fh:
            cached = json.load(fh)
        exp = datetime.fromisoformat(cached["expires_at"])
        if exp - datetime.now(timezone.utc) > timedelta(minutes=30):
            return cached["key"]
    except (OSError, ValueError, KeyError):
        pass
    from ops_loop import store
    with Writer() as cur:
        key, exp = store.mint_key(cur, label="ops_review cli")
    os.makedirs(os.path.dirname(KEY_CACHE), mode=0o700, exist_ok=True)
    fd = os.open(KEY_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"key": key, "expires_at": exp.isoformat()}, fh)
    return key


def call(method, path, body=None, query=None, content_type=None, raw=False,
         timeout=60):
    url = BACKEND_URL.rstrip("/") + path
    if query:
        from urllib.parse import urlencode
        url += "?" + urlencode(query)
    data = None
    headers = {"Authorization": "Ops " + ops_key(),
               "User-Agent": "valmera-ops-review/1"}
    if body is not None:
        if isinstance(body, (bytes, bytearray)):
            data = bytes(body)
            headers["Content-Type"] = content_type or "application/octet-stream"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:400].decode("utf-8", "replace")
        raise CliError(f"{method} {path} answered {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise CliError(f"{method} {path} failed: {e.reason}")
    return payload if raw else json.loads(payload or b"{}")


def presign(asset_ids, keys):
    asset_ids, keys = list(asset_ids or []), list(keys or [])
    if not asset_ids and not keys:
        return {"assets": {}, "keys": {}}
    got = {"assets": {}, "keys": {}}
    for i in range(0, max(len(asset_ids), len(keys)), 150):
        part = call("POST", "/ops/review/presign",
                    {"asset_ids": asset_ids[i:i + 150],
                     "keys": keys[i:i + 150], "expires": 6 * 3600})
        got["assets"].update(part.get("assets") or {})
        got["keys"].update(part.get("keys") or {})
    return got


# ── Sessions ─────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc)


def find_session(cur, key, include_unsettled=False):
    from ops_loop import sessions
    m = KEY_RE.match(key or "")
    if not m:
        raise CliError("a session key looks like p4746-1792941458")
    pid, epoch = int(m.group(1)), int(m.group(2))
    start = datetime.fromtimestamp(epoch, tz=timezone.utc)
    found, _ = sessions.discover(cur, start, _now(), project_ids=[pid],
                                 include_unsettled=True)
    for s in found:
        if s["key"] == key:
            if not s["settled"] and not include_unsettled:
                raise CliError(f"{key} is not settled yet (work is running "
                               "or happened in the last 30 minutes)")
            prev = sessions.reviewed(cur, [key]).get(key)
            s["previous_review"] = prev
            return s
    raise CliError(f"no session {key}: its window changed (new activity "
                   "joined it) or it is not reviewable. Run `pending` again.")


def cmd_pending(a):
    from ops_loop import sessions
    conn = connect()
    cur = conn.cursor()
    since = _now() - timedelta(days=a.days)
    found, stats = sessions.pending(
        cur, since, _now(), settle=timedelta(minutes=a.settle_minutes),
        project_ids=[a.project] if a.project else None)
    found.sort(key=lambda s: s["started_at"])
    if a.limit:
        found = found[:a.limit]
    if a.count:
        print(len(found))
        return 0
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    folder = a.out or os.path.join(OPS_DIR, "work", stamp)
    os.makedirs(folder, exist_ok=True)
    listing = []
    for s in found:
        ctx = sessions.context(s, cur, None if a.no_urls else presign)
        path = os.path.join(folder, f"{s['key']}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(ctx, fh, indent=1, default=str, ensure_ascii=False)
        item = sessions.brief(s)
        item["context_file"] = path
        item["renders"] = len(ctx["renders"])
        item["errors"] = len(ctx["errors"])
        if ctx.get("url_error"):
            item["url_error"] = ctx["url_error"]
        listing.append(item)
    conn.close()
    out({"stats": stats, "folder": folder, "pending": listing})
    return 0


def cmd_context(a):
    from ops_loop import sessions
    conn = connect()
    cur = conn.cursor()
    s = find_session(cur, a.session, include_unsettled=True)
    out(sessions.context(s, cur, None if a.no_urls else presign))
    conn.close()
    return 0


# ── Evidence ─────────────────────────────────────────────────────────────

def _image_ext(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def cmd_evidence(a):
    from ops_loop.sessions import evidence_slug
    if not KEY_RE.match(a.session or ""):
        raise CliError("--session must be a session key like p4746-1792941458")
    with open(a.file, "rb") as fh:
        data = fh.read()
    if len(data) > 6 * 1024 * 1024:
        raise CliError(f"{a.file} is {len(data) // 1024} KB; evidence images "
                       "are at most 6 MB (export a JPEG at quality 80)")
    ext = _image_ext(data)
    if not ext:
        raise CliError(f"{a.file} is not a PNG, JPEG or WebP image")
    base = a.name or os.path.basename(a.file)
    stem = re.sub(r"[^a-z0-9_-]+", "-", base.rsplit(".", 1)[0].lower()) \
        .strip("-")[:56] or "evidence"
    name = f"{stem}.{ext}"
    got = call("POST", "/ops/review/evidence", data,
               {"session": evidence_slug(a.session), "name": name},
               content_type={"png": "image/png", "jpg": "image/jpeg",
                             "webp": "image/webp"}[ext])
    item = {"storage_key": got["storage_key"], "bytes": got.get("bytes")}
    if a.kind:
        item["kind"] = a.kind
    if a.caption:
        item["caption"] = a.caption
    out(item)
    return 0


def cmd_fetch(a):
    got = presign([], [a.key])
    url = (got.get("keys") or {}).get(a.key)
    if not url:
        raise CliError(f"no URL for {a.key}")
    with urllib.request.urlopen(url, timeout=120) as resp, \
            open(a.out, "wb") as fh:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    out({"saved": a.out, "bytes": os.path.getsize(a.out)})
    return 0


# ── Verdicts and tickets ─────────────────────────────────────────────────

def _evidence_items(a):
    items = []
    if a.evidence_json:
        with open(a.evidence_json, encoding="utf-8") as fh:
            loaded = json.load(fh)
        items.extend(loaded if isinstance(loaded, list) else
                     loaded.get("evidence") or [])
    for spec in a.evidence or []:
        parts = spec.split("|", 2)
        if len(parts) != 3:
            raise CliError("--evidence is 'storage_key|kind|caption'")
        items.append({"storage_key": parts[0], "kind": parts[1],
                      "caption": parts[2]})
    return items


def _signals(ctx):
    n = ctx["next_summary"]
    return {"paying": ctx["user"]["paying"], "plan": ctx["user"]["plan"],
            "new_user": ctx["user"]["new_user"],
            "renders": len(ctx["renders"]), "errors": len(ctx["errors"]),
            "tool_calls": len(ctx["timeline"]),
            "requests": len(ctx["requests"]),
            **{k: n[k] for k in ("exported", "downloaded", "thumbs_up",
                                 "thumbs_down", "checkout", "paid",
                                 "cancelled", "left")}}


def cmd_verdict(a):
    from ops_loop import sessions, store
    if a.json:
        with open(a.json, encoding="utf-8") as fh:
            spec = json.load(fh)
        for k in ("verdict", "score", "reasoning", "root_cause",
                  "request_summary"):
            if getattr(a, k, None) in (None, "") and spec.get(k) is not None:
                setattr(a, k, spec[k])
        if spec.get("evidence") and not (a.evidence or a.evidence_json):
            a.evidence_json = a.json
        a.session = a.session or spec.get("session_key")
    conn = connect()
    cur = conn.cursor()
    s = find_session(cur, a.session)
    ctx = sessions.context(s, cur)
    conn.close()
    with Writer(a.dry_run) as w:
        row = store.write_verdict(
            w, s, a.verdict, a.score, a.reasoning, _evidence_items(a),
            root_cause=a.root_cause, request_summary=a.request_summary,
            signals=_signals(ctx))
    out({"review_id": row["id"], "review_count": row["review_count"],
         "session_key": s["key"], "verdict": a.verdict, "score": int(a.score),
         "dry_run": bool(a.dry_run)})
    return 0


def _mark_p0(ticket):
    try:
        with open(P0_MARKER, encoding="utf-8") as fh:
            marker = json.load(fh)
    except (OSError, ValueError):
        marker = {"tickets": []}
    ids = set(marker.get("tickets") or [])
    ids.add(ticket["id"])
    marker.update({"tickets": sorted(ids), "updated_at": _now().isoformat(),
                   "why": "a P0 ticket is open: run FIXER.md now"})
    tmp = P0_MARKER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(marker, fh, indent=1)
    os.replace(tmp, P0_MARKER)


def cmd_ticket(a):
    from ops_loop import store
    with Writer(a.dry_run) as w:
        t, action = store.open_or_attach(
            w, a.cause, a.title, a.severity, session_key=a.session,
            summary=a.summary)
    if t["severity"] == "P0" and t["status"] in ("open", "fixing") \
            and not a.dry_run:
        _mark_p0(t)
    out({"ticket_id": t["id"], "action": action, "cause_key": t["cause_key"],
         "severity": t["severity"], "status": t["status"],
         "occurrences": t["occurrences"],
         "recurrence_count": t["recurrence_count"],
         "p0_marker": P0_MARKER if t["severity"] == "P0" else None,
         "dry_run": bool(a.dry_run)})
    return 0


def cmd_fixing(a):
    from ops_loop import store
    with Writer(a.dry_run) as w:
        store.set_fixing(w, a.ticket, a.note)
    out({"ticket_id": a.ticket, "status": "fixing"})
    return 0


def cmd_close(a):
    from ops_loop import store
    with Writer(a.dry_run) as w:
        t = store.close(w, a.ticket, a.status, root_cause=a.root_cause,
                        fix_sha=a.fix_sha,
                        regression_check=a.regression_check,
                        reason=a.reason)
    _clear_p0(t["id"])
    out({"ticket_id": t["id"], "status": t["status"],
         "fix_sha": t["fix_sha"], "regression_check": t["regression_check"],
         "closed_at": t["closed_at"], "dry_run": bool(a.dry_run)})
    return 0


def _clear_p0(ticket_id):
    try:
        with open(P0_MARKER, encoding="utf-8") as fh:
            marker = json.load(fh)
    except (OSError, ValueError):
        return
    left = [i for i in marker.get("tickets") or [] if i != ticket_id]
    if left:
        marker["tickets"] = left
        with open(P0_MARKER, "w", encoding="utf-8") as fh:
            json.dump(marker, fh, indent=1)
    else:
        os.remove(P0_MARKER)


def cmd_tickets(a):
    from ops_loop import views
    conn = connect()
    got = views.tickets(conn.cursor(), status=a.status)
    conn.close()
    if a.count:
        print(got["total"])
        return 0
    if a.urls:
        keys = sorted({e["storage_key"] for g in got["groups"]
                       for t in g["tickets"] for r in t["reviews"]
                       for e in r["evidence"] if e.get("storage_key")})
        signed = presign([], keys).get("keys") or {}
        for g in got["groups"]:
            for t in g["tickets"]:
                for r in t["reviews"]:
                    for e in r["evidence"]:
                        e["url"] = signed.get(e.get("storage_key"))
    out(got)
    return 0


def sync_p0_marker(rows):
    """P0_PENDING mirrors the database: the P0 tickets the Fixer should take
    now (open, or 'fixing' with no update for 6 h). Written atomically;
    removed when there are none."""
    if not rows:
        if os.path.exists(P0_MARKER):
            os.remove(P0_MARKER)
        return None
    marker = {"tickets": sorted(r["id"] for r in rows),
              "titles": {str(r["id"]): r["title"] for r in rows},
              "updated_at": _now().isoformat(),
              "why": "a P0 ticket is open: run FIXER.md in P0 mode now"}
    tmp = P0_MARKER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(marker, fh, indent=1, default=str)
    os.replace(tmp, P0_MARKER)
    return marker


def cmd_p0(a):
    """The P0 watch's cheap check: one read-only query."""
    from ops_loop import intake
    conn = connect()
    rows = intake.pending_p0(conn.cursor())
    conn.close()
    if a.sync_marker:
        sync_p0_marker(rows)
    if a.count:
        print(len(rows))
        return 0
    out({"p0": rows, "marker": P0_MARKER if rows else None})
    return 0


def cmd_failures(a):
    """A ticket's filed failures with what the Fixer needs to start: each
    job's full error, attempts and timings, and the review session it
    belongs to."""
    from ops_loop import sessions
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tickets WHERE id = %s", (a.ticket,))
    t = cur.fetchone()
    if not t:
        raise CliError(f"no ticket {a.ticket}")
    fails = [h for h in (t["history"] or []) if isinstance(h, dict)
             and h.get("event") == "failure"][-a.limit:]
    job_ids = sorted({int(h["job"]) for h in fails if h.get("job")} |
                     {int(h["ref"].split(":")[1]) for h in fails
                      if str(h.get("ref", "")).startswith(("job:",
                                                           "request:"))})
    jobs = {}
    if job_ids:
        cur.execute("""
            SELECT id, type, state, user_id, project_id, attempts,
                   total_claims, created_at, updated_at, error,
                   payload->>'tool' AS tool, payload->>'source' AS source,
                   payload->>'quality' AS quality,
                   result->'timings' AS timings,
                   result->>'outcome' AS outcome,
                   LEFT(result->>'honesty', 1500) AS honesty
              FROM video_jobs WHERE id = ANY(%s)""", (job_ids,))
        jobs = {r["id"]: dict(r) for r in cur.fetchall()}
    for h in fails:
        jid = h.get("job") or (int(h["ref"].split(":")[1])
                               if str(h.get("ref", "")).startswith(
                                   ("job:", "request:")) else None)
        if jid and int(jid) in jobs:
            h["job_row"] = jobs[int(jid)]
        if h.get("project") and h.get("at"):
            at = datetime.fromisoformat(h["at"])
            try:
                found, _ = sessions.discover(
                    cur, at - timedelta(minutes=5), _now(),
                    project_ids=[int(h["project"])], include_unsettled=True)
                h["sessions"] = [{"key": x["key"], "settled": x["settled"]}
                                 for x in found
                                 if x["started_at"] <= at + timedelta(
                                     minutes=5)]
            except Exception as e:      # the failure itself still prints
                h["sessions_error"] = f"{type(e).__name__}: {e}"[:200]
    conn.close()
    out({"ticket": {k: t[k] for k in ("id", "cause_key", "title", "severity",
                                       "status", "occurrences",
                                       "recurrence_count", "summary",
                                       "first_seen", "last_seen")},
         "failures": fails})
    return 0


def cmd_reviews(a):
    from ops_loop import views
    conn = connect()
    got = views.sessions(conn.cursor(), days=a.days, per_page=100)
    conn.close()
    out(got)
    return 0


def cmd_whoami(a):
    out(call("GET", "/ops/review/whoami"))
    return 0


def parser():
    ap = argparse.ArgumentParser(
        description="The outcome loop's CLI (Reviewer and Fixer sessions).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending", help="settled sessions to review, with "
                                       "full context files")
    p.add_argument("--days", type=float, default=3)
    p.add_argument("--settle-minutes", type=float, default=30)
    p.add_argument("--project", type=int)
    p.add_argument("--limit", type=int)
    p.add_argument("--out")
    p.add_argument("--count", action="store_true",
                   help="print only how many are pending")
    p.add_argument("--no-urls", action="store_true")
    p.set_defaults(fn=cmd_pending)
    p = sub.add_parser("context", help="one session's full context")
    p.add_argument("--session", required=True)
    p.add_argument("--no-urls", action="store_true")
    p.set_defaults(fn=cmd_context)
    p = sub.add_parser("evidence", help="store one picture for a session")
    p.add_argument("--session", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--name")
    p.add_argument("--kind")
    p.add_argument("--caption")
    p.set_defaults(fn=cmd_evidence)
    p = sub.add_parser("fetch", help="download a stored picture or sheet")
    p.add_argument("--key", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("verdict", help="write the review of one session")
    p.add_argument("--session")
    p.add_argument("--json", help="a file with all fields")
    p.add_argument("--verdict", choices=("delivered_good", "delivered_poor",
                                         "not_delivered"))
    p.add_argument("--score", type=int)
    p.add_argument("--reasoning")
    p.add_argument("--root-cause", dest="root_cause")
    p.add_argument("--request-summary", dest="request_summary")
    p.add_argument("--evidence-json", dest="evidence_json")
    p.add_argument("--evidence", action="append",
                   help="'storage_key|kind|caption' (repeatable)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_verdict)
    p = sub.add_parser("ticket", help="open or attach the ticket for a cause")
    p.add_argument("--session", required=True)
    p.add_argument("--cause", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--severity", required=True, choices=("P0", "P1", "P2"))
    p.add_argument("--summary")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_ticket)
    p = sub.add_parser("tickets", help="tickets grouped by root cause")
    p.add_argument("--status", default="active",
                   choices=("all", "active", "open", "fixing", "fixed",
                            "wont_fix"))
    p.add_argument("--urls", action="store_true",
                   help="add download URLs for the evidence pictures")
    p.add_argument("--count", action="store_true",
                   help="print only how many tickets match")
    p.set_defaults(fn=cmd_tickets)
    p = sub.add_parser("fixing", help="mark a ticket as being fixed")
    p.add_argument("--ticket", type=int, required=True)
    p.add_argument("--note")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_fixing)
    p = sub.add_parser("close", help="close a ticket (fixed or wont_fix)")
    p.add_argument("--ticket", type=int, required=True)
    p.add_argument("--status", required=True, choices=("fixed", "wont_fix"))
    p.add_argument("--root-cause", dest="root_cause")
    p.add_argument("--fix-sha", dest="fix_sha")
    p.add_argument("--regression-check", dest="regression_check")
    p.add_argument("--reason")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_close)
    p = sub.add_parser("p0", help="P0 tickets the Fixer should take now")
    p.add_argument("--count", action="store_true")
    p.add_argument("--sync-marker", action="store_true",
                   help="write (or remove) .valmera/ops-loop/P0_PENDING")
    p.set_defaults(fn=cmd_p0)
    p = sub.add_parser("failures", help="a ticket's filed failures with "
                                        "job errors and sessions")
    p.add_argument("--ticket", type=int, required=True)
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(fn=cmd_failures)
    p = sub.add_parser("reviews", help="recent reviews")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_reviews)
    p = sub.add_parser("whoami", help="check the backend accepts this CLI")
    p.set_defaults(fn=cmd_whoami)
    return ap


def main(argv=None):
    a = parser().parse_args(argv)
    try:
        return a.fn(a)
    except FileNotFoundError as e:
        print(f"ops_review: no such file: {e.filename} (paths are relative "
              f"to {os.getcwd()})", file=sys.stderr)
        return 2
    except CliError as e:
        print(f"ops_review: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        from ops_loop.store import StoreError
        if isinstance(e, StoreError):
            print(f"ops_review: refused: {e}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
