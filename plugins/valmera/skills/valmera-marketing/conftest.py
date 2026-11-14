"""Every test gets a delivered ledger of its own (_shell/delivered.py): no test reads or writes the owner's real one."""
import pytest


@pytest.fixture(autouse=True)
def _own_delivered_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DELIVERED_LEDGER", str(tmp_path / "delivered.jsonl"))
