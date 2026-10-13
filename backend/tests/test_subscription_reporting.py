from datetime import datetime, timezone

from admin_metrics.subscriptions import summarize
from subscription_reporting import record_snapshot


def dt(day):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


NOW = dt("2026-10-13")


def group(sid, first="2026-09-08", last="2026-10-08", count=2, cents=1500):
    return dict(subscription_id=sid, first_paid_at=dt(first), last_paid_at=dt(last),
                payment_count=count, paid_cents=cents)


def test_lifetime_dates_and_normal_renewal_are_not_a_return():
    result = summarize([group("sub_1")], {}, now=NOW)
    assert result["first_paid_at"] == "2026-09-08T00:00:00Z"
    assert result["last_paid_at"] == "2026-10-08T00:00:00Z"
    assert result["subscriber_kind"] == "existing"
    assert result["renewal_state"] == "unknown"


def test_new_subscriber_excludes_one_time_payments_and_never_paid():
    assert summarize([], {}, now=NOW)["subscriber_kind"] == "never"
    new = group("sub_1", "2026-10-12", "2026-10-12", 1)
    assert summarize([new], {}, now=NOW)["subscriber_kind"] == "new"
    new["subscription_id"] = None
    assert summarize([new], {}, now=NOW)["subscriber_kind"] == "one_time"


def test_return_and_cancelled_renewal_are_independent():
    groups = [group("sub_old"), group("sub_new", "2026-10-13", "2026-10-13", 1, 2500)]
    snapshots = {
        "sub_old": {"status": "canceled", "canceled_at": dt("2026-10-08")},
        "sub_new": {"status": "active", "started_at": dt("2026-10-13"),
                    "scheduled_action": "cancel", "scheduled_effective_at": dt("2026-11-13")},
    }
    result = summarize(groups, snapshots, "sub_new", now=NOW)
    assert result["subscriber_kind"] == "returned"
    assert result["returned_at"] == "2026-10-13T00:00:00Z"
    assert result["renewal_state"] == "cancelled"
    assert result["renewal_at"] == "2026-11-13T00:00:00Z"
    assert result["paid_usd"] == 40
    assert result["payment_count"] == 3
    # Resuming renewal clears the cancellation; ending is a separate state.
    snapshots["sub_new"].update(scheduled_action=None, next_billed_at=dt("2026-11-13"))
    assert summarize(groups, snapshots, "sub_new", now=NOW)["renewal_state"] == "renews"
    snapshots["sub_new"].update(status="canceled", canceled_at=dt("2026-11-13"))
    assert summarize(groups, snapshots, "sub_new", now=NOW)["renewal_state"] == "ended"


def test_upgrade_or_unknown_history_is_not_claimed_as_a_return():
    groups = [group("sub_old"), group("sub_new", "2026-10-13", "2026-10-13", 1)]
    snapshots = {"sub_new": {"started_at": dt("2026-10-13")}}
    assert summarize(groups, snapshots, now=NOW)["subscriber_kind"] == "history_unknown"
    snapshots["sub_old"] = {"canceled_at": dt("2026-10-14")}
    assert summarize(groups, snapshots, now=NOW)["subscriber_kind"] == "existing"


def test_snapshot_writer_is_ordered_reporting_only_and_clears_cancel():
    class Conn:
        def __init__(self): self.sql = []
        def cursor(self): return self
        def execute(self, sql, params=None): self.sql.append((sql, params))
        def close(self): pass
    conn = Conn()
    assert record_snapshot(conn, {"id": "sub_1", "status": "active", "updated_at": "2026-10-13T00:00:00Z"})
    sql, params = conn.sql[1]
    assert "admin_subscription_snapshots.provider_updated_at" in sql
    assert "EXCLUDED.scheduled_action" in sql
    assert params[6:8] == (None, None)
    assert all("UPDATE users" not in sql and "COMMIT" not in sql for sql, _ in conn.sql)
    assert not record_snapshot(conn, {"id": "txn_1", "status": "completed"})
