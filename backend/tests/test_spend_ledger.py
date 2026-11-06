"""The backend's half of the spend fence (worker/tests/test_spend_fence.py has
the rest): the concierge, the wallet estimate, the ops CLI, the company path."""
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SKIP_DB_INIT", "1")
os.environ.setdefault("DATABASE_URL", "postgresql://stub/stub")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import pytest  # noqa: E402

import anthropic_wallet  # noqa: E402
import claude_credit  # noqa: E402
import spend_ledger  # noqa: E402
from ops_loop import EXCLUDED_USER_IDS, store  # noqa: E402
from schema_contract import DATABASE_REQUIRED_RELATIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


class Conn:
    def __init__(self, migrated=True):
        self.migrated = migrated
        self.charges = []
        self.paid = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        q = " ".join(sql.split())
        self._row = None
        if "to_regclass('public.spend_ledger')" in q:
            self._row = {"t": "spend_ledger" if self.migrated else None}
        elif q.startswith("INSERT INTO spend_ledger"):
            self.charges.append(params[:4])
        elif q.startswith("INSERT INTO trial_budget_days"):
            self.paid.append(params[0])
        elif "information_schema.columns" in q:
            self._row = {"x": 1}
        elif q.startswith("SAVEPOINT") or "SAVEPOINT" in q:
            pass
        elif "FROM trial_budget_days" in q:
            self._row = {"usd": 2.5}
        elif "FROM spend_ledger" in q:
            self._row = {"usd": 7.5}
        else:
            raise AssertionError(q)

    def fetchone(self):
        return self._row


def test_the_internal_list_is_the_ops_loops():
    assert spend_ledger.INTERNAL_USER_IDS == set(EXCLUDED_USER_IDS)


def test_a_table_the_release_must_have():
    assert "spend_ledger" in DATABASE_REQUIRED_RELATIONS
    sql = (ROOT / "backend/migrations/043_spend_ledger.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS spend_ledger" in sql
    assert "CHECK (lane IN ('customer', 'internal', 'replay'))" in sql


def test_a_charge_before_the_migration_is_a_quiet_no():
    assert spend_ledger.charge(Conn(migrated=False), "openai", "customer", "x", 1) is False


@pytest.fixture
def video(monkeypatch):
    monkeypatch.delenv("SPEND_LANE", raising=False)
    from routes import video
    return video


def test_a_customers_concierge_on_claude_is_on_both_ledgers(video):
    conn = Conn()
    video._meter_concierge(conn, {"model": "claude-haiku-5-5", "prompt_tokens": 1000,
                                  "completion_tokens": 100}, 2744)
    assert [c[:3] for c in conn.charges] == [("anthropic", "customer", "concierge")]
    assert conn.charges[0][3] > 0 and len(conn.paid) == 1


def test_a_customers_concierge_on_openai_is_on_the_ledger_for_the_first_time(video):
    conn = Conn()
    video._meter_concierge(conn, {"model": "gpt-6-luna", "prompt_tokens": 1000,
                                  "completion_tokens": 100}, 2744)
    assert [c[:3] for c in conn.charges] == [("openai", "customer", "concierge")]
    assert conn.charges[0][3] > 0 and conn.paid == []


def test_our_own_concierge_stays_off_the_customers_line(video):
    conn = Conn()
    video._meter_concierge(conn, {"model": "claude-haiku-5-5", "prompt_tokens": 1000,
                                  "completion_tokens": 100}, 60)
    assert [c[:3] for c in conn.charges] == [("anthropic", "internal", "concierge")]
    assert conn.paid == []


def test_the_wallet_estimate_counts_company_spend():
    conn = Conn()
    assert anthropic_wallet._spend(conn) == 10.0
    assert anthropic_wallet._spend(Conn(migrated=False)) == 2.5


def test_the_ops_cli_reports_a_replay_on_its_lane():
    cur = Conn()
    store.add_replay_spend(cur, "anthropic", 1.25)
    assert cur.charges == [("anthropic", 1.25)]
    with pytest.raises(store.StoreError):
        store.add_replay_spend(cur, "anthropic", -1)


def test_the_store_may_write_the_ledger_and_still_nothing_else():
    ok = "INSERT INTO spend_ledger (day) VALUES (NOW())"
    assert store.guard(ok) == ok
    with pytest.raises(store.StoreError):
        store.guard("UPDATE app_kv SET value = 'x'")


# Every place the backend reaches a model provider. A new one is added here on
# purpose, with its charge. The rest are one-token probes and the retired app
# builder, which CLAUDE.md says not to touch.
BACKEND_PROVIDER_FILES = {
    "routes/video.py",                # the concierge: _meter_concierge
    "routes/auth.py", "routes/ai_proxy.py",   # retired app builder
    "scripts/claude_credit_probe.py", "scripts/probe_advanced_model.py",
    "provider_readiness.py", "scripts/fallback_canary.py",
}


def test_no_backend_module_reaches_a_provider_off_the_ledger():
    import re
    backend = ROOT / "backend"
    call = re.compile(r"\.messages\.(?:stream|create)\(|\.chat\.completions\.create\("
                      r"|\.responses\.create\(|/responses'|CONCIERGE_CLAUDE_URL,")
    found = set()
    for path in backend.rglob("*.py"):
        name = str(path.relative_to(backend))
        if name.startswith(("tests/", ".venv/", "entrepreneur-bot-backend")):
            continue
        if call.search(path.read_text(encoding="utf-8", errors="ignore")):
            found.add(name)
    assert found <= BACKEND_PROVIDER_FILES, found - BACKEND_PROVIDER_FILES


def test_the_company_recovery_route_is_gone():
    from routes import admin_video
    assert not hasattr(admin_video, "repair_project_edit")
    assert not hasattr(claude_credit, "repair_available")
