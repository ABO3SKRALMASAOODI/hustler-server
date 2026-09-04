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


def test_ipv6_loopback_is_not_accepted_as_a_production_database():
    config = _safe_config()
    config["DATABASE_URL"] = (
        "postgresql" + "://valmera:valmera@[" + ":" + ":1]:5432/valmera")

    failures = dict(preflight.validate(config))

    assert failures["DATABASE_URL"] == (
        "points at a local database, not production")


def test_cli_never_prints_secret_values(monkeypatch, capsys):
    config = _safe_config()
    config["PADDLE_WEBHOOK_SECRET"] = "too-short"
    monkeypatch.setattr(preflight, "_configuration", lambda _path: config)

    assert preflight.main([]) == 1
    output = capsys.readouterr().out
    assert "PADDLE_WEBHOOK_SECRET" in output
    for value in config.values():
        assert value not in output


def test_database_only_cli_ignores_unrelated_application_secrets(
        monkeypatch, capsys):
    config = {"DATABASE_URL": _safe_config()["DATABASE_URL"]}
    monkeypatch.setattr(preflight, "_configuration", lambda _path: config)

    assert preflight.main(["--database-only"]) == 0
    output = capsys.readouterr().out
    assert "database credential is rotated" in output
    assert config["DATABASE_URL"] not in output


def test_optional_direct_database_url_is_checked_too():
    config = _safe_config()
    config["DIRECT_DATABASE_URL"] = (
        "postgresql" + "://direct-user:old-password@db.example/valmera")
    fingerprint = hashlib.sha256(
        config["DIRECT_DATABASE_URL"].encode()).hexdigest()

    assert preflight.validate_database(
        config, compromised_hash=fingerprint) == [
            ("DIRECT_DATABASE_URL",
             "still matches the credential exposed in Git; rotate it")]
