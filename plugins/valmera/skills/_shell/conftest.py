"""Every test gets a delivered ledger of its own (_shell/delivered.py): no test reads or writes the owner's real one."""
import pytest


@pytest.fixture(autouse=True)
def _own_delivered_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("VALMERA_DELIVERED_LEDGER", str(tmp_path / "delivered.jsonl"))
    monkeypatch.delenv("VALMERA_DELIVERY_DRIVER", raising=False)      # no test runs as a driver's shell unless it says so


@pytest.fixture(autouse=True)
def _no_platform_song_calls(monkeypatch):
    """Delivery chooses each film's platform song with a model (platform_song.py); no test reaches a real model: the
    choice fails safely (no platform_music.json) unless a test hands its own `ask`."""
    try:
        import platform_song
    except ImportError:
        return

    def refuse(*a, **k):
        raise RuntimeError("no model calls in tests")
    monkeypatch.setattr(platform_song.llm, "ask", refuse)
