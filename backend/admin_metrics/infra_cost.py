"""What the Cloudflare executor fleet really costs, modeled from our own ledger.

No Cloudflare analytics token exists, so the bill is rebuilt from
`remote_executions` (every queue-backed call: lane, call id and its times)
the way the Worker spends it (strategy-1025 costs §1.2, red team split.py):

* each call lands on the Durable Object shard the Worker's FNV-1a routing
  picks (`shardName`, worker/cloudflare/src/index.ts; remote._cloudflare_shard_index);
* a shard is awake from its first call until the lane's `sleepAfter` after
  its last one; overlapping calls share one awake span;
* awake time is billed at the lane's PROVISIONED memory and disk, CPU at the
  measured `container_cpu_s`;
* the ledger misses synchronous child calls, deploy probes and idle CPU in
  awake containers, so the modeled compute is multiplied by CALIBRATION
  (1.43: $136.9 modeled against $140.5 of dashboard compute, 7-24 Oct 2026);
* R2 is the stored bytes of distinct live asset keys, accrued per day;
  keys the retention sweep deleted (meta.r2_purged_at) stop at their purge.

The admin's old "executor ceiling" summed per-job telemetry and showed $71.58
for 30 days against about $190 of real spend; this model is within 3% of the
dashboard. Change SHAPES / TAIL_SCHEDULE in the same commit as any change to
an instance type or `sleepAfter` in wrangler.jsonc / index.ts.
"""
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from admin_metrics import db

OWNER_ID = 60
CALIBRATION = 1.43
# Cloudflare Containers list prices (per second).
MEM_USD_PER_GIB_S = 0.0000025
DISK_USD_PER_GB_S = 0.00000007
CPU_USD_PER_VCPU_S = 0.000020
R2_USD_PER_GB_MONTH = 0.015
R2_FREE_GB = 10.0
# Shards per lane (max_instances) and provisioned (memory GiB, disk GB).
SHARDS = {"interactive": 20, "batch": 8, "agent": 5, "mcp": 20, "shorts": 8}
SHAPES = {"interactive": (8, 16), "mcp": (4, 8), "batch": (12, 20),
          "agent": (4, 8), "shorts": (4, 8)}
# sleepAfter per lane, by the date it took effect (UTC). Append a row when a
# release changes it; the model uses the row in force at each call.
TAIL_SCHEDULE = (
    (datetime(2026, 7, 1, tzinfo=timezone.utc),
     {"interactive": 240, "mcp": 240, "batch": 60, "agent": 60,
      "shorts": 60}),
    # d09b55ac (strategy 1025 cost cuts): MCP 90 s, every other lane 30 s;
    # its executor release rolled out about 12:00 UTC on 25 Oct 2026.
    (datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc),
     {"interactive": 30, "mcp": 90, "batch": 30, "agent": 30,
      "shorts": 30}),
)
# The Cloudflare billing cycle renews on this day of the month.
CYCLE_DAY = 7

_MCP_KEY = re.compile(r"^cf-mcp-p([0-9]+)-")
_PREVIEW_KEY = re.compile(r"^cf-preview-p([0-9]+)-")
_GROUP_KEY = re.compile(r"^cf-render-g([0-9]+-[01])-")


def fnv1a(text):
    v = 2166136261
    for ch in text:
        v ^= ord(ch)
        v = (v * 16777619) & 0xFFFFFFFF
    return v


def shard(lane, call_id):
    """The Durable Object shard index the Worker routes this call to."""
    cid = str(call_id or "")
    key = cid
    group = _GROUP_KEY.match(cid) if lane in ("batch", "interactive") else None
    project = (_MCP_KEY.match(cid) if lane == "mcp" else
               _PREVIEW_KEY.match(cid) if lane == "interactive" else None)
    if group:
        key = f"render-group:{group.group(1)}"
    elif project:
        key = f"project:{project.group(1)}"
    v = fnv1a(key)
    if lane == "batch" and cid.startswith("cf-faces-"):
        return 7 - v % 2
    return v % SHARDS[lane]


def tail_for(lane, at):
    tails = TAIL_SCHEDULE[0][1]
    for since, row in TAIL_SCHEDULE:
        if at >= since.timestamp():
            tails = row
    return tails.get(lane, 60)


def cycle_bounds(now):
    """[start, end) of the Cloudflare billing cycle containing `now`."""
    start = now.replace(day=CYCLE_DAY, hour=0, minute=0, second=0,
                        microsecond=0)
    if now < start:
        start = (start.replace(day=1) - timedelta(days=1)).replace(
            day=CYCLE_DAY)
    nxt = (start + timedelta(days=32)).replace(day=CYCLE_DAY)
    return start, nxt


def _day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).date()


def _spread(costs, start, end, usd_per_s):
    """Add an awake span to the UTC days it covers."""
    t = start
    while t < end:
        d = _day(t)
        midnight = datetime(d.year, d.month, d.day,
                            tzinfo=timezone.utc).timestamp() + 86400
        piece = min(end, midnight) - t
        costs[d] += piece * usd_per_s
        t += piece


def model(rows, who=None):
    """{day: {lane: usd}} for rows (lane, call_id, sub, end, user_id, cpu_s),
    calibrated. `who` limits the calls priced ('owner' / 'customers'), as if
    the others did not exist (the red team's counterfactual split)."""
    per = defaultdict(list)
    cpu = defaultdict(lambda: defaultdict(float))
    for lane, call_id, sub, end, uid, cpu_s in rows:
        if lane not in SHARDS or sub is None:
            continue
        if who == "owner" and uid != OWNER_ID:
            continue
        if who == "customers" and (uid is None or uid == OWNER_ID):
            continue
        per[(lane, shard(lane, call_id))].append((sub, max(end or sub, sub)))
        cpu[_day(sub)][lane] += float(cpu_s or 0) * CPU_USD_PER_VCPU_S
    out = defaultdict(lambda: defaultdict(float))
    for (lane, _shard), spans in per.items():
        spans.sort()
        mem, disk = SHAPES[lane]
        rate = mem * MEM_USD_PER_GIB_S + disk * DISK_USD_PER_GB_S
        awake = defaultdict(float)
        a = b = None
        for s, e in spans:
            e2 = e + tail_for(lane, s)
            if a is None:
                a, b = s, e2
            elif s <= b:
                b = max(b, e2)
            else:
                _spread(awake, a, b, rate)
                a, b = s, e2
        if a is not None:
            _spread(awake, a, b, rate)
        for d, usd in awake.items():
            out[d][lane] += usd
    for d, lanes in cpu.items():
        for lane, usd in lanes.items():
            out[d][lane] += usd
    return {d: {lane: usd * CALIBRATION for lane, usd in lanes.items()}
            for d, lanes in out.items()}


ROWS_SQL = """
    SELECT r.function_name AS lane, r.call_id,
           EXTRACT(EPOCH FROM r.submitted_at) AS sub,
           EXTRACT(EPOCH FROM COALESCE(r.completed_at, r.last_observed_at,
                                       r.started_at, r.submitted_at)) AS fin,
           j.user_id,
           COALESCE(NULLIF(j.result->'timings'->>'container_cpu_s', '')
                    ::float, 0) AS cpu_s
      FROM remote_executions r LEFT JOIN video_jobs j ON j.id = r.job_id
     WHERE r.provider = 'cloudflare' AND r.submitted_at >= %s"""


def fetch_rows(cur, since):
    cur.execute(ROWS_SQL, (since,))
    return [(r["lane"], r["call_id"], float(r["sub"]),
             float(r["fin"]) if r["fin"] is not None else None,
             r["user_id"], r["cpu_s"]) for r in cur.fetchall()]


# One row per storage key (Shorts children and re-uploads share keys). A key
# the retention sweep deleted (r2_retention.py marks every row that points at
# it ``meta.r2_purged_at``) is no longer stored: it leaves the stored total,
# stops accruing on its purge time, and its bytes count as REMOVED in the week
# it was swept. "Added" is every key created in a window, swept since or not,
# so last week's upload volume does not shrink because the sweep ran later.
R2_SQL = """
    WITH rows AS (
      SELECT a.id, a.storage_key, COALESCE(a.bytes, 0) AS bytes,
             a.created_at, p.user_id,
             COALESCE(a.meta, '{}'::jsonb) ? 'r2_purged_at' AS purged,
             CASE WHEN a.meta->>'r2_purged_at'
                       ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'
                  THEN (a.meta->>'r2_purged_at')::timestamptz END
                 AS purged_at
        FROM assets a LEFT JOIN projects p ON p.id = a.project_id
       WHERE a.storage_key IS NOT NULL AND a.storage_key <> ''),
    keys AS (
      SELECT storage_key,
             (ARRAY_AGG(bytes ORDER BY id))[1] AS bytes,
             MIN(created_at) AS created_at,
             (ARRAY_AGG(user_id ORDER BY id))[1] AS user_id,
             BOOL_OR(purged) AS purged,
             MIN(purged_at) AS purged_at
        FROM rows GROUP BY storage_key),
    payers AS (SELECT DISTINCT p.user_id FROM payments p
                WHERE p.status IN ('completed','paid') AND p.amount_cents > 0)
    SELECT CASE WHEN k.user_id = %(owner)s THEN 'owner'
                WHEN k.user_id IN (SELECT user_id FROM payers) THEN 'payers'
                ELSE 'never_paid' END AS population,
           COALESCE(SUM(k.bytes) FILTER (WHERE NOT k.purged), 0) AS bytes,
           COUNT(*) FILTER (WHERE NOT k.purged) AS keys,
           COALESCE(SUM(k.bytes) FILTER (
               WHERE k.created_at >= %(week)s), 0) AS bytes_7d,
           COALESCE(SUM(k.bytes) FILTER (
               WHERE k.created_at >= %(prev)s AND k.created_at < %(week)s),
               0) AS bytes_prev_7d,
           COALESCE(SUM(k.bytes) FILTER (
               WHERE k.purged AND k.purged_at >= %(week)s), 0)
               AS purged_7d,
           COALESCE(SUM(k.bytes) FILTER (
               WHERE k.purged AND k.purged_at >= %(prev)s
                 AND k.purged_at < %(week)s), 0) AS purged_prev_7d,
           COALESCE(SUM(k.bytes) FILTER (WHERE k.purged), 0) AS purged_total,
           COALESCE(SUM(k.bytes * GREATEST(0, EXTRACT(EPOCH FROM (
               CASE WHEN k.purged
                    THEN LEAST(COALESCE(k.purged_at, k.created_at),
                               %(now)s)
                    ELSE %(now)s END
               - GREATEST(k.created_at, %(cycle)s))) / 86400.0))
               FILTER (WHERE k.created_at < %(now)s), 0) AS byte_days_cycle
      FROM keys k GROUP BY 1"""

POPULATIONS = ("never_paid", "owner", "payers")


def _gb(value):
    return round(float(value or 0) / 1e9, 1)


def r2(cur, now, cycle_start):
    """Stored GB by population (live keys only), and the week's growth as
    added minus removed by the retention sweep."""
    cur.execute(R2_SQL, {"owner": OWNER_ID, "now": now,
                         "week": now - timedelta(days=7),
                         "prev": now - timedelta(days=14),
                         "cycle": cycle_start})
    pops, byte_days = {}, 0.0
    for r in cur.fetchall():
        added, removed = float(r["bytes_7d"]), float(r["purged_7d"])
        added_prev = float(r["bytes_prev_7d"])
        removed_prev = float(r["purged_prev_7d"])
        pops[r["population"]] = {
            "gb": _gb(r["bytes"]), "keys": int(r["keys"] or 0),
            "added_7d_gb": _gb(added), "removed_7d_gb": _gb(removed),
            "net_7d_gb": _gb(added - removed),
            "added_prev_7d_gb": _gb(added_prev),
            "removed_prev_7d_gb": _gb(removed_prev),
            "net_prev_7d_gb": _gb(added_prev - removed_prev),
            "removed_total_gb": _gb(r["purged_total"])}
        byte_days += float(r["byte_days_cycle"] or 0)
    for name in POPULATIONS:
        pops.setdefault(name, {
            "gb": 0.0, "keys": 0, "added_7d_gb": 0.0, "removed_7d_gb": 0.0,
            "net_7d_gb": 0.0, "added_prev_7d_gb": 0.0,
            "removed_prev_7d_gb": 0.0, "net_prev_7d_gb": 0.0,
            "removed_total_gb": 0.0})

    def total(field):
        return round(sum(p[field] for p in pops.values()), 1)
    stored = total("gb")
    gb_months = byte_days / 1e9 / 30.0
    return {"total_gb": stored,
            "added_7d_gb": total("added_7d_gb"),
            "removed_7d_gb": total("removed_7d_gb"),
            "net_7d_gb": total("net_7d_gb"),
            "added_prev_7d_gb": total("added_prev_7d_gb"),
            "removed_prev_7d_gb": total("removed_prev_7d_gb"),
            "net_prev_7d_gb": total("net_prev_7d_gb"),
            "removed_total_gb": total("removed_total_gb"),
            "by_population": pops,
            "last_sweep": last_sweep(cur),
            "monthly_usd_at_current_size": round(
                max(0.0, stored - R2_FREE_GB) * R2_USD_PER_GB_MONTH, 2),
            "cycle_gb_months": round(gb_months, 1),
            "cycle_usd": round(max(0.0, gb_months - R2_FREE_GB)
                               * R2_USD_PER_GB_MONTH, 2),
            "method": ("distinct live asset keys; keys the retention sweep "
                       "deleted (meta.r2_purged_at) are not stored and are "
                       "counted as removed in the week they were swept")}


def last_sweep(cur):
    """The retention sweep's own summary (app_kv r2_retention_last): when it
    ran and what it kept and deleted, beside the board's own count."""
    import json
    try:
        cur.execute("SELECT value, updated_at FROM app_kv WHERE key = %s",
                    ("r2_retention_last",))
        row = cur.fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        value = json.loads(row["value"]) if isinstance(row["value"], str) \
            else dict(row["value"] or {})
    except (TypeError, ValueError):
        return None
    return {"at": value.get("at"), "mode": value.get("mode"),
            "deleted_gb": value.get("deleted_gb"),
            "deleted_keys": value.get("deleted_keys"),
            "kept_gb": value.get("kept_gb"),
            "selected_gb": value.get("selected_gb"),
            "refused": value.get("refused"),
            "errors": value.get("errors")}


def _sum(days, start, end):
    return sum(sum(lanes.values()) for d, lanes in days.items()
               if start <= d < end)


def summarize(rows, now, r2_block=None):
    """The cost numbers the cockpit and the Costs tab show."""
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    allm, owner, cust = model(rows), model(rows, "owner"), model(rows, "customers")
    today = now.date()
    yesterday = today - timedelta(days=1)
    cstart, cend = cycle_bounds(now)
    cycle_days = (cend - cstart).days
    elapsed = max((now - cstart).total_seconds() / 86400.0, 1e-6)
    compute_cycle = _sum(allm, cstart.date(), today + timedelta(days=1))
    r2_cycle = (r2_block or {}).get("cycle_usd") or 0.0
    lanes = defaultdict(float)
    for d, per in allm.items():
        if d >= today - timedelta(days=30):
            for lane, usd in per.items():
                lanes[lane] += usd
    last7 = _sum(allm, today - timedelta(days=7), today)
    owner7 = _sum(owner, today - timedelta(days=7), today)
    cust7 = _sum(cust, today - timedelta(days=7), today)
    daily = [{"day": (today - timedelta(days=i)).isoformat(),
              "usd": round(sum((allm.get(today - timedelta(days=i)) or {})
                               .values()), 2),
              "owner_usd": round(sum((owner.get(today - timedelta(days=i))
                                      or {}).values()), 2)}
             for i in range(14, 0, -1)]
    return {
        "method": ("shard-union model over remote_executions × "
                   f"{CALIBRATION} (calibrated to the Cloudflare dashboard, "
                   "7-24 Oct 2026); R2 from stored asset bytes"),
        "calibration": CALIBRATION,
        "yesterday_usd": round(sum((allm.get(yesterday) or {}).values()), 2),
        "yesterday_owner_usd": round(sum((owner.get(yesterday) or {})
                                         .values()), 2),
        "last_7d_usd": round(last7, 2),
        "last_7d_owner_share_pct": (round(100.0 * owner7 / (owner7 + cust7), 1)
                                    if owner7 + cust7 > 0 else None),
        "last_7d_customers_usd": round(cust7, 2),
        "last_30d_compute_usd": round(_sum(allm, today - timedelta(days=30),
                                           today + timedelta(days=1)), 2),
        "last_30d_owner_usd": round(_sum(owner, today - timedelta(days=30),
                                         today + timedelta(days=1)), 2),
        "last_30d_customers_usd": round(_sum(cust, today - timedelta(days=30),
                                             today + timedelta(days=1)), 2),
        "by_lane_30d_usd": {k: round(v, 2) for k, v in sorted(lanes.items())},
        "cycle": {"start": cstart.date().isoformat(),
                  "end": (cend - timedelta(days=1)).date().isoformat(),
                  "days": cycle_days, "elapsed_days": round(elapsed, 2),
                  "compute_usd": round(compute_cycle, 2),
                  "r2_usd": round(r2_cycle, 2),
                  "to_date_usd": round(compute_cycle + r2_cycle, 2),
                  "projected_usd": round((compute_cycle + r2_cycle)
                                         / elapsed * cycle_days, 2)},
        "daily": daily,
    }


def report(cur, now=None):
    """Compute (cached per process for 10 minutes: both the cockpit and the
    Costs tab read it, and the scan decompresses a month of job timings)."""
    now = now or datetime.now(timezone.utc)

    def compute():
        cstart, _ = cycle_bounds(now)
        since = min(cstart, now - timedelta(days=31))
        rows = fetch_rows(cur, since)
        r2_block = r2(cur, now, cstart)
        out = summarize(rows, now, r2_block)
        out["r2"] = r2_block
        out["calls"] = len(rows)
        return out
    return db.cached_value(("infra_cost", now.strftime("%Y-%m-%d %H"),
                            now.minute // 10), 600, compute)
