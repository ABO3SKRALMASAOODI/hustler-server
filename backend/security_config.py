"""Shared, non-secret production configuration checks."""

import hashlib
from urllib.parse import urlsplit


# SHA-256 of the database URL that was committed to operator guidance. Keeping
# only its one-way fingerprint lets runtime and release gates reject that exact
# credential without carrying the password in source, logs, or health output.
COMPROMISED_DATABASE_URL_SHA256 = (
    "3fd20182a59ab5fe43f4729c05a24b14656f510ac8654e7c6e4fdf821fb81519"
)
PLACEHOLDERS = {
    "", "changeme", "change-me", "devsecret", "example", "placeholder",
    "secret", "supersecretkey", "test", "todo",
}


def secret_ok(value, minimum):
    value = str(value or "").strip()
    return len(value) >= minimum and value.lower() not in PLACEHOLDERS


def database_url_issue(value, compromised_hash=None):
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
    if str(password).lower() in PLACEHOLDERS:
        return "uses a placeholder password"
    return None


def database_credential_status(value, compromised_hash=None):
    """Bounded public status: never returns any component of the URL."""
    value = str(value or "").strip()
    if not value:
        return "missing"
    digest = hashlib.sha256(value.encode()).hexdigest()
    if digest == (compromised_hash or COMPROMISED_DATABASE_URL_SHA256):
        return "exposed"
    if database_url_issue(value, compromised_hash=compromised_hash):
        return "invalid"
    return "rotated"
