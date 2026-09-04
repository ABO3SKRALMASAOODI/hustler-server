"""Credential scans detect real-looking secrets and reveal no values."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts import scan_tracked_secrets as scanner


def test_scanner_flags_remote_database_and_token_without_retaining_value(
        tmp_path):
    # Assemble fixtures so the scanner's own tracked test file does not embed
    # a credential-shaped literal.
    database_url = (
        "postgresql" + "://service:high-entropy-password@db.example/prod")
    api_key = "sk-" + "A1b2" * 10
    path = tmp_path / "unsafe.txt"
    path.write_text(f"DATABASE_URL={database_url}\nAPI_KEY={api_key}\n")

    findings = scanner.scan_paths([path])

    assert [(line, kind) for _path, line, kind in findings] == [
        (2, "OpenAI-style key"),
        (1, "credentialed PostgreSQL URL"),
    ]
    assert database_url not in repr(findings)
    assert api_key not in repr(findings)


def test_scanner_allows_explicit_local_integration_credentials(tmp_path):
    path = tmp_path / "docker-compose.yml"
    path.write_text(
        "DATABASE_URL: postgresql://valmera:valmera@postgres:5432/valmera\n"
        "TEST_DATABASE_URL: postgresql://stub/stub\n")

    assert scanner.scan_paths([path]) == []


def test_scanner_flags_private_key_markers(tmp_path):
    path = tmp_path / "key.txt"
    path.write_text("-----BEGIN " + "PRIVATE KEY-----\nredacted\n")

    findings = scanner.scan_paths([path])

    assert [(line, kind) for _path, line, kind in findings] == [
        (1, "private key")]
