"""The free trial (owner decision D2, 25 Oct 2026): First cut + a capped
per-account trial budget on the account's first project, export behind a
plan, budget kill switch, abuse limits, and nothing sent by email."""
import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import free_trial  # noqa: E402
from routes import video  # noqa: E402


class DB:
    """Just enough of the tables the trial reads and writes (a cursor that is
    also its own connection, as claude_credit / claude_trial expect)."""

    def __init__(self, user=None, device_others=0, other_admission=False,
                 month_usd=0.0, today=0, account_usd=None, stored=None,
                 admission=None, all_usd=0.0, ledger=True, credit_state=None,
                 cycle_usd=0.0, paid_before=False, account_cap=0.15):
        self.user = user or {"email": "a@gmail.com", "is_verified": 1}
        self.device_others = device_others
        self.other_admission = other_admission
        self.month_usd, self.today, self.account_usd = month_usd, today, account_usd
        self.all_usd = all_usd
        self.stored, self.admission = stored, admission
        self.ledger, self.credit_state = ledger, credit_state
        self.cycle_usd, self.paid_before = cycle_usd, paid_before
        self.account_cap = account_cap
        self.updates = []
        self._row = None
        self.rowcount = 0

    # the cursor is its own connection
    @property
    def connection(self):
        return self

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        if q.startswith(("SAVEPOINT", "RELEASE", "ROLLBACK")):
            self._row = None
        elif q.startswith("SELECT value FROM app_kv"):
            key = (params or ["free_result"])[0]
            value = {"free_result": self.stored,
                     "claude_credit_state": self.credit_state}.get(key)
            self._row = {"value": value} if value else None
        elif q.startswith("SELECT to_regclass"):
            self._row = ({"a": "t", "b": "t", "t": "t"} if self.ledger
                         else {"a": None, "b": None, "t": None})
        elif "information_schema.columns" in q:
            self._row = {"x": 1} if self.ledger else None
        elif "GREATEST(spent_usd - extras_usd" in q:
            self._row = {"usd": self.cycle_usd}
        elif "AS cycle" in q:
            self._row = {"month": self.month_usd, "cycle": self.all_usd}
        elif q.startswith("SELECT cap_usd, spent_usd, exhausted_at FROM trial_budgets"):
            self._row = (None if self.account_usd is None else
                         {"cap_usd": self.account_cap,
                          "spent_usd": self.account_usd, "exhausted_at": None})
        elif q.startswith("SELECT 1 FROM payments"):
            self._row = {"x": 1} if self.paid_before else None
        elif q.startswith("SELECT meta FROM projects"):
            self._row = {"meta": ({"free_result": self.admission}
                                  if self.admission else {})}
        elif q.startswith("SELECT id FROM projects WHERE user_id"):
            self._row = {"id": 5} if self.other_admission else None
        elif "date_trunc('day'" in q:
            self._row = {"n": self.today}
        elif q.startswith("SELECT email, is_verified FROM users"):
            self._row = self.user
        elif q.startswith("SELECT device_id FROM website_signups"):
            self._row = {"device_id": "dev-1"}
        elif "COUNT(DISTINCT p.user_id)" in q:
            self._row = {"n": self.device_others}
        elif q.startswith("UPDATE projects"):
            self.admission = json.loads(params[0])
            self.updates.append(self.admission)
            self._row = None
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self._row


def _on(**extra):
    started = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    return json.dumps({"on": True, "start_at": started,
                       "per_account_usd_cap": 0.15, **extra})


def _fresh():
    free_trial._cache.update(value=None, expires=0.0)
    free_trial._spend_cache.update(value=None, expires=0.0)
    free_trial._credit_cache.update(value=None, expires=0.0)


def test_it_ships_off_with_the_owners_caps():
    # Owner override (25 Oct): D2 on hold, the wall stays as it is.
    d = free_trial.DEFAULTS
    assert free_trial.KEY == "free_result" and d["on"] is False
    assert d["per_account_usd_cap"] == 1.0 and d["pool_usd_cap"] == 200.0
    assert d["daily_count_cap"] == 40 and d["trial_requests"] is True
    assert 60 in d["excluded_user_ids"] and 60 not in d["internal_user_ids"]
    # internal test accounts are configured at release, never by default
    assert d["test_user_ids"] == []


def test_switched_off_admits_nobody_not_even_internal_accounts():
    for uid in (777, 29):
        _fresh()
        assert free_trial.admit_upload(DB(stored=None), uid, 9,
                                       subscribed=False) is None


def test_before_start_only_internal_accounts_are_admitted():
    _fresh()
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    db = DB(stored=json.dumps({"on": True, "start_at": future}))
    assert free_trial.admit_upload(db, 777, 9, subscribed=False) is None
    _fresh()
    admitted = free_trial.admit_upload(DB(stored=json.dumps(
        {"on": True, "start_at": future})), 29, 9, subscribed=False)
    assert admitted["kind"] == "trial" and admitted["first_cut"] is True


def test_admission_marks_the_project_once_and_payers_get_the_first_cut():
    _fresh()
    db = DB(stored=_on())
    first = free_trial.admit_upload(db, 777, 9, subscribed=False)
    assert first["kind"] == "trial" and len(db.updates) == 1
    assert free_trial.admit_upload(db, 777, 9, subscribed=False) == first
    assert len(db.updates) == 1
    _fresh()
    payer = free_trial.admit_upload(DB(stored=_on()), 778, 10, subscribed=True)
    assert payer["kind"] == "payer"


def test_one_trial_per_account_device_and_inbox():
    for db in (DB(stored=_on(), other_admission=True),
               DB(stored=_on(), device_others=2),
               DB(stored=_on(), user={"email": "x@mailinator.com",
                                      "is_verified": 1}),
               DB(stored=_on(), user={"email": "x@gmail.com",
                                      "is_verified": 0})):
        _fresh()
        assert free_trial.admit_upload(db, 777, 9, subscribed=False) is None
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), device_others=1),
                                   777, 9, subscribed=False)


def test_the_budget_switches_new_admissions_off_by_itself():
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), month_usd=200.0),
                                   777, 9, subscribed=False) is None
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), all_usd=200.0),
                                   777, 9, subscribed=False) is None
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), today=40),
                                   777, 9, subscribed=False) is None
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(on=False)),
                                   777, 9, subscribed=False) is None
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on()), 60, 9,
                                   subscribed=False) is None


def test_trial_requests_have_a_kill_switch_and_follow_the_credit_switch():
    trial = {"kind": "trial"}
    _fresh()
    off = free_trial.request_allowance(
        DB(stored=_on(trial_requests=False), admission=trial), 777, 9)
    assert off == {"allowed": False, "reason": "trial_requests_off"}
    _fresh()
    spent = free_trial.request_allowance(
        DB(stored=_on(), admission=trial,
           credit_state=json.dumps({"state": "off", "reason": "budget"})),
        777, 9)
    assert spent == {"allowed": False, "reason": "credit_switch_off"}
    _fresh()
    over_line = free_trial.request_allowance(
        DB(stored=_on(), admission=trial, cycle_usd=195.0), 777, 9)
    assert over_line["reason"] == "credit_switch_off"


def test_test_accounts_get_the_trial_while_it_is_off_and_nobody_else():
    off = json.dumps({"on": False, "test_user_ids": [29]})
    _fresh()
    assert free_trial.admit_upload(DB(stored=off), 777, 9,
                                   subscribed=False) is None
    _fresh()
    admitted = free_trial.admit_upload(DB(stored=off), 29, 9,
                                       subscribed=False)
    assert admitted["kind"] == "trial"
    _fresh()
    ok = free_trial.request_allowance(DB(stored=off, admission=admitted),
                                      29, 9)
    assert ok["allowed"] is True
    _fresh()
    assert free_trial.request_allowance(
        DB(stored=off, admission=admitted), 777, 9)["reason"] == "switched_off"


def test_the_credit_switch_returns_everyone_to_the_wall_but_keeps_the_export_paywall():
    off_state = json.dumps({"state": "off", "reason": "billing_error"})
    trial = {"kind": "trial", "first_cut": True}
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), credit_state=off_state),
                                   777, 9, subscribed=False) is None
    _fresh()
    assert free_trial.trial_project(DB(stored=_on(), admission=trial,
                                       credit_state=off_state),
                                    777, 9, live_only=True) is None
    _fresh()
    # the export paywall reads the raw admission: never a free download
    assert free_trial.trial_project(DB(stored=_on(), admission=trial,
                                       credit_state=off_state),
                                    777, 9, live_only=False) == trial
    _fresh()
    assert free_trial.trial_project(DB(stored=_on(), admission=trial),
                                    777, 9, live_only=True) == trial


def test_without_migration_040_nobody_is_admitted():
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), ledger=False), 777, 9,
                                   subscribed=False) is None


def test_a_lapsed_subscriber_is_not_a_trial_account():
    _fresh()
    assert free_trial.admit_upload(DB(stored=_on(), paid_before=True), 777, 9,
                                   subscribed=False) is None


def test_requests_run_until_the_account_budget_is_spent():
    trial = {"kind": "trial", "first_cut": True}
    _fresh()
    ok = free_trial.request_allowance(
        DB(stored=_on(trial_requests=True), admission=trial,
           account_usd=0.05), 777, 9)
    assert ok["allowed"] and abs(ok["remaining_usd"] - 0.10) < 1e-9
    _fresh()
    # no grant yet (track D grants at the first metered call): the whole cap
    before = free_trial.request_allowance(
        DB(stored=_on(), admission=trial, account_usd=None), 777, 9)
    assert before["allowed"] and abs(before["remaining_usd"] - 0.15) < 1e-9
    _fresh()
    spent = free_trial.request_allowance(
        DB(stored=_on(trial_requests=True), admission=trial,
           account_usd=0.15), 777, 9)
    assert spent == {"allowed": False, "reason": "trial_spent",
                     "remaining_usd": 0.0}
    _fresh()
    other = free_trial.request_allowance(DB(stored=_on()), 777, 9)
    assert other["reason"] == "not_trial_project"


def test_studio_status_never_shows_dollars():
    _fresh()
    status = free_trial.status_for_studio(
        DB(stored=_on(trial_requests=True),
           admission={"kind": "trial", "first_cut": True},
           account_usd=0.03), 777, 9, subscribed=False)
    assert status["export_requires_plan"] is True
    assert status["edits_available"] is True
    assert status["edits_left_share"] == 0.8
    assert "usd" not in json.dumps(status)


def test_the_ready_email_is_a_draft_with_no_sender():
    subject, text = free_trial.ready_email("Sam", "beach.mp4", None,
                                           "https://valmera.io/studio")
    assert subject == "Your first cut is ready" and "Sam" in text
    source = inspect.getsource(free_trial)
    assert "send_email" not in source and "brevo" not in source.lower()


def test_export_is_the_paywall_and_keeps_the_version_for_checkout():
    source = inspect.getsource(video._request_final)
    gate = source[source.index("THE PAYWALL SITS ON EXPORT"):]
    gate = gate[:gate.index("return jsonify(body), 402")]
    assert "_subscribe_gate_applies(cur, user_id)" in gate
    # only the trial project: lapsed subscribers keep exporting old work
    assert ("admission = (_trial_admission(cur, user_id, project_id, "
            "live_only=False)") in gate
    assert "if admission:" in gate
    assert '"export_version": int(version)' in gate
    assert "free_result_keep_clicked" in gate


def test_trial_requests_skip_the_credit_walls_and_ride_the_trial_lane():
    source = inspect.getsource(video.post_message)
    assert "trial = _trial_request(cur, user_id, project_id)" in source
    assert "not trial_lane and plan_gate.needs_plan" in source
    assert "not trial_lane and not check_and_reserve" in source
    assert '{"trial_lane": True}' in source


def test_only_studio_uploads_can_be_admitted():
    core = inspect.getsource(video.complete_upload_core)
    assert "if studio and kind == \"original\" and not staged" in core
    assert "studio=True" in inspect.getsource(video.complete_upload)
    import routes.mcp as mcp
    assert "studio=True" not in inspect.getsource(mcp)


def test_trial_project_state_is_not_locked():
    source = inspect.getsource(video.project_state)
    assert "and not _trial_admission(cur, user_id, project_id))" in source
    assert '{"free_trial": trial_status} if trial_status else {}' in source
