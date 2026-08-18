#!/usr/bin/env python3
"""Deterministically fingerprint Valmera selection, assignment, or recast JSON."""

from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys


TOP_LEVEL_EXCLUDES = {
    "selection": {"selection_fingerprint"},
    "assignment": {"assignment_input_fingerprint"},
    "recast": {
        "recast_input_fingerprint",
        "status",
        "approved_by",
        "approved_candidate_id",
        "approved_cast",
        "approved_treatment_delta",
        "contradiction_summary",
    },
    "editor-result": {"result_fingerprint"},
    "parent-qc": {"qc_fingerprint"},
}


def _decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("NaN and infinity are not valid canonical JSON numbers")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def canonical_json(value) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Decimal):
        return _decimal(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Canonical JSON object keys must be strings")
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False) + ":" + canonical_json(value[key])
            for key in sorted(value)
        ) + "}"
    raise ValueError(f"Unsupported JSON value type: {type(value).__name__}")


def fingerprint(kind: str, payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Contract payload must be a JSON object")
    projected = {
        key: value for key, value in payload.items()
        if key not in TOP_LEVEL_EXCLUDES[kind]
    }
    canonical = canonical_json(projected).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _load(path: str):
    text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    return json.loads(text, parse_float=Decimal, parse_int=Decimal)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=sorted(TOP_LEVEL_EXCLUDES), required=True)
    parser.add_argument("--input", default="-", help="JSON path, or - for stdin")
    args = parser.parse_args()
    try:
        print(fingerprint(args.kind, _load(args.input)))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
