"""The release preflight blocks known unsafe config without leaking it."""

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import security_release_preflight as preflight


def _safe_config():
    return {
        "SECRET_KEY": "s" * 64,
        "PADDLE_API_KEY": "pdl_live_" + "a" * 40,
        "PADDLE_WEBHOOK_SECRET": "pdl_ntfset_" + "b" * 40,
        "DATABASE_URL": (
            "postgresql" + "://release_user:new-random-password@"
            "db.production.example/valmera"),
    }


def test_safe_production_configuration_passes():
    assert preflight.validate(_safe_config()) == []


def test_compromised_database_fingerprint_is_blocked():
    config = _safe_config()
    fingerprint = hashlib.sha256(
        config["DATABASE_URL"].encode()).hexdigest()

    assert preflight.validate(config, compromised_hash=fingerprint) == [
        ("DATABASE_URL",
         "still matches the credential exposed in Git; rotate it")]


def test_missing_and_sandbox_settings_are_all_reported():
    failures = dict(preflight.validate({
        "SECRET_KEY": "supersecretkey",
        "PADDLE_API_KEY": "",
        "PADDLE_WEBHOOK_SECRET": "todo",
        "PADDLE_MODE": "sandbox",
        "DATABASE_URL": "postgresql://valmera:valmera@localhost/valmera",
    }))

    assert set(failures) == {
        "SECRET_KEY", "PADDLE_API_KEY", "PADDLE_WEBHOOK_SECRET",
        "PADDLE_MODE", "DATABASE_URL",
    }


def test_cli_never_prints_secret_values(monkeypatch, capsys):
    config = _safe_config()
    config["PADDLE_WEBHOOK_SECRET"] = "too-short"
    monkeypatch.setattr(preflight, "_configuration", lambda _path: config)

    assert preflight.main([]) == 1
    output = capsys.readouterr().out
    assert "PADDLE_WEBHOOK_SECRET" in output
    for value in config.values():
        assert value not in output
