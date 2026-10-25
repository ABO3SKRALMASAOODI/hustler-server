"""R2 retention sweeper (owner decision D4, approved 25 Oct 2026).

R2 holds about 1.15 TB of live keys and grows about 390 GB every 28 days; 65%
of it belongs to accounts that never paid (PLAN.md C1, C2.1). An R2 lifecycle
rule is prefix + age only, and previews, finals and originals share
`media/<project>/` and `originals/<project>/`, so a rule cannot say "never
paid" or "approval preview". This sweeper decides from the database instead.

THE POLICY (D4, the owner's words):
  * never-paid originals, clips and proxies: 7 days after last activity
    (``never_paid_source``: original, video_clip and proxy assets, plus the
    audio track extracted from an original, which is a proxy of it);
  * never-paid renders: 30 days after last activity (``never_paid_render``);
  * owner R&D approval previews: 14 days (``owner_approval_preview``: user
    60's approval-quality previews older than 14 days, in projects with no
    activity for 14 days, never a project's newest render);
  * never touch paying or active data.

WHAT "NEVER TOUCH" MEANS HERE, conservatively:
  * Protected accounts: any `payments` row of any status (a refund or a
    failed renewal still means a customer), a subscription or card-trial id,
    any billing or trial status, a paid plan or the subscribed flag, and the
    internal accounts. Only user 60's approval
    previews are ever selected from a protected account.
  * Activity is the ACCOUNT's newest trace anywhere: last_seen_at, any job,
    chat message, client event, MCP grant or token use, project, asset or
    EDL. A never-paid account active in any project keeps everything.
  * A storage key is deleted only when EVERY asset row that points at it is
    selected (Shorts children and re-uploads share keys); one kept reference
    keeps the object.
  * Rows already purged (``meta.r2_purged_at``) are skipped.

MODES (env R2_RETENTION_MODE): ``off``; ``dry_run`` selects and writes the
manifest (local file in the CLI, R2 + app_kv when scheduled) and deletes
nothing; ``live`` (the default since 25 Oct 2026, after the read-only dry
run and the scheduled dry run selected the same 683 GB in 7,750 keys,
manifest retention/manifests/2026-10-25T113926-dry_run.json.gz) deletes
the selected objects in batches of
1,000, marks their asset rows ``meta.r2_purged_at`` (the files are gone, the
row still describes them) and writes the manifest of deleted keys to R2
(`retention/manifests/`) and a summary to app_kv `r2_retention_last`.
A live run refuses to start above MAX_RUN_GB / MAX_RUN_KEYS, so a selection
bug cannot empty the bucket.

    # read-only dry run against production, from backend/ (no R2 access):
    DATABASE_URL=... python r2_retention.py --dry-run --manifest out.json
"""

import datetime as dt
import gzip
import json
import os
import sys
import threading

POLICY_VERSION = "d4-2026-10-25"
SOURCE_KINDS = ("original", "video_clip", "proxy", "audio")
RENDER_KINDS = ("render",)
SOURCE_DAYS = 7
RENDER_DAYS = 30
OWNER_PREVIEW_DAYS = 14
OWNER_USER_ID = 60
# PLAN.md: internal accounts, excluded everywhere (60 is the owner's agents).
INTERNAL_USER_IDS = (22, 28, 29, 45, 46, 49, 60, 61, 62, 68, 108, 398, 1058)
CATEGORIES = ("never_paid_source", "never_paid_render",
              "owner_approval_preview")
MAX_RUN_GB = float(os.getenv("R2_RETENTION_MAX_RUN_GB", "900"))
MAX_RUN_KEYS = int(os.getenv("R2_RETENTION_MAX_RUN_KEYS", "40000"))
MANIFEST_PREFIX = "retention/manifests"
KV_KEY = "r2_retention_last"
LOCK_ID = 918273651
BATCH = 1000
MODES = ("off", "dry_run", "live")
DEFAULT_MODE = "live"


def mode():
    value = os.getenv("R2_RETENTION_MODE", DEFAULT_MODE).strip().lower()
    return value if value in MODES else "off"


# One row per storage key: its bytes, its category when EVERY reference is
# selected, else the reason it is kept. %(now)s is the run's clock.
SELECTION_SQL = """
WITH protected AS (
    SELECT user_id AS id FROM payments WHERE user_id IS NOT NULL
    UNION
    SELECT id FROM users
     WHERE COALESCE(is_subscribed, 0) <> 0
        OR subscription_id IS NOT NULL
        OR COALESCE(plan, 'free') <> 'free'
        OR billing_status IS NOT NULL
        OR trial_status IS NOT NULL
        OR trial_subscription_id IS NOT NULL
    UNION
    SELECT unnest(%(internal)s::int[])
),
activity AS (
    SELECT u.id,
           GREATEST(u.created_at, u.last_seen_at, j.t, e.t, c.t, g.t, k.t,
                    p.t, a.t, d.t) AS last_active
      FROM users u
      LEFT JOIN (SELECT user_id, MAX(updated_at) AS t FROM video_jobs
                  GROUP BY user_id) j ON j.user_id = u.id
      LEFT JOIN (SELECT user_id, MAX(created_at) AS t FROM client_events
                  WHERE user_id IS NOT NULL GROUP BY user_id) e
             ON e.user_id = u.id
      LEFT JOIN (SELECT s.user_id, MAX(m.created_at) AS t
                   FROM chat_messages m JOIN chat_sessions s
                     ON s.id = m.session_id
                  GROUP BY s.user_id) c ON c.user_id = u.id
      LEFT JOIN (SELECT user_id, MAX(GREATEST(created_at, last_used_at)) AS t
                   FROM mcp_oauth_grants GROUP BY user_id) g
             ON g.user_id = u.id
      LEFT JOIN (SELECT user_id, MAX(GREATEST(created_at, last_used_at)) AS t
                   FROM mcp_tokens GROUP BY user_id) k ON k.user_id = u.id
      LEFT JOIN (SELECT user_id, MAX(created_at) AS t FROM projects
                  GROUP BY user_id) p ON p.user_id = u.id
      LEFT JOIN (SELECT pr.user_id, MAX(x.created_at) AS t
                   FROM assets x JOIN projects pr ON pr.id = x.project_id
                  GROUP BY pr.user_id) a ON a.user_id = u.id
      LEFT JOIN (SELECT pr.user_id, MAX(x.created_at) AS t
                   FROM edls x JOIN projects pr ON pr.id = x.project_id
                  GROUP BY pr.user_id) d ON d.user_id = u.id
),
project_activity AS (
    SELECT pr.id,
           GREATEST(pr.created_at, j.t, a.t, d.t) AS last_active
      FROM projects pr
      LEFT JOIN (SELECT project_id, MAX(updated_at) AS t FROM video_jobs
                  GROUP BY project_id) j ON j.project_id = pr.id
      LEFT JOIN (SELECT project_id, MAX(created_at) AS t FROM assets
                  GROUP BY project_id) a ON a.project_id = pr.id
      LEFT JOIN (SELECT project_id, MAX(created_at) AS t FROM edls
                  GROUP BY project_id) d ON d.project_id = pr.id
),
newest_render AS (
    SELECT project_id, MAX(id) AS id FROM assets
     WHERE kind = 'render' GROUP BY project_id
),
rows AS (
    SELECT x.id, x.storage_key, x.kind, COALESCE(x.bytes, 0) AS bytes,
           x.project_id, pr.user_id,
           CASE
             WHEN x.meta ? 'r2_purged_at' THEN NULL
             WHEN pr.user_id = %(owner)s
                  AND x.kind = 'render'
                  AND x.meta->>'variant' = 'preview'
                  AND x.meta->>'quality' = 'approval'
                  AND x.created_at < %(now)s - make_interval(days => %(owner_days)s)
                  AND pa.last_active < %(now)s - make_interval(days => %(owner_days)s)
                  AND x.id <> nr.id
               THEN 'owner_approval_preview'
             WHEN pu.id IS NOT NULL THEN NULL
             WHEN x.kind = ANY(%(source_kinds)s)
                  AND ua.last_active < %(now)s - make_interval(days => %(source_days)s)
               THEN 'never_paid_source'
             WHEN x.kind = ANY(%(render_kinds)s)
                  AND ua.last_active < %(now)s - make_interval(days => %(render_days)s)
               THEN 'never_paid_render'
           END AS category,
           CASE
             WHEN x.meta ? 'r2_purged_at' THEN 'already_purged'
             WHEN pr.user_id = %(owner)s THEN 'owner'
             WHEN pu.id = ANY(%(internal)s::int[]) THEN 'internal'
             WHEN pu.id IS NOT NULL THEN 'paying'
             WHEN NOT (x.kind = ANY(%(source_kinds)s)
                       OR x.kind = ANY(%(render_kinds)s))
               THEN 'never_paid_kind_not_in_policy'
             ELSE 'never_paid_recently_active'
           END AS kept_reason
      FROM assets x
      JOIN projects pr ON pr.id = x.project_id
      LEFT JOIN protected pu ON pu.id = pr.user_id
      LEFT JOIN activity ua ON ua.id = pr.user_id
      LEFT JOIN project_activity pa ON pa.id = x.project_id
      LEFT JOIN newest_render nr ON nr.project_id = x.project_id
     WHERE x.storage_key IS NOT NULL AND x.storage_key <> ''
)
SELECT storage_key,
       MAX(bytes) AS bytes,
       CASE WHEN BOOL_AND(category IS NOT NULL) THEN MIN(category) END
           AS category,
       CASE WHEN BOOL_AND(category IS NOT NULL) THEN NULL
            WHEN BOOL_OR(category IS NOT NULL) THEN 'shared_with_kept'
            ELSE MIN(kept_reason) END AS kept_reason,
       ARRAY_AGG(DISTINCT kind) AS kinds,
       ARRAY_AGG(id ORDER BY id) AS asset_ids,
       ARRAY_AGG(DISTINCT project_id) AS project_ids,
       ARRAY_AGG(DISTINCT user_id) AS user_ids
  FROM rows
 GROUP BY storage_key
"""

PROTECTED_SQL = """
    SELECT user_id AS id FROM payments WHERE user_id IS NOT NULL
    UNION
    SELECT id FROM users
     WHERE COALESCE(is_subscribed, 0) <> 0 OR subscription_id IS NOT NULL
        OR COALESCE(plan, 'free') <> 'free' OR billing_status IS NOT NULL
        OR trial_status IS NOT NULL OR trial_subscription_id IS NOT NULL
"""


def _params(now):
    return {"now": now, "internal": list(INTERNAL_USER_IDS),
            "owner": OWNER_USER_ID, "owner_days": OWNER_PREVIEW_DAYS,
            "source_kinds": list(SOURCE_KINDS), "source_days": SOURCE_DAYS,
            "render_kinds": list(RENDER_KINDS), "render_days": RENDER_DAYS}


def _row(r):
    return dict(r) if not isinstance(r, dict) else r


def select(conn, now=None):
    """Every storage key with its category (selected) or kept reason."""
    now = now or dt.datetime.now(dt.timezone.utc)
    with conn.cursor() as cur:
        cur.execute(SELECTION_SQL, _params(now))
        rows = [_row(r) for r in cur.fetchall()]
        cur.execute(PROTECTED_SQL)
        protected = {int(_row(r)["id"]) for r in cur.fetchall()}
    return rows, protected


def verify(selected, protected):
    """Defense in depth, outside SQL: no selected key may name a protected
    account, except the owner's own approval previews."""
    bad = []
    for row in selected:
        users = {int(u) for u in (row.get("user_ids") or []) if u is not None}
        if row["category"] == "owner_approval_preview":
            if users != {OWNER_USER_ID}:
                bad.append(row["storage_key"])
        elif users & (protected | set(INTERNAL_USER_IDS)):
            bad.append(row["storage_key"])
        elif row["category"] not in CATEGORIES:
            bad.append(row["storage_key"])
    return bad


def report(rows):
    """GB and key counts per selected category and per kept reason."""
    def bucket():
        return {"keys": 0, "bytes": 0}
    selected, kept = {}, {}
    for row in rows:
        target = (selected.setdefault(row["category"], bucket())
                  if row.get("category") else
                  kept.setdefault(row.get("kept_reason") or "unknown",
                                  bucket()))
        target["keys"] += 1
        target["bytes"] += int(row.get("bytes") or 0)
    by_kind = {}
    for row in rows:
        if not row.get("category"):
            continue
        for kind in row.get("kinds") or []:
            entry = by_kind.setdefault(f"{row['category']}:{kind}", bucket())
            entry["keys"] += 1
            entry["bytes"] += int(row.get("bytes") or 0)
            break

    def gb(table):
        return {k: {"keys": v["keys"], "gb": round(v["bytes"] / 1e9, 2)}
                for k, v in sorted(table.items())}
    total = sum(v["bytes"] for v in selected.values())
    kept_total = sum(v["bytes"] for v in kept.values())
    return {"selected": gb(selected), "selected_by_kind": gb(by_kind),
            "kept": gb(kept),
            "selected_gb": round(total / 1e9, 2),
            "selected_keys": sum(v["keys"] for v in selected.values()),
            "kept_gb": round(kept_total / 1e9, 2),
            "total_gb": round((total + kept_total) / 1e9, 2)}


def manifest(rows, mode_, now, summary, deleted=None, errors=None):
    keys = [{"key": r["storage_key"], "bytes": int(r.get("bytes") or 0),
             "category": r["category"], "kinds": r.get("kinds"),
             "asset_ids": r.get("asset_ids"),
             "project_ids": r.get("project_ids"),
             "user_ids": r.get("user_ids")}
            for r in rows if r.get("category")]
    if deleted is not None:
        done = set(deleted)
        keys = [k for k in keys if k["key"] in done]
    return {"policy": POLICY_VERSION, "mode": mode_,
            "generated_at": now.isoformat(), "summary": summary,
            "errors": errors or [], "keys": keys}


def _delete_batch(client, bucket, keys):
    """(deleted, errors) for one DeleteObjects call of at most 1,000 keys."""
    resp = client.delete_objects(
        Bucket=bucket,
        Delete={"Objects": [{"Key": k} for k in keys], "Quiet": False})
    deleted = [d["Key"] for d in resp.get("Deleted") or []]
    errors = [{"key": e.get("Key"), "code": e.get("Code"),
               "message": str(e.get("Message"))[:200]}
              for e in resp.get("Errors") or []]
    return deleted, errors


def _mark_purged(conn, keys, category_of, now):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE assets AS a
                  SET meta = a.meta || jsonb_build_object(
                        'r2_purged_at', %s::text,
                        'r2_purge_policy', %s::text,
                        'r2_purge_category', c.category)
                 FROM (SELECT unnest(%s::text[]) AS storage_key,
                              unnest(%s::text[]) AS category) c
                WHERE a.storage_key = c.storage_key
                  AND NOT (a.meta ? 'r2_purged_at')""",
            (now.isoformat(), POLICY_VERSION, list(keys),
             [category_of[k] for k in keys]))
    conn.commit()


def run(conn, mode_, storage_mod=None, now=None, write_manifest=None,
        save_summary=None):
    """One sweep. Returns the summary (and never deletes in dry_run)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if mode_ not in ("dry_run", "live"):
        return {"mode": mode_, "skipped": "off"}
    rows, protected = select(conn, now)
    selected = [r for r in rows if r.get("category")]
    summary = dict(report(rows), mode=mode_, policy=POLICY_VERSION,
                   at=now.isoformat())
    bad = verify(selected, protected)
    if bad:
        summary["refused"] = f"{len(bad)} selected key(s) name a protected account"
        summary["refused_sample"] = bad[:5]
        if save_summary:
            save_summary(summary)
        return summary
    selected_bytes = sum(int(r.get("bytes") or 0) for r in selected)
    if mode_ == "live" and (selected_bytes > MAX_RUN_GB * 1e9
                            or len(selected) > MAX_RUN_KEYS):
        summary["refused"] = (
            f"selection {summary['selected_gb']} GB / "
            f"{summary['selected_keys']} keys exceeds the per-run cap "
            f"({MAX_RUN_GB} GB / {MAX_RUN_KEYS} keys)")
        if save_summary:
            save_summary(summary)
        return summary
    deleted, errors = None, []
    if mode_ == "live":
        client, bucket = storage_mod.client(), storage_mod.bucket()
        category_of = {r["storage_key"]: r["category"] for r in selected}
        deleted = []
        keys = [r["storage_key"] for r in selected]
        for i in range(0, len(keys), BATCH):
            batch = keys[i:i + BATCH]
            try:
                done, failed = _delete_batch(client, bucket, batch)
            except Exception as exc:  # noqa: BLE001 — stop, report, keep rows
                errors.append({"batch": i // BATCH,
                               "error": f"{type(exc).__name__}: "
                                        f"{str(exc)[:200]}"})
                break
            errors.extend(failed)
            if done:
                _mark_purged(conn, done, category_of, now)
                deleted.extend(done)
        gone = set(deleted)
        summary["deleted_keys"] = len(deleted)
        summary["deleted_gb"] = round(sum(
            int(r.get("bytes") or 0) for r in selected
            if r["storage_key"] in gone) / 1e9, 2)
        summary["errors"] = len(errors)
    doc = manifest(rows, mode_, now, summary, deleted=deleted, errors=errors)
    if write_manifest:
        try:
            summary["manifest"] = write_manifest(doc)
        except Exception as exc:  # noqa: BLE001 — the summary still lands
            summary["manifest_error"] = f"{type(exc).__name__}: {exc}"[:300]
    if save_summary:
        save_summary(summary)
    return summary


# ── Scheduled run (web process) ─────────────────────────────────────────────

def _r2_manifest_writer(storage_mod):
    def write(doc):
        stamp = doc["generated_at"].replace(":", "").replace("+", "Z")[:17]
        key = f"{MANIFEST_PREFIX}/{stamp}-{doc['mode']}.json.gz"
        body = gzip.compress(json.dumps(doc, default=str).encode("utf-8"))
        storage_mod.client().put_object(
            Bucket=storage_mod.bucket(), Key=key, Body=body,
            ContentType="application/json", ContentEncoding="gzip")
        return key
    return write


def _kv_saver(conn):
    def save(summary):
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO app_kv (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key) DO UPDATE
                     SET value = EXCLUDED.value, updated_at = NOW()""",
                (KV_KEY, json.dumps(summary, default=str)))
        conn.commit()
    return save


def _ran_recently(conn, mode_, hours=20):
    with conn.cursor() as cur:
        cur.execute("SELECT value, updated_at FROM app_kv WHERE key = %s",
                    (KV_KEY,))
        row = cur.fetchone()
    if not row:
        return False
    row = _row(row)
    try:
        last = json.loads(row["value"])
    except (TypeError, ValueError):
        return False
    age = dt.datetime.now(dt.timezone.utc) - row["updated_at"]
    return last.get("mode") == mode_ and age < dt.timedelta(hours=hours) \
        and not last.get("refused")


def scheduled_tick():
    """The daily run: advisory-locked across gunicorn workers, at most once
    per 20 h per mode."""
    import psycopg2
    from psycopg2.extras import RealDictCursor
    import storage

    mode_ = mode()
    if mode_ == "off" or not storage.is_configured():
        return {"skipped": mode_ if mode_ == "off" else "storage unconfigured"}
    conn = psycopg2.connect(os.environ.get("DIRECT_DATABASE_URL")
                            or os.environ["DATABASE_URL"],
                            cursor_factory=RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS got", (LOCK_ID,))
            got = _row(cur.fetchone())["got"]
        conn.commit()
        if not got:
            return {"skipped": "another worker holds the lock"}
        try:
            if _ran_recently(conn, mode_):
                return {"skipped": "ran in the last 20 h"}
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '300s'")
            return run(conn, mode_, storage_mod=storage,
                       write_manifest=_r2_manifest_writer(storage),
                       save_summary=_kv_saver(conn))
        finally:
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            conn.commit()
    finally:
        conn.close()


_scheduler = None
_lock = threading.Lock()


def start_scheduler(app):
    """Daily sweep, first run 15 minutes after boot. Started once per
    gunicorn worker; the advisory lock and the 20 h guard make the rest
    no-ops."""
    global _scheduler
    if mode() == "off":
        app.logger.info("r2 retention sweeper off")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except Exception:                    # pragma: no cover
        app.logger.warning("apscheduler missing: r2 retention not started")
        return
    with _lock:
        if _scheduler is not None:
            return

        def job():
            try:
                out = scheduled_tick()
                app.logger.info("r2 retention: %s", {
                    k: out.get(k) for k in (
                        "mode", "skipped", "refused", "selected_gb",
                        "selected_keys", "deleted_gb", "deleted_keys",
                        "errors", "manifest")})
            except Exception as exc:     # never let the scheduler thread die
                app.logger.error("r2 retention tick failed: %s", exc)

        sched = BackgroundScheduler(daemon=True, timezone="UTC")
        sched.add_job(job, "interval", hours=24, id="r2_retention",
                      replace_existing=True,
                      next_run_time=dt.datetime.now(dt.timezone.utc)
                      + dt.timedelta(minutes=15))
        sched.start()
        _scheduler = sched
        app.logger.info("r2 retention scheduler started (%s)", mode())


# ── CLI: read-only dry run ─────────────────────────────────────────────────

def main(argv=None):
    import argparse
    import psycopg2
    from psycopg2.extras import RealDictCursor

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", required=True,
                        help="the CLI only ever dry-runs")
    parser.add_argument("--manifest", help="write the manifest JSON here")
    args = parser.parse_args(argv)
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"], cursor_factory=RealDictCursor,
        options="-c default_transaction_read_only=on "
                "-c statement_timeout=300000")
    try:
        written = {}

        def write(doc):
            if args.manifest:
                with open(args.manifest, "w", encoding="utf-8") as handle:
                    json.dump(doc, handle, default=str)
                written["path"] = args.manifest
            return args.manifest

        summary = run(conn, "dry_run", write_manifest=write)
    finally:
        conn.close()
    json.dump(summary, sys.stdout, indent=2, default=str)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
