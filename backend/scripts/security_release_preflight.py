#!/usr/bin/env python3
"""Fail closed before a production release uses unsafe configuration.

Only setting names and pass/fail reasons are printed. Secret values, database
hosts, usernames, and paths from the connection URL never leave this process.
"""

import argparse
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values


# SHA-256 of the database URL that was committed to operator guidance. Keeping
# its one-way fingerprint lets release operators prove they rotated it without
# copying the compromised credential into another file or command.
COMPROMISED_DATABASE_URL_SHA256 = (
    "3fd20182a59ab5fe43f4729c05a24b14656f510ac8654e7c6e4fdf821fb81519"
)
_PLACEHOLDERS = {
    "", "changeme", "change-me", "devsecret", "example", "placeholder",
    "secret", "supersecretkey", "test", "todo",
}


def _secret_ok(value, minimum):
    value = str(value or "").strip()
    return len(value) >= minimum and value.lower() not in _PLACEHOLDERS


def _database_url_issue(value, compromised_hash=None):
    value = str(value or "").strip()
    if not value:
        return "is missing"
    if hashlib.sha256(value.encode()).hexdigest() == (
            compromised_hash or COMPROMISED_DATABASE_URL_SHA256):
        return "still matches the credential exposed in Git; rotate it"
    try:
        parsed = urlsplit(value)
        password = parsed.password
    except ValueError:
        return "is not a valid PostgreSQL URL"
    if parsed.scheme not in ("postgres", "postgresql") \
            or not parsed.hostname or not parsed.username or not password:
        return "must include a PostgreSQL host, username, and password"
    if parsed.hostname.lower() in ("localhost", "127.0.0.1", "postgres"):
        return "points at a local database, not production"
    if str(password).lower() in _PLACEHOLDERS:
        return "uses a placeholder password"
    return None


def validate(config, compromised_hash=None):
    """Return bounded, non-secret failure descriptions."""
    failures = []
    if not _secret_ok(config.get("SECRET_KEY"), 32):
        failures.append(("SECRET_KEY", "must be at least 32 non-placeholder characters"))
    if not _secret_ok(config.get("PADDLE_API_KEY"), 16):
        failures.append(("PADDLE_API_KEY", "is missing or looks like a placeholder"))
    if not _secret_ok(config.get("PADDLE_WEBHOOK_SECRET"), 16):
        failures.append(("PADDLE_WEBHOOK_SECRET", "is missing or looks like a placeholder"))
    if str(config.get("PADDLE_MODE") or "").strip().lower() == "sandbox":
        failures.append(("PADDLE_MODE", "is sandbox for a production release"))
    database_issue = _database_url_issue(
        config.get("DATABASE_URL"), compromised_hash=compromised_hash)
    if database_issue:
        failures.append(("DATABASE_URL", database_issue))
    return failures


def _configuration(env_file=None):
    config = {}
    if env_file:
        config.update({key: value for key, value in
                       dotenv_values(Path(env_file)).items()
                       if value is not None})
    # Explicit process environment is the release authority.
    config.update(os.environ)
    return config


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate production security settings without printing them.")
    parser.add_argument(
        "--env-file", help="optional dotenv file to check (process env wins)")
    args = parser.parse_args(argv)
    failures = validate(_configuration(args.env_file))
    if failures:
        print("RELEASE PREFLIGHT FAILED")
        for name, reason in failures:
            print(f"- {name}: {reason}")
        return 1
    print("RELEASE PREFLIGHT PASSED: required secrets are present, the "
          "database credential is rotated, and production mode is selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
