"""The Health cockpit (strategy 1025 §4): the cost model, the flags, the
alerts and digest to the admin inbox only, and the instrumentation it reads.

No database: facts are built in memory and connections are scripted fakes.
The SQL was run read-only against production (see the admin probe).
"""
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import billing                                            # noqa: E402
import cockpit_alerts as alerts                           # noqa: E402
from admin_metrics import cockpit, infra_cost             # noqa: E402

NOW = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)


# ── The cost model ───────────────────────────────────────────────────────

def test_shard_routing_matches_the_worker():
    # FNV-1a 32-bit of a known string, and project-pinned MCP keys.
    assert infra_cost.fnv1a("a") == 0xE40C292C
    a = infra_cost.shard("mcp", "cf-mcp-p4365-aaaa")
    b = infra_cost.shard("mcp", "cf-mcp-p4365-bbbb")
    assert a == b == infra_cost.fnv1a("project:4365") % 20
    assert infra_cost.shard("batch", "cf-faces-x") in (6, 7)
    g = infra_cost.shard("interactive", "cf-render-g12-0-zz")
    assert g == infra_cost.fnv1a("render-group:12-0") % 20


def test_overlapping_calls_share_one_awake_span_and_get_calibrated():
    t = NOW.timestamp() - 86400 * 2
    rows = [("mcp", "cf-mcp-p1-a", t, t + 60, 60, 0.0),
            ("mcp", "cf-mcp-p1-b", t + 30, t + 90, 60, 0.0)]   # same shard
    days = infra_cost.model(rows)
    awake = 90 + infra_cost.tail_for("mcp", t)
    rate = 4 * infra_cost.MEM_USD_PER_GIB_S + 8 * infra_cost.DISK_USD_PER_GB_S
    total = sum(sum(v.values()) for v in days.values())
    assert total == pytest.approx(awake * rate * infra_cost.CALIBRATION)
    # Priced alone, customers cost nothing here; the owner costs it all.
    assert infra_cost.model(rows, "customers") == {}
    assert sum(sum(v.values()) for v in infra_cost.model(
        rows, "owner").values()) == pytest.approx(total)


def test_cpu_is_billed_and_a_span_crossing_midnight_is_split():
    midnight = datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp()
    rows = [("batch", "cf-x", midnight - 30, midnight + 30, 7, 100.0)]
    days = infra_cost.model(rows)
    assert len(days) == 2
    cpu = 100.0 * infra_cost.CPU_USD_PER_VCPU_S * infra_cost.CALIBRATION
    first = sum(days[min(days)].values())
    assert first > cpu                    # cpu counted on the submit day


def test_cycle_bounds_and_projection():
    start, end = infra_cost.cycle_bounds(NOW)
    assert start.date().isoformat() == "2026-10-07"
    assert end.date().isoformat() == "2026-11-07"
    early = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert infra_cost.cycle_bounds(early)[0].date().isoformat() == \
        "2026-10-07"
    t = (NOW - timedelta(days=1)).timestamp()
    out = infra_cost.summarize([("mcp", "cf-mcp-p1-a", t, t + 600, 60, 0.0)],
                               NOW, {"cycle_usd": 1.0})
    assert out["yesterday_usd"] > 0
    assert out["last_7d_owner_share_pct"] == 100.0
    c = out["cycle"]
    assert c["to_date_usd"] == pytest.approx(c["compute_usd"] + 1.0, abs=0.01)
    assert c["projected_usd"] == pytest.approx(
        c["to_date_usd"] / c["elapsed_days"] * c["days"], rel=0.01)


# ── Repeat asks and error classes ────────────────────────────────────────

def _at(hours):
    return NOW + timedelta(hours=hours)


def test_repeat_ask_catches_the_same_request_across_languages():
    asks = [(_at(0), "lower the music for the video"),
            (_at(1), "lower the audio volume for the video"),
            (_at(2), "ZESLAP AUDIO NA 20 %"),
            (_at(3), "ZTLUM JEŠTĚ AUDIO VE VIDEU NA 15%")]
    assert cockpit.repeat_count(asks) == 4
    silences = [(_at(0), "alright just cut any silence and ums"),
                (_at(0.1), "alright just cut any silence and ums"),
                (_at(0.2), "Cut the silences and remove the ums"),
                (_at(0.5), "Cut the silences and remove the ums")]
    assert cockpit.repeat_count(silences) == 4


def test_different_requests_or_spread_out_asks_are_not_repeats():
    varied = [(_at(0), "make it vertical for tiktok"),
              (_at(1), "add captions in yellow"),
              (_at(2), "trim the first ten seconds"),
              (_at(3), "add a title card that says hello")]
    assert cockpit.repeat_count(varied) < 3
    spread = [(_at(0), "make the music quieter"),
              (_at(30), "make the music quieter"),
              (_at(60), "make the music quieter")]
    assert cockpit.repeat_count(spread) == 1


def test_error_classes():
    assert cockpit.error_class("no remote executor is configured for index") \
        == "executor_missing"
    assert cockpit.error_class("Cloudflare Container shard is busy") == \
        "shard_busy"
    assert cockpit.error_class("Cloudflare source version does not match "
                               "the dispatcher") == "version_skew"
    assert cockpit.error_class("Deploy in progress: switching") == \
        "deploy_kill"
    assert cockpit.error_class("ffmpeg was killed by the out-of-memory "
                               "killer (exit -9)") == "oom"
    assert cockpit.error_class("I can't reach the editing model") == \
        "model_unreachable"


# ── Flags ────────────────────────────────────────────────────────────────

def _facts(**kw):
    f = {k: {} for k in ("payments", "jobs", "events", "messages", "grants",
                         "projects", "snapshots")}
    f["history"] = {}
    for k, v in kw.items():
        f[k] = v
    return f


def _pay(uid, days_ago, cents=1500, plan="ai", sid="sub_1", status="completed"):
    return {"user_id": uid, "subscription_id": sid, "plan": plan,
            "status": status, "amount_cents": cents, "currency": "USD",
            "error_code": None, "at": NOW - timedelta(days=days_ago)}


def _job(uid, kind, days_ago, state="done", jid=1, **extra):
    at = NOW - timedelta(days=days_ago)
    row = {"id": jid, "user_id": uid, "project_id": 9, "type": kind,
           "state": state, "created_at": at, "updated_at": at, "error": None,
           "root": str(jid), "outcome": None, "credits": 0, "receipt": None}
    row.update(extra)
    return row


def _acct(uid, **kw):
    a = {"id": uid, "email": f"u{uid}@example.com",
         "created_at": NOW - timedelta(days=90), "last_seen_at": None,
         "plan": "ai", "billing_plan": "ai", "billing_period": "monthly",
         "billing_status": "active", "is_subscribed": 1,
         "subscription_id": "sub_1", "payment_failed_at": None,
         "payment_failed_reason": None, "payment_failed_count": 0}
    a.update(kw)
    return a


def _row(acct, facts, state="renews", **journey):
    return cockpit.build_row(acct, facts, dict(renewal_state=state, **journey),
                             NOW)


def test_renewal_with_no_use_is_charged_no_use_and_red():
    f = _facts(payments={1: [_pay(1, 61), _pay(1, 31), _pay(1, 0.3)]},
               jobs={1: [_job(1, "agent_turn", 60)]},
               snapshots={"sub_1": {"next_billed_at": NOW + timedelta(30)}})
    r = _row(_acct(1), f)
    assert "CHARGED_NO_USE" in r["flags"] and r["health"] == "red"
    assert r["next_action"].startswith("Refund or pause")
    # Used it the week before the charge: not flagged.
    f["jobs"][1].append(_job(1, "mcp_tool", 3, jid=2))
    assert "CHARGED_NO_USE" not in _row(_acct(1), f)["flags"]


def test_charge_soon_counts_calendar_days():
    f = _facts(payments={1: [_pay(1, 25)]},
               snapshots={"sub_1": {"next_billed_at":
                                    NOW + timedelta(days=5, hours=3)}})
    r = _row(_acct(1), f)
    assert "CHARGE_SOON_NO_USE" in r["flags"]
    assert "(30 Oct)" in r["next_action"]


def test_renewal_off_still_active_and_quick_cancel():
    paid = NOW - timedelta(days=3)
    f = _facts(payments={1: [_pay(1, 3)]},
               history={"sub_1": [
                   {"provider_updated_at": paid + timedelta(minutes=20),
                    "status": "active", "scheduled_action": "cancel"}]},
               jobs={1: [_job(1, "agent_turn", 1)]})
    r = _row(_acct(1), f, state="cancelled",
             plan_ends_at=(NOW + timedelta(days=27)).isoformat())
    assert {"RENEWAL_OFF_STILL_ACTIVE", "QUICK_CANCEL"} <= set(r["flags"])
    assert r["renewal"]["off_minutes_after_paying"] == pytest.approx(20, abs=1)
    assert "27 days left" in r["next_action"] or \
        "26 days left" in r["next_action"]


def test_paddle_ending_a_past_due_subscription_is_not_a_renewal_off():
    f = _facts(payments={1: [_pay(1, 60)]},
               history={"sub_1": [
                   {"provider_updated_at": NOW - timedelta(days=30),
                    "status": "past_due", "scheduled_action": None},
                   {"provider_updated_at": NOW - timedelta(days=1),
                    "status": "canceled", "scheduled_action": None}]})
    r = _row(_acct(1), f, state="ended")
    assert r["renewal"]["off_minutes_after_paying"] is None
    assert r["health"] == "ended"


def test_past_due_dormant_shows_the_next_retry():
    f = _facts(payments={1: [_pay(1, 60)]},
               snapshots={"sub_1": {"next_billed_at":
                                    NOW + timedelta(days=3)}})
    acct = _acct(1, last_seen_at=NOW - timedelta(days=40),
                 billing_status="past_due")
    r = _row(acct, f, state="past_due")
    assert "PAST_DUE_DORMANT" in r["flags"]
    assert "next retry 28 Oct" in r["next_action"]
    assert r["billing"]["next_retry_at"].startswith("2026-10-28")


def test_wrong_plan_stuck_setup_and_paid_no_result():
    f = _facts(payments={1: [_pay(1, 3, plan="mcp_connect")]},
               grants={1: [{"created_at": NOW - timedelta(days=2),
                            "last_used_at": NOW - timedelta(days=1),
                            "calls": 4, "revoked_at": None,
                            "client_name": "Claude"}]})
    r = _row(_acct(1, plan="mcp_connect", billing_plan="mcp_connect"), f)
    assert {"WRONG_PLAN", "STUCK_SETUP", "PAID_NO_RESULT_24H"} <= \
        set(r["flags"])
    assert r["client"] == "MCP via Claude"
    # An export delivered to the AI app is a finished result.
    f["jobs"] = {1: [_job(1, "mcp_tool", 1, receipt="final_export")]}
    r = _row(_acct(1, plan="mcp_connect", billing_plan="mcp_connect"), f)
    assert r["results"]["total"] == 1
    assert "PAID_NO_RESULT_24H" not in r["flags"]
    assert "STUCK_SETUP" not in r["flags"]
    # Plus includes the Studio editor since D1: never a wrong plan.
    f["jobs"] = {}
    f["payments"] = {1: [_pay(1, 3, plan="plus_2026")]}
    r = _row(_acct(1, plan="plus_2026", billing_plan="plus_2026"), f)
    assert "WRONG_PLAN" not in r["flags"]


def test_error_loop_unfinished_charged_long_source_and_result_before_paying():
    msgs = [{"session_id": 5, "role": "assistant", "content": "x",
             "created_at": NOW - timedelta(hours=h), "kind": None,
             "error": "job_failed", "job": "1"} for h in (1, 2)]
    f = _facts(
        payments={1: [_pay(1, 10)]},
        jobs={1: [_job(1, "index", 0.5, state="failed",
                       error="no remote executor is configured for index"),
                  _job(1, "agent_turn", 2, jid=2, outcome="partial",
                       credits=111)]},
        messages={1: msgs},
        events={1: [{"kind": "download_triggered", "project_id": 9,
                     "created_at": NOW - timedelta(days=11), "detail": None}]},
        projects={1: [{"id": 9, "title": "Gaming", "created_at": NOW,
                       "source_s": 104 * 60}]})
    r = _row(_acct(1), f)
    assert {"ERROR_LOOP", "UNFINISHED_CHARGED", "LONG_SOURCE"} <= \
        set(r["flags"])
    assert r["friction"]["error_classes_7d"] == ["executor_missing"]
    assert r["requests"]["unfinished_credits_30d"] == 111
    assert r["result_before_paying"] is True
    assert r["making"]["longest_source_min"] == 104


def test_rows_sort_red_first_then_soonest_due():
    a = {"health": "amber", "flags": ["QUICK_CANCEL"], "_sort_due": 10,
         "id": 1}
    b = {"health": "red", "flags": ["CHARGED_NO_USE"], "_sort_due": 99,
         "id": 2}
    c = {"health": "amber", "flags": ["QUICK_CANCEL"], "_sort_due": 5,
         "id": 3}
    rows = sorted([a, b, c], key=lambda r: (
        cockpit.HEALTH_RANK[r["health"]],
        min(cockpit.SEVERITY_RANK[cockpit.FLAGS[k][0]] for k in r["flags"]),
        r["_sort_due"], -r["id"]))
    assert [r["id"] for r in rows] == [2, 3, 1]


# ── The endpoint ─────────────────────────────────────────────────────────

def test_health_board_endpoint_is_admin_only_and_shared(monkeypatch):
    import jwt
    from flask import Flask
    from routes import admin, admin_video
    from admin_metrics import cache
    secret = "q" * 40
    seen = {}

    def shared(endpoint, params, compute, fresh=False, logger=None):
        seen["endpoint"], seen["fresh"] = endpoint, fresh
        return {"strip": {}, "rows": [], "computed_at": "x", "cache_age_s": 0}
    monkeypatch.setattr(cache, "shared_report", shared)
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secret, DATABASE_URL="postgresql://stub/s")
    app.register_blueprint(admin_video.admin_video_bp)
    c = app.test_client()
    assert c.get("/admin/video/health-board").status_code in (401, 403)
    h = {"Authorization": "Bearer " + jwt.encode(
        {"email": admin.ADMIN_EMAIL}, secret, algorithm="HS256")}
    r = c.get("/admin/video/health-board?fresh=1", headers=h)
    assert r.status_code == 200 and r.json["rows"] == []
    assert seen == {"endpoint": "/admin/video/health-board", "fresh": True}


# ── Alerts and the digest: the admin inbox only ──────────────────────────

class KvCur:
    """app_kv in a dict, plus the advisory lock."""

    def __init__(self, kv, locked=False):
        self.kv, self.locked, self._row = kv, locked, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self._row = None
        if "pg_try_advisory_lock" in flat:
            self._row = {"got": not self.locked}
        elif flat.startswith("INSERT INTO app_kv") and "DO NOTHING" in flat:
            if params[0] not in self.kv:
                self.kv[params[0]] = params[1]
                self._row = {"key": params[0]}
        elif flat.startswith("INSERT INTO app_kv"):
            self.kv[params[0]] = params[1]
        elif flat.startswith("DELETE FROM app_kv"):
            self.kv.pop(params[0], None)
        elif flat.startswith("SELECT value FROM app_kv"):
            v = self.kv.get(params[0])
            self._row = {"value": v} if v is not None else None

    def fetchone(self):
        return self._row


class KvConn:
    def __init__(self, kv, locked=False):
        self.kv, self.locked = kv, locked

    def cursor(self):
        return KvCur(self.kv, self.locked)

    def close(self):
        pass


def _tick(monkeypatch, kv, now, events=(), daily=(), send_ok=True,
          locked=False):
    sent = []
    monkeypatch.setattr(alerts, "event_alerts", lambda r, since, n: list(events))
    monkeypatch.setattr(alerts, "daily_alerts", lambda b, n: list(daily))
    monkeypatch.setattr(alerts, "funnel_yesterday", lambda r, n: {
        "day": "2026-10-24", "start": n - timedelta(days=1), "end": n,
        "signups": 20, "uploaders": 10, "first_cuts": 0, "watched_half": 0,
        "keep_clicks": 0, "checkouts": 2, "cash_cents": 1500})
    monkeypatch.setattr(cockpit, "compute", lambda r, n=None: {
        "strip": {"committed_mrr": {"value": 100.0}}, "rows": [],
        "factory": {}})

    def send(subject, body):
        sent.append((subject, body))
        return send_ok
    out = alerts.run_tick(now, send=send, rconn=KvConn(kv),
                          wconn=KvConn(kv, locked))
    return out, sent


def test_one_email_per_tick_and_each_alert_once(monkeypatch):
    kv = {}
    items = [("pay:t1", "Payments", "Renewal $15"),
             ("err:9", "Paying customer saw a service error", "x")]
    before = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)  # 07:00 Dubai
    out, sent = _tick(monkeypatch, kv, before, events=items)
    assert out["alerts"] == 2 and out["sent"] and len(sent) == 1
    assert "2 alerts" in sent[0][0]
    assert not out["digest"]
    out, sent = _tick(monkeypatch, kv, before + timedelta(minutes=5),
                      events=items)
    assert out["alerts"] == 0 and sent == []


def test_a_failed_send_releases_its_claims(monkeypatch):
    kv = {}
    items = [("job:5", "No executor for a user job", "index job 5")]
    at = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    out, sent = _tick(monkeypatch, kv, at, events=items, send_ok=False)
    assert not out["sent"] and "cockpit_alert:job:5" not in kv
    out, sent = _tick(monkeypatch, kv, at + timedelta(minutes=5),
                      events=items)
    assert out["sent"] and len(sent) == 1


def test_the_daily_cap_holds(monkeypatch):
    at = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    kv = {alerts._count_key(at): json.dumps(
        {"n": alerts.MAX_ALERT_EMAILS_PER_DAY})}
    out, sent = _tick(monkeypatch, kv, at, events=[("a:1", "S", "l")])
    assert sent == [] and not out["sent"]


def test_digest_at_0800_dubai_once_a_day(monkeypatch):
    kv = {}
    early = datetime(2026, 10, 25, 3, 59, tzinfo=timezone.utc)   # 07:59
    out, sent = _tick(monkeypatch, kv, early)
    assert not out["digest"] and sent == []
    at8 = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)      # 08:00
    out, sent = _tick(monkeypatch, kv, at8)
    assert out["digest"] and sent[0][0].startswith("Valmera daily")
    late = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
    out, sent = _tick(monkeypatch, kv, late)
    assert not out["digest"] and sent == []
    # A worker asleep at 08:00 still sends on its first tick of the day.
    nxt = datetime(2026, 10, 26, 11, 0, tzinfo=timezone.utc)
    out, sent = _tick(monkeypatch, kv, nxt)
    assert out["digest"]


def test_another_worker_holding_the_lock_skips(monkeypatch):
    out, sent = _tick(monkeypatch, {}, NOW, events=[("a:1", "S", "l")],
                      locked=True)
    assert "skipped" in out and sent == []


def test_alerts_only_ever_go_to_the_admin_inbox(monkeypatch):
    import brevo_delivery
    payloads = []
    monkeypatch.setattr(brevo_delivery, "send_email",
                        lambda payload, category="critical": payloads.append(
                            (payload, category)) or True)
    assert alerts._send("Valmera alert: x", "<p>customer@example.com</p>")
    assert payloads[0][0]["to"] == [{"email": "thevalmera@gmail.com"}]
    assert payloads[0][1] == "critical"
    import inspect
    assert "to" not in inspect.signature(alerts._send).parameters
    assert alerts.ADMIN_TO == "thevalmera@gmail.com"


def test_daily_alerts_from_the_board():
    board = {"rows": [
        {"id": 2007, "email": "e@x", "flags": ["CHARGE_SOON_NO_USE"],
         "renewal": {"days_left": 3, "next_charge_at": "2026-10-28T13:36:17Z",
                     "next_charge_usd": 25.0},
         "billing": {}},
        {"id": 1621, "email": "l@x", "flags": ["PAST_DUE_DORMANT"],
         "renewal": {}, "billing": {"next_retry_at": "2026-10-26T05:20:41Z"}},
        {"id": 1924, "email": "g@x", "flags": ["PAST_DUE_DORMANT"],
         "renewal": {}, "billing": {"next_retry_at": "2026-11-21T14:58:08Z"}}],
        "strip": {"infra": {"yesterday_usd": 12.71,
                            "cycle_projected_usd": 245.0},
                  "first_cut": {"used_pct": 82, "budget_usd": 200,
                                "month_usd": 30}}}
    keys = [k for k, _, _ in alerts.daily_alerts(board, NOW)]
    assert "chargesoon:2007:2026-10-28T13:36:17Z" in keys
    assert "pastdue:1621:2026-10-26" in keys
    assert not any(k.startswith("pastdue:1924") for k in keys)
    assert "infra:20261024" in keys
    assert "trial:50:202610" in keys and "trial:80:202610" in keys
    assert not any(k.startswith("trial:95") for k in keys)


def test_scheduler_never_starts_without_a_real_database(monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.config["DATABASE_URL"] = "postgresql://stub/stub"
    alerts.start_scheduler(app)
    assert alerts._scheduler is None


# ── Instrumentation ──────────────────────────────────────────────────────

def test_refunds_and_chargebacks_are_ledgered_as_money():
    base = {"id": "adj_1", "transaction_id": "txn_1",
            "subscription_id": "sub_1", "totals": {"total": "1500",
                                                    "currency_code": "USD"}}
    assert billing.adjustment_entry(dict(base, action="refund",
                                         status="approved")) == \
        ("refunded", -1500, "USD")
    assert billing.adjustment_entry(dict(base, action="refund",
                                         status="pending_approval")) == \
        ("refund_pending_approval", 0, "USD")
    assert billing.adjustment_entry(dict(base, action="chargeback",
                                         status="approved")) == \
        ("chargeback", -1500, "USD")
    assert billing.adjustment_entry(dict(base, action="chargeback_reverse",
                                         status="approved")) == \
        ("chargeback_reversed", 1500, "USD")
    assert billing.adjustment_entry(dict(base, action="credit")) is None


def test_record_adjustment_upserts_by_adjustment_id(monkeypatch):
    monkeypatch.setattr(billing, "columns_ready", lambda conn: True)
    executed = []

    class Cur:
        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def close(self):
            pass

    class Conn:
        committed = 0

        def cursor(self):
            return Cur()

        def commit(self):
            Conn.committed += 1

        def rollback(self):
            pass
    ok = billing.record_adjustment(Conn(), 7, {
        "id": "adj_9", "action": "refund", "status": "approved",
        "transaction_id": "txn_1", "subscription_id": "sub_1",
        "totals": {"total": "4500"}, "updated_at": "2026-10-25T10:00:00Z"})
    assert ok and Conn.committed == 1
    sql, params = executed[0]
    assert "ON CONFLICT (transaction_id) DO UPDATE" in sql
    assert params[1] == "adj_9" and params[4] == "refunded" and \
        params[5] == -4500
    # Negative and non-'completed' rows can never count as revenue.
    from admin_metrics import defs
    assert "amount_cents > 0" in defs.success("p")


def test_the_webhook_ledgers_a_chargeback(monkeypatch):
    from flask import Flask
    from routes import paddle_webhook as webhook

    class Db:
        def commit(self):
            pass
    ledgered, downgraded = [], []
    monkeypatch.setattr(webhook, "PADDLE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(webhook, "_verify_paddle_signature", lambda _r: True)
    monkeypatch.setattr(webhook, "get_db", lambda: Db())
    monkeypatch.setattr(webhook, "_verified_payer_user_id", lambda *a: 7)
    monkeypatch.setattr(webhook.billing, "record_adjustment",
                        lambda conn, uid, data: ledgered.append((uid, data)))
    monkeypatch.setattr(webhook, "update_user_subscription_status",
                        lambda *a, **k: downgraded.append(a))
    app = Flask(__name__)
    app.register_blueprint(webhook.paddle_webhook)
    r = app.test_client().post("/webhook/paddle", json={
        "event_type": "adjustment.created",
        "data": {"id": "adj_1", "action": "chargeback", "status": "approved",
                 "subscription_id": "sub_1", "transaction_id": "txn_1",
                 "totals": {"total": "1500"}}})
    assert r.status_code == 200
    assert ledgered and ledgered[0][0] == 7
    assert downgraded == []            # a chargeback is ledgered, not granted


def test_mcp_only_refusal_is_recorded(monkeypatch):
    from routes import video
    events = []
    monkeypatch.setattr(video, "_studio_ai_gate_unrecorded",
                        lambda cur, uid: ("refused", 403))
    monkeypatch.setattr(video, "record_client_event",
                        lambda *a, **k: events.append((a, k)))
    assert video._studio_ai_gate(None, 5) == ("refused", 403)
    assert events[0][0][:3] == (5, None, "mcp_only_refused")
    monkeypatch.setattr(video, "_studio_ai_gate_unrecorded",
                        lambda cur, uid: None)
    assert video._studio_ai_gate(None, 5) is None and len(events) == 1
    assert {"mcp_only_refused", "mcp_export_delivered",
            "cancel_feedback"} <= video.CLIENT_EVENT_KINDS


def test_llm_credit_spend_against_what_was_loaded(monkeypatch):
    class Cur:
        def execute(self, sql, params=None):
            self.sql, self.params = " ".join(sql.split()), params

        def fetchone(self):
            return {"usd": 170.0}
    cur = Cur()
    out = cockpit.llm_credit(cur, {"anthropic": {
        "loaded_usd": 200, "loaded_at": "2026-10-25T00:00:00Z"},
        "mystery": {"loaded_usd": 5, "loaded_at": "2026-10-25T00:00:00Z"}})
    assert out == [{"provider": "anthropic", "loaded_usd": 200.0,
                    "loaded_at": "2026-10-25T00:00:00Z", "spent_usd": 170.0,
                    "remaining_usd": 30.0, "used_pct": 85.0}]
    assert "LIKE 'claude%%'" in cur.sql      # psycopg2 turns %% into %
    assert cockpit.llm_credit(cur, None) == []
    board = {"rows": [], "strip": {"first_cut": {"llm_credit": out}}}
    keys = [k for k, _, _ in alerts.daily_alerts(board, NOW)]
    assert keys == ["llmcredit:anthropic:80:2026-10-25T00:00:00Z"]
