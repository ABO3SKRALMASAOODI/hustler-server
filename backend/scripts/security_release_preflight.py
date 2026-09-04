#!/usr/bin/env python3
"""Fail closed before a production release uses unsafe configuration.

Only setting names and pass/fail reasons are printed. Secret values, database
hosts, usernames, and paths from the connection URL never leave this process.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values

# Running this file directly puts backend/scripts, not backend, on sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import security_config  # noqa: E402


def validate(config, compromised_hash=None):
    """Return bounded, non-secret failure descriptions."""
    failures = []
    if not security_config.secret_ok(config.get("SECRET_KEY"), 32):
        failures.append(("SECRET_KEY", "must be at least 32 non-placeholder characters"))
    if not security_config.secret_ok(config.get("PADDLE_API_KEY"), 16):
        failures.append(("PADDLE_API_KEY", "is missing or looks like a placeholder"))
    if not security_config.secret_ok(
            config.get("PADDLE_WEBHOOK_SECRET"), 16):
        failures.append(("PADDLE_WEBHOOK_SECRET", "is missing or looks like a placeholder"))
    if str(config.get("PADDLE_MODE") or "").strip().lower() == "sandbox":
        failures.append(("PADDLE_MODE", "is sandbox for a production release"))
    database_issue = security_config.database_url_issue(
        config.get("DATABASE_URL"), compromised_hash=compromised_hash)
    if database_issue:
        failures.append(("DATABASE_URL", database_issue))
    return failures


def validate_database(config, compromised_hash=None):
    issue = security_config.database_url_issue(
        config.get("DATABASE_URL"), compromised_hash=compromised_hash)
    return [("DATABASE_URL", issue)] if issue else []


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
    parser.add_argument(
        "--database-only", action="store_true",
        help="validate only DATABASE_URL for executor deployment gates")
    args = parser.parse_args(argv)
    config = _configuration(args.env_file)
    failures = (validate_database(config) if args.database_only
                else validate(config))
    if failures:
        print("RELEASE PREFLIGHT FAILED")
        for name, reason in failures:
            print(f"- {name}: {reason}")
        return 1
    if args.database_only:
        print("RELEASE PREFLIGHT PASSED: the database credential is rotated "
              "and production-shaped.")
    else:
        print("RELEASE PREFLIGHT PASSED: required secrets are present, the "
              "database credential is rotated, and production mode is selected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
