"""Every test gets a delivered ledger of its own (_shell/delivered.py): no test reads or writes the owner's real one."""
import pytest


@pytest.fixture(autouse=True)
def _own_delivered_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DELIVERED_LEDGER", str(tmp_path / "delivered.jsonl"))


@pytest.fixture(autouse=True)
def _no_run_pace_leaks(monkeypatch):
    """podcast.shorts() sets the run-wide footage pace in os.environ; every test starts and ends without one."""
    for k in ("FOOTAGE_PACE_DIR", "FOOTAGE_PACE_S", "FOOTAGE_PACE_BURST"):
        monkeypatch.setenv(k, "")
        monkeypatch.delenv(k)
