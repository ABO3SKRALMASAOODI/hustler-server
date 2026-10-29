"""The owner's free-trial switch (trial_toggle.py, GET/POST /admin/video/trial).

It shows ON, OFF or auto-OFF with the reason, Claude spend against the cycle
budget, accounts served and the model; the owner switches ON or OFF; the
automatic OFF is the credit switch's own and is never overridden from here.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import free_trial
import trial_toggle

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class Conn:
    def __init__(self, kv=None, ledger=True, spent=0.0, served=(3, 5, 1, 0.21),
                 today=2):
        self.kv = dict(kv or {})
        self.ledger = ledger
        self.spent = spent
        self.served = served
        self.today = today
        self.writes = []

    def cursor(self):
        return Cur(self)

    def commit(self):
        pass


class Cur:
    def __init__(self, conn):
        self.c = conn
        self.row = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        c = self.c
        if s.startswith("SELECT value, updated_at FROM app_kv"):
            v = c.kv.get(params[0])
            self.row = None if v is None else {"value": v, "updated_at": NOW}
        elif s.startswith("SELECT value FROM app_kv"):
            v = c.kv.get(params[0])
            self.row = None if v is None else {"value": v}
        elif "to_regclass('public.trial_budgets')" in s:
            self.row = {"a": c.ledger or None, "b": c.ledger or None}
        elif "information_schema.columns" in s:
            self.row = {"?": 1} if c.ledger else None
        elif "FROM trial_budget_days" in s:
            self.row = {"usd": c.spent}
        elif "FROM trial_budgets" in s:
            cyc, all_, exh, spent = c.served
            self.row = {"cycle": cyc, "all_time": all_, "exhausted": exh,
                        "spent": spent}
        elif "FROM projects" in s:
            self.row = {"n": c.today}
        elif s.startswith("INSERT INTO app_kv"):
            c.kv[params[0]] = params[1]
            c.writes.append((params[0], json.loads(params[1])))
        elif "pg_advisory_xact_lock" in s:
            self.row = None
        else:
            raise AssertionError(f"unexpected SQL: {s[:120]}")

    def fetchone(self):
        return self.row


def _flag(**value):
    return json.dumps(value)


def test_off_by_default_and_says_so():
    st = trial_toggle.status(Conn(), now=NOW)
    assert st["state"] == "off" and st["owner_on"] is False
    assert st["reason_text"] == "switched off by the owner"
    assert st["model"] == "claude-haiku-5-5"
    assert st["per_account_usd_cap"] == 0.12
    assert st["spend"]["budget_usd"] == 200.0
    assert st["accounts"] == {"cycle": 3, "all": 5, "exhausted": 1,
                              "spent_usd": 0.21, "first_cuts_today": 2}
    assert st["takes_effect_s"] == 30


def test_on_with_spend_against_the_cycle_budget():
    conn = Conn(kv={"free_result": _flag(on=True, model="claude-haiku-5-5",
                                         per_account_usd_cap=0.12),
                    "claude_credit": _flag(cycle_budget_usd=170,
                                           safety_margin_usd=10)},
                spent=42.5)
    st = trial_toggle.status(conn, now=NOW)
    assert st["state"] == "on" and st["reason"] is None
    assert st["spend"]["spent_usd"] == 42.5
    assert st["spend"]["budget_usd"] == 170 and st["spend"]["line_usd"] == 160
    assert st["spend"]["cycle_end"].startswith("2026-11-08")


def test_auto_off_at_the_line_names_the_reason_and_when_it_comes_back():
    conn = Conn(kv={"free_result": _flag(on=True),
                    "claude_credit": _flag(cycle_budget_usd=170,
                                           safety_margin_usd=10)},
                spent=160.01)
    st = trial_toggle.status(conn, now=NOW)
    assert st["state"] == "auto_off" and st["owner_on"] is True
    assert st["reason"] == "budget"
    assert "reached the line" in st["reason_text"]
    assert st["comes_back"] == "by itself in the next cycle (2026-11-08)"


def test_auto_off_on_a_billing_refusal():
    conn = Conn(kv={"free_result": _flag(on=True),
                    "claude_credit_state": _flag(
                        state="off", reason="billing_error",
                        since="2026-10-30T11:00:00+00:00")})
    st = trial_toggle.status(conn, now=NOW)
    assert st["state"] == "auto_off" and st["reason"] == "billing_error"
    assert "billing" in st["reason_text"]
    assert st["since"] == "2026-10-30T11:00:00+00:00"
    auth = Conn(kv={"free_result": _flag(on=True),
                    "claude_credit_state": _flag(state="off",
                                                 reason="auth_error")})
    assert trial_toggle.status(auth, now=NOW)["comes_back"] == \
        "at the next probe once the key works"


def test_no_ledger_means_nothing_is_admitted_whatever_the_flag():
    st = trial_toggle.status(Conn(kv={"free_result": _flag(on=True)},
                                  ledger=False), now=NOW)
    assert st["state"] == "auto_off" and st["reason"] == "ledger_missing"
    assert st["accounts"]["cycle"] is None


def test_switching_writes_only_the_flag_and_keeps_every_other_key():
    conn = Conn(kv={"free_result": _flag(on=False, test_user_ids=[29, 46],
                                         daily_count_cap=40)})
    free_trial._cache.update(value={"on": False}, expires=1e18)
    value = trial_toggle.switch(conn, True, now=NOW)
    assert value["on"] is True and value["test_user_ids"] == [29, 46]
    assert value["daily_count_cap"] == 40
    # The first ON carries the calibrated release values.
    assert value["model"] == "claude-haiku-5-5"
    assert value["per_account_usd_cap"] == 0.12
    assert [k for k, _ in conn.writes] == ["free_result"]   # never the credit
    assert free_trial._cache["value"] is None                # own cache cleared
    off = trial_toggle.switch(conn, False, now=NOW)
    assert off["on"] is False and off["test_user_ids"] == [29, 46]
    assert trial_toggle.status(conn, now=NOW)["state"] == "off"
    # A value the owner set is never replaced by the release defaults.
    conn2 = Conn(kv={"free_result": _flag(on=False, per_account_usd_cap=0.2)})
    assert trial_toggle.switch(conn2, True, now=NOW)[
        "per_account_usd_cap"] == 0.2


def test_on_while_auto_off_stays_auto_off():
    conn = Conn(kv={"free_result": _flag(on=False),
                    "claude_credit_state": _flag(state="off",
                                                 reason="budget")})
    trial_toggle.switch(conn, True, now=NOW)
    st = trial_toggle.status(conn, now=NOW)
    assert st["state"] == "auto_off" and st["owner_on"] is True
    assert "claude_credit_state" not in [k for k, _ in conn.writes]


def test_the_routes_are_admin_only_and_validate_the_body():
    src = (ROOT / "routes" / "admin_video.py").read_text()
    get = src.split('@admin_video_bp.route("/admin/video/trial", '
                    'methods=["GET"])', 1)[1].split("\n\n\n", 1)[0]
    post = src.split('@admin_video_bp.route("/admin/video/trial", '
                     'methods=["POST"])', 1)[1].split("\n\n\n", 1)[0]
    assert get.lstrip().startswith("@admin_required")
    assert post.lstrip().startswith("@admin_required")
    assert 'isinstance(body.get("on"), bool)' in post
    assert "trial_toggle.switch(conn, body[\"on\"]" in post
