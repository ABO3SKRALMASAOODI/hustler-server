"""The health board's R2 line after the retention sweep.

The sweep (r2_retention.py) deleted 683 GB on 25 Oct 2026 and marked every
row of each deleted key ``meta.r2_purged_at``; the board kept summing those
rows and still showed 1,159 GB against about 475 GB really stored. Stored GB
counts live keys only; the week's growth is what was added minus what the
sweep removed; a swept key stops accruing cost at its purge time.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_metrics import infra_cost  # noqa: E402

NOW = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)
GB = 1_000_000_000


class Cur:
    def __init__(self, rows, sweep=None):
        self.rows, self.sweep = rows, sweep
        self.sql, self.params = [], []
        self._last = None

    def execute(self, sql, params=None):
        self.sql.append(" ".join(sql.split()))
        self.params.append(params)
        self._last = "kv" if "app_kv" in sql else "r2"

    def fetchall(self):
        return self.rows

    def fetchone(self):
        if self._last == "kv" and self.sweep is not None:
            return {"value": json.dumps(self.sweep), "updated_at": NOW}
        return None


def row(population, stored, added=0, added_prev=0, removed=0,
        removed_prev=0, removed_total=0, byte_days=0, keys=1):
    return {"population": population, "bytes": stored * GB, "keys": keys,
            "bytes_7d": added * GB, "bytes_prev_7d": added_prev * GB,
            "purged_7d": removed * GB, "purged_prev_7d": removed_prev * GB,
            "purged_total": removed_total * GB,
            "byte_days_cycle": byte_days * GB}


def test_stored_is_live_keys_and_growth_is_added_minus_removed():
    cur = Cur([row("never_paid", 89, added=66.8, added_prev=71,
                   removed=676.9, removed_total=676.9),
               row("owner", 205.6, added=41, added_prev=32.6, removed=6.5,
                   removed_total=6.5),
               row("payers", 181.2, added=15.6, added_prev=41.2)],
              sweep={"at": "2026-10-25T11:57:00+00:00", "mode": "live",
                     "deleted_gb": 683.41, "kept_gb": 474.86,
                     "deleted_keys": 7753, "selected_gb": 683.41,
                     "errors": 0})
    out = infra_cost.r2(cur, NOW, NOW - timedelta(days=18))
    assert out["total_gb"] == 475.8
    assert out["by_population"]["never_paid"]["gb"] == 89.0
    assert out["by_population"]["never_paid"]["net_7d_gb"] == -610.1
    assert out["added_7d_gb"] == 123.4
    assert out["removed_7d_gb"] == 683.4
    assert out["net_7d_gb"] == -560.0
    assert out["net_prev_7d_gb"] == 144.8
    assert out["last_sweep"]["kept_gb"] == 474.86
    assert out["monthly_usd_at_current_size"] == round(
        (475.8 - infra_cost.R2_FREE_GB) * infra_cost.R2_USD_PER_GB_MONTH, 2)


def test_every_population_is_present_even_when_empty():
    out = infra_cost.r2(Cur([row("owner", 10)]), NOW, NOW)
    assert set(out["by_population"]) == {"never_paid", "owner", "payers"}
    assert out["by_population"]["payers"]["gb"] == 0.0
    assert out["last_sweep"] is None


def test_the_sql_leaves_swept_keys_out_of_storage_and_stops_their_cost():
    cur = Cur([])
    infra_cost.r2(cur, NOW, NOW - timedelta(days=18))
    sql = cur.sql[0]
    assert "? 'r2_purged_at'" in sql
    # stored bytes are live keys only...
    assert "SUM(k.bytes) FILTER (WHERE NOT k.purged)" in sql
    # ...a swept key accrues only until its purge...
    assert "LEAST(COALESCE(k.purged_at, k.created_at)" in sql
    # ...and "added" counts every key created in the window, swept or not.
    assert "WHERE k.created_at >= %(week)s), 0) AS bytes_7d" in sql
    # A malformed stamp can never take the board down with a cast error.
    assert "~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T'" in sql
    params = cur.params[0]
    assert params["week"] == NOW - timedelta(days=7)
    assert params["prev"] == NOW - timedelta(days=14)
