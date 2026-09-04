#!/usr/bin/env python3
"""Reject credential-shaped values in Git-tracked files without echoing them."""

import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


_TOKEN_PATTERNS = (
    ("private key", re.compile(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("OpenAI-style key", re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b")),
    ("GitHub token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("AWS access key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Stripe live secret", re.compile(rb"\bsk_live_[A-Za-z0-9]{16,}\b")),
    ("Paddle live secret", re.compile(
        rb"\bpdl_(?:live|ntfset)_[A-Za-z0-9_-]{16,}\b")),
)
_DATABASE_URL = re.compile(
    rb"postgres(?:ql)?://[^\s<>`\"']+", re.IGNORECASE)
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "postgres"}
_LOCAL_PASSWORDS = {"postgres", "stub", "test", "valmera"}


def _line_number(data, offset):
    return data.count(b"\n", 0, offset) + 1


def scan_file(path):
    """Return (kind, line) findings; never retain matched secret text."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    if b"\0" in data:
        return []
    findings = []
    for kind, pattern in _TOKEN_PATTERNS:
        findings.extend((kind, _line_number(data, match.start()))
                        for match in pattern.finditer(data))
    for match in _DATABASE_URL.finditer(data):
        try:
            parsed = urlsplit(match.group().decode("utf-8"))
            host = (parsed.hostname or "").lower()
            password = (parsed.password or "").lower()
        except (UnicodeDecodeError, ValueError):
            findings.append(("credentialed PostgreSQL URL",
                             _line_number(data, match.start())))
            continue
        # Local integration fixtures are intentionally public. Anything with
        # a password outside that exact local shape is a secret, even in docs.
        if password and not (host in _LOCAL_HOSTS
                             and password in _LOCAL_PASSWORDS):
            findings.append(("credentialed PostgreSQL URL",
                             _line_number(data, match.start())))
    return findings


def tracked_paths(root):
    raw = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root).split(b"\0")
    return [Path(root, item.decode("utf-8")) for item in raw if item]


def scan_paths(paths):
    findings = []
    for path in paths:
        for kind, line in scan_file(path):
            findings.append((str(path), line, kind))
    return findings


def main():
    root = Path(__file__).resolve().parents[2]
    findings = scan_paths(tracked_paths(root))
    if findings:
        print("TRACKED SECRET SCAN FAILED")
        for path, line, kind in findings:
            try:
                label = str(Path(path).relative_to(root))
            except ValueError:
                label = str(path)
            print(f"- {label}:{line}: {kind}")
        print("Matched values were intentionally redacted.")
        return 1
    print("Tracked secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
