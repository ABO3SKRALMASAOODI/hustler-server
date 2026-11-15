"""Suite-wide isolation for process-global latches.

The OpenAI credit gate (openai_credit) latches a process for 15 minutes after a credit refusal.
Many tests simulate an empty wallet; without this reset one of them would shut OpenAI for every
test after it, and its alert would reach for a database. Each test starts with the gate open and
its alert inert (tests/test_openai_credit.py covers the real alert)."""
import pytest


@pytest.fixture(autouse=True)
def _openai_credit_gate_open(monkeypatch):
    try:
        import openai_credit
    except Exception:  # noqa: BLE001 - a suite without the module
        yield
        return
    openai_credit.reopen()
    real_alert = openai_credit._alert
    monkeypatch.setattr(openai_credit, "_alert", lambda *a, **k: None)
    openai_credit._real_alert = real_alert
    yield
    openai_credit.reopen()


@pytest.fixture(autouse=True)
def _isolate_internal_budget(monkeypatch):
    import openai_budget
    openai_budget._stopped.clear()
    def no_live_budget():
        raise RuntimeError("a unit test attempted a non-injected budget DB")
    monkeypatch.setattr(openai_budget, "connect", no_live_budget)
    yield
    openai_budget._stopped.clear()
